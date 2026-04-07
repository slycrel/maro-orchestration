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


# ---------------------------------------------------------------------------
# Phase 59 (Feynman steal): Task ledger — per-step audit trail
# ---------------------------------------------------------------------------

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
# Step trace recording (Meta-Harness steal: proposer reads full execution traces)
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
            "step": getattr(s, "step", ""),
            "status": getattr(s, "status", ""),
            "result": (getattr(s, "result", "") or "")[:500],
            "summary": getattr(s, "summary", "") or "",
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
        Dict mapping outcome_id → trace dict. Missing IDs are absent.
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
    status_icon = "✓" if outcome.status == "done" else "✗"
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

def _store_lesson(
    task_type: str,
    outcome: str,
    lesson: str,
    source_goal: str,
    confidence: float = 0.7,
) -> Lesson:
    """Append a lesson to the lessons ledger."""
    import uuid
    # Check for near-duplicate (same lesson text for same task type)
    existing = load_lessons(task_type=task_type, limit=50)
    for ex in existing:
        if _text_similarity(ex.lesson, lesson) > 0.8:
            # Reinforce existing lesson
            ex.times_reinforced += 1
            ex.confidence = min(1.0, ex.confidence + 0.05)
            # We'd need to rewrite the file to update — keep simple for now
            # (just append the new one; dedup on load)
            break

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
            before returning (fetches 3× limit internally, then ranks down).
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
# Session raw → LLM compress → TF-IDF retrieval
# ---------------------------------------------------------------------------

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


