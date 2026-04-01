"""Tests for Phase 47: VerificationAgent — first-class verification agent."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from verification_agent import VerificationAgent, StepVerdict, ClaimContest, QualityVerdict


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


def _make_step_outcome(text: str, result: str, status: str = "done", index: int = 1):
    obj = MagicMock()
    obj.text = text
    obj.result = result
    obj.status = status
    obj.index = index
    return obj


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


# ---------------------------------------------------------------------------
# adversarial_pass
# ---------------------------------------------------------------------------

class TestAdversarialPass:
    def test_returns_contested_claims(self):
        claims_json = json.dumps([
            {"claim": "Caffeine improves cognition", "verdict": "CONTESTED",
             "reason": "Evidence is mixed — observational only"},
        ])
        adapter = _make_adapter(claims_json)
        va = VerificationAgent(adapter)
        claims = va.adversarial_pass("nootropic research", "some result text")
        assert len(claims) == 1
        assert claims[0].verdict == "CONTESTED"
        assert "Caffeine" in claims[0].claim

    def test_empty_list_no_contests(self):
        adapter = _make_adapter("[]")
        va = VerificationAgent(adapter)
        claims = va.adversarial_pass("goal", "solid result")
        assert claims == []

    def test_empty_result_returns_empty(self):
        adapter = _make_adapter("[]")
        va = VerificationAgent(adapter)
        claims = va.adversarial_pass("goal", "")
        assert claims == []
        adapter.complete.assert_not_called()

    def test_adapter_error_returns_empty(self):
        adapter = MagicMock()
        adapter.complete.side_effect = RuntimeError("error")
        va = VerificationAgent(adapter)
        claims = va.adversarial_pass("goal", "result")
        assert claims == []

    def test_multiple_claims_parsed(self):
        claims_json = json.dumps([
            {"claim": "Claim A", "verdict": "OVERCLAIMED", "reason": "reason A"},
            {"claim": "Claim B", "verdict": "CONFIRMED", "reason": "reason B"},
        ])
        adapter = _make_adapter(claims_json)
        va = VerificationAgent(adapter)
        claims = va.adversarial_pass("goal", "result")
        assert len(claims) == 2
        verdicts = {c.verdict for c in claims}
        assert "OVERCLAIMED" in verdicts
        assert "CONFIRMED" in verdicts


# ---------------------------------------------------------------------------
# quality_review
# ---------------------------------------------------------------------------

class TestQualityReview:
    def _make_multi_adapter(self, responses: list) -> MagicMock:
        """Adapter that returns responses in sequence."""
        resps = []
        for content in responses:
            r = MagicMock()
            r.content = content
            r.input_tokens = 5
            r.output_tokens = 5
            resps.append(r)
        adapter = MagicMock()
        adapter.complete.side_effect = resps
        return adapter

    def test_pass_verdict(self):
        quality_json = '{"verdict": "PASS", "reason": "thorough", "confidence": 0.85}'
        adapter = self._make_multi_adapter([quality_json, "[]"])
        va = VerificationAgent(adapter)
        outcomes = [_make_step_outcome("step 1", "good result")]
        verdict = va.quality_review("research nootropics", outcomes)
        assert verdict.verdict == "PASS"
        assert verdict.escalate is False
        assert verdict.confidence == 0.85

    def test_escalate_verdict(self):
        quality_json = '{"verdict": "ESCALATE", "reason": "too shallow", "confidence": 0.9}'
        adapter = self._make_multi_adapter([quality_json, "[]"])
        va = VerificationAgent(adapter)
        outcomes = [_make_step_outcome("step 1", "shallow result")]
        verdict = va.quality_review("research nootropics", outcomes)
        assert verdict.verdict == "ESCALATE"
        assert verdict.escalate is True

    def test_no_done_steps_returns_pass(self):
        adapter = _make_adapter("")
        va = VerificationAgent(adapter)
        outcomes = [_make_step_outcome("step 1", "blocked", status="blocked")]
        verdict = va.quality_review("goal", outcomes)
        assert verdict.verdict == "PASS"
        adapter.complete.assert_not_called()

    def test_adversarial_pass_included(self):
        quality_json = '{"verdict": "PASS", "reason": "ok", "confidence": 0.8}'
        claims_json = json.dumps([
            {"claim": "Contested claim", "verdict": "CONTESTED", "reason": "weak evidence"},
        ])
        adapter = self._make_multi_adapter([quality_json, claims_json])
        va = VerificationAgent(adapter)
        outcomes = [_make_step_outcome("step", "result with contested claim")]
        verdict = va.quality_review("goal", outcomes, run_adversarial=True)
        assert len(verdict.contested_claims) == 1
        assert verdict.contested_claims[0].verdict == "CONTESTED"

    def test_adversarial_skipped_when_disabled(self):
        quality_json = '{"verdict": "PASS", "reason": "ok", "confidence": 0.8}'
        adapter = _make_adapter(quality_json)
        va = VerificationAgent(adapter)
        outcomes = [_make_step_outcome("step", "result")]
        verdict = va.quality_review("goal", outcomes, run_adversarial=False)
        assert verdict.contested_claims == []
        assert adapter.complete.call_count == 1  # only 1 pass

    def test_adapter_error_returns_pass(self):
        adapter = MagicMock()
        adapter.complete.side_effect = RuntimeError("error")
        va = VerificationAgent(adapter)
        outcomes = [_make_step_outcome("step", "result")]
        verdict = va.quality_review("goal", outcomes)
        assert verdict.verdict == "PASS"
        assert verdict.escalate is False

    def test_contested_summary_format(self):
        verdict = QualityVerdict(
            verdict="PASS", reason="ok", confidence=0.8, escalate=False,
            contested_claims=[
                ClaimContest("Claim A", "CONTESTED", "reason A"),
            ],
        )
        summary = verdict.contested_summary()
        assert "[CONTESTED]" in summary
        assert "Claim A" in summary
        assert "Verification notes" in summary

    def test_contested_summary_empty(self):
        verdict = QualityVerdict(
            verdict="PASS", reason="ok", confidence=0.8, escalate=False,
            contested_claims=[],
        )
        assert verdict.contested_summary() == ""
