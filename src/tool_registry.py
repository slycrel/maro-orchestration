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

    def resolve_and_call(self, tool_name: str, input_data: Dict[str, Any]) -> Any:
        """Resolve a registered tool by name and invoke it with input_data.

        For MCP tools (registered via load_mcp_server / register_mcp_server),
        this dispatches through the tool's MCPServerClient.call_tool().

        For non-MCP tools that expose a callable via a ``_handler`` attribute,
        that handler is called with input_data.

        Raises:
            KeyError:   tool_name not found in the registry.
            TypeError:  tool is registered but has no callable handler.
        """
        td = self._tools.get(tool_name)
        if td is None:
            raise KeyError(f"Tool not found in registry: {tool_name!r}")

        # MCP tools attach _mcp_caller at registration time
        caller = getattr(td, "_mcp_caller", None)
        if caller is not None:
            return caller(input_data)

        # Generic handler hook (non-MCP tools may attach _handler)
        handler = getattr(td, "_handler", None)
        if handler is not None:
            return handler(input_data)

        raise TypeError(
            f"Tool {tool_name!r} is registered but has no callable handler "
            f"(_mcp_caller or _handler). Cannot invoke via resolve_and_call()."
        )

    def load_mcp_server(
        self,
        cmd_or_url: "str | List[str]",
        server_name: Optional[str] = None,
        roles_allowed: Optional[List[str]] = None,
        defer: bool = True,
        http_headers: Optional[Dict[str, str]] = None,
    ) -> int:
        """Connect to an MCP tool server and register its tools as deferred stubs.

        Detects transport from cmd_or_url:
          - str starting with 'http://' or 'https://' → HTTP transport
          - str or List[str] otherwise → stdio transport (subprocess command)

        Tools are registered as mcp__<server_name>__<tool_name> with
        should_defer=True by default (only name+description in initial prompt).

        Args:
            cmd_or_url:    HTTP URL, shell command string, or argv list.
            server_name:   Override auto-derived server name.
            roles_allowed: Roles that can see these tools (empty = all roles).
            defer:         Register tools as deferred stubs (default True).
            http_headers:  Extra HTTP headers (HTTP transport only).

        Returns:
            Number of tools registered.

        Raises:
            MCPTransportError: if the server cannot be reached.
            MCPProtocolError:  if the initialize handshake fails.
        """
        # Late import — mcp_client imports from tool_registry, so we defer to
        # break the circular dependency at module load time.
        from mcp_client import MCPServerClient, register_mcp_server  # noqa: PLC0415

        # ---- transport selection ----------------------------------------
        if isinstance(cmd_or_url, str) and cmd_or_url.startswith(("http://", "https://")):
            derived_name = _server_name_from_url(cmd_or_url)
            client = MCPServerClient.http(
                server_name or derived_name,
                cmd_or_url,
                headers=http_headers,
            )
        else:
            # stdio — accept both a bare string and an argv list
            argv: List[str] = (
                cmd_or_url if isinstance(cmd_or_url, list) else cmd_or_url.split()
            )
            derived_name = _server_name_from_argv(argv)
            client = MCPServerClient.stdio(server_name or derived_name, argv)

        return register_mcp_server(
            client,
            self,
            roles_allowed=roles_allowed,
            defer=defer,
        )


# ---------------------------------------------------------------------------
# Helpers for load_mcp_server name derivation
# ---------------------------------------------------------------------------

def _server_name_from_url(url: str) -> str:
    """Derive a stable server name from an HTTP URL.

    Examples:
        'http://localhost:3001'         → 'localhost'
        'https://mcp.example.com/mem'  → 'example'
    """
    try:
        from urllib.parse import urlparse
        host = urlparse(url).hostname or "mcp"
        # Use the second-level domain label if hostname is multi-part
        parts = host.split(".")
        return parts[-2] if len(parts) >= 2 else parts[0]
    except Exception:
        return "mcp"


def _server_name_from_argv(argv: List[str]) -> str:
    """Derive a stable server name from a stdio command argv list.

    Examples:
        ['npx', '-y', '@modelcontextprotocol/server-memory']  → 'memory'
        ['python', 'filesystem_server.py']                    → 'filesystem_server'
        ['/usr/local/bin/mcp-brave-search']                   → 'mcp_brave_search'
    """
    import os
    import re
    # Walk argv in reverse to find the first non-flag positional argument
    for token in reversed(argv):
        if token.startswith("-"):
            continue
        # Strip npm package scope (e.g. @scope/server-foo → server-foo)
        token = re.sub(r"^@[^/]+/", "", token)
        # Strip file extension and path
        token = os.path.splitext(os.path.basename(token))[0]
        # Normalise: strip common prefixes like 'server-' or 'mcp-server-'
        token = re.sub(r"^(?:mcp-server-|server-)", "", token, flags=re.IGNORECASE)
        token = token.replace("-", "_").lower()
        if token:
            return token
    return "mcp"


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
        "register_tool":      [ROLE_WORKER],
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
