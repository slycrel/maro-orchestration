"""Meta-Evolver — §19 Self-Leveling / Meta-Evolution

Periodically reviews recent run outcomes to identify failure patterns,
propose prompt improvements, and generate new guardrails.

This is the "Poe gets better over time" component. It:
  1. Loads the last N outcomes from memory/outcomes.jsonl
  2. Asks an LLM to identify failure patterns and suggest improvements
  3. Writes structured suggestions to memory/suggestions.jsonl
  4. Optionally sends a summary via Telegram

Design follows the Reflexion pattern (per §19): reflect on failures,
store lessons, inject lessons into future prompts (handled by memory.py).
The meta-evolver is the *aggregate* level — looking across many runs, not
just one.

Usage:
    python3 evolver.py                  # run once
    python3 evolver.py --dry-run        # analyze without writing
    python3 evolver.py --min-outcomes 5 # only run if >= 5 new outcomes
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from llm_parse import extract_json, safe_float, safe_str, safe_list, content_or_empty

log = logging.getLogger("poe.evolver")

# Module-level imports for clean test patching
try:
    from memory import load_outcomes, load_lessons, Outcome, Lesson
except ImportError:  # pragma: no cover
    load_outcomes = None  # type: ignore[assignment]
    load_lessons = None  # type: ignore[assignment]

try:
    from llm import build_adapter, MODEL_CHEAP, MODEL_MID, LLMMessage
except ImportError:  # pragma: no cover
    build_adapter = None  # type: ignore[assignment]

try:
    from skills import validate_skill_mutation
except ImportError:  # pragma: no cover
    validate_skill_mutation = None  # type: ignore[assignment]

try:
    from memory import record_tiered_lesson, MemoryTier
except ImportError:  # pragma: no cover
    record_tiered_lesson = None  # type: ignore[assignment]
    MemoryTier = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Suggestion:
    suggestion_id: str
    category: str           # "prompt_tweak" | "new_guardrail" | "skill_pattern" | "observation"
    target: str             # what this suggestion applies to: task_type or "all"
    suggestion: str         # the actual text of the improvement
    failure_pattern: str    # what pattern was observed to motivate this
    confidence: float       # 0.0-1.0
    outcomes_analyzed: int  # how many outcomes were reviewed
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    applied: bool = False

    def to_dict(self) -> dict:
        return {
            "suggestion_id": self.suggestion_id,
            "category": self.category,
            "target": self.target,
            "suggestion": self.suggestion,
            "failure_pattern": self.failure_pattern,
            "confidence": self.confidence,
            "outcomes_analyzed": self.outcomes_analyzed,
            "generated_at": self.generated_at,
            "applied": self.applied,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Suggestion":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


@dataclass
class EvolverReport:
    run_id: str
    outcomes_reviewed: int
    suggestions: List[Suggestion] = field(default_factory=list)
    failure_patterns: List[str] = field(default_factory=list)
    elapsed_ms: int = 0
    skipped: bool = False
    skip_reason: str = ""

    def summary(self) -> str:
        if self.skipped:
            return f"evolver run_id={self.run_id} skipped: {self.skip_reason}"
        lines = [
            f"evolver run_id={self.run_id}",
            f"outcomes_reviewed={self.outcomes_reviewed}",
            f"suggestions={len(self.suggestions)}",
            f"failure_patterns={len(self.failure_patterns)}",
            f"elapsed_ms={self.elapsed_ms}",
        ]
        for s in self.suggestions:
            lines.append(f"  [{s.category}] {s.target}: {s.suggestion[:80]}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "outcomes_reviewed": self.outcomes_reviewed,
            "suggestions": [s.to_dict() for s in self.suggestions],
            "failure_patterns": self.failure_patterns,
            "elapsed_ms": self.elapsed_ms,
            "skipped": self.skipped,
            "skip_reason": self.skip_reason,
        }


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def _suggestions_path() -> Path:
    from orch_items import memory_dir
    return memory_dir() / "suggestions.jsonl"


def _dynamic_constraints_path() -> Path:
    """Path to evolver-generated dynamic constraint patterns."""
    from orch_items import memory_dir
    return memory_dir() / "dynamic-constraints.jsonl"


def load_suggestions(limit: int = 20) -> List[Suggestion]:
    """Load most recent suggestions, newest first."""
    p = _suggestions_path()
    if not p.exists():
        return []
    suggestions: List[Suggestion] = []
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    suggestions.append(Suggestion.from_dict(json.loads(line)))
                except Exception:
                    pass
    except Exception:
        pass
    return list(reversed(suggestions))[:limit]


def _save_suggestions(suggestions: List[Suggestion]) -> None:
    p = _suggestions_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        for s in suggestions:
            f.write(json.dumps(s.to_dict()) + "\n")


def list_pending_suggestions(limit: int = 20) -> List[Suggestion]:
    """Return suggestions where applied=False, newest first."""
    all_suggestions = load_suggestions(limit=1000)
    pending = [s for s in all_suggestions if not s.applied]
    return pending[:limit]


def _apply_suggestion_action(d: dict) -> None:
    """Execute the real-world effect of an approved suggestion.

    Called from apply_suggestion() after the test gate passes.  Each category
    has a concrete action that closes the feedback loop:

        skill_pattern  → write/update a Skill in skills.jsonl
        prompt_tweak   → record a TieredLesson (medium tier) for future injection
        new_guardrail  → append pattern to memory/dynamic-constraints.jsonl
        observation    → no-op (informational only)

    Never raises — failures are logged to stderr and silently swallowed so
    a bad suggestion never blocks the caller.
    """
    category = d.get("category", "observation")
    suggestion_text = d.get("suggestion", "")
    target = d.get("target", "all")
    suggestion_id = d.get("suggestion_id", "")
    confidence = float(d.get("confidence", 0.5))

    # Capture before-state for rollback surface.
    before_state = None
    try:
        if category == "skill_pattern":
            from skills import load_skills as _ls_audit, _skills_path as _sp_audit
            _existing = next((s for s in _ls_audit() if s.name == target or s.id == target), None)
            if _existing is not None:
                before_state = {"type": "skill_update", "old_description": _existing.description[:500]}
            else:
                before_state = {"type": "skill_create"}
        elif category == "new_guardrail":
            before_state = {"type": "guardrail_append"}
        elif category == "prompt_tweak":
            before_state = {"type": "lesson_add"}
    except Exception:
        pass

    # Audit trail: log every mutation before it happens so changes are recoverable.
    try:
        from orch_items import memory_dir as _memory_dir
        import hashlib as _hashlib
        _cl_path = _memory_dir() / "change_log.jsonl"
        _cl_entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "module": "evolver",
            "action": "_apply_suggestion_action",
            "category": category,
            "suggestion_id": suggestion_id,
            "target": target,
            "confidence": confidence,
            "suggestion_text": suggestion_text[:500],
            "suggestion_hash": _hashlib.sha256(suggestion_text.encode()).hexdigest()[:12],
            "before_state": before_state,
        }
        _cl_path.parent.mkdir(parents=True, exist_ok=True)
        with open(_cl_path, "a", encoding="utf-8") as _clf:
            _clf.write(json.dumps(_cl_entry) + "\n")
    except Exception:
        pass  # audit trail must never block execution

    try:
        if category == "skill_pattern":
            # Write or update the skill in skills.jsonl
            from skills import load_skills, save_skill, Skill, _skills_path as _sp
            import uuid as _uuid
            skills = load_skills()
            existing = next((s for s in skills if s.name == target or s.id == target), None)
            if existing is not None:
                # Backup the skill file before mutating so rollback is possible.
                # .bak is overwritten on each suggestion — keeps last-good state.
                try:
                    import shutil as _shutil
                    _src = _sp()
                    if _src.exists():
                        _shutil.copy2(str(_src), str(_src) + ".bak")
                except Exception as _be:
                    print(f"[evolver] skill backup failed (non-blocking): {_be}", file=sys.stderr)
                # Update description with the suggestion; keep rest intact
                existing.description = suggestion_text[:500]
                save_skill(existing)
            else:
                # Create a new provisional skill from the suggestion text
                new_skill = Skill(
                    id=_uuid.uuid4().hex[:8],
                    name=target or f"evolver-skill-{suggestion_id}",
                    description=suggestion_text[:500],
                    trigger_patterns=[target] if target and target != "all" else [],
                    steps_template=[suggestion_text[:200]],
                    source_loop_ids=[suggestion_id],
                    created_at=datetime.now(timezone.utc).isoformat(),
                    tier="provisional",
                    utility_score=confidence,
                )
                save_skill(new_skill)

        elif category == "prompt_tweak":
            # Record as a tiered lesson so it gets injected into future prompts
            if record_tiered_lesson is not None and MemoryTier is not None:
                record_tiered_lesson(
                    lesson_text=suggestion_text,
                    task_type=target if target and target != "all" else "general",
                    outcome="evolver_suggestion",
                    source_goal=f"evolver-{suggestion_id}",
                    tier=MemoryTier.MEDIUM,
                    confidence=confidence,
                )

        elif category == "new_guardrail":
            # Append to dynamic-constraints.jsonl — loaded by constraint.py at runtime
            entry = {
                "pattern": suggestion_text,
                "risk": "MEDIUM",
                "detail": f"evolver guardrail (id={suggestion_id}): {suggestion_text[:80]}",
                "source": suggestion_id,
                "added_at": datetime.now(timezone.utc).isoformat(),
            }
            with open(_dynamic_constraints_path(), "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")

        # observation: no action needed

        # Captain's log: evolver applied a suggestion
        try:
            from captains_log import log_event, EVOLVER_APPLIED
            log_event(
                event_type=EVOLVER_APPLIED,
                subject=target or category,
                summary=f"Applied {category} suggestion (confidence: {confidence:.2f}). {suggestion_text[:100]}",
                context={"suggestion_id": suggestion_id, "category": category, "confidence": confidence},
            )
        except Exception:
            pass

    except Exception as e:
        print(f"[evolver] _apply_suggestion_action({category}) failed: {e}", file=sys.stderr)


def apply_suggestion(suggestion_id: str) -> bool:
    """Mark a suggestion as applied=True by rewriting suggestions.jsonl.

    Phase 14: For suggestions with category == "skill_pattern", runs the
    unit-test gate via validate_skill_mutation() before applying. If the gate
    blocks the mutation, sets status to "gate_blocked" instead of "applied".

    Returns True if the suggestion was found and updated, False otherwise.
    """
    log.info("apply_suggestion id=%s", suggestion_id)
    p = _suggestions_path()
    if not p.exists():
        return False

    lines = p.read_text(encoding="utf-8").splitlines()
    found = False
    new_lines: List[str] = []

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
            if d.get("suggestion_id") == suggestion_id:
                found = True
                # Phase 14: skill_pattern suggestions go through test gate
                category = d.get("category", "observation")

                if category == "skill_pattern" and validate_skill_mutation is not None:
                    gate_result = _run_skill_test_gate(d)
                    if gate_result is not None and gate_result.get("blocked"):
                        d["applied"] = False
                        d["status"] = "gate_blocked"
                        d["block_reason"] = gate_result.get("block_reason", "test gate blocked mutation")
                    else:
                        d["applied"] = True
                        d.pop("status", None)
                        _apply_suggestion_action(d)
                elif category == "new_guardrail":
                    # Guardrails can permanently block execution — require explicit opt-in.
                    # Set POE_AUTO_APPLY_GUARDRAILS=1 to enable, otherwise hold for human review.
                    if os.environ.get("POE_AUTO_APPLY_GUARDRAILS") == "1":
                        d["applied"] = True
                        _apply_suggestion_action(d)
                    else:
                        d["applied"] = False
                        d["status"] = "held_for_review"
                        d["block_reason"] = "new_guardrail requires POE_AUTO_APPLY_GUARDRAILS=1 or manual review"
                        log.info("evolver: guardrail held for review (not auto-applied): %s", d.get("suggestion", "")[:100])
                elif category == "prompt_tweak":
                    # Prompt tweaks are lower risk (just a lesson) but log prominently
                    d["applied"] = True
                    _apply_suggestion_action(d)
                    log.info("evolver: auto-applied prompt_tweak: %s", d.get("suggestion", "")[:100])
                else:
                    # observation, sub_mission, etc. — safe to apply
                    d["applied"] = True
                    _apply_suggestion_action(d)
            new_lines.append(json.dumps(d))
        except Exception:
            new_lines.append(line)

    if found:
        p.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    return found


def _run_skill_test_gate(suggestion_dict: dict) -> Optional[dict]:
    """Run the unit-test gate for a skill_pattern suggestion.

    Returns dict with {blocked: bool, block_reason: str} or None if gate
    cannot be run (e.g., skill not found).
    """
    if validate_skill_mutation is None:
        return None

    try:
        from skills import load_skills, Skill
        import uuid as _uuid
        from datetime import datetime, timezone

        skills = load_skills()
        suggestion_text = suggestion_dict.get("suggestion", "")
        target = suggestion_dict.get("target", "")

        # Try to find the target skill
        original_skill = None
        for sk in skills:
            if sk.name == target or sk.id == target:
                original_skill = sk
                break

        if original_skill is None:
            # Cannot validate — allow through
            return {"blocked": False, "block_reason": ""}

        # Create a mutated skill from the suggestion
        mutated_skill = Skill(
            id=original_skill.id,
            name=original_skill.name,
            description=suggestion_text[:500] if suggestion_text else original_skill.description,
            trigger_patterns=original_skill.trigger_patterns,
            steps_template=original_skill.steps_template,
            source_loop_ids=original_skill.source_loop_ids,
            created_at=original_skill.created_at,
            use_count=original_skill.use_count,
            success_rate=original_skill.success_rate,
        )

        # Build a cheap adapter for the gate so it actually runs tests rather
        # than falling through as a dry-run (adapter=None → blocked=False always).
        _gate_adapter = None
        try:
            from llm import build_adapter as _build_adapter, MODEL_CHEAP as _MODEL_CHEAP
            _gate_adapter = _build_adapter(model=_MODEL_CHEAP)
        except Exception:
            pass  # fall back to heuristic path if adapter unavailable

        result = validate_skill_mutation(original_skill, mutated_skill, adapter=_gate_adapter)
        return {"blocked": result.blocked, "block_reason": result.block_reason}

    except Exception as e:
        if __debug__:
            print(f"[evolver] _run_skill_test_gate failed: {e}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Business signal scanning (Mode 2 → Mode 3 bridge)
# ---------------------------------------------------------------------------

_SIGNAL_SYSTEM = """\
You are Poe's signal scanner. You analyze completed run outcomes to identify
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

    try:
        adapter = build_adapter(model=MODEL_CHEAP)
        resp = adapter.complete(
            [
                LLMMessage("system", _SIGNAL_SYSTEM),
                LLMMessage("user", f"Scan these outcomes for signals:\n\n" + "\n".join(lines)),
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
# LLM analysis
# ---------------------------------------------------------------------------

_EVOLVER_SYSTEM = """\
You are Poe's meta-evolution agent. You analyze patterns across many completed and failed runs
to identify systemic improvements.

You will receive a summary of recent run outcomes. Identify:
1. Failure patterns (repeated reasons for "stuck" outcomes)
2. Success patterns (what made "done" outcomes succeed)
3. Prompt improvements (changes to agent instructions that would reduce failures)
4. New guardrails (checks or constraints to prevent common failure modes)

Respond ONLY with a JSON object in this format:
{
  "failure_patterns": ["pattern 1", "pattern 2"],
  "suggestions": [
    {
      "category": "prompt_tweak|new_guardrail|skill_pattern|observation",
      "target": "all|research|build|ops|agenda|now",
      "suggestion": "specific improvement text",
      "failure_pattern": "what pattern motivated this",
      "confidence": 0.0-1.0
    }
  ]
}

Be specific and actionable. Suggest at most 5 improvements total. If there are no clear patterns
(e.g., too few outcomes), return {"failure_patterns": [], "suggestions": []}.
"""


def _build_outcomes_summary(outcomes: List[Any]) -> str:
    """Summarize outcomes for LLM analysis.

    Meta-Harness steal: enriches stuck outcomes with full step-level execution
    traces so the proposer sees actual failure paths, not just aggregate summaries.
    """
    if not outcomes:
        return "(no outcomes to analyze)"

    stuck = [o for o in outcomes if o.status == "stuck"]
    done = [o for o in outcomes if o.status == "done"]

    lines = [
        f"Total outcomes: {len(outcomes)} ({len(done)} done, {len(stuck)} stuck)",
        "",
        "Recent outcomes:",
    ]
    for o in outcomes[:20]:
        lines.append(
            f"  [{o.status}] [{o.task_type}] {o.goal[:60]}"
            + (f" — {o.summary[:80]}" if o.summary else "")
        )

    if stuck:
        lines.append("\nStuck outcome summaries:")
        for o in stuck[:10]:
            lines.append(f"  - {o.summary[:120]}")

        # Meta-Harness: include full step traces for stuck outcomes so the
        # proposer can identify exactly where runs failed and why
        stuck_ids = [getattr(o, "outcome_id", "") for o in stuck[:5] if getattr(o, "outcome_id", "")]
        if stuck_ids:
            try:
                from memory import load_step_traces
                traces = load_step_traces(stuck_ids)
                if traces:
                    lines.append("\nFull step traces for stuck runs:")
                    for oid, trace in traces.items():
                        lines.append(f"\n  [trace:{oid}] goal: {trace.get('goal', '')[:80]}")
                        for step in trace.get("steps", [])[:8]:
                            s_status = step.get("status", "?")
                            s_text = step.get("step", "")[:60]
                            s_reason = step.get("stuck_reason", "")
                            lines.append(f"    [{s_status}] {s_text}"
                                         + (f" — stuck: {s_reason[:80]}" if s_reason else ""))
            except Exception:
                pass

    return "\n".join(lines)


def _llm_analyze(outcomes: List[Any], *, dry_run: bool = False) -> tuple[List[str], List[dict]]:
    """Ask LLM to identify patterns and suggest improvements. Returns (patterns, raw_suggestions)."""
    if dry_run or not outcomes:
        return [], []

    try:
        adapter = build_adapter(model=MODEL_MID)
        summary = _build_outcomes_summary(outcomes)

        # Captain's log context: recent learning-system actions for the evolver
        # to account for (e.g., "skill X was just demoted — don't re-suggest it")
        _log_ctx = ""
        try:
            from captains_log import load_log
            _recent = load_log(limit=20)
            _relevant = [
                e for e in _recent
                if e.get("event_type") in (
                    "SKILL_PROMOTED", "SKILL_DEMOTED", "SKILL_CIRCUIT_OPEN",
                    "SKILL_REWRITE", "EVOLVER_APPLIED", "EVOLVER_SKIPPED",
                    "STANDING_RULE_CONTRADICTED", "RULE_GRADUATED",
                )
            ]
            if _relevant:
                _log_lines = [
                    f"- [{e.get('event_type')}] {e.get('summary', '')[:100]}"
                    for e in _relevant[-5:]
                ]
                _log_ctx = "\n\nRecent learning system activity:\n" + "\n".join(_log_lines)
        except Exception:
            pass

        resp = adapter.complete(
            [
                LLMMessage("system", _EVOLVER_SYSTEM),
                LLMMessage("user", f"Analyze these outcomes:\n\n{summary}{_log_ctx}"),
            ],
            max_tokens=2048,
            temperature=0.2,
        )
        data = extract_json(content_or_empty(resp), dict, log_tag="evolver._analyze")
        if data:
            patterns = safe_list(data.get("failure_patterns", []), element_type=str)
            raw_suggestions = safe_list(data.get("suggestions", []), element_type=dict)
            return patterns, raw_suggestions
    except Exception as e:
        if __debug__:
            print(f"[evolver] LLM analysis failed: {e}", file=sys.stderr)
    return [], []


# ---------------------------------------------------------------------------
# Core run
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
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


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


def run_evolver(
    *,
    outcomes_window: int = 50,
    min_outcomes: int = 3,
    dry_run: bool = False,
    verbose: bool = True,
    notify: bool = False,
    scan_signals: bool = True,
    scan_calibration: bool = True,
    scan_costs: bool = True,
    scan_drift: bool = True,
) -> EvolverReport:
    """Run one meta-evolution cycle.

    Args:
        outcomes_window: How many recent outcomes to analyze.
        min_outcomes: Skip if fewer than this many outcomes exist.
        dry_run: Analyze without writing suggestions.
        verbose: Print progress to stderr.
        notify: Send Telegram summary if suggestions were generated.

    Returns:
        EvolverReport with suggestions and failure patterns.
    """
    import uuid as _uuid

    run_id = _uuid.uuid4().hex[:8]
    started = time.monotonic()

    log.info("evolver_start run_id=%s outcomes_window=%d min=%d dry_run=%s",
             run_id, outcomes_window, min_outcomes, dry_run)
    if verbose:
        print(f"[evolver] run_id={run_id} starting...", file=sys.stderr)

    # Load recent outcomes
    try:
        outcomes = load_outcomes(limit=outcomes_window)
    except Exception as e:
        return EvolverReport(run_id=run_id, outcomes_reviewed=0, skipped=True, skip_reason=str(e))

    if len(outcomes) < min_outcomes:
        return EvolverReport(
            run_id=run_id,
            outcomes_reviewed=len(outcomes),
            skipped=True,
            skip_reason=f"only {len(outcomes)} outcomes (need {min_outcomes})",
        )

    if verbose:
        print(f"[evolver] analyzing {len(outcomes)} outcomes...", file=sys.stderr)

    # LLM analysis
    patterns, raw_suggestions = _llm_analyze(outcomes, dry_run=dry_run)

    # Build Suggestion objects
    suggestions: List[Suggestion] = []
    for i, raw in enumerate(raw_suggestions):
        try:
            suggestions.append(Suggestion(
                suggestion_id=f"{run_id}-{i:02d}",
                category=raw.get("category", "observation"),
                target=raw.get("target", "all"),
                suggestion=raw.get("suggestion", ""),
                failure_pattern=raw.get("failure_pattern", ""),
                confidence=safe_float(raw.get("confidence"), default=0.5, min_val=0.0, max_val=1.0),
                outcomes_analyzed=len(outcomes),
            ))
        except Exception:
            pass

    # Business signal scan — convert actionable findings to sub_mission suggestions
    if scan_signals:
        try:
            signals = scan_outcomes_for_signals(outcomes, dry_run=dry_run)
            for sig in signals:
                import uuid as _sig_uuid
                suggestions.append(Suggestion(
                    suggestion_id=f"sig-{_sig_uuid.uuid4().hex[:8]}",
                    category="sub_mission",
                    target=sig.signal_type,
                    suggestion=sig.suggested_goal,
                    failure_pattern=f"signal from: {sig.source_outcome[:80]}",
                    confidence=sig.confidence,
                    outcomes_analyzed=len(outcomes),
                ))
            if verbose and signals:
                print(f"[evolver] signal_scan: {len(signals)} sub_mission suggestion(s)", file=sys.stderr)
            log.info("evolver signal_scan signals=%d", len(signals))
        except Exception as _sig_exc:
            log.debug("signal scan failed (non-fatal): %s", _sig_exc)

    # Calibration review — detect systematic over/under-confidence in escalation decisions
    if scan_calibration:
        try:
            cal_findings = scan_calibration_log()
            for cf in cal_findings:
                import uuid as _cal_uuid
                suggestions.append(Suggestion(
                    suggestion_id=f"cal-{_cal_uuid.uuid4().hex[:8]}",
                    category="prompt_tweak",
                    target="escalation",
                    suggestion=cf.suggestion,
                    failure_pattern=(
                        f"calibration: class={cf.decision_class!r} "
                        f"override_rate={cf.override_rate:.0%} "
                        f"mean_confidence={cf.mean_confidence:.1f}/10 "
                        f"n={cf.entry_count}"
                    ),
                    confidence=0.75,
                    outcomes_analyzed=cf.entry_count,
                ))
            if verbose and cal_findings:
                print(f"[evolver] calibration_scan: {len(cal_findings)} finding(s)", file=sys.stderr)
            log.info("evolver calibration_scan findings=%d", len(cal_findings))
        except Exception as _cal_exc:
            log.debug("calibration scan failed (non-fatal): %s", _cal_exc)

    # Step cost scan — detect high-burn step patterns, propose Haiku routing
    if scan_costs:
        try:
            cost_suggestions = scan_step_costs()
            suggestions.extend(cost_suggestions)
            if verbose and cost_suggestions:
                print(f"[evolver] cost_scan: {len(cost_suggestions)} high-burn suggestion(s)", file=sys.stderr)
            log.info("evolver cost_scan suggestions=%d", len(cost_suggestions))
        except Exception as _cost_exc:
            log.debug("cost scan failed (non-fatal): %s", _cost_exc)

    # Quality drift detection — compare this cycle to rolling baseline
    if scan_drift:
        try:
            # Convert outcomes to dicts for scan_quality_drift
            _outcome_dicts = [o if isinstance(o, dict) else (o.__dict__ if hasattr(o, "__dict__") else {}) for o in outcomes]
            drift_findings = scan_quality_drift(_outcome_dicts)
            for df in drift_findings:
                import uuid as _drift_uuid
                suggestions.append(Suggestion(
                    suggestion_id=f"drift-{_drift_uuid.uuid4().hex[:8]}",
                    category="observation",
                    target=df.metric,
                    suggestion=df.suggestion,
                    failure_pattern=f"quality_drift: {df.metric} delta={df.delta_pct:.1f}% consecutive={df.consecutive_drops}",
                    confidence=min(0.9, 0.6 + df.consecutive_drops * 0.1),
                    outcomes_analyzed=len(outcomes),
                ))
            if verbose and drift_findings:
                print(f"[evolver] drift_scan: {len(drift_findings)} quality drift finding(s)", file=sys.stderr)
            log.info("evolver drift_scan findings=%d", len(drift_findings))
        except Exception as _drift_exc:
            log.debug("quality drift scan failed (non-fatal): %s", _drift_exc)

    report = EvolverReport(
        run_id=run_id,
        outcomes_reviewed=len(outcomes),
        suggestions=suggestions,
        failure_patterns=patterns,
        elapsed_ms=int((time.monotonic() - started) * 1000),
    )

    if verbose:
        print(f"[evolver] found {len(patterns)} patterns, {len(suggestions)} suggestions", file=sys.stderr)

    # Persist suggestions
    if not dry_run and suggestions:
        try:
            _save_suggestions(suggestions)
        except Exception as e:
            if verbose:
                print(f"[evolver] failed to save suggestions: {e}", file=sys.stderr)

    # Auto-apply high-confidence suggestions (closes the feedback loop)
    # Advisor Pattern: for medium-confidence suggestions (0.6–0.79), consult
    # Opus before applying. High-confidence (≥0.8) still auto-apply directly.
    auto_applied = 0
    advisor_promoted = 0
    if not dry_run and suggestions:
        for s in suggestions:
            if s.applied:
                continue
            if s.confidence >= 0.8:
                if apply_suggestion(s.suggestion_id):
                    auto_applied += 1
            elif 0.6 <= s.confidence < 0.8:
                # Advisor gate: let Opus decide on medium-confidence suggestions
                try:
                    from llm import advisor_call as _adv_call
                    _adv_context = (
                        f"Category: {s.category}\n"
                        f"Suggestion: {s.text[:300]}\n"
                        f"Confidence: {s.confidence:.2f}\n"
                        f"Target: {getattr(s, 'target', 'all')}\n"
                        f"Based on {len(outcomes)} recent outcomes."
                    )
                    _advice = _adv_call(
                        goal="meta-improvement: should this suggestion be auto-applied?",
                        context=_adv_context,
                        question=(
                            "This suggestion has medium confidence (0.6-0.79). "
                            "Should we auto-apply it? Consider: (a) could it degrade existing behavior, "
                            "(b) is the evidence strong enough, (c) is it reversible? "
                            "Answer YES to apply, NO to defer for human review."
                        ),
                    )
                    if _advice and "yes" in _advice.lower().split()[:5]:
                        if apply_suggestion(s.suggestion_id):
                            auto_applied += 1
                            advisor_promoted += 1
                            log.info("evolver advisor: promoted suggestion %s (confidence %.2f)",
                                     s.suggestion_id, s.confidence)
                    else:
                        log.info("evolver advisor: deferred suggestion %s (confidence %.2f): %s",
                                 s.suggestion_id, s.confidence, (_advice or "no response")[:100])
                except Exception:
                    pass  # advisor is optional — never block evolver
        if verbose and auto_applied:
            print(f"[evolver] auto-applied {auto_applied} suggestions ({advisor_promoted} via advisor)", file=sys.stderr)

    # Telegram notification
    if notify and suggestions and not dry_run:
        _notify_telegram(report)

    report.elapsed_ms = int((time.monotonic() - started) * 1000)
    log.info("evolver_done run_id=%s patterns=%d suggestions=%d auto_applied=%d elapsed=%dms",
             run_id, len(patterns), len(suggestions), auto_applied, report.elapsed_ms)

    # Captain's log: evolver cycle summary
    try:
        from captains_log import log_event, EVOLVER_GENERATED, EVOLVER_APPLIED, EVOLVER_SKIPPED
        if suggestions:
            log_event(
                event_type=EVOLVER_GENERATED,
                subject=f"run-{run_id}",
                summary=f"Generated {len(suggestions)} suggestions from {len(outcomes)} outcomes. {auto_applied} auto-applied.",
                context={
                    "run_id": run_id,
                    "outcomes_reviewed": len(outcomes),
                    "suggestions": len(suggestions),
                    "auto_applied": auto_applied,
                    "patterns": len(patterns),
                },
            )
        elif not report.skipped:
            log_event(
                event_type=EVOLVER_SKIPPED,
                subject=f"run-{run_id}",
                summary=f"No suggestions from {len(outcomes)} outcomes.",
                context={"run_id": run_id, "outcomes_reviewed": len(outcomes)},
            )
    except Exception:
        pass

    # Phase 17: check if router retraining is needed
    try:
        from router import maybe_retrain
        maybe_retrain()
    except Exception:
        pass

    # Phase 46: intervention graduation — propose permanent rules for repeated patterns
    if not dry_run:
        try:
            from graduation import run_graduation
            _grad_count = run_graduation(verbose=verbose)
            if _grad_count and verbose:
                print(f"[evolver] graduation: {_grad_count} new permanent rule suggestion(s)", file=sys.stderr)
            log.debug("evolver graduation_pass: new_suggestions=%d", _grad_count)
        except Exception as _grad_exc:
            log.debug("graduation pass failed (non-fatal): %s", _grad_exc)

    # FunSearch island model — anti-monoculture selection pressure on skill pool
    try:
        from skills import run_island_cycle
        _island_result = run_island_cycle(dry_run=dry_run, verbose=verbose)
        if _island_result.get("total_culled") and verbose:
            print(f"[evolver] island_cycle: culled {_island_result['total_culled']} underperforming skills",
                  file=sys.stderr)
        log.debug("evolver island_cycle: assigned=%d total_culled=%d",
                  _island_result.get("assigned", 0), _island_result.get("total_culled", 0))
    except Exception as _island_exc:
        log.debug("island cycle failed (non-fatal): %s", _island_exc)

    return report


# ---------------------------------------------------------------------------
# Telegram notification
# ---------------------------------------------------------------------------

def _notify_telegram(report: EvolverReport) -> None:
    try:
        from telegram_listener import TelegramBot, _resolve_token, _resolve_allowed_chats
        token = _resolve_token()
        if not token:
            return
        bot = TelegramBot(token)
        allowed = _resolve_allowed_chats()
        if not allowed:
            return
        lines = [f"🧠 *Poe Meta-Evolver* — {len(report.suggestions)} suggestions"]
        for fp in report.failure_patterns[:3]:
            lines.append(f"• Pattern: {fp}")
        for s in report.suggestions[:3]:
            lines.append(f"  [{s.category}] {s.suggestion[:100]}")
        msg = "\n".join(lines)
        for chat_id in allowed:
            bot.send_message(chat_id, msg)
    except Exception as e:
        print(f"[evolver] telegram notify failed: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Friction-aware evolver (Phase 12)
# ---------------------------------------------------------------------------

def receive_inspector_tickets(tickets: List[dict]) -> int:
    """Accept inspector-generated tickets and convert them to Suggestion objects.

    Inspector tickets map:
      title          → suggestion
      pattern        → failure_pattern
      suggested_fix  → suggestion (preferred over title)
      priority       → confidence (high=0.9, medium=0.7, low=0.5)

    Args:
        tickets: List of ticket dicts from inspector.generate_tickets().

    Returns:
        Count of suggestions added.
    """
    import uuid as _uuid

    _PRIORITY_CONFIDENCE = {"high": 0.9, "medium": 0.7, "low": 0.5}

    suggestions: List[Suggestion] = []
    for t in tickets:
        if not isinstance(t, dict):
            continue
        priority = t.get("priority", "medium")
        confidence = _PRIORITY_CONFIDENCE.get(priority, 0.7)
        suggestion_text = t.get("suggested_fix") or t.get("title") or ""
        if not suggestion_text.strip():
            continue
        suggestions.append(Suggestion(
            suggestion_id=f"insp-{_uuid.uuid4().hex[:8]}",
            category="inspection_finding",
            target="all",
            suggestion=suggestion_text,
            failure_pattern=t.get("pattern", "inspector finding"),
            confidence=confidence,
            outcomes_analyzed=0,
        ))

    if suggestions:
        try:
            _save_suggestions(suggestions)
        except Exception as e:
            print(f"[evolver] receive_inspector_tickets: failed to save: {e}", file=sys.stderr)
            return 0

    return len(suggestions)


def _compactness_adjusted_score(skill: "Skill") -> float:
    """Brevity-penalized utility score (FunSearch-inspired).

    Favors compact skills over verbose ones with the same utility. Uses a
    log-penalty so very short skills aren't unfairly favored over medium ones.

    char_count = len(description) + sum of step lengths
    adjusted = utility_score / log(1 + char_count / 200)

    A skill with utility_score=0.9 and 400 chars scores ~0.66.
    A skill with utility_score=0.9 and 100 chars scores ~0.86.
    """
    import math
    char_count = len(skill.description) + sum(len(s) for s in skill.steps_template)
    penalty = math.log(1.0 + char_count / 200.0)
    return skill.utility_score / max(penalty, 1.0)


def _top_peer_skills(failing_skill: "Skill", k: int = 2) -> List["Skill"]:
    """Return up to k healthy peer skills with the highest compactness-adjusted score.

    Used to build ranked-candidate context for rewrite_skill (FunSearch pattern:
    LLM sees "here is v0 (score=X), v1 (score=Y) — generate v2").
    """
    try:
        from skills import load_skills
    except ImportError:
        return []

    all_skills = load_skills()
    # Exclude the failing skill and any with open circuit
    candidates = [
        s for s in all_skills
        if s.id != failing_skill.id and s.circuit_state != "open" and s.utility_score > 0.5
    ]
    if not candidates:
        return []

    # Score by compactness-adjusted utility
    scored = sorted(candidates, key=_compactness_adjusted_score, reverse=True)
    return scored[:k]


def rewrite_skill(skill: "Skill", adapter) -> Optional["Skill"]:
    """LLM-rewrite a skill whose circuit breaker is OPEN.

    Analyses the skill's failure_notes and current body, produces a revised
    description + steps_template. Resets consecutive_failures and sets
    circuit_state to "half_open" (probationary — not yet trusted).

    Returns the updated Skill on success, None if rewrite fails or adapter unavailable.

    The skill is saved to disk whether or not the caller uses the return value.
    """
    try:
        from skills import _save_skills, load_skills, compute_skill_hash, _skill_to_dict
        from llm import LLMMessage
    except ImportError:
        return None

    if adapter is None:
        return None

    failure_summary = (
        "\n".join(f"- {n}" for n in skill.failure_notes)
        if skill.failure_notes
        else "(no specific failure reasons recorded)"
    )

    # Build ranked-candidate context (FunSearch pattern: show top performers so LLM
    # can recombine their approaches rather than starting from scratch)
    peer_skills = _top_peer_skills(skill)
    peer_context = ""
    if peer_skills:
        lines = ["Top-performing peer skills for reference (compactness-adjusted):"]
        for i, peer in enumerate(peer_skills):
            steps_preview = "; ".join(peer.steps_template[:3])
            lines.append(
                f"  v{i} (score={peer.utility_score:.2f}): {peer.name} — {peer.description[:100]}"
                f"\n    Steps: {steps_preview}"
            )
        peer_context = "\n" + "\n".join(lines) + "\n"

    prompt = f"""You are improving a skill definition for an autonomous agent system.

The skill "{skill.name}" has a tripped circuit breaker (consecutive_failures={skill.consecutive_failures},
utility_score={skill.utility_score:.2f}). Here are the recorded failure reasons:

{failure_summary}

Current skill description:
{skill.description}

Current steps template:
{chr(10).join(f"{i+1}. {s}" for i, s in enumerate(skill.steps_template))}
{peer_context}
Based on the failure pattern, rewrite the skill. Output ONLY valid JSON with these keys:
{{
  "description": "<revised one-sentence description of what this skill does>",
  "steps_template": ["<step 1>", "<step 2>", "..."],
  "trigger_patterns": ["<keyword or phrase that should trigger this skill>", "..."]
}}

Rules:
- Keep steps concrete and actionable (not vague)
- Address the failure reasons directly
- Do not add steps that require external network access if failures were network-related
- 2-5 steps maximum
- trigger_patterns: 3-6 short keyword phrases"""

    try:
        resp = adapter.complete(
            [LLMMessage("user", prompt)],
            max_tokens=600,
        )
        raw = resp.content.strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = "\n".join(raw.split("\n")[1:])
            if raw.endswith("```"):
                raw = raw[: raw.rfind("```")]
        parsed = json.loads(raw)
    except Exception as e:
        if verbose:
            print(f"[evolver] rewrite_skill parse error for {skill.id}: {e}", file=sys.stderr)
        return None

    new_desc = str(parsed.get("description", skill.description)).strip()
    new_steps = [str(s).strip() for s in parsed.get("steps_template", skill.steps_template) if str(s).strip()]
    new_triggers = [str(t).strip() for t in parsed.get("trigger_patterns", skill.trigger_patterns) if str(t).strip()]

    # Pre-save sanity gate (FunSearch pattern: discard invalid candidates before storing)
    # Silently discard if the rewrite fails basic structural requirements.
    if not new_steps or not new_desc:
        log.debug("rewrite_skill discard: empty steps or description for skill %s", skill.id)
        return None
    if len(new_desc) > 400:
        log.debug("rewrite_skill discard: description too long (%d chars) for skill %s",
                  len(new_desc), skill.id)
        return None
    if len(new_steps) > 10:
        log.debug("rewrite_skill discard: too many steps (%d) for skill %s",
                  len(new_steps), skill.id)
        return None
    if not new_triggers:
        # Inherit existing triggers rather than discarding
        new_triggers = skill.trigger_patterns

    # Apply rewrite — set to half_open (probationary) not closed
    skills = load_skills()
    target = next((s for s in skills if s.id == skill.id), None)
    if target is None:
        return None

    target.description = new_desc
    target.steps_template = new_steps
    target.trigger_patterns = new_triggers
    target.consecutive_failures = 0
    target.consecutive_successes = 0
    target.circuit_state = "half_open"  # on probation — not trusted yet
    target.content_hash = compute_skill_hash(target)
    target.failure_notes = target.failure_notes[-2:]  # keep last 2 for history

    _save_skills(skills)

    if verbose:
        print(
            f"[evolver] rewrote skill {skill.id} ({skill.name}) → half_open",
            file=sys.stderr,
        )

    return target


# ---------------------------------------------------------------------------
# Phase 32: Skill synthesis — create a new skill from a successful outcome
# ---------------------------------------------------------------------------

_SYNTHESIZE_SYSTEM = """\
You are an agent that distills successful task executions into reusable skill templates.
Given a completed goal and its outcome summary, synthesize ONE reusable skill definition.
Output ONLY valid JSON with these keys:
{
  "name": "<short snake_case skill name, e.g. web_research_summarise>",
  "description": "<one sentence describing what this skill does>",
  "trigger_patterns": ["<2-5 short keyword phrases that should trigger this skill>"],
  "steps_template": ["<step 1>", "<step 2>", "<step 3>"]
}
Rules:
- 2-5 steps, each concrete and actionable
- trigger_patterns should be distinct phrases likely found in similar goal strings
- description must be one sentence
- name must be unique and descriptive (snake_case)
"""


def synthesize_skill(
    goal: str,
    outcome_summary: str,
    source_loop_id: str = "",
    *,
    adapter=None,
    dry_run: bool = False,
    verbose: bool = False,
) -> "Optional[Skill]":
    """Synthesize a new provisional skill from a completed goal + outcome.

    Called when a successful loop had no matching skill at start — this fills
    the gap so similar goals benefit from the pattern next time.

    Args:
        goal:            The completed goal string.
        outcome_summary: Brief description of what was accomplished.
        source_loop_id:  Loop ID to tag as the source of this skill.
        adapter:         LLMAdapter to use for synthesis.
        dry_run:         If True, synthesize but do not persist.
        verbose:         Print progress to stderr.

    Returns:
        New Skill on success, None if synthesis fails or adapter unavailable.
    """
    log.info("synthesize_skill goal=%r source_loop=%s", goal[:60], source_loop_id)
    try:
        from skills import Skill, save_skill, load_skills, compute_skill_hash
        from llm import LLMMessage
    except ImportError:
        return None

    if adapter is None:
        log.debug("synthesize_skill skipped — no adapter")
        return None

    prompt = (
        f"Completed goal: {goal}\n\n"
        f"Outcome: {outcome_summary[:400]}"
    )

    try:
        resp = adapter.complete(
            [
                LLMMessage("system", _SYNTHESIZE_SYSTEM),
                LLMMessage("user", prompt),
            ],
            max_tokens=512,
            temperature=0.3,
        )
        parsed = extract_json(content_or_empty(resp), dict, log_tag="evolver.synthesize_skill")
    except Exception as e:
        if verbose:
            print(f"[evolver] synthesize_skill parse error: {e}", file=sys.stderr)
        return None

    if not parsed:
        return None

    name = str(parsed.get("name", "")).strip()
    description = str(parsed.get("description", "")).strip()
    trigger_patterns = [str(t) for t in parsed.get("trigger_patterns", [])]
    steps_template = [str(s) for s in parsed.get("steps_template", [])]

    if not name or not description or not steps_template:
        return None

    # Deduplicate — don't create if a skill with this name already exists
    if not dry_run:
        existing_names = {s.name for s in load_skills()}
        if name in existing_names:
            if verbose:
                print(f"[evolver] synthesize_skill: skill '{name}' already exists, skipping", file=sys.stderr)
            return None

    now = datetime.now(timezone.utc).isoformat()
    new_skill = Skill(
        id=__import__("uuid").uuid4().hex[:8],
        name=name,
        description=description,
        trigger_patterns=trigger_patterns or [goal[:60]],
        steps_template=steps_template,
        source_loop_ids=[source_loop_id] if source_loop_id else [],
        created_at=now,
        tier="provisional",
        utility_score=1.0,
        circuit_state="closed",
    )
    new_skill.content_hash = compute_skill_hash(new_skill)

    if not dry_run:
        try:
            save_skill(new_skill)
        except Exception as e:
            if verbose:
                print(f"[evolver] synthesize_skill: save failed: {e}", file=sys.stderr)
            return None

    if verbose:
        print(f"[evolver] synthesized new skill: {new_skill.name} ({new_skill.id})", file=sys.stderr)

    # Captain's log
    try:
        from captains_log import log_event, SKILL_SYNTHESIZED
        log_event(
            event_type=SKILL_SYNTHESIZED,
            subject=new_skill.name,
            summary=f"New skill synthesized from goal: {goal[:80]}.",
            context={"skill_id": new_skill.id, "goal": goal[:200], "outcome": outcome_summary[:200]},
            loop_id=source_loop_id or None,
            related_ids=[f"skill:{new_skill.id}"],
        )
    except Exception:
        pass

    return new_skill


def run_skill_maintenance(
    *,
    adapter=None,
    dry_run: bool = False,
    verbose: bool = False,
) -> dict:
    """Phase 32: auto-promotion, demotion, circuit-breaker-gated rewriting.

    Called from run_evolver() and from heartbeat every N ticks.

    Rewrite policy:
      - Only skills with circuit_state == "open" are eligible
      - A single failure never triggers a rewrite (blip tolerance)
      - CIRCUIT_OPEN_THRESHOLD (3) consecutive failures trips the breaker
      - After rewrite, skill is set to "half_open" (probationary)
      - CIRCUIT_HALFOPEN_RECOVERY (2) consecutive successes closes the breaker

    Returns dict with keys: promoted, demoted, rewritten, rewrite_candidates.
    """
    from skills import (
        maybe_auto_promote_skills,
        maybe_demote_skills,
        skills_needing_rewrite,
        frontier_skills,
        retire_losing_variants,
        create_skill_variant,
    )

    promoted: list = []
    demoted: list = []
    rewritten: list = []
    rewrite_candidates: list = []

    if not dry_run:
        try:
            promoted = maybe_auto_promote_skills()
            if promoted and verbose:
                print(f"[evolver] auto-promoted skills: {promoted}", file=sys.stderr)
        except Exception as e:
            if verbose:
                print(f"[evolver] auto-promote failed: {e}", file=sys.stderr)

        try:
            demoted = maybe_demote_skills()
            if demoted and verbose:
                print(f"[evolver] demoted skills: {demoted}", file=sys.stderr)
        except Exception as e:
            if verbose:
                print(f"[evolver] demotion failed: {e}", file=sys.stderr)

    try:
        candidates = skills_needing_rewrite()
        rewrite_candidates = [s.id for s in candidates]
        if rewrite_candidates and verbose:
            print(f"[evolver] skills with open circuit (rewrite candidates): {rewrite_candidates}", file=sys.stderr)

        if not dry_run and adapter is not None:
            for skill in candidates:
                updated = rewrite_skill(skill, adapter=adapter, verbose=verbose)
                if updated is not None:
                    rewritten.append(skill.id)
    except Exception as e:
        if verbose:
            print(f"[evolver] rewrite scan failed: {e}", file=sys.stderr)

    # Agent0 steal: frontier task targeting — also rewrite skills in the 40-70% zone.
    # These are neither trivially successful nor circuit-broken; they're the hardest
    # to diagnose without trying an improved version. Cap at 2 per cycle to avoid
    # over-spending LLM budget on exploratory rewrites.
    try:
        _frontier = frontier_skills()
        if _frontier and verbose:
            print(f"[evolver] frontier skills (40-70% utility): {[s.id for s in _frontier[:2]]}", file=sys.stderr)
        if not dry_run and adapter is not None:
            for skill in _frontier[:2]:  # max 2 frontier rewrites per cycle
                if skill.id not in rewrite_candidates:  # don't double-rewrite
                    # Pre-score candidate with replay-based fitness oracle before rewriting
                    try:
                        from strategy_evaluator import evaluate_skill as _eval_skill
                        _fitness = _eval_skill(skill)
                        log.info(
                            "evolver frontier_prescore: skill %s fitness=%.2f confidence=%.2f verdict=%s",
                            skill.id, _fitness.fitness_score, _fitness.confidence, _fitness.verdict,
                        )
                        if _fitness.verdict == "PASS" and _fitness.confidence >= 0.3:
                            if verbose:
                                print(
                                    f"[evolver] frontier skill {skill.id} scores PASS — skipping rewrite",
                                    file=sys.stderr,
                                )
                            continue
                    except Exception as _pe:
                        log.debug("strategy pre-score failed (non-fatal): %s", _pe)
                    _updated = rewrite_skill(skill, adapter=adapter, verbose=verbose)
                    if _updated is not None:
                        # A/B variant: frontier rewrites become challengers, not replacements
                        try:
                            _challenger = create_skill_variant(skill, _updated)
                            from skills import save_skill as _save_skill
                            _save_skill(_challenger)
                            rewritten.append(skill.id)
                            log.info(
                                "evolver frontier_ab: created challenger %s for parent %s (utility=%.2f)",
                                _challenger.id, skill.id, skill.utility_score,
                            )
                        except Exception as _ve:
                            log.debug("ab variant save failed (non-fatal): %s", _ve)
    except Exception as _fe:
        log.debug("frontier rewrite scan failed (non-fatal): %s", _fe)

    # A/B retirement: check existing variants for sufficient evidence and retire losers
    try:
        _ab_result = retire_losing_variants(dry_run=dry_run)
        if _ab_result.get("retired") and verbose:
            print(
                f"[evolver] ab_variants: promoted={_ab_result['promoted']} retired={_ab_result['retired']}",
                file=sys.stderr,
            )
    except Exception as _ab_e:
        log.debug("ab variant retirement failed (non-fatal): %s", _ab_e)

    return {
        "promoted": promoted,
        "demoted": demoted,
        "rewritten": rewritten,
        "rewrite_candidates": rewrite_candidates,
    }


def get_friction_summary() -> str:
    """Return a brief human-readable friction summary from the latest inspector run.

    Used by heartbeat tier-2 LLM diagnosis and run_evolver_with_friction().
    Delegates to inspector.get_friction_summary() to avoid duplication.
    """
    try:
        from inspector import get_friction_summary as _inspector_summary
        return _inspector_summary()
    except Exception:
        return ""


def run_evolver_with_friction(
    *,
    outcomes_window: int = 50,
    min_outcomes: int = 3,
    dry_run: bool = False,
    verbose: bool = True,
    notify: bool = False,
    adapter=None,
) -> "EvolverReport":
    """Run meta-evolution cycle enriched with inspection friction findings.

    Same as run_evolver() but prepends friction summary from the latest
    InspectionReport to the LLM prompt, giving the evolver richer context
    for its improvement suggestions.
    """
    import uuid as _uuid

    run_id = _uuid.uuid4().hex[:8]
    started = time.monotonic()

    if verbose:
        print(f"[evolver-friction] run_id={run_id} starting...", file=sys.stderr)

    # Load recent outcomes
    try:
        outcomes = load_outcomes(limit=outcomes_window)
    except Exception as e:
        return EvolverReport(run_id=run_id, outcomes_reviewed=0, skipped=True, skip_reason=str(e))

    if len(outcomes) < min_outcomes:
        return EvolverReport(
            run_id=run_id,
            outcomes_reviewed=len(outcomes),
            skipped=True,
            skip_reason=f"only {len(outcomes)} outcomes (need {min_outcomes})",
        )

    # Load friction summary from latest inspection
    friction_summary = get_friction_summary()

    # Build outcomes summary
    outcomes_summary = _build_outcomes_summary(outcomes)

    # Prepend friction context to the analysis prompt
    if friction_summary and not dry_run:
        enriched_summary = (
            f"Recent quality inspection found these friction patterns:\n{friction_summary}\n\n"
            f"---\n{outcomes_summary}"
        )
    else:
        enriched_summary = outcomes_summary

    # Run LLM analysis (using the enriched summary)
    patterns: List[str] = []
    raw_suggestions: List[dict] = []

    if not dry_run and outcomes:
        try:
            _adapter = adapter
            if _adapter is None:
                _adapter = build_adapter(model=MODEL_MID)
            resp = _adapter.complete(
                [
                    LLMMessage("system", _EVOLVER_SYSTEM),
                    LLMMessage("user", f"Analyze these outcomes:\n\n{enriched_summary}"),
                ],
                max_tokens=2048,
                temperature=0.2,
            )
            data = extract_json(content_or_empty(resp), dict, log_tag="evolver.run_friction")
            if data:
                patterns = safe_list(data.get("failure_patterns", []), element_type=str)
                raw_suggestions = safe_list(data.get("suggestions", []), element_type=dict)
        except Exception as e:
            if verbose:
                print(f"[evolver-friction] LLM analysis failed: {e}", file=sys.stderr)

    # Build Suggestion objects
    suggestions: List[Suggestion] = []
    for i, raw in enumerate(raw_suggestions):
        try:
            suggestions.append(Suggestion(
                suggestion_id=f"{run_id}-{i:02d}",
                category=raw.get("category", "observation"),
                target=raw.get("target", "all"),
                suggestion=raw.get("suggestion", ""),
                failure_pattern=raw.get("failure_pattern", ""),
                confidence=safe_float(raw.get("confidence"), default=0.5, min_val=0.0, max_val=1.0),
                outcomes_analyzed=len(outcomes),
            ))
        except Exception:
            pass

    report = EvolverReport(
        run_id=run_id,
        outcomes_reviewed=len(outcomes),
        suggestions=suggestions,
        failure_patterns=patterns,
        elapsed_ms=int((time.monotonic() - started) * 1000),
    )

    if verbose:
        print(
            f"[evolver-friction] found {len(patterns)} patterns, {len(suggestions)} suggestions",
            file=sys.stderr,
        )

    # Persist suggestions
    if not dry_run and suggestions:
        try:
            _save_suggestions(suggestions)
        except Exception as e:
            if verbose:
                print(f"[evolver-friction] failed to save suggestions: {e}", file=sys.stderr)

    if notify and suggestions and not dry_run:
        _notify_telegram(report)

    # Phase 32: skill maintenance on every evolver run
    try:
        _skill_maint = run_skill_maintenance(
            adapter=adapter, dry_run=dry_run, verbose=verbose
        )
        if any(_skill_maint.get(k) for k in ("promoted", "demoted", "rewritten")):
            if verbose:
                print(f"[evolver] skill maint: {_skill_maint}", file=sys.stderr)
    except Exception:
        pass

    report.elapsed_ms = int((time.monotonic() - started) * 1000)
    return report


# ---------------------------------------------------------------------------
# CLI entry point (poe-evolver)
# ---------------------------------------------------------------------------

def main() -> int:
    """CLI entry point for poe-evolver."""
    import argparse

    parser = argparse.ArgumentParser(description="Poe meta-evolver — analyze outcomes, manage suggestions")
    subparsers = parser.add_subparsers(dest="cmd")

    # Default: run evolver analysis
    run_p = subparsers.add_parser("run", help="Run evolver analysis on recent outcomes")
    run_p.add_argument("--dry-run", action="store_true", help="Analyze without writing suggestions")
    run_p.add_argument("--min-outcomes", type=int, default=3)
    run_p.add_argument("--window", type=int, default=50)
    run_p.add_argument("--notify", action="store_true")
    run_p.add_argument("--format", choices=["text", "json"], default="text")

    # List pending suggestions
    subparsers.add_parser("list", help="List pending (unapplied) suggestions")

    # Apply pending suggestions
    apply_p = subparsers.add_parser("apply", help="Apply pending suggestions (human-in-loop)")
    apply_p.add_argument("--all", action="store_true", help="Apply all pending (no confirmation)")
    apply_p.add_argument("--dry-run", action="store_true", help="Show what would be applied without doing it")
    apply_p.add_argument("id", nargs="?", help="Suggestion ID to apply (omit for interactive mode)")

    args = parser.parse_args()

    if args.cmd == "list" or args.cmd is None:
        # List pending suggestions (also default when no subcommand)
        pending = list_pending_suggestions(limit=50)
        if not pending:
            print("No pending suggestions.")
            return 0
        print(f"\nPending suggestions ({len(pending)}):\n")
        for s in pending:
            print(f"  [{s.suggestion_id}] {s.category:15s} conf={s.confidence:.0%}  {s.suggestion[:80]}")
        return 0

    if args.cmd == "apply":
        pending = list_pending_suggestions(limit=50)
        if not pending:
            print("No pending suggestions to apply.")
            return 0

        to_apply = pending
        if hasattr(args, "id") and args.id:
            to_apply = [s for s in pending if s.suggestion_id == args.id]
            if not to_apply:
                print(f"Suggestion {args.id!r} not found in pending list.")
                return 1

        if args.dry_run:
            print(f"dry_run: would apply {len(to_apply)} suggestion(s):")
            for s in to_apply:
                print(f"  [{s.suggestion_id}] {s.category}: {s.suggestion[:100]}")
            return 0

        if not getattr(args, "all", False):
            # Interactive review
            applied = 0
            for s in to_apply:
                print(f"\n[{s.suggestion_id}] {s.category} (conf={s.confidence:.0%})")
                print(f"  {s.suggestion}")
                resp = input("Apply? [y/N/q]: ").strip().lower()
                if resp == "q":
                    break
                if resp == "y":
                    if apply_suggestion(s.suggestion_id):
                        print(f"  Applied.")
                        applied += 1
                    else:
                        print(f"  Apply failed (gate blocked or not found).")
            print(f"\nApplied {applied} suggestion(s).")
        else:
            applied = sum(1 for s in to_apply if apply_suggestion(s.suggestion_id))
            print(f"Applied {applied}/{len(to_apply)} suggestions.")
        return 0

    # run subcommand
    report = run_evolver(
        outcomes_window=getattr(args, "window", 50),
        min_outcomes=getattr(args, "min_outcomes", 3),
        dry_run=getattr(args, "dry_run", False),
        notify=getattr(args, "notify", False),
    )
    fmt = getattr(args, "format", "text")
    if fmt == "json":
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(report.summary())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
