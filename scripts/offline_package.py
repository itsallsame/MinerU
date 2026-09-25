"""Prepare and verify a separately transported MinerU model directory."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path

MANIFEST_SCHEMA = 1
CHUNK_SIZE = 1024 * 1024
SOURCE_LOCK_NAME = ".mineru_source_lock.json"
_COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")


def validate_source_lock(lock_path: Path, manifest: dict[str, object]) -> list[dict[str, object]]:
    """Bind approved model repository commits to the hashed model-file manifest."""
    from mineru.model.registry import MINERU_2_5_PRO_2605_1_2B, MINERU_4_MODELS_TORCH

    if lock_path.name != SOURCE_LOCK_NAME or not lock_path.is_file() or lock_path.is_symlink():
        raise ValueError("Model source lock is missing or invalid")
    files = manifest.get("files")
    if not isinstance(files, dict) or files.get(SOURCE_LOCK_NAME) != sha256_file(lock_path):
        raise ValueError("Model source lock is not bound to the model manifest")
    if any(not isinstance(name, str) for name in files):
        raise ValueError("Model manifest contains an invalid file name")
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    if not isinstance(lock, dict) or lock.get("schema") != 1 or lock.get("source") != "huggingface":
        raise ValueError("Model source lock has an unsupported schema or provider")
    repos = lock.get("repos")
    expected = (MINERU_4_MODELS_TORCH, MINERU_2_5_PRO_2605_1_2B)
    if not isinstance(repos, list) or len(repos) != len(expected):
        raise ValueError("Model source lock must identify the required Torch and vLLM repositories")
    for item, repo in zip(repos, expected, strict=True):
        if not isinstance(item, dict) or set(item) != {"repo", "repo_id", "revision", "file_count"}:
            raise ValueError("Model source lock repository record is invalid")
        revision = item["revision"]
        if (
            item["repo"] != repo.name or item["repo_id"] != repo.repos["huggingface"]
            or not isinstance(revision, str) or _COMMIT_RE.fullmatch(revision) is None
            or type(item["file_count"]) is not int or item["file_count"] < 1
        ):
            raise ValueError(f"Model source lock repository identity is invalid: {repo.name}")
        count = sum(
            name.startswith(f"{repo.local_name}/") and not name.endswith("/.mineru_complete")
            and name != f"{repo.local_name}/.mineru_complete"
            for name in files
        )
        if count != item["file_count"]:
            raise ValueError(f"Model source lock file count differs from manifest: {repo.name}")
    return repos


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def model_files(root: Path) -> list[Path]:
    if not root.is_dir() or root.is_symlink():
        raise ValueError(f"Model directory must be a real directory: {root}")
    files: list[Path] = []
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"Symlinks are not allowed in model directory: {path}")
        if path.is_file():
            files.append(path)
    if not files:
        raise ValueError("Model directory is empty")
    return sorted(files)


def create_manifest(root: Path, output: Path) -> dict[str, object]:
    if root.is_symlink():
        raise ValueError("Model directory must not be a symlink")
    root = root.resolve()
    if output.exists() or output.is_symlink():
        raise ValueError("Model manifest output already exists; keep prior manifests immutable")
    if output.resolve().is_relative_to(root):
        raise ValueError("Manifest must be outside the model directory")
    files = {path.relative_to(root).as_posix(): sha256_file(path) for path in model_files(root)}
    manifest: dict[str, object] = {"schema": MANIFEST_SCHEMA, "files": files}
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=output.parent, prefix=f".{output.name}.", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return manifest


def verify_manifest(root: Path, manifest_path: Path) -> int:
    if not manifest_path.is_file():
        raise ValueError(f"Model manifest missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("schema") != MANIFEST_SCHEMA:
        raise ValueError("Unsupported model manifest schema")
    expected = manifest.get("files")
    if not isinstance(expected, dict) or not expected:
        raise ValueError("Model manifest has no files")
    if root.is_symlink():
        raise ValueError("Model directory must not be a symlink")
    root = root.resolve()
    actual_paths = {path.relative_to(root).as_posix(): path for path in model_files(root)}
    if set(actual_paths) != set(expected):
        missing = sorted(set(expected) - set(actual_paths))
        unexpected = sorted(set(actual_paths) - set(expected))
        raise ValueError(f"Model file set differs: missing={missing}, unexpected={unexpected}")
    for relative_path, expected_hash in expected.items():
        if not isinstance(expected_hash, str) or len(expected_hash) != 64:
            raise ValueError(f"Invalid SHA-256 entry: {relative_path}")
        if sha256_file(actual_paths[relative_path]) != expected_hash:
            raise ValueError(f"Model checksum mismatch: {relative_path}")
    return len(actual_paths)


def preflight(root: Path, manifest_path: Path) -> int:
    required = {
        "MINERU_MODEL_SOURCE": "local",
        "MINERU_MODEL_SMALL_BACKEND": "torch",
        "MINERU_MODEL_VLM_ENGINE": "vllm",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HF_DATASETS_OFFLINE": "1",
        "MINERU_DOCLIB_REMOTE_DISABLED": "1",
        "MINERU_LLM_AIDED_FEATURES_TITLE_LEVELING": "false",
        "MINERU_LLM_AIDED_FEATURES_CROSS_PAGE_TABLE_CELL_MERGE": "false",
    }
    for key, expected in required.items():
        if os.getenv(key) != expected:
            raise ValueError(f"{key} must be {expected!r} in offline production")
    if os.getenv("MINERU_MODEL_VLM_SERVER_URL"):
        raise ValueError("Remote VLM server is disabled in offline production")
    expected_manifest_hash = os.getenv("MINERU_EXPECTED_MODEL_MANIFEST_SHA256")
    if expected_manifest_hash is not None:
        if len(expected_manifest_hash) != 64 or any(char not in "0123456789abcdef" for char in expected_manifest_hash):
            raise ValueError("MINERU_EXPECTED_MODEL_MANIFEST_SHA256 must be a lowercase SHA-256")
        if not manifest_path.is_file() or sha256_file(manifest_path) != expected_manifest_hash:
            raise ValueError("Mounted model manifest differs from the selected release")
    return verify_manifest(root, manifest_path)


def verify_mineru_repos(root: Path) -> None:
    """Check the Torch and vLLM repositories required by this deployment."""
    from mineru.config import config
    from mineru.model.download import verify_model_repo
    from mineru.model.registry import MINERU_2_5_PRO_2605_1_2B, MINERU_4_MODELS_TORCH

    if Path(config.model.base_dir).resolve() != root.resolve():
        raise ValueError("Effective MinerU model.base_dir differs from the mounted model directory")
    if config.model.source != "local" or config.model.small_backend != "torch" or config.model.vlm.engine != "vllm":
        raise ValueError("Effective MinerU model configuration is not local Torch + vLLM")
    for repo in (MINERU_4_MODELS_TORCH, MINERU_2_5_PRO_2605_1_2B):
        result = verify_model_repo(repo)
        if not result.ready:
            raise ValueError(f"Required MinerU model repo {repo.name} is incomplete: {result.missing_paths}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("create-manifest", "verify-manifest", "preflight"):
        command_parser = subparsers.add_parser(command)
        command_parser.add_argument("--model-dir", type=Path, required=True)
        command_parser.add_argument("--manifest", type=Path, required=True)
        if command == "preflight":
            command_parser.add_argument("--require-mineru-repos", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "create-manifest":
            manifest = create_manifest(args.model_dir, args.manifest)
            print(f"Created model manifest with {len(manifest['files'])} files")
        elif args.command == "verify-manifest":
            print(f"Verified {verify_manifest(args.model_dir, args.manifest)} model files")
        else:
            count = preflight(args.model_dir, args.manifest)
            if args.require_mineru_repos:
                verify_mineru_repos(args.model_dir)
            print(f"Offline preflight verified {count} model files")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Offline package error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
