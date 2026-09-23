"""Business-scoped Doclib discovery and revision-bound progressive reading."""

from __future__ import annotations

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


class BusinessDiscovery:
    """Never expose Doclib-only documents, paths, parse IDs, or mutable hits as reviewed evidence."""

    _MAX_SCAN = 500

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


__all__ = ["BusinessDiscovery", "BusinessSearchHit", "BusinessSearchPage", "DiscoveryError", "HistoricalRead"]
