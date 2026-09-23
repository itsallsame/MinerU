"""Prepare and verify a separately transported MinerU model directory."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

MANIFEST_SCHEMA = 1
CHUNK_SIZE = 1024 * 1024


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
    if output.resolve().is_relative_to(root):
        raise ValueError("Manifest must be outside the model directory")
    files = {path.relative_to(root).as_posix(): sha256_file(path) for path in model_files(root)}
    manifest: dict[str, object] = {"schema": MANIFEST_SCHEMA, "files": files}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
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
        "MINERU_LLM_AIDED_FEATURES_TITLE_LEVELING": "false",
        "MINERU_LLM_AIDED_FEATURES_CROSS_PAGE_TABLE_CELL_MERGE": "false",
    }
    for key, expected in required.items():
        if os.getenv(key) != expected:
            raise ValueError(f"{key} must be {expected!r} in offline production")
    if os.getenv("MINERU_MODEL_VLM_SERVER_URL"):
        raise ValueError("Remote VLM server is disabled in offline production")
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
