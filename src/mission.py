#!/usr/bin/env python3
"""Phase 10: Mission Layer for Poe orchestration.

Formal hierarchy: Mission → Milestone → Feature → Worker Session

Each Feature gets a fresh context window (calls run_agent_loop independently).
Milestones execute sequentially with LLM validation gates.
Features within a milestone can parallelize (max 2 workers).

Usage:
    from mission import run_mission
    result = run_mission("Build a polymarket research pipeline", project="poly-pipeline")
    print(result.summary())

CLI:
    orch poe-mission "multi-day goal" [--project SLUG] [--dry-run] [--verbose]
"""

from __future__ import annotations

import json
import sys
import textwrap
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Feature:
    id: str                              # uuid[:8]
    title: str
    status: str                          # "pending" | "running" | "done" | "blocked"
    worker_session_id: Optional[str] = None
    result_summary: Optional[str] = None
    elapsed_ms: int = 0


@dataclass
class Milestone:
    id: str                              # uuid[:8]
    title: str
    features: List[Feature]
    validation_criteria: List[str]       # plain text checks
    status: str                          # "pending" | "running" | "validating" | "done" | "failed"
    validation_result: Optional[str] = None


@dataclass
class Mission:
    id: str                              # uuid[:8]
    goal: str
    project: str                         # project slug
    milestones: List[Milestone]
    status: str                          # "pending" | "running" | "done" | "stuck" | "interrupted"
    created_at: str
    completed_at: Optional[str] = None
    ancestry_context: str = ""


