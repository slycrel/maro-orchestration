"""MemoryStore port — the swappable-backend seam for orchestration memory.

Decision record: GOAL_BRAIN Decisions 2026-07-07 ("memory becomes a module;
consider pre-existing offerings before building our own"). Design rules, in
order: maintainability over cleverness; our crystallization engine stays
PRIMARY (trust, tiers, decay, promotion), any backend behind this port is
SECONDARY (storage + retrieval); unused backend features get ignored, not
wrapped.

The port is deliberately small — five verbs and two record types. It is the
surface a third-party adapter (TencentDB Agent Memory, Mem0, Graphiti, ...)
must cover to enter the bake-off, and the surface our own substrates hide
behind afterwards. Vendor-specific concepts (embeddings, persona pyramids,
temporal graphs) stay inside adapters.

Wiring plan (NOT yet done — gated on the bake-off verdict returning to
Jeremy): `recall.recall()` becomes a client of this port; until then the
production spine is untouched and the port's consumers are the contract
tests (tests/test_memory_port.py) and the bake-off harness.

Scope model ("orchestration all the way down", 2026-06-21 decree): a scope
is a slash path — "" is global, "thread/<id>" a thread, "thread/<id>/run/<id>"
a run. An item is visible at scope S when the item's scope is S or an
ancestor of S: a run reads its own scope plus every enclosing scope, and
never a sibling's. `visible_at()` is the single definition.

Trust model ("decay trust, never data"): `invalidate()` drops trust and
records why; nothing is ever deleted. Default recall hides invalidated
items; `get(..., include_invalid=True)` can always still see them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Protocol, runtime_checkable

# Item kinds mirror the existing substrates so adapter-0 and the live engine
# speak the same vocabulary. Adapters MUST preserve unknown kinds verbatim —
# the enum is descriptive, not a validation gate.
KNOWN_KINDS = (
    "lesson", "rule", "decision", "knowledge", "failure",
    "attempt", "playbook", "note",
)

# Below this trust an item is retrievable only via get(include_invalid=True).
# Matches the crystallization GC floor (knowledge_web.py) so the two brains
# agree on what "effectively gone" means.
MIN_RECALL_TRUST = 0.2


@dataclass
class MemoryItem:
    kind: str
    content: str
    scope: str = ""              # slash path; "" = global
    trust: float = 1.0           # 0..1; invalidation lowers, never deletes
    provenance: Dict[str, Any] = field(default_factory=dict)
    id: str = ""                 # assigned by the store on append
    created_at: str = ""         # ISO-8601 UTC, assigned by the store
    valid: bool = True
    invalid_reason: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MemoryEdge:
    src: str                     # item id
    dst: str                     # item id
    rel: str                     # e.g. supersedes | supports | contradicts | derived_from
    created_at: str = ""


def visible_at(item_scope: str, query_scope: str) -> bool:
    """True when an item at `item_scope` is readable from `query_scope`.

    Own scope + ancestors only: global ("") is visible everywhere; a thread
    item is visible to that thread and its runs; siblings never leak.
    """
    item_scope = item_scope.strip("/")
    query_scope = query_scope.strip("/")
    if not item_scope:
        return True
    if item_scope == query_scope:
        return True
    return query_scope.startswith(item_scope + "/")


@runtime_checkable
class MemoryStore(Protocol):
    """The five verbs. Adapters may raise on append/link/invalidate (writers
    should hear about broken storage); `recall` and `neighbors` must degrade
    to empty instead of raising — retrieval failures never take a loop down.
    """

    def append(self, item: MemoryItem) -> str:
        """Persist an item; returns its assigned id."""
        ...

    def recall(
        self,
        query: str,
        *,
        scope: str = "",
        kinds: Optional[Iterable[str]] = None,
        k: int = 8,
    ) -> List[MemoryItem]:
        """Top-k valid items relevant to `query`, visible from `scope`,
        ordered most-relevant first. Never raises."""
        ...

    def get(self, item_id: str, *, include_invalid: bool = False) -> Optional[MemoryItem]:
        ...

    def link(self, src_id: str, dst_id: str, rel: str) -> None:
        ...

    def neighbors(
        self, item_id: str, *, rel: Optional[str] = None, limit: int = 8,
    ) -> List[MemoryItem]:
        """Items linked from `item_id` (outgoing edges). Never raises; a
        backend with no graph support returns []."""
        ...

    def invalidate(self, item_id: str, reason: str) -> None:
        """Decay trust to below the recall floor and record why. The content
        must remain retrievable via get(include_invalid=True) forever."""
        ...

    def stats(self) -> Dict[str, Any]:
        """Cheap counters for observability: items, valid, edges, backend."""
        ...


def format_block(items: List[MemoryItem], *, header: str = "", max_chars: int = 1200) -> str:
    """Render recalled items as one injectable prompt block.

    The consumer shape the planner already expects (see RecallResult
    formatting in recall.py); kept here so every adapter yields identical
    injection text and the bake-off compares retrieval, not formatting.
    """
    if not items:
        return ""
    lines = [header] if header else []
    for it in items:
        lines.append(f"- [{it.kind}] {it.content}")
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars].rsplit("\n", 1)[0]
    return text
