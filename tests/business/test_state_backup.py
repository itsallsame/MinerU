"""Offline state copies are complete, checksum-bound and never overwrite live data."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from mineru.business.store import BusinessStore
from mineru.business.documents import StoredUpload
from scripts import business_state_backup as backup


def _state(root: Path) -> tuple[Path, Path, Path, Path]:
    business = root / "business"
    business.mkdir()
    store = BusinessStore(business / "business.sqlite3")
    store.initialize()
    doclib = root / "doclib"
    doclib.mkdir()
    (doclib / "parsed-result.bin").write_bytes(b"Doclib historical parse")
    shared = root / "shared"
    shared.mkdir()
    original = b"<h1>Business original</h1>"
    (shared / "opaque.html").write_bytes(original)
    store.create_or_reuse_document_with_task(
        StoredUpload(
            path=shared / "opaque.html", sha256=hashlib.sha256(original).hexdigest(), size=len(original), extension="html"
        ),
        original_name="notice.html",
        requested_tier=None,
        template_code=None,
        request_key="backup-replay-request-key",
    )
    release = root / "release.json"
    release.write_text(json.dumps({"schema": 4, "source_revision": "a" * 40}), encoding="utf-8")
    return business, doclib, shared, release


def test_backup_refuses_unshipped_older_release_schema(tmp_path: Path) -> None:
    business, doclib, shared, release = _state(tmp_path)
    release.write_text(json.dumps({"schema": 3, "source_revision": "a" * 40}))
    with pytest.raises(backup.BackupError, match="schema is unsupported"):
        backup.create_backup(
            business_dir=business,
            doclib_dir=doclib,
            shared_documents_dir=shared,
            release_manifest=release,
            output=tmp_path / "backup",
            check_stopped=lambda: None,
        )


def test_backup_refuses_prior_business_schema_without_migrating_it(tmp_path: Path) -> None:
    business, doclib, shared, release = _state(tmp_path)
    with closing(sqlite3.connect(business / "business.sqlite3")) as database, database:
        database.execute("PRAGMA user_version = 14")
    with pytest.raises(backup.BackupError, match="schema or integrity"):
        backup.create_backup(
            business_dir=business,
            doclib_dir=doclib,
            shared_documents_dir=shared,
            release_manifest=release,
            output=tmp_path / "backup",
            check_stopped=lambda: None,
        )
    with closing(sqlite3.connect(business / "business.sqlite3")) as database:
        assert database.execute("PRAGMA user_version").fetchone()[0] == 14


def test_backup_refuses_current_schema_missing_extraction_request_table(tmp_path: Path) -> None:
    business, doclib, shared, release = _state(tmp_path)
    with closing(sqlite3.connect(business / "business.sqlite3")) as database, database:
        database.execute("DROP TABLE extraction_requests")
    with pytest.raises(backup.BackupError, match="schema or integrity"):
        backup.create_backup(
            business_dir=business,
            doclib_dir=doclib,
            shared_documents_dir=shared,
            release_manifest=release,
            output=tmp_path / "backup",
            check_stopped=lambda: None,
        )


def test_backup_refuses_current_schema_missing_task_retry_request_table(tmp_path: Path) -> None:
    business, doclib, shared, release = _state(tmp_path)
    with closing(sqlite3.connect(business / "business.sqlite3")) as database, database:
        database.execute("DROP TABLE task_retry_requests")
    with pytest.raises(backup.BackupError, match="schema or integrity"):
        backup.create_backup(
            business_dir=business,
            doclib_dir=doclib,
            shared_documents_dir=shared,
            release_manifest=release,
            output=tmp_path / "backup",
            check_stopped=lambda: None,
        )


def test_backup_verifies_and_restores_only_into_new_directories(tmp_path: Path) -> None:
    business, doclib, shared, release = _state(tmp_path)
    prior = BusinessStore(business / "business.sqlite3").get_upload_request("backup-replay-request-key")
    assert prior is not None
    cancelled_intent = BusinessStore(business / "business.sqlite3").request_task_cancel(prior[1].id)
    assert cancelled_intent.status == "cancel_requested"
    output = tmp_path / "backup"
    stopped_checks = 0

    def stopped() -> None:
        nonlocal stopped_checks
        stopped_checks += 1

    record = backup.create_backup(
        business_dir=business,
        doclib_dir=doclib,
        shared_documents_dir=shared,
        release_manifest=release,
        output=output,
        check_stopped=stopped,
    )
    assert stopped_checks == 1 and record["business_schema"] == 15
    assert backup.verify_backup(output) == record
    assert (output / "COMPLETE").is_file()
    assert (output / "shared_documents" / "opaque.html").read_bytes() == b"<h1>Business original</h1>"

    restored = tmp_path / "restored"
    restored.mkdir()
    targets = {
        "business_dir": restored / "business",
        "doclib_dir": restored / "doclib",
        "shared_documents_dir": restored / "shared",
    }
    backup.restore_backup(backup=output, release_manifest=release, check_stopped=stopped, **targets)
    assert stopped_checks == 2
    assert (restored / "shared" / "opaque.html").read_bytes() == (shared / "opaque.html").read_bytes()
    assert (restored / "doclib" / "parsed-result.bin").read_bytes() == (doclib / "parsed-result.bin").read_bytes()
    with closing(sqlite3.connect(restored / "business" / "business.sqlite3")) as database:
        assert database.execute("PRAGMA user_version").fetchone()[0] == 15
        assert database.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    recovered_store = BusinessStore(restored / "business" / "business.sqlite3")
    recovered_store.initialize()
    assert recovered_store.get_task(prior[1].id) == cancelled_intent
    original = b"<h1>Business original</h1>"
    _document, _task, created = recovered_store.create_or_reuse_document_with_task(
        StoredUpload(
            path=restored / "shared" / "opaque.html",
            sha256=hashlib.sha256(original).hexdigest(),
            size=len(original),
            extension="html",
        ),
        original_name="notice.html",
        requested_tier=None,
        template_code=None,
        request_key="backup-replay-request-key",
    )
    assert created is False


def test_backup_never_overwrites_output_or_existing_restore_target(tmp_path: Path) -> None:
    business, doclib, shared, release = _state(tmp_path)
    output = tmp_path / "backup"
    output.mkdir()
    (output / "keep.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(backup.BackupError, match="new path"):
        backup.create_backup(
            business_dir=business,
            doclib_dir=doclib,
            shared_documents_dir=shared,
            release_manifest=release,
            output=output,
            check_stopped=lambda: None,
        )
    assert (output / "keep.txt").read_text() == "keep"

    fresh = tmp_path / "fresh"
    backup.create_backup(
        business_dir=business,
        doclib_dir=doclib,
        shared_documents_dir=shared,
        release_manifest=release,
        output=fresh,
        check_stopped=lambda: None,
    )
    destination = tmp_path / "restore-business"
    destination.mkdir()
    (destination / "keep.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(backup.BackupError, match="new path"):
        backup.restore_backup(
            backup=fresh,
            release_manifest=release,
            business_dir=destination,
            doclib_dir=tmp_path / "restore-doclib",
            shared_documents_dir=tmp_path / "restore-shared",
            check_stopped=lambda: None,
        )
    assert (destination / "keep.txt").read_text() == "keep"
    assert not (tmp_path / "restore-doclib").exists()


def test_restore_requires_exact_selected_release_before_writing(tmp_path: Path) -> None:
    business, doclib, shared, release = _state(tmp_path)
    output = tmp_path / "backup"
    backup.create_backup(
        business_dir=business,
        doclib_dir=doclib,
        shared_documents_dir=shared,
        release_manifest=release,
        output=output,
        check_stopped=lambda: None,
    )
    other_release = tmp_path / "other-release.json"
    other_release.write_text(json.dumps({"schema": 4, "source_revision": "b" * 40}), encoding="utf-8")
    destinations = {
        "business_dir": tmp_path / "restored-business",
        "doclib_dir": tmp_path / "restored-doclib",
        "shared_documents_dir": tmp_path / "restored-shared",
    }

    def should_not_check_services() -> None:
        pytest.fail("A wrong release must be rejected before the stop check or any write")

    with pytest.raises(backup.BackupError, match="differs from the backup release"):
        backup.restore_backup(
            backup=output, release_manifest=other_release, check_stopped=should_not_check_services, **destinations
        )
    assert not any(path.exists() for path in destinations.values())

    missing_release = tmp_path / "missing-release.json"
    with pytest.raises(backup.BackupError, match="existing real file"):
        backup.restore_backup(
            backup=output, release_manifest=missing_release, check_stopped=should_not_check_services, **destinations
        )
    assert not any(path.exists() for path in destinations.values())


def test_tamper_or_incomplete_backup_cannot_be_restored(tmp_path: Path) -> None:
    business, doclib, shared, release = _state(tmp_path)
    output = tmp_path / "backup"
    backup.create_backup(
        business_dir=business,
        doclib_dir=doclib,
        shared_documents_dir=shared,
        release_manifest=release,
        output=output,
        check_stopped=lambda: None,
    )
    source = output / "shared_documents" / "opaque.html"
    source.write_bytes(b"changed")
    with pytest.raises(backup.BackupError, match="checksum mismatch"):
        backup.verify_backup(output)
    with pytest.raises(backup.BackupError, match="checksum mismatch"):
        backup.restore_backup(
            backup=output,
            release_manifest=release,
            business_dir=tmp_path / "restored-business",
            doclib_dir=tmp_path / "restored-doclib",
            shared_documents_dir=tmp_path / "restored-shared",
            check_stopped=lambda: None,
        )
    assert not (tmp_path / "restored-business").exists()
    source.write_bytes(b"<h1>Business original</h1>")
    (output / "COMPLETE").unlink()
    with pytest.raises(backup.BackupError, match="completion marker"):
        backup.verify_backup(output)


def test_malformed_completed_manifest_is_rejected_before_restore(tmp_path: Path) -> None:
    business, doclib, shared, release = _state(tmp_path)
    output = tmp_path / "backup"
    backup.create_backup(
        business_dir=business,
        doclib_dir=doclib,
        shared_documents_dir=shared,
        release_manifest=release,
        output=output,
        check_stopped=lambda: None,
    )
    manifest = output / "manifest.json"
    record = json.loads(manifest.read_text(encoding="utf-8"))
    record["files"] = None
    manifest.write_text(json.dumps(record), encoding="utf-8")
    (output / "COMPLETE").write_text(backup._digest(manifest) + "\n", encoding="ascii")
    with pytest.raises(backup.BackupError, match="unsupported identity"):
        backup.verify_backup(output)
    with pytest.raises(backup.BackupError, match="unsupported identity"):
        backup.restore_backup(
            backup=output,
            release_manifest=release,
            business_dir=tmp_path / "restored-business",
            doclib_dir=tmp_path / "restored-doclib",
            shared_documents_dir=tmp_path / "restored-shared",
            check_stopped=lambda: None,
        )
    assert not (tmp_path / "restored-business").exists()


def test_backup_refuses_symlinks_overlap_old_schema_and_running_services(tmp_path: Path) -> None:
    business, doclib, shared, release = _state(tmp_path)
    (shared / "alias.html").symlink_to(shared / "opaque.html")
    with pytest.raises(backup.BackupError, match="symlink"):
        backup.create_backup(
            business_dir=business,
            doclib_dir=doclib,
            shared_documents_dir=shared,
            release_manifest=release,
            output=tmp_path / "backup",
            check_stopped=lambda: None,
        )
    assert not (tmp_path / "backup").exists()
    (shared / "alias.html").unlink()
    with pytest.raises(backup.BackupError, match="overlap"):
        backup.create_backup(
            business_dir=business,
            doclib_dir=doclib,
            shared_documents_dir=shared,
            release_manifest=release,
            output=shared / "nested",
            check_stopped=lambda: None,
        )

    def running() -> None:
        raise backup.BackupError("Stop services")

    with pytest.raises(backup.BackupError, match="Stop services"):
        backup.create_backup(
            business_dir=business,
            doclib_dir=doclib,
            shared_documents_dir=shared,
            release_manifest=release,
            output=tmp_path / "backup",
            check_stopped=running,
        )
    assert not (tmp_path / "backup").exists()
    with closing(sqlite3.connect(business / "business.sqlite3")) as database, database:
        database.execute("PRAGMA user_version = 7")
    with pytest.raises(backup.BackupError, match="schema or integrity"):
        backup.create_backup(
            business_dir=business,
            doclib_dir=doclib,
            shared_documents_dir=shared,
            release_manifest=release,
            output=tmp_path / "backup",
            check_stopped=lambda: None,
        )
    assert not (tmp_path / "backup").exists()


def test_compose_stop_check_fails_closed_on_running_or_unavailable_docker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compose = tmp_path / "compose.yaml"
    compose.write_text("services: {}", encoding="utf-8")
    monkeypatch.setattr(
        backup.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            stdout='[{"Service":"business-api","State":"running"}]',
        ),
    )
    with pytest.raises(backup.BackupError, match="Stop business-api"):
        backup._assert_services_stopped(compose)

    monkeypatch.setattr(
        backup.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            stdout='[{"Service":"business-api","State":"exited"},{"Service":"doclib-worker","State":"exited"}]',
        ),
    )
    backup._assert_services_stopped(compose)

    def unavailable(*_args: object, **_kwargs: object) -> None:
        raise FileNotFoundError("docker")

    monkeypatch.setattr(backup.subprocess, "run", unavailable)
    with pytest.raises(backup.BackupError, match="Cannot verify"):
        backup._assert_services_stopped(compose)


def test_stop_check_rejects_other_running_containers_with_writable_state_mounts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    business, doclib, shared, _release = _state(tmp_path)
    compose = tmp_path / "compose.yaml"
    compose.write_text("services: {}", encoding="utf-8")
    mounts = [{"Type": "bind", "Source": str(business.parent), "RW": True}]

    def docker(command: list[str], **_kwargs: object) -> SimpleNamespace:
        if command[:2] == ["docker", "compose"]:
            return SimpleNamespace(stdout='[{"Service":"business-api","State":"exited"}]')
        if command == ["docker", "ps", "--quiet"]:
            return SimpleNamespace(stdout="abc123\n")
        if command == ["docker", "container", "inspect", "abc123"]:
            return SimpleNamespace(stdout=json.dumps([
                {"Id": "abc123456", "State": {"Running": True}, "Mounts": mounts},
            ]))
        raise AssertionError(command)

    monkeypatch.setattr(backup.subprocess, "run", docker)
    with pytest.raises(backup.BackupError, match="writable state mount"):
        backup._assert_services_stopped(compose, protected_dirs=(business, doclib, shared))
    mounts[0] = {"Type": "bind", "Source": str(business), "RW": False}
    backup._assert_services_stopped(compose, protected_dirs=(business, doclib, shared))
    mounts[0] = {"Type": "bind", "Source": str(tmp_path / "unrelated"), "RW": True}
    backup._assert_services_stopped(compose, protected_dirs=(business, doclib, shared))

    mounts[0] = {"Type": "bind", "Source": str(tmp_path), "RW": True}
    with pytest.raises(backup.BackupError, match="writable state mount"):
        backup._assert_services_stopped(compose, protected_dirs=(tmp_path / "new-restore-target",))


def test_stop_check_fails_closed_if_global_container_inspection_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    compose = tmp_path / "compose.yaml"
    compose.write_text("services: {}", encoding="utf-8")

    def docker(command: list[str], **_kwargs: object) -> SimpleNamespace:
        if command[:2] == ["docker", "compose"]:
            return SimpleNamespace(stdout="")
        if command == ["docker", "ps", "--quiet"]:
            return SimpleNamespace(stdout="abc123\n")
        raise FileNotFoundError("docker inspect unavailable")

    monkeypatch.setattr(backup.subprocess, "run", docker)
    with pytest.raises(backup.BackupError, match="Cannot inspect all running Docker container mounts"):
        backup._assert_services_stopped(compose, protected_dirs=(tmp_path / "data",))
