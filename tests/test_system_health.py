"""Tests for system_health — dynamic-process liveness probes.

Two layers:
- Harness tests: transition narration, snapshot semantics, shielding,
  killswitch — via fake probes so the state machine is tested alone.
- Probe-semantics tests: the non-obvious probe behaviors (closure
  baseline-vs-growth, variant streak, contradiction pending-age) against
  seeded data.
"""

from __future__ import annotations

import json
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import system_health as sh
from system_health import (
    OK,
    SILENT,
    UNKNOWN,
    HISTORY_KEEP,
    STREAK_FOR_SILENT,
    CANDIDATE_STARVATION_HOURS,
    ProcessDeclaration,
    run_health_probes,
    load_snapshot,
    render_snapshot,
)
from captains_log import (
    set_log_path,
    load_log,
    SUBSYSTEM_SILENT,
    SUBSYSTEM_RECOVERED,
)


@pytest.fixture(autouse=True)
def _tmp_health(tmp_path, monkeypatch):
    """Isolate snapshot + captain's log per test."""
    snap_path = tmp_path / "system_health.json"
    monkeypatch.setattr(sh, "_snapshot_path", lambda: snap_path)
    log_path = tmp_path / "captains_log.jsonl"
    set_log_path(log_path)
    yield snap_path
    set_log_path(None)


def _decl(probe, name="fake_process"):
    return ProcessDeclaration(
        name=name,
        description="fake dynamic process",
        expectation="fires in tests",
        probe=probe,
    )


def _seq_probe(statuses):
    """Probe returning each status in sequence (last one repeats)."""
    state = {"i": 0}

    def probe(prior):
        s = statuses[min(state["i"], len(statuses) - 1)]
        state["i"] += 1
        return s, f"evidence for {s}", {"obs_cycle": state["i"]}

    return probe


def _events(event_type=None):
    entries = load_log(limit=10_000)
    if event_type:
        return [e for e in entries if e.get("event_type") == event_type]
    return entries


# ---------------------------------------------------------------------------
# Harness: narration state machine
# ---------------------------------------------------------------------------

class TestTransitionNarration:
    def test_first_observation_ok_emits_nothing(self, monkeypatch):
        monkeypatch.setattr(sh, "DECLARED_PROCESSES", [_decl(_seq_probe([OK]))])
        run_health_probes()
        assert _events() == []
        snap = load_snapshot()
        assert snap["processes"]["fake_process"]["status"] == OK

    def test_first_observation_silent_narrates_once(self, monkeypatch):
        """A subsystem observed dead on the very first probe cycle is
        newsworthy (probes with baseline semantics handle that probe-side
        by returning OK)."""
        monkeypatch.setattr(
            sh, "DECLARED_PROCESSES", [_decl(_seq_probe([SILENT, SILENT, SILENT]))])
        for _ in range(3):
            run_health_probes()
        assert len(_events(SUBSYSTEM_SILENT)) == 1  # held state never repeats
        assert _events(SUBSYSTEM_RECOVERED) == []

    def test_ok_silent_ok_full_cycle(self, monkeypatch):
        monkeypatch.setattr(
            sh, "DECLARED_PROCESSES",
            [_decl(_seq_probe([OK, SILENT, SILENT, OK, OK]))])
        for _ in range(5):
            run_health_probes()
        silent = _events(SUBSYSTEM_SILENT)
        recovered = _events(SUBSYSTEM_RECOVERED)
        assert len(silent) == 1
        assert len(recovered) == 1
        assert silent[0]["subject"] == "fake_process"
        assert silent[0]["audience"] == "user"  # user-surfaced by decree
        assert recovered[0]["audience"] == "user"

    def test_unknown_to_silent_narrates(self, monkeypatch):
        """The streak probes (variant_ab, lesson_receipts) arrive at SILENT
        via UNKNOWN watch cycles — that edge must narrate. (The original
        {prev,curr}=={OK,SILENT} check missed exactly this path.)"""
        monkeypatch.setattr(
            sh, "DECLARED_PROCESSES",
            [_decl(_seq_probe([UNKNOWN, UNKNOWN, SILENT]))])
        for _ in range(3):
            run_health_probes()
        assert len(_events(SUBSYSTEM_SILENT)) == 1

    def test_silent_unknown_ok_still_narrates_recovery(self, monkeypatch):
        """A probe that breaks (UNKNOWN) between SILENT and OK must not
        swallow the recovery narration."""
        monkeypatch.setattr(
            sh, "DECLARED_PROCESSES",
            [_decl(_seq_probe([SILENT, UNKNOWN, OK]))])
        for _ in range(3):
            run_health_probes()
        assert len(_events(SUBSYSTEM_SILENT)) == 1
        assert len(_events(SUBSYSTEM_RECOVERED)) == 1

    def test_silent_unknown_silent_does_not_renarrate(self, monkeypatch):
        monkeypatch.setattr(
            sh, "DECLARED_PROCESSES",
            [_decl(_seq_probe([SILENT, UNKNOWN, SILENT]))])
        for _ in range(3):
            run_health_probes()
        assert len(_events(SUBSYSTEM_SILENT)) == 1


