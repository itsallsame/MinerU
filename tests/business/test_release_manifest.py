"""Pure release-record tests; real Docker architecture is an acceptance gate."""

from __future__ import annotations

import importlib.util
import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("release_manifest", ROOT / "scripts" / "release_manifest.py")
assert SPEC is not None and SPEC.loader is not None
release_manifest = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release_manifest)

REVISION = "a" * 40
IMAGE_ID = "sha256:" + "b" * 64
BUSINESS_ID = "sha256:" + "d" * 64
BASE_ID = "sha256:" + "c" * 64
BASE_LAYER = "sha256:" + "1" * 64


def _image(
    image_id: str, *, revision: str | None = None, arch: str = "amd64", web_sha256: str | None = None,
) -> dict[str, object]:
    labels = {"org.opencontainers.image.revision": revision} if revision else {}
    if revision:
        labels["org.opencontainers.image.base.id"] = BASE_ID
    if web_sha256:
        labels["io.mineru.business.web.manifest.sha256"] = web_sha256
    layers = [BASE_LAYER] if image_id == BASE_ID else [BASE_LAYER, image_id]
    return {
        "Os": "linux", "Architecture": arch, "Id": image_id,
        "Config": {"Labels": labels}, "RootFS": {"Type": "layers", "Layers": layers},
    }


def _artifacts(tmp_path: Path) -> tuple[Path, Path, Path, str]:
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    (wheelhouse / "requirements.lock").write_text("demo==1.0 --hash=sha256:" + "d" * 64)
    (wheelhouse / "demo-1.0-py3-none-any.whl").write_bytes(b"wheel")
    model_manifest = tmp_path / "model-manifest.json"
    model_manifest.write_text(json.dumps({"schema": 1, "files": {"weights.bin": "e" * 64}}))
    web_dist = tmp_path / "web-dist"
    web_dist.mkdir()
    index = web_dist / "index.html"
    index.write_text("<title>MinerU</title>")
    web_manifest = web_dist / "asset-manifest.json"
    web_manifest.write_text(json.dumps({"index.html": hashlib.sha256(index.read_bytes()).hexdigest()}))
    return wheelhouse, model_manifest, web_dist, hashlib.sha256(web_manifest.read_bytes()).hexdigest()


def test_release_record_binds_code_image_deps_and_models(tmp_path: Path) -> None:
    wheelhouse, model_manifest, web_dist, web_sha256 = _artifacts(tmp_path)
    record = release_manifest.build_release_record(
        revision=REVISION,
        worker=_image(IMAGE_ID, revision=REVISION),
        business=_image(BUSINESS_ID, revision=REVISION, web_sha256=web_sha256),
        base=_image(BASE_ID),
        wheelhouse=wheelhouse,
        model_manifest=model_manifest,
        web_dist=web_dist,
    )
    assert record["source_revision"] == REVISION
    assert record["schema"] == 3
    assert record["worker_image_id"] == IMAGE_ID
    assert record["business_image_id"] == BUSINESS_ID
    assert record["model"]["file_count"] == 1
    assert "requirements.lock" in record["wheelhouse"]["files"]
    assert record["business_web"]["manifest_sha256"] == web_sha256


