"""Post-goal curation pass — classify a finished run and park it for mining.

Runs once at goal-end (hooked from handle.py's finalize block). It does NOT
discard the paid-for capture; it writes a compact `run_card.json` into the
run-dir that classifies the outcome and inventories what's mineable, so later
passes can act on it — scrape reusable skills/scripts, feed decision priors into
a similar or rephrased re-attempt, rescue a partial run before it went off the
rails, or just surface history to the user (and prune it on request).

Designed as a miner registry: `CURATORS` is an ordered list of pure functions
`(run_dir, meta, card) -> None` that enrich the card; new miners append to the
list without touching the hook. Shipped miners: classification, asset
inventory, result excerpt, spend transparency, skills-lite promotion, and the
four BACKLOG #0 miners — script scraper (`scrape_scripts`), skill scraper
(`flag_skill_candidate`), partial-run rescue (`rescue_partial`), and the
decision-prior indexer (`index_decision_prior`). The decision-prior indexer's
read half (`format_prior_decisions` / `prior_decision_context`) is surfaced
through `recall()` into a re-attempt's context BEFORE it starts, so a retried
or rephrased goal arrives warm instead of cold.

This is an adornment on the run-dir plan, not a new subsystem. Capture is
default-on; turn it off with MARO_RECORD=0 / config record.enabled=false (see
runs.recording_enabled). Curation is cheap and runs regardless, but produces an
empty inventory when capture is off.

CLI:
    python3 -m run_curation list [--limit N]
    python3 -m run_curation show <handle_id>
    python3 -m run_curation curate <handle_id>
    python3 -m run_curation prune <handle_id> [--yes]
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import List, Optional

_SUCCESS_STATUSES = {"done", "complete", "completed"}
# "incomplete" = closure demoted a finished run (work ended, goal not met) —
# partial, not unknown (burn-in batch 1 surfaced it landing in "unknown").
_PARTIAL_STATUSES = {"partial", "restart", "incomplete"}
_FAIL_STATUSES = {"stuck", "error", "failed", "blocked"}

# Asset extensions worth flagging as potentially-reusable scripts.
_SCRIPT_EXTS = {".py", ".sh", ".js", ".ts", ".rb", ".go"}

# Dangerous-pattern blocklist for the skills-lite static scan below. A
# Python-code substring list — relocated here from the retired sandbox.py
# (its only remaining consumer; container executor arc C4 §7, 2026-07-13).
# Applied to code regions only, never prose (prose false-positives on
# instructional text like "read the ledger with open(...)").
_DANGEROUS_PATTERNS = [
    "import os",
    "import subprocess",
    "__import__",
    "eval(",
    "exec(",
    "open(",
    "shutil",
    "rmdir",
    "unlink",
    "system(",
    "socket.connect",
    "urllib.request",
    "requests.get",
    "requests.post",
    "httpx.",
    "aiohttp.",
    "pickle.loads",
    "marshal.loads",
    "import ctypes",
    "ctypes.",
    "cffi.",
]


def _runs_root() -> Path:
    # Delegate to runs.runs_root so workspace resolution can't diverge.
    from runs import runs_root
    return runs_root()


def _run_dir_for(handle_id: str) -> Optional[Path]:
    # nickname is deterministic, so runs.run_dir gives the exact path.
    from runs import run_dir
    rd = run_dir(handle_id)
    return rd if rd.is_dir() else None


def _read_meta(rd: Path) -> dict:
    p = rd / "metadata.json"
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def run_result(handle_id: str, run_dir: Optional[Path] = None) -> Optional[dict]:
    """Uniform result retrieval — the substrate-facing 'what was the answer?'.

    Normalizes the two lane shapes into one dict:
      NOW    → artifact/now-<hid>.json          (payload['result'])
      AGENDA → build/loop-*-RESULT.md|PARTIAL.md (newest; RESULT preferred)

    Returns {handle_id, lane, status, result, result_path} or None if the run
    (or any result artifact) doesn't exist.
    """
    rd = run_dir or _run_dir_for(handle_id)
    if rd is None or not rd.is_dir():
        return None
    meta = _read_meta(rd)
    base = {
        "handle_id": handle_id,
        "lane": meta.get("lane"),
        "status": meta.get("status"),
    }

    now_artifact = rd / "artifact" / f"now-{handle_id}.json"
    if now_artifact.is_file():
        try:
            payload = json.loads(now_artifact.read_text())
            return {**base, "result": payload.get("result", ""),
                    "result_path": str(now_artifact)}
        except Exception:
            pass

    # AGENDA: prefer a completed RESULT over a PARTIAL; newest wins within kind
    # (continuation loops write one transcript each).
    build = rd / "build"
    for pattern in ("loop-*-RESULT.md", "loop-*-PARTIAL.md"):
        candidates = sorted(build.glob(pattern),
                            key=lambda p: p.stat().st_mtime, reverse=True)
        if candidates:
            f = candidates[0]
            try:
                return {**base, "result": f.read_text(encoding="utf-8"),
                        "result_path": str(f)}
            except Exception:
                continue
    return None


# --- curators (the miner registry) -----------------------------------------

def classify_outcome(rd: Path, meta: dict, card: dict) -> None:
    """Set success_class from process status + the goal verdict (done≠achieved)."""
    status = (meta.get("status") or "").lower()
    achieved = meta.get("goal_achieved")  # may be absent = unverified
    if status in _SUCCESS_STATUSES and achieved is True:
        cls = "success"
    elif status in _SUCCESS_STATUSES and achieved is False:
        cls = "done-not-achieved"   # finished but verdict says it didn't land
    elif status in _SUCCESS_STATUSES:
        cls = "done-unverified"
    elif status in _PARTIAL_STATUSES:
        cls = "partial"
    elif status in _FAIL_STATUSES:
        cls = "failed"
    else:
        cls = "unknown"
    card["success_class"] = cls
    card["status"] = status
    card["goal_achieved"] = achieved
    card["goal_verdict_summary"] = meta.get("goal_verdict_summary")
    # Cost-per-run via the loop_ids join key (absent on pre-2026-07-02 runs).
    try:
        import metrics as _metrics
        _lids = meta.get("loop_ids") or []
        card["total_cost_usd"] = (
            round(_metrics.spend_for_loops(_lids), 6) if _lids else None
        )
    except Exception:
        card["total_cost_usd"] = None


def inventory_assets(rd: Path, meta: dict, card: dict) -> None:
    """Inventory what's mineable: captured calls, scripts, artifacts, steps."""
    build = rd / "build"
    calls = list((build / "calls").glob("call-*.json")) if (build / "calls").is_dir() else []

    scripts: List[str] = []
    artifacts: List[str] = []
    for sub in (build, rd / "artifact"):
        if not sub.is_dir():
            continue
        for f in sub.rglob("*"):
            if not f.is_file():
                continue
            rel = str(f.relative_to(rd))
            if f.suffix in _SCRIPT_EXTS:
                scripts.append(rel)
            elif "artifact" in rel and f.suffix in (".txt", ".md", ".json", ".csv"):
                artifacts.append(rel)

    # step count from the loop log, if present
    n_steps = 0
    for logf in build.glob("loop-*-log.json"):
        try:
            n_steps = max(n_steps, len(json.loads(logf.read_text()).get("steps", [])))
        except Exception:
            pass

    inv = {
        "n_calls": len(calls),
        "n_steps": n_steps,
        "scripts": sorted(set(scripts))[:50],
        "artifacts": sorted(set(artifacts))[:50],
    }
    card["inventory"] = inv
    # "mineable" = there's recorded substance worth a later pass.
    card["mineable"] = bool(calls or scripts or artifacts)


