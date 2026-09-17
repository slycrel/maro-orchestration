"""Durable plan-node ids (LoopsBench follow-up chunk 4, 2026-09-16).

A plan node IS its NEXT.md item. The checkpoint carries the item of every
remaining step (`step_items`) and the ORIGINAL plan's number→item binding
(`plan_items`); a resume restores both BEFORE decomposition (no planner
call, no second copy of the plan in NEXT.md), the gate resolves `[after:N]`
through the binding so a resumed suffix keeps its declared edges, and the
DAG lane schedules a suffix by its re-keyed edges instead of self-depending
tags.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import checkpoint as ckmod
from checkpoint import Checkpoint, CompletedStep, load_checkpoint, write_checkpoint


def _oc(index, status, text="t"):
    return SimpleNamespace(index=index, status=status, text=text, result="",
                           tokens_in=0, tokens_out=0, elapsed_ms=0)


def _ckpt_env(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_ORCH_ROOT", str(tmp_path))
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setattr(ckmod, "_rundir_checkpoint_path", lambda: None)


# --- checkpoint carries identity ------------------------------------------

def test_writer_persists_step_items_and_plan_binding(monkeypatch, tmp_path):
    _ckpt_env(monkeypatch, tmp_path)
    steps = ["A", "B", "C"]
    write_checkpoint("lp-ids", "g", "p", steps, [_oc(11, "done", "A")],
                     step_indices=[11, 13, 49], plan_items=[11, 13, 49])
    raw = json.loads(ckmod._checkpoint_path("lp-ids").read_text())
    assert raw["step_items"] == [11, 13, 49] and raw["plan_items"] == [11, 13, 49]
    ck = load_checkpoint("lp-ids")
    assert ck.step_items == [11, 13, 49] and ck.plan_items == [11, 13, 49]
    assert ck.remaining_steps == ["B", "C"]
    assert ck.remaining_items == [13, 49]


def test_writer_drops_step_items_when_the_mapping_drifts(monkeypatch, tmp_path, caplog):
    """A mapping that does not pair 1:1 with the plan positions nothing
    reliably (chunk 2) and must not be carried as identity either."""
    _ckpt_env(monkeypatch, tmp_path)
    write_checkpoint("lp-drift", "g", "p", ["A", "B", "C"], [_oc(11, "done")],
                     step_indices=[11, 13], plan_items=[11, 13, 49])
    ck = load_checkpoint("lp-drift")
    assert ck.step_items is None and ck.remaining_items is None
    assert ck.plan_items == [11, 13, 49]          # the binding is verbatim, not derived
    write_checkpoint("lp-dupid", "g", "p", ["A", "B"], [],
                     step_indices=[5, 5], plan_items=None)
    assert load_checkpoint("lp-dupid").step_items is None


@pytest.mark.parametrize("raw, want", [
    ([13, 49, 11], [13, 49, 11]),
    (["13", 49.0, 11], [13, 49, 11]),          # integral strings/floats coerce
    ([13, 49], None),                         # wrong length → not carried
    ([13, True, 11], None),                   # bool is not an identity
    ([13, None, 11], None),
    ([13, 1.5, 11], None),
    ("13,49,11", None),
    ([], None),
])
def test_from_dict_step_items_all_or_nothing(raw, want):
    ck = Checkpoint.from_dict({"loop_id": "x", "goal": "g", "project": "p",
                               "steps": ["A", "B", "C"], "completed": [],
                               "positioned": True, "step_items": raw})
    assert ck.step_items == want


@pytest.mark.parametrize("raw, want", [
    ([10, 11, 12, 13], [10, 11, 12, 13]),
    ([10, -1, 12], [10, -1, 12]),             # an unmirrored plan step keeps its slot
    ([10, "x"], None),
    ({"1": 10}, None),
    (None, None),
])
def test_from_dict_plan_items_all_or_nothing(raw, want):
    ck = Checkpoint.from_dict({"loop_id": "x", "goal": "g", "project": "p",
                               "steps": ["A"], "completed": [], "plan_items": raw})
    assert ck.plan_items == want


def test_branch_carries_identity(monkeypatch, tmp_path):
    _ckpt_env(monkeypatch, tmp_path)
    write_checkpoint("lp-src", "g", "p", ["A", "B"], [_oc(7, "done")],
                     step_indices=[7, 8], plan_items=[7, 8])
    new_id = ckmod.branch_checkpoint("lp-src")
    br = load_checkpoint(new_id)
    assert br.step_items == [7, 8] and br.plan_items == [7, 8]
    assert br.remaining_items == [8]


def test_second_hop_carries_the_original_binding_verbatim(monkeypatch, tmp_path):
    """Hop 2's plan is the suffix; its step_items are the suffix's items and
    plan_items is STILL the original binding (never re-bound)."""
    _ckpt_env(monkeypatch, tmp_path)
    write_checkpoint("lp-hop2", "g", "p", ["B", "C"], [_oc(10, "done", "A"), _oc(11, "done", "B")],
                     step_indices=[11, 12], plan_items=[10, 11, 12])
    ck = load_checkpoint("lp-hop2")
    assert ck.remaining_steps == ["C"] and ck.remaining_items == [12]
    assert ck.plan_items == [10, 11, 12]


# --- gate resolves through the binding ------------------------------------

def test_bound_numbers_never_bind_unmirrored_or_duplicate_items():
    from step_gate import bound_plan_numbers
    assert bound_plan_numbers([10, -1, 12, 12]) == {10: 1}      # unmirrored + duplicate never bind
    assert bound_plan_numbers(None) == {} and bound_plan_numbers([]) == {}
    assert bound_plan_numbers([10, "11", 12.0, 13.5, True]) == {10: 1, 11: 2, 12: 3}


# --- identity is validated ONCE, all-or-nothing, at the checkpoint boundary --

@pytest.mark.parametrize("step_items,plan_items,want_steps,want_plan", [
    ([10, 11, 11], [10, 11, 12], None, [10, 11, 12]),          # duplicate step item
    ([10, 11, 12], [10, 10, 12], [10, 11, 12], None),          # duplicate plan item: binding dropped
    ([10, 12, 11], [10, 11, 12], None, [10, 11, 12]),          # swapped: not in plan order
    ([10, 11, 12.5], [10, 11, 12], None, [10, 11, 12]),        # fractional
    ([10, 11, 12], [10, 11.5, 12], [10, 11, 12], None),        # fractional plan slot
    ([10, 11, True], [10, 11, 12], None, [10, 11, 12]),        # bool
    ([10, 11], [10, 11, 12], None, [10, 11, 12]),              # short
    ([10, 11, 99], [10, 11, 12], [10, 11, 99], [10, 11, 12]),  # unbound step item: allowed (soft)
    ([10, 11, 12], [-1, 11, 12], [10, 11, 12], [-1, 11, 12]),  # -1 = explicitly unmirrored
    ([10, 11, 12], [-1, -1, 12], [10, 11, 12], [-1, -1, 12]),  # -1 may repeat
    ([10, -1, 12], [10, 11, 12], [10, -1, 12], [10, 11, 12]),  # unmirrored step slot
    ([10, 11, 12], [11, 10, 12], [10, 11, 12], None),          # reordered binding (r2 finding 1)
    ([-2, 11, 12], [10, 11, 12], None, [10, 11, 12]),          # only -1 is a sentinel (r2 finding 3)
    ([10, 11, 12], [10, -5, 12], [10, 11, 12], None),
    ([10, 11, 12], [10, 11, 12], [10, 11, 12], [10, 11, 12]),  # negative control
])
def test_loader_identity_must_detects(step_items, plan_items, want_steps, want_plan):
    """Shapes built to evade the per-list checks (unique, right length) —
    the loader must drop the offending list as a WHOLE, never keep a
    partially-trusted binding that could hard-gate on an ambiguous item
    (round-1 finding: [10,10,12] refused step 3 on item 10)."""
    d = {"loop_id": "l", "goal": "g", "project": "p", "steps": ["A", "B", "C"],
         "completed": [{"index": 10, "text": "A", "status": "done", "result": ""}],
         "step_items": step_items, "plan_items": plan_items}
    ck = Checkpoint.from_dict(d)
    assert ck.step_items == want_steps and ck.plan_items == want_plan
    # the writer applies the same validator (never manufactures -1)
    from checkpoint import validate_identity
    from checkpoint import _int_list
    assert validate_identity(_int_list(step_items, 3), _int_list(plan_items)) == (want_steps, want_plan)


def test_writer_never_manufactures_unmirrored_slots(monkeypatch, tmp_path):
    _ckpt_env(monkeypatch, tmp_path)
    write_checkpoint("lp-garbage", "g", "p", ["A", "B"], [],
                     step_indices=[10, True], plan_items=[20, 20.5])
    ck = load_checkpoint("lp-garbage")
    assert ck.step_items is None and ck.plan_items is None


def test_non_string_step_entries_refuse_the_resume(monkeypatch, tmp_path):
    """A JSON-valid checkpoint whose step is a dict used to load, then
    crash dependency parsing after the restore (round-1 QA finding 5):
    it is unreadable at the boundary, so the explicit resume is refused."""
    _loop_env(monkeypatch, tmp_path)
    import agent_loop as al
    with pytest.raises(ValueError):
        Checkpoint.from_dict({"loop_id": "l", "goal": "g", "project": "p",
                              "steps": ["A", {"text": "B"}], "completed": []})
    ck_path = ckmod._checkpoint_path("lp-dictstep")
    ck_path.parent.mkdir(parents=True, exist_ok=True)
    ck_path.write_text(json.dumps({"loop_id": "lp-dictstep", "goal": "g", "project": "",
                                   "steps": ["A", {"text": "B"}], "completed": []}))
    seen = []
    res = al.run_agent_loop("g", adapter=_Adapter(seen), max_steps=2, max_iterations=2,
                            resume_from_loop_id="lp-dictstep")
    assert res.status != "done" and not seen, (res.status, res.stuck_reason, seen)
    assert "lp-dictstep" in (res.stuck_reason or "")


def test_verdict_resumed_suffix_keeps_its_declared_edge():
    """Suffix step 'orig 3 [after:1]' (item 12) under the carried binding
    [10, 11, 12]: the tag resolves to item 10 whose CARRIED row is blocked
    → hard gate, even though the suffix numbering says nothing about 1."""
    from step_gate import prerequisite_verdict
    v = prerequisite_verdict("orig 3 [after:1]", 12, deps={1: set()}, step_indices=[12],
                             step_outcomes=[_oc(10, "blocked"), _oc(11, "done")],
                             plan_items=[10, 11, 12], identity_intact=True)
    assert v.plan_no == 3 and v.explicit is True
    assert not v.ready and v.hard and v.unmet[0][:2] == (1, "blocked")


def test_verdict_implicit_edge_is_the_original_plans_default_not_deps():
    """Untagged suffix step orig 3 (item 12) waits on ORIGINAL step 2 (item
    11), not on `deps` parsed over the suffix (which would say position 1
    ↔ nothing). Carried row for item 11 is skipped → soft unmet; hard when
    the implicit class is hardened."""
    from step_gate import prerequisite_verdict
    kw = dict(deps={1: set(), 2: {1}}, step_indices=[12, 13],
              step_outcomes=[_oc(10, "done"), _oc(11, "skipped")],
              plan_items=[10, 11, 12, 13], identity_intact=True)
    v = prerequisite_verdict("orig 3", 12, **kw)
    assert v.plan_no == 3 and v.explicit is False and not v.ready and not v.hard
    assert v.unmet[0][:2] == (2, "skipped")
    v2 = prerequisite_verdict("orig 3", 12, gate_implicit=True, **kw)
    assert v2.hard
    # ORIGINAL step 1 has no sequential prerequisite (same shape as the
    # positional path: an implicit check with no edges)
    v1 = prerequisite_verdict("orig 1", 10, **kw)
    assert v1.plan_no == 1 and v1.ready and v1.unmet == [] and v1.explicit is False


def test_verdict_unbound_step_checks_only_its_tag():
    from step_gate import prerequisite_verdict
    kw = dict(deps={}, step_indices=[-1], step_outcomes=[_oc(10, "blocked")],
              plan_items=[10, 11], identity_intact=True)
    assert prerequisite_verdict("recovery sub-step", -1, **kw).explicit is None
    v = prerequisite_verdict("recovery sub-step [after:1]", -1, **kw)
    assert v.plan_no == 0 and v.explicit is True and v.hard


def test_verdict_binding_wins_over_positional_step_indices():
    """Fresh run: binding == step_indices, so both paths agree (negative
    control that the new path does not change fresh-run verdicts)."""
    from step_gate import prerequisite_verdict
    outcomes = [_oc(10, "blocked")]
    a = prerequisite_verdict("two [after:1]", 11, deps={1: set(), 2: {1}},
                             step_indices=[10, 11], step_outcomes=outcomes)
    b = prerequisite_verdict("two [after:1]", 11, deps={1: set(), 2: {1}},
                             step_indices=[10, 11], step_outcomes=outcomes,
                             plan_items=[10, 11])
    assert (a.ready, a.hard, a.unmet, a.plan_no) == (b.ready, b.hard, b.unmet, b.plan_no)


# --- suffix remap for the DAG lane ----------------------------------------

_PLAN_ITEMS = [10, 11, 12, 13]


def test_remap_rekeys_tags_to_suffix_positions_and_drops_finished_prereqs():
    """Original plan: 1 A, 2 B[after:1], 3 C[after:1], 4 D[after:2,3]. A
    finished; suffix = [B, C, D]. Parsed as-is the suffix self-depends
    (B[after:1] on position 1 = itself); re-keyed it is two independent
    roots and a join."""
    from planner import build_execution_levels, parse_dependencies
    from step_gate import remap_suffix_deps
    suffix = ["B [after:1]", "C [after:1]", "D [after:2,3]"]
    _, stale = parse_dependencies(suffix)
    assert 1 in stale[1] or stale[1] == {1}, stale       # the bug: self-dependence
    deps, declared, pre = remap_suffix_deps(suffix, [11, 12, 13], _PLAN_ITEMS,
                                            [_oc(10, "done", "A")])
    assert deps == {1: set(), 2: set(), 3: {1, 2}}
    assert declared == {3: {1, 2}}
    assert pre == {}
    assert build_execution_levels(deps) == [[1, 2], [3]]


def test_remap_pre_gates_a_dependent_of_a_carried_skipped_prereq():
    """A carried SKIPPED prerequisite (finished, so not in the suffix) is
    unmet for its declared dependent: pre-gated. An implicit edge to it is
    soft unless the implicit class is hardened."""
    from step_gate import remap_suffix_deps
    suffix = ["C [after:1]", "D"]                       # orig 3 tagged, orig 4 untagged (waits on 3)
    carried = [_oc(10, "skipped", "A"), _oc(11, "done", "B")]
    deps, declared, pre = remap_suffix_deps(suffix, [12, 13], _PLAN_ITEMS, carried)
    assert deps == {1: set(), 2: {1}} and declared == {}
    assert list(pre) == [1] and "step 1 ended skipped" in pre[1] and "declared" in pre[1]
    # untagged orig 2 (item 11) waiting on skipped orig 1: soft → not pre-gated…
    deps2, _, pre2 = remap_suffix_deps(["B"], [11], _PLAN_ITEMS, [_oc(10, "skipped")])
    assert deps2 == {1: set()} and pre2 == {}
    # …unless hardened
    _, _, pre3 = remap_suffix_deps(["B"], [11], _PLAN_ITEMS, [_oc(10, "skipped")],
                                   gate_implicit=True)
    assert 1 in pre3 and "sequential" in pre3[1]


def test_remap_unknown_and_noise_references_never_edge():
    from step_gate import remap_suffix_deps
    # no carried row for orig 1 → unknown → satisfied; [after:9] out of plan;
    # [after:3] on orig 3 = self → noise
    deps, declared, pre = remap_suffix_deps(["C [after:1,3,9]"], [12], _PLAN_ITEMS, [])
    assert deps == {1: set()} and declared == {} and pre == {}


def test_remap_unbound_suffix_step_uses_queue_order():
    """A sub-step (item -1) or an interrupt addition in the suffix has no
    original number: untagged it waits on the previous suffix step, as
    parse_dependencies would; tagged it resolves its tag through the binding."""
    from step_gate import remap_suffix_deps
    deps, declared, pre = remap_suffix_deps(["B", "sub-step", "extra [after:2]"],
                                            [11, -1, 77], _PLAN_ITEMS, [_oc(10, "done")])
    assert deps == {1: set(), 2: {1}, 3: {1}} and declared == {3: {1}}


def test_remap_a_prereq_in_the_suffix_is_an_edge_not_a_pre_gate():
    """A carried BLOCKED prerequisite never finished its position, so it is
    IN the suffix: the dependent waits on its re-run (edge), it is not
    pre-gated on the stale outcome."""
    from step_gate import remap_suffix_deps
    deps, declared, pre = remap_suffix_deps(["A", "B [after:1]"], [10, 11], _PLAN_ITEMS,
                                            [_oc(10, "blocked", "A")])
    assert deps == {1: set(), 2: {1}} and declared == {2: {1}} and pre == {}


def test_dag_pre_gated_step_is_recorded_blocked_and_gates_its_dependents():
    from unittest.mock import patch
    from loop_parallel import _run_steps_dag
    ran = []

    def _fake_exec(**kwargs):
        ran.append(kwargs["step_num"])
        return {"status": "done", "result": "ok", "tokens_in": 0, "tokens_out": 0,
                "summary": "ok"}

    with patch("loop_parallel._execute_step", side_effect=_fake_exec):
        outcomes = _run_steps_dag(
            goal="g", steps=["C", "D", "E"], deps={1: set(), 2: {1}, 3: set()},
            adapter=SimpleNamespace(model_key="t"), ancestry_context="", tools=[],
            verbose=False, max_workers=2, declared={2: {1}},
            pre_gated={1: "not executed — declared prerequisite not met: step 1 ended skipped (before this resume)"})
    assert [o["status"] for o in outcomes] == ["blocked", "blocked", "done"]
    assert "before this resume" in outcomes[0]["stuck_reason"]
    assert "step 1 ended blocked" in outcomes[1]["stuck_reason"]
    assert ran == [3]


def test_dag_pre_gated_soft_dependent_is_released():
    from unittest.mock import patch
    from loop_parallel import _run_steps_dag
    ran = []

    def _fake_exec(**kwargs):
        ran.append(kwargs["step_num"])
        return {"status": "done", "result": "ok", "tokens_in": 0, "tokens_out": 0,
                "summary": "ok"}

    with patch("loop_parallel._execute_step", side_effect=_fake_exec):
        outcomes = _run_steps_dag(
            goal="g", steps=["C", "D"], deps={1: set(), 2: {1}},
            adapter=SimpleNamespace(model_key="t"), ancestry_context="", tools=[],
            verbose=False, max_workers=2, declared={}, pre_gated={1: "not executed — x"})
    assert [o["status"] for o in outcomes] == ["blocked", "done"] and ran == [2]


# --- loop flows -----------------------------------------------------------

def _loop_env(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_ORCH_ROOT", str(tmp_path))
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path / "ws"))
    import introspect
    import loop_execute
    monkeypatch.setattr(loop_execute, "_free_auto_ralph_enabled", lambda: False)
    monkeypatch.setattr(introspect, "plan_recovery", lambda diag: None)


class _Adapter:
    """complete_step for everything; flag_stuck for steps whose text
    contains any of `stuck_on`. Records every 'Current step' prompt."""
    model_key = "test"

    def __init__(self, seen, stuck_on=()):
        self.seen = seen
        self.stuck_on = tuple(stuck_on)

    def complete(self, messages, **kwargs):
        from llm import LLMResponse, ToolCall
        user = " ".join(m.content for m in messages if getattr(m, "role", "") == "user")
        if "Current step" in user:
            cur = user.split("Current step")[-1][:120]
            self.seen.append(cur)
            if any(t in cur for t in self.stuck_on):
                return LLMResponse(content="", tool_calls=[ToolCall(
                    name="flag_stuck", arguments={"reason": "cannot"})],
                    input_tokens=1, output_tokens=1)
        return LLMResponse(content="", tool_calls=[ToolCall(
            name="complete_step", arguments={"result": "ok", "summary": "ok"})],
            input_tokens=1, output_tokens=1)


PLAN = ["Step one: fetch", "Step two: transform", "Step three: report"]


def _next_items(project):
    import orch_items as o
    _, items = o.parse_next(project)
    return items


def test_resume_keeps_next_items_and_skips_the_planner(monkeypatch, tmp_path):
    """Hop 1 mirrors the plan to NEXT.md (3 items) and stops after step one.
    Hop 2 resumes WITHOUT preset steps: the planner must not be called,
    NEXT.md must still hold exactly 3 items, and the ORIGINAL items 2 and 3
    are the ones marked done. The hop-2 checkpoint carries the suffix's
    items and the original binding verbatim."""
    _loop_env(monkeypatch, tmp_path)
    import agent_loop as al
    import loop_planning
    seen = []
    first = al.run_agent_loop("keep items", adapter=_Adapter(seen), preset_steps=PLAN,
                              max_steps=3, max_iterations=1)
    ck1 = load_checkpoint(first.loop_id)
    assert ck1 is not None and ck1.step_items and len(ck1.step_items) == 3
    assert ck1.plan_items == ck1.step_items
    project = ck1.project
    before = _next_items(project)          # template items + the mirrored plan
    items = list(ck1.step_items)
    assert [it.text for it in before if it.index in items] == PLAN

    def _no_planner(*a, **k):
        raise AssertionError("planner called on an explicit resume")
    monkeypatch.setattr(loop_planning, "_decompose", _no_planner)
    seen.clear()
    second = al.run_agent_loop("keep items", adapter=_Adapter(seen), max_steps=3,
                               max_iterations=6, resume_from_loop_id=first.loop_id)
    assert second.status == "done", second.stuck_reason
    assert not any("Step one" in c for c in seen)
    after = _next_items(project)
    assert [it.index for it in after] == [it.index for it in before], \
        "resume appended a second copy of the plan"
    states = {it.index: it.state for it in after}
    assert states[items[1]] == "x" and states[items[2]] == "x", states   # NEXT.md done marker
    ck2 = load_checkpoint(second.loop_id)
    assert ck2.steps == PLAN[1:]
    assert ck2.step_items == items[1:]
    assert ck2.plan_items == items


def test_resume_with_an_empty_suffix_runs_nothing_and_plans_nothing(monkeypatch, tmp_path):
    _loop_env(monkeypatch, tmp_path)
    import agent_loop as al
    import loop_planning
    seen = []
    first = al.run_agent_loop("empty suffix", adapter=_Adapter(seen), preset_steps=PLAN[:1],
                              max_steps=1, max_iterations=6)
    assert first.status == "done"
    ck = load_checkpoint(first.loop_id)
    assert ck is not None and ck.remaining_steps == []
    monkeypatch.setattr(loop_planning, "_decompose",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("planner called")))
    seen.clear()
    second = al.run_agent_loop("empty suffix", adapter=_Adapter(seen), max_steps=1,
                               max_iterations=6, resume_from_loop_id=first.loop_id)
    assert seen == [] and second.status == "done"


def test_gate_enforces_a_declared_edge_after_resume(monkeypatch, tmp_path, caplog):
    """Plan: one, two, three [after:1]. Step one blocks; hop 1 stops. Hop 2
    resumes — step one is re-run (blocked never finishes a position) and
    blocks again; step two (soft edge) runs; step three's declared edge
    must be ENFORCED with the binding carried, not degraded to soft
    because 'the run resumed'."""
    import logging
    _loop_env(monkeypatch, tmp_path)
    import agent_loop as al
    import loop_execute
    monkeypatch.setattr(loop_execute, "_process_blocked_step",
                        lambda ctx, blk: ("normal", blk.step_idx, "", "", 0, 0, blk.replan_count))
    plan = ["Step one: fetch", "Step two: transform", "Step three: report [after:1]"]
    seen = []
    first = al.run_agent_loop("gate after resume", adapter=_Adapter(seen, stuck_on=("Step one",)),
                              preset_steps=plan, max_steps=3, max_iterations=1)
    ck1 = load_checkpoint(first.loop_id)
    assert ck1.remaining_steps == plan            # a blocked row never finishes a position
    seen.clear()
    caplog.set_level(logging.WARNING)
    second = al.run_agent_loop("gate after resume", adapter=_Adapter(seen, stuck_on=("Step one",)),
                               max_steps=3, max_iterations=8, resume_from_loop_id=first.loop_id)
    assert not any("Step three" in c for c in seen), f"gated step reached the adapter: {seen}"
    assert any("Step two" in c for c in seen)
    three = [s for s in second.steps if "Step three" in s.text]
    assert three and three[-1].status == "blocked"
    assert three[-1].result.startswith("not executed — declared prerequisite not met: step 1 ended blocked")
    assert not any("plan identity not intact" in r.getMessage() for r in caplog.records)
    assert not any("no durable plan binding" in r.getMessage() for r in caplog.records)
    # the GATE writer site (loop_execute) carried the original binding
    ck2 = load_checkpoint(second.loop_id)
    assert ck2.plan_items == load_checkpoint(first.loop_id).plan_items
    assert [c.status for c in ck2.completed if "Step three" in c.text][-1] == "blocked"


def test_dag_lane_schedules_a_resumed_suffix_by_its_real_edges(monkeypatch, tmp_path):
    """Original plan A, B[after:1], C[after:1], D[after:2,3]; hop 1 is
    sequential and finishes A. Hop 2 with fan-out: parsed as-is the suffix
    self-depends (no parallel level → sequential lane, and D would wait
    on itself); re-keyed it is the DAG {B, C} → D."""
    _loop_env(monkeypatch, tmp_path)
    import agent_loop as al
    import loop_parallel
    plan = ["Step A: fetch", "Step B: parse [after:1]", "Step C: index [after:1]",
            "Step D: report [after:2,3]"]
    seen = []
    first = al.run_agent_loop("dag resume", adapter=_Adapter(seen), preset_steps=plan,
                              max_steps=4, max_iterations=1)
    assert any("Step A" in c for c in seen)
    before = _next_items(load_checkpoint(first.loop_id).project)
    captured = {}
    real_dag = loop_parallel._run_steps_dag

    def spy(**kw):
        captured["deps"] = {k: set(v) for k, v in kw["deps"].items()}
        captured["declared"] = kw.get("declared")
        captured["identity_intact"] = kw.get("identity_intact")
        return real_dag(**kw)
    monkeypatch.setattr(loop_parallel, "_run_steps_dag", spy)
    seen.clear()
    second = al.run_agent_loop("dag resume", adapter=_Adapter(seen), max_steps=4,
                               max_iterations=8, parallel_fan_out=2,
                               resume_from_loop_id=first.loop_id)
    assert captured, "resumed suffix did not take the DAG lane"
    assert captured["deps"] == {1: set(), 2: set(), 3: {1, 2}}
    assert captured["declared"] == {3: {1, 2}} and captured["identity_intact"] is True
    assert second.status == "done", [(s.text, s.status, s.result) for s in second.steps]
    assert not any("Step A" in c for c in seen)
    assert {s.status for s in second.steps} == {"done"}
    # The lane's rows carry the ORIGINAL items and mark them — no second
    # copy of the plan, every original node finished (round-1 finding:
    # the parallel lanes returned before the only binding phase).
    ck1 = load_checkpoint(first.loop_id)
    items = list(ck1.step_items)
    after = _next_items(ck1.project)
    assert [it.index for it in after] == [it.index for it in before], "DAG resume appended fresh items"
    assert [s.index for s in second.steps] == items[1:]
    states = {it.index: it.state for it in after}
    assert all(states[i] == "x" for i in items), states


def test_fresh_run_binds_only_when_the_plan_identity_is_intact(monkeypatch, tmp_path):
    """Binding is the positional map on a fresh run; a duplicate item index
    (or a reshaped plan) leaves it None so the gate degrades as before."""
    _loop_env(monkeypatch, tmp_path)
    import loop_planning
    from loop_types import LoopContext
    ctx = LoopContext(goal="g", project="proj", loop_id="lp-bind", verbose=False)
    calls = {"n": 0}

    class _O:
        def append_next_items(self, project, steps):
            calls["n"] += 1
            return [40, 41, 42][:len(steps)]

        def append_decision(self, project, lines):
            pass
    monkeypatch.setattr(loop_planning, "_orch", lambda: _O())
    monkeypatch.setattr(loop_planning, "_shape_steps", lambda steps, label="": list(steps))
    steps, idxs, _ = loop_planning._prepare_execution(ctx, ["a", "b", "c"], ["a", "b", "c"],
                                                      deps={1: set(), 2: {1}, 3: {2}})
    assert idxs == [40, 41, 42] and ctx.plan_items == [40, 41, 42]
    # reshaped: 3 parsed became 2 shaped
    ctx2 = LoopContext(goal="g", project="proj", loop_id="lp-bind2", verbose=False)
    loop_planning._prepare_execution(ctx2, ["a", "b"], ["a", "b"],
                                     deps={1: set(), 2: {1}, 3: {2}})
    assert ctx2.plan_items is None


def test_resume_without_carried_items_appends_fresh_and_drops_the_binding(monkeypatch, tmp_path, caplog):
    """An older checkpoint (no step_items) resumes as before this chunk:
    fresh NEXT.md items, no binding, gate soft — and says so."""
    import logging
    _loop_env(monkeypatch, tmp_path)
    import loop_planning
    from loop_planning import RestoredCheckpoint
    from loop_types import LoopContext
    ctx = LoopContext(goal="g", project="proj", loop_id="lp-old", verbose=False)

    class _O:
        def append_next_items(self, project, steps):
            return [90, 91][:len(steps)]

        def append_decision(self, project, lines):
            pass
    monkeypatch.setattr(loop_planning, "_orch", lambda: _O())
    monkeypatch.setattr(loop_planning, "_shape_steps", lambda steps, label="": list(steps))
    old = RestoredCheckpoint(loop_id="old", ckpt=None, steps=["b", "c"], items=None,
                             plan_items=None, completed=[], project="proj")
    caplog.set_level(logging.WARNING)
    _, idxs, _ = loop_planning._prepare_execution(ctx, ["b", "c"], ["b", "c"], deps={}, resume=old)
    assert idxs == [90, 91] and ctx.plan_items is None
    assert any("no durable plan binding" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("fan_out", [0, 2])
def test_cross_project_resume_is_refused_before_any_lane(monkeypatch, tmp_path, fan_out):
    """A checkpoint that NAMES another project is refused at the loader —
    before preflight remaps its rows and before the parallel lane can
    enforce them (round-1: with fan-out the DAG ran the foreign graph and
    returned before the late project check; without it, the original's
    completed steps were silently absent from the new project)."""
    _loop_env(monkeypatch, tmp_path)
    import agent_loop as al
    seen = []
    first = al.run_agent_loop("cross", adapter=_Adapter(seen), preset_steps=PLAN,
                              max_steps=3, max_iterations=1, project="orig-proj")
    ck = load_checkpoint(first.loop_id)
    assert ck.project == "orig-proj" and ck.remaining_steps == PLAN[1:]
    seen.clear()
    res = al.run_agent_loop("cross", adapter=_Adapter(seen), max_steps=3, max_iterations=6,
                            parallel_fan_out=fan_out, project="other-proj",
                            resume_from_loop_id=first.loop_id)
    assert res.status != "done" and not seen, (res.status, res.stuck_reason, seen)
    assert "orig-proj" in (res.stuck_reason or "") and "other-proj" in (res.stuck_reason or "")
    # a checkpoint that names NO project (legacy / CLI-minted) is not refused
    from loop_planning import RestoredCheckpoint, _load_resume
    from loop_types import LoopContext
    ck_path = ckmod._checkpoint_path("lp-noproj")
    ck_path.write_text(json.dumps({"loop_id": "lp-noproj", "goal": "g", "project": "",
                                   "steps": ["A", "B"], "completed": []}))
    ctx = LoopContext(goal="g", project="other-proj", loop_id="lp-x", verbose=False)
    restored, refusal = _load_resume(ctx, "lp-noproj")
    assert refusal is None and isinstance(restored, RestoredCheckpoint)
    assert restored.steps == ["A", "B"] and restored.items is None


def test_carried_items_must_still_name_the_steps(monkeypatch, tmp_path, caplog):
    """A NEXT.md item id is the item's line number. Hop 1 mirrors the plan;
    a hand edit inserts a line ABOVE it before hop 2. The carried ids now
    name other lines, so the loader drops the identity (fresh items, soft
    gate) instead of marking the wrong items — and the run still runs."""
    import logging
    _loop_env(monkeypatch, tmp_path)
    import agent_loop as al
    import orch_items
    seen = []
    first = al.run_agent_loop("drift", adapter=_Adapter(seen), preset_steps=PLAN,
                              max_steps=3, max_iterations=1)
    ck1 = load_checkpoint(first.loop_id)
    project = ck1.project
    items = list(ck1.step_items)
    next_md = orch_items.project_dir(project) / "NEXT.md"
    next_md.write_text("- [ ] a note someone added by hand\n" + next_md.read_text())
    before = _next_items(project)
    caplog.set_level(logging.WARNING)
    seen.clear()
    second = al.run_agent_loop("drift", adapter=_Adapter(seen), max_steps=3,
                               max_iterations=6, resume_from_loop_id=first.loop_id)
    assert second.status == "done", second.stuck_reason
    assert any("no longer name" in r.getMessage() for r in caplog.records)
    after = _next_items(project)
    assert len(after) == len(before) + 2, "fresh items were not appended for the suffix"
    states = {it.index: it.state for it in after}
    # the shifted originals (now +1) were NOT marked; the fresh ones were
    shifted = [i + 1 for i in items[1:]]
    assert all(states[i] == " " for i in shifted), states
    fresh = [it.index for it in after if it.index not in {b.index for b in before}]
    assert all(states[i] == "x" for i in fresh), states
    ck2 = load_checkpoint(second.loop_id)
    assert ck2.plan_items is None and ck2.step_items == fresh


def test_duplicate_task_text_is_ambiguous_not_identity(monkeypatch, tmp_path):
    """A duplicate of a remaining step's text inserted right above it sits
    at the original's line offset and would verify in its place; text is
    not an identity, so the carried ids are dropped (r2 finding 2)."""
    import orch_items
    from loop_planning import _items_name_these_steps
    monkeypatch.setenv("MARO_ORCH_ROOT", str(tmp_path))
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path / "ws"))
    project = "dup-text"
    items = orch_items.append_next_items(project, PLAN)
    assert _items_name_these_steps(project, items, PLAN)
    next_md = orch_items.project_dir(project) / "NEXT.md"
    lines = next_md.read_text().splitlines()
    # insert a copy of step two directly above it: the copy takes its line
    lines.insert(items[1], f"- [ ] {PLAN[1]}")
    next_md.write_text("\n".join(lines) + "\n")
    _, ledger = orch_items.parse_next(project)
    assert {it.index: it.text for it in ledger}[items[1]] == PLAN[1]      # the line still "matches"
    assert not _items_name_these_steps(project, items[1:], PLAN[1:])
    # unmirrored slots are skipped; a wrong text or a missing line is no
    three = items[2] + 1                      # step three shifted down one line
    assert _items_name_these_steps(project, [-1, three], ["anything", PLAN[2]])
    assert not _items_name_these_steps(project, [three], ["other"])
    assert not _items_name_these_steps(project, [9999], [PLAN[2]])
    assert not _items_name_these_steps("", items, PLAN)


def test_mirror_never_carries_another_projects_items(monkeypatch):
    """Direct callers: a RestoredCheckpoint naming another project is not
    trusted at the mutation boundary either (r2 finding 5); a checkpoint
    naming NO project is carried."""
    import loop_planning
    from loop_planning import RestoredCheckpoint
    from loop_types import LoopContext

    class _O:
        def append_next_items(self, project, steps):
            return [90, 91][:len(steps)]

        def append_decision(self, project, lines):
            pass
    monkeypatch.setattr(loop_planning, "_orch", lambda: _O())
    ctx = LoopContext(goal="g", project="proj", loop_id="lp-foreign", verbose=False)
    other = RestoredCheckpoint(loop_id="oth", ckpt=None, steps=["b", "c"], items=[4, 5],
                               plan_items=[3, 4, 5], completed=[], project="elsewhere")
    assert loop_planning._mirror_plan_items(ctx, ["b", "c"], deps={}, resume=other) == [90, 91]
    assert ctx.plan_items is None
    ctx2 = LoopContext(goal="g", project="proj", loop_id="lp-noproj", verbose=False)
    legacy = RestoredCheckpoint(loop_id="leg", ckpt=None, steps=["b", "c"], items=[4, 5],
                                plan_items=[3, 4, 5], completed=[], project="")
    assert loop_planning._mirror_plan_items(ctx2, ["b", "c"], deps={}, resume=legacy) == [4, 5]
    assert ctx2.plan_items == [3, 4, 5]


def test_every_writer_carries_the_original_binding(monkeypatch, tmp_path):
    """Writer spy at the ONE writer all four loop sites alias: after a
    resume, every checkpoint written carries the ORIGINAL binding verbatim
    (removing `plan_items=` at any site fails this)."""
    _loop_env(monkeypatch, tmp_path)
    import agent_loop as al
    seen = []
    first = al.run_agent_loop("writers", adapter=_Adapter(seen), preset_steps=PLAN,
                              max_steps=3, max_iterations=1)
    binding = list(load_checkpoint(first.loop_id).plan_items)
    assert len(binding) == 3
    calls = []
    real = ckmod.write_checkpoint

    def spy(loop_id, goal, project, steps, step_outcomes, **kw):
        calls.append((list(steps), kw.get("plan_items"), kw.get("in_flight_index")))
        return real(loop_id, goal, project, steps, step_outcomes, **kw)
    monkeypatch.setattr(ckmod, "write_checkpoint", spy)
    second = al.run_agent_loop("writers", adapter=_Adapter(seen), max_steps=3,
                               max_iterations=6, resume_from_loop_id=first.loop_id)
    assert second.status == "done" and calls
    assert any(inflight is not None for _, _, inflight in calls), "in-flight site not exercised"
    assert any(inflight is None for _, _, inflight in calls), "post-step site not exercised"
    for steps, plan_items, _ in calls:
        assert list(plan_items or []) == binding, (steps, plan_items)


def test_cli_resume_runs_the_suffix_on_its_original_items(monkeypatch, tmp_path):
    """The literal production entry: `maro resume <loop_id>` → `_cmd_resume`
    → locked reload → `run_agent_loop(project=ckpt.project,
    resume_from_loop_id=)`. Through the real loop: no planner call, no
    second copy of the plan, the original items marked."""
    import argparse
    _loop_env(monkeypatch, tmp_path)
    import agent_loop as al
    import cli
    import llm
    import loop_planning
    seen = []
    first = al.run_agent_loop("cli goal", adapter=_Adapter(seen), preset_steps=PLAN,
                              max_steps=3, max_iterations=1)
    ck1 = load_checkpoint(first.loop_id)
    project, items = ck1.project, list(ck1.step_items)
    before = _next_items(project)
    monkeypatch.setattr(llm, "build_adapter", lambda *a, **k: _Adapter(seen))
    monkeypatch.setattr(cli, "_closure_verdict_pass", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_finalize_cli_deferred_learning", lambda *a, **k: None)

    def _no_planner(*a, **k):
        raise AssertionError("planner called on an explicit resume")
    monkeypatch.setattr(loop_planning, "_decompose", _no_planner)
    seen.clear()
    rc = cli._cmd_resume(argparse.Namespace(run_id=first.loop_id, verbose=False, format="text"))
    assert rc == 0, rc
    assert seen and not any("Step one" in c for c in seen), seen
    after = _next_items(project)
    assert [it.index for it in after] == [it.index for it in before]
    states = {it.index: it.state for it in after}
    assert all(states[i] == "x" for i in items), states


def test_cli_resume_refusal_reaches_the_operator(monkeypatch, tmp_path, capsys):
    """`maro resume` of a checkpoint that names another project: the loop
    refuses and the CLI prints WHY (json `stuck_reason`; text → stderr),
    not just "status: stuck" (r2 finding 6)."""
    import argparse
    _loop_env(monkeypatch, tmp_path)
    import agent_loop as al
    import cli
    import llm
    seen = []
    first = al.run_agent_loop("cli refuse", adapter=_Adapter(seen), preset_steps=PLAN,
                              max_steps=3, max_iterations=1, project="orig-proj")
    # the CLI resumes under the checkpoint's own project; force the mismatch
    # the way a direct caller would hit it
    real_loop = al.run_agent_loop
    monkeypatch.setattr(al, "run_agent_loop",
                        lambda goal, **kw: real_loop(goal, **{**kw, "project": "other-proj"}))
    monkeypatch.setattr(llm, "build_adapter", lambda *a, **k: _Adapter(seen))
    monkeypatch.setattr(cli, "_closure_verdict_pass", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_finalize_cli_deferred_learning", lambda *a, **k: None)
    seen.clear()
    rc = cli._cmd_resume(argparse.Namespace(run_id=first.loop_id, verbose=False, format="json"))
    out = capsys.readouterr().out
    assert rc != 0 and not seen
    payload = json.loads(out.strip().splitlines()[-1])
    assert payload["status"] != "done" and "orig-proj" in payload["stuck_reason"]
    rc = cli._cmd_resume(argparse.Namespace(run_id=first.loop_id, verbose=False, format="text"))
    err = capsys.readouterr().err
    assert rc != 0 and "orig-proj" in err and "other-proj" in err
