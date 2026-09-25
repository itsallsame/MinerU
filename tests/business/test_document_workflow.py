"""The business ingestion workflow must remain recoverable across failures."""

from __future__ import annotations

import hashlib
import io
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event
from unittest.mock import Mock

import pytest
from httpx import ReadTimeout

from mineru.business.documents import DoclibGateway, ImmutableUploadStore
from mineru.business.services import DocumentSubmission, DocumentWorkflow, DocumentWorkflowError
from mineru.business.store import BusinessStore
from mineru.doclib import DoclibInterface, ParseInfo, ParseRequest, ParseResponse
from mineru.doclib.types import ParseReleaseItem, ParseReleaseResponse
from mineru.errors import ServerNotRunningError


def _parse_response(path: str, *, parse_id: int = 7) -> ParseResponse:
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    return ParseResponse(
        sha256=digest,
        short_id=digest[:12],
        tier="flash",
        page_range="1",
        status="pending",
        created_parse_ids=[parse_id],
    )


def _parse_info(
    sha256: str, *, status: str = "done", parse_id: int = 7,
    page_range: str = "1", short_id: str | None = None,
) -> ParseInfo:
    return ParseInfo(
        id=parse_id,
        sha256=sha256,
        short_id=short_id or sha256[:12],
        tier="flash",
        page_range=page_range,
        status=status,
        privacy="local",
        created_at=1,
        updated_at=2,
        done_at=2 if status == "done" else None,
    )


def _workflow(tmp_path: Path, client: Mock, *, initialize_db: bool = True) -> tuple[DocumentWorkflow, BusinessStore, Path]:
    shared = tmp_path / "shared"
    shared.mkdir()
    business_dir = tmp_path / "business"
    business_dir.mkdir()
    store = BusinessStore(business_dir / "business.sqlite3")
    if initialize_db:
        store.initialize()
    workflow = DocumentWorkflow(
        uploads=ImmutableUploadStore(shared, max_bytes=1024),
        store=store,
        gateway=DoclibGateway(client, shared_root=shared),
        doclib=client,
        producer_version="4.0.6",
    )
    return workflow, store, shared


def _released(task_id: str, *items: tuple[int, str, str]) -> ParseReleaseResponse:
    return ParseReleaseResponse(
        consumer_key=f"business:{task_id}",
        results=[
            ParseReleaseItem(parse_id=parse_id, disposition=disposition, status_at_release=status)
            for parse_id, disposition, status in items
        ],
    )


def test_uploaded_cancel_tombstones_intent_without_submitting(tmp_path: Path) -> None:
    client = Mock(spec=DoclibInterface)
    workflow, store, shared = _workflow(tmp_path, client)
    upload = ImmutableUploadStore(shared, max_bytes=1024).store(io.BytesIO(b"<h1>Stop</h1>"), filename="stop.html")
    document, task = store.create_document_with_task(upload, original_name="stop.html", requested_tier=None)
    client.release_parse_consumer.return_value = _released(task.id)

    cancelled = workflow.cancel(task.id)
    assert cancelled.status == "cancelled" and cancelled.cancel_effect == "not_submitted"
    assert workflow.cancel(task.id) == cancelled
    assert workflow.retry(task.id) == cancelled
    assert store.list_revisions(document.id) == ()
    client.ensure_parse.assert_not_called()
    client.release_parse_consumer.assert_called_once()


def test_submitted_cancel_reports_only_actual_batch_release_and_blocks_revision(tmp_path: Path) -> None:
    client = Mock(spec=DoclibInterface)
    client.ensure_parse.side_effect = lambda request: _parse_response(request.path)
    workflow, store, _shared = _workflow(tmp_path, client)
    submitted = workflow.submit(
        io.BytesIO(b"<h1>Shared</h1>"), filename="shared.html", template_code="official_document",
    )
    task_id = submitted.task.id
    client.release_parse_consumer.return_value = _released(task_id, (7, "shared", "pending"))

    cancelled = workflow.cancel(task_id)
    assert cancelled.status == "cancelled" and cancelled.cancel_effect == "may_continue"
    assert workflow.refresh(task_id) == cancelled
    assert store.complete_task_with_revision(
        task_id, parse=_parse_info(submitted.document.sha256), producer_version="4.0.6"
    ) == cancelled
    assert store.list_revisions(submitted.document.id) == ()
    assert store.quality_stats()["extraction_pending"] == 0