def _compressed_outcomes_path() -> Path:
    return _memory_dir() / "compressed_outcomes.jsonl"


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
    "write a single compact paragraph (≤120 words) that captures the key patterns, "
    "recurring failures, and lessons learned. Focus on actionable insights that would "
    "help an agent avoid repeating mistakes or build on successes. Be specific about "
    "task types and failure modes. Do not list individual missions — synthesise."
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

    log.info("compress_old_outcomes: compressed %d outcomes → batch %s", len(parsed), batch.batch_id)
    return batch


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
            parts.append(f"- [{b.oldest_at[:10]}→{b.newest_at[:10]}, {b.batch_size} missions] {b.summary}")

    if recent:
        parts.append("## Recent Outcomes")
        for o in recent:
            icon = "✓" if o.status == "done" else "✗"
            parts.append(f"- {icon} {o.goal[:60]} ({o.task_type}, {o.recorded_at[:10]}): {o.summary[:80]}")

    context_text = "\n".join(parts) if parts else ""

    return {
        "recent": recent,
        "compressed": compressed,
        "context_text": context_text,
    }


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

    def _one_sample() -> "List[tuple]":
        try:
            resp = adapter.complete(
                [
                    LLMMessage("system", system_prompt),
                    LLMMessage("user", user_msg),
                ],
                max_tokens=320,
                temperature=0.3,
            )
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
    lessons = extract_lessons_via_llm(
        goal=goal,
        status=status,
        result_summary=result_summary,
        task_type=task_type,
        adapter=adapter,
        dry_run=dry_run,
    )
    log.debug("extracted %d lessons from reflection", len(lessons))

    return record_outcome(
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


# ===========================================================================
# Phase 16: Tiered Memory — Short, Medium, Long Term
# ===========================================================================
#
# Three tiers:
#   SHORT  — in-process only, never persisted. Evicted at session end.
#   MEDIUM — memory/medium/lessons.jsonl. Decays daily; promoted on validation.
#   LONG   — memory/long/lessons.jsonl. Explicit promotion required.
#
# Grok decay model:
#   score *= 0.85  per non-reinforced day
#   score  = min(1.0, score + 0.3)  on reinforcement
#   Promote when score >= 0.9 AND sessions_validated >= 3
#   GC (garbage-collect) when score < 0.2
# ===========================================================================

DECAY_FACTOR = 0.85          # daily non-reinforced decay multiplier
REINFORCE_BONUS = 0.3        # added to score on reinforcement
PROMOTE_MIN_SCORE = 0.9      # minimum score to promote medium → long
PROMOTE_MIN_SESSIONS = 3     # minimum validated sessions to promote
GC_THRESHOLD = 0.2           # gc entries with score below this


class MemoryTier:
    SHORT = "short"
    MEDIUM = "medium"
    LONG = "long"


@dataclass
class TieredLesson:
    """A lesson with decay score and tier placement (Phase 16).

    Phase 59 (Feynman steal): evidence_sources field enables claim tracing —
    every lesson can carry the URLs/papers/outcomes that back its claim.
    """
    lesson_id: str
    task_type: str
    outcome: str
    lesson: str
    source_goal: str
    confidence: float
    tier: str                       # MemoryTier.MEDIUM | MemoryTier.LONG
    score: float                    # Grok decay score; starts at 1.0
    last_reinforced: str            # ISO date (YYYY-MM-DD)
    sessions_validated: int = 0     # how many sessions have confirmed this lesson
    times_applied: int = 0
    times_reinforced: int = 0
    recorded_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    acquired_for: Optional[str] = None  # goal_id that triggered this lesson (incidental flag)
    # Phase 59: evidence sources for claim tracing (URLs, outcome_ids, paper refs)
    evidence_sources: List[str] = field(default_factory=list)
    # Phase 59 NeMo S1: typed lesson taxonomy — "execution" | "planning" | "recovery" | "verification" | "cost"
    lesson_type: str = ""


# ---------------------------------------------------------------------------
# Short-term memory (in-process only, session-scoped)
# ---------------------------------------------------------------------------

_SHORT_TERM: Dict[str, Any] = {}


def short_set(key: str, value: Any) -> None:
    """Store a value in the short-term (session-scoped) memory store."""
    _SHORT_TERM[key] = value


def short_get(key: str, default: Any = None) -> Any:
    """Retrieve a value from short-term memory. Returns default if absent."""
    return _SHORT_TERM.get(key, default)


def short_clear() -> None:
    """Evict all short-term memory. Call at session end."""
    _SHORT_TERM.clear()


def short_all() -> Dict[str, Any]:
    """Return a snapshot of all short-term memory (read-only view)."""
    return dict(_SHORT_TERM)


# ---------------------------------------------------------------------------
# Storage paths (tiered)
# ---------------------------------------------------------------------------

def _tiered_lessons_path(tier: str) -> Path:
    d = _memory_dir() / tier
    d.mkdir(parents=True, exist_ok=True)
    return d / "lessons.jsonl"


# ---------------------------------------------------------------------------
# Decay helpers
# ---------------------------------------------------------------------------

def _days_since(date_str: str) -> int:
    """Return whole days elapsed since date_str (YYYY-MM-DD)."""
    try:
        recorded = datetime.strptime(date_str[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return max(0, (now - recorded).days)
    except Exception:
        return 0


def decay_score(score: float, days: int) -> float:
    """Apply exponential decay: score *= DECAY_FACTOR^days."""
    return score * (DECAY_FACTOR ** days)


def reinforce_score(score: float) -> float:
    """Apply reinforcement bonus: score = min(1.0, score + REINFORCE_BONUS)."""
    return min(1.0, score + REINFORCE_BONUS)


def _current_date() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# CRUD for tiered lessons
# ---------------------------------------------------------------------------

def record_tiered_lesson(
    lesson_text: str,
    task_type: str,
    outcome: str,
    source_goal: str,
    *,
    tier: str = MemoryTier.MEDIUM,
    confidence: float = 0.7,
    acquired_for: Optional[str] = None,
    evidence_sources: Optional[List[str]] = None,
    lesson_type: str = "",
) -> TieredLesson:
    """Record a new lesson at the given tier.

    Checks for near-duplicates before writing; reinforces existing if match found.
    Pass ``acquired_for=goal_id`` to tag incidental knowledge (e.g. lessons acquired
    as a prerequisite sub-goal rather than as the primary task outcome).

    Phase 59 NeMo S1: ``lesson_type`` classifies the lesson — "execution" | "planning" |
        "recovery" | "verification" | "cost". Enables type-filtered retrieval.
    Phase 59: ``evidence_sources`` accepts a list of URLs/outcome_ids/paper refs
        that back the lesson's claim, enabling post-hoc claim tracing.
    """
    import uuid

    existing = load_tiered_lessons(tier=tier, task_type=task_type)
    for ex in existing:
        if _text_similarity(ex.lesson, lesson_text) > 0.8:
            return _reinforce_tiered_lesson(ex, tier=tier)

    tl = TieredLesson(
        lesson_id=str(uuid.uuid4())[:8],
        task_type=task_type,
        outcome=outcome,
        lesson=lesson_text,
        source_goal=source_goal,
        confidence=confidence,
        tier=tier,
        score=1.0,
        last_reinforced=_current_date(),
        acquired_for=acquired_for,
        evidence_sources=evidence_sources or [],
        lesson_type=lesson_type if lesson_type in _LESSON_TYPES else "",
    )
    _append_tiered_lesson(tl, tier=tier)
    return tl


def _append_tiered_lesson(tl: TieredLesson, *, tier: str) -> None:
    path = _tiered_lessons_path(tier)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(tl)) + "\n")


def _reinforce_tiered_lesson(tl: TieredLesson, *, tier: str) -> TieredLesson:
    """Reinforce an existing lesson: bump score and sessions_validated, rewrite file."""
    tl.score = reinforce_score(tl.score)
    tl.sessions_validated += 1
    tl.times_reinforced += 1
    tl.last_reinforced = _current_date()
    _rewrite_tiered_lessons(tier)
    return tl


def load_tiered_lessons(
    tier: str,
    *,
    task_type: Optional[str] = None,
    lesson_type: Optional[str] = None,
    min_score: float = 0.0,
    limit: int = 50,
    max_age_days: Optional[int] = None,
) -> List[TieredLesson]:
    """Load tiered lessons from disk, applying current-day decay inline.

    Args:
        lesson_type:  If set, only return lessons with this lesson_type
                      (Phase 59 NeMo S1 typed taxonomy filter).
        max_age_days: If set, skip lessons last reinforced more than this many days ago.
                      Useful for pruning stale lessons in retrieval contexts.
    """
    path = _tiered_lessons_path(tier)
    if not path.exists():
        return []

    results: List[TieredLesson] = []
    today = _current_date()
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                tl = TieredLesson(**{k: d[k] for k in TieredLesson.__dataclass_fields__ if k in d})
                # Apply decay inline (days since last reinforcement)
                days = _days_since(tl.last_reinforced)
                if max_age_days is not None and days > max_age_days:
                    continue  # lesson too stale
                if days > 0:
                    tl.score = decay_score(tl.score, days)
                if tl.score < min_score:
                    continue
                if task_type and tl.task_type != task_type:
                    continue
                if lesson_type and tl.lesson_type != lesson_type:
                    continue
                results.append(tl)
            except Exception:
                continue
    except Exception:
        pass

    results.sort(key=lambda x: x.score, reverse=True)
    return results[:limit]


def _rewrite_tiered_lessons(tier: str, lessons: Optional[List[TieredLesson]] = None) -> None:
    """Rewrite the tiered lessons file with the current state (after updates/GC)."""
    if lessons is None:
        lessons = load_tiered_lessons(tier=tier, min_score=0.0)
    path = _tiered_lessons_path(tier)
    with open(path, "w", encoding="utf-8") as f:
        for tl in lessons:
            f.write(json.dumps(asdict(tl)) + "\n")


# ---------------------------------------------------------------------------
# Reinforce, forget, promote
# ---------------------------------------------------------------------------

def reinforce_lesson(lesson_id: str, tier: str = MemoryTier.MEDIUM) -> Optional[TieredLesson]:
    """Find lesson by ID in the given tier and reinforce it (score + sessions)."""
    lessons = load_tiered_lessons(tier=tier, min_score=0.0)
    target = next((l for l in lessons if l.lesson_id == lesson_id), None)
    if not target:
        return None
    target.score = reinforce_score(target.score)
    target.sessions_validated += 1
    target.times_reinforced += 1
    target.last_reinforced = _current_date()
    _rewrite_tiered_lessons(tier=tier, lessons=lessons)
    return target


def search_graveyard(
    topic: str,
    *,
    min_score: float = GC_THRESHOLD,
    max_score: float = 0.4,
    limit: int = 10,
    resurrect: bool = False,
) -> List[TieredLesson]:
    """Find decayed lessons matching *topic* before triggering a sub-goal re-acquisition.

    The "graveyard" is lessons in the decay band [GC_THRESHOLD, 0.4) — still on disk
    but below the active-injection threshold (0.3 default in inject_lessons).  These
    are recoverable via ``reinforce_lesson()``.

    Args:
        topic:      Keywords to fuzzy-match against lesson text (space-separated; any
                    word match counts; ranked by match ratio then score).
        min_score:  Lower bound — default is GC_THRESHOLD (0.2) to include everything
                    that hasn't been GC'd yet.
        max_score:  Upper bound — default 0.4 (just below the injection threshold 0.3,
                    plus a small buffer to surface lessons that need one reinforcement
                    to become active again).
        limit:      Maximum results to return.
        resurrect:  If True, automatically call ``reinforce_lesson()`` on every match,
                    bumping them back toward the active zone.  Default False (read-only).

    Returns a list of TieredLesson sorted by similarity then score (descending).
    """
    keywords = [w.lower() for w in topic.split() if w]
    results: List[TieredLesson] = []

    for tier in (MemoryTier.MEDIUM, MemoryTier.LONG):
        lessons = load_tiered_lessons(tier=tier, min_score=min_score)
        for tl in lessons:
            if tl.score >= max_score:
                continue
            text = tl.lesson.lower()
            match_ratio = sum(1 for kw in keywords if kw in text) / max(len(keywords), 1)
            if match_ratio > 0:
                results.append((match_ratio, tl.score, tl))

    results.sort(key=lambda x: (x[0], x[1]), reverse=True)
    matched = [tl for _, _, tl in results[:limit]]

    if resurrect:
        for tl in matched:
            reinforce_lesson(tl.lesson_id, tier=tl.tier)

    return matched


def forget_lesson(lesson_id: str, tier: str = MemoryTier.MEDIUM) -> bool:
    """Permanently remove a lesson from a tier. Returns True if found and removed."""
    lessons = load_tiered_lessons(tier=tier, min_score=0.0)
    before = len(lessons)
    lessons = [l for l in lessons if l.lesson_id != lesson_id]
    if len(lessons) == before:
        return False
    _rewrite_tiered_lessons(tier=tier, lessons=lessons)
    return True


def promote_lesson(lesson_id: str) -> bool:
    """Promote a medium-tier lesson to long-tier.

    Eligibility: score >= PROMOTE_MIN_SCORE AND sessions_validated >= PROMOTE_MIN_SESSIONS.
    Returns True if promotion succeeded.
    """
    lessons = load_tiered_lessons(tier=MemoryTier.MEDIUM, min_score=0.0)
    target = next((l for l in lessons if l.lesson_id == lesson_id), None)
    if not target:
        return False
    if target.score < PROMOTE_MIN_SCORE or target.sessions_validated < PROMOTE_MIN_SESSIONS:
        return False
    # Remove from medium, add to long
    lessons = [l for l in lessons if l.lesson_id != lesson_id]
    _rewrite_tiered_lessons(tier=MemoryTier.MEDIUM, lessons=lessons)
    target.tier = MemoryTier.LONG
    _append_tiered_lesson(target, tier=MemoryTier.LONG)
    return True


# ---------------------------------------------------------------------------
# Decay cycle (run daily / on session start)
# ---------------------------------------------------------------------------

def run_decay_cycle(
    tier: str = MemoryTier.MEDIUM,
    *,
    dry_run: bool = False,
) -> Dict[str, int]:
    """Apply decay to all lessons in a tier, auto-promote eligibles, GC below threshold.

    Returns a dict with counts: decayed, promoted, gc'd.
    """
    lessons = load_tiered_lessons(tier=tier, min_score=0.0)

    decayed = 0
    promoted_ids = []
    gc_ids = []

    for tl in lessons:
        days = _days_since(tl.last_reinforced)
        if days > 0:
            old_score = tl.score
            tl.score = decay_score(tl.score, days)
            if tl.score != old_score:
                decayed += 1

        if tier == MemoryTier.MEDIUM:
            if tl.score >= PROMOTE_MIN_SCORE and tl.sessions_validated >= PROMOTE_MIN_SESSIONS:
                promoted_ids.append(tl.lesson_id)
            elif tl.score < GC_THRESHOLD:
                gc_ids.append(tl.lesson_id)

    if not dry_run:
        # Audit trail: log the decay cycle before mutating lesson store.
        try:
            from datetime import datetime as _dt, timezone as _tz
            _cl_path = _tiered_lessons_path(tier).parent / "change_log.jsonl"
            _cl_entry = {
                "ts": _dt.now(_tz.utc).isoformat(),
                "module": "memory",
                "action": "run_decay_cycle",
                "tier": tier,
                "total": len(lessons),
                "decayed": decayed,
                "promoted": len(promoted_ids),
                "gc": len(gc_ids),
                "promoted_ids": promoted_ids,
                "gc_ids": gc_ids,
            }
            with open(_cl_path, "a", encoding="utf-8") as _clf:
                _clf.write(json.dumps(_cl_entry) + "\n")
        except Exception:
            pass  # audit trail must never block execution

        # Promote eligible lessons
        for lid in promoted_ids:
            promote_lesson(lid)

        # Rewrite remaining lessons using the in-memory list (with updated decay scores).
        # Do NOT reload from disk here — a reload would lose the score changes computed above.
        promoted_set = set(promoted_ids)
        gc_set = set(gc_ids)
        remaining = [l for l in lessons if l.lesson_id not in promoted_set and l.lesson_id not in gc_set]
        _rewrite_tiered_lessons(tier=tier, lessons=remaining)

    return {"decayed": decayed, "promoted": len(promoted_ids), "gc": len(gc_ids)}


# ---------------------------------------------------------------------------
# TF-IDF relevance ranking (Phase 35 P1)
# ---------------------------------------------------------------------------

_STOP_WORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "is", "was", "are", "were", "be", "been", "being", "it",
    "its", "this", "that", "these", "those", "i", "we", "you", "he", "she",
    "they", "what", "when", "where", "who", "which", "how", "if", "as", "by",
    "from", "not", "can", "will", "do", "did", "does", "have", "had", "has",
    "should", "would", "could", "may", "might", "step", "goal", "task",
})


