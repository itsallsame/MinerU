"""Offline packaging checks that do not require model weights or a Docker daemon."""

from __future__ import annotations

import importlib.util
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

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


def test_docker_build_context_sends_only_code_and_offline_artifacts(tmp_path: Path) -> None:
    if shutil.which("docker") is None:
        pytest.skip("Docker CLI is unavailable")
    probe = subprocess.run(["docker", "info"], capture_output=True, text=True, timeout=10, check=False)
    if probe.returncode:
        pytest.skip("Docker daemon is unavailable")

    context = tmp_path / "context"
    context.mkdir()
    shutil.copy2(ROOT / ".dockerignore", context / ".dockerignore")
    fixtures = {
        "pyproject.toml": "[project]\nname='probe'\n",
        "README.md": "readme",
        "LICENSE.md": "license",
        "mineru/keep.py": "source",
        "mineru/accidental-model.safetensors": "private-weight",
        "mineru/private.sqlite3": "private-database",
        "mineru/.env.local": "private-config",
        "scripts/offline_package.py": "package",
        "scripts/other.py": "excluded",
        "docker/worker/entrypoint.sh": "entrypoint",
        "business-web/dist/index.html": "web",
        "business-web/src/private.js": "excluded",
        "wheelhouse/requirements.lock": "lock",
        "wheelhouse/probe.whl": "wheel",
        "wheelhouse/private.txt": "excluded",
        "wheelhouse/nested/old.whl": "excluded",
        "models-v2/weights.onnx": "private-weight",
        "new-customer-data/private.txt": "private-document",
        "business-data/private.sqlite3": "private-database",
        "secret.env": "private-config",
    }
    for name, content in fixtures.items():
        path = context / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    (context / "Dockerfile").write_text("FROM scratch\nCOPY . /payload/\n")
    output = tmp_path / "output"
    subprocess.run(
        [
            "docker", "buildx", "build", "--pull=false", "--network=none", "--progress=quiet",
            f"--output=type=local,dest={output}", "-f", str(context / "Dockerfile"), str(context),
        ],
        capture_output=True, text=True, timeout=90, check=True,
    )
    for name in ("mineru/keep.py", "scripts/offline_package.py", "business-web/dist/index.html",
                 "wheelhouse/requirements.lock", "wheelhouse/probe.whl"):
        assert (output / "payload" / name).is_file(), name
    for name in ("mineru/accidental-model.safetensors", "mineru/private.sqlite3", "mineru/.env.local",
                 "scripts/other.py",
                 "business-web/src/private.js", "wheelhouse/private.txt", "wheelhouse/nested/old.whl",
                 "models-v2/weights.onnx",
                 "new-customer-data/private.txt", "business-data/private.sqlite3", "secret.env"):
        assert not (output / "payload" / name).exists(), name


def test_business_worker_retains_parse_history_for_evidence() -> None:
    compose = (ROOT / "docker" / "compose.business.yaml").read_text()
    worker = compose.split("  doclib-worker:", 1)[1]
    assert 'MINERU_DOCLIB_COMPACTION_INTERVAL_SEC: "0"' in worker
    assert "MINERU_EXPECTED_MODEL_MANIFEST_SHA256" in worker
    assert "MINERU_EXPECTED_MODEL_MANIFEST_SHA256" in (ROOT / "docker" / "worker" / "entrypoint.sh").read_text()


def test_business_compose_persistent_paths_match_runtime_preflight_contract() -> None:
    services = yaml.safe_load((ROOT / "docker" / "compose.business.yaml").read_text())["services"]
    assert {key: services["doclib-worker"]["environment"][key] for key in (
        "MINERU_HOME", "MINERU_MODEL_BASE_DIR", "MINERU_MODEL_MANIFEST",
    )} == {
        "MINERU_HOME": "/var/lib/mineru",
        "MINERU_MODEL_BASE_DIR": "/opt/mineru-models",
        "MINERU_MODEL_MANIFEST": "/etc/mineru/model-manifest.json",
    }
    assert services["doclib-worker"]["environment"]["MINERU_DOCLIB_COMPACTION_INTERVAL_SEC"] == "0"
    assert {key: services["business-api"]["environment"][key] for key in (
        "MINERU_BUSINESS_DB_PATH", "MINERU_BUSINESS_UPLOAD_ROOT",
        "MINERU_BUSINESS_WEB_ROOT", "MINERU_BUSINESS_REQUIRE_WEB",
    )} == {
        "MINERU_BUSINESS_DB_PATH": "/var/lib/mineru-business/business.sqlite3",
        "MINERU_BUSINESS_UPLOAD_ROOT": "/srv/mineru-inbox",
        "MINERU_BUSINESS_WEB_ROOT": "/opt/mineru/business-web/dist",
        "MINERU_BUSINESS_REQUIRE_WEB": "1",
    }


def test_compose_bind_mounts_never_create_missing_host_paths(tmp_path: Path) -> None:
    compose_file = ROOT / "docker" / "compose.business.yaml"
    source = yaml.safe_load(compose_file.read_text())
    for service in source["services"].values():
        for volume in service["volumes"]:
            assert volume["type"] == "bind"
            assert volume["bind"]["create_host_path"] is False

    if shutil.which("docker") is None:
        pytest.skip("Docker Compose CLI is unavailable for rendered-config verification")
    sources = {name: tmp_path / name for name in ("models", "manifest.json", "doclib", "inbox", "business")}
    env = {
        **os.environ,
        "MINERU_WORKER_IMAGE": "worker:local",
        "MINERU_BUSINESS_IMAGE": "business:local",
        "MINERU_MODELS_HOST_DIR": str(sources["models"]),
        "MINERU_MODEL_MANIFEST_HOST_FILE": str(sources["manifest.json"]),
        "MINERU_DOCLIB_HOST_DIR": str(sources["doclib"]),
        "MINERU_SHARED_DOCUMENTS_HOST_DIR": str(sources["inbox"]),
        "MINERU_BUSINESS_HOST_DIR": str(sources["business"]),
        "MINERU_EXPECTED_MODEL_MANIFEST_SHA256": "a" * 64,
    }
    result = subprocess.run(
        ["docker", "compose", "-f", str(compose_file), "config", "--format", "json"],
        env=env, capture_output=True, text=True, check=True,
    )
    rendered = json.loads(result.stdout)
    assert rendered["networks"]["business-internal"]["internal"] is True
    for service in rendered["services"].values():
        for volume in service["volumes"]:
            assert volume["bind"].get("create_host_path") is not True
            assert volume["source"] in {str(path) for path in sources.values()}
    assert all(not path.exists() for path in sources.values())


def test_wheelhouse_preparation_requires_base_pins_and_hashes() -> None:
    script = (ROOT / "scripts" / "prepare-worker-wheelhouse.sh").read_text()
    assert "MINERU_BASE_CONSTRAINTS" in script
    assert "MINERU_BASE_IMAGE_ID" in script
    assert "python3 -m scripts.inspect_worker_base" in script
    assert script.index("python3 -m scripts.inspect_worker_base") < script.index("stage=$(mktemp")
    assert '--python "$(command -v python3)"' in script
    assert "--generate-hashes" in script
    assert "--require-hashes --only-binary=:all:" in script
    assert "--pull=never --network=none --read-only" in script
    assert "--dry-run --ignore-installed --no-deps" in script
    assert script.index("python3 -m pip download") < script.index("docker run --rm") < script.index('mv "$stage" wheelhouse')
