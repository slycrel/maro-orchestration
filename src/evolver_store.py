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
    applied_manually: bool = False  # V2 authority provenance; additive only
    # VERIFY_LEARN_ARC V1: which observable this change expects to move, and
    # which direction — e.g. [{"metric": "failure_class_rate", "class": "retry_churn",
    # "direction": "down"}]. Declared at generation time (statically by graduation
    # templates, or by the LLM proposer); absent/empty means no expectation was
    # declared. Read-time interpretation (a class-neutral fallback pair, cadence
    # verdict rendering) is V2's job, not this field's — this is capture only.
    expected_signal: List[dict] = field(default_factory=list)
    # VERIFY_LEARN_ARC V2: cadence-verdict lifecycle state, stamped by
    # verify_applied_suggestions() at evolver cadence. All additive/empty-
    # default so every pre-V2 row rehydrates unchanged.
    #   verified_at      — ISO stamp when a TERMINAL verdict was rendered
    #                      (confirmed / unverifiable / degraded). Empty = still
    #                      pending; the cadence pass keeps re-examining it.
    #   verify_verdict   — "confirmed" | "degraded" | "degraded_needs_review"
    #                      | "unverifiable". The behavioral verdict, distinct
    #                      from `applied` (a reverted row is applied=False AND
    #                      verify_verdict="degraded").
    #   verify_extensions— cadence passes that rendered inconclusive before a
    #                      terminal verdict; parks as unverifiable past the cap.
    verified_at: str = ""
    verify_verdict: str = ""
    verify_extensions: int = 0

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
            "applied_manually": self.applied_manually,
            "expected_signal": self.expected_signal,
            "verified_at": self.verified_at,
            "verify_verdict": self.verify_verdict,
            "verify_extensions": self.verify_extensions,
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


def get_suggestion(suggestion_id: str) -> Optional[Suggestion]:
    """Return the current on-disk row for one suggestion, or None if absent.

    A single-row, uncapped lookup — unlike load_suggestions() this never drops
    the row behind a newest-N window, and it re-reads current state (used by the
    V2 auto-revert guard to re-confirm authority just before an irreversible
    revert, so the decision isn't made off a stale snapshot).
    """
    p = _suggestions_path()
    if not p.exists():
        return None
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("suggestion_id") == suggestion_id:
                try:
                    return Suggestion.from_dict(d)
                except Exception:
                    return None
    except Exception:
        return None
    return None


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


def suggestion_is_applied(suggestion_id: str) -> bool:
    """Read the durable post-gate state for one suggestion."""
    p = _suggestions_path()
    if not p.exists():
        return False
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except Exception:
                continue
            if row.get("suggestion_id") == suggestion_id:
                return row.get("applied") is True
    except OSError:
        return False
    return False


