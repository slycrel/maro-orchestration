"""Phase 12: Inspector — end-to-end quality oversight agent.

Role distinction (important):
  Heartbeat  = health: is the system running? (periodic liveness, recovery)
  Inspector  = quality: is the system producing the right outcomes? (post-hoc analysis)

The Inspector is a read-only observer. It never modifies running loops.
It examines outcomes.jsonl after the fact and asks:
  - Did we produce the right results?
  - Are there repeating friction patterns?
  - What can the evolver do to improve quality?

Seven friction signals based on Factory AI Signals research:
  error_events          — LLM/API failures caused the session to get stuck
  repeated_rephrasing   — same task attempted with slight variations without progress
  escalation_tone       — language in stuck reason indicates escalating severity
  platform_confusion    — agent confused about what platform/context it is operating in
  abandoned_tool_flow   — tool call chains were abandoned mid-way
  backtracking          — agent repeated the same approach after it already failed
  context_churn         — very large context + stuck = too much context, no progress

Closed loop:
  Inspector findings → suggestions.jsonl → Evolver reads → better future prompts
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from llm_parse import extract_json, safe_float, content_or_empty

log = logging.getLogger("maro.inspector")

# Module-level imports so tests can patch cleanly
try:
    from memory import load_outcomes
except ImportError:  # pragma: no cover
    load_outcomes = None  # type: ignore[assignment]

try:
    from llm import build_adapter, MODEL_CHEAP, MODEL_MID, LLMMessage
except ImportError:  # pragma: no cover
    build_adapter = None  # type: ignore[assignment]
    MODEL_CHEAP = "cheap"  # type: ignore[assignment]
    MODEL_MID = "mid"  # type: ignore[assignment]

try:
    from attribution import attribute_failure, Attribution
except ImportError:  # pragma: no cover
    attribute_failure = None  # type: ignore[assignment]
    Attribution = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Friction signal constants (Factory AI Signals research)
# ---------------------------------------------------------------------------

SIGNAL_ERROR_EVENTS       = "error_events"
SIGNAL_REPEATED_REPHRASE  = "repeated_rephrasing"
SIGNAL_ESCALATION_TONE    = "escalation_tone"
SIGNAL_PLATFORM_CONFUSION = "platform_confusion"
SIGNAL_ABANDONED_TOOL_FLOW = "abandoned_tool_flow"
SIGNAL_BACKTRACKING       = "backtracking"
SIGNAL_CONTEXT_CHURN      = "context_churn"

ALL_SIGNALS = [
    SIGNAL_ERROR_EVENTS,
    SIGNAL_REPEATED_REPHRASE,
    SIGNAL_ESCALATION_TONE,
    SIGNAL_PLATFORM_CONFUSION,
    SIGNAL_ABANDONED_TOOL_FLOW,
    SIGNAL_BACKTRACKING,
    SIGNAL_CONTEXT_CHURN,
]

# ---------------------------------------------------------------------------
# Configurable thresholds (override via env vars for calibration)
# ---------------------------------------------------------------------------

def _env_float(key: str, default: float) -> float:
    """Read a float from env, falling back to default."""
    val = os.environ.get(key)
    if val is not None:
        try:
            return float(val)
        except (ValueError, TypeError):
            pass
    return default


def _env_int(key: str, default: int) -> int:
    val = os.environ.get(key)
    if val is not None:
        try:
            return int(val)
        except (ValueError, TypeError):
            pass
    return default


# Thresholds: loaded from config.yml with env var and hardcoded fallbacks.
# Priority: env var > config.yml > hardcoded default.
def _cfg_float(config_key: str, env_key: str, default: float) -> float:
    """Load a float threshold: env var → config.yml → hardcoded default."""
    env_val = os.environ.get(env_key)
    if env_val is not None:
        try:
            return float(env_val)
        except (ValueError, TypeError):
            pass
    try:
        from config import get as _cfg_get
        val = _cfg_get(config_key)
        if val is not None:
            return float(val)
    except Exception:
        pass
    return default

def _cfg_int(config_key: str, env_key: str, default: int) -> int:
    env_val = os.environ.get(env_key)
    if env_val is not None:
        try:
            return int(env_val)
        except (ValueError, TypeError):
            pass
    try:
        from config import get as _cfg_get
        val = _cfg_get(config_key)
        if val is not None:
            return int(val)
    except Exception:
        pass
    return default

_BREACH_THRESHOLD = _cfg_float("inspector.breach_threshold", "INSPECTOR_BREACH_THRESHOLD", 0.30)
_ESCALATION_MIN_HITS = _cfg_int("inspector.escalation_min_hits", "INSPECTOR_ESCALATION_MIN_HITS", 3)
_CONTEXT_CHURN_TOKEN_THRESHOLD = _cfg_int("inspector.context_churn_tokens", "INSPECTOR_CONTEXT_CHURN_TOKENS", 10000)
_ALIGNMENT_GOOD = _cfg_float("inspector.alignment_good", "INSPECTOR_ALIGNMENT_GOOD", 0.7)
_ALIGNMENT_POOR = _cfg_float("inspector.alignment_poor", "INSPECTOR_ALIGNMENT_POOR", 0.4)
_REPHRASING_MIN_COUNT = _cfg_int("inspector.rephrasing_min_count", "INSPECTOR_REPHRASING_MIN_COUNT", 2)


def inspector_thresholds() -> Dict[str, Any]:
    """Return current threshold values for introspection/calibration."""
    return {
        "breach_threshold": _BREACH_THRESHOLD,
        "escalation_min_hits": _ESCALATION_MIN_HITS,
        "context_churn_token_threshold": _CONTEXT_CHURN_TOKEN_THRESHOLD,
        "alignment_good": _ALIGNMENT_GOOD,
        "alignment_poor": _ALIGNMENT_POOR,
        "rephrasing_min_count": _REPHRASING_MIN_COUNT,
    }


# Escalation keywords for tone detection.
#
# Session 20 adversarial review finding 3.5: the previous list included
# "stuck", "error", "failed", "cannot" — words that appear in *every*
# stuck outcome's `stuck_reason` by construction (it's literally the
# stuck message). The signal fired tautologically on any stuck session
# and said nothing about escalation severity.
#
# Split into tautological vs informative. Tautological keywords are
# expected in stuck_reason and carry no signal on their own. Informative
# keywords indicate genuine frustration/escalation beyond "yep, stuck".
# We require ≥2 informative hits before firing, and ignore tautological
# hits entirely.
_ESCALATION_KEYWORDS_TAUTOLOGICAL = frozenset([
    "stuck", "error", "failed", "cannot",
])
_ESCALATION_KEYWORDS_INFORMATIVE = frozenset([
    "broken", "impossible",
    "doesn't work", "not working", "won't work", "can't",
])
# Legacy export: union for backward compatibility with anything that imports it.
_ESCALATION_KEYWORDS = _ESCALATION_KEYWORDS_TAUTOLOGICAL | _ESCALATION_KEYWORDS_INFORMATIVE
_ESCALATION_INFORMATIVE_MIN_HITS = 2

# Human-readable descriptions for each friction type (spec FRICTION_TYPES dict)
FRICTION_TYPES: Dict[str, str] = {
    SIGNAL_ERROR_EVENTS:        "Tool failures, LLM errors, stuck loops",
    SIGNAL_REPEATED_REPHRASE:   "Same goal decomposed 3+ times with little variation",
    SIGNAL_ESCALATION_TONE:     "Words like 'broken', 'failed', 'stuck' in decision logs",
    SIGNAL_PLATFORM_CONFUSION:  "Steps that ask about capabilities rather than execute",
    SIGNAL_ABANDONED_TOOL_FLOW: "Steps marked blocked without attempting alternatives",
    SIGNAL_BACKTRACKING:        "Step marked done then re-added or re-executed",
    SIGNAL_CONTEXT_CHURN:       "Same context/lessons loaded but not applied",
}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class FrictionSignal:
    signal_type: str      # one of ALL_SIGNALS
    severity: str         # "low" | "medium" | "high"
    count: int = 1
    evidence: str = ""    # anonymized evidence snippet (no raw user content — max 80 chars)
    session_id: str = ""

    def to_dict(self) -> dict:
        return {
            "signal_type": self.signal_type,
            "severity": self.severity,
            "count": self.count,
            "evidence": self.evidence,
            "session_id": self.session_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "FrictionSignal":
        return cls(
            signal_type=d.get("signal_type", ""),
            severity=d.get("severity", "low"),
            count=d.get("count", 1),
            evidence=d.get("evidence", ""),
            session_id=d.get("session_id", ""),
        )


@dataclass
class SessionQuality:
    session_id: str           # loop_id or mission_id or outcome_id
    session_type: str         # "loop" | "mission"
    goal: str
    project: str
    status: str               # "done" | "stuck" | "interrupted"
    goal_alignment_score: float  # 0.0-1.0: did completed work match mission intent?
    friction_signals: List[FrictionSignal] = field(default_factory=list)
    delight_signals: List[str] = field(default_factory=list)   # positive patterns
    overall_quality: str = "fair"     # "good" | "fair" | "poor"
    inspector_notes: str = ""         # brief LLM analysis
    inspected_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "session_type": self.session_type,
            "goal": self.goal,
            "project": self.project,
            "status": self.status,
            "goal_alignment_score": self.goal_alignment_score,
            "friction_signals": [s.to_dict() for s in self.friction_signals],
            "delight_signals": self.delight_signals,
            "overall_quality": self.overall_quality,
            "inspector_notes": self.inspector_notes,
            "inspected_at": self.inspected_at,
        }


@dataclass
class InspectionReport:
    """Lightweight quality scan report from run_inspection_cycle().

    Has quality_distribution, suggestions (plain strings), threshold_breaches.
    Written to memory/inspection-log.jsonl. Evolver reads from
    suggestions.jsonl directly, not from this report file.

    (The heavyweight spec §12 InspectorReport/run_full_inspector() this class
    was originally distinguished from was dead code — zero production
    callers, superseded by this lightweight path — and was deleted 2026-07-02.
    See docs/REFACTOR_PLAN.md Tier 1.)
    """
    run_id: str
    inspected_sessions: int
    quality_distribution: Dict[str, int] = field(default_factory=lambda: {"good": 0, "fair": 0, "poor": 0})
    top_friction_signals: List[Dict] = field(default_factory=list)
    alignment_score_avg: float = 0.0
    patterns: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)
    threshold_breaches: List[str] = field(default_factory=list)
    elapsed_ms: int = 0
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def summary(self) -> str:
        dist = self.quality_distribution
        lines = [
            f"inspector run_id={self.run_id}",
            f"sessions={self.inspected_sessions}",
            f"quality: good={dist.get('good', 0)} fair={dist.get('fair', 0)} poor={dist.get('poor', 0)}",
            f"alignment_avg={self.alignment_score_avg:.2f}",
            f"elapsed_ms={self.elapsed_ms}",
        ]
        if self.patterns:
            lines.append("patterns:")
            for p in self.patterns[:3]:
                lines.append(f"  - {p}")
        if self.suggestions:
            lines.append("suggestions:")
            for s in self.suggestions[:3]:
                lines.append(f"  - {s}")
        if self.threshold_breaches:
            lines.append(f"threshold_breaches: {', '.join(self.threshold_breaches)}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "inspected_sessions": self.inspected_sessions,
            "quality_distribution": self.quality_distribution,
            "top_friction_signals": self.top_friction_signals,
            "alignment_score_avg": self.alignment_score_avg,
            "patterns": self.patterns,
            "suggestions": self.suggestions,
            "threshold_breaches": self.threshold_breaches,
            "elapsed_ms": self.elapsed_ms,
            "generated_at": self.generated_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "InspectionReport":
        return cls(
            run_id=d.get("run_id", ""),
            inspected_sessions=d.get("inspected_sessions", 0),
            quality_distribution=d.get("quality_distribution", {"good": 0, "fair": 0, "poor": 0}),
            top_friction_signals=d.get("top_friction_signals", []),
            alignment_score_avg=d.get("alignment_score_avg", 0.0),
            patterns=d.get("patterns", []),
            suggestions=d.get("suggestions", []),
            threshold_breaches=d.get("threshold_breaches", []),
            elapsed_ms=d.get("elapsed_ms", 0),
            generated_at=d.get("generated_at", ""),
        )


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------

def _inspection_log_path() -> Path:
    from orch_items import memory_dir
    return memory_dir() / "inspection-log.jsonl"


def _suggestions_path() -> Path:
    """Path to suggestions.jsonl — shared with evolver."""
    from orch_items import memory_dir
    return memory_dir() / "suggestions.jsonl"


def _save_inspection_report(report: InspectionReport) -> None:
    """Append inspection report to inspection-log.jsonl."""
    p = _inspection_log_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(report.to_dict()) + "\n")


def _save_inspection_suggestions(suggestions: List[str]) -> None:
    """Write inspector suggestions to suggestions.jsonl (feeds evolver pipeline).

    Privacy principle: suggestions are aggregate patterns, not raw user content.
    """
    if not suggestions:
        return
    p = _suggestions_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    with p.open("a", encoding="utf-8") as f:
        for i, suggestion_text in enumerate(suggestions):
            entry = {
                "suggestion_id": f"insp-{uuid.uuid4().hex[:6]}-{i:02d}",
                "category": "inspection_finding",
                "target": "all",
                "suggestion": suggestion_text,
                "failure_pattern": "inspector cross-session analysis",
                "confidence": 0.7,
                "outcomes_analyzed": 0,
                "generated_at": now,
                "applied": False,
            }
            f.write(json.dumps(entry) + "\n")


def get_latest_inspection() -> Optional[InspectionReport]:
    """Return the most recent InspectionReport from inspection-log.jsonl, or None."""
    p = _inspection_log_path()
    if not p.exists():
        return None
    last_line = None
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                last_line = line
    except Exception:
        return None
    if last_line is None:
        return None
    try:
        return InspectionReport.from_dict(json.loads(last_line))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Friction detection — heuristic-first, LLM as enhancement
# ---------------------------------------------------------------------------

def detect_friction_signals(outcome: dict) -> List[FrictionSignal]:
    """Detect friction signals from a single outcome record (heuristic, no LLM needed).

    Privacy principle: evidence snippets are truncated to 80 chars and never contain
    raw user goal content verbatim — only anonymized pattern descriptions.
    """
    signals: List[FrictionSignal] = []
    status = outcome.get("status", "")
    summary = outcome.get("summary", "") or ""
    session_id = outcome.get("outcome_id", "") or outcome.get("loop_id", "") or ""
    tokens_in = outcome.get("tokens_in", 0) or 0

    summary_lower = summary.lower()

    # error_events: stuck + LLM/API error mentioned
    if status == "stuck" and any(
        kw in summary_lower for kw in ("llm call failed", "api", "timeout", "connection error", "rate limit")
    ):
        signals.append(FrictionSignal(
            signal_type=SIGNAL_ERROR_EVENTS,
            severity="high",
            count=1,
            evidence=f"stuck+error: {summary[:80]}",
            session_id=session_id,
        ))

    # backtracking: stuck + repeated/same outcome language
    if status == "stuck" and any(
        kw in summary_lower for kw in ("repeated", "same outcome", "already tried", "same result", "loop detected")
    ):
        signals.append(FrictionSignal(
            signal_type=SIGNAL_BACKTRACKING,
            severity="medium",
            count=1,
            evidence=f"stuck+repeated: {summary[:80]}",
            session_id=session_id,
        ))

    # escalation_tone: stuck + "critical" or "failed" appearing N+ times
    if status == "stuck":
        fail_count = summary_lower.count("critical") + summary_lower.count("failed")
        if fail_count >= _ESCALATION_MIN_HITS:
            signals.append(FrictionSignal(
                signal_type=SIGNAL_ESCALATION_TONE,
                severity="medium",
                count=fail_count,
                evidence=f"escalated language ({fail_count}x): {summary[:80]}",
                session_id=session_id,
            ))

    # context_churn: lots of input tokens + stuck = too much context, no progress
    if status == "stuck" and tokens_in > _CONTEXT_CHURN_TOKEN_THRESHOLD:
        signals.append(FrictionSignal(
            signal_type=SIGNAL_CONTEXT_CHURN,
            severity="low",
            count=1,
            evidence=f"stuck with tokens_in={tokens_in}: {summary[:80]}",
            session_id=session_id,
        ))

    # platform_confusion: language about wrong context/environment
    if any(kw in summary_lower for kw in ("wrong platform", "not supported", "platform confusion", "wrong context")):
        signals.append(FrictionSignal(
            signal_type=SIGNAL_PLATFORM_CONFUSION,
            severity="medium",
            count=1,
            evidence=f"platform confusion: {summary[:80]}",
            session_id=session_id,
        ))

    # abandoned_tool_flow: language about incomplete tool chains
    if status == "stuck" and any(
        kw in summary_lower for kw in ("tool call", "abandoned", "incomplete", "tool chain", "mid-way")
    ):
        signals.append(FrictionSignal(
            signal_type=SIGNAL_ABANDONED_TOOL_FLOW,
            severity="low",
            count=1,
            evidence=f"abandoned tool flow: {summary[:80]}",
            session_id=session_id,
        ))

    return signals


# ---------------------------------------------------------------------------
# Goal alignment scoring
# ---------------------------------------------------------------------------

def assess_goal_alignment(goal: str, result_summary: str, adapter=None) -> float:
    """Score how well the result matched the goal, 0.0-1.0.

    If no adapter: return 0.7 (assume moderate alignment — heuristic default).
    With adapter: ask LLM for a numeric score.
    """
    if adapter is None:
        return 0.7

    try:
        prompt = (
            f"Goal: {goal[:200]}\n"
            f"Result: {result_summary[:400]}\n\n"
            "On a scale of 0.0 to 1.0, how well does this result match the stated goal? "
            "Reply ONLY with a number."
        )
        resp = adapter.complete(
            [LLMMessage("user", prompt)],
            max_tokens=16,
            temperature=0.0,
        )
        text = resp.content.strip()
        return float(text)
    except (ValueError, TypeError):
        return 0.5
    except Exception:
        return 0.5


# ---------------------------------------------------------------------------
# Session inspection
# ---------------------------------------------------------------------------

_INSPECTOR_NOTES_SYSTEM = """\
You are a quality inspector for an autonomous AI system. Provide a brief one-sentence
quality assessment of this agent session. Be specific and factual. No fluff.

