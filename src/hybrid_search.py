"""Hybrid retrieval — BM25 + Reciprocal Rank Fusion.

From the Mimir steal list: replace TF-IDF with BM25 for better lesson
retrieval, and add RRF to fuse multiple ranking signals.

BM25 improvements over TF-IDF:
  - Term frequency saturation (k1 parameter) — stops rewarding repetition
  - Document length normalization (b parameter) — short docs aren't penalized
  - Better IDF formula: log((N - df + 0.5) / (df + 0.5) + 1)

RRF (Reciprocal Rank Fusion): combine two or more ranked lists without
needing score normalization. `rrf_score(d) = Σ 1 / (k + rank_i(d))`

Usage:
    from hybrid_search import bm25_rank, rrf_rank, hybrid_rank

    # Direct BM25 ranking
    ranked = bm25_rank(query, docs, top_k=10)

    # Fuse two rankings
    ranking_a = bm25_rank(query, docs)
    ranking_b = recency_rank(docs)  # your recency-ordered list
    fused = rrf_rank([ranking_a, ranking_b], k=60, top_k=10)

    # One-shot hybrid (BM25 + built-in recency signal)
    ranked = hybrid_rank(query, docs, top_k=10)

doc format: any object with a .lesson attribute (TieredLesson) OR a plain str.
For strings, BM25 score is computed directly.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any, Dict, List, Optional, TypeVar, Union

T = TypeVar("T")

# BM25 tuning parameters
_K1 = 1.5   # term frequency saturation (1.2–2.0 typical)
_B = 0.75   # document length normalization (0.0 = off, 0.75 typical)

_STOP_WORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "is", "was", "are", "were", "be", "been", "being", "it",
    "its", "this", "that", "these", "those", "i", "we", "you", "he", "she",
    "they", "what", "when", "where", "who", "which", "how", "if", "as", "by",
    "from", "not", "can", "will", "do", "did", "does", "have", "had", "has",
    "should", "would", "could", "may", "might", "step", "goal", "task",
})


def _tokenize(text: str) -> List[str]:
    """Lowercase + split on non-alphanumeric, filter stop words + short tokens."""
    return [
        t for t in re.sub(r"[^a-z0-9]+", " ", text.lower()).split()
        if t not in _STOP_WORDS and len(t) > 2
    ]


def _doc_text(doc: Any) -> str:
    """Extract text from a doc (TieredLesson object or string)."""
    if isinstance(doc, str):
        return doc
    # TieredLesson / Lesson objects have a .lesson attribute
    if hasattr(doc, "lesson"):
        return str(doc.lesson)
    return str(doc)


def bm25_rank(
    query: str,
    docs: List[T],
    *,
    top_k: Optional[int] = None,
) -> List[T]:
    """Rank docs by BM25 score against query.

    Pure stdlib — no external dependencies. O(N * |query|) time.

    Args:
        query: Query string.
        docs: List of documents (TieredLesson objects or strings).
        top_k: Return only top-K results. None = return all, ranked.

    Returns:
        Docs sorted by descending BM25 score. Zero-score docs included last.
    """
    if not docs:
        return []

    query_terms = _tokenize(query)
    if not query_terms:
        return docs  # no signal — return as-is

    # Tokenize all docs
    doc_tokens: List[List[str]] = [_tokenize(_doc_text(d)) for d in docs]
    n_docs = len(docs)
    avgdl = sum(len(t) for t in doc_tokens) / max(n_docs, 1)

    # Document frequency per term
    df: Counter = Counter()
    for tokens in doc_tokens:
        for term in set(tokens):
            df[term] += 1

    def _idf(term: str) -> float:
        """BM25 IDF: log((N - df + 0.5) / (df + 0.5) + 1)"""
        n = df.get(term, 0)
        return math.log((n_docs - n + 0.5) / (n + 0.5) + 1)

    def _bm25_score(doc_idx: int) -> float:
        tokens = doc_tokens[doc_idx]
        tf_counts = Counter(tokens)
        dl = len(tokens)
        score = 0.0
        for term in query_terms:
            if term not in tf_counts:
                continue
            tf = tf_counts[term]
            idf = _idf(term)
            # BM25 term weight
            numerator = tf * (_K1 + 1)
            denominator = tf + _K1 * (1 - _B + _B * dl / max(avgdl, 1))
            score += idf * (numerator / denominator)
        return score

    scored = [((_bm25_score(i), doc)) for i, doc in enumerate(docs)]
    scored.sort(key=lambda x: x[0], reverse=True)
    ranked = [doc for _, doc in scored]
    return ranked[:top_k] if top_k is not None else ranked


def rrf_rank(
    rankings: List[List[T]],
    *,
    k: int = 60,
    top_k: Optional[int] = None,
) -> List[T]:
    """Reciprocal Rank Fusion of multiple ranked lists.

    Combines N ranked lists without needing score normalization.
    rrf_score(d) = Σ_i 1 / (k + rank_i(d))

    Docs not present in a ranking are treated as rank = len(that_ranking) + 1.

    Args:
        rankings: List of ranked document lists (all containing the same docs).
        k: Smoothing constant (default 60, standard in the literature).
        top_k: Return only top-K fused results.

    Returns:
        Fused ranking by descending RRF score.
    """
    if not rankings:
        return []
    if len(rankings) == 1:
        return rankings[0][:top_k] if top_k else rankings[0]

    # Collect all unique docs (preserving identity via id())
    all_docs: Dict[int, T] = {}
    for ranking in rankings:
        for doc in ranking:
            all_docs[id(doc)] = doc

    # Build rank lookup for each ranking: id(doc) -> 0-based position
    rank_maps: List[Dict[int, int]] = []
    for ranking in rankings:
        rank_maps.append({id(doc): pos for pos, doc in enumerate(ranking)})

    # Compute RRF score
    max_rank = max(len(r) for r in rankings)
    rrf_scores: List[tuple] = []
    for doc_id, doc in all_docs.items():
        score = sum(
            1.0 / (k + rm.get(doc_id, max_rank + 1))
            for rm in rank_maps
        )
        rrf_scores.append((score, doc))

    rrf_scores.sort(key=lambda x: x[0], reverse=True)
    result = [doc for _, doc in rrf_scores]
    return result[:top_k] if top_k is not None else result


def hybrid_rank(
    query: str,
    docs: List[T],
    *,
    top_k: Optional[int] = None,
    recency_key: Optional[str] = None,
) -> List[T]:
    """One-shot hybrid ranking: BM25 + optional recency signal via RRF.

    If docs have a recency_key attribute (e.g. 'created_at'), a recency
    ranking is computed and fused with BM25 via RRF. Otherwise pure BM25.

    Args:
        query: Query string.
        docs: Documents to rank.
        top_k: Return only top-K results.
        recency_key: Attribute name on docs to use for recency ordering.
                     If None, defaults to checking for 'created_at' attr.

    Returns:
        Fused ranking (or pure BM25 if recency unavailable).
    """
    if not docs:
        return []

    bm25_ranking = bm25_rank(query, docs)

    # Try to build a recency ranking
    _rkey = recency_key
    if _rkey is None and docs and hasattr(docs[0], "created_at"):
        _rkey = "created_at"

    if _rkey:
        try:
            recency_ranking = sorted(
                docs,
                key=lambda d: getattr(d, _rkey, "") or "",
                reverse=True,  # newest first
            )
            return rrf_rank([bm25_ranking, recency_ranking], top_k=top_k)
        except Exception:
            pass  # fall through to pure BM25

    return bm25_ranking[:top_k] if top_k is not None else bm25_ranking
