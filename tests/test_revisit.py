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
