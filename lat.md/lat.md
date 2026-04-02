This directory defines the high-level concepts, business logic, and architecture of this project using markdown. It is managed by [lat.md](https://www.npmjs.com/package/lat.md) — a tool that anchors source code to these definitions. Install the `lat` command with `npm i -g lat.md` and run `lat --help`.

## Index

Top-level map of all concept documents in this wiki. Each entry links to a dedicated page covering one subsystem.

- [[core-loop]] — The central autonomous execution loop (decompose → execute → checkpoint → memory)
- [[memory-system]] — Multi-tier memory: outcomes, lessons, skills, graveyard recovery
- [[self-improvement]] — Evolver, thinkback, bughunter, nightly eval — systems that improve Poe over time
- [[worker-agents]] — Director, workers, team workers, verification agent, persona system
- [[quality-gates]] — Constraint enforcement, inspector, adversarial pass, council, cross-reference
- [[poe-identity]] — Stable self-model injected into every planning call (GAP 1 fix)
- [[checkpointing]] — Per-step checkpoint writes for loop resume (GAP 3 fix)
- [[intent-classification]] — Lane routing (NOW vs AGENDA) and prefix handling
- [[constraint-system]] — Pre-execution safety layer; tier/risk enforcement
