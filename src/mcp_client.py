"""mcp_client.py — Phase 41 step 7: MCP (Model Context Protocol) client.

Exposes external MCP tool servers as ToolDefinition entries in the registry,
with should_defer=True so they participate in the deferred-loading machinery
from tool_search.py without bloating the initial context window.

Naming convention: mcp__<server_name>__<tool_name>
  e.g. mcp__memory__store, mcp__filesystem__read_file

Two transports supported:
  - stdio: launch a subprocess, JSON-RPC over stdin/stdout
  - http:  POST to an HTTP endpoint (streamable SSE or direct JSON)

Usage (register a server):
    from mcp_client import MCPServerClient, register_mcp_server
    from tool_registry import registry

    # stdio transport
    client = MCPServerClient.stdio("memory", ["npx", "-y", "@modelcontextprotocol/server-memory"])
    register_mcp_server(client, registry)

    # HTTP transport
    client = MCPServerClient.http("filesystem", "http://localhost:3001")
    register_mcp_server(client, registry)

Protocol reference: https://spec.modelcontextprotocol.io/specification/
"""

from __future__ import annotations

import json
import logging
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

log = logging.getLogger("poe.mcp_client")

# MCP JSON-RPC protocol version
_JSONRPC_VERSION = "2.0"
_MCP_PROTOCOL_VERSION = "2024-11-05"

# Timeout for individual RPC calls (seconds)
_RPC_TIMEOUT = 30


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class MCPError(RuntimeError):
    """Raised when an MCP server returns an error or the transport fails."""
    pass


class MCPTransportError(MCPError):
    """Transport-level failure (subprocess crashed, HTTP unreachable)."""
    pass


class MCPProtocolError(MCPError):
    """Server returned a JSON-RPC error response."""
    def __init__(self, code: int, message: str, data: Any = None):
        self.code = code
        self.data = data
        super().__init__(f"MCP error {code}: {message}")


# ---------------------------------------------------------------------------
# Transport layer
# ---------------------------------------------------------------------------

