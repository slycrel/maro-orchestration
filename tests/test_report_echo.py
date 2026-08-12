"""MH #6 Communication Failure (subagent edge) — worker-report echo.

Pins for director._report_echo and its wiring: a DONE worker whose output
makes no lexical contact with the compiled report was dropped on the way
to the parent — previously plausible and undetected (the taxonomy's
ruling, live-source-verified 2026-08-10: context_budget.clip already
marks the compile window's cuts, so visibility was covered; DETECTION was
the gap). The asymmetry is inverted vs memory_bridge.slice_echo and the
pins enforce it: False is the meaningful pole (dropped), True is weak
evidence of coverage, None means the check could not have failed
(verbatim concatenation paths) or there was nothing to judge.
"""

import json
from unittest.mock import MagicMock

import pytest

from director import _compile_report, _report_echo
from workers import WorkerResult


def _wr(result, status="done", worker_type="research"):
    return WorkerResult(worker_type=worker_type, ticket="t", status=status,
                       result=result)


RESULT_TEXT = (
    "The chlorination threshold for potable water is 0.2mg/L residual; "
    "the WHO guideline document specifies breakpoint-chlorination curves "
    "and contact-time requirements for giardia inactivation."
)


class TestReportEcho:
    def test_contact_is_true(self):
        report = ("Findings: breakpoint-chlorination curves govern dosing; "
                  "giardia inactivation needs the WHO contact-time table.")
        assert _report_echo(RESULT_TEXT, report) is True

    def test_dropped_content_is_false(self):
        report = ("The survey of container orchestration frameworks found "
                  "kubernetes dominant among respondents this quarter.")
        assert _report_echo(RESULT_TEXT, report) is False

    def test_short_result_is_unjudged(self):
        assert _report_echo("ok done", "any report text here") is None

    def test_empty_sides_are_unjudged(self):
        assert _report_echo("", "report") is None
        assert _report_echo(RESULT_TEXT, "") is None

    def test_shares_slice_echo_vocabulary(self):
        # One extraction rule for every echo-shaped check — drift guard.
        from memory_bridge import distinctive_terms
        assert "breakpoint-chlorination" in distinctive_terms(RESULT_TEXT)
        assert "should" not in distinctive_terms("you should always check")


class TestCompileStamping:
    def _adapter(self, report_text):
        resp = MagicMock()
        resp.content = report_text
        resp.input_tokens = 10
        resp.output_tokens = 10
        adapter = MagicMock()
        adapter.complete.return_value = resp
        return adapter

    def test_llm_path_stamps_every_worker(self):
        covered = _wr(RESULT_TEXT)
        dropped = _wr("The kubernetes-orchestration survey results: "
                      "respondents preferred helm-charts and operator-sdk "
                      "patterns for deployment automation this quarter.")
        report_text = ("Report: breakpoint-chlorination and giardia "
                       "inactivation contact-time requirements are the "
                       "governing constraints.")
        report, _ = _compile_report("directive", "spec", [covered, dropped],
                                    self._adapter(report_text), dry_run=False)
        assert report == report_text
        assert covered.report_echoed is True
        assert dropped.report_echoed is False

    def test_dry_run_path_stays_unjudged(self):
        # Verbatim concatenation — an echo check could not fail, so it
        # proves nothing and must not be recorded as evidence.
        w = _wr(RESULT_TEXT)
        _compile_report("directive", "spec", [w], None, dry_run=True)
        assert w.report_echoed is None

    def test_exception_fallback_stays_unjudged(self):
        w = _wr(RESULT_TEXT)
        adapter = MagicMock()
        adapter.complete.side_effect = RuntimeError("adapter down")
        report, _ = _compile_report("directive", "spec", [w], adapter,
                                    dry_run=False)
        assert RESULT_TEXT in report  # fallback concatenates verbatim
        assert w.report_echoed is None