# ---------------------------------------------------------------------------
# Harness: snapshot semantics
# ---------------------------------------------------------------------------

class TestSnapshot:
    def test_unknown_keys_survive_rewrite(self, monkeypatch, _tmp_health):
        monkeypatch.setattr(sh, "DECLARED_PROCESSES", [_decl(_seq_probe([OK]))])
        _tmp_health.write_text(json.dumps({
            "processes": {"fake_process": {"status": OK, "operator_note": "keep me"}},
            "future_top_level": True,
        }))
        run_health_probes()
        snap = load_snapshot()
        assert snap["processes"]["fake_process"]["operator_note"] == "keep me"
        assert snap["future_top_level"] is True

    def test_history_ring_capped(self, monkeypatch):
        monkeypatch.setattr(sh, "DECLARED_PROCESSES", [_decl(_seq_probe([OK]))])
        for _ in range(HISTORY_KEEP + 3):
            run_health_probes()
        hist = load_snapshot()["processes"]["fake_process"]["history"]
        assert len(hist) == HISTORY_KEEP
        # newest observation retained
        assert hist[-1]["obs_cycle"] == HISTORY_KEEP + 3

    def test_raising_probe_reports_unknown_and_cycle_continues(self, monkeypatch):
        def boom(prior):
            raise RuntimeError("probe exploded")

        monkeypatch.setattr(
            sh, "DECLARED_PROCESSES",
            [_decl(boom, name="broken"), _decl(_seq_probe([OK]), name="healthy")])
        summary = run_health_probes()
        assert summary["ran"] == 2
        snap = load_snapshot()
        assert snap["processes"]["broken"]["status"] == UNKNOWN
        assert "probe failed" in snap["processes"]["broken"]["evidence"]
        assert snap["processes"]["healthy"]["status"] == OK

    def test_killswitch_skips_everything(self, monkeypatch, _tmp_health):
        import config as config_mod
        real_get = config_mod.get

        def fake_get(key, default=None):
            if key == "health.probes_enabled":
                return False
            return real_get(key, default)

        monkeypatch.setattr(config_mod, "get", fake_get)
        monkeypatch.setattr(sh, "DECLARED_PROCESSES", [_decl(_seq_probe([SILENT]))])
        summary = run_health_probes()
        assert summary.get("skipped")
        assert summary["ran"] == 0
        assert not _tmp_health.exists()
        assert _events() == []

    def test_cycle_counter_increments(self, monkeypatch):
        monkeypatch.setattr(sh, "DECLARED_PROCESSES", [_decl(_seq_probe([OK]))])
        run_health_probes()
        run_health_probes()
        assert load_snapshot()["cycle"] == 2

    def test_render_orders_silent_first(self, monkeypatch):
        monkeypatch.setattr(
            sh, "DECLARED_PROCESSES",
            [_decl(_seq_probe([OK]), name="aaa_fine"),
             _decl(_seq_probe([SILENT]), name="zzz_dead")])
        run_health_probes()
        text = render_snapshot()
        assert text.index("zzz_dead") < text.index("aaa_fine")


# ---------------------------------------------------------------------------
# Probe semantics
# ---------------------------------------------------------------------------

def _outcome_row(loop_id, *, status="done", task_type="agenda",
                 goal_achieved=None, hours_ago=2.0):
    row = {
        "loop_id": loop_id,
        "status": status,
        "task_type": task_type,
        "recorded_at": (datetime.now(timezone.utc)
                        - timedelta(hours=hours_ago)).isoformat(),
    }
    if goal_achieved is not None:
        row["goal_achieved"] = goal_achieved
    return row


