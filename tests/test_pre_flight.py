"""Tests for pre_flight.py — plan review before execution."""

import json
import sys
import types
import pytest
from unittest.mock import MagicMock, patch


def _make_adapter(response_json: dict):
    """Build a mock adapter that returns a given JSON response."""
    adapter = MagicMock()
    resp = MagicMock()
    resp.content = json.dumps(response_json)
    adapter.complete.return_value = resp
    return adapter


def _patch_reviewer(response_json: dict):
    """Context manager: patch pre_flight's internal build_adapter to return a mock."""
    mock_adapter = _make_adapter(response_json)
    return patch("pre_flight.build_adapter", return_value=mock_adapter)


def _wide_response():
    return {
        "scope": "wide",
        "scope_note": "codebase review cannot fit in 8 steps",
        "assumptions": [
            {"step": 1, "issue": "assumes repo is already cloned"},
            {"step": 3, "issue": "assumes test suite passes before analysis"},
        ],
        "milestone_candidates": [
            {"step": 5, "reason": "reading all of src/ is a sub-project not a step"},
        ],
        "unknown_unknowns": [
            "file sizes unknown — could be much larger than estimated",
        ],
    }


def _narrow_response():
    return {
        "scope": "narrow",
        "scope_note": "simple 3-step lookup, no hidden depth",
        "assumptions": [],
        "milestone_candidates": [],
        "unknown_unknowns": [],
    }


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

sys.path.insert(0, "src")
from pre_flight import review_plan, PlanReview, PlanFlag


# ---------------------------------------------------------------------------
# Basic review parsing
# ---------------------------------------------------------------------------

class TestReviewPlan:
    def test_wide_scope_parsed(self):
        steps = ["Clone repo", "Run tests", "Read src/", "Analyze findings", "Write report"]
        with _patch_reviewer(_wide_response()):
            review = review_plan("Review the codebase", steps, MagicMock())
        assert review.scope == "wide"
        assert "8 steps" in review.scope_note
        assert len(review.flags) >= 3
        assert 5 in review.milestone_step_indices

    def test_narrow_scope_parsed(self):
        steps = ["Read config.py", "Check value", "Report result"]
        with _patch_reviewer(_narrow_response()):
            review = review_plan("Find the timeout setting", steps, MagicMock())
        assert review.scope == "narrow"
        assert review.flags == []
        assert review.milestone_step_indices == []

    def test_assumption_flags_created(self):
        steps = ["step 1", "step 2", "step 3", "step 4", "step 5"]
        with _patch_reviewer(_wide_response()):
            review = review_plan("goal", steps, MagicMock())
        assumption_flags = [f for f in review.flags if f.kind == "assumption"]
        assert len(assumption_flags) == 2
        assert assumption_flags[0].step == 1
        assert assumption_flags[0].severity == "warn"

    def test_milestone_flags_created(self):
        steps = ["s1", "s2", "s3", "s4", "s5"]
        with _patch_reviewer(_wide_response()):
            review = review_plan("goal", steps, MagicMock())
        milestone_flags = [f for f in review.flags if f.kind == "milestone"]
        assert len(milestone_flags) == 1
        assert milestone_flags[0].step == 5

    def test_unknown_flags_created(self):
        steps = ["s1", "s2"]
        with _patch_reviewer(_wide_response()):
            review = review_plan("goal", steps, MagicMock())
        unknown_flags = [f for f in review.flags if f.kind == "unknown"]
        assert len(unknown_flags) == 1
        assert unknown_flags[0].severity == "info"

    def test_strips_markdown_fences(self):
        mock_adapter = MagicMock()
        resp = MagicMock()
        resp.content = "```json\n" + json.dumps(_narrow_response()) + "\n```"
        mock_adapter.complete.return_value = resp
        with patch("pre_flight.build_adapter", return_value=mock_adapter):
            review = review_plan("goal", ["step 1"], MagicMock())
        assert review.scope == "narrow"

    def test_empty_steps_returns_unknown(self):
        with _patch_reviewer(_narrow_response()):
            review = review_plan("goal", [], MagicMock())
        assert review.scope == "unknown"
        assert "no steps" in review.scope_note

    def test_adapter_failure_returns_heuristic(self):
        """When no API adapter is available, fall back to heuristic scope estimate."""
        with patch("pre_flight.build_adapter", side_effect=RuntimeError("no adapter")):
            review = review_plan("goal", ["step 1"], MagicMock())
        assert review.scope in ("narrow", "medium", "wide")
        assert "heuristic" in review.scope_note

    def test_malformed_json_returns_unknown(self):
        mock_adapter = MagicMock()
        resp = MagicMock()
        resp.content = "not json at all"
        mock_adapter.complete.return_value = resp
        with patch("pre_flight.build_adapter", return_value=mock_adapter):
            review = review_plan("goal", ["step 1"], MagicMock())
        assert review.scope == "unknown"


# ---------------------------------------------------------------------------
# PlanReview helpers
# ---------------------------------------------------------------------------

