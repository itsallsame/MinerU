"""Business-scoped Doclib discovery and revision-bound progressive reading."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ...doclib import DoclibInterface
from ...doclib.locators import parse_content_cursor
from ...doclib.locators import page_ref
from ...parser.page_range import parse_page_range_set
from ...errors import MineruError, ServerNotRunningError
from ...types import Tier
from ..domain import BusinessDocument
from ..store import BusinessStore


class DiscoveryError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class BusinessSearchHit:
    document: BusinessDocument
    revision_id: str
    tier: Tier
    snippet: str


@dataclass(frozen=True)
class BusinessSearchPage:
    items: tuple[BusinessSearchHit, ...]
    scan_complete: bool
    scanned_doclib_hits: int


@dataclass(frozen=True)
class HistoricalRead:
    document_id: str
    revision_id: str
    locator: str
    tier: Tier
    content: str
    truncated: bool
    next_locator: str | None


@dataclass(frozen=True)
class RevisionSearchHit:
    locator: str
    page_no: int
    snippet: str


@dataclass(frozen=True)
class RevisionSearchPage:
    revision_id: str
    items: tuple[RevisionSearchHit, ...]
    next_page: int | None
    scanned_pages: int


@dataclass(frozen=True)
class RevisionHeading:
    level: int
    title: str
    page_no: int
    locator: str


@dataclass(frozen=True)
class RevisionOutlinePage:
    revision_id: str
    items: tuple[RevisionHeading, ...]
    next_page: int | None
    scanned_pages: int


@dataclass(frozen=True)
class StructureBlock:
    type: str
    block_no: int | None
    locator: str
    preview: str
    level: int | None
    bbox: tuple[float, float, float, float] | None


@dataclass(frozen=True)
class BusinessStructurePage:
    document_id: str
    revision_id: str
    page_no: int
    blocks: tuple[StructureBlock, ...]


class BusinessDiscovery:
    """Never expose Doclib-only documents, paths, parse IDs, or mutable hits as reviewed evidence."""

    _MAX_SCAN = 500
    _REVISION_SCAN_PAGES = 25
    _HEADING_RE = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.+?)\s*$")

    def __init__(self, *, store: BusinessStore, doclib: DoclibInterface) -> None:
        self._store = store
        self._doclib = doclib

    def search(self, query: str, *, limit: int = 20) -> BusinessSearchPage:
        query = query.strip()
        if not query or len(query) > 200 or not 1 <= limit <= 50:
            raise DiscoveryError("invalid_search_request")
        found: list[BusinessSearchHit] = []
        seen: set[tuple[str, str]] = set()
        offset = 0
        total = 0
        stopped_early = False
        while offset < self._MAX_SCAN and len(found) < limit:
            page_size = min(100, self._MAX_SCAN - offset)
            try:
                response = self._doclib.search(query, limit=page_size, offset=offset)
            except MineruError as exc:
                raise DiscoveryError("doclib_search_unavailable") from exc
            total = response.total
            if not response.results:
                break
            for item_index, item in enumerate(response.results):
                if item.tier is None:
                    continue
                links = self._store.find_documents_with_revision(item.sha256, tier=item.tier)
                for link_index, (document, revision_id) in enumerate(links):
                    key = document.id, revision_id
                    if key not in seen:
                        seen.add(key)
                        found.append(BusinessSearchHit(document, revision_id, item.tier, item.snippet))
                        if len(found) >= limit:
                            stopped_early = item_index < len(response.results) - 1 or link_index < len(links) - 1
                            break
                if len(found) >= limit:
                    break
            offset += len(response.results)
            if offset >= total:
                break
        return BusinessSearchPage(tuple(found), scan_complete=not stopped_early and offset >= total,
                                  scanned_doclib_hits=offset)

    def read(self, revision_id: str, locator: str, *, limit: int = 12000) -> HistoricalRead:
        revision = self._store.get_revision(revision_id)
        if revision is None:
            raise DiscoveryError("revision_not_found")
        if not 1 <= limit <= 30000:
            raise DiscoveryError("invalid_read_limit")
        try:
            cursor = parse_content_cursor(locator)
        except ValueError as exc:
            raise DiscoveryError("invalid_content_locator") from exc
        if cursor.short_id.lower() != revision.short_id.lower() or cursor.tier != revision.tier:
            raise DiscoveryError("invalid_content_locator")
        parse_id = revision.parse_id_for_page(cursor.page_no)
        if parse_id is None:
            raise DiscoveryError("invalid_content_locator")
        try:
            response = self._doclib.read_parse_content(parse_id, locator, limit=limit)
        except ServerNotRunningError as exc:
            raise DiscoveryError("doclib_unavailable") from exc
        except MineruError as exc:
            raise DiscoveryError("historical_content_unavailable") from exc
        if (
            response.sha256 != revision.sha256 or response.short_id.lower() != revision.short_id.lower()
            or response.tier != revision.tier or response.request_scope.locator != locator
        ):
            raise DiscoveryError("historical_content_mismatch")
        next_locator = response.next_request.locator if response.next_request else None
        if next_locator is not None:
            try:
                next_cursor = parse_content_cursor(next_locator)
            except ValueError as exc:
                raise DiscoveryError("historical_content_mismatch") from exc
            if next_cursor.short_id.lower() != revision.short_id.lower() or next_cursor.tier != revision.tier:
                raise DiscoveryError("historical_content_mismatch")
            if revision.parse_id_for_page(next_cursor.page_no) is None:
                raise DiscoveryError("historical_content_mismatch")
        if next_locator is None:
            later_pages = sorted(page for page in parse_page_range_set(revision.page_range) if page > cursor.page_no)
            if later_pages:
                next_locator = page_ref(revision.short_id, revision.tier, later_pages[0])
        return HistoricalRead(
            document_id=revision.document_id, revision_id=revision.id, locator=locator,
            tier=revision.tier, content=response.content, truncated=response.truncated,
            next_locator=next_locator,
        )

    def search_revision(self, revision_id: str, query: str, *, start_page: int | None = None) -> RevisionSearchPage:
        query = query.strip()
        if not query or len(query) > 200 or (start_page is not None and start_page < 1):
            raise DiscoveryError("invalid_search_request")
        revision = self._store.get_revision(revision_id)
        if revision is None:
            raise DiscoveryError("revision_not_found")
        pages = sorted(parse_page_range_set(revision.page_range))
        if start_page is not None and start_page not in pages:
            raise DiscoveryError("invalid_search_request")
        start = pages.index(start_page) if start_page is not None else 0
        window = pages[start:start + self._REVISION_SCAN_PAGES]
        pattern = re.compile(re.escape(query), re.IGNORECASE)
        found: list[RevisionSearchHit] = []
        for page_no in window:
            locator = page_ref(revision.short_id, revision.tier, page_no)
            content = self.read(revision_id, locator, limit=30000)
            if content.truncated:
                raise DiscoveryError("historical_content_truncated")
            match = pattern.search(content.content)
            if match is None:
                continue
            left = max(0, match.start() - 90)
            right = min(len(content.content), match.end() + 90)
            found.append(RevisionSearchHit(locator, page_no, content.content[left:right]))
        next_index = start + len(window)
        return RevisionSearchPage(
            revision_id=revision_id, items=tuple(found),
            next_page=pages[next_index] if next_index < len(pages) else None,
            scanned_pages=len(window),
        )

    def outline(self, revision_id: str, *, start_page: int | None = None) -> RevisionOutlinePage:
        revision = self._store.get_revision(revision_id)
        if revision is None:
            raise DiscoveryError("revision_not_found")
        pages = sorted(parse_page_range_set(revision.page_range))
        if start_page is not None and start_page not in pages:
            raise DiscoveryError("invalid_outline_request")
        start = pages.index(start_page) if start_page is not None else 0
        window = pages[start:start + self._REVISION_SCAN_PAGES]
        headings: list[RevisionHeading] = []
        for page_no in window:
            locator = page_ref(revision.short_id, revision.tier, page_no)
            content = self.read(revision_id, locator, limit=30000)
            if content.truncated:
                raise DiscoveryError("historical_content_truncated")
            fence: str | None = None
            for line in content.content.splitlines():
                stripped = line.strip()
                if stripped.startswith(("```", "~~~")):
                    marker = stripped[:3]
                    if fence is None:
                        fence = marker
                    elif marker == fence:
                        fence = None
                    continue
                if fence is not None:
                    continue
                match = self._HEADING_RE.fullmatch(line)
                if match is None:
                    continue
                title = match.group(2).strip().rstrip("#").strip()
                if title:
                    headings.append(RevisionHeading(len(match.group(1)), title[:200], page_no, locator))
                    if len(headings) > 200:
                        raise DiscoveryError("outline_too_many_headings")
        next_index = start + len(window)
        return RevisionOutlinePage(
            revision_id=revision_id, items=tuple(headings),
            next_page=pages[next_index] if next_index < len(pages) else None,
            scanned_pages=len(window),
        )

    def structure(self, revision_id: str, page_no: int) -> BusinessStructurePage:
        revision = self._store.get_revision(revision_id)
        if revision is None:
            raise DiscoveryError("revision_not_found")
        parse_id = revision.parse_id_for_page(page_no)
        if parse_id is None:
            raise DiscoveryError("invalid_structure_page")
        try:
            response = self._doclib.read_parse_structure(parse_id, page_no)
        except ServerNotRunningError as exc:
            raise DiscoveryError("doclib_unavailable") from exc
        except MineruError as exc:
            raise DiscoveryError("historical_structure_unavailable") from exc
        if (
            response.sha256 != revision.sha256 or response.short_id.lower() != revision.short_id.lower()
            or response.tier != revision.tier or response.page_no != page_no or len(response.blocks) > 500
        ):
            raise DiscoveryError("historical_structure_mismatch")
        seen: set[int] = set()
        blocks: list[StructureBlock] = []
        for block in response.blocks:
            try:
                cursor = parse_content_cursor(block.locator)
            except ValueError as exc:
                raise DiscoveryError("historical_structure_mismatch") from exc
            if (
                cursor.short_id.lower() != revision.short_id.lower() or cursor.tier != revision.tier
                or cursor.page_no != page_no or cursor.char_offset is not None
                or cursor.block_no != block.block_no or len(block.preview) > 200
            ):
                raise DiscoveryError("historical_structure_mismatch")
            if block.block_no is not None:
                if block.block_no in seen:
                    raise DiscoveryError("historical_structure_mismatch")
                seen.add(block.block_no)
            blocks.append(StructureBlock(**block.model_dump()))
        return BusinessStructurePage(revision.document_id, revision_id, page_no, tuple(blocks))


__all__ = [
    "BusinessDiscovery", "BusinessSearchHit", "BusinessSearchPage", "DiscoveryError", "HistoricalRead",
    "RevisionSearchHit", "RevisionSearchPage",
    "RevisionHeading", "RevisionOutlinePage",
    "BusinessStructurePage", "StructureBlock",
]
