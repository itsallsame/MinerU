"""Project Skill uses only the same open business API as the Web."""

from __future__ import annotations

import importlib.util
import io
import hashlib
import json
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from mineru.business.api import create_app
from mineru.business.documents import DoclibGateway, ImmutableUploadStore
from mineru.business.services import BusinessDiscovery, DocumentWorkflow, EvidenceReader, EvidenceWriter
from mineru.business.store import BusinessStore
from mineru.doclib import DoclibInterface, ParseInfo, ParseRequest, ParseResponse, SearchResponse
from mineru.doclib.types import (
    ContentRequestScope, DocContentResponse, ParseBlockMatch, ParseBlockSearchResponse,
    ParseBlockSummary, ParseStructureResponse, SearchResult,
)


def _script() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "skills" / "business-documents" / "scripts" / "business_documents.py"
    spec = importlib.util.spec_from_file_location("business_documents_skill", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Response:
    status = 200

    def __init__(self, value: Any, *, status: int = 200) -> None:
        self.status = status
        self.value = value

    def read(self) -> bytes:
        return json.dumps(self.value).encode()


class _Connection:
    def __init__(self, responses: dict[tuple[str, str], _Response]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str]] = []
        self.headers: list[tuple[str, str]] = []
        self.body = io.BytesIO()
        self.current: tuple[str, str] | None = None

    def request(self, method: str, path: str, *, headers: dict[str, str]) -> None:
        self.current = method, path
        self.calls.append(self.current)
        self.headers.extend(headers.items())

    def putrequest(self, method: str, path: str) -> None:
        self.current = method, path
        self.calls.append(self.current)

    def putheader(self, name: str, value: str) -> None:
        self.headers.append((name, value))

    def endheaders(self) -> None:
        pass

    def send(self, chunk: bytes) -> None:
        self.body.write(chunk)

    def getresponse(self) -> _Response:
        assert self.current is not None
        return self.responses[self.current]

    def close(self) -> None:
        pass


class _ASGIConnection:
    def __init__(self, api: TestClient) -> None:
        self.api = api
        self.method = ""
        self.path = ""
        self.headers: dict[str, str] = {}
        self.body = io.BytesIO()

    def request(self, method: str, path: str, *, headers: dict[str, str]) -> None:
        self.method, self.path, self.headers = method, path, headers

    def putrequest(self, method: str, path: str) -> None:
        self.method, self.path = method, path

    def putheader(self, name: str, value: str) -> None:
        self.headers[name] = value

    def endheaders(self) -> None:
        pass

    def send(self, chunk: bytes) -> None:
        self.body.write(chunk)

    def getresponse(self) -> _Response:
        result = self.api.request(self.method, self.path, content=self.body.getvalue(), headers=self.headers)
        return _Response(result.json(), status=result.status_code)

    def close(self) -> None:
        pass


