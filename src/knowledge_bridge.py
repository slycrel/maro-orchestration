"""K4: Bridge to Orchestration — Write Path.

Closes the bidirectional knowledge loop: orchestration outcomes update the
knowledge layer with validated observations, causal edges, and skill patterns.

Entry point: `outcome_to_knowledge(outcome)` — call after reflect_and_record().

Design principles:
- Non-blocking: all writes are fire-and-forget with try/except guards
- Fail-open: knowledge writes must never break the main orchestration path
- Heuristic fallback: if no adapter, extract from lessons instead of LLM
- Dedup via title similarity (Jaccard ≥ 0.7) — avoids node explosion
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Similarity helper (local copy — no cross-module import of private utils)
# ---------------------------------------------------------------------------

def _jaccard(a: str, b: str, *, n: int = 3) -> float:
    """n-gram Jaccard similarity between two strings."""
    def ngrams(s: str) -> set:
        s = s.lower()
        return {s[i:i + n] for i in range(len(s) - n + 1)} if len(s) >= n else {s}

    sa, sb = ngrams(a), ngrams(b)
    union = sa | sb
    if not union:
        return 0.0
    return len(sa & sb) / len(union)


# ---------------------------------------------------------------------------
# Heuristic observation extraction (no LLM required)
# ---------------------------------------------------------------------------

def _extract_heuristic(outcome) -> List[Tuple[str, str]]:
    """Extract (title, description) pairs from outcome lessons heuristically.

    Returns up to 3 candidates (title, description).
    """
    lessons: List[str] = getattr(outcome, "lessons", []) or []
    goal: str = getattr(outcome, "goal", "")
    status: str = getattr(outcome, "status", "done")
    task_type: str = getattr(outcome, "task_type", "general")

    candidates: List[Tuple[str, str]] = []

    for lesson in lessons[:5]:  # cap at 5 lessons per outcome
        lesson = lesson.strip()
        if len(lesson) < 20:
            continue

        # Build a short title from the first clause (≤ 8 words)
        words = lesson.split()
        title = " ".join(words[:8])
        if title.endswith(","):
            title = title[:-1]

        node_type = "insight"
        if status == "done" and any(
            kw in lesson.lower()
            for kw in ("always", "never", "when", "pattern", "principle", "best practice")
        ):
            node_type = "principle"
        elif any(kw in lesson.lower() for kw in ("use ", "apply ", "prefer ", "try ")):
            node_type = "technique"

        desc = f"{lesson}\n\n[Extracted from: {task_type} / {status} — {goal[:80]}]"
        candidates.append((title, desc, node_type))

    return candidates  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# LLM-based observation extraction
# ---------------------------------------------------------------------------

_EXTRACT_PROMPT = """\
Given this completed orchestration run, extract 1-3 generalizable knowledge observations.

GOAL: {goal}
STATUS: {status}
TASK TYPE: {task_type}
SUMMARY: {summary}
LESSONS: {lessons_text}

For each observation produce ONE JSON object on its own line:
{{"title": "<8 words max>", "description": "<2-3 sentences>", "node_type": "<principle|pattern|technique|insight>", "domain": "<orchestration|memory|quality|planning|tooling>"}}

