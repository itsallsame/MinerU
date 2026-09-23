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
from ...types import Tier
from ..documents.uploads import StoredUpload
from ..domain import (
    BUILTIN_TEMPLATES,
    BusinessDocument,
    EvidenceSnapshot,
    IngestTask,
    ParseRevision,
    TemplateField,
    TemplateVersion,
    validate_template,
)

_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_SCHEMA_VERSION = 2


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
                if tables != {"documents", "tasks", "revisions", "evidence", "templates", "template_versions"}:
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
                        original_name TEXT NOT NULL,
                        storage_key TEXT NOT NULL UNIQUE,
                        sha256 TEXT NOT NULL,
                        size INTEGER NOT NULL CHECK(size > 0),
                        created_at_ms INTEGER NOT NULL
                    );
                    CREATE INDEX documents_created ON documents(created_at_ms);
                    CREATE TABLE tasks (
                        id TEXT PRIMARY KEY,
                        document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE RESTRICT,
                        requested_tier TEXT CHECK(requested_tier IN ('flash', 'basic', 'standard', 'advanced')),
                        actual_tier TEXT CHECK(actual_tier IN ('flash', 'basic', 'standard', 'advanced')),
                        status TEXT NOT NULL CHECK(status IN ('uploaded', 'submitted', 'done', 'failed')),
                        parse_ids_json TEXT NOT NULL,
                        error_code TEXT,
                        created_at_ms INTEGER NOT NULL,
                        updated_at_ms INTEGER NOT NULL
                    );
                    CREATE INDEX tasks_document_created ON tasks(document_id, created_at_ms);
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
                    CREATE TABLE templates (
                        code TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        built_in INTEGER NOT NULL CHECK(built_in IN (0, 1)),
                        enabled INTEGER NOT NULL CHECK(enabled IN (0, 1)),
                        current_version INTEGER NOT NULL CHECK(current_version > 0)
                    );
                    CREATE TABLE template_versions (
                        code TEXT NOT NULL REFERENCES templates(code) ON DELETE RESTRICT,
                        version INTEGER NOT NULL CHECK(version > 0),
                        name TEXT NOT NULL,
                        fields_json TEXT NOT NULL,
                        created_at_ms INTEGER NOT NULL,
                        PRIMARY KEY (code, version)
                    );
                    PRAGMA user_version = 2;
                    COMMIT;
                    """
                )
                for code, name, fields in BUILTIN_TEMPLATES:
                    self._insert_template(database, code=code, name=name, fields=fields, built_in=True)

    @staticmethod
    def _fields_json(fields: tuple[TemplateField, ...]) -> str:
        return json.dumps([vars(field) for field in fields], ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _template_from_row(row: sqlite3.Row) -> TemplateVersion:
        return TemplateVersion(
            code=row["code"],
            name=row["version_name"],
            version=row["version"],
            built_in=bool(row["built_in"]),
            enabled=bool(row["enabled"]),
            fields=tuple(TemplateField(**field) for field in json.loads(row["fields_json"])),
            created_at_ms=row["created_at_ms"],
        )

    @staticmethod
    def _insert_template(
        database: sqlite3.Connection, *, code: str, name: str, fields: tuple[TemplateField, ...], built_in: bool
    ) -> None:
        validate_template(code, name, fields)
        now = _now_ms()
        database.execute("INSERT INTO templates VALUES (?, ?, ?, 1, 1)", (code, name, int(built_in)))
        database.execute(
            "INSERT INTO template_versions VALUES (?, 1, ?, ?, ?)",
            (code, name, BusinessStore._fields_json(fields), now),
        )

    def create_template(self, *, code: str, name: str, fields: tuple[TemplateField, ...]) -> TemplateVersion:
        validate_template(code, name, fields)
        with closing(self._connect()) as database, database:
            database.execute("BEGIN IMMEDIATE")
            if database.execute("SELECT 1 FROM templates WHERE code=?", (code,)).fetchone():
                raise BusinessStoreError("Template code already exists")
            self._insert_template(database, code=code, name=name, fields=fields, built_in=False)
        template = self.get_template(code)
        assert template is not None
        return template

    def update_template(self, code: str, *, name: str, fields: tuple[TemplateField, ...]) -> TemplateVersion:
        validate_template(code, name, fields)
        with closing(self._connect()) as database, database:
            database.execute("BEGIN IMMEDIATE")
            row = database.execute("SELECT * FROM templates WHERE code=?", (code,)).fetchone()
            if row is None:
                raise BusinessStoreError("Template not found")
            if row["built_in"]:
                raise BusinessStoreError("Built-in template cannot be edited")
            if not row["enabled"]:
                raise BusinessStoreError("Disabled template cannot be edited")
            next_version = row["current_version"] + 1
            database.execute(
                "INSERT INTO template_versions VALUES (?, ?, ?, ?, ?)",
                (code, next_version, name, self._fields_json(fields), _now_ms()),
            )
            database.execute(
                "UPDATE templates SET name=?, current_version=? WHERE code=?", (name, next_version, code)
            )
        template = self.get_template(code)
        assert template is not None
        return template

    def disable_template(self, code: str) -> TemplateVersion:
        with closing(self._connect()) as database, database:
            cursor = database.execute("UPDATE templates SET enabled=0 WHERE code=? AND built_in=0", (code,))
            if cursor.rowcount != 1:
                raise BusinessStoreError("Only custom templates can be disabled")
        template = self.get_template(code)
        assert template is not None
        return template

    def get_template(self, code: str, *, version: int | None = None) -> TemplateVersion | None:
        with closing(self._connect()) as database:
            row = database.execute(
                "SELECT t.code, t.built_in, t.enabled, v.version, v.name AS version_name, "
                "v.fields_json, v.created_at_ms FROM templates t JOIN template_versions v "
                "ON v.code=t.code AND v.version=COALESCE(?, t.current_version) WHERE t.code=?",
                (version, code),
            ).fetchone()
        return self._template_from_row(row) if row is not None else None

    def list_templates(self) -> tuple[TemplateVersion, ...]:
        with closing(self._connect()) as database:
            rows = database.execute(
                "SELECT t.code, t.built_in, t.enabled, v.version, v.name AS version_name, "
                "v.fields_json, v.created_at_ms FROM templates t JOIN template_versions v "
                "ON v.code=t.code AND v.version=t.current_version ORDER BY t.code"
            ).fetchall()
        return tuple(self._template_from_row(row) for row in rows)

    def create_document(self, upload: StoredUpload, *, original_name: str) -> BusinessDocument:
        document = self._new_document(upload, original_name=original_name)
        with closing(self._connect()) as database, database:
            self._insert_document(database, document)
        return document

    def create_document_with_task(
        self, upload: StoredUpload, *, original_name: str, requested_tier: Tier | None
    ) -> tuple[BusinessDocument, IngestTask]:
        """Persist a document and recoverable initial task in one transaction."""
        document = self._new_document(upload, original_name=original_name)
        now = _now_ms()
        task = IngestTask(
            id=uuid.uuid4().hex,
            document_id=document.id,
            requested_tier=requested_tier,
            actual_tier=None,
            status="uploaded",
            parse_ids=(),
            error_code=None,
            created_at_ms=now,
            updated_at_ms=now,
        )
        with closing(self._connect()) as database, database:
            self._insert_document(database, document)
            database.execute(
                "INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (task.id, task.document_id, task.requested_tier, task.actual_tier, task.status, "[]", None, now, now),
            )
        return document, task

    @staticmethod
    def _new_document(upload: StoredUpload, *, original_name: str) -> BusinessDocument:
        if not original_name.strip():
            raise BusinessStoreError("Original document name is required")
        if not _SHA256_RE.fullmatch(upload.sha256) or upload.size < 1:
            raise BusinessStoreError("Stored upload identity is invalid")
        storage_key = upload.path.name
        if storage_key in ("", ".", "..") or upload.path.parent == upload.path:
            raise BusinessStoreError("Stored upload key is invalid")
        return BusinessDocument(
            id=uuid.uuid4().hex,
            original_name=original_name,
            storage_key=storage_key,
            sha256=upload.sha256,
            size=upload.size,
            created_at_ms=_now_ms(),
        )

    @staticmethod
    def _insert_document(database: sqlite3.Connection, document: BusinessDocument) -> None:
        database.execute(
            "INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?)",
            (
                document.id,
                document.original_name,
                document.storage_key,
                document.sha256,
                document.size,
                document.created_at_ms,
            ),
        )

    def get_document(self, document_id: str) -> BusinessDocument | None:
        with closing(self._connect()) as database:
            row = database.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
        return BusinessDocument(**dict(row)) if row is not None else None

    def get_task(self, task_id: str) -> IngestTask | None:
        with closing(self._connect()) as database:
            row = database.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return self._task_from_row(row) if row is not None else None

    def mark_task_submitted(self, task_id: str, *, actual_tier: Tier, parse_ids: tuple[int, ...]) -> IngestTask:
        if not parse_ids or any(parse_id < 1 for parse_id in parse_ids):
            raise BusinessStoreError("Doclib submission must expose parse IDs")
        with closing(self._connect()) as database, database:
            cursor = database.execute(
                "UPDATE tasks SET status='submitted', actual_tier=?, parse_ids_json=?, error_code=NULL, updated_at_ms=? "
                "WHERE id=? AND status IN ('uploaded', 'failed')",
                (actual_tier, json.dumps(parse_ids), _now_ms(), task_id),
            )
            if cursor.rowcount != 1:
                raise BusinessStoreError("Task cannot be submitted from its current state")
        task = self.get_task(task_id)
        assert task is not None
        return task

    def mark_task_failed(self, task_id: str, *, error_code: str) -> IngestTask:
        if not error_code.strip():
            raise BusinessStoreError("Failure code is required")
        with closing(self._connect()) as database, database:
            cursor = database.execute(
                "UPDATE tasks SET status='failed', error_code=?, updated_at_ms=? "
                "WHERE id=? AND status IN ('uploaded', 'submitted', 'failed')",
                (error_code, _now_ms(), task_id),
            )
            if cursor.rowcount != 1:
                raise BusinessStoreError("Task cannot fail from its current state")
        task = self.get_task(task_id)
        assert task is not None
        return task

    def mark_task_done(self, task_id: str) -> IngestTask:
        with closing(self._connect()) as database, database:
            cursor = database.execute(
                "UPDATE tasks SET status='done', updated_at_ms=? WHERE id=? AND status='submitted'",
                (_now_ms(), task_id),
            )
            if cursor.rowcount != 1:
                raise BusinessStoreError("Task cannot complete from its current state")
        task = self.get_task(task_id)
        assert task is not None
        return task

    @staticmethod
    def _task_from_row(row: sqlite3.Row) -> IngestTask:
        payload = dict(row)
        payload["parse_ids"] = tuple(json.loads(payload.pop("parse_ids_json")))
        return IngestTask(**payload)

    def add_completed_revision(
        self,
        document_id: str,
        *,
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
            document = database.execute("SELECT sha256 FROM documents WHERE id=?", (document_id,)).fetchone()
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

    def get_revision(self, revision_id: str) -> ParseRevision | None:
        with closing(self._connect()) as database:
            row = database.execute("SELECT * FROM revisions WHERE id=?", (revision_id,)).fetchone()
        return ParseRevision(**dict(row)) if row is not None else None

    def list_revisions(self, document_id: str) -> tuple[ParseRevision, ...]:
        with closing(self._connect()) as database:
            rows = database.execute(
                "SELECT * FROM revisions WHERE document_id=? ORDER BY created_at_ms DESC, id DESC", (document_id,)
            ).fetchall()
        return tuple(ParseRevision(**dict(row)) for row in rows)

    def capture_evidence(
        self,
        revision_id: str,
        *,
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
            row = database.execute("SELECT * FROM revisions WHERE id=?", (revision_id,)).fetchone()
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

    def get_evidence(self, evidence_id: str) -> EvidenceSnapshot | None:
        with closing(self._connect()) as database:
            row = database.execute(
                "SELECT e.*, r.document_id FROM evidence e JOIN revisions r ON r.id=e.revision_id WHERE e.id=?",
                (evidence_id,),
            ).fetchone()
        if row is None:
            return None
        payload = dict(row)
        bbox_json = payload.pop("bbox_json")
        payload["bbox"] = tuple(json.loads(bbox_json)) if bbox_json is not None else None
        return EvidenceSnapshot(**payload)

    def list_evidence(self, revision_id: str) -> tuple[EvidenceSnapshot, ...]:
        with closing(self._connect()) as database:
            rows = database.execute(
                "SELECT e.*, r.document_id FROM evidence e JOIN revisions r ON r.id=e.revision_id "
                "WHERE e.revision_id=? ORDER BY e.created_at_ms DESC, e.id DESC",
                (revision_id,),
            ).fetchall()
        result = []
        for row in rows:
            payload = dict(row)
            bbox_json = payload.pop("bbox_json")
            payload["bbox"] = tuple(json.loads(bbox_json)) if bbox_json is not None else None
            result.append(EvidenceSnapshot(**payload))
        return tuple(result)


__all__ = ["BusinessStore", "BusinessStoreError"]
