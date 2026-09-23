"""Reject overlapping host mounts before starting the isolated business stack."""

from __future__ import annotations

import argparse
import sys
from itertools import combinations
from pathlib import Path


def check_host_separation(
    *, model_dir: Path, model_manifest: Path, doclib_dir: Path, shared_documents_dir: Path, business_dir: Path
) -> None:
    directories = {
        "model": model_dir.resolve(),
        "Doclib data": doclib_dir.resolve(),
        "shared documents": shared_documents_dir.resolve(),
        "business data": business_dir.resolve(),
    }
    for (first_name, first), (second_name, second) in combinations(directories.items(), 2):
        if first.is_relative_to(second) or second.is_relative_to(first):
            raise ValueError(f"Host mount directories overlap: {first_name} and {second_name}")
    manifest = model_manifest.resolve()
    if any(manifest.is_relative_to(directory) for directory in directories.values()):
        raise ValueError("Model manifest must be outside every mounted directory")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--model-manifest", type=Path, required=True)
    parser.add_argument("--doclib-dir", type=Path, required=True)
    parser.add_argument("--shared-documents-dir", type=Path, required=True)
    parser.add_argument("--business-dir", type=Path, required=True)
    args = parser.parse_args()
    directories = (args.model_dir, args.doclib_dir, args.shared_documents_dir, args.business_dir)
    try:
        for directory in directories:
            if directory.is_symlink() or not directory.is_dir():
                raise ValueError(f"Host mount must be an existing real directory: {directory}")
        if args.model_manifest.is_symlink() or not args.model_manifest.is_file():
            raise ValueError("Model manifest must be an existing real file")
        check_host_separation(
            model_dir=args.model_dir,
            model_manifest=args.model_manifest,
            doclib_dir=args.doclib_dir,
            shared_documents_dir=args.shared_documents_dir,
            business_dir=args.business_dir,
        )
    except ValueError as error:
        print(f"Host layout error: {error}", file=sys.stderr)
        return 1
    print("Host mount layout verified; model artifacts do not overlap writable data mounts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
