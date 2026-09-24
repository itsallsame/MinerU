"""Offline packaging checks that do not require model weights or a Docker daemon."""

from __future__ import annotations

import importlib.util
import hashlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("offline_package", ROOT / "scripts" / "offline_package.py")
assert SPEC is not None and SPEC.loader is not None
offline_package = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(offline_package)


def test_model_manifest_round_trip(tmp_path: Path) -> None:
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    (model_dir / "weights.safetensors").write_bytes(b"model-contents")
    manifest_path = tmp_path / "model-manifest.json"

    offline_package.create_manifest(model_dir, manifest_path)

    assert offline_package.verify_manifest(model_dir, manifest_path) == 1
    assert json.loads(manifest_path.read_text())["files"]["weights.safetensors"]


def test_model_manifest_never_overwrites_a_previous_version(tmp_path: Path) -> None:
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    (model_dir / "weights.safetensors").write_bytes(b"model-contents")
    manifest_path = tmp_path / "version-1" / "model-manifest.json"
    offline_package.create_manifest(model_dir, manifest_path)
    original = manifest_path.read_bytes()

    with pytest.raises(ValueError, match="already exists"):
        offline_package.create_manifest(model_dir, manifest_path)
    assert manifest_path.read_bytes() == original
    assert not list(manifest_path.parent.glob(f".{manifest_path.name}.*"))

    next_manifest = tmp_path / "version-2" / "model-manifest.json"
    (model_dir / "weights.safetensors").write_bytes(b"new-model-contents")
    offline_package.create_manifest(model_dir, next_manifest)
    assert next_manifest.read_bytes() != original
    assert manifest_path.read_bytes() == original


def test_model_manifest_refuses_symlink_and_model_directory_output(tmp_path: Path) -> None:
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    (model_dir / "weights.safetensors").write_bytes(b"model-contents")
    original = tmp_path / "original.json"
    original.write_bytes(b"keep this file")
    link = tmp_path / "model-manifest.json"
    link.symlink_to(original)

    with pytest.raises(ValueError, match="already exists"):
        offline_package.create_manifest(model_dir, link)
    assert original.read_bytes() == b"keep this file"

    with pytest.raises(ValueError, match="outside the model directory"):
        offline_package.create_manifest(model_dir, model_dir / "model-manifest.json")
    assert not (model_dir / "model-manifest.json").exists()


def test_model_manifest_concurrent_writer_wins_without_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    (model_dir / "weights.safetensors").write_bytes(b"model-contents")
    output = tmp_path / "model-manifest.json"

    def competing_writer(_source: Path, target: Path) -> None:
        target.write_bytes(b"other writer")
        raise FileExistsError(target)

    monkeypatch.setattr(offline_package.os, "link", competing_writer)
    with pytest.raises(FileExistsError):
        offline_package.create_manifest(model_dir, output)
    assert output.read_bytes() == b"other writer"
    assert not list(tmp_path.glob(f".{output.name}.*"))


def test_modified_or_missing_model_fails(tmp_path: Path) -> None:
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    model = model_dir / "weights.safetensors"
    model.write_bytes(b"original")
    manifest_path = tmp_path / "manifest.json"
    offline_package.create_manifest(model_dir, manifest_path)

    model.write_bytes(b"changed")
    with pytest.raises(ValueError, match="checksum mismatch"):
        offline_package.verify_manifest(model_dir, manifest_path)
    model.unlink()
    with pytest.raises(ValueError, match="empty"):
        offline_package.verify_manifest(model_dir, manifest_path)


def test_unexpected_model_file_and_symlink_fail(tmp_path: Path) -> None:
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    (model_dir / "weights.safetensors").write_bytes(b"original")
    manifest_path = tmp_path / "manifest.json"
    offline_package.create_manifest(model_dir, manifest_path)

    (model_dir / "unlisted.txt").write_text("unexpected")
    with pytest.raises(ValueError, match="file set differs"):
        offline_package.verify_manifest(model_dir, manifest_path)
    (model_dir / "unlisted.txt").unlink()
    (model_dir / "link").symlink_to(model_dir / "weights.safetensors")
    with pytest.raises(ValueError, match="Symlinks"):
        offline_package.verify_manifest(model_dir, manifest_path)


