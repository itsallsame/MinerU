"""Package a clean master commit for verified, model-free offline source transfer."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
DEPENDENCY_INPUTS = ("pyproject.toml", "docker/worker/build-requirements.in")
PACKAGE_FILES = {"source.bundle", "manifest.json", "COMPLETE"}


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)
    return result.stdout.strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _bundle_heads(bundle: Path) -> set[tuple[str, str]]:
    result = subprocess.run(["git", "bundle", "list-heads", str(bundle)], capture_output=True, text=True, check=True)
    heads = {tuple(line.split()) for line in result.stdout.splitlines()}
    if any(len(head) != 2 for head in heads):
        raise ValueError("Offline source bundle has malformed Git refs")
    return heads


def _real_file(path: Path) -> bool:
    return path.is_file() and not path.is_symlink() and path.stat().st_nlink == 1


def _write_sync(path: Path, payload: str) -> None:
    with path.open("x", encoding="utf-8") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | (os.O_DIRECTORY if hasattr(os, "O_DIRECTORY") else 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def create_bundle(*, repo: Path, output: Path, previous: str | None = None, reuse_wheelhouse: bool = False) -> dict:
    if repo.is_symlink() or not repo.is_dir():
        raise ValueError("Source checkout must be a real directory")
    repo = repo.resolve()
    destination = output.resolve()
    if output.exists() or output.is_symlink() or not output.parent.is_dir():
        raise ValueError("Bundle output must be a new path under an existing directory")
    if destination.is_relative_to(repo):
        raise ValueError("Bundle output must be outside the source checkout")
    output = destination
    if _git(repo, "branch", "--show-current") != "master" or _git(repo, "status", "--porcelain"):
        raise ValueError("Create the offline source bundle from a clean master checkout")
    revision = _git(repo, "rev-parse", "HEAD")
    if not COMMIT_RE.fullmatch(revision):
        raise ValueError("Source HEAD must be a full Git commit")
    if previous is not None:
        if not COMMIT_RE.fullmatch(previous) or _git(repo, "cat-file", "-t", previous) != "commit":
            raise ValueError("Previous release must name an existing full commit")
        if (
            previous == revision
            or subprocess.run(
                ["git", "-C", str(repo), "merge-base", "--is-ancestor", previous, revision],
                check=False,
            ).returncode
            != 0
        ):
            raise ValueError("Previous release must be an ancestor before the current master commit")
    if reuse_wheelhouse:
        if previous is None:
            raise ValueError("Wheelhouse reuse requires a previous release commit")
        changed = subprocess.run(
            ["git", "-C", str(repo), "diff", "--quiet", previous, revision, "--", *DEPENDENCY_INPUTS],
            check=False,
        )
        if changed.returncode != 0:
            raise ValueError("Dependency inputs changed; prepare and verify a new Linux wheelhouse")
    output.mkdir(mode=0o700)
    bundle = output / "source.bundle"
    _git(repo, "bundle", "create", str(bundle), "HEAD", "master", *([f"^{previous}"] if previous else []))
    _git(repo, "bundle", "verify", str(bundle))
    if _bundle_heads(bundle) != {(revision, "HEAD"), (revision, "refs/heads/master")}:
        raise ValueError("Offline source bundle refs differ from master HEAD")
    with bundle.open("rb") as stream:
        os.fsync(stream.fileno())
    record = {
        "schema": 1,
        "source_revision": revision,
        "previous_revision": previous,
        "reuse_wheelhouse_inputs_unchanged": reuse_wheelhouse,
        "bundle_sha256": _sha256(bundle),
    }
    manifest = output / "manifest.json"
    _write_sync(manifest, json.dumps(record, indent=2, sort_keys=True) + "\n")
    _write_sync(output / "COMPLETE", _sha256(manifest) + "\n")
    _sync_directory(output)
    return record


def verify_bundle(*, bundle_dir: Path, target_repo: Path | None = None) -> dict:
    if bundle_dir.is_symlink() or not bundle_dir.is_dir():
        raise ValueError("Offline source bundle directory is missing")
    if {path.name for path in bundle_dir.iterdir()} != PACKAGE_FILES:
        raise ValueError("Offline source bundle has missing or extra files")
    bundle, manifest, marker = (bundle_dir / name for name in ("source.bundle", "manifest.json", "COMPLETE"))
    if not all(_real_file(path) for path in (bundle, manifest, marker)):
        raise ValueError("Offline source bundle files must be regular, non-linked files")
    if marker.read_text(encoding="ascii") != _sha256(manifest) + "\n":
        raise ValueError("Offline source completion marker differs from its manifest")
    record = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(record, dict) or set(record) != {
        "schema",
        "source_revision",
        "previous_revision",
        "reuse_wheelhouse_inputs_unchanged",
        "bundle_sha256",
    }:
        raise ValueError("Unsupported offline source manifest")
    previous = record["previous_revision"]
    if (
        record["schema"] != 1
        or not isinstance(record["source_revision"], str)
        or not COMMIT_RE.fullmatch(record["source_revision"])
        or (previous is not None and (not isinstance(previous, str) or not COMMIT_RE.fullmatch(previous)))
        or not isinstance(record["reuse_wheelhouse_inputs_unchanged"], bool)
        or (record["reuse_wheelhouse_inputs_unchanged"] and previous is None)
        or not isinstance(record["bundle_sha256"], str)
        or not SHA256_RE.fullmatch(record["bundle_sha256"])
    ):
        raise ValueError("Unsupported offline source manifest")
    if _sha256(bundle) != record["bundle_sha256"]:
        raise ValueError("Offline source bundle checksum mismatch")
    revision = record["source_revision"]
    if _bundle_heads(bundle) != {(revision, "HEAD"), (revision, "refs/heads/master")}:
        raise ValueError("Offline source bundle refs differ from selected source revision")
    if target_repo is not None:
        if target_repo.is_symlink() or not target_repo.is_dir():
            raise ValueError("Target checkout must be a real directory")
        if _git(target_repo, "branch", "--show-current") != "master" or _git(target_repo, "status", "--porcelain"):
            raise ValueError("Target checkout must be on clean master before importing a bundle")
        if previous is not None and _git(target_repo, "rev-parse", "HEAD") != previous:
            raise ValueError("Target checkout is not at the recorded previous release commit")
        _git(target_repo, "bundle", "verify", str(bundle.resolve()))
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("--repo", type=Path, default=Path("."))
    create.add_argument("--output", type=Path, required=True)
    create.add_argument("--previous-revision")
    create.add_argument("--reuse-wheelhouse", action="store_true")
    verify = commands.add_parser("verify")
    verify.add_argument("--bundle-dir", type=Path, required=True)
    verify.add_argument("--target-repo", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "create":
            record = create_bundle(
                repo=args.repo,
                output=args.output,
                previous=args.previous_revision,
                reuse_wheelhouse=args.reuse_wheelhouse,
            )
            print(f"Offline source bundle created for {record['source_revision']}; models and wheelhouse are separate")
        else:
            record = verify_bundle(bundle_dir=args.bundle_dir, target_repo=args.target_repo)
            print(f"Offline source bundle verified for {record['source_revision']}; target build still required")
    except (OSError, ValueError, UnicodeError, subprocess.CalledProcessError) as exc:
        print(f"Offline source bundle error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
