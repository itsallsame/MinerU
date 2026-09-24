"""Download this deployment's exact Hugging Face model commits before offline transfer.

Run only on a connected preparation machine. Production never invokes this module.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from huggingface_hub import HfApi, snapshot_download
from huggingface_hub.utils import filter_repo_objects

from mineru.model.download import MODEL_COMPLETE_MARKER
from mineru.model.registry import MINERU_2_5_PRO_2605_1_2B, MINERU_4_MODELS_TORCH, ModelRepo
from scripts import offline_package

_COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")
_LOCK_NAME = ".mineru_source_lock.json"


def _checked_commit(value: str) -> str:
    if _COMMIT_RE.fullmatch(value) is None:
        raise ValueError("Model revision must be an exact lowercase 40-character commit SHA")
    return value


def _expected_files(repo: ModelRepo, info: Any) -> tuple[list[str], list[str] | None]:
    filenames = [getattr(item, "rfilename", None) for item in (getattr(info, "siblings", None) or ())]
    if not filenames or any(not isinstance(name, str) for name in filenames):
        raise ValueError(f"Hugging Face returned no file list for {repo.name}")
    for name in filenames:
        path = Path(name)
        if path.is_absolute() or not name or "\\" in name or any(part in ("", ".", "..") for part in name.split("/")):
            raise ValueError(f"Unsafe remote model path: {name}")
    if len(filenames) != len(set(filenames)):
        raise ValueError(f"Hugging Face returned duplicate file names for {repo.name}")
    patterns: list[str] | None = None
    if repo.download_mode == "required_paths":
        patterns = []
        for required in repo.required_paths():
            relative = required.relative_path
            patterns.extend((relative, f"{relative}/*"))
    selected = sorted(filter_repo_objects(filenames, allow_patterns=patterns))
    if not selected:
        raise ValueError(f"Pinned {repo.name} commit has no required model files")
    if repo.download_mode == "required_paths":
        for required in repo.required_paths():
            relative = required.relative_path
            if not any(name == relative or name.startswith(f"{relative}/") for name in selected):
                raise ValueError(f"Pinned {repo.name} commit lacks required path: {relative}")
    return selected, patterns


def _download_one(repo: ModelRepo, revision: str, stage: Path, *, api: HfApi) -> dict[str, object]:
    repo_id = repo.repos["huggingface"]
    info = api.model_info(repo_id, revision=revision)
    if getattr(info, "sha", None) != revision:
        raise ValueError(f"Resolved Hugging Face commit differs for {repo.name}")
    expected, patterns = _expected_files(repo, info)
    local_dir = stage / repo.local_name
    local_dir.mkdir()
    downloaded = Path(
        snapshot_download(
            repo_id,
            revision=revision,
            local_dir=str(local_dir),
            allow_patterns=patterns,
        )
    )
    if downloaded.resolve() != local_dir.resolve():
        raise ValueError(f"Hugging Face wrote {repo.name} outside its staged directory")
    provider_cache = local_dir / ".cache"
    if provider_cache.is_symlink():
        raise ValueError(f"Hugging Face cache is a symlink for {repo.name}")
    if provider_cache.is_dir():
        shutil.rmtree(provider_cache)  # Only metadata inside this newly created stage.
    actual = {path.relative_to(local_dir).as_posix() for path in offline_package.model_files(local_dir)}
    if actual != set(expected):
        raise ValueError(
            f"Pinned {repo.name} file set differs: missing={sorted(set(expected) - actual)}, "
            f"unexpected={sorted(actual - set(expected))}"
        )
    if repo.download_mode == "full":
        (local_dir / MODEL_COMPLETE_MARKER).touch()
    else:
        for required in repo.required_paths():
            path = local_dir / required.relative_path
            if path.is_dir():
                (path / MODEL_COMPLETE_MARKER).touch()
            elif not path.is_file():
                raise ValueError(f"Pinned {repo.name} required path is incomplete: {required.relative_path}")
    return {"repo": repo.name, "repo_id": repo_id, "revision": revision, "file_count": len(expected)}


def prepare(
    model_dir: Path, manifest_path: Path, *, small_revision: str, vlm_revision: str, api: HfApi | None = None
) -> dict[str, object]:
    """Stage, verify and publish one new Torch + vLLM model directory and manifest."""
    small_revision = _checked_commit(small_revision)
    vlm_revision = _checked_commit(vlm_revision)
    if model_dir.exists() or model_dir.is_symlink() or manifest_path.exists() or manifest_path.is_symlink():
        raise ValueError("Model directory and manifest must be new; existing releases are immutable")
    if not model_dir.is_absolute() or not manifest_path.is_absolute():
        raise ValueError("Model directory and manifest paths must be absolute")
    if model_dir.parent.is_symlink() or manifest_path.parent.is_symlink():
        raise ValueError("Output parents must not be symlinks")
    model_parent = model_dir.parent.resolve(strict=True)
    manifest_parent = manifest_path.parent.resolve(strict=True)
    if not model_parent.is_dir() or not manifest_parent.is_dir():
        raise ValueError("Output parents must be existing real directories")
    source_root = Path(__file__).resolve().parents[1]
    if (model_parent / model_dir.name).is_relative_to(source_root) or (manifest_parent / manifest_path.name).is_relative_to(
        source_root
    ):
        raise ValueError("Model artifacts must be outside the source checkout")
    if manifest_path.is_relative_to(model_dir) or model_dir.is_relative_to(manifest_path):
        raise ValueError("Manifest must be outside the model directory")
    client = api or HfApi()
    stage = Path(tempfile.mkdtemp(prefix=".mineru-model-stage-", dir=model_parent))
    sources = [
        _download_one(MINERU_4_MODELS_TORCH, small_revision, stage, api=client),
        _download_one(MINERU_2_5_PRO_2605_1_2B, vlm_revision, stage, api=client),
    ]
    lock: dict[str, object] = {"schema": 1, "source": "huggingface", "repos": sources}
    (stage / _LOCK_NAME).write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    offline_package.model_files(stage)  # Reject symlinks before publishing anything.
    if model_dir.exists() or model_dir.is_symlink():
        raise ValueError("Model directory appeared during preparation")
    os.rename(stage, model_dir)
    offline_package.create_manifest(model_dir, manifest_path)
    return lock


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--small-revision", required=True)
    parser.add_argument("--vlm-revision", required=True)
    args = parser.parse_args()
    try:
        result = prepare(
            args.model_dir,
            args.manifest,
            small_revision=args.small_revision,
            vlm_revision=args.vlm_revision,
        )
    except (OSError, ValueError) as exc:
        print(f"Model preparation error: {exc}", file=sys.stderr)
        return 1
    print(f"Prepared {len(result['repos'])} pinned model repositories; manifest: {args.manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