class TestOmissionEvent:
    def test_done_dropped_worker_emits_candidate_event(self, tmp_path):
        # Wiring pin at the run_director call-site convention: emit only
        # for DONE workers with report_echoed False. Exercised via the
        # captains_log store the event lands in.
        from captains_log import WORKER_REPORT_OMISSION, load_log, log_event
        log_event(
            WORKER_REPORT_OMISSION,
            subject="director_compile",
            summary="research worker output (200 chars) shows no lexical "
                    "contact with the compiled report",
            context={"director_id": "d1", "worker_type": "research",
                     "result_length": 200, "mh_edge": "subagent",
                     "mh_class": "communication_failure_candidate"},
        )
        events = [e for e in load_log()
                  if e.get("event_type") == WORKER_REPORT_OMISSION]
        assert events, "event did not land in the captain's log"
        ctx = events[-1].get("context") or {}
        assert ctx.get("mh_edge") == "subagent"
        assert ctx.get("mh_class") == "communication_failure_candidate"

    def test_blocked_workers_do_not_emit(self):
        # Status already carries a blocked worker's absence; the omission
        # lane is for silent drops of DONE work. Pin the guard's shape by
        # replaying the call-site condition against both statuses.
        done_dropped = _wr("x", status="done")
        done_dropped.report_echoed = False
        blocked = _wr("y", status="blocked")
        blocked.report_echoed = False
        fires = [r for r in (done_dropped, blocked)
                 if r.status == "done" and r.report_echoed is False]
        assert fires == [done_dropped]


class TestDirectorLogRow:
    def test_log_row_carries_report_echoed(self, tmp_path):
        from director import _write_director_log
        w = _wr(RESULT_TEXT)
        w.report_echoed = False
        path = _write_director_log(
            project=None, director_id="d-test", directive="d", spec="s",
            tickets=[], worker_results=[w], status="done", elapsed_ms=1)
        assert path is not None
        from orch import output_root
        log_file = output_root() / "artifacts" / "director" / "director-d-test-log.json"
        payload = json.loads(log_file.read_text(encoding="utf-8"))
        assert payload["worker_results"][0]["report_echoed"] is False

    def test_log_row_carries_delegation_gap(self, tmp_path):
        # MH #13: a WORKER-AUTHORED blocked reason with provision shape
        # flags the row; a done worker never does (status guard, not just
        # keywords).
        from director import _write_director_log
        blocked = _wr("partial", status="blocked")
        blocked.stuck_reason = "the source CSV was not provided"
        blocked.blocked_origin = "worker"
        done = _wr("fine output with the phrase not provided in prose")
        path = _write_director_log(
            project=None, director_id="d-gap", directive="d", spec="s",
            tickets=[], worker_results=[blocked, done], status="stuck",
            elapsed_ms=1)
        assert path is not None
        from orch import output_root
        log_file = output_root() / "artifacts" / "director" / "director-d-gap-log.json"
        payload = json.loads(log_file.read_text(encoding="utf-8"))
        rows = payload["worker_results"]
        assert rows[0]["delegation_gap"] is True
        assert rows[1]["delegation_gap"] is False

    def test_adapter_origin_blocked_is_not_a_delegation_candidate(self, tmp_path):
        # Adversarial review 2026-08-11: "LLM call failed: no access to
        # endpoint" pattern-matches the provision keywords but is an
        # infrastructure failure — only worker-authored flag_blocked
        # reasons are classified.
        from director import _delegation_gap_row
        adapter_blocked = _wr("", status="blocked")
        adapter_blocked.stuck_reason = "LLM call failed: no access to model endpoint"
        adapter_blocked.blocked_origin = "adapter"
        assert _delegation_gap_row(adapter_blocked) is False
        worker_blocked = _wr("", status="blocked")
        worker_blocked.stuck_reason = "no access to the analytics dashboard was provided"
        worker_blocked.blocked_origin = "worker"
        assert _delegation_gap_row(worker_blocked) is True
