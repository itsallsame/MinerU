"""Business identity and frozen evidence survive process boundaries."""

from __future__ import annotations

import hashlib
import io
import sqlite3
import stat
from contextlib import closing
from pathlib import Path

import pytest

from mineru.business.documents import ImmutableUploadStore
from mineru.business.store import BusinessStore, BusinessStoreError
from mineru.doclib.types import ParseInfo


def _done_parse(sha256: str, *, parse_id: int, tier: str = "flash") -> ParseInfo:
    return ParseInfo(
        id=parse_id,
        sha256=sha256,
        short_id=sha256[:12],
        tier=tier,
        page_range="1",
        status="done",
        privacy="local",
        created_at=1,
        updated_at=2,
        done_at=2,
    )


def _store(tmp_path: Path) -> tuple[BusinessStore, ImmutableUploadStore]:
    shared = tmp_path / "shared"
    shared.mkdir()
    database_dir = tmp_path / "business"
    database_dir.mkdir()
    business = BusinessStore(database_dir / "business.sqlite3")
    business.initialize()
    return business, ImmutableUploadStore(shared, max_bytes=1024)


def test_business_ids_are_distinct_from_doclib_content_identity(tmp_path: Path) -> None:
    business, uploads = _store(tmp_path)
    payload = b"<h1>Same content, two business documents</h1>"
    first = business.create_document(uploads.store(io.BytesIO(payload), filename="a.html"), original_name="a.html")
    second = business.create_document(uploads.store(io.BytesIO(payload), filename="b.html"), original_name="b.html")

    assert first.id != second.id
    assert first.storage_key != second.storage_key
    assert first.sha256 == second.sha256 == hashlib.sha256(payload).hexdigest()
    assert business.get_document(first.id) == first

    reopened = BusinessStore(tmp_path / "business" / "business.sqlite3")
    reopened.initialize()
    assert reopened.get_document(second.id) == second


def test_database_creation_is_explicit_and_private(tmp_path: Path) -> None:
    database_path = tmp_path / "business.sqlite3"
    business = BusinessStore(database_path)
    with pytest.raises(BusinessStoreError, match="explicitly initialized"):
        business.get_document("unknown")
    assert not database_path.exists()
    business.initialize()
    assert stat.S_IMODE(database_path.stat().st_mode) == 0o600
    alias = tmp_path / "alias.sqlite3"
    alias.symlink_to(database_path)
    with pytest.raises(BusinessStoreError, match="symbolic link"):
        BusinessStore(alias).initialize()


def test_completed_revision_and_frozen_evidence_survive_reparse_and_restart(tmp_path: Path) -> None:
    business, uploads = _store(tmp_path)
    upload = uploads.store(io.BytesIO(b"<h1>Original evidence</h1>"), filename="report.html")
    document = business.create_document(upload, original_name="report.html")
    first = business.add_completed_revision(
        document.id, parse=_done_parse(upload.sha256, parse_id=17), producer_version="4.0.6"
    )
    assert (
        business.add_completed_revision(document.id, parse=_done_parse(upload.sha256, parse_id=17), producer_version="4.0.6")
        == first
    )
    with pytest.raises(BusinessStoreError, match="conflicting provenance"):
        business.add_completed_revision(document.id, parse=_done_parse(upload.sha256, parse_id=17), producer_version="4.0.7")
    locator = f"doc:{upload.sha256[:12]}/tier:flash/page:1/block:1"
    evidence = business.capture_evidence(first.id, locator=locator, snippet="Original evidence", bbox=(1.0, 2.0, 3.0, 4.0))
    second = business.add_completed_revision(
        document.id, parse=_done_parse(upload.sha256, parse_id=18), producer_version="4.0.7"
    )

    assert first.id != second.id
    assert evidence.revision_id == first.id
    assert evidence.snippet_sha256 == hashlib.sha256(b"Original evidence").hexdigest()
    assert business.get_evidence("unknown") is None

    reopened = BusinessStore(tmp_path / "business" / "business.sqlite3")
    assert reopened.get_revision(first.id) == first
    assert reopened.get_revision(second.id) == second
    assert reopened.get_evidence(evidence.id) == evidence
    assert {record.id for record in reopened.list_revisions(document.id)} == {first.id, second.id}
    assert reopened.list_evidence(first.id) == (evidence,)
    assert reopened.list_evidence(second.id) == ()


