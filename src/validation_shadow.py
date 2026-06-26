"""Shadow-eval harness for step validation — local-vs-paid agreement evidence.

The local (free) validator earns its keep only on the step classes where it
actually agrees with the paid judge. This module gathers that evidence the same
way `navigator_shadow` gathers dispatch-cutover evidence: decide-only
instrumentation that changes nothing. On a validated step it records BOTH the
local verdict and the paid verdict on the same result, tagged by step class, as a
`VALIDATOR_SHADOWED` captain's-log row. `--agreement` then tabulates per-class
agreement, false-pass / false-fail (paid treated as ground truth), and confidence
calibration — the inputs to a per-class `min_certainty` and a routing decision.

Two data sources, neither alters the validation decision:
  * **Escalation path** (local UNDECIDED → production calls paid anyway): both
    verdicts already exist, logged for free.
  * **Decisive path** (local confident → production skips paid): an EXTRA paid
    call is the only way to learn whether local was right. That call is real
    spend, so the whole harness is gated behind `validate.shadow_eval` (default
    off) — opt in to gather data, off to stop. Exactly mirrors navigator's
    `shadow_dispatch` opt-in.

Never raises, never changes a verdict, no-op without a real paid adapter (the
extra-call path requires an `llm.LLMAdapter`, so dry-run / test doubles are inert).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# --- config -----------------------------------------------------------------


def _cfg(key: str, default):
    """Read `validate.<key>`; tolerate config.py being unavailable in tests."""
    try:
        from config import get
        return get(f"validate.{key}", default)
    except Exception:
        return default


def shadow_eval_enabled() -> bool:
    """Whether the validation shadow experiment is running. Off by default: the
    decisive-path comparison makes a real extra paid call per validated step (the
    spend that buys agreement data), so it must be opted into explicitly."""
    val = _cfg("shadow_eval", False)
    if isinstance(val, str):
        return val.strip().lower() not in ("false", "0", "no", "off", "")
    return bool(val)


# --- recording (the decide-only instrumentation) ----------------------------


def _classify(step_text: str) -> str:
    """Step class for per-class grouping. Reuses the executor's own classifier so
    the agreement table lines up with how steps are actually routed."""
    try:
        from step_exec import _classify_step
        return _classify_step(step_text or "")
    except Exception:
        return "general"


def _verdict_fields(v) -> Dict[str, Any]:
    return {
        "passed": bool(getattr(v, "passed", True)),
        "confidence": float(getattr(v, "confidence", 0.0) or 0.0),
        "reason": str(getattr(v, "reason", ""))[:200],
    }


def record(*, step_text: str, local, local_source: str, paid, paid_source: str,
           escalated: bool) -> None:
    """Write one VALIDATOR_SHADOWED row: the two verdicts on the same step result
    plus whether they agree (paid as ground truth). Pure logging; never raises."""
    try:
        from captains_log import log_event, VALIDATOR_SHADOWED
        lf, pf = _verdict_fields(local), _verdict_fields(paid)
        agree = lf["passed"] == pf["passed"]
        step_class = _classify(step_text)
        log_event(
            VALIDATOR_SHADOWED,
            subject="validation",
            summary=(f"validator shadow [{step_class}]: "
                     f"local={'PASS' if lf['passed'] else 'FAIL'}({lf['confidence']:.2f}) "
                     f"vs paid={'PASS' if pf['passed'] else 'FAIL'}({pf['confidence']:.2f}) "
                     f"→ {'AGREE' if agree else 'DISAGREE'}"),
            context={
                "step_class": step_class,
                "step_preview": str(step_text)[:200],
                "local_passed": lf["passed"],
                "local_confidence": lf["confidence"],
                "local_source": local_source,
                "paid_passed": pf["passed"],
                "paid_confidence": pf["confidence"],
                "paid_source": paid_source,
                "agreement": "AGREE" if agree else "DISAGREE",
                "escalated": bool(escalated),
            },
        )
    except Exception:
        pass


def shadow_eval(step_text: str, result: str, local, local_source: str, *,
                paid_adapter=None, paid_verdict=None, paid_source: str = "paid",
                escalated: bool = False, confidence_threshold: float = 0.75) -> None:
    """Capture a local-vs-paid agreement row for one validated step. No-op unless
    `validate.shadow_eval` is on. Two modes:

      * `paid_verdict` given (escalation path) — the paid call already happened;
        just log the pair. Free.
      * `paid_adapter` given (decisive path) — production skipped paid, so make the
        extra paid call here to learn whether local was right. Real spend; only
        runs for a real `llm.LLMAdapter` (dry-run / test doubles are skipped).

    Decide-only: the caller's actual verdict is unchanged either way. Never raises.
    """
    if not shadow_eval_enabled():
        return
    try:
        if paid_verdict is None:
            if paid_adapter is None:
                return
            from llm import LLMAdapter
            if not isinstance(paid_adapter, LLMAdapter):   # dry-run / test double → no real spend
                return
            from verification_agent import VerificationAgent
            paid_verdict = VerificationAgent(
                paid_adapter, confidence_threshold=confidence_threshold
            ).verify_step(step_text, result)
        record(step_text=step_text, local=local, local_source=local_source,
               paid=paid_verdict, paid_source=paid_source, escalated=escalated)
    except Exception:
        pass


# --- analysis (the --agreement evidence) ------------------------------------

# Confidence buckets for calibration: does a local "0.9" actually agree with paid
# ~90% of the time? Sub-0.6 rows are the escalation tail (local was uncertain).
_BUCKETS = ((0.0, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.01))


def _bucket(conf: float) -> str:
    for lo, hi in _BUCKETS:
        if lo <= conf < hi:
            return f"{lo:.1f}-{hi:.1f}" if hi <= 1.0 else f"{lo:.1f}-1.0"
    return "1.0-1.0"


def analyze_validation_agreement(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Tabulate VALIDATOR_SHADOWED rows into per-class agreement + calibration.

    Paid is treated as ground truth (the question is whether the *free* judge can
    stand in for it). Per class: agree/disagree, plus the two error directions —
    false_pass (local PASS, paid FAIL — the dangerous one: bad output waved
    through) and false_fail (local FAIL, paid PASS — wasted escalation). The
    disagreements are returned verbatim for adjudication.
    """
    rows: List[Dict[str, Any]] = []
    for e in events:
        if e.get("event_type") != "VALIDATOR_SHADOWED":
            continue
        c = e.get("context") or {}
        rows.append({
            "timestamp": str(e.get("timestamp", ""))[:19],
            "step_class": c.get("step_class", "general"),
            "local_passed": bool(c.get("local_passed")),
            "local_confidence": float(c.get("local_confidence") or 0.0),
            "paid_passed": bool(c.get("paid_passed")),
            "agreement": c.get("agreement"),
            "escalated": bool(c.get("escalated")),
            "step_preview": str(c.get("step_preview", ""))[:80],
        })

    by_class: Dict[str, Dict[str, int]] = {}
    calibration: Dict[str, Dict[str, int]] = {}
    disagreements: List[Dict[str, Any]] = []
    for r in rows:
        slot = by_class.setdefault(
            str(r["step_class"]),
            {"agree": 0, "disagree": 0, "false_pass": 0, "false_fail": 0, "n": 0})
        slot["n"] += 1
        agree = r["local_passed"] == r["paid_passed"]
        if agree:
            slot["agree"] += 1
        else:
            slot["disagree"] += 1
            if r["local_passed"] and not r["paid_passed"]:
                slot["false_pass"] += 1
            elif not r["local_passed"] and r["paid_passed"]:
                slot["false_fail"] += 1
            disagreements.append(r)
        cb = calibration.setdefault(_bucket(r["local_confidence"]), {"agree": 0, "n": 0})
        cb["n"] += 1
        cb["agree"] += 1 if agree else 0

    total = len(rows)
    agreements = sum(s["agree"] for s in by_class.values())
    return {
        "rows": total,
        "agreements": agreements,
        "agreement_rate": (agreements / total) if total else 0.0,
        "by_class": by_class,
        "calibration": calibration,
        "disagreements": disagreements,
    }


