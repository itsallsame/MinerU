"""Create, verify and restore a quiesced offline business state without overwriting data."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
from contextlib import closing
from pathlib import Path, PurePosixPath
from typing import Any, Callable

DATA_NAMES = ("business", "doclib", "shared_documents")


class BackupError(ValueError):
    """A backup or restore prerequisite was not met."""


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sync_file(path: Path) -> None:
    with path.open("rb") as stream:
        os.fsync(stream.fileno())


def _sync_directory(path: Path) -> None:
    flags = os.O_RDONLY | (os.O_DIRECTORY if hasattr(os, "O_DIRECTORY") else 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _real_directory(path: Path, label: str) -> Path:
    if path.is_symlink() or not path.is_dir():
        raise BackupError(f"{label} must be an existing real directory")
    return path.resolve()


def _separate(paths: dict[str, Path]) -> None:
    values = list(paths.items())
    for index, (first_name, first) in enumerate(values):
        for second_name, second in values[index + 1 :]:
            if first.is_relative_to(second) or second.is_relative_to(first):
                raise BackupError(f"Paths overlap: {first_name} and {second_name}")


def _walk_error(error: OSError) -> None:
    raise BackupError("Cannot inspect the complete state tree") from error


def _inventory(root: Path) -> tuple[list[str], dict[str, dict[str, Any]]]:
    directories: list[str] = []
    files: dict[str, dict[str, Any]] = {}
    for current, names, filenames in os.walk(root, topdown=True, followlinks=False, onerror=_walk_error):
        base = Path(current)
        for name in sorted(names):
            path = base / name
            if not stat.S_ISDIR(path.lstat().st_mode):
                raise BackupError("State tree contains a symlink or non-directory entry")
            directories.append(path.relative_to(root).as_posix())
        for name in sorted(filenames):
            path = base / name
            metadata = path.lstat()
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise BackupError("State tree contains a symlink, hard link or special file")
            files[path.relative_to(root).as_posix()] = {"size": metadata.st_size, "sha256": _digest(path)}
    return sorted(directories), dict(sorted(files.items()))


def _copy_tree(source: Path, target: Path) -> None:
    target.mkdir(mode=0o700)
    destinations: list[Path] = []
    for current, names, filenames in os.walk(source, topdown=True, followlinks=False, onerror=_walk_error):
        base = Path(current)
        destination = target / base.relative_to(source)
        if destination != target:
            destination.mkdir(mode=0o700)
        destinations.append(destination)
        for name in sorted(names):
            path = base / name
            if not stat.S_ISDIR(path.lstat().st_mode):
                raise BackupError("State tree contains a symlink or non-directory entry")
        for name in sorted(filenames):
            path = base / name
            metadata = path.lstat()
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise BackupError("State tree contains a symlink, hard link or special file")
            output = destination / name
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            with path.open("rb") as stream:
                descriptor = os.open(output, flags, 0o600)
                with os.fdopen(descriptor, "wb") as target_stream:
                    shutil.copyfileobj(stream, target_stream, length=1024 * 1024)
                    target_stream.flush()
                    os.fsync(target_stream.fileno())
            shutil.copystat(path, output, follow_symlinks=False)
    for destination in reversed(destinations):
        source_dir = source / destination.relative_to(target)
        shutil.copystat(source_dir, destination, follow_symlinks=False)
        _sync_directory(destination)


def _verify_business_database(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise BackupError("Business SQLite database is missing")
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as database:
            version = database.execute("PRAGMA user_version").fetchone()[0]
            result = database.execute("PRAGMA quick_check").fetchone()[0]
            requests = database.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='ingest_requests'"
            ).fetchone()
    except sqlite3.Error as exc:
        raise BackupError("Business SQLite integrity check failed") from exc
    if version != 11 or result != "ok" or requests is None:
        raise BackupError("Business SQLite schema or integrity check failed")


def _assert_services_stopped(compose_file: Path, env_file: Path | None = None) -> None:
    if compose_file.is_symlink() or not compose_file.is_file():
        raise BackupError("Compose file must be an existing real file")
    command = ["docker", "compose", "--file", str(compose_file)]
    if env_file is not None:
        if env_file.is_symlink() or not env_file.is_file():
            raise BackupError("Compose env file must be an existing real file")
        command.extend(("--env-file", str(env_file)))
    command.extend(("ps", "--all", "--format", "json", "business-api", "doclib-worker"))
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise BackupError("Cannot verify that both business services are stopped") from exc
    raw = result.stdout.strip()
    if not raw:
        return
    try:
        data = json.loads(raw)
    except ValueError:
        try:
            data = [json.loads(line) for line in raw.splitlines()]
        except ValueError as exc:
            raise BackupError("Cannot parse Docker Compose service states") from exc
    entries = data if isinstance(data, list) else [data]
    if any(not isinstance(entry, dict) or entry.get("State") not in ("exited", "created", "dead") for entry in entries):
        raise BackupError("Stop business-api and doclib-worker before copying state")


def _valid_relative(name: str) -> bool:
    if not isinstance(name, str) or not name or "\\" in name:
        return False
    path = PurePosixPath(name)
    return not path.is_absolute() and all(part not in ("", ".", "..") for part in path.parts)


def create_backup(
    *,
    business_dir: Path,
    doclib_dir: Path,
    shared_documents_dir: Path,
    release_manifest: Path,
    output: Path,
    check_stopped: Callable[[], None],
) -> dict[str, Any]:
    """Reserve a new output directory; write COMPLETE only after byte-for-byte verification."""
    sources = {
        "business": _real_directory(business_dir, "Business data"),
        "doclib": _real_directory(doclib_dir, "Doclib data"),
        "shared_documents": _real_directory(shared_documents_dir, "Shared documents"),
    }
    destination = output.resolve()
    _separate({**sources, "backup": destination})
    if output.exists() or output.is_symlink() or not output.parent.is_dir():
        raise BackupError("Backup output must be a new path under an existing directory")
    if release_manifest.is_symlink() or not release_manifest.is_file():
        raise BackupError("Release manifest must be an existing real file")
    if any(release_manifest.resolve().is_relative_to(source) for source in sources.values()):
        raise BackupError("Release manifest must be outside writable data directories")
    try:
        release = json.loads(release_manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BackupError("Release manifest is unreadable") from exc
    if not isinstance(release, dict) or release.get("schema") != 4:
        raise BackupError("Release manifest schema is unsupported")
    check_stopped()
    _verify_business_database(sources["business"] / "business.sqlite3")
    original = {name: _inventory(source) for name, source in sources.items()}
    output.mkdir(mode=0o700)
    for name, source in sources.items():
        _copy_tree(source, output / name)
    shutil.copy2(release_manifest, output / "release.json", follow_symlinks=False)
    _sync_file(output / "release.json")
    copied = {name: _inventory(output / name) for name in DATA_NAMES}
    if copied != original or _digest(output / "release.json") != _digest(release_manifest):
        raise BackupError("Source changed during backup or copied bytes differ; incomplete backup retained")
    _verify_business_database(output / "business" / "business.sqlite3")
    record = {
        "schema": 1,
        "business_schema": 11,
        "release_sha256": _digest(output / "release.json"),
        "directories": {name: directories for name, (directories, _files) in copied.items()},
        "files": {name: files for name, (_directories, files) in copied.items()},
    }
    manifest = output / "manifest.json"
    manifest.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _sync_file(manifest)
    marker = output / "COMPLETE"
    marker.write_text(_digest(manifest) + "\n", encoding="ascii")
    _sync_file(marker)
    _sync_directory(output)
    return record


def verify_backup(backup: Path) -> dict[str, Any]:
    root = _real_directory(backup, "Backup")
    manifest, marker, release = (root / name for name in ("manifest.json", "COMPLETE", "release.json"))
    for path in (manifest, marker, release):
        if path.is_symlink() or not path.is_file():
            raise BackupError("Backup manifest, completion marker or release is missing")
    if marker.read_text(encoding="ascii") != _digest(manifest) + "\n":
        raise BackupError("Backup completion marker does not match its manifest")
    try:
        record = json.loads(manifest.read_text(encoding="utf-8"))
        release_record = json.loads(release.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BackupError("Backup manifest or release is unreadable") from exc
    if not isinstance(record, dict) or record.get("schema") != 1 or record.get("business_schema") != 11:
        raise BackupError("Backup manifest has an unsupported identity")
    if not isinstance(release_record, dict) or release_record.get("schema") != 4:
        raise BackupError("Backup release schema is unsupported")
    directories_by_name, files_by_name = record.get("directories"), record.get("files")
    if (
        not isinstance(directories_by_name, dict)
        or not isinstance(files_by_name, dict)
        or set(directories_by_name) != set(DATA_NAMES)
        or set(files_by_name) != set(DATA_NAMES)
        or record.get("release_sha256") != _digest(release)
    ):
        raise BackupError("Backup manifest has an unsupported identity")
    if {path.name for path in root.iterdir()} != {*DATA_NAMES, "manifest.json", "COMPLETE", "release.json"}:
        raise BackupError("Backup contains unrecorded top-level entries")
    for name in DATA_NAMES:
        if not isinstance(directories_by_name[name], list) or any(
            not _valid_relative(item) for item in directories_by_name[name]
        ):
            raise BackupError("Backup directory path is unsafe")
        if not isinstance(files_by_name[name], dict) or any(not _valid_relative(item) for item in files_by_name[name]):
            raise BackupError("Backup file path is unsafe")
        directories, files = _inventory(_real_directory(root / name, name))
        if directories != directories_by_name[name] or files != files_by_name[name]:
            raise BackupError(f"Backup {name} inventory or checksum mismatch")
    _verify_business_database(root / "business" / "business.sqlite3")
    return record


def restore_backup(
    *,
    backup: Path,
    release_manifest: Path,
    business_dir: Path,
    doclib_dir: Path,
    shared_documents_dir: Path,
    check_stopped: Callable[[], None],
) -> None:
    """Restore only to three new paths; never overwrite or remove existing data."""
    record = verify_backup(backup)
    if release_manifest.is_symlink() or not release_manifest.is_file():
        raise BackupError("Selected release manifest must be an existing real file")
    if _digest(release_manifest) != record["release_sha256"]:
        raise BackupError("Selected release manifest differs from the backup release")
    targets = {"business": business_dir, "doclib": doclib_dir, "shared_documents": shared_documents_dir}
    resolved = {name: path.resolve() for name, path in targets.items()}
    _separate({**resolved, "backup": backup.resolve()})
    for name, path in targets.items():
        if path.exists() or path.is_symlink() or not path.parent.is_dir():
            raise BackupError(f"Restored {name} destination must be a new path under an existing directory")
    check_stopped()
    for name, path in targets.items():
        _copy_tree(backup / name, path)
    for name, path in targets.items():
        if _inventory(path) != (record["directories"][name], record["files"][name]):
            raise BackupError(f"Restored {name} differs from the verified backup")
    _verify_business_database(business_dir / "business.sqlite3")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for action in ("create", "restore"):
        command = commands.add_parser(action)
        command.add_argument("--business-dir", type=Path, required=True)
        command.add_argument("--doclib-dir", type=Path, required=True)
        command.add_argument("--shared-documents-dir", type=Path, required=True)
        command.add_argument("--compose-file", type=Path, required=True)
        command.add_argument("--env-file", type=Path)
        if action == "create":
            command.add_argument("--release-manifest", type=Path, required=True)
            command.add_argument("--output", type=Path, required=True)
        else:
            command.add_argument("--backup", type=Path, required=True)
            command.add_argument("--release-manifest", type=Path, required=True)
    commands.add_parser("verify").add_argument("--backup", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "verify":
            verify_backup(args.backup)
            print("Offline state backup verified; this does not prove target restore or model compatibility")
        elif args.command == "create":
            create_backup(
                business_dir=args.business_dir,
                doclib_dir=args.doclib_dir,
                shared_documents_dir=args.shared_documents_dir,
                release_manifest=args.release_manifest,
                output=args.output,
                check_stopped=lambda: _assert_services_stopped(args.compose_file, args.env_file),
            )
            print(f"Offline state backup created: {args.output}")
        else:
            restore_backup(
                backup=args.backup,
                release_manifest=args.release_manifest,
                business_dir=args.business_dir,
                doclib_dir=args.doclib_dir,
                shared_documents_dir=args.shared_documents_dir,
                check_stopped=lambda: _assert_services_stopped(args.compose_file, args.env_file),
            )
            print("Offline state restored to new directories; inspect ownership and run runtime verification")
    except (BackupError, OSError, UnicodeError) as exc:
        print(f"Offline state backup error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
