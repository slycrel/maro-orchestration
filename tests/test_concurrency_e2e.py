"""Concurrency phase 4: cross-cutting stress + crash-safety.

Phase 2/3 tests pin each primitive in isolation; these mix them the way the
real box does (heartbeat drain + manual run + background task all writing
one workspace) and SIGKILL a writer mid-atomic_write to prove readers can
never observe a torn file.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

SRC = str(Path(__file__).resolve().parents[1] / "src")


def _env(tmp_path) -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = SRC
    env["OPENCLAW_WORKSPACE"] = str(tmp_path)
    env["MARO_WORKSPACE"] = str(tmp_path)
    return env


MIXED_WORKER = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[5])
from file_lock import locked_append, locked_rmw
from orch_items import mark_item

ws, worker_id, slug = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
indices = [int(x) for x in sys.argv[4].split(",")]
log_path = ws / "memory" / "stress.log"
counter_path = ws / "memory" / "stress-counter.txt"

def _inc(old):
    return str(int(old or "0") + 1)

for round_no in range(10):
    locked_append(log_path, f"{worker_id}-r{round_no}-" + "x" * 512)
    locked_rmw(counter_path, _inc, default="0")
for idx in indices:
    mark_item(slug, idx, "~")
    mark_item(slug, idx, "x")
"""


def test_mixed_ops_three_procs_no_loss(tmp_path, monkeypatch):
    """3 procs each doing 10 locked_appends + 10 rmw increments + marking 3
    items TODO→DOING→DONE on one workspace → nothing lost, nothing torn."""
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    import orch_items

    slug = "stress-proj"
    indices = orch_items.append_next_items(slug, [f"item-{i}" for i in range(9)])
    assert len(indices) == 9

    procs = [
        subprocess.Popen(
            [sys.executable, "-c", MIXED_WORKER, str(tmp_path), f"w{p}", slug,
             ",".join(str(i) for i in indices[p * 3:(p + 1) * 3]), SRC],
            env=_env(tmp_path), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True,
        )
        for p in range(3)
    ]
    for p in procs:
        _, err = p.communicate(timeout=120)
        assert p.returncode == 0, err

    lines = (tmp_path / "memory" / "stress.log").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 30
    assert all(line.endswith("x" * 512) for line in lines)  # no torn lines
    counter = (tmp_path / "memory" / "stress-counter.txt").read_text(encoding="utf-8")
    assert counter.strip() == "30"  # no lost increments
    _, items = orch_items.parse_next(slug)
    assert len(items) == 9
    assert all(it.state == "x" for it in items), [(it.index, it.state) for it in items]


CRASH_WRITER = """
import sys, os
sys.path.insert(0, sys.argv[2])
from file_lock import atomic_write

target = sys.argv[1]
payload = "HEADER\\n" + ("y" * 65536) + "\\nFOOTER"
print("READY", flush=True)
i = 0
while True:
    atomic_write(target, payload)
    i += 1
    if i % 50 == 0:
        print("ALIVE", flush=True)
"""


def test_sigkill_mid_atomic_write_never_torn(tmp_path):
    """SIGKILL a child hammering atomic_write, repeatedly — the reader must
    only ever see a complete old or complete new payload, never a partial."""
    target = tmp_path / "atomic-target.txt"
    expected = "HEADER\n" + ("y" * 65536) + "\nFOOTER"

    for _round in range(5):
        proc = subprocess.Popen(
            [sys.executable, "-c", CRASH_WRITER, str(target), SRC],
            env=_env(tmp_path), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True,
        )
        try:
            assert proc.stdout.readline().strip() == "READY", proc.stderr.read()
            # let it get some writes in, then kill mid-flight
            time.sleep(0.05 + _round * 0.03)
            proc.send_signal(signal.SIGKILL)
        finally:
            proc.communicate()

        if target.exists():
            content = target.read_text(encoding="utf-8")
            assert content == expected, (
                f"torn read after SIGKILL round {_round}: "
                f"len={len(content)} vs {len(expected)}"
            )
