---
status: living
---

# Δ-gate build brief — effect-based promotion for minted lessons

**Priority decided (Jeremy, 2026-08-06, on session wrap): "Sure, prep
the gate for the next run, which will be after I clear the session."**
Δ-gate goes ahead of SP/world-facts; the next session builds it. This
brief is the prep — surfaces scouted, sequencing set, falsifiers named
— so that session starts at the wire, not at re-discovery. Full arc
context: BACKLOG "LeAct acceptance filter" entry (two Opus contrasts,
prerequisite census, 2026-08-03 trigger record).

## 1. The wire (one, not an arc)

Add an **effect-based route to LONG** beside the tenure route — today
promotion is `score >= PROMOTE_MIN_SCORE (0.9) AND sessions_validated
>= PROMOTE_MIN_SESSIONS (3)` (`knowledge_web.py:72-73`, eligibility
check ~:598, `promote_lesson` :1078, auto-promotion via
`_post_reinforce_hooks`). Tenure selects for "agrees with the corpus"
(a lesson must be independently re-derived 3×) — which actively
excludes blind-spot lessons, the ones a Δ measure values most. The new
route: a lesson whose measured Δ (injection improves replay outcome on
its own run family) clears a threshold promotes on effect, tenure
unmet. **Consumer-first: the Δ verdict gates tiering; it is not an
annotation.** The tenure route stays live beside it — slice 1 compares
routes, it does not replace one.

## 2. Measurement loop (all ingredients live, census 2026-08-03)

- **Replay corpus**: per-call records in `<run>/build/calls/*.json`
  ({seq, backend, model, prompt, response, tool_events, …}) — 116 run
  dirs, including the LT-1 held-out arms themselves (`01e55212` 20
  calls, `2738d9c0` 45, `d9607baa` 30). Fixture source and
  pre-registered held-out set are the same runs.
- **Reward without logprobs**: LeAct App D.2.1 action-match over M
  samples — replay a decision-bearing call with/without the lesson
  injected, score agreement with the known-good action. Hosted-free
  rung makes M replays ~$0 (cheap-tier fallback fine; the seed-reader
  A/B ran 28 calls for cents).
- **With/without harness precedents**: `compact_ab.run_ab_step/
  run_ab_test` (action-match-style, one skill), `lens_ablation.py`
  (costume vs evidence arms), and — freshest, with the working
  hygiene — the seed-reader A/B harness at
  `~/.maro/workspace/output/seed-reader-ab/harness.py` (frozen-state
  snapshot, per-cell raw JSON, no-store-writes boundary, deterministic
  metrics + jackknife). Steal its shape.
- **Verdict denominator**: `PYTHONPATH=src python3 -m verdict_flow` —
  67 judged rows, arrivals two weeks running (W31 18, W32 9).
- **Corpus hygiene, both cleared 2026-08-06**: the S2 style exemplar
  is removed (A/B verdict — mild type-anchoring + homogenization,
  zero quality gain), and new mints carry mint-grounding receipts
  (`mint_grounding.py`), so fabricated-provenance rows are marked in
  the corpus Δ will be measured over.

## 3. Sequencing for the build session

1. **Instrument first, standalone**: build the replay-Δ measurer and
   validate it on the LT-1 arms against pre-registered expectations
   (which lessons SHOULD move which decisions — write the predictions
   before running, LT discipline). No promotion wiring yet.
2. **Stratify rule-vs-reason before reading results**: lessons that
   ARE their own action ("run tests before claiming done") have Δ≈0
   definitionally (LeAct §6 self-referential collapse). Unstratified
   aggregate Δ is uninterpretable; discovering the corpus split is
   part of the experiment.
3. **Wire the route**: effect-based LONG promotion behind the
   validated instrument (killswitch-gated config default, registered
   in docs/DEFAULTS.md per the defaults decree).
4. **Routes census readout**: which rows each route promotes, overlap,
   and what tenure alone would have missed — the readout IS the
   evidence for Jeremy's keep/adjust call.

## 4. Falsifiers (named up front, design-review corollary)

- If the instrument can't separate known-effective from known-inert
  lessons on the LT-1 arms (pre-registered), it's noise — do NOT wire
  it to promotion; stop and say so.
- If Δ-gated and tenure promotion select ~the same rows on the first
  real corpus, the gate added mechanism without changing selection —
  the blind-spot thesis is refuted for this corpus; record and pause.
- If most lessons land in the rule stratum (Δ≈0 by construction), the
  corpus can't support effect-based promotion yet — the finding is
  about the corpus, not the gate.

## 5. Cuts (slice 1)

Lessons only — no evolver/thinkback trace scoring yet (the original
LeAct item's wider target; it queues behind a working lesson gate).
No competence-redundancy decay (follow-on steal, needs this same
harness; Jeremy's 55c877da: calendar decay was "mostly placeholder").
No un-contest/refight verb. Tenure route untouched. No retroactive
re-scoring of the existing LONG tier.
