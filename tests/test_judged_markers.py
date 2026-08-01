"""§13e slice 2 — typed judge-error marks on fail-open verdicts.

Advisory judges (ralph verify, artifact check, quality gate, inspector
alignment) fail open by design: an error must never block execution. The
2026-07-31 fail-open census showed the cost — the fail-open default was
indistinguishable from a judged pass everywhere downstream, so learning
writers credited skills and the thread brain recorded "ralph-verified"
for judgments that never happened. These pins hold the boundary: behavior
stays fail-open, but the RECORD says judged=False.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from artifact_check import ArtifactVerdict, check_execution_claim, check_fabrication
from quality_gate import QualityVerdict, run_quality_gate
from verification_agent import StepVerdict, VerificationAgent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _hosted_free_off(monkeypatch):
    """Hermetic default: hosted-free OFF (this box has live keys)."""
    import hosted_free
    monkeypatch.setattr(hosted_free, "available", lambda: False)


def _adapter(content: str):
    resp = SimpleNamespace(content=content, input_tokens=10, output_tokens=20)
    adapter = MagicMock()
    adapter.complete.return_value = resp
    adapter._active_provider = ""
    adapter.model_key = "stub-model"
    return adapter


def _raising_adapter():
    adapter = MagicMock()
    adapter.complete.side_effect = RuntimeError("LLM transport down")
    adapter._active_provider = ""
    adapter.model_key = "stub-model"
    return adapter


def _make_step(status="done", text="do something", result="result text"):
    return SimpleNamespace(status=status, text=text, result=result, index=1)


# ---------------------------------------------------------------------------
# StepVerdict (ralph verify judge)
# ---------------------------------------------------------------------------

class TestStepVerdictJudged:
    def test_default_is_judged(self):
        assert StepVerdict(passed=True, reason="", confidence=0.9).judged is True

    def test_transport_error_fail_open_is_unjudged(self):
        va = VerificationAgent(_raising_adapter())
        v = va.verify_step("do the thing", "a perfectly plausible result")
        assert v.passed is True          # fail-open behavior unchanged
        assert v.confidence == 0.0
        assert v.judged is False         # ...but the record is honest

    def test_unparseable_output_fail_open_is_unjudged(self):
        va = VerificationAgent(_adapter("I cannot answer in JSON today."))
        v = va.verify_step("do the thing", "a result")
        assert v.passed is True
        assert v.judged is False

    def test_retry_under_threshold_coercion_stays_judged(self):
        # A real verdict arrived (RETRY, low confidence) and the threshold
        # policy coerced it to a pass — that is a JUDGED pass, not fail-open.
        va = VerificationAgent(
            _adapter('{"verdict": "RETRY", "reason": "weak", "confidence": 0.3}'),
            confidence_threshold=0.75)
        v = va.verify_step("do the thing", "a result")
        assert v.passed is True
        assert v.judged is True

    def test_empty_result_fail_is_judged(self):
        # Deterministic verdict (empty result → fail) is a judgment.
        va = VerificationAgent(_adapter("unused"))
        v = va.verify_step("do the thing", "   ")
        assert v.passed is False
        assert v.judged is True


class TestVerifyStepDict:
    """step_exec.verify_step returns a dict — judged must survive the seam."""

    def test_fail_open_dict_is_unjudged(self):
        from step_exec import verify_step
        out = verify_step("do the thing", "a result", _raising_adapter())
        assert out["passed"] is True
        assert out["judged"] is False

    def test_judged_verdict_dict_carries_judged_true(self):
        from step_exec import verify_step
        out = verify_step(
            "do the thing", "a result",
            _adapter('{"verdict": "PASS", "reason": "ok", "confidence": 0.9}'))
        assert out["passed"] is True
        assert out["judged"] is True


# ---------------------------------------------------------------------------
# Ralph thread-brain marker (loop_post_step)
# ---------------------------------------------------------------------------

class TestRalphCompiledTruthMarker:
    def _run(self, tmp_path, monkeypatch, verdict: dict):
        import loop_post_step as lps
        import thread_brain
        thread_brain.create_thread_brain(tmp_path, goal="g")
        monkeypatch.setattr(lps, "_verify_step", lambda *a, **k: verdict)
        monkeypatch.setattr(lps, "_current_run_dir_safe", lambda: tmp_path)
        ctx = SimpleNamespace(project="", verbose=False,
                              adapter=SimpleNamespace(model_key="mid"))
        return lps._run_ralph_verify(
            ctx, "verify the config", 3, "some result", "done", {},
            SimpleNamespace(model_key="mid"),
            step_tier_overrides={}, session_verify_failures=0,
            session_tier_floor="", verify_fail_threshold=3)

    def test_judged_pass_writes_ralph_verified(self, tmp_path, monkeypatch):
        self._run(tmp_path, monkeypatch,
                  {"passed": True, "reason": "ok", "confidence": 0.9,
                   "judged": True})
        from thread_brain import brain_path
        text = brain_path(tmp_path).read_text(encoding="utf-8")
        assert "step 3 ralph-verified: verify the config" in text
        assert "verify-error:" not in text

    def test_unjudged_pass_writes_verify_error_not_ralph_verified(
            self, tmp_path, monkeypatch):
        self._run(tmp_path, monkeypatch,
                  {"passed": True, "reason": "verify skipped (error)",
                   "confidence": 0.0, "judged": False})
        from thread_brain import brain_path
        text = brain_path(tmp_path).read_text(encoding="utf-8")
        assert "ralph-verified:" not in text     # forged truth is the bug
        assert "step 3 verify-error: unjudged fail-open pass" in text

    def test_missing_judged_key_defaults_to_judged(self, tmp_path, monkeypatch):
        # Old-style dicts (no judged key) must keep the pre-slice behavior.
        self._run(tmp_path, monkeypatch,
                  {"passed": True, "reason": "ok", "confidence": 0.9})
        from thread_brain import brain_path
        text = brain_path(tmp_path).read_text(encoding="utf-8")
        assert "ralph-verified:" in text


# ---------------------------------------------------------------------------
# ArtifactVerdict (fabrication / execution-claim checks)
# ---------------------------------------------------------------------------

class TestArtifactVerdictJudged:
    def test_default_is_judged(self):
        assert ArtifactVerdict(False).judged is True

    def test_fabrication_check_error_is_unjudged(self, monkeypatch, tmp_path):
        import artifact_check
        monkeypatch.setattr(artifact_check, "extract_write_claims",
                            lambda *_: (_ for _ in ()).throw(RuntimeError("boom")))
        v = check_fabrication("wrote foo.py", tmp_path, {})
        assert v.fabricated is False     # fail-open behavior unchanged
        assert v.judged is False
        assert "fail-open" in v.reason

    def test_fabrication_clean_pass_is_judged(self, tmp_path):
        v = check_fabrication("just narration, no write claims", tmp_path, {})
        assert v.fabricated is False
        assert v.judged is True

    def test_execution_check_error_is_unjudged(self, monkeypatch):
        import artifact_check
        monkeypatch.setattr(artifact_check, "_tool_failed",
                            lambda *_: (_ for _ in ()).throw(RuntimeError("boom")))
        v = check_execution_claim(
            "ran the tests, all passed",
            [{"name": "Bash", "input": {"command": "pytest"}}])
        assert v.fabricated is False
        assert v.judged is False

    def test_execution_clean_pass_is_judged(self):
        v = check_execution_claim("ran the tests", [])
        assert v.fabricated is False
        assert v.judged is True


# ---------------------------------------------------------------------------
# QualityVerdict (quality gate pass 1)
# ---------------------------------------------------------------------------

class TestQualityGateJudged:
    def test_default_is_judged(self):
        assert QualityVerdict("PASS", "", 0.9, False).judged is True

    def test_no_adapter_is_unjudged(self):
        v = run_quality_gate("goal", [_make_step()], adapter=None)
        assert v.verdict == "PASS"
        assert v.judged is False

    def test_parse_exception_is_unjudged_and_emits_gate_error(self, monkeypatch):
        events = []
        import captains_log
        monkeypatch.setattr(
            captains_log, "log_event",
            lambda *a, **k: events.append((a, k)))
        v = run_quality_gate("goal", [_make_step()], _raising_adapter(),
                             run_adversarial=False)
        assert v.verdict == "PASS"
        assert v.judged is False
        gate_errors = [k for _, k in events
                       if (k.get("context") or {}).get("decision") == "GATE_ERROR"]
        assert len(gate_errors) == 1
        assert gate_errors[0]["context"]["judged"] is False

    def test_no_json_fallthrough_is_unjudged_and_emits_gate_error(
            self, monkeypatch):
        # Third fail-open (census miss): extract_json returns nothing, no
        # exception — the pre-initialized PASS defaults reach the final
        # return. Must be marked unjudged and counted in the denominator.
        events = []
        import captains_log
        monkeypatch.setattr(
            captains_log, "log_event",
            lambda *a, **k: events.append((a, k)))
        v = run_quality_gate("goal", [_make_step()],
                             _adapter("no json anywhere in this reply"),
                             run_adversarial=False)
        assert v.verdict == "PASS"
        assert v.judged is False
        gate_errors = [k for _, k in events
                       if (k.get("context") or {}).get("decision") == "GATE_ERROR"]
        assert len(gate_errors) == 1
        assert gate_errors[0]["context"]["judged"] is False

    def test_judged_pass_is_judged(self):
        v = run_quality_gate(
            "goal", [_make_step()],
            _adapter('{"verdict": "PASS", "reason": "fine", "confidence": 0.9}'),
            run_adversarial=False)
        assert v.verdict == "PASS"
        assert v.judged is True
