"""Transfer the built business Web separately from source, wheels, and models."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from scripts import offline_source_bundle, release_manifest


def _clean_master_revision(repo: Path) -> str:
    if repo.is_symlink() or not repo.is_dir():
        raise ValueError("Source checkout must be a real directory")
    if offline_source_bundle._git(repo, "branch", "--show-current") != "master" or offline_source_bundle._git(
        repo, "status", "--porcelain"
    ):
        raise ValueError("Business Web bundle requires a clean master checkout")
    revision = offline_source_bundle._git(repo, "rev-parse", "HEAD")
    if not offline_source_bundle.COMMIT_RE.fullmatch(revision):
        raise ValueError("Source HEAD must be a full Git commit")
    return revision


def _regular_dist_files(web_dist: Path) -> None:
    if not web_dist.is_dir() or web_dist.is_symlink():
        raise ValueError("Business Web dist directory is missing or is a symlink")
    if any(not offline_source_bundle._real_file(path) for path in web_dist.iterdir()):
        raise ValueError("Business Web dist must contain only non-linked regular files")


def create_bundle(*, repo: Path, web_dist: Path, output: Path) -> dict[str, object]:
    revision = _clean_master_revision(repo)
    repo = repo.resolve()
    _regular_dist_files(web_dist)
    release_manifest._validate_web_source_match(web_dist, repo / "business-web" / "src")
    if output.exists() or output.is_symlink() or not output.parent.is_dir():
        raise ValueError("Web bundle output must be a new path under an existing directory")
    destination = output.resolve()
    if destination.is_relative_to(repo) or destination.is_relative_to(web_dist.resolve()):
        raise ValueError("Web bundle output must be outside source and built assets")
    output = destination
    output.mkdir(mode=0o700)
    copied = output / "dist"
    shutil.copytree(web_dist, copied)
    _regular_dist_files(copied)
    manifest_sha256, files = release_manifest._validated_web_assets(copied)
    release_manifest._validate_web_source_match(copied, repo / "business-web" / "src")
    record: dict[str, object] = {
        "schema": 1,
        "source_revision": revision,
        "web_manifest_sha256": manifest_sha256,
        "asset_count": len(files),
    }
    manifest = output / "manifest.json"
    offline_source_bundle._write_sync(manifest, json.dumps(record, indent=2, sort_keys=True) + "\n")
    offline_source_bundle._write_sync(output / "COMPLETE", offline_source_bundle._sha256(manifest) + "\n")
    offline_source_bundle._sync_directory(output)
    return record


def verify_bundle(*, bundle_dir: Path, target_repo: Path | None = None, installed_dist: Path | None = None) -> dict:
    if bundle_dir.is_symlink() or not bundle_dir.is_dir():
        raise ValueError("Web bundle directory is missing or is a symlink")
    if {item.name for item in bundle_dir.iterdir()} != {"dist", "manifest.json", "COMPLETE"}:
        raise ValueError("Web bundle has missing or extra files")
    manifest, marker = (bundle_dir / name for name in ("manifest.json", "COMPLETE"))
    if not all(offline_source_bundle._real_file(path) for path in (manifest, marker)):
        raise ValueError("Web bundle manifest and marker must be regular, non-linked files")
    if marker.read_text(encoding="ascii") != offline_source_bundle._sha256(manifest) + "\n":
        raise ValueError("Web bundle completion marker differs from its manifest")
    record = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(record, dict) or set(record) != {
        "schema",
        "source_revision",
        "web_manifest_sha256",
        "asset_count",
    }:
        raise ValueError("Unsupported Web bundle manifest")
    if (
        record["schema"] != 1
        or not isinstance(record["source_revision"], str)
        or not offline_source_bundle.COMMIT_RE.fullmatch(record["source_revision"])
        or not isinstance(record["web_manifest_sha256"], str)
        or not offline_source_bundle.SHA256_RE.fullmatch(record["web_manifest_sha256"])
        or type(record["asset_count"]) is not int
        or record["asset_count"] < 1
    ):
        raise ValueError("Unsupported Web bundle manifest")
    copied = bundle_dir / "dist"
    _regular_dist_files(copied)
    manifest_sha256, files = release_manifest._validated_web_assets(copied)
    if manifest_sha256 != record["web_manifest_sha256"] or len(files) != record["asset_count"]:
        raise ValueError("Web bundle assets differ from selected manifest")
    if installed_dist is not None and target_repo is None:
        raise ValueError("Installed Web assets require a target source checkout")
    if target_repo is not None:
        revision = _clean_master_revision(target_repo)
        if revision != record["source_revision"]:
            raise ValueError("Target source revision differs from Web bundle")
        release_manifest._validate_web_source_match(copied, target_repo / "business-web" / "src")
    if installed_dist is not None:
        if installed_dist.resolve() != (target_repo.resolve() / "business-web" / "dist").resolve():
            raise ValueError("Installed Web assets must be the target checkout's business-web/dist")
        _regular_dist_files(installed_dist)
        installed_sha256, installed_files = release_manifest._validated_web_assets(installed_dist)
        if installed_sha256 != manifest_sha256 or installed_files != files:
            raise ValueError("Installed Web assets differ from verified bundle")
        release_manifest._validate_web_source_match(installed_dist, target_repo / "business-web" / "src")
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("--repo", required=True, type=Path)
    create.add_argument("--web-dist", required=True, type=Path)
    create.add_argument("--output", required=True, type=Path)
    verify = commands.add_parser("verify")
    verify.add_argument("--bundle-dir", required=True, type=Path)
    verify.add_argument("--target-repo", type=Path)
    verify.add_argument("--installed-dist", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            record = create_bundle(repo=args.repo, web_dist=args.web_dist, output=args.output)
            action = "created"
        else:
            record = verify_bundle(
                bundle_dir=args.bundle_dir,
                target_repo=args.target_repo,
                installed_dist=args.installed_dist,
            )
            action = "verified"
        print(f"Business Web bundle {action} for source {record['source_revision']}; image build remains separate")
    except (OSError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
        print(f"Offline Web bundle error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
