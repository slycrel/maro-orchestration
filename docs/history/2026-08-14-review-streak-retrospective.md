# Why every review round finds severe edges — retrospective at round 13

**Question (Jeremy, 2026-08-14):** "Let's see if we can identify why we
keep having such severe edges; I'm starting to wonder if we're just not
disciplined enough on each round of changes at this point. (We're
guessing we're right too often maybe.)"

**Short answer: yes on both counts, but the useful diagnosis is more
specific than "be more careful."** Three named causes, two of them
method, one structural. Each has a concrete countermeasure that shipped
today; none of them is "try harder."

## The record being explained

Thirteen consecutive adversarial-review rounds on this project have
returned real findings (zero reviewer hallucinations across all of
them). The truncation-audit arc alone: round 11 found 11 (consensus:
the shared verdict writer missed), round 12 found 9 including a
consensus HIGH **introduced by round 11's own fix** (retry stamp
only-when-present vs. a preserve-omitted-keys merge), round 13 found 8
deduped including two HIGHs that are the *same class one key over*
(replacement tuple missing `goal_verdict_gaps`; the stop tuple never
cleared). The suite was 8,500+ green before every one of these rounds.

## Cause 1 — we work the list, the defects live in the class

Every round we fixed an enumerated list (the BACKLOG filing, the
previous round's findings) and declared the lane done at the list's
end. The severe leftovers were, every single time, *other members of
the same equivalence class*: the same field's other writers, the same
value's downstream consumers, the exception-branch twin of a fixed
site.

The measurement that makes this undeniable: while round 13 ran, an AST
sweep for the exact defect shape (bare `[:N]` on a rationale-family
name) found **168 occurrences in src/**. Three rounds of "all findings
fixed" had touched perhaps 40. Each round was *sampling* an
undispositioned population — of course the next round found more, and
of course some were severe: severity is roughly random across the
population. Even the reviewers under-enumerate: round 12 listed four
`stop_evidence` homes, round 13 found three more, and the sweep finds
still others.

**Countermeasure (shipped):** `tests/test_truncation_discipline.py` —
the sweep as a permanent tripwire. The current population (163
occurrences / 120 sites after today's fixes) is frozen in a checked-in
inventory: any NEW bare slice on a rationale-family name fails the
suite; a fixed site whose row isn't trimmed also fails, so the debt
number stays honest in both directions and only ever ratchets down.

**Discipline rule:** before closing any round that touches a durable
field, grep by the FIELD NAME (not by the cut spelling you know about)
and disposition every writer and reader found. Minutes per field; it
would have prevented the majority of rounds 11-13's findings.

## Cause 2 — new contracts ship reasoned, not probed

Every round's worst finding was in code written *that same round*: the
retry stamp against a merge whose semantics I assumed instead of read;
the replacement API that replaces four tuple members and not the
fifth; degenerate budgets returning 4x the requested size; a forged
marker riding the idempotence path through any cap. The reviewers
caught these with five-line probe scripts — seed stale keys, pass the
schema-max value, forge the marker — that I could have run before
landing and didn't, because the suite was green and the reasoning felt
sound. That is precisely "guessing we're right too often": suite-green
plus self-review is systematically overcalibrated here, because the
author's pins encode the author's assumptions.

**Discipline rule:** every new or changed contract gets an adversarial
self-probe BEFORE landing — the schema-max input, the degenerate
input, the stale-sibling state, the forged shape. This is the LT-arc
lesson ("prove the probe can fail") applied to our own code. And for
state-mutating writers specifically: a state-transition probe through
the REAL writer (failure-with-gaps → achieved, judged → unjudged,
first-attempt → retry), asserting the exact resulting key set. All
three round-13 reviewers' process notes converged on this
independently.

## Cause 3 — caller-owned semantics regenerate defects

Everywhere a value's bounding/stamping semantics live at N call sites,
every new writer is a fresh chance to get it wrong, and review rounds
keep harvesting. Where a schema owner existed (`make_decision_prior`,
`stamp_run_verdict`), a whole class closed with one edit. The 2026-08
defects were not exotic: they were the same three semantics —
bound-honestly, replace-wholly, clear-explicitly — re-implemented
inconsistently by hand at dozens of seams.

**Countermeasure (shipped today):** `stamp_run_verdict` now replaces
the verdict tuple WHOLE (gaps included); `clear_run_verdict` /
`clear_run_stop_verdict` write absence explicitly; the main closure
writer routes through the owner instead of a raw metadata merge.
**Direction for future work:** when a round finds the same semantics
hand-rolled at three-plus seams, the fix is an owner, not three
patches (CODING_NOTES' 3-is-fine/4-wants-extraction, applied to
persistence semantics).

## What this predicts

If the diagnosis is right, round 14 (running as this is written) should
come back materially smaller — not because we got more careful, but
because the class census is now mechanical, the tuple semantics have
owners, and the highs' whole family (incomplete replacement) is closed
at one seam. If round 14 still finds highs, the diagnosis is
incomplete and the next suspect is the one this doc couldn't test:
fields whose names don't advertise their role (the sweep is name-based
by construction).

## Reviewer process notes (round 13, verbatim themes)

All three, independently: replace the exact-string blacklist with a
field-owner inventory and table-driven end-to-end probes through the
real writers; seed sibling keys with sentinels and assert the exact
resulting key set; run a field-oriented census before declaring a round
closed. The Skeptic's closing observation deserves the last word: the
lexical pins "banned `_cmerge.detail[:500]` and one spelling of
`reasoning[:500]`, but not `_cc_exc[:500]`, `(evidence + _note)[:500]`,
or `_post_closure.summary[:300]`."
