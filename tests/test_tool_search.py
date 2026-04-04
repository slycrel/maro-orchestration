"""Tests for Phase 41 step 6: tool_search.py — deferred tool resolution."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tool_registry import (
    ROLE_WORKER,
    PermissionContext,
    ToolDefinition,
    ToolRegistry,
)
from tool_search import (
    TOOL_SEARCH_SCHEMA,
    format_tool_search_result,
    inject_tool_search_if_needed,
    resolve_deferred_tools,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_tool(name: str, description: str = "", defer: bool = False, roles=None) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=description or f"Does {name}",
        input_schema={
            "type": "object",
            "properties": {"x": {"type": "string", "description": "Input x"}},
            "required": ["x"],
        },
        roles_allowed=roles if roles is not None else [ROLE_WORKER],
        should_defer=defer,
    )


def _make_registry(*tools: ToolDefinition) -> ToolRegistry:
    reg = ToolRegistry()
    for t in tools:
        reg.register(t)
    return reg


# ---------------------------------------------------------------------------
# TOOL_SEARCH_SCHEMA
# ---------------------------------------------------------------------------

class TestToolSearchSchema:
    def test_name(self):
        assert TOOL_SEARCH_SCHEMA["name"] == "tool_search"

    def test_has_query_parameter(self):
        props = TOOL_SEARCH_SCHEMA["parameters"]["properties"]
        assert "query" in props
        assert props["query"]["type"] == "string"

    def test_query_is_required(self):
        assert "query" in TOOL_SEARCH_SCHEMA["parameters"]["required"]

    def test_description_mentions_deferred(self):
        assert "deferred" in TOOL_SEARCH_SCHEMA["description"].lower()


# ---------------------------------------------------------------------------
# resolve_deferred_tools
# ---------------------------------------------------------------------------

class TestResolveDeferredTools:
    def _ctx(self):
        return PermissionContext(role=ROLE_WORKER)

    def test_exact_name_match(self):
        reg = _make_registry(
            _make_tool("schedule_run", defer=True),
            _make_tool("complete_step", defer=False),
        )
        results = resolve_deferred_tools("schedule_run", self._ctx(), registry=reg)
        assert len(results) == 1
        assert results[0]["name"] == "schedule_run"

    def test_partial_name_match(self):
        reg = _make_registry(_make_tool("schedule_run", defer=True))
        results = resolve_deferred_tools("schedule", self._ctx(), registry=reg)
        assert len(results) == 1

    def test_description_match(self):
        reg = _make_registry(_make_tool("my_tool", description="Schedule a job for later", defer=True))
        results = resolve_deferred_tools("schedule", self._ctx(), registry=reg)
        assert len(results) == 1

    def test_non_deferred_not_returned(self):
        reg = _make_registry(
            _make_tool("schedule_run", defer=False),  # not deferred
        )
        results = resolve_deferred_tools("schedule", self._ctx(), registry=reg)
        assert results == []

    def test_no_match_returns_empty(self):
        reg = _make_registry(_make_tool("schedule_run", defer=True))
        results = resolve_deferred_tools("nonexistent_xyz", self._ctx(), registry=reg)
        assert results == []

    def test_returns_full_schema(self):
        reg = _make_registry(_make_tool("schedule_run", defer=True))
        results = resolve_deferred_tools("schedule_run", self._ctx(), registry=reg)
        assert len(results) == 1
        schema = results[0]
        assert "name" in schema
        assert "description" in schema
        assert "parameters" in schema
        # Full schema has parameters — NOT the empty stub
        props = schema["parameters"].get("properties", {})
        assert len(props) > 0

    def test_deferred_description_not_stubbed(self):
        reg = _make_registry(_make_tool("schedule_run", description="Schedule a future run", defer=True))
        results = resolve_deferred_tools("schedule_run", self._ctx(), registry=reg)
        # to_schema() returns real description (not [deferred] prefix)
        assert "[deferred]" not in results[0]["description"]

    def test_role_filtering_respected(self):
        from tool_registry import ROLE_INSPECTOR
        reg = _make_registry(
            _make_tool("worker_only_tool", defer=True, roles=[ROLE_WORKER]),
        )
        inspector_ctx = PermissionContext(role=ROLE_INSPECTOR)
        results = resolve_deferred_tools("worker_only_tool", inspector_ctx, registry=reg)
        assert results == []  # inspector can't see worker-only tools

    def test_multiple_matches_returned(self):
        reg = _make_registry(
            _make_tool("create_team_worker", defer=True),
            _make_tool("create_project", defer=True),
            _make_tool("complete_step", defer=False),
        )
        results = resolve_deferred_tools("create", self._ctx(), registry=reg)
        names = {r["name"] for r in results}
        assert "create_team_worker" in names
        assert "create_project" in names
        assert "complete_step" not in names

    def test_exact_match_ranked_first(self):
        reg = _make_registry(
            _make_tool("schedule_run", defer=True),
            _make_tool("schedule_run_daily", defer=True),
        )
        results = resolve_deferred_tools("schedule_run", self._ctx(), registry=reg)
        assert results[0]["name"] == "schedule_run"

    def test_empty_registry(self):
        reg = ToolRegistry()
        results = resolve_deferred_tools("anything", self._ctx(), registry=reg)
        assert results == []

    def test_case_insensitive_matching(self):
        reg = _make_registry(_make_tool("Schedule_Run", defer=True))
        results = resolve_deferred_tools("schedule", self._ctx(), registry=reg)
        assert len(results) == 1


# ---------------------------------------------------------------------------
# format_tool_search_result
# ---------------------------------------------------------------------------

class TestFormatToolSearchResult:
    def test_no_results_message(self):
        result = format_tool_search_result([])
        assert "No matching" in result

    def test_single_result_formatted(self):
        schemas = [
            {
                "name": "schedule_run",
                "description": "Schedule a future run",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "goal": {"type": "string", "description": "Goal to schedule"},
                        "when": {"type": "string", "description": "When to run"},
                    },
                    "required": ["goal"],
                },
            }
        ]
        result = format_tool_search_result(schemas)
        assert "schedule_run" in result
        assert "Schedule a future run" in result
        assert "goal" in result
        assert "(required)" in result
        assert "when" in result

    def test_includes_count(self):
        schemas = [
            {"name": "a", "description": "A", "parameters": {"type": "object", "properties": {}, "required": []}},
            {"name": "b", "description": "B", "parameters": {"type": "object", "properties": {}, "required": []}},
        ]
        result = format_tool_search_result(schemas)
        assert "2" in result

    def test_ends_with_call_instruction(self):
        schemas = [{"name": "x", "description": "y", "parameters": {"type": "object", "properties": {}, "required": []}}]
        result = format_tool_search_result(schemas)
        assert "call" in result.lower()


# ---------------------------------------------------------------------------
# inject_tool_search_if_needed
# ---------------------------------------------------------------------------

class TestInjectToolSearchIfNeeded:
    def _stub_schema(self, name: str) -> dict:
        """Mimics what ToolDefinition.to_stub() returns."""
        return {
            "name": name,
            "description": f"[deferred] Does {name}",
            "parameters": {"type": "object", "properties": {}, "required": []},
        }

    def _full_schema(self, name: str) -> dict:
        return {
            "name": name,
            "description": f"Does {name}",
            "parameters": {
                "type": "object",
                "properties": {"x": {"type": "string"}},
                "required": ["x"],
            },
        }

    def test_injects_when_deferred_present(self):
        schemas = [self._stub_schema("schedule_run"), self._full_schema("complete_step")]
        result = inject_tool_search_if_needed(schemas)
        names = [s["name"] for s in result]
        assert "tool_search" in names

    def test_no_injection_when_no_deferred(self):
        schemas = [self._full_schema("complete_step"), self._full_schema("flag_stuck")]
        result = inject_tool_search_if_needed(schemas)
        names = [s["name"] for s in result]
        assert "tool_search" not in names

    def test_no_double_injection(self):
        schemas = [self._stub_schema("schedule_run")]
        result1 = inject_tool_search_if_needed(schemas)
        result2 = inject_tool_search_if_needed(result1)
        count = sum(1 for s in result2 if s["name"] == "tool_search")
        assert count == 1

    def test_original_schemas_preserved(self):
        schemas = [self._stub_schema("schedule_run"), self._full_schema("complete_step")]
        result = inject_tool_search_if_needed(schemas)
        names = {s["name"] for s in result}
        assert "schedule_run" in names
        assert "complete_step" in names

    def test_empty_list_no_injection(self):
        result = inject_tool_search_if_needed([])
        assert result == []

    def test_deferred_detection_requires_deferred_marker(self):
        # A schema with empty properties but NO [deferred] in description
        # should NOT trigger injection (it might be a tool with no params)
        schemas = [
            {
                "name": "simple_tool",
                "description": "A simple tool with no params",
                "parameters": {"type": "object", "properties": {}, "required": []},
            }
        ]
        result = inject_tool_search_if_needed(schemas)
        names = [s["name"] for s in result]
        assert "tool_search" not in names
