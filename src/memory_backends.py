#!/usr/bin/env python3
"""Phase 40: Pluggable Memory Backend.

Defines the abstract MemoryBackend interface and two concrete implementations:
  - JSONLBackend  — current behavior; one .jsonl file per collection
  - SQLiteBackend — SQLite equivalent; one table per collection

Usage:
    from memory_backends import get_backend
    backend = get_backend()  # reads MARO_MEMORY_BACKEND env var (default: jsonl)

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

log = logging.getLogger("maro.memory_backends")

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
        """Replace all PARSEABLE records in collection with records (atomic
        where possible). A stored row the strict reader cannot vouch for is
        NOT replaced — it is carried through the rewrite verbatim and
        announced, because `read_all` never returned it and the caller
        therefore cannot be deciding anything about it (adversarial r11)."""

    @abc.abstractmethod
    def transform(self, collection: str,
                  fn: "Any") -> List[Dict[str, Any]]:
        """Atomically read -> fn(records) -> rewrite, as ONE transaction.

        Part of the abstract contract, not a JSONL nicety (adversarial
        r14, five seats, probed): r13 added this to JSONLBackend only,
        so the selectable SQLite twin kept the exact lost-update race
        the method exists to close — a clean append between a caller's
        read_all() and rewrite() was reader-vouched, deleted, and never
        reinserted. Every backend must make the read-modify-write
        decidable by construction. Returns the record list fn produced
        (and the store now holds)."""

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
        # Prove the emission BEFORE the store is touched (adversarial r12,
        # four seats): the bare dumps here happily wrote CPython NaN and
        # clean \udcXX escapes — rows read_all() then announces and skips,
        # i.e. this writer minting its own strandees while reporting
        # success. A failure raises out; the store is intact.
        from jsonl_utils import prove_record_line
        line = prove_record_line(record)
        try:
            from file_lock import locked_append
            locked_append(path, line)
        except OSError as exc:
            # Loud, and it PROPAGATES (adversarial r13, QA, probed): the
            # old warn-and-return converted a disk-full into apparent
            # success — the caller proceeded as though the record existed
            # and no later reader can announce a row that was never
            # written. The record is the caller's to retry or surface.
            log.error("JSONLBackend.append(%s): record NOT persisted "
                      "(%s): %s", collection, path, exc)
            raise

    def read_all(self, collection: str) -> List[Dict[str, Any]]:
        """Every record in the collection, with any loss announced.

        Surfaced by adversarial r10: the scanner's new lexical scoping
        made this pair visible for the first time, and it was carrying
        three of the arc's families at once. `read_text(encoding="utf-8")`
        is a STRICT whole-file decode, and `except OSError` does not catch
        UnicodeDecodeError (a ValueError), so one torn byte raised out of
        every caller; the per-line `except json.JSONDecodeError: pass`
        dropped rows in silence; and `rewrite()` — the method directly
        below, whose own comment names the "read_all -> transform ->
        rewrite" pattern — writes the survivors back, which turns each
        silent drop into a deletion. The shared reader answers all three:
        one torn byte costs one row, the loss is announced with the store
        path, and a byte-tainted row is refused rather than laundered.
        """
        from jsonl_utils import read_jsonl_announced
        path = self._path(collection)
        if not path.exists():
            return []
        return read_jsonl_announced(path, f"JSONLBackend.read_all({collection})")

    def rewrite(self, collection: str, records: List[Dict[str, Any]]) -> None:
        # Lock + atomic replace: the bare tmp+replace was atomic for readers
        # but still lost concurrent appends landing between the caller's
        # read_all and this replace. Callers doing read→transform→rewrite
        # should hold locked_write(path) across both (reentrant here).
        #
        # Strandees are THIS method's job, not the caller's. Adversarial
        # r11 — all five seats, independently, the round's only unanimous
        # finding: read_all() announces a row it cannot vouch for and
        # returns a list WITHOUT it, so the documented read_all ->
        # transform -> rewrite composition deleted the announced row. The
        # caller cannot preserve what its input never contained; the
        # rewrite is the destructive half, so the rewrite re-reads the
        # store under its own lock and carries every raw line loads_clean
        # refuses, verbatim, after the caller's records (this store has no
        # per-row key, so ordinals cannot be reassigned; relative strandee
        # order is kept).
        from jsonl_utils import (is_frame_blank, loads_clean,
                                 prove_record_line, store_text)
        path = self._path(collection)
        try:
            from file_lock import locked_write, atomic_write
            with locked_write(path):
                stranded: List[str] = []
                if path.exists():
                    for line in store_text(path).split("\n"):
                        if is_frame_blank(line):
                            continue
                        try:
                            # The strand rule must MIRROR read_all's
                            # admission, not just the parser: read_all also
                            # announces-and-skips clean non-dict JSON
                            # (`null`, arrays, strings), so those rows were
                            # never in the caller's list either — r11's
                            # re-read pass stranded only parse failures and
                            # this rewrite deleted a `null` row it had just
                            # been taught not to (adversarial r12, two
                            # seats, probed).
                            if not isinstance(loads_clean(line), dict):
                                stranded.append(line)
                        except Exception:
                            stranded.append(line)
                # Prove every emission through the reader's own door
                # BEFORE the replace — allow_nan=False alone let a lone
                # surrogate ride through as a clean escape and replace
                # healthy data with a row read_all() strands (adversarial
                # r12, four seats, probed). The whole payload is built
                # first, so a failure aborts with the store untouched.
                # Strandees ride FIRST (adversarial r13, Failure Operator,
                # probed: they were re-homed to the tail, where any keyed
                # last-row-wins consumer would let a later-repaired legacy
                # row outrank the caller's records — same ordinal decision
                # as skills._save_skills, r7). A generic rewrite has no
                # slot metadata, so exact ordinals cannot be preserved;
                # oldest-content-first is the honest deterministic choice.
                # A torn FINAL row that lost its LF terminator gains one
                # here: strandees no longer ride last, so the LF is
                # required row framing, the payload bytes are unchanged,
                # and re-splitting yields the identical strandee
                # (adversarial r14, two seats, probed; judged
                # accept-and-pin — "verbatim" means the payload, not the
                # terminator state of a row torn mid-write).
                payload = (
                    "".join(l + "\n" for l in stranded)
                    + "".join(prove_record_line(r) + "\n" for r in records))
                atomic_write(path, payload, errors="surrogateescape")
                # Announce AFTER the commit (adversarial r13, QA, probed):
                # the old order logged "carried through the rewrite" and
                # THEN built the payload, so a refused emission left a
                # false audit line about a rewrite that never happened.
                if stranded:
                    log.warning(
                        "JSONLBackend.rewrite(%s): %d unreadable row(s) "
                        "carried through the rewrite verbatim (%s)",
                        collection, len(stranded), path)
        except OSError as exc:
            log.error("JSONLBackend.rewrite(%s): rewrite NOT performed, "
                      "store unchanged (%s): %s", collection, path, exc)
            raise

    def transform(self, collection: str,
                  fn: "Any") -> List[Dict[str, Any]]:
        """Atomically read -> fn(records) -> rewrite, under ONE lock.

        The bare `read_all() -> transform -> rewrite()` composition is a
        lost-update race (adversarial r13, Skeptic, probed): a record
        appended between the caller's read and its rewrite is CLEAN, so
        the rewrite's strandee pass cannot distinguish "the caller
        decided to drop it" from "the caller never saw it" — with a bare
        list API that is undecidable, and the concurrent append was
        silently deleted. This method makes the decision decidable by
        construction: fn receives the records read under the same lock
        the write commits under. Use this for every read-modify-write;
        `rewrite()` remains for whole-store replacement where losing a
        concurrent append is acceptable and announced by design.

        Returns the record list fn produced (and the store now holds).
        """
        path = self._path(collection)
        from file_lock import locked_write
        # require=True: locked_write's documented fail-open path
        # (MARO_FILELOCK_FAIL_OPEN=1, or a lock file that cannot be
        # created) yields WITHOUT a lock — and an unlocked transform is
        # precisely the lost-update race this method exists to close
        # (adversarial r14, four seats, probed). A transaction that
        # cannot lock must refuse to run, not degrade into the bug.
        with locked_write(path, require=True):   # reentrant re-enter ok
            records = fn(self.read_all(collection))
            self.rewrite(collection, records)
            return records

    def append_text(self, collection: str, text: str) -> None:
        path = self._text_path(collection)
        try:
            from file_lock import locked_write
            with locked_write(path):
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
        # Same emission door as the JSONL twin (adversarial r13,
        # Minimalist: this backend is one MARO_MEMORY_BACKEND flip from
        # live and had kept every writer/reader gap the JSONL side spent
        # three rounds closing).
        from jsonl_utils import prove_record_line
        data = prove_record_line(record)
        try:
            with self._connect() as con:
                con.execute(
                    "INSERT INTO memory_records (collection, data) VALUES (?, ?)",
                    (collection, data),
                )
                con.commit()
        except sqlite3.Error as exc:
            log.error("SQLiteBackend.append(%s): record NOT persisted "
                      "(%s): %s", collection, self._db_path, exc)
            raise

    def read_all(self, collection: str) -> List[Dict[str, Any]]:
        try:
            with self._connect() as con:
                cur = con.execute(
                    "SELECT data FROM memory_records WHERE collection=? ORDER BY id ASC",
                    (collection,),
                )
                rows = cur.fetchall()
            from jsonl_utils import loads_clean
            records: List[Dict[str, Any]] = []
            dropped = 0
            for (data,) in rows:
                # The announced strict reader, same admission as the JSONL
                # twin (adversarial r13, Minimalist, probed): the bare
                # `except JSONDecodeError: pass` silently dropped a
                # damaged row, and rewrite() then deleted it for good.
                try:
                    value = loads_clean(data)
                except Exception:
                    dropped += 1
                    continue
                if not isinstance(value, dict):
                    dropped += 1
                    continue
                records.append(value)
            if dropped:
                log.warning(
                    "SQLiteBackend.read_all(%s): %d unreadable row(s) "
                    "excluded from the result (%s)",
                    collection, dropped, self._db_path)
            return records
        except sqlite3.Error as exc:
            # Raise, never return [] (adversarial r14, two seats, probed):
            # an empty list is indistinguishable from a verified-empty
            # store, so a transient lock/IO error during a repair's read
            # phase handed the caller a valid-looking [] and the next
            # rewrite deleted every healthy row. A read that did not
            # happen must not look like a store with nothing in it.
            log.error("SQLiteBackend.read_all(%s): read NOT performed "
                      "(%s): %s", collection, self._db_path, exc)
            raise

    def rewrite(self, collection: str, records: List[Dict[str, Any]]) -> None:
        # Doctrine parity with the JSONL twin (adversarial r13,
        # Minimalist, probed: DELETE-all + read_all's silent drop
        # permanently destroyed a damaged row on the standard
        # read -> transform -> rewrite composition). Emissions are proven
        # BEFORE the transaction; only rows the strict reader vouches for
        # are deleted — an unreadable `data` value stays in place,
        # verbatim, and is announced after the commit.
        from jsonl_utils import loads_clean, prove_record_line
        datas = [prove_record_line(r) for r in records]
        try:
            with self._connect() as con:
                cur = con.execute(
                    "SELECT id, data FROM memory_records WHERE collection=?",
                    (collection,))
                readable = []
                carried = 0
                for row_id, data in cur.fetchall():
                    try:
                        if isinstance(loads_clean(data), dict):
                            readable.append(row_id)
                        else:
                            carried += 1
                    except Exception:
                        carried += 1
                con.executemany(
                    "DELETE FROM memory_records WHERE id=?",
                    [(i,) for i in readable])
                con.executemany(
                    "INSERT INTO memory_records (collection, data) VALUES (?, ?)",
                    [(collection, d) for d in datas],
                )
                con.commit()
            if carried:
                log.warning(
                    "SQLiteBackend.rewrite(%s): %d unreadable row(s) "
                    "carried through the rewrite verbatim (%s)",
                    collection, carried, self._db_path)
        except sqlite3.Error as exc:
            log.error("SQLiteBackend.rewrite(%s): rewrite NOT performed, "
                      "store unchanged (%s): %s",
                      collection, self._db_path, exc)
            raise

    def transform(self, collection: str,
                  fn: "Any") -> List[Dict[str, Any]]:
        """Atomic read -> fn(records) -> rewrite in ONE write transaction.

        The JSONL twin's r13 transform never travelled here (adversarial
        r14, five seats, probed): a clean append between read_all() and
        rewrite() was reader-vouched by rewrite's own re-select, deleted,
        and never reinserted — the exact lost-update race transform
        exists to close. BEGIN IMMEDIATE takes the write lock BEFORE the
        read, so fn decides over the same rows the commit replaces; only
        the ids this transaction's own read vouched for are deleted, and
        every emission is proven through the reader's door first. Any
        failure — fn raising, a refused emission, an sqlite error — rolls
        back with the store untouched.
        """
        from jsonl_utils import loads_clean, prove_record_line
        try:
            con = self._connect()
            try:
                con.execute("BEGIN IMMEDIATE")
                cur = con.execute(
                    "SELECT id, data FROM memory_records WHERE "
                    "collection=? ORDER BY id ASC", (collection,))
                vouched, records, carried = [], [], 0
                for row_id, data in cur.fetchall():
                    try:
                        value = loads_clean(data)
                    except Exception:
                        carried += 1
                        continue
                    if not isinstance(value, dict):
                        carried += 1
                        continue
                    vouched.append(row_id)
                    records.append(value)
                out = fn(records)
                datas = [prove_record_line(r) for r in out]
                con.executemany(
                    "DELETE FROM memory_records WHERE id=?",
                    [(i,) for i in vouched])
                con.executemany(
                    "INSERT INTO memory_records (collection, data) "
                    "VALUES (?, ?)",
                    [(collection, d) for d in datas])
                con.commit()
            except BaseException:
                con.rollback()
                raise
            finally:
                con.close()
            # Announce AFTER the commit, same as the twins (r13, QA).
            if carried:
                log.warning(
                    "SQLiteBackend.transform(%s): %d unreadable row(s) "
                    "carried through the rewrite verbatim (%s)",
                    collection, carried, self._db_path)
            return out
        except sqlite3.Error as exc:
            log.error("SQLiteBackend.transform(%s): transform NOT "
                      "performed, store unchanged (%s): %s",
                      collection, self._db_path, exc)
            raise

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

    Reads MARO_MEMORY_BACKEND (default: "jsonl").
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

    backend_name = os.environ.get("MARO_MEMORY_BACKEND", "jsonl").lower().strip()

    if backend_name == "sqlite":
        db_path = memory_dir / "memory.db"
        log.debug("Using SQLiteBackend at %s", db_path)
        return SQLiteBackend(db_path)

    if backend_name != "jsonl":
        log.warning("Unknown MARO_MEMORY_BACKEND=%r, falling back to jsonl", backend_name)

    log.debug("Using JSONLBackend at %s", memory_dir)
    return JSONLBackend(memory_dir)
