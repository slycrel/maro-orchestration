---
status: living
---

# DEV_PATTERNS — taste and judgement for development sessions

*Phase 0.5 of the swarm-review arc (Jeremy, 2026-07-21). ALPHA — being
validated by a with-doc/control battery before graduating to a standing
pre-read. Dev-facing only: this lives in docs/ and not skills/ because
`skill_loader.py` globs repo `skills/` into runtime prompts; these patterns
govern how we build maro, not how maro runs.*

The two halves are the delegation-boundary razor applied to ourselves:
**taste** is determining the plan/task/"what" we attempt; **judgement** is
validating that we accomplished what we set out to do. (Jeremy, 2026-07-21.)
Every pattern below was paid for at least twice in the repo's history — the
era citations are the receipts (`docs/KNOWLEDGE_JOURNEY.md`).

## Taste — up-front commitments (apply while shaping the work)

1. **Cuts-first.** Constraints-with-basis before steps: what are we NOT
   doing, and on what evidence? A plan that opens with steps has skipped
   the decision. *(Qix-cuts decree, era 10 · deterministic-home: none yet)*
2. **Consumer-first.** Every store, emitter, or signal names its acting
   consumer in the same plan — no write side without its read side landing
   together. The repo's most-repeated wound: six half-closed loops in six
   eras. *(era 10; V2/V5 shipped this way · deterministic-home: the
   DEFAULTS half landed chunk 8 (`test_every_documented_key_has_a_reader`);
   the stores/guards census half is still BACKLOG'd behind a registration
   convention — this check leaves when THAT lands, not before.
   Tag re-pointed 2026-07-28, thread census.)*
3. **Decree-with-tripwire.** Every principle adopted names the test that
   could fail it, ideally landing first and failing. A principle without a
   tripwire waits to be rediscovered. *(the meta-finding, era 12 ·
   deterministic-home: none yet)*
4. **Done-means.** Name the executed check that will verify "done" BEFORE
   building. If the check can't be named, the scope isn't understood yet.
   *(through-line #1, seven eras deep · deterministic-home: closure verdicts
   cover runtime; dev-side home: none yet)*
5. **Scope/inversion.** Ask what would make this the wrong thing to build,
   before building it. *(era 04 whiteboard; REASSESS practical-use ·
   deterministic-home: none yet)*
