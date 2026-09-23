"""Small, local-only adapter from business-owned files to the Doclib SDK."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from ...doclib import DoclibInterface, ParseRequest
from ...doclib.types import ParseSubmitStatus
from ...filetypes import FLASH_ONLY_PARSE_EXTENSIONS, TIERED_PARSE_EXTENSIONS, normalize_parse_extension
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


class DoclibGateway:
    """Submit only shared, immutable business source paths to the local Doclib."""

    def __init__(self, client: DoclibInterface, *, shared_root: Path) -> None:
        root = shared_root.resolve(strict=True)
        if not root.is_dir():
            raise DocumentPathError("Shared ingestion root must be a directory")
        self._client = client
        self._shared_root = root

    def submit(self, source: Path, *, tier: Tier | None = None, force: bool = False) -> SubmittedParse:
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
            force=force, remote=False,
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
            parse_ids = tuple(
                parse_info.id
                for parse_info in listing.parses
                if parse_info.sha256 == response.sha256 and parse_info.tier == response.tier
            )
        return SubmittedParse(
            sha256=response.sha256,
            short_id=response.short_id,
            tier=response.tier,
            status=response.status,
            parse_ids=parse_ids,
        )


__all__ = ["DoclibGateway", "DocumentIntegrityError", "DocumentPathError", "SubmittedParse", "resolve_parse_tier"]
