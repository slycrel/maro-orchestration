"""Micro-bench for the Graphiti/falkordblite MemoryStore adapter.

Run with the bake-off venv python:
  GRAPHITI_TELEMETRY_ENABLED=false PYTHONPATH=<repo>:<repo>/src \
    <venv>/bin/python bakeoff/bench_graphiti.py <workdir>

Appends 500 small items, runs 50 recalls (k=8), reports wall seconds,
store size on disk, and redis child RSS.
"""

import os
import random
import subprocess
import sys
import time
from pathlib import Path

os.environ["GRAPHITI_TELEMETRY_ENABLED"] = "false"

from graphiti_adapter import GraphitiMemoryStore  # noqa: E402  (bakeoff on path)
from memory_port import MemoryItem  # noqa: E402

WORDS = ("navigator escalate blocked polymarket ledger pytest lesson rule "
         "trust decay scope thread run crystallize skill persona heartbeat "
         "token quota falkor graph memory recall append edge fence retry").split()

def main():
    workdir = Path(sys.argv[1])
    workdir.mkdir(parents=True, exist_ok=True)
    store = GraphitiMemoryStore(workdir / "bench.db")
    rng = random.Random(42)

    t0 = time.perf_counter()
    for i in range(500):
        content = f"item {i}: " + " ".join(rng.sample(WORDS, 6))
        store.append(MemoryItem(kind=rng.choice(("lesson", "rule", "note")),
                                content=content,
                                scope=rng.choice(("", "thread/a", "thread/a/run/x"))))
    t_append = time.perf_counter() - t0

    t0 = time.perf_counter()
    hits = 0
    for i in range(50):
        q = " ".join(rng.sample(WORDS, 3))
        hits += len(store.recall(q, scope="thread/a/run/x", k=8))
    t_recall = time.perf_counter() - t0

    print(f"append 500 items : {t_append:.2f}s  ({t_append / 500 * 1000:.1f} ms/item)")
    print(f"recall x50 (k=8) : {t_recall:.2f}s  ({t_recall / 50 * 1000:.1f} ms/recall)  total hits={hits}")
    print("stats:", store.stats())

    du = subprocess.run(["du", "-sh", str(workdir)], capture_output=True, text=True)
    print("du -sh store dir :", du.stdout.strip())

    # The redis child daemonizes (reparents to PID 1), so find it via the
    # embedded client's pidfile rather than --ppid.
    import graphiti_adapter as ga
    for db in ga._DBS.values():
        pid = db.client._sync_client.pid
        ps = subprocess.run(["ps", "-o", "pid,rss,etime,args", "-p", str(pid)],
                            capture_output=True, text=True)
        print("redis child:")
        print("\n".join(line[:120] for line in ps.stdout.strip().splitlines()))


if __name__ == "__main__":
    main()
