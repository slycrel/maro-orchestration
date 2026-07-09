"""Concurrency phase 2: file_lock under real cross-process contention.

Spawns actual OS processes (subprocess, not threads) hammering one file —
threads can't catch flock bugs since flock is per-process. Sized for the
4-core Mac Mini under test-safe.sh: ≤4 procs, small Ns, bounded seconds.

Pins:
- locked_append: no torn/interleaved lines even > PIPE_BUF (4096B)
- locked_rmw: no lost updates (the counter test — each increment survives)
- fail-closed: FileLockTimeout past the deadline; it IS an OSError
- fail-open escape hatch: MARO_FILELOCK_FAIL_OPEN=1 restores warn-and-proceed
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from file_lock import FileLockTimeout, atomic_write, locked_rmw, locked_write

SRC = str(Path(__file__).resolve().parents[1] / "src")


def _spawn(code: str, *args: str, env_extra: dict | None = None) -> subprocess.Popen:
    env = dict(os.environ)
    env["PYTHONPATH"] = SRC
    if env_extra:
        env.update(env_extra)
    return subprocess.Popen(
        [sys.executable, "-c", code, *args],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


APPEND_WORKER = """
import json, sys
from pathlib import Path
from file_lock import locked_append
path, proc_id, n, size = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
line = json.dumps({"proc": proc_id, "pad": "x" * size})
for _ in range(n):
    locked_append(path, line)
"""


def test_concurrent_appends_no_torn_lines(tmp_path):
    """4 procs x 25 appends of ~8KB lines (2x PIPE_BUF) → exactly 100
    intact, parseable lines. Bare open('a') tears these."""
    target = tmp_path / "ledger.jsonl"
    procs = [
        _spawn(APPEND_WORKER, str(target), f"p{i}", "25", "8000")
        for i in range(4)
    ]
    for p in procs:
        _, err = p.communicate(timeout=60)
        assert p.returncode == 0, err

    lines = target.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 100
    counts: dict = {}
    for line in lines:
        d = json.loads(line)  # raises on any torn line
        assert len(d["pad"]) == 8000
        counts[d["proc"]] = counts.get(d["proc"], 0) + 1
    assert counts == {f"p{i}": 25 for i in range(4)}


RMW_WORKER = """
import sys
from pathlib import Path
from file_lock import locked_rmw
path, n = Path(sys.argv[1]), int(sys.argv[2])
for _ in range(n):
    locked_rmw(path, lambda old: str(int(old or "0") + 1), default="0")
"""


def test_concurrent_rmw_no_lost_updates(tmp_path):
    """4 procs x 25 read-modify-write increments → exactly 100. The
    read-outside-lock pattern this replaces loses updates here."""
    counter = tmp_path / "counter.txt"
    procs = [_spawn(RMW_WORKER, str(counter), "25") for _ in range(4)]
    for p in procs:
        _, err = p.communicate(timeout=60)
        assert p.returncode == 0, err
    assert int(counter.read_text(encoding="utf-8")) == 100


HOLDER = """
import fcntl, sys, time
lock_path = sys.argv[1]
fh = open(lock_path, "w")
fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
print("HELD", flush=True)
time.sleep(float(sys.argv[2]))
"""


def _hold_lock(data_path: Path, seconds: float) -> subprocess.Popen:
    """Spawn a process holding data_path's sidecar lock; wait until held."""
    proc = _spawn(HOLDER, str(data_path) + ".lock", str(seconds))
    assert proc.stdout.readline().strip() == "HELD"
    return proc


def test_fail_closed_raises_filelocktimeout(tmp_path, monkeypatch):
    target = tmp_path / "contended.jsonl"
    holder = _hold_lock(target, 15)
    try:
        monkeypatch.setenv("MARO_FILELOCK_TIMEOUT_S", "1")
        monkeypatch.delenv("MARO_FILELOCK_FAIL_OPEN", raising=False)
        start = time.monotonic()
        with pytest.raises(FileLockTimeout):
            with locked_write(target):
                pass  # pragma: no cover — must not be reached
        waited = time.monotonic() - start
        assert 0.9 <= waited < 5  # bounded: deadline, not the holder's sleep
        # FileLockTimeout is an OSError so existing narrow excepts contain it
        assert issubclass(FileLockTimeout, OSError)
        # nothing was written unlocked
        assert not target.exists()
    finally:
        holder.kill()
        holder.communicate()


def test_fail_open_flag_proceeds_unlocked(tmp_path, monkeypatch):
    target = tmp_path / "contended.jsonl"
    holder = _hold_lock(target, 15)
    try:
        monkeypatch.setenv("MARO_FILELOCK_TIMEOUT_S", "1")
        monkeypatch.setenv("MARO_FILELOCK_FAIL_OPEN", "1")
        with locked_write(target):
            target.write_text("degraded but written\n", encoding="utf-8")
        assert target.read_text(encoding="utf-8") == "degraded but written\n"
    finally:
        holder.kill()
        holder.communicate()


def test_lock_released_on_holder_death(tmp_path):
    """flock is kernel-released when the holder dies — a crashed process
    can never wedge a waiter (the premise of fail-closed)."""
    target = tmp_path / "ledger.jsonl"
    holder = _hold_lock(target, 60)
    holder.kill()
    holder.communicate()
    start = time.monotonic()
    with locked_write(target):
        target.write_text("acquired after holder death\n", encoding="utf-8")
    assert time.monotonic() - start < 5


def test_locked_rmw_reentrant(tmp_path):
    """A locked_rmw inside a locked_write on the same file must not
    self-deadlock (mark_item nests write_next_lines this way)."""
    target = tmp_path / "nested.txt"
    with locked_write(target):
        locked_rmw(target, lambda old: old + "inner\n", default="")
    assert target.read_text(encoding="utf-8") == "inner\n"


def test_atomic_write_reader_never_sees_partial(tmp_path):
    """Writer rewrites in a loop; reader polls — every observation is a
    complete old or new payload, never a truncation."""
    target = tmp_path / "swap.txt"
    atomic_write(target, "A" * 20000)
    writer = _spawn(
        """
import sys
from pathlib import Path
from file_lock import atomic_write
target = Path(sys.argv[1])
for i in range(200):
    atomic_write(target, ("A" if i % 2 else "B") * 20000)
""",
        str(target),
    )
    try:
        deadline = time.monotonic() + 20
        while writer.poll() is None and time.monotonic() < deadline:
            content = target.read_text(encoding="utf-8")
            assert len(content) == 20000
            assert content in ("A" * 20000, "B" * 20000)
    finally:
        _, err = writer.communicate(timeout=30)
        assert writer.returncode == 0, err
