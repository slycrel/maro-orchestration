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
    _EVIDENCE_LENSES,
    _lens_evidence_transcript,
    _lens_evidence_artifact,
    _probe_contested_claims,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _hosted_free_off(monkeypatch):
    """Hermetic default: the hosted-free tier is OFF unless a test opts in.

    The box this suite runs on has live hosted-free keys — without this pin,
    council/gate tests would make real network calls (and their pass/fail
    would depend on box consent state). Tests that want the tier re-patch
    hosted_free.available/build_hosted_free_adapter themselves (5a precedent).
    """
    import hosted_free
    monkeypatch.setattr(hosted_free, "available", lambda: False)


def _make_step(status="done", text="do something", result="result text"):
    s = SimpleNamespace(status=status, text=text, result=result, index=1)
    return s


def _make_adapter(content: str):
    resp = SimpleNamespace(content=content, input_tokens=10, output_tokens=20)
    adapter = MagicMock()
    adapter.complete.return_value = resp
    # Pin attribution attrs — MagicMock would auto-create truthy Mocks and
    # persona_dispatch._adapter_source would misread the seat as hosted-free.
    adapter._active_provider = ""
    adapter.model_key = "stub-model"
    return adapter


def _hosted_on(monkeypatch, hosted_adapter):
    """Opt a test into the hosted-free tier with the given mock adapter."""
    import hosted_free
    monkeypatch.setattr(hosted_free, "available", lambda: True)
    monkeypatch.setattr(hosted_free, "build_hosted_free_adapter",
                        lambda: hosted_adapter)


def _capture_events(monkeypatch):
    captured: list = []

    def fake_log_event(event_type, *, subject, summary, context=None,
                       note=None, loop_id=None, related_ids=None):
        captured.append({
            "event_type": event_type, "subject": subject, "summary": summary,
            "context": context or {}, "loop_id": loop_id,
        })
        return {}

    import captains_log as _cl
    monkeypatch.setattr(_cl, "log_event", fake_log_event)
    return captured


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
# Evidence-path lenses (chunk 5b — seats differ in evidence, not costume)
# ---------------------------------------------------------------------------

class TestEvidenceLenses:
    def test_three_lenses_exist(self):
        assert len(_EVIDENCE_LENSES) == 3

    def test_lens_names(self):
        names = [l[0] for l in _EVIDENCE_LENSES]
        assert names == ["transcript_aware", "artifact_only", "probe_armed"]

    def test_lens_prompts_rendered(self):
        # The {code_instruction}/{json_contract} placeholders must be gone
        # and every seat must carry the typed finding-code vocabulary.
        for name, prompt, _builder in _EVIDENCE_LENSES:
            assert len(prompt) > 100, f"{name} prompt too short"
            assert "{code_instruction}" not in prompt
            assert "{json_contract}" not in prompt
            assert "FINDING[PHANTOM_SYMBOL]" in prompt, f"{name} missing code vocab"

    def test_probe_lens_demands_settled_by_command(self):
        prompt = dict((n, p) for n, p, _b in _EVIDENCE_LENSES)["probe_armed"]
        assert "settled_by_command" in prompt

    def test_evidence_builders_diverge(self):
        # The whole point: seats see DIFFERENT evidence for the same run.
        steps = [
            _make_step(text="step one text", result="early result"),
            _make_step(text="step two text", result="FINAL DELIVERABLE BODY"),
        ]
        transcript = _lens_evidence_transcript("goal", steps)
        artifact = _lens_evidence_artifact("goal", steps)
        # Transcript seat sees the trail...
        assert "step one text" in transcript
        assert "Run transcript" in transcript
        # ...the context-blind seat sees only the final deliverable.
        assert "step one text" not in artifact
        assert "early result" not in artifact
        assert "FINAL DELIVERABLE BODY" in artifact


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
# Council ladder semantics — hosted-free first, paid confirmation acts
# ---------------------------------------------------------------------------

_WEAK = '{"verdict": "WEAK", "concerns": ["FINDING[GAP_UNDERSTATED] thin"], "most_critical_gap": "gap"}'
_STRONG = '{"verdict": "STRONG", "concerns": [], "most_critical_gap": ""}'


