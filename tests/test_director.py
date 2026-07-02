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

def test_cli_director_text(monkeypatch, tmp_path, capsys):
    _setup(monkeypatch, tmp_path)
    import cli
    rc = cli.main(["director", "research polymarket strategies", "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "director_id=" in out
    assert "REPORT" in out


def test_cli_director_json(monkeypatch, tmp_path, capsys):
    _setup(monkeypatch, tmp_path)
    import cli
    rc = cli.main(["director", "build a report", "--dry-run", "--format", "json"])
    assert rc == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert "director_id" in data
    assert "report" in data
    assert data["status"] == "done"


def test_cli_director_explicit_acceptance(monkeypatch, tmp_path, capsys):
    _setup(monkeypatch, tmp_path)
    import cli
    rc = cli.main(["director", "send email to users", "--dry-run", "--format", "json"])
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

class TestProbeModalityClassifier:
    """Tests for _classify_probe_modality — closure quality observability."""

    def test_curl_is_http(self):
        from director import _classify_probe_modality
        assert _classify_probe_modality("curl -f http://localhost:8080/health") == "http"

    def test_wscat_is_ws(self):
        from director import _classify_probe_modality
        assert _classify_probe_modality("wscat -c ws://localhost:8080/ws") == "ws"
        assert _classify_probe_modality("nc -z localhost 8080 && curl -i ws://x/y") == "ws"

    def test_playwright_is_browser(self):
        from director import _classify_probe_modality
        assert _classify_probe_modality("npx playwright test e2e.spec.ts") == "browser"

    def test_go_build_is_static(self):
        from director import _classify_probe_modality
        assert _classify_probe_modality("cd /repo && go build ./...") == "static"

    def test_grep_test_f_is_static(self):
        from director import _classify_probe_modality
        assert _classify_probe_modality("grep -q foo bar.go") == "static"
        assert _classify_probe_modality("test -f web/index.html") == "static"

    def test_server_boot_curl_is_http_not_static(self):
        from director import _classify_probe_modality
        cmd = "timeout 5 ./slycrel-server --addr :18080 & sleep 1; curl -f localhost:18080/health; kill %1"
        # The curl makes this behavioral even though there's a static-looking tail.
        assert _classify_probe_modality(cmd) == "http"

    def test_bare_binary_invocation_is_process(self):
        from director import _classify_probe_modality
        assert _classify_probe_modality("./slycrel --help") == "process"
        assert _classify_probe_modality("go run ./cmd/cli -validate") == "process"

    def test_empty_is_static(self):
        from director import _classify_probe_modality
        assert _classify_probe_modality("") == "static"


class TestDetectBehavioralGap:
    """Tests for _detect_behavioral_gap — the complete=True downgrade.

    The feature catches closure's exact slycrel-go failure mode: LLM returns
    complete=True while its own summary admits runtime wasn't exercised and
    modality_distribution has zero behavioral probes. The fix reads the LLM's
    own words, not an external "if goal is a server, require http" taxonomy.
    """

    def _call(self, **overrides):
        from director import _detect_behavioral_gap
        kwargs = dict(
            complete=True,
            summary="",
            gaps=[],
            modality_dist={"static": 5},  # all-static = the runtime gap case
            scope=None,
        )
        kwargs.update(overrides)
        return _detect_behavioral_gap(**kwargs)

    def test_complete_false_never_flags(self):
        # When the LLM already said incomplete, the downgrade path is moot.
        assert self._call(complete=False, summary="runtime was not performed") == ""

    def test_behavioral_probe_present_never_flags(self):
        # Any behavioral modality (http/ws/browser/process) clears the gap.
        for mod in ("http", "ws", "browser", "process"):
            assert self._call(
                modality_dist={mod: 1, "static": 3},
                summary="runtime validation was not performed",
            ) == ""

    def test_slycrel_go_exact_admission_flags(self):
        # The exact phrasing from the 2026-04-17 slycrel-go run.
        reason = self._call(
            summary=(
                "The branch is architecturally sound. All structural components "
                "are in place and the code compiles cleanly. "
                "Gap: runtime validation (server startup + browser connection) "
                "was not performed."
            ),
        )
        assert reason  # non-empty
        assert "not" in reason.lower() or "runtime" in reason.lower()

    def test_admission_in_gaps_list_also_flags(self):
        # Admission can live in the gaps list instead of the summary.
        reason = self._call(
            summary="all checks passed",
            gaps=["No server boot was tested"],
        )
        assert reason

    def test_no_admission_and_no_scope_does_not_flag(self):
        # LLM returned all-static verdict with no self-contradiction and no
        # scope failure modes to cross-check — nothing to infer from.
        assert self._call(summary="all checks passed on config parsing") == ""

    def test_scope_failure_modes_with_runtime_hint_flag(self):
        # Scope already said "server must respond to /health" — closure with
        # zero behavioral probes contradicts scope, not just the LLM summary.
        class _FakeScope:
            failure_modes = ["Server does not respond to /health under concurrent load"]
        reason = self._call(
            summary="all checks passed",
            scope=_FakeScope(),
        )
        assert reason
        assert "scope" in reason.lower()

    def test_scope_without_runtime_hint_does_not_flag(self):
        # Scope cared only about code-level things → static probes are fine.
        class _FakeScope:
            failure_modes = ["Import cycle between modules", "Type annotation drift"]
        assert self._call(summary="all checks passed", scope=_FakeScope()) == ""

    def test_bad_scope_object_does_not_raise(self):
        # Defensive: a scope without failure_modes shouldn't crash.
        class _Bad:
            pass
        assert self._call(scope=_Bad()) == ""


class TestDetectDiagnosisGap:
    def _diag(self, failure_class="decomposition_too_broad", severity="warning", recommendation="split the work"):
        class _Diag:
            pass
        d = _Diag()
        d.failure_class = failure_class
        d.severity = severity
        d.recommendation = recommendation
        return d

    def test_non_broad_diagnosis_does_not_flag(self):
        from director import _detect_diagnosis_gap
        assert _detect_diagnosis_gap(
            complete=True,
            diagnosis=self._diag(failure_class="healthy"),
            modality_dist={"static": 2},
        ) == ""

    def test_behavioral_probe_clears_diagnosis_gap(self):
        from director import _detect_diagnosis_gap
        assert _detect_diagnosis_gap(
            complete=True,
            diagnosis=self._diag(),
            modality_dist={"http": 1, "static": 2},
        ) == ""

    def test_broad_diagnosis_with_only_static_checks_flags(self):
        from director import _detect_diagnosis_gap
        reason = _detect_diagnosis_gap(
            complete=True,
            diagnosis=self._diag(),
            modality_dist={"static": 4},
        )
        assert "decomposition_too_broad" in reason
        assert "no behavioral probe" in reason


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

    def test_empty_workspace_path_falls_back_to_run_scoped_cwd(self, monkeypatch, tmp_path):
        """Burn-in batch-1 regression: with no workspace_path (repo_path empty
        for non-repo goals), checks must run in the executor's run-scoped cwd
        (project dir) — not Maro's launch cwd, which made every artifact
        check a false negative."""
        from unittest.mock import MagicMock, patch
        import llm as llm_mod

        (tmp_path / "artifacts").mkdir()
        (tmp_path / "artifacts" / "thing.txt").write_text("built")
        token = llm_mod._DEFAULT_SUBPROCESS_CWD.set(str(tmp_path))
        try:
            adapter = MagicMock()
            adapter.complete.side_effect = [MagicMock(), MagicMock()]
            checks = [{"description": "artifact exists",
                       "command": "test -f artifacts/thing.txt"}]
            verdict_data = {"complete": True, "confidence": 0.9, "gaps": [],
                            "summary": "ok"}
            with patch("director.extract_json",
                       side_effect=[{"checks": checks}, verdict_data]):
                with patch("director.content_or_empty", return_value="{}"):
                    result = verify_goal_completion("build thing", [], adapter)
            assert result.checks_run == 1
            assert result.checks_passed == 1
        finally:
            llm_mod._DEFAULT_SUBPROCESS_CWD.reset(token)

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

    # ------------------------------------------------------------------
    # Regression: CLOSURE_VERDICT must be emitted on ALL early-exit paths
    # (root cause of run-03-treat missing the event in captain's log).
    # ------------------------------------------------------------------

    def test_closure_verdict_emitted_when_no_checks_generated(self, tmp_path):
        """CLOSURE_VERDICT must be emitted even when the plan LLM returns no
        checks (e.g. for research goals or malformed JSON).  Previously the
        function silently returned _null, leaving the captain's log without any
        record that closure had been attempted."""
        from unittest.mock import MagicMock, patch

        adapter = MagicMock()
        captured = []

        def _spy(*args, **kwargs):
            captured.append(kwargs)

        with patch("director.extract_json", return_value={"checks": []}):
            with patch("director.content_or_empty", return_value="{}"):
                with patch("captains_log.log_event", side_effect=_spy):
                    verify_goal_completion(
                        "summarize docs", [], adapter,
                        loop_id="loop-abc",
                    )

        assert len(captured) == 1, "expected exactly one CLOSURE_VERDICT event"
        ctx = captured[0].get("context", {})
        assert ctx.get("skip_reason") == "no_checks_generated"
        assert ctx.get("checks_run") == 0
        assert captured[0].get("loop_id") == "loop-abc"

    def test_closure_verdict_emitted_when_no_check_results(self, tmp_path):
        """CLOSURE_VERDICT emitted when checks were generated but none ran."""
        from unittest.mock import MagicMock, patch

        adapter = MagicMock()
        captured = []

        def _spy(*args, **kwargs):
            captured.append(kwargs)

        # Return a check with no command so check_results stays empty
        with patch("director.extract_json", return_value={"checks": [{"description": "x", "command": ""}]}):
            with patch("director.content_or_empty", return_value="{}"):
                with patch("captains_log.log_event", side_effect=_spy):
                    verify_goal_completion(
                        "do a thing", [], adapter,
                        workspace_path=str(tmp_path),
                        loop_id="loop-def",
                    )

        assert len(captured) == 1
        ctx = captured[0].get("context", {})
        assert ctx.get("skip_reason") == "no_check_results"

    def test_closure_verdict_emitted_on_exception(self):
        """CLOSURE_VERDICT emitted even when the adapter raises."""
        from unittest.mock import MagicMock, patch

        adapter = MagicMock()
        adapter.complete.side_effect = RuntimeError("API down")
        captured = []

        def _spy(*args, **kwargs):
            captured.append(kwargs)

        with patch("captains_log.log_event", side_effect=_spy):
            verify_goal_completion("build X", [], adapter, loop_id="loop-ghi")

        assert len(captured) == 1
        ctx = captured[0].get("context", {})
        assert ctx.get("skip_reason") == "exception"
        assert captured[0].get("loop_id") == "loop-ghi"

    def test_closure_verdict_not_emitted_on_dry_run(self):
        """dry_run=True is an intentional skip — no captain's log entry expected."""
        from unittest.mock import patch

        captured = []

        def _spy(*args, **kwargs):
            captured.append(kwargs)

        with patch("captains_log.log_event", side_effect=_spy):
            verify_goal_completion("build X", [], None, dry_run=True)

        assert len(captured) == 0, "dry_run must not emit CLOSURE_VERDICT"

    def test_closure_verdict_event_carries_loop_id(self, tmp_path):
        """CLOSURE_VERDICT captains_log event must include loop_id when supplied.

        Regression guard: prior implementation logged the verdict without loop_id,
        so the event couldn't be linked to its loop in run-dir slices or lineage
        chains. QUALITY_GATE_VERDICT and LOOP_CREATED already had it; closure
        was the missing third leg.
        """
        from unittest.mock import MagicMock, patch

        adapter = MagicMock()
        checks = [{"description": "true", "command": "true"}]
        verdict_data = {"complete": True, "confidence": 0.9, "gaps": [], "summary": "Done."}

        captured = {}

        def _spy_log_event(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return {}

        with patch("director.extract_json", side_effect=[{"checks": checks}, verdict_data]):
            with patch("director.content_or_empty", return_value="{}"):
                with patch("captains_log.log_event", side_effect=_spy_log_event):
                    verify_goal_completion(
                        "do a thing", [], adapter,
                        workspace_path=str(tmp_path),
                        loop_id="abc12345",
                    )

        assert captured.get("kwargs", {}).get("loop_id") == "abc12345"

    def test_closure_verdict_event_loop_id_omitted_when_blank(self, tmp_path):
        """No loop_id supplied → log_event called with loop_id=None (existing
        skip-when-empty behavior in captains_log.log_event preserves omission)."""
        from unittest.mock import MagicMock, patch

        adapter = MagicMock()
        checks = [{"description": "true", "command": "true"}]
        verdict_data = {"complete": True, "confidence": 0.9, "gaps": [], "summary": "Done."}
        captured = {}

        def _spy_log_event(*args, **kwargs):
            captured["kwargs"] = kwargs
            return {}

        with patch("director.extract_json", side_effect=[{"checks": checks}, verdict_data]):
            with patch("director.content_or_empty", return_value="{}"):
                with patch("captains_log.log_event", side_effect=_spy_log_event):
                    verify_goal_completion(
                        "do a thing", [], adapter,
                        workspace_path=str(tmp_path),
                    )

        # Default kwarg loop_id="" → passed as None (falsy → captains_log omits it)
        assert captured.get("kwargs", {}).get("loop_id") is None

    def test_plan_prompt_uses_inversion_framing(self):
        """_CLOSURE_PLAN_SYSTEM must use inversion framing, not goal-type taxonomy.

        Regression guard: the prior version encoded a four-category taxonomy
        ('static artifact / running service / changes to existing codebase /
        research') with category-specific mandates. That's prompt-patching: it
        front-loads the correct answer instead of letting the system infer it
        from the goal and its failure modes. The prompt must instead reason by
        inversion — probe whether failure modes (from scope, or on-the-fly)
        actually occurred — and each check must label its failure_mode.
        """
        from director import _CLOSURE_PLAN_SYSTEM
        text = _CLOSURE_PLAN_SYSTEM.lower()
        # Must frame itself as inversion-based
        assert "inversion" in text
        assert "failure mode" in text
        # Must require each check to name its failure mode in output
        assert "failure_mode" in text
        # Must NOT hardcode the service-category mandate
        assert "running service" not in text
        assert "behavioral check" not in text

    def test_plan_prompt_has_fallback_when_scope_absent(self):
        """Prompt must handle the no-scope case by doing its own inversion."""
        from director import _CLOSURE_PLAN_SYSTEM
        text = _CLOSURE_PLAN_SYSTEM.lower()
        # Explicitly covers the case of no input failure modes
        assert "no failure modes" in text or "your own inversion" in text

    def test_plan_prompt_prefers_runtime_probe_for_runtime_claims(self):
        """Prompt should bias toward behavioral probes when success depends on runtime behavior."""
        from director import _CLOSURE_PLAN_SYSTEM
        text = _CLOSURE_PLAN_SYSTEM.lower()
        assert "behavioral/runtime probe" in text or "runtime probe" in text
        assert "static" in text

    def test_plan_prompt_includes_runtime_scaffolding_examples(self):
        """Prompt should show cheap lifecycle scaffolding for runtime probes."""
        from director import _CLOSURE_PLAN_SYSTEM
        text = _CLOSURE_PLAN_SYSTEM.lower()
        assert "trap 'kill $pid' exit" in text
        assert "curl -fss http://127.0.0.1:8000/health" in text
        assert "101 switching protocols" in text
        assert "./bin/tool --help" in text

    def test_plan_prompt_warns_against_brittle_grep_theater(self):
        """Prompt should bias toward robust predicates, not fragile string matching."""
        from director import _CLOSURE_PLAN_SYSTEM
        text = _CLOSURE_PLAN_SYSTEM.lower()
        assert "brittle string-matching theater" in text
        assert "endpoint status codes" in text
        assert "process exit codes" in text
        assert "grep -e" in text

    def test_check_modality_classification(self):
        """Closure checks should expose a coarse probe modality for evals."""
        from director import _check_modality_from_command

        assert _check_modality_from_command("grep -q foo app.py") == "static"
        assert _check_modality_from_command("curl -fsS http://localhost:8000/health") == "http"
        assert _check_modality_from_command("wscat -c ws://localhost:8000/ws") == "ws"
        assert _check_modality_from_command("timeout 5 python -m http.server 8000") == "process"
        assert _check_modality_from_command("playwright test smoke.spec.ts") == "browser"

    def test_scope_failure_modes_reach_plan_prompt(self, monkeypatch, tmp_path):
        """When scope is supplied, its failure modes appear in the plan-call user message."""
        from unittest.mock import MagicMock, patch
        from scope import ScopeSet

        adapter = MagicMock()
        captured_messages = []

        def _complete(messages, **kwargs):
            captured_messages.append(messages)
            return MagicMock()

        adapter.complete.side_effect = _complete

        scope = ScopeSet(
            failure_modes=[
                "server compiles but never accepts a connection",
                "websocket handshake fails under TLS",
            ],
            in_scope=["bidirectional messaging"],
            out_of_scope=["auth"],
            raw_text="",
        )

        with patch("director.extract_json",
                   side_effect=[{"checks": []}, {"complete": True}]):
            with patch("director.content_or_empty", return_value="{}"):
                verify_goal_completion(
                    "build a websocket server", [], adapter,
                    workspace_path=str(tmp_path), scope=scope,
                )

        # First call is the plan call; messages[1] is the user message
        assert captured_messages, "expected adapter.complete to be called"
        user_msg = captured_messages[0][1].content
        assert "server compiles but never accepts a connection" in user_msg
        assert "websocket handshake fails under TLS" in user_msg
        assert "failure modes" in user_msg.lower()

    def test_no_scope_no_failure_mode_block(self, monkeypatch, tmp_path):
        """When scope is None, user message must NOT contain the failure-modes header."""
        from unittest.mock import MagicMock, patch

        adapter = MagicMock()
        captured_messages = []

        def _complete(messages, **kwargs):
            captured_messages.append(messages)
            return MagicMock()

        adapter.complete.side_effect = _complete

        with patch("director.extract_json",
                   side_effect=[{"checks": []}, {"complete": True}]):
            with patch("director.content_or_empty", return_value="{}"):
                verify_goal_completion(
                    "build X", [], adapter,
                    workspace_path=str(tmp_path), scope=None,
                )

        user_msg = captured_messages[0][1].content
        assert "Failure modes identified when planning" not in user_msg

    def test_resolved_intent_deliverables_injected_into_plan(self, monkeypatch, tmp_path):
        """ResolvedIntent.deliverables become explicit verification targets in the plan call."""
        from unittest.mock import MagicMock, patch
        from scope import Deliverable, ResolvedIntent, ScopeSet

        adapter = MagicMock()
        captured_messages = []

        def _complete(messages, **kwargs):
            captured_messages.append(messages)
            return MagicMock()

        adapter.complete.side_effect = _complete

        ri = ResolvedIntent(
            scope=ScopeSet(failure_modes=[], in_scope=[], out_of_scope=[], raw_text=""),
            deliverables=[
                Deliverable(
                    name="cmd/server/main.go",
                    description="HTTP server entry point",
                    preconditions=["go", "port 8080"],
                ),
                Deliverable(
                    name="static/index.html",
                    description="client UI",
                    preconditions=[],
                ),
            ],
            raw_text="",
        )

        with patch("director.extract_json",
                   side_effect=[{"checks": []}, {"complete": True}]):
            with patch("director.content_or_empty", return_value="{}"):
                verify_goal_completion(
                    "build the websocket app", [], adapter,
                    workspace_path=str(tmp_path), resolved_intent=ri,
                )

        user_msg = captured_messages[0][1].content
        assert "Deliverables committed when planning" in user_msg
        assert "cmd/server/main.go" in user_msg
        assert "HTTP server entry point" in user_msg
        assert "preconditions: go, port 8080" in user_msg
        assert "static/index.html" in user_msg
        # Deliverables section comes alongside scope section, not instead of work summary
        assert "Work done" in user_msg

    def test_no_resolved_intent_no_deliverable_block(self, monkeypatch, tmp_path):
        """When resolved_intent is None, user message must NOT contain the deliverables header."""
        from unittest.mock import MagicMock, patch

        adapter = MagicMock()
        captured_messages = []

        def _complete(messages, **kwargs):
            captured_messages.append(messages)
            return MagicMock()

        adapter.complete.side_effect = _complete

        with patch("director.extract_json",
                   side_effect=[{"checks": []}, {"complete": True}]):
            with patch("director.content_or_empty", return_value="{}"):
                verify_goal_completion(
                    "build X", [], adapter,
                    workspace_path=str(tmp_path), resolved_intent=None,
                )

        user_msg = captured_messages[0][1].content
        assert "Deliverables committed when planning" not in user_msg

    def test_precondition_preflight_classifies_inputs(self):
        from director import _classify_precondition
        assert _classify_precondition("go") == "command"
        assert _classify_precondition("python3") == "command"
        assert _classify_precondition("./run.sh") == "path"
        assert _classify_precondition("cmd/server/main.go") == "path"
        assert _classify_precondition("/usr/bin/whatever") == "path"
        assert _classify_precondition("port 8080") == "opaque"
        assert _classify_precondition("") == "opaque"

    def test_precondition_classifies_sentinel_non_values_opaque(self):
        """Sentinel strings are not commands — must not get shutil.which'd.

        Regression: 2026-04-26 scope A/B closure preflight ran
        `shutil.which("none")` and reported it as a missing command, polluting
        the closure check feed with synthetic failures.
        """
        from director import _classify_precondition
        for sentinel in ("none", "None", "NONE", "n/a", "N/A", "-", "tbd", "(none)", "null"):
            assert _classify_precondition(sentinel) == "opaque", (
                f"sentinel {sentinel!r} must be opaque, not classified as command/path"
            )

    def test_precondition_classifies_go_module_paths_opaque(self):
        """Go module paths (and other import paths) are not filesystem paths.

        Regression: 2026-04-26 scope A/B closure preflight tried
        `Path('gorilla/websocket').exists()` and `Path('github.com/x/y').exists()`
        — module paths look slash-shaped but are not on the filesystem.
        """
        from director import _classify_precondition
        # Two-segment lowercase slashy strings — typical Go module idiom
        assert _classify_precondition("gorilla/websocket") == "opaque"
        assert _classify_precondition("urfave/cli") == "opaque"
        # Domain-prefixed full module paths
        assert _classify_precondition("github.com/x/y") == "opaque"
        assert _classify_precondition("golang.org/x/term") == "opaque"
        assert _classify_precondition("gopkg.in/yaml.v3") == "opaque"
        # URLs always opaque
        assert _classify_precondition("https://example.com/api") == "opaque"
        assert _classify_precondition("ws://localhost:8080") == "opaque"

    def test_precondition_classifies_real_filesystem_paths_correctly(self):
        """After tightening, genuine filesystem paths must still classify as 'path'."""
        from director import _classify_precondition
        # Absolute paths
        assert _classify_precondition("/usr/local/bin/x") == "path"
        # Relative with explicit prefix
        assert _classify_precondition("./scripts/run.sh") == "path"
        assert _classify_precondition("../shared/config.yml") == "path"
        # Home-relative
        assert _classify_precondition("~/.config/poe.yml") == "path"
        # Nested project paths (3+ segments — not module-shaped)
        assert _classify_precondition("internal/io/websocket_impl.go") == "path"
        assert _classify_precondition("cmd/server/main.go") == "path"

    def test_precondition_classifies_versioned_strings_opaque(self):
        """Strings with dots but no slashes (versions, dotted names) → opaque."""
        from director import _classify_precondition
        # Version strings shouldn't try shutil.which("1.2.3")
        assert _classify_precondition("1.2.3") == "opaque"
        assert _classify_precondition("v1.0") == "opaque"
        # A bare dotted-name shouldn't be a command either
        assert _classify_precondition("python3.12") == "opaque"

    def test_precondition_preflight_command_present(self, tmp_path):
        from director import _run_precondition_preflight
        from scope import Deliverable

        # `sh` is on PATH on every Linux/Unix box; safe to assume.
        d = Deliverable(name="x", description="", preconditions=["sh"])
        results = _run_precondition_preflight([d], cwd=str(tmp_path))
        assert len(results) == 1
        assert results[0]["passed"] is True
        assert results[0]["modality"] == "preflight"
        assert "sh" in results[0]["description"]

    def test_precondition_preflight_command_missing(self, tmp_path):
        from director import _run_precondition_preflight
        from scope import Deliverable

        d = Deliverable(name="x", description="", preconditions=["this-command-does-not-exist-xyzzy"])
        results = _run_precondition_preflight([d], cwd=str(tmp_path))
        assert len(results) == 1
        assert results[0]["passed"] is False
        assert results[0]["exit_code"] == 127
        assert "not on PATH" in results[0]["stderr"]

    def test_precondition_preflight_path_present(self, tmp_path):
        from director import _run_precondition_preflight
        from scope import Deliverable

        # Create a real file in cwd and reference it.
        (tmp_path / "run.sh").write_text("#!/bin/sh\n")
        d = Deliverable(name="x", description="", preconditions=["./run.sh"])
        results = _run_precondition_preflight([d], cwd=str(tmp_path))
        assert len(results) == 1
        assert results[0]["passed"] is True
        assert results[0]["modality"] == "preflight"

    def test_precondition_preflight_path_missing(self, tmp_path):
        from director import _run_precondition_preflight
        from scope import Deliverable

        d = Deliverable(name="x", description="", preconditions=["./not-here.sh"])
        results = _run_precondition_preflight([d], cwd=str(tmp_path))
        assert len(results) == 1
        assert results[0]["passed"] is False
        assert results[0]["exit_code"] == 127
        assert results[0]["outcome"] == "inconclusive"
        assert "does not exist" in results[0]["stderr"]

    def test_precondition_preflight_skips_opaque(self, tmp_path):
        from director import _run_precondition_preflight
        from scope import Deliverable

        d = Deliverable(name="x", description="", preconditions=["port 8080", "env var X required"])
        results = _run_precondition_preflight([d], cwd=str(tmp_path))
        assert results == []

    def test_failing_preconditions_become_check_results(self, monkeypatch, tmp_path):
        """Failed preflight checks get prepended to check_results so director sees them."""
        from unittest.mock import MagicMock, patch
        from scope import Deliverable, ResolvedIntent, ScopeSet

        adapter = MagicMock()

        # The plan call returns one regular check.
        # Preflight will add 1 failing check (missing command) before that.
        def _complete(messages, **kwargs):
            return MagicMock()
        adapter.complete.side_effect = _complete

        ri = ResolvedIntent(
            scope=ScopeSet(failure_modes=[], in_scope=[], out_of_scope=[], raw_text=""),
            deliverables=[
                Deliverable(
                    name="cmd/server/main.go",
                    description="",
                    preconditions=["this-cmd-does-not-exist-xyzzy"],
                ),
            ],
            raw_text="",
        )

        captured_verdict_user = []

        def _extract_json_side_effect(content, _type, log_tag=None):
            # Plan call: return one check
            if "closure_plan" in (log_tag or ""):
                return {"checks": [{"description": "fake check", "command": "true"}]}
            # Verdict call: capture the user message context for inspection
            return {"complete": True, "confidence": 0.9, "gaps": [], "summary": "ok"}

        with patch("director.extract_json", side_effect=_extract_json_side_effect):
            with patch("director.content_or_empty", return_value="{}"):
                verdict = verify_goal_completion(
                    "build the thing", [], adapter,
                    workspace_path=str(tmp_path), resolved_intent=ri,
                )

        assert verdict is not None
        # 2 checks total: 1 failing preflight + 1 fake passing check
        assert verdict.checks_run == 2
        assert verdict.checks_passed == 1

    def test_passing_preconditions_not_prepended(self, monkeypatch, tmp_path):
        """When all preflight checks pass, they shouldn't pollute check_results."""
        from unittest.mock import MagicMock, patch
        from scope import Deliverable, ResolvedIntent, ScopeSet

        adapter = MagicMock()
        adapter.complete.side_effect = lambda *a, **kw: MagicMock()

        # `sh` is always present.
        ri = ResolvedIntent(
            scope=ScopeSet(failure_modes=[], in_scope=[], out_of_scope=[], raw_text=""),
            deliverables=[
                Deliverable(name="x", description="", preconditions=["sh"]),
            ],
            raw_text="",
        )

        def _extract_json_side_effect(content, _type, log_tag=None):
            if "closure_plan" in (log_tag or ""):
                return {"checks": [{"description": "fake", "command": "true"}]}
            return {"complete": True, "confidence": 0.9, "gaps": [], "summary": "ok"}

        with patch("director.extract_json", side_effect=_extract_json_side_effect):
            with patch("director.content_or_empty", return_value="{}"):
                verdict = verify_goal_completion(
                    "x", [], adapter,
                    workspace_path=str(tmp_path), resolved_intent=ri,
                )

        # Only the LLM-generated check; passing preflight is suppressed.
        assert verdict.checks_run == 1

    def test_empty_deliverables_list_skips_block(self, monkeypatch, tmp_path):
        """ResolvedIntent with empty deliverables list shouldn't render the block."""
        from unittest.mock import MagicMock, patch
        from scope import ResolvedIntent, ScopeSet

        adapter = MagicMock()
        captured_messages = []

        def _complete(messages, **kwargs):
            captured_messages.append(messages)
            return MagicMock()

        adapter.complete.side_effect = _complete

        ri = ResolvedIntent(
            scope=ScopeSet(failure_modes=[], in_scope=[], out_of_scope=[], raw_text=""),
            deliverables=[],
            raw_text="",
        )

        with patch("director.extract_json",
                   side_effect=[{"checks": []}, {"complete": True}]):
            with patch("director.content_or_empty", return_value="{}"):
                verify_goal_completion(
                    "build X", [], adapter,
                    workspace_path=str(tmp_path), resolved_intent=ri,
                )

        user_msg = captured_messages[0][1].content
        assert "Deliverables committed when planning" not in user_msg

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

    def test_command_not_found_downgrades_complete_to_inconclusive(self, tmp_path):
        """A probe that couldn't run must not count as evidence of completion."""
        import subprocess
        from unittest.mock import MagicMock, patch

        adapter = MagicMock()
        checks = [{"description": "behavioral probe", "command": "go build ./... && ./server"}]
        verdict_data = {"complete": True, "confidence": 0.9, "gaps": [], "summary": "Looks complete."}

        completed = subprocess.CompletedProcess(
            args=checks[0]["command"], returncode=127, stdout="", stderr="go: command not found"
        )

        with patch("director.extract_json", side_effect=[{"checks": checks}, verdict_data]):
            with patch("director.content_or_empty", return_value="{}"):
                with patch("subprocess.run", return_value=completed):
                    result = verify_goal_completion(
                        "build X", [], adapter,
                        workspace_path=str(tmp_path),
                    )

        assert result.complete is False
        assert result.confidence == 0.6
        assert result.checks_run == 1
        assert result.checks_passed == 0
        assert result.inconclusive_count == 1
        assert any("inconclusive" in gap.lower() for gap in result.gaps)

    def test_inconclusive_probe_does_not_poison_passing_evidence(self, tmp_path):
        """Burn-in batch-2 regression: one inconclusive probe (often the
        verifier's own malformed command) must not flip a verdict backed by
        passing checks — positive mechanical evidence wins over missing data."""
        import subprocess
        from unittest.mock import MagicMock, patch

        adapter = MagicMock()
        checks = [
            {"description": "artifact exists", "command": "test -f out.md"},
            {"description": "broken probe", "command": "frobnicate --check"},
        ]
        verdict_data = {"complete": True, "confidence": 0.95, "gaps": [],
                        "summary": "Goal achieved."}

        results = [
            subprocess.CompletedProcess(args=checks[0]["command"], returncode=0,
                                        stdout="ok", stderr=""),
            subprocess.CompletedProcess(args=checks[1]["command"], returncode=127,
                                        stdout="", stderr="frobnicate: command not found"),
        ]

        with patch("director.extract_json", side_effect=[{"checks": checks}, verdict_data]):
            with patch("director.content_or_empty", return_value="{}"):
                with patch("subprocess.run", side_effect=results):
                    result = verify_goal_completion(
                        "build X", [], adapter, workspace_path=str(tmp_path),
                    )

        assert result.complete is True
        assert result.confidence == 0.95
        assert result.checks_passed == 1
        assert result.inconclusive_count == 1

    def test_check_results_include_modality_in_verdict_context(self, monkeypatch, tmp_path):
        """Verification results passed to the verdict step should include probe modality."""
        from unittest.mock import MagicMock, patch

        adapter = MagicMock()
        captured_messages = []

        def _complete(messages, **kwargs):
            captured_messages.append(messages)
            return MagicMock()

        adapter.complete.side_effect = _complete

        checks = [{"description": "health endpoint", "command": "curl -fsS http://localhost:9999/health"}]

        with patch("director.extract_json", side_effect=[{"checks": checks}, {"complete": True, "confidence": 0.8, "gaps": [], "summary": "ok"}]):
            with patch("director.content_or_empty", return_value="{}"):
                with patch("subprocess.run") as run:
                    run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
                    verify_goal_completion("build health check", [], adapter, workspace_path=str(tmp_path))

        verdict_user_msg = captured_messages[1][1].content
        assert '"modality": "http"' in verdict_user_msg

    def test_broad_diagnosis_downgrades_complete_without_behavioral_probe(self, monkeypatch, tmp_path):
        """Closure should not bless an over-broad run on static checks alone."""
        from unittest.mock import MagicMock, patch

        class _Diag:
            failure_class = "decomposition_too_broad"
            severity = "warning"
            recommendation = "split the work into narrower steps"

        adapter = MagicMock()
        checks = [{"description": "repo grep", "command": "grep -q handler server.go"}]
        verdict_data = {"complete": True, "confidence": 0.9, "gaps": [], "summary": "All good."}

        with patch("director.extract_json", side_effect=[{"checks": checks}, verdict_data]):
            with patch("director.content_or_empty", return_value="{}"):
                with patch("subprocess.run") as run:
                    run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
                    result = verify_goal_completion(
                        "build X",
                        [],
                        adapter,
                        workspace_path=str(tmp_path),
                        diagnosis=_Diag(),
                    )

        assert result.complete is False
        assert result.confidence >= 0.6
        assert any("decomposition_too_broad" in gap for gap in result.gaps)


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

    def test_restart_returns_restart_with_context(self):
        """restart action is now wired (Phase C)."""
        from unittest.mock import MagicMock, patch

        adapter = MagicMock()
        ctx = _eval_ctx()
        data = {
            "action": "restart",
            "reasoning": "approach is fundamentally wrong",
            "restart_context": "tried X, learned Y, need fresh start with Z",
            "next_check_in": 3,
        }

        with patch("director.extract_json", return_value=data):
            with patch("director.content_or_empty", return_value="{}"):
                result = director_evaluate("build X", ctx, "stuck", adapter)

        assert result.action == "restart"
        assert result.restart_context == "tried X, learned Y, need fresh start with Z"

    def test_escalate_returns_escalate_with_question(self):
        """escalate action is now wired (Phase C)."""
        from unittest.mock import MagicMock, patch

        adapter = MagicMock()
        ctx = _eval_ctx()
        data = {
            "action": "escalate",
            "reasoning": "conflicting goals",
            "user_question": "Should we prioritize X or Y?",
            "next_check_in": 3,
        }

        with patch("director.extract_json", return_value=data):
            with patch("director.content_or_empty", return_value="{}"):
                result = director_evaluate("build X", ctx, "verify_failure", adapter)

        assert result.action == "escalate"
        assert result.user_question == "Should we prioritize X or Y?"

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
