"""Tests for _run_steps_dag — dep-aware parallel execution pool."""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from agent_loop import _run_steps_dag


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_outcome(status: str = "done", result: str = "ok", step_text: str = "") -> dict:
    return {
        "status": status,
        "result": result,
        "summary": step_text[:40] if step_text else result[:40],
        "tokens_in": 10,
        "tokens_out": 20,
    }


def _make_adapter():
    adapter = MagicMock()
    adapter.model_key = "test-model"
    return adapter


def _mock_execute_step(outcomes_by_step: dict):
    """Return a side_effect for _execute_step that returns different outcomes per step_num."""
    def _side_effect(**kwargs):
        step_num = kwargs.get("step_num", 1)
        return outcomes_by_step.get(step_num, _make_outcome())
    return _side_effect


# ---------------------------------------------------------------------------
# TestRunStepsDag — basic topology
# ---------------------------------------------------------------------------

class TestRunStepsDag:
    def test_single_step_no_deps(self):
        """One step with no deps runs immediately."""
        steps = ["do the thing"]
        deps = {1: set()}

        with patch("agent_loop._execute_step", return_value=_make_outcome("done", "result")):
            outcomes = _run_steps_dag(
                goal="test",
                steps=steps,
                deps=deps,
                adapter=_make_adapter(),
                ancestry_context="",
                tools=[],
                verbose=False,
                max_workers=2,
            )

        assert len(outcomes) == 1
        assert outcomes[0]["status"] == "done"

    def test_all_independent_all_run(self):
        """Three steps with no deps all run in parallel."""
        steps = ["step A", "step B", "step C"]
        deps = {1: set(), 2: set(), 3: set()}

        call_order = []

        def _fake_exec(**kwargs):
            call_order.append(kwargs["step_num"])
            return _make_outcome("done", f"result_{kwargs['step_num']}")

        with patch("agent_loop._execute_step", side_effect=_fake_exec):
            outcomes = _run_steps_dag(
                goal="test",
                steps=steps,
                deps=deps,
                adapter=_make_adapter(),
                ancestry_context="",
                tools=[],
                verbose=False,
                max_workers=4,
            )

        assert len(outcomes) == 3
        assert all(o["status"] == "done" for o in outcomes)
        assert set(call_order) == {1, 2, 3}

    def test_diamond_dag_ordering(self):
        """Diamond DAG: 1→{2,3}→4. Steps 2 and 3 should run after 1; 4 after both."""
        # 1 has no deps; 2 and 3 depend on 1; 4 depends on 2 and 3
        steps = ["root", "left", "right", "merge"]
        deps = {1: set(), 2: {1}, 3: {1}, 4: {2, 3}}

        completion_log = []
        start_log = {}
        lock = threading.Lock()

        def _fake_exec(**kwargs):
            step_num = kwargs["step_num"]
            with lock:
                start_log[step_num] = time.monotonic()
            # Simulate brief work
            time.sleep(0.02)
            result = f"done_{step_num}"
            with lock:
                completion_log.append(step_num)
            return _make_outcome("done", result)

        with patch("agent_loop._execute_step", side_effect=_fake_exec):
            outcomes = _run_steps_dag(
                goal="diamond",
                steps=steps,
                deps=deps,
                adapter=_make_adapter(),
                ancestry_context="",
                tools=[],
                verbose=False,
                max_workers=4,
            )

        assert len(outcomes) == 4
        assert all(o["status"] == "done" for o in outcomes)
        # Step 1 must complete before 2, 3, 4
        assert completion_log.index(1) < completion_log.index(2)
        assert completion_log.index(1) < completion_log.index(3)
        # Steps 2 and 3 must both complete before 4
        assert completion_log.index(2) < completion_log.index(4)
        assert completion_log.index(3) < completion_log.index(4)

    def test_dep_results_passed_as_context(self):
        """Dependent step receives completed dep results as completed_context."""
        steps = ["gather data", "analyze data"]
        deps = {1: set(), 2: {1}}

        captured_contexts = {}

        def _fake_exec(**kwargs):
            step_num = kwargs["step_num"]
            captured_contexts[step_num] = list(kwargs.get("completed_context", []))
            result = "gathered_result" if step_num == 1 else "analysis_done"
            return _make_outcome("done", result)

        with patch("agent_loop._execute_step", side_effect=_fake_exec):
            outcomes = _run_steps_dag(
                goal="pipeline",
                steps=steps,
                deps=deps,
                adapter=_make_adapter(),
                ancestry_context="",
                tools=[],
                verbose=False,
                max_workers=2,
            )

        assert outcomes[1]["status"] == "done"
        # Step 2 should have received step 1's result as context
        assert len(captured_contexts[2]) == 1
        assert "gathered_result" in captured_contexts[2][0]

    def test_no_dep_results_for_independent_steps(self):
        """Steps with no deps get empty completed_context."""
        steps = ["parallel A", "parallel B"]
        deps = {1: set(), 2: set()}

        captured_contexts = {}

        def _fake_exec(**kwargs):
            step_num = kwargs["step_num"]
            captured_contexts[step_num] = list(kwargs.get("completed_context", []))
            return _make_outcome("done", f"result_{step_num}")

        with patch("agent_loop._execute_step", side_effect=_fake_exec):
            _run_steps_dag(
                goal="parallel",
                steps=steps,
                deps=deps,
                adapter=_make_adapter(),
                ancestry_context="",
                tools=[],
                verbose=False,
                max_workers=2,
            )

        assert captured_contexts[1] == []
        assert captured_contexts[2] == []

    def test_blocked_dep_still_unblocks_downstream(self):
        """If a dep step is blocked, downstream steps still run (with empty dep context)."""
        steps = ["will_fail", "depends_on_fail"]
        deps = {1: set(), 2: {1}}

        def _fake_exec(**kwargs):
            step_num = kwargs["step_num"]
            if step_num == 1:
                return _make_outcome("blocked", "", "will_fail")
            return _make_outcome("done", "ran_anyway")

        with patch("agent_loop._execute_step", side_effect=_fake_exec):
            outcomes = _run_steps_dag(
                goal="resilience",
                steps=steps,
                deps=deps,
                adapter=_make_adapter(),
                ancestry_context="",
                tools=[],
                verbose=False,
                max_workers=2,
            )

        assert outcomes[0]["status"] == "blocked"
        assert outcomes[1]["status"] == "done"

    def test_returns_list_in_step_order(self):
        """Outcomes are returned in step-index order regardless of completion order."""
        steps = ["slow", "fast"]
        deps = {1: set(), 2: set()}

        def _fake_exec(**kwargs):
            step_num = kwargs["step_num"]
            if step_num == 1:
                time.sleep(0.05)  # slow
            return _make_outcome("done", f"result_{step_num}")

        with patch("agent_loop._execute_step", side_effect=_fake_exec):
            outcomes = _run_steps_dag(
                goal="ordering",
                steps=steps,
                deps=deps,
                adapter=_make_adapter(),
                ancestry_context="",
                tools=[],
                verbose=False,
                max_workers=2,
            )

        assert len(outcomes) == 2
        assert "result_1" in outcomes[0]["result"]
        assert "result_2" in outcomes[1]["result"]

    def test_execution_error_marks_step_blocked(self):
        """If _execute_step raises, the outcome is marked blocked."""
        steps = ["crasher"]
        deps = {1: set()}

        def _fake_exec(**kwargs):
            raise RuntimeError("boom")

        with patch("agent_loop._execute_step", side_effect=_fake_exec):
            outcomes = _run_steps_dag(
                goal="crash",
                steps=steps,
                deps=deps,
                adapter=_make_adapter(),
                ancestry_context="",
                tools=[],
                verbose=False,
                max_workers=2,
            )

        assert outcomes[0]["status"] == "blocked"
        assert "boom" in outcomes[0].get("stuck_reason", "")

    def test_shared_ctx_passed_through(self):
        """shared_ctx is forwarded to _execute_step."""
        steps = ["one step"]
        deps = {1: set()}
        shared = {"key": "value"}

        captured = {}

        def _fake_exec(**kwargs):
            captured["shared_ctx"] = kwargs.get("shared_ctx")
            return _make_outcome()

        with patch("agent_loop._execute_step", side_effect=_fake_exec):
            _run_steps_dag(
                goal="shared_ctx_test",
                steps=steps,
                deps=deps,
                adapter=_make_adapter(),
                ancestry_context="",
                tools=[],
                verbose=False,
                max_workers=2,
                shared_ctx=shared,
            )

        assert captured["shared_ctx"] is shared

    def test_max_workers_limits_concurrency(self):
        """With max_workers=1, steps with no deps run sequentially."""
        steps = ["A", "B", "C"]
        deps = {1: set(), 2: set(), 3: set()}

        order = []
        lock = threading.Lock()

        def _fake_exec(**kwargs):
            step_num = kwargs["step_num"]
            time.sleep(0.02)
            with lock:
                order.append(step_num)
            return _make_outcome("done", f"r{step_num}")

        with patch("agent_loop._execute_step", side_effect=_fake_exec):
            outcomes = _run_steps_dag(
                goal="serial",
                steps=steps,
                deps=deps,
                adapter=_make_adapter(),
                ancestry_context="",
                tools=[],
                verbose=False,
                max_workers=1,
            )

        assert len(outcomes) == 3
        assert all(o["status"] == "done" for o in outcomes)
        # With max_workers=1, all 3 steps run but serially
        assert set(order) == {1, 2, 3}


