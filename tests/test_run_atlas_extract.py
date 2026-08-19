from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


def _load_extractor(workspace: Path):
    """Import scripts/run_atlas/extract_paths.py against a synthetic workspace."""
    repo_root = Path(__file__).resolve().parents[1]
    src = repo_root / "scripts" / "run_atlas" / "extract_paths.py"
    spec = importlib.util.spec_from_file_location("run_atlas_extract", src)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    # the module reads MARO_WORKSPACE at import time; repoint it explicitly
    mod.WS = workspace
    mod.RUNS = workspace / "runs"
    return mod


def _rundir(ws: Path, name: str) -> Path:
    rd = ws / "runs" / name
    (rd / "build").mkdir(parents=True)
    (rd / "source").mkdir(parents=True)
    return rd


def _write(p: Path, obj) -> None:
    p.write_text(json.dumps(obj), encoding="utf-8")


def _jsonl(p: Path, rows) -> None:
    p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


@pytest.fixture()
def ws(tmp_path):
    (tmp_path / "runs").mkdir()
    return tmp_path


def test_absent_origin_is_not_reported_as_cli(ws):
    """`origin` was added late. Absence means "not recorded", never "came from
    the CLI" -- reading it as CLI silently invented an entry point for 703 of
    788 real runs."""
    rd = _rundir(ws, "aaaa1111-test-run")
    _write(rd / "metadata.json", {
        "handle_id": "aaaa1111", "prompt": "p", "lane": "agenda",
        "started_at": "2026-08-01T00:00:00", "ended_at": "2026-08-01T00:01:00",
        "status": "done",
    })
    rec = _load_extractor(ws).build(rd)

    assert "intake.arrive" in rec["visits"]
    assert rec["visits"]["intake.arrive"]["a"]["label"] == "unrecorded"
    assert "intake.cli" not in rec["visits"]


def test_recorded_origin_lights_its_entry_point(ws):
    rd = _rundir(ws, "bbbb2222-test-run")
    _write(rd / "metadata.json", {
        "handle_id": "bbbb2222", "prompt": "p", "lane": "agenda",
        "started_at": "2026-08-01T00:00:00", "ended_at": "2026-08-01T00:01:00",
        "status": "done", "origin": {"source": "telegram"},
    })
    rec = _load_extractor(ws).build(rd)

    assert "intake.listener" in rec["visits"]
    assert "intake.cli" not in rec["visits"]


def test_unattributed_events_are_marked_windowed_not_attributed(ws):
    """The captain's-log slice is a byte range of the GLOBAL log, so an event
    with no loop_id may belong to a concurrent run. It must never be recorded
    with the same confidence as a loop_id-matched one."""
    rd = _rundir(ws, "cccc3333-test-run")
    _write(rd / "metadata.json", {
        "handle_id": "cccc3333", "prompt": "p", "lane": "agenda",
        "started_at": "2026-08-01T00:00:00", "ended_at": "2026-08-01T00:10:00",
        "status": "done", "loop_ids": ["loop-a"],
    })
    _jsonl(rd / "build" / "captains_log_slice.jsonl", [
        # carries our loop_id -> attributed
        {"event_type": "CUTS_DRAWN", "ts": "2026-08-01T00:02:00",
         "loop_id": "loop-a", "context": {}},
        # no loop_id, inside the window -> windowed
        {"event_type": "BOUNDARY_EXPANDED", "ts": "2026-08-01T00:03:00",
         "context": {"sub_steps": [1, 2]}},
        # no loop_id, AFTER ended_at -> outside the window entirely
        {"event_type": "SCAVENGE_DETECTED", "ts": "2026-08-01T00:30:00",
         "context": {}},
    ])
    rec = _load_extractor(ws).build(rd)
    v = rec["visits"]

    assert v["plan.cuts"]["e"] == "a"
    assert v["exec.boundary"]["e"] == "w"
    assert "exec.scavenge" not in v


def test_learning_tail_after_ended_at_is_not_credited_to_this_run(ws):
    """LESSON/SKILL promotions routinely fire after the run closes and belong
    to whatever ran next."""
    rd = _rundir(ws, "dddd4444-test-run")
    _write(rd / "metadata.json", {
        "handle_id": "dddd4444", "prompt": "p", "lane": "agenda",
        "started_at": "2026-08-01T00:00:00", "ended_at": "2026-08-01T00:10:00",
        "status": "done", "loop_ids": ["loop-a"],
    })
    _jsonl(rd / "build" / "captains_log_slice.jsonl", [
        {"event_type": "SKILL_PROMOTED", "ts": "2026-08-01T00:20:00",
         "loop_id": "loop-a", "context": {}},
    ])
    rec = _load_extractor(ws).build(rd)

    assert "close.learning" not in rec["visits"]


