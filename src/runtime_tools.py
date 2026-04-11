"""runtime_tools.py — Pi self-extending agent pattern.

During a mission the agent can call register_tool() to create a new
bash-backed tool, which becomes available in subsequent steps automatically
via the tool_registry singleton.

Runtime tools persist across sessions in memory/runtime_tools.json.
On module import, existing tools are loaded and registered automatically.

Usage (via tool call in step_exec):
    from runtime_tools import register_runtime_tool, dispatch_runtime_tool
    schema = register_runtime_tool("jq_filter", "Filter JSON with jq", "echo {args} | jq .")
    result = dispatch_runtime_tool("jq_filter", {"args": "[1,2,3]"})
"""

from __future__ import annotations

import json
import logging
import shlex
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("poe.runtime_tools")

_NAME_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_")


def _runtime_tools_path() -> Path:
    try:
        from orch_items import orch_root
        return orch_root() / "memory" / "runtime_tools.json"
    except Exception:
        return Path(__file__).parent.parent / "memory" / "runtime_tools.json"


# ---------------------------------------------------------------------------
# Data type
# ---------------------------------------------------------------------------

_DEFAULT_PARAMS: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "args": {
            "type": "string",
            "description": "Arguments to pass to the command.",
        }
    },
    "required": [],
}


@dataclass
class RuntimeTool:
    name: str
    description: str
    bash_template: str
    parameters: Dict[str, Any] = field(default_factory=lambda: dict(_DEFAULT_PARAMS))

    def to_schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        """Run bash_template with substituted arguments. Returns stdout+stderr.

        All arguments are shell-quoted before substitution to prevent injection.
        """
        # Sanitize all arguments with shlex.quote to prevent shell injection
        safe_args = {k: shlex.quote(str(v)) for k, v in arguments.items()}
        try:
            cmd = self.bash_template.format(**safe_args)
        except KeyError as exc:
            return f"[runtime_tool error: missing argument {exc}]"
        except Exception as exc:
            return f"[runtime_tool error: template format failed: {exc}]"
        try:
            result = subprocess.run(
                shlex.split(cmd),
                capture_output=True,
                text=True,
                timeout=60,
            )
            output = (result.stdout + result.stderr).strip()
            return output if output else "[no output]"
        except subprocess.TimeoutExpired:
            return "[runtime_tool error: command timed out after 60s]"
        except Exception as exc:
            return f"[runtime_tool error: {exc}]"


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

class _RuntimeToolStore:
    """In-memory store backed by JSON file. Lazy-loads on first access."""

    def __init__(self) -> None:
        self._tools: Dict[str, RuntimeTool] = {}
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            path = _runtime_tools_path()
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                for item in data:
                    t = RuntimeTool(
                        name=item["name"],
                        description=item.get("description", ""),
                        bash_template=item.get("bash_template", ""),
                        parameters=item.get("parameters", dict(_DEFAULT_PARAMS)),
                    )
                    self._tools[t.name] = t
                    _register_in_global_registry(t)
                if self._tools:
                    log.debug("runtime_tools: loaded %d tools from disk", len(self._tools))
        except Exception as exc:
            log.warning("runtime_tools: load failed (non-fatal): %s", exc)

    def register(self, tool: RuntimeTool) -> None:
        self._ensure_loaded()
        self._tools[tool.name] = tool
        _register_in_global_registry(tool)
        self._save()

    def get(self, name: str) -> Optional[RuntimeTool]:
        self._ensure_loaded()
        return self._tools.get(name)

    def all_tools(self) -> List[RuntimeTool]:
        self._ensure_loaded()
        return list(self._tools.values())

    def _save(self) -> None:
        try:
            path = _runtime_tools_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            data = [asdict(t) for t in self._tools.values()]
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as exc:
            log.warning("runtime_tools: save failed (non-fatal): %s", exc)


def _register_in_global_registry(tool: RuntimeTool) -> None:
    """Add a RuntimeTool to the module-level tool_registry singleton."""
    try:
        from tool_registry import registry, ToolDefinition
        registry.register(ToolDefinition(
            name=tool.name,
            description=tool.description,
            input_schema=tool.parameters,
        ))
    except Exception as exc:
        log.debug("runtime_tools: global registry unavailable: %s", exc)


# Module singleton — auto-loads from disk on first access.
_store = _RuntimeToolStore()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def register_runtime_tool(
    name: str,
    description: str,
    bash_template: str,
    parameters: Optional[Dict[str, Any]] = None,
) -> dict:
    """Create, persist, and return a new runtime tool.

    Args:
        name:          Tool name (snake_case, alphanumeric + underscores only).
        description:   What the tool does (shown in system prompt).
        bash_template: Shell command with {placeholder} substitution. Placeholders
                       must match parameter names in the parameters schema.
                       Example: "jq {filter} {file}"
        parameters:    JSON Schema dict for tool parameters. Defaults to a single
                       string 'args' parameter if omitted.

    Returns:
        Tool schema dict (name, description, parameters) for injection into
        the active tool list in subsequent steps.

    Raises:
        ValueError: If name is empty or contains invalid characters.
    """
    if not name:
        raise ValueError("Tool name cannot be empty")
    if not all(c in _NAME_CHARS for c in name):
        raise ValueError(
            f"Invalid tool name {name!r}: only lowercase alphanumeric and underscores allowed"
        )
    tool = RuntimeTool(
        name=name,
        description=description,
        bash_template=bash_template,
        parameters=parameters or dict(_DEFAULT_PARAMS),
    )
    _store.register(tool)
    log.info("runtime_tools: registered %r (bash_template=%r)", name, bash_template[:60])
    return tool.to_schema()


def dispatch_runtime_tool(name: str, arguments: Dict[str, Any]) -> Optional[str]:
    """Execute a runtime tool by name. Returns output string or None if not found."""
    _store._ensure_loaded()
    tool = _store.get(name)
    if tool is None:
        return None
    log.debug("runtime_tools: dispatching %r with args=%r", name, list(arguments.keys()))
    return tool.execute(arguments)


def list_runtime_tools() -> List[RuntimeTool]:
    """Return all persisted runtime tools."""
    _store._ensure_loaded()
    return _store.all_tools()


def clear_runtime_tools() -> None:
    """Remove all runtime tools (for testing). Also unregisters from global registry."""
    _store._ensure_loaded()
    names = list(_store._tools.keys())
    _store._tools.clear()
    _store._save()
    try:
        from tool_registry import registry
        for name in names:
            registry._tools.pop(name, None)
    except Exception:
        pass
    log.debug("runtime_tools: cleared %d tools", len(names))
