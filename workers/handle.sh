#!/usr/bin/env bash
set -euo pipefail

cd "${ORCH_ROOT:?}"
export PYTHONPATH="${ORCH_ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"
if [[ -n "${ORCH_RUN_ID:-}" ]]; then
  export POE_LLM_MAX_RETRIES="${POE_LLM_MAX_RETRIES:-0}"
  export POE_CLAUDE_RATE_LIMIT_MAX_RETRIES="${POE_CLAUDE_RATE_LIMIT_MAX_RETRIES:-0}"
fi
set +e
python3 src/bootstrap_task.py
rc=$?
set -e
if [[ "$rc" -eq 0 || "$rc" -eq 1 ]]; then
  exit "$rc"
fi

stdout_file="$(mktemp)"
stderr_file="$(mktemp)"
set +e
python3 src/handle.py --format json "${ORCH_ITEM_TEXT:?}" >"$stdout_file" 2>"$stderr_file"
handle_rc=$?
set -e
cat "$stdout_file"
cat "$stderr_file" >&2

if [[ -n "${ORCH_SESSION_RESULT_PATH:-}" && ! -f "${ORCH_SESSION_RESULT_PATH}" ]]; then
  python3 - "$stdout_file" "${ORCH_SESSION_RESULT_PATH}" <<'PY'
import json, sys
from pathlib import Path
stdout_path = Path(sys.argv[1])
result_path = Path(sys.argv[2])
text = stdout_path.read_text(encoding='utf-8').strip()
status = 'blocked'
note = 'handle produced no output'
if text:
    try:
        payload = json.loads(text)
    except Exception:
        note = text.splitlines()[-1][:500]
    else:
        raw_status = str(payload.get('status') or '').strip().lower()
        result = str(payload.get('result') or '').strip()
        if raw_status == 'done':
            status = 'done'
            note = result or 'handle completed successfully'
        else:
            note = result or f'handle returned status={raw_status or "unknown"}'
result_path.write_text(json.dumps({'status': status, 'note': note}, indent=2) + '\n', encoding='utf-8')
PY
fi

rm -f "$stdout_file" "$stderr_file"
exit "$handle_rc"
