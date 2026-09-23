"""Offline packaging checks that do not require model weights or a Docker daemon."""

from __future__ import annotations

import importlib.util
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

    assert offline_package.preflight(model_dir, manifest_path) == 1
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


def test_wheelhouse_preparation_requires_base_pins_and_hashes() -> None:
    script = (ROOT / "scripts" / "prepare-worker-wheelhouse.sh").read_text()
    assert "MINERU_BASE_CONSTRAINTS" in script
    assert "for dependency in torch torchvision vllm" in script
    assert "--generate-hashes" in script
    assert "--require-hashes --only-binary=:all:" in script
