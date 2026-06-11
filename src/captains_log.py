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
SKILL_SYNTHESIS_REJECTED = "SKILL_SYNTHESIS_REJECTED"  # 3-gate quality check rejected a candidate
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
# Decay-by-invalidation v0 (2026-06-11): a contested standing rule was
# re-derived against its contradiction evidence (knowledge_lens.refight_rule,
# run from the evolver cycle). Context carries action (keep|revise|retire),
# reasoning, and old/new rule text — the audit trail for trust changes.
RULE_REFOUGHT = "RULE_REFOUGHT"
# Freshness signal (2026-06-11): a promoted standing rule was re-confirmed in
# production (observe_pattern matched an existing rule). Stamps last_verified —
# the signal that keeps an uncontradicted rule out of the stale
# verify-before-relying injection block.
RULE_VERIFIED = "RULE_VERIFIED"
# Log maintenance (2026-06-11): the active file exceeded its size gate and
# older entries moved to a timestamped archive (data never deleted). First
# entry of each fresh active file — the rotation audit trail.
LOG_ROTATED = "LOG_ROTATED"

# Evolver actions
EVOLVER_APPLIED = "EVOLVER_APPLIED"
EVOLVER_GENERATED = "EVOLVER_GENERATED"
EVOLVER_SKIPPED = "EVOLVER_SKIPPED"
GRADUATION_PROPOSED = "GRADUATION_PROPOSED"

# Recovery & diagnosis
AUTO_RECOVERY = "AUTO_RECOVERY"
DIAGNOSIS = "DIAGNOSIS"
INPUT_MISMATCH = "INPUT_MISMATCH"  # skill invoked on out-of-domain input

# Memory lifecycle maintenance (session 40): in-process consolidation —
# decay-consequence cycle (promote/GC) that rides along app lifecycle calls.
MEMORY_CONSOLIDATED = "MEMORY_CONSOLIDATED"

# Decisions
DECISION_RECORDED = "DECISION_RECORDED"
METACOGNITIVE_DECISION = "METACOGNITIVE_DECISION"  # mid-loop re-decompose/retry decisions

# Phase 65 scope + closure observability
SCOPE_GENERATED = "SCOPE_GENERATED"
SCOPE_PARSE_FAILED = "SCOPE_PARSE_FAILED"
# Scope generation was enabled but produced nothing (adapter error or None
# return). During the May-2026 rc=1 outage every run silently lost its scope —
# nothing recorded that scoping was skipped (goal-brain pressure test finding).
SCOPE_SKIPPED = "SCOPE_SKIPPED"
CLOSURE_VERDICT = "CLOSURE_VERDICT"  # per-check modality distribution + pass/gap counts
CLAIM_PROBED = "CLAIM_PROBED"  # adversarial-review grounding: per-claim probe outcome
CLAIM_VERIFIER_OUTCOME = "CLAIM_VERIFIER_OUTCOME"  # per-step: file/symbol claim verification outcome + downstream action

# Run transparency: loop lifecycle + quality-gate decisions
LOOP_CREATED = "LOOP_CREATED"  # every loop spawn — reason, parent_loop_id, slug, max_steps
QUALITY_GATE_VERDICT = "QUALITY_GATE_VERDICT"  # PASS / ESCALATE — most important escalation signal

# Per-step resource-burn signal: step exceeded the cap from the
# decomposition_too_broad post-mortem note (≤120s and ≤200K tokens per step).
# Fires mid-loop so the warning is visible without waiting for the loop to
# finish — addresses BACKLOG:316 leftover (8/8-strong loops where the
# post-mortem warning fires too late to act on this loop).
STEP_TOO_BROAD = "STEP_TOO_BROAD"

# recall() seam instrumentation (goal-brain step 3, docs/RECALL_DESIGN.md).
# Every recall() call logs one RECALL_PERFORMED — these tuples are the
# crystallization substrate per the 2026-05-18 static-now-instrument-everything
# decision. RECALL_GUARD_TRIPPED fires when the dispatch guard refuses to
# re-run a goal whose recent attempts all failed (the ~25x repeat burn,
# 2026-05-17).
RECALL_PERFORMED = "RECALL_PERFORMED"
RECALL_GUARD_TRIPPED = "RECALL_GUARD_TRIPPED"

