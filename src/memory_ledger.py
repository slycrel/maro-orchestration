#!/usr/bin/env python3
"""Temporal/recording layer of the Poe memory system.

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
from typing import Any, Dict, List, Optional

from llm_parse import extract_json, safe_list, content_or_empty

log = logging.getLogger("poe.memory.ledger")

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


@dataclass
class Lesson:
    lesson_id: str
    task_type: str          # what kind of task this lesson applies to
    outcome: str            # "done" | "stuck" — what happened
    lesson: str             # the insight
    source_goal: str        # which goal produced this lesson
    confidence: float       # 0.0-1.0 (starts at 0.7, adjusts with reinforcement)
    times_applied: int = 0
    times_reinforced: int = 0
    recorded_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


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
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
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
        steps_data.append(entry)

    trace = {
        "outcome_id": outcome_id,
        "goal": goal[:200],
        "task_type": task_type,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "steps": steps_data,
    }
    try:
        with open(_step_traces_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(trace) + "\n")
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

def record_outcome(
    goal: str,
    status: str,
    summary: str,
    *,
    task_type: str = "general",
    project: Optional[str] = None,
    lessons: Optional[List[str]] = None,
    tokens_in: int = 0,
    tokens_out: int = 0,
    elapsed_ms: int = 0,
    model: str = "",
    failure_chain: Optional[List[str]] = None,
    recovery_steps: int = 0,
) -> Outcome:
    """Record the outcome of a completed run.

    Appends to outcomes.jsonl and daily log. Also extracts lessons if provided.

    Args:
        failure_chain: Agent0 steal — list of failure/diagnosis/recovery strings describing
                       the error-recovery trajectory (e.g. ["step 3 failed: timeout",
                       "diagnosis: rate limit", "recovery: waited 60s and retried"]).
                       Turns retries into training signal for future runs.
        recovery_steps: How many retries or recovery actions were needed.
    """
    import uuid
    from metrics import estimate_cost
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
    )

    # Append to outcomes ledger
    with open(_outcomes_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(outcome)) + "\n")

    # Append to daily log
    _append_daily_log(outcome)

    # Store lessons
    for lesson_text in (lessons or []):
        if lesson_text.strip():
            _store_lesson(
                task_type=task_type,
                outcome=status,
                lesson=lesson_text,
                source_goal=goal,
            )

    # Update MEMORY.md index
    _update_memory_index()

    return outcome


def _append_daily_log(outcome: Outcome):
    """Append a human-readable entry to today's daily log."""
    path = _daily_path()
    status_icon = "\u2713" if outcome.status == "done" else "\u2717"
    tokens = f"{outcome.tokens_in}in+{outcome.tokens_out}out"
    cost_str = f" (${outcome.cost_usd:.6f})" if outcome.cost_usd else ""
    entry = (
        f"\n## [{outcome.recorded_at[:10]}] {status_icon} {outcome.goal[:80]}\n"
        f"- **Status**: {outcome.status}\n"
        f"- **Type**: {outcome.task_type}\n"
        f"- **Summary**: {outcome.summary}\n"
        f"- **Tokens**: {tokens} in {outcome.elapsed_ms}ms{cost_str}\n"
    )
    if outcome.lessons:
        entry += "- **Lessons**:\n" + "".join(f"  - {l}\n" for l in outcome.lessons)
    if outcome.project:
        entry += f"- **Project**: {outcome.project}\n"

    with open(path, "a", encoding="utf-8") as f:
        f.write(entry)


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
) -> Lesson:
    """Append a lesson to the lessons ledger, or reinforce existing near-duplicate."""
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
    # Check for near-duplicate (same lesson text for same task type)
    existing = load_lessons(task_type=task_type, limit=50)
    for ex in existing:
        if _text_similarity(ex.lesson, lesson) > 0.8:
            # Reinforce existing lesson and persist the update
            ex.times_reinforced += 1
            ex.confidence = min(1.0, ex.confidence + 0.05)
            _rewrite_lessons_file(task_type, existing)
            return ex

    l = Lesson(
        lesson_id=str(uuid.uuid4())[:8],
        task_type=task_type,
        outcome=outcome,
        lesson=lesson,
        source_goal=source_goal,
        confidence=confidence,
    )
    with open(_lessons_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(l)) + "\n")
    return l


def _rewrite_lessons_file(task_type: str, updated_lessons: List[Lesson]) -> None:
    """Rewrite the lessons file, replacing entries for the given task_type with updated versions."""
    path = _lessons_path()
    if not path.exists():
        return
    try:
        from file_lock import locked_write
    except ImportError:
        locked_write = None

    # Read all lines, replace matching task_type entries, keep others
    all_lines = []
    updated_ids = {l.lesson_id for l in updated_lessons}
    updated_by_id = {l.lesson_id: l for l in updated_lessons}

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
            lid = d.get("lesson_id", "")
            if lid in updated_ids:
                all_lines.append(json.dumps(asdict(updated_by_id[lid])))
            else:
                all_lines.append(line)
        except Exception:
            all_lines.append(line)  # preserve unparseable lines

    content = "\n".join(all_lines) + ("\n" if all_lines else "")
    if locked_write:
        with locked_write(path):
            path.write_text(content, encoding="utf-8")
    else:
        path.write_text(content, encoding="utf-8")


def load_lessons(
    task_type: Optional[str] = None,
    outcome_filter: Optional[str] = None,
    limit: int = 10,
    *,
    query: Optional[str] = None,
) -> List[Lesson]:
    """Load relevant lessons from the lessons ledger.

    Args:
        task_type: Filter by task type (None = all types).
        outcome_filter: Filter by outcome ("done" | "stuck" | None = all).
        limit: Maximum number of lessons to return.
        query: If provided, rank lessons by TF-IDF relevance to this query
            before returning (fetches 3x limit internally, then ranks down).
            Without query, returns most recent first.

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
                l = Lesson(**{k: d[k] for k in Lesson.__dataclass_fields__ if k in d})
                if task_type and l.task_type != task_type:
                    continue
                if outcome_filter and l.outcome != outcome_filter:
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
# Three-layer memory compression (724-office steal)
# ---------------------------------------------------------------------------

def _save_compressed_batch(batch: CompressedBatch) -> None:
    path = _compressed_outcomes_path()
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "batch_id": batch.batch_id,
            "summary": batch.summary,
            "task_types": batch.task_types,
            "outcome_ids": batch.outcome_ids,
            "batch_size": batch.batch_size,
            "oldest_at": batch.oldest_at,
            "newest_at": batch.newest_at,
            "compressed_at": batch.compressed_at,
        }) + "\n")


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
    surviving_lines = raw_lines[compress_count:]

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

    # Persist: save compressed batch, rewrite outcomes.jsonl without old entries
    _save_compressed_batch(batch)
    try:
        path.write_text("\n".join(surviving_lines) + ("\n" if surviving_lines else ""), encoding="utf-8")
    except Exception as exc:
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
            icon = "\u2713" if o.status == "done" else "\u2717"
            parts.append(f"- {icon} {o.goal[:60]} ({o.task_type}, {o.recorded_at[:10]}): {o.summary[:80]}")

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
        total_tokens = sum(o.tokens_in + o.tokens_out for o in outcomes)

        lines = [
            "# Memory Index",
            "",
            f"*Auto-updated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}*",
            "",
            "## Stats (last 10 runs)",
            f"- Done: {done_count} | Stuck: {stuck_count}",
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

        _memory_index_path().write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:
        pass
