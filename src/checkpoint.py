# @lat: [[checkpointing]]
"""Session checkpoint — write per-step progress so loops can resume mid-run.

Addresses research GAP 3: no mechanism to resume a prior loop from where it
stopped. Long-running goals can be resumed from the last completed step
instead of restarting from scratch.

Checkpoint format (JSON per file):
    {
        "loop_id": "abc12345",
        "goal": "...",
        "project": "...",
        "steps": ["step 1", "step 2", ...],
        "completed": [{"index": 1, "text": "...", "status": "done", "result": "..."}],
        "timestamp": "2026-04-01T12:00:00Z"
    }

Usage:
    from checkpoint import write_checkpoint, load_checkpoint, resume_from

    # After each step:
    write_checkpoint(loop_id, goal, project, all_steps, step_outcomes_so_far)

    # On resume:
    ckpt = load_checkpoint(loop_id)
    if ckpt:
        steps, completed = resume_from(ckpt)
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("poe.checkpoint")

_CHECKPOINT_DIR_NAME = "checkpoints"


def _checkpoint_dir() -> Path:
    """Resolve checkpoint storage directory."""
    try:
        from orch_items import orch_root
        d = orch_root() / _CHECKPOINT_DIR_NAME
    except Exception:
        d = Path(__file__).parent.parent / _CHECKPOINT_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def _checkpoint_path(loop_id: str) -> Path:
    return _checkpoint_dir() / f"ckpt_{loop_id}.json"


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class CompletedStep:
    index: int
    text: str
    status: str
    result: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    elapsed_ms: int = 0


@dataclass
class Checkpoint:
    loop_id: str
    goal: str
    project: str
    steps: List[str]
    completed: List[CompletedStep]
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    @property
    def next_step_index(self) -> int:
        """Zero-based index of the next step to execute."""
        if not self.completed:
            return 0
        return max(s.index for s in self.completed)

    @property
    def remaining_steps(self) -> List[str]:
        """Steps not yet completed (by position in steps list)."""
        done_indices = {s.index for s in self.completed}
        return [s for i, s in enumerate(self.steps, 1) if i not in done_indices]

    def is_complete(self) -> bool:
        """True if all steps have an outcome."""
        return len(self.completed) >= len(self.steps)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "loop_id": self.loop_id,
            "goal": self.goal,
            "project": self.project,
            "steps": self.steps,
            "completed": [asdict(c) for c in self.completed],
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Checkpoint":
        completed = [
            CompletedStep(**c) if isinstance(c, dict) else c
            for c in d.get("completed", [])
        ]
        return cls(
            loop_id=d["loop_id"],
            goal=d["goal"],
            project=d.get("project", ""),
            steps=d.get("steps", []),
            completed=completed,
            timestamp=d.get("timestamp", ""),
        )


# ---------------------------------------------------------------------------
# Core API
# ---------------------------------------------------------------------------


def write_checkpoint(
    loop_id: str,
    goal: str,
    project: str,
    steps: List[str],
    step_outcomes: List[Any],  # List[StepOutcome] — avoid circular import
) -> None:
    """Write current loop progress to disk.

    Safe to call after every step — overwrites previous checkpoint for the
    same loop_id. Swallows all errors: a failed checkpoint write must never
    abort a running loop.

    Args:
        loop_id: Unique ID for this loop run.
        goal: Original goal text.
        project: Project slug.
        steps: Full list of planned steps (all, including future ones).
        step_outcomes: List of StepOutcome objects completed so far.
    """
    try:
        completed = [
            CompletedStep(
                index=getattr(s, "index", i + 1),
                text=getattr(s, "text", ""),
                status=getattr(s, "status", ""),
                result=getattr(s, "result", ""),
                tokens_in=getattr(s, "tokens_in", 0),
                tokens_out=getattr(s, "tokens_out", 0),
                elapsed_ms=getattr(s, "elapsed_ms", 0),
            )
            for i, s in enumerate(step_outcomes)
        ]
        ckpt = Checkpoint(
            loop_id=loop_id,
            goal=goal,
            project=project,
            steps=steps,
            completed=completed,
        )
        path = _checkpoint_path(loop_id)
        path.write_text(json.dumps(ckpt.to_dict(), indent=2), encoding="utf-8")
        log.debug("checkpoint written: %s (%d/%d steps)", loop_id, len(completed), len(steps))
    except Exception as exc:
        log.debug("checkpoint write failed (non-fatal): %s", exc)


def load_checkpoint(loop_id: str) -> Optional[Checkpoint]:
    """Load a checkpoint by loop_id. Returns None if not found or corrupt."""
    try:
        path = _checkpoint_path(loop_id)
        data = json.loads(path.read_text(encoding="utf-8"))
        return Checkpoint.from_dict(data)
    except FileNotFoundError:
        return None
    except Exception as exc:
        log.warning("checkpoint load failed for %s: %s", loop_id, exc)
        return None


def delete_checkpoint(loop_id: str) -> None:
    """Delete a checkpoint file after a loop completes successfully."""
    try:
        _checkpoint_path(loop_id).unlink(missing_ok=True)
        log.debug("checkpoint deleted: %s", loop_id)
    except Exception:
        pass


def list_checkpoints() -> List[Checkpoint]:
    """List all saved checkpoints, newest first."""
    ckpts = []
    try:
        for p in sorted(_checkpoint_dir().glob("ckpt_*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                ckpts.append(Checkpoint.from_dict(data))
            except Exception:
                pass
    except Exception:
        pass
    return ckpts


def resume_from(ckpt: Checkpoint) -> tuple[List[str], List[CompletedStep]]:
    """Extract resumable state from a checkpoint.

    Returns:
        (remaining_steps, already_completed) — caller skips completed steps
        and resumes execution from remaining_steps.
    """
    return ckpt.remaining_steps, ckpt.completed


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli_main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Poe checkpoint manager")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("list", help="List all saved checkpoints")

    load_p = sub.add_parser("show", help="Show checkpoint details")
    load_p.add_argument("loop_id", help="Loop ID to show")

    del_p = sub.add_parser("delete", help="Delete a checkpoint")
    del_p.add_argument("loop_id", help="Loop ID to delete")

    args = parser.parse_args()

    if args.cmd == "list":
        ckpts = list_checkpoints()
        if not ckpts:
            print("No checkpoints found.")
            return
        for c in ckpts:
            done = len(c.completed)
            total = len(c.steps)
            print(f"{c.loop_id}  {done}/{total}  {c.timestamp[:19]}  {c.goal[:60]}")
    elif args.cmd == "show":
        c = load_checkpoint(args.loop_id)
        if c is None:
            print(f"No checkpoint found for {args.loop_id}")
            return
        print(json.dumps(c.to_dict(), indent=2))
    elif args.cmd == "delete":
        delete_checkpoint(args.loop_id)
        print(f"Deleted checkpoint {args.loop_id}")
    else:
        parser.print_help()


if __name__ == "__main__":
    _cli_main()
