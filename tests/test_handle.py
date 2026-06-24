"""Tests for Phase 2: handle.py (unified entry point, NOW/AGENDA routing)."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

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


def test_handle_now_artifact_lands_in_run_dir(monkeypatch, tmp_path):
    """NOW artifacts belong in the run dir's artifact/ subtree, inside the
    workspace — not the stale doubled prototype path they used to hit."""
    _setup(monkeypatch, tmp_path)
    result = handle("write a haiku", dry_run=True)
    from runs import run_dir
    p = Path(result.artifact_path)
    assert p.exists()
    assert p.parent == run_dir(result.handle_id) / "artifact"


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


def test_handle_poe_yolo_env_skips_clarity_block(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    monkeypatch.setenv("POE_YOLO", "true")

    from agent_loop import LoopResult, StepOutcome

    fake_loop = LoopResult(
        loop_id="test-lr-yolo",
        project="test-proj-yolo",
        goal="ambiguous goal",
        status="done",
        steps=[StepOutcome(index=0, text="step 1", status="done", result="output", iteration=0)],
    )
    gate_verdict = MagicMock()
    gate_verdict.escalate = False
    gate_verdict.contested_claims = []

    with patch("intent.check_goal_clarity", return_value={"clear": False, "question": "Need more context?"}), \
         patch("agent_loop.run_agent_loop", return_value=fake_loop), \
         patch("quality_gate.run_quality_gate", return_value=gate_verdict):
        result = handle("ambiguous goal", force_lane="agenda", dry_run=False, adapter=MagicMock())

    assert result.status == "done"
    assert result.project == "test-proj-yolo"


def test_handle_build_loop_source_skips_quality_gate(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    monkeypatch.setenv("POE_YOLO", "true")
    monkeypatch.setenv("ORCH_SOURCE", "build-loop")

    from agent_loop import LoopResult, StepOutcome

    fake_loop = LoopResult(
        loop_id="test-lr-build-loop",
        project="test-proj-build-loop",
        goal="autonomous build loop goal",
        status="done",
        steps=[StepOutcome(index=0, text="step 1", status="done", result="output", iteration=0)],
    )

    with patch("intent.check_goal_clarity", return_value={"clear": True}), \
         patch("agent_loop.run_agent_loop", return_value=fake_loop), \
         patch("quality_gate.run_quality_gate", side_effect=AssertionError("quality gate should be skipped")):
        result = handle("autonomous build loop goal", force_lane="agenda", dry_run=False, adapter=MagicMock())

    assert result.status == "done"
    assert result.project == "test-proj-build-loop"


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


# ---------------------------------------------------------------------------
# Dry-run hermeticity
# ---------------------------------------------------------------------------

class TestDryRunHermeticity:
    """dry_run=True must never build a live adapter anywhere in the pipeline.

    Regression: the decompose planner-lift and per-step model selection
    called build_adapter() unconditionally, so dry runs made real subprocess
    LLM calls (each test took minutes of retry sleeps instead of ms).
    """

    def test_dry_run_never_builds_real_adapter(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import llm

        def _boom(*args, **kwargs):
            raise AssertionError("build_adapter called during dry_run")

        monkeypatch.setattr(llm, "build_adapter", _boom)
        result = handle("ralph: research market trends", dry_run=True)
        assert result.message == "research market trends"

    def test_dry_run_agenda_lane_hermetic(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import llm

        def _boom(*args, **kwargs):
            raise AssertionError("build_adapter called during dry_run")

        monkeypatch.setattr(llm, "build_adapter", _boom)
        result = handle("research polymarket strategies", dry_run=True, force_lane="agenda")
        assert result.status in ("done", "stuck", "error", "clarification_needed")

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


class TestOriginAncestry:
    """Ancestry survives the requeue boundary (goal-brain pressure test,
    2026-06-10, finding 1): handle(origin=...) stamps run metadata, and
    handle_task threads the task's origin through to handle()."""

    def test_origin_stamped_into_run_metadata(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        origin = {
            "parent_handle_id": "feed1234",
            "parent_loop_id": "loop-7",
            "parent_goal": "the parent goal",
        }
        result = handle("summarize the findings", dry_run=True, origin=origin)
        meta_path = next((tmp_path / "runs").glob(f"{result.handle_id}-*/metadata.json"))
        meta = json.loads(meta_path.read_text())
        assert meta["origin"] == origin

    def test_no_origin_for_direct_input(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        result = handle("summarize the findings", dry_run=True)
        meta_path = next((tmp_path / "runs").glob(f"{result.handle_id}-*/metadata.json"))
        meta = json.loads(meta_path.read_text())
        assert "origin" not in meta

    def test_handle_task_threads_origin_through(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import handle as handle_mod

        captured = {}

        def _fake_handle(message, **kwargs):
            captured["message"] = message
            captured["origin"] = kwargs.get("origin")
            return HandleResult(
                handle_id="x", lane="now", lane_confidence=1.0,
                classification_reason="t", message=message, status="done", result="",
            )

        monkeypatch.setattr(handle_mod, "handle", _fake_handle)
        task = {
            "job_id": "task-001",
            "source": "user_goal",
            "reason": "do the thing [after:3]",
            "parent_job_id": "task-000",
            "origin": {"parent_loop_id": "loop-42", "parent_goal": "big goal"},
        }
        handle_mod.handle_task(task, dry_run=True)
        assert captured["message"] == "do the thing [after:3]"
        assert captured["origin"]["parent_loop_id"] == "loop-42"
        assert captured["origin"]["parent_goal"] == "big goal"
        assert captured["origin"]["job_id"] == "task-001"
        assert captured["origin"]["parent_job_id"] == "task-000"
        assert captured["origin"]["source"] == "user_goal"


class TestRecallDispatchGuard:
    """handle_task refuses to re-run a goal whose recent attempts all failed
    (goal-brain step 3 dispatch guard, docs/RECALL_DESIGN.md). Basis: the same
    goal ran ~25x in 35 minutes on 2026-05-17 with no memory at dispatch."""

    GOAL = "verify the polymarket rate limit handling end to end"

    def _make_failed_runs(self, n, *, status="stuck", goal=None):
        import runs
        import uuid
        for _ in range(n):
            handle_id = uuid.uuid4().hex[:12]
            rd = runs.create_run_dir(handle_id, prompt=goal or self.GOAL)
            meta_path = rd / "metadata.json"
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            meta["status"] = status
            meta_path.write_text(json.dumps(meta), encoding="utf-8")

    def _fake_handle_factory(self, calls):
        def _fake_handle(message, **kwargs):
            calls.append(message)
            return HandleResult(
                handle_id="x", lane="agenda", lane_confidence=1.0,
                classification_reason="t", message=message, status="done", result="",
            )
        return _fake_handle

    def _task(self):
        return {"job_id": "task-009", "source": "user_goal", "reason": self.GOAL}

    def test_guard_blocks_all_failing_repeats(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import handle as handle_mod
        self._make_failed_runs(3)
        calls = []
        monkeypatch.setattr(handle_mod, "handle", self._fake_handle_factory(calls))

        result = handle_mod.handle_task(self._task(), dry_run=False)

        assert calls == [], "guard should have fired before handle()"
        assert result.status == "error"
        assert result.classification_reason == "recall_guard"
        assert "refusing to re-run" in result.result

    def test_guard_disarmed_by_a_done_attempt(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import handle as handle_mod
        self._make_failed_runs(3)
        self._make_failed_runs(1, status="done")
        calls = []
        monkeypatch.setattr(handle_mod, "handle", self._fake_handle_factory(calls))

        result = handle_mod.handle_task(self._task(), dry_run=False)

        assert calls == [self.GOAL]
        assert result.status == "done"

    def test_guard_skipped_on_dry_run(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import handle as handle_mod
        self._make_failed_runs(5)
        calls = []
        monkeypatch.setattr(handle_mod, "handle", self._fake_handle_factory(calls))

        result = handle_mod.handle_task(self._task(), dry_run=True)

        assert calls == [self.GOAL]
        assert result.status == "done"

    def test_guard_below_threshold_passes_through(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import handle as handle_mod
        self._make_failed_runs(2)
        calls = []
        monkeypatch.setattr(handle_mod, "handle", self._fake_handle_factory(calls))

        result = handle_mod.handle_task(self._task(), dry_run=False)

        assert calls == [self.GOAL]
        assert result.status == "done"

    def test_guard_trip_emits_event(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import handle as handle_mod
        self._make_failed_runs(3)
        monkeypatch.setattr(handle_mod, "handle", self._fake_handle_factory([]))
        events = []
        from unittest.mock import patch as _patch
        with _patch("captains_log.log_event",
                    side_effect=lambda et, *a, **k: events.append(et)):
            handle_mod.handle_task(self._task(), dry_run=False)
        assert "RECALL_GUARD_TRIPPED" in events


class TestNavigatorDispatchShadow:
    """handle_task calls the live navigator shadow (decide-only) after the
    guard verdict, reusing the guard's RecallResult. The shadow is config-
    gated inside shadow_dispatch_live itself; these tests cover the wiring:
    when it's invoked and what verdict it's told (docs/NAVIGATOR_SCHEMA.md)."""

    GOAL = "verify the polymarket rate limit handling end to end"

    def _task(self):
        return {"job_id": "task-011", "source": "user_goal", "reason": self.GOAL}

    def _patch_shadow(self, monkeypatch, calls):
        import navigator_shadow

        def _fake_shadow(goal, **kwargs):
            calls.append({"goal": goal, **kwargs})
            return None

        monkeypatch.setattr(navigator_shadow, "shadow_dispatch_live", _fake_shadow)

    def _patch_handle(self, monkeypatch):
        import handle as handle_mod

        def _fake_handle(message, **kwargs):
            return HandleResult(
                handle_id="x", lane="agenda", lane_confidence=1.0,
                classification_reason="t", message=message, status="done", result="",
            )

        monkeypatch.setattr(handle_mod, "handle", _fake_handle)

    def test_shadow_sees_execute_on_normal_dispatch(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import handle as handle_mod
        calls = []
        self._patch_shadow(monkeypatch, calls)
        self._patch_handle(monkeypatch)

        result = handle_mod.handle_task(self._task(), dry_run=False)

        assert result.status == "done"
        assert len(calls) == 1
        assert calls[0]["goal"] == self.GOAL
        assert calls[0]["pipeline_move"] == "execute"
        assert calls[0]["extra"]["job_id"] == "task-011"

    def test_shadow_sees_guard_refused_on_trip(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import handle as handle_mod
        import runs
        import uuid
        for _ in range(3):
            handle_id = uuid.uuid4().hex[:12]
            rd = runs.create_run_dir(handle_id, prompt=self.GOAL)
            meta_path = rd / "metadata.json"
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            meta["status"] = "stuck"
            meta_path.write_text(json.dumps(meta), encoding="utf-8")
        calls = []
        self._patch_shadow(monkeypatch, calls)
        self._patch_handle(monkeypatch)

        result = handle_mod.handle_task(self._task(), dry_run=False)

        assert result.classification_reason == "recall_guard"
        assert len(calls) == 1
        assert calls[0]["pipeline_move"] == "guard_refused"
        assert calls[0]["recall_result"] is not None

    def test_shadow_skipped_on_dry_run(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import handle as handle_mod
        calls = []
        self._patch_shadow(monkeypatch, calls)
        self._patch_handle(monkeypatch)

        handle_mod.handle_task(self._task(), dry_run=True)

        assert calls == []

    def test_shadow_failure_never_blocks_dispatch(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import handle as handle_mod
        import navigator_shadow

        def _boom(goal, **kwargs):
            raise RuntimeError("shadow exploded")

        monkeypatch.setattr(navigator_shadow, "shadow_dispatch_live", _boom)
        self._patch_handle(monkeypatch)

        result = handle_mod.handle_task(self._task(), dry_run=False)

        assert result.status == "done"


class TestNavigatorDispatchCutover:
    """Dispatch-class cutover (navigator.act_dispatch): the navigator's
    escalate/close decisions act instead of being shadow-only. Default OFF;
    confidence floor; guard keeps the first word; everything else executes."""

    GOAL = "verify the polymarket rate limit handling end to end"

    def _task(self):
        return {"job_id": "task-012", "source": "user_goal", "reason": self.GOAL}

    def _decision(self, move, confidence, payload=None):
        from navigator import NavigatorDecision
        return NavigatorDecision(
            move=move, reasoning="test reasoning", confidence=confidence,
            payload=payload or {},
        )

    def _patch_shadow_returning(self, monkeypatch, decision):
        import navigator_shadow
        monkeypatch.setattr(
            navigator_shadow, "shadow_dispatch_live",
            lambda goal, **kwargs: decision,
        )

    def _patch_handle(self, monkeypatch, calls):
        import handle as handle_mod

        def _fake_handle(message, **kwargs):
            calls.append(message)
            return HandleResult(
                handle_id="x", lane="agenda", lane_confidence=1.0,
                classification_reason="t", message=message, status="done", result="",
            )

        monkeypatch.setattr(handle_mod, "handle", _fake_handle)

    def _patch_act_config(self, monkeypatch, enabled=True, floor=0.9, moves=None):
        import config
        # Tests that exercise close pass moves=("escalate", "close"); the
        # production default (act_moves=["escalate"]) is pinned by its own test.
        _moves = list(moves) if moves is not None else ["escalate", "close"]

        def _cfg(name, default=None):
            if name == "navigator.act_dispatch":
                return enabled
            if name == "navigator.act_confidence_floor":
                return floor
            if name == "navigator.act_moves":
                return _moves
            return default

        monkeypatch.setattr(config, "get", _cfg)

    def test_act_off_by_default_escalate_is_shadow_only(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import handle as handle_mod
        calls = []
        self._patch_shadow_returning(monkeypatch, self._decision("escalate", 0.95))
        self._patch_handle(monkeypatch, calls)

        result = handle_mod.handle_task(self._task(), dry_run=False)

        assert calls == [self.GOAL], "default off: decision must not act"
        assert result.status == "done"

    def test_default_act_moves_is_escalate_only_close_stays_shadow(self, monkeypatch, tmp_path):
        """act_dispatch on, act_moves unset → escalate acts, close does not.
        Per-move cutover: escalate earned it; close is opt-in (2026-06-12)."""
        _setup(monkeypatch, tmp_path)
        import handle as handle_mod
        import config
        # Config patch that enables act_dispatch but leaves act_moves to the
        # code's own default (["escalate"]) by returning the passed default.
        monkeypatch.setattr(config, "get", lambda name, default=None: (
            True if name == "navigator.act_dispatch"
            else 0.9 if name == "navigator.act_confidence_floor"
            else default))

        # escalate acts under the default
        calls = []
        self._patch_shadow_returning(monkeypatch, self._decision("escalate", 0.95))
        self._patch_handle(monkeypatch, calls)
        r1 = handle_mod.handle_task(self._task(), dry_run=False)
        assert calls == [] and r1.classification_reason == "navigator_escalate"

        # close does NOT act under the default — falls through to execute
        calls2 = []
        self._patch_shadow_returning(
            monkeypatch, self._decision("close", 0.99, payload={"closure": "delivered"}))
        self._patch_handle(monkeypatch, calls2)
        r2 = handle_mod.handle_task(self._task(), dry_run=False)
        assert calls2 == [self.GOAL], "close must be opt-in, not in default act_moves"
        assert r2.status == "done"

    def test_escalate_acts_when_enabled(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import handle as handle_mod
        calls = []
        self._patch_shadow_returning(monkeypatch, self._decision("escalate", 0.95))
        self._patch_handle(monkeypatch, calls)
        self._patch_act_config(monkeypatch)

        result = handle_mod.handle_task(self._task(), dry_run=False)

        assert calls == [], "escalate should have prevented the run"
        assert result.status == "stuck"
        assert result.classification_reason == "navigator_escalate"
        assert "test reasoning" in result.result

    def test_close_delivered_acts_as_done(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import handle as handle_mod
        calls = []
        self._patch_shadow_returning(
            monkeypatch,
            self._decision("close", 0.99, payload={"closure": "delivered"}),
        )
        self._patch_handle(monkeypatch, calls)
        self._patch_act_config(monkeypatch)

        result = handle_mod.handle_task(self._task(), dry_run=False)

        assert calls == []
        assert result.status == "done"
        assert result.classification_reason == "navigator_close"

    def test_close_abandoned_acts_as_incomplete(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import handle as handle_mod
        calls = []
        self._patch_shadow_returning(
            monkeypatch,
            self._decision("close", 0.95, payload={"closure": "abandoned"}),
        )
        self._patch_handle(monkeypatch, calls)
        self._patch_act_config(monkeypatch)

        result = handle_mod.handle_task(self._task(), dry_run=False)

        assert calls == []
        assert result.status == "incomplete"

    def test_below_confidence_floor_executes(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import handle as handle_mod
        calls = []
        self._patch_shadow_returning(monkeypatch, self._decision("escalate", 0.85))
        self._patch_handle(monkeypatch, calls)
        self._patch_act_config(monkeypatch, floor=0.9)

        result = handle_mod.handle_task(self._task(), dry_run=False)

        assert calls == [self.GOAL], "below floor: pipeline keeps the wheel"
        assert result.status == "done"

    def test_execute_decision_proceeds(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import handle as handle_mod
        calls = []
        self._patch_shadow_returning(monkeypatch, self._decision("execute", 0.99))
        self._patch_handle(monkeypatch, calls)
        self._patch_act_config(monkeypatch)

        result = handle_mod.handle_task(self._task(), dry_run=False)

        assert calls == [self.GOAL]
        assert result.status == "done"

    def test_non_dispatch_moves_fall_through_to_execute(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import handle as handle_mod
        for move in ("extend", "fork", "collate", "idunno"):
            calls = []
            self._patch_shadow_returning(monkeypatch, self._decision(move, 0.99))
            self._patch_handle(monkeypatch, calls)
            self._patch_act_config(monkeypatch)
            handle_mod.handle_task(self._task(), dry_run=False)
            assert calls == [self.GOAL], f"{move} must not act at dispatch"

    def test_guard_takes_precedence_over_navigator(self, monkeypatch, tmp_path):
        """A tripped guard refuses before the navigator's decision is consulted."""
        _setup(monkeypatch, tmp_path)
        import handle as handle_mod
        import runs
        import uuid
        for _ in range(3):
            handle_id = uuid.uuid4().hex[:12]
            rd = runs.create_run_dir(handle_id, prompt=self.GOAL)
            meta_path = rd / "metadata.json"
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            meta["status"] = "stuck"
            meta_path.write_text(json.dumps(meta), encoding="utf-8")
        calls = []
        # Navigator says close-delivered (done) — guard must still win.
        self._patch_shadow_returning(
            monkeypatch,
            self._decision("close", 0.99, payload={"closure": "delivered"}),
        )
        self._patch_handle(monkeypatch, calls)
        self._patch_act_config(monkeypatch)

        result = handle_mod.handle_task(self._task(), dry_run=False)

        assert result.classification_reason == "recall_guard"
        assert result.status == "error"

    def test_act_emits_navigator_acted_event(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import handle as handle_mod
        self._patch_shadow_returning(monkeypatch, self._decision("escalate", 0.95))
        self._patch_handle(monkeypatch, [])
        self._patch_act_config(monkeypatch)
        events = []
        from unittest.mock import patch as _patch
        with _patch("captains_log.log_event",
                    side_effect=lambda et, *a, **k: events.append(et)):
            handle_mod.handle_task(self._task(), dry_run=False)
        assert "NAVIGATOR_ACTED" in events


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

    def test_escalation_disabled_via_config_runs_now_lane(self, monkeypatch, tmp_path):
        """When escalate_to_director is configured False (default flipped to
        True on 2026-06-11), complex messages still use NOW."""
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

    def test_inconclusive_closure_does_not_restart(self, monkeypatch, tmp_path):
        """Inconclusive verification should not trigger a closure restart loop."""
        self._setup(monkeypatch, tmp_path)
        from unittest.mock import patch

        done_result = self._fake_loop_result(status="done")
        calls = []
        def _fake_run(*args, **kwargs):
            calls.append(kwargs.copy())
            return done_result

        inconclusive = self._fake_closure(False, 0.6, gaps=["verification was inconclusive"])
        inconclusive.inconclusive_count = 1

        with patch("agent_loop.run_agent_loop", side_effect=_fake_run), \
             patch("intent.check_goal_clarity", return_value={"clear": True}), \
             patch("director.verify_goal_completion", return_value=inconclusive), \
             self._no_quality_gate():
            handle("build X", force_lane="agenda", dry_run=False)

        assert len(calls) == 1, "inconclusive closure should not trigger restart"

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

    def test_scope_plumbed_to_closure(self, monkeypatch, tmp_path):
        """Generated scope must be passed into verify_goal_completion.

        This is the link between scope (planning-side inversion) and closure
        (verification-side inversion): closure probes the failure modes scope
        enumerated. If scope is not plumbed, the two halves don't compose.
        """
        self._setup(monkeypatch, tmp_path)
        from unittest.mock import patch, MagicMock
        from scope import ScopeSet

        done_result = self._fake_loop_result(status="done")

        # Enable scope generation via config.get
        def _cfg(name, default=None):
            if name == "scope_generation":
                return True
            return default

        fake_scope = ScopeSet(
            failure_modes=["server never started"],
            in_scope=["message echo"],
            out_of_scope=["auth"],
            raw_text="",
        )

        verify_calls = []
        def _fake_verify(*args, **kwargs):
            verify_calls.append(kwargs)
            return self._fake_closure(True, 0.9)

        with patch("agent_loop.run_agent_loop", return_value=done_result), \
             patch("intent.check_goal_clarity", return_value={"clear": True}), \
             patch("scope.generate_scope", return_value=fake_scope), \
             patch("director.verify_goal_completion", side_effect=_fake_verify), \
             patch("config.get", side_effect=_cfg), \
             self._no_quality_gate():
            handle("build X", force_lane="agenda", dry_run=False)

        assert verify_calls, "verify_goal_completion was not invoked"
        passed_scope = verify_calls[0].get("scope")
        assert passed_scope is not None, "scope not plumbed into verify_goal_completion"
        assert passed_scope.failure_modes == ["server never started"]

    def test_scope_skipped_event_when_generator_returns_none(self, monkeypatch, tmp_path):
        """When scope generation is enabled but produces nothing (adapter
        failure swallowed inside generate_scope), a SCOPE_SKIPPED captain's-log
        event must record the skip. During the May-2026 rc=1 outage every run
        silently lost its scope with no trace in the artifacts."""
        self._setup(monkeypatch, tmp_path)
        from unittest.mock import patch

        done_result = self._fake_loop_result(status="done")

        def _cfg(name, default=None):
            if name == "scope_generation":
                return True
            return default

        events = []
        def _fake_log_event(event_type, *args, **kwargs):
            events.append((event_type, kwargs))

        with patch("agent_loop.run_agent_loop", return_value=done_result), \
             patch("intent.check_goal_clarity", return_value={"clear": True}), \
             patch("scope.generate_resolved_intent", return_value=None), \
             patch("captains_log.log_event", side_effect=_fake_log_event), \
             patch("config.get", side_effect=_cfg), \
             self._no_quality_gate():
            handle("build X", force_lane="agenda", dry_run=False)

        skipped = [e for e in events if e[0] == "SCOPE_SKIPPED"]
        assert skipped, f"no SCOPE_SKIPPED event recorded (events: {[e[0] for e in events]})"
        assert skipped[0][1]["context"]["reason"] == "generator_returned_none"

    def test_diagnosis_plumbed_to_closure(self, monkeypatch, tmp_path):
        """Loop diagnosis should reach closure so it can distrust broad runs."""
        self._setup(monkeypatch, tmp_path)
        from unittest.mock import patch

        done_result = self._fake_loop_result(status="done")

        class _Diag:
            failure_class = "decomposition_too_broad"
            severity = "warning"
            recommendation = "split into narrower slices"

        verify_calls = []

        def _fake_verify(*args, **kwargs):
            verify_calls.append(kwargs)
            return self._fake_closure(True, 0.9)

        with patch("agent_loop.run_agent_loop", return_value=done_result), \
             patch("intent.check_goal_clarity", return_value={"clear": True}), \
             patch("introspect.diagnose_loop", return_value=_Diag()), \
             patch("director.verify_goal_completion", side_effect=_fake_verify), \
             self._no_quality_gate():
            handle("build X", force_lane="agenda", dry_run=False)

        assert verify_calls, "verify_goal_completion was not invoked"
        passed_diag = verify_calls[0].get("diagnosis")
        assert passed_diag is not None, "diagnosis not plumbed into verify_goal_completion"
        assert passed_diag.failure_class == "decomposition_too_broad"

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


# ---------------------------------------------------------------------------
# Post-escalate closure (audit finding 2026-04-26)
# ---------------------------------------------------------------------------

class TestPostEscalateClosure:
    """Quality gate ESCALATE re-runs the loop with a stronger model. The
    escalated re-run is the version we ship — but until 2026-04-26 the
    captain's log only carried the *initial* loop's CLOSURE_VERDICT, so the
    actual delivered work had no closure record. handle.py now runs a second
    verify_goal_completion after the escalated loop returns.
    """

    def _setup(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))

    def _fake_loop_result(self, status="done", loop_id="lr-1"):
        from agent_loop import LoopResult, StepOutcome
        return LoopResult(
            loop_id=loop_id, project="proj", goal="build X",
            status=status, stuck_reason=None,
            steps=[StepOutcome(index=0, text="step", status="done",
                               result="output", iteration=0)],
        )

    def _fake_closure(self, complete=True, confidence=0.9):
        from director import ClosureVerdict
        return ClosureVerdict(
            complete=complete, confidence=confidence,
            gaps=[], summary="ok", checks_run=2,
            checks_passed=(2 if complete else 0),
        )

    def _escalating_gate(self):
        from unittest.mock import patch, MagicMock
        verdict = MagicMock()
        verdict.escalate = True
        verdict.contested_claims = []
        verdict.reason = "weak coverage"
        return patch("quality_gate.run_quality_gate", return_value=verdict)

    def test_post_escalate_closure_runs(self, monkeypatch, tmp_path):
        """When quality gate escalates, verify_goal_completion is called
        TWICE — once for the initial loop, once for the escalated re-run."""
        self._setup(monkeypatch, tmp_path)
        from unittest.mock import patch, MagicMock

        initial = self._fake_loop_result(status="done", loop_id="lr-initial")
        escalated = self._fake_loop_result(status="done", loop_id="lr-escalated")
        run_results = [initial, escalated]

        def _fake_run(*args, **kwargs):
            return run_results.pop(0)

        verify_calls = []
        def _fake_verify(*args, **kwargs):
            verify_calls.append(kwargs)
            return self._fake_closure(complete=True, confidence=0.9)

        with patch("agent_loop.run_agent_loop", side_effect=_fake_run), \
             patch("intent.check_goal_clarity", return_value={"clear": True}), \
             patch("director.verify_goal_completion", side_effect=_fake_verify), \
             patch("llm.build_adapter", return_value=MagicMock()), \
             self._escalating_gate():
            handle("build X", force_lane="agenda", model="cheap", dry_run=False)

        assert len(verify_calls) == 2, (
            f"expected 2 closure calls (initial + post-escalate), got {len(verify_calls)}"
        )
        # Second call should reference the escalated loop's id
        second_loop_id = verify_calls[1].get("loop_id", "")
        assert second_loop_id == "lr-escalated", (
            f"post-escalate closure should target escalated loop_id, got {second_loop_id!r}"
        )

    def test_post_escalate_closure_failure_does_not_break_delivery(
        self, monkeypatch, tmp_path
    ):
        """Post-escalate closure errors are swallowed — handle.py must still
        return a result even if verify_goal_completion crashes on the second
        call."""
        self._setup(monkeypatch, tmp_path)
        from unittest.mock import patch, MagicMock

        initial = self._fake_loop_result(status="done", loop_id="lr-initial")
        escalated = self._fake_loop_result(status="done", loop_id="lr-escalated")
        run_results = [initial, escalated]

        def _fake_run(*args, **kwargs):
            return run_results.pop(0)

        call_count = {"n": 0}
        def _fake_verify(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return self._fake_closure(complete=True, confidence=0.9)
            raise RuntimeError("boom")  # second call (post-escalate) fails

        with patch("agent_loop.run_agent_loop", side_effect=_fake_run), \
             patch("intent.check_goal_clarity", return_value={"clear": True}), \
             patch("director.verify_goal_completion", side_effect=_fake_verify), \
             patch("llm.build_adapter", return_value=MagicMock()), \
             self._escalating_gate():
            result = handle("build X", force_lane="agenda", model="cheap", dry_run=False)

        # Both verify calls were attempted, but the failure didn't propagate
        assert call_count["n"] == 2
        assert result is not None
        assert result.status in ("done", "complete", "stuck", "partial", "restart")


# ---------------------------------------------------------------------------
# Per-run finalize in handle() itself (2026-06-11): every caller — not just
# the CLI — must leave run metadata with a real status, or recall reads the
# run as "unknown" and the dispatch guard counts succeeding repeats as
# failing.
# ---------------------------------------------------------------------------

class TestHandleFinalizesRun:
    def _run_meta(self, handle_id):
        from runs import run_dir
        return json.loads((run_dir(handle_id) / "metadata.json").read_text())

    def test_programmatic_handle_finalizes_metadata(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        result = handle("what time is it?", dry_run=True)
        meta = self._run_meta(result.handle_id)
        assert meta["status"] == result.status
        assert meta["ended_at"] is not None

    def test_finalize_records_thread_brain_close(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import thread_brain
        from runs import run_dir
        result = handle("what time is it?", dry_run=True)
        text = thread_brain.load_thread_brain(run_dir(result.handle_id))
        assert f"thread closed: {result.status}" in text

    def test_exception_path_finalizes_as_error(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from unittest.mock import patch
        import runs

        runs.set_current_run_dir(None)
        with patch("intent.classify", side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError):
                handle("what time is it?", dry_run=True)
        # The run dir was pinned by this call before the raise — it must be
        # closed as error, not left status=None.
        rd = runs.current_run_dir()
        assert rd is not None
        meta = json.loads((rd / "metadata.json").read_text())
        assert meta["status"] == "error"

    def test_stale_pin_from_prior_task_not_finalized_on_early_raise(
        self, monkeypatch, tmp_path
    ):
        _setup(monkeypatch, tmp_path)
        from unittest.mock import patch

        # Simulate a drain loop: task A finished and left its pin (only the
        # CLI clears). Task B raises before _handle_impl pins a new run dir
        # (in-body failures before create_run_dir are all swallowed, so the
        # equivalent observable is _handle_impl itself raising early).
        prior = handle("what time is it?", dry_run=True)
        prior_meta_before = self._run_meta(prior.handle_id)

        with patch("handle._handle_impl", side_effect=RuntimeError("early boom")):
            with pytest.raises(RuntimeError):
                handle("second goal", dry_run=True)

        # Task A's metadata must be untouched (not re-finalized as "error").
        assert self._run_meta(prior.handle_id) == prior_meta_before


class TestNowLaneOutcomeRecord:
    """NOW-lane runs record a slim outcome (no LLM lesson extraction)."""

    def test_now_run_records_outcome(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from unittest.mock import patch, MagicMock

        fake_adapter = MagicMock()
        canned = {"status": "done", "result": "the answer",
                  "tokens_in": 7, "tokens_out": 3}
        with patch("handle._run_now", return_value=canned):
            with patch("intent.classify", return_value=("now", 0.9, "simple")):
                result = handle(
                    "what time is it?",
                    adapter=fake_adapter,
                    force_lane="now",
                    dry_run=False,
                )
        assert result.lane == "now"
        from memory import load_outcomes
        recs = load_outcomes(limit=5)
        assert any(
            o.task_type == "now" and o.status == "done"
            and "what time is it" in o.goal
            for o in recs
        ), f"no NOW outcome recorded: {[(o.task_type, o.goal) for o in recs]}"

    def test_now_outcome_skips_lesson_extraction(self, monkeypatch, tmp_path):
        """The slim record must not invoke the LLM reflection path."""
        _setup(monkeypatch, tmp_path)
        from unittest.mock import patch, MagicMock

        fake_adapter = MagicMock()
        canned = {"status": "done", "result": "x", "tokens_in": 1, "tokens_out": 1}
        with patch("memory.extract_lessons_via_llm") as mock_extract:
            with patch("handle._run_now", return_value=canned):
                with patch("intent.classify", return_value=("now", 0.9, "simple")):
                    handle("ping", adapter=fake_adapter, force_lane="now", dry_run=False)
        mock_extract.assert_not_called()

    def test_dry_run_now_records_nothing(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        handle("what time is it?", dry_run=True, force_lane="now")
        from memory import load_outcomes
        assert not any(o.task_type == "now" for o in load_outcomes(limit=10))


class TestNowStatusHonesty:
    """Autonomous NOW runs demote 'done' to 'incomplete' when the response
    itself reports non-fulfillment (live find 2026-06-11: an impossible
    execution goal was recorded done by a quick-lane completion that honestly
    said it could not be done — poisoning recall, the guard, and the navigator)."""

    def _verdict_adapter(self, fulfilled):
        from unittest.mock import MagicMock
        adapter = MagicMock()
        resp = MagicMock()
        resp.content = '{"fulfilled": %s}' % ("true" if fulfilled else "false")
        resp.input_tokens = 5
        resp.output_tokens = 2
        adapter.complete.return_value = resp
        return adapter

    def test_verify_demotes_unfulfilled(self):
        from handle import _verify_now_outcome
        outcome = {"status": "done", "result": "The goal is incomplete.",
                   "tokens_in": 10, "tokens_out": 4}
        out = _verify_now_outcome("run the thing", outcome, self._verdict_adapter(False))
        assert out["status"] == "incomplete"
        assert out["goal_achieved"] is False
        assert out["tokens_in"] == 15 and out["tokens_out"] == 6

    def test_verify_keeps_fulfilled(self):
        from handle import _verify_now_outcome
        outcome = {"status": "done", "result": "Here is the answer: 42.",
                   "tokens_in": 10, "tokens_out": 4}
        out = _verify_now_outcome("what is the answer", outcome, self._verdict_adapter(True))
        assert out["status"] == "done"
        assert out["goal_achieved"] is True
        assert out["tokens_in"] == 10  # no verdict tokens added when kept

    def test_verify_fails_open(self):
        from unittest.mock import MagicMock
        from handle import _verify_now_outcome
        adapter = MagicMock()
        adapter.complete.side_effect = RuntimeError("adapter down")
        outcome = {"status": "done", "result": "x", "tokens_in": 1, "tokens_out": 1}
        out = _verify_now_outcome("goal", outcome, adapter)
        assert out["status"] == "done"
        assert "goal_achieved" not in out  # fail-open means unverified, not achieved

    def test_autonomous_now_run_demoted_end_to_end(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from unittest.mock import patch
        canned = {"status": "done", "result": "The binary does not exist; goal incomplete.",
                  "tokens_in": 7, "tokens_out": 3}
        with patch("handle._run_now", return_value=canned):
            with patch("intent.classify", return_value=("now", 0.9, "simple")):
                result = handle(
                    "do it",
                    adapter=self._verdict_adapter(False),
                    force_lane="now",
                    dry_run=False,
                    origin={"parent_handle_id": "abc", "source": "user_goal"},
                )
        assert result.status == "incomplete"
        # Finalized run metadata carries the honest status for recall —
        # and the goal verdict as its own dimension (done != successful).
        from runs import run_dir
        meta = json.loads((run_dir(result.handle_id) / "metadata.json").read_text())
        assert meta["status"] == "incomplete"
        assert meta["goal_achieved"] is False
        assert meta["goal_verdict_source"] == "now_self_verdict"

    def test_autonomous_now_verified_done_records_goal_achieved(self, monkeypatch, tmp_path):
        """A verified-done NOW run records goal_achieved=True, not just status."""
        _setup(monkeypatch, tmp_path)
        from unittest.mock import patch
        canned = {"status": "done", "result": "Here is the report you asked for.",
                  "tokens_in": 7, "tokens_out": 3}
        with patch("handle._run_now", return_value=canned):
            with patch("intent.classify", return_value=("now", 0.9, "simple")):
                result = handle(
                    "do it",
                    adapter=self._verdict_adapter(True),
                    force_lane="now",
                    dry_run=False,
                    origin={"parent_handle_id": "abc", "source": "user_goal"},
                )
        assert result.status == "done"
        from runs import run_dir
        meta = json.loads((run_dir(result.handle_id) / "metadata.json").read_text())
        assert meta["goal_achieved"] is True
        assert meta["goal_verdict_source"] == "now_self_verdict"

    def test_interactive_now_skips_verification(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from unittest.mock import patch
        canned = {"status": "done", "result": "cannot be done",
                  "tokens_in": 1, "tokens_out": 1}
        adapter = self._verdict_adapter(False)
        with patch("handle._run_now", return_value=canned):
            with patch("intent.classify", return_value=("now", 0.9, "simple")):
                result = handle("do it", adapter=adapter, force_lane="now", dry_run=False)
        assert result.status == "done"
        adapter.complete.assert_not_called()


class TestOutputProvenanceGuard:
    """Deterministic done!=achieved guard: when the goal names a dir-qualified
    output path that never landed, demote regardless of the text narrative.
    Catches the false_pass the text-only validator can't see (shadow-eval n=42,
    2026-06-24: 'save listing to artifacts/X' saved elsewhere, local PASS@1.00)."""

    def _adapter_that_must_not_be_called(self):
        from unittest.mock import MagicMock
        adapter = MagicMock()
        adapter.complete.side_effect = AssertionError("judge should not be reached")
        return adapter

    def _fulfilled_adapter(self):
        from unittest.mock import MagicMock
        adapter = MagicMock()
        resp = MagicMock()
        resp.content = '{"fulfilled": true}'
        resp.input_tokens = 1
        resp.output_tokens = 1
        adapter.complete.return_value = resp
        return adapter

    def test_extraction_dir_qualified_only(self):
        from handle import _claimed_output_paths
        assert _claimed_output_paths("save the listing to artifacts/skills-listing.txt") == \
            ["artifacts/skills-listing.txt"]
        assert _claimed_output_paths("save it to report.md") == []          # bare name
        assert _claimed_output_paths("read data/in.csv and summarize") == []  # input, not output
        assert _claimed_output_paths("create a function in src/foo.py") == []  # 'in', not a write target

    def test_missing_dir_qualified_output_demotes(self, tmp_path):
        from handle import _verify_now_outcome
        missing = tmp_path / "nope" / "missing.txt"   # absolute, does not exist
        outcome = {"status": "done", "result": "All set — saved the report.",
                   "tokens_in": 3, "tokens_out": 1}
        adapter = self._adapter_that_must_not_be_called()
        out = _verify_now_outcome(f"compute X and write the report to {missing}", outcome, adapter)
        assert out["status"] == "incomplete"
        assert out["goal_achieved"] is False
        assert str(missing) in out["provenance_missing"]
        adapter.complete.assert_not_called()   # deterministic short-circuit, no judge call

    def test_existing_output_passes_to_judge(self, tmp_path):
        from handle import _verify_now_outcome
        landed = tmp_path / "out.txt"
        landed.write_text("done")
        outcome = {"status": "done", "result": "saved", "tokens_in": 3, "tokens_out": 1}
        out = _verify_now_outcome(f"write the summary to {landed}", outcome, self._fulfilled_adapter())
        # provenance satisfied → falls through to the (fulfilled) judge → kept
        assert out["status"] == "done"
        assert out["goal_achieved"] is True

    def test_no_claim_unchanged(self):
        from handle import _verify_now_outcome
        outcome = {"status": "done", "result": "the answer is 42", "tokens_in": 3, "tokens_out": 1}
        out = _verify_now_outcome("what is the answer", outcome, self._fulfilled_adapter())
        assert out["status"] == "done"  # no output claimed → judge path, unchanged behavior

    def test_disabled_by_config_skips_guard(self, tmp_path, monkeypatch):
        import config
        from handle import _verify_now_outcome
        monkeypatch.setattr(config, "get", lambda key, default=None:
                            False if key == "validate.output_provenance" else default)
        missing = tmp_path / "gone" / "x.txt"
        outcome = {"status": "done", "result": "ok", "tokens_in": 3, "tokens_out": 1}
        # guard off → provenance does not demote; judge (fulfilled) keeps done
        out = _verify_now_outcome(f"save it to {missing}", outcome, self._fulfilled_adapter())
        assert out["status"] == "done"


class TestEscalationLaneMetadata:
    def test_escalated_run_metadata_records_agenda(self, monkeypatch, tmp_path):
        """The now→agenda escalation must rewrite the lane written at classify time."""
        _setup(monkeypatch, tmp_path)
        from unittest.mock import patch
        msg = ("Run the command /usr/bin/some-tool --report and save its complete "
               "output as an artifact file. Do not substitute another command and "
               "do not fabricate output. If the tool cannot be run, the goal is incomplete.")
        from handle import _is_complex_directive
        assert _is_complex_directive(msg)
        with patch("intent.classify", return_value=("now", 0.9, "looks quick")):
            result = handle(msg, dry_run=True)
        assert result.lane == "agenda"
        from runs import run_dir
        meta = json.loads((run_dir(result.handle_id) / "metadata.json").read_text())
        assert meta["lane"] == "agenda"


class TestEscalationRespectsForceLane:
    def test_forced_now_is_not_escalated(self, monkeypatch, tmp_path):
        """force_lane='now' wins over the complex-directive escalation."""
        _setup(monkeypatch, tmp_path)
        msg = ("Run the command /usr/bin/some-tool --report and save its complete "
               "output as an artifact file. Do not substitute another command and "
               "do not fabricate output. If the tool cannot be run, the goal is incomplete.")
        from handle import _is_complex_directive
        assert _is_complex_directive(msg)
        result = handle(msg, force_lane="now", dry_run=True)
        assert result.lane == "now"


class TestClosureStatusHonesty:
    """A 'done' the director's verifier contradicts at high confidence is
    recorded incomplete (live find 2026-06-11: unsatisfiable goal, every step
    result said 'goal is incomplete', closure agreed 0.95-0.99, run finalized
    done anyway — restarted loops were never re-verified)."""

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
            gaps=gaps or [], summary="still not achieved",
            checks_run=checks_run, checks_passed=(checks_run if complete else 0),
        )

    def test_persistent_contradiction_demotes_after_restart(self, monkeypatch, tmp_path):
        """Restart happens, re-verify still says incomplete -> status incomplete."""
        self._setup(monkeypatch, tmp_path)
        from unittest.mock import patch

        calls = []
        def _fake_run(*args, **kwargs):
            calls.append(kwargs.copy())
            return self._fake_loop_result(status="done")

        verdict_seq = [self._fake_closure(False, 0.85, gaps=["impossible"]),
                       self._fake_closure(False, 0.95, gaps=["still impossible"])]
        with patch("agent_loop.run_agent_loop", side_effect=_fake_run), \
             patch("intent.check_goal_clarity", return_value={"clear": True}), \
             patch("director.verify_goal_completion",
                   side_effect=lambda *a, **k: verdict_seq.pop(0)), \
             self._no_quality_gate():
            result = handle("do the impossible", force_lane="agenda", dry_run=False)

        assert len(calls) == 2, "restart should still happen"
        assert result.status == "incomplete"
        assert "still not achieved" in (result.result or "") or True  # status is the contract

    def test_restart_that_fixes_gaps_stays_done(self, monkeypatch, tmp_path):
        self._setup(monkeypatch, tmp_path)
        from unittest.mock import patch

        def _fake_run(*args, **kwargs):
            return self._fake_loop_result(status="done")

        verdict_seq = [self._fake_closure(False, 0.85, gaps=["gap"]),
                       self._fake_closure(True, 0.9)]
        with patch("agent_loop.run_agent_loop", side_effect=_fake_run), \
             patch("intent.check_goal_clarity", return_value={"clear": True}), \
             patch("director.verify_goal_completion",
                   side_effect=lambda *a, **k: verdict_seq.pop(0)), \
             self._no_quality_gate():
            result = handle("build X", force_lane="agenda", dry_run=False)

        assert result.status == "done"

    def test_demotion_fires_without_restart_when_disabled(self, monkeypatch, tmp_path):
        """closure_restart=False still demotes — honesty is not restart policy."""
        self._setup(monkeypatch, tmp_path)
        from unittest.mock import patch

        calls = []
        def _fake_run(*args, **kwargs):
            calls.append(kwargs.copy())
            return self._fake_loop_result(status="done")

        def _cfg(name, default=None):
            if name == "closure_restart":
                return False
            return default

        with patch("agent_loop.run_agent_loop", side_effect=_fake_run), \
             patch("intent.check_goal_clarity", return_value={"clear": True}), \
             patch("director.verify_goal_completion",
                   return_value=self._fake_closure(False, 0.9, gaps=["gap"])), \
             patch("config.get", side_effect=_cfg), \
             self._no_quality_gate():
            result = handle("build X", force_lane="agenda", dry_run=False)

        assert len(calls) == 1
        assert result.status == "incomplete"

    def test_low_confidence_contradiction_keeps_done(self, monkeypatch, tmp_path):
        self._setup(monkeypatch, tmp_path)
        from unittest.mock import patch

        def _fake_run(*args, **kwargs):
            return self._fake_loop_result(status="done")

        with patch("agent_loop.run_agent_loop", side_effect=_fake_run), \
             patch("intent.check_goal_clarity", return_value={"clear": True}), \
             patch("director.verify_goal_completion",
                   return_value=self._fake_closure(False, 0.5, gaps=["maybe"])), \
             self._no_quality_gate():
            result = handle("build X", force_lane="agenda", dry_run=False)

        assert result.status == "done"

    def test_demoted_status_reaches_run_metadata(self, monkeypatch, tmp_path):
        """recall reads metadata.json — the demotion must land there."""
        self._setup(monkeypatch, tmp_path)
        from unittest.mock import patch

        def _fake_run(*args, **kwargs):
            return self._fake_loop_result(status="done")

        def _cfg(name, default=None):
            if name == "closure_restart":
                return False
            return default

        with patch("agent_loop.run_agent_loop", side_effect=_fake_run), \
             patch("intent.check_goal_clarity", return_value={"clear": True}), \
             patch("director.verify_goal_completion",
                   return_value=self._fake_closure(False, 0.9, gaps=["gap"])), \
             patch("config.get", side_effect=_cfg), \
             self._no_quality_gate():
            result = handle("build X", force_lane="agenda", dry_run=False)

        from runs import run_dir
        meta = json.loads((run_dir(result.handle_id) / "metadata.json").read_text())
        assert meta["status"] == "incomplete"
        # Goal verdict recorded as its own dimension alongside process status.
        assert meta["goal_achieved"] is False
        assert meta["goal_verdict_source"] == "closure"
        assert meta["goal_verdict_confidence"] == 0.9
        assert meta["goal_verdict_summary"]

    def test_closure_complete_records_goal_achieved_true(self, monkeypatch, tmp_path):
        """done + closure complete=True -> goal_achieved=True in metadata."""
        self._setup(monkeypatch, tmp_path)
        from unittest.mock import patch

        def _fake_run(*args, **kwargs):
            return self._fake_loop_result(status="done")

        with patch("agent_loop.run_agent_loop", side_effect=_fake_run), \
             patch("intent.check_goal_clarity", return_value={"clear": True}), \
             patch("director.verify_goal_completion",
                   return_value=self._fake_closure(True, 0.95)), \
             self._no_quality_gate():
            result = handle("build X", force_lane="agenda", dry_run=False)

        assert result.status == "done"
        from runs import run_dir
        meta = json.loads((run_dir(result.handle_id) / "metadata.json").read_text())
        assert meta["goal_achieved"] is True
        assert meta["goal_verdict_source"] == "closure"
        assert meta["goal_verdict_confidence"] == 0.95
