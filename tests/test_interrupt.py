"""Tests for interrupt.py — source-agnostic interrupt queue."""

import json
import os
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def queue_path(tmp_path):
    return tmp_path / "interrupts.jsonl"


@pytest.fixture
def queue(queue_path):
    from interrupt import InterruptQueue
    return InterruptQueue(queue_path=queue_path)


# ---------------------------------------------------------------------------
# InterruptQueue.post / poll / peek / clear
# ---------------------------------------------------------------------------

class TestInterruptQueue:
    def test_post_creates_file(self, queue, queue_path):
        queue.post("also check rate limiting", source="cli", intent="additive")
        assert queue_path.exists()

    def test_post_returns_interrupt(self, queue):
        from interrupt import Interrupt
        intr = queue.post("stop", source="telegram", intent="stop")
        assert isinstance(intr, Interrupt)
        assert intr.intent == "stop"
        assert intr.source == "telegram"
        assert intr.id  # non-empty

    def test_poll_returns_pending(self, queue):
        queue.post("also do X", source="cli", intent="additive")
        queue.post("stop", source="cli", intent="stop")
        pending = queue.poll()
        assert len(pending) == 2

    def test_poll_marks_applied(self, queue, queue_path):
        queue.post("also do X", source="cli", intent="additive")
        queue.poll()
        # Second poll should be empty
        pending2 = queue.poll()
        assert len(pending2) == 0

    def test_peek_does_not_consume(self, queue):
        queue.post("also do X", source="cli", intent="additive")
        p1 = queue.peek()
        p2 = queue.peek()
        assert len(p1) == 1
        assert len(p2) == 1

    def test_poll_empty_queue(self, queue):
        assert queue.poll() == []

    def test_is_empty(self, queue):
        assert queue.is_empty()
        queue.post("stop", source="cli", intent="stop")
        assert not queue.is_empty()

    def test_clear_removes_pending(self, queue):
        queue.post("do X", source="cli", intent="additive")
        queue.post("do Y", source="cli", intent="additive")
        n = queue.clear()
        assert n == 2
        assert queue.is_empty()

    def test_clear_already_applied_not_counted(self, queue):
        queue.post("do X", source="cli", intent="additive")
        queue.poll()  # mark applied
        n = queue.clear()
        assert n == 0

    def test_poll_order_preserved(self, queue):
        queue.post("first", source="cli", intent="additive")
        queue.post("second", source="cli", intent="priority")
        pending = queue.poll()
        assert pending[0].message == "first"
        assert pending[1].message == "second"

    def test_to_dict_round_trip(self, queue):
        from interrupt import Interrupt
        intr = queue.post("do X", source="cli", intent="additive")
        d = intr.to_dict()
        recovered = Interrupt.from_dict(d)
        assert recovered.id == intr.id
        assert recovered.message == intr.message
        assert recovered.intent == intr.intent


# ---------------------------------------------------------------------------
# Intent classification (heuristic)
# ---------------------------------------------------------------------------

class TestClassifyIntentHeuristic:
    def test_stop_keywords(self):
        from interrupt import _classify_intent
        intent, steps, goal = _classify_intent("stop", adapter=None)
        assert intent == "stop"

    def test_stop_keyword_halt(self):
        from interrupt import _classify_intent
        intent, _, _ = _classify_intent("halt everything", adapter=None)
        assert intent == "stop"

    def test_additive_default(self):
        from interrupt import _classify_intent
        intent, steps, _ = _classify_intent("also research competitor pricing", adapter=None)
        assert intent == "additive"
        assert len(steps) >= 1

    def test_corrective_keyword_instead(self):
        from interrupt import _classify_intent
        intent, _, _ = _classify_intent("focus on security instead", adapter=None)
        assert intent == "corrective"

    def test_priority_keyword_first(self):
        from interrupt import _classify_intent
        intent, steps, _ = _classify_intent("first check the API rate limits", adapter=None)
        assert intent == "priority"

    def test_stop_exclamation(self):
        from interrupt import _classify_intent
        intent, _, _ = _classify_intent("Stop!", adapter=None)
        assert intent == "stop"


