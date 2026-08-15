---
status: record
---

# What our adversarial reviews MISS — a taxonomy from ~50 rounds (2026-08-15)

Jeremy's ask: *"Let's also try and find more patterns in what we are
missing in the reviews so we can watch for those up front."*

This extends the 2026-08-14 review-streak retrospective, which answered
a different question (why do AUTHORS keep shipping severe edges). This
one asks what the REVIEWS themselves fail to see on their first pass —
defects that a later round, a different framing, or an instrument
eventually caught. Method: three parallel mining passes over every
review record we have (BACKLOG + BACKLOG_DONE round records, all
docs/history verdict docs, and ~60 commits of round-fix messages —
roughly 50 rounds, July 2026 → today, codex + sonnet + one grok).
Every pattern below carries at least three independently-recorded
instances; none is inferred from a single anecdote.

The headline, first: reviewer *precision* is not the problem.
Hallucination has been ~0 for fifteen-plus consecutive rounds (against
a 30–78% historical band), and the sonnet-medium lane held that (1/20
across its first three rounds). The misses are all *recall* failures,
and they cluster into seven shapes.

## The seven miss patterns

### 1. The fix layer ships unreviewed — and it is where the next HIGHs live

The single dominant pattern, present in EVERY multi-round arc:
- stretch r2: "five of the seven confirmed findings were defects in
  the round-1 fix layer itself"; stretch r4: all five in r3's fixes.
- 2026-08-13 fixpoint round: all five findings in the whole-changeset
  round's own fix commit — "every one was a fix that traded correctness
  for a new defect."
- truncation r12→r17: every round's worst finding was in the previous
  round's fix (only-when-present stamp, gaps key, non-atomic trio,
  fourth caller, swallowed writer failure, second producer branch).
- The scrub-"1" regression: a review-FIX introduced a live production
  break hours after landing.

**Why reviews miss it:** the round reviews the diff it was pointed at;
the fixes for its findings land after it returns. The next round is
the first pair of eyes on them — so severity migrates into fix layers
by construction.

**Watch up front:** never close an arc on an unreviewed fix layer (the
fixpoint discipline — rounds until quiet — exists for this; convergence
signatures on record: 12→9→8→5→2, 9→5→4→0, 10→7→3, 6→4→0). And point
the reviewer AT the fix commit explicitly: "the previous round's fixes
are part of your scope, and historically the likeliest home of this
round's HIGH."

### 2. Sibling-lane blindness — reviewers verify the diff's sites, not the class

The author enumerates N sites; the reviewer verifies those N; members
N+1..M keep the defect:
- stop_evidence: round 12 listed four homes, round 13 found three
  more, the AST sweep found still others.
- The contested-marker fix landed on the main closure lane; r17 found
  the escalation lane re-consulting it as in-memory routing — "the
  exact 2026-08-09 pattern reintroduced in the sibling lane."
- The reinforce-path lock fix (2026-08-04) never reached
  promote_lesson (2026-08-08 TOCTOU HIGH): "the fix landed on the
  reinforce path only."
- Killswitch string-normalization: decreed 2026-07-21 (chunk-5a F1),
  found un-propagated 2026-08-09 (s5-cuts F6) and twice on 2026-08-13.
- audit_policy.error: bounded on one of two producer branches (r17).

**Why reviews miss it:** the reviewer inherits the author's
enumeration. Both under-enumerate; grep-by-known-spelling fails
exactly like the author's lexical pins did.

**Watch up front:** before accepting "fixed", census the FIELD/CLASS —
every writer, every reader, every branch twin (exception path, retry
path, escalation lane, parallel lane, the CLI twin) — by AST/field
name, not by the spellings in the diff. Ask: "which siblings of the
touched sites share this data shape and did NOT change?"

### 3. Composition blindness — units verified apart, never the production path

- Today's on-mode warning: seam guard correct alone, walk correct
  alone; `build_adapter` always wraps, so the composition silently
  no-opped — "the tests assert against the seam guard and the walk in
  isolation but never against the two composed the way production
  actually composes them."
- The whole-changeset pass's marginal value was cross-chunk seams:
  the receipts auditor (reviewed to fixpoint) vs the failed-attempt
  recorder (a different chunk) — "five separate per-chunk fixpoint
  reviews structurally could not" see it.
- Token brake: the no-retry policy verified at the adapter seam,
  wrongly generalized — the loop layer retried anyway.
- `python -m handle` module identity: registrar and drainer held two
  different dicts; only a module-identity probe saw it.

**Why reviews miss it:** per-chunk framing scopes the reviewer to one
side of every seam, and unit tests stand in for the other side.

**Watch up front:** trace ONE literal production call path end-to-end
through the changed code (the real entry point, the real wrapper
stack, the real module identity) and demand at least one test on that
literal path. Schedule periodic whole-changeset/seam passes; they have
paid every time.

### 4. The tests are part of the attack surface — and mostly unreviewed

The recurring shapes, each recorded verbatim in some round:
- asserted the object, not the flow (token-brake merge gate);
- captured rc and never asserted it (maro-export inspect);
- pinned the bug as intended behavior (chunk-3 F6, skill-candidate
  consumption);
- monkeypatched a raise production never produces (escalation payload);
- hid the race behind a 30s sleep (token brake);
- seeded exactly one row, making filter-before-limit and
  filter-after-limit indistinguishable (Δ-demotion);
