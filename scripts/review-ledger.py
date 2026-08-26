#!/usr/bin/env python3
"""Structured ledger for adversarial-review findings.

Every review round produces findings that get fixed and forgotten, and
patterns that recur across unrelated subsystems. `docs/REVIEW_PATTERNS.md`
names the patterns; this records the findings against them, so the
recurrence counts in that file stop being recalled and start being
computed.

DEV-FACING TOOLING. This is about the human/agent development loop, not
about Maro's runtime self-improvement — the same boundary `correspondence`
sits on. The ledger lives in the REPO (`review/findings.jsonl`, committed)
and deliberately NOT in `~/.maro/workspace/memory/`, so nothing in the
learning pipeline can ingest a review finding as if it were a run outcome.

Usage:
    review-ledger.py add --arc go-port --round 4 --target internal/evolver \\
        --reviewer opus --severity high --lens L12 --verdict confirmed \\
        --fix-site production --summary "load_suggestions was half a reader"

    review-ledger.py import rows.json        # bulk, a JSON list of objects
    review-ledger.py report                  # everything
    review-ledger.py report --arc go-port --by lens
    review-ledger.py lenses                  # lens ids parsed from the catalog
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

VERDICTS = ("confirmed", "hallucinated", "known-gap", "wontfix")
FIX_SITES = ("production", "test", "battery", "doc", "none")
SEVERITIES = ("high", "medium", "low", "nit")

FIELDS = ("arc", "round", "target", "reviewer", "severity", "lens",
          "verdict", "fix_site", "summary", "recorded_at")


def repo_root() -> Path:
    """The repo this script lives in — not the cwd, and not a workspace.

    Resolved from __file__ so the ledger is the same file whether the
    command is run from the shared checkout or from a worktree of it.
    """
    return Path(__file__).resolve().parent.parent


def ledger_path() -> Path:
    # MARO_REVIEW_LEDGER exists for tests. It is deliberately NOT
    # MARO_WORKSPACE: pointing this at the runtime workspace is the one
    # thing the module docstring says not to do.
    override = os.environ.get("MARO_REVIEW_LEDGER")
    if override:
        return Path(override)
    return repo_root() / "review" / "findings.jsonl"


def catalog_path() -> Path:
    return repo_root() / "docs" / "REVIEW_PATTERNS.md"


def known_lenses() -> dict[str, str]:
    """Lens id -> title, parsed from the catalog.

    The catalog is the source of truth for what a lens IS. Parsing it here
    means a typo'd lens id is caught at record time instead of quietly
    creating a one-row bucket that looks like a new pattern.
    """
    path = catalog_path()
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^###\s+((?:L|P)\d+)\s+—\s+(.*)$", line.strip())
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate(row: dict, lenses: dict[str, str], strict_lens: bool) -> list[str]:
    problems = []
    for f in ("arc", "target", "summary", "verdict"):
        if not row.get(f):
            problems.append(f"missing {f}")
    if row.get("verdict") not in VERDICTS:
        problems.append(f"verdict must be one of {VERDICTS}")
    if row.get("fix_site") and row["fix_site"] not in FIX_SITES:
        problems.append(f"fix_site must be one of {FIX_SITES}")
    if row.get("severity") and row["severity"] not in SEVERITIES:
        problems.append(f"severity must be one of {SEVERITIES}")
    lens = row.get("lens")
    if lens and lenses and lens not in lenses:
        msg = (f"lens {lens!r} is not in {catalog_path().name} "
               f"(known: {', '.join(sorted(lenses))})")
        if strict_lens:
            problems.append(msg)
        else:
            print(f"warning: {msg}", file=sys.stderr)
    return problems


def append_rows(rows: list[dict], strict_lens: bool = True) -> int:
    lenses = known_lenses()
    prepared = []
    for i, raw in enumerate(rows):
        row = {f: raw.get(f) for f in FIELDS}
        row["recorded_at"] = row["recorded_at"] or _now()
        if row.get("round") is not None:
            row["round"] = int(row["round"])
        problems = _validate(row, lenses, strict_lens)
        if problems:
            raise SystemExit(f"row {i}: " + "; ".join(problems))
        prepared.append(row)
    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for row in prepared:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(prepared)


def load_rows(arc: str | None = None) -> list[dict]:
    path = ledger_path()
    if not path.exists():
        return []
    out = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            # Report, never drop silently: a corrupt line is a fact about
            # the ledger, and a reader that hides it makes the counts a
            # quiet lie.
            print(f"warning: {path}:{n} is not JSON — skipped", file=sys.stderr)
            continue
        if arc and row.get("arc") != arc:
            continue
        out.append(row)
    return out


def cmd_report(args) -> int:
    rows = load_rows(args.arc)
    if not rows:
        print("no findings recorded yet")
        return 0
    lenses = known_lenses()

    print(f"{len(rows)} finding(s)" + (f" in arc {args.arc}" if args.arc else ""))
    print()

    verdicts = Counter(r.get("verdict") for r in rows)
    judged = sum(verdicts[v] for v in ("confirmed", "hallucinated"))
    print("verdicts:")
    for v in VERDICTS:
        if verdicts[v]:
            print(f"  {v:<14} {verdicts[v]}")
    if judged:
        rate = 100.0 * verdicts["hallucinated"] / judged
        print(f"  hallucination rate: {rate:.0f}% "
              f"({verdicts['hallucinated']}/{judged} judged)")
    print()

    if args.by in ("lens", "all"):
        print("by lens:")
        counts = Counter(r.get("lens") or "(unattributed)" for r in rows)
        for lens, n in counts.most_common():
            title = lenses.get(lens, "")
            print(f"  {lens:<6} {n:>3}  {title}")
        print()

    if args.by in ("reviewer", "all"):
        print("by reviewer (hallucination rate needs >= 3 judged):")
        per = defaultdict(Counter)
        for r in rows:
            per[r.get("reviewer") or "(unknown)"][r.get("verdict")] += 1
        for who, c in sorted(per.items()):
            j = c["confirmed"] + c["hallucinated"]
            tail = f"{100.0 * c['hallucinated'] / j:.0f}% halluc" if j >= 3 \
                else "too few judged"
            print(f"  {who:<12} {sum(c.values()):>3} finding(s)  {tail}")
        print()

    if args.by in ("fix_site", "all"):
        print("by fix site:")
        for site, n in Counter(
                r.get("fix_site") or "(unset)" for r in rows).most_common():
            print(f"  {site:<12} {n}")
        print()

    if args.by in ("round", "all"):
        print("by round (convergence — P5 says lows by 3-4):")
        per = defaultdict(Counter)
        for r in rows:
            per[(r.get("arc"), r.get("round"))][r.get("severity") or "?"] += 1
        for (arc, rnd), c in sorted(
                per.items(), key=lambda kv: (str(kv[0][0]), kv[0][1] or 0)):
            shape = " ".join(f"{c[s]}{s[0].upper()}" for s in SEVERITIES if c[s])
            print(f"  {arc} r{rnd}: {sum(c.values()):>3}  {shape}")
    return 0


def cmd_lenses(args) -> int:
    lenses = known_lenses()
    if not lenses:
        print(f"no lens catalog found at {catalog_path()}", file=sys.stderr)
        return 1
    counts = Counter(r.get("lens") for r in load_rows())
    for lens, title in sorted(lenses.items(),
                              key=lambda kv: (kv[0][0], int(kv[0][1:]))):
        print(f"{lens:<6} {counts[lens]:>3}  {title}")
    return 0


PROMPT_HEAD = """\
## Review output contract

