# Poe Next-Phase Plan
_Updated: 2026-04-04 — corrected sequencing, 3-sprint grouping, full exit criteria_

---

## Status Corrections (CLAUDE.md is stale)

| Phase | CLAUDE.md says | Confirmed actual status |
|-------|---------------|------------------------|
| 42    | TODO          | DONE (2026-03-31, eval.py wired to heartbeat_loop) |
| 46    | IN PROGRESS   | DONE (2026-03-31, graduation.py shipped) |

These are no longer candidates. Sequence below reflects confirmed ground truth.

---

## Recommended Execution Sequence

```
Phase 41 Step 7 (MCP)      ← in-flight, unblock first
  ↓
Phase 40 (Memory Backend)  ← foundational; no blockers after MCP lands
  ↓
Phase 24 (Messaging)  ←—parallel—→  Phase 27 (Knowledge Sub-Goals)
  ↓
Phase 38 (Subpackage Restructure)   ← maintenance; lowest priority
```

---

## Sprint 1 — Close In-Flight + Lay Foundation

### Phase 41 Step 7 — MCP Integration
**Status:** PARTIAL (Steps 1–6 shipped; Step 7 remains)  
**Priority:** #1 — actively in flight; unblock first to avoid tool_registry conflicts

#### Goal
Expose `tool_registry`-registered tools via the Model Context Protocol so external clients (Claude Code, other MCP-aware agents) can discover and invoke Poe's tools without a direct Python import.

#### Rationale
Steps 1–6 shipped `tool_registry.py`, `skill_loader.py`, `step_events.py`, `tool_search.py`, magic keyword prefixes, and `poe-doctor` extensions. MCP is the only remaining gap. It closes the Claude Code ↔ Poe integration loop and is isolated enough to not conflict with other tracks — but any work touching `tool_registry.py` or `tool_search.py` must sequence after this.

#### Exit Criteria
- MCP server starts on a configurable port and advertises all tools registered in `tool_registry.py`
- External MCP client can list available tools and call at least one successfully
- Tool results conform to MCP response schema
- `poe-doctor` extended to validate MCP server health
- Graceful shutdown on SIGTERM; no zombie processes
- All existing tests (≥2282) pass

#### Testable Success Gate
`mcp-client list-tools --url http://localhost:<port>` returns a JSON list containing at least one tool registered in `tool_registry.py`.

---

### Phase 40 — Pluggable Memory Backend
**Status:** TODO  
**Priority:** #2 — foundational multiplier; sequence immediately after MCP

#### Goal
Decouple `memory.py` from jsonl-only storage. Add SQLite as an opt-in backend via `MEMORY_BACKEND=sqlite`. Every self-improvement loop writes to memory; until the backend is pluggable, future phases (FunSearch, Agent0 problem generation, nightly eval replay) risk tight coupling to flat-file limits.

#### Rationale
No hard blockers. Foundational for Phase 42 nightly eval queries and any long-term memory scaling. Should land before Phase 24 messaging handlers start writing outcomes to memory.

#### Exit Criteria
- `MEMORY_BACKEND=sqlite` env var (or `config.toml` key) switches the storage layer in `memory.py`
- SQLite schema mirrors existing jsonl fields exactly — no new abstractions
- jsonl remains the default; sqlite is strictly opt-in
- `poe-memory migrate` CLI converts existing jsonl files to SQLite with a progress indicator
- Roundtrip test: write records to jsonl → migrate to sqlite → read back → byte-identical records
- All existing tests (≥2282) pass with default jsonl backend

#### Testable Success Gate
`poe-memory migrate` completes on the real `memory/outcomes.jsonl` with zero data loss: record count in == record count out, verified by the migrate command's exit output.

---

## Sprint 2 — Extend Reach + Capability (parallel tracks)

### Phase 24 — Messaging Integrations (Telegram + Slack)
**Status:** PARTIAL  
**Priority:** #3 — closes the human feedback loop

#### Goal
Reliable inbound command handling and outbound formatted reports for Telegram (`@edgar_allen_bot`) and Slack. Signal deferred.

#### Rationale
Telegram bot token already in `~/.openclaw/openclaw.json`. Slack skeleton is partially built. This unblocks Jeremy's ability to interact with Poe without direct shell access. Can run in parallel with Phase 27 once Phase 40 lands.

#### Exit Criteria
- Telegram: inbound commands parsed and dispatched; outbound reports with markdown formatting delivered
- Slack: channel listener active; async responses posted within 10s of trigger
- `poe-msg send --channel telegram "test"` delivers end-to-end within 5 seconds
- Message handler failures logged with full traceback — no silent drops
- Heartbeat milestone completions auto-routed to Telegram

