---
status: record
---

# Factory-mode adjudication — Phase 49's decision gate, finally fired

**Date:** 2026-07-21 (swarm-review chunk 1, item b). **Adjudicator:**
Claude session under Jeremy's standing grant; verdict below is Jeremy's,
recorded verbatim from his recall during the knowledge journey.

## What was open

Phase 47 (2026-03-31) ran the "Bitter Lesson" experiment: can
behavior-description prompts replace the Mode 2 orchestration stack?
Result PARTIAL (`docs/history/2026-03-31-factory-mode-findings.md`) —
high variance, no rubric, FACTORY_STEP without output criteria exploded
4.4× in tokens vs the criteria-bearing Mode 2 prompt. Phase 49 defined
the decision gate — "merge factory as mode:thin in handle.py, or discard
the factory branch entirely" — and never ran (shelved behind Phase 46;
both Purgatorio audits flagged the branch as arch-10 "work finished,
pushed, lost from the record").

## Jeremy's verdict (recalled 2026-07-21, never previously persisted)

> Mixed bag — not much different than a general LLM prompt.

That is the Phase 47 conclusion, three months later, with usage data to
back it: **zero organic mode:thin runs** in outcomes.jsonl (0 of 1431
rows), 456-line factory_thin.py riding at HEAD untouched by any run.

## Dispositions

1. **origin/factory branch → ARCHIVED.** Tagged
   `archive/factory-2026-03-31` (head `1476f97`, factory_full_sim v1-v4
   + state gitignore), tag pushed, remote branch deleted. Every commit
   remains reachable via the tag; the learnings were already merged as
   the Phase 47 findings doc + the Phase 0.5 lineage
   (output-criteria finding → DEV_PATTERNS "done-means" commitment).
   This closes the arch-10 flag: the record now says where the work went.
2. **`mode:thin` + factory_thin.py at HEAD → KEEP** as an operator-only
   escape hatch and benchmark instrument (it is the "merge as mode:thin"
   half of the Phase 49 gate, which had in fact already happened). It is
   reachable only via explicit prefix — no auto-routing — and is the
   natural harness for the star-pattern melt-test rerun
   (`.claude/skills/star/SKILL.md`). Default tier bumped cheap → MID per
   the 2026-07-20 execution-floor decree (Phase 49's own item 3 — "a
   Sonnet factory run isolates prompt design from model capability" —
   argued the same direction).
3. **factory_minimal.py → KEEP** as the single-completion benchmark
   baseline (CLI-only, zero runtime callers; `docs/success-criteria.md`
   cites its $0.04-0.06/60s baseline numbers).

## Why not delete thin/minimal too?

The verdict is "mixed bag," not "worse" — and both files are the
cheapest decorrelated baseline we own: when a future arc asks "is the
orchestration earning its overhead?", the answer requires exactly these
harnesses. They carry zero live-path risk (prefix-gated / CLI-only).
Deleting them would re-create the instrument the next time the Bitter
Lesson question recurs — and the knowledge journey shows it recurs
(Phase 47, the 04-26 "1-shot first" note, REASSESS lineage §6, the
star-pattern skill).
