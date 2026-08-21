---
status: record
---

# Taste and the germ line — session wrap conversation, 2026-08-21

Distillation, not transcript. The engineering of this session lives in
`2026-08-20-async-tail-process-spawn.md`; this records the DIRECTION
conversation from the wrap, kept because two of its articulations are new to
the repo and carry testable consequences. Jeremy's own caveat applies and is
part of the record: this came out of a Claude/Jeremy exchange — "a mirror and
an echo chamber" — so the claims below are stated in their falsifiable forms
on purpose.

## The narrow claim about what maro is (falsifiable form)

Verification is everywhere — eval harnesses, post-hoc checkers, guardrails.
The claim is narrower: **the closed loop where a verdict's TRUSTWORTHINESS
gates what enters persistent memory** (closure judges → trust tiers → only
trusted verdicts may teach → contamination quarantine → decay) is rare to
the point that no shipped system with it is known to either of us. The
falsification is a survey: find a production system where a low-confidence
verdict blocks skill crystallization. Worth actually running someday;
informative either way.

**Why the niche is structurally invisible** (the part that makes the rarity
plausible rather than flattering): labs don't need a verdict layer because
benchmarks carry their own ground truth — the eval harness IS the closure
layer, so the problem never surfaces internally. Products don't build
verdict-gated learning because products mostly aren't ALLOWED to learn —
persistent learning from users is a liability. The problem is only forced
into the open for a single-operator autonomous system running open-world
tasks with permission to learn: maro's exact configuration. Corollary that
resolves "good enough for who it's for": for most current LLM use a human
reads the output, so the human IS the closure layer and verification
genuinely doesn't matter — in exact proportion to how much a human stays in
the loop. Maro hit the problem early because AFK autonomy was the premise
from day one.

## Taste = memory × a trusted judge (Jeremy's frame, sharpened)

Jeremy: the self-learning/trust loop is "the foundation of (probably a slice
of) the beginnings of taste... we didn't start with an implementation in
mind, we sort of grew it." The technical restatement: memory keeps what
happened; **taste keeps what worked as judged by something you have decided
to trust.** Without the trust gate, learning is accretion. Honest scope: it
is task-completion taste — design taste (is this the right SHAPE) is still
mostly imported from Jeremy; the house-style intentionality loop and
discretion readout are early reaches toward it.

**Germ line vs eval loops:** ralph-style loops have perfect ground truth
within an instance and total amnesia across instances — selection WITHIN a
task. Eval harnesses select MODELS. Maro's loop selects LESSONS — heredity
ACROSS tasks — and heredity requires a germ line: a protected channel where
only some experience becomes inheritance. The verdict-trust gate is the germ
line boundary. Bad heredity, where everything propagates, is kudzu by
definition.

## Kudzu and the mortality term (the culling doctrine's frame)

Jeremy's growth worry, biological answer: kudzu isn't uniquely vigorous — in
Japan it has predators. Its invasive range lacks a MORTALITY TERM. Maro's
asymmetry, stated plainly: **birth is automatic everywhere (every run
mints); death is automatic in exactly one store** (MEDIUM-tier lesson
decay/GC). Everything else — backlog entries, skills, knowledge nodes,
docs — is append-forever plus occasional manual archive. The subtraction
audit (BACKLOG, Vision/Deferred) is the designed predator; the tree line
arrives when deletion is as unremarkable as creation. **The kudzu gauge:**
dev-status's net-new-open-items per 10 commits (+0.75 at this writing). The
day it goes negative for a sustained stretch without anyone forcing it, the
predators took.

## The ecosystem position (why "behind the harness" is a category error)

Maro rides the Claude CLI; it does not compete with it. Where maro
duplicates harness mechanics, the platform wins and will keep winning —
that is the churn zone and the subtraction audit's hunting ground; every
platform release that absorbs a maro mechanism is free labor (delete the
local copy, keep the doctrine). Where maro is additive is where the
platform structurally won't go: the trust doctrine, the operator's
accumulated judged experience, the instrumentation. Jeremy's stance,
recorded because it should steer future build-vs-wait calls: **maro is long
all three inputs** — model capability, harness quality, and its own usage.
Most agent companies are SHORT model capability (their moat is patching
model weaknesses; releases erode it). Maro's moat appreciates regardless of
who wins the capability race: one operator, full instrumentation, years of
decisions with receipts. "We're consistent, and that's not nothin'" — it is
most of it.

## Adjudications made in the same conversation

- `tail.spawn` flipped to default-ON (burn-in evidence; see the async-tail
  record).
- Knowledge-graph direction: **(a) — use the edges** (traversal into
  retrieval), A/B-gated, measure first.
- "Done means done" prompt-rule: **declined** — done≠achieved already
  encodes the sharper version; a prompt-rule restatement is what you write
  when you don't have closure machinery.
- Four link-farm steal candidates promoted to BACKLOG ("not next, but worth
  a visit before later").
