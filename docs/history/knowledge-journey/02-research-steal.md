---
status: history
---

# Research & steal: external prior art becomes the method

*2026-03-17 – 2026-03-31*

Boundary commits: `2b1ecb1` (2026-03-19, durable run lifecycle control plane) → `9968ded` (2026-03-31 23:37, population_match in adversarial output schema). Representative: `e11cb5a` (2026-03-31, Phase 47: VerificationAgent + btw: mode + role-specific tool visibility + SOURCES.md).

## Architecture as it was

Repo still named `openclaw-orchestration`; the product was **Poe**, an autonomous Telegram-reachable concierge. `docs/ARCHITECTURE.md@e11cb5a` inventories "48 files, ~29K LOC" — but that inventory was written 03-28 (`ff19d85`) and was stale by era end: the actual `e11cb5a` tree has 60 `.py` entries in `src/`.

Request lifecycle: `telegram_listener.py` (long-poll, ack-then-edit UX) → `handle.py` → `intent.py` (NOW vs AGENDA) → `poe.py` "CEO layer". AGENDA goals ran `agent_loop.py` (1,925 lines at `e11cb5a`): decompose → execute → retry → recover, with `planner.py` multi-plan decomposition, `step_exec.py` behind `llm.py` adapters (subprocess `claude -p` / Anthropic SDK / OpenRouter / OpenAI / Codex), and `director.py`/`workers.py` for complex directives. Background daemons: `heartbeat.py` 60s loop driving `sheriff.py` stuck detection, `mission.py` multi-day drains, `evolver.py` every 10 ticks, `inspector.py` every 20.

Learning stack already elaborate: `memory.py` tiered lessons (TF-IDF upgraded to BM25+RRF `hybrid_search.py` on the last day), `skills.py` with EMA scoring + circuit breakers, `rules.py` graduated zero-cost paths, `attribution.py`, `introspect.py` multi-lens diagnosis, `graduation.py`. Safety: `constraint.py` HITL tiers, `security.py` injection scan, `sandbox.py` for skill code only.

What makes this the "research & steal" era: nearly every module traces to a named external source, tracked in first-class artifacts — `STEAL_LIST.md` (NOW/NEXT/LATER queue, per-item source + landing file + S/M/L effort) and `SOURCES.md` ("Canonical log of every external source we've drawn from"). Roadmap phases were derived directly from research docs. Magic-prefix language in `handle.py` at era end: `effort:`, `mode:thin`, `ultraplan:`, `btw:`, `ralph:`/`verify` (`skeptic:` and `direct:` came just after — 04-04 and 04-01 respectively).

Velocity was extreme: Phases 1–8 all landed 2026-03-23 (CHANGELOG 1.0.0, headline says "1–7" but includes a Phase 8 section); 47 numbered phases in ~9 days; CHANGELOG 1.2.0 (03-31) claims ~1,290 tests.

## Discoveries & aha moments

### Stop inventing, start mining production systems (2026-03-24)

The roadmap stopped being self-generated and started being derived from documented production architectures. Factory AI's droids/Missions/System-Notifications research was captured with an explicit "Poe parallel" per pattern, and Phases 10–13 were implemented within 13 hours of the doc landing. Research-doc-then-build became the standing method.

- `8592a8f` (03-24 12:56) Factory AI research doc; `9768cd1` (03-24) Phases 9–13 roadmap
- `f95ecf7`..`1774df6` (03-25 00:45–01:16) Phases 10–13 shipped
- `e11cb5a:docs/FACTORY_AI_RESEARCH.md` ("This document exists to inform Phases 10-13")

### Externalized skepticism beats self-critique (2026-03-25)

From Anthropic's three-agent app builder: models "confidently praise their own mediocre output," so evaluation must be a *separate agent tuned toward skepticism*, negotiating sprint contracts before work starts. This GAN framing became Phase 19 and is the intellectual ancestor of every later adversarial-review mechanism.

- `1c06a0a` (03-25 13:22) research doc + Phase 19 roadmap; `af7e973` (03-25 14:06) Phase 19: sprint contracts, worker boot protocol, GAN enforcement
- `e11cb5a:docs/ANTHROPIC_HARNESS_RESEARCH.md` §1 "GAN-inspired"

### Skills as external memory with a test gate — Memento-Skills (2026-03-25)

The Memento-Skills paper (arXiv:2603.18743; SRDP: state = environment + evolving skill library) reframed learning as file-level mutation with skill-level credit assignment, guarded by an auto-generated unit-test gate before write-back. Phase 14 shipped the same morning the research doc landed.

