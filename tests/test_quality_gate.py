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
