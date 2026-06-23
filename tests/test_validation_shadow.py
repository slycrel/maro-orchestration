"""Tests for the validation shadow-eval harness (local-vs-paid agreement)."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import validation_shadow as vs  # noqa: E402


def _verdict(passed, conf, reason="r"):
    return SimpleNamespace(passed=passed, confidence=conf, reason=reason)


@pytest.fixture
def captured(monkeypatch):
    """Capture captains_log.log_event calls instead of writing to disk."""
    events = []

    def fake_log_event(event_type, *, subject="", summary="", context=None):
        events.append({"event_type": event_type, "subject": subject,
                       "summary": summary, "context": context or {}})

    import captains_log
    monkeypatch.setattr(captains_log, "log_event", fake_log_event)
    return events


def _enable(monkeypatch, on=True):
    monkeypatch.setattr(vs, "_cfg", lambda key, default: on if key == "shadow_eval" else default)


# --- gating -----------------------------------------------------------------

def test_disabled_by_default(monkeypatch):
    monkeypatch.setattr(vs, "_cfg", lambda key, default: default)
    assert vs.shadow_eval_enabled() is False


def test_enabled_via_config_string(monkeypatch):
    monkeypatch.setattr(vs, "_cfg", lambda key, default: "true")
    assert vs.shadow_eval_enabled() is True
    monkeypatch.setattr(vs, "_cfg", lambda key, default: "off")
    assert vs.shadow_eval_enabled() is False


def test_shadow_eval_noop_when_disabled(monkeypatch, captured):
    _enable(monkeypatch, on=False)
    vs.shadow_eval("do x", "result", _verdict(True, 0.9), "local",
                   paid_verdict=_verdict(True, 0.8))
    assert captured == []


# --- recording paths --------------------------------------------------------

def test_escalation_path_logs_pair_without_calling_adapter(monkeypatch, captured):
    _enable(monkeypatch)
    called = {"adapter": False}

    class Boom:
        def complete(self, *a, **k):
            called["adapter"] = True
            raise AssertionError("paid adapter must NOT be called on escalation path")

    vs.shadow_eval("Analyze the data", "result text", _verdict(False, 0.4), "qwen",
                   paid_verdict=_verdict(True, 0.85), escalated=True)
    assert called["adapter"] is False
    assert len(captured) == 1
    c = captured[0]["context"]
    assert captured[0]["event_type"] == "VALIDATOR_SHADOWED"
    assert c["step_class"] == "analyze"          # "Analyze ..." classified
    assert c["local_passed"] is False and c["paid_passed"] is True
    assert c["agreement"] == "DISAGREE"
    assert c["escalated"] is True


def test_decisive_path_makes_extra_paid_call(monkeypatch, captured):
    _enable(monkeypatch)
    from llm import LLMAdapter

    class FakePaid(LLMAdapter):
        model_key = "paid"
        def complete(self, *a, **k):  # not reached — VerificationAgent is faked below
            raise AssertionError

    paid_verdict = _verdict(True, 0.7)

    class FakeVA:
        def __init__(self, adapter, **k): pass
        def verify_step(self, step_text, result): return paid_verdict

    import verification_agent
    monkeypatch.setattr(verification_agent, "VerificationAgent", FakeVA)

    vs.shadow_eval("Run the build", "ok", _verdict(True, 0.95), "qwen",
                   paid_adapter=FakePaid())
    assert len(captured) == 1
    c = captured[0]["context"]
    assert c["agreement"] == "AGREE"
    assert c["local_confidence"] == 0.95
    assert c["paid_passed"] is True
    assert c["escalated"] is False


def test_decisive_path_skips_non_llm_adapter(monkeypatch, captured):
    # Dry-run / test double (not an LLMAdapter) → no real spend, no row.
    _enable(monkeypatch)
    vs.shadow_eval("Run the build", "ok", _verdict(True, 0.95), "qwen",
                   paid_adapter=object())
    assert captured == []


def test_shadow_eval_never_raises(monkeypatch, captured):
    _enable(monkeypatch)
    # neither paid_verdict nor paid_adapter → nothing to compare, must not raise
    vs.shadow_eval("x", "y", _verdict(True, 0.9), "local")
    assert captured == []


# --- analysis ---------------------------------------------------------------

def _ev(step_class, local_passed, lc, paid_passed, escalated=False):
    return {"event_type": "VALIDATOR_SHADOWED", "timestamp": "2026-06-22T10:00:00",
            "context": {"step_class": step_class, "local_passed": local_passed,
                        "local_confidence": lc, "paid_passed": paid_passed,
                        "agreement": "AGREE" if local_passed == paid_passed else "DISAGREE",
                        "escalated": escalated, "step_preview": "p"}}


def test_analyze_per_class_and_error_directions():
    events = [
        _ev("exec_command", True, 0.95, True),     # agree
        _ev("exec_command", True, 0.9, True),      # agree
        _ev("exec_command", True, 0.8, False),     # false_pass (dangerous)
        _ev("analyze", False, 0.4, True, escalated=True),   # false_fail
        _ev("analyze", True, 0.7, True),           # agree
        {"event_type": "OTHER", "context": {}},    # ignored
    ]
    out = vs.analyze_validation_agreement(events)
    assert out["rows"] == 5
    assert out["agreements"] == 3
    assert abs(out["agreement_rate"] - 0.6) < 1e-9
    ec = out["by_class"]["exec_command"]
    assert ec["n"] == 3 and ec["agree"] == 2 and ec["false_pass"] == 1 and ec["false_fail"] == 0
    an = out["by_class"]["analyze"]
    assert an["n"] == 2 and an["false_fail"] == 1
    assert len(out["disagreements"]) == 2


def test_analyze_calibration_buckets():
    events = [_ev("general", True, 0.95, True), _ev("general", True, 0.92, False),
              _ev("general", False, 0.4, False)]
    out = vs.analyze_validation_agreement(events)
    hi = out["calibration"]["0.9-1.0"]
    assert hi["n"] == 2 and hi["agree"] == 1   # one agree, one disagree at high conf
    lo = out["calibration"]["0.0-0.6"]
    assert lo["n"] == 1 and lo["agree"] == 1


def test_analyze_empty():
    out = vs.analyze_validation_agreement([])
    assert out["rows"] == 0 and out["agreement_rate"] == 0.0 and out["by_class"] == {}


def test_cli_agreement_smoke(monkeypatch, capsys):
    monkeypatch.setattr(vs, "_read_events", lambda base=None: [
        _ev("exec_command", True, 0.9, True), _ev("analyze", True, 0.8, False)])
    rc = vs.main(["--agreement"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "VALIDATOR_SHADOWED rows: 2" in out
    assert "exec_command" in out and "false_pass" in out