def excerpt_result(rd: Path, meta: dict, card: dict) -> None:
    """Put a result excerpt + pointer on the card so a substrate reading only
    run_card.json gets the answer (or knows where the full text lives)."""
    res = run_result(meta.get("handle_id", ""), run_dir=rd)
    if not res:
        return
    text = (res.get("result") or "").strip()
    card["result_excerpt"] = text[:500] + ("…" if len(text) > 500 else "")
    card["result_path"] = res.get("result_path")


def spend_transparency(rd: Path, meta: dict, card: dict) -> None:
    """Spend-gated transparency mandate (BACKLOG #11): above a configured
    spend threshold (`budget.transparency_usd`, default $2), the run card
    must carry the full build/artifact bundle — absolute paths + sizes, no
    grep required. An expensive run that hides what it built is unauditable;
    below the threshold the compact inventory is enough. The card IS the
    notify payload, so the bundle lands in front of the user directly."""
    _CAP = 200
    try:
        from config import get as _cfg_get
        threshold = float(_cfg_get("budget.transparency_usd", 2.0))
    except Exception:
        threshold = 2.0
    cost = card.get("total_cost_usd")
    if cost is None or threshold <= 0 or cost < threshold:
        return
    bundle: dict = {"run_dir": str(rd)}
    files = []
    for sub in ("build", "artifact"):
        d = rd / sub
        if not d.is_dir():
            continue
        for f in sorted(d.rglob("*")):
            if f.is_file():
                try:
                    files.append({"path": str(f), "bytes": f.stat().st_size})
                except OSError:
                    pass
    bundle["files"] = files[:_CAP]
    bundle["file_count"] = len(files)
    bundle["truncated"] = len(files) > _CAP
    # Project artifacts live outside the run dir — same no-grep mandate.
    try:
        from agent_loop import _goal_to_slug
        import orch_items as o
        _slug = _goal_to_slug(meta.get("prompt", "") or "")
        pa = o.projects_root() / _slug / "artifacts"
        if pa.is_dir():
            pfiles = []
            for f in sorted(pa.rglob("*")):
                if f.is_file():
                    try:
                        pfiles.append({"path": str(f), "bytes": f.stat().st_size})
                    except OSError:
                        pass
            bundle["project_artifacts"] = pfiles[:_CAP]
            bundle["project_artifact_count"] = len(pfiles)
    except Exception:
        pass
    card["spend_transparency"] = {"threshold_usd": threshold, "bundle": bundle}


# --- skills-lite promotion (Rider A, post-Purgatorio decision batch) ---------

