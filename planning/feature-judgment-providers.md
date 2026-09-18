---
status: living
---

# Feature — judgment providers (Jev, local PCD, and the existing judge on one seam)

Opened 2026-09-17 overnight on branch `jev` (worktree `maro-wt-jev`, off
`successor`). Jeremy's ask: investigate what TypeSafe's Jev can do for
Maro's taste/judgement per the plan, and keep a local drop-in for anyone
without Jev access. This doc is the design and the decree record; the
build log carries the step entries.

## What Jev is, for this engine

A System One model: one request carries a `state` (string, object or
array) and a map of typed questions — Choice (one of N, full distribution +
confidence), Score (ordered levels, expected value + distribution + confidence),
Noul (probability of yes). No generated text, no reasoning trace. Questions
in one request are evaluated independently. Latency, cost and quality measurements are not recorded here (see
guardrails); they live under the workspace root.
Pricing is per input token, output free. (Numbers about Jev's quality or
agreement are NOT recorded in this repo — see guardrails.)

## Decrees (Jeremy, 2026-09-17)

1. **Three providers, always.** Whatever decision the seam takes over
   (step judge, closure judge, intake clarity, routing…), the *existing*
   code is refactored to sit behind the same provider interface. So every
   judged decision has `llm` (the incumbent generative judge), `jev`, and
   a no-Jev alternative. The incumbent is the grounded baseline; the cost
   is that shadow work is harder to double up.
   **Amended ~02:30:** the alternative is a *cheap hosted model*
   (`hosted`: gemini-flash-lite via the OpenAI-compatible endpoint, groq
   by config — the 2026-07-16 hosted-free decision, made on the same
   14-case corpus), not the local PCD sidecar. Jeremy's M1 sessions
   concluded the 1.5B PCD approach does not work; their write-up joins
   the existing local-LLM notes. The sidecar stays as an optional,
   experimental `pcd` provider measured by the same replay. A classifier
   trained on Maro's own labelled outcomes is a direction to revisit if
   the data grows.
2. **Budget:** up to $5 of Jev testing in various capacities before
   asking again (plan is $5/month plus purchased tokens).
3. **Landing:** push branch `jev`; no merge into `successor` until
   discussed. Jeremy wants test runs once the systems are in place.
4. **Ordering** left to the session: judge/verdict seam first, then the
   clarification gate, then planner-side taste with Jeremy.

## Contract guardrails (from Jeremy's legal read of the TypeSafe MCA)

- **No published Jev numbers.** Agreement, latency, cost and accuracy
  results involving Jev live under the workspace root (`judgment report`
  output) and in private notes — never in docs, README, fixtures or
  committed benchmark output.
- **No distillation.** Jev answers are never used as labels or tuning
  material for the local backend or any other model. The local sidecar
  is calibrated against Maro's own outcomes and paid-judge verdicts only.
  Ordinary application use (judge, route, classify, score plans, record
  that a workflow succeeded) is intended use.

## Design

- **Package `go/internal/judgment`** — the typed Request/Response in
  TypeSafe's exact wire shape. Providers are `invoke.Backend`s (design
  §4: "others attach through the same seam"): the Request JSON is the
  prompt bytes, the Response JSON the result. Nothing new in the
  invocation state machine, receipts, usage, or fold parity.
  - `HTTP` backend: base URL + model + optional bearer key from the
    secrets store. Serves `jev` (api.typesafe.ai) and `pcd` (the sidecar
    URL) with one implementation.
  - `LLM` backend adapter: renders the Request as a versioned prose
    prompt to the incumbent judge backend and parses one strict JSON
    object back into the Response shape (answers + probabilities +
    confidence + why/falsifiers). This is the existing judge, moved
    behind the seam.
- **Primary + shadow.** The configured primary provider produces the
  verdict records the resolver folds, exactly as today (default `llm`,
  so behaviour is preserved). Shadow providers answer the same Request
  and land as `shadow_judgment/1` records the resolver never sees — a
  shadow answer cannot change an effective verdict by construction.
  Shadow default is empty (no silent spend); ON per workspace config.
