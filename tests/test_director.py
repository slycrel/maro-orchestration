"""Tests for Phase 3: director.py + workers.py (Director/Worker hierarchy)."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from workers import (
    WorkerResult,
    dispatch_worker,
    infer_worker_type,
    infer_crew_size,
    _dry_run_worker,
    WORKER_RESEARCH, WORKER_BUILD, WORKER_OPS, WORKER_GENERAL, WORKER_TYPES,
)
from director import (
    DirectorResult,
    Ticket,
    ReviewDecision,
    ClosureVerdict,
    EvaluationContext,
    DirectorDecision,
    director_evaluate,
    run_director,
    requires_explicit_acceptance,
    verify_goal_completion,
    _produce_spec,
    _review_worker_output,
    _is_simple_directive,
    _is_large_scope_review,
    _LARGE_SCOPE_SPEC_SYSTEM,
)


def _setup(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------------------
# Workers
# ---------------------------------------------------------------------------

class TestInferWorkerType:
    def test_research(self):
        assert infer_worker_type("research polymarket strategies") == WORKER_RESEARCH

    def test_analyze(self):
        assert infer_worker_type("analyze competitor pricing") == WORKER_RESEARCH

    def test_build(self):
        assert infer_worker_type("build a Python script for scraping") == WORKER_BUILD

    def test_implement(self):
        assert infer_worker_type("implement the auth middleware") == WORKER_BUILD

    def test_ops(self):
        assert infer_worker_type("deploy the service to production") == WORKER_OPS

    def test_monitor(self):
        assert infer_worker_type("monitor CPU usage") == WORKER_OPS

    def test_general_fallback(self):
        assert infer_worker_type("do the thing") == WORKER_GENERAL

    def test_unknown_words(self):
        wtype = infer_worker_type("xyzzy frobble zork")
        assert wtype in WORKER_TYPES


class TestDryRunWorker:
    def test_returns_done(self):
        r = _dry_run_worker(WORKER_RESEARCH, "test ticket")
        assert r.status == "done"
        assert len(r.result) > 0

    def test_includes_worker_type(self):
        r = _dry_run_worker(WORKER_BUILD, "build something")
        assert "build" in r.result.lower()

    def test_has_token_counts(self):
        r = _dry_run_worker(WORKER_GENERAL, "task")
        assert r.tokens_in >= 0
        assert r.tokens_out >= 0


class TestDispatchWorker:
    def test_dry_run_research(self):
        r = dispatch_worker(WORKER_RESEARCH, "research X", dry_run=True)
        assert r.status == "done"
        assert r.worker_type == WORKER_RESEARCH

    def test_dry_run_build(self):
        r = dispatch_worker(WORKER_BUILD, "build Y", dry_run=True)
        assert r.status == "done"

    def test_dry_run_invalid_type_defaults_to_general(self):
        r = dispatch_worker("invalid_type", "some task", dry_run=True)
        assert r.worker_type == WORKER_GENERAL

    def test_dry_run_no_adapter(self):
        r = dispatch_worker(WORKER_OPS, "run diagnostics")  # no adapter, no dry_run
        assert r.status == "done"  # falls back to dry-run behavior

    def test_worker_result_has_ticket(self):
        r = dispatch_worker(WORKER_RESEARCH, "find X", dry_run=True)
        assert r.ticket == "find X"

    def test_api_failure_returns_blocked(self):
        class FailAdapter:
            def complete(self, *args, **kwargs):
                raise RuntimeError("API error")

        r = dispatch_worker(WORKER_GENERAL, "some task", adapter=FailAdapter())
        assert r.status == "blocked"
        assert "LLM call failed" in r.stuck_reason


# ---------------------------------------------------------------------------
# requires_explicit_acceptance
# ---------------------------------------------------------------------------

class TestPlanAcceptance:
    def test_post_tweet_is_explicit(self):
        assert requires_explicit_acceptance("post a tweet about AI")

    def test_publish_is_explicit(self):
        assert requires_explicit_acceptance("publish the article to Medium")

    def test_send_email_is_explicit(self):
        assert requires_explicit_acceptance("send email to newsletter subscribers")

    def test_delete_is_explicit(self):
        assert requires_explicit_acceptance("delete the old database records")

    def test_research_is_inferred(self):
        assert not requires_explicit_acceptance("research polymarket strategies")

    def test_build_is_inferred(self):
        assert not requires_explicit_acceptance("build a research report")

    def test_analyze_is_inferred(self):
        assert not requires_explicit_acceptance("analyze competitor pricing")


# ---------------------------------------------------------------------------
# Director integration
# ---------------------------------------------------------------------------

class TestRunDirector:
    def test_dry_run_returns_result(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        result = run_director("research polymarket strategies", dry_run=True)
        assert isinstance(result, DirectorResult)
        assert result.status == "done"

    def test_dry_run_has_tickets(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        result = run_director("research and build a report", dry_run=True)
        assert len(result.tickets) >= 1
        assert all(isinstance(t, Ticket) for t in result.tickets)

    def test_dry_run_has_worker_results(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        result = run_director("analyze X", dry_run=True)
        assert len(result.worker_results) >= 1
        assert all(isinstance(r, WorkerResult) for r in result.worker_results)

    def test_dry_run_has_report(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        result = run_director("write a research report", dry_run=True)
        assert len(result.report) > 0

    def test_plan_acceptance_explicit(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        result = run_director("post a tweet about our product", dry_run=True)
        assert result.plan_acceptance == "explicit"

    def test_plan_acceptance_inferred(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        result = run_director("research winning strategies", dry_run=True)
        assert result.plan_acceptance == "inferred"

    def test_writes_log(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        result = run_director("test log writing", dry_run=True)
        assert result.log_path is not None

    def test_summary_format(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        result = run_director("summarize the findings", dry_run=True)
        s = result.summary()
        assert "director_id=" in s
        assert "status=" in s
        assert "plan_acceptance=" in s

    def test_review_decisions_populated(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        result = run_director("analyze X", dry_run=True)
        assert len(result.review_decisions) >= 1
        assert all(isinstance(d, ReviewDecision) for d in result.review_decisions)

    def test_token_tracking(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        result = run_director("count tokens", dry_run=True)
        assert result.tokens_in >= 0
        assert result.tokens_out >= 0


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------

def test_cli_poe_director_text(monkeypatch, tmp_path, capsys):
    _setup(monkeypatch, tmp_path)
    import cli
    rc = cli.main(["poe-director", "research polymarket strategies", "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "director_id=" in out
    assert "REPORT" in out


def test_cli_poe_director_json(monkeypatch, tmp_path, capsys):
    _setup(monkeypatch, tmp_path)
    import cli
    rc = cli.main(["poe-director", "build a report", "--dry-run", "--format", "json"])
    assert rc == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert "director_id" in data
    assert "report" in data
    assert data["status"] == "done"


def test_cli_poe_director_explicit_acceptance(monkeypatch, tmp_path, capsys):
    _setup(monkeypatch, tmp_path)
    import cli
    rc = cli.main(["poe-director", "send email to users", "--dry-run", "--format", "json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["plan_acceptance"] == "explicit"


# ---------------------------------------------------------------------------
# Phase 8: infer_crew_size
# ---------------------------------------------------------------------------

class TestInferCrewSize:
    def test_simple_short_directive(self):
        assert infer_crew_size("fix the bug") == 1

    def test_simple_keyword(self):
        assert infer_crew_size("do a quick check on the system health status") == 1

    def test_brief_keyword(self):
        assert infer_crew_size("give me a brief summary of recent events") == 1

    def test_medium_directive(self):
        assert infer_crew_size("analyze the performance metrics and identify areas for improvement in our system") == 2

    def test_comprehensive_keyword(self):
        assert infer_crew_size("comprehensive review of all project outcomes") == 3

    def test_detailed_keyword(self):
        assert infer_crew_size("detailed analysis of failure patterns") == 3

    def test_exhaustive_keyword(self):
        assert infer_crew_size("exhaustive audit of system configuration") == 4

    def test_thorough_keyword(self):
        assert infer_crew_size("thorough investigation of all error logs") == 4

    def test_very_long_directive(self):
        words = ["word"] * 55
        assert infer_crew_size(" ".join(words)) == 4

    def test_returns_int(self):
        result = infer_crew_size("test")
        assert isinstance(result, int)

    def test_range_1_to_4(self):
        for text in ["hi", "medium length text with some words", "comprehensive review", "exhaustive deep dive"]:
            size = infer_crew_size(text)
            assert 1 <= size <= 4


# ---------------------------------------------------------------------------
# Skip-Director experiment: _is_simple_directive + skip_if_simple
# ---------------------------------------------------------------------------

class TestIsSimpleDirective:
    def test_short_clear_goal_is_simple(self):
        assert _is_simple_directive("check weather in New York") is True

    def test_single_word_is_simple(self):
        assert _is_simple_directive("status") is True

    def test_over_15_words_is_not_simple(self):
        long_goal = "fetch the latest price data for all listed tokens and then compare them across all markets"
        assert _is_simple_directive(long_goal) is False

    def test_exactly_15_words_is_simple(self):
        fifteen = " ".join(["word"] * 15)
        assert _is_simple_directive(fifteen) is True

    def test_16_words_is_not_simple(self):
        sixteen = " ".join(["word"] * 16)
        assert _is_simple_directive(sixteen) is False

    def test_definitely_complex_mission(self):
        assert _is_simple_directive("build a multi-day mission plan") is False

    def test_definitely_complex_architecture(self):
        assert _is_simple_directive("design system architecture") is False

    def test_definitely_complex_refactor(self):
        assert _is_simple_directive("refactor the codebase") is False

    def test_definitely_complex_deploy(self):
        assert _is_simple_directive("deploy the release") is False

    def test_complex_keyword_and_then(self):
        assert _is_simple_directive("fetch data and then analyze it") is False

    def test_complex_keyword_pipeline(self):
        assert _is_simple_directive("set up a pipeline") is False

    def test_complex_keyword_coordinate(self):
        assert _is_simple_directive("coordinate with the team") is False

    def test_complex_keyword_research_and(self):
        assert _is_simple_directive("research and compare approaches") is False

    def test_multiple_sentences_is_not_simple(self):
        assert _is_simple_directive("do this. do that. then do more.") is False

    def test_semicolon_is_not_simple(self):
        assert _is_simple_directive("do this; then that") is False

    def test_single_sentence_with_one_period_is_ok(self):
        assert _is_simple_directive("fetch the price.") is True

    def test_strips_leading_trailing_whitespace(self):
        assert _is_simple_directive("  check status  ") is True

    def test_case_insensitive(self):
        assert _is_simple_directive("MISSION critical task") is False


class TestRunDirectorSkipIfSimple:
    def test_simple_goal_skips_director(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        import agent_loop as _al
        from agent_loop import LoopResult, StepOutcome

        called = []

        def _fake_loop(goal, *, project=None, adapter=None, dry_run=False, verbose=False, **kw):
            called.append(goal)
            return LoopResult(
                loop_id="skip-test",
                project=project or "test",
                goal=goal,
                status="done",
                steps=[StepOutcome(index=1, text=goal, status="done",
                                   result="direct result", iteration=1)],
            )

        monkeypatch.setattr(_al, "run_agent_loop", _fake_loop)

        result = run_director("check the price", skip_if_simple=True, dry_run=True)

        assert result.status == "done"
        assert called == ["check the price"]
        assert "direct result" in (result.report or "")

    def test_complex_goal_does_not_skip(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        import agent_loop as _al

        skipped = []

        def _fake_loop(goal, **kw):
            skipped.append("called")

        monkeypatch.setattr(_al, "run_agent_loop", _fake_loop)

        # complex goal — should go through Director, not fake loop
        result = run_director(
            "build and test the full pipeline then deploy to staging",
            skip_if_simple=True,
            dry_run=True,
        )

        # Director dry_run returns a DirectorResult without calling run_agent_loop
        assert not skipped
        assert isinstance(result, DirectorResult)

    def test_skip_if_simple_false_does_not_skip(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        import agent_loop as _al

        skipped = []

        def _fake_loop(goal, **kw):
            skipped.append("called")

        monkeypatch.setattr(_al, "run_agent_loop", _fake_loop)

        result = run_director("check price", skip_if_simple=False, dry_run=True)

        # skip_if_simple=False → Director runs normally, fake loop never called
        assert not skipped
        assert isinstance(result, DirectorResult)

    def test_skip_result_has_done_status(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        import agent_loop as _al
        from agent_loop import LoopResult, StepOutcome

        monkeypatch.setattr(_al, "run_agent_loop", lambda goal, **kw: LoopResult(
            loop_id="x",
            project="test",
            goal=goal,
            status="done",
            steps=[StepOutcome(index=1, text=goal, status="done",
                               result="output", iteration=1)],
        ))

        result = run_director("quick lookup", skip_if_simple=True, dry_run=True)
        assert result.status == "done"

    def test_large_scope_not_simple(self):
        assert _is_simple_directive("adversarial review of the entire codebase") is False
        assert _is_simple_directive("do a comprehensive review of the full repo") is False
        assert _is_simple_directive("full audit of all modules") is False

    def test_skip_result_preserves_directive(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        import agent_loop as _al
        from agent_loop import LoopResult, StepOutcome

        monkeypatch.setattr(_al, "run_agent_loop", lambda goal, **kw: LoopResult(
            loop_id="x",
            project="test",
            goal=goal,
            status="done",
            steps=[StepOutcome(index=1, text=goal, status="done",
                               result="r", iteration=1)],
        ))

        result = run_director("quick lookup", skip_if_simple=True, dry_run=True)
        assert result.directive == "quick lookup"


class TestLargeScopeReview:
    def test_detection_positive(self):
        assert _is_large_scope_review("adversarial review of the entire codebase")
        assert _is_large_scope_review("do a comprehensive review of the full repo")
        assert _is_large_scope_review("full audit of all modules")
        assert _is_large_scope_review("codebase review focusing on security")
        assert _is_large_scope_review("audit the codebase for quality issues")

    def test_detection_negative(self):
        assert not _is_large_scope_review("review the auth module")
        assert not _is_large_scope_review("analyze the test failures")
        assert not _is_large_scope_review("write a report on memory usage")

    def test_produce_spec_large_scope_dry_run(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        # In dry_run, _produce_spec returns a single-ticket fallback regardless
        spec, tickets, _ = _produce_spec(
            "adversarial review of the entire codebase",
            adapter=None,
            dry_run=True,
            log=lambda m: None,
        )
        assert "[dry-run]" in tickets[0].task

    def test_produce_spec_large_scope_uses_large_system(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        captured = {}

        class _FakeAdapter:
            def complete(self, messages, **kw):
                captured["system"] = messages[0].content
                from types import SimpleNamespace
                return SimpleNamespace(
                    content='{"spec": "staged review", "tickets": ['
                            '{"worker_type": "research", "task": "pass 1: docs"},'
                            '{"worker_type": "research", "task": "pass 2: core"},'
                            '{"worker_type": "research", "task": "pass 3: tests"},'
                            '{"worker_type": "general", "task": "pass 4: synthesize"}'
                            ']}',
                    input_tokens=10,
                    output_tokens=40,
                )

        spec, tickets, _ = _produce_spec(
            "adversarial review of the entire codebase",
            adapter=_FakeAdapter(),
            dry_run=False,
            log=lambda m: None,
        )
        # Should use the large-scope spec system (allows 4-6 tickets)
        assert "staged" in captured["system"].lower() or "domain" in captured["system"].lower()
        assert len(tickets) == 4


# ---------------------------------------------------------------------------
# Review loop exhaustion (max rounds hit without acceptance)
# ---------------------------------------------------------------------------

class TestReviewLoopExhaustion:
    def test_review_exhaustion_proceeds_with_best_effort(self, monkeypatch, tmp_path):
        """When MAX_REVIEW_ROUNDS exhausted without acceptance, director returns best-effort result."""
        _setup(monkeypatch, tmp_path)
        from unittest.mock import MagicMock, patch

        # Fake dispatch_worker that always returns a done result
        fake_worker_result = MagicMock()
        fake_worker_result.status = "done"
        fake_worker_result.result = "best effort research output"
        fake_worker_result.tokens_in = 5
        fake_worker_result.tokens_out = 10

        # Fake _review_worker_output that always rejects (never accepts)
        fake_review = MagicMock()
        fake_review.accepted = False
        fake_review.revision_request = "needs more detail"

        with patch("director.dispatch_worker", return_value=fake_worker_result):
            with patch("director._review_worker_output", return_value=(fake_review, (5, 5))):
                result = run_director(
                    "research the market",
                    adapter=MagicMock(),
                    dry_run=False,
                )

        # Should complete (not crash) with best-effort result
        assert result is not None
        assert result.status in ("done", "partial", "error")
        # Review decisions should be recorded (even if not accepted)
        assert len(result.review_decisions) > 0

    def test_review_loop_stops_at_max_rounds(self, monkeypatch, tmp_path):
        """Director should not call dispatch_worker more than 1 + (MAX_REVIEW_ROUNDS - 1) times per ticket."""
        _setup(monkeypatch, tmp_path)
        from unittest.mock import MagicMock, patch
        from director import MAX_REVIEW_ROUNDS

        dispatch_call_count = [0]

        def _counting_dispatch(*a, **kw):
            dispatch_call_count[0] += 1
            result = MagicMock()
            result.status = "done"
            result.result = "output"
            result.tokens_in = 5
            result.tokens_out = 10
            return result

        # Always reject
        fake_review = MagicMock()
        fake_review.accepted = False
        fake_review.revision_request = "revise"

        with patch("director.dispatch_worker", side_effect=_counting_dispatch):
            with patch("director._review_worker_output", return_value=(fake_review, (5, 5))):
                run_director("research topic", adapter=MagicMock(), dry_run=False)

        # 1 initial + (MAX_REVIEW_ROUNDS - 1) revisions = MAX_REVIEW_ROUNDS per ticket, upper bound
        assert dispatch_call_count[0] <= MAX_REVIEW_ROUNDS * 10


# ---------------------------------------------------------------------------
# Director Closure Check
# ---------------------------------------------------------------------------

class TestVerifyGoalCompletion:
    """Tests for verify_goal_completion — director closure check."""

    def test_dry_run_returns_complete(self):
        verdict = verify_goal_completion("build X", [], None, dry_run=True)
        assert verdict.complete is True
        assert verdict.checks_run == 0

    def test_no_adapter_returns_complete(self):
        verdict = verify_goal_completion("build X", [], None)
        assert verdict.complete is True

    def test_no_checks_returns_complete(self, monkeypatch):
        """If director generates no checks (research goal), skip verification."""
        from unittest.mock import MagicMock, patch
        adapter = MagicMock()
        adapter.complete.return_value = MagicMock()
        with patch("director.extract_json", return_value={"checks": []}):
            verdict = verify_goal_completion("summarize this article", [], adapter)
        assert verdict.complete is True
        assert verdict.checks_run == 0

    def test_all_checks_pass(self, monkeypatch, tmp_path):
        """All passing checks → director can declare complete."""
        from unittest.mock import MagicMock, patch, call

        adapter = MagicMock()
        plan_resp = MagicMock()
        verdict_resp = MagicMock()
        adapter.complete.side_effect = [plan_resp, verdict_resp]

        checks = [{"description": "file exists", "command": f"test -f {tmp_path}"}]
        verdict_data = {"complete": True, "confidence": 0.9, "gaps": [], "summary": "All good."}

        with patch("director.extract_json", side_effect=[{"checks": checks}, verdict_data]):
            with patch("director.content_or_empty", return_value="{}"):
                result = verify_goal_completion(
                    "create a directory", [], adapter, workspace_path=str(tmp_path)
                )

        # Verdict comes from the mocked data, not the subprocess result
        assert isinstance(result, ClosureVerdict)

    def test_failed_checks_surface_gaps(self, monkeypatch, tmp_path):
        """Failed checks + director verdict with gaps → needs_work emitted."""
        from unittest.mock import MagicMock, patch

        adapter = MagicMock()
        channel = MagicMock()
        channel.emit = MagicMock()

        checks = [{"description": "server builds", "command": "false"}]
        verdict_data = {
            "complete": False, "confidence": 0.2,
            "gaps": ["Server does not compile"], "summary": "Build failed."
        }

        with patch("director.extract_json", side_effect=[{"checks": checks}, verdict_data]):
            with patch("director.content_or_empty", return_value="{}"):
                result = verify_goal_completion(
                    "build a server", [], adapter,
                    workspace_path=str(tmp_path), channel=channel,
                )

        assert result.complete is False
        assert len(result.gaps) > 0
        # needs_work event should have been emitted
        emitted_types = [c.args[0] for c in channel.emit.call_args_list]
        assert "needs_work" in emitted_types

    def test_verification_event_emitted(self, monkeypatch, tmp_path):
        """verification event always emitted when checks run."""
        from unittest.mock import MagicMock, patch

        adapter = MagicMock()
        channel = MagicMock()
        channel.emit = MagicMock()

        checks = [{"description": "true", "command": "true"}]
        verdict_data = {"complete": True, "confidence": 0.95, "gaps": [], "summary": "Done."}

        with patch("director.extract_json", side_effect=[{"checks": checks}, verdict_data]):
            with patch("director.content_or_empty", return_value="{}"):
                verify_goal_completion(
                    "do a thing", [], adapter,
                    workspace_path=str(tmp_path), channel=channel,
                )

        emitted_types = [c.args[0] for c in channel.emit.call_args_list]
        assert "verification" in emitted_types

    def test_exception_returns_complete(self):
        """Exceptions are swallowed — never blocks execution."""
        from unittest.mock import MagicMock, patch

        adapter = MagicMock()
        adapter.complete.side_effect = RuntimeError("API down")

        result = verify_goal_completion("build X", [], adapter)
        assert result.complete is True

    def test_timeout_marks_check_failed(self, monkeypatch, tmp_path):
        """Timed-out checks are marked failed, not raised."""
        import subprocess
        from unittest.mock import MagicMock, patch

        adapter = MagicMock()
        checks = [{"description": "slow", "command": "sleep 999"}]
        verdict_data = {"complete": False, "confidence": 0.3,
                        "gaps": ["check timed out"], "summary": "Timed out."}

        def _raise_timeout(*a, **kw):
            raise subprocess.TimeoutExpired("sleep", 1)

        with patch("director.extract_json", side_effect=[{"checks": checks}, verdict_data]):
            with patch("director.content_or_empty", return_value="{}"):
                with patch("subprocess.run", side_effect=_raise_timeout):
                    result = verify_goal_completion(
                        "build X", [], adapter,
                        workspace_path=str(tmp_path), timeout_per_check=1,
                    )

        assert result.checks_run == 1
        assert result.checks_passed == 0


# ---------------------------------------------------------------------------
# Adaptive Execution — Phase 64, Phase A
# ---------------------------------------------------------------------------

def _eval_ctx(
    *,
    goal="test goal",
    steps_completed=None,
    steps_remaining=None,
    step_results_summary="",
    verify_failure_count=0,
    total_steps_taken=0,
    max_steps=10,
) -> EvaluationContext:
    return EvaluationContext(
        goal=goal,
        current_pass_scope=goal,
        steps_completed=steps_completed or [],
        steps_remaining=steps_remaining or [],
        step_results_summary=step_results_summary,
        verify_failure_count=verify_failure_count,
        total_steps_taken=total_steps_taken,
        max_steps=max_steps,
    )


class TestEvaluationContext:
    def test_defaults(self):
        ctx = _eval_ctx()
        assert ctx.current_approach == ""
        assert ctx.convergence_budget_remaining == 2

    def test_fields_set(self):
        ctx = _eval_ctx(
            goal="build a thing",
            steps_completed=["step 1"],
            steps_remaining=["step 2"],
            verify_failure_count=1,
            total_steps_taken=3,
            max_steps=20,
        )
        assert ctx.goal == "build a thing"
        assert ctx.steps_completed == ["step 1"]
        assert ctx.steps_remaining == ["step 2"]
        assert ctx.verify_failure_count == 1
        assert ctx.total_steps_taken == 3
        assert ctx.max_steps == 20


class TestDirectorDecision:
    def test_continue_defaults(self):
        d = DirectorDecision(action="continue", reasoning="all good")
        assert d.action == "continue"
        assert d.revised_steps is None
        assert d.next_check_in == 3

    def test_adjust_with_steps(self):
        d = DirectorDecision(
            action="adjust",
            reasoning="steps off track",
            revised_steps=["new step 1", "new step 2"],
            next_check_in=5,
        )
        assert d.action == "adjust"
        assert len(d.revised_steps) == 2
        assert d.next_check_in == 5

    def test_phase_bc_fields_default_none(self):
        d = DirectorDecision(action="continue", reasoning="ok")
        assert d.new_approach is None
        assert d.restart_context is None
        assert d.user_question is None


class TestDirectorEvaluate:
    def test_dry_run_returns_continue(self):
        ctx = _eval_ctx()
        result = director_evaluate("goal", ctx, "step_threshold", None, dry_run=True)
        assert result.action == "continue"

    def test_none_adapter_returns_continue(self):
        ctx = _eval_ctx()
        result = director_evaluate("goal", ctx, "verify_failure", None)
        assert result.action == "continue"

    def test_llm_returns_continue(self):
        from unittest.mock import MagicMock, patch

        adapter = MagicMock()
        ctx = _eval_ctx(steps_remaining=["finish it"])
        data = {"action": "continue", "reasoning": "looks fine", "next_check_in": 3}

        with patch("director.extract_json", return_value=data):
            with patch("director.content_or_empty", return_value="{}"):
                result = director_evaluate("build X", ctx, "step_threshold", adapter)

        assert result.action == "continue"
        assert result.reasoning == "looks fine"
        assert result.next_check_in == 3
        assert result.revised_steps is None

    def test_llm_returns_adjust_with_steps(self):
        from unittest.mock import MagicMock, patch

        adapter = MagicMock()
        ctx = _eval_ctx(steps_remaining=["old step 1", "old step 2"])
        data = {
            "action": "adjust",
            "reasoning": "steps are wrong",
            "revised_steps": ["new step A", "new step B", "new step C"],
            "next_check_in": 4,
        }

        with patch("director.extract_json", return_value=data):
            with patch("director.content_or_empty", return_value="{}"):
                result = director_evaluate("build X", ctx, "verify_failure", adapter)

        assert result.action == "adjust"
        assert result.revised_steps == ["new step A", "new step B", "new step C"]
        assert result.next_check_in == 4

    def test_adjust_with_empty_steps_falls_back_to_continue(self):
        from unittest.mock import MagicMock, patch

        adapter = MagicMock()
        ctx = _eval_ctx()
        data = {"action": "adjust", "reasoning": "eh", "revised_steps": [], "next_check_in": 3}

        with patch("director.extract_json", return_value=data):
            with patch("director.content_or_empty", return_value="{}"):
                result = director_evaluate("build X", ctx, "stuck", adapter)

        assert result.action == "continue"

    def test_replan_allowed_with_budget(self):
        """replan is allowed when convergence budget > 0."""
        from unittest.mock import MagicMock, patch

        adapter = MagicMock()
        ctx = _eval_ctx()
        ctx.convergence_budget_remaining = 2
        data = {
            "action": "replan",
            "reasoning": "wrong approach entirely",
            "new_approach": "try a different strategy",
            "next_check_in": 3,
        }

        with patch("director.extract_json", return_value=data):
            with patch("director.content_or_empty", return_value="{}"):
                result = director_evaluate("build X", ctx, "stuck", adapter)

        assert result.action == "replan"
        assert result.new_approach == "try a different strategy"
        assert result.revised_steps is None

    def test_replan_with_zero_budget_still_returned_by_evaluate(self):
        """director_evaluate returns replan regardless of budget — enforcement is in agent_loop."""
        from unittest.mock import MagicMock, patch

        adapter = MagicMock()
        ctx = _eval_ctx()
        ctx.convergence_budget_remaining = 0
        data = {"action": "replan", "reasoning": "need fresh start", "next_check_in": 1}

        with patch("director.extract_json", return_value=data):
            with patch("director.content_or_empty", return_value="{}"):
                result = director_evaluate("build X", ctx, "stuck", adapter)

        # director_evaluate itself does NOT clamp replan — agent_loop does
        assert result.action == "replan"

    def test_restart_and_escalate_clamped_to_continue(self):
        """restart and escalate (Phase C) are not yet wired — clamp to continue."""
        from unittest.mock import MagicMock, patch

        adapter = MagicMock()
        ctx = _eval_ctx()

        for disallowed_action in ("restart", "escalate"):
            data = {"action": disallowed_action, "reasoning": "whatever", "next_check_in": 1}
            with patch("director.extract_json", return_value=data):
                with patch("director.content_or_empty", return_value="{}"):
                    result = director_evaluate("build X", ctx, "stuck", adapter)
            assert result.action == "continue", f"{disallowed_action} should be clamped"

    def test_next_check_in_clamped_to_minimum_1(self):
        from unittest.mock import MagicMock, patch

        adapter = MagicMock()
        ctx = _eval_ctx()
        data = {"action": "continue", "reasoning": "ok", "next_check_in": 0}

        with patch("director.extract_json", return_value=data):
            with patch("director.content_or_empty", return_value="{}"):
                result = director_evaluate("build X", ctx, "step_threshold", adapter)

        assert result.next_check_in >= 1

    def test_bad_json_returns_continue(self):
        from unittest.mock import MagicMock, patch

        adapter = MagicMock()
        ctx = _eval_ctx()

        with patch("director.extract_json", return_value=None):
            with patch("director.content_or_empty", return_value="not json"):
                result = director_evaluate("build X", ctx, "step_threshold", adapter)

        assert result.action == "continue"

    def test_adapter_exception_returns_continue(self):
        from unittest.mock import MagicMock

        adapter = MagicMock()
        adapter.complete.side_effect = RuntimeError("network error")
        ctx = _eval_ctx()

        result = director_evaluate("build X", ctx, "verify_failure", adapter)
        assert result.action == "continue"
