---
status: history
---

# Session sprint: core-loop machinery and the PM/dev experiment

*2026-04-13 – 2026-04-16*

122 commits in ~3 days (window `--since=2026-04-14 --until=2026-04-17`; count drifts to 123 depending on time-of-day git resolves the bare `--until`), sessions ~20.5–34, first 92c6633 (04-14 02:07) → last 60ec6ac (04-17 00:40), representative end-of-era tree e5da6cb (04-16 14:49, "synthesize_skill 3-gate pre-promotion check"). The era that produced "compiles is not works," the rectangle/inversion model, Phase 62–65, the closure gate, and the inference-not-prompting standing rule.

## Architecture as it was

At e5da6cb the system was still Poe end-to-end (`~/.poe/workspace`, `poe-*` CLI, `src/poe.py`): 424 files, ~4,341 passing tests.

- **Front door:** `src/handle.py` (1,408 lines) — a linear judgment pipeline readable top-to-bottom: NOW/AGENDA classify → Bitter-Lesson imperative-goal rewrite (:548) → clarity check with optional `channel.ask()` (:561-591) → Phase 65 scope generation gated by `scope_generation` config (:753-804, `scope_ab_skip` control arm at :763-764) → `run_agent_loop` → director-requested restart (depth ≤3) → the era-new closure gate: `director.verify_goal_completion`, whose `complete=False` (confidence ≥0.6, ≥1 check run) injected the gap list as `== Closure gap context ==` ancestry and re-ran the loop (:846-908) → quality-gate skeptic review with tier escalation.
- **Core loop:** `src/agent_loop.py` monolith, 4,973 lines. This era converted its ~50-field `LoopContext` into `LoopStateMachine` — seven phases INIT→DECOMPOSE→PRE_FLIGHT→(PARALLEL)→PREPARE→EXECUTE→FINALIZE with an `_ALLOWED` transition dict and `InvalidTransitionError` (agent_loop.py:179-330; 424725b, dd553fc). The `phase` field had existed but was literally never set anywhere until session 21. Context assembly injected tiered lessons, standing rules, playbook, graveyard, knowledge nodes, skills, repo stack detection, and the new AST call graph (`codebase_graph.py`, centrality = 0.7×in_degree + 0.3×normalized size, 510f9b8).
- **Models:** `llm.py` adapter zoo (ClaudeSubprocess, CodexCLI, AnthropicSDK, OpenRouter, OpenAI) wrapped by the era-new `FailoverAdapter` in `model.backend_order` priority — fail over on 401/402/403/5xx, propagate 400-class (llm.py:255+, 19faca4).
- **Director supervision (new):** Phase 63 closure check — executable inversion-driven probes run mechanically with real exit codes, LLM interprets outcomes only (`_CLOSURE_PLAN_SYSTEM` at director.py:1137); fail-open `complete=True` on any error (director.py:1222). Phase 64 adaptive execution — `director_evaluate` with continue/adjust/replan/restart/escalate on stuck-streak/verify-failure/step-count triggers, replan budget ceiling 2, `adaptive_execution` default OFF (9eb8630, e2d6740, 3bb095c).
- **Self-improvement ring, wired but partly dormant:** `evolver.py` (auto-revert-on-verify-failure, 3-gate `synthesize_skill`), `inspector.py` friction scans, K4 knowledge write path (`knowledge_bridge.outcome_to_knowledge`, cc7cabc), 17-regex `injection_guard.py`, and `heartbeat.py` deployed as systemd units (`poe-heartbeat.service`, `poe-observe.service` on port 80 via CAP_NET_BIND_SERVICE, d0c7435) — though heartbeat had been stopped since the Apr 7–9 token burn.
- **Interface:** Phase 62 `ConversationChannel` (`conversation.py`); the observe.py dashboard as "first channel peer; Telegram/Slack/openclaw are future peers at same level" (CHANGELOG 1.19.0).
- **Dev tooling (explicitly NOT runtime):** `correspondence.py` FTS5/BM25 dev-recall over 110 files / 1,181 chunks.
- **Live-fire sibling:** `orchestrator-test-recipes` (FastAPI recipe site) — the PM/dev experiment: an orchestrator-driven PM agent filed GitHub issues #16–#43, a dev agent implemented and closed them, rounds 2–9 in 3 days (19 commits).

