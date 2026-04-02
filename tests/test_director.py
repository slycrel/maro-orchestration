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
    run_director,
    requires_explicit_acceptance,
    _produce_spec,
    _review_worker_output,
    _is_simple_directive,
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