class TestCouncilLadder:
    def test_free_agreement_never_touches_paid(self, monkeypatch):
        _capture_events(monkeypatch)
        hosted = _make_adapter(_STRONG)
        hosted._active_provider = "groq"
        hosted.model_key = "llama-3.1-8b-instant"
        _hosted_on(monkeypatch, hosted)
        paid = _make_adapter(_WEAK)
        v = run_llm_council("goal", [_make_step()], adapter=paid)
        assert v.escalate is False
        assert hosted.complete.call_count == 3
        paid.complete.assert_not_called()
        assert v.source.startswith("hosted_free:groq:")

    def test_free_flag_needs_paid_confirmation_to_act(self, monkeypatch):
        _capture_events(monkeypatch)
        hosted = _make_adapter(_WEAK)
        hosted._active_provider = "groq"
        hosted.model_key = "llama-3.1-8b-instant"
        _hosted_on(monkeypatch, hosted)
        paid = _make_adapter(_WEAK)
        paid.model_key = "claude-sonnet-4-6"
        v = run_llm_council("goal", [_make_step()], adapter=paid)
        # Free flagged, paid confirmed — escalation acts, attribution is paid.
        assert v.escalate is True
        assert paid.complete.call_count == 3
        assert v.source == "claude-sonnet-4-6"

    def test_paid_round_overrules_free_flag(self, monkeypatch):
        _capture_events(monkeypatch)
        hosted = _make_adapter(_WEAK)
        _hosted_on(monkeypatch, hosted)
        paid = _make_adapter(_STRONG)
        v = run_llm_council("goal", [_make_step()], adapter=paid)
        assert v.escalate is False
        assert paid.complete.call_count == 3

    def test_free_flag_without_paid_adapter_is_flag_only(self, monkeypatch):
        captured = _capture_events(monkeypatch)
        hosted = _make_adapter(_WEAK)
        _hosted_on(monkeypatch, hosted)
        v = run_llm_council("goal", [_make_step()], adapter=None)
        # Weaker family flagged with nothing stronger to confirm — recorded,
        # never acted on (weaker-never-acts).
        assert v.escalate is False
        assert v.weak_count == 3
        ev = [e for e in captured if e["event_type"] == "QUALITY_GATE_COUNCIL"]
        assert len(ev) == 1
        assert ev[0]["context"]["free_flag_unconfirmed"] is True

    def test_degraded_free_tier_falls_back_to_paid(self, monkeypatch):
        _capture_events(monkeypatch)
        hosted = _make_adapter("total garbage, no json")
        _hosted_on(monkeypatch, hosted)
        paid = _make_adapter(_WEAK)
        v = run_llm_council("goal", [_make_step()], adapter=paid)
        # All free seats unparsable — paid round runs and its flag acts.
        assert paid.complete.call_count == 3
        assert v.escalate is True

    def test_council_event_carries_seats_and_codes(self, monkeypatch):
        captured = _capture_events(monkeypatch)
        adapter = _make_adapter(_WEAK)
        v = run_llm_council("goal", [_make_step()], adapter=adapter,
                            loop_id="loop-42")
        assert v.escalate is True
        ev = [e for e in captured if e["event_type"] == "QUALITY_GATE_COUNCIL"]
        assert len(ev) == 1
        ctx = ev[0]["context"]
        assert ev[0]["loop_id"] == "loop-42"
        assert [s["lens"] for s in ctx["seats"]] == [
            "transcript_aware", "artifact_only", "probe_armed"]
        assert ctx["seats"][0]["finding_codes"] == ["GAP_UNDERSTATED"]
        assert ctx["escalate"] is True

    def test_critique_source_stamped(self):
        adapter = _make_adapter(_STRONG)
        adapter.model_key = "claude-sonnet-4-6"
        v = run_llm_council("goal", [_make_step()], adapter=adapter)
        assert all(c.source == "claude-sonnet-4-6" for c in v.critiques)

    def test_confirmation_event_keeps_free_round_seats(self, monkeypatch):
        # 5b adversarial-review pin (unanimous finding): when paid
        # confirmation acts, the free round's per-seat evidence must survive
        # in the event — it IS the A/B data, not just a weak count.
        captured = _capture_events(monkeypatch)
        hosted = _make_adapter(_WEAK)
        hosted._active_provider = "groq"
        hosted.model_key = "llama-3.1-8b-instant"
        _hosted_on(monkeypatch, hosted)
        paid = _make_adapter(_STRONG)
        paid.model_key = "claude-sonnet-4-6"
        v = run_llm_council("goal", [_make_step()], adapter=paid)
        assert v.escalate is False
        ev = [e for e in captured if e["event_type"] == "QUALITY_GATE_COUNCIL"]
        ctx = ev[0]["context"]
        assert "confirmed_by_paid" in ev[0]["summary"]
        # Acting seats are the paid round...
        assert all(s["source"] == "claude-sonnet-4-6" for s in ctx["seats"])
        # ...and the free round survives per-seat, codes and all.
        assert [s["lens"] for s in ctx["free_seats"]] == [
            "transcript_aware", "artifact_only", "probe_armed"]
        assert all(s["verdict"] == "WEAK" for s in ctx["free_seats"])
        assert all(s["source"].startswith("hosted_free:groq:")
                   for s in ctx["free_seats"])
        assert ctx["free_seats"][0]["finding_codes"] == ["GAP_UNDERSTATED"]
        assert ctx["free_round_weak"] == 3

    def test_empty_paid_confirmation_is_not_confirmation(self, monkeypatch):
        # 5b adversarial-review pin: paid round returning zero parsable votes
        # must record as an UNCONFIRMED free flag, not as "confirmed_by_paid"
        # — "paid disagreed" and "paid failed to vote" never conflate.
        captured = _capture_events(monkeypatch)
        hosted = _make_adapter(_WEAK)
        hosted._active_provider = "groq"
        hosted.model_key = "llama-3.1-8b-instant"
        _hosted_on(monkeypatch, hosted)
        paid = _make_adapter("total garbage, no json")
        v = run_llm_council("goal", [_make_step()], adapter=paid)
        assert paid.complete.call_count == 3  # confirmation was attempted
        assert v.escalate is False            # but never acts on free alone
        ev = [e for e in captured if e["event_type"] == "QUALITY_GATE_COUNCIL"]
        assert "confirmed_by_paid" not in ev[0]["summary"]
        ctx = ev[0]["context"]
        assert ctx["confirmation_ran"] is True
        assert ctx["free_flag_unconfirmed"] is True
        # Acting seats fall back to the free round (flag-only record).
        assert all(s["source"].startswith("hosted_free:groq:")
                   for s in ctx["seats"])


