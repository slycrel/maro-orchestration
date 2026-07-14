"""Goal Ancestry — §12 of maro_orchestration_spec.md

Every project carries a reverse-linked chain back to the top-level mission.
This prevents drift, keeps agents aligned with the big picture, and enables
reflection/auditing across the full goal tree.

Storage: <project_dir>/ancestry.json  (optional; root projects have none)

Data model:
{
  "parent_id": "parent-project-slug",   // null for root projects
  "ancestry": [                         // top-level mission first, immediate parent last
    {"id": "slug-or-id", "title": "Top-level mission"},
    ...
    {"id": "parent-slug", "title": "Immediate parent goal"}
  ]
}

Usage:
    from ancestry import (
        set_project_ancestry, get_project_ancestry,
        build_ancestry_prompt, create_child_ancestry,
        list_child_projects, orch_ancestry, orch_impact,
    )
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, TypedDict


MEASUREMENT_CLASSES = ("organic", "smoke", "control", "benchmark")


def normalize_measurement_class(
    value: Optional[str], *, default: str = "organic"
) -> str:
    """Validate prospective success-measurement provenance at its boundary."""
    normalized = str(value or default).strip().lower()
    if normalized not in MEASUREMENT_CLASSES:
        raise ValueError(
            "measurement_class must be one of: "
            + ", ".join(sorted(MEASUREMENT_CLASSES))
        )
    return normalized


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class AncestryNode:
    id: str
    title: str


@dataclass
class ProjectAncestry:
    parent_id: Optional[str]
    ancestry: List[AncestryNode] = field(default_factory=list)

    def depth(self) -> int:
        return len(self.ancestry)

    def to_dict(self) -> dict:
        return {
            "parent_id": self.parent_id,
            "ancestry": [asdict(n) for n in self.ancestry],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ProjectAncestry":
        nodes = [AncestryNode(**n) for n in d.get("ancestry", [])]
        return cls(parent_id=d.get("parent_id"), ancestry=nodes)


class Origin(TypedDict, total=False):
    """Serialized lineage shared by task, run, recall, and thread metadata.

    The shape remains a plain JSON dictionary on disk.  ``total=False`` is
    intentional: root/user-created work has no parent, while continuations
    acquire parent and recursion-cadence fields as they move through the
    queue.
    """

    source: str
    job_id: str
    parent_job_id: str
    parent_loop_id: str
    parent_handle_id: str
    parent_goal: str
    parent_project: str
    resumed_from: str
    next_checkin_depth: int
    checkins_sent: int
    measurement_class: str
    # Transport snapshot only. NavigatorDecision remains the authoritative
    # domain type; this field deliberately does not redeclare that contract.
    dispatch_navigator: Dict[str, Any]


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------

def _ancestry_path(project_dir: Path) -> Path:
    return project_dir / "ancestry.json"


def get_project_ancestry(project_dir: Path) -> Optional[ProjectAncestry]:
    """Load ancestry for a project. Returns None if no ancestry.json exists."""
    p = _ancestry_path(project_dir)
    if not p.exists():
        return None
    try:
        return ProjectAncestry.from_dict(json.loads(p.read_text(encoding="utf-8")))
    except Exception:
        return None


def set_project_ancestry(project_dir: Path, ancestry: ProjectAncestry) -> None:
    """Write ancestry.json for a project."""
    _ancestry_path(project_dir).write_text(
        json.dumps(ancestry.to_dict(), indent=2),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Ancestry creation helpers
# ---------------------------------------------------------------------------

def create_child_ancestry(
    parent_project_id: str,
    parent_title: str,
    parent_dir: Path,
) -> ProjectAncestry:
    """Build ancestry for a new child project given its parent's context.

    The child inherits the parent's full ancestry chain, then the parent itself
    is appended as the most-recent (deepest) ancestor.
    """
    parent_ancestry = get_project_ancestry(parent_dir)
    inherited: List[AncestryNode] = []
    if parent_ancestry:
        inherited = list(parent_ancestry.ancestry)
    inherited.append(AncestryNode(id=parent_project_id, title=parent_title))
    return ProjectAncestry(parent_id=parent_project_id, ancestry=inherited)


def record_fork_ancestry(
    child_dir: Path,
    *,
    parent_id: str,
    parent_title: str,
    parent_dir: Optional[Path] = None,
) -> bool:
    """Write ancestry.json for a forked/dispatched child project.

    First call wins — an existing ancestry.json is never clobbered (the
    original fork's lineage is the truth; re-dispatches don't rewrite it).
    When parent_dir is given and carries its own ancestry.json, the child
    inherits the full chain; otherwise the parent becomes the chain root.

    Returns True iff a new ancestry.json was written.
    """
    if not parent_id and not parent_title:
        return False
    if _ancestry_path(child_dir).exists():
        return False
    inherited: List[AncestryNode] = []
    if parent_dir is not None:
        parent_ancestry = get_project_ancestry(parent_dir)
        if parent_ancestry:
            inherited = list(parent_ancestry.ancestry)
    inherited.append(AncestryNode(id=parent_id, title=parent_title))
    child_dir.mkdir(parents=True, exist_ok=True)
    set_project_ancestry(child_dir, ProjectAncestry(parent_id=parent_id, ancestry=inherited))
    return True


# ---------------------------------------------------------------------------
# Prompt injection
# ---------------------------------------------------------------------------

_MAX_ANCESTRY_DEPTH = 10  # truncate beyond this to avoid context bloat


def build_ancestry_prompt(
    ancestry: Optional[ProjectAncestry],
    current_task: str = "",
) -> str:
    """Build a prompt prefix that injects the goal ancestry chain.

    Returns empty string if no ancestry (root projects).
    """
    if not ancestry or not ancestry.ancestry:
        return ""

    nodes = ancestry.ancestry
    if len(nodes) > _MAX_ANCESTRY_DEPTH:
        # Keep first 2 + last 2, summarize middle
        middle_count = len(nodes) - 4
        nodes = (
            nodes[:2]
            + [AncestryNode(id="...", title=f"...{middle_count} intermediate goals...")]
            + nodes[-2:]
        )

    lines = ["Goal Ancestry (stay aligned with this chain):"]
    for i, node in enumerate(nodes, 1):
        lines.append(f"  {i}. {node.title}")
    if current_task:
        lines.append(f"  → Current Task: {current_task}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tree traversal helpers
# ---------------------------------------------------------------------------

def list_child_projects(parent_slug: str, workspace_root: Path) -> List[str]:
    """List all project slugs that have parent_slug as direct parent."""
    children: List[str] = []
    projects_root = workspace_root
    if not projects_root.exists():
        return children
    for project_dir in sorted(projects_root.iterdir()):
        if not project_dir.is_dir():
            continue
        ancestry = get_project_ancestry(project_dir)
        if ancestry and ancestry.parent_id == parent_slug:
            children.append(project_dir.name)
    return children


def orch_ancestry(project_slug: str, project_dir: Path) -> List[str]:
    """Return the ancestry chain for a project as a list of strings (root first).

    Each entry is formatted as "slug: title" for the ancestor nodes,
    and "→ <project_slug> (current)" as the last entry.
    """
    ancestry = get_project_ancestry(project_dir)
    if not ancestry:
        return [f"→ {project_slug} (root — no ancestry)"]

    lines: List[str] = []
    for node in ancestry.ancestry:
        lines.append(f"{node.id}: {node.title}")
    lines.append(f"→ {project_slug} (current)")
    return lines


def orch_impact(
    goal_slug: str,
    workspace_root: Path,
    *,
    max_depth: int = 20,
) -> List[str]:
    """BFS traversal of all descendant projects of goal_slug.

    Returns list of slugs in breadth-first order.
    """
    result: List[str] = []
    queue = [goal_slug]
    visited: set = set()

    depth = 0
    while queue and depth < max_depth:
        next_queue: List[str] = []
        for slug in queue:
            if slug in visited:
                continue
            visited.add(slug)
            children = list_child_projects(slug, workspace_root)
            for child in children:
                result.append(child)
                next_queue.append(child)
        queue = next_queue
        depth += 1

    return result