_LITE_TIER = "skills-lite"
_LITE_MAX_PER_RUN = 5     # promotion is per-run incremental, not a bulk import
_LITE_SCAN_CAP = 200      # .md files examined per run before giving up


def _lite_enabled() -> bool:
    try:
        from config import get as _cfg_get
        return bool(_cfg_get("skills.lite_promotion", True))
    except Exception:
        return True


def _skill_shaped(fm: dict) -> bool:
    """A candidate is skill-shaped iff its frontmatter carries the fields
    skill_loader injects from: name + description + (triggers or roles)."""
    return bool(fm.get("name")) and bool(fm.get("description")) and bool(
        fm.get("triggers") or fm.get("roles_allowed")
    )


_CODE_REGION_RE = None  # compiled lazily; module import stays regex-free


def _code_regions(text: str) -> str:
    """Concatenated code regions of a markdown doc: fenced ``` blocks plus
    inline `...` spans. The dangerous-pattern scan runs on these only — a
    skills-lite .md is instructions, never executed Python, so code
    substrings in prose ("use open() to read the ledger") are description,
    not payload. Anything an author marks AS code is scanned in full."""
    import re
    global _CODE_REGION_RE
    if _CODE_REGION_RE is None:
        # \Z alternative: an unterminated fence is still code to the reader
        # (and to the prompt) — don't let a missing closing fence skip the scan.
        _CODE_REGION_RE = re.compile(
            r"```.*?(?:```|\Z)|`[^`\n]+`", re.DOTALL
        )
    return "\n".join(m.group(0) for m in _CODE_REGION_RE.finditer(text))


def _lite_candidate_files(rd: Path, meta: dict) -> List[Path]:
    """Skill-shaped .md candidates: run-dir artifact/ + build/, plus the
    project artifacts dir (same join as spend_transparency)."""
    dirs = [rd / "artifact", rd / "build"]
    try:
        from agent_loop import _goal_to_slug
        import orch_items as o
        _slug = _goal_to_slug(meta.get("prompt", "") or "")
        pa = o.projects_root() / _slug / "artifacts"
        if pa.is_dir():
            dirs.append(pa)
    except Exception:
        pass
    out: List[Path] = []
    seen = 0
    for d in dirs:
        if not d.is_dir():
            continue
        for f in sorted(d.rglob("*.md")):
            seen += 1
            if seen > _LITE_SCAN_CAP:
                return out
            out.append(f)
    return out