## Discoveries & aha moments

### Compiles is not works — verify against reality, not LLM judgment (04-16)
The slycrel-go regression ran to status=done because `go build ./...` exited 0 — the server "passed" because it compiled and the loop declared it done; nobody ran a browser against it. The system was "very good at producing plausible things and very bad at knowing whether they work." Split verification into LLM judgment vs ground-truth behavioral feedback and re-centered the project on the latter. Direct consequence: the closure verdict — previously computed and then DISCARDED — was wired to gate the loop and trigger gap-context restarts.
Evidence: docs/conversations/2026-04-16-constraint-orchestration.md; 19bad34 "closure check gates the loop, prompt mandates behavioral verification"; handle.py:846-908 at e5da6cb; docs/CONSTRAINT_ORCHESTRATION_REVIEW.md (Scope Correction).

### The rectangle: expert judgment = deliberate narrowing of the solution space, via inversion (04-16)
Jeremy's whiteboard model — a rectangle sliced by 4-5 constraint lines until only the goal-space remains. The planner had been filling an UNBOUNDED space with steps, conflating "what are we NOT doing" (refinement, needs perspective) with "how do we do it" (implementation, needs competence). Munger-style inversion is the constraint generator: failure modes are structurally easier to enumerate than success conditions; "the path to success isn't designed — it's what remains after you've systematically eliminated the failure modes." Jeremy: "it's essentially 'good judgement' systematized." Shipped same-day as the Phase 65 MVE (`src/scope.py`).
Evidence: docs/conversations/2026-04-16-constraint-orchestration.md:190; e1f00c3 (design doc); 1b520eb (Phase 65 MVE); src/scope.py `_SCOPE_SYSTEM` at e5da6cb.

### "Have we built a really fancy model-trainer?" — self-improvement is expensive in-context RLHF (04-16)
Jeremy: "So... have we built a really fancy model-trainer for an LLM, but crazy inefficient...?" — "using tokens to thrash around to get training data." The honest answer conceded it: lessons cost hundreds of thousands of tokens, store as natural language, retrieve imperfectly — "prompt engineering with extra steps and a persistence layer." Durable value reframed as the reliable autonomous execution environment; "self-improvement" demoted from mechanism to aspiration. Earliest written record of the skepticism that later closed the Verify→Learn arc.
Evidence: docs/conversations/2026-04-16-constraint-orchestration.md:52-54 (REFRAME section).

### Inference, not prompting — the taxonomy prompt-patch rejection (04-16)
First closure-prompt fix (8255b52) hardcoded a four-category taxonomy ("if service goal, demand behavioral check"). Jeremy pushed back: that's prompt-patching, the exact class of fix the project exists to replace — "the foundation is intentionally vague and fuzzy prompting; the payoff is inference, memory, inversion, and perspective rotation." Replaced (74cd090) with inversion framing: closure probes the failure modes scope already generated, each check labeled with its failure_mode. Became a standing rule (feedback_inference_not_prompting) still cited today; linked Phase 65 and the closure gate as "the two halves of good judgment." (Note: 19bad34/8255b52 and 74cd090/587d8a0 are duplicate commit objects — identical subject+timestamp, distinct hashes.)
Evidence: docs/history/ROADMAP_ARCHIVE.md:1472; 74cd090; aa985cf "MILESTONES — correct the closure prompt entry (inversion, not taxonomy)".

### Verify-before-fix: adversarial reviewers hallucinate findings (04-14)
Session-20 blind adversarial self-review: 14 findings, and checking each against code before fixing revealed 2 flat hallucinations (3.4 "Director bypassed" — default was False; 3.14 "persona auto-selection missing" — existed at persona.py:793). "Would have wasted hours building the wrong thing." Hardened into the standing verify-each-finding discipline (~30-50% hallucination rate per later memory) and, by era end, the runtime-probe-bias / review-grounding thread.
Evidence: memory/archive/project_session20_5.md; ROADMAP_ARCHIVE.md:1664 "Hallucinated findings (verified, no fix needed)"; 60ec6ac.