def test_revision_and_locator_must_match_document_identity(tmp_path: Path) -> None:
    business, uploads = _store(tmp_path)
    upload = uploads.store(io.BytesIO(b"<h1>Source</h1>"), filename="report.html")
    document = business.create_document(upload, original_name="report.html")

    with pytest.raises(BusinessStoreError, match="does not match"):
        business.add_completed_revision(document.id, parse=_done_parse("0" * 64, parse_id=1), producer_version="4.0.6")
    queued = _done_parse(upload.sha256, parse_id=2).model_copy(update={"status": "pending"})
    with pytest.raises(BusinessStoreError, match="completed"):
        business.add_completed_revision(document.id, parse=queued, producer_version="4.0.6")
    revision = business.add_completed_revision(
        document.id, parse=_done_parse(upload.sha256, parse_id=3), producer_version="4.0.6"
    )
    with pytest.raises(BusinessStoreError, match="does not belong"):
        business.capture_evidence(revision.id, locator="doc:deadbeef/tier:flash/page:1", snippet="Source")
    with pytest.raises(BusinessStoreError, match="does not belong"):
        business.capture_evidence(
            revision.id,
            locator=f"doc:{upload.sha256[:12]}/tier:basic/page:1",
            snippet="Source",
        )
    with pytest.raises(BusinessStoreError, match="bbox"):
        business.capture_evidence(
            revision.id,
            locator=f"doc:{upload.sha256[:12]}/tier:flash/page:1",
            snippet="Source",
            bbox=(3.0, 2.0, 1.0, 4.0),
        )
    assert business.get_revision(revision.id) == revision


def test_existing_unknown_database_is_not_modified(tmp_path: Path) -> None:
    database_path = tmp_path / "existing.sqlite3"
    with closing(sqlite3.connect(database_path)) as database, database:
        database.execute("CREATE TABLE user_data (secret TEXT NOT NULL)")
        database.execute("INSERT INTO user_data VALUES ('leave me alone')")

    with pytest.raises(BusinessStoreError, match="unknown database"):
        BusinessStore(database_path).initialize()
    with closing(sqlite3.connect(database_path)) as database:
        assert database.execute("SELECT secret FROM user_data").fetchone()[0] == "leave me alone"


def test_claimed_schema_version_must_have_expected_tables(tmp_path: Path) -> None:
    database_path = tmp_path / "spoofed.sqlite3"
    with closing(sqlite3.connect(database_path)) as database, database:
        database.execute("PRAGMA user_version = 6")
        database.execute("CREATE TABLE user_data (secret TEXT NOT NULL)")
    with pytest.raises(BusinessStoreError, match="does not match"):
        BusinessStore(database_path).initialize()


def test_previous_prototype_schema_is_refused_without_migration(tmp_path: Path) -> None:
    database_path = tmp_path / "old-prototype.sqlite3"
    with closing(sqlite3.connect(database_path)) as database, database:
        database.execute("PRAGMA user_version = 5")
        database.execute("CREATE TABLE old_business_data (marker TEXT NOT NULL)")
        database.execute("INSERT INTO old_business_data VALUES ('preserve')")
    with pytest.raises(BusinessStoreError, match="Unsupported business schema version: 5"):
        BusinessStore(database_path).initialize()
    with closing(sqlite3.connect(database_path)) as database:
        assert database.execute("SELECT marker FROM old_business_data").fetchone()[0] == "preserve"
