"""Tests for Phase 47: VerificationAgent — first-class verification agent."""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from verification_agent import VerificationAgent, StepVerdict


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_adapter(response_content: str) -> MagicMock:
    """Build a mock adapter that returns a fixed content string."""
    resp = MagicMock()
    resp.content = response_content
    resp.input_tokens = 10
    resp.output_tokens = 10
    adapter = MagicMock()
    adapter.complete.return_value = resp
    return adapter


# ---------------------------------------------------------------------------
# verify_step
# ---------------------------------------------------------------------------

class TestVerifyStep:
    def test_pass_verdict(self):
        adapter = _make_adapter('{"verdict": "PASS", "reason": "complete", "confidence": 0.9}')
        va = VerificationAgent(adapter)
        result = va.verify_step("fetch market data", "Fetched 100 records from API")
        assert result.passed is True
        assert result.confidence == 0.9
        assert result.reason == "complete"

    def test_retry_verdict_above_threshold(self):
        adapter = _make_adapter('{"verdict": "RETRY", "reason": "too vague", "confidence": 0.9}')
        va = VerificationAgent(adapter)
        result = va.verify_step("fetch market data", "I would fetch the data by calling the API")
        assert result.passed is False
        assert "vague" in result.reason

    def test_retry_below_threshold_passes(self):
        # Low-confidence RETRY → passes anyway (threshold 0.75, confidence 0.4)
        adapter = _make_adapter('{"verdict": "RETRY", "reason": "uncertain", "confidence": 0.4}')
        va = VerificationAgent(adapter)
        result = va.verify_step("fetch market data", "some result")
        assert result.passed is True

    def test_empty_result_fails(self):
        adapter = _make_adapter("")
        va = VerificationAgent(adapter)
        result = va.verify_step("fetch market data", "")
        assert result.passed is False
        assert result.reason == "empty result"
        adapter.complete.assert_not_called()

    def test_adapter_error_returns_pass(self):
        adapter = MagicMock()
        adapter.complete.side_effect = RuntimeError("network error")
        va = VerificationAgent(adapter)
        result = va.verify_step("some step", "some result")
        assert result.passed is True
        assert result.confidence == 0.0

    def test_non_string_result_coerced(self):
        adapter = _make_adapter('{"verdict": "PASS", "reason": "ok", "confidence": 0.8}')
        va = VerificationAgent(adapter)
        result = va.verify_step("step", {"key": "value"})
        assert result.passed is True

    def test_custom_confidence_threshold(self):
        adapter = _make_adapter('{"verdict": "RETRY", "reason": "poor", "confidence": 0.5}')
        va = VerificationAgent(adapter, confidence_threshold=0.4)
        result = va.verify_step("step", "weak result")
        # confidence 0.5 >= threshold 0.4, so should NOT pass
        assert result.passed is False

    def test_malformed_json_returns_pass(self):
        adapter = _make_adapter("not json at all")
        va = VerificationAgent(adapter)
        result = va.verify_step("step", "result")
        assert result.passed is True


# NOTE: TestAdversarialPass, TestAdversarialGrounding, and TestQualityReview
# tested VerificationAgent.adversarial_pass/quality_review, deleted 2026-07-02
# as dead code superseded by quality_gate.py (Tier 1 refactor-plan cleanup).
# Adversarial-claim-grounding coverage (probe_contested_claims,
# DISMISSED_BY_PROBE) lives on in tests/test_quality_gate.py's
# TestProbeContestedClaims and tests/test_factory_thin_adversarial.py, which
# own that logic now.

# ---------------------------------------------------------------------------
# Input window (max_input_chars) — paid default vs larger free-local window
# ---------------------------------------------------------------------------

class TestInputWindow:
    def _user_msg_len(self, adapter) -> int:
        # messages = first positional arg to complete()
        messages = adapter.complete.call_args.args[0]
        return len(messages[1].content)

    def test_default_clips_to_paid_window(self):
        adapter = _make_adapter('{"verdict":"PASS","reason":"ok","confidence":0.9}')
        big = "X" * 5000
        VerificationAgent(adapter).verify_step("goal", big)
        # default 1200-char window → user msg holds ~1200 of the result, not 5000
        assert self._user_msg_len(adapter) < 1600

    def test_larger_window_passes_more_result(self):
        adapter = _make_adapter('{"verdict":"PASS","reason":"ok","confidence":0.9}')
        big = "X" * 5000
        VerificationAgent(adapter, max_input_chars=4000).verify_step("goal", big)
        # 4000-char window → far more of the result reaches the validator
        assert self._user_msg_len(adapter) > 3900
