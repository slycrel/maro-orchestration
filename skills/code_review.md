---
name: code_review
description: "Review a git diff and return file:line findings split into confirmed (with reasoning or a reproduction) vs speculative"
roles_allowed: [worker]
triggers: [code review, review diff, review changes, review pull request, find bugs, diff review]
---

# Code Review

## When to use

Use this skill when a goal hands you a git diff (or a PR / set of changes) and asks
whether the change is correct. It produces an evidence-graded finding list, not a
gut-feel opinion. The core discipline: **a finding is only "confirmed" if you can
show reasoning that forces the conclusion or, better, an actual reproduction.**
Everything else is "speculative" and must be labeled as such. Never launder a hunch
into a confirmed bug.

Do not use this for writing new code (use code_implement) or for diagnosing an
already-failing system with no diff in hand (use debug_investigate).

## Workflow

1. **Read the diff, not the whole repo.** Parse each hunk. For every changed file,
   record the new-file line numbers so every finding can cite `path:line`. Ignore
   unchanged context except where it's needed to judge a changed line.
2. **Reconstruct changed units.** For each edited function/block, mentally (or by
   extracting the post-change file) rebuild what the code now does after the diff
   is applied — not what it did before.
3. **Scan for defect classes.** Walk each changed line against a checklist:
   off-by-one / bounds, null-or-empty inputs (division by zero, empty collections),
   resource leaks, mutable default args and shared state, incorrect operators
   (`and`/`or`, `=`/`==`, `<`/`<=`), error handling, and changed control flow.
4. **Draft candidate findings.** For each suspicion write `path:line` + one sentence
   on the suspected failure. Do not grade yet.
5. **Attempt to prove each candidate.** This is the gate that separates the two
   buckets. For each candidate, try in order:
   (a) a concrete reproduction — extract the changed code and run an input that
   triggers the failure, capturing the output; or
   (b) a forcing argument — a specific input/state and the exact line-by-line
   consequence that must occur. If neither succeeds, the finding stays speculative.
6. **Attack your own candidates (red-herring pass).** For each candidate, spend one
   honest attempt to show it is NOT a bug (intended behavior, a caller guarantees
   the precondition, the value is correct despite looking wrong). If the refutation
   holds, drop it from confirmed — at most it is speculative with the caveat noted.
7. **Grade and split.** A candidate becomes **confirmed** only if step 5 produced a
   reproduction or a forcing argument AND step 6 failed to refute it. All others go
   to **speculative** with the reason they couldn't be proven.
8. **Emit the report** in the Output format below. Confirmed first, then speculative.

## Output format

```
## Confirmed (N)
- path:line — <one-line defect> [BUG CLASS]
  Evidence: <reproduction command + observed output> OR <forcing argument: input → consequence>

## Speculative (M)
- path:line — <one-line concern> [BUG CLASS]
  Why not confirmed: <what refuted it or what evidence is missing to promote it>
```

Rules:
- Every finding MUST carry a `path:line` reference tied to a changed line.
- The Confirmed section contains ONLY findings backed by a reproduction or a forcing
  argument. If you are unsure, it goes in Speculative — err toward Speculative.
- Never restate a speculative finding as confirmed in a summary. If there are zero
  confirmed findings, say so plainly.

## Failure modes

- **Grading a hunch as confirmed.** The most damaging failure. If you can't produce
  a reproduction or a forcing argument, it is speculative — full stop.
- **Flagging the red herring.** Code that looks wrong but is correct (e.g. `* 0.9`
  for a 10% discount, `range(len(x))` iteration, `== None` that works). Step 6 exists
  to catch these; always run one refutation attempt before confirming.
- **Reviewing the pre-change code.** Judge the state after the diff is applied.
- **Findings with no line reference.** Unciteable findings are unactionable; drop or
  fix them.
- **Reviewing beyond the diff.** Only findings on changed lines (or directly caused by
  them) are in scope; note unrelated issues at most as a one-line aside.

---

*Provenance: built by Maro itself (dogfood run 0baac0ab-witty-spruce,
2026-07-09) as 1.0 item (f); verified against a planted-bug fixture
(3/3 real bugs confirmed with reproductions, 1 red herring correctly
refuted, reproductions re-run by hand). Closure verdict was
goal_achieved=false @0.35 — hand-overridden after artifact
verification (the "missing" fixture.diff exists; wrong-cwd verifier
class). Reviewed and graduated by hand. Satisfies the 2026-07-09
adversarial-review ship-skill decree (Purgatorio hist-06).*
