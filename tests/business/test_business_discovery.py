"""Business search filters Doclib hits and historical reads stay bound to parse revisions."""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from mineru.business.api import create_app
from mineru.business.documents import ImmutableUploadStore
from mineru.business.services import BusinessDiscovery, DiscoveryError, EvidenceReader, EvidenceWriter
from mineru.business.store import BusinessStore
from mineru.doclib import DoclibInterface, ParseInfo, SearchResponse
from mineru.doclib.types import (
    ContentNextRequest, ContentRequestScope, DocContentResponse, ParseBlockSummary, ParseStructureResponse, SearchResult,
)
from mineru.errors import ServerNotRunningError


def _fixture(tmp_path: Path) -> tuple[BusinessStore, Mock, str, str, str, str]:
    root = tmp_path / "uploads"
    root.mkdir()
    store = BusinessStore(tmp_path / "business.sqlite3")
    store.initialize()
    uploads = ImmutableUploadStore(root, max_bytes=1024)
    first = uploads.store(io.BytesIO(b"<h1>Project Lantern</h1>"), filename="report.html")
    second = uploads.store(io.BytesIO(b"<h1>Project Lantern</h1>"), filename="copy.html")
    document_a = store.create_document(first, original_name="report.html")
    document_b = store.create_document(second, original_name="copy.html")
    parse = ParseInfo(
        id=7, sha256=first.sha256, short_id=first.sha256[:12], tier="flash", page_range="1",
        status="done", privacy="local", created_at=1, updated_at=2, done_at=2,
    )
    revision_a = store.add_completed_revision(document_a.id, parse=parse, producer_version="4.0.6")
    revision_b = store.add_completed_revision(document_b.id, parse=parse, producer_version="4.0.6")
    doclib = Mock(spec=DoclibInterface)
    return store, doclib, document_a.id, document_b.id, revision_a.id, revision_b.id


def test_search_only_maps_business_revisions_and_does_not_leak_doclib_paths(tmp_path: Path) -> None:
    store, doclib, document_a, document_b, revision_a, revision_b = _fixture(tmp_path)
    sha = store.get_document(document_a).sha256
    doclib.search.return_value = SearchResponse(
        query="Lantern", total=2, results=[
            SearchResult(
                sha256="f" * 64, short_id="f" * 12, tier="flash", snippet="Outside platform",
                files=[{"path": "/private/unmanaged.pdf", "filename": "unmanaged.pdf", "ext": "pdf", "status": "active"}],
            ),
            SearchResult(
                sha256=sha, short_id=sha[:12], tier="flash", snippet="Current index preview",
                files=[{"path": "/private/managed.html", "filename": "report.html", "ext": "html", "status": "active"}],
            ),
        ],
    )
    discovery = BusinessDiscovery(store=store, doclib=doclib)
    page = discovery.search(" Lantern ")
    assert {hit.document.id for hit in page.items} == {document_a, document_b}
    assert {hit.revision_id for hit in page.items} == {revision_a, revision_b}
    assert all(hit.snippet == "Current index preview" for hit in page.items)
    assert page.scan_complete
    doclib.search.assert_called_once_with("Lantern", limit=100, offset=0)

    api = TestClient(create_app(
        workflow=Mock(), store=store, discovery=discovery,
        evidence_reader=EvidenceReader(store=store, doclib=doclib),
        evidence_writer=EvidenceWriter(store=store, doclib=doclib),
    ))
    response = api.get("/api/business/search", params={"query": "Lantern"})
    assert response.status_code == 200
    assert {item["document"]["id"] for item in response.json()["items"]} == {document_a, document_b}
    assert all(item["state"] == "current_index_unconfirmed" for item in response.json()["items"])
    assert "/private/" not in response.text
    assert "doclib_parse_id" not in response.text
    assert api.get("/api/business/search", params={"query": "  "}).status_code == 422


