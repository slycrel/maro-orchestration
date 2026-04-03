"""Tests for Phase 41: tool_registry.py — declarative tool registry."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tool_registry import (
    ROLE_DIRECTOR,
    ROLE_INSPECTOR,
    ROLE_SHORT,
    ROLE_VERIFIER,
    ROLE_WORKER,
    PermissionContext,
    ToolDefinition,
    ToolRegistry,
    director_context,
    inspector_context,
    registry,
    short_context,
    worker_context,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tool(
    name: str,
    roles: list | None = None,
    enabled: bool = True,
    defer: bool = False,
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=f"Does {name}",
        input_schema={"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]},
        roles_allowed=roles if roles is not None else [],
        is_enabled=lambda enabled=enabled: enabled,
        should_defer=defer,
    )


# ---------------------------------------------------------------------------
# ToolDefinition
# ---------------------------------------------------------------------------

class TestToolDefinition:
    def test_to_schema(self):
        t = _make_tool("my_tool", roles=[ROLE_WORKER])
        s = t.to_schema()
        assert s["name"] == "my_tool"
        assert s["description"] == "Does my_tool"
        assert "properties" in s["parameters"]

    def test_to_stub(self):
        t = _make_tool("deferred_tool", defer=True)
        s = t.to_stub()
        assert s["name"] == "deferred_tool"
        assert "[deferred]" in s["description"]
        assert s["parameters"]["properties"] == {}

    def test_is_enabled_callable(self):
        t = _make_tool("off_tool", enabled=False)
        assert not t.is_enabled()

    def test_roles_allowed_default_empty(self):
        t = ToolDefinition(
            name="x", description="y",
            input_schema={"type": "object", "properties": {}, "required": []},
        )
        assert t.roles_allowed == []

    def test_should_defer_default_false(self):
        t = _make_tool("x")
        assert not t.should_defer


# ---------------------------------------------------------------------------
# PermissionContext
# ---------------------------------------------------------------------------

class TestPermissionContext:
    def test_allows_no_patterns(self):
        ctx = PermissionContext(role=ROLE_WORKER)
        assert ctx.allows("any_tool")

    def test_allows_deny_exact(self):
        ctx = PermissionContext(role=ROLE_WORKER, deny_patterns=["schedule_run"])
        assert not ctx.allows("schedule_run")
        assert ctx.allows("complete_step")

    def test_allows_deny_glob(self):
        ctx = PermissionContext(role=ROLE_WORKER, deny_patterns=["create_*"])
        assert not ctx.allows("create_team_worker")
        assert ctx.allows("complete_step")
        assert ctx.allows("flag_stuck")

    def test_allows_multiple_deny_patterns(self):
        ctx = PermissionContext(role=ROLE_WORKER, deny_patterns=["schedule_run", "create_*"])
        assert not ctx.allows("schedule_run")
        assert not ctx.allows("create_team_worker")
        assert ctx.allows("complete_step")

    def test_default_role_is_worker(self):
        ctx = PermissionContext()
        assert ctx.role == ROLE_WORKER

    def test_deny_glob_wildcard_all(self):
        ctx = PermissionContext(role=ROLE_WORKER, deny_patterns=["*"])
        assert not ctx.allows("anything")


# ---------------------------------------------------------------------------
# ToolRegistry
# ---------------------------------------------------------------------------

class TestToolRegistry:
    def _reg(self, *tools: ToolDefinition) -> ToolRegistry:
        reg = ToolRegistry()
        for t in tools:
            reg.register(t)
        return reg

    def test_register_and_names(self):
        reg = self._reg(_make_tool("a"), _make_tool("b"))
        assert set(reg.names()) == {"a", "b"}

    def test_get(self):
        reg = self._reg(_make_tool("my_tool"))
        t = reg.get("my_tool")
        assert t is not None
        assert t.name == "my_tool"

    def test_get_missing(self):
        reg = self._reg()
        assert reg.get("nonexistent") is None

    def test_register_overwrites(self):
        reg = ToolRegistry()
        reg.register(_make_tool("x", roles=[ROLE_WORKER]))
        reg.register(_make_tool("x", roles=[ROLE_INSPECTOR]))
        assert reg.get("x").roles_allowed == [ROLE_INSPECTOR]

    # --- role filtering ---

    def test_worker_sees_worker_tools(self):
        reg = self._reg(
            _make_tool("worker_tool", roles=[ROLE_WORKER]),
            _make_tool("inspector_tool", roles=[ROLE_INSPECTOR]),
        )
        ctx = PermissionContext(role=ROLE_WORKER)
        tools = reg.get_tools(ctx)
        names = [t.name for t in tools]
        assert "worker_tool" in names
        assert "inspector_tool" not in names

    def test_inspector_sees_inspector_tools(self):
        reg = self._reg(
            _make_tool("flag_stuck", roles=[ROLE_WORKER, ROLE_INSPECTOR]),
            _make_tool("complete_step", roles=[ROLE_WORKER]),
        )
        ctx = PermissionContext(role=ROLE_INSPECTOR)
        names = [t.name for t in reg.get_tools(ctx)]
        assert "flag_stuck" in names
        assert "complete_step" not in names

    def test_empty_roles_allowed_visible_to_all(self):
        reg = self._reg(_make_tool("universal", roles=[]))
        for role in [ROLE_WORKER, ROLE_INSPECTOR, ROLE_SHORT, ROLE_DIRECTOR, ROLE_VERIFIER]:
            ctx = PermissionContext(role=role)
            names = [t.name for t in reg.get_tools(ctx)]
            assert "universal" in names, f"universal not visible to {role}"

    def test_director_sees_no_execute_tools(self):
        reg = self._reg(
            _make_tool("complete_step", roles=[ROLE_WORKER, ROLE_SHORT]),
            _make_tool("flag_stuck", roles=[ROLE_WORKER, ROLE_INSPECTOR, ROLE_SHORT]),
        )
        ctx = PermissionContext(role=ROLE_DIRECTOR)
        assert reg.get_tools(ctx) == []

    # --- deny patterns ---

    def test_deny_pattern_removes_tool(self):
        reg = self._reg(_make_tool("schedule_run", roles=[ROLE_WORKER]))
        ctx = PermissionContext(role=ROLE_WORKER, deny_patterns=["schedule_run"])
        assert reg.get_tools(ctx) == []

    def test_deny_glob_removes_matching_tools(self):
        reg = self._reg(
            _make_tool("create_team_worker", roles=[ROLE_WORKER]),
            _make_tool("complete_step", roles=[ROLE_WORKER]),
        )
        ctx = PermissionContext(role=ROLE_WORKER, deny_patterns=["create_*"])
        names = [t.name for t in reg.get_tools(ctx)]
        assert "create_team_worker" not in names
        assert "complete_step" in names

    # --- is_enabled gate ---

    def test_disabled_tool_not_returned(self):
        reg = self._reg(_make_tool("off_tool", roles=[ROLE_WORKER], enabled=False))
        ctx = PermissionContext(role=ROLE_WORKER)
        assert reg.get_tools(ctx) == []

    def test_enabled_tool_returned(self):
        reg = self._reg(_make_tool("on_tool", roles=[ROLE_WORKER], enabled=True))
        ctx = PermissionContext(role=ROLE_WORKER)
        assert len(reg.get_tools(ctx)) == 1

    # --- deterministic ordering ---

    def test_tools_sorted_alphabetically(self):
        reg = self._reg(
            _make_tool("zzz", roles=[ROLE_WORKER]),
            _make_tool("aaa", roles=[ROLE_WORKER]),
            _make_tool("mmm", roles=[ROLE_WORKER]),
        )
        ctx = PermissionContext(role=ROLE_WORKER)
        names = [t.name for t in reg.get_tools(ctx)]
        assert names == sorted(names)

    # --- get_tool_schemas ---

    def test_get_tool_schemas_returns_dicts(self):
        reg = self._reg(_make_tool("my_tool", roles=[ROLE_WORKER]))
        ctx = PermissionContext(role=ROLE_WORKER)
        schemas = reg.get_tool_schemas(ctx)
        assert len(schemas) == 1
        assert schemas[0]["name"] == "my_tool"
        assert "parameters" in schemas[0]

    def test_deferred_tool_returns_stub(self):
        reg = self._reg(_make_tool("big_tool", roles=[ROLE_WORKER], defer=True))
        ctx = PermissionContext(role=ROLE_WORKER)
        schemas = reg.get_tool_schemas(ctx)
        assert len(schemas) == 1
        assert "[deferred]" in schemas[0]["description"]
        assert schemas[0]["parameters"]["properties"] == {}

    def test_non_deferred_tool_returns_full_schema(self):
        reg = self._reg(_make_tool("normal_tool", roles=[ROLE_WORKER], defer=False))
        ctx = PermissionContext(role=ROLE_WORKER)
        schemas = reg.get_tool_schemas(ctx)
        assert len(schemas) == 1
        assert "[deferred]" not in schemas[0]["description"]

    def test_default_ctx_is_worker(self):
        reg = self._reg(_make_tool("worker_only", roles=[ROLE_WORKER]))
        schemas = reg.get_tool_schemas()  # no ctx — defaults to worker
        assert len(schemas) == 1


# ---------------------------------------------------------------------------
# Default registry (loaded from step_exec.EXECUTE_TOOLS)
# ---------------------------------------------------------------------------

class TestDefaultRegistry:
    def test_registry_not_empty(self):
        assert len(registry.names()) > 0

    def test_complete_step_registered(self):
        assert registry.get("complete_step") is not None

    def test_flag_stuck_registered(self):
        assert registry.get("flag_stuck") is not None

    def test_schedule_run_registered(self):
        assert registry.get("schedule_run") is not None

    def test_create_team_worker_registered(self):
        assert registry.get("create_team_worker") is not None

    def test_worker_sees_all_tools(self):
        schemas = registry.get_tool_schemas(worker_context())
        names = {s["name"] for s in schemas}
        assert "complete_step" in names
        assert "flag_stuck" in names
        assert "schedule_run" in names
        assert "create_team_worker" in names

    def test_short_excludes_schedule_run_and_team(self):
        schemas = registry.get_tool_schemas(short_context())
        names = {s["name"] for s in schemas}
        assert "complete_step" in names
        assert "flag_stuck" in names
        assert "schedule_run" not in names
        assert "create_team_worker" not in names

    def test_inspector_only_flag_stuck(self):
        schemas = registry.get_tool_schemas(inspector_context())
        names = {s["name"] for s in schemas}
        assert names == {"flag_stuck"}

    def test_director_sees_no_tools(self):
        schemas = registry.get_tool_schemas(director_context())
        assert schemas == []

    def test_deny_pattern_overrides_role(self):
        ctx = worker_context(deny_patterns=["schedule_run"])
        schemas = registry.get_tool_schemas(ctx)
        names = {s["name"] for s in schemas}
        assert "schedule_run" not in names
        assert "complete_step" in names

    def test_schemas_are_sorted(self):
        schemas = registry.get_tool_schemas(worker_context())
        names = [s["name"] for s in schemas]
        assert names == sorted(names)

    def test_schema_has_required_keys(self):
        schemas = registry.get_tool_schemas(worker_context())
        for s in schemas:
            assert "name" in s
            assert "description" in s
            assert "parameters" in s


# ---------------------------------------------------------------------------
# Convenience context factories
# ---------------------------------------------------------------------------

class TestContextFactories:
    def test_worker_context(self):
        ctx = worker_context()
        assert ctx.role == ROLE_WORKER
        assert ctx.deny_patterns == []

    def test_short_context(self):
        ctx = short_context()
        assert ctx.role == ROLE_SHORT

    def test_inspector_context(self):
        ctx = inspector_context()
        assert ctx.role == ROLE_INSPECTOR

    def test_director_context(self):
        ctx = director_context()
        assert ctx.role == ROLE_DIRECTOR

    def test_deny_patterns_passed_through(self):
        ctx = worker_context(deny_patterns=["schedule_run"])
        assert "schedule_run" in ctx.deny_patterns
