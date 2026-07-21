---
status: history
---

# Phase sprint: tiered memory, promotion cycle, personas

*2026-04-01 – 2026-04-13*

263 commits in 13 days (48cc7cb 2026-04-01 → 670f5fe 2026-04-14 00:29), +52,332/−2,912 lines in src/+tests/, tests 2,013 → ~4,278, sessions 6 → 20.5. **Dating correction:** tiered memory (Phase 16, f6d7cf4) and the persona system (Phase 20, 8e8842d) shipped 2026-03-25, *before* this window. In-window persona work was the garrytan persona (1eed4ee), persona template injection (54e1139), and the Enneagram 6w5/INFJ companion research (CHANGELOG 1.10.26).

## Architecture as it was

At era end (capstone commit 93da18b, the 04-13 phase audit) the repo was "openclaw-orchestration" and the system was "Poe — an autonomous AI concierge." 97 src modules, 106 test files, 3,789+ tests. Five documented subsystems, each with a session-bootstrap architecture skill (skills/arch-*.md, a658eb0) explicitly documenting "intent vs implementation gaps."

- **Interface:** handle.py (1,104 lines) routing NOW/AGENDA via intent.py, magic-prefix registry — `ralph:`, `verify:`, `strict:`, `effort:`, `ultraplan:`, `direct:`, `btw:`, `mode:thin` (CHANGELOG 1.10.0). Runtime state in ~/.poe/workspace/ with workspace→repo resolution order so self-evolved skills/personas override shipped defaults (9e3d46e).
- **Core loop:** agent_loop.py monolith, 4,261 lines at 93da18b. Decompose → execute → introspect; pre-flight plan review + milestone expansion (Phase 58); adaptive tier escalation cheap→mid→power on retries plus trajectory floor (Phase 57, live at agent_loop:502-509 per the audit); staged-pass decomposition with budget-ceiling continuation tasks (73f17db); director escalation consumer (a9bf68b/9119505); per-step checkpointing with resume/branch/export (Phase 54, 1.9.0); Phase 62 adaptive replanning — convergence tracking, mid-loop re-decomposition, sibling failure correlation, NEED_INFO, shared artifact layer (771b67c).
- **Tools:** Phase 41 gating at prompt-composition time — ToolRegistry + PermissionContext role factories, progressive skill disclosure (summary stub in prompt, full body on demand), deferred-tool resolution via a tool_search tool (4976db8, dce8460, f2927bf; CHANGELOG 1.7.0).
- **Memory:** re-architected mid-era. memory.py split 2,968→530 lines into memory_ledger.py (943), knowledge_web.py (1,330), knowledge_lens.py (835) on 2026-04-10 (94c7f7a, 721a263, 129ec5a), per the Ledger/Web/Lens design (docs/knowledge-layer/01_ARCHITECTURE.md, from the 04-09 cowork commit 11fab6f). On top of pre-era tiered lessons: Phase 56 promotion cycle (observe_pattern → StandingRule at 2 confirmations, contradict_pattern demotes) and a decision journal (Decision dataclass with rationale/alternatives/trade_offs, TF-IDF search). Both injected into every decompose call (93da18b:src/agent_loop.py:2444-2454). lat.md/ held 9 wiki-linked concept nodes with `# @lat:` source backlinks (5e9fe14); K2 imported 315 link-farm nodes (1389e08).
- **Self-improvement:** evolver.py (2,347 lines) enriched by the 04-04/05 steal flood — calibration review, cost scanning, FunSearch island model, Agent0 skill A/B variants, majority-vote lesson extraction, skill validation harness with repair loop (1.10.x); plus thinkback hindsight replay, passes pipeline, quality gate with council/debate/cross-ref, and — added the final night — auto-revert on verify failure (c7e49bf).
- **Platform:** llm.py adapter suite (ClaudeSubprocess/AnthropicSDK/OpenRouter/OpenAI/Codex) with multi-cycle rate-limit retry (CHANGELOG 1.10.4) and config-driven backend_order (d024554); heartbeat with 1,800s diagnosis cooldown, interactive-session guard, event-reactive wakeup, `.poe-failed`/`.poe-paused` lifecycle markers (1.11.0).