def test_revision_read_uses_historical_parse_id_and_returns_safe_continuation(tmp_path: Path) -> None:
    store, doclib, document_a, _document_b, revision_a, _revision_b = _fixture(tmp_path)
    sha = store.get_document(document_a).sha256
    locator = f"doc:{sha[:12]}/tier:flash/page:1/block:1"
    next_locator = f"doc:{sha[:12]}/tier:flash/page:1/block:2"
    doclib.read_parse_content.return_value = DocContentResponse(
        sha256=sha, short_id=sha[:12], tier="flash", content="Project Lantern",
        request_scope=ContentRequestScope(locator=locator),
        next_request=ContentNextRequest(locator=next_locator), truncated=True,
        asset={"path": "/private/doclib/internal.png", "mime_type": "image/png"},
    )
    discovery = BusinessDiscovery(store=store, doclib=doclib)
    read = discovery.read(revision_a, locator, limit=12000)
    assert read.document_id == document_a
    assert read.next_locator == next_locator and read.truncated
    doclib.read_parse_content.assert_called_once_with(7, locator, limit=12000)

    api = TestClient(create_app(
        workflow=Mock(), store=store, discovery=discovery,
        evidence_reader=EvidenceReader(store=store, doclib=doclib),
        evidence_writer=EvidenceWriter(store=store, doclib=doclib),
    ))
    response = api.get(f"/api/business/revisions/{revision_a}/content", params={"locator": locator})
    assert response.status_code == 200
    assert response.json()["state"] == "historical_parse_unconfirmed"
    assert response.json()["next_locator"] == next_locator
    assert "/private/" not in response.text and "doclib_parse_id" not in response.text
    assert api.get("/api/business/revisions/missing/content", params={"locator": locator}).status_code == 404
    assert api.get(f"/api/business/revisions/{revision_a}/content", params={"locator": "bad"}).status_code == 422
    too_large = api.get(f"/api/business/revisions/{revision_a}/content", params={"locator": locator, "limit": 30001})
    assert too_large.status_code == 422


def test_progressive_read_crosses_historical_parse_batch_boundary(tmp_path: Path) -> None:
    store, doclib, document_id, _other, _revision, _other_revision = _fixture(tmp_path)
    sha = store.get_document(document_id).sha256
    short_id = sha[:7]
    parses = tuple(ParseInfo(
        id=parse_id, sha256=sha, short_id=short_id, tier="flash", page_range=str(page_no),
        status="done", privacy="local", created_at=1, updated_at=2, done_at=2,
    ) for parse_id, page_no in ((8, 1), (9, 2)))
    revision = store.add_completed_revision(document_id, parse=parses, producer_version="4.0.6")

    def read(parse_id: int, locator: str, *, limit: int) -> DocContentResponse:
        assert parse_id == (8 if "/page:1" in locator else 9)
        return DocContentResponse(
            sha256=sha, short_id=short_id, tier="flash", content=f"Page from batch {parse_id}",
            request_scope=ContentRequestScope(locator=locator),
        )

    doclib.read_parse_content.side_effect = read
    discovery = BusinessDiscovery(store=store, doclib=doclib)
    first = discovery.read(revision.id, f"doc:{short_id}/tier:flash/page:1")
    assert first.content == "Page from batch 8"
    assert first.next_locator == f"doc:{short_id}/tier:flash/page:2"
    second = discovery.read(revision.id, first.next_locator)
    assert second.content == "Page from batch 9" and second.next_locator is None
    with pytest.raises(DiscoveryError, match="invalid_content_locator"):
        discovery.read(revision.id, f"doc:{short_id}/tier:flash/page:3")


