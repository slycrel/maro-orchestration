"""Tests for quality_gate.py — LLM Council + quality gate integration."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from quality_gate import (
    CouncilCritique,
    CouncilVerdict,
    QualityVerdict,
    run_llm_council,
    run_quality_gate,
    next_model_tier,
    _COUNCIL_FRAMINGS,
    DebatePosition,
    DebateVerdict,
    run_debate,
    _BULL_SYSTEM,
    _BEAR_SYSTEM,
    _RISK_MANAGER_SYSTEM,
    _probe_contested_claims,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_step(status="done", text="do something", result="result text"):
    s = SimpleNamespace(status=status, text=text, result=result, index=1)
    return s


def _make_adapter(content: str):
    resp = SimpleNamespace(content=content, input_tokens=10, output_tokens=20)
    adapter = MagicMock()
    adapter.complete.return_value = resp
    return adapter


# ---------------------------------------------------------------------------
# CouncilCritique + CouncilVerdict
# ---------------------------------------------------------------------------

class TestCouncilDataclasses:
    def test_critique_fields(self):
        c = CouncilCritique(
            critic="devil_advocate",
            verdict="WEAK",
            concerns=["missing controls"],
            most_critical_gap="no comparison group",
        )
        assert c.critic == "devil_advocate"
        assert c.verdict == "WEAK"

    def test_verdict_escalate_on_two_weak(self):
        critiques = [
            CouncilCritique("devil_advocate", "WEAK", [], "gap 1"),
            CouncilCritique("domain_skeptic", "WEAK", [], "gap 2"),
            CouncilCritique("implementation_critic", "STRONG", [], ""),
        ]
        v = CouncilVerdict(critiques=critiques, weak_count=2, escalate=True)
        assert v.escalate is True
        assert v.weak_count == 2

    def test_verdict_no_escalate_on_one_weak(self):
        critiques = [
            CouncilCritique("devil_advocate", "WEAK", [], "gap"),
            CouncilCritique("domain_skeptic", "ACCEPTABLE", [], ""),
            CouncilCritique("implementation_critic", "STRONG", [], ""),
        ]
        v = CouncilVerdict(critiques=critiques, weak_count=1, escalate=False)
        assert v.escalate is False


# ---------------------------------------------------------------------------
# Council framings
# ---------------------------------------------------------------------------

class TestCouncilFramings:
    def test_three_framings_exist(self):
        assert len(_COUNCIL_FRAMINGS) == 3

    def test_framing_names(self):
        names = [f[0] for f in _COUNCIL_FRAMINGS]
        assert "devil_advocate" in names
        assert "domain_skeptic" in names
        assert "implementation_critic" in names

    def test_framing_prompts_not_empty(self):
        for name, prompt in _COUNCIL_FRAMINGS:
            assert len(prompt) > 50, f"{name} prompt too short"


# ---------------------------------------------------------------------------
# run_llm_council
# ---------------------------------------------------------------------------

class TestRunLLMCouncil:
    def test_no_adapter_returns_empty(self):
        steps = [_make_step()]
        v = run_llm_council("goal", steps, adapter=None)
        assert v.critiques == []
        assert v.escalate is False

    def test_no_done_steps_returns_empty(self):
        adapter = _make_adapter('{"verdict": "WEAK", "concerns": [], "most_critical_gap": "x"}')
        v = run_llm_council("goal", [], adapter=adapter)
        assert v.critiques == []

    def test_three_weak_escalates(self):
        adapter = _make_adapter('{"verdict": "WEAK", "concerns": ["issue"], "most_critical_gap": "gap"}')
        steps = [_make_step()]
        v = run_llm_council("research goal", steps, adapter=adapter)
        assert len(v.critiques) == 3
        assert v.weak_count == 3
        assert v.escalate is True

    def test_three_strong_no_escalate(self):
        adapter = _make_adapter('{"verdict": "STRONG", "concerns": [], "most_critical_gap": ""}')
        steps = [_make_step()]
        v = run_llm_council("research goal", steps, adapter=adapter)
        assert v.escalate is False
        assert v.weak_count == 0

    def test_two_weak_one_acceptable_escalates(self):
        responses = [
            '{"verdict": "WEAK", "concerns": ["a"], "most_critical_gap": "x"}',
            '{"verdict": "WEAK", "concerns": ["b"], "most_critical_gap": "y"}',
            '{"verdict": "ACCEPTABLE", "concerns": [], "most_critical_gap": ""}',
        ]
        adapter = MagicMock()
        adapter.complete.side_effect = [
            SimpleNamespace(content=r, input_tokens=5, output_tokens=10)
            for r in responses
        ]
        steps = [_make_step()]
        v = run_llm_council("goal", steps, adapter=adapter)
        assert v.weak_count == 2
        assert v.escalate is True

    def test_adapter_error_returns_empty(self):
        adapter = MagicMock()
        adapter.complete.side_effect = RuntimeError("network error")
        steps = [_make_step()]
        v = run_llm_council("goal", steps, adapter=adapter)
        assert v.escalate is False

    def test_bad_json_skips_critic(self):
        adapter = _make_adapter("not valid json at all")
        steps = [_make_step()]
        v = run_llm_council("goal", steps, adapter=adapter)
        # Bad JSON → no critiques parsed, no escalation
        assert v.escalate is False


# ---------------------------------------------------------------------------
# QualityVerdict council field
# ---------------------------------------------------------------------------

class TestQualityVerdictCouncilField:
    def test_council_defaults_to_none(self):
        v = QualityVerdict("PASS", "ok", 0.9, False)
        assert v.council is None

    def test_council_can_be_set(self):
        council = CouncilVerdict([], 0, False)
        v = QualityVerdict("PASS", "ok", 0.9, False, [], council)
        assert v.council is council


# ---------------------------------------------------------------------------
# run_quality_gate with run_council=True
# ---------------------------------------------------------------------------

class TestRunQualityGateWithCouncil:
    def _gate_pass_resp(self):
        return SimpleNamespace(
            content='{"verdict": "PASS", "reason": "solid", "confidence": 0.9}',
            input_tokens=10, output_tokens=20,
        )

    def _adv_resp(self):
        return SimpleNamespace(content="[]", input_tokens=5, output_tokens=5)

    def _council_weak_resp(self):
        return SimpleNamespace(
            content='{"verdict": "WEAK", "concerns": ["thin"], "most_critical_gap": "no data"}',
            input_tokens=5, output_tokens=10,
        )

    def test_council_escalates_pass(self):
        adapter = MagicMock()
        # Gate → PASS, adversarial → [], council → 3× WEAK
        adapter.complete.side_effect = [
            self._gate_pass_resp(),
            self._adv_resp(),
            self._council_weak_resp(),
            self._council_weak_resp(),
            self._council_weak_resp(),
        ]
        steps = [_make_step()]
        verdict = run_quality_gate("goal", steps, adapter=adapter, run_council=True)
        assert verdict.escalate is True
        assert verdict.council is not None
        assert verdict.council.weak_count == 3

    def test_run_council_false_skips_council(self):
        adapter = MagicMock()
        adapter.complete.side_effect = [
            self._gate_pass_resp(),
            self._adv_resp(),
        ]
        steps = [_make_step()]
        verdict = run_quality_gate("goal", steps, adapter=adapter, run_council=False)
        assert verdict.council is None
        assert adapter.complete.call_count == 2

    def test_council_strong_keeps_pass(self):
        adapter = MagicMock()
        adapter.complete.side_effect = [
            self._gate_pass_resp(),
            self._adv_resp(),
            SimpleNamespace(content='{"verdict": "STRONG", "concerns": [], "most_critical_gap": ""}',
                            input_tokens=5, output_tokens=5),
            SimpleNamespace(content='{"verdict": "STRONG", "concerns": [], "most_critical_gap": ""}',
                            input_tokens=5, output_tokens=5),
            SimpleNamespace(content='{"verdict": "ACCEPTABLE", "concerns": [], "most_critical_gap": ""}',
                            input_tokens=5, output_tokens=5),
        ]
        steps = [_make_step()]
        verdict = run_quality_gate("goal", steps, adapter=adapter, run_council=True)
        assert verdict.verdict == "PASS"
        assert verdict.escalate is False


# ---------------------------------------------------------------------------
# next_model_tier (unchanged, regression guard)
# ---------------------------------------------------------------------------

class TestNextModelTier:
    def test_cheap_to_mid(self):
        assert next_model_tier("cheap") == "mid"

    def test_mid_to_power(self):
        assert next_model_tier("mid") == "power"

    def test_power_is_top(self):
        assert next_model_tier("power") is None

    def test_unknown_returns_none(self):
        assert next_model_tier("gpt-4") is None


# ---------------------------------------------------------------------------
# Multi-agent debate: DebatePosition, DebateVerdict, run_debate
# ---------------------------------------------------------------------------

def _make_debate_adapter(bull_json: str, bear_json: str, rm_json: str):
    """Adapter that returns bull, bear, risk-manager responses in sequence."""
    responses = [bull_json, bear_json, rm_json]
    call_count = [0]

    def _complete(messages, **kw):
        idx = call_count[0]
        call_count[0] += 1
        resp = MagicMock()
        resp.content = responses[idx] if idx < len(responses) else '{"verdict":"PROCEED","reasoning":"ok","dominant_position":"neutral","key_risk":""}'
        resp.tool_calls = []
        resp.input_tokens = 80
        resp.output_tokens = 40
        return resp

    adapter = MagicMock()
    adapter.complete = MagicMock(side_effect=_complete)
    return adapter


class TestDebateDataclasses:
    def test_debate_position_fields(self):
        p = DebatePosition(role="bull", position="positive", key_points=["a"], confidence=0.8, highlight="evidence")
        assert p.role == "bull"
        assert p.confidence == 0.8

    def test_debate_verdict_fields(self):
        v = DebateVerdict(
            bull=None, bear=None,
            risk_manager_verdict="PROCEED", risk_manager_reasoning="looks good",
            dominant_position="bull", key_risk="watch rates",
            escalate=False,
        )
        assert v.escalate is False
        assert v.risk_manager_verdict == "PROCEED"


class TestRunDebate:
    def test_no_adapter_returns_non_escalating_default(self):
        result = run_debate("some goal", [], adapter=None)
        assert result.escalate is False
        assert result.risk_manager_verdict == "PROCEED"

    def test_no_done_steps_returns_default(self):
        adapter = MagicMock()
        steps = [MagicMock(status="blocked", index=1, text="step", result="")]
        result = run_debate("goal", steps, adapter=adapter)
        assert result.escalate is False

    def test_proceed_verdict_no_escalation(self):
        adapter = _make_debate_adapter(
            bull_json='{"position":"strong","key_points":["a","b"],"confidence":0.8,"strongest_evidence":"data"}',
            bear_json='{"position":"weak","key_points":["x"],"confidence":0.3,"fatal_flaw":"minor"}',
            rm_json='{"verdict":"PROCEED","reasoning":"bull wins","dominant_position":"bull","key_risk":"none"}',
        )
        steps = [MagicMock(status="done", index=1, text="step", result="output")]
        result = run_debate("goal", steps, adapter=adapter)
        assert result.risk_manager_verdict == "PROCEED"
        assert result.escalate is False
        assert result.dominant_position == "bull"

    def test_reject_verdict_escalates(self):
        adapter = _make_debate_adapter(
            bull_json='{"position":"ok","key_points":[],"confidence":0.4,"strongest_evidence":""}',
            bear_json='{"position":"fatal","key_points":["fatal flaw"],"confidence":0.9,"fatal_flaw":"wrong data"}',
            rm_json='{"verdict":"REJECT","reasoning":"output is unreliable","dominant_position":"bear","key_risk":"wrong data"}',
        )
        steps = [MagicMock(status="done", index=1, text="step", result="output")]
        result = run_debate("goal", steps, adapter=adapter)
        assert result.risk_manager_verdict == "REJECT"
        assert result.escalate is True
        assert result.dominant_position == "bear"

    def test_caution_verdict_escalates(self):
        adapter = _make_debate_adapter(
            bull_json='{"position":"ok","key_points":[],"confidence":0.6,"strongest_evidence":"partial"}',
            bear_json='{"position":"weak","key_points":["gap"],"confidence":0.5,"fatal_flaw":"incomplete"}',
            rm_json='{"verdict":"CAUTION","reasoning":"proceed with caveats","dominant_position":"neutral","key_risk":"incomplete data"}',
        )
        steps = [MagicMock(status="done", index=1, text="step", result="output")]
        result = run_debate("goal", steps, adapter=adapter)
        assert result.escalate is True
        assert result.risk_manager_verdict == "CAUTION"

    def test_bull_position_populated(self):
        adapter = _make_debate_adapter(
            bull_json='{"position":"strong thesis","key_points":["p1","p2"],"confidence":0.85,"strongest_evidence":"RCT data"}',
            bear_json='{"position":"weak","key_points":[],"confidence":0.2,"fatal_flaw":""}',
            rm_json='{"verdict":"PROCEED","reasoning":"ok","dominant_position":"bull","key_risk":""}',
        )
        steps = [MagicMock(status="done", index=1, text="step", result="output")]
        result = run_debate("goal", steps, adapter=adapter)
        assert result.bull is not None
        assert result.bull.position == "strong thesis"
        assert result.bull.confidence == 0.85
        assert result.bull.highlight == "RCT data"

    def test_bear_position_populated(self):
        adapter = _make_debate_adapter(
            bull_json='{"position":"ok","key_points":[],"confidence":0.5,"strongest_evidence":""}',
            bear_json='{"position":"fatal flaw found","key_points":["data wrong"],"confidence":0.9,"fatal_flaw":"methodology invalid"}',
            rm_json='{"verdict":"REJECT","reasoning":"bear wins","dominant_position":"bear","key_risk":"methodology"}',
        )
        steps = [MagicMock(status="done", index=1, text="step", result="output")]
        result = run_debate("goal", steps, adapter=adapter)
        assert result.bear is not None
        assert result.bear.highlight == "methodology invalid"

    def test_adapter_failure_returns_default(self):
        adapter = MagicMock()
        adapter.complete = MagicMock(side_effect=RuntimeError("API down"))
        steps = [MagicMock(status="done", index=1, text="step", result="output")]
        result = run_debate("goal", steps, adapter=adapter)
        assert result.escalate is False  # failure never escalates

    def test_debate_prompts_exist(self):
        assert "BULL" in _BULL_SYSTEM or "bull" in _BULL_SYSTEM.lower()
        assert "BEAR" in _BEAR_SYSTEM or "bear" in _BEAR_SYSTEM.lower()
        assert "RISK MANAGER" in _RISK_MANAGER_SYSTEM or "risk" in _RISK_MANAGER_SYSTEM.lower()


class TestQualityGateWithDebate:
    def test_with_debate_false_no_debate_field_populated(self):
        adapter = MagicMock()
        resp = MagicMock()
        resp.content = '{"verdict":"PASS","reason":"good","confidence":0.9}'
        resp.tool_calls = []
        resp.input_tokens = 50
        resp.output_tokens = 25
        adapter.complete = MagicMock(return_value=resp)
        steps = [MagicMock(status="done", index=1, text="step", result="result")]
        verdict = run_quality_gate("goal", steps, adapter, with_debate=False)
        assert verdict.debate is None

    def test_with_debate_true_populates_debate_field(self):
        call_count = [0]

        def _multi_resp(messages, **kw):
            i = call_count[0]
            call_count[0] += 1
            resp = MagicMock()
            resp.tool_calls = []
            resp.input_tokens = 50
            resp.output_tokens = 25
            if i == 0:  # quality gate pass 1
                resp.content = '{"verdict":"PASS","reason":"ok","confidence":0.9}'
            elif i == 1:  # adversarial
                resp.content = '[]'
            elif i == 2:  # bull
                resp.content = '{"position":"strong","key_points":[],"confidence":0.8,"strongest_evidence":"x"}'
            elif i == 3:  # bear
                resp.content = '{"position":"weak","key_points":[],"confidence":0.3,"fatal_flaw":"none"}'
            else:  # risk manager
                resp.content = '{"verdict":"PROCEED","reasoning":"fine","dominant_position":"bull","key_risk":""}'
            return resp

        adapter = MagicMock()
        adapter.complete = MagicMock(side_effect=_multi_resp)
        steps = [MagicMock(status="done", index=1, text="step", result="output")]
        verdict = run_quality_gate("goal", steps, adapter, run_adversarial=True, with_debate=True)
        assert verdict.debate is not None

    def test_debate_reject_overrides_pass(self):
        call_count = [0]

        def _multi_resp(messages, **kw):
            i = call_count[0]
            call_count[0] += 1
            resp = MagicMock()
            resp.tool_calls = []
            resp.input_tokens = 50
            resp.output_tokens = 25
            if i == 0:
                resp.content = '{"verdict":"PASS","reason":"ok","confidence":0.9}'
            elif i == 1:
                resp.content = '[]'
            elif i == 2:
                resp.content = '{"position":"ok","key_points":[],"confidence":0.5,"strongest_evidence":""}'
            elif i == 3:
                resp.content = '{"position":"fatal","key_points":["flaw"],"confidence":0.9,"fatal_flaw":"wrong"}'
            else:
                resp.content = '{"verdict":"REJECT","reasoning":"output unreliable","dominant_position":"bear","key_risk":"wrong data"}'
            return resp

        adapter = MagicMock()
        adapter.complete = MagicMock(side_effect=_multi_resp)
        steps = [MagicMock(status="done", index=1, text="step", result="output")]
        verdict = run_quality_gate("goal", steps, adapter, run_adversarial=True, with_debate=True)
        assert verdict.escalate is True
        assert "REJECT" in verdict.reason or "Debate" in verdict.reason


class TestProbeContestedClaims:
    """Tests for _probe_contested_claims — inversion-at-verification for adversarial review.

    The feature catches reviewer hallucinations (e.g. 2026-04-17 slycrel-go
    run: "Go not installed on this machine" when Go is demonstrably at
    ~/go/bin/go). The reviewer self-generates the probe that would settle
    its own claim; the probe's exit code is the ground truth.
    """

    def test_empty_claims_list_returns_empty(self):
        assert _probe_contested_claims([]) == []

    def test_non_dict_items_passed_through(self):
        # Defensive: sometimes the adversarial JSON returns non-object entries.
        result = _probe_contested_claims(["not a dict", 42])
        assert result == ["not a dict", 42]

    def test_claim_without_command_marked_unprobed(self):
        claim = {"claim": "the output is too optimistic", "verdict": "CONTESTED", "reason": "no metric"}
        [out] = _probe_contested_claims([claim])
        assert out["probe_status"] == "unprobed"
        assert out["verdict"] == "CONTESTED"  # unchanged — can't run nothing

    def test_null_command_marked_unprobed(self):
        claim = {"claim": "x", "verdict": "CONTESTED", "settled_by_command": None}
        [out] = _probe_contested_claims([claim])
        assert out["probe_status"] == "unprobed"

    def test_empty_command_marked_unprobed(self):
        claim = {"claim": "x", "verdict": "CONTESTED", "settled_by_command": "   "}
        [out] = _probe_contested_claims([claim])
        assert out["probe_status"] == "unprobed"

    def test_probe_exits_zero_dismisses_claim(self):
        # "exit 0 means claim-as-stated-by-reviewer-is-wrong" convention.
        claim = {
            "claim": "the file /etc/hostname does not exist",
            "verdict": "CONTESTED",
            "settled_by_command": "test -f /etc/hostname",
        }
        [out] = _probe_contested_claims([claim])
        assert out["probe_status"] == "dismissed"
        assert out["verdict"] == "DISMISSED_BY_PROBE"
        assert out["original_verdict"] == "CONTESTED"
        assert out["probe_exit_code"] == 0

    def test_probe_nonzero_exit_validates_reviewer(self):
        # Probe agrees with the reviewer — contestation stands.
        claim = {
            "claim": "the file /nonexistent/nowhere does not exist",
            "verdict": "CONTESTED",
            "settled_by_command": "test -f /nonexistent/nowhere",
        }
        [out] = _probe_contested_claims([claim])
        assert out["probe_status"] == "validated"
        assert out["verdict"] == "CONTESTED"  # unchanged
        assert out["probe_exit_code"] != 0
        assert "original_verdict" not in out  # no reclassification happened

    def test_probe_timeout_is_unrunnable(self, monkeypatch):
        import subprocess as _sp
        def _raise_timeout(*a, **kw):
            raise _sp.TimeoutExpired(cmd=a[0] if a else "", timeout=1)
        monkeypatch.setattr(_sp, "run", _raise_timeout)
        claim = {"claim": "x", "verdict": "CONTESTED", "settled_by_command": "sleep 100"}
        [out] = _probe_contested_claims([claim])
        assert out["probe_status"] == "unrunnable"
        assert out["verdict"] == "CONTESTED"  # don't grant either side
        assert "timeout" in out["probe_output_preview"].lower()

    def test_probe_exception_is_unrunnable(self, monkeypatch):
        import subprocess as _sp
        def _raise(*a, **kw):
            raise OSError("simulated exec failure")
        monkeypatch.setattr(_sp, "run", _raise)
        claim = {"claim": "x", "verdict": "CONTESTED", "settled_by_command": "does-not-matter"}
        [out] = _probe_contested_claims([claim])
        assert out["probe_status"] == "unrunnable"
        assert out["verdict"] == "CONTESTED"
        assert "exec error" in out["probe_output_preview"]

    def test_mixed_batch_classifies_each_independently(self):
        # Dismissed + validated + unprobed together in one batch — each slot
        # is independent, per-claim captain's log emission too.
        claims = [
            {"claim": "file /etc/hostname does not exist", "verdict": "CONTESTED",
             "settled_by_command": "test -f /etc/hostname"},
            {"claim": "file /nowhere/never does not exist", "verdict": "CONTESTED",
             "settled_by_command": "test -f /nowhere/never"},
            {"claim": "subjective claim about tone", "verdict": "CONTESTED"},
        ]
        out = _probe_contested_claims(claims)
        statuses = [c["probe_status"] for c in out]
        assert statuses == ["dismissed", "validated", "unprobed"]

    def test_caller_dict_is_not_mutated(self):
        claim = {"claim": "x", "verdict": "CONTESTED",
                 "settled_by_command": "test -f /etc/hostname"}
        _probe_contested_claims([claim])
        # Caller's dict must be untouched — function returns a new list of
        # shallow copies so callers can diff before/after safely.
        assert "probe_status" not in claim
        assert claim["verdict"] == "CONTESTED"