def _read_events(base: Optional[Path] = None) -> List[Dict[str, Any]]:
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
                if "VALIDATOR_SHADOWED" not in line:
                    continue
                try:
                    events.append(json.loads(line))
                except Exception:
                    continue
        except Exception:
            continue
    return events


def _analyze_main(json_out: bool) -> int:
    summary = analyze_validation_agreement(_read_events())
    if json_out:
        print(json.dumps(summary, indent=2))
        return 0
    print(f"VALIDATOR_SHADOWED rows: {summary['rows']} "
          f"(agreement {summary['agreement_rate']*100:.1f}%)")
    print("per step class (paid = ground truth):")
    for cls, s in sorted(summary["by_class"].items()):
        rate = (s["agree"] / s["n"] * 100) if s["n"] else 0.0
        print(f"  {cls:14s} n={s['n']:3d} agree={rate:5.1f}%  "
              f"false_pass={s['false_pass']:2d} false_fail={s['false_fail']:2d}")
    if summary["calibration"]:
        print("local confidence calibration (agree-rate per bucket):")
        for b, cb in sorted(summary["calibration"].items()):
            rate = (cb["agree"] / cb["n"] * 100) if cb["n"] else 0.0
            print(f"  conf {b}  n={cb['n']:3d}  agree={rate:5.1f}%")
    if summary["disagreements"]:
        print(f"disagreements ({len(summary['disagreements'])} — adjudicate; "
              f"false_pass is the dangerous direction):")
        for d in summary["disagreements"][:40]:
            print(f"  {d['timestamp']} [{d['step_class']}] "
                  f"local={'PASS' if d['local_passed'] else 'FAIL'}"
                  f"({d['local_confidence']:.2f}) vs paid="
                  f"{'PASS' if d['paid_passed'] else 'FAIL'} | {d['step_preview']}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Shadow-eval harness for step validation: tabulate local-vs-paid "
                    "agreement (decide-only; changes nothing).")
    parser.add_argument("--agreement", action="store_true",
                        help="tabulate VALIDATOR_SHADOWED agreement per step class "
                             "(the per-class routing evidence) and exit")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args(argv)
    if args.agreement:
        return _analyze_main(args.json)
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
