"""SQLite extraction leases prevent stale workers from publishing partial results."""

from __future__ import annotations

import io
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
