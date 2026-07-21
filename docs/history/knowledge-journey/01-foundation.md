---
status: history
---

# Foundation: phases 0–6, the first autonomous loop

*2026-03-05 – 2026-03-17*

> Framing correction up front: the "first autonomous loop" did **not** run in this era. This window planned Phases 0–6 and executed only Phase 0. The phase modules (agent_loop.py, intent.py/handle.py, director.py, sheriff.py, memory.py, llm.py, telegram_listener.py) all landed 2026-03-23 (328d08a, 2555883, 482341a, 930a1e1; CHANGELOG [1.0.0]), after ~ten 03-19/03-20 run-lifecycle commits that sit between the era end and the burst.

Boundary commits: 97473ac (2026-03-05, Codex \<codex@local\>, "chore: repo hygiene for autonomy" — genesis, 34 files) → dcb241a (2026-03-17, Jeremy Stone, Co-Authored-By: Claude Opus 4.6, "feat: add vision guide, autonomy-first roadmap, and source docs" — 65-file tree).

## Architecture as it was

A deterministic, zero-LLM, file-first orchestration kernel. 65 files; exactly two Python runtime modules.

- **`src/orch.py`** — Markdown checklists as the entire state machine. Each project = `projects/<slug>/` with `NEXT.md` (checkbox states `[ ]`/`[~]`/`[x]`/`[!]` for todo/doing/done/blocked, parsed by strict regex `ITEM_RE`), append-only `DECISIONS.md`, plus `PROVENANCE.md`, `RISKS.md`, and a `PRIORITY` file. Global scheduling: explicit priority with newest-mtime fallback. The genesis docstring (97473ac — trimmed by cdea33b on 03-11; at dcb241a it reads only "Poe orchestration core utilities.") said "We intentionally keep parsing rules strict and explicit" and aspired to "support a loop-until-blocked executor."
- **`src/cli.py`** — 126 lines: `init|next|done|log|blocked|report`, with an `ERROR[E_*]` error taxonomy.
- Around it: shell scripts (`enqueue.sh`, `new_project.sh`, `mark_next_done.sh`, `smoke.sh`), pytest suites, CI (shellcheck + pytest + smoke), and a large docs surface (migration, queue-adapter, backward-compat, security-model, end-to-end, publish checklist) built during M1–M4 (docs/reports/poe-orchestration-overnight-implementation-2026-03-11.md; CHANGELOG [0.4.0]).

Nothing in the repo could think. No LLM adapter, no loop runner, no agent — the executor was a Codex/Poe session inside OpenClaw driving the CLI by hand. The repo lived at `~/.openclaw/workspace/prototypes/poe-orchestration/`: `ws_root()` returned `~/.openclaw/workspace` and `orch_root()` appended the prototype path; portability was a one-commit late shim (0ffdf71, OPENCLAW_WORKSPACE/WORKSPACE_ROOT env override, "for publish"). Every commit until the last was authored `Codex <codex@local>` (11 commits total). The tree carried Poe-operational residue: live project dirs (`projects/x-obscicron-*/`, `polymarket-wallet-research/`, `todo-inbox/`) and six persona Markdown specs with no code to load them.

The era ends with a documentation payload, not code: dcb241a added **VISION.md** (238 lines — Body/Process/Mask layers, Director/Worker hierarchy, NOW/AGENDA lanes, Level-C autonomy policy, UX timing contract, Loop Sheriff, 11 anti-patterns, memory strategy — all anchored with verbatim dated Jeremy quotes), **ROADMAP.md** (8 phases, 0–7, each with a shippable artifact and a cost model), and three source docs distilled from 2,349 Telegram messages (Feb 5 – Mar 12): docs/poe_intent.md, docs/poe_orchestration_spec.md, docs/poe_miscommunication_patterns.md. MAINLINE_PLAN.md was rewritten as the v0.5.0 "honest audit" baseline (preserved at docs/history/2026-03-17-mainline-plan.md).

## Discoveries & aha moments