class TestClosureVerdictsProbe:
    def test_first_observation_backlog_is_baseline_not_silent(self, monkeypatch):
        monkeypatch.setattr(sh, "_recent_outcomes", lambda limit=50: [
            _outcome_row("aaaa1111"), _outcome_row("bbbb2222", goal_achieved=True)])
        status, evidence, obs = sh._probe_closure_verdicts({})
        assert status == OK
        assert "baseline" in evidence
        assert obs["unverdicted_ids"] == ["aaaa1111"]

    def test_new_id_is_silent(self, monkeypatch):
        monkeypatch.setattr(sh, "_recent_outcomes", lambda limit=50: [
            _outcome_row("aaaa1111"), _outcome_row("cccc3333")])
        prior = {"history": [
            {"done": 1, "unverdicted": 1, "unverdicted_ids": ["aaaa1111"]}]}
        status, evidence, _ = sh._probe_closure_verdicts(prior)
        assert status == SILENT
        assert "accreting" in evidence
        assert "cccc3333" in evidence

    def test_window_slide_same_count_still_silent(self, monkeypatch):
        """2026-07-30 review (Skeptic F3): old unverdicted run scrolls out
        of the recency window just as a new one appears — same count,
        brand-new silent failure. Identity tracking must catch it."""
        monkeypatch.setattr(sh, "_recent_outcomes", lambda limit=50: [
            _outcome_row("cccc3333")])  # aaaa1111 scrolled out; count still 1
        prior = {"history": [
            {"done": 1, "unverdicted": 1, "unverdicted_ids": ["aaaa1111"]}]}
        status, _, _ = sh._probe_closure_verdicts(prior)
        assert status == SILENT

    def test_stable_backlog_is_ok(self, monkeypatch):
        monkeypatch.setattr(sh, "_recent_outcomes", lambda limit=50: [
            _outcome_row("aaaa1111"), _outcome_row("cccc3333")])
        prior = {"history": [
            {"done": 2, "unverdicted": 2,
             "unverdicted_ids": ["aaaa1111", "cccc3333"], "new_ids": []}]}
        status, evidence, _ = sh._probe_closure_verdicts(prior)
        assert status == OK
        assert "no new ids" in evidence

    def test_recent_growth_holds_the_alarm(self, monkeypatch):
        """No SILENT/RECOVERED ping-pong: the cycle after an accretion is
        acknowledged stays SILENT until the growth ages out of the window."""
        monkeypatch.setattr(sh, "_recent_outcomes", lambda limit=50: [
            _outcome_row("aaaa1111"), _outcome_row("cccc3333")])
        prior = {"history": [
            {"done": 2, "unverdicted": 2,
             "unverdicted_ids": ["aaaa1111", "cccc3333"],
             "new_ids": ["cccc3333"]}]}
        status, evidence, _ = sh._probe_closure_verdicts(prior)
        assert status == SILENT
        assert "holding" in evidence

    def test_preid_history_gets_baseline_not_false_alarm(self, monkeypatch):
        """History rows from before id tracking shipped carry only counts —
        upgrading must re-baseline, not alarm on every current id."""
        monkeypatch.setattr(sh, "_recent_outcomes", lambda limit=50: [
            _outcome_row("aaaa1111")])
        prior = {"history": [{"done": 1, "unverdicted": 1}]}
        status, evidence, _ = sh._probe_closure_verdicts(prior)
        assert status == OK
        assert "baseline" in evidence

    def test_fresh_rows_get_grace(self, monkeypatch):
        # Recorded 5 minutes ago — closure may still be in flight.
        monkeypatch.setattr(sh, "_recent_outcomes", lambda limit=50: [
            _outcome_row("aaaa1111", hours_ago=0.1)])
        status, _, obs = sh._probe_closure_verdicts({})
        assert status == OK
        assert obs["unverdicted"] == 0

    def test_all_verdicted_is_ok(self, monkeypatch):
        monkeypatch.setattr(sh, "_recent_outcomes", lambda limit=50: [
            _outcome_row("aaaa1111", goal_achieved=True),
            _outcome_row("bbbb2222", goal_achieved=False)])
        status, evidence, _ = sh._probe_closure_verdicts({})
        assert status == OK
        assert "2/2" in evidence


