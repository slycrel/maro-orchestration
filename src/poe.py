#!/usr/bin/env python3
"""Phase 13: Poe CEO layer — top-level entry point for new sessions.

Poe is a communicator and planner, not an executor.

Role contract (enforced here, not just in docs):
  - Poe routes to Director/Mission — never executes steps directly
  - Poe communicates at mission/goal level — doesn't surface step detail by default
  - If the intent is trivial (NOW lane), Poe still handles it directly (CEO, not bureaucrat)

Routing logic:
  1. Classify intent (NOW/AGENDA)
  2. Check autonomy tier — if requires_human, return asking for confirmation
  3. NOW intent → handle directly (1-shot LLM call)
  4. AGENDA → run_mission() if multi-day goal, else run_agent_loop()
  5. /status or status-asking → executive summary
  6. /inspect or quality-asking → latest inspection quality formatted as summary
  7. Goal relationship queries → goal_map traversal

Usage:
    from poe import poe_handle
    response = poe_handle("research winning polymarket strategies")
    print(response.message)

CLI:
    orch poe "message"
    orch poe-status
    orch poe-map
    orch poe-autonomy [--tier TIER] [--project PROJECT] [--action ACTION]
"""

from __future__ import annotations

import sys
import time
import uuid
from dataclasses import dataclass
from typing import Optional


# ---------------------------------------------------------------------------
# Module-level imports (so tests can patch cleanly)
# ---------------------------------------------------------------------------

try:
    from llm import build_adapter, MODEL_CHEAP, MODEL_MID, MODEL_POWER, LLMMessage
except ImportError:  # pragma: no cover
    build_adapter = None  # type: ignore[assignment]
    MODEL_CHEAP = "cheap"  # type: ignore[assignment]
    MODEL_MID = "mid"  # type: ignore[assignment]
    MODEL_POWER = "power"  # type: ignore[assignment]
    LLMMessage = None  # type: ignore[assignment]

try:
    from intent import classify
except ImportError:  # pragma: no cover
    classify = None  # type: ignore[assignment]

try:
    from agent_loop import run_agent_loop, _DryRunAdapter
except ImportError:  # pragma: no cover
    run_agent_loop = None  # type: ignore[assignment]
    _DryRunAdapter = None  # type: ignore[assignment]

try:
    from mission import run_mission, list_missions
except ImportError:  # pragma: no cover
    run_mission = None  # type: ignore[assignment]
    list_missions = None  # type: ignore[assignment]

try:
    from inspector import get_latest_inspection
except ImportError:  # pragma: no cover
    get_latest_inspection = None  # type: ignore[assignment]

try:
    from goal_map import build_goal_map
except ImportError:  # pragma: no cover
    build_goal_map = None  # type: ignore[assignment]

try:
    from autonomy import evaluate_action, ActionRequest, TIER_SAFE
except ImportError:  # pragma: no cover
    evaluate_action = None  # type: ignore[assignment]
    ActionRequest = None  # type: ignore[assignment]
    TIER_SAFE = "safe"  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class PoeResponse:
    message: str           # what to send back to Jeremy
    routed_to: str         # "now_lane" | "mission" | "director" | "inspector" | "status" | "goal_map"
    mission_id: Optional[str] = None
    executive_summary: Optional[str] = None  # Poe's own framing of what happened/is happening


# ---------------------------------------------------------------------------
# Model role assignment
# ---------------------------------------------------------------------------

def assign_model_by_role(role: str) -> str:
    """Map a semantic role to a model tier constant.

    This is a pure function — no I/O, no LLM calls.

    Roles:
      orchestrator | planner | reviewer  → MODEL_POWER
      worker | executor | researcher     → MODEL_MID
      classifier | heartbeat | signal_detector | cheap_worker → MODEL_CHEAP
      (anything else)                    → MODEL_MID
    """
    power_roles = {"orchestrator", "planner", "reviewer"}
    cheap_roles = {"classifier", "heartbeat", "signal_detector", "cheap_worker"}

    if role in power_roles:
        return MODEL_POWER
    if role in cheap_roles:
        return MODEL_CHEAP
    # worker, executor, researcher, and anything unknown → MID
    return MODEL_MID


# ---------------------------------------------------------------------------
# Per-step cost classification (Phase 35 P1)
# ---------------------------------------------------------------------------

