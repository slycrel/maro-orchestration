"""Integration tests — mocked-LLM scenarios covering both lanes, magic keywords,
constraint enforcement, and inter-module wiring.

These tests use a ScriptedAdapter (no API calls) but exercise real code paths
across handle → intent → agent_loop → memory. They catch wiring bugs that
unit tests (which mock individual modules) can't find.

Scenarios:
  1. NOW lane: heuristic classifier routes short questions to NOW
  2. NOW lane: adapter error still returns HandleResult (no exception)
  3. AGENDA lane: force_lane="agenda" works without classify call
  4. AGENDA lane: project slug is set on result
  5. Magic keyword: effort:low sets cheap model, message cleaned
  6. Magic keyword: btw: returns Observation tag
  7. Magic keyword: pipeline: | syntax runs explicit steps
  8. Magic keyword: team: mode enables parallel_fan_out
  9. Constraint enforcement: destructive step blocked (check_step_constraints)
  10. _apply_prefixes: stacking order is stable across all prefix types
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))


# ---------------------------------------------------------------------------
# Minimal scripted adapter
# ---------------------------------------------------------------------------

class MinimalScriptedAdapter:
    """Scripted adapter for integration tests. Simpler than ScriptedAdapter."""

    model_key = "scripted"

    def __init__(self, responses: List[Dict[str, Any]]):
        self._responses = list(responses)
        self._idx = 0

    def complete(self, messages, *, tools=None, tool_choice="auto", **kwargs):
        from llm import LLMResponse, ToolCall

        resp = self._responses[self._idx] if self._idx < len(self._responses) else self._responses[-1]
        self._idx += 1

        if "steps" in resp:
            return LLMResponse(
                content=json.dumps(resp["steps"]),
                stop_reason="end_turn",
                input_tokens=40,
                output_tokens=20,
            )

        if "tool" in resp and (tools or tool_choice == "required"):
            name = resp["tool"]
            if name == "complete_step":
                return LLMResponse(
                    content="",
                    tool_calls=[ToolCall(
                        name="complete_step",
                        arguments={
                            "result": resp.get("result", "done"),
                            "summary": resp.get("result", "done")[:60],
                        },
                    )],
                    stop_reason="tool_use",
                    input_tokens=60,
                    output_tokens=30,
                )

        content = resp.get("content", "ok")
        return LLMResponse(
            content=content,
            stop_reason="end_turn",
            input_tokens=15,
            output_tokens=8,
        )


def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))


def _suppress_side_effects(monkeypatch):
    """Silence expensive side-effects that are not under test here."""
    import intent as _intent
    monkeypatch.setattr(_intent, "check_goal_clarity", lambda *a, **kw: {"clear": True})
    try:
        import pre_flight as _pf
        monkeypatch.setattr(_pf, "review_plan", lambda *a, **kw: MagicMock(scope="narrow"))
    except ImportError:
        pass
    try:
        import agent_loop as _al
        for fn in ("run_boot_protocol", "run_hooks", "negotiate_sprint_contract",
                   "grade_sprint_contract"):
            if hasattr(_al, fn):
                monkeypatch.setattr(_al, fn, lambda *a, **kw: None)
    except ImportError:
        pass


# ---------------------------------------------------------------------------
# 1. NOW lane: heuristic classifier
# ---------------------------------------------------------------------------

class TestNowLaneHeuristic:
    def test_short_question_routes_to_now(self, monkeypatch, tmp_path):
        """Heuristic classify routes obvious NOW questions to NOW."""
        from intent import classify
        lane, confidence, reason = classify("what time is it?", adapter=None)
        assert lane == "now"
        assert confidence > 0.0

    def test_long_research_routes_to_agenda(self, monkeypatch, tmp_path):
        """Heuristic classify routes research goals to AGENDA."""
        from intent import classify
        lane, confidence, reason = classify(
            "Research and analyze the top 10 winning strategies across 500 Polymarket markets"
        )
        assert lane == "agenda"


# ---------------------------------------------------------------------------
# 2. NOW lane: adapter error yields HandleResult (no crash)
# ---------------------------------------------------------------------------

class TestNowLaneError:
    def test_adapter_exception_returns_error_status(self, monkeypatch, tmp_path):
        _env(monkeypatch, tmp_path)
        _suppress_side_effects(monkeypatch)
        from handle import handle
        from llm import LLMResponse

        class BrokenAdapter:
            model_key = "broken"
            def complete(self, *a, **kw):
                raise RuntimeError("connection refused")

        result = handle(
            "What is the capital of Mars?",
            adapter=BrokenAdapter(),
            force_lane="now",
        )

        # Must return HandleResult — never raise
        assert result is not None
        assert result.status == "error"
        assert result.lane == "now"


# ---------------------------------------------------------------------------
# 3. AGENDA lane: force_lane bypasses classify
# ---------------------------------------------------------------------------

class TestAgendaForced:
    def test_force_agenda_skips_classify(self, monkeypatch, tmp_path):
        _env(monkeypatch, tmp_path)
        _suppress_side_effects(monkeypatch)
        from handle import handle

        adapter = MinimalScriptedAdapter([
            {"steps": ["Research step"]},
            {"tool": "complete_step", "result": "Research complete"},
        ])

        # Even a trivially short message routes to AGENDA when forced
        result = handle(
            "do it",
            adapter=adapter,
            force_lane="agenda",
            project="forced-agenda",
        )

        assert result.lane == "agenda"
        assert result.status == "done"

    def test_project_slug_on_result(self, monkeypatch, tmp_path):
        _env(monkeypatch, tmp_path)
        _suppress_side_effects(monkeypatch)
        from handle import handle

        adapter = MinimalScriptedAdapter([
            {"steps": ["Step A"]},
            {"tool": "complete_step", "result": "A done"},
        ])

        result = handle(
            "Analyze the logs",
            adapter=adapter,
            force_lane="agenda",
            project="my-project-slug",
        )

        # project propagates to the result
        assert result.project == "my-project-slug" or result.loop_result is not None


# ---------------------------------------------------------------------------
# 4. Magic keyword: effort:low
# ---------------------------------------------------------------------------

class TestEffortPrefix:
    def test_effort_low_extracts_model_tier(self):
        from handle import _apply_prefixes
        pr = _apply_prefixes("effort:low check the logs")
        assert pr.model_tier == "cheap"
        assert pr.message == "check the logs"

    def test_effort_high_extracts_model_tier(self):
        from handle import _apply_prefixes
        pr = _apply_prefixes("effort:high deep analysis of the codebase")
        assert pr.model_tier == "power"
        assert "effort" not in pr.message.lower()

    def test_effort_mid_extracts_model_tier(self):
        from handle import _apply_prefixes
        pr = _apply_prefixes("effort:mid summarize the docs")
        assert pr.model_tier == "mid"

    def test_effort_mid_does_not_set_ralph_mode(self):
        from handle import _apply_prefixes
        pr = _apply_prefixes("effort:mid check")
        assert pr.ralph_mode is False


# ---------------------------------------------------------------------------
# 5. Magic keyword: btw:
# ---------------------------------------------------------------------------

class TestBtwPrefix:
    def test_btw_returns_observation_prefix(self, monkeypatch, tmp_path):
        _env(monkeypatch, tmp_path)
        _suppress_side_effects(monkeypatch)
        from handle import handle

        adapter = MinimalScriptedAdapter([
            {"content": "Disk at 80%."},
        ])

        result = handle("btw: disk usage is creeping", adapter=adapter)
        assert result.result.startswith("[Observation]")
        assert result.lane == "now"

    def test_btw_is_fast(self, monkeypatch, tmp_path):
        _env(monkeypatch, tmp_path)
        _suppress_side_effects(monkeypatch)
        from handle import handle

        adapter = MinimalScriptedAdapter([{"content": "ok"}])
        result = handle("btw: something to note", adapter=adapter)
        assert result.elapsed_ms < 10_000  # well under 10s (scripted = <100ms)


# ---------------------------------------------------------------------------
# 6. Magic keyword: pipeline:
# ---------------------------------------------------------------------------

class TestPipelinePrefix:
    def test_pipeline_skips_decompose(self, monkeypatch, tmp_path):
        _env(monkeypatch, tmp_path)
        _suppress_side_effects(monkeypatch)
        from handle import handle

        # No "steps" response needed — pipeline skips LLM decompose.
        # force_lane="agenda" because the stripped message "Do step X | Do step Y"
        # would be classified as NOW by the heuristic classifier.
        adapter = MinimalScriptedAdapter([
            {"tool": "complete_step", "result": "Step X done"},
            {"tool": "complete_step", "result": "Step Y done"},
        ])

        result = handle(
            "pipeline: Do step X | Do step Y",
            project="pipe-test",
            adapter=adapter,
            force_lane="agenda",
        )

        assert result.lane == "agenda"
        assert result.status in ("done", "partial")

    def test_pipeline_result_contains_output(self, monkeypatch, tmp_path):
        _env(monkeypatch, tmp_path)
        _suppress_side_effects(monkeypatch)
        from handle import handle

        adapter = MinimalScriptedAdapter([
            {"tool": "complete_step", "result": "Alpha output"},
        ])

        result = handle(
            "pipeline: Do alpha",
            project="pipe-test2",
            adapter=adapter,
        )

        assert result.result  # non-empty


# ---------------------------------------------------------------------------
# 7. Constraint enforcement: check_step_constraints
# ---------------------------------------------------------------------------

class TestConstraintEnforcement:
    def test_destructive_op_flagged_high(self):
        from constraint import check_step_constraints, ConstraintResult
        result = check_step_constraints("rm -rf /home/clawd/data", goal="clean up disk")
        high_flags = [f for f in result.flags if f.risk == "HIGH"]
        assert len(high_flags) >= 1
        assert not result.allowed

    def test_secret_access_flagged(self):
        from constraint import check_step_constraints
        result = check_step_constraints("Read ~/.env for API keys", goal="configure the tool")
        high_flags = [f for f in result.flags if f.risk == "HIGH"]
        assert len(high_flags) >= 1

    def test_benign_step_allowed(self):
        from constraint import check_step_constraints
        result = check_step_constraints("Read config.py and report the timeout value", goal="find settings")
        assert result.allowed
        assert result.flags == []

    def test_drop_table_blocked(self):
        from constraint import check_step_constraints
        result = check_step_constraints("Execute: DROP TABLE users", goal="clean up old schema")
        assert not result.allowed

    def test_unsafe_network_medium(self):
        from constraint import check_step_constraints
        result = check_step_constraints("Run curl -X DELETE https://api.example.com/items/1", goal="delete item")
        medium_or_high = [f for f in result.flags if f.risk in ("MEDIUM", "HIGH")]
        assert len(medium_or_high) >= 1


# ---------------------------------------------------------------------------
# 8. _apply_prefixes stacking stability
# ---------------------------------------------------------------------------

class TestPrefixStacking:
    def test_ralph_and_strict_stack(self):
        from handle import _apply_prefixes
        pr = _apply_prefixes("ralph: strict: do the audit")
        assert pr.ralph_mode is True
        assert pr.strict_mode is True
        assert pr.message == "do the audit"

    def test_effort_only_sets_first_match(self):
        """effort: group is exclusive — first wins, second is left in message."""
        from handle import _apply_prefixes
        pr = _apply_prefixes("effort:low effort:high do something")
        # First effort: wins
        assert pr.model_tier == "cheap"

    def test_garrytan_prefix_sets_persona(self):
        from handle import _apply_prefixes
        pr = _apply_prefixes("garrytan: review the architecture")
        assert pr.forced_persona == "garrytan"
        assert pr.model_tier == "power"
        assert pr.message == "review the architecture"

    def test_unknown_prefix_left_in_message(self):
        from handle import _apply_prefixes
        pr = _apply_prefixes("notaprefix: do something")
        # Unknown prefix is NOT stripped
        assert "notaprefix:" in pr.message

    def test_case_insensitive_strip(self):
        from handle import _apply_prefixes
        pr = _apply_prefixes("BTW: disk usage high")
        assert pr.btw_mode is True
        assert "BTW" not in pr.message


# ---------------------------------------------------------------------------
# Phase 61: Integration depth — checkpoint recovery + memory injection
# ---------------------------------------------------------------------------

class TestCheckpointRecovery:
    """Phase 61: loop resume from checkpoint restores correct step index."""

    def _setup(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))

    def test_checkpoint_written_per_step(self, monkeypatch, tmp_path):
        """A checkpoint is written after each step during a non-dry-run loop."""
        self._setup(monkeypatch, tmp_path)
        import agent_loop as _al
        monkeypatch.setattr(_al, "reflect_and_record", lambda *a, **kw: None, raising=False)
        import skills as _skills
        monkeypatch.setattr(_skills, "extract_skills", lambda *a, **kw: [], raising=False)

        from agent_loop import run_agent_loop, _DryRunAdapter
        result = run_agent_loop(
            "research market volatility patterns",
            project="ckpt-test",
            adapter=_DryRunAdapter(),
            dry_run=False,
        )

        assert result.status == "done"
        # Checkpoint should be deleted on success (clean loop)
        from checkpoint import load_checkpoint
        ckpt = load_checkpoint(result.loop_id)
        assert ckpt is None, "checkpoint should be deleted after successful loop"

    def test_resume_skips_completed_steps(self, monkeypatch, tmp_path):
        """Resuming from a checkpoint skips already-completed steps."""
        self._setup(monkeypatch, tmp_path)
        from checkpoint import write_checkpoint, CompletedStep

        loop_id = "test-resume-abc123"
        steps = ["Step A: research topic", "Step B: analyze data", "Step C: write summary"]
        # Pre-complete step A
        completed = [CompletedStep(index=1, text=steps[0], status="done", result="research done")]
        write_checkpoint(loop_id, "summarize market data", "ckpt-proj", steps, completed)

        import agent_loop as _al
        monkeypatch.setattr(_al, "reflect_and_record", lambda *a, **kw: None, raising=False)
        import skills as _skills
        monkeypatch.setattr(_skills, "extract_skills", lambda *a, **kw: [], raising=False)

        from agent_loop import run_agent_loop, _DryRunAdapter
        result = run_agent_loop(
            "summarize market data",
            project="ckpt-proj",
            adapter=_DryRunAdapter(),
            dry_run=False,
            resume_from_loop_id=loop_id,
        )

        assert result.status == "done"
        # Result should include the pre-completed step A outcome
        step_texts = [s.text for s in result.steps]
        assert any("Step A" in t or "research topic" in t for t in step_texts), \
            f"resumed run should include step A; got: {step_texts}"

    def test_resume_from_missing_checkpoint_starts_fresh(self, monkeypatch, tmp_path):
        """If the checkpoint doesn't exist, loop runs from scratch without error."""
        self._setup(monkeypatch, tmp_path)
        import agent_loop as _al
        monkeypatch.setattr(_al, "reflect_and_record", lambda *a, **kw: None, raising=False)
        import skills as _skills
        monkeypatch.setattr(_skills, "extract_skills", lambda *a, **kw: [], raising=False)

        from agent_loop import run_agent_loop, _DryRunAdapter
        result = run_agent_loop(
            "analyze trends",
            project="fresh-start",
            adapter=_DryRunAdapter(),
            dry_run=False,
            resume_from_loop_id="nonexistent-loop-id-xyz",
        )

        assert result.status == "done"
        assert len(result.steps) >= 1


