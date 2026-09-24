"""Small, local-only adapter from business-owned files to the Doclib SDK."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from ...doclib import DoclibInterface, ParseRequest
from ...doclib.types import ParseInfo, ParseSubmitStatus
from ...filetypes import FLASH_ONLY_PARSE_EXTENSIONS, TIERED_PARSE_EXTENSIONS, normalize_parse_extension
from ...parser.page_range import parse_page_range_set
from ...types import Tier


class DocumentPathError(ValueError):
    """The file is not a regular file inside the shared ingestion directory."""


class DocumentIntegrityError(RuntimeError):
    """The source file changed while Doclib was ingesting it."""


@dataclass(frozen=True)
class SubmittedParse:
    """Stable source identity plus the parse tasks created or reused by Doclib."""

    sha256: str
    short_id: str | None
    tier: Tier
    status: ParseSubmitStatus
    parse_ids: tuple[int, ...]


def resolve_parse_tier(source: str | Path, tier: Tier | None) -> Tier:
    """Apply MinerU 4's native-format versus PDF/image tier rule."""
    extension = normalize_parse_extension(source)
    if extension in FLASH_ONLY_PARSE_EXTENSIONS:
        if tier not in (None, "flash"):
            raise ValueError(f"{extension} supports only the flash tier")
        return "flash"
    if extension in TIERED_PARSE_EXTENSIONS:
        if tier is None:
            raise ValueError("PDF and image sources require an explicit parse tier")
        return tier
    raise ValueError(f"Unsupported document extension: {extension}")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _completed_coverage_ids(parses: list[ParseInfo], *, page_range: str) -> tuple[int, ...]:
    """Choose a non-overlapping exact cover, not every historical done batch."""
    requested = frozenset(parse_page_range_set(page_range))
    if not requested:
        raise DocumentIntegrityError("Completed parse response has no page coverage")
    candidates = []
    for parse in parses:
        pages = frozenset(parse_page_range_set(parse.page_range))
        if parse.status == "done" and pages and pages <= requested:
            candidates.append((parse.id, pages, parse.done_at or 0))
    candidates.sort(key=lambda item: (-len(item[1]), -item[2], -item[0]))
    explored = 0

    @lru_cache(maxsize=10000)
    def cover(remaining: frozenset[int]) -> tuple[int, ...] | None:
        nonlocal explored
        if not remaining:
            return ()
        explored += 1
        if explored > 10000:
            raise DocumentIntegrityError("Completed parse coverage is too ambiguous")
        first = min(remaining)
        for parse_id, pages, _done_at in candidates:
            if first in pages and pages <= remaining:
                rest = cover(remaining - pages)
                if rest is not None:
                    return (parse_id, *rest)
        return None

    selected = cover(requested)
    if selected is None:
        raise DocumentIntegrityError("Completed parse batches do not form a disjoint full-page cover")
    return selected


class DoclibGateway:
    """Submit only shared, immutable business source paths to the local Doclib."""

    def __init__(self, client: DoclibInterface, *, shared_root: Path) -> None:
        root = shared_root.resolve(strict=True)
        if not root.is_dir():
            raise DocumentPathError("Shared ingestion root must be a directory")
        self._client = client
        self._shared_root = root

    def submit(
        self, source: Path, *, tier: Tier | None = None, force: bool = False, consumer_key: str | None = None
    ) -> SubmittedParse:
        if source.is_symlink():
            raise DocumentPathError("Symlink sources are not allowed")
        try:
            path = source.resolve(strict=True)
        except OSError as exc:
            raise DocumentPathError(f"Source file cannot be resolved: {source}") from exc
        if not path.is_file() or not path.is_relative_to(self._shared_root):
            raise DocumentPathError("Source must be a regular file inside the shared ingestion root")
        resolved_tier = resolve_parse_tier(path, tier)
        extension = normalize_parse_extension(path)
        before = _sha256_file(path)
        response = self._client.ensure_parse(ParseRequest(
            path=str(path), tier=resolved_tier, page_range="all" if extension == "pdf" else None,
            force=force, remote=False, consumer_key=consumer_key,
        ))
        after = _sha256_file(path)
        if before != after or response.sha256 != before:
            raise DocumentIntegrityError("Source bytes changed while Doclib ingested the file")
        parse_ids = tuple(dict.fromkeys(response.wait_parse_ids + response.created_parse_ids + response.reused_parse_ids))
        if not parse_ids and response.status == "done":
            # A completed cache hit can omit every parse-ID list. Resolve it
            # through the public Doclib listing, not its private SQLite tables.
            listing = self._client.list_parses(doc_ref=response.sha256, tier=response.tier, status="done", limit=200)
            if listing.total > len(listing.parses):
                raise DocumentIntegrityError("Completed parse listing was truncated")
            parse_ids = _completed_coverage_ids(
                [parse_info for parse_info in listing.parses
                 if parse_info.sha256 == response.sha256 and parse_info.tier == response.tier],
                page_range=response.page_range,
            )
        return SubmittedParse(
            sha256=response.sha256,
            short_id=response.short_id,
            tier=response.tier,
            status=response.status,
            parse_ids=parse_ids,
        )


__all__ = ["DoclibGateway", "DocumentIntegrityError", "DocumentPathError", "SubmittedParse", "resolve_parse_tier"]