class _StdioTransport:
    """Manages a long-lived subprocess and JSON-RPC framing over stdio."""

    def __init__(self, command: List[str]):
        self._command = command
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._next_id = 1

    def start(self) -> None:
        if self._proc is not None:
            return
        try:
            self._proc = subprocess.Popen(
                self._command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,  # line-buffered
            )
        except FileNotFoundError as exc:
            raise MCPTransportError(f"MCP server command not found: {self._command[0]}") from exc

    def stop(self) -> None:
        if self._proc is None:
            return
        try:
            self._proc.stdin.close()
            self._proc.wait(timeout=5)
        except Exception:
            self._proc.kill()
        finally:
            self._proc = None

    def send(self, method: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """Send a request and block until the matching response arrives."""
        with self._lock:
            if self._proc is None:
                raise MCPTransportError("Transport not started — call start() first")

            msg_id = self._next_id
            self._next_id += 1

            request = {
                "jsonrpc": _JSONRPC_VERSION,
                "id": msg_id,
                "method": method,
                "params": params or {},
            }
            line = json.dumps(request) + "\n"
            try:
                self._proc.stdin.write(line)
                self._proc.stdin.flush()
            except BrokenPipeError as exc:
                raise MCPTransportError("MCP subprocess stdin closed unexpectedly") from exc

            deadline = time.monotonic() + _RPC_TIMEOUT
            while time.monotonic() < deadline:
                raw = self._proc.stdout.readline()
                if not raw:
                    rc = self._proc.poll()
                    raise MCPTransportError(
                        f"MCP subprocess exited unexpectedly (rc={rc})"
                    )
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    resp = json.loads(raw)
                except json.JSONDecodeError:
                    log.debug("MCP: non-JSON line from server: %s", raw[:200])
                    continue
                if resp.get("id") != msg_id:
                    # Notification or out-of-order response — skip
                    continue
                if "error" in resp:
                    err = resp["error"]
                    raise MCPProtocolError(
                        err.get("code", -1),
                        err.get("message", "unknown error"),
                        err.get("data"),
                    )
                return resp.get("result")

            raise MCPTransportError(
                f"MCP RPC timed out after {_RPC_TIMEOUT}s (method={method})"
            )

    def notify(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        if self._proc is None:
            raise MCPTransportError("Transport not started")
        msg = {"jsonrpc": _JSONRPC_VERSION, "method": method, "params": params or {}}
        line = json.dumps(msg) + "\n"
        self._proc.stdin.write(line)
        self._proc.stdin.flush()


class _HttpTransport:
    """HTTP JSON-RPC transport — sends each request as an HTTP POST."""

    def __init__(self, base_url: str, headers: Optional[Dict[str, str]] = None):
        self._base_url = base_url.rstrip("/")
        self._headers = headers or {}
        self._next_id = 1
        self._lock = threading.Lock()

    def start(self) -> None:
        pass  # No-op for HTTP — connections are per-request

    def stop(self) -> None:
        pass  # No-op

    def send(self, method: str, params: Optional[Dict[str, Any]] = None) -> Any:
        try:
            import requests  # type: ignore
        except ImportError as exc:
            raise MCPTransportError(
                "requests package required for HTTP MCP transport: pip install requests"
            ) from exc

        with self._lock:
            msg_id = self._next_id
            self._next_id += 1

        payload = {
            "jsonrpc": _JSONRPC_VERSION,
            "id": msg_id,
            "method": method,
            "params": params or {},
        }
        headers = {"Content-Type": "application/json", **self._headers}
        try:
            resp = requests.post(
                self._base_url,
                json=payload,
                headers=headers,
                timeout=_RPC_TIMEOUT,
            )
            resp.raise_for_status()
        except Exception as exc:
            raise MCPTransportError(f"MCP HTTP request failed: {exc}") from exc

        data = resp.json()
        if "error" in data:
            err = data["error"]
            raise MCPProtocolError(
                err.get("code", -1),
                err.get("message", "unknown error"),
                err.get("data"),
            )
        return data.get("result")

    def notify(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        """Send notification (fire-and-forget POST, ignore response)."""
        try:
            import requests  # type: ignore
            msg = {"jsonrpc": _JSONRPC_VERSION, "method": method, "params": params or {}}
            requests.post(
                self._base_url,
                json=msg,
                headers={"Content-Type": "application/json", **self._headers},
                timeout=5,
            )
        except Exception as exc:
            log.debug("MCP HTTP notification failed (non-fatal): %s", exc)


# ---------------------------------------------------------------------------
# MCPServerClient
# ---------------------------------------------------------------------------

@dataclass
class MCPToolInfo:
    """Raw tool descriptor returned by the MCP server's tools/list."""
    name: str
    description: str
    input_schema: Dict[str, Any]


class MCPServerClient:
    """Client for a single MCP tool server.

    Handles the initialize handshake, tool discovery, and tool invocation.
    Naming convention for tools registered into the Poe registry:
        mcp__<server_name>__<tool_name>

    Create via class methods:
        MCPServerClient.stdio("memory", ["npx", "-y", "@modelcontextprotocol/server-memory"])
        MCPServerClient.http("filesystem", "http://localhost:3001")
    """

    def __init__(self, server_name: str, transport: "_StdioTransport | _HttpTransport"):
        self.server_name = server_name
        self._transport = transport
        self._initialized = False
        self._server_info: Dict[str, Any] = {}
        self._capabilities: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Factory methods
    # ------------------------------------------------------------------

    @classmethod
    def stdio(cls, server_name: str, command: List[str]) -> "MCPServerClient":
        """Create a client using stdio transport (subprocess)."""
        return cls(server_name, _StdioTransport(command))

    @classmethod
    def http(
        cls,
        server_name: str,
        base_url: str,
        headers: Optional[Dict[str, str]] = None,
    ) -> "MCPServerClient":
        """Create a client using HTTP transport."""
        return cls(server_name, _HttpTransport(base_url, headers))

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Start transport and perform the MCP initialize handshake."""
        self._transport.start()
        self._initialize()

    def disconnect(self) -> None:
        """Send shutdown notification and stop transport."""
        if self._initialized:
            try:
                self._transport.notify("notifications/cancelled", {"requestId": "shutdown"})
            except Exception:
                pass
        self._transport.stop()
        self._initialized = False

    def __enter__(self) -> "MCPServerClient":
        self.connect()
        return self

    def __exit__(self, *_: Any) -> None:
        self.disconnect()

    # ------------------------------------------------------------------
    # MCP protocol methods
    # ------------------------------------------------------------------

    def _initialize(self) -> None:
        """Perform MCP initialize + initialized handshake."""
        result = self._transport.send(
            "initialize",
            {
                "protocolVersion": _MCP_PROTOCOL_VERSION,
                "capabilities": {
                    "roots": {"listChanged": False},
                    "sampling": {},
                },
                "clientInfo": {
                    "name": "poe-orchestration",
                    "version": "1.0.0",
                },
            },
        )
        if result is None:
            raise MCPProtocolError(-1, "initialize returned no result")

        self._server_info = result.get("serverInfo", {})
        self._capabilities = result.get("capabilities", {})
        proto = result.get("protocolVersion", "")

        log.info(
            "MCP server '%s' connected — server=%s proto=%s",
            self.server_name,
            self._server_info.get("name", "unknown"),
            proto,
        )

        # Required: send initialized notification to complete handshake
        self._transport.notify("notifications/initialized")
        self._initialized = True

    def list_tools(self) -> List[MCPToolInfo]:
        """Fetch the tool list from the MCP server.

        Returns a list of MCPToolInfo with name, description, input_schema.
        Raises MCPError on transport or protocol failure.
        """
        if not self._initialized:
            raise MCPError("Client not initialized — call connect() first")

        result = self._transport.send("tools/list")
        if result is None:
            return []

        tools = result.get("tools", [])
        out = []
        for t in tools:
            name = t.get("name", "")
            if not name:
                continue
            out.append(MCPToolInfo(
                name=name,
                description=t.get("description", ""),
                input_schema=t.get("inputSchema", {"type": "object", "properties": {}}),
            ))
        log.debug("MCP server '%s' listed %d tools", self.server_name, len(out))
        return out

    def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """Invoke a tool on the MCP server.

        Args:
            tool_name:  The MCP tool name (without mcp__<server>__ prefix).
            arguments:  Dict of arguments matching the tool's input_schema.

        Returns:
            The tool result content — a list of content blocks per the MCP spec,
            or the raw result dict if the server returns a non-standard shape.

        Raises:
            MCPError on transport failure or protocol error.
        """
        if not self._initialized:
            raise MCPError("Client not initialized — call connect() first")

        result = self._transport.send(
            "tools/call",
            {"name": tool_name, "arguments": arguments},
        )
        if result is None:
            return []

        # MCP spec: result has {content: [...], isError: bool}
        if "isError" in result and result["isError"]:
            content = result.get("content", [])
            text = _extract_text(content) or str(content)
            raise MCPError(f"Tool '{tool_name}' returned error: {text}")

        return result.get("content", result)

    # ------------------------------------------------------------------
    # Registry integration helpers
    # ------------------------------------------------------------------

    def registry_tool_name(self, mcp_tool_name: str) -> str:
        """Convert an MCP tool name to the registry naming convention."""
        return f"mcp__{self.server_name}__{mcp_tool_name}"

    @property
    def server_capabilities(self) -> Dict[str, Any]:
        return dict(self._capabilities)

    @property
    def server_info(self) -> Dict[str, Any]:
        return dict(self._server_info)


# ---------------------------------------------------------------------------
# Registry integration
# ---------------------------------------------------------------------------

def register_mcp_server(
    client: MCPServerClient,
    registry: Any,  # ToolRegistry — avoid circular import
    roles_allowed: Optional[List[str]] = None,
    defer: bool = True,
) -> int:
    """Connect client, fetch its tools, and register them into registry.

    Each tool is registered as mcp__<server>__<tool_name> with should_defer=True
    by default (token-efficient: only name+description in initial prompt).

    Args:
        client:       Connected or unconnected MCPServerClient.
        registry:     ToolRegistry instance to register into.
        roles_allowed: Roles that can see these tools (empty = all roles).
        defer:        Whether to register with should_defer=True (default True).

    Returns:
        Number of tools registered.
    """
    from tool_registry import ToolDefinition  # local import to avoid circular dep

    if not client._initialized:
        client.connect()

    tools = client.list_tools()
    count = 0
    for tool in tools:
        reg_name = client.registry_tool_name(tool.name)

        # Capture tool_name in closure for call_tool dispatch
        def make_caller(c: MCPServerClient, t: str):
            def _call(arguments: Dict[str, Any]) -> Any:
                return c.call_tool(t, arguments)
            return _call

        td = ToolDefinition(
            name=reg_name,
            description=tool.description,
            input_schema=tool.input_schema,
            roles_allowed=roles_allowed or [],
            should_defer=defer,
        )
        # Attach caller as metadata for executor dispatch
        td._mcp_caller = make_caller(client, tool.name)  # type: ignore[attr-defined]
        td._mcp_server = client.server_name  # type: ignore[attr-defined]
        td._mcp_tool_name = tool.name  # type: ignore[attr-defined]

        registry.register(td)
        log.info("Registered MCP tool: %s", reg_name)
        count += 1

    return count


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _extract_text(content: Any) -> str:
    """Extract text from MCP content blocks (list of {type, text} dicts)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n".join(parts)
    return str(content)


def dispatch_mcp_call(tool_name: str, arguments: Dict[str, Any], registry: Any) -> Any:
    """Look up a registered MCP tool by registry name and invoke it.

    This is the bridge between the executor (which calls tools by registry name)
    and the MCPServerClient (which calls tools by MCP name).

    Raises KeyError if tool_name is not a registered MCP tool.
    Raises MCPError on server failure.
    """
    td = registry.get(tool_name)
    if td is None:
        raise KeyError(f"Tool not found in registry: {tool_name}")
    caller = getattr(td, "_mcp_caller", None)
    if caller is None:
        raise KeyError(f"Tool '{tool_name}' is not an MCP tool (no _mcp_caller)")
    return caller(arguments)
