"""Tests for step_exec — _parse_when and schedule_run tool handler."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


# ---------------------------------------------------------------------------
# _parse_when
# ---------------------------------------------------------------------------

class TestParseWhen:
    def test_daily_at(self):
        from step_exec import _parse_when
        result = _parse_when("daily at 09:00")
        assert result == {"type": "daily", "time": "09:00"}

    def test_daily_at_no_leading_zero(self):
        from step_exec import _parse_when
        result = _parse_when("daily at 8:30")
        assert result == {"type": "daily", "time": "8:30"}

    def test_in_minutes(self):
        from step_exec import _parse_when
        result = _parse_when("in 30 minutes")
        assert result["type"] == "once"
        assert "at" in result

    def test_in_hours(self):
        from step_exec import _parse_when
        result = _parse_when("in 2 hours")
        assert result["type"] == "once"
        assert "at" in result

    def test_in_days(self):
        from step_exec import _parse_when
        result = _parse_when("in 1 day")
        assert result["type"] == "once"
        assert "at" in result

    def test_iso_datetime_z(self):
        from step_exec import _parse_when
        result = _parse_when("2026-04-02T09:00:00Z")
        assert result["type"] == "once"
        assert "09:00:00" in result["at"]

    def test_iso_datetime_offset(self):
        from step_exec import _parse_when
        result = _parse_when("2026-04-02T09:00:00+00:00")
        assert result["type"] == "once"
        assert "09:00:00" in result["at"]

    def test_unknown_falls_back(self):
        from step_exec import _parse_when
        result = _parse_when("whenever")
        # Fallback: once, 1 hour from now
        assert result["type"] == "once"
        assert "at" in result

    def test_plural_singular_minutes(self):
        from step_exec import _parse_when
        singular = _parse_when("in 1 minute")
        plural = _parse_when("in 5 minutes")
        assert singular["type"] == "once"
        assert plural["type"] == "once"


# ---------------------------------------------------------------------------
# schedule_run tool handler in execute_step
# ---------------------------------------------------------------------------

class _ScheduleRunAdapter:
    """Minimal adapter that returns a schedule_run tool call."""
    model_key = "test"

    def __init__(self, goal, when, note=""):
        self._goal = goal
        self._when = when
        self._note = note

    def complete(self, messages, *, tools=None, tool_choice="auto",
                 max_tokens=4096, temperature=0.3, **kwargs):
        from llm import LLMResponse, ToolCall
        args = {"goal": self._goal, "when": self._when}
        if self._note:
            args["note"] = self._note
        return LLMResponse(
            content="",
            tool_calls=[ToolCall(name="schedule_run", arguments=args)],
            stop_reason="tool_use",
            input_tokens=50,
            output_tokens=20,
        )


class TestScheduleRunTool:
    def test_schedule_run_daily(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))

        from step_exec import execute_step, EXECUTE_TOOLS

        adapter = _ScheduleRunAdapter(
            goal="Check Polymarket top markets",
            when="daily at 08:00",
        )
        result = execute_step(
            goal="Set up daily research routine",
            step_text="Schedule daily Polymarket check at 08:00",
            step_num=1,
            total_steps=1,
            completed_context=[],
            adapter=adapter,
            tools=EXECUTE_TOOLS,
        )
        assert result["status"] == "done"
        assert "Polymarket" in result["result"]
        assert "job_id" in result["result"]

    def test_schedule_run_in_hours(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))

        from step_exec import execute_step, EXECUTE_TOOLS

        adapter = _ScheduleRunAdapter(
            goal="Re-analyze market after data update",
            when="in 2 hours",
        )
        result = execute_step(
            goal="Watch for market update",
            step_text="Schedule re-analysis in 2 hours",
            step_num=1,
            total_steps=1,
            completed_context=[],
            adapter=adapter,
            tools=EXECUTE_TOOLS,
        )
        assert result["status"] == "done"
        assert "Re-analyze" in result["result"]

    def test_schedule_run_with_note(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))

        from step_exec import execute_step, EXECUTE_TOOLS

        adapter = _ScheduleRunAdapter(
            goal="Run weekly digest",
            when="daily at 07:30",
            note="Include top 5 markets",
        )
        result = execute_step(
            goal="Set up weekly digest",
            step_text="Schedule weekly digest",
            step_num=1,
            total_steps=1,
            completed_context=[],
            adapter=adapter,
            tools=EXECUTE_TOOLS,
        )
        assert result["status"] == "done"
        assert "Include top 5 markets" in result["result"]

    def test_schedule_run_persists_job(self, tmp_path, monkeypatch):
        """Job should actually be saved to jobs.json."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))

        from step_exec import execute_step, EXECUTE_TOOLS

        adapter = _ScheduleRunAdapter(
            goal="Monitor BTC price",
            when="in 10 minutes",
        )
        execute_step(
            goal="Set up monitoring",
            step_text="Schedule BTC monitoring",
            step_num=1,
            total_steps=1,
            completed_context=[],
            adapter=adapter,
            tools=EXECUTE_TOOLS,
        )
        # Verify the job was persisted
        from scheduler import list_jobs
        jobs = list_jobs()
        assert any("BTC" in j["goal"] for j in jobs)
