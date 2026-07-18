#!/usr/bin/env python3
# @lat: [[memory-system]]
"""Phase 5: Memory + Learning system for Maro orchestration.

Three memory layers:
1. Session bootstrap: every session loads prior outcomes for context
2. Outcome recording: after each run, record what happened + lessons
3. Reflexion: per-task reflection stored as structured lessons, injected on future similar tasks

File structure (under orch_root()):
    memory/
        YYYY-MM-DD.md          — daily narrative log (append-only)
        outcomes.jsonl          — structured outcome ledger (append-only)
        lessons.jsonl           — structured lessons from reflection (append-only)
        MEMORY.md               — human-readable index + recent highlights

DSPy-style principle: treat lessons as prompt modules. When a similar task
arrives, inject the most relevant lessons. Over time, lessons compound.

Reflexion principle: after each task, reflect on what went well/wrong.
Store the reflection as a structured lesson keyed by task_type + outcome.
On future similar tasks, prepend relevant lessons to the agent's system prompt.

Usage:
    from memory import record_outcome, load_lessons, bootstrap_context
    lessons = load_lessons(task_type="research", limit=5)
    context = bootstrap_context()  # for session start
    record_outcome(goal="...", status="done", summary="...", lessons=["..."])
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
import textwrap
import logging
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, date, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from llm_parse import extract_json, safe_list, content_or_empty

log = logging.getLogger("maro.memory")

# ---------------------------------------------------------------------------
# Re-exports from memory_ledger.py (decomposition Phase 1)
# All data types and CRUD functions live in memory_ledger now.
# Re-exported here for backward compatibility — external code imports from memory.
# ---------------------------------------------------------------------------
from memory_ledger import (  # noqa: F401, E402
    Outcome, OutcomeVerdictStampResult, Lesson, TaskLedgerEntry, CompressedBatch,
    _memory_dir, _outcomes_path, _lessons_path, _daily_path,
    _memory_index_path, _step_traces_path, _task_ledger_path,
    _compressed_outcomes_path, _text_similarity,
    append_task_ledger, load_task_ledger,
    record_step_trace, load_step_traces,
    record_outcome, stamp_outcome_verdict, _append_daily_log,
    annotate_outcome_lessons, load_outcome_by_loop_id,
    _INJECTION_PATTERNS, _lesson_looks_adversarial,
    _store_lesson, _rewrite_lessons_file,
    load_lessons, load_outcomes,
    _save_compressed_batch, load_compressed_batches,
    compress_old_outcomes, _tfidf_rank_batches,
    load_outcomes_with_context, _update_memory_index,
)
from knowledge_web import (  # noqa: F401, E402
    MemoryTier, TieredLesson,
    DECAY_FACTOR, REINFORCE_BONUS, PROMOTE_MIN_SCORE, PROMOTE_MIN_SESSIONS, GC_THRESHOLD,
    CANON_APPLY_THRESHOLD, CANON_TASK_TYPE_MIN,
    _STOP_WORDS, _CITATION_PENALTY, _CONFIDENCE_SINGLE_CALL, _CONFIDENCE_MAJORITY_VOTE,
    _CONFIDENCE_MULTI_SESSION,
    short_set, short_get, short_clear, short_all,
    _tiered_lessons_path, _days_since, decay_score, reinforce_score, _current_date,
    confidence_from_k_samples, _tokenize, _tfidf_rank,
    record_tiered_lesson, _append_tiered_lesson, _reinforce_tiered_lesson,
    load_tiered_lessons, _rewrite_tiered_lessons,
    reinforce_lesson, search_graveyard, forget_lesson, promote_lesson,
    resurrect_archived_lesson, _load_archived_lessons, _lessons_archive_path,
    run_decay_cycle, maybe_consolidate, consolidation_due,
    inject_tiered_lessons, query_lessons,
    _increment_times_applied, _canon_stats_path, _record_canon_hit,
    _load_canon_stats, get_canon_candidates, memory_status,
)
from knowledge_lens import (  # noqa: F401, E402
    StandingRule, Hypothesis, Decision, VerificationOutcome,
    RULE_PROMOTE_CONFIRMATIONS, DECISION_SEARCH_LIMIT,
    _ALIGNMENT_THRESHOLD_BASE, _ALIGNMENT_THRESHOLD_MIN, _ALIGNMENT_THRESHOLD_MAX,
    _CALIBRATION_MIN_SAMPLES,
    _rules_path, _hypotheses_path, _decisions_path, _verification_outcomes_path,
    load_standing_rules, load_hypotheses, _rewrite_rules, _rewrite_hypotheses,
    observe_pattern, contradict_pattern, check_contradiction, inject_standing_rules,
    contested_rules, refight_rule,
    record_decision, search_decisions, inject_decisions,
    record_verification, load_verification_outcomes,
    verification_accuracy, calibrated_alignment_threshold,
)

# Hybrid retrieval (BM25 + RRF) — graceful fallback to TF-IDF if unavailable
try:
    from hybrid_search import hybrid_rank as _hybrid_rank
    _USE_HYBRID = True
except ImportError:  # pragma: no cover
    _USE_HYBRID = False


# NOTE: Data types (Outcome, Lesson, TaskLedgerEntry, CompressedBatch) and
# all CRUD functions (record_outcome, load_lessons, load_outcomes, etc.)
# have been extracted to memory_ledger.py and are re-exported above.

# ---------------------------------------------------------------------------
# Session bootstrap
# ---------------------------------------------------------------------------

def bootstrap_context(*, max_outcomes: int = 5, max_lessons: int = 10) -> str:
    """Build a context string for session startup.

    Returns a string that can be prepended to the system prompt to give
    the agent memory of recent work and accumulated lessons.
    """
    parts = []

    # Recent outcomes
    outcomes = load_outcomes(limit=max_outcomes)
    if outcomes:
        parts.append("## Recent Work")
        for o in outcomes[:max_outcomes]:
            # Verdict-preferred (SF-2): judged goal-not-achieved renders as a
            # failure even when the loop finished.
            from outcome_policy import is_verdict_pending
            icon = ("?" if is_verdict_pending(o) else
                    ("✓" if (o.status == "done" and o.goal_achieved is not False) else "✗"))
            verdict_note = " [goal NOT achieved]" if o.goal_achieved is False else ""
            parts.append(f"- {icon} {o.goal[:60]} ({o.task_type}, {o.recorded_at[:10]}){verdict_note}: {o.summary[:80]}")

    # Key lessons (high-confidence, recent)
    lessons = load_lessons(limit=max_lessons)
    high_conf = [l for l in lessons if l.confidence >= 0.7]
    if high_conf:
        parts.append("\n## Accumulated Lessons")
        for l in high_conf[:max_lessons]:
            parts.append(f"- [{l.task_type}] {l.lesson}")

    if not parts:
        return ""

    return "# Memory Context (from prior sessions)\n\n" + "\n".join(parts)


_MAX_LESSON_INJECT_CHARS = 1200  # cap total injected lesson text to avoid token spikes


def inject_lessons_for_task(task_type: str, goal: str, max_lessons: int = 3) -> str:
    """Build a lessons injection string for a specific task type.

    Used to prepend relevant lessons to an agent's system prompt.
    Capped at _MAX_LESSON_INJECT_CHARS to prevent token spikes as lessons accumulate.
    """
    lessons = load_lessons(task_type=task_type, limit=max_lessons)
    if not lessons:
        # Try general lessons
        lessons = load_lessons(task_type="general", limit=max_lessons)

    if not lessons:
        return ""

    # Time-blindness hook (a): flag-gated age suffix from the stored
    # timestamp; absent/unparsable timestamps render byte-identically.
    from age_stamp import age_stamps_enabled, age_suffix
    _stamp_ages = age_stamps_enabled()

    lines = ["## Lessons from Prior Runs (apply these)"]
    for l in lessons:
        # Verdict-preferred (SF-2): a lesson from a run judged goal-not-achieved
        # is a failure lesson even though the run's process status was "done".
        icon = "✗" if getattr(l, "goal_achieved", None) is False else ("✓" if l.outcome == "done" else "✗")
        _suffix = (age_suffix(getattr(l, "recorded_at", "") or "")
                   if _stamp_ages else "")
        lines.append(f"- {icon} {l.lesson}{_suffix}")
    result = "\n".join(lines)
    if len(result) > _MAX_LESSON_INJECT_CHARS:
        result = result[:_MAX_LESSON_INJECT_CHARS].rsplit("\n", 1)[0]
    return result


# ---------------------------------------------------------------------------
# Reflexion: post-run lesson extraction
# ---------------------------------------------------------------------------

_REFLECT_SYSTEM = textwrap.dedent("""\
    You are a meta-learning agent. After each completed run, extract durable lessons.
    A lesson is a generalizable insight that would improve future similar runs.
    Good lessons are: specific, actionable, and generalize beyond this one case.
    Bad lessons are: too specific to this one task, or trivially obvious.

    Lesson types (pick the best fit for each lesson):
    - "execution": how to carry out steps more effectively (tools, sequencing, parallelism)
    - "planning": how to decompose or scope goals better
    - "recovery": how to handle failure, retries, or stuck states
    - "verification": how to validate output quality or catch errors early
    - "cost": how to reduce token spend or latency without sacrificing quality

    Respond with a JSON array of 1-3 lesson objects, each with "lesson" (string) and "type" (one of the above).
    Example: [{"lesson": "Research tasks produce better output when the goal includes success criteria", "type": "planning"},
              {"lesson": "Stuck detection triggers prematurely on research tasks that need multiple iterations", "type": "recovery"}]
