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
from ...doclib.types import ParseInfo, ParseReleaseResponse
from ...parser.page_range import format_page_range, parse_page_range_set
from ...types import Tier
from ..documents.uploads import StoredUpload
from ..domain import (
    AuditEvent,
    AuditPage,
    AuditRecord,
    BUILTIN_TEMPLATES,
    BusinessDocument,
    CandidateMethod,
    ConfirmedField,
    ConfirmedResult,
    EvidenceSnapshot,
    ExtractionRun,
    FieldCandidate,
    FieldDecision,
    IngestTask,
    IssueResolution,
    ParseBatch,
    ParseRevision,
    QualityIssue,
    ReviewSource,
    TemplateField,
    TemplateVersion,
    TaskStatus,
    validate_template,
)

_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_SCHEMA_VERSION = 14
_REQUEST_KEY_RE = re.compile(r"[A-Za-z0-9_-]{16,128}\Z")


class BusinessStoreError(ValueError):
    """A business invariant or expected record was not satisfied."""


class UploadRequestConflict(BusinessStoreError):
    """An idempotency key was reused for a different upload intent."""


class ExtractionRequestConflict(BusinessStoreError):
    """An idempotency key was reused for another extraction revision."""


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def _verify_evidence_checksum(snippet: str, expected_sha256: str) -> None:
    if not isinstance(snippet, str) or not isinstance(expected_sha256, str) or (
        hashlib.sha256(snippet.encode("utf-8")).hexdigest() != expected_sha256
    ):
        raise BusinessStoreError("Frozen evidence checksum mismatch")