### The Honest Audit — "we built scaffolding, not the product" (2026-03-17)
Five weeks produced CI, semver policy, migration docs, publish checklists — real infrastructure, zero autonomy. The reset: "An honest audit of the codebase. The original M0-M4 milestones built real infrastructure scaffolding. v0.5.0 acknowledges that and resets the roadmap around the actual goal: making Poe autonomous." Phase 1 became "THE critical unlock. Without this, nothing else matters. Poe gets an LLM brain." First act of done≠achieved self-honesty — a theme that recurs for months.
- Evidence: dcb241a commit message; docs/history/2026-03-17-mainline-plan.md §"What v0.5.0 represents"; dcb241a:ROADMAP.md Phases 0–1; docs/history/ROADMAP_ARCHIVE.md Phase 0.

### Autonomy decays per-session unless structurally encoded (2026-03-17, distilled from Feb–Mar)
Jeremy granted Level-C autonomy "6-7 separate times"; the agent kept reverting to permission-seeking. Root cause as written: "You're not failing to communicate it. The system is failing to encode it, so every new situation reverts to generic 'be cautious' behavior." Conclusion: "Autonomy policy must be encoded in a single, canonical, always-loaded location... It should be impossible for a new session to start without loading the authority level." Direct ancestor of CLAUDE.md's autonomy section and the GOAL_BRAIN always-loaded pattern.
- Evidence: dcb241a:docs/poe_miscommunication_patterns.md Pattern 1; dcb241a:VISION.md §6.

### Validator-based loop control, not count-based — the Loop Sheriff is born (2026-03-17; quote dated Mar 3)
Iteration caps are the wrong abstraction. Jeremy, verbatim: "You need an independent validator to keep from getting stuck in a loop. This could be a script, agent, or even simple queue. With agents we don't have to know up front." Reframe: "are we still making progress?" — not "how many iterations?". Seed of sheriff.py (mainline first-add 930a1e1, 2026-03-23; BACKLOG_DONE.md:1008 cites same-day twin 12a7a90, which is not on main) and, much later, inspector/navigator escalation.
- Evidence: dcb241a:VISION.md §8; dcb241a:ROADMAP.md Phase 4; BACKLOG_DONE.md:1008; src/sheriff.py (still present today).

### The persona is the portable unit, not the agent instance (2026-03-17; quote dated Feb 27)
Three layers: Infrastructure (Body) / Agent Runtime (Process) / Persona (Mask). Jeremy: "I keep seeing people talk about agents as processes/identities. I think I'd like to add nuance to that and build sub-agent personas. And keep those separate from the infrastructure that runs the agentic distribution." Survives intact today (personas/ + persona.py; Maro=framework, Poe=optional persona).
- Evidence: dcb241a:VISION.md §3; dcb241a:docs/poe_orchestration_spec.md §2 Layer 3; current CLAUDE.md ("can optionally wear a persona").

### Orchestration IS the product; projects are test cases (2026-03-17; miscommunication dated Mar 1)
Orchestration features had been built inside the Polymarket prototype because that's where code lived. Jeremy: "The poe orchestration was intended to be sandboxed while built, then applied to our openclaw setup... I'm not really sure how that got lost." Written lesson: "When the orchestration IS the product, coupling it to a test case defeats the purpose." Earliest statement of the substrate-agnostic vision that later drove the Maro rename.
- Evidence: dcb241a:docs/poe_orchestration_spec.md §1; dcb241a:docs/poe_miscommunication_patterns.md Pattern 3; memory: project_substrate_agnostic_vision.md.

