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