def _seed_log_row(event_type, subject, hours_ago, context=None):
    """Append a raw log row so tests control the timestamp."""
    from captains_log import _log_path
    row = {
        "timestamp": (datetime.now(timezone.utc)
                      - timedelta(hours=hours_ago)).isoformat(),
        "event_type": event_type,
        "subject": subject,
        "summary": "seeded",
    }
    if context:
        row["context"] = context
    path = _log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


class TestVariantAbProbe:
    def _patch_skills(self, monkeypatch, *, frontier):
        skill_objs = [types.SimpleNamespace(variant_of=None)] * max(frontier, 1)
        import skills as skills_mod
        monkeypatch.setattr(skills_mod, "load_skills", lambda: skill_objs)
        monkeypatch.setattr(
            skills_mod, "frontier_skills",
            lambda all_skills, **kw: all_skills[:frontier])

    def test_empty_frontier_owes_nothing(self, monkeypatch):
        self._patch_skills(monkeypatch, frontier=0)
        status, evidence, _ = sh._probe_variant_ab({})
        assert status == OK
        assert "no variants owed" in evidence

    def test_streak_required_before_silent(self, monkeypatch):
        self._patch_skills(monkeypatch, frontier=3)
        prior = {}
        for i in range(STREAK_FOR_SILENT - 1):
            status, _, obs = sh._probe_variant_ab(prior)
            assert status == UNKNOWN, f"cycle {i + 1} should still be watching"
            prior = {"history": prior.get("history", []) + [obs]}
        status, evidence, _ = sh._probe_variant_ab(prior)
        assert status == SILENT
        assert "not firing" in evidence
        assert "0 variants ever" in evidence

    def test_recent_creation_breaks_the_freeze(self, monkeypatch):
        self._patch_skills(monkeypatch, frontier=3)
        _seed_log_row("SKILL_VARIANT_CREATED", "some-variant", hours_ago=2)
        prior = {"history": [
            {"frontier": 3, "variant_events": 0},
            {"frontier": 3, "variant_events": 0}]}
        status, evidence, _ = sh._probe_variant_ab(prior)
        assert status == OK
        assert "created" in evidence

    def test_old_variant_does_not_mask_stopped_generator(self, monkeypatch):
        """2026-07-30 review (Minimalist F1): one historical variant must
        not make the probe OK forever — a count frozen across the streak
        with the last creation beyond the grace window is SILENT."""
        self._patch_skills(monkeypatch, frontier=3)
        _seed_log_row("SKILL_VARIANT_CREATED", "old-variant",
                      hours_ago=(sh.VARIANT_STALE_DAYS + 2) * 24)
        prior = {"history": [
            {"frontier": 3, "variant_events": 1},
            {"frontier": 3, "variant_events": 1}]}
        status, evidence, _ = sh._probe_variant_ab(prior)
        assert status == SILENT
        assert "no new variant since" in evidence

    def test_frozen_count_within_grace_is_ok(self, monkeypatch):
        self._patch_skills(monkeypatch, frontier=3)
        _seed_log_row("SKILL_VARIANT_CREATED", "fresh-variant", hours_ago=20)
        prior = {"history": [
            {"frontier": 3, "variant_events": 1},
            {"frontier": 3, "variant_events": 1}]}
        status, evidence, _ = sh._probe_variant_ab(prior)
        assert status == OK
        assert "grace window" in evidence

    def test_preupgrade_history_keeps_watching(self, monkeypatch):
        """Old-format history rows (variants counts, no variant_events)
        must not complete a streak."""
        self._patch_skills(monkeypatch, frontier=3)
        prior = {"history": [
            {"frontier": 3, "variants": 0},
            {"frontier": 3, "variants": 0}]}
        status, _, _ = sh._probe_variant_ab(prior)
        assert status == UNKNOWN


