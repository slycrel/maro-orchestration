---
status: living
---

# Reading Queue — docs awaiting Jeremy

Rows here render to the **Reading** tab on the viz server
(`https://maro.feifdom.com/reading.html`). Each row links to the
GitHub-rendered copy on `main` — nothing is self-hosted (Jeremy
2026-07-28: "better just as a link to the github-pushed artifact so we
don't reinvent the wheel"). A row is only useful once its doc has landed
— land first, then queue.

**The page refreshes when this file lands** (`scripts/land.sh`, from the
landed blob) and whenever the runs index is written. Before 2026-08-03 it
was the second trigger only, so a row added in a quiet stretch could sit
invisible for days — that's what Jeremy hit. The page renders the Queue
section only; its header links back here for the Done history.

Add a row when a landed doc needs Jeremy's eyes for a decision — not for
every record. When it's read/decided, MOVE the row to Done (don't delete
it; the queue's history is part of the decision record).

## Queue

| Added | Doc | Why / decision needed |
|---|---|---|
| 2026-08-05 | [BACKLOG § LT-4 batch results](https://github.com/slycrel/maro-orchestration/blob/main/BACKLOG.md) | Your directed cold/warm batch, complete same day: **12/12 PASS, warm = −49% on byte-identical arms** — artifacts/continuity are the carriers (LT-1's lessons-carry-nothing inverted), and B1w proved the skill store amortizes (cold-minted skills injected, manifest-verified). Per-arm evidence: `~/.maro/workspace/output/lt4-logs/scorecard.md`. ~~One decision: skill dead-drop fix direction~~ **DECIDED 2026-08-06** (promotion-side ingest, per recommendation) — batch results themselves still worth the read; LT-5 sonnet arm since added (verdict flip at lower cost, families model-independent). |

## Done

| Added | Doc | Resolved |
|---|---|---|
| 2026-08-08 | [Adversarial review: Δ-demotion + session-fork](https://github.com/slycrel/maro-orchestration/blob/main/docs/history/2026-08-08-delta-demotion-session-fork-adversarial-review.md) | 2026-08-08 — fix pass landed same day (c9cc864); the one open decision (GC re-mint erasing demote stamps) CALLED: gentle re-mint policy — tombstone survives GC, re-mints circulate while gathering data, 3rd re-mint forces full-set re-measurement (decision dcf8eab8). Noted-not-built variants: strict stamp-rides, experimental both-ways-viewable. |
| 2026-08-08 | [Scout wiring — design input](https://github.com/slycrel/maro-orchestration/blob/main/docs/history/2026-08-08-scout-wiring-design-input.md) | 2026-08-08 — load-bearing call CALLED: scout output is reading material only, never a store write ("gut says we still don't know enough yet... re-open if we need to later", decision 4d562766) — legs 3–4 void, untrusted-git boundary stays closed, ordering question moot. Telemetry + pedigree BACKLOG items stand on their own. |
| 2026-08-07 | [Live-writer census — 3 decision-shaped survivors](https://github.com/slycrel/maro-orchestration/blob/main/docs/history/2026-08-06-live-writer-census.md) | 2026-08-08 — all three CALLED (decision 1addc859): Phase-60 verification-calibration REMOVED ("makes me a little sad" but agreed — git remembers); inspector gets the evolver's run-finalize cadence lane + periodic deeper-pass rider (Jeremy's recalled resolution was the evolver's — inspector never got it); node promotion judged on age+content. |
| 2026-08-03 | [World-fact plan items design](https://github.com/slycrel/maro-orchestration/blob/main/docs/WORLD_FACTS_DESIGN.md) | 2026-08-08 — §7 all CALLED (decision f8f8d440): hypothesis quarantine at injection mirrors the provenance pattern; planner FACT: emission stays slice 3; cap sizes = build-time tuning. Jeremy open to discussion as testing/review rounds surface edges. |
| 2026-08-03 | [BACKLOG § LeAct — 2026-08-03 amendment](https://github.com/slycrel/maro-orchestration/blob/main/BACKLOG.md) | 2026-08-08 — moved to Done: both asks in the row were already CALLED (Δ-gate first 2026-08-06, decision 623eb056; seed-reader A/B ran + acted same day). Row was pure record since. |
| 2026-08-08 | [Δ-gate census round 2 + demotion shipped](https://github.com/slycrel/maro-orchestration/blob/main/docs/history/2026-08-06-delta-gate-validation.md) | 2026-08-08 — read same day; every decision in the row resolved in-row: 3 negatives stamped ("go ahead and stamp now"), promote floor RESOLVED BY DATA (sweep positives = winner's curse, sign-flipped on full set), retest 2 = no stamps for the flipped pair (run-to-run volatility finding; two-agreeing-full-set-runs standard held). Follow-on noise-floor calibration (temperature question) launched same day. |
| 2026-08-07 | [Δ-gate validation + routes census](https://github.com/slycrel/maro-orchestration/blob/main/docs/history/2026-08-06-delta-gate-validation.md) | 2026-08-08 — Jeremy read both, keep/adjust CALLED (GOAL_BRAIN decision + journal f7335288): gate stays ("very happy the gate is working"); negative-Δ demotion sanctioned GATED on census round 2 agreeing; Δ decreed surface-scoped (his catch: "other contexts might end up promoting on the same data"); haiku/sonnet-low replay spend cleared standing; calculation variables = adjustable priors. Round 2 launched same session (retest ×3 + stratified ×9 + rule ×2, checkpointed). |
| 2026-08-01 | [Run teachings design](https://github.com/slycrel/maro-orchestration/blob/main/docs/RUN_TEACHINGS_DESIGN.md) | 2026-08-02 — all sections now answered: §4b/§4a/§4d RATIFIED earlier same day (probe-gated first injection); §4c-expanded ANSWERED — terrain at its own surface, separate call to start (maybe parallel; pre/post-planner tension named in the doc), self-teachings caveat-grade injectable as single-win seed with NO solely time-based expiration. New ask folded in → BACKLOG: planner non-action world-fact types (anecdotal vs hypothesis). Decision block in §4c-expanded. |
| 2026-08-02 | [Playbook surprise read](https://github.com/slycrel/maro-orchestration/blob/main/docs/history/2026-08-02-playbook-surprise-read.md) | 2026-08-02 — Jeremy reacted to all flagged entries same day; disposition applied in full (live playbook + repo seed rewritten). Guidance-form decree minted ("usually, do this" — priors not requirements); P2 step-cap removed; truncation root cause fixed (bare [:200] at evolver append sites); frozen alarms/signals retired with mechanism gaps → BACKLOG. Reactions + disposition table recorded in the doc. |
| 2026-07-31 | [Lesson-corpus surprise read](https://github.com/slycrel/maro-orchestration/blob/main/docs/history/2026-07-31-lesson-corpus-surprise-read.md) | 2026-08-02 — Jeremy: remove from the list. Chunk 1's outputs both SHIPPED (retirement-by-contradiction + what-not-how mint-form pass); chunk 2's F1–F8 family verdicts waived — nothing blocks on them now that the tail families ride decay + the contest verb organically. The F1–F8 section stays in the doc if ever wanted. |
| 2026-08-01 | [BACKLOG § Fail-open judge-error edges](https://github.com/slycrel/maro-orchestration/blob/main/BACKLOG.md) | 2026-08-01 — Jeremy: "let's fix the promote validation." Adapter wired same session; validation harness live for the first time, SKILL_PROMOTED events stamp `validation: passed/unjudged/skipped`. |
| 2026-07-28 | [Telegram-runs review](https://github.com/slycrel/maro-orchestration/blob/main/docs/history/2026-07-28-telegram-runs-review.md) | 2026-07-29 — forked session adjudicated all dispositions (Jeremy granted direction/extraction/priority); provenance gate SHIPPED, db37d525+9d6b63fe quarantined, envelope specced (docs/DISPATCH_ENVELOPE.md), maro-dispatch skill 0.2.0 installed. "Already recovered" finding corrected: true on mini2, false-in-effect here — no artifact channel at the dispatch boundary. |
| 2026-07-28 | docs/history/2026-07-28-thread-census.md | 2026-07-28 — split ratified (cap 7), drift batch adjudicated (all four re-parked falsifiably), free slot = closure-check unification |
| 2026-07-28 | [M1 vs box workflow contrast](https://github.com/slycrel/maro-orchestration/blob/main/docs/history/2026-07-28-m1-vs-box-workflow-contrast.md) | 2026-07-29 — **CLOSED, item archived.** All four questions answered (§7) + the residual machine question (§8). Load-bearing claim SPLIT: diff-vs-run observation confirmed, merge-gate remedy rejected ("no master/slave between boxes"); observe-only thresholds rejected as too heavy. Spine corrected to two machines / three kinds of evidence. Produced four new items incl. the newly-decreed out-of-the-box invariant. |
