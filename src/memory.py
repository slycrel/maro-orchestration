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
from typing import Any, Dict, List, NamedTuple, Optional
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
    DECAY_FACTOR, REINFORCE_BONUS, NOVELTY_BONUS, PROMOTE_MIN_SCORE, PROMOTE_MIN_SESSIONS,
    GC_THRESHOLD,
    CANON_APPLY_THRESHOLD, CANON_TASK_TYPE_MIN,
    _STOP_WORDS, _CITATION_PENALTY, _CONFIDENCE_SINGLE_CALL, _CONFIDENCE_MAJORITY_VOTE,
    _CONFIDENCE_MULTI_SESSION,
    short_set, short_get, short_clear, short_all,
    _tiered_lessons_path, _days_since, decay_score, reinforce_score, _current_date,
    confidence_from_k_samples, _tokenize, _tfidf_rank,
    record_tiered_lesson, _append_tiered_lesson, _reinforce_tiered_lesson,
    load_tiered_lessons, _rewrite_tiered_lessons,
    reinforce_lesson, search_graveyard, forget_lesson, promote_lesson,
    contest_lesson, contested_lessons, refight_lesson,
    confirm_lesson_by_delta,
    resurrect_archived_lesson, _load_archived_lessons, _lessons_archive_path,
    run_decay_cycle, maybe_consolidate, consolidation_due,
    inject_tiered_lessons, query_lessons,
    _increment_times_applied, _canon_stats_path, _record_canon_hit,
    _load_canon_stats, get_canon_candidates, promote_canon_lesson,
    memory_status,
)
from knowledge_lens import (  # noqa: F401, E402
    StandingRule, Hypothesis, Decision,
    RULE_PROMOTE_CONFIRMATIONS, DECISION_SEARCH_LIMIT,
    _rules_path, _hypotheses_path, _decisions_path,
    load_standing_rules, load_hypotheses, _rewrite_rules, _rewrite_hypotheses,
    observe_pattern, contradict_pattern, check_contradiction, inject_standing_rules,
    standing_rules_with_ids, contested_rules, refight_rule,
    record_decision, search_decisions, inject_decisions,
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

    # Mint-grounding display (MINT_GROUNDING_DESIGN §3 slice 1): an
    # unsupported method claim rides into the prompt WITH its warning.
    from mint_grounding import grounding_marker

    lines = ["## Lessons from Prior Runs (apply these)"]
    for l in lessons:
        # Verdict-preferred (SF-2): a lesson from a run judged goal-not-achieved
        # is a failure lesson even though the run's process status was "done".
        icon = "✗" if getattr(l, "goal_achieved", None) is False else ("✓" if l.outcome == "done" else "✗")
        _suffix = (age_suffix(getattr(l, "recorded_at", "") or "")
                   if _stamp_ages else "")
        _gmark = grounding_marker(getattr(l, "grounding", None))
        lines.append(f"- {icon} {l.lesson}{_gmark}{_suffix}")
    result = "\n".join(lines)
    if len(result) > _MAX_LESSON_INJECT_CHARS:
        result = result[:_MAX_LESSON_INJECT_CHARS].rsplit("\n", 1)[0]
    return result


# ---------------------------------------------------------------------------
# Reflexion: post-run lesson extraction
# ---------------------------------------------------------------------------

# What-not-how mint rule (Jeremy, 2026-08-02: "how is ok when asking for
# work, but usually we aren't — asking for the right result is the more
# important part"). Shared by every LLM mint-site prompt; the operator
# surprise-read certified L4/M9/M13/M14 as the failure shapes this blocks.
_LESSON_FORM_RULES = textwrap.dedent("""\
    Lesson form — record WHAT was derived, not HOW to act on it:
    - Mint the observation (the mismatch, the requirement, the observed
      failure), never a prescribed procedure (a named checkpoint, tool path,
      or step sequence). Future planners treat lessons as evidence to reason
      from, not instructions to obey. Write "exact-pricing claims needed 2
      trusted sources; a single category page was not enough" — not "budget
      two lookups per item (spec page, then retailer page)".
    - A repeated failure is itself the observation: state that it repeated
      ("this same blocker sank the prior attempt too"), not a pre-baked
      countermeasure for next time.
    - Never credit a method or pass with a general capability ("this catches
      X", "this prevents Y"). State only the instance actually observed in
      this run. No self-credit clause without the observation that evidences
      it.
    - Procedure form is acceptable only when the goal itself asked for a
      procedure (a runbook, script, or how-to) — there the procedure IS the
      derived result.
""").strip()


# Backstop on the evidence block handed to lesson extraction. Set ABOVE the
# ContextBudget that builds the block (context_budget.DEFAULT_TOTAL_BUDGET =
# 24,000) so the budget stays the thing that decides, and this only catches a
# caller that passes something unbudgeted. It marks when it bites.
_LESSON_EVIDENCE_CUT = 24000


_REFLECT_SYSTEM = (textwrap.dedent("""\
    You are a meta-learning agent. After each completed run, extract durable lessons.
    A lesson is a generalizable insight that would improve future similar runs.
    Good lessons are: specific observations that generalize beyond this one case.
    Bad lessons are: too specific to this one task, or trivially obvious.

    Start from the expectation-mismatch question: what actually DIFFERED from
    what the plan assumed? Surprises — wrong guesses, unexpected obstacles,
    unexpected shortcuts — carry the most durable lessons. When a mismatch
    exists, capture the mismatch itself (assumed X, found Y), not just the
    workaround. A run with no surprises usually has no lesson worth keeping.
""").strip() + "\n\n" + _LESSON_FORM_RULES + "\n\n" + textwrap.dedent("""\
    Lesson types (pick the best fit for each lesson):
    - "execution": carrying out steps (tools, sequencing, parallelism)
    - "planning": decomposing or scoping goals
    - "recovery": failure, retries, or stuck states
    - "verification": output quality and catching errors early
    - "cost": token spend or latency

    Each lesson also gets a "scope" — an axis INDEPENDENT of "type":
    - "method": knowledge about how to work (process, tooling, sequencing,
      verification, recovery). Holds whatever the next task is about.
    - "world": knowledge about one external subject (a site, API, repo,
      dataset, domain fact). Useful again mainly in that same subject area.
    "type" is ALWAYS one of the five lesson types above; "scope" is ALWAYS
    method or world. Never put a scope value in "type" or a type value in "scope".

    Respond with a JSON array of 1-3 lesson objects, each with "lesson" (string), "type" (one of the five), and "scope" ("method" or "world").
    Example: [{"lesson": "Research tasks produce better output when the goal includes success criteria", "type": "planning", "scope": "method"},
              {"lesson": "Stuck detection triggers prematurely on research tasks that need multiple iterations", "type": "recovery", "scope": "method"},
              {"lesson": "The pure-gas.org station list answers automated fetches with a bot challenge, so retries against it never recover", "type": "recovery", "scope": "world"}]
""").strip())


_LESSON_TYPES = frozenset({"execution", "planning", "recovery", "verification", "cost"})

# §14a slice 3: mint-time scope stamp — provenance, a fact about where the
# knowledge came from, stamped categorically at mint and never flipped after
# (decision e2b83703). Deliberately NOT a ranking input: the slice-1 census
# cross-tab is starved on the world side (of the 6 lessons carrying >=3
# verdicted foreign citations, every resolvable one is method-scope; only 5
# world-scope lessons have ANY foreign citation), so there is no evidence yet
# that scope predicts portability. The stamp exists to FEED that census —
# earned globality (portability.py) keeps doing the behavioral work.
#
# The stamp is one sample of an ambiguous judgement, and it is
# LABELLER-DEPENDENT. Measured pre-ship: the production mint lane stamps ~81%
# method and repeats itself 97.5% across two passes; hosted-free
# (gemini-flash-lite) stamped only ~44% method on the same runs and repeated
# itself 88.8% — it anchors on whatever subject the evidence names. So the
# stamp is trustworthy row-by-row on the main lane, but a corpus whose rows
# were minted by different lanes has no single base rate. Read stamps as
# provenance, not ground truth, and never compare them across labellers.
_LESSON_SCOPES = frozenset({"method", "world"})


class TypedLesson(NamedTuple):
    """One extracted lesson: text, type (S1), scope (§14a slice 3).

    A tuple subclass on purpose. ``return_typed=True`` used to yield plain
    ``(text, type)`` pairs and both in-tree callers and test fakes destructure
    them positionally, so widening in place would have broken every two-name
    unpack at once. Consumers that need the stamp go through
    :func:`as_typed_lesson`, which accepts either width and defaults the stamp
    to "" — an unstamped lesson is the honest reading of a pair that never
    carried one.
    """
    lesson: str
    lesson_type: str
    scope: str = ""


def as_typed_lesson(item) -> TypedLesson:
    """Normalize a (text, type) pair or (text, type, scope) triple.

    The boundary that lets the extractor widen without a flag day: legacy
    two-element returns — including the ones tests monkeypatch in — become
    unstamped triples rather than raising on unpack.
    """
    if isinstance(item, TypedLesson):
        return item
    parts = tuple(item)
    text = str(parts[0]) if parts else ""
    lesson_type = str(parts[1]) if len(parts) > 1 else "execution"
    scope = str(parts[2]).strip().lower() if len(parts) > 2 else ""
    return TypedLesson(text, lesson_type, scope if scope in _LESSON_SCOPES else "")


# S2 seed-reader (NeMo steal) REMOVED 2026-08-06 after the sanctioned A/B
# (13 runs × 2 arms, ~/.maro/workspace/output/seed-reader-ab/RESULTS.md):
# showing the top LONG lesson as a verbatim "emulate this" exemplar bought
# no measurable quality (what-not-how, length, count all flat-or-worse)
# while tilting extraction toward the seed's lesson_type 3.5× across
# unrelated runs and homogenizing lessons ~60% (jackknife-stable) — the
# LeAct contamination mechanism, mild but real. The store's then-top seed
# was itself procedure-form, so the exemplar was modeling the exact shape
# the what-not-how mint rule forbids. Qualitative style guidance lives in
# _REFLECT_SYSTEM + _LESSON_FORM_RULES; a redacted-guidance successor
# would need its own A/B (the harness in that output dir reruns cheaply).


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
    lesson_evidence: str = "",
) -> "List":
    """Use LLM to extract generalizable lessons from a completed run.

    Phase 59 NeMo steals:
    - S1: Returns typed lessons (lesson_type per lesson) when return_typed=True.
    - S2: Seed-reader style exemplar — REMOVED 2026-08-06 (A/B: no quality
      gain, type-anchoring + homogenization; see note above).
    - S3: ATIF feedback — passes times_reinforced + times_applied stats into prompt.

    Args:
        return_typed: If True, return List[Tuple[str, str]] (lesson_text, lesson_type).
                      If False (default), return List[str] for backward compat.
        lesson_evidence: Wide per-step evidence, when the caller has the step
                      outcomes in hand (loop_finalize does; the deferred path,
                      which reads a stored row back, does not). Prompt-only and
                      never persisted, so it is budgeted in tokens rather than
                      disk — see loop_finalize._step_evidence. Falls back to
                      result_summary when absent.

    Returns list of lesson strings (or typed tuples). Falls back to empty list on failure.
    """
    if dry_run or adapter is None:
        # Generate a dry-run lesson. Verdict-preferred framing (SF-2): a run
        # judged goal-not-achieved is a failure regardless of process status.
        icon = "succeeded" if (status == "done" and goal_achieved is not False) else "failed"
        lesson = f"[dry-run lesson] {task_type} task {icon}: {goal[:40]}"
        # Dry-run lessons carry no scope stamp: nothing classified them, and a
        # fabricated "method" would be indistinguishable in the census from a
        # real mint-time judgement.
        return [TypedLesson(lesson, "execution", "")] if return_typed else [lesson]

    from llm import LLMMessage

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

    system_prompt = _REFLECT_SYSTEM + atif_block

    # Verdict-preferred framing (SF-2): tell the extractor when a completed
    # run was judged goal-not-achieved so lessons come out failure-flavored
    # (recovery/verification) instead of celebrating a run that didn't deliver.
    outcome_desc = status
    if goal_achieved is False:
        outcome_desc += " — but the goal was judged NOT achieved (treat this as a failure)"
    elif goal_achieved is True:
        outcome_desc += " — goal verified achieved"
    # Evidence, widest available. This was `result_summary[:500]` — a cut that
    # measured as pure decoration (0 of 1,493 stored summaries ever reached it,
    # median length 70) because the real loss happened upstream, where the
    # summary was built from one step's first 80 characters. Now that callers
    # can pass a real evidence block, this backstop would start binding, so it
    # is set above the ContextBudget that produces the block and it SAYS when
    # it bites rather than trimming in silence.
    _evidence = (lesson_evidence or result_summary or "").strip()
    if len(_evidence) > _LESSON_EVIDENCE_CUT:
        _evidence = (f"{_evidence[:_LESSON_EVIDENCE_CUT]}\n"
                     f"… [evidence truncated: first {_LESSON_EVIDENCE_CUT} of "
                     f"{len(_evidence)} characters]")
    user_msg = (
        f"Task type: {task_type}\n"
        f"Goal: {goal}\n"
        f"Outcome: {outcome_desc}\n"
        f"What the run did:\n{_evidence}\n\n"
        "Extract 1-3 generalizable lessons as typed JSON objects."
    )

    def _parse_typed(raw: object) -> "List[tuple]":
        """Parse [{"lesson":..., "type":..., "scope":...}] or ["plain string", ...].

        Returns TypedLesson triples. Both older shapes still parse: a dict
        without "scope" yields an empty stamp, a bare string stays the legacy
        execution/unstamped fallback.
        """
        results = []
        # element_type must admit dicts: safe_list defaults to str, which
        # silently dropped every typed lesson object — the shape the prompt
        # explicitly asks for — so production extraction returned [] on
        # every run (found live 2026-06-11; tests only fed legacy strings).
        items = safe_list(raw, element_type=(dict, str), max_items=3)
        for item in items:
            scope = ""
            if isinstance(item, dict):
                lesson_text = str(item.get("lesson", "")).strip()
                lesson_type = str(item.get("type", "execution")).strip().lower()
                scope = str(item.get("scope", "")).strip().lower()
                # Cross-slot recovery: asking for two categorical keys in one
                # object makes the model occasionally answer the wrong one.
                # Measured pre-ship on the naive schema wording: a scope value
                # landed in "type" on 2 of 18 lessons, and the plain
                # not-in-_LESSON_TYPES fallback below would have silently
                # rewritten those to "execution" — a wrong lesson_type bought
                # by adding a field. The shipped prompt drove that to 0/31
                # across two lanes, so this is the belt to that suspenders:
                # recover the misplaced value instead of destroying it.
                if lesson_type in _LESSON_SCOPES:
                    if not scope:
                        scope = lesson_type
                    log.debug("lesson scope value %r arrived in the type slot", lesson_type)
                    lesson_type = ""
                if scope in _LESSON_TYPES:
                    if lesson_type not in _LESSON_TYPES:
                        lesson_type = scope
                    scope = ""
                if lesson_type not in _LESSON_TYPES:
                    lesson_type = "execution"
                if scope not in _LESSON_SCOPES:
                    scope = ""
            elif isinstance(item, str):
                lesson_text = item.strip()
                lesson_type = "execution"  # legacy fallback
            else:
                continue
            if lesson_text:
                results.append(TypedLesson(lesson_text, lesson_type, scope))
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
    for item in typed:
        if item.lesson_type not in type_seen:
            type_seen.add(item.lesson_type)
            capped.append(item)
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
    return [item.lesson for item in typed]


