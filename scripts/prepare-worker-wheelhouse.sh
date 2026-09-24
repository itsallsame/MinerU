#!/bin/sh
# Run only on the connected Linux amd64 preparation host with the SAME Python
# major/minor version as the imported production base image.
set -eu

if [ "$(uname -s)" != Linux ] || [ "$(uname -m)" != x86_64 ]; then
  echo 'Wheelhouse preparation requires a Linux x86_64 host.' >&2
  exit 1
fi

if [ ! -f pyproject.toml ] || [ ! -f docker/worker/build-requirements.in ]; then
  echo 'Run this script from the MinerU fork root.' >&2
  exit 1
fi

command -v uv >/dev/null 2>&1 || { echo 'uv is required.' >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo 'python3 is required.' >&2; exit 1; }
if [ -z "${MINERU_BASE_IMAGE:-}" ] || [ -z "${MINERU_BASE_IMAGE_ID:-}" ]; then
  echo 'MINERU_BASE_IMAGE and MINERU_BASE_IMAGE_ID must select one imported immutable base image.' >&2
  exit 1
fi
if [ -z "${MINERU_BASE_CONSTRAINTS:-}" ] || [ ! -f "$MINERU_BASE_CONSTRAINTS" ]; then
  echo 'MINERU_BASE_CONSTRAINTS must name a file pinning the base image torch, torchvision and vllm versions.' >&2
  exit 1
fi
python3 -m scripts.inspect_worker_base \
  --base-image "$MINERU_BASE_IMAGE" --expected-id "$MINERU_BASE_IMAGE_ID" \
  --verify "$MINERU_BASE_CONSTRAINTS"

if [ -e wheelhouse ] || [ -L wheelhouse ]; then
  echo 'wheelhouse already exists; preserve it and prepare this release in a fresh source checkout.' >&2
  exit 1
fi
stage=$(mktemp -d .wheelhouse-stage.XXXXXX)
echo "Preparing wheelhouse in $stage; a failed run leaves it for inspection." >&2
uv pip compile pyproject.toml docker/worker/build-requirements.in \
  --python "$(command -v python3)" --extra full --no-emit-package mineru --generate-hashes \
  --constraint "$MINERU_BASE_CONSTRAINTS" \
  --output-file "$stage/requirements.lock"
python3 -m pip download --require-hashes --only-binary=:all: \
  --dest "$stage" --requirement "$stage/requirements.lock"
if [ -e wheelhouse ] || [ -L wheelhouse ]; then
  echo 'wheelhouse appeared during preparation; leaving the staged files untouched.' >&2
  exit 1
fi
mv "$stage" wheelhouse

echo 'Wheelhouse prepared. Verify Python ABI, CUDA/vLLM compatibility and hashes before transfer.'
