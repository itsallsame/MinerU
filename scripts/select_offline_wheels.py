"""Select lock entries absent from the immutable base image for wheel download.

The full requirements.lock remains the install contract. This only avoids
re-downloading large distributions already present at the exact locked version
in the inspected base image.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


_REQUIREMENT = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.-]*)==([^\s;\\]+)(?:\s|$)")


def _name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def select(lock_text: str, installed: list[dict[str, str]]) -> tuple[str, int]:
    base = {_name(item["name"]): item["version"] for item in installed}
    if len(base) != len(installed):
        raise ValueError("Base image has duplicate normalized package names")
    header: list[str] = []
    blocks: list[tuple[str, str, list[str]]] = []
    current: list[str] = []
    current_name = ""
    current_version = ""
    seen: set[str] = set()
    for line in lock_text.splitlines(keepends=True):
        match = _REQUIREMENT.match(line)
        if match:
            if current:
                blocks.append((current_name, current_version, current))
            current_name, current_version = _name(match.group(1)), match.group(2)
            if current_name in seen:
                raise ValueError(f"Duplicate locked package: {current_name}")
            seen.add(current_name)
            current = [line]
        elif current:
            current.append(line)
        else:
            header.append(line)
    if current:
        blocks.append((current_name, current_version, current))
    if not blocks:
        raise ValueError("Lock file has no exact package pins")
    missing = [block for name, version, block in blocks if base.get(name) != version]
    return "".join(header + [line for block in missing for line in block]), len(missing)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", required=True, type=Path)
    parser.add_argument("--base-installed", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    inventory = json.loads(args.base_installed.read_text(encoding="utf-8"))
    if not isinstance(inventory, list) or any(
        not isinstance(item, dict) or not isinstance(item.get("name"), str)
        or not isinstance(item.get("version"), str) for item in inventory
    ):
        raise ValueError("Invalid base-image pip inventory")
    filtered, count = select(args.lock.read_text(encoding="utf-8"), inventory)
    with args.output.open("x", encoding="utf-8") as stream:
        stream.write(filtered)
    print(f"Selected {count} locked distributions absent from the base image")


if __name__ == "__main__":
    main()