# Keywords whose presence suggests a step is cheap (retrieve/format/classify).
# Multi-word phrases match as substrings; single words use word boundaries.
_CHEAP_STEP_KEYWORDS = [
    # retrieval / lookup
    "check if", "check whether", "look up", "find all", "find the", "list all",
    "list the", "fetch the", "get the", "count the", "how many",
    # classification / boolean
    "classify", "categorise", "categorize", "tag", "label", "is it",
    "does it", "true or false", "yes or no", "determine if", "determine whether",
    # formatting / extraction
    "format", "extract all", "extract the", "parse", "convert", "reformat",
    "strip", "clean up", "rename", "sort by",
    # simple summarization of a single known source
    "briefly summarise", "briefly summarize", "one-line summary", "one sentence",
    "tl;dr",
    # verification
    "verify that", "confirm that", "assert that", "validate",
    "check reachability", "abort early if",
]

# Keywords that override cheap classification → keep at MID
_FORCE_MID_KEYWORDS = [
    "synthesize", "synthesise", "analyse in depth", "analyze in depth",
    "write a comprehensive", "write a detailed", "produce a report",
    "research", "investigate", "evaluate", "compare and contrast",
    "design", "implement", "develop", "build", "create a",
    "explain why", "reason about", "infer", "hypothesize",
]


def classify_step_model(step_text: str) -> str:
    """Return MODEL_CHEAP or MODEL_MID based on step text content.

    Cheap steps: retrieval, classification, formatting, boolean checks.
    Mid steps: research, synthesis, writing, analysis, implementation.

    This is a pure keyword heuristic — zero token cost. Aim for high precision
    on CHEAP (don't wrongly downgrade synthesis) over high recall.

    Args:
        step_text: The step description string.

    Returns:
        MODEL_CHEAP or MODEL_MID.
    """
    import re as _re

    text = step_text.lower()

    def _match(kw: str) -> bool:
        if " " in kw:
            return kw in text
        return bool(_re.search(r"\b" + _re.escape(kw) + r"\b", text))

    # If any force-MID keyword present, keep at MID regardless
    if any(_match(kw) for kw in _FORCE_MID_KEYWORDS):
        return MODEL_MID

    # If any cheap keyword present, downgrade to CHEAP
    if any(_match(kw) for kw in _CHEAP_STEP_KEYWORDS):
        return MODEL_CHEAP

    return MODEL_MID


# ---------------------------------------------------------------------------
# Status / inspection helpers (read-only, no execution)
# ---------------------------------------------------------------------------

_EXECUTIVE_SUMMARY_SYSTEM = """\
You are Poe, an autonomous AI executive assistant. Compile a brief executive summary for Jeremy.
Focus on: what's in progress, what completed recently, any quality issues worth knowing.
Be concise — 3-5 bullet points max. Don't list steps or technical detail. Mission-level only.
If there is nothing to report, say so briefly.
"""


