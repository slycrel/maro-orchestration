#!/usr/bin/env python3
"""Phase 13: Goal relationship map — active mission relationship tracking.

Poe needs to know how active missions relate to each other and to north star goals.
This module builds and queries a live graph of the goal hierarchy.

Usage:
    from goal_map import build_goal_map, find_conflicts
    gmap = build_goal_map()
    print(gmap.summary())
    conflicts = find_conflicts(gmap)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class GoalNode:
    id: str           # project slug or mission id
    title: str
    node_type: str    # "north_star" | "mission" | "project" | "feature"
    parent_ids: List[str]
    status: str
    last_updated: str

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "node_type": self.node_type,
            "parent_ids": list(self.parent_ids),
            "status": self.status,
            "last_updated": self.last_updated,
        }


@dataclass
class GoalMap:
    nodes: Dict[str, GoalNode] = field(default_factory=dict)

    def children_of(self, node_id: str) -> List[GoalNode]:
        """Return all nodes that list node_id as a parent."""
        return [n for n in self.nodes.values() if node_id in n.parent_ids]

    def ancestors_of(self, node_id: str) -> List[GoalNode]:
        """Return the ancestor chain for a node (immediate parent first, root last).

        Stops at nodes without parents or after 20 hops to prevent cycles.
        """
        result: List[GoalNode] = []
        visited = set()
        current_id = node_id
        for _ in range(20):
            node = self.nodes.get(current_id)
            if node is None or not node.parent_ids:
                break
            parent_id = node.parent_ids[0]  # use first parent for chain
            if parent_id in visited:
                break
            parent = self.nodes.get(parent_id)
            if parent is None:
                break
            visited.add(parent_id)
            result.append(parent)
            current_id = parent_id
        return result

    def find_conflicts(self) -> List[str]:
        """Detect nodes with multiple active missions sharing the same parent.

        Returns list of human-readable conflict descriptions.
        """
        return find_conflicts(self)

    def summary(self) -> str:
        """Return a human-readable summary of the goal map."""
        if not self.nodes:
            return "Goal map: empty (no projects or missions found)"

        lines = [f"Goal map: {len(self.nodes)} nodes"]

        # Group by node_type
        by_type: Dict[str, List[GoalNode]] = {}
        for node in self.nodes.values():
            by_type.setdefault(node.node_type, []).append(node)

        for ntype in ("north_star", "mission", "project", "feature"):
            nodes = by_type.get(ntype, [])
            if nodes:
                lines.append(f"\n{ntype.upper()}S ({len(nodes)}):")
                for n in sorted(nodes, key=lambda x: x.id)[:10]:
                    parent_str = f" ← {n.parent_ids[0]}" if n.parent_ids else ""
                    lines.append(f"  [{n.status:10s}] {n.id}: {n.title[:50]}{parent_str}")
                if len(nodes) > 10:
                    lines.append(f"  ... and {len(nodes) - 10} more")

        conflicts = find_conflicts(self)
        if conflicts:
            lines.append(f"\nCONFLICTS ({len(conflicts)}):")
            for c in conflicts:
                lines.append(f"  ! {c}")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Conflict detection
# ---------------------------------------------------------------------------

def find_conflicts(goal_map: GoalMap) -> List[str]:
    """Detect competing active missions sharing the same parent.

    Simple rule: multiple nodes with status=running sharing the same parent_id
    → potential resource competition.

    Returns list of human-readable conflict descriptions.
    """
    # Map parent_id → list of running children
    from collections import defaultdict
    parent_to_running: Dict[str, List[GoalNode]] = defaultdict(list)

    for node in goal_map.nodes.values():
        if node.status == "running":
            for parent_id in node.parent_ids:
                parent_to_running[parent_id].append(node)

    conflicts: List[str] = []
    for parent_id, running_children in parent_to_running.items():
        if len(running_children) >= 2:
            child_names = ", ".join(f"'{c.id}'" for c in running_children[:4])
            parent_node = goal_map.nodes.get(parent_id)
            parent_label = parent_node.title if parent_node else parent_id
            conflicts.append(
                f"Competing active missions under '{parent_label}': {child_names}"
            )

    return conflicts


# ---------------------------------------------------------------------------
# Map builder
# ---------------------------------------------------------------------------

def _orch_root() -> Optional[Path]:
    """Resolve orch_root safely."""
    try:
        from orch import orch_root
        return orch_root()
    except Exception:
        return None


def build_goal_map() -> GoalMap:
    """Scan all projects for ancestry.json + mission.json, build a GoalNode graph.

    Returns GoalMap with all discovered nodes linked by parent_ids.
    """
    goal_map = GoalMap()

    base = _orch_root()
    if base is None:
        return goal_map

    # Scan projects directory
    try:
        from orch import projects_root as _projects_root
        projects_dir = _projects_root()
    except Exception:
        projects_dir = base / "projects"

    if not projects_dir or not projects_dir.exists():
        return goal_map

    _now = datetime.now(timezone.utc).isoformat()

    for project_dir in sorted(projects_dir.iterdir()):
        if not project_dir.is_dir():
            continue

        project_slug = project_dir.name
        parent_ids: List[str] = []
        node_type = "project"
        title = project_slug
        status = "unknown"
        last_updated = _now

        # Load ancestry.json if present
        ancestry_file = project_dir / "ancestry.json"
        if ancestry_file.exists():
            try:
                ancestry_data = json.loads(ancestry_file.read_text(encoding="utf-8"))
                parent_id = ancestry_data.get("parent_id")
                if parent_id:
                    parent_ids = [parent_id]
                ancestry_chain = ancestry_data.get("ancestry", [])
                if ancestry_chain:
                    # If ancestry has entries, use last entry as immediate parent title context
                    last_ancestor = ancestry_chain[-1]
                    if parent_id and parent_id == last_ancestor.get("id"):
                        pass  # consistent
            except Exception:
                pass

        # Load mission.json if present
        mission_file = project_dir / "mission.json"
        if mission_file.exists():
            try:
                mission_data = json.loads(mission_file.read_text(encoding="utf-8"))
                title = mission_data.get("goal", project_slug)[:80]
                status = mission_data.get("status", "unknown")
                node_type = "mission"
                completed_at = mission_data.get("completed_at")
                created_at = mission_data.get("created_at", _now)
                last_updated = completed_at or created_at

                # Add mission node with mission-specific id
                mission_id = mission_data.get("id", project_slug)
                if mission_id != project_slug:
                    # Add a separate mission node
                    mission_node = GoalNode(
                        id=mission_id,
                        title=title,
                        node_type="mission",
                        parent_ids=[project_slug],
                        status=status,
                        last_updated=last_updated,
                    )
                    goal_map.nodes[mission_id] = mission_node

            except Exception:
                pass
        else:
            # Check tasks.md for any status hints
            tasks_file = project_dir / "tasks.md"
            if tasks_file.exists():
                try:
                    tasks_text = tasks_file.read_text(encoding="utf-8")
                    if "- [ ]" in tasks_text:
                        status = "running"
                    elif "- [x]" in tasks_text:
                        status = "done"
                    else:
                        status = "pending"
                except Exception:
                    status = "unknown"

        # Add project node
        project_node = GoalNode(
            id=project_slug,
            title=title,
            node_type=node_type,
            parent_ids=parent_ids,
            status=status,
            last_updated=last_updated,
        )
        goal_map.nodes[project_slug] = project_node

    return goal_map