Rules:
- Only include observations that generalize beyond this specific run
- Skip trivial or obvious observations
- Prefer principles and patterns over one-off insights
- Output only the JSON lines, nothing else
"""


def _extract_llm(outcome, adapter) -> List[Tuple[str, str, str, str]]:
    """Use LLM to extract (title, description, node_type, domain) from outcome."""
    try:
        lessons = getattr(outcome, "lessons", []) or []
        lessons_text = "\n".join(f"- {l}" for l in lessons[:8]) or "(none)"
        summary = getattr(outcome, "summary", "") or ""
        goal = getattr(outcome, "goal", "") or ""
        status = getattr(outcome, "status", "done")
        task_type = getattr(outcome, "task_type", "general")

        prompt = _EXTRACT_PROMPT.format(
            goal=goal[:300],
            status=status,
            task_type=task_type,
            summary=summary[:500],
            lessons_text=lessons_text,
        )

        result = adapter.complete(prompt)
        text = result.get("content", "") if isinstance(result, dict) else str(result)

        candidates = []
        for line in text.strip().splitlines():
            line = line.strip()
            if not line or not line.startswith("{"):
                continue
            try:
                d = json.loads(line)
                title = str(d.get("title", "")).strip()
                description = str(d.get("description", "")).strip()
                node_type = str(d.get("node_type", "insight")).strip()
                domain = str(d.get("domain", "orchestration")).strip()
                if title and description:
                    candidates.append((title, description, node_type, domain))
            except json.JSONDecodeError:
                continue

        return candidates
    except Exception as e:
        log.debug("knowledge_bridge: LLM extraction failed, falling back: %s", e)
        return []


# ---------------------------------------------------------------------------
# Upsert logic — dedup by title similarity
# ---------------------------------------------------------------------------

_DEDUP_THRESHOLD = 0.7  # Jaccard similarity to consider a title a duplicate


def _find_similar_node(title: str, nodes) -> Optional[Any]:
    """Return the first existing node whose title is similar to `title`."""
    for node in nodes:
        if _jaccard(title, node.title) >= _DEDUP_THRESHOLD:
            return node
    return None


def upsert_knowledge_from_candidate(
    title: str,
    description: str,
    node_type: str,
    domain: str,
    sources: List[str],
    *,
    existing_nodes,
    confidence_bump: float = 0.05,
) -> Tuple[Any, bool]:
    """Insert a new node or update an existing similar one.

    Returns (node, is_new).
    """
    from knowledge_web import (
        KnowledgeNode,
        KnowledgeEdge,
        append_knowledge_node,
        append_knowledge_edge,
        NODE_CANDIDATE,
        NODE_TYPES,
    )
    from file_lock import locked_write
    from memory_ledger import _memory_dir

    # Validate node_type
    if node_type not in NODE_TYPES:
        node_type = "insight"

    existing = _find_similar_node(title, existing_nodes)

    if existing is not None:
        # Update: bump confidence, update validated_at, add source if new
        new_confidence = min(1.0, existing.confidence + confidence_bump)
        new_sources = list(set(existing.sources + sources))
        now = datetime.now(timezone.utc).isoformat()

        # Rewrite the node in-place via full-file rewrite (small file)
        nodes_path = _memory_dir() / "knowledge_nodes.jsonl"
        with locked_write(nodes_path):
            lines = []
            if nodes_path.exists():
                for line in nodes_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                        if d.get("node_id") == existing.node_id:
                            d["confidence"] = new_confidence
                            d["validated_at"] = now
                            d["sources"] = new_sources
                            d["times_applied"] = d.get("times_applied", 0) + 1
                        lines.append(json.dumps(d, sort_keys=True))
                    except (json.JSONDecodeError, TypeError):
                        lines.append(line)
            nodes_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        # Update the existing object for return
        existing.confidence = new_confidence
        existing.validated_at = now
        existing.sources = new_sources
        log.debug("knowledge_bridge: updated node %s %r → confidence=%.2f",
                  existing.node_id, existing.title[:40], new_confidence)
        return existing, False

    else:
        # Create new candidate node
        node = KnowledgeNode(
            node_id=uuid.uuid4().hex[:12],
            node_type=node_type,
            title=title,
            description=description,
            domain=domain,
            sources=sources,
            tags=["auto-extracted"],
            status=NODE_CANDIDATE,
            confidence=0.3,  # low initial confidence for auto-extracted nodes
            author="knowledge_bridge",
        )
        append_knowledge_node(node)
        log.debug("knowledge_bridge: created node %s %r", node.node_id, node.title[:40])
        return node, True


# ---------------------------------------------------------------------------
# Skill knowledge edges
# ---------------------------------------------------------------------------

def record_skill_knowledge_edge(
    skill_id: str,
    skill_name: str,
    outcome_id: str,
    task_type: str,
    success: bool,
    *,
    existing_nodes,
) -> None:
    """Record that a skill was used in an outcome (causal knowledge edge).

    If there's a knowledge node for the skill, create an 'implements' edge
    from the skill node to the outcome. This builds the skill effectiveness graph.
    """
    try:
        from knowledge_web import KnowledgeEdge, append_knowledge_edge
        from memory_ledger import _memory_dir

        # Find an existing skill-related node by name similarity
        skill_node = None
        for node in existing_nodes:
            if node.node_type in ("technique", "tool", "pattern"):
                if _jaccard(skill_name, node.title) >= 0.5:
                    skill_node = node
                    break

        if skill_node is None:
            return  # No matching node — skip edge (don't create orphan edges)

        relation = "supports" if success else "contradicts"
        edge = KnowledgeEdge(
            source_id=skill_node.node_id,
            target_id=f"outcome:{outcome_id}",
            relation=relation,
            weight=0.8 if success else 0.4,
        )
        append_knowledge_edge(edge)
        log.debug("knowledge_bridge: skill edge %s →[%s]→ outcome:%s",
                  skill_node.node_id, relation, outcome_id)
    except Exception as e:
        log.debug("knowledge_bridge: skill edge failed (non-fatal): %s", e)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def outcome_to_knowledge(
    outcome,
    *,
    adapter=None,
    dry_run: bool = False,
    skills_used: Optional[List[Dict[str, Any]]] = None,
) -> int:
    """K4 write path: extract and upsert knowledge from a completed outcome.

    Args:
        outcome:       Outcome dataclass from memory_ledger.
        adapter:       LLM adapter (optional — falls back to heuristic if None).
        dry_run:       If True, extract candidates but don't write.
        skills_used:   Optional list of {"skill_id": ..., "skill_name": ..., "success": ...}
                       dicts for recording skill effectiveness edges.

    Returns:
        Count of new knowledge nodes created (0 on error or dry_run).
    """
    try:
        from knowledge_web import load_knowledge_nodes

        goal = getattr(outcome, "goal", "")
        status = getattr(outcome, "status", "done")
        outcome_id = getattr(outcome, "outcome_id", "")

        # Load existing nodes for dedup
        existing = load_knowledge_nodes(status=None)  # all statuses  # type: ignore[arg-type]

        # Extract candidates
        candidates: List[Tuple[str, str, str, str]] = []

        if adapter is not None:
            candidates = _extract_llm(outcome, adapter)

        if not candidates:
            # Heuristic fallback
            heuristic = _extract_heuristic(outcome)
            candidates = [
                (title, desc, ntype, "orchestration")
                for title, desc, ntype in heuristic
            ]

        if not candidates:
            return 0

        if dry_run:
            log.info("knowledge_bridge [dry_run]: %d candidates from %r",
                     len(candidates), goal[:60])
            return 0

        new_count = 0
        sources = [f"outcome:{outcome_id}"] if outcome_id else []

        for title, description, node_type, domain in candidates:
            try:
                _node, is_new = upsert_knowledge_from_candidate(
                    title=title,
                    description=description,
                    node_type=node_type,
                    domain=domain,
                    sources=sources,
                    existing_nodes=existing,
                )
                if is_new:
                    new_count += 1
                    # Reload existing to avoid creating near-duplicate nodes
                    existing = load_knowledge_nodes(status=None)  # type: ignore[arg-type]
            except Exception as e:
                log.debug("knowledge_bridge: upsert failed for %r: %s", title, e)

        # Record skill knowledge edges
        if skills_used:
            for skill_info in skills_used:
                try:
                    record_skill_knowledge_edge(
                        skill_id=skill_info.get("skill_id", ""),
                        skill_name=skill_info.get("skill_name", ""),
                        outcome_id=outcome_id,
                        task_type=getattr(outcome, "task_type", "general"),
                        success=skill_info.get("success", True),
                        existing_nodes=existing,
                    )
                except Exception as e:
                    log.debug("knowledge_bridge: skill edge failed: %s", e)

        if new_count:
            log.info("knowledge_bridge: +%d new knowledge nodes from outcome %s",
                     new_count, outcome_id)
        return new_count

    except Exception as e:
        log.warning("knowledge_bridge: outcome_to_knowledge failed (non-fatal): %s", e)
        return 0


# ---------------------------------------------------------------------------
# Skill evolution hook
# ---------------------------------------------------------------------------

def record_skill_evolution(
    skill,
    event: str,
    *,
    outcome_summary: str = "",
    dry_run: bool = False,
) -> None:
    """Record that a skill was created/promoted/demoted/retired.

    Creates or updates a 'technique' knowledge node for the skill,
    with the evolution event as provenance.

    Events: "created" | "promoted" | "demoted" | "retired" | "rewritten"
    """
    try:
        if dry_run:
            return

        from knowledge_web import load_knowledge_nodes

        skill_name: str = getattr(skill, "name", "")
        skill_id: str = getattr(skill, "id", "")
        description: str = getattr(skill, "description", "")
        tier: str = getattr(skill, "tier", "provisional")

        if not skill_name:
            return

        existing = load_knowledge_nodes(status=None, node_type="technique")  # type: ignore[arg-type]

        title = f"Skill: {skill_name}"
        domain = "orchestration"
        sources = [f"skill:{skill_id}", f"event:{event}"]
        if outcome_summary:
            sources.append(f"summary:{outcome_summary[:80]}")

        desc_parts = [description or f"Auto-evolved skill: {skill_name}"]
        desc_parts.append(f"\nEvolution history: {event} (tier={tier})")
        if outcome_summary:
            desc_parts.append(f"\nContext: {outcome_summary[:200]}")

        upsert_knowledge_from_candidate(
            title=title,
            description="\n".join(desc_parts),
            node_type="technique",
            domain=domain,
            sources=sources,
            existing_nodes=existing,
            confidence_bump=0.1 if event == "promoted" else 0.05,
        )
    except Exception as e:
        log.debug("knowledge_bridge: record_skill_evolution failed (non-fatal): %s", e)


# ---------------------------------------------------------------------------
# Principle validation hook
# ---------------------------------------------------------------------------

def validate_principle(
    node_id: str,
    *,
    validated: bool,
    outcome_id: str = "",
    notes: str = "",
) -> bool:
    """Mark a knowledge node as validated or contradicted by an outcome.

    For validated=True: bumps confidence, sets validated_at.
    For validated=False: decreases confidence; if confidence < 0.2, marks superseded.

    Returns True if the node was found and updated.
    """
    try:
        from knowledge_web import NODE_CANDIDATE, NODE_ACTIVE, NODE_SUPERSEDED
        from file_lock import locked_write
        from memory_ledger import _memory_dir

        nodes_path = _memory_dir() / "knowledge_nodes.jsonl"
        if not nodes_path.exists():
            return False

        now = datetime.now(timezone.utc).isoformat()
        found = False

        with locked_write(nodes_path):
            lines = []
            for line in nodes_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    if d.get("node_id") == node_id:
                        found = True
                        if validated:
                            d["confidence"] = min(1.0, d.get("confidence", 0.5) + 0.1)
                            d["validated_at"] = now
                            if d.get("status") == NODE_CANDIDATE:
                                d["status"] = NODE_ACTIVE
                        else:
                            d["confidence"] = max(0.0, d.get("confidence", 0.5) - 0.15)
                            if d["confidence"] < 0.2:
                                d["status"] = NODE_SUPERSEDED
                        if outcome_id:
                            srcs = d.get("sources", [])
                            srcs.append(f"contradiction:outcome:{outcome_id}" if not validated
                                        else f"validation:outcome:{outcome_id}")
                            d["sources"] = list(set(srcs))
                    lines.append(json.dumps(d, sort_keys=True))
                except (json.JSONDecodeError, TypeError):
                    lines.append(line)

            nodes_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        if found:
            action = "validated" if validated else "contradicted"
            log.info("knowledge_bridge: principle %s %s by outcome %s", node_id, action, outcome_id)
        return found

    except Exception as e:
        log.warning("knowledge_bridge: validate_principle failed (non-fatal): %s", e)
        return False
