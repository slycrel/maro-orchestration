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

    def test_adapter_failure_returns_unknown(self):
        with patch("pre_flight.build_adapter", side_effect=RuntimeError("no adapter")):
            review = review_plan("goal", ["step 1"], MagicMock())
        assert review.scope == "unknown"

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


# ---------------------------------------------------------------------------
# Multi-lens review
# ---------------------------------------------------------------------------

from pre_flight import multi_lens_review


def _make_multi_lens_adapter(responses: list):
    """Build a mock adapter that returns scripted responses in sequence."""
    adapter = MagicMock()
    responses_iter = iter(responses)

    def _complete(*a, **kw):
        resp = MagicMock()
        resp.content = next(responses_iter, responses[-1])
        return resp

    adapter.complete.side_effect = _complete
    return adapter


class TestMultiLensReview:
    def test_returns_plan_review(self):
        """multi_lens_review returns a PlanReview."""
        import json
        adapter = _make_multi_lens_adapter([
            json.dumps({"scope": "medium", "note": "ok", "compressed_steps": []}),
            json.dumps({"dependency_risks": []}),
            json.dumps({"critical_assumptions": []}),
        ])
        with patch("pre_flight.build_adapter", return_value=adapter):
            review = multi_lens_review("goal", ["step 1", "step 2"], MagicMock())
        assert review.scope == "medium"

    def test_wide_scope_from_lens1(self):
        import json
        adapter = _make_multi_lens_adapter([
            json.dumps({"scope": "wide", "note": "hidden depth", "compressed_steps": [2]}),
            json.dumps({"dependency_risks": []}),
            json.dumps({"critical_assumptions": []}),
        ])
        with patch("pre_flight.build_adapter", return_value=adapter):
            review = multi_lens_review("goal", ["step 1", "step 2"], MagicMock())
        assert review.scope == "wide"
        assert 2 in review.milestone_step_indices

    def test_dependency_flags_from_lens2(self):
        import json
        adapter = _make_multi_lens_adapter([
            json.dumps({"scope": "medium", "note": "ok", "compressed_steps": []}),
            json.dumps({"dependency_risks": [{"step": 3, "missing": "repo not cloned"}]}),
            json.dumps({"critical_assumptions": []}),
        ])
        with patch("pre_flight.build_adapter", return_value=adapter):
            review = multi_lens_review("goal", ["s1", "s2", "s3"], MagicMock())
        dep_flags = [f for f in review.flags if "hidden dep" in f.message]
        assert len(dep_flags) >= 1

    def test_assumption_flags_from_lens3(self):
        import json
        adapter = _make_multi_lens_adapter([
            json.dumps({"scope": "medium", "note": "ok", "compressed_steps": []}),
            json.dumps({"dependency_risks": []}),
            json.dumps({"critical_assumptions": [{"step": 1, "assumption": "API key available"}]}),
        ])
        with patch("pre_flight.build_adapter", return_value=adapter):
            review = multi_lens_review("goal", ["s1"], MagicMock())
        assumption_flags = [f for f in review.flags if f.kind == "assumption"]
        assert len(assumption_flags) >= 1

    def test_empty_steps_returns_unknown(self):
        review = multi_lens_review("goal", [], MagicMock())
        assert review.scope == "unknown"

    def test_partial_lens_failure_degrades_gracefully(self):
        """If lens 2 fails, lenses 1 and 3 still contribute."""
        import json
        call_count = [0]

        def _side_effect(*a, **kw):
            call_count[0] += 1
            resp = MagicMock()
            if call_count[0] == 1:
                resp.content = json.dumps({"scope": "narrow", "note": "ok", "compressed_steps": []})
            elif call_count[0] == 2:
                raise RuntimeError("lens 2 failed")
            else:
                resp.content = json.dumps({"critical_assumptions": []})
            return resp

        adapter = MagicMock()
        adapter.complete.side_effect = _side_effect
        with patch("pre_flight.build_adapter", return_value=adapter):
            review = multi_lens_review("goal", ["s1"], MagicMock())
        # Should not raise, should return a valid PlanReview
        assert review.scope in ("narrow", "medium", "unknown")

    def test_has_concerns_when_flags_present(self):
        import json
        adapter = _make_multi_lens_adapter([
            json.dumps({"scope": "narrow", "note": "ok", "compressed_steps": []}),
            json.dumps({"dependency_risks": [{"step": 1, "missing": "auth token"}]}),
            json.dumps({"critical_assumptions": []}),
        ])
        with patch("pre_flight.build_adapter", return_value=adapter):
            review = multi_lens_review("goal", ["s1"], MagicMock())
        # has_concerns=True if scope=wide OR any warn flag
        if any(f.severity == "warn" for f in review.flags):
            assert review.has_concerns is True


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
