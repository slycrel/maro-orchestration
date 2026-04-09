"""Captain's Log — narrated learning-system changelog.

Append-only event stream tracking every action the learning pipeline takes.
Not raw data (that's outcomes.jsonl). Not aggregated metrics (that's the dashboard).
A human-readable changelog of what the system decided about its own knowledge.

Usage:
    from captains_log import log_event, render_log, EVENT_TYPES

    log_event(
        event_type="SKILL_CIRCUIT_OPEN",
        subject="jina-x-scraper",
        summary="Hit 3 consecutive failures. Utility: 0.82 -> 0.61.",
        context={"utility_before": 0.82, "utility_after": 0.61},
        note="Failures may reflect input mismatch, not skill degradation.",
        related_ids=["skill:jina-x-scraper"],
    )
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Event type constants
# ---------------------------------------------------------------------------

# Skill lifecycle
SKILL_SYNTHESIZED = "SKILL_SYNTHESIZED"
SKILL_PROMOTED = "SKILL_PROMOTED"
SKILL_DEMOTED = "SKILL_DEMOTED"
SKILL_REWRITE = "SKILL_REWRITE"
SKILL_CIRCUIT_OPEN = "SKILL_CIRCUIT_OPEN"
SKILL_CIRCUIT_HALF_OPEN = "SKILL_CIRCUIT_HALF_OPEN"
SKILL_CIRCUIT_CLOSED = "SKILL_CIRCUIT_CLOSED"
SKILL_VARIANT_CREATED = "SKILL_VARIANT_CREATED"
AB_RETIRED = "AB_RETIRED"
ISLAND_CULLED = "ISLAND_CULLED"

# Knowledge crystallization
LESSON_RECORDED = "LESSON_RECORDED"
LESSON_REINFORCED = "LESSON_REINFORCED"
LESSON_DECAYED = "LESSON_DECAYED"
LESSON_RECOVERED = "LESSON_RECOVERED"
HYPOTHESIS_CREATED = "HYPOTHESIS_CREATED"
HYPOTHESIS_PROMOTED = "HYPOTHESIS_PROMOTED"
HYPOTHESIS_CONTRADICTED = "HYPOTHESIS_CONTRADICTED"
STANDING_RULE_CONTRADICTED = "STANDING_RULE_CONTRADICTED"
RULE_GRADUATED = "RULE_GRADUATED"
RULE_DEMOTED = "RULE_DEMOTED"
CANON_CANDIDATE = "CANON_CANDIDATE"

# Evolver actions
EVOLVER_APPLIED = "EVOLVER_APPLIED"
EVOLVER_GENERATED = "EVOLVER_GENERATED"
EVOLVER_SKIPPED = "EVOLVER_SKIPPED"
GRADUATION_PROPOSED = "GRADUATION_PROPOSED"

# Recovery & diagnosis
AUTO_RECOVERY = "AUTO_RECOVERY"
DIAGNOSIS = "DIAGNOSIS"

# Decisions
DECISION_RECORDED = "DECISION_RECORDED"

EVENT_TYPES = {
    SKILL_SYNTHESIZED, SKILL_PROMOTED, SKILL_DEMOTED, SKILL_REWRITE,
    SKILL_CIRCUIT_OPEN, SKILL_CIRCUIT_HALF_OPEN, SKILL_CIRCUIT_CLOSED,
    SKILL_VARIANT_CREATED, AB_RETIRED, ISLAND_CULLED,
    LESSON_RECORDED, LESSON_REINFORCED, LESSON_DECAYED, LESSON_RECOVERED,
    HYPOTHESIS_CREATED, HYPOTHESIS_PROMOTED, HYPOTHESIS_CONTRADICTED,
    STANDING_RULE_CONTRADICTED, RULE_GRADUATED, RULE_DEMOTED, CANON_CANDIDATE,
    EVOLVER_APPLIED, EVOLVER_GENERATED, EVOLVER_SKIPPED, GRADUATION_PROPOSED,
    AUTO_RECOVERY, DIAGNOSIS, DECISION_RECORDED,
}

# ---------------------------------------------------------------------------
# Path
# ---------------------------------------------------------------------------

_log_path_override: Optional[Path] = None


def _log_path() -> Path:
    if _log_path_override is not None:
        return _log_path_override
    from config import memory_dir
    return memory_dir() / "captains_log.jsonl"


def set_log_path(path: Optional[Path]) -> None:
    """Override log path (for testing)."""
    global _log_path_override
    _log_path_override = path


# ---------------------------------------------------------------------------
# Core: log_event
# ---------------------------------------------------------------------------

def log_event(
    event_type: str,
    subject: str,
    summary: str,
    context: Optional[Dict[str, Any]] = None,
    note: Optional[str] = None,
    loop_id: Optional[str] = None,
    related_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Append a captain's log entry. Never raises on I/O failure."""
    entry: Dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": event_type,
        "subject": subject,
        "summary": summary,
    }
    if context:
        entry["context"] = context
    if note:
        entry["note"] = note
    if loop_id:
        entry["loop_id"] = loop_id
    if related_ids:
        entry["related_ids"] = related_ids

    try:
        path = _log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as exc:
        logger.warning("captains_log: write failed: %s", exc)

    return entry


