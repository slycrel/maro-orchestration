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
| What are we deliberately NOT building? | `ARCHITECTURE_NON_GOALS.md` |
| What should Maro be able to do? (example goals, test corpus, pre-installed skill target) | `CAPABILITIES.md` (living catalog — add real asks as they happen) |
| Two-box / Hermes dispatch, interactive goals, effort-based spend UX, mid-flight injection? | `SESSION_PROTOCOL_DESIGN.md` (dormant-design; the 2026-07-15 skeleton, iterate there) |
| How do subsystems X work in detail? | `../skills/arch-*.md` (mandatory pre-reads per CLAUDE.md) |
| Coding style / seam principles for this repo? | `CODING_NOTES.md`; project artifacts: `CONVENTIONS.md` |
| What config flags exist, their defaults, why, and flip effects? | `DEFAULTS.md` (census-enforced by `tests/test_defaults_doc.py`) |
| Where may workers write? (write fence) | `BOUNDED_WORKSPACE.md` |
| What events go to the captain's log? | `CAPTAINS_LOG_EVENTS.md` |
| How does the navigator decide? | `NAVIGATOR_SCHEMA.md`; memory slice: `RECALL_DESIGN.md` |
| How does an external substrate (OpenClaw/Hermes) call us? | `SUBSTRATE_INTEGRATION.md` |
| Two-box Hermes-interface + Maro-orchestrator PoC recipe? | `../deploy/hermes/TWO_BOX_POC.md` (+ `../deploy/hermes/README.md` for the dispatch lane) |
| Local validator model setup/results? | `LOCAL_VALIDATOR.md` |
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
