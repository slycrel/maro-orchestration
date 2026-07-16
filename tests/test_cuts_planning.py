"""Cuts-first planning (Qix-cuts decree, 2026-07-10).

The pattern: 0-4 narrowing cuts off the rectangle before committing a plan —
committed constraints from prior knowledge, 0-2 cheap probes, then the plan
for the bounded remainder is drawn AFTER probe evidence lands (boundary
expansion in loop_execute). See planner.draw_cuts / docs/DEFAULTS.md
`planner.cuts_first`.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from planner import (
    BOUNDARY_TAG,
    Cuts,
    _cuts_plan,
    decompose,
    draw_cuts,
    is_boundary_step,
    strip_boundary_tag,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _adapter_returning(*payloads):
    """Fake adapter that returns each payload in sequence (last one repeats)."""
    calls = {"n": 0, "systems": [], "kwargs": []}

    class _Adapter:
        def complete(self, messages, **kw):
            for m in messages:
                if getattr(m, "role", "") == "system":
                    calls["systems"].append(getattr(m, "content", ""))
            calls["kwargs"].append(kw)
            i = min(calls["n"], len(payloads) - 1)
            calls["n"] += 1
            return SimpleNamespace(content=payloads[i], input_tokens=5, output_tokens=20)

    return _Adapter(), calls


_CUTS_JSON = json.dumps({
    "known_constraints": [
        "Maverik stations often sell ethanol-free gas (basis: prior knowledge)",
        "The ask is location-bound to Manti, Utah (basis: goal text)",
    ],
    "probes": ["Search for Maverik station locations within 15 miles of Manti, Utah"],
    "bounded": False,
    "remainder": "verify ethanol-free availability at the located stations and report the nearest",
})

_BOUNDED_JSON = json.dumps({
    "known_constraints": ["Config lives in ~/.maro/config.yml (basis: provided context)"],
    "probes": [],
    "bounded": True,
    "remainder": "",
})


def _cuts_config(monkeypatch, enabled=True):
    """Point config.get('planner.cuts_first') at `enabled`, pass through the rest."""
    import config as _config
    _orig = _config.get

    def _fake_get(key, default=None):
        if key == "planner.cuts_first":
            return enabled
        return _orig(key, default)

    monkeypatch.setattr(_config, "get", _fake_get)


# ---------------------------------------------------------------------------
# boundary tag helpers
# ---------------------------------------------------------------------------

class TestBoundaryTag:
    def test_is_boundary_step(self):
        assert is_boundary_step(f"plan the remainder {BOUNDARY_TAG}")
        assert is_boundary_step(f"plan the remainder {BOUNDARY_TAG} [after:2]")
        assert not is_boundary_step("plan the remainder")
        assert not is_boundary_step("")

    def test_strip_boundary_tag(self):
        assert strip_boundary_tag(f"plan the remainder {BOUNDARY_TAG}") == "plan the remainder"
        assert strip_boundary_tag("no tag here") == "no tag here"
        assert strip_boundary_tag("") == ""


# ---------------------------------------------------------------------------
# draw_cuts
# ---------------------------------------------------------------------------

class TestDrawCuts:
    def test_happy_path(self):
        adapter, _ = _adapter_returning(_CUTS_JSON)
        cuts = draw_cuts("Where can I get non-ethanol gas in or around Manti, Utah?", adapter)
        assert cuts is not None
        assert len(cuts.known_constraints) == 2
        assert len(cuts.probes) == 1
        assert cuts.bounded is False
        assert "verify ethanol-free" in cuts.remainder

    def test_probes_capped_at_two(self):
        payload = json.dumps({
            "known_constraints": ["c"],
            "probes": ["p1", "p2", "p3", "p4"],
            "bounded": False,
            "remainder": "rest",
        })
        adapter, _ = _adapter_returning(payload)
        cuts = draw_cuts("some goal", adapter)
        assert len(cuts.probes) == 2

    def test_adapter_failure_returns_none(self):
        class _Failing:
            def complete(self, messages, **kw):
                raise RuntimeError("simulated outage")
        assert draw_cuts("goal", _Failing()) is None

    def test_non_json_returns_none(self):
        adapter, _ = _adapter_returning("I think you should consider several things...")
        assert draw_cuts("goal", adapter) is None

    def test_empty_inputs_return_none(self):
        adapter, _ = _adapter_returning(_CUTS_JSON)
        assert draw_cuts("", adapter) is None
        assert draw_cuts("goal", None) is None

    def test_context_extras_reach_system_prompt(self):
        adapter, calls = _adapter_returning(_CUTS_JSON)
        draw_cuts("goal", adapter, context_extras="LESSONS: prefer official pages")
        assert any("prefer official pages" in s for s in calls["systems"])


# ---------------------------------------------------------------------------
# _cuts_plan
# ---------------------------------------------------------------------------

class TestCutsPlan:
    def test_probes_then_boundary(self):
        cuts = Cuts(known_constraints=["c"], probes=["probe one", "probe two"],
                    bounded=False, remainder="finish the work")
        plan = _cuts_plan(cuts, "the goal")
        assert plan[0] == "probe one"
        assert plan[1] == "probe two"
        assert is_boundary_step(plan[2])
        assert "finish the work" in plan[2]

    def test_missing_remainder_falls_back_to_goal(self):
        cuts = Cuts(probes=["probe"], remainder="")
        plan = _cuts_plan(cuts, "the original goal")
        assert "the original goal" in plan[-1]


# ---------------------------------------------------------------------------
# decompose integration
# ---------------------------------------------------------------------------

class TestDecomposeCutsFirst:
    def test_probes_become_the_plan(self, monkeypatch):
        _cuts_config(monkeypatch, enabled=True)
        adapter, calls = _adapter_returning(_CUTS_JSON)
        steps = decompose("find non-ethanol gas near Manti", adapter, max_steps=8)
        # One cuts call, no full-plan commit
        assert calls["n"] == 1
        assert steps[0].startswith("Search for Maverik")
        assert is_boundary_step(steps[-1])

    def test_bounded_cuts_inject_constraints_into_plan_prompt(self, monkeypatch):
        _cuts_config(monkeypatch, enabled=True)
        adapter, calls = _adapter_returning(_BOUNDED_JSON, '["step one", "step two"]')
        steps = decompose("check the config timeout value", adapter, max_steps=4)
        assert steps == ["step one", "step two"]
        # The plan call's system prompt carries the committed constraints
        assert any("COMMITTED CONSTRAINTS" in s for s in calls["systems"][1:])

    def test_flag_off_means_no_cuts_call(self, monkeypatch):
        _cuts_config(monkeypatch, enabled=False)
        adapter, calls = _adapter_returning('["step one"]')
        steps = decompose("check the config timeout value", adapter, max_steps=4)
        assert steps == ["step one"]
        assert all("narrowing pass" not in s for s in calls["systems"])

    def test_allow_cuts_false_skips_cuts(self, monkeypatch):
        _cuts_config(monkeypatch, enabled=True)
        adapter, calls = _adapter_returning('["step one"]')
        steps = decompose("check the config timeout value", adapter, max_steps=4,
                          allow_cuts=False)
        assert steps == ["step one"]
        assert all("narrowing pass" not in s for s in calls["systems"])

    def test_wide_goals_skip_cuts(self, monkeypatch):
        """Wide/deep goals go to staged-pass — cuts never fire."""
        _cuts_config(monkeypatch, enabled=True)
        adapter, calls = _adapter_returning('["Pass 1/2 — read", "Pass 2/2 — synthesize [after:1]"]')
        decompose("adversarial review of the entire codebase", adapter, max_steps=8)
        assert all("narrowing pass" not in s for s in calls["systems"])

    def test_cuts_failure_falls_through_to_normal_plan(self, monkeypatch):
        """draw_cuts returning garbage must never break decomposition."""
        _cuts_config(monkeypatch, enabled=True)
        adapter, calls = _adapter_returning("not json at all", '["step one"]')
        steps = decompose("check the config timeout value", adapter, max_steps=4)
        assert steps == ["step one"]


# ---------------------------------------------------------------------------
# _shape_steps leaves boundary steps intact
# ---------------------------------------------------------------------------

class TestShapeStepsBoundaryExemption:
    def test_boundary_step_not_split(self):
        from loop_planning import _shape_steps
        # "run ... and verify result" would normally trip the exec+analyze split
        step = f"Plan and complete: run checks and verify result for stations {BOUNDARY_TAG}"
        shaped = _shape_steps([step])
        assert shaped == [step]

    def test_normal_steps_still_split(self):
        from loop_planning import _shape_steps, _is_combined_exec_analyze
        step = "run pytest and analyze the failures"
        assert _is_combined_exec_analyze(step)
        shaped = _shape_steps([step])
        assert len(shaped) == 2


# ---------------------------------------------------------------------------
# boundary expansion in the loop (integration)
# ---------------------------------------------------------------------------

def _no_milestones_review():
    review = MagicMock()
    review.milestone_step_indices = []
    review.scope = "narrow"
    review.flags = []
    return review


def test_boundary_step_expanded_with_probe_evidence(monkeypatch, tmp_path):
    """A [boundary] step is expanded mid-loop via planner.decompose with the
    probe findings in ancestry context and allow_cuts=False — the plan for
    the bounded remainder is drawn after evidence lands."""
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    from agent_loop import run_agent_loop, _DryRunAdapter
    import loop_planning

    initial_plan = [
        "Probe: search for Maverik stations near Manti",
        f"Plan and complete the remaining bounded work using findings from "
        f"the prior steps: confirm availability at located stations {BOUNDARY_TAG}",
    ]
    mock_expand = MagicMock(return_value=["bounded step A", "bounded step B"])

    with patch.object(loop_planning, "_decompose_impl", return_value=initial_plan), \
         patch("pre_flight.review_plan", return_value=_no_milestones_review()), \
         patch("planner.decompose", mock_expand):
        result = run_agent_loop(
            "find non-ethanol gas near Manti",
            adapter=_DryRunAdapter(),
            dry_run=False,
            max_iterations=10,
        )

    assert mock_expand.called
    kwargs = mock_expand.call_args.kwargs
    assert kwargs.get("allow_cuts") is False
    assert "PROBE FINDINGS" in kwargs.get("ancestry_context", "")
    texts = [s.text for s in result.steps]
    assert any("bounded step A" in t for t in texts)
    assert any("bounded step B" in t for t in texts)
    # The marker step itself never executes as a step
    assert not any(BOUNDARY_TAG in t for t in texts)


def test_boundary_expansion_carries_goal_priority_directive(monkeypatch, tmp_path):
    """#23c under cuts-first (run 75fe8b4e): the boundary remainder text may
    drop the goal's priority phrasing, so the expansion decompose can't detect
    it — the binding directive must be carried across the boundary explicitly."""
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    from agent_loop import run_agent_loop, _DryRunAdapter
    import loop_planning

    goal = ("Sweep sources. Remaining work, in priority order: 1. fetch HN. "
            "2. fetch Reddit. 3. write synthesis.")
    initial_plan = [
        "Probe: confirm HN DOM structure",
        f"Plan and complete the remaining bounded work using findings from "
        f"the prior steps: fetch both sources then synthesize {BOUNDARY_TAG}",
    ]
    mock_expand = MagicMock(return_value=["bounded step A"])

    with patch.object(loop_planning, "_decompose_impl", return_value=initial_plan), \
         patch("pre_flight.review_plan", return_value=_no_milestones_review()), \
         patch("planner.decompose", mock_expand):
        run_agent_loop(goal, adapter=_DryRunAdapter(), dry_run=False,
                       max_iterations=10)

    assert mock_expand.called
    _ctx = mock_expand.call_args.kwargs.get("ancestry_context", "")
    assert "PRIORITY ORDER" in _ctx
    assert "in priority order" in _ctx  # original goal carried as the source


def test_boundary_expansion_carries_step_ceiling(monkeypatch, tmp_path):
    """Step-ceiling analog of the #23c carry above: the boundary remainder
    text may drop the goal's "N steps max" phrasing, so the expansion
    decompose can't detect it — the binding directive is carried explicitly
    and the re-decompose's max_steps is clamped to the ceiling."""
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    from agent_loop import run_agent_loop, _DryRunAdapter
    import loop_planning

    goal = "Find the nearest station and report it, 2-3 steps maximum."
    initial_plan = [
        "Probe: search for stations near Manti",
        f"Plan and complete the remaining bounded work using findings from "
        f"the prior steps: report the nearest station {BOUNDARY_TAG}",
    ]
    mock_expand = MagicMock(return_value=["bounded step A"])

    with patch.object(loop_planning, "_decompose_impl", return_value=initial_plan), \
         patch("pre_flight.review_plan", return_value=_no_milestones_review()), \
         patch("planner.decompose", mock_expand):
        run_agent_loop(goal, adapter=_DryRunAdapter(), dry_run=False,
                       max_iterations=10)

    assert mock_expand.called
    kwargs = mock_expand.call_args.kwargs
    assert kwargs.get("max_steps") == 3  # min(5, ceiling 3)
    _ctx = kwargs.get("ancestry_context", "")
    assert "STEP-COUNT CEILING" in _ctx
    assert "AT MOST 3" in _ctx
    assert "2-3 steps maximum" in _ctx  # original goal carried as the source


def test_boundary_expansion_failure_degrades_to_broad_step(monkeypatch, tmp_path):
    """If expansion returns nothing usable, the boundary step runs as one
    broad step with the tag stripped — degrade, don't die."""
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    from agent_loop import run_agent_loop, _DryRunAdapter
    import loop_planning

    remainder = "confirm availability at located stations"
    initial_plan = [
        "Probe: search for stations",
        f"Plan and complete the remaining bounded work using findings from "
        f"the prior steps: {remainder} {BOUNDARY_TAG}",
    ]

    def _expansion_fails(goal, adapter, **kw):
        return [goal]  # verbatim fallback shape — "nothing usable"

    with patch.object(loop_planning, "_decompose_impl", return_value=initial_plan), \
         patch("pre_flight.review_plan", return_value=_no_milestones_review()), \
         patch("planner.decompose", side_effect=_expansion_fails):
        result = run_agent_loop(
            "find non-ethanol gas near Manti",
            adapter=_DryRunAdapter(),
            dry_run=False,
            max_iterations=10,
        )

    texts = [s.text for s in result.steps]
    # The remainder executed as a normal broad step, tag stripped
    assert any(remainder in t and BOUNDARY_TAG not in t for t in texts)
