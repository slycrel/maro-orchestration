# Compatibility as a standard, applied to this engine (2026-09-18)

Jeremy handed over three documents from his work side on 2026-08-28 — an
overview of forwards compatibility, the concrete service-contract standard
behind it, and a working draft on how such a standard gets enforced in
tests. They sat on the box unread by this work for three weeks. This file
is the mining result: the ideas in my own words, and what each one means
for the successor engine. **The source documents are his employer's and
are not reproduced or committed anywhere in this repo.**

The reason this is worth a planning file rather than a note: the engine
already has four contract surfaces with two independent consumers each,
and it has been breaking the rules below by accident. The live regression
in `planning/reorient-mined-2026-09-18.md` (the Go shadow arm dead since
2026-09-07) is a textbook violation of the one rule the standard itself
lists as missing.

---

## The idea, compressed

Compatibility is a deal with two halves, and one half alone buys nothing.
A writer promises never to take anything away from what it has published.
A reader promises to survive things it has never seen — new fields, new
enum values, longer lists. With both halves, the two sides stop having to
ship together: a producer can deploy without knowing who consumes it, a
consumer can lag without blocking anyone, and a rollback stops being
frightening because old code can always read new data.

Four rules carry it: a contract has exactly one owner, writers only add,
readers tolerate growth, and nothing is ever removed except through a
deprecation lifecycle (mark it, measure real usage, let consumers migrate,
remove only when measured usage is zero). Two subtleties do most of the
damage in practice. First, MEANING is part of the shape — a field that
keeps its name and type but changes what it counts breaks every reader
while passing every schema check. Second, requiredness is a one-way door:
over time you may only give readers more and demand less of writers, and a
field you ever guaranteed is a promise you can never measure your way out
of, because which fields a consumer reads is not observable.

The enforcement draft adds two things that turn the standard into tests.

**One: every assertion must name the change that would turn it red.** If
you cannot state a one-token edit to production code that fails the test,
the test is decoration. The corollary is the useful part: an assertion must
be red on breaking changes and GREEN on safe ones. A pin that goes red
when someone adds a field is not enforcing compatibility, it is preventing
the thing compatibility exists to permit.

**Two: the dangerous direction depends on the element, not the edge.** Some
elements are open worlds that mean "at least these" — the fields of a
payload, the length of a list. You break them by removing or renaming.
Others are closed worlds that mean "exactly these" — the set of values a
field may take, the set of inputs a reader accepts, the width of the column
it is stored in. You break those by ADDING a value (every reader must now
handle it) or by TIGHTENING what you accept (input that was legal
yesterday is refused today). A shape diff cannot see the second kind at
all, which is why hand-written tests keep finding them and schema tooling
never does.

The generative consequence, and the part worth stealing outright: **every
real failure sits on a seam where an open-world element flows into a
closed-world one.** A tolerated value reaching a fixed-width store. An
unknown value falling into a default branch that happens to be the most
privileged reading. A grown list read by index. The rule that follows is
short: wherever something open flows into something closed, that seam is a
required assertion.

---

## The rule the standard is missing, which this engine needed

The four rules are written from the point of view of a writer publishing a
response. They say what a writer may add; they say nothing about a reader
narrowing what it accepts, because on a response payload that cannot
happen. On a REQUEST payload it is the main hazard, and the enforcement
draft proposes the missing half:

> **Readers only loosen.** A reader may widen what it accepts. It may never
> narrow it without going through the deprecation lifecycle. Rule 2 governs
> what you write; this governs what you refuse.

The Go engine violated exactly this, against its own durable records, and
it cost a live lane eleven days. `go/internal/run/fold.go` re-derives the
intent prompt over a run's goal and refuses the record if the recorded
request is not byte-identical to what today's code renders. Changing the
prompt's wording (`84a7c12a`) narrowed what the fold accepts, on records
already written. Every journal from before that commit became unfoldable,
and the shadow lane's persistent workspace — which exists precisely so the
Go engine's landscape accrues across runs — has refused every run since.

The instinct behind the check is right and should survive: re-executing the
interpretation boundary on read is what makes the record evidence rather
than testimony. What was wrong is the direction of the tightening, and the
fix is the one the standard names — the reader must accept what earlier
writers legitimately produced, which in record terms means the prompt is a
VERSIONED template and a record is folded against the version it was
written under.