### Same-day independent review as institution — and the "scope" rename (04-16)
The Phase 65 design got a fresh-eyes review by an independent agent with no conversation context THE SAME DAY. It caught: the design "improves the planning phase of a system whose biggest defect is in the verification phase"; a name collision with `src/constraint.py`; an unreachable human gate on the autonomous path; and a much smaller minimum experiment. Jeremy's call: rename the concept to "scope" ("captures both what IS and what IS NOT in the bounded space"). Design + review + verbatim conversation log preserved as three separate documents "for decision-trail integrity."
Evidence: docs/CONSTRAINT_ORCHESTRATION_REVIEW.md; 68b8192 (independent audit); BACKLOG_DONE.md:2588; 717ea4d.

### Ship the MVE, mark every punted decision greppably (04-16)
Phase 65 shipped as a single-LLM-call minimum viable experiment with SEVEN explicitly deferred design elements — triad personas, human gate, enforcement, lifecycle, retrieval-based injection, cross-goal memory, A/B-skip — each emitting a runtime `[scope-deferred]` log marker so the deferral surface stays searchable. A/B instrumentation (scope_ab_skip control arm) built into the feature at birth. (ResolvedIntent v0 is NOT this era — it shipped 2026-04-23, 9884adb.)
Evidence: src/scope.py docstring at e5da6cb; handle.py:788-802; docs/PHASE_65_IMPLEMENTATION_PLAN.md.

### Dev tooling is not the system — and BM25 beat embeddings by 42 minutes (04-16)
`correspondence.py` was framed with an explicit boundary: "it's easy to conflate how we build the system with what we're building" — dev-recall got no poe- prefix and no runtime imports. Same afternoon, the initial sqlite-vec embeddings implementation (55573fc, 13:29) was ripped out for SQLite FTS5/BM25 (caa5061, 14:11): BM25 against the author's own terminology needs no API keys and no external calls. Still the live dev-recall engine 3 months later.
Evidence: 55573fc → caa5061; src/correspondence.py docstring at e5da6cb; MILESTONES.md session 34.

## Pros vs today's architecture

