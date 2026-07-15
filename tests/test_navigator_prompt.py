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


def _resp(move, reasoning="because", confidence=0.7, planning_depth=None, **payload):
    obj = {
        "move": move, "reasoning": reasoning,
        "confidence": confidence, "payload": payload,
    }
    if planning_depth is not None:
        obj["planning_depth"] = planning_depth
    return json.dumps(obj)


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
        # Thread-arch #5 (MILESTONES 1.5): always logged, defaults "plan"
        # when the caller never opted into judge_planning_depth.
        assert ctx["planning_depth"] == "plan"


class TestPlanningDepthPrompt:
    """decide(judge_planning_depth=...): the "no new LLM call, one new
    envelope field" mechanism (thread-arch #5, MILESTONES 1.5) — off by
    default so every existing caller's prompt is byte-identical; on, it
    rides the SAME request, appending PLANNING_DEPTH_ADDENDUM to the system
    prompt for that call only."""

    def test_off_by_default_prompt_unchanged(self):
        adapter = _FakeAdapter([_resp("execute", instruction="go")])
        d, _ = decide(_nav_input(), tiers=["cheap"], adapter_factory=lambda t: adapter)
        system_sent = adapter.calls[0][0].content
        assert "planning_depth" not in system_sent
        assert d.planning_depth == "plan"  # unjudged default

    def test_on_appends_addendum_to_system_prompt(self):
        adapter = _FakeAdapter([_resp("execute", instruction="go")])
        decide(_nav_input(), tiers=["cheap"], adapter_factory=lambda t: adapter,
               judge_planning_depth=True)
        system_sent = adapter.calls[0][0].content
        assert "planning_depth" in system_sent
        # The recursion decree shape must be present, not dropped as an
        # enum afterthought.
        assert "spawn-sub-goal" in system_sent
        assert "one-shot" in system_sent and "thin-plan" in system_sent

    def test_on_captures_model_emitted_depth(self):
        adapter = _FakeAdapter([_resp(
            "extend", instruction="scope it", expected_artifact="scope.md",
            planning_depth="thin-plan")])
        d, _ = decide(_nav_input(), tiers=["cheap"], adapter_factory=lambda t: adapter,
                      judge_planning_depth=True)
        assert d.planning_depth == "thin-plan"

    def test_no_second_llm_call(self):
        """The mechanism is one more field on the SAME request — turning it
        on must not add an adapter call."""
        adapter = _FakeAdapter([_resp("execute", instruction="go")])
        decide(_nav_input(), tiers=["cheap"], adapter_factory=lambda t: adapter,
               judge_planning_depth=True)
        assert len(adapter.calls) == 1

    def test_malformed_model_output_defaults_without_retry_penalty(self):
        """A garbage planning_depth from the model must not count as a
        format failure — only move/confidence/payload are hard-fail
        mechanics; planning_depth fails closed to "plan" at parse time."""
        adapter = _FakeAdapter([_resp(
            "execute", instruction="go", planning_depth="mega-ultra-plan")])
        d, meta = decide(_nav_input(), tiers=["cheap"], adapter_factory=lambda t: adapter,
                         judge_planning_depth=True)
        assert d.planning_depth == "plan"
        assert meta["format_failures"] == 0


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

    def test_gate_open_by_default_via_act_dispatch(self):
        """navigator.act_dispatch defaults ON (2026-07-08) and implies the
        decide call, so a clean config gets a decision — a deployment that
        acts on dispatch needs the decision even with shadowing off."""
        from unittest.mock import patch
        from navigator_shadow import shadow_dispatch_live
        built = []
        with patch("config.get", side_effect=self._cfg({})):
            result = shadow_dispatch_live(
                self.GOAL,
                adapter_factory=lambda t: built.append(t) or _FakeAdapter(
                    [_resp("execute", instruction="go")]),
            )
        assert result is not None and result.move == "execute"
        assert built, "default-on act_dispatch must imply the decide call"

    def test_gate_closed_when_both_flags_off(self):
        from unittest.mock import patch
        from navigator_shadow import shadow_dispatch_live
        built = []
        with patch("config.get", side_effect=self._cfg(
                {"navigator.shadow_dispatch": False,
                 "navigator.act_dispatch": False})):
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

    def test_planning_depth_off_by_default(self):
        """navigator.shadow_planning_depth defaults False — a clean install
        gets no depth judgment even with dispatch shadow/act on (thread-arch
        #5, MILESTONES 1.5): the returned decision carries the unjudged
        default and pipeline_actual carries no depth_equivalent."""
        from unittest.mock import patch
        from navigator_shadow import shadow_dispatch_live
        events = []
        with patch("config.get",
                   side_effect=self._cfg({"navigator.shadow_dispatch": True})), \
             patch("captains_log.log_event",
                   side_effect=lambda et, **kw: events.append((et, kw))):
            decision = shadow_dispatch_live(
                self.GOAL,
                adapter_factory=lambda t: _FakeAdapter(
                    [_resp("execute", instruction="go")]),
            )
        assert decision.planning_depth == "plan"
        ctx = [kw for et, kw in events if et == "NAVIGATOR_DECIDED"][0]["context"]
        assert "depth_equivalent" not in ctx["pipeline_actual"]

    def test_planning_depth_shadow_enabled_records_depth_equivalent(self):
        from unittest.mock import patch
        from navigator_shadow import shadow_dispatch_live
        events = []
        with patch("config.get", side_effect=self._cfg({
                "navigator.shadow_dispatch": True,
                "navigator.shadow_planning_depth": True})), \
             patch("captains_log.log_event",
                   side_effect=lambda et, **kw: events.append((et, kw))):
            decision = shadow_dispatch_live(
                self.GOAL,
                adapter_factory=lambda t: _FakeAdapter([_resp(
                    "extend", instruction="scope it", expected_artifact="scope.md",
                    planning_depth="thin-plan")]),
            )
        assert decision.planning_depth == "thin-plan"
        ctx = [kw for et, kw in events if et == "NAVIGATOR_DECIDED"][0]["context"]
        assert ctx["pipeline_actual"]["depth_equivalent"] == "plan"
        assert ctx["planning_depth"] == "thin-plan"

    def test_planning_depth_gate_prompts_the_model(self):
        from unittest.mock import patch
        from navigator_shadow import shadow_dispatch_live
        adapter = _FakeAdapter([_resp("execute", instruction="go")])
        with patch("config.get", side_effect=self._cfg({
                "navigator.shadow_dispatch": True,
                "navigator.shadow_planning_depth": True})):
            shadow_dispatch_live(self.GOAL, adapter_factory=lambda t: adapter)
        assert "planning_depth" in adapter.calls[0][0].content

    def test_planning_depth_never_raises_when_decide_explodes(self):
        from unittest.mock import patch
        from navigator_shadow import shadow_dispatch_live
        with patch("config.get", side_effect=self._cfg({
                "navigator.shadow_dispatch": True,
                "navigator.shadow_planning_depth": True})), \
             patch("navigator_prompt.decide",
                   side_effect=RuntimeError("decide blew up")):
            result = shadow_dispatch_live(self.GOAL)
        assert result is None


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

    def test_does_not_judge_planning_depth(self):
        """Thread-arch #5 (MILESTONES 1.5) is decided as a DISPATCH-only
        judgment ('at the existing dispatch decide() call' — GOAL_BRAIN);
        the blocked-step shadow must not request or record it, even with
        every navigator flag on — no depth_equivalent, no addendum in the
        prompt sent to the model."""
        from unittest.mock import patch
        from navigator_shadow import shadow_blocked_step_live
        events = []
        adapter = _FakeAdapter([_resp(
            "extend", instruction="do it", expected_artifact="thing.md")])
        with patch("config.get", side_effect=self._cfg({
                "navigator.shadow_blocked_step": True,
                "navigator.shadow_planning_depth": True})), \
             patch("captains_log.log_event",
                   side_effect=lambda et, **kw: events.append((et, kw))):
            shadow_blocked_step_live(
                self.GOAL, heuristic_action="retry",
                adapter_factory=lambda t: adapter,
            )
        ctx = [kw for et, kw in events if et == "NAVIGATOR_DECIDED"][0]["context"]
        assert "depth_equivalent" not in ctx["pipeline_actual"]
        assert "planning_depth" not in adapter.calls[0][0].content

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