You MUST end your response with exactly one of these verdicts on its own line:
VERDICT: **PROCEED** — output meets quality bar, no rework needed
VERDICT: **RETRY** — output has fixable issues, rework the last step
VERDICT: **ABORT** — output is fundamentally wrong, escalate to human
No hedging. No "it depends". Commit to a verdict.
"""


def inspect_session(outcome: dict, adapter=None) -> SessionQuality:
    """Inspect a single outcome record and return a SessionQuality assessment.

    Inspector never modifies running loops — read-only analysis of outcomes.
    """
    session_id = outcome.get("outcome_id", outcome.get("loop_id", uuid.uuid4().hex[:8]))
    goal = outcome.get("goal", "")
    project = outcome.get("project", "") or ""
    status = outcome.get("status", "done")
    summary = outcome.get("summary", "") or ""

    # Determine session type from outcome fields
    session_type = "loop" if outcome.get("loop_id") else "mission" if outcome.get("mission_id") else "loop"

    # Detect friction signals (heuristic, no LLM)
    friction_signals = detect_friction_signals(outcome)

    # Assess goal alignment (LLM if available)
    alignment_score = assess_goal_alignment(goal, summary, adapter=adapter)

    # Determine delight signals
    delight_signals: List[str] = []
    if status == "done" and alignment_score >= _ALIGNMENT_GOOD:
        delight_signals.append("task_completed_successfully")

    # Determine overall_quality
    has_high_friction = any(s.severity == "high" for s in friction_signals)
    if alignment_score >= _ALIGNMENT_GOOD and not has_high_friction:
        overall_quality = "good"
    elif alignment_score < _ALIGNMENT_POOR or has_high_friction:
        overall_quality = "poor"
    else:
        overall_quality = "fair"

    # LLM inspector notes (brief, optional)
    inspector_notes = ""
    if adapter is not None:
        try:
            note_prompt = (
                f"Session status: {status}\n"
                f"Goal (truncated): {goal[:100]}\n"
                f"Result (truncated): {summary[:200]}\n"
                f"Friction signals: {[s.signal_type for s in friction_signals]}\n"
                f"Alignment score: {alignment_score:.2f}"
            )
            resp = adapter.complete(
                [
                    LLMMessage("system", _INSPECTOR_NOTES_SYSTEM),
                    LLMMessage("user", note_prompt),
                ],
                max_tokens=128,
                temperature=0.2,
            )
            inspector_notes = resp.content.strip()[:300]
        except Exception:
            inspector_notes = ""

    return SessionQuality(
        session_id=session_id,
        session_type=session_type,
        goal=goal[:80],  # privacy: truncate goal
        project=project,
        status=status,
        goal_alignment_score=alignment_score,
        friction_signals=friction_signals,
        delight_signals=delight_signals,
        overall_quality=overall_quality,
        inspector_notes=inspector_notes,
    )


# ---------------------------------------------------------------------------
# Cross-session pattern analysis
# ---------------------------------------------------------------------------

_PATTERN_SYSTEM = """\
You are a quality inspector for an autonomous AI system.
Analyze these session quality results and identify:
1. Cross-session patterns (what keeps going wrong?)
2. Improvement suggestions (concrete, actionable)
3. Any signals that have crossed a threshold (appearing in >30% of sessions)

