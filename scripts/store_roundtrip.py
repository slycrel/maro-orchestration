#!/usr/bin/env python3
"""Does the reader load what the writer wrote?

The 2026-08-04 dynamic-guardrail bug was not a missing reader. There WAS a
reader, it ran on every step, and it had silently returned `[]` for the
store's entire life: the writer stamped `added_at` as an ISO string, the
reader compared it to `time.time() - ttl`, `str < float` raised, and the
per-row `except Exception: continue` one frame up discarded the row whole.
Every unit test passed, because each side was tested against its own idea
of the contract and nothing ever ran one through the other.

A writer/reader *existence* census — the shape BACKLOG item (a) specifies —
would have called that store healthy. What catches it is counting: rows on
disk vs rows the loader hands back, with the gap explained or not.

    PYTHONPATH=src python3 scripts/store_roundtrip.py
    PYTHONPATH=src python3 scripts/store_roundtrip.py --json

**Read-only.** Every probe loads; none writes. Run it against the live
workspace.

This is a diagnostic, not a gate. The probe table below is hand-written,
which is exactly the rot risk BACKLOG item (a) names — so the script also
walks the workspace for every store file it has no probe for and prints
them. Uncovered is reported, never implied. When the store-path registry
lands, this table is what it replaces.

A gap is not automatically a bug. Several loaders filter by design (decay,
contested, dedup, tier splits). Each probe declares what it is allowed to
drop; a gap outside that is the work-list.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

BIG = 10_000_000


def _workspace() -> Path:
    ws = os.environ.get("MARO_WORKSPACE") or os.environ.get("OPENCLAW_WORKSPACE")
    return Path(ws) if ws else Path.home() / ".maro" / "workspace"


def _disk_rows(path: Path) -> Dict[str, int]:
    """Non-empty lines, and how many of them are even JSON."""
    if not path.exists():
        return {"lines": 0, "parsed": 0, "unparseable": 0}
    lines = parsed = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        lines += 1
        try:
            json.loads(line)
            parsed += 1
        except ValueError:
            pass
    return {"lines": lines, "parsed": parsed, "unparseable": lines - parsed}


# --- the probe table -------------------------------------------------------
# `drops` names what this loader is ALLOWED to drop. An empty string means
# the loader claims to be lossless and any gap is a finding.

def _probes() -> List[Dict[str, Any]]:
    import captains_log
    import constraint
    import evolver_store
    import introspect
    import knowledge_lens
    import knowledge_web
    import memory_ledger
    import metrics
    import navigator_shadow
    import skills

    mem = _workspace() / "memory"

    def tiered(tier):
        return lambda: knowledge_web.load_tiered_lessons(tier=tier, limit=None,
                                                         raw=True)

    return [
        # --- the learning stores: these feed injection, so a silent drop
        # --- here is lost learning, not just a missing row in a report.
        dict(name="tiered lessons (medium)", path=mem / "medium" / "lessons.jsonl",
             load=tiered("medium"), drops=""),
        dict(name="tiered lessons (long)", path=mem / "long" / "lessons.jsonl",
             load=tiered("long"), drops=""),
        dict(name="flat lessons", path=mem / "lessons.jsonl",
             load=lambda: memory_ledger.load_lessons(limit=BIG),
             drops="contested rows, by design"),
        dict(name="outcomes", path=mem / "outcomes.jsonl",
             load=lambda: memory_ledger.load_outcomes(limit=BIG), drops=""),
        dict(name="knowledge nodes", path=mem / "knowledge_nodes.jsonl",
             # status=None or it defaults to active-only, which is a filter,
             # not a round trip.
             load=lambda: knowledge_web.load_knowledge_nodes(status=None),
             # Loader validates numeric fields since 2026-08-21
             # (edge-review r3): a validation drop is by design, announced
             # by the loader's own drift warning.
             drops="malformed rows (non-numeric/out-of-range confidence "
                   "or times_applied), by design"),
        dict(name="knowledge edges", path=mem / "knowledge_edges.jsonl",
             load=knowledge_web.load_knowledge_edges,
             # Loader validates the whole row since 2026-08-21 (edge-review
             # r1-r3): a validation drop is by design, announced by the
             # loader's own drift warning.
             drops="malformed rows (bad endpoint ids, non-numeric/"
                   "non-finite/out-of-range weight), by design"),
        dict(name="skills", path=mem / "skills.jsonl",
             load=skills.load_skills,
             # Probed 2026-08-04: disk == loaded, i.e. this store is
             # rewritten in place, not appended. So no gap is expected and
             # any future gap is a finding.
             drops=""),
        dict(name="standing rules", path=mem / "standing_rules.jsonl",
             load=knowledge_lens.load_standing_rules, drops=""),
        dict(name="hypotheses", path=mem / "hypotheses.jsonl",
             load=knowledge_lens.load_hypotheses, drops=""),

        # --- the self-improvement stores
        dict(name="evolver suggestions", path=mem / "suggestions.jsonl",
             load=lambda: evolver_store.load_suggestions(limit=BIG), drops=""),
        dict(name="dynamic constraints", path=mem / "dynamic-constraints.jsonl",
             load=constraint._load_dynamic_constraints,
             drops="expired rows (TTL) and prose patterns, at read time"),
        dict(name="diagnoses", path=mem / "diagnoses.jsonl",
             load=lambda: introspect.load_diagnoses(limit=BIG), drops=""),
        dict(name="verification outcomes",
             path=mem / "verification_outcomes.jsonl",
             load=lambda: knowledge_lens.load_verification_outcomes(limit=BIG),
             drops=""),

        # --- the record
        dict(name="captain's log", path=mem / "captains_log.jsonl",
             load=lambda: captains_log.load_log(limit=BIG), drops=""),
        dict(name="task ledger", path=mem / "task_ledger.jsonl",
             load=lambda: memory_ledger.load_task_ledger(limit=BIG), drops=""),
        dict(name="step costs", path=mem / "step-costs.jsonl",
             load=lambda: metrics.load_step_costs(limit=BIG), drops=""),
        dict(name="skill stats", path=mem / "skill-stats.jsonl",
             load=skills.get_all_skill_stats,
             # Probed 2026-08-04: disk == loaded, i.e. this store is
             # rewritten in place, not appended. So no gap is expected and
             # any future gap is a finding.
             drops=""),
        dict(name="navigator lessons", path=mem / "navigator_lessons.jsonl",
             load=lambda: navigator_shadow.load_navigator_lessons(limit=BIG),
             drops=""),
    ]


def probe_all() -> List[Dict[str, Any]]:
    rows = []
    for spec in _probes():
        path: Path = spec["path"]
        row = {"name": spec["name"], "path": str(path), "drops": spec["drops"]}
        row.update(_disk_rows(path))
        try:
            loaded = spec["load"]()
            row["loaded"] = len(loaded)
            row["error"] = ""
        except Exception as exc:                       # the probe's whole point
            row["loaded"] = None
            row["error"] = f"{type(exc).__name__}: {exc}"
        rows.append(row)
    return rows


def uncovered_stores(rows: List[Dict[str, Any]]) -> List[str]:
    """Every workspace store file no probe above touches.

    Per-run and per-project stores are excluded: they're run artifacts with
    their own readers, not the shared learning substrate this is about.
    """
    covered = {r["path"] for r in rows}
    ws = _workspace()
    out = []
    for p in sorted(ws.rglob("*.jsonl")):
        rel = p.relative_to(ws).parts
        if rel[0] in ("runs", "projects") or any("backup" in x for x in rel):
            continue
        if str(p) not in covered:
            out.append(str(p.relative_to(ws)))
    return out


def classify(row: Dict[str, Any]) -> tuple:
    """(verdict text, is_finding) for one probed store.

    A raising loader and an undeclared drop are findings. A drop the probe
    declared is not — but it still gets printed with its reason, because
    "expected" is a claim someone has to be able to check.
    """
    if row["loaded"] is None:
        return f"LOADER RAISED — {row['error'][:44]}", True
    if row["lines"] == 0:
        verdict, finding = "empty store", False
    elif row["loaded"] < row["parsed"]:
        gap = row["parsed"] - row["loaded"]
        if row["drops"]:
            verdict, finding = f"-{gap} — expected: {row['drops']}", False
        else:
            verdict, finding = f"-{gap} DROPPED, loader claims lossless", True
    else:
        verdict, finding = "ok", False
    if row["unparseable"]:
        verdict += f"  [{row['unparseable']} unparseable line(s)]"
    return verdict, finding


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    rows = probe_all()
    unc = uncovered_stores(rows)

    if args.json:
        for r in rows:
            r["verdict"], r["finding"] = classify(r)
        print(json.dumps({"probes": rows, "uncovered": unc}, indent=2))
        return 0

    print(f"{'store':<28} {'disk':>7} {'bad':>5} {'loaded':>7}  verdict")
    print("-" * 78)
    findings = 0
    for r in rows:
        verdict, finding = classify(r)
        findings += bool(finding)
        loaded = "raised" if r["loaded"] is None else r["loaded"]
        print(f"{r['name']:<28} {r['lines']:>7} {r['unparseable']:>5} "
              f"{loaded:>7}  {verdict}")

    print(f"\n{findings} finding(s).")
    if unc:
        print(f"\n{len(unc)} workspace store(s) with no probe here — coverage "
              f"gap, stated not hidden:")
        for u in unc:
            print(f"    {u}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