# ---------------------------------------------------------------------------
# Per-step learning (2026-07-27): provisional lessons from verified steps
# ---------------------------------------------------------------------------

_STEP_LESSON_SYSTEM = (textwrap.dedent("""\
    You are a meta-learning agent. A run's HIGH-LEVEL GOAL DID NOT land, but
    some of its steps individually PASSED verification. Extract durable method
    lessons scoped strictly to what those steps verifiably did.

    Rules:
    - Scope every lesson to the step-level method ("fetching X via Y worked",
      "parsing Z needs W first") — NEVER to goal-level success. The run as a
      whole failed; do not extract a lesson that celebrates or implies the
      goal landed.
    - NEVER extract negative/deadness claims ("X doesn't work", "X is dead",
      "avoid Y") from this run. The run's failure is not evidence that any
      particular method is dead — a wrongly-recorded dead-end gets a working
      approach permanently avoided, which costs far more than a missed tip.
    - Good lessons are specific observations that generalize beyond this
      case. A step with no surprise usually has no lesson worth keeping.
""").strip() + "\n\n" + _LESSON_FORM_RULES + "\n\n" + textwrap.dedent("""\
    Lesson types (pick the best fit for each lesson):
    - "execution": carrying out steps (tools, sequencing, parallelism)
    - "planning": decomposing or scoping goals
    - "verification": output quality and catching errors early
    - "cost": token spend or latency

    Respond with a JSON array of 0-3 lesson objects, each with "lesson"
    (string) and "type" (one of the above). An empty array is a valid answer.
""").strip())

