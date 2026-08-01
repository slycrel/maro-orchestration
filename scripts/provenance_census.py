#!/usr/bin/env python3
"""Census the run paper trail: which provenance artifacts each run actually wrote.

LT-0(c) + LT-0(d) evidence gatherer (BACKLOG "LT. Live-learning test arc").
Answers one question with data instead of belief: **for each stage of a run,
did the record we think exists actually land on disk?** Jeremy's framing at
open (2026-07-31): "We keep stumbling on data that we thought we had but
didn't for various reasons."

Deliberately **read-only and stdlib-only** — it does NOT import from ``src``.
Sibling stats scripts (verdict_gap_stats.py) do import maro modules; this one
must not, because it is designed to run as a detached subprocess *while the
box is doing directed work*. Importing ``config``/``ancestry`` would load
workspace config and give this probe a chance to perturb the very state it is
measuring. Everything here is a filesystem read + JSON parse.

Usage:
    python3 scripts/provenance_census.py                    # text summary to stdout
    python3 scripts/provenance_census.py --out DIR          # + JSON/text artifacts
    python3 scripts/provenance_census.py --since 2026-07-01
    python3 scripts/provenance_census.py --json             # machine-readable only

Exit status is 0 whenever the census itself ran. A census that finds gaps is a
successful census — the gaps are the deliverable, not an error.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# What we expect a run to have written, and when that expectation applies.
#
# `applies`: which lanes the artifact is expected for. A NOW one-shot has no
# decompose phase, so a missing loop plan there is CORRECT, not a gap. A census
# that reports expected absences as holes cries wolf and gets ignored — the
# per-lane expectation is what makes the "missing" column trustworthy.
#
# `stage`: the harness layer the artifact is the paper trail *for*. Grouping by
# stage is what turns a file list into an answer to "which layer is blind?"
# ---------------------------------------------------------------------------

ARTIFACTS: Tuple[Dict[str, Any], ...] = (
    {"key": "metadata.json", "path": "metadata.json",
     "stage": "intake", "applies": ("now", "agenda", "unknown")},
    {"key": "source/prompt.txt", "path": "source/prompt.txt",
     "stage": "intake", "applies": ("now", "agenda", "unknown")},
    {"key": "source/environment.json", "path": "source/environment.json",
     "stage": "intake", "applies": ("now", "agenda", "unknown")},
    {"key": "source/scope.md", "path": "source/scope.md",
     "stage": "scoping", "applies": ("agenda",)},
    {"key": "source/resolved_intent.md", "path": "source/resolved_intent.md",
     "stage": "scoping", "applies": ("agenda",)},
    {"key": "source/recall_citations.json", "path": "source/recall_citations.json",
     "stage": "recall", "applies": ("agenda",)},
    {"key": "source/skills_manifest.jsonl", "path": "source/skills_manifest.jsonl",
     "stage": "skill-injection", "applies": ("agenda",)},
    {"key": "build/calls/", "path": "build/calls", "is_dir": True,
     "stage": "llm-io", "applies": ("now", "agenda", "unknown")},
    {"key": "build/captains_log_slice.jsonl", "path": "build/captains_log_slice.jsonl",
     "stage": "events", "applies": ("agenda",)},
    {"key": "build/<loop>-plan.md", "glob": "build/loop-*-plan.md",
     "stage": "planning", "applies": ("agenda",)},
    {"key": "build/<loop>-log.json", "glob": "build/loop-*-log.json",
     "stage": "step-exec", "applies": ("agenda",)},
    {"key": "build/<loop>-step-*.md", "glob": "build/loop-*-step-*.md",
     "stage": "step-exec", "applies": ("agenda",)},
    {"key": "build/<loop>-report.html", "glob": "build/loop-*-report.html",
     "stage": "reporting", "applies": ("agenda",)},
    {"key": "build/checkpoint.json", "path": "build/checkpoint.json",
     "stage": "step-exec", "applies": ("agenda",)},
    {"key": "run_card.json", "path": "run_card.json",
     "stage": "curation", "applies": ("now", "agenda", "unknown")},
    {"key": "source/skill_attribution.json", "path": "source/skill_attribution.json",
     "stage": "attribution", "applies": ("agenda",)},
)

# The attribution seam (skill_attribution.json + the durable dispatch join)
# shipped 2026-07-29. Runs older than that CANNOT have it, so a single
# whole-history rate would understate today's coverage and hide a live
# regression. Bucketing by era is what keeps the number honest.
#
# CAUTION, learned from the first box run (2026-08-01): a single boundary this
# late leaves a tiny post bucket (n=8 of 734), and every "100% post-era" cell
# then rests on a handful of runs. Worse, the whole-history column pools runs
# that PREDATE a feature with runs that could have used it — which is how the
# first run's `build/calls/` 12.6% got read as "record mode is off in
# production" when the monthly series showed 81% for the only month in which
# record mode existed. **The monthly series below is the load-bearing view;
# the era columns are a coarse summary and must not be read alone.**
ERA_BOUNDARY = "2026-07-29"

# Below this many runs, a percentage is noise. Rendered cells carry a marker so
# nobody quotes a 100% that is 3-of-3.
THIN_DENOMINATOR = 20

TERMINAL_STATUSES = {"done", "stuck", "error", "failed", "interrupted",
                     "complete", "completed", "partial"}


def _parse_ts(value: Any) -> Optional[datetime]:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError, AttributeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _read_json(path: Path) -> Optional[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _iter_jsonl(path: Path):
    if not path.is_file():
        return
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            yield row


def workspace_root() -> Path:
    env = os.environ.get("MARO_WORKSPACE") or os.environ.get("OPENCLAW_WORKSPACE")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".maro" / "workspace"


# ---------------------------------------------------------------------------
# Per-run inspection
# ---------------------------------------------------------------------------

def inspect_run(run_dir: Path) -> Dict[str, Any]:
    """Presence matrix + liveness classification for one run dir. Never raises."""
    meta = _read_json(run_dir / "metadata.json") or {}
    lane = (meta.get("lane") or "unknown").strip().lower()
    if lane not in ("now", "agenda"):
        lane = "unknown"

    status = (meta.get("status") or "").strip().lower()
    ended_at = meta.get("ended_at")
    started_at = meta.get("started_at")

    # In-flight runs legitimately lack finalize-time artifacts. Counting them as
    # gaps would manufacture findings. A run is "settled" only once it carries a
    # terminal status or an end timestamp.
    settled = bool(ended_at) or status in TERMINAL_STATUSES

    present: Dict[str, bool] = {}
    applicable: Dict[str, bool] = {}
    for spec in ARTIFACTS:
        applicable[spec["key"]] = lane in spec["applies"]
        if spec.get("glob"):
            hit = any(run_dir.glob(spec["glob"]))
        elif spec.get("is_dir"):
            target = run_dir / spec["path"]
            hit = target.is_dir() and any(target.iterdir())
        else:
            hit = (run_dir / spec["path"]).is_file()
        present[spec["key"]] = hit

    call_dir = run_dir / "build" / "calls"
    call_count = 0
    if call_dir.is_dir():
        call_count = sum(1 for _ in call_dir.glob("call-*.json"))

    # EDGE 2 evidence: record_llm_call returns None silently when recording is
    # off or no run-dir is pinned. A settled run with zero captured calls is
    # either recording-disabled or a context-var drop — both are invisible from
    # inside the run, which is exactly why the census has to look from outside.
    zero_calls = settled and call_count == 0

    era = "post" if str(started_at or "") >= ERA_BOUNDARY else "pre"

    return {
        "run": run_dir.name,
        "handle_id": meta.get("handle_id") or run_dir.name.split("-")[0],
        "lane": lane,
        "status": status or "(none)",
        "settled": settled,
        "started_at": started_at,
        "era": era,
        "call_count": call_count,
        "zero_calls": zero_calls,
        "has_metadata": bool(meta),
        "present": present,
        "applicable": applicable,
        # Recorded so a coverage number can be re-derived per measurement class
        # rather than pooling canaries with organic work.
        "measurement_class": meta.get("measurement_class") or "unknown",
        "dry_run": bool(meta.get("dry_run")),
        # Corpus-shape fields. Not the prompt itself (census.json is already
        # large, and prompts are user content) — just enough to tell a corpus
        # of GOALS from a corpus of STEPS. The 2026-05 era opened a run-dir per
        # step: 476 dirs, 120 distinct prompts, median length 61, one
        # adversarial-verification step repeated 46 times. Pooled with real
        # goals it silently became 65% of every all-history percentage.
        "prompt_len": len(str(meta.get("prompt") or "")),
        "prompt_fp": hashlib.sha1(
            str(meta.get("prompt") or "").strip().lower().encode()
        ).hexdigest()[:12],
    }


# ---------------------------------------------------------------------------
# Verdictability (LT-0 a/b): can this run's outcome row ever BE verdicted?
# ---------------------------------------------------------------------------

def census_outcomes(outcomes_path: Path) -> Dict[str, Any]:
    total = 0
    no_loop_id = 0
    verdicted = 0
    done_no_verdict = 0
    by_task_type_blind: Counter = Counter()
    examples_blind: List[str] = []
    examples_done_no_verdict: List[str] = []
    # Same lesson as the artifact table: a whole-history unverdictable rate
    # pools rows written before loop_id stamping existed with rows that could
    # have carried it. Without this bucket the number reads as a live outage.
    monthly: Dict[str, Dict[str, int]] = defaultdict(
        lambda: {"total": 0, "no_loop_id": 0, "verdicted": 0})

    for row in _iter_jsonl(outcomes_path):
        total += 1
        loop_id = row.get("loop_id")
        has_verdict = row.get("goal_achieved") is not None
        status = (row.get("status") or "").strip().lower()

        month = str(row.get("recorded_at") or "")[:7] or "unknown"
        monthly[month]["total"] += 1
        if not loop_id:
            monthly[month]["no_loop_id"] += 1
        if has_verdict:
            monthly[month]["verdicted"] += 1

        if not loop_id:
            no_loop_id += 1
            by_task_type_blind[row.get("task_type") or "(none)"] += 1
            if len(examples_blind) < 5:
                examples_blind.append(str(row.get("goal", ""))[:70])
        if has_verdict:
            verdicted += 1
        elif loop_id and status == "done":
            # Has the join key, finished cleanly, still never judged: closure
            # never ran. Distinct from the no-loop-id class — different fix.
            done_no_verdict += 1
            if len(examples_done_no_verdict) < 5:
                examples_done_no_verdict.append(str(row.get("goal", ""))[:70])

    return {
        "total": total,
        "no_loop_id": no_loop_id,
        "unverdictable_pct": round(100.0 * no_loop_id / total, 1) if total else 0.0,
        "verdicted": verdicted,
        "done_with_loop_id_no_verdict": done_no_verdict,
        "blind_by_task_type": dict(by_task_type_blind.most_common(10)),
        "examples_unverdictable": examples_blind,
        "examples_done_no_verdict": examples_done_no_verdict,
        "by_month": {m: dict(v) for m, v in sorted(monthly.items())},
    }


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def _month_of(row: Dict[str, Any]) -> str:
    return str(row.get("started_at") or "")[:7] or "unknown"


def monthly_coverage(runs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Per-month presence rate per artifact — the view that keeps eras honest.

    A whole-history rate pools runs from before a feature existed with runs
    that could have used it, and reads as a live outage. The month a feature
    ships is visible here as a step change; a real regression is visible as a
    decline. The first box run needed exactly this to tell those apart.
    """
    settled = [r for r in runs if r["settled"]]
    months = sorted({_month_of(r) for r in settled})
    out: Dict[str, Any] = {"months": months, "runs_per_month": {}, "artifacts": {}}
    for m in months:
        out["runs_per_month"][m] = sum(1 for r in settled if _month_of(r) == m)
    for spec in ARTIFACTS:
        key = spec["key"]
        cells: Dict[str, Any] = {}
        for m in months:
            rows = [r for r in settled
                    if _month_of(r) == m and r["applicable"][key]]
            if not rows:
                cells[m] = None
                continue
            hits = sum(1 for r in rows if r["present"][key])
            cells[m] = {"n": len(rows), "present": hits,
                        "pct": round(100.0 * hits / len(rows), 1),
                        "thin": len(rows) < THIN_DENOMINATOR}
        out["artifacts"][key] = cells

    # Ship-month inference: the first month an artifact appears AT ALL is the
    # earliest month the writer could have existed. Coverage before it is not a
    # gap — the feature wasn't there. Reporting a "since first seen" rate is
    # what separates "never shipped / shipped late" from "shipped and dropping",
    # which is the exact distinction the first box run got wrong by pooling.
    # Corpus composition per month — makes the denominator's CONTENTS visible,
    # not just its size. A month of goals and a month of step-level pseudo-runs
    # produce the same run count and completely different meaning.
    out["composition"] = {}
    for m in months:
        rows = [r for r in settled if _month_of(r) == m]
        fps = [r.get("prompt_fp", "") for r in rows]
        lens = sorted(r.get("prompt_len", 0) for r in rows)
        counts = Counter(fps)
        top_n = counts.most_common(1)[0][1] if counts else 0
        distinct = len(counts)
        median = lens[len(lens) // 2] if lens else 0
        out["composition"][m] = {
            "runs": len(rows),
            "distinct_prompts": distinct,
            "median_prompt_len": median,
            "max_repeat": top_n,
            "distinct_ratio": round(distinct / len(rows), 2) if rows else None,
            # Heuristic, deliberately conservative and advisory only: short,
            # heavily-repeated prompts are step texts, not goals.
            "step_shaped": bool(rows) and median < 100 and distinct < len(rows) / 2,
        }

    out["since_first_seen"] = {}
    for spec in ARTIFACTS:
        key = spec["key"]
        cells = out["artifacts"][key]
        first = next((m for m in months
                      if cells.get(m) and cells[m]["present"] > 0), None)
        if first is None:
            out["since_first_seen"][key] = {
                "first_seen": None, "n": 0, "present": 0, "pct": None,
                "note": "never observed — writer may not exist yet",
            }
            continue
        rows = [r for r in settled
                if _month_of(r) >= first and r["applicable"][key]]
        hits = sum(1 for r in rows if r["present"][key])
        out["since_first_seen"][key] = {
            "first_seen": first,
            "n": len(rows),
            "present": hits,
            "pct": round(100.0 * hits / len(rows), 1) if rows else None,
            "thin": len(rows) < THIN_DENOMINATOR,
        }
    return out


def aggregate(runs: List[Dict[str, Any]]) -> Dict[str, Any]:
    settled = [r for r in runs if r["settled"]]

    coverage: Dict[str, Any] = {}
    for spec in ARTIFACTS:
        key = spec["key"]
        rows = [r for r in settled if r["applicable"][key]]
        hits = sum(1 for r in rows if r["present"][key])
        pre = [r for r in rows if r["era"] == "pre"]
        post = [r for r in rows if r["era"] == "post"]
        coverage[key] = {
            "stage": spec["stage"],
            "applicable_runs": len(rows),
            "present": hits,
            "missing": len(rows) - hits,
            "pct": round(100.0 * hits / len(rows), 1) if rows else None,
            "pct_pre_era": round(100.0 * sum(1 for r in pre if r["present"][key]) / len(pre), 1) if pre else None,
            "pct_post_era": round(100.0 * sum(1 for r in post if r["present"][key]) / len(post), 1) if post else None,
            "missing_runs": [r["run"] for r in rows if not r["present"][key]][:10],
        }

    by_stage: Dict[str, List[str]] = defaultdict(list)
    for key, row in coverage.items():
        if row["pct"] is not None and row["pct"] < 100.0:
            by_stage[row["stage"]].append(f"{key} {row['pct']}%")

    return {
        "runs_total": len(runs),
        "runs_settled": len(settled),
        "runs_in_flight": len(runs) - len(settled),
        "by_lane": dict(Counter(r["lane"] for r in runs)),
        "by_status": dict(Counter(r["status"] for r in runs).most_common(10)),
        "zero_call_runs": [r["run"] for r in settled if r["zero_calls"]],
        "zero_call_count": sum(1 for r in settled if r["zero_calls"]),
        "coverage": coverage,
        "incomplete_stages": {k: v for k, v in by_stage.items()},
    }


def render_text(report: Dict[str, Any]) -> str:
    agg = report["runs"]
    out: List[str] = []
    add = out.append

    add("=" * 74)
    add("PROVENANCE CENSUS — what the runs actually wrote down")
    add("=" * 74)
    add(f"generated : {report['generated_at']}")
    add(f"workspace : {report['workspace']}")
    add(f"host      : {report['host']}")
    if report.get("git_commit"):
        add(f"commit    : {report['git_commit']}")
    add(f"window    : {report['window']}")
    add("")

    add(f"Runs scanned : {agg['runs_total']}  "
        f"(settled {agg['runs_settled']}, in-flight {agg['runs_in_flight']} — "
        f"in-flight excluded from coverage)")
    add(f"By lane      : {agg['by_lane']}")
    add(f"By status    : {agg['by_status']}")
    add("")

    add("-" * 74)
    add("ARTIFACT COVERAGE (settled runs, per-lane applicability honored)")
    add("-" * 74)
    add("READ THE 'since' COLUMN, NOT 'all'. 'all' spans the whole workspace")
    add("including runs written before a given writer existed, so a feature")
    add("that shipped late reads as broken. 'since' starts at the first month")
    add("the artifact was ever observed. '~' = thin denominator (<%d runs)."
        % THIN_DENOMINATOR)
    add("")
    sfs = (report.get("monthly") or {}).get("since_first_seen", {})
    add(f"{'artifact':<34}{'stage':<16}{'all':>10}{'first':>9}{'since':>10}")
    for key, row in report["runs"]["coverage"].items():
        if not row["applicable_runs"]:
            add(f"{key:<34}{row['stage']:<16}{'n/a':>10}{'—':>9}{'—':>10}")
            continue
        allpct = "-" if row["pct"] is None else f"{row['pct']:.0f}%"
        s = sfs.get(key) or {}
        first = s.get("first_seen") or "—"
        if s.get("pct") is None:
            since = "never"
        else:
            since = ("~" if s.get("thin") else "") + f"{s['pct']:.0f}%"
        add(f"{key:<34}{row['stage']:<16}{allpct:>10}{first:>9}{since:>10}")
    add("")

    mon = report.get("monthly") or {}
    months = mon.get("months") or []
    comp = mon.get("composition") or {}
    if comp:
        add("-" * 74)
        add("CORPUS COMPOSITION — what the denominator actually CONTAINS")
        add("-" * 74)
        add("A month of goals and a month of step-level pseudo-runs have the")
        add("same run count and nothing else in common. STEP-SHAPED months are")
        add("flagged: short, heavily-repeated prompts are step texts opened as")
        add("their own run dirs, and pooling them dilutes every rate above.")
        add("")
        add(f"{'month':<10}{'runs':>7}{'distinct':>10}{'med len':>9}"
            f"{'max rep':>9}  shape")
        for m in months:
            c = comp.get(m) or {}
            shape = "STEP-SHAPED (exclude)" if c.get("step_shaped") else "goal-shaped"
            add(f"{m:<10}{c.get('runs', 0):>7}{c.get('distinct_prompts', 0):>10}"
                f"{c.get('median_prompt_len', 0):>9}{c.get('max_repeat', 0):>9}"
                f"  {shape}")
        flagged = [m for m in months if (comp.get(m) or {}).get("step_shaped")]
        if flagged:
            excl = sum((comp[m] or {}).get("runs", 0) for m in flagged)
            tot = sum((comp[m] or {}).get("runs", 0) for m in months)
            add("")
            add(f"WARNING: {excl} of {tot} settled runs ({100*excl//max(1,tot)}%)"
                f" sit in step-shaped month(s) {', '.join(flagged)}.")
            add("Every all-history percentage above is computed over that pool.")
            add("Read the per-month columns for those artifacts, not 'all' or")
            add("even 'since' when first-seen predates the flagged month(s).")
        add("")
    if months:
        add("-" * 74)
        add("PER-MONTH COVERAGE — the load-bearing view")
        add("-" * 74)
        add("A whole-history rate pools runs from BEFORE a feature existed with")
        add("runs that could have used it, and reads as a live outage. Read the")
        add("month a feature ships as a step change; read a decline as a")
        add("regression. '~' marks a cell with a thin denominator (<%d runs)."
            % THIN_DENOMINATOR)
        add("")
        add(f"{'artifact':<34}" + "".join(f"{m:>10}" for m in months))
        add(f"{'(runs settled)':<34}"
            + "".join(f"{mon['runs_per_month'].get(m, 0):>10}" for m in months))
        for key, cells in mon.get("artifacts", {}).items():
            line = f"{key:<34}"
            for m in months:
                cell = cells.get(m)
                if not cell:
                    line += f"{'—':>10}"
                else:
                    mark = "~" if cell["thin"] else " "
                    line += f"{mark + str(int(cell['pct'])) + '%':>10}"
            add(line)
        add("")

    ocm = report["outcomes"].get("by_month") or {}
    if ocm:
        add("-" * 74)
        add("PER-MONTH VERDICTABILITY (outcomes.jsonl)")
        add("-" * 74)
        add(f"{'month':<12}{'rows':>8}{'no loop_id':>13}{'blind %':>10}{'verdicted':>11}")
        for m, v in ocm.items():
            pct = 100.0 * v["no_loop_id"] / v["total"] if v["total"] else 0.0
            add(f"{m:<12}{v['total']:>8}{v['no_loop_id']:>13}{pct:>9.1f}%{v['verdicted']:>11}")
        add("")

    # Same denominator discipline as the coverage table: this summary used to
    # quote all-history percentages, which is the exact number that read a
    # working feature as an outage. Rank by the since-first-seen rate instead.
    incomplete = []
    for key, s in sfs.items():
        if s.get("pct") is None or s["pct"] >= 100.0:
            continue
        stage = report["runs"]["coverage"][key]["stage"]
        incomplete.append((s["pct"], stage, key, s))
    if incomplete:
        add("-" * 74)
        add("INCOMPLETE PAPER TRAIL — ranked by 'since first seen', worst first")
        add("-" * 74)
        for pct, stage, key, s in sorted(incomplete):
            mark = "~" if s.get("thin") else ""
            add(f"  {mark + format(pct, '.0f') + '%':>6}  {stage:<16} {key}"
                f"   (since {s['first_seen']}, n={s['n']})")
        add("")

    add("-" * 74)
    add("EDGE 2 — silent record-mode drop")
    add("-" * 74)
    calls_first = (sfs.get("build/calls/") or {}).get("first_seen")
    _settled_rows = [r for r in report.get("run_detail", []) if r["settled"]]
    in_era = [r for r in _settled_rows
              if calls_first and _month_of(r) >= calls_first]
    era_zero = [r for r in in_era if r["zero_calls"]]
    if calls_first:
        # The all-history count is NOT the finding: most zero-call runs simply
        # predate record mode. Only runs since the writer first appeared can
        # be a drop at all.
        add(f"Record mode first observed {calls_first}. Runs BEFORE it cannot")
        add("be drops — they are the permanently-blind historical corpus.")
        add(f"  historical (pre-{calls_first}, unrecoverable): "
            f"{agg['zero_call_count'] - len(era_zero)} run(s)")
        add(f"  ACTUAL DROPS (since {calls_first}): {len(era_zero)} of "
            f"{len(in_era)} run(s)"
            + (f" — {100.0*len(era_zero)/len(in_era):.0f}%" if in_era else ""))
        if era_zero:
            # Discriminator: "the run died before making a call" would explain
            # zero records innocently. A run that produced a PLAN provably made
            # LLM calls (decompose is an LLM call), and one that reached `done`
            # ran to completion. Those two counts separate an innocent early
            # death from a genuine silent drop — ask it here so the reader
            # doesn't have to.
            planned = sum(1 for r in era_zero
                          if r["applicable"].get("build/<loop>-plan.md")
                          and r["present"].get("build/<loop>-plan.md"))
            finished = sum(1 for r in era_zero if r["status"] == "done")
            add(f"  of those drops: {planned} produced a PLAN (so LLM calls")
            add(f"  provably happened) and {finished} reached status=done.")
            if planned or finished:
                add("  => NOT explained by runs dying early. Recording was off,")
                add("     or the run-dir ContextVar was not inherited on the")
                add("     calling path. Both fail silently today.")
            for r in era_zero[:10]:
                add(f"  - {r['run']}  ({r['lane']}, {r['status']})")
    elif agg["zero_call_count"]:
        add(f"{agg['zero_call_count']} settled run(s) captured ZERO LLM calls,")
        add("and NO run has ever captured one — the writer may not exist here.")
    else:
        add("None — every settled run captured at least one LLM call.")
    add("")

    oc = report["outcomes"]
    add("-" * 74)
    add("VERDICTABILITY (outcomes.jsonl) — CROSS-CHECK ONLY")
    add("-" * 74)
    add("`python3 -m verdict_flow` is the authority here: it separates STOCK")
    add("from FLOW and counts verdict EVENTS by arrival week. These totals are")
    add("all-history stock and will read alarmingly low for the same reason the")
    add("artifact table does — April-era rows predate verdict stamping and are")
    add("not backfilled, by decree. Use the per-month table below, not the")
    add("headline, and prefer verdict_flow over both.")
    add(f"rows total                    : {oc['total']}")
    add(f"no loop_id (unverdictable)    : {oc['no_loop_id']}  ({oc['unverdictable_pct']}%)")
    add(f"carrying a verdict            : {oc['verdicted']}")
    add(f"done + loop_id + NO verdict   : {oc['done_with_loop_id_no_verdict']}  (closure never ran)")
    if oc["blind_by_task_type"]:
        add(f"unverdictable by task_type    : {oc['blind_by_task_type']}")
    for goal in oc["examples_unverdictable"]:
        add(f"    blind e.g. : {goal}")
    for goal in oc["examples_done_no_verdict"]:
        add(f"    no-verdict : {goal}")
    add("")
    add("=" * 74)
    add("Gaps found are the deliverable. Re-run after fixes and diff the JSON.")
    add("=" * 74)
    return "\n".join(out)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workspace", type=Path, default=None,
                    help="workspace root (default: $MARO_WORKSPACE or ~/.maro/workspace)")
    ap.add_argument("--since", default=None,
                    help="only runs started on/after this ISO date (e.g. 2026-07-01)")
    ap.add_argument("--limit", type=int, default=0,
                    help="cap the number of runs scanned, newest first (0 = all)")
    ap.add_argument("--out", type=Path, default=None,
                    help="directory for census.json + census.txt artifacts")
    ap.add_argument("--json", action="store_true",
                    help="print JSON to stdout instead of the text summary")
    args = ap.parse_args(argv)

    ws = (args.workspace or workspace_root()).expanduser()
    runs_root = ws / "runs"
    if not runs_root.is_dir():
        print(f"no runs directory at {runs_root}", file=sys.stderr)
        return 2

    dirs = sorted((d for d in runs_root.iterdir() if d.is_dir()),
                  key=lambda d: d.stat().st_mtime, reverse=True)
    if args.limit:
        dirs = dirs[: args.limit]

    inspected: List[Dict[str, Any]] = []
    for d in dirs:
        row = inspect_run(d)
        if args.since and str(row.get("started_at") or "") < args.since:
            continue
        inspected.append(row)

    git_commit = ""
    head = Path(__file__).resolve().parents[1] / ".git" / "HEAD"
    try:
        ref = head.read_text(encoding="utf-8").strip()
        if ref.startswith("ref: "):
            ref_path = head.parent / ref[5:]
            git_commit = ref_path.read_text(encoding="utf-8").strip()[:12]
        else:
            git_commit = ref[:12]
    except OSError:
        pass

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "workspace": str(ws),
        "host": os.uname().nodename if hasattr(os, "uname") else "",
        "git_commit": git_commit,
        "window": args.since or "all history",
        "era_boundary": ERA_BOUNDARY,
        "runs": aggregate(inspected),
        "monthly": monthly_coverage(inspected),
        "run_detail": inspected,
        "outcomes": census_outcomes(ws / "memory" / "outcomes.jsonl"),
    }

    text = render_text(report)
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(text)

    if args.out:
        try:
            args.out.mkdir(parents=True, exist_ok=True)
            (args.out / "census.json").write_text(
                json.dumps(report, indent=2, default=str), encoding="utf-8")
            (args.out / "census.txt").write_text(text, encoding="utf-8")
            print(f"\nartifacts: {args.out}/census.json, {args.out}/census.txt",
                  file=sys.stderr)
        except OSError as exc:
            print(f"could not write artifacts: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
