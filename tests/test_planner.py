"""Tests for planner.py — decomposition, dependency parsing, execution levels."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from planner import parse_steps, parse_dependencies, build_execution_levels


# ---------------------------------------------------------------------------
# parse_steps
# ---------------------------------------------------------------------------

def test_parse_steps_from_json_array():
    assert parse_steps('["step 1", "step 2"]', 10) == ["step 1", "step 2"]


def test_parse_steps_with_markdown():
    assert parse_steps('```json\n["a", "b"]\n```', 10) == ["a", "b"]


def test_parse_steps_respects_max():
    assert len(parse_steps('["a","b","c","d","e"]', 3)) == 3


def test_parse_steps_returns_none_on_invalid():
    assert parse_steps("not json at all", 10) is None


def test_parse_steps_drops_dependency_only_placeholders():
    assert parse_steps('["real step", "[after:4]"]', 10) == ["real step"]


# ---------------------------------------------------------------------------
# parse_dependencies
# ---------------------------------------------------------------------------

def test_parse_deps_no_tags_is_sequential():
    steps = ["Clone repo", "Map structure", "Read code"]
    clean, deps = parse_dependencies(steps)
    assert clean == steps
    assert deps == {1: set(), 2: {1}, 3: {2}}


def test_parse_deps_with_after_tags():
    steps = [
        "Clone repo",
        "Map structure [after:1]",
        "Read core [after:2]",
        "Read I/O [after:2]",
        "Synthesize [after:3,4]",
    ]
    clean, deps = parse_dependencies(steps)
    assert clean[0] == "Clone repo"
    assert clean[1] == "Map structure"
    assert "[after:" not in clean[2]
    assert deps[3] == {2}
    assert deps[4] == {2}
    assert deps[5] == {3, 4}


def test_parse_deps_strips_tag_from_text():
    steps = ["Do something [after:1]"]
    clean, _ = parse_dependencies(steps)
    assert clean[0] == "Do something"


# ---------------------------------------------------------------------------
# build_execution_levels
# ---------------------------------------------------------------------------

def test_levels_sequential():
    deps = {1: set(), 2: {1}, 3: {2}}
    levels = build_execution_levels(deps)
    assert levels == [[1], [2], [3]]


def test_levels_parallel_middle():
    deps = {1: set(), 2: {1}, 3: {1}, 4: {1}, 5: {2, 3, 4}}
    levels = build_execution_levels(deps)
    assert levels[0] == [1]
    assert set(levels[1]) == {2, 3, 4}  # parallel
    assert levels[2] == [5]


def test_levels_all_independent():
    deps = {1: set(), 2: set(), 3: set()}
    levels = build_execution_levels(deps)
    assert levels == [[1, 2, 3]]


def test_levels_diamond():
    # 1 → 2,3 → 4
    deps = {1: set(), 2: {1}, 3: {1}, 4: {2, 3}}
    levels = build_execution_levels(deps)
    assert levels[0] == [1]
    assert set(levels[1]) == {2, 3}
    assert levels[2] == [4]


def test_levels_empty():
    assert build_execution_levels({}) == []


# ---------------------------------------------------------------------------
# Large-scope review detection
# ---------------------------------------------------------------------------

from planner import _is_large_scope_review, decompose


class TestLargeScopeDetection:
    def test_positive_cases(self):
        assert _is_large_scope_review("adversarial review of the entire codebase")
        assert _is_large_scope_review("comprehensive review of the full repo")
        assert _is_large_scope_review("full audit of all modules")
        assert _is_large_scope_review("codebase review for security issues")
        assert _is_large_scope_review("audit the codebase")
        assert _is_large_scope_review("review the entire repo")

    def test_negative_cases(self):
        assert not _is_large_scope_review("review the auth module")
        assert not _is_large_scope_review("analyze test failures in test_memory.py")
        assert not _is_large_scope_review("write a summary of memory.py")
        assert not _is_large_scope_review("run the test suite")


class TestStagedPassDecomposition:
    """decompose() should return staged passes for large-scope goals."""

    def _make_adapter(self, response_json: str):
        from types import SimpleNamespace
        class _Adapter:
            def complete(self, messages, **kw):
                return SimpleNamespace(
                    content=response_json,
                    input_tokens=5,
                    output_tokens=20,
                )
        return _Adapter()

    def test_staged_pass_returned_for_large_scope(self):
        passes = [
            "Pass 1/3 — Architecture: read CLAUDE.md and map modules",
            "Pass 2/3 — Core: audit agent_loop.py and step_exec.py [after:1]",
            "Pass 3/3 — Synthesize findings [after:1,2]",
        ]
        adapter = self._make_adapter(f'["{passes[0]}", "{passes[1]}", "{passes[2]}"]')
        result = decompose("adversarial review of the entire codebase", adapter, max_steps=8)
        assert len(result) == 3
        assert "Pass 1" in result[0]
        assert "Pass 3" in result[2]

    def test_staged_pass_not_triggered_for_normal_goal(self):
        """Normal goals should go through multi-plan, not staged-pass."""
        steps = ["Step 1: read auth.py", "Step 2: analyze patterns", "Step 3: write report"]
        adapter = self._make_adapter(f'["{steps[0]}", "{steps[1]}", "{steps[2]}"]')
        result = decompose("review the auth module for injection risks", adapter, max_steps=8)
        # Should return the multi-plan result (all 3 steps, since adapter always returns same JSON)
        assert len(result) == 3


class TestDecomposeFallback:
    """When the LLM is unavailable, decompose must return the goal verbatim
    as a single step — never chop it on punctuation.

    The old heuristic fallback (orch.decompose_goal, split on [.;]) turned
    "Flag claims into artifacts/flagged-claims.md [after:3,4,5]" into the
    fragments "Flag claims into artifacts/flagged-claims" and
    "md [after:3,4,5]", each of which became a standalone nonsense goal.
    Traced across ~40 error/stuck production runs, 2026-05-13..17.
    """

    class _FailingAdapter:
        def complete(self, messages, **kw):
            raise RuntimeError("claude subprocess failed (rc=1): simulated outage")

    def test_failing_adapter_returns_goal_verbatim(self):
        goal = "Flag claims rated weak or contested into artifacts/flagged-claims.md [after:3,4,5]"
        result = decompose(goal, self._FailingAdapter(), max_steps=8)
        assert result == [goal]

    def test_failing_adapter_never_splits_on_periods(self):
        goal = "Read config.yml. Update the timeout. Run scripts/test-safe.sh."
        result = decompose(goal, self._FailingAdapter(), max_steps=8)
        assert result == [goal]


# ---------------------------------------------------------------------------
# estimate_goal_scope (Phase 58)
# ---------------------------------------------------------------------------

from planner import estimate_goal_scope


class TestEstimateGoalScope:
    def test_narrow_what_question(self):
        assert estimate_goal_scope("what is the timeout value") == "narrow"

    def test_narrow_short_lookup(self):
        assert estimate_goal_scope("list the active skills") == "narrow"

    def test_narrow_check(self):
        assert estimate_goal_scope("check if the scheduler is enabled") == "narrow"

    def test_wide_codebase_review(self):
        assert estimate_goal_scope("do a full audit of the entire codebase") == "wide"

    def test_wide_comprehensive_review(self):
        assert estimate_goal_scope("adversarial review of the repo") == "wide"

    def test_deep_build_from_scratch(self):
        assert estimate_goal_scope("build a complete self-improving AI system from scratch") == "deep"

    def test_medium_research_task(self):
        # Research + analyze goal → medium (not narrow, not wide)
        scope = estimate_goal_scope("research winning Polymarket strategies from last month")
        assert scope == "medium"

    def test_medium_implement_feature(self):
        scope = estimate_goal_scope("implement rate limit retry logic in llm.py")
        assert scope == "medium"

    def test_empty_goal_is_narrow_or_medium(self):
        # Edge case: empty string
        scope = estimate_goal_scope("")
        assert scope in ("narrow", "medium")

    def test_is_large_scope_review_wide(self):
        from planner import _is_large_scope_review
        assert _is_large_scope_review("review the entire repo") is True

    def test_is_large_scope_review_narrow(self):
        from planner import _is_large_scope_review
        assert _is_large_scope_review("check the config") is False

    def test_is_large_scope_review_deep(self):
        from planner import _is_large_scope_review
        assert _is_large_scope_review("build a complete production-ready agent from scratch") is True


# ---------------------------------------------------------------------------
# decompose — user-context injection resolves via the workspace overlay
# ---------------------------------------------------------------------------

class _CapturingAdapter:
    """Fake adapter: records every system prompt, returns a fixed plan."""

    def __init__(self):
        self.system_prompts = []

    def complete(self, messages, **kwargs):
        for m in messages:
            if getattr(m, "role", "") == "system":
                self.system_prompts.append(getattr(m, "content", ""))

        class _Resp:
            content = '["step one", "step two"]'
            input_tokens = 10
            output_tokens = 5
        return _Resp()


class TestDecomposeUserContextInjection:
    def test_workspace_overlay_feeds_decompose_prompt(self, tmp_path):
        """USER CONTEXT comes from <workspace>/user/ when the overlay exists
        (conftest points MARO_WORKSPACE at tmp_path)."""
        from planner import decompose

        user_dir = tmp_path / "user"
        user_dir.mkdir()
        (user_dir / "GOALS.md").write_text("OVERLAY-GOALS-MARKER content")

        adapter = _CapturingAdapter()
        decompose("check the config", adapter, max_steps=4)

        combined = "\n".join(adapter.system_prompts)
        assert "USER CONTEXT (GOALS.md)" in combined
        assert "OVERLAY-GOALS-MARKER" in combined

    def test_fresh_install_gets_no_personal_data(self, tmp_path, monkeypatch):
        """With no overlay (empty workspace), decompose falls back to the
        shipped neutral templates — never someone else's personal context."""
        from planner import decompose

        adapter = _CapturingAdapter()
        decompose("check the config", adapter, max_steps=4)

        combined = "\n".join(adapter.system_prompts).lower()
        for marker in ("jeremy", "slycrel", "retatrutide", "edgar_allen_bot"):
            assert marker not in combined


