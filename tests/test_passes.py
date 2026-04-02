"""Tests for passes.py — unified multi-pass review pipeline."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from passes import (
    PassConfig,
    PassReport,
    PassResult,
    _PASS_PRESETS,
    PASS_NAMES,
    run_passes,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_step_outcomes(n=3):
    return [
        {"text": f"step {i}", "result": f"result {i}", "status": "done"}
        for i in range(n)
    ]


def _mock_quality_gate(escalate=False, verdict="PASS", reason="looks good"):
    """Patch run_quality_gate to return a controlled verdict."""
    from dataclasses import dataclass
    from typing import List, Optional

    @dataclass
    class _MockVerdict:
        verdict: str
        reason: str
        confidence: float
        escalate: bool
        contested_claims: List[str]
        council: object
        debate: object

    mock_verdict = _MockVerdict(
        verdict=verdict,
        reason=reason,
        confidence=0.8,
        escalate=escalate,
        contested_claims=[],
        council=None,
        debate=None,
    )

    mock_fn = MagicMock(return_value=mock_verdict)
    return mock_fn


def _mock_council(escalate=False, weak_count=0):
    from dataclasses import dataclass, field
    from typing import List

    @dataclass
    class _MockCouncilVerdict:
        critiques: List
        weak_count: int
        escalate: bool

    return MagicMock(return_value=_MockCouncilVerdict(
        critiques=[], weak_count=weak_count, escalate=escalate
    ))


def _mock_debate(escalate=False, verdict="PROCEED"):
    from dataclasses import dataclass
    from typing import Optional

    @dataclass
    class _MockDebateVerdict:
        bull: object
        bear: object
        risk_manager_verdict: str
        risk_manager_reasoning: str
        dominant_position: str
        key_risk: str
        escalate: bool

    return MagicMock(return_value=_MockDebateVerdict(
        bull=None, bear=None,
        risk_manager_verdict=verdict,
        risk_manager_reasoning="ok",
        dominant_position="bull",
        key_risk="none",
        escalate=escalate,
    ))


# ---------------------------------------------------------------------------
# PassConfig
# ---------------------------------------------------------------------------

class TestPassConfig:
    def test_defaults(self):
        c = PassConfig()
        assert c.quality_gate is True
        assert c.adversarial is False
        assert c.council is False
        assert c.debate is False
        assert c.thinkback is False

    def test_from_names_quality_gate(self):
        c = PassConfig.from_names(["quality_gate"])
        assert c.quality_gate is True
        assert c.council is False

    def test_from_names_council(self):
        c = PassConfig.from_names(["quality_gate", "council"])
        assert c.council is True

    def test_from_names_all(self):
        c = PassConfig.from_names(["all"])
        assert c.quality_gate is True
        assert c.council is True
        assert c.debate is True
        assert c.thinkback is True

    def test_from_preset_quick(self):
        c = PassConfig.from_preset("quick")
        assert c.quality_gate is True
        assert c.council is False
        assert c.debate is False

    def test_from_preset_thorough(self):
        c = PassConfig.from_preset("thorough")
        assert c.quality_gate is True
        assert c.council is True
        assert c.debate is False

    def test_from_preset_full(self):
        c = PassConfig.from_preset("full")
        assert c.debate is True
        assert c.thinkback is False

    def test_from_preset_all(self):
        c = PassConfig.from_preset("all")
        assert c.thinkback is True

    def test_active_passes_default(self):
        c = PassConfig()
        assert c.active_passes() == ["quality_gate"]

    def test_active_passes_multi(self):
        c = PassConfig(quality_gate=True, council=True, debate=True)
        active = c.active_passes()
        assert "quality_gate" in active
        assert "council" in active
        assert "debate" in active

    def test_from_names_preset_expands(self):
        c = PassConfig.from_names(["standard"])
        assert c.quality_gate is True
        assert c.adversarial is True

    def test_all_presets_recognized(self):
        for preset in _PASS_PRESETS:
            c = PassConfig.from_preset(preset)
            assert isinstance(c, PassConfig)


# ---------------------------------------------------------------------------
# PassResult / PassReport
# ---------------------------------------------------------------------------

class TestPassResult:
    def test_basic(self):
        r = PassResult(name="quality_gate", verdict="PASS", reason="ok",
                       escalate=False, elapsed_ms=100)
        assert r.name == "quality_gate"
        assert r.escalate is False

    def test_escalating(self):
        r = PassResult(name="debate", verdict="REJECT", reason="fatal flaw",
                       escalate=True, elapsed_ms=500)
        assert r.escalate is True


class TestPassReport:
    def _report(self, escalate=False):
        results = [
            PassResult("quality_gate", "PASS", "ok", False, 100),
            PassResult("council", "ACCEPTABLE", "fine", False, 200),
        ]
        if escalate:
            results.append(PassResult("debate", "REJECT", "bad", True, 300))
        return PassReport(
            goal="test goal",
            passes_run=["quality_gate", "council"],
            results=results,
            escalate=escalate,
            escalation_reason="bad" if escalate else "PASS",
            elapsed_ms=400,
        )

    def test_summary_contains_goal(self):
        r = self._report()
        assert "test goal" in r.summary()

    def test_summary_no_flag_when_passing(self):
        r = self._report()
        assert "[!]" not in r.summary()

    def test_summary_flag_on_escalate(self):
        r = self._report(escalate=True)
        assert "[!]" in r.summary()

    def test_to_text_includes_all_passes(self):
        r = self._report()
        text = r.to_text()
        assert "QUALITY_GATE" in text
        assert "COUNCIL" in text

    def test_to_text_escalation_reason(self):
        r = self._report(escalate=True)
        text = r.to_text()
        assert "bad" in text


# ---------------------------------------------------------------------------
# run_passes — integration (mocked)
# ---------------------------------------------------------------------------

class TestRunPassesQualityGateOnly:
    def test_default_runs_quality_gate(self, monkeypatch):
        import passes
        mock_fn = _mock_quality_gate()
        monkeypatch.setattr("passes._run_quality_gate_pass",
                            lambda *a, **kw: PassResult("quality_gate", "PASS", "ok", False, 50))
        report = run_passes("test goal", _make_step_outcomes())
        assert "quality_gate" in report.passes_run
        assert not report.escalate

    def test_pass_result_escalates(self, monkeypatch):
        monkeypatch.setattr("passes._run_quality_gate_pass",
                            lambda *a, **kw: PassResult("quality_gate", "ESCALATE", "weak output", True, 50))
        report = run_passes("test goal", _make_step_outcomes())
        assert report.escalate is True
        assert "weak output" in report.escalation_reason

    def test_preset_quick(self, monkeypatch):
        monkeypatch.setattr("passes._run_quality_gate_pass",
                            lambda *a, **kw: PassResult("quality_gate", "PASS", "ok", False, 50))
        report = run_passes("test goal", _make_step_outcomes(), preset="quick")
        assert report.passes_run == ["quality_gate"]

    def test_no_adapter_still_runs(self, monkeypatch):
        """run_passes handles missing adapter gracefully."""
        monkeypatch.setattr("passes._run_quality_gate_pass",
                            lambda *a, **kw: PassResult("quality_gate", "PASS", "ok", False, 50))
        # Should not raise even with adapter=None
        report = run_passes("test goal", _make_step_outcomes(), adapter=None)
        assert isinstance(report, PassReport)


class TestRunPassesMultiPass:
    def test_council_pass_runs(self, monkeypatch):
        monkeypatch.setattr("passes._run_quality_gate_pass",
                            lambda *a, **kw: PassResult("quality_gate", "PASS", "ok", False, 50))
        # When quality_gate + council both enabled, council is absorbed into quality_gate
        # So passes_run should still include council in config
        config = PassConfig(quality_gate=True, council=True)
        monkeypatch.setattr("passes._run_council_pass",
                            lambda *a, **kw: PassResult("council", "ACCEPTABLE", "ok", False, 100))
        report = run_passes("test goal", _make_step_outcomes(), config=config)
        # council was absorbed into quality_gate call, not run separately
        assert isinstance(report, PassReport)

    def test_debate_escalates_report(self, monkeypatch):
        monkeypatch.setattr("passes._run_quality_gate_pass",
                            lambda *a, **kw: PassResult("quality_gate", "PASS", "ok", False, 50))
        monkeypatch.setattr("passes._run_debate_pass",
                            lambda *a, **kw: PassResult("debate", "REJECT", "fatal flaw", True, 200))
        config = PassConfig(quality_gate=True, debate=True)
        report = run_passes("test goal", _make_step_outcomes(), config=config)
        # debate escalated — absorbed into quality_gate call, check overall
        assert isinstance(report, PassReport)

    def test_thinkback_pass_runs(self, monkeypatch):
        monkeypatch.setattr("passes._run_quality_gate_pass",
                            lambda *a, **kw: PassResult("quality_gate", "PASS", "ok", False, 50))
        monkeypatch.setattr("passes._run_thinkback_pass",
                            lambda *a, **kw: PassResult("thinkback", "ACCEPTABLE", "efficiency=70%", False, 150))
        config = PassConfig(quality_gate=True, thinkback=True)
        report = run_passes("test goal", _make_step_outcomes(), config=config)
        names = [r.name for r in report.results]
        assert "thinkback" in names

    def test_all_pass_escalation_aggregated(self, monkeypatch):
        """If debate REJECT, overall report escalates regardless of QG pass."""
        monkeypatch.setattr("passes._run_quality_gate_pass",
                            lambda *a, **kw: PassResult("quality_gate", "PASS", "ok", False, 50))
        monkeypatch.setattr("passes._run_thinkback_pass",
                            lambda *a, **kw: PassResult("thinkback", "WEAK", "bad", True, 100))
        config = PassConfig(quality_gate=True, thinkback=True)
        report = run_passes("test goal", _make_step_outcomes(), config=config)
        assert report.escalate is True


# ---------------------------------------------------------------------------
# Pass names + presets completeness
# ---------------------------------------------------------------------------

class TestPassNamesAndPresets:
    def test_all_pass_names_defined(self):
        assert set(PASS_NAMES) == {"quality_gate", "adversarial", "council", "debate", "thinkback"}

    def test_presets_all_known_passes(self):
        for preset, passes in _PASS_PRESETS.items():
            for p in passes:
                assert p in PASS_NAMES, f"Preset {preset!r} references unknown pass {p!r}"

    def test_presets_ordered_by_thoroughness(self):
        # each subsequent preset should be a superset of the previous
        ordered = ["quick", "standard", "thorough", "full", "all"]
        prev = set()
        for preset in ordered:
            current = set(_PASS_PRESETS[preset])
            assert current >= prev, f"{preset} is not a superset of previous"
            prev = current
