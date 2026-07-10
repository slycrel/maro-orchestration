"""Evolver suggestion storage + apply/revert engine.

Extracted from evolver.py (Tier 3 refactor split). Owns the durable
suggestions.jsonl store and the apply/revert lifecycle: writing suggestions,
applying their real-world effect (skill mutation, lesson, guardrail, etc.),
and reverting via the change_log.jsonl audit trail.

evolver.py (facade) imports and re-exports everything here so external
callers (cli.py, heartbeat.py, loop_finalize.py, knowledge.py,
harness_optimizer.py, skills.py) continue to work unchanged.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

log = logging.getLogger("maro.evolver")

# Module-level imports for clean test patching
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
    applied_at: str = ""  # ISO timestamp stamped by apply_suggestion()

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
            "applied_at": self.applied_at,
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


def _cadence_path() -> Path:
    """Path to the run-cadence counter (evolver meta-cycle trigger state)."""
    from orch_items import memory_dir
    return memory_dir() / "evolver_cadence.json"


def evolver_cadence_tick(cadence: int) -> bool:
    """Count one run finalization toward the evolver run-cadence.

    Increments the persistent runs-since-evolve counter; when `cadence` is
    set (> 0) and the counter reaches it, resets the counter and returns
    True — the caller then fires run_evolver(). The increment-check-reset is
    a single locked read-modify-write so concurrent finalizations (the
    concurrency-hardening arc allows parallel runs) can't both trigger.

    "App, not systemic" (2026-07-09): this counter is the entire scheduling
    mechanism — the meta-cycle rides run finalizations; no daemon, no timer.
    Callers must not count dry_run runs.
    """
    from file_lock import locked_rmw

    fired = False

    def _bump(old: str) -> str:
        nonlocal fired
        try:
            count = int(json.loads(old).get("runs_since_evolve", 0))
        except Exception:
            count = 0
        count += 1
        if cadence > 0 and count >= cadence:
            fired = True
            count = 0
        return json.dumps({
            "runs_since_evolve": count,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })

    path = _cadence_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    locked_rmw(path, _bump, default="{}")
    return fired


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
    from file_lock import locked_append
    for s in suggestions:
        locked_append(p, json.dumps(s.to_dict()))


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
        from file_lock import locked_append
        locked_append(_cl_path, json.dumps(_cl_entry))
    except Exception:
        pass  # audit trail must never block execution

    try:
        if category == "skill_pattern":
            # Write or update the skill in skills.jsonl
            from skill_types import Skill
            from skills import load_skills, save_skill, _skills_path as _sp
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

        elif category == "sub_mission":
            # Enqueue the suggested goal for execution on the next heartbeat tick.
            # Gated by evolver.auto_enqueue_signals (default False) — opt-in only.
            # When off, the suggestion is logged to playbook for human review.
            try:
                from config import get as _cfg_get
                _auto_enqueue = _cfg_get("evolver.auto_enqueue_signals", False)
                if _auto_enqueue:
                    from handle import enqueue_goal as _enqueue_goal
                    _job_id = _enqueue_goal(
                        suggestion_text,
                        reason=f"evolver signal ({target}): {suggestion_text[:80]}",
                    )
                    log.info(
                        "evolver sub_mission enqueued job_id=%s confidence=%.2f",
                        _job_id, confidence,
                    )
                else:
                    # Not auto-enqueuing — record to playbook so the human can review
                    try:
                        from playbook import append_to_playbook
                        append_to_playbook(
                            f"[Signal] {suggestion_text[:200]}",
                            section="Signals",
                            source=f"evolver:{suggestion_id}",
                        )
                    except Exception:
                        pass
                    log.info(
                        "evolver sub_mission held for review (auto_enqueue_signals=false): %s",
                        suggestion_text[:80],
                    )
            except Exception as _sm_exc:
                log.warning("evolver sub_mission action failed: %s", _sm_exc)

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

        # Update director's playbook with the applied insight
        if category in ("prompt_tweak", "new_guardrail", "observation") and confidence >= 0.7:
            try:
                from playbook import append_to_playbook
                _section_map = {
                    "prompt_tweak": "Execution",
                    "new_guardrail": "Quality",
                    "observation": "Learned",
                }
                append_to_playbook(
                    suggestion_text[:200],
                    section=_section_map.get(category, "Learned"),
                    source=f"evolver:{suggestion_id}",
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

    # Snapshot read (no lock) to find the target. The decision work below —
    # injection scan, skill test gate — can spawn subprocesses and take
    # seconds, so it runs OUTSIDE the critical section. The file update at
    # the end is a keyed merge under the lock, so suggestions appended or
    # updated by concurrent processes in between are preserved.
    d = None
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except Exception:
            continue
        if entry.get("suggestion_id") == suggestion_id:
            d = entry
            break
    if d is None:
        return False

    guard_blocked = False
    # Injection guard: scan suggestion text before applying (fail-closed)
    try:
        from injection_guard import scan_content
        _suggestion_text_for_scan = d.get("suggestion", "")
        _scan = scan_content(_suggestion_text_for_scan, source="internal")
        if not _scan.safe_to_auto_apply:
            d["applied"] = False
            d["status"] = "injection_risk_blocked"
            d["block_reason"] = f"injection_guard: {_scan.findings[0][:120]}"
            log.warning(
                "apply_suggestion: injection risk blocked id=%s risk=%s finding=%s",
                suggestion_id, _scan.risk_level, _scan.findings[0][:80] if _scan.findings else "?",
            )
            guard_blocked = True
    except Exception as _ig_exc:
        # Fail-closed: if the guard itself throws, skip this apply rather
        # than silently applying potentially malicious content.
        log.warning(
            "apply_suggestion: injection_guard scan FAILED — skipping apply "
            "for id=%s to avoid silent pass-through: %s",
            suggestion_id, _ig_exc,
        )
        d["applied"] = False
        d["status"] = "injection_guard_scan_failed"
        guard_blocked = True

    if not guard_blocked:
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
            # Guardrails can permanently block execution paths. Gate on
            # environment + explicit override:
            #   MARO_AUTO_APPLY_GUARDRAILS=0 → always hold for review (prod-safe override)
            #   MARO_AUTO_APPLY_GUARDRAILS=1 → always auto-apply (dev override)
            #   unset → auto-apply in non-prod, hold in prod
            #
            # Session 20 adversarial review finding 3.13: the previous
            # default (hold unless env=1) silently disabled the
            # guardrail self-improvement path everywhere. Most runs are
            # dev/experiment — guardrails should evolve there by default.
            _env_override = os.environ.get("MARO_AUTO_APPLY_GUARDRAILS")
            if _env_override == "1":
                _should_apply = True
            elif _env_override == "0":
                _should_apply = False
            else:
                try:
                    from config import get as _cfg_get
                    _env = str(_cfg_get("environment", "dev")).lower()
                except Exception:
                    _env = "dev"
                _should_apply = _env != "production"

            if _should_apply:
                d["applied"] = True
                _apply_suggestion_action(d)
                log.info("evolver: auto-applied new_guardrail (env=%s): %s",
                         _env_override or "config", d.get("suggestion", "")[:100])
            else:
                d["applied"] = False
                d["status"] = "held_for_review"
                d["block_reason"] = (
                    "new_guardrail held: production environment (set "
                    "MARO_AUTO_APPLY_GUARDRAILS=1 to override, or change "
                    "config 'environment' from 'production')"
                )
                log.info("evolver: guardrail held for review (production env): %s",
                         d.get("suggestion", "")[:100])
        elif category == "prompt_tweak":
            # Prompt tweaks are lower risk (just a lesson) but log prominently
            d["applied"] = True
            _apply_suggestion_action(d)
            log.info("evolver: auto-applied prompt_tweak: %s", d.get("suggestion", "")[:100])
        elif category == "cost_optimization":
            # No executor exists yet — surface for human review instead of
            # silently marking applied. Previously fell through to else and
            # looked "applied" in logs without any real-world effect.
            d["applied"] = False
            d["status"] = "pending_human_review"
            d["block_reason"] = "cost_optimization has no auto-apply handler; review manually"
            log.info("evolver: cost_optimization held for human review: %s", d.get("suggestion", "")[:100])
        elif category == "crystallization":
            # Stage 2→3 promotion is human-gated by design (KNOWLEDGE_CRYSTALLIZATION.md).
            # Never auto-write to AGENTS.md — surface for Jeremy's review only.
            d["applied"] = False
            d["status"] = "pending_human_review"
            d["block_reason"] = (
                "crystallization requires human review: run `maro-memory canon-candidates` "
                "to inspect and manually promote to AGENTS.md"
            )
            log.info("evolver: crystallization held for human review: %s", d.get("suggestion", "")[:100])
        else:
            # observation, sub_mission, etc. — safe to apply
            d["applied"] = True
            _apply_suggestion_action(d)
        if d.get("applied"):
            # Apply timestamp lives HERE, not (only) in the captain's
            # log. scan_evolver_impact previously had to read
            # EVOLVER_APPLIED log events to learn when a change
            # landed — making the log the source of truth for a
            # system function, which it must not be (captain's log =
            # visibility/data, THREAD_ARCHITECTURE.md).
            d["applied_at"] = datetime.now(timezone.utc).isoformat()

    # Keyed merge under the lock: replace only this suggestion's line.
    # Suggestions appended/updated by concurrent processes between the
    # snapshot read and now are preserved (the old full-snapshot rewrite
    # silently dropped them).
    from file_lock import locked_rmw
    updated_line = json.dumps(d)

    def _merge(old: str) -> str:
        out = []
        replaced = False
        for line in old.splitlines():
            s = line.strip()
            if not s:
                continue
            try:
                if json.loads(s).get("suggestion_id") == suggestion_id:
                    out.append(updated_line)
                    replaced = True
                    continue
            except Exception:
                pass
            out.append(s)
        if not replaced:  # line vanished between snapshot and merge — re-add
            out.append(updated_line)
        return "\n".join(out) + "\n" if out else ""

    locked_rmw(p, _merge)
    return True


def revert_suggestion(suggestion_id: str) -> dict:
    """Revert a previously applied suggestion using the change_log audit trail.

    Reads change_log.jsonl to find the most recent entry for this suggestion_id,
    then reverses the action based on the recorded before_state:

        skill_update    → restore old description from before_state
        skill_create    → remove the skill from skills.jsonl
        lesson_add      → no-op (lessons are append-only; decay handles cleanup)
        guardrail_append → remove the pattern from dynamic-constraints.jsonl

    Also marks the suggestion as applied=False in suggestions.jsonl and logs
    the revert to captain's log.

    Returns:
        dict with keys: reverted (bool), category, detail (str).
    """
    from orch_items import memory_dir

    cl_path = memory_dir() / "change_log.jsonl"
    if not cl_path.exists():
        return {"reverted": False, "category": "", "detail": "no change_log.jsonl found"}

    # Find the matching entry (most recent first)
    entries = []
    for line in cl_path.read_text(encoding="utf-8").splitlines():
        try:
            entries.append(json.loads(line))
        except Exception:
            continue

    match = None
    for entry in reversed(entries):
        if entry.get("suggestion_id") == suggestion_id:
            match = entry
            break

    if not match:
        return {"reverted": False, "category": "", "detail": f"suggestion_id {suggestion_id} not found in change_log"}

    category = match.get("category", "")
    before_state = match.get("before_state") or {}
    target = match.get("target", "")
    detail = ""

    try:
        if category == "skill_pattern":
            from skills import load_skills, _save_skills
            skills = load_skills()
            state_type = before_state.get("type", "")

            if state_type == "skill_update":
                # Restore old description
                old_desc = before_state.get("old_description", "")
                for s in skills:
                    if s.name == target or s.id == target:
                        s.description = old_desc
                        detail = f"restored description for skill '{s.name}'"
                        break
                else:
                    return {"reverted": False, "category": category,
                            "detail": f"skill '{target}' not found for rollback"}
                _save_skills(skills)

            elif state_type == "skill_create":
                # Remove the created skill
                original_len = len(skills)
                skills = [s for s in skills if s.name != target and s.id != target]
                if len(skills) < original_len:
                    _save_skills(skills)
                    detail = f"removed created skill '{target}'"
                else:
                    return {"reverted": False, "category": category,
                            "detail": f"skill '{target}' not found for removal"}

        elif category == "new_guardrail":
            # Remove matching pattern from dynamic-constraints.jsonl
            # (read + filter under the lock — lost-update safe)
            dc_path = _dynamic_constraints_path()
            if dc_path.exists():
                suggestion_text = match.get("suggestion_text", "")
                removed_flag = {"removed": False}

                def _drop_constraint(old: str) -> str:
                    new_lines = []
                    for line in old.splitlines():
                        try:
                            d = json.loads(line)
                            if d.get("source") == f"evolver:{suggestion_id}" or d.get("pattern", "") == suggestion_text[:200]:
                                removed_flag["removed"] = True
                                continue
                        except Exception:
                            pass
                        new_lines.append(line)
                    return "\n".join(new_lines) + "\n" if new_lines else ""

                from file_lock import locked_rmw
                locked_rmw(dc_path, _drop_constraint)
                if removed_flag["removed"]:
                    detail = "removed dynamic constraint"
                else:
                    detail = "dynamic constraint not found (may have expired)"

        elif category == "prompt_tweak":
            detail = "prompt_tweak lessons are append-only; lesson will decay naturally"

        else:
            detail = f"no revert action for category '{category}'"

    except Exception as exc:
        return {"reverted": False, "category": category, "detail": f"revert failed: {exc}"}

    # Mark suggestion as not applied (read + rewrite under the lock)
    try:
        p = _suggestions_path()
        if p.exists():
            def _mark_reverted(old: str) -> str:
                new_lines = []
                for line in old.splitlines():
                    try:
                        d = json.loads(line.strip())
                        if d.get("suggestion_id") == suggestion_id:
                            d["applied"] = False
                            d["status"] = "reverted"
                        new_lines.append(json.dumps(d))
                    except Exception:
                        new_lines.append(line)
                return "\n".join(new_lines) + "\n"

            from file_lock import locked_rmw
            locked_rmw(p, _mark_reverted)
    except Exception:
        pass

    # Captain's log
    try:
        from captains_log import log_event, EVOLVER_REVERTED
        log_event(
            event_type=EVOLVER_REVERTED,
            subject=suggestion_id,
            summary=f"Reverted suggestion {suggestion_id} ({category}): {detail}",
            context={"suggestion_id": suggestion_id, "category": category, "target": target},
        )
    except Exception:
        pass

    log.info("revert_suggestion id=%s category=%s: %s", suggestion_id, category, detail)
    return {"reverted": True, "category": category, "detail": detail}


def _run_skill_test_gate(suggestion_dict: dict) -> Optional[dict]:
    """Run the unit-test gate for a skill_pattern suggestion.

    Returns dict with {blocked: bool, block_reason: str} or None if gate
    cannot be run (e.g., skill not found).
    """
    if validate_skill_mutation is None:
        return None

    try:
        from skill_types import Skill
        from skills import load_skills
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
