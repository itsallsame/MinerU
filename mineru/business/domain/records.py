"""Persisted, immutable views of the business document contract."""

from __future__ import annotations

from dataclasses import dataclass

from ...types import Tier


@dataclass(frozen=True)
class BusinessDocument:
    id: str
    owner_id: str
    original_name: str
    storage_key: str
    sha256: str
    size: int
    created_at_ms: int


@dataclass(frozen=True)
class ParseRevision:
    id: str
    document_id: str
    doclib_parse_id: int
    sha256: str
    short_id: str
    tier: Tier
    producer_version: str
    model_ref: str | None
    created_at_ms: int


@dataclass(frozen=True)
class EvidenceSnapshot:
    id: str
    revision_id: str
    document_id: str
    locator: str
    page_no: int
    block_no: int | None
    bbox: tuple[float, float, float, float] | None
    snippet: str
    snippet_sha256: str
    created_at_ms: int


__all__ = ["BusinessDocument", "EvidenceSnapshot", "ParseRevision"]
