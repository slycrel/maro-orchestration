"""Adapter-1: the production MemoryStore — stdlib sqlite3 + FTS5.

Verdict pedigree: docs/history/2026-07-07-memory-bakeoff.md (Jeremy:
"steal sounds good when we take the strengths we're looking for from all
3 and put them together"). The strengths, and who they're stolen from:

- Append-only JSONL event log as SOURCE OF TRUTH, SQLite as a rebuildable
  index (TencentDB) — same `memory_events.jsonl` format as memory_jsonl's
  adapter-0, so the two stores are interchangeable on disk and the index
  can always be rebuilt from the log (the dev-recall ghost-index lesson:
  indexes must detect stale sources or they rot invisibly).
- Bi-temporal validity (Graphiti): `created_at`/`expired_at` transaction
  time + `valid_at`/`invalid_at` event time columns; invalidate() is
  set-and-record, the event log keeps everything, default recall filters
  at query time.
- The event log doubles as Mem0's append-only history table: every
  mutation is a line; nothing is ever rewritten.
- `schema_meta` versioning (TencentDB's embedding_meta generalized):
  schema_version + ingested byte offset; version mismatch or a log that
  shrank/diverged triggers a full rebuild instead of silent wrongness.

Concurrency contract (the bar Mem0's embedded qdrant failed): WAL mode +
flock'd log appends; every public verb first catches the index up to the
log's current byte offset, so a store handle in one process sees writes
made by another process's handle. Multi-reader/multi-writer at
orchestrator scale (single-digit MB, forked workers) is exactly SQLite's
home turf.

Retrieval: FTS5 BM25 over content, rank = -bm25 * trust, filtered by
scope ancestry / kind / validity / trust floor in SQL. No embeddings —
the semantic lane (fastembed + sqlite-vec, ~150 lines) is gated on BM25
measuring insufficient on real recall traffic, not assumed.

Imports: stdlib + memory_port/file_lock only — keeps extraction a copy.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from file_lock import locked_append
from memory_port import MIN_RECALL_TRUST, MemoryItem, visible_at

log = logging.getLogger("maro.memory_sqlite")

SCHEMA_VERSION = 1

_TOKEN_RE = re.compile(r"[A-Za-z0-9]{2,}")

_DDL = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY, value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS items (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT '',
    trust REAL NOT NULL DEFAULT 1.0,
    valid INTEGER NOT NULL DEFAULT 1,
    invalid_reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT '',   -- transaction time: learned
    expired_at TEXT NOT NULL DEFAULT '',   -- transaction time: invalidated
    valid_at TEXT NOT NULL DEFAULT '',     -- event time: became true
    invalid_at TEXT NOT NULL DEFAULT '',   -- event time: stopped being true
    provenance TEXT NOT NULL DEFAULT '{}',
    meta TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_items_scope ON items(scope);
CREATE TABLE IF NOT EXISTS edges (
    src TEXT NOT NULL, dst TEXT NOT NULL, rel TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_edges_src ON edges(src);
CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
    content, id UNINDEXED
);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fts_query(text: str) -> str:
    """Sanitize free text into an OR-of-quoted-terms FTS5 MATCH query."""
    toks = _TOKEN_RE.findall(text or "")
    return " OR ".join(f'"{t}"' for t in toks[:32])


def _scope_ancestors(scope: str) -> List[str]:
    """["", "a", "a/b", ...] — every scope whose items are visible here."""
    scope = (scope or "").strip("/")
    out = [""]
    if scope:
        parts = scope.split("/")
        for i in range(1, len(parts) + 1):
            out.append("/".join(parts[:i]))
    return out


class SqliteMemoryStore:
    """MemoryStore over `<root>/memory_events.jsonl` + `<root>/index.db`."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.log_path = self.root / "memory_events.jsonl"
        self.db_path = self.root / "index.db"
        self._db = sqlite3.connect(self.db_path, timeout=30)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.executescript(_DDL)
        if self._meta("schema_version") != str(SCHEMA_VERSION):
            self._rebuild()
        self._catch_up()

    # -- index-as-cache maintenance ----------------------------------------

    def _meta(self, key: str) -> str:
        row = self._db.execute(
            "SELECT value FROM schema_meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else ""

    def _set_meta(self, key: str, value: str) -> None:
        self._db.execute(
            "INSERT INTO schema_meta(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value))

    def _rebuild(self) -> None:
        """Full re-index from the event log — the log is the truth."""
        self._db.executescript(
            "DELETE FROM items; DELETE FROM edges; DELETE FROM items_fts;")
        self._set_meta("schema_version", str(SCHEMA_VERSION))
        self._set_meta("log_offset", "0")
        self._db.commit()

    def _catch_up(self) -> None:
        """Apply log events past our offset. Shrunken log ⇒ full rebuild
        (a source that went backwards means the index is a ghost)."""
        size = self.log_path.stat().st_size if self.log_path.exists() else 0
        offset = int(self._meta("log_offset") or 0)
        if size < offset:
            self._rebuild()
            offset = 0
        if size == offset:
            return
        try:
            with open(self.log_path, "r", encoding="utf-8") as fh:
                fh.seek(offset)
                for line in fh:
                    line = line.strip()
                    if line:
                        try:
                            self._apply(json.loads(line))
                        except (ValueError, KeyError, TypeError) as exc:
                            log.warning("memory_sqlite: bad event skipped: %s", exc)
                new_offset = fh.tell()
        except OSError as exc:
            log.warning("memory_sqlite: cannot read log: %s", exc)
            return
        self._set_meta("log_offset", str(new_offset))
        self._db.commit()

    def _apply(self, ev: dict) -> None:
        op = ev.get("op")
        if op == "item":
            d = dict(ev["item"])
            self._db.execute(
                "INSERT OR REPLACE INTO items(id,kind,content,scope,trust,"
                "valid,invalid_reason,created_at,expired_at,valid_at,"
                "invalid_at,provenance,meta) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (d["id"], d["kind"], d["content"], d.get("scope", ""),
                 d.get("trust", 1.0), 1 if d.get("valid", True) else 0,
                 d.get("invalid_reason", ""), d.get("created_at", ""),
                 d.get("expired_at", ""), d.get("valid_at", ""),
                 d.get("invalid_at", ""),
                 json.dumps(d.get("provenance") or {}),
                 json.dumps(d.get("meta") or {})))
            self._db.execute("DELETE FROM items_fts WHERE id=?", (d["id"],))
            self._db.execute("INSERT INTO items_fts(content,id) VALUES(?,?)",
                             (d["content"], d["id"]))
        elif op == "link":
            e = ev["edge"]
            self._db.execute(
                "INSERT INTO edges(src,dst,rel,created_at) VALUES(?,?,?,?)",
                (e["src"], e["dst"], e["rel"], e.get("created_at", "")))
        elif op == "invalidate":
            self._db.execute(
                "UPDATE items SET valid=0, trust=MIN(trust, ?), "
                "invalid_reason=?, expired_at=?, invalid_at=? WHERE id=?",
                (ev.get("trust", 0.0), ev.get("reason", ""),
                 ev.get("expired_at", ""), ev.get("invalid_at", ""),
                 ev["id"]))

    def _write(self, ev: dict) -> None:
        locked_append(self.log_path, json.dumps(ev, ensure_ascii=False))
        # Catch-up replays from our recorded offset, so this picks up both
        # our event and anything another process appended before ours.
        self._catch_up()

    # -- MemoryStore verbs --------------------------------------------------

    def append(self, item: MemoryItem) -> str:
        item.id = item.id or uuid.uuid4().hex[:12]
        item.created_at = item.created_at or _now_iso()
        self._write({"op": "item", "item": asdict(item)})
        return item.id

    def recall(
        self,
        query: str,
        *,
        scope: str = "",
        kinds: Optional[Iterable[str]] = None,
        k: int = 8,
    ) -> List[MemoryItem]:
        try:
            self._catch_up()
            scopes = _scope_ancestors(scope)
            where = [f"i.scope IN ({','.join('?' * len(scopes))})",
                     "i.valid=1", "i.trust >= ?"]
            args: List[Any] = [*scopes, MIN_RECALL_TRUST]
            want = list(kinds) if kinds else []
            if want:
                where.append(f"i.kind IN ({','.join('?' * len(want))})")
                args.extend(want)
            fq = _fts_query(query)
            if fq:
                sql = ("SELECT i.*, bm25(items_fts) * -1 * i.trust AS score "
                       "FROM items_fts JOIN items i ON i.id = items_fts.id "
                       f"WHERE items_fts MATCH ? AND {' AND '.join(where)} "
                       "ORDER BY score DESC, i.created_at DESC LIMIT ?")
                rows = self._db.execute(sql, [fq, *args, max(k, 0)]).fetchall()
            else:
                sql = (f"SELECT i.*, 0 FROM items i WHERE {' AND '.join(where)} "
                       "ORDER BY i.created_at DESC LIMIT ?")
                rows = self._db.execute(sql, [*args, max(k, 0)]).fetchall()
            return [self._row_to_item(r) for r in rows]
        except Exception as exc:
            log.warning("memory_sqlite: recall degraded to []: %s", exc)
            return []

    def get(self, item_id: str, *, include_invalid: bool = False) -> Optional[MemoryItem]:
        self._catch_up()
        row = self._db.execute(
            "SELECT *, 0 FROM items WHERE id=?", (item_id,)).fetchone()
        if row is None:
            return None
        it = self._row_to_item(row)
        if not include_invalid and not it.valid:
            return None
        return it

    def link(self, src_id: str, dst_id: str, rel: str) -> None:
        self._catch_up()
        for x in (src_id, dst_id):
            if not self._db.execute(
                    "SELECT 1 FROM items WHERE id=?", (x,)).fetchone():
                raise KeyError(f"link endpoints must exist: {src_id} -> {dst_id}")
        self._write({"op": "link", "edge": {
            "src": src_id, "dst": dst_id, "rel": rel, "created_at": _now_iso()}})

    def neighbors(
        self, item_id: str, *, rel: Optional[str] = None, limit: int = 8,
    ) -> List[MemoryItem]:
        try:
            self._catch_up()
            sql = ("SELECT i.*, 0 FROM edges e JOIN items i ON i.id = e.dst "
                   "WHERE e.src=? AND i.valid=1")
            args: List[Any] = [item_id]
            if rel is not None:
                sql += " AND e.rel=?"
                args.append(rel)
            sql += " LIMIT ?"
            args.append(max(limit, 0))
            return [self._row_to_item(r) for r in self._db.execute(sql, args)]
        except Exception as exc:
            log.warning("memory_sqlite: neighbors degraded to []: %s", exc)
            return []

    def invalidate(self, item_id: str, reason: str) -> None:
        self._catch_up()
        if not self._db.execute(
                "SELECT 1 FROM items WHERE id=?", (item_id,)).fetchone():
            raise KeyError(f"no such item: {item_id}")
        now = _now_iso()
        self._write({"op": "invalidate", "id": item_id, "reason": reason,
                     "trust": 0.0, "expired_at": now, "invalid_at": now})

    def state_get(self, key: str) -> str:
        """Adapter-state KV (e.g. memory_bridge ingest offsets). Lives in
        schema_meta so bridge state never litters source directories;
        deliberately survives index rebuilds (source-consumption state is
        independent of index state)."""
        self._catch_up()
        return self._meta("state:" + key)

    def state_set(self, key: str, value: str) -> None:
        self._set_meta("state:" + key, value)
        self._db.commit()

    def stats(self) -> Dict[str, Any]:
        self._catch_up()
        items = self._db.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        valid = self._db.execute(
            "SELECT COUNT(*) FROM items WHERE valid=1").fetchone()[0]
        edges = self._db.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        return {"backend": "sqlite-fts5", "items": items, "valid": valid,
                "edges": edges, "path": str(self.log_path),
                "schema_version": int(self._meta("schema_version") or 0)}

    # -- helpers -------------------------------------------------------------

    def _row_to_item(self, row) -> MemoryItem:
        (iid, kind, content, scope, trust, valid, invalid_reason, created_at,
         expired_at, valid_at, invalid_at, provenance, meta, _score) = row
        return MemoryItem(
            id=iid, kind=kind, content=content, scope=scope, trust=trust,
            valid=bool(valid), invalid_reason=invalid_reason,
            created_at=created_at,
            provenance=json.loads(provenance or "{}"),
            meta=json.loads(meta or "{}"))
