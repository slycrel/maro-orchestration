"""Tests for Phase 14: attribution.py — failure attribution."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from attribution import (
    Attribution,
    AttributionReport,
    attribute_batch,
    attribute_failure,
    load_attributions,
    save_attribution,
    _heuristic_failure_mode,
    _heuristic_failed_step,
)
from llm import LLMResponse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _stuck_outcome(
    goal="test goal",
    stuck_reason="stuck because things failed",
    status="stuck",
    outcome_id="oc001",
):
    return {
        "outcome_id": outcome_id,
        "goal": goal,
        "status": status,
        "stuck_reason": stuck_reason,
        "summary": "failed to complete",
    }


def _done_outcome(goal="test done goal"):
    return {
        "outcome_id": "oc_done",
        "goal": goal,
        "status": "done",
        "stuck_reason": "",
        "summary": "completed successfully",
    }


class _MockAdapter:
    """Returns valid attribution JSON."""

    def __init__(self, response_json=None):
        self._response_json = response_json or {
            "failed_step": "Step 2: call external API",
            "failure_mode": "tool_failure",
            "contributing_factors": ["network timeout", "missing credentials"],
            "confidence": 0.85,
        }

    def complete(self, messages, **kwargs):
        return LLMResponse(
            content=json.dumps(self._response_json),
            stop_reason="end_turn",
            input_tokens=50,
            output_tokens=40,
        )


class _BadJsonAdapter:
    """Returns garbage JSON."""

    def complete(self, messages, **kwargs):
        return LLMResponse(
            content="NOT_VALID_JSON {{broken}}",
            stop_reason="end_turn",
            input_tokens=10,
            output_tokens=5,
        )


class _FailingAdapter:
    """Always raises."""

    def complete(self, messages, **kwargs):
        raise RuntimeError("LLM unavailable")


# ---------------------------------------------------------------------------
# Heuristic failure mode detection
# ---------------------------------------------------------------------------

def test_heuristic_failure_mode_llm_error():
    """LLM-related keywords → llm_error."""
    mode, conf = _heuristic_failure_mode("LLM call failed: api timeout")
    assert mode == "llm_error"
    assert 0.0 <= conf <= 1.0


def test_heuristic_failure_mode_tool_failure():
    """Tool-related keywords → tool_failure."""
    mode, conf = _heuristic_failure_mode("tool call failed: subprocess returned error")
    assert mode == "tool_failure"
    assert 0.0 <= conf <= 1.0


def test_heuristic_failure_mode_bad_output():
    """Bad output keywords → bad_output."""
    mode, conf = _heuristic_failure_mode("cannot parse invalid JSON format response")
    assert mode == "bad_output"
    assert 0.0 <= conf <= 1.0


def test_heuristic_failure_mode_stuck_loop():
    """Stuck loop keywords → stuck_loop."""
    mode, conf = _heuristic_failure_mode("same step repeated 3 times, no progress")
    assert mode == "stuck_loop"
    assert 0.0 <= conf <= 1.0


def test_heuristic_failure_mode_unknown():
    """No matching keywords → unknown with low confidence."""
    mode, conf = _heuristic_failure_mode("zyzzyva frumious bandersnatch")
    assert mode == "unknown"
    assert conf < 0.4


# ---------------------------------------------------------------------------
# attribute_failure — heuristic paths
# ---------------------------------------------------------------------------

def test_attribute_failure_heuristic_stuck():
    """Stuck outcome → Attribution with failure_mode set."""
    outcome = _stuck_outcome(stuck_reason="LLM call failed repeatedly")
    attr = attribute_failure(outcome)
    assert isinstance(attr, Attribution)
    assert attr.failure_mode in ("tool_failure", "bad_output", "stuck_loop", "llm_error", "unknown")
    assert attr.session_id == "oc001"
    assert attr.goal == "test goal"
    assert isinstance(attr.confidence, float)
    assert 0.0 <= attr.confidence <= 1.0


def test_attribute_failure_heuristic_llm_error():
    """'LLM call failed' in stuck_reason → failure_mode=llm_error."""
    outcome = _stuck_outcome(stuck_reason="LLM call failed: api error")
    attr = attribute_failure(outcome)
    assert attr.failure_mode == "llm_error"


def test_attribute_failure_heuristic_returns_attribution():
    """attribute_failure returns an Attribution dataclass."""
    attr = attribute_failure(_stuck_outcome())
    assert isinstance(attr, Attribution)
    assert attr.raw_reason is not None


def test_attribute_failure_no_stuck_reason():
    """Empty stuck_reason → falls back gracefully."""
    outcome = _stuck_outcome(stuck_reason="")
    attr = attribute_failure(outcome)
    assert isinstance(attr, Attribution)
    assert attr.failure_mode == "unknown"


def test_attribute_failure_step_number_parsing():
    """Step number in stuck_reason gets extracted."""
    outcome = _stuck_outcome(stuck_reason="step 3: call api failed with 500")
    attr = attribute_failure(outcome)
    assert isinstance(attr, Attribution)
    assert attr.failed_step  # should have something


def test_attribute_failure_uses_outcome_id():
    """session_id comes from outcome_id."""
    outcome = _stuck_outcome(outcome_id="sess_xyz")
    attr = attribute_failure(outcome)
    assert attr.session_id == "sess_xyz"


def test_attribute_failure_goal_preserved():
    """Goal field is preserved in attribution."""
    outcome = _stuck_outcome(goal="deploy the widget")
    attr = attribute_failure(outcome)
    assert attr.goal == "deploy the widget"


# ---------------------------------------------------------------------------
# attribute_failure — with mock adapter
# ---------------------------------------------------------------------------

def test_attribute_failure_with_mock_adapter():
    """LLM returns valid JSON → Attribution uses LLM data."""
    outcome = _stuck_outcome()
    attr = attribute_failure(outcome, adapter=_MockAdapter())
    assert attr.failure_mode == "tool_failure"
    assert attr.confidence == 0.85
    assert "api" in attr.failed_step.lower()
    assert len(attr.contributing_factors) >= 1


def test_attribute_failure_bad_json():
    """LLM returns garbage → falls back to heuristic."""
    outcome = _stuck_outcome(stuck_reason="LLM call failed")
    attr = attribute_failure(outcome, adapter=_BadJsonAdapter())
    # Should still return a valid Attribution via heuristic
    assert isinstance(attr, Attribution)
    assert attr.failure_mode in ("tool_failure", "bad_output", "stuck_loop", "llm_error", "unknown")


def test_attribute_failure_adapter_raises():
    """Adapter raises → falls back to heuristic, no exception."""
    outcome = _stuck_outcome(stuck_reason="tool call failed")
    attr = attribute_failure(outcome, adapter=_FailingAdapter())
    assert isinstance(attr, Attribution)
    assert attr.failure_mode == "tool_failure"


def test_attribute_failure_confidence_clamped():
    """LLM returns out-of-range confidence → gets clamped to [0, 1]."""
    adapter = _MockAdapter({"failed_step": "step 1", "failure_mode": "unknown",
                            "contributing_factors": [], "confidence": 99.0})
    attr = attribute_failure(_stuck_outcome(), adapter=adapter)
    assert 0.0 <= attr.confidence <= 1.0


def test_attribute_failure_invalid_failure_mode():
    """LLM returns invalid failure_mode → replaced with 'unknown'."""
    adapter = _MockAdapter({"failed_step": "step 1", "failure_mode": "INVALID_MODE",
                            "contributing_factors": [], "confidence": 0.5})
    attr = attribute_failure(_stuck_outcome(), adapter=adapter)
    assert attr.failure_mode == "unknown"


# ---------------------------------------------------------------------------
# attribute_batch
# ---------------------------------------------------------------------------

def test_attribute_batch_filters_done_outcomes():
    """Only stuck/error outcomes get attributed — done outcomes skipped."""
    outcomes = [
        _stuck_outcome(outcome_id="s1"),
        _done_outcome(),
        _stuck_outcome(outcome_id="s2", stuck_reason="tool failed"),
    ]
    report = attribute_batch(outcomes)
    assert isinstance(report, AttributionReport)
    assert len(report.attributions) == 2
    for attr in report.attributions:
        assert attr.session_id in ("s1", "s2")


def test_attribute_batch_aggregates_modes():
    """most_common_failure_modes is populated from results."""
    outcomes = [
        _stuck_outcome(outcome_id=f"s{i}", stuck_reason="LLM api timeout error")
        for i in range(3)
    ]
    report = attribute_batch(outcomes)
    assert isinstance(report.most_common_failure_modes, list)
    assert len(report.most_common_failure_modes) >= 1


def test_attribute_batch_empty():
    """No stuck outcomes → empty report."""
    outcomes = [_done_outcome(), _done_outcome()]
    report = attribute_batch(outcomes)
    assert isinstance(report, AttributionReport)
    assert len(report.attributions) == 0
    assert report.most_common_failure_modes == []
    assert report.most_blamed_skills == []


def test_attribute_batch_empty_list():
    """Empty outcomes list → empty report."""
    report = attribute_batch([])
    assert isinstance(report, AttributionReport)
    assert len(report.attributions) == 0


def test_attribute_batch_returns_report():
    """Returns an AttributionReport."""
    report = attribute_batch([_stuck_outcome()])
    assert isinstance(report, AttributionReport)
    assert hasattr(report, "attributions")
    assert hasattr(report, "most_common_failure_modes")
    assert hasattr(report, "most_blamed_skills")
    assert hasattr(report, "timestamp")


# ---------------------------------------------------------------------------
# save_attribution / load_attributions — round-trip
# ---------------------------------------------------------------------------

def test_save_load_attribution(tmp_path):
    """Round-trip through attributions.jsonl."""
    with patch("attribution._attributions_path", return_value=tmp_path / "attributions.jsonl"):
        attr = Attribution(
            session_id="sess_001",
            goal="test goal",
            failed_step="Step 2: API call",
            failed_skill="research tool",
            failure_mode="tool_failure",
            contributing_factors=["missing token"],
            confidence=0.75,
            raw_reason="tool call failed",
        )
        save_attribution(attr)
        loaded = load_attributions(limit=10)
        assert len(loaded) == 1
        loaded_attr = loaded[0]
        assert loaded_attr.session_id == "sess_001"
        assert loaded_attr.goal == "test goal"
        assert loaded_attr.failure_mode == "tool_failure"
        assert loaded_attr.confidence == 0.75
        assert loaded_attr.failed_skill == "research tool"


def test_load_attributions_empty(tmp_path):
    """Non-existent file → []."""
    with patch("attribution._attributions_path", return_value=tmp_path / "no_file.jsonl"):
        result = load_attributions()
        assert result == []


def test_save_multiple_attributions(tmp_path):
    """Multiple attributions can be saved and loaded."""
    with patch("attribution._attributions_path", return_value=tmp_path / "attributions.jsonl"):
        for i in range(3):
            attr = Attribution(
                session_id=f"sess_{i:03d}",
                goal=f"goal {i}",
                failed_step=f"step {i}",
                failed_skill=None,
                failure_mode="unknown",
                contributing_factors=[],
                confidence=0.4,
                raw_reason="stuck",
            )
            save_attribution(attr)
        loaded = load_attributions(limit=10)
        assert len(loaded) == 3


# ---------------------------------------------------------------------------
# Attribution confidence range
# ---------------------------------------------------------------------------

def test_attribution_confidence_range():
    """Heuristic attribution confidence is always 0.0–1.0."""
    test_cases = [
        "LLM call failed with timeout",
        "tool subprocess returned error",
        "cannot parse JSON output",
        "repeated same step 5 times",
        "abcdefghijklmnopqrstuvwxyz",
    ]
    for reason in test_cases:
        attr = attribute_failure(_stuck_outcome(stuck_reason=reason))
        assert 0.0 <= attr.confidence <= 1.0, f"confidence={attr.confidence} out of range for reason={reason!r}"


def test_attribution_to_dict_round_trip():
    """to_dict/from_dict round-trip preserves all fields."""
    attr = Attribution(
        session_id="sess_abc",
        goal="some goal",
        failed_step="Step 1: failed",
        failed_skill="my skill",
        failure_mode="llm_error",
        contributing_factors=["factor A"],
        confidence=0.6,
        raw_reason="raw stuck reason",
    )
    d = attr.to_dict()
    restored = Attribution.from_dict(d)
    assert restored.session_id == attr.session_id
    assert restored.failure_mode == attr.failure_mode
    assert restored.confidence == attr.confidence
    assert restored.failed_skill == attr.failed_skill