class TestAnalyzePlanningDepthAgreement:
    """analyze_planning_depth_agreement: the same per-class-cutover shape as
    analyze_live_agreement, applied to thread-arch #5 (MILESTONES 1.5) —
    'adjudication/agreement-table tooling should follow the same shape as
    python3 -m navigator_shadow --agreement' (the decided design)."""

    def _event(self, planning_depth, *, depth_equivalent="plan", live=True,
               conf=0.9, goal="g", move="execute"):
        pa = {"move_equivalent": move, "live": live}
        if depth_equivalent is not None:
            pa["depth_equivalent"] = depth_equivalent
        return {
            "event_type": "NAVIGATOR_DECIDED",
            "timestamp": "2026-07-12T00:00:00+00:00",
            "context": {
                "move": move, "confidence": conf, "tier": "cheap",
                "planning_depth": planning_depth,
                "input_digest": {"goal_preview": goal},
                "pipeline_actual": pa,
            },
        }

    def test_rows_without_depth_equivalent_excluded(self):
        """A live dispatch row where the depth shadow was OFF must not count
        — its planning_depth is an unjudged default, not data."""
        from navigator_shadow import analyze_planning_depth_agreement
        events = [
            self._event("plan", depth_equivalent=None),
            self._event("plan"),
        ]
        s = analyze_planning_depth_agreement(events)
        assert s["live_rows"] == 1

    def test_plan_agrees_lighter_shapes_diverge(self):
        from navigator_shadow import analyze_planning_depth_agreement
        events = [
            self._event("plan"),
            self._event("plan"),
            self._event("one-shot", goal="read config.yml and report its keys"),
            self._event("spawn-sub-goal", goal="make me rich"),
        ]
        s = analyze_planning_depth_agreement(events)
        assert s["live_rows"] == 4
        assert s["by_depth"]["plan"] == {"agree": 2, "diverge": 0}
        assert s["by_depth"]["one-shot"] == {"agree": 0, "diverge": 1}
        assert s["by_depth"]["spawn-sub-goal"] == {"agree": 0, "diverge": 1}
        assert s["agreements"] == 2
        assert len(s["divergences"]) == 2
        previews = {d["goal_preview"] for d in s["divergences"]}
        assert "make me rich" in previews

    def test_non_live_events_ignored(self):
        from navigator_shadow import analyze_planning_depth_agreement
        events = [
            self._event("plan", live=False),
            {"event_type": "CLOSURE_VERDICT", "context": {}},
            self._event("thin-plan"),
        ]
        s = analyze_planning_depth_agreement(events)
        assert s["live_rows"] == 1
        assert s["by_depth"]["thin-plan"] == {"agree": 0, "diverge": 1}


