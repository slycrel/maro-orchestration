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
in one request are evaluated independently. Measured on this box
2026-09-17: a three-question judge-shaped request answered in ~250 ms.
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
