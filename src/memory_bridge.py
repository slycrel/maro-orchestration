#!/usr/bin/env python3
"""Memory bridge: ingest substrate into SqliteMemoryStore (Phase 1 experiment gate).

This module mirrors the lessons substrate (~/.maro/workspace/memory/lessons.jsonl)
into a SqliteMemoryStore behind the memory_port protocol. The ingest is incremental
(tracked by byte offset), never modifies source files, and derives scope from lesson
metadata (acquired_for/thread_id if present, else global scope "").

Design: "memory becomes a module; 3rd party backend behind a port" (GOAL_BRAIN 2026-07-07).
This is the adapter-0 path: feed existing crystallization output to a MemoryStore,
measure worker recall quality, then decide on further indexing/retrieval work.

Usage:
    from memory_bridge import ingest_lessons_to_store, recall_for_worker

    # One-time setup: ingest lessons into the store
    ingest_lessons_to_store()

    # In worker dispatch: get top-K items for the ticket
    items = recall_for_worker("research ticket text", thread_scope="thread/xyz")
    formatted = format_block(items, max_chars=1200)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import List, Optional

from memory_port import MemoryItem, format_block
from memory_sqlite import SqliteMemoryStore

log = logging.getLogger("maro.memory_bridge")


def _memory_store_path() -> Path:
    """Path to the memory module store root."""
    from config import memory_dir
    root = memory_dir() / "module"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _lessons_source_paths() -> List[Path]:
    """Paths to lessons source files.

    Checks both the tiered lessons (medium/long) and falls back to the single
    lessons.jsonl if it exists. Returns paths that actually exist.
    """
    from config import memory_dir
    base = memory_dir()
    candidates = [
        base / "medium" / "lessons.jsonl",
        base / "long" / "lessons.jsonl",
        base / "lessons.jsonl",
    ]
    return [p for p in candidates if p.exists()]


def _derive_scope_from_lesson(lesson: dict) -> str:
    """Derive scope from lesson metadata.

    Scope hierarchy:
    - acquired_for: goal_id → "goal/<id>" (inferred)
    - thread_id: explicit → "thread/<id>" (if present)
    - otherwise: "" (global scope)

    Note: current TieredLesson has acquired_for (goal_id) but no explicit
    thread_id. Future versions may carry thread hierarchy in meta field.
    """
    # Check for explicit thread in meta (forward compatible)
    meta = lesson.get("meta") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except (ValueError, TypeError):
            meta = {}

    if meta.get("thread_id"):
        return f"thread/{meta['thread_id']}"

    # Fall back to acquired_for (goal_id) if present
    acquired_for = lesson.get("acquired_for")
    if acquired_for:
        return f"goal/{acquired_for}"

    # Global scope
    return ""


def _lesson_to_memory_item(lesson: dict, kind: str = "lesson") -> MemoryItem:
    """Convert a lesson from JSONL to a MemoryItem.

    Args:
        lesson: Dict parsed from lessons.jsonl line (TieredLesson shape).
        kind: Item kind for the store (default "lesson").

    Returns:
        MemoryItem ready for append to store.
    """
    scope = _derive_scope_from_lesson(lesson)

    # Decay trust by the score field if present (session 40 crystallization).
    # TieredLesson carries score (decayed); use it as trust.
    trust = float(lesson.get("score", 1.0))

    return MemoryItem(
        kind=kind,
        content=lesson.get("lesson", ""),
        scope=scope,
        trust=max(trust, 0.0),  # clamp to [0, 1)
        provenance={
            "lesson_id": lesson.get("lesson_id", ""),
            "source_goal": lesson.get("source_goal", ""),
            "task_type": lesson.get("task_type", ""),
            "outcome": lesson.get("outcome", ""),
            "confidence": lesson.get("confidence", 0.0),
            "sessions_validated": lesson.get("sessions_validated", 0),
            "lesson_type": lesson.get("lesson_type", ""),
        },
        meta={
            "tier": lesson.get("tier", ""),
            "times_applied": lesson.get("times_applied", 0),
            "times_reinforced": lesson.get("times_reinforced", 0),
            "recorded_at": lesson.get("recorded_at", ""),
            "acquired_for": lesson.get("acquired_for", ""),
        },
    )


def _get_ingest_offset(store: SqliteMemoryStore, source_path: Path) -> int:
    """Get the byte offset to resume from.

    Checks an offset file next to each source for incremental tracking.
    If the source has shrunk, returns 0 (corruption detection).
    """
    offset_path = source_path.parent / f"{source_path.name}.offset"

    # Read stored offset
    stored = 0
    if offset_path.exists():
        try:
            stored = int(offset_path.read_text().strip())
        except (ValueError, OSError):
            stored = 0

    # Corruption check: source shrunk
    current_size = source_path.stat().st_size if source_path.exists() else 0
    if stored > current_size:
        log.warning("memory_bridge: source %s shrank (%d→%d); full rebuild",
                    source_path.name, stored, current_size)
        return 0

    return stored


def _save_ingest_offset(source_path: Path, offset: int) -> None:
    """Save the byte offset for incremental resume."""
    offset_path = source_path.parent / f"{source_path.name}.offset"
    offset_path.write_text(str(offset), encoding="utf-8")


def ingest_lessons_to_store(
    store: Optional[SqliteMemoryStore] = None,
    *,
    verbose: bool = False,
) -> dict:
    """Ingest lessons from all source files into the store.

    Args:
        store: SqliteMemoryStore instance. If None, creates one at the default path.
        verbose: If True, log details about ingested items.

    Returns:
        Dict with ingestion stats:
        {
            "ingested": total items added,
            "sources": {source_name: count},
            "errors": count of malformed lines skipped,
        }
    """
    if store is None:
        store = SqliteMemoryStore(_memory_store_path())

    stats = {
        "ingested": 0,
        "sources": {},
        "errors": 0,
    }

    for source_path in _lessons_source_paths():
        ingested_count = 0
        offset = _get_ingest_offset(store, source_path)

        try:
            with open(source_path, "r", encoding="utf-8") as fh:
                fh.seek(offset)
                for line_num, line in enumerate(fh, start=1):
                    line = line.strip()
                    if not line:
                        continue

                    try:
                        lesson = json.loads(line)
                        item = _lesson_to_memory_item(lesson)
                        item_id = store.append(item)
                        ingested_count += 1

                        if verbose:
                            log.debug("memory_bridge: ingested lesson %s → %s",
                                    lesson.get("lesson_id", "?")[:8], item_id)
                    except (ValueError, KeyError, TypeError) as exc:
                        stats["errors"] += 1
                        if verbose:
                            log.warning("memory_bridge: skipped malformed line in %s:%d: %s",
                                      source_path.name, line_num, exc)

                new_offset = fh.tell()
        except OSError as exc:
            log.warning("memory_bridge: cannot read %s: %s", source_path.name, exc)
            new_offset = offset

        # Save progress for next ingest
        _save_ingest_offset(source_path, new_offset)

        stats["ingested"] += ingested_count
        stats["sources"][source_path.name] = ingested_count

        if verbose or ingested_count > 0:
            log.info("memory_bridge: ingested %d items from %s",
                    ingested_count, source_path.name)

    return stats


def recall_for_worker(
    ticket: str,
    *,
    thread_scope: str = "",
    k: int = 5,
    store: Optional[SqliteMemoryStore] = None,
) -> List[MemoryItem]:
    """Recall top-K items for a worker ticket.

    Args:
        ticket: The worker task text to search for.
        thread_scope: Worker's scope (e.g. "thread/xyz" or "goal/abc").
                     Defaults to global "" if not specified.
        k: Number of items to recall (default 5).
        store: SqliteMemoryStore instance. If None, creates one at the default path.

    Returns:
        List of top-K MemoryItems ranked by relevance and trust, or [] on any error.
    """
    if store is None:
        store = SqliteMemoryStore(_memory_store_path())

    return store.recall(
        ticket,
        scope=thread_scope,
        kinds=["lesson"],
        k=k,
    )


def format_worker_memory_block(
    items: List[MemoryItem],
    *,
    max_chars: int = 1200,
) -> str:
    """Format recalled items as an injectable prompt block for workers.

    Uses memory_port.format_block with a worker-specific header.
    """
    if not items:
        return ""

    return format_block(
        items,
        header="Prior lessons from memory:",
        max_chars=max_chars,
    )
