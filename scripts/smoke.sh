#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
export MARO_WORKSPACE="$TMP"
export OPENCLAW_WORKSPACE="$TMP"

python3 "$ROOT_DIR/src/cli.py" init smoke "Smoke test mission" --priority 1
python3 "$ROOT_DIR/src/cli.py" next --project smoke
RUN_OUTPUT="$(python3 "$ROOT_DIR/src/cli.py" cycle --project smoke --worker smoke --source smoke-test)"
echo "$RUN_OUTPUT"
RUN_ID="$(printf '%s\n' "$RUN_OUTPUT" | tr ' ' '\n' | awk -F= '/^run_id=/{print $2; exit}')"
python3 "$ROOT_DIR/src/cli.py" finish "$RUN_ID" --status done --note "smoke verified"

TICK_OUTPUT="$(python3 "$ROOT_DIR/src/cli.py" tick \
  --project smoke \
  --worker smoke \
  --source smoke-tick \
  --exec-cmd 'printf "%s" "$ORCH_PROJECT" > "$ORCH_RUN_ARTIFACT_DIR/project.txt"' \
  --require-artifact project.txt \
  --require-nonempty \
  --review-cmd 'grep -q smoke "$ORCH_RUN_ARTIFACT_DIR/project.txt" && printf ok > "$ORCH_REVIEW_ARTIFACT_DIR/verdict.txt"')"
echo "$TICK_OUTPUT"
TICK_RUN_ID="$(printf '%s\n' "$TICK_OUTPUT" | tr ' ' '\n' | awk -F= '/^run_id=/{print $2; exit}')"
TICK_ARTIFACT_REL="$(printf '%s\n' "$TICK_OUTPUT" | tr ' ' '\n' | awk -F= '/^artifact=/{print $2; exit}')"
case "$TICK_ARTIFACT_REL" in
  '~workspace/'*) TICK_ARTIFACT_DIR="$TMP/${TICK_ARTIFACT_REL#\~workspace/}" ;;
  *)              TICK_ARTIFACT_DIR="$TMP/$TICK_ARTIFACT_REL" ;;
esac

test -f "$TICK_ARTIFACT_DIR/project.txt"
test -f "$TICK_ARTIFACT_DIR/review/verdict.txt"
test -f "$TICK_ARTIFACT_DIR/validation-summary.json"

python3 "$ROOT_DIR/src/cli.py" log smoke "smoke run completed"
python3 "$ROOT_DIR/src/cli.py" status --dry-run >/dev/null
python3 "$ROOT_DIR/src/cli.py" report --project smoke --out "$TMP/report.md"

echo "smoke=ok workspace=$TMP run_id=$RUN_ID tick_run_id=$TICK_RUN_ID"
