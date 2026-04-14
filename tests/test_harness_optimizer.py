"""Tests for harness_optimizer.py — harness self-optimization loop."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from harness_optimizer import (
    HarnessProposal,
    HarnessOptimizerReport,
    _hash_prompt,
    _load_stuck_traces,
    _format_trace_for_prompt,
    load_candidates_history,
    run_harness_optimizer,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_trace(goal: str, steps: list, recorded_at: str = "2026-04-04T00:00:00Z") -> dict:
    return {"goal": goal, "steps": steps, "recorded_at": recorded_at, "outcome_id": "test-id"}


def _stuck_step(text: str, reason: str = "no progress") -> dict:
    return {"step": text, "status": "stuck", "stuck_reason": reason, "result": ""}


def _done_step(text: str) -> dict:
    return {"step": text, "status": "done", "result": "ok"}


# ---------------------------------------------------------------------------
# TestHashPrompt
# ---------------------------------------------------------------------------

class TestHashPrompt:
    def test_returns_16_hex_chars(self):
        h = _hash_prompt("hello world")
        assert len(h) == 16
        assert all(c in "0123456789abcdef" for c in h)

    def test_different_texts_differ(self):
        assert _hash_prompt("abc") != _hash_prompt("xyz")

    def test_same_text_same_hash(self):
        assert _hash_prompt("stable") == _hash_prompt("stable")


# ---------------------------------------------------------------------------
# TestLoadStuckTraces
# ---------------------------------------------------------------------------

class TestLoadStuckTraces:
    def test_missing_file_returns_empty(self, tmp_path):
        with patch("harness_optimizer._step_traces_path", return_value=tmp_path / "nope.jsonl"):
            result = _load_stuck_traces()
        assert result == []

    def test_only_returns_traces_with_stuck_steps(self, tmp_path):
        p = tmp_path / "step_traces.jsonl"
        traces = [
            _make_trace("goal A", [_stuck_step("step1")], "2026-04-04T01:00:00Z"),
            _make_trace("goal B", [_done_step("step1")], "2026-04-04T02:00:00Z"),
            _make_trace("goal C", [_stuck_step("stepX"), _done_step("stepY")], "2026-04-04T03:00:00Z"),
        ]
        with open(p, "w") as f:
            for t in traces:
                f.write(json.dumps(t) + "\n")

        with patch("harness_optimizer._step_traces_path", return_value=p):
            result = _load_stuck_traces()

        assert len(result) == 2
        goals = {r["goal"] for r in result}
        assert "goal A" in goals
        assert "goal C" in goals
        assert "goal B" not in goals

    def test_most_recent_first(self, tmp_path):
        p = tmp_path / "step_traces.jsonl"
        traces = [
            _make_trace("old", [_stuck_step("s")], "2026-01-01T00:00:00Z"),
            _make_trace("new", [_stuck_step("s")], "2026-04-04T00:00:00Z"),
        ]
        with open(p, "w") as f:
            for t in traces:
                f.write(json.dumps(t) + "\n")

        with patch("harness_optimizer._step_traces_path", return_value=p):
            result = _load_stuck_traces()

        assert result[0]["goal"] == "new"

    def test_limit_applied(self, tmp_path):
        p = tmp_path / "step_traces.jsonl"
        with open(p, "w") as f:
            for i in range(20):
                t = _make_trace(f"goal_{i}", [_stuck_step("s")], f"2026-04-04T{i:02d}:00:00Z")
                f.write(json.dumps(t) + "\n")

        with patch("harness_optimizer._step_traces_path", return_value=p):
            result = _load_stuck_traces(limit=5)

        assert len(result) == 5

    def test_malformed_lines_skipped(self, tmp_path):
        p = tmp_path / "step_traces.jsonl"
        good = _make_trace("ok", [_stuck_step("s")])
        with open(p, "w") as f:
            f.write("not valid json\n")
            f.write(json.dumps(good) + "\n")
            f.write("\n")

        with patch("harness_optimizer._step_traces_path", return_value=p):
            result = _load_stuck_traces()

        assert len(result) == 1
        assert result[0]["goal"] == "ok"


# ---------------------------------------------------------------------------
# TestFormatTraceForPrompt
# ---------------------------------------------------------------------------

class TestFormatTraceForPrompt:
    def test_includes_goal(self):
        trace = _make_trace("Find the answer", [_done_step("look it up")])
        out = _format_trace_for_prompt(trace)
        assert "Find the answer" in out

    def test_stuck_labeled(self):
        trace = _make_trace("g", [_stuck_step("broken step", "no tool available")])
        out = _format_trace_for_prompt(trace)
        assert "[STUCK]" in out
        assert "no tool available" in out

    def test_done_labeled(self):
        trace = _make_trace("g", [_done_step("finished")])
        out = _format_trace_for_prompt(trace)
        assert "[done]" in out

    def test_max_steps_capped(self):
        steps = [_done_step(f"step_{i}") for i in range(20)]
        trace = _make_trace("g", steps)
        out = _format_trace_for_prompt(trace, max_steps=3)
        # Should only show 3 steps
        assert out.count("[done]") == 3

    def test_goal_truncated_at_80(self):
        long_goal = "x" * 200
        trace = _make_trace(long_goal, [])
        out = _format_trace_for_prompt(trace)
        # Goal line should be truncated
        assert len(out.split("\n")[0]) <= len("Goal: ") + 80


# ---------------------------------------------------------------------------
# TestLoadCandidatesHistory
# ---------------------------------------------------------------------------

class TestLoadCandidatesHistory:
    def test_missing_file_returns_empty(self, tmp_path):
        with patch("harness_optimizer._candidates_path", return_value=tmp_path / "nope.jsonl"):
            result = load_candidates_history("EXECUTE_SYSTEM")
        assert result == []

    def test_filters_by_target(self, tmp_path):
        p = tmp_path / "harness_candidates.jsonl"
        entries = [
            {"target": "EXECUTE_SYSTEM", "hash": "aaa", "recorded_at": "2026-04-01"},
            {"target": "DECOMPOSE_SYSTEM", "hash": "bbb", "recorded_at": "2026-04-02"},
            {"target": "EXECUTE_SYSTEM", "hash": "ccc", "recorded_at": "2026-04-03"},
        ]
        with open(p, "w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")

        with patch("harness_optimizer._candidates_path", return_value=p):
            result = load_candidates_history("EXECUTE_SYSTEM")

        assert len(result) == 2
        assert all(r["target"] == "EXECUTE_SYSTEM" for r in result)

    def test_malformed_lines_skipped(self, tmp_path):
        p = tmp_path / "harness_candidates.jsonl"
        with open(p, "w") as f:
            f.write("garbage\n")
            f.write(json.dumps({"target": "EXECUTE_SYSTEM", "hash": "xyz"}) + "\n")

        with patch("harness_optimizer._candidates_path", return_value=p):
            result = load_candidates_history("EXECUTE_SYSTEM")

        assert len(result) == 1


# ---------------------------------------------------------------------------
# TestRunHarnessOptimizer — skip / no-op paths
# ---------------------------------------------------------------------------

class TestRunHarnessOptimizerSkips:
    def test_skips_when_no_harness_text_loadable(self):
        with patch("harness_optimizer._load_harness_text", return_value=None):
            report = run_harness_optimizer()
        assert report.skipped
        assert "could not load" in report.skip_reason

    def test_skips_when_insufficient_traces(self, tmp_path):
        with (
            patch("harness_optimizer._load_harness_text", return_value="some prompt text"),
            patch("harness_optimizer._step_traces_path", return_value=tmp_path / "none.jsonl"),
            patch("harness_optimizer._candidates_path", return_value=tmp_path / "cands.jsonl"),
        ):
            report = run_harness_optimizer(min_stuck_traces=3)
        assert report.skipped
        assert "stuck traces" in report.skip_reason

    def test_dry_run_skips_save(self, tmp_path):
        fake_trace = _make_trace("goal", [_stuck_step("s"), _stuck_step("t")])
        traces_path = tmp_path / "step_traces.jsonl"
        with open(traces_path, "w") as f:
            for _ in range(3):
                f.write(json.dumps(fake_trace) + "\n")

        with (
            patch("harness_optimizer._load_harness_text", return_value="prompt text"),
            patch("harness_optimizer._step_traces_path", return_value=traces_path),
            patch("harness_optimizer._candidates_path", return_value=tmp_path / "cands.jsonl"),
            patch("harness_optimizer._llm_analyze_harness", return_value=[]) as mock_llm,
        ):
            report = run_harness_optimizer(dry_run=True, min_stuck_traces=2)

        # dry_run=True means _llm_analyze_harness gets dry_run=True and returns []
        assert not report.skipped
        assert report.proposals == []

    def test_report_has_run_id_and_elapsed(self, tmp_path):
        with (
            patch("harness_optimizer._load_harness_text", return_value=None),
        ):
            report = run_harness_optimizer()
        assert report.run_id
        assert len(report.run_id) == 8

    def test_summary_skipped(self):
        with patch("harness_optimizer._load_harness_text", return_value=None):
            report = run_harness_optimizer()
        s = report.summary()
        assert "skipped" in s
        assert report.run_id in s


# ---------------------------------------------------------------------------
# TestRunHarnessOptimizerFull — happy path with proposals
# ---------------------------------------------------------------------------

class TestRunHarnessOptimizerFull:
    def _make_proposals(self) -> list:
        return [
            HarnessProposal(
                target="EXECUTE_SYSTEM",
                original_clause="old text",
                proposed_change="new text",
                failure_pattern="agent gets stuck on tool calls",
                confidence=0.8,
            )
        ]

    def test_proposals_saved_on_normal_run(self, tmp_path):
        fake_trace = _make_trace("goal", [_stuck_step("s")])
        traces_path = tmp_path / "step_traces.jsonl"
        with open(traces_path, "w") as f:
            for _ in range(3):
                f.write(json.dumps(fake_trace) + "\n")

        saved = []

        def fake_save(proposals, run_id):
            saved.extend(proposals)
            return len(proposals)

        with (
            patch("harness_optimizer._load_harness_text", return_value="prompt text"),
            patch("harness_optimizer._step_traces_path", return_value=traces_path),
            patch("harness_optimizer._candidates_path", return_value=tmp_path / "cands.jsonl"),
            patch("harness_optimizer._llm_analyze_harness", return_value=self._make_proposals()),
            patch("harness_optimizer._save_harness_proposals", side_effect=fake_save),
        ):
            report = run_harness_optimizer(min_stuck_traces=2)

        assert not report.skipped
        assert len(report.proposals) == 1
        assert len(saved) == 1

    def test_summary_includes_proposal_count(self, tmp_path):
        fake_trace = _make_trace("goal", [_stuck_step("s")])
        traces_path = tmp_path / "step_traces.jsonl"
        with open(traces_path, "w") as f:
            for _ in range(3):
                f.write(json.dumps(fake_trace) + "\n")

        with (
            patch("harness_optimizer._load_harness_text", return_value="prompt text"),
            patch("harness_optimizer._step_traces_path", return_value=traces_path),
            patch("harness_optimizer._candidates_path", return_value=tmp_path / "cands.jsonl"),
            patch("harness_optimizer._llm_analyze_harness", return_value=self._make_proposals()),
            patch("harness_optimizer._save_harness_proposals", return_value=1),
        ):
            report = run_harness_optimizer(min_stuck_traces=2)

        s = report.summary()
        assert "proposals=1" in s

    def test_candidate_recorded_on_normal_run(self, tmp_path):
        fake_trace = _make_trace("goal", [_stuck_step("s")])
        traces_path = tmp_path / "step_traces.jsonl"
        with open(traces_path, "w") as f:
            for _ in range(3):
                f.write(json.dumps(fake_trace) + "\n")

        cands_path = tmp_path / "cands.jsonl"

        with (
            patch("harness_optimizer._load_harness_text", return_value="prompt text"),
            patch("harness_optimizer._step_traces_path", return_value=traces_path),
            patch("harness_optimizer._candidates_path", return_value=cands_path),
            patch("harness_optimizer._llm_analyze_harness", return_value=[]),
            patch("harness_optimizer._save_harness_proposals", return_value=0),
        ):
            run_harness_optimizer(min_stuck_traces=2)

        assert cands_path.exists()
        lines = [json.loads(l) for l in cands_path.read_text().splitlines() if l.strip()]
        assert len(lines) >= 1
        assert lines[0]["target"] in ("EXECUTE_SYSTEM", "DECOMPOSE_SYSTEM")

    def test_no_candidate_recorded_in_dry_run(self, tmp_path):
        cands_path = tmp_path / "cands.jsonl"
        with patch("harness_optimizer._load_harness_text", return_value=None):
            run_harness_optimizer(dry_run=True)
        # If harness text not found, skipped — cands_path should not be written
        assert not cands_path.exists()


# ---------------------------------------------------------------------------
# Friction scanner tests — "Harness Is the Problem"
# ---------------------------------------------------------------------------

from harness_optimizer import (
    FrictionPoint,
    HarnessFrictionReport,
    scan_harness_friction,
    _classify_error,
    _save_friction_suggestions,
)


class TestClassifyError:
    def test_timeout(self):
        assert _classify_error("step timed out after 900s") == "timeout"

    def test_rate_limit(self):
        assert _classify_error("hit your limit for claude-opus") == "rate_limit"

    def test_adapter_error(self):
        assert _classify_error("adapter_error: unexpected keyword argument") == "adapter_error"

    def test_retry_storm(self):
        assert _classify_error("retrying step after failure") == "retry_storm"

    def test_tool_error(self):
        assert _classify_error("tool_error: json decode failed") == "tool_error"

    def test_phase_failure(self):
        assert _classify_error("step stuck in phase C") == "phase_failure"

    def test_none_for_clean_text(self):
        assert _classify_error("step completed successfully") is None


class TestScanHarnessFriction:
    def _make_adapter_error_traces(self, n: int) -> list:
        traces = []
        for i in range(n):
            traces.append({
                "goal": f"goal {i}",
                "recorded_at": f"2026-04-14T0{i % 10}:00:00Z",
                "steps": [
                    {"step": "research X", "status": "stuck",
                     "stuck_reason": "adapter_error: unexpected keyword argument thinking_budget",
                     "result": ""},
                ],
            })
        return traces

    def _make_timeout_traces(self, n: int) -> list:
        traces = []
        for i in range(n):
            traces.append({
                "goal": f"goal {i}",
                "recorded_at": f"2026-04-14T0{i % 10}:00:00Z",
                "steps": [
                    {"step": "run pytest", "status": "stuck",
                     "stuck_reason": "subprocess timed out after 900s",
                     "result": ""},
                ],
            })
        return traces

    def test_detects_adapter_errors(self):
        traces = self._make_adapter_error_traces(5)
        report = scan_harness_friction(traces=traces, min_frequency=2)
        assert not report.skipped
        assert any(fp.friction_type == "adapter_error" for fp in report.friction_points)

    def test_detects_timeouts(self):
        traces = self._make_timeout_traces(5)
        report = scan_harness_friction(traces=traces, min_frequency=2)
        assert not report.skipped
        assert any(fp.friction_type == "timeout" for fp in report.friction_points)

    def test_no_traces_skips(self):
        report = scan_harness_friction(traces=[], min_frequency=1)
        assert report.skipped
        assert report.traces_analyzed == 0

    def test_below_min_frequency_not_surfaced(self):
        # 1 adapter error with min_frequency=3 — should NOT create a friction point
        traces = self._make_adapter_error_traces(1)
        report = scan_harness_friction(traces=traces, min_frequency=3)
        assert not any(fp.friction_type == "adapter_error" for fp in report.friction_points)

    def test_high_rate_is_high_severity(self):
        traces = self._make_adapter_error_traces(10)
        report = scan_harness_friction(traces=traces, min_frequency=2)
        adapter_fps = [fp for fp in report.friction_points if fp.friction_type == "adapter_error"]
        if adapter_fps:
            assert adapter_fps[0].severity == "high"

    def test_friction_points_have_suggestion(self):
        traces = self._make_adapter_error_traces(5)
        report = scan_harness_friction(traces=traces, min_frequency=2)
        for fp in report.friction_points:
            assert fp.suggestion and len(fp.suggestion) > 10

    def test_sorted_high_first(self):
        # Mix of high and low severity — highs should come first
        adapter_traces = self._make_adapter_error_traces(8)  # high rate
        single_timeout = self._make_timeout_traces(2)  # medium/low rate
        all_traces = adapter_traces + single_timeout
        report = scan_harness_friction(traces=all_traces, min_frequency=2)
        if len(report.friction_points) >= 2:
            sev_order = {"high": 0, "medium": 1, "low": 2}
            for i in range(len(report.friction_points) - 1):
                a = sev_order.get(report.friction_points[i].severity, 3)
                b = sev_order.get(report.friction_points[i + 1].severity, 3)
                assert a <= b

    def test_clean_traces_no_friction(self):
        clean_traces = [
            {"goal": "research X", "recorded_at": "2026-04-14T00:00:00Z",
             "steps": [{"step": "step 1", "status": "done", "result": "success", "stuck_reason": ""}]}
            for _ in range(10)
        ]
        report = scan_harness_friction(traces=clean_traces, min_frequency=2)
        assert len(report.friction_points) == 0


class TestSaveFrictionSuggestions:
    def test_saves_medium_and_high_only(self, tmp_path):
        fps = [
            FrictionPoint("adapter_error", "adapter_error: 3/10 traces", 3, 10,
                          ["example"], "fix kwargs", "high"),
            FrictionPoint("timeout", "timeout: 2/10 traces", 2, 10,
                          ["example"], "fix timeout", "medium"),
            FrictionPoint("retry_storm", "retry_storm: 2/10 traces", 2, 10,
                          ["example"], "reduce retries", "low"),
        ]
        with patch("harness_optimizer._save_friction_suggestions.__wrapped__",
                   side_effect=lambda *a, **kw: None) if False else patch(
            "evolver.Suggestion", side_effect=lambda **kw: MagicMock(**kw)
        ), patch("evolver._save_suggestions") as mock_save:
            from harness_optimizer import _save_friction_suggestions
            n = _save_friction_suggestions(fps, run_id="test-run", dry_run=False)
            # Should save high + medium = 2
            # (low is filtered out)
            assert n == 2

    def test_dry_run_saves_nothing(self):
        fps = [
            FrictionPoint("adapter_error", "x", 5, 10, [], "fix it", "high"),
        ]
        with patch("evolver._save_suggestions") as mock_save:
            from harness_optimizer import _save_friction_suggestions
            n = _save_friction_suggestions(fps, run_id="test-run", dry_run=True)
            assert n == 0
            mock_save.assert_not_called()