def test_revision_search_returns_bounded_page_locators_from_historical_batches(tmp_path: Path) -> None:
    store, doclib, document_id, _other, _revision, _other_revision = _fixture(tmp_path)
    sha = store.get_document(document_id).sha256
    short_id = sha[:7]
    parses = tuple(ParseInfo(
        id=parse_id, sha256=sha, short_id=short_id, tier="flash", page_range=page_range,
        status="done", privacy="local", created_at=1, updated_at=2, done_at=2,
    ) for parse_id, page_range in ((8, "1-25"), (9, "26-27")))
    revision = store.add_completed_revision(document_id, parse=parses, producer_version="4.0.6")

    def read(parse_id: int, locator: str, *, limit: int) -> DocContentResponse:
        page_no = int(locator.rsplit("page:", 1)[1])
        assert parse_id == (8 if page_no <= 25 else 9)
        return DocContentResponse(
            sha256=sha, short_id=short_id, tier="flash",
            content="Needle on this page" if page_no == 26 else "Other text",
            request_scope=ContentRequestScope(locator=locator),
        )

    doclib.read_parse_content.side_effect = read
    discovery = BusinessDiscovery(store=store, doclib=doclib)
    first = discovery.search_revision(revision.id, "needle")
    assert first.items == () and first.scanned_pages == 25 and first.next_page == 26
    second = discovery.search_revision(revision.id, "needle", start_page=first.next_page)
    assert len(second.items) == 1 and second.items[0].page_no == 26
    assert second.items[0].locator == f"doc:{short_id}/tier:flash/page:26"
    assert second.next_page is None and second.scanned_pages == 2
    with pytest.raises(DiscoveryError, match="invalid_search_request"):
        discovery.search_revision(revision.id, "needle", start_page=28)

    api = TestClient(create_app(
        workflow=Mock(), store=store, discovery=discovery,
        evidence_reader=EvidenceReader(store=store, doclib=doclib),
        evidence_writer=EvidenceWriter(store=store, doclib=doclib),
    ))
    response = api.get(f"/api/business/revisions/{revision.id}/search", params={"query": "needle", "start_page": 26})
    assert response.status_code == 200
    assert response.json()["items"][0]["state"] == "historical_parse_unconfirmed"
    assert "/private/" not in response.text and "parse_id" not in response.text
    assert api.get("/api/business/revisions/missing/search", params={"query": "needle"}).status_code == 404
    doclib.read_parse_content.side_effect = lambda parse_id, locator, *, limit: DocContentResponse(
        sha256=sha, short_id=short_id, tier="flash", content="Partial",
        request_scope=ContentRequestScope(locator=locator), truncated=True,
    )
    incomplete = api.get(f"/api/business/revisions/{revision.id}/search", params={"query": "needle"})
    assert incomplete.status_code == 409 and incomplete.json()["detail"] == "historical_content_truncated"


def test_outline_extracts_only_historical_markdown_headings_with_page_locations(tmp_path: Path) -> None:
    store, doclib, document_id, _other, _revision, _other_revision = _fixture(tmp_path)
    sha = store.get_document(document_id).sha256
    short_id = sha[:7]
    parses = tuple(ParseInfo(
        id=parse_id, sha256=sha, short_id=short_id, tier="flash", page_range=str(page_no),
        status="done", privacy="local", created_at=1, updated_at=2, done_at=2,
    ) for parse_id, page_no in ((8, 1), (9, 2)))
    revision = store.add_completed_revision(document_id, parse=parses, producer_version="4.0.6")

    def read(parse_id: int, locator: str, *, limit: int) -> DocContentResponse:
        assert parse_id == (8 if "/page:1" in locator else 9)
        content = "# Project Lantern\n```python\n# Not a heading\n```\n## Results" if parse_id == 8 else "### Conclusion"
        return DocContentResponse(
            sha256=sha, short_id=short_id, tier="flash", content=content,
            request_scope=ContentRequestScope(locator=locator),
        )

    doclib.read_parse_content.side_effect = read
    discovery = BusinessDiscovery(store=store, doclib=doclib)
    outline = discovery.outline(revision.id)
    assert [(item.level, item.title, item.page_no) for item in outline.items] == [
        (1, "Project Lantern", 1), (2, "Results", 1), (3, "Conclusion", 2),
    ]
    assert outline.items[-1].locator == f"doc:{short_id}/tier:flash/page:2"
    assert discovery.outline(revision.id, start_page=2).scanned_pages == 1
    with pytest.raises(DiscoveryError, match="invalid_outline_request"):
        discovery.outline(revision.id, start_page=3)

    api = TestClient(create_app(
        workflow=Mock(), store=store, discovery=discovery,
        evidence_reader=EvidenceReader(store=store, doclib=doclib),
        evidence_writer=EvidenceWriter(store=store, doclib=doclib),
    ))
    response = api.get(f"/api/business/revisions/{revision.id}/outline")
    assert response.status_code == 200
    assert response.json()["items"][0]["state"] == "historical_parse_unconfirmed"
    assert "/private/" not in response.text and "parse_id" not in response.text
    assert api.get("/api/business/revisions/missing/outline").status_code == 404


