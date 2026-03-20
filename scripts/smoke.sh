#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
export OPENCLAW_WORKSPACE="$TMP"

python3 "$ROOT_DIR/src/cli.py" init smoke "Smoke test mission" --priority 1
python3 "$ROOT_DIR/src/cli.py" next --project smoke
RUN_OUTPUT="$(python3 "$ROOT_DIR/src/cli.py" run --project smoke --worker smoke --source smoke-test)"
echo "$RUN_OUTPUT"
RUN_ID="$(printf '%s\n' "$RUN_OUTPUT" | tr ' ' '\n' | awk -F= '/^run_id=/{print $2; exit}')"
python3 "$ROOT_DIR/src/cli.py" finish "$RUN_ID" --status done --note "smoke verified"
python3 "$ROOT_DIR/src/cli.py" log smoke "smoke run completed"
python3 "$ROOT_DIR/src/cli.py" status >/dev/null
python3 "$ROOT_DIR/src/cli.py" report --project smoke --out "$TMP/report.md"

echo "smoke=ok workspace=$TMP run_id=$RUN_ID"