### Mining the conversation record into compiled truth (2026-03-17)
The era-end commit designed backward: 2,349 Telegram messages distilled into intent (Jeremy's words), spec (what to build), and 11 named anti-patterns of the collaboration itself — every rule anchored to a verbatim dated quote. Methodological ancestor of GOAL_BRAIN.md and the SF-13 decree-capture rule.
- Evidence: dcb241a commit message; dcb241a:docs/poe_intent.md header; dcb241a:docs/poe_miscommunication_patterns.md.

### Overnight autonomous implementation works — M1–M4 in one night (2026-03-11)
cdea33b converted the entire M1–M4 roadmap "from plan-only to executable implementation" in one overnight Codex run, with a validation log of commands actually run. The CHANGELOG [0.4.0] Fixed line: the fix was that plans became code. First demonstrated instance of the north-star loop (waking up to progress) — done by a session, not yet by the orchestrator.
- Evidence: cdea33b; dcb241a:docs/reports/poe-orchestration-overnight-implementation-2026-03-11.md; docs/history/CHANGELOG.md [0.4.0].

## Pros vs today's architecture

- **Auditable in one sitting**: two Python files (~350 lines), zero LLM spend, fully deterministic. Today src/ is ~130 flat modules with a REFACTOR_PLAN to subpackage them. (git ls-tree dcb241a; ROADMAP.md Phase 0: "Cost: Zero LLM spend. This is file editing.")
- **Human-repairable state**: Markdown checkboxes + append-only DECISIONS.md were the whole state machine; any text editor was a recovery tool. The JSONL-corruption bug class arrived with the later runtime (docs/history/2026-03-28-phase-audit.md C2).
- **Quote-anchored VISION.md**: every design rule carried a verbatim dated Jeremy quote inline with the design it justified. GOAL_BRAIN.md keeps decisions today, but quotes-fused-to-design was arguably the purer drift-prevention device.
- **Cost as a design column from day 1**: every roadmap phase carried an explicit cost model ("Zero LLM spend", "~$0.01-0.05 per goal", "Sheriff uses cheap model") before any code existed. (dcb241a:ROADMAP.md Phases 0–4.)
- **11 anti-patterns as pre-paid lessons**: #5 no count-based loop control, #10 done means verified-done, #11 no non-actionable alerts — several later re-learned independently at cost (compare feedback_delivery_loop.md, 2026-07-17, re-deriving #6/#11 territory). Written first; under-used.
- **Publish discipline from week one**: go/no-go checklist with secrets sweep, semver/backward-compat policy, community templates (c30b709). The 2026-07-15 PyPI 0.8.0 push stood on this; docs/PUBLISH_CHECKLIST.md survives today.

## Cons vs today's architecture

- **Zero autonomy in-repo** — no LLM adapter, no loop runner, no agent code; the "brain" was an OpenClaw Codex session driving the CLI. *(resolved-since: Phase 1–6 burst 2026-03-23 — 328d08a agent_loop, 2555883 intent/handle, 482341a director — through today's agent_loop.py/loop_*.py.)*
- **Scheduling = newest-mtime-wins plus a PRIORITY-file patch** — no intent classification, no lanes, no decomposition. "What should I do next?" was answered by file modification times. *(resolved-since: NOW/AGENDA routing (2555883), Director hierarchy, today's MILESTONES.md queue.)*
- **Workspace root hardcoded to the OpenClaw prototype path** (orch_root() → `~/.openclaw/workspace/prototypes/poe-orchestration`); portability a single env-var shim added "for publish" (0ffdf71). *(resolved-since: ~/.maro/workspace + config.py, workspace-pin layout 2026-07-03; PyPI 0.8.0 install.)*
- **Version identity chaos**: tag v0.1.0 and CHANGELOG [0.4.0] share the same date (2026-03-11); MAINLINE_PLAN declares a v0.5.0 baseline; no v0.5.0 tag exists (only v0.1.0, v0.2.0). *(resolved-since: 0.8.0 PyPI release discipline — memory: project_1_0_installability.md.)*
- **"Done means verified-done" was prose only** (anti-pattern #10, no enforcement code) — the 03-23 "COMPLETE" phases had unwired pieces per the 03-28 audit; done≠successful not decreed until July. *(still-present: substantial machinery shipped — inspector, closure verdicts, write fence — but current CLAUDE.md still says "verify→learn loop not closed".)*
- **Poe-operational residue tangled into the framework repo**: live project dirs, a yahoo mark-read helper (8c172ff), browser-profile paths (854f08b) — the exact Pattern-3 coupling the era's own docs warned against. *(resolved-since: repo/workspace split; projects live in ~/.maro/workspace/projects.)*

## What we believed then

- **Phase durations of "~1-2 weeks"** — wrong in both directions: Phases 1–7 landed in one day (2026-03-23), and the resulting "COMPLETE" claims were overstated (03-28 audit: circuit-breaker never checked at match time, opt-in cost budgets, "recovers from blocks" = heartbeat restart).
- **GPT/Codex as permanent substrate**: "gpt-5.4 primary → gpt-5.3-codex-spark → … Gemini as last resort" with Codex CLI OAuth as the single auth surface (dcb241a:docs/poe_orchestration_spec.md §2). By July the box is Claude-primary and OpenClaw was shut down here 2026-07-16.
- **OpenClaw's ~80 shell scripts as the foundation**: Phase 1 planned to "Wire loop runner to existing task queue (scripts/task-queue.sh)". The runtime went pure Python; the script layer was never the integration point and retired with OpenClaw.
- **The file-first CLI as publish-worthy product**: v0.1.0 release notes led with "a practical orchestration loop that survives model/runtime churn" (dcb241a:docs/releases-v0.1.0.md), gated by a go/no-go checklist (c30b709). Six days later the mainline plan reclassified all of it as scaffolding.
- **"Same action repeated 3x" stuck detection as an acceptable Phase-1 seed** — even though the same commit's anti-pattern #5 declared count-based loop control wrong. The known-bad heuristic still shipped 03-23 and had to be replaced by validator machinery (sheriff → inspector → navigator) over months.
- **Semver + backward-compat guarantees at v0.x prototype stage** (docs/BACKWARD_COMPATIBILITY.md, M4 "v1 readiness"). Posture later inverted: 1.0 explicitly NOT a work gate; pre-autonomy compat promises were premature polish — exactly what the honest audit called out.

## Lost good ideas

- **"Also-After" hooks** (VISION.md §11): every completed goal auto-triggers post-goal capture — audit entry, memory write, follow-ups, index artifacts — as a first-class hook, not a convention. Never implemented (zero hits for also-after/also_after in current src/); end-of-chunk discipline is human/CLAUDE.md convention only. **Worth reviving: yes** — it mechanizes the end-of-chunk rule and SF-13 decree capture; a post-goal hook point makes capture structural instead of remembered.
- **Concrete UX response-timing SLA** (VISION.md §7): ack ~1s, status 5-15s, substantive 30-40s, three named response modes. Survives only in record docs (docs/maro_orchestration_spec.md:120, docs/maro_intent.md:48); the 2026-07-17 delivery-loop decree re-derived the principle without the measurable table. **Worth reviving: yes** — as latency SLOs on the delivery loop; the numbers were the enforceable part and they evaporated.
- **Machine-checkable error taxonomy** (`ERROR[E_*]`, M1/CHANGELOG 0.4.0): carried only by the legacy shell scripts today; the Python runtime never adopted typed error codes. **Worth reviving: yes** — the failure-pattern corpus (24 entries/6 families) is a taxonomy rediscovered the hard way; typed runtime codes would let it be grepped/counted instead of LLM-classified.
- **Per-phase cost models in the roadmap**: every planned phase carried an expected-spend line before code. Phases dissolved into MILESTONES; spend UX standardized on EFFORT language; post-hoc tracking exists (metrics.py) but pre-commitment cost modeling disappeared. **Worth reviving: maybe** — a one-line expected-cost note per milestone is cheap and made frugality legible from day 1.

## Sources

- git log 2026-03-05..2026-03-18 (11 commits, authors verified); git log 2026-03-18..2026-03-26 (post-era phase burst).
- git show 97473ac (--stat + src/orch.py full — genesis docstring); cdea33b (docstring trim + M1-M4); 8c172ff; 854f08b; c30b709; 0ffdf71 (ws_root portability); eedd770:MAINLINE_PLAN.md.
- git show dcb241a: README.md, VISION.md, ROADMAP.md, CHANGELOG.md, src/cli.py, docs/poe_intent.md, docs/poe_orchestration_spec.md, docs/poe_miscommunication_patterns.md, docs/reports/poe-orchestration-overnight-implementation-2026-03-11.md, docs/releases-v0.1.0.md; git ls-tree -r dcb241a (65 files).
- docs/history/: 2026-03-17-mainline-plan.md, ROADMAP_ARCHIVE.md, CHANGELOG.md ([0.4.0], [1.0.0]), 2026-03-28-phase-audit.md.
- git tag --list + rev-list (v0.1.0→539897c, v0.2.0→7999e72; no v0.5.0).
- BACKLOG_DONE.md:1008 (sheriff birth); GOAL_BRAIN.md (no in-era hits).
- Current-tree survival greps: src/sheriff.py present; also-after zero hits; ERROR[E_ shell-only; UX timing in record docs only; docs/PUBLISH_CHECKLIST.md exists.
- Memory: project_orchestration_phases.md (archive), project_substrate_agnostic_vision.md, project_1_0_installability.md, project_budget_posture.md, project_poe_openclaw.md.