# ---------------------------------------------------------------------------
# VERIFY_LEARN_ARC V4 — navigator divergence adjudication
# ---------------------------------------------------------------------------

def _decided(move, pipeline, *, ts="2026-06-12T00:00:00+00:00", conf=0.9,
             goal="g", point="dispatch", reasoning="because"):
    """A live NAVIGATOR_DECIDED event (dispatch by default)."""
    return {
        "event_type": "NAVIGATOR_DECIDED",
        "timestamp": ts,
        "context": {
            "move": move, "confidence": conf, "tier": "cheap",
            "reasoning": reasoning,
            "input_digest": {"goal_preview": goal},
            "pipeline_actual": {"move_equivalent": pipeline, "live": True,
                                "point": point},
        },
    }


class _VerdictAdapter:
    """Returns a scripted adjudication JSON per call, repeating the last."""
    def __init__(self, verdicts):
        self.verdicts = list(verdicts)
        self.calls = 0

    def complete(self, messages, **kwargs):
        self.calls += 1
        v = self.verdicts.pop(0) if len(self.verdicts) > 1 else self.verdicts[0]

        class R:
            content = v
        return R()


def _verdict_json(verdict, rationale="r"):
    return json.dumps({"verdict": verdict, "rationale": rationale})


class TestAdjudicatedAgreementTable:
    """analyze_live_agreement(events, adjudications=...): the V4 consumer — each
    divergence gets its verdict attached and the table grows an `adjudicated`
    breakdown."""

    def test_verdicts_attach_and_breakdown_counts(self):
        from navigator_shadow import analyze_live_agreement, _divergence_key
        events = [
            _decided("execute", "execute"),               # agree
            _decided("escalate", "execute", goal="a"),    # divergence 1
            _decided("close", "extend", goal="b"),        # divergence 2 (unadjudicated)
        ]
        # Adjudicate only the first divergence.
        from navigator_shadow import analyze_live_agreement as _ala
        div1_key = _divergence_key({"timestamp": "2026-06-12T00:00:00",
                                    "point": "dispatch", "move": "escalate",
                                    "pipeline": "execute"})
        adj = {div1_key: {"verdict": "navigator_right", "rationale": "r"}}
        s = analyze_live_agreement(events, adjudications=adj)
        assert s["adjudicated"]["navigator_right"] == 1
        assert s["adjudicated"]["unadjudicated"] == 1
        # verdict is attached to the right divergence row
        for d in s["divergences"]:
            if d["move"] == "escalate":
                assert d["adjudication"] == "navigator_right"
            else:
                assert d["adjudication"] is None

    def test_no_adjudications_all_unadjudicated(self):
        from navigator_shadow import analyze_live_agreement
        s = analyze_live_agreement([_decided("escalate", "execute")])
        assert s["adjudicated"]["unadjudicated"] == 1
        assert s["divergences"][0]["adjudication"] is None


