# Milestones — Prioritized Work Queue

What to do next, in what order. Updated each session. Strategic phases live in ROADMAP.md; deferred ideas live in BACKLOG.md. This file is the bridge — the executable queue.

Last updated: 2026-04-11 (session 15)

---

## Next Up

1. **Advisor Pattern integration** — Sonnet executes, Opus advises at decision points. `advisor_call()` shipped in `llm.py`, wired into stuck detection. Next: wire into evolver meta-improvement triggers and milestone boundaries. (9/10 from X research)

2. **Thinking Token Budget** — `thinking_budget: int` in `llm.py`; high for planning, low for execution. Immediate win. (8/10)

3. **Route output + projects to workspace** — `output_root()` and `projects_root()` still use `orch_root()` (the repo). Needs `relative_to(orch_root())` audit. Then `poe-export/import` becomes a simple tar.

4. **K2: Migrate links → knowledge nodes** — Transform existing knowledge into Ledger/Web/Lens architecture. K0, K1 DONE. K3 partially done (captain's log read bridge shipped).

5. **Evals-as-Training-Data flywheel** — Mine prod failures → auto-generate evals → harness tweaks. (9/10, medium effort)

6. **Real-world regression tests** — Polymarket behavioral analysis, nootropic re-run.

## Queued

7. **Event-driven subprocess wakeup** — Replace polling with asyncio.Queue signal. (7/10)
8. **Phase 62: Auto persona+skill packaging**
9. **Codebase Graph + LSP** — Pre-build call graph; LSP-guided context slicing. (9/10, longer term)
10. **Remaining adversarial findings** — BUG-2 (lock file mode), BUG-3 (project starvation sort)

## Done (session 15)

- [x] 5 adversarial bugs fixed (constraint DoS, parallel fan-out, goal-text, security bypass, meta-command)
- [x] Meta-command detection: hard syntactic gate (URL-strip + word-count + exact match)
- [x] 10 X links researched via live orchestration (two loops, quality gate auto-escalated)
- [x] Workspace consolidated on `~/.poe/workspace/` — fixed memory_dir split-brain
- [x] Two-tier YAML config: `~/.poe/config.yml` (user) + `~/.poe/workspace/config.yml` (workspace)
- [x] Inspector + constraint thresholds wired to config.yml
- [x] Captain's log read bridge (K3 partial) — 11K events now surface at reasoning time
- [x] Advisor Pattern: `advisor_call()` in llm.py, wired into stuck detection
- [x] `poe-enqueue` CLI + user_goal queue + `_check_cycle` DFS fix
- [x] Adversarial self-review via orchestration — 11 findings, 7 fixed
- [x] Dead import cleanup (7 items across poe.py, handle.py, orch_items.py)
- [x] markitdown installed
- [x] 3436 tests passing (up 35 from 3401 at session start)
