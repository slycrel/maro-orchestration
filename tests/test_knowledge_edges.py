"""Edge-traversal arc (2026-08-21): co-derivation edge derivation +
one-hop recall expansion.

Pre-arc measured state this chunk answers (BACKLOG "Knowledge edges are
minted but never traversed"): the only edges ever written were the one-time
lf- reference import (2124 rows, uniform relation/weight), zero first-party
edges existed, and load_knowledge_edges had zero callers — so recall could
never traverse anything. These tests pin the derived writer and the
flag-gated reader.
"""
from __future__ import annotations

import json

import pytest


@pytest.fixture()
def tmp_workspace(tmp_path, monkeypatch):
    """Redirect the workspace (and thus memory dir) to tmp_path."""
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    (tmp_path / "memory").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _mk_node(node_id, title, description, *, sources=(), status="active",
             confidence=0.5, node_type="insight"):
    from knowledge_web import KnowledgeNode, append_knowledge_node
    node = KnowledgeNode(
        node_id=node_id, node_type=node_type, title=title,
        description=description, sources=list(sources), status=status,
        confidence=confidence, author="test",
    )
    append_knowledge_node(node)
    return node


def _edges(tmp_workspace):
    p = tmp_workspace / "memory" / "knowledge_edges.jsonl"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


# ---------------------------------------------------------------------------
# derive_coderivation_edges — the first-party writer
# ---------------------------------------------------------------------------

class TestDeriveCoderivationEdges:
    def test_shared_outcome_mints_edge(self, tmp_workspace):
        from knowledge_web import derive_coderivation_edges
        _mk_node("aaa", "alpha", "a", sources=["outcome:o1"])
        _mk_node("bbb", "beta", "b", sources=["outcome:o1"])
        stats = derive_coderivation_edges()
        assert stats["edges_appended"] == 1
        rows = _edges(tmp_workspace)
        assert len(rows) == 1
        e = rows[0]
        assert e["relation"] == "co_derived"
        assert {e["source_id"], e["target_id"]} == {"aaa", "bbb"}
        assert e["weight"] == pytest.approx(0.5)  # 1 shared outcome

    def test_three_nodes_one_outcome_full_clique(self, tmp_workspace):
        from knowledge_web import derive_coderivation_edges
        for nid in ("aaa", "bbb", "ccc"):
            _mk_node(nid, nid, nid, sources=["outcome:o1"])
        stats = derive_coderivation_edges()
        assert stats["edges_appended"] == 3

    def test_repeat_coobservation_raises_weight(self, tmp_workspace):
        from knowledge_web import derive_coderivation_edges
        _mk_node("aaa", "alpha", "a", sources=["outcome:o1", "outcome:o2"])
        _mk_node("bbb", "beta", "b", sources=["outcome:o1", "outcome:o2"])
        derive_coderivation_edges()
        rows = _edges(tmp_workspace)
        assert len(rows) == 1
        assert rows[0]["weight"] == pytest.approx(0.7)  # 2 shared outcomes

    def test_idempotent_second_run_appends_nothing(self, tmp_workspace):
        from knowledge_web import derive_coderivation_edges
        _mk_node("aaa", "alpha", "a", sources=["outcome:o1"])
        _mk_node("bbb", "beta", "b", sources=["outcome:o1"])
        derive_coderivation_edges()
        stats = derive_coderivation_edges()
        assert stats["edges_appended"] == 0
        assert len(_edges(tmp_workspace)) == 1

    def test_weight_growth_appends_superseding_row_max_wins(
            self, tmp_workspace):
        """New shared outcome after the first sweep → a stronger row appends
        (append-only store; readers take max per pair)."""
        from knowledge_web import (derive_coderivation_edges,
                                   _coderivation_adjacency)
        na = _mk_node("aaa", "alpha", "a", sources=["outcome:o1"])
        _mk_node("bbb", "beta", "b", sources=["outcome:o1"])
        derive_coderivation_edges()
        # Second co-observation lands on the node rows (bridge upsert shape).
        p = tmp_workspace / "memory" / "knowledge_nodes.jsonl"
        rows = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
        for r in rows:
            r["sources"] = r["sources"] + ["outcome:o2"]
        p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")

        stats = derive_coderivation_edges()
        assert stats["edges_appended"] == 1
        assert len(_edges(tmp_workspace)) == 2  # append-only, no rewrite
        adj = _coderivation_adjacency()
        assert adj["aaa"] == [("bbb", pytest.approx(0.7))]

    def test_lf_reference_nodes_never_participate(self, tmp_workspace):
        from knowledge_web import derive_coderivation_edges
        _mk_node("aaa", "alpha", "a", sources=["outcome:o1"])
        _mk_node("lf-ref1", "ref", "r", sources=["outcome:o1"])
        stats = derive_coderivation_edges()
        assert stats["edges_appended"] == 0
        assert _edges(tmp_workspace) == []

    def test_candidate_nodes_participate(self, tmp_workspace):
        """Candidates mint edges too — the edge is provenance fact, and a
        later promotion should not have to re-derive it."""
        from knowledge_web import derive_coderivation_edges
        _mk_node("aaa", "alpha", "a", sources=["outcome:o1"],
                 status="candidate")
        _mk_node("bbb", "beta", "b", sources=["outcome:o1"],
                 status="candidate")
        assert derive_coderivation_edges()["edges_appended"] == 1

    def test_dry_run_writes_nothing(self, tmp_workspace):
        from knowledge_web import derive_coderivation_edges
        _mk_node("aaa", "alpha", "a", sources=["outcome:o1"])
        _mk_node("bbb", "beta", "b", sources=["outcome:o1"])
        stats = derive_coderivation_edges(dry_run=True)
        assert stats["edges_appended"] == 1
        assert _edges(tmp_workspace) == []

    def test_event_emitted_only_on_append(self, tmp_workspace, monkeypatch):
        import captains_log
        from knowledge_web import derive_coderivation_edges
        calls = []
        monkeypatch.setattr(
            captains_log, "log_event",
            lambda *a, **k: calls.append(k.get("event_type") or (a[0] if a else None)))
        _mk_node("aaa", "alpha", "a", sources=["outcome:o1"])
        _mk_node("bbb", "beta", "b", sources=["outcome:o1"])
        derive_coderivation_edges()
        assert calls == ["KNOWLEDGE_EDGES_DERIVED"]
        calls.clear()
        derive_coderivation_edges()  # converged — silent
        assert calls == []

    def test_no_outcome_sources_no_edges(self, tmp_workspace):
        from knowledge_web import derive_coderivation_edges
        _mk_node("aaa", "alpha", "a", sources=["https://example.com"])
        _mk_node("bbb", "beta", "b", sources=["https://example.com"])
        assert derive_coderivation_edges()["edges_appended"] == 0


