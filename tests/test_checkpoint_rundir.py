"""(h) slice 2 — checkpoint substrate: run-dir placement, in_flight marker,
legacy migration, call-seq rebuild (BACKEND_RESILIENCE_DESIGN §3, slice 2).

The legacy orch_root()/checkpoints dir resolved differently per environment
(52 stale files in the repo, 0 in the live workspace); checkpoints now land
in <run-dir>/build/checkpoint.json when a run is active. The in_flight
marker is the mid-step-crash discriminator (the hermes goal-2 wound).
"""

import json
import os
from types import SimpleNamespace

import pytest

import checkpoint as ckpt_module
from checkpoint import (
    delete_checkpoint,
    list_checkpoints,
    load_checkpoint,
    write_checkpoint,
)


def _outcome(index, text="step", status="done"):
    return SimpleNamespace(index=index, text=text, status=status, result="r",
                           tokens_in=1, tokens_out=1, elapsed_ms=5)


@pytest.fixture
def run_dir(tmp_path):
    """An active run dir with metadata.json, current for the duration."""
    rd = tmp_path / "runs" / "h123-brave-otter"
    rd.mkdir(parents=True)
    (rd / "metadata.json").write_text(json.dumps({"handle_id": "h123"}))
    from runs import set_current_run_dir
    set_current_run_dir(rd)
    try:
        yield rd
    finally:
        set_current_run_dir(None)


@pytest.fixture
def legacy_dir(tmp_path, monkeypatch):
    d = tmp_path / "legacy-ckpts"
    d.mkdir()
    monkeypatch.setattr(ckpt_module, "_checkpoint_dir", lambda: d)
    return d


def test_write_lands_in_run_dir_with_handle_id(run_dir, legacy_dir):
    write_checkpoint("loopA", "goal", "proj", ["s1", "s2"], [_outcome(1)])
    path = run_dir / "build" / "checkpoint.json"
    assert path.exists()
    data = json.loads(path.read_text())
    assert data["loop_id"] == "loopA"
    assert data["handle_id"] == "h123"
    assert not list(legacy_dir.glob("ckpt_*.json"))  # not duplicated


def test_write_falls_back_to_legacy_without_run(legacy_dir):
    write_checkpoint("loopB", "goal", "", ["s1"], [])
    assert (legacy_dir / "ckpt_loopB.json").exists()


def test_in_flight_stamped_and_cleared(run_dir, legacy_dir):
    # pre-step write stamps the marker
    write_checkpoint("loopC", "goal", "", ["s1", "s2"], [_outcome(1)],
                     in_flight_index=2)
    ckpt = load_checkpoint("loopC")
    assert ckpt.in_flight is not None
    assert ckpt.in_flight["index"] == 2
    assert ckpt.in_flight["pid"] == os.getpid()
    assert ckpt.in_flight["started_at"]
    # post-step write (no marker) clears it
    write_checkpoint("loopC", "goal", "", ["s1", "s2"],
                     [_outcome(1), _outcome(2)])
    ckpt = load_checkpoint("loopC")
    assert ckpt.in_flight is None
    assert len(ckpt.completed) == 2


def test_crash_shape_survives_reload(run_dir, legacy_dir):
    """A mid-step death leaves the marker on disk for a later process."""
    write_checkpoint("loopD", "goal", "", ["s1", "s2", "s3"],
                     [_outcome(1), _outcome(2)], in_flight_index=3)
    # a fresh process has no run-dir contextvar — the newest-first runs scan
    # is what finds the corpse
    from runs import set_current_run_dir
    set_current_run_dir(None)
    root = run_dir.parent
    orig = ckpt_module._runs_root
    ckpt_module._runs_root = lambda: root
    try:
        ckpt = load_checkpoint("loopD")
        assert ckpt is not None
        assert ckpt.in_flight["index"] == 3
        assert ckpt.handle_id == "h123"
    finally:
        ckpt_module._runs_root = orig


def test_legacy_checkpoints_still_load(legacy_dir):
    """Migration: pre-move checkpoints (old dir, old shape) keep loading."""
    old = {"loop_id": "loopE", "goal": "g", "project": "",
           "steps": ["s1"], "completed": [], "timestamp": "2026-07-01T00:00:00"}
    (legacy_dir / "ckpt_loopE.json").write_text(json.dumps(old))
    ckpt = load_checkpoint("loopE")
    assert ckpt is not None
    assert ckpt.handle_id == ""
    assert ckpt.in_flight is None


def test_delete_removes_run_dir_checkpoint(run_dir, legacy_dir):
    write_checkpoint("loopF", "goal", "", ["s1"], [_outcome(1)])
    path = run_dir / "build" / "checkpoint.json"
    assert path.exists()
    delete_checkpoint("loopF")
    assert not path.exists()


def test_delete_spares_other_loops_checkpoint(run_dir, legacy_dir):
    """Same run dir, different loop (project-loop restart): don't clobber."""
    write_checkpoint("loopG2", "goal", "", ["s1"], [_outcome(1)])
    delete_checkpoint("loopG1")  # different loop_id
    assert (run_dir / "build" / "checkpoint.json").exists()


def test_list_covers_both_locations(run_dir, legacy_dir, monkeypatch):
    write_checkpoint("loopH", "goal", "", ["s1"], [])  # → run dir
    old = {"loop_id": "loopI", "goal": "g", "project": "", "steps": [],
           "completed": [], "timestamp": "2026-07-01T00:00:00"}
    (legacy_dir / "ckpt_loopI.json").write_text(json.dumps(old))
    monkeypatch.setattr(ckpt_module, "_runs_root", lambda: run_dir.parent)
    ids = {c.loop_id for c in list_checkpoints()}
    assert {"loopH", "loopI"} <= ids


def test_call_seq_rebuilds_from_disk(tmp_path):
    """Crash+resume must not overwrite call-00001.json (runs.py counter)."""
    import runs as runs_module
    rd = tmp_path / "run"
    calls = rd / "build" / "calls"
    calls.mkdir(parents=True)
    (calls / "call-00007.json").write_text("{}")
    (calls / "call-00003.json").write_text("{}")
    runs_module._CALL_COUNTERS.pop(str(rd), None)  # simulate fresh process
    assert runs_module._next_call_seq(rd) == 8
    assert runs_module._next_call_seq(rd) == 9


def test_call_seq_fresh_dir_starts_at_one(tmp_path):
    import runs as runs_module
    rd = tmp_path / "newrun"
    runs_module._CALL_COUNTERS.pop(str(rd), None)
    assert runs_module._next_call_seq(rd) == 1
