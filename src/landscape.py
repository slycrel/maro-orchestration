"""The landscape (feature-related-runs, 2026-09-05): before a goal runs, Maro
looks at the workspace's prior runs and DECIDES its relation to them — the run
decides, not the operator (Jeremy: "the orchestrator doesn't have the data to
make a better decision than maro"). Candidate selection is deterministic and
recorded; the relation is one cheap recorded judge call, made only when
candidates exist. The decision lives on the run's metadata (`landscape`) and,
when a prior run was chosen, on its origin (`parent_handle_id`,
`related_by = "landscape"`), so feature 1's lineage machinery — recall's walk,
minting at the lineage root — follows the DECIDED lineage:

  fresh   — nothing here bears on the goal; it is the root of its own lineage
  related — a prior run bears on it: the goal FOLLOWS that run and the prior's
            delivered answer rides into the goal's request as context
  rerun   — the prior asked the same thing: follows it, its answer is context,
            and its plan (when the run left a plan manifest) is offered to the
            planner to reuse or revise; a rerun still runs

`--after` is the operator override: the origin already names a parent and no
landscape is read. `--fresh` records a landscape that was skipped, no call.

Same shape as the Go engine's `Landscape` record (go/internal/run/landscape.go):
Jaccard over goal words of ≥3 characters, floor 0.2, top 3, the judge prompt
template versioned on the record. Every failure degrades to fresh, recorded.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("maro.landscape")

FLOOR = 0.2
TOP_K = 3
RELATED_HEAD = 2000
SCAN_CAP = 200          # eligible finished runs considered (newest first)
PROMPT_VER = 4          # template 3 + `continues` and each candidate's project (review 2026-09-13)

# A prior run is a candidate when it RAN TO AN END: every terminal status the
# lifecycle writes (run_curation's success / partial / fail vocabularies),
# shown to the judge as the candidate's outcome — a failed prior is
# landscape information too (same as the Go engine, whose watermark covers
# delivered and delivery-failed runs). A run still running, or one whose
# status is not a word the lifecycle writes, is not. Neither is a run whose
# VERDICT IS STILL OWED: the answer-first early close publishes `done`
# with an ACTIVE `verdict_pending` marker before the quality gate has run,
# and the gate may still escalate — moving the run's project to a
# provisional retry destination and its answer to the retry's. A decision
# made over that window would bind a continuation to a workspace that the
# revert of a failed retry then abandons (review 2026-09-13 round 5). The
# run settles when the finalize (or the crash-orphan sweep) resolves the
# marker; until then it is landscape information the way a running run is:
# not yet.
TERMINAL_STATUSES = frozenset({
    "done", "complete", "completed",             # success
    "partial", "restart", "incomplete",           # partial
    "stuck", "error", "failed", "blocked",        # fail
    "killed", "cancelled", "canceled", "timeout", # interrupted
})

RELATIONS = ("fresh", "related", "rerun")
RULE_JUDGE = "judge"
RULE_NO_CANDIDATES = "no_candidates"
RULE_UNREADABLE = "judge_unreadable"
RULE_FRESH_OVERRIDE = "fresh_override"
RULES = (RULE_JUDGE, RULE_NO_CANDIDATES, RULE_UNREADABLE, RULE_FRESH_OVERRIDE)
RELATED_BY = "landscape"

# The answer contract by template version. The record carries the version it
# was asked with; the first named candidates by number only, the second also
# by run id (a live judge answered with the handle the prompt showed it).
_CONTRACT = {
    1: '{"relation": "fresh" | "related" | "rerun", "run": "<candidate number, or 0 for fresh>", "reason": "<one sentence>"}',
    2: '{"relation": "fresh" | "related" | "rerun", "run": <the candidate\'s number (1, 2, …) or its run id, or 0 for fresh>, "reason": "<one sentence>"}',
    3: '{"relation": "fresh" | "related" | "rerun", "run": <the candidate\'s number (1, 2, …) or its run id, or 0 for fresh>, "reason": "<one sentence>"}',
    # the fourth also asks whether the goal CONTINUES the chosen run's work —
    # `related` covers a tangent whose answer is useful context, which is not
    # the same as "the deliverable belongs with that run" (review 2026-09-13:
    # a context relation must not be promoted into a workspace decision)
    4: '{"relation": "fresh" | "related" | "rerun", "run": <the candidate\'s number (1, 2, …) or its run id, or 0 for fresh>, "continues": <true when the goal carries that run\'s work forward and its deliverable belongs with it, false when the run is only context>, "reason": "<one sentence>"}',
}


def project_name(value: Any) -> str:
    """A recorded project identity as a directory NAME, or "". Persisted
    metadata is a boundary: only a non-empty string with no path separators,
    no surrounding whitespace, and not `.`/`..` names a project — anything
    else (a number, a list, a path, a padded name) is rejected rather than
    coerced or canonicalised into some OTHER directory's name (on Linux
    " board-reports " and "board-reports" are two directories)."""
    if not isinstance(value, str):
        return ""
    name = value
    if not name or name != name.strip() or "/" in name or "\\" in name or name in (".", ".."):
        return ""
    return name


def project_inside_root(name: str) -> bool:
    """Whether `projects_root()/name` is a place a run may bind to: absent
    (it will be created), or a real directory that RESOLVES inside the
    projects root. A symlink — to anywhere — or a non-directory is not a
    project (`is_dir()` alone follows links out of the root). False on any
    error: the safe direction for the directory a deliverable lands in."""
    if not project_name(name):
        return False
    try:
        from orch_items import projects_root
        root = projects_root()
        target = root / name
        if target.is_symlink():
            return False
        if not target.exists():
            return True
        return target.is_dir() and target.resolve().is_relative_to(root.resolve())
    except Exception:
        return False


def recorded_project(handle_id: str) -> str:
    """The project a run recorded in its metadata (validated name), or ""."""
    try:
        from runs import resolve_run_dir
        rd = resolve_run_dir(str(handle_id or ""))
        meta = _read_meta(Path(rd)) if rd else None
    except Exception:
        return ""
    return project_name((meta or {}).get("project"))


def goal_words(text: str) -> set:
    """Lower-cased runs of letters/digits of length ≥ 3."""
    return {w for w in re.findall(r"[^\W_]+", (text or "").lower()) if len(w) >= 3}


def similarity(a: str, b: str) -> float:
    """Jaccard overlap of two goals' word sets."""
    wa, wb = goal_words(a), goal_words(b)
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def _read_meta(rd: Path) -> Optional[dict]:
    try:
        meta = json.loads((rd / "metadata.json").read_text(encoding="utf-8"))
    except Exception:
        return None
    return meta if isinstance(meta, dict) else None


