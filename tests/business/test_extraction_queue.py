"""SQLite extraction leases prevent stale workers from publishing partial results."""

from __future__ import annotations

import io
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import mineru.business.store.sqlite as sqlite_store
from mineru.business.documents import ImmutableUploadStore
from mineru.business.store import BusinessStore, BusinessStoreError
from mineru.doclib import ParseInfo


def _queued(tmp_path: Path) -> tuple[BusinessStore, str, str]:
    store = BusinessStore(tmp_path / "business.sqlite3")
    store.initialize()
    upload = ImmutableUploadStore(tmp_path, max_bytes=1024).store(io.BytesIO(b"source"), filename="source.html")
    document = store.create_document(upload, original_name="source.html", template_code="official_document")
    parse = ParseInfo(
        id=3, sha256=upload.sha256, short_id=upload.sha256[:12], tier="flash", page_range="1",
        status="done", privacy="local", created_at=1, updated_at=2, done_at=2,
    )
    revision = store.add_completed_revision(document.id, parse=parse, producer_version="4.0.6")
    evidence = store.capture_evidence(
        revision.id, locator=f"doc:{upload.sha256[:12]}/tier:flash/page:1", snippet="标题：真实内容"
    )
    queued = store.enqueue_extraction(revision.id)
    assert queued.status == "queued"
    return store, revision.id, evidence.id


def test_enqueue_is_idempotent_while_active_and_persists_across_restart(tmp_path: Path) -> None:
    store, revision_id, _evidence_id = _queued(tmp_path)
    first = store.list_extractions(revision_id)[0]
    assert store.enqueue_extraction(revision_id) == first
    reopened = BusinessStore(tmp_path / "business.sqlite3")
    reopened.initialize()
    assert reopened.list_extractions(revision_id) == (first,)
    claimed = reopened.claim_next_extraction()
    assert claimed is not None and claimed.id == first.id
    assert claimed.status == "running" and claimed.attempts == 1
    assert claimed.claim_token and claimed.lease_until_ms
    assert reopened.enqueue_extraction(revision_id).id == first.id


def test_request_key_replays_terminal_extraction_without_creating_another_run(tmp_path: Path) -> None:
    store, revision_id, _evidence_id = _queued(tmp_path)
    request_key = "manual-extraction-request-0001"
    first = store.enqueue_extraction(revision_id, request_key=request_key)
    claimed = store.claim_next_extraction()
    assert claimed is not None and claimed.id == first.id and claimed.claim_token is not None
    terminal = store.fail_extraction(claimed.id, claim_token=claimed.claim_token, error_code="test_failure")
    assert terminal.status == "failed"

    reopened = BusinessStore(tmp_path / "business.sqlite3")
    assert reopened.get_extraction_request(request_key) == terminal
    assert reopened.enqueue_extraction(revision_id, request_key=request_key) == terminal
    another = reopened.enqueue_extraction(revision_id, request_key="manual-extraction-request-0002")
    assert another.id != first.id
    assert len(reopened.list_extractions(revision_id)) == 2


def test_request_key_cannot_be_reused_for_another_revision(tmp_path: Path) -> None:
    store, revision_id, _evidence_id = _queued(tmp_path)
    key = "manual-extraction-request-0003"
    first = store.enqueue_extraction(revision_id, request_key=key)
    prior = store.get_revision(revision_id)
    assert prior is not None
    other = store.add_completed_revision(
        prior.document_id,
        parse=ParseInfo(
            id=4, sha256=prior.sha256, short_id=prior.short_id, tier=prior.tier, page_range="1",
            status="done", privacy="local", created_at=3, updated_at=4, done_at=4,
        ),
        producer_version="4.0.6",
    )
    with pytest.raises(BusinessStoreError, match="different extraction"):
        store.enqueue_extraction(other.id, request_key=key)
    assert store.get_extraction_request(key) == first


def test_parallel_extraction_request_key_creates_one_run(tmp_path: Path) -> None:
    store, revision_id, _evidence_id = _queued(tmp_path)
    key = "manual-extraction-request-0005"
    reopened = BusinessStore(tmp_path / "business.sqlite3")
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(store.enqueue_extraction, revision_id, request_key=key)
        second = pool.submit(reopened.enqueue_extraction, revision_id, request_key=key)
        results = [first.result(timeout=5), second.result(timeout=5)]
    assert results[0].id == results[1].id
    assert store.get_extraction_request(key).id == results[0].id
    assert len(store.list_extractions(revision_id)) == 1


def test_expired_lease_reclaims_same_run_without_old_partial_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, revision_id, evidence_id = _queued(tmp_path)
    clock = [1000]
    monkeypatch.setattr(sqlite_store, "_now_ms", lambda: clock[0])
    first = store.claim_next_extraction(lease_ms=1000)
    assert first is not None and first.claim_token is not None
    store.add_field_candidate(
        first.id, claim_token=first.claim_token, field_code="title", value="真实内容", evidence_id=evidence_id
    )
    assert len(store.list_field_candidates(first.id)) == 1
    clock[0] = 2001
    reopened = BusinessStore(tmp_path / "business.sqlite3")
    second = reopened.claim_next_extraction(lease_ms=1000)
    assert second is not None and second.id == first.id and second.claim_token != first.claim_token
    assert second.attempts == 2
    assert reopened.list_field_candidates(second.id) == ()
    with pytest.raises(BusinessStoreError, match="active claim"):
        store.add_field_candidate(
            first.id, claim_token=first.claim_token, field_code="title", value="真实内容",
            evidence_id=evidence_id,
        )
    with pytest.raises(BusinessStoreError, match="active"):
        store.finish_extraction(first.id, claim_token=first.claim_token, complete_coverage=True)
    with pytest.raises(BusinessStoreError, match="active"):
        store.fail_extraction(first.id, claim_token=first.claim_token, error_code="stale")
    reopened.add_field_candidate(
        second.id, claim_token=second.claim_token, field_code="title", value="真实内容", evidence_id=evidence_id
    )
    finished = reopened.finish_extraction(second.id, claim_token=second.claim_token, complete_coverage=True)
    assert finished.status == "done" and finished.attempts == 2
    assert reopened.claim_next_extraction() is None
    assert reopened.list_extractions(revision_id)[0].status == "done"


def test_expired_max_attempts_becomes_failed_instead_of_stuck_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _revision_id, _evidence_id = _queued(tmp_path)
    clock = [1000]
    monkeypatch.setattr(sqlite_store, "_now_ms", lambda: clock[0])
    first = store.claim_next_extraction(lease_ms=1000, max_attempts=2)
    assert first is not None
    clock[0] = 2001
    second = store.claim_next_extraction(lease_ms=1000, max_attempts=2)
    assert second is not None and second.attempts == 2
    clock[0] = 3002
    assert store.claim_next_extraction(lease_ms=1000, max_attempts=2) is None
    failed = store.get_extraction(second.id)
    assert failed is not None
    assert failed.status == "failed" and failed.error_code == "worker_retries_exhausted"
    assert failed.claim_token is None and failed.lease_until_ms is None
