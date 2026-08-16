---
status: living
---

# Mutation specs

Must-detect mutation lists, one JSON file per surface, run by
`scripts/mutate.py`. The runner's docstring is the format reference; this
file is the convention and the coverage ledger.

```bash
python3 scripts/mutate.py tests/mutation/scope_14a.json
python3 scripts/mutate.py tests/mutation/scope_14a.json --only quarantine
```

These are **not** collected by pytest and do not run in CI — a sweep is
minutes of targeted test runs and the value is in the reading behind the
list, not in re-running it on every push. Run one when you change the
surface it covers, and when you add coverage to a new surface.

## Why the spec file is the artifact

Derive the mutation list from **reading the file, not from your own
diff** (HOUSE_STYLE step 3; Jeremy 2026-08-16). A diff-derived list only
tests whether your fixes are pinned. A file-derived list tests whether
the behavior is — and in the arc that produced this directory, five
adversarial review rounds and two diff-derived harnesses walked past
twelve real gaps, the worst being a decree guarded by two tests that
could not fail.

Committing the spec answers "what did we actually probe?" months later.
Before this, each round wrote a throwaway harness in a scratchpad and
the answer was unrecoverable by the next session.

## Conventions

- **One anchor, one match.** The runner refuses an anchor matching 0 or
  2+ times, because a mis-applied mutation silently reads as a pass.
- **A survivor is a hole in the suite**, not a mutation to delete.
  Strengthen the test.
- **Mark genuinely equivalent mutants `equivalent`** with the reason
  they cannot fail. They are still applied and run; a *detected*
  equivalent fails the run, because it means the stated reason is wrong.
  Contorting a test to kill an equivalent mutant is how a suite starts
  testing its own mocks.
- **Name the surface, not the file.** Coverage below is per *surface* —
  a spec covering the scope paths in `knowledge_web.py` says nothing
  about the other 85% of that module. Do not read "swept" off a
  filename.

## Coverage ledger

| Spec | Surface | Mutations | Swept |
|---|---|---|---|
| `scope_14a.json` | §14a scope/stamp/portability: `knowledge_web` scope screens, tiered-store load/rewrite/mutate, quarantine, reinforce heal, `_tfidf_rank_scored`; `camera_readout` census, `_lesson_origins`, `_stamp_coverage`, `_scope_rollup`, `_print_portability`; `pack` lesson transport border | 34 + 1 equivalent | 2026-08-16 |

Everything else in `src/` (178 modules) has never had a file-derived
sweep. The ordered plan for covering it is the top item in the BACKLOG
Actionable Stack: tests that claim to enforce a decree first (silent by
construction), then data-integrity boundaries, then operator-facing
output, then churn.
