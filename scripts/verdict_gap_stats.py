#!/usr/bin/env python3
"""Report the prospective organic done-vs-achieved re-audit gate honestly.

Only rows carrying an explicit ``measurement_class`` participate in a named
cohort.  Legacy rows remain ``unknown``: goal wording is not durable evidence
that a run was organic, a smoke canary, or a deliberate control.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ancestry import MEASUREMENT_CLASSES

CLASSES = (*MEASUREMENT_CLASSES, "unknown")


def _utc(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError, AttributeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def load_outcomes(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.is_file():
        return rows
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return rows
    for line in lines:
        try:
            row = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _dedupe_runs(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Newest outcome per run wins; pre-instrumentation rows stay individual."""
    latest: dict[str, tuple[datetime, int, dict[str, Any]]] = {}
    for index, row in enumerate(rows):
        handle_id = str(row.get("handle_id") or "").strip()
        outcome_id = str(row.get("outcome_id") or "").strip()
        key = f"handle:{handle_id}" if handle_id else f"outcome:{outcome_id or index}"
        timestamp = _utc(row.get("recorded_at")) or datetime.min.replace(
            tzinfo=timezone.utc
        )
        prior = latest.get(key)
        if prior is None or (timestamp, index) >= (prior[0], prior[1]):
            latest[key] = (timestamp, index, row)
    return [entry[2] for entry in latest.values()]


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def calculate(
    rows: Iterable[dict[str, Any]],
    *,
    target: int = 30,
    since: datetime | None = None,
) -> dict[str, Any]:
    if target <= 0:
        raise ValueError("target must be positive")

    deduped = _dedupe_runs(rows)
    class_counts = {name: 0 for name in CLASSES}
    dry_run_excluded = 0
    eligible: list[dict[str, Any]] = []
    for row in deduped:
        timestamp = _utc(row.get("recorded_at"))
        if since is not None and (timestamp is None or timestamp < since):
            continue
        if row.get("dry_run") is True:
            dry_run_excluded += 1
            continue
        raw_class = str(row.get("measurement_class") or "").strip().lower()
        measurement_class = raw_class if raw_class in CLASSES[:-1] else "unknown"
        class_counts[measurement_class] += 1
        if measurement_class == "organic":
            eligible.append(row)

    judged = [row for row in eligible if isinstance(row.get("goal_achieved"), bool)]
    achieved = [row for row in judged if row["goal_achieved"] is True]
    not_achieved = [row for row in judged if row["goal_achieved"] is False]
    done = [row for row in judged if row.get("status") == "done"]
    done_not_achieved = [
        row for row in judged
        if row.get("status") == "done" and row["goal_achieved"] is False
    ]
    not_done_achieved = [
        row for row in judged
        if row.get("status") != "done" and row["goal_achieved"] is True
    ]
    remaining = max(0, target - len(judged))

    return {
        "target_judged_organic_runs": target,
        "re_audit_due": len(judged) >= target,
        "judged_organic_runs_remaining": remaining,
        "since": since.isoformat() if since is not None else None,
        "source_rows_after_run_dedup": len(deduped),
        "source_rows_after_filters": sum(class_counts.values()),
        "dry_run_excluded": dry_run_excluded,
        "class_counts": class_counts,
        "organic": {
            "runs": len(eligible),
            "judged": len(judged),
            "unjudged": len(eligible) - len(judged),
            "done": len(done),
            "achieved": len(achieved),
            "not_achieved": len(not_achieved),
            "done_not_achieved": len(done_not_achieved),
            "not_done_achieved": len(not_done_achieved),
            "raw_achieved_rate_among_judged": _rate(len(achieved), len(judged)),
            "done_rate_among_judged": _rate(len(done), len(judged)),
            "done_not_achieved_rate_among_done": _rate(
                len(done_not_achieved), len(done)
            ),
        },
        "semantics": {
            "organic": (
                "normal production work explicitly stamped measurement_class=organic"
            ),
            "unknown": (
                "legacy, missing, or invalid measurement_class; excluded from the organic gate"
            ),
            "raw_rate": (
                "recorded goal_achieved verdicts only; not the manually spot-audited corrected success rate"
            ),
            "gate": (
                "when due, rerun the artifact spot-audit before changing thresholds or quoting success"
            ),
            "dedup": (
                "newest outcome per handle_id defines current request state; a newer unjudged "
                "continuation keeps that request out of the judged gate; rows without handle_id "
                "remain distinct legacy outcomes"
            ),
        },
    }


def _fmt_rate(value: float | None) -> str:
    return "n/a" if value is None else f"{100 * value:.1f}%"


def render_text(report: dict[str, Any]) -> str:
    org = report["organic"]
    gate = "DUE — perform manual artifact spot-audit" if report["re_audit_due"] else (
        f"not due ({report['judged_organic_runs_remaining']} judged organic runs remaining)"
    )
    lines = [
        "Done vs achieved — prospective organic re-audit gate",
        f"since: {report['since'] or '(all explicitly instrumented history)'}",
        f"gate: {gate}",
        "",
        "measurement class  runs",
    ]
    for name in CLASSES:
        lines.append(f"{name:<18} {report['class_counts'][name]:>4}")
    lines.extend([
        f"{'dry-run excluded':<18} {report['dry_run_excluded']:>4}",
        "",
        f"organic judged/unjudged: {org['judged']}/{org['unjudged']}",
        f"organic done: {org['done']}",
        f"organic achieved/not-achieved: {org['achieved']}/{org['not_achieved']}",
        f"done-but-not-achieved: {org['done_not_achieved']}",
        f"not-done-but-achieved: {org['not_done_achieved']}",
        f"raw achieved rate among judged: {_fmt_rate(org['raw_achieved_rate_among_judged'])}",
        f"done rate among judged: {_fmt_rate(org['done_rate_among_judged'])}",
        f"done-not-achieved among done: {_fmt_rate(org['done_not_achieved_rate_among_done'])}",
        "",
        "Unknown legacy rows do not count as organic. Raw verdict rate is not a corrected success rate.",
    ])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outcomes", type=Path)
    parser.add_argument("--target", type=int, default=30)
    parser.add_argument("--since", help="include rows recorded on/after this ISO timestamp")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.target <= 0:
        parser.error("--target must be positive")
    since = _utc(args.since) if args.since else None
    if args.since and since is None:
        parser.error("--since must be an ISO-8601 timestamp")
    if args.outcomes is None:
        from memory_ledger import _outcomes_path
        args.outcomes = _outcomes_path()
    if not args.outcomes.is_file():
        print(f"warning: no outcomes source found at {args.outcomes}", file=sys.stderr)
    report = calculate(load_outcomes(args.outcomes), target=args.target, since=since)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
