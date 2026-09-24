"""Runtime preflight accepts only the selected isolated deployment shape."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

import pytest

from scripts import verify_business_runtime
from scripts.verify_business_runtime import _validated_bind, check_artifact_report, check_runtime


def _deployment(tmp_path: Path) -> dict[str, object]:
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    model_manifest = tmp_path / "model-manifest.json"
    model_manifest.write_text("{}")
    inbox = str(tmp_path / "inbox")
    network_name = "mineru_business-internal"
    network_id = "network-1"
    common = {
        "State": {"Running": True},
        "HostConfig": {"ReadonlyRootfs": True},
        "NetworkSettings": {"Networks": {network_name: {"NetworkID": network_id}}},
    }
    business = deepcopy(common)
    business.update(
        {
            "Image": "sha256:business",
            "Mounts": [
                {"Type": "bind", "Source": inbox, "Destination": "/srv/mineru-inbox", "RW": True},
                {
                    "Type": "bind",
                    "Source": str(tmp_path / "business-data"),
                    "Destination": "/var/lib/mineru-business",
                    "RW": True,
                },
            ],
            "Config": {
                "Env": [
                    "HF_HUB_OFFLINE=1",
                    "TRANSFORMERS_OFFLINE=1",
                    "HF_DATASETS_OFFLINE=1",
                    "MINERU_BUSINESS_DOCLIB_URL=http://doclib-worker:15980",
                ]
            },
        }
    )
    business["NetworkSettings"]["Ports"] = {"8080/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8088"}]}
    worker = deepcopy(common)
    worker.update(
        {
            "Image": "sha256:worker",
            "Mounts": [
                {"Type": "bind", "Source": inbox, "Destination": "/srv/mineru-inbox", "RW": False},
                {"Type": "bind", "Source": str(tmp_path / "doclib-data"), "Destination": "/var/lib/mineru", "RW": True},
                {"Type": "bind", "Source": str(model_dir), "Destination": "/opt/mineru-models", "RW": False},
                {
                    "Type": "bind",
                    "Source": str(model_manifest),
                    "Destination": "/etc/mineru/model-manifest.json",
                    "RW": False,
                },
            ],
            "Config": {
                "Env": [
                    "MINERU_MODEL_SOURCE=local",
                    "MINERU_MODEL_SMALL_BACKEND=torch",
                    "MINERU_MODEL_VLM_ENGINE=vllm",
                    "HF_HUB_OFFLINE=1",
                    "TRANSFORMERS_OFFLINE=1",
                    "HF_DATASETS_OFFLINE=1",
                    "MINERU_DOCLIB_TCP_ENABLED=true",
                    "MINERU_DOCLIB_TCP_STRICT_PORT=true",
                    "MINERU_LLM_AIDED_FEATURES_TITLE_LEVELING=false",
                    "MINERU_LLM_AIDED_FEATURES_CROSS_PAGE_TABLE_CELL_MERGE=false",
                    "MINERU_EXPECTED_MODEL_MANIFEST_SHA256=manifest-hash",
                    "MINERU_MODEL_VLM_SERVER_URL=",
                ]
            },
        }
    )
    worker["HostConfig"]["DeviceRequests"] = [{"Capabilities": [["gpu"]]}]
    worker["NetworkSettings"]["Ports"] = {"15980/tcp": None}
    return {
        "release": {
            "schema": 3,
            "platform": "linux/amd64",
            "source_revision": "a" * 40,
            "base_image_id": "sha256:base",
            "worker_image_id": "sha256:worker",
            "business_image_id": "sha256:business",
            "model": {"manifest_sha256": "manifest-hash"},
        },
        "business": business,
        "worker": worker,
        "network": {"Id": network_id, "Internal": True},
        "model_dir": model_dir,
        "model_manifest": model_manifest,
        "business_bind": "127.0.0.1",
        "business_port": 8088,
        "capabilities": {"parseable_extensions": [".pdf"], "max_upload_bytes": 1},
        "doclib_status": {
            "running": True,
            "version": "4.0.2",
            "tcp": {"enabled": True, "port": 15980},
            "workers": {"parse_running": True},
            "parse_server": {"remote": {"url": None}},
        },
        "cuda": {"available": True, "count": 1},
        "host_gpu_lines": ["0, NVIDIA GPU, 24000, 550.0"],
    }


def test_runtime_preflight_accepts_isolated_local_deployment(tmp_path: Path) -> None:
    report = check_runtime(**_deployment(tmp_path))
    assert report["result"] == "runtime_preflight_passed"
    assert report["worker_cuda_device_count"] == 1
    assert "models" not in str(report)


def test_runtime_requires_matching_successful_artifact_report(tmp_path: Path) -> None:
    release = _deployment(tmp_path)["release"]
    release_bytes = json.dumps(release).encode()
    report = {
        "schema": 1,
        "result": "artifact_integrity_passed",
        "release_manifest_sha256": hashlib.sha256(release_bytes).hexdigest(),
        "source_revision": release["source_revision"],
        "platform": release["platform"],
        "worker_image_id": release["worker_image_id"],
        "business_image_id": release["business_image_id"],
        "base_image_id": release["base_image_id"],
        "model_manifest_sha256": release["model"]["manifest_sha256"],
        "model_files_verified": 2,
        "wheelhouse_files_verified": 3,
        "web_assets_verified": 4,
    }
    check_artifact_report(report, release, release_bytes)
    for key, value in [
        ("result", "skipped"),
        ("release_manifest_sha256", "0" * 64),
        ("worker_image_id", "sha256:other"),
        ("model_files_verified", 0),
    ]:
        changed = {**report, key: value}
        with pytest.raises(ValueError, match="Artifact verification report"):
            check_artifact_report(changed, release, release_bytes)


def test_runtime_cli_rejects_stale_artifact_report_before_docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    release = _deployment(tmp_path)["release"]
    release_path = tmp_path / "release.json"
    release_path.write_text(json.dumps(release))
    artifact_report = tmp_path / "artifact-verification.json"
    artifact_report.write_text(json.dumps({"schema": 1, "result": "artifact_integrity_passed"}))
    output = tmp_path / "runtime.json"
    monkeypatch.setattr(verify_business_runtime, "_command", lambda *_: pytest.fail("Docker was called"))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "verify_business_runtime.py", "--release", str(release_path),
            "--artifact-report", str(artifact_report),
            "--model-dir", str(tmp_path / "models"),
            "--model-manifest", str(tmp_path / "model-manifest.json"),
            "--business-bind", "127.0.0.1", "--output", str(output),
        ],
    )
    assert verify_business_runtime.main() == 1
    assert not output.exists()


@pytest.mark.parametrize(
    "change, message",
    [
        (lambda x: x["worker"].update(Image="sha256:other"), "image differs"),
        (lambda x: x["business"]["State"].update(Running=False), "not running"),
        (lambda x: x["worker"]["HostConfig"].update(ReadonlyRootfs=False), "read-only"),
        (lambda x: x["worker"]["Mounts"][0].update(RW=True), "read/write mode"),
        (lambda x: x["worker"]["Mounts"][2].update(Source="/tmp/other"), "model mounts differ"),
        (
            lambda x: (
                x["business"]["Mounts"][0].update(Source=str(x["model_dir"])),
                x["worker"]["Mounts"][0].update(Source=str(x["model_dir"])),
            ),
            "directories overlap",
        ),
        (
            lambda x: x["worker"]["Mounts"][1].update(Source=str(x["model_dir"] / "doclib")),
            "directories overlap",
        ),
        (lambda x: x["business"]["Mounts"].append({"Destination": "/opt/mineru-models"}), "unexpected mount"),
        (
            lambda x: x["business"]["Mounts"].append(
                {"Type": "bind", "Source": str(x["model_dir"]), "Destination": "/different-name", "RW": True}
            ),
            "unexpected mount",
        ),
        (lambda x: x["worker"]["Mounts"].append({"Destination": "/extra"}), "unexpected mount"),
        (lambda x: x["business"].pop("Mounts"), "mount inventory is unavailable"),
        (lambda x: x["business"]["Mounts"].pop(), "missing a required mount"),
        (lambda x: x["network"].update(Internal=False), "internal network"),
        (lambda x: x["worker"]["NetworkSettings"]["Ports"].update({"15980/tcp": [{}]}), "published host port"),
        (lambda x: x["business"]["NetworkSettings"]["Ports"]["8080/tcp"][0].update(HostIp="0.0.0.0"), "published port"),
        (lambda x: x["worker"]["HostConfig"].update(DeviceRequests=[]), "GPU device request"),
        (
            lambda x: x["worker"]["Config"]["Env"].append("HF_HUB_OFFLINE=0"),
            "local offline models",
        ),
        (lambda x: x["worker"]["Config"]["Env"].append("HF_DATASETS_OFFLINE=0"), "local offline models"),
        (lambda x: x["worker"]["Config"]["Env"].append("MINERU_MODEL_VLM_SERVER_URL=http://remote"), "local offline"),
        (lambda x: x["doclib_status"]["tcp"].update(port=15981), "Doclib is unavailable"),
        (lambda x: x["doclib_status"].update(parse_server=None), "Doclib is unavailable"),
        (
            lambda x: x["doclib_status"]["parse_server"]["remote"].update(url="http://remote"),
            "remote parsing configured",
        ),
        (lambda x: x["cuda"].update(available=False), "CUDA is unavailable"),
        (lambda x: x.update(host_gpu_lines=[]), "Host NVIDIA GPU"),
    ],
)
def test_runtime_preflight_rejects_unsafe_or_unavailable_state(tmp_path: Path, change: object, message: str) -> None:
    deployment = _deployment(tmp_path)
    change(deployment)
    with pytest.raises(ValueError, match=message):
        check_runtime(**deployment)


@pytest.mark.parametrize("address", ["0.0.0.0", "8.8.8.8", "::", "2001:4860:4860::8888"])
def test_runtime_preflight_rejects_public_bind(address: str) -> None:
    with pytest.raises(ValueError, match="private or loopback"):
        _validated_bind(address)
