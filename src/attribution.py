"""Phase 14: Failure Attribution — pinpoint which step/skill caused a stuck session.

When a session gets stuck, this module identifies the specific step/skill responsible
rather than just recording "task failed". Feeds structured attribution into Inspector
and the meta-evolver for precise improvement signals.

Inspired by Memento-Skills (arXiv:2603.18743) SRDP failure attribution mechanism.

Usage:
    from attribution import attribute_failure, attribute_batch, save_attribution
    attr = attribute_failure(outcome_dict)
    report = attribute_batch(outcomes_list)
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

# Module-level imports for clean test patching
try:
    from llm import MODEL_CHEAP, LLMMessage
except ImportError:  # pragma: no cover
    MODEL_CHEAP = "cheap"  # type: ignore[assignment]
    LLMMessage = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Attribution:
    session_id: str             # loop_id / outcome_id from outcomes.jsonl
    goal: str
    failed_step: str            # which step text most likely caused the failure
    failed_skill: Optional[str] # matched skill name if known
    failure_mode: str           # "tool_failure" | "bad_output" | "stuck_loop" | "llm_error" | "unknown"
    contributing_factors: List[str]  # other steps that may have set up the failure
    confidence: float           # 0.0–1.0
    raw_reason: str             # the stuck_reason from the outcome
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "goal": self.goal,
            "failed_step": self.failed_step,
            "failed_skill": self.failed_skill,
            "failure_mode": self.failure_mode,
            "contributing_factors": self.contributing_factors,
            "confidence": self.confidence,
            "raw_reason": self.raw_reason,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Attribution":
        return cls(
            session_id=d.get("session_id", ""),
            goal=d.get("goal", ""),
            failed_step=d.get("failed_step", ""),
            failed_skill=d.get("failed_skill"),
            failure_mode=d.get("failure_mode", "unknown"),
            contributing_factors=d.get("contributing_factors", []),
            confidence=float(d.get("confidence", 0.4)),
            raw_reason=d.get("raw_reason", ""),
            timestamp=d.get("timestamp", datetime.now(timezone.utc).isoformat()),
        )


@dataclass
class AttributionReport:
    attributions: List[Attribution]
    most_common_failure_modes: List[str]   # sorted by frequency
    most_blamed_skills: List[str]          # skill names that appear most
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return {
            "attributions": [a.to_dict() for a in self.attributions],
            "most_common_failure_modes": self.most_common_failure_modes,
            "most_blamed_skills": self.most_blamed_skills,
            "timestamp": self.timestamp,
        }

    def summary(self) -> str:
        lines = [
            f"AttributionReport — {len(self.attributions)} attributions",
            f"  timestamp: {self.timestamp[:19]}",
        ]
        if self.most_common_failure_modes:
            lines.append(f"  most_common_failure_modes: {', '.join(self.most_common_failure_modes[:3])}")
        if self.most_blamed_skills:
            lines.append(f"  most_blamed_skills: {', '.join(self.most_blamed_skills[:3])}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------

def _attributions_path() -> Path:
    try:
        from orch import orch_root
        d = orch_root() / "memory"
        d.mkdir(parents=True, exist_ok=True)
        return d / "attributions.jsonl"
    except Exception:
        d = Path.cwd() / "memory"
        d.mkdir(parents=True, exist_ok=True)
        return d / "attributions.jsonl"


def save_attribution(attr: Attribution) -> None:
    """Append an Attribution record to memory/attributions.jsonl."""
    path = _attributions_path()
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(attr.to_dict()) + "\n")


def load_attributions(limit: int = 50) -> List[Attribution]:
    """Load recent attributions from memory/attributions.jsonl, newest first."""
    path = _attributions_path()
    if not path.exists():
        return []
    attributions: List[Attribution] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                attributions.append(Attribution.from_dict(d))
                if len(attributions) >= limit:
                    break
            except Exception:
                continue
    except Exception:
        pass
    return attributions


# ---------------------------------------------------------------------------
# LLM attribution prompt
# ---------------------------------------------------------------------------

_ATTRIBUTION_SYSTEM = """\
You are analyzing a stuck AI agent session. Given the goal and failure reason,
identify the most likely root cause.

Goal: {goal}
Stuck reason: {stuck_reason}
Steps attempted: {steps_summary}