def verdict_settled(meta: dict) -> bool:
    """False while a run's `verdict_pending` marker is ACTIVE (a dict with
    no `resolved_at`): its status is published, its verdict — and with it
    its project and answer — is not yet final. Any other shape (no marker,
    a resolved one, a malformed one) is settled: the marker is the ONLY
    signal of an owed verdict, and a forged or broken one must not hold a
    finished run out of the landscape forever."""
    vp = (meta or {}).get("verdict_pending")
    return not (isinstance(vp, dict) and not vp.get("resolved_at"))


def candidates(goal: str, *, exclude_handle_id: str = "") -> Tuple[List[dict], int, int]:
    """Scan the workspace's finished runs for the top-K at or above the floor,
    by similarity then by handle (deterministic). Returns (candidates,
    scanned, below_floor). A run that has not ended, a dry run, and the
    goal's own run are not candidates. The cap counts ELIGIBLE runs, newest
    first, so unrelated directories never push a finished run out of view
    (review 2026-09-05); when more eligible runs exist than the cap, the
    record says so (`truncated` on the decision)."""
    cands, scanned, below, _ = _candidates(goal, exclude_handle_id=exclude_handle_id)
    return cands, scanned, below


def _candidates(goal: str, *, exclude_handle_id: str = "") -> Tuple[List[dict], int, int, bool]:
    from runs import runs_root

    root = runs_root()
    if not root.is_dir():
        return [], 0, 0, False
    try:
        dirs = sorted((d for d in root.iterdir() if d.is_dir()),
                      key=lambda d: d.stat().st_mtime, reverse=True)
    except OSError:
        return [], 0, 0, False
    scanned = below = 0
    truncated = False
    found: List[dict] = []
    for rd in dirs:
        if scanned >= SCAN_CAP:
            truncated = True
            break
        meta = _read_meta(rd)
        if not meta:
            continue
        hid = str(meta.get("handle_id") or rd.name.split("-", 1)[0])
        if exclude_handle_id and hid == exclude_handle_id:
            continue
        prompt = str(meta.get("prompt") or "")
        status = str(meta.get("status") or "").strip().lower()
        if not prompt or status not in TERMINAL_STATUSES or not meta.get("ended_at") or meta.get("dry_run"):
            continue
        if not verdict_settled(meta):
            continue
        scanned += 1
        sim = similarity(goal, prompt)
        if sim < FLOOR:
            below += 1
            continue
        found.append({"handle_id": hid, "goal": prompt, "similarity": round(sim, 4),
                      "status": status, "run_dir": str(rd),
                      "project": project_name(meta.get("project"))})
    # by similarity descending, then handle descending (stable two-pass sort)
    found.sort(key=lambda c: c["handle_id"], reverse=True)
    found.sort(key=lambda c: c["similarity"], reverse=True)
    return found[:TOP_K], scanned, below, truncated


