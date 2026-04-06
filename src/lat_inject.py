"""lat_inject.py — TF-IDF selection of lat.md knowledge graph nodes for planning context.

Closes the lat.md runtime integration gap: the lat.md/ directory contains 9 cross-linked
concept nodes about Poe's architecture. This module selects the 1-2 most relevant nodes
for a given goal and returns their content for injection into director / decompose prompts.

Zero-LLM, zero-cost — pure TF-IDF over node titles + first paragraphs. Cached on first
call. Fails silently (returns empty string) if lat.md/ is missing or unreadable.

Usage:
    from lat_inject import inject_relevant_nodes
    context = inject_relevant_nodes("improve the evolver scoring logic")
    # Returns: 1-2 paragraphs from self-improvement.md and memory-system.md
    system_prompt += "\\n\\n" + context  # if context is non-empty

Design follows the same TF-IDF pattern as memory.py's `_tfidf_rank`.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Node registry (lazy-loaded)
# ---------------------------------------------------------------------------

_NODES: Optional[Dict[str, str]] = None  # filename → content


def _lat_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "lat.md"


def _load_nodes() -> Dict[str, str]:
    """Load all .md nodes from lat.md/ directory. Cached after first call."""
    global _NODES
    if _NODES is not None:
        return _NODES

    lat = _lat_dir()
    result = {}
    if not lat.exists():
        _NODES = result
        return _NODES

    for path in sorted(lat.glob("*.md")):
        if path.name == "lat.md":
            continue  # skip the index
        try:
            content = path.read_text(encoding="utf-8").strip()
            result[path.stem] = content
        except Exception:
            pass

    _NODES = result
    return _NODES


# ---------------------------------------------------------------------------
# TF-IDF ranking (same stdlib approach as memory.py)
# ---------------------------------------------------------------------------

_STOP_WORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "is", "was", "are", "were", "be", "been", "it", "its",
    "this", "that", "these", "those", "i", "we", "you", "he", "she", "they",
    "what", "when", "where", "who", "which", "how", "if", "as", "by", "from",
    "not", "can", "will", "do", "did", "does", "have", "had", "has",
    "should", "would", "could", "may", "might", "step", "goal", "task",
    "via", "each", "all", "per", "src", "py", "md",
})


def _tokenize(text: str) -> List[str]:
    """Lowercase, split on non-alphanumeric, filter stop words."""
    return [
        t for t in re.sub(r"[^a-z0-9]+", " ", text.lower()).split()
        if t not in _STOP_WORDS and len(t) > 2
    ]


def _score_nodes(query: str, nodes: Dict[str, str]) -> List[Tuple[str, float]]:
    """Score each node against query using TF-IDF cosine similarity."""
    if not nodes:
        return []

    query_tokens = Counter(_tokenize(query))
    if not query_tokens:
        return []

    # Build IDF from all node contents
    docs = list(nodes.values())
    N = len(docs)
    df: Counter = Counter()
    doc_tfs = []
    for doc in docs:
        tokens = _tokenize(doc)
        tf = Counter(tokens)
        doc_tfs.append(tf)
        df.update(tf.keys())

    idf = {t: math.log((N + 1) / (df.get(t, 0) + 1)) for t in query_tokens}

    # Score each node
    scored = []
    for (name, content), tf in zip(nodes.items(), doc_tfs):
        score = sum(
            query_tokens[t] * tf.get(t, 0) * idf.get(t, 0)
            for t in query_tokens
        )
        if score > 0:
            scored.append((name, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def inject_relevant_nodes(
    goal: str,
    *,
    max_nodes: int = 2,
    max_chars_per_node: int = 400,
) -> str:
    """Select the most relevant lat.md nodes for `goal` and return their content.

    Args:
        goal: The goal or step text to match against.
        max_nodes: Maximum number of nodes to include (default 2).
        max_chars_per_node: Truncate each node to this many chars.

    Returns:
        Formatted context string (empty if nothing relevant or lat.md unavailable).
        Format: "SYSTEM KNOWLEDGE:\\n\\n## <node name>\\n<excerpt>\\n\\n## ..."
    """
    try:
        nodes = _load_nodes()
        if not nodes:
            return ""

        scored = _score_nodes(goal, nodes)
        if not scored:
            return ""

        selected = scored[:max_nodes]
        parts = []
        for name, _ in selected:
            content = nodes[name][:max_chars_per_node]
            # Use first 2 paragraphs (most informative part of each node)
            paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
            excerpt = "\n\n".join(paragraphs[:2])[:max_chars_per_node]
            title = name.replace("-", " ").title()
            parts.append(f"## {title}\n{excerpt}")

        if not parts:
            return ""

        return "SYSTEM KNOWLEDGE (architecture context):\n\n" + "\n\n".join(parts)

    except Exception:
        return ""  # lat_inject must never block any caller


def relevant_node_names(goal: str, *, max_nodes: int = 2) -> List[str]:
    """Return just the names of the most relevant nodes (for testing / logging)."""
    try:
        nodes = _load_nodes()
        scored = _score_nodes(goal, nodes)
        return [name for name, _ in scored[:max_nodes]]
    except Exception:
        return []
