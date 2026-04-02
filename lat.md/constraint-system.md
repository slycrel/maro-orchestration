# Constraint System

Pre-execution safety layer. Guards every step before tool calls. Prevents unauthorized external actions, spending, and irreversible operations.

## Tiers

Five risk tiers control what actions Poe may take autonomously versus escalate.

| Tier | Default | Notes |
|------|---------|-------|
| READ | Always allowed | File reads, searches |
| WRITE | Allowed with logging | File writes, local state |
| EXTERNAL | gate=confirm | Network calls; proceeds autonomously in headless mode |
| FINANCIAL | Blocked | Explicit allowlist only |
| DESTRUCTIVE | Blocked | Escalate to Jeremy |

## Key Source Files

Implementation of the constraint enforcement layer.

- `src/constraint.py` — `enforce_constraint()`: tier classification, risk scoring, policy enforcement

## HITL Policy

`gate=confirm` steps log `[poe] HITL confirm: ... proceeding autonomously` and continue. Current headless-mode behavior. Future: interrupt queue integration for async confirmation.

## Related Concepts

Other systems that interact with constraint enforcement during execution.

- [[core-loop]] — constraint check fires in step_exec before every tool call
- [[quality-gates]] — constraint is the earliest gate; quality gates are post-execution
- [[worker-agents]] — role-based tool visibility is a related access-control layer (Phase 41)
