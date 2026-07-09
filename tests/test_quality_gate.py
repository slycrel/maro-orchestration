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
        # sys.executable is guaranteed to exist and is portable — /etc/hostname
        # (used here previously) is a Linux-only convention; macOS has no such
        # file at all, so this test always failed on macOS regardless of what
        # the code under test actually did.
        claim = {
            "claim": f"the file {sys.executable} does not exist",
            "verdict": "CONTESTED",
            "settled_by_command": f"test -f {sys.executable}",
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

    def test_probe_runs_in_ambient_cwd(self, tmp_path):
        # The settled_by_command must resolve relative paths against the
        # run-scoped project dir, not Maro's launch cwd — the bug that made a
        # probe dismiss a correct path-mismatch contestation. Create a file in
        # tmp_path, set the ambient cwd there, probe with a RELATIVE test.
        import llm
        (tmp_path / "marker.txt").write_text("here")
        llm.set_default_subprocess_cwd(str(tmp_path))
        try:
            claim = {
                "claim": "marker.txt does not exist",
                "verdict": "CONTESTED",
                "settled_by_command": "test -f marker.txt",  # relative
            }
            [out] = _probe_contested_claims([claim])
        finally:
            llm.set_default_subprocess_cwd(None)
        # Resolved in tmp_path → file found → exit 0 → contestation dismissed.
        assert out["probe_status"] == "dismissed"
        assert out["probe_exit_code"] == 0

    def test_mixed_batch_classifies_each_independently(self):
        # Dismissed + validated + unprobed together in one batch — each slot
        # is independent, per-claim captain's log emission too.
        claims = [
            # sys.executable: portable "known to exist" file (see
            # test_probe_exits_zero_dismisses_claim for why not /etc/hostname).
            {"claim": f"file {sys.executable} does not exist", "verdict": "CONTESTED",
             "settled_by_command": f"test -f {sys.executable}"},
            {"claim": "file /nowhere/never does not exist", "verdict": "CONTESTED",
             "settled_by_command": "test -f /nowhere/never"},
            {"claim": "subjective claim about tone", "verdict": "CONTESTED"},
        ]
        out = _probe_contested_claims(claims)
        statuses = [c["probe_status"] for c in out]
        assert statuses == ["dismissed", "validated", "unprobed"]

    def test_caller_dict_is_not_mutated(self):
        claim = {"claim": "x", "verdict": "CONTESTED",
                 "settled_by_command": f"test -f {sys.executable}"}
        _probe_contested_claims([claim])
        # Caller's dict must be untouched — function returns a new list of
        # shallow copies so callers can diff before/after safely.
        assert "probe_status" not in claim
        assert claim["verdict"] == "CONTESTED"


class TestQualityGateCaptainsLogEmit:
    """Run-transparency: QUALITY_GATE_VERDICT event lands on the captain's log."""

    def test_emits_quality_gate_verdict_event(self, monkeypatch):
        captured: list = []

        def fake_log_event(event_type, *, subject, summary, context=None,
                           note=None, loop_id=None, related_ids=None):
            captured.append({
                "event_type": event_type, "summary": summary,
                "context": context or {}, "loop_id": loop_id,
            })
            return {}

        import captains_log as _cl
        monkeypatch.setattr(_cl, "log_event", fake_log_event)

        resp = MagicMock()
        resp.tool_calls = []
        resp.input_tokens = 10
        resp.output_tokens = 5
        resp.content = '{"verdict":"ESCALATE","reason":"shallow output","confidence":0.85}'
        adapter = MagicMock()
        adapter.complete = MagicMock(return_value=resp)

        steps = [MagicMock(status="done", index=1, text="step", result="result text")]
        run_quality_gate("ship the headless server", steps, adapter,
                         run_adversarial=False, loop_id="abc12345")

        gate_events = [e for e in captured if e["event_type"] == "QUALITY_GATE_VERDICT"]
        assert len(gate_events) == 1
        ev = gate_events[0]
        assert ev["loop_id"] == "abc12345"
        assert ev["context"]["verdict"] == "ESCALATE"
        assert ev["context"]["escalate"] is True  # 0.85 >= default 0.75 threshold
        assert ev["context"]["confidence"] == pytest.approx(0.85)
        assert ev["context"]["decision"] == "ESCALATE"  # decision matches action
        assert "shallow output" in ev["context"]["reason"]

    def test_decision_weak_escalate_when_confidence_below_threshold(self, monkeypatch):
        """LLM says ESCALATE but confidence too low → decision=WEAK_ESCALATE.

        Regression: 2026-04-26 audit caught log lines reading
        `verdict=ESCALATE escalate=False` — the printed verdict didn't match
        the action. Now `decision` is the action-matching label so the log
        line and captain's-log event are unambiguous.
        """
        captured: list = []

        def fake_log_event(event_type, *, subject, summary, context=None,
                           note=None, loop_id=None, related_ids=None):
            captured.append({
                "event_type": event_type, "summary": summary,
                "context": context or {}, "loop_id": loop_id,
            })
            return {}

        import captains_log as _cl
        monkeypatch.setattr(_cl, "log_event", fake_log_event)

        # LLM recommends ESCALATE but confidence 0.68 is below default 0.75
        resp = MagicMock()
        resp.tool_calls = []
        resp.input_tokens = 10
        resp.output_tokens = 5
        resp.content = '{"verdict":"ESCALATE","reason":"truncated mid-sentence","confidence":0.68}'
        adapter = MagicMock()
        adapter.complete = MagicMock(return_value=resp)

        steps = [MagicMock(status="done", index=1, text="step", result="result text")]
        verdict = run_quality_gate("ship the thing", steps, adapter,
                                   run_adversarial=False, loop_id="def67890")

        # Action: did NOT escalate (confidence too low)
        assert verdict.escalate is False
        # Raw LLM recommendation preserved
        assert verdict.verdict == "ESCALATE"

        gate_events = [e for e in captured if e["event_type"] == "QUALITY_GATE_VERDICT"]
        assert len(gate_events) == 1
        ev = gate_events[0]
        # Decision label matches action, not raw LLM recommendation
        assert ev["context"]["decision"] == "WEAK_ESCALATE"
        assert ev["context"]["verdict"] == "ESCALATE"      # raw recommendation
        assert ev["context"]["escalate"] is False           # action
        assert ev["context"]["confidence_threshold"] == pytest.approx(0.75)
        # Summary string is unambiguous
        assert "decision=WEAK_ESCALATE" in ev["summary"]

    def test_decision_pass_when_verdict_pass(self, monkeypatch):
        """verdict=PASS → decision=PASS (no contradiction even when verdict=PASS)."""
        captured: list = []

        def fake_log_event(event_type, *, subject, summary, context=None,
                           note=None, loop_id=None, related_ids=None):
            captured.append({
                "event_type": event_type, "summary": summary,
                "context": context or {}, "loop_id": loop_id,
            })
            return {}

        import captains_log as _cl
        monkeypatch.setattr(_cl, "log_event", fake_log_event)

        resp = MagicMock()
        resp.tool_calls = []
        resp.input_tokens = 10
        resp.output_tokens = 5
        resp.content = '{"verdict":"PASS","reason":"all good","confidence":0.9}'
        adapter = MagicMock()
        adapter.complete = MagicMock(return_value=resp)

        steps = [MagicMock(status="done", index=1, text="step", result="result text")]
        run_quality_gate("a goal", steps, adapter, run_adversarial=False)

        gate_events = [e for e in captured if e["event_type"] == "QUALITY_GATE_VERDICT"]
        assert len(gate_events) == 1
        ev = gate_events[0]
        assert ev["context"]["decision"] == "PASS"
        assert ev["context"]["verdict"] == "PASS"
        assert ev["context"]["escalate"] is False


class TestQualityGateLocalLadder:
    """BACKLOG #7: local-first ladder on the post-loop gate — a free local
    model judges first; decisive verdicts skip the paid call, UNDECIDED
    escalates to the paid adapter (same stance as step_exec.verify_step)."""

    def _resp(self, verdict="PASS", confidence=0.9, reason="fine"):
        resp = MagicMock()
        resp.tool_calls = []
        resp.input_tokens = 10
        resp.output_tokens = 5
        resp.content = (
            f'{{"verdict":"{verdict}","reason":"{reason}","confidence":{confidence}}}'
        )
        return resp

    def _steps(self):
        return [MagicMock(status="done", index=1, text="step", result="result text")]

    def _wire_local(self, monkeypatch, local_adapter, min_cert=0.75):
        import local_models as _lm
        monkeypatch.setattr(_lm, "configured_models", lambda: ["qwen-test"])
        monkeypatch.setattr(_lm, "ensure_validator_running", lambda **kw: True)
        monkeypatch.setattr(_lm, "build_local_validator_adapter",
                            lambda fallback=None: local_adapter)
        monkeypatch.setattr(_lm, "min_certainty", lambda: min_cert)

    def test_decisive_local_skips_paid(self, monkeypatch):
        local = MagicMock()
        local.model_key = "local-test"
        local.complete = MagicMock(return_value=self._resp("PASS", 0.9, "local says fine"))
        paid = MagicMock()
        paid.complete = MagicMock(side_effect=AssertionError("paid must not be called"))
        self._wire_local(monkeypatch, local)

        v = run_quality_gate("goal", self._steps(), paid, run_adversarial=False)
        assert v.verdict == "PASS"
        assert "local says fine" in v.reason
        assert paid.complete.call_count == 0
        assert local.complete.call_count >= 1

    def test_undecided_local_escalates_to_paid(self, monkeypatch):
        local = MagicMock()
        local.model_key = "local-test"
        local.complete = MagicMock(return_value=self._resp("ESCALATE", 0.3, "local unsure"))
        paid = MagicMock()
        paid.complete = MagicMock(return_value=self._resp("PASS", 0.9, "paid verdict"))
        self._wire_local(monkeypatch, local)

        v = run_quality_gate("goal", self._steps(), paid, run_adversarial=False)
        assert "paid verdict" in v.reason
        assert paid.complete.call_count >= 1
        assert local.complete.call_count >= 1

    def test_local_failure_is_nonfatal(self, monkeypatch):
        import local_models as _lm
        monkeypatch.setattr(_lm, "configured_models",
                            lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        paid = MagicMock()
        paid.complete = MagicMock(return_value=self._resp("PASS", 0.9, "paid verdict"))

        v = run_quality_gate("goal", self._steps(), paid, run_adversarial=False)
        assert "paid verdict" in v.reason
        assert paid.complete.call_count >= 1

    def test_no_local_configured_uses_paid_directly(self, monkeypatch):
        import local_models as _lm
        monkeypatch.setattr(_lm, "configured_models", lambda: [])
        paid = MagicMock()
        paid.complete = MagicMock(return_value=self._resp("PASS", 0.9, "paid verdict"))

        v = run_quality_gate("goal", self._steps(), paid, run_adversarial=False)
        assert "paid verdict" in v.reason
        assert paid.complete.call_count >= 1
