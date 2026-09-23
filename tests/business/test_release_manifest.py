"""Pure release-record tests; real Docker architecture is an acceptance gate."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("release_manifest", ROOT / "scripts" / "release_manifest.py")
assert SPEC is not None and SPEC.loader is not None
release_manifest = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release_manifest)

REVISION = "a" * 40
IMAGE_ID = "sha256:" + "b" * 64
BASE_ID = "sha256:" + "c" * 64


def _image(image_id: str, *, revision: str | None = None, arch: str = "amd64") -> dict[str, object]:
    labels = {"org.opencontainers.image.revision": revision} if revision else {}
    if revision:
        labels["org.opencontainers.image.base.id"] = BASE_ID
    return {"Os": "linux", "Architecture": arch, "Id": image_id, "Config": {"Labels": labels}}


def _artifacts(tmp_path: Path) -> tuple[Path, Path]:
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    (wheelhouse / "requirements.lock").write_text("demo==1.0 --hash=sha256:" + "d" * 64)
    (wheelhouse / "demo-1.0-py3-none-any.whl").write_bytes(b"wheel")
    model_manifest = tmp_path / "model-manifest.json"
    model_manifest.write_text(json.dumps({"schema": 1, "files": {"weights.bin": "e" * 64}}))
    return wheelhouse, model_manifest


def test_release_record_binds_code_image_deps_and_models(tmp_path: Path) -> None:
    wheelhouse, model_manifest = _artifacts(tmp_path)
    record = release_manifest.build_release_record(
        revision=REVISION,
        worker=_image(IMAGE_ID, revision=REVISION),
        base=_image(BASE_ID),
        wheelhouse=wheelhouse,
        model_manifest=model_manifest,
    )
    assert record["source_revision"] == REVISION
    assert record["worker_image_id"] == IMAGE_ID
    assert record["model"]["file_count"] == 1
    assert "requirements.lock" in record["wheelhouse"]["files"]


def test_release_record_rejects_wrong_architecture_and_revision(tmp_path: Path) -> None:
    wheelhouse, model_manifest = _artifacts(tmp_path)
    args = {
        "revision": REVISION,
        "worker": _image(IMAGE_ID, revision=REVISION),
        "base": _image(BASE_ID),
        "wheelhouse": wheelhouse,
        "model_manifest": model_manifest,
    }
    with pytest.raises(ValueError, match="linux/amd64"):
        release_manifest.build_release_record(**{**args, "worker": _image(IMAGE_ID, revision=REVISION, arch="arm64")})
    with pytest.raises(ValueError, match="revision label"):
        release_manifest.build_release_record(**{**args, "worker": _image(IMAGE_ID, revision="f" * 40)})
    wrong_base = _image(IMAGE_ID, revision=REVISION)
    wrong_base["Config"]["Labels"]["org.opencontainers.image.base.id"] = "sha256:" + "f" * 64
    with pytest.raises(ValueError, match="base label"):
        release_manifest.build_release_record(**{**args, "worker": wrong_base})


def test_release_record_rejects_incomplete_artifacts(tmp_path: Path) -> None:
    wheelhouse, model_manifest = _artifacts(tmp_path)
    (wheelhouse / "demo-1.0-py3-none-any.whl").unlink()
    with pytest.raises(ValueError, match="no wheel files"):
        release_manifest.build_release_record(
            revision=REVISION,
            worker=_image(IMAGE_ID, revision=REVISION),
            base=_image(BASE_ID),
            wheelhouse=wheelhouse,
            model_manifest=model_manifest,
        )
