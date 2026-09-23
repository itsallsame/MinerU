"""Initial single-node SQLite store for business identities and evidence."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path

from ...doclib.locators import parse_content_cursor
from ...doclib.types import ParseInfo
from ..documents.uploads import StoredUpload
from ..domain import BusinessDocument, EvidenceSnapshot, ParseRevision

_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_SCHEMA_VERSION = 1


class BusinessStoreError(ValueError):
    """A business invariant or expected record was not satisfied."""


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


class BusinessStore:
    """Single-node business DB. Connections and transactions are per operation."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def _connect(self) -> sqlite3.Connection:
        if self._path.is_symlink() or not self._path.is_file():
            raise BusinessStoreError("Business database must be explicitly initialized at a regular file path")
        database = sqlite3.connect(self._path, timeout=5)
        database.row_factory = sqlite3.Row
        database.execute("PRAGMA foreign_keys = ON")
        database.execute("PRAGMA busy_timeout = 5000")
        return database

    def initialize(self) -> None:
        """Create only the new business schema, refusing unknown existing data."""
        if not self._path.parent.is_dir():
            raise BusinessStoreError("Business data directory must already exist")
        if self._path.is_symlink():
            raise BusinessStoreError("Business database cannot be a symbolic link")
        if not self._path.exists():
            try:
                descriptor = os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
            except FileExistsError:
                pass  # Another initializer created it; inspect the existing schema below.
            else:
                os.close(descriptor)
        with closing(self._connect()) as database:
            version = database.execute("PRAGMA user_version").fetchone()[0]
            if version == _SCHEMA_VERSION:
                tables = {
                    row["name"]
                    for row in database.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                    )
                }
                if tables != {"documents", "revisions", "evidence"}:
                    raise BusinessStoreError("Business schema version does not match its tables")
                return
            if version != 0:
                raise BusinessStoreError(f"Unsupported business schema version: {version}")
            existing = database.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchone()
            if existing is not None:
                raise BusinessStoreError("Refusing to initialize an existing unknown database")
            database.execute("PRAGMA journal_mode = WAL")
            with database:
                database.executescript(
                    """
                    BEGIN IMMEDIATE;
                    CREATE TABLE documents (
                        id TEXT PRIMARY KEY,
                        owner_id TEXT NOT NULL,
                        original_name TEXT NOT NULL,
                        storage_key TEXT NOT NULL UNIQUE,
                        sha256 TEXT NOT NULL,
                        size INTEGER NOT NULL CHECK(size > 0),
                        created_at_ms INTEGER NOT NULL
                    );
                    CREATE INDEX documents_owner_created ON documents(owner_id, created_at_ms);
                    CREATE TABLE revisions (
                        id TEXT PRIMARY KEY,
                        document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE RESTRICT,
                        doclib_parse_id INTEGER NOT NULL CHECK(doclib_parse_id > 0),
                        sha256 TEXT NOT NULL,
                        short_id TEXT NOT NULL,
                        tier TEXT NOT NULL CHECK(tier IN ('flash', 'basic', 'standard', 'advanced')),
                        producer_version TEXT NOT NULL,
                        model_ref TEXT,
                        created_at_ms INTEGER NOT NULL,
                        UNIQUE(document_id, doclib_parse_id)
                    );
                    CREATE INDEX revisions_document_created ON revisions(document_id, created_at_ms);
                    CREATE TABLE evidence (
                        id TEXT PRIMARY KEY,
                        revision_id TEXT NOT NULL REFERENCES revisions(id) ON DELETE RESTRICT,
                        locator TEXT NOT NULL,
                        page_no INTEGER NOT NULL CHECK(page_no > 0),
                        block_no INTEGER CHECK(block_no > 0),
                        bbox_json TEXT,
                        snippet TEXT NOT NULL,
                        snippet_sha256 TEXT NOT NULL,
                        created_at_ms INTEGER NOT NULL
                    );
                    CREATE INDEX evidence_revision_created ON evidence(revision_id, created_at_ms);
                    PRAGMA user_version = 1;
                    COMMIT;
                    """
                )

    def create_document(self, upload: StoredUpload, *, original_name: str, owner_id: str) -> BusinessDocument:
        if not owner_id.strip() or not original_name.strip():
            raise BusinessStoreError("Document owner and original name are required")
        if not _SHA256_RE.fullmatch(upload.sha256) or upload.size < 1:
            raise BusinessStoreError("Stored upload identity is invalid")
        storage_key = upload.path.name
        if storage_key in ("", ".", "..") or upload.path.parent == upload.path:
            raise BusinessStoreError("Stored upload key is invalid")
        document = BusinessDocument(
            id=uuid.uuid4().hex,
            owner_id=owner_id,
            original_name=original_name,
            storage_key=storage_key,
            sha256=upload.sha256,
            size=upload.size,
            created_at_ms=_now_ms(),
        )
        with closing(self._connect()) as database, database:
            database.execute(
                "INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    document.id,
                    document.owner_id,
                    document.original_name,
                    document.storage_key,
                    document.sha256,
                    document.size,
                    document.created_at_ms,
                ),
            )
        return document

    def get_document(self, document_id: str, *, owner_id: str) -> BusinessDocument | None:
        with closing(self._connect()) as database:
            row = database.execute("SELECT * FROM documents WHERE id=? AND owner_id=?", (document_id, owner_id)).fetchone()
        return BusinessDocument(**dict(row)) if row is not None else None

    def add_completed_revision(
        self,
        document_id: str,
        *,
        owner_id: str,
        parse: ParseInfo,
        producer_version: str,
        model_ref: str | None = None,
    ) -> ParseRevision:
        if parse.status != "done":
            raise BusinessStoreError("Only completed Doclib parses can become evidence revisions")
        if not producer_version.strip():
            raise BusinessStoreError("Producer version is required")
        with closing(self._connect()) as database, database:
            database.execute("BEGIN IMMEDIATE")
            document = database.execute(
                "SELECT sha256 FROM documents WHERE id=? AND owner_id=?", (document_id, owner_id)
            ).fetchone()
            if document is None:
                raise BusinessStoreError("Business document not found")
            if parse.sha256 != document["sha256"]:
                raise BusinessStoreError("Doclib parse source does not match the business document")
            existing = database.execute(
                "SELECT * FROM revisions WHERE document_id=? AND doclib_parse_id=?", (document_id, parse.id)
            ).fetchone()
            if existing is not None:
                revision = ParseRevision(**dict(existing))
                if (
                    revision.sha256 != parse.sha256
                    or revision.short_id != parse.short_id
                    or revision.tier != parse.tier
                    or revision.producer_version != producer_version
                    or revision.model_ref != model_ref
                ):
                    raise BusinessStoreError("Existing parse revision has conflicting provenance")
                return revision
            revision = ParseRevision(
                id=uuid.uuid4().hex,
                document_id=document_id,
                doclib_parse_id=parse.id,
                sha256=parse.sha256,
                short_id=parse.short_id,
                tier=parse.tier,
                producer_version=producer_version,
                model_ref=model_ref,
                created_at_ms=_now_ms(),
            )
            database.execute(
                "INSERT INTO revisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    revision.id,
                    revision.document_id,
                    revision.doclib_parse_id,
                    revision.sha256,
                    revision.short_id,
                    revision.tier,
                    revision.producer_version,
                    revision.model_ref,
                    revision.created_at_ms,
                ),
            )
        return revision

    def get_revision(self, revision_id: str, *, owner_id: str) -> ParseRevision | None:
        with closing(self._connect()) as database:
            row = database.execute(
                "SELECT r.* FROM revisions r JOIN documents d ON d.id=r.document_id WHERE r.id=? AND d.owner_id=?",
                (revision_id, owner_id),
            ).fetchone()
        return ParseRevision(**dict(row)) if row is not None else None

    def capture_evidence(
        self,
        revision_id: str,
        *,
        owner_id: str,
        locator: str,
        snippet: str,
        bbox: tuple[float, float, float, float] | None = None,
    ) -> EvidenceSnapshot:
        if not snippet.strip():
            raise BusinessStoreError("Evidence requires a frozen source snippet")
        cursor = parse_content_cursor(locator)
        if bbox is not None and (
            len(bbox) != 4 or not all(math.isfinite(value) for value in bbox) or bbox[2] < bbox[0] or bbox[3] < bbox[1]
        ):
            raise BusinessStoreError("Evidence bbox must be a finite ordered rectangle")
        with closing(self._connect()) as database, database:
            row = database.execute(
                "SELECT r.*, d.owner_id FROM revisions r JOIN documents d ON d.id=r.document_id WHERE r.id=? AND d.owner_id=?",
                (revision_id, owner_id),
            ).fetchone()
            if row is None:
                raise BusinessStoreError("Parse revision not found")
            if cursor.short_id.lower() != row["short_id"].lower() or cursor.tier != row["tier"]:
                raise BusinessStoreError("Locator does not belong to the parse revision")
            evidence = EvidenceSnapshot(
                id=uuid.uuid4().hex,
                revision_id=revision_id,
                document_id=row["document_id"],
                locator=locator,
                page_no=cursor.page_no,
                block_no=cursor.block_no,
                bbox=bbox,
                snippet=snippet,
                snippet_sha256=hashlib.sha256(snippet.encode("utf-8")).hexdigest(),
                created_at_ms=_now_ms(),
            )
            database.execute(
                "INSERT INTO evidence VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    evidence.id,
                    evidence.revision_id,
                    evidence.locator,
                    evidence.page_no,
                    evidence.block_no,
                    json.dumps(evidence.bbox) if evidence.bbox is not None else None,
                    evidence.snippet,
                    evidence.snippet_sha256,
                    evidence.created_at_ms,
                ),
            )
        return evidence

    def get_evidence(self, evidence_id: str, *, owner_id: str) -> EvidenceSnapshot | None:
        with closing(self._connect()) as database:
            row = database.execute(
                "SELECT e.*, r.document_id FROM evidence e "
                "JOIN revisions r ON r.id=e.revision_id JOIN documents d ON d.id=r.document_id "
                "WHERE e.id=? AND d.owner_id=?",
                (evidence_id, owner_id),
            ).fetchone()
        if row is None:
            return None
        payload = dict(row)
        bbox_json = payload.pop("bbox_json")
        payload["bbox"] = tuple(json.loads(bbox_json)) if bbox_json is not None else None
        return EvidenceSnapshot(**payload)


__all__ = ["BusinessStore", "BusinessStoreError"]
