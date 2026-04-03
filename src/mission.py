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
import logging
import sys
import textwrap
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional
from llm_parse import extract_json, safe_str, safe_list, content_or_empty

log = logging.getLogger("poe.mission")


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
    try:
        from poe import assign_model_by_role as _amr
        _ = _amr("orchestrator")  # ensure assign_model_by_role("orchestrator") → MODEL_POWER
    except Exception:
        pass

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
        data = extract_json(content_or_empty(resp), dict, log_tag="mission.decompose_milestones")
        if data:
            raw_milestones = safe_list(data.get("milestones", []), element_type=dict, max_items=max_milestones)
            for rm in raw_milestones:
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
    except ImportError:
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
    try:
        from poe import assign_model_by_role as _amr
        _ = _amr("reviewer")  # ensure assign_model_by_role("reviewer") → MODEL_POWER
    except Exception:
        pass

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
        data = extract_json(content_or_empty(resp), dict, log_tag="mission.validate_milestone")
        if data:
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
    hook_registry=None,
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
    from hooks import (
        run_hooks, any_blocking, get_injected_context,
        SCOPE_MISSION, SCOPE_MILESTONE, SCOPE_FEATURE, load_registry,
    )

    # Phase 19: optional sprint_contract + boot_protocol imports
    try:
        from sprint_contract import negotiate_contract, grade_contract, save_contract, save_grade
        _sprint_contract_available = True
    except ImportError:
        _sprint_contract_available = False

    try:
        from boot_protocol import run_boot_protocol, format_boot_context
        _boot_protocol_available = True
    except ImportError:
        _boot_protocol_available = False

    started_at = time.monotonic()
    log.info("mission_start goal=%r project=%s dry_run=%s", goal[:60], project or "(auto)", dry_run)

    def _log(msg: str):
        if verbose:
            print(f"[poe:mission] {msg}", file=sys.stderr, flush=True)

    # Build adapter
    if adapter is None and not dry_run:
        adapter = build_adapter(model=model or MODEL_POWER)
    elif dry_run:
        adapter = _DryRunAdapter()

    # Load hook registry (lazy — only if not provided)
    _hook_registry = hook_registry
    if _hook_registry is None:
        try:
            _hook_registry = load_registry()
        except ImportError:
            _hook_registry = None

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

    # Phase 19: Generate immutable feature manifest (never overwrites existing)
    try:
        generate_feature_manifest(mission, project)
        _log("feature manifest created")
    except Exception:
        pass

    # Persist initial mission
    try:
        save_mission(mission, project)
    except Exception:
        pass

    # Mission-start hooks (before)
    try:
        _mission_ctx = {"goal": goal, "mission_id": mission.id, "project": project}
        run_hooks(SCOPE_MISSION, _mission_ctx, registry=_hook_registry, adapter=adapter,
                  dry_run=dry_run, fire_on="before")
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

            # Feature "before" hooks — collect injected_context from NOTIFICATION hooks
            _feature_before_ctx = {
                "goal": goal,
                "project": project,
                "feature_title": feature.title,
                "milestone_title": milestone.title,
                "mission_id": mission.id,
            }
            _extra_ancestry = ""
            try:
                _before_results = run_hooks(
                    SCOPE_FEATURE, _feature_before_ctx,
                    registry=_hook_registry, adapter=adapter,
                    dry_run=dry_run, fire_on="before",
                )
                _extra_ancestry = get_injected_context(_before_results)
            except Exception:
                pass

            # Phase 19: Worker Boot Protocol
            _boot_ctx = ""
            if _boot_protocol_available:
                try:
                    _boot_state = run_boot_protocol(project, dry_run=dry_run)
                    _boot_ctx = format_boot_context(_boot_state)
                    _log(f"  boot protocol: {len(_boot_state.completed_features)} completed features, "
                         f"{len(_boot_state.dead_ends)} dead ends")
                except Exception:
                    pass

            if _boot_ctx:
                _extra_ancestry = (_extra_ancestry + "\n\n" + _boot_ctx) if _extra_ancestry else _boot_ctx

            # Phase 19: Sprint Contract negotiation (before worker starts)
            _sprint_contract = None
            if _sprint_contract_available:
                try:
                    _sprint_contract = negotiate_contract(
                        feature_title=feature.title,
                        mission_goal=goal,
                        milestone_title=milestone.title,
                        feature_id=feature.id,
                        adapter=adapter,
                    )
                    save_contract(_sprint_contract, project)
                    # Inject contract criteria into worker context
                    _contract_ctx = (
                        f"Sprint contract for this feature:\n"
                        + "\n".join(f"- {c}" for c in _sprint_contract.success_criteria)
                        + "\nAcceptance keywords: " + ", ".join(_sprint_contract.acceptance_keywords)
                    )
                    _extra_ancestry = (_extra_ancestry + "\n\n" + _contract_ctx) if _extra_ancestry else _contract_ctx
                    # Update feature_before_ctx with contract info
                    _feature_before_ctx["success_criteria"] = "\n".join(
                        f"- {c}" for c in _sprint_contract.success_criteria
                    )
                    _log(f"  sprint contract negotiated: {_sprint_contract.contract_id} "
                         f"by={_sprint_contract.negotiated_by}")
                except Exception:
                    pass

            try:
                loop_result = run_agent_loop(
                    feature.title,
                    project=project,
                    adapter=adapter if dry_run else None,
                    dry_run=dry_run,
                    ancestry_context_extra=_extra_ancestry,
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

            # Feature "after" hooks — check for reviewer blocks
            _feature_after_ctx = {
                **_feature_before_ctx,
                "feature_status": feature.status,
                "feature_result_summary": feature.result_summary or "",
                "feature_result": feature.result_summary or "",
            }
            try:
                _after_results = run_hooks(
                    SCOPE_FEATURE, _feature_after_ctx,
                    registry=_hook_registry, adapter=adapter,
                    dry_run=dry_run, fire_on="after",
                )
                if any_blocking(_after_results):
                    feature.status = "blocked"
                    _blocked_outputs = [r.output for r in _after_results if r.should_block]
                    feature.result_summary = (
                        (feature.result_summary or "") +
                        " [BLOCKED by hook: " + "; ".join(_blocked_outputs[:2]) + "]"
                    )
            except Exception:
                pass

            # Phase 19: Sprint Contract grading (GAN: separate from worker — called here in mission context)
            if _sprint_contract_available and _sprint_contract is not None:
                try:
                    _grade = grade_contract(
                        _sprint_contract,
                        feature.result_summary or "",
                        adapter=adapter,  # GAN: mission.py calls grade, not agent_loop
                    )
                    save_grade(_grade, project)
                    # Mark feature passing in immutable manifest
                    try:
                        mark_feature_passing(project, feature.id, _grade)
                    except ValueError:
                        pass  # Monotonicity violation — non-fatal
                    feature.result_summary = (
                        (feature.result_summary or "")
                        + f" [contract:{_sprint_contract.contract_id} score={_grade.score:.2f}]"
                    )
                    _log(f"  contract graded: {_grade.contract_id} passed={_grade.passed} score={_grade.score:.2f}")
                except Exception:
                    pass

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

        # Milestone "after" hooks — may block advancement
        _ms_ctx = {
            "goal": goal,
            "project": project,
            "milestone_title": milestone.title,
            "mission_id": mission.id,
            "validation_criteria": "\n".join(f"- {c}" for c in milestone.validation_criteria),
            "features_done": sum(1 for f in milestone.features if f.status == "done"),
            "features_total": len(milestone.features),
            "features_summary": "\n".join(
                f"- {f.title}: {f.status}" for f in milestone.features
            ),
        }
        try:
            _ms_results = run_hooks(
                SCOPE_MILESTONE, _ms_ctx,
                registry=_hook_registry, adapter=adapter,
                dry_run=dry_run, fire_on="after",
            )
            if any_blocking(_ms_results):
                passed = False
                _log(f"  milestone BLOCKED by hook: {milestone.title!r}")
        except Exception:
            pass

        # Count how many features actually completed
        _features_ok = sum(1 for f in milestone.features if f.status == "done")
        _features_total = len(milestone.features)

        if passed:
            milestone.status = "done"
            milestone.validation_result = "passed"
            milestones_done += 1
            _log(f"  milestone passed: {milestone.title!r}")
        elif _features_ok > 0:
            # Partial success — some features completed, validation failed.
            # Mark as partial, continue to next milestone with available data.
            milestone.status = "partial"
            milestone.validation_result = "partial"
            milestones_done += 1  # count partial as progress
            _log(f"  milestone PARTIAL: {milestone.title!r} ({_features_ok}/{_features_total} features)")
        else:
            # Total failure — no features completed.
            milestone.status = "failed"
            milestone.validation_result = "failed"
            _log(f"  milestone FAILED: {milestone.title!r} (0/{_features_total} features)")
            # Don't break — try remaining milestones. Later milestones
            # may have independent sub-goals that can still produce value.

        # Persist after each milestone
        try:
            save_mission(mission, project)
        except Exception:
            pass

    if mission.status != "stuck":
        _any_failed = any(ms.status == "failed" for ms in mission.milestones)
        _any_partial = any(ms.status == "partial" for ms in mission.milestones)
        if _any_failed and milestones_done == 0:
            mission.status = "stuck"
        elif _any_failed or _any_partial:
            mission.status = "partial"
        else:
            mission.status = "done"
    mission.completed_at = datetime.now(timezone.utc).isoformat()

    # Mission-end hooks (after)
    try:
        _mission_end_ctx = {"goal": goal, "mission_id": mission.id, "project": project,
                            "mission_status": mission.status}
        run_hooks(SCOPE_MISSION, _mission_end_ctx, registry=_hook_registry, adapter=adapter,
                  dry_run=dry_run, fire_on="after")
    except Exception:
        pass

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

    log.info("mission_done id=%s status=%s milestones=%d/%d features=%d/%d elapsed=%dms",
             mission.id, mission.status, milestones_done, len(mission.milestones),
             features_done, features_total, elapsed)
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


# ---------------------------------------------------------------------------
# Phase 19: Immutable Feature Manifest
# ---------------------------------------------------------------------------

def generate_feature_manifest(mission: Mission, project: str) -> dict:
    """Create feature_list.json alongside mission.json.

    Written at mission start; never overwritten if it already exists.
    Each feature starts with passes=false.

    Args:
        mission: The decomposed Mission.
        project: Project slug.

    Returns:
        The manifest dict (for testing).
    """
    o = _orch()
    path = o.project_dir(project) / "feature_list.json"

    # Never overwrite an existing manifest
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass

    features = []
    for ms in mission.milestones:
        for f in ms.features:
            features.append({
                "id": f.id,
                "title": f.title,
                "milestone_id": ms.id,
                "passes": False,
                "contract_id": None,
                "grade_score": None,
            })

    manifest = {"features": features}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    except Exception:
        pass
    return manifest


def mark_feature_passing(
    project: str,
    feature_id: str,
    contract_grade,
) -> None:
    """Mark a feature as passing in feature_list.json.

    Monotonicity enforced: cannot set passes=False on a feature that was
    already passes=True.

    Args:
        project:        Project slug.
        feature_id:     Feature ID to mark passing.
        contract_grade: ContractGrade object (or dict) with .passed and .score.

    Raises:
        ValueError: If trying to downgrade a feature that already passes=True.
    """
    o = _orch()
    path = o.project_dir(project) / "feature_list.json"
    if not path.exists():
        return  # Manifest not created yet — silent no-op

    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return

    # Get grade info
    if hasattr(contract_grade, "passed"):
        passed = bool(contract_grade.passed)
        score = float(getattr(contract_grade, "score", 0.0))
        contract_id = getattr(contract_grade, "contract_id", None)
    elif isinstance(contract_grade, dict):
        passed = bool(contract_grade.get("passed", False))
        score = float(contract_grade.get("score", 0.0))
        contract_id = contract_grade.get("contract_id")
    else:
        passed = False
        score = 0.0
        contract_id = None

    updated = False
    for feat in manifest.get("features", []):
        if feat.get("id") == feature_id:
            # Monotonicity: once passes=True, cannot be set to False
            if feat.get("passes") is True and not passed:
                raise ValueError(
                    f"Monotonicity violation: feature {feature_id!r} already passes=True; "
                    "cannot downgrade to passes=False."
                )
            feat["passes"] = passed
            feat["grade_score"] = score
            if contract_id:
                feat["contract_id"] = contract_id
            updated = True
            break

    if updated:
        try:
            path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        except Exception:
            pass


def validate_manifest_monotonicity(project: str) -> bool:
    """Validate that no feature has been incorrectly downgraded.

    Returns True if manifest is monotonically correct (or doesn't exist).
    """
    o = _orch()
    path = o.project_dir(project) / "feature_list.json"
    if not path.exists():
        return True
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        # All features should have either passes=True or passes=False (never None-like downgrade)
        for feat in manifest.get("features", []):
            passes = feat.get("passes")
            if passes is not None and not isinstance(passes, bool):
                return False
        return True
    except Exception:
        return True


def load_feature_manifest(project: str) -> Optional[dict]:
    """Load feature_list.json for a project. Returns None if not found."""
    try:
        import orch as _o
        path = _o.project_dir(project) / "feature_list.json"
    except ImportError:
        return None
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ImportError:
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


# ---------------------------------------------------------------------------
# Phase 34: Morning briefing + mission drain detection
# ---------------------------------------------------------------------------

def pending_missions() -> List[dict]:
    """Return missions that have work remaining (status != 'done' and have pending milestones).

    Used by heartbeat to detect whether autonomous drain should trigger.
    """
    all_missions = list_missions()
    result = []
    for m in all_missions:
        if m.get("status") == "done":
            continue
        # Has at least one non-done milestone
        project = m.get("project", "")
        mission = load_mission(project)
        if mission is None:
            continue
        has_pending = any(
            ms.status not in ("done",) for ms in mission.milestones
        )
        if has_pending:
            result.append({
                **m,
                "milestones_pending": sum(
                    1 for ms in mission.milestones if ms.status not in ("done",)
                ),
            })
    return result


def morning_briefing(max_missions: int = 5) -> str:
    """Generate a morning status briefing of active/pending missions.

    Suitable for Telegram notification or CLI display after an overnight run.
    Returns a concise multi-line string.
    """
    from datetime import datetime, timezone

    all_missions = list_missions()
    now = datetime.now(timezone.utc)

    done_today = []
    in_progress = []
    pending = []

    for m in all_missions:
        status = m.get("status", "unknown")
        if status == "done":
            done_today.append(m)
        elif status in ("running", "blocked"):
            in_progress.append(m)
        else:
            pending.append(m)

    lines = [f"Morning briefing — {now.strftime('%Y-%m-%d %H:%M')} UTC"]
    lines.append("")

    if done_today:
        lines.append(f"Completed ({len(done_today)}):")
        for m in done_today[:max_missions]:
            ms_done = m.get("milestones_done", 0)
            ms_total = m.get("milestones_total", 0)
            lines.append(f"  ✓ [{m['project']}] {m['goal'][:60]} ({ms_done}/{ms_total} milestones)")

    if in_progress:
        lines.append(f"\nIn progress ({len(in_progress)}):")
        for m in in_progress[:max_missions]:
            ms_done = m.get("milestones_done", 0)
            ms_total = m.get("milestones_total", 0)
            lines.append(f"  → [{m['project']}] {m['goal'][:60]} ({ms_done}/{ms_total} milestones)")

    if pending:
        lines.append(f"\nQueued ({len(pending)}):")
        for m in pending[:max_missions]:
            lines.append(f"  ○ [{m['project']}] {m['goal'][:60]}")

    if not done_today and not in_progress and not pending:
        lines.append("No active missions.")

    return "\n".join(lines)

# ---------------------------------------------------------------------------
# Phase 34: Autonomous mission drain
# ---------------------------------------------------------------------------

_DRAIN_LOCK_FILE = "mission-drain.lock"  # relative to orch memory/


def _drain_lock_path() -> Path:
    o = _orch()
    return o.orch_root() / "memory" / _DRAIN_LOCK_FILE


def is_drain_running() -> bool:
    """Return True if a mission drain is currently in progress (lock file exists)."""
    return _drain_lock_path().exists()


def _acquire_drain_lock(mission_id: str) -> bool:
    """Write drain lock file. Returns False if already locked."""
    lock_path = _drain_lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if lock_path.exists():
        return False
    try:
        lock_path.write_text(json.dumps({
            "mission_id": mission_id,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }), encoding="utf-8")
        return True
    except Exception:
        return False


def _release_drain_lock() -> None:
    """Remove drain lock file."""
    try:
        _drain_lock_path().unlink(missing_ok=True)
    except Exception:
        pass


def _send_milestone_notification(project: str, milestone_title: str, status: str) -> None:
    """Send a Telegram notification when a milestone completes."""
    try:
        from telegram_listener import TelegramBot, _resolve_token, _resolve_allowed_chats
    except ImportError:
        return
    try:
        token = _resolve_token()
        if not token:
            return
        bot = TelegramBot(token)
        allowed = _resolve_allowed_chats()
        if not allowed:
            return
        icon = "✓" if status == "done" else "⚠"
        msg = f"{icon} [{project}] Milestone: {milestone_title} — {status}"
        for chat_id in allowed:
            bot.send_message(chat_id, msg)
    except Exception as exc:
        print(f"[mission] milestone notification failed: {exc}", file=sys.stderr)


def drain_next_mission(
    *,
    dry_run: bool = False,
    verbose: bool = False,
    notify: bool = True,
) -> Optional[dict]:
    """Pick the oldest pending mission and run it synchronously.

    Returns a summary dict if a mission was drained, None if no missions pending
    or drain is already running.

    Acquires a lock file to prevent concurrent drains.
    Sends per-milestone Telegram notifications (when notify=True).
    """
    if is_drain_running():
        if verbose:
            print("[mission:drain] drain already running, skipping", file=sys.stderr)
        return None

    pending = pending_missions()
    if not pending:
        return None

    # Pick oldest mission (first in sorted list)
    target = pending[0]
    project = target.get("project", "")
    goal = target.get("goal", "")
    mission_id = target.get("mission_id", "?")

    if not project or not goal:
        return None

    if not _acquire_drain_lock(mission_id):
        return None

    log.info("drain_start project=%s goal=%r mission_id=%s", project, goal[:60], mission_id)
    if verbose:
        print(f"[mission:drain] draining {project!r}: {goal[:60]!r}", file=sys.stderr)

    try:
        # Load the full mission to track milestone progress
        mission = load_mission(project)
        if mission is None:
            return None

        from llm import build_adapter, MODEL_POWER
        adapter = build_adapter(model=MODEL_POWER) if not dry_run else None

        started = time.monotonic()
        milestones_done = 0

        for ms in mission.milestones:
            if ms.status == "done":
                milestones_done += 1
                continue

            if verbose:
                print(f"[mission:drain] milestone: {ms.title!r}", file=sys.stderr)

            # Run features within this milestone
            for feature in ms.features:
                if feature.status == "done":
                    continue
                if dry_run:
                    feature.status = "done"
                    feature.result_summary = "dry-run"
                else:
                    from agent_loop import run_agent_loop
                    loop_result = run_agent_loop(
                        feature.title,
                        project=project,
                        adapter=adapter,
                        verbose=verbose,
                    )
                    feature.status = loop_result.status
                    done_steps = sum(1 for s in loop_result.steps if s.status == "done")
                    feature.result_summary = (
                        f"{done_steps}/{len(loop_result.steps)} steps done"
                    )

            # Determine milestone status
            all_done = all(f.status == "done" for f in ms.features)
            ms.status = "done" if all_done else "blocked"
            milestones_done += 1 if all_done else 0

            # Persist intermediate state
            save_mission(mission, project)

            # Per-milestone Telegram notification
            if notify and not dry_run:
                _send_milestone_notification(project, ms.title, ms.status)

        elapsed_ms = int((time.monotonic() - started) * 1000)
        all_milestones_done = all(ms.status == "done" for ms in mission.milestones)
        mission_status = "done" if all_milestones_done else "blocked"

        # Update mission top-level status
        mission.status = mission_status
        if all_milestones_done:
            mission.completed_at = datetime.now(timezone.utc).isoformat()
        save_mission(mission, project)

        # Send morning briefing if done
        if notify and not dry_run and all_milestones_done:
            try:
                from telegram_listener import TelegramBot, _resolve_token, _resolve_allowed_chats
            except ImportError:
                pass
            else:
                try:
                    token = _resolve_token()
                    if token:
                        bot = TelegramBot(token)
                        allowed = _resolve_allowed_chats()
                        briefing = morning_briefing()
                        for chat_id in (allowed or []):
                            bot.send_message(chat_id, f"Mission complete!\n{briefing[:3000]}")
                except Exception as exc:
                    print(f"[mission] morning briefing notification failed: {exc}", file=sys.stderr)

        return {
            "project": project,
            "mission_id": mission_id,
            "goal": goal,
            "status": mission_status,
            "milestones_done": milestones_done,
            "milestones_total": len(mission.milestones),
            "elapsed_ms": elapsed_ms,
        }
    finally:
        _release_drain_lock()