def test_skill_rejects_unconfigured_or_non_plain_business_endpoint(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    script = _script()
    monkeypatch.delenv("MINERU_BUSINESS_API_URL", raising=False)
    for url in (
        "https://example.com:443", "http://host", "http://u:p@host:8080", "http://host:8080/v1",
        "http://example.com:8080", "http://8.8.8.8:8080", "http://host:bad",
    ):
        with pytest.raises(script.BusinessAPIError):
            script.BusinessClient(url)
    assert script.main(["capabilities"]) == 1
    assert json.loads(capsys.readouterr().err)["error"] == "MINERU_BUSINESS_API_URL or --base-url is required"
    assert "confirm" not in script.parser()._subparsers._group_actions[0].choices


def test_skill_overview_separates_unconfirmed_candidates_from_confirmed_result() -> None:
    script = _script()
    responses = {
        ("GET", "/api/business/documents/doc-1"): _Response({"id": "doc-1"}),
        ("GET", "/api/business/documents/doc-1/revisions"): _Response([{"id": "rev-1"}]),
        ("GET", "/api/business/revisions/rev-1/extractions"): _Response([{"id": "run-1"}]),
        ("GET", "/api/business/extractions/run-1"): _Response({
            "run": {"id": "run-1", "status": "done"}, "candidates": [{"value": "draft"}],
            "issues": [{"code": "conflicting_candidates", "status": "open", "severity": "blocking"}],
        }),
        ("GET", "/api/business/extractions/run-1/results"): _Response([{"version": 1, "fields": [{"value": "reviewed"}]}]),
    }
    connection = _Connection(responses)
    client = script.BusinessClient("http://127.0.0.1:8080")
    client._connect = lambda: connection
    result = client.overview("doc-1")
    assert result["draft"]["state"] == "machine_unconfirmed"
    assert result["draft"]["candidates"][0]["value"] == "draft"
    assert result["draft"]["issues"][0]["status"] == "open"
    assert result["confirmed_for_latest_run"]["fields"][0]["value"] == "reviewed"
    assert all(path.startswith("/api/business/") for _method, path in connection.calls)
    assert not any(name.lower() == "authorization" for name, _value in connection.headers)

    responses[("GET", "/api/business/extractions/run-1/results")] = _Response([])
    assert client.overview("doc-1")["confirmed_for_latest_run"] is None


def test_skill_upload_preflights_limits_and_streams_only_to_business_api(tmp_path: Path) -> None:
    script = _script()
    file = tmp_path / "notice.pdf"
    file.write_bytes(b"%PDF-1.4\nhello")
    capabilities = {
        "parseable_extensions": ["pdf", "html"], "tiered_extensions": ["pdf"],
        "tiers": ["flash", "basic", "standard", "advanced"], "max_upload_bytes": 1024,
    }
    connection = _Connection({
        ("GET", "/api/business/capabilities"): _Response(capabilities),
        ("POST", "/api/business/documents"): _Response({"task": {"status": "submitted"}}, status=202),
    })
    client = script.BusinessClient("http://127.0.0.1:8080")
    client._connect = lambda: connection
    assert client.upload(file, tier="standard", template="official_document")["task"]["status"] == "submitted"
    assert connection.calls == [
        ("GET", "/api/business/capabilities"), ("POST", "/api/business/documents"),
    ]
    assert b"%PDF-1.4\nhello" in connection.body.getvalue()
    assert b'name="tier"' in connection.body.getvalue()
    assert b"standard" in connection.body.getvalue()
    assert b"official_document" in connection.body.getvalue()
    content_length = int(dict(connection.headers)["Content-Length"])
    assert content_length == len(connection.body.getvalue())

    html = tmp_path / "notice.html"
    html.write_text("<h1>title</h1>")
    with pytest.raises(script.BusinessAPIError, match="Tier standard is not allowed"):
        client.upload(html, tier="standard", template=None)
    assert len(connection.calls) == 3  # Only the capabilities preflight was sent.


def test_skill_reports_business_error_without_success_guess() -> None:
    script = _script()
    connection = _Connection({("GET", "/api/business/tasks/missing"): _Response({"detail": "Task not found"}, status=404)})
    client = script.BusinessClient("http://127.0.0.1:8080")
    client._connect = lambda: connection
    with pytest.raises(script.BusinessAPIError, match="Task not found") as exc:
        client.request("GET", "/tasks/missing")
    assert exc.value.status == 404

    class OfflineConnection(_Connection):
        def request(self, method: str, path: str, *, headers: dict[str, str]) -> None:
            raise ConnectionRefusedError("offline")

    client._connect = lambda: OfflineConnection({})
    with pytest.raises(script.BusinessAPIError, match="Business API unavailable") as offline:
        client.request("GET", "/tasks/missing")
    assert offline.value.status is None


def test_skill_evidence_link_uses_the_same_validated_business_web_origin() -> None:
    script = _script()
    evidence = {
        "id": "evidence-1", "document_id": "doc-1", "revision_id": "rev-1",
        "locator": "doc:abcdef123456/tier:flash/page:1", "snippet": "Frozen text",
        "navigation_status": "changed",
    }
    connection = _Connection({
        ("GET", "/api/business/evidence/evidence-1"): _Response(evidence),
    })
    client = script.BusinessClient("http://127.0.0.1:8080")
    client._connect = lambda: connection
    result = script.run(script.parser().parse_args(["evidence", "evidence-1"]), client)
    assert result == {**evidence, "web_url": "http://127.0.0.1:8080/#evidence=evidence-1"}
    assert connection.calls == [("GET", "/api/business/evidence/evidence-1")]


def test_skill_upload_and_read_use_the_real_open_business_api(tmp_path: Path) -> None:
    script = _script()
    shared = tmp_path / "shared"
    shared.mkdir()
    store = BusinessStore(tmp_path / "business.sqlite3")
    store.initialize()
    uploads = ImmutableUploadStore(shared, max_bytes=1024)
    doclib = Mock(spec=DoclibInterface)

    def submit_to_doclib(request: ParseRequest) -> ParseResponse:
        digest = hashlib.sha256(Path(request.path).read_bytes()).hexdigest()
        return ParseResponse(
            sha256=digest, short_id=digest[:12], tier="flash", page_range="1", status="pending",
            created_parse_ids=[7],
        )

    doclib.ensure_parse.side_effect = submit_to_doclib
    workflow = DocumentWorkflow(
        uploads=uploads, store=store, gateway=DoclibGateway(doclib, shared_root=shared),
        doclib=doclib, producer_version="4.0.6",
    )
    app = create_app(
        workflow=workflow, store=store, uploads=uploads,
        evidence_reader=EvidenceReader(store=store, doclib=doclib),
        evidence_writer=EvidenceWriter(store=store, doclib=doclib),
        discovery=BusinessDiscovery(store=store, doclib=doclib),
    )
    client = script.BusinessClient("http://127.0.0.1:8080")
    client._connect = lambda: _ASGIConnection(TestClient(app))
    file = tmp_path / "notice.html"
    file.write_bytes(b"<h1>Notice</h1>")
    submitted = client.upload(file, tier=None, template="official_document")
    assert submitted["task"]["status"] == "submitted"
    assert submitted["document"]["template_code"] == "official_document"
    digest = submitted["document"]["sha256"]
    doclib.get_parse.return_value = ParseInfo(
        id=7, sha256=digest, short_id=digest[:12], tier="flash", page_range="1",
        status="done", privacy="local", created_at=1, updated_at=2, done_at=2,
    )
    task = client.request("GET", f"/tasks/{submitted['task']['id']}")
    assert task["status"] == "done"
    overview = client.overview(submitted["document"]["id"])
    assert overview["revision"]["tier"] == "flash"
    assert overview["draft"] is None
    assert overview["confirmed_for_latest_run"] is None
    doclib.search.return_value = SearchResponse(
        query="Notice", total=1, results=[SearchResult(
            sha256=digest, short_id=digest[:12], tier="flash", snippet="Notice index preview",
        )],
    )
    search_args = script.parser().parse_args(["search", "Notice"])
    search_result = script.run(search_args, client)
    assert search_result["items"][0]["document"]["id"] == submitted["document"]["id"]
    assert search_result["items"][0]["state"] == "current_index_unconfirmed"
    locator = f"doc:{digest[:12]}/tier:flash/page:1"
    doclib.read_parse_content.return_value = DocContentResponse(
        sha256=digest, short_id=digest[:12], tier="flash", content="Notice historical text",
        request_scope=ContentRequestScope(locator=locator),
    )
    read_args = script.parser().parse_args(["read", overview["revision"]["id"], locator])
    read_result = script.run(read_args, client)
    assert read_result["content"] == "Notice historical text"
    assert read_result["state"] == "historical_parse_unconfirmed"
    doclib.read_parse_content.assert_called_once_with(7, locator, limit=12000)
    page_args = script.parser().parse_args(["search-pages", overview["revision"]["id"], "Notice"])
    page_result = script.run(page_args, client)
    assert page_result["items"][0]["locator"] == locator
    assert page_result["items"][0]["state"] == "historical_parse_unconfirmed"
    assert page_result["next_page"] is None
    doclib.search_parse_blocks.return_value = ParseBlockSearchResponse(
        sha256=digest, short_id=digest[:12], tier="flash", page_no=1,
        matches=[ParseBlockMatch(block_no=1, locator=f"{locator}/block:1",
                                 snippet="Notice historical text")],
    )
    blocks_args = script.parser().parse_args(["search-blocks", overview["revision"]["id"], "Notice"])
    blocks_result = script.run(blocks_args, client)
    assert blocks_result["items"][0]["block_no"] == 1
    assert blocks_result["items"][0]["state"] == "historical_parse_unconfirmed"
    doclib.read_parse_content.return_value = DocContentResponse(
        sha256=digest, short_id=digest[:12], tier="flash", content="# Notice historical text",
        request_scope=ContentRequestScope(locator=locator),
    )
    outline_args = script.parser().parse_args(["outline", overview["revision"]["id"]])
    outline_result = script.run(outline_args, client)
    assert outline_result["items"][0]["title"] == "Notice historical text"
    assert outline_result["items"][0]["state"] == "historical_parse_unconfirmed"
    doclib.read_parse_structure.return_value = ParseStructureResponse(
        sha256=digest, short_id=digest[:12], tier="flash", page_no=1,
        blocks=[ParseBlockSummary(type="doc_title", block_no=1, locator=f"{locator}/block:1", path=[0],
                                  preview="Notice historical text", level=1)],
    )
    structure_args = script.parser().parse_args(["structure", overview["revision"]["id"], "1"])
    structure_result = script.run(structure_args, client)
    assert structure_result["blocks"][0]["type"] == "doc_title"
    assert structure_result["blocks"][0]["state"] == "historical_parse_unconfirmed"
    with pytest.raises(script.BusinessAPIError) as missing:
        client.overview("missing")
    assert missing.value.status == 404
