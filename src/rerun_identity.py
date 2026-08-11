"""Re-run identity — deterministic prior-attempts brief at intake.

A re-dispatched goal should KNOW it's a re-run, with prior art, instead of
rediscovering (or misreading) its own history (Jeremy, 2026-08-09; BACKLOG
"Re-run identity"). The specimens, one per layer: the dispatch navigator
escalated at conf 0.95 quoting a since-restamped false verdict served
unattributed by top-k recall ("prior attempts failed by claiming
completion" — one-sided sample, no standing, no sight of the successful
attempts); and runs 2-4 of the same series paid steps for project-dir
archaeology every time ("ls -la to confirm whether FINAL_REPORT.md
actually exists").

Mechanism: handle() appends {handle_id, raw_input, ts} to
memory/handle_inputs.jsonl for every dispatch. A normalized exact-text
match over that record yields the COMPLETE list of prior attempts with
zero LLM spend — complete-by-construction, which is exactly what fixes the
navigator's one-sided-sample failure (recall's top-k snippets stay as the
fuzzy/semantic complement). Each attempt's verdict is read WITH STANDING
from its run metadata (closure / operator_restamp / contested), so a
corrected or contested record can never silently read as plain failure.

Consumers:
- the dispatch navigator (handle_queue -> shadow_dispatch_live ->
  NavigatorInput.prior_attempts_block): its prior for "verbatim
  re-dispatch, latest attempt succeeded" must differ from "fails
  repeatedly";
- the run itself (handle() AGENDA context assembly): step 1 becomes
  "build on <prior deliverable>" instead of archaeology.

Exact-match (normalized) only, by decision — fuzzy/paraphrase matching is
a separate later call (the five-word-slug collision hazard is the
cautionary sibling). Killswitch: `rerun.brief` (default ON — read-only,
deterministic, no LLM spend; same convention as recall.dispatch_inject).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import List, Optional

log = logging.getLogger("maro.rerun_identity")

# Metadata resolution is indexed but nonzero cost; a goal dispatched dozens
# of times (2026-05-17: the same goal ran ~25x in 35 minutes) needs the
# count, not a metadata read per row.
_MAX_RESOLVED = 12
_MAX_SHOWN = 5
_MAX_DELIVERABLES = 6


def brief_enabled() -> bool:
    try:
        from config import get as cfg_get
        return bool(cfg_get("rerun.brief", True))
    except Exception:
        return True


def normalize_goal(text: str) -> str:
    """Whitespace-collapsed, casefolded exact-match key. Deliberately
    conservative: no punctuation or URL stripping — a prefix-bearing
    variant ("effort:high do X") will not match its bare sibling, and
    that is the accepted v1 trade."""
    return " ".join((text or "").split()).casefold()


@dataclass
class PriorAttempt:
    """One prior dispatch of the same (normalized) goal text."""
    handle_id: str
    ts: str = ""                # intake ISO timestamp
    run_name: str = ""          # run-dir name when a run record resolved
    standing: str = ""          # verdict WITH standing, human-readable
    achieved: Optional[bool] = None
    verdict_source: str = ""
    contested: bool = False
    stop_verdict: str = ""
    project: str = ""
    status: str = ""
    dry_run: bool = False


def prior_attempts(goal: str, *, exclude_handle_id: str = "") -> List[PriorAttempt]:
    """Complete list of prior attempts at this exact goal, newest first.

    Scans memory/handle_inputs.jsonl (append-ordered, one row per handle()
    call). The newest _MAX_RESOLVED matches get their run metadata read for
    verdict-with-standing; older rows are counted but not inspected.
    Dry-run previews are dropped (they are not attempts). Never raises —
    an unreadable record degrades to "knows nothing".
    """
    key = normalize_goal(goal)
    if not key:
        return []
    try:
        from orch_items import memory_dir
        path = memory_dir() / "handle_inputs.jsonl"
        if not path.is_file():
            return []
        raw = path.read_text(encoding="utf-8")
    except Exception as exc:
        log.debug("rerun: intake record unreadable: %s", exc)
        return []
    # Cheap prefilter before json.loads: the first token of the normalized
    # goal must appear in a matching line. json.dumps escapes non-ASCII
    # (\uXXXX), so only trust the prefilter for plain alnum ASCII tokens.
    tok = key.split(" ", 1)[0][:24]
    use_prefilter = len(tok) >= 4 and tok.isascii() and tok.isalnum()
    matches: List[PriorAttempt] = []
    seen: set = set()
    for line in raw.splitlines():
        if use_prefilter and tok not in line.casefold():
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        hid = str(rec.get("handle_id") or "")
        if not hid or hid == exclude_handle_id or hid in seen:
            continue
        if normalize_goal(str(rec.get("raw_input") or "")) != key:
            continue
        seen.add(hid)
        matches.append(PriorAttempt(handle_id=hid, ts=str(rec.get("ts") or "")))
    matches.reverse()  # append-ordered file -> newest first
    out: List[PriorAttempt] = []
    for i, att in enumerate(matches):
        if i < _MAX_RESOLVED:
            _resolve(att)
        else:
            att.standing = "(older attempt — record not inspected)"
        if att.dry_run:
            continue
        out.append(att)
    return out


def _resolve(att: PriorAttempt) -> None:
    """Fill verdict-with-standing from the attempt's run metadata."""
    try:
        from runs import resolve_run_dir
        rd = resolve_run_dir(att.handle_id)
        if rd is None:
            att.standing = "(no run record — NOW-lane, crashed, or pre-runs era)"
            return
        att.run_name = rd.name
        meta = json.loads((rd / "metadata.json").read_text(encoding="utf-8"))
    except Exception as exc:
        log.debug("rerun: metadata unreadable for %s: %s", att.handle_id, exc)
        att.standing = "(run record unreadable)"
        return
    att.dry_run = bool(meta.get("dry_run"))
    achieved = meta.get("goal_achieved")
    att.achieved = achieved if isinstance(achieved, bool) else None
    att.verdict_source = str(meta.get("goal_verdict_source") or "")
    att.contested = bool(meta.get("goal_verdict_contested"))
    att.stop_verdict = str(meta.get("stop_verdict") or "")
    att.project = str(meta.get("project") or "")
    att.status = str(meta.get("status") or "")
    att.standing = _standing_line(att, meta)


