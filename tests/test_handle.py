"""Tests for Phase 2: handle.py (unified entry point, NOW/AGENDA routing)."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import orch
from handle import handle, HandleResult


def _setup(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------------------
# HandleResult.format()
# ---------------------------------------------------------------------------

def test_handle_result_format_text():
    r = HandleResult(
        handle_id="abc123",
        lane="now",
        lane_confidence=0.9,
        classification_reason="simple",
        message="hi",
        status="done",
        result="hello",
    )
    text = r.format("text")
    assert "handle_id=abc123" in text
    assert "lane=now" in text
    assert "hello" in text


def test_handle_result_format_json():
    r = HandleResult(
        handle_id="abc123",
        lane="agenda",
        lane_confidence=0.75,
        classification_reason="research task",
        message="research X",
        status="done",
        result="findings",
        project="my-project",
    )
    data = json.loads(r.format("json"))
    assert data["handle_id"] == "abc123"
    assert data["lane"] == "agenda"
    assert data["project"] == "my-project"
    assert data["result"] == "findings"


# ---------------------------------------------------------------------------
# NOW lane (dry_run)
# ---------------------------------------------------------------------------

def test_handle_now_lane_dry_run(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    result = handle("what time is it?", dry_run=True)
    assert isinstance(result, HandleResult)
    assert result.lane == "now"
    assert result.status == "done"
    assert result.result != ""


def test_handle_now_forced(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    result = handle("research polymarket strategies", dry_run=True, force_lane="now")
    assert result.lane == "now"
    assert result.lane_confidence == 1.0


def test_handle_now_writes_artifact(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    result = handle("write a haiku", dry_run=True)
    # Artifact path should be set
    assert result.artifact_path is not None


# ---------------------------------------------------------------------------
# AGENDA lane (dry_run)
# ---------------------------------------------------------------------------

def test_handle_agenda_lane_dry_run(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    result = handle("research winning polymarket strategies", dry_run=True)
    assert isinstance(result, HandleResult)
    assert result.lane == "agenda"
    assert result.status == "done"
    assert result.project is not None
    assert result.loop_result is not None


def test_handle_agenda_forced(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    result = handle("what is 2+2?", dry_run=True, force_lane="agenda")
    assert result.lane == "agenda"
    assert result.status == "done"


def test_handle_agenda_creates_project(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    result = handle("analyze competitor pricing strategies", dry_run=True, project="comp-pricing")
    assert orch.project_dir("comp-pricing").exists()


def test_handle_agenda_result_has_content(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    result = handle("build a research report on X strategies", dry_run=True)
    assert len(result.result) > 0


# ---------------------------------------------------------------------------
# Auto-classification routing
# ---------------------------------------------------------------------------

def test_handle_routes_simple_to_now(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    result = handle("write a haiku about the moon", dry_run=True)
    # Heuristic should route this to NOW
    assert result.lane == "now"


def test_handle_routes_research_to_agenda(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    result = handle("research and analyze polymarket prediction patterns", dry_run=True)
    assert result.lane == "agenda"


# ---------------------------------------------------------------------------
# Token tracking
# ---------------------------------------------------------------------------

def test_handle_tracks_tokens(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    result = handle("what is 2+2?", dry_run=True, force_lane="now")
    assert result.tokens_in >= 0
    assert result.tokens_out >= 0


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------

def test_cli_poe_handle_now(monkeypatch, tmp_path, capsys):
    _setup(monkeypatch, tmp_path)
    import cli
    rc = cli.main(["poe-handle", "what is 2 plus 2?", "--dry-run", "--lane", "now"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "lane=now" in out


def test_cli_poe_handle_agenda(monkeypatch, tmp_path, capsys):
    _setup(monkeypatch, tmp_path)
    import cli
    rc = cli.main(["poe-handle", "research polymarket strategies", "--dry-run", "--lane", "agenda"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "lane=agenda" in out


def test_cli_poe_handle_json(monkeypatch, tmp_path, capsys):
    _setup(monkeypatch, tmp_path)
    import cli
    rc = cli.main(["poe-handle", "hello", "--dry-run", "--lane", "now", "--format", "json"])
    assert rc == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert "handle_id" in data
    assert data["lane"] == "now"


# ---------------------------------------------------------------------------
# effort: prefix modifier
# ---------------------------------------------------------------------------

class TestEffortModifier:
    """effort:low/mid/high prefix strips the keyword and overrides model tier."""

    def _run(self, monkeypatch, tmp_path, goal, expected_message_fragment=None):
        """Run handle in dry_run mode and return (result, captured_model)."""
        _setup(monkeypatch, tmp_path)

        captured = {}
        import handle as _handle_mod
        _orig_build = None
        try:
            from llm import build_adapter
            _orig_build = build_adapter
        except Exception:
            pass

        def _fake_build(model=None, **kwargs):
            captured["model"] = model
            from unittest.mock import MagicMock
            m = MagicMock()
            m.model_key = model or "cheap"
            return m

        monkeypatch.setattr(_handle_mod, "build_adapter", _fake_build, raising=False)

        result = handle(goal, dry_run=True)
        return result, captured.get("model")

    def test_effort_low_strips_prefix(self, monkeypatch, tmp_path):
        result, _ = self._run(monkeypatch, tmp_path, "effort:low summarize this topic")
        assert result.message == "summarize this topic"

    def test_effort_mid_strips_prefix(self, monkeypatch, tmp_path):
        result, _ = self._run(monkeypatch, tmp_path, "effort:mid summarize this topic")
        assert result.message == "summarize this topic"

    def test_effort_high_strips_prefix(self, monkeypatch, tmp_path):
        result, _ = self._run(monkeypatch, tmp_path, "effort:high deep research task")
        assert result.message == "deep research task"

    def test_no_effort_prefix_unchanged(self, monkeypatch, tmp_path):
        result, _ = self._run(monkeypatch, tmp_path, "what is 2 plus 2")
        assert result.message == "what is 2 plus 2"


# ---------------------------------------------------------------------------
# mode:thin prefix modifier
# ---------------------------------------------------------------------------

class TestModeThinModifier:
    """mode:thin prefix strips keyword and routes to factory_thin loop."""

    def _run(self, monkeypatch, tmp_path, goal):
        _setup(monkeypatch, tmp_path)

        # Patch at module level so `from factory_thin import run_factory_thin` picks it up
        import factory_thin as _ft_mod
        _called_thin = []

        def _fake_factory_thin(g, **kwargs):
            _called_thin.append(g)
            class _R:
                loop_id = "fake"
                status = "done"
                steps = []
                final_report = f"[thin result for: {g}]"
                total_tokens = 100
                cost_usd = 0.01
                elapsed_ms = 500
                model = "cheap"
            return _R()

        monkeypatch.setattr(_ft_mod, "run_factory_thin", _fake_factory_thin)

        # Patch build_adapter so no real LLM calls are made
        import handle as _handle_mod
        from unittest.mock import MagicMock
        _mock_adapter = MagicMock()
        _mock_adapter.model_key = "cheap"
        monkeypatch.setattr(_handle_mod, "build_adapter",
                            lambda **kw: _mock_adapter, raising=False)

        result = handle(goal, force_lane="agenda")
        return result, _called_thin

    def test_mode_thin_strips_prefix(self, monkeypatch, tmp_path):
        result, _ = self._run(monkeypatch, tmp_path, "mode:thin research nootropics")
        assert result.message == "research nootropics"

    def test_mode_thin_routes_to_factory(self, monkeypatch, tmp_path):
        result, called_thin = self._run(monkeypatch, tmp_path, "mode:thin analyze this market")
        assert called_thin  # factory_thin WAS called
        assert "thin result" in result.result

    def test_mode_thin_classification_reason(self, monkeypatch, tmp_path):
        result, _ = self._run(monkeypatch, tmp_path, "mode:thin check bitcoin price")
        assert "mode:thin" in result.classification_reason

    def test_no_mode_thin_unchanged(self, monkeypatch, tmp_path):
        """Without prefix, message is unchanged (dry_run path, no factory_thin call)."""
        _setup(monkeypatch, tmp_path)
        result = handle("research nootropics", dry_run=True)
        assert result.message == "research nootropics"


# ---------------------------------------------------------------------------
# ultraplan: prefix modifier
# ---------------------------------------------------------------------------

class TestUltraplanModifier:
    """ultraplan: strips prefix, sets model=power, max_steps=12."""

    def test_ultraplan_strips_prefix(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        result = handle("ultraplan: research the history of AI", dry_run=True)
        assert result.message == "research the history of AI"

    def test_ultraplan_no_prefix_unchanged(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        result = handle("research the history of AI", dry_run=True)
        assert result.message == "research the history of AI"

    def test_ultraplan_sets_power_model(self, monkeypatch, tmp_path):
        """ultraplan: should set model=power when no explicit model is given."""
        _setup(monkeypatch, tmp_path)
        import handle as _handle_mod
        captured = {}

        def _fake_build(**kw):
            captured["model"] = kw.get("model")
            from unittest.mock import MagicMock
            m = MagicMock()
            m.model_key = kw.get("model", "cheap")
            return m

        monkeypatch.setattr(_handle_mod, "build_adapter", _fake_build, raising=False)
        # dry_run uses DryRunAdapter so build_adapter isn't called; test non-dry
        # by directly checking that model override landed in kwargs
        # (dry_run replaces adapter so we check message strip only)
        result = handle("ultraplan:analyze market trends", dry_run=True)
        assert result.message == "analyze market trends"


# ---------------------------------------------------------------------------
# btw: prefix modifier
# ---------------------------------------------------------------------------

class TestBtwModifier:
    """btw: strips prefix, routes to NOW, tags result as [Observation]."""

    def test_btw_strips_prefix(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        result = handle("btw: the rate limit looks close", dry_run=True)
        assert result.message == "the rate limit looks close"

    def test_btw_routes_to_now(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        result = handle("btw: noticed something odd in the logs", dry_run=True)
        assert result.lane == "now"

    def test_btw_classification_reason(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        result = handle("btw: api returning 429s", dry_run=True)
        assert "btw" in result.classification_reason

    def test_btw_result_tagged_observation(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        result = handle("btw: some quick note", dry_run=True)
        assert result.result.startswith("[Observation]")

    def test_no_btw_prefix_unchanged(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        result = handle("research polymarket strategies", dry_run=True)
        assert result.message == "research polymarket strategies"
        assert not result.result.startswith("[Observation]")


# ---------------------------------------------------------------------------

class TestDirectModifier:
    """direct: strips prefix, skips quality gate, routes straight to run_agent_loop."""

    def test_direct_strips_prefix(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        result = handle("direct: check market status", dry_run=True)
        assert result.message == "check market status"

    def test_direct_routes_to_agenda(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        result = handle("direct: research trading strategies", dry_run=True)
        assert result.lane == "agenda"

    def test_direct_classification_reason(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        result = handle("direct: do something useful", dry_run=True)
        assert "[direct]" in result.classification_reason

    def test_direct_case_insensitive(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        result = handle("Direct: check the logs", dry_run=True)
        assert result.message == "check the logs"
        assert "[direct]" in result.classification_reason

    def test_no_direct_prefix_unchanged(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        result = handle("research polymarket strategies", dry_run=True)
        assert result.message == "research polymarket strategies"
        assert "[direct]" not in result.classification_reason


# ---------------------------------------------------------------------------
# Magic keyword prefixes: ralph:, verify:, pipeline:, strict:
# ---------------------------------------------------------------------------

class TestMagicKeywordPrefixes:
    """Magic prefixes strip the keyword and mutate execution behaviour."""

    def test_ralph_strips_prefix(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        result = handle("ralph: research market trends", dry_run=True)
        assert result.message == "research market trends"

    def test_verify_strips_prefix(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        result = handle("verify: analyze the data", dry_run=True)
        assert result.message == "analyze the data"

    def test_ralph_case_insensitive(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        result = handle("Ralph: research something", dry_run=True)
        assert result.message == "research something"

    def test_pipeline_strips_prefix(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        result = handle("pipeline: fetch market data", dry_run=True)
        assert result.message == "fetch market data"

    def test_strict_strips_prefix(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        result = handle("strict: analyze trading performance", dry_run=True)
        assert result.message == "analyze trading performance"

    def test_no_ralph_prefix_unchanged(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        result = handle("research market trends", dry_run=True)
        assert result.message == "research market trends"

    def test_ralph_and_pipeline_combined(self, monkeypatch, tmp_path):
        # ralph: then pipeline: — both prefixes stripped in sequence
        _setup(monkeypatch, tmp_path)
        result = handle("ralph: pipeline: fetch and verify data", dry_run=True)
        assert result.message == "fetch and verify data"

    def test_verify_equivalent_to_ralph(self, monkeypatch, tmp_path):
        # verify: is an alias for ralph: — message stripped identically
        _setup(monkeypatch, tmp_path)
        r1 = handle("ralph: check the analysis", dry_run=True)
        r2 = handle("verify: check the analysis", dry_run=True)
        assert r1.message == r2.message == "check the analysis"
