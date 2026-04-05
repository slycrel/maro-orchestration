#!/usr/bin/env python3
"""Phase 40: Pluggable Memory Backend.

Defines the abstract MemoryBackend interface and two concrete implementations:
  - JSONLBackend  — current behavior; one .jsonl file per collection
  - SQLiteBackend — SQLite equivalent; one table per collection

Usage:
    from memory_backends import get_backend
    backend = get_backend()  # reads POE_MEMORY_BACKEND env var (default: jsonl)

Collections (match existing jsonl filenames):
    outcomes        — structured outcome ledger (append-only)
    lessons         — per-task lessons (append-only, dedup on read)
    step_traces     — full execution traces keyed by outcome_id (append-only)
    decisions       — decision journal entries (append-only)
    canon_stats     — canon promotion stats (append-only)
    standing_rules  — active standing rules (rewrite-on-change)
    hypotheses      — active hypotheses (rewrite-on-change)
    tiered/{tier}   — tiered lessons for a named tier (append + rewrite)

Text collections (non-JSON, append-only):
    daily/{date}    — YYYY-MM-DD daily narrative log (plain text)
"""

from __future__ import annotations

import abc
import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

log = logging.getLogger("poe.memory_backends")

# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class MemoryBackend(abc.ABC):
    """Abstract memory storage backend.

    All JSON collections store dicts; text collections store raw strings.
    Collection names are slash-free identifiers (e.g. "outcomes", "lessons",
    "tiered/agenda") — backends translate to their native storage layout.
    """

    @abc.abstractmethod
    def append(self, collection: str, record: Dict[str, Any]) -> None:
        """Append one JSON record to collection (creates if absent)."""

    @abc.abstractmethod
    def read_all(self, collection: str) -> List[Dict[str, Any]]:
        """Return all records from collection (oldest first)."""

    @abc.abstractmethod
    def rewrite(self, collection: str, records: List[Dict[str, Any]]) -> None:
        """Replace all records in collection with records (atomic where possible)."""

    @abc.abstractmethod
    def append_text(self, collection: str, text: str) -> None:
        """Append raw text to a text collection (e.g. daily log)."""

    @abc.abstractmethod
    def read_text(self, collection: str) -> str:
        """Return full text content of a text collection, or '' if absent."""

    # ------------------------------------------------------------------
    # Convenience helpers (built on abstract methods)
    # ------------------------------------------------------------------

    def iter_all(self, collection: str) -> Iterator[Dict[str, Any]]:
        """Iterate records without holding them all in memory."""
        yield from self.read_all(collection)

    def count(self, collection: str) -> int:
        return len(self.read_all(collection))

    def exists(self, collection: str) -> bool:
        """Return True if collection has any records."""
        return bool(self.read_all(collection))


# ---------------------------------------------------------------------------
# JSONL backend (preserves current behaviour exactly)
# ---------------------------------------------------------------------------

