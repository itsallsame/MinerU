"""Browser and Skill share an open business API without leaking Doclib paths."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path
from unittest.mock import Mock

from fastapi.testclient import TestClient

from mineru.business.api import create_app
from mineru.business.documents import DoclibGateway, ImmutableUploadStore
from mineru.business.services import DocumentWorkflow, EvidenceReader, EvidenceWriter
from mineru.business.store import BusinessStore
from mineru.doclib import DoclibInterface, ParseInfo, ParseRequest, ParseResponse
from mineru.doclib.types import ParseReleaseItem, ParseReleaseResponse


def test_public_business_api_contract_has_no_auth_or_doclib_routes(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    store = BusinessStore(tmp_path / "business.sqlite3")
    store.initialize()
    doclib = Mock(spec=DoclibInterface)
    workflow = DocumentWorkflow(
        uploads=ImmutableUploadStore(shared, max_bytes=1024),
        store=store,
        gateway=DoclibGateway(doclib, shared_root=shared),
        doclib=doclib,
        producer_version="4.0.6",
    )
    app = create_app(
        workflow=workflow,
        store=store,
        evidence_reader=EvidenceReader(store=store, doclib=doclib),
        evidence_writer=EvidenceWriter(store=store, doclib=doclib),
    )
    schema = app.openapi()
    paths = schema["paths"]
    required = {
        "/api/business/documents": {"get", "post"},
        "/api/business/upload-requests/{request_key}": {"get"},
        "/api/business/tasks/{task_id}": {"get"},
        "/api/business/tasks/{task_id}/retry": {"post"},
        "/api/business/tasks/{task_id}/cancel": {"post"},
        "/api/business/revisions/{revision_id}/content": {"get"},
        "/api/business/revisions/{revision_id}/extractions": {"get", "post"},
        "/api/business/extraction-requests/{request_key}": {"get"},
        "/api/business/extractions/{run_id}/confirm": {"post"},
        "/api/business/extractions/{run_id}/results": {"get"},
        "/api/business/templates": {"get", "post"},
    }
    for path, methods in required.items():
        assert path in paths
        assert methods <= paths[path].keys()
    assert all(path.startswith("/api/business/") for path in paths)
    assert "security" not in schema
    assert "securitySchemes" not in schema.get("components", {})
    assert all("security" not in operation for operations in paths.values() for operation in operations.values())


def test_open_upload_document_status_and_retry_api(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    database_dir = tmp_path / "business"
    database_dir.mkdir()
    store = BusinessStore(database_dir / "business.sqlite3")
    store.initialize()
    doclib = Mock(spec=DoclibInterface)

    assert store.quality_stats() == {
        "documents": 0, "parse_pending": 0, "parse_failed": 0, "parse_done": 0,
        "revisions": 0, "extraction_pending": 0, "extraction_failed": 0, "extraction_done": 0,
        "open_issues": 0, "confirmed_runs": 0, "result_versions": 0,
    }

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

    assert client.get("/api/business/quality-stats").json()["documents"] == 0

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
    assert client.post(f"/api/business/tasks/{task_id}/cancel").status_code == 409
    assert client.get("/api/business/tasks/unknown").status_code == 404
    assert client.get("/api/business/documents/unknown").status_code == 404


def test_open_cancel_api_distinguishes_business_cancel_from_compute_stop(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    store = BusinessStore(tmp_path / "business.sqlite3")
    store.initialize()
    doclib = Mock(spec=DoclibInterface)

    def submit_to_doclib(request: ParseRequest) -> ParseResponse:
        digest = hashlib.sha256(Path(request.path).read_bytes()).hexdigest()
        return ParseResponse(
            sha256=digest, short_id=digest[:12], tier="flash", page_range="1",
            status="pending", created_parse_ids=[7],
        )

    doclib.ensure_parse.side_effect = submit_to_doclib
    workflow = DocumentWorkflow(
        uploads=ImmutableUploadStore(shared, max_bytes=1024), store=store,
        gateway=DoclibGateway(doclib, shared_root=shared), doclib=doclib, producer_version="4.0.6",
    )
    client = TestClient(create_app(
        workflow=workflow, store=store,
        evidence_reader=EvidenceReader(store=store, doclib=doclib),
        evidence_writer=EvidenceWriter(store=store, doclib=doclib),
    ))
    uploaded = client.post(
        "/api/business/documents", files={"file": ("report.html", b"<h1>Open</h1>", "text/html")},
    )
    task_id = uploaded.json()["task"]["id"]
    doclib.release_parse_consumer.return_value = ParseReleaseResponse(
        consumer_key=f"business:{task_id}",
        results=[ParseReleaseItem(parse_id=7, disposition="shared", status_at_release="pending")],
    )
    cancelled = client.post(f"/api/business/tasks/{task_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["cancel_effect"] == "may_continue"
    assert client.post(f"/api/business/tasks/{task_id}/cancel").json() == cancelled.json()
    assert client.post(f"/api/business/tasks/{task_id}/retry").json() == cancelled.json()
    assert client.post("/api/business/tasks/missing/cancel").status_code == 404
    assert store.list_revisions(uploaded.json()["document"]["id"]) == ()


def test_upload_idempotency_key_replays_same_intent_and_rejects_conflict(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    store = BusinessStore(tmp_path / "business.sqlite3")
    store.initialize()
    doclib = Mock(spec=DoclibInterface)

    def submit_to_doclib(request: ParseRequest) -> ParseResponse:
        digest = hashlib.sha256(Path(request.path).read_bytes()).hexdigest()
        return ParseResponse(sha256=digest, short_id=digest[:12], tier="flash", page_range="1",
                             status="pending", created_parse_ids=[7])

    doclib.ensure_parse.side_effect = submit_to_doclib
    workflow = DocumentWorkflow(
        uploads=ImmutableUploadStore(shared, max_bytes=1024), store=store,
        gateway=DoclibGateway(doclib, shared_root=shared), doclib=doclib, producer_version="4.0.6",
    )
    client = TestClient(create_app(
        workflow=workflow, store=store,
        evidence_reader=EvidenceReader(store=store, doclib=doclib),
        evidence_writer=EvidenceWriter(store=store, doclib=doclib),
    ))

    def upload(content: bytes, key: str) -> object:
        return client.post("/api/business/documents", headers={"Idempotency-Key": key},
                           files={"file": ("report.html", content, "text/html")})

    first = upload(b"<h1>Lantern</h1>", "first-upload-request-key")
    replay = upload(b"<h1>Lantern</h1>", "first-upload-request-key")
    assert first.status_code == replay.status_code == 202
    assert first.json() == replay.json()
    recovered = client.get("/api/business/upload-requests/first-upload-request-key")
    assert recovered.status_code == 200
    assert recovered.json() == first.json()
    assert client.get("/api/business/upload-requests/absent-upload-request-key").status_code == 404
    assert client.get("/api/business/upload-requests/short").status_code == 422
    assert doclib.ensure_parse.call_count == 1
    assert len(list(shared.iterdir())) == 1
    assert upload(b"<h1>Changed</h1>", "first-upload-request-key").status_code == 409
    assert upload(b"<h1>Lantern</h1>", "short").status_code == 422
    distinct = upload(b"<h1>Lantern</h1>", "second-upload-request-key")
    assert distinct.status_code == 202
    assert distinct.json()["document"]["id"] != first.json()["document"]["id"]
    assert doclib.ensure_parse.call_count == 2
    assert len(list(shared.iterdir())) == 2
    store.mark_task_failed(first.json()["task"]["id"], error_code="doclib_submission_failed")
    assert client.get("/api/business/upload-requests/first-upload-request-key").json()["task"]["status"] == "failed"


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


def test_open_document_library_capabilities_and_source(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    database_dir = tmp_path / "business"
    database_dir.mkdir()
    store = BusinessStore(database_dir / "business.sqlite3")
    store.initialize()
    uploads = ImmutableUploadStore(shared, max_bytes=1024)
    doclib = Mock(spec=DoclibInterface)
    workflow = DocumentWorkflow(
        uploads=uploads, store=store, gateway=DoclibGateway(doclib, shared_root=shared),
        doclib=doclib, producer_version="4.0.6",
    )
    client = TestClient(create_app(
        workflow=workflow, store=store,
        evidence_reader=EvidenceReader(store=store, doclib=doclib),
        evidence_writer=EvidenceWriter(store=store, doclib=doclib), uploads=uploads,
    ))
    capabilities = client.get("/api/business/capabilities")
    assert capabilities.status_code == 200
    assert capabilities.json()["max_upload_bytes"] == 1024
    assert "pdf" in capabilities.json()["tiered_extensions"]
    assert "docx" in capabilities.json()["flash_only_extensions"]
    assert {"mhtml", "mht"} <= set(capabilities.json()["flash_only_extensions"])
    assert {"mhtml", "mht"} <= set(capabilities.json()["parseable_extensions"])

    html = uploads.store(io.BytesIO(b"<script>alert(1)</script>"), filename="untrusted.html")
    pdf = uploads.store(io.BytesIO(b"%PDF-1.4\nmock"), filename="report.pdf")
    first, first_task = store.create_document_with_task(html, original_name="untrusted.html", requested_tier=None)
    second, second_task = store.create_document_with_task(pdf, original_name="report.pdf", requested_tier="basic")
    page = client.get("/api/business/documents?limit=1")
    assert page.status_code == 200
    assert page.json()["total"] == 2
    assert page.json()["items"][0]["document"]["id"] == second.id
    assert page.json()["items"][0]["task"]["id"] == second_task.id
    assert "storage_key" not in page.text
    next_page = client.get("/api/business/documents?limit=1&offset=1")
    assert next_page.json()["items"][0]["document"]["id"] == first.id
    assert client.get("/api/business/documents?template_code=unknown").json()["total"] == 0
    assert client.get("/api/business/documents?status=done").json()["total"] == 0
    store.mark_task_failed(first_task.id, error_code="parse_unavailable")
    failed = client.get("/api/business/documents?status=failed")
    assert failed.json()["total"] == 1
    assert failed.json()["items"][0]["document"]["id"] == first.id
    assert client.get("/api/business/documents?limit=0").status_code == 422
    assert client.get("/api/business/documents?offset=-1").status_code == 422
    assert client.get("/api/business/documents?status=invalid").status_code == 422

    html_source = client.get(f"/api/business/documents/{first.id}/source")
    assert html_source.status_code == 200
    assert html_source.content == b"<script>alert(1)</script>"
    assert html_source.headers["content-disposition"].startswith("attachment;")
    assert html_source.headers["x-content-type-options"] == "nosniff"
    html_head = client.head(f"/api/business/documents/{first.id}/source")
    assert html_head.status_code == 200
    assert html_head.content == b""
    assert html_head.headers["content-length"] == str(first.size)
    pdf_source = client.get(f"/api/business/documents/{second.id}/source")
    assert pdf_source.status_code == 200
    assert pdf_source.headers["content-disposition"].startswith("inline;")
    office = uploads.store(io.BytesIO(b"mock-office"), filename="report.docx")
    image = uploads.store(io.BytesIO(b"mock-image"), filename="scan.png")
    office_document, _ = store.create_document_with_task(office, original_name="report.docx", requested_tier=None)
    image_document, _ = store.create_document_with_task(image, original_name="scan.png", requested_tier="flash")
    office_source = client.get(f"/api/business/documents/{office_document.id}/source")
    assert office_source.content == b"mock-office"
    assert office_source.headers["content-disposition"].startswith("attachment;")
    assert office_source.headers["x-content-type-options"] == "nosniff"
    image_source = client.get(f"/api/business/documents/{image_document.id}/source")
    assert image_source.content == b"mock-image"
    assert image_source.headers["content-disposition"].startswith("inline;")
    assert client.get("/api/business/documents/unknown/source").status_code == 404
    assert client.head("/api/business/documents/unknown/source").status_code == 404
    assert first_task.document_id == first.id
    html.path.chmod(0o644)
    html.path.write_bytes(b"changed")
    assert client.get(f"/api/business/documents/{first.id}/source").status_code == 409
    assert client.head(f"/api/business/documents/{first.id}/source").status_code == 409
