"""An unjudged run must be explicitly unjudged, never silently neither.

Backlog ("NOW + evolver_verify lanes are verdict-blind", census 2026-07-29):
"an honest denominator needs rows to be verdictable-or-exempt, not silently
neither."

The 2026-08-06 re-census found that item largely SHIPPED already -- chunk B
(2026-07-31) closed both named lanes, and post-ship coverage is 41/45 rows
judged. Two real gaps survived, both here:

  1. The done-without-closure tripwire watched `done` only, so a `stuck`
     run closure never judged was equally silent.
  2. The tripwire logged to the captain's log while the OUTCOMES ROW said
     nothing -- and the ledger is what a denominator counts.
"""
import json

import pytest

from stop_verdicts import (
    EXECUTION_FINISHED_STATUSES,
    PAUSED_STATUSES,
    VERDICT_SOURCE_NEVER_STAMPED,
)


class TestTheStatusVocabulary:
    def test_stuck_is_covered_not_just_done(self):
        """A stuck run finished executing; "stuck" is a process status, not
        a verdict, so an unjudged one is the same gap."""
        assert "done" in EXECUTION_FINISHED_STATUSES
        assert "stuck" in EXECUTION_FINISHED_STATUSES

    def test_paused_runs_are_not_owed_a_verdict_yet(self):
        """A paused run may or may not ever be finished (§13e) -- firing the
        tripwire on it would report a gap that does not exist."""
        assert not (EXECUTION_FINISHED_STATUSES & PAUSED_STATUSES)

    def test_backend_error_is_not_blamed_on_closure(self):
        """error = the backend died; closure never got a chance. Labelling
        that "closure never stamped" blames the wrong layer."""
        assert "error" not in EXECUTION_FINISHED_STATUSES


@pytest.fixture
def run_env(tmp_path, monkeypatch):
    """A workspace with one agenda run dir and one outcomes row.

    No importlib.reload here: both runs and memory_ledger resolve the
    workspace from env at CALL time, and reloading memory_ledger mints new
    class objects — dataclass __eq__ then fails across the old/new split for
    any already-imported module (test_verdict_learning's
    OutcomeVerdictStampResult comparisons broke exactly this way under
    xdist, 2026-08-06)."""
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    import runs as _runs
    import memory_ledger as _ml
    return tmp_path, _runs, _ml


def _seed(tmp_path, runs_mod, ledger, *, handle_id, loop_id, status,
          lane="agenda", verdict_source="", dry_run=False):
    d = runs_mod.run_dir(handle_id)
    d.mkdir(parents=True, exist_ok=True)
    meta = {"handle_id": handle_id, "loop_id": loop_id, "lane": lane,
            "status": status, "dry_run": dry_run}
    if verdict_source:
        meta["goal_verdict_source"] = verdict_source
    (d / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    ledger.record_outcome(goal="g", status=status, summary="s",
                          task_type="agenda", loop_id=loop_id)
    return d


def _row(ledger, loop_id):
    o = ledger.load_outcome_by_loop_id(loop_id)
    return o


class TestTheLedgerLearnsAboutIt:
    def test_stuck_run_gets_the_row_marked(self, run_env):
        tmp, runs_mod, ledger = run_env
        _seed(tmp, runs_mod, ledger, handle_id="h1", loop_id="L1", status="stuck")
        runs_mod.close_run("h1", status="stuck")
        row = _row(ledger, "L1")
        assert row is not None
        assert row.goal_verdict_source == VERDICT_SOURCE_NEVER_STAMPED

    def test_done_run_gets_the_row_marked(self, run_env):
        tmp, runs_mod, ledger = run_env
        _seed(tmp, runs_mod, ledger, handle_id="h2", loop_id="L2", status="done")
        runs_mod.close_run("h2", status="done")
        assert _row(ledger, "L2").goal_verdict_source == VERDICT_SOURCE_NEVER_STAMPED

    def test_marking_does_not_invent_a_verdict(self, run_env):
        """goal_achieved stays None: we do not know, and saying otherwise is
        the fabrication these guards exist to prevent."""
        tmp, runs_mod, ledger = run_env
        _seed(tmp, runs_mod, ledger, handle_id="h3", loop_id="L3", status="done")
        runs_mod.close_run("h3", status="done")
        assert _row(ledger, "L3").goal_achieved is None

    def test_a_judged_run_is_left_alone(self, run_env):
        """The tripwire must not overwrite a real verdict with 'never
        stamped' -- that would erase the thing it is auditing."""
        tmp, runs_mod, ledger = run_env
        _seed(tmp, runs_mod, ledger, handle_id="h4", loop_id="L4",
              status="done", verdict_source="closure")
        runs_mod.close_run("h4", status="done")
        assert _row(ledger, "L4").goal_verdict_source != VERDICT_SOURCE_NEVER_STAMPED

    def test_dry_runs_are_exempt(self, run_env):
        tmp, runs_mod, ledger = run_env
        _seed(tmp, runs_mod, ledger, handle_id="h5", loop_id="L5",
              status="done", dry_run=True)
        runs_mod.close_run("h5", status="done")
        assert _row(ledger, "L5").goal_verdict_source != VERDICT_SOURCE_NEVER_STAMPED

    def test_non_agenda_lanes_are_left_to_their_own_verdict_paths(self, run_env):
        """NOW stamps its verdict inline at record time, so it never needs
        this post-hoc marker -- that is why loop_id was never NOW's blocker."""
        tmp, runs_mod, ledger = run_env
        _seed(tmp, runs_mod, ledger, handle_id="h6", loop_id="L6",
              status="done", lane="now")
        runs_mod.close_run("h6", status="done")
        assert _row(ledger, "L6").goal_verdict_source != VERDICT_SOURCE_NEVER_STAMPED


class TestTheLogStillFires:
    def test_event_names_the_actual_status(self, run_env, monkeypatch):
        """It used to hardcode "status=done" in the summary; now that stuck
        is covered, a stuck run saying "status=done" would be a lie."""
        tmp, runs_mod, ledger = run_env
        seen = []
        import captains_log
        monkeypatch.setattr(
            captains_log, "log_event",
            lambda *a, **kw: seen.append((a, kw)))
        _seed(tmp, runs_mod, ledger, handle_id="h7", loop_id="L7", status="stuck")
        runs_mod.close_run("h7", status="stuck")
        assert seen, "no honesty event fired"
        summaries = [kw.get("summary", "") for _, kw in seen]
        assert any("status=stuck" in s for s in summaries), summaries
