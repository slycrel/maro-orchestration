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
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("poe.inspector")

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
    from evolver import receive_inspector_tickets
except ImportError:  # pragma: no cover
    receive_inspector_tickets = None  # type: ignore[assignment]

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

# Threshold: signal appearing in this fraction of sessions → threshold breach
_BREACH_THRESHOLD = 0.30

# Escalation keywords for tone detection
_ESCALATION_KEYWORDS = frozenset([
    "broken", "failed", "stuck", "error", "cannot", "impossible",
    "doesn't work", "not working", "won't work", "can't",
])

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


# ---------------------------------------------------------------------------
# Phase 12 spec dataclasses (new in this phase — alongside existing ones above)
# FrictionSignal below uses float severity (spec); existing FrictionSignal uses str.
# They coexist under different usage paths.
# ---------------------------------------------------------------------------

@dataclass
class SpecFrictionSignal:
    """Spec-defined FrictionSignal with float severity (0.0–1.0) and timestamp."""
    session_id: str
    signal_type: str
    severity: float          # 0.0–1.0
    evidence: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "signal_type": self.signal_type,
            "severity": self.severity,
            "evidence": self.evidence,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SpecFrictionSignal":
        return cls(
            session_id=d.get("session_id", ""),
            signal_type=d.get("signal_type", ""),
            severity=float(d.get("severity", 0.5)),
            evidence=d.get("evidence", ""),
            timestamp=d.get("timestamp", datetime.now(timezone.utc).isoformat()),
        )


@dataclass
class AlignmentResult:
    """Goal alignment check result (spec §12)."""
    session_id: str
    mission_goal: str
    work_summary: str
    aligned: bool
    alignment_score: float   # 0.0–1.0
    gaps: List[str]          # specific ways the work diverged from intent
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "mission_goal": self.mission_goal,
            "work_summary": self.work_summary,
            "aligned": self.aligned,
            "alignment_score": self.alignment_score,
            "gaps": self.gaps,
            "timestamp": self.timestamp,
        }