# Navigator instrumentation (goal-brain step 4, docs/NAVIGATOR_SCHEMA.md).
# One per navigator invocation, shadow or live: input digest, decision, tier,
# and (in shadow mode) what the static pipeline actually did — the divergence
# signal that earns per-class cutover. Visibility + crystallization substrate;
# nothing reads it for control flow.
NAVIGATOR_DECIDED = "NAVIGATOR_DECIDED"

EVENT_TYPES = {
    SKILL_SYNTHESIZED, SKILL_SYNTHESIS_REJECTED, SKILL_PROMOTED, SKILL_DEMOTED, SKILL_REWRITE,
    SKILL_CIRCUIT_OPEN, SKILL_CIRCUIT_HALF_OPEN, SKILL_CIRCUIT_CLOSED,
    SKILL_VARIANT_CREATED, AB_RETIRED, ISLAND_CULLED,
    LESSON_RECORDED, LESSON_REINFORCED, LESSON_DECAYED, LESSON_RECOVERED,
    MEMORY_CONSOLIDATED,
    HYPOTHESIS_CREATED, HYPOTHESIS_PROMOTED, HYPOTHESIS_CONTRADICTED,
    STANDING_RULE_CONTRADICTED, RULE_GRADUATED, RULE_DEMOTED, CANON_CANDIDATE,
    RULE_REFOUGHT, RULE_VERIFIED, LOG_ROTATED,
    EVOLVER_APPLIED, EVOLVER_GENERATED, EVOLVER_SKIPPED, GRADUATION_PROPOSED,
    AUTO_RECOVERY, DIAGNOSIS, INPUT_MISMATCH,
    DECISION_RECORDED, METACOGNITIVE_DECISION,
    SCOPE_GENERATED, SCOPE_PARSE_FAILED, SCOPE_SKIPPED, CLOSURE_VERDICT, CLAIM_PROBED,
    CLAIM_VERIFIER_OUTCOME,
    LOOP_CREATED, QUALITY_GATE_VERDICT, STEP_TOO_BROAD,
    RECALL_PERFORMED, RECALL_GUARD_TRIPPED,
    NAVIGATOR_DECIDED,
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


def _archive_paths() -> List[Path]:
    """Rotated archive files, oldest first. The timestamped name pattern
    (captains_log.<stamp>.jsonl) never matches the active captains_log.jsonl,
    and lexicographic sort == chronological for the fixed-width stamp."""
    try:
        return sorted(_log_path().parent.glob("captains_log.*.jsonl"))
    except Exception:
        return []


def _all_log_paths() -> List[Path]:
    """Archives (oldest first) + the active file — the full corpus, in
    chronological order. Used by the archaeology readers (query_log,
    timeline); the hot-path reader (load_log) stays active-file-only."""
    return [*_archive_paths(), _log_path()]


# ---------------------------------------------------------------------------
# Input type classification (for INPUT_MISMATCH detection)
# ---------------------------------------------------------------------------

import re as _re

_URL_RE = _re.compile(r"https?://\S+", _re.IGNORECASE)
_CODE_INDICATORS = frozenset({"def ", "class ", "import ", "function ", "return ", "```"})
_STRUCTURED_INDICATORS = frozenset({"{", "}", '":', "]: "})


def classify_input_type(text: str) -> str:
    """Classify the input domain of a goal or step text.

    Returns one of: "url", "code", "structured_data", "plain_text".

    Used to detect INPUT_MISMATCH when a skill trained on one input type
    (e.g., web content from Jina) is invoked with a different type.
    """
    if not text:
        return "plain_text"

    # URL-heavy: Jina-style web fetch, link analysis
    url_count = len(_URL_RE.findall(text))
    if url_count >= 2 or (url_count == 1 and len(text) < 200):
        return "url"

    # Code: function definitions, imports, code blocks
    text_lower = text.lower()
    code_hits = sum(1 for kw in _CODE_INDICATORS if kw.lower() in text_lower)
    if code_hits >= 2:
        return "code"

    # Structured data: JSON-like or tabular
    struct_hits = sum(1 for kw in _STRUCTURED_INDICATORS if kw in text)
    if struct_hits >= 3:
        return "structured_data"

    return "plain_text"


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
        from file_lock import locked_append
        locked_append(path, json.dumps(entry))
    except Exception as exc:
        logger.warning("captains_log: write failed: %s", exc)

    _maybe_rotate()
    return entry


_rotation_in_progress = False


def _maybe_rotate() -> None:
    """Size-gated rotation, riding on log_event (no cron — the no-scheduler
    invariant). Never raises; never deletes data.

    When the active file exceeds `captains_log.rotate_mb` (default 5, 0
    disables), everything but the most recent `captains_log.rotate_keep`
    entries (default 1000) moves to a timestamped archive beside it. The
    retained tail keeps recent-window reads (load_log on the recall hot
    path) working without spanning files; query_log/timeline span archives.
    The point is read cost, not disk: load_log JSON-parses the whole active
    file per call, and it sits on every dispatch recall.
    """
    global _rotation_in_progress
    if _rotation_in_progress:
        # The LOG_ROTATED audit append lands in the fresh active file; without
        # this guard a threshold smaller than the retained tail cascades.
        return
    try:
        path = _log_path()
        if not path.exists():
            return
        try:
            from config import get as _cfg_get
            rotate_mb = float(_cfg_get("captains_log.rotate_mb", 5))
            keep = int(_cfg_get("captains_log.rotate_keep", 1000))
        except Exception:
            rotate_mb, keep = 5.0, 1000
        if rotate_mb <= 0:
            return
        max_bytes = int(rotate_mb * 1024 * 1024)
        if path.stat().st_size < max_bytes:
            return

        from file_lock import locked_write
        rotated_to = None
        _rotation_in_progress = True
        with locked_write(path):
            # Re-check under the lock — another process may have rotated.
            if not path.exists() or path.stat().st_size < max_bytes:
                return
            lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
            keep = max(keep, 0)
            head, tail = (lines[:-keep], lines[-keep:]) if keep else (lines, [])
            if not head:
                return  # fewer entries than the tail retention; nothing to move
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            archive = path.with_name(f"captains_log.{stamp}.jsonl")
            n = 0
            while archive.exists():  # same-second rotation must not overwrite
                n += 1
                archive = path.with_name(f"captains_log.{stamp}-{n}.jsonl")
            archive.write_text("\n".join(head) + "\n", encoding="utf-8")
            path.write_text(
                "\n".join(tail) + ("\n" if tail else ""), encoding="utf-8")
            rotated_to = archive

        if rotated_to is not None:
            logger.info("captains_log: rotated %d entries to %s (kept %d)",
                        len(head), rotated_to.name, len(tail))
            log_event(
                event_type=LOG_ROTATED,
                subject=rotated_to.name,
                summary=(f"Rotated {len(head)} entries to {rotated_to.name}; "
                         f"{len(tail)} retained in the active file"),
                context={"archived": len(head), "retained": len(tail),
                         "archive": rotated_to.name},
            )
    except Exception as exc:
        logger.warning("captains_log: rotation failed: %s", exc)
    finally:
        _rotation_in_progress = False


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

    Reads the active file only — this sits on the dispatch recall hot path,
    and rotation retains a recent tail precisely so this stays one small
    file. Use query_log for history that may span rotated archives.
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
# Historical query — full-text search across entire log
# ---------------------------------------------------------------------------

def query_log(
    query: str = "",
    *,
    since: Optional[str] = None,
    until: Optional[str] = None,
    event_type: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """Full-text search across all captain's log fields.

    Unlike load_log() which only searches subject, this searches across
    summary, note, subject, context values, and related_ids. Designed
    for historical archaeology across the full 14K+ event corpus.

    Args:
        query: Search string (case-insensitive). Empty = match all.
        since: ISO date (e.g. "2026-04-09"). Entries on or after.
        until: ISO date (e.g. "2026-04-11"). Entries strictly before.
        event_type: Filter by event type prefix.
        limit: Max results (0 = unlimited). Default 100.

    Returns:
        Matching entries, most recent first.

    Spans rotated archives (oldest first) plus the active file, so rotation
    never hides history from archaeology.
    """
    query_lower = query.lower()
    entries: List[Dict[str, Any]] = []

    for path in _all_log_paths():
        if not path.exists():
            continue
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                ts = entry.get("timestamp", "")
                if since and ts < since:
                    continue
                if until and ts >= until:
                    continue
                if event_type and not entry.get("event_type", "").startswith(event_type.upper()):
                    continue

                # Full-text match across all string fields
                if query_lower:
                    searchable = " ".join([
                        entry.get("subject", ""),
                        entry.get("summary", ""),
                        entry.get("note", ""),
                        entry.get("loop_id", ""),
                        " ".join(entry.get("related_ids", [])),
                        # Flatten context values
                        " ".join(str(v) for v in (entry.get("context") or {}).values()),
                    ]).lower()
                    if query_lower not in searchable:
                        continue

                entries.append(entry)

    entries.reverse()  # Most recent first
    if limit > 0:
        entries = entries[:limit]
    return entries


# ---------------------------------------------------------------------------
# Timeline — aggregate event counts by day and type
# ---------------------------------------------------------------------------

def timeline(
    *,
    since: Optional[str] = None,
    until: Optional[str] = None,
    bucket: str = "day",
) -> List[Dict[str, Any]]:
    """Aggregate event counts by time bucket.

    Returns a list of {date, total, by_type: {EVENT_TYPE: count}} dicts,
    one per bucket. Useful for spotting when failure patterns emerged.

    Args:
        since/until: Date range filters.
        bucket: "day" (default) or "hour".

    Spans rotated archives plus the active file.
    """
    from collections import Counter

    buckets: Dict[str, Counter] = {}
    for path in _all_log_paths():
        if not path.exists():
            continue
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                ts = entry.get("timestamp", "")
                if since and ts < since:
                    continue
                if until and ts >= until:
                    continue

                if bucket == "hour":
                    key = ts[:13]  # "2026-04-10T03"
                else:
                    key = ts[:10]  # "2026-04-10"

                if key not in buckets:
                    buckets[key] = Counter()
                buckets[key][entry.get("event_type", "UNKNOWN")] += 1

    result = []
    for date_key in sorted(buckets.keys()):
        counts = buckets[date_key]
        result.append({
            "date": date_key,
            "total": sum(counts.values()),
            "by_type": dict(counts.most_common()),
        })
    return result


# ---------------------------------------------------------------------------
# Git commit correlation
# ---------------------------------------------------------------------------

def correlate_with_git(
    entries: List[Dict[str, Any]],
    *,
    window_hours: int = 2,
    repo_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Cross-reference log entries with git commits by timestamp.

    For each entry, finds git commits within ±window_hours of the event.
    This reveals what code changes landed around the time failures or
    improvements appeared.

    Args:
        entries: Log entries (from query_log or load_log).
        window_hours: Hours before/after event to search for commits.
        repo_path: Path to git repo (default: auto-detect from orch_root).

    Returns:
        Entries augmented with "nearby_commits" field.
    """
    if not entries:
        return entries

    import subprocess

    # Find repo path
    if repo_path is None:
        try:
            from orch_items import orch_root
            repo_path = str(orch_root())
        except Exception:
            repo_path = "."

    # Load all git commits with timestamps
    try:
        result = subprocess.run(
            ["git", "log", "--format=%H|%aI|%s", "--all"],
            capture_output=True, text=True, cwd=repo_path, timeout=10,
        )
        if result.returncode != 0:
            return entries
    except Exception:
        return entries

    commits = []
    for line in result.stdout.strip().splitlines():
        parts = line.split("|", 2)
        if len(parts) == 3:
            commits.append({
                "hash": parts[0][:12],
                "timestamp": parts[1],
                "message": parts[2],
            })

    if not commits:
        return entries

    # For each entry, find commits within the time window
    from datetime import timedelta

    def _parse_ts(ts_str: str) -> Optional[datetime]:
        try:
            # Handle both +00:00 and Z formats
            ts_str = ts_str.replace("Z", "+00:00")
            if "+" not in ts_str and "-" not in ts_str[10:]:
                ts_str += "+00:00"
            return datetime.fromisoformat(ts_str)
        except (ValueError, TypeError):
            return None

    window = timedelta(hours=window_hours)
    augmented = []
    for entry in entries:
        entry_ts = _parse_ts(entry.get("timestamp", ""))
        if entry_ts is None:
            augmented.append(entry)
            continue

        nearby = []
        for commit in commits:
            commit_ts = _parse_ts(commit["timestamp"])
            if commit_ts is None:
                continue
            delta = abs((entry_ts - commit_ts).total_seconds())
            if delta <= window.total_seconds():
                nearby.append({
                    "hash": commit["hash"],
                    "message": commit["message"][:80],
                    "timestamp": commit["timestamp"][:19],
                    "delta_hours": round(delta / 3600, 1),
                })

        # Sort by proximity
        nearby.sort(key=lambda c: c["delta_hours"])
        entry_copy = dict(entry)
        if nearby:
            entry_copy["nearby_commits"] = nearby[:5]  # Top 5 closest
        augmented.append(entry_copy)

    return augmented


def render_correlated_entry(entry: Dict[str, Any]) -> str:
    """Render a log entry with git commit correlation."""
    base = render_entry(entry)
    commits = entry.get("nearby_commits", [])
    if commits:
        base += "\n  Git:"
        for c in commits[:3]:
            base += f"\n    [{c['hash']}] {c['message']} (±{c['delta_hours']}h)"
    return base


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
    parser.add_argument("query", nargs="?", default="",
                        help="Full-text search across all fields (optional)")
    parser.add_argument("--since", help="Show entries from this date (YYYY-MM-DD)")
    parser.add_argument("--until", help="Show entries before this date (YYYY-MM-DD)")
    parser.add_argument("--type", dest="event_type", help="Filter by event type prefix (e.g. SKILL, EVOLVER)")
    parser.add_argument("--subject", help="Filter by subject substring")
    parser.add_argument("--limit", type=int, default=20, help="Max entries (default: 20, 0=unlimited)")
    parser.add_argument("--json", action="store_true", help="Output raw JSON instead of rendered text")
    parser.add_argument("--git", action="store_true", help="Show correlated git commits")
    parser.add_argument("--timeline", action="store_true", help="Show event count timeline")

    args = parser.parse_args()

    # Timeline mode
    if args.timeline:
        tl = timeline(since=args.since, until=args.until)
        if not tl:
            print("No events found.")
            return
        for bucket in tl:
            top_types = ", ".join(f"{t}:{c}" for t, c in list(bucket["by_type"].items())[:5])
            print(f"{bucket['date']}  {bucket['total']:>5} events  [{top_types}]")
        return

    # Query mode (full-text search)
    if args.query or args.until:
        entries = query_log(
            args.query,
            since=args.since,
            until=args.until,
            event_type=args.event_type,
            limit=args.limit,
        )
    else:
        entries = load_log(
            since=args.since,
            event_type=args.event_type,
            subject=args.subject,
            limit=args.limit,
        )

    if not entries:
        print("No log entries found.")
        return

    # Git correlation
    if args.git:
        entries = correlate_with_git(entries)

    if args.json:
        for e in entries:
            print(json.dumps(e))
    else:
        renderer = render_correlated_entry if args.git else render_entry
        print("\n\n".join(renderer(e) for e in entries))


if __name__ == "__main__":
    main()