- `f0eace0` (03-25 07:25) research doc; `52d7c81` (03-25 07:42) Phase 14: skill evolution, failure attribution, unit-test gate

### Token burn is an engineering surface: 789k → 67k, 91% (2026-03-27)

A 789k-token research run traced not to the model but to plumbing: sub-agents fetching raw HTML via WebFetch plus a context-carry bug. Pre-fetching clean markdown (Jina Reader), banning sub-agent WebFetch/WebSearch, and a token-efficiency prompt cut it 91%. Context hygiene became first-class from here on.

- `748c84a` (03-27 01:02) context-carry fix + token-efficiency; `fafa59b` (03-26 23:57), `85fdd6b` Jina Reader + fetch ban
- `docs/history/CHANGELOG.md` [1.1.0]

### The auditor hallucinates too — verify the verifier (2026-03-28)

An automated phase-completion audit flagged many "CRITICAL" broken features that manual fact-checking proved were wired and working, while real issues (circuit breaker never consulted at skill-matching time, silent JSONL corruption swallowing) hid in the noise. Earliest written instance of the standing verify-before-fix rule (~30–50% of adversarial findings are hallucinated).

- `d2881eb` (03-28 23:12) audit; `0961a22` (03-29) orchestrated re-audit: 24 VERIFIED, 6 PARTIAL
- `e11cb5a:docs/PHASE_AUDIT.md` ('The automated audit found many "CRITICAL" issues that turned out to be false')

### Hallucinated evidence is a data-plumbing gap, not model dishonesty (2026-03-28)

Accuracy audit found ~60% fabricated evidence in step outputs — steps had no structured way to reference prior steps' findings, so the executor invented plausible file names. Fix was infrastructure (per-loop scratchpad, write-after-step / read-before-step, capped inline summaries), not prompting. Seed of the positive-evidence principle.

- `2170f23` (03-28 17:51) loop scratchpad; `e5a34f6` per-step file verification for hallucinated `.py` references
- `e11cb5a:docs/LOOP_SCRATCHPAD.md` ("Accuracy audit showed ~60% fabricated evidence")

### Bitter Lesson audit: classify every module as "what" vs "how" (2026-03-30)

Via Grok feedback + Miessler's Bitter Lesson Engineering + Zakin's Mode 1/2/3 taxonomy, the project audited itself: CEO→Director→Worker→Inspector hierarchy, persona auto-routing, and the sheriff flagged as "how" (candidate cruft as models improve); vision/outcomes/budgets/observability are "what" (keep). Concrete outputs: outcome-first goal rewriting, data-pipeline enforcement, the `user/` context folder, Skip-Director experiment.

- `99f5a67` (03-30 18:00); `e11cb5a:docs/BITTER_LESSON_ANALYSIS.md` ("What vs How Audit"); CHANGELOG [1.2.0]

### Empirical scaffolding triage: adversarial review is load-bearing, persona routing is not (2026-03-31)

Instead of arguing the Bitter Lesson, the era tested it: `factory_minimal.py` (single LLM call) and `factory_thin.py` (decompose→execute→adversarial-review→compile) benchmarked against Mode 2 on real goals with real dollar costs. Verdict: adversarial review catches real errors for +$0.05 (merge it); Ralph verify useful but +30% wall time (flag-only); persona routing, lesson injection, multi-plan comparison removed with no quality loss; scaffolding is ~50% of elapsed time; Haiku's verbosity erases thin-mode economics on complex goals. Architecture decisions by benchmark, not taste — ancestor of every later A/B bakeoff.

- `a75d096` (03-31 00:38) factory branch; `055ee95` merge; `30a01dd` findings
- `e11cb5a:docs/FACTORY_MODE_FINDINGS.md` ("Adversarial review is load-bearing. Merge it.")

### The steal blitz: prior art becomes a tracked artifact with provenance (2026-03-29 – 03-31)

`STEAL_LIST.md` (03-29) and `SOURCES.md` (03-31) made external inspiration first-class and auditable — every stolen pattern names its source repo, what was taken, and the exact file it landed in. In ~48 hours: Ralph verify (oh-my-claudecode), BM25+RRF hybrid retrieval + error-nodes-as-memory (Mimir), cron persistence (724-office), SlowUpdateScheduler (MetaClaw), FileTaskStore (ClawTeam), effort:/ultraplan:/bughunter (claw-code), role-specific tool visibility + event-driven reminders (systematicls), commitment-forced verdicts + pre-plan challenger (TradingAgents dogfood run).