## Discoveries & aha moments

### Promotion cycle: lessons need a confirmation ladder (04-01)
Raw lessons are hints, not rules. Phase 56: observation → hypothesis (2+ confirmations) → StandingRule injected unconditionally into every decompose call, with contradiction-driven demotion, plus an ADR-style decision journal searched before planning. Epigraph: "Lessons observed once are hints. Lessons confirmed twice become rules."
Evidence: b37256e; CHANGELOG [1.6.0]; ROADMAP_ARCHIVE.md Phase 56; 93da18b:src/agent_loop.py:2444-2454.

### Docs as a graph, not prose (04-01)
"Flat AGENTS.md doesn't scale — concepts need cross-references, not prose paragraphs" (Phase 55 epigraph). Concepts moved to 9 wiki-linked lat.md nodes with `# @lat:` backlinks from source and a `lat check` validator — code and concept docs became one navigable graph.
Evidence: 5e9fe14; ROADMAP_ARCHIVE.md Phase 55; CHANGELOG [1.6.0].

### Subtraction as discipline: −51% system prompt + explicit non-goals (04-03)
Pi coding-agent research flipped thinking from adding rules to deleting them: EXECUTE_SYSTEM 844→333 tokens, DECOMPOSE_SYSTEM 1048→603, −51% combined with behavior preserved. Same session: docs/ARCHITECTURE_NON_GOALS.md — 8 deliberate non-goals with revisit conditions. Still exists today.
Evidence: b009301; CHANGELOG [1.8.0]; BACKLOG_DONE.md:2563-2564.

### Prompt-composition-time tool gating + deferred resolution (04-02/03)
Permissions belong at prompt composition, not execution: ToolRegistry + PermissionContext, progressive skill disclosure, tool_search resolving deferred schemas on demand. Stolen from Claude Code architecture research — the identical stub-then-ToolSearch pattern later shipped in Claude Code's own harness, validating the design.
Evidence: 4976db8, dce8460, f2927bf; CHANGELOG [1.7.0].

### The steal flood: research-run → ranked steal list → same-day build (04-04/05)
27 CHANGELOG releases in two days (1.10.0–1.10.26); tests 2,013→2,917. Orchestration runs ingested X posts and papers (GStack, Meta-Harness, Agent0, FunSearch, Voyager, 724-office, MetaClaw, Agent-Reach, @_overment), ranked steal candidates, and built them immediately with source attribution. The system was partly building itself.
Evidence: CHANGELOG [1.10.0]–[1.10.26]; docs/history/2026-04-05-steal-list.md; e5e9892, 806e8b9, cc0f822, a4f2668.

### The flywheel was inert: times_applied always 0 (04-04/05)
The success-criteria gap audit forced binary PASS/FAIL gates on five dimensions and found nothing was measured: "times_applied is always 0 — the lesson feedback loop is inert; lessons are recorded but never retrieved at inference time" (G1); dollar cost never computed (G7); no per-session summary (G8). Building learning machinery is not learning — the write path had outrun the read path.
Evidence: docs/history/2026-04-05-success-criteria-gap-audit.md.

### Overnight token gorge: background autonomy silently burns money (04-07)
Heartbeat tier-2 LLM diagnosis fired every 60s per stuck project; 6 zombie projects = 360 calls/hour all night. Fixes set the standing pattern: 1,800s cooldowns, an interactive-session guard pausing ALL autonomous LLM work, and explicit `.poe-failed`/`.poe-paused` markers so zombies stop being "stuck."
Evidence: CHANGELOG [1.11.0] Fixed — token runaway.

