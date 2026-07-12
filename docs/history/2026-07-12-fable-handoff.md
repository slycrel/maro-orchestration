---
status: record
---

# Fable-handoff session — 2026-07-12

Context: top-tier-model (Fable) access ended ~2026-07-13; implementation
continues on Sonnet/Opus. This session audited the queues, adjudicated the
queued decision debt with Jeremy live, and front-loaded the design passes so
successor sessions inherit execution-shaped work. Companion doc:
`docs/IMPLEMENTATION_HANDOFF.md` (living guidance).

## Audit verdict

Queues healthy; r2 blocker list 6 items; record-keeping machinery (SF-13)
held under adversarial re-audit. The scarce resource was design judgment:
container executor *decided but undesigned* (arch-r2-01), verify→learn
*decreed but unwritten* (#6), verifier synthesis + live-data routing
*repeatedly named, never scheduled*, ~20 Jeremy-gated decisions queued.

## Decisions adjudicated (full text in GOAL_BRAIN Decisions 2026-07-12)

1. **Escalation channel (1.0 item (a)) — DECREED**: the substrate LLM
   go-between is the official escalation surface; durable escalation file
   ships unconditionally; no beacon machinery; doctor reports what's live.
2. **Portable-learning §8** — all 8 ratified as written.
3. **Backend-resilience** — all 4 flagged provisionals ratified; depth-cap
   unification queued.
4. **`claude -p` ToS** — keep as quickstart default + usage-policy caveat
   (README updated same session).
5. **Orphan scope A/B datasets** — written off (closes arch-03); data kept
   per retention decree.
6. **slack-bridge** — leave as-is until someone touches the notify surface.
7. **Heartbeat toggles** — autonomy OFF until direct-use transition;
   host-check threshold realigned (ops-r2-01/02; runtime-box change, rides
   the supervision-convergence chunk).
8. **Navigator close cutover** — reconfirmed organic-blocked.

Remains Jeremy's: git-history privacy review, PyPI publish act, #24
model-route session (in progress that day), container flip after burn-in.

## Designs shipped this session

- `docs/CONTAINER_EXECUTOR_DESIGN.md` — clears arch-r2-01 (r2 blocker #4's
  design half). Key catch: mounting host `~/.claude` rw into the sandbox is
  an escape vector (settings/hooks tampering → host code execution);
  dedicated container auth volume instead. Chunks C1–C4.
- `docs/VERIFY_LEARN_ARC.md` — the decreed next-arc-after-1.0 brief.
  Frame: applied changes get the lifecycle discipline lessons already have
  (expectation at birth, verdict at cadence, demotion when contradicted);
  generalizes refight_rule / circuit-breaker / shadow-adjudication. Hard
  dependency: probe-env hardening (B3) before any verdict-window logic.
  Chunks V0–V5.
- `docs/ROUTING_AND_PROBE_SYNTHESIS_DESIGN.md` — Part A: `needs_live_data`
  classifier signal (the Manti routing failure is structural: the classifier
  prompt's own NOW example teaches the misroute). Part B: probe honesty
  (Deliverable.shape, shape-conditional behavioral-probe MUST, probe-env
  hardening incl. the surviving cwd=None hole at closure_verify.py:640).
- Queue vehicles (no design pass, per Jeremy's pick): time-blindness first
  slice + perspective end-user seat, both sized in BACKLOG Vision entries.

## Session-adjacent facts

Concurrent sessions the same day: BACKLOG #23 (worker async-escape family)
closed with all seven mechanisms; #24 (model-route exploration) research
phase committed (168319e). MILESTONES staleness reconciled (-4 Purgatorio
marked complete-through-r2; -1/0 arc labels corrected). SECURITY_MODEL.md
line-drift fixed per currency rule. Commits this session: 30628a3 (queue
hygiene), ffff3f6 (decision batch), + the design-pass commit carrying this
file.
