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
        assert obs == {"done": 2, "unverdicted": 1}

    def test_growth_vs_prior_is_silent(self, monkeypatch):
        monkeypatch.setattr(sh, "_recent_outcomes", lambda limit=50: [
            _outcome_row("aaaa1111"), _outcome_row("cccc3333")])
        prior = {"history": [{"done": 1, "unverdicted": 1}]}
        status, evidence, _ = sh._probe_closure_verdicts(prior)
        assert status == SILENT
        assert "accreting" in evidence

    def test_stable_backlog_is_ok(self, monkeypatch):
        monkeypatch.setattr(sh, "_recent_outcomes", lambda limit=50: [
            _outcome_row("aaaa1111"), _outcome_row("cccc3333")])
        prior = {"history": [{"done": 2, "unverdicted": 2}]}
        status, evidence, _ = sh._probe_closure_verdicts(prior)
        assert status == OK
        assert "not growing" in evidence

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


class TestVariantAbProbe:
    def _patch_skills(self, monkeypatch, *, frontier, variants):
        skill_objs = ([types.SimpleNamespace(variant_of=None)] * frontier
                      + [types.SimpleNamespace(variant_of="parent") for _ in range(variants)])
        import skills as skills_mod
        monkeypatch.setattr(skills_mod, "load_skills", lambda: skill_objs)
        monkeypatch.setattr(
            skills_mod, "frontier_skills",
            lambda all_skills, **kw: all_skills[:frontier])

    def test_variants_exist_is_ok(self, monkeypatch):
        self._patch_skills(monkeypatch, frontier=2, variants=1)
        status, _, _ = sh._probe_variant_ab({})
        assert status == OK

    def test_empty_frontier_owes_nothing(self, monkeypatch):
        self._patch_skills(monkeypatch, frontier=0, variants=0)
        status, evidence, _ = sh._probe_variant_ab({})
        assert status == OK
        assert "no variants owed" in evidence

    def test_streak_required_before_silent(self, monkeypatch):
        self._patch_skills(monkeypatch, frontier=3, variants=0)
        # Cycles 1..STREAK-1: watching (UNKNOWN)
        prior = {}
        for i in range(STREAK_FOR_SILENT - 1):
            status, _, obs = sh._probe_variant_ab(prior)
            assert status == UNKNOWN, f"cycle {i + 1} should still be watching"
            hist = prior.get("history", []) + [obs]
            prior = {"history": hist}
        # Cycle STREAK: the streak completes
        status, evidence, _ = sh._probe_variant_ab(prior)
        assert status == SILENT
        assert "not firing" in evidence

    def test_streak_broken_by_variant_resets(self, monkeypatch):
        self._patch_skills(monkeypatch, frontier=3, variants=0)
        prior = {"history": [
            {"frontier": 3, "variants": 0},
            {"frontier": 2, "variants": 1},  # a variant existed mid-window
        ]}
        status, _, _ = sh._probe_variant_ab(prior)
        assert status == UNKNOWN


class TestContradictionProbe:
    def _seed_event(self, tmp_path, event_type, loop_id, hours_ago):
        """Append a raw log row so we control the timestamp."""
        from captains_log import _log_path
        row = {
            "timestamp": (datetime.now(timezone.utc)
                          - timedelta(hours=hours_ago)).isoformat(),
            "event_type": event_type,
            "subject": loop_id,
            "summary": "seeded",
            "context": {"loop_id": loop_id},
        }
        with open(_log_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")

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
