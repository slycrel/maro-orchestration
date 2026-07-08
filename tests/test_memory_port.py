"""MemoryStore contract tests.

This suite IS the port's spec (GOAL_BRAIN 2026-07-07: the tests double as
"what we would ideally want to architect"). Every adapter — the JSONL
reference, and each third-party candidate in the bake-off — must pass the
whole suite via the `store_factory` param below. Adding a candidate to the
bake-off = adding one entry to ADAPTERS; nothing else changes.

Contract, in English:
  1. append/recall round-trips; relevance beats noise.
  2. Scope is hierarchical: own + ancestors visible, siblings never.
  3. Kind filters work; unknown kinds pass through verbatim.
  4. Invalidation hides an item from recall but NEVER deletes it
     (decay trust, never data).
  5. Links are queryable (graph read-side — the gap that killed
     knowledge_edges.jsonl was writes without reads).
  6. State survives reopen (persistence).
  7. Retrieval degrades to empty, it never raises.
"""

import pytest

from memory_jsonl import JsonlMemoryStore
from memory_port import MemoryItem, MemoryStore, format_block, visible_at


def _jsonl_factory(tmp_path):
    return lambda: JsonlMemoryStore(tmp_path / "mem")

# Bake-off hook: candidate adapters append (name, factory-builder) here.
ADAPTERS = [
    ("jsonl", _jsonl_factory),
]


@pytest.fixture(params=ADAPTERS, ids=[name for name, _ in ADAPTERS])
def store_factory(request, tmp_path):
    """Returns a zero-arg factory; calling it twice must open the SAME
    persistent store (reopen semantics for the persistence test)."""
    _, build = request.param
    return build(tmp_path)


@pytest.fixture
def store(store_factory):
    return store_factory()


def _add(store, content, *, kind="lesson", scope="", trust=1.0):
    return store.append(MemoryItem(kind=kind, content=content,
                                   scope=scope, trust=trust))


class TestRoundTrip:
    def test_append_assigns_id_and_recall_finds_it(self, store):
        iid = _add(store, "pytest tips over the TUI, use test-safe.sh")
        assert iid
        got = store.recall("pytest TUI")
        assert [i.id for i in got] == [iid]

    def test_relevance_beats_noise(self, store):
        _add(store, "polymarket ledger compounds across runs")
        target = _add(store, "navigator escalates doomed goals at blocked steps")
        top = store.recall("navigator blocked escalate")[0]
        assert top.id == target

    def test_get_returns_full_item(self, store):
        iid = _add(store, "fact", kind="decision", scope="thread/t1")
        it = store.get(iid)
        assert (it.kind, it.scope, it.content) == ("decision", "thread/t1", "fact")

    def test_unknown_kind_passes_through(self, store):
        iid = _add(store, "vendor-specific thing", kind="persona_l3")
        assert store.get(iid).kind == "persona_l3"


class TestScope:
    """A run reads its own scope plus every enclosing scope, never a
    sibling's — the 2026-06-21 'orchestration all the way down' decree."""

    def test_global_visible_everywhere(self, store):
        iid = _add(store, "global standing rule", scope="")
        assert iid in [i.id for i in store.recall("standing rule",
                                                  scope="thread/a/run/x")]

    def test_ancestor_visible_to_descendant(self, store):
        iid = _add(store, "thread level decision", scope="thread/a")
        assert iid in [i.id for i in store.recall("thread decision",
                                                  scope="thread/a/run/x")]

    def test_sibling_never_leaks(self, store):
        _add(store, "thread-b private lesson", scope="thread/b")
        assert store.recall("private lesson", scope="thread/a") == []

    def test_descendant_not_visible_to_ancestor(self, store):
        _add(store, "run-local scratch note", scope="thread/a/run/x")
        assert store.recall("scratch note", scope="thread/a") == []

    def test_visible_at_prefix_is_segment_wise(self):
        # "thread/ab" must not leak into "thread/abc" via string prefixing.
        assert not visible_at("thread/ab", "thread/abc")