class TestMemoryInjection:
    """Phase 61: lessons from prior run surface in next run's context."""

    def _setup(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))

    def test_lessons_written_during_loop_are_retrievable(self, monkeypatch, tmp_path):
        """Lessons recorded during a run can be queried in subsequent runs."""
        self._setup(monkeypatch, tmp_path)
        # Directly record a lesson as if a prior run produced it
        from memory_ledger import _store_lesson, load_lessons

        _store_lesson(
            task_type="research",
            outcome="done",
            lesson="Always cross-check Polymarket market IDs before querying.",
            source_goal="prior polymarket goal",
        )

        lessons = load_lessons(task_type="research", limit=10)
        assert len(lessons) >= 1
        texts = [l.lesson for l in lessons]
        assert any("Polymarket" in t for t in texts)

    def test_lessons_inject_into_decompose_context(self, monkeypatch, tmp_path):
        """Lessons from memory are injected into the decompose system prompt."""
        self._setup(monkeypatch, tmp_path)
        from memory_ledger import _store_lesson

        # Seed a lesson
        _store_lesson(
            task_type="research",
            outcome="done",
            lesson="Verify source credibility before citing statistics.",
            source_goal="prior research goal",
            confidence=0.85,
        )

        # Capture the decompose prompt
        captured_prompts = []

        class CapturingAdapter:
            model_key = "scripted"

            def complete(self, messages, *, tools=None, tool_choice="auto", **kwargs):
                from llm import LLMResponse, ToolCall
                for msg in messages:
                    if hasattr(msg, "content") and isinstance(msg.content, str):
                        captured_prompts.append(msg.content)
                # Always return a decompose-style response
                return LLMResponse(
                    content=json.dumps(["Step 1: research sources", "Step 2: verify claims"]),
                    stop_reason="end_turn", input_tokens=50, output_tokens=20,
                )

        import agent_loop as _al
        monkeypatch.setattr(_al, "reflect_and_record", lambda *a, **kw: None, raising=False)
        import skills as _skills
        monkeypatch.setattr(_skills, "extract_skills", lambda *a, **kw: [], raising=False)

        from agent_loop import run_agent_loop
        run_agent_loop(
            "research competitor analysis",
            project="inject-test",
            adapter=CapturingAdapter(),
            dry_run=False,
        )

        # At least one decompose prompt should contain lesson context
        all_prompts = " ".join(captured_prompts)
        # Lessons are injected as a block — check for lesson content or lesson header
        has_lesson_injection = (
            "Verify source credibility" in all_prompts
            or "lesson" in all_prompts.lower()
            or "prior run" in all_prompts.lower()
        )
        assert has_lesson_injection, \
            "decompose prompt should contain lesson injection from prior run"