def _apply_suggestion_action(d: dict) -> bool:
    """Execute the real-world effect of an approved suggestion.

    Called from apply_suggestion() after the test gate passes.  Each category
    has a concrete action that closes the feedback loop:

        skill_pattern  → write/update a Skill in skills.jsonl
        prompt_tweak   → record a TieredLesson (medium tier) for future injection
        new_guardrail  → append pattern to memory/dynamic-constraints.jsonl
        observation    → no-op (informational only)

    Never raises. Returns True only when the category's primary action
    completed (including intentional observation no-ops); callers must not
    stamp durable ``applied`` state on False.
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
            if record_tiered_lesson is None or MemoryTier is None:
                raise RuntimeError("tiered lesson writer unavailable")
            recorded_lesson = record_tiered_lesson(
                lesson_text=suggestion_text,
                task_type=target if target and target != "all" else "general",
                outcome="evolver_suggestion",
                source_goal=f"evolver-{suggestion_id}",
                tier=MemoryTier.MEDIUM,
                confidence=confidence,
            )
            if getattr(recorded_lesson, "lesson_id", "") == "rejected":
                raise RuntimeError("tiered lesson writer rejected the suggestion")

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
                from playbook import append_to_playbook
                append_to_playbook(
                    f"[Signal] {suggestion_text[:200]}",
                    section="Signals",
                    source=f"evolver:{suggestion_id}",
                )
                log.info(
                    "evolver sub_mission held for review (auto_enqueue_signals=false): %s",
                    suggestion_text[:80],
                )

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

        return True

    except Exception as e:
        print(f"[evolver] _apply_suggestion_action({category}) failed: {e}", file=sys.stderr)
        return False


def apply_suggestion(suggestion_id: str, manual: bool = False) -> bool:
    """Mark a suggestion as applied=True by rewriting suggestions.jsonl.

    Phase 14: For suggestions with category == "skill_pattern", runs the
    unit-test gate via validate_skill_mutation() before applying. If the gate
    blocks the mutation, sets status to "gate_blocked" instead of "applied".

    manual=True means a human explicitly asked for this apply (CLI review
    path). That bypasses the evolver.auto_apply hold for guardrails — the
    review IS the gate — but never the injection guard or the skill test
    gate, which protect against bad content regardless of who asks.

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

    # Re-applying a live row must be a no-op. Besides replaying the concrete
    # mutation, a second apply could rewrite applied_manually and corrupt the
    # authority provenance that later decides whether automatic revert is
    # allowed.
    if d.get("applied") is True:
        return True

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
                d["applied"] = _apply_suggestion_action(d)
                if d["applied"]:
                    d.pop("status", None)
                else:
                    d["status"] = "action_failed"
        elif category == "new_guardrail":
            # Guardrails can permanently block execution paths. There is no
            # dev/prod split anymore (2026-07-10 decree: the system always
            # runs with production semantics), so the gate is an explicit
            # opt-in knob rather than an environment inference:
            #   manual apply (CLI review)      → apply (the review is the gate)
            #   MARO_AUTO_APPLY_GUARDRAILS=1   → auto-apply (env override)
            #   MARO_AUTO_APPLY_GUARDRAILS=0   → hold (env override)
            #   config evolver.auto_apply      → default False = held_for_review
            _env_override = os.environ.get("MARO_AUTO_APPLY_GUARDRAILS")
            if manual:
                _should_apply = True
            elif _env_override == "1":
                _should_apply = True
            elif _env_override == "0":
                _should_apply = False
            else:
                try:
                    from config import get as _cfg_get
                    _should_apply = bool(_cfg_get("evolver.auto_apply", False))
                except Exception:
                    _should_apply = False

            if _should_apply:
                d["applied"] = _apply_suggestion_action(d)
                if d["applied"]:
                    d.pop("status", None)
                    log.info("evolver: applied new_guardrail (%s): %s",
                             "manual" if manual else "auto_apply on",
                             d.get("suggestion", "")[:100])
                else:
                    d["status"] = "action_failed"
            else:
                d["applied"] = False
                d["status"] = "held_for_review"
                d["block_reason"] = (
                    "new_guardrail held for review: auto-apply is off by "
                    "default (apply via `maro evolver --apply <id>`, or set "
                    "config evolver.auto_apply: true / "
                    "MARO_AUTO_APPLY_GUARDRAILS=1 to auto-apply)"
                )
                log.info("evolver: guardrail held for review: %s",
                         d.get("suggestion", "")[:100])
        elif category == "prompt_tweak":
            # Prompt tweaks are lower risk (just a lesson) but log prominently
            d["applied"] = _apply_suggestion_action(d)
            if d["applied"]:
                d.pop("status", None)
                log.info("evolver: auto-applied prompt_tweak: %s", d.get("suggestion", "")[:100])
            else:
                d["status"] = "action_failed"
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
            d["applied"] = _apply_suggestion_action(d)
            if d["applied"]:
                d.pop("status", None)
            else:
                d["status"] = "action_failed"
        if d.get("applied"):
            # Apply timestamp lives HERE, not (only) in the captain's
            # log. scan_evolver_impact previously had to read
            # EVOLVER_APPLIED log events to learn when a change
            # landed — making the log the source of truth for a
            # system function, which it must not be (captain's log =
            # visibility/data, THREAD_ARCHITECTURE.md).
            d["applied_at"] = datetime.now(timezone.utc).isoformat()
            d["applied_manually"] = bool(manual)

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
        return {"reverted": False, "behavioral": False, "category": "", "detail": "no change_log.jsonl found"}

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
        return {"reverted": False, "behavioral": False, "category": "", "detail": f"suggestion_id {suggestion_id} not found in change_log"}

    category = match.get("category", "")
    before_state = match.get("before_state") or {}
    target = match.get("target", "")
    detail = ""
    # `behavioral` = did we actually undo the change's effect on behavior, not
    # just flip bookkeeping? True only for structural rollbacks (skill restore/
    # remove, guardrail removal). Append-only categories (prompt_tweak/lesson)
    # and the no-op `else` branch mark applied=False but leave the behavioral
    # influence in place until it decays — callers that rely on a real undo
    # (VERIFY_LEARN_ARC V2 auto-revert) must key off this, not `reverted`.
    behavioral = False

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
                    return {"reverted": False, "behavioral": False, "category": category,
                            "detail": f"skill '{target}' not found for rollback"}
                _save_skills(skills)
                behavioral = True

            elif state_type == "skill_create":
                # Remove the created skill
                original_len = len(skills)
                skills = [s for s in skills if s.name != target and s.id != target]
                if len(skills) < original_len:
                    _save_skills(skills)
                    detail = f"removed created skill '{target}'"
                    behavioral = True
                else:
                    return {"reverted": False, "behavioral": False, "category": category,
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
                    behavioral = True
                else:
                    detail = "dynamic constraint not found (may have expired)"

        elif category == "prompt_tweak":
            detail = "prompt_tweak lessons are append-only; lesson will decay naturally"

        else:
            detail = f"no revert action for category '{category}'"

    except Exception as exc:
        return {"reverted": False, "behavioral": False, "category": category, "detail": f"revert failed: {exc}"}

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

    log.info("revert_suggestion id=%s category=%s behavioral=%s: %s",
             suggestion_id, category, behavioral, detail)
    return {"reverted": True, "behavioral": behavioral, "category": category, "detail": detail}


def stamp_verification(
    suggestion_id: str,
    *,
    verdict: Optional[str] = None,
    verified_at: Optional[str] = None,
    extensions: Optional[int] = None,
) -> bool:
    """Durably record VERIFY_LEARN_ARC V2 cadence-verdict state on a suggestion.

    Keyed-merge write under the lock (same discipline as apply_suggestion):
    suggestions appended/updated by concurrent finalizations between read and
    write are preserved. Only the fields explicitly passed are updated:

        verdict     → verify_verdict (terminal label, or interim "" cleared)
        verified_at → the terminal stamp; pass a truthy ISO string to mark the
                      row TERMINAL (no longer re-examined). Leave None for an
                      interim inconclusive re-check so the row stays pending.
        extensions  → verify_extensions counter (absolute value, not a delta).

    Never touches `applied` — a degraded row is reverted (applied=False) by
    revert_suggestion; the verdict is a separate, orthogonal stamp. Returns
    True if the row was found and rewritten.
    """
    p = _suggestions_path()
    if not p.exists():
        return False

    found = {"hit": False}

    def _merge(old: str) -> str:
        out = []
        for line in old.splitlines():
            s = line.strip()
            if not s:
                continue
            try:
                d = json.loads(s)
            except Exception:
                out.append(s)
                continue
            if d.get("suggestion_id") == suggestion_id:
                found["hit"] = True
                if verdict is not None:
                    d["verify_verdict"] = verdict
                if verified_at is not None:
                    d["verified_at"] = verified_at
                if extensions is not None:
                    d["verify_extensions"] = int(extensions)
                out.append(json.dumps(d))
            else:
                out.append(s)
        return "\n".join(out) + "\n" if out else ""

    from file_lock import locked_rmw
    locked_rmw(p, _merge)
    return found["hit"]


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
