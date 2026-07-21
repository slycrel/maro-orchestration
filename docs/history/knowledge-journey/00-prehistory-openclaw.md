---
status: history
---

# Prehistory: OpenClaw, Poe, and the poe-orchestrator prototype

*Feb 2026 – 2026-03-05*

## Architecture as it was

The system was not this repo — it was **Poe**, a persona running on OpenClaw on the 2014 Mac Mini. Genesis 2026-02-01 (`~/.openclaw/workspace/MEMORY.md` "Genesis"): Jeremy gave an AI admin access, credits, and a partner framing. Within a week the model stack settled on GPT-4o primary + Gemini-2.0-flash fallback after a Codex-via-ChatGPT-Plus-OAuth prototype failed ("Failed to extract accountId from token"; `prototypes/2026-02-06-codex-oauth.md`, status ENDED). Telegram was the portal. Autonomy ran on shell: HEARTBEAT.md checks every 5 minutes, a proactive-loop rotation, and a large script surface for email/X/Moltbook/calendar/GUI automation (94 scripts in `~/.openclaw/workspace/scripts/` today; the in-era count is unverified since the directory kept growing). The most mature prototype was NOT the orchestrator — it was the **Polymarket research bot** (MEMORY.md 2026-02-16..20): staged artifacts, verifier gates, promotion gates blocked on named metrics, falsification harnesses. Verify-before-promote discipline was born there, before the orchestrator existed.

The orchestration prototype at the era's single commit, `97473ac` (2026-03-05, author Codex, "chore: repo hygiene for autonomy", 34 files):

- **Engine**: `src/orch.py`, 162 lines of deterministic Python, zero LLM calls. A strict-regex NEXT.md checkbox state machine — `[ ]` todo, `[~]` doing, `[x]` done, `[!]` blocked — with `select_next_item` (first todo wins), `select_global_next` (`# Newest-modified project wins.` — the whole scheduler), `append_decision` (timestamped append-only DECISIONS.md), and `ws_root()` hardcoded to `/home/clawd/.openclaw/workspace`.
- **Scripts**: three; `scripts/enqueue.sh` delegated to OpenClaw's global task-queue (`"$QUEUE" enqueue project_task "project=$SLUG :: $TASK"`).
- **The "agent"** was Poe-on-OpenClaw reading and writing these files; the code was only the state contract.
- **Conventions before code**: `docs/CONVENTIONS.md` — "One mission, one folder." / "One living checklist." / "Artifacts are truth. If it didn't get written down, it didn't happen." / "Interruptions are expensive. Only ping the human when required." — plus an "Autonomy contract (default authority = C)". Canonical doc quartet NEXT.md / RISKS.md / DECISIONS.md / PROVENANCE.md, instantiated in 4 of the 5 live projects (orchestration-hardening carried only NEXT.md + DECISIONS.md).
- **Personas**: five markdown files, including `personas/loop-validator.md` ("Loop Sheriff").

What did NOT exist in-era: goal ancestry (`src/ancestry.py` is Phase 6), heartbeat code in the prototype (`src/heartbeat.py` is Phase 4; heartbeats were OpenClaw shell), durable run records (control plane lands `2b1ecb1`, 2026-03-19), planner, tests (arrive 0.4.0, 2026-03-11), execution verification of any kind. The Claude Code decision is also post-era — `~/claude/idea.md` and `~/claude/CLAUDE.md` date to 2026-03-23.

The era ends with the prototype committing itself to git under Codex authorship. The same lineage (`97473ac` exists in both `~/.openclaw/workspace/prototypes/poe-orchestration/` and this repo) was pushed to github.com/slycrel/openclaw-orchestration on 2026-03-11, later renamed maro-orchestration. This repo did not supersede the prototype; it IS the prototype, grown up.

## Discoveries & aha moments