class TestGoalPriorityOrder:
    """BACKLOG #23c: an explicit goal-stated priority order binds step order.

    r3 specimen (run 5c40740e): goal said "Remaining work, in priority
    order: 1. X/Twitter sweep..." and the planner scheduled Reddit first,
    consuming the whole budget there.
    """

    def test_detects_in_priority_order(self):
        from planner import goal_states_priority_order
        assert goal_states_priority_order(
            "Remaining work, in priority order: 1. X/Twitter sweep 2. Reddit")

    def test_detects_priority_order_colon(self):
        from planner import goal_states_priority_order
        assert goal_states_priority_order(
            "Priority order:\n1. sweep X\n2. sweep Reddit")

    def test_detects_priorities_numbered(self):
        from planner import goal_states_priority_order
        assert goal_states_priority_order(
            "Do the audit. Priorities:\n1) find bugs\n2) write report")

    def test_plain_goal_not_detected(self):
        from planner import goal_states_priority_order
        assert not goal_states_priority_order(
            "review the auth module and prioritize readability")

    def test_directive_injected_into_decompose_system(self):
        from types import SimpleNamespace
        from planner import decompose, _PRIORITY_DIRECTIVE
        seen_systems = []

        class _Adapter:
            def complete(self, messages, **kw):
                seen_systems.append(messages[0].content)
                return SimpleNamespace(content='["step one", "step two"]',
                                       input_tokens=5, output_tokens=5)

        decompose("Do the sweep, in priority order: 1. X 2. Reddit 3. HN",
                  _Adapter(), max_steps=8)
        assert any(_PRIORITY_DIRECTIVE in s for s in seen_systems)

    def test_directive_reaches_staged_pass_lane(self):
        from types import SimpleNamespace
        from planner import decompose, _PRIORITY_DIRECTIVE
        seen_systems = []

        class _Adapter:
            def complete(self, messages, **kw):
                seen_systems.append(messages[0].content)
                return SimpleNamespace(content='["Pass 1/2 — X sweep", "Pass 2/2 — synth [after:1]"]',
                                       input_tokens=5, output_tokens=5)

        decompose("adversarial review of the entire codebase, in priority "
                  "order: 1. core loop 2. memory", _Adapter(), max_steps=8)
        assert any(_PRIORITY_DIRECTIVE in s for s in seen_systems)

    def test_no_directive_without_priority_goal(self):
        from types import SimpleNamespace
        from planner import decompose, _PRIORITY_DIRECTIVE
        seen_systems = []

        class _Adapter:
            def complete(self, messages, **kw):
                seen_systems.append(messages[0].content)
                return SimpleNamespace(content='["step one", "step two"]',
                                       input_tokens=5, output_tokens=5)

        decompose("review the auth module for injection risks",
                  _Adapter(), max_steps=8)
        assert not any(_PRIORITY_DIRECTIVE in s for s in seen_systems)

    def test_prompt_names_rate_limited_batching(self):
        from planner import DECOMPOSE_SYSTEM
        assert "rate-limited network operations" in DECOMPOSE_SYSTEM
        assert "GOAL PRIORITY ORDER" in DECOMPOSE_SYSTEM

    def test_directive_reaches_draw_cuts_under_cuts_first(self, monkeypatch):
        # Regression (run 75fe8b4e, 2026-07-12): the cuts-first probe path
        # returns BEFORE the old injection point, so the directive never
        # reached any prompt. Detection now happens above the cuts block and
        # must arrive in draw_cuts' context_extras.
        import config
        import planner
        from planner import decompose, _PRIORITY_DIRECTIVE
        from types import SimpleNamespace
        monkeypatch.setattr(config, "get", lambda key, default=None:
                            True if key == "planner.cuts_first" else default)
        seen_extras = []

        def _spy_draw_cuts(goal, adapter, context_extras=""):
            seen_extras.append(context_extras)
            return None  # cuts fail → decompose falls through to normal lanes

        monkeypatch.setattr(planner, "draw_cuts", _spy_draw_cuts)

        class _Adapter:
            def complete(self, messages, **kw):
                return SimpleNamespace(content='["step one", "step two"]',
                                       input_tokens=5, output_tokens=5)

        decompose("Do the sweep, in priority order: 1. X 2. Reddit 3. HN",
                  _Adapter(), max_steps=8)
        assert seen_extras, "draw_cuts was never called — cuts gate broken?"
        assert any(_PRIORITY_DIRECTIVE in x for x in seen_extras)