class TestAdjudicateDivergences:
    """adjudicate_navigator_divergences: the capped, cheap-tier LLM pass that
    writes append-only NAVIGATOR_ADJUDICATED rows (V4)."""

    def _run(self, monkeypatch, events, adapter, *, max_per_cycle=5, dry_run=False):
        import navigator_shadow as ns
        monkeypatch.setattr(ns, "_load_navigator_events", lambda: events)
        written = []
        monkeypatch.setattr(
            captains_log, "log_event",
            lambda etype, **kw: written.append((etype, kw)))
        result = ns.adjudicate_navigator_divergences(
            "test", max_per_cycle=max_per_cycle, tier="cheap",
            dry_run=dry_run, adapter_factory=lambda t: adapter)
        return result, written

    def test_adjudicates_divergences_and_writes_rows(self, monkeypatch):
        import navigator_shadow as ns
        events = [
            _decided("execute", "execute"),                 # agree — skipped
            _decided("escalate", "execute", goal="a"),      # divergence
        ]
        adapter = _VerdictAdapter([_verdict_json("navigator_right")])
        result, written = self._run(monkeypatch, events, adapter)
        assert result["adjudicated"] == 1
        assert result["verdicts"]["navigator_right"] == 1
        assert len(written) == 1
        etype, kw = written[0]
        assert etype == "NAVIGATOR_ADJUDICATED"
        ctx = kw["context"]
        assert ctx["verdict"] == "navigator_right"
        # div_key joins back to the divergence row
        div = [d for d in ns.analyze_live_agreement(events)["divergences"]][0]
        assert ctx["div_key"] == ns._divergence_key(div)

    def test_already_adjudicated_are_skipped(self, monkeypatch):
        import navigator_shadow as ns
        div_event = _decided("escalate", "execute", goal="a")
        div = ns.analyze_live_agreement([div_event])["divergences"][0]
        prior = {
            "event_type": "NAVIGATOR_ADJUDICATED",
            "timestamp": "2026-06-12T01:00:00+00:00",
            "context": {"div_key": ns._divergence_key(div),
                        "verdict": "pipeline_right"},
        }
        adapter = _VerdictAdapter([_verdict_json("navigator_right")])
        result, written = self._run(monkeypatch, [div_event, prior], adapter)
        assert result["already_adjudicated"] == 1
        assert result["adjudicated"] == 0
        assert written == []            # no new LLM call, no new row

    def test_cap_limits_llm_calls(self, monkeypatch):
        events = [_decided("escalate", "execute", goal=f"g{i}",
                           ts=f"2026-06-12T00:0{i}:00+00:00") for i in range(5)]
        adapter = _VerdictAdapter([_verdict_json("both_defensible")])
        result, written = self._run(monkeypatch, events, adapter, max_per_cycle=2)
        assert result["divergences_total"] == 5
        assert result["adjudicated"] == 2
        assert len(written) == 2

    def test_unparseable_verdict_is_skipped_not_stored(self, monkeypatch):
        events = [_decided("escalate", "execute", goal="a")]
        adapter = _VerdictAdapter(["not json at all"])
        result, written = self._run(monkeypatch, events, adapter)
        assert result["adjudicated"] == 0
        assert result["skipped_no_verdict"] == 1
        assert written == []

    def test_invalid_verdict_value_is_skipped(self, monkeypatch):
        events = [_decided("escalate", "execute", goal="a")]
        adapter = _VerdictAdapter([_verdict_json("navigator_is_god")])
        result, written = self._run(monkeypatch, events, adapter)
        assert result["adjudicated"] == 0
        assert result["skipped_no_verdict"] == 1
        assert written == []

    def test_dry_run_renders_without_persisting(self, monkeypatch):
        events = [_decided("escalate", "execute", goal="a")]
        adapter = _VerdictAdapter([_verdict_json("pipeline_right")])
        result, written = self._run(monkeypatch, events, adapter, dry_run=True)
        assert result["adjudicated"] == 1
        assert result["dry_run"] is True
        assert written == []            # nothing persisted