# ---------------------------------------------------------------------------
# Killswitch coercion (the quoted-"false" class, chunk-5a review F1)
# ---------------------------------------------------------------------------

class TestFlagCoercion:
    def test_derivation_quoted_false_disables(self, monkeypatch):
        import knowledge_web as kw
        import config
        monkeypatch.setattr(config, "get",
                            lambda key, default=None: "false")
        assert kw._edge_derivation_enabled() is False

    def test_expansion_quoted_true_enables(self, monkeypatch):
        import knowledge_web as kw
        import config
        monkeypatch.setattr(config, "get",
                            lambda key, default=None: "true")
        assert kw._edge_expansion_enabled() is True

    def test_expansion_default_off(self, monkeypatch):
        import knowledge_web as kw
        import config
        monkeypatch.setattr(config, "get",
                            lambda key, default=None: default)
        assert kw._edge_expansion_enabled() is False


# ---------------------------------------------------------------------------
# query_knowledge one-hop expansion
# ---------------------------------------------------------------------------

def _seed_graph(tmp_workspace):
    """One lexical hit + one lexically-distant sibling, edge between them.

    The sibling is appended LAST behind two zero-score noise nodes so it can
    only reach a small top-k via the edge boost, never via zero-score tie
    order — the discriminator is real."""
    from knowledge_web import derive_coderivation_edges
    _mk_node("seed1", "widget frobnication procedure",
             "how to frobnicate widgets safely",
             sources=["outcome:o1"], confidence=0.8)
    _mk_node("noise", "unrelated cooking recipe",
             "how to bake bread", confidence=0.8)
    _mk_node("noise2", "unrelated gardening tips",
             "prune roses in spring", confidence=0.8)
    _mk_node("sib1", "gadget calibration baseline",
             "calibration numbers observed on the bench",
             sources=["outcome:o1"], confidence=0.8)
    derive_coderivation_edges()


