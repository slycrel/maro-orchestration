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