def promote_skills_lite(rd: Path, meta: dict, card: dict) -> None:
    """Rider A (Jeremy, 2026-07-09): "we want things promoted to skills that
    the local orchestration can pick up and use while waiting for user
    review... looked at as skills-lite, and degraded the same as regular
    skills that get broken or stop working."

    Two-tier promotion: skill-shaped .md artifacts from successful runs are
    copied into the workspace skills overlay (tier: skills-lite) where
    skill_loader injects them immediately, AND registered as a companion
    provisional Skill in skills.jsonl so the normal stats/decay/circuit-
    breaker machinery tracks them. degrade_skills_lite() quarantines the .md
    when its companion trips. Human review gates only ship-set (repo skills/)
    graduation. Also the first BACKLOG #0 miner (skill scraper).
    """
    if not _lite_enabled():
        return
    # Only runs whose process status finished cleanly and whose verdict didn't
    # say "didn't land" seed skills (done-not-achieved is exactly the run you
    # don't want to learn from).
    if card.get("success_class") not in ("success", "done-unverified"):
        return

    from skill_loader import _parse_frontmatter, _slugify, _FRONTMATTER_RE, skill_loader as _loader
    from config import skills_dir as _ws_skills_dir

    ws_dir = _ws_skills_dir()
    _loader.invalidate()
    known_names = {s.name for s in _loader.load_summaries()}
    try:
        from skills import load_skills as _load_skills
        known_names |= {s.name for s in _load_skills()}
    except Exception:
        pass

    promoted: List[dict] = []
    skipped: List[dict] = []
    handle_id = meta.get("handle_id", "") or card.get("handle_id", "")

    for f in _lite_candidate_files(rd, meta):
        if len(promoted) >= _LITE_MAX_PER_RUN:
            break
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        fm, _body = _parse_frontmatter(text)
        if not _skill_shaped(fm):
            continue  # not a skill artifact — silent, most .md files aren't
        name = fm["name"]
        rel = str(f.relative_to(rd)) if str(f).startswith(str(rd)) else str(f)

        if name in known_names:
            skipped.append({"file": rel, "name": name,
                            "reason": "name collision with existing skill"})
            continue
        dest = ws_dir / f"{_slugify(name)}.md"
        if dest.exists():
            skipped.append({"file": rel, "name": name,
                            "reason": f"destination exists: {dest.name}"})
            continue
        # Fail-closed static scan — scoped to the .md's CODE regions (fenced
        # blocks + inline spans). _DANGEROUS_PATTERNS (module-level) is a
        # Python-code substring list; applied to prose it false-positives on
        # instructional text ("read the ledger with open(...)" — batch-03,
        # funnel_report specimen). Prose threats are prompt injection, which is
        # the injection_guard gate below.
        try:
            code_text = _code_regions(text)
            hit = next((p for p in _DANGEROUS_PATTERNS if p in code_text), None)
        except Exception:
            hit = "static scan unavailable"
        if hit:
            skipped.append({"file": rel, "name": name,
                            "reason": f"dangerous pattern: {hit!r}"})
            continue
        # Prompt-injection gate — the same guard the sibling self-mod lanes
        # run (evolver_store.apply_suggestion, skill_lifecycle.synthesize_skill).
        # The pattern scan above guards executed-Python threats; a skills-lite
        # .md is *instructions* injected into future planning prompts, which
        # is injection_guard's threat model. Fail-closed: a guard error
        # quarantines the candidate for human review, same as an unsafe hit.
        try:
            from injection_guard import scan_content
            _ig = scan_content(text, source=f"run-artifact:{rel}")
            ig_reason = (None if _ig.is_clean
                         else f"injection risk ({_ig.risk_level}): "
                              f"{_ig.findings[0][:120] if _ig.findings else '?'}")
        except Exception as exc:
            ig_reason = f"injection_guard scan failed (fail-closed): {exc}"
        if ig_reason:
            skipped.append({"file": rel, "name": name, "reason": ig_reason})
            continue

        # Stamp the lite tier + origin into the frontmatter (drop any tier the
        # artifact claimed for itself — the lane assigns tiers, not the run).
        m = _FRONTMATTER_RE.match(text)
        raw_fm = "\n".join(
            ln for ln in m.group(1).splitlines()
            if not ln.strip().startswith(("tier:", "promoted_from:"))
        )
        stamped = (f"---\n{raw_fm}\ntier: {_LITE_TIER}\n"
                   f"promoted_from: {handle_id}\n---\n{text[m.end():]}")

        try:
            from file_lock import atomic_write
            atomic_write(dest, stamped)
        except Exception as exc:
            skipped.append({"file": rel, "name": name, "reason": f"write failed: {exc}"})
            continue

        # Companion runtime Skill = the degradation hook. find_matching_skills
        # matches it during runs, record_skill_outcome/attribute_failure feed
        # its stats, and an open circuit quarantines the .md (sweep below).
        try:
            import uuid as _uuid
            from datetime import datetime, timezone
            from skills import save_skill, write_skill_provenance
            from skill_types import Skill
            triggers = fm.get("triggers") or []
            if not isinstance(triggers, list):
                triggers = [triggers]
            save_skill(Skill(
                id=str(_uuid.uuid4())[:8],
                name=name,
                description=fm.get("description", ""),
                trigger_patterns=triggers,
                steps_template=[],
                source_loop_ids=list(meta.get("loop_ids") or []),
                created_at=datetime.now(timezone.utc).isoformat(),
                tier="provisional",
            ))
            write_skill_provenance(
                name, "create",
                reason=f"skills-lite auto-promotion from run {handle_id}",
                source_loop_ids=list(meta.get("loop_ids") or []),
                extra={"tier": _LITE_TIER, "source_file": rel,
                       "dest": str(dest), "handle_id": handle_id},
            )
        except Exception:
            # md landed but companion didn't — degrade sweep treats a missing
            # companion as quarantine-worthy, so this fails toward review.
            pass

        known_names.add(name)
        promoted.append({"name": name, "file": rel, "dest": str(dest)})

    quarantined = degrade_skills_lite()
    if promoted or skipped or quarantined:
        card["skills_lite"] = {"promoted": promoted, "skipped": skipped,
                               "quarantined": quarantined}
        _loader.invalidate()


def degrade_skills_lite() -> List[str]:
    """Quarantine skills-lite .md files whose companion runtime Skill tripped
    its circuit breaker or vanished (gc/culling). Mirrors maybe_demote_skills'
    signals, applied to the markdown overlay — the "degraded the same as
    regular skills" half of Rider A. The loader only globs the top-level
    skills dir, so moving into _quarantine/ removes the skill from injection
    while keeping the file for human review."""
    if not _lite_enabled():
        return []
    try:
        from config import skills_dir as _ws_skills_dir
        from skill_loader import _parse_frontmatter
        from skills import load_skills, write_skill_provenance
        ws_dir = _ws_skills_dir()
        by_name = {s.name: s for s in load_skills()}
    except Exception:
        return []

    quarantined: List[str] = []
    for f in sorted(ws_dir.glob("*.md")):
        try:
            fm, _ = _parse_frontmatter(f.read_text(encoding="utf-8"))
        except OSError:
            continue
        if fm.get("tier") != _LITE_TIER:
            continue
        name = fm.get("name") or f.stem
        comp = by_name.get(name)
        if comp is None:
            reason = "companion runtime skill missing (gc'd, culled, or never registered)"
        elif getattr(comp, "circuit_state", "closed") == "open":
            reason = "companion circuit breaker open (sustained failures)"
        else:
            continue
        qdir = ws_dir / "_quarantine"
        qdir.mkdir(exist_ok=True)
        target = qdir / f.name
        n = 1
        while target.exists():
            target = qdir / f"{f.stem}.{n}{f.suffix}"
            n += 1
        try:
            f.rename(target)
        except OSError:
            continue
        try:
            write_skill_provenance(
                name, "demote", reason=reason,
                extra={"tier": _LITE_TIER, "quarantined_to": str(target)},
            )
        except Exception:
            pass
        quarantined.append(name)

    if quarantined:
        try:
            from skill_loader import skill_loader as _loader
            _loader.invalidate()
        except Exception:
            pass
    return quarantined


