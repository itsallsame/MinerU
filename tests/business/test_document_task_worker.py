"""Open document tasks progress across API restarts without per-task browser polling."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path
from unittest.mock import Mock

import pytest

from mineru.business.documents import DoclibGateway, ImmutableUploadStore, UploadError
from mineru.business.services import DocumentTaskWorker, DocumentWorkflow
from mineru.business.store import BusinessStore, BusinessStoreError
from mineru.doclib import DoclibInterface, ParseInfo, ParseRequest, ParseResponse
from mineru.doclib.types import ParseReleaseResponse
from mineru.errors import ServerNotRunningError


def _setup(tmp_path: Path) -> tuple[DocumentWorkflow, BusinessStore, ImmutableUploadStore, Mock]:
    database = tmp_path / "business.sqlite3"
    store = BusinessStore(database)
    store.initialize()
    (tmp_path / "uploads").mkdir()
    uploads = ImmutableUploadStore(tmp_path / "uploads", max_bytes=1024)
    client = Mock(spec=DoclibInterface)
    workflow = DocumentWorkflow(
        uploads=uploads,
        store=store,
        gateway=DoclibGateway(client, shared_root=tmp_path / "uploads"),
        doclib=client,
        producer_version="4.0.6",
    )
    return workflow, store, uploads, client


def _response(request: ParseRequest) -> ParseResponse:
    sha256 = hashlib.sha256(Path(request.path).read_bytes()).hexdigest()
    return ParseResponse(
        sha256=sha256,
        short_id=sha256[:12],
        tier="flash",
        page_range="1",
        status="pending",
        created_parse_ids=[7],
    )


def test_worker_recovers_uploaded_and_completes_submitted_after_restart(tmp_path: Path) -> None:
    _workflow, store, uploads, client = _setup(tmp_path)
    stored = uploads.store(io.BytesIO(b"<h1>Recover</h1>"), filename="recover.html")
    document, task = store.create_document_with_task(stored, original_name="recover.html", requested_tier=None)
    client.ensure_parse.side_effect = _response
    reopened_store = BusinessStore(tmp_path / "business.sqlite3")
    reopened_workflow = DocumentWorkflow(
        uploads=uploads,
        store=reopened_store,
        gateway=DoclibGateway(client, shared_root=tmp_path / "uploads"),
        doclib=client,
        producer_version="4.0.6",
    )
    worker = DocumentTaskWorker(reopened_workflow, reopened_store)

    assert worker.run_once() is True
    assert reopened_store.get_task(task.id).status == "submitted"
    assert worker.run_once() is False
    client.get_parse.return_value = ParseInfo(
        id=7,
        sha256=document.sha256,
        short_id=document.sha256[:12],
        tier="flash",
        page_range="1",
        status="done",
        privacy="local",
        created_at=1,
        updated_at=2,
        done_at=2,
    )
    assert worker.run_once() is True
    assert reopened_store.get_task(task.id).status == "done"
    assert len(reopened_store.list_revisions(document.id)) == 1
    assert worker.run_once() is False
    assert worker.run_once() is False
    assert client.ensure_parse.call_args.args[0].submission_attempt == 1


def test_worker_replays_ambiguous_submission_without_new_generation(tmp_path: Path) -> None:
    workflow, store, _uploads, client = _setup(tmp_path)
    seen: list[tuple[int | None, bool]] = []

    def submit(request: ParseRequest) -> ParseResponse:
        seen.append((request.submission_attempt, request.force))
        if len(seen) == 1:
            raise ServerNotRunningError()
        return _response(request)

    client.ensure_parse.side_effect = submit
    result = workflow.submit(io.BytesIO(b"<h1>Unknown</h1>"), filename="unknown.html")
    assert result.task.status == "failed" and result.task.error_code == "doclib_submission_failed"
    worker = DocumentTaskWorker(workflow, store)
    assert worker.run_once() is True
    recovered = store.get_task(result.task.id)
    assert recovered.status == "submitted" and recovered.submission_attempt == 1
    assert seen == [(1, False), (1, False)]


def test_worker_recovers_stranded_submitting_generation(tmp_path: Path) -> None:
    workflow, store, uploads, client = _setup(tmp_path)
    stored = uploads.store(io.BytesIO(b"<h1>Interrupted</h1>"), filename="interrupted.html")
    _document, task = store.create_document_with_task(stored, original_name="interrupted.html", requested_tier=None)
    assert store.begin_task_submission(task.id).status == "submitting"
    client.ensure_parse.side_effect = _response

    worker = DocumentTaskWorker(workflow, store)
    assert worker.run_once() is True
    recovered = store.get_task(task.id)
    assert recovered.status == "submitted" and recovered.submission_attempt == 1
    assert client.ensure_parse.call_args.args[0].submission_attempt == 1


def test_worker_scans_all_pending_tasks_before_wrapping(tmp_path: Path) -> None:
    workflow, store, uploads, client = _setup(tmp_path)
    client.ensure_parse.side_effect = _response
    tasks = []
    for name in ("a.html", "b.html", "c.html"):
        stored = uploads.store(io.BytesIO(name.encode()), filename=name)
        _document, task = store.create_document_with_task(stored, original_name=name, requested_tier=None)
        tasks.append(task)
    worker = DocumentTaskWorker(workflow, store)
    assert [worker.run_once() for _ in tasks] == [True] * len(tasks)
    assert worker.run_once() is False
    assert all(store.get_task(task.id).status == "submitted" for task in tasks)
    assert client.ensure_parse.call_count == len(tasks)


def test_worker_finishes_unknown_cancel_and_does_not_resubmit(tmp_path: Path) -> None:
    workflow, store, _uploads, client = _setup(tmp_path)
    client.ensure_parse.side_effect = _response
    result = workflow.submit(io.BytesIO(b"<h1>Cancel</h1>"), filename="cancel.html")
    client.release_parse_consumer.side_effect = [
        ServerNotRunningError(),
        ParseReleaseResponse(consumer_key=f"business:{result.task.id}", results=[]),
    ]
    assert workflow.cancel(result.task.id).status == "cancel_requested"
    worker = DocumentTaskWorker(workflow, store)
    assert worker.run_once() is True
    assert store.get_task(result.task.id).status == "cancelled"
    assert client.ensure_parse.call_count == 1
    assert worker.run_once() is False


def test_worker_marks_missing_retained_source_failed_instead_of_looping(tmp_path: Path) -> None:
    _workflow, store, uploads, client = _setup(tmp_path)
    stored = uploads.store(io.BytesIO(b"<h1>Missing</h1>"), filename="missing.html")
    _document, task = store.create_document_with_task(stored, original_name="missing.html", requested_tier=None)
    stored.path.unlink()
    workflow = DocumentWorkflow(
        uploads=uploads,
        store=store,
        gateway=DoclibGateway(client, shared_root=tmp_path / "uploads"),
        doclib=client,
        producer_version="4.0.6",
    )
    worker = DocumentTaskWorker(workflow, store)
    assert worker.run_once() is True
    failed = store.get_task(task.id)
    assert failed.status == "failed" and failed.error_code == "source_unavailable"
    assert worker.run_once() is False
    client.ensure_parse.assert_not_called()


def test_missing_source_result_cannot_override_concurrent_submission(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workflow, store, uploads, client = _setup(tmp_path)
    stored = uploads.store(io.BytesIO(b"<h1>Race</h1>"), filename="race.html")
    _document, task = store.create_document_with_task(stored, original_name="race.html", requested_tier=None)

    def competing_submission(_storage_key: str) -> Path:
        store.mark_task_submitted(task.id, actual_tier="flash", parse_ids=(7,), submission_attempt=1)
        raise UploadError("Source unavailable to this caller")

    monkeypatch.setattr(uploads, "source_path", competing_submission)
    worker = DocumentTaskWorker(workflow, store)
    assert worker.run_once() is True
    assert store.get_task(task.id).status == "submitted"
    client.ensure_parse.assert_not_called()


def test_recovery_scan_is_bounded_and_rejects_invalid_limit(tmp_path: Path) -> None:
    _workflow, store, uploads, _client = _setup(tmp_path)
    for name in ("a.html", "b.html", "c.html"):
        stored = uploads.store(io.BytesIO(name.encode()), filename=name)
        store.create_document_with_task(stored, original_name=name, requested_tier=None)
    first = store.list_recoverable_task_ids(limit=2)
    second = store.list_recoverable_task_ids(after_id=first[-1], limit=2)
    assert len(first) == 2 and len(second) == 1
    assert set(first + second) == set(store.list_recoverable_task_ids())
    with pytest.raises(BusinessStoreError, match="limit"):
        store.list_recoverable_task_ids(limit=0)
