#!/usr/bin/env bash
# The task_store byte differential: drive both runtimes over the same
# sequence, into two fresh workspaces, and diff the transcripts.
#
# Exits 0 when the two are identical after normalising the two fields that
# CANNOT match — a freshly minted job id and the wall clock. Everything
# else is compared byte for byte: the task file's bytes, its mode, the lock
# name, the status summary and the minimal row.
#
# Run from anywhere:   bash go/internal/tasks/ts_diff.sh
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
gomod="$(cd "$here/../.." && pwd)"
repo="$(cd "$gomod/.." && pwd)"

if [ ! -f "$repo/src/task_store.py" ]; then
  echo "ts_diff: no python tree at $repo/src — nothing to compare against" >&2
  exit 2
fi

tmp="$(mktemp -d /tmp/ts-diff-XXXXXX)"
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/py" "$tmp/go"

# Three fields cannot match between two processes: a freshly minted job id
# (a clock plus eight random hex), a timestamp, and a pid. Nothing else is
# masked — a normaliser that hides a field is how a differential goes
# quietly green.
#
# Each mask keeps the field's SHAPE. `task-20260826T172737Z-4a7e11d6`
# becomes `task-<stamp>-<hex8>`, so a port that minted a UUID, dropped the
# stamp, or used sixteen hex digits still shows up as a diff — masking it
# to a bare `<id>` would have made new_job_id's format untested by the one
# test that reads it.
normalise() {
  sed -E \
    -e 's/task-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}/task-<stamp>-<hex8>/g' \
    -e 's/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/<uuid>/g' \
    -e 's/[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]+(\+00:00|Z)/<ts>/g' \
    -e 's/"claimed_by_pid": [0-9]+/"claimed_by_pid": <pid>/g'
}

MARO_WORKSPACE="$tmp/py" MARO_USER_DIR="$tmp/py-user" \
  PYTHONPATH="$repo/src" python3 "$here/ts_measure.py" | normalise > "$tmp/py.txt"

MARO_TASKS_DRIVE_WS="$tmp/go" MARO_USER_DIR="$tmp/go-user" \
  go test -count=1 -run TestTaskStoreDriveProbe -v "$gomod/internal/tasks/" 2>/dev/null \
  | sed -n '/=== BYTES ===/,/^--- PASS/p' | sed '/^--- PASS/d' \
  | normalise > "$tmp/go.txt"

# An EMPTY transcript diffs clean against another empty one, so prove both
# sides produced something before believing the diff (P10).
for side in py go; do
  n=$(wc -l < "$tmp/$side.txt")
  if [ "$n" -lt 20 ]; then
    echo "ts_diff: the $side transcript is $n lines — it did not run" >&2
    cat "$tmp/$side.txt" >&2
    exit 3
  fi
done

if diff -u "$tmp/py.txt" "$tmp/go.txt"; then
  echo "ts_diff: identical across $(wc -l < "$tmp/py.txt") lines"
else
  echo "ts_diff: the two runtimes DISAGREE (above: - python, + go)" >&2
  exit 1
fi