# --- BACKLOG #0 miners: script scraper, skill scraper, partial rescue, --------
# --- decision-prior indexer. All pure (rd, meta, card)->None; never raise. ----

def _load_loop_log(rd: Path) -> Optional[dict]:
    """Newest structured loop log (build/loop-*-log.json) for a run, or None.

    The loop log carries per-step {index,text,status,...} plus stuck_reason and
    totals — structured signal that beats re-parsing the rendered PARTIAL.md.
    """
    build = rd / "build"
    if not build.is_dir():
        return None
    logs = sorted(build.glob("loop-*-log.json"),
                  key=lambda p: p.stat().st_mtime, reverse=True)
    for f in logs:
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return None


# Reusability heuristic bar (miner #2). A captured script clears it when the
# static signal score reaches _REUSABLE_MIN. NOTE: skills.py's scoring
# (utility_score/circuit breaker) is RUNTIME — it needs use_count + success_rate
# that a just-finished run doesn't have yet. So this is a shape heuristic, not
# that machinery: cheap, explainable, and only a *judgment on the card* (the
# real promotion still goes through skills/evolver).
_REUSABLE_MIN = 3
_SCRIPT_READ_CAP = 40000
_SCRIPTS_JUDGED_CAP = 30


def _judge_script_reusability(path: Path) -> dict:
    """Static reusability judgment for one captured script (pure inspection —
    never executed). Returns {reusable, score, reasons}."""
    reasons: List[str] = []
    score = 0
    try:
        text = path.read_text(encoding="utf-8", errors="replace")[:_SCRIPT_READ_CAP]
    except OSError:
        return {"reusable": False, "score": 0, "reasons": ["unreadable"]}
    code_lines = [ln for ln in text.splitlines()
                  if ln.strip() and not ln.strip().startswith("#")]
    n_lines = len(code_lines)
    suffix = path.suffix
    # Parameterized (def/class/shell function) = built to be called again.
    if any(tok in text for tok in ("\ndef ", "\nclass ", "function ", "() {")):
        score += 2
        reasons.append("defines reusable function/class")
    # Takes inputs (CLI/args) rather than hardcoding a single case.
    if any(tok in text for tok in ("argparse", "sys.argv", "__main__",
                                   "getopts", '"$1"', "'$1'", "click.")):
        score += 2
        reasons.append("parameterized via CLI/args")
    # A top-of-file docstring/header signals documented, deliberate intent.
    stripped = text.lstrip()
    if stripped.startswith(('"""', "'''")) or (suffix == ".sh" and text[:3] == "#!/"):
        score += 1
        reasons.append("documented intent")
    if 8 <= n_lines <= 400:
        score += 1
        reasons.append(f"substantive ({n_lines} lines)")
    if n_lines < 5:
        score -= 2
        reasons.append("too small — likely one-off glue")
    # Run-specific hardcoding (this run's dir, /tmp) is the signature of
    # throwaway glue, not a reusable tool.
    if "/tmp/" in text or ".maro/workspace/runs/" in text:
        score -= 2
        reasons.append("hardcoded run-specific path")
    return {"reusable": score >= _REUSABLE_MIN, "score": score, "reasons": reasons}


def scrape_scripts(rd: Path, meta: dict, card: dict) -> None:
    """Script scraper (miner #2): judge which of the run's captured scripts are
    genuinely reusable tools vs one-off glue, and record the judgment on the
    card. Reads inventory_assets' `scripts` list (so it must run after it).
    Pure static inspection — no runtime signal exists at curation time."""
    inv = card.get("inventory") or {}
    scripts = inv.get("scripts") or []
    if not scripts:
        return
    judged: List[dict] = []
    for rel in scripts[:_SCRIPTS_JUDGED_CAP]:
        p = rd / rel
        if not p.is_file():
            continue
        judged.append({"path": rel, **_judge_script_reusability(p)})
    if not judged:
        return
    reusable = [j for j in judged if j["reusable"]]
    card["reusable_scripts"] = {
        "reusable": reusable,
        "n_reusable": len(reusable),
        "n_judged": len(judged),
    }


