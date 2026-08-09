---
status: living
---

# World-Fact Plan Items — Design Sketch

Status: **ALL THREE SLICES SHIPPED** — slice 1 2026-08-08 (capture +
ledger + injection, anecdotal-only render; hypothesis kind accepted at
capture and quarantined per §7.1 — `src/world_facts.py`, completion-tool
`world_facts` channel in step_exec, `LoopContext.world_facts`, checkpoint
carry + resume restore, `world_facts.enabled` default ON); slices 2+3
2026-08-09. §7 decisions taken by Jeremy 2026-08-06: quarantine mirrors
the provenance pattern; planner `FACT:` emission stays slice 3; cap
sizes are build-time tuning.

**Slice 2 (finalize landing, 2026-08-09):** `world_facts.land_facts`,
called from `_build_result_and_finalize` before `_finalize_loop` (so the
bridge's own extraction pass dedups against fact-minted nodes).
Anecdotal → `upsert_knowledge_from_candidate` CANDIDATE nodes (born
invisible; the V3 promotion sweep IS the generalizability filter — the
§4 teaching mint stays dormant-design, so the bridge is the landing per
§2's census). Hypothesis → `observe_pattern` with
`world_fact:<loop_id>` provenance. Verdict-independent by design (a
failed run's "archive X is blocked" is still a fact). Caps 5/3 per run,
strongest (distinct-step hits) first. Idempotent per run dir via a
`world_facts_landed_keys` metadata list — keyed on the FACTS, not the
loop id, because a demoted-then-resumed run re-enters finalize under a
FRESH loop_id with the same restored ledger (the original loop-id stamp
guarded nothing real — 2026-08-09 review); per-key recording also lets
transiently-failed writes retry while landed hypotheses never
self-confirm. Landing is an allowlist (`source == "step"`) — unknown
future producer labels are preserved but not landable. One
`WORLD_FACTS_LANDED` captain's-log event.
**Watch item:** the confirmation bar is the lane's existing
RULE_PROMOTE_CONFIRMATIONS=2 — two runs declaring the same pattern
guess promotes a StandingRule. Pinned deliberately
(`test_cross_run_redeclaration_confirms_toward_rule`); the
contradiction check at promotion + refight lane are the backstops. If
live runs promote junk rules, raise the bar for world_fact-sourced
hypotheses first.

**Slice 3 (planner emission, 2026-08-09):** decompose teaches
`WORLD_FACT_RULES` behind `planner.world_facts` in the assembled-prompt
lanes only — the staged-pass lane is deliberately untaught (2026-08-09
review, 3-lens consensus: it replaces the assembled prompt and never
receives the injected-context extras, so a staged FACT could only be a
model prior wearing a grounding claim; detection still strips any it
emits). Emission-only gate; `parse_steps` detection unconditional —
FACT entries never execute, never count against max_steps. Facts seed
the ledger as anecdotal with sticky `source="planner"`: they render as
known but `land_facts` refuses them — planner facts come FROM injected
context, and landing them back would launder stored knowledge into
fresh confidence bumps; a step restating a rendered fact saw it as
"treat as known" first, so provenance never upgrades (a lexical
near-restatement merges into the planner row via a difflib ≥0.85 guard;
a true semantic paraphrase still slips it — named residual). Named
residuals: loop_execute re-decompose lanes (boundary/milestone/replan)
don't collect facts_out — FACT lines there are plucked and dropped
(safe, just unledgered); multi-plan collects facts from ALL sampled
candidates including rejected plans (DECIDED: facts claim grounding in
the shared injected context, not in the winning plan — the injection
scan and never-lands guard bound the damage); factory_thin's local
parser doesn't strip FACT lines (DECIDED: adjudicated separate
instrument, never taught the protocol).

Review hardening 2026-08-08: declarations pass the injection_guard scan
(fail-closed), the kill switch gates render as well as capture (and now
landing + planner teaching), and checkpoint restore drops corrupt rows
per-row. Named scope limit: the checkpoint carry is the sequential
lane's — parallel fan-out/DAG paths have no checkpoint/resume
machinery, so their facts die with the run.
Origin: §4c decision batch, 2026-08-02. Related: RUN_TEACHINGS_DESIGN
§4c/§5b (terrain), docs/DEFAULTS.md rows `world_facts.enabled` +
`planner.world_facts`.

## 1. The ask (verbatim)

> "we need to add non-action types to the planner. I think 2 types of
> world facts: anecdotal/accidentally found and hypothesis type
> findings (pattern recognition/ideas)."

Plans today are action-only. A run that stumbles onto a fact ("archive
X is blocked", "the dataset has a second sheet") or forms a pattern
hypothesis ("these failures cluster by transport") has no plan-native
place to put it — it leaks into step prose or dies with the step.

## 2. What already exists (census, 2026-08-03)

The design below is mostly WIRING — every load-bearing piece has a
shipped precedent:

| Piece | Shipped precedent |
|---|---|
| Executor declares typed non-action output | `decisions` directive on the completion tool (step_exec ~1505: validated `{decision, rationale}` dicts, capped 2, journaled) |
| Run-scoped fact ledger, rendered into later steps | `terrain.py` (§5b slice 0): deterministic accumulator on LoopContext, ONE `terrain` contribution per step via the §6 ledger seam |
| Plan items carrying metadata through resume/splits | inline step tags `[after:N]` / `[boundary]` / `[recon: …]` — the string IS the carrier |
| Durable landing for anecdotal facts | knowledge web candidate nodes + the V3 promotion path (born invisible, earn active by re-observation) |
| Durable landing for hypotheses | `hypotheses.jsonl` + `observe_pattern()` (confirmation counting, promotes to StandingRule, contradiction flow) |
| "MAY emit, never must" prompting | guidance-form decree ("usually, do this" = priors not requirements) |

## 3. Proposed shape — three small pieces

**(a) Capture: a `world_facts` side-channel on the completion tool**,
mirroring the `decisions` directive exactly. Validated entries
`{kind: "anecdotal"|"hypothesis", fact, evidence}`, capped (2–3/step),
malformed entries don't consume the cap. The worker prompt gets a
guidance-form line: fact-shaped findings that aren't the step's
deliverable MAY be declared here. Planner-side: `_decompose` MAY emit
`FACT:`-prefixed lines alongside steps (things it already believes
about the terrain from injected context) — these seed the ledger, they
are never executable steps and never count against max_steps.

**(b) A run-scoped fact ledger on LoopContext** — the terrain
accumulator generalized one notch, NOT a new store. Anecdotal facts and
terrain blocks render as one merged "known this run" contribution into
subsequent steps (same seam, same drop/re-render discipline).
Hypothesis-kind facts are ledgered but NOT injected into steps by
default — an unconfirmed pattern guess injected as fact is exactly the
M18/M19 self-referential-hedging failure §4c routed AROUND. The ledger
rides the manifest so facts survive resume/replan (the plan-native part
of the ask: a replan sees the facts, not just the surviving steps).

**(c) Landing at finalize, split by kind** — no new lifecycle:
- **anecdotal** → evidence source for the §4 terrain/teaching mint;
  generalizable ones ride the existing bridge (candidate node → V3
  earned promotion). Facts with a mechanical check get a
  `verify_probe` per the ratified probe-gated-injection decision.
- **hypothesis** → `observe_pattern()` lane: minted as Hypothesis,
  earns StandingRule status only through repeated confirmation, dies
  quietly if never confirmed. Pattern-guesses get confirmation
  machinery, not belief.

No solely time-based expiration anywhere (standing constraint) — both
lanes are evidence-driven already.

## 4. Falsifiers (what would prove this wrong)

- **Cap-starvation**: if real runs regularly produce >3 legitimate
  facts/step, the per-step cap is wrong — visible as truncation in the
  ledger vs step prose.
- **Noise flood**: if >~half of declared anecdotal facts are restated
  step results (not accidental finds), the worker prompt guidance is
  miscalibrated — measurable by sampling ledgers from a week of runs.
- **Hypothesis lane dead**: if no declared hypothesis is ever
  confirmed by `observe_pattern` within ~30 runs, the kind split adds
  schema without signal — collapse to one kind.
- **Injection regression**: if the merged known-this-run block pushes
  step prompts past the §12 context discipline or steps start citing
  facts instead of doing work, the render cap is wrong.

## 5. Minimum slice + order

1. **Slice 1 — capture + ledger + injection (anecdotal only):**
   completion-tool channel, LoopContext ledger merged with terrain's
   render, manifest persistence. Deterministic, testable without live
   runs. Hypothesis kind ACCEPTED at capture but only ledgered
   (forward-compatible schema, no consumer yet).
2. **Slice 2 — landing:** finalize routes anecdotal facts into the
   teaching/bridge mint with provenance; hypothesis kind into
   `observe_pattern`.
3. **Slice 3 — planner emission:** `FACT:` lines from decompose seed
   the ledger. Last because it's the only piece needing new planner
   prompt surface, and (a)+(b) already deliver most of the value.

## 6. Cuts (named, per cuts-first)

- No new JSONL store, no daemon, no cadence (standing invariants).
- No LLM classification of kind — the declarer says which kind it is.
- No hypothesis injection into live runs (confirmation first).
- No cross-run fact sharing beyond the existing teaching/knowledge
  lanes (portable-learning scope rules apply unchanged).
- Not the §4 terrain-teaching build itself — this composes with it.

## 7. Decisions for Jeremy

1. **Hypothesis quarantine at injection** — slice 1 keeps hypothesis
   facts OUT of step prompts until confirmed. Right call, or should a
   run see its own hypotheses marked as guesses?
2. **Planner `FACT:` emission as slice 3** (last), since capture +
   ledger deliver the value — or do you want the planner half first,
   since the ask was phrased at the planner?
3. **Cap sizes** (2–3 facts/step, render cap on the known-this-run
   block) — taken as build-time tuning unless you care.
