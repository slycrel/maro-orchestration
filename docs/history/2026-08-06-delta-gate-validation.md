---
status: record
---

# Δ-gate instrument validation — pre-registered run, 2026-08-06 (overnight)

The replay-Δ instrument (`src/delta_replay.py`, landed 4879ef2) was
validated against pre-registered predictions before any promotion wiring
existed, per DELTA_GATE_BUILD_BRIEF §3.1 and falsifier 1 (§4). Predictions
were frozen in `~/.maro/workspace/output/delta-gate-v1/predictions.md`
BEFORE the first replay; raw per-call results in `.../raw/`, summary in
`.../results.json`.

## Protocol

- **Fixture**: all 18 oracle `navigator decision` calls across the 25 runs
  judged `goal_achieved=True` (143 runs scanned). Recorded actions: 15×
  extend, 2× execute, 1× close. The navigator lesson is present in every
  recorded prompt, so all measurements ran the ablation direction.
- **Specimens**: E = the judge-graduated navigator extend-lesson (core
  sentence as recorded — the 3× phrasing, not the store's current 4×);
  I = medium-tier d8da88dd (HathiTrust/API-blocking observation, irrelevant
  to navigation); R = long-tier 5a3081e1 (token_explosion distill
  prescription, rule stratum).
- **Replay**: gemini flash-lite via the hosted-free ladder, 3 samples per
  arm per call = 324 replays, $0. Zero replay errors in the accepted run.

## Results

| specimen | stratum | Δ | jackknife | prediction | verdict |
|---|---|---|---|---|---|
| E (effective) | reason | **+0.593** | 0.094 | Δ > 0, ≥ +0.15 | **P1 ✓** |
| I (inert) | reason | −0.056 | 0.062 | \|Δ\| ≤ 0.10 | **P2 ✓** |
| R (rule) | rule | **−0.148** | 0.068 | \|Δ\| ≤ 0.10 | **P3 ✗** |

**Separation criterion (the falsifier): VALIDATES.** Δ_E > 0; E−I = 0.65
and E−R = 0.74, both ≥ 0.15. No jackknife dominance (spread ≪ Δ on all
three). Promotion wiring is therefore sanctioned.

**The P3 miss is a real finding, not noise**: the rule-stratum specimen
was predicted inert-by-construction (LeAct §6) and instead measured
mildly NEGATIVE — injecting an off-topic prescription into a decision
prompt actively pushed replays away from the recorded known-good action.
Two readings, both recorded: (a) blind injection has a measurable cost,
which strengthens the Δ-gate thesis (injection surface real estate is not
free); (b) this was not the §6 self-referential-collapse test — that
requires measuring a rule lesson on ITS OWN action family, which this
corpus's decision purposes don't expose yet. The wired route excludes
rule-stratum lessons from effect promotion outright.

## Operational lessons (three dead runs before the clean one)

1. Unpaced hosted-free bursts trip the rate breakers ~14 calls in; every
   subsequent replay fails instantly and the error-count readout is the
   only tell (run 1: 94–108 errors/specimen in 18.8s — worthless).
2. Groq free tier (llama-3.1-8b-instant) is 6K TPM; ~2.5K-token prompts
   mean 2 calls/min sustainable — the wrong rung for batch replay. Gemini
   flash-lite (250K TPM, RPM-bound) at 6.5s pacing ran 324/324 clean.
3. `hosted_free`'s latency guard trips a provider for the REST OF THE
   PROCESS on one slow call — correct for run-time validation, fatal for
   a batch harness (run 3 died at replay 64 on one ReadTimeout). The
   harness disables the cap and resets breaker state before retries.

## Known limitations (named in predictions.md, unchanged)

- Replay model (gemini flash-lite) ≠ recorded model (haiku); Δ transfers
  to production only to the extent the decision surface is model-stable.
- One known-effective specimen exists in today's corpus — separation is
  one data point, not a distribution.
- M=3 sampling at provider-default temperature; error bars optimistic if
  the provider is near-deterministic.

## What was wired on this verdict

`knowledge_web.promote_lesson_by_effect` (effect route to LONG beside the
untouched tenure route; guards: killswitch
`knowledge.effect_promotion_enabled`, Δ ≥ 0.30, ≥ 6 oracle calls,
jackknife < Δ, reason stratum only, same provisional/quarantined/contested
boundary as tenure) + `python3 -m delta_replay` census/promote CLI.
Measurement is deliberate CLI spend — nothing ambient replays. Registered
in docs/DEFAULTS.md.

## Routes census — first corpus measurement (2026-08-07)

Ran on subprocess haiku (Jeremy cleared paid-cheap for the day; replay
model now MATCHES the recorded model family, dropping the model-transfer
limitation above). Top 3 MEDIUM lessons by score, 50 oracle decision
calls each, samples=1, 300 replays, **zero errors, zero skipped**.
Result: `~/.maro/workspace/output/delta-gate-v1/census.json`.

| lesson | score (corpus rank) | stratum | Δ | jackknife |
|---|---|---|---|---|
| 3036c141 cross-referencing external claims | 1.27 (#1) | reason | **−0.10** | 0.022 |
| 46613838 re-fetch fresh for adversarial verify | 1.25 (#2) | reason | **−0.08** | 0.022 |
| f67f59ca reddit top-5 needs roundup threads | 1.08 (#3) | reason | **−0.10** | 0.022 |

**Nothing promotes** (all Δ < 0.30 floor) — the gate held. The finding
is the direction: the corpus's three HIGHEST-scored lessons all measure
as mild decision-distractors, the same direction as the validation's
rule specimen (−0.148), with spreads tight enough (0.022) that the sign
is not noise. At samples=1 a Δ of −0.10 means 5 net decisions across 50
flipped AWAY from the known-good action when the lesson was present.

Reading: reinforcement scoring rewards narrative resonance (these are
run-diary observations — well-written, true, and decision-irrelevant),
while Δ measures decision utility. The two promotion routes now
disagree in an informative way on the corpus's front-runners. None were
tenure-eligible yet (sessions_validated=0 across the board), so tenure
has promoted nothing wrongly — but score rank is tenure's precursor,
and score rank is exactly what Δ contradicts here.

Census caveats: 3 of 208 candidates measured (limit=3 driver;
census.json records candidates_not_measured=205), samples=1 per arm so
per-call scoring is 0/1, and all three lessons sit in a narrow score
band. The keep/adjust call on the routes — and whether negative-Δ
retirement (brief §5) graduates from cut to slice 2 — is Jeremy's read.

## Routes census round 2 — retest + stratified sweep (2026-08-08)

Jeremy's call on round 1 (GOAL_BRAIN Decisions 2026-08-08): negative Δ
for demotion is the right direction, **build it if more testing gets us
agreeing data**; haiku/sonnet-low replay spend cleared standing. Round 2
ran the test: re-measure the three round-1 negatives on the FULL oracle
set (51 calls, samples=3), plus a 9-lesson stratified sweep across the
reason-stratum score range and 2 rule-stratum specimens (30 calls,
samples=3 each). Sharded 3-way on subprocess haiku (~2h wall); 966
replays, **zero errors**. Result:
`~/.maro/workspace/output/delta-gate-v1/census_round2_merged.json`.

**Retest — the agreeing-data condition, MET (3/3 same sign, stable):**

| lesson | round 1 Δ | round 2 Δ | jackknife |
|---|---|---|---|
| 3036c141 cross-referencing external claims | −0.10 | **−0.137** | 0.024 |
| 46613838 re-fetch fresh for adversarial verify | −0.08 | **−0.059** | 0.020 |
| f67f59ca reddit top-5 needs roundup threads | −0.10 | **−0.078** | 0.024 |

**Stratified sweep — Δ is non-monotonic in score:** ranks 2/27/50
measured exactly 0.000 (inert); ranks 118–186 mildly negative
(−0.03..−0.10); and two mid-ranking lessons measured the corpus's first
POSITIVE Δs — rank 72 (61e4cbd7, "query library catalogs for
bibliographic questions") **+0.200** jk 0.041 and rank 95 (32a656a5,
"gather output schema and collision-check constraints before the main
write step") **+0.167** jk 0.029. Both are method-shaped (procedural
how-to), unlike the narrative top-scorers. Neither clears the 0.30
promote floor — flagged to Jeremy as the first live adjustable-prior
question (his round-1 words: "open to adjust some of our calculation
variables when the data leads us there").

**Rule stratum: MIXED** (e29b9ae6 −0.067, 6044bf32 +0.067) — the
validation's rule-negative doesn't generalize to a stratum-wide claim.
Rules stay excluded from the effect surface by construction (LeAct §6),
not by measured sign.

**Instrument honesty note:** 0/51 oracle decision prompts carry a
"## Tiered Lessons" block in production — every measurement here is
SYNTHETIC injection (the with-arm inserts the block in production
format). Valid for gating what WOULD be injected, which is exactly what
promotion/demotion control; it says nothing about lessons' effects on
surfaces the instrument doesn't replay.

**Acted on (same day):** `knowledge_web.demote_lesson_by_effect` +
`--demote` on the census CLI — measured-negative MEDIUM lessons (Δ ≤
−0.05, jackknife-dominant, ≥6 calls, reason stratum) get a
`route="effect-demote"` stamp: excluded from `inject_tiered_lessons`,
blocked from the tenure route to LONG. Surface-scoped per the decree —
no score mutation, no deletion, flat ledger and `query_lessons`
untouched ("other contexts might end up promoting on the same data").
A later measurement clearing the promote bar replaces the stamp.
Killswitch `knowledge.effect_demotion_enabled` (docs/DEFAULTS.md). The
three retested negatives are eligible; stamping them is a one-line
CLI run (`--demote --lesson-id ...`), left for after Jeremy's read.