def flag_skill_candidate(rd: Path, meta: dict, card: dict) -> None:
    """Skill scraper (miner #1): FLAG a successful run that produced a reusable
    tool/procedure as a skill-crystallization candidate, for the EXISTING
    pipeline (skills.extract_skills / evolver.synthesize_skill, which already
    run at loop-finalize) or human review to consider.

    Deliberately NOT a second promotion path: promote_skills_lite already
    handles runs that author a skill .md; this only marks candidacy so the
    runs that produced a reusable script or a repeatable procedure WITHOUT
    authoring a doc stop falling through the gap (arch-quality-selfimprove:
    'skills-lite covers only runs that deliberately author a skill .md').
    Auto-injects nothing — the flag is advisory."""
    # Same gate extract_skills uses: only clean finishes not judged unachieved
    # (done ≠ achieved) — never crystallize from a run that didn't land.
    if card.get("success_class") not in ("success", "done-unverified"):
        return
    # An authored .md already promoted via skills-lite is the stronger signal —
    # don't double-flag it as a heuristic candidate.
    if (card.get("skills_lite") or {}).get("promoted"):
        return
    reasons: List[str] = []
    reusable = (card.get("reusable_scripts") or {}).get("reusable") or []
    if reusable:
        reasons.append(f"{len(reusable)} reusable script(s): "
                       + ", ".join(r["path"] for r in reusable[:3]))
    n_steps = (card.get("inventory") or {}).get("n_steps") or 0
    if n_steps >= 3:
        reasons.append(f"repeatable {n_steps}-step procedure")
    if not reasons:
        return
    card["skill_candidate"] = {
        "flagged": True,
        "reasons": reasons,
        "note": ("advisory — for extract_skills / synthesize_skill or human "
                 "review; not auto-promoted"),
    }


def rescue_partial(rd: Path, meta: dict, card: dict) -> None:
    """Partial-run rescue (miner #4): for a run that ended partial/incomplete,
    record WHAT WAS ACCOMPLISHED — which steps completed, what artifacts exist,
    and where it got stuck — so a follow-up run or a human can resume from
    there instead of restarting cold. Shares the card with index_decision_prior
    (#3), which references this block via `resume_from` when present."""
    if card.get("success_class") != "partial":
        return
    rescue: dict = {}
    log = _load_loop_log(rd)
    if log:
        steps = log.get("steps") or []
        done = [{"index": s.get("index"), "text": (s.get("text") or "")[:200]}
                for s in steps if s.get("status") == "done"]
        blocked = [{"index": s.get("index"), "text": (s.get("text") or "")[:200]}
                   for s in steps if s.get("status") not in ("done", None)]
        rescue["done_steps"] = done
        rescue["n_done"] = len(done)
        rescue["n_total"] = len(steps)
        if blocked:
            rescue["stuck_at"] = blocked[0]
        if log.get("stuck_reason"):
            rescue["stuck_reason"] = str(log["stuck_reason"])[:300]
    # Existing artifacts are the salvage — a follow-up shouldn't regenerate them.
    inv = card.get("inventory") or {}
    if inv.get("artifacts"):
        rescue["artifacts"] = inv["artifacts"][:20]
    # Point at the human-readable partial transcript when one was written.
    build = rd / "build"
    partials = sorted(build.glob("loop-*-PARTIAL.md")) if build.is_dir() else []
    if partials:
        rescue["partial_transcript"] = str(partials[-1].relative_to(rd))
    if not rescue:
        return
    _stuck_idx = (rescue.get("stuck_at") or {}).get("index", "?")
    rescue["resume_hint"] = (
        f"{rescue.get('n_done', 0)}/{rescue.get('n_total', 0)} steps completed; "
        f"resume from step {_stuck_idx} rather than restarting. Existing "
        f"artifacts are salvage."
    )
    card["partial_rescue"] = rescue


# --- decision-prior indexer (miner #3, the owner ask) ------------------------

_DECISION_LESSON_CAP = 5
_DECISION_TRIED_CHARS = 400


def _run_lessons(meta: dict) -> List[str]:
    """Typed lessons this run recorded, joined by its loop_ids. Empty on any
    memory-read failure — a curator never hard-depends on memory being
    reachable (adornment, not critical path)."""
    out: List[str] = []
    seen: set = set()
    try:
        from memory_ledger import load_outcome_by_loop_id
    except Exception:
        return out
    for lid in (meta.get("loop_ids") or []):
        try:
            oc = load_outcome_by_loop_id(str(lid))
        except Exception:
            oc = None
        for lesson in (getattr(oc, "lessons", None) or []):
            key = str(lesson)[:80]
            if key in seen:
                continue
            seen.add(key)
            out.append(str(lesson)[:200])
            if len(out) >= _DECISION_LESSON_CAP:
                return out
    return out


