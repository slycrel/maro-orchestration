#!/usr/bin/env bash
set -euo pipefail

cd "${ORCH_ROOT:?}"
export PYTHONPATH="${ORCH_ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"
exec python3 src/handle.py "${ORCH_ITEM_TEXT:?}"
