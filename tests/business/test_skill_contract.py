"""Project Skill uses only the same open business API as the Web."""

from __future__ import annotations

import importlib.util
import io
import hashlib
import json
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.parse import quote
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
        "http://100.64.0.1:8080", "http://192.0.2.1:8080", "http://198.18.0.1:8080",
    ):
        with pytest.raises(script.BusinessAPIError):
            script.BusinessClient(url)
    assert script.main(["capabilities"]) == 1
    assert json.loads(capsys.readouterr().err)["error"] == "MINERU_BUSINESS_API_URL or --base-url is required"
    assert "confirm" not in script.parser()._subparsers._group_actions[0].choices


def test_skill_resolves_internal_hostname_only_to_approved_private_addresses(monkeypatch: pytest.MonkeyPatch) -> None:
    script = _script()
    client = script.BusinessClient("http://mineru.internal:8080")
    monkeypatch.setattr(script.socket, "getaddrinfo", lambda *_args, **_kwargs: [
        (2, 1, 6, "", ("10.8.0.7", 8080)),
    ])
    assert client._connect().host == "10.8.0.7"

    monkeypatch.setattr(script.socket, "getaddrinfo", lambda *_args, **_kwargs: [
        (2, 1, 6, "", ("10.8.0.7", 8080)),
        (2, 1, 6, "", ("8.8.8.8", 8080)),
    ])
    with pytest.raises(script.BusinessAPIError, match="outside approved private networks"):
        client._connect()


def test_skill_write_commands_require_explicit_acknowledgement() -> None:
    script = _script()
    client = Mock()
    source = Path("/tmp/not-a-real-document.pdf")
    for command in (
        ["upload", str(source), "--tier", "flash"],
        ["extract", "revision-1"],
    ):
        with pytest.raises(script.BusinessAPIError, match="requires --confirm-write"):
            script.run(script.parser().parse_args(command), client)
    client.upload.assert_not_called()
    client.request.assert_not_called()

    client.upload.return_value = {"task": {"status": "submitted"}}
    uploaded = script.run(
        script.parser().parse_args(["upload", str(source), "--tier", "flash", "--confirm-write"]), client,
    )
    assert uploaded["task"]["status"] == "submitted"
    assert len(uploaded["request_key"]) == 32
    client.upload.assert_called_once_with(source, tier="flash", template=None, request_key=uploaded["request_key"])

    client.upload.reset_mock()
    explicit = script.run(script.parser().parse_args([
        "upload", str(source), "--tier", "flash", "--request-key", "replay-upload-request-key",
        "--confirm-write",
    ]), client)
    assert explicit["request_key"] == "replay-upload-request-key"
    client.upload.assert_called_once_with(source, tier="flash", template=None,
                                          request_key="replay-upload-request-key")

    client.upload.side_effect = script.BusinessAPIError("Upload outcome unknown")
    with pytest.raises(script.BusinessAPIError) as uncertain:
        script.run(script.parser().parse_args([
            "upload", str(source), "--request-key", "replay-upload-request-key", "--confirm-write",
        ]), client)
    assert uncertain.value.request_key == "replay-upload-request-key"
    client.upload.side_effect = None

    client.request.return_value = {"id": "run-1", "revision_id": "revision-1", "status": "queued"}
    extracted = script.run(script.parser().parse_args(["extract", "revision-1", "--confirm-write"]), client)
    assert extracted["id"] == "run-1"
    client.request.assert_called_once_with("POST", "/revisions/revision-1/extractions")


def test_skill_upload_request_lookup_is_read_only_and_404_is_not_rejection() -> None:
    script = _script()
    client = Mock()
    key = "prior-upload-request-key"
    args = script.parser().parse_args(["upload-request", key])
    client.request.return_value = {"document": {"id": "doc-1"}, "task": {"id": "task-1", "status": "submitted"}}
    found = script.run(args, client)
    assert found == {
        "request_key": key, "state": "accepted",
        "document": {"id": "doc-1"}, "task": {"id": "task-1", "status": "submitted"},
    }
    client.request.assert_called_once_with("GET", f"/upload-requests/{key}")
    client.upload.assert_not_called()

    client.request.side_effect = script.BusinessAPIError("Upload request not found", status=404)
    assert script.run(args, client) == {"request_key": key, "state": "not_recorded_at_lookup"}
    client.request.side_effect = script.BusinessAPIError("Unavailable", status=503)
    with pytest.raises(script.BusinessAPIError) as unavailable:
        script.run(args, client)
    assert unavailable.value.status == 503
    client.request.reset_mock(side_effect=True)
    with pytest.raises(script.BusinessAPIError, match="Invalid upload idempotency key"):
        script.run(script.parser().parse_args(["upload-request", "short"]), client)
    client.request.assert_not_called()
    client.request.return_value = {"task": {"status": "submitted"}}
    with pytest.raises(script.BusinessAPIError, match="incomplete upload request"):
        script.run(args, client)


