#!/usr/bin/env python3
"""Import link-farm posts into knowledge nodes.

Reads posts_final_v3.json from the link-farm repo and creates
KnowledgeNode entries in ~/.poe/workspace/memory/knowledge_nodes.jsonl.

Usage:
    python3 scripts/import_link_farm.py [--link-farm-path PATH] [--dry-run]

The link-farm repo is expected at ~/claude/link-farm/ by default.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add src/ to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from knowledge_web import (
    KnowledgeNode, KnowledgeEdge,
    append_knowledge_node, append_knowledge_edge,
    load_knowledge_nodes,
    NODE_ACTIVE, NODE_CANDIDATE,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Topic → domain mapping
# ---------------------------------------------------------------------------

# Map link-farm topics to knowledge node domains
_TOPIC_TO_DOMAIN: Dict[str, str] = {
    "agent-design": "orchestration",
    "dev-practices": "engineering",
    "claude-code": "tooling",
    "general": "general",
    "management": "management",
    "research": "research",
    "skills-mcp": "tooling",
    "prompting": "prompting",
    "industry": "industry",
    "questionable": "general",
}

# Map link-farm topics to node types
_TOPIC_TO_TYPE: Dict[str, str] = {
    "agent-design": "pattern",
    "dev-practices": "technique",
    "claude-code": "tool",
    "general": "insight",
    "management": "insight",
    "research": "insight",
    "skills-mcp": "tool",
    "prompting": "technique",
    "industry": "insight",
    "questionable": "insight",
}

# Priority → confidence mapping
_PRIORITY_TO_CONFIDENCE: Dict[str, float] = {
    "now": 0.8,
    "near-term": 0.6,
    "long-term": 0.4,
}


# ---------------------------------------------------------------------------
# Import logic
# ---------------------------------------------------------------------------

def _post_to_node_id(post: Dict[str, Any]) -> str:
    """Deterministic node ID from post URL."""
    url = post.get("url", "")
    return "lf-" + hashlib.sha256(url.encode()).hexdigest()[:10]


def _post_to_node(post: Dict[str, Any]) -> KnowledgeNode:
    """Convert a link-farm post to a KnowledgeNode."""
    topics = post.get("topics", [])
    primary_topic = topics[0] if topics else "general"

    # Build description from summary + author context
    summary = post.get("summary", "").strip()
    author = post.get("author", "")
    handle = post.get("handle", "")
    content = post.get("content", "")

    # Use content if available, fall back to summary
    description = content.strip() if content else summary
    if not description:
        description = post.get("subject", "")

    # Truncate very long descriptions (some content fields are huge)
    if len(description) > 1000:
        description = description[:997] + "..."

    return KnowledgeNode(
        node_id=_post_to_node_id(post),
        node_type=_TOPIC_TO_TYPE.get(primary_topic, "insight"),
        title=_build_title(post),
        description=description,
        domain=_TOPIC_TO_DOMAIN.get(primary_topic, "general"),
        sources=[post.get("url", "")],
        tags=topics,
        status=NODE_ACTIVE,
        confidence=_PRIORITY_TO_CONFIDENCE.get(post.get("priority", ""), 0.5),
        author=f"{author} ({handle})" if handle else author,
        created_at=post.get("date", ""),
    )


def _build_title(post: Dict[str, Any]) -> str:
    """Build a concise title from the post."""
    summary = post.get("summary", "")
    subject = post.get("subject", "")

    # If subject is generic ("Post by X on X"), derive from summary
    if subject.startswith("Post by ") or not subject:
        # Take first sentence of summary, cap at 100 chars
        first_sentence = summary.split(". ")[0] if summary else "Untitled"
        if len(first_sentence) > 100:
            first_sentence = first_sentence[:97] + "..."
        return first_sentence
    return subject[:100]


def _build_topic_edges(nodes: List[KnowledgeNode]) -> List[KnowledgeEdge]:
    """Build edges between nodes that share topics.

    Two nodes get a "related" edge if they share ≥2 topics (strong signal)
    or share a non-generic topic (agent-design, skills-mcp, etc.).
    """
    # Group nodes by tag
    tag_to_nodes: Dict[str, List[str]] = {}
    for node in nodes:
        for tag in node.tags:
            if tag not in tag_to_nodes:
                tag_to_nodes[tag] = []
            tag_to_nodes[tag].append(node.node_id)

    # Build edges from shared tags
    edge_pairs: Dict[tuple, float] = {}
    generic_tags = {"general", "questionable"}

    for tag, node_ids in tag_to_nodes.items():
        if tag in generic_tags:
            continue
        if len(node_ids) > 50:
            continue  # Skip overly common tags (would create too many edges)

        weight = 0.5 if len(node_ids) > 20 else 0.8
        for i, a in enumerate(node_ids):
            for b in node_ids[i + 1:]:
                pair = (min(a, b), max(a, b))
                edge_pairs[pair] = max(edge_pairs.get(pair, 0), weight)

    edges = []
    for (source, target), weight in edge_pairs.items():
        edges.append(KnowledgeEdge(
            source_id=source,
            target_id=target,
            relation="related",
            weight=weight,
        ))
    return edges


def import_link_farm(
    json_path: Path,
    *,
    dry_run: bool = False,
    verbose: bool = False,
) -> Dict[str, Any]:
    """Import link-farm posts into knowledge nodes.

    Args:
        json_path: Path to posts_final_v3.json
        dry_run: Don't write anything, just report what would happen.
        verbose: Print progress.

    Returns:
        Summary dict with counts.
    """
    with open(json_path, "r", encoding="utf-8") as f:
        posts = json.load(f)

    if verbose:
        print(f"[import] loaded {len(posts)} posts from {json_path}", file=sys.stderr)

    # Check for existing nodes to avoid duplicates
    existing_ids = set()
    if not dry_run:
        for node in load_knowledge_nodes(status=""):
            existing_ids.add(node.node_id)

    # Convert posts to nodes
    nodes: List[KnowledgeNode] = []
    skipped = 0
    for post in posts:
        node = _post_to_node(post)
        if node.node_id in existing_ids:
            skipped += 1
            continue
        nodes.append(node)

    if verbose:
        print(f"[import] {len(nodes)} new nodes ({skipped} already exist)", file=sys.stderr)

    # Domain distribution
    domains = Counter(n.domain for n in nodes)
    types = Counter(n.node_type for n in nodes)
    if verbose:
        print(f"[import] domains: {dict(domains.most_common())}", file=sys.stderr)
        print(f"[import] types: {dict(types.most_common())}", file=sys.stderr)

    # Build edges
    edges = _build_topic_edges(nodes)
    if verbose:
        print(f"[import] {len(edges)} topic-based edges", file=sys.stderr)

    if dry_run:
        return {
            "posts_loaded": len(posts),
            "nodes_new": len(nodes),
            "nodes_skipped": skipped,
            "edges": len(edges),
            "domains": dict(domains),
            "types": dict(types),
        }

    # Write nodes
    for node in nodes:
        append_knowledge_node(node)
        existing_ids.add(node.node_id)

    # Write edges
    for edge in edges:
        append_knowledge_edge(edge)

    # Log to captain's log
    try:
        from captains_log import log_event
        log_event(
            event_type="KNOWLEDGE_IMPORT",
            subject="link-farm",
            summary=f"Imported {len(nodes)} knowledge nodes from link-farm ({skipped} skipped as duplicates). "
                    f"{len(edges)} topic-based edges created.",
            context={
                "source": str(json_path),
                "nodes_imported": len(nodes),
                "nodes_skipped": skipped,
                "edges_created": len(edges),
                "domains": dict(domains),
            },
        )
    except Exception:
        pass

    if verbose:
        print(f"[import] done: {len(nodes)} nodes + {len(edges)} edges written to "
              f"~/.poe/workspace/memory/", file=sys.stderr)

    return {
        "posts_loaded": len(posts),
        "nodes_new": len(nodes),
        "nodes_skipped": skipped,
        "edges": len(edges),
        "domains": dict(domains),
        "types": dict(types),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Import link-farm posts into knowledge nodes",
    )
    parser.add_argument("--link-farm-path", type=Path,
                        default=Path.home() / "claude" / "link-farm" / "posts_final_v3.json",
                        help="Path to posts_final_v3.json")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be imported without writing")
    parser.add_argument("--verbose", "-v", action="store_true")

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)

    if not args.link_farm_path.exists():
        print(f"Error: {args.link_farm_path} not found", file=sys.stderr)
        print(f"Clone the link-farm repo: git clone https://github.com/slycrel/link-farm.git ~/claude/link-farm",
              file=sys.stderr)
        sys.exit(1)

    result = import_link_farm(args.link_farm_path, dry_run=args.dry_run, verbose=True)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