- **Decision-trail preservation at its richest:** verbatim conversation log + design doc + independent same-day review + `[scope-deferred]` runtime markers, cross-referenced. Today's GOAL_BRAIN compresses decisions into dated lines; the era kept the thinking that produced them ("the synthesis loses the thinking that produced it"). (docs/conversations/2026-04-16-constraint-orchestration.md; markers still grep-able in current src/scope.py, 6 hits)
- **The whole judgment pipeline readable top-to-bottom in one file:** handle.py's rewrite→clarity→scope→loop→restart→closure→quality-gate sequence was a linear story at 1,408 lines. Current handle.py is 2,881 lines with closure moved to closure_verify.py — more correct, less legible.
- **Measurement-first feature design:** A/B control arms (scope_ab_skip), config kill switches (closure_restart, adaptive_execution default-off), eval train/test holdout against metric gaming (5e77899) — built INTO features at ship time; made the 04-22/23 scope A/B possible at all.
- **Cheap deterministic gates before LLM spend:** 3-gate synthesize_skill (fixed 10-goal off-target corpus, no new LLM calls), no-LLM friction scan, <5ms direct-file symbol index (CHANGELOG 1.14.0, 1.17.0). Same instinct as today's Tier-0 validation ladder, discovered independently.
- **Live-fire regression culture:** 5 parallel real-world goals including a blind adversarial self-review and the polymarket-edges ledger; PM/dev two-agent GitHub-issue workflow proven legible over 9 rounds (issues #16–#43).
- **Cadence with green-suite discipline:** 122 commits/~3 days/~15 sessions, full pytest green at every commit (caught the silently un-run integration suite), coverage floor ratcheted to 70% (719ee1d) — while designing Phase 65 from scratch.

## Cons vs today's architecture

- **agent_loop.py 4,973-line monolith** — the state machine was a veneer; the era's own review flagged it MODERATE and deferred. *resolved-since:* current agent_loop.py = 807 lines (loop_init/loop_execute/loop_finalize/loop_types extraction); LoopStateMachine survives at src/loop_types.py:481.
- **Closure fail-open and ephemeral:** `complete=True` on ANY error, verdicts never persisted, no cwd binding. Later produced recorded false positives (skipped closure logged as achieved) and 3/3 false negatives on the 2026-07-02 burn-in. *resolved-since:* 90b4d1b positive-evidence rule, ec4c1f3 cwd fix, verdict-persistence contract (docs/history/2026-07-14-verdict-persistence-contract.md), done≠successful split.
- **Daemon-shaped autonomy:** systemd units incl. port-80 dashboard; already burned once (Apr 7–9) and stopped, but the architecture assumed it'd come back. *resolved-since:* heartbeat health-only 04-22; Jeremy 2026-06-10 invariant "program, not operating system... no cron" (GOAL_BRAIN.md:93); deploy/systemd gone from current tree.
- **Two config systems** (in-repo user/CONFIG.md vs ~/.poe/config.yml) bit immediately — scope flags shipped reading the wrong one (33d74a0). *resolved-since:* defaults registry decree (docs/DEFAULTS.md + census tripwire).
- **Prompt-injection defense = 17 regex patterns** in injection_guard.py; fail-closed wiring good, detection still pattern-matching not structural isolation. *still-present.*
- **"Boil the ocean" completion standard** (6c1beb9, user/COMPLETION_STANDARD.md, injected into every AGENDA run) pushes maximal scope — in unresolved tension with the later cuts-first planning decree and fork-scope-overrun lesson. *still-present.*
- **Trusted self-reports:** status=done from the loop was the truth acted on, closure a single non-blocking advisory. *resolved-since:* claim_probe.py review-grounding, worker push guard, done≠successful split, "never trust delegated self-reports."
- **Phase 65's deferred majority never built:** persona triad, constraint lifecycle (revise/except/break), mid-execution violation detection, cross-goal scope retrieval — frozen by the 04-26 A/B pause. *still-present.*

## What we believed then

- **"Scope injection improves judgment quality."** The 04-22/23 A/B showed the reliable effect was structural plan compression (8 steps vs 15-40, ~$8 vs up to $41) — valuable, but not the believed mechanism; closure-quality stayed under-tested, `decomposition_too_broad` fired on every treat run (threshold miscalibrated), 04-26 delta-audit paused expansion.
- **"Fail-open closure is a safe non-fatal default."** Later poisoned the record both ways (skipped-closure false positive; 3/3 cwd false negatives). Closure was treated as advisory decoration; it became a data-integrity surface.
- **"Daemonized autonomy is the deployment model."** Reversed entirely by the 2026-06-10 program-not-OS invariant.
- **"Poe is the product."** Later settled: orchestration (Maro) is the product, substrate swappable, Poe an optional persona.
- **"The self-improvement ring will compound if we keep wiring it."** The era itself voiced the doubt ("fancy model-trainer... crazy inefficient") but kept building; the evolver stayed data-starved (heartbeat off), and Verify→Learn eventually closed with no successor arc.
- **"A big mocked test count is confidence."** 4,278→4,341 touted while the era's own review flagged "width not depth, all LLM calls mocked"; the 2026-07-14 test-suite-truth pass found documented full-suite commands silently dropping slow tests.
- **"Raw token totals are the alarming cost signal."** 1.28M/1.39M-token runs and the $41 control arm treated as raw-spend problems; later analysis showed the metric was cache-blind, and budget posture made effort, not dollars, the frame.
- **"Phase numbering and MILESTONES Next-Up queues are durable coordination."** The May-12 autonomous session synthesized from these stale queues and re-planned already-shipped work (ROADMAP_ARCHIVE:1248) — exactly what the "Docs are best-guess" invariant retired.

## Lost good ideas

- **Constraint-level outcomes as the learning signal** — "did the constraint set we chose produce a working system?" is structured, high-signal, the right granularity vs noisy step-level success/failure. Lost to the Phase 65 pause. *Worth reviving:* yes — maps cleanly onto today's verdict-persistence + run-card infrastructure, which now records exactly the outcomes needed to score a scope/plan pairing after the fact.
- **The exception/break distinction** — a broken constraint is either (a) right constraint, justified exception, or (b) wrong constraint, needs updating; both look like break-the-rule in the moment but produce different learning. Lifecycle was [scope-deferred] and never built. *Worth reviving:* yes, for standing-rules/skills maintenance — today's system accumulates rules with no principled path for distinguishing the two.
- **Human gate at constraint altitude** — "You don't want Jeremy approving individual steps. You want Jeremy at the constraint gate" — 4-5 strategic calls, not 40 implementation choices. Never got an approval UX; the review flagged the gate unreachable on the autonomous path. *Worth reviving:* strongly — it's the missing UX shape for the 2026-07 escalation-design decree (substrate go-between IS the surface): escalations should surface scope/constraint decisions, not step approvals.
- **Longitudinal self-modification impact measurement** — `scan_evolver_impact` compared stuck_rate before/after each EVOLVER_APPLIED event. Data-starved from birth (heartbeat off). *Worth reviving:* the concept — any surviving self-modifying path (e.g. navigator.lesson_inject, on A/B watch) deserves before/after outcome comparison rather than trust.
- **Eval train/test holdout** — oldest 70% of failure patterns generate suggestions, newest 30% held out, explicitly to prevent the system gaming its own eval metric. Went idle with the flywheel; never carried forward as a named principle. *Worth reviving:* yes, wherever the system grades its own improvements — cheap insurance against reward hacking.
- **The persona triad ablation** — before building PM/engineer/architect inversion, run the cheap test: do three personas draw DIFFERENT constraint lines, or near-identical lists at 3x cost? The review demanded this before investment; the pause killed it, so the zoom+rotation thesis remains empirically untested. *Worth reviving:* yes, precisely as specified — it's the minimum experiment named in the still-open Phase 65 memory, and budget posture now permits it.

## Sources

All claims verified 22/22 CONFIRMED against the repos (unusually clean; prior eras ran 30-78% error rates on first drafts).

- `git log --since=2026-04-14 --until=2026-04-17` in /home/clawd/claude/maro-orchestration (122 commits; all boundary/date hashes verified)
- `git show e5da6cb:` — CHANGELOG.md (1.12.0–1.19.0), src/agent_loop.py, src/handle.py, src/director.py, src/scope.py, src/llm.py, src/correspondence.py, MILESTONES.md, full tree (424 files, deploy/systemd present)
- docs/CONSTRAINT_ORCHESTRATION_DESIGN.md, _REVIEW.md, _AUDIT.md; docs/conversations/2026-04-16-constraint-orchestration.md (413 lines, Jeremy quotes verbatim); docs/PHASE_65_IMPLEMENTATION_PLAN.md
- docs/history/ROADMAP_ARCHIVE.md (sessions 17–38, ~:1240-1720); BACKLOG_DONE.md (:2524, :2588, :2621, :2682, :2854-2863); GOAL_BRAIN.md (invariants :88-120, burn-in decisions :460-515)
- Memory: archive/project_session20_regression.md, archive/project_session20_5.md, feedback_inference_not_prompting.md, feedback_verify_before_fix.md
- /home/clawd/claude/orchestrator-test-recipes (README + git log 04-13..04-18; issues #16–#43 verified via GitHub public API)
- Live dev-recall FTS query via `PYTHONPATH=src python3 -m correspondence query` — still working
- Current-tree checks: wc -l handle.py/agent_loop.py, src/loop_types.py:481, user/COMPLETION_STANDARD.md, src/injection_guard.py, `git log -S ResolvedIntent` (9884adb, 04-23 — out-of-era)
