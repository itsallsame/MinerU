"""Recheck imported offline release artifacts against a selected release.json.

Run from the repository root with ``python3 -m scripts.verify_offline_release``.
This is a read-only integrity/provenance check, not a GPU or model-quality test.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts import offline_package, release_manifest


def verify_release(
    *,
    release_path: Path,
    worker: dict[str, Any],
    business: dict[str, Any],
    base: dict[str, Any],
    wheelhouse: Path,
    model_manifest: Path,
    model_dir: Path,
    web_dist: Path,
    previous_release: Path | None = None,
) -> dict[str, Any]:
    if not release_path.is_file() or release_path.is_symlink():
        raise ValueError("Selected release manifest is missing or is a symlink")
    selected = json.loads(release_path.read_text(encoding="utf-8"))
    if not isinstance(selected, dict) or selected.get("schema") != 4:
        raise ValueError("Selected release manifest has an unsupported schema")
    revision = selected.get("source_revision")
    if not isinstance(revision, str):
        raise ValueError("Selected release has no source revision")
    if selected.get("previous_release_sha256") is not None and previous_release is None:
        raise ValueError("Selected release requires the previous release manifest")
    rebuilt = release_manifest.build_release_record(
        revision=revision,
        worker=worker,
        business=business,
        base=base,
        wheelhouse=wheelhouse,
        model_manifest=model_manifest,
        model_source_lock=model_dir / offline_package.SOURCE_LOCK_NAME,
        web_dist=web_dist,
        previous_release=previous_release,
    )
    if rebuilt != selected:
        differing = sorted(
            set(rebuilt) ^ set(selected) | {key for key in rebuilt.keys() & selected.keys() if rebuilt[key] != selected[key]}
        )
        raise ValueError(f"Imported artifacts differ from selected release fields: {', '.join(differing)}")
    model_count = offline_package.verify_manifest(model_dir, model_manifest)
    if model_count != selected["model"]["file_count"]:
        raise ValueError("Mounted model file count differs from selected release")
    return {
        "schema": 1,
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "release_manifest_sha256": release_manifest._sha256(release_path),
        "source_revision": selected["source_revision"],
        "platform": selected["platform"],
        "worker_image_id": selected["worker_image_id"],
        "business_image_id": selected["business_image_id"],
        "base_image_id": selected["base_image_id"],
        "model_manifest_sha256": selected["model"]["manifest_sha256"],
        "model_files_verified": model_count,
        "wheelhouse_files_verified": len(selected["wheelhouse"]["files"]),
        "web_assets_verified": len(selected["business_web"]["files"]),
        "result": "artifact_integrity_passed",
    }


def _verify_source_tree(source_tree: Path, expected_revision: str) -> None:
    if not source_tree.is_dir() or source_tree.is_symlink():
        raise ValueError("Source tree is missing or is a symlink")
    result = subprocess.run(
        ["git", "-C", str(source_tree), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    if result.stdout.strip() != expected_revision:
        raise ValueError("Source checkout differs from selected release")
    status = subprocess.run(
        ["git", "-C", str(source_tree), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
    )
    if status.stdout.strip():
        raise ValueError("Source checkout is not clean")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", required=True, type=Path)
    parser.add_argument("--worker-image", required=True)
    parser.add_argument("--business-image", required=True)
    parser.add_argument("--base-image", required=True)
    parser.add_argument("--wheelhouse", required=True, type=Path)
    parser.add_argument("--model-manifest", required=True, type=Path)
    parser.add_argument("--model-dir", required=True, type=Path)
    parser.add_argument("--web-dist", required=True, type=Path)
    parser.add_argument("--previous-release", type=Path)
    parser.add_argument("--source-tree", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        if args.output is not None:
            if args.output.exists() or args.output.is_symlink():
                raise ValueError("Artifact verification report already exists; choose a new output path")
            output = args.output.resolve()
            protected = [args.wheelhouse.resolve(), args.model_dir.resolve(), args.web_dist.resolve()]
            protected_files = {args.release.resolve(), args.model_manifest.resolve()}
            if args.previous_release is not None:
                protected_files.add(args.previous_release.resolve())
            if any(output.is_relative_to(root) for root in protected) or output in protected_files:
                raise ValueError("Verification report must be outside selected release artifacts")
        report = verify_release(
            release_path=args.release,
            worker=release_manifest._image_info(args.worker_image),
            business=release_manifest._image_info(args.business_image),
            base=release_manifest._image_info(args.base_image),
            wheelhouse=args.wheelhouse,
            model_manifest=args.model_manifest,
            model_dir=args.model_dir,
            web_dist=args.web_dist,
            previous_release=args.previous_release,
        )
        if args.source_tree is not None:
            _verify_source_tree(args.source_tree, report["source_revision"])
        if args.output is not None:
            release_manifest._write_new_release(args.output, report)
        print("Offline release artifacts verified; runtime and GPU acceptance remain separate")
    except (OSError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
        print(f"Offline release verification error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