class TestClassifyIntentLLM:
    def test_llm_additive(self):
        from interrupt import _classify_intent
        mock_adapter = MagicMock()
        mock_resp = MagicMock()
        mock_resp.content = json.dumps({
            "intent": "additive",
            "new_steps": ["check rate limits", "verify API keys"],
            "replacement_goal": None,
        })
        mock_adapter.complete.return_value = mock_resp

        intent, steps, goal = _classify_intent("also check rate limits", adapter=mock_adapter)
        assert intent == "additive"
        assert "check rate limits" in steps

    def test_llm_stop(self):
        from interrupt import _classify_intent
        mock_adapter = MagicMock()
        mock_resp = MagicMock()
        mock_resp.content = json.dumps({
            "intent": "stop",
            "new_steps": [],
            "replacement_goal": None,
        })
        mock_adapter.complete.return_value = mock_resp
        intent, _, _ = _classify_intent("cancel everything", adapter=mock_adapter)
        assert intent == "stop"

    def test_llm_corrective(self):
        from interrupt import _classify_intent
        mock_adapter = MagicMock()
        mock_resp = MagicMock()
        mock_resp.content = json.dumps({
            "intent": "corrective",
            "new_steps": ["focus on security"],
            "replacement_goal": "Analyze security vulnerabilities instead",
        })
        mock_adapter.complete.return_value = mock_resp
        intent, steps, replacement = _classify_intent("actually focus on security", adapter=mock_adapter)
        assert intent == "corrective"
        assert replacement == "Analyze security vulnerabilities instead"

    def test_llm_falls_back_on_bad_json(self):
        from interrupt import _classify_intent
        mock_adapter = MagicMock()
        mock_resp = MagicMock()
        mock_resp.content = "this is not json"
        mock_adapter.complete.return_value = mock_resp
        # Should not raise, falls back to heuristic
        intent, steps, _ = _classify_intent("also check rate limits", adapter=mock_adapter)
        assert intent in {"additive", "corrective", "priority", "stop"}

    def test_llm_invalid_intent_fallback(self):
        from interrupt import _classify_intent
        mock_adapter = MagicMock()
        mock_resp = MagicMock()
        mock_resp.content = json.dumps({"intent": "unknown_thing", "new_steps": [], "replacement_goal": None})
        mock_adapter.complete.return_value = mock_resp
        intent, _, _ = _classify_intent("do something", adapter=mock_adapter)
        assert intent == "additive"  # default for invalid


# ---------------------------------------------------------------------------
# apply_interrupt_to_steps
# ---------------------------------------------------------------------------