class TestContradictionProbe:
    def _seed_event(self, tmp_path, event_type, loop_id, hours_ago):
        _seed_log_row(event_type, loop_id, hours_ago,
                      context={"loop_id": loop_id})

    def test_no_candidates_is_unknown(self, tmp_path):
        status, _, _ = sh._probe_contradiction_lifecycle({})
        assert status == UNKNOWN

    def test_drained_queue_is_ok(self, tmp_path):
        self._seed_event(tmp_path, "CONTRADICTION_CANDIDATE", "loop1", hours_ago=100)
        self._seed_event(tmp_path, "CONTRADICTION_ADJUDICATED", "loop1", hours_ago=99)
        status, evidence, _ = sh._probe_contradiction_lifecycle({})
        assert status == OK
        assert "drained" in evidence

    def test_recent_pending_within_window_is_ok(self, tmp_path):
        self._seed_event(tmp_path, "CONTRADICTION_CANDIDATE", "loop2", hours_ago=1)
        status, evidence, _ = sh._probe_contradiction_lifecycle({})
        assert status == OK
        assert "drain window" in evidence

    def test_stale_pending_is_silent(self, tmp_path):
        self._seed_event(
            tmp_path, "CONTRADICTION_CANDIDATE", "loop3",
            hours_ago=CANDIDATE_STARVATION_HOURS + 5)
        status, evidence, _ = sh._probe_contradiction_lifecycle({})
        assert status == SILENT
        assert "not" in evidence and "draining" in evidence


class TestLessonReceiptsProbe:
    def _cite(self, tmp_path, monkeypatch, cited_loop):
        """Empty memory dir (receipt sum 0) + one recent run citing lessons."""
        monkeypatch.setattr(sh, "_memory_dir", lambda: tmp_path / "mem")
        if cited_loop is None:
            monkeypatch.setattr(sh, "_recent_outcomes", lambda limit=50: [])
            return
        monkeypatch.setattr(
            sh, "_recent_outcomes", lambda limit=50: [_outcome_row(cited_loop)])
        src = tmp_path / f"src-{cited_loop}"
        src.mkdir(parents=True, exist_ok=True)
        (src / "recall_citations.json").write_text(
            json.dumps({"lesson_ids": ["l1"], "rule_ids": []}))
        monkeypatch.setattr(sh, "_run_source", lambda lid: src)

    def test_new_citing_run_with_frozen_sum_is_silent(self, monkeypatch, tmp_path):
        self._cite(tmp_path, monkeypatch, "runB")
        prior = {"history": [
            {"receipt_sum": 0, "last_cited_loop": "runA"},
            {"receipt_sum": 0, "last_cited_loop": "runA"}]}
        status, evidence, _ = sh._probe_lesson_receipts(prior)
        assert status == SILENT
        assert "not reaching disk" in evidence

    def test_stale_citation_is_not_a_false_alarm(self, monkeypatch, tmp_path):
        """2026-07-30 review (Minimalist F2): the same old cited run sitting
        in the recency window owes nothing new — a frozen sum is OK."""
        self._cite(tmp_path, monkeypatch, "runA")
        prior = {"history": [
            {"receipt_sum": 0, "last_cited_loop": "runA"},
            {"receipt_sum": 0, "last_cited_loop": "runA"}]}
        status, evidence, _ = sh._probe_lesson_receipts(prior)
        assert status == OK
        assert "no new citations owed" in evidence

    def test_first_citation_after_none_counts_as_advance(self, monkeypatch, tmp_path):
        self._cite(tmp_path, monkeypatch, "runB")
        prior = {"history": [
            {"receipt_sum": 0, "last_cited_loop": None},
            {"receipt_sum": 0, "last_cited_loop": None}]}
        status, _, _ = sh._probe_lesson_receipts(prior)
        assert status == SILENT

    def test_moving_sum_is_ok(self, monkeypatch, tmp_path):
        self._cite(tmp_path, monkeypatch, "runB")
        prior = {"history": [
            {"receipt_sum": 5, "last_cited_loop": "runA"},
            {"receipt_sum": 8, "last_cited_loop": "runA"}]}
        status, evidence, _ = sh._probe_lesson_receipts(prior)
        assert status == OK
        assert "moving" in evidence

    def test_preupgrade_history_keeps_watching(self, monkeypatch, tmp_path):
        self._cite(tmp_path, monkeypatch, "runB")
        prior = {"history": [
            {"receipt_sum": 0, "recent_run_cited_lessons": True},
            {"receipt_sum": 0, "recent_run_cited_lessons": True}]}
        status, _, _ = sh._probe_lesson_receipts(prior)
        assert status == UNKNOWN


# ---------------------------------------------------------------------------
# Review fixes (2026-07-30 adversarial round)
# ---------------------------------------------------------------------------

