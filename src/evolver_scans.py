"""Evolver statistical scanners + business-signal scan + impact analysis.

Extracted from evolver.py (Tier 3 refactor split). Owns the pure/statistical
scanners that run_evolver() fans out to (calibration, step-cost, quality
drift, canon candidates, suggestion-outcome calibration), the LLM-backed
business-signal scan, and the longitudinal apply-impact analysis.

evolver.py (facade) imports and re-exports everything here so run_evolver
and external callers keep working unchanged.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from llm_parse import extract_json, safe_float, safe_list, content_or_empty

from evolver_store import Suggestion, load_suggestions

log = logging.getLogger("maro.evolver")

# Module-level imports for clean test patching
try:
    from memory import load_outcomes
except ImportError:  # pragma: no cover
    load_outcomes = None  # type: ignore[assignment]

try:
    from llm import build_adapter, MODEL_CHEAP, LLMMessage
except ImportError:  # pragma: no cover
    build_adapter = None  # type: ignore[assignment]

try:
    from captains_log import query_log as _query_log_impl, EVOLVER_APPLIED as _EVOLVER_APPLIED_CONST
    query_log = _query_log_impl
except ImportError:  # pragma: no cover
    query_log = None  # type: ignore[assignment]
    _EVOLVER_APPLIED_CONST = "EVOLVER_APPLIED"


# ---------------------------------------------------------------------------
# Business signal scanning (Mode 2 → Mode 3 bridge)
# ---------------------------------------------------------------------------

_SIGNAL_SYSTEM = """\
You are a signal scanner. You analyze completed run outcomes to identify
actionable business opportunities, follow-up leads, and domain insights that
should become autonomous sub-missions.

You receive summaries of recent completed run results. Look for:
1. Findings that warrant deeper investigation (e.g. "this market shows unusual patterns")
2. Data sources identified but not fully explored
3. Patterns suggesting a repeatable opportunity or risk
4. Follow-up questions the current run could not answer

Do NOT propose generic "do more research" missions. Each signal must be concrete and
actionable — something that can be turned into a specific autonomous goal.

Respond with JSON:
{
  "signals": [
    {
      "signal_type": "opportunity|lead|pattern|follow_up",
      "description": "what was found and why it matters",
      "suggested_goal": "a specific, runnable goal for an autonomous agent",
      "confidence": 0.0-1.0,
      "source_outcome": "brief description of the outcome that generated this signal"
    }
  ]
}