def _standing_line(att: PriorAttempt, meta: dict) -> str:
    """The verdict as a sentence that carries its own provenance. The whole
    point of this module is that a consumer reading these lines cannot
    mistake a superseded or disputed verdict for plain failure."""
    if att.dry_run:
        return "dry-run preview (not a real attempt)"
    if att.achieved is None:
        return (f"no verdict recorded (status: {att.status or 'unknown'}"
                + (" — possibly still in flight)" if att.status == "running"
                   else ")"))
    base = "ACHIEVED" if att.achieved else "NOT ACHIEVED"
    if att.verdict_source == "operator_restamp":
        base += (" — operator re-stamp: the original automated verdict was "
                 "wrong and is superseded; do not read it as failure")
    elif att.verdict_source:
        conf = meta.get("goal_verdict_confidence")
        base += (f" (source: {att.verdict_source}"
                 + (f", conf {conf}" if isinstance(conf, (int, float)) else "")
                 + ")")
    if att.contested:
        base += (" — CONTESTED: verdict is disputed and held out of "
                 "learning; treat as anecdote, not ground truth")
    if att.stop_verdict:
        base += f"; stop_verdict: {att.stop_verdict}"
    return base


def _deliverables(project: str) -> List[str]:
    """Top project-dir files by recency — what a re-run should read before
    producing anything. Names only; the run has real tools to open them."""
    if not project or "/" in project or "\\" in project or ".." in project:
        return []
    try:
        from orch_items import projects_root
        root = projects_root()
        pdir = root / project
        if not pdir.is_dir() or not pdir.resolve().is_relative_to(root.resolve()):
            return []
        cands = []
        for f in pdir.iterdir():
            if f.is_file() and not f.name.startswith("."):
                cands.append((f.stat().st_mtime, f.name))
        art = pdir / "artifacts"
        if art.is_dir():
            for f in art.iterdir():
                if f.is_file() and not f.name.startswith("."):
                    cands.append((f.stat().st_mtime, f"artifacts/{f.name}"))
        cands.sort(reverse=True)
        return [name for _, name in cands[:_MAX_DELIVERABLES]]
    except Exception:
        return []


def render_brief(attempts: List[PriorAttempt]) -> str:
    """Render the provenance-labeled brief, or "" when there is no history.
    History rides its own labeled channel and never becomes the goal — the
    dispatch-envelope pattern."""
    if not attempts:
        return ""
    n = len(attempts)
    lines = [
        "== Re-run notice: this exact goal has prior attempts on record ==",
        (f"Deterministic intake-record match (normalized exact text): "
         f"{n} prior attempt{'s' if n != 1 else ''}. This list is COMPLETE "
         f"— the whole history, not a recall sample."),
        "Attempts, newest first:",
    ]
    for att in attempts[:_MAX_SHOWN]:
        date = att.ts[:10] or "?"
        ref = att.run_name or att.handle_id
        lines.append(f"- {date} {ref}: {att.standing}")
    if n > _MAX_SHOWN:
        lines.append(f"- … and {n - _MAX_SHOWN} earlier attempt(s)")
    proj = next((a.project for a in attempts if a.project), "")
    if proj:
        deliv = _deliverables(proj)
        if deliv:
            lines.append(
                f"Existing deliverables in shared project '{proj}': "
                + ", ".join(deliv))
    lines.append(
        "Guidance: you are a re-run. The records above are PRIOR ART from "
        "earlier attempts — not your own work, and not necessarily failure. "
        "Build on existing deliverables instead of re-deriving them; verify "
        "before overwriting. A contested or re-stamped verdict must not be "
        "counted as a failed attempt.")
    lines.append("== End re-run notice ==")
    return "\n".join(lines)


def brief_for_goal(goal: str, *, exclude_handle_id: str = "") -> str:
    """One-call convenience for injection sites: killswitch + detect +
    render. Returns "" when disabled, on first attempts, and on any error."""
    if not brief_enabled():
        return ""
    try:
        return render_brief(prior_attempts(goal, exclude_handle_id=exclude_handle_id))
    except Exception as exc:
        log.debug("rerun: brief_for_goal degraded to empty: %s", exc)
        return ""
