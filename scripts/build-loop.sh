#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$REPO_ROOT"
if [[ -z "${POE_ORCH_ROOT:-}" && -z "${POE_WORKSPACE:-}" && -z "${OPENCLAW_WORKSPACE:-}" && -z "${WORKSPACE_ROOT:-}" ]]; then
  export POE_ORCH_ROOT="$REPO_ROOT"
fi
export PYTHONPATH="$REPO_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
exec python3 "$REPO_ROOT/src/cli.py" build-loop "$@"
