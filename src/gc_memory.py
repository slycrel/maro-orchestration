"""poe-gc — Memory garbage collection and JSONL rotation (Phase 25).

Prevents unbounded disk growth from:
- outcomes.jsonl accumulating indefinitely
- tiered lessons staying below GC_THRESHOLD (already decayed below useful range)
- sandbox-audit.jsonl growing without limit
- Daily narrative logs (YYYY-MM-DD.md) older than retention window

Usage:
    poe-gc status          → show what would be collected (dry run)
    poe-gc run             → execute GC (with confirmation in interactive mode)
    poe-gc run --yes       → execute without prompting (for cron/systemd)
    poe-gc run --dry-run   → alias for status
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_OUTCOMES_RETAIN_DAYS = 90      # keep outcomes for 90 days
DEFAULT_AUDIT_RETAIN_ENTRIES = 1000    # keep last 1000 sandbox audit entries
DEFAULT_NARRATIVE_RETAIN_DAYS = 180    # keep daily .md logs for 180 days


# ---------------------------------------------------------------------------
# Path helpers (mirrors memory.py)
# ---------------------------------------------------------------------------

def _memory_dir() -> Path:
    from orch_items import memory_dir
    return memory_dir()


def _outcomes_path() -> Path:
    return _memory_dir() / "outcomes.jsonl"


def _audit_path() -> Path:
    return _memory_dir() / "sandbox-audit.jsonl"


def _tiered_lessons_path(tier: str) -> Path:
    return _memory_dir() / tier / "lessons.jsonl"


# ---------------------------------------------------------------------------
# GC result
# ---------------------------------------------------------------------------

@dataclass
class GCReport:
    outcomes_total: int = 0
    outcomes_removed: int = 0
    outcomes_retained: int = 0
    audit_total: int = 0
    audit_removed: int = 0
    audit_retained: int = 0
    lessons_gc_removed: int = 0       # below GC_THRESHOLD
    narrative_logs_removed: int = 0
    bytes_freed: int = 0
    errors: List[str] = field(default_factory=list)
    dry_run: bool = True

    def summary(self) -> str:
        lines = []
        status = "DRY RUN — " if self.dry_run else ""
        lines.append(f"{status}GC Summary")
        lines.append(f"  outcomes:   {self.outcomes_removed}/{self.outcomes_total} removed ({self.outcomes_retained} retained)")
        lines.append(f"  audit:      {self.audit_removed}/{self.audit_total} removed ({self.audit_retained} retained)")
        lines.append(f"  lessons:    {self.lessons_gc_removed} below-threshold entries removed")
        lines.append(f"  narratives: {self.narrative_logs_removed} daily log(s) removed")
        if self.bytes_freed:
            lines.append(f"  freed:      {self.bytes_freed // 1024} KB")
        if self.errors:
            for e in self.errors:
                lines.append(f"  ⚠  {e}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# GC operations
# ---------------------------------------------------------------------------

def _gc_outcomes(
    retain_days: int = DEFAULT_OUTCOMES_RETAIN_DAYS,
    *,
    dry_run: bool = True,
) -> tuple[int, int, int]:
    """Return (total, removed, freed_bytes). Rewrites file if not dry_run."""
    path = _outcomes_path()
    if not path.exists():
        return 0, 0, 0

    cutoff = datetime.now(timezone.utc) - timedelta(days=retain_days)
    keep = []
    total = 0

    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            total += 1
            try:
                d = json.loads(line)
                ts_str = d.get("recorded_at") or d.get("timestamp", "")
                if ts_str:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    if ts >= cutoff:
                        keep.append(line)
                    # else: drop (too old)
                else:
                    keep.append(line)  # no timestamp → keep conservatively
            except Exception:
                keep.append(line)  # parse error → keep conservatively
    except Exception:
        return 0, 0, 0

    removed = total - len(keep)
    freed = 0

    if removed > 0 and not dry_run:
        original_size = path.stat().st_size
        path.write_text("\n".join(keep) + ("\n" if keep else ""), encoding="utf-8")
        freed = original_size - path.stat().st_size

    return total, removed, freed


def _gc_audit(
    retain_entries: int = DEFAULT_AUDIT_RETAIN_ENTRIES,
    *,
    dry_run: bool = True,
) -> tuple[int, int, int]:
    """Trim audit log to last N entries. Return (total, removed, freed_bytes)."""
    path = _audit_path()
    if not path.exists():
        return 0, 0, 0

    try:
        lines = [l.strip() for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    except Exception:
        return 0, 0, 0

    total = len(lines)
    if total <= retain_entries:
        return total, 0, 0

    keep = lines[-retain_entries:]
    removed = total - len(keep)
    freed = 0

    if not dry_run:
        original_size = path.stat().st_size
        path.write_text("\n".join(keep) + "\n", encoding="utf-8")
        freed = original_size - path.stat().st_size

    return total, removed, freed


def _gc_tiered_lessons(*, dry_run: bool = True) -> int:
    """Remove tiered lessons below GC_THRESHOLD. Returns count removed."""
    try:
        from memory import GC_THRESHOLD, MemoryTier, load_tiered_lessons, _rewrite_tiered_lessons
    except ImportError:
        return 0

    removed_total = 0

    for tier in (MemoryTier.MEDIUM, MemoryTier.LONG):
        path = _tiered_lessons_path(tier)
        if not path.exists():
            continue
        all_lessons = load_tiered_lessons(tier, min_score=0.0)
        above_threshold = [l for l in all_lessons if l.score >= GC_THRESHOLD]
        removed = len(all_lessons) - len(above_threshold)
        if removed > 0 and not dry_run:
            _rewrite_tiered_lessons(tier, above_threshold)
            # Captain's log: lesson decay
            try:
                from captains_log import log_event, LESSON_DECAYED
                log_event(
                    event_type=LESSON_DECAYED,
                    subject=f"{tier} tier",
                    summary=f"GC removed {removed} lessons below threshold from {tier} tier.",
                    context={"tier": tier, "removed": removed, "remaining": len(above_threshold)},
                )
            except Exception:
                pass
        removed_total += removed

    return removed_total


def _gc_narrative_logs(
    retain_days: int = DEFAULT_NARRATIVE_RETAIN_DAYS,
    *,
    dry_run: bool = True,
) -> tuple[int, int]:
    """Remove daily YYYY-MM-DD.md narrative logs older than retain_days. Return (found, removed)."""
    mem = _memory_dir()
    if not mem.exists():
        return 0, 0

    cutoff = datetime.now(timezone.utc) - timedelta(days=retain_days)
    found = 0
    removed = 0

    for f in mem.glob("????-??-??.md"):
        found += 1
        try:
            date_str = f.stem  # YYYY-MM-DD
            file_date = datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc)
            if file_date < cutoff:
                if not dry_run:
                    f.unlink()
                removed += 1
        except Exception:
            continue

    return found, removed


# ---------------------------------------------------------------------------
# Top-level GC run
# ---------------------------------------------------------------------------

def run_gc(
    outcomes_retain_days: int = DEFAULT_OUTCOMES_RETAIN_DAYS,
    audit_retain_entries: int = DEFAULT_AUDIT_RETAIN_ENTRIES,
    narrative_retain_days: int = DEFAULT_NARRATIVE_RETAIN_DAYS,
    *,
    dry_run: bool = True,
) -> GCReport:
    report = GCReport(dry_run=dry_run)

    try:
        total, removed, freed = _gc_outcomes(retain_days=outcomes_retain_days, dry_run=dry_run)
        report.outcomes_total = total
        report.outcomes_removed = removed
        report.outcomes_retained = total - removed
        report.bytes_freed += freed
    except Exception as e:
        report.errors.append(f"outcomes GC failed: {e}")

    try:
        total, removed, freed = _gc_audit(retain_entries=audit_retain_entries, dry_run=dry_run)
        report.audit_total = total
        report.audit_removed = removed
        report.audit_retained = total - removed
        report.bytes_freed += freed
    except Exception as e:
        report.errors.append(f"audit GC failed: {e}")

    try:
        report.lessons_gc_removed = _gc_tiered_lessons(dry_run=dry_run)
    except Exception as e:
        report.errors.append(f"lessons GC failed: {e}")

    try:
        _, removed = _gc_narrative_logs(retain_days=narrative_retain_days, dry_run=dry_run)
        report.narrative_logs_removed = removed
    except Exception as e:
        report.errors.append(f"narrative GC failed: {e}")

    return report


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    import argparse
    parser = argparse.ArgumentParser(
        prog="poe-gc",
        description="Memory GC — prevent unbounded disk growth",
    )
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("status", help="Show what would be collected (dry run)")

    p_run = sub.add_parser("run", help="Execute GC")
    p_run.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")
    p_run.add_argument("--dry-run", action="store_true", help="Alias for status")
    p_run.add_argument("--outcomes-retain-days", type=int, default=DEFAULT_OUTCOMES_RETAIN_DAYS)
    p_run.add_argument("--audit-retain-entries", type=int, default=DEFAULT_AUDIT_RETAIN_ENTRIES)
    p_run.add_argument("--narrative-retain-days", type=int, default=DEFAULT_NARRATIVE_RETAIN_DAYS)

    args = parser.parse_args(argv)

    if args.cmd == "status" or (args.cmd == "run" and args.dry_run):
        report = run_gc(dry_run=True)
        print(report.summary())
        return

    if args.cmd == "run":
        if not args.yes:
            # Show dry run first
            preview = run_gc(dry_run=True)
            print(preview.summary())
            print()
            try:
                answer = input("Proceed? [y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\nAborted.")
                return
            if answer not in ("y", "yes"):
                print("Aborted.")
                return

        report = run_gc(
            outcomes_retain_days=args.outcomes_retain_days,
            audit_retain_entries=args.audit_retain_entries,
            narrative_retain_days=args.narrative_retain_days,
            dry_run=False,
        )
        print(report.summary())
    else:
        # No subcommand → show status
        report = run_gc(dry_run=True)
        print(report.summary())


if __name__ == "__main__":
    main()