Return your findings as a JSON list, one object per finding, and NOTHING
else after it. The list is fed straight to `review-ledger.py import`, so a
field spelled wrong is a finding that never gets counted.

Each object:

  arc       {arc!r}
  round     {round}                    (integer)
  target    the package/file you reviewed
  reviewer  {reviewer!r}
  severity  one of: high | medium | low | nit
  lens      a lens id from the catalog below, or null if none fits
  verdict   one of: confirmed | hallucinated | known-gap | wontfix
  fix_site  one of: production | test | battery | doc | none
  summary   one sentence, past tense, naming the SITE and the CONSEQUENCE

Two rules that make the data worth collecting:

1. Record findings you RETRACT as `"verdict": "hallucinated"`. The
   hallucination rate is the number this ledger exists to measure and it
   cannot be measured from the findings that survived.
2. If a finding fits no lens, set `"lens": null` and say so in the
   summary. Do NOT stretch an existing lens to fit — an unattributed row
   is a lens candidate, and a wrong attribution is noise forever.

## The lens catalog

Walk these against the whole chunk before writing findings. They are
ordered by how often they have actually fired.
"""


def cmd_prompt(args) -> int:
    lenses = known_lenses()
    if not lenses:
        print(f"no lens catalog found at {catalog_path()}", file=sys.stderr)
        return 1
    print(PROMPT_HEAD.format(arc=args.arc, round=args.round if args.round
                             is not None else 1, reviewer=args.reviewer or "?"))
    counts = Counter(r.get("lens") for r in load_rows())
    for lens, title in sorted(lenses.items(),
                              key=lambda kv: (-counts[kv[0]], kv[0][0],
                                              int(kv[0][1:]))):
        print(f"  {lens:<5} ({counts[lens]:>3} seen)  {title}")
    print(f"\nFull text with canonical instances and tripwires: "
          f"{catalog_path().relative_to(repo_root())}")
    return 0


def cmd_add(args) -> int:
    n = append_rows([{
        "arc": args.arc, "round": args.round, "target": args.target,
        "reviewer": args.reviewer, "severity": args.severity,
        "lens": args.lens, "verdict": args.verdict,
        "fix_site": args.fix_site, "summary": args.summary,
    }], strict_lens=not args.allow_new_lens)
    print(f"recorded {n} finding -> {ledger_path()}")
    return 0


def cmd_import(args) -> int:
    raw = json.loads(Path(args.path).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise SystemExit("import expects a JSON list of objects")
    n = append_rows(raw, strict_lens=not args.allow_new_lens)
    print(f"recorded {n} finding(s) -> {ledger_path()}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="record one finding")
    a.add_argument("--arc", required=True, help="e.g. go-port, treasure-map")
    a.add_argument("--round", type=int)
    a.add_argument("--target", required=True, help="package/file/subsystem")
    a.add_argument("--reviewer", help="model or tier that produced it")
    a.add_argument("--severity", choices=SEVERITIES)
    a.add_argument("--lens", help="lens id from docs/REVIEW_PATTERNS.md")
    a.add_argument("--verdict", required=True, choices=VERDICTS)
    a.add_argument("--fix-site", dest="fix_site", choices=FIX_SITES)
    a.add_argument("--summary", required=True)
    a.add_argument("--allow-new-lens", action="store_true",
                   help="accept a lens id not yet in the catalog (warns)")
    a.set_defaults(func=cmd_add)

    i = sub.add_parser("import", help="bulk-record from a JSON list")
    i.add_argument("path")
    i.add_argument("--allow-new-lens", action="store_true")
    i.set_defaults(func=cmd_import)

    r = sub.add_parser("report", help="what recurs, and what survives verification")
    r.add_argument("--arc")
    r.add_argument("--by", default="all",
                   choices=("all", "lens", "reviewer", "fix_site", "round"))
    r.set_defaults(func=cmd_report)

    l = sub.add_parser("lenses", help="catalog ids with recorded counts")
    l.set_defaults(func=cmd_lenses)

    pr = sub.add_parser("prompt",
                        help="the block to paste into a review subagent: "
                             "output contract + lens catalog, hottest first")
    pr.add_argument("--arc", default="go-port")
    pr.add_argument("--round", type=int)
    pr.add_argument("--reviewer")
    pr.set_defaults(func=cmd_prompt)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