Output JSON: {"patterns": [...], "suggestions": [...], "threshold_breaches": [...]}
"""


def _analyze_patterns_with_llm(
    session_qualities: List[SessionQuality],
    signal_counts: Dict[str, int],
    *,
    dry_run: bool = False,
    adapter=None,
) -> tuple[List[str], List[str], List[str]]:
    """Ask LLM to identify cross-session patterns. Returns (patterns, suggestions, threshold_breaches)."""
    if dry_run or adapter is None or not session_qualities:
        return [], [], []

    # Build a concise summary for the LLM
    n = len(session_qualities)
    dist: Dict[str, int] = {"good": 0, "fair": 0, "poor": 0}
    for sq in session_qualities:
        dist[sq.overall_quality] = dist.get(sq.overall_quality, 0) + 1

    summary_lines = [
        f"Total sessions inspected: {n}",
        f"Quality: good={dist['good']} fair={dist['fair']} poor={dist['poor']}",
        f"Signal counts: {json.dumps(signal_counts)}",
        "",
        "Sample poor sessions:",
    ]
    for sq in [s for s in session_qualities if s.overall_quality == "poor"][:5]:
        summary_lines.append(
            f"  - [{sq.status}] alignment={sq.overall_quality} "
            f"friction=[{','.join(s.signal_type for s in sq.friction_signals)}]"
        )

    user_content = "\n".join(summary_lines)

    try:
        resp = adapter.complete(
            [
                LLMMessage("system", _PATTERN_SYSTEM),
                LLMMessage("user", user_content),
            ],
            max_tokens=1024,
            temperature=0.2,
        )
        data = extract_json(content_or_empty(resp), dict, log_tag="inspector.analyze_patterns")
        if data:
            return (
                data.get("patterns", []),
                data.get("suggestions", []),
                data.get("threshold_breaches", []),
            )
    except Exception as e:
        if __debug__:
            print(f"[inspector] LLM pattern analysis failed: {e}", file=sys.stderr)

    return [], [], []


# ---------------------------------------------------------------------------
# Core run
# ---------------------------------------------------------------------------

def run_inspector(
    limit: int = 50,
    adapter=None,
    dry_run: bool = False,
    verbose: bool = True,
) -> InspectionReport:
    """Run one inspection cycle across recent outcomes.

    Inspector is a read-only observer — never modifies running loops.

    Args:
        limit:   Number of recent outcomes to inspect.
        adapter: LLM adapter (optional — heuristics work without one).
        dry_run: Skip LLM calls; return stub data.
        verbose: Print progress to stderr.

    Returns:
        InspectionReport with quality distribution, friction patterns, suggestions.
    """
    run_id = uuid.uuid4().hex[:8]
    started = time.monotonic()

    if verbose:
        print(f"[inspector] run_id={run_id} starting...", file=sys.stderr)

    # Load outcomes
    outcomes_raw: List[Any] = []
    try:
        if load_outcomes is not None:
            from dataclasses import asdict
            outcomes_raw = [asdict(o) for o in load_outcomes(limit=limit)]
    except Exception as e:
        if verbose:
            print(f"[inspector] failed to load outcomes: {e}", file=sys.stderr)

    if not outcomes_raw:
        report = InspectionReport(
            run_id=run_id,
            inspected_sessions=0,
        )
        report.elapsed_ms = int((time.monotonic() - started) * 1000)
        if not dry_run:
            try:
                _save_inspection_report(report)
            except Exception:
                pass
        return report

    if verbose:
        print(f"[inspector] inspecting {len(outcomes_raw)} outcomes...", file=sys.stderr)

    # Inspect each session
    session_qualities: List[SessionQuality] = []
    for outcome in outcomes_raw:
        try:
            sq = inspect_session(outcome, adapter=adapter if not dry_run else None)
            session_qualities.append(sq)
        except Exception as e:
            if verbose:
                print(f"[inspector] session inspect failed: {e}", file=sys.stderr)

    # Aggregate quality distribution
    quality_dist: Dict[str, int] = {"good": 0, "fair": 0, "poor": 0}
    for sq in session_qualities:
        quality_dist[sq.overall_quality] = quality_dist.get(sq.overall_quality, 0) + 1

    # Aggregate friction signals
    signal_counts: Dict[str, int] = {}
    signal_severity_max: Dict[str, str] = {}
    for sq in session_qualities:
        for sig in sq.friction_signals:
            signal_counts[sig.signal_type] = signal_counts.get(sig.signal_type, 0) + sig.count
            # Track max severity
            sev_rank = {"low": 0, "medium": 1, "high": 2}
            existing = signal_severity_max.get(sig.signal_type, "low")
            if sev_rank.get(sig.severity, 0) > sev_rank.get(existing, 0):
                signal_severity_max[sig.signal_type] = sig.severity

    # Top friction signals (by count, descending)
    top_signals = sorted(
        [
            {"signal_type": k, "count": v, "severity": signal_severity_max.get(k, "low")}
            for k, v in signal_counts.items()
        ],
        key=lambda x: x["count"],
        reverse=True,
    )[:5]

    # Average alignment score
    alignment_scores = [sq.goal_alignment_score for sq in session_qualities]
    avg_alignment = sum(alignment_scores) / len(alignment_scores) if alignment_scores else 0.0

    # Heuristic threshold breaches (>30% of sessions have this signal)
    n = len(session_qualities)
    heuristic_breaches: List[str] = []
    if n > 0:
        for sig_type, count in signal_counts.items():
            # count is total across sessions; normalize to per-session fraction
            sessions_with_signal = sum(
                1 for sq in session_qualities
                if any(s.signal_type == sig_type for s in sq.friction_signals)
            )
            if sessions_with_signal / n > _BREACH_THRESHOLD:
                heuristic_breaches.append(sig_type)

    # LLM cross-session pattern analysis
    patterns: List[str] = []
    suggestions: List[str] = []
    llm_breaches: List[str] = []

    if not dry_run and adapter is not None:
        patterns, suggestions, llm_breaches = _analyze_patterns_with_llm(
            session_qualities,
            signal_counts,
            dry_run=dry_run,
            adapter=adapter,
        )

    threshold_breaches = list(set(heuristic_breaches + llm_breaches))

    report = InspectionReport(
        run_id=run_id,
        inspected_sessions=len(session_qualities),
        quality_distribution=quality_dist,
        top_friction_signals=top_signals,
        alignment_score_avg=round(avg_alignment, 3),
        patterns=patterns,
        suggestions=suggestions,
        threshold_breaches=threshold_breaches,
        elapsed_ms=int((time.monotonic() - started) * 1000),
    )

    if verbose:
        print(
            f"[inspector] done: good={quality_dist['good']} fair={quality_dist['fair']} "
            f"poor={quality_dist['poor']} alignment_avg={avg_alignment:.2f}",
            file=sys.stderr,
        )

    # Persist
    if not dry_run:
        try:
            _save_inspection_report(report)
        except Exception as e:
            if verbose:
                print(f"[inspector] failed to save report: {e}", file=sys.stderr)

        # Feed suggestions into evolver pipeline
        if suggestions:
            try:
                _save_inspection_suggestions(suggestions)
            except Exception as e:
                if verbose:
                    print(f"[inspector] failed to save suggestions: {e}", file=sys.stderr)

    report.elapsed_ms = int((time.monotonic() - started) * 1000)
    return report


# ---------------------------------------------------------------------------
# Inspector loop (for systemd: maro-inspector --loop)
# ---------------------------------------------------------------------------

def inspector_loop(
    interval_seconds: float = 3600.0,
    adapter=None,
    verbose: bool = True,
) -> None:
    """Run inspector on a fixed interval forever.

    Designed for systemd: maro-inspector --loop
    Role: quality oversight, separate from heartbeat (health oversight).
    """
    if verbose:
        print(f"[inspector] loop started interval={interval_seconds}s", file=sys.stderr)
    while True:
        try:
            # Build adapter fresh each cycle so credential changes take effect
            _adapter = adapter
            if _adapter is None and build_adapter is not None:
                try:
                    _adapter = build_adapter(model=MODEL_CHEAP)
                except Exception:
                    _adapter = None
            run_inspector(adapter=_adapter, verbose=verbose)
        except Exception as e:
            print(f"[inspector] run failed: {e}", file=sys.stderr)
        time.sleep(interval_seconds)


# ---------------------------------------------------------------------------
# Convenience accessors
# ---------------------------------------------------------------------------

def get_friction_summary() -> str:
    """Return a brief human-readable friction summary from the latest inspection.

    Used by heartbeat tier-2 LLM diagnosis context and get_friction_summary().
    Returns empty string if no inspection has been run yet.
    """
    report = get_latest_inspection()
    if report is None:
        return ""

    if report.inspected_sessions == 0:
        return "Inspector: no sessions inspected yet."

    dist = report.quality_distribution
    lines = [
        f"Inspector ({report.run_id}): {report.inspected_sessions} sessions — "
        f"good={dist.get('good', 0)} fair={dist.get('fair', 0)} poor={dist.get('poor', 0)} "
        f"alignment_avg={report.alignment_score_avg:.2f}"
    ]
    if report.top_friction_signals:
        top = report.top_friction_signals[0]
        lines.append(f"Top friction: {top['signal_type']} (count={top['count']} severity={top['severity']})")
    if report.threshold_breaches:
        lines.append(f"Threshold breaches: {', '.join(report.threshold_breaches)}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI entry point (standalone)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Maro Inspector — quality oversight")
    parser.add_argument("--loop", action="store_true", help="Run forever on an interval (for systemd)")
    parser.add_argument("--interval", type=float, default=3600.0, help="Seconds between runs (default: 3600)")
    parser.add_argument("--limit", type=int, default=50, help="Number of outcomes to inspect (default: 50)")
    parser.add_argument("--dry-run", action="store_true", help="Run without LLM calls or saving results")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    args = parser.parse_args()

    if args.loop:
        inspector_loop(interval_seconds=args.interval)
    else:
        _adapter = None
        if not args.dry_run and build_adapter is not None:
            try:
                _adapter = build_adapter(model=MODEL_CHEAP)
            except Exception:
                pass
        report = run_inspector(limit=args.limit, adapter=_adapter, dry_run=args.dry_run)
        if args.format == "json":
            print(json.dumps(report.to_dict(), indent=2))
        else:
            print(report.summary())
