#!/usr/bin/env python3
"""Temporal/recording layer of the Maro memory system.

Extracted from memory.py — contains the outcome ledger, lesson storage,
daily log, task ledger, step traces, compression pipeline, and memory
index maintenance.  Everything here is about *recording what happened*
and *retrieving historical records*.

Higher-level reflection, tiered memory, and session bootstrap remain
in memory.py.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import logging
import textwrap
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, date, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from llm_parse import extract_json, safe_list, content_or_empty

log = logging.getLogger("maro.memory.ledger")

# Hybrid retrieval (BM25 + RRF) — graceful fallback to TF-IDF if unavailable
try:
    from hybrid_search import hybrid_rank as _hybrid_rank
    _USE_HYBRID = True
except ImportError:  # pragma: no cover
    _USE_HYBRID = False


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class Outcome:
    outcome_id: str
    goal: str
    task_type: str          # "research" | "build" | "ops" | "general" | "now" | "agenda"
    status: str             # "done" | "stuck"
    summary: str            # what was accomplished or why it failed
    lessons: List[str]      # list of lesson strings extracted from this run
    project: Optional[str] = None
    tokens_in: int = 0
    tokens_out: int = 0
    elapsed_ms: int = 0
    cost_usd: float = 0.0
    model: str = ""          # model tier used ("cheap" | "mid" | "power" | raw model string)
    recorded_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    # Agent0 steal: failure-chain recording — turns every retry into a training signal
    failure_chain: List[str] = field(default_factory=list)   # [failure_desc, diagnosis, recovery_action, ...]
    recovery_steps: int = 0  # how many retries/recoveries were needed
    # Goal-verdict tri-state (SF-2 / data-02, done ≠ achieved): True/False when
    # a verdict exists, None = unjudged. Serialized as an ABSENT key when None
    # (never null) — absence means "not judged", not "failed".
    goal_achieved: Optional[bool] = None
    goal_verdict_source: str = ""   # "closure" | "closure_unverifiable" | "provenance" | "now_self_verdict" | "now_self_verdict_free" | "now_self_verdict_error" | "deterministic_tests" | ""
    goal_verdict_confidence: Optional[float] = None  # closure judge confidence, when judged
    # Typed stop verdict (stop_verdicts.py vocabulary; "" = none recorded).
    # Sibling to goal_verdict_source: WHY the run stopped, machine-readable —
    # so learning consumers can tell "cap too low" from "goal impossible"
    # from "backend died" without parsing stuck_reason prose (stop-path
    # survey 2026-07-23, conflation 1).
    stop_verdict: str = ""
    stop_evidence: str = ""         # bounded citation for the verdict (≤500 chars at stamp sites)
    # Typed pause reason (§13e; stop_verdicts.py PAUSE_* vocabulary). "" =
    # not paused / reason untyped. Sibling of stop_verdict: the paused state
    # is a lifecycle observation, the verdict an ending observation.
    pause_reason: str = ""
    loop_id: str = ""               # join key to runs/*/metadata.json for post-closure annotation
    dry_run: bool = False            # excludes synthetic dry-run lessons from production funnel metrics
    lesson_extraction_status: str = ""  # "deferred" | "completed" | "failed" | legacy unknown
    lesson_extraction_count: int = 0
    # Prospective cohort provenance for the done-vs-achieved re-audit.
    # Empty/absent means pre-instrumentation unknown; never infer this from
    # goal wording after the fact.
    measurement_class: str = ""  # "organic" | "smoke" | "control" | "benchmark"
    handle_id: str = ""          # durable run-level dedup key (restarts share it)


# ---------------------------------------------------------------------------
# Verdict trust policy (VERIFY_LEARN_ARC §4) — single source
# ---------------------------------------------------------------------------

# Trust buckets, in a fixed vocabulary so consumers can pattern-match without
# re-deriving the policy. See docs/VERIFY_LEARN_ARC.md §4.
VERDICT_TRUST_FULL = "full"                # judged True/False, conf >= floor, not env-capped
VERDICT_TRUST_DIRECTIONAL = "directional"  # judged but low-confidence — may flavor, never gate/count
VERDICT_TRUST_NEUTRAL = "neutral"          # verdict absent (done-unverified) — present state, keep
VERDICT_TRUST_EXCLUDED = "excluded"        # verifier's-own-failure / env-capped — trust nothing

# The confidence floor below which a judged verdict is directional-only. This
# is the same 0.7 the closure machinery was built around (done-vs-achieved
# analysis, 2026-07-09); NOT a tunable — a lower bar would let the verifier's
# own low-confidence guesses gate learning.
VERDICT_CONFIDENCE_FLOOR = 0.7


def verdict_trust(outcome: Any) -> str:
    """Classify how much a run's goal-verdict may be trusted by learning.

    The single policy function (VERIFY_LEARN_ARC §4), consumed by V2 cadence
    windows, deferred-lesson extraction, and skill crystallization gates.
    Accepts an Outcome dataclass OR a plain dict row (ledger rehydration).

    Returns one of VERDICT_TRUST_{FULL,DIRECTIONAL,NEUTRAL,EXCLUDED}:

      full        — judged True/False, confidence >= floor, no env-error cap.
                    The tri-state the machinery was built for.
      directional — judged but confidence < floor. May flavor lesson framing;
                    never gates crystallization or counts in a V2 window.
      neutral     — verdict absent (goal_achieved None / done-unverified).
                    Present state, keep — absence means "not judged", not "failed".
      excluded    — closure_unverifiable (verifier's own failure) or an
                    environment-error-capped verdict. Excluded from ALL learning
                    consumers: a verifier-cwd bug must not be taught as a regression.
    """
    def _get(key, default=None):
        if isinstance(outcome, dict):
            return outcome.get(key, default)
        return getattr(outcome, key, default)

    source = str(_get("goal_verdict_source", "") or "")
    # closure_unverifiable = the verifier failed to reach a verdict (its own
    # cwd/env bug, a timeout, a probe that could not run). Environment-error
    # caps fold into this source today; if a distinct source value is ever
    # introduced it should be excluded here too.
    if source == "closure_unverifiable":
        return VERDICT_TRUST_EXCLUDED

    achieved = _get("goal_achieved", None)
    if achieved is None:
        # Unjudged — a dict row omits the key entirely when None (never null).
        return VERDICT_TRUST_NEUTRAL

    conf = _get("goal_verdict_confidence", None)
    # A judged verdict with no confidence attached (deterministic provenance
    # guard, NOW self-verdict) is authoritative — only an *explicit* low
    # confidence downgrades to directional.
    if conf is not None:
        try:
            if float(conf) < VERDICT_CONFIDENCE_FLOOR:
                return VERDICT_TRUST_DIRECTIONAL
        except (TypeError, ValueError):
            pass
    return VERDICT_TRUST_FULL


@dataclass(frozen=True)
class OutcomeVerdictStampResult:
    """Atomic outcome-verdict persistence result.

    ``missing`` is a valid absence (there is no ledger evidence to protect),
    while ``write_failed`` means a present/possibly-present row could not be
    made honest.  Keeping those states distinct prevents callers from either
    aborting useful recovery on absence or swallowing a persistence failure.
    """

    status: Literal["updated", "missing", "write_failed"]
    attempts: int = 0
    error: str = ""

    def __bool__(self) -> bool:
        """Forbid the ambiguous boolean idiom this result replaced."""
        raise TypeError(
            "OutcomeVerdictStampResult has no truth value; inspect .status")


@dataclass
class Lesson:
    lesson_id: str
    task_type: str          # what kind of task this lesson applies to
    outcome: str            # "done" | "stuck" — what happened (process status)
    lesson: str             # the insight
    source_goal: str        # which goal produced this lesson
    confidence: float       # 0.0-1.0 (starts at 0.7, adjusts with reinforcement)
    times_applied: int = 0
    times_reinforced: int = 0
    recorded_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    # Goal-verdict tri-state, same convention as Outcome: absent key = unjudged.
    # Only stamped when the verdict is already known at write time.
    goal_achieved: Optional[bool] = None
    goal_verdict_source: str = ""
    # Provenance gate (2026-07-29, lesson_provenance.py): "" (legacy) |
    # "outcome" | "prompt". "prompt" = generalized from dispatch-prompt
    # instruction text — quarantined from injection (load_lessons default),
    # kept on disk and visible in readouts. Absent key stays off the row
    # (_verdict_row), same discipline as the verdict fields.
    minted_from: str = ""
    # Retirement-by-contradiction (2026-08-02): empty = full citizen;
    # non-empty ({reason, source, contested_at}) = contested — excluded
    # from injection (load_lessons default), kept on disk. Mirrors the
    # tiered TieredLesson.contested field; UU-4 dual-written rows share a
    # lesson_id, so knowledge_web.contest_lesson stamps both stores.
    # Absent key stays off the row (_verdict_row discipline).
    contested: Dict[str, Any] = field(default_factory=dict)
    # Mint-time grounding (2026-08-06, mint_grounding.py): claim-receipt
    # stamps from the minting run's tool events, mirroring the tiered
    # TieredLesson.grounding field (dual-written rows carry the same
    # stamps). Absent key stays off the row (_verdict_row discipline).
    grounding: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class TaskLedgerEntry:
    """One entry in the per-session task ledger.

    Every executed step gets a ledger row: who did it, what was the task,
    and when it finished. Enables post-session auditing without grep'ing logs.

    Fields mirror the Feynman research agent's task ledger pattern:
        task_id   — step label (e.g. "step_3") or loop_id+index
        owner     — who executed it ("agent_loop", worker name, etc.)
        task      — the step text as given to the executor
        status    — "todo" | "in_progress" | "done" | "blocked"
        loop_id   — parent loop_id for traceability
        result_summary — first 200 chars of the step result (optional)
        completed_at   — UTC ISO timestamp when finished
    """
    task_id: str
    owner: str
    task: str
    status: str    # "todo" | "in_progress" | "done" | "blocked"
    loop_id: str = ""
    result_summary: str = ""
    completed_at: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class CompressedBatch:
    """LLM-compressed summary of a batch of older outcomes."""
    batch_id: str
    summary: str            # One compact paragraph summarising the batch
    task_types: List[str]   # Unique task types present in the batch
    outcome_ids: List[str]  # IDs of the outcomes that were compressed
    batch_size: int
    oldest_at: str
    newest_at: str
    compressed_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ---------------------------------------------------------------------------
# Storage paths
# ---------------------------------------------------------------------------

def _memory_dir() -> Path:
    from orch_items import memory_dir
    return memory_dir()


def _outcomes_path() -> Path:
    return _memory_dir() / "outcomes.jsonl"


def _lessons_path() -> Path:
    return _memory_dir() / "lessons.jsonl"


def _daily_path(for_date: Optional[date] = None) -> Path:
    d = for_date or date.today()
    return _memory_dir() / f"{d.isoformat()}.md"


def _memory_index_path() -> Path:
    return _memory_dir() / "MEMORY.md"


def _step_traces_path() -> Path:
    return _memory_dir() / "step_traces.jsonl"


def _task_ledger_path() -> Path:
    return _memory_dir() / "task_ledger.jsonl"


def _compressed_outcomes_path() -> Path:
    return _memory_dir() / "compressed_outcomes.jsonl"


# ---------------------------------------------------------------------------
# Task ledger (Phase 59 Feynman steal)
# ---------------------------------------------------------------------------

def append_task_ledger(entry: TaskLedgerEntry) -> None:
    """Append one entry to the task ledger (task_ledger.jsonl)."""
    path = _task_ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "task_id": entry.task_id,
        "owner": entry.owner,
        "task": entry.task,
        "status": entry.status,
        "loop_id": entry.loop_id,
        "result_summary": entry.result_summary,
        "completed_at": entry.completed_at or datetime.now(timezone.utc).isoformat(),
        "created_at": entry.created_at,
    }
    try:
        from file_lock import locked_append
        locked_append(path, json.dumps(row))
    except Exception as exc:
        log.debug("append_task_ledger: write failed: %s", exc)


def load_task_ledger(
    loop_id: str = "",
    limit: int = 100,
) -> List[TaskLedgerEntry]:
    """Load recent task ledger entries, optionally filtered by loop_id."""
    path = _task_ledger_path()
    if not path.exists():
        return []
    entries = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                if loop_id and d.get("loop_id", "") != loop_id:
                    continue
                entries.append(TaskLedgerEntry(
                    task_id=d.get("task_id", ""),
                    owner=d.get("owner", ""),
                    task=d.get("task", ""),
                    status=d.get("status", ""),
                    loop_id=d.get("loop_id", ""),
                    result_summary=d.get("result_summary", ""),
                    completed_at=d.get("completed_at", ""),
                    created_at=d.get("created_at", ""),
                ))
            except Exception:
                continue
    except Exception:
        pass
    return list(reversed(entries))[:limit]


# ---------------------------------------------------------------------------
# Step trace recording (Meta-Harness steal)
# ---------------------------------------------------------------------------

def record_step_trace(
    outcome_id: str,
    goal: str,
    step_outcomes: List[Any],
    *,
    task_type: str = "general",
) -> None:
    """Persist per-step execution trace alongside the outcome record.

    Stores all step details (step text, status, result, summary, stuck_reason)
    in memory/step_traces.jsonl keyed by outcome_id. The evolver reads these
    to give the proposer full execution context, not just summary metrics.

    Args:
        outcome_id: ID from the Outcome returned by reflect_and_record.
        goal: The top-level goal for this run.
        step_outcomes: List of StepOutcome objects from agent_loop.
        task_type: Task classification (e.g. "agenda", "research").
    """
    steps_data = []
    for s in step_outcomes:
        entry: Dict[str, Any] = {
            "step": getattr(s, "text", "") or getattr(s, "step", ""),
            "status": getattr(s, "status", ""),
            "result": (getattr(s, "result", "") or "")[:500],
        }
        sr = getattr(s, "stuck_reason", None)
        if sr:
            entry["stuck_reason"] = str(sr)[:300]
        # Per-step learning (2026-07-27): the verify ladder's per-step
        # confidence ("strong"|"weak"|"inferred"|"unverified") is the
        # granularity step-level extraction keys on — persist it so the
        # trace can answer "which steps individually verified" after the
        # ephemeral StepOutcome objects are gone.
        conf = getattr(s, "confidence", "")
        if conf:
            entry["confidence"] = conf
        steps_data.append(entry)

    trace = {
        "outcome_id": outcome_id,
        "goal": goal[:200],
        "task_type": task_type,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "steps": steps_data,
    }
    try:
        from file_lock import locked_append
        locked_append(_step_traces_path(), json.dumps(trace))
    except OSError as exc:
        log.warning("record_step_trace: failed to write: %s", exc)


def load_step_traces(outcome_ids: List[str]) -> Dict[str, Any]:
    """Load step traces for the given outcome_ids.

    Returns:
        Dict mapping outcome_id -> trace dict. Missing IDs are absent.
    """
    path = _step_traces_path()
    if not path.exists():
        return {}

    target_ids = set(outcome_ids)
    result: Dict[str, Any] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                trace = json.loads(line)
                oid = trace.get("outcome_id", "")
                if oid in target_ids:
                    result[oid] = trace
            except json.JSONDecodeError:
                pass
    except OSError:
        pass
    return result


# ---------------------------------------------------------------------------
# Text similarity (simple — for dedup)
# ---------------------------------------------------------------------------

def _text_similarity(a: str, b: str) -> float:
    """Simple word-overlap similarity for lesson deduplication."""
    words_a = set(re.sub(r"[^a-z0-9 ]", "", a.lower()).split())
    words_b = set(re.sub(r"[^a-z0-9 ]", "", b.lower()).split())
    if not words_a or not words_b:
        return 0.0
    intersection = len(words_a & words_b)
    union = len(words_a | words_b)
    return intersection / union if union > 0 else 0.0


# ---------------------------------------------------------------------------
# Outcome recording
# ---------------------------------------------------------------------------

def _verdict_row(obj: Any) -> Dict[str, Any]:
    """asdict() with tri-state discipline: an unjudged verdict must serialize
    as an ABSENT goal_achieved key (never null), and empty verdict/join fields
    stay off the row entirely — the 1381 pre-fix rows set the precedent that
    consumers treat a missing key as "not judged"."""
    row = asdict(obj)
    if row.get("goal_achieved") is None:
        row.pop("goal_achieved", None)
    if not row.get("goal_verdict_source"):
        row.pop("goal_verdict_source", None)
    if row.get("goal_verdict_confidence", 0.0) is None:
        row.pop("goal_verdict_confidence")
    if "loop_id" in row and not row["loop_id"]:
        row.pop("loop_id")
    if "measurement_class" in row and not row["measurement_class"]:
        row.pop("measurement_class")
    if "handle_id" in row and not row["handle_id"]:
        row.pop("handle_id")
    if "stop_verdict" in row and not row["stop_verdict"]:
        row.pop("stop_verdict")
    if "stop_evidence" in row and not row["stop_evidence"]:
        row.pop("stop_evidence")
    if "pause_reason" in row and not row["pause_reason"]:
        row.pop("pause_reason")
    if "minted_from" in row and not row["minted_from"]:
        row.pop("minted_from")
    if "contested" in row and not row["contested"]:
        row.pop("contested")
    if "grounding" in row and not row["grounding"]:
        row.pop("grounding")
    return row


def record_outcome(
    goal: str,
    status: str,
    summary: str,
    *,
    task_type: str = "general",
    project: Optional[str] = None,
    lessons: Optional[List[str]] = None,
    lesson_ids: Optional[List[str]] = None,
    lesson_groundings: Optional[List[List[Dict[str, Any]]]] = None,
    tokens_in: int = 0,
    tokens_out: int = 0,
    elapsed_ms: int = 0,
    model: str = "",
    failure_chain: Optional[List[str]] = None,
    recovery_steps: int = 0,
    goal_achieved: Optional[bool] = None,
    goal_verdict_source: str = "",
    loop_id: str = "",
    dry_run: bool = False,
    lesson_extraction_status: str = "",
    lesson_extraction_count: int = 0,
    measurement_class: str = "",
    handle_id: str = "",
    stop_verdict: str = "",
    stop_evidence: str = "",
    pause_reason: str = "",
    lesson_evidence: str = "",
) -> Outcome:
    """Record the outcome of a completed run.

    Appends to outcomes.jsonl and daily log. Also extracts lessons if provided.

    Args:
        failure_chain: Agent0 steal — list of failure/diagnosis/recovery strings describing
                       the error-recovery trajectory (e.g. ["step 3 failed: timeout",
                       "diagnosis: rate limit", "recovery: waited 60s and retried"]).
                       Turns retries into training signal for future runs.
        recovery_steps: How many retries or recovery actions were needed.
        goal_achieved: Tri-state goal verdict (done ≠ achieved): True/False when
                       a verdict exists at record time, None = unjudged (key is
                       omitted from the row). Agenda-lane verdicts land after
                       closure via stamp_outcome_verdict(loop_id, ...).
        goal_verdict_source: Where the verdict came from ("closure",
                       "closure_unverifiable", "provenance", "now_self_verdict").
        loop_id: Loop id for this run, when known — the join key that lets the
                       post-closure verdict annotation find this row.
        dry_run: Persisted cohort exclusion; dry-run lessons are synthetic.
        lesson_extraction_status/count: Durable funnel and idempotency state.
        measurement_class: Explicit done-vs-achieved cohort provenance. Empty
                       means unknown; callers must not infer it from goal text.
        handle_id: Run-level key used to collapse restarted loop outcomes.
    """
    import uuid
    from metrics import estimate_cost
    # Vocabulary holds at every persistence boundary, not just the stamp
    # rail: a direct caller's off-vocab string would live in the ledger
    # while curation silently falls back — stores disagreeing instead of
    # rejecting at ingress (2026-07-31 slice-1 adversarial review #6).
    if pause_reason:
        from stop_verdicts import VALID_PAUSE_REASONS
        if pause_reason not in VALID_PAUSE_REASONS:
            log.debug("record_outcome: dropping off-vocabulary pause_reason %r",
                      pause_reason)
            pause_reason = ""
    # Same rule for the sibling field. The 2026-07-31 review closed the hole
    # on pause_reason only, so stop_verdict kept an ingress an off-vocabulary
    # string could walk through — stamp_outcome_stop_verdict and
    # LoopContext.stamp_stop both reject, this one didn't, and the NOW lane
    # became a direct caller of it 2026-08-02.
    if stop_verdict:
        from stop_verdicts import VALID_STOP_VALUES
        if stop_verdict not in VALID_STOP_VALUES:
            log.debug("record_outcome: dropping off-vocabulary stop_verdict %r",
                      stop_verdict)
            stop_verdict = ""
            stop_evidence = ""
    cost_usd = estimate_cost(tokens_in, tokens_out, model=model or None)
    outcome = Outcome(
        outcome_id=str(uuid.uuid4())[:8],
        goal=goal,
        task_type=task_type,
        status=status,
        summary=summary,
        project=project,
        lessons=lessons or [],
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        elapsed_ms=elapsed_ms,
        cost_usd=cost_usd,
        model=model,
        failure_chain=failure_chain or [],
        recovery_steps=recovery_steps,
        goal_achieved=goal_achieved,
        goal_verdict_source=goal_verdict_source,
        loop_id=loop_id,
        dry_run=bool(dry_run),
        lesson_extraction_status=lesson_extraction_status,
        lesson_extraction_count=max(0, int(lesson_extraction_count)),
        measurement_class=measurement_class,
        handle_id=handle_id,
        stop_verdict=stop_verdict,
        stop_evidence=(stop_evidence or "")[:500],
        pause_reason=pause_reason,
    )

    # Append to outcomes ledger
    from file_lock import locked_append
    locked_append(_outcomes_path(), json.dumps(_verdict_row(outcome)))

    # Append to daily log
    _append_daily_log(outcome)

    # Store lessons. `lesson_ids` (UU-4, parallel to `lessons`) carries the
    # id each lesson already holds in the tiered store, so the flat row
    # joins instead of minting a divergent uuid. Absent/short list → mint
    # as before (legacy callers unchanged).
    for _idx, lesson_text in enumerate(lessons or []):
        if lesson_text.strip():
            _shared = ""
            if lesson_ids and _idx < len(lesson_ids):
                _shared = str(lesson_ids[_idx] or "")
            # Mint-grounding stamps ride parallel to lesson_ids (same
            # index contract); absent/short list → unstamped, as before.
            _ground = None
            if lesson_groundings and _idx < len(lesson_groundings):
                _ground = lesson_groundings[_idx]
            _store_lesson(
                task_type=task_type,
                outcome=status,
                lesson=lesson_text,
                source_goal=goal,
                goal_achieved=goal_achieved,
                goal_verdict_source=goal_verdict_source,
                lesson_id=_shared,
                grounding=_ground,
                # R1-5: the extraction evidence rides to the provenance
                # classifier — scaffolding can arrive via step output, not
                # just the goal.
                source_evidence=lesson_evidence,
            )

    # Update MEMORY.md index
    _update_memory_index()

    return outcome


_DAILY_LOG_SUMMARY_CUT = 400


def _daily_log_summary(summary: str) -> str:
    """One-glance version of a stored summary, honest when it trims."""
    summary = str(summary or "").strip()
    if len(summary) <= _DAILY_LOG_SUMMARY_CUT:
        return summary
    return (f"{summary[:_DAILY_LOG_SUMMARY_CUT]}… "
            f"[{len(summary)} chars total; full text in outcomes.jsonl]")


def _append_daily_log(outcome: Outcome):
    """Append a human-readable entry to today's daily log."""
    path = _daily_path()
    # Prefer the goal verdict over process status: done-but-goal-not-achieved
    # renders as a failure, not a success (SF-2).
    status_icon = "\u2713" if (outcome.status == "done" and outcome.goal_achieved is not False) else "\u2717"
    status_str = outcome.status
    if outcome.goal_achieved is False:
        status_str += " (goal NOT achieved)"
    elif outcome.goal_achieved is True:
        status_str += " (goal achieved)"
    tokens = f"{outcome.tokens_in}in+{outcome.tokens_out}out"
    cost_str = f" (${outcome.cost_usd:.6f})" if outcome.cost_usd else ""
    entry = (
        f"\n## [{outcome.recorded_at[:10]}] {status_icon} {outcome.goal[:80]}\n"
        f"- **Status**: {status_str}\n"
        f"- **Type**: {outcome.task_type}\n"
        # DISPLAY, so it gets a display bound: since 2026-08-06 the stored
        # summary carries real per-step evidence for the lesson extractor
        # (loop_finalize._step_evidence), which is the right size for a prompt
        # and the wrong size for a human skimming a day's runs.
        f"- **Summary**: {_daily_log_summary(outcome.summary)}\n"
        f"- **Tokens**: {tokens} in {outcome.elapsed_ms}ms{cost_str}\n"
    )
    if outcome.lessons:
        entry += "- **Lessons**:\n" + "".join(f"  - {l}\n" for l in outcome.lessons)
    if outcome.project:
        entry += f"- **Project**: {outcome.project}\n"

    # Multi-line block append can exceed PIPE_BUF (4096B) — bare open('a')
    # interleaves/tears under concurrent writers, so take the file's lock.
    from file_lock import locked_write
    with locked_write(path):
        with open(path, "a", encoding="utf-8") as f:
            f.write(entry)


def mark_outcomes_superseded(handle_id: str, *, max_attempts: int = 1) -> int:
    """Resume-≡-uninterrupted (continuation decree, Jeremy 2026-08-02): after
    a resumed run finalizes, the run identity has ONE authoritative outcome —
    the newest row for this handle_id. Every older same-handle row gains a
    ``superseded_by`` marker pointing at it.

    Addendum, never overwrite (Jeremy's clarification): the old rows keep
    every field they had — the marker is added so attempt-counting consumers
    (verdict_flow, done-vs-achieved cohorts, the recall repeat-guard) can
    exclude interrupted segments without the data being touched. Same locked
    read + atomic publish pattern as stamp_outcome_verdict.

    Returns the number of rows marked (0 = nothing to do or handle unseen).
    """
    if not handle_id:
        return 0
    path = _outcomes_path()
    marked = {"n": 0}
    from file_lock import atomic_write, locked_write

    def _mark(old: str) -> str:
        lines = old.splitlines()
        rows = []
        for i, line in enumerate(lines):
            line_s = line.strip()
            if not line_s:
                continue
            try:
                row = json.loads(line_s)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and str(row.get("handle_id") or "") == handle_id:
                rows.append((i, row))
        if len(rows) < 2:
            return old  # zero or one row — nothing is superseded
        # Newest row (last appended) is the authority; it survives unmarked.
        final_idx, final_row = rows[-1]
        final_id = str(final_row.get("outcome_id") or "")
        now = datetime.now(timezone.utc).isoformat()
        for i, row in rows[:-1]:
            if row.get("superseded_by"):
                continue  # already marked by an earlier resume — leave it
            row["superseded_by"] = final_id
            row["superseded_at"] = now
            lines[i] = json.dumps(row)
            marked["n"] += 1
        return "\n".join(lines) + ("\n" if lines else "")

    for attempt in range(1, max(1, int(max_attempts)) + 1):
        marked["n"] = 0
        try:
            with locked_write(path):
                try:
                    old = path.read_text(encoding="utf-8")
                except FileNotFoundError:
                    return 0
                new = _mark(old)
                if new != old:
                    atomic_write(path, new)
            return marked["n"]
        except OSError:
            if attempt >= max(1, int(max_attempts)):
                return 0
    return 0


def stamp_outcome_verdict(
    loop_id: str,
    *,
    goal_achieved: Optional[bool],
    goal_verdict_source: str,
    goal_verdict_confidence: Optional[float] = None,
    max_attempts: int = 1,
) -> OutcomeVerdictStampResult:
    """Atomically stamp a verdict, distinguishing absence from write failure.

    The agenda lane records its outcome at loop finalization, but the closure
    verdict is judged afterwards (handle.py) — so the verdict has to land on
    the row post-hoc. Finds the NEWEST row whose loop_id matches and merges
    the verdict fields in, mirroring run-metadata semantics:

    - goal_achieved True/False sets the key; None (unjudged, e.g. source
      "closure_unverifiable") leaves any existing goal_achieved untouched —
      an unverifiable closure must never erase a provenance-guard False.
    - goal_verdict_source / goal_verdict_confidence are always updated.

    Rewrites outcomes.jsonl with a locked read + atomic publish, safe against
    concurrent appends. The loop-id lookup and merge occur inside the same
    critical section. ``max_attempts`` bounds idempotent retries after an
    ``OSError``; a missing row is returned immediately and is never retried.
    """
    if not loop_id:
        return OutcomeVerdictStampResult("missing")
    path = _outcomes_path()

    attempts = max(1, int(max_attempts))
    from file_lock import atomic_write, locked_write
    for attempt in range(1, attempts + 1):
        updated = {"hit": False}

        def _stamp(old: str) -> str:
            lines = old.splitlines()
            # Newest matching row wins — a restarted goal appends a fresh row per loop.
            target_idx = None
            for i in range(len(lines) - 1, -1, -1):
                line = lines[i].strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict) and row.get("loop_id") == loop_id:
                    target_idx = i
                    break
            if target_idx is None:
                return old
            row = json.loads(lines[target_idx])
            # Re-stamp honesty (Jeremy decree 2026-08-10: corrections may
            # flip a verdict "but be honest about it and note they were
            # failures at run time"): overwriting an existing judged
            # verdict preserves the superseded one on the row itself, not
            # just in event history. Only fires on a real re-stamp of a
            # judged row — the first verdict landing on an unjudged row
            # writes no history.
            if goal_achieved is not None and row.get("goal_achieved") is not None:
                row.setdefault("verdict_history", []).append({
                    "goal_achieved": row.get("goal_achieved"),
                    "goal_verdict_source": row.get("goal_verdict_source", ""),
                    "goal_verdict_at": row.get("goal_verdict_at", ""),
                    "goal_verdict_confidence": row.get("goal_verdict_confidence"),
                    "superseded_at": datetime.now(timezone.utc).isoformat(),
                    "superseded_by": goal_verdict_source,
                })
            if goal_achieved is not None:
                row["goal_achieved"] = bool(goal_achieved)
            row["goal_verdict_source"] = goal_verdict_source
            # When the verdict landed (chunk B, 2026-07-31): the row's own ts
            # is record time; without this stamp the framing→verdict delay —
            # the flow number the whole learning pipeline divides by — is
            # unmeasurable. Unverifiable stamps get it too: "judged, could
            # not verify" is itself a verdict event.
            row["goal_verdict_at"] = datetime.now(timezone.utc).isoformat()
            if goal_verdict_confidence is not None:
                row["goal_verdict_confidence"] = float(goal_verdict_confidence)
            lines[target_idx] = json.dumps(row)
            updated["hit"] = True
            updated["row"] = row
            return "\n".join(lines) + ("\n" if lines else "")

        try:
            # Read, locate, and publish under the same append-compatible lock.
            # Unlike locked_rmw, this does not create/rewrite the ledger when
            # the file or loop row is absent.
            with locked_write(path):
                try:
                    old = path.read_text(encoding="utf-8")
                except FileNotFoundError:
                    return OutcomeVerdictStampResult(
                        "missing", attempts=attempt)
                new = _stamp(old)
                if not updated["hit"]:
                    log.debug(
                        "stamp_outcome_verdict: no outcomes row with loop_id=%s",
                        loop_id,
                    )
                    return OutcomeVerdictStampResult(
                        "missing", attempts=attempt)
                atomic_write(path, new)
        except OSError as exc:
            if attempt < attempts:
                continue
            log.warning(
                "stamp_outcome_verdict: rewrite failed for loop %s after %d attempt(s): %s",
                loop_id, attempt, exc,
            )
            return OutcomeVerdictStampResult(
                "write_failed", attempts=attempt, error=str(exc))
        _maybe_emit_contradiction_candidate(loop_id, updated.get("row") or {})
        _maybe_record_skill_injection_outcomes(loop_id, updated.get("row") or {})
        return OutcomeVerdictStampResult("updated", attempts=attempt)