# ---------------------------------------------------------------------------
# VERIFY_LEARN_ARC V5 — navigator lessons (crystallize + inject)
# ---------------------------------------------------------------------------

def _adjudicated(verdict, move, pipeline, *, point="dispatch", goal="g", i=0):
    """A NAVIGATOR_ADJUDICATED event carrying a verdict for a divergence."""
    return {
        "event_type": "NAVIGATOR_ADJUDICATED",
        "timestamp": f"2026-06-12T00:0{i}:00+00:00",
        "context": {
            "div_key": f"k{verdict}{move}{pipeline}{point}{i}",
            "verdict": verdict, "point": point, "move": move,
            "pipeline": pipeline, "goal_preview": goal,
        },
    }


class TestCrystallizeNavigatorLessons:
    """crystallize_navigator_lessons: cluster pipeline_right adjudications by
    shape (point, move, pipeline); ≥3 same-shape becomes a lesson."""

    def _run(self, monkeypatch, tmp_path, events):
        import navigator_shadow as ns
        monkeypatch.setattr(ns, "_load_navigator_events", lambda: events)
        monkeypatch.setattr(ns, "_navigator_lessons_path",
                            lambda: tmp_path / "navigator_lessons.jsonl")
        return ns.crystallize_navigator_lessons(), ns

    def test_three_same_shape_pipeline_right_becomes_a_lesson(self, monkeypatch, tmp_path):
        events = [_adjudicated("pipeline_right", "escalate", "execute", i=i)
                  for i in range(3)]
        result, ns = self._run(monkeypatch, tmp_path, events)
        assert result["lessons"] == 1
        lessons = ns.load_navigator_lessons()
        assert len(lessons) == 1
        assert "escalate" in lessons[0] and "execute" in lessons[0]

    def test_below_threshold_no_lesson(self, monkeypatch, tmp_path):
        events = [_adjudicated("pipeline_right", "escalate", "execute", i=i)
                  for i in range(2)]
        result, ns = self._run(monkeypatch, tmp_path, events)
        assert result["lessons"] == 0
        assert ns.load_navigator_lessons() == []

    def test_navigator_right_and_both_defensible_excluded(self, monkeypatch, tmp_path):
        # Only pipeline_right (navigator-wrong) clusters crystallize into
        # corrective lessons.
        events = ([_adjudicated("navigator_right", "escalate", "execute", i=i)
                   for i in range(4)]
                  + [_adjudicated("both_defensible", "close", "execute", i=10 + i)
                     for i in range(4)])
        result, ns = self._run(monkeypatch, tmp_path, events)
        assert result["lessons"] == 0

    def test_distinct_shapes_cluster_separately(self, monkeypatch, tmp_path):
        events = ([_adjudicated("pipeline_right", "escalate", "execute", i=i)
                   for i in range(3)]
                  + [_adjudicated("pipeline_right", "close", "extend",
                                  point="blocked_step", i=10 + i)
                     for i in range(3)])
        result, ns = self._run(monkeypatch, tmp_path, events)
        assert result["lessons"] == 2

    def test_rewrite_reflects_only_current_clusters(self, monkeypatch, tmp_path):
        import navigator_shadow as ns
        path = tmp_path / "navigator_lessons.jsonl"
        monkeypatch.setattr(ns, "_navigator_lessons_path", lambda: path)
        # First: one cluster crystallizes.
        monkeypatch.setattr(ns, "_load_navigator_events",
                            lambda: [_adjudicated("pipeline_right", "escalate", "execute", i=i)
                                     for i in range(3)])
        ns.crystallize_navigator_lessons()
        assert len(ns.load_navigator_lessons()) == 1
        # Then: evidence no longer supports it → the view rewrites to empty
        # (derived, not append-only), never a stale lesson.
        monkeypatch.setattr(ns, "_load_navigator_events", lambda: [])
        ns.crystallize_navigator_lessons()
        assert ns.load_navigator_lessons() == []


