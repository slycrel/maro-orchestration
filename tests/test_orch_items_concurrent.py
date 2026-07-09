"""Concurrency phase 2: NEXT.md mark_item under real cross-process contention.

The heartbeat-vs-run race this pins: heartbeat's backlog drain flips an item
to DOING while a finishing run flips another to DONE. Before mark_item held
the file lock across parse→rewrite, one whole-file rewrite clobbered the
other and an item's state change was silently lost.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

SRC = str(Path(__file__).resolve().parents[1] / "src")

MARK_WORKER = """
import sys
from orch_items import mark_item
slug = sys.argv[1]
for idx in sys.argv[2:]:
    mark_item(slug, int(idx), "~")
    mark_item(slug, int(idx), "x")
"""


def test_concurrent_mark_item_no_lost_updates(tmp_path, monkeypatch):
    """4 procs each flip 4 distinct items TODO→DOING→DONE concurrently →
    all 16 end DONE, zero lines lost or torn."""
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    import orch_items

    slug = "race-project"
    indices = orch_items.append_next_items(slug, [f"item-{i}" for i in range(16)])
    assert len(indices) == 16

    env = dict(os.environ)
    env["PYTHONPATH"] = SRC
    env["OPENCLAW_WORKSPACE"] = str(tmp_path)
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", MARK_WORKER, slug,
             *[str(i) for i in indices[p * 4:(p + 1) * 4]]],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        for p in range(4)
    ]
    for p in procs:
        _, err = p.communicate(timeout=60)
        assert p.returncode == 0, err

    _, items = orch_items.parse_next(slug)
    done = [it for it in items if it.state == "x"]
    assert len(items) == 16, [it.text for it in items]
    assert len(done) == 16, [(it.index, it.state) for it in items]


def test_concurrent_append_next_items(tmp_path, monkeypatch):
    """4 procs appending 5 items each → all 20 present (whole-file rewrite
    used to drop concurrent appends)."""
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    import orch_items

    slug = "append-race"
    orch_items.append_next_items(slug, ["seed"])

    append_worker = """
import sys
from orch_items import append_next_items
append_next_items(sys.argv[1], [f"{sys.argv[2]}-{i}" for i in range(5)])
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = SRC
    env["OPENCLAW_WORKSPACE"] = str(tmp_path)
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", append_worker, slug, f"p{p}"],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        for p in range(4)
    ]
    for p in procs:
        _, err = p.communicate(timeout=60)
        assert p.returncode == 0, err

    _, items = orch_items.parse_next(slug)
    texts = {it.text for it in items}
    assert len(items) == 21, sorted(texts)
    for p in range(4):
        for i in range(5):
            assert f"p{p}-{i}" in texts