### Ledger/Web/Lens: memory is four operations, three views of one reality (04-09/10)
"'Memory' is used to mean at least four different cognitive operations: recall, association, reasoning, recognition... that happen to share a storage layer." The cowork session named the skeleton the project had been building blind; memory.py was physically split the next day. Same doc introduced the Mage "Correspondence" framing that today names the dev-recall module.
Evidence: 11fab6f, 0532be1, 94c7f7a, 721a263, 129ec5a; docs/knowledge-layer/01_ARCHITECTURE.md; GOAL_BRAIN.md:586.

### Compact notation A/B: NOT RECOMMENDED — measure before adopting (04-10)
The hyped token-shorthand vocabulary was built as an opt-in skill, then A/B tested: avg +0.7% token reduction, median +9.3% reduction, but range −97.8% to +63.6% (positive = fewer tokens, per src/compact_ab.py:77). The verdict rested on variance, not the median: the LLM doesn't reliably adopt shorthand and sometimes spends more tokens mixing styles. Recorded and left off — the harness killing its own plausible optimization with evidence.
Evidence: 965ad81 (harness), 1db8878 (verdict); BACKLOG_DONE.md:2565.

### Self-audits hallucinate: 6 of 10 findings fabricated (04-12/13)
A subprocess-adapter self-audit produced 10 code claims; verification confirmed 3, found 6 hallucinated (nonexistent functions, invented line numbers, plausible TOCTOU bugs). Origin of the standing verify-before-fix rule (~30–50% of adversarial findings hallucinated) and the claim_verifier symbol-checking work (CHANGELOG [1.17.0]).
Evidence: memory archive project_session18_testing.md; feedback_verify_before_fix.md.

### Ghost features: "done" in the roadmap ≠ wired in the execution path (04-13)
"Context: Jeremy suspected multiple phases were surface-level implemented." The session-19 audit of 8 high-risk phases checked whether main code paths actually CALL each feature: 5 honestly wired, 2 loop-end-only (Phases 44/45 — diagnosis/recovery never fire mid-loop), 1 pure ghost — Phase 59's record_skill_outcome() defined but never called. The audit method (invocation, not existence) became the template for every later verification arc.
Evidence: 93da18b; docs/history/2026-04-13-phase-audit.md.

### Self-improvement needs rollback as a first-class primitive (04-13)
Session 20's blind adversarial review (14 findings, 5 parallel live goals) found the evolver's _verify_post_apply logged test failures after auto-applying a suggestion but did NOT revert — "the self-improvement loop can make itself worse and stay that way." Fixed the same night: auto-revert on verify failure; cost_optimization suggestions demoted to held-for-review.
Evidence: d7ce543, c7e49bf; memory archive project_session20_regression.md finding 3.2.

## Pros vs today's architecture

- **Raw velocity with full legibility:** 263 commits, +52k src+tests lines, ~2,300 net new tests in 13 days — every increment a versioned CHANGELOG entry with test counts, reconstructible months later. Today's process (decrees, verification layers, fork protocols) is safer but heavier per shipped idea. (git diff --shortstat 48cc7cb^..670f5fe; CHANGELOG [1.6.0]–[1.18.0])
- **Steal provenance discipline:** every borrowed idea tracked source → landing file → status → date in one table; still one of the project's best-maintained documents. (docs/history/2026-04-05-steal-list.md)
- **Evidence-based rejection already alive:** the Polymarket BTC-lag claim was structurally invalidated ("4% round-trip fees vs 0.3% claimed edge = −13x EV. No build warranted.") instead of built; compact notation was A/B tested and declined. (4c4c4cb; CHANGELOG [1.6.0]; BACKLOG_DONE.md:2565)
- **Invented today's audit instruments:** wired-vs-exists phase audit, verify-before-fix, evolver auto-revert, intent-vs-implementation-gap arch skills (still present, updated as late as 2026-07-03). (2026-04-13-phase-audit.md; a658eb0; skills/arch-core-loop.md)
- **Monolith simplicity as speed:** whole loop in one file, no fork/worktree protocol — one overnight session shipped five phases (+16,285 lines, 48cc7cb). Real asset then, debt later.
- **Crisp falsifiable success criteria:** the gap audit's binary gates with explicit FAIL conditions ("any miss = FAIL") and an all-green exit condition — sharper than much later prose-form criteria. (2026-04-05-success-criteria-gap-audit.md)
- **Workspace→repo resolution order** cleanly separated shipped product from learned state; survived into the Maro packaging era. (9e3d46e)