class JSONLBackend(MemoryBackend):
    """File-per-collection JSONL storage — identical to current memory.py behaviour."""

    def __init__(self, memory_dir: Path) -> None:
        self._base = memory_dir
        self._base.mkdir(parents=True, exist_ok=True)

    def _path(self, collection: str) -> Path:
        """Map collection name to .jsonl file path."""
        if "/" in collection:
            # e.g. "tiered/agenda" → memory/tiered/agenda/lessons.jsonl
            parts = collection.split("/", 1)
            p = self._base / parts[0] / parts[1] / "lessons.jsonl"
        else:
            p = self._base / f"{collection}.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def _text_path(self, collection: str) -> Path:
        """Map text collection name to file path."""
        if "/" in collection:
            parts = collection.split("/", 1)
            p = self._base / parts[0] / parts[1]
        else:
            p = self._base / collection
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def append(self, collection: str, record: Dict[str, Any]) -> None:
        path = self._path(collection)
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
        except OSError as exc:
            log.warning("JSONLBackend.append(%s): %s", collection, exc)

    def read_all(self, collection: str) -> List[Dict[str, Any]]:
        path = self._path(collection)
        if not path.exists():
            return []
        records: List[Dict[str, Any]] = []
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        except OSError:
            pass
        return records

    def rewrite(self, collection: str, records: List[Dict[str, Any]]) -> None:
        path = self._path(collection)
        tmp = path.with_suffix(".jsonl.tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                for r in records:
                    f.write(json.dumps(r) + "\n")
            tmp.replace(path)
        except OSError as exc:
            log.warning("JSONLBackend.rewrite(%s): %s", collection, exc)
            tmp.unlink(missing_ok=True)

    def append_text(self, collection: str, text: str) -> None:
        path = self._text_path(collection)
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(text)
        except OSError as exc:
            log.warning("JSONLBackend.append_text(%s): %s", collection, exc)

    def read_text(self, collection: str) -> str:
        path = self._text_path(collection)
        if not path.exists():
            return ""
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return ""


# ---------------------------------------------------------------------------
# SQLite backend
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_records (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    collection  TEXT    NOT NULL,
    data        TEXT    NOT NULL,        -- JSON-encoded record
    recorded_at TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_collection ON memory_records(collection);

CREATE TABLE IF NOT EXISTS memory_text (
    collection  TEXT    NOT NULL,
    content     TEXT    NOT NULL DEFAULT '',
    PRIMARY KEY (collection)
);
"""


class SQLiteBackend(MemoryBackend):
    """SQLite-backed memory storage.

    Schema mirrors the JSONL files:
      memory_records(id, collection, data TEXT/JSON, recorded_at)
      memory_text(collection, content)

    Collections map 1-to-1 with JSONL collection names (e.g. "outcomes",
    "tiered/agenda"). The slash is preserved as-is in the collection column.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            con.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self._db_path), timeout=10)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        return con

    def append(self, collection: str, record: Dict[str, Any]) -> None:
        try:
            with self._connect() as con:
                con.execute(
                    "INSERT INTO memory_records (collection, data) VALUES (?, ?)",
                    (collection, json.dumps(record)),
                )
                con.commit()
        except sqlite3.Error as exc:
            log.warning("SQLiteBackend.append(%s): %s", collection, exc)

    def read_all(self, collection: str) -> List[Dict[str, Any]]:
        try:
            with self._connect() as con:
                cur = con.execute(
                    "SELECT data FROM memory_records WHERE collection=? ORDER BY id ASC",
                    (collection,),
                )
                rows = cur.fetchall()
            records: List[Dict[str, Any]] = []
            for (data,) in rows:
                try:
                    records.append(json.loads(data))
                except json.JSONDecodeError:
                    pass
            return records
        except sqlite3.Error as exc:
            log.warning("SQLiteBackend.read_all(%s): %s", collection, exc)
            return []

    def rewrite(self, collection: str, records: List[Dict[str, Any]]) -> None:
        try:
            with self._connect() as con:
                con.execute(
                    "DELETE FROM memory_records WHERE collection=?", (collection,)
                )
                con.executemany(
                    "INSERT INTO memory_records (collection, data) VALUES (?, ?)",
                    [(collection, json.dumps(r)) for r in records],
                )
                con.commit()
        except sqlite3.Error as exc:
            log.warning("SQLiteBackend.rewrite(%s): %s", collection, exc)

    def append_text(self, collection: str, text: str) -> None:
        try:
            with self._connect() as con:
                con.execute(
                    """
                    INSERT INTO memory_text (collection, content) VALUES (?, ?)
                    ON CONFLICT(collection) DO UPDATE SET content = content || excluded.content
                    """,
                    (collection, text),
                )
                con.commit()
        except sqlite3.Error as exc:
            log.warning("SQLiteBackend.append_text(%s): %s", collection, exc)

    def read_text(self, collection: str) -> str:
        try:
            with self._connect() as con:
                cur = con.execute(
                    "SELECT content FROM memory_text WHERE collection=?", (collection,)
                )
                row = cur.fetchone()
            return row[0] if row else ""
        except sqlite3.Error as exc:
            log.warning("SQLiteBackend.read_text(%s): %s", collection, exc)
            return ""


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_backend(memory_dir: Optional[Path] = None) -> MemoryBackend:
    """Return the configured backend.

    Reads POE_MEMORY_BACKEND (default: "jsonl").
    Supported values: "jsonl", "sqlite".

    Args:
        memory_dir: Override the memory directory. If None, falls back to
            orch_items.memory_dir() (same resolution as existing memory.py).
    """
    if memory_dir is None:
        try:
            from orch_items import memory_dir as _md
            memory_dir = _md()
        except ImportError:
            memory_dir = Path("memory")

    backend_name = os.environ.get("POE_MEMORY_BACKEND", "jsonl").lower().strip()

    if backend_name == "sqlite":
        db_path = memory_dir / "memory.db"
        log.debug("Using SQLiteBackend at %s", db_path)
        return SQLiteBackend(db_path)

    if backend_name != "jsonl":
        log.warning("Unknown POE_MEMORY_BACKEND=%r, falling back to jsonl", backend_name)

    log.debug("Using JSONLBackend at %s", memory_dir)
    return JSONLBackend(memory_dir)
