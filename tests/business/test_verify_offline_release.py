"""Imported release verification uses real file hashes and simulated Docker inspect records."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from scripts import release_manifest, verify_offline_release

REVISION = "a" * 40
BASE_ID = "sha256:" + "b" * 64
WORKER_ID = "sha256:" + "c" * 64
BUSINESS_ID = "sha256:" + "d" * 64
BASE_LAYER = "sha256:" + "1" * 64


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _image(image_id: str, *, web_hash: str | None = None, arch: str = "amd64") -> dict[str, object]:
    labels = (
        {}
        if image_id == BASE_ID
        else {
            "org.opencontainers.image.revision": REVISION,
            "org.opencontainers.image.base.id": BASE_ID,
        }
    )
    if web_hash:
        labels["io.mineru.business.web.manifest.sha256"] = web_hash
    return {
        "Os": "linux",
        "Architecture": arch,
        "Id": image_id,
        "Config": {"Labels": labels},
        "RootFS": {"Type": "layers", "Layers": [BASE_LAYER] if image_id == BASE_ID else [BASE_LAYER, image_id]},
    }


def _release(tmp_path: Path, *, previous: bool = False) -> dict[str, object]:
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    (wheelhouse / "requirements.lock").write_text("demo==1.0 --hash=sha256:" + "e" * 64)
    (wheelhouse / "demo-1.0-py3-none-any.whl").write_bytes(b"wheel")
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    (model_dir / "weights.bin").write_bytes(b"weights")
    model_manifest = tmp_path / "model-manifest.json"
    model_manifest.write_text(json.dumps({"schema": 1, "files": {"weights.bin": _hash(b"weights")}}))
    web_dist = tmp_path / "web-dist"
    web_dist.mkdir()
    (web_dist / "index.html").write_bytes(b"<h1>MinerU</h1>")
    (web_dist / "asset-manifest.json").write_text(json.dumps({"index.html": _hash(b"<h1>MinerU</h1>")}))
    web_hash = _hash((web_dist / "asset-manifest.json").read_bytes())
    images = {
        "worker": _image(WORKER_ID),
        "business": _image(BUSINESS_ID, web_hash=web_hash),
        "base": _image(BASE_ID),
    }
    previous_release = tmp_path / "previous-release.json" if previous else None
    if previous_release:
        previous_release.write_text('{"schema":3}\n')
    record = release_manifest.build_release_record(
        revision=REVISION,
        **images,
        wheelhouse=wheelhouse,
        model_manifest=model_manifest,
        web_dist=web_dist,
        previous_release=previous_release,
    )
    release = tmp_path / "release.json"
    release.write_text(json.dumps(record))
    return {
        "release_path": release,
        "wheelhouse": wheelhouse,
        "model_manifest": model_manifest,
        "model_dir": model_dir,
        "web_dist": web_dist,
        "previous_release": previous_release,
        **images,
    }


def test_imported_release_artifacts_match_selected_record(tmp_path: Path) -> None:
    artifacts = _release(tmp_path)
    result = verify_offline_release.verify_release(**artifacts)
    assert result["result"] == "artifact_integrity_passed"
    assert result["model_files_verified"] == 1
    assert result["wheelhouse_files_verified"] == 2
    assert result["web_assets_verified"] == 1
    assert result["worker_image_id"] == WORKER_ID
    assert result["release_manifest_sha256"] == _hash(artifacts["release_path"].read_bytes())


def test_imported_release_rejects_wheelhouse_file_excluded_from_build_context(tmp_path: Path) -> None:
    artifacts = _release(tmp_path)
    (artifacts["wheelhouse"] / "transfer-notes.txt").write_text("not copied by Docker")
    with pytest.raises(ValueError, match="Wheelhouse contains files excluded from Docker build context"):
        verify_offline_release.verify_release(**artifacts)


@pytest.mark.parametrize(
    "changed, expected",
    [
        ("model", "Model checksum mismatch"),
        ("wheelhouse", "wheelhouse"),
        ("web", "Web asset differs"),
        ("image", "worker_image_id"),
    ],
)
def test_imported_release_rejects_changed_artifacts(tmp_path: Path, changed: str, expected: str) -> None:
    artifacts = _release(tmp_path)
    if changed == "model":
        (artifacts["model_dir"] / "weights.bin").write_bytes(b"altered")
    elif changed == "wheelhouse":
        (artifacts["wheelhouse"] / "demo-1.0-py3-none-any.whl").write_bytes(b"altered")
    elif changed == "web":
        (artifacts["web_dist"] / "index.html").write_bytes(b"altered")
    else:
        artifacts["worker"] = _image("sha256:" + "f" * 64)
    with pytest.raises(ValueError, match=expected):
        verify_offline_release.verify_release(**artifacts)


def test_imported_release_rejects_wrong_architecture_and_missing_chain(tmp_path: Path) -> None:
    artifacts = _release(tmp_path, previous=True)
    with pytest.raises(ValueError, match="previous release"):
        verify_offline_release.verify_release(**{**artifacts, "previous_release": None})
    bad_worker = _image(WORKER_ID, arch="arm64")
    with pytest.raises(ValueError, match="linux/amd64"):
        verify_offline_release.verify_release(**{**artifacts, "worker": bad_worker})
    artifacts["previous_release"].write_text("changed")
    with pytest.raises(ValueError, match="previous_release_sha256"):
        verify_offline_release.verify_release(**artifacts)


def test_verify_cli_writes_report_only_after_all_checks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    artifacts = _release(tmp_path)
    output = tmp_path / "verification.json"
    images = {"worker:local": artifacts["worker"], "business:local": artifacts["business"], "base:local": artifacts["base"]}
    monkeypatch.setattr(release_manifest, "_image_info", lambda reference: images[reference])
    argv = [
        "verify_offline_release.py",
        "--release",
        str(artifacts["release_path"]),
        "--worker-image",
        "worker:local",
        "--business-image",
        "business:local",
        "--base-image",
        "base:local",
        "--wheelhouse",
        str(artifacts["wheelhouse"]),
        "--model-manifest",
        str(artifacts["model_manifest"]),
        "--model-dir",
        str(artifacts["model_dir"]),
        "--web-dist",
        str(artifacts["web_dist"]),
        "--output",
        str(output),
    ]
    monkeypatch.setattr(sys, "argv", argv)
    assert verify_offline_release.main() == 0
    report = json.loads(output.read_text())
    assert report["result"] == "artifact_integrity_passed"
    assert "weights.bin" not in output.read_text()
    original = output.read_bytes()
    assert verify_offline_release.main() == 1
    assert output.read_bytes() == original
    (artifacts["model_dir"] / "weights.bin").write_bytes(b"wrong")
    output.unlink()
    assert verify_offline_release.main() == 1
    assert not output.exists()


def test_verify_cli_refuses_report_inside_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    artifacts = _release(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "verify_offline_release.py",
            "--release",
            str(artifacts["release_path"]),
            "--worker-image",
            "unused",
            "--business-image",
            "unused",
            "--base-image",
            "unused",
            "--wheelhouse",
            str(artifacts["wheelhouse"]),
            "--model-manifest",
            str(artifacts["model_manifest"]),
            "--model-dir",
            str(artifacts["model_dir"]),
            "--web-dist",
            str(artifacts["web_dist"]),
            "--output",
            str(artifacts["model_dir"] / "verification.json"),
        ],
    )
    assert verify_offline_release.main() == 1
    assert not (artifacts["model_dir"] / "verification.json").exists()
