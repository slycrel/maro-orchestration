"""Tests for hybrid_search — BM25 + RRF retrieval."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


# ---------------------------------------------------------------------------
# Test documents
# ---------------------------------------------------------------------------

@dataclass
class _Doc:
    text: str
    lesson: str = ""  # alias for compatibility with memory.py TieredLesson interface
    created_at: str = ""

    def __post_init__(self):
        if not self.lesson:
            self.lesson = self.text


def _docs(*texts) -> List[_Doc]:
    return [_Doc(t) for t in texts]


# ---------------------------------------------------------------------------
# bm25_rank
# ---------------------------------------------------------------------------

class TestBm25Rank:
    def test_empty_docs_returns_empty(self):
        from hybrid_search import bm25_rank
        assert bm25_rank("query", []) == []

    def test_empty_query_returns_as_is(self):
        from hybrid_search import bm25_rank
        docs = _docs("alpha", "beta", "gamma")
        result = bm25_rank("", docs)
        assert result == docs

    def test_exact_match_ranked_first(self):
        from hybrid_search import bm25_rank
        docs = _docs(
            "unrelated content about databases",
            "polymarket trading strategies for prediction markets",
            "something completely different",
        )
        result = bm25_rank("polymarket prediction market trading", docs)
        assert result[0].text == "polymarket trading strategies for prediction markets"

    def test_top_k_limits_results(self):
        from hybrid_search import bm25_rank
        docs = _docs("alpha beta", "gamma delta", "epsilon zeta", "eta theta")
        result = bm25_rank("alpha beta gamma", docs, top_k=2)
        assert len(result) == 2

    def test_single_doc_returned(self):
        from hybrid_search import bm25_rank
        docs = _docs("the only document")
        result = bm25_rank("only document", docs)
        assert len(result) == 1

    def test_ranked_order_by_relevance(self):
        """More matching terms should rank higher."""
        from hybrid_search import bm25_rank
        docs = _docs(
            "research polymarket",
            "research polymarket markets trading strategy",
            "nothing relevant here",
        )
        result = bm25_rank("polymarket markets trading strategy research", docs)
        # The longer matching doc should be first (or at least not last)
        assert result[0].text != "nothing relevant here"
        assert result[-1].text == "nothing relevant here"

    def test_length_normalization_favors_precise_docs(self):
        """Very long docs shouldn't rank higher than short precise docs."""
        from hybrid_search import bm25_rank
        filler = " ".join(["filler"] * 100)
        docs = _docs(
            f"polymarket {filler}",   # relevant term buried in noise
            "polymarket prediction",  # short and precise
        )
        result = bm25_rank("polymarket prediction", docs)
        # Precise short doc should rank higher
        assert result[0].text == "polymarket prediction"

    def test_string_docs_work(self):
        """bm25_rank should work with plain strings."""
        from hybrid_search import bm25_rank
        docs = ["research polymarket", "database optimization", "trading strategies"]
        result = bm25_rank("polymarket trading", docs)
        assert isinstance(result[0], str)
        assert "polymarket" in result[0]


# ---------------------------------------------------------------------------
# rrf_rank
# ---------------------------------------------------------------------------

class TestRrfRank:
    def test_empty_rankings_returns_empty(self):
        from hybrid_search import rrf_rank
        assert rrf_rank([]) == []

    def test_single_ranking_passthrough(self):
        from hybrid_search import rrf_rank
        docs = _docs("a", "b", "c")
        result = rrf_rank([docs])
        assert result == docs

    def test_fuses_two_rankings(self):
        """Doc ranked #1 in both lists should win."""
        from hybrid_search import rrf_rank
        docs = _docs("winner", "second", "third")
        # Both lists agree: winner > second > third
        ranking_a = [docs[0], docs[1], docs[2]]
        ranking_b = [docs[0], docs[2], docs[1]]
        result = rrf_rank([ranking_a, ranking_b])
        assert result[0] is docs[0]

    def test_top_k_limits_output(self):
        from hybrid_search import rrf_rank
        docs = _docs("a", "b", "c", "d", "e")
        ranking_a = list(docs)
        ranking_b = list(reversed(docs))
        result = rrf_rank([ranking_a, ranking_b], top_k=3)
        assert len(result) == 3

    def test_doc_absent_from_one_ranking(self):
        """Doc in only one of two rankings should still appear in output."""
        from hybrid_search import rrf_rank
        d1, d2, d3 = _docs("a", "b", "c")
        ranking_a = [d1, d2]       # d3 absent
        ranking_b = [d3, d1, d2]   # d3 present
        result = rrf_rank([ranking_a, ranking_b])
        assert d3 in result


# ---------------------------------------------------------------------------
# hybrid_rank
# ---------------------------------------------------------------------------

class TestHybridRank:
    def test_empty_returns_empty(self):
        from hybrid_search import hybrid_rank
        assert hybrid_rank("query", []) == []

    def test_no_recency_falls_back_to_bm25(self):
        from hybrid_search import hybrid_rank
        docs = _docs("polymarket research", "database tuning", "trading strategies")
        result = hybrid_rank("polymarket trading", docs)
        assert len(result) == 3
        # Relevant docs should rank higher
        assert result[0].text != "database tuning"

    def test_with_recency_key_fuses_signals(self):
        """With recency_key, newer+relevant docs should rank better."""
        from hybrid_search import hybrid_rank

        @dataclass
        class TimedDoc:
            lesson: str
            created_at: str

        docs = [
            TimedDoc("polymarket trading strategies", "2026-03-28"),
            TimedDoc("polymarket analysis", "2026-03-30"),  # newer
            TimedDoc("unrelated content", "2026-03-31"),     # newest but irrelevant
        ]
        result = hybrid_rank("polymarket trading strategy analysis", docs,
                              recency_key="created_at")
        assert len(result) == 3
        # Newest+relevant doc should rank highly
        unrelated_rank = next(i for i, d in enumerate(result) if "unrelated" in d.lesson)
        # Unrelated should not be first despite being newest
        assert unrelated_rank > 0

    def test_top_k_respected(self):
        from hybrid_search import hybrid_rank
        docs = _docs("a", "b", "c", "d", "e")
        result = hybrid_rank("a b", docs, top_k=2)
        assert len(result) == 2