def index_decision_prior(rd: Path, meta: dict, card: dict) -> None:
    """Decision-prior indexer (miner #3 — the OWNER ASK, Jeremy 2026-07-04:
    retried failures start cold despite 'the old task context available').

    Distills this finished run into a compact, retrieval-ready prior — what it
    tried, how it ended and why, what it learned, and (when partial) where to
    resume — so a later re-attempt of the SAME or a rephrased goal arrives
    warm. This is the WRITE half of the loop; the READ half is
    format_prior_decisions() / prior_decision_context(), surfaced through
    recall() into the new run's context BEFORE it starts
    (recall.RecallResult.prior_decisions). Must run after classify_outcome,
    inventory_assets, excerpt_result and rescue_partial (it reads their fields).
    """
    inv = card.get("inventory") or {}
    excerpt = (card.get("result_excerpt") or "").strip()

    tried_parts: List[str] = []
    log = _load_loop_log(rd)
    if log and log.get("steps"):
        step_txts = [(s.get("text") or "").strip() for s in log["steps"]]
        step_txts = [t for t in step_txts if t][:6]
        if step_txts:
            tried_parts.append("steps: " + "; ".join(t[:80] for t in step_txts))
    if inv.get("scripts"):
        tried_parts.append("scripts: " + ", ".join(inv["scripts"][:5]))
    if inv.get("artifacts"):
        tried_parts.append("produced: " + ", ".join(inv["artifacts"][:5]))
    if not tried_parts and excerpt:
        tried_parts.append(excerpt[:_DECISION_TRIED_CHARS])
    what_was_tried = (" | ".join(tried_parts))[:_DECISION_TRIED_CHARS] or "no captured detail"

    cls = card.get("success_class")
    why = card.get("goal_verdict_summary") or ""
    if cls in ("partial", "failed", "done-not-achieved"):
        _be = meta.get("backend_error")
        _be_action = _be.get("user_action") if isinstance(_be, dict) else ""
        why = ((card.get("partial_rescue") or {}).get("stuck_reason")
               or why or _be_action or excerpt[-300:])
    why = str(why or "")[:400]

    prior = {
        "handle_id": card.get("handle_id"),
        "goal": card.get("goal", ""),
        "outcome": cls,
        "goal_achieved": card.get("goal_achieved"),
        "when": card.get("started_at"),
        "what_was_tried": what_was_tried,
        "why": why,
        "lessons": _run_lessons(meta),
    }
    if card.get("partial_rescue"):
        prior["resume_from"] = card["partial_rescue"].get("resume_hint")
    card["decision_prior"] = prior


# --- decision-prior READ side (surfaced into the next run via recall) --------

