"""Tests for cross_ref.py — second-source fact verification."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from cross_ref import (
    ClaimVerification,
    CrossRefReport,
    _extract_json,
    cross_ref_annotation,
    extract_verifiable_claims,
    run_cross_ref,
    verify_single_claim,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_adapter(response_text: str):
    resp = MagicMock()
    resp.content = response_text
    resp.tool_calls = []
    resp.input_tokens = 100
    resp.output_tokens = 50
    adapter = MagicMock()
    adapter.complete = MagicMock(return_value=resp)
    return adapter


def _claims_response(claims):
    return json.dumps({"claims": claims})


def _verify_response(status="confirmed", confidence=0.9, note="Looks correct."):
    return json.dumps({"status": status, "confidence": confidence, "note": note})


# ---------------------------------------------------------------------------
# _extract_json
# ---------------------------------------------------------------------------

class TestExtractJson:
    def test_simple_json(self):
        result = _extract_json('{"key": "value"}')
        assert result == {"key": "value"}

    def test_json_with_prefix(self):
        result = _extract_json('Here is my answer: {"key": "value"}')
        assert result == {"key": "value"}

    def test_no_json(self):
        result = _extract_json("No JSON here")
        assert result == {}

    def test_invalid_json(self):
        result = _extract_json("{bad json}")
        assert result == {}

    def test_nested_json(self):
        result = _extract_json('{"outer": {"inner": 1}}')
        assert result["outer"] == {"inner": 1}


# ---------------------------------------------------------------------------
# ClaimVerification
# ---------------------------------------------------------------------------

class TestClaimVerification:
    def test_confirmed(self):
        cv = ClaimVerification(
            claim="Lions sleep 18-20 hours/day.",
            category="statistic",
            status="confirmed",
            confidence=0.9,
            note="Well-established fact.",
        )
        assert cv.status == "confirmed"
        assert cv.confidence == 0.9

    def test_disputed(self):
        cv = ClaimVerification(
            claim="Coffee causes cancer.",
            category="mechanism",
            status="disputed",
            confidence=0.8,
            note="Evidence does not support this.",
        )
        assert cv.status == "disputed"

    def test_default_elapsed(self):
        cv = ClaimVerification("claim", "statistic", "unknown", 0.5, "note")
        assert cv.elapsed_ms == 0


# ---------------------------------------------------------------------------
# CrossRefReport
# ---------------------------------------------------------------------------

class TestCrossRefReport:
    def _make_report(self, disputes=None):
        verified = [
            ClaimVerification("claim A", "statistic", "confirmed", 0.9, "ok"),
            ClaimVerification("claim B", "mechanism", "unknown", 0.4, "?"),
        ]
        if disputes:
            verified.extend(disputes)
        return CrossRefReport(
            verified=verified,
            claims_extracted=len(verified),
            claims_checked=len(verified),
            disputes=disputes or [],
            elapsed_ms=100,
        )

    def test_no_disputes(self):
        r = self._make_report()
        assert not r.has_disputes

    def test_has_disputes(self):
        d = [ClaimVerification("bad claim", "statistic", "disputed", 0.9, "wrong")]
        r = self._make_report(disputes=d)
        assert r.has_disputes

    def test_dispute_summary_empty(self):
        r = self._make_report()
        assert "no disputes" in r.dispute_summary()

    def test_dispute_summary_shows_claim(self):
        d = [ClaimVerification("bad claim is here", "statistic", "disputed", 0.9, "wrong")]
        r = self._make_report(disputes=d)
        s = r.dispute_summary()
        assert "bad claim is here" in s
        assert "DISPUTED" in s

    def test_full_summary_no_disputes(self):
        r = self._make_report()
        s = r.full_summary()
        assert "confirmed" in s.lower()

    def test_full_summary_with_disputes(self):
        d = [ClaimVerification("bad claim", "statistic", "disputed", 0.85, "wrong")]
        r = self._make_report(disputes=d)
        s = r.full_summary()
        assert "disputed" in s.lower()


# ---------------------------------------------------------------------------
# extract_verifiable_claims
# ---------------------------------------------------------------------------

class TestExtractVerifiableClaims:
    def test_returns_list(self):
        claims = [
            {"claim": "X causes Y.", "category": "mechanism", "confidence_in_text": "high"},
            {"claim": "N=500 participants.", "category": "statistic", "confidence_in_text": "high"},
        ]
        adapter = _make_adapter(_claims_response(claims))
        result = extract_verifiable_claims("Some research text", adapter)
        assert len(result) == 2
        assert result[0]["claim"] == "X causes Y."

    def test_empty_text_returns_empty(self):
        adapter = _make_adapter(_claims_response([]))
        result = extract_verifiable_claims("", adapter)
        assert result == []

    def test_adapter_exception_returns_empty(self):
        adapter = MagicMock()
        adapter.complete = MagicMock(side_effect=RuntimeError("API error"))
        result = extract_verifiable_claims("Some text", adapter)
        assert result == []

    def test_max_claims_capped(self):
        claims = [
            {"claim": f"Claim {i}.", "category": "statistic", "confidence_in_text": "high"}
            for i in range(10)
        ]
        adapter = _make_adapter(_claims_response(claims))
        result = extract_verifiable_claims("text", adapter, max_claims=3)
        assert len(result) <= 3

    def test_invalid_json_returns_empty(self):
        adapter = _make_adapter("not json at all")
        result = extract_verifiable_claims("text", adapter)
        assert result == []

    def test_missing_claim_key_skipped(self):
        # One claim missing "claim" key
        claims = [
            {"category": "statistic"},  # no "claim"
            {"claim": "Valid claim.", "category": "statistic", "confidence_in_text": "high"},
        ]
        adapter = _make_adapter(_claims_response(claims))
        result = extract_verifiable_claims("text", adapter)
        # Only the valid one
        assert len(result) == 1
        assert result[0]["claim"] == "Valid claim."


# ---------------------------------------------------------------------------
# verify_single_claim
# ---------------------------------------------------------------------------

class TestVerifySingleClaim:
    def test_confirmed(self):
        adapter = _make_adapter(_verify_response("confirmed", 0.9, "correct"))
        result = verify_single_claim("Lions sleep 20h/day.", "statistic", adapter)
        assert result.status == "confirmed"
        assert result.confidence == 0.9
        assert result.note == "correct"

    def test_disputed(self):
        adapter = _make_adapter(_verify_response("disputed", 0.85, "wrong"))
        result = verify_single_claim("Coffee causes cancer.", "mechanism", adapter)
        assert result.status == "disputed"

    def test_unknown(self):
        adapter = _make_adapter(_verify_response("unknown", 0.3, "can't assess"))
        result = verify_single_claim("Obscure claim.", "historical", adapter)
        assert result.status == "unknown"

    def test_adapter_exception_returns_unknown(self):
        adapter = MagicMock()
        adapter.complete = MagicMock(side_effect=RuntimeError("API error"))
        result = verify_single_claim("Any claim.", "statistic", adapter)
        assert result.status == "unknown"
        assert result.confidence == 0.0

    def test_elapsed_ms_set(self):
        adapter = _make_adapter(_verify_response())
        result = verify_single_claim("Claim.", "statistic", adapter)
        assert result.elapsed_ms >= 0

    def test_preserves_claim_and_category(self):
        adapter = _make_adapter(_verify_response())
        result = verify_single_claim("My claim.", "comparison", adapter)
        assert result.claim == "My claim."
        assert result.category == "comparison"


# ---------------------------------------------------------------------------
# run_cross_ref
# ---------------------------------------------------------------------------

class TestRunCrossRef:
    def test_dry_run_returns_empty_report(self):
        report = run_cross_ref("Some text", dry_run=True)
        assert isinstance(report, CrossRefReport)
        assert report.claims_extracted == 0
        assert not report.has_disputes

    def test_empty_text_returns_empty_report(self):
        adapter = _make_adapter(_claims_response([]))
        report = run_cross_ref("", adapter=adapter)
        assert report.claims_extracted == 0

    def test_whitespace_only_returns_empty(self):
        report = run_cross_ref("   \n   ", dry_run=True)
        assert report.claims_extracted == 0

    def test_no_claims_extracted_no_verification(self):
        """When extraction returns empty, verification is skipped."""
        adapter = _make_adapter(_claims_response([]))
        report = run_cross_ref("General knowledge text", adapter=adapter)
        assert report.claims_checked == 0
        assert not report.has_disputes

    def test_confirmed_claims_no_dispute(self):
        """All confirmed claims → no disputes."""
        claims = [{"claim": "Claim A.", "category": "statistic", "confidence_in_text": "high"}]
        # First call returns claims, second returns confirmed
        call_count = [0]

        def side_effect(messages, tools=[]):
            resp = MagicMock()
            resp.tool_calls = []
            resp.input_tokens = 50
            resp.output_tokens = 20
            if call_count[0] == 0:
                resp.content = _claims_response(claims)
            else:
                resp.content = _verify_response("confirmed", 0.9, "correct")
            call_count[0] += 1
            return resp

        adapter = MagicMock()
        adapter.complete = MagicMock(side_effect=side_effect)
        report = run_cross_ref("Research text.", adapter=adapter)
        assert report.claims_extracted == 1
        assert report.claims_checked == 1
        assert not report.has_disputes

    def test_disputed_claim_creates_dispute(self):
        """Disputed claim with high confidence → dispute recorded."""
        claims = [{"claim": "Bad claim.", "category": "statistic", "confidence_in_text": "high"}]
        call_count = [0]

        def side_effect(messages, tools=[]):
            resp = MagicMock()
            resp.tool_calls = []
            resp.input_tokens = 50
            resp.output_tokens = 20
            if call_count[0] == 0:
                resp.content = _claims_response(claims)
            else:
                resp.content = _verify_response("disputed", 0.9, "this is wrong")
            call_count[0] += 1
            return resp

        adapter = MagicMock()
        adapter.complete = MagicMock(side_effect=side_effect)
        report = run_cross_ref("Text with bad claim.", adapter=adapter)
        assert report.has_disputes
        assert len(report.disputes) == 1
        assert report.disputes[0].claim == "Bad claim."

    def test_disputed_low_confidence_not_dispute(self):
        """Disputed claim with low confidence (below threshold) → not flagged."""
        claims = [{"claim": "Uncertain claim.", "category": "statistic", "confidence_in_text": "high"}]
        call_count = [0]

        def side_effect(messages, tools=[]):
            resp = MagicMock()
            resp.tool_calls = []
            resp.input_tokens = 50
            resp.output_tokens = 20
            if call_count[0] == 0:
                resp.content = _claims_response(claims)
            else:
                resp.content = _verify_response("disputed", 0.3, "not sure")
            call_count[0] += 1
            return resp

        adapter = MagicMock()
        adapter.complete = MagicMock(side_effect=side_effect)
        report = run_cross_ref("text", adapter=adapter, dispute_threshold=0.6)
        # confidence=0.3 < threshold=0.6, so not a dispute
        assert not report.has_disputes

    def test_no_adapter_dry_run_returns_empty_report(self):
        """dry_run=True → empty report with no adapter needed."""
        report = run_cross_ref("text", dry_run=True)
        assert isinstance(report, CrossRefReport)
        assert report.claims_extracted == 0

    def test_elapsed_ms_populated(self):
        adapter = _make_adapter(_claims_response([]))
        report = run_cross_ref("text", adapter=adapter)
        assert report.elapsed_ms >= 0


# ---------------------------------------------------------------------------
# cross_ref_annotation
# ---------------------------------------------------------------------------

class TestCrossRefAnnotation:
    def test_empty_when_no_disputes(self):
        report = CrossRefReport(
            verified=[], claims_extracted=0, claims_checked=0, disputes=[], elapsed_ms=0
        )
        assert cross_ref_annotation(report) == ""

    def test_annotation_when_disputes(self):
        disputes = [ClaimVerification("bad claim", "statistic", "disputed", 0.9, "wrong")]
        report = CrossRefReport(
            verified=disputes, claims_extracted=1, claims_checked=1,
            disputes=disputes, elapsed_ms=50,
        )
        ann = cross_ref_annotation(report)
        assert ann.startswith("\n\n")
        assert "DISPUTED" in ann


# ---------------------------------------------------------------------------
# quality_gate integration: cross_ref field on QualityVerdict
# ---------------------------------------------------------------------------

class TestQualityGateCrossRefField:
    def test_verdict_has_cross_ref_field(self):
        from quality_gate import QualityVerdict
        v = QualityVerdict(
            verdict="PASS", reason="ok", confidence=0.9, escalate=False,
            cross_ref=None,
        )
        assert v.cross_ref is None

    def test_verdict_accepts_cross_ref_report(self):
        from quality_gate import QualityVerdict
        report = CrossRefReport(
            verified=[], claims_extracted=0, claims_checked=0, elapsed_ms=0
        )
        v = QualityVerdict(
            verdict="PASS", reason="ok", confidence=0.9, escalate=False,
            cross_ref=report,
        )
        assert isinstance(v.cross_ref, CrossRefReport)
