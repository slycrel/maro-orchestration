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
# adversarial_pass grounding — fabricated contestations are probed away
# (regression for the logged "Go not installed" / "branch X missing" fabrications)
# ---------------------------------------------------------------------------

class TestAdversarialGrounding:
    def test_fabricated_contestation_dismissed_by_probe(self):
        # Reviewer claims a tool is missing; its own probe exits 0 (`true`),
        # proving the contestation wrong → DISMISSED_BY_PROBE, not asserted.
        claims_json = json.dumps([
            {"claim": "Go is not installed on this machine", "verdict": "CONTESTED",
             "reason": "build would fail", "settled_by_command": "true"},
        ])
        va = VerificationAgent(_make_adapter(claims_json))
        claims = va.adversarial_pass("ship the Go service", "result mentioning go build")
        assert len(claims) == 1
        assert claims[0].verdict == "DISMISSED_BY_PROBE"
        assert claims[0].probe_status == "dismissed"

    def test_valid_contestation_survives_probe(self):
        # Probe exits non-zero (`false`) → reviewer was right, verdict stands.
        claims_json = json.dumps([
            {"claim": "config.yml does not exist", "verdict": "CONTESTED",
             "reason": "referenced but absent", "settled_by_command": "false"},
        ])
        va = VerificationAgent(_make_adapter(claims_json))
        claims = va.adversarial_pass("goal", "result")
        assert len(claims) == 1
        assert claims[0].verdict == "CONTESTED"
        assert claims[0].probe_status == "validated"

    def test_unprobeable_claim_left_alone(self):
        # No settled_by_command (subjective claim) → unprobed, verdict unchanged.
        claims_json = json.dumps([
            {"claim": "The tone is too informal", "verdict": "CONTESTED",
             "reason": "subjective", "settled_by_command": None},
        ])
        va = VerificationAgent(_make_adapter(claims_json))
        claims = va.adversarial_pass("goal", "result")
        assert len(claims) == 1
        assert claims[0].verdict == "CONTESTED"
        assert claims[0].probe_status == "unprobed"

    def test_dismissed_claim_excluded_from_summary(self):
        # A probe-dismissed fabrication must not reach user-facing verification notes.
        dismissed = ClaimContest("Go not installed", "DISMISSED_BY_PROBE", "wrong", "dismissed")
        real = ClaimContest("Overstated effect size", "OVERCLAIMED", "p>0.05", "unprobed")
        qv = QualityVerdict("PASS", "ok", 0.9, False, contested_claims=[dismissed, real])
        summary = qv.contested_summary()
        assert "Go not installed" not in summary
        assert "Overstated effect size" in summary


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