def answer_head(handle_id: str, run_dir: Optional[str] = None, *, head: int = RELATED_HEAD) -> str:
    """The head of a run's delivered answer (run_curation.run_result), or ""."""
    try:
        from run_curation import run_result
        res = run_result(handle_id, run_dir=Path(run_dir) if run_dir else None)
    except Exception:
        return ""
    text = str((res or {}).get("result") or "").strip()
    if len(text) > head:
        text = text[:head].rstrip() + "…"
    return text


def prompt(goal: str, cands: List[dict], *, ver: int = PROMPT_VER) -> str:
    """The judge's request: the goal and every candidate, with the contract."""
    lines = ["You decide how a new goal relates to prior runs in this workspace. "
             "Answer with one JSON object and nothing else:",
             _CONTRACT[ver or 1],
             "fresh: no prior run bears on the goal. related: a prior run bears on it "
             "(a follow-up, an angle, a tangent) and its answer is useful context. "
             "rerun: a prior run asked the same thing."]
    if (ver or 1) >= 4:
        lines.append("continues: true when the goal carries the chosen run's work forward, so "
                     "its deliverable belongs in that run's project; false when the run is "
                     "only useful context (an angle, a tangent, the same method for other "
                     "work). A rerun always continues.")
    lines += ["", "New goal:", goal, ""]
    for i, c in enumerate(cands, 1):
        lines.append(f"Candidate {i} (run {c['handle_id']}, similarity {c['similarity']:.2f}, outcome {c['status'] or 'unknown'}):")
        lines.append(f"Goal: {c['goal']}")
        if (ver or 1) >= 4:
            lines.append(f"Project: {c.get('project') or '(none recorded)'}")
        lines.append(f"Answer: {answer_head(c['handle_id'], c.get('run_dir')) or '(none recorded)'}")
        lines.append("")
    return "\n".join(lines)


def parse(text: str, cands: List[dict], *, ver: int = PROMPT_VER) -> Tuple[str, str, str]:
    """Read the judge's answer against the candidates under the contract of
    the template version it was asked with. Returns (relation, chosen
    handle_id or "", reason); raises ValueError outside the contract."""
    return parse_full(text, cands, ver=ver)[:3]


