"""Base constraints must come from the exact imported amd64 image, not a manual guess."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from scripts import inspect_worker_base

IMAGE_ID = "sha256:" + "a" * 64


def _mock_docker(
    monkeypatch: pytest.MonkeyPatch,
    *,
    architecture: str = "amd64",
    image_id: str = IMAGE_ID,
    python: str | None = None,
    package_versions: dict[str, str] | None = None,
) -> list[tuple[str, ...]]:
    calls: list[tuple[str, ...]] = []
    versions = package_versions or {"torch": "2.9.0+cu130", "torchvision": "0.24.0+cu130", "vllm": "0.21.0"}

    def fake_run(*args: str) -> str:
        calls.append(args)
        if args[:3] == ("docker", "image", "inspect"):
            return json.dumps({"Os": "linux", "Architecture": architecture, "Id": image_id})
        assert args[:2] == ("docker", "run")
        return json.dumps({"python": python or f"{sys.version_info.major}.{sys.version_info.minor}", **versions})

    monkeypatch.setattr(inspect_worker_base, "_run", fake_run)
    return calls


def test_inspect_pins_exact_installed_base_and_runs_without_network(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _mock_docker(monkeypatch)
    image_id, constraints = inspect_worker_base.inspect_base(image="vllm:local", expected_id=IMAGE_ID)
    assert image_id == IMAGE_ID
    assert constraints == "torch==2.9.0+cu130\ntorchvision==0.24.0+cu130\nvllm==0.21.0\n"
    assert calls[0] == ("docker", "image", "inspect", "vllm:local", "--format", "{{json .}}")
    assert "--pull=never" in calls[1]
    assert "--network=none" in calls[1]
    assert "--read-only" in calls[1]
    assert calls[1][calls[1].index("--entrypoint") + 1] == "python3"
    assert calls[1][calls[1].index("--entrypoint") + 2] == IMAGE_ID


@pytest.mark.parametrize(
    "change, error",
    [
        ({"architecture": "arm64"}, "linux/amd64"),
        ({"image_id": "sha256:" + "b" * 64}, "immutable ID"),
        ({"python": "3.10"}, "Python ABI"),
        ({"package_versions": {"torch": "2.9", "torchvision": "0.24"}}, "invalid Python/package inventory"),
        ({"package_versions": {"torch": "2.9", "torchvision": "0.24", "vllm": "bad version"}}, "invalid installed"),
    ],
)
def test_inspect_rejects_mismatched_base(monkeypatch: pytest.MonkeyPatch, change: dict, error: str) -> None:
    _mock_docker(monkeypatch, **change)
    with pytest.raises(ValueError, match=error):
        inspect_worker_base.inspect_base(image="vllm:local", expected_id=IMAGE_ID)


def test_cli_creates_once_and_refuses_drift(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _mock_docker(monkeypatch)
    output = tmp_path / "constraints.txt"
    monkeypatch.setattr(
        sys, "argv", ["inspect", "--base-image", "vllm:local", "--expected-id", IMAGE_ID, "--output", str(output)]
    )
    assert inspect_worker_base.main() == 0
    original = output.read_text(encoding="ascii")
    assert inspect_worker_base.main() == 1
    assert output.read_text(encoding="ascii") == original
    monkeypatch.setattr(
        sys, "argv", ["inspect", "--base-image", "vllm:local", "--expected-id", IMAGE_ID, "--verify", str(output)]
    )
    assert inspect_worker_base.main() == 0
    output.write_text("torch==0\n", encoding="ascii")
    assert inspect_worker_base.main() == 1
    assert "Constraints differ" in capsys.readouterr().err