def stamp_outcome_stop_verdict(loop_id: str, stop_verdict: str,
                               stop_evidence: str = "") -> bool:
    """Post-hoc stop-verdict stamp on the newest outcomes row for loop_id.

    The reachable-but-not-worth-it verdict is decided AFTER the run closed
    (director escalation "close" — a later value/cost judgment about a run
    that ended "stuck"), so it must land on the row post-hoc, the same way
    closure verdicts do. Merge-only: never touches goal-verdict fields.
    Evidence, when given, rides along so the row stays auditable without a
    metadata join. Returns True when a row was updated. Best-effort —
    False on any miss, including an off-vocabulary verdict (fail to
    unstamped so status fallbacks apply, never a phantom value).
    """
    if not loop_id or not stop_verdict:
        return False
    from stop_verdicts import VALID_STOP_VALUES
    if stop_verdict not in VALID_STOP_VALUES:
        log.warning("stamp_outcome_stop_verdict: off-vocabulary verdict %r "
                    "for loop %s dropped", stop_verdict, loop_id)
        return False
    path = _outcomes_path()
    from file_lock import atomic_write, locked_write
    hit = {"v": False}

    def _stamp(old: str) -> str:
        lines = old.splitlines()
        for i in range(len(lines) - 1, -1, -1):
            line = lines[i].strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and row.get("loop_id") == loop_id:
                row["stop_verdict"] = stop_verdict
                if stop_evidence:
                    row["stop_evidence"] = (stop_evidence or "")[:500]
                lines[i] = json.dumps(row)
                hit["v"] = True
                break
        return "\n".join(lines) + ("\n" if lines else "")

    try:
        with locked_write(path):
            try:
                old = path.read_text(encoding="utf-8")
            except FileNotFoundError:
                return False
            new = _stamp(old)
            if hit["v"]:
                atomic_write(path, new)
    except OSError as exc:
        log.warning("stamp_outcome_stop_verdict failed for loop %s: %s",
                    loop_id, exc)
        return False
    return hit["v"]