---

## The four surfaces this engine has, and who owns each

| Surface | Writer | Reader(s) | State of the deal |
|---|---|---|---|
| Journal record kinds (`go/internal/run`, `invoke`, `record`) | whichever engine version wrote the record | every later version's fold, plus `runs show`, the projector, the tail | schema versions exist (`plan/2`, `step_done/2`) and the door validates them, but there is no rule that a reader may not tighten; the prompt check above is one instance and the plan-request residue is a second |
| Declared contracts under `go/contracts/` | the code, via `contracts gen` | `contracts check` in CI and any reader of a generated file | the closest thing here to an additivity gate, and the only surface with a drift check. It pins SHAPE, not direction: nothing today fails a regeneration that removes a key |
| The shared workspace formats (`~/.maro/workspace/`) | both engines | both engines | this is the two-consumer case the standard is written for. Today's discipline is byte-comparison by hand when someone remembers |
| Prompt templates | the engine version that made a call | the fold, re-deriving | the live regression. Treated as code, consumed as a contract |

Two observations fall straight out of that table.

**The record kinds are the contract that matters most, because the reader
is always a future version of ourselves.** Nothing else on the box has a
consumer that cannot be upgraded — and a durable record has exactly that
consumer, forever. The four rules land almost unchanged: add fields, never
retype or re-mean one, never make an absent field required for records that
predate it, and never tighten a check against history.

**The engine has the instrument the work-side standard wishes it had.**
That draft's biggest single lever is "open decision 3": until a machine can
diff two versions of a spec and rule on additivity, every mechanical
concern gets hand-rolled into brittle pins. This repo already generates a
machine-readable contract per record kind and already fails a build on
drift. What is missing is the DIRECTION check — a gate that says a
regenerated contract may gain keys and may not lose or retype them. That
is a small, well-scoped build on top of something that exists, and it is
the first candidate I would put in the plan from this mining.

---

## What I would take, in order

1. **Versioned prompt templates, folded by the version a record names.**
   Fixes the live regression, closes the same class queued from chunk 1
   and the landscape work, and is the concrete form of "readers only
   loosen" for this engine.
2. **An additivity gate on the generated contracts.** Compare the
   regenerated set against the committed one and fail a change that
   removes or retypes a key instead of merely reporting drift. The
   mechanical half of enforcement, bought with the machinery already here.
3. **Name the open/closed direction on each declared field.** A field that
   is an open set (the members of a list, the keys of a map) breaks on
   removal; a field that is a closed vocabulary (an outcome value, a
   terminal class, a policy) breaks on ADDITION, because every existing
   fold must already handle the new member. Today both kinds are just
   "fields". Marking them is what makes the gate in 2 able to give
   different verdicts for the two directions.
4. **A seam census.** Wherever an open-world value flows into a
   closed-world sink, assert it. The engine's own examples: a recorded
   outcome value reaching a fold's switch (what happens to an unknown
   one — refuse, or the most permissive arm?), a terminal class reaching
   the delivery decision, a policy name reaching the mechanism registry.
   The standard's own worked failure was an unknown value landing in a
   default branch that granted the most privileged reading; the engine's
   doctrine is fail-closed, which is the opposite default and should be
   proved rather than assumed.
5. **The test discipline, adopted as a rule for this repo's guards:** every
   assertion names the one-token mutation that turns it red, and no
   assertion may be red on an addition. This is the same rule Jeremy
   already gave for mutation testing ("derive must-detect mutations from
   the FILE not the diff"), arriving from a second direction, which is
   itself a reason to trust it.

**Not taken, and why.** The deprecation lifecycle's measurement step does
not transfer as written: it relies on telemetry showing a route unused,
and a durable journal's readers are unobservable by construction — every
old record may be folded again tomorrow. For records the honest rule is
stronger than the standard's: a record kind's shape, once written to a
journal that is never rewritten, is permanent. Removal is not a lifecycle,
it is a migration that rewrites history or a reader that keeps reading the
old shape forever, and this engine's doctrine ("one history per forgery")
already forbids the first.
