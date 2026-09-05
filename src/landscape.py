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
SCAN_CAP = 200
PROMPT_VER = 2

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
}


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


def candidates(goal: str, *, exclude_handle_id: str = "") -> Tuple[List[dict], int, int]:
    """Scan the workspace's finished runs for the top-K at or above the floor,
    by similarity then by handle (deterministic). Returns (candidates,
    scanned, below_floor). A run that has not ended, a dry run, and the
    goal's own run are not candidates."""
    from runs import runs_root

    root = runs_root()
    if not root.is_dir():
        return [], 0, 0
    try:
        dirs = sorted((d for d in root.iterdir() if d.is_dir()),
                      key=lambda d: d.stat().st_mtime, reverse=True)
    except OSError:
        return [], 0, 0
    scanned = below = 0
    found: List[dict] = []
    for rd in dirs[:SCAN_CAP]:
        meta = _read_meta(rd)
        if not meta:
            continue
        hid = str(meta.get("handle_id") or rd.name.split("-", 1)[0])
        if exclude_handle_id and hid == exclude_handle_id:
            continue
        prompt = str(meta.get("prompt") or "")
        if not prompt or not meta.get("status") or not meta.get("ended_at") or meta.get("dry_run"):
            continue
        scanned += 1
        sim = similarity(goal, prompt)
        if sim < FLOOR:
            below += 1
            continue
        found.append({"handle_id": hid, "goal": prompt, "similarity": round(sim, 4),
                      "status": str(meta.get("status") or ""), "run_dir": str(rd)})
    # by similarity descending, then handle descending (stable two-pass sort)
    found.sort(key=lambda c: c["handle_id"], reverse=True)
    found.sort(key=lambda c: c["similarity"], reverse=True)
    return found[:TOP_K], scanned, below


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
             "rerun: a prior run asked the same thing.", "",
             "New goal:", goal, ""]
    for i, c in enumerate(cands, 1):
        lines.append(f"Candidate {i} (run {c['handle_id']}, similarity {c['similarity']:.2f}, outcome {c['status'] or 'unknown'}):")
        lines.append(f"Goal: {c['goal']}")
        lines.append(f"Answer: {answer_head(c['handle_id'], c.get('run_dir')) or '(none recorded)'}")
        lines.append("")
    return "\n".join(lines)


def parse(text: str, cands: List[dict], *, ver: int = PROMPT_VER) -> Tuple[str, str, str]:
    """Read the judge's answer against the candidates under the contract of
    the template version it was asked with. Returns (relation, chosen
    handle_id or "", reason); raises ValueError outside the contract."""
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
    if rel == "fresh":
        return rel, "", reason
    run = a.get("run")
    n = 0
    if isinstance(run, (int, float)) and not isinstance(run, bool):
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
    return rel, cands[n - 1]["handle_id"], reason


def decide(goal: str, *, handle_id: str, adapter=None, fresh: bool = False,
           why: str = "") -> Dict[str, Any]:
    """The decision record for a goal about to run. `adapter` is the judge,
    or a zero-argument factory for it (called only when candidates exist).
    No judge with candidates ⇒ unreadable (fresh, recorded as such)."""
    rec: Dict[str, Any] = {"floor": FLOOR, "top_k": TOP_K, "scanned": 0, "below_floor": 0,
                           "candidates": [], "relation": "fresh", "chosen": "", "reason": ""}
    if fresh:
        rec["rule"] = RULE_FRESH_OVERRIDE
        if why:
            rec["reason"] = why
        return rec
    cands, scanned, below = candidates(goal, exclude_handle_id=handle_id)
    rec["scanned"], rec["below_floor"] = scanned, below
    rec["candidates"] = [{k: c[k] for k in ("handle_id", "goal", "similarity", "status")} for c in cands]
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
        rel, chosen, reason = parse(content, cands)
    except ValueError as exc:
        rec["rule"], rec["reason"] = RULE_UNREADABLE, str(exc)[:200]
        return rec
    rec["rule"], rec["relation"], rec["chosen"], rec["reason"] = RULE_JUDGE, rel, chosen, reason
    return rec


def apply(handle_id: str, origin: Optional[dict], rec: Dict[str, Any]) -> Optional[dict]:
    """Stamp the decision on the run's metadata; when a prior run was chosen,
    the origin names it as the parent (feature 1's lineage substrate) and
    says the landscape chose it. Returns the origin to run with."""
    from runs import stamp_run_metadata_for
    out = dict(origin or {})
    if rec.get("relation") in ("related", "rerun") and rec.get("chosen"):
        prior_goal = next((c["goal"] for c in rec.get("candidates", []) if c["handle_id"] == rec["chosen"]), "")
        out.update({"parent_handle_id": rec["chosen"], "parent_goal": prior_goal[:200],
                    "related_by": RELATED_BY, "relation": rec["relation"]})
        out.setdefault("source", "cli")
    fields: Dict[str, Any] = {"landscape": rec}
    if out:
        fields["origin"] = out
    stamp_run_metadata_for(handle_id, fields)
    return out or None


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
