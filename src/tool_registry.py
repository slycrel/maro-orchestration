"""tool_registry.py — Phase 41: Declarative tool registry + role-gated visibility.

Key insight from Claude Code architecture: gate tools at prompt-composition time,
not at call time. A tool that never appears in the system prompt cannot be
hallucinated by the model.

Current poe-orchestration uses imperative lists (EXECUTE_TOOLS_WORKER/SHORT/INSPECTOR)
selected at execution time. This module replaces that with:

  1. ToolDefinition  — declarative per-tool object (schema, roles, feature gate)
  2. PermissionContext — role + deny patterns, threaded through the call stack
  3. ToolRegistry     — canonical store; get_tools(ctx) filters at composition time

Backward compatibility: the existing EXECUTE_TOOLS list in step_exec.py is the
source of truth for schema content; ToolRegistry wraps it. Callers can continue
using the old lists or migrate to get_tool_schemas(ctx).

Usage:
    from tool_registry import registry, PermissionContext, ROLE_WORKER

    ctx = PermissionContext(role=ROLE_WORKER)
    schemas = registry.get_tool_schemas(ctx)   # filtered List[dict] for LLM API
"""

from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger("poe.registry")

# ---------------------------------------------------------------------------
# Role constants
# ---------------------------------------------------------------------------

ROLE_WORKER = "worker"       # Full tool set — research / build / ops
ROLE_SHORT = "short"         # complete_step + flag_stuck only (factory_thin, quick steps)
ROLE_INSPECTOR = "inspector" # flag_stuck only (quality inspectors produce critiques)
ROLE_DIRECTOR = "director"   # No execute tools — director plans, workers execute
ROLE_VERIFIER = "verifier"   # flag_stuck only — read + analysis, no scheduling

_ALL_ROLES = {ROLE_WORKER, ROLE_SHORT, ROLE_INSPECTOR, ROLE_DIRECTOR, ROLE_VERIFIER}


# ---------------------------------------------------------------------------
# ToolDefinition
# ---------------------------------------------------------------------------

