"""Read an imported amd64 NVIDIA/vLLM base image without pulling or using the network."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

IMAGE_ID_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
VERSION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.!+_-]*\Z")
PACKAGES = ("torch", "torchvision", "vllm")


def _run(*args: str) -> str:
    return subprocess.run(args, capture_output=True, text=True, check=True).stdout.strip()


def inspect_base(*, image: str, expected_id: str) -> tuple[str, str]:
    if not image or not IMAGE_ID_RE.fullmatch(expected_id):
        raise ValueError("Base image reference and immutable SHA-256 ID are required")
    metadata = json.loads(_run("docker", "image", "inspect", image, "--format", "{{json .}}"))
    if not isinstance(metadata, dict) or metadata.get("Os") != "linux" or metadata.get("Architecture") != "amd64":
        raise ValueError("Imported base image must be linux/amd64")
    if metadata.get("Id") != expected_id:
        raise ValueError("Imported base image ID differs from the selected immutable ID")
    probe = (
        "import json, sys; from importlib.metadata import version; "
        "print(json.dumps({'python': f'{sys.version_info.major}.{sys.version_info.minor}', "
        "'torch': version('torch'), 'torchvision': version('torchvision'), 'vllm': version('vllm')}))"
    )
    result: Any = json.loads(
        _run(
            "docker",
            "run",
            "--rm",
            "--pull=never",
            "--network=none",
            "--read-only",
            "--entrypoint",
            "python3",
            expected_id,
            "-c",
            probe,
        )
    )
    if not isinstance(result, dict) or set(result) != {"python", *PACKAGES}:
        raise ValueError("Base image returned an invalid Python/package inventory")
    host_python = f"{sys.version_info.major}.{sys.version_info.minor}"
    if result["python"] != host_python:
        raise ValueError(f"Preparation Python ABI {host_python} differs from base image {result['python']}")
    if any(not isinstance(result[name], str) or not VERSION_RE.fullmatch(result[name]) for name in PACKAGES):
        raise ValueError("Base image returned an invalid installed package version")
    constraints = "".join(f"{name}=={result[name]}\n" for name in PACKAGES)
    return expected_id, constraints


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-image", required=True)
    parser.add_argument("--expected-id", required=True)
    destination = parser.add_mutually_exclusive_group(required=True)
    destination.add_argument("--output", type=Path, help="Create a new constraints file; never overwrite")
    destination.add_argument("--verify", type=Path, help="Verify an existing constraints file exactly")
    args = parser.parse_args()
    try:
        image_id, constraints = inspect_base(image=args.base_image, expected_id=args.expected_id)
        selected = args.output or args.verify
        if args.output is not None:
            if selected.is_symlink():
                raise ValueError("Constraints output must not be a symlink")
            with selected.open("x", encoding="ascii") as stream:
                stream.write(constraints)
            print(f"Base constraints created for {image_id}")
        else:
            if not selected.is_file() or selected.is_symlink() or selected.read_text(encoding="ascii") != constraints:
                raise ValueError("Constraints differ from the selected base image's installed versions")
            print(f"Base constraints verified for {image_id}")
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
        print(f"Base inspection error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