class TestApplyInterruptToSteps:
    def _make_interrupt(self, intent, new_steps=None, replacement_goal=None):
        from interrupt import Interrupt
        return Interrupt(
            id="test01",
            message="test",
            source="cli",
            intent=intent,
            new_steps=new_steps or [],
            replacement_goal=replacement_goal,
        )

    def test_stop_clears_steps(self):
        from interrupt import apply_interrupt_to_steps
        intr = self._make_interrupt("stop")
        remaining, goal, should_stop = apply_interrupt_to_steps(intr, ["step1", "step2"], "my goal")
        assert should_stop is True
        assert remaining == []

    def test_additive_appends(self):
        from interrupt import apply_interrupt_to_steps
        intr = self._make_interrupt("additive", new_steps=["new step A"])
        remaining, goal, should_stop = apply_interrupt_to_steps(intr, ["step1", "step2"], "my goal")
        assert should_stop is False
        assert remaining == ["step1", "step2", "new step A"]

    def test_priority_prepends(self):
        from interrupt import apply_interrupt_to_steps
        intr = self._make_interrupt("priority", new_steps=["urgent step"])
        remaining, goal, should_stop = apply_interrupt_to_steps(intr, ["step1", "step2"], "my goal")
        assert should_stop is False
        assert remaining == ["urgent step", "step1", "step2"]

    def test_corrective_replaces_steps(self):
        from interrupt import apply_interrupt_to_steps
        intr = self._make_interrupt("corrective", new_steps=["new plan A", "new plan B"], replacement_goal="New goal")
        remaining, goal, should_stop = apply_interrupt_to_steps(intr, ["old step"], "my goal")
        assert should_stop is False
        assert remaining == ["new plan A", "new plan B"]
        assert goal == "New goal"

    def test_corrective_keeps_remaining_if_no_new_steps(self):
        from interrupt import apply_interrupt_to_steps
        intr = self._make_interrupt("corrective", new_steps=[], replacement_goal="New goal")
        remaining, goal, should_stop = apply_interrupt_to_steps(intr, ["step1"], "my goal")
        assert remaining == ["step1"]
        assert goal == "New goal"

    def test_additive_empty_new_steps(self):
        from interrupt import apply_interrupt_to_steps
        intr = self._make_interrupt("additive", new_steps=[])
        remaining, goal, should_stop = apply_interrupt_to_steps(intr, ["step1"], "goal")
        assert remaining == ["step1"]
        assert not should_stop


# ---------------------------------------------------------------------------
# Loop lock
# ---------------------------------------------------------------------------

class TestLoopLock:
    def test_set_and_get(self, tmp_path, monkeypatch):
        lock_path = tmp_path / "loop.lock"
        monkeypatch.setattr("interrupt._default_lock_path", lambda: lock_path)
        from interrupt import set_loop_running, get_running_loop, clear_loop_running, is_loop_running

        assert not is_loop_running()
        set_loop_running("abc123", "test goal")
        info = get_running_loop()
        assert info is not None
        assert info["loop_id"] == "abc123"
        assert is_loop_running()

        clear_loop_running()
        assert not is_loop_running()

    def test_stale_lock_cleared(self, tmp_path, monkeypatch):
        """Lock with dead PID should be treated as not running."""
        lock_path = tmp_path / "loop.lock"
        monkeypatch.setattr("interrupt._default_lock_path", lambda: lock_path)
        # Write a lock with PID 99999999 (almost certainly doesn't exist)
        lock_path.write_text(json.dumps({"loop_id": "old", "pid": 99999999, "goal": "x"}))
        from interrupt import get_running_loop
        result = get_running_loop()
        assert result is None
        assert not lock_path.exists()


# ---------------------------------------------------------------------------
# Integration: run_agent_loop with interrupt
# ---------------------------------------------------------------------------

class TestAgentLoopInterrupt:
    def test_stop_interrupt_halts_loop(self, tmp_path, monkeypatch):
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

        # Patch orch so no real filesystem needed
        mock_orch = MagicMock()
        mock_orch.project_dir.return_value = tmp_path / "proj"
        mock_orch.orch_root.return_value = tmp_path
        mock_orch.STATE_DONE = "done"
        mock_orch.STATE_BLOCKED = "blocked"
        mock_orch.append_next_items.return_value = [1, 2, 3]
        mock_orch.append_decision.return_value = None
        mock_orch.mark_item.return_value = None
        mock_orch.write_operator_status.return_value = None

        monkeypatch.setattr("agent_loop._orch", lambda: mock_orch)

        from interrupt import InterruptQueue, INTENT_STOP

        q = InterruptQueue(queue_path=tmp_path / "interrupts.jsonl")
        # Pre-load a stop interrupt so it fires after step 1
        q.post("stop", source="test", intent="stop")

        from agent_loop import run_agent_loop
        result = run_agent_loop(
            "do three things",
            dry_run=True,
            project="test-interrupt",
            interrupt_queue=q,
        )
        assert result.status == "interrupted"
        assert result.interrupts_applied == 1

    def test_additive_interrupt_extends_steps(self, tmp_path, monkeypatch):
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

        mock_orch = MagicMock()
        mock_orch.project_dir.return_value = tmp_path / "proj"
        mock_orch.orch_root.return_value = tmp_path
        mock_orch.STATE_DONE = "done"
        mock_orch.STATE_BLOCKED = "blocked"
        mock_orch.append_next_items.return_value = [1, 2, 3, 4]
        mock_orch.append_decision.return_value = None
        mock_orch.mark_item.return_value = None
        mock_orch.write_operator_status.return_value = None

        monkeypatch.setattr("agent_loop._orch", lambda: mock_orch)

        from interrupt import InterruptQueue

        q = InterruptQueue(queue_path=tmp_path / "interrupts.jsonl")
        # Post additive interrupt — should complete without stopping
        q.post("also verify the results", source="test", intent="additive")

        from agent_loop import run_agent_loop
        result = run_agent_loop(
            "research topic",
            dry_run=True,
            project="test-additive",
            interrupt_queue=q,
        )
        # Loop finishes (dry-run always completes steps)
        assert result.status == "done"
        assert result.interrupts_applied == 1


