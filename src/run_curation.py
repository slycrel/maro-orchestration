"""Post-goal curation pass — classify a finished run and park it for mining.

Runs once at goal-end (hooked from handle.py's finalize block). It does NOT
discard the paid-for capture; it writes a compact `run_card.json` into the
run-dir that classifies the outcome and inventories what's mineable, so later
passes can act on it — scrape reusable skills/scripts, feed decision priors into
a similar or rephrased re-attempt, rescue a partial run before it went off the
rails, or just surface history to the user (and prune it on request).

Designed as a miner registry: `CURATORS` is an ordered list of pure functions
`(run_dir, meta, card) -> None` that enrich the card. v0 ships classification +
asset inventory; future miners (skill scraper, script scraper, decision-prior
indexer, re-attempt hinter) append to the list without touching the hook.

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


# Ordered registry. Append future miners here; the hook never changes.
CURATORS = [classify_outcome, inventory_assets, excerpt_result]


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