Return ONLY JSON (no markdown fences):
{{
  "failed_step": "which step text failed",
  "failure_mode": "tool_failure|bad_output|stuck_loop|llm_error|unknown",
  "contributing_factors": ["factor1"],
  "confidence": 0.0
}}"""


# ---------------------------------------------------------------------------
# Heuristic failure mode detection
# ---------------------------------------------------------------------------

_LLM_ERROR_KEYWORDS = frozenset([
    "llm", "api", "token", "rate limit", "timeout", "connection",
    "model", "completion", "anthropic", "openai", "openrouter",
])
_TOOL_FAILURE_KEYWORDS = frozenset([
    "tool", "function", "call failed", "command", "subprocess", "script",
    "permission", "file not found", "import error", "module",
])
_BAD_OUTPUT_KEYWORDS = frozenset([
    "invalid", "parse", "json", "format", "unexpected", "garbage",
    "malformed", "cannot parse", "bad output",
])
_STUCK_LOOP_KEYWORDS = frozenset([
    "repeated", "same", "loop", "cycle", "retried", "again", "stuck",
    "no progress", "not advancing",
])


def _heuristic_failure_mode(stuck_reason: str) -> tuple[str, float]:
    """Return (failure_mode, confidence) based on keyword analysis."""
    text = stuck_reason.lower()

    matches: dict[str, int] = {
        "llm_error": sum(1 for kw in _LLM_ERROR_KEYWORDS if kw in text),
        "tool_failure": sum(1 for kw in _TOOL_FAILURE_KEYWORDS if kw in text),
        "bad_output": sum(1 for kw in _BAD_OUTPUT_KEYWORDS if kw in text),
        "stuck_loop": sum(1 for kw in _STUCK_LOOP_KEYWORDS if kw in text),
    }

    best = max(matches, key=lambda k: matches[k])
    if matches[best] == 0:
        return "unknown", 0.3

    # Confidence proportional to match density, capped at 0.65
    confidence = min(0.65, 0.35 + matches[best] * 0.1)
    return best, confidence


def _heuristic_failed_step(outcome: dict) -> str:
    """Extract a best-guess failed step from outcome data."""
    # Try to find step references in stuck_reason
    stuck_reason = str(outcome.get("stuck_reason", ""))
    goal = str(outcome.get("goal", ""))

    # Look for "step N" pattern
    step_match = re.search(r"step\s+(\d+)[:\s]([^.]+)", stuck_reason, re.IGNORECASE)
    if step_match:
        return f"Step {step_match.group(1)}: {step_match.group(2).strip()[:80]}"

    # Fall back to goal summary
    if stuck_reason.strip():
        return stuck_reason[:80]
    return f"(unknown step in goal: {goal[:50]})"


def _extract_steps_summary(outcome: dict) -> str:
    """Build a short summary of steps attempted from outcome data."""
    steps = outcome.get("steps", [])
    if isinstance(steps, list) and steps:
        parts = []
        for i, s in enumerate(steps[:10]):
            if isinstance(s, dict):
                text = s.get("text", s.get("step", ""))
                status = s.get("status", "")
                parts.append(f"{i + 1}. [{status}] {str(text)[:60]}")
            elif isinstance(s, str):
                parts.append(f"{i + 1}. {s[:60]}")
        return "\n".join(parts)
    # Fall back to summary/result_summary
    summary = outcome.get("summary") or outcome.get("result_summary") or ""
    return summary[:300] if summary else "(no steps recorded)"


# ---------------------------------------------------------------------------
# Core attribution functions
# ---------------------------------------------------------------------------

def attribute_failure(outcome: dict, adapter=None) -> Attribution:
    """Attribute a single stuck/failed outcome to a specific step/skill.

    Args:
        outcome: Outcome dict (from outcomes.jsonl). Expected keys: goal,
                 status, stuck_reason, steps (optional).
        adapter:  LLMAdapter for richer attribution. None → heuristic fallback.

    Returns:
        Attribution dataclass.
    """
    session_id = (
        str(outcome.get("outcome_id", ""))
        or str(outcome.get("loop_id", ""))
        or str(outcome.get("session_id", "?"))
    )
    goal = str(outcome.get("goal", ""))
    stuck_reason = str(outcome.get("stuck_reason", ""))
    steps_summary = _extract_steps_summary(outcome)

    # Attempt LLM attribution
    if adapter is not None:
        try:
            prompt = _ATTRIBUTION_SYSTEM.format(
                goal=goal[:300],
                stuck_reason=stuck_reason[:500],
                steps_summary=steps_summary[:500],
            )
            if LLMMessage is not None:
                resp = adapter.complete(
                    [LLMMessage("user", prompt)],
                    max_tokens=512,
                    temperature=0.1,
                )
            else:
                raise RuntimeError("LLMMessage not available")
            content = resp.content.strip()
            start = content.find("{")
            end = content.rfind("}") + 1
            if start >= 0 and end > start:
                data = json.loads(content[start:end])
                failed_step = str(data.get("failed_step", "")).strip() or _heuristic_failed_step(outcome)
                failure_mode = str(data.get("failure_mode", "unknown"))
                if failure_mode not in ("tool_failure", "bad_output", "stuck_loop", "llm_error", "unknown"):
                    failure_mode = "unknown"
                contributing_factors = [str(f) for f in data.get("contributing_factors", []) if str(f).strip()]
                confidence = float(data.get("confidence", 0.5))
                confidence = max(0.0, min(1.0, confidence))
                return Attribution(
                    session_id=session_id,
                    goal=goal,
                    failed_step=failed_step,
                    failed_skill=None,
                    failure_mode=failure_mode,
                    contributing_factors=contributing_factors,
                    confidence=confidence,
                    raw_reason=stuck_reason,
                )
        except Exception as e:
            if __debug__:
                print(f"[attribution] LLM call failed: {e}, falling back to heuristic", file=sys.stderr)

    # Heuristic fallback
    failure_mode, confidence = _heuristic_failure_mode(stuck_reason)
    failed_step = _heuristic_failed_step(outcome)

    # Try to match a skill name from the steps summary or stuck_reason
    failed_skill = _try_match_skill(outcome)

    return Attribution(
        session_id=session_id,
        goal=goal,
        failed_step=failed_step,
        failed_skill=failed_skill,
        failure_mode=failure_mode,
        contributing_factors=[],
        confidence=confidence,
        raw_reason=stuck_reason,
    )


def _try_match_skill(outcome: dict) -> Optional[str]:
    """Try to find a matching skill name from the outcome context."""
    try:
        from skills import load_skills
        skills = load_skills()
        if not skills:
            return None
        text = " ".join([
            str(outcome.get("goal", "")),
            str(outcome.get("stuck_reason", "")),
            str(outcome.get("summary", "")),
        ]).lower()
        for skill in skills:
            if skill.name.lower() in text:
                return skill.name
        # Try trigger patterns
        for skill in skills:
            for pat in skill.trigger_patterns:
                if pat.lower() in text:
                    return skill.name
    except Exception:
        pass
    return None


def attribute_batch(outcomes: List[dict], adapter=None) -> AttributionReport:
    """Attribute failures across multiple outcomes.

    Args:
        outcomes: List of outcome dicts (from outcomes.jsonl).
        adapter:  LLMAdapter. None → heuristic paths only.

    Returns:
        AttributionReport with aggregated statistics.
    """
    from collections import Counter

    # Filter to stuck/error outcomes only
    failed = [o for o in outcomes if o.get("status") in ("stuck", "error", "blocked")]
    # Analyze up to 20 most recent
    to_analyze = failed[-20:] if len(failed) > 20 else failed

    attributions: List[Attribution] = []
    for outcome in to_analyze:
        try:
            attr = attribute_failure(outcome, adapter=adapter)
            attributions.append(attr)
        except Exception as e:
            if __debug__:
                print(f"[attribution] attribute_failure error: {e}", file=sys.stderr)

    # Aggregate statistics
    mode_counts = Counter(a.failure_mode for a in attributions)
    most_common_failure_modes = [mode for mode, _ in mode_counts.most_common()]

    skill_counts = Counter(
        a.failed_skill for a in attributions
        if a.failed_skill
    )
    most_blamed_skills = [skill for skill, _ in skill_counts.most_common()]

    return AttributionReport(
        attributions=attributions,
        most_common_failure_modes=most_common_failure_modes,
        most_blamed_skills=most_blamed_skills,
    )
