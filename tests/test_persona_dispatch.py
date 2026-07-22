"""Tests for persona_dispatch — the owned one-shot persona × prompt verb."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from persona_dispatch import (
    DispatchResult,
    dispatch_panel,
    dispatch_prompt,
    _adapter_source,
    _cli_main,
)


@pytest.fixture(autouse=True)
def _hosted_free_off(monkeypatch):
    """Hermetic default — the box has live hosted-free keys; tests opt in."""
    import hosted_free
    monkeypatch.setattr(hosted_free, "available", lambda: False)


def _adapter(content='{"verdict": "STRONG"}', model_key="claude-sonnet-4-6"):
    resp = SimpleNamespace(content=content, input_tokens=5, output_tokens=5)
    a = MagicMock()
    a.complete.return_value = resp
    a.model_key = model_key
    a._active_provider = ""
    return a


def _registry(tmp_path):
    from persona import PersonaRegistry
    (tmp_path / "testcritic.md").write_text(
        "---\n"
        "name: testcritic\n"
        "role: Test Critic\n"
        "model_tier: cheap\n"
        "communication_style: blunt\n"
        "---\n"
        "Attack the weakest claim first.\n",
        encoding="utf-8",
    )
    return PersonaRegistry(personas_dir=tmp_path)


class TestDispatchPrompt:
    def test_needs_persona_or_system(self):
        r = dispatch_prompt("hello")
        assert not r.ok
        assert "persona and/or system" in r.error

    def test_unknown_persona_is_error(self, tmp_path):
        r = dispatch_prompt("hello", persona="nope", registry=_registry(tmp_path),
                            adapter=_adapter())
        assert not r.ok
        assert "not found" in r.error

    def test_persona_frames_system_message(self, tmp_path):
        adapter = _adapter(content="fine")
        r = dispatch_prompt("judge this", persona="testcritic",
                            registry=_registry(tmp_path), adapter=adapter)
        assert r.ok
        assert r.persona == "testcritic"
        assert r.content == "fine"
        messages = adapter.complete.call_args[0][0]
        assert messages[0].role == "system"
        assert "Test Critic" in messages[0].content
        assert "Attack the weakest claim first." in messages[0].content
        assert messages[1].role == "user"
        assert messages[1].content == "judge this"

    def test_system_only_dispatch(self):
        adapter = _adapter(content="ok")
        r = dispatch_prompt("p", system="You are a raw lens.", adapter=adapter)
        assert r.ok
        assert r.persona == "(system)"
        messages = adapter.complete.call_args[0][0]
        assert messages[0].content == "You are a raw lens."

    def test_persona_plus_system_appends_contract(self, tmp_path):
        adapter = _adapter(content="ok")
        dispatch_prompt("p", persona="testcritic", system="Respond with JSON.",
                        registry=_registry(tmp_path), adapter=adapter)
        sys_text = adapter.complete.call_args[0][0][0].content
        assert "Attack the weakest claim first." in sys_text
        assert sys_text.rstrip().endswith("Respond with JSON.")

    def test_no_tools_is_pinned(self):
        adapter = _adapter()
        dispatch_prompt("p", system="s", adapter=adapter)
        assert adapter.complete.call_args.kwargs["no_tools"] is True

    def test_expect_json_parses_data(self):
        adapter = _adapter(content='prose {"verdict": "WEAK"} trailing')
        r = dispatch_prompt("p", system="s", adapter=adapter, expect="json")
        assert r.data == {"verdict": "WEAK"}

    def test_no_adapter_no_hosted_is_error(self):
        r = dispatch_prompt("p", system="s")
        assert not r.ok
        assert "no adapter" in r.error

    def test_hosted_free_used_when_no_adapter(self, monkeypatch):
        import hosted_free
        hosted = _adapter(content="from the free tier",
                          model_key="llama-3.1-8b-instant")
        hosted._active_provider = "groq"
        monkeypatch.setattr(hosted_free, "available", lambda: True)
        monkeypatch.setattr(hosted_free, "build_hosted_free_adapter", lambda: hosted)
        r = dispatch_prompt("p", system="s")
        assert r.ok
        assert r.source == "hosted_free:groq:llama-3.1-8b-instant"

    def test_explicit_adapter_beats_hosted(self, monkeypatch):
        import hosted_free
        monkeypatch.setattr(
            hosted_free, "available",
            lambda: (_ for _ in ()).throw(AssertionError("hosted consulted")))
        adapter = _adapter(content="paid")
        r = dispatch_prompt("p", system="s", adapter=adapter)
        assert r.ok and r.content == "paid"

    def test_adapter_exception_never_raises(self):
        adapter = MagicMock()
        adapter.complete.side_effect = RuntimeError("boom")
        adapter.model_key = "m"
        adapter._active_provider = ""
        r = dispatch_prompt("p", system="s", adapter=adapter)
        assert not r.ok
        assert "boom" in r.error

    def test_purpose_label_passed_through(self):
        adapter = _adapter()
        dispatch_prompt("p", system="s", adapter=adapter, purpose="council lens x")
        assert adapter.complete.call_args.kwargs["purpose"] == "council lens x"


class TestDispatchPanel:
    def test_panel_preserves_order_and_isolates_failures(self, tmp_path):
        adapter = _adapter(content="answer")
        results = dispatch_panel("p", ["missing-one", "testcritic"],
                                 registry=_registry(tmp_path), adapter=adapter)
        assert len(results) == 2
        assert not results[0].ok and "not found" in results[0].error
        assert results[1].ok and results[1].persona == "testcritic"


class TestAdapterSource:
    def test_hosted_attribution(self):
        a = SimpleNamespace(_active_provider="gemini",
                            model_key="gemini-flash-lite-latest")
        assert _adapter_source(a) == "hosted_free:gemini:gemini-flash-lite-latest"

    def test_paid_attribution(self):
        a = SimpleNamespace(_active_provider="", model_key="claude-sonnet-4-6")
        assert _adapter_source(a) == "claude-sonnet-4-6"


class TestCLI:
    def test_cli_panel_json(self, monkeypatch, capsys):
        import persona_dispatch as pd
        monkeypatch.setattr(pd, "dispatch_panel", lambda prompt, names, **kw: [
            DispatchResult(persona=n, content=f"{n} says ok", source="stub")
            for n in names
        ])
        rc = _cli_main(["do the thing", "--panel", "critic,simplifier", "--json"])
        assert rc == 0
        out = capsys.readouterr().out
        assert '"persona": "critic"' in out
        assert '"persona": "simplifier"' in out

    def test_cli_error_exit_code(self, monkeypatch, capsys):
        import persona_dispatch as pd
        monkeypatch.setattr(pd, "dispatch_panel", lambda prompt, names, **kw: [
            DispatchResult(persona="x", content="", error="no adapter")
        ])
        rc = _cli_main(["p", "--persona", "x"])
        assert rc == 1
        assert "ERROR: no adapter" in capsys.readouterr().out
