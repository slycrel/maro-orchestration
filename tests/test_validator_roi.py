"""Tests for the local-validator ROI report (BACKLOG #9)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import validator_roi as vr  # noqa: E402


def _ladder_row(tier, *, local_ms=0, paid_ms=0, chars=4000, passed=True, conf=0.9):
    return {"event_type": "VALIDATION_LADDER", "context": {
        "tier": tier, "source": "qwen2.5-coder:3b" if tier == "local-decisive" else "paid",
        "passed": passed, "confidence": conf,
        "local_elapsed_ms": local_ms, "paid_elapsed_ms": paid_ms,
        "input_chars": chars}}


def _gate_row(source, chars=2400):
    return {"event_type": "QUALITY_GATE_VERDICT", "context": {
        "decision": "PASS", "verdict": "PASS", "confidence": 1.0,
        "source": source, "input_chars": chars}}


class TestIsLocalSource:
    def test_configured_local_model_matches(self):
        assert vr._is_local_source("qwen2.5-coder:3b", ["qwen2.5-coder:3b"])

    def test_ollama_tag_heuristic_without_config(self):
        assert vr._is_local_source("qwen2.5-coder:3b", [])

    def test_paid_and_empty_sources_are_not_local(self):
        assert not vr._is_local_source("power", [])
        assert not vr._is_local_source("unknown", [])
        assert not vr._is_local_source("", ["qwen2.5-coder:3b"])
        assert not vr._is_local_source("anthropic:claude", [])


class TestAnalyzeRoi:
    def test_empty_corpus(self):
        s = vr.analyze_roi([], [], local_names=[])
        assert s["step_ladder"]["rows"] == 0
        assert s["quality_gate"]["rows"] == 0
        assert s["est_total_saved_usd"] == 0.0

    def test_ladder_tiers_and_rates(self):
        rows = [
            _ladder_row("local-decisive", local_ms=900),
            _ladder_row("local-decisive", local_ms=1100),
            _ladder_row("escalated", local_ms=800, paid_ms=3000),
            _ladder_row("paid", paid_ms=5000),
        ]
        s = vr.analyze_roi(rows, [], local_names=["qwen2.5-coder:3b"])["step_ladder"]
        assert s["local_decisive"] == 2
        assert s["escalated"] == 1
        assert s["paid_only"] == 1
        assert s["paid_calls_skipped"] == 2
        # decisive rate over local ATTEMPTS (decisive + escalated), not all rows
        assert s["decisive_rate"] == pytest.approx(2 / 3)
        assert s["avg_local_latency_ms"] == 1000
        assert s["avg_paid_latency_ms"] == 4000
        assert s["avg_wasted_local_ms_on_escalation"] == 800
        assert s["est_saved_usd"] > 0

    def test_gate_local_vs_paid_split(self):
        gates = [_gate_row("qwen2.5-coder:3b"), _gate_row("qwen2.5-coder:3b"),
                 _gate_row("power"), _gate_row("unknown")]
        g = vr.analyze_roi([], gates, local_names=["qwen2.5-coder:3b"])["quality_gate"]
        assert g["local_decisive"] == 2
        assert g["paid"] == 2
        assert g["paid_calls_skipped"] == 2
        assert g["est_saved_usd"] > 0

    def test_unknown_tier_rows_ignored(self):
        s = vr.analyze_roi([_ladder_row("bogus")], [], local_names=[])
        assert s["step_ladder"]["rows"] == 0

    def test_missing_input_chars_uses_fallback_estimate(self):
        row = _ladder_row("local-decisive", chars=0)
        s = vr.analyze_roi([row], [], local_names=["qwen2.5-coder:3b"])
        assert s["step_ladder"]["est_saved_usd"] > 0


class TestVerifyStepEmitsLadderRow:
    """verify_step's instrumentation writes one VALIDATION_LADDER row per call."""

    @pytest.fixture
    def captured(self, monkeypatch):
        events = []

        def fake_log_event(event_type, *, subject="", summary="", context=None, **kw):
            events.append({"event_type": event_type, "context": context or {}})

        import captains_log
        monkeypatch.setattr(captains_log, "log_event", fake_log_event)
        return events

    def test_paid_only_path_emits_paid_tier(self, monkeypatch, captured):
        import step_exec
        import local_models
        from types import SimpleNamespace
        from unittest.mock import MagicMock, patch

        monkeypatch.setattr(local_models, "configured_models", lambda: [])
        va = MagicMock()
        va.return_value.verify_step.return_value = SimpleNamespace(
            passed=True, reason="ok", confidence=0.9)
        with patch("verification_agent.VerificationAgent", va):
            out = step_exec.verify_step("do the thing", "did the thing", adapter=object())
        assert out["passed"] is True
        ladder = [e for e in captured if e["event_type"] == "VALIDATION_LADDER"]
        assert len(ladder) == 1
        assert ladder[0]["context"]["tier"] == "paid"
        assert ladder[0]["context"]["input_chars"] == len("did the thing")

    def test_local_decisive_path_emits_local_tier(self, monkeypatch, captured):
        import step_exec
        import local_models
        from types import SimpleNamespace
        from unittest.mock import MagicMock, patch

        monkeypatch.setattr(local_models, "configured_models", lambda: ["qwen2.5-coder:3b"])
        monkeypatch.setattr(local_models, "ensure_validator_running", lambda: None)
        monkeypatch.setattr(local_models, "min_certainty", lambda: 0.6)
        monkeypatch.setattr(local_models, "input_char_budget", lambda: 6000)
        _local_adapter = SimpleNamespace(model_key="qwen2.5-coder:3b")
        monkeypatch.setattr(local_models, "build_local_validator_adapter",
                            lambda: _local_adapter)
        va = MagicMock()
        va.return_value.verify_step.return_value = SimpleNamespace(
            passed=True, reason="ok", confidence=0.95)
        with patch("verification_agent.VerificationAgent", va):
            out = step_exec.verify_step("do the thing", "did the thing", adapter=object())
        assert out["source"] == "qwen2.5-coder:3b"
        ladder = [e for e in captured if e["event_type"] == "VALIDATION_LADDER"]
        assert len(ladder) == 1
        assert ladder[0]["context"]["tier"] == "local-decisive"

    def test_escalation_path_emits_escalated_tier(self, monkeypatch, captured):
        import step_exec
        import local_models
        from types import SimpleNamespace
        from unittest.mock import MagicMock, patch

        monkeypatch.setattr(local_models, "configured_models", lambda: ["qwen2.5-coder:3b"])
        monkeypatch.setattr(local_models, "ensure_validator_running", lambda: None)
        monkeypatch.setattr(local_models, "min_certainty", lambda: 0.6)
        monkeypatch.setattr(local_models, "input_char_budget", lambda: 6000)
        monkeypatch.setattr(local_models, "build_local_validator_adapter",
                            lambda: SimpleNamespace(model_key="qwen2.5-coder:3b"))
        va = MagicMock()
        # local UNDECIDED (conf below min_certainty) then paid decisive
        va.return_value.verify_step.side_effect = [
            SimpleNamespace(passed=True, reason="?", confidence=0.2),
            SimpleNamespace(passed=False, reason="paid says no", confidence=0.9),
        ]
        with patch("verification_agent.VerificationAgent", va):
            out = step_exec.verify_step("do the thing", "did the thing", adapter=object())
        assert out["decision"] == "ESCALATED"
        ladder = [e for e in captured if e["event_type"] == "VALIDATION_LADDER"]
        assert len(ladder) == 1
        assert ladder[0]["context"]["tier"] == "escalated"