def load_decision_prior(handle_id: str) -> Optional[dict]:
    """Read a finished run's decision-prior brief from its run_card.json.

    Falls back to a thin brief synthesized from the card's classification +
    result excerpt for runs curated before the indexer existed (no backfill
    needed). None when the run has no card at all (uncurated / pruned) — which
    is exactly why the CURRENT run self-excludes: its card is written at
    goal-END, so at read time (goal-start) it has none.
    """
    rd = _run_dir_for(handle_id)
    if rd is None:
        return None
    cp = rd / "run_card.json"
    if not cp.is_file():
        return None
    try:
        card = json.loads(cp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    dp = card.get("decision_prior")
    if isinstance(dp, dict):
        return dp
    return {
        "handle_id": card.get("handle_id", handle_id),
        "goal": card.get("goal", ""),
        "outcome": card.get("success_class"),
        "goal_achieved": card.get("goal_achieved"),
        "when": card.get("started_at"),
        "what_was_tried": (card.get("result_excerpt") or "")[:_DECISION_TRIED_CHARS],
        "why": card.get("goal_verdict_summary") or "",
        "lessons": [],
    }


def format_prior_decisions(attempts, *, goal: str = "",
                           exclude_handle_id: str = "", k: int = 3,
                           max_chars: int = 1000) -> str:
    """Render up to k prior attempts' decision-priors as one injectable block.

    `attempts` are recall.PriorAttempt-shaped (only `.handle_id` is required;
    dicts also accepted). Only attempts with a loadable card contribute — the
    current run has no card yet at read time, so it self-excludes;
    exclude_handle_id is belt-and-suspenders. Empty string when no prior has a
    usable brief. This is the READ half of miner #3; recall() calls it so a
    re-attempt of the same/rephrased goal arrives warm."""
    briefs: List[str] = []
    seen: set = set()
    for a in (attempts or []):
        if isinstance(a, dict):
            hid = a.get("handle_id")
        else:
            hid = getattr(a, "handle_id", None)
        if not hid or hid == exclude_handle_id or hid in seen:
            continue
        seen.add(hid)
        dp = load_decision_prior(hid)
        if not dp:
            continue
        outcome = dp.get("outcome") or "?"
        when = (dp.get("when") or "")[:10]
        line = f"- [{outcome} {when} · {hid}] tried: {dp.get('what_was_tried') or '?'}"
        why = (dp.get("why") or "").strip()
        if why and outcome != "success":
            line += f". Why it ended: {why}"
        lessons = dp.get("lessons") or []
        if lessons:
            line += ". Lessons: " + "; ".join(lessons[:3])
        if dp.get("resume_from"):
            line += f". Resume: {dp['resume_from']}"
        briefs.append(line)
        if len(briefs) >= k:
            break
    if not briefs:
        return ""
    block = ("## Prior attempts at this goal — read before planning\n"
             + "\n".join(briefs)
             + "\nDo not repeat an approach that already failed the same way; "
               "build on what worked, resume a partial, or change the approach.")
    return block[:max_chars]


def prior_decision_context(goal: str, *, window_hours: float = 24.0,
                           k: int = 3, exclude_handle_id: str = "") -> str:
    """Standalone entry point: detect a retry/rephrase of `goal` (reusing
    recall's similarity match — exact + near, 0.9 threshold) and return the
    matched attempts' decision-priors as one injectable block.

    recall() calls format_prior_decisions() directly on the attempts it already
    computed; this convenience wrapper is for callers/tests that hold only the
    goal text. Lazy import of recall keeps the two modules cycle-free."""
    try:
        from recall import _find_prior_attempts
        attempts = _find_prior_attempts(goal, window_hours=window_hours)
    except Exception:
        return ""
    return format_prior_decisions(attempts, goal=goal,
                                  exclude_handle_id=exclude_handle_id, k=k)


# Ordered registry. Append future miners here; the hook never changes.
# Order carries data deps: spend_transparency + promote_skills_lite read
# success_class/total_cost_usd (classify_outcome). The BACKLOG #0 miners chain
# further: scrape_scripts reads inventory (inventory_assets); flag_skill_candidate
# reads reusable_scripts (scrape_scripts) + success_class; rescue_partial reads
# inventory + success_class; index_decision_prior reads all of the above incl.
# partial_rescue, so it runs LAST.
CURATORS = [classify_outcome, inventory_assets, excerpt_result, spend_transparency,
            promote_skills_lite,
            scrape_scripts,          # #2 script scraper
            flag_skill_candidate,    # #1 skill scraper (flag, not a 2nd promotion path)
            rescue_partial,          # #4 partial-run rescue
            index_decision_prior]    # #3 decision-prior indexer (owner ask)


def curate_run(handle_id: str, status: Optional[str] = None,
               run_dir: Optional[Path] = None) -> Optional[dict]:
    """Build + write `run_card.json` for a finished run. Returns the card.

    Best-effort: returns None and never raises on a missing/unreadable run.
    """
    try:
        rd = run_dir or _run_dir_for(handle_id)
        if rd is None or not rd.is_dir():
            return None
        meta = _read_meta(rd)
        if status:
            meta.setdefault("status", status)
        card = {
            "handle_id": handle_id,
            "nickname": meta.get("nickname", ""),
            "goal": meta.get("prompt", ""),
            "lane": meta.get("lane"),
            "model": meta.get("model"),
            "started_at": meta.get("started_at"),
            "ended_at": meta.get("ended_at"),
        }
        for curator in CURATORS:
            try:
                curator(rd, meta, card)
            except Exception:
                pass  # one bad miner must not sink the card
        (rd / "run_card.json").write_text(json.dumps(card, indent=2))
        return card
    except Exception:
        return None


# --- user-facing surface (visible + prunable) ------------------------------

def list_runs(limit: int = 50) -> List[dict]:
    """Summaries of curated runs, newest first (by started_at)."""
    root = _runs_root()
    if not root.is_dir():
        return []
    cards = []
    for d in root.iterdir():
        if not d.is_dir():
            continue
        cp = d / "run_card.json"
        if cp.is_file():
            try:
                cards.append(json.loads(cp.read_text()))
                continue
            except Exception:
                pass
        # uncurated run — synthesize a thin summary from metadata
        meta = _read_meta(d)
        if meta:
            cards.append({
                "handle_id": meta.get("handle_id", d.name.split("-", 1)[0]),
                "goal": meta.get("prompt", ""),
                "status": meta.get("status"),
                "success_class": "uncurated",
                "started_at": meta.get("started_at"),
            })
    cards.sort(key=lambda c: c.get("started_at") or "", reverse=True)
    return cards[:limit]


def prune_run(handle_id: str) -> bool:
    """Delete a run-dir (the 'clean up if necessary' path). Returns success."""
    rd = _run_dir_for(handle_id)
    if rd is None or not rd.is_dir():
        return False
    shutil.rmtree(rd)
    return True


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Curate/list/prune recorded runs")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pl = sub.add_parser("list"); pl.add_argument("--limit", type=int, default=50)
    ps = sub.add_parser("show"); ps.add_argument("handle_id")
    pt = sub.add_parser("status"); pt.add_argument("handle_id")
    pr = sub.add_parser("result"); pr.add_argument("handle_id")
    pc = sub.add_parser("curate"); pc.add_argument("handle_id")
    pp = sub.add_parser("prune"); pp.add_argument("handle_id"); pp.add_argument("--yes", action="store_true")
    args = ap.parse_args(argv)

    if args.cmd == "list":
        for c in list_runs(args.limit):
            print(f"{c.get('handle_id','?'):8}  {c.get('success_class','?'):18}  "
                  f"{(c.get('goal','') or '')[:70]}")
    elif args.cmd == "show":
        rd = _run_dir_for(args.handle_id)
        if rd and (rd / "run_card.json").is_file():
            print((rd / "run_card.json").read_text())
        else:
            print(json.dumps(curate_run(args.handle_id) or {}, indent=2))
    elif args.cmd == "status":
        rd = _run_dir_for(args.handle_id)
        if rd is None:
            print("not found")
            return 1
        meta = _read_meta(rd)
        print(json.dumps({
            "handle_id": args.handle_id,
            "status": meta.get("status"),
            "goal_achieved": meta.get("goal_achieved"),
            "lane": meta.get("lane"),
            "started_at": meta.get("started_at"),
            "ended_at": meta.get("ended_at"),
        }, indent=2))
    elif args.cmd == "result":
        res = run_result(args.handle_id)
        if res is None:
            print("not found")
            return 1
        print(res.get("result", ""))
    elif args.cmd == "curate":
        print(json.dumps(curate_run(args.handle_id) or {}, indent=2))
    elif args.cmd == "prune":
        if not args.yes:
            print("refusing to prune without --yes")
            return 1
        print("pruned" if prune_run(args.handle_id) else "not found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