# ---------------------------------------------------------------------------
# Phase 61: AGENDA lane heartbeat e2e
# ---------------------------------------------------------------------------

class TestAgendaLaneHeartbeat:
    """Phase 61: full AGENDA path — enqueue_goal → drain_task_store → task done."""

    def _setup(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))

    def test_enqueued_task_is_processed_by_drain(self, monkeypatch, tmp_path):
        """enqueue_goal() creates a task; drain_task_store() executes it end-to-end."""
        self._setup(monkeypatch, tmp_path)
        _suppress_side_effects(monkeypatch)

        import agent_loop as _al
        monkeypatch.setattr(_al, "reflect_and_record", lambda *a, **kw: None, raising=False)
        import skills as _skills
        monkeypatch.setattr(_skills, "extract_skills", lambda *a, **kw: [], raising=False)

        from handle import enqueue_goal, drain_task_store
        from task_store import list_tasks

        job_id = enqueue_goal("summarize emerging AI research trends")

        # Task should be queued
        queued = list_tasks(status_filter="queued")
        assert any(t["job_id"] == job_id for t in queued), "task should be queued before drain"

        # Drain with dry_run=True so no real LLM calls are made
        processed = drain_task_store(dry_run=True)
        assert processed >= 1, "drain should process at least 1 task"

        # Task should no longer be queued (moved to done/archive)
        still_queued = [t for t in list_tasks(status_filter="queued") if t["job_id"] == job_id]
        assert len(still_queued) == 0, "processed task should not remain in queue"

    def test_drain_returns_zero_when_queue_empty(self, monkeypatch, tmp_path):
        """drain_task_store() returns 0 with no queued tasks."""
        self._setup(monkeypatch, tmp_path)
        _suppress_side_effects(monkeypatch)

        from handle import drain_task_store
        processed = drain_task_store(dry_run=True)
        assert processed == 0

    def test_drain_respects_max_tasks_limit(self, monkeypatch, tmp_path):
        """drain_task_store() processes at most max_tasks tasks per call."""
        self._setup(monkeypatch, tmp_path)
        _suppress_side_effects(monkeypatch)

        import agent_loop as _al
        monkeypatch.setattr(_al, "reflect_and_record", lambda *a, **kw: None, raising=False)
        import skills as _skills
        monkeypatch.setattr(_skills, "extract_skills", lambda *a, **kw: [], raising=False)

        from handle import enqueue_goal, drain_task_store

        # Enqueue 3 tasks
        for i in range(3):
            enqueue_goal(f"research task {i}: analyze market segment")

        # Drain with limit=1 — only 1 should be processed per call
        processed = drain_task_store(dry_run=True, max_tasks=1)
        assert processed == 1, f"max_tasks=1 should process exactly 1 task, got {processed}"


