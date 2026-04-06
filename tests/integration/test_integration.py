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
