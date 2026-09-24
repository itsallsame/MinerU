"""Offline Git transfer is immutable, checksum-bound and incremental when possible."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import offline_source_bundle


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


def _repository(tmp_path: Path) -> Path:
    repo = tmp_path / "source"
    subprocess.run(["git", "init", "-q", "-b", "master", str(repo)], check=True)
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Offline Test")
    (repo / "docker" / "worker").mkdir(parents=True)
    (repo / "docker" / "worker" / "build-requirements.in").write_text("wheel\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text("[project]\nname='sample'\nversion='0.1'\n", encoding="utf-8")
    (repo / "app.py").write_text("version = 1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "initial")
    return repo


def test_full_and_incremental_bundle_import_without_model_directory(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    previous = _git(repo, "rev-parse", "HEAD")
    full = tmp_path / "full-package"
    full_record = offline_source_bundle.create_bundle(repo=repo, output=full)
    assert full_record["source_revision"] == previous
    assert offline_source_bundle.verify_bundle(bundle_dir=full) == full_record
    initial_target = tmp_path / "initial-target"
    subprocess.run(["git", "clone", "-q", str(full / "source.git"), str(initial_target)], check=True)
    assert _git(initial_target, "rev-parse", "HEAD") == previous
    assert _git(initial_target, "rev-parse", "--is-shallow-repository") == "true"
    assert offline_source_bundle.verify_bundle(bundle_dir=full, target_repo=initial_target) == full_record
    subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.offline_source_bundle",
            "verify",
            "--bundle-dir",
            str(full),
            "--target-repo",
            str(initial_target),
        ],
        check=True,
    )

    host_models = tmp_path / "separate-models"
    host_models.mkdir()
    (host_models / "weights.safetensors").write_bytes(b"large external weights")
    (repo / "app.py").write_text("version = 2\n", encoding="utf-8")
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-qm", "code only")
    current = _git(repo, "rev-parse", "HEAD")
    update = tmp_path / "incremental-package"
    record = offline_source_bundle.create_bundle(
        repo=repo,
        output=update,
        previous=previous,
        reuse_wheelhouse=True,
    )
    assert record["previous_revision"] == previous
    assert record["reuse_wheelhouse_inputs_unchanged"] is True
    assert offline_source_bundle.verify_bundle(bundle_dir=update, target_repo=initial_target) == record
    _git(initial_target, "fetch", str(update / "source.bundle"), "master")
    _git(initial_target, "merge", "--ff-only", "FETCH_HEAD")
    assert _git(initial_target, "rev-parse", "HEAD") == current
    assert (initial_target / "app.py").read_text(encoding="utf-8") == "version = 2\n"
    assert not (initial_target / "separate-models").exists()
    assert (host_models / "weights.safetensors").read_bytes() == b"large external weights"


def test_reused_wheelhouse_refuses_changed_dependency_inputs(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    previous = _git(repo, "rev-parse", "HEAD")
    (repo / "pyproject.toml").write_text("[project]\nname='sample'\nversion='0.2'\n", encoding="utf-8")
    _git(repo, "add", "pyproject.toml")
    _git(repo, "commit", "-qm", "dependency inputs changed")
    output = tmp_path / "reuse-not-allowed"
    with pytest.raises(ValueError, match="Dependency inputs changed"):
        offline_source_bundle.create_bundle(repo=repo, output=output, previous=previous, reuse_wheelhouse=True)
    assert not output.exists()
    record = offline_source_bundle.create_bundle(repo=repo, output=tmp_path / "new-wheelhouse-package", previous=previous)
    assert record["reuse_wheelhouse_inputs_unchanged"] is False


def test_bundle_refuses_dirty_checkout_existing_destination_and_tampering(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    (repo / "app.py").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(ValueError, match="clean master"):
        offline_source_bundle.create_bundle(repo=repo, output=tmp_path / "bundle")
    assert not (tmp_path / "bundle").exists()
    (repo / "app.py").write_text("version = 1\n", encoding="utf-8")
    output = tmp_path / "bundle"
    offline_source_bundle.create_bundle(repo=repo, output=output)
    with pytest.raises(ValueError, match="new path"):
        offline_source_bundle.create_bundle(repo=repo, output=output)
    manifest = output / "manifest.json"
    marker = output / "COMPLETE"
    original_manifest = manifest.read_text(encoding="utf-8")
    record = json.loads(original_manifest)
    record["source_revision"] = "a" * 40
    manifest.write_text(json.dumps(record), encoding="utf-8")
    marker.write_text(offline_source_bundle._sha256(manifest) + "\n", encoding="ascii")
    with pytest.raises(ValueError, match="unexpected Git history or refs"):
        offline_source_bundle.verify_bundle(bundle_dir=output)
    manifest.write_text(original_manifest, encoding="utf-8")
    marker.write_text(offline_source_bundle._sha256(manifest) + "\n", encoding="ascii")
    snapshot_file = output / "source.git" / "config"
    snapshot_file.write_bytes(snapshot_file.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="checksum mismatch"):
        offline_source_bundle.verify_bundle(bundle_dir=output)


def test_first_snapshot_excludes_deleted_historical_model_weights(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    historical_weight = repo / "old-model.onnx"
    historical_weight.write_bytes(b"historical model bytes")
    _git(repo, "add", "old-model.onnx")
    _git(repo, "commit", "-qm", "old model")
    weight_object = _git(repo, "rev-parse", "HEAD:old-model.onnx")
    historical_weight.unlink()
    _git(repo, "add", "-u")
    _git(repo, "commit", "-qm", "remove model")
    output = tmp_path / "source-only-snapshot"
    record = offline_source_bundle.create_bundle(repo=repo, output=output)
    assert record["format"] == "shallow-snapshot"
    snapshot = output / "source.git"
    assert offline_source_bundle.verify_bundle(bundle_dir=output) == record
    missing = subprocess.run(["git", "-C", str(snapshot), "cat-file", "-e", weight_object], capture_output=True)
    assert missing.returncode != 0

    historical_weight.write_bytes(b"current model bytes")
    _git(repo, "add", "old-model.onnx")
    _git(repo, "commit", "-qm", "restore model")
    with pytest.raises(ValueError, match="model artifacts"):
        offline_source_bundle.create_bundle(repo=repo, output=tmp_path / "refused-snapshot")


def test_incremental_bundle_rejects_even_transient_model_weight_commit(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    previous = _git(repo, "rev-parse", "HEAD")
    weight = repo / "temporary-model.safetensors"
    weight.write_bytes(b"never send this")
    _git(repo, "add", weight.name)
    _git(repo, "commit", "-qm", "add model by mistake")
    weight.unlink()
    _git(repo, "add", "-u")
    _git(repo, "commit", "-qm", "remove model")
    with pytest.raises(ValueError, match="model artifacts"):
        offline_source_bundle.create_bundle(repo=repo, output=tmp_path / "unsafe-delta", previous=previous)


@pytest.mark.parametrize("path", ["models/config.json", "assets/weights/tokenizer.json", "tokenizer.model"])
def test_source_package_rejects_model_artifacts_without_weight_suffix(tmp_path: Path, path: str) -> None:
    repo = _repository(tmp_path)
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("model artifact", encoding="utf-8")
    _git(repo, "add", path)
    _git(repo, "commit", "-qm", "accidental model artifact")
    with pytest.raises(ValueError, match="model artifacts"):
        offline_source_bundle.create_bundle(repo=repo, output=tmp_path / "blocked-first")
    assert not (tmp_path / "blocked-first").exists()


def test_incremental_rejects_transient_model_directory_even_after_delete(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    previous = _git(repo, "rev-parse", "HEAD")
    artifact = repo / "models" / "config.json"
    artifact.parent.mkdir()
    artifact.write_text("transient", encoding="utf-8")
    _git(repo, "add", "models/config.json")
    _git(repo, "commit", "-qm", "accidental model metadata")
    artifact.unlink()
    _git(repo, "add", "-u")
    _git(repo, "commit", "-qm", "remove model metadata")
    with pytest.raises(ValueError, match="model artifacts"):
        offline_source_bundle.create_bundle(repo=repo, output=tmp_path / "blocked-delta", previous=previous)
    assert not (tmp_path / "blocked-delta").exists()


def test_incremental_verifier_requires_exact_clean_target_baseline(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    previous = _git(repo, "rev-parse", "HEAD")
    target = tmp_path / "target"
    subprocess.run(["git", "clone", "-q", str(repo), str(target)], check=True)
    (repo / "app.py").write_text("version = 2\n", encoding="utf-8")
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-qm", "update")
    output = tmp_path / "incremental"
    offline_source_bundle.create_bundle(repo=repo, output=output, previous=previous)
    (target / "app.py").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(ValueError, match="clean master"):
        offline_source_bundle.verify_bundle(bundle_dir=output, target_repo=target)
    (target / "app.py").write_text("version = 1\n", encoding="utf-8")
    _git(target, "fetch", str(output / "source.bundle"), "master")
    _git(target, "merge", "--ff-only", "FETCH_HEAD")
    with pytest.raises(ValueError, match="previous release"):
        offline_source_bundle.verify_bundle(bundle_dir=output, target_repo=target)
