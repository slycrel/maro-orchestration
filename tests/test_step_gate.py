"""step_gate.py — the sequential lane's prerequisite gate (2026-09-16).

Pure verdict tests plus one loop-level flow: a step whose declared
prerequisite ended blocked is recorded blocked WITHOUT an adapter call while
an untagged successor still runs (soft implicit edge). Must-detect: removing
the gate makes the dependent's adapter call count 1, not 0.
"""
import json

import pytest

import step_gate
from step_gate import (GateVerdict, explicit_deps, latest_status_by_item,
                       plan_numbers, prerequisite_verdict)


class _Oc:
    def __init__(self, index, status):
        self.index = index
        self.status = status


# --- grammar agrees with the planner's ------------------------------------

@pytest.mark.parametrize("text,want", [
    ("Do x [after:1]", {1}),
    ("Do x [after:1,3]", {1, 3}),
    ("Do x [after:2] ", {2}),
    ("Do x [after: 1, 3]", {1, 3}),    # whitespace-tolerant inside the tag
    ("Do x [after:1 ,3 ]", {1, 3}),
    ("Do x", None),
    ("[after:1] Do x", None),          # tag must be terminal
    ("Do x [after:a]", None),
    ("Do x [after:]", None),
])
def test_explicit_deps_matches_planner_grammar(text, want):
    from planner import after_deps, parse_dependencies
    assert explicit_deps(text) == want
    assert after_deps(text) == want
    # The planner's plan-level parse reads the SAME regex object.
    _clean, deps = parse_dependencies(["one", text])
    assert deps[2] == (want if want is not None else {1})


def test_grammar_is_one_object_across_planner_and_gate():
    import planner
    assert planner._AFTER_RE is step_gate.AFTER_RE


def test_plan_numbers_skips_substeps_and_dupes():
    assert plan_numbers([10, 11, -1, 12, 11]) == {10: 1, 11: 2, 12: 4}
    assert plan_numbers([]) == {}


def test_latest_status_by_item_last_row_wins():
    rows = [_Oc(5, "blocked"), _Oc(6, "done"), _Oc(5, "done"), _Oc(-1, "blocked")]
    assert latest_status_by_item(rows) == {5: "done", 6: "done"}
    assert latest_status_by_item([{"index": 7, "status": "skipped"}]) == {7: "skipped"}


# --- verdicts --------------------------------------------------------------

_IDX = [10, 11, 12]
_DEPS = {1: set(), 2: {1}, 3: {2}}
_STEPS = ["one", "two", "three"]


def test_explicit_unmet_is_hard():
    v = prerequisite_verdict("three [after:1]", 12, deps=_DEPS, step_indices=_IDX,
                             step_outcomes=[_Oc(10, "blocked"), _Oc(11, "done")],
                             plan_steps=_STEPS)
    assert not v.ready and v.hard and v.explicit is True
    assert v.unmet == [(1, "blocked", "one")]
    assert "declared prerequisite not met: step 1 ended blocked" == v.reason


def test_explicit_met_is_ready_even_when_sequential_predecessor_failed():
    # Declared independence from step 2: its failure must not gate step 3.
    v = prerequisite_verdict("three [after:1]", 12, deps=_DEPS, step_indices=_IDX,
                             step_outcomes=[_Oc(10, "done"), _Oc(11, "blocked")])
    assert v.ready and not v.unmet


def test_implicit_unmet_is_soft_by_default_and_hard_when_configured():
    soft = prerequisite_verdict("three", 12, deps=_DEPS, step_indices=_IDX,
                                step_outcomes=[_Oc(11, "blocked")])
    assert not soft.ready and not soft.hard and soft.explicit is False
    assert soft.reason.startswith("sequential prerequisite not met")
    hard = prerequisite_verdict("three", 12, deps=_DEPS, step_indices=_IDX,
                                step_outcomes=[_Oc(11, "blocked")], gate_implicit=True)
    assert not hard.ready and hard.hard


def test_unknown_prerequisite_never_gates():
    # Step 1 was replaced by sub-steps (no row for item 10) — positive evidence only.
    v = prerequisite_verdict("two [after:1]", 11, deps=_DEPS, step_indices=_IDX,
                             step_outcomes=[_Oc(-1, "done")])
    assert v.ready and v.unknown == [1] and v.unmet == []


