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
