#!/usr/bin/env python3
"""Phase 19: Worker Boot Protocol for Poe orchestration.

Mandatory startup sequence for Worker sessions. Prevents re-doing completed
work or declaring premature success by reading progress state, git HEAD, and
known dead ends before picking the next task.

Usage:
    from boot_protocol import run_boot_protocol, format_boot_context
    state = run_boot_protocol("my-project")
    print(format_boot_context(state))
"""

from __future__ import annotations

import json
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class BootState:
    project: str
    loop_id: str
    completed_features: List[str]    # feature titles already DONE
    git_head: Optional[str]          # current git HEAD sha (if in git repo)
    existing_tests_pass: bool        # did existing test artifacts pass?
    dead_ends: List[str]             # loaded from DEAD_ENDS.md
    boot_timestamp: str
    boot_method: str                 # "full" | "minimal" | "dry_run"


# ---------------------------------------------------------------------------
# Core: run_boot_protocol
# ---------------------------------------------------------------------------

def run_boot_protocol(project: str, dry_run: bool = False) -> BootState:
    """Run the mandatory Worker boot sequence.

    Steps:
    1. Read NEXT.md — find items already marked DONE
    2. Check DEAD_ENDS.md — load known dead ends (create file if missing)
    3. Try git HEAD via subprocess
    4. Check mission.json if it exists — find completed features
    5. Returns BootState

    Args:
        project:  Project slug.
        dry_run:  If True, return minimal BootState without filesystem reads.

    Returns:
        BootState with all available context.
    """
    loop_id = str(uuid.uuid4())[:8]
    boot_timestamp = datetime.now(timezone.utc).isoformat()

    if dry_run:
        return BootState(
            project=project,
            loop_id=loop_id,
            completed_features=[],
            git_head=None,
            existing_tests_pass=True,
            dead_ends=[],
            boot_timestamp=boot_timestamp,
            boot_method="dry_run",
        )

    # Resolve project directory
    project_path = _project_path(project)

    # 1. Read NEXT.md for completed items
    completed_features = _read_completed_from_next(project_path)

    # 2. Check mission.json for completed features (authoritative if present)
    mission_completed = _read_completed_from_mission(project_path)
    # Merge: union of both sources
    all_completed = list(dict.fromkeys(completed_features + mission_completed))

    # 3. Load DEAD_ENDS.md
    dead_ends = _load_dead_ends(project_path)

    # 4. Get git HEAD
    git_head = _get_git_head(project_path)

    # 5. Check for passing test artifacts (heuristic: any recent .md artifact with "done")
    existing_tests_pass = _check_existing_artifacts(project_path)

    boot_method = "full" if project_path.exists() else "minimal"

    return BootState(
        project=project,
        loop_id=loop_id,
        completed_features=all_completed,
        git_head=git_head,
        existing_tests_pass=existing_tests_pass,
        dead_ends=dead_ends,
        boot_timestamp=boot_timestamp,
        boot_method=boot_method,
    )


def _project_path(project: str) -> Path:
    """Resolve the filesystem path for a project."""
    try:
        import orch
        return orch.project_dir(project)
    except Exception:
        return Path.cwd() / "projects" / project


def _read_completed_from_next(project_path: Path) -> List[str]:
    """Read NEXT.md and extract items marked as done ([x])."""
    next_file = project_path / "NEXT.md"
    if not next_file.exists():
        return []
    completed = []
    try:
        for line in next_file.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            # Match markdown checklist: - [x] item text
            if stripped.startswith("- [x]") or stripped.startswith("[x]"):
                text = stripped.lstrip("- [x]").strip()
                if text:
                    completed.append(text)
    except Exception:
        pass
    return completed


def _read_completed_from_mission(project_path: Path) -> List[str]:
    """Read mission.json and extract features with status==done."""
    mission_file = project_path / "mission.json"
    if not mission_file.exists():
        return []
    completed = []
    try:
        data = json.loads(mission_file.read_text(encoding="utf-8"))
        for ms in data.get("milestones", []):
            for f in ms.get("features", []):
                if f.get("status") == "done":
                    title = f.get("title", "")
                    if title:
                        completed.append(title)
    except Exception:
        pass
    return completed


