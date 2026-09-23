"""Browser and Skill share an open business API without leaking Doclib paths."""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import Mock

from fastapi.testclient import TestClient

from mineru.business.api import create_app
from mineru.business.documents import DoclibGateway, ImmutableUploadStore
from mineru.business.services import DocumentWorkflow, EvidenceReader, EvidenceWriter
from mineru.business.store import BusinessStore
from mineru.doclib import DoclibInterface, ParseInfo, ParseRequest, ParseResponse


def test_open_upload_document_status_and_retry_api(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    database_dir = tmp_path / "business"
    database_dir.mkdir()
    store = BusinessStore(database_dir / "business.sqlite3")
    store.initialize()
    doclib = Mock(spec=DoclibInterface)

    def submit_to_doclib(request: ParseRequest) -> ParseResponse:
        path = Path(request.path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return ParseResponse(
            sha256=digest,
            short_id=digest[:12],
            tier="flash",
            page_range="1",
            status="pending",
            created_parse_ids=[7],
        )

    doclib.ensure_parse.side_effect = submit_to_doclib
    workflow = DocumentWorkflow(
        uploads=ImmutableUploadStore(shared, max_bytes=1024),
        store=store,
        gateway=DoclibGateway(doclib, shared_root=shared),
        doclib=doclib,
        producer_version="4.0.6",
    )
    client = TestClient(
        create_app(
            workflow=workflow,
            store=store,
            evidence_reader=EvidenceReader(store=store, doclib=doclib),
            evidence_writer=EvidenceWriter(store=store, doclib=doclib),
        )
    )

    uploaded = client.post(
        "/api/business/documents",
        files={"file": ("report.html", b"<h1>Lantern</h1>", "text/html")},
    )
    assert uploaded.status_code == 202
    payload = uploaded.json()
    assert payload["task"]["status"] == "submitted"
    assert "storage_key" not in payload["document"]
    assert "path" not in payload["document"]
    assert "parse_ids" not in payload["task"]
    assert payload["document"]["template_code"] is None
    assert payload["document"]["template_version"] is None
    document_id = payload["document"]["id"]
    task_id = payload["task"]["id"]
    assert client.get(f"/api/business/documents/{document_id}").status_code == 200

    digest = payload["document"]["sha256"]
    doclib.get_parse.return_value = ParseInfo(
        id=7,
        sha256=digest,
        short_id=digest[:12],
        tier="flash",
        page_range="1",
        status="done",
        privacy="local",
        created_at=1,
        updated_at=2,
        done_at=2,
    )
    assert client.get(f"/api/business/tasks/{task_id}").json()["status"] == "done"
    assert client.post(f"/api/business/tasks/{task_id}/retry").json()["status"] == "done"
    assert client.get("/api/business/tasks/unknown").status_code == 404
    assert client.get("/api/business/documents/unknown").status_code == 404


def test_open_api_rejects_invalid_tier_and_accepts_selected_template(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    database_dir = tmp_path / "business"
    database_dir.mkdir()
    store = BusinessStore(database_dir / "business.sqlite3")
    store.initialize()
    doclib = Mock(spec=DoclibInterface)
    workflow = DocumentWorkflow(
        uploads=ImmutableUploadStore(shared, max_bytes=1024),
        store=store,
        gateway=DoclibGateway(doclib, shared_root=shared),
        doclib=doclib,
        producer_version="4.0.6",
    )
    client = TestClient(
        create_app(
            workflow=workflow,
            store=store,
            evidence_reader=EvidenceReader(store=store, doclib=doclib),
            evidence_writer=EvidenceWriter(store=store, doclib=doclib),
        )
    )
    rejected = client.post(
        "/api/business/documents",
        data={"tier": "advanced"},
        files={"file": ("report.html", b"<h1>Wrong tier</h1>", "text/html")},
    )
    assert rejected.status_code == 422
    assert list(shared.iterdir()) == []
    doclib.ensure_parse.assert_not_called()

    def submit_to_doclib(request: ParseRequest) -> ParseResponse:
        digest = hashlib.sha256(Path(request.path).read_bytes()).hexdigest()
        return ParseResponse(
            sha256=digest, short_id=digest[:12], tier="flash", page_range="1",
            status="pending", created_parse_ids=[9],
        )

    doclib.ensure_parse.side_effect = submit_to_doclib
    accepted = client.post(
        "/api/business/documents", data={"template_code": "official_document"},
        files={"file": ("notice.html", b"<h1>Notice</h1>", "text/html")},
    )
    assert accepted.status_code == 202
    assert accepted.json()["document"]["template_code"] == "official_document"
    assert accepted.json()["document"]["template_version"] == 1


def test_upload_rejects_unknown_template_and_discards_source(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    database_dir = tmp_path / "business"
    database_dir.mkdir()
    store = BusinessStore(database_dir / "business.sqlite3")
    store.initialize()
    doclib = Mock(spec=DoclibInterface)
    workflow = DocumentWorkflow(
        uploads=ImmutableUploadStore(shared, max_bytes=1024),
        store=store,
        gateway=DoclibGateway(doclib, shared_root=shared),
        doclib=doclib,
        producer_version="4.0.6",
    )
    client = TestClient(create_app(
        workflow=workflow, store=store,
        evidence_reader=EvidenceReader(store=store, doclib=doclib),
        evidence_writer=EvidenceWriter(store=store, doclib=doclib),
    ))
    rejected = client.post(
        "/api/business/documents", data={"template_code": "missing"},
        files={"file": ("report.html", b"<h1>Not retained</h1>", "text/html")},
    )
    assert rejected.status_code == 422
    assert list(shared.iterdir()) == []
    doclib.ensure_parse.assert_not_called()
