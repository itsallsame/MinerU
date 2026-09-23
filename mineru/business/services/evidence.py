"""Frozen evidence retrieval with honest current-locator verification."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

from ...doclib import DoclibInterface
from ...doclib.locators import parse_content_cursor
from ...errors import MineruError
from ..domain import EvidenceSnapshot
from ..store import BusinessStore, BusinessStoreError

NavigationStatus = Literal["current_match", "changed", "unavailable"]


@dataclass(frozen=True)
class EvidenceInspection:
    snapshot: EvidenceSnapshot
    navigation_status: NavigationStatus


class EvidenceReader:
    """Never replace a frozen snippet with live Doclib content."""

    def __init__(self, *, store: BusinessStore, doclib: DoclibInterface) -> None:
        self._store = store
        self._doclib = doclib

    def inspect(self, evidence_id: str) -> EvidenceInspection | None:
        snapshot = self._store.get_evidence(evidence_id)
        if snapshot is None:
            return None
        revision = self._store.get_revision(snapshot.revision_id)
        if revision is None:
            return EvidenceInspection(snapshot, "unavailable")
        try:
            current = self._doclib.read_content(snapshot.locator, context=0, limit=30000, format="markdown")
        except MineruError:
            return EvidenceInspection(snapshot, "unavailable")
        if current.truncated:
            return EvidenceInspection(snapshot, "unavailable")
        if (
            current.sha256 != revision.sha256
            or current.tier != revision.tier
            or current.request_scope.locator != snapshot.locator
        ):
            return EvidenceInspection(snapshot, "changed")
        current_hash = hashlib.sha256(current.content.encode("utf-8")).hexdigest()
        status: NavigationStatus = "current_match" if current_hash == snapshot.snippet_sha256 else "changed"
        return EvidenceInspection(snapshot, status)


class EvidenceCaptureError(RuntimeError):
    """Safe, path-free rejection of a requested historical source capture."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class EvidenceWriter:
    """Capture only server-read content from a specific persisted parse ID."""

    def __init__(self, *, store: BusinessStore, doclib: DoclibInterface) -> None:
        self._store = store
        self._doclib = doclib

    def capture(self, revision_id: str, *, locator: str) -> EvidenceSnapshot:
        revision = self._store.get_revision(revision_id)
        if revision is None:
            raise EvidenceCaptureError("revision_not_found")
        try:
            cursor = parse_content_cursor(locator)
        except ValueError as exc:
            raise EvidenceCaptureError("invalid_evidence_locator") from exc
        if cursor.short_id.lower() != revision.short_id.lower() or cursor.tier != revision.tier:
            raise EvidenceCaptureError("invalid_evidence_locator")
        try:
            content = self._doclib.read_parse_content(revision.doclib_parse_id, locator, limit=30000)
        except MineruError as exc:
            raise EvidenceCaptureError("historical_content_unavailable") from exc
        if content.truncated or not content.content.strip():
            raise EvidenceCaptureError("historical_content_unavailable")
        if (
            content.sha256 != revision.sha256
            or content.short_id.lower() != revision.short_id.lower()
            or content.tier != revision.tier
            or content.request_scope.locator != locator
        ):
            raise EvidenceCaptureError("historical_content_mismatch")
        try:
            return self._store.capture_evidence(revision_id, locator=locator, snippet=content.content)
        except (BusinessStoreError, ValueError) as exc:
            raise EvidenceCaptureError("invalid_evidence_locator") from exc


__all__ = ["EvidenceCaptureError", "EvidenceInspection", "EvidenceReader", "EvidenceWriter", "NavigationStatus"]