class TestReviewFixes:
    def test_narration_sheds_ambient_loop_id(self, monkeypatch):
        """Architect F1: probes run inside loop_finalize's loop_id_scope —
        transitions are global process state and must not be attributed to
        the run that hosted the probe cycle."""
        from captains_log import loop_id_scope
        monkeypatch.setattr(
            sh, "DECLARED_PROCESSES", [_decl(_seq_probe([SILENT]))])
        with loop_id_scope("host-run-123"):
            run_health_probes()
        events = _events(SUBSYSTEM_SILENT)
        assert len(events) == 1
        assert "loop_id" not in events[0]

    def test_failed_snapshot_write_defers_narration(self, monkeypatch):
        """Skeptic F2: narrating before the snapshot persists would repeat
        the same transition forever if the write fails — the log must only
        say what the durable state machine remembers telling."""
        monkeypatch.setattr(
            sh, "DECLARED_PROCESSES", [_decl(_seq_probe([SILENT, SILENT]))])
        real_write = sh._write_snapshot

        def boom(snapshot):
            raise OSError("disk full")

        monkeypatch.setattr(sh, "_write_snapshot", boom)
        summary = run_health_probes()
        assert summary.get("error")
        assert _events(SUBSYSTEM_SILENT) == []  # nothing narrated
        monkeypatch.setattr(sh, "_write_snapshot", real_write)
        run_health_probes()
        assert len(_events(SUBSYSTEM_SILENT)) == 1  # narrated exactly once

    def test_snapshot_write_rides_the_file_lock(self, monkeypatch):
        """Skeptic F1 / Architect F2: the cycle is a read-modify-write of
        shared state — it must hold the snapshot's lock, not rely on bare
        atomic_write."""
        import file_lock
        held_during_write = {}
        real_write = sh._write_snapshot

        def spy_write(snapshot):
            lock_key = str(
                (sh._snapshot_path().parent
                 / (sh._snapshot_path().name + ".lock")).resolve())
            held_during_write["locked"] = lock_key in file_lock._get_held()
            real_write(snapshot)

        monkeypatch.setattr(sh, "_write_snapshot", spy_write)
        monkeypatch.setattr(sh, "DECLARED_PROCESSES", [_decl(_seq_probe([OK]))])
        run_health_probes()
        assert held_during_write.get("locked") is True


# ---------------------------------------------------------------------------
# Registry contract
# ---------------------------------------------------------------------------

class TestRegistry:
    def test_declared_processes_are_well_formed(self):
        names = [d.name for d in sh.DECLARED_PROCESSES]
        assert len(names) == len(set(names)), "duplicate process names"
        for d in sh.DECLARED_PROCESSES:
            assert d.description and d.expectation
            assert callable(d.probe)

    def test_full_cycle_runs_on_empty_workspace(self, monkeypatch, tmp_path):
        """Out-of-the-box invariant: the real registry must probe cleanly
        against a fresh workspace (all-UNKNOWN is fine; raising is not)."""
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        summary = run_health_probes()
        assert "error" not in summary
        assert summary["ran"] == len(sh.DECLARED_PROCESSES)


class TestContainerAuthProbe:
    """The container_auth row reads breaker state only — no docker, no LLM."""

    def _patch(self, monkeypatch, mode, state):
        import container_exec as ce
        monkeypatch.setattr(ce, "container_mode", lambda: mode)
        monkeypatch.setattr(ce, "auth_breaker_snapshot", lambda: state)

    def test_off_mode_is_ok(self, monkeypatch):
        self._patch(monkeypatch, "off", None)
        status, evidence, obs = sh._probe_container_auth({})
        assert status == OK and "not in play" in evidence

    def test_armed_and_clear_is_ok(self, monkeypatch):
        self._patch(monkeypatch, "on", None)
        status, evidence, obs = sh._probe_container_auth({})
        assert status == OK and obs["breaker_tripped"] is False

    def test_tripped_is_silent_immediately(self, monkeypatch):
        # A tripped breaker is a definite state, not cross-cycle noise — no
        # streak grace before SILENT.
        self._patch(monkeypatch, "on", {"tripped_at": 1755100000.0,
                                        "reason": "oauth session expired"})
        status, evidence, obs = sh._probe_container_auth({})
        assert status == SILENT
        assert "re-seed" in evidence and "degrade to host" in evidence

    def test_tripped_require_names_refusal(self, monkeypatch):
        self._patch(monkeypatch, "require", {"tripped_at": 1755100000.0,
                                             "reason": "not logged in"})
        status, evidence, _ = sh._probe_container_auth({})
        assert status == SILENT and "REFUSE" in evidence
