"""Tests for revisit.py — §14h revisit mechanic (acquisition events
reopen standing dead ends) and the §13b reopen-payload stamp."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, "src")

from revisit import (
    ACQUISITION_EVENT_TYPES,
    MAX_CANDIDATES_PER_SWEEP,
    scan,
    sweep,
)


def _workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    (tmp_path / "runs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "memory").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _write_run(ws, name, *, verdict, ended_at, goal_achieved=None,
               payload=None):
    rd = ws / "runs" / name
    rd.mkdir(parents=True, exist_ok=True)
    meta = {
        "handle_id": name.split("-")[0],
        "prompt": f"goal of {name}",
        "status": "stuck",
        "ended_at": ended_at,
        "stop_verdict": verdict,
        "stop_evidence": f"evidence for {name}",
    }
    if goal_achieved is not None:
        meta["goal_achieved"] = goal_achieved
    if payload is not None:
        meta["stop_reopen_payload"] = payload
    (rd / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    return rd


def _write_events(ws, events):
    log_path = ws / "memory" / "captains_log.jsonl"
    with log_path.open("a", encoding="utf-8") as fh:
        for ts, etype, subject in events:
            fh.write(json.dumps({
                "timestamp": ts, "event_type": etype, "subject": subject,
                "summary": "s", "audience": "system",
            }) + "\n")


_T0 = "2026-08-01T00:00:00+00:00"
_T1 = "2026-08-10T00:00:00+00:00"
_T2 = "2026-08-12T00:00:00+00:00"


class TestScan:
    def test_acquisition_after_stop_makes_candidate(self, tmp_path, monkeypatch):
        ws = _workspace(tmp_path, monkeypatch)
        _write_run(ws, "aaa-old-run", verdict="thesis-refuted", ended_at=_T0)
        _write_events(ws, [(_T1, "SKILL_PROMOTED", "new skill")])
        result = scan()
        assert len(result.candidates) == 1
        c = result.candidates[0]
        assert c.run_name == "aaa-old-run"
        assert c.signals[0]["subject"] == "new skill"
        assert "landmark" in c.reopen_cond  # type-derived condition carried

    def test_acquisition_before_stop_is_not_a_signal(self, tmp_path, monkeypatch):
        ws = _workspace(tmp_path, monkeypatch)
        _write_run(ws, "bbb-run", verdict="thesis-refuted", ended_at=_T1)
        _write_events(ws, [(_T0, "SKILL_PROMOTED", "old skill")])
        result = scan()
        assert result.candidates == []
        assert any(d["run_name"] == "bbb-run" for d in result.standing)

    def test_budget_and_plot_verdicts_stand_but_never_match(
            self, tmp_path, monkeypatch):
        ws = _workspace(tmp_path, monkeypatch)
        _write_run(ws, "ccc-budget", verdict="out-of-budget", ended_at=_T0,
                   payload={"kind": "budget-daily", "daily_cap_usd": 25.0,
                            "spent_usd": 25.1})
        _write_run(ws, "ddd-plot", verdict="lost-the-plot", ended_at=_T0)
        _write_events(ws, [(_T1, "SKILL_PROMOTED", "shiny")])
        result = scan()
        assert result.candidates == []
        assert {d["run_name"] for d in result.standing} == \
            {"ccc-budget", "ddd-plot"}

    def test_achieved_goal_never_a_dead_end(self, tmp_path, monkeypatch):
        ws = _workspace(tmp_path, monkeypatch)
        _write_run(ws, "eee-won", verdict="reachable-but-not-worth-it",
                   ended_at=_T0, goal_achieved=True)
        _write_events(ws, [(_T1, "CANON_PROMOTED", "canon")])
        result = scan()
        assert result.candidates == []
        assert result.standing == []

    def test_unparseable_ended_at_stays_standing_not_guessed(
            self, tmp_path, monkeypatch):
        ws = _workspace(tmp_path, monkeypatch)
        _write_run(ws, "fff-no-ts", verdict="thesis-refuted", ended_at="???")
        _write_events(ws, [(_T1, "RULE_GRADUATED", "rule")])
        result = scan()
        assert result.candidates == []
        assert any(d["run_name"] == "fff-no-ts" for d in result.standing)

    def test_verdict_taxonomy_fully_partitioned(self):
        """Every goal verdict is deliberately classed matchable or
        standing-only — a new verdict added to stop_verdicts without a
        revisit decision trips this (r1, minimalist: the standing set
        must earn its keep as a coverage pin, not sit inert)."""
        from revisit import EVENT_MATCHABLE_VERDICTS, STANDING_ONLY_VERDICTS
        from stop_verdicts import GOAL_VERDICTS
        assert EVENT_MATCHABLE_VERDICTS | STANDING_ONLY_VERDICTS == \
            GOAL_VERDICTS
        assert not (EVENT_MATCHABLE_VERDICTS & STANDING_ONLY_VERDICTS)

    def test_mixed_naive_and_aware_timestamps_never_zero_the_scan(
            self, tmp_path, monkeypatch):
        """A tz-naive ended_at beside aware event stamps used to raise
        TypeError out of scan(), silently zeroing the whole sweep (r1,
        both lenses' HIGH, probe-confirmed). Naive pins to UTC."""
        ws = _workspace(tmp_path, monkeypatch)
        _write_run(ws, "aaa-naive", verdict="thesis-refuted",
                   ended_at="2026-08-01T00:00:00")  # no tz
        _write_run(ws, "bbb-aware", verdict="thesis-refuted", ended_at=_T0)
        _write_events(ws, [(_T1, "SKILL_PROMOTED", "skill")])
        result = scan()
        assert {c.run_name for c in result.candidates} == \
            {"aaa-naive", "bbb-aware"}

    def test_z_suffixed_timestamps_parse_and_match(
            self, tmp_path, monkeypatch):
        """requires-python floors at 3.10, whose fromisoformat rejects
        "Z" — a Z-suffixed ended_at or event ts would silently fall out
        of matching (r2, skeptic HIGH). _parse_ts normalizes Z→+00:00
        like its sibling modules."""
        ws = _workspace(tmp_path, monkeypatch)
        _write_run(ws, "zzz-zulu", verdict="thesis-refuted",
                   ended_at="2026-08-01T00:00:00Z")
        _write_events(ws, [("2026-08-10T00:00:00Z", "SKILL_PROMOTED", "s")])
        result = scan()
        assert {c.run_name for c in result.candidates} == {"zzz-zulu"}

    def test_non_utc_offset_window_does_not_exclude_valid_signals(
            self, tmp_path, monkeypatch):
        """The acquisition window must come from the UTC-normalized min
        datetime, not a raw-string min: slicing the date off a +05:00
        ended_at sets the window a day late and silently drops
        acquisitions that genuinely came after the stop (r2, architect
        LOW upgraded by probe)."""
        ws = _workspace(tmp_path, monkeypatch)
        # 2026-08-01T00:00+05:00 == 2026-07-31T19:00Z; the acquisition
        # at 20:00Z the same UTC day is after the stop but has a date
        # prefix before the naive [:10] slice of the ended_at string.
        _write_run(ws, "hhh-offset", verdict="thesis-refuted",
                   ended_at="2026-08-01T00:00:00+05:00")
        _write_events(
            ws, [("2026-07-31T20:00:00+00:00", "SKILL_PROMOTED", "s")])
        result = scan()
        assert {c.run_name for c in result.candidates} == {"hhh-offset"}

    def test_payload_rides_the_candidate(self, tmp_path, monkeypatch):
        ws = _workspace(tmp_path, monkeypatch)
        _write_run(ws, "ggg-close", verdict="reachable-but-not-worth-it",
                   ended_at=_T0,
                   payload={"kind": "escalation-close", "depth": 2,
                            "confidence": 7})
        _write_events(ws, [(_T1, "KNOWLEDGE_NODE_PROMOTED", "node")])
        result = scan()
        assert len(result.candidates) == 1
        assert result.candidates[0].reopen_payload["confidence"] == 7


class TestSweep:
    def test_sweep_emits_once_per_signal_then_dedupes(
            self, tmp_path, monkeypatch):
        ws = _workspace(tmp_path, monkeypatch)
        _write_run(ws, "aaa-run", verdict="thesis-refuted", ended_at=_T0)
        _write_events(ws, [(_T1, "SKILL_PROMOTED", "skill one")])
        emitted = []
        with patch("captains_log.log_event",
                   side_effect=lambda *a, **k: emitted.append((a, k)) or {}):
            first = sweep()
            second = sweep()
        assert first["new"] == 1
        assert second["new"] == 0  # same signal never re-fires
        assert len(emitted) == 1
        # A NEWER acquisition re-arms the candidate.
        _write_events(ws, [(_T2, "CANON_PROMOTED", "canon two")])
        with patch("captains_log.log_event",
                   side_effect=lambda *a, **k: emitted.append((a, k)) or {}):
            third = sweep()
        assert third["new"] == 1

    def test_sweep_capped_per_cycle(self, tmp_path, monkeypatch):
        ws = _workspace(tmp_path, monkeypatch)
        for i in range(MAX_CANDIDATES_PER_SWEEP + 2):
            _write_run(ws, f"r{i}-run", verdict="thesis-refuted",
                       ended_at=_T0)
        _write_events(ws, [(_T1, "SKILL_PROMOTED", "skill")])
        with patch("captains_log.log_event", return_value={}):
            first = sweep()
            second = sweep()
        assert first["new"] == MAX_CANDIDATES_PER_SWEEP
        assert second["new"] == 2  # the overflow surfaces next sweep

    def test_disabled_sweep_noops(self, tmp_path, monkeypatch):
        ws = _workspace(tmp_path, monkeypatch)
        (ws / "config.yml").write_text("revisit:\n  enabled: false\n")
        _write_run(ws, "aaa-run", verdict="thesis-refuted", ended_at=_T0)
        _write_events(ws, [(_T1, "SKILL_PROMOTED", "skill")])
        with patch("captains_log.log_event", return_value={}) as mock_ev:
            out = sweep()
        assert out == {"total": 0, "matched": 0, "new": 0}
        assert not mock_ev.called

    def test_sweep_never_raises(self, tmp_path, monkeypatch):
        _workspace(tmp_path, monkeypatch)
        with patch("revisit.scan", side_effect=RuntimeError("boom")):
            out = sweep()
        assert out["new"] == 0

    def test_payload_reaches_event_context_and_state_hits_disk(
            self, tmp_path, monkeypatch):
        """The sweep path, not just the scan dataclass: the emitted
        event's context carries the reopen payload, and dedup state is
        really on disk (r1, architect)."""
        ws = _workspace(tmp_path, monkeypatch)
        _write_run(ws, "ggg-close", verdict="reachable-but-not-worth-it",
                   ended_at=_T0,
                   payload={"kind": "escalation-close", "depth": 2,
                            "confidence": 7})
        _write_events(ws, [(_T1, "SKILL_PROMOTED", "skill")])
        captured = {}
        with patch("captains_log.log_event",
                   side_effect=lambda *a, **k: captured.update(k) or {}):
            out = sweep()
        assert out["new"] == 1
        assert captured["context"]["reopen_payload"]["confidence"] == 7
        state = json.loads(
            (ws / "memory" / "revisit_state.json").read_text())
        assert state["ggg-close"] == _T1

    def test_contended_mutex_skips_cycle(self, tmp_path, monkeypatch):
        """A held sweep mutex means another sweep is mid-flight — this
        one must skip, not queue or double-emit (r1, two lenses)."""
        ws = _workspace(tmp_path, monkeypatch)
        _write_run(ws, "aaa-run", verdict="thesis-refuted", ended_at=_T0)
        _write_events(ws, [(_T1, "SKILL_PROMOTED", "skill")])
        from file_lock import FileLockTimeout
        with patch("file_lock.locked_write",
                   side_effect=FileLockTimeout("held")), \
             patch("captains_log.log_event", return_value={}) as mock_ev:
            out = sweep()
        assert out == {"total": 0, "matched": 0, "new": 0}
        assert not mock_ev.called


class TestReopenPayloadStamp:
    """§13b: the schema owner stores/clears stop_reopen_payload with the
    tuple's replace-whole doctrine."""

    def _stamp(self, rd, **kw):
        from runs import stamp_run_stop_verdict
        return stamp_run_stop_verdict(run_dir=rd, **kw)

    def _meta(self, rd):
        return json.loads((rd / "metadata.json").read_text())

    @pytest.fixture()
    def rd(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        d = tmp_path / "runs" / "xxx-run"
        d.mkdir(parents=True)
        (d / "metadata.json").write_text("{}")
        return d

    def test_payload_stored_with_verdict(self, rd):
        self._stamp(rd, stop_verdict="out-of-budget", stop_evidence="e",
                    reopen_payload={"kind": "budget-daily",
                                    "daily_cap_usd": 25.0, "spent_usd": 26.0})
        meta = self._meta(rd)
        assert meta["stop_reopen_payload"]["spent_usd"] == 26.0

    def test_new_verdict_without_payload_pops_stale_one(self, rd):
        self._stamp(rd, stop_verdict="out-of-budget", stop_evidence="e",
                    reopen_payload={"kind": "budget-daily"})
        self._stamp(rd, stop_verdict="thesis-refuted", stop_evidence="e2")
        meta = self._meta(rd)
        assert meta["stop_verdict"] == "thesis-refuted"
        assert "stop_reopen_payload" not in meta

    def test_clearing_verdict_clears_payload(self, rd):
        self._stamp(rd, stop_verdict="out-of-budget", stop_evidence="e",
                    reopen_payload={"kind": "budget-daily"})
        self._stamp(rd, stop_verdict="", stop_evidence="")
        meta = self._meta(rd)
        assert "stop_verdict" not in meta
        assert "stop_reopen_payload" not in meta

    def test_non_dict_payload_dropped_not_persisted(self, rd):
        self._stamp(rd, stop_verdict="out-of-budget", stop_evidence="e",
                    reopen_payload="not a dict")
        assert "stop_reopen_payload" not in self._meta(rd)

    def test_same_verdict_restamp_without_payload_pops_it(self, rd):
        """Pins the doctrine (r1, skeptic raised it as a concern): even a
        SAME-verdict re-stamp that doesn't resupply the payload pops it —
        the payload describes the stamp that wrote it, and letting a
        predecessor's numbers annotate a fresher ending is exactly the
        stale-tuple drift the replace-whole doctrine exists to prevent."""
        self._stamp(rd, stop_verdict="out-of-budget", stop_evidence="e1",
                    reopen_payload={"kind": "budget-daily",
                                    "daily_cap_usd": 25.0})
        self._stamp(rd, stop_verdict="out-of-budget", stop_evidence="e2")
        meta = self._meta(rd)
        assert meta["stop_evidence"] == "e2"
        assert "stop_reopen_payload" not in meta
