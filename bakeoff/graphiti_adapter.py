"""Graphiti-backed MemoryStore adapter — bake-off candidate (sandboxed trial).

Backend: graphiti-core 0.29.2 over falkordblite (embedded FalkorDB — a
forked redis-server child over a unix socket, no daemon install).

Zero-LLM by construction:
  * Items are written with the direct ``EntityNode.save(driver)`` API and
    links with ``EntityEdge.save(driver)`` — never ``add_episode`` (LLM
    extraction) and never the resolve/dedupe paths (LLM calls).
  * recall() is BM25-only: graphiti's ``node_fulltext_search`` →
    ``db.idx.fulltext.queryNodes`` (RediSearch inside FalkorDB). No
    embedder is ever constructed; ``name_embedding`` stays None
    (FalkorDB's save query tolerates ``vecf32(null)``).

Mapping:
  MemoryItem -> EntityNode(:MemoryItem:Entity)
      name           = content        (covered by the Entity fulltext index)
      attributes     = kind / scope / trust / valid / invalid_reason /
                       provenance (JSON str) / meta (JSON str) [/ expired_at]
      (FalkorDB flattens attributes onto node properties on save and
      graphiti's record parser reassembles them on read.)
  link(a, b, rel) -> EntityEdge as [:RELATES_TO {name: rel, fact: rel}]
  invalidate      = idiomatic set-and-resave: valid=False, trust decayed to
                    0.05 (< MIN_RECALL_TRUST), reason + expired_at recorded.
                    Nothing is ever deleted.

Scope: group_id has a charset restriction (no slashes), so the slash-path
scope lives in attributes and hierarchy filtering (``visible_at``) is done
adapter-side, on an over-fetched BM25 candidate list. group_id is a constant.

Known upstream wart routed around here: for an empty/whitespace query,
``FalkorDriver.build_fulltext_query`` returns ``' ()'`` instead of ``''``,
which is a RediSearch syntax error — node_fulltext_search raises instead of
returning []. recall() pre-guards (and is try/except-wrapped anyway, per the
"retrieval never raises" contract).

Concurrency model: graphiti is async-only; the port is sync. One module-owned
event loop runs every verb (asyncio.run per verb would rebind falkordblite's
loop-bound connections). AsyncFalkorDB instances are cached per dbfilename —
a second embedded instance on the same file would fight the first.
"""

from __future__ import annotations

import asyncio
import atexit
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

os.environ.setdefault("GRAPHITI_TELEMETRY_ENABLED", "false")
os.environ["GRAPHITI_TELEMETRY_ENABLED"] = "false"

from redislite.async_falkordb_client import AsyncFalkorDB  # noqa: E402

from graphiti_core.driver.falkordb_driver import FalkorDriver  # noqa: E402
from graphiti_core.edges import EntityEdge  # noqa: E402
from graphiti_core.nodes import EntityNode  # noqa: E402
from graphiti_core.search.search_filters import SearchFilters  # noqa: E402
from graphiti_core.search.search_utils import node_fulltext_search  # noqa: E402
from graphiti_core.utils.datetime_utils import utc_now  # noqa: E402

from memory_port import MIN_RECALL_TRUST, MemoryItem, visible_at  # noqa: E402

GROUP_ID = "maro"  # group_id charset forbids slashes; scope lives in attributes
ITEM_LABEL = "MemoryItem"

# --- module-level caches -----------------------------------------------------
# One event loop owns all graphiti/falkordblite awaits for the process.
_LOOP: Optional[asyncio.AbstractEventLoop] = None
# One embedded DB per dbfilename (a second instance on the same file fights
# the first), one driver per (dbfilename, graph).
_DBS: Dict[str, AsyncFalkorDB] = {}
_DRIVERS: Dict[tuple, FalkorDriver] = {}
_INDEXED: set = set()


def _loop() -> asyncio.AbstractEventLoop:
    global _LOOP
    if _LOOP is None or _LOOP.is_closed():
        _LOOP = asyncio.new_event_loop()
    return _LOOP


def _run(coro):
    return _loop().run_until_complete(coro)


def _shutdown_all() -> None:
    """Kill the embedded redis children at interpreter exit.

    UPSTREAM BUG (falkordblite 0.10.0, found in this trial): the async
    wrapper's ``close()`` sets ``_sync_client._async_managed = True`` and then
    calls ``_cleanup()`` — whose first check is ``if _async_managed: return``
    ("let the async wrapper handle shutdown"). The shutdown is therefore
    unreachable from the async API, and every AsyncFalkorDB leaks an orphaned
    redis-server daemon (reparented to PID 1, ~24 MB RSS each). We reach into
    redislite internals to run the real shutdown path ourselves.
    """
    import signal
    import time

    def _alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    for db in list(_DBS.values()):
        try:
            _run(db.client._client.aclose())  # drop our async connection pool
        except Exception:
            pass
        try:
            sync_client = db.client._sync_client
            pid = int(sync_client.pid or 0)
        except Exception:
            continue
        # Not sync_client._cleanup(): that path is double-gated (the
        # _async_managed early-return AND a _connection_count() <= 1 check
        # that the async pool's lingering connections fail), so it silently
        # skips shutdown. Send SHUTDOWN SAVE ourselves and escalate.
        try:
            sync_client._async_managed = False
            sync_client.shutdown(save=True, now=True, force=True)
        except Exception:
            pass  # server closes the connection mid-reply; expected
        if pid:
            for _ in range(50):
                if not _alive(pid):
                    break
                time.sleep(0.1)
            if _alive(pid):
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
    _DBS.clear()
    _DRIVERS.clear()
    _INDEXED.clear()


