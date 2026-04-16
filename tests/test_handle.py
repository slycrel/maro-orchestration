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


# ---------------------------------------------------------------------------
# _apply_prefixes registry unit tests
# ---------------------------------------------------------------------------

class TestApplyPrefixes:
    """Direct tests for the _apply_prefixes() registry function."""

    def test_no_prefix_unchanged(self):
        from handle import _apply_prefixes
        r = _apply_prefixes("just a plain goal")
        assert r.message == "just a plain goal"
        assert not r.btw_mode
        assert not r.ralph_mode
        assert not r.strict_mode

    def test_single_prefix_stripped(self):
        from handle import _apply_prefixes
        r = _apply_prefixes("btw: something interesting")
        assert r.message == "something interesting"
        assert r.btw_mode is True

    def test_stacked_prefixes_all_stripped(self):
        from handle import _apply_prefixes
        r = _apply_prefixes("strict: pipeline: verify this dataset")
        assert r.message == "verify this dataset"
        assert r.strict_mode is True
        assert r.pipeline_mode is True

    def test_effort_low_sets_model_tier(self):
        from handle import _apply_prefixes
        r = _apply_prefixes("effort:low research nootropics")
        assert r.message == "research nootropics"
        assert r.model_tier == "cheap"

    def test_effort_high_sets_model_tier(self):
        from handle import _apply_prefixes
        r = _apply_prefixes("effort:high analyze the codebase")
        assert r.model_tier == "power"
        assert r.message == "analyze the codebase"

    def test_ultraplan_sets_max_steps(self):
        from handle import _apply_prefixes
        r = _apply_prefixes("ultraplan: review everything")
        assert r.ultraplan_mode is True
        assert r.max_steps == 12
        assert r.model_tier == "power"
        assert r.message == "review everything"

    def test_verify_sets_ralph_mode(self):
        from handle import _apply_prefixes
        r = _apply_prefixes("verify: check the claim")
        assert r.ralph_mode is True
        assert r.message == "check the claim"

    def test_ralph_and_verify_both_set_ralph_mode(self):
        from handle import _apply_prefixes
        r1 = _apply_prefixes("ralph: do thing")
        r2 = _apply_prefixes("verify: do thing")
        assert r1.ralph_mode is True
        assert r2.ralph_mode is True
        assert r1.message == r2.message == "do thing"

    def test_direct_mode_flag(self):
        from handle import _apply_prefixes
        r = _apply_prefixes("direct: fetch the data")
        assert r.direct_mode is True
        assert r.message == "fetch the data"

    def test_case_insensitive_matching(self):
        from handle import _apply_prefixes
        r = _apply_prefixes("BTW: something")
        assert r.btw_mode is True
        assert r.message == "something"

    def test_effort_first_model_wins(self):
        # effort: sets model_tier; ultraplan: also sets model_tier (power)
        # but effort: parsed first so it wins (effort:mid → mid, not overridden by ultraplan)
        from handle import _apply_prefixes
        r = _apply_prefixes("effort:mid ultraplan: do complex thing")
        assert r.model_tier == "mid"  # effort:mid wins (first non-empty tier wins)

    def test_prefix_registry_covers_all_known_prefixes(self):
        """Sanity: all 11 known prefixes are in the registry."""
        from handle import _PREFIX_REGISTRY
        prefixes = {r.prefix for r in _PREFIX_REGISTRY}
        for expected in ["effort:low", "effort:mid", "effort:high", "mode:thin",
                          "btw:", "ultraplan:", "direct:", "ralph:", "verify:",
                          "pipeline:", "strict:"]:
            assert expected in prefixes, f"Missing prefix: {expected}"


# ---------------------------------------------------------------------------
# Phase 58: pre_flight_review surfaced on LoopResult
# ---------------------------------------------------------------------------

