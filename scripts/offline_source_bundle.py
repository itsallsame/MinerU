"""Package a clean master commit as a shallow first checkout or incremental Git bundle."""

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
WEIGHT_SUFFIXES = {
    ".safetensors",
    ".onnx",
    ".pt",
    ".pth",
    ".gguf",
    ".ckpt",
    ".bin",
    ".model",
    ".tiktoken",
    ".ggml",
    ".ot",
    ".npy",
    ".npz",
    ".h5",
    ".hdf5",
    ".pb",
    ".pdparams",
}
MODEL_DIRECTORY_NAMES = {
    "models",
    "model-weights",
    "model_weights",
    "weights",
    "checkpoints",
    "snapshots",
    "mineru-4_models_torch",
    "mineru2.5-pro-2605-1.2b",
}


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


def _source_files(repo: Path, *revisions: str) -> list[str]:
    command = ["git", "-C", str(repo), "rev-list", "--objects", *revisions]
    result = subprocess.run(command, capture_output=True, check=True)
    return [line.split(b" ", 1)[1].decode("utf-8", "surrogateescape") for line in result.stdout.splitlines() if b" " in line]


def _reject_weights(paths: list[str]) -> None:
    offenders = [
        path
        for path in paths
        if Path(path).suffix.lower() in WEIGHT_SUFFIXES
        or any(part.lower() in MODEL_DIRECTORY_NAMES for part in Path(path).parts[:-1])
    ]
    if offenders:
        raise ValueError(f"Source package would include model artifacts: {offenders[:3]}")


def _tree_hashes(root: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"Source snapshot contains a symlink: {path}")
        if path.is_dir():
            continue
        if not _real_file(path):
            raise ValueError(f"Source snapshot contains a non-regular file: {path}")
        files[path.relative_to(root).as_posix()] = _sha256(path)
    if not files:
        raise ValueError("Source snapshot is empty")
    return files


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
    if previous is None:
        # Git bundles of full fork history would include deleted historical model
        # weights. A depth-one bare clone keeps the exact upstream commit ID while
        # transferring only the current tree and a shallow boundary.
        current_paths = _git(repo, "ls-tree", "-r", "--name-only", "HEAD").splitlines()
        _reject_weights(current_paths)
        output.mkdir(mode=0o700)
        snapshot = output / "source.git"
        subprocess.run(
            [
                "git",
                "clone",
                "--quiet",
                "--bare",
                "--depth=1",
                "--single-branch",
                "--no-tags",
                "--branch=master",
                repo.as_uri(),
                str(snapshot),
            ],
            check=True,
        )
        _git(snapshot, "remote", "remove", "origin")
        if _git(snapshot, "rev-parse", "HEAD") != revision or _git(snapshot, "rev-parse", "--is-shallow-repository") != "true":
            raise ValueError("Shallow source snapshot differs from the selected master commit")
        _git(snapshot, "fsck", "--no-reflogs")
        artifact = "source.git"
        hashes = _tree_hashes(snapshot)
        format_name = "shallow-snapshot"
    else:
        _reject_weights(_source_files(repo, revision, f"^{previous}"))
        output.mkdir(mode=0o700)
        bundle = output / "source.bundle"
        _git(repo, "bundle", "create", str(bundle), "HEAD", "master", f"^{previous}")
        _git(repo, "bundle", "verify", str(bundle))
        if _bundle_heads(bundle) != {(revision, "HEAD"), (revision, "refs/heads/master")}:
            raise ValueError("Offline source bundle refs differ from master HEAD")
        with bundle.open("rb") as stream:
            os.fsync(stream.fileno())
        artifact = "source.bundle"
        hashes = {artifact: _sha256(bundle)}
        format_name = "incremental-bundle"
    record = {
        "schema": 2,
        "format": format_name,
        "source_revision": revision,
        "previous_revision": previous,
        "reuse_wheelhouse_inputs_unchanged": reuse_wheelhouse,
        "files_sha256": hashes,
    }
    manifest = output / "manifest.json"
    _write_sync(manifest, json.dumps(record, indent=2, sort_keys=True) + "\n")
    _write_sync(output / "COMPLETE", _sha256(manifest) + "\n")
    _sync_directory(output)
    return record