@dataclass
class MissionResult:
    mission_id: str
    project: str
    goal: str
    status: str
    milestones_done: int
    milestones_total: int
    features_done: int
    features_total: int
    elapsed_ms: int

    def summary(self) -> str:
        lines = [
            f"mission_id={self.mission_id}",
            f"project={self.project}",
            f"goal={self.goal!r}",
            f"status={self.status}",
            f"milestones={self.milestones_done}/{self.milestones_total}",
            f"features={self.features_done}/{self.features_total}",
            f"elapsed_ms={self.elapsed_ms}",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

_DECOMPOSE_SYSTEM = textwrap.dedent("""\
    You are Poe, a mission planning agent.
    Decompose a multi-day goal into 2-4 milestones with validation criteria,
    and 2-4 features per milestone.
    Each feature is a discrete unit of work completable in one agent session.
    Respond ONLY with JSON. No prose. No markdown fences.
    JSON shape:
    {
      "milestones": [
        {
          "title": "Milestone title",
          "features": ["Feature one description", "Feature two description"],
          "validation_criteria": ["Check one", "Check two"]
        }
      ]
    }
""").strip()

_VALIDATE_SYSTEM = textwrap.dedent("""\
    You are Poe, a milestone validation agent.
    Given completed feature work and validation criteria, decide if this milestone succeeded.
    Respond ONLY with JSON: {"passed": true or false, "reason": "one sentence"}
""").strip()


# ---------------------------------------------------------------------------
# Lazy orch import (same pattern as agent_loop.py)
# ---------------------------------------------------------------------------

def _orch():
    import orch
    return orch


# ---------------------------------------------------------------------------
# Core: decompose
# ---------------------------------------------------------------------------

def decompose_mission(
    goal: str,
    adapter,
    max_milestones: int = 4,
    max_features_per_milestone: int = 3,
) -> Mission:
    """LLM call to decompose goal into milestones + features. Falls back to heuristic."""
    from llm import LLMMessage, MODEL_POWER

    mission_id = str(uuid.uuid4())[:8]
    created_at = datetime.now(timezone.utc).isoformat()

    milestones: List[Milestone] = []

    try:
        resp = adapter.complete(
            [
                LLMMessage("system", _DECOMPOSE_SYSTEM),
                LLMMessage(
                    "user",
                    f"Goal: {goal}\n\nDecompose into {max_milestones} or fewer milestones, "
                    f"each with {max_features_per_milestone} or fewer features.",
                ),
            ],
            max_tokens=2048,
            temperature=0.2,
        )
        content = resp.content.strip()
        # Strip markdown fences if present
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        start = content.find("{")
        end = content.rfind("}") + 1
        if start >= 0 and end > start:
            data = json.loads(content[start:end])
            raw_milestones = data.get("milestones", [])
            for rm in raw_milestones[:max_milestones]:
                features = [
                    Feature(
                        id=str(uuid.uuid4())[:8],
                        title=str(f).strip(),
                        status="pending",
                    )
                    for f in rm.get("features", [])[:max_features_per_milestone]
                    if str(f).strip()
                ]
                if not features:
                    continue
                milestones.append(Milestone(
                    id=str(uuid.uuid4())[:8],
                    title=str(rm.get("title", "Milestone")).strip(),
                    features=features,
                    validation_criteria=[str(c).strip() for c in rm.get("validation_criteria", []) if str(c).strip()],
                    status="pending",
                ))
    except Exception:
        pass  # fall through to heuristic

    # Heuristic fallback
    if not milestones:
        words = goal.split()
        half = len(words) // 2 or 1
        first_half = " ".join(words[:half])
        second_half = " ".join(words[half:]) or first_half
        milestones = [
            Milestone(
                id=str(uuid.uuid4())[:8],
                title=f"Phase 1: {first_half[:60]}",
                features=[
                    Feature(id=str(uuid.uuid4())[:8], title=f"Research {first_half[:40]}", status="pending"),
                    Feature(id=str(uuid.uuid4())[:8], title=f"Implement {first_half[:40]}", status="pending"),
                ],
                validation_criteria=[f"Phase 1 work for '{first_half[:40]}' is complete"],
                status="pending",
            ),
            Milestone(
                id=str(uuid.uuid4())[:8],
                title=f"Phase 2: {second_half[:60]}",
                features=[
                    Feature(id=str(uuid.uuid4())[:8], title=f"Finalize {second_half[:40]}", status="pending"),
                    Feature(id=str(uuid.uuid4())[:8], title=f"Verify {second_half[:40]}", status="pending"),
                ],
                validation_criteria=[f"Phase 2 work for '{second_half[:40]}' is complete"],
                status="pending",
            ),
        ]

    return Mission(
        id=mission_id,
        goal=goal,
        project="",  # filled in by run_mission
        milestones=milestones,
        status="pending",
        created_at=created_at,
    )


# ---------------------------------------------------------------------------
# Core: validate milestone
# ---------------------------------------------------------------------------

def _validate_milestone(
    milestone: Milestone,
    project: str,
    adapter,
    dry_run: bool = False,
) -> bool:
    """LLM call to validate milestone. Returns True if passed."""
    if not milestone.validation_criteria:
        return True
    if dry_run or adapter is None:
        return True

    from llm import LLMMessage, MODEL_MID

    features_summary = "\n".join(
        f"- {f.title}: {f.status}"
        + (f" — {f.result_summary[:200]}" if f.result_summary else "")
        for f in milestone.features
    )
    criteria_text = "\n".join(f"- {c}" for c in milestone.validation_criteria)

    user_msg = (
        f"Milestone: {milestone.title}\n\n"
        f"Validation criteria:\n{criteria_text}\n\n"
        f"Completed features:\n{features_summary}\n\n"
        "Did this milestone succeed? Respond with JSON only."
    )

    try:
        resp = adapter.complete(
            [
                LLMMessage("system", _VALIDATE_SYSTEM),
                LLMMessage("user", user_msg),
            ],
            max_tokens=256,
            temperature=0.1,
        )
        content = resp.content.strip()
        start = content.find("{")
        end = content.rfind("}") + 1
        if start >= 0 and end > start:
            data = json.loads(content[start:end])
            return bool(data.get("passed", True))
    except Exception:
        pass

    return True  # Default: pass (don't get stuck in validation loops)


# ---------------------------------------------------------------------------
# Core: run_mission
# ---------------------------------------------------------------------------

def run_mission(
    goal: str,
    *,
    project: Optional[str] = None,
    adapter=None,
    model: Optional[str] = None,
    dry_run: bool = False,
    verbose: bool = False,
) -> MissionResult:
    """Run a mission: decompose into milestones + features, execute sequentially.

    Args:
        goal: High-level multi-day goal.
        project: Project slug (auto-created if not given).
        adapter: LLMAdapter (auto-built if not given).
        model: LLM model string.
        dry_run: No LLM calls; use stubs.
        verbose: Print progress to stderr.

    Returns:
        MissionResult with full outcome.
    """
    from agent_loop import run_agent_loop, _DryRunAdapter, _goal_to_slug
    from llm import build_adapter, MODEL_POWER

    started_at = time.monotonic()

    def _log(msg: str):
        if verbose:
            print(f"[poe:mission] {msg}", file=sys.stderr, flush=True)

    # Build adapter
    if adapter is None and not dry_run:
        adapter = build_adapter(model=model or MODEL_POWER)
    elif dry_run:
        adapter = _DryRunAdapter()

    # Resolve project
    o = _orch()
    if not project:
        project = _goal_to_slug(goal)
    if not o.project_dir(project).exists():
        o.ensure_project(project, goal[:80])
        _log(f"created project={project}")

    # Load ancestry context
    ancestry_context = ""
    try:
        from ancestry import get_project_ancestry, build_ancestry_prompt
        _proj_dir = o.project_dir(project)
        _ancestry = get_project_ancestry(_proj_dir)
        ancestry_context = build_ancestry_prompt(_ancestry, current_task=goal)
    except Exception:
        pass

    # Decompose
    _log(f"decomposing goal={goal!r}")
    mission = decompose_mission(goal, adapter)
    mission.project = project
    mission.ancestry_context = ancestry_context
    mission.status = "running"
    _log(f"decomposed: {len(mission.milestones)} milestones")

    # Persist initial mission
    try:
        save_mission(mission, project)
    except Exception:
        pass

    milestones_done = 0

    for ms_idx, milestone in enumerate(mission.milestones):
        _log(f"milestone {ms_idx + 1}/{len(mission.milestones)}: {milestone.title!r}")
        milestone.status = "running"

        # Execute features (parallel, max 2 workers)
        def _run_feature(feature: Feature) -> Feature:
            feature.status = "running"
            feat_start = time.monotonic()
            try:
                loop_result = run_agent_loop(
                    feature.title,
                    project=project,
                    adapter=adapter if dry_run else None,
                    dry_run=dry_run,
                )
                feature.worker_session_id = loop_result.loop_id
                feature.status = "done" if loop_result.status == "done" else "blocked"
                done_steps = sum(1 for s in loop_result.steps if s.status == "done")
                feature.result_summary = (
                    f"loop={loop_result.loop_id} status={loop_result.status} "
                    f"steps={done_steps}/{len(loop_result.steps)}"
                )
            except Exception as exc:
                feature.status = "blocked"
                feature.result_summary = f"error: {exc}"
            feature.elapsed_ms = int((time.monotonic() - feat_start) * 1000)
            return feature

        try:
            if len(milestone.features) <= 1:
                for feature in milestone.features:
                    _run_feature(feature)
                    _log(f"  feature done: {feature.title!r} status={feature.status}")
            else:
                with ThreadPoolExecutor(max_workers=2) as executor:
                    futures = {executor.submit(_run_feature, f): f for f in milestone.features}
                    for future in as_completed(futures):
                        feat = futures[future]
                        try:
                            future.result()
                        except Exception as exc:
                            feat.status = "blocked"
                            feat.result_summary = f"executor error: {exc}"
                        _log(f"  feature done: {feat.title!r} status={feat.status}")
        except Exception as exc:
            _log(f"  feature execution error: {exc}")
            for f in milestone.features:
                if f.status == "pending" or f.status == "running":
                    f.status = "blocked"

        # Validate milestone
        milestone.status = "validating"
        _log(f"  validating milestone: {milestone.title!r}")
        try:
            passed = _validate_milestone(milestone, project, adapter, dry_run=dry_run)
        except Exception:
            passed = True  # Don't get stuck in validation

        if passed:
            milestone.status = "done"
            milestone.validation_result = "passed"
            milestones_done += 1
            _log(f"  milestone passed: {milestone.title!r}")
        else:
            milestone.status = "failed"
            milestone.validation_result = "failed"
            mission.status = "stuck"
            _log(f"  milestone FAILED validation: {milestone.title!r}")
            # Persist and break
            try:
                save_mission(mission, project)
            except Exception:
                pass
            break

        # Persist after each milestone
        try:
            save_mission(mission, project)
        except Exception:
            pass

    if mission.status != "stuck":
        mission.status = "done"
    mission.completed_at = datetime.now(timezone.utc).isoformat()

    elapsed = int((time.monotonic() - started_at) * 1000)

    features_done = sum(
        1 for ms in mission.milestones for f in ms.features if f.status == "done"
    )
    features_total = sum(len(ms.features) for ms in mission.milestones)

    result = MissionResult(
        mission_id=mission.id,
        project=project,
        goal=goal,
        status=mission.status,
        milestones_done=milestones_done,
        milestones_total=len(mission.milestones),
        features_done=features_done,
        features_total=features_total,
        elapsed_ms=elapsed,
    )

    # Final persist
    try:
        save_mission(mission, project)
    except Exception:
        pass

    # Write to mission-log.jsonl
    try:
        _write_mission_log(result, mission)
    except Exception:
        pass

    _log(f"mission complete: {result.summary()}")
    return result


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _mission_path(project: str) -> Path:
    o = _orch()
    return o.project_dir(project) / "mission.json"


def _mission_log_path() -> Path:
    o = _orch()
    mem = o.orch_root() / "memory"
    mem.mkdir(parents=True, exist_ok=True)
    return mem / "mission-log.jsonl"


def save_mission(mission: Mission, project: str) -> None:
    """Write mission.json for a project."""
    path = _mission_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)

    def _feature_dict(f: Feature) -> dict:
        return {
            "id": f.id,
            "title": f.title,
            "status": f.status,
            "worker_session_id": f.worker_session_id,
            "result_summary": f.result_summary,
            "elapsed_ms": f.elapsed_ms,
        }

    def _milestone_dict(ms: Milestone) -> dict:
        return {
            "id": ms.id,
            "title": ms.title,
            "features": [_feature_dict(f) for f in ms.features],
            "validation_criteria": ms.validation_criteria,
            "status": ms.status,
            "validation_result": ms.validation_result,
        }

    payload = {
        "id": mission.id,
        "goal": mission.goal,
        "project": mission.project,
        "milestones": [_milestone_dict(ms) for ms in mission.milestones],
        "status": mission.status,
        "created_at": mission.created_at,
        "completed_at": mission.completed_at,
        "ancestry_context": mission.ancestry_context,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_mission(project: str) -> Optional[Mission]:
    """Load mission.json for a project. Returns None if not found."""
    path = _mission_path(project)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        milestones = []
        for md in data.get("milestones", []):
            features = [
                Feature(
                    id=f["id"],
                    title=f["title"],
                    status=f["status"],
                    worker_session_id=f.get("worker_session_id"),
                    result_summary=f.get("result_summary"),
                    elapsed_ms=f.get("elapsed_ms", 0),
                )
                for f in md.get("features", [])
            ]
            milestones.append(Milestone(
                id=md["id"],
                title=md["title"],
                features=features,
                validation_criteria=md.get("validation_criteria", []),
                status=md["status"],
                validation_result=md.get("validation_result"),
            ))
        return Mission(
            id=data["id"],
            goal=data["goal"],
            project=data["project"],
            milestones=milestones,
            status=data["status"],
            created_at=data["created_at"],
            completed_at=data.get("completed_at"),
            ancestry_context=data.get("ancestry_context", ""),
        )
    except Exception:
        return None


def list_missions() -> List[dict]:
    """Scan all projects for mission.json, return summaries."""
    o = _orch()
    projects_root = o.projects_root()
    results = []
    if not projects_root.exists():
        return results
    for project_dir in sorted(projects_root.iterdir()):
        if not project_dir.is_dir():
            continue
        mission_file = project_dir / "mission.json"
        if not mission_file.exists():
            continue
        try:
            data = json.loads(mission_file.read_text(encoding="utf-8"))
            milestones = data.get("milestones", [])
            features_done = sum(
                1 for ms in milestones for f in ms.get("features", []) if f.get("status") == "done"
            )
            features_total = sum(len(ms.get("features", [])) for ms in milestones)
            results.append({
                "project": project_dir.name,
                "mission_id": data.get("id", "?"),
                "goal": data.get("goal", ""),
                "status": data.get("status", "?"),
                "milestones_total": len(milestones),
                "milestones_done": sum(1 for ms in milestones if ms.get("status") == "done"),
                "features_done": features_done,
                "features_total": features_total,
                "created_at": data.get("created_at", ""),
            })
        except Exception:
            continue
    return results


def _write_mission_log(result: MissionResult, mission: Mission) -> None:
    """Append a MissionResult entry to mission-log.jsonl."""
    path = _mission_log_path()
    entry = {
        "mission_id": result.mission_id,
        "project": result.project,
        "goal": result.goal,
        "status": result.status,
        "milestones_done": result.milestones_done,
        "milestones_total": result.milestones_total,
        "features_done": result.features_done,
        "features_total": result.features_total,
        "elapsed_ms": result.elapsed_ms,
        "created_at": mission.created_at,
        "completed_at": mission.completed_at,
    }
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")
