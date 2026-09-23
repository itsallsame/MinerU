"""Unconfirmed candidates and quality issues from one immutable parse/template pair."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ExtractionStatus = Literal["running", "done", "failed"]
IssueCode = Literal["required_missing", "conflicting_candidates", "coverage_incomplete"]


@dataclass(frozen=True)
class ExtractionRun:
    id: str
    revision_id: str
    template_code: str
    template_version: int
    status: ExtractionStatus
    error_code: str | None
    created_at_ms: int
    updated_at_ms: int


@dataclass(frozen=True)
class FieldCandidate:
    id: str
    run_id: str
    field_code: str
    value: str
    evidence_id: str
    method: Literal["label_rule"]
    created_at_ms: int


@dataclass(frozen=True)
class QualityIssue:
    id: str
    run_id: str
    field_code: str | None
    code: IssueCode
    severity: Literal["blocking"]
    status: Literal["open"]
    created_at_ms: int


__all__ = ["ExtractionRun", "ExtractionStatus", "FieldCandidate", "IssueCode", "QualityIssue"]