def outcome_row_has_step_lessons(loop_id: str) -> bool:
    """True when the newest outcomes row for loop_id carries a
    ``step_lesson_count`` key — the idempotence marker for per-step
    extraction. Raw-line scan (the Outcome dataclass drops unknown keys, so
    a typed load can't see the stamp). Missing file/row → False."""
    if not loop_id:
        return False
    path = _outcomes_path()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("loop_id") == loop_id:
            return "step_lesson_count" in row
    return False


def stamp_outcome_step_lessons(loop_id: str, count: int) -> bool:
    """Stamp ``step_lesson_count`` onto the newest outcomes row for loop_id.

    Per-step learning (2026-07-27): marks that the step-level extraction pass
    ran for this run (count may be 0 when the LLM returned nothing usable).
    run_deferred_learning is called for loops that didn't defer, so without
    this marker a failure-shaped run would re-pay the extraction call on
    every post-closure pass. Merge-only, same shape as the stop-verdict
    stamp. Returns True when a row was updated.
    """
    if not loop_id:
        return False
    path = _outcomes_path()
    from file_lock import atomic_write, locked_write
    hit = {"v": False}

    def _stamp(old: str) -> str:
        lines = old.splitlines()
        for i in range(len(lines) - 1, -1, -1):
            line = lines[i].strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and row.get("loop_id") == loop_id:
                row["step_lesson_count"] = int(count)
                lines[i] = json.dumps(row)
                hit["v"] = True
                break
        return "\n".join(lines) + ("\n" if lines else "")

    try:
        with locked_write(path):
            try:
                old = path.read_text(encoding="utf-8")
            except FileNotFoundError:
                return False
            new = _stamp(old)
            if hit["v"]:
                atomic_write(path, new)
    except OSError as exc:
        log.warning("stamp_outcome_step_lessons failed for loop %s: %s",
                    loop_id, exc)
        return False
    return hit["v"]


