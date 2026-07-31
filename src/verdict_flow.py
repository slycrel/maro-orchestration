"""Verdict-flow readout — verdicts/week by lane + framing→verdict delay.

Chunk B of the camera/verdict arc (2026-07-31), built to the taste-lens
panel's round-3 prescription: "Instrument the flow before the stock.
Verdicts/week by lane, plus the framing→verdict delay distribution
(you've capped cadence against a delay you've never measured). Then close
the two verdict-blind lanes."

Stock and flow are reported SEPARATELY: the all-time judged fraction is
dominated by pre-verdict-era rows that will never be judged (no backfill,
by decree), so blending them into one number understates a healthy live
pipeline. Stock answers "what does the ledger hold"; flow answers "is the
verdict pipe moving this week".

Read-only: consumes memory/outcomes.jsonl only. No LLM calls, no writes,
no config flags — a CLI that spends nothing needs no killswitch.

Honesty rules (same as discretion_readout): torn lines, unparsable
timestamps, and dry-run exclusions are counted and printed, never
silently dropped; delays that CANNOT be computed (historical closure
stamps that predate goal_verdict_at) are reported as a not-computable
count, not omitted.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Sources whose verdict exists AT record time: the row's own elapsed_ms IS
# the framing→verdict delay (verdict landed when the run finished), and
# record→verdict is ~0. Everything else (closure family) lands later via
# stamp_outcome_verdict and needs goal_verdict_at to be measurable.
VERDICT_AT_RECORD_SOURCES = (
    "provenance",
    "now_self_verdict",
    "now_self_verdict_free",
    "deterministic_tests",
)

# Delay buckets (upper bound in seconds, label). Order matters.
DELAY_BUCKETS: Tuple[Tuple[float, str], ...] = (
    (60.0, "<1m"),
    (600.0, "1-10m"),
    (3600.0, "10-60m"),
    (6 * 3600.0, "1-6h"),
    (24 * 3600.0, "6-24h"),
    (float("inf"), ">24h"),
)


def _parse_ts(raw: Any) -> Optional[datetime]:
    """ISO timestamp → aware UTC datetime, or None. Naive stamps are
    assumed UTC (the writer uses datetime.now(timezone.utc)); 'Z' suffix
    tolerated for older/foreign rows."""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _week_key(dt: datetime) -> str:
    iso = dt.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def load_rows(ledger: Path,
              coverage: Optional[Dict[str, int]] = None) -> List[Dict[str, Any]]:
    """Read ledger rows; torn lines / non-dict rows are counted, not raised."""
    cov = coverage if coverage is not None else {}
    cov.update({"lines_read": 0, "lines_torn": 0})
    rows: List[Dict[str, Any]] = []
    if not ledger.is_file():
        return rows
    with open(ledger, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            cov["lines_read"] += 1
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                cov["lines_torn"] += 1
                continue
            if not isinstance(row, dict):
                cov["lines_torn"] += 1
                continue
            rows.append(row)
    return rows


def _bucket_label(seconds: float) -> str:
    for bound, label in DELAY_BUCKETS:
        if seconds < bound:
            return label
    return DELAY_BUCKETS[-1][1]


def compute_report(rows: List[Dict[str, Any]], weeks: int,
                   now: Optional[datetime] = None) -> Dict[str, Any]:
    """Pure computation over parsed rows → report dict (render-agnostic).

    ``now`` exists for tests; production passes None and uses wall clock
    (this is a readout, not runtime — determinism-for-resume rules don't
    apply here).
    """
    now = now or datetime.now(timezone.utc)
    cov = Counter()

    # Per-lane stock; per-week/lane flow; source tabulation; delays.
    stock: Dict[str, Counter] = defaultdict(Counter)
    flow: Dict[str, Dict[str, Counter]] = defaultdict(lambda: defaultdict(Counter))
    sources_judged = Counter()
    sources_unverifiable = Counter()
    delay_buckets = Counter()
    delay_kind = Counter()
    delays_stamped_later: List[float] = []

    for row in rows:
        if row.get("dry_run"):
            cov["dry_run_excluded"] += 1
            continue
        ts = _parse_ts(row.get("recorded_at"))
        if ts is None:
            cov["ts_unparsable"] += 1
            continue
        lane = str(row.get("task_type") or "?")
        judged = "goal_achieved" in row
        source = str(row.get("goal_verdict_source") or "")
        stamped = bool(source)

        st = stock[lane]
        st["rows"] += 1
        if judged:
            st["judged"] += 1
            if row.get("goal_achieved"):
                st["achieved"] += 1
            else:
                st["not_achieved"] += 1
            sources_judged[source or "(judged, no source)"] += 1
        elif stamped:
            # "judged, could not verify" — a verdict event without a
            # goal_achieved value (closure_unverifiable family).
            st["stamped_unverifiable"] += 1
            sources_unverifiable[source] += 1
        else:
            st["never_stamped"] += 1

        age_days = (now - ts).total_seconds() / 86400.0
        if age_days <= weeks * 7:
            wk = flow[_week_key(ts)][lane]
            wk["rows"] += 1
            if judged:
                wk["judged"] += 1

        # ---- framing→verdict delay ----
        if not (judged or stamped):
            continue
        elapsed_s = 0.0
        try:
            elapsed_s = max(0.0, float(row.get("elapsed_ms") or 0) / 1000.0)
        except (TypeError, ValueError):
            pass
        verdict_at = _parse_ts(row.get("goal_verdict_at"))
        if source in VERDICT_AT_RECORD_SOURCES:
            delay_kind["at_record"] += 1
            delay_buckets[_bucket_label(elapsed_s)] += 1
        elif verdict_at is not None:
            record_to_verdict = (verdict_at - ts).total_seconds()
            if record_to_verdict < 0:
                cov["delay_negative"] += 1
                continue
            delay_kind["stamped_later"] += 1
            delays_stamped_later.append(record_to_verdict)
            delay_buckets[_bucket_label(record_to_verdict + elapsed_s)] += 1
        else:
            # Historical closure stamps predate the goal_verdict_at field
            # (added 2026-07-31): the delay genuinely was not recorded.
            cov["delay_not_computable"] += 1

    return {
        "coverage": dict(cov),
        "stock": {k: dict(v) for k, v in stock.items()},
        "flow": {w: {k: dict(v) for k, v in lanes.items()}
                 for w, lanes in flow.items()},
        "sources_judged": dict(sources_judged),
        "sources_unverifiable": dict(sources_unverifiable),
        "delay_buckets": dict(delay_buckets),
        "delay_kind": dict(delay_kind),
        "delays_stamped_later_s": sorted(delays_stamped_later),
    }


def render(report: Dict[str, Any], weeks: int, coverage: Dict[str, int],
           ledger: Path) -> str:
    out: List[str] = []
    cov = dict(coverage)
    cov.update(report["coverage"])
    out.append("VERDICT FLOW — verdict pipe readout")
    out.append(f"  ledger: {ledger}")
    out.append(f"  rows {cov.get('lines_read', 0)}  torn {cov.get('lines_torn', 0)}"
               f"  dry-run excluded {cov.get('dry_run_excluded', 0)}"
               f"  ts-unparsable {cov.get('ts_unparsable', 0)}")
    if cov.get("lines_torn"):
        out.append("  WARNING: torn lines skipped — counts below undercount"
                   " actual activity")

    out.append("")
    out.append("STOCK — all-time by lane (judged | unverifiable-stamped |"
               " never-stamped)")
    for lane in sorted(report["stock"]):
        st = report["stock"][lane]
        out.append(
            f"  {lane:16s} rows {st.get('rows', 0):5d}"
            f"   judged {st.get('judged', 0):4d}"
            f" (T {st.get('achieved', 0)} / F {st.get('not_achieved', 0)})"
            f"   unverifiable {st.get('stamped_unverifiable', 0):3d}"
            f"   never-stamped {st.get('never_stamped', 0):4d}")

    out.append("")
    out.append(f"FLOW — verdicts/week by lane (last {weeks} weeks)")
    if not report["flow"]:
        out.append("  (no rows in window)")
    for week in sorted(report["flow"]):
        lanes = report["flow"][week]
        parts = [f"{lane} {c.get('judged', 0)}/{c.get('rows', 0)}"
                 for lane, c in sorted(lanes.items())]
        out.append(f"  {week}  " + "   ".join(parts))
    out.append("  (judged/recorded, bucketed by record week — a verdict"
               " stamped later still counts in its row's record week)")

    out.append("")
    out.append("VERDICT SOURCES (judged rows)")
    for src, n in sorted(report["sources_judged"].items(),
                         key=lambda kv: -kv[1]):
        out.append(f"  {src or '(empty)':26s} {n:5d}")
    if report["sources_unverifiable"]:
        out.append("  stamped-but-unverifiable:")
        for src, n in sorted(report["sources_unverifiable"].items(),
                             key=lambda kv: -kv[1]):
            out.append(f"    {src:24s} {n:5d}")

    out.append("")
    out.append("FRAMING→VERDICT DELAY (verdict-bearing rows)")
    kinds = report["delay_kind"]
    out.append(f"  at-record verdicts {kinds.get('at_record', 0)}"
               " (delay = run duration; provenance/now/deterministic)")
    out.append(f"  stamped-later verdicts {kinds.get('stamped_later', 0)}"
               " (delay = record→stamp + run duration; closure family)")
    for _, label in DELAY_BUCKETS:
        n = report["delay_buckets"].get(label, 0)
        bar = "#" * min(40, n)
        out.append(f"    {label:7s} {n:5d}  {bar}")
    later = report["delays_stamped_later_s"]
    if later:
        med = later[len(later) // 2]
        out.append(f"  stamped-later record→verdict median: {med / 60.0:.1f}m"
                   f" (n={len(later)})")
    nc = report["coverage"].get("delay_not_computable", 0)
    neg = report["coverage"].get("delay_negative", 0)
    out.append("")
    out.append("NOT COMPUTABLE")
    out.append(f"  {nc} verdict-bearing rows predate the goal_verdict_at"
               " stamp (added 2026-07-31) — their delay was never recorded;"
               " no backfill by decree")
    if neg:
        out.append(f"  {neg} rows with verdict stamped before record time"
                   " (clock skew) — excluded from buckets")
    return "\n".join(out)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="verdict_flow",
        description="Verdicts/week by lane + framing->verdict delay"
                    " (read-only; run with PYTHONPATH=src)")
    parser.add_argument("--weeks", type=int, default=6,
                        help="flow window in weeks (default 6)")
    parser.add_argument("--ledger", default=None,
                        help="path to outcomes.jsonl (default: live workspace)")
    args = parser.parse_args(argv)

    if args.ledger:
        ledger = Path(args.ledger)
    else:
        # Private import on purpose: the ledger path policy (workspace
        # resolution) has exactly one owner and this readout must read
        # the same file the writers write.
        from memory_ledger import _outcomes_path
        ledger = _outcomes_path()

    coverage: Dict[str, int] = {}
    rows = load_rows(ledger, coverage)
    report = compute_report(rows, weeks=max(1, args.weeks))
    print(render(report, weeks=max(1, args.weeks), coverage=coverage,
                 ledger=ledger))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