- `0719edb` STEAL_LIST; `64d3d3b`, `4e7f5da`, `d6d0c0e`, `400c9f3`, `ddddf0d`, `2179645`, `b0f5b25`
- `e11cb5a:SOURCES.md`, `e11cb5a:STEAL_LIST.md`

## Pros vs today's architecture

- **Readable in a sitting.** ~60 src files, ~29–30K LOC, with a one-page module inventory and dependency graph close to the tree (`e11cb5a:docs/ARCHITECTURE.md`; `ff19d85`). Today src/ is 100+ modules with facades and ~4.6k tests — more capable, far harder to hold in one head.
- **Research-to-code latency in hours.** Factory research 03-24 12:56 → Phases 10–13 by 03-25 01:16; Memento research 03-25 07:25 → Phase 14 at 07:42 the same morning. No pipeline, no backlog aging.
- **Provenance discipline.** SOURCES.md logged every source with "what we took / where it landed" down to function names, including warnings (MetaClaw's hardcoded API key). Today's steal-list memory + link-farm carry the queue but the landed-where mapping stopped being one canonical doc.
- **Cheap empirical architecture decisions.** The factory branch answered "which scaffolding is load-bearing?" for a few dollars total with a same-day written merge/shelve decision (`docs/history/2026-03-31-factory-mode-findings.md`, $0.035–$1.40/run tables).
- **Honest prioritization.** STEAL_LIST's NOW/NEXT/LATER + S/M/L effort + explicit deferral reasons; several LATER calls (graph edges for lessons, three-layer memory compression) proved correct months later.

## Cons vs today's architecture

- **Phase-complete claims outran verification** (resolved-since). 47 phases in ~9 days; the 03-28 audit found an unwired circuit breaker (C1), silent JSONL corruption swallowing (C2), and "recovery" that was just heartbeat restart (H5). Resolved via done≠successful split, closure verdicts, claim_probe.py, ralph-verify in closure; numbered phases abandoned for MILESTONES/GOAL_BRAIN threads.
- **Cost control was opt-in** (resolved-since). `cost_budget` defaulted to None (audit H1); no test proved it stops a loop (H4). Today: per-step cost recording, budget.transparency_usd, spend-as-EFFORT UX.
- **Quality comparison was subjective** (still-present). FACTORY_MODE_FINDINGS admits the minimal-vs-thin gap was "hard to quantify rigorously without a scoring rubric" / "feels better". Phase 49's rigorous comparison was shelved ("*(TODO — shelved until Phase 46+ ships)*", ROADMAP_ARCHIVE), its binary merge-or-discard call never made; `src/factory_minimal.py`/`factory_thin.py` and `mode:thin` (handle.py:1258) still ride at current tip on the informal result.
- **Personal health context committed to public history** (resolved-since, by acceptance). `user/` folder (`99f5a67`, 03-30) reachable in public history even after tip clean 2026-07-09; Jeremy RESOLVED-ACCEPT 2026-07-16 (GOAL_BRAIN ~3106).
- **Token metrics were cache-blind** (resolved-since). Raw token totals (789k runs, Haiku 1,512K factory run) treated as the cost signal without prompt-cache accounting. 2026-06-21 verdict: "the token explosion was a cache-blind metric artifact; caching already absorbs the re-reads" (BACKLOG_DONE).
- **~60% fabricated evidence in step outputs** (resolved-since). Scratchpad fix landed in-era; the broader positive-evidence machinery (claim probes, per-step file verification as a gate) came months later.
- **Everything ran unfenced** (resolved-since). No workspace write fence, no scavenge detection; sandbox.py covered skill code only. write_fence shipped+enabled 2026-07-04.

## What we believed then

- **"Phase COMPLETE" meant working features.** The era's own 03-28 audit began the correction; numbered phases were later abandoned entirely for MILESTONES + GOAL_BRAIN threads.
- **Raw token totals measured cost.** The 2026-06-21 re-measurement ruled the per-step "explosion" a cache-blind artifact. The WebFetch-HTML waste was real; the token-count-as-alarm framing was wrong.
- **"Lesson injection is not load-bearing"** (FACTORY_MODE_FINDINGS §4). The Verify→Learn arc later closed V1–V5 with navigator.lesson_inject ENABLED under A/B watch — it returned as a deliberate, measured mechanism.
- **An automated auditor could verify the system.** Its "CRITICAL" findings were largely false; only manual fact-checking separated signal from noise. Now the standing rule: ~30–50% of adversarial findings are hallucinated; verify every claim first.
- **OpenClaw gateway integration was strategic infrastructure.** Phase 15 added `src/gateway.py` (ws://127.0.0.1:18789; the sheriff's gateway health check actually predates it, shipping with the Phase 4 sheriff, `930a1e1`). Phase 21 decoupled it within the same era; OpenClaw shut down on this box 2026-07-16.
- **A thin prompt-driven "factory mode" could replace the scaffolding wholesale.** The era's own benchmark walked it back (Haiku verbosity erased the cost edge; adversarial review was the load-bearing part), and the deciding experiment (Phase 49) never ran — the orchestration layer remained the product.

## Lost good ideas

- **SOURCES.md as a maintained canonical provenance log.** Frozen into `docs/history/2026-03-31-sources.md` in the 2026-07-04 docs refactor; the steal queue lives on (memory + link-farm) but landed-where mapping stopped after early April. *Worth reviving: yes, cheaply — fold per-steal "what we took / where it landed" lines into the link-farm README (already the decreed first stop for OSS lookup). Positive-evidence applied to our own intellectual debts.*
- **Runtime awareness (Factory AI §2f): tell the model how long each tool takes.** Captured in FACTORY_AI_RESEARCH.md, never implemented; per-step adaptive timeout (`b0a030d`) solved the harness side but the model was never told the timings. *Worth reviving: plausibly — per-step wall time and cost are recorded anyway; injecting observed tool latencies into worker context is a small, measurable steal validated in Factory's Terminal-Bench work.*
- **Phase 49's decision gate: benchmark factory-thin vs Mode 2 with a rubric, then merge or delete.** Shelved, superseded in spirit by eval-driven harness hill-climbing (BACKLOG.md:2381); the binary call never made, so factory files persist unadjudicated. *Worth reviving: the decision gate specifically — either mode:thin earns its keep in a modern A/B (the bakeoff machinery exists now) or the files go. Carrying an unadjudicated variant contradicts the era's own best insight.*
- **Graph edges for lessons (Mimir steal, LATER tier): depends_on / supersedes / caused_by.** Correctly deferred; later knowledge_edges.jsonl accumulated 2,124 written edges with zero readers — but the 2026-07-13 trace (GOAL_BRAIN Decisions) found all 2,124 were link-farm-import noise touching zero real orchestration nodes, so "query side never did" was the right call for the wrong-sounding reason. *Worth reviving: the minimal three-relation vocabulary remains the sanctioned longer-term direction — starting from zero real edges, not the 2,124.*
- **The research-doc-with-Poe-parallels format:** every external deep-dive annotated inline with "Poe parallel: we have X, missing Y, Phase Z fixes it" — reading as a diff against our own architecture. Faded after early April into steal-list one-liners. *Worth reviving: yes for major sources — the Factory doc converted one afternoon of reading into four shipped phases; highest research-to-code conversion rate in project history.*

## Sources

- git log 2026-03-18..2026-04-01 (225 commits), /home/clawd/claude/maro-orchestration
- `e11cb5a`: STEAL_LIST.md, SOURCES.md, docs/ARCHITECTURE.md, docs/FACTORY_AI_RESEARCH.md, docs/ANTHROPIC_HARNESS_RESEARCH.md, docs/MEMENTO_SKILLS_RESEARCH.md, docs/BITTER_LESSON_ANALYSIS.md, docs/FACTORY_MODE_FINDINGS.md, docs/PHASE_AUDIT.md, docs/LOOP_SCRATCHPAD.md, MAINLINE_PLAN.md, VISION.md
- docs/history/ROADMAP_ARCHIVE.md (Phases 7–21, 47, 49), docs/history/CHANGELOG.md ([1.0.0], [1.1.0], [1.2.0])
- BACKLOG_DONE.md (factory/steal/Grok, cache-aware metric entry), BACKLOG.md:2381, GOAL_BRAIN.md (~3106, Purgatorio #3), MILESTONES.md, current src/ + handle.py
- memory: project_orchestration_phases.md (archive), feedback_verify_before_fix.md, project_retrieval_graph_memory_direction.md, reference_link_farm.md
- Verification pass 2026-07-21: 20 load-bearing claims checked; 17 confirmed, 3 corrected (Phase 19 commit dates 03-25 not 03-26; agent_loop.py 1,925 lines at e11cb5a, 1,575 was the stale 03-28 inventory figure; skeptic:/direct: prefixes post-date the era)