# ---------------------------------------------------------------------------
# Goal-stated step-count ceiling (BACKLOG: step-count constraint ignored)
# ---------------------------------------------------------------------------
# Specimen: goal said "2-3 steps maximum"; the planner produced 7 steps /
# 1.55M tokens for what one shell step answers. Same family as #23c above,
# plus MECHANICAL enforcement (corrective re-ask → hard truncation).

from planner import goal_step_ceiling, _STEP_CEILING_DIRECTIVE


class TestGoalStepCeilingDetector:
    def test_range_returns_upper_bound(self):
        # The BACKLOG specimen phrasing.
        assert goal_step_ceiling("answer the question, 2-3 steps maximum") == 3

    def test_range_with_to(self):
        assert goal_step_ceiling("2 to 3 steps max") == 3

    def test_steps_max(self):
        assert goal_step_ceiling("summarize the config, 3 steps max") == 3

    def test_maximum_of(self):
        assert goal_step_ceiling("use a maximum of 3 steps") == 3

    def test_at_most(self):
        assert goal_step_ceiling("plan this in at most 3 steps") == 3

    def test_no_more_than(self):
        assert goal_step_ceiling("no more than 3 steps") == 3

    def test_or_fewer(self):
        assert goal_step_ceiling("do it in 3 steps or fewer") == 3

    def test_or_less(self):
        assert goal_step_ceiling("do it in 3 steps or less") == 3

    def test_limit_to(self):
        assert goal_step_ceiling("limit to 3 steps") == 3

    def test_limit_the_plan_to(self):
        assert goal_step_ceiling("limit the plan to 3 steps") == 3

    def test_word_number(self):
        # Word-numbers one–ten are in scope: a shared alternation keeps the
        # regex readable and "three steps max" is as binding as "3 steps max".
        assert goal_step_ceiling("three steps max") == 3

    def test_case_insensitive(self):
        assert goal_step_ceiling("AT MOST 3 STEPS") == 3

    def test_single_step_with_qualifier(self):
        assert goal_step_ceiling("do it in a single step") == 1
        assert goal_step_ceiling("just one step") == 1
        assert goal_step_ceiling("one step only") == 1
        assert goal_step_ceiling("one step maximum") == 1

    # -- negatives: conservative, stays out of ambiguous phrasing --

    def test_comma_before_maximum(self):
        # review F4: comma between the count and the qualifier
        assert goal_step_ceiling("summarize the config files - 3 steps, maximum") == 3

    def test_known_gap_bound_qualifier_in_content_reference_fires(self):
        """KNOWN GAP (review F3, accepted): a bound qualifier inside a
        content reference still fires — regex cannot tell a content
        reference from a plan bound. Pinned so any future change here is a
        deliberate decision, not an accident. Impact is bounded: the plan
        clamps to the stated N; it degrades, never crashes."""
        assert goal_step_ceiling(
            "document the deploy pipeline; it is limited to 3 steps by design") == 3
        assert goal_step_ceiling(
            "investigate the failures - at most 3 steps of the pipeline "
            "are affected") == 3

    def test_bare_count_does_not_fire(self):
        assert goal_step_ceiling("explain the deploy process in 3 steps") is None

    def test_plan_content_reference_does_not_fire(self):
        assert goal_step_ceiling(
            "document the 3 steps of the deploy process") is None

    def test_step_ordinal_does_not_fire(self):
        assert goal_step_ceiling("step 2 of the migration") is None

    def test_non_step_number_does_not_fire(self):
        assert goal_step_ceiling("fix those 2 goal related things") is None

    def test_bare_single_step_does_not_fire(self):
        assert goal_step_ceiling("one step of the process") is None
        assert goal_step_ceiling(
            "this is a single step in a longer journey") is None

    def test_empty_and_plain_goals(self):
        assert goal_step_ceiling("") is None
        assert goal_step_ceiling("review the auth module") is None