def parse_full(text: str, cands: List[dict], *, ver: int = PROMPT_VER) -> Tuple[str, str, str, bool]:
    """`parse` plus the continuation verdict: (relation, chosen, reason,
    continues). A rerun always continues; a related run continues only when
    the fourth contract's `continues` is the JSON boolean true — absent,
    a string, or asked under an older template reads as NOT continuing (the
    relation and its context stand; the project is not bound on it)."""
    s = (text or "").strip()
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j <= i:
        raise ValueError("answer is not the JSON contract")
    try:
        a = json.loads(s[i:j + 1])
    except Exception as exc:
        raise ValueError(f"answer is not the JSON contract: {exc}")
    if not isinstance(a, dict):
        raise ValueError("answer is not an object")
    rel = str(a.get("relation") or "").strip().lower()
    if rel not in RELATIONS:
        raise ValueError(f"relation {a.get('relation')!r} out of vocabulary")
    reason = str(a.get("reason") or "").strip()
    strict = (ver or 1) >= 3
    run = a.get("run")
    if rel == "fresh":
        # under the strict contract a fresh answer names no candidate: a
        # fresh that names one is contradictory evidence, not fresh
        if strict and run not in (None, 0, "0", "") and not (isinstance(run, float) and run == 0):
            raise ValueError(f"fresh names candidate {run!r}")
        return rel, "", reason, False
    continues = rel == "rerun" or ((ver or 1) >= 4 and a.get("continues") is True)
    n = 0
    if isinstance(run, bool):
        n = 0
    elif isinstance(run, int):
        n = run
    elif isinstance(run, float):
        if strict and run != int(run):
            raise ValueError(f"{rel} names candidate {run!r}, which is not a whole number")
        n = int(run)
    elif isinstance(run, str):
        v = run.strip()
        if v.startswith("run "):
            v = v[4:].strip()
        if (ver or 1) >= 2:
            for k, c in enumerate(cands, 1):
                if v == c["handle_id"]:
                    n = k
        if n == 0 and v.isdigit():
            n = int(v)
    if n < 1 or n > len(cands):
        raise ValueError(f"{rel} names candidate {run!r}, which is not one of {len(cands)}")
    return rel, cands[n - 1]["handle_id"], reason, continues


def decide(goal: str, *, handle_id: str, adapter=None, fresh: bool = False,
           why: str = "") -> Dict[str, Any]:
    """The decision record for a goal about to run. `adapter` is the judge,
    or a zero-argument factory for it (called only when candidates exist).
    No judge with candidates ⇒ unreadable (fresh, recorded as such)."""
    rec: Dict[str, Any] = {"floor": FLOOR, "top_k": TOP_K, "scanned": 0, "below_floor": 0,
                           "candidates": [], "relation": "fresh", "chosen": "", "reason": "",
                           "continues": False}
    if fresh:
        rec["rule"] = RULE_FRESH_OVERRIDE
        if why:
            rec["reason"] = why
        return rec
    cands, scanned, below, truncated = _candidates(goal, exclude_handle_id=handle_id)
    rec["scanned"], rec["below_floor"] = scanned, below
    if truncated:
        rec["truncated"] = True
    rec["candidates"] = [{k: c[k] for k in ("handle_id", "goal", "similarity", "status", "project")} for c in cands]
    if not cands:
        rec["rule"] = RULE_NO_CANDIDATES
        return rec
    rec["prompt_ver"] = PROMPT_VER
    if callable(adapter) and not hasattr(adapter, "complete"):
        # a judge factory: built only now that there is something to judge
        try:
            adapter = adapter()
        except Exception as exc:
            adapter = None
            log.debug("landscape: judge factory failed: %s", exc)
    if adapter is None:
        rec["rule"], rec["reason"] = RULE_UNREADABLE, "no judge available"
        return rec
    try:
        from llm import LLMMessage
        resp = adapter.complete(
            [LLMMessage("user", prompt(goal, cands))],
            max_tokens=200, temperature=0.0, no_tools=True, purpose="landscape")
        content = str(getattr(resp, "content", "") or "")
        rec["judge"] = {"model": str(getattr(resp, "model", "") or getattr(adapter, "model", "") or ""),
                        "input_tokens": int(getattr(resp, "input_tokens", 0) or 0),
                        "output_tokens": int(getattr(resp, "output_tokens", 0) or 0)}
    except Exception as exc:
        rec["rule"], rec["reason"] = RULE_UNREADABLE, f"judge failed: {str(exc).splitlines()[0][:200]}"
        return rec
    try:
        rel, chosen, reason, continues = parse_full(content, cands)
    except ValueError as exc:
        rec["rule"], rec["reason"] = RULE_UNREADABLE, str(exc)[:200]
        return rec
    rec["rule"], rec["relation"], rec["chosen"], rec["reason"] = RULE_JUDGE, rel, chosen, reason
    rec["continues"] = continues
    return rec


