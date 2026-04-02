# Self-Improvement

Systems that make Poe measurably better over time without human intervention.

## Active Components

Currently running self-improvement subsystems and their roles in the feedback loop.

- **Evolver** (`src/evolver.py`) — meta-improvement every ~10 heartbeats; scans outcomes, generates Suggestions, auto-applies low-risk fixes
- **Thinkback** (`src/thinkback.py`) — session-level hindsight replay; reviews each step decision (good/acceptable/poor), extracts key lessons, rates mission efficiency. See [[quality-gates#Passes Pipeline]]
- **Bughunter** (`src/bughunter.py`) — AST-based self-directed code quality scan; runs against own src/
- **Nightly eval** (`src/eval.py`) — fires via `eval_every=1440`; failures → evolver Suggestion entries

## Pending: Promotion Cycle (Phase 56)

The single highest-leverage improvement from 2026-04-01 research batch.

Poe records outcomes and lessons but doesn't yet promote repeated lessons into standing rules applied by default. See [[memory-system#Pending: Promotion Cycle (Phase 56)]] for implementation plan.

**The three CLAUDE.md blocks to implement:**
1. Knowledge hierarchy — observation → hypothesis (2+ confirmations) → standing rule
2. Decision journal — ADR-style log searched before new decisions
3. Self-tightening quality gates — triggers promote, never-fires prune

## Related Concepts

Systems that supply raw material or apply the outputs of self-improvement.

- [[memory-system]] — raw material for all improvement signals
- [[quality-gates]] — inspector + adversarial + thinkback; separate quality layer
- [[core-loop]] — loop outcomes feed into self-improvement pipeline
