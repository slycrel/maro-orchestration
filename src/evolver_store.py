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
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Store-hygiene helpers shared with memory_ledger/knowledge_web (2026-08-17
# silent-drop arc): announced byte-level reads so one torn byte costs one
# row — not an empty corpus (load/get/save-dedup here) and not an uncaught
# UnicodeDecodeError into callers (is_applied/apply/revert here; a decode
# error is a ValueError, invisible to `except OSError`). Probed live against
# this module before converting. loads_clean refuses byte-tainted lines so
# the keyed-merge rewrites never launder one into clean escapes.
from jsonl_utils import (
    loads_clean as _loads_clean,
    read_jsonl_announced as _read_store,
    read_rows_as as _rows_as,
)

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
    # Review lifecycle. apply_suggestion has written these into the JSON since
    # the held_for_review gate landed, but they were absent from the dataclass,
    # so from_dict dropped them and every reader that went through Suggestion
    # (CLI --list included) was blind to whether a row was held, why, or
    # whether a human had already dealt with it.
    #   status       — "" (never gated) | "held_for_review" |
    #                  "pending_human_review" | "action_failed" | "dismissed"
    #   block_reason — what to do about it, in words, for the operator
    #   dismissed_at — ISO stamp; the exit path. Dismissed rows leave the
    #                  pending list and are never re-surfaced, but stay on disk.
    status: str = ""
    block_reason: str = ""
    dismissed_at: str = ""
    # Set by scanners whose output is a READING of a live check rather than a
    # durable insight ("calibration:observation", "drift:closure_rate"). The
    # playbook entry it produces is an alarm: re-readings replace it, and it
    # expires when the check stops firing. Empty = durable insight.
    playbook_key: str = ""
    # new_guardrail only: the regex the guardrail actually matches step text
    # with. Separate from `suggestion` because the two are different things —
    # `suggestion` is prose for the playbook, this is a pattern for
    # constraint.py. Writing prose into the pattern slot produced guardrails
    # that could never fire (2026-08-04). Empty = no constraint row written.
    pattern: str = ""

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
            "pattern": self.pattern,
            "status": self.status,
            "block_reason": self.block_reason,
            "dismissed_at": self.dismissed_at,
            "playbook_key": self.playbook_key,
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
            # Plain json.loads is deliberate here (2026-08-17 review r1):
            # this is a single-object counter file fully rewritten each call
            # — a torn byte costs a counter reset, not a laundered record.
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
    suggestions = _rows_as(p, "load_suggestions", Suggestion.from_dict)
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
    for d in _read_store(p, "get_suggestion"):
        if d.get("suggestion_id") == suggestion_id:
            try:
                return Suggestion.from_dict(d)
            except Exception as exc:
                log.warning("get_suggestion: row %s is JSON but not loadable "
                            "as Suggestion (%s: %s) — treating as absent",
                            suggestion_id, type(exc).__name__, exc)
                return None
    return None


def _content_key(d: Dict[str, Any]) -> tuple:
    """Identity of a suggestion's finding, independent of its suggestion_id.

    Scans re-derive from their inputs every finalize, and some (calibration)
    mint a fresh uuid per derivation — so id equality can't detect "same
    finding again". Content equality can: if the input stream moved, the
    derived text moves with it (thresholds, counts) and the row saves.
    """
    return (
        str(d.get("category", "")),
        str(d.get("target", "")),
        str(d.get("suggestion", "")).strip(),
    )


def _save_suggestions(suggestions: List[Suggestion]) -> None:
    """Append rows, skipping any whose finding is already on disk.

    Dismissed and applied rows count as "already have it" — re-deriving
    identical content from an unmoved input must not resurrect a suggestion
    someone already reviewed (2026-08-06 live-writer census item 4: 81
    duplicate calibration-* rows from a stream frozen since 2026-07-03).
    """
    p = _suggestions_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    # Announced read (2026-08-17): the old blind scan meant one torn byte
    # emptied `seen` and every re-derived suggestion resurrected as a
    # duplicate — the exact 81-duplicate bug this dedup was built to end
    # (probed: 2 copies after one torn byte + one re-save).
    seen: set = set()
    for d in _read_store(p, "_save_suggestions"):
        seen.add(_content_key(d))
    from file_lock import locked_append
    for s in suggestions:
        key = _content_key(s.to_dict())
        if key in seen:
            continue
        seen.add(key)
        locked_append(p, json.dumps(s.to_dict()))


def list_pending_suggestions(limit: int = 20) -> List[Suggestion]:
    """Return suggestions awaiting a decision, newest first.

    Dismissed rows are excluded — a review surface nobody can clear is the
    thing that turned the playbook into permanent every-run context.
    """
    all_suggestions = load_suggestions(limit=1000)
    pending = [s for s in all_suggestions
               if not s.applied and s.status != "dismissed"]
    return pending[:limit]


def dismiss_suggestion(suggestion_id: str, reason: str = "") -> bool:
    """Mark a suggestion reviewed-and-declined. The other exit from pending.

    Nothing is deleted: the row keeps its text, its provenance, and now a
    dismissal stamp. It simply stops being asked about.
    """
    p = _suggestions_path()
    if not p.exists():
        return False
    found = False

    def _merge(old: str) -> str:
        nonlocal found
        out = []
        for line in old.splitlines():
            s = line.strip()
            if not s:
                continue
            try:
                # loads_clean: a byte-tainted line never id-matches — it is
                # re-emitted verbatim below, never re-dumped as clean escapes.
                row = _loads_clean(s)
            except Exception:
                out.append(s)
                continue
            if row.get("suggestion_id") == suggestion_id and not row.get("applied"):
                row["status"] = "dismissed"
                row["dismissed_at"] = datetime.now(timezone.utc).isoformat()
                if reason:
                    row["block_reason"] = reason
                found = True
                out.append(json.dumps(row))
            else:
                out.append(s)
        return "\n".join(out) + "\n" if out else ""

    from file_lock import locked_rmw
    locked_rmw(p, _merge)
    if found:
        log.info("dismiss_suggestion id=%s reason=%s", suggestion_id, reason or "-")
    return found


def suggestion_is_applied(suggestion_id: str) -> bool:
    """Read the durable post-gate state for one suggestion."""
    p = _suggestions_path()
    if not p.exists():
        return False
    for row in _read_store(p, "suggestion_is_applied"):
        if row.get("suggestion_id") == suggestion_id:
            return row.get("applied") is True
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
                # Mint the created skill's id HERE so the audit row
                # carries it (adversarial r17, two seats, HIGH, probed):
                # a change row holding only {"type": "skill_create"}
                # forced rollback to match by mutable name-or-id, which
                # deleted every same-name skill — including an
                # operator's independent record.
                import uuid as _uuid_pre
                before_state = {"type": "skill_create",
                                "created_skill_id": _uuid_pre.uuid4().hex[:8]}
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
                # Create a new provisional skill from the suggestion text.
                # The id was minted at before_state capture so the audit
                # row names it (r17); fall back to a fresh one if the
                # capture path was skipped.
                _pre_id = (before_state or {}).get("created_skill_id") \
                    if isinstance(before_state, dict) else None
                new_skill = Skill(
                    id=_pre_id or _uuid.uuid4().hex[:8],
                    name=target or f"evolver-skill-{suggestion_id}",
                    description=suggestion_text[:500],
                    trigger_patterns=[target] if target and target != "all" else [],
                    steps_template=[suggestion_text[:200]],
                    source_loop_ids=[suggestion_id],
                    created_at=datetime.now(timezone.utc).isoformat(),
                    tier="provisional",
                    utility_score=confidence,
                    origin="synthesized",
                )
                save_skill(new_skill)

        elif category == "prompt_tweak":
            # Record as a tiered lesson so it gets injected into future prompts.
            # Deliberately NO mint-grounding stamp (slice-2 census,
            # 2026-08-16): apply-time is not observe-time — the suggestion
            # was minted by an evolver scan long before this apply runs, so
            # there is no minting-run event log to join against here; and
            # suggestion text is imperative advice, which the claim-shape
            # discipline never stamps anyway.
            if record_tiered_lesson is None or MemoryTier is None:
                raise RuntimeError("tiered lesson writer unavailable")
            recorded_lesson = record_tiered_lesson(
                lesson_text=suggestion_text,
                task_type=target if target and target != "all" else "general",
                outcome="evolver_suggestion",
                source_goal=f"evolver-{suggestion_id}",
                tier=MemoryTier.MEDIUM,
                confidence=confidence,
                # §5 cut B: producer stamp — makes evolver traces
                # Δ-measurable as a class (delta_replay --origin evolver).
                # NOT provisional: evolver suggestions have their own
                # behavioral verify lifecycle (EVOLVER_VERDICT), and this
                # category exists to be injected.
                minted_by="evolver",
            )
            if getattr(recorded_lesson, "lesson_id", "") == "rejected":
                raise RuntimeError("tiered lesson writer rejected the suggestion")

        elif category == "new_guardrail":
            # Append to dynamic-constraints.jsonl — loaded by constraint.py at
            # runtime, which matches `pattern` as a REGEX against step text.
            # The suggestion prose used to be written into that slot, so every
            # guardrail was either an unmatchable literal or a re.error the
            # loader dropped (2026-08-04 probe: 1 live row, 0 loaded). No
            # pattern now means no row — the prose still lands in the playbook
            # below, which is the honest home for a guardrail we can't match.
            _pattern = str(d.get("pattern", "") or "").strip()
            if not _pattern:
                log.info(
                    "evolver new_guardrail %s has no matchable pattern — "
                    "guidance only, no constraint row", suggestion_id,
                )
            else:
                try:
                    re.compile(_pattern, re.I)
                except re.error as _pat_exc:
                    log.warning(
                        "evolver new_guardrail %s pattern is not a valid regex "
                        "(%s) — guidance only, no constraint row",
                        suggestion_id, _pat_exc,
                    )
                else:
                    entry = {
                        "pattern": _pattern,
                        "risk": "MEDIUM",
                        "detail": f"evolver guardrail (id={suggestion_id}): {suggestion_text[:80]}",
                        "source": suggestion_id,
                        # Epoch seconds: the TTL check in
                        # constraint._load_dynamic_constraints compares against
                        # time.time(). This was written as an ISO string, so the
                        # comparison raised and the row was silently discarded —
                        # the whole lane, not just its expiry.
                        "added_at": time.time(),
                        "added_at_iso": datetime.now(timezone.utc).isoformat(),
                    }
                    with open(_dynamic_constraints_path(), "a", encoding="utf-8") as f:
                        f.write(json.dumps(entry) + "\n")

        elif category == "sub_mission":
            # Enqueue the suggested goal for execution on the next heartbeat
            # tick. The not-enqueuing case never reaches here — apply_suggestion
            # holds it at the review gate, the same way it holds a guardrail.
            # It used to be appended to the playbook "for human review", which
            # put an unreviewed goal proposal into every director and decompose
            # call at the TOP of the ranking (non-seed section = learned =
            # outranks the curated seed), with no way out. Two lived there
            # unreviewed until the 2026-08-02 rewrite pulled them by hand.
            from handle import enqueue_goal as _enqueue_goal
            _job_id = _enqueue_goal(
                suggestion_text,
                reason=f"evolver signal ({target}): {suggestion_text[:80]}",
            )
            log.info(
                "evolver sub_mission enqueued job_id=%s confidence=%.2f",
                _job_id, confidence,
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
                # No [:200] slice: it clipped entries mid-sentence into
                # permanent every-run context (operator surprise read
                # 2026-08-02, P9/P10/P17). append_to_playbook's 500-char
                # cap with an honest ellipsis is the only truncation.
                append_to_playbook(
                    suggestion_text,
                    section=_section_map.get(category, "Learned"),
                    source=f"evolver:{suggestion_id}",
                    key=str(d.get("playbook_key", "") or ""),
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
    for entry in _read_store(p, "apply_suggestion"):
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
        elif category == "sub_mission":
            # A signal proposes autonomous WORK, so it holds by default — same
            # gate shape as new_guardrail: a manual apply is the review, and
            # evolver.auto_enqueue_signals opts a box into running them
            # unattended. Held rows wait in `maro evolver --list` (where a
            # human sees them once) instead of in every prompt (where nobody
            # ever did).
            _auto_enqueue = False
            try:
                from config import get as _cfg_get
                _auto_enqueue = bool(_cfg_get("evolver.auto_enqueue_signals", False))
            except Exception:
                _auto_enqueue = False
            if manual or _auto_enqueue:
                d["applied"] = _apply_suggestion_action(d)
                if d["applied"]:
                    d.pop("status", None)
                else:
                    d["status"] = "action_failed"
            else:
                d["applied"] = False
                d["status"] = "held_for_review"
                d["block_reason"] = (
                    "signal proposes a new autonomous goal: run it with "
                    "`maro evolver --apply <id>`, or clear it with "
                    "`maro evolver --dismiss <id>` (set config "
                    "evolver.auto_enqueue_signals: true to run signals "
                    "unattended)"
                )
                log.info("evolver: signal held for review: %s",
                         d.get("suggestion", "")[:100])
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
                # loads_clean: a byte-tainted line (locked_rmw's byte-safe
                # read carries it as surrogates) must never id-match — it
                # falls through and is re-emitted verbatim below.
                if _loads_clean(s).get("suggestion_id") == suggestion_id:
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
    entries = _read_store(cl_path, "revert_suggestion")

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
                        restored_id = s.id
                        break
                else:
                    return {"reverted": False, "behavioral": False, "category": category,
                            "detail": f"skill '{target}' not found for rollback"}
                _save_skills(skills, updated_ids={restored_id})
                behavioral = True

            elif state_type == "skill_create":
                # Remove the created skill — by the id the audit row
                # recorded, never by mutable name (adversarial r17, two
                # seats, HIGH, probed: the name-or-id match deleted an
                # operator's independent same-name skill alongside the
                # created one). Legacy change rows that predate
                # created_skill_id keep the name-or-id match, but every
                # removal now archives first — the rollback is
                # recoverable either way (retention decree).
                created_id = before_state.get("created_skill_id", "")
                if created_id:
                    removal = [s for s in skills if s.id == created_id]
                else:
                    removal = [s for s in skills
                               if s.name == target or s.id == target]
                if removal:
                    from skills import _archive_skills
                    _removed = {s.id for s in removal}
                    # Archive BEFORE the delete; _archive_skills raises
                    # on failure, so a failed retention copy aborts the
                    # removal with the live pool untouched.
                    _archive_skills(removal,
                                    reason="evolver_skill_create_reverted")
                    skills = [s for s in skills if s.id not in _removed]
                    # dropped_ids: a deliberate removal must be named
                    # (r16 _save_skills contract).
                    _save_skills(skills, dropped_ids=_removed,
                                 updated_ids=frozenset())
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
                            # loads_clean: a tainted line never matches the
                            # drop key — preserved verbatim below.
                            d = _loads_clean(line)
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
                        # loads_clean: this branch re-dumps EVERY parseable
                        # row, so a tainted-but-valid line would be laundered
                        # into clean escapes — refuse it into the preserve
                        # branch instead.
                        d = _loads_clean(line.strip())
                        if d.get("suggestion_id") == suggestion_id:
                            d["applied"] = False
                            d["status"] = "reverted"
                        new_lines.append(json.dumps(d))
                    except Exception:
                        new_lines.append(line)
                return "\n".join(new_lines) + "\n"

            from file_lock import locked_rmw
            locked_rmw(p, _mark_reverted)
    except Exception as exc:
        # The behavioral revert above COMMITTED; only this bookkeeping
        # failed. Say so, name the store, and let the returned detail
        # carry the contradiction (adversarial r17, Skeptic, probed: a
        # locked_rmw failure here was swallowed by a bare `pass`, so the
        # suggestion stayed applied=True inside a result claiming the
        # revert completed).
        log.warning(
            "revert_suggestion %s: revert applied but suggestions.jsonl "
            "was NOT updated (%s) — the suggestion still reads "
            "applied=True: %s", suggestion_id, _suggestions_path(), exc)
        detail += ("; suggestion store NOT updated — still marked "
                   "applied")

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
                # loads_clean: a byte-tainted line never id-matches — it is
                # re-emitted verbatim below, never re-dumped as clean escapes.
                d = _loads_clean(s)
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
