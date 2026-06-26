#!/usr/bin/env python3
"""Harvest a reusable test corpus from the live workspace run/project history.

Dev-facing tooling (like correspondence.py) — NOT part of Maro's runtime. It
walks ~/.maro/workspace/runs/ and projects/ and distills the orchestration
history into thin, replayable fixture slices under
tests/fixtures/orchestration_corpus/ so future tests can drive the post-LLM
machinery (verify gating, quality-gate escalation, claim probing, stuck
classification, hallucinated-file detection, decompose shapes) against REAL
recorded inputs/outputs instead of hand-built stubs.

What it captures (the layer we actually have — see MANIFEST):
  - loop_outcomes  : goal -> status, step plan, per-step status + tokens
  - step_outcomes  : step text -> summary, result_excerpt, files_cited
  - <event_type>   : one slice per captains-log event_type (quality_gate_verdict,
                     claim_probed, closure_verdict, scope_generated, ...)

What it deliberately does NOT capture: the byte-exact assembled per-step LLM
prompt + raw response (not persisted historically) and inner tool_events
(capture is new — only future runs will have them).

Each slice is emitted twice:
  <slice>.jsonl          full records (provenance-tagged)
  <slice>.thinned.jsonl  one representative per signature, with dup_count

Usage:
    python3 scripts/harvest_corpus.py            # harvest to default out dir
    python3 scripts/harvest_corpus.py --keep 5   # keep 5 reps per cluster
    python3 scripts/harvest_corpus.py --dry-run  # counts only, no write
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

WORKSPACE = Path(os.path.expanduser("~/.maro/workspace"))
REPO = Path(__file__).resolve().parent.parent
OUT_DIR = REPO / "tests" / "fixtures" / "orchestration_corpus"

# --- secret scrubbing -------------------------------------------------------
# The corpus is committed to git, so redact anything credential-shaped before
# it lands. Conservative: a false redaction is harmless, a leaked key is not.
_SECRET_RES = [
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)(bearer|authorization|api[_-]?key|token|secret|password)\s*[:=]\s*\S{8,}"),
]


def scrub(obj):
    """Recursively redact secret-shaped substrings from any JSON value."""
    if isinstance(obj, str):
        s = obj
        for rx in _SECRET_RES:
            s = rx.sub("[REDACTED]", s)
        return s
    if isinstance(obj, list):
        return [scrub(x) for x in obj]
    if isinstance(obj, dict):
        return {k: scrub(v) for k, v in obj.items()}
    return obj


def _norm_sig(text: str) -> str:
    """Signature for thinning: lowercase, strip digits/paths/whitespace runs."""
    t = (text or "").lower()
    t = re.sub(r"/[\w./\-]+", "/PATH", t)        # collapse paths
    t = re.sub(r"\d+", "#", t)                    # collapse numbers
    t = re.sub(r"\s+", " ", t).strip()
    return hashlib.sha1(t[:400].encode()).hexdigest()[:16]


def _read_json(p: Path):
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def _iter_run_dirs():
    rroot = WORKSPACE / "runs"
    if rroot.is_dir():
        yield from (d for d in rroot.iterdir() if d.is_dir())


# --- extractors -------------------------------------------------------------

def harvest_loops(records: dict):
    """loop_outcomes from every loop-*-log.json under runs/ and projects/."""
    seen = set()
    roots = [WORKSPACE / "runs", WORKSPACE / "projects"]
    for root in roots:
        if not root.is_dir():
            continue
        for logf in root.glob("**/loop-*-log.json"):
            d = _read_json(logf)
            if not isinstance(d, dict):
                continue
            lid = d.get("loop_id")
            if lid and lid in seen:
                continue
            if lid:
                seen.add(lid)
            steps = d.get("steps") or []
            rec = {
                "goal": d.get("goal", ""),
                "project": d.get("project", ""),
                "status": d.get("status", ""),
                "stuck_reason": d.get("stuck_reason"),
                "n_steps": len(steps),
                "steps": [
                    {
                        "index": s.get("index"),
                        "text": s.get("text", ""),
                        "status": s.get("status", ""),
                        "tokens_in": s.get("tokens_in"),
                        "tokens_out": s.get("tokens_out"),
                    }
                    for s in steps
                ],
                "totals": d.get("totals"),
                "_provenance": str(logf.relative_to(WORKSPACE)),
            }
            records["loop_outcomes"].append((d.get("goal", ""), rec))


def harvest_steps(records: dict):
    """step_outcomes from every scratchpad/step_N.json."""
    roots = [WORKSPACE / "runs", WORKSPACE / "projects"]
    for root in roots:
        if not root.is_dir():
            continue
        for sf in root.glob("**/*scratchpad/step_*.json"):
            d = _read_json(sf)
            if not isinstance(d, dict) or "text" not in d:
                continue
            rec = {
                "text": d.get("text", ""),
                "summary": d.get("summary", ""),
                "result_excerpt": d.get("result_excerpt", ""),
                "files_cited": d.get("files_cited", []),
                "_provenance": str(sf.relative_to(WORKSPACE)),
            }
            records["step_outcomes"].append((d.get("text", ""), rec))


def harvest_captains(records: dict):
    """One slice per captains-log event_type, across all run slices."""
    for rd in _iter_run_dirs():
        slicef = rd / "build" / "captains_log_slice.jsonl"
        if not slicef.is_file():
            continue
        for line in slicef.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except Exception:
                continue
            et = (e.get("event_type") or "unknown").lower()
            e["_provenance"] = str(slicef.relative_to(WORKSPACE))
            sig_text = e.get("summary", "") + " " + str(e.get("subject", ""))
            records[f"event_{et}"].append((sig_text, e))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", type=int, default=3,
                    help="representatives kept per signature in the thinned slice")
    ap.add_argument("--out", default=str(OUT_DIR))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    if not WORKSPACE.is_dir():
        print(f"workspace not found: {WORKSPACE}", file=sys.stderr)
        return 1

    # slice_name -> list[(sig_text, record)]
    records: dict = defaultdict(list)
    harvest_loops(records)
    harvest_steps(records)
    harvest_captains(records)

    out = Path(args.out)
    manifest_rows = []
    for slice_name, rows in sorted(records.items()):
        full = [scrub(r) for _, r in rows]
        # thinning: bucket by signature, keep up to --keep reps, tally the rest
        buckets = defaultdict(list)
        for sig_text, r in rows:
            buckets[_norm_sig(sig_text)].append(scrub(r))
        thinned = []
        for sig, items in buckets.items():
            reps = items[: args.keep]
            for i, rep in enumerate(reps):
                rep = dict(rep)
                rep["_cluster_size"] = len(items)
                thinned.append(rep)
        manifest_rows.append((slice_name, len(full), len(buckets), len(thinned)))

        if not args.dry_run:
            out.mkdir(parents=True, exist_ok=True)
            (out / f"{slice_name}.jsonl").write_text(
                "".join(json.dumps(r, default=str) + "\n" for r in full))
            (out / f"{slice_name}.thinned.jsonl").write_text(
                "".join(json.dumps(r, default=str) + "\n" for r in thinned))

    # manifest
    total = sum(r[1] for r in manifest_rows)
    lines = [
        "# Orchestration Test Corpus",
        "",
        "Harvested from `~/.maro/workspace/runs/` + `projects/` by "
        "`scripts/harvest_corpus.py`. Dev fixtures only — not runtime data.",
        "",
        "Each slice has a full `.jsonl` and a `.thinned.jsonl` (one+ representative "
        "per normalized signature, with `_cluster_size`). Records carry "
        "`_provenance` (path under the workspace). Secret-shaped strings are redacted.",
        "",
        "**Not captured:** byte-exact per-step LLM prompt/response (never persisted) "
        "and inner tool_events (capture is new — only future runs will have them).",
        "",
        f"Total records: **{total}**",
        "",
        "| slice | records | unique sigs | thinned |",
        "|-------|--------:|------------:|--------:|",
    ]
    for name, n, uniq, thin in manifest_rows:
        lines.append(f"| {name} | {n} | {uniq} | {thin} |")
    manifest = "\n".join(lines) + "\n"

    if args.dry_run:
        print(manifest)
    else:
        (out / "MANIFEST.md").write_text(manifest)
        print(manifest)
        print(f"\nwrote {len(manifest_rows)} slices -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