def test_release_record_rejects_wrong_architecture_and_revision(tmp_path: Path) -> None:
    wheelhouse, model_manifest, web_dist, web_sha256 = _artifacts(tmp_path)
    args = {
        "revision": REVISION,
        "worker": _image(IMAGE_ID, revision=REVISION),
        "business": _image(BUSINESS_ID, revision=REVISION, web_sha256=web_sha256),
        "base": _image(BASE_ID),
        "wheelhouse": wheelhouse,
        "model_manifest": model_manifest,
        "web_dist": web_dist,
    }
    with pytest.raises(ValueError, match="linux/amd64"):
        release_manifest.build_release_record(**{**args, "worker": _image(IMAGE_ID, revision=REVISION, arch="arm64")})
    with pytest.raises(ValueError, match="revision label"):
        release_manifest.build_release_record(**{**args, "worker": _image(IMAGE_ID, revision="f" * 40)})
    wrong_base = _image(IMAGE_ID, revision=REVISION)
    wrong_base["Config"]["Labels"]["org.opencontainers.image.base.id"] = "sha256:" + "f" * 64
    with pytest.raises(ValueError, match="base label"):
        release_manifest.build_release_record(**{**args, "worker": wrong_base})
    with pytest.raises(ValueError, match="linux/amd64"):
        release_manifest.build_release_record(**{
            **args, "business": _image(BUSINESS_ID, revision=REVISION, arch="arm64", web_sha256=web_sha256)
        })
    with pytest.raises(ValueError, match="business image revision label"):
        release_manifest.build_release_record(**{**args, "business": _image(BUSINESS_ID, revision="f" * 40)})
    wrong_business_base = _image(BUSINESS_ID, revision=REVISION, web_sha256=web_sha256)
    wrong_business_base["Config"]["Labels"]["org.opencontainers.image.base.id"] = "sha256:" + "f" * 64
    with pytest.raises(ValueError, match="business image base label"):
        release_manifest.build_release_record(**{**args, "business": wrong_business_base})
    with pytest.raises(ValueError, match="distinct IDs"):
        release_manifest.build_release_record(**{
            **args, "business": _image(IMAGE_ID, revision=REVISION, web_sha256=web_sha256)
        })


def test_release_record_rejects_forged_base_label_without_matching_layers(tmp_path: Path) -> None:
    wheelhouse, model_manifest, web_dist, web_sha256 = _artifacts(tmp_path)
    args = {
        "revision": REVISION,
        "worker": _image(IMAGE_ID, revision=REVISION),
        "business": _image(BUSINESS_ID, revision=REVISION, web_sha256=web_sha256),
        "base": _image(BASE_ID),
        "wheelhouse": wheelhouse,
        "model_manifest": model_manifest,
        "web_dist": web_dist,
    }
    wrong_worker = _image(IMAGE_ID, revision=REVISION)
    wrong_worker["RootFS"]["Layers"][0] = "sha256:" + "2" * 64
    with pytest.raises(ValueError, match="not built on the inspected base"):
        release_manifest.build_release_record(**{**args, "worker": wrong_worker})
    wrong_business = _image(BUSINESS_ID, revision=REVISION, web_sha256=web_sha256)
    wrong_business["RootFS"]["Layers"][0] = "sha256:" + "2" * 64
    with pytest.raises(ValueError, match="not built on the inspected base"):
        release_manifest.build_release_record(**{**args, "business": wrong_business})
    missing_layers = _image(IMAGE_ID, revision=REVISION)
    del missing_layers["RootFS"]
    with pytest.raises(ValueError, match="no verifiable RootFS"):
        release_manifest.build_release_record(**{**args, "worker": missing_layers})


def test_release_record_rejects_unbound_or_changed_web_assets(tmp_path: Path) -> None:
    wheelhouse, model_manifest, web_dist, web_sha256 = _artifacts(tmp_path)
    args = {
        "revision": REVISION,
        "worker": _image(IMAGE_ID, revision=REVISION),
        "business": _image(BUSINESS_ID, revision=REVISION, web_sha256=web_sha256),
        "base": _image(BASE_ID),
        "wheelhouse": wheelhouse,
        "model_manifest": model_manifest,
        "web_dist": web_dist,
    }
    with pytest.raises(ValueError, match="Web asset manifest label"):
        release_manifest.build_release_record(**{**args, "business": _image(BUSINESS_ID, revision=REVISION)})
    (web_dist / "index.html").write_text("changed")
    with pytest.raises(ValueError, match="differs from its manifest"):
        release_manifest.build_release_record(**args)