def apply(handle_id: str, origin: Optional[dict], rec: Dict[str, Any], *, replace: bool = False) -> Optional[dict]:
    """Stamp the decision on the run's metadata; when a prior run was chosen,
    the origin names it as the parent (feature 1's lineage substrate) and
    says the landscape chose it. Returns the origin to run with. `replace`
    (a RE-decision over a clarified goal) writes the origin even when it is
    empty, in the same metadata operation as the record: an earlier
    decision's parent must not outlive the decision that replaced it, and
    two writes would leave a window where it does (review 2026-09-13 r3)."""
    from runs import stamp_run_metadata_for
    out = dict(origin or {})
    if rec.get("relation") in ("related", "rerun") and rec.get("chosen"):
        prior = next((c for c in rec.get("candidates", []) if c.get("handle_id") == rec["chosen"]), None)
        if prior is None:
            # the chosen run must be one the record shows the judge — a
            # record that names another is not a decision this run made
            raise ValueError(f"landscape names {rec['chosen']}, which is not one of its candidates")
        out.update({"parent_handle_id": rec["chosen"], "parent_goal": str(prior.get("goal") or "")[:200],
                    "related_by": RELATED_BY, "relation": rec["relation"]})
        out.setdefault("source", "cli")
    fields: Dict[str, Any] = {"landscape": rec}
    if out or replace:
        fields["origin"] = out
    if stamp_run_metadata_for(handle_id, fields) is None:
        # the decision is recorded or it is not a decision: an unrecorded
        # lineage must not drive the run (review 2026-09-05)
        raise RuntimeError(f"landscape for {handle_id} could not be recorded")
    return out or None


def _chosen_project_as_judged(rec: Dict[str, Any]) -> str:
    """The chosen run's project AS THE JUDGE SAW IT — the candidate snapshot
    the decision carries — falling back to the run's metadata only for a
    record without one (a hand-built record, a template-3 record from
    before candidates carried their project). The decision was made over
    the snapshot; the binding follows the decision, not whatever the run's
    metadata says by the time the handle reads it again (review 2026-09-13
    round 5: an escalation in flight rewrites `project` provisionally)."""
    chosen = str(rec.get("chosen") or "")
    for c in rec.get("candidates") or []:
        if isinstance(c, dict) and str(c.get("handle_id") or "") == chosen and "project" in c:
            return project_name(c.get("project"))
    return recorded_project(chosen)


def chosen_project(rec: Dict[str, Any]) -> str:
    """The project of the run the landscape chose AND judged the goal to
    continue (`continues`: a rerun, or a related run whose work the goal
    carries forward — not a tangent that is merely useful context): the
    deliverable lands where the prior work is. Read from the candidate
    snapshot the judge decided over (see `_chosen_project_as_judged`). ""
    when fresh, when the judge did not say the goal continues that run,
    when the chosen run recorded no valid project name, or when that
    project is not a directory inside the projects root (a symlink
    pointing out of the root is not a project: `is_dir()` alone would
    follow it — the same containment guard the navigator's binder keeps).
    The handle then falls back to the goal-text shortcuts."""
    if rec.get("relation") not in ("related", "rerun") or not rec.get("chosen"):
        return ""
    if rec.get("continues") is not True:
        return ""
    try:
        from orch_items import projects_root
        project = _chosen_project_as_judged(rec)
        if not project:
            return ""
        if not (projects_root() / project).is_dir() or not project_inside_root(project):
            return ""
        return project
    except Exception:
        log.warning("landscape: chosen run's project unreadable, binding by goal text", exc_info=True)
        return ""


