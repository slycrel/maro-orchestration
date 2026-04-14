#!/usr/bin/env bash
# Safe test runner for the 4-core Mac Mini.
#
# Caps pytest to 2 of 4 CPU cores (leaving 2 free for TUI + gateway) and runs
# at nice +15 so the TUI stays responsive. Runs tests in chunks so progress is
# visible and hangs are easy to spot.
#
# Usage:
#   scripts/test-safe.sh               # run full suite in chunks
#   scripts/test-safe.sh tests/foo     # run specific path (no chunking)
#   scripts/test-safe.sh --chunk 500   # custom chunk size
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# Defaults — tuned for 4-core box with a TUI running
CORES="${TEST_CORES:-0,1}"          # use cores 0-1, leave 2-3 for TUI
NICE="${TEST_NICE:-15}"             # +15 nice = lowest priority
CHUNK_SIZE="${TEST_CHUNK:-1000}"    # tests per chunk

TARGET=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --chunk) CHUNK_SIZE="$2"; shift 2 ;;
        --cores) CORES="$2"; shift 2 ;;
        --nice)  NICE="$2";  shift 2 ;;
        -h|--help)
            sed -n '2,13p' "$0" | sed 's/^# \?//'
            exit 0
            ;;
        *) TARGET="$1"; shift ;;
    esac
done

# Clean up any stale pytest processes from prior interrupted runs.
# This is a common cause of load spikes — each abandoned pytest holds
# its own subprocess tree.
STALE="$(pgrep -f "pytest.*openclaw-orchestration" 2>/dev/null || true)"
if [[ -n "$STALE" ]]; then
    echo "[test-safe] killing stale pytest processes: $STALE" >&2
    echo "$STALE" | xargs -r kill -TERM 2>/dev/null || true
    sleep 1
    echo "$STALE" | xargs -r kill -KILL 2>/dev/null || true
fi

# If user specified a target, just run that directly — no chunking needed.
if [[ -n "$TARGET" ]]; then
    echo "[test-safe] running: $TARGET (cores=$CORES, nice=$NICE)" >&2
    exec nice -n "$NICE" taskset -c "$CORES" python3 -m pytest "$TARGET" --tb=short -q
fi

# Full suite — run in chunks so progress is visible and a hang in one
# chunk doesn't mean waiting 100s before you see output.
TMP_LIST="$(mktemp)"
trap 'rm -f "$TMP_LIST"' EXIT

echo "[test-safe] collecting test list..." >&2
# pytest --collect-only -q prints per-test nodeids only when given one path at a time;
# when given a directory it prints one line per *file* as "tests/foo.py: NN" (file + count).
# We ask pytest to print the collection tree with --co -q and then extract lines that
# look like test nodeids ("path/to/file.py::Class::test" or "path/to/file.py::test").
# Fallback to per-file chunking if nothing matches (handles both output formats).
nice -n "$NICE" taskset -c "$CORES" python3 -m pytest tests/ --collect-only -q 2>/dev/null | \
    grep -E '^tests/[^ ]+::' | sort -u > "$TMP_LIST" || true

if [[ ! -s "$TMP_LIST" ]]; then
    # Newer pytest: collect-only prints "path: NN" per file. Extract paths only and chunk by file.
    echo "[test-safe] collection returned file-level output; chunking by file" >&2
    nice -n "$NICE" taskset -c "$CORES" python3 -m pytest tests/ --collect-only -q 2>/dev/null | \
        grep -E '^tests/[^ ]+\.py' | sed -E 's/: *[0-9]+\s*$//' | sort -u > "$TMP_LIST" || true
fi

TOTAL="$(wc -l < "$TMP_LIST")"
if [[ "$TOTAL" -eq 0 ]]; then
    echo "[test-safe] no tests collected — falling back to full suite" >&2
    exec nice -n "$NICE" taskset -c "$CORES" python3 -m pytest tests/ --tb=short -q
fi

echo "[test-safe] $TOTAL items, chunks of $CHUNK_SIZE (cores=$CORES, nice=$NICE)" >&2

CHUNK_NUM=0
FAILED_CHUNKS=()
while IFS= read -r -d '' chunk_file; do
    CHUNK_NUM=$((CHUNK_NUM + 1))
    CHUNK_LINES="$(wc -l < "$chunk_file")"
    echo "" >&2
    echo "[test-safe] chunk $CHUNK_NUM ($CHUNK_LINES items)" >&2
    # Use xargs -a to pass chunk lines as args safely (handles spaces if any).
    if ! xargs -a "$chunk_file" nice -n "$NICE" taskset -c "$CORES" python3 -m pytest --tb=short -q; then
        FAILED_CHUNKS+=("$CHUNK_NUM")
        echo "[test-safe] chunk $CHUNK_NUM had failures" >&2
    fi
done < <(
    split -l "$CHUNK_SIZE" "$TMP_LIST" "${TMP_LIST}.chunk-" && \
    find "$(dirname "$TMP_LIST")" -name "$(basename "$TMP_LIST").chunk-*" -print0 | sort -z
)

# Cleanup chunk files
rm -f "${TMP_LIST}".chunk-*

if [[ ${#FAILED_CHUNKS[@]} -gt 0 ]]; then
    echo "" >&2
    echo "[test-safe] FAILURES in chunks: ${FAILED_CHUNKS[*]}" >&2
    exit 1
fi

echo "" >&2
echo "[test-safe] all $TOTAL items passed" >&2
