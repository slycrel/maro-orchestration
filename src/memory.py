#!/usr/bin/env python3
# @lat: [[memory-system]]
"""Phase 5: Memory + Learning system for Poe orchestration.

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
import re
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

log = logging.getLogger("poe.memory")

# ---------------------------------------------------------------------------
# Re-exports from memory_ledger.py (decomposition Phase 1)
# All data types and CRUD functions live in memory_ledger now.
# Re-exported here for backward compatibility — external code imports from memory.
# ---------------------------------------------------------------------------
from memory_ledger import (  # noqa: F401, E402
    Outcome, Lesson, TaskLedgerEntry, CompressedBatch,
    _memory_dir, _outcomes_path, _lessons_path, _daily_path,
    _memory_index_path, _step_traces_path, _task_ledger_path,
    _compressed_outcomes_path, _text_similarity,
    append_task_ledger, load_task_ledger,
    record_step_trace, load_step_traces,
    record_outcome, _append_daily_log,
    _INJECTION_PATTERNS, _lesson_looks_adversarial,
    _store_lesson, _rewrite_lessons_file,
    load_lessons, load_outcomes,
    _save_compressed_batch, load_compressed_batches,
    compress_old_outcomes, _tfidf_rank_batches,
    load_outcomes_with_context, _update_memory_index,
)
from knowledge_web import (  # noqa: F401, E402
    MemoryTier, TieredLesson, GoalGap,
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
    run_decay_cycle, inject_tiered_lessons, detect_goal_gaps, query_lessons,
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
    record_decision, search_decisions, inject_decisions,
    record_verification, load_verification_outcomes,
    verification_accuracy, calibrated_alignment_threshold,
)

# ---------------------------------------------------------------------------
# Backend accessor (Phase 40) — used by agent_loop._build_loop_context
# ---------------------------------------------------------------------------

_BACKEND: Optional[Any] = None
_BACKEND_DIR: Optional[Any] = None


def _backend() -> Any:
    """Return the active memory backend, keyed by current memory_dir.

    Re-initialises if _memory_dir() has changed (e.g. monkeypatched in tests).
    """
    global _BACKEND, _BACKEND_DIR
    current_dir = _memory_dir()
    if _BACKEND is None or _BACKEND_DIR != current_dir:
        from memory_backends import get_backend
        _BACKEND = get_backend(current_dir)
        _BACKEND_DIR = current_dir
    return _BACKEND


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
            icon = "✓" if o.status == "done" else "✗"
            parts.append(f"- {icon} {o.goal[:60]} ({o.task_type}, {o.recorded_at[:10]}): {o.summary[:80]}")

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

    lines = ["## Lessons from Prior Runs (apply these)"]
    for l in lessons:
        icon = "✓" if l.outcome == "done" else "✗"
        lines.append(f"- {icon} {l.lesson}")
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


def _jaccard_similarity(a: str, b: str) -> float:
    """Jaccard similarity between two lesson strings (word-level)."""
    ta = set(re.sub(r"[^a-z0-9]+", " ", a.lower()).split())
    tb = set(re.sub(r"[^a-z0-9]+", " ", b.lower()).split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def majority_vote_lessons(
    all_samples: List[List[str]],
    *,
    threshold: float = 0.4,
) -> List[str]:
    """Agent0 steal: return only lessons that appear in majority of k samples.

    Two lessons are considered "the same" if their Jaccard similarity ≥ threshold.
    For each candidate lesson, count how many samples contain a similar lesson.
    Only return lessons with count > len(all_samples) / 2 (strict majority).

    Args:
        all_samples:  List of k lesson lists (one list per LLM sample call).
        threshold:    Jaccard similarity threshold for "same lesson" matching.

    Returns:
        Deduplicated list of lessons that appear in majority of samples.
        Falls back to all lessons from sample 0 if k == 1 (no filtering).
    """
    k = len(all_samples)
    if k <= 1:
        return all_samples[0] if all_samples else []

    # Collect all unique candidates from all samples
    all_candidates: List[str] = []
    seen: set = set()
    for sample in all_samples:
        for lesson in sample:
            lesson = lesson.strip()
            if lesson and lesson not in seen:
                seen.add(lesson)
                all_candidates.append(lesson)

    majority_threshold = k / 2.0  # strict majority: > 50%
    accepted: List[str] = []
    for candidate in all_candidates:
        count = 0
        for sample in all_samples:
            # Count this sample as agreeing if any lesson in it is "similar enough"
            for s_lesson in sample:
                if _jaccard_similarity(candidate, s_lesson) >= threshold:
                    count += 1
                    break
        if count > majority_threshold:
            accepted.append(candidate)

    return accepted[:3]  # cap at 3 (same as single-sample limit)


_LESSON_TYPES = frozenset({"execution", "planning", "recovery", "verification", "cost"})

# Phase 60: citation enforcement — uncited lessons are gently penalised in ranking.
# A 10% discount means a clearly-better uncited lesson still wins; this is a tie-breaker.
_CITATION_PENALTY = 0.90


def extract_lessons_via_llm(
    goal: str,
    status: str,
    result_summary: str,
    task_type: str,
    *,
    adapter=None,
    dry_run: bool = False,
    k_samples: int = 1,
    return_typed: bool = False,
) -> "List":
    """Use LLM to extract generalizable lessons from a completed run.

    Phase 59 NeMo steals:
    - S1: Returns typed lessons (lesson_type per lesson) when return_typed=True.
    - S2: Seed-reader bootstrapping — prepends top-1 long-tier lesson as style guide.
    - S3: ATIF feedback — passes times_reinforced + times_applied stats into prompt.

    Args:
        k_samples:    Agent0 steal — number of LLM samples to draw. When k_samples ≥ 3,
                      only lessons that appear in majority of samples are returned
                      (majority-vote pseudo-labels). Default: 1 (original behaviour).
        return_typed: If True, return List[Tuple[str, str]] (lesson_text, lesson_type).
                      If False (default), return List[str] for backward compat.

    Returns list of lesson strings (or typed tuples). Falls back to empty list on failure.
    """
    if dry_run or adapter is None:
        # Generate a dry-run lesson
        icon = "succeeded" if status == "done" else "failed"
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

    user_msg = (
        f"Task type: {task_type}\n"
        f"Goal: {goal}\n"
        f"Outcome: {status}\n"
        f"Summary: {result_summary[:500]}\n\n"
        "Extract 1-3 generalizable lessons as typed JSON objects."
    )

    def _parse_typed(raw: object) -> "List[tuple]":
        """Parse [{"lesson": ..., "type": ...}] or ["plain string", ...] — both accepted."""
        results = []
        items = safe_list(raw, max_items=3)
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
            )
            # F6: token transparency — track per-call token usage
            # LLMResponse uses input_tokens/output_tokens; accept either naming convention
            _total_tokens_in += (getattr(resp, "input_tokens", 0) or getattr(resp, "tokens_in", 0) or 0)
            _total_tokens_out += (getattr(resp, "output_tokens", 0) or getattr(resp, "tokens_out", 0) or 0)
            raw = extract_json(content_or_empty(resp), list, log_tag="memory.extract_lessons")
            return _parse_typed(raw)
        except Exception:
            return []

    if k_samples <= 1:
        typed = _one_sample()
    else:
        # Multi-sample majority vote (Agent0 pseudo-label pattern)
        typed_samples = [_one_sample() for _ in range(k_samples)]
        # Extract plain strings for majority vote, then reattach types
        str_samples = [[t for t, _ in s] for s in typed_samples]
        agreed_strs = set(majority_vote_lessons(str_samples))
        # Collect typed tuples for agreed lessons (first occurrence wins)
        seen: set = set()
        typed = []
        for sample in typed_samples:
            for lesson_text, lesson_type in sample:
                if lesson_text in agreed_strs and lesson_text not in seen:
                    seen.add(lesson_text)
                    typed.append((lesson_text, lesson_type))
        log.debug("extract_lessons k=%d samples=%d agreed=%d typed=%d",
                  k_samples, len(typed_samples), len(agreed_strs), len(typed))

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
            "extract_lessons tokens: in=%d out=%d k_samples=%d lessons=%d",
            _total_tokens_in, _total_tokens_out, max(k_samples, 1), len(typed),
        )
        try:
            from metrics import record_cost
            record_cost("memory.extract_lessons", tokens_in=_total_tokens_in, tokens_out=_total_tokens_out)
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
) -> Outcome:
    """Reflect on a completed run and record the outcome + lessons.

    This is the main hook to call after run_agent_loop or handle() completes.

    Args:
        failure_chain: Agent0 steal — ordered list of failure/diagnosis/recovery strings
                       (e.g. ["step 3 timed out", "diagnosed rate-limit", "retried after 60s"]).
                       Turns every retry into a training signal stored alongside the outcome.
        recovery_steps: How many recovery actions were required.
    """
    log.info("reflect_and_record goal=%r status=%s tokens=%d elapsed=%dms",
             goal[:60], status, tokens_in + tokens_out, elapsed_ms)
    # Phase 59 NeMo S1: use return_typed=True to capture lesson_type per lesson
    typed_lessons = extract_lessons_via_llm(
        goal=goal,
        status=status,
        result_summary=result_summary,
        task_type=task_type,
        adapter=adapter,
        dry_run=dry_run,
        return_typed=True,
    )
    lessons = [text for text, _ in typed_lessons]
    log.debug("extracted %d lessons from reflection", len(lessons))

    # Auto-record each typed lesson to the tiered system (MEDIUM tier, k_samples=1 → 0.5 confidence)
    # This closes the loop: lesson_type is preserved from extraction → tiered storage → injection.
    if not dry_run and typed_lessons:
        for lesson_text, lesson_type in typed_lessons:
            try:
                record_tiered_lesson(
                    lesson_text=lesson_text,
                    task_type=task_type,
                    outcome=status,
                    source_goal=goal[:120],
                    tier=MemoryTier.MEDIUM,
                    k_samples=1,  # single extraction → 0.5 confidence (F5)
                    lesson_type=lesson_type,
                )
            except Exception:
                pass  # tiered recording must never block the main reflection path

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
    )

    # K4: write path — outcomes update knowledge layer (non-blocking)
    if not dry_run:
        try:
            from knowledge_bridge import outcome_to_knowledge
            outcome_to_knowledge(outcome, adapter=adapter, dry_run=False)
        except Exception:
            pass  # knowledge write must never break the reflection path

    return outcome


# ---------------------------------------------------------------------------
# Memory index
# ---------------------------------------------------------------------------

# _update_memory_index and _text_similarity moved to memory_ledger.py (re-exported above)



# NOTE: Tiered memory (MemoryTier, TieredLesson, decay, promotion, canon)
# extracted to knowledge_web.py and re-exported above.
#
# NOTE: Standing rules, hypotheses, decisions, verification
# extracted to knowledge_lens.py and re-exported above.