#### Testable Success Gate
`poe-msg send --channel telegram "smoke test"` returns exit 0 and the message appears in `@edgar_allen_bot` within 5 seconds on a clean run.

#### Dependencies
- Phase 40 recommended first (messaging handlers may write outcomes to memory)
- Slack workspace credentials (Jeremy to confirm if present)

---

### Phase 27 — Prerequisite Knowledge Sub-Goals
**Status:** PARTIAL  
**Priority:** #4 — goal quality improvement; parallel with Phase 24

#### Goal
Before executing a goal, automatically detect and resolve knowledge prerequisites as injected sub-goals rather than failing mid-execution.

#### Rationale
Knowledge gap detection was prototyped but not wired into `handle.py`. Deferred until in-flight work (MCP) clears. Phase 41 tool registry (DONE) and Phase 54 session checkpointing (DONE) are both complete — no new blockers.

#### Exit Criteria
- `handle.py` detects knowledge-gap signals in goal text and injects a `research:` sub-goal before the main plan
- At least 3 gap patterns recognized (unknown entity, missing data source, ambiguous domain)
- Sub-goals appear in milestone trace and are marked complete before parent goal resumes
- No regression on existing `ralph:` / `verify:` / `pipeline:` / `strict:` magic keywords

#### Testable Success Gate
A goal containing "analyze [unknown ticker]" auto-injects a research sub-goal, and that sub-goal's result is referenced in the parent step's context block.

#### Dependencies
- Phase 41 tool registry (DONE)
- Phase 54 session checkpointing (DONE)
- Phase 40 recommended first (sub-goal outcomes write to memory)

---

## Sprint 3 — Structural Consolidation

### Phase 38 — Subpackage Structure
**Status:** PARTIAL  
**Priority:** #5 — maintainability; lowest priority, schedule after higher-leverage phases ship

#### Goal
Reorganize `src/` (50+ flat modules) into logical subpackages (`core/`, `memory/`, `tools/`, `messaging/`, `eval/`) to reduce import coupling and improve onboarding.

#### Rationale
Pure maintenance — no new capability. Easier before the surface area grows further. Schedule after MCP (Phase 41 Step 7) since MCP adds an external surface that should be placed in the right subpackage from day one.

#### Exit Criteria
- `src/` split into at least 4 subpackages with `__init__.py` files
- All existing imports updated — zero broken imports after restructure
- `python3 -m pytest tests/ -q` passes with same count as pre-restructure
- `scripts/smoke.sh` passes

#### Testable Success Gate
`python3 -m pytest tests/ -q` reports ≥2282 passing tests (matching pre-restructure baseline) with zero import errors.

#### Dependencies
None blocking. Sequence after Phase 41 Step 7 (MCP) to place the MCP module in the right subpackage.

---

## Background / Research Tracks (run as AGENDA, not blocking sprints)

| Track | Phase | Status | Notes |
|-------|-------|--------|-------|
| Observability Dashboard | 23 | PARTIAL | Useful for debugging; unblock after Phase 38 |
| Human Psychology Research | 29 | PARTIAL | Pure research track; no blocking dependency |
| Phase 27 research threads | 27 | PARTIAL | Knowledge gap taxonomy — run overnight missions |

---

## Success Criteria Reference

Full measurement definitions are in `docs/success-criteria.md` (v1.0, 2026-04-04). Summary of gates:

| Metric | Target | Alarm |
|--------|--------|-------|
| Task Completion Rate | ≥85% NOW-lane | <70% / 7-day |
| Autonomy Ratio | ≥90% steps autonomous | <80% for 3+ days |
| Cost-Per-Mission | NOW ≤$0.25, AGENDA ≤$1.50 | >$5 unauthorized |
| Memory Retention Rate | ≥80% rules applied when relevant | — |
| Friction Score | mean ≤0.20/session | >0.30 breach |
| Self-Improvement Velocity | ≥5 net-new standing rules/month | — |
| Phase Exit Quality | binary gate: all 6 above + test coverage | — |

Measurement sources already wired: `task_store`, `inspector.py`, `metrics.py`, `memory/outcomes.jsonl`, `memory/standing_rules.jsonl`. No new instrumentation needed.

---

## Conflict Map

| Work item | Touches | Must sequence after |
|-----------|---------|---------------------|
| Phase 40 | `memory.py` | Phase 41 Step 7 (MCP) — avoid parallel `memory.py` edits |
| Phase 24 | messaging handlers, heartbeat | Phase 40 (outcomes write to memory) |
| Phase 27 | `handle.py` | Phase 40; parallel with Phase 24 |
| Phase 38 | all `src/` imports | Phase 41 Step 7 (MCP) — place MCP module correctly |