def _verify_result_checksum(fields_json: str, expected_sha256: str) -> None:
    if not isinstance(fields_json, str) or not isinstance(expected_sha256, str) or (
        hashlib.sha256(fields_json.encode("utf-8")).hexdigest() != expected_sha256
    ):
        raise BusinessStoreError("Confirmed result checksum mismatch")


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
                if tables != {
                    "documents", "tasks", "revisions", "evidence", "templates", "template_versions",
                    "extraction_runs", "field_candidates", "quality_issues", "field_decisions",
                    "issue_resolutions", "confirmed_results", "audit_events", "ingest_requests",
                    "extraction_requests",
                }:
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
                        created_at_ms INTEGER NOT NULL,
                        template_code TEXT,
                        template_version INTEGER,
                        CHECK ((template_code IS NULL AND template_version IS NULL) OR
                               (template_code IS NOT NULL AND template_version IS NOT NULL)),
                        FOREIGN KEY (template_code, template_version)
                            REFERENCES template_versions(code, version) ON DELETE RESTRICT
                    );
                    CREATE INDEX documents_created ON documents(created_at_ms);
                    CREATE TABLE tasks (
                        id TEXT PRIMARY KEY,
                        document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE RESTRICT,
                        requested_tier TEXT CHECK(requested_tier IN ('flash', 'basic', 'standard', 'advanced')),
                        actual_tier TEXT CHECK(actual_tier IN ('flash', 'basic', 'standard', 'advanced')),
                        status TEXT NOT NULL CHECK(status IN (
                            'uploaded', 'submitting', 'submitted', 'done', 'failed', 'cancel_requested', 'cancelled'
                        )),
                        parse_ids_json TEXT NOT NULL,
                        error_code TEXT,
                        created_at_ms INTEGER NOT NULL,
                        updated_at_ms INTEGER NOT NULL,
                        cancel_effect TEXT CHECK(cancel_effect IN ('not_submitted', 'queued_skipped', 'may_continue')),
                        cancel_results_json TEXT,
                        submission_attempt INTEGER NOT NULL CHECK(submission_attempt BETWEEN 1 AND 1000000),
                        submission_force INTEGER NOT NULL CHECK(submission_force IN (0, 1))
                    );
                    CREATE INDEX tasks_document_created ON tasks(document_id, created_at_ms);
                    CREATE TABLE ingest_requests (
                        request_key TEXT PRIMARY KEY,
                        document_id TEXT NOT NULL UNIQUE REFERENCES documents(id) ON DELETE RESTRICT,
                        task_id TEXT NOT NULL UNIQUE REFERENCES tasks(id) ON DELETE RESTRICT,
                        original_name TEXT NOT NULL,
                        sha256 TEXT NOT NULL,
                        size INTEGER NOT NULL CHECK(size > 0),
                        requested_tier TEXT,
                        template_code TEXT
                    );
                    CREATE TABLE revisions (
                        id TEXT PRIMARY KEY,
                        document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE RESTRICT,
                        doclib_parse_id INTEGER NOT NULL CHECK(doclib_parse_id > 0),
                        parse_batches_json TEXT NOT NULL,
                        page_range TEXT NOT NULL,
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
                    CREATE TABLE extraction_runs (
                        id TEXT PRIMARY KEY,
                        revision_id TEXT NOT NULL REFERENCES revisions(id) ON DELETE RESTRICT,
                        template_code TEXT NOT NULL,
                        template_version INTEGER NOT NULL,
                        status TEXT NOT NULL CHECK(status IN ('queued', 'running', 'done', 'failed')),
                        error_code TEXT,
                        claim_token TEXT,
                        lease_until_ms INTEGER,
                        attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
                        created_at_ms INTEGER NOT NULL,
                        updated_at_ms INTEGER NOT NULL,
                        CHECK ((status = 'running' AND claim_token IS NOT NULL AND lease_until_ms IS NOT NULL)
                            OR (status != 'running' AND claim_token IS NULL AND lease_until_ms IS NULL)),
                        FOREIGN KEY (template_code, template_version)
                            REFERENCES template_versions(code, version) ON DELETE RESTRICT
                    );
                    CREATE INDEX extraction_runs_revision_created ON extraction_runs(revision_id, created_at_ms);
                    CREATE UNIQUE INDEX extraction_runs_one_active_revision
                        ON extraction_runs(revision_id) WHERE status IN ('queued', 'running');
                    CREATE TABLE extraction_requests (
                        request_key TEXT PRIMARY KEY,
                        revision_id TEXT NOT NULL REFERENCES revisions(id) ON DELETE RESTRICT,
                        run_id TEXT NOT NULL REFERENCES extraction_runs(id) ON DELETE RESTRICT,
                        created_at_ms INTEGER NOT NULL
                    );
                    CREATE TABLE field_candidates (
                        id TEXT PRIMARY KEY,
                        run_id TEXT NOT NULL REFERENCES extraction_runs(id) ON DELETE RESTRICT,
                        field_code TEXT NOT NULL,
                        value TEXT NOT NULL,
                        evidence_id TEXT NOT NULL REFERENCES evidence(id) ON DELETE RESTRICT,
                        method TEXT NOT NULL CHECK(method IN (
                            'label_rule', 'native_doc_title', 'section_heading', 'table_row'
                        )),
                        created_at_ms INTEGER NOT NULL,
                        UNIQUE(run_id, field_code, value, evidence_id)
                    );
                    CREATE INDEX field_candidates_run_created ON field_candidates(run_id, created_at_ms);
                    CREATE TABLE quality_issues (
                        id TEXT PRIMARY KEY,
                        run_id TEXT NOT NULL REFERENCES extraction_runs(id) ON DELETE RESTRICT,
                        field_code TEXT,
                        code TEXT NOT NULL CHECK(code IN ('required_missing', 'conflicting_candidates', 'coverage_incomplete')),
                        severity TEXT NOT NULL CHECK(severity = 'blocking'),
                        status TEXT NOT NULL CHECK(status IN ('open', 'resolved', 'ignored')),
                        created_at_ms INTEGER NOT NULL,
                        UNIQUE(run_id, field_code, code)
                    );
                    CREATE INDEX quality_issues_run_created ON quality_issues(run_id, created_at_ms);
                    CREATE TABLE field_decisions (
                        id TEXT PRIMARY KEY,
                        run_id TEXT NOT NULL REFERENCES extraction_runs(id) ON DELETE RESTRICT,
                        field_code TEXT NOT NULL,
                        previous_value TEXT,
                        value TEXT NOT NULL,
                        evidence_id TEXT NOT NULL REFERENCES evidence(id) ON DELETE RESTRICT,
                        basis TEXT NOT NULL CHECK(basis IN ('candidate_acceptance', 'manual_correction')),
                        source TEXT NOT NULL CHECK(source IN ('web', 'skill', 'api')),
                        reason TEXT,
                        created_at_ms INTEGER NOT NULL
                    );
                    CREATE INDEX field_decisions_run_created ON field_decisions(run_id, created_at_ms);
                    CREATE TABLE issue_resolutions (
                        id TEXT PRIMARY KEY,
                        issue_id TEXT NOT NULL UNIQUE REFERENCES quality_issues(id) ON DELETE RESTRICT,
                        run_id TEXT NOT NULL REFERENCES extraction_runs(id) ON DELETE RESTRICT,
                        previous_status TEXT NOT NULL CHECK(previous_status = 'open'),
                        status TEXT NOT NULL CHECK(status IN ('resolved', 'ignored')),
                        source TEXT NOT NULL CHECK(source IN ('web', 'skill', 'api')),
                        reason TEXT NOT NULL,
                        created_at_ms INTEGER NOT NULL
                    );
                    CREATE INDEX issue_resolutions_run_created ON issue_resolutions(run_id, created_at_ms);
                    CREATE TABLE confirmed_results (
                        id TEXT PRIMARY KEY,
                        run_id TEXT NOT NULL REFERENCES extraction_runs(id) ON DELETE RESTRICT,
                        version INTEGER NOT NULL CHECK(version > 0),
                        revision_id TEXT NOT NULL REFERENCES revisions(id) ON DELETE RESTRICT,
                        template_code TEXT NOT NULL,
                        template_version INTEGER NOT NULL,
                        fields_json TEXT NOT NULL,
                        fields_sha256 TEXT NOT NULL,
                        source TEXT NOT NULL CHECK(source IN ('web', 'skill', 'api')),
                        created_at_ms INTEGER NOT NULL,
                        FOREIGN KEY (template_code, template_version)
                            REFERENCES template_versions(code, version) ON DELETE RESTRICT,
                        UNIQUE(run_id, version)
                    );
                    CREATE INDEX confirmed_results_run_version ON confirmed_results(run_id, version);
                    CREATE TABLE audit_events (
                        id TEXT PRIMARY KEY,
                        run_id TEXT NOT NULL REFERENCES extraction_runs(id) ON DELETE RESTRICT,
                        action TEXT NOT NULL CHECK(action IN ('field_decided', 'issue_resolved', 'result_confirmed')),
                        target_id TEXT NOT NULL,
                        source TEXT NOT NULL CHECK(source IN ('web', 'skill', 'api')),
                        old_value TEXT,
                        new_value TEXT NOT NULL,
                        reason TEXT,
                        created_at_ms INTEGER NOT NULL
                    );
                    CREATE INDEX audit_events_run_created ON audit_events(run_id, created_at_ms);
                    """
                )
                for code, name, fields in BUILTIN_TEMPLATES:
                    self._insert_template(database, code=code, name=name, fields=fields, built_in=True)
                database.execute("PRAGMA user_version = 14")
                database.execute("COMMIT")

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

    def quality_stats(self) -> dict[str, int]:
        """Count persisted workflow records, not inferred accuracy or unique content hashes."""
        queries = {
            "documents": "SELECT COUNT(*) FROM documents",
            "parse_pending": (
                "SELECT COUNT(*) FROM tasks WHERE status IN ('uploaded', 'submitting', 'submitted', 'cancel_requested')"
            ),
            "parse_failed": "SELECT COUNT(*) FROM tasks WHERE status = 'failed'",
            "parse_done": "SELECT COUNT(*) FROM tasks WHERE status = 'done'",
            "revisions": "SELECT COUNT(*) FROM revisions",
            "extraction_pending": "SELECT COUNT(*) FROM extraction_runs WHERE status IN ('queued', 'running')",
            "extraction_failed": "SELECT COUNT(*) FROM extraction_runs WHERE status = 'failed'",
            "extraction_done": "SELECT COUNT(*) FROM extraction_runs WHERE status = 'done'",
            "open_issues": "SELECT COUNT(*) FROM quality_issues WHERE status = 'open'",
            "confirmed_runs": "SELECT COUNT(DISTINCT run_id) FROM confirmed_results",
            "result_versions": "SELECT COUNT(*) FROM confirmed_results",
        }
        with closing(self._connect()) as database:
            database.execute("BEGIN")
            result = {name: int(database.execute(query).fetchone()[0]) for name, query in queries.items()}
            database.rollback()
        return result

    def enqueue_extraction(self, revision_id: str, *, request_key: str | None = None) -> ExtractionRun:
        """Persist a run and optionally bind one durable request key in the same transaction."""
        if request_key is not None and _REQUEST_KEY_RE.fullmatch(request_key) is None:
            raise BusinessStoreError("Invalid extraction idempotency key")
        with closing(self._connect()) as database, database:
            database.execute("BEGIN IMMEDIATE")
            if request_key is not None:
                prior = database.execute(
                    "SELECT revision_id, run_id FROM extraction_requests WHERE request_key=?", (request_key,)
                ).fetchone()
                if prior is not None:
                    if prior["revision_id"] != revision_id:
                        raise ExtractionRequestConflict("Idempotency key belongs to a different extraction")
                    existing = database.execute(
                        "SELECT * FROM extraction_runs WHERE id=?", (prior["run_id"],)
                    ).fetchone()
                    if existing is None or existing["revision_id"] != revision_id:
                        raise BusinessStoreError("Idempotent extraction record is incomplete")
                    return ExtractionRun(**dict(existing))
            run = self._enqueue_extraction_in_transaction(database, revision_id, automatic=False)
            assert run is not None
            if request_key is not None:
                database.execute(
                    "INSERT INTO extraction_requests VALUES (?, ?, ?, ?)",
                    (request_key, revision_id, run.id, _now_ms()),
                )
        return run

    def get_extraction_request(self, request_key: str) -> ExtractionRun | None:
        """Read a prior keyed write outcome without creating another extraction run."""
        if _REQUEST_KEY_RE.fullmatch(request_key) is None:
            raise BusinessStoreError("Invalid extraction idempotency key")
        with closing(self._connect()) as database:
            request = database.execute(
                "SELECT revision_id, run_id FROM extraction_requests WHERE request_key=?", (request_key,)
            ).fetchone()
            if request is None:
                return None
            row = database.execute("SELECT * FROM extraction_runs WHERE id=?", (request["run_id"],)).fetchone()
        if row is None or row["revision_id"] != request["revision_id"]:
            raise BusinessStoreError("Idempotent extraction record is incomplete")
        return ExtractionRun(**dict(row))

    @staticmethod
    def _enqueue_extraction_in_transaction(
        database: sqlite3.Connection, revision_id: str, *, automatic: bool
    ) -> ExtractionRun | None:
        row = database.execute(
            "SELECT d.template_code, d.template_version FROM revisions r "
            "JOIN documents d ON d.id=r.document_id WHERE r.id=?", (revision_id,)
        ).fetchone()
        if row is None:
            raise BusinessStoreError("Parse revision not found")
        if row["template_code"] is None or row["template_version"] is None:
            if automatic:
                return None
            raise BusinessStoreError("Document has no selected template")
        existing = database.execute(
            "SELECT * FROM extraction_runs WHERE revision_id=?"
            + ("" if automatic else " AND status IN ('queued', 'running')")
            + " ORDER BY created_at_ms DESC, id DESC LIMIT 1",
            (revision_id,),
        ).fetchone()
        if existing is not None:
            return ExtractionRun(**dict(existing))
        now = _now_ms()
        run = ExtractionRun(
            id=uuid.uuid4().hex, revision_id=revision_id,
            template_code=row["template_code"], template_version=row["template_version"],
            status="queued", error_code=None, claim_token=None, lease_until_ms=None,
            attempts=0, created_at_ms=now, updated_at_ms=now,
        )
        database.execute(
            "INSERT INTO extraction_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run.id, run.revision_id, run.template_code, run.template_version,
             run.status, run.error_code, run.claim_token, run.lease_until_ms, run.attempts,
             run.created_at_ms, run.updated_at_ms),
        )
        return run

    def claim_next_extraction(self, *, lease_ms: int = 180000, max_attempts: int = 3) -> ExtractionRun | None:
        if lease_ms < 1 or max_attempts < 1:
            raise BusinessStoreError("Extraction lease and attempts must be positive")
        with closing(self._connect()) as database, database:
            database.execute("BEGIN IMMEDIATE")
            now = _now_ms()
            database.execute(
                "UPDATE extraction_runs SET status='failed', error_code='worker_retries_exhausted', "
                "claim_token=NULL, lease_until_ms=NULL, updated_at_ms=? "
                "WHERE status='running' AND lease_until_ms<=? AND attempts>=?",
                (now, now, max_attempts),
            )
            row = database.execute(
                "SELECT * FROM extraction_runs WHERE status='queued' OR "
                "(status='running' AND lease_until_ms<=? AND attempts<?) "
                "ORDER BY created_at_ms, rowid LIMIT 1",
                (now, max_attempts),
            ).fetchone()
            if row is None:
                return None
            if row["status"] == "running":
                # A crashed lease may have left unconfirmed partial candidates. Never mix attempts.
                database.execute("DELETE FROM field_candidates WHERE run_id=?", (row["id"],))
            token = uuid.uuid4().hex
            database.execute(
                "UPDATE extraction_runs SET status='running', claim_token=?, lease_until_ms=?, "
                "attempts=attempts+1, updated_at_ms=? WHERE id=?",
                (token, now + lease_ms, now, row["id"]),
            )
            claimed = database.execute("SELECT * FROM extraction_runs WHERE id=?", (row["id"],)).fetchone()
        assert claimed is not None
        return ExtractionRun(**dict(claimed))

    def renew_extraction_lease(self, run_id: str, *, claim_token: str, lease_ms: int = 180000) -> None:
        if lease_ms < 1:
            raise BusinessStoreError("Extraction lease must be positive")
        now = _now_ms()
        with closing(self._connect()) as database, database:
            cursor = database.execute(
                "UPDATE extraction_runs SET lease_until_ms=?, updated_at_ms=? "
                "WHERE id=? AND status='running' AND claim_token=? AND lease_until_ms>?",
                (now + lease_ms, now, run_id, claim_token, now),
            )
            if cursor.rowcount != 1:
                raise BusinessStoreError("Extraction claim is no longer active")

    def get_extraction(self, run_id: str) -> ExtractionRun | None:
        with closing(self._connect()) as database:
            row = database.execute("SELECT * FROM extraction_runs WHERE id=?", (run_id,)).fetchone()
        return ExtractionRun(**dict(row)) if row is not None else None

    def list_extractions(self, revision_id: str) -> tuple[ExtractionRun, ...]:
        with closing(self._connect()) as database:
            rows = database.execute(
                "SELECT * FROM extraction_runs WHERE revision_id=? ORDER BY created_at_ms DESC, id DESC",
                (revision_id,),
            ).fetchall()
        return tuple(ExtractionRun(**dict(row)) for row in rows)

    def add_field_candidate(
        self, run_id: str, *, claim_token: str, field_code: str, value: str, evidence_id: str,
        method: CandidateMethod = "label_rule",
    ) -> FieldCandidate:
        """Only a frozen snippet from this run's revision can support a candidate."""
        if not value.strip() or len(value) > 2000:
            raise BusinessStoreError("Candidate value must be 1-2000 characters")
        if method not in ("label_rule", "native_doc_title", "section_heading", "table_row"):
            raise BusinessStoreError("Unsupported candidate method")
        with closing(self._connect()) as database, database:
            database.execute("BEGIN IMMEDIATE")
            row = database.execute(
                "SELECT x.status, x.claim_token, x.lease_until_ms, x.template_code, x.template_version, "
                "x.revision_id, e.snippet, e.snippet_sha256, e.revision_id AS "
                "evidence_revision_id FROM extraction_runs x JOIN evidence e ON e.id=? WHERE x.id=?",
                (evidence_id, run_id),
            ).fetchone()
            if (
                row is None or row["status"] != "running" or row["claim_token"] != claim_token
                or row["lease_until_ms"] <= _now_ms() or row["evidence_revision_id"] != row["revision_id"]
            ):
                raise BusinessStoreError("Candidate requires active claim and evidence from the same revision")
            _verify_evidence_checksum(row["snippet"], row["snippet_sha256"])
            fields_row = database.execute(
                "SELECT fields_json FROM template_versions WHERE code=? AND version=?",
                (row["template_code"], row["template_version"]),
            ).fetchone()
            if fields_row is None or field_code not in {
                field["code"] for field in json.loads(fields_row["fields_json"])
            }:
                raise BusinessStoreError("Candidate field is not in the frozen template")
            if value not in row["snippet"]:
                raise BusinessStoreError("Candidate value is absent from frozen source evidence")
            candidate = FieldCandidate(
                id=uuid.uuid4().hex, run_id=run_id, field_code=field_code,
                value=value, evidence_id=evidence_id, method=method, created_at_ms=_now_ms(),
            )
            database.execute(
                "INSERT INTO field_candidates VALUES (?, ?, ?, ?, ?, ?, ?)",
                (candidate.id, candidate.run_id, candidate.field_code, candidate.value,
                 candidate.evidence_id, candidate.method, candidate.created_at_ms),
            )
        return candidate

    def list_field_candidates(self, run_id: str) -> tuple[FieldCandidate, ...]:
        with closing(self._connect()) as database:
            rows = database.execute(
                "SELECT * FROM field_candidates WHERE run_id=? ORDER BY created_at_ms, id", (run_id,)
            ).fetchall()
        return tuple(FieldCandidate(**dict(row)) for row in rows)

    def finish_extraction(self, run_id: str, *, claim_token: str, complete_coverage: bool) -> ExtractionRun:
        """Generate blocking quality issues only after every source page was scanned."""
        with closing(self._connect()) as database, database:
            database.execute("BEGIN IMMEDIATE")
            row = database.execute("SELECT * FROM extraction_runs WHERE id=?", (run_id,)).fetchone()
            if (
                row is None or row["status"] != "running" or row["claim_token"] != claim_token
                or row["lease_until_ms"] <= _now_ms()
            ):
                raise BusinessStoreError("Extraction claim is no longer active")
            fields_row = database.execute(
                "SELECT fields_json FROM template_versions WHERE code=? AND version=?",
                (row["template_code"], row["template_version"]),
            ).fetchone()
            if fields_row is None:
                raise BusinessStoreError("Frozen template version is missing")
            fields = json.loads(fields_row["fields_json"])
            candidate_rows = database.execute(
                "SELECT field_code, value FROM field_candidates WHERE run_id=?", (run_id,)
            ).fetchall()
            values_by_field: dict[str, set[str]] = {}
            for candidate in candidate_rows:
                values_by_field.setdefault(candidate["field_code"], set()).add(candidate["value"])
            now = _now_ms()
            if not complete_coverage:
                database.execute(
                    "INSERT INTO quality_issues VALUES (?, ?, NULL, 'coverage_incomplete', 'blocking', 'open', ?)",
                    (uuid.uuid4().hex, run_id, now),
                )
            for field in fields:
                values = values_by_field.get(field["code"], set())
                issue_code = (
                    "required_missing" if complete_coverage and field["required"] and not values
                    else "conflicting_candidates" if len(values) > 1 else None
                )
                if issue_code is not None:
                    database.execute(
                        "INSERT INTO quality_issues VALUES (?, ?, ?, ?, 'blocking', 'open', ?)",
                        (uuid.uuid4().hex, run_id, field["code"], issue_code, now),
                    )
            database.execute(
                "UPDATE extraction_runs SET status='done', claim_token=NULL, lease_until_ms=NULL, "
                "updated_at_ms=? WHERE id=?", (now, run_id)
            )
        result = self.get_extraction(run_id)
        assert result is not None
        return result

    def fail_extraction(self, run_id: str, *, claim_token: str, error_code: str) -> ExtractionRun:
        if not error_code.strip():
            raise BusinessStoreError("Extraction failure code is required")
        with closing(self._connect()) as database, database:
            cursor = database.execute(
                "UPDATE extraction_runs SET status='failed', error_code=?, claim_token=NULL, "
                "lease_until_ms=NULL, updated_at_ms=? "
                "WHERE id=? AND status='running' AND claim_token=? AND lease_until_ms>?",
                (error_code, _now_ms(), run_id, claim_token, _now_ms()),
            )
            if cursor.rowcount != 1:
                raise BusinessStoreError("Extraction claim is no longer active")
        result = self.get_extraction(run_id)
        assert result is not None
        return result

    def list_quality_issues(self, run_id: str) -> tuple[QualityIssue, ...]:
        with closing(self._connect()) as database:
            rows = database.execute(
                "SELECT * FROM quality_issues WHERE run_id=? ORDER BY created_at_ms, id", (run_id,)
            ).fetchall()
        return tuple(QualityIssue(**dict(row)) for row in rows)

    @staticmethod
    def _review_source(source: str) -> ReviewSource:
        if source not in ("web", "skill", "api"):
            raise BusinessStoreError("Review source must be web, skill, or api")
        return source  # type: ignore[return-value]

    @staticmethod
    def _latest_decisions(database: sqlite3.Connection, run_id: str) -> dict[str, FieldDecision]:
        rows = database.execute(
            "SELECT * FROM field_decisions WHERE run_id=? ORDER BY rowid", (run_id,)
        ).fetchall()
        latest: dict[str, FieldDecision] = {}
        for row in rows:
            decision = FieldDecision(**dict(row))
            latest[decision.field_code] = decision
        return latest

    @staticmethod
    def _append_audit(
        database: sqlite3.Connection, *, run_id: str, action: str, target_id: str, source: ReviewSource,
        old_value: str | None, new_value: str, reason: str | None, created_at_ms: int,
    ) -> None:
        database.execute(
            "INSERT INTO audit_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (uuid.uuid4().hex, run_id, action, target_id, source, old_value, new_value, reason, created_at_ms),
        )

    def decide_field(
        self, run_id: str, *, field_code: str, value: str, evidence_id: str,
        source: str, reason: str | None = None,
    ) -> FieldDecision:
        reviewed_by = self._review_source(source)
        if not value.strip() or len(value) > 2000:
            raise BusinessStoreError("Review value must be 1-2000 characters")
        if reason is not None and len(reason) > 2000:
            raise BusinessStoreError("Review reason is too long")
        with closing(self._connect()) as database, database:
            database.execute("BEGIN IMMEDIATE")
            row = database.execute(
                "SELECT x.*, e.revision_id AS evidence_revision_id, e.snippet, e.snippet_sha256 FROM extraction_runs x "
                "JOIN evidence e ON e.id=? WHERE x.id=?", (evidence_id, run_id),
            ).fetchone()
            if row is None or row["status"] != "done" or row["evidence_revision_id"] != row["revision_id"]:
                raise BusinessStoreError("Review requires a completed run and evidence from the same revision")
            _verify_evidence_checksum(row["snippet"], row["snippet_sha256"])
            fields_row = database.execute(
                "SELECT fields_json FROM template_versions WHERE code=? AND version=?",
                (row["template_code"], row["template_version"]),
            ).fetchone()
            if fields_row is None or field_code not in {
                field["code"] for field in json.loads(fields_row["fields_json"])
            }:
                raise BusinessStoreError("Review field is not in the frozen template")
            accepted = database.execute(
                "SELECT 1 FROM field_candidates WHERE run_id=? AND field_code=? AND value=? AND evidence_id=?",
                (run_id, field_code, value, evidence_id),
            ).fetchone() is not None
            basis = "candidate_acceptance" if accepted else "manual_correction"
            if not accepted and not (reason and reason.strip()):
                raise BusinessStoreError("Manual correction requires a reason")
            previous = self._latest_decisions(database, run_id).get(field_code)
            now = _now_ms()
            decision = FieldDecision(
                id=uuid.uuid4().hex, run_id=run_id, field_code=field_code,
                previous_value=previous.value if previous else None, value=value, evidence_id=evidence_id,
                basis=basis, source=reviewed_by, reason=reason, created_at_ms=now,
            )
            database.execute(
                "INSERT INTO field_decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (decision.id, decision.run_id, decision.field_code, decision.previous_value, decision.value,
                 decision.evidence_id, decision.basis, decision.source, decision.reason, decision.created_at_ms),
            )
            self._append_audit(
                database, run_id=run_id, action="field_decided", target_id=decision.id,
                source=reviewed_by, old_value=decision.previous_value, new_value=value,
                reason=reason, created_at_ms=now,
            )
        return decision

    def list_field_decisions(self, run_id: str) -> tuple[FieldDecision, ...]:
        with closing(self._connect()) as database:
            rows = database.execute(
                "SELECT * FROM field_decisions WHERE run_id=? ORDER BY rowid", (run_id,)
            ).fetchall()
        return tuple(FieldDecision(**dict(row)) for row in rows)

    def resolve_issue(
        self, issue_id: str, *, status: str, source: str, reason: str
    ) -> IssueResolution:
        reviewed_by = self._review_source(source)
        if status not in ("resolved", "ignored"):
            raise BusinessStoreError("Issue status must be resolved or ignored")
        if not reason.strip() or len(reason) > 2000:
            raise BusinessStoreError("Issue resolution requires a reason")
        with closing(self._connect()) as database, database:
            database.execute("BEGIN IMMEDIATE")
            row = database.execute(
                "SELECT q.*, x.status AS run_status FROM quality_issues q "
                "JOIN extraction_runs x ON x.id=q.run_id WHERE q.id=?", (issue_id,),
            ).fetchone()
            if row is None or row["run_status"] != "done" or row["status"] != "open":
                raise BusinessStoreError("Issue is not open on a completed extraction")
            if row["code"] == "coverage_incomplete":
                raise BusinessStoreError("Incomplete source coverage cannot be waived")
            if row["code"] == "required_missing" and status == "ignored":
                raise BusinessStoreError("Required field absence cannot be ignored")
            if status == "resolved" and row["field_code"] not in self._latest_decisions(database, row["run_id"]):
                raise BusinessStoreError("Resolving a field issue requires a field decision")
            now = _now_ms()
            resolution = IssueResolution(
                id=uuid.uuid4().hex, issue_id=issue_id, run_id=row["run_id"],
                previous_status="open", status=status, source=reviewed_by,
                reason=reason, created_at_ms=now,
            )
            database.execute("UPDATE quality_issues SET status=? WHERE id=? AND status='open'", (status, issue_id))
            database.execute(
                "INSERT INTO issue_resolutions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (resolution.id, resolution.issue_id, resolution.run_id, resolution.previous_status,
                 resolution.status, resolution.source, resolution.reason, resolution.created_at_ms),
            )
            self._append_audit(
                database, run_id=row["run_id"], action="issue_resolved", target_id=resolution.id,
                source=reviewed_by, old_value="open", new_value=status, reason=reason, created_at_ms=now,
            )
        return resolution

    def list_issue_resolutions(self, run_id: str) -> tuple[IssueResolution, ...]:
        with closing(self._connect()) as database:
            rows = database.execute(
                "SELECT * FROM issue_resolutions WHERE run_id=? ORDER BY rowid", (run_id,)
            ).fetchall()
        return tuple(IssueResolution(**dict(row)) for row in rows)

    @staticmethod
    def _result_from_row(row: sqlite3.Row) -> ConfirmedResult:
        payload = dict(row)
        fields_json = payload.pop("fields_json")
        _verify_result_checksum(fields_json, payload["fields_sha256"])
        try:
            payload["fields"] = tuple(ConfirmedField(**field) for field in json.loads(fields_json))
        except (TypeError, ValueError) as exc:
            raise BusinessStoreError("Confirmed result payload is invalid") from exc
        return ConfirmedResult(**payload)

    def confirm_result(self, run_id: str, *, source: str) -> ConfirmedResult:
        reviewed_by = self._review_source(source)
        with closing(self._connect()) as database, database:
            database.execute("BEGIN IMMEDIATE")
            run = database.execute("SELECT * FROM extraction_runs WHERE id=?", (run_id,)).fetchone()
            if run is None or run["status"] != "done":
                raise BusinessStoreError("Only completed extraction can be confirmed")
            open_issue = database.execute(
                "SELECT 1 FROM quality_issues WHERE run_id=? AND status='open' LIMIT 1", (run_id,)
            ).fetchone()
            if open_issue is not None:
                raise BusinessStoreError("Blocking quality issues remain open")
            fields_row = database.execute(
                "SELECT fields_json FROM template_versions WHERE code=? AND version=?",
                (run["template_code"], run["template_version"]),
            ).fetchone()
            if fields_row is None:
                raise BusinessStoreError("Frozen template version is missing")
            latest = self._latest_decisions(database, run_id)
            for decision in latest.values():
                evidence = database.execute(
                    "SELECT revision_id, snippet, snippet_sha256 FROM evidence WHERE id=?", (decision.evidence_id,)
                ).fetchone()
                if evidence is None or evidence["revision_id"] != run["revision_id"]:
                    raise BusinessStoreError("Confirmed field evidence does not belong to the run revision")
                _verify_evidence_checksum(evidence["snippet"], evidence["snippet_sha256"])
            missing = [
                field["code"] for field in json.loads(fields_row["fields_json"])
                if field["required"] and field["code"] not in latest
            ]
            if missing:
                raise BusinessStoreError("Required fields need explicit review decisions")
            fields = tuple(
                ConfirmedField(
                    field_code=code, value=decision.value, evidence_id=decision.evidence_id,
                    decision_id=decision.id, basis=decision.basis,
                )
                for code, decision in sorted(latest.items())
            )
            fields_json = json.dumps([vars(field) for field in fields], ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"))
            fields_sha256 = hashlib.sha256(fields_json.encode("utf-8")).hexdigest()
            history = database.execute(
                "SELECT version, fields_json, fields_sha256 FROM confirmed_results "
                "WHERE run_id=? ORDER BY version DESC",
                (run_id,),
            ).fetchall()
            for historical in history:
                _verify_result_checksum(historical["fields_json"], historical["fields_sha256"])
            previous = history[0] if history else None
            if previous is not None and previous["fields_sha256"] == fields_sha256:
                raise BusinessStoreError("No review changes since the last confirmation")
            now = _now_ms()
            result = ConfirmedResult(
                id=uuid.uuid4().hex, run_id=run_id, version=previous["version"] + 1 if previous else 1,
                revision_id=run["revision_id"], template_code=run["template_code"],
                template_version=run["template_version"], fields=fields, fields_sha256=fields_sha256,
                source=reviewed_by, created_at_ms=now,
            )
            database.execute(
                "INSERT INTO confirmed_results VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (result.id, result.run_id, result.version, result.revision_id, result.template_code,
                 result.template_version, fields_json, result.fields_sha256, result.source, result.created_at_ms),
            )
            self._append_audit(
                database, run_id=run_id, action="result_confirmed", target_id=result.id,
                source=reviewed_by, old_value=previous["fields_sha256"] if previous else None,
                new_value=fields_sha256, reason=None, created_at_ms=now,
            )
        return result

    def get_confirmed_result(self, result_id: str) -> ConfirmedResult | None:
        with closing(self._connect()) as database:
            row = database.execute("SELECT * FROM confirmed_results WHERE id=?", (result_id,)).fetchone()
        return self._result_from_row(row) if row is not None else None

    def list_confirmed_results(self, run_id: str) -> tuple[ConfirmedResult, ...]:
        with closing(self._connect()) as database:
            rows = database.execute(
                "SELECT * FROM confirmed_results WHERE run_id=? ORDER BY version DESC", (run_id,)
            ).fetchall()
        return tuple(self._result_from_row(row) for row in rows)

    def list_audit_events(self, run_id: str) -> tuple[AuditEvent, ...]:
        with closing(self._connect()) as database:
            rows = database.execute(
                "SELECT * FROM audit_events WHERE run_id=? ORDER BY rowid", (run_id,)
            ).fetchall()
        return tuple(AuditEvent(**dict(row)) for row in rows)

    def audit_page(self, *, limit: int = 20, before: str | None = None) -> AuditPage:
        """Page immutable events by insertion order; newer concurrent events cannot shift older pages."""
        if not 1 <= limit <= 100:
            raise BusinessStoreError("Audit page limit must be between 1 and 100")
        with closing(self._connect()) as database:
            database.execute("BEGIN")
            cursor_rowid: int | None = None
            if before is not None:
                cursor = database.execute("SELECT rowid FROM audit_events WHERE id=?", (before,)).fetchone()
                if cursor is None:
                    raise BusinessStoreError("Audit cursor not found")
                cursor_rowid = int(cursor["rowid"])
            rows = database.execute(
                "SELECT a.*, r.revision_id, v.document_id, d.original_name AS document_name "
                "FROM audit_events a JOIN extraction_runs r ON r.id=a.run_id "
                "JOIN revisions v ON v.id=r.revision_id JOIN documents d ON d.id=v.document_id "
                "WHERE (? IS NULL OR a.rowid < ?) ORDER BY a.rowid DESC LIMIT ?",
                (cursor_rowid, cursor_rowid, limit + 1),
            ).fetchall()
            database.rollback()
        items = tuple(
            AuditRecord(
                event=AuditEvent(**{key: row[key] for key in (
                    "id", "run_id", "action", "target_id", "source", "old_value", "new_value", "reason", "created_at_ms"
                )}),
                document_id=row["document_id"], document_name=row["document_name"],
                revision_id=row["revision_id"],
            ) for row in rows[:limit]
        )
        return AuditPage(items=items, next_before=items[-1].event.id if len(rows) > limit else None)

    def create_document(
        self, upload: StoredUpload, *, original_name: str, template_code: str | None = None
    ) -> BusinessDocument:
        with closing(self._connect()) as database, database:
            database.execute("BEGIN IMMEDIATE")
            template_version = self._resolve_template_version(database, template_code)
            document = self._new_document(
                upload, original_name=original_name, template_code=template_code, template_version=template_version
            )
            self._insert_document(database, document)
        return document

    def create_document_with_task(
        self, upload: StoredUpload, *, original_name: str, requested_tier: Tier | None,
        template_code: str | None = None,
    ) -> tuple[BusinessDocument, IngestTask]:
        """Persist a document and recoverable initial task in one transaction."""
        document, task, _created = self._create_document_with_task(
            upload, original_name=original_name, requested_tier=requested_tier,
            template_code=template_code, request_key=None,
        )
        return document, task

    def create_or_reuse_document_with_task(
        self, upload: StoredUpload, *, original_name: str, requested_tier: Tier | None,
        template_code: str | None, request_key: str,
    ) -> tuple[BusinessDocument, IngestTask, bool]:
        """Use one durable request key for one upload intent, never content-wide deduplication."""
        if _REQUEST_KEY_RE.fullmatch(request_key) is None:
            raise BusinessStoreError("Invalid upload idempotency key")
        return self._create_document_with_task(
            upload, original_name=original_name, requested_tier=requested_tier,
            template_code=template_code, request_key=request_key,
        )

    def get_upload_request(self, request_key: str) -> tuple[BusinessDocument, IngestTask] | None:
        """Resolve a durable upload request after a client lost its accepted response."""
        if _REQUEST_KEY_RE.fullmatch(request_key) is None:
            raise BusinessStoreError("Invalid upload idempotency key")
        with closing(self._connect()) as database:
            previous = database.execute(
                "SELECT document_id, task_id FROM ingest_requests WHERE request_key=?", (request_key,)
            ).fetchone()
            if previous is None:
                return None
            document_row = database.execute(
                "SELECT * FROM documents WHERE id=?", (previous["document_id"],)
            ).fetchone()
            task_row = database.execute("SELECT * FROM tasks WHERE id=?", (previous["task_id"],)).fetchone()
            if document_row is None or task_row is None:
                raise BusinessStoreError("Idempotent upload record is incomplete")
            return BusinessDocument(**dict(document_row)), self._task_from_row(task_row)

    def _create_document_with_task(
        self, upload: StoredUpload, *, original_name: str, requested_tier: Tier | None,
        template_code: str | None, request_key: str | None,
    ) -> tuple[BusinessDocument, IngestTask, bool]:
        with closing(self._connect()) as database, database:
            database.execute("BEGIN IMMEDIATE")
            if request_key is not None:
                previous = database.execute(
                    "SELECT * FROM ingest_requests WHERE request_key=?", (request_key,)
                ).fetchone()
                if previous is not None:
                    if (
                        previous["original_name"] != original_name or previous["sha256"] != upload.sha256
                        or previous["size"] != upload.size or previous["requested_tier"] != requested_tier
                        or previous["template_code"] != template_code
                    ):
                        raise UploadRequestConflict("Idempotency key already belongs to a different upload")
                    document_row = database.execute(
                        "SELECT * FROM documents WHERE id=?", (previous["document_id"],)
                    ).fetchone()
                    task_row = database.execute("SELECT * FROM tasks WHERE id=?", (previous["task_id"],)).fetchone()
                    if document_row is None or task_row is None:
                        raise BusinessStoreError("Idempotent upload record is incomplete")
                    return BusinessDocument(**dict(document_row)), self._task_from_row(task_row), False
            template_version = self._resolve_template_version(database, template_code)
            document = self._new_document(
                upload, original_name=original_name, template_code=template_code, template_version=template_version
            )
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
            self._insert_document(database, document)
            database.execute(
                "INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (task.id, task.document_id, task.requested_tier, task.actual_tier, task.status, "[]", None, now, now,
                 None, None, 1, 0),
            )
            if request_key is not None:
                database.execute(
                    "INSERT INTO ingest_requests VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (request_key, document.id, task.id, original_name, upload.sha256, upload.size,
                     requested_tier, template_code),
                )
        return document, task, True

    @staticmethod
    def _resolve_template_version(database: sqlite3.Connection, code: str | None) -> int | None:
        if code is None:
            return None
        row = database.execute(
            "SELECT current_version FROM templates WHERE code=? AND enabled=1", (code,)
        ).fetchone()
        if row is None:
            raise BusinessStoreError("Selected template does not exist or is disabled")
        return int(row["current_version"])

    @staticmethod
    def _new_document(
        upload: StoredUpload, *, original_name: str, template_code: str | None, template_version: int | None
    ) -> BusinessDocument:
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
            template_code=template_code,
            template_version=template_version,
        )

    @staticmethod
    def _insert_document(database: sqlite3.Connection, document: BusinessDocument) -> None:
        database.execute(
            "INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                document.id,
                document.original_name,
                document.storage_key,
                document.sha256,
                document.size,
                document.created_at_ms,
                document.template_code,
                document.template_version,
            ),
        )

    def get_document(self, document_id: str) -> BusinessDocument | None:
        with closing(self._connect()) as database:
            row = database.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
        return BusinessDocument(**dict(row)) if row is not None else None

    def find_documents_with_revision(self, sha256: str, *, tier: Tier) -> tuple[tuple[BusinessDocument, str], ...]:
        """Map a Doclib index hit only to business documents with a completed matching-tier revision."""
        if not _SHA256_RE.fullmatch(sha256):
            return ()
        with closing(self._connect()) as database:
            rows = database.execute(
                "SELECT d.*, r.id AS revision_id FROM documents d "
                "JOIN revisions r ON r.document_id=d.id "
                "WHERE d.sha256=? AND r.sha256=? AND r.tier=? "
                "ORDER BY d.created_at_ms DESC, d.rowid DESC, r.created_at_ms DESC, r.rowid DESC",
                (sha256, sha256, tier),
            ).fetchall()
        found: list[tuple[BusinessDocument, str]] = []
        seen: set[str] = set()
        for row in rows:
            if row["id"] in seen:
                continue
            seen.add(row["id"])
            found.append((BusinessDocument(
                id=row["id"], original_name=row["original_name"], storage_key=row["storage_key"],
                sha256=row["sha256"], size=row["size"], created_at_ms=row["created_at_ms"],
                template_code=row["template_code"], template_version=row["template_version"],
            ), row["revision_id"]))
        return tuple(found)

    def list_documents(
        self, *, limit: int = 20, offset: int = 0, template_code: str | None = None,
        status: TaskStatus | None = None,
    ) -> tuple[tuple[tuple[BusinessDocument, IngestTask | None], ...], int]:
        """Page business documents with their latest ingest task, without user scoping."""
        if not 1 <= limit <= 100 or offset < 0:
            raise BusinessStoreError("Document page is out of range")
        filters: list[str] = []
        parameters: list[str] = []
        if template_code is not None:
            filters.append("d.template_code=?")
            parameters.append(template_code)
        if status is not None:
            filters.append("t.status=?")
            parameters.append(status)
        source = (
            " FROM documents d LEFT JOIN tasks t ON t.id=("
            "SELECT newest.id FROM tasks newest WHERE newest.document_id=d.id "
            "ORDER BY newest.created_at_ms DESC, newest.rowid DESC LIMIT 1)"
        )
        where = " WHERE " + " AND ".join(filters) if filters else ""
        with closing(self._connect()) as database:
            database.execute("BEGIN")
            total = int(database.execute("SELECT COUNT(*)" + source + where, parameters).fetchone()[0])
            rows = database.execute(
                "SELECT d.*, t.id AS task_id" + source + where + " ORDER BY d.created_at_ms DESC, d.rowid DESC "
                "LIMIT ? OFFSET ?", (*parameters, limit, offset),
            ).fetchall()
            items = []
            for row in rows:
                document = BusinessDocument(
                    id=row["id"], original_name=row["original_name"], storage_key=row["storage_key"],
                    sha256=row["sha256"], size=row["size"], created_at_ms=row["created_at_ms"],
                    template_code=row["template_code"], template_version=row["template_version"],
                )
                task_row = None
                if row["task_id"]:
                    task_row = database.execute("SELECT * FROM tasks WHERE id=?", (row["task_id"],)).fetchone()
                items.append((document, self._task_from_row(task_row) if task_row is not None else None))
        return tuple(items), total

    def get_task(self, task_id: str) -> IngestTask | None:
        with closing(self._connect()) as database:
            row = database.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return self._task_from_row(row) if row is not None else None

    def list_recoverable_task_ids(self, *, after_id: str = "", limit: int = 100) -> tuple[str, ...]:
        """Bounded keyset scan for work that must progress without an open browser."""
        if not 1 <= limit <= 1000:
            raise BusinessStoreError("Recovery scan limit must be between 1 and 1000")
        with closing(self._connect()) as database:
            rows = database.execute(
                "SELECT id FROM tasks WHERE id>? AND ("
                "status IN ('uploaded', 'submitting', 'submitted', 'cancel_requested') OR "
                "(status='failed' AND error_code='doclib_submission_failed')) "
                "ORDER BY id LIMIT ?",
                (after_id, limit),
            ).fetchall()
        return tuple(row["id"] for row in rows)

    def begin_task_submission(self, task_id: str) -> IngestTask:
        """Persist intent before calling Doclib; a stranded submitting task can be retried."""
        with closing(self._connect()) as database, database:
            database.execute("BEGIN IMMEDIATE")
            row = database.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                raise BusinessStoreError("Task not found")
            if row["status"] in ("uploaded", "failed"):
                new_force_attempt = row["status"] == "failed" and row["error_code"] in (
                    "doclib_parse_failed", "parse_coverage_incomplete", "parse_batch_invalid"
                )
                attempt = row["submission_attempt"] + int(new_force_attempt)
                force = bool(new_force_attempt or row["submission_force"])
                database.execute(
                    "UPDATE tasks SET status='submitting', submission_attempt=?, submission_force=?, updated_at_ms=? "
                    "WHERE id=?",
                    (attempt, int(force), _now_ms(), task_id),
                )
                row = database.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            assert row is not None
            return self._task_from_row(row)

    def request_task_cancel(self, task_id: str) -> IngestTask:
        """Fence completion and new submissions before the cross-DB release call."""
        with closing(self._connect()) as database, database:
            database.execute("BEGIN IMMEDIATE")
            row = database.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                raise BusinessStoreError("Task not found")
            if row["status"] in ("uploaded", "submitting", "submitted", "failed"):
                initial_effect = "not_submitted" if row["status"] == "uploaded" else None
                database.execute(
                    "UPDATE tasks SET status='cancel_requested', cancel_effect=?, updated_at_ms=? WHERE id=?",
                    (initial_effect, _now_ms(), task_id),
                )
                row = database.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            assert row is not None
            return self._task_from_row(row)

    def finish_task_cancel(self, task_id: str, release: ParseReleaseResponse) -> IngestTask:
        """Persist Doclib's first release facts; never turn an unknown result into stopped work."""
        if release.consumer_key != f"business:{task_id}":
            raise BusinessStoreError("Release result belongs to another task")
        with closing(self._connect()) as database, database:
            database.execute("BEGIN IMMEDIATE")
            row = database.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                raise BusinessStoreError("Task not found")
            if row["status"] == "cancel_requested":
                effect = (
                    "not_submitted" if row["cancel_effect"] == "not_submitted" and not release.results
                    else "queued_skipped" if release.results and all(item.disposition == "skipped" for item in release.results)
                    else "may_continue"
                )
                database.execute(
                    "UPDATE tasks SET status='cancelled', cancel_effect=?, cancel_results_json=?, updated_at_ms=? "
                    "WHERE id=? AND status='cancel_requested'",
                    (effect, release.model_dump_json(), _now_ms(), task_id),
                )
                row = database.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            assert row is not None
            return self._task_from_row(row)

    def mark_task_submitted(
        self, task_id: str, *, actual_tier: Tier, parse_ids: tuple[int, ...], submission_attempt: int
    ) -> IngestTask:
        if not parse_ids or any(parse_id < 1 for parse_id in parse_ids):
            raise BusinessStoreError("Doclib submission must expose parse IDs")
        with closing(self._connect()) as database, database:
            cursor = database.execute(
                "UPDATE tasks SET status='submitted', actual_tier=?, parse_ids_json=?, error_code=NULL, updated_at_ms=? "
                "WHERE id=? AND submission_attempt=? AND "
                "(status='submitting' OR (status='failed' AND error_code='doclib_submission_failed'))",
                (actual_tier, json.dumps(parse_ids), _now_ms(), task_id, submission_attempt),
            )
            if cursor.rowcount != 1:
                task = self.get_task(task_id)
                if task is not None and (
                    task.status in ("cancel_requested", "cancelled", "submitted", "done")
                    or task.submission_attempt != submission_attempt
                ):
                    return task
                raise BusinessStoreError("Task cannot be submitted from its current state")
        task = self.get_task(task_id)
        assert task is not None
        return task

    def mark_task_failed(
        self, task_id: str, *, error_code: str, expected_submission_attempt: int | None = None
    ) -> IngestTask:
        if not error_code.strip():
            raise BusinessStoreError("Failure code is required")
        with closing(self._connect()) as database, database:
            if expected_submission_attempt is None:
                cursor = database.execute(
                    "UPDATE tasks SET status='failed', error_code=?, updated_at_ms=? "
                    "WHERE id=? AND status IN ('uploaded', 'submitting', 'submitted', 'failed')",
                    (error_code, _now_ms(), task_id),
                )
            else:
                cursor = database.execute(
                    "UPDATE tasks SET status='failed', error_code=?, updated_at_ms=? "
                    "WHERE id=? AND status='submitting' AND submission_attempt=?",
                    (error_code, _now_ms(), task_id, expected_submission_attempt),
                )
            if cursor.rowcount != 1:
                task = self.get_task(task_id)
                if task is not None and (task.status in ("cancel_requested", "cancelled", "done", "submitted")
                                         or expected_submission_attempt is not None):
                    return task
                raise BusinessStoreError("Task cannot fail from its current state")
        task = self.get_task(task_id)
        assert task is not None
        return task

    def fail_submitted_task_if_current(
        self, task_id: str, *, error_code: str, parse_ids: tuple[int, ...], submission_attempt: int
    ) -> IngestTask:
        """A stale Doclib poll may not fail a newer submission generation."""
        if not error_code.strip():
            raise BusinessStoreError("Failure code is required")
        with closing(self._connect()) as database, database:
            database.execute(
                "UPDATE tasks SET status='failed', error_code=?, updated_at_ms=? "
                "WHERE id=? AND status='submitted' AND parse_ids_json=? AND submission_attempt=?",
                (error_code, _now_ms(), task_id, json.dumps(parse_ids), submission_attempt),
            )
        task = self.get_task(task_id)
        if task is None:
            raise BusinessStoreError("Task not found")
        return task

    @staticmethod
    def _task_from_row(row: sqlite3.Row) -> IngestTask:
        payload = dict(row)
        payload["parse_ids"] = tuple(json.loads(payload.pop("parse_ids_json")))
        payload["submission_force"] = bool(payload["submission_force"])
        return IngestTask(**payload)

    def add_completed_revision(
        self,
        document_id: str,
        *,
        parse: ParseInfo | tuple[ParseInfo, ...],
        producer_version: str,
        model_ref: str | None = None,
    ) -> ParseRevision:
        """Record an independently produced, completed parse revision."""
        first, parse_batches, page_range = self._completed_batches(parse, producer_version)
        with closing(self._connect()) as database, database:
            database.execute("BEGIN IMMEDIATE")
            return self._insert_completed_revision(
                database, document_id, first, parse_batches, page_range, producer_version, model_ref
            )

    def complete_task_with_revision(
        self,
        task_id: str,
        *,
        parse: ParseInfo | tuple[ParseInfo, ...],
        producer_version: str,
        submission_attempt: int | None = None,
    ) -> IngestTask:
        """Commit a submitted task and its revision together, or observe a concurrent terminal transition."""
        first, parse_batches, page_range = self._completed_batches(parse, producer_version)
        with closing(self._connect()) as database, database:
            database.execute("BEGIN IMMEDIATE")
            task_row = database.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if task_row is None:
                raise BusinessStoreError("Task not found")
            if task_row["status"] != "submitted" or (
                submission_attempt is not None and task_row["submission_attempt"] != submission_attempt
            ):
                return self._task_from_row(task_row)
            if first.tier != task_row["actual_tier"]:
                raise BusinessStoreError("Completed parse tier does not match the submitted task")
            expected_ids = set(json.loads(task_row["parse_ids_json"]))
            if not {batch.parse_id for batch in parse_batches} <= expected_ids:
                raise BusinessStoreError("Completed parse does not belong to the submitted task")
            revision = self._insert_completed_revision(
                database, task_row["document_id"], first, parse_batches, page_range, producer_version, None
            )
            self._enqueue_extraction_in_transaction(database, revision.id, automatic=True)
            database.execute(
                "UPDATE tasks SET status='done', updated_at_ms=? WHERE id=? AND status='submitted'",
                (_now_ms(), task_id),
            )
            completed = database.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            assert completed is not None
            return self._task_from_row(completed)

    @staticmethod
    def _completed_batches(
        parse: ParseInfo | tuple[ParseInfo, ...], producer_version: str
    ) -> tuple[ParseInfo, tuple[ParseBatch, ...], str]:
        parses = (parse,) if isinstance(parse, ParseInfo) else parse
        if not parses or any(item.status != "done" for item in parses):
            raise BusinessStoreError("Only completed Doclib parses can become evidence revisions")
        if not producer_version.strip():
            raise BusinessStoreError("Producer version is required")
        first = parses[0]
        pages: set[int] = set()
        batches: list[ParseBatch] = []
        for item in parses:
            if (item.sha256, item.short_id, item.tier) != (first.sha256, first.short_id, first.tier):
                raise BusinessStoreError("Parse batches do not share a document identity and tier")
            batch_pages = parse_page_range_set(item.page_range)
            if not batch_pages or pages & batch_pages or item.id < 1:
                raise BusinessStoreError("Parse batches overlap or have invalid page coverage")
            pages.update(batch_pages)
            batches.append(ParseBatch(item.id, item.page_range))
        batches.sort(key=lambda batch: min(parse_page_range_set(batch.page_range)))
        parse_batches = tuple(batches)
        page_range = format_page_range(pages)
        return first, parse_batches, page_range

    def _insert_completed_revision(
        self,
        database: sqlite3.Connection,
        document_id: str,
        first: ParseInfo,
        parse_batches: tuple[ParseBatch, ...],
        page_range: str,
        producer_version: str,
        model_ref: str | None,
    ) -> ParseRevision:
        document = database.execute("SELECT sha256 FROM documents WHERE id=?", (document_id,)).fetchone()
        if document is None:
            raise BusinessStoreError("Business document not found")
        if first.sha256 != document["sha256"]:
            raise BusinessStoreError("Doclib parse source does not match the business document")
        existing = database.execute(
            "SELECT * FROM revisions WHERE document_id=? AND doclib_parse_id=?", (document_id, first.id)
        ).fetchone()
        if existing is not None:
            revision = self._revision_from_row(existing)
            if (
                revision.sha256 != first.sha256
                or revision.short_id != first.short_id
                or revision.tier != first.tier
                or revision.parse_batches != parse_batches
                or revision.page_range != page_range
                or revision.producer_version != producer_version
                or revision.model_ref != model_ref
            ):
                raise BusinessStoreError("Existing parse revision has conflicting provenance")
            return revision
        revision = ParseRevision(
            id=uuid.uuid4().hex,
            document_id=document_id,
            doclib_parse_id=first.id,
            parse_batches=parse_batches,
            page_range=page_range,
            sha256=first.sha256,
            short_id=first.short_id,
            tier=first.tier,
            producer_version=producer_version,
            model_ref=model_ref,
            created_at_ms=_now_ms(),
        )
        database.execute(
            "INSERT INTO revisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                revision.id,
                revision.document_id,
                revision.doclib_parse_id,
                json.dumps([vars(batch) for batch in revision.parse_batches]),
                revision.page_range,
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
        return self._revision_from_row(row) if row is not None else None

    def list_revisions(self, document_id: str) -> tuple[ParseRevision, ...]:
        with closing(self._connect()) as database:
            rows = database.execute(
                "SELECT * FROM revisions WHERE document_id=? ORDER BY created_at_ms DESC, id DESC", (document_id,)
            ).fetchall()
        return tuple(self._revision_from_row(row) for row in rows)

    @staticmethod
    def _revision_from_row(row: sqlite3.Row) -> ParseRevision:
        payload = dict(row)
        payload["parse_batches"] = tuple(ParseBatch(**item) for item in json.loads(payload.pop("parse_batches_json")))
        return ParseRevision(**payload)

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
            if cursor.page_no not in parse_page_range_set(row["page_range"]):
                raise BusinessStoreError("Locator page does not belong to the parse revision")
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

    @staticmethod
    def _evidence_from_row(row: sqlite3.Row) -> EvidenceSnapshot:
        payload = dict(row)
        _verify_evidence_checksum(payload["snippet"], payload["snippet_sha256"])
        bbox_json = payload.pop("bbox_json")
        payload["bbox"] = tuple(json.loads(bbox_json)) if bbox_json is not None else None
        return EvidenceSnapshot(**payload)

    def get_evidence(self, evidence_id: str) -> EvidenceSnapshot | None:
        with closing(self._connect()) as database:
            row = database.execute(
                "SELECT e.*, r.document_id FROM evidence e JOIN revisions r ON r.id=e.revision_id WHERE e.id=?",
                (evidence_id,),
            ).fetchone()
        if row is None:
            return None
        return self._evidence_from_row(row)

    def list_evidence(self, revision_id: str) -> tuple[EvidenceSnapshot, ...]:
        with closing(self._connect()) as database:
            rows = database.execute(
                "SELECT e.*, r.document_id FROM evidence e JOIN revisions r ON r.id=e.revision_id "
                "WHERE e.revision_id=? ORDER BY e.created_at_ms DESC, e.id DESC",
                (revision_id,),
            ).fetchall()
        return tuple(self._evidence_from_row(row) for row in rows)


__all__ = ["BusinessStore", "BusinessStoreError"]
