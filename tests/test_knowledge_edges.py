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

    def test_expansion_int_one_enables(self, monkeypatch):
        # edge-review r1, Skeptic: the old `val is True` read YAML `1` as
        # disabled — the only killswitch in the tree with that asymmetry.
        import knowledge_web as kw
        import config
        monkeypatch.setattr(config, "get", lambda key, default=None: 1)
        assert kw._edge_expansion_enabled() is True

    def test_expansion_int_zero_disables(self, monkeypatch):
        import knowledge_web as kw
        import config
        monkeypatch.setattr(config, "get", lambda key, default=None: 0)
        assert kw._edge_expansion_enabled() is False

    def test_expansion_other_values_stay_off(self, monkeypatch):
        # Strict for an OFF-default behaviour-changing flag: arbitrary
        # truthy junk does not switch recall behaviour.
        import knowledge_web as kw
        import config
        for junk in (2, 3.5, ["true"], {"on": True}):
            monkeypatch.setattr(config, "get",
                                lambda key, default=None, _j=junk: _j)
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
        # max_nodes=2: small enough that the sibling can only render via the
        # edge (set-semantics: expansion counts only on membership change —
        # with the default 5 all four seeded nodes render in both arms).
        block = inject_knowledge_for_goal("frobnicate the widget",
                                          max_nodes=2)
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

    def test_char_budget_truncated_entrant_known_gap(self, tmp_workspace,
                                                     monkeypatch):
        """KNOWN GAP pin (edge-review r2, Expert QA): a boosted entrant can
        change query-level membership yet be truncated away by the char
        budget — no event fires and no [linked] renders, even though the
        entrant may have evicted a baseline node from the query slice. The
        undercount is the safe direction for the A/B denominator (events
        only claim renders that actually happened); this pin documents the
        accepted residual rather than closing it."""
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
        # max_chars small enough that only the first (seed) entry renders;
        # the boosted sibling enters the query top-2 but never the render.
        block = inject_knowledge_for_goal("frobnicate the widget",
                                          max_nodes=2, max_chars=120)
        assert "widget frobnication" in block
        assert "[linked]" not in block
        assert not [e for e in events
                    if e.get("event_type") == "KNOWLEDGE_EDGE_EXPANSION"]


# ---------------------------------------------------------------------------
# Set-semantics — expansion counts only when rendered MEMBERSHIP changes
# (edge-review r1, Architect HIGH: the old per-node boost stamped neighbours
# already inside the top-k, firing the A/B-denominator event on recalls whose
# rendered set was identical to text-only ranking)
# ---------------------------------------------------------------------------

def _bare_node(nid):
    from knowledge_web import KnowledgeNode
    return KnowledgeNode(node_id=nid, node_type="insight",
                         title=nid, description=nid)


