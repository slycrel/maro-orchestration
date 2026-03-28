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
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

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
    try:
        from orch import orch_root
        d = orch_root() / "memory"
        d.mkdir(parents=True, exist_ok=True)
        return d / "suggestions.jsonl"
    except Exception:
        return Path.cwd() / "memory" / "suggestions.jsonl"


def _dynamic_constraints_path() -> Path:
    """Path to evolver-generated dynamic constraint patterns."""
    try:
        from orch import orch_root
        d = orch_root() / "memory"
        d.mkdir(parents=True, exist_ok=True)
        return d / "dynamic-constraints.jsonl"
    except Exception:
        return Path.cwd() / "memory" / "dynamic-constraints.jsonl"


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

    try:
        if category == "skill_pattern":
            # Write or update the skill in skills.jsonl
            from skills import load_skills, save_skill, Skill
            import uuid as _uuid
            skills = load_skills()
            existing = next((s for s in skills if s.name == target or s.id == target), None)
            if existing is not None:
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

    except Exception as e:
        print(f"[evolver] _apply_suggestion_action({category}) failed: {e}", file=sys.stderr)


def apply_suggestion(suggestion_id: str) -> bool:
    """Mark a suggestion as applied=True by rewriting suggestions.jsonl.

    Phase 14: For suggestions with category == "skill_pattern", runs the
    unit-test gate via validate_skill_mutation() before applying. If the gate
    blocks the mutation, sets status to "gate_blocked" instead of "applied".

    Returns True if the suggestion was found and updated, False otherwise.
    """
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
                if d.get("category") == "skill_pattern" and validate_skill_mutation is not None:
                    gate_result = _run_skill_test_gate(d)
                    if gate_result is not None and gate_result.get("blocked"):
                        d["applied"] = False
                        d["status"] = "gate_blocked"
                        d["block_reason"] = gate_result.get("block_reason", "test gate blocked mutation")
                    else:
                        d["applied"] = True
                        d.pop("status", None)
                        _apply_suggestion_action(d)
                else:
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

        # Run the gate (heuristic path — no adapter in apply_suggestion)
        result = validate_skill_mutation(original_skill, mutated_skill, adapter=None)
        return {"blocked": result.blocked, "block_reason": result.block_reason}

    except Exception as e:
        if __debug__:
            print(f"[evolver] _run_skill_test_gate failed: {e}", file=sys.stderr)
        return None


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
    """Summarize outcomes for LLM analysis."""
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

    return "\n".join(lines)


def _llm_analyze(outcomes: List[Any], *, dry_run: bool = False) -> tuple[List[str], List[dict]]:
    """Ask LLM to identify patterns and suggest improvements. Returns (patterns, raw_suggestions)."""
    if dry_run or not outcomes:
        return [], []

    try:
        adapter = build_adapter(model=MODEL_MID)
        summary = _build_outcomes_summary(outcomes)
        resp = adapter.complete(
            [
                LLMMessage("system", _EVOLVER_SYSTEM),
                LLMMessage("user", f"Analyze these outcomes:\n\n{summary}"),
            ],
            max_tokens=2048,
            temperature=0.2,
        )
        content = resp.content.strip()
        # Extract JSON from response
        start = content.find("{")
        end = content.rfind("}") + 1
        if start >= 0 and end > start:
            data = json.loads(content[start:end])
            patterns = data.get("failure_patterns", [])
            raw_suggestions = data.get("suggestions", [])
            return patterns, raw_suggestions
    except Exception as e:
        if __debug__:
            print(f"[evolver] LLM analysis failed: {e}", file=sys.stderr)
    return [], []


# ---------------------------------------------------------------------------
# Core run
# ---------------------------------------------------------------------------

def run_evolver(
    *,
    outcomes_window: int = 50,
    min_outcomes: int = 3,
    dry_run: bool = False,
    verbose: bool = True,
    notify: bool = False,
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
                confidence=float(raw.get("confidence", 0.5)),
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
        print(f"[evolver] found {len(patterns)} patterns, {len(suggestions)} suggestions", file=sys.stderr)

    # Persist suggestions
    if not dry_run and suggestions:
        try:
            _save_suggestions(suggestions)
        except Exception as e:
            if verbose:
                print(f"[evolver] failed to save suggestions: {e}", file=sys.stderr)

    # Auto-apply high-confidence suggestions (closes the feedback loop)
    auto_applied = 0
    if not dry_run and suggestions:
        for s in suggestions:
            if s.confidence >= 0.8 and not s.applied:
                if apply_suggestion(s.suggestion_id):
                    auto_applied += 1
        if verbose and auto_applied:
            print(f"[evolver] auto-applied {auto_applied} high-confidence suggestions", file=sys.stderr)

    # Telegram notification
    if notify and suggestions and not dry_run:
        _notify_telegram(report)

    report.elapsed_ms = int((time.monotonic() - started) * 1000)

    # Phase 17: check if router retraining is needed
    try:
        from router import maybe_retrain
        maybe_retrain()
    except Exception:
        pass

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

    prompt = f"""You are improving a skill definition for an autonomous agent system.

The skill "{skill.name}" has a tripped circuit breaker (consecutive_failures={skill.consecutive_failures},
utility_score={skill.utility_score:.2f}). Here are the recorded failure reasons:

{failure_summary}

Current skill description:
{skill.description}

Current steps template:
{chr(10).join(f"{i+1}. {s}" for i, s in enumerate(skill.steps_template))}

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
    new_steps = [str(s) for s in parsed.get("steps_template", skill.steps_template)]
    new_triggers = [str(t) for t in parsed.get("trigger_patterns", skill.trigger_patterns)]

    if not new_steps or not new_desc:
        return None

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
    try:
        from skills import Skill, save_skill, load_skills, compute_skill_hash
        from llm import LLMMessage
    except ImportError:
        return None

    if adapter is None:
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
        raw = resp.content.strip()
        if raw.startswith("```"):
            raw = "\n".join(raw.split("\n")[1:])
            if raw.endswith("```"):
                raw = raw[: raw.rfind("```")]
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start < 0 or end <= start:
            return None
        parsed = json.loads(raw[start:end])
    except Exception as e:
        if verbose:
            print(f"[evolver] synthesize_skill parse error: {e}", file=sys.stderr)
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
            content = resp.content.strip()
            start = content.find("{")
            end = content.rfind("}") + 1
            if start >= 0 and end > start:
                data = json.loads(content[start:end])
                patterns = data.get("failure_patterns", [])
                raw_suggestions = data.get("suggestions", [])
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
                confidence=float(raw.get("confidence", 0.5)),
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
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Poe meta-evolver")
    parser.add_argument("--dry-run", action="store_true", help="Analyze without writing suggestions")
    parser.add_argument("--min-outcomes", type=int, default=3, help="Minimum outcomes needed to run")
    parser.add_argument("--window", type=int, default=50, help="How many recent outcomes to analyze")
    parser.add_argument("--notify", action="store_true", help="Send Telegram summary")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    args = parser.parse_args()

    report = run_evolver(
        outcomes_window=args.window,
        min_outcomes=args.min_outcomes,
        dry_run=args.dry_run,
        notify=args.notify,
    )
    if args.format == "json":
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(report.summary())