class TestKinds:
    def test_kind_filter(self, store):
        _add(store, "a lesson about retries", kind="lesson")
        rid = _add(store, "a rule about retries", kind="rule")
        got = store.recall("retries", kinds=["rule"])
        assert [i.id for i in got] == [rid]


class TestInvalidation:
    """Decay trust, never data."""

    def test_invalidated_hidden_from_recall(self, store):
        iid = _add(store, "obsolete claim about sqlite-vec embeddings")
        store.invalidate(iid, "docstring lie — embeddings never existed")
        assert store.recall("sqlite-vec embeddings") == []

    def test_invalidated_hidden_from_default_get(self, store):
        iid = _add(store, "obsolete")
        store.invalidate(iid, "superseded")
        assert store.get(iid) is None

    def test_content_never_deleted(self, store):
        iid = _add(store, "wrong but historically important")
        store.invalidate(iid, "proven wrong 2026-07-07")
        it = store.get(iid, include_invalid=True)
        assert it is not None
        assert it.content == "wrong but historically important"
        assert it.invalid_reason == "proven wrong 2026-07-07"
        assert it.valid is False

    def test_invalidate_missing_item_raises(self, store):
        with pytest.raises(Exception):
            store.invalidate("nope", "reason")


class TestLinks:
    def test_link_and_neighbors(self, store):
        a = _add(store, "new fence policy", kind="rule")
        b = _add(store, "old fence policy", kind="rule")
        store.link(a, b, "supersedes")
        assert [i.id for i in store.neighbors(a)] == [b]

    def test_rel_filter(self, store):
        a = _add(store, "claim")
        b = _add(store, "evidence for claim")
        c = _add(store, "counter-evidence")
        store.link(a, b, "supports")
        store.link(a, c, "contradicts")
        assert [i.id for i in store.neighbors(a, rel="contradicts")] == [c]

    def test_neighbors_of_unlinked_item_empty(self, store):
        a = _add(store, "loner")
        assert store.neighbors(a) == []

    def test_invalid_neighbor_hidden(self, store):
        a = _add(store, "root")
        b = _add(store, "retracted leaf")
        store.link(a, b, "supports")
        store.invalidate(b, "retracted")
        assert store.neighbors(a) == []


class TestPersistence:
    def test_state_survives_reopen(self, store_factory):
        s1 = store_factory()
        a = _add(s1, "durable lesson", kind="lesson")
        b = _add(s1, "durable rule", kind="rule", scope="thread/t")
        s1.link(a, b, "derived_from")
        s1.invalidate(b, "demoted")

        s2 = store_factory()
        assert s2.get(a).content == "durable lesson"
        assert s2.get(b) is None
        assert s2.get(b, include_invalid=True).invalid_reason == "demoted"
        assert s2.stats()["items"] == 2


class TestDegradation:
    """Retrieval never takes a loop down."""

    def test_empty_store_recalls_empty(self, store):
        assert store.recall("anything") == []

    def test_weird_inputs_do_not_raise(self, store):
        _add(store, "something")
        for q in ("", "   ", "🦆" * 500, "a\x00b"):
            assert isinstance(store.recall(q), list)
        assert isinstance(store.recall("x", scope="////", k=-1), list)

    def test_protocol_conformance(self, store):
        assert isinstance(store, MemoryStore)


class TestFormatBlock:
    def test_renders_and_caps(self):
        items = [MemoryItem(kind="lesson", content=f"lesson {i} " + "x" * 80)
                 for i in range(30)]
        block = format_block(items, header="== Recall ==", max_chars=500)
        assert block.startswith("== Recall ==")
        assert len(block) <= 500
        assert block.endswith("x")  # cap cuts on a line boundary, not mid-line

    def test_empty_items_empty_block(self):
        assert format_block([], header="== Recall ==") == ""