class TestNavigatorLessonInjection:
    """decide(navigator.lesson_inject): the V5 consumer — navigator lessons are
    injected into the decide prompt (same seam worker slices use), off by
    default, and the NAVIGATOR_DECIDED row records whether injection was on."""

    def _patch_flag(self, monkeypatch, on, lessons):
        import config
        import navigator_shadow as ns
        real = config.get
        monkeypatch.setattr(
            config, "get",
            lambda k, d=None: on if k == "navigator.lesson_inject" else real(k, d))
        monkeypatch.setattr(ns, "load_navigator_lessons", lambda *a, **k: lessons)

    def test_off_by_default_no_injection(self, monkeypatch):
        adapter = _FakeAdapter([_resp("execute", instruction="go")])
        # flag defaults off; load should never even be consulted
        _, meta = decide(_nav_input(), tiers=["cheap"],
                         adapter_factory=lambda t: adapter)
        user_sent = adapter.calls[0][1].content
        assert "Lessons from past adjudicated divergences" not in user_sent
        assert meta["lessons_injected"] == 0

    def test_on_injects_lessons_into_prompt(self, monkeypatch):
        self._patch_flag(monkeypatch, True,
                         ["When you chose 'escalate' ... prefer 'execute'."])
        adapter = _FakeAdapter([_resp("execute", instruction="go")])
        _, meta = decide(_nav_input(), tiers=["cheap"],
                         adapter_factory=lambda t: adapter)
        user_sent = adapter.calls[0][1].content
        assert "Lessons from past adjudicated divergences" in user_sent
        assert "prefer 'execute'" in user_sent
        assert meta["lessons_injected"] == 1

    def test_on_but_no_lessons_is_a_noop(self, monkeypatch):
        self._patch_flag(monkeypatch, True, [])
        adapter = _FakeAdapter([_resp("execute", instruction="go")])
        _, meta = decide(_nav_input(), tiers=["cheap"],
                         adapter_factory=lambda t: adapter)
        assert "Lessons from past adjudicated divergences" not in adapter.calls[0][1].content
        assert meta["lessons_injected"] == 0

    def test_injection_recorded_on_decision_row(self, monkeypatch):
        self._patch_flag(monkeypatch, True, ["lesson A", "lesson B"])
        events = []
        monkeypatch.setattr(captains_log, "log_event",
                            lambda etype, **kw: events.append((etype, kw)))
        decide(_nav_input(), tiers=["cheap"],
               adapter_factory=lambda t: _FakeAdapter([_resp("execute", instruction="go")]))
        etype, kw = events[0]
        assert etype == "NAVIGATOR_DECIDED"
        assert kw["context"]["lessons_injected"] == 2

    def test_no_marker_when_injection_off(self, monkeypatch):
        events = []
        monkeypatch.setattr(captains_log, "log_event",
                            lambda etype, **kw: events.append((etype, kw)))
        decide(_nav_input(), tiers=["cheap"],
               adapter_factory=lambda t: _FakeAdapter([_resp("execute", instruction="go")]))
        # off path: no lessons_injected key on the row (kept sparse)
        assert "lessons_injected" not in events[0][1]["context"]
