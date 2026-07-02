"""BACKLOG #2 regression: parallel-batch steps must carry real NEXT.md item
indices (not hardcoded -1), mark done items, and never render "Step -1"."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import agent_loop
from agent_loop import _run_parallel_batch, step_from_decompose


class _Ctx:
    goal = "test goal"
    adapter = None
    ancestry_context = ""
    verbose = False
    project = "test-project"


class _OrchStub:
    STATE_DONE = "done"

    def __init__(self):
        self.marked = []

    def mark_item(self, project, idx, state):
        self.marked.append((project, idx, state))


@pytest.fixture
def orch_stub(monkeypatch):
    stub = _OrchStub()
    monkeypatch.setattr(agent_loop, "_orch", lambda: stub)
    return stub


def _run_batch(monkeypatch, outcomes, batch_item_indices):
    monkeypatch.setattr(
        agent_loop, "_run_steps_parallel",
        lambda **kw: outcomes,
    )
    step_outcomes = []
    _run_parallel_batch(
        _Ctx(), "lead step", ["peer one", "peer two"],
        step_outcomes=step_outcomes,
        completed_context=[],
        remaining_steps=[],
        remaining_indices=[],
        loop_shared_ctx={},
        resolve_tools_fn=lambda: [],
        parallel_fan_out=2,
        proj_artifact_dir="",
        iteration=0,
        step_idx=0,
        batch_item_indices=batch_item_indices,
    )
    return step_outcomes


_DONE = [{"status": "done", "result": "r1"},
         {"status": "done", "result": "r2"},
         {"status": "blocked", "stuck_reason": "x"}]


def test_batch_outcomes_carry_item_indices(monkeypatch, orch_stub):
    outcomes = _run_batch(monkeypatch, _DONE, [3, 7, 9])
    assert [s.index for s in outcomes] == [3, 7, 9]


def test_batch_marks_done_items(monkeypatch, orch_stub):
    _run_batch(monkeypatch, _DONE, [3, 7, 9])
    # two done steps marked; blocked step (idx 9) not marked
    assert orch_stub.marked == [("test-project", 3, "done"),
                                ("test-project", 7, "done")]


def test_batch_tolerates_missing_indices(monkeypatch, orch_stub):
    outcomes = _run_batch(monkeypatch, _DONE, None)
    assert [s.index for s in outcomes] == [-1, -1, -1]
    assert orch_stub.marked == []


def test_batch_skips_mark_for_injected_steps(monkeypatch, orch_stub):
    _run_batch(monkeypatch, _DONE, [3, -1, -1])
    assert orch_stub.marked == [("test-project", 3, "done")]


def test_handle_result_numbers_by_position_not_index():
    """handle.py result assembly must not print 'Step -1' for batch steps."""
    from handle import _loop_result_to_handle
    from agent_loop import LoopResult

    steps = [
        step_from_decompose("first", 4, status="done", result="a"),
        step_from_decompose("injected", -1, status="done", result="b"),
        step_from_decompose("skipped", 5, status="blocked", result=""),
    ]
    lr = LoopResult(loop_id="x", goal="g", project="p", steps=steps,
                    status="done")
    hr = _loop_result_to_handle(
        lr, handle_id="h1", message="g", confidence=0.9, reason="test",
        started_at=time.monotonic(),
    )
    assert "Step -1" not in hr.result
    assert "**Step 1: first**" in hr.result
    assert "**Step 2: injected**" in hr.result
