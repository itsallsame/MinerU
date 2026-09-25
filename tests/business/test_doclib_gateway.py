"""The business boundary must not bypass Doclib or use unshared paths."""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import Mock

import pytest

from mineru.business.documents import DoclibGateway, DocumentIntegrityError, DocumentPathError
from mineru.doclib import DoclibInterface, ParseInfo, ParseResponse
from mineru.doclib.types import ListParsesResponse


def _response(path: Path, *, tier: str = "flash") -> ParseResponse:
    return ParseResponse(
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        short_id="abc123",
        tier=tier,
        page_range="1",
        status="pending",
        wait_parse_ids=[42],
        created_parse_ids=[42],
    )


def test_gateway_submits_shared_html_as_local_flash(tmp_path: Path) -> None:
    root = tmp_path / "shared"
    root.mkdir()
    source = root / "report.html"
    source.write_text("<h1>Local document</h1>")
    client = Mock(spec=DoclibInterface)
    client.ensure_parse.return_value = _response(source)

    submitted = DoclibGateway(client, shared_root=root).submit(source, consumer_key="business:task-1")

    assert submitted.sha256 == hashlib.sha256(source.read_bytes()).hexdigest()
    assert submitted.parse_ids == (42,)
    request = client.ensure_parse.call_args.args[0]
    assert request.path == str(source)
    assert request.tier == "flash"
    assert request.remote is False
    assert request.consumer_key == "business:task-1"


def test_gateway_keeps_reused_parse_ids(tmp_path: Path) -> None:
    root = tmp_path / "shared"
    root.mkdir()
    source = root / "report.html"
    source.write_text("<h1>Already parsed</h1>")
    client = Mock(spec=DoclibInterface)
    client.ensure_parse.return_value = _response(source).model_copy(
        update={"created_parse_ids": [], "wait_parse_ids": [], "reused_parse_ids": [42]}
    )
    assert DoclibGateway(client, shared_root=root).submit(source).parse_ids == (42,)


def test_gateway_resolves_completed_cache_hit_without_response_ids(tmp_path: Path) -> None:
    root = tmp_path / "shared"
    root.mkdir()
    source = root / "report.html"
    source.write_text("<h1>Already completed</h1>")
    response = _response(source).model_copy(update={"status": "done", "created_parse_ids": [], "wait_parse_ids": []})
    client = Mock(spec=DoclibInterface)
    client.ensure_parse.return_value = response
    client.list_parses.return_value = ListParsesResponse(
        parses=[
            ParseInfo(
                id=42,
                sha256=response.sha256,
                short_id=response.sha256[:12],
                tier="flash",
                page_range="1",
                status="done",
                privacy="local",
                created_at=1,
                updated_at=2,
                done_at=2,
            )
        ],
        total=1,
        limit=200,
    )
    assert DoclibGateway(client, shared_root=root).submit(source).parse_ids == (42,)
    assert client.list_parses.call_args.kwargs["status"] == "done"


def test_completed_cache_hit_chooses_disjoint_cover_not_every_historical_batch(tmp_path: Path) -> None:
    root = tmp_path / "shared"
    root.mkdir()
    source = root / "report.pdf"
    source.write_bytes(b"%PDF-placeholder")
    response = _response(source).model_copy(update={
        "status": "done", "page_range": "1-13", "created_parse_ids": [], "wait_parse_ids": [],
    })
    client = Mock(spec=DoclibInterface)
    client.ensure_parse.return_value = response

    def done(parse_id: int, page_range: str) -> ParseInfo:
        return ParseInfo(
            id=parse_id, sha256=response.sha256, short_id=response.sha256[:12], tier="flash",
            page_range=page_range, status="done", privacy="local", created_at=1,
            updated_at=parse_id, done_at=parse_id,
        )

    client.list_parses.return_value = ListParsesResponse(
        parses=[done(1, "1-13"), done(2, "1-10")], total=2, limit=200,
    )
    assert DoclibGateway(client, shared_root=root).submit(source, tier="flash").parse_ids == (1,)

    client.list_parses.return_value = ListParsesResponse(
        parses=[done(1, "1-10"), done(2, "11-13")], total=2, limit=200,
    )
    assert DoclibGateway(client, shared_root=root).submit(source, tier="flash").parse_ids == (1, 2)

    client.list_parses.return_value = ListParsesResponse(
        parses=[done(1, "1-10"), done(2, "5-13")], total=2, limit=200,
    )
    with pytest.raises(DocumentIntegrityError, match="disjoint"):
        DoclibGateway(client, shared_root=root).submit(source, tier="flash")


def test_gateway_rejects_unshared_path_and_symlink(tmp_path: Path) -> None:
    root = tmp_path / "shared"
    root.mkdir()
    outside = tmp_path / "outside.html"
    outside.write_text("outside")
    alias = root / "alias.html"
    alias.symlink_to(outside)
    client = Mock(spec=DoclibInterface)
    gateway = DoclibGateway(client, shared_root=root)

    with pytest.raises(DocumentPathError):
        gateway.submit(outside)
    with pytest.raises(DocumentPathError):
        gateway.submit(alias)
    client.ensure_parse.assert_not_called()


def test_gateway_enforces_tier_by_format(tmp_path: Path) -> None:
    root = tmp_path / "shared"
    root.mkdir()
    html = root / "report.html"
    pdf = root / "report.pdf"
    html.write_text("<h1>Report</h1>")
    pdf.write_bytes(b"%PDF-placeholder")
    client = Mock(spec=DoclibInterface)
    gateway = DoclibGateway(client, shared_root=root)

    with pytest.raises(ValueError, match="flash"):
        gateway.submit(html, tier="standard")
    with pytest.raises(ValueError, match="explicit"):
        gateway.submit(pdf)
    client.ensure_parse.assert_not_called()


def test_gateway_detects_source_identity_mismatch(tmp_path: Path) -> None:
    root = tmp_path / "shared"
    root.mkdir()
    source = root / "report.html"
    source.write_text("original")
    client = Mock(spec=DoclibInterface)
    response = _response(source)
    response.sha256 = "0" * 64
    client.ensure_parse.return_value = response

    with pytest.raises(DocumentIntegrityError):
        DoclibGateway(client, shared_root=root).submit(source)


def test_gateway_rejects_changed_source_before_doclib_submission(tmp_path: Path) -> None:
    root = tmp_path / "shared"
    root.mkdir()
    source = root / "report.html"
    source.write_text("changed after registration")
    client = Mock(spec=DoclibInterface)

    with pytest.raises(DocumentIntegrityError, match="business source"):
        DoclibGateway(client, shared_root=root).submit(source, expected_sha256="0" * 64)
    client.ensure_parse.assert_not_called()
