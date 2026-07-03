"""Blocked-step escalate cutover (2026-07-03, dumb-loop audit rounds 2-4).

Unit tests for `loop_blocked._navigator_act_blocked_step` — the act path that
lets a high-confidence navigator escalate override a FORWARD heuristic
recovery decision with an honest stop — and for the shadow tap's OR-gate
(`navigator.act_blocked_step` implies the navigator call fires even with the
shadow flag off, so the audit trail keeps accruing).
"""

from types import SimpleNamespace

import pytest

import loop_blocked as _lb


def _nav(move="escalate", confidence=0.95, reasoning="doomed block"):
    return SimpleNamespace(move=move, confidence=confidence, reasoning=reasoning)


def _forward_decision():
    return _lb._BlockDecision(
        retry=True, hint="try again", loop_status="", stuck_reason="",
    )


def _terminal_decision():
    return _lb._BlockDecision(
        retry=False, hint="", loop_status="stuck", stuck_reason="MISSING_INPUT: gone",
    )


def _cfg(overrides):
    def _get(key, default=None):
        return overrides.get(key, default)
    return _get


class TestActBlockedStep:
    def test_default_off_returns_none(self, monkeypatch):
        monkeypatch.setattr("config.get", _cfg({}))
        out = _lb._navigator_act_blocked_step(
            _nav(), _forward_decision(), goal="g", step_text="s", step_idx=1)
        assert out is None

    def test_escalate_above_floor_overrides_forward(self, monkeypatch):
        monkeypatch.setattr("config.get", _cfg({"navigator.act_blocked_step": True}))
        events = []
        monkeypatch.setattr(
            "captains_log.log_event",
            lambda event_type=None, **kw: events.append((event_type, kw)))
        notes = []
        monkeypatch.setattr("notify.emit", lambda kind, payload: notes.append((kind, payload)))
        out = _lb._navigator_act_blocked_step(
            _nav(confidence=0.95), _forward_decision(),
            goal="g", step_text="s", step_idx=3, loop_id="abc123")
        assert out is not None
        assert out.retry is False
        assert out.loop_status == "stuck"
        assert out.stuck_reason.startswith("NAVIGATOR_ESCALATE:")
        # audit event + human signal both fired
        assert any("NAVIGATOR_ACTED" in str(e[0]) for e in events)
        acted = next(kw for et, kw in events if "NAVIGATOR_ACTED" in str(et))
        assert acted["context"]["point"] == "blocked_step"
        assert acted["context"]["heuristic_action"] == "retry"
        assert notes and notes[0][0] == "escalation"
        assert notes[0][1]["point"] == "blocked_step"

    def test_below_floor_falls_through(self, monkeypatch):
        monkeypatch.setattr("config.get", _cfg({"navigator.act_blocked_step": True}))
        out = _lb._navigator_act_blocked_step(
            _nav(confidence=0.85), _forward_decision(),
            goal="g", step_text="s", step_idx=1)
        assert out is None

    def test_non_escalate_moves_fall_through(self, monkeypatch):
        monkeypatch.setattr("config.get", _cfg({"navigator.act_blocked_step": True}))
        for move in ("execute", "extend", "fork", "close", "collate"):
            out = _lb._navigator_act_blocked_step(
                _nav(move=move, confidence=0.99), _forward_decision(),
                goal="g", step_text="s", step_idx=1)
            assert out is None, move

    def test_terminal_heuristic_not_double_acted(self, monkeypatch):
        """If the heuristic already stopped, its honest reason stands."""
        monkeypatch.setattr("config.get", _cfg({"navigator.act_blocked_step": True}))
        out = _lb._navigator_act_blocked_step(
            _nav(confidence=0.99), _terminal_decision(),
            goal="g", step_text="s", step_idx=1)
        assert out is None

    def test_custom_floor_respected(self, monkeypatch):
        monkeypatch.setattr("config.get", _cfg({
            "navigator.act_blocked_step": True,
            "navigator.act_confidence_floor": 0.99,
        }))
        out = _lb._navigator_act_blocked_step(
            _nav(confidence=0.95), _forward_decision(),
            goal="g", step_text="s", step_idx=1)
        assert out is None

    def test_none_inputs_never_raise(self, monkeypatch):
        monkeypatch.setattr("config.get", _cfg({"navigator.act_blocked_step": True}))
        assert _lb._navigator_act_blocked_step(
            None, _forward_decision(), goal="g", step_text="s", step_idx=1) is None
        assert _lb._navigator_act_blocked_step(
            _nav(), None, goal="g", step_text="s", step_idx=1) is None

    def test_split_and_redecompose_count_as_forward(self, monkeypatch):
        monkeypatch.setattr("config.get", _cfg({"navigator.act_blocked_step": True}))
        monkeypatch.setattr("captains_log.log_event", lambda **kw: None)
        monkeypatch.setattr("notify.emit", lambda kind, payload: None)
        split = _lb._BlockDecision(
            retry=False, hint="", loop_status="", stuck_reason="",
            split_into=["a", "b"])
        redecomp = _lb._BlockDecision(
            retry=False, hint="", loop_status="", stuck_reason="",
            redecompose=True)
        for dec in (split, redecomp):
            out = _lb._navigator_act_blocked_step(
                _nav(confidence=0.95), dec, goal="g", step_text="s", step_idx=1)
            assert out is not None
            assert out.loop_status == "stuck"


class TestShadowGateOr:
    """act_blocked_step alone must open the shadow tap's gate."""

    def _decide_stub(self, decision):
        def _decide(nav_input, tiers=None, adapter_factory=None,
                    shadow=True, pipeline_actual=None):
            return decision, {}
        return _decide

    def test_act_flag_alone_fires_navigator(self, monkeypatch):
        from navigator_shadow import shadow_blocked_step_live
        monkeypatch.setattr("config.get", _cfg({
            "navigator.shadow_blocked_step": False,
            "navigator.act_blocked_step": True,
            "navigator.shadow_tiers": ["cheap"],
        }))
        want = _nav()
        monkeypatch.setattr("navigator_prompt.decide", self._decide_stub(want))
        got = shadow_blocked_step_live(
            "goal", heuristic_action="retry", block_reason="b",
            signals={"retries": 1}, turn_index=2)
        assert got is want

    def test_both_flags_off_skips(self, monkeypatch):
        from navigator_shadow import shadow_blocked_step_live
        monkeypatch.setattr("config.get", _cfg({
            "navigator.shadow_blocked_step": False,
            "navigator.act_blocked_step": False,
        }))
        called = []
        monkeypatch.setattr("navigator_prompt.decide",
                            lambda *a, **kw: called.append(1) or (None, {}))
        got = shadow_blocked_step_live(
            "goal", heuristic_action="retry", block_reason="b",
            signals={}, turn_index=1)
        assert got is None
        assert not called