@dataclass
class ToolDefinition:
    """Declarative description of a single tool.

    Fields:
        name:          Tool name — must match the name used in LLM tool_use blocks.
        description:   Human-readable description (shown in system prompt).
        input_schema:  JSON Schema dict for the tool's parameters.
        roles_allowed: Roles that can see this tool. Empty list = all roles.
        is_enabled:    Callable returning False to feature-flag the tool off.
        should_defer:  If True, only name+description appear in prompt initially;
                       full schema is loaded on first invocation (deferred loading).
    """
    name: str
    description: str
    input_schema: Dict[str, Any]
    roles_allowed: List[str] = field(default_factory=list)
    is_enabled: Callable[[], bool] = field(default_factory=lambda: (lambda: True))
    should_defer: bool = False

    def to_schema(self) -> dict:
        """Return the dict format expected by the LLM API (Anthropic tool_use format)."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.input_schema,
        }

    def to_stub(self) -> dict:
        """Return a minimal stub with only name + description (for deferred tools)."""
        return {
            "name": self.name,
            "description": f"[deferred] {self.description}",
            "parameters": {"type": "object", "properties": {}, "required": []},
        }


# ---------------------------------------------------------------------------
# PermissionContext
# ---------------------------------------------------------------------------

@dataclass
class PermissionContext:
    """Role + deny patterns for a single agent invocation.

    Gates which tools appear in the system prompt at context-build time.
    Deny patterns use glob syntax: e.g. "schedule_run" or "create_*".

    Args:
        role:          One of ROLE_WORKER / ROLE_SHORT / ROLE_INSPECTOR /
                       ROLE_DIRECTOR / ROLE_VERIFIER.
        deny_patterns: Tool names (glob OK) to explicitly block for this context,
                       regardless of role. e.g. ["schedule_run"] during a dry run.
    """
    role: str = ROLE_WORKER
    deny_patterns: List[str] = field(default_factory=list)

    def allows(self, tool_name: str) -> bool:
        """Return False if tool_name matches any deny pattern."""
        for pattern in self.deny_patterns:
            if fnmatch.fnmatch(tool_name, pattern):
                return False
        return True


# ---------------------------------------------------------------------------
# ToolRegistry
# ---------------------------------------------------------------------------

class ToolRegistry:
    """Canonical store for all registered tools.

    Registration order is preserved internally but get_tool_schemas() returns
    tools in deterministic alphabetical order (preserves LLM prefix cache).
    """

    def __init__(self) -> None:
        self._tools: Dict[str, ToolDefinition] = {}

    def register(self, tool_def: ToolDefinition) -> None:
        """Register a tool. Re-registration overwrites (useful for testing)."""
        self._tools[tool_def.name] = tool_def

    def get_tools(self, ctx: Optional[PermissionContext] = None) -> List[ToolDefinition]:
        """Return tools visible to ctx, filtered and sorted.

        Filter pipeline (mirrors Claude Code's assembly pipeline):
        1. Load all registered tools
        2. Strip tools not allowed for ctx.role (roles_allowed check)
        3. Strip tools matching ctx.deny_patterns
        4. Strip tools where is_enabled() returns False
        5. Sort deterministically by name (prefix cache)
        """
        if ctx is None:
            ctx = PermissionContext()

        result = []
        for tool in self._tools.values():
            # Role check — empty roles_allowed means available to all roles
            if tool.roles_allowed and ctx.role not in tool.roles_allowed:
                continue
            # Deny pattern check
            if not ctx.allows(tool.name):
                log.debug("registry: tool %r denied by pattern for role %r", tool.name, ctx.role)
                continue
            # Feature flag check
            if not tool.is_enabled():
                log.debug("registry: tool %r is_enabled=False, skipping", tool.name)
                continue
            result.append(tool)

        return sorted(result, key=lambda t: t.name)

    def get_tool_schemas(self, ctx: Optional[PermissionContext] = None) -> List[dict]:
        """Return filtered tool schemas as dicts for the LLM API.

        Deferred tools (should_defer=True) return a stub entry — full schema
        loads when the tool is actually invoked.
        """
        schemas = []
        for tool in self.get_tools(ctx):
            if tool.should_defer:
                schemas.append(tool.to_stub())
            else:
                schemas.append(tool.to_schema())
        return schemas

    def get(self, name: str) -> Optional[ToolDefinition]:
        """Lookup a single tool by name."""
        return self._tools.get(name)

    def names(self) -> List[str]:
        """All registered tool names (unsorted)."""
        return list(self._tools.keys())


# ---------------------------------------------------------------------------
# Default registry — populated from step_exec.py tool definitions
# ---------------------------------------------------------------------------

def _build_default_registry() -> ToolRegistry:
    """Build the canonical registry from EXECUTE_TOOLS in step_exec.py.

    This keeps step_exec.py as the single source of truth for schema content
    while adding role metadata and the registry API on top.
    """
    try:
        from step_exec import EXECUTE_TOOLS
    except ImportError:
        log.warning("tool_registry: could not import EXECUTE_TOOLS from step_exec")
        return ToolRegistry()

    # Role assignments for each tool
    _ROLE_MAP: Dict[str, List[str]] = {
        "complete_step":      [ROLE_WORKER, ROLE_SHORT, ROLE_VERIFIER],
        "flag_stuck":         [ROLE_WORKER, ROLE_SHORT, ROLE_INSPECTOR, ROLE_VERIFIER],
        "create_team_worker": [ROLE_WORKER],
        "schedule_run":       [ROLE_WORKER],
    }

    reg = ToolRegistry()
    for schema in EXECUTE_TOOLS:
        name = schema["name"]
        reg.register(ToolDefinition(
            name=name,
            description=schema.get("description", ""),
            input_schema=schema.get("parameters", {}),
            roles_allowed=_ROLE_MAP.get(name, list(_ALL_ROLES)),
        ))
    return reg


# Module-level singleton — import and use directly:
#   from tool_registry import registry, PermissionContext, ROLE_WORKER
registry: ToolRegistry = _build_default_registry()


# ---------------------------------------------------------------------------
# Convenience: role-preset contexts
# ---------------------------------------------------------------------------

def worker_context(deny_patterns: Optional[List[str]] = None) -> PermissionContext:
    return PermissionContext(role=ROLE_WORKER, deny_patterns=deny_patterns or [])

def short_context(deny_patterns: Optional[List[str]] = None) -> PermissionContext:
    return PermissionContext(role=ROLE_SHORT, deny_patterns=deny_patterns or [])

def inspector_context(deny_patterns: Optional[List[str]] = None) -> PermissionContext:
    return PermissionContext(role=ROLE_INSPECTOR, deny_patterns=deny_patterns or [])

def director_context(deny_patterns: Optional[List[str]] = None) -> PermissionContext:
    return PermissionContext(role=ROLE_DIRECTOR, deny_patterns=deny_patterns or [])
