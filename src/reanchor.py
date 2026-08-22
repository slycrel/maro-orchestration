"""
reanchor.py — §9.5 mid-meander coherence re-anchor (COMPOUND_THINKING_DESIGN).

Closure already verifies the finished work against the interpretation the
director-proxy committed to before planning (closure_verify's binding
interpretation block) — that IS the anti-drift check. The hole is cadence:
closure fires at the END, so a long meander can burn its budget on a
drifted string before anything re-anchors. This module runs the same
goal-level question MID-RUN, at milestone boundaries — the moments the
loop already treats as "about to commit budget to a sub-project"
(PlanReview.milestone_step_indices, live again as of 2026-08-15).

Cadence, not mechanism. The question is closure's; the boundary is
pre-flight's; this module only joins them and records what it saw.

This is an EXPERIMENT (§9.5): if the mid-meander question catches real
drift, coherence never needed to be its own signal. Every check — on
course or not — is appended to build/reanchor.jsonl in the active run dir
so the verdicts are adjudicable later, and the map lens renders them as
anchor landmarks. On drift the anchor note feeds the milestone
expansion's ancestry context, re-anchoring the sub-plan about to be
drawn; nothing stops, replans, or escalates on this signal yet
(consumer-first — a heavier corrective must earn its wiring with data).

Non-fatal everywhere: any failure yields an on-course verdict with the
error noted, and recording failures are swallowed. The check must never
block execution.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

log = logging.getLogger("maro.reanchor")

# Section header written by ResolvedIntent.to_markdown (scope.py). Parsed
# back here rather than threading the object through the loop layers —
# artifacts are the interface (2026-07-27 decree), and the file is already
# per-run under runs.source_dir().
_INTERPRETATION_HEADER = "## Resolved interpretation (binding goal definition)"

_REANCHOR_SYSTEM = """\
You are a mid-run coherence checker for an autonomous agent. Before
execution, the run committed to a goal (and possibly a binding
interpretation of it). Execution is now at a milestone boundary — about
to commit significant budget to a sub-project. Your ONE question: does
the work done so far still serve the original commitment?

Drift means the work has substituted a different objective — solving an
adjacent problem, polishing a tangent, or following an interpretation the
run never committed to. Normal intermediate work (setup, research,
partial results, recoverable failures) is NOT drift. Be conservative:
flag drift only when the trajectory visibly serves something other than
the commitment.