def _compile_executive_summary(adapter=None) -> str:
    """Compile a brief executive summary from mission log + inspection data.

    Returns formatted bullet-point summary. Uses LLM if adapter provided,
    otherwise builds a heuristic summary from available data.
    """
    # Gather context
    missions_text = ""
    inspection_text = ""

    # Load active missions
    try:
        if list_missions is not None:
            missions = list_missions()
            if missions:
                active = [m for m in missions if m.get("status") in ("running", "pending")]
                done = [m for m in missions if m.get("status") == "done"]
                stuck = [m for m in missions if m.get("status") in ("stuck", "failed")]
                parts = []
                if active:
                    names = [f"'{m['goal'][:40]}' ({m['project']})" for m in active[:3]]
                    parts.append(f"Active: {', '.join(names)}")
                if done:
                    names = [f"'{m['goal'][:40]}'" for m in done[-3:]]
                    parts.append(f"Recently completed: {', '.join(names)}")
                if stuck:
                    names = [f"'{m['goal'][:40]}'" for m in stuck[:3]]
                    parts.append(f"Stuck: {', '.join(names)}")
                missions_text = "; ".join(parts) if parts else "No missions found."
            else:
                missions_text = "No missions recorded."
    except Exception:
        missions_text = "(mission data unavailable)"

    # Load latest inspection
    try:
        if get_latest_inspection is not None:
            report = get_latest_inspection()
            if report and report.inspected_sessions > 0:
                dist = report.quality_distribution
                inspection_text = (
                    f"Quality: good={dist.get('good', 0)} fair={dist.get('fair', 0)} "
                    f"poor={dist.get('poor', 0)} alignment_avg={report.alignment_score_avg:.2f}"
                )
                if report.threshold_breaches:
                    inspection_text += f" — breaches: {', '.join(report.threshold_breaches)}"
    except Exception:
        pass

    if adapter is None or LLMMessage is None:
        # Heuristic summary
        lines = ["Executive summary:"]
        if missions_text and missions_text not in ("No missions recorded.", "(mission data unavailable)"):
            lines.append(f"- {missions_text}")
        else:
            lines.append("- No active missions.")
        if inspection_text:
            lines.append(f"- {inspection_text}")
        return "\n".join(lines)

    # LLM summary
    context = (
        f"Missions: {missions_text}\n"
        f"Quality inspection: {inspection_text or 'No recent inspection.'}\n"
    )

    try:
        resp = adapter.complete(
            [
                LLMMessage("system", _EXECUTIVE_SUMMARY_SYSTEM),
                LLMMessage("user", f"Current system state:\n{context}\n\nCompile executive summary."),
            ],
            max_tokens=512,
            temperature=0.3,
        )
        return resp.content.strip() or "No summary available."
    except Exception as exc:
        return f"Executive summary unavailable: {exc}"


def _describe_goal_relationships(goal_query: str, adapter=None) -> str:
    """Describe how active missions relate to the queried goal topic.

    Uses ancestry module to traverse the goal hierarchy.
    Returns formatted description.
    """
    try:
        if build_goal_map is None:
            return "Goal map not available."
        gmap = build_goal_map()
        if not gmap.nodes:
            return "No active missions or projects found."

        # Find nodes relevant to the query
        query_lower = goal_query.lower()
        relevant = [
            n for n in gmap.nodes.values()
            if query_lower in n.title.lower() or query_lower in n.id.lower()
        ]

        active = [n for n in gmap.nodes.values() if n.status in ("running", "pending")]
        active_names = [f"'{n.id}' ({n.status})" for n in active[:5]]

        lines = [f"Active missions: {', '.join(active_names) if active_names else 'none'}"]

        if relevant:
            lines.append(f"\nRelated to your query ({goal_query!r}):")
            for node in relevant[:4]:
                ancestors = gmap.ancestors_of(node.id)
                chain = " → ".join([a.id for a in reversed(ancestors)] + [node.id])
                lines.append(f"  {chain} [{node.status}]")
        else:
            lines.append(f"\nNo missions directly related to {goal_query!r}.")

        conflicts = gmap.find_conflicts()
        if conflicts:
            lines.append(f"\nConflicts detected:")
            for c in conflicts:
                lines.append(f"  ! {c}")

        return "\n".join(lines)
    except Exception as exc:
        return f"Goal relationship query failed: {exc}"


# ---------------------------------------------------------------------------
# Status keyword detection
# ---------------------------------------------------------------------------

_STATUS_KEYWORDS = {
    "/status", "status", "what's happening", "what is happening",
    "what are you working on", "update me", "progress report",
    "how are things", "executive summary", "summary",
}

_INSPECT_KEYWORDS = {
    "/inspect", "/quality", "quality", "how well", "how good",
    "inspection", "friction", "alignment", "inspector",
}

_GOAL_MAP_KEYWORDS = {
    "/map", "goal map", "mission map", "relates to",
    "relationship between", "how do these relate",
    "how does this relate", "how does poe relate",
}


def _looks_like_status(message: str) -> bool:
    ml = message.lower().strip()
    return any(kw in ml for kw in _STATUS_KEYWORDS)


def _looks_like_inspect(message: str) -> bool:
    ml = message.lower().strip()
    return any(kw in ml for kw in _INSPECT_KEYWORDS)


def _looks_like_goal_map(message: str) -> bool:
    ml = message.lower().strip()
    return any(kw in ml for kw in _GOAL_MAP_KEYWORDS)


