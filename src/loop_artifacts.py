"""Artifact/manifest/log writers for the agent loop (Tier 3 split of agent_loop.py).

Extracted verbatim from agent_loop.py — writers for per-step artifacts, the
human-readable plan manifest, the full loop-log JSON, and the goal-to-slug
helper used to derive project directory names.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

from loop_types import _orch, _project_dir_root, StepOutcome
from step_exec import _classify_step

# ---------------------------------------------------------------------------
# Artifact writing
# ---------------------------------------------------------------------------

def _write_step_artifact(
    project: str,
    loop_id: str,
    step_num: int,
    step_text: str,
    result: str,
) -> Optional[str]:
    """Write a step's result to the project artifacts directory."""
    try:
        o = _orch()
        try:
            from runs import artifact_dir as _runs_artifact_dir
            artifacts_dir = _runs_artifact_dir(project, project_root_fn=_project_dir_root)
        except Exception:
            artifacts_dir = _project_dir_root() / project / "artifacts"
            artifacts_dir.mkdir(parents=True, exist_ok=True)
        fname = f"loop-{loop_id}-step-{step_num:02d}.md"
        path = artifacts_dir / fname
        content = f"# Step {step_num}: {step_text}\n\n{result}\n"
        path.write_text(content, encoding="utf-8")
        return o.relative_display_path(path)
    except Exception:
        return None


def _plan_manifest_path(project: str, loop_id: str) -> Optional[Path]:
    """Return path for the human-readable plan manifest file."""
    if not project:
        return None
    try:
        o = _orch()
        try:
            from runs import artifact_dir as _runs_artifact_dir
            artifacts_dir = _runs_artifact_dir(project, project_root_fn=_project_dir_root)
        except Exception:
            artifacts_dir = o.projects_root() / project / "artifacts"
            artifacts_dir.mkdir(parents=True, exist_ok=True)
        return artifacts_dir / f"loop-{loop_id}-plan.md"
    except Exception:
        return None


def _write_plan_manifest(
    project: str,
    loop_id: str,
    goal: str,
    planned_steps: List[str],
    start_ts: str,
    step_outcomes: Optional[List[StepOutcome]] = None,
    *,
    status: str = "running",
    elapsed_ms: int = 0,
    replan_count: int = 0,
) -> Optional[str]:
    """Write (or overwrite) the human-readable run plan manifest.

    Emitted immediately after decomposition (step_outcomes=[]) so the full
    plan is visible before execution begins. Overwritten after each step with
    current progress. Always human-readable — this is the primary debugging
    artifact for in-flight runs.

    Returns path written (relative to orch_root) or None on failure.
    """
    path = _plan_manifest_path(project, loop_id)
    if path is None:
        return None

    step_outcomes = step_outcomes or []
    _by_idx: Dict[int, StepOutcome] = {s.index: s for s in step_outcomes}
    _done = sum(1 for s in step_outcomes if s.status == "done")
    _blocked = sum(1 for s in step_outcomes if s.status == "blocked")
    _total = len(planned_steps)

    replan_note = f"  **Replans:** {replan_count}" if replan_count else ""
    header = [
        f"# Run Plan — `{loop_id}`",
        f"**Project:** {project}  **Goal:** {goal[:120]}",
        f"**Started:** {start_ts}  **Status:** {status}  "
        f"**Progress:** {_done}/{_total} done, {_blocked} blocked{replan_note}",
        "",
        f"## Steps ({_total} planned)",
        "",
    ]

    step_lines = []
    for i, step_text in enumerate(planned_steps, 1):
        outcome = _by_idx.get(i)
        step_type = _classify_step(step_text)
        _type_tag = f" `[{step_type}]`" if step_type != "general" else ""
        if outcome is None:
            icon = "⬜"
            suffix = ""
        elif outcome.status == "done":
            icon = "✅"
            t_total = outcome.tokens_in + outcome.tokens_out
            try:
                from metrics import estimate_cost as _est
                cost_str = f" | ${_est(outcome.tokens_in, outcome.tokens_out, cache_read_tokens=getattr(outcome, 'cache_read_tokens', 0)):.4f}"
            except Exception:
                cost_str = ""
            suffix = f" | {outcome.elapsed_ms}ms | {t_total} tok{cost_str}"
        else:
            icon = "❌"
            suffix = f" | {outcome.elapsed_ms}ms"
        step_lines.append(f"{i}. {icon}{_type_tag} {step_text[:120]}{suffix}")

    exec_lines: List[str] = []
    if step_outcomes:
        exec_lines = ["", "## Execution Log", ""]
        for _pos, s in enumerate(step_outcomes, start=1):
            icon = "✅" if s.status == "done" else "❌"
            t_total = s.tokens_in + s.tokens_out
            # s.index is the NEXT.md ledger line, not plan position.
            exec_lines.append(
                f"### Step {_pos} (ledger #{s.index}) {icon}"
                f" | {s.elapsed_ms}ms | {t_total} tok")
            exec_lines.append(f"**{s.text[:120]}**")
            blurb = getattr(s, "summary", None) or s.result
            if blurb:
                exec_lines.append(f"> {blurb[:300]}")
            exec_lines.append("")

    footer: List[str] = []
    if status != "running":
        footer = [
            "---",
            f"**Final:** {status} | {_done}/{_total} done | {_blocked} blocked"
            + (f" | {elapsed_ms}ms total" if elapsed_ms else ""),
        ]

    content = "\n".join(header + step_lines + exec_lines + footer) + "\n"
    try:
        path.write_text(content, encoding="utf-8")
        try:
            o = _orch()
            return o.relative_display_path(path)
        except Exception:
            return str(path)
    except Exception:
        return None