def test_outline_requires_continuation_and_rejects_truncated_pages(tmp_path: Path) -> None:
    store, doclib, document_id, _other, _revision, _other_revision = _fixture(tmp_path)
    sha = store.get_document(document_id).sha256
    short_id = sha[:7]
    parse = ParseInfo(
        id=8, sha256=sha, short_id=short_id, tier="flash", page_range="1-27",
        status="done", privacy="local", created_at=1, updated_at=2, done_at=2,
    )
    revision = store.add_completed_revision(document_id, parse=parse, producer_version="4.0.6")

    def read(parse_id: int, locator: str, *, limit: int) -> DocContentResponse:
        page_no = int(locator.rsplit("page:", 1)[1])
        return DocContentResponse(
            sha256=sha, short_id=short_id, tier="flash",
            content="## Later heading" if page_no == 26 else "No heading",
            request_scope=ContentRequestScope(locator=locator),
            truncated=page_no == 27,
        )

    doclib.read_parse_content.side_effect = read
    discovery = BusinessDiscovery(store=store, doclib=doclib)
    first = discovery.outline(revision.id)
    assert first.items == () and first.scanned_pages == 25 and first.next_page == 26
    with pytest.raises(DiscoveryError, match="historical_content_truncated"):
        discovery.outline(revision.id, start_page=first.next_page)
    doclib.read_parse_content.side_effect = lambda parse_id, locator, *, limit: DocContentResponse(
        sha256=sha, short_id=short_id, tier="flash",
        content="## Later heading" if locator.endswith("page:26") else "No heading",
        request_scope=ContentRequestScope(locator=locator),
    )
    last = discovery.outline(revision.id, start_page=26)
    assert [(item.title, item.page_no) for item in last.items] == [("Later heading", 26)]
    assert last.scanned_pages == 2 and last.next_page is None