# ---------------------------------------------------------------------------
# Read & render
# ---------------------------------------------------------------------------

def load_log(
    *,
    since: Optional[str] = None,
    event_type: Optional[str] = None,
    subject: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Load log entries with optional filters.

    Args:
        since: ISO date string (e.g. "2026-04-09"). Only entries on or after.
        event_type: Filter by event type prefix (e.g. "SKILL" matches all SKILL_* events).
        subject: Substring match on subject field.
        limit: Max entries to return (most recent first).
    """
    path = _log_path()
    if not path.exists():
        return []

    entries = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            if since and entry.get("timestamp", "") < since:
                continue
            if event_type and not entry.get("event_type", "").startswith(event_type.upper()):
                continue
            if subject and subject.lower() not in entry.get("subject", "").lower():
                continue

            entries.append(entry)

    # Most recent first, limited
    entries.reverse()
    return entries[:limit]


def render_entry(entry: Dict[str, Any]) -> str:
    """Render a single log entry in captain's log format."""
    ts = entry.get("timestamp", "")[:19].replace("T", " ")
    etype = entry.get("event_type", "UNKNOWN")
    subject = entry.get("subject", "")
    summary = entry.get("summary", "")
    note = entry.get("note")
    loop_id = entry.get("loop_id")

    lines = [f"[{ts}] {etype} — {subject}"]
    if summary:
        lines.append(f"  {summary}")
    if loop_id:
        lines.append(f"  Loop: {loop_id}")
    if note:
        lines.append(f"  Note: {note}")
    return "\n".join(lines)


def render_log(
    *,
    since: Optional[str] = None,
    event_type: Optional[str] = None,
    subject: Optional[str] = None,
    limit: int = 20,
) -> str:
    """Render filtered log entries as human-readable text."""
    entries = load_log(since=since, event_type=event_type, subject=subject, limit=limit)
    if not entries:
        return "No log entries found."
    return "\n\n".join(render_entry(e) for e in entries)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """CLI: poe-log [--since DATE] [--type EVENT_TYPE] [--subject PATTERN] [--limit N]"""
    import argparse

    parser = argparse.ArgumentParser(
        prog="poe-log",
        description="Captain's Log — learning system changelog",
    )
    parser.add_argument("--since", help="Show entries from this date (YYYY-MM-DD)")
    parser.add_argument("--type", dest="event_type", help="Filter by event type prefix (e.g. SKILL, EVOLVER)")
    parser.add_argument("--subject", help="Filter by subject substring")
    parser.add_argument("--limit", type=int, default=20, help="Max entries (default: 20)")
    parser.add_argument("--json", action="store_true", help="Output raw JSON instead of rendered text")

    args = parser.parse_args()

    if args.json:
        entries = load_log(
            since=args.since,
            event_type=args.event_type,
            subject=args.subject,
            limit=args.limit,
        )
        for e in entries:
            print(json.dumps(e))
    else:
        print(render_log(
            since=args.since,
            event_type=args.event_type,
            subject=args.subject,
            limit=args.limit,
        ))


if __name__ == "__main__":
    main()
