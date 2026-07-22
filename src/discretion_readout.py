"""Discretion readout — a judgement report over existing instrumentation.

Chunk 7 of the swarm-review arc. Not a spend report: the headline is EFFORT
(calls, tokens, model mix) plus coordination-discretion signals — where the
system re-planned the same question, retried without new evidence, or
re-injected context — with dollars as one trailing column, per the
budget-posture decree (EFFORT language, not dollars).

Read-only: consumes captain's-log events (active + rotated archives, same
glob as navigator_shadow._load_navigator_events) and memory/step-costs.jsonl.
No LLM calls, no writes, no config flags — a CLI that spends nothing needs
no killswitch. (CLI arguments like --json/--log-dir are surfaces, not
config: nothing here changes runtime behavior.)

Honesty rule: every metric the plan names that CANNOT be computed from
current instrumentation is listed in the report's "Not computable today"
section instead of being silently dropped — silent truncation reads as
"covered everything".
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

# Event types this readout consumes. Kept as one tuple so the loader's
# substring prefilter and the section computers stay in sync.
CONSUMED_EVENT_TYPES = (
    "METACOGNITIVE_DECISION",
    "RECALL_PERFORMED",
    "QUALITY_GATE_SECOND_FAMILY",
    "QUALITY_GATE_COUNCIL",
    "QUALITY_GATE_CROSS_REF",
    "LESSON_RECORDED",
    "MEMORY_CONSOLIDATED",
    "PLAYBOOK_CURATED",
)


def load_events(base: Optional[Path] = None,
                coverage: Optional[Dict[str, int]] = None) -> List[Dict[str, Any]]:
    """Read consumed event rows from the workspace captain's log (active +
    rotated archives), chronological. Mirrors navigator_shadow's loader so
    both readouts see the same source of truth.

    ``coverage``, when given, is filled with input-honesty counters
    (files_read / files_failed / lines_skipped) — a judgement report must
    not let a corrupt archive silently shrink every denominator (review
    finding: the honesty rule applies to the inputs too)."""
    if base is None:
        try:
            from captains_log import _log_path
            base = _log_path().parent
        except Exception:
            base = Path.home() / ".maro" / "workspace" / "memory"
    cov = coverage if coverage is not None else {}
    cov.update({"files_read": 0, "files_failed": 0, "lines_skipped": 0})
    events: List[Dict[str, Any]] = []
    for p in sorted(base.glob("captains_log*.jsonl")):
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except Exception:
            cov["files_failed"] += 1
            continue
        cov["files_read"] += 1
        for line in lines:
            if not any(t in line for t in CONSUMED_EVENT_TYPES):
                continue
            try:
                e = json.loads(line)
            except Exception:
                cov["lines_skipped"] += 1
                continue
            if isinstance(e, dict) and e.get("event_type") in CONSUMED_EVENT_TYPES:
                events.append(e)
    return events


def _rows(events: List[Dict[str, Any]], event_type: str) -> List[Dict[str, Any]]:
    return [e for e in events if e.get("event_type") == event_type]


# ---------------------------------------------------------------------------
# Coordination-discretion metrics (plan chunk-7 item 1, computable subset)
# ---------------------------------------------------------------------------

def metacog_summary(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Re-planning and retry discretion from METACOGNITIVE_DECISION rows.

    evidence_free_retries: a retry whose newest failure fingerprint equals
    the previous one — the step was retried without producing new evidence
    (the fingerprints field carries the last 3, so this is computable
    per-row without cross-row joins)."""
    rows = _rows(events, "METACOGNITIVE_DECISION")
    by_action: Dict[str, int] = {}
    evidence_free = 0
    max_replan = 0
    for e in rows:
        c = e.get("context") or {}
        action = str(c.get("action") or "?")
        by_action[action] = by_action.get(action, 0) + 1
        fps = c.get("fingerprints") or []
        if action == "retry" and len(fps) >= 2 and fps[-1] == fps[-2]:
            evidence_free += 1
        try:
            max_replan = max(max_replan, int(c.get("replan_count") or 0))
        except Exception:
            pass
    return {
        "rows": len(rows),
        "by_action": by_action,
        "evidence_free_retries": evidence_free,
        "redecompose_rows": by_action.get("redecompose", 0),
        "max_replan_count": max_replan,
    }