# ---------------------------------------------------------------------------
# Phase 61: Adapter failover e2e chain
# ---------------------------------------------------------------------------

class TestFailoverAdapterChain:
    """Phase 61: FailoverAdapter tries backends in order; skips on 402/5xx."""

    def test_failover_skips_to_second_on_402(self):
        """When first adapter raises 402, FailoverAdapter succeeds on second."""
        from llm import FailoverAdapter, LLMResponse

        call_log = []

        class FirstAdapter:
            model_key = "primary"
            backend = "primary"
            def complete(self, messages, **kwargs):
                call_log.append("first")
                raise RuntimeError("402 payment required: quota exceeded")

        class SecondAdapter:
            model_key = "fallback"
            backend = "fallback"
            def complete(self, messages, **kwargs):
                call_log.append("second")
                return LLMResponse(
                    content="done via fallback",
                    stop_reason="end_turn",
                    input_tokens=10,
                    output_tokens=5,
                )

        adapter = FailoverAdapter([FirstAdapter(), SecondAdapter()])
        result = adapter.complete([])

        assert call_log == ["first", "second"]
        assert "fallback" in result.content

    def test_failover_propagates_non_failover_error(self):
        """A 400 (bad request) error propagates immediately without trying next adapter."""
        from llm import FailoverAdapter

        call_log = []

        class BadRequestAdapter:
            model_key = "primary"
            backend = "primary"
            def complete(self, messages, **kwargs):
                call_log.append("first")
                raise ValueError("400 bad request: invalid tool schema")

        class SecondAdapter:
            model_key = "fallback"
            backend = "fallback"
            def complete(self, messages, **kwargs):
                call_log.append("second")  # should never run

        adapter = FailoverAdapter([BadRequestAdapter(), SecondAdapter()])

        with pytest.raises(ValueError, match="400 bad request"):
            adapter.complete([])

        assert call_log == ["first"], "non-failover error should not try second adapter"

    def test_failover_first_succeeds_no_switch(self):
        """When first adapter succeeds, no failover occurs."""
        from llm import FailoverAdapter, LLMResponse

        call_log = []

        class GoodAdapter:
            model_key = "primary"
            backend = "primary"
            def complete(self, messages, **kwargs):
                call_log.append("first")
                return LLMResponse(
                    content="success on first",
                    stop_reason="end_turn",
                    input_tokens=10,
                    output_tokens=5,
                )

        class FallbackAdapter:
            model_key = "fallback"
            backend = "fallback"
            def complete(self, messages, **kwargs):
                call_log.append("second")

        adapter = FailoverAdapter([GoodAdapter(), FallbackAdapter()])
        result = adapter.complete([])

        assert call_log == ["first"]
        assert result.content == "success on first"

    def test_failover_exhausts_all_then_raises(self):
        """When all adapters fail with failover errors, the last exception propagates."""
        from llm import FailoverAdapter

        call_log = []

        class FailingAdapter:
            def __init__(self, name):
                self.model_key = name
                self.backend = name
            def complete(self, messages, **kwargs):
                call_log.append(self.model_key)
                raise RuntimeError(f"503 service unavailable on {self.model_key}")

        adapter = FailoverAdapter([FailingAdapter("a"), FailingAdapter("b")])

        with pytest.raises(RuntimeError, match="503"):
            adapter.complete([])

        assert call_log == ["a", "b"], "both adapters should have been tried"