def test_skill_progressive_read_uses_one_revision_and_server_continuations() -> None:
    script = _script()
    locator = "doc:returned-short/tier:flash/page:26/block:2"
    next_locator = "doc:returned-short/tier:flash/page:27"
    responses = {
        ("GET", "/api/business/documents/doc-1/revisions"): _Response([{"id": "rev-1", "short_id": "returned-short"}]),
        ("GET", "/api/business/revisions/rev-1/outline"): _Response({"items": [], "next_page": 26}),
        ("GET", "/api/business/revisions/rev-1/outline?start_page=26"): _Response({"items": [], "next_page": None}),
        ("GET", "/api/business/revisions/rev-1/search-blocks?query=risk"): _Response({
            "items": [{"locator": locator, "state": "historical_parse_unconfirmed"}], "next_page": 26,
        }),
        ("GET", "/api/business/revisions/rev-1/search-blocks?query=risk&start_page=26"): _Response({
            "items": [], "next_page": None,
        }),
        ("GET", f"/api/business/revisions/rev-1/content?locator={quote(locator, safe='')}&limit=12000"): _Response({
            "content": "Risk passage", "next_locator": next_locator, "state": "historical_parse_unconfirmed",
        }),
    }
    connection = _Connection(responses)
    client = script.BusinessClient("http://127.0.0.1:8080")
    client._connect = lambda: connection

    revisions = script.run(script.parser().parse_args(["revisions", "doc-1"]), client)
    assert revisions[0]["id"] == "rev-1"
    first = script.run(script.parser().parse_args(["outline", "rev-1"]), client)
    assert first["next_page"] == 26
    second = script.run(script.parser().parse_args(["outline", "rev-1", "--start-page", "26"]), client)
    assert second["next_page"] is None
    matches = script.run(script.parser().parse_args(["search-blocks", "rev-1", "risk"]), client)
    assert matches["items"][0]["locator"] == locator
    continued = script.run(
        script.parser().parse_args(["search-blocks", "rev-1", "risk", "--start-page", "26"]), client,
    )
    assert continued["next_page"] is None
    read = script.run(script.parser().parse_args(["read", "rev-1", locator]), client)
    assert read["next_locator"] == next_locator
    assert read["state"] == "historical_parse_unconfirmed"
    assert all(method == "GET" and path.startswith("/api/business/") for method, path in connection.calls)


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
    assert len(dict(connection.headers)["Idempotency-Key"]) == 32
    content_length = int(dict(connection.headers)["Content-Length"])
    assert content_length == len(connection.body.getvalue())

    with pytest.raises(script.BusinessAPIError, match="Invalid upload idempotency key"):
        client.upload(file, tier="standard", template=None, request_key="")
    assert len(connection.calls) == 2

    html = tmp_path / "notice.html"
    html.write_text("<h1>title</h1>")
    with pytest.raises(script.BusinessAPIError, match="Tier standard is not allowed"):
        client.upload(html, tier="standard", template=None)
    assert len(connection.calls) == 3  # Only the capabilities preflight was sent.


def test_skill_upload_503_keeps_request_key_and_reports_unknown_outcome(tmp_path: Path) -> None:
    script = _script()
    file = tmp_path / "notice.html"
    file.write_bytes(b"<h1>Notice</h1>")
    connection = _Connection({
        ("GET", "/api/business/capabilities"): _Response({
            "parseable_extensions": ["html"], "tiered_extensions": [], "tiers": ["flash"],
            "max_upload_bytes": 1024,
        }),
        ("POST", "/api/business/documents"): _Response({"detail": "gateway unavailable"}, status=503),
    })
    client = script.BusinessClient("http://127.0.0.1:8080")
    client._connect = lambda: connection
    with pytest.raises(script.BusinessAPIError, match="outcome unknown") as unknown:
        client.upload(file, tier=None, template=None, request_key="same-upload-request-key")
    assert unknown.value.status == 503
    assert unknown.value.request_key == "same-upload-request-key"
    assert connection.calls == [("GET", "/api/business/capabilities"), ("POST", "/api/business/documents")]


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


