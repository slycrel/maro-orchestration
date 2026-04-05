"""tests/test_mcp_loader.py — Phase 41 step 7: MCP loader test suite.

Covers:
 - Server registration (stdio + HTTP transport selection)
 - Deferred stub injection (should_defer=True, to_stub() shape)
 - resolve_and_call round-trip (MCP and generic _handler paths)
 - Error on unreachable server (MCPTransportError propagation)

All MCP network I/O is mocked via unittest.mock — no real subprocess or HTTP.
"""

from __future__ import annotations

import sys
import os
import json
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

# Ensure src/ is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tool_registry import (
    ToolDefinition,
    ToolRegistry,
    PermissionContext,
    ROLE_WORKER,
)
from mcp_client import (
    MCPServerClient,
    MCPToolInfo,
    MCPTransportError,
    MCPProtocolError,
    MCPError,
    register_mcp_server,
    dispatch_mcp_call,
    _extract_text,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client(server_name: str = "test_server", tools: List[MCPToolInfo] = None) -> MCPServerClient:
    """Return a mock-connected MCPServerClient with specified tools."""
    client = MCPServerClient.stdio(server_name, ["echo", "mcp"])
    client._initialized = True
    client._server_info = {"name": server_name}
    client._capabilities = {}
    if tools is None:
        tools = [
            MCPToolInfo(
                name="echo",
                description="Echo back input",
                input_schema={"type": "object", "properties": {"msg": {"type": "string"}}},
            ),
            MCPToolInfo(
                name="upper",
                description="Uppercase input",
                input_schema={"type": "object", "properties": {"text": {"type": "string"}}},
            ),
        ]
    client.list_tools = MagicMock(return_value=tools)
    return client


# ---------------------------------------------------------------------------
# 1. Server registration — tool count and naming convention
# ---------------------------------------------------------------------------

class TestServerRegistration:

    def test_register_mcp_server_returns_tool_count(self):
        reg = ToolRegistry()
        client = _make_client("myserver")
        n = register_mcp_server(client, reg)
        assert n == 2

    def test_registered_names_follow_convention(self):
        reg = ToolRegistry()
        client = _make_client("myserver")
        register_mcp_server(client, reg)
        names = reg.names()
        assert "mcp__myserver__echo" in names
        assert "mcp__myserver__upper" in names

    def test_register_zero_tools(self):
        reg = ToolRegistry()
        client = _make_client("empty", tools=[])
        n = register_mcp_server(client, reg)
        assert n == 0
        assert reg.names() == []

    def test_load_mcp_server_stdio_transport(self):
        """load_mcp_server() with a shell string uses stdio transport."""
        reg = ToolRegistry()
        client_mock = _make_client("cli_tool")
        # MCPServerClient is imported locally inside load_mcp_server — patch in mcp_client
        with patch("mcp_client.MCPServerClient") as MockClient:
            MockClient.stdio.return_value = client_mock
            with patch("mcp_client.register_mcp_server", return_value=2) as mock_reg:
                reg.load_mcp_server("npx some-mcp-server")
                assert mock_reg.called

    def test_load_mcp_server_http_transport(self):
        """load_mcp_server() with an http:// URL uses HTTP transport."""
        reg = ToolRegistry()
        client_mock = _make_client("http_server")
        with patch("mcp_client.MCPServerClient") as MockClient:
            MockClient.http.return_value = client_mock
            with patch("mcp_client.register_mcp_server", return_value=1) as mock_reg:
                reg.load_mcp_server("http://localhost:3001")
                assert mock_reg.called

    def test_unreachable_server_raises_transport_error(self):
        """load_mcp_server() raises MCPTransportError when server is unreachable."""
        reg = ToolRegistry()
        with patch("mcp_client.MCPServerClient") as MockClient:
            mock_instance = MagicMock()
            mock_instance._initialized = False  # force connect() to be called
            mock_instance.connect.side_effect = MCPTransportError("not found: fake-mcp-server")
            MockClient.stdio.return_value = mock_instance
            with pytest.raises(MCPTransportError):
                reg.load_mcp_server(["fake-mcp-server", "--port", "9999"])


# ---------------------------------------------------------------------------
# 2. Deferred stub injection
# ---------------------------------------------------------------------------

class TestDeferredStubInjection:

    def test_mcp_tools_registered_with_should_defer_true(self):
        reg = ToolRegistry()
        client = _make_client("srvr")
        register_mcp_server(client, reg, defer=True)
        td = reg.get("mcp__srvr__echo")
        assert td is not None
        assert td.should_defer is True

    def test_stub_shape_has_deferred_prefix(self):
        reg = ToolRegistry()
        client = _make_client("srvr")
        register_mcp_server(client, reg, defer=True)
        td = reg.get("mcp__srvr__echo")
        stub = td.to_stub()
        assert stub["name"] == "mcp__srvr__echo"
        assert stub["description"].startswith("[deferred]")
        assert stub["parameters"]["type"] == "object"

    def test_get_tool_schemas_returns_stubs_for_deferred(self):
        reg = ToolRegistry()
        client = _make_client("srvr")
        register_mcp_server(client, reg, defer=True)
        ctx = PermissionContext(role=ROLE_WORKER)
        schemas = reg.get_tool_schemas(ctx)
        for s in schemas:
            assert s["description"].startswith("[deferred]")

    def test_defer_false_returns_full_schema(self):
        reg = ToolRegistry()
        client = _make_client("srvr")
        register_mcp_server(client, reg, defer=False)
        td = reg.get("mcp__srvr__echo")
        assert td.should_defer is False
        schema = td.to_schema()
        assert "msg" in schema["parameters"]["properties"]

    def test_mcp_caller_attached_on_registration(self):
        reg = ToolRegistry()
        client = _make_client("srvr")
        register_mcp_server(client, reg)
        td = reg.get("mcp__srvr__echo")
        assert hasattr(td, "_mcp_caller")
        assert callable(td._mcp_caller)


# ---------------------------------------------------------------------------
# 3. resolve_and_call round-trip
# ---------------------------------------------------------------------------

class TestResolveAndCall:

    def test_resolve_and_call_dispatches_mcp_caller(self):
        reg = ToolRegistry()
        client = _make_client("srvr")
        register_mcp_server(client, reg)

        # Patch the registered tool's caller
        td = reg.get("mcp__srvr__echo")
        td._mcp_caller = MagicMock(return_value=[{"type": "text", "text": "hello"}])

        result = reg.resolve_and_call("mcp__srvr__echo", {"msg": "hello"})
        td._mcp_caller.assert_called_once_with({"msg": "hello"})
        assert result == [{"type": "text", "text": "hello"}]

    def test_resolve_and_call_dispatches_generic_handler(self):
        reg = ToolRegistry()
        td = ToolDefinition(
            name="my_tool",
            description="Generic tool",
            input_schema={"type": "object", "properties": {}},
        )
        td._handler = MagicMock(return_value={"ok": True})  # type: ignore[attr-defined]
        reg.register(td)

        result = reg.resolve_and_call("my_tool", {"x": 1})
        td._handler.assert_called_once_with({"x": 1})
        assert result == {"ok": True}

    def test_resolve_and_call_raises_key_error_for_unknown_tool(self):
        reg = ToolRegistry()
        with pytest.raises(KeyError, match="not found"):
            reg.resolve_and_call("nonexistent_tool", {})

    def test_resolve_and_call_raises_type_error_without_handler(self):
        reg = ToolRegistry()
        td = ToolDefinition(
            name="handlerless",
            description="No handler",
            input_schema={"type": "object", "properties": {}},
        )
        reg.register(td)
        with pytest.raises(TypeError, match="no callable handler"):
            reg.resolve_and_call("handlerless", {})

    def test_dispatch_mcp_call_uses_caller(self):
        """dispatch_mcp_call() function from mcp_client works as bridge."""
        reg = ToolRegistry()
        client = _make_client("bridge")
        register_mcp_server(client, reg)
        td = reg.get("mcp__bridge__echo")
        td._mcp_caller = MagicMock(return_value="pong")
        result = dispatch_mcp_call("mcp__bridge__echo", {"msg": "ping"}, reg)
        assert result == "pong"

    def test_dispatch_mcp_call_raises_for_non_mcp_tool(self):
        """dispatch_mcp_call raises KeyError when tool has no _mcp_caller."""
        reg = ToolRegistry()
        td = ToolDefinition(
            name="plain_tool",
            description="No MCP caller",
            input_schema={"type": "object", "properties": {}},
        )
        reg.register(td)
        with pytest.raises(KeyError):
            dispatch_mcp_call("plain_tool", {}, reg)


# ---------------------------------------------------------------------------
# 4. Error on unreachable server (mock subprocess MCP server)
# ---------------------------------------------------------------------------

class TestUnreachableServer:

    def test_stdio_transport_raises_when_command_not_found(self):
        from mcp_client import _StdioTransport
        transport = _StdioTransport(["nonexistent-mcp-server-xyz"])
        with pytest.raises(MCPTransportError, match="not found"):
            transport.start()

    def test_connect_raises_when_initialize_fails(self):
        client = MCPServerClient.stdio("badserver", ["cat"])
        with patch.object(client._transport, "start"), \
             patch.object(client._transport, "send", side_effect=MCPTransportError("EOF")):
            with pytest.raises(MCPTransportError):
                client.connect()

    def test_list_tools_raises_when_not_initialized(self):
        client = MCPServerClient.stdio("x", ["cat"])
        with pytest.raises(MCPError, match="not initialized"):
            client.list_tools()

    def test_call_tool_raises_on_protocol_error(self):
        client = _make_client("errserver")
        client.call_tool = MagicMock(
            side_effect=MCPProtocolError(-32601, "method not found")
        )
        with pytest.raises(MCPProtocolError):
            client.call_tool("missing_tool", {})

    def test_register_mcp_server_propagates_transport_error(self):
        """If connect() fails mid-registration, MCPTransportError bubbles up."""
        reg = ToolRegistry()
        client = MCPServerClient.stdio("failing", ["bad-cmd"])
        client._initialized = False
        with patch.object(client, "connect", side_effect=MCPTransportError("cannot start")):
            with pytest.raises(MCPTransportError, match="cannot start"):
                register_mcp_server(client, reg)
