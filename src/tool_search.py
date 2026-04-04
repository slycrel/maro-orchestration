"""tool_search.py — Phase 41 step 6: Deferred tool resolution via ToolSearch.

When a ToolDefinition has should_defer=True, only a stub (name + description,
empty parameters) appears in the initial system prompt. This prevents prompt
bloat for rarely-used tools while keeping them discoverable.

When the model wants to use a deferred tool, it calls `tool_search` first:

    tool_search(query="schedule")
    → Returns full schema for matching tools → model can now call them correctly

ToolSearch is always a full tool (never deferred itself) so the model always has
access to it. step_exec.py handles the tool_search response by injecting the
retrieved schemas into the next LLM call.

Integration pattern in step_exec:
    1. LLM calls tool_search(query="schedule_run")
    2. step_exec detects tc.name == "tool_search"
    3. Calls resolve_deferred_tools(query, ctx) → list of full schemas
    4. Injects schemas + re-calls LLM with the additional tool definitions
    5. LLM now calls the actual tool with correct parameters

This avoids prompt explosion for large tool sets while preserving full
capability discovery.
"""

from __future__ import annotations

import fnmatch
import logging
from typing import List, Optional

log = logging.getLogger("poe.tool_search")


# ---------------------------------------------------------------------------
# Tool search schema (always injected as a full tool, never deferred)
# ---------------------------------------------------------------------------

TOOL_SEARCH_SCHEMA: dict = {
    "name": "tool_search",
    "description": (
        "Look up the full schema for one or more tools marked as [deferred]. "
        "Use this when you need to call a deferred tool but don't have its parameters. "
        "Returns the full parameter schemas so you can make a well-formed tool call."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Tool name or keyword to search for. "
                    "Exact name preferred (e.g. 'schedule_run'). "
                    "Partial names and keywords also work."
                ),
            },
        },
        "required": ["query"],
    },
}


# ---------------------------------------------------------------------------
# Resolution logic
# ---------------------------------------------------------------------------

def resolve_deferred_tools(
    query: str,
    ctx=None,
    *,
    registry=None,
) -> List[dict]:
    """Return full schemas for deferred tools matching query.

    Matching strategy (in order of priority):
    1. Exact name match
    2. Name contains query (case-insensitive)
    3. Glob match on name
    4. Query appears in description

    Only returns tools that:
    - Are registered in the registry
    - Are visible to ctx (role + deny patterns)
    - Have should_defer=True (non-deferred tools are already in the prompt)

    Args:
        query:    Name or keyword to search for.
        ctx:      PermissionContext for role/deny filtering. None = worker.
        registry: ToolRegistry to search. None = module-level default.

    Returns:
        List of full schema dicts (to_schema() format). Empty list if nothing found.
    """
    if registry is None:
        try:
            from tool_registry import registry as _default_registry
            registry = _default_registry
        except ImportError:
            log.warning("tool_search: cannot import registry")
            return []

    if ctx is None:
        try:
            from tool_registry import PermissionContext
            ctx = PermissionContext()
        except ImportError:
            ctx = None

    # Get all deferred tools visible to ctx
    all_tools = registry.get_tools(ctx)
    deferred = [t for t in all_tools if t.should_defer]

    if not deferred:
        log.debug("tool_search: no deferred tools available for query %r", query)
        return []

    query_lower = query.strip().lower()

    def score(tool) -> int:
        name_lower = tool.name.lower()
        desc_lower = tool.description.lower()
        if name_lower == query_lower:
            return 4
        if query_lower in name_lower:
            return 3
        if fnmatch.fnmatch(name_lower, f"*{query_lower}*"):
            return 2
        if query_lower in desc_lower:
            return 1
        return 0

    scored = [(score(t), t) for t in deferred]
    matched = [(s, t) for s, t in scored if s > 0]
    matched.sort(key=lambda x: x[0], reverse=True)

    results = [t.to_schema() for _, t in matched]
    log.debug("tool_search: query=%r matched %d deferred tool(s)", query, len(results))
    return results


def format_tool_search_result(schemas: List[dict]) -> str:
    """Format resolved schemas as a human-readable block for LLM injection.

    This is injected as a user message when re-calling the LLM after a
    tool_search call resolves deferred schemas.
    """
    if not schemas:
        return "No matching tools found. Check the tool name and try again."

    lines = [f"Found {len(schemas)} tool(s). Full schemas:\n"]
    for schema in schemas:
        lines.append(f"Tool: {schema['name']}")
        lines.append(f"Description: {schema['description']}")
        params = schema.get("parameters", {})
        props = params.get("properties", {})
        req = params.get("required", [])
        if props:
            lines.append("Parameters:")
            for pname, pdef in props.items():
                required_marker = " (required)" if pname in req else ""
                ptype = pdef.get("type", "any")
                pdesc = pdef.get("description", "")
                lines.append(f"  - {pname} [{ptype}]{required_marker}: {pdesc}")
        lines.append("")
    lines.append("You can now call the tool with the correct parameters.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Injection helper: add tool_search to a tool list if deferred tools exist
# ---------------------------------------------------------------------------

def inject_tool_search_if_needed(schemas: List[dict]) -> List[dict]:
    """Add the tool_search schema to schemas if any entries are deferred stubs.

    Deferred stubs have empty parameters (properties == {}). This function
    detects them and adds tool_search so the model can look up full schemas.

    Call this before passing schemas to the LLM adapter.
    """
    has_deferred = any(
        not s.get("parameters", {}).get("properties")
        and "[deferred]" in s.get("description", "")
        for s in schemas
    )
    if not has_deferred:
        return schemas

    # Don't double-add
    if any(s["name"] == "tool_search" for s in schemas):
        return schemas

    return schemas + [TOOL_SEARCH_SCHEMA]