def test_native_structure_is_routed_to_the_historical_page_batch_and_sanitized(tmp_path: Path) -> None:
    store, doclib, document_id, _other, _revision, _other_revision = _fixture(tmp_path)
    sha = store.get_document(document_id).sha256
    short_id = sha[:7]
    parses = tuple(ParseInfo(
        id=parse_id, sha256=sha, short_id=short_id, tier="flash", page_range=str(page_no),
        status="done", privacy="local", created_at=1, updated_at=2, done_at=2,
    ) for parse_id, page_no in ((8, 1), (9, 2)))
    revision = store.add_completed_revision(document_id, parse=parses, producer_version="4.0.6")
    block_locator = f"doc:{short_id}/tier:flash/page:2/block:3"
    doclib.read_parse_structure.return_value = ParseStructureResponse(
        sha256=sha, short_id=short_id, tier="flash", page_no=2,
        blocks=[ParseBlockSummary(type="list", block_no=3, locator=block_locator, path=[0],
                                  children=[ParseBlockSummary(
                                      type="paragraph_title", block_no=3, locator=block_locator, path=[0, 0],
                                      preview="Historical section", level=2, bbox=(1, 2, 3, 4),
                                  )])],
    )
    discovery = BusinessDiscovery(store=store, doclib=doclib)
    page = discovery.structure(revision.id, 2)
    assert page.document_id == document_id and page.blocks[0].children[0].level == 2
    doclib.read_parse_structure.assert_called_once_with(9, 2)

    api = TestClient(create_app(
        workflow=Mock(), store=store, discovery=discovery,
        evidence_reader=EvidenceReader(store=store, doclib=doclib),
        evidence_writer=EvidenceWriter(store=store, doclib=doclib),
    ))
    response = api.get(f"/api/business/revisions/{revision.id}/structure", params={"page_no": 2})
    assert response.status_code == 200 and response.json()["blocks"][0]["locator"] == block_locator
    assert response.json()["blocks"][0]["state"] == "historical_parse_unconfirmed"
    assert response.json()["blocks"][0]["children"][0]["path"] == [0, 0]
    assert "parse_id" not in response.text and "/private/" not in response.text
    assert api.get(f"/api/business/revisions/{revision.id}/structure", params={"page_no": 3}).status_code == 422

    doclib.read_parse_structure.return_value = ParseStructureResponse(
        sha256="0" * 64, short_id=short_id, tier="flash", page_no=2,
        blocks=[ParseBlockSummary(type="text", block_no=3, locator=block_locator)],
    )
    with pytest.raises(DiscoveryError, match="historical_structure_mismatch"):
        discovery.structure(revision.id, 2)

    doclib.read_parse_structure.return_value = ParseStructureResponse(
        sha256=sha, short_id=short_id, tier="flash", page_no=2,
        blocks=[ParseBlockSummary(type="list", block_no=3, locator=block_locator, path=[0],
                                  children=[ParseBlockSummary(type="text", block_no=4, locator=block_locator,
                                                              path=[0, 0], preview="forged")])],
    )
    with pytest.raises(DiscoveryError, match="historical_structure_mismatch"):
        discovery.structure(revision.id, 2)


def test_revision_read_rejects_identity_drift_and_worker_outage(tmp_path: Path) -> None:
    store, doclib, document_a, _document_b, revision_a, _revision_b = _fixture(tmp_path)
    sha = store.get_document(document_a).sha256
    locator = f"doc:{sha[:12]}/tier:flash/page:1"
    discovery = BusinessDiscovery(store=store, doclib=doclib)
    with pytest.raises(DiscoveryError, match="invalid_content_locator"):
        discovery.read(revision_a, f"doc:{sha[:12]}/tier:basic/page:1")
    doclib.read_parse_content.return_value = DocContentResponse(
        sha256="0" * 64, short_id=sha[:12], tier="flash", content="Other document",
        request_scope=ContentRequestScope(locator=locator),
    )
    with pytest.raises(DiscoveryError, match="historical_content_mismatch"):
        discovery.read(revision_a, locator)
    doclib.read_parse_content.side_effect = ServerNotRunningError()
    with pytest.raises(DiscoveryError, match="doclib_unavailable"):
        discovery.read(revision_a, locator)
    api = TestClient(create_app(
        workflow=Mock(), store=store, discovery=discovery,
        evidence_reader=EvidenceReader(store=store, doclib=doclib),
        evidence_writer=EvidenceWriter(store=store, doclib=doclib),
    ))
    unavailable = api.get(f"/api/business/revisions/{revision_a}/content", params={"locator": locator})
    assert unavailable.status_code == 503


def test_search_reports_incomplete_scan_when_result_limit_is_reached(tmp_path: Path) -> None:
    store, doclib, document_a, _document_b, _revision_a, _revision_b = _fixture(tmp_path)
    sha = store.get_document(document_a).sha256
    doclib.search.return_value = SearchResponse(
        query="Lantern", total=100, results=[SearchResult(
            sha256=sha, short_id=sha[:12], tier="flash", snippet="Preview",
        )],
    )
    result = BusinessDiscovery(store=store, doclib=doclib).search("Lantern", limit=1)
    assert len(result.items) == 1
    assert not result.scan_complete