6. **Possible-now bias.** Before deferring work to future model capability,
   name the composition of current capabilities that was tried or ruled
   out. "Needs a better model" is a claim requiring evidence, not a
   default. *(Jeremy 2026-07-21: "what scares me most is decisions NOT to
   do the work when the work is possible" · deterministic-home: none yet)*

## Judgement — audit checks (apply while reviewing work, yours or an agent's)

1. **Live writer?** For every store the work reads or creates: who writes
   it, today, on the live path — and who consumes what it writes? Cite the
   caller. *(eras 03/05/06/07/09 · deterministic-home: wiring census,
   chunk 8)*
2. **Executed check behind "done"?** A completion claim counts only with
   the check's actual output attached. Narration of verification is not
   verification. *(eras 00/04/05/08 · deterministic-home: closure verdicts
   at runtime; dev-side: none yet)*
3. **Reviewer claim verified?** Any finding you're about to act on gets its
   code claim re-verified against the tree first — 30-78% of unverified
   reviewer claims were wrong across five independent measurements.
   *(through-line #3 · deterministic-home: claim_probe covers runtime
   contestations; dev-side: none yet)*
4. **Inconclusive scored as inconclusive?** Never silently promoted to
   pass, never counted as fail without saying so. *(era 09 fails-toward-
   doubt · deterministic-home: unjudged tri-state at runtime; dev-side:
   none yet)*
5. **Citation direction checked?** The cited source must actually assert
   the claim's direction — citation inversion is a named failure class and
   this session produced three instances itself. *(05-12 taxonomy; era 12 ·
   deterministic-home: none yet)*
6. **The delegation-boundary razor.** For any orchestration mechanism:
   does it serve parent-taste (choosing what) or parent-judgement
   (validating outcomes)? If neither, why does the parent own it? Applied
   to the 03-31 factory benchmark this correctly sorts load-bearing
   (adversarial review, verify loop, output criteria — all judgement) from
   removable (persona-as-routing, multi-plan ceremony — neither).
   *(Jeremy 2026-07-21 · deterministic-home: none yet)*
7. **Tri-state boolean check.** Wherever a value is read then compared or
   sorted: is ABSENT distinguished from EMPTY distinguished from a real
   value? Coercing missing to a default ("" that sorts first, 0 that
   counts, [] that passes) silently promotes absence to evidence.
   Recurring instances: missing `started_at` lexically sorting a tagged
   loop to the cohort front (2026-08-01 recon review), recall citations
   absent-vs-zero, closure verdicts unjudged-vs-contested. *(Jeremy
   2026-08-01, "missing != empty, and we're checking for a value" ·
   deterministic-home: none yet)*

## Reviewer watch-list (inject into adversarial-review prompts up front)

Seven recall probes, one per miss-pattern our own ~50 review rounds kept
paying for — evidence + instances in
`docs/history/2026-08-15-review-miss-taxonomy.md`. Reviewer precision has
been fine (0-hallucination streak); these target what first passes DON'T
see. Give reviewers these questions verbatim:

1. **Fix layer:** the previous round's fixes are in your scope and are
   historically the likeliest home of this round's HIGH — attack them
   first.
2. **Siblings:** for every field/value the diff touches, census ALL its
   writers, readers, and branch twins (exception path, retry path,
   escalation lane, parallel lane, CLI twin) — which members of the class
   did NOT change, and should have?
3. **Composition:** trace one LITERAL production call path end-to-end
   (real entry point, real wrapper stack, real module identity). Do the
   units compose the way the docstrings claim? Is any test on that
   literal path?
4. **Tests:** name a defect the new tests still pass on. Look for:
   asserted-the-object-not-the-flow, captured-but-not-asserted, pins
   encoding the bug, mocks proving the wrong path, single-row seeds that
   can't distinguish orderings, guarantees with no capture assertion.
5. **Claims:** for every guarantee in the comments/docstrings/commit
   message, find the executing line — or flag the claim as unwired.
6. **Degenerates + direction:** probe schema-max, empty/None, NaN,
   string-typed booleans ("false", ""), forged markers — and for every
   error/fallback branch ask which way it errs, and whether that's the
   safe direction for THIS module's stated doctrine.
7. **Instruments:** if the diff adds/extends a scanner, census, or pin,
   demand must-detect fixtures (shapes built to evade it) and a negative
   control — "found 0" is untrusted until the fixtures prove it can find.

Calibrations that ride along: a finding is a lead, not a fact
(verify-before-fix); consensus from a shared unmeasured premise is not
evidence — run the settling probe; lens disagreement is signal, chase it;
verify impact claims separately from code claims; record every deferral's
premise and re-check it when config flips.

## Typed finding codes (the shared vocabulary for review findings)

When a review (dev session, adversarial pass, evidence-path lens) writes
a finding that fits a known error class, stamp it `FINDING[CODE]` on the
finding's first line. Codes and their definitions live in
`src/finding_codes.py` (seeded from the 2026-05-12 taxonomy:
CITATION_INVERSION, PHANTOM_SYMBOL, THEORY_MECHANISM, GAP_UNDERSTATED).
Stamps make error classes greppable/countable — the chunk-7 discretion
readout tabulates them instead of LLM-classifying prose. Extend the
vocabulary in the module (zero-overlap rule in its docstring); a finding
that fits no code goes honestly unstamped.

## Graduation rule (the tripwire on this document itself)

Each check carries a `deterministic-home:` tag. When a check's home ships —
a test, census assertion, or gate that catches the violation mechanically —
the check LEAVES this document, enforced by a chunk-8 census assertion that
fails if a tagged home exists while its check is still listed here. This
document is scaffolding by design: its success is shrinking. Its two prior
instantiations (the playbook, Stage-5 compiled rules) half-died for lack of
exactly this rule.

## Status

- Battery RUN + ADJUDICATED 2026-07-21: **ambiguous — no measurable delta**
  (both arms caught all pointed ground truths at ceiling; the plan-shape
  measure was invalidated by a schema flaw). Per the pre-registered gate
  this ships as a **non-gated pre-read** (CLAUDE.md line), on cost≈zero
  grounds, not benchmark evidence. Full report + raw outputs:
  `docs/history/2026-07-21-phase05-battery.md`.
- Runtime injection of this content stays a chunk-2 decision on chunk-2
  evidence.
