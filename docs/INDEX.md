---
status: living
---

# docs/ Index — question → doc

Status legend (frontmatter on every doc here): **living** = kept current, trust it;
**dormant-design** = design thinking read for intent, NOT current state — verify
against code before acting on specifics; **record** = point-in-time snapshot in
`docs/history/`, correct as of its date. Root files (GOAL_BRAIN, MILESTONES,
BACKLOG, VISION, CLAUDE, README…) are living by definition and carry no frontmatter.

| Question | Doc |
|---|---|
| What is current truth / what won a decision? | `../GOAL_BRAIN.md` (wins on conflict, by decree) |
| What should I work on next? | `../MILESTONES.md`, then `../BACKLOG.md` |
| How does the whole system fit together? | `ARCHITECTURE_OVERVIEW.md` (incl. V→R→R doctrine + visibility ladder) |
| How did our *thinking* get here? (era timeline, aha moments, pros/cons vs today) | `KNOWLEDGE_JOURNEY.md` (living; details in `history/knowledge-journey/`; raw excavation artifacts in `history/knowledge-journey/artifacts/`) |
| The swarm-review arc conversation (decrees, taste/judgement, star pattern, CGI)? | `conversations/2026-07-20-swarm-review-arc.md` (verbatim session log) |
| Taste/judgement patterns for dev sessions (cuts-first, consumer-first, live-writer?…)? | `DEV_PATTERNS.md` (living; non-gated CLAUDE.md pre-read — battery verdict 2026-07-21) |
| The Phase 0.5 with-doc/control battery (protocol, verdict, new findings V1-V6)? | `history/2026-07-21-phase05-battery.md` (raw arm outputs in `history/phase05-battery/`) |
| Which memory/knowledge stores have live writers AND readers? (orphan/dead map) | `history/2026-07-21-wiring-inventory.md` (report-only; agent-produced, verify before fixing; enforcement pin after chunks 3-4) |
| What happened to factory mode / the Bitter Lesson experiment / mode:thin? | `history/2026-07-21-factory-adjudication.md` (branch archived as tag `archive/factory-2026-03-31`; thin/minimal kept as instruments) |
| What did the chunk-1 adversarial review find? (residual cheap paths, strict finding-code boundary) | `history/2026-07-21-chunk1-adversarial-review.md` (6 fixed / 1 rejected; 0/7 reviewer claims hallucinated) |
| What did the chunk-2 adversarial review find? (LLM-under-lock, soft compression guard, dedup-before-rank) | `history/2026-07-21-chunk2-adversarial-review.md` (6 findings, all accepted ≥ in part; 0/6 hallucinated) |
| Where can a run stop, and what conflations did the seam map find? | `history/2026-07-23-stop-path-survey.md` (~50 seams / 11 families vs the four stop verdicts) |
| The mid-step token brake (ceilings, calibration, review verdicts)? | `history/2026-07-27-token-brake-adversarial-review.md` (fresh 300K + weighted 600K; trap-metric warning) |
| How are typed stop verdicts wired? (break sites, precedence, consumers) | `history/2026-07-27-stop-verdict-split.md` (chunk-9 #4; external-interrupt = event marker per decree, not a fifth verdict) |
| What did the chunk-9 #4 adversarial review find? (evidence on ledger rows, merge-failure re-stamp, two-channel repeat guard) | `history/2026-07-27-chunk9-4-adversarial-review.md` (6 findings, 6/6 verified, 4 accepted; eleventh clean round) |
| Recon flavor + §14 diagnosis in star, and the cap-stuck / achieved-but-stuck live numbers? | `history/2026-07-27-chunk9-2-recon-diagnosis-star.md` (star use #2; blocked_on contract; 9/726 cap-stuck; 2 achieved-true runs classify "failed") |
| What did the chunk-9 #2 adversarial review find? (dedup key, reject-bridge, substantiation line) | `history/2026-07-27-chunk9-2-adversarial-review.md` (6 findings, 6/6 verified, all accepted; twelfth clean round) |
| Did the tire-goal rerun move the needle after the brake land? | `history/2026-07-27-tire-rerun-needle.md` (run 4 delivered; brake armed, never fired; warm-start confound) |
| Per-step learning from failed runs? (provisional lessons, achieved-not-done, promote-on-evidence) | `history/2026-07-27-per-step-learning.md` (learn at verification granularity, inject conservatively; asymmetric bar) |
| What did the per-step learning adversarial review find? (verdict-blind extraction, graveyard leak, validation accrual) | `history/2026-07-27-per-step-learning-adversarial-review.md` (7 findings, 6/7 verified, 0 hallucinated; thirteenth clean round) |
| What does an escalation payload carry? (single-chasm decision line, family-ROI recurrence line) | `history/2026-07-27-escalation-payload.md` (§9.6 simple-first; three emit sites; telegram leads with the ask) |
| What did the §9.6 adversarial review find? (diagnose_loop side effect, unreadable-ledger false "first", useless count cap) | `history/2026-07-27-escalation-payload-adversarial-review.md` (3 findings, 3/3 verified, 0 hallucinated; fourteenth clean round) |
| What open threads exist, in what state? (premise-drift finds, ACTIVE/PARKED split, marker-convention rules) | `history/2026-07-28-thread-census.md` (star use #3; 56 threads / 7 states; 7 premise-drifts, 1 closed on the spot; 8 doc currency fixes; 10/10 sweep spot-checks held) |
| What are we deliberately NOT building? | `ARCHITECTURE_NON_GOALS.md` |
| What should Maro be able to do? (example goals, test corpus, pre-installed skill target) | `CAPABILITIES.md` (living catalog — add real asks as they happen) |
| What do those goals add up to, and what's the next bridge? (C0–C5 checkpoints, per-chasm ladders, tech-tree nodes) | `CAPABILITY_LADDER.md` (progression map; CAPABILITIES.md stays the goal well) |
| Why does the lesson funnel miss operational/terrain facts, and what's the fix? (teaching kinds/altitudes, goal-level extraction, scope, probe-verified terrain) | `RUN_TEACHINGS_DESIGN.md` (dormant-design; specimen = the $15 blocked-archives relearn, UU-2) |
| Two-box / Hermes dispatch, interactive goals, effort-based spend UX, mid-flight injection? | `SESSION_PROTOCOL_DESIGN.md` (dormant-design; the 2026-07-15 skeleton, iterate there) |
| How do subsystems X work in detail? | `../skills/arch-*.md` (mandatory pre-reads per CLAUDE.md) |
| Coding style / seam principles for this repo? | `CODING_NOTES.md`; project artifacts: `CONVENTIONS.md` |
| What config flags exist, their defaults, why, and flip effects? | `DEFAULTS.md` (census-enforced by `tests/test_defaults_doc.py`) |
| Where may workers write? (write fence) | `BOUNDED_WORKSPACE.md` |
| What events go to the captain's log? | `CAPTAINS_LOG_EVENTS.md` |
| What landed docs await Jeremy's read/decision? | `READING_QUEUE.md` (living; renders to the viz server's Reading tab at every index regeneration — GitHub links, nothing self-hosted) |
| What happened in recent dev sessions? (narrative + surprises) | `DEV_LOG.md` (living; append-only dev captain's log, newest first) |
| How does the navigator decide? | `NAVIGATOR_SCHEMA.md`; memory slice: `RECALL_DESIGN.md` |
| How does an external substrate (OpenClaw/Hermes) call us? | `SUBSTRATE_INTEGRATION.md` |
| Two-box Hermes-interface + Maro-orchestrator PoC recipe? | `../deploy/hermes/TWO_BOX_POC.md` (+ `../deploy/hermes/README.md` for the dispatch lane) |
| Local validator model setup/results? | `LOCAL_VALIDATOR.md` (history — local rung removed 2026-07-21; revival trigger + bakeoff methodology inside) |
| Security / sandbox posture? | `SECURITY_MODEL.md` |
| How do we cut/publish a release? | `PUBLISH_CHECKLIST.md` (exists since v0.1 — cite it, don't re-derive; SF-14 release amnesia) |
| How do I monitor the host (disk/spend/orphans/heartbeat)? | `HOST_MONITORING.md` (runs `../scripts/host-check.sh`) |
| End-to-end smoke commands? | `END_TO_END.md` |
| Active refactor plan? | `REFACTOR_PLAN.md` (closes to history when done) |
| Pre-1.0 retrospective audit (the seven eyes)? | `PURGATORIO_AUDIT.md` (gates 1.0 completeness; findings → `audit-2026-07/`) |
| Dumb-loop audit evidence? | `DUMB_LOOP_AUDIT.md` (closes to history when done) |
| Adversarial-review verdicts on repo claims? | `VERDICT_INDEX.md` (full report in history) |
| The memory decision (filesystem vs "real" memory)? | `history/2026-07-04-memory-decision-brief.md` (direction DECIDED 2026-07-07: module + bake-off — see GOAL_BRAIN Decisions; port = `src/memory_port.py`) |
| The memory module (port/adapters/bridge/instrument)? | module docstrings are canonical: `src/memory_port.py` (contract), `src/memory_sqlite.py` (production store), `src/memory_jsonl.py` (reference adapter), `src/memory_bridge.py` (lessons ingest + worker slice), `src/memory_quality.py` (retrieval instrument); verdict pedigree in `history/2026-07-07-memory-bakeoff.md`; §7 A/B verdict in `history/2026-07-08-worker-slice-ab.md` (raw rows/logs in `../output/experiments/`) |
| Memory/knowledge design (input to the memory decision) | `MEMORY_ARCHITECTURE.md`, `KNOWLEDGE_CRYSTALLIZATION.md` (both dormant-design; several of their "missing" items have since shipped — see the brief §2) |
| Intent resolution / "what does done mean"? | `INTENT_RESOLUTION_DESIGN.md` (partially shipped) |
| Mint-time grounding — evidence receipts on lesson/skill mints (claims-vs-events family)? | `MINT_GROUNDING_DESIGN.md` (living; shape decided 2026-08-06 — annotation not judge, fail-closed at republish only) |
| Portable/shareable learning — migration + learning packs (1.0 item (g))? | `PORTABLE_LEARNING_DESIGN.md` (dormant-design; §8 RATIFIED 2026-07-12 — all chunks 1-4 shipped, minimum 1.0 slice complete) |
| How do I move a workspace to a new machine? | `MIGRATION.md` (living runbook; §7 chunk 1 SHIPPED 2026-07-12 — `maro-doctor` now checks config paths/stale state/index sync post-restore; chunks 3+4 SHIPPED 2026-07-13 — `maro-pack export`/`seal`/`import`/`adopt`, full lifecycle closed) |
| Containerized executor (arch-r2-01, 1.0 blocker #4)? | `CONTAINER_EXECUTOR_DESIGN.md` (dormant-design; C1–C3 shipped, §7 sandbox retired 2026-07-13; C4 burn-in + flip is Jeremy's on box evidence) |
| How do I burn in the container executor / run the security acceptance probe? | `CONTAINER_BURN_IN.md` (living runbook; box-side procedure + `scripts/container-acceptance-probe.sh`) |
| Verify→learn — the next arc after 1.0 (thread-arch #6)? | `VERIFY_LEARN_ARC.md` (dormant-design; hard dependency probe-env hardening B3 SATISFIED 2026-07-12; V1 expectation-stamping SHIPPED 2026-07-14, V2-V5 open) |
| Live-data routing signal + probe-synthesis first slice? | `docs/history/2026-07-12-routing-and-probe-synthesis-design.md` (record — BOTH PARTS SHIPPED 2026-07-12; Manti canonical case is the acceptance) |
| Handing work to less-capable implementing models? | `IMPLEMENTATION_HANDOFF.md` (written at the 2026-07-12 Fable transition) |
| Scope/constraint orchestration (Phase 65, PAUSED)? | `CONSTRAINT_ORCHESTRATION_DESIGN.md` + `_REVIEW` + `_AUDIT` |
| Thread architecture reframe? | `THREAD_ARCHITECTURE.md` (dormant; navigator subset shipped) |
| Thread architecture — what's actually still pending (vs. what shipped)? | `history/2026-07-09-thread-architecture-decisions-brief.md` (triage brief, not yet decided — see BACKLOG #19) |
| Completed phase history (0–62)? | `history/ROADMAP_ARCHIVE.md` |
| Everything dated/pre-rename/superseded? | `history/` (see its README) |

Subdirectories: `history/` = dated records; `conversations/`, `research/`,
`knowledge-layer/` = source material and research records, kept as-written.
`../lat.md/` is a hand-written knowledge graph, mostly stale (content era
2026-05-12; two nodes cite modules that don't exist; still injected as ~200
tokens of flat text by `src/lat_inject.py` on meta-work) — its fate is
decision point #3 in `history/2026-07-04-memory-decision-brief.md`.
