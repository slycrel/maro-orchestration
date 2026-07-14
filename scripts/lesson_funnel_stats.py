#!/usr/bin/env python3
"""Measure lesson-extraction intake without treating missing evidence as zero.

Outcome rows are the cohort denominator and now carry dry-run/extraction state.
``LESSON_EXTRACTION`` captain events add tiered-persistence attempt counts.
Legacy non-empty rows prove productive extraction except for the reserved
``[dry-run lesson]`` placeholder; legacy empty rows remain unknown.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

_ARCHIVE_STAMP_RE = re.compile(r"^captains_log\.(\d{8}-\d{6})(?:-\d+)?\.jsonl$")
_STATES = (
    "completed", "deferred", "failed", "unknown_not_instrumented",
    "dry_run_excluded",
)


def _utc(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError, AttributeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _log_paths(active: Path, *, earliest: datetime | None = None) -> list[Path]:
    archives = []
    for path in sorted(active.parent.glob("captains_log.*.jsonl")):
        match = _ARCHIVE_STAMP_RE.match(path.name)
        if earliest is not None and match:
            stamp = datetime.strptime(match.group(1), "%Y%m%d-%H%M%S").replace(
                tzinfo=timezone.utc
            )
            if stamp < earliest:
                continue
        archives.append(path)
    return [*archives, active]


def _load_jsonl(paths: Iterable[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        if not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                row = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def load_outcomes(path: Path) -> list[dict[str, Any]]:
    return _load_jsonl([path])


def load_extraction_events(
    active: Path, *, earliest: datetime | None = None
) -> list[dict[str, Any]]:
    return [
        row for row in _load_jsonl(_log_paths(active, earliest=earliest))
        if row.get("event_type") == "LESSON_EXTRACTION"
    ]


def _latest_events(events: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest: dict[str, tuple[datetime, dict[str, Any]]] = {}
    for event in events:
        context = event.get("context")
        if not isinstance(context, dict):
            continue
        outcome_id = str(context.get("outcome_id", "")).strip()
        timestamp = _utc(str(event.get("timestamp", "")))
        if not outcome_id or timestamp is None:
            continue
        prior = latest.get(outcome_id)
        if prior is None or timestamp >= prior[0]:
            latest[outcome_id] = (timestamp, context)
    return {key: value[1] for key, value in latest.items()}


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def _window(
    outcomes: Iterable[dict[str, Any]],
    event_by_outcome: dict[str, dict[str, Any]],
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    counts = {state: 0 for state in _STATES}
    productive = 0
    extracted_lessons = 0
    tiered_succeeded = 0
    tiered_failed = 0
    seen: set[str] = set()

    for row in outcomes:
        timestamp = _utc(str(row.get("recorded_at", "")))
        if timestamp is None or not (start <= timestamp < end):
            continue
        outcome_id = str(row.get("outcome_id", "")).strip()
        if not outcome_id or outcome_id in seen:
            continue
        seen.add(outcome_id)
        lessons = row.get("lessons")
        historical_count = len(lessons) if isinstance(lessons, list) else 0
        event = event_by_outcome.get(outcome_id)

        inferred_dry_run = bool(
            historical_count
            and all(
                isinstance(lesson, str) and lesson.startswith("[dry-run lesson]")
                for lesson in lessons
            )
        )
        if row.get("dry_run") is True or inferred_dry_run or (
            event is not None and event.get("dry_run") is True
        ):
            counts["dry_run_excluded"] += 1
            continue

        if event is not None:
            try:
                tiered_ok = max(0, int(event.get("tiered_succeeded", 0)))
                tiered_bad = max(0, int(event.get("tiered_failed", 0)))
            except (TypeError, ValueError):
                tiered_ok = tiered_bad = 0
            tiered_succeeded += tiered_ok
            tiered_failed += tiered_bad
        raw_row_state = str(row.get("lesson_extraction_status", "")).strip().lower()
        raw_event_state = (
            str(event.get("status", "")).strip().lower() if event is not None else ""
        )
        if raw_row_state in _STATES[:3]:
            state = raw_row_state
            try:
                lesson_count = max(0, int(row.get("lesson_extraction_count", historical_count)))
            except (TypeError, ValueError):
                lesson_count = historical_count
        elif raw_event_state in _STATES[:3]:
            state = raw_event_state
            try:
                lesson_count = max(0, int(event.get("extracted_count", 0)))
            except (TypeError, ValueError):
                lesson_count = 0
        elif historical_count:
            # Before explicit instrumentation, non-empty outcome lessons are
            # sufficient evidence that extraction completed productively.
            state = "completed"
            lesson_count = historical_count
        else:
            # Empty historically meant several things; never relabel it zero.
            state = "unknown_not_instrumented"
            lesson_count = 0

        counts[state] += 1
        if state == "completed":
            extracted_lessons += lesson_count
            if lesson_count > 0:
                productive += 1

    total = len(seen)
    eligible = total - counts["dry_run_excluded"]
    instrumented = eligible - counts["unknown_not_instrumented"]
    completed = counts["completed"]
    tiered_attempts = tiered_succeeded + tiered_failed
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "total_outcomes": total,
        "eligible_outcomes": eligible,
        "counts": counts,
        "instrumented_outcomes": instrumented,
        "productive_outcomes": productive,
        "extracted_lessons": extracted_lessons,
        "tiered_succeeded": tiered_succeeded,
        "tiered_failed": tiered_failed,
        "instrumentation_coverage": _rate(instrumented, eligible),
        "completion_rate_among_instrumented": _rate(completed, instrumented),
        "productive_rate_among_completed": _rate(productive, completed),
        "average_lessons_per_completed": _rate(extracted_lessons, completed),
        "tiered_persistence_rate": _rate(tiered_succeeded, tiered_attempts),
    }


def calculate(
    outcomes: Iterable[dict[str, Any]],
    events: Iterable[dict[str, Any]],
    *,
    days: int,
    now: datetime,
) -> dict[str, Any]:
    if days <= 0:
        raise ValueError("days must be positive")
    end = now.astimezone(timezone.utc)
    current_start = end - timedelta(days=days)
    previous_start = current_start - timedelta(days=days)
    materialized = list(outcomes)
    latest = _latest_events(events)
    current = _window(materialized, latest, current_start, end)
    previous = _window(materialized, latest, previous_start, current_start)
    metric_keys = (
        "instrumentation_coverage",
        "completion_rate_among_instrumented",
        "productive_rate_among_completed",
        "average_lessons_per_completed",
        "tiered_persistence_rate",
    )
    deltas = {}
    for key in metric_keys:
        cur, prev = current[key], previous[key]
        deltas[key] = round(cur - prev, 6) if cur is not None and prev is not None else None
    return {
        "days": days,
        "semantics": {
            "unknown_not_instrumented": (
                "empty legacy lessons without durable extraction state or a LESSON_EXTRACTION event; "
                "not evidence that extraction ran and yielded zero"
            ),
            "dry_run_excluded": (
                "persisted/event-marked dry runs and legacy [dry-run lesson] placeholders "
                "are outside the production-learning cohort"
            ),
            "cohort_state": (
                "current/previous are recorded-at cohorts classified by latest known "
                "durable extraction state as of report generation; deferred cohorts may "
                "settle differently on a later rerun"
            ),
            "productive_rate_among_completed": (
                "outcomes with >=1 extracted lesson / completed extractions"
            ),
            "tiered_persistence_rate": (
                "successful tiered writes or reinforcements / attempted tiered writes; "
                "available only for explicitly instrumented extraction"
            ),
            "target": "observational report only; no desired funnel rate has been decreed",
        },
        "current": current,
        "previous": previous,
        "delta_current_minus_previous": deltas,
    }


def _fmt(value: float | None, *, percent: bool = True) -> str:
    if value is None:
        return "n/a"
    return f"{100 * value:.1f}%" if percent else f"{value:.2f}"


def render_text(report: dict[str, Any]) -> str:
    cur, prev = report["current"], report["previous"]
    lines = [
        f"Lesson intake funnel — {report['days']}-day rolling cohort",
        f"current:  {cur['start']} .. {cur['end']} (outcomes={cur['total_outcomes']})",
        f"previous: {prev['start']} .. {prev['end']} (outcomes={prev['total_outcomes']})",
        "",
        "state                         current  previous",
    ]
    for state in _STATES:
        lines.append(f"{state:<29} {cur['counts'][state]:>7}  {prev['counts'][state]:>8}")
    lines.extend([
        "",
        f"instrumentation coverage: {_fmt(cur['instrumentation_coverage'])}",
        f"completion among instrumented: {_fmt(cur['completion_rate_among_instrumented'])}",
        f"productive among completed: {_fmt(cur['productive_rate_among_completed'])}",
        "average lessons/completed extraction: "
        f"{_fmt(cur['average_lessons_per_completed'], percent=False)}",
        f"tiered persistence: {_fmt(cur['tiered_persistence_rate'])}",
        "",
        "Unknown historical empty rows are not counted as zero-yield extractions.",
        "No desired funnel-rate target has been decreed.",
    ])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--outcomes", type=Path)
    parser.add_argument("--log", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--now", help="override current ISO timestamp")
    args = parser.parse_args(argv)
    if args.days <= 0:
        parser.error("--days must be positive")
    now = _utc(args.now) if args.now else datetime.now(timezone.utc)
    if now is None:
        parser.error("--now must be an ISO-8601 timestamp")
    if args.outcomes is None:
        from memory_ledger import _outcomes_path
        args.outcomes = _outcomes_path()
    if args.log is None:
        from captains_log import _log_path
        args.log = _log_path()
    if not args.outcomes.is_file():
        print(f"warning: no outcomes source found at {args.outcomes}", file=sys.stderr)
    earliest = now.astimezone(timezone.utc) - timedelta(days=2 * args.days)
    if not any(path.is_file() for path in _log_paths(args.log, earliest=earliest)):
        print(f"warning: no captain's-log source found at {args.log} or its archives", file=sys.stderr)
    report = calculate(
        load_outcomes(args.outcomes),
        load_extraction_events(args.log, earliest=earliest),
        days=args.days,
        now=now,
    )
    print(json.dumps(report, indent=2, sort_keys=True) if args.json else render_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
