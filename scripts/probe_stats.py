#!/usr/bin/env python3
"""Rolling calibration metrics for adversarial-review claim probes.

``validated`` means the probe returned nonzero and the producer retained the
reviewer's verdict; it does *not* prove the reviewer was correct because a weak
or broken probe can produce the same status. ``dismissed`` means a zero exit
caused the producer to dismiss the reviewer objection. The summary ratio is
therefore named reviewer verdict retention rate, not accuracy or agreement.
Unprobed and unrunnable claims are reported separately.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

STATUSES = ("validated", "dismissed", "unprobed", "unrunnable", "unknown")


def _utc(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError, AttributeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


_ARCHIVE_STAMP_RE = re.compile(r"^captains_log\.(\d{8}-\d{6})(?:-\d+)?\.jsonl$")


def _log_paths(active: Path, *, earliest: datetime | None = None) -> list[Path]:
    """Return relevant rotated archives then active log.

    Rotation names carry the UTC rotation time. An archive rotated before the
    earliest requested event cannot contain a later event, so rolling reports
    need not re-read the system's entire append-only history. Unknown archive
    names remain included conservatively.
    """
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


def load_claim_probes(
    active: Path, *, earliest: datetime | None = None
) -> list[dict[str, Any]]:
    """Read CLAIM_PROBED objects across archives; malformed rows are ignored."""
    events: list[dict[str, Any]] = []
    for path in _log_paths(active, earliest=earliest):
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
            if isinstance(row, dict) and row.get("event_type") == "CLAIM_PROBED":
                events.append(row)
    return events


def _window(
    events: Iterable[dict[str, Any]], start: datetime, end: datetime
) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    for event in events:
        timestamp = _utc(str(event.get("timestamp", "")))
        if timestamp is None or not (start <= timestamp < end):
            continue
        context = event.get("context")
        raw_status = context.get("probe_status") if isinstance(context, dict) else None
        status = str(raw_status or "").strip().lower()
        counts[status if status in STATUSES[:-1] else "unknown"] += 1

    total = sum(counts.values())
    decisive = counts["validated"] + counts["dismissed"]

    def rate(value: int, denominator: int = total) -> float | None:
        return round(value / denominator, 6) if denominator else None

    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "total": total,
        "counts": {status: counts[status] for status in STATUSES},
        "rates": {status: rate(counts[status]) for status in STATUSES},
        "probe_coverage": rate(decisive),
        "reviewer_verdict_retention_rate": rate(counts["validated"], decisive),
    }


def calculate(
    events: Iterable[dict[str, Any]], *, days: int, now: datetime
) -> dict[str, Any]:
    """Compare the current rolling window with the immediately prior one."""
    if days <= 0:
        raise ValueError("days must be positive")
    end = now.astimezone(timezone.utc)
    current_start = end - timedelta(days=days)
    previous_start = current_start - timedelta(days=days)
    materialized = list(events)
    current = _window(materialized, current_start, end)
    previous = _window(materialized, previous_start, current_start)

    deltas: dict[str, float | None] = {}
    for key in ("probe_coverage", "reviewer_verdict_retention_rate"):
        cur, prev = current[key], previous[key]
        deltas[key] = round(cur - prev, 6) if cur is not None and prev is not None else None
    for status in STATUSES:
        cur, prev = current["rates"][status], previous["rates"][status]
        deltas[f"{status}_rate"] = (
            round(cur - prev, 6) if cur is not None and prev is not None else None
        )

    return {
        "days": days,
        "semantics": {
            "validated": (
                "probe returned nonzero and reviewer verdict was retained; "
                "reviewer may be right or probe may be weak/wrong"
            ),
            "dismissed": (
                "probe returned zero and producer dismissed reviewer objection"
            ),
            "reviewer_verdict_retention_rate": (
                "validated / (validated + dismissed); not an accuracy score"
            ),
            "probe_coverage": "(validated + dismissed) / all CLAIM_PROBED events",
        },
        "current": current,
        "previous": previous,
        "delta_current_minus_previous": deltas,
    }


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{100 * value:.1f}%"


def render_text(report: dict[str, Any]) -> str:
    current = report["current"]
    previous = report["previous"]
    delta = report["delta_current_minus_previous"]
    lines = [
        f"CLAIM_PROBED reviewer calibration — {report['days']}-day rolling window",
        f"current:  {current['start']} .. {current['end']} (N={current['total']})",
        f"previous: {previous['start']} .. {previous['end']} (N={previous['total']})",
        "",
        "status       current       previous      delta",
    ]
    for status in STATUSES:
        lines.append(
            f"{status:<12} "
            f"{current['counts'][status]:>5} {_pct(current['rates'][status]):>8}  "
            f"{previous['counts'][status]:>5} {_pct(previous['rates'][status]):>8}  "
            f"{_pct(delta[f'{status}_rate']):>8}"
        )
    lines.extend([
        "",
        "reviewer verdict retention rate (not accuracy): "
        f"{_pct(current['reviewer_verdict_retention_rate'])} "
        f"(previous {_pct(previous['reviewer_verdict_retention_rate'])}, "
        f"delta {_pct(delta['reviewer_verdict_retention_rate'])})",
        "probe coverage: "
        f"{_pct(current['probe_coverage'])} "
        f"(previous {_pct(previous['probe_coverage'])}, "
        f"delta {_pct(delta['probe_coverage'])})",
    ])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=30, help="rolling window length (default: 30)")
    parser.add_argument("--log", type=Path, help="active captains_log.jsonl path")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument("--now", help="override current ISO timestamp (for replay/tests)")
    args = parser.parse_args(argv)
    if args.days <= 0:
        parser.error("--days must be positive")
    now = _utc(args.now) if args.now else datetime.now(timezone.utc)
    if now is None:
        parser.error("--now must be an ISO-8601 timestamp")
    if args.log is None:
        from captains_log import _log_path
        args.log = _log_path()

    earliest = now.astimezone(timezone.utc) - timedelta(days=2 * args.days)
    source_paths = [
        path for path in _log_paths(args.log, earliest=earliest) if path.is_file()
    ]
    if not source_paths:
        print(
            f"warning: no captain's-log source found at {args.log} or its archives",
            file=sys.stderr,
        )
    report = calculate(
        load_claim_probes(args.log, earliest=earliest), days=args.days, now=now
    )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
