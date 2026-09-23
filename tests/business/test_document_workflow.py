"""The business ingestion workflow must remain recoverable across failures."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path
from unittest.mock import Mock

import pytest

from mineru.business.documents import DoclibGateway, ImmutableUploadStore
from mineru.business.services import DocumentWorkflow, DocumentWorkflowError
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
        submitted.document.sha256, parse_id=parse_id, page_range="1"
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
