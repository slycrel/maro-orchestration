#!/usr/bin/env bash
# Dispatch a goal from OpenClaw (or any shell substrate) to Maro.
#
#   maro-dispatch.sh "research X and write a summary"     # enqueue + run now
#   maro-dispatch.sh --queue "big goal"                   # enqueue only
#   echo "goal text" | maro-dispatch.sh                   # goal on stdin
#
# Results come back through Maro's notify hook (see deploy/openclaw/README.md)
# or by polling:  maro-runs list  /  maro-runs result <handle_id>.
#
# Uses the pip-installed maro-enqueue when present, else falls back to the
# repo checkout (MARO_REPO, default ~/claude/maro-orchestration).
set -euo pipefail

MARO_REPO="${MARO_REPO:-$HOME/claude/maro-orchestration}"

# Workspace sanitization: a dispatch must land in Maro's canonical workspace
# (~/.maro/workspace) no matter what environment the substrate runs us from.
# OpenClaw pins OPENCLAW_WORKSPACE (and friends) for its subprocesses, which
# Maro's legacy fallbacks honor — routing events, step-costs (the budget
# ledger!), and lessons into ~/.openclaw/workspace/prototypes/* instead.
# Seen live 2026-07-02: runs split-brained into the deprecated
# poe-orchestration prototype dir. NOTE: any pinned workspace var (even
# MARO_WORKSPACE) flips orch_items.memory_dir into the legacy
# <ws>/prototypes/maro-orchestration layout — the clean no-env default is
# the only path to ~/.maro/workspace/memory, so unset them all.
unset MARO_WORKSPACE OPENCLAW_WORKSPACE WORKSPACE_ROOT MARO_ORCH_ROOT MARO_MEMORY_DIR

drain="--drain"
if [ "${1:-}" = "--queue" ]; then
    drain=""
    shift
fi

goal="${*:-}"
if [ -z "$goal" ]; then
    goal="$(cat)"
fi
if [ -z "$goal" ]; then
    echo "usage: maro-dispatch.sh [--queue] <goal text>   (or goal on stdin)" >&2
    exit 2
fi

if command -v maro-enqueue >/dev/null 2>&1; then
    exec maro-enqueue "$goal" $drain
else
    cd "$MARO_REPO"
    exec env PYTHONPATH="$MARO_REPO/src" python3 -c "
import sys
from handle import enqueue_main
sys.exit(enqueue_main(sys.argv[1:]) or 0)
" "$goal" $drain
fi