# ---------------------------------------------------------------------------
# TestRunStepsDag — integration with planner.parse_dependencies
# ---------------------------------------------------------------------------

class TestDagWithParsedDeps:
    """Verify that the DAG executor integrates correctly with parse_dependencies output."""

    def test_after_tag_creates_correct_dep(self):
        """Steps with [after:N] tags should create correct dep graph via parse_dependencies."""
        from planner import parse_dependencies

        raw_steps = [
            "Fetch data from API",
            "Parse the JSON response [after:1]",
            "Write report [after:2]",
        ]
        clean_steps, deps = parse_dependencies(raw_steps)

        call_order = []

        def _fake_exec(**kwargs):
            call_order.append(kwargs["step_num"])
            return _make_outcome("done", f"r{kwargs['step_num']}")

        with patch("agent_loop._execute_step", side_effect=_fake_exec):
            outcomes = _run_steps_dag(
                goal="pipeline",
                steps=clean_steps,
                deps=deps,
                adapter=_make_adapter(),
                ancestry_context="",
                tools=[],
                verbose=False,
                max_workers=3,
            )

        assert len(outcomes) == 3
        assert all(o["status"] == "done" for o in outcomes)
        # Must run in order: 1 → 2 → 3
        assert call_order == [1, 2, 3]

    def test_parallel_after_tags(self):
        """Steps 2 and 3 depending on step 1 should run concurrently after step 1."""
        from planner import parse_dependencies, build_execution_levels

        raw_steps = [
            "Initialize environment",
            "Run test suite A [after:1]",
            "Run test suite B [after:1]",
            "Collect results [after:2,3]",
        ]
        clean_steps, deps = parse_dependencies(raw_steps)
        levels = build_execution_levels(deps)
        parallel_levels = [l for l in levels if len(l) > 1]

        # Confirm parse produced parallelism: [2, 3] should be in same level
        assert any(2 in lvl and 3 in lvl for lvl in parallel_levels), \
            f"Expected steps 2 and 3 in same parallel level; got levels={levels}"

        started_at: dict = {}
        lock = threading.Lock()

        def _fake_exec(**kwargs):
            step_num = kwargs["step_num"]
            with lock:
                started_at[step_num] = time.monotonic()
            time.sleep(0.03)
            return _make_outcome("done", f"r{step_num}")

        with patch("agent_loop._execute_step", side_effect=_fake_exec):
            outcomes = _run_steps_dag(
                goal="parallel suites",
                steps=clean_steps,
                deps=deps,
                adapter=_make_adapter(),
                ancestry_context="",
                tools=[],
                verbose=False,
                max_workers=4,
            )

        assert len(outcomes) == 4
        assert all(o["status"] == "done" for o in outcomes)
        # Steps 2 and 3 start after step 1 but before step 4
        assert started_at[1] < started_at[2]
        assert started_at[1] < started_at[3]
        assert started_at[2] < started_at[4] or started_at[3] < started_at[4]
        # Steps 2 and 3 should start within a small window of each other (parallel)
        delta_23 = abs(started_at[2] - started_at[3])
        assert delta_23 < 0.05, f"Steps 2 and 3 started too far apart: {delta_23:.3f}s"
