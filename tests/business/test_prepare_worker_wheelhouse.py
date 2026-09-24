"""Preparation must never mix a new Linux wheel lock with an older wheelhouse."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "prepare-worker-wheelhouse.sh"


def _run_preparation(tmp_path: Path, *, fail_uv: bool = False, fail_pip: bool = False) -> subprocess.CompletedProcess[str]:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'mineru'\n")
    build_requirements = tmp_path / "docker" / "worker" / "build-requirements.in"
    build_requirements.parent.mkdir(parents=True)
    build_requirements.write_text("build\n")
    constraints = tmp_path / "base-constraints.txt"
    constraints.write_text("torch==2.0\ntorchvision==0.15\nvllm==0.1\n")
    mock_bin = tmp_path / "mock-bin"
    mock_bin.mkdir()
    scripts = {
        "uname": """#!/bin/sh
case "$1" in
  -s) echo Linux ;;
  -m) echo x86_64 ;;
esac
""",
        "uv": """#!/bin/sh
if [ "${TEST_FAIL_UV:-}" = 1 ]; then exit 9; fi
while [ "$#" -gt 0 ]; do
  if [ "$1" = --output-file ]; then shift; output=$1; fi
  shift
done
printf 'demo==1.0 --hash=sha256:%064d\\n' 1 > "$output"
""",
        "python3": """#!/bin/sh
if [ "${TEST_FAIL_PIP:-}" = 1 ]; then exit 8; fi
while [ "$#" -gt 0 ]; do
  if [ "$1" = --dest ]; then shift; destination=$1; fi
  shift
done
printf wheel > "$destination/demo-1.0-py3-none-any.whl"
""",
    }
    for name, source in scripts.items():
        executable = mock_bin / name
        executable.write_text(source)
        executable.chmod(0o755)
    return subprocess.run(
        ["sh", str(SCRIPT)], cwd=tmp_path, capture_output=True, text=True, check=False,
        env={**os.environ, "PATH": f"{mock_bin}:{os.environ['PATH']}",
             "MINERU_BASE_CONSTRAINTS": str(constraints),
             "TEST_FAIL_UV": "1" if fail_uv else "0", "TEST_FAIL_PIP": "1" if fail_pip else "0"},
    )


def test_existing_wheelhouse_is_not_reused_or_overwritten(tmp_path: Path) -> None:
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    old_wheel = wheelhouse / "old-1.0-py3-none-any.whl"
    old_wheel.write_bytes(b"old-wheel")
    result = _run_preparation(tmp_path)
    assert result.returncode != 0
    assert "wheelhouse already exists" in result.stderr
    assert old_wheel.read_bytes() == b"old-wheel"
    assert not (wheelhouse / "requirements.lock").exists()
    assert not list(tmp_path.glob(".wheelhouse-stage.*"))


@pytest.mark.parametrize("failure", ["compile", "download"])
def test_failed_prepare_never_publishes_partial_wheelhouse(tmp_path: Path, failure: str) -> None:
    result = _run_preparation(tmp_path, fail_uv=failure == "compile", fail_pip=failure == "download")
    assert result.returncode != 0
    assert not (tmp_path / "wheelhouse").exists()
    stages = list(tmp_path.glob(".wheelhouse-stage.*"))
    assert len(stages) == 1
    assert (stages[0] / "requirements.lock").is_file() == (failure == "download")
    assert not list(stages[0].glob("*.whl"))


def test_success_publishes_only_new_lock_and_wheel(tmp_path: Path) -> None:
    result = _run_preparation(tmp_path)
    assert result.returncode == 0, result.stderr
    assert {path.name for path in (tmp_path / "wheelhouse").iterdir()} == {
        "requirements.lock", "demo-1.0-py3-none-any.whl",
    }
    assert not list(tmp_path.glob(".wheelhouse-stage.*"))