class TestQueryExpansion:
    GOAL = "frobnicate the widget"

    def test_off_is_todays_behaviour(self, tmp_workspace):
        from knowledge_web import query_knowledge
        _seed_graph(tmp_workspace)
        results = query_knowledge(self.GOAL, expand_edges=False,
                                  max_results=2)
        ids = [n.node_id for n in results]
        assert ids[0] == "seed1"
        # The sibling shares no vocabulary with the goal — without edges it
        # cannot outrank anything on lexical score.
        assert not any(getattr(n, "via_edge_from", None) for n in results)

    def test_expansion_surfaces_sibling_below_seed(self, tmp_workspace):
        from knowledge_web import query_knowledge
        _seed_graph(tmp_workspace)
        results = query_knowledge(self.GOAL, expand_edges=True,
                                  max_results=2)
        ids = [n.node_id for n in results]
        assert ids[0] == "seed1"          # direct hit never displaced
        assert "sib1" in ids              # sibling surfaced via edge
        sib = results[ids.index("sib1")]
        assert getattr(sib, "via_edge_from", None) == "seed1"

    def test_expansion_never_widens_pool(self, tmp_workspace):
        """A candidate-status neighbour stays invisible: expansion respects
        the same filters as the base query."""
        from knowledge_web import derive_coderivation_edges, query_knowledge
        _mk_node("seed1", "widget frobnication procedure",
                 "how to frobnicate widgets", sources=["outcome:o1"],
                 confidence=0.8)
        _mk_node("hidden", "gadget calibration baseline",
                 "bench numbers", sources=["outcome:o1"],
                 confidence=0.8, status="candidate")
        derive_coderivation_edges()
        results = query_knowledge(self.GOAL, expand_edges=True,
                                  max_results=5)
        assert "hidden" not in [n.node_id for n in results]

    def test_no_edges_expansion_is_noop(self, tmp_workspace):
        from knowledge_web import query_knowledge
        _mk_node("seed1", "widget frobnication procedure",
                 "how to frobnicate widgets", confidence=0.8)
        on = query_knowledge(self.GOAL, expand_edges=True)
        off = query_knowledge(self.GOAL, expand_edges=False)
        assert [n.node_id for n in on] == [n.node_id for n in off]

    def test_config_default_drives_expansion(self, tmp_workspace,
                                             monkeypatch):
        import config
        from knowledge_web import query_knowledge
        _seed_graph(tmp_workspace)
        monkeypatch.setattr(
            config, "get",
            lambda key, default=None: (True if key == "knowledge.edge_expansion"
                                       else default))
        results = query_knowledge(self.GOAL, max_results=2)  # expand_edges=None
        assert "sib1" in [n.node_id for n in results]


# ---------------------------------------------------------------------------
# inject_knowledge_for_goal — marker + observability event
# ---------------------------------------------------------------------------

class TestInjectExpansion:
    def test_linked_marker_and_event(self, tmp_workspace, monkeypatch):
        import captains_log
        import config
        from knowledge_web import inject_knowledge_for_goal
        _seed_graph(tmp_workspace)
        monkeypatch.setattr(
            config, "get",
            lambda key, default=None: (True if key == "knowledge.edge_expansion"
                                       else default))
        events = []
        monkeypatch.setattr(captains_log, "log_event",
                            lambda **k: events.append(k))
        block = inject_knowledge_for_goal("frobnicate the widget")
        assert "[linked]" in block
        assert "gadget calibration baseline" in block
        exp = [e for e in events
               if e.get("event_type") == "KNOWLEDGE_EDGE_EXPANSION"]
        assert len(exp) == 1
        assert exp[0]["context"]["expanded"] == [
            {"node_id": "sib1", "seed_id": "seed1"}]

    def test_no_event_when_expansion_off(self, tmp_workspace, monkeypatch):
        import captains_log
        from knowledge_web import inject_knowledge_for_goal
        _seed_graph(tmp_workspace)
        events = []
        monkeypatch.setattr(captains_log, "log_event",
                            lambda **k: events.append(k))
        block = inject_knowledge_for_goal("frobnicate the widget")
        assert "[linked]" not in block
        assert not [e for e in events
                    if e.get("event_type") == "KNOWLEDGE_EDGE_EXPANSION"]
