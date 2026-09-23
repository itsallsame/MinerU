"""Append-only review decisions and immutable confirmed result snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ReviewSource = Literal["web", "skill", "api"]
DecisionBasis = Literal["candidate_acceptance", "manual_correction"]
IssueResolutionStatus = Literal["resolved", "ignored"]


@dataclass(frozen=True)
class FieldDecision:
    id: str
    run_id: str
    field_code: str
    previous_value: str | None
    value: str
    evidence_id: str
    basis: DecisionBasis
    source: ReviewSource
    reason: str | None
    created_at_ms: int


@dataclass(frozen=True)
class IssueResolution:
    id: str
    issue_id: str
    run_id: str
    previous_status: Literal["open"]
    status: IssueResolutionStatus
    source: ReviewSource
    reason: str
    created_at_ms: int


@dataclass(frozen=True)
class ConfirmedField:
    field_code: str
    value: str
    evidence_id: str
    decision_id: str
    basis: DecisionBasis


@dataclass(frozen=True)
class ConfirmedResult:
    id: str
    run_id: str
    version: int
    revision_id: str
    template_code: str
    template_version: int
    fields: tuple[ConfirmedField, ...]
    fields_sha256: str
    source: ReviewSource
    created_at_ms: int


@dataclass(frozen=True)
class AuditEvent:
    id: str
    run_id: str
    action: Literal["field_decided", "issue_resolved", "result_confirmed"]
    target_id: str
    source: ReviewSource
    old_value: str | None
    new_value: str
    reason: str | None
    created_at_ms: int


__all__ = [
    "AuditEvent", "ConfirmedField", "ConfirmedResult", "DecisionBasis", "FieldDecision", "IssueResolution",
    "IssueResolutionStatus", "ReviewSource",
]