### Artifacts are truth — state moves out of the chat and onto disk (2026-03-05)
Chat is ephemeral and models lie about progress; the only durable ground truth is files. The single oldest surviving idea in the project — still verbatim in the current tree.
- Evidence: `97473ac` docs/CONVENTIONS.md; `97473ac` README.md "Artifacts as ground truth: everything important is written to disk"; current `docs/CONVENTIONS.md:11` (verbatim survival).

### Authority Level C — autonomy as a negotiated standing contract (2026-03-04)
Autonomy stopped being per-task permission and became a one-time contract with enumerated hard boundaries (money, credentials, destructive deletes, external posting, scope pivots). Jeremy granted it with a single character — "C" — on Telegram; Poe replied "C accepted... you give me a mission, I'll figure out how to do it, execute autonomously, and only interrupt you for the hard boundaries." Codified in CONVENTIONS.md the next day. Every later autonomy doc (AGENTS.md "Authority Level: C (Aggressive)", CLAUDE.md "Act, don't ask") descends from this.
- Evidence: Telegram export 2026-03-04 07:02 (quote verified against raw export; dev-recall FTS turn id 612 unverified); `97473ac` docs/CONVENTIONS.md "Autonomy contract (default authority = C)".

### Loop Sheriff — behavioral validation replaces iteration counters (2026-03-05)
The right defense against runaway loops is not "max N tasks per run" but liveness checks on real state: did an artifact appear, did NEXT.md/DECISIONS.md change, did queue depth drop? orch.py's docstring commits to "a loop-until-blocked executor without relying on arbitrary iteration limits." Conceptual ancestor of sheriff.py stuck-detection (Phase 4, post-era) and today's validator-over-counter posture.
- Evidence: `97473ac` personas/loop-validator.md ('replace arbitrary "N tasks per run" limits with behavioral validation'); `97473ac` src/orch.py docstring; docs/history/CHANGELOG.md "Phase 4: Loop Sheriff + Heartbeat".

### "When idle, take the next action" — the autonomous work loop stated as policy (2026-03-03)
An idle agent picks up the next task rather than waiting for a prompt. Jeremy on Telegram: 'let's change that to "When idle, take the next action". I like the north star approach, but let's keep that in tasks, and have agents help keep us moving' — the germ of the later north star ("wakes up with tasks, executes them without hand-holding, and gets measurably better over time").
- Evidence: Telegram export 2026-03-03 18:25 (quote verified against raw export; FTS turn id 596 unverified); `~/claude/CLAUDE.md` "North star" (2026-03-23, post-era formalization).

### Decision-gated pings — interruptions are a budgeted resource (2026-03-05)
Only interrupt the human at real forks and risk boundaries; everything else is silent execution plus artifacts. "Interruptions are expensive. Only ping the human when required." Earliest form of the escalation-design thinking that much later became "the substrate go-between IS the surface."
- Evidence: `97473ac` README.md "Decision-gated pings"; `97473ac` docs/CONVENTIONS.md Philosophy; memory/project_1_0_installability.md (lineage).

### Verifier gates predate the orchestrator — they came from the Polymarket bot (2026-02-16..20)
Verify-before-promote (staged artifacts, promotion gates blocked on explicit metrics like verification_pass_rate and score_stability, falsification harnesses) matured in the research pipeline in mid-February, weeks before the orchestration prototype existed. The orchestrator inherited the instinct, not the other way around.
- Evidence: MEMORY.md "Polymarket Program Maturation (2026-02-16 to 2026-02-17)"; "Polymarket Program Hardening (2026-02-19 to 2026-02-20)".

### Workflow contract before automation (2026-03-05)
Establish filesystem conventions and the human/agent contract first; wire the loop second. "This prototype is intentionally minimal right now: it establishes the workflow contract and filesystem conventions first." The conventions outlived every line of the era's code.
- Evidence: `97473ac` README.md "Current status".

## Pros vs today's architecture

