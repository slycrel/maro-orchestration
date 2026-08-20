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
    injections: Optional[List[dict]] = None,
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
            # §6a after-the-fact delineation: operator interrupts applied
            # mid-run, injected content kept distinct from the goal above.
            # When a corrective interrupt changed the goal, the record's
            # goal_before/goal_after carries the original.
            "injections": injections or [],
            "steps": [
                {
                    "index": s.index,
                    "text": s.text,
                    "status": s.status,
                    "result_length": len(s.result),
                    "iteration": s.iteration,
                    "tokens_in": s.tokens_in,
                    "tokens_out": s.tokens_out,
                    "provider_cost_usd": getattr(s, "provider_cost_usd", 0.0),
                    "executor_session_id": getattr(s, "executor_session_id", ""),
                    "executor_session_resumed": getattr(
                        s, "executor_session_resumed", False),
                    "elapsed_ms": s.elapsed_ms,
                    # rung-4 unification (BACKLOG #0): link the truncated view
                    # to the full byte-level capture when record-mode had one
                    "call_record": getattr(s, "call_record", ""),
                    # run-visibility report: when this step finished, for
                    # timeline positioning (loop_report.py)
                    "ended_ts": getattr(s, "ended_ts", ""),
                    # Origin story (2026-08-18). started_ts makes the timeline
                    # measured instead of derived; the gap to the previous
                    # step's ended_ts is the replan/verify/hook time that the
                    # cumulative-sum fallback used to fold into a step's own
                    # duration. venue is where the executor call ACTUALLY ran
                    # (config records intent; mode "on" degrades to host when
                    # docker is down). model/tier make the cheap->mid->power
                    # retry ladder measurable — cost was recorded, the model
                    # that spent it was not.
                    "started_ts": getattr(s, "started_ts", ""),
                    "venue": getattr(s, "venue", ""),
                    "model": getattr(s, "model", ""),
                    "model_tier": getattr(s, "model_tier", ""),
                    "tier_escalated_from": getattr(s, "tier_escalated_from", ""),
                    # fail-open denominator (2026-08-06 readout): "judged" |
                    # "unjudged" | "" (check not run for this step)
                    "artifact_check": getattr(s, "artifact_check", ""),
                }
                for s in steps
            ],
            "totals": {
                "steps_done": sum(1 for s in steps if s.status == "done"),
                "steps_blocked": sum(1 for s in steps if s.status == "blocked"),
                "tokens_in": sum(s.tokens_in for s in steps),
                "tokens_out": sum(s.tokens_out for s in steps),
                "provider_cost_usd": sum(
                    getattr(s, "provider_cost_usd", 0.0) for s in steps),
                "executor_session_resumed_steps": sum(
                    1 for s in steps
                    if getattr(s, "executor_session_resumed", False)),
                # How many steps actually ran isolated. The C4 flip is gated on
                # burn-in evidence and this is the numerator for it.
                "steps_containerized": sum(
                    1 for s in steps
                    if str(getattr(s, "venue", "")).startswith("container:")),
                "steps_on_host": sum(
                    1 for s in steps if getattr(s, "venue", "") == "host"),
                "steps_tier_escalated": sum(
                    1 for s in steps if getattr(s, "tier_escalated_from", "")),
                # judged + unjudged < len(steps) is normal: steps the check
                # never sees (gate off, blocked, empty result) count neither.
                "artifact_checks_judged": sum(
                    1 for s in steps
                    if getattr(s, "artifact_check", "") == "judged"),
                "artifact_checks_unjudged": sum(
                    1 for s in steps
                    if getattr(s, "artifact_check", "") == "unjudged"),
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


# Words that carry no subject: function words, the imperatives goals open
# with, and the shape nouns that name the *kind* of thing rather than the
# thing ("book", "article", "repo"). Used only as a GUARD — a word missing
# from this set means resolve_project_slug degrades to today's behavior
# (reuse the colliding project), never to a new hazard. That asymmetry is
# why a phrase list is acceptable here and wasn't for building the slug.
_GENERIC_WORDS = frozenset("""
a an the this that these those it its my our your their his her
and or but not for from with without into onto over under about above
of on in to at by as is are was were be been being do does did done
i me we us you they them he she who what which when where why how
please can could should would may might will shall let lets need needs
tell told say said give given show shown ask asked find found get got
look looking see seen read reading write writing make making create
creating build building fix fixing check checking research researching
review reviewing analyze analyzing summarize summarizing explain
explaining update updating add adding run running test testing use using
help helping work working try trying take taking put putting
new old more most some any all every each other another same
summary report overview analysis notes note thing things stuff item items
book books article articles paper papers file files code repo repos
project projects doc docs document documents page pages site sites
thread threads post posts video videos story stories task tasks
""".split())

# Longest disambiguator we'll try before falling back to a goal hash.
_SLUG_DISAMBIGUATION_CAP = 20


def _subject_words(text: str) -> set:
    """Content words of a goal — the part that says what it is *about*.

    Drops punctuation, generic words, and anything under 3 characters
    (initials and digits carry no reliable subject signal).
    """
    import re
    words = re.sub(r"[^a-z0-9 ]", " ", (text or "").lower()).split()
    return {w for w in words if len(w) >= 3 and w not in _GENERIC_WORDS}


def _slug_is_generic(slug: str) -> bool:
    """True when a slug's words name no subject — "tell-me-about-the-book".

    A slug carrying two or more subject words ("research-the-chlorination-of-
    water") is specific enough that a collision means two goals about the
    same thing, which is what slugs are for. One or zero means the slug is
    pure phrasing and a collision proves nothing.
    """
    return len(_subject_words(slug.replace("-", " "))) <= 1


def _recorded_mission(slug: str) -> str:
    """The goal a project recorded when it was created, "" if unreadable."""
    try:
        o = _orch()
        text = o.next_path(slug).read_text(encoding="utf-8")
    except Exception:
        return ""
    # ensure_project writes "Mission:\n\n> <goal>\n"
    for line in text.splitlines():
        line = line.strip()
        if line.startswith(">"):
            return line.lstrip("> ").strip()
    return ""


def _same_subject(goal: str, mission: str, slug: str) -> bool:
    """Do two goals sharing a generic slug talk about the same thing?

    Compares the *distinguishing tail* — subject words outside the slug the
    two share by construction. One shared tail word is enough: the bug being
    guarded against is two goals with nothing in common (Systemantics vs
    Notes on the Synthesis of Form), and biasing toward "same" keeps every
    real continuity family intact. Missing evidence (no mission recorded, no
    tail on either side) reads as same — today's behavior.
    """
    if not mission:
        return True
    slug_words = set(slug.split("-"))
    tail_a = _subject_words(goal) - slug_words
    tail_b = _subject_words(mission) - slug_words
    if not tail_a or not tail_b:
        return True
    return bool(tail_a & tail_b)


def resolve_project_slug(goal: str) -> str:
    """Project slug for a goal, disambiguated when a generic opening would
    otherwise merge it into an unrelated project.

    ``_goal_to_slug`` is the first five words and nothing else, so goals
    that open the way people actually open them — "tell me about the
    book…", "write a summary of…" — collide on phrasing alone and the
    second goal inherits the first's artifacts as its own prior work. The
    guards can't catch that: those files really are present.

    Two conditions must both hold before this changes anything, so
    subject-collisions (the continuity mechanism) are untouched:
    the slug must carry no subject, AND the recorded mission of the
    project it hit must share no subject word with the incoming goal.
    Only then does the goal get its own ``-2``, ``-3``… directory —
    re-entered on later runs, since the same mission check matches it.
    """
    base = _goal_to_slug(goal)
    try:
        o = _orch()
        if not o.project_dir(base).exists():
            return base
        if not _slug_is_generic(base):
            return base
        if _same_subject(goal, _recorded_mission(base), base):
            return base
        for n in range(2, _SLUG_DISAMBIGUATION_CAP + 1):
            cand = f"{base}-{n}"
            if not o.project_dir(cand).exists():
                return cand
            if _same_subject(goal, _recorded_mission(cand), base):
                return cand
        # Cap reached: a hash still beats silently merging unrelated work.
        import hashlib
        return f"{base}-{hashlib.sha256(goal.encode('utf-8')).hexdigest()[:8]}"
    except Exception:
        return base
