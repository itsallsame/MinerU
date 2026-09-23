"""Recoverable business ingestion workflow; no direct Doclib exposure to users."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from ...doclib import DoclibInterface
from ...errors import MineruError
from ...types import Tier
from ..documents import DoclibGateway, DocumentIntegrityError, ImmutableUploadStore, resolve_parse_tier
from ..domain import BusinessDocument, IngestTask
from ..store import BusinessStore


class DocumentWorkflowError(RuntimeError):
    """A workflow invariant failed; the caller should not expose internals."""


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
        self, source: BinaryIO, *, filename: str, tier: Tier | None = None, template_code: str | None = None
    ) -> DocumentSubmission:
        """Accept a source, persist its identity, then request local parsing."""
        resolve_parse_tier(filename, tier)
        uploaded = self._uploads.store(source, filename=filename)
        try:
            document, task = self._store.create_document_with_task(
                uploaded, original_name=Path(filename.replace("\\", "/")).name, requested_tier=tier,
                template_code=template_code,
            )
        except Exception:
            self._uploads.discard_unregistered(uploaded)
            raise
        task = self._submit_existing(task, source_path=uploaded.path, expected_sha256=document.sha256)
        return DocumentSubmission(document=document, task=task)

    def retry(self, task_id: str) -> IngestTask:
        """Resubmit a retained source after a failed or interrupted submission."""
        task = self._store.get_task(task_id)
        if task is None:
            raise DocumentWorkflowError("Task not found")
        if task.status not in ("uploaded", "failed"):
            return task
        document = self._store.get_document(task.document_id)
        if document is None:
            raise DocumentWorkflowError("Task source document not found")
        source_path = self._uploads.source_path(document.storage_key)
        return self._submit_existing(task, source_path=source_path, expected_sha256=document.sha256)

    def _submit_existing(self, task: IngestTask, *, source_path: Path, expected_sha256: str) -> IngestTask:
        try:
            submitted = self._gateway.submit(
                source_path, tier=task.requested_tier, force=task.error_code == "doclib_parse_failed"
            )
            if submitted.sha256 != expected_sha256:
                raise DocumentIntegrityError("Doclib submission no longer matches the business source")
            if not submitted.parse_ids:
                raise DocumentWorkflowError("Doclib did not return a trackable parse ID")
        except DocumentIntegrityError as exc:
            self._store.mark_task_failed(task.id, error_code="source_integrity_failed")
            raise DocumentWorkflowError("Source integrity failed during Doclib submission") from exc
        except (MineruError, OSError, ValueError, DocumentWorkflowError):
            return self._store.mark_task_failed(task.id, error_code="doclib_submission_failed")
        return self._store.mark_task_submitted(task.id, actual_tier=submitted.tier, parse_ids=submitted.parse_ids)

    def refresh(self, task_id: str) -> IngestTask:
        """Poll Doclib and persist terminal state; safe to repeat after restart."""
        task = self._store.get_task(task_id)
        if task is None:
            raise DocumentWorkflowError("Task not found")
        if task.status != "submitted":
            return task
        try:
            parses = [self._doclib.get_parse(parse_id) for parse_id in task.parse_ids]
        except MineruError:
            return task  # A transient worker outage must not erase a submitted task.
        if any(parse.status in ("failed", "superseded") for parse in parses):
            return self._store.mark_task_failed(task.id, error_code="doclib_parse_failed")
        if not all(parse.status == "done" for parse in parses):
            return task
        for parse in parses:
            self._store.add_completed_revision(task.document_id, parse=parse, producer_version=self._producer_version)
        return self._store.mark_task_done(task.id)


__all__ = ["DocumentSubmission", "DocumentWorkflow", "DocumentWorkflowError"]