def _maybe_emit_contradiction_candidate(loop_id: str, row: dict) -> None:
    """Chunk-4 contradiction wiring (era-07's "natural collision detector"):
    a fully-trusted goal_achieved=False verdict on a run that was injected
    with specific rules/lessons is a candidate collision between crystallized
    knowledge and reality. Emits the append-only CONTRADICTION_CANDIDATE
    event; the capped adjudicator (knowledge_lens.
    adjudicate_contradiction_candidates, evolver cadence) decides whether the
    failure actually contradicts what was cited — the emitter never judges.

    Gates, in order:
    - goal_achieved is False (True/None verdicts collide with nothing);
    - verdict_trust(row) == FULL — the era-10 single-gate law: closure
      verdicts are consumed only through verdict_trust, so a directional
      (low-confidence) or excluded (verifier's-own-failure) False can never
      seed a contradiction against a standing rule;
    - the STAMPED LOOP's run dir has a non-empty
      source/recall_citations.json (written by recall's loop slice). The
      join is by durable identity — runs.resolve_run_dir(loop_id) — never
      the ambient run-dir ContextVar (chunk-4 review F6: a cross-process
      re-stamp, e.g. audit_repair, either has no context or somebody
      else's; the citation join belongs to the run that was actually
      injected, and the index knows which one that was). Unresolvable
      loop → no event.

    Never raises: candidate emission is observability-grade, and a log
    failure must not perturb the verdict stamp it rides on.
    """
    try:
        if row.get("goal_achieved") is not False:
            return
        if verdict_trust(row) != VERDICT_TRUST_FULL:
            return
        import runs as _runs
        rd = _runs.resolve_run_dir(loop_id)
        if rd is None:
            return
        cit_path = Path(rd) / "source" / "recall_citations.json"
        if not cit_path.exists():
            return
        cit = json.loads(cit_path.read_text(encoding="utf-8"))
        rule_ids = [str(r) for r in (cit.get("rule_ids") or []) if r]
        lesson_ids = [str(l) for l in (cit.get("lesson_ids") or []) if l]
        if not rule_ids and not lesson_ids:
            return
        from captains_log import log_event, CONTRADICTION_CANDIDATE
        log_event(
            CONTRADICTION_CANDIDATE,
            subject=loop_id,
            summary=(
                f"Run failed with full-trust verdict while injected with "
                f"{len(rule_ids)} rule(s) / {len(lesson_ids)} lesson(s) — "
                "candidate collision, awaiting adjudication."),
            context={
                "loop_id": loop_id,
                "rule_ids": rule_ids,
                "lesson_ids": lesson_ids,
                "failure_summary": str(row.get("summary", "") or "")[:300],
                "goal_preview": str(row.get("goal", "") or "")[:200],
                "verdict_source": str(row.get("goal_verdict_source", "") or ""),
                # MH taxonomy relabel (#7 Overgeneralization, 2026-08-09):
                # this event IS maro's model—memory collision signal —
                # crystallized knowledge injected, reality disagreed.
                # Candidate-grade until the adjudicator rules.
                "mh_edge": "model-memory",
                "mh_class": "overgeneralization_candidate",
            },
            loop_id=loop_id,
            related_ids=[f"rule:{r}" for r in rule_ids],
        )
    except Exception:
        log.debug(
            "contradiction-candidate emit failed for %s", loop_id,
            exc_info=True)