- **Report + replay.** `maro-go judgment report` pairs primary verdicts
  with shadow answers (agreement, disagreements, confidence buckets,
  latency). `maro-go judgment replay --corpus tests/fixtures/validation_cases.json`
  runs the retired local-validator corpus (14 labelled step-validation
  cases) through any set of providers — the pre-registered revival
  methodology from `docs/LOCAL_VALIDATOR.md`, now with a backend that
  cannot emit an invalid verdict and whose confidence comes from a
  distribution rather than from the model writing down a number.

## The local sidecar (`tools/pcd-sidecar/`) — experimental, not the drop-in

A stdlib HTTP server speaking the same wire contract, scoring candidates
from a small open model's logits (prefill once, batch every candidate's
full token sequence, PMI-correct against a state-free prompt, softmax).
Built from scratch with the M1 sessions' findings on the upstream
HF engine as the defect list to avoid: first-token ranking with a 0.75
confidence clamp on collisions, a silent first-choice fallback, and the
raw-likelihood short-string bias. Home is the thinkcentre (CPU); the M1
and the incoming M6 are optional faster homes. Its softmax is a score, not
demonstrated calibration — the replay and shadow report are how it earns
(or fails to earn) a rung.

## 2026-09-17, later: evidence, the ladder, and the fallback (branch `jev-tiers`)

The dev-Mac evaluation of Jev (private; see the workspace root) reduced to
three engine changes. Recorded here before the code, so the fold rules are
decided rather than discovered.

