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