@dataclass
class InspectorReport:
    """Full inspector run report (spec §12 — distinct from InspectionReport above)."""
    report_id: str
    sessions_analyzed: int
    friction_signals: List["SpecFrictionSignal"] = field(default_factory=list)
    alignment_results: List[AlignmentResult] = field(default_factory=list)
    patterns: List[str] = field(default_factory=list)
    evolver_tickets: List[dict] = field(default_factory=list)
    executive_summary: str = ""
    elapsed_ms: int = 0
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def summary(self) -> str:
        lines = [
            f"inspector report_id={self.report_id}",
            f"sessions_analyzed={self.sessions_analyzed}",
            f"friction_signals={len(self.friction_signals)}",
            f"alignment_results={len(self.alignment_results)}",
            f"patterns={len(self.patterns)}",
            f"evolver_tickets={len(self.evolver_tickets)}",
            f"elapsed_ms={self.elapsed_ms}",
        ]
        if self.executive_summary:
            lines.append(f"summary: {self.executive_summary[:200]}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "report_id": self.report_id,
            "sessions_analyzed": self.sessions_analyzed,
            "friction_signals": [s.to_dict() for s in self.friction_signals],
            "alignment_results": [a.to_dict() for a in self.alignment_results],
            "patterns": self.patterns,
            "evolver_tickets": self.evolver_tickets,
            "executive_summary": self.executive_summary,
            "elapsed_ms": self.elapsed_ms,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "InspectorReport":
        return cls(
            report_id=d.get("report_id", ""),
            sessions_analyzed=d.get("sessions_analyzed", 0),
            friction_signals=[SpecFrictionSignal.from_dict(s) for s in d.get("friction_signals", [])],
            alignment_results=[
                AlignmentResult(
                    session_id=a.get("session_id", ""),
                    mission_goal=a.get("mission_goal", ""),
                    work_summary=a.get("work_summary", ""),
                    aligned=a.get("aligned", False),
                    alignment_score=float(a.get("alignment_score", 0.5)),
                    gaps=a.get("gaps", []),
                    timestamp=a.get("timestamp", ""),
                )
                for a in d.get("alignment_results", [])
            ],
            patterns=d.get("patterns", []),
            evolver_tickets=d.get("evolver_tickets", []),
            executive_summary=d.get("executive_summary", ""),
            elapsed_ms=d.get("elapsed_ms", 0),
            timestamp=d.get("timestamp", ""),
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


def _inspector_report_log_path() -> Path:
    """memory/inspector-log.jsonl — for spec InspectorReport objects."""
    from orch_items import memory_dir
    return memory_dir() / "inspector-log.jsonl"


def _friction_signals_log_path() -> Path:
    """memory/friction-signals.jsonl — running friction signal log."""
    from orch_items import memory_dir
    return memory_dir() / "friction-signals.jsonl"


def _suggestions_path() -> Path:
    """Path to suggestions.jsonl — shared with evolver."""
    from orch_items import memory_dir
    return memory_dir() / "suggestions.jsonl"


# ---------------------------------------------------------------------------
# Phase 12 spec functions — detect_friction, check_alignment, cluster_patterns,
# generate_tickets, run_inspector (with spec signature), format_inspector_report
# ---------------------------------------------------------------------------

def detect_friction(
    outcomes: List[Any],
    hook_results_path: Optional[str] = None,
) -> List["SpecFrictionSignal"]:
    """Analyze outcome records for friction signals (spec §12, 7-signal model).

    Handles both dict outcomes and Outcome dataclass objects.
    Returns one SpecFrictionSignal per detected instance.

    Args:
        outcomes:          List of outcome dicts or Outcome objects.
        hook_results_path: (unused, reserved for future hook log analysis)

    Returns:
        List of SpecFrictionSignal.
    """
    signals: List[SpecFrictionSignal] = []

    def _to_dict(o: Any) -> dict:
        if isinstance(o, dict):
            return o
        try:
            from dataclasses import asdict
            return asdict(o)
        except Exception:
            return o.__dict__ if hasattr(o, "__dict__") else {}

    items = [_to_dict(o) for o in outcomes]
    n = max(len(items), 1)

    # --- error_events: status == "stuck" or "error" ---
    stuck_items = [o for o in items if o.get("status") in ("stuck", "error")]
    if stuck_items:
        severity = min(1.0, len(stuck_items) / n)
        for o in stuck_items:
            sid = o.get("outcome_id") or o.get("loop_id") or o.get("session_id", "?")
            # Phase 14: add attribution context to evidence
            evidence = f"status={o.get('status')} goal={str(o.get('goal', ''))[:50]}"
            if attribute_failure is not None:
                try:
                    attr = attribute_failure(o)
                    attr_ctx = (
                        f" | failed_step={attr.failed_step[:40]}"
                        f" failure_mode={attr.failure_mode}"
                    )
                    evidence = (evidence + attr_ctx)[:200]
                except Exception:
                    pass
            signals.append(SpecFrictionSignal(
                session_id=sid,
                signal_type=SIGNAL_ERROR_EVENTS,
                severity=severity,
                evidence=evidence,
            ))

    # --- escalation_tone: scan summary / stuck_reason for keywords ---
    for o in items:
        text = " ".join([
            str(o.get("summary", "")),
            str(o.get("stuck_reason", "")),
            str(o.get("result_summary", "")),
        ]).lower()
        found = [kw for kw in _ESCALATION_KEYWORDS if kw in text]
        if found:
            sid = o.get("outcome_id") or o.get("session_id", "?")
            signals.append(SpecFrictionSignal(
                session_id=sid,
                signal_type=SIGNAL_ESCALATION_TONE,
                severity=min(1.0, len(found) * 0.2),
                evidence=f"keywords={found[:3]} in outcome text",
            ))

    # --- abandoned_tool_flow: blocked/stuck with empty or very short result ---
    for o in items:
        if o.get("status") in ("blocked", "stuck"):
            result = str(o.get("result_summary") or o.get("summary") or "")
            if len(result.strip()) < 20:
                sid = o.get("outcome_id") or o.get("session_id", "?")
                signals.append(SpecFrictionSignal(
                    session_id=sid,
                    signal_type=SIGNAL_ABANDONED_TOOL_FLOW,
                    severity=0.7,
                    evidence=f"blocked/stuck with short result ({len(result.strip())} chars)",
                ))

    # --- repeated_rephrasing: same goal slug stuck 3+ times ---
    import re
    from collections import Counter

    def _goal_slug(goal: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", goal.lower().strip())[:40]

    slug_stuck: Dict[str, int] = {}
    for o in items:
        if o.get("status") in ("stuck", "error"):
            slug = _goal_slug(str(o.get("goal", "")))
            slug_stuck[slug] = slug_stuck.get(slug, 0) + 1

    for slug, count in slug_stuck.items():
        if count >= 3:
            signals.append(SpecFrictionSignal(
                session_id="multiple",
                signal_type=SIGNAL_REPEATED_REPHRASE,
                severity=min(1.0, count * 0.2),
                evidence=f"goal slug '{slug}' stuck {count} times",
            ))

    # --- backtracking: done outcome followed by stuck in same project ---
    project_statuses: Dict[str, List[str]] = {}
    for o in items:
        proj = str(o.get("project") or "unknown")
        project_statuses.setdefault(proj, []).append(o.get("status", ""))

    for proj, statuses in project_statuses.items():
        found_done = False
        for s in statuses:
            if s == "done":
                found_done = True
            elif found_done and s in ("stuck", "blocked", "error"):
                signals.append(SpecFrictionSignal(
                    session_id=proj,
                    signal_type=SIGNAL_BACKTRACKING,
                    severity=0.6,
                    evidence=f"project '{proj}' had done then stuck/blocked",
                ))
                break

    # --- context_churn: lessons loaded but outcome still stuck ---
    for o in items:
        if o.get("status") in ("stuck", "error"):
            lessons = o.get("lessons") or o.get("lessons_context") or []
            if isinstance(lessons, list) and len(lessons) > 0:
                sid = o.get("outcome_id") or o.get("session_id", "?")
                signals.append(SpecFrictionSignal(
                    session_id=sid,
                    signal_type=SIGNAL_CONTEXT_CHURN,
                    severity=0.5,
                    evidence=f"lessons loaded ({len(lessons)}) but outcome stuck",
                ))

    return signals


_ALIGNMENT_SYSTEM = """\
You are an impartial quality reviewer. Given a stated goal and work summary,
determine whether the work accomplished the goal.

Respond ONLY with valid JSON:
{"aligned": true or false, "score": 0.0 to 1.0, "gaps": ["gap1", "gap2"]}

Score guide: 0.9+ = fully aligned, 0.7 = mostly, 0.5 = partial, 0.3 = minimal, 0.0 = not aligned.
gaps: specific ways work diverged from intent. Empty list if fully aligned.
"""

# ---------------------------------------------------------------------------
# Phase 19: Inspector Skepticism Calibration — few-shot examples
# ---------------------------------------------------------------------------

SKEPTICISM_EXAMPLES = [
    {
        "work_summary": "Completed the research task. Found some information about the topic.",
        "goal": "Research winning Polymarket trading strategies with citations",
        "label": "MEDIOCRE",
        "reason": "Vague output. No citations. 'Some information' is not a deliverable.",
    },
    {
        "work_summary": "Error: LLM call failed. Unable to complete step.",
        "goal": "Build a data pipeline",
        "label": "FAILED",
        "reason": "Technical failure with no recovery attempted.",
    },
    {
        "work_summary": (
            "Analyzed 12 wallets, identified 3 profitable patterns: momentum trading (avg +23%), "
            "arbitrage (avg +18%), liquidity provision (avg +12%). "
            "Full breakdown in artifacts/research-findings.md."
        ),
        "goal": "Research winning Polymarket trading strategies with citations",
        "label": "GOOD",
        "reason": "Specific, quantified, cites artifacts, directly addresses the goal.",
    },
]


def _build_skeptic_prompt_prefix() -> str:
    """Build few-shot example prefix for Inspector prompts."""
    lines = [
        "Before you evaluate, here are calibration examples (MEDIOCRE/FAILED/GOOD):",
        "",
    ]
    for ex in SKEPTICISM_EXAMPLES:
        lines.append(f"Example [{ex['label']}]:")
        lines.append(f"  Goal: {ex['goal']}")
        lines.append(f"  Work: {ex['work_summary'][:120]}")
        lines.append(f"  Verdict: {ex['label']} — {ex['reason']}")
        lines.append("")
    lines.append("Now evaluate the actual session below with the same skepticism:")
    lines.append("")
    return "\n".join(lines)


def check_alignment(session: Any, adapter=None) -> AlignmentResult:
    """Check whether completed work accomplished the stated goal.

    Args:
        session: Outcome record (dict or Outcome dataclass).
        adapter: LLMAdapter. None → heuristic (status-based scoring).

    Returns:
        AlignmentResult with score and gaps list.
    """
    if not isinstance(session, dict):
        try:
            from dataclasses import asdict
            session = asdict(session)
        except Exception:
            session = session.__dict__ if hasattr(session, "__dict__") else {}

    session_id = session.get("outcome_id") or session.get("loop_id") or session.get("id", "?")
    goal = session.get("goal", "")
    summary = session.get("summary") or session.get("result_summary") or ""
    status = session.get("status", "")

    # Heuristic (no adapter)
    if adapter is None:
        if status == "done":
            return AlignmentResult(
                session_id=session_id, mission_goal=goal, work_summary=summary,
                aligned=True, alignment_score=0.8, gaps=[],
            )
        elif status in ("stuck", "error", "blocked"):
            return AlignmentResult(
                session_id=session_id, mission_goal=goal, work_summary=summary,
                aligned=False, alignment_score=0.3,
                gaps=[f"Task did not complete: status={status}"],
            )
        else:
            return AlignmentResult(
                session_id=session_id, mission_goal=goal, work_summary=summary,
                aligned=False, alignment_score=0.5,
                gaps=[f"Unclear completion: status={status}"],
            )

    # LLM-as-judge (MODEL_MID per spec) with Phase 19 skepticism calibration
    # Prepend few-shot examples so Inspector is calibrated against "confidently mediocre" output
    _skeptic_prefix = _build_skeptic_prompt_prefix()
    user_msg = (
        _skeptic_prefix
        + f"Goal: {goal}\n\nWork summary: {summary[:600]}\n\n"
        "Did the work accomplish the goal? Return JSON only."
    )
    try:
        resp = adapter.complete(
            [
                LLMMessage("system", _ALIGNMENT_SYSTEM),
                LLMMessage("user", user_msg),
            ],
            max_tokens=512,
            temperature=0.1,
        )
        content = resp.content.strip()
        start = content.find("{")
        end = content.rfind("}") + 1
        if start >= 0 and end > start:
            data = json.loads(content[start:end])
            score = float(data.get("score", 0.5))
            aligned = bool(data.get("aligned", score >= 0.6))
            gaps = [str(g) for g in data.get("gaps", [])]
            return AlignmentResult(
                session_id=session_id, mission_goal=goal, work_summary=summary,
                aligned=aligned, alignment_score=score, gaps=gaps,
            )
    except Exception as e:
        if __debug__:
            print(f"[inspector] check_alignment LLM call failed: {e}", file=sys.stderr)

    # LLM failed — heuristic fallback
    return check_alignment(session, adapter=None)


_CLUSTER_SYSTEM = """\
You are analyzing friction signals from an autonomous AI system.
Identify 1-3 named patterns that explain the most common problems.
Respond ONLY with a JSON array of strings:
["Pattern name: brief description", "Pattern name 2: ..."]
If there are no clear patterns return: []
"""


def cluster_patterns(
    signals: List["SpecFrictionSignal"],
    adapter=None,
) -> List[str]:
    """Cluster friction signals into named patterns (spec §12).

    Args:
        signals: List of SpecFrictionSignal.
        adapter: LLMAdapter (cheap model). None → group by signal_type.

    Returns:
        List of human-readable pattern descriptions (1–3 items).
    """
    if not signals:
        return []

    # Fallback: group by signal_type
    if adapter is None:
        from collections import Counter
        type_counts = Counter(s.signal_type for s in signals)
        patterns = []
        for sig_type, count in type_counts.most_common(3):
            desc = FRICTION_TYPES.get(sig_type, sig_type)
            patterns.append(f"{sig_type}: {desc} ({count} occurrences)")
        return patterns

    signal_summary = "\n".join(
        f"- [{s.signal_type}] severity={s.severity:.2f} evidence={s.evidence[:70]}"
        for s in signals[:30]
    )
    try:
        resp = adapter.complete(
            [
                LLMMessage("system", _CLUSTER_SYSTEM),
                LLMMessage("user", f"Signals:\n{signal_summary}\n\nIdentify 1-3 patterns."),
            ],
            max_tokens=512,
            temperature=0.2,
        )
        content = resp.content.strip()
        start = content.find("[")
        end = content.rfind("]") + 1
        if start >= 0 and end > start:
            result = json.loads(content[start:end])
            if isinstance(result, list) and all(isinstance(p, str) for p in result):
                return [p.strip() for p in result if p.strip()][:3]
    except Exception as e:
        if __debug__:
            print(f"[inspector] cluster_patterns LLM call failed: {e}", file=sys.stderr)

    # LLM failed — fallback
    return cluster_patterns(signals, adapter=None)


_TICKET_SYSTEM = """\
You are generating improvement tickets for an autonomous AI system.
For each friction pattern, create one structured improvement ticket.
Respond ONLY with a JSON array:
[{"title": "...", "pattern": "...", "suggested_fix": "...", "priority": "high|medium|low"}]
"""


def generate_tickets(
    patterns: List[str],
    signals: List["SpecFrictionSignal"],
    adapter=None,
    attribution_report=None,
) -> List[dict]:
    """Generate improvement tickets from friction patterns (spec §12).

    Returns list of ticket dicts: {id, title, pattern, suggested_fix, priority, auto_evolver}
    auto_evolver=True means forward to evolver.

    Phase 14: when attribution_report is provided and has most_blamed_skills,
    include per-skill failure breakdown in ticket descriptions.
    """
    if not patterns:
        return []

    from collections import Counter
    type_sev: Dict[str, List[float]] = {}
    for s in signals:
        type_sev.setdefault(s.signal_type, []).append(s.severity)
    avg_sev = {t: sum(v) / len(v) for t, v in type_sev.items()}
    max_sev = max(avg_sev.values()) if avg_sev else 0.5

    def _prio(idx: int) -> str:
        if max_sev >= 0.7 or idx == 0:
            return "high"
        if max_sev >= 0.4:
            return "medium"
        return "low"

    # Phase 14: build skill blame suffix from attribution report
    skill_blame_suffix = ""
    if attribution_report is not None:
        blamed = getattr(attribution_report, "most_blamed_skills", [])
        if blamed:
            skill_blame_suffix = f" Most blamed skills: {', '.join(blamed[:3])}."

    # Fallback: generic ticket per pattern
    if adapter is None:
        tickets = []
        for i, pat in enumerate(patterns):
            priority = _prio(i)
            tickets.append({
                "id": f"insp-{uuid.uuid4().hex[:6]}",
                "title": f"Address: {pat[:60]}",
                "pattern": pat,
                "suggested_fix": (
                    "Review recent stuck outcomes and identify systemic improvements "
                    "to prompts, tools, or error recovery paths."
                    + skill_blame_suffix
                ),
                "priority": priority,
                "auto_evolver": priority in ("high", "medium"),
            })
        return tickets

    patterns_text = "\n".join(f"{i + 1}. {p}" for i, p in enumerate(patterns))
    try:
        resp = adapter.complete(
            [
                LLMMessage("system", _TICKET_SYSTEM),
                LLMMessage("user", f"Patterns:\n{patterns_text}\n\nGenerate tickets."),
            ],
            max_tokens=1024,
            temperature=0.2,
        )
        content = resp.content.strip()
        start = content.find("[")
        end = content.rfind("]") + 1
        if start >= 0 and end > start:
            raw = json.loads(content[start:end])
            tickets = []
            for i, t in enumerate(raw):
                if not isinstance(t, dict):
                    continue
                priority = t.get("priority", _prio(i))
                tickets.append({
                    "id": f"insp-{uuid.uuid4().hex[:6]}",
                    "title": str(t.get("title", f"Fix pattern {i + 1}")),
                    "pattern": str(t.get("pattern", patterns[i] if i < len(patterns) else "")),
                    "suggested_fix": str(t.get("suggested_fix", "")),
                    "priority": priority,
                    "auto_evolver": priority in ("high", "medium"),
                })
            return tickets
    except Exception as e:
        if __debug__:
            print(f"[inspector] generate_tickets LLM call failed: {e}", file=sys.stderr)

    # LLM failed — fallback
    return generate_tickets(patterns, signals, adapter=None)


def format_inspector_report(report: "InspectorReport") -> str:
    """Format an InspectorReport as human-readable text for CLI output."""
    lines = [
        f"=== Inspector Report {report.report_id} ===",
        f"Timestamp:          {report.timestamp[:19]}",
        f"Sessions analyzed:  {report.sessions_analyzed}",
        f"Friction signals:   {len(report.friction_signals)}",
        f"Alignment results:  {len(report.alignment_results)}",
        f"Patterns:           {len(report.patterns)}",
        f"Evolver tickets:    {len(report.evolver_tickets)}",
        f"Elapsed ms:         {report.elapsed_ms}",
        "",
    ]
    if report.executive_summary:
        lines += ["Summary:", f"  {report.executive_summary}", ""]
    if report.patterns:
        lines.append("Patterns:")
        for p in report.patterns:
            lines.append(f"  - {p}")
        lines.append("")
    if report.friction_signals:
        lines.append(f"Friction Signals ({len(report.friction_signals)}):")
        for s in report.friction_signals[:10]:
            lines.append(
                f"  [{s.signal_type:22s}] sev={s.severity:.2f} {s.evidence[:70]}"
            )
        if len(report.friction_signals) > 10:
            lines.append(f"  ... and {len(report.friction_signals) - 10} more")
        lines.append("")
    if report.alignment_results:
        aligned_n = sum(1 for a in report.alignment_results if a.aligned)
        lines.append(f"Alignment ({aligned_n}/{len(report.alignment_results)} on-target):")
        for a in report.alignment_results[:5]:
            flag = "OK" if a.aligned else "MISS"
            lines.append(
                f"  [{flag}] score={a.alignment_score:.2f} goal={a.mission_goal[:50]!r}"
            )
        lines.append("")
    if report.evolver_tickets:
        lines.append(f"Evolver Tickets ({len(report.evolver_tickets)}):")
        for t in report.evolver_tickets:
            auto = "[auto]" if t.get("auto_evolver") else "      "
            lines.append(
                f"  {auto} [{t.get('priority', '?'):6s}] {t.get('title', '')[:60]}"
            )
    return "\n".join(lines)


def _build_spec_executive_summary(
    sessions_analyzed: int,
    signals: List["SpecFrictionSignal"],
    alignment_results: List[AlignmentResult],
    patterns: List[str],
    tickets: List[dict],
    adapter=None,
    dry_run: bool = False,
) -> str:
    """Build 2-4 sentence executive summary (spec §12)."""
    n_signals = len(signals)
    n_aligned = sum(1 for a in alignment_results if a.aligned)
    n_total = len(alignment_results)
    n_tickets = len(tickets)

    if dry_run or adapter is None:
        if n_signals == 0:
            return (
                f"Inspector analyzed {sessions_analyzed} sessions. "
                "No friction signals detected — system appears healthy."
            )
        return (
            f"Inspector analyzed {sessions_analyzed} sessions. "
            f"Found {n_signals} friction signal(s) across {len(patterns)} pattern(s). "
            f"Alignment: {n_aligned}/{n_total} sessions on-target. "
            f"Generated {n_tickets} improvement ticket(s)."
        )

    _EXEC_SYS = (
        "You are writing a brief executive quality summary for Poe's autonomous system. "
        "2-4 sentences max. Lead with the most important finding."
    )
    data_text = (
        f"Sessions: {sessions_analyzed}\n"
        f"Friction signals: {n_signals}\n"
        f"Patterns: {patterns[:3]}\n"
        f"Alignment: {n_aligned}/{n_total}\n"
        f"Tickets: {n_tickets}"
    )
    try:
        resp = adapter.complete(
            [
                LLMMessage("system", _EXEC_SYS),
                LLMMessage("user", f"Quality data:\n{data_text}\n\nWrite executive summary."),
            ],
            max_tokens=256,
            temperature=0.3,
        )
        return resp.content.strip()
    except Exception:
        pass

    return _build_spec_executive_summary(
        sessions_analyzed, signals, alignment_results, patterns, tickets,
        adapter=None, dry_run=True,
    )


def run_full_inspector(
    *,
    adapter=None,
    dry_run: bool = False,
    min_sessions: int = 5,
    notify_telegram: bool = False,
    limit: int = 50,
    verbose: bool = False,
) -> "InspectorReport":
    """Run one quality inspection cycle returning InspectorReport (spec §12).

    This is the spec-defined run_inspector function with the spec signature.
    The existing run_inspector() function is preserved for backward compatibility.

    Args:
        adapter:          LLMAdapter. None → heuristics only.
        dry_run:          No LLM, no Telegram, no persistence.
        min_sessions:     Skip if fewer sessions available.
        notify_telegram:  Send Telegram summary if signals found.
        limit:            Load at most this many recent outcomes.
        verbose:          Print progress to stderr.

    Returns:
        InspectorReport
    """
    report_id = uuid.uuid4().hex[:8]
    started = time.monotonic()
    log.info("inspector_start id=%s min_sessions=%d dry_run=%s", report_id, min_sessions, dry_run)

    if verbose:
        print(f"[inspector] full_inspector report_id={report_id} starting...", file=sys.stderr)

    # Load outcomes
    try:
        if load_outcomes is None:
            raise ImportError("memory.load_outcomes not available")
        outcomes_raw = load_outcomes(limit=limit)
    except Exception as e:
        if verbose:
            print(f"[inspector] failed to load outcomes: {e}", file=sys.stderr)
        return InspectorReport(
            report_id=report_id,
            sessions_analyzed=0,
            executive_summary=f"Inspection skipped: could not load outcomes ({e})",
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )

    def _to_dict(o: Any) -> dict:
        if isinstance(o, dict):
            return o
        try:
            from dataclasses import asdict
            return asdict(o)
        except Exception:
            return o.__dict__ if hasattr(o, "__dict__") else {}

    outcomes = [_to_dict(o) for o in outcomes_raw]

    if len(outcomes) < min_sessions:
        if verbose:
            print(
                f"[inspector] only {len(outcomes)} sessions (need {min_sessions}), skipping",
                file=sys.stderr,
            )
        return InspectorReport(
            report_id=report_id,
            sessions_analyzed=len(outcomes),
            executive_summary=(
                f"Inspection skipped: only {len(outcomes)} sessions "
                f"(minimum {min_sessions} required)."
            ),
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )

    _adapter = None if dry_run else adapter

    # 1. Detect friction
    signals = detect_friction(outcomes)

    # 2. Check alignment on last 10
    alignment_results: List[AlignmentResult] = []
    for sess in outcomes[:10]:
        try:
            alignment_results.append(check_alignment(sess, adapter=_adapter))
        except Exception:
            pass

    # 3. Cluster patterns
    patterns = cluster_patterns(signals, adapter=_adapter)

    # 4. Generate tickets
    tickets = generate_tickets(patterns, signals, adapter=_adapter)

    # 5. Executive summary
    executive_summary = _build_spec_executive_summary(
        len(outcomes), signals, alignment_results, patterns, tickets,
        adapter=_adapter, dry_run=dry_run,
    )

    report = InspectorReport(
        report_id=report_id,
        sessions_analyzed=len(outcomes),
        friction_signals=signals,
        alignment_results=alignment_results,
        patterns=patterns,
        evolver_tickets=tickets,
        executive_summary=executive_summary,
        elapsed_ms=int((time.monotonic() - started) * 1000),
    )

    # 6. Forward auto_evolver tickets to evolver
    if not dry_run:
        auto_tickets = [t for t in tickets if t.get("auto_evolver")]
        if auto_tickets:
            try:
                n = receive_inspector_tickets(auto_tickets) if receive_inspector_tickets else 0
                if verbose:
                    print(f"[inspector] forwarded {n} tickets to evolver", file=sys.stderr)
            except Exception as e:
                if verbose:
                    print(f"[inspector] could not forward tickets: {e}", file=sys.stderr)

    # 7. Persist
    if not dry_run:
        try:
            p = _inspector_report_log_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a", encoding="utf-8") as f:
                f.write(json.dumps(report.to_dict()) + "\n")
        except Exception as e:
            if verbose:
                print(f"[inspector] failed to persist report: {e}", file=sys.stderr)

        if signals:
            try:
                sp = _friction_signals_log_path()
                sp.parent.mkdir(parents=True, exist_ok=True)
                with sp.open("a", encoding="utf-8") as f:
                    for s in signals:
                        f.write(json.dumps(s.to_dict()) + "\n")
            except Exception as e:
                if verbose:
                    print(f"[inspector] failed to persist signals: {e}", file=sys.stderr)

    # 8. Telegram
    if not dry_run and notify_telegram and signals:
        try:
            from telegram_listener import TelegramBot, _resolve_token, _resolve_allowed_chats
            token = _resolve_token()
            if token:
                bot = TelegramBot(token)
                allowed = _resolve_allowed_chats()
                lines = [
                    f"Inspector Report — {len(signals)} friction signal(s)",
                    f"Sessions: {len(outcomes)}",
                ]
                for pat in patterns[:2]:
                    lines.append(f"Pattern: {pat[:80]}")
                if executive_summary:
                    lines.append(f"Summary: {executive_summary[:200]}")
                msg = "\n".join(lines)
                for chat_id in (allowed or []):
                    bot.send_message(chat_id, msg)
        except Exception as e:
            print(f"[inspector] telegram notify failed: {e}", file=sys.stderr)

    report.elapsed_ms = int((time.monotonic() - started) * 1000)
    log.info("inspector_done id=%s sessions=%d signals=%d tickets=%d elapsed=%dms",
             report_id, len(outcomes_raw), len(signals), len(tickets), report.elapsed_ms)

    if verbose:
        print(
            f"[inspector] done report_id={report_id} elapsed_ms={report.elapsed_ms} "
            f"signals={len(signals)} tickets={len(tickets)}",
            file=sys.stderr,
        )

    return report


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

    # escalation_tone: stuck + "critical" or "failed" appearing 3+ times
    if status == "stuck":
        fail_count = summary_lower.count("critical") + summary_lower.count("failed")
        if fail_count >= 3:
            signals.append(FrictionSignal(
                signal_type=SIGNAL_ESCALATION_TONE,
                severity="medium",
                count=fail_count,
                evidence=f"escalated language ({fail_count}x): {summary[:80]}",
                session_id=session_id,
            ))

    # context_churn: lots of input tokens + stuck = too much context, no progress
    if status == "stuck" and tokens_in > 10000:
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
    if status == "done" and alignment_score >= 0.7:
        delight_signals.append("task_completed_successfully")

    # Determine overall_quality
    has_high_friction = any(s.severity == "high" for s in friction_signals)
    if alignment_score >= 0.7 and not has_high_friction:
        overall_quality = "good"
    elif alignment_score < 0.4 or has_high_friction:
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
        content = resp.content.strip()
        start = content.find("{")
        end = content.rfind("}") + 1
        if start >= 0 and end > start:
            data = json.loads(content[start:end])
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
# Inspector loop (for systemd: poe-inspector --loop)
# ---------------------------------------------------------------------------

def inspector_loop(
    interval_seconds: float = 3600.0,
    adapter=None,
    verbose: bool = True,
) -> None:
    """Run inspector on a fixed interval forever.

    Designed for systemd: poe-inspector --loop
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

    parser = argparse.ArgumentParser(description="Poe Inspector — quality oversight")
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