def _tokenize(text: str) -> List[str]:
    """Lowercase + split on non-alphanumeric, filter stop words + short tokens."""
    return [
        t for t in re.sub(r"[^a-z0-9]+", " ", text.lower()).split()
        if t not in _STOP_WORDS and len(t) > 2
    ]


def _tfidf_rank(
    query: str,
    lessons: List["TieredLesson"],
    *,
    top_k: Optional[int] = None,
) -> List["TieredLesson"]:
    """Rank lessons by TF-IDF cosine similarity to query.

    Pure stdlib — no sklearn, no numpy. Uses Counter for term frequency,
    log-IDF for inverse document frequency, cosine similarity for ranking.

    Args:
        query: Goal or step text used as the query document.
        lessons: List of TieredLesson objects to rank.
        top_k: Return only the top-K matches. None = return all, ranked.

    Returns:
        Lessons sorted by descending cosine similarity to query.
        Lessons with zero similarity are still included (sorted last).
    """
    if not lessons:
        return []

    query_terms = _tokenize(query)
    if not query_terms:
        return lessons  # no query signal — return as-is

    # Build corpus: query + all lesson texts
    docs: List[List[str]] = [query_terms]
    for l in lessons:
        docs.append(_tokenize(l.lesson))

    n_docs = len(docs)  # includes query

    # IDF: log(N / df + 1) for each term across the corpus
    df: Counter = Counter()
    for doc in docs:
        for term in set(doc):
            df[term] += 1

    def idf(term: str) -> float:
        return math.log(n_docs / (df.get(term, 0) + 1)) + 1.0

    def tfidf_vec(doc_terms: List[str]) -> Dict[str, float]:
        tf = Counter(doc_terms)
        total = max(len(doc_terms), 1)
        return {t: (c / total) * idf(t) for t, c in tf.items()}

    def cosine(v1: Dict[str, float], v2: Dict[str, float]) -> float:
        dot = sum(v1.get(t, 0.0) * v2.get(t, 0.0) for t in v1)
        norm1 = math.sqrt(sum(x * x for x in v1.values())) or 1.0
        norm2 = math.sqrt(sum(x * x for x in v2.values())) or 1.0
        return dot / (norm1 * norm2)

    query_vec = tfidf_vec(query_terms)
    scores: List[tuple] = []
    for lesson, doc_terms in zip(lessons, docs[1:]):
        doc_vec = tfidf_vec(doc_terms)
        sim = cosine(query_vec, doc_vec)
        scores.append((sim, lesson))

    scores.sort(key=lambda x: x[0], reverse=True)
    ranked = [l for _, l in scores]
    return ranked[:top_k] if top_k is not None else ranked