class _SeqAdapter:
    """Fake adapter: returns each payload in sequence (last one repeats),
    records every message of every call as (role, content) tuples."""

    def __init__(self, *payloads):
        self.payloads = payloads
        self.calls = []

    def complete(self, messages, **kw):
        self.calls.append([(getattr(m, "role", ""), getattr(m, "content", ""))
                           for m in messages])
        i = min(len(self.calls) - 1, len(self.payloads) - 1)
        from types import SimpleNamespace
        return SimpleNamespace(content=self.payloads[i],
                               input_tokens=5, output_tokens=20)

    def systems(self):
        return [c for call in self.calls for (r, c) in call if r == "system"]

    def users(self):
        return [c for call in self.calls for (r, c) in call if r == "user"]


def _steps_json(n):
    import json
    return json.dumps([f"step {i}" for i in range(1, n + 1)])


class TestStepCeilingDecompose:
    """The ceiling is binding: clamped prompt, loud directive, and mechanical
    enforcement (one corrective re-ask, then hard truncation)."""

    NARROW_CEILING_GOAL = "do the thing, 3 steps max"  # narrow scope, ceiling 3

    def test_corrective_reask_then_truncate(self, monkeypatch, caplog):
        import logging
        import captains_log
        events = []
        monkeypatch.setattr(captains_log, "log_event",
                            lambda *a, **k: events.append((a, k)))
        adapter = _SeqAdapter(_steps_json(7))  # every call returns 7 steps

        with caplog.at_level(logging.WARNING, logger="maro.planner"):
            result = decompose(self.NARROW_CEILING_GOAL, adapter, max_steps=8)

        # single-shot + ONE corrective re-ask, then hard truncation
        assert len(adapter.calls) == 2
        retry_user = adapter.users()[1]
        assert "You returned 7 steps" in retry_user
        assert "at most 3 steps" in retry_user
        assert result == ["step 1", "step 2", "step 3"]
        assert any("hard-truncated" in m for m in caplog.messages)
        assert any(a[0] == "STEP_CEILING_ENFORCED" for a, _ in events)

    def test_corrective_reask_success_no_truncation(self):
        adapter = _SeqAdapter(_steps_json(7), '["merged 1", "merged 2", "merged 3"]')
        result = decompose(self.NARROW_CEILING_GOAL, adapter, max_steps=8)
        assert len(adapter.calls) == 2
        assert result == ["merged 1", "merged 2", "merged 3"]

    def test_compliant_plan_untouched_no_corrective_call(self):
        adapter = _SeqAdapter(_steps_json(2))
        result = decompose(self.NARROW_CEILING_GOAL, adapter, max_steps=8)
        # Never padded up to the ceiling, no corrective call issued.
        assert result == ["step 1", "step 2"]
        assert len(adapter.calls) == 1

    def test_prompt_clamped_directive_present_hint_suppressed(self):
        adapter = _SeqAdapter(_steps_json(2))
        decompose(self.NARROW_CEILING_GOAL, adapter, max_steps=8)
        user = adapter.users()[0]
        system = adapter.systems()[0]
        assert "Decompose into 3 or fewer concrete steps." in user
        assert "STEP-COUNT CEILING" in system
        assert "AT MOST 3" in system
        # Scope hint must not contradict the ceiling — suppressed outright.
        assert "SCOPE HINT" not in system

    def test_compose_lane_clamped_and_enforced(self):
        # "implement" → medium scope → multi-plan + compose lane.
        goal = "implement rate limit retry logic in llm.py, at most 3 steps"
        adapter = _SeqAdapter(_steps_json(7))
        result = decompose(goal, adapter, max_steps=8)
        # 3 candidates + compose + ONE corrective re-ask
        assert len(adapter.calls) == 5
        assert result == ["step 1", "step 2", "step 3"]
        compose_user = adapter.users()[3]
        assert "Compose the best plan (3 steps max)." in compose_user
        compose_system = adapter.systems()[3]
        assert "STEP-COUNT CEILING" in compose_system

    def test_directive_reaches_staged_pass_lane(self):
        adapter = _SeqAdapter('["Pass 1/2 — core", "Pass 2/2 — synth [after:1]"]')
        decompose("adversarial review of the entire codebase, at most 3 steps",
                  adapter, max_steps=8)
        assert any("STEP-COUNT CEILING" in s for s in adapter.systems())

    # -- per-lane enforcement pins (review F1: mutants nulling the ceiling
    # on these four lanes survived the original suite) -------------------

    def test_staged_lane_enforced(self, monkeypatch):
        import captains_log
        events = []
        monkeypatch.setattr(captains_log, "log_event",
                            lambda *a, **k: events.append((a, k)))
        # wide scope → staged lane; every call returns 4 passes
        adapter = _SeqAdapter(_steps_json(4))
        result = decompose(
            "adversarial review of the entire codebase, at most 3 steps",
            adapter, max_steps=8)
        assert len(adapter.calls) == 2  # staged + ONE corrective re-ask
        assert result == ["step 1", "step 2", "step 3"]
        assert any(a[0] == "STEP_CEILING_ENFORCED" for a, _ in events)

    def test_compose_fallback_lane_enforced(self):
        goal = "implement rate limit retry logic in llm.py, at most 3 steps"
        adapter = _SeqAdapter(_steps_json(7), _steps_json(7), _steps_json(7),
                              "not json", "still not json")
        result = decompose(goal, adapter, max_steps=8)
        # 3 candidates + failed compose + ONE corrective re-ask → truncation
        assert len(adapter.calls) == 5
        assert result == ["step 1", "step 2", "step 3"]

    def test_single_candidate_lane_enforced(self):
        goal = "implement rate limit retry logic in llm.py, at most 3 steps"
        adapter = _SeqAdapter("nope", "nope", _steps_json(7), "nope")
        result = decompose(goal, adapter, max_steps=8)
        # 3 candidate attempts (1 valid) + ONE corrective re-ask → truncation
        assert len(adapter.calls) == 4
        assert result == ["step 1", "step 2", "step 3"]

    def test_single_plan_lane_enforced(self):
        goal = "implement rate limit retry logic in llm.py, at most 3 steps"
        adapter = _SeqAdapter("nope", "nope", "nope", _steps_json(7), "nope")
        result = decompose(goal, adapter, max_steps=8)
        # 3 failed candidates + single-plan fallback + ONE corrective re-ask
        assert len(adapter.calls) == 5
        assert result == ["step 1", "step 2", "step 3"]

    def test_directive_reaches_draw_cuts_under_cuts_first(self, monkeypatch):
        # Same seam as the #23c regression test above: the cuts probe path
        # returns early, so the directive must already be in extras.
        import config
        import planner
        monkeypatch.setattr(config, "get", lambda key, default=None:
                            True if key == "planner.cuts_first" else default)
        seen_extras = []

        def _spy_draw_cuts(goal, adapter, context_extras=""):
            seen_extras.append(context_extras)
            return None

        monkeypatch.setattr(planner, "draw_cuts", _spy_draw_cuts)
        adapter = _SeqAdapter(_steps_json(2))
        decompose(self.NARROW_CEILING_GOAL, adapter, max_steps=8)
        assert seen_extras, "draw_cuts was never called — cuts gate broken?"
        assert any("STEP-COUNT CEILING" in x for x in seen_extras)

    def test_verification_step_respects_ceiling(self):
        from planner import maybe_add_verification_step
        steps = ["a", "b", "c"]
        # Research goal WITH a ceiling already met → no injection.
        bounded = maybe_add_verification_step(
            steps, "research X thoroughly, at most 3 steps", max_steps=8)
        assert bounded == steps
        # Same research goal WITHOUT a ceiling → injection still happens.
        unbounded = maybe_add_verification_step(
            steps, "research X thoroughly", max_steps=8)
        assert len(unbounded) == 4

    # -- byte-identity regression pin -------------------------------------

    def test_no_ceiling_prompts_byte_identical(self):
        """When NO ceiling is detected, every prompt decompose sends must be
        byte-identical to the pre-change construction: exact user_msg bytes,
        exact scope-hint bytes at the end of system, no directive fragment
        anywhere, and no extra adapter calls."""
        # Narrow lane
        goal_n = "check the config timeout value"
        adapter_n = _SeqAdapter(_steps_json(2))
        decompose(goal_n, adapter_n, max_steps=4)
        assert len(adapter_n.calls) == 1
        assert adapter_n.users()[0] == (
            f"Goal: {goal_n}\n\nDecompose into 4 or fewer concrete steps.")
        assert adapter_n.systems()[0].endswith(
            "SCOPE HINT: This goal is narrow — expect 1-4 steps. "
            "Do not over-decompose.")
        # Medium lane (multi-plan + compose)
        goal_m = "implement rate limit retry logic in llm.py"
        adapter_m = _SeqAdapter(_steps_json(3))
        decompose(goal_m, adapter_m, max_steps=8)
        assert len(adapter_m.calls) == 4  # 3 candidates + compose, nothing more
        expected_user = f"Goal: {goal_m}\n\nDecompose into 8 or fewer concrete steps."
        assert adapter_m.users()[0] == expected_user
        assert adapter_m.users()[1] == expected_user
        assert adapter_m.users()[2] == expected_user
        assert adapter_m.users()[3].endswith(
            "Compose the best plan (8 steps max). JSON array only.")
        for s in adapter_m.systems()[:3]:
            assert s.endswith(
                "SCOPE HINT: This goal is medium complexity — expect 5-10 steps.")
        for adapter in (adapter_n, adapter_m):
            for call in adapter.calls:
                for _, content in call:
                    assert "STEP-COUNT CEILING" not in content
