"""Recoverable business ingestion workflow; no direct Doclib exposure to users."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from httpx import RequestError

from ...doclib import DoclibInterface
from ...doclib.types import ParseInfo, ParseReleaseRequest
from ...errors import MineruError
from ...parser.page_range import parse_page_range_set
from ...types import Tier
from ..documents import (
    DoclibGateway, DocumentIntegrityError, ImmutableUploadStore, UploadError, UploadIntegrityError,
    resolve_parse_tier,
)
from ..domain import BusinessDocument, IngestTask
from ..store import BusinessStore, BusinessStoreError


class DocumentWorkflowError(RuntimeError):
    """A workflow invariant failed; the caller should not expose internals."""


def _distinct_completed_batches(parses: list[ParseInfo]) -> tuple[ParseInfo, ...]:
    """Prefer the newest completed result for an identical page set.

    Doclib may queue the same range twice when its automatic ingest and an
    explicit all-pages request race. Partial overlaps remain invalid and are
    rejected by BusinessStore rather than silently discarding pages.
    """
    by_pages: dict[tuple[str, str, Tier, frozenset[int]], ParseInfo] = {}
    for parse in parses:
        pages = frozenset(parse_page_range_set(parse.page_range))
        key = (parse.sha256, parse.short_id, parse.tier, pages)
        previous = by_pages.get(key)
        if previous is None or (parse.done_at or 0, parse.id) > (previous.done_at or 0, previous.id):
            by_pages[key] = parse
    return tuple(by_pages.values())


@dataclass(frozen=True)
class DocumentSubmission:
    document: BusinessDocument
    task: IngestTask


class DocumentWorkflow:
    """Coordinate upload, business identity, and Doclib tasks without a 2PC."""

    def __init__(
        self,
        *,
        uploads: ImmutableUploadStore,
        store: BusinessStore,
        gateway: DoclibGateway,
        doclib: DoclibInterface,
        producer_version: str,
    ) -> None:
        if not producer_version.strip():
            raise ValueError("Producer version is required")
        self._uploads = uploads
        self._store = store
        self._gateway = gateway
        self._doclib = doclib
        self._producer_version = producer_version

    def submit(
        self, source: BinaryIO, *, filename: str, tier: Tier | None = None, template_code: str | None = None,
        request_key: str | None = None,
    ) -> DocumentSubmission:
        """Accept a source, persist its identity, then request local parsing."""
        resolve_parse_tier(filename, tier)
        uploaded = self._uploads.store(source, filename=filename)
        original_name = Path(filename.replace("\\", "/")).name
        try:
            if request_key is None:
                document, task = self._store.create_document_with_task(
                    uploaded, original_name=original_name, requested_tier=tier, template_code=template_code,
                )
                created = True
            else:
                document, task, created = self._store.create_or_reuse_document_with_task(
                    uploaded, original_name=original_name, requested_tier=tier, template_code=template_code,
                    request_key=request_key,
                )
        except Exception:
            self._uploads.discard_unregistered(uploaded)
            raise
        if not created:
            self._uploads.discard_unregistered(uploaded)
            return DocumentSubmission(document=document, task=task)
        task = self._submit_existing(task, source_path=uploaded.path, expected_sha256=document.sha256)
        return DocumentSubmission(document=document, task=task)

    def retry(self, task_id: str, *, request_key: str | None = None) -> IngestTask:
        """Resubmit a retained source after a failed or interrupted submission."""
        if request_key is not None:
            task, should_submit = self._store.begin_task_retry(task_id, request_key=request_key)
            if not should_submit:
                return task
        else:
            task = self._store.get_task(task_id)
            if task is None:
                raise DocumentWorkflowError("Task not found")
            if task.status not in ("uploaded", "submitting", "failed"):
                return task
            task = self._store.begin_task_submission(task.id)
            if task.status != "submitting":
                return task
        document = self._store.get_document(task.document_id)
        if document is None:
            raise DocumentWorkflowError("Task source document not found")
        try:
            source_path = self._uploads.source_path(document.storage_key)
        except (UploadError, OSError):
            return self._store.mark_task_failed(
                task.id, error_code="source_unavailable", expected_submission_attempt=task.submission_attempt
            )
        return self._submit_existing(task, source_path=source_path, expected_sha256=document.sha256)

    def _submit_existing(self, task: IngestTask, *, source_path: Path, expected_sha256: str) -> IngestTask:
        task = self._store.begin_task_submission(task.id)
        if task.status != "submitting":
            return task
        try:
            submitted = self._gateway.submit(
                source_path, tier=task.requested_tier,
                force=task.submission_force,
                consumer_key=f"business:{task.id}",
                submission_attempt=task.submission_attempt,
                expected_sha256=expected_sha256,
            )
            if submitted.sha256 != expected_sha256:
                raise DocumentIntegrityError("Doclib submission no longer matches the business source")
            if not submitted.parse_ids:
                raise DocumentWorkflowError("Doclib did not return a trackable parse ID")
        except DocumentIntegrityError as exc:
            self._store.mark_task_failed(
                task.id, error_code="source_integrity_failed", expected_submission_attempt=task.submission_attempt
            )
            raise DocumentWorkflowError("Source integrity failed during Doclib submission") from exc
        except (MineruError, RequestError, OSError, ValueError, DocumentWorkflowError):
            return self._store.mark_task_failed(
                task.id, error_code="doclib_submission_failed", expected_submission_attempt=task.submission_attempt
            )
        return self._store.mark_task_submitted(
            task.id, actual_tier=submitted.tier, parse_ids=submitted.parse_ids,
            submission_attempt=task.submission_attempt,
        )

    def cancel(self, task_id: str, *, request_key: str | None = None) -> IngestTask:
        """Fence business completion, then durably release the Doclib work intent."""
        task = self._store.request_task_cancel(task_id, request_key=request_key)
        if task.status == "cancelled":
            return task
        try:
            released = self._doclib.release_parse_consumer(ParseReleaseRequest(consumer_key=f"business:{task_id}"))
        except (MineruError, RequestError, OSError):
            return task  # The result is unknown; a later cancel call retries the same stable intent.
        return self._store.finish_task_cancel(task_id, released)

    def refresh(self, task_id: str) -> IngestTask:
        """Poll Doclib and persist terminal state; safe to repeat after restart."""
        task = self._store.get_task(task_id)
        if task is None:
            raise DocumentWorkflowError("Task not found")
        if task.status == "cancel_requested":
            return self.cancel(task_id)
        if task.status == "submitting":
            document = self._store.get_document(task.document_id)
            if document is None:
                raise DocumentWorkflowError("Task source document not found")
            try:
                source_path = self._uploads.source_path(document.storage_key)
            except (UploadError, OSError):
                return self._store.mark_task_failed(
                    task.id, error_code="source_unavailable", expected_submission_attempt=task.submission_attempt
                )
            task = self._submit_existing(task, source_path=source_path, expected_sha256=document.sha256)
        if task.status != "submitted":
            return task
        try:
            parses = [self._doclib.get_parse(parse_id) for parse_id in task.parse_ids]
        except (MineruError, RequestError):
            return task  # A transient worker outage must not erase a submitted task.
        if any(parse.status in ("failed", "superseded", "skipped") for parse in parses):
            return self._store.fail_submitted_task_if_current(
                task.id, error_code="doclib_parse_failed", parse_ids=task.parse_ids,
                submission_attempt=task.submission_attempt,
            )
        if not all(parse.status == "done" for parse in parses):
            return task
        document = self._store.get_document(task.document_id)
        assert document is not None
        if document.original_name.lower().endswith(".pdf"):
            try:
                doc = self._doclib.get_doc(document.sha256)
            except (MineruError, RequestError):
                return task
            covered_pages = set().union(*(parse_page_range_set(parse.page_range) for parse in parses))
            if not isinstance(doc.page_count, int) or doc.page_count < 1 or covered_pages != set(range(1, doc.page_count + 1)):
                return self._store.fail_submitted_task_if_current(
                    task.id, error_code="parse_coverage_incomplete", parse_ids=task.parse_ids,
                    submission_attempt=task.submission_attempt,
                )
        try:
            self._uploads.verified_source_path(
                document.storage_key, sha256=document.sha256, size=document.size,
            )
        except UploadIntegrityError:
            return self._store.fail_submitted_task_if_current(
                task.id, error_code="source_integrity_failed", parse_ids=task.parse_ids,
                submission_attempt=task.submission_attempt,
            )
        except (UploadError, OSError):
            return self._store.fail_submitted_task_if_current(
                task.id, error_code="source_unavailable", parse_ids=task.parse_ids,
                submission_attempt=task.submission_attempt,
            )
        try:
            return self._store.complete_task_with_revision(
                task.id, parse=_distinct_completed_batches(parses), producer_version=self._producer_version,
                submission_attempt=task.submission_attempt,
            )
        except BusinessStoreError:
            return self._store.fail_submitted_task_if_current(
                task.id, error_code="parse_batch_invalid", parse_ids=task.parse_ids,
                submission_attempt=task.submission_attempt,
            )


__all__ = ["DocumentSubmission", "DocumentWorkflow", "DocumentWorkflowError"]