def test_cancel_retries_unknown_doclib_result_after_restart(tmp_path: Path) -> None:
    client = Mock(spec=DoclibInterface)
    client.ensure_parse.side_effect = lambda request: _parse_response(request.path)
    workflow, store, shared = _workflow(tmp_path, client)
    submitted = workflow.submit(io.BytesIO(b"<h1>Recover</h1>"), filename="recover.html")
    client.release_parse_consumer.side_effect = [
        ServerNotRunningError(),
        _released(submitted.task.id, (7, "skipped", "skipped")),
    ]

    uncertain = workflow.cancel(submitted.task.id)
    assert uncertain.status == "cancel_requested" and uncertain.cancel_effect is None
    reopened = DocumentWorkflow(
        uploads=ImmutableUploadStore(shared, max_bytes=1024), store=BusinessStore(tmp_path / "business" / "business.sqlite3"),
        gateway=DoclibGateway(client, shared_root=shared), doclib=client, producer_version="4.0.6",
    )
    cancelled = reopened.refresh(submitted.task.id)
    assert cancelled.status == "cancelled" and cancelled.cancel_effect == "queued_skipped"
    assert store.list_revisions(submitted.document.id) == ()
    assert client.release_parse_consumer.call_count == 2


def test_doclib_read_timeout_keeps_submission_generation_recoverable(tmp_path: Path) -> None:
    client = Mock(spec=DoclibInterface)
    requests: list[ParseRequest] = []

    def submit(request: ParseRequest) -> ParseResponse:
        requests.append(request)
        if len(requests) == 1:
            raise ReadTimeout("response lost after upload")
        return _parse_response(request.path)

    client.ensure_parse.side_effect = submit
    workflow, store, shared = _workflow(tmp_path, client)
    initial = workflow.submit(io.BytesIO(b"<h1>Timeout</h1>"), filename="timeout.html")
    assert initial.task.status == "failed" and initial.task.error_code == "doclib_submission_failed"
    reopened = DocumentWorkflow(
        uploads=ImmutableUploadStore(shared, max_bytes=1024),
        store=BusinessStore(tmp_path / "business" / "business.sqlite3"),
        gateway=DoclibGateway(client, shared_root=shared), doclib=client, producer_version="4.0.6",
    )
    recovered = reopened.retry(initial.task.id)
    assert recovered.status == "submitted" and recovered.parse_ids == (7,)
    assert [(request.submission_attempt, request.force) for request in requests] == [(1, False), (1, False)]
    assert store.list_revisions(initial.document.id) == ()


def test_doclib_read_timeout_during_poll_or_cancel_preserves_task_intent(tmp_path: Path) -> None:
    client = Mock(spec=DoclibInterface)
    client.ensure_parse.side_effect = lambda request: _parse_response(request.path)
    workflow, store, _shared = _workflow(tmp_path, client)
    submitted = workflow.submit(io.BytesIO(b"<h1>Poll timeout</h1>"), filename="poll.html")
    client.get_parse.side_effect = [ReadTimeout("parse poll timed out"), _parse_info(submitted.document.sha256)]
    assert workflow.refresh(submitted.task.id).status == "submitted"
    assert workflow.refresh(submitted.task.id).status == "done"
    assert len(store.list_revisions(submitted.document.id)) == 1

    another = workflow.submit(io.BytesIO(b"<h1>Cancel timeout</h1>"), filename="cancel.html")
    client.release_parse_consumer.side_effect = [
        ReadTimeout("release response lost"), _released(another.task.id, (7, "skipped", "skipped")),
    ]
    assert workflow.cancel(another.task.id).status == "cancel_requested"
    assert workflow.refresh(another.task.id).status == "cancelled"


def test_doclib_read_timeout_during_pdf_coverage_keeps_submitted_task(tmp_path: Path) -> None:
    client = Mock(spec=DoclibInterface)
    client.ensure_parse.side_effect = lambda request: _parse_response(request.path)
    workflow, store, _shared = _workflow(tmp_path, client)
    submitted = workflow.submit(io.BytesIO(b"%PDF-1.7\ncontent"), filename="report.pdf", tier="flash")
    client.get_parse.return_value = _parse_info(submitted.document.sha256)
    doc = Mock(page_count=1)
    client.get_doc.side_effect = [ReadTimeout("document metadata timed out"), doc]

    assert workflow.refresh(submitted.task.id).status == "submitted"
    assert store.list_revisions(submitted.document.id) == ()
    assert workflow.refresh(submitted.task.id).status == "done"
    assert len(store.list_revisions(submitted.document.id)) == 1


