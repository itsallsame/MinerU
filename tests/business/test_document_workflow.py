"""The business ingestion workflow must remain recoverable across failures."""

from __future__ import annotations

import hashlib
import io
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from unittest.mock import Mock

import pytest

from mineru.business.documents import DoclibGateway, ImmutableUploadStore
from mineru.business.services import DocumentSubmission, DocumentWorkflow, DocumentWorkflowError
from mineru.business.store import BusinessStore
from mineru.doclib import DoclibInterface, ParseInfo, ParseResponse
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
    submitted = workflow.submit(io.BytesIO(b"<h1>Parallel finish</h1>"), filename="parallel.html")
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
    assert len(store.list_revisions(submitted.document.id)) == 1


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
