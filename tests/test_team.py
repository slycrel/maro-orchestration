"""Tests for team.py — TeamCreateTool dynamic worker creation."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from team import (
    TeamResult,
    _build_persona,
    _ROLE_PERSONAS,
    create_team_worker,
    format_team_result_for_injection,
)


# ---------------------------------------------------------------------------
# _build_persona
# ---------------------------------------------------------------------------

class TestBuildPersona:
    def test_known_role_returns_rich_persona(self):
        p = _build_persona("market-analyst", None)
        assert "Market Analyst" in p
        assert "deliver_result" in p

    def test_known_role_case_insensitive(self):
        p = _build_persona("Market-Analyst", None)
        assert "Market Analyst" in p

    def test_known_role_underscore_normalised(self):
        p = _build_persona("risk_auditor", None)
        assert "Risk Auditor" in p

    def test_unknown_role_uses_generic(self):
        p = _build_persona("quantum-physicist", None)
        assert "quantum-physicist" in p
        assert "deliver_result" in p

    def test_persona_override_takes_precedence(self):
        p = _build_persona("market-analyst", "Custom persona text")
        assert p == "Custom persona text"

    def test_all_known_roles_non_empty(self):
        for role in _ROLE_PERSONAS:
            p = _build_persona(role, None)
            assert len(p) > 20

    def test_devil_advocate_role(self):
        p = _build_persona("devil-advocate", None)
        assert "Devil" in p or "devil" in p.lower()

    def test_synthesizer_role(self):
        p = _build_persona("synthesizer", None)
        assert "Synthesizer" in p or "synthesizer" in p.lower()


# ---------------------------------------------------------------------------
# TeamResult
# ---------------------------------------------------------------------------

class TestTeamResult:
    def test_default_fields(self):
        r = TeamResult(role="analyst", task="analyze X", status="done", result="findings")
        assert r.tokens_in == 0
        assert r.tokens_out == 0
        assert r.stuck_reason is None

    def test_blocked_result(self):
        r = TeamResult(role="analyst", task="analyze X", status="blocked",
                       result="", stuck_reason="no data")
        assert r.status == "blocked"
        assert r.stuck_reason == "no data"


# ---------------------------------------------------------------------------
# create_team_worker — dry run
# ---------------------------------------------------------------------------

class TestCreateTeamWorkerDryRun:
    def test_dry_run_returns_done(self):
        result = create_team_worker("market-analyst", "analyze BTC trend", dry_run=True)
        assert result.status == "done"
        assert "market-analyst" in result.result
        assert "analyze BTC trend" in result.result

    def test_dry_run_no_adapter_needed(self):
        result = create_team_worker("risk-auditor", "check failure modes", adapter=None, dry_run=True)
        assert result.status == "done"

    def test_dry_run_preserves_role_and_task(self):
        result = create_team_worker("synthesizer", "merge all findings", dry_run=True)
        assert result.role == "synthesizer"
        assert result.task == "merge all findings"

    def test_dry_run_adapter_none_defaults_to_dry(self):
        # When adapter=None and dry_run=False, still does dry run
        result = create_team_worker("analyst", "analyze X", adapter=None, dry_run=False)
        assert result.status == "done"
        assert "[dry-run:" in result.result


# ---------------------------------------------------------------------------
# create_team_worker — adapter mocking
# ---------------------------------------------------------------------------

def _make_adapter(tool_name: str, tool_args: dict):
    """Build a mock adapter that returns a specific tool call."""
    tc = MagicMock()
    tc.name = tool_name
    tc.arguments = tool_args
    resp = MagicMock()
    resp.tool_calls = [tc]
    resp.content = ""
    resp.input_tokens = 100
    resp.output_tokens = 50
    adapter = MagicMock()
    adapter.complete = MagicMock(return_value=resp)
    return adapter


class TestCreateTeamWorkerWithAdapter:
    def test_deliver_result_tool_call(self):
        adapter = _make_adapter("deliver_result", {"result": "analyst findings here"})
        result = create_team_worker("market-analyst", "analyze X", adapter=adapter)
        assert result.status == "done"
        assert result.result == "analyst findings here"
        assert result.tokens_in == 100
        assert result.tokens_out == 50

    def test_flag_blocked_tool_call(self):
        adapter = _make_adapter("flag_blocked", {"reason": "no data available", "partial": ""})
        result = create_team_worker("data-extractor", "extract Y", adapter=adapter)
        assert result.status == "blocked"
        assert result.stuck_reason == "no data available"

    def test_content_fallback(self):
        resp = MagicMock()
        resp.tool_calls = []
        resp.content = "This is the analyst's content response, long enough to be useful."
        resp.input_tokens = 80
        resp.output_tokens = 40
        adapter = MagicMock()
        adapter.complete = MagicMock(return_value=resp)
        result = create_team_worker("analyst", "task", adapter=adapter)
        assert result.status == "done"
        assert "content response" in result.result

    def test_adapter_exception_returns_blocked(self):
        adapter = MagicMock()
        adapter.complete = MagicMock(side_effect=RuntimeError("API error"))
        result = create_team_worker("analyst", "task", adapter=adapter)
        assert result.status == "blocked"
        assert "API error" in result.stuck_reason

    def test_custom_persona_injected(self):
        adapter = _make_adapter("deliver_result", {"result": "custom output"})
        result = create_team_worker(
            "custom-role", "do thing",
            persona="You are a custom expert.",
            adapter=adapter,
        )
        # Verify adapter was called with custom persona in system message
        call_args = adapter.complete.call_args
        messages = call_args[0][0]  # positional first arg
        system_msg = next(m for m in messages if m.role == "system")
        assert "custom expert" in system_msg.content
        assert result.status == "done"

    def test_no_useful_output_returns_blocked(self):
        resp = MagicMock()
        resp.tool_calls = []
        resp.content = "ok"  # too short
        resp.input_tokens = 10
        resp.output_tokens = 2
        adapter = MagicMock()
        adapter.complete = MagicMock(return_value=resp)
        result = create_team_worker("analyst", "task", adapter=adapter)
        assert result.status == "blocked"
        assert "no useful output" in result.stuck_reason


# ---------------------------------------------------------------------------
# format_team_result_for_injection
# ---------------------------------------------------------------------------

class TestFormatTeamResultForInjection:
    def test_done_result_formatting(self):
        r = TeamResult(role="market-analyst", task="analyze BTC",
                       status="done", result="BTC is trending up.")
        text = format_team_result_for_injection(r)
        assert "market-analyst" in text
        assert "analyze BTC" in text
        assert "BTC is trending up" in text

    def test_blocked_result_formatting(self):
        r = TeamResult(role="data-extractor", task="get prices",
                       status="blocked", result="", stuck_reason="no API key")
        text = format_team_result_for_injection(r)
        assert "BLOCKED" in text
        assert "no API key" in text

    def test_blocked_with_partial_shows_partial(self):
        r = TeamResult(role="analyst", task="analyze",
                       status="blocked", result="partial work done",
                       stuck_reason="rate limit")
        text = format_team_result_for_injection(r)
        assert "partial work done" in text


# ---------------------------------------------------------------------------
# create_team_worker tool in EXECUTE_TOOLS
# ---------------------------------------------------------------------------

class TestCreateTeamWorkerInExecuteTools:
    def test_tool_registered(self):
        import step_exec
        names = [t["name"] for t in step_exec.EXECUTE_TOOLS]
        assert "create_team_worker" in names

    def test_tool_not_in_short_subset(self):
        import step_exec
        names = [t["name"] for t in step_exec.EXECUTE_TOOLS_SHORT]
        assert "create_team_worker" not in names

    def test_tool_not_in_inspector_subset(self):
        import step_exec
        names = [t["name"] for t in step_exec.EXECUTE_TOOLS_INSPECTOR]
        assert "create_team_worker" not in names

    def test_tool_in_worker_subset(self):
        import step_exec
        names = [t["name"] for t in step_exec.EXECUTE_TOOLS_WORKER]
        assert "create_team_worker" in names

    def test_tool_has_required_fields(self):
        import step_exec
        tool = next(t for t in step_exec.EXECUTE_TOOLS if t["name"] == "create_team_worker")
        assert "description" in tool
        assert "parameters" in tool
        required = tool["parameters"].get("required", [])
        assert "role" in required
        assert "task" in required


# ---------------------------------------------------------------------------
# execute_step integration: create_team_worker tool dispatch
# ---------------------------------------------------------------------------

class TestExecuteStepWithTeamWorker:
    def _make_exec_adapter(self, team_result_text: str):
        """Adapter that returns create_team_worker tool call on first call."""
        tc = MagicMock()
        tc.name = "create_team_worker"
        tc.arguments = {"role": "market-analyst", "task": "analyze trends"}
        resp = MagicMock()
        resp.tool_calls = [tc]
        resp.content = ""
        resp.input_tokens = 200
        resp.output_tokens = 80
        adapter = MagicMock()
        adapter.complete = MagicMock(return_value=resp)
        return adapter

    def test_team_worker_step_returns_done(self, monkeypatch):
        import step_exec, team

        monkeypatch.setattr(
            team, "create_team_worker",
            lambda role, task, **kw: team.TeamResult(
                role=role, task=task, status="done",
                result="analyst output here"
            )
        )

        adapter = self._make_exec_adapter("analyst output here")
        import step_exec as _se
        from llm import LLMTool
        tools = [LLMTool(**t) for t in _se.EXECUTE_TOOLS_WORKER]

        result = _se.execute_step(
            step_text="analyze market using a specialist",
            step_num=1,
            total_steps=3,
            goal="research markets",
            completed_context=[],
            adapter=adapter,
            tools=tools,
        )
        assert result["status"] == "done"
        assert "market-analyst" in result["result"] or "analyst output" in result["result"]

    def test_team_worker_step_summary_contains_role(self, monkeypatch):
        import step_exec, team

        monkeypatch.setattr(
            team, "create_team_worker",
            lambda role, task, **kw: team.TeamResult(
                role=role, task=task, status="done", result="output"
            )
        )

        adapter = self._make_exec_adapter("output")
        from llm import LLMTool
        tools = [LLMTool(**t) for t in step_exec.EXECUTE_TOOLS_WORKER]

        result = step_exec.execute_step(
            step_text="delegate to specialist",
            step_num=1,
            total_steps=2,
            goal="test",
            completed_context=[],
            adapter=adapter,
            tools=tools,
        )
        assert "market-analyst" in result.get("summary", "")