def reinjection_summary(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Context-reinjection volume + exact-duplicate goal texts from
    RECALL_PERFORMED rows. Duplicate detection is exact-normalized-text only;
    semantic overlap is in the not-computable list."""
    rows = _rows(events, "RECALL_PERFORMED")
    blocks = 0
    lessons = 0
    elapsed = 0
    elapsed_n = 0
    by_slice: Dict[str, int] = {}
    goal_counts: Dict[str, int] = {}
    for e in rows:
        c = e.get("context") or {}
        blocks += int(c.get("knowledge_blocks") or 0)
        lessons += len(c.get("lessons_cited") or [])
        if c.get("elapsed_ms") is not None:
            try:
                elapsed += int(c.get("elapsed_ms"))
                elapsed_n += 1
            except Exception:
                pass
        # slice name rides in the summary ("recall slice=loop: ...")
        s = str(e.get("summary") or "")
        if "slice=" in s:
            sl = s.split("slice=", 1)[1].split(":", 1)[0].strip()
            by_slice[sl] = by_slice.get(sl, 0) + 1
        goal = " ".join(str(c.get("goal_preview") or "").lower().split())
        if goal:
            goal_counts[goal] = goal_counts.get(goal, 0) + 1
    dupes = {g: n for g, n in goal_counts.items() if n > 1}
    return {
        "rows": len(rows),
        "avg_knowledge_blocks": round(blocks / len(rows), 2) if rows else 0.0,
        "avg_lessons_cited": round(lessons / len(rows), 2) if rows else 0.0,
        "avg_elapsed_ms": int(elapsed / elapsed_n) if elapsed_n else 0,
        "by_slice": by_slice,
        "duplicate_goal_texts": len(dupes),
        "top_duplicates": sorted(
            ((n, g[:80]) for g, n in dupes.items()), reverse=True)[:5],
    }


# ---------------------------------------------------------------------------
# Gate-family agreement (chunks 5a/5b A/B evidence) + typed-code consumption
# ---------------------------------------------------------------------------

def gate_family_summary(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Tabulate the flag-only gate lanes: second-family agreement,
    council seat verdicts + FINDING[CODE] tallies, cross-ref lanes
    (zero-claim rows kept — they are denominator data)."""
    sf_rows = _rows(events, "QUALITY_GATE_SECOND_FAMILY")
    sf_by_decision: Dict[str, int] = {}
    for e in sf_rows:
        # live vocabulary is SECOND_FAMILY_AGREE/... — normalize the prefix
        # so the table reads AGREE/DISSENT/UNDECIDED/NO_VERDICT
        d = str((e.get("context") or {}).get("decision") or "?").replace(
            "SECOND_FAMILY_", "")
        sf_by_decision[d] = sf_by_decision.get(d, 0) + 1
    decided = sf_by_decision.get("AGREE", 0) + sf_by_decision.get("DISSENT", 0)
    sf_agreement = (sf_by_decision.get("AGREE", 0) / decided) if decided else None

    council_rows = _rows(events, "QUALITY_GATE_COUNCIL")
    weak_seats = 0
    seats_total = 0
    flag_unconfirmed = 0
    finding_codes: Dict[str, int] = {}
    for e in council_rows:
        c = e.get("context") or {}
        if "free_flag_unconfirmed" in str(e.get("summary") or "") or c.get(
                "free_flag_unconfirmed"):
            flag_unconfirmed += 1
        for seat in list(c.get("seats") or []) + list(c.get("free_seats") or []):
            if not isinstance(seat, dict):
                continue
            seats_total += 1
            if seat.get("verdict") == "WEAK":
                weak_seats += 1
            for code in seat.get("finding_codes") or []:
                code = str(code)
                finding_codes[code] = finding_codes.get(code, 0) + 1

    cr_rows = _rows(events, "QUALITY_GATE_CROSS_REF")
    cr_by_lane: Dict[str, int] = {}
    cr_claims = 0
    cr_disputes = 0
    cr_zero_claim = 0
    for e in cr_rows:
        c = e.get("context") or {}
        lane = str(c.get("lane") or "?")
        cr_by_lane[lane] = cr_by_lane.get(lane, 0) + 1
        cr_claims += int(c.get("claims_checked") or 0)
        cr_disputes += int(c.get("disputes") or 0)
        if not int(c.get("claims_extracted") or 0):
            cr_zero_claim += 1

    return {
        "second_family": {
            "rows": len(sf_rows),
            "by_decision": sf_by_decision,
            "agreement_rate": (round(sf_agreement, 3)
                               if sf_agreement is not None else None),
        },
        "council": {
            "rows": len(council_rows),
            "seats": seats_total,
            "weak_seats": weak_seats,
            "free_flag_unconfirmed": flag_unconfirmed,
            "finding_codes": finding_codes,
        },
        "cross_ref": {
            "rows": len(cr_rows),
            "by_lane": cr_by_lane,
            "claims_checked": cr_claims,
            "disputes": cr_disputes,
            "zero_claim_rows": cr_zero_claim,
        },
    }


# ---------------------------------------------------------------------------
# Novelty tabulation (chunk 6's deferred readout)
# ---------------------------------------------------------------------------

def novelty_summary(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """LESSON_RECORDED novelty distribution. Rows without the field predate
    chunk 6 and are counted separately — they are denominator honesty, not
    zero-novelty lessons."""
    rows = _rows(events, "LESSON_RECORDED")
    vals: List[float] = []
    boosted = 0
    missing = 0
    for e in rows:
        c = e.get("context") or {}
        if "novelty" not in c:
            missing += 1
            continue
        try:
            v = float(c.get("novelty"))
        except Exception:
            missing += 1
            continue
        vals.append(v)
        try:
            if float(c.get("score") or 0.0) > 1.0:
                boosted += 1
        except Exception:
            pass
    buckets = {"0.00-0.25": 0, "0.25-0.50": 0, "0.50-0.75": 0, "0.75-1.00": 0}
    for v in vals:
        if v < 0.25:
            buckets["0.00-0.25"] += 1
        elif v < 0.5:
            buckets["0.25-0.50"] += 1
        elif v < 0.75:
            buckets["0.50-0.75"] += 1
        else:
            buckets["0.75-1.00"] += 1
    return {
        "rows": len(rows),
        "with_novelty": len(vals),
        "pre_chunk6_rows": missing,
        "mean_novelty": round(sum(vals) / len(vals), 3) if vals else None,
        "boosted": boosted,
        "buckets": buckets,
    }


# ---------------------------------------------------------------------------
# Duty cycle — surviving background lanes only (consolidation/dream-cycle)
# ---------------------------------------------------------------------------

def duty_cycle_summary(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Per-lane activity for the background lanes that still exist. The NOW
    triage lane emits no captain's-log event — it is in the not-computable
    list, not silently absent."""
    out: Dict[str, Any] = {}
    for lane, etype in (("consolidation", "MEMORY_CONSOLIDATED"),
                        ("playbook_curation", "PLAYBOOK_CURATED")):
        rows = _rows(events, etype)
        days = sorted({str(e.get("timestamp") or "")[:10] for e in rows if e.get("timestamp")})
        out[lane] = {
            "rows": len(rows),
            "days_active": len(days),
            "first": days[0] if days else None,
            "last": days[-1] if days else None,
        }
    return out


# ---------------------------------------------------------------------------
# EFFORT summary (spend is one column, not the headline)
# ---------------------------------------------------------------------------

def effort_summary(entries: Optional[List[dict]] = None,
                   days: int = 7,
                   path: Optional[Path] = None) -> Dict[str, Any]:
    """Per-day EFFORT from step-costs.jsonl: calls, tokens, model mix —
    cost_usd rides as the trailing column.

    Reads the WHOLE file (limit=None) — this is an offline CLI, and a tail
    cap here would silently understate the headline section (unanimous
    review finding: the newest-5000 sample truncated older days with no
    caveat). ``path`` keeps the corpus coherent under --log-dir: cost
    telemetry comes from the same memory dir as the events."""
    if entries is None:
        from jsonl_utils import read_jsonl_tail
        if path is None:
            import metrics
            path = metrics._step_costs_path()
        entries = read_jsonl_tail(path, limit=None)
    by_day: Dict[str, Dict[str, Any]] = {}
    for e in entries:
        day = str(e.get("recorded_at") or "")[:10]
        if not day:
            continue
        slot = by_day.setdefault(day, {"calls": 0, "tokens": 0,
                                       "cost_usd": 0.0, "models": {}})
        slot["calls"] += 1
        slot["tokens"] += int(e.get("total_tokens") or 0)
        try:
            slot["cost_usd"] += float(e.get("cost_usd") or 0.0)
        except Exception:
            pass
        m = str(e.get("model") or "?")
        slot["models"][m] = slot["models"].get(m, 0) + 1
    recent = sorted(by_day)[-days:]
    return {
        "days": {d: by_day[d] for d in recent},
        "total_calls": sum(by_day[d]["calls"] for d in recent),
        "total_tokens": sum(by_day[d]["tokens"] for d in recent),
        "total_cost_usd": round(sum(by_day[d]["cost_usd"] for d in recent), 4),
    }


# ---------------------------------------------------------------------------
# Not computable today — the plan's log() requirement
# ---------------------------------------------------------------------------

def not_computable() -> List[str]:
    return [
        "fan-out justification (did parallel work produce integrated "
        "results): no event links a parallel batch to its integration "
        "outcome — needs an emitter before a readout can exist",
        "semantic duplicate/overlapping goals: only exact-normalized "
        "goal-text duplicates are counted; near-duplicate goals have no "
        "instrument",
        "NOW-triage duty cycle: the NOW lane emits no captain's-log event — "
        "scoped out until an emitter exists",
        "playbook-curation duty cycle counts changed-file runs only: "
        "curate_playbook emits PLAYBOOK_CURATED only when the file changed, "
        "so quiet (no-change) passes are invisible — rows=0 does not mean "
        "the lane never ran",
        "novelty survival (do boosted lessons outlive decay): needs store "
        "age across decay cycles; revisit once the novelty field has "
        "accumulated history",
    ]


# ---------------------------------------------------------------------------
# Report assembly + CLI
# ---------------------------------------------------------------------------

def build_payload(events: Optional[List[Dict[str, Any]]] = None,
                  step_entries: Optional[List[dict]] = None,
                  base: Optional[Path] = None,
                  input_coverage: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
    """``base`` sources BOTH inputs (events glob and step-costs.jsonl) from
    one directory — an alternate --log-dir must never mix archive events
    with live-workspace cost telemetry (review finding: mixed corpus)."""
    coverage: Dict[str, int] = dict(input_coverage or {})
    if events is None:
        events = load_events(base, coverage=coverage)
    step_path = (base / "step-costs.jsonl") if base is not None else None
    return {
        "metacognition": metacog_summary(events),
        "reinjection": reinjection_summary(events),
        "gate_families": gate_family_summary(events),
        "novelty": novelty_summary(events),
        "duty_cycle": duty_cycle_summary(events),
        "effort": effort_summary(step_entries, path=step_path),
        "input_coverage": coverage,
        "not_computable": not_computable(),
    }


def _fmt_counts(d: Dict[str, int]) -> str:
    return ", ".join(f"{k}={v}" for k, v in sorted(d.items())) or "(none)"


def build_report(payload: Optional[Dict[str, Any]] = None) -> str:
    p = payload or build_payload()
    lines: List[str] = ["# Discretion readout (judgement report, not a bill)"]

    eff = p["effort"]
    lines.append("")
    lines.append(f"## EFFORT — last {len(eff['days'])} active day(s)")
    lines.append(f"total: {eff['total_calls']} calls, "
                 f"{eff['total_tokens']:,} tokens "
                 f"(cost column: ${eff['total_cost_usd']:.2f})")
    for day, s in eff["days"].items():
        mix = _fmt_counts(s["models"])
        lines.append(f"  {day}  calls={s['calls']:4d} tokens={s['tokens']:>9,} "
                     f"models[{mix}] ${s['cost_usd']:.2f}")

    m = p["metacognition"]
    lines.append("")
    lines.append("## Re-planning and retry discretion (METACOGNITIVE_DECISION)")
    lines.append(f"rows: {m['rows']}  actions: {_fmt_counts(m['by_action'])}")
    lines.append(f"evidence-free retries (same fingerprint retried): "
                 f"{m['evidence_free_retries']}")
    lines.append(f"redecompose rows: {m['redecompose_rows']}  "
                 f"max replan_count seen: {m['max_replan_count']}")

    r = p["reinjection"]
    lines.append("")
    lines.append("## Context reinjection (RECALL_PERFORMED)")
    lines.append(f"rows: {r['rows']}  by slice: {_fmt_counts(r['by_slice'])}")
    lines.append(f"avg knowledge blocks: {r['avg_knowledge_blocks']}  "
                 f"avg lessons cited: {r['avg_lessons_cited']}  "
                 f"avg elapsed: {r['avg_elapsed_ms']}ms")
    lines.append(f"exact-duplicate goal texts: {r['duplicate_goal_texts']}")
    for n, g in r["top_duplicates"]:
        lines.append(f"  x{n}  {g}")

    g = p["gate_families"]
    sf = g["second_family"]
    lines.append("")
    lines.append("## Gate families (flag-only A/B lanes)")
    rate = (f"{sf['agreement_rate']:.0%}" if sf["agreement_rate"] is not None
            else "n/a")
    lines.append(f"second-family: {sf['rows']} rows  "
                 f"{_fmt_counts(sf['by_decision'])}  agreement={rate}")
    co = g["council"]
    lines.append(f"council: {co['rows']} rows  seats={co['seats']} "
                 f"weak={co['weak_seats']} "
                 f"free_flag_unconfirmed={co['free_flag_unconfirmed']}")
    if co["finding_codes"]:
        lines.append(f"  finding codes: {_fmt_counts(co['finding_codes'])}")
    cr = g["cross_ref"]
    lines.append(f"cross-ref: {cr['rows']} rows  lanes: "
                 f"{_fmt_counts(cr['by_lane'])}  "
                 f"claims_checked={cr['claims_checked']} "
                 f"disputes={cr['disputes']} "
                 f"zero_claim_rows={cr['zero_claim_rows']}")

    n = p["novelty"]
    lines.append("")
    lines.append("## Lesson novelty (LESSON_RECORDED, chunk-6 term)")
    mean = f"{n['mean_novelty']}" if n["mean_novelty"] is not None else "n/a"
    lines.append(f"rows: {n['rows']}  with novelty: {n['with_novelty']}  "
                 f"pre-chunk-6: {n['pre_chunk6_rows']}")
    lines.append(f"mean novelty: {mean}  boosted (score>1.0): {n['boosted']}")
    lines.append(f"  buckets: {_fmt_counts(n['buckets'])}")

    d = p["duty_cycle"]
    lines.append("")
    lines.append("## Background-lane duty cycle")
    for lane, s in d.items():
        span = (f"{s['first']} → {s['last']}" if s["first"] else "never ran")
        lines.append(f"  {lane:18s} rows={s['rows']:4d} "
                     f"days_active={s['days_active']:3d}  {span}")

    cov = p.get("input_coverage") or {}
    if cov:
        lines.append("")
        note = (f"input: {cov.get('files_read', 0)} log file(s) read, "
                f"{cov.get('lines_skipped', 0)} malformed line(s) skipped")
        if cov.get("files_failed"):
            note += (f", {cov['files_failed']} file(s) UNREADABLE — "
                     "denominators above are incomplete")
        lines.append(note)

    lines.append("")
    lines.append("## Not computable today")
    for item in p["not_computable"]:
        lines.append(f"- {item}")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Judgement report over existing instrumentation "
                    "(EFFORT language; read-only).")
    parser.add_argument("--log-dir", default=None,
                        help="memory directory sourcing BOTH inputs — "
                             "captains_log*.jsonl and step-costs.jsonl "
                             "(default: the workspace memory dir)")
    parser.add_argument("--json", action="store_true",
                        help="emit the raw payload as JSON")
    args = parser.parse_args(argv)
    base = Path(args.log_dir) if args.log_dir else None
    payload = build_payload(base=base)
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(build_report(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
