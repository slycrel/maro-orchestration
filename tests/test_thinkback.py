"""Tests for thinkback.py — hindsight replay and session-level self-improvement."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from thinkback import (
    StepReview,
    ThinkbackReport,
    _build_steps_block,
    _parse_thinkback_response,
    run_thinkback,
    run_thinkback_from_outcome,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_loop_result(
    goal="test goal",
    status="done",
    steps=None,
    loop_id="abc12345",
):
    """Build a minimal LoopResult-like object."""

    class FakeStep:
        def __init__(self, index, text, result, step_status="done", confidence=""):
            self.index = index
            self.text = text
            self.status = step_status
            self.result = result
            self.confidence = confidence
            self.iteration = 0
            self.tokens_in = 10
            self.tokens_out = 5

    class FakeLoop:
        pass

    obj = FakeLoop()
    obj.loop_id = loop_id
    obj.goal = goal
    obj.status = status
    obj.total_tokens_in = 100
    obj.total_tokens_out = 50
    if steps is None:
        steps = [
            FakeStep(0, "Research the topic", "Found 5 relevant sources."),
            FakeStep(1, "Analyze findings", "Key patterns identified."),
            FakeStep(2, "Write summary", "Summary complete."),
        ]
    obj.steps = steps
    return obj


def _make_thinkback_response(
    step_count=3,
    overall="acceptable",
    efficiency=0.75,
    would_retry=False,
):
    """Build a valid JSON thinkback response."""
    step_reviews = [
        {
            "step_index": i,
            "step_summary": f"Step {i} summary",
            "decision_quality": "good" if i == 0 else "acceptable",
            "hindsight_note": f"Step {i} was handled well.",
            "counterfactual": None,
        }
        for i in range(step_count)
    ]
    return json.dumps({
        "step_reviews": step_reviews,
        "overall_assessment": overall,
        "mission_efficiency": efficiency,
        "key_lessons": ["Lesson A", "Lesson B"],
        "would_retry": would_retry,
        "retry_strategy": "Try a different approach" if would_retry else None,
    })


def _make_adapter(response_text: str):
    resp = MagicMock()
    resp.content = response_text
    resp.tool_calls = []
    resp.input_tokens = 200
    resp.output_tokens = 100
    adapter = MagicMock()
    adapter.complete = MagicMock(return_value=resp)
    return adapter


# ---------------------------------------------------------------------------
# StepReview
# ---------------------------------------------------------------------------

class TestStepReview:
    def test_basic_fields(self):
        r = StepReview(
            step_index=2,
            step_summary="analyze data",
            decision_quality="good",
            hindsight_note="Good call.",
        )
        assert r.step_index == 2
        assert r.counterfactual is None

    def test_with_counterfactual(self):
        r = StepReview(
            step_index=1,
            step_summary="bad step",
            decision_quality="poor",
            hindsight_note="Should have done X.",
            counterfactual="Do X instead",
        )
        assert r.counterfactual == "Do X instead"
        assert r.decision_quality == "poor"


# ---------------------------------------------------------------------------
# ThinkbackReport
# ---------------------------------------------------------------------------

class TestThinkbackReport:
    def _make_report(self, reviews=None):
        reviews = reviews or [
            StepReview(0, "step 0", "good", "fine"),
            StepReview(1, "step 1", "acceptable", "ok"),
            StepReview(2, "step 2", "poor", "bad", "do differently"),
        ]
        return ThinkbackReport(
            run_id="abc",
            goal="test goal",
            status="done",
            step_reviews=reviews,
            overall_assessment="acceptable",
            mission_efficiency=0.6,
            key_lessons=["L1", "L2"],
            would_retry=False,
            retry_strategy=None,
        )

    def test_summary_contains_goal(self):
        r = self._make_report()
        s = r.summary()
        assert "test goal" in s

    def test_summary_shows_efficiency(self):
        r = self._make_report()
        s = r.summary()
        assert "60%" in s

    def test_summary_counts_poor(self):
        r = self._make_report()
        s = r.summary()
        # Should show 1 poor
        assert "1" in s

    def test_summary_shows_lessons(self):
        r = self._make_report()
        s = r.summary()
        assert "L1" in s

    def test_summary_no_retry_strategy_when_false(self):
        r = self._make_report()
        assert r.would_retry is False


# ---------------------------------------------------------------------------
# _build_steps_block
# ---------------------------------------------------------------------------

class TestBuildStepsBlock:
    def test_empty_steps(self):
        result = _build_steps_block([])
        assert "no step data" in result

    def test_formats_step_status(self):
        steps = [
            {"index": 0, "text": "do X", "status": "done", "result": "X done", "confidence": ""},
            {"index": 1, "text": "do Y", "status": "blocked", "result": "", "confidence": "weak"},
        ]
        block = _build_steps_block(steps)
        assert "done" in block
        assert "blocked" in block
        assert "[weak]" in block

    def test_truncates_long_text(self):
        steps = [{"index": 0, "text": "x" * 200, "status": "done", "result": "res", "confidence": ""}]
        block = _build_steps_block(steps)
        # text capped at 80 chars
        assert "x" * 200 not in block

    def test_result_preview_truncated(self):
        steps = [{"index": 0, "text": "step", "status": "done", "result": "r" * 500, "confidence": ""}]
        block = _build_steps_block(steps)
        # result capped at 200 chars
        assert "r" * 500 not in block


# ---------------------------------------------------------------------------
# _parse_thinkback_response
# ---------------------------------------------------------------------------

class TestParseThinkbackResponse:
    def test_valid_json(self):
        raw = _make_thinkback_response(step_count=2)
        parsed = _parse_thinkback_response(raw, 2)
        assert "step_reviews" in parsed
        assert len(parsed["step_reviews"]) == 2

    def test_json_with_prefix_text(self):
        raw = "Here is my analysis:\n" + _make_thinkback_response()
        parsed = _parse_thinkback_response(raw, 3)
        assert "step_reviews" in parsed

    def test_empty_string(self):
        parsed = _parse_thinkback_response("", 0)
        assert parsed == {}

    def test_invalid_json(self):
        parsed = _parse_thinkback_response("{bad json", 0)
        assert parsed == {}


# ---------------------------------------------------------------------------
# run_thinkback — dry_run
# ---------------------------------------------------------------------------

class TestRunThinkbackDryRun:
    def test_dry_run_no_adapter_needed(self):
        lr = _make_loop_result()
        report = run_thinkback(lr, dry_run=True)
        assert isinstance(report, ThinkbackReport)
        assert report.goal == "test goal"
        assert report.status == "done"

    def test_dry_run_step_count_matches(self):
        lr = _make_loop_result()
        report = run_thinkback(lr, dry_run=True)
        assert len(report.step_reviews) == len(lr.steps)

    def test_dry_run_all_acceptable(self):
        lr = _make_loop_result()
        report = run_thinkback(lr, dry_run=True)
        for r in report.step_reviews:
            assert r.decision_quality == "acceptable"

    def test_dry_run_has_lesson(self):
        lr = _make_loop_result()
        report = run_thinkback(lr, dry_run=True)
        assert len(report.key_lessons) > 0

    def test_dry_run_empty_steps(self):
        lr = _make_loop_result(steps=[])
        report = run_thinkback(lr, dry_run=True)
        assert len(report.step_reviews) == 0


# ---------------------------------------------------------------------------
# run_thinkback — with adapter
# ---------------------------------------------------------------------------

class TestRunThinkbackWithAdapter:
    def test_parses_good_response(self):
        lr = _make_loop_result()
        raw = _make_thinkback_response(step_count=3, overall="strong", efficiency=0.9)
        adapter = _make_adapter(raw)

        report = run_thinkback(lr, adapter=adapter)
        assert report.overall_assessment == "strong"
        assert abs(report.mission_efficiency - 0.9) < 0.01
        assert len(report.step_reviews) == 3

    def test_poor_step_identified(self):
        lr = _make_loop_result()
        data = {
            "step_reviews": [
                {"step_index": 0, "step_summary": "step", "decision_quality": "poor",
                 "hindsight_note": "was bad", "counterfactual": "do X"},
                {"step_index": 1, "step_summary": "step", "decision_quality": "good",
                 "hindsight_note": "fine", "counterfactual": None},
                {"step_index": 2, "step_summary": "step", "decision_quality": "good",
                 "hindsight_note": "fine", "counterfactual": None},
            ],
            "overall_assessment": "acceptable",
            "mission_efficiency": 0.7,
            "key_lessons": ["fix step 0"],
            "would_retry": False,
            "retry_strategy": None,
        }
        adapter = _make_adapter(json.dumps(data))
        report = run_thinkback(lr, adapter=adapter)
        poor = [r for r in report.step_reviews if r.decision_quality == "poor"]
        assert len(poor) == 1
        assert poor[0].counterfactual == "do X"

    def test_adapter_exception_falls_back_to_dry(self):
        lr = _make_loop_result()
        adapter = MagicMock()
        adapter.complete = MagicMock(side_effect=RuntimeError("API error"))
        # Should not raise — falls back
        report = run_thinkback(lr, adapter=adapter)
        assert isinstance(report, ThinkbackReport)

    def test_partial_json_fills_missing_steps(self):
        lr = _make_loop_result()
        # Only 1 step review in response for a 3-step run
        data = {
            "step_reviews": [
                {"step_index": 0, "step_summary": "ok", "decision_quality": "good",
                 "hindsight_note": "good", "counterfactual": None},
            ],
            "overall_assessment": "acceptable",
            "mission_efficiency": 0.5,
            "key_lessons": [],
            "would_retry": False,
            "retry_strategy": None,
        }
        adapter = _make_adapter(json.dumps(data))
        report = run_thinkback(lr, adapter=adapter)
        # Missing steps should be filled in
        assert len(report.step_reviews) == 3

    def test_would_retry_with_strategy(self):
        lr = _make_loop_result(status="stuck")
        raw = _make_thinkback_response(overall="weak", efficiency=0.3, would_retry=True)
        adapter = _make_adapter(raw)
        report = run_thinkback(lr, adapter=adapter)
        assert report.would_retry is True
        assert report.retry_strategy is not None

    def test_step_reviews_sorted_by_index(self):
        lr = _make_loop_result()
        data = {
            "step_reviews": [
                {"step_index": 2, "step_summary": "last", "decision_quality": "good",
                 "hindsight_note": "ok", "counterfactual": None},
                {"step_index": 0, "step_summary": "first", "decision_quality": "good",
                 "hindsight_note": "ok", "counterfactual": None},
                {"step_index": 1, "step_summary": "mid", "decision_quality": "good",
                 "hindsight_note": "ok", "counterfactual": None},
            ],
            "overall_assessment": "strong",
            "mission_efficiency": 0.85,
            "key_lessons": [],
            "would_retry": False,
            "retry_strategy": None,
        }
        adapter = _make_adapter(json.dumps(data))
        report = run_thinkback(lr, adapter=adapter)
        indices = [r.step_index for r in report.step_reviews]
        assert indices == sorted(indices)

    def test_save_lessons_called(self, tmp_path, monkeypatch):
        import thinkback
        monkeypatch.setattr(thinkback, "_save_thinkback_lessons",
                            MagicMock())
        lr = _make_loop_result()
        adapter = _make_adapter(_make_thinkback_response())
        run_thinkback(lr, adapter=adapter, save_lessons=True)
        thinkback._save_thinkback_lessons.assert_called_once()


# ---------------------------------------------------------------------------
# run_thinkback_from_outcome
# ---------------------------------------------------------------------------

class TestRunThinkbackFromOutcome:
    def _outcome(self):
        return {
            "outcome_id": "abc12345",
            "goal": "research nootropics",
            "status": "done",
            "summary": "Found 5 key patterns.",
            "lessons": ["Lesson 1", "Lesson 2"],
            "task_type": "research",
            "tokens_in": 500,
            "tokens_out": 200,
        }

    def test_dry_run(self):
        outcome = self._outcome()
        report = run_thinkback_from_outcome(outcome, dry_run=True)
        assert report.goal == "research nootropics"
        assert report.run_id == "abc12345"

    def test_synthesizes_steps_from_summary_and_lessons(self):
        outcome = self._outcome()
        report = run_thinkback_from_outcome(outcome, dry_run=True)
        # 1 (summary) + 2 (lessons)
        assert len(report.step_reviews) == 3

    def test_with_adapter(self):
        outcome = self._outcome()
        raw = _make_thinkback_response(step_count=3)
        adapter = _make_adapter(raw)
        report = run_thinkback_from_outcome(outcome, adapter=adapter)
        assert isinstance(report, ThinkbackReport)