atexit.register(_shutdown_all)


def _driver(db_path: str, graph: str) -> FalkorDriver:
    key = (db_path, graph)
    if key not in _DRIVERS:
        if db_path not in _DBS:
            _DBS[db_path] = AsyncFalkorDB(dbfilename=db_path)
        _DRIVERS[key] = FalkorDriver(falkor_db=_DBS[db_path], database=graph)
    drv = _DRIVERS[key]
    if key not in _INDEXED:
        # Idempotent: the driver swallows "already indexed" errors.
        _run(drv.build_indices_and_constraints())
        _INDEXED.add(key)
    return drv


# --- row <-> item ------------------------------------------------------------

_ITEM_RETURN = (
    "n.uuid AS uuid, n.name AS name, n.created_at AS created_at, "
    "n.kind AS kind, n.scope AS scope, n.trust AS trust, n.valid AS valid, "
    "n.invalid_reason AS invalid_reason, n.provenance AS provenance, "
    "n.meta AS meta"
)


def _json_or(default, raw):
    try:
        return json.loads(raw) if raw else default
    except (TypeError, ValueError):
        return default


def _item_from_row(row: Dict[str, Any]) -> MemoryItem:
    return MemoryItem(
        kind=row.get("kind") or "",
        content=row.get("name") or "",
        scope=row.get("scope") or "",
        trust=float(row.get("trust") if row.get("trust") is not None else 1.0),
        provenance=_json_or({}, row.get("provenance")),
        id=row.get("uuid") or "",
        created_at=row.get("created_at") or "",
        valid=bool(row.get("valid", True)),
        invalid_reason=row.get("invalid_reason") or "",
        meta=_json_or({}, row.get("meta")),
    )


def _item_from_node(node: EntityNode) -> MemoryItem:
    at = node.attributes or {}
    created = node.created_at
    if isinstance(created, datetime):
        created = created.isoformat()
    return MemoryItem(
        kind=at.get("kind") or "",
        content=node.name,
        scope=at.get("scope") or "",
        trust=float(at.get("trust", 1.0)),
        provenance=_json_or({}, at.get("provenance")),
        id=node.uuid,
        created_at=created or "",
        valid=bool(at.get("valid", True)),
        invalid_reason=at.get("invalid_reason") or "",
        meta=_json_or({}, at.get("meta")),
    )


def _recallable(item: MemoryItem) -> bool:
    return item.valid and item.trust >= MIN_RECALL_TRUST


