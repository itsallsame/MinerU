"""Create an auditable release record after an offline Linux amd64 build."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def _command(*args: str) -> str:
    result = subprocess.run(args, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _image_info(reference: str) -> dict[str, Any]:
    payload = _command("docker", "image", "inspect", reference, "--format", "{{json .}}")
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise ValueError("Docker image inspect did not return an object")
    return data


def _validated_image_id(label: str, image: dict[str, Any], *, revision: str | None = None, base_id: str | None = None) -> str:
    if image.get("Os") != "linux" or image.get("Architecture") != "amd64":
        raise ValueError(f"{label} image is not linux/amd64")
    image_id = image.get("Id")
    if not isinstance(image_id, str) or not IMAGE_ID_RE.fullmatch(image_id):
        raise ValueError(f"{label} image has no immutable SHA-256 ID")
    labels = (image.get("Config") or {}).get("Labels") or {}
    if revision is not None and labels.get("org.opencontainers.image.revision") != revision:
        raise ValueError(f"{label} image revision label differs from the checked-out source")
    if base_id is not None and labels.get("org.opencontainers.image.base.id") != base_id:
        raise ValueError(f"{label} image base label differs from the inspected base image")
    return image_id


def build_release_record(
    *,
    revision: str,
    worker: dict[str, Any],
    business: dict[str, Any],
    base: dict[str, Any],
    wheelhouse: Path,
    model_manifest: Path,
    previous_release: Path | None = None,
) -> dict[str, Any]:
    if not COMMIT_RE.fullmatch(revision):
        raise ValueError("Source revision must be a full lowercase Git commit SHA")
    base_id = _validated_image_id("base", base)
    worker_id = _validated_image_id("worker", worker, revision=revision, base_id=base_id)
    business_id = _validated_image_id("business", business, revision=revision, base_id=base_id)
    if worker_id == business_id:
        raise ValueError("Worker and business images must have distinct IDs")
    if not wheelhouse.is_dir() or wheelhouse.is_symlink():
        raise ValueError("Wheelhouse directory is missing or is a symlink")
    wheel_files = sorted(path for path in wheelhouse.rglob("*") if path.is_file())
    if not wheel_files or not (wheelhouse / "requirements.lock").is_file():
        raise ValueError("Wheelhouse must contain requirements.lock and wheels")
    if any(path.is_symlink() for path in wheelhouse.rglob("*")):
        raise ValueError("Wheelhouse must not contain symlinks")
    if not any(path.suffix == ".whl" for path in wheel_files):
        raise ValueError("Wheelhouse has no wheel files")
    if not model_manifest.is_file() or model_manifest.is_symlink():
        raise ValueError("Model manifest is missing")
    model_data = json.loads(model_manifest.read_text(encoding="utf-8"))
    if (
        not isinstance(model_data, dict)
        or model_data.get("schema") != 1
        or not isinstance(model_data.get("files"), dict)
        or not model_data["files"]
    ):
        raise ValueError("Model manifest is empty or unsupported")
    if previous_release is not None and (not previous_release.is_file() or previous_release.is_symlink()):
        raise ValueError("Previous release manifest is missing")
    return {
        "schema": 2,
        "source_revision": revision,
        "platform": "linux/amd64",
        "worker_image_id": worker_id,
        "business_image_id": business_id,
        "base_image_id": base_id,
        "model": {
            "source": "local",
            "small_backend": "torch",
            "vlm_engine": "vllm",
            "manifest_sha256": _sha256(model_manifest),
            "file_count": len(model_data["files"]),
        },
        "wheelhouse": {
            "files": {path.relative_to(wheelhouse).as_posix(): _sha256(path) for path in wheel_files},
        },
        "previous_release_sha256": _sha256(previous_release) if previous_release else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker-image", required=True)
    parser.add_argument("--business-image", required=True)
    parser.add_argument("--base-image", required=True)
    parser.add_argument("--wheelhouse", required=True, type=Path)
    parser.add_argument("--model-manifest", required=True, type=Path)
    parser.add_argument("--previous-release", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.output.resolve().is_relative_to(args.wheelhouse.resolve()):
            raise ValueError("Release manifest output must be outside the wheelhouse")
        if _command("git", "status", "--porcelain"):
            raise ValueError("Release source tree must be clean")
        revision = _command("git", "rev-parse", "HEAD")
        record = build_release_record(
            revision=revision,
            worker=_image_info(args.worker_image),
            business=_image_info(args.business_image),
            base=_image_info(args.base_image),
            wheelhouse=args.wheelhouse,
            model_manifest=args.model_manifest,
            previous_release=args.previous_release,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_suffix(args.output.suffix + ".tmp")
        temporary.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, args.output)
        print(f"Release manifest written to {args.output}")
    except (OSError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
        print(f"Release manifest error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
