"""Frozen evidence retrieval with honest current-locator verification."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

from ...doclib import DoclibInterface
from ...errors import MineruError
from ..domain import EvidenceSnapshot
from ..store import BusinessStore

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


__all__ = ["EvidenceInspection", "EvidenceReader", "NavigationStatus"]