def test_retry_is_detected_from_repeated_step_text(ws):
    """`METACOGNITIVE_DECISION.context.retries` is the PRIOR count and tops out
    at 1; real attempt counts come from duplicate step text at consecutive
    iterations. Injected steps carry index -1."""
    rd = _rundir(ws, "eeee5555-test-run")
    _write(rd / "metadata.json", {
        "handle_id": "eeee5555", "prompt": "p", "lane": "agenda",
        "started_at": "2026-08-01T00:00:00", "ended_at": "2026-08-01T00:10:00",
        "status": "stuck", "loop_ids": ["loop-a"],
    })
    _write(rd / "build" / "loop-loop-a-log.json", {
        "loop_id": "loop-a", "status": "stuck", "started_at": "2026-08-01T00:01:00",
        "elapsed_ms": 1000,
        "steps": [
            {"index": 12, "text": "do the thing", "status": "blocked",
             "iteration": 1, "ended_ts": "2026-08-01T00:02:00"},
            {"index": 12, "text": "do the thing", "status": "blocked",
             "iteration": 2, "ended_ts": "2026-08-01T00:03:00"},
            {"index": -1, "text": "a narrower thing", "status": "done",
             "iteration": 3, "ended_ts": "2026-08-01T00:04:00"},
        ],
        "totals": {},
    })
    rec = _load_extractor(ws).build(rd)
    steps = rec["loops"][0]["steps"]

    assert [s["rep"] for s in steps] == [1, 2, 1]
    assert "exec.retry" in rec["visits"]
    assert "exec.blocked" in rec["visits"]
    assert "exec.inject" in rec["visits"]


def test_approx_timing_flagged_when_a_step_lacks_ended_ts(ws):
    """Without ended_ts the timeline is a cumulative-sum estimate that absorbs
    replans and verification into the preceding step -- it must be labeled."""
    rd = _rundir(ws, "ffff6666-test-run")
    _write(rd / "metadata.json", {
        "handle_id": "ffff6666", "prompt": "p", "lane": "agenda",
        "started_at": "2026-08-01T00:00:00", "ended_at": "2026-08-01T00:10:00",
        "status": "done", "loop_ids": ["loop-a"],
    })
    _write(rd / "build" / "loop-loop-a-log.json", {
        "loop_id": "loop-a", "status": "done", "started_at": "2026-08-01T00:01:00",
        "elapsed_ms": 1000,
        "steps": [{"index": 1, "text": "s", "status": "done",
                   "iteration": 1, "ended_ts": ""}],
        "totals": {},
    })
    rec = _load_extractor(ws).build(rd)

    assert rec["loops"][0]["approx"] is True


def test_run_that_never_reached_the_loop_is_marked(ws):
    rd = _rundir(ws, "7777aaaa-test-run")
    _write(rd / "metadata.json", {
        "handle_id": "7777aaaa", "prompt": "p", "lane": "now",
        "started_at": "2026-08-01T00:00:00", "ended_at": "2026-08-01T00:00:30",
        "status": "done",
    })
    rec = _load_extractor(ws).build(rd)

    assert "exec.never_ran" in rec["visits"]
    assert "route.now" in rec["visits"]
    assert rec["loops"] == []


def test_closure_verdict_and_terminal_class_are_carried(ws):
    rd = _rundir(ws, "8888bbbb-test-run")
    _write(rd / "metadata.json", {
        "handle_id": "8888bbbb", "prompt": "p", "lane": "agenda",
        "started_at": "2026-08-01T00:00:00", "ended_at": "2026-08-01T00:10:00",
        "status": "stuck", "goal_achieved": False, "goal_verdict_source": "closure",
    })
    _write(rd / "run_card.json", {"success_class": "failed", "total_cost_usd": 1.25})
    _jsonl(rd / "build" / "closure_verdicts.jsonl", [{
        "loop_id": "loop-a", "complete": False, "confidence": 0.65,
        "checks_run": 4, "checks_passed": 4, "inconclusive_count": 0,
        "judged": True, "gaps": ["g1", "g2"],
        "check_results": [{"description": "d", "outcome": "pass", "exit_code": 0}],
    }])
    rec = _load_extractor(ws).build(rd)

    assert rec["cls"] == "failed"
    assert rec["visits"]["verify.closure"]["a"]["complete"] is False
    assert rec["visits"]["verify.stamp"]["a"]["source"] == "closure"
    assert "term.failed" in rec["visits"]
    assert rec["checks"][0]["o"] == "pass"