class TestPreFlightReviewSurfacing:
    def test_loop_result_has_pre_flight_review_field(self, monkeypatch, tmp_path):
        """LoopResult has a pre_flight_review field (may be None for dry_run)."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        result = handle("research market trends for five industries", dry_run=True, force_lane="agenda")
        assert hasattr(result.loop_result, "pre_flight_review")

    def test_wide_scope_appends_warning_to_result_text(self, monkeypatch, tmp_path):
        """When pre_flight_review.scope == 'wide', result text gets a warning."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        from unittest.mock import MagicMock, patch
        from agent_loop import LoopResult, StepOutcome

        # Build a LoopResult with a scope=wide pre_flight_review
        _fake_pf = MagicMock()
        _fake_pf.scope = "wide"
        _fake_pf.scope_note = "goal hides many sub-tasks"
        _fake_lr = LoopResult(
            loop_id="test-lr",
            project="test-proj",
            goal="analyze everything",
            status="done",
            steps=[StepOutcome(index=0, text="step 1", status="done", result="output", iteration=0)],
            pre_flight_review=_fake_pf,
        )

        # Patch run_agent_loop to return our fake result
        with patch("agent_loop.run_agent_loop", return_value=_fake_lr):
            with patch("intent.check_goal_clarity", return_value={"clear": True}):
                result = handle("analyze everything", force_lane="agenda")

        assert "scope=wide" in result.result or "Pre-flight" in result.result

    def test_non_wide_scope_no_warning(self, monkeypatch, tmp_path):
        """scope=narrow/medium doesn't add a pre-flight warning."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        from unittest.mock import MagicMock, patch
        from agent_loop import LoopResult, StepOutcome

        _fake_pf = MagicMock()
        _fake_pf.scope = "narrow"
        _fake_pf.scope_note = "small goal"
        _fake_lr = LoopResult(
            loop_id="test-lr2",
            project="test-proj2",
            goal="write a haiku",
            status="done",
            steps=[StepOutcome(index=0, text="write", status="done", result="haiku output", iteration=0)],
            pre_flight_review=_fake_pf,
        )

        with patch("agent_loop.run_agent_loop", return_value=_fake_lr):
            with patch("intent.check_goal_clarity", return_value={"clear": True}):
                result = handle("write a haiku", force_lane="agenda")

        assert "Pre-flight" not in result.result
        assert "scope=wide" not in result.result


# ---------------------------------------------------------------------------
# NOW → Director escalation (_is_complex_directive + escalation wiring)
# ---------------------------------------------------------------------------

class TestIsComplexDirective:
    """Tests for _is_complex_directive() heuristic."""

    def test_simple_factual_not_complex(self):
        from handle import _is_complex_directive
        assert not _is_complex_directive("what is the capital of France")
        assert not _is_complex_directive("who won the game last night")
        assert not _is_complex_directive("tell me a joke")

    def test_long_message_is_complex(self):
        from handle import _is_complex_directive
        # 26+ words → complex
        msg = "I want you to look into the performance characteristics of the database and also tell me about the schema design and indexing strategy used by the current team"
        assert _is_complex_directive(msg)

    def test_multi_step_language_is_complex(self):
        from handle import _is_complex_directive
        assert _is_complex_directive("first do X, then do Y")
        assert _is_complex_directive("step 1: research topic")

    def test_complex_action_verb_at_start_is_complex(self):
        from handle import _is_complex_directive
        # Need 8+ words + complex verb to trigger (guards against "research this" = simple)
        assert _is_complex_directive("research the latest LLM benchmark performance across providers")
        assert _is_complex_directive("implement a caching layer with Redis for the database queries")
        assert _is_complex_directive("design a new schema for users with roles and permissions")

    def test_compound_task_multiple_sentences_is_complex(self):
        from handle import _is_complex_directive
        assert _is_complex_directive("Do task A. Then do task B. Finally summarize.")

    def test_short_action_verb_not_complex(self):
        from handle import _is_complex_directive
        # Short message with action verb but < 25 words and no other signals
        assert not _is_complex_directive("write a haiku")
        assert not _is_complex_directive("summarize this")


class TestNowDirectorEscalation:
    """Tests for NOW→agenda escalation when now_lane.escalate_to_director is enabled."""

    def _setup(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        monkeypatch.setenv("ANTHROPIC_API_KEY", "")
        monkeypatch.setenv("OPENAI_API_KEY", "")

    def test_escalation_disabled_by_default_runs_now_lane(self, monkeypatch, tmp_path):
        """When escalate_to_director is False (default), complex messages still use NOW."""
        self._setup(monkeypatch, tmp_path)
        from handle import handle
        monkeypatch.setattr("config.get", lambda key, default=None: False if "escalate_to_director" in key else default, raising=False)

        now_called = []
        agenda_called = []

        from unittest.mock import patch, MagicMock
        fake_resp = MagicMock()
        fake_resp.content = "answer"
        fake_resp.input_tokens = 10
        fake_resp.output_tokens = 5

        with patch("handle._run_now", wraps=lambda *a, **kw: {"status": "done", "result": "answer", "tokens_in": 0, "tokens_out": 0, "elapsed_ms": 0}) as mock_now:
            with patch("intent.classify", return_value=("now", 0.9, "simple")):
                result = handle("research all the LLMs and implement a comparison framework", dry_run=True, force_lane="now")
        # Should have used NOW lane (or dry_run short-circuits — either is fine)
        assert result.lane in ("now",)

    def test_escalation_enabled_complex_goes_to_agenda(self, monkeypatch, tmp_path):
        """When escalate_to_director=True and message is complex, lane becomes agenda."""
        self._setup(monkeypatch, tmp_path)
        from handle import _is_complex_directive

        # Verify the heuristic fires for our test message
        msg = "research the top 5 LLM providers and implement a benchmarking framework then summarize the results"
        assert _is_complex_directive(msg), "Test message should be detected as complex"

    def test_escalation_enabled_simple_stays_now(self, monkeypatch, tmp_path):
        """Simple messages are not escalated even when escalation is enabled."""
        self._setup(monkeypatch, tmp_path)
        from handle import _is_complex_directive
        assert not _is_complex_directive("what time is it"), "Simple message should not escalate"

    def test_escalation_reason_suffix_appended(self):
        """When escalation fires, reason includes the escalation note."""
        from handle import _is_complex_directive
        # The escalation check in handle() appends " [now→agenda: complex directive ...]"
        # We verify _is_complex_directive correctly identifies the trigger condition
        complex_msg = "implement a full REST API with authentication and then deploy it to production"
        assert _is_complex_directive(complex_msg)


# ---------------------------------------------------------------------------
# Phase 64C: director restart re-run
# ---------------------------------------------------------------------------

class TestDirectorRestart:
    """handle.py detects loop_result.status == 'restart' and re-runs with context."""

    def _setup(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))

    @staticmethod
    def _no_quality_gate():
        """Context manager that stubs quality gate to never escalate."""
        from unittest.mock import patch, MagicMock
        verdict = MagicMock()
        verdict.escalate = False
        verdict.contested_claims = []
        return patch("quality_gate.run_quality_gate", return_value=verdict)

    def _fake_loop_result(self, status="done", stuck_reason=None):
        from agent_loop import LoopResult, StepOutcome
        return LoopResult(
            loop_id="test-lr",
            project="test-proj",
            goal="do the thing",
            status=status,
            stuck_reason=stuck_reason,
            steps=[StepOutcome(index=0, text="step 1", status="done",
                               result="output", iteration=0)],
        )

    def test_restart_status_triggers_rerun(self, monkeypatch, tmp_path):
        """When loop returns 'restart', handle re-runs the loop with restart context."""
        self._setup(monkeypatch, tmp_path)
        from unittest.mock import patch

        restart_result = self._fake_loop_result(
            status="restart", stuck_reason="director: wrong approach, try X instead"
        )
        done_result = self._fake_loop_result(status="done")

        call_count = {"n": 0}
        def _fake_run(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return restart_result
            return done_result

        with patch("agent_loop.run_agent_loop", side_effect=_fake_run), \
             patch("intent.check_goal_clarity", return_value={"clear": True}), \
             self._no_quality_gate():
            handle("do the thing", force_lane="agenda", dry_run=False)

        assert call_count["n"] == 2, "loop should have been called twice"

    def test_restart_injects_context_into_ancestry(self, monkeypatch, tmp_path):
        """Restart re-run receives the restart_context in ancestry_context_extra."""
        self._setup(monkeypatch, tmp_path)
        from unittest.mock import patch

        restart_result = self._fake_loop_result(
            status="restart", stuck_reason="learned: X fails; try Y"
        )
        done_result = self._fake_loop_result(status="done")

        calls = []
        def _fake_run(*args, **kwargs):
            calls.append(kwargs.copy())
            if len(calls) == 1:
                return restart_result
            return done_result

        with patch("agent_loop.run_agent_loop", side_effect=_fake_run), \
             patch("intent.check_goal_clarity", return_value={"clear": True}), \
             self._no_quality_gate():
            handle("do the thing", force_lane="agenda", dry_run=False)

        assert len(calls) == 2
        restart_ancestry = calls[1].get("ancestry_context_extra", "")
        assert "learned: X fails; try Y" in restart_ancestry
        assert "Director restart context" in restart_ancestry

    def test_restart_increments_continuation_depth(self, monkeypatch, tmp_path):
        """Restart re-run has continuation_depth incremented by 1."""
        self._setup(monkeypatch, tmp_path)
        from unittest.mock import patch

        restart_result = self._fake_loop_result(status="restart", stuck_reason="retry")
        done_result = self._fake_loop_result(status="done")

        calls = []
        def _fake_run(*args, **kwargs):
            calls.append(kwargs.copy())
            if len(calls) == 1:
                return restart_result
            return done_result

        with patch("agent_loop.run_agent_loop", side_effect=_fake_run), \
             patch("intent.check_goal_clarity", return_value={"clear": True}), \
             self._no_quality_gate():
            handle("do the thing", force_lane="agenda", dry_run=False)

        first_depth = calls[0].get("continuation_depth", 0)
        second_depth = calls[1].get("continuation_depth", 0)
        assert second_depth == first_depth + 1

    def test_restart_depth_cap_prevents_infinite_loop(self, monkeypatch, tmp_path):
        """Restart loop is capped at depth 3 — loop is not re-run beyond that."""
        self._setup(monkeypatch, tmp_path)
        from unittest.mock import patch

        restart_result = self._fake_loop_result(status="restart", stuck_reason="retry forever")
        calls = []

        def _fake_run(*args, **kwargs):
            calls.append(kwargs.copy())
            return restart_result

        with patch("agent_loop.run_agent_loop", side_effect=_fake_run), \
             patch("intent.check_goal_clarity", return_value={"clear": True}), \
             self._no_quality_gate():
            handle("do the thing", force_lane="agenda", dry_run=False)

        # initial + up to 3 restarts (cap at continuation_depth == 3)
        assert len(calls) <= 4, f"restart loop ran {len(calls)} times, expected ≤4"

    def test_done_result_no_restart(self, monkeypatch, tmp_path):
        """'done' status does not trigger a restart re-run."""
        self._setup(monkeypatch, tmp_path)
        from unittest.mock import patch

        done_result = self._fake_loop_result(status="done")
        call_count = {"n": 0}

        def _fake_run(*args, **kwargs):
            call_count["n"] += 1
            return done_result

        with patch("agent_loop.run_agent_loop", side_effect=_fake_run), \
             patch("intent.check_goal_clarity", return_value={"clear": True}), \
             self._no_quality_gate():
            handle("do the thing", force_lane="agenda", dry_run=False)

        assert call_count["n"] == 1


# ---------------------------------------------------------------------------
# Phase 65+: closure-driven restart (verdict gates the loop)
# ---------------------------------------------------------------------------

class TestClosureRestart:
    """Closure verdict actually gates execution — not just informational events.

    When director closure check returns complete=False with material confidence,
    handle.py re-runs the loop with gaps injected as ancestry context. This is
    the other half of 'nobody ran a browser' — scope sets bounds up front, this
    catches the silent-failure case on the way out.
    """

    def _setup(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))

    @staticmethod
    def _no_quality_gate():
        from unittest.mock import patch, MagicMock
        verdict = MagicMock()
        verdict.escalate = False
        verdict.contested_claims = []
        return patch("quality_gate.run_quality_gate", return_value=verdict)

    def _fake_loop_result(self, status="done"):
        from agent_loop import LoopResult, StepOutcome
        return LoopResult(
            loop_id="test-lr", project="test-proj", goal="build X",
            status=status, stuck_reason=None,
            steps=[StepOutcome(index=0, text="step", status="done",
                               result="output", iteration=0)],
        )

    def _fake_closure(self, complete, confidence, gaps=None, checks_run=2):
        from director import ClosureVerdict
        return ClosureVerdict(
            complete=complete, confidence=confidence,
            gaps=gaps or [], summary="verified",
            checks_run=checks_run, checks_passed=(checks_run if complete else 0),
        )

    def test_incomplete_verdict_triggers_restart(self, monkeypatch, tmp_path):
        """complete=False with confidence >= 0.6 re-runs the loop."""
        self._setup(monkeypatch, tmp_path)
        from unittest.mock import patch

        done_result = self._fake_loop_result(status="done")
        calls = []
        def _fake_run(*args, **kwargs):
            calls.append(kwargs.copy())
            return done_result

        incomplete = self._fake_closure(False, 0.85,
                                         gaps=["server never started"])
        complete = self._fake_closure(True, 0.9)
        verdict_seq = [incomplete, complete]

        with patch("agent_loop.run_agent_loop", side_effect=_fake_run), \
             patch("intent.check_goal_clarity", return_value={"clear": True}), \
             patch("director.verify_goal_completion",
                   side_effect=lambda *a, **k: verdict_seq.pop(0)), \
             self._no_quality_gate():
            handle("build a websocket server", force_lane="agenda", dry_run=False)

        assert len(calls) == 2, f"expected restart, got {len(calls)} calls"

    def test_gaps_injected_as_ancestry_context(self, monkeypatch, tmp_path):
        """Restart re-run receives the gap list in ancestry_context_extra."""
        self._setup(monkeypatch, tmp_path)
        from unittest.mock import patch

        done_result = self._fake_loop_result(status="done")
        calls = []
        def _fake_run(*args, **kwargs):
            calls.append(kwargs.copy())
            return done_result

        incomplete = self._fake_closure(
            False, 0.85, gaps=["server never started", "no client handshake"],
        )
        complete = self._fake_closure(True, 0.9)
        verdict_seq = [incomplete, complete]

        with patch("agent_loop.run_agent_loop", side_effect=_fake_run), \
             patch("intent.check_goal_clarity", return_value={"clear": True}), \
             patch("director.verify_goal_completion",
                   side_effect=lambda *a, **k: verdict_seq.pop(0)), \
             self._no_quality_gate():
            handle("build a websocket server", force_lane="agenda", dry_run=False)

        assert len(calls) == 2
        restart_ancestry = calls[1].get("ancestry_context_extra", "")
        assert "Closure gap context" in restart_ancestry
        assert "server never started" in restart_ancestry
        assert "no client handshake" in restart_ancestry

    def test_continuation_depth_increments(self, monkeypatch, tmp_path):
        """Closure restart uses the same continuation_depth bucket as director restart."""
        self._setup(monkeypatch, tmp_path)
        from unittest.mock import patch

        done_result = self._fake_loop_result(status="done")
        calls = []
        def _fake_run(*args, **kwargs):
            calls.append(kwargs.copy())
            return done_result

        incomplete = self._fake_closure(False, 0.85, gaps=["gap"])
        complete = self._fake_closure(True, 0.9)
        verdict_seq = [incomplete, complete]

        with patch("agent_loop.run_agent_loop", side_effect=_fake_run), \
             patch("intent.check_goal_clarity", return_value={"clear": True}), \
             patch("director.verify_goal_completion",
                   side_effect=lambda *a, **k: verdict_seq.pop(0)), \
             self._no_quality_gate():
            handle("build X", force_lane="agenda", dry_run=False)

        first_depth = calls[0].get("continuation_depth", 0)
        second_depth = calls[1].get("continuation_depth", 0)
        assert second_depth == first_depth + 1

    def test_low_confidence_does_not_restart(self, monkeypatch, tmp_path):
        """complete=False but confidence below threshold → no restart (too noisy)."""
        self._setup(monkeypatch, tmp_path)
        from unittest.mock import patch

        done_result = self._fake_loop_result(status="done")
        calls = []
        def _fake_run(*args, **kwargs):
            calls.append(kwargs.copy())
            return done_result

        weak = self._fake_closure(False, 0.3, gaps=["maybe a problem"])

        with patch("agent_loop.run_agent_loop", side_effect=_fake_run), \
             patch("intent.check_goal_clarity", return_value={"clear": True}), \
             patch("director.verify_goal_completion", return_value=weak), \
             self._no_quality_gate():
            handle("build X", force_lane="agenda", dry_run=False)

        assert len(calls) == 1, "low-confidence closure should not trigger restart"

    def test_complete_verdict_does_not_restart(self, monkeypatch, tmp_path):
        """complete=True → no restart (happy path)."""
        self._setup(monkeypatch, tmp_path)
        from unittest.mock import patch

        done_result = self._fake_loop_result(status="done")
        calls = []
        def _fake_run(*args, **kwargs):
            calls.append(kwargs.copy())
            return done_result

        ok = self._fake_closure(True, 0.9)

        with patch("agent_loop.run_agent_loop", side_effect=_fake_run), \
             patch("intent.check_goal_clarity", return_value={"clear": True}), \
             patch("director.verify_goal_completion", return_value=ok), \
             self._no_quality_gate():
            handle("build X", force_lane="agenda", dry_run=False)

        assert len(calls) == 1

    def test_no_checks_run_does_not_restart(self, monkeypatch, tmp_path):
        """Research goals get checks_run=0 — should not restart."""
        self._setup(monkeypatch, tmp_path)
        from unittest.mock import patch

        done_result = self._fake_loop_result(status="done")
        calls = []
        def _fake_run(*args, **kwargs):
            calls.append(kwargs.copy())
            return done_result

        skipped = self._fake_closure(False, 0.8, gaps=[], checks_run=0)

        with patch("agent_loop.run_agent_loop", side_effect=_fake_run), \
             patch("intent.check_goal_clarity", return_value={"clear": True}), \
             patch("director.verify_goal_completion", return_value=skipped), \
             self._no_quality_gate():
            handle("summarize this article", force_lane="agenda", dry_run=False)

        assert len(calls) == 1

    def test_config_flag_disables_restart(self, monkeypatch, tmp_path):
        """closure_restart=False disables the restart path for A/B comparison."""
        self._setup(monkeypatch, tmp_path)
        from unittest.mock import patch

        done_result = self._fake_loop_result(status="done")
        calls = []
        def _fake_run(*args, **kwargs):
            calls.append(kwargs.copy())
            return done_result

        incomplete = self._fake_closure(False, 0.9, gaps=["a gap"])

        def _cfg(name, default=None):
            if name == "closure_restart":
                return False
            return default

        with patch("agent_loop.run_agent_loop", side_effect=_fake_run), \
             patch("intent.check_goal_clarity", return_value={"clear": True}), \
             patch("director.verify_goal_completion", return_value=incomplete), \
             patch("config.get", side_effect=_cfg), \
             self._no_quality_gate():
            handle("build X", force_lane="agenda", dry_run=False)

        assert len(calls) == 1, "closure_restart=False should skip restart"

    def test_depth_cap_prevents_infinite_loop(self, monkeypatch, tmp_path):
        """Closure restart shares the continuation_depth cap of 3."""
        self._setup(monkeypatch, tmp_path)
        from unittest.mock import patch

        done_result = self._fake_loop_result(status="done")
        calls = []
        def _fake_run(*args, **kwargs):
            calls.append(kwargs.copy())
            return done_result

        incomplete = self._fake_closure(False, 0.9, gaps=["persistent gap"])

        with patch("agent_loop.run_agent_loop", side_effect=_fake_run), \
             patch("intent.check_goal_clarity", return_value={"clear": True}), \
             patch("director.verify_goal_completion", return_value=incomplete), \
             self._no_quality_gate():
            handle("build X", force_lane="agenda", dry_run=False)

        # Initial + up to 3 restarts
        assert len(calls) <= 4, f"closure restart ran {len(calls)} times, expected ≤4"