## Cons vs today's architecture

- **No test isolation for 11 days** — 62 test files wrote to the real workspace; pytest contaminated production calibration.jsonl. *(resolved-since: conftest autouse fixture 2026-04-12, BACKLOG_DONE.md:2673; workspace-pin unified 2026-07-03)*
- **Learning flywheel inert while machinery grew** — times_applied always 0, lessons never retrieved at inference, evolver fired on count not trend, cost not computed. *(resolved-since: knowledge injection session 16; real cost ede617d; verify→learn arc closed 2026-07-14)*
- **Unverified "done" claims** — ghost features, loop-end-only wiring, stale CLAUDE.md phase table ("Status Corrections (CLAUDE.md is stale)", 2026-04-04-plan-next-phase.md). *(resolved-since: phase audit + later done≠successful split)*
- **Decision journal write side never wired** — record_decision() had no production caller then and still has none (only knowledge_lens.py:772 + test callers); ~/.maro/workspace/memory/decisions.jsonl does not exist, yet recall.py:643-644 still reads it. *(still-present)*
- **Promotion cycle yield near zero** — after 3.5 months: 4 standing rules, 0 hypotheses. Mechanism survives (knowledge_web.py:339,700) but never became a real learning channel. *(still-present)*
- **agent_loop.py monolith** — 4,261 lines, 15+ silent `except Exception: pass` sites in the first 1,000 lines, LoopPhase as unenforced strings. *(resolved-since: exception sweep d4d932f in-era; facade split into 9 loop_*.py modules 2026-07-03, 242c4db)*
- **Steal-flood dead weight** — bulk-shipped machinery with no callers (TradingAgents debate pass, polymarket backtests, genetic-programming apparatus) cost a ~9,600-line deletion (a278575, 2026-07-02) and multiple audit rounds. *(resolved-since)*
- **Subprocess lifecycle blindness** — a claude -p child spawned 160+ pytest workers on the wrong codebase, 12GB RAM; no process-group management. *(resolved-since: _run_subprocess_safe with start_new_session + os.killpg, session 19)*

## What we believed then

- **"Standing rules will compound"** — 2-confirmation promotion + unconditional injection would build a self-improving planner. Reality: 4 rules, 0 hypotheses after 3.5 months; real learning flowed through lesson injection, GOAL_BRAIN, and the July memory-module rebuild.
- **"The decision journal will be auto-written and searched"** — the write side never got one production caller; the human-curated GOAL_BRAIN.md Decisions section became the actual journal (SF-13 routes decree-class statements there manually).
- **"Token shorthand will cut costs"** — the project's own A/B declined it on variance/unreliable adoption (1db8878; BACKLOG_DONE.md:2565).
- **"Phases marked DONE are done"** — the 04-13 audit found ghosts and loop-end-only wiring among 8 "done" phases; thereafter "done" required invocation-path evidence.
- **"An LLM self-audit produces trustworthy findings"** — 6 of 10 hallucinated with plausible line numbers.
- **"≤$50/month, ≤$0.25/mission are the thresholds that matter"** — superseded by the budget-posture decree (highest tier, EFFORT language not dollars).
- **"More steals faster = compounding capability"** — breadth outran verification; later eras spent heavily auditing, wiring, or deleting the bulk.
- **"agent_loop's decomposition happened in this period"** — belief persisted until 2026-07-02; GOAL_BRAIN.md:578-590 records the remembered split never occurred. The real completed split of this era was memory.py.