def context_only_project(rec: Dict[str, Any]) -> str:
    """The project of a run the judge related the goal to WITHOUT saying the
    goal continues its work (a tangent, the same method for other work):
    context, not a destination. The handle keeps its automatic fallbacks
    (the named shortcut, the minted slug — which reuses an existing slug
    for a goal that opens the same way) out of this project: a verdict of
    "context only" must not be undone one layer down (review 2026-09-13
    round 2). Read from the same candidate snapshot as `chosen_project`:
    the exclusion names the project the judge considered. "" when fresh,
    when the goal continues the run, or when the run recorded no valid
    project name."""
    if rec.get("relation") != "related" or not rec.get("chosen") or rec.get("continues") is True:
        return ""
    try:
        return _chosen_project_as_judged(rec)
    except Exception:
        return ""


def _prior_plan(rd: Optional[str]) -> List[str]:
    """The steps of the prior run's newest plan manifest, when it left one."""
    if not rd:
        return []
    meta = _read_meta(Path(rd)) or {}
    loops = meta.get("loops") if isinstance(meta.get("loops"), list) else []
    project = str(meta.get("project") or "")
    for entry in reversed(loops):
        loop_id = str((entry or {}).get("loop_id") or "") if isinstance(entry, dict) else ""
        if not loop_id:
            continue
        try:
            from loop_artifacts import _plan_manifest_path
            p = _plan_manifest_path(project, loop_id)
            if p is None or not p.is_file():
                continue
            steps = _manifest_steps(p.read_text(encoding="utf-8"))
            if steps:
                return steps
        except Exception:
            continue
    return []


def _manifest_steps(text: str) -> List[str]:
    """The planned steps of a loop plan manifest (loop_artifacts
    `_write_plan_manifest`): the numbered lines under "## Steps", without
    the outcome icon, the type tag, and the cost suffix."""
    steps: List[str] = []
    in_steps = False
    for line in text.splitlines():
        if line.startswith("## "):
            in_steps = line.startswith("## Steps")
            continue
        if not in_steps:
            continue
        m = re.match(r"^\s*\d+\.\s+(.*)$", line)
        if not m:
            continue
        body = m.group(1)
        body = re.sub(r"^[\u2705\u274c\u2b1c\s]+", "", body)
        body = re.sub(r"^`\[[^\]]*\]`\s*", "", body)
        body = body.split(" | ")[0].strip()
        if body:
            steps.append(body)
    return steps


def related_context(rec: Dict[str, Any]) -> str:
    """The chosen run's answer (and, for a rerun, its plan) as one block for
    the goal's requests. "" when fresh."""
    rel, chosen = rec.get("relation"), rec.get("chosen")
    if rel not in ("related", "rerun") or not chosen:
        return ""
    cand = next((c for c in rec.get("candidates", []) if c["handle_id"] == chosen), None)
    if cand is None:
        return ""
    from runs import resolve_run_dir
    rd = resolve_run_dir(chosen)
    lines = [f"## Related prior run ({chosen}, {rel})", f"Its goal: {cand['goal']}",
             "Its answer:", answer_head(chosen, str(rd) if rd else None) or "(none recorded)"]
    if rel == "rerun":
        steps = _prior_plan(str(rd) if rd else None)
        if steps:
            lines.append("Its plan (reuse or revise):")
            lines.extend(f"{i}. {s}" for i, s in enumerate(steps, 1))
    return "\n".join(lines)
