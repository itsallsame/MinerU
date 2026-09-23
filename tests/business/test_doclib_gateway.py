"""The business boundary must not bypass Doclib or use unshared paths."""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import Mock

import pytest

from mineru.business.documents import DoclibGateway, DocumentIntegrityError, DocumentPathError
from mineru.doclib import DoclibInterface, ParseResponse


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

    submitted = DoclibGateway(client, shared_root=root).submit(source)

    assert submitted.sha256 == hashlib.sha256(source.read_bytes()).hexdigest()
    assert submitted.parse_ids == (42,)
    request = client.ensure_parse.call_args.args[0]
    assert request.path == str(source)
    assert request.tier == "flash"
    assert request.remote is False


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