- **Whole-system auditability**: the entire engine was 162 lines of Python + 3 shell scripts — readable end-to-end in ten minutes. Today's `src/` is 153 Python files, ~96k lines (ROADMAP_ARCHIVE.md:738 already flagged 46 files/27K lines as cognitive load).
- **Zero-cost, zero-LLM core**: orch.py made no model calls, so the orchestration layer itself could never silently spend. Runaway spend was only possible in the OpenClaw shell around it — exactly where it later happened (archive/project_openclaw_cron_tokenburn.md).
- **Human-readable, hand-editable state**: NEXT.md checklists and timestamped DECISIONS.md could be read, grepped, and fixed with any editor. Today's run state (events JSONL, run cards) needs tooling to inspect.
- **Conventions-first design that aged well**: "Artifacts are truth", decision-gated pings, per-project DECISIONS.md (direct ancestor of GOAL_BRAIN.md's dated append-only Decisions section) were right the first time and survive today.
- **Personas as plain markdown** with no code coupling — five focused personas including the Loop Sheriff, editable by anyone, instantiable by convention rather than framework.
- **Capability breadth of the surrounding system**: era-Poe had email, X, Moltbook, calendar, browser/GUI automation, and a local ops dashboard — external-reach breadth maro still does not own natively.

## Cons vs today's architecture

- **No execution verification at all** *(resolved-since)*: "done" was a checkbox flip; nothing checked work happened. This structural blindness let GPT "pretend to build" an orchestration system — M1-M4 milestones existed as docs, not code (CHANGELOG [0.4.0]: "roadmap M1-M4 items were converted from plan-only to executable implementation"; idea.md line 96). Resolved via verify loops, done≠successful split, claim probes.
- **Scheduler was "newest-modified project wins"** *(resolved-since)*: recency as priority, trivially gamed by any file touch, no notion of goal importance. PRIORITY files arrive 0.4.0; later milestones/threads.
- **Substrate lock-in** *(resolved-since)*: `ws_root()` hardcoded to the OpenClaw workspace; enqueue.sh depended on OpenClaw's task-queue.sh. Portable root `0ffdf71` (2026-03-11); MARO_WORKSPACE unification; M5 portability.
- **Zero tests** *(resolved-since)*: correctness rested on strict regexes and hope. Tests arrive 0.4.0; current suite ~4.6k tests.
- **Unmetered background autonomy** *(resolved-since in maro; recurred post-era on the OpenClaw side)*: 5-minute heartbeats and cron loops with no budget gate and delivery:none — silent spend by construction. The class bit as late as 2026-06-21 (poe-orchestration-build-loop cron burning Codex quota on the deprecated prototype). Resolved via $2/run $10/day caps and good-citizen off-switch rules; OpenClaw shut down on this box 2026-07-16.
- **Persona/system fusion** *(resolved-since)*: Poe WAS the orchestrator — identity, memory, and orchestration logic one entity, unpublishable and unswappable. Resolved by Maro=framework / Conductor=role / Poe=optional persona.
- **No provenance of what the model actually did** *(resolved-since)*: PROVENANCE.md was optional source pointers, not a run record; no cost, steps, or attribution. Resolved by the durable run control plane (`2b1ecb1`), run cards, captain's log, attribution.

## What we believed then

- **That GPT-4o/Codex was an adequate doing-engine.** Jeremy's written verdict (idea.md:96, 2026-03-23): 'chatGPT seems to default to "we're talking" and much less "we're doing things"... I've found that sonnet especially, and mostly opus, understands my intent much better than any GPT variant.' The whole era ran on the wrong engine.
- **That milestone documents describe built software.** M1-M4 "implementation" was plan-only; the v0.5.0 honest audit (docs/history/2026-03-17-mainline-plan.md) had to reset the roadmap. The era had no defense against an LLM narrating progress it hadn't made.
- **That a markdown checklist is sufficient durable state.** Within two weeks the successor needed a durable run lifecycle control plane (`2b1ecb1`) — checkboxes couldn't represent runs, retries, outcomes, or evidence.
- **That frequent heartbeats make a system more autonomous.** Five-minute wake-ups mostly burned tokens on no-ops; the later heartbeat-gate design concluded "The real deliverable is the work-signal context assembler, not the model," and the surviving cron instance of this belief burned real quota in June.
- **That the persona is the product.** Era framing was Poe-as-entity; the mature position inverted this — orchestration is the product, substrate and persona swappable.
- **That riding OpenClaw's global task-queue.sh was the right integration seam.** The queue delegation died with the prototype; dispatch/runs became first-class, and OpenClaw itself was shut down 2026-07-16.

## Lost good ideas

- **RISKS.md as a canonical per-project file** — "Known risks, unknowns, assumptions, and things to watch" living next to NEXT.md and DECISIONS.md. Lost as run-centric state (events, run cards, GOAL_BRAIN threads) took over; a path helper survives at `src/orch_items.py:365` but the ledger is no longer maintained convention. **Worth reviving: yes, cheaply** — a maintained per-project risk ledger is a natural input to navigator blocked-step escalation and closure verification (known-gap pins are the same instinct).
- **Loop Sheriff's one-page diagnosis artifact** — on detected spin, write a single markdown alert: Symptoms, top-3 root-cause hypotheses, evidence links, "Minimal fix (one action)". Lost when stuck detection became code that acts rather than explains. **Worth reviving: partially** — the acting path is better now, but attaching a Loop-Sheriff-style one-pager to escalations serves the delivery-loop principle (user hears the outcome in plain words).
- **"Prefer degrading into deterministic/cheap glue work instead of going idle"** — the era's explicit budget-pressure policy (MEMORY.md Operational Notes). Maro's posture became refuse-at-cap; the graceful-degradation ladder was never rebuilt. **Worth reviving: maybe** — the local-validator ladder partially revives it for validation; degrade-don't-idle for whole-run planning under budget pressure is unexplored.
- **Metric-thresholded promotion gates** (Polymarket bot) — promotion blocked until named metrics (verification_pass_rate, score_stability) pass, with the blocking metric reported as "a clear next optimization target". Lost when the bot was archived; skill/rule promotion adopted confirmation counts (2+ observations) instead. **Worth reviving: yes for skill/persona promotion** — "promoted when metric X clears threshold Y, currently blocked on Z" is more diagnosable than counting, and the failure-pattern corpus could supply the metrics.

## Sources

- `git show 97473ac` in /home/clawd/claude/maro-orchestration (README.md, docs/CONVENTIONS.md, src/orch.py, personas/, scripts/enqueue.sh, full file list); lineage check vs `~/.openclaw/workspace/prototypes/poe-orchestration` (shared first commit)
- `~/.openclaw/workspace/MEMORY.md` (Genesis through 2026-02-20), GOALS.md (mtime 2026-02-09, era-authentic), prototypes/2026-02-06-codex-oauth.md, scripts/ listing; SOUL.md and AGENTS.md are post-era revisions, used for lineage only
- `~/claude/idea.md` and `~/claude/CLAUDE.md` (mtimes 2026-03-23, post-era — retrospective quotes and lead-dating)
- maro-orchestration `docs/history/`: CHANGELOG.md ([0.4.0], Phases 4-8), 2026-03-17-mainline-plan.md (honest audit), ROADMAP_ARCHIVE.md; current docs/CONVENTIONS.md:11; src/orch_items.py:365
- Telegram export (ChatExport_2026-04-16/result.json) via dev-recall FTS — quotes and timestamps verified against the raw export; FTS turn ids (596/612/777) unverified
- Memory archive: project_openclaw_cron_tokenburn.md, project_orchestration_phases.md

*Verification: 26 load-bearing claims checked — 24 confirmed, 1 refuted (doc quartet was in 4 of 5 projects, not all 5; corrected above), 1 unverifiable (FTS turn ids, marked).*
