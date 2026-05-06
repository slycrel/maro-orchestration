#!/usr/bin/env bash
set -euo pipefail

cd "${ORCH_ROOT:?}"
export PYTHONPATH="${ORCH_ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"
if [[ -n "${ORCH_RUN_ID:-}" ]]; then
  export POE_LLM_MAX_RETRIES="${POE_LLM_MAX_RETRIES:-0}"
  export POE_CLAUDE_RATE_LIMIT_MAX_RETRIES="${POE_CLAUDE_RATE_LIMIT_MAX_RETRIES:-0}"
fi
python3 src/bootstrap_task.py
rc=$?
if [[ "$rc" -eq 0 || "$rc" -eq 1 ]]; then
  exit "$rc"
fi
exec python3 src/handle.py "${ORCH_ITEM_TEXT:?}"