## Lost good ideas

- **Bi-temporal knowledge** — every fact carries t_valid and t_learned: "The BTC lag claim was 'true' (believed) from 2026-04-01 to 2026-04-02 when it was invalidated. That trajectory is knowledge." (docs/knowledge-layer/01_ARCHITECTURE.md). Lost: doc stayed design-concept; fields never implemented. **Worth reviving: yes** — directly feeds the active graph-memory direction and gives the hallucination-reduction arc claim provenance.
- **Queryable git** — "the commit history IS the ledger": structured commit messages, parseable diffs, blame-as-provenance. Lost: dev-recall/FTS realized the read side only. **Worth reviving: partially** — this era-archaeology exercise is the proven use case; formalizing the loose commit-message schema is a cheap win.
- **Decision schema with alternatives + trade_offs** — the Decision dataclass was the right machine-readable ADR shape. Lost: no write path; empty store injected. **Worth reviving: yes, as auto-capture** — piping SF-13 decree-class statements through record_decision() resurrects the searchable journal for ~zero new design.
- **`# @lat:` source-code backlinks** — code files carrying edges into the concept graph, verified by `lat check`. Lost: discipline faded; fabricated lat.md content had to be deleted from the injection corpus 2026-07-09 (c3b6cad). **Worth reviving: only with staleness enforcement** — indexes rot invisibly without sources-on-disk checks; the code→concept edge idea itself remains sound.
- **Binary PASS/FAIL gates per success dimension** with explicit FAIL conditions and an all-green exit. Lost: filed as a one-off record, never re-run as a recurring instrument. **Worth reviving: yes** — current validator-ROI/quality-gate work is rebuilding exactly this; the 2026-04-05 table is a ready-made template.
- **`await:<kind>` zero-LLM-token event steps** — DAG steps blocking on a typed EventRouter event, completing with the payload, 0 tokens (CHANGELOG 1.10.24). Partially lost: EventRouter survives in src/interrupt.py, but the current thread architecture rebuilt human-waits via ConversationChannel.ask() (1.19.0). **Worth a look** when threads gain long-lived waits: a zero-token, checkpoint-safe wait beats a blocked LLM loop.

## Sources

- maro-orchestration git log 2026-04-01..2026-04-14 (263 commits; boundary/flood commits inspected individually); git show 93da18b:CLAUDE.md, :MILESTONES.md, :src/agent_loop.py, ls-tree + module line counts
- docs/history/CHANGELOG.md [1.6.0]–[1.19.0]; docs/history/ROADMAP_ARCHIVE.md (Phases 15–22, 48, 50–62)
- docs/history/2026-04-13-phase-audit.md; 2026-04-05-success-criteria-gap-audit.md; 2026-04-05-steal-list.md; 2026-04-04-plan-next-phase.md; 2026-04-01-prediction-markets-research-summary.md
- docs/knowledge-layer/01_ARCHITECTURE.md (dated via git log --follow); BACKLOG_DONE.md:1935, :2365, :2556–2684; GOAL_BRAIN.md:560–600
- Memory archive: project_orchestration_phases.md, project_session17_summary.md, project_session18_testing.md, project_session20_regression.md, project_session20_5.md, project_bugs_found.md
- dev-recall FTS queries; current-tree verification greps (record_decision, observe_pattern, EventRouter, lat.md, arch skills, ~/.maro/workspace/memory counts); commit-date verification for Phases 16–21 (all 2026-03-25)
- Independent verification pass 2026-07-21: ~24 claim clusters checked against git/docs; corrections applied here — compact-A/B sign convention (median was 9.3% *reduction*), 27 not 26 releases in 1.10.x, hash 857dc99 nonexistent (CHANGELOG 1.10.4 content real), escalation-consumer pair is a9bf68b/9119505