# ---------------------------------------------------------------------------
# Project isolation: per-project lockfile
# ---------------------------------------------------------------------------

class TestProjectLock:
    def test_set_project_lock(self, tmp_path, monkeypatch):
        lock_path = tmp_path / "loop.lock"
        monkeypatch.setattr("interrupt._default_lock_path", lambda: lock_path)
        from interrupt import set_loop_running, get_running_project_loop, is_project_running, clear_loop_running

        set_loop_running("abc123", "test goal", project="polymarket")
        # Global lock exists
        assert lock_path.exists()
        # Per-project lock exists
        proj_lock = tmp_path / "loop-polymarket.lock"
        assert proj_lock.exists()
        # is_project_running returns True
        assert is_project_running("polymarket")
        # Different project is not running
        assert not is_project_running("nootropics")

    def test_clear_loop_removes_project_lock(self, tmp_path, monkeypatch):
        lock_path = tmp_path / "loop.lock"
        monkeypatch.setattr("interrupt._default_lock_path", lambda: lock_path)
        from interrupt import set_loop_running, clear_loop_running, is_project_running

        set_loop_running("abc123", "goal", project="research")
        assert is_project_running("research")
        clear_loop_running()
        assert not is_project_running("research")
        assert not lock_path.exists()

    def test_no_project_no_project_lock(self, tmp_path, monkeypatch):
        lock_path = tmp_path / "loop.lock"
        monkeypatch.setattr("interrupt._default_lock_path", lambda: lock_path)
        from interrupt import set_loop_running, is_project_running, clear_loop_running

        set_loop_running("abc123", "no-project goal")  # no project kwarg
        # No per-project lock files should be created
        proj_locks = list(tmp_path.glob("loop-*.lock"))
        assert proj_locks == []
        assert not is_project_running("anything")
        clear_loop_running()

    def test_stale_project_lock_cleared(self, tmp_path, monkeypatch):
        lock_path = tmp_path / "loop.lock"
        monkeypatch.setattr("interrupt._default_lock_path", lambda: lock_path)
        from interrupt import get_running_project_loop

        # Write a per-project lock with dead PID
        proj_lock = tmp_path / "loop-recipes.lock"
        proj_lock.write_text(json.dumps({"loop_id": "old", "pid": 99999999, "project": "recipes"}))
        result = get_running_project_loop("recipes")
        assert result is None
        assert not proj_lock.exists()

    def test_project_lock_payload_includes_project(self, tmp_path, monkeypatch):
        lock_path = tmp_path / "loop.lock"
        monkeypatch.setattr("interrupt._default_lock_path", lambda: lock_path)
        from interrupt import set_loop_running, get_running_loop

        set_loop_running("loop1", "my goal", project="polymarket-edges")
        info = get_running_loop()
        assert info is not None
        assert info["project"] == "polymarket-edges"