def test_release_record_rejects_incomplete_artifacts(tmp_path: Path) -> None:
    wheelhouse, model_manifest, web_dist, web_sha256 = _artifacts(tmp_path)
    (wheelhouse / "demo-1.0-py3-none-any.whl").unlink()
    with pytest.raises(ValueError, match="no wheel files"):
        release_manifest.build_release_record(
            revision=REVISION,
            worker=_image(IMAGE_ID, revision=REVISION),
            business=_image(BUSINESS_ID, revision=REVISION, web_sha256=web_sha256),
            base=_image(BASE_ID),
            wheelhouse=wheelhouse,
            model_manifest=model_manifest,
            web_dist=web_dist,
        )


def test_release_cli_requires_and_records_both_code_images(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wheelhouse, model_manifest, web_dist, web_sha256 = _artifacts(tmp_path)
    output = tmp_path / "release.json"
    images = {
        "worker:test": _image(IMAGE_ID, revision=REVISION),
        "business:test": _image(BUSINESS_ID, revision=REVISION, web_sha256=web_sha256),
        "base:test": _image(BASE_ID),
    }

    def command(*args: str) -> str:
        if args == ("git", "status", "--porcelain"):
            return ""
        if args == ("git", "rev-parse", "HEAD"):
            return REVISION
        if args[:3] == ("docker", "image", "inspect"):
            return json.dumps(images[args[3]])
        raise AssertionError(f"Unexpected command: {args}")

    monkeypatch.setattr(release_manifest, "_command", command)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "release_manifest.py",
            "--worker-image",
            "worker:test",
            "--business-image",
            "business:test",
            "--base-image",
            "base:test",
            "--wheelhouse",
            str(wheelhouse),
            "--model-manifest",
            str(model_manifest),
            "--web-dist",
            str(web_dist),
            "--output",
            str(output),
        ],
    )
    assert release_manifest.main() == 0
    record = json.loads(output.read_text())
    assert record["worker_image_id"] == IMAGE_ID
    assert record["business_image_id"] == BUSINESS_ID
    assert record["business_web"]["manifest_sha256"] == web_sha256


def test_release_output_cannot_clobber_existing_artifacts(tmp_path: Path) -> None:
    wheelhouse, model_manifest, web_dist, _ = _artifacts(tmp_path)
    previous_release = tmp_path / "previous-release.json"
    previous_release.write_bytes(b"previous release")
    output = tmp_path / "release.json"
    output.write_bytes(b"current release")

    for target in (output, model_manifest, previous_release):
        original = target.read_bytes()
        with pytest.raises(ValueError, match="already exists"):
            release_manifest._validate_output_path(
                target, wheelhouse=wheelhouse, model_manifest=model_manifest,
                web_dist=web_dist, previous_release=previous_release,
            )
        assert target.read_bytes() == original

    for target in (wheelhouse / "new-release.json", web_dist / "new-release.json"):
        with pytest.raises(ValueError, match="outside selected release artifacts"):
            release_manifest._validate_output_path(
                target, wheelhouse=wheelhouse, model_manifest=model_manifest,
                web_dist=web_dist, previous_release=previous_release,
            )
        assert not target.exists()


def test_release_output_cannot_follow_symlink_to_prior_release(tmp_path: Path) -> None:
    wheelhouse, model_manifest, web_dist, _ = _artifacts(tmp_path)
    previous_release = tmp_path / "previous-release.json"
    previous_release.write_bytes(b"previous release")
    output = tmp_path / "release.json"
    output.symlink_to(previous_release)

    with pytest.raises(ValueError, match="already exists"):
        release_manifest._validate_output_path(
            output, wheelhouse=wheelhouse, model_manifest=model_manifest,
            web_dist=web_dist, previous_release=previous_release,
        )
    assert previous_release.read_bytes() == b"previous release"


def test_release_writer_creates_once_without_overwriting(tmp_path: Path) -> None:
    output = tmp_path / "nested" / "release.json"
    record = {"schema": 3, "source_revision": REVISION}
    release_manifest._write_new_release(output, record)
    original = output.read_bytes()
    assert json.loads(original) == record

    with pytest.raises(FileExistsError):
        release_manifest._write_new_release(output, {"schema": 999})
    assert output.read_bytes() == original
    assert not list(output.parent.glob(f".{output.name}.*"))