def _load_dead_ends(project_path: Path) -> List[str]:
    """Load DEAD_ENDS.md — list of known dead ends. Creates file if missing."""
    dead_ends_file = project_path / "DEAD_ENDS.md"
    if not dead_ends_file.exists():
        # Create an empty DEAD_ENDS.md
        try:
            project_path.mkdir(parents=True, exist_ok=True)
            dead_ends_file.write_text(
                "# Dead Ends\n\nApproaches tried and failed. Do not repeat these.\n\n",
                encoding="utf-8",
            )
        except Exception:
            pass
        return []

    dead_ends = []
    try:
        content = dead_ends_file.read_text(encoding="utf-8")
        # Extract section headings (## [...] Loop ... — Step: ...)
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("## "):
                text = stripped[3:].strip()
                if text:
                    dead_ends.append(text)
    except Exception:
        pass
    return dead_ends


def _get_git_head(project_path: Path) -> Optional[str]:
    """Try to get git HEAD SHA. Returns None if not in a git repo."""
    # Try running git from the project path or its parent
    for search_dir in [project_path, project_path.parent if project_path.parent != project_path else None]:
        if search_dir is None:
            continue
        try:
            proc = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                timeout=5,
                cwd=str(search_dir),
            )
            if proc.returncode == 0:
                sha = proc.stdout.strip()
                if sha:
                    return sha[:12]
        except Exception:
            pass
    return None


def _check_existing_artifacts(project_path: Path) -> bool:
    """Heuristic: check if project has any completed artifacts."""
    artifacts_dir = project_path / "artifacts"
    if not artifacts_dir.exists():
        return True  # No artifacts yet = trivially passing
    try:
        artifact_files = list(artifacts_dir.glob("*.md")) + list(artifacts_dir.glob("*.json"))
        return len(artifact_files) > 0
    except Exception:
        return True


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def format_boot_context(state: BootState) -> str:
    """Format BootState as a system notification string for injection into Worker context.

    Returns:
        Non-empty string describing boot state.
    """
    lines = [f"[Boot Protocol — loop {state.loop_id}]"]
    lines.append(f"Project: {state.project} | Method: {state.boot_method} | Boot: {state.boot_timestamp[:19]}Z")

    if state.completed_features:
        lines.append(f"\nCompleted features ({len(state.completed_features)}) — DO NOT redo:")
        for f in state.completed_features[:10]:
            lines.append(f"  - {f}")
        if len(state.completed_features) > 10:
            lines.append(f"  ... and {len(state.completed_features) - 10} more")
    else:
        lines.append("\nNo features completed yet.")

    if state.dead_ends:
        lines.append(f"\nKnown dead ends ({len(state.dead_ends)}) — avoid these approaches:")
        for de in state.dead_ends[:5]:
            lines.append(f"  ! {de}")
    else:
        lines.append("\nNo known dead ends.")

    if state.git_head:
        lines.append(f"\nGit HEAD: {state.git_head}")

    lines.append(
        "\nInstruction: Pick the NEXT uncompleted task. Do NOT redo completed work. "
        "Do NOT declare success without evidence."
    )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Dead ends tracking
# ---------------------------------------------------------------------------

def update_dead_ends(project: str, new_dead_ends: List[str]) -> None:
    """Append new dead ends to DEAD_ENDS.md for a project.

    Creates the file if it doesn't exist. Appends with timestamp.

    Args:
        project:        Project slug.
        new_dead_ends:  List of dead end descriptions to append.
    """
    if not new_dead_ends:
        return

    project_path = _project_path(project)
    try:
        project_path.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    dead_ends_file = project_path / "DEAD_ENDS.md"
    timestamp = datetime.now(timezone.utc).isoformat()[:19] + "Z"

    try:
        if not dead_ends_file.exists():
            dead_ends_file.write_text(
                "# Dead Ends\n\nApproaches tried and failed. Do not repeat these.\n\n",
                encoding="utf-8",
            )
        with open(dead_ends_file, "a", encoding="utf-8") as fh:
            for de in new_dead_ends:
                fh.write(f"\n## [{timestamp}] {de}\n")
    except Exception:
        pass  # Never fatal
