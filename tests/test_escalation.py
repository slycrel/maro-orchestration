"""Tests for escalation consumer: director.handle_escalation + handle.handle_task/drain_task_store."""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ---------------------------------------------------------------------------
# director.handle_escalation
# ---------------------------------------------------------------------------

from director import handle_escalation, EscalationDecision


def _make_escalation_task(depth=2, reason="ESCALATION — task has been through 2 passes.\n\nOriginal goal: review the codebase\n\nAccomplished: step 1, step 2\n\nRemaining:\n- step 3\n- step 4"):
    return {
        "job_id": "esc-test-001",
        "source": "loop_escalation",
        "lane": "agenda",
        "reason": reason,
        "continuation_depth": depth,
        "parent_job_id": "parent-loop-001",
        "status": "claimed",
    }


class TestHandleEscalationDryRun:
    def test_dry_run_closes_without_llm(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        task = _make_escalation_task()
        result = handle_escalation(task, dry_run=True)
        assert result.action == "close"
        assert result.followup_task_id is None

    def test_returns_escalation_decision_dataclass(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        task = _make_escalation_task()
        result = handle_escalation(task, dry_run=True)
        assert isinstance(result, EscalationDecision)
        assert result.action in ("continue", "narrow", "close", "surface")


class TestHandleEscalationWithLLM:
    def _make_adapter(self, action: str, revised_goal: str = ""):
        class _Adapter:
            def complete(self, messages, **kw):
                body = {
                    "action": action,
                    "reasoning": f"test reasoning for {action}",
                    "summary_for_user": "test summary",
                }
                if revised_goal:
                    body["revised_goal"] = revised_goal
                import json
                return SimpleNamespace(
                    content=json.dumps(body),
                    input_tokens=10,
                    output_tokens=20,
                )
        return _Adapter()

    def test_continue_action_enqueues_continuation(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        enqueued = {}

        def _fake_enqueue(lane, source, reason, parent_job_id, continuation_depth=0):
            enqueued.update({"lane": lane, "source": source, "depth": continuation_depth})
            return {"job_id": "cont-new-001"}

        with mock.patch("task_store.enqueue", _fake_enqueue):
            task = _make_escalation_task(depth=2)
            result = handle_escalation(task, adapter=self._make_adapter("continue"))

        assert result.action == "continue"
        assert result.followup_task_id == "cont-new-001"
        assert enqueued["source"] == "loop_continuation"
        assert enqueued["depth"] == 3  # depth+1

    def test_narrow_action_enqueues_with_revised_goal(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        enqueued = {}

        def _fake_enqueue(lane, source, reason, parent_job_id, continuation_depth=0):
            enqueued.update({"reason": reason, "source": source, "depth": continuation_depth})
            return {"job_id": "narrow-001"}

        with mock.patch("task_store.enqueue", _fake_enqueue):
            task = _make_escalation_task(depth=2)
            result = handle_escalation(
                task,
                adapter=self._make_adapter("narrow", revised_goal="review only auth.py for injection risks"),
            )

        assert result.action == "narrow"
        assert result.followup_task_id == "narrow-001"
        assert "auth.py" in enqueued["reason"]
        assert enqueued["depth"] == 3

    def test_close_action_no_followup_task(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        task = _make_escalation_task(depth=2)
        result = handle_escalation(task, adapter=self._make_adapter("close"))
        assert result.action == "close"
        assert result.followup_task_id is None

    def test_surface_action_no_followup_task(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        task = _make_escalation_task(depth=2)
        result = handle_escalation(task, adapter=self._make_adapter("surface"))
        assert result.action == "surface"
        assert result.followup_task_id is None

    def test_invalid_action_defaults_to_surface(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))

        class _BadAdapter:
            def complete(self, messages, **kw):
                import json
                return SimpleNamespace(
                    content=json.dumps({"action": "explode", "reasoning": "bad", "summary_for_user": "x"}),
                    input_tokens=5, output_tokens=10,
                )

        task = _make_escalation_task(depth=2)
        result = handle_escalation(task, adapter=_BadAdapter())
        assert result.action == "surface"


# ---------------------------------------------------------------------------
# handle.handle_task routing
# ---------------------------------------------------------------------------

from handle import handle_task, drain_task_store


class TestHandleTask:
    def test_escalation_routes_to_director(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        called = {}

        def _fake_handle_escalation(task, **kw):
            called["task_id"] = task.get("job_id")
            return EscalationDecision(action="close", reasoning="test", summary_for_user="ok")

        with mock.patch("director.handle_escalation", _fake_handle_escalation):
            task = _make_escalation_task()
            result = handle_task(task, dry_run=True)

        assert called["task_id"] == "esc-test-001"
        assert isinstance(result, EscalationDecision)

    def test_continuation_routes_to_run_agent_loop(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        called = {}

        class _FakeLoopResult:
            status = "done"

        def _fake_run_agent_loop(goal, **kw):
            called["goal"] = goal
            called["depth"] = kw.get("continuation_depth", -1)
            return _FakeLoopResult()

        with mock.patch("agent_loop.run_agent_loop", _fake_run_agent_loop):
            task = {
                "job_id": "cont-t-001",
                "source": "loop_continuation",
                "reason": "CONTINUATION of: review auth module",
                "continuation_depth": 2,
                "status": "claimed",
            }
            handle_task(task, dry_run=True)

        assert called["depth"] == 2

    def test_unknown_source_routes_to_handle(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        called = {}

        class _FakeHandleResult:
            status = "done"

        def _fake_handle(message, **kw):
            called["message"] = message
            return _FakeHandleResult()

        with mock.patch("handle.handle", _fake_handle):
            task = {
                "job_id": "other-001",
                "source": "some_other_source",
                "reason": "do a thing",
                "continuation_depth": 0,
                "status": "claimed",
            }
            handle_task(task, dry_run=True)

        assert called["message"] == "do a thing"


class TestDrainTaskStore:
    def test_drain_empty_queue_returns_zero(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        with mock.patch("task_store.list_tasks", return_value=[]):
            count = drain_task_store(dry_run=True)
        assert count == 0

    def test_drain_processes_escalation_tasks(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        task = _make_escalation_task()
        task["status"] = "queued"
        processed = []

        with mock.patch("task_store.list_tasks", return_value=[task]), \
             mock.patch("task_store.claim", return_value=task), \
             mock.patch("task_store.complete", return_value=task), \
             mock.patch("handle.handle_task", lambda t, **kw: processed.append(t["job_id"])):
            count = drain_task_store(dry_run=False)

        assert count == 1
        assert "esc-test-001" in processed

    def test_drain_respects_max_tasks(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        tasks = [
            {**_make_escalation_task(), "job_id": f"esc-{i}", "status": "queued"}
            for i in range(5)
        ]
        processed = []

        with mock.patch("task_store.list_tasks", return_value=tasks), \
             mock.patch("task_store.claim", side_effect=lambda jid: None), \
             mock.patch("task_store.complete", side_effect=lambda jid: None), \
             mock.patch("handle.handle_task", lambda t, **kw: processed.append(t["job_id"])):
            count = drain_task_store(dry_run=False, max_tasks=2)

        assert count == 2
        assert len(processed) == 2

    def test_drain_skips_wrong_sources(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        tasks = [
            {"job_id": "other-001", "source": "manual", "reason": "do a thing",
             "continuation_depth": 0, "status": "queued"},
        ]
        with mock.patch("task_store.list_tasks", return_value=tasks):
            count = drain_task_store(dry_run=False)
        assert count == 0
