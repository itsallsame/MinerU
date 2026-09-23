#!/bin/sh
set -eu

python3 /opt/mineru/scripts/offline_package.py preflight \
  --model-dir "${MINERU_MODEL_BASE_DIR:?MINERU_MODEL_BASE_DIR is required}" \
  --manifest "${MINERU_MODEL_MANIFEST:?MINERU_MODEL_MANIFEST is required}" \
  --require-mineru-repos

exec "$@"