def _maybe_record_skill_injection_outcomes(loop_id: str, row: dict) -> None:
    """Run-verdict skill attribution (2026-07-29 measurement-honesty fix):
    when a FULL-trust closure verdict lands, credit it to the skills that
    were ACTUALLY injected into the run's prompts — the run dir's
    source/skills_manifest.jsonl, written at each injection site — not the
    keyword-matched bystanders the legacy per-step counters credit.

    Gates, in order:
    - goal_achieved is a real bool (None = unjudged, nothing to attribute);
    - verdict_trust(row) == FULL — the era-10 single-gate law, same as the
      contradiction emitter beside this: learning counters must not be fed
      by low-confidence or verifier's-own-failure verdicts;
    - the STAMPED LOOP's run dir resolves durably (runs.resolve_run_dir,
      never the ambient ContextVar — chunk-4 review F6) and holds a
      non-empty manifest;
    - idempotence marker: verdicts can be re-stamped (audit_repair /
      audit_policy), and a re-stamp must not double-count — the first
      successful attribution writes source/skill_attribution.json to the
      run dir and later stamps skip.

    Never raises: attribution is telemetry, and a failure must not perturb
    the verdict stamp it rides on.
    """
    try:
        if not isinstance(row.get("goal_achieved"), bool):
            return
        if verdict_trust(row) != VERDICT_TRUST_FULL:
            return
        import runs as _runs
        rd = _runs.resolve_run_dir(loop_id)
        if rd is None:
            return
        manifest = Path(rd) / "source" / "skills_manifest.jsonl"
        if not manifest.is_file():
            return
        marker = Path(rd) / "source" / "skill_attribution.json"
        if marker.exists():
            return
        skill_ids: list = []
        seen: set = set()
        for line in manifest.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            for entry in rec.get("skills") or []:
                sid = str(entry.get("id", "") or "")
                if sid and sid not in seen:
                    seen.add(sid)
                    skill_ids.append(sid)
        if not skill_ids:
            return
        achieved = bool(row["goal_achieved"])
        from skills import record_skill_injection_outcome
        for sid in skill_ids:
            record_skill_injection_outcome(sid, achieved)
        marker.write_text(json.dumps({
            "loop_id": loop_id,
            "goal_achieved": achieved,
            "skill_ids": skill_ids,
            "attributed_at": datetime.now(timezone.utc).isoformat(),
        }), encoding="utf-8")
    except Exception:
        log.debug(
            "skill-injection attribution failed for %s", loop_id,
            exc_info=True)


def load_outcome_by_loop_id(loop_id: str) -> Optional[Outcome]:
    """Load the NEWEST outcomes row matching loop_id, rehydrated as an Outcome.

    Companion to stamp_outcome_verdict: the deferred-learning path
    (data-r2-01) reads the row back post-closure to get the goal/summary
    context AND the verdict that was stamped onto it. Absent tri-state keys
    rehydrate to their dataclass defaults (goal_achieved=None = unjudged).
    """
    if not loop_id:
        return None
    path = _outcomes_path()
    if not path.exists():
        return None
    from dataclasses import fields as _dc_fields
    _known = {f.name for f in _dc_fields(Outcome)}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("loop_id") == loop_id:
            try:
                return Outcome(**{k: v for k, v in row.items() if k in _known})
            except TypeError:
                return None
    return None


def annotate_outcome_lessons(loop_id: str, lessons: List[str]) -> bool:
    """Stamp deferred lessons onto the already-written outcomes row for loop_id.

    The agenda lane can defer lesson extraction past closure judging
    (data-r2-01) — the row is written at finalization with lessons=[], and
    the verdict-aware extraction fills them in here. Completed-zero is also
    stamped explicitly so a later finalize does not repay extraction. Uses
    the same newest-row-wins lookup rule as stamp_outcome_verdict.
    """
    if not loop_id:
        return False
    path = _outcomes_path()
    if not path.exists():
        return False

    updated = {"hit": False}

    def _stamp(old: str) -> str:
        lines = old.splitlines()
        target_idx = None
        for i in range(len(lines) - 1, -1, -1):
            line = lines[i].strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and row.get("loop_id") == loop_id:
                target_idx = i
                break
        if target_idx is None:
            return old
        row = json.loads(lines[target_idx])
        row["lessons"] = list(lessons)
        row["lesson_extraction_status"] = "completed"
        row["lesson_extraction_count"] = len(lessons)
        lines[target_idx] = json.dumps(row)
        updated["hit"] = True
        return "\n".join(lines) + ("\n" if lines else "")

    from file_lock import locked_rmw
    try:
        locked_rmw(path, _stamp)
    except OSError as exc:
        log.warning("annotate_outcome_lessons: rewrite failed for loop %s: %s", loop_id, exc)
        return False
    if not updated["hit"]:
        log.debug("annotate_outcome_lessons: no outcomes row with loop_id=%s", loop_id)
    return updated["hit"]


def annotate_outcome_extraction_failure(loop_id: str) -> bool:
    """Durably mark a deferred extraction attempt failed and retryable."""
    if not loop_id:
        return False
    path = _outcomes_path()
    if not path.exists():
        return False
    updated = {"hit": False}

    def _stamp(old: str) -> str:
        lines = old.splitlines()
        for i in range(len(lines) - 1, -1, -1):
            try:
                row = json.loads(lines[i])
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(row, dict) and row.get("loop_id") == loop_id:
                row["lesson_extraction_status"] = "failed"
                row["lesson_extraction_count"] = 0
                lines[i] = json.dumps(row)
                updated["hit"] = True
                break
        return "\n".join(lines) + ("\n" if lines else "")

    from file_lock import locked_rmw
    try:
        locked_rmw(path, _stamp)
    except OSError as exc:
        log.warning("annotate_outcome_extraction_failure: rewrite failed for loop %s: %s", loop_id, exc)
        return False
    return updated["hit"]


# ---------------------------------------------------------------------------
# Lesson storage + retrieval
# ---------------------------------------------------------------------------

_INJECTION_PATTERNS = (
    "ignore previous", "ignore above", "disregard", "system:", "[INST]", "[/INST]",
    "<|system|>", "<|im_start|>", "you are now", "new instructions:", "override:",
    "forget everything", "act as if",
)


def _lesson_looks_adversarial(text: str) -> bool:
    """Reject lessons that look like prompt injection attempts."""
    lower = text.lower()
    return any(p in lower for p in _INJECTION_PATTERNS)


