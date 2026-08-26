#!/usr/bin/env python3
"""The CPython half of the task_store byte differential.

`probe_test.go`'s TestTaskStoreDriveProbe drives the Go side and prints a
labelled transcript; this drives task_store.py and prints the SAME
transcript, line for line, so the two can be diffed byte for byte after
normalising the two volatile fields.

It lives in the repo because a claim that can only be re-checked by a
script nobody has is not a claim. Both halves used to be described in
REVIEW.md and only the Go half existed, so `go test ./internal/tasks/`
reported one skip and the interop assertion rested on a scratch file that
had been deleted (adversarial tasks-r1 LOW).

Run it through ts_diff.sh, which owns the two temp workspaces and the
normalisation. Direct use:

    MARO_WORKSPACE=/tmp/some-empty-dir PYTHONPATH=<repo>/src \\
        python3 ts_measure.py

REFUSES to run against a workspace outside /tmp, and against anything
inside ~/.maro. It writes a whole queue tree; the 2026-08-16 live-ledger
overwrite is the reason that check is here and not in the caller.
"""
import json
import os
import pathlib
import sys

ws = os.environ.get("MARO_WORKSPACE", "")
if not ws:
    raise SystemExit("ts_measure: MARO_WORKSPACE is required")
real = os.path.realpath(ws)
live = os.path.realpath(os.path.expanduser("~/.maro"))
if not real.startswith("/tmp/"):
    raise SystemExit("ts_measure: refusing to drive a workspace outside /tmp: %s" % real)
if real == live or os.path.commonpath([real, live]) == live:
    raise SystemExit("ts_measure: refusing — %s is inside the live workspace" % real)

import task_store  # noqa: E402  (after the guard, on purpose)

# The resolver's OWN answer, not the environment variable it was handed —
# a symlinked temp dir defeats a string comparison, which is the shape that
# makes this class of accident survive review.
resolved = str(task_store._tasks_dir())
if not resolved.startswith("/tmp/"):
    raise SystemExit("ts_measure: _tasks_dir() resolved outside /tmp: %s" % resolved)


def dump(path):
    sys.stdout.write(pathlib.Path(path).read_text(encoding="utf-8"))


task_store.enqueue(
    job_id="task-fixed-0001",
    lane="agenda",
    source="cli",
    reason="café → naïve ",
    parent_job_id="task-parent",
    continuation_depth=2,
    origin={"parent_loop_id": "L1", "parent_goal": "do a thing", "z": 1},
)
p = task_store.task_path("task-fixed-0001")
print("=== BYTES ===")
dump(p)
print("=== MODE === 0o%o" % (p.stat().st_mode & 0o777))
print("=== LOCKNAME === %s" % p.with_suffix(".lock").name)

task_store.claim("task-fixed-0001", pid=os.getpid())
task_store.complete("task-fixed-0001", {"a": "/x/y"}, "incomplete")
print("=== AFTER COMPLETE ===")
dump(p)

task_store.enqueue(job_id="task-fail-0002")
task_store.fail("task-fail-0002", "boom")
print("=== AFTER FAIL ===")
dump(task_store.task_path("task-fail-0002"))

print("=== SUMMARY === %s" % json.dumps(task_store.status_summary()))
print("=== NEWID === %s" % task_store.new_job_id())
print("=== NOW === %s" % task_store.utc_now())

print("=== EMPTYORIGIN ===")
task_store.enqueue(job_id="task-min-0003")
dump(task_store.task_path("task-min-0003"))