def _looks_like_multi_day(goal: str) -> bool:
    """Heuristic: does this goal sound like a multi-day mission?"""
    keywords = {
        "build", "create", "develop", "implement", "design", "architect",
        "research and", "analyze and", "deploy", "pipeline", "system",
        "platform", "full", "complete", "end-to-end",
    }
    gl = goal.lower()
    return any(kw in gl for kw in keywords) and len(goal.split()) > 6


# ---------------------------------------------------------------------------
# Core poe_handle function
# ---------------------------------------------------------------------------

def poe_handle(
    message: str,
    *,
    adapter=None,
    model: Optional[str] = None,
    dry_run: bool = False,
) -> PoeResponse:
    """Process a request through the Poe CEO layer.

    Poe routes — never executes steps directly.

    Args:
        message: Natural language request or slash command.
        adapter: Pre-built LLMAdapter (auto-built if None and not dry_run).
        model: LLM model string override.
        dry_run: Simulate without API calls.

    Returns:
        PoeResponse with message and routing info.
    """
    # dry_run path — no LLM calls, no imports needed
    if dry_run:
        if _looks_like_status(message):
            return PoeResponse(
                message="[dry-run] Executive summary: no active missions.",
                routed_to="status",
                executive_summary="[dry-run]",
            )
        if _looks_like_inspect(message):
            return PoeResponse(
                message="[dry-run] No inspection report available.",
                routed_to="inspector",
            )
        if _looks_like_goal_map(message):
            return PoeResponse(
                message="[dry-run] Goal map: empty.",
                routed_to="goal_map",
            )
        # Default dry-run: route as NOW lane
        return PoeResponse(
            message=f"[dry-run] Would handle: {message[:80]}",
            routed_to="now_lane",
        )

    # Build adapter if not provided
    if adapter is None and build_adapter is not None:
        _model = model or assign_model_by_role("classifier")
        try:
            adapter = build_adapter(model=_model)
        except Exception:
            adapter = None

    # --- Status / inspect / map routing (no autonomy check needed — read-only) ---

    if _looks_like_status(message):
        summary = _compile_executive_summary(adapter=adapter)
        return PoeResponse(
            message=summary,
            routed_to="status",
            executive_summary=summary,
        )

    if _looks_like_inspect(message):
        try:
            if get_latest_inspection is not None:
                report = get_latest_inspection()
                if report and report.inspected_sessions > 0:
                    dist = report.quality_distribution
                    msg = (
                        f"Quality report ({report.run_id}):\n"
                        f"- Sessions: {report.inspected_sessions}\n"
                        f"- Quality: good={dist.get('good',0)} fair={dist.get('fair',0)} poor={dist.get('poor',0)}\n"
                        f"- Alignment avg: {report.alignment_score_avg:.2f}"
                    )
                    if report.threshold_breaches:
                        msg += f"\n- Breaches: {', '.join(report.threshold_breaches)}"
                    if report.suggestions:
                        msg += f"\n- Top suggestion: {report.suggestions[0][:100]}"
                else:
                    msg = "No inspection report available. Run poe-inspector first."
            else:
                msg = "Inspector not available."
        except Exception as exc:
            msg = f"Inspection query failed: {exc}"
        return PoeResponse(message=msg, routed_to="inspector")

    if _looks_like_goal_map(message):
        goal_desc = _describe_goal_relationships(message, adapter=adapter)
        return PoeResponse(message=goal_desc, routed_to="goal_map")

    # --- Intent classification ---

    lane = "agenda"
    confidence = 0.6
    reason = "default"

    if classify is not None and adapter is not None:
        try:
            lane, confidence, reason = classify(message, adapter=adapter)
        except Exception:
            lane = "agenda"

    # --- Autonomy check ---
    if evaluate_action is not None and ActionRequest is not None:
        action_type = "run_mission" if lane == "agenda" else "execute_step"
        req = ActionRequest(
            action_type=action_type,
            project="",
            description=message[:200],
            is_reversible=True,
            estimated_cost_usd=0.0,
        )
        decision = evaluate_action(req)
        if decision.requires_human:
            return PoeResponse(
                message=(
                    f"This action requires your approval before I proceed.\n"
                    f"Action: {action_type}\n"
                    f"Reason: {decision.reason}\n"
                    f"Reply 'approved' or 'go ahead' to proceed."
                ),
                routed_to="status",
            )

    # --- NOW lane ---

    if lane == "now":
        now_response = _handle_now_lane(message, adapter=adapter)
        return PoeResponse(
            message=now_response,
            routed_to="now_lane",
        )

    # --- AGENDA lane ---

    # Route to mission layer if this sounds like a multi-day goal
    if _looks_like_multi_day(message) and run_mission is not None:
        try:
            result = run_mission(message, dry_run=False, verbose=False)
            summary = (
                f"Mission launched: {result.goal[:60]}\n"
                f"Project: {result.project}\n"
                f"Status: {result.status}\n"
                f"Milestones: {result.milestones_done}/{result.milestones_total}\n"
                f"Features: {result.features_done}/{result.features_total}"
            )
            return PoeResponse(
                message=summary,
                routed_to="mission",
                mission_id=result.mission_id,
                executive_summary=summary,
            )
        except Exception as exc:
            # Fall through to agent loop
            pass

    # Shorter AGENDA work → agent loop (with persona auto-selection)
    if run_agent_loop is not None:
        try:
            # Phase 31: select the best persona for this goal and inject system context
            _persona_context = ""
            _persona_name_selected = ""
            _persona_conf_selected = 0.0
            try:
                from persona import persona_for_goal, PersonaRegistry, build_persona_system_prompt
                _registry = PersonaRegistry()
                _persona_name_selected, _persona_conf_selected = persona_for_goal(
                    message, registry=_registry, confidence_threshold=0.75
                )
                _spec = _registry.load(_persona_name_selected)
                if _spec:
                    # Apply skeptic modifier when goal is prefixed with "skeptic:" or
                    # contains "--skeptic" flag (stripped from message before execution)
                    _skeptic = (
                        message.lower().startswith("skeptic:")
                        or "--skeptic" in message.lower()
                    )
                    if _skeptic:
                        from persona import apply_skeptic_modifier
                        _spec = apply_skeptic_modifier(_spec)
                    _persona_context = build_persona_system_prompt(_spec, goal=message)
            except Exception:
                _persona_context = ""

            loop_result = run_agent_loop(
                message,
                adapter=adapter,
                dry_run=False,
                verbose=False,
                ancestry_context_extra=_persona_context,
            )

            # Phase 31: record persona outcome for feedback loop
            if _persona_name_selected:
                try:
                    from persona import record_persona_outcome
                    record_persona_outcome(
                        persona_name=_persona_name_selected,
                        goal=message,
                        status=loop_result.status,
                        confidence=_persona_conf_selected,
                        loop_id=loop_result.loop_id,
                    )
                except Exception:
                    pass

            done_steps = sum(1 for s in loop_result.steps if s.status == "done")
            summary = (
                f"Task completed.\n"
                f"Steps: {done_steps}/{len(loop_result.steps)}\n"
                f"Status: {loop_result.status}"
            )
            if loop_result.status == "stuck" and loop_result.stuck_reason:
                summary += f"\nStuck: {loop_result.stuck_reason}"
            return PoeResponse(
                message=summary,
                routed_to="director",
                executive_summary=summary,
            )
        except Exception as exc:
            return PoeResponse(
                message=f"Task failed to execute: {exc}",
                routed_to="director",
            )

    # Fallback
    return PoeResponse(
        message=f"Received: {message[:80]}. No executor available.",
        routed_to="now_lane",
    )


# ---------------------------------------------------------------------------
# NOW lane helper (1-shot, no step execution)
# ---------------------------------------------------------------------------

_NOW_SYSTEM = """\
You are Poe, an autonomous AI assistant.
Answer the user's request directly and completely. Be thorough but concise.
If the request is a question, answer it. If it's a task, complete it.
Do not hedge or defer — just do the work.
"""


def _handle_now_lane(message: str, adapter=None) -> str:
    """Execute a NOW-lane task: single LLM call."""
    if adapter is None or LLMMessage is None:
        return f"[no adapter] Would answer: {message[:80]}"

    try:
        resp = adapter.complete(
            [
                LLMMessage("system", _NOW_SYSTEM),
                LLMMessage("user", message),
            ],
            max_tokens=2048,
            temperature=0.4,
        )
        return resp.content.strip() or "[no response]"
    except Exception as exc:
        return f"Error: {exc}"
