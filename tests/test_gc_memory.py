"""Tests for poe-gc memory garbage collection (Phase 25)."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import gc_memory
from gc_memory import (
    GCReport,
    DEFAULT_OUTCOMES_RETAIN_DAYS,
    DEFAULT_AUDIT_RETAIN_ENTRIES,
    _gc_audit,
    _gc_narrative_logs,
    _gc_outcomes,
    _gc_tiered_lessons,
    run_gc,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mem_dir(tmp_path) -> Path:
    """Return the memory dir that _memory_dir() will resolve to."""
    mem = tmp_path / "prototypes" / "poe-orchestration" / "memory"
    mem.mkdir(parents=True, exist_ok=True)
    return mem


def _write_outcome(mem: Path, days_ago: int = 0, status: str = "done") -> None:
    ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    line = json.dumps({"goal": "test", "status": status, "recorded_at": ts})
    with open(mem / "outcomes.jsonl", "a") as f:
        f.write(line + "\n")


def _write_audit_entry(mem: Path) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    line = json.dumps({"skill_name": "test", "success": True, "timestamp": ts})
    with open(mem / "sandbox-audit.jsonl", "a") as f:
        f.write(line + "\n")


def _write_narrative(mem: Path, days_ago: int) -> Path:
    date = (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%d")
    f = mem / f"{date}.md"
    f.write_text(f"# Log {date}\n\nContent here.\n")
    return f


def _write_tiered_lesson(mem: Path, tier: str, score: float) -> None:
    import uuid
    from datetime import date
    tier_dir = mem / tier
    tier_dir.mkdir(exist_ok=True)
    line = json.dumps({
        "lesson_id": str(uuid.uuid4())[:8],
        "task_type": "test",
        "outcome": "success",
        "lesson": "test lesson",
        "source_goal": "test",
        "confidence": 0.7,
        "tier": tier,
        "score": score,
        "last_reinforced": date.today().isoformat(),
        "sessions_validated": 0,
        "times_applied": 0,
        "times_reinforced": 0,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "acquired_for": None,
    })
    with open(tier_dir / "lessons.jsonl", "a") as f:
        f.write(line + "\n")


# ---------------------------------------------------------------------------
# _gc_outcomes
# ---------------------------------------------------------------------------

def test_gc_outcomes_empty_workspace(monkeypatch, tmp_path):
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
    _mem_dir(tmp_path)
    total, removed, freed = _gc_outcomes(dry_run=True)
    assert total == 0
    assert removed == 0


def test_gc_outcomes_removes_old_entries(monkeypatch, tmp_path):
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
    mem = _mem_dir(tmp_path)
    _write_outcome(mem, days_ago=100)  # old
    _write_outcome(mem, days_ago=100)  # old
    _write_outcome(mem, days_ago=1)    # recent
    total, removed, freed = _gc_outcomes(retain_days=90, dry_run=True)
    assert total == 3
    assert removed == 2


def test_gc_outcomes_dry_run_does_not_modify(monkeypatch, tmp_path):
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
    mem = _mem_dir(tmp_path)
    _write_outcome(mem, days_ago=100)
    _write_outcome(mem, days_ago=1)
    path = mem / "outcomes.jsonl"
    original_size = path.stat().st_size
    _gc_outcomes(retain_days=90, dry_run=True)
    assert path.stat().st_size == original_size


def test_gc_outcomes_live_run_removes_entries(monkeypatch, tmp_path):
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
    mem = _mem_dir(tmp_path)
    _write_outcome(mem, days_ago=100)
    _write_outcome(mem, days_ago=1)
    total, removed, freed = _gc_outcomes(retain_days=90, dry_run=False)
    assert removed == 1
    # Verify file only has recent entry
    lines = (mem / "outcomes.jsonl").read_text().splitlines()
    assert len(lines) == 1
    remaining = json.loads(lines[0])
    assert remaining["status"] == "done"


def test_gc_outcomes_keeps_all_recent(monkeypatch, tmp_path):
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
    mem = _mem_dir(tmp_path)
    for i in range(5):
        _write_outcome(mem, days_ago=i)
    total, removed, freed = _gc_outcomes(retain_days=90, dry_run=True)
    assert removed == 0


# ---------------------------------------------------------------------------
# _gc_audit
# ---------------------------------------------------------------------------

def test_gc_audit_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
    _mem_dir(tmp_path)
    total, removed, freed = _gc_audit(dry_run=True)
    assert total == 0


def test_gc_audit_within_limit(monkeypatch, tmp_path):
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
    mem = _mem_dir(tmp_path)
    for _ in range(5):
        _write_audit_entry(mem)
    total, removed, freed = _gc_audit(retain_entries=10, dry_run=True)
    assert total == 5
    assert removed == 0


def test_gc_audit_trims_to_limit(monkeypatch, tmp_path):
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
    mem = _mem_dir(tmp_path)
    for _ in range(15):
        _write_audit_entry(mem)
    total, removed, freed = _gc_audit(retain_entries=10, dry_run=True)
    assert total == 15
    assert removed == 5


def test_gc_audit_live_trim(monkeypatch, tmp_path):
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
    mem = _mem_dir(tmp_path)
    for _ in range(15):
        _write_audit_entry(mem)
    _gc_audit(retain_entries=10, dry_run=False)
    lines = (mem / "sandbox-audit.jsonl").read_text().splitlines()
    assert len(lines) == 10


# ---------------------------------------------------------------------------
# _gc_narrative_logs
# ---------------------------------------------------------------------------

def test_gc_narrative_logs_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
    _mem_dir(tmp_path)
    found, removed = _gc_narrative_logs(retain_days=180, dry_run=True)
    assert found == 0


def test_gc_narrative_logs_removes_old(monkeypatch, tmp_path):
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
    mem = _mem_dir(tmp_path)
    _write_narrative(mem, days_ago=200)  # old
    _write_narrative(mem, days_ago=5)    # recent
    found, removed = _gc_narrative_logs(retain_days=180, dry_run=True)
    assert found == 2
    assert removed == 1


def test_gc_narrative_logs_dry_run_keeps_files(monkeypatch, tmp_path):
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
    mem = _mem_dir(tmp_path)
    old = _write_narrative(mem, days_ago=200)
    _gc_narrative_logs(retain_days=180, dry_run=True)
    assert old.exists()


def test_gc_narrative_logs_live_deletes(monkeypatch, tmp_path):
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
    mem = _mem_dir(tmp_path)
    old = _write_narrative(mem, days_ago=200)
    recent = _write_narrative(mem, days_ago=5)
    _gc_narrative_logs(retain_days=180, dry_run=False)
    assert not old.exists()
    assert recent.exists()


# ---------------------------------------------------------------------------
# _gc_tiered_lessons
# ---------------------------------------------------------------------------

def test_gc_tiered_lessons_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
    _mem_dir(tmp_path)
    removed = _gc_tiered_lessons(dry_run=True)
    assert removed == 0


def test_gc_tiered_lessons_removes_below_threshold(monkeypatch, tmp_path):
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
    mem = _mem_dir(tmp_path)
    _write_tiered_lesson(mem, "medium", score=0.1)  # below GC_THRESHOLD (0.2)
    _write_tiered_lesson(mem, "medium", score=0.5)  # above threshold
    removed = _gc_tiered_lessons(dry_run=True)
    assert removed == 1


def test_gc_tiered_lessons_live_removes(monkeypatch, tmp_path):
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
    mem = _mem_dir(tmp_path)
    _write_tiered_lesson(mem, "medium", score=0.1)
    _write_tiered_lesson(mem, "medium", score=0.8)
    _gc_tiered_lessons(dry_run=False)
    lines = (mem / "medium" / "lessons.jsonl").read_text().splitlines()
    assert len(lines) == 1
    kept = json.loads(lines[0])
    assert kept["score"] == 0.8


# ---------------------------------------------------------------------------
# run_gc (top-level)
# ---------------------------------------------------------------------------

def test_run_gc_returns_report(monkeypatch, tmp_path):
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
    _mem_dir(tmp_path)
    report = run_gc(dry_run=True)
    assert isinstance(report, GCReport)
    assert report.dry_run is True


def test_run_gc_summary_contains_sections(monkeypatch, tmp_path):
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
    _mem_dir(tmp_path)
    report = run_gc(dry_run=True)
    summary = report.summary()
    assert "outcomes" in summary
    assert "audit" in summary
    assert "lessons" in summary
    assert "narratives" in summary
    assert "DRY RUN" in summary


def test_run_gc_live_cleans_old_data(monkeypatch, tmp_path):
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
    mem = _mem_dir(tmp_path)
    _write_outcome(mem, days_ago=100)
    _write_outcome(mem, days_ago=1)
    report = run_gc(outcomes_retain_days=90, dry_run=False)
    assert report.outcomes_removed == 1
    assert report.outcomes_retained == 1
    assert report.dry_run is False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_main_status_runs(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
    _mem_dir(tmp_path)
    gc_memory.main(["status"])
    out = capsys.readouterr().out
    assert "DRY RUN" in out
    assert "outcomes" in out


def test_main_no_args_shows_status(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
    _mem_dir(tmp_path)
    gc_memory.main([])
    out = capsys.readouterr().out
    assert "DRY RUN" in out


def test_main_run_dry_run_flag(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
    _mem_dir(tmp_path)
    gc_memory.main(["run", "--dry-run"])
    out = capsys.readouterr().out
    assert "DRY RUN" in out


def test_main_run_yes_executes(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
    mem = _mem_dir(tmp_path)
    _write_outcome(mem, days_ago=100)
    gc_memory.main(["run", "--yes", "--outcomes-retain-days", "90"])
    out = capsys.readouterr().out
    assert "GC Summary" in out
    assert "DRY RUN" not in out