class TestPlanReview:
    def test_has_concerns_wide_scope(self):
        r = PlanReview(scope="wide", scope_note="too big")
        assert r.has_concerns is True

    def test_has_concerns_warn_flag(self):
        r = PlanReview(scope="narrow", scope_note="ok",
                       flags=[PlanFlag(kind="assumption", step=1,
                                       message="bad assumption", severity="warn")])
        assert r.has_concerns is True

    def test_no_concerns_narrow_clean(self):
        r = PlanReview(scope="narrow", scope_note="ok")
        assert r.has_concerns is False

    def test_summary_includes_scope(self):
        r = PlanReview(scope="medium", scope_note="ok")
        assert "scope=medium" in r.summary()

    def test_summary_includes_milestones(self):
        r = PlanReview(scope="wide", scope_note="x", milestone_step_indices=[3, 7])
        assert "milestone_candidates=[3, 7]" in r.summary()

    def test_format_for_log_has_flags(self):
        r = PlanReview(
            scope="wide",
            scope_note="too large",
            flags=[PlanFlag(kind="assumption", step=2, message="bad", severity="warn")],
        )
        log_str = r.format_for_log()
        assert "assumption" in log_str
        assert "step 2" in log_str
        assert "bad" in log_str

    def test_format_for_log_whole_plan_flag(self):
        r = PlanReview(
            scope="medium",
            scope_note="ok",
            flags=[PlanFlag(kind="unknown", step=0, message="hidden dep", severity="info")],
        )
        log_str = r.format_for_log()
        assert "plan" in log_str


# ---------------------------------------------------------------------------
# Verbose output (smoke)
# ---------------------------------------------------------------------------

class TestVerboseOutput:
    def test_verbose_wide_prints_warning(self, capsys):
        steps = ["s1", "s2", "s3", "s4", "s5"]
        with _patch_reviewer(_wide_response()):
            review_plan("goal", steps, MagicMock(), verbose=True)
        captured = capsys.readouterr()
        assert "wide" in captured.err or "wide" in captured.out

    def test_verbose_narrow_no_warning(self, capsys):
        steps = ["s1"]
        with _patch_reviewer(_narrow_response()):
            review_plan("goal", steps, MagicMock(), verbose=True)
        captured = capsys.readouterr()
        assert "WARNING" not in captured.err


# NOTE: TestMultiLensReview tested pre_flight.multi_lens_review, deleted
# 2026-07-02 as unwired dead code (Tier 1 refactor-plan cleanup) — see
# docs/REFACTOR_PLAN.md.

# ---------------------------------------------------------------------------
# preflight_calibration_stats
# ---------------------------------------------------------------------------

from pre_flight import preflight_calibration_stats


class TestPreflightCalibrationStats:
    def test_no_file_returns_zero_total(self, tmp_path):
        result = preflight_calibration_stats(cal_path=tmp_path / "nonexistent.jsonl")
        assert result["total"] == 0

    def test_empty_file_returns_zero_total(self, tmp_path):
        cal = tmp_path / "preflight_calibration.jsonl"
        cal.write_text("")
        result = preflight_calibration_stats(cal_path=cal)
        assert result["total"] == 0

    def test_single_true_positive_entry(self, tmp_path):
        cal = tmp_path / "preflight_calibration.jsonl"
        entry = {
            "ts": "2026-04-06T00:00:00Z",
            "scope_predicted": "wide",
            "actual_status": "stuck",
            "true_positive": True,
            "false_positive": False,
            "false_negative": False,
            "true_negative": False,
        }
        cal.write_text(json.dumps(entry) + "\n")
        result = preflight_calibration_stats(cal_path=cal)
        assert result["total"] == 1
        assert result["true_positive"] == 1
        assert result["false_positive"] == 0
        assert result["precision"] == 1.0
        assert result["recall"] == 1.0

    def test_false_positive_classification(self, tmp_path):
        """scope=wide + actual done = false positive."""
        cal = tmp_path / "preflight_calibration.jsonl"
        entry = {
            "ts": "2026-04-06T00:00:00Z",
            "scope_predicted": "wide",
            "actual_status": "done",
            "true_positive": False,
            "false_positive": True,
            "false_negative": False,
            "true_negative": False,
        }
        cal.write_text(json.dumps(entry) + "\n")
        result = preflight_calibration_stats(cal_path=cal)
        assert result["false_positive"] == 1
        assert result["precision"] == 0.0  # tp=0, fp=1

    def test_scope_breakdown_populated(self, tmp_path):
        cal = tmp_path / "preflight_calibration.jsonl"
        entries = [
            {"scope_predicted": "wide", "actual_status": "stuck",
             "true_positive": True, "false_positive": False,
             "false_negative": False, "true_negative": False},
            {"scope_predicted": "narrow", "actual_status": "done",
             "true_positive": False, "false_positive": False,
             "false_negative": False, "true_negative": True},
        ]
        cal.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
        result = preflight_calibration_stats(cal_path=cal)
        assert result["total"] == 2
        assert "wide" in result["scope_breakdown"]
        assert "narrow" in result["scope_breakdown"]
        assert result["scope_breakdown"]["wide"]["stuck"] == 1
        assert result["scope_breakdown"]["narrow"]["done"] == 1

    def test_skips_malformed_lines(self, tmp_path):
        """Malformed JSON lines are skipped gracefully."""
        cal = tmp_path / "preflight_calibration.jsonl"
        good_entry = json.dumps({
            "scope_predicted": "medium", "actual_status": "done",
            "true_positive": False, "false_positive": False,
            "false_negative": False, "true_negative": True,
        })
        cal.write_text("not-json\n" + good_entry + "\n{broken\n")
        result = preflight_calibration_stats(cal_path=cal)
        assert result["total"] == 1  # only the valid entry
