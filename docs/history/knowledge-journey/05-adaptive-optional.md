---
status: history
---

# Adaptive execution and the optional-services turn

*2026-04-15 – 2026-04-30*

111 commits (last in-range 04-29); 82 by slycrel, 29 by the autonomous Poe identity (`agentic.poe@yahoo.com`) — the autonomous lane was live and committing while its own leash was being redesigned.

## Architecture as it was

At representative commit `7fa3078` (2026-04-26) the system was still **openclaw-orchestration**: one repo, 105 src modules with `agent_loop.py` at 5,070 lines (verified via `git ls-tree`/`git show` at the commit — the "48 modules / ~29K LOC / agent_loop 1,575 lines" inventory in `docs/history/2026-04-22-architecture.md` was already stale when archived, carried from the 03-28 census; the map's *structure* was right, its figures were not). Lifecycle: Telegram long-poll → `handle.py` → `intent.py` classifies NOW (single LLM call) vs AGENDA (`agent_loop.py`: decompose → execute → done|stuck), with `director.py` (608 lines) dispatching typed workers (research/build/ops/general) under persona prompts. One LLM adapter (`llm.py`, 903 lines), backend priority ANTHROPIC_API_KEY → `claude -p` subprocess (tool calls simulated via JSON-in-system-prompt) → OpenRouter → OpenAI; cheap/mid/power tiers. Tiered memory with Grok-style decay and the Stages 1–5 crystallization path; goal-ancestry chains injected into decompose and step execution.

What the era added: **Phase 63 closure** — `director.verify_goal_completion()` generates 2–5 executable shell checks, runs them mechanically, and signs off instead of the loop self-reporting (`426a119`); by `7fa3078` closure also read ResolvedIntent deliverables (`5edc248`), and minutes later the same session added precondition pre-flight via `shutil.which`/`Path.exists` (`bfd91dc` — 2m18s after `7fa3078`, so not in that tree). **Phase 64 adaptive execution** (`docs/ADAPTIVE_EXECUTION_DESIGN.md`): `director_evaluate(goal, EvaluationContext, trigger)` → five-action DirectorDecision (continue/adjust/replan/restart/escalate), convergence budget (ceiling 2 replans → forced escalate), triggers on stuck-streak / verify-failure / step-threshold — fully shipped, gated behind `adaptive_execution` flag, default OFF (`9eb8630`, `e2d6740`, `3bb095c`). **Phase 65's minimum experiment** as `src/scope.py` (ScopeSet before planning) plus ResolvedIntent v0 (`bacba86`). **Run transparency**: new `src/runs.py` — per-run dirs with source/build/artifact subtree, run-dir as write destination, captain's-log slice and restorable git bundle per paid run (`a99e2c5`, `22bb7ae`, `8f88962`, `dcf1bec`).

Mid-era the deployment story flipped: heartbeat went from implicit autonomy daemon (scheduler/task-store/mission/backlog drains + evolver + inspector + eval, resurrecting on reboot) to `heartbeat_loop(autonomy=False)` health-only, `--autonomy` an explicit opt-in, systemd units self-describing as optional templates (`dec8df2`, `27500a1`, `9e2e405`).

## Discoveries & aha moments

### Loop "done" != goal satisfied — closure belongs to the intent-holder (04-15)
The loop self-reports completion by running out of steps; the director is the only entity holding original intent, so it must sign off with real executed checks. Seeded today's done≠achieved distinction.
- `426a119` verbatim: "Key insight: loop \"done\" != goal satisfied. Director is the only entity holding original intent — it should sign off on completion, not the loop."
- `docs/ADAPTIVE_EXECUTION_DESIGN.md`: "\"Done\" means the loop ran out of steps, not that the goal was achieved."

### The plan is a hypothesis — director as persistent supervisor (04-15)
A plan generated in one pre-execution LLM call is "a hypothesis about what done looks like, written with zero information about what execution will discover." Phase 64 made the director a mid-run supervisor with one decision function and a convergence budget; default autonomous, escalate reserved for genuine decision points.
- `f50cf99` / design doc Problem section; `9eb8630` (64A), `e2d6740` (64B), `3bb095c` (64C).

### The optional-services turn: never-off becomes opt-in (04-22)
Live-box behavior exposed the coupling: heartbeat came back on reboot as an autonomy daemon, and a stale April-4 scheduled goal ("Monitor BTC price") got revived and installed cron AND systemd persistence unattended. The founding always-on-autonomous-host framing was explicitly demoted — orchestration became a tool you invoke, not a resident daemon. The philosophy shift of the era.
- `dec8df2` + MILESTONES session 35: heartbeat had "turned … from \"health substrate\" into an autonomy daemon by default"; "intentional in the original autonomous-host framing, but wrong for manual-use mode."
- `a58c2a7` BACKLOG BLOCKER: persistence-install guardrail, citing the BTC incident; `625cf35` (duplicate-dispatch cleanup).

### Scope injection structurally compresses plans — the one reliable A/B signal (04-22)
First paid A/B (3 treat + 3 control, slycrel-go goal): scope-injected runs planned 8 steps at ~$8 consistently; controls planned 15/37/40 steps at $8–$41 with two recovery-layer failures (SIGTERM step hang; 61-minute rate-limit backoff cascade). Compression real and consistent; closure-quality hypothesis under-tested. Long plans preferentially surface recovery bugs.
- `d59c6bd` (scope_ab_runner); MILESTONES session 36: "Primary signal: scope injection structurally compresses plan length (8 vs 15–40)"; `4e4bc39`.

### Verification theater exposed: checks that never ran counted as passed (04-22)
Run-00: closure ran behavioral checks with `go` missing from PATH; every command died at "go: command not found" yet closure returned complete=True, confidence=0.75, checks_passed=5/5. "The verification verdict was decoupled from whether anything was actually verified." In-era response: precondition pre-flight (`bfd91dc`), failed probes downgraded to inconclusive with no restart on inconclusive (`dc52ced`, `00ba932`), quality-gate `decision` field (`1961ea6`).
- `36ee7f0`; BACKLOG_DONE.md:2391.

### Driver and Watcher: naming the layers above orchestration (04-22)
Why does the orchestrator feel insufficient even when every mechanism works? Two absent layers: a **driver** holding the agenda, and a **watcher** — parallel meta-attention asking "has this traversal earned another cycle?" with interrupt rights. "Agenda at rest is a document; agenda in motion is an interrupt." Grounded in the godot font saga: the axis-shift came from a different signal source, not a different algorithm. Plus user-is-lazy-by-design: "The orchestration is the adult in the room" — asking the user to be more precise is a failure mode.
- `4f22b96` / `docs/DRIVER_AND_WATCHER.md` (246 lines, with transcript notes). Jeremy verbatim: "I don't want to build God, already got one. I want a mega chia pet that is super sci-fi."

### Plan-creation as its own step: ResolvedIntent v0, closure reads the same map (04-23)
The conflation `goal → dispatch substeps` in a single move was named the concrete bug — intent was goal → create plan → execute plan. ResolvedIntent made the plan a durable artifact with `Deliverable(name, description, preconditions)` parsed from the same single LLM call, written to `resolved_intent.md`, injected into planner ancestry; then closure was wired to verify against that same map ("the watcher half of docs/DRIVER_AND_WATCHER.md #4") — without it deliverables were advisory prompt text only.
- `bacba86`, `5edc248`; GOAL_BRAIN Decisions 2026-04-23 (ship v0, pause further Phase 65 work).

### The 1-shot-first inversion and the Thread Architecture reframe (04-26/27)
Jeremy, AFK: "Try this in one shot. If you can't do it without more planning, explain why and return instead — don't decompose." Decomposition becomes the escape hatch that must justify its cost; the scope/intent/constraint scaffolding aggregate named as "we trust the model less and less." Two days later Thread Architecture absorbed it: every interaction is a thread; goals/loops/missions are shapes a thread takes; every turn is navigator → work → navigator; Director reduced to escalation + kickoff; no upfront algorithm decision — the navigator chooses each turn. The Phase 65 delta-audit recommended pausing the phase until the frame resolved — scaffolding paused by a frame, a first.
- BACKLOG DISCUSS note at `cc5d4c6`; `2867e68` (docs/THREAD_ARCHITECTURE.md + 7-turn transcript, branch arch/thread-navigator); `127a634`; GOAL_BRAIN Decisions 2026-04-27 ("sketch only, no implementation").

### Run transparency: write where it lands (04-26)
Triggered by real data loss (treat-arm commits wiped by control's setup-reset). Jeremy reframed it as systemic: every paid run produces a source/build/artifact bundle, and — his correction — the run-dir is the write destination from the start, not a copy target. `runs.py` born here was later promoted by Thread Architecture as build-folder-as-thread-residence.
- MILESTONES session 37 (`7fa3078`): "source (plan + prompt artifacts) + build folder (interim objects + resources) for compiling a project"; `de3366b`, `a99e2c5`, `22bb7ae`, `8f88962`, `dcf1bec`.

### Inference, not prompt taxonomies — and dev-recall is born (04-16)
First closure-prompt rewrite encoded a four-category taxonomy ("if service goal, demand behavioral check"); Jeremy pushed back: prompt-patching is "the exact class of fix this project is designed to replace." Replaced with inversion framing — each check probes a named failure mode. Bitter principle recorded: "orchestration harnesses general LLM capability; it doesn't replicate it." Same session shipped `correspondence.py` (dev-facing retrieval; sqlite-vec tried and dropped for FTS5/BM25 the same day) — the tool this archaeology used was built in the era it mined.
- MILESTONES session 34; `587d8a0`, `aa985cf`; `55573fc` → `caa5061`.

## Pros vs today's architecture

- **One single-page map** — a per-module inventory and dependency graph in a single ARCHITECTURE.md. Its numbers were stale on arrival (see correction above: 105 modules on disk vs 48 claimed), which itself proves era 02's point about inventory rot — but the *form* (one page you can hold in your head) has no equivalent today: GOAL_BRAIN.md is a decision log, not a map.
- **Real dogfooded autonomy.** 29/111 commits authored by the autonomous Poe identity, draining a live backlog and committing tested code daily. Today's autonomous lane is far more gated (dispatch classes, push guards, PR-hold lanes).
- **Minimum-viable-experiment discipline, enforced.** Phase 65's review demanded an A/B mechanism + cost ceiling + gate heuristic before code; INTENT_RESOLUTION_DESIGN said run the side-quest DAG by hand before building it; the delta-audit paused a whole phase pending a frame question (`a58c2a7`, `127a634`).
- **Reconstructible records.** Session-numbered MILESTONES entries carried rationale, verbatim corrections, and SHAs; run bundles extended the same legibility to paid runs.
- **Theory captured as transcripts, not just conclusions.** DRIVER_AND_WATCHER notes its own n=1 caveat; THREAD_ARCHITECTURE ships with the full 7-turn transcript and glossary.
- **A clean supervision contract.** The five-action space with an explicit convergence budget is arguably cleaner as a spec than the organically-grown navigator decision surface that superseded it (`src/director.py:1447ff`, still present).

## Cons vs today's architecture

- **Closure fail-open by design** — "any exception returns complete=True, never blocks execution", plus three silent no-verdict early-return paths. *(resolved-since — 2026-06-11, `_emit_skip(reason)` + 4 regression tests; BACKLOG_DONE.md:3228)*
- **Decomposition-by-default ceremony** — controls planned up to 40 steps; every 8-step scope run tripped a noise `decomposition_too_broad` warning tuned on pre-scope data. *(resolved-since — NOW-lane triage + conversational-compute decree 2026-07-17)*
- **Phase 64 shipped, gated OFF, forgotten** — DEFAULTS.md row said "not started" until 2026-07-16; sibling stuck-advisor dead code from birth via swallowed exception. *(still-present — `docs/DEFAULTS.md:135,137`)*
- **Broad except-swallowing as house style** — a 2026-04-26 extraction left `ctx` out of scope; the NameError was swallowed every run until 2026-06-10, killing self-reflection for six weeks. Two probe-modality classifiers written 8h apart, neither aware of the other. *(resolved-since — pyflakes suite test; narrow-except rule)*
- **No goal identity across dispatch boundaries** — run dirs unlinkable to intent, plan-step text recirculated as top-level goals, same goal ran ~25× in 35 minutes with nothing consulting prior outcomes. *(resolved-since — 2026-06-10 fixes; thread brains + goal-brain carry identity)*
- **Autonomous lane unguarded** — installed persistence unattended (BTC incident); era-ending output was a 13+-commit worker-manifest-alias flood no one asked for. *(resolved-since — mostly by retreat: heartbeat never enabled under Maro; off-switches-stay-off; gated dispatch lanes)*
- **Paid-experiment hygiene** — clean-run ratio recovery-confounded; two follow-up scope-A/B datasets (04-25-v0, 04-26-v1) generated and never analyzed, written off 2026-07-12. *(resolved-since — GOAL_BRAIN.md:705, :1948)*

## What we believed then

- **That closure fail-open was a safe conservative default.** It manufactured verification theater — never-executed checks counted as passed; silent verdict-skips hid closure outages for weeks.
- **That heartbeat was the system's living spine, merely needing taming.** Post-rename reality: it has never beaten in production under the Maro name, and the claim it ran in-process was FALSE — only ever reachable via the CLI (GOAL_BRAIN CORRECTION 2026-07-09).
- **That the A/B clean-run ratio (3/3 vs 1/3) measured the treatment.** It measured recovery-layer bugs long plans preferentially trigger; only plan compression was reliable.
- **That numbered phases were the durable organizing frame.** Phase 65 was paused mid-era by a frame question; the phase system itself was retired for MILESTONES + GOAL_BRAIN threads.
- **That mid-run director supervision would be the main lever against premature-done and drift.** It shipped complete, stayed flag-OFF for spend, and its DEFAULTS row read "not started" for months. The levers that landed: closure hardening, navigator at blocked steps, 1-shot-first triage.
- **That an autonomous backlog drain committing tested code equals productive autonomy.** The alias flood previewed the later-named failure mode: delegated self-reports can't be trusted, and autonomous sessions synthesize from stale sources — a May-12 autonomous session queued re-implementing `src/scope.py`, which had shipped 04-23 (docs/history/ROADMAP_ARCHIVE.md:1248).

## Lost good ideas

- **Watcher as a literal parallel process with interrupt rights** — "standing meta-attention that runs parallel to the traversal, not serial inside it. Only job: has this traversal earned another cycle?" (`4f22b96`). Deferred as architecturally expensive; the navigator absorbed the role *serially*, one decision per turn, still inside the traversal. **Worth reviving: yes** — concurrency hardening + session-reuse now exist; a cheap parallel watcher polling captain's-log slices for "N cycles, error not reducing" is buildable and sees exactly what serial navigator turns cannot see mid-step.
- **Signal-source rotation** — when N cycles on the same parameter axis produce no progress, rotate the input channel (screenshots → logs → git-diff-of-what-shipped). From the godot saga: the fix came from a different signal source, not a different algorithm. Never implemented; "rotation" survived only as persona/judge rotation — a different thing (rotating the judge, not the evidence). **Worth reviving: yes**, as a navigator move at stuck steps — narrow, testable, taxonomy-free, and the run-dir bundles from this same era mean alternate signal sources already exist per run.
- **Elephant-invariant per sub-goal** — each sub-goal carries a one-sentence "serves [X]; if this step stops serving that, fail" predicate evaluated at step boundary. Never built; ancestry injection covers direction-drift but nothing catches over-depth with direction intact (toenail-polishing), which cost-cap heuristics mislabel. **Worth reviving: maybe** — cheap to pilot as one plan-step field checked by the navigator.
- **Agenda-reframing as a driver primitive** — for "/make-me-rich": ask 2–3 targeted questions to split high-variance branches, and sometimes propose a *dominating* meta-goal ("what you asked solves A; here's a thing solving A+B+C, want that?"). Clarification half landed (director clarification-first + YOLO); the propose-a-better-goal half never did. **Worth reviving: yes** — highest-leverage piece of user-is-lazy-by-design; the dispatch navigator's pre-spend decline is the natural seam.
- **Convergence budget with forced escalation** — `director_replan_count` persists across restarts; at ceiling, replan/restart disallowed — converge or surface to a human. Alive in code (`src/director.py:1447ff`) but dormant behind the OFF flag; futility re-landed narrower (navigator escalate-only at blocked steps). **Worth reviving: partially** — the contract matters more than the mechanism: a hard cross-run cap on strategy-changes-per-goal is exactly what the ~25-runs-in-35-minutes pathology needed, and no cross-run equivalent exists today.
- **ConversationChannel.ask()** — loop blocks mid-run on a real user question (timeout 300s, then best judgment), dashboard as chat peer (CHANGELOG 1.19.0). `src/conversation.py` survives but the surface was overtaken by Telegram/Hermes; blocking-ask faded into escalate-and-end. **Worth reviving:** the delivery-loop decree is this idea's descendant — blocking ask() over the Hermes/Telegram lane would close the loop the 2026-04-15 design already specified.

## Sources

Git archaeology in `/home/clawd/claude/maro-orchestration`: `git log 2026-04-15..2026-05-01` (111 commits; 82 slycrel / 29 agentic.poe). Full reads at pinned commits: `7fa3078:docs/ARCHITECTURE.md`, `7fa3078:docs/ADAPTIVE_EXECUTION_DESIGN.md`, `4f22b96:docs/DRIVER_AND_WATCHER.md`, `2867e68:docs/THREAD_ARCHITECTURE.md` (+ transcript), `cc5d4c6:MILESTONES.md` (sessions 34–38), `cc5d4c6:BACKLOG.md`. Commit diffs: `426a119`, `f50cf99`, `9eb8630`, `dec8df2`, `27500a1`, `9e2e405`, `a58c2a7`, `625cf35`, `d59c6bd`, `4e4bc39`, `bacba86`, `5edc248`, `bfd91dc`, `1961ea6`, `dc52ced`, `00ba932`, `36ee7f0`, `127a634`, `6a9bb01`, `de3366b`, `a99e2c5`, `22bb7ae`, `8f88962`, `dcf1bec`, `55573fc`, `caa5061`. Current-state anchors: BACKLOG_DONE.md:2391, :3228; GOAL_BRAIN.md:374, :578, :705, :1948 + Decisions 04-23/04-27; docs/DEFAULTS.md:135-137; docs/history/ROADMAP_ARCHIVE.md:1248; docs/history/CHANGELOG.md [1.19.0]; memory archives (project_resolved_intent_v0, project_orchestration_phases). All load-bearing claims independently verified 2026-07-21: 20/22 confirmed; 2 citation-placement errors corrected in this text (bfd91dc landed 2m18s after `7fa3078`, not in its tree; the May-12 stale-synthesis record is ROADMAP_ARCHIVE.md:1248, not BACKLOG_DONE.md).
