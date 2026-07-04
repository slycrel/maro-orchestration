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
| How do subsystems X work in detail? | `../skills/arch-*.md` (mandatory pre-reads per CLAUDE.md) |
| Coding style / seam principles for this repo? | `CODING_NOTES.md`; project artifacts: `CONVENTIONS.md` |
| Where may workers write? (write fence) | `BOUNDED_WORKSPACE.md` |
| What events go to the captain's log? | `CAPTAINS_LOG_EVENTS.md` |
| How does the navigator decide? | `NAVIGATOR_SCHEMA.md`; memory slice: `RECALL_DESIGN.md` |
| How does an external substrate (OpenClaw/Hermes) call us? | `SUBSTRATE_INTEGRATION.md` |
| Local validator model setup/results? | `LOCAL_VALIDATOR.md` |
| Security / sandbox posture? | `SECURITY_MODEL.md` |
| End-to-end smoke commands? | `END_TO_END.md` |
| Active refactor plan? | `REFACTOR_PLAN.md` (closes to history when done) |
| Dumb-loop audit evidence? | `DUMB_LOOP_AUDIT.md` (closes to history when done) |
| Adversarial-review verdicts on repo claims? | `VERDICT_INDEX.md` (full report in history) |
| Memory/knowledge design (input to the memory decision) | `MEMORY_ARCHITECTURE.md`, `KNOWLEDGE_CRYSTALLIZATION.md` (both dormant-design) |
| Intent resolution / "what does done mean"? | `INTENT_RESOLUTION_DESIGN.md` (partially shipped) |
| Scope/constraint orchestration (Phase 65, PAUSED)? | `CONSTRAINT_ORCHESTRATION_DESIGN.md` + `_REVIEW` + `_AUDIT` |
| Thread architecture reframe? | `THREAD_ARCHITECTURE.md` (dormant; navigator subset shipped) |
| Completed phase history (0–62)? | `history/ROADMAP_ARCHIVE.md` |
| Everything dated/pre-rename/superseded? | `history/` (see its README) |

Subdirectories: `history/` = dated records; `conversations/`, `research/`,
`knowledge-layer/` = source material and research records, kept as-written.
`../lat.md/` is a stale knowledge graph (last touched 2026-05-12, still injected
by `src/lat_inject.py` on meta-work) — its fate is part of the memory decision.
