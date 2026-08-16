#!/usr/bin/env python3
"""Census the claim extractor against the live learning stores.

The measurement surface for MINT_GROUNDING_DESIGN §4's two falsifiers:
the >30%-unprobed rate (widen the lexicon) and its twin, the precision
rate (stamps landing on text that asserts nothing — a gate failure, not
a widening trigger). Both must be read off the REAL stores; fixtures
cannot show either.

The 2026-08-16 baseline this replaces guesswork with — the run that
refuted slice 2b's premise before a line of it was written:

    store                       hits(pre-gate)  hits(gated)  true
    skills.jsonl (398 rows)                 76            1     0
    skills-lite .md (56 files)              24            0     0
    lessons live+archive (914)             103           24   ~20
    knowledge nodes (1,250)                100           12    ~5

"true" is a hand read, not a computation — the script reports counts and
prints the sentences so the next reader can redo that judgement rather
than inherit it. `--stamped` reports what the stores actually carry
(status mix over rows minted since slice 1), which is the unprobed-rate
half of the falsifier.

Usage:
    PYTHONPATH=src python3 scripts/mint_grounding_census.py [--show]
    MARO_WORKSPACE=~/maro-box-copy/workspace \\
        PYTHONPATH=src python3 scripts/mint_grounding_census.py --stamped
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Iterator, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from config import workspace_root  # noqa: E402
from mint_grounding import extract_claims  # noqa: E402

Row = Tuple[str, str]


def _jsonl(path: Path) -> Iterator[dict]:
    if not path.is_file():
        return
    for line in path.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except Exception:
            continue


def _skill_rows(ws: Path) -> Iterator[Row]:
    for r in _jsonl(ws / "memory" / "skills.jsonl"):
        yield (f"skill:{str(r.get('name', ''))[:40]}",
               "\n".join([str(r.get("description", "")),
                          *[str(s) for s in (r.get("steps_template") or [])],
                          str(r.get("optimization_objective", ""))]))


def _lite_rows(ws: Path) -> Iterator[Row]:
    d = ws / "skills"
    if not d.is_dir():
        return
    for f in sorted(d.glob("*.md")):
        yield (f"lite:{f.name}", f.read_text(errors="replace"))


def _lesson_rows(ws: Path) -> Iterator[Row]:
    for name in ("lessons.jsonl", "lessons_archive.jsonl"):
        for r in _jsonl(ws / "memory" / name):
            yield ("lesson", str(r.get("lesson") or r.get("lesson_text")
                                 or r.get("text") or ""))


def _node_rows(ws: Path) -> Iterator[Row]:
    for r in _jsonl(ws / "memory" / "knowledge_nodes.jsonl"):
        yield (f"node:{str(r.get('title', ''))[:40]}",
               f"{r.get('title', '')}\n{r.get('description', '')}")


STORES = (
    ("skills.jsonl", _skill_rows),
    ("skills-lite .md", _lite_rows),
    ("lessons live+archive", _lesson_rows),
    ("knowledge nodes", _node_rows),
)


def census(ws: Path, show: bool) -> None:
    print(f"workspace: {ws}")
    for label, source in STORES:
        rows = claims = with_claims = 0
        lines = []
        for tag, body in source(ws):
            rows += 1
            found = extract_claims(body)
            claims += len(found)
            with_claims += bool(found)
            for c in found:
                lines.append(f"    [{c['family']}] {tag} :: "
                             + " ".join(c["_sentence"].split())[:150])
        print(f"  {label}: {claims} claims over {rows} rows "
              f"({with_claims} rows carrying at least one)")
        if show:
            for line in lines:
                print(line)


def stamped(ws: Path) -> None:
    """Status mix of stamps the stores actually carry (unprobed falsifier)."""
    print(f"workspace: {ws}")
    for name in ("lessons.jsonl", "lessons_archive.jsonl",
                 "knowledge_nodes.jsonl", "skills.jsonl"):
        rows = list(_jsonl(ws / "memory" / name))
        stamps = [g for r in rows for g in (r.get("grounding") or [])
                  if isinstance(g, dict)]
        mix = Counter(str(g.get("status", "?")) for g in stamps)
        n_rows = sum(1 for r in rows if r.get("grounding"))
        total = sum(mix.values())
        unprobed = mix.get("unprobed", 0) / total if total else 0.0
        print(f"  {name}: {n_rows}/{len(rows)} rows stamped, "
              f"{total} stamps {dict(mix)} unprobed={unprobed:.0%}")


def recheck(ws: Path) -> int:
    """Re-extract every STORED stamp's claim and report what no longer mints.

    The audit the counts cannot do (round-1 Skeptic HIGH): "the census went
    103→24 and nothing already stamped was lost" are two different claims,
    and the second one needs a per-row diff. Reading a total and trusting it
    is exactly how the landed commit came to assert 9/9 when the truth was
    8/9 — one row's claim sat in "needed TO BE checked", which the modal
    veto correctly refuses as policy language.

    Exit 0 always: a drop is a finding to read, not a build break — some
    drops are the point (negated and counterfactual claims SHOULD stop
    minting). The operator judges each.
    """
    print(f"workspace: {ws}")
    total = dropped = 0
    for name in ("lessons.jsonl", "lessons_archive.jsonl",
                 "knowledge_nodes.jsonl", "skills.jsonl"):
        rows = [r for r in _jsonl(ws / "memory" / name) if r.get("grounding")]
        if not rows:
            continue
        for r in rows:
            text = str(r.get("lesson") or r.get("lesson_text")
                       or r.get("description") or r.get("text") or "")
            live = {c["claim"] for c in extract_claims(text)}
            for g in r.get("grounding") or []:
                if not isinstance(g, dict):
                    continue
                total += 1
                claim = str(g.get("claim", ""))
                if claim not in live:
                    dropped += 1
                    rid = str(r.get("lesson_id") or r.get("node_id")
                              or r.get("id") or "?")[:8]
                    print(f"  DROPPED {name} id={rid} "
                          f"was={g.get('status')} :: {claim[:110]}")
    print(f"  {total - dropped}/{total} stored stamps still mint "
          f"({dropped} dropped)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--show", action="store_true",
                    help="print every extracted sentence (the precision read)")
    ap.add_argument("--stamped", action="store_true",
                    help="report stamps already on the stores, not extraction")
    ap.add_argument("--recheck", action="store_true",
                    help="re-extract each STORED stamp's claim; list drops")
    args = ap.parse_args()
    ws = workspace_root()
    if not ws.is_dir():
        print(f"no workspace at {ws} (set MARO_WORKSPACE)", file=sys.stderr)
        return 1
    if args.recheck:
        return recheck(ws)
    if args.stamped:
        stamped(ws)
    else:
        census(ws, args.show)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