If there are no actionable signals, return {"signals": []}.
Propose at most 3 signals. High confidence (>= 0.8) only.
"""


@dataclass
class BusinessSignal:
    signal_type: str        # "opportunity" | "lead" | "pattern" | "follow_up"
    description: str
    suggested_goal: str
    confidence: float
    source_outcome: str

    def to_dict(self) -> dict:
        return {
            "signal_type": self.signal_type,
            "description": self.description,
            "suggested_goal": self.suggested_goal,
            "confidence": self.confidence,
            "source_outcome": self.source_outcome,
        }


def _load_user_signals() -> str:
    """Load SIGNALS.md as context for signal scanning. Non-fatal — returns '' on error.

    Resolution: workspace overlay (~/.maro/workspace/user/SIGNALS.md) wins over
    the repo/install template (see config.user_file — SF-5/docs-02).
    """
    try:
        from config import user_file as _user_file
        _signals_path = _user_file("SIGNALS.md")
        if _signals_path is not None:
            return _signals_path.read_text(encoding="utf-8").strip()[:600]
    except Exception:
        pass
    return ""


def scan_outcomes_for_signals(
    outcomes: List[Any],
    *,
    dry_run: bool = False,
    min_confidence: float = 0.7,
) -> List[BusinessSignal]:
    """Scan done outcomes for actionable business signals and follow-up leads.

    Converts high-confidence signals into sub_mission Suggestion entries so the
    evolver queue can schedule them as autonomous runs. This closes the
    Mode 2 → Mode 3 loop: the system proposes its own next goals from findings.

    Also consults user/SIGNALS.md for declared active research threads — signals
    that align with user priorities get higher weighting in the proposed sub-missions.

    Args:
        outcomes: List of Outcome objects (recent).
        dry_run: Skip if True (analysis only).
        min_confidence: Filter signals below this threshold.

    Returns:
        List of BusinessSignal objects above the confidence threshold.
    """
    if dry_run or build_adapter is None:
        return []

    done_outcomes = [o for o in outcomes if getattr(o, "status", "") == "done"]
    if not done_outcomes:
        return []

    # Build summary from done outcomes — goals + key findings
    lines = ["Recent completed outcomes and their key findings:"]
    for o in done_outcomes[:15]:
        goal_text = getattr(o, "goal", "")[:80]
        summary_text = getattr(o, "summary", "")[:200]
        if summary_text:
            lines.append(
                f"  [{getattr(o, 'task_type', 'general')}] {goal_text}\n"
                f"    Finding: {summary_text}"
            )

    if len(lines) <= 1:
        return []

    # Include user/SIGNALS.md for context — align proposed sub-missions with user priorities
    user_signals = _load_user_signals()
    user_block = (
        f"\n\nActive user research priorities (from user/SIGNALS.md — weight signals that align):\n{user_signals}"
        if user_signals else ""
    )

    try:
        adapter = build_adapter(model=MODEL_CHEAP)
        resp = adapter.complete(
            [
                LLMMessage("system", _SIGNAL_SYSTEM),
                LLMMessage("user", f"Scan these outcomes for signals:{user_block}\n\n" + "\n".join(lines)),
            ],
            max_tokens=1024,
            temperature=0.3,
        )
        data = extract_json(content_or_empty(resp), dict, log_tag="evolver.signal_scan")
        if not data:
            return []

        signals: List[BusinessSignal] = []
        for r in safe_list(data.get("signals", []), element_type=dict):
            confidence = safe_float(r.get("confidence"), default=0.0, min_val=0.0, max_val=1.0)
            if confidence < min_confidence:
                continue
            suggested_goal = r.get("suggested_goal", "").strip()
            if not suggested_goal:
                continue
            signals.append(BusinessSignal(
                signal_type=r.get("signal_type", "follow_up"),
                description=r.get("description", ""),
                suggested_goal=suggested_goal,
                confidence=confidence,
                source_outcome=r.get("source_outcome", ""),
            ))

        log.info("signal_scan done=%d signals=%d", len(done_outcomes), len(signals))
        return signals

    except Exception as exc:
        log.debug("scan_outcomes_for_signals failed (non-fatal): %s", exc)
        return []


# ---------------------------------------------------------------------------
# Statistical scanners
# ---------------------------------------------------------------------------

@dataclass
class CalibrationFinding:
    """One finding from the calibration log scan."""
    decision_class: str
    entry_count: int
    override_count: int
    override_rate: float      # fraction where action_raw != action_final
    mean_confidence: float    # mean LLM-reported confidence (1–10 scale)
    suggestion: str           # human-readable recommendation


def scan_calibration_log(
    cal_path: Optional[Path] = None,
    *,
    min_entries: int = 5,
    high_override_threshold: float = 0.4,
    low_confidence_threshold: float = 6.0,
) -> List[CalibrationFinding]:
    """Scan memory/calibration.jsonl for systematic miscalibration patterns.

    Each entry in calibration.jsonl has:
        {"ts": "...", "job_id": "...", "decision_class": "...",
         "confidence": 1-10, "action_raw": "...", "action_final": "...", ...}

    Findings are generated when:
    - override_rate > high_override_threshold for a decision_class
      (LLM keeps picking an action the guardrails override)
    - mean_confidence < low_confidence_threshold for any class
      (LLM is systematically uncertain — prompt may need clearer rules)

    Args:
        cal_path: Path to calibration.jsonl. Defaults to orch_root/memory/calibration.jsonl.
        min_entries: Skip a class with fewer entries than this.
        high_override_threshold: Override rate above which a finding is raised.
        low_confidence_threshold: Mean confidence below which a finding is raised.

    Returns:
        List of CalibrationFinding objects (empty if no issues found).
    """
    if cal_path is None:
        try:
            from orch_items import memory_dir
            cal_path = memory_dir() / "calibration.jsonl"
        except Exception:
            return []

    if not cal_path.exists():
        return []

    # Parse entries
    entries: List[Dict[str, Any]] = []
    try:
        with open(cal_path) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    except OSError:
        return []

    if not entries:
        return []

    # Group by decision_class
    from collections import defaultdict
    by_class: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for entry in entries:
        dc = entry.get("decision_class", "unknown")
        by_class[dc].append(entry)

    findings: List[CalibrationFinding] = []
    for decision_class, class_entries in by_class.items():
        if len(class_entries) < min_entries:
            continue

        override_count = sum(
            1 for e in class_entries
            if e.get("action_raw") != e.get("action_final")
        )
        override_rate = override_count / len(class_entries)
        confidences = [e.get("confidence", 5) for e in class_entries if isinstance(e.get("confidence"), (int, float))]
        mean_confidence = sum(confidences) / len(confidences) if confidences else 5.0

        finding_reason = []
        if override_rate > high_override_threshold:
            finding_reason.append(
                f"override rate {override_rate:.0%} (>{high_override_threshold:.0%}) — "
                f"LLM action is being overridden by guardrails too often; "
                f"add clearer {decision_class!r} examples to the escalation prompt"
            )
        if mean_confidence < low_confidence_threshold:
            finding_reason.append(
                f"mean confidence {mean_confidence:.1f}/10 (<{low_confidence_threshold}) — "
                f"LLM is systematically uncertain on {decision_class!r} decisions; "
                f"consider adding explicit criteria or worked examples"
            )

        if finding_reason:
            findings.append(CalibrationFinding(
                decision_class=decision_class,
                entry_count=len(class_entries),
                override_count=override_count,
                override_rate=override_rate,
                mean_confidence=mean_confidence,
                suggestion="; ".join(finding_reason),
            ))

    return findings


def scan_step_costs(
    entries: Optional[List[dict]] = None,
    *,
    expensive_threshold_multiplier: float = 2.0,
    min_entries: int = 5,
) -> List[Suggestion]:
    """Detect high-burn step patterns from step-costs.jsonl and propose cheaper alternatives.

    No LLM calls — pure statistical analysis. Identifies step types whose average
    token cost is more than `expensive_threshold_multiplier`× the median, and generates
    a Suggestion recommending Haiku routing or output-size constraints.

    Returns:
        List of Suggestion objects (category="cost_optimization").
    """
    try:
        from metrics import analyze_step_costs, load_step_costs
    except ImportError:
        return []

    try:
        if entries is None:
            entries = load_step_costs(limit=200)
        if len(entries) < min_entries:
            return []

        analysis = analyze_step_costs(entries)
        expensive_types = analysis.get("expensive_types", [])
        by_type = analysis.get("by_type", {})
        total_cost = analysis.get("total_cost_usd", 0.0)

        if not expensive_types:
            return []

        suggestions: List[Suggestion] = []
        for step_type in expensive_types:
            stats = by_type.get(step_type, {})
            avg_tok = stats.get("avg_tokens", 0)
            count = stats.get("count", 0)
            if count < 2:
                continue

            # Estimate potential savings: routing to Haiku saves ~5× vs Sonnet
            avg_cost = stats.get("avg_cost_usd", 0.0)
            est_savings = avg_cost * count * 0.8  # conservative 80% savings via Haiku

            suggestion_text = (
                f"Step type '{step_type}' averages {avg_tok:,} tokens across {count} steps "
                f"(~${avg_cost:.6f}/step, ~${est_savings:.4f} total savings potential). "
                f"Consider routing these steps to MODEL_CHEAP (Haiku) via classify_step_model(), "
                f"or adding a token-budget constraint in the step prompt."
            )
            suggestions.append(Suggestion(
                suggestion_id=f"cost-{step_type[:12]}",
                category="cost_optimization",
                target=step_type,
                suggestion=suggestion_text,
                failure_pattern=f"high_burn_step: {step_type} avg={avg_tok}tok",
                confidence=0.70,
                outcomes_analyzed=count,
            ))
            log.info("scan_step_costs: high-burn step_type=%r avg=%d tok count=%d",
                     step_type, avg_tok, count)

        return suggestions

    except Exception as exc:
        log.debug("scan_step_costs failed (non-fatal): %s", exc)
        return []


# ---------------------------------------------------------------------------
# Quality drift detection
# ---------------------------------------------------------------------------

@dataclass
class QualityDriftFinding:
    """One finding from the quality drift scan."""
    metric: str                # e.g. "success_rate", "avg_cost_usd"
    current_value: float
    baseline_value: float      # rolling average of prior cycles
    delta_pct: float           # percentage change from baseline
    consecutive_drops: int     # how many consecutive cycles below baseline
    suggestion: str


def _baselines_path() -> Path:
    from orch_items import memory_dir
    return memory_dir() / "evolver-baselines.jsonl"


def _load_baselines(limit: int = 20) -> List[dict]:
    """Load recent evolver cycle baselines (newest first)."""
    path = _baselines_path()
    if not path.exists():
        return []
    lines = []
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            raw = raw.strip()
            if raw:
                try:
                    lines.append(json.loads(raw))
                except json.JSONDecodeError:
                    pass
    except OSError:
        return []
    return lines[-limit:][::-1]  # newest first


def _save_baseline(entry: dict) -> None:
    """Append a cycle quality snapshot to baselines."""
    path = _baselines_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    from file_lock import locked_append
    locked_append(path, json.dumps(entry))


def scan_quality_drift(
    outcomes: List[dict],
    *,
    drop_threshold_pct: float = 15.0,
    consecutive_alert: int = 3,
) -> List[QualityDriftFinding]:
    """Compare current cycle quality against rolling baseline from prior cycles.

    Tracks success_rate and avg cost. Flags when current cycle is significantly
    worse than the rolling average of prior cycles for N consecutive cycles.

    Args:
        outcomes: Current cycle's outcome dicts.
        drop_threshold_pct: Percentage drop from baseline that counts as degradation.
        consecutive_alert: Number of consecutive drops before generating a finding.

    Returns:
        List of QualityDriftFinding (empty if quality is stable or improving).
    """
    if not outcomes:
        return []

    # Compute current cycle metrics
    total = len(outcomes)
    done = sum(1 for o in outcomes if o.get("status") == "done")
    current_success = done / total if total > 0 else 0.0

    costs = [o.get("cost_usd", 0.0) for o in outcomes if isinstance(o.get("cost_usd"), (int, float))]
    current_avg_cost = sum(costs) / len(costs) if costs else 0.0

    now = datetime.now(timezone.utc).isoformat()
    snapshot = {
        "ts": now,
        "success_rate": round(current_success, 4),
        "avg_cost_usd": round(current_avg_cost, 6),
        "outcomes_count": total,
    }

    # Save this cycle's snapshot
    try:
        _save_baseline(snapshot)
    except Exception:
        pass

    # Load prior baselines
    prior = _load_baselines(limit=20)
    # Skip the one we just wrote (newest)
    if prior and prior[0].get("ts") == now:
        prior = prior[1:]

    if len(prior) < 3:
        return []  # not enough history to detect drift

    findings: List[QualityDriftFinding] = []

    # Check each metric for drift
    for metric_key, current_val, higher_is_better in [
        ("success_rate", current_success, True),
        ("avg_cost_usd", current_avg_cost, False),
    ]:
        prior_values = [p.get(metric_key, 0.0) for p in prior if isinstance(p.get(metric_key), (int, float))]
        if not prior_values:
            continue
        baseline = sum(prior_values) / len(prior_values)
        if baseline == 0:
            continue

        if higher_is_better:
            delta_pct = ((baseline - current_val) / baseline) * 100
            is_worse = current_val < baseline * (1 - drop_threshold_pct / 100)
        else:
            delta_pct = ((current_val - baseline) / baseline) * 100
            is_worse = current_val > baseline * (1 + drop_threshold_pct / 100)

        if not is_worse:
            continue

        # Count consecutive drops (including this one)
        consecutive = 1
        for pv in prior_values:
            if higher_is_better:
                if pv < baseline * (1 - drop_threshold_pct / 100):
                    consecutive += 1
                else:
                    break
            else:
                if pv > baseline * (1 + drop_threshold_pct / 100):
                    consecutive += 1
                else:
                    break

        if consecutive >= consecutive_alert:
            direction = "dropped" if higher_is_better else "risen"
            findings.append(QualityDriftFinding(
                metric=metric_key,
                current_value=current_val,
                baseline_value=baseline,
                delta_pct=delta_pct,
                consecutive_drops=consecutive,
                suggestion=(
                    f"{metric_key} has {direction} {delta_pct:.1f}% from baseline "
                    f"({current_val:.4f} vs {baseline:.4f}) for {consecutive} consecutive cycles. "
                    f"Recent evolver changes may be degrading quality — consider rolling back "
                    f"recent auto-applied suggestions."
                ),
            ))

    return findings


# ---------------------------------------------------------------------------
# Stage 2→3: Canon candidate scan — surfaces lessons ready for identity promotion
# ---------------------------------------------------------------------------

def scan_canon_candidates(
    *,
    min_hits: int = 10,
    min_task_types: int = 3,
) -> List[Suggestion]:
    """Scan long-tier lessons for Stage 2→3 promotion candidates.

    A lesson that has been applied 10+ times across 3+ task types has proven
    itself broadly. It's a candidate to move from tiered retrieval (Stage 2)
    to always-active identity in the system prompt (Stage 3 / AGENTS.md).

    Promotion is NOT automatic — this just surfaces the candidates as
    observation Suggestions in the evolver report for human review.

    Returns one Suggestion per candidate, category='crystallization'.
    """
    try:
        from memory import get_canon_candidates as _get_canon
    except ImportError:
        return []

    try:
        candidates = _get_canon(min_hits=min_hits, min_task_types=min_task_types)
    except Exception as exc:
        log.debug("scan_canon_candidates: failed to load candidates: %s", exc)
        return []

    suggestions: List[Suggestion] = []
    import uuid as _cuid
    for c in candidates:
        lesson_text = c.get("lesson", "")[:200]
        times = c.get("times_applied", 0)
        types = c.get("task_types_seen", [])
        lid = c.get("lesson_id", "?")
        suggestions.append(Suggestion(
            suggestion_id=f"canon-{_cuid.uuid4().hex[:8]}",
            category="crystallization",
            target=c.get("task_type", "general"),
            suggestion=(
                f"PROMOTE TO IDENTITY (Stage 3): '{lesson_text}' — "
                f"applied {times}x across {len(types)} task types "
                f"({', '.join(types[:4])}). "
                f"Add to AGENTS.md or persona system prompt to eliminate retrieval cost."
            ),
            failure_pattern=f"lesson_id={lid} times_applied={times} task_types={len(types)}",
            confidence=min(0.95, 0.5 + times * 0.03 + len(types) * 0.05),
            outcomes_analyzed=times,
        ))

    return suggestions


def _record_suggestion_outcomes(
    suggestion_ids: List[str],
    passed: bool,
    run_id: str,
) -> None:
    """Write per-suggestion verification outcomes to suggestion_outcomes.jsonl.

    Called from _verify_post_apply after the test suite runs.  Enables
    scan_suggestion_outcomes() to compute empirical confidence (actual pass
    rate vs self-reported confidence) across categories over time.
    """
    if not suggestion_ids:
        return
    try:
        from config import memory_dir as _memory_dir
        out_path = _memory_dir() / "suggestion_outcomes.jsonl"

        # Look up confidence + category for each suggestion_id from change_log
        from config import memory_dir
        cl_path = memory_dir() / "change_log.jsonl"
        cl_by_id: dict = {}
        if cl_path.exists():
            for _line in cl_path.read_text(encoding="utf-8").splitlines():
                _line = _line.strip()
                if not _line:
                    continue
                try:
                    _entry = json.loads(_line)
                    _sid = _entry.get("suggestion_id", "")
                    if _sid:
                        cl_by_id[_sid] = _entry
                except Exception:
                    pass

        now = datetime.now(timezone.utc).isoformat()
        lines = []
        for sid in suggestion_ids:
            cl_entry = cl_by_id.get(sid, {})
            lines.append(json.dumps({
                "suggestion_id": sid,
                "category": cl_entry.get("category", "unknown"),
                "confidence": float(cl_entry.get("confidence", 0.5)),
                "verified": passed,
                "run_id": run_id,
                "verified_at": now,
            }))

        from file_lock import locked_append
        for _line in lines:
            locked_append(out_path, _line)
        log.debug("_record_suggestion_outcomes: wrote %d entries (passed=%s) to %s",
                  len(lines), passed, out_path)
    except Exception as exc:
        log.debug("_record_suggestion_outcomes failed (non-fatal): %s", exc)


def scan_suggestion_outcomes(
    *,
    min_samples: int = 3,
    overconfidence_ratio: float = 0.6,
    outcomes_path: "Path | None" = None,
) -> List[Suggestion]:
    """Compute empirical confidence from suggestion_outcomes.jsonl.

    Compares self-reported confidence (from the evolver LLM at suggestion time)
    against the actual verify-pass rate.  If a category's empirical pass rate is
    consistently below ``overconfidence_ratio * mean_self_reported_confidence``,
    it's systematically overconfident.

    Returns Suggestion(category='observation') entries for each miscalibrated
    category, so the evolver report surfaces them for human review.

    Args:
        min_samples: Minimum verified outcomes per category to report.
        overconfidence_ratio: Flag category if empirical < ratio * self_reported.
        outcomes_path: Override default path (for testing).
    """
    try:
        if outcomes_path is None:
            from orch_items import memory_dir
            outcomes_path = memory_dir() / "suggestion_outcomes.jsonl"

        if not outcomes_path.exists():
            return []

        from collections import defaultdict
        cat_data: dict = defaultdict(lambda: {"passed": 0, "failed": 0, "confidences": []})
        for _line in outcomes_path.read_text(encoding="utf-8").splitlines():
            _line = _line.strip()
            if not _line:
                continue
            try:
                entry = json.loads(_line)
                cat = entry.get("category", "unknown")
                cat_data[cat]["confidences"].append(float(entry.get("confidence", 0.5)))
                if entry.get("verified"):
                    cat_data[cat]["passed"] += 1
                else:
                    cat_data[cat]["failed"] += 1
            except Exception:
                continue

        suggestions: List[Suggestion] = []
        import uuid as _uuid
        for cat, data in cat_data.items():
            total = data["passed"] + data["failed"]
            if total < min_samples:
                continue
            empirical_rate = data["passed"] / total
            mean_conf = sum(data["confidences"]) / len(data["confidences"]) if data["confidences"] else 0.5
            if mean_conf <= 0:
                continue
            # Flag if empirical pass rate is well below self-reported confidence
            if empirical_rate < overconfidence_ratio * mean_conf:
                suggestions.append(Suggestion(
                    suggestion_id=f"calibration-{_uuid.uuid4().hex[:8]}",
                    category="observation",
                    target=cat,
                    suggestion=(
                        f"CONFIDENCE MISCALIBRATION in category '{cat}': "
                        f"self-reported confidence {mean_conf:.2f} but empirical pass rate "
                        f"{empirical_rate:.2f} ({data['passed']}/{total} verified). "
                        f"Reduce LLM confidence prompts for this category or tighten "
                        f"auto-apply threshold."
                    ),
                    failure_pattern=f"overconfident:{cat}",
                    confidence=0.8,
                    outcomes_analyzed=total,
                ))
                log.info(
                    "scan_suggestion_outcomes: %s overconfident — reported=%.2f empirical=%.2f (%d/%d)",
                    cat, mean_conf, empirical_rate, data["passed"], total,
                )

        return suggestions
    except Exception as exc:
        log.debug("scan_suggestion_outcomes failed (non-fatal): %s", exc)
        return []


# ---------------------------------------------------------------------------
# Longitudinal evolver impact analysis (K6 verify→learn gap)
# ---------------------------------------------------------------------------

@dataclass
class EvolverImpactRecord:
    """Impact of a single EVOLVER_APPLIED event on subsequent run quality."""
    suggestion_id: str
    category: str
    applied_at: str               # ISO timestamp of apply event
    outcomes_before: int          # Outcomes in lookback window before apply
    stuck_before: int             # Stuck outcomes before apply
    outcomes_after: int           # Outcomes in lookback window after apply
    stuck_after: int              # Stuck outcomes after apply
    stuck_rate_before: float      # stuck / total before (NaN if no data)
    stuck_rate_after: float       # stuck / total after (NaN if no data)
    delta: float                  # stuck_rate_after - stuck_rate_before (neg = improvement)
    verdict: str                  # "improved" | "degraded" | "neutral" | "insufficient_data"


def scan_evolver_impact(
    *,
    lookback_hours: int = 24,
    lookahead_hours: int = 24,
    min_outcomes: int = 3,
    limit: int = 10,
) -> List[EvolverImpactRecord]:
    """Longitudinal analysis: did evolver mutations actually improve run quality?

    For each recent EVOLVER_APPLIED captain's log event, compares the stuck rate
    in a window before the application vs. the window after. Surfaces the delta
    as evidence for or against the verify→learn loop working.

    Args:
        lookback_hours:  How many hours before the apply event to sample.
        lookahead_hours: How many hours after the apply event to sample.
        min_outcomes:    Minimum outcomes in each window to produce a verdict.
        limit:           Max EVOLVER_APPLIED events to analyze.

    Returns:
        List of EvolverImpactRecord, one per apply event (most recent first).
    """
    import math
    from datetime import datetime, timedelta, timezone

    def _parse_iso(ts: str) -> Optional[datetime]:
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except Exception:
            return None

    # Apply records: suggestions.jsonl is the durable source of truth —
    # apply_suggestion() stamps applied_at at apply time. The captain's log
    # EVOLVER_APPLIED events remain only as fallback for historical applies
    # that predate the stamp (captain's log = visibility/data, not the wire
    # a system function hangs off — THREAD_ARCHITECTURE.md).
    apply_records: List[dict] = []
    _seen_ids: set = set()
    try:
        for s in load_suggestions(limit=1000):
            if s.applied and getattr(s, "applied_at", ""):
                apply_records.append({
                    "suggestion_id": s.suggestion_id,
                    "category": s.category,
                    "applied_at": s.applied_at,
                })
                _seen_ids.add(s.suggestion_id)
    except Exception:
        pass
    try:
        if query_log is not None:
            for _event in query_log(event_type=_EVOLVER_APPLIED_CONST, limit=limit):
                _ctx = _event.get("context", {}) or {}
                _sid = str(_ctx.get("suggestion_id", "") or _event.get("subject", ""))
                if _sid and _sid in _seen_ids:
                    continue
                apply_records.append({
                    "suggestion_id": _sid,
                    "category": str(_ctx.get("category", "unknown")),
                    "applied_at": _event.get("timestamp", ""),
                })
    except Exception:
        pass
    if not apply_records:
        return []
    apply_records.sort(key=lambda r: r["applied_at"], reverse=True)
    apply_records = apply_records[:limit]

    # Load outcomes for window sampling (use module-level load_outcomes for testability)
    _outcomes_cache: Optional[List[Any]] = None
    if load_outcomes is not None:
        try:
            _outcomes_cache = load_outcomes(limit=5000)
        except Exception:
            return []
    else:
        return []

    def _outcomes_for_window(t_center: datetime, hours_before: float, hours_after: float) -> List[Any]:
        """Return outcomes in (t_center - before, t_center + after) window."""
        t_from = t_center - timedelta(hours=hours_before)
        t_to = t_center + timedelta(hours=hours_after)
        if _outcomes_cache is None:
            return []
        results = []
        for o in _outcomes_cache:
            ts = getattr(o, "created_at", None) or getattr(o, "timestamp", None) or ""
            t_o = _parse_iso(ts) if ts else None
            if t_o and t_from <= t_o < t_to:
                results.append(o)
        return results

    def _stuck_rate(outcomes: List[Any]) -> float:
        if not outcomes:
            return float("nan")
        n_stuck = sum(1 for o in outcomes if getattr(o, "status", "done") == "stuck")
        return n_stuck / len(outcomes)

    records: List[EvolverImpactRecord] = []
    for rec in apply_records:
        applied_at_str = rec["applied_at"]
        t_apply = _parse_iso(applied_at_str)
        if not t_apply:
            continue

        suggestion_id = rec["suggestion_id"]
        category = rec["category"]

        outcomes_before = _outcomes_for_window(t_apply, lookback_hours, 0)
        outcomes_after = _outcomes_for_window(t_apply, 0, lookahead_hours)

        n_before = len(outcomes_before)
        n_after = len(outcomes_after)
        sr_before = _stuck_rate(outcomes_before)
        sr_after = _stuck_rate(outcomes_after)

        if n_before < min_outcomes and n_after < min_outcomes:
            verdict = "insufficient_data"
            delta = float("nan")
        elif math.isnan(sr_before) or math.isnan(sr_after):
            verdict = "insufficient_data"
            delta = float("nan")
        else:
            delta = sr_after - sr_before
            if abs(delta) < 0.05:
                verdict = "neutral"
            elif delta < 0:
                verdict = "improved"
            else:
                verdict = "degraded"

        records.append(EvolverImpactRecord(
            suggestion_id=suggestion_id,
            category=category,
            applied_at=applied_at_str,
            outcomes_before=n_before,
            stuck_before=sum(1 for o in outcomes_before if getattr(o, "status", "done") == "stuck"),
            outcomes_after=n_after,
            stuck_after=sum(1 for o in outcomes_after if getattr(o, "status", "done") == "stuck"),
            stuck_rate_before=sr_before,
            stuck_rate_after=sr_after,
            delta=delta,  # NaN for insufficient_data — callers check math.isnan()
            verdict=verdict,
        ))

    return records


def format_impact_summary(records: List[EvolverImpactRecord]) -> str:
    """Format impact records as a human-readable summary."""
    if not records:
        return "No EVOLVER_APPLIED events found (or insufficient outcome data)."

    improved = sum(1 for r in records if r.verdict == "improved")
    degraded = sum(1 for r in records if r.verdict == "degraded")
    neutral = sum(1 for r in records if r.verdict == "neutral")
    no_data = sum(1 for r in records if r.verdict == "insufficient_data")

    lines = [
        f"Evolver impact analysis: {len(records)} applied suggestion(s) analyzed",
        f"  improved={improved} degraded={degraded} neutral={neutral} no_data={no_data}",
        "",
    ]
    for r in records:
        if r.verdict == "insufficient_data":
            lines.append(
                f"  [{r.category}] {r.suggestion_id[:12]} @ {r.applied_at[:10]} — "
                f"insufficient data (before={r.outcomes_before}, after={r.outcomes_after})"
            )
        else:
            import math
            _delta_str = f"{r.delta:+.1%}" if not math.isnan(r.delta) else "n/a"
            lines.append(
                f"  [{r.category}] {r.suggestion_id[:12]} @ {r.applied_at[:10]} — "
                f"{r.verdict}: stuck {r.stuck_rate_before:.0%}→{r.stuck_rate_after:.0%} "
                f"(Δ{_delta_str})"
            )
    return "\n".join(lines)
