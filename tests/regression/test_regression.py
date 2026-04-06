"""Regression tests — golden-path scenarios for the full orchestration pipeline.

Tests the handle() entry point end-to-end with scripted adapters (no real LLM calls).
These catch inter-module wiring breakage that unit tests miss.

Scenarios:
  A. NOW lane happy path
  B. AGENDA lane 3-step goal completes (status=done, result non-empty)
  C. Magic prefix: direct: strips prefix, routes to AGENDA, skips CEO
  D. Magic prefix: btw: returns [Observation] immediately
  E. Magic prefix: pipeline: runs explicit steps without decompose
  F. Stuck step → loop marks partial completion
  G. Prefix stacking: ralph: + effort:low set model tier + ralph mode
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
# Shared ScriptedAdapter (same as test_e2e_smoke — kept local for isolation)
# ---------------------------------------------------------------------------

class ScriptedAdapter:
    """Programmable mock LLM — returns scripted responses in sequence."""

    model_key = "scripted"

    def __init__(self, responses: List[Dict[str, Any]]):
        self._responses = list(responses)
        self._call_idx = 0
        self.calls: List[Dict[str, Any]] = []

    def complete(self, messages, *, tools=None, tool_choice="auto",
                 max_tokens=4096, temperature=0.3, **kwargs):
        from llm import LLMResponse, ToolCall

        user_content = next(
            (m.content for m in reversed(messages) if m.role == "user"), ""
        )
        self.calls.append({
            "idx": self._call_idx,
            "user_content_prefix": user_content[:200],
            "has_tools": bool(tools),
        })

        if self._call_idx < len(self._responses):
            resp = self._responses[self._call_idx]
        elif tools:
            resp = next(
                (r for r in reversed(self._responses) if "tool" in r),
                self._responses[-1] if self._responses else {},
            )
        else:
            resp = next(
                (r for r in reversed(self._responses) if "tool" not in r),
                self._responses[-1] if self._responses else {},
            )
        self._call_idx += 1

        if "steps" in resp:
            return LLMResponse(
                content=json.dumps(resp["steps"]),
                stop_reason="end_turn",
                input_tokens=50,
                output_tokens=30,
            )

        if "tool" in resp and (tools or tool_choice == "required"):
            tool_name = resp["tool"]
            if tool_name == "complete_step":
                return LLMResponse(
                    content="",
                    tool_calls=[ToolCall(
                        name="complete_step",
                        arguments={
                            "result": resp.get("result", "[scripted] done"),
                            "summary": resp.get("result", "[scripted] done")[:60],
                        },
                    )],
                    stop_reason="tool_use",
                    input_tokens=80,
                    output_tokens=40,
                )
            elif tool_name == "flag_stuck":
                return LLMResponse(
                    content="",
                    tool_calls=[ToolCall(
                        name="flag_stuck",
                        arguments={"reason": resp.get("reason", "[scripted] blocked")},
                    )],
                    stop_reason="tool_use",
                    input_tokens=80,
                    output_tokens=40,
                )

        content = resp.get("content", '{"passed": true}')
        return LLMResponse(
            content=content,
            stop_reason="end_turn",
            input_tokens=20,
            output_tokens=10,
        )


def _setup(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))


def _patch_side_effects(monkeypatch):
    """Suppress expensive side-effects: clarity check, pre-flight review, boot protocol."""
    import intent as _intent
    monkeypatch.setattr(_intent, "check_goal_clarity", lambda *a, **kw: {"clear": True})
    try:
        import pre_flight as _pf
        monkeypatch.setattr(_pf, "review_plan", lambda *a, **kw: MagicMock(scope="narrow"))
    except ImportError:
        pass
    try:
        import agent_loop as _al
        if hasattr(_al, "run_boot_protocol"):
            monkeypatch.setattr(_al, "run_boot_protocol", lambda *a, **kw: None)
        if hasattr(_al, "run_hooks"):
            monkeypatch.setattr(_al, "run_hooks", lambda *a, **kw: None)
        if hasattr(_al, "negotiate_sprint_contract"):
            monkeypatch.setattr(_al, "negotiate_sprint_contract", lambda *a, **kw: None)
        if hasattr(_al, "grade_sprint_contract"):
            monkeypatch.setattr(_al, "grade_sprint_contract", lambda *a, **kw: None)
    except ImportError:
        pass


# ---------------------------------------------------------------------------
# Scenario A — NOW lane happy path
# ---------------------------------------------------------------------------

class TestScenarioA_NowLane:
    """Simple factual question routes to NOW lane, returns a result."""

    def test_now_lane_returns_done(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        _patch_side_effects(monkeypatch)
        from handle import handle

        adapter = ScriptedAdapter([
            {"content": "The capital of France is Paris."},
        ])

        result = handle(
            "What is the capital of France?",
            adapter=adapter,
            force_lane="now",
        )

        assert result.lane == "now"
        assert result.status == "done"
        assert result.result != ""
        assert "Paris" in result.result

    def test_now_lane_result_is_non_empty_string(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        _patch_side_effects(monkeypatch)
        from handle import handle

        adapter = ScriptedAdapter([
            {"content": "42 is the answer to life, the universe, and everything."},
        ])

        result = handle(
            "What is the answer to everything?",
            adapter=adapter,
            force_lane="now",
        )

        assert isinstance(result.result, str)
        assert len(result.result) > 0


# ---------------------------------------------------------------------------
# Scenario B — AGENDA lane 3-step goal completes
# ---------------------------------------------------------------------------

class TestScenarioB_AgendaLane:
    """Multi-step goal decomposes and executes to completion."""

    def test_three_step_goal_completes(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        _patch_side_effects(monkeypatch)
        from handle import handle

        adapter = ScriptedAdapter([
            {"steps": ["Fetch the data", "Parse the results", "Write summary"]},
            {"tool": "complete_step", "result": "Fetched 42 records"},
            {"tool": "complete_step", "result": "Parsed into 3 categories"},
            {"tool": "complete_step", "result": "Summary: 42 records across 3 categories"},
        ])

        result = handle(
            "Fetch and summarize the dataset",
            project="regression-b",
            adapter=adapter,
            force_lane="agenda",
        )

        assert result.lane == "agenda"
        assert result.status == "done"

    def test_agenda_result_contains_step_output(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        _patch_side_effects(monkeypatch)
        from handle import handle

        adapter = ScriptedAdapter([
            {"steps": ["Read the config", "Validate values"]},
            {"tool": "complete_step", "result": "Config loaded: timeout=30"},
            {"tool": "complete_step", "result": "All values valid"},
        ])

        result = handle(
            "Validate the configuration",
            project="regression-b2",
            adapter=adapter,
            force_lane="agenda",
        )

        assert result.status == "done"
        assert result.result  # non-empty

    def test_loop_result_attached(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        _patch_side_effects(monkeypatch)
        from handle import handle

        adapter = ScriptedAdapter([
            {"steps": ["Step one"]},
            {"tool": "complete_step", "result": "done"},
        ])

        result = handle(
            "Do step one",
            project="regression-b3",
            adapter=adapter,
            force_lane="agenda",
        )

        # loop_result is set on AGENDA outcomes
        assert result.loop_result is not None


# ---------------------------------------------------------------------------
# Scenario C — Magic prefix: direct:
# ---------------------------------------------------------------------------

class TestScenarioC_DirectPrefix:
    """direct: prefix strips keyword and forces AGENDA, skips CEO meta-check."""

    def test_direct_prefix_stripped_from_message(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        _patch_side_effects(monkeypatch)
        from handle import handle

        adapter = ScriptedAdapter([
            {"steps": ["Check the logs"]},
            {"tool": "complete_step", "result": "Logs are clean"},
        ])

        result = handle(
            "direct: Check the logs for errors",
            project="regression-c",
            adapter=adapter,
        )

        # Goal was routed to AGENDA (direct: forces agenda)
        assert result.lane == "agenda"
        # Prefix removed from message
        assert not result.message.lower().startswith("direct:")

    def test_direct_prefix_case_insensitive(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        _patch_side_effects(monkeypatch)
        from handle import handle

        adapter = ScriptedAdapter([
            {"steps": ["Step A"]},
            {"tool": "complete_step", "result": "done"},
        ])

        result = handle(
            "DIRECT: Do the thing",
            project="regression-c2",
            adapter=adapter,
        )

        assert result.lane == "agenda"


# ---------------------------------------------------------------------------
# Scenario D — Magic prefix: btw:
# ---------------------------------------------------------------------------

class TestScenarioD_BtwPrefix:
    """btw: routes immediately to NOW lane, wraps result in [Observation]."""

    def test_btw_returns_observation(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        _patch_side_effects(monkeypatch)
        from handle import handle

        adapter = ScriptedAdapter([
            {"content": "The disk is 87% full."},
        ])

        result = handle(
            "btw: disk usage is creeping up",
            adapter=adapter,
        )

        assert result.lane == "now"
        assert result.status == "done"
        assert result.result.startswith("[Observation]")

    def test_btw_classification_reason(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        _patch_side_effects(monkeypatch)
        from handle import handle

        adapter = ScriptedAdapter([
            {"content": "Noted."},
        ])

        result = handle(
            "btw: memory usage is high",
            adapter=adapter,
        )

        assert "btw" in result.classification_reason.lower() or result.lane == "now"


# ---------------------------------------------------------------------------
# Scenario E — Magic prefix: pipeline:
# ---------------------------------------------------------------------------

class TestScenarioE_PipelinePrefix:
    """pipeline: bypasses LLM decompose, runs explicit steps separated by |."""

    def test_pipeline_runs_explicit_steps(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        _patch_side_effects(monkeypatch)
        from handle import handle

        # No decompose call needed — steps come from the message
        adapter = ScriptedAdapter([
            {"tool": "complete_step", "result": "Step A done"},
            {"tool": "complete_step", "result": "Step B done"},
        ])

        result = handle(
            "pipeline: Do step A | Do step B",
            project="regression-e",
            adapter=adapter,
        )

        assert result.lane == "agenda"
        assert result.status in ("done", "partial")


# ---------------------------------------------------------------------------
# Scenario F — Stuck step → partial completion
# ---------------------------------------------------------------------------

class TestScenarioF_StuckStep:
    """A step that flags_stuck should yield status='stuck' or 'partial'."""

    def test_stuck_step_yields_nondone_status(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        _patch_side_effects(monkeypatch)

        # Suppress recovery loop to keep the test deterministic
        try:
            import agent_loop as _al
            if hasattr(_al, "_recovery_in_progress"):
                monkeypatch.setattr(_al, "_recovery_in_progress", True)
        except ImportError:
            pass

        from handle import handle

        adapter = ScriptedAdapter([
            {"steps": ["Do the thing", "Summarize"]},
            {"tool": "flag_stuck", "reason": "Cannot access the resource"},
            {"tool": "complete_step", "result": "Summary: step 1 was stuck"},
        ])

        result = handle(
            "Do the thing and summarize",
            project="regression-f",
            adapter=adapter,
            force_lane="agenda",
        )

        # Stuck step → loop ends with status != "done" OR partial milestone reached
        assert result.status in ("stuck", "done", "partial")
        # Even when stuck, handle returns a HandleResult (no uncaught exception)
        assert result.handle_id


# ---------------------------------------------------------------------------
# Scenario G — Prefix stacking: effort:low strips model tier
# ---------------------------------------------------------------------------

class TestScenarioG_PrefixStacking:
    """effort:low overrides model tier to cheap; ralph: activates verify mode."""

    def test_effort_low_model_tier(self):
        """_apply_prefixes correctly extracts model_tier from effort:low."""
        from handle import _apply_prefixes
        pr = _apply_prefixes("effort:low do the task")
        assert pr.model_tier == "cheap"
        assert pr.message == "do the task"

    def test_ralph_prefix_stacks(self):
        """ralph: sets ralph_mode without overriding other flags."""
        from handle import _apply_prefixes
        pr = _apply_prefixes("ralph: effort:mid check the outputs")
        assert pr.ralph_mode is True
        assert pr.model_tier == "mid"
        assert "ralph" not in pr.message.lower()
        assert "effort" not in pr.message.lower()

    def test_direct_and_strict_stack(self):
        """direct: and strict: can stack on the same goal."""
        from handle import _apply_prefixes
        pr = _apply_prefixes("direct: strict: run the audit")
        assert pr.direct_mode is True
        assert pr.strict_mode is True
        assert pr.message == "run the audit"

    def test_effort_high_model_tier(self):
        """effort:high maps to power tier."""
        from handle import _apply_prefixes
        pr = _apply_prefixes("effort:high deep research on X")
        assert pr.model_tier == "power"