def _write_loop_log(
    project: str,
    loop_id: str,
    goal: str,
    status: str,
    steps: List[StepOutcome],
    start_ts: str,
    elapsed_ms: int,
    stuck_reason: Optional[str],
) -> Optional[str]:
    """Write the full loop log JSON to the project artifacts directory."""
    try:
        o = _orch()
        try:
            from runs import artifact_dir as _runs_artifact_dir
            artifacts_dir = _runs_artifact_dir(project, project_root_fn=_project_dir_root)
        except Exception:
            artifacts_dir = _project_dir_root() / project / "artifacts"
            artifacts_dir.mkdir(parents=True, exist_ok=True)
        fname = f"loop-{loop_id}-log.json"
        path = artifacts_dir / fname
        payload = {
            "loop_id": loop_id,
            "project": project,
            "goal": goal,
            "status": status,
            "started_at": start_ts,
            "elapsed_ms": elapsed_ms,
            "stuck_reason": stuck_reason,
            "steps": [
                {
                    "index": s.index,
                    "text": s.text,
                    "status": s.status,
                    "result_length": len(s.result),
                    "iteration": s.iteration,
                    "tokens_in": s.tokens_in,
                    "tokens_out": s.tokens_out,
                    "elapsed_ms": s.elapsed_ms,
                    # rung-4 unification (BACKLOG #0): link the truncated view
                    # to the full byte-level capture when record-mode had one
                    "call_record": getattr(s, "call_record", ""),
                    # run-visibility report: when this step finished, for
                    # timeline positioning (loop_report.py)
                    "ended_ts": getattr(s, "ended_ts", ""),
                }
                for s in steps
            ],
            "totals": {
                "steps_done": sum(1 for s in steps if s.status == "done"),
                "steps_blocked": sum(1 for s in steps if s.status == "blocked"),
                "tokens_in": sum(s.tokens_in for s in steps),
                "tokens_out": sum(s.tokens_out for s in steps),
            },
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return o.relative_display_path(path)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Slug helper
# ---------------------------------------------------------------------------

def _goal_to_slug(goal: str) -> str:
    """Convert a goal string to a filesystem-safe project slug."""
    import re
    words = re.sub(r"[^a-z0-9 ]", "", goal.lower()).split()
    slug = "-".join(words[:5])
    return slug or "unnamed-goal"