_STEP_LESSON_MAX_STEPS = 8  # prompt cap — verified steps beyond this are dropped (logged)


def _step_learning_enabled() -> bool:
    """Killswitch for per-step provisional extraction (default ON). Same
    quoted-"false" normalization as the other killswitches (chunk-5a F1)."""
    try:
        from config import get as _cfg_get
        val = _cfg_get("memory.step_learning_enabled", True)
        if isinstance(val, str):
            return val.strip().lower() not in ("false", "0", "no", "off")
        return bool(val)
    except Exception:
        return True


def extract_step_lessons(
    goal: str,
    step_outcomes: List[Any],
    *,
    task_type: str = "agenda",
    adapter=None,
    loop_id: str = "",
    dry_run: bool = False,
) -> int:
    """Extract PROVISIONAL lessons from individually-verified steps of a run
    whose run-level outcome failed the learnability gate.

    The run-level gate (outcome_policy.is_learnable_outcome) is correct about
    the run — but it is applied at the only granularity that existed when it
    was built. A run stuck at step 9/10 tossed the method evidence in the
    eight steps that individually verified. This pass learns at the
    granularity where verification actually happened: steps with
    status="done" AND confidence="strong" (the verify ladder's positive
    verdict; "weak"/"inferred"/"unverified" do not qualify).

    Lessons enter the tiered store with provisional=True: reduced entry
    score, excluded from every injection surface, never promoted to LONG —
    until a confirmed-context re-record clears the flag (promote-on-evidence,
    see record_tiered_lesson). Decay disposes of the unconfirmed rest.

    Idempotent per run via the ``step_lesson_count`` stamp on the outcomes
    row (run_deferred_learning is called for loops that didn't defer — the
    stamp keeps a failure-shaped run from re-paying the extraction call on
    every post-closure pass). The stamp lands only after a SUCCESSFUL pass:
    a transient LLM failure deliberately leaves the row unstamped so a later
    pass retries instead of forfeiting the learning permanently. One LLM
    call per successful pass, capped at _STEP_LESSON_MAX_STEPS steps.
    Never raises.

    Returns the number of provisional lessons recorded (0 = pass skipped or
    nothing usable).
    """
    if not _step_learning_enabled():
        return 0
    if dry_run or adapter is None or not step_outcomes:
        return 0

    verified = [
        s for s in step_outcomes
        if getattr(s, "status", "") == "done"
        and getattr(s, "confidence", "") == "strong"
    ]
    if not verified:
        return 0

    from memory_ledger import outcome_row_has_step_lessons, stamp_outcome_step_lessons
    if loop_id and outcome_row_has_step_lessons(loop_id):
        return 0

    if len(verified) > _STEP_LESSON_MAX_STEPS:
        log.info("extract_step_lessons: %d verified steps, using first %d "
                 "(plan order)", len(verified), _STEP_LESSON_MAX_STEPS)
        verified = verified[:_STEP_LESSON_MAX_STEPS]

    # The verified result IS the evidence for a method lesson, so it gets a
    # budget rather than a fixed cut. The old `result[:300]` left 8.7% of step
    # results intact (measured over 1,851 recorded steps: median 1,180 chars,
    # p90 2,247); the step TEXT at 200 was already fine at 90.8% intact and
    # stays where it is. Eviction and per-entry capping announce themselves.
    from context_budget import ContextBudget
    _budget = ContextBudget()
    for s in verified:
        text = (getattr(s, "text", "") or "")[:200]
        result = (getattr(s, "result", "") or "")
        _budget.add(f"- step: {text}\n  verified result: {result}")

    _step_evidence_block = _budget.render()
    user_msg = (
        f"Task type: {task_type}\n"
        f"High-level goal (NOT achieved): {goal[:300]}\n\n"
        f"Individually-verified steps:\n" + _step_evidence_block + "\n\n"
        "Extract 0-3 step-scoped method lessons as typed JSON objects."
    )

    try:
        from llm import LLMMessage
        resp = adapter.complete(
            [
                LLMMessage("system", _STEP_LESSON_SYSTEM),
                LLMMessage("user", user_msg),
            ],
            max_tokens=320,
            temperature=0.3,
            no_tools=True,
            purpose="step lesson extraction",
        )
        raw = extract_json(content_or_empty(resp), list,
                           log_tag="memory.extract_step_lessons")
    except Exception as exc:
        log.warning("extract_step_lessons: LLM call failed for loop %s: %s",
                    loop_id, exc)
        return 0

    _step_types = frozenset({"execution", "planning", "verification", "cost"})
    step_items: List[tuple] = []
    for item in safe_list(raw, element_type=(dict, str), max_items=3):
        if isinstance(item, dict):
            lesson_text = str(item.get("lesson", "")).strip()
            lesson_type = str(item.get("type", "execution")).strip().lower()
        else:
            lesson_text, lesson_type = str(item).strip(), "execution"
        if lesson_type not in _step_types:
            lesson_type = "execution"
        if lesson_text:
            step_items.append((lesson_text, lesson_type))

    # Mint-time grounding (MINT_GROUNDING_DESIGN §3, slice-2 writer
    # completion 2026-08-16): step-lessons were minting unstamped even
    # though loop_id was already in hand (R1-3). Fail-open as ever.
    _step_groundings: List[list] = []
    if step_items and loop_id:
        try:
            from mint_grounding import ground_lessons_for_run
            _step_groundings = ground_lessons_for_run(
                [t for t, _ in step_items], loop_id)
        except Exception as exc:
            log.debug("extract_step_lessons: mint grounding unavailable: %s",
                      exc)
            _step_groundings = []

    recorded = 0
    for _s_idx, (lesson_text, lesson_type) in enumerate(step_items):
        try:
            tl = record_tiered_lesson(
                lesson_text=lesson_text,
                task_type=task_type,
                outcome="step-verified",
                # Full goal — the provenance gate classifies on it; the
                # store truncates the row's excerpt itself.
                source_goal=goal,
                tier=MemoryTier.MEDIUM,
                k_samples=1,
                lesson_type=lesson_type,
                provisional=True,
                evidence_sources=[f"loop:{loop_id}"] if loop_id else [],
                grounding=(_step_groundings[_s_idx]
                           if _s_idx < len(_step_groundings) else None),
                # R1-5: step results are the extraction input — scaffolding
                # planted there must reach the provenance classifier.
                source_evidence=_step_evidence_block,
            )
            if getattr(tl, "lesson_id", "") != "rejected":
                recorded += 1
        except Exception:
            continue  # recording must never block finalize

    if loop_id:
        # Stamp even when 0 recorded — the pass RAN; re-running it on the
        # same steps would produce the same nothing for another LLM call.
        stamp_outcome_step_lessons(loop_id, recorded)
    log.info("extract_step_lessons: %d provisional lesson(s) from %d "
             "verified step(s) for loop %s", recorded, len(verified),
             loop_id or "?")
    return recorded


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
    stop_verdict: str = "",
    stop_evidence: str = "",
    pause_reason: str = "",
    lesson_evidence: str = "",
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
            lesson_evidence=lesson_evidence,
            task_type=task_type,
            adapter=adapter,
            dry_run=dry_run,
            return_typed=True,
            goal_achieved=goal_achieved,
        )
    # Normalize at the boundary so a caller (or a test double) handing back
    # legacy (text, type) pairs still flows through the scope-aware writes.
    typed_lessons = [as_typed_lesson(t) for t in typed_lessons]
    lessons = [t.lesson for t in typed_lessons]
    log.debug("extracted %d lessons from reflection", len(lessons))

    # Auto-record each typed lesson to the tiered system (MEDIUM tier, k_samples=1 → 0.5 confidence)
    # This closes the loop: lesson_type is preserved from extraction → tiered storage → injection.
    tiered_succeeded = 0
    tiered_failed = 0
    # UU-4: one extraction event dual-writes each lesson (tiered store here,
    # flat ledger inside record_outcome below). Mint ONE id per lesson and
    # thread it through both writers so the rows join — before this, the
    # same lesson carried independent uuid4 ids in each store and "was run
    # A's lesson applied in run B?" was unanswerable by id (it cost the
    # 2026-08-01 warm-arm forensics a wrong conclusion). Fresh mints only:
    # a near-duplicate reinforce returns the existing row's id, which wins.
    lesson_shared_ids: List[str] = []
    # Mint-time grounding (MINT_GROUNDING_DESIGN §3 slice 1): join each
    # lesson's method claims against the minting run's tool events and
    # stamp receipts on both store rows. Fail-open — any failure yields
    # empty stamp lists and the mint proceeds exactly as before.
    lesson_groundings: List[list] = []
    if not dry_run and typed_lessons:
        try:
            from mint_grounding import ground_lessons_for_run
            lesson_groundings = ground_lessons_for_run(
                [t.lesson for t in typed_lessons], loop_id or handle_id)
        except Exception:
            lesson_groundings = []
    if not dry_run and typed_lessons:
        import uuid as _uuid
        for _l_idx, (lesson_text, lesson_type, lesson_scope) in enumerate(typed_lessons):
            _shared_id = str(_uuid.uuid4())[:8]
            try:
                recorded = record_tiered_lesson(
                    lesson_text=lesson_text,
                    task_type=task_type,
                    outcome=status,
                    source_goal=goal,
                    tier=MemoryTier.MEDIUM,
                    k_samples=1,  # single extraction → 0.5 confidence (F5)
                    lesson_type=lesson_type,
                    lesson_id=_shared_id,
                    # M14 defect: reflect mints landed with evidence_sources=[]
                    # even though the originating run was known — the Phase 60
                    # citation penalty never had anything to reward.
                    evidence_sources=[f"loop:{loop_id}"] if loop_id else [],
                    grounding=(lesson_groundings[_l_idx]
                               if _l_idx < len(lesson_groundings) else None),
                    # R1-5: same block extract_lessons_via_llm saw — the
                    # classifier must see what the extractor generalized from.
                    source_evidence=lesson_evidence or result_summary,
                    scope=lesson_scope,
                )
                if getattr(recorded, "lesson_id", "") == "rejected":
                    tiered_failed += 1
                else:
                    tiered_succeeded += 1
                    # Reinforce path returns the pre-existing id — carry THAT
                    # to the flat ledger so both point at the live row.
                    _shared_id = getattr(recorded, "lesson_id", _shared_id) or _shared_id
            except Exception:
                tiered_failed += 1  # recording must never block reflection
            lesson_shared_ids.append(_shared_id)

    outcome = record_outcome(
        goal=goal,
        status=status,
        summary=result_summary,
        task_type=task_type,
        project=project,
        lessons=lessons,
        lesson_ids=lesson_shared_ids or None,
        lesson_groundings=lesson_groundings or None,
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
        stop_verdict=stop_verdict,
        stop_evidence=stop_evidence,
        pause_reason=pause_reason,
        lesson_evidence=lesson_evidence or result_summary,
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
    typed_lessons = [as_typed_lesson(t) for t in typed_lessons]
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
    lessons = [t.lesson for t in typed_lessons]
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
    # UU-4: same shared-id thread as reflect_and_record — this deferred path
    # is the one the 2026-08-01 cold chlorination run actually took
    # (mode=deferred), where the id divergence was observed live.
    deferred_shared_ids: list = []
    # Mint-time grounding — same join as the finalize-time path; this
    # deferred lane is the one organic runs actually take (mode=deferred),
    # so skipping it here would leave the production mints unstamped.
    lesson_groundings: list = []
    if not dry_run:
        try:
            from mint_grounding import ground_lessons_for_run
            lesson_groundings = ground_lessons_for_run(
                [t.lesson for t in typed_lessons], loop_id)
        except Exception:
            lesson_groundings = []
    if not dry_run:
        import uuid as _uuid
        for _l_idx, (lesson_text, lesson_type, lesson_scope) in enumerate(typed_lessons):
            _shared_id = str(_uuid.uuid4())[:8]
            try:
                recorded = record_tiered_lesson(
                    lesson_text=lesson_text,
                    task_type=outcome.task_type,
                    outcome=outcome.status,
                    source_goal=outcome.goal,
                    tier=MemoryTier.MEDIUM,
                    k_samples=1,
                    lesson_type=lesson_type,
                    lesson_id=_shared_id,
                    evidence_sources=[f"loop:{loop_id}"] if loop_id else [],
                    grounding=(lesson_groundings[_l_idx]
                               if _l_idx < len(lesson_groundings) else None),
                    # R1-5: the stored summary is this path's extraction input.
                    source_evidence=outcome.summary,
                    scope=lesson_scope,
                )
                if getattr(recorded, "lesson_id", "") == "rejected":
                    tiered_failed += 1
                else:
                    tiered_succeeded += 1
                    _shared_id = getattr(recorded, "lesson_id", _shared_id) or _shared_id
            except Exception:
                tiered_failed += 1  # recording must never block deferred delivery
            deferred_shared_ids.append(_shared_id)
    for _idx, lesson_text in enumerate(lessons):
        if lesson_text.strip():
            _shared = ""
            if _idx < len(deferred_shared_ids):
                _shared = str(deferred_shared_ids[_idx] or "")
            _store_lesson(
                task_type=outcome.task_type,
                outcome=outcome.status,
                lesson=lesson_text,
                source_goal=outcome.goal,
                goal_achieved=outcome.goal_achieved,
                goal_verdict_source=outcome.goal_verdict_source,
                lesson_id=_shared,
                grounding=(lesson_groundings[_idx]
                           if _idx < len(lesson_groundings) else None),
                source_evidence=outcome.summary,
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