# ---------------------------------------------------------------------------
# Tier-aware context injection
# ---------------------------------------------------------------------------

def inject_tiered_lessons(
    task_type: str,
    goal: str = "",
    *,
    max_long: int = 5,
    max_medium: int = 3,
    include_short: bool = False,
    track_applied: bool = True,
) -> str:
    """Build a lessons injection string that respects tier priority.

    Long-tier lessons are always included (up to max_long).
    Medium-tier lessons are filtered by recency and relevance.
    Short-tier (session) items only included if include_short=True.

    If track_applied=True (default), increments times_applied on each injected
    lesson. This powers the canon-candidates pathway: lessons applied many times
    across diverse task types become candidates for AGENTS.md identity promotion.
    """
    parts: List[str] = []
    applied_ids: List[tuple] = []  # (lesson_id, tier)

    # Load candidate lessons — fetch a wider pool when using TF-IDF ranking
    _pool_multiplier = 3 if goal else 1

    long_candidates = load_tiered_lessons(
        tier=MemoryTier.LONG, task_type=task_type, min_score=0.0,
        limit=max_long * _pool_multiplier,
    )
    if goal and len(long_candidates) > max_long:
        _ranker = _hybrid_rank if _USE_HYBRID else _tfidf_rank
        long_candidates = _ranker(goal, long_candidates, top_k=max_long)
    long_lessons = long_candidates[:max_long]

    if long_lessons:
        parts.append("### Long-Term Lessons (always apply)")
        for l in long_lessons:
            icon = "✓" if l.outcome == "done" else "✗"
            parts.append(f"- {icon} {l.lesson}")
            applied_ids.append((l.lesson_id, MemoryTier.LONG))

    medium_candidates = load_tiered_lessons(
        tier=MemoryTier.MEDIUM, task_type=task_type, min_score=0.3,
        limit=max_medium * _pool_multiplier,
    )
    if goal and len(medium_candidates) > max_medium:
        _ranker = _hybrid_rank if _USE_HYBRID else _tfidf_rank
        medium_candidates = _ranker(goal, medium_candidates, top_k=max_medium)
    medium_lessons = medium_candidates[:max_medium]

    if medium_lessons:
        parts.append("### Medium-Term Lessons (apply if relevant)")
        for l in medium_lessons:
            icon = "✓" if l.outcome == "done" else "✗"
            parts.append(f"- {icon} {l.lesson} [score={l.score:.2f}]")
            applied_ids.append((l.lesson_id, MemoryTier.MEDIUM))

    if include_short and _SHORT_TERM:
        parts.append("### Session Context")
        for k, v in list(_SHORT_TERM.items())[:5]:
            parts.append(f"- {k}: {str(v)[:80]}")

    if not parts:
        return ""

    # Track application counts for canon-candidate detection
    if track_applied and applied_ids:
        _increment_times_applied(applied_ids, task_type=task_type)

    return "## Tiered Lessons\n\n" + "\n".join(parts)