def _store_lesson(
    task_type: str,
    outcome: str,
    lesson: str,
    source_goal: str,
    confidence: float = 0.7,
    goal_achieved: Optional[bool] = None,
    goal_verdict_source: str = "",
    minted_from: str = "",
    lesson_id: str = "",
    grounding: Optional[List[Dict[str, Any]]] = None,
    source_evidence: str = "",
) -> Lesson:
    """Append a lesson to the lessons ledger, or reinforce existing near-duplicate.

    Provenance gate (2026-07-29): ``minted_from`` is classified here at the
    choke point when the caller leaves it "" — source_goal is the FULL goal
    on this path, so the scaffolding-echo signal has everything it needs. A
    prompt-derived lesson matching an existing row is ignored (no
    reinforcement): instruction text must not move persistent state.
    """
    import uuid

    # Sanitize: reject lessons that look like prompt injection
    if _lesson_looks_adversarial(lesson):
        log.warning("lesson rejected (injection pattern detected): %s", lesson[:100])
        # Return a dummy lesson so callers don't break, but don't persist it
        return Lesson(
            lesson_id="rejected",
            task_type=task_type,
            outcome=outcome,
            lesson="[rejected: injection pattern]",
            source_goal=source_goal,
            confidence=0.0,
        )
    if not minted_from:
        try:
            from lesson_provenance import (classify_lesson_provenance,
                                           provenance_gate_enabled)
            if provenance_gate_enabled():
                minted_from = classify_lesson_provenance(
                    lesson, source_goal, source_evidence)
        except Exception:
            minted_from = ""
    # Pass 1: fast exact-text dedup (no limit — prevents unbounded accumulation)
    # Pass 2: near-duplicate check within recent 100 lessons (word-overlap ≥ 0.8)
    # include_quarantined/contested — dedup must see excluded rows or a
    # re-minted duplicate would append a fresh live copy, laundering the
    # exclusion.
    existing = load_lessons(task_type=task_type, limit=500,
                            include_quarantined=True,
                            include_contested=True)
    for ex in existing:
        if ex.lesson == lesson:
            if minted_from == "prompt":
                log.info("prompt-derived flat re-record of %s ignored "
                         "(provenance gate)", ex.lesson_id)
                return ex
            # Exact match: reinforce without touching confidence (it's already there)
            ex.times_reinforced += 1
            if (ex.minted_from == "prompt" and minted_from == "outcome"
                    and not ex.contested):
                # Contested rows never regain citizenship via a duplicate
                # write (mirrors the tiered anti-laundering guard).
                # Outcome-derived re-record clears quarantine — mirrors the
                # tiered _reinforce_tiered_lesson citizenship rule. The
                # incoming record must be affirmatively outcome-classified:
                # an unclassified "" (gate off) must not clear, or disabling
                # the killswitch re-arms stamped rows via the next duplicate
                # write (adversarial review 2026-07-29).
                ex.minted_from = "outcome"
                log.info("quarantined flat lesson %s cleared by an "
                         "outcome-derived re-record", ex.lesson_id)
            _rewrite_lessons_file(task_type, existing)
            return ex
    for ex in existing[:100]:
        if _text_similarity(ex.lesson, lesson) > 0.8:
            if minted_from == "prompt":
                log.info("prompt-derived flat re-record of %s ignored "
                         "(provenance gate)", ex.lesson_id)
                return ex
            # Reinforce existing lesson and persist the update
            ex.times_reinforced += 1
            if not ex.contested:  # contested rows: sighting counted, nothing else moves
                ex.confidence = min(1.0, ex.confidence + 0.05)
            if (ex.minted_from == "prompt" and minted_from == "outcome"
                    and not ex.contested):
                ex.minted_from = "outcome"  # same citizenship rule as above
                log.info("quarantined flat lesson %s cleared by an "
                         "outcome-derived re-record", ex.lesson_id)
            _rewrite_lessons_file(task_type, existing)
            return ex

    # UU-4 (BACKLOG LT arc): a caller that dual-writes the same lesson to
    # this ledger AND the tiered store may supply one shared id, so the two
    # rows join. Fresh mints only — the reinforce path above returns the
    # EXISTING row with its original id, and that id wins (the join then
    # points at the row that actually holds the lesson).
    l = Lesson(
        lesson_id=lesson_id or str(uuid.uuid4())[:8],
        task_type=task_type,
        outcome=outcome,
        lesson=lesson,
        source_goal=source_goal,
        confidence=confidence,
        goal_achieved=goal_achieved,
        goal_verdict_source=goal_verdict_source,
        minted_from=minted_from,
        # Fresh mints only — the reinforce paths above return the existing
        # row, whose stamps point at the run that actually minted it.
        grounding=grounding or [],
    )
    if minted_from == "prompt":
        log.info("flat lesson %s quarantined at mint (prompt-derived): %s",
                 l.lesson_id, lesson[:80])
    from file_lock import locked_append
    locked_append(_lessons_path(), json.dumps(_verdict_row(l)))
    return l


def set_flat_lesson_minted_from(lesson_id: str, minted_from: str,
                                *, reason: str = "") -> bool:
    """Stamp minted_from on a stored flat-ledger lesson — the quarantine
    (and unquarantine) maintenance verb for the legacy store. The row stays
    on disk; it only leaves the injection surfaces (load_lessons default).
    Returns True if the lesson was found and stamped.
    """
    if minted_from not in ("", "outcome", "prompt"):
        raise ValueError(f"invalid minted_from: {minted_from!r}")
    path = _lessons_path()
    if not path.exists():
        return False
    hit = {"lesson": ""}

    def _stamp(old: str) -> str:
        lines = []
        for line in old.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                row = json.loads(stripped)
            except json.JSONDecodeError:
                lines.append(line)
                continue
            if isinstance(row, dict) and row.get("lesson_id") == lesson_id:
                if minted_from:
                    row["minted_from"] = minted_from
                else:
                    row.pop("minted_from", None)
                hit["lesson"] = str(row.get("lesson", ""))
                lines.append(json.dumps(row))
            else:
                lines.append(line)
        return "\n".join(lines) + ("\n" if lines else "")

    from file_lock import locked_rmw
    try:
        locked_rmw(path, _stamp)
    except OSError as exc:
        log.warning("set_flat_lesson_minted_from: rewrite failed for %s: %s",
                    lesson_id, exc)
        return False
    if not hit["lesson"]:
        return False
    try:
        from captains_log import log_event, LESSON_QUARANTINED
        log_event(
            event_type=LESSON_QUARANTINED,
            subject=lesson_id,
            summary=(f"Flat lesson {lesson_id} minted_from set to "
                     f"{minted_from or 'unset'}: {hit['lesson'][:100]}"),
            context={"store": "flat", "minted_from": minted_from,
                     "reason": reason},
        )
    except Exception:
        pass
    log.info("set_flat_lesson_minted_from: %s → %r%s", lesson_id,
             minted_from, f" — {reason}" if reason else "")
    return True


def contest_flat_lesson(lesson_id: str, stamp: Dict[str, Any]) -> bool:
    """Stamp a flat-ledger lesson contested (retirement-by-contradiction).

    Helper for knowledge_web.contest_lesson, which owns the LESSON_CONTESTED
    event — this function only rewrites the row (UU-4 dual-written lessons
    share a lesson_id across both stores, so one contest call flips both).
    An already-contested row keeps its ORIGINAL stamp (first contradiction
    wins the audit trail) and still counts as a hit.
    Returns True if the lesson was found.
    """
    path = _lessons_path()
    if not path.exists():
        return False
    hit = {"found": False}

    def _stamp(old: str) -> str:
        lines = []
        for line in old.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                row = json.loads(stripped)
            except json.JSONDecodeError:
                lines.append(line)
                continue
            if isinstance(row, dict) and row.get("lesson_id") == lesson_id:
                hit["found"] = True
                if not row.get("contested"):
                    # Per-store sighting snapshot (refight evidence counter —
                    # mirrors the tiered stamp).
                    row["contested"] = dict(
                        stamp,
                        times_reinforced_at_contest=int(
                            row.get("times_reinforced") or 0))
                lines.append(json.dumps(row))
            else:
                lines.append(line)
        return "\n".join(lines) + ("\n" if lines else "")

    from file_lock import locked_rmw
    try:
        locked_rmw(path, _stamp)
    except OSError as exc:
        log.warning("contest_flat_lesson: rewrite failed for %s: %s",
                    lesson_id, exc)
        return False
    return hit["found"]


def uncontest_flat_lesson(lesson_id: str,
                          expected_stamp: Optional[Dict] = None) -> bool:
    """Clear a flat-ledger lesson's contested stamp — helper for
    knowledge_web.refight_lesson on a "keep" verdict (the only path that
    restores citizenship; revise/retire leave the flat row contested
    because its text is the refuted original).

    expected_stamp binds the clear to the contest the verdict judged
    (matched on contested_at + source): if the flat row carries a NEWER
    contest — the stores are dual-written but not atomically, so they can
    diverge — a keep rendered against the old stamp must not erase it
    (2026-08-09 review). None = unconditional clear (operator escape).

    Returns True only if the lesson was found AND its stamp was cleared
    (or it had none); False on absent row or stamp mismatch — callers
    treat False as "flat surfaces may still exclude this lesson".
    """
    path = _lessons_path()
    if not path.exists():
        return False
    hit = {"cleared": False}

    def _stamp_matches(contested: Dict) -> bool:
        if expected_stamp is None or not contested:
            return True
        return (contested.get("contested_at") ==
                expected_stamp.get("contested_at")
                and contested.get("source") == expected_stamp.get("source"))

    def _clear(old: str) -> str:
        lines = []
        for line in old.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                row = json.loads(stripped)
            except json.JSONDecodeError:
                lines.append(line)
                continue
            if isinstance(row, dict) and row.get("lesson_id") == lesson_id:
                contested = row.get("contested")
                if _stamp_matches(contested if isinstance(contested, dict)
                                  else {}):
                    hit["cleared"] = True
                    row.pop("contested", None)
                lines.append(json.dumps(row))
            else:
                lines.append(line)
        return "\n".join(lines) + ("\n" if lines else "")

    from file_lock import locked_rmw
    try:
        locked_rmw(path, _clear)
    except OSError as exc:
        log.warning("uncontest_flat_lesson: rewrite failed for %s: %s",
                    lesson_id, exc)
        return False
    return hit["cleared"]


def _rewrite_lessons_file(task_type: str, updated_lessons: List[Lesson]) -> None:
    """Rewrite the lessons file, replacing entries for the given task_type with updated versions.

    Goes through locked_rmw so the read happens under the lock — reading
    first and locking only the write was a lost-update race: a lesson
    appended by a concurrent run between our read and write vanished.
    """
    path = _lessons_path()
    if not path.exists():
        return
    from file_lock import locked_rmw

    updated_ids = {l.lesson_id for l in updated_lessons}
    updated_by_id = {l.lesson_id: l for l in updated_lessons}

    def _merge(old: str) -> str:
        all_lines = []
        for line in old.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                lid = d.get("lesson_id", "")
                if lid in updated_ids:
                    all_lines.append(json.dumps(_verdict_row(updated_by_id[lid])))
                else:
                    all_lines.append(line)
            except Exception:
                all_lines.append(line)  # preserve unparseable lines
        return "\n".join(all_lines) + ("\n" if all_lines else "")

    locked_rmw(path, _merge)


