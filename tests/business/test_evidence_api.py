"""Frozen evidence remains readable when current Doclib navigation drifts."""

from __future__ import annotations

import io
import sqlite3
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock

from fastapi.testclient import TestClient

from mineru.business.api import create_app
from mineru.business.documents import ImmutableUploadStore
from mineru.business.services import DocumentWorkflow, EvidenceReader, EvidenceWriter
from mineru.business.store import BusinessStore
from mineru.doclib import DoclibInterface, ParseInfo
from mineru.doclib.types import ContentRequestScope, DocContentResponse
from mineru.errors import ServerNotRunningError


def _fixture(tmp_path: Path) -> tuple[BusinessStore, Mock, str, str, str]:
    shared = tmp_path / "shared"
    shared.mkdir()
    database_dir = tmp_path / "business"
    database_dir.mkdir()
    store = BusinessStore(database_dir / "business.sqlite3")
    store.initialize()
    upload = ImmutableUploadStore(shared, max_bytes=1024).store(io.BytesIO(b"<h1>Lantern</h1>"), filename="report.html")
    document = store.create_document(upload, original_name="report.html")
    parse = ParseInfo(
        id=7,
        sha256=upload.sha256,
        short_id=upload.sha256[:12],
        tier="flash",
        page_range="1",
        status="done",
        privacy="local",
        created_at=1,
        updated_at=2,
        done_at=2,
    )
    revision = store.add_completed_revision(document.id, parse=parse, producer_version="4.0.6")
    locator = f"doc:{upload.sha256[:12]}/tier:flash/page:1/block:1"
    evidence = store.capture_evidence(revision.id, locator=locator, snippet="Lantern")
    doclib = Mock(spec=DoclibInterface)
    doclib.read_content.return_value = DocContentResponse(
        sha256=upload.sha256,
        short_id=upload.sha256[:12],
        tier="flash",
        content="Lantern",
        request_scope=ContentRequestScope(locator=locator),
    )
    return store, doclib, document.id, revision.id, evidence.id


def test_evidence_reader_distinguishes_current_match_change_and_outage(tmp_path: Path) -> None:
    store, doclib, _document_id, _revision_id, evidence_id = _fixture(tmp_path)
    reader = EvidenceReader(store=store, doclib=doclib)
    assert reader.inspect(evidence_id).navigation_status == "current_match"
    assert reader.inspect("missing") is None

    doclib.read_content.return_value = doclib.read_content.return_value.model_copy(update={"content": "Changed"})
    changed = reader.inspect(evidence_id)
    assert changed.navigation_status == "changed"
    assert changed.snapshot.snippet == "Lantern"

    doclib.read_content.return_value = doclib.read_content.return_value.model_copy(
        update={"content": "Lantern", "sha256": "0" * 64}
    )
    assert reader.inspect(evidence_id).navigation_status == "changed"

    doclib.read_content.return_value = doclib.read_content.return_value.model_copy(update={"truncated": True})
    assert reader.inspect(evidence_id).navigation_status == "unavailable"
    doclib.read_content.side_effect = ServerNotRunningError()
    assert reader.inspect(evidence_id).navigation_status == "unavailable"


def test_corrupted_frozen_evidence_is_not_served_as_a_valid_snapshot(tmp_path: Path) -> None:
    store, doclib, document_id, revision_id, evidence_id = _fixture(tmp_path)
    client = TestClient(create_app(
        workflow=Mock(spec=DocumentWorkflow), store=store,
        evidence_reader=EvidenceReader(store=store, doclib=doclib),
        evidence_writer=EvidenceWriter(store=store, doclib=doclib),
    ))
    with closing(sqlite3.connect(tmp_path / "business" / "business.sqlite3")) as database, database:
        database.execute("UPDATE evidence SET snippet=? WHERE id=?", ("Altered source", evidence_id))
    detail = client.get(f"/api/business/evidence/{evidence_id}")
    listed = client.get(f"/api/business/revisions/{revision_id}/evidence")
    assert detail.status_code == 409 and detail.json()["detail"] == "Frozen evidence checksum mismatch"
    assert listed.status_code == 409 and listed.json()["detail"] == "Frozen evidence checksum mismatch"
    assert "Altered source" not in detail.text and "Altered source" not in listed.text
    assert client.get(f"/api/business/documents/{document_id}/revisions").status_code == 200
    doclib.read_content.assert_not_called()


