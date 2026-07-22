"""Free-validator ROI report — token/cost delta on the real corpus (BACKLOG #9).

The free validation rung exists to skip paid validation calls. Since
2026-07-21 that rung is hosted-free (`hosted_free.py` — gemini/groq free
tiers, tier="hosted-free-decisive"); before that it was a local model
(qwen via ollama, tier="local-decisive" — removed by decree, historical
rows still counted). This module answers "what is the free rung actually
buying us?" from the captain's log:

  * `VALIDATION_LADDER` rows (step-level `verify_step`, one per call): which
    tier decided, per-tier latency, payload size.
  * `QUALITY_GATE_VERDICT` rows (goal-level gate, one per tier): free-decisive
    verdicts vs paid verdicts, latency where recorded.

Report: paid calls skipped (the saving), escalation rate (free UNDECIDED →
paid anyway, where the free attempt is pure added latency), latency of free
vs paid tiers, and an estimated USD saving. Cost is an ESTIMATE — verdict
calls aren't individually metered, so we price the recorded payload
(`input_chars`/4 tokens in, 256 max out) through `metrics.estimate_cost`'s
default rate; rows without payload data use the average of rows that have it.
Safety (is the free judge right?) is `validation_shadow --agreement`'s job —
this report only cites its false_pass count.

Usage:
    PYTHONPATH=src python3 -m validator_roi           # human table
    PYTHONPATH=src python3 -m validator_roi --json    # machine-readable
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_EST_OUT_TOKENS = 256          # verdict calls are max_tokens<=256 by construction
_DEFAULT_IN_TOKENS = 2000      # fallback when no row carries input_chars


def _local_model_names() -> List[str]:
    # Local-model tier removed 2026-07-21 (decree); historical ledger rows
    # with local sources still classify via the ollama-tag heuristic below.
    return []


def _is_local_source(source: str, local_names: List[str]) -> bool:
    s = (source or "").strip()
    if not s:
        return False
    if s in local_names or s == "local":
        return True
    # hosted-free rung sources (hosted_free:<provider>:<model>, 2026-07-21+)
    if s.startswith("hosted_free:"):
        return True
    # ollama-style tags (name:size) that aren't a configured paid backend key
    return ":" in s and s.split(":")[0] not in ("anthropic", "openai", "openrouter")


def _read_events(kinds: List[str], base: Optional[Path] = None) -> List[Dict[str, Any]]:
    if base is None:
        try:
            from captains_log import _log_path  # type: ignore
            base = _log_path().parent
        except Exception:
            base = Path.home() / ".maro" / "workspace" / "memory"
    events: List[Dict[str, Any]] = []
    for p in sorted(base.glob("captains_log*.jsonl")):
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                if not any(k in line for k in kinds):
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if d.get("event_type") in kinds:
                    events.append(d)
        except Exception:
            continue
    return events


def _est_call_cost(input_chars: int, fallback_in_tokens: float) -> float:
    tokens_in = (input_chars / 4.0) if input_chars > 0 else fallback_in_tokens
    try:
        from metrics import estimate_cost
        return float(estimate_cost(int(tokens_in), _EST_OUT_TOKENS))
    except Exception:
        return 0.0


def _avg(vals: List[float]) -> float:
    return (sum(vals) / len(vals)) if vals else 0.0


def analyze_roi(ladder_events: List[Dict[str, Any]],
                gate_events: List[Dict[str, Any]],
                local_names: Optional[List[str]] = None) -> Dict[str, Any]:
    """Aggregate ladder + gate rows into the ROI summary. Pure function."""
    local_names = local_names if local_names is not None else _local_model_names()

    # --- step-level ladder (VALIDATION_LADDER) ---
    # Free-decisive tiers: hosted-free (current rung, 2026-07-21+) and
    # local (removed rung; historical rows). Both skipped a paid call.
    _free_decisive_tiers = ("local-decisive", "hosted-free-decisive")
    tiers = {"local-decisive": 0, "hosted-free-decisive": 0,
             "escalated": 0, "paid": 0}
    free_lat: List[float] = []        # decisive free-rung latency
    paid_lat: List[float] = []        # paid latency (paid + escalated rows)
    wasted_lat: List[float] = []      # free attempt latency on escalated rows
    skipped_costs: List[float] = []
    in_chars: List[float] = []
    for e in ladder_events:
        c = e.get("context") or {}
        tier = str(c.get("tier", ""))
        if tier not in tiers:
            continue
        tiers[tier] += 1
        ic = int(c.get("input_chars") or 0)
        if ic > 0:
            in_chars.append(ic)
        if tier in _free_decisive_tiers:
            free_lat.append(float(c.get("local_elapsed_ms") or 0.0))
        else:
            paid_lat.append(float(c.get("paid_elapsed_ms") or 0.0))
            if tier == "escalated":
                wasted_lat.append(float(c.get("local_elapsed_ms") or 0.0))
    fallback_in = _avg(in_chars) / 4.0 if in_chars else _DEFAULT_IN_TOKENS
    for e in ladder_events:
        c = e.get("context") or {}
        if str(c.get("tier", "")) in _free_decisive_tiers:
            skipped_costs.append(_est_call_cost(int(c.get("input_chars") or 0), fallback_in))

    ladder_total = sum(tiers.values())
    free_decisive = tiers["local-decisive"] + tiers["hosted-free-decisive"]
    free_attempts = free_decisive + tiers["escalated"]

    # --- goal-level gate (QUALITY_GATE_VERDICT) ---
    gate_local = 0
    gate_paid = 0
    gate_local_costs: List[float] = []
    gate_in_chars: List[float] = []
    for e in gate_events:
        c = e.get("context") or {}
        ic = int(c.get("input_chars") or 0)
        if ic > 0:
            gate_in_chars.append(ic)
    gate_fallback_in = _avg(gate_in_chars) / 4.0 if gate_in_chars else _DEFAULT_IN_TOKENS
    for e in gate_events:
        c = e.get("context") or {}
        if _is_local_source(str(c.get("source", "")), local_names):
            gate_local += 1
            gate_local_costs.append(_est_call_cost(int(c.get("input_chars") or 0), gate_fallback_in))
        else:
            gate_paid += 1

    return {
        "step_ladder": {
            "rows": ladder_total,
            "free_decisive": free_decisive,
            "local_decisive": tiers["local-decisive"],
            "hosted_free_decisive": tiers["hosted-free-decisive"],
            "escalated": tiers["escalated"],
            "paid_only": tiers["paid"],
            "decisive_rate": (free_decisive / free_attempts) if free_attempts else 0.0,
            "paid_calls_skipped": free_decisive,
            "est_saved_usd": round(sum(skipped_costs), 4),
            "avg_free_latency_ms": round(_avg(free_lat)),
            "avg_paid_latency_ms": round(_avg(paid_lat)),
            "avg_wasted_free_ms_on_escalation": round(_avg(wasted_lat)),
        },
        "quality_gate": {
            "rows": len(gate_events),
            "free_decisive": gate_local,
            "paid": gate_paid,
            "paid_calls_skipped": gate_local,
            "est_saved_usd": round(sum(gate_local_costs), 4),
        },
        "est_total_saved_usd": round(sum(skipped_costs) + sum(gate_local_costs), 4),
        "note": ("costs are estimates (payload-priced, verdict calls aren't "
                 "individually metered); agreement/safety is "
                 "`python3 -m validation_shadow --agreement`"),
    }


def _false_pass_count() -> Optional[int]:
    """Cite validation_shadow's dangerous-direction count (local PASS/paid FAIL)."""
    try:
        import validation_shadow as _vs
        summary = _vs.analyze_validation_agreement(_vs._read_events())
        return sum(s.get("false_pass", 0) for s in summary.get("by_class", {}).values())
    except Exception:
        return None


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Free-validator ROI (hosted-free rung; historical local "
                    "rows counted): paid calls skipped, escalation rate, "
                    "latency, estimated USD saved (BACKLOG #9).")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args(argv)

    summary = analyze_roi(
        _read_events(["VALIDATION_LADDER"]),
        _read_events(["QUALITY_GATE_VERDICT"]),
    )
    fp = _false_pass_count()
    if fp is not None:
        summary["shadow_false_pass"] = fp

    if args.json:
        print(json.dumps(summary, indent=2))
        return 0

    s = summary["step_ladder"]
    g = summary["quality_gate"]
    print("Free-validator ROI (captain's-log corpus)")
    print(f"  step ladder rows: {s['rows']} "
          f"(free-decisive {s['free_decisive']} "
          f"[hosted {s['hosted_free_decisive']}, local-era {s['local_decisive']}], "
          f"escalated {s['escalated']}, paid-only {s['paid_only']})")
    if s["free_decisive"] or s["escalated"]:
        print(f"  decisive-free rate: {s['decisive_rate']*100:.1f}% "
              f"| paid calls skipped: {s['paid_calls_skipped']} "
              f"(~${s['est_saved_usd']:.2f})")
        print(f"  latency: free {s['avg_free_latency_ms']}ms vs paid "
              f"{s['avg_paid_latency_ms']}ms; wasted free attempt on "
              f"escalation {s['avg_wasted_free_ms_on_escalation']}ms")
    else:
        print("  (no VALIDATION_LADDER rows yet — instrumentation shipped "
              "2026-07-04; data accrues from future validated runs)")
    print(f"  quality gate rows: {g['rows']} "
          f"(free-decisive {g['free_decisive']}, paid {g['paid']}) "
          f"| paid calls skipped: {g['paid_calls_skipped']} (~${g['est_saved_usd']:.2f})")
    print(f"  est total saved: ~${summary['est_total_saved_usd']:.2f}")
    if fp is not None:
        print(f"  safety: shadow-eval false_pass count = {fp} "
              f"(details: python3 -m validation_shadow --agreement)")
    print(f"  note: {summary['note']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
