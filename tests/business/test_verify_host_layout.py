"""Host mount roots must be independent before or after Compose starts."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.verify_host_layout import check_host_separation, main


def _layout(tmp_path: Path) -> dict[str, Path]:
    paths = {
        "model_dir": tmp_path / "models",
        "model_manifest": tmp_path / "model-manifest.json",
        "doclib_dir": tmp_path / "doclib",
        "shared_documents_dir": tmp_path / "inbox",
        "business_dir": tmp_path / "business",
    }
    for name, path in paths.items():
        if name == "model_manifest":
            path.write_text("{}")
        else:
            path.mkdir()
    return paths


def test_independent_host_mounts_pass(tmp_path: Path) -> None:
    check_host_separation(**_layout(tmp_path))


@pytest.mark.parametrize(
    "name, replacement",
    [
        ("shared_documents_dir", "models"),
        ("shared_documents_dir", "models/uploads"),
        ("model_dir", "inbox/models"),
        ("doclib_dir", "business/doclib"),
        ("business_dir", "inbox"),
    ],
)
def test_overlap_is_rejected(tmp_path: Path, name: str, replacement: str) -> None:
    layout = _layout(tmp_path)
    layout[name] = tmp_path / replacement
    with pytest.raises(ValueError, match="directories overlap"):
        check_host_separation(**layout)


def test_manifest_within_writable_mount_is_rejected(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    layout["model_manifest"] = layout["shared_documents_dir"] / "model-manifest.json"
    with pytest.raises(ValueError, match="manifest must be outside"):
        check_host_separation(**layout)


def test_symlink_alias_is_rejected(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    alias = tmp_path / "inbox-alias"
    alias.symlink_to(layout["shared_documents_dir"], target_is_directory=True)
    layout["model_dir"] = alias
    with pytest.raises(ValueError, match="directories overlap"):
        check_host_separation(**layout)


def test_cli_rejects_missing_mounts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout = _layout(tmp_path)
    layout["doclib_dir"].rmdir()
    args = ["verify_host_layout"]
    for name, path in layout.items():
        args.extend(["--" + name.replace("_", "-"), str(path)])
    monkeypatch.setattr("sys.argv", args)
    assert main() == 1


def test_cli_accepts_independent_existing_mounts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout = _layout(tmp_path)
    args = ["verify_host_layout"]
    for name, path in layout.items():
        args.extend(["--" + name.replace("_", "-"), str(path)])
    monkeypatch.setattr("sys.argv", args)
    assert main() == 0