""").strip()


_LESSON_TYPES = frozenset({"execution", "planning", "recovery", "verification", "cost"})


def extract_lessons_via_llm(
    goal: str,
    status: str,
    result_summary: str,
    task_type: str,
    *,
    adapter=None,
    dry_run: bool = False,
    return_typed: bool = False,
    goal_achieved: Optional[bool] = None,
    raise_on_failure: bool = False,
) -> "List":
    """Use LLM to extract generalizable lessons from a completed run.

    Phase 59 NeMo steals:
    - S1: Returns typed lessons (lesson_type per lesson) when return_typed=True.
    - S2: Seed-reader bootstrapping — prepends top-1 long-tier lesson as style guide.
    - S3: ATIF feedback — passes times_reinforced + times_applied stats into prompt.

    Args:
        return_typed: If True, return List[Tuple[str, str]] (lesson_text, lesson_type).
                      If False (default), return List[str] for backward compat.

    Returns list of lesson strings (or typed tuples). Falls back to empty list on failure.
    """
    if dry_run or adapter is None:
        # Generate a dry-run lesson. Verdict-preferred framing (SF-2): a run
        # judged goal-not-achieved is a failure regardless of process status.
        icon = "succeeded" if (status == "done" and goal_achieved is not False) else "failed"
        lesson = f"[dry-run lesson] {task_type} task {icon}: {goal[:40]}"
        return [(lesson, "execution")] if return_typed else [lesson]

    from llm import LLMMessage

    # S2: Seed-reader bootstrapping — load top-1 long-tier lesson as style example
    seed_block = ""
    try:
        seed_lessons = load_tiered_lessons(MemoryTier.LONG, task_type=task_type, min_score=0.7, limit=1)
        if seed_lessons:
            seed = seed_lessons[0]
            seed_block = (
                f"\nHigh-quality lesson example (emulate this style and specificity):\n"
                f'  {{"lesson": "{seed.lesson[:120]}", "type": "{seed.lesson_type or "execution"}"}}'
                f"  [reinforced {seed.times_reinforced}x, applied {seed.times_applied}x, score={seed.score:.2f}]"
            )
    except Exception:
        pass

    # S3: ATIF feedback — pass reinforcement stats for this task_type
    atif_block = ""
    try:
        recent = load_tiered_lessons(MemoryTier.MEDIUM, task_type=task_type, min_score=0.0, limit=5)
        if recent:
            avg_reinforced = sum(l.times_reinforced for l in recent) / len(recent)
            avg_applied = sum(l.times_applied for l in recent) / len(recent)
            atif_block = (
                f"\nRecent lesson stats for task_type={task_type!r}: "
                f"avg_reinforced={avg_reinforced:.1f}, avg_applied={avg_applied:.1f}. "
                f"Prefer lessons that generalize (high applied count)."
            )
    except Exception:
        pass

    system_prompt = _REFLECT_SYSTEM + seed_block + atif_block

    # Verdict-preferred framing (SF-2): tell the extractor when a completed
    # run was judged goal-not-achieved so lessons come out failure-flavored
    # (recovery/verification) instead of celebrating a run that didn't deliver.
    outcome_desc = status
    if goal_achieved is False:
        outcome_desc += " — but the goal was judged NOT achieved (treat this as a failure)"
    elif goal_achieved is True:
        outcome_desc += " — goal verified achieved"
    user_msg = (
        f"Task type: {task_type}\n"
        f"Goal: {goal}\n"
        f"Outcome: {outcome_desc}\n"
        f"Summary: {result_summary[:500]}\n\n"
        "Extract 1-3 generalizable lessons as typed JSON objects."
    )

    def _parse_typed(raw: object) -> "List[tuple]":
        """Parse [{"lesson": ..., "type": ...}] or ["plain string", ...] — both accepted."""
        results = []
        # element_type must admit dicts: safe_list defaults to str, which
        # silently dropped every typed lesson object — the shape the prompt
        # explicitly asks for — so production extraction returned [] on
        # every run (found live 2026-06-11; tests only fed legacy strings).
        items = safe_list(raw, element_type=(dict, str), max_items=3)
        for item in items:
            if isinstance(item, dict):
                lesson_text = str(item.get("lesson", "")).strip()
                lesson_type = str(item.get("type", "execution")).strip().lower()
                if lesson_type not in _LESSON_TYPES:
                    lesson_type = "execution"
            elif isinstance(item, str):
                lesson_text = item.strip()
                lesson_type = "execution"  # legacy fallback
            else:
                continue
            if lesson_text:
                results.append((lesson_text, lesson_type))
        return results

    _total_tokens_in = 0
    _total_tokens_out = 0

    def _one_sample() -> "List[tuple]":
        nonlocal _total_tokens_in, _total_tokens_out
        try:
            resp = adapter.complete(
                [
                    LLMMessage("system", system_prompt),
                    LLMMessage("user", user_msg),
                ],
                max_tokens=320,
                temperature=0.3,
                no_tools=True,
                purpose="lesson extraction",
            )
            # F6: token transparency — track per-call token usage
            # LLMResponse uses input_tokens/output_tokens; accept either naming convention
            _total_tokens_in += (getattr(resp, "input_tokens", 0) or getattr(resp, "tokens_in", 0) or 0)
            _total_tokens_out += (getattr(resp, "output_tokens", 0) or getattr(resp, "tokens_out", 0) or 0)
            raw = extract_json(content_or_empty(resp), list, log_tag="memory.extract_lessons")
            return _parse_typed(raw)
        except Exception:
            if raise_on_failure:
                raise
            return []

    typed = _one_sample()

    # S5: Cross-type cap — at most 1 lesson per lesson_type prevents any single
    # type crowding out others (e.g., 3 "execution" lessons drowning out "recovery").
    type_seen: set = set()
    capped: list = []
    for lesson_text, lesson_type in typed:
        if lesson_type not in type_seen:
            type_seen.add(lesson_type)
            capped.append((lesson_text, lesson_type))
    typed = capped

    # F6: Token transparency — log extraction cost so expensive paths are visible
    if _total_tokens_in or _total_tokens_out:
        log.info(
            "extract_lessons tokens: in=%d out=%d lessons=%d",
            _total_tokens_in, _total_tokens_out, len(typed),
        )
        try:
            from metrics import record_step_cost
            record_step_cost(
                "memory.extract_lessons",
                tokens_in=_total_tokens_in,
                tokens_out=_total_tokens_out,
                status="done",
            )
        except Exception:
            pass

    if return_typed:
        return typed
    return [text for text, _ in typed]


def reflect_and_record(
    goal: str,
    status: str,
    result_summary: str,
    *,
    task_type: str = "general",
    project: Optional[str] = None,
    tokens_in: int = 0,
    tokens_out: int = 0,
    elapsed_ms: int = 0,
    model: str = "",
    adapter=None,
    dry_run: bool = False,
    failure_chain: Optional[List[str]] = None,
    recovery_steps: int = 0,
    goal_achieved: Optional[bool] = None,
    goal_verdict_source: str = "",
    loop_id: str = "",
    defer_lessons: bool = False,
    measurement_class: str = "",
    handle_id: str = "",
) -> Outcome:
    """Reflect on a completed run and record the outcome + lessons.

    This is the main hook to call after run_agent_loop or handle() completes.

    Args:
        failure_chain: Agent0 steal — ordered list of failure/diagnosis/recovery strings
                       (e.g. ["step 3 timed out", "diagnosed rate-limit", "retried after 60s"]).
                       Turns every retry into a training signal stored alongside the outcome.
        recovery_steps: How many recovery actions were required.
        goal_achieved: Tri-state goal verdict when already known at reflection
                       time (True/False; None = unjudged → absent on the row).
                       Agenda-lane closure runs after finalization, so those
                       verdicts land later via stamp_outcome_verdict(loop_id).
        goal_verdict_source: Provenance of the verdict when known.
        loop_id: This run's loop id — stored on the outcome row so the
                       post-closure verdict annotation can find it.
        defer_lessons: data-r2-01 — record the outcome row (lessons=[]) but
                       skip lesson extraction AND the knowledge write; the
                       caller promises to run extract_deferred_lessons(loop_id)
                       once the closure verdict has been stamped on the row.
                       Requires loop_id (the join key the deferred extraction
                       uses to find the row).
        measurement_class: Explicit organic/smoke/control/benchmark cohort
                       label; empty means unknown, never inferred retroactively.
        handle_id: Run-level key so restarted loop rows count as one run.
    """
    log.info("reflect_and_record goal=%r status=%s tokens=%d elapsed=%dms deferred=%s",
             goal[:60], status, tokens_in + tokens_out, elapsed_ms, defer_lessons)
    if defer_lessons and not loop_id:
        # Without the join key the deferred extraction can never find the
        # row — extracting verdict-blind beats losing the lessons entirely.
        log.warning("reflect_and_record: defer_lessons without loop_id — extracting now")
        defer_lessons = False
    if defer_lessons:
        typed_lessons = []
    else:
        # Phase 59 NeMo S1: use return_typed=True to capture lesson_type per lesson
        typed_lessons = extract_lessons_via_llm(
            goal=goal,
            status=status,
            result_summary=result_summary,
            task_type=task_type,
            adapter=adapter,
            dry_run=dry_run,
            return_typed=True,
            goal_achieved=goal_achieved,
        )
    lessons = [text for text, _ in typed_lessons]
    log.debug("extracted %d lessons from reflection", len(lessons))

    # Auto-record each typed lesson to the tiered system (MEDIUM tier, k_samples=1 → 0.5 confidence)
    # This closes the loop: lesson_type is preserved from extraction → tiered storage → injection.
    tiered_succeeded = 0
    tiered_failed = 0
    if not dry_run and typed_lessons:
        for lesson_text, lesson_type in typed_lessons:
            try:
                recorded = record_tiered_lesson(
                    lesson_text=lesson_text,
                    task_type=task_type,
                    outcome=status,
                    source_goal=goal[:120],
                    tier=MemoryTier.MEDIUM,
                    k_samples=1,  # single extraction → 0.5 confidence (F5)
                    lesson_type=lesson_type,
                )
                if getattr(recorded, "lesson_id", "") == "rejected":
                    tiered_failed += 1
                else:
                    tiered_succeeded += 1
            except Exception:
                tiered_failed += 1  # recording must never block reflection

    outcome = record_outcome(
        goal=goal,
        status=status,
        summary=result_summary,
        task_type=task_type,
        project=project,
        lessons=lessons,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        elapsed_ms=elapsed_ms,
        model=model,
        failure_chain=failure_chain or [],
        recovery_steps=recovery_steps,
        goal_achieved=goal_achieved,
        goal_verdict_source=goal_verdict_source,
        loop_id=loop_id,
        dry_run=dry_run,
        lesson_extraction_status="deferred" if defer_lessons else "completed",
        lesson_extraction_count=len(lessons),
        measurement_class=measurement_class,
        handle_id=handle_id,
    )

    _log_lesson_extraction(
        outcome_id=outcome.outcome_id,
        loop_id=loop_id,
        status="deferred" if defer_lessons else "completed",
        extracted_count=len(lessons),
        tiered_succeeded=tiered_succeeded,
        tiered_failed=tiered_failed,
        mode="deferred" if defer_lessons else "immediate",
        dry_run=dry_run,
    )

    # K4: write path — outcomes update knowledge layer (non-blocking).
    # Deferred with the lessons (data-r2-01): the knowledge extraction reads
    # the whole outcome, so it should see the judged version, not the blind one.
    if not dry_run and not defer_lessons:
        try:
            from knowledge_bridge import outcome_to_knowledge
            outcome_to_knowledge(outcome, adapter=adapter, dry_run=False)
        except Exception:
            pass  # knowledge write must never break the reflection path

    return outcome


def extract_deferred_lessons(
    loop_id: str,
    *,
    adapter=None,
    dry_run: bool = False,
    raise_on_failure: bool = True,
) -> int:
    """Run the lesson extraction that reflect_and_record(defer_lessons=True)
    skipped — now that the closure/provenance verdict has been stamped onto
    the outcomes row (data-r2-01: lessons must not be extracted verdict-blind
    from a done-but-not-achieved run).

    Reads the row back by loop_id (verdict included), extracts typed lessons
    with goal_achieved passed, records them through the same tiered + legacy
    paths reflect_and_record uses, stamps the lesson texts onto the row, and
    runs the deferred knowledge write. Idempotent: a row that already has
    lessons (extracted at finalize, or a prior call) is left alone.

    Returns the number of lessons recorded (0 = nothing to do or no row).
    """
    from memory_ledger import (
        load_outcome_by_loop_id,
        annotate_outcome_lessons,
        annotate_outcome_extraction_failure,
    )

    outcome = load_outcome_by_loop_id(loop_id)
    if outcome is None:
        log.debug("extract_deferred_lessons: no outcomes row for loop_id=%s", loop_id)
        return 0
    if outcome.lessons or outcome.lesson_extraction_status == "completed":
        return 0  # already extracted — nothing was deferred (or already ran)

    try:
        typed_lessons = extract_lessons_via_llm(
            goal=outcome.goal,
            status=outcome.status,
            result_summary=outcome.summary,
            task_type=outcome.task_type,
            adapter=adapter,
            dry_run=dry_run,
            return_typed=True,
            goal_achieved=outcome.goal_achieved,
            raise_on_failure=raise_on_failure,
        )
    except Exception as exc:
        annotate_outcome_extraction_failure(loop_id)
        _log_lesson_extraction(
            outcome_id=outcome.outcome_id,
            loop_id=loop_id,
            status="failed",
            extracted_count=0,
            tiered_succeeded=0,
            tiered_failed=0,
            mode="deferred",
            dry_run=outcome.dry_run or dry_run,
            error=str(exc),
        )
        raise
    if not typed_lessons:
        if not annotate_outcome_lessons(loop_id, []):
            annotate_outcome_extraction_failure(loop_id)
            error = "could not persist completed-zero extraction onto outcome row"
            _log_lesson_extraction(
                outcome_id=outcome.outcome_id,
                loop_id=loop_id,
                status="failed",
                extracted_count=0,
                tiered_succeeded=0,
                tiered_failed=0,
                mode="deferred",
                dry_run=outcome.dry_run or dry_run,
                error=error,
            )
            raise RuntimeError(error)
        _log_lesson_extraction(
            outcome_id=outcome.outcome_id,
            loop_id=loop_id,
            status="completed",
            extracted_count=0,
            tiered_succeeded=0,
            tiered_failed=0,
            mode="deferred",
            dry_run=outcome.dry_run or dry_run,
        )
        return 0
    lessons = [text for text, _ in typed_lessons]
    log.info("extract_deferred_lessons: %d lesson(s) for loop %s (verdict=%s)",
             len(lessons), loop_id, outcome.goal_achieved)

    # Stamp the outcome before any downstream fan-out. This is both the durable
    # idempotency marker (including completed-zero above) and the authoritative
    # cohort state for the funnel report.
    if not annotate_outcome_lessons(loop_id, lessons):
        annotate_outcome_extraction_failure(loop_id)
        error = "could not persist extracted lessons onto outcome row"
        _log_lesson_extraction(
            outcome_id=outcome.outcome_id,
            loop_id=loop_id,
            status="failed",
            extracted_count=len(lessons),
            tiered_succeeded=0,
            tiered_failed=0,
            mode="deferred",
            dry_run=outcome.dry_run or dry_run,
            error=error,
        )
        raise RuntimeError(error)
    outcome.lessons = lessons
    outcome.lesson_extraction_status = "completed"
    outcome.lesson_extraction_count = len(lessons)

    # Same recording fan-out as the finalize-time path, minus row append.
    tiered_succeeded = 0
    tiered_failed = 0
    if not dry_run:
        for lesson_text, lesson_type in typed_lessons:
            try:
                recorded = record_tiered_lesson(
                    lesson_text=lesson_text,
                    task_type=outcome.task_type,
                    outcome=outcome.status,
                    source_goal=outcome.goal[:120],
                    tier=MemoryTier.MEDIUM,
                    k_samples=1,
                    lesson_type=lesson_type,
                )
                if getattr(recorded, "lesson_id", "") == "rejected":
                    tiered_failed += 1
                else:
                    tiered_succeeded += 1
            except Exception:
                tiered_failed += 1  # recording must never block deferred delivery
    for lesson_text in lessons:
        if lesson_text.strip():
            _store_lesson(
                task_type=outcome.task_type,
                outcome=outcome.status,
                lesson=lesson_text,
                source_goal=outcome.goal,
                goal_achieved=outcome.goal_achieved,
                goal_verdict_source=outcome.goal_verdict_source,
            )
    _log_lesson_extraction(
        outcome_id=outcome.outcome_id,
        loop_id=loop_id,
        status="completed",
        extracted_count=len(lessons),
        tiered_succeeded=tiered_succeeded,
        tiered_failed=tiered_failed,
        mode="deferred",
        dry_run=outcome.dry_run or dry_run,
    )

    if not dry_run:
        try:
            from knowledge_bridge import outcome_to_knowledge
            outcome_to_knowledge(outcome, adapter=adapter, dry_run=False)
        except Exception:
            pass  # knowledge write must never break the deferred path

    return len(lessons)


def _log_lesson_extraction(
    *,
    outcome_id: str,
    loop_id: str,
    status: str,
    extracted_count: int,
    tiered_succeeded: int,
    tiered_failed: int,
    mode: str,
    dry_run: bool,
    error: str = "",
) -> None:
    """Emit one durable intake-funnel observation for an outcome.

    Empty historical outcome lesson lists are ambiguous. Durable outcome state
    now drives control/idempotency; this companion event adds tiered-write
    counts and makes transitions inspectable. Newer events supersede older
    event state for the same outcome.
    """
    try:
        from captains_log import log_event, LESSON_EXTRACTION
        context = {
            "outcome_id": outcome_id,
            "loop_id": loop_id,
            "status": status,
            "mode": mode,
            "dry_run": bool(dry_run),
            "extracted_count": max(0, int(extracted_count)),
            "tiered_succeeded": max(0, int(tiered_succeeded)),
            "tiered_failed": max(0, int(tiered_failed)),
        }
        if error:
            context["error"] = error[:200]
        log_event(
            event_type=LESSON_EXTRACTION,
            subject=outcome_id,
            summary=(
                f"Lesson extraction {status}: {extracted_count} extracted, "
                f"{tiered_succeeded} tiered writes, {tiered_failed} failures"
            ),
            context=context,
            loop_id=loop_id or None,
        )
    except Exception:
        pass  # funnel observability must never break result delivery


# ---------------------------------------------------------------------------
# Memory index
# ---------------------------------------------------------------------------

# _update_memory_index and _text_similarity moved to memory_ledger.py (re-exported above)



# NOTE: Tiered memory (MemoryTier, TieredLesson, decay, promotion, canon)
# extracted to knowledge_web.py and re-exported above.
#
# NOTE: Standing rules, hypotheses, decisions, verification
# extracted to knowledge_lens.py and re-exported above.
