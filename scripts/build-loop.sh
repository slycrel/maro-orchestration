#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
INVOKE_CWD="$(pwd -P)"

args=("$@")
if (( ${#args[@]} > 0 )); then
  last_index=$((${#args[@]} - 1))
  last_arg="${args[$last_index]}"
  if [[ "$last_arg" != -* ]]; then
    if [[ "$last_arg" = /* ]]; then
      workspace_arg="$last_arg"
    else
      workspace_arg="$INVOKE_CWD/$last_arg"
    fi
    if [[ -d "$workspace_arg" ]]; then
      resolved_workspace="$(cd "$workspace_arg" && pwd -P)"
      unset 'args[$last_index]'
      args=("${args[@]}")
      if [[ -z "${POE_WORKSPACE:-}" && -z "${OPENCLAW_WORKSPACE:-}" && -z "${WORKSPACE_ROOT:-}" && -z "${POE_ORCH_ROOT:-}" ]]; then
        export OPENCLAW_WORKSPACE="$resolved_workspace"
      fi
    fi
  fi
fi

cd "$REPO_ROOT"
if [[ -z "${POE_ORCH_ROOT:-}" && -z "${POE_WORKSPACE:-}" && -z "${OPENCLAW_WORKSPACE:-}" && -z "${WORKSPACE_ROOT:-}" ]]; then
  export POE_ORCH_ROOT="$REPO_ROOT"
fi
export PYTHONPATH="$REPO_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
exec python3 "$REPO_ROOT/src/cli.py" build-loop "${args[@]}"
