"""Business-owned, versioned structure views; never Doclib model mutations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .review import ReviewSource


@dataclass(frozen=True)
class StructurePageVersion:
    document_id: str
    revision_id: str
    page_no: int
    version: int
    nodes: list[dict[str, Any]]
    operation: dict[str, Any] | None
    source: ReviewSource | None
    reason: str | None
    created_at_ms: int | None


__all__ = ["StructurePageVersion"]
