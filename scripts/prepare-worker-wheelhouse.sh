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
if [ -z "${MINERU_BASE_CONSTRAINTS:-}" ] || [ ! -f "$MINERU_BASE_CONSTRAINTS" ]; then
  echo 'MINERU_BASE_CONSTRAINTS must name a file pinning the base image torch, torchvision and vllm versions.' >&2
  exit 1
fi
for dependency in torch torchvision vllm; do
  if ! grep -Eq "^${dependency}==[0-9]" "$MINERU_BASE_CONSTRAINTS"; then
    echo "Missing exact ${dependency} pin in base constraints." >&2
    exit 1
  fi
done

mkdir -p wheelhouse
uv pip compile pyproject.toml docker/worker/build-requirements.in \
  --extra full --no-emit-package mineru --generate-hashes \
  --constraint "$MINERU_BASE_CONSTRAINTS" \
  --output-file wheelhouse/requirements.lock
python3 -m pip download --require-hashes --only-binary=:all: \
  --dest wheelhouse --requirement wheelhouse/requirements.lock

echo 'Wheelhouse prepared. Verify Python ABI, CUDA/vLLM compatibility and hashes before transfer.'
