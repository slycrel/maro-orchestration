"""Tests for runtime_tools.py — Pi self-extending agent pattern."""
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Stub orch_items so _runtime_tools_path() resolves to a temp dir
_orch_stub = types.ModuleType("orch_items")


def _src():
    return Path(__file__).parent.parent / "src"


sys.path.insert(0, str(_src()))


# ---------------------------------------------------------------------------
# RuntimeTool unit tests
# ---------------------------------------------------------------------------

class TestRuntimeTool(unittest.TestCase):
    def _make(self, bash_template="echo {args}"):
        from runtime_tools import RuntimeTool
        return RuntimeTool(
            name="test_tool",
            description="A test tool",
            bash_template=bash_template,
        )

    def test_to_schema_keys(self):
        t = self._make()
        schema = t.to_schema()
        self.assertEqual(schema["name"], "test_tool")
        self.assertEqual(schema["description"], "A test tool")
        self.assertIn("parameters", schema)

    def test_execute_basic(self):
        t = self._make("echo hello_{args}")
        result = t.execute({"args": "world"})
        self.assertIn("hello_world", result)

    def test_execute_missing_arg(self):
        t = self._make("echo {missing}")
        result = t.execute({"args": "x"})
        self.assertIn("missing argument", result.lower())

    def test_execute_nonzero_exit_still_returns(self):
        t = self._make("exit 1")
        result = t.execute({})
        # Should not raise — result is whatever the command produced
        self.assertIsInstance(result, str)

    def test_execute_no_output(self):
        t = self._make("true")
        result = t.execute({})
        self.assertEqual(result, "[no output]")

    def test_execute_timeout_reported(self):
        from runtime_tools import RuntimeTool
        import subprocess
        t = RuntimeTool("slow", "slow", "sleep 5")
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("sleep", 60)):
            result = t.execute({})
        self.assertIn("timed out", result)


# ---------------------------------------------------------------------------
# register_runtime_tool / dispatch_runtime_tool
# ---------------------------------------------------------------------------

class TestRegisterAndDispatch(unittest.TestCase):
    def setUp(self):
        # Isolate from real disk and real registry
        import runtime_tools as rt
        # Reset store state
        rt._store._tools.clear()
        rt._store._loaded = True  # prevent real disk load
        # Patch _register_in_global_registry to be a no-op
        self._orig_register = rt._register_in_global_registry
        rt._register_in_global_registry = lambda t: None
        # Patch _save to be a no-op
        self._orig_save = rt._store._save
        rt._store._save = lambda: None

    def tearDown(self):
        import runtime_tools as rt
        rt._register_in_global_registry = self._orig_register
        rt._store._save = self._orig_save
        rt._store._tools.clear()
        rt._store._loaded = False

    def test_register_returns_schema(self):
        from runtime_tools import register_runtime_tool
        schema = register_runtime_tool("my_tool", "does stuff", "echo {args}")
        self.assertEqual(schema["name"], "my_tool")
        self.assertIn("parameters", schema)

    def test_dispatch_hits_registered_tool(self):
        from runtime_tools import register_runtime_tool, dispatch_runtime_tool
        register_runtime_tool("echo_tool", "echo", "echo hello_{args}")
        result = dispatch_runtime_tool("echo_tool", {"args": "world"})
        self.assertIsNotNone(result)
        self.assertIn("hello_world", result)

    def test_dispatch_unknown_returns_none(self):
        from runtime_tools import dispatch_runtime_tool
        result = dispatch_runtime_tool("nonexistent_tool", {})
        self.assertIsNone(result)

    def test_invalid_name_raises(self):
        from runtime_tools import register_runtime_tool
        with self.assertRaises(ValueError):
            register_runtime_tool("Bad Name!", "desc", "echo x")

    def test_invalid_name_spaces(self):
        from runtime_tools import register_runtime_tool
        with self.assertRaises(ValueError):
            register_runtime_tool("has space", "desc", "echo x")

    def test_invalid_name_empty(self):
        from runtime_tools import register_runtime_tool
        with self.assertRaises(ValueError):
            register_runtime_tool("", "desc", "echo x")

    def test_custom_parameters(self):
        from runtime_tools import register_runtime_tool, dispatch_runtime_tool
        params = {
            "type": "object",
            "properties": {
                "filter": {"type": "string"},
                "file": {"type": "string"},
            },
            "required": ["filter", "file"],
        }
        register_runtime_tool("jq_run", "run jq", "echo {filter} {file}", params)
        result = dispatch_runtime_tool("jq_run", {"filter": ".foo", "file": "bar.json"})
        self.assertIsNotNone(result)
        self.assertIn(".foo", result)

    def test_list_runtime_tools(self):
        from runtime_tools import register_runtime_tool, list_runtime_tools
        register_runtime_tool("tool_a", "a", "echo a")
        register_runtime_tool("tool_b", "b", "echo b")
        tools = list_runtime_tools()
        names = {t.name for t in tools}
        self.assertIn("tool_a", names)
        self.assertIn("tool_b", names)

    def test_register_overwrites_same_name(self):
        from runtime_tools import register_runtime_tool, dispatch_runtime_tool
        register_runtime_tool("overwrite_me", "v1", "echo version_one_{args}")
        register_runtime_tool("overwrite_me", "v2", "echo version_two_{args}")
        result = dispatch_runtime_tool("overwrite_me", {"args": "x"})
        self.assertIn("version_two", result)


# ---------------------------------------------------------------------------
# Disk persistence
# ---------------------------------------------------------------------------

class TestPersistence(unittest.TestCase):
    def test_round_trip(self):
        """Tools survive save → load cycle."""
        import tempfile, os, runtime_tools as rt
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp_path = Path(f.name)

        try:
            # Save
            tool = rt.RuntimeTool("persist_tool", "desc", "echo persisted_{args}")
            data = [{"name": tool.name, "description": tool.description,
                     "bash_template": tool.bash_template, "parameters": tool.parameters}]
            tmp_path.write_text(json.dumps(data), encoding="utf-8")

            # Load via a fresh store instance
            store = rt._RuntimeToolStore()
            store._loaded = False
            with patch.object(rt, "_runtime_tools_path", return_value=tmp_path):
                with patch.object(rt, "_register_in_global_registry"):
                    store._ensure_loaded()

            loaded = store.get("persist_tool")
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.bash_template, "echo persisted_{args}")
        finally:
            tmp_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Global registry integration
# ---------------------------------------------------------------------------

class TestGlobalRegistryIntegration(unittest.TestCase):
    def test_registers_in_tool_registry(self):
        """register_runtime_tool() adds the tool to tool_registry.registry."""
        import runtime_tools as rt
        rt._store._tools.clear()
        rt._store._loaded = True
        rt._store._save = lambda: None

        from tool_registry import registry
        # Remove any prior entry
        registry._tools.pop("registry_test_tool", None)

        rt.register_runtime_tool("registry_test_tool", "desc", "echo {args}")

        # Verify it's in the global registry
        self.assertIn("registry_test_tool", registry._tools)

        # Cleanup
        registry._tools.pop("registry_test_tool", None)
        rt._store._tools.clear()
        rt._store._loaded = False


if __name__ == "__main__":
    unittest.main()