def test_skill_cancel_requires_explicit_write_ack_and_uses_only_business_api() -> None:
    script = _script()
    connection = _Connection({
        ("POST", "/api/business/tasks/task-1/cancel"): _Response({
            "id": "task-1", "status": "cancelled", "cancel_effect": "may_continue",
        }),
    })
    client = script.BusinessClient("http://127.0.0.1:8080")
    client._connect = lambda: connection
    with pytest.raises(script.BusinessAPIError, match="explicit user approval"):
        script.run(script.parser().parse_args(["cancel", "task-1"]), client)
    assert connection.calls == []
    result = script.run(script.parser().parse_args(["cancel", "task-1", "--confirm-write"]), client)
    assert result == {"id": "task-1", "status": "cancelled", "cancel_effect": "may_continue"}
    assert connection.calls == [("POST", "/api/business/tasks/task-1/cancel")]


def test_skill_cancel_unknown_post_only_reads_same_task() -> None:
    script = _script()
    connection = _Connection({
        ("POST", "/api/business/tasks/task-1/cancel"): _Response({"detail": "unavailable"}, status=503),
        ("GET", "/api/business/tasks/task-1"): _Response({"id": "task-1", "status": "cancel_requested"}),
    })
    client = script.BusinessClient("http://127.0.0.1:8080")
    client._connect = lambda: connection
    args = script.parser().parse_args(["cancel", "task-1", "--confirm-write"])

    result = script.run(args, client)
    assert result == {"state": "cancel_outcome_unconfirmed", "task": {"id": "task-1", "status": "cancel_requested"}}
    assert connection.calls == [
        ("POST", "/api/business/tasks/task-1/cancel"), ("GET", "/api/business/tasks/task-1"),
    ]
    connection.responses[("POST", "/api/business/tasks/task-1/cancel")] = _Response({"unexpected": True})
    assert script.run(args, client)["state"] == "cancel_outcome_unconfirmed"
    connection.responses[("POST", "/api/business/tasks/task-1/cancel")] = _Response(
        {"detail": "Task cannot be cancelled"}, status=409,
    )
    get_count = connection.calls.count(("GET", "/api/business/tasks/task-1"))
    with pytest.raises(script.BusinessAPIError, match="cannot be cancelled"):
        script.run(args, client)
    assert connection.calls.count(("GET", "/api/business/tasks/task-1")) == get_count
    connection.responses[("POST", "/api/business/tasks/task-1/cancel")] = _Response(
        {"detail": "unavailable"}, status=503,
    )
    connection.responses[("GET", "/api/business/tasks/task-1")] = _Response(
        {"detail": "unavailable"}, status=503,
    )
    with pytest.raises(script.BusinessAPIError, match="outcome unknown"):
        script.run(args, client)
    assert connection.calls[-2:] == [
        ("POST", "/api/business/tasks/task-1/cancel"), ("GET", "/api/business/tasks/task-1"),
    ]


def test_skill_extract_unknown_post_only_lists_revision_runs() -> None:
    script = _script()
    connection = _Connection({
        ("POST", "/api/business/revisions/rev-1/extractions"): _Response({"detail": "unavailable"}, status=503),
        ("GET", "/api/business/revisions/rev-1/extractions"): _Response([
            {"id": "run-1", "revision_id": "rev-1", "status": "queued"},
        ]),
    })
    client = script.BusinessClient("http://127.0.0.1:8080")
    client._connect = lambda: connection
    args = script.parser().parse_args(["extract", "rev-1", "--confirm-write"])

    result = script.run(args, client)
    assert result == {"state": "extraction_outcome_unconfirmed", "runs": [
        {"id": "run-1", "revision_id": "rev-1", "status": "queued"},
    ]}
    assert connection.calls == [
        ("POST", "/api/business/revisions/rev-1/extractions"),
        ("GET", "/api/business/revisions/rev-1/extractions"),
    ]
    connection.responses[("POST", "/api/business/revisions/rev-1/extractions")] = _Response({"unexpected": True})
    assert script.run(args, client)["state"] == "extraction_outcome_unconfirmed"
    connection.responses[("POST", "/api/business/revisions/rev-1/extractions")] = _Response(
        {"detail": "Revision not ready"}, status=409,
    )
    get_count = connection.calls.count(("GET", "/api/business/revisions/rev-1/extractions"))
    with pytest.raises(script.BusinessAPIError, match="Revision not ready"):
        script.run(args, client)
    assert connection.calls.count(("GET", "/api/business/revisions/rev-1/extractions")) == get_count
    connection.responses[("POST", "/api/business/revisions/rev-1/extractions")] = _Response(
        {"detail": "unavailable"}, status=503,
    )
    connection.responses[("GET", "/api/business/revisions/rev-1/extractions")] = _Response(
        {"detail": "unavailable"}, status=503,
    )
    with pytest.raises(script.BusinessAPIError, match="outcome unknown"):
        script.run(args, client)
    assert connection.calls[-2:] == [
        ("POST", "/api/business/revisions/rev-1/extractions"),
        ("GET", "/api/business/revisions/rev-1/extractions"),
    ]


