#!/usr/bin/env bash
# Resource-conscious test runner.
#
# On Linux with taskset, caps pytest to selected CPU cores. On hosts without
# taskset (including macOS), keeps the nice priority but skips affinity. Runs
# tests in chunks so progress is visible and hangs are easy to spot.
#
# Usage:
#   scripts/test-safe.sh               # run full suite in file chunks
#   scripts/test-safe.sh --fast        # skip tests marked slow
#   scripts/test-safe.sh tests/foo     # run specific path (no chunking)
#   scripts/test-safe.sh --chunk 40    # custom files per chunk
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# Defaults — tuned for 4-core box with a TUI running
CORES="${TEST_CORES:-0,1}"          # use cores 0-1, leave 2-3 for TUI
NICE="${TEST_NICE:-15}"             # +15 nice = lowest priority
CHUNK_SIZE="${TEST_CHUNK:-40}"      # files per chunk on current pytest
if [[ -n "${TEST_PYTHON:-}" ]]; then
    PYTHON="$TEST_PYTHON"
elif [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
    PYTHON="$ROOT_DIR/.venv/bin/python"
else
    PYTHON="python3"
fi

TARGET=""
# An explicit tautology makes "full" immune to a future project-wide marker
# default; --fast is the only path that intentionally deselects slow tests.
PYTEST_MARK_EXPR="not slow or slow"
MODE_LABEL="full"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --chunk) CHUNK_SIZE="$2"; shift 2 ;;
        --cores) CORES="$2"; shift 2 ;;
        --nice)  NICE="$2";  shift 2 ;;
        --fast)  PYTEST_MARK_EXPR="not slow"; MODE_LABEL="fast"; shift ;;
        -h|--help)
            sed -n '2,13p' "$0" | sed 's/^# \?//'
            exit 0
            ;;
        *) TARGET="$1"; shift ;;
    esac
done

RUN_PREFIX=(nice -n "$NICE")
if command -v taskset >/dev/null 2>&1; then
    RUN_PREFIX+=(taskset -c "$CORES")
    RESOURCE_LABEL="cores=$CORES, nice=$NICE"
else
    RESOURCE_LABEL="cores=unrestricted (taskset unavailable), nice=$NICE"
fi

# Clean up any stale pytest processes from prior interrupted runs.
# This is a common cause of load spikes — each abandoned pytest holds
# its own subprocess tree.
STALE="$(pgrep -f "pytest.*(openclaw|maro)-orchestration" 2>/dev/null || true)"
if [[ -n "$STALE" ]]; then
    echo "[test-safe] killing stale pytest processes: $STALE" >&2
    echo "$STALE" | xargs kill -TERM 2>/dev/null || true
    sleep 1
    echo "$STALE" | xargs kill -KILL 2>/dev/null || true
fi

# If user specified a target, just run that directly — no chunking needed.
if [[ -n "$TARGET" ]]; then
    echo "[test-safe] running: $TARGET (mode=$MODE_LABEL, $RESOURCE_LABEL)" >&2
    exec "${RUN_PREFIX[@]}" "$PYTHON" -m pytest "$TARGET" -m "$PYTEST_MARK_EXPR" --tb=short -q
fi

# Full suite — run in chunks so progress is visible and a hang in one
# chunk doesn't mean waiting 100s before you see output.
TMP_LIST="$(mktemp)"
trap 'rm -f "$TMP_LIST" "${TMP_LIST}".chunk-*' EXIT

echo "[test-safe] collecting test list..." >&2
# pytest --collect-only -q prints per-test nodeids only when given one path at a time;
# when given a directory it prints one line per *file* as "tests/foo.py: NN" (file + count).
# We ask pytest to print the collection tree with --co -q and then extract lines that
# look like test nodeids ("path/to/file.py::Class::test" or "path/to/file.py::test").
# Fallback to per-file chunking if nothing matches (handles both output formats).
"${RUN_PREFIX[@]}" "$PYTHON" -m pytest tests/ -m "$PYTEST_MARK_EXPR" --collect-only -q 2>/dev/null | \
    grep -E '^tests/[^ ]+::' | sort -u > "$TMP_LIST" || true

if [[ ! -s "$TMP_LIST" ]]; then
    # Newer pytest: collect-only prints "path: NN" per file. Extract paths only and chunk by file.
    echo "[test-safe] collection returned file-level output; chunking by file" >&2
    "${RUN_PREFIX[@]}" "$PYTHON" -m pytest tests/ -m "$PYTEST_MARK_EXPR" --collect-only -q 2>/dev/null | \
        grep -E '^tests/[^ ]+\.py' | sed -E 's/: *[0-9]+\s*$//' | sort -u > "$TMP_LIST" || true
fi

TOTAL="$(wc -l < "$TMP_LIST" | tr -d '[:space:]')"
if [[ "$TOTAL" -eq 0 ]]; then
    echo "[test-safe] no tests collected — falling back to full suite" >&2
    exec "${RUN_PREFIX[@]}" "$PYTHON" -m pytest tests/ -m "$PYTEST_MARK_EXPR" --tb=short -q
fi

echo "[test-safe] $TOTAL items, chunks of $CHUNK_SIZE (mode=$MODE_LABEL, $RESOURCE_LABEL)" >&2

CHUNK_NUM=0
FAILED_CHUNKS=()
split -l "$CHUNK_SIZE" "$TMP_LIST" "${TMP_LIST}.chunk-"
for chunk_file in "${TMP_LIST}".chunk-*; do
    CHUNK_NUM=$((CHUNK_NUM + 1))
    CHUNK_LINES="$(wc -l < "$chunk_file")"
    echo "" >&2
    echo "[test-safe] chunk $CHUNK_NUM ($CHUNK_LINES items)" >&2
    CHUNK_ARGS=()
    while IFS= read -r item; do
        CHUNK_ARGS+=("$item")
    done < "$chunk_file"
    if ! "${RUN_PREFIX[@]}" "$PYTHON" -m pytest -m "$PYTEST_MARK_EXPR" --tb=short -q "${CHUNK_ARGS[@]}"; then
        FAILED_CHUNKS+=("$CHUNK_NUM")
        echo "[test-safe] chunk $CHUNK_NUM had failures" >&2
    fi
done

# Cleanup chunk files
rm -f "${TMP_LIST}".chunk-*

if [[ ${#FAILED_CHUNKS[@]} -gt 0 ]]; then
    echo "" >&2
    echo "[test-safe] FAILURES in chunks: ${FAILED_CHUNKS[*]}" >&2
    exit 1
fi

echo "" >&2
echo "[test-safe] all $TOTAL items passed" >&2