# ---------------------------------------------------------------------------
# Phase 59 (Feynman Steal 10): Multi-round gap analysis
# ---------------------------------------------------------------------------

@dataclass
class GoalGap:
    """A detected gap in goal coverage — motivates a targeted follow-up step.

    Feynman pattern: after each research round, assess gaps and spawn targeted
    steps to fill them. GoalGap describes what evidence or coverage is missing.
    """
    gap_type: str          # "single_source" | "blocked_step" | "lesson_gap" | "no_coverage"
    description: str       # human-readable gap description
    severity: str          # "high" | "medium" | "low"
    suggested_step: str    # what follow-up step to spawn (empty = no suggestion)


def detect_goal_gaps(
    goal: str,
    outcomes: Optional[List["Outcome"]] = None,
    *,
    blocked_steps: Optional[List[str]] = None,
    max_gaps: int = 5,
) -> List[GoalGap]:
    """Detect coverage gaps in a set of outcomes relative to a goal.

    Phase 59 (Feynman Steal 10): Heuristic gap detection — no LLM call.
    Identifies:
    1. Blocked/stuck steps that were attempted but not completed
    2. Lessons that mention key terms in the goal but weren't applied
    3. Goal keywords with zero outcome coverage
    4. Outcomes with no evidence sources (single-source claims)

    Args:
        goal:          The original goal text.
        outcomes:      List of completed Outcomes. Loads recent if None.
        blocked_steps: List of step texts that were blocked/stuck.
        max_gaps:      Maximum number of gaps to return.

    Returns:
        List of GoalGap objects, most severe first.
    """
    gaps: List[GoalGap] = []
    goal_lower = goal.lower()

    # Gap 1: Blocked steps → high-severity gaps
    for step in (blocked_steps or []):
        gaps.append(GoalGap(
            gap_type="blocked_step",
            description=f"Step was blocked and not completed: {step[:100]}",
            severity="high",
            suggested_step=f"Retry with different approach: {step[:80]}",
        ))

    # Gap 2: Load outcomes if not provided
    if outcomes is None:
        try:
            outcomes = load_outcomes(limit=20)
        except Exception:
            outcomes = []

    # Gap 3: Check goal keywords against outcome coverage
    # Extract meaningful keywords from goal (skip stopwords)
    _STOP = {"the", "a", "an", "and", "or", "for", "to", "in", "of", "is",
              "it", "this", "that", "with", "from", "are", "were", "have"}
    goal_words = set(
        w for w in re.findall(r"[a-z]{4,}", goal_lower) if w not in _STOP
    )
    covered_words: set = set()
    for o in outcomes:
        text = (o.goal + " " + o.summary).lower()
        covered_words.update(w for w in goal_words if w in text)

    uncovered = goal_words - covered_words
    if uncovered and len(uncovered) >= 2:
        sample = list(uncovered)[:3]
        gaps.append(GoalGap(
            gap_type="no_coverage",
            description=f"Goal concepts not addressed in outcomes: {', '.join(sample)}",
            severity="medium",
            suggested_step=f"Research specifically: {', '.join(sample)}",
        ))

    # Gap 4: Recent lessons about similar topics that weren't applied
    try:
        relevant_lessons = query_lessons(goal, n=3)
        unused_lessons = [l for l in relevant_lessons if l.times_applied == 0]
        if unused_lessons:
            gaps.append(GoalGap(
                gap_type="lesson_gap",
                description=f"Relevant past lessons not applied: {unused_lessons[0].lesson[:80]}",
                severity="low",
                suggested_step="",  # no specific step — just apply the lesson
            ))
    except Exception:
        pass

    # Sort by severity and truncate
    _sev_order = {"high": 0, "medium": 1, "low": 2}
    gaps.sort(key=lambda g: _sev_order.get(g.severity, 3))
    return gaps[:max_gaps]


def query_lessons(
    query: str,
    *,
    n: int = 3,
    task_type: Optional[str] = None,
    lesson_type: Optional[str] = None,
    tiers: Optional[List[str]] = None,
    min_score: float = 0.0,
) -> "List[TieredLesson]":
    """Retrieve the top-N lessons most relevant to `query` via hybrid retrieval.

    Workers can call this directly in step context to get relevant past insights
    without burning tokens on full lesson injection.

    Args:
        query:       Goal text or step description to match against.
        n:           Maximum number of lessons to return.
        task_type:   If set, only search lessons for this task type.
        lesson_type: If set, only return lessons of this type (NeMo S1 filter).
                     Values: "execution" | "planning" | "recovery" | "verification" | "cost"
        tiers:       Which tiers to search. Default: [LONG, MEDIUM].
        min_score:   Minimum lesson confidence/score to include.

    Returns:
        List of TieredLesson objects (most relevant first).
    """
    if tiers is None:
        tiers = [MemoryTier.LONG, MemoryTier.MEDIUM]

    _ranker = _hybrid_rank if _USE_HYBRID else _tfidf_rank

    candidates: "List[TieredLesson]" = []
    for tier in tiers:
        pool = load_tiered_lessons(
            tier=tier,
            task_type=task_type,
            lesson_type=lesson_type,
            min_score=min_score,
            limit=n * 5,
        )
        candidates.extend(pool)

    if not candidates:
        return []

    ranked = _ranker(query, candidates, top_k=n)
    return ranked[:n]


