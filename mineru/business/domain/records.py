"""Persisted, immutable views of the business document contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ...types import Tier
from ...parser.page_range import parse_page_range_set

TaskStatus = Literal["uploaded", "submitting", "submitted", "done", "failed", "cancel_requested", "cancelled"]
CancelEffect = Literal["not_submitted", "queued_skipped", "may_continue"]


@dataclass(frozen=True)
class BusinessDocument:
    id: str
    original_name: str
    storage_key: str
    sha256: str
    size: int
    created_at_ms: int
    template_code: str | None
    template_version: int | None


@dataclass(frozen=True)
class IngestTask:
    id: str
    document_id: str
    requested_tier: Tier | None
    actual_tier: Tier | None
    status: TaskStatus
    parse_ids: tuple[int, ...]
    error_code: str | None
    created_at_ms: int
    updated_at_ms: int
    cancel_effect: CancelEffect | None = None
    cancel_results_json: str | None = None
    submission_attempt: int = 1
    submission_force: bool = False


@dataclass(frozen=True)
class ParseBatch:
    parse_id: int
    page_range: str


@dataclass(frozen=True)
class ParseRevision:
    id: str
    document_id: str
    doclib_parse_id: int
    parse_batches: tuple[ParseBatch, ...]
    page_range: str
    sha256: str
    short_id: str
    tier: Tier
    producer_version: str
    model_ref: str | None
    created_at_ms: int

    def parse_id_for_page(self, page_no: int) -> int | None:
        for batch in self.parse_batches:
            if page_no in parse_page_range_set(batch.page_range):
                return batch.parse_id
        return None


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


__all__ = ["BusinessDocument", "CancelEffect", "EvidenceSnapshot", "IngestTask", "ParseBatch", "ParseRevision", "TaskStatus"]