def test_cancel_during_doclib_submission_prevents_late_submitted_state(tmp_path: Path) -> None:
    client = Mock(spec=DoclibInterface)
    workflow, store, _shared = _workflow(tmp_path, client)
    entered = Event()
    resume = Event()
    task_ids: list[str] = []

    def blocked_submit(request: ParseRequest) -> ParseResponse:
        task_ids.append(request.consumer_key.removeprefix("business:"))
        entered.set()
        assert resume.wait(5)
        return _parse_response(request.path)

    client.ensure_parse.side_effect = blocked_submit
    with ThreadPoolExecutor(max_workers=2) as pool:
        submitting = pool.submit(workflow.submit, io.BytesIO(b"<h1>Race</h1>"), filename="race.html")
        assert entered.wait(5)
        task_id = task_ids[0]
        assert store.get_task(task_id).status == "submitting"
        client.release_parse_consumer.return_value = _released(task_id, (7, "skipped", "skipped"))
        cancelled = workflow.cancel(task_id)
        resume.set()
        submitted = submitting.result(timeout=5)
    assert cancelled.status == submitted.task.status == "cancelled"
    assert store.list_revisions(submitted.document.id) == ()


def test_parallel_failed_parse_retries_share_one_force_generation(tmp_path: Path) -> None:
    client = Mock(spec=DoclibInterface)
    force_barrier = Barrier(2)
    requests: list[ParseRequest] = []

    def submit(request: ParseRequest) -> ParseResponse:
        requests.append(request)
        if request.force:
            force_barrier.wait(timeout=5)
        return _parse_response(request.path, parse_id=8 if request.force else 7)

    client.ensure_parse.side_effect = submit
    workflow, store, shared = _workflow(tmp_path, client)
    initial = workflow.submit(io.BytesIO(b"<h1>Retry race</h1>"), filename="race.html")
    client.get_parse.return_value = _parse_info(initial.document.sha256, status="failed")
    assert workflow.refresh(initial.task.id).status == "failed"
    reopened = DocumentWorkflow(
        uploads=ImmutableUploadStore(shared, max_bytes=1024),
        store=BusinessStore(tmp_path / "business" / "business.sqlite3"),
        gateway=DoclibGateway(client, shared_root=shared), doclib=client, producer_version="4.0.6",
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(workflow.retry, initial.task.id)
        second = pool.submit(reopened.retry, initial.task.id)
        results = [first.result(timeout=5), second.result(timeout=5)]
    assert all(task.status == "submitted" and task.parse_ids == (8,) for task in results)
    assert store.get_task(initial.task.id).submission_attempt == 2
    assert [(request.submission_attempt, request.force) for request in requests] == [
        (1, False), (2, True), (2, True)
    ]


def test_lost_force_response_retries_same_generation_and_force_flag(tmp_path: Path) -> None:
    client = Mock(spec=DoclibInterface)
    requests: list[ParseRequest] = []
    force_calls = 0

    def submit(request: ParseRequest) -> ParseResponse:
        nonlocal force_calls
        requests.append(request)
        if request.force:
            force_calls += 1
            if force_calls == 1:
                raise ServerNotRunningError()
        return _parse_response(request.path, parse_id=8 if request.force else 7)

    client.ensure_parse.side_effect = submit
    workflow, store, _shared = _workflow(tmp_path, client)
    initial = workflow.submit(io.BytesIO(b"<h1>Lost force response</h1>"), filename="lost.html")
    client.get_parse.return_value = _parse_info(initial.document.sha256, status="failed")
    assert workflow.refresh(initial.task.id).status == "failed"
    uncertain = workflow.retry(initial.task.id)
    assert uncertain.status == "failed" and uncertain.error_code == "doclib_submission_failed"
    assert uncertain.submission_attempt == 2 and uncertain.submission_force is True
    recovered = workflow.retry(initial.task.id)
    assert recovered.status == "submitted" and recovered.parse_ids == (8,)
    assert recovered.submission_attempt == 2
    assert [(request.submission_attempt, request.force) for request in requests] == [
        (1, False), (2, True), (2, True)
    ]
    assert store.list_revisions(initial.document.id) == ()


def test_keyed_retry_replay_does_not_submit_again_after_quick_failure(tmp_path: Path) -> None:
    client = Mock(spec=DoclibInterface)
    requests: list[ParseRequest] = []

    def submit(request: ParseRequest) -> ParseResponse:
        requests.append(request)
        if request.force and len(requests) == 2:
            raise ServerNotRunningError()
        return _parse_response(request.path, parse_id=8 if request.force else 7)

    client.ensure_parse.side_effect = submit
    workflow, store, _shared = _workflow(tmp_path, client)
    initial = workflow.submit(io.BytesIO(b"<h1>Keyed retry</h1>"), filename="keyed.html")
    client.get_parse.return_value = _parse_info(initial.document.sha256, status="failed")
    assert workflow.refresh(initial.task.id).status == "failed"
    key = "manual-task-retry-request-0001"
    first = workflow.retry(initial.task.id, request_key=key)
    assert first.status == "failed" and first.error_code == "doclib_submission_failed"
    assert store.get_task_retry_request(key) == first
    assert workflow.retry(initial.task.id, request_key=key) == first
    assert len(requests) == 2
    second = workflow.retry(initial.task.id, request_key="manual-task-retry-request-0002")
    assert second.status == "submitted"
    assert len(requests) == 3
    assert store.get_task_retry_request(key) == second


def test_stale_failed_poll_cannot_fail_newer_submission_generation(tmp_path: Path) -> None:
    client = Mock(spec=DoclibInterface)
    client.ensure_parse.side_effect = lambda request: _parse_response(
        request.path, parse_id=8 if request.force else 7
    )
    workflow, store, _shared = _workflow(tmp_path, client)
    initial = workflow.submit(io.BytesIO(b"<h1>Stale failure</h1>"), filename="stale.html")

    def replace_before_old_failure(_parse_id: int) -> ParseInfo:
        store.mark_task_failed(initial.task.id, error_code="doclib_parse_failed")
        newer = workflow.retry(initial.task.id)
        assert newer.status == "submitted" and newer.submission_attempt == 2
        return _parse_info(initial.document.sha256, status="failed", parse_id=7)

    client.get_parse.side_effect = replace_before_old_failure
    observed = workflow.refresh(initial.task.id)
    assert observed.status == "submitted" and observed.parse_ids == (8,)
    assert store.list_revisions(initial.document.id) == ()


def test_stale_done_poll_cannot_create_revision_for_newer_generation(tmp_path: Path) -> None:
    client = Mock(spec=DoclibInterface)
    client.ensure_parse.side_effect = lambda request: _parse_response(
        request.path, parse_id=8 if request.force else 7
    )
    workflow, store, _shared = _workflow(tmp_path, client)
    initial = workflow.submit(io.BytesIO(b"<h1>Stale completion</h1>"), filename="stale.html")

    def replace_before_old_completion(_parse_id: int) -> ParseInfo:
        store.mark_task_failed(initial.task.id, error_code="doclib_parse_failed")
        newer = workflow.retry(initial.task.id)
        assert newer.status == "submitted" and newer.submission_attempt == 2
        return _parse_info(initial.document.sha256, status="done", parse_id=7)

    client.get_parse.side_effect = replace_before_old_completion
    observed = workflow.refresh(initial.task.id)
    assert observed.status == "submitted" and observed.parse_ids == (8,)
    assert store.list_revisions(initial.document.id) == ()


def test_submission_and_refresh_survive_new_service_instance(tmp_path: Path) -> None:
    client = Mock(spec=DoclibInterface)
    client.ensure_parse.side_effect = lambda request: _parse_response(request.path)
    workflow, store, shared = _workflow(tmp_path, client)

    submitted = workflow.submit(io.BytesIO(b"<h1>Lantern</h1>"), filename="report.html")
    assert submitted.task.status == "submitted"
    assert submitted.task.parse_ids == (7,)
    assert client.ensure_parse.call_args.args[0].consumer_key == f"business:{submitted.task.id}"
    assert (shared / submitted.document.storage_key).exists()
    client.get_parse.return_value = _parse_info(submitted.document.sha256, status="pending")
    assert workflow.refresh(submitted.task.id).status == "submitted"

    reopened = BusinessStore(tmp_path / "business" / "business.sqlite3")
    resumed = DocumentWorkflow(
        uploads=ImmutableUploadStore(shared, max_bytes=1024),
        store=reopened,
        gateway=DoclibGateway(client, shared_root=shared),
        doclib=client,
        producer_version="4.0.6",
    )
    client.get_parse.return_value = _parse_info(submitted.document.sha256)
    assert resumed.refresh(submitted.task.id).status == "done"
    assert resumed.refresh(submitted.task.id).status == "done"
    with pytest.raises(DocumentWorkflowError, match="not found"):
        resumed.refresh("unknown")
    assert store.get_document(submitted.document.id) == submitted.document


def test_templated_parse_completion_atomically_queues_one_unconfirmed_extraction(tmp_path: Path) -> None:
    client = Mock(spec=DoclibInterface)
    client.ensure_parse.side_effect = lambda request: _parse_response(request.path)
    workflow, store, _shared = _workflow(tmp_path, client)
    submitted = workflow.submit(
        io.BytesIO(b"<h1>Template</h1>"), filename="template.html", template_code="official_document",
    )
    client.get_parse.return_value = _parse_info(submitted.document.sha256)

    assert workflow.refresh(submitted.task.id).status == "done"
    revisions = store.list_revisions(submitted.document.id)
    assert len(revisions) == 1
    runs = store.list_extractions(revisions[0].id)
    assert len(runs) == 1
    assert runs[0].status == "queued"
    assert runs[0].template_code == "official_document" and runs[0].template_version == 1
    assert store.enqueue_extraction(revisions[0].id).id == runs[0].id
    assert workflow.refresh(submitted.task.id).status == "done"
    assert store.list_extractions(revisions[0].id) == runs


def test_untemplated_parse_completion_does_not_queue_field_extraction(tmp_path: Path) -> None:
    client = Mock(spec=DoclibInterface)
    client.ensure_parse.side_effect = lambda request: _parse_response(request.path)
    workflow, store, _shared = _workflow(tmp_path, client)
    submitted = workflow.submit(io.BytesIO(b"<h1>No template</h1>"), filename="plain.html")
    client.get_parse.return_value = _parse_info(submitted.document.sha256)
    assert workflow.refresh(submitted.task.id).status == "done"
    revision = store.list_revisions(submitted.document.id)[0]
    assert store.list_extractions(revision.id) == ()


def test_refresh_does_not_create_revision_after_concurrent_terminal_transition(tmp_path: Path) -> None:
    client = Mock(spec=DoclibInterface)
    client.ensure_parse.side_effect = lambda request: _parse_response(request.path)
    workflow, store, _shared = _workflow(tmp_path, client)
    submitted = workflow.submit(io.BytesIO(b"<h1>Race</h1>"), filename="race.html")

    def finish_after_failure(_parse_id: int) -> ParseInfo:
        store.mark_task_failed(submitted.task.id, error_code="concurrent_failure")
        return _parse_info(submitted.document.sha256)

    client.get_parse.side_effect = finish_after_failure

    assert workflow.refresh(submitted.task.id).status == "failed"
    assert store.list_revisions(submitted.document.id) == ()


def test_parallel_refreshes_commit_only_one_completed_revision(tmp_path: Path) -> None:
    client = Mock(spec=DoclibInterface)
    client.ensure_parse.side_effect = lambda request: _parse_response(request.path)
    workflow, store, _shared = _workflow(tmp_path, client)
    submitted = workflow.submit(
        io.BytesIO(b"<h1>Parallel finish</h1>"), filename="parallel.html", template_code="official_document",
    )
    barrier = Barrier(2)

    def completed_parse(_parse_id: int) -> ParseInfo:
        barrier.wait(timeout=5)
        return _parse_info(submitted.document.sha256)

    client.get_parse.side_effect = completed_parse
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(workflow.refresh, submitted.task.id)
        second = pool.submit(workflow.refresh, submitted.task.id)
        statuses = {first.result(timeout=10).status, second.result(timeout=10).status}

    assert statuses == {"done"}
    revisions = store.list_revisions(submitted.document.id)
    assert len(revisions) == 1
    assert len(store.list_extractions(revisions[0].id)) == 1


def test_request_key_replays_one_upload_without_suppressing_intentional_duplicate(tmp_path: Path) -> None:
    client = Mock(spec=DoclibInterface)
    client.ensure_parse.side_effect = lambda request: _parse_response(request.path)
    workflow, store, shared = _workflow(tmp_path, client)
    source = b"<h1>Same bytes</h1>"
    first = workflow.submit(io.BytesIO(source), filename="report.html", request_key="first-upload-request-key")
    reopened = DocumentWorkflow(
        uploads=ImmutableUploadStore(shared, max_bytes=1024),
        store=BusinessStore(tmp_path / "business" / "business.sqlite3"),
        gateway=DoclibGateway(client, shared_root=shared), doclib=client, producer_version="4.0.6",
    )
    replay = reopened.submit(io.BytesIO(source), filename="report.html", request_key="first-upload-request-key")
    assert replay == first
    assert client.ensure_parse.call_count == 1
    assert len(list(shared.iterdir())) == 1
    assert store.list_documents(limit=20, offset=0)[1] == 1

    new_intent = workflow.submit(io.BytesIO(source), filename="report.html", request_key="second-upload-request-key")
    assert new_intent.document.id != first.document.id
    assert client.ensure_parse.call_count == 2
    assert [call.args[0].consumer_key for call in client.ensure_parse.call_args_list] == [
        f"business:{first.task.id}", f"business:{new_intent.task.id}"
    ]
    assert store.list_documents(limit=20, offset=0)[1] == 2

    with pytest.raises(ValueError, match="Idempotency key"):
        workflow.submit(io.BytesIO(b"<h1>Different bytes</h1>"), filename="report.html",
                        request_key="first-upload-request-key")
    with pytest.raises(ValueError, match="Idempotency key"):
        workflow.submit(io.BytesIO(source), filename="different.html", request_key="first-upload-request-key")
    with pytest.raises(ValueError, match="Idempotency key"):
        workflow.submit(io.BytesIO(source), filename="report.html", template_code="official_document",
                        request_key="first-upload-request-key")
    assert len(list(shared.iterdir())) == 2
    assert client.ensure_parse.call_count == 2


def test_parallel_replay_cannot_create_second_document(tmp_path: Path) -> None:
    client = Mock(spec=DoclibInterface)
    client.ensure_parse.side_effect = lambda request: _parse_response(request.path)
    workflow, store, shared = _workflow(tmp_path, client)
    barrier = Barrier(2)

    def submit() -> DocumentSubmission:
        barrier.wait()
        return workflow.submit(io.BytesIO(b"<h1>Parallel</h1>"), filename="report.html",
                               request_key="parallel-upload-request-key")

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(submit)
        second = pool.submit(submit)
        results = (first.result(), second.result())
    assert len({result.document.id for result in results}) == 1
    assert len({result.task.id for result in results}) == 1
    assert store.list_documents(limit=20, offset=0)[1] == 1
    assert len(list(shared.iterdir())) == 1
    assert client.ensure_parse.call_count == 1


def test_pdf_all_pages_become_one_logical_revision_across_parse_batches(tmp_path: Path) -> None:
    client = Mock(spec=DoclibInterface)

    def ensure(request: object) -> ParseResponse:
        assert request.page_range == "all" and request.remote is False
        response = _parse_response(request.path)
        return response.model_copy(update={"page_range": "1-13", "created_parse_ids": [7, 8]})

    client.ensure_parse.side_effect = ensure
    workflow, store, _shared = _workflow(tmp_path, client)
    submitted = workflow.submit(io.BytesIO(b"%PDF-1.7\nlong document"), filename="report.pdf", tier="flash")
    assert submitted.task.parse_ids == (7, 8)
    client.get_parse.side_effect = lambda parse_id: _parse_info(
        submitted.document.sha256, parse_id=parse_id,
        page_range="1-10" if parse_id == 7 else "11-13", short_id=submitted.document.sha256[:7],
    )
    client.get_doc.return_value.page_count = 13
    assert workflow.refresh(submitted.task.id).status == "done"
    revisions = store.list_revisions(submitted.document.id)
    assert len(revisions) == 1
    assert revisions[0].page_range == "1-13"
    assert revisions[0].short_id == submitted.document.sha256[:7]
    assert revisions[0].parse_id_for_page(1) == 7
    assert revisions[0].parse_id_for_page(13) == 8
    assert revisions[0].parse_id_for_page(14) is None


def test_duplicate_completed_pdf_range_uses_newest_batch_without_losing_pages(tmp_path: Path) -> None:
    client = Mock(spec=DoclibInterface)
    client.ensure_parse.side_effect = lambda request: _parse_response(request.path).model_copy(
        update={"created_parse_ids": [7, 8, 9]}
    )
    workflow, store, _shared = _workflow(tmp_path, client)
    submitted = workflow.submit(io.BytesIO(b"%PDF-1.7\nlong document"), filename="report.pdf", tier="flash")
    client.get_parse.side_effect = lambda parse_id: _parse_info(
        submitted.document.sha256,
        parse_id=parse_id,
        page_range="1-10" if parse_id in (7, 8) else "11-13",
        short_id=submitted.document.sha256[:7],
    )
    client.get_doc.return_value.page_count = 13
    assert workflow.refresh(submitted.task.id).status == "done"
    revision = store.list_revisions(submitted.document.id)[0]
    assert revision.page_range == "1-13"
    assert revision.parse_id_for_page(1) == 8
    assert revision.parse_id_for_page(13) == 9
    assert submitted.task.parse_ids == (7, 8, 9)


def test_same_range_with_conflicting_parse_identity_still_fails(tmp_path: Path) -> None:
    client = Mock(spec=DoclibInterface)
    client.ensure_parse.side_effect = lambda request: _parse_response(request.path).model_copy(
        update={"created_parse_ids": [7, 8]}
    )
    workflow, store, _shared = _workflow(tmp_path, client)
    submitted = workflow.submit(io.BytesIO(b"<h1>Lantern</h1>"), filename="report.html")
    client.get_parse.side_effect = lambda parse_id: _parse_info(
        submitted.document.sha256,
        parse_id=parse_id,
        short_id=submitted.document.sha256[:12] if parse_id == 7 else "different-id",
    )
    failed = workflow.refresh(submitted.task.id)
    assert failed.status == "failed" and failed.error_code == "parse_batch_invalid"
    assert store.list_revisions(submitted.document.id) == ()


def test_pdf_missing_pages_fail_without_creating_a_completed_revision(tmp_path: Path) -> None:
    client = Mock(spec=DoclibInterface)
    client.ensure_parse.side_effect = lambda request: _parse_response(request.path)
    workflow, store, _shared = _workflow(tmp_path, client)
    submitted = workflow.submit(io.BytesIO(b"%PDF-1.7\nlong document"), filename="report.pdf", tier="flash")
    client.get_parse.return_value = _parse_info(submitted.document.sha256, page_range="1-10")
    client.get_doc.return_value.page_count = 13
    task = workflow.refresh(submitted.task.id)
    assert task.status == "failed" and task.error_code == "parse_coverage_incomplete"
    assert store.list_revisions(submitted.document.id) == ()
    workflow.retry(task.id)
    assert client.ensure_parse.call_args.args[0].force is True


def test_overlapping_batches_fail_as_retryable_task_without_a_revision(tmp_path: Path) -> None:
    client = Mock(spec=DoclibInterface)
    client.ensure_parse.side_effect = lambda request: _parse_response(request.path).model_copy(
        update={"created_parse_ids": [7, 8]}
    )
    workflow, store, _shared = _workflow(tmp_path, client)
    submitted = workflow.submit(io.BytesIO(b"<h1>Lantern</h1>"), filename="report.html")
    client.get_parse.side_effect = lambda parse_id: _parse_info(
        submitted.document.sha256, parse_id=parse_id, page_range="1-2" if parse_id == 7 else "2-3"
    )
    failed = workflow.refresh(submitted.task.id)
    assert failed.status == "failed" and failed.error_code == "parse_batch_invalid"
    assert store.list_revisions(submitted.document.id) == ()
    workflow.retry(failed.id)
    assert client.ensure_parse.call_args.args[0].force is True


def test_doclib_outage_keeps_source_and_failed_task_for_retry(tmp_path: Path) -> None:
    client = Mock(spec=DoclibInterface)
    client.ensure_parse.side_effect = [ServerNotRunningError(), None]
    workflow, store, shared = _workflow(tmp_path, client)
    submitted = workflow.submit(io.BytesIO(b"<h1>Retained</h1>"), filename="report.html")
    assert submitted.task.status == "failed"
    assert submitted.task.error_code == "doclib_submission_failed"
    assert (shared / submitted.document.storage_key).exists()

    client.ensure_parse.side_effect = lambda request: _parse_response(request.path)
    retried = workflow.retry(submitted.task.id)
    assert retried.status == "submitted"
    assert retried.error_code is None
    assert store.get_task(submitted.task.id) == retried


def test_parse_failure_requires_forced_retry(tmp_path: Path) -> None:
    client = Mock(spec=DoclibInterface)
    client.ensure_parse.side_effect = lambda request: _parse_response(request.path, parse_id=8 if request.force else 7)
    workflow, _store, _shared = _workflow(tmp_path, client)
    submitted = workflow.submit(io.BytesIO(b"<h1>Retry</h1>"), filename="report.html")
    client.get_parse.return_value = _parse_info(submitted.document.sha256, status="failed")
    failed = workflow.refresh(submitted.task.id)
    assert failed.status == "failed"
    assert failed.error_code == "doclib_parse_failed"

    retried = workflow.retry(submitted.task.id)
    assert retried.parse_ids == (8,)
    assert client.ensure_parse.call_args.args[0].force is True


def test_unexpected_skipped_batch_fails_business_task_without_revision(tmp_path: Path) -> None:
    client = Mock(spec=DoclibInterface)
    client.ensure_parse.side_effect = lambda request: _parse_response(request.path)
    workflow, store, _shared = _workflow(tmp_path, client)
    submitted = workflow.submit(io.BytesIO(b"<h1>Skipped</h1>"), filename="report.html")
    client.get_parse.return_value = _parse_info(submitted.document.sha256, status="skipped")

    failed = workflow.refresh(submitted.task.id)
    assert failed.status == "failed" and failed.error_code == "doclib_parse_failed"
    assert store.list_revisions(submitted.document.id) == ()


def test_invalid_tier_or_database_failure_does_not_leave_upload(tmp_path: Path) -> None:
    client = Mock(spec=DoclibInterface)
    workflow, _store, shared = _workflow(tmp_path, client, initialize_db=False)
    with pytest.raises(ValueError, match="flash"):
        workflow.submit(io.BytesIO(b"<h1>Wrong</h1>"), filename="report.html", tier="advanced")
    with pytest.raises(ValueError, match="explicit"):
        workflow.submit(io.BytesIO(b"%PDF-1.7"), filename="report.pdf")
    with pytest.raises(ValueError, match="initialized"):
        workflow.submit(io.BytesIO(b"<h1>No DB</h1>"), filename="report.html")
    assert list(shared.iterdir()) == []
    client.ensure_parse.assert_not_called()


def test_changed_source_is_not_accepted_on_retry(tmp_path: Path) -> None:
    client = Mock(spec=DoclibInterface)
    client.ensure_parse.side_effect = ServerNotRunningError()
    workflow, store, shared = _workflow(tmp_path, client)
    submitted = workflow.submit(io.BytesIO(b"<h1>Original</h1>"), filename="report.html")
    source = shared / submitted.document.storage_key
    source.chmod(0o600)
    source.write_bytes(b"<h1>Changed</h1>")
    client.ensure_parse.side_effect = lambda request: _parse_response(request.path)

    with pytest.raises(DocumentWorkflowError, match="integrity"):
        workflow.retry(submitted.task.id)
    task = store.get_task(submitted.task.id)
    assert task is not None
    assert task.error_code == "source_integrity_failed"


def test_changed_source_fails_before_unavailable_doclib_is_called(tmp_path: Path) -> None:
    client = Mock(spec=DoclibInterface)
    client.ensure_parse.side_effect = ServerNotRunningError()
    workflow, store, shared = _workflow(tmp_path, client)
    initial = workflow.submit(io.BytesIO(b"<h1>Original</h1>"), filename="report.html")
    source = shared / initial.document.storage_key
    source.chmod(0o600)
    source.write_bytes(b"<h1>Replaced</h1>")
    client.ensure_parse.reset_mock(side_effect=True)
    client.ensure_parse.side_effect = ServerNotRunningError()

    with pytest.raises(DocumentWorkflowError, match="integrity"):
        workflow.retry(initial.task.id)
    task = store.get_task(initial.task.id)
    assert task is not None and task.error_code == "source_integrity_failed"
    client.ensure_parse.assert_not_called()


def test_submitted_task_rejects_changed_source_before_revision(tmp_path: Path) -> None:
    client = Mock(spec=DoclibInterface)
    client.ensure_parse.side_effect = lambda request: _parse_response(request.path)
    workflow, store, shared = _workflow(tmp_path, client)
    submitted = workflow.submit(io.BytesIO(b"<h1>Original</h1>"), filename="report.html")
    source = shared / submitted.document.storage_key
    source.chmod(0o600)
    source.write_bytes(b"<h1>Replaced</h1>")
    client.get_parse.return_value = _parse_info(submitted.document.sha256)

    failed = workflow.refresh(submitted.task.id)
    assert failed.status == "failed" and failed.error_code == "source_integrity_failed"
    assert store.list_revisions(submitted.document.id) == ()


def test_submitted_task_rejects_missing_source_before_revision(tmp_path: Path) -> None:
    client = Mock(spec=DoclibInterface)
    client.ensure_parse.side_effect = lambda request: _parse_response(request.path)
    workflow, store, shared = _workflow(tmp_path, client)
    submitted = workflow.submit(io.BytesIO(b"<h1>Original</h1>"), filename="report.html")
    (shared / submitted.document.storage_key).unlink()
    client.get_parse.return_value = _parse_info(submitted.document.sha256)

    failed = workflow.refresh(submitted.task.id)
    assert failed.status == "failed" and failed.error_code == "source_unavailable"
    assert store.list_revisions(submitted.document.id) == ()