class TestExpansionSetSemantics:
    def test_reorder_only_returns_text_ranking_verbatim(self, monkeypatch):
        """Neighbour already in the rendered slice: boost would only reorder
        or relabel — the ON arm must return exactly what OFF renders, with
        no via_edge_from stamp (so no event fires downstream)."""
        import knowledge_web as kw
        seed, nb = _bare_node("seed"), _bare_node("nb")
        monkeypatch.setattr(kw, "_coderivation_adjacency",
                            lambda: {"seed": [("nb", 0.9)],
                                     "nb": [("seed", 0.9)]})
        scored = [(1.0, seed), (0.2, nb)]  # both inside top-5 on their own
        out = kw._expand_scored_via_edges(list(scored), 5)
        assert [(s, n.node_id) for s, n in out] == [(1.0, "seed"), (0.2, "nb")]
        assert getattr(nb, "via_edge_from", None) is None

    def test_membership_change_marks_only_new_entrant(self, monkeypatch):
        import knowledge_web as kw
        seed, other, nb = _bare_node("seed"), _bare_node("other"), _bare_node("nb")
        monkeypatch.setattr(kw, "_coderivation_adjacency",
                            lambda: {"seed": [("nb", 0.9)]})
        scored = [(1.0, seed), (0.3, other), (0.0, nb)]
        out = kw._expand_scored_via_edges(list(scored), 2)
        # nb inherits 1.0 * 0.9 * 0.5 = 0.45 > other's 0.3 → enters top-2
        assert [n.node_id for _, n in out[:2]] == ["seed", "nb"]
        assert getattr(nb, "via_edge_from", None) == "seed"
        assert getattr(seed, "via_edge_from", None) is None
        assert getattr(other, "via_edge_from", None) is None

    def test_boosted_but_already_rendered_not_marked(self, monkeypatch):
        """When the set DOES change, a baseline member that also got a boost
        still carries no stamp — only genuine new entrants are 'expansion'."""
        import knowledge_web as kw
        seed = _bare_node("seed")
        weak = _bare_node("weak")
        filler = _bare_node("filler")
        nb = _bare_node("nb")
        monkeypatch.setattr(kw, "_coderivation_adjacency",
                            lambda: {"seed": [("weak", 0.9), ("nb", 0.85)]})
        scored = [(1.0, seed), (0.1, weak), (0.05, filler), (0.0, nb)]
        out = kw._expand_scored_via_edges(list(scored), 3)
        top = [n.node_id for _, n in out[:3]]
        assert top == ["seed", "weak", "nb"]  # nb displaced filler
        assert getattr(nb, "via_edge_from", None) == "seed"
        assert getattr(weak, "via_edge_from", None) is None  # was rendered anyway

    def test_expansion_body_crash_degrades_to_text_ranking(self, monkeypatch,
                                                           caplog):
        """recall.py wraps injection in a blanket swallow — a crash inside
        expansion must degrade to text-only ranking WITH a log line, never
        propagate (edge-review r1, Expert QA)."""
        import logging
        import knowledge_web as kw
        seed = _bare_node("seed")
        monkeypatch.setattr(kw, "_coderivation_adjacency",
                            lambda: {"seed": [("nb",)]})  # malformed pair
        scored = [(1.0, seed)]
        with caplog.at_level(logging.WARNING):
            out = kw._expand_scored_via_edges(list(scored), 5)
        assert out == scored
        assert any("edge expansion" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Forged-row hardening (edge-review r1, Expert QA HIGH: a single malformed
# weight in the shared edges file must not wedge the writer or silently
# no-op the reader — the string-typed-numeric class that hit lessons
# 2026-08-16)
# ---------------------------------------------------------------------------

def _forge_edge_row(tmp_workspace, weight):
    p = tmp_workspace / "memory" / "knowledge_edges.jsonl"
    row = {"source_id": "seed1", "target_id": "sib1",
           "relation": "co_derived", "weight": weight}
    with open(p, "a") as f:
        f.write(json.dumps(row) + "\n")


class TestForgedWeightRows:
    def test_loader_skips_and_warns_on_string_weight(self, tmp_workspace,
                                                     caplog):
        import logging
        from knowledge_web import load_knowledge_edges
        _forge_edge_row(tmp_workspace, "high")
        with caplog.at_level(logging.WARNING):
            edges = load_knowledge_edges()
        assert edges == []
        assert any("skipped" in r.message for r in caplog.records)

    def test_loader_skips_nan_weight(self, tmp_workspace):
        from knowledge_web import load_knowledge_edges
        _forge_edge_row(tmp_workspace, float("nan"))
        assert load_knowledge_edges() == []

    def test_loader_coerces_numeric_string_weight(self, tmp_workspace):
        from knowledge_web import load_knowledge_edges
        _forge_edge_row(tmp_workspace, "0.7")
        edges = load_knowledge_edges()
        assert len(edges) == 1
        assert edges[0].weight == pytest.approx(0.7)

    def test_writer_survives_forged_row(self, tmp_workspace):
        from knowledge_web import derive_coderivation_edges
        _forge_edge_row(tmp_workspace, "high")
        _mk_node("aaa", "alpha", "a", sources=["outcome:o1"])
        _mk_node("bbb", "beta", "b", sources=["outcome:o1"])
        stats = derive_coderivation_edges()
        assert stats["edges_appended"] == 1  # sweep did not wedge

    def test_reader_survives_forged_row(self, tmp_workspace):
        from knowledge_web import query_knowledge
        _seed_graph(tmp_workspace)
        _forge_edge_row(tmp_workspace, None)
        results = query_knowledge("frobnicate the widget",
                                  expand_edges=True, max_results=2)
        # The real derived edge still surfaces the sibling.
        assert "sib1" in [n.node_id for n in results]

    def test_loader_rejects_nonfinite_and_out_of_range_weight(
            self, tmp_workspace):
        # edge-review r2 (three lenses): inf parses "successfully" past a
        # NaN-only guard, then permanently freezes max-wins idempotency and
        # dominates every boost comparison. Range is the contract (0-1).
        from knowledge_web import load_knowledge_edges
        for w in (float("inf"), float("-inf"), 1e9, -50, 1.5, True, False):
            _forge_edge_row(tmp_workspace, w)
        assert load_knowledge_edges() == []

    def test_loader_rejects_malformed_endpoint_ids(self, tmp_workspace):
        # edge-review r2 (Expert QA HIGH): a null/non-string endpoint id
        # constructs fine and then TypeErrors inside sorted() in the
        # writer's snapshot — same wedge class as the weight bug.
        from knowledge_web import load_knowledge_edges
        p = tmp_workspace / "memory" / "knowledge_edges.jsonl"
        rows = [
            {"source_id": "a", "target_id": None,
             "relation": "co_derived", "weight": 0.5},
            {"source_id": ["a", "b"], "target_id": "c",
             "relation": "co_derived", "weight": 0.5},
            {"source_id": "", "target_id": "c",
             "relation": "co_derived", "weight": 0.5},
        ]
        with open(p, "a") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        assert load_knowledge_edges() == []

    def test_writer_survives_null_endpoint_row(self, tmp_workspace):
        from knowledge_web import derive_coderivation_edges
        p = tmp_workspace / "memory" / "knowledge_edges.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a") as f:
            f.write(json.dumps({"source_id": "x", "target_id": None,
                                "relation": "co_derived",
                                "weight": 0.5}) + "\n")
        _mk_node("aaa", "alpha", "a", sources=["outcome:o1"])
        _mk_node("bbb", "beta", "b", sources=["outcome:o1"])
        assert derive_coderivation_edges()["edges_appended"] == 1


# ---------------------------------------------------------------------------
# Maintenance-cadence wiring (edge-review r1, Expert QA: the production call
# path — mirrors test_skill_maintenance_wires_node_promotion)
# ---------------------------------------------------------------------------

class TestMaintenanceWiring:
    def test_maintenance_wires_edge_derivation(self, tmp_workspace,
                                               monkeypatch):
        import knowledge_web as kw
        from skill_lifecycle import run_skill_maintenance
        seen = {}

        def _capture(*, dry_run=False, **k):
            seen["dry_run"] = dry_run
            return {"pairs": 3, "edges_appended": 2, "edges_existing": 1}

        monkeypatch.setattr(kw, "derive_coderivation_edges", _capture)
        result = run_skill_maintenance(dry_run=False)
        assert seen.get("dry_run") is False
        assert result["knowledge_edges_derived"] == 2

    def test_maintenance_flag_off_skips_sweep(self, tmp_workspace,
                                              monkeypatch):
        import knowledge_web as kw
        from skill_lifecycle import run_skill_maintenance
        called = []
        monkeypatch.setattr(kw, "derive_coderivation_edges",
                            lambda **k: called.append(1) or {})
        monkeypatch.setattr(kw, "_edge_derivation_enabled", lambda: False)
        result = run_skill_maintenance(dry_run=False)
        assert called == []
        assert result["knowledge_edges_derived"] == 0

    def test_maintenance_survives_sweep_exception(self, tmp_workspace,
                                                  monkeypatch):
        import knowledge_web as kw
        from skill_lifecycle import run_skill_maintenance

        def _boom(**k):
            raise RuntimeError("poisoned store")

        monkeypatch.setattr(kw, "derive_coderivation_edges", _boom)
        result = run_skill_maintenance(dry_run=False)  # must not raise
        assert result["knowledge_edges_derived"] == 0


# ---------------------------------------------------------------------------
# CLI (edge-review r1, Architect: the one entry point with zero coverage)
# ---------------------------------------------------------------------------

class TestCli:
    def test_derive_edges_cli(self, tmp_workspace, capsys):
        from knowledge import main
        _mk_node("aaa", "alpha", "a", sources=["outcome:o1"])
        _mk_node("bbb", "beta", "b", sources=["outcome:o1"])
        main(["derive-edges"])
        out = capsys.readouterr().out
        assert "appended 1 edge row(s)" in out
        assert len(_edges(tmp_workspace)) == 1

    def test_derive_edges_cli_dry_run(self, tmp_workspace, capsys):
        from knowledge import main
        _mk_node("aaa", "alpha", "a", sources=["outcome:o1"])
        _mk_node("bbb", "beta", "b", sources=["outcome:o1"])
        main(["derive-edges", "--dry-run"])
        out = capsys.readouterr().out
        assert "would append 1 edge row(s)" in out
        assert _edges(tmp_workspace) == []


# ---------------------------------------------------------------------------
# Sweep scope — superseded nodes stay out (edge-review r1, Skeptic)
# ---------------------------------------------------------------------------

class TestSweepScope:
    def test_superseded_nodes_mint_no_edges(self, tmp_workspace):
        from knowledge_web import derive_coderivation_edges
        _mk_node("aaa", "alpha", "a", sources=["outcome:o1"],
                 status="superseded")
        _mk_node("bbb", "beta", "b", sources=["outcome:o1"])
        assert derive_coderivation_edges()["edges_appended"] == 0