def _lesson_from_row(d: dict) -> Lesson:
    """Raw ledger row → Lesson. A row with no recorded_at key must load as
    "" (absence preserved; parse_stored_ts("") is None → no age stamp) — the
    field's default_factory would fabricate a load-time date for legacy rows,
    and the rewrite paths (_rewrite_lessons_file via record_lesson
    reinforcement, deduplicate_lessons) persist whatever was loaded. The
    write path (record_lesson) still stamps genuinely new lessons at
    creation."""
    kwargs = {k: d[k] for k in Lesson.__dataclass_fields__ if k in d}
    if "recorded_at" not in d:
        kwargs["recorded_at"] = ""
    return Lesson(**kwargs)


def load_lessons(
    task_type: Optional[str] = None,
    outcome_filter: Optional[str] = None,
    limit: int = 10,
    *,
    query: Optional[str] = None,
    include_quarantined: bool = False,
    include_contested: bool = False,
) -> List[Lesson]:
    """Load relevant lessons from the lessons ledger.

    Args:
        task_type: Filter by task type (None = all types).
        outcome_filter: Filter by outcome ("done" | "stuck" | None = all).
        limit: Maximum number of lessons to return.
        query: If provided, rank lessons by TF-IDF relevance to this query
            before returning (fetches 3x limit internally, then ranks down).
            Without query, returns most recent first.
        include_quarantined: Default False — prompt-derived (quarantined)
            lessons stay out of every consumer that injects into prompts
            (recall top-up, bootstrap_context, inject_lessons_for_task).
            Readout/maintenance surfaces opt in.
        include_contested: Default False — contested lessons
            (retirement-by-contradiction) stay out of the same injection
            consumers. Readout/maintenance/dedup surfaces opt in.

    Returns:
        List of Lesson objects.
    """
    path = _lessons_path()
    if not path.exists():
        return []

    lessons = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                l = _lesson_from_row(d)
                if task_type and l.task_type != task_type:
                    continue
                if outcome_filter and l.outcome != outcome_filter:
                    continue
                if not include_quarantined and l.minted_from == "prompt":
                    continue
                if not include_contested and l.contested:
                    continue
                lessons.append(l)
            except Exception:
                continue
    except Exception:
        pass

    # Deduplicate by lesson text
    seen: set = set()
    deduped: List[Lesson] = []
    _pool_limit = limit * 3 if query else limit
    for l in reversed(lessons):
        key = l.lesson.strip()[:100]
        if key not in seen:
            seen.add(key)
            deduped.append(l)
        if len(deduped) >= _pool_limit:
            break

    # TF-IDF re-rank if query provided (always re-rank when query present)
    if query and deduped:
        # Adapt Lesson objects to look like TieredLesson for _tfidf_rank
        class _LessonProxy:
            def __init__(self, l: "Lesson"):
                self._l = l
                self.lesson = l.lesson
            def __getattr__(self, name: str):
                return getattr(self._l, name)

        proxies = [_LessonProxy(l) for l in deduped]
        if _USE_HYBRID:
            ranked = _hybrid_rank(query, proxies, top_k=limit)
        else:
            # Lazy import to avoid circular dependency with memory.py
            from memory import _tfidf_rank
            ranked = _tfidf_rank(query, proxies, top_k=limit)  # type: ignore[arg-type]
        return [p._l for p in ranked]  # type: ignore[attr-defined]

    return deduped[:limit]


def load_outcomes(limit: int = 20) -> List[Outcome]:
    """Load recent outcomes from the ledger."""
    path = _outcomes_path()
    if not path.exists():
        return []

    outcomes = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                o = Outcome(**{k: d[k] for k in Outcome.__dataclass_fields__ if k in d})
                outcomes.append(o)
            except Exception:
                continue
    except Exception:
        pass

    return list(reversed(outcomes))[:limit]


# ---------------------------------------------------------------------------
# Lesson deduplication (cleanup utility)
# ---------------------------------------------------------------------------

def deduplicate_lessons(*, dry_run: bool = False) -> dict:
    """Deduplicate lessons.jsonl by exact text match and near-duplicate word overlap.

    Keeps the first occurrence (oldest) of each lesson text.
    Near-duplicates (word overlap ≥ 0.8) are merged: the older entry survives,
    its times_reinforced count is bumped for each dropped near-dup.

    Returns:
        Dict with keys: before, after, removed_exact, removed_near, removed_dry_run.
    """
    path = _lessons_path()
    if not path.exists():
        return {"before": 0, "after": 0, "removed_exact": 0, "removed_near": 0}

    stats = {"before": 0, "after": 0, "removed_exact": 0, "removed_near": 0}

    def _dedup(old: str) -> str:
        all_lessons: List[Lesson] = []
        for line in old.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                l = _lesson_from_row(d)
                all_lessons.append(l)
            except Exception:
                pass

        stats["before"] = len(all_lessons)
        kept: List[Lesson] = []

        for l in all_lessons:
            # Exact match check
            exact_match = next((k for k in kept if k.lesson == l.lesson), None)
            if exact_match is not None:
                exact_match.times_reinforced += 1
                stats["removed_exact"] += 1
                continue

            # Near-duplicate check
            near_match = next(
                (k for k in kept if _text_similarity(k.lesson, l.lesson) > 0.8),
                None,
            )
            if near_match is not None:
                near_match.times_reinforced += 1
                near_match.confidence = min(1.0, near_match.confidence + 0.05)
                stats["removed_near"] += 1
                continue

            kept.append(l)

        stats["after"] = len(kept)
        if not dry_run and stats["after"] < stats["before"]:
            return "\n".join(json.dumps(_verdict_row(l)) for l in kept) + "\n"
        return old  # dry-run or nothing removed — leave the file as-is

    # Parse + dedup + rewrite all under the file's lock (locked_rmw) so a
    # lesson appended mid-dedup isn't dropped. Pure compute inside the
    # critical section — no LLM/subprocess work.
    try:
        from file_lock import locked_rmw
        locked_rmw(path, _dedup)
    except Exception as exc:
        log.warning("deduplicate_lessons: write failed: %s", exc)
        if stats["before"] == 0:
            return {"before": 0, "after": 0, "removed_exact": 0, "removed_near": 0}

    before = stats["before"]
    after = stats["after"]
    removed_exact = stats["removed_exact"]
    removed_near = stats["removed_near"]

    log.info(
        "deduplicate_lessons: before=%d after=%d removed_exact=%d removed_near=%d dry_run=%s",
        before, after, removed_exact, removed_near, dry_run,
    )
    return {
        "before": before,
        "after": after,
        "removed_exact": removed_exact,
        "removed_near": removed_near,
        "removed_dry_run": dry_run and (removed_exact + removed_near),
    }


# ---------------------------------------------------------------------------
# Three-layer memory compression (724-office steal)
# ---------------------------------------------------------------------------

def _save_compressed_batch(batch: CompressedBatch) -> None:
    path = _compressed_outcomes_path()
    from file_lock import locked_append
    locked_append(path, json.dumps({
        "batch_id": batch.batch_id,
        "summary": batch.summary,
        "task_types": batch.task_types,
        "outcome_ids": batch.outcome_ids,
        "batch_size": batch.batch_size,
        "oldest_at": batch.oldest_at,
        "newest_at": batch.newest_at,
        "compressed_at": batch.compressed_at,
    }))


def load_compressed_batches(limit: int = 20) -> List[CompressedBatch]:
    """Load recently compressed outcome batches (most recent first)."""
    path = _compressed_outcomes_path()
    if not path.exists():
        return []
    batches = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                batches.append(CompressedBatch(**{
                    k: d[k] for k in CompressedBatch.__dataclass_fields__ if k in d
                }))
            except Exception:
                continue
    except Exception:
        pass
    return list(reversed(batches))[:limit]


_COMPRESS_SYSTEM = (
    "You are a memory archivist. Given a batch of AI agent mission outcomes, "
    "write a single compact paragraph (\u2264120 words) that captures the key patterns, "
    "recurring failures, and lessons learned. Focus on actionable insights that would "
    "help an agent avoid repeating mistakes or build on successes. Be specific about "
    "task types and failure modes. Do not list individual missions \u2014 synthesise."
)