def test_retry_latest_status_wins():
    v = prerequisite_verdict("two [after:1]", 11, deps=_DEPS, step_indices=_IDX,
                             step_outcomes=[_Oc(10, "blocked"), _Oc(10, "done")])
    assert v.ready


def test_skipped_counts_as_unmet():
    v = prerequisite_verdict("two [after:1]", 11, deps=_DEPS, step_indices=_IDX,
                             step_outcomes=[_Oc(10, "skipped")])
    assert not v.ready and v.hard


@pytest.mark.parametrize("text", ["two [after:2]", "two [after:3]", "two [after:0]", "two [after:9]"])
def test_self_forward_and_out_of_plan_refs_are_ignored(text):
    v = prerequisite_verdict(text, 11, deps=_DEPS, step_indices=_IDX,
                             step_outcomes=[_Oc(11, "blocked"), _Oc(12, "blocked")])
    assert v.ready and not v.unmet and not v.unknown


def test_substep_without_tag_has_nothing_to_check():
    v = prerequisite_verdict("sub-step", -1, deps=_DEPS, step_indices=_IDX,
                             step_outcomes=[_Oc(10, "blocked")])
    assert v.ready and v.explicit is None and v.plan_no == 0


def test_substep_with_tag_still_honours_it():
    v = prerequisite_verdict("sub-step [after:1]", -1, deps=_DEPS, step_indices=_IDX,
                             step_outcomes=[_Oc(10, "blocked")])
    assert not v.ready and v.hard


def test_gate_implicit_enabled_reads_config(monkeypatch):
    import config
    monkeypatch.setattr(config, "get_bool",
                        lambda key, default=False: key == "execution.gate_implicit_prerequisites")
    assert step_gate.gate_implicit_enabled() is True


# --- loop-level flow -------------------------------------------------------

def test_loop_gate_skips_dependent_of_blocked_step_without_adapter_call(monkeypatch, tmp_path):
    """Step 1 blocks terminally, run advances; step 2 [after:1] must be recorded
    blocked with no adapter call; untagged step 3 (soft edge) still runs."""
    monkeypatch.setenv("MARO_ORCH_ROOT", str(tmp_path))
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path / "ws"))
    import agent_loop as al
    import loop_execute
    monkeypatch.setattr(loop_execute, "_free_auto_ralph_enabled", lambda: False)

    # Blocked-step handling: terminal, run advances ("" status = step dies,
    # run continues — the token-runaway brake's contract).
    def _advance(ctx, blk):
        return ("normal", blk.step_idx, "", "", 0, 0, blk.replan_count)
    monkeypatch.setattr(loop_execute, "_process_blocked_step", _advance)

    calls = []

    class _Adapter:
        model_key = "test"

        def complete(self, messages, **kwargs):
            from llm import LLMResponse, ToolCall
            user = " ".join(m.content for m in messages if getattr(m, "role", "") == "user")
            calls.append(user)
            if "Step one" in user and "Step two" not in user[:200]:
                return LLMResponse(content="", tool_calls=[ToolCall(
                    name="flag_stuck", arguments={"reason": "no network"})],
                    input_tokens=1, output_tokens=1)
            return LLMResponse(content="", tool_calls=[ToolCall(
                name="complete_step", arguments={"result": "ok", "summary": "ok"})],
                input_tokens=1, output_tokens=1)

    result = al.run_agent_loop(
        "gate flow",
        adapter=_Adapter(),
        preset_steps=["Step one: fetch the dataset",
                      "Step two: transform the dataset [after:1]",
                      "Step three: write the summary"],
        max_steps=3,
        max_iterations=6,
    )
    by_text = {s.text: s for s in result.steps}
    two = next(s for t, s in by_text.items() if "Step two" in t)
    three = next(s for t, s in by_text.items() if "Step three" in t)
    assert two.status == "blocked"
    assert two.result.startswith("not executed — declared prerequisite not met: step 1 ended blocked")
    assert three.status == "done"
    # The gated step never reached the adapter; the soft-edged step did.
    assert not any("Step two: transform" in c and "Current step" in c for c in calls), \
        "dependent step was sent to the adapter despite a blocked prerequisite"
    assert any("Step three" in c for c in calls)


# --- plan identity ---------------------------------------------------------