def _increment_times_applied(
    lesson_ids: List[tuple],
    *,
    task_type: str,
) -> None:
    """Increment times_applied for each (lesson_id, tier) pair.

    Also records which task_types a lesson has been applied to, enabling
    the canon-candidate check (task_type diversity gate).
    """
    for lid, tier in lesson_ids:
        lessons = load_tiered_lessons(tier=tier, min_score=0.0)
        target = next((l for l in lessons if l.lesson_id == lid), None)
        if not target:
            continue
        target.times_applied += 1
        # Track task_type diversity in short-term store (session-level aggregator)
        # Persisted canon-tracking uses a separate canon_stats.jsonl
        _record_canon_hit(lid, tier=tier, task_type=task_type)
        _rewrite_tiered_lessons(tier=tier, lessons=lessons)


# ---------------------------------------------------------------------------
# Canon tracking (long → AGENTS.md identity path)
# ---------------------------------------------------------------------------

CANON_APPLY_THRESHOLD = 10   # times_applied before surfacing as candidate
CANON_TASK_TYPE_MIN = 3      # distinct task_types before surfacing as candidate


def _canon_stats_path() -> Path:
    d = _memory_dir()
    return d / "canon_stats.jsonl"


def _record_canon_hit(lesson_id: str, *, tier: str, task_type: str) -> None:
    """Record that lesson_id was applied to task_type. Appends to canon_stats.jsonl."""
    path = _canon_stats_path()
    entry = {
        "lesson_id": lesson_id,
        "tier": tier,
        "task_type": task_type,
        "at": _current_date(),
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def _load_canon_stats() -> Dict[str, Dict[str, Any]]:
    """Load aggregated canon stats keyed by lesson_id.

    Returns: {lesson_id: {total_hits, task_types: set, tier}}
    """
    path = _canon_stats_path()
    if not path.exists():
        return {}
    stats: Dict[str, Dict[str, Any]] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
                lid = e["lesson_id"]
                if lid not in stats:
                    stats[lid] = {"total_hits": 0, "task_types": set(), "tier": e.get("tier", MemoryTier.LONG)}
                stats[lid]["total_hits"] += 1
                stats[lid]["task_types"].add(e.get("task_type", "general"))
            except Exception:
                continue
    except Exception:
        pass
    return stats


def get_canon_candidates(
    *,
    min_hits: int = CANON_APPLY_THRESHOLD,
    min_task_types: int = CANON_TASK_TYPE_MIN,
) -> List[Dict[str, Any]]:
    """Return long-tier lessons eligible for promotion to AGENTS.md identity.

    Eligibility: times_applied >= min_hits AND distinct task_types >= min_task_types.
    Candidates are surfaced for human review — never auto-written to AGENTS.md.
    """
    stats = _load_canon_stats()
    long_lessons = load_tiered_lessons(tier=MemoryTier.LONG, min_score=0.0, limit=200)
    lesson_map = {l.lesson_id: l for l in long_lessons}

    candidates = []
    for lid, s in stats.items():
        if s["tier"] != MemoryTier.LONG:
            continue
        if s["total_hits"] < min_hits:
            continue
        if len(s["task_types"]) < min_task_types:
            continue
        lesson = lesson_map.get(lid)
        if not lesson:
            continue
        candidates.append({
            "lesson_id": lid,
            "lesson": lesson.lesson,
            "task_type": lesson.task_type,
            "score": round(lesson.score, 3),
            "times_applied": s["total_hits"],
            "task_types_seen": sorted(s["task_types"]),
            "sessions_validated": lesson.sessions_validated,
            "recorded_at": lesson.recorded_at[:10],
            "recommendation": "PROMOTE TO AGENTS.md — identity-level pattern",
        })

    candidates.sort(key=lambda x: x["times_applied"], reverse=True)
    return candidates


# ---------------------------------------------------------------------------
# Memory status report
# ---------------------------------------------------------------------------

def memory_status() -> Dict[str, Any]:
    """Return a status report across all tiers."""
    def _tier_stats(tier: str) -> Dict[str, Any]:
        lessons = load_tiered_lessons(tier=tier, min_score=0.0)
        if not lessons:
            return {"count": 0}
        scores = [l.score for l in lessons]
        decay_candidates = [l for l in lessons if l.score < GC_THRESHOLD]
        promote_candidates = [
            l for l in lessons
            if l.score >= PROMOTE_MIN_SCORE and l.sessions_validated >= PROMOTE_MIN_SESSIONS
        ] if tier == MemoryTier.MEDIUM else []
        return {
            "count": len(lessons),
            "avg_score": round(sum(scores) / len(scores), 3),
            "min_score": round(min(scores), 3),
            "max_score": round(max(scores), 3),
            "gc_candidates": len(decay_candidates),
            "promote_candidates": len(promote_candidates),
            "oldest": min(l.recorded_at[:10] for l in lessons),
            "newest": max(l.recorded_at[:10] for l in lessons),
        }

    return {
        "short": {"count": len(_SHORT_TERM), "note": "in-process only"},
        "medium": _tier_stats(MemoryTier.MEDIUM),
        "long": _tier_stats(MemoryTier.LONG),
        "rules": {"count": len(load_standing_rules())},
        "gc_threshold": GC_THRESHOLD,
        "promote_min_score": PROMOTE_MIN_SCORE,
        "promote_min_sessions": PROMOTE_MIN_SESSIONS,
    }


# ---------------------------------------------------------------------------
# Phase 56: Standing Rules — promotion cycle top tier
# ---------------------------------------------------------------------------
# Observation → hypothesis (2+ confirmations) → standing rule (applied by default)
# Contradiction demotes back to hypothesis. Rules survive indefinitely (no decay).
# @lat: [[memory-system#Pending: Promotion Cycle (Phase 56)]]

_RULES_FILENAME = "standing_rules.jsonl"
_HYPOTHESES_FILENAME = "hypotheses.jsonl"

# Minimum long-tier confirmations before promoting to standing rule
RULE_PROMOTE_CONFIRMATIONS = 2


@dataclass
class StandingRule:
    """A promoted rule applied unconditionally in every planning call."""
    rule_id: str
    rule: str                       # The rule text injected into decompose
    source_lesson_id: str           # Long-tier lesson this was promoted from
    domain: str                     # goal domain / task_type tag
    confirmations: int              # times confirmed in production after promotion
    contradictions: int             # times contradicted (≥1 → demoted back to hypothesis)
    promoted_at: str
    last_applied: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_id": self.rule_id, "rule": self.rule,
            "source_lesson_id": self.source_lesson_id, "domain": self.domain,
            "confirmations": self.confirmations, "contradictions": self.contradictions,
            "promoted_at": self.promoted_at, "last_applied": self.last_applied,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "StandingRule":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


@dataclass
class Hypothesis:
    """A lesson being tracked toward standing-rule promotion."""
    hyp_id: str
    lesson: str
    domain: str
    confirmations: int              # how many sessions have confirmed this pattern
    contradictions: int
    source_lesson_ids: List[str]    # lessons that contributed
    first_seen: str
    last_seen: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "hyp_id": self.hyp_id, "lesson": self.lesson, "domain": self.domain,
            "confirmations": self.confirmations, "contradictions": self.contradictions,
            "source_lesson_ids": self.source_lesson_ids,
            "first_seen": self.first_seen, "last_seen": self.last_seen,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Hypothesis":
        return cls(**{k: d.get(k, v) for k, v in {
            "hyp_id": "", "lesson": "", "domain": "", "confirmations": 0,
            "contradictions": 0, "source_lesson_ids": [], "first_seen": "", "last_seen": "",
        }.items()})


def _rules_path() -> Path:
    return _memory_dir() / _RULES_FILENAME


def _hypotheses_path() -> Path:
    return _memory_dir() / _HYPOTHESES_FILENAME


def load_standing_rules(domain: Optional[str] = None) -> List[StandingRule]:
    """Load all standing rules, optionally filtered by domain."""
    try:
        path = _rules_path()
        if not path.exists():
            return []
        rules = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    rules.append(StandingRule.from_dict(json.loads(line)))
                except Exception:
                    pass
        if domain:
            rules = [r for r in rules if r.domain == domain or r.domain == ""]
        return rules
    except Exception:
        return []


def load_hypotheses(domain: Optional[str] = None) -> List[Hypothesis]:
    """Load tracked hypotheses, optionally filtered by domain."""
    try:
        path = _hypotheses_path()
        if not path.exists():
            return []
        hyps = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    hyps.append(Hypothesis.from_dict(json.loads(line)))
                except Exception:
                    pass
        if domain:
            hyps = [h for h in hyps if h.domain == domain or h.domain == ""]
        return hyps
    except Exception:
        return []


def _rewrite_rules(rules: List[StandingRule]) -> None:
    try:
        _rules_path().write_text(
            "\n".join(json.dumps(r.to_dict()) for r in rules) + ("\n" if rules else ""),
            encoding="utf-8",
        )
    except Exception:
        pass


def _rewrite_hypotheses(hyps: List[Hypothesis]) -> None:
    try:
        _hypotheses_path().write_text(
            "\n".join(json.dumps(h.to_dict()) for h in hyps) + ("\n" if hyps else ""),
            encoding="utf-8",
        )
    except Exception:
        pass


def observe_pattern(lesson: str, domain: str, *, source_lesson_id: str = "") -> Optional[StandingRule]:
    """Record a confirmed lesson observation. Promotes to StandingRule at threshold.

    Call this when a long-tier lesson is confirmed again in production:
    - First call: creates/increments Hypothesis
    - At RULE_PROMOTE_CONFIRMATIONS: promotes to StandingRule and removes Hypothesis

    Returns the new StandingRule if promotion occurred, else None.
    """
    now = _current_date()
    hyps = load_hypotheses(domain=None)

    # Find existing hypothesis by similarity (exact or near-exact lesson text)
    target_hyp: Optional[Hypothesis] = None
    lesson_lower = lesson.lower().strip()
    for h in hyps:
        if h.lesson.lower().strip() == lesson_lower or h.domain == domain and _text_similarity(h.lesson, lesson) > 0.85:
            target_hyp = h
            break

    if target_hyp is None:
        # New observation — create hypothesis
        import uuid as _uuid
        target_hyp = Hypothesis(
            hyp_id=str(_uuid.uuid4())[:8],
            lesson=lesson,
            domain=domain,
            confirmations=1,
            contradictions=0,
            source_lesson_ids=[source_lesson_id] if source_lesson_id else [],
            first_seen=now,
            last_seen=now,
        )
        hyps.append(target_hyp)
        _rewrite_hypotheses(hyps)
        return None

    # Existing hypothesis — confirm
    target_hyp.confirmations += 1
    target_hyp.last_seen = now
    if source_lesson_id and source_lesson_id not in target_hyp.source_lesson_ids:
        target_hyp.source_lesson_ids.append(source_lesson_id)

    if target_hyp.confirmations >= RULE_PROMOTE_CONFIRMATIONS:
        # Promote to standing rule
        import uuid as _uuid
        rule = StandingRule(
            rule_id=str(_uuid.uuid4())[:8],
            rule=target_hyp.lesson,
            source_lesson_id=target_hyp.source_lesson_ids[0] if target_hyp.source_lesson_ids else "",
            domain=target_hyp.domain,
            confirmations=target_hyp.confirmations,
            contradictions=0,
            promoted_at=now,
        )
        with open(_rules_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(rule.to_dict()) + "\n")
        # Remove from hypotheses
        hyps = [h for h in hyps if h.hyp_id != target_hyp.hyp_id]
        _rewrite_hypotheses(hyps)
        log.info("standing rule promoted: %s (domain=%s, confirmations=%d)",
                 rule.rule_id, rule.domain, rule.confirmations)
        return rule

    _rewrite_hypotheses(hyps)
    return None


def contradict_pattern(lesson: str, domain: str) -> bool:
    """Record a contradiction — demotes hypothesis or increments rule.contradictions.

    A standing rule with contradictions >= 1 should be flagged for review.
    Returns True if something was found and updated.
    """
    lesson_lower = lesson.lower().strip()

    # Check standing rules first
    rules = load_standing_rules()
    for r in rules:
        if r.rule.lower().strip() == lesson_lower or _text_similarity(r.rule, lesson) > 0.85:
            r.contradictions += 1
            _rewrite_rules(rules)
            log.warning("standing rule contradicted: rule_id=%s contradictions=%d", r.rule_id, r.contradictions)
            return True

    # Check hypotheses
    hyps = load_hypotheses()
    for h in hyps:
        if h.lesson.lower().strip() == lesson_lower or _text_similarity(h.lesson, lesson) > 0.85:
            h.contradictions += 1
            if h.contradictions > h.confirmations:
                # Demote — remove hypothesis
                hyps = [x for x in hyps if x.hyp_id != h.hyp_id]
                log.info("hypothesis demoted (contradictions > confirmations): %s", h.hyp_id)
            _rewrite_hypotheses(hyps)
            return True

    return False


def inject_standing_rules(domain: str = "") -> str:
    """Return standing rules formatted for injection into decompose system prompt.

    Returns empty string if no rules exist (safe to always call).
    """
    rules = load_standing_rules(domain=domain)
    if not rules:
        return ""
    lines = ["### Standing Rules (apply unconditionally)"]
    for r in rules:
        lines.append(f"- {r.rule}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Phase 56: Decision Journal
# ---------------------------------------------------------------------------
# ADR-style log of significant decisions. Searched before new decisions.
# Format: what was decided, alternatives considered, why this won, trade-offs.

_DECISIONS_FILENAME = "decisions.jsonl"

DECISION_SEARCH_LIMIT = 3  # max decisions to inject into context


@dataclass
class Decision:
    """A recorded architectural or strategic decision."""
    decision_id: str
    domain: str                     # goal domain / subsystem tag
    decision: str                   # what was decided
    alternatives: List[str]         # what else was considered
    rationale: str                  # why this won
    trade_offs: str                 # known downsides
    recorded_at: str
    goal_context: str = ""          # the goal that prompted this decision

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision_id": self.decision_id, "domain": self.domain,
            "decision": self.decision, "alternatives": self.alternatives,
            "rationale": self.rationale, "trade_offs": self.trade_offs,
            "recorded_at": self.recorded_at, "goal_context": self.goal_context,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Decision":
        return cls(**{k: d.get(k, v) for k, v in {
            "decision_id": "", "domain": "", "decision": "", "alternatives": [],
            "rationale": "", "trade_offs": "", "recorded_at": "", "goal_context": "",
        }.items()})


def _decisions_path() -> Path:
    return _memory_dir() / _DECISIONS_FILENAME


def record_decision(
    decision: str,
    rationale: str,
    *,
    domain: str = "",
    alternatives: Optional[List[str]] = None,
    trade_offs: str = "",
    goal_context: str = "",
) -> Decision:
    """Record a significant decision to the decision journal.

    Args:
        decision: What was decided (one sentence).
        rationale: Why this was chosen over alternatives.
        domain: Subsystem/domain tag for filtering (e.g. "memory", "routing").
        alternatives: Other options that were considered.
        trade_offs: Known downsides or limitations of the chosen approach.
        goal_context: The goal that prompted this decision.

    Returns:
        The recorded Decision object.
    """
    import uuid as _uuid
    d = Decision(
        decision_id=str(_uuid.uuid4())[:8],
        domain=domain,
        decision=decision,
        alternatives=alternatives or [],
        rationale=rationale,
        trade_offs=trade_offs,
        recorded_at=datetime.now(timezone.utc).isoformat(),
        goal_context=goal_context,
    )
    try:
        with open(_decisions_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(d.to_dict()) + "\n")
    except Exception as exc:
        log.warning("decision journal write failed: %s", exc)
    return d


def search_decisions(query: str, domain: str = "", limit: int = DECISION_SEARCH_LIMIT) -> List[Decision]:
    """Search the decision journal for relevant prior decisions.

    Uses TF-IDF similarity against decision + rationale text.
    Returns top-K matches, newest first on ties.
    """
    try:
        path = _decisions_path()
        if not path.exists():
            return []
        all_decisions = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    all_decisions.append(Decision.from_dict(json.loads(line)))
                except Exception:
                    pass
        if domain:
            all_decisions = [d for d in all_decisions if d.domain == domain or d.domain == ""]

        if not all_decisions:
            return []

        # Rank by similarity to query
        class _FakeTL:
            """Adapter so _tfidf_rank can score decisions."""
            def __init__(self, d: Decision):
                self.lesson = f"{d.decision} {d.rationale}"
                self._d = d

        scored = _tfidf_rank(query, [_FakeTL(d) for d in all_decisions], top_k=limit)
        return [s._d for s in scored]
    except Exception:
        return []


def inject_decisions(goal: str, domain: str = "") -> str:
    """Return relevant prior decisions formatted for injection into decompose prompt.

    Returns empty string if no relevant decisions (safe to always call).
    """
    decisions = search_decisions(goal, domain=domain)
    if not decisions:
        return ""
    lines = ["### Prior Decisions (search before making new ones)"]
    for d in decisions:
        alts = f" Alternatives considered: {', '.join(d.alternatives)}." if d.alternatives else ""
        lines.append(f"- **{d.decision}** — {d.rationale}{alts}")
    return "\n".join(lines)