def compress_old_outcomes(
    *,
    threshold: int = 100,
    batch_size: int = 50,
    keep_recent: int = 50,
    dry_run: bool = False,
    adapter: Any = None,
) -> Optional[CompressedBatch]:
    """LLM-compress oldest outcomes when total count exceeds threshold.

    Reads outcomes.jsonl. If total > threshold, takes the oldest `batch_size`
    outcomes (up to total - keep_recent), compresses them with an LLM call,
    saves the CompressedBatch to compressed_outcomes.jsonl, and removes the
    compressed entries from outcomes.jsonl.

    Args:
        threshold:    Only compress if total outcomes exceed this.
        batch_size:   How many old outcomes to compress per call.
        keep_recent:  Always keep at least this many raw outcomes untouched.
        dry_run:      Return a dummy batch without reading/writing files.
        adapter:      LLM adapter for the compression call. If None, uses
                      a no-LLM placeholder (useful for dry_run or testing).

    Returns:
        CompressedBatch if compression happened, None otherwise.
    """
    import uuid as _uuid

    if dry_run:
        return CompressedBatch(
            batch_id=_uuid.uuid4().hex[:8],
            summary="[dry-run] compressed batch placeholder",
            task_types=["general"],
            outcome_ids=["dry-run-1"],
            batch_size=1,
            oldest_at="2026-01-01T00:00:00+00:00",
            newest_at="2026-01-02T00:00:00+00:00",
        )

    path = _outcomes_path()
    if not path.exists():
        return None

    try:
        raw_lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    except Exception:
        return None

    total = len(raw_lines)
    if total <= threshold:
        log.debug("compress_old_outcomes: %d outcomes (threshold %d), skipping", total, threshold)
        return None

    # Take oldest batch_size, but never dip below keep_recent recent entries
    compress_count = min(batch_size, max(0, total - keep_recent))
    if compress_count <= 0:
        return None

    to_compress_lines = raw_lines[:compress_count]

    # Parse outcomes for metadata
    parsed: List[Dict[str, Any]] = []
    for line in to_compress_lines:
        try:
            parsed.append(json.loads(line))
        except Exception:
            pass

    if not parsed:
        return None

    task_types = list({d.get("task_type", "general") for d in parsed})
    outcome_ids = [d.get("outcome_id", "") for d in parsed if d.get("outcome_id")]
    oldest_at = parsed[0].get("recorded_at", "")
    newest_at = parsed[-1].get("recorded_at", "")

    # Build LLM compression prompt
    lines_for_llm = []
    for d in parsed:
        goal = d.get("goal", "")[:80]
        status = d.get("status", "")
        summary = d.get("summary", "")[:120]
        lines_for_llm.append(f"- [{status}] {goal}: {summary}")
    batch_text = "\n".join(lines_for_llm[:batch_size])

    # LLM compress or fallback to heuristic
    if adapter is not None:
        try:
            from llm import LLMMessage
            resp = adapter.complete(
                [
                    LLMMessage("system", _COMPRESS_SYSTEM),
                    LLMMessage("user", f"Compress these {len(parsed)} mission outcomes:\n\n{batch_text}"),
                ],
                max_tokens=200,
                temperature=0.2,
                no_tools=True,
                purpose="outcome compression",
            )
            compressed_text = content_or_empty(resp).strip()[:600]
        except Exception as exc:
            log.debug("compress_old_outcomes: LLM failed (%s), using heuristic", exc)
            compressed_text = f"[heuristic] {len(parsed)} missions ({', '.join(task_types)}). Oldest: {oldest_at[:10]}. Newest: {newest_at[:10]}."
    else:
        # No adapter — build a keyword-based summary without LLM
        done_count = sum(1 for d in parsed if d.get("status") == "done")
        stuck_count = len(parsed) - done_count
        goals_sample = "; ".join(d.get("goal", "")[:40] for d in parsed[:3])
        compressed_text = (
            f"{len(parsed)} missions ({done_count} done, {stuck_count} stuck) "
            f"in task types: {', '.join(task_types)}. "
            f"Sample goals: {goals_sample}."
        )

    batch = CompressedBatch(
        batch_id=_uuid.uuid4().hex[:8],
        summary=compressed_text,
        task_types=task_types,
        outcome_ids=outcome_ids,
        batch_size=len(parsed),
        oldest_at=oldest_at,
        newest_at=newest_at,
    )

    # Persist: save compressed batch, rewrite outcomes.jsonl without old
    # entries. The LLM call above ran on an unlocked snapshot — merge under
    # the lock by dropping exactly the lines we compressed, so appends that
    # landed mid-compression survive (keyed-merge pattern).
    _save_compressed_batch(batch)
    from file_lock import locked_rmw
    _compressed_set = set(to_compress_lines)

    def _drop_compressed(old: str) -> str:
        kept = [l for l in old.splitlines() if l.strip() and l not in _compressed_set]
        return "\n".join(kept) + ("\n" if kept else "")

    try:
        locked_rmw(path, _drop_compressed)
    except OSError as exc:
        log.warning("compress_old_outcomes: failed to rewrite outcomes.jsonl: %s", exc)

    log.info("compress_old_outcomes: compressed %d outcomes -> batch %s", len(parsed), batch.batch_id)
    return batch


# ---------------------------------------------------------------------------
# TF-IDF ranking for compressed batches
# ---------------------------------------------------------------------------

def _tfidf_rank_batches(
    query: str,
    batches: List[CompressedBatch],
    *,
    top_k: Optional[int] = None,
) -> List[CompressedBatch]:
    """Rank compressed batches by TF-IDF cosine similarity to query.

    Re-uses the same no-dependency TF-IDF pattern from _tfidf_rank.
    """
    if not batches or not query:
        return batches

    stop_words = {
        "the", "and", "for", "was", "this", "that", "with", "from", "are",
        "were", "have", "has", "had", "its", "but", "not", "you", "all",
        "can", "will", "more", "than", "been", "into",
    }

    def _tok(text: str) -> List[str]:
        return [
            t for t in re.sub(r"[^a-z0-9]+", " ", text.lower()).split()
            if t not in stop_words and len(t) > 2
        ]

    query_terms = _tok(query)
    if not query_terms:
        return batches

    docs = [query_terms] + [_tok(b.summary) for b in batches]
    n = len(docs)
    df: Counter = Counter()
    for doc in docs:
        for term in set(doc):
            df[term] += 1

    def _idf(t: str) -> float:
        return math.log(n / (df.get(t, 0) + 1)) + 1.0

    def _vec(terms: List[str]) -> Dict[str, float]:
        tf = Counter(terms)
        total = max(len(terms), 1)
        return {t: (c / total) * _idf(t) for t, c in tf.items()}

    def _cos(v1: Dict[str, float], v2: Dict[str, float]) -> float:
        dot = sum(v1.get(t, 0.0) * v2.get(t, 0.0) for t in v1)
        n1 = math.sqrt(sum(x * x for x in v1.values())) or 1.0
        n2 = math.sqrt(sum(x * x for x in v2.values())) or 1.0
        return dot / (n1 * n2)

    qvec = _vec(query_terms)
    scored = sorted(
        [(b, _cos(qvec, _vec(_tok(b.summary)))) for b in batches],
        key=lambda x: x[1],
        reverse=True,
    )
    ranked = [b for b, _ in scored]
    return ranked[:top_k] if top_k is not None else ranked


# ---------------------------------------------------------------------------
# Three-layer outcome retrieval
# ---------------------------------------------------------------------------

def load_outcomes_with_context(
    goal: str = "",
    *,
    limit: int = 20,
    compressed_limit: int = 5,
) -> Dict[str, Any]:
    """Three-layer outcome retrieval.

    Layer 1 (raw recent): last `limit` outcomes from outcomes.jsonl.
    Layer 2 (compressed): top `compressed_limit` compressed batches ranked by
                          TF-IDF similarity to `goal` (or most recent if no goal).
    Layer 3 (injection): returns a merged context string for prompt injection.

    Returns:
        {
            "recent": List[Outcome],
            "compressed": List[CompressedBatch],
            "context_text": str,  # ready to inject into a prompt
        }
    """
    recent = load_outcomes(limit=limit)
    raw_batches = load_compressed_batches(limit=20)

    if goal and raw_batches:
        compressed = _tfidf_rank_batches(goal, raw_batches, top_k=compressed_limit)
    else:
        compressed = raw_batches[:compressed_limit]

    # Build context text
    parts: List[str] = []

    if compressed:
        parts.append("## Compressed Memory (older missions)")
        for b in compressed:
            parts.append(f"- [{b.oldest_at[:10]}\u2192{b.newest_at[:10]}, {b.batch_size} missions] {b.summary}")

    if recent:
        parts.append("## Recent Outcomes")
        for o in recent:
            # Verdict-preferred (SF-2): judged goal-not-achieved renders as a
            # failure even though the loop finished.
            from outcome_policy import is_verdict_pending
            icon = ("?" if is_verdict_pending(o) else
                    ("\u2713" if (o.status == "done" and o.goal_achieved is not False) else "\u2717"))
            verdict_note = " [goal NOT achieved]" if o.goal_achieved is False else ""
            parts.append(f"- {icon} {o.goal[:60]} ({o.task_type}, {o.recorded_at[:10]}){verdict_note}: {o.summary[:80]}")

    context_text = "\n".join(parts) if parts else ""

    return {
        "recent": recent,
        "compressed": compressed,
        "context_text": context_text,
    }


# ---------------------------------------------------------------------------
# Memory index
# ---------------------------------------------------------------------------

def _update_memory_index():
    """Rewrite MEMORY.md with a current index of memory files."""
    try:
        mem_dir = _memory_dir()
        daily_files = sorted(mem_dir.glob("????-??-??.md"), reverse=True)[:7]

        outcomes = load_outcomes(limit=10)
        done_count = sum(1 for o in outcomes if o.status == "done")
        stuck_count = sum(1 for o in outcomes if o.status == "stuck")
        # Verdict tri-state (SF-2): done ≠ achieved — surface judged verdicts.
        achieved_count = sum(1 for o in outcomes if o.goal_achieved is True)
        not_achieved_count = sum(1 for o in outcomes if o.goal_achieved is False)
        verdict_line = (
            f"- Goal verdicts: {achieved_count} achieved | {not_achieved_count} NOT achieved "
            f"| {len(outcomes) - achieved_count - not_achieved_count} unjudged"
        )
        total_tokens = sum(o.tokens_in + o.tokens_out for o in outcomes)

        lines = [
            "# Memory Index",
            "",
            f"*Auto-updated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}*",
            "",
            "## Stats (last 10 runs)",
            f"- Done: {done_count} | Stuck: {stuck_count}",
            verdict_line,
            f"- Total tokens: {total_tokens:,}",
            "",
            "## Daily Logs",
        ]
        for f in daily_files:
            lines.append(f"- [{f.stem}]({f.name})")

        lines += ["", "## Lessons Count"]
        lesson_path = _lessons_path()
        if lesson_path.exists():
            n = sum(1 for l in lesson_path.read_text().splitlines() if l.strip())
            lines.append(f"- {n} lessons stored in lessons.jsonl")
        else:
            lines.append("- 0 lessons stored")

        from file_lock import atomic_write
        atomic_write(_memory_index_path(), "\n".join(lines) + "\n")
    except Exception:
        pass