def test_skill_retry_requires_explicit_write_ack_and_uses_only_business_api() -> None:
    script = _script()
    connection = _Connection({
        ("POST", "/api/business/tasks/task-1/retry"): _Response({"id": "task-1", "status": "submitted"}),
    })
    client = script.BusinessClient("http://127.0.0.1:8080")
    client._connect = lambda: connection
    with pytest.raises(script.BusinessAPIError, match="explicit user approval"):
        script.run(script.parser().parse_args(["retry", "task-1"]), client)
    assert connection.calls == []
    result = script.run(script.parser().parse_args(["retry", "task-1", "--confirm-write"]), client)
    assert result == {"id": "task-1", "status": "submitted"}
    assert connection.calls == [("POST", "/api/business/tasks/task-1/retry")]


def test_skill_retry_unknown_post_result_only_reads_task_without_resubmitting() -> None:
    script = _script()
    connection = _Connection({
        ("POST", "/api/business/tasks/task-1/retry"): _Response({"detail": "unavailable"}, status=503),
        ("GET", "/api/business/tasks/task-1"): _Response({"id": "task-1", "status": "submitted"}),
    })
    client = script.BusinessClient("http://127.0.0.1:8080")
    client._connect = lambda: connection
    result = script.run(script.parser().parse_args(["retry", "task-1", "--confirm-write"]), client)
    assert result == {"state": "retry_outcome_unconfirmed", "task": {"id": "task-1", "status": "submitted"}}
    assert connection.calls == [
        ("POST", "/api/business/tasks/task-1/retry"), ("GET", "/api/business/tasks/task-1"),
    ]
    connection.responses[("POST", "/api/business/tasks/task-1/retry")] = _Response({"unexpected": True})
    malformed = script.run(script.parser().parse_args(["retry", "task-1", "--confirm-write"]), client)
    assert malformed["state"] == "retry_outcome_unconfirmed"
    assert connection.calls[-2:] == [
        ("POST", "/api/business/tasks/task-1/retry"), ("GET", "/api/business/tasks/task-1"),
    ]
    connection.responses[("POST", "/api/business/tasks/task-1/retry")] = _Response(
        {"detail": "unavailable"}, status=503,
    )
    connection.responses[("GET", "/api/business/tasks/task-1")] = _Response({"detail": "offline"}, status=503)
    with pytest.raises(script.BusinessAPIError, match="outcome unknown"):
        script.run(script.parser().parse_args(["retry", "task-1", "--confirm-write"]), client)
    assert connection.calls.count(("POST", "/api/business/tasks/task-1/retry")) == 3
    assert connection.calls.count(("GET", "/api/business/tasks/task-1")) == 3

    connection.responses[("POST", "/api/business/tasks/task-1/retry")] = _Response(
        {"detail": "Task cannot be retried"}, status=409,
    )
    prior_get_count = connection.calls.count(("GET", "/api/business/tasks/task-1"))
    with pytest.raises(script.BusinessAPIError, match="Task cannot be retried") as conflict:
        script.run(script.parser().parse_args(["retry", "task-1", "--confirm-write"]), client)
    assert conflict.value.status == 409
    assert connection.calls.count(("GET", "/api/business/tasks/task-1")) == prior_get_count


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
    submitted = client.upload(file, tier=None, template="official_document", request_key="skill-upload-request-key")
    assert submitted["task"]["status"] == "submitted"
    assert submitted["document"]["template_code"] == "official_document"
    lookup = script.run(script.parser().parse_args(["upload-request", "skill-upload-request-key"]), client)
    assert lookup["state"] == "accepted"
    assert lookup["document"] == submitted["document"]
    assert lookup["task"] == submitted["task"]
    replay = client.upload(file, tier=None, template="official_document", request_key="skill-upload-request-key")
    assert replay == submitted
    assert doclib.ensure_parse.call_count == 1
    digest = submitted["document"]["sha256"]
    doclib.get_parse.return_value = ParseInfo(
        id=7, sha256=digest, short_id=digest[:12], tier="flash", page_range="1",
        status="done", privacy="local", created_at=1, updated_at=2, done_at=2,
    )
    task = client.request("GET", f"/tasks/{submitted['task']['id']}")
    assert task["status"] == "done"
    done_lookup = script.run(script.parser().parse_args(["upload-request", "skill-upload-request-key"]), client)
    assert done_lookup["task"]["status"] == "done"
    overview = client.overview(submitted["document"]["id"])
    assert overview["revision"]["tier"] == "flash"
    assert overview["draft"]["state"] == "machine_unconfirmed"
    assert overview["draft"]["run"]["status"] == "queued"
    assert overview["draft"]["run"]["revision_id"] == overview["revision"]["id"]
    assert overview["draft"]["candidates"] == []
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