# ---------------------------------------------------------------------------
# Probe-armed seat — its own probes can refute its concerns
# ---------------------------------------------------------------------------

_PROBE_WEAK = (
    '{"verdict": "WEAK", "concerns": ['
    '{"claim": "config file missing", "settled_by_command": "test -f cfg"}], '
    '"most_critical_gap": "missing config"}'
)


class TestProbeArmedLens:
    def _responses(self, probe_json):
        # Seat order: transcript_aware, artifact_only, probe_armed.
        return [
            SimpleNamespace(content=_STRONG, input_tokens=1, output_tokens=1),
            SimpleNamespace(content=_STRONG, input_tokens=1, output_tokens=1),
            SimpleNamespace(content=probe_json, input_tokens=1, output_tokens=1),
        ]

    def test_dismissed_probe_downgrades_weak(self, monkeypatch):
        import quality_gate as qg
        _capture_events(monkeypatch)
        monkeypatch.setattr(qg, "_probe_contested_claims", lambda claims: [
            {**c, "probe_status": "dismissed"} for c in claims])
        adapter = MagicMock()
        adapter.complete.side_effect = self._responses(_PROBE_WEAK)
        v = run_llm_council("goal", [_make_step()], adapter=adapter)
        probe_seat = [c for c in v.critiques if c.critic == "probe_armed"][0]
        # WEAK rested entirely on a probe-dismissed concern → ACCEPTABLE.
        assert probe_seat.verdict == "ACCEPTABLE"
        assert probe_seat.probe_dismissed == 1
        assert probe_seat.concerns == []
        assert v.escalate is False

    def test_validated_probe_keeps_weak(self, monkeypatch):
        import quality_gate as qg
        _capture_events(monkeypatch)
        monkeypatch.setattr(qg, "_probe_contested_claims", lambda claims: [
            {**c, "probe_status": "validated"} for c in claims])
        adapter = MagicMock()
        adapter.complete.side_effect = self._responses(_PROBE_WEAK)
        v = run_llm_council("goal", [_make_step()], adapter=adapter)
        probe_seat = [c for c in v.critiques if c.critic == "probe_armed"][0]
        assert probe_seat.verdict == "WEAK"
        assert probe_seat.concerns == ["config file missing [probe:validated]"]

    def test_string_concerns_tolerated_without_probes(self, monkeypatch):
        import quality_gate as qg
        _capture_events(monkeypatch)
        probes_run = []
        monkeypatch.setattr(qg, "_probe_contested_claims",
                            lambda claims: probes_run.append(claims) or claims)
        adapter = _make_adapter(_WEAK)  # plain-string concerns everywhere
        v = run_llm_council("goal", [_make_step()], adapter=adapter)
        assert probes_run == []  # nothing dict-shaped → no subprocess probes
        assert len(v.critiques) == 3

    def test_string_concerns_tagged_unprobed(self, monkeypatch):
        # 5b adversarial-review pin: a probe seat that ignores its dict
        # contract keeps its concerns (conservatism — absence of a probe
        # never silences a claim) but they must be VISIBLY unprobed, not
        # indistinguishable from probe-survived ones.
        import quality_gate as qg
        _capture_events(monkeypatch)
        monkeypatch.setattr(qg, "_probe_contested_claims",
                            lambda claims: claims)
        adapter = _make_adapter(_WEAK)
        v = run_llm_council("goal", [_make_step()], adapter=adapter)
        probe_seat = [c for c in v.critiques if c.critic == "probe_armed"][0]
        assert probe_seat.verdict == "WEAK"
        assert probe_seat.concerns == [
            "FINDING[GAP_UNDERSTATED] thin [probe:unprobed]"]


