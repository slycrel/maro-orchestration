"""Adapter-0: reference MemoryStore over one append-only JSONL event log.

This is BOTH the port's proof (contract tests run against it) and the
"our own, simple/straightforward" candidate in the memory bake-off — the
thing we'd grow if the third-party candidates lose. It deliberately mirrors
the house substrate idiom: JSONL on disk, locked appends, replay on load,
no third-party deps, no embeddings.

Event-sourced because "decay trust, never data" falls out for free: every
op is a new line (`item` / `link` / `invalidate`), state is a replay, and
history is the file itself. At current scale (single-digit MB across all
substrates) full replay on open is milliseconds; if that ever hurts, the
BM25/FTS5 index-as-cache from the decision brief goes in front — the port
surface doesn't change.

Retrieval is token-overlap ranking (same spirit as the TF-IDF retrieval the
live substrates use) weighted by trust. Good enough by design: the bake-off
scores candidates on retrieval quality, and this adapter is the baseline.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from file_lock import locked_append
from memory_port import (
    MIN_RECALL_TRUST, MemoryEdge, MemoryItem, visible_at,
)

log = logging.getLogger("maro.memory_jsonl")

_WORD_RE = re.compile(r"[a-z0-9]{2,}")
_STOPWORDS = frozenset(
    "the a an and or of to in for on with is are was were be this that it as "
    "at by from not do does did done".split()
)


def _tokens(text: str) -> frozenset:
    return frozenset(
        t for t in _WORD_RE.findall(text.lower()) if t not in _STOPWORDS
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class JsonlMemoryStore:
    """MemoryStore over `<root>/memory_events.jsonl`."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "memory_events.jsonl"
        self._items: Dict[str, MemoryItem] = {}
        self._edges: List[MemoryEdge] = []
        self._replay()

    # -- event log ----------------------------------------------------------

    def _replay(self) -> None:
        if not self.path.exists():
            return
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            log.warning("memory_jsonl: cannot read %s: %s", self.path, exc)
            return
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                self._apply(json.loads(line))
            except (ValueError, KeyError, TypeError) as exc:
                # One corrupt line loses one event, never the store.
                log.warning("memory_jsonl: skipping bad event: %s", exc)

    def _apply(self, ev: dict) -> None:
        op = ev.get("op")
        if op == "item":
            d = ev["item"]
            self._items[d["id"]] = MemoryItem(**d)
        elif op == "link":
            self._edges.append(MemoryEdge(**ev["edge"]))
        elif op == "invalidate":
            it = self._items.get(ev["id"])
            if it is not None:
                it.valid = False
                it.trust = min(it.trust, ev.get("trust", 0.0))
                it.invalid_reason = ev.get("reason", "")

    def _write(self, ev: dict) -> None:
        locked_append(self.path, json.dumps(ev, ensure_ascii=False))
        self._apply(ev)

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
            want = set(kinds) if kinds else None
            q = _tokens(query or "")
            scored: List[tuple] = []
            for it in self._items.values():
                if not it.valid or it.trust < MIN_RECALL_TRUST:
                    continue
                if want is not None and it.kind not in want:
                    continue
                if not visible_at(it.scope, scope):
                    continue
                overlap = len(q & _tokens(it.content)) if q else 0
                if q and not overlap:
                    continue
                scored.append((overlap * it.trust, it.created_at, it))
            scored.sort(key=lambda s: (s[0], s[1]), reverse=True)
            return [it for _, _, it in scored[: max(k, 0)]]
        except Exception as exc:
            log.warning("memory_jsonl: recall degraded to []: %s", exc)
            return []

    def get(self, item_id: str, *, include_invalid: bool = False) -> Optional[MemoryItem]:
        it = self._items.get(item_id)
        if it is None:
            return None
        if not include_invalid and not it.valid:
            return None
        return it

    def link(self, src_id: str, dst_id: str, rel: str) -> None:
        if src_id not in self._items or dst_id not in self._items:
            raise KeyError(f"link endpoints must exist: {src_id} -> {dst_id}")
        self._write({"op": "link", "edge": asdict(
            MemoryEdge(src=src_id, dst=dst_id, rel=rel, created_at=_now_iso())
        )})

    def neighbors(
        self, item_id: str, *, rel: Optional[str] = None, limit: int = 8,
    ) -> List[MemoryItem]:
        try:
            out: List[MemoryItem] = []
            for e in self._edges:
                if e.src != item_id or (rel is not None and e.rel != rel):
                    continue
                it = self._items.get(e.dst)
                if it is not None and it.valid:
                    out.append(it)
                if len(out) >= limit:
                    break
            return out
        except Exception as exc:
            log.warning("memory_jsonl: neighbors degraded to []: %s", exc)
            return []

    def invalidate(self, item_id: str, reason: str) -> None:
        if item_id not in self._items:
            raise KeyError(f"no such item: {item_id}")
        self._write({"op": "invalidate", "id": item_id,
                     "reason": reason, "trust": 0.0})

    def stats(self) -> Dict[str, Any]:
        return {
            "backend": "jsonl",
            "items": len(self._items),
            "valid": sum(1 for i in self._items.values() if i.valid),
            "edges": len(self._edges),
            "path": str(self.path),
        }