def test_preflight_rejects_remote_configuration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    (model_dir / "weights.safetensors").write_bytes(b"original")
    manifest_path = tmp_path / "manifest.json"
    offline_package.create_manifest(model_dir, manifest_path)
    required = {
        "MINERU_MODEL_SOURCE": "local",
        "MINERU_MODEL_SMALL_BACKEND": "torch",
        "MINERU_MODEL_VLM_ENGINE": "vllm",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "MINERU_LLM_AIDED_FEATURES_TITLE_LEVELING": "false",
        "MINERU_LLM_AIDED_FEATURES_CROSS_PAGE_TABLE_CELL_MERGE": "false",
    }
    for name, value in required.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("MINERU_MODEL_VLM_SERVER_URL", raising=False)
    monkeypatch.delenv("MINERU_EXPECTED_MODEL_MANIFEST_SHA256", raising=False)

    assert offline_package.preflight(model_dir, manifest_path) == 1
    monkeypatch.setenv("MINERU_EXPECTED_MODEL_MANIFEST_SHA256", hashlib.sha256(manifest_path.read_bytes()).hexdigest())
    assert offline_package.preflight(model_dir, manifest_path) == 1
    monkeypatch.setenv("MINERU_EXPECTED_MODEL_MANIFEST_SHA256", "a" * 64)
    with pytest.raises(ValueError, match="differs from the selected release"):
        offline_package.preflight(model_dir, manifest_path)
    monkeypatch.setenv("MINERU_EXPECTED_MODEL_MANIFEST_SHA256", "not-a-digest")
    with pytest.raises(ValueError, match="lowercase SHA-256"):
        offline_package.preflight(model_dir, manifest_path)
    monkeypatch.delenv("MINERU_EXPECTED_MODEL_MANIFEST_SHA256")
    monkeypatch.setenv("MINERU_MODEL_SOURCE", "auto")
    with pytest.raises(ValueError, match="MINERU_MODEL_SOURCE"):
        offline_package.preflight(model_dir, manifest_path)
    monkeypatch.setenv("MINERU_MODEL_SOURCE", "local")
    monkeypatch.setenv("MINERU_MODEL_VLM_SERVER_URL", "https://example.test")
    with pytest.raises(ValueError, match="Remote VLM"):
        offline_package.preflight(model_dir, manifest_path)


def test_worker_build_uses_local_source_only() -> None:
    dockerfile = (ROOT / "docker" / "worker" / "Dockerfile").read_text()
    assert "--no-index" in dockerfile
    assert "--require-hashes -r /opt/wheelhouse/requirements.lock" in dockerfile
    assert "--no-deps --no-build-isolation /opt/mineru" in dockerfile
    assert "models download" not in dockerfile
    assert "COPY mineru/" in dockerfile
    assert "COPY models/" not in dockerfile


def test_business_worker_retains_parse_history_for_evidence() -> None:
    compose = (ROOT / "docker" / "compose.business.yaml").read_text()
    worker = compose.split("  doclib-worker:", 1)[1]
    assert 'MINERU_DOCLIB_COMPACTION_INTERVAL_SEC: "0"' in worker
    assert "MINERU_EXPECTED_MODEL_MANIFEST_SHA256" in worker
    assert "MINERU_EXPECTED_MODEL_MANIFEST_SHA256" in (ROOT / "docker" / "worker" / "entrypoint.sh").read_text()


def test_wheelhouse_preparation_requires_base_pins_and_hashes() -> None:
    script = (ROOT / "scripts" / "prepare-worker-wheelhouse.sh").read_text()
    assert "MINERU_BASE_CONSTRAINTS" in script
    assert "for dependency in torch torchvision vllm" in script
    assert "--generate-hashes" in script
    assert "--require-hashes --only-binary=:all:" in script