# ---------------------------------------------------------------------------
# Cross-ref lanes — strict: acts, research hosted-free lane is flag-only
# ---------------------------------------------------------------------------

class TestCrossRefLanes:
    def _gate_pass_adapter(self):
        return _make_adapter('{"verdict": "PASS", "reason": "ok", "confidence": 0.9}')

    def _report(self, n_disputes):
        from cross_ref import CrossRefReport, ClaimVerification
        disputes = [
            ClaimVerification(claim=f"claim {i}", category="statistic",
                              status="disputed", confidence=0.9, note="off")
            for i in range(n_disputes)
        ]
        return CrossRefReport(verified=disputes, claims_extracted=n_disputes,
                              claims_checked=n_disputes, disputes=disputes)

    def _force_cr_config(self, monkeypatch, value):
        import config as _config
        real_get = _config.get

        def fake_get(key, default=None):
            if key == "quality_gate.cross_ref_research":
                return value
            return real_get(key, default)

        monkeypatch.setattr(_config, "get", fake_get)

    def test_paid_lane_disputes_act(self, monkeypatch):
        _capture_events(monkeypatch)
        import cross_ref as _cr
        monkeypatch.setattr(_cr, "run_cross_ref",
                            lambda text, adapter=None, **kw: self._report(2))
        v = run_quality_gate("goal", [_make_step()], self._gate_pass_adapter(),
                             run_adversarial=False, run_cross_ref=True,
                             _ladder=False)
        assert v.escalate is True
        assert v.verdict == "ESCALATE"

    def test_hosted_lane_disputes_are_flag_only(self, monkeypatch):
        captured = _capture_events(monkeypatch)
        import cross_ref as _cr
        seen_adapters = []

        def fake_run(text, adapter=None, **kw):
            seen_adapters.append(adapter)
            return self._report(2)

        monkeypatch.setattr(_cr, "run_cross_ref", fake_run)
        hosted = MagicMock()
        hosted._active_provider = "gemini"
        hosted.model_key = "gemini-flash-lite-latest"
        _hosted_on(monkeypatch, hosted)
        self._force_cr_config(monkeypatch, True)
        v = run_quality_gate("research goal", [_make_step()],
                             self._gate_pass_adapter(),
                             run_adversarial=False,
                             run_cross_ref="hosted_free", _ladder=False)
        # Disputes recorded, verdict untouched — weaker family never acts.
        assert v.verdict == "PASS"
        assert v.escalate is False
        assert v.cross_ref is not None
        assert seen_adapters == [hosted]
        ev = [e for e in captured if e["event_type"] == "QUALITY_GATE_CROSS_REF"]
        assert len(ev) == 1
        assert ev[0]["context"]["lane"] == "hosted_free"
        assert ev[0]["context"]["acted"] is False
        assert ev[0]["context"]["disputes"] == 2
        assert ev[0]["context"]["source"].startswith("hosted_free:gemini:")

    def test_hosted_lane_inert_without_consent(self, monkeypatch):
        # hosted-free OFF (autouse default) → the research lane does nothing.
        _capture_events(monkeypatch)
        import cross_ref as _cr
        called = []
        monkeypatch.setattr(_cr, "run_cross_ref",
                            lambda *a, **kw: called.append(1) or self._report(0))
        self._force_cr_config(monkeypatch, True)
        v = run_quality_gate("research goal", [_make_step()],
                             self._gate_pass_adapter(),
                             run_adversarial=False,
                             run_cross_ref="hosted_free", _ladder=False)
        assert called == []
        assert v.cross_ref is None

    def test_hosted_lane_zero_claims_still_emits_event(self, monkeypatch):
        # 5b adversarial-review pin: "ran and found nothing" must reach the
        # captain's log — zero-claim runs are the readout's denominator.
        captured = _capture_events(monkeypatch)
        import cross_ref as _cr
        monkeypatch.setattr(_cr, "run_cross_ref",
                            lambda text, adapter=None, **kw: self._report(0))
        hosted = MagicMock()
        hosted._active_provider = "gemini"
        hosted.model_key = "gemini-flash-lite-latest"
        _hosted_on(monkeypatch, hosted)
        self._force_cr_config(monkeypatch, True)
        v = run_quality_gate("research goal", [_make_step()],
                             self._gate_pass_adapter(),
                             run_adversarial=False,
                             run_cross_ref="hosted_free", _ladder=False)
        assert v.cross_ref is not None
        ev = [e for e in captured if e["event_type"] == "QUALITY_GATE_CROSS_REF"]
        assert len(ev) == 1
        assert ev[0]["context"]["claims_extracted"] == 0
        assert ev[0]["context"]["disputes"] == 0
        assert ev[0]["context"]["acted"] is False

    def test_hosted_lane_killswitch_quoted_false(self, monkeypatch):
        # DEFAULTS discipline: a quoted "false" must actually kill the lane.
        _capture_events(monkeypatch)
        import cross_ref as _cr
        called = []
        monkeypatch.setattr(_cr, "run_cross_ref",
                            lambda *a, **kw: called.append(1) or self._report(0))
        hosted = MagicMock()
        _hosted_on(monkeypatch, hosted)
        self._force_cr_config(monkeypatch, "false")
        run_quality_gate("research goal", [_make_step()],
                         self._gate_pass_adapter(),
                         run_adversarial=False,
                         run_cross_ref="hosted_free", _ladder=False)
        assert called == []


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
        # Command must pass the read-only guard to reach execution at all.
        claim = {"claim": "x", "verdict": "CONTESTED",
                 "settled_by_command": "grep -r pattern /huge/tree"}
        [out] = _probe_contested_claims([claim])
        assert out["probe_status"] == "unrunnable"
        assert out["verdict"] == "CONTESTED"  # don't grant either side
        assert "timeout" in out["probe_output_preview"].lower()

    def test_probe_exception_is_unrunnable(self, monkeypatch):
        import subprocess as _sp
        def _raise(*a, **kw):
            raise OSError("simulated exec failure")
        monkeypatch.setattr(_sp, "run", _raise)
        # Command must pass the read-only guard to reach execution at all.
        claim = {"claim": "x", "verdict": "CONTESTED",
                 "settled_by_command": "test -f some/file"}
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




