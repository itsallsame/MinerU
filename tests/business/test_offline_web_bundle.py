"""Business Web transfer remains source-bound and separate from model artifacts."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts import offline_web_bundle


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


def _source(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "source"
    subprocess.run(["git", "init", "-q", "-b", "master", str(repo)], check=True)
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Offline Test")
    source = repo / "business-web" / "src"
    source.mkdir(parents=True)
    (source / "index.html").write_text("<h1>MinerU</h1>\n", encoding="utf-8")
    (source / "app.js").write_text("export const version = 1;\n", encoding="utf-8")
    (repo / ".gitignore").write_text("dist/\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "web source")
    dist = repo / "business-web" / "dist"
    dist.mkdir()
    hashes = {}
    for path in source.iterdir():
        shutil.copy2(path, dist / path.name)
        hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    (dist / "asset-manifest.json").write_text(json.dumps(hashes), encoding="utf-8")
    return repo, dist


def test_web_bundle_import_matches_clean_target_source_and_installed_dist(tmp_path: Path) -> None:
    repo, dist = _source(tmp_path)
    bundle = tmp_path / "web-package"
    record = offline_web_bundle.create_bundle(repo=repo, web_dist=dist, output=bundle)
    assert record["source_revision"] == _git(repo, "rev-parse", "HEAD")
    assert record["asset_count"] == 2
    assert offline_web_bundle.verify_bundle(bundle_dir=bundle) == record
    target = tmp_path / "target"
    subprocess.run(["git", "clone", "-q", str(repo), str(target)], check=True)
    installed = target / "business-web" / "dist"
    shutil.copytree(bundle / "dist", installed)
    assert offline_web_bundle.verify_bundle(bundle_dir=bundle, target_repo=target, installed_dist=installed) == record
    assert "models" not in {path.name for path in bundle.iterdir()}
    with pytest.raises(ValueError, match="target checkout's"):
        offline_web_bundle.verify_bundle(bundle_dir=bundle, target_repo=target, installed_dist=bundle / "dist")


def test_web_bundle_rejects_stale_build_dirty_tree_and_existing_output(tmp_path: Path) -> None:
    repo, dist = _source(tmp_path)
    (dist / "app.js").write_text("stale", encoding="utf-8")
    with pytest.raises(ValueError, match="differs"):
        offline_web_bundle.create_bundle(repo=repo, web_dist=dist, output=tmp_path / "stale")
    assert not (tmp_path / "stale").exists()
    shutil.copy2(repo / "business-web" / "src" / "app.js", dist / "app.js")
    (repo / "business-web" / "src" / "app.js").write_text("dirty", encoding="utf-8")
    with pytest.raises(ValueError, match="clean master"):
        offline_web_bundle.create_bundle(repo=repo, web_dist=dist, output=tmp_path / "dirty")
    (repo / "business-web" / "src" / "app.js").write_text("export const version = 1;\n", encoding="utf-8")
    output = tmp_path / "web-package"
    offline_web_bundle.create_bundle(repo=repo, web_dist=dist, output=output)
    with pytest.raises(ValueError, match="new path"):
        offline_web_bundle.create_bundle(repo=repo, web_dist=dist, output=output)


def test_web_bundle_rejects_tamper_wrong_commit_and_changed_installed_bytes(tmp_path: Path) -> None:
    repo, dist = _source(tmp_path)
    bundle = tmp_path / "web-package"
    offline_web_bundle.create_bundle(repo=repo, web_dist=dist, output=bundle)
    target = tmp_path / "target"
    subprocess.run(["git", "clone", "-q", str(repo), str(target)], check=True)
    installed = target / "business-web" / "dist"
    shutil.copytree(bundle / "dist", installed)
    (installed / "app.js").write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="asset"):
        offline_web_bundle.verify_bundle(bundle_dir=bundle, target_repo=target, installed_dist=installed)
    (installed / "app.js").write_bytes((bundle / "dist" / "app.js").read_bytes())
    (target / "business-web" / "src" / "app.js").write_text("export const version = 2;\n", encoding="utf-8")
    _git(target, "add", ".")
    _git(target, "-c", "user.email=test@example.invalid", "-c", "user.name=Offline Test", "commit", "-qm", "new source")
    with pytest.raises(ValueError, match="revision"):
        offline_web_bundle.verify_bundle(bundle_dir=bundle, target_repo=target)
    (bundle / "dist" / "app.js").write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="differs"):
        offline_web_bundle.verify_bundle(bundle_dir=bundle)


def test_web_bundle_rejects_hardlinked_asset(tmp_path: Path) -> None:
    repo, dist = _source(tmp_path)
    shared = tmp_path / "shared.js"
    shared.write_bytes((dist / "app.js").read_bytes())
    (dist / "app.js").unlink()
    (dist / "app.js").hardlink_to(shared)
    with pytest.raises(ValueError, match="regular files"):
        offline_web_bundle.create_bundle(repo=repo, web_dist=dist, output=tmp_path / "linked")


def test_web_bundle_rejects_symlink_and_missing_target_for_installed_dist(tmp_path: Path) -> None:
    repo, dist = _source(tmp_path)
    bundle = tmp_path / "web-package"
    offline_web_bundle.create_bundle(repo=repo, web_dist=dist, output=bundle)
    with pytest.raises(ValueError, match="require a target"):
        offline_web_bundle.verify_bundle(bundle_dir=bundle, installed_dist=dist)
    (bundle / "dist" / "app.js").unlink()
    (bundle / "dist" / "app.js").symlink_to(dist / "app.js")
    with pytest.raises(ValueError, match="regular files"):
        offline_web_bundle.verify_bundle(bundle_dir=bundle)