class GraphitiMemoryStore:
    """Sync MemoryStore over graphiti-core's async direct-save APIs."""

    def __init__(self, db_path, graph: str = "memory"):
        self._db_path = str(db_path)
        self._graph = graph
        self._drv = _driver(self._db_path, graph)

    # -- write side (raises loudly) -------------------------------------

    def append(self, item: MemoryItem) -> str:
        node = EntityNode(
            name=item.content,
            group_id=GROUP_ID,
            labels=[ITEM_LABEL],
            summary="",
            created_at=utc_now(),
            attributes={
                "kind": item.kind,
                "scope": (item.scope or "").strip("/"),
                "trust": float(item.trust),
                "valid": bool(item.valid),
                "invalid_reason": item.invalid_reason or "",
                "provenance": json.dumps(item.provenance or {}),
                "meta": json.dumps(item.meta or {}),
            },
        )
        _run(node.save(self._drv))
        item.id = node.uuid
        item.created_at = node.created_at.isoformat()
        return node.uuid

    def link(self, src_id: str, dst_id: str, rel: str) -> None:
        rows, _, _ = _run(self._drv.execute_query(
            "MATCH (n:Entity) WHERE n.uuid IN [$a, $b] RETURN count(n) AS c",
            a=src_id, b=dst_id))
        if not rows or rows[0]["c"] != 2:
            raise KeyError(f"link: unknown item id(s): {src_id!r} -> {dst_id!r}")
        edge = EntityEdge(
            source_node_uuid=src_id,
            target_node_uuid=dst_id,
            name=rel,
            fact=rel,
            group_id=GROUP_ID,
            created_at=utc_now(),
        )
        _run(edge.save(self._drv))

    def invalidate(self, item_id: str, reason: str) -> None:
        rows, _, _ = _run(self._drv.execute_query(
            f"MATCH (n:{ITEM_LABEL} {{uuid: $u}}) RETURN {_ITEM_RETURN}",
            u=item_id))
        if not rows:
            raise KeyError(f"invalidate: unknown item id {item_id!r}")
        old = _item_from_row(rows[0])
        # Idiomatic graphiti invalidation: set-and-resave with an expiry
        # timestamp; trust decays below the recall floor, data stays forever.
        node = EntityNode(
            uuid=old.id,
            name=old.content,
            group_id=GROUP_ID,
            labels=[ITEM_LABEL],
            summary="",
            created_at=datetime.fromisoformat(old.created_at)
            if old.created_at else utc_now(),
            attributes={
                "kind": old.kind,
                "scope": old.scope,
                "trust": min(old.trust, 0.05),
                "valid": False,
                "invalid_reason": reason,
                "provenance": json.dumps(old.provenance or {}),
                "meta": json.dumps(old.meta or {}),
                "expired_at": utc_now().isoformat(),
                "invalid_at": utc_now().isoformat(),
            },
        )
        _run(node.save(self._drv))

    # -- read side (degrades to empty / None) ---------------------------

    def get(self, item_id: str, *, include_invalid: bool = False) -> Optional[MemoryItem]:
        try:
            rows, _, _ = _run(self._drv.execute_query(
                f"MATCH (n:{ITEM_LABEL} {{uuid: $u}}) RETURN {_ITEM_RETURN}",
                u=item_id))
        except Exception:
            return None
        if not rows:
            return None
        item = _item_from_row(rows[0])
        if not include_invalid and not _recallable(item):
            return None
        return item

    def recall(
        self,
        query: str,
        *,
        scope: str = "",
        kinds: Optional[Iterable[str]] = None,
        k: int = 8,
    ) -> List[MemoryItem]:
        try:
            k = max(0, int(k))
            if k == 0:
                return []
            # Upstream wart: empty/whitespace queries build the fulltext
            # query ' ()', a RediSearch syntax error. Pre-guard.
            fq = self._drv.build_fulltext_query(query or "", None)
            if not fq or fq.strip() == "()":
                return []
            kindset = set(kinds) if kinds is not None else None
            candidates = _run(node_fulltext_search(
                self._drv, query, SearchFilters(), None,
                limit=min(max(k * 8, 64), 512)))
            out: List[MemoryItem] = []
            for node in candidates:  # already BM25-score-ordered
                item = _item_from_node(node)
                if not _recallable(item):
                    continue
                if not visible_at(item.scope, scope):
                    continue
                if kindset is not None and item.kind not in kindset:
                    continue
                out.append(item)
                if len(out) == k:
                    break
            return out
        except Exception:
            return []

    def neighbors(
        self, item_id: str, *, rel: Optional[str] = None, limit: int = 8,
    ) -> List[MemoryItem]:
        try:
            limit = max(0, int(limit))
            if limit == 0:
                return []
            rel_clause = "AND r.name = $rel " if rel is not None else ""
            rows, _, _ = _run(self._drv.execute_query(
                f"MATCH (a:{ITEM_LABEL} {{uuid: $u}})-[r:RELATES_TO]->(b:{ITEM_LABEL}) "
                f"WHERE true {rel_clause}"
                f"RETURN {_ITEM_RETURN.replace('n.', 'b.')}, r.created_at AS _ec "
                "ORDER BY _ec",
                u=item_id, rel=rel))
            out = []
            for row in rows or []:
                item = _item_from_row(row)
                if _recallable(item):
                    out.append(item)
                if len(out) == limit:
                    break
            return out
        except Exception:
            return []

    def stats(self) -> Dict[str, Any]:
        try:
            rows, _, _ = _run(self._drv.execute_query(
                f"MATCH (n:{ITEM_LABEL}) "
                "RETURN count(n) AS items, "
                "sum(CASE WHEN n.valid AND n.trust >= $floor THEN 1 ELSE 0 END) AS valid",
                floor=MIN_RECALL_TRUST))
            erows, _, _ = _run(self._drv.execute_query(
                f"MATCH (:{ITEM_LABEL})-[r:RELATES_TO]->(:{ITEM_LABEL}) "
                "RETURN count(r) AS edges"))
            return {
                "items": int(rows[0]["items"]) if rows else 0,
                "valid": int(rows[0]["valid"] or 0) if rows else 0,
                "edges": int(erows[0]["edges"]) if erows else 0,
                "backend": "graphiti-core/falkordblite",
            }
        except Exception:
            return {"items": 0, "valid": 0, "edges": 0,
                    "backend": "graphiti-core/falkordblite"}


def contract_factory(tmp_path):
    """(tmp_path) -> zero-arg factory; both calls open the SAME store."""
    db_path = str(tmp_path / "graphiti_mem.db")
    return lambda: GraphitiMemoryStore(db_path)