- hand-built the metadata the code was supposed to produce (§13e);
- guard-test duplicated the detector instead of running it (map lens);
- tested the hook, not the seam that must invoke it (fork-seed);
- no log-capture assertion on a visibility guarantee (today).

**Why reviews miss it:** reviewers attack the code and read the tests
as evidence. The author's tests encode the author's assumptions — the
retrospective said this about self-review; it is equally true of what
tests prove TO a reviewer.

**Watch up front:** review the new tests as adversarially as the code:
"name a defect these tests still pass on." Authors: must-detect
verification (stash the fix, watch the test fail) is now standing
practice — it caught nothing-detecting tests twice today alone.

### 5. Claimed-but-unwired — prose guarantees with no executing line

- Today's SF-6 warning: docstrings and commit message described a
  throttled walk warning that did not exist anywhere in code.
- a0bae77's own repair path: "the exact announced-but-structurally-dead
  class a0bae77 exists to fix, alive on its repair path."
- "every outcome shape is stamped" — false as landed (recon flavor,
  five early returns unstamped).
- notified_early recorded regardless of delivery; the consumer side of
  the auth-breaker notification had no rendering branch — "the write
  side shipped without its read side."
- The pre-flight plan reviewer: silently dead for months, 488/488
  calibration entries empty, discovered by census not review.

**Why reviews miss it:** comments and commit messages read as
descriptions of the code; a reviewer verifying "is this line correct?"
never asks "does the promised line exist at all?"

**Watch up front:** for every guarantee stated in the diff's comments,
docstrings, or commit message, locate the executing line — or flag the
claim. Treat visibility/notification guarantees (warnings, events,
markers) as testable behavior needing a capture assertion, not prose.

### 6. Failure-direction inversions — defaults, coercions, and degenerate inputs

- `gaps=None` default silently CLEARED real gaps at two unmigrated
  callers — "a destructive default let incomplete migrations pass
  silently."
- Quoted `"false"`/`""` killswitches arriving truthy (three separate
  recurrences of one decreed rule).
- NaN passing isinstance and both band comparisons; `bool(parsed["valid"])`
  promoting on the string "false"; `loop_status=""` coerced to "stuck".
- Fail-open on exception in lane selection — "the exception fallback
  falls back to the HOST prompt: the dangerous direction"; the verbs
  variant silently flipping a previously-safe under-advertising
  direction.

**Why reviews miss it:** these only show under degenerate inputs the
reviewer must think to inject; the happy path and the suite are green.

**Watch up front:** a standing degenerate probe list per new contract:
schema-max value, empty/None, NaN, string-typed booleans, forged
marker/shape, and "which way does this branch err when its input is
garbage — and is that the safe direction for THIS module's doctrine?"

### 7. Instrument blindness — our censuses and pins fail like our code

- The lexical caller grep missed the aliased fourth caller "exactly
  like the lexical pin table did."
- The census silently skipped a file with a transient syntax error and
  mislabeled its sites "new."
- Chained `.strip()[:N]` evaded the round-13 scanner; scrub-wrapped
  cuts were certified out of the census; dict-literal-only scanning
  missed the two biggest real bypasses (variable-built extras) until
  today's var-flow upgrade — which immediately surfaced a site the
  frozen inventory never listed.

**Why reviews miss it:** a green tripwire reads as "class closed"; the
tripwire's own recall is rarely questioned.

**Watch up front:** every scanner/census/pin gets must-detect fixtures
(shapes designed to evade it) and a negative control; "instrument
found 0" is untrusted until the fixtures prove it CAN find. Scanner
upgrades are expected to GROW inventories — that's surfaced debt, not
regression.

## Meta-findings about reviewer judgment (keep these calibrations)

- **A finding is a lead, not a fact** — verify-before-fix stays
  mandatory even at 0% recent hallucination (historical band 30–78%).
- **Consensus is not evidence when premises are unmeasured**: two
  independent reviewers both rated the fork-session-state finding HIGH
  from the same unmeasured premise; measurement refuted both.
- **Disagreement is signal**: the reanchor-r2 lens contradiction is
  what exposed the Architect's wrong shown-work.
- **Impact-overstatement is a distinct failure mode from
  fabrication** (recon flavor F2/F5) — verify the impact claim
  separately from the code claim.
- **Deferral premises expire**: R6's "adjudication is gated OFF"
  deferrals reactivated the day the gate flipped. Record the premise
  with the deferral and re-check it when config changes.

## What shipped from this analysis

1. This record.
2. A distilled reviewer watch-list added to `docs/DEV_PATTERNS.md`
   (the judgement-half pre-read both boxes load before reviewing) —
   seven one-line probes matching the patterns above, to be injected
   into reviewer prompts up front.
3. The Mac adversarial-review skill's prompt template now carries the
   watch-list instruction (box's skill copy: mirror when convenient —
   the DEV_PATTERNS copy is the shared source of truth).

The falsifier, matching the retrospective's practice: if the taxonomy
is right, rounds that inject the watch-list should shift findings
earlier (round-1 catches of fix-layer/sibling/composition shapes) and
shrink the arc length to fixpoint. If arcs stay long and HIGHs keep
landing in round-2+ fix layers despite the watch-list, the taxonomy is
naming symptoms, not causes.
