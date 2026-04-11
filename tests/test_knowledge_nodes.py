"""Tests for Phase K2: Knowledge Nodes — structured queryable knowledge."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from knowledge_web import (
    KnowledgeNode,
    KnowledgeEdge,
    NODE_TYPES,
    NODE_ACTIVE,
    NODE_SUPERSEDED,
    NODE_CANDIDATE,
    append_knowledge_node,
    append_knowledge_edge,
    load_knowledge_nodes,
    load_knowledge_edges,
    find_knowledge_node,
    query_knowledge,
    inject_knowledge_for_goal,
    extract_wiki_links,
    build_wiki_link_edges,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolate_workspace(monkeypatch, tmp_path):
    """Point all memory paths to a temp dir."""
    monkeypatch.setenv("POE_MEMORY_DIR", str(tmp_path / "memory"))
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    (tmp_path / "memory").mkdir(parents=True, exist_ok=True)


def _make_node(node_id="n001", title="Test Principle", node_type="principle",
               description="A test knowledge node", domain="testing",
               confidence=0.8, tags=None, sources=None, status=NODE_ACTIVE):
    return KnowledgeNode(
        node_id=node_id,
        node_type=node_type,
        title=title,
        description=description,
        domain=domain,
        confidence=confidence,
        tags=tags or [],
        sources=sources or [],
        status=status,
    )


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------

class TestKnowledgeNodeSchema:

    def test_node_types_are_strings(self):
        assert all(isinstance(t, str) for t in NODE_TYPES)
        assert "principle" in NODE_TYPES
        assert "pattern" in NODE_TYPES
        assert "concept" in NODE_TYPES

    def test_node_defaults(self):
        node = KnowledgeNode(node_id="x", node_type="principle", title="t", description="d")
        assert node.status == NODE_ACTIVE
        assert node.confidence == 0.5
        assert node.times_applied == 0
        assert node.sources == []
        assert node.tags == []

    def test_edge_defaults(self):
        edge = KnowledgeEdge(source_id="a", target_id="b", relation="supports")
        assert edge.weight == 1.0


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

class TestKnowledgeStorage:

    def test_append_and_load_node(self):
        node = _make_node()
        append_knowledge_node(node)
        loaded = load_knowledge_nodes()
        assert len(loaded) == 1
        assert loaded[0].node_id == "n001"
        assert loaded[0].title == "Test Principle"

    def test_append_and_load_edge(self):
        edge = KnowledgeEdge(source_id="a", target_id="b", relation="supports")
        append_knowledge_edge(edge)
        loaded = load_knowledge_edges()
        assert len(loaded) == 1
        assert loaded[0].relation == "supports"

    def test_load_nodes_filters_by_type(self):
        append_knowledge_node(_make_node(node_id="n1", node_type="principle"))
        append_knowledge_node(_make_node(node_id="n2", node_type="pattern"))
        principles = load_knowledge_nodes(node_type="principle")
        assert len(principles) == 1
        assert principles[0].node_id == "n1"

    def test_load_nodes_filters_by_domain(self):
        append_knowledge_node(_make_node(node_id="n1", domain="memory"))
        append_knowledge_node(_make_node(node_id="n2", domain="quality"))
        memory = load_knowledge_nodes(domain="memory")
        assert len(memory) == 1
        assert memory[0].node_id == "n1"

    def test_load_nodes_filters_by_status(self):
        append_knowledge_node(_make_node(node_id="n1", status=NODE_ACTIVE))
        append_knowledge_node(_make_node(node_id="n2", status=NODE_SUPERSEDED))
        active = load_knowledge_nodes(status=NODE_ACTIVE)
        assert len(active) == 1
        assert active[0].node_id == "n1"

    def test_load_nodes_filters_by_tag(self):
        append_knowledge_node(_make_node(node_id="n1", tags=["harness", "design"]))
        append_knowledge_node(_make_node(node_id="n2", tags=["cost", "budget"]))
        harness = load_knowledge_nodes(tag="harness")
        assert len(harness) == 1
        assert harness[0].node_id == "n1"

    def test_find_node_by_id(self):
        append_knowledge_node(_make_node(node_id="find-me"))
        node = find_knowledge_node("find-me")
        assert node is not None
        assert node.node_id == "find-me"

    def test_find_node_missing(self):
        assert find_knowledge_node("nonexistent") is None

    def test_load_edges_filters_by_node(self):
        append_knowledge_edge(KnowledgeEdge(source_id="a", target_id="b", relation="supports"))
        append_knowledge_edge(KnowledgeEdge(source_id="c", target_id="d", relation="related"))
        edges = load_knowledge_edges(node_id="a")
        assert len(edges) == 1
        assert edges[0].source_id == "a"

    def test_empty_store_returns_empty(self):
        assert load_knowledge_nodes() == []
        assert load_knowledge_edges() == []


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------

class TestKnowledgeQuery:

    def test_query_by_relevance(self):
        append_knowledge_node(_make_node(
            node_id="n1", title="Decomposition Strategy",
            description="Break goals into atomic steps for reliable execution",
        ))
        append_knowledge_node(_make_node(
            node_id="n2", title="Cost Tracking",
            description="Track token costs per model per step for budget management",
        ))
        results = query_knowledge("how to break down a complex goal into steps")
        assert len(results) >= 1
        assert results[0].node_id == "n1"  # more relevant

    def test_query_filters_by_confidence(self):
        append_knowledge_node(_make_node(node_id="n1", confidence=0.9))
        append_knowledge_node(_make_node(node_id="n2", confidence=0.1))
        results = query_knowledge("test", min_confidence=0.5)
        assert len(results) == 1
        assert results[0].node_id == "n1"

    def test_query_empty_goal(self):
        append_knowledge_node(_make_node())
        results = query_knowledge("")
        assert isinstance(results, list)

    def test_query_respects_max_results(self):
        for i in range(10):
            append_knowledge_node(_make_node(
                node_id=f"n{i}", title=f"Knowledge item {i}",
                description="relevant knowledge about orchestration",
            ))
        results = query_knowledge("orchestration", max_results=3)
        assert len(results) <= 3


# ---------------------------------------------------------------------------
# Injection
# ---------------------------------------------------------------------------

class TestKnowledgeInjection:

    def test_inject_returns_formatted_string(self):
        append_knowledge_node(_make_node(
            title="Verification First",
            description="Always verify outputs before recording as done",
            confidence=0.9,
        ))
        result = inject_knowledge_for_goal("verify the build output")
        assert "## Relevant Knowledge" in result
        assert "Verification First" in result

    def test_inject_empty_store(self):
        result = inject_knowledge_for_goal("anything")
        assert result == ""

    def test_inject_respects_max_chars(self):
        for i in range(20):
            append_knowledge_node(_make_node(
                node_id=f"n{i}", title=f"Principle {i}",
                description="A" * 200, confidence=0.9,
            ))
        result = inject_knowledge_for_goal("test", max_chars=500)
        assert len(result) <= 600  # some header overhead


# ---------------------------------------------------------------------------
# Wiki-link extraction
# ---------------------------------------------------------------------------

class TestWikiLinks:

    def test_extract_wiki_links(self):
        text = "This relates to [[core-loop]] and [[memory-system]] design."
        links = extract_wiki_links(text)
        assert links == ["core-loop", "memory-system"]

    def test_extract_no_links(self):
        assert extract_wiki_links("no links here") == []

    def test_build_edges_from_wiki_links(self):
        nodes = [
            _make_node(node_id="n1", title="Core Loop",
                       description="The main loop, related to [[memory-system]]"),
            _make_node(node_id="n2", title="Memory System",
                       description="Stores outcomes from [[core-loop]]"),
        ]
        edges = build_wiki_link_edges(nodes)
        assert len(edges) == 2  # bidirectional references
        sources = {(e.source_id, e.target_id) for e in edges}
        assert ("n1", "n2") in sources
        assert ("n2", "n1") in sources

    def test_no_self_referencing_edges(self):
        nodes = [
            _make_node(node_id="n1", title="Self Ref",
                       description="This is about [[self-ref]]"),
        ]
        edges = build_wiki_link_edges(nodes)
        assert len(edges) == 0  # self-ref should not create edge
