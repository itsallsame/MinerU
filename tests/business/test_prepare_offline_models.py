"""Pinned model preparation uses tiny fake repositories, never the network."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest

from mineru.model.registry import MINERU_2_5_PRO_2605_1_2B, MINERU_4_MODELS_TORCH
from scripts import offline_package, prepare_offline_models

SMALL_SHA = "a" * 40
VLM_SHA = "b" * 40


def _repo_files() -> dict[str, list[str]]:
    small = []
    for required in MINERU_4_MODELS_TORCH.required_paths():
        path = required.relative_path
        small.append(f"{path}/weights.bin" if Path(path).suffix == "" else path)
    return {
        MINERU_4_MODELS_TORCH.repos["huggingface"]: small,
        MINERU_2_5_PRO_2605_1_2B.repos["huggingface"]: ["config.json", "weights.safetensors"],
    }


class FakeApi:
    def __init__(self, files: dict[str, list[str]]) -> None:
        self.files = files
        self.calls: list[tuple[str, str]] = []
        self.sha_override: str | None = None

    def model_info(self, repo_id: str, *, revision: str) -> SimpleNamespace:
        self.calls.append((repo_id, revision))
        return SimpleNamespace(
            sha=self.sha_override or revision,
            siblings=[SimpleNamespace(rfilename=name) for name in self.files[repo_id]],
        )


def _fake_download(
    files: dict[str, list[str]],
    *,
    omit: str | None = None,
    extra: bool = False,
) -> Callable[..., str]:
    def download(repo_id: str, *, revision: str, local_dir: str, allow_patterns: list[str] | None) -> str:
        assert revision in (SMALL_SHA, VLM_SHA)
        root = Path(local_dir)
        selected = prepare_offline_models.filter_repo_objects(files[repo_id], allow_patterns=allow_patterns)
        for name in selected:
            if name == omit:
                continue
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(f"{repo_id}/{name}/{revision}".encode())
        metadata = root / ".cache" / "huggingface"
        metadata.mkdir(parents=True)
        (metadata / "metadata").write_text("ignored")
        if extra:
            (root / "unexpected.bin").write_bytes(b"wrong")
        return local_dir

    return download


def _prepare(tmp_path: Path, api: FakeApi) -> tuple[Path, Path]:
    model_dir = tmp_path / "models-v1"
    manifest = tmp_path / "model-manifest-v1.json"
    prepare_offline_models.prepare(
        model_dir,
        manifest,
        small_revision=SMALL_SHA,
        vlm_revision=VLM_SHA,
        api=api,
    )
    return model_dir, manifest


def test_exact_commits_are_downloaded_and_hashed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    files = _repo_files()
    api = FakeApi(files)
    monkeypatch.setattr(prepare_offline_models, "snapshot_download", _fake_download(files))
    model_dir, manifest = _prepare(tmp_path, api)

    assert api.calls == [
        (MINERU_4_MODELS_TORCH.repos["huggingface"], SMALL_SHA),
        (MINERU_2_5_PRO_2605_1_2B.repos["huggingface"], VLM_SHA),
    ]
    lock = json.loads((model_dir / ".mineru_source_lock.json").read_text())
    assert [item["revision"] for item in lock["repos"]] == [SMALL_SHA, VLM_SHA]
    assert offline_package.verify_manifest(model_dir, manifest) == 13
    assert not list(model_dir.rglob(".cache"))
    assert (model_dir / MINERU_2_5_PRO_2605_1_2B.local_name / ".mineru_complete").is_file()
    with pytest.raises(ValueError, match="immutable"):
        _prepare(tmp_path, api)


@pytest.mark.parametrize("revision", ["main", "A" * 40, "abc", "a" * 39])
def test_mutable_or_invalid_revision_rejected_without_network(tmp_path: Path, revision: str) -> None:
    api = FakeApi(_repo_files())
    with pytest.raises(ValueError, match="exact lowercase"):
        prepare_offline_models.prepare(
            tmp_path / "models",
            tmp_path / "manifest.json",
            small_revision=revision,
            vlm_revision=VLM_SHA,
            api=api,
        )
    assert not api.calls
    assert not (tmp_path / "models").exists()


@pytest.mark.parametrize("failure", ["wrong-sha", "missing", "extra", "remote-missing"])
def test_bad_source_never_publishes_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    files = _repo_files()
    api = FakeApi(files)
    if failure == "wrong-sha":
        api.sha_override = "c" * 40
    if failure == "remote-missing":
        files[MINERU_4_MODELS_TORCH.repos["huggingface"]].pop()
    monkeypatch.setattr(
        prepare_offline_models,
        "snapshot_download",
        _fake_download(
            files,
            omit=files[MINERU_4_MODELS_TORCH.repos["huggingface"]][0] if failure == "missing" else None,
            extra=failure == "extra",
        ),
    )
    with pytest.raises(ValueError):
        _prepare(tmp_path, api)
    assert not (tmp_path / "models-v1").exists()
    assert not (tmp_path / "model-manifest-v1.json").exists()


def test_existing_manifest_is_not_replaced(tmp_path: Path) -> None:
    manifest = tmp_path / "model-manifest-v1.json"
    manifest.write_text("preserve")
    api = FakeApi(_repo_files())
    with pytest.raises(ValueError, match="immutable"):
        _prepare(tmp_path, api)
    assert manifest.read_text() == "preserve"
    assert not api.calls


def test_symlink_parent_rejected(tmp_path: Path) -> None:
    actual = tmp_path / "actual"
    actual.mkdir()
    link = tmp_path / "link"
    link.symlink_to(actual, target_is_directory=True)
    api = FakeApi(_repo_files())
    with pytest.raises(ValueError, match="symlinks"):
        prepare_offline_models.prepare(
            link / "models",
            tmp_path / "manifest.json",
            small_revision=SMALL_SHA,
            vlm_revision=VLM_SHA,
            api=api,
        )
    assert not api.calls