def test_open_api_exposes_revisions_and_frozen_evidence_without_internal_ids(tmp_path: Path) -> None:
    store, doclib, document_id, revision_id, evidence_id = _fixture(tmp_path)
    client = TestClient(
        create_app(
            workflow=Mock(spec=DocumentWorkflow),
            store=store,
            evidence_reader=EvidenceReader(store=store, doclib=doclib),
            evidence_writer=EvidenceWriter(store=store, doclib=doclib),
        )
    )
    revisions = client.get(f"/api/business/documents/{document_id}/revisions")
    assert revisions.status_code == 200
    assert revisions.json()[0]["id"] == revision_id
    assert "doclib_parse_id" not in revisions.json()[0]
    assert client.get("/api/business/documents/missing/revisions").status_code == 404

    listed = client.get(f"/api/business/revisions/{revision_id}/evidence")
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == evidence_id
    assert "navigation_status" not in listed.json()[0]
    assert client.get("/api/business/revisions/missing/evidence").status_code == 404

    detail = client.get(f"/api/business/evidence/{evidence_id}")
    assert detail.status_code == 200
    assert detail.json()["snippet"] == "Lantern"
    assert detail.json()["navigation_status"] == "current_match"
    assert client.get("/api/business/evidence/missing").status_code == 404


def test_api_captures_only_historical_server_content(tmp_path: Path) -> None:
    store, doclib, _document_id, revision_id, evidence_id = _fixture(tmp_path)
    locator = store.get_evidence(evidence_id).locator
    doclib.read_parse_content.return_value = doclib.read_content.return_value.model_copy(
        update={"content": "Historical server text"}
    )
    client = TestClient(
        create_app(
            workflow=Mock(spec=DocumentWorkflow),
            store=store,
            evidence_reader=EvidenceReader(store=store, doclib=doclib),
            evidence_writer=EvidenceWriter(store=store, doclib=doclib),
        )
    )
    response = client.post(f"/api/business/revisions/{revision_id}/evidence", json={"locator": locator})
    assert response.status_code == 201
    assert response.json()["snippet"] == "Historical server text"
    assert "doclib_parse_id" not in response.json()
    assert store.get_evidence(response.json()["id"]).snippet == "Historical server text"
    doclib.read_parse_content.assert_called_once_with(7, locator, limit=30000)
    assert client.post("/api/business/revisions/missing/evidence", json={"locator": locator}).status_code == 404
    spoofed = client.post(f"/api/business/revisions/{revision_id}/evidence", json={"locator": locator, "snippet": "fake"})
    assert spoofed.status_code == 422
    assert client.post(f"/api/business/revisions/{revision_id}/evidence", json={"locator": "not-a-locator"}).status_code == 422

    doclib.read_parse_content.return_value = doclib.read_parse_content.return_value.model_copy(update={"truncated": True})
    rejected = client.post(f"/api/business/revisions/{revision_id}/evidence", json={"locator": locator})
    assert rejected.status_code == 409
    assert rejected.json()["detail"] == "historical_content_unavailable"

    doclib.read_parse_content.return_value = doclib.read_parse_content.return_value.model_copy(
        update={"truncated": False, "sha256": "0" * 64}
    )
    rejected = client.post(f"/api/business/revisions/{revision_id}/evidence", json={"locator": locator})
    assert rejected.status_code == 409
    assert rejected.json()["detail"] == "historical_content_mismatch"


__all__ = []