# ---------------------------------------------------------------------------
# Pass 1.5 — hosted-free second-family check (chunk 5a, stack-don't-substitute)
# ---------------------------------------------------------------------------

class TestSecondFamilyCheck:
    """The stacked second-family check is flag-only: it records agreement
    evidence and may never change the paid gate's verdict or escalate."""

    def _paid_adapter(self, verdict="PASS", confidence=0.9):
        return _make_adapter(
            f'{{"verdict": "{verdict}", "reason": "judged", "confidence": {confidence}}}'
        )

    def _hosted(self, monkeypatch, content=None, *, raises=None, min_cert=0.6):
        import hosted_free
        fake = MagicMock()
        fake._active_provider = "groq"
        fake.model_key = "llama-3.1-8b-instant"
        if raises is not None:
            fake.complete.side_effect = raises
        else:
            fake.complete.return_value = SimpleNamespace(content=content or "")
        monkeypatch.setattr(hosted_free, "available", lambda: True)
        monkeypatch.setattr(hosted_free, "build_hosted_free_adapter", lambda: fake)
        monkeypatch.setattr(hosted_free, "min_certainty", lambda: min_cert)
        # Hermeticity (chunk-5a review F4): force the killswitch ON so the
        # positive-path tests don't silently exercise the skip path when the
        # box config carries `quality_gate.second_family_check: false`.
        self._force_config(monkeypatch, True)
        return fake

    @staticmethod
    def _force_config(monkeypatch, value):
        import config as config_module
        real_get = config_module.get

        def forced_get(key, default=None):
            if key == "quality_gate.second_family_check":
                return value
            return real_get(key, default)

        monkeypatch.setattr(config_module, "get", forced_get)

    def _captured_events(self, monkeypatch):
        captured = []

        def fake_log_event(event_type, subject="", summary="", context=None,
                           note=None, loop_id=None, related_ids=None, **kw):
            captured.append({"event_type": event_type, "summary": summary,
                             "context": context or {}, "loop_id": loop_id})
            return {}

        import captains_log as _cl
        monkeypatch.setattr(_cl, "log_event", fake_log_event)
        return captured

    def test_second_family_defaults_none(self):
        v = QualityVerdict("PASS", "ok", 0.9, False)
        assert v.second_family is None

    def test_dissent_is_flag_only(self, monkeypatch):
        events = self._captured_events(monkeypatch)
        hosted = self._hosted(
            monkeypatch,
            '{"verdict": "ESCALATE", "reason": "shallow", "confidence": 0.9}')
        v = run_quality_gate("goal", [_make_step()], self._paid_adapter(),
                             run_adversarial=False, loop_id="loop-sf-1")
        # Flag only — the paid verdict is untouched.
        assert v.verdict == "PASS"
        assert v.escalate is False
        assert v.second_family["decision"] == "SECOND_FAMILY_DISSENT"
        assert v.second_family["verdict"] == "ESCALATE"
        assert hosted.complete.call_count == 1
        sf = [e for e in events if e["event_type"] == "QUALITY_GATE_SECOND_FAMILY"]
        assert len(sf) == 1
        assert sf[0]["context"]["decision"] == "SECOND_FAMILY_DISSENT"
        assert sf[0]["context"]["paid_verdict"] == "PASS"
        assert sf[0]["context"]["source"].startswith("hosted_free:groq:")
        assert sf[0]["loop_id"] == "loop-sf-1"

    def test_agreement_recorded(self, monkeypatch):
        events = self._captured_events(monkeypatch)
        self._hosted(
            monkeypatch,
            '{"verdict": "PASS", "reason": "fine", "confidence": 0.8}')
        v = run_quality_gate("goal", [_make_step()], self._paid_adapter(),
                             run_adversarial=False)
        assert v.second_family["decision"] == "SECOND_FAMILY_AGREE"
        sf = [e for e in events if e["event_type"] == "QUALITY_GATE_SECOND_FAMILY"]
        assert len(sf) == 1

    def test_low_confidence_escalate_is_undecided(self, monkeypatch):
        # min_certainty semantics from the validator ladder: a weak judge
        # cannot flag — ESCALATE below the bar is UNDECIDED, not DISSENT.
        self._captured_events(monkeypatch)
        self._hosted(
            monkeypatch,
            '{"verdict": "ESCALATE", "reason": "hmm", "confidence": 0.3}',
            min_cert=0.6)
        v = run_quality_gate("goal", [_make_step()], self._paid_adapter(),
                             run_adversarial=False)
        assert v.second_family["decision"] == "SECOND_FAMILY_UNDECIDED"
        assert v.escalate is False

    def test_garbage_response_is_no_verdict(self, monkeypatch):
        # Still emitted — the agreement readout needs the true denominator.
        events = self._captured_events(monkeypatch)
        self._hosted(monkeypatch, "I cannot help with that.")
        v = run_quality_gate("goal", [_make_step()], self._paid_adapter(),
                             run_adversarial=False)
        assert v.second_family["decision"] == "SECOND_FAMILY_NO_VERDICT"
        sf = [e for e in events if e["event_type"] == "QUALITY_GATE_SECOND_FAMILY"]
        assert len(sf) == 1

    def test_paid_escalate_skips_check(self, monkeypatch):
        self._captured_events(monkeypatch)
        hosted = self._hosted(monkeypatch, '{"verdict": "PASS"}')
        v = run_quality_gate("goal", [_make_step()],
                             self._paid_adapter(verdict="ESCALATE"),
                             run_adversarial=False)
        assert v.second_family is None
        hosted.complete.assert_not_called()

    def test_weak_escalate_skips_check(self, monkeypatch):
        # Paid verdict ESCALATE under threshold (decision WEAK_ESCALATE) is
        # not a PASS — the stacked check runs on paid PASS only.
        self._captured_events(monkeypatch)
        hosted = self._hosted(monkeypatch, '{"verdict": "PASS"}')
        v = run_quality_gate("goal", [_make_step()],
                             self._paid_adapter(verdict="ESCALATE", confidence=0.5),
                             run_adversarial=False)
        assert v.second_family is None
        hosted.complete.assert_not_called()

    def test_unavailable_tier_is_inert(self, monkeypatch):
        import hosted_free
        self._captured_events(monkeypatch)
        monkeypatch.setattr(hosted_free, "available", lambda: False)
        build = MagicMock()
        monkeypatch.setattr(hosted_free, "build_hosted_free_adapter", build)
        v = run_quality_gate("goal", [_make_step()], self._paid_adapter(),
                             run_adversarial=False)
        assert v.verdict == "PASS"
        assert v.second_family is None
        build.assert_not_called()

    def test_config_off_skips(self, monkeypatch):
        self._captured_events(monkeypatch)
        hosted = self._hosted(monkeypatch, '{"verdict": "PASS"}')
        self._force_config(monkeypatch, False)
        v = run_quality_gate("goal", [_make_step()], self._paid_adapter(),
                             run_adversarial=False)
        assert v.second_family is None
        hosted.complete.assert_not_called()

    def test_config_quoted_false_string_disables(self, monkeypatch):
        # Review F1 pin (unanimous): config.get returns raw YAML nodes, so a
        # quoted "false" arrives as a truthy string — the killswitch must
        # normalize it like hosted_free_enabled() does.
        self._captured_events(monkeypatch)
        hosted = self._hosted(monkeypatch, '{"verdict": "PASS"}')
        self._force_config(monkeypatch, "false")
        v = run_quality_gate("goal", [_make_step()], self._paid_adapter(),
                             run_adversarial=False)
        assert v.second_family is None
        hosted.complete.assert_not_called()

    def test_ladder_false_skips(self, monkeypatch):
        self._captured_events(monkeypatch)
        hosted = self._hosted(monkeypatch, '{"verdict": "PASS"}')
        v = run_quality_gate("goal", [_make_step()], self._paid_adapter(),
                             run_adversarial=False, _ladder=False)
        assert v.second_family is None
        hosted.complete.assert_not_called()

    def test_transport_failure_non_fatal(self, monkeypatch):
        events = self._captured_events(monkeypatch)
        self._hosted(monkeypatch, raises=RuntimeError("all providers tripped"))
        v = run_quality_gate("goal", [_make_step()], self._paid_adapter(),
                             run_adversarial=False)
        assert v.verdict == "PASS"
        assert v.second_family is None
        sf = [e for e in events if e["event_type"] == "QUALITY_GATE_SECOND_FAMILY"]
        assert sf == []