**1. The judge sees the evidence, not only the claim.** `StepJudgeRequest` and
`ClosureJudgeRequest` gain an `evidence` section: a deterministic digest of
the step's recorded effects — per tool effect, in ordinal order: the op, its
class, `refused`/`announced`, whether the result was an error, and a bounded
tail of the result output — plus the execute terminal. It is derived only
from committed records (`tool_effect`, `tool_effect_result`, the receipt), so
the driver and the fold build byte-identical bytes through one function.
`judgment.evidence.max_bytes` bounds it (registered, with a why). A step
whose execute produced no effects says so explicitly ("no recorded
effects") — an absent section is never silently equal to an empty one.

**2. A confidence ladder, recorded in the attempt.** The resolver already
demotes a success verdict below `Promote` (0.5) to unknown. The ladder adds
one registered bar: `judgment.escalate` (default 0.6 — the retired Python
rung's `min_certainty`, the same corpus). A primary answer whose confidence
is under it is UNDECIDED for this judgment: the driver asks the fallback
provider the same question and the fallback's answer is the verdict of
record. Both bars live in the attempt's `ConfigSnapshot` so the fold checks
what was in force, not a live default.

**3. Primary failure escalates; nothing proceeds unjudged by default.** When
the primary's call ends `failed` (unreachable, 429/529, timeout, no key,
incapable) the driver asks `judgment.fallback` (default `llm`, the incumbent)
instead of committing a verdict-less step. Only if the fallback also fails
does the step stay `unjudged`, and that is emitted with its reason. Never
fail-open: an unjudged step still gates its dependents (`gatedBy` treats it
as not done).

**Fold rules (the part that makes this a seam, not a patch).** A judge
verdict's invocation must have been asked through the attempt's primary OR
its recorded fallback — read from the invocation's `Backend.Name`, never
guessed. The fold renders and parses under the provider that answered. A
fallback-answered verdict is admissible only when the record shows why: a
primary invocation for the same judgment (same request address under the
primary's rendering) ended `failed`, or answered with confidence below the
recorded `judgment.escalate`. Anything else is a fold error.

**Not changed.** Shadow semantics; `Promote`/`Refute`; the LLM arm's prose
template; PCD stays experimental (`PCD_PMI` default moves to off — on the
evaluation's benign documents PMI was the most over-asserting rule, and the
replay is how it earns it back).

**As landed (`jev-tiers`, same day).** Names, and the two places the code
went past the text above:

- Purpose `judge_fallback` (`invoke.PurposeJudgeFallback`) marks the
  fallback's call; `judge` stays the primary's. Stages `judge_escalated`
  (with the reason) and `judge_fallback_failed`. Flags `--judge-fallback`
  (`judgment.fallback`, `llm`) and `--judge-escalate` (`judgment.escalate`,
  0.6), both lanes; `ConfigSnapshot.JudgmentFallback` /
  `JudgmentFallbackBackend` / `JudgmentEscalate` (omitempty).
- The ladder is recorded — and in force — only when the primary is a wire
  provider. The llm arm has no lower rung, so an attempt on the default arm
  records byte-for-byte what it did before this branch.
- A third escalation trigger, not in the text above: a primary answer the
  boundary *refuses* (malformed, probabilities off, a choice outside the
  vocabulary) is undecided too, and the fold admits the fallback's verdict for
  it. Without this a wire provider that answers garbage would leave the step
  unjudged while a working fallback sat idle.
- On a resumed attempt the primary's landed call is reused as before; the
  fallback's is asked again. One reuse path is enough to keep exact, and the
  fallback is the cheap arm.
- Evidence under fold parity: `StepJudgeRequest`/`ClosureJudgeRequest` take
  the digest as a string; `judgment-llm/2` is the prompt version that names it.

## Where judgment goes next (not built tonight)

- Intake clarity: both 2026-09-16 dispatches of the PCD goal stalled on
  "clarification needed"; a Noul "actionable without asking?" plus a
  Choice on ask-type, confidence-gated, is the first live consumer.
- NOW/AGENDA lane and family classification (today regex shapes in
  `run/family.go`).
- Planner taste: score candidate decompositions rather than generate
  them; the PCD run's 11-dimension schema
  (`~/.maro/workspace/projects/saw-this-in-discord-and/`) is the input,
  reduced to crisp discriminative questions.

## Open design: the judge delivers data, and the engine cannot revise its question (2026-09-18)

**Status: NOT DECIDED.** This section records a design hole and the angles
found so far. It is deliberately not a plan — Jeremy's framing (2026-09-18)
is that it "needs more vetting/angles", and two people at the end of a merge
is not enough vetting for a change this shaped.

### The premise

Jeremy, on what the step judge is for: *"let's not get caught in the trap
that the validator should drive a decision; it's delivering data for
something else to make a decision on what's next"* — the planner when
something needs rethinking, the orchestrator when a call is needed. And the
answer need not be binary: a step's result is *"a reveal of new map data —
new opportunity (build a bridge), new discovery (there's a tower here), dead
end (an empty field)"*. The naive output is yes/no; the valuable output is
the "okay, what now" metadata alongside it.

### Finding 1: the decision layer exists, and step judgment bypasses it

`internal/verdict` is already the "judge contributes, something else
decides" architecture: `Candidates{Verdicts, Observations}` -> `Commit` ->
a `Resolution` carrying the rule that decided it, with standing ranks
(self < judge < deterministic < operator), per-standing direction (a
self-claim `may_demote` only), thresholds each carrying a registered why,
and `could_not_observe` held distinct from `refuted`. Versioned
`resolver/1`; every resolution says which version decided it.

`verdict.Commit` is called **once in the engine** — at closure
(`run/driver.go`). A step binds the judge's answer straight to the engine's
action:

    sd.Verdict, sd.Outcome = v.ID, StepOutcome(v.Outcome)

So at the level that matters most, the validator *is* the decider. Routing
step judgment through the resolver is the structural fix, and it reuses
machinery already proven at closure. Until that happens, a richer judge
vocabulary only makes a bigger switch statement in a worse place.

### Finding 2: the wire already carries more than one question

`judgment.Request.Questions` is `map[string]Question` plus an `Order`;
`Ask1` is a convenience commented as "the shape every judge in this engine
makes". N questions is one call and one round-trip, with a wire provider
returning a distribution per question. Asking more is a caller change, not
a seam change.

### Finding 3: the engine can revise its answer, but not its question

What adaptation exists today:

| channel | trigger | granularity | initiated by |
|---|---|---|---|
| mid-run ask | executor writes `$MARO_ASK`, grounded by `run/ground.go` | pauses the run, asks the operator | the executor, about itself |
| `question_bounce/1` | the grounding gate refuses the ask | **the step re-runs, problems carried into its context** | the engine |
| step `blocked` | judge | kills the attempt | judge |
| new attempt | attempt failed | re-plans from the top | engine |
| closure | end of plan | resolver + observations | judge |

Three things follow.

**The only fine-grained adaptive channel runs on the lowest-standing
signal.** The executor volunteering that it is stuck is `StandingSelf` —
which this engine's own doctrine says may only demote. The judge, which
holds the evidence and outranks it, has exactly one mid-run verb: kill the
attempt. The grounding gate (2026-09-17, audit §4.4) invests real machinery
in validating an executor-initiated question, which sharpens rather than
softens the asymmetry: nothing at all validates, or even solicits, a
*judge*-initiated "the plan's premise has shifted".

**The plan language cannot express a branch.** A plan is an ordered list
with `after:` prerequisite edges. There is no way to say "step 4 depends on
what step 2 finds", so organic shift must come through replanning — and
replanning only happens by discarding an attempt. `planPrompt` has no slot
for a prior attempt; whatever reaches a re-plan about why the last one died
arrives through the recall block or not at all.

**Rework is all-or-nothing, which is probably why nothing reworks.**
Discovering at step 2 that the plan is wrong leaves two options: kill the
attempt (discard the work) or ride out steps 3..n knowing they are wrong.
The cost of acting on the discovery is high enough that not acting is the
default.

### The test case: learning a language to draw a kanji

Jeremy's standing example. The goal asks for a kanji; carrying out step 2
reveals the real task is learning the language. Today:

- the executor volunteers it -> ask, grounded, operator decides. The good
  path, and it exists — but only if the executor chooses to self-report;
- the executor quietly draws a mediocre kanji -> the judge is asked "is this
  step done?", and the step *was* done as asked -> `done` -> the remaining
  steps run on a plan everyone would now reject, and **the discovery is
  never recorded, because nothing ever asks for it**;
- the executor fails -> `blocked` -> the attempt dies -> re-plan, possibly
  into the same plan.

The silent middle case is the common one and the expensive one.

There is one encouraging precedent: `question_bounce/1` already re-runs a
step *within* an attempt with new context attached. The primitive Jeremy
wants — revise and retry a step mid-plan, carrying why — is built, for
exactly one narrow trigger. Generalising it is a smaller move than
inventing it.

### A first cut at the question set (for critique, not for building)

Each question must name a consumer; a question with no consumer is
decoration, the same discipline the defaults registry applies to flags.

| question | vocabulary | consumer |
|---|---|---|
| completion | done / partial / not done / cannot tell | resolver -> step outcome |
| grounding | corroborated / contradicted / unverifiable | resolver (evidence, not claim) |
| obstacle | missing prerequisite / capability limit / environment / ambiguous ask / false premise | routes planner vs operator |
| reframe | none / narrower / broader / different problem | **planner — no consumer today** |
| residue | nothing / new prerequisite / new opportunity / dead end | **planner — no consumer today** |
| scope | as asked / did less / did more | orchestrator (over-reach) |

### What needs vetting before any of this is built

1. **Record shape.** The resolver's inputs are typed: `Observation` has a
   closed `CheckKind` vocabulary and `Verdict` a closed outcome set per
   kind. A six-answer judgment does not drop into that as-is. Which answers
   become observations, which become verdicts, and which need a new record
   kind is the real design question underneath this one.
2. **Consumers first.** `reframe: different problem` with no re-planner
   attached is a label, not a capability. The order of work is arguably:
   route step judgment through the resolver; give the planner a mid-attempt
   revision channel (generalise the bounce); *then* enrich the vocabulary.
3. **Intent runs once, at the front.** The engine asks "do I understand the
   goal?" at the moment it knows least, and never again. The kanji case is
   an intent revision arriving late, and there is no record kind for one.
   Whether that belongs to the judge, the planner, or a new stage is open.
4. **Fork members bury it deepest.** A parallel member that finds the
   reframe is folded into a member list and judged on "were the sub-goals
   answered" — two layers from anything that could act on it.
5. **Cost and failure direction.** Every new question is more surface for a
   judge to be confidently wrong on. The measured behaviour so far (private:
   `~/.maro/workspace/judgment/`, numbers stay out of this repo by contract)
   is that evidence in the state moves errors toward the safe direction, but
   that was measured on a pass/fail vocabulary, not this one.
6. **No corpus exists for this question.** The 14-case fixture is
   claim-only pass/fail and its labels reward trusting an unverifiable
   claim. A vocabulary built around "what happened, therefore what?" would
   need a corpus labelled from records. That is the measurement that would
   settle any of this.