Respond ONLY with this JSON (no prose, no markdown):
{
  "on_course": true | false,
  "drift_summary": "<empty if on course, else ONE sentence naming what the work is serving instead>",
  "anchor_note": "<empty if on course, else 1-2 sentences re-anchoring the upcoming milestone to the commitment>"
}"""


@dataclass
class ReanchorVerdict:
    on_course: bool
    drift_summary: str = ""
    anchor_note: str = ""
    error: str = ""
    raw: str = ""


def read_committed_interpretation(source_dir: Optional[Path]) -> str:
    """Extract the binding interpretation from resolved_intent.md, or "".

    Reads the bullet lines under the "Resolved interpretation" section
    header (the exact section ResolvedIntent.to_markdown writes). Returns
    "" when the file or section is absent — the caller anchors on the
    goal text instead.
    """
    if source_dir is None:
        return ""
    try:
        path = Path(source_dir) / "resolved_intent.md"
        if not path.is_file():
            return ""
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    bullets: List[str] = []
    in_section = False
    for line in lines:
        stripped = line.strip()
        if stripped == _INTERPRETATION_HEADER:
            in_section = True
            continue
        if in_section:
            if stripped.startswith("## "):
                break
            if stripped.startswith("- "):
                bullets.append(stripped[2:].strip())
    return "\n".join(b for b in bullets if b)


def check_reanchor(
    goal: str,
    interpretation: str,
    recent_work: str,
    milestone_step: str,
    upcoming_steps: List[str],
    adapter,
) -> ReanchorVerdict:
    """Ask the goal-level coherence question against the commitment.

    Non-fatal: any failure (no adapter, call error, unparseable answer)
    returns on_course=True with the error noted, mirroring closure's
    never-blocks posture. A check that cannot run must not become a
    drift signal.
    """
    if adapter is None:
        return ReanchorVerdict(on_course=True, error="no adapter")
    try:
        from llm import LLMMessage
        from llm_parse import extract_json, safe_str, content_or_empty

        commitment_block = (
            f"Binding interpretation committed before planning:\n{interpretation}\n\n"
            if interpretation
            else "(no binding interpretation was recorded — the goal text is the commitment)\n\n"
        )
        upcoming_lines = [f"  {i + 1}. {s}" for i, s in enumerate(upcoming_steps[:5])]
        if len(upcoming_steps) > 5:
            upcoming_lines.append(f"  ... ({len(upcoming_steps) - 5} more)")
        upcoming = "\n".join(upcoming_lines)
        user_msg = (
            f"Goal: {goal}\n\n"
            f"{commitment_block}"
            f"Work so far (recent step results):\n{recent_work or '(none yet)'}\n\n"
            f"Milestone about to start: {milestone_step}\n\n"
            f"Steps after it:\n{upcoming or '  (none)'}"
        )
        resp = adapter.complete(
            [LLMMessage("system", _REANCHOR_SYSTEM), LLMMessage("user", user_msg)],
            max_tokens=256,
            temperature=0.1,
            timeout=30,
            no_tools=True,
            purpose="milestone re-anchor",
        )
        raw = content_or_empty(resp)
        data = extract_json(raw, dict, log_tag="reanchor")
        if not data:
            return ReanchorVerdict(on_course=True, error="unparseable answer", raw=raw[:500])
        return ReanchorVerdict(
            on_course=bool(data.get("on_course", True)),
            drift_summary=safe_str(data.get("drift_summary", ""), max_len=500),
            anchor_note=safe_str(data.get("anchor_note", ""), max_len=800),
            raw=raw[:500],
        )
    except Exception as exc:
        return ReanchorVerdict(on_course=True, error=f"{type(exc).__name__}: {exc}")


def _record(entry: dict) -> None:
    """Append a check record to build/reanchor.jsonl in the active run dir.

    Swallows everything — the experiment's data channel must never take
    down the run it is observing.
    """
    try:
        from runs import current_run_dir
        rd = current_run_dir()
        if rd is None:
            return
        build = Path(rd) / "build"
        build.mkdir(parents=True, exist_ok=True)
        with (build / "reanchor.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:
        log.debug("reanchor: record failed (non-blocking): %s", exc)


def run_milestone_reanchor(
    *,
    goal: str,
    milestone_step: str,
    step_outcomes: list,
    remaining_steps: List[str],
    adapter,
    loop_id: str = "",
    step_idx: int = 0,
) -> str:
    """The full §9.5 check at one milestone boundary. Returns the anchor
    note to feed the milestone expansion's ancestry context ("" when on
    course or when the check could not run).

    Reads the commitment from the run's resolved_intent.md artifact,
    summarizes the last 3 step outcomes (any status, tagged, each clipped
    at the gate's measured evidence window with a marker), asks the
    coherence question, records the verdict to build/reanchor.jsonl, and
    emits a captain's-log event.
    """
    interpretation = ""
    try:
        from runs import source_dir
        interpretation = read_committed_interpretation(source_dir())
    except Exception:
        pass

    # Last 3 outcomes REGARDLESS of status, each tagged (round-1 review,
    # Architect): a blocked or skipped stretch is often exactly where drift
    # lives — a done-only summary would hide the signal this check exists
    # to catch. Window rides the gate's measured constant (caps sweep
    # 2026-08-21): step results run median 1,180 / p99 4,671 chars, so the
    # old bare [:600] showed this check under half of a typical outcome —
    # the same starved shape quality_gate measured and fixed — and clip()
    # marks the cut so the model can tell trimmed evidence from complete.
    from context_budget import clip
    from quality_gate import _REVIEW_STEP_CUT
    recent_work = "\n---\n".join(
        f"[{getattr(o, 'status', '?') or '?'}] "
        + clip(getattr(o, "result", "") or "", _REVIEW_STEP_CUT)
        for o in step_outcomes[-3:]
    )

    verdict = check_reanchor(
        goal, interpretation, recent_work, milestone_step,
        list(remaining_steps), adapter,
    )

    _record({
        "ts": datetime.now(timezone.utc).isoformat(),
        "loop_id": loop_id,
        "step_idx": step_idx,
        "milestone_step": milestone_step[:200],
        "anchor_source": "interpretation" if interpretation else "goal",
        "on_course": verdict.on_course,
        "drift_summary": verdict.drift_summary,
        "anchor_note": verdict.anchor_note,
        "error": verdict.error,
    })

    try:
        from captains_log import log_event, REANCHOR_CHECKED
        from context_budget import clip
        log_event(
            REANCHOR_CHECKED,
            subject="reanchor",
            summary=(
                f"Milestone boundary step {step_idx}: "
                + ("on course."
                   if verdict.on_course
                   else f"DRIFT — {clip(verdict.drift_summary, 160)}")
                + (f" (check error: {clip(verdict.error, 80)})" if verdict.error else "")
            ),
            context={
                "loop_id": loop_id,
                "step_idx": step_idx,
                "on_course": verdict.on_course,
                "anchor_source": "interpretation" if interpretation else "goal",
            },
            loop_id=loop_id or None,
        )
    except Exception:
        pass

    if verdict.on_course:
        log.info("reanchor: step %d on course (anchor=%s)",
                 step_idx, "interpretation" if interpretation else "goal")
        return ""
    try:
        from context_budget import clip as _clip
        _drift_log = _clip(verdict.drift_summary, 200)
    except Exception:
        _drift_log = verdict.drift_summary
    log.warning("reanchor: DRIFT at step %d — %s", step_idx, _drift_log)
    return verdict.anchor_note
