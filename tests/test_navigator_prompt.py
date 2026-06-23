"""Tests for the navigator prompt seam + shadow replay (goal-brain step 5).

All LLM behavior is faked at the adapter seam — conftest blocks the real
CLIs and these tests never build a live adapter.
"""
import json

import pytest

import captains_log
from navigator import ChildSummary, NavigatorInput, WorkReport
from navigator_prompt import decide, render_input
from navigator_shadow import input_from_run, replay_run, resolve_run_dir


def _resp(move, reasoning="because", confidence=0.7, **payload):
    return json.dumps({
        "move": move, "reasoning": reasoning,
        "confidence": confidence, "payload": payload,
    })


class _FakeAdapter:
    """Returns scripted responses in order; repeats the last one."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def complete(self, messages, **kwargs):
        self.calls.append(messages)
        text = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]

        class R:
            content = text
        return R()


def _factory_for(tier_map, built):
    """tier_map: tier -> list of scripted responses."""
    def factory(tier):
        built.append(tier)
        return _FakeAdapter(tier_map[tier])
    return factory


def _nav_input(**kw):
    defaults = dict(goal="check reddit for a used thinkpad")
    defaults.update(kw)
    return NavigatorInput(**defaults)


class TestRenderInput:
    def test_sections_always_present(self):
        text = render_input(_nav_input())
        for header in ("## Goal (verbatim)", "## Goal context", "## Ancestry",
                       "## Turn", "## Last work turn", "## Open children",
                       "## What the system already knows"):
            assert header in text
        assert "(none — no work has run yet)" in text
        assert "(nothing relevant on record)" in text

    def test_work_report_and_children_rendered(self):
        text = render_input(_nav_input(
            last_work=WorkReport(
                move="execute", status="failed", summary="fetch died",
                recommendation="retry with backoff", signals={"errors": 2}),
            open_children=[ChildSummary("c1", "check craigslist", "failed")],
            recall_block="Prior attempts: 3 runs, all stuck.",
        ))
        assert "fetch died" in text
        assert "advisory, not binding" in text
        assert "c1 [failed] check craigslist" in text
        assert "Prior attempts: 3 runs" in text


class TestDecideTierChain:
    def test_first_tier_decides(self):
        built = []
        d, meta = decide(
            _nav_input(),
            tiers=["cheap", "mid"],
            adapter_factory=_factory_for(
                {"cheap": [_resp("execute", instruction="search reddit")]}, built),
        )
        assert d.move == "execute"
        assert meta["tier"] == "cheap"
        assert built == ["cheap"]

    def test_idunno_escalates_tier_and_carries_confusion(self):
        built = []
        tier_map = {
            "cheap": [_resp("idunno", confusion="goal ambiguous",
                            missing=["which model"])],
            "mid": [_resp("execute", instruction="search for thinkpads")],
        }
        d, meta = decide(
            _nav_input(), tiers=["cheap", "mid"],
            adapter_factory=_factory_for(tier_map, built))
        assert d.move == "execute"
        assert meta["tier"] == "mid"
        assert built == ["cheap", "mid"]

    def test_confusion_text_reaches_next_tier(self):
        built = []
        mid_adapter = _FakeAdapter([_resp("execute", instruction="go")])
        cheap_adapter = _FakeAdapter(
            [_resp("idunno", confusion="goal ambiguous")])
        adapters = {"cheap": cheap_adapter, "mid": mid_adapter}

        def factory(tier):
            built.append(tier)
            return adapters[tier]
        decide(_nav_input(), tiers=["cheap", "mid"], adapter_factory=factory)
        mid_user_msg = mid_adapter.calls[0][1].content
        assert "goal ambiguous" in mid_user_msg
        assert "stronger tier" in mid_user_msg

    def test_exhausted_chain_synthesizes_escalate(self):
        built = []
        d, meta = decide(
            _nav_input(), tiers=["cheap", "mid"],
            adapter_factory=_factory_for({
                "cheap": [_resp("idunno", confusion="unclear")],
                "mid": [_resp("idunno", confusion="still unclear")],
            }, built))
        assert d.move == "escalate"
        assert meta["escalated_via"] == "idunno_chain"
        assert "unclear" in d.payload["why"]
        assert d.payload["question"]

    def test_invalid_output_retried_with_feedback_then_fixed(self):
        adapter = _FakeAdapter([
            "I think we should execute.",          # unparseable
            _resp("execute", instruction="do it"),  # corrected
        ])
        d, meta = decide(
            _nav_input(), tiers=["cheap"], adapter_factory=lambda t: adapter)
        assert d.move == "execute"
        assert meta["format_failures"] == 1
        retry_msg = adapter.calls[1][1].content
        assert "previous response was invalid" in retry_msg

    def test_persistent_garbage_counts_as_idunno(self):
        d, meta = decide(
            _nav_input(), tiers=["cheap"],
            adapter_factory=lambda t: _FakeAdapter(["garbage", "more garbage"]))
        assert d.move == "escalate"
        assert meta["escalated_via"] == "idunno_chain"
        assert "no valid decision" in d.payload["why"]

    def test_validation_failure_close_rule_fed_back(self):
        nav_in = _nav_input(open_children=[ChildSummary("c9", "child goal", "open")])
        adapter = _FakeAdapter([
            _resp("close", closure="delivered", verdict="done"),  # missing disposition
            _resp("close", closure="delivered", verdict="done",
                  children_disposition={"c9": "abandoned"}),
        ])
        d, _ = decide(nav_in, tiers=["cheap"], adapter_factory=lambda t: adapter)
        assert d.move == "close"
        assert "undispositioned" in adapter.calls[1][1].content

    def test_decision_instrumented(self, tmp_path, monkeypatch):
        events = []
        monkeypatch.setattr(
            captains_log, "log_event",
            lambda etype, **kw: events.append((etype, kw)))
        decide(
            _nav_input(), tiers=["cheap"],
            adapter_factory=lambda t: _FakeAdapter(
                [_resp("execute", instruction="go")]),
            shadow=True, pipeline_actual={"move_equivalent": "execute"})
        assert len(events) == 1
        etype, kw = events[0]
        assert etype == "NAVIGATOR_DECIDED"
        ctx = kw["context"]
        assert ctx["shadow"] is True
        assert ctx["pipeline_actual"] == {"move_equivalent": "execute"}
        assert ctx["move"] == "execute"
        assert ctx["tier"] == "cheap"


def _make_run(tmp_workspace_run, handle_id, goal, status, started_iso, ended_iso=None):
    from runs import runs_root
    rd = runs_root() / f"{handle_id}-test-{handle_id}"
    (rd / "source").mkdir(parents=True, exist_ok=True)
    (rd / "build").mkdir(parents=True, exist_ok=True)
    (rd / "source" / "prompt.txt").write_text(goal, encoding="utf-8")
    meta = {
        "handle_id": handle_id, "prompt": goal, "lane": "agenda",
        "model": "cheap", "status": status, "started_at": started_iso,
    }
    if ended_iso:
        meta["ended_at"] = ended_iso
    (rd / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    return rd


class TestShadowReplay:
    def test_input_from_run_dispatch_sees_asof_history(self, tmp_path):
        # three earlier failures of the same goal, then the run under replay
        for i, hid in enumerate(("aaa1", "aaa2", "aaa3")):
            _make_run(tmp_path, hid, "verify the claims", "stuck",
                      f"2026-05-17T0{i}:00:00+00:00")
        target = _make_run(tmp_path, "bbb1", "verify the claims", "stuck",
                           "2026-05-17T04:00:00+00:00")
        nav_input, actual = input_from_run(target, point="dispatch")
        assert actual["prior_attempts_asof"] == 3
        assert actual["move_equivalent"] == "execute"
        assert "3 runs" in nav_input.recall_block
        assert nav_input.last_work is None

    def test_asof_excludes_later_runs(self, tmp_path):
        target = _make_run(tmp_path, "ccc1", "verify the claims", "done",
                           "2026-05-17T00:00:00+00:00")
        _make_run(tmp_path, "ccc2", "verify the claims", "stuck",
                  "2026-05-17T02:00:00+00:00")  # AFTER the target
        _, actual = input_from_run(target, point="dispatch")
        assert actual["prior_attempts_asof"] == 0

    def test_closure_point_builds_work_report(self, tmp_path):
        target = _make_run(tmp_path, "ddd1", "read the doc", "done",
                           "2026-05-12T00:00:00+00:00",
                           "2026-05-12T00:05:00+00:00")
        nav_input, actual = input_from_run(target, point="closure")
        assert nav_input.turn_index == 1
        assert nav_input.last_work.status == "ok"
        assert nav_input.last_work.signals["duration_s"] == 300
        assert actual["move_equivalent"] == "ended:done"

    def test_replay_run_end_to_end_with_fake_adapter(self, tmp_path):
        target = _make_run(tmp_path, "eee1", "read the doc and summarize",
                           "done", "2026-05-12T00:00:00+00:00")
        results = replay_run(
            str(target), points=("dispatch",), tiers=["cheap"],
            adapter_factory=lambda t: _FakeAdapter(
                [_resp("execute", instruction="read it")]))
        assert len(results) == 1
        r = results[0]
        assert r["navigator"] == "execute"
        assert r["pipeline"] == "execute"
        assert r["tier"] == "cheap"

    def test_resolve_run_dir_prefix_and_ambiguity(self, tmp_path):
        _make_run(tmp_path, "fff1", "g", "done", "2026-05-12T00:00:00+00:00")
        _make_run(tmp_path, "fff2", "g", "done", "2026-05-12T00:00:00+00:00")
        assert resolve_run_dir("fff1").name.startswith("fff1")
        with pytest.raises(ValueError):
            resolve_run_dir("fff")
        with pytest.raises(FileNotFoundError):
            resolve_run_dir("zzz9")


class TestShadowDispatchLive:
    """shadow_dispatch_live: config gate, never-raises contract, and that
    the guard's RecallResult actually reaches the navigator's prompt."""

    GOAL = "research thinkpad prices on reddit"

    def _cfg(self, overrides):
        def get(name, default=None):
            return overrides.get(name, default)
        return get

    def test_off_by_default_in_code(self):
        from unittest.mock import patch
        from navigator_shadow import shadow_dispatch_live
        built = []
        with patch("config.get", side_effect=self._cfg({})):
            result = shadow_dispatch_live(
                self.GOAL,
                adapter_factory=lambda t: built.append(t) or _FakeAdapter(
                    [_resp("execute", instruction="go")]),
            )
        assert result is None
        assert built == [], "no adapter should be built when the gate is off"

    def test_enabled_returns_decision_and_instruments(self):
        from unittest.mock import patch
        from navigator_shadow import shadow_dispatch_live
        events = []
        with patch("config.get",
                   side_effect=self._cfg({"navigator.shadow_dispatch": True})), \
             patch("captains_log.log_event",
                   side_effect=lambda et, **kw: events.append((et, kw))):
            decision = shadow_dispatch_live(
                self.GOAL,
                pipeline_move="guard_refused",
                extra={"job_id": "task-042"},
                adapter_factory=lambda t: _FakeAdapter(
                    [_resp("escalate", question="why repeat?", why="burn")]),
            )
        assert decision is not None
        assert decision.move == "escalate"
        decided = [kw for et, kw in events if et == "NAVIGATOR_DECIDED"]
        assert len(decided) == 1
        actual = decided[0]["context"]["pipeline_actual"]
        assert actual["live"] is True
        assert actual["move_equivalent"] == "guard_refused"
        assert actual["job_id"] == "task-042"

    def test_recall_result_reaches_prompt(self):
        from unittest.mock import patch
        from recall import PriorAttempt, RecallResult, ThreadIdentity
        from navigator_shadow import shadow_dispatch_live
        rr = RecallResult(
            thread=ThreadIdentity(
                parent_goal="the big mission", parent_handle_id="abc123",
                chain=["abc123"], source="loop_continuation"),
            prior_attempts=[PriorAttempt(
                goal=self.GOAL, handle_id="old1", status="stuck",
                when="2026-06-10T00:00:00+00:00", match="exact")],
        )
        adapter = _FakeAdapter([_resp("execute", instruction="go")])
        with patch("config.get",
                   side_effect=self._cfg({"navigator.shadow_dispatch": True})):
            decision = shadow_dispatch_live(
                self.GOAL, recall_result=rr,
                adapter_factory=lambda t: adapter,
            )
        assert decision is not None
        user_text = adapter.calls[0][-1].content
        assert "the big mission" in user_text
        assert "old1" in user_text or "stuck" in user_text

    def test_never_raises_when_decide_explodes(self):
        from unittest.mock import patch
        from navigator_shadow import shadow_dispatch_live
        with patch("config.get",
                   side_effect=self._cfg({"navigator.shadow_dispatch": True})), \
             patch("navigator_prompt.decide",
                   side_effect=RuntimeError("decide blew up")):
            result = shadow_dispatch_live(self.GOAL)
        assert result is None

    def test_default_tiers_come_from_config(self):
        from unittest.mock import patch
        from navigator_shadow import shadow_dispatch_live
        built = []
        with patch("config.get", side_effect=self._cfg({
                "navigator.shadow_dispatch": True,
                "navigator.shadow_tiers": ["mid"]})):
            shadow_dispatch_live(
                self.GOAL,
                adapter_factory=_factory_for(
                    {"mid": [_resp("execute", instruction="go")]}, built),
            )
        assert built == ["mid"]


class TestShadowBlockedStepLive:
    """shadow_blocked_step_live: the dumb-loop audit priority-1 point.
    Config gate, heuristic->move mapping, instrumentation, never-raises."""

    GOAL = "summarize the quarterly report"

    def _cfg(self, overrides):
        def get(name, default=None):
            return overrides.get(name, default)
        return get

    def test_off_by_default_in_code(self):
        from unittest.mock import patch
        from navigator_shadow import shadow_blocked_step_live
        built = []
        with patch("config.get", side_effect=self._cfg({})):
            result = shadow_blocked_step_live(
                self.GOAL, heuristic_action="retry",
                adapter_factory=lambda t: built.append(t) or _FakeAdapter(
                    [_resp("extend")]),
            )
        assert result is None
        assert built == [], "no adapter should be built when the gate is off"

    def test_records_move_equivalent_and_signals(self):
        from unittest.mock import patch
        from navigator_shadow import shadow_blocked_step_live
        events = []
        with patch("config.get",
                   side_effect=self._cfg({"navigator.shadow_blocked_step": True})), \
             patch("captains_log.log_event",
                   side_effect=lambda et, **kw: events.append((et, kw))):
            decision = shadow_blocked_step_live(
                self.GOAL,
                heuristic_action="redecompose",
                block_reason="subprocess timed out",
                signals={"retries": 2, "converging": False,
                         "sibling_fail_rate": 0.6, "replan_count": 1},
                turn_index=4,
                adapter_factory=lambda t: _FakeAdapter([_resp(
                    "fork", children=[{"goal": "fetch report"},
                                      {"goal": "extract figures"}])]),
            )
        assert decision is not None and decision.move == "fork"
        decided = [kw for et, kw in events if et == "NAVIGATOR_DECIDED"]
        assert len(decided) == 1
        actual = decided[0]["context"]["pipeline_actual"]
        assert actual["live"] is True
        assert actual["point"] == "blocked_step"
        # redecompose maps to the fork move equivalent.
        assert actual["move_equivalent"] == "fork"
        assert actual["heuristic_action"] == "redecompose"
        # the heuristic's signals ride along for adjudication.
        assert actual["retries"] == 2 and actual["sibling_fail_rate"] == 0.6

    def test_stuck_maps_to_close_and_failed_status(self):
        from unittest.mock import patch
        from navigator_shadow import shadow_blocked_step_live
        adapter = _FakeAdapter([_resp("close", closure="abandoned",
                                      verdict="exhausted retries")])
        with patch("config.get",
                   side_effect=self._cfg({"navigator.shadow_blocked_step": True})):
            shadow_blocked_step_live(
                self.GOAL, heuristic_action="stuck",
                block_reason="exhausted retries",
                adapter_factory=lambda t: adapter,
            )
        # "stuck" is terminal -> WorkReport.status failed reaches the prompt.
        user_text = adapter.calls[0][-1].content
        assert "failed" in user_text

    def test_never_raises_when_decide_explodes(self):
        from unittest.mock import patch
        from navigator_shadow import shadow_blocked_step_live
        with patch("config.get",
                   side_effect=self._cfg({"navigator.shadow_blocked_step": True})), \
             patch("navigator_prompt.decide",
                   side_effect=RuntimeError("decide blew up")):
            result = shadow_blocked_step_live(self.GOAL, heuristic_action="retry")
        assert result is None


class TestAnalyzeLiveAgreement:
    """analyze_live_agreement: per-move agreement table from NAVIGATOR_DECIDED
    rows — the per-class cutover evidence, structured."""

    def _event(self, move, pipeline, *, live=True, conf=0.9, goal="g", point=None):
        pa = {"move_equivalent": pipeline, "live": live}
        if point is not None:
            pa["point"] = point
        return {
            "event_type": "NAVIGATOR_DECIDED",
            "timestamp": "2026-06-12T00:00:00+00:00",
            "context": {
                "move": move, "confidence": conf, "tier": "cheap",
                "input_digest": {"goal_preview": goal},
                "pipeline_actual": pa,
            },
        }

    def test_by_point_breakdown(self):
        from navigator_shadow import analyze_live_agreement
        events = [
            self._event("execute", "execute", point="dispatch"),
            self._event("extend", "extend", point="blocked_step"),
            self._event("close", "fork", point="blocked_step", goal="bad"),
            self._event("execute", "execute"),  # no point -> defaults dispatch
        ]
        s = analyze_live_agreement(events)
        assert s["by_point"]["dispatch"] == {"agree": 2, "diverge": 0}
        assert s["by_point"]["blocked_step"] == {"agree": 1, "diverge": 1}
        # divergence row carries its point for adjudication.
        assert s["divergences"][0]["point"] == "blocked_step"

    def test_agreement_and_divergence_counting(self):
        from navigator_shadow import analyze_live_agreement
        events = [
            self._event("execute", "execute"),
            self._event("execute", "execute"),
            self._event("escalate", "execute", goal="debris"),
        ]
        s = analyze_live_agreement(events)
        assert s["live_rows"] == 3
        assert s["by_move"]["execute"] == {"agree": 2, "diverge": 0}
        assert s["by_move"]["escalate"] == {"agree": 0, "diverge": 1}
        assert len(s["divergences"]) == 1
        assert s["divergences"][0]["goal_preview"] == "debris"

    def test_guard_refused_counts_as_agreement_in_kind(self):
        from navigator_shadow import analyze_live_agreement
        events = [
            self._event("close", "guard_refused"),
            self._event("escalate", "guard_refused"),
        ]
        s = analyze_live_agreement(events)
        assert s["agreements"] == 2
        assert s["divergences"] == []

    def test_non_live_and_foreign_events_ignored(self):
        from navigator_shadow import analyze_live_agreement
        events = [
            self._event("execute", "execute", live=False),  # replay row
            {"event_type": "CLOSURE_VERDICT", "context": {}},
            self._event("execute", "execute"),
        ]
        s = analyze_live_agreement(events)
        assert s["live_rows"] == 1
