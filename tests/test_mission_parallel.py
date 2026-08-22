"""Milestone-DAG parallelism: depends_on edges + concurrent scheduling.

Covers the 2026-08-22 flip (mission.parallel_milestones ON by default,
flag is the revert lever): decompose emits/normalizes depends_on, the
DAG scheduler orders by dependency, runs independents concurrently, and
preserves the sequential walk's failure semantics (ordering, not gating).

All tests use mock adapters + a stubbed run_agent_loop — no real API calls.
"""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import agent_loop
import config as config_mod
from llm import LLMResponse
from mission import (
    Feature,
    Milestone,
    Mission,
    _run_milestone_dag,
    decompose_mission,
    load_mission,
    run_mission,
    save_mission,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _setup_workspace(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    return tmp_path


def _adapter_for(milestones_payload, validation_passed=True):
    """Adapter that answers decompose calls with `milestones_payload` and
    validation calls with the given verdict."""

    class _Adapter:
        def complete(self, messages, **kwargs):
            user_content = next((m.content for m in messages if m.role == "user"), "")
            if "Did this milestone succeed?" in user_content:
                return LLMResponse(
                    content=json.dumps({"passed": validation_passed, "reason": "mock"}),
                    stop_reason="end_turn", input_tokens=10, output_tokens=5,
                )
            return LLMResponse(
                content=json.dumps({"milestones": milestones_payload}),
                stop_reason="end_turn", input_tokens=10, output_tokens=5,
            )

    return _Adapter()


def _cfg(monkeypatch, overrides):
    """Patch config.get so mission.* keys resolve from `overrides`."""
    real_get = config_mod.get

    def _fake_get(key, default=None):
        if key in overrides:
            return overrides[key]
        return real_get(key, default)

    monkeypatch.setattr(config_mod, "get", _fake_get)


def _stub_loop(record=None, gates=None, fail_titles=()):
    """Stand-in for run_agent_loop. Optionally records start/end order,
    waits on per-title threading gates, and fails named features."""
    lock = threading.Lock()

    def _run(goal, **kwargs):
        if record is not None:
            with lock:
                record.append(f"start:{goal}")
        if gates is not None and goal in gates:
            gates[goal]()
        status = "error" if goal in fail_titles else "done"
        if record is not None:
            with lock:
                record.append(f"end:{goal}")
        return SimpleNamespace(loop_id=f"stub-{goal}", status=status, steps=[])

    return _run


# ---------------------------------------------------------------------------
# decompose_mission: depends_on parsing
# ---------------------------------------------------------------------------

def test_decompose_parses_depends_on_indexes_to_ids(monkeypatch, tmp_path):
    _setup_workspace(monkeypatch, tmp_path)
    adapter = _adapter_for([
        {"title": "A", "features": ["fa"], "validation_criteria": [], "depends_on": []},
        {"title": "B", "features": ["fb"], "validation_criteria": [], "depends_on": []},
        {"title": "C", "features": ["fc"], "validation_criteria": [], "depends_on": [0, 1]},
    ])
    mission = decompose_mission("goal", adapter)
    a, b, c = mission.milestones
    assert a.depends_on == []
    assert b.depends_on == []
    assert c.depends_on == [a.id, b.id]


def test_decompose_absent_depends_on_chains_to_previous(monkeypatch, tmp_path):
    """No depends_on key -> sequential chain, the pre-DAG semantics."""
    _setup_workspace(monkeypatch, tmp_path)
    adapter = _adapter_for([
        {"title": "A", "features": ["fa"], "validation_criteria": []},
        {"title": "B", "features": ["fb"], "validation_criteria": []},
        {"title": "C", "features": ["fc"], "validation_criteria": []},
    ])
    mission = decompose_mission("goal", adapter)
    a, b, c = mission.milestones
    assert a.depends_on == []
    assert b.depends_on == [a.id]
    assert c.depends_on == [b.id]


def test_decompose_invalid_refs_dropped(monkeypatch, tmp_path):
    """Self/forward/non-int/dup refs are discarded; earlier valid ones kept."""
    _setup_workspace(monkeypatch, tmp_path)
    adapter = _adapter_for([
        {"title": "A", "features": ["fa"], "validation_criteria": []},
        {"title": "B", "features": ["fb"], "validation_criteria": [],
         "depends_on": [0, 0, 1, 2, -1, 99, "x", True]},
    ])
    mission = decompose_mission("goal", adapter)
    a, b = mission.milestones
    assert b.depends_on == [a.id]


def test_decompose_malformed_depends_on_falls_back_to_chain(monkeypatch, tmp_path):
    _setup_workspace(monkeypatch, tmp_path)
    adapter = _adapter_for([
        {"title": "A", "features": ["fa"], "validation_criteria": []},
        {"title": "B", "features": ["fb"], "validation_criteria": [], "depends_on": "garbage"},
    ])
    mission = decompose_mission("goal", adapter)
    a, b = mission.milestones
    assert b.depends_on == [a.id]


def test_decompose_ref_to_dropped_milestone_discarded(monkeypatch, tmp_path):
    """A feature-less milestone is dropped; refs to it resolve to nothing."""
    _setup_workspace(monkeypatch, tmp_path)
    adapter = _adapter_for([
        {"title": "A", "features": ["fa"], "validation_criteria": []},
        {"title": "Empty", "features": [], "validation_criteria": []},
        {"title": "C", "features": ["fc"], "validation_criteria": [], "depends_on": [1]},
    ])
    mission = decompose_mission("goal", adapter)
    assert [ms.title for ms in mission.milestones] == ["A", "C"]
    assert mission.milestones[1].depends_on == []


def test_heuristic_fallback_chains(monkeypatch, tmp_path):
    _setup_workspace(monkeypatch, tmp_path)

    class _Broken:
        def complete(self, messages, **kwargs):
            return LLMResponse(content="not json", stop_reason="end_turn",
                               input_tokens=1, output_tokens=1)

    mission = decompose_mission("some goal words here", _Broken())
    assert len(mission.milestones) == 2
    assert mission.milestones[1].depends_on == [mission.milestones[0].id]


# ---------------------------------------------------------------------------
# Persistence round-trip
# ---------------------------------------------------------------------------

def test_save_load_roundtrip_depends_on(monkeypatch, tmp_path):
    _setup_workspace(monkeypatch, tmp_path)
    a = Milestone(id="ms-a", title="A", features=[Feature(id="f1", title="fa", status="pending")],
                  validation_criteria=[], status="pending", depends_on=[])
    b = Milestone(id="ms-b", title="B", features=[Feature(id="f2", title="fb", status="pending")],
                  validation_criteria=[], status="pending", depends_on=["ms-a"])
    mission = Mission(id="m1", goal="g", project="rt-proj", milestones=[a, b],
                      status="pending", created_at="2026-08-22T00:00:00Z")
    import orch
    orch.ensure_project("rt-proj", "g")
    save_mission(mission, "rt-proj")
    loaded = load_mission("rt-proj")
    assert loaded.milestones[0].depends_on == []
    assert loaded.milestones[1].depends_on == ["ms-a"]


def test_load_legacy_mission_without_depends_on_chains(monkeypatch, tmp_path):
    """Pre-DAG mission.json (no depends_on key) loads as a sequential chain."""
    _setup_workspace(monkeypatch, tmp_path)
    import orch
    orch.ensure_project("legacy-proj", "g")
    payload = {
        "id": "m1", "goal": "g", "project": "legacy-proj",
        "status": "pending", "created_at": "2026-01-01T00:00:00Z",
        "milestones": [
            {"id": "ms-a", "title": "A", "status": "pending",
             "features": [{"id": "f1", "title": "fa", "status": "pending"}],
             "validation_criteria": [], "validation_result": None},
            {"id": "ms-b", "title": "B", "status": "pending",
             "features": [{"id": "f2", "title": "fb", "status": "pending"}],
             "validation_criteria": [], "validation_result": None},
        ],
    }
    path = orch.project_dir("legacy-proj") / "mission.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    loaded = load_mission("legacy-proj")
    assert loaded.milestones[0].depends_on == []
    assert loaded.milestones[1].depends_on == ["ms-a"]


# ---------------------------------------------------------------------------
# Scheduling: ordering, concurrency, failure semantics
# ---------------------------------------------------------------------------

def test_independent_milestones_run_concurrently(monkeypatch, tmp_path):
    """Two independent milestones overlap: each feature blocks on a barrier
    that only releases when both are running. Sequential execution would
    time out and break the barrier."""
    _setup_workspace(monkeypatch, tmp_path)
    _cfg(monkeypatch, {"mission.parallel_milestones": True, "mission.milestone_workers": 2})
    barrier = threading.Barrier(2, timeout=30)
    gates = {"fa": barrier.wait, "fb": barrier.wait}
    monkeypatch.setattr(agent_loop, "run_agent_loop", _stub_loop(gates=gates))
    adapter = _adapter_for([
        {"title": "A", "features": ["fa"], "validation_criteria": [], "depends_on": []},
        {"title": "B", "features": ["fb"], "validation_criteria": [], "depends_on": []},
    ])
    result = run_mission("concurrent goal", project="conc-proj", adapter=adapter)
    assert not barrier.broken
    assert result.status == "done"
    assert result.milestones_done == 2


def test_dependent_milestone_waits_for_dependency(monkeypatch, tmp_path):
    _setup_workspace(monkeypatch, tmp_path)
    _cfg(monkeypatch, {"mission.parallel_milestones": True, "mission.milestone_workers": 2})
    record = []
    monkeypatch.setattr(agent_loop, "run_agent_loop", _stub_loop(record=record))
    adapter = _adapter_for([
        {"title": "A", "features": ["fa"], "validation_criteria": [], "depends_on": []},
        {"title": "B", "features": ["fb"], "validation_criteria": [], "depends_on": [0]},
    ])
    result = run_mission("dag order goal", project="order-proj", adapter=adapter)
    assert result.status == "done"
    assert record.index("end:fa") < record.index("start:fb")


def test_failed_dependency_does_not_gate_dependent(monkeypatch, tmp_path):
    """Ordering, not gating: B still runs after A fails (sequential parity)."""
    _setup_workspace(monkeypatch, tmp_path)
    _cfg(monkeypatch, {"mission.parallel_milestones": True, "mission.milestone_workers": 2})
    record = []
    monkeypatch.setattr(agent_loop, "run_agent_loop",
                        _stub_loop(record=record, fail_titles=("fa",)))
    adapter = _adapter_for([
        {"title": "A", "features": ["fa"], "validation_criteria": ["must pass"], "depends_on": []},
        {"title": "B", "features": ["fb"], "validation_criteria": [], "depends_on": [0]},
    ], validation_passed=False)
    result = run_mission("failure parity goal", project="fail-proj", adapter=adapter)
    assert "start:fb" in record
    assert record.index("end:fa") < record.index("start:fb")
    loaded = load_mission("fail-proj")
    assert loaded.milestones[0].status == "failed"
    # B has no validation criteria -> validates trivially -> done
    assert loaded.milestones[1].status == "done"
    assert result.status == "partial"
    assert result.milestones_done == 1


def test_flag_off_runs_sequentially(monkeypatch, tmp_path):
    """mission.parallel_milestones=False is the revert lever: independent
    milestones still execute strictly in list order."""
    _setup_workspace(monkeypatch, tmp_path)
    _cfg(monkeypatch, {"mission.parallel_milestones": False})
    record = []
    monkeypatch.setattr(agent_loop, "run_agent_loop", _stub_loop(record=record))
    adapter = _adapter_for([
        {"title": "A", "features": ["fa"], "validation_criteria": [], "depends_on": []},
        {"title": "B", "features": ["fb"], "validation_criteria": [], "depends_on": []},
    ])
    result = run_mission("sequential lever goal", project="seq-proj", adapter=adapter)
    assert result.status == "done"
    assert record == ["start:fa", "end:fa", "start:fb", "end:fb"]


def test_dag_stall_fallback_runs_everything(monkeypatch, tmp_path):
    """A malformed (cyclic) dep set from the load path can't deadlock:
    the scheduler falls back to list order and every milestone runs."""
    _setup_workspace(monkeypatch, tmp_path)
    ran = []

    def _run_one(idx, ms):
        ran.append(ms.id)
        ms.status = "done"

    a = Milestone(id="ms-a", title="A", features=[], validation_criteria=[],
                  status="pending", depends_on=["ms-b"])
    b = Milestone(id="ms-b", title="B", features=[], validation_criteria=[],
                  status="pending", depends_on=["ms-a"])
    mission = Mission(id="m1", goal="g", project="p", milestones=[a, b],
                      status="running", created_at="2026-08-22T00:00:00Z")
    _run_milestone_dag(mission, _run_one, lambda msg: None, max_workers=2)
    assert ran == ["ms-a", "ms-b"]


def test_dag_thread_exception_marks_milestone_failed(monkeypatch, tmp_path):
    _setup_workspace(monkeypatch, tmp_path)

    def _run_one(idx, ms):
        if ms.id == "ms-a":
            raise RuntimeError("boom")
        ms.status = "done"

    a = Milestone(id="ms-a", title="A", features=[], validation_criteria=[],
                  status="pending", depends_on=[])
    b = Milestone(id="ms-b", title="B", features=[], validation_criteria=[],
                  status="pending", depends_on=["ms-a"])
    mission = Mission(id="m1", goal="g", project="p", milestones=[a, b],
                      status="running", created_at="2026-08-22T00:00:00Z")
    _run_milestone_dag(mission, _run_one, lambda msg: None, max_workers=2)
    assert a.status == "failed"
    assert "boom" in (a.validation_result or "")
    assert b.status == "done"