def verify_bundle(*, bundle_dir: Path, target_repo: Path | None = None) -> dict:
    if bundle_dir.is_symlink() or not bundle_dir.is_dir():
        raise ValueError("Offline source bundle directory is missing")
    manifest, marker = (bundle_dir / name for name in ("manifest.json", "COMPLETE"))
    if not all(_real_file(path) for path in (manifest, marker)):
        raise ValueError("Offline source manifest and marker must be regular, non-linked files")
    if marker.read_text(encoding="ascii") != _sha256(manifest) + "\n":
        raise ValueError("Offline source completion marker differs from its manifest")
    record = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(record, dict) or set(record) != {
        "schema",
        "format",
        "source_revision",
        "previous_revision",
        "reuse_wheelhouse_inputs_unchanged",
        "files_sha256",
    }:
        raise ValueError("Unsupported offline source manifest")
    previous = record["previous_revision"]
    if (
        record["schema"] != 2
        or record["format"] not in {"shallow-snapshot", "incremental-bundle"}
        or not isinstance(record["source_revision"], str)
        or not COMMIT_RE.fullmatch(record["source_revision"])
        or (previous is not None and (not isinstance(previous, str) or not COMMIT_RE.fullmatch(previous)))
        or not isinstance(record["reuse_wheelhouse_inputs_unchanged"], bool)
        or (record["reuse_wheelhouse_inputs_unchanged"] and previous is None)
        or not isinstance(record["files_sha256"], dict)
        or not record["files_sha256"]
        or not all(
            isinstance(key, str) and isinstance(value, str) and SHA256_RE.fullmatch(value)
            for key, value in record["files_sha256"].items()
        )
        or (record["format"] == "shallow-snapshot" and previous is not None)
        or (record["format"] == "incremental-bundle" and previous is None)
    ):
        raise ValueError("Unsupported offline source manifest")
    revision = record["source_revision"]
    if record["format"] == "shallow-snapshot":
        snapshot = bundle_dir / "source.git"
        if {path.name for path in bundle_dir.iterdir()} != {"source.git", "manifest.json", "COMPLETE"}:
            raise ValueError("Offline source package has missing or extra files")
        if snapshot.is_symlink() or not snapshot.is_dir() or _tree_hashes(snapshot) != record["files_sha256"]:
            raise ValueError("Offline source snapshot checksum mismatch")
        if (
            _git(snapshot, "rev-parse", "HEAD") != revision
            or _git(snapshot, "symbolic-ref", "HEAD") != "refs/heads/master"
            or _git(snapshot, "rev-parse", "--is-shallow-repository") != "true"
            or _git(snapshot, "rev-list", "--count", "HEAD") != "1"
        ):
            raise ValueError("Offline source snapshot has unexpected Git history or refs")
        _reject_weights(_git(snapshot, "ls-tree", "-r", "--name-only", "HEAD").splitlines())
        _git(snapshot, "fsck", "--no-reflogs")
    else:
        bundle = bundle_dir / "source.bundle"
        if {path.name for path in bundle_dir.iterdir()} != {"source.bundle", "manifest.json", "COMPLETE"}:
            raise ValueError("Offline source package has missing or extra files")
        if not _real_file(bundle) or record["files_sha256"] != {"source.bundle": _sha256(bundle)}:
            raise ValueError("Offline source bundle checksum mismatch")
        if _bundle_heads(bundle) != {(revision, "HEAD"), (revision, "refs/heads/master")}:
            raise ValueError("Offline source bundle refs differ from selected source revision")
    if target_repo is not None:
        if target_repo.is_symlink() or not target_repo.is_dir():
            raise ValueError("Target checkout must be a real directory")
        if _git(target_repo, "branch", "--show-current") != "master" or _git(target_repo, "status", "--porcelain"):
            raise ValueError("Target checkout must be on clean master before importing a bundle")
        if previous is not None and _git(target_repo, "rev-parse", "HEAD") != previous:
            raise ValueError("Target checkout is not at the recorded previous release commit")
        if previous is not None:
            _git(target_repo, "bundle", "verify", str(bundle.resolve()))
        elif _git(target_repo, "rev-parse", "HEAD") != revision:
            raise ValueError("Target checkout differs from the initial source snapshot")
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