def test_plan_identity_intact_cases():
    from step_gate import plan_identity_intact
    assert plan_identity_intact([10, 11, 12], _DEPS) == (True, "")
    ok, why = plan_identity_intact([10, 11, 12], _DEPS, resumed=True)
    assert not ok and "resumed" in why
    ok, why = plan_identity_intact([10, 11, 12, 13], _DEPS)      # shaping split a step
    assert not ok and "reshaped" in why
    ok, why = plan_identity_intact([10, 11, 10], _DEPS)
    assert not ok and "appears twice" in why
    assert plan_identity_intact([10, -1, 12], {1: set(), 2: {1}, 3: {2}}) == (True, "")
    assert plan_identity_intact([], {}) == (True, "")


def test_identity_not_intact_degrades_hard_edges_to_soft():
    v = prerequisite_verdict("three [after:1]", 12, deps=_DEPS, step_indices=_IDX,
                             step_outcomes=[_Oc(10, "blocked")], identity_intact=False)
    assert not v.ready and not v.hard and v.unmet == [(1, "blocked", "")]
    v = prerequisite_verdict("three", 12, deps=_DEPS, step_indices=_IDX,
                             step_outcomes=[_Oc(11, "blocked")], gate_implicit=True,
                             identity_intact=False)
    assert not v.ready and not v.hard


def test_superseded_prerequisite_reads_as_unknown():
    # Step 1 was split into sub-steps; its blocked row records the
    # replacement, not a failed prerequisite.
    v = prerequisite_verdict("two [after:1]", 11, deps=_DEPS, step_indices=_IDX,
                             step_outcomes=[_Oc(10, "blocked"), _Oc(-1, "done")],
                             superseded={10})
    assert v.ready and v.unknown == [1] and v.unmet == []
    v = prerequisite_verdict("two [after:1]", 11, deps=_DEPS, step_indices=_IDX,
                             step_outcomes=[_Oc(10, "blocked")], superseded={"x", None})
    assert not v.ready and v.hard


def test_loop_gate_records_bookkeeping_for_a_gated_step(monkeypatch, tmp_path):
    """A gated step advances the counters, marks its NEXT.md item blocked and
    lands in the checkpoint — the same trail an executed step leaves."""
    monkeypatch.setenv("MARO_ORCH_ROOT", str(tmp_path))
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path / "ws"))
    import agent_loop as al
    import loop_execute
    monkeypatch.setattr(loop_execute, "_free_auto_ralph_enabled", lambda: False)
    monkeypatch.setattr(loop_execute, "_process_blocked_step",
                        lambda ctx, blk: ("normal", blk.step_idx, "", "", 0, 0, blk.replan_count))
    marked = []
    import orch as o
    _real_mark = o.mark_item
    monkeypatch.setattr(o, "mark_item", lambda project, idx, state: marked.append((idx, state)))

    class _Adapter:
        model_key = "test"

        def complete(self, messages, **kwargs):
            from llm import LLMResponse, ToolCall
            user = " ".join(m.content for m in messages if getattr(m, "role", "") == "user")
            if "Step one" in user and "Step two" not in user[:200]:
                return LLMResponse(content="", tool_calls=[ToolCall(
                    name="flag_stuck", arguments={"reason": "no network"})],
                    input_tokens=1, output_tokens=1)
            return LLMResponse(content="", tool_calls=[ToolCall(
                name="complete_step", arguments={"result": "ok", "summary": "ok"})],
                input_tokens=1, output_tokens=1)

    result = al.run_agent_loop(
        "gate bookkeeping", adapter=_Adapter(),
        preset_steps=["Step one: fetch", "Step two: transform [after:1]", "Step three: report"],
        max_steps=3, max_iterations=6,
    )
    rows = {s.text: s for s in result.steps}
    two = next(s for t, s in rows.items() if "Step two" in t)
    three = next(s for t, s in rows.items() if "Step three" in t)
    assert two.status == "blocked"
    # Iterations are distinct and increasing across the gated row.
    assert two.iteration != three.iteration
    assert (two.index, o.STATE_BLOCKED) in marked
    from checkpoint import load_checkpoint
    ck = load_checkpoint(result.loop_id)
    assert ck is not None
    assert any(getattr(c, "index", None) == two.index and getattr(c, "status", "") == "blocked"
               for c in ck.completed)
