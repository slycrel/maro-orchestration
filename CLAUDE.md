# Claude Code — Maro

**This is the mainline repo** (`maro`, formerly `openclaw-orchestration`). All orchestration work happens here unless explicitly directed elsewhere.

**Currency rule:** narrative prose anywhere (including this file) loses to GOAL_BRAIN.md and MILESTONES.md. If a doc or skill states a fact you've just proven stale, fix it in the same commit — don't leave it for "later".

**Start-of-session checklist:**
1. Read this file (CLAUDE.md)
2. Read GOAL_BRAIN.md — compiled truth: Jeremy's invariants (quoted), verified state, decisions, open threads. **When it disagrees with any other doc, GOAL_BRAIN.md wins** — all other docs are best-guess by decree. Update its system-maintained sections at end-of-chunk.
3. Read MILESTONES.md — prioritized work queue. This is what to do next.
4. Read BACKLOG.md — active deferred items, bugs, ideas. Update as you work. When an item ships, move it to BACKLOG_DONE.md with its context intact (the archive is ingested by `dev-recall` for historical "why/how/rejected" context).
5. Looking for a specific doc? `docs/INDEX.md` maps questions → docs and carries the status legend (living / dormant-design / history).
6. Check `~/claude/grok-response-*.txt` for unprocessed feedback (the two files there as of 2026-08-11 — rounds 2–3, March — are long processed; only NEW files matter)

**When you need to recall something from prior correspondence (design docs, conversation logs, rationale for a past decision), use `dev-recall` instead of blind grep.** It's full-text (FTS5/BM25) retrieval over docs/, lat.md/, GOAL_BRAIN/VISION/MILESTONES/BACKLOG/BACKLOG_DONE/ROADMAP/CLAUDE, and auto-memory:

```bash
PYTHONPATH=src python3 -m correspondence query "why did we rename constraint to scope"
PYTHONPATH=src python3 -m correspondence ingest --since 1d   # re-embed recent changes
PYTHONPATH=src python3 -m correspondence status
```

This is **dev-facing tooling only** — not part of Maro's runtime self-improvement. See `src/correspondence.py` module docstring. Don't blur these.

**Before modifying a subsystem, load its architecture skill.** The `skills/arch-*.md` files describe intent, interfaces, gaps, and file maps for each subsystem. Read the relevant one before making design decisions:

| Working on... | Load this skill |
|--------------|----------------|
| Goal entry, routing, intent, director, workers, personas | `skills/arch-interface-routing.md` |
| Core loop, decompose, step execution, pre-flight | `skills/arch-core-loop.md` |
| Memory, knowledge, lessons, captain's log, crystallization | `skills/arch-memory-knowledge.md` |
| Inspector, evolver, graduation, introspect, skills, constraints | `skills/arch-quality-selfimprove.md` |
| LLM adapters, config, heartbeat, projects, tasks, metrics | `skills/arch-platform.md` |

These skills document **intent vs implementation gaps** — what the system is supposed to do vs what's actually coded. They prevent accidental regressions and surface the real design constraints.

**Session patterns:** read `docs/DEV_PATTERNS.md` before shaping or
reviewing work — the taste half (cuts-first, consumer-first, done-means,
possible-now bias…) while planning, the judgement half (live writer?
executed check? claim verified?…) while reviewing. Non-gated pre-read,
honestly labeled: the 2026-07-21 with-doc/control battery showed no
measurable delta (both arms at ceiling; see
`docs/history/2026-07-21-phase05-battery.md`) — it ships on cost≈zero
grounds, not benchmark evidence.

**House style:** `docs/HOUSE_STYLE.md` is the written-down dev approach —
the workflow loop (shape → build → verify → document → land →
cross-model adversarial review → verify-before-fix → fix → record) and
the standing invariants with their tripwires (out-of-the-box decree,
consumer-first, SF-13, data retention). This file stays the session
entry pointer; the approach itself lives there.

**Coding posture:** read `docs/CODING_NOTES.md` before shipping. This repo
is heavily iterating — principles for keeping seams visible and rework
cheap live there (registry vs dispatch, 3-is-fine/4-wants-extraction,
don't-refactor-mid-feature, test seams not internals, etc.). Not a style
guide; the minimum overhead that keeps the codebase honest during
exploration.

**Open design spaces** — if your work touches these, read the doc first:

| Space | Doc | Status |
|---|---|---|
| Intent resolution / side-quests / "what does done mean" | `docs/INTENT_RESOLUTION_DESIGN.md` | Partially shipped (ResolvedIntent/Deliverable live); side-quest handling open |
| Scope + constraint orchestration (Phase 65) | `docs/CONSTRAINT_ORCHESTRATION_DESIGN.md` + review | Scope+ResolvedIntent injection LIVE on this box since 2026-07-09 (SF-4 resolution; 2026-04-22 A/B: inject wins). Fresh installs: `scope_generation` OFF by default (no silent LLM spend). Deeper constraint-orchestration discussion deferred |
| Adaptive execution | `docs/ADAPTIVE_EXECUTION_DESIGN.md` | Phases A–C shipped 2026-04-15 (reassess/replan/restart/escalate live in loop_execute); closure-check unification SHIPPED 2026-07-28 (`director.evaluate_closure` decision layer, ClosureVerdict kept as evidence record — see doc's spec correction); §9.3 structural declare-blocked at the restart boundary SHIPPED 2026-07-29 (closure fingerprint convergence → thesis-refuted, stall-driven not budget-driven); Phase D (ExecutionPlan/memory layer) still open |
| Memory / graph / filesystem-vs-real-memory | `docs/history/2026-07-04-memory-decision-brief.md` (inputs: `docs/MEMORY_ARCHITECTURE.md`, `docs/KNOWLEDGE_CRYSTALLIZATION.md`) | Direction decided 2026-07-07: memory-as-module, 3rd-party bake-off behind `src/memory_port.py`; MILESTONES arc -1 |
| Dispatch boundary / cross-agent authority | `docs/DISPATCH_ENVELOPE.md` | Provenance gate SHIPPED 2026-07-29 (`src/lesson_provenance.py` — prompt-derived lessons quarantined at mint, `minted_from` stamp, answers the db37d525 contamination); typed envelope box-side intake SHIPPED 2026-07-29 (`src/dispatch_envelope.py` — user_ask=goal, operator context rides ancestry channel, extraction exclusion by construction); delivery rendering box-side SHIPPED 2026-07-29; Poe-side skill (mini2 SKILL v0.3.0) + artifacts-travel rider SHIPPED 2026-08-01; return-path quality (all-candidate serving, post-hoc top-pick ranking, `ANSWER.md`) SHIPPED 2026-08-06. **Arc is closed** — sole named residual is that a CONTAINERIZED worker can read neither attachment copy, evidence-gated on the C4-BOX flip (don't pre-build) |

- GitHub: https://github.com/slycrel/maro-orchestration (renamed from openclaw-orchestration 2026-06-26; kept the `-orchestration` suffix rather than bare `maro`)
- Machine: Ubuntu headless, user `clawd`, `/home/clawd/claude/maro-orchestration/`
- Owner: Jeremy Stone (`slycrel`) — 25+ years engineering, AI orchestration

---

## What this is

**Maro** — an autonomous agent framework. Takes a high-level mission, breaks it into milestones, executes over days/weeks, learns from what works, reports progress without hand-holding. The framework orchestrates as a neutral role (the Conductor) and can optionally wear a persona (e.g. `personas/poe.md`). User's job: mission definition + exception handling.

North star: self-improving, autonomous agent. Visible → Reliable → Replayable.
Jeremy named it (2026-07-21): **CGI — capable general intelligence** — *"I don't
want a slave mind or to create artificial life; I want something as capable as
me as a workhorse in the digital space."*

---

## Architecture (5 subsystems)

See `docs/ARCHITECTURE_OVERVIEW.md` for the full map with intent-vs-implementation gaps. (The older, longer `ARCHITECTURE.md` is a point-in-time record in `docs/history/` — pre-rename era, don't treat it as current.)

| Subsystem | What | Key files | Skill |
|-----------|------|-----------|-------|
| **Interface** | Goal entry, classification, routing | handle.py, intent.py, director.py, workers.py, persona.py | `skills/arch-interface-routing.md` |
| **Core Loop** | Decompose → execute → introspect | agent_loop.py, planner.py, step_exec.py, pre_flight.py | `skills/arch-core-loop.md` |
| **Memory/Knowledge** | Recording, retrieval, crystallization | memory.py, knowledge_web.py, knowledge_lens.py, memory_ledger.py | `skills/arch-memory-knowledge.md` |
| **Quality + Self-Improvement** | Validation AND getting better over time | inspector.py, evolver.py, graduation.py, introspect.py, skills.py | `skills/arch-quality-selfimprove.md` |
| **Platform** | LLM adapters, config, heartbeat, projects, tasks, metrics | llm.py, config.py, heartbeat.py, orch_items.py, task_store.py | `skills/arch-platform.md` |

**Two things, often conflated:**
- **Maro-as-tool**: Execute tasks autonomously. *Works today.*
- **Maro-as-self-improving-system**: Detect friction → change behavior → verify it worked → learn. *Infrastructure 80% built; verify→learn loop not closed.*

---

## Repo layout

```
src/                 All production Python (~170 flat modules; REFACTOR_PLAN Tier 4 = subpackage plan)
  agent_loop.py      Core loop entry (physical phases split into loop_*.py modules)
  handle.py          Entry point — routes to NOW or AGENDA lane
  intent.py          Goal classifier (NOW vs AGENDA)
  director.py        Director: plans, delegates, reviews
  workers.py         Workers: research / build / ops / general
  inspector.py       Quality gates — friction detection
  evolver.py         Meta-improvement every ~10 heartbeats
  memory.py          Outcome recording, lesson extraction, Reflexion
  skills.py          Skill library: auto-promote, score, test
  introspect.py      Phases 44–46: failure classifier, lenses, recovery planner, intervention graduation (DONE)
  llm.py             LLM adapter suite (Anthropic, OpenRouter, OpenAI, subprocess)
  web_fetch.py       Jina Reader + X/tweet fetching (Phase 30 — token saver)
  metrics.py         Cost + token tracking per model
  persona.py         Persona system — modular agent identities
  constraint.py      Pre-execution constraint enforcement
  ...

tests/               pytest suite (run via bash scripts/test-safe.sh; counts change weekly; the invariant is zero UNCONDITIONAL skips — platform/feature-gated skipifs (Darwin-only probe, docker-gated e2e, corpus-gated) are the only allowed kind, so expect ~1 skip on this box)
scripts/             smoke.sh, audit-phases.sh, enqueue.sh
personas/            YAML persona specs
docs/                Architecture, memory systems, self-reflection design
lat.md/              Knowledge graph: 9 cross-linked concept nodes + index
memory/              Repo-local: stale pre-2026-08-06 copies (nothing writes here since resolution unification). Real data is in ~/.maro/workspace/memory/
output/              Repo-local output (real output in ~/.maro/workspace/output/)
research/            Research outputs: X link synthesis, Polymarket validation, Phase 41 design
user/                Neutral operator-doc templates (GOALS, CONFIG, CONTEXT, SIGNALS, COMPLETION_STANDARD);
                     real files live in ~/.maro/workspace/user/ (overlay wins) — see user/README.md
personas/poe.md      Optional Poe persona (the framework defaults to a neutral role)
deploy/              systemd service files
```

---

## Current state

**This file does not track current state — by design.** Current truth lives in GOAL_BRAIN.md (compiled truth + decisions), MILESTONES.md (queue), and BACKLOG.md. Phase history: `docs/history/` (ROADMAP_ARCHIVE for completed phases). A snapshot here rots — the 2026-04-14 snapshot that used to live in this section sat stale for months claiming to be current.

Prototype-era steal-list research (all items long since shipped) is recorded in `docs/history/` (steal-list + sources). The old prototype at `~/.openclaw/workspace/prototypes/poe-orchestrator/` is reference only; do not develop there.

---

## Where things live on this machine

| Path | What |
|------|------|
| `/home/clawd/claude/maro-orchestration/` | **This repo — mainline** |
| `~/.openclaw/workspace/` | OpenClaw system (GPT/Codex-based). Has SOUL.md, TASKS.md, AGENTS.md, GOALS.md |
| `~/.openclaw/workspace/prototypes/poe-orchestrator/` | Old prototype — reference only, do not continue work here |
| `~/.openclaw/workspace/scripts/` | ~80 shell scripts: heartbeat, task queue, X/Telegram/email |
| `~/.claude/projects/.../memory/` | Claude Code persistent memory across sessions |
| `/home/clawd/.maro/workspace/` | **Stable runtime workspace** — all learning data, self-evolved artifacts, and runtime state. Not in git. |

**Workspace layout (`~/.maro/workspace/`):**

| Path | What | Written by |
|------|------|-----------|
| `memory/` | Outcomes, lessons, knowledge nodes, captain's log, diagnoses | reflect_and_record, learning pipeline |
| `skills/` | Self-created/evolved skill .md files (override repo skills) | evolver |
| `personas/` | Self-created/evolved persona specs (override repo personas) | evolver |
| `playbook.md` | Director's operational wisdom (auto-maintained) | evolver, append_to_playbook() |
| `output/` | Run artifacts, operator status, research outputs | agent_loop, orch |
| `projects/` | Per-project NEXT.md, decisions, risks | orch_items |
| `config.yml` | Workspace-level config | manual |

**Resolution order** for skills and personas: workspace → repo. When the system evolves a better version of a shipped skill/persona, the workspace version wins. Repo versions are the shipped defaults.

---

## Configuration

Two-tier YAML config (like git's `~/.gitconfig` vs `.git/config`):

| File | Scope | What goes here |
|------|-------|---------------|
| `~/.maro/config.yml` | User-level | Model prefs, notifications (API keys stay in env or `secrets/.env`; `yolo` lives in `user/CONFIG.md` / `MARO_YOLO`) |
| `~/.maro/workspace/config.yml` | Workspace-level | Evolver, inspector thresholds, constraint settings, quality gate |

Workspace inherits from user; workspace keys override. Nested dicts merge one level deep.

Access in code: `from config import get; get("inspector.breach_threshold", 0.30)`

Priority: env var > config.yml > hardcoded default. Tests are isolated (config reads from tmp paths).

---

## Running things

```bash
# Tests — targeted (safe to run alongside TUI)
cd /home/clawd/claude/maro-orchestration
python3 -m pytest tests/test_agent_loop.py -q

# Tests — full suite (use this one — caps CPU to 2 cores + nice 15)
# Runs ONE parallel pytest sized to the core budget: 2 workers pinned to the
# 2 allowed cores here, `auto` on an unrestricted host. Won't tip over the box.
bash scripts/test-safe.sh

# Fast feedback lane (explicitly skips @pytest.mark.slow)
bash scripts/test-safe.sh --fast

# Force the old sequential chunked path (ordering-dependent failure hunting)
bash scripts/test-safe.sh --jobs 1

# Tests — full suite, raw (only when the box is idle / no TUI running)
python3 -m pytest tests/ -q          # serial, ~2m
python3 -m pytest tests/ -q -n auto  # parallel, ~20s on an 8-core host

# Tests — with coverage (enforces 70% floor per .coveragerc)
bash scripts/test-cov.sh
bash scripts/test-cov.sh --html     # also produce output/coverage_html/

# Smoke
bash scripts/smoke.sh

# Phase audit
bash scripts/audit-phases.sh

# Run a goal (defaults to ~/.maro/workspace/ — no env vars needed)
cd /home/clawd/claude/maro-orchestration
PYTHONPATH=src python3 -m handle "your goal here"

# Introspection (Phase 44)
maro-introspect --latest
maro-introspect --latest --lenses
```

---

## Jeremy's communication style

- Says what he means once. If permission is granted, it's granted.
- "Sounds good" = execute now. "Keep going" = stop pausing.
- Frustrated by: re-asking for permission, plans presented as work, option tables when action suffices.
- Values: honest "tried X, failed, learned Y, trying Z" updates. Progress over perfection.

Act, don't ask. Forgiveness over permission. Ask first only for: spending real money, posting publicly as Jeremy, destructive irreversible actions, exposing private data.

---

## Concurrent sessions — shared tree rules

Multiple Claude sessions often run in this checkout at once. `git status`
showing files you didn't touch means another session is mid-chunk. Rules,
learned the hard way (2026-07-29: an `--autostash` rebase in the shared
tree while another session kept writing wedged the sequencer and left a
detached HEAD):

1. **Never run tree-mutating git ops (rebase, checkout, `reset
   --hard`/`--keep`, stash) in the shared tree while it holds another
   session's uncommitted work.** These rewrite working-tree files and
   assume a single actor. If you need one (push refused → rebase), do it
   in a worktree instead. `reset --mixed`/`--soft` and `branch -f` move
   refs only and are safe.
2. **Worktree when dirty:** if the tree is dirty with someone else's work
   and your chunk involves more than additive edits to your own files,
   take a worktree up front — `EnterWorktree` tool if available, else
   `git worktree add ../maro-wt-<slug> origin/main`. Work, commit, land
   with `scripts/land.sh` (ref-only push — worktree-safe by
   construction), then `git worktree remove` it. Worktrees share the
   object store; they're cheap. Note: a worktree can't check out `main`
   (the primary tree holds it) — work on a temp branch or detached from
   `origin/main`; land.sh doesn't care. After landing, converge the
   shared tree's stale `main` with `git fetch origin && git reset --mixed
   origin/main` — ref/index only, tree untouched. **Then materialize:**
   work landed from a worktree never updates the shared working tree —
   your new files show as `D`, your edits as stale `M`, and the live
   runtime (which runs from this checkout) keeps executing pre-land
   code (found 2026-07-29: the SSRF fix was landed but not live). For
   each dirty path, `git hash-object` it and compare against recent
   commits' blobs (`git rev-parse <commit>:<path>`): matches an
   ancestor → stale, safe to `git checkout -- <path>`; matches none →
   another session's real uncommitted work, leave it alone.
   **Run `scripts/tree-triage.sh` instead of doing that by hand** (`--fix`
   restores only the stale paths; a third verdict BEHIND — tree copy
   matches no ancestor but is deletion-only vs HEAD, the stale-MIX shape
   a rebase-replay leaves — is reported with a blame summary and only
   restored via explicit `--fix-behind`, found 2026-08-15). A dirty path is a TWO-valued signal and
   the failure mode is reading it as one-valued: 2026-08-06, a session saw
   three files dirty, correctly concluded "someone is mid-chunk, stay
   away" — and later committed them, reverting an executor tag scheme and
   a pytest-in-image change that had already landed and been pushed. The
   instinct was right; the premise was wrong, and nothing made the branch
   point visible. A worktree does not cover this: it protects work you are
   *writing*, not work you are *based on*.
3. In the shared tree: stage explicit paths only (never `git add -A` /
   `git commit -a`), eyeball `git status` for strangers before every
   commit, and don't touch shared living docs (GOAL_BRAIN.md, BACKLOG.md,
   DEV_LOG.md) in a commit while another session has them dirty — your
   staged copy would carry their half-written hunks.
4. **Resolving a conflict is the work. Do it; do not route around it.**
   Jeremy, 2026-08-16: *"branch or no, a rebase + conflict resolution +
   FF merge is the answer here. no mechanism will save you from the
   conflict resolution work... you can mask it to pretend it doesn't
   exist, but it's going to be there one way or another."* The sequence,
   every time:

   ```
   git fetch
   git rebase origin/main        # in a worktree if the shared tree is dirty
   # resolve: read BOTH sides, keep both, then VERIFY both survive
   git push origin HEAD:main     # ff-only (scripts/land.sh)
   ```

   **Verify, don't assume:** after resolving, grep the result for a
   distinctive string from each side. A resolution that silently keeps
   one side looks exactly like a clean merge in `git status`.

   For GENERATED files (docs/DEV_STATUS.md, any managed block) the
   resolution is neither side: regenerate. For narrative/ledger files
   with two real authors, there is no shortcut — read both, keep both.

   **The failure this rule is written from (2026-08-16, `fff8606`):** a
   session squashed WIP commits with `git reset --soft <base>` + `git add
   -A` over a working tree left stale by an earlier worktree land. No
   conflict ever appeared — `add -A` simply staged an OLD copy of
   `tests/mutation/README.md`, and git recorded the difference as
   deletions of content that was in its own parent commit. Three sections
   and two ledger rows were lost and had to be restored by another
   session (`7a2f685`). The lesson is not "conflicts are dangerous", it
   is that the pattern used to AVOID rebasing is what destroyed content.
   Two habits follow: never `git add -A` (rule 3, violated repeatedly
   that day), and never `reset --soft` to squash — land the series, or
   make one commit to begin with.

5. Landing races are fine: land.sh is ff-only, and since 2026-08-16 a
   lost race is handled for you — land.sh replays your commits onto
   fresh origin/main **in a temp worktree** (never this tree), lands
   the replayed sha, converges your ref (ref/index only), and runs
   tree-triage --fix to materialize upstream paths. Conflicts abort to
   manual with the recipe printed; `--no-rebase` restores the old
   refuse-outright behavior.

## End-of-chunk discipline

When a chunk of work is done (milestone delivered, bug fixed, feature shipped — not every tiny edit), always:

1. **Document** — update MILESTONES.md / BACKLOG.md / relevant docs so the next session knows what changed and what's next. Run
   **`PYTHONPATH=src python3 src/cli.py dev-status --write`** and commit the
   regenerated `docs/DEV_STATUS.md` — it is one command, and it
   is the surface that answers "where are we?" without re-deriving it from the
   backlog (which is a findings log and grows forever by design). `land.sh`
   prints the same one-line summary after every push and says when the written
   block has drifted. If a landed doc needs Jeremy's read for a decision, add a row to `docs/READING_QUEUE.md` in the same commit (it renders to the viz server's Reading tab).
2. **Commit** — clean, scoped commit with a useful message. No "WIP" or dangling work.
3. **Land** — get it onto `main`. On the maro box, once tests are green, land your
   directed work directly with **`bash scripts/land.sh`** (fast-forwards `main`
   over SSH — no PR, no GitHub API token). Don't leave a finished chunk sitting
   on a branch waiting for a human to merge it; a box crash loses unlanded work.

**Landing policy (Jeremy, 2026-07-20 — "PRs for Poe; maro box continues as before"):**
The PR-and-human-review gate is the **Poe/Hermes** lane only (mini2 dispatched-
autonomous work — `deploy/hermes/`, `PROPOSE_LANE.md`), where an agent that could
modify its own orchestration must have a human in the loop. The **maro box lands
its own directed work directly to main** — you're already the human in the loop
here. `scripts/land.sh` is ff-only and never force-pushes main, so it's safe
alongside concurrent sessions. SSH push is the landing credential; the `gh`
token was re-minted 2026-08-01 (after a dead spell since ~07-17), which powers
land.sh's post-land CI watch (`scripts/ci-watch.sh` — Telegram-pings on a red
Actions conclusion, silent otherwise). PRs stay the Poe/Hermes lane only.

Don't wait to be asked. Landing is cheap, forgetting is expensive.

**Session-close rule (SF-13, standing since 2026-07-09):** any Jeremy
statement worth an auto-memory write also gets a GOAL_BRAIN.md Decisions
line before the session ends — even when the conversation produced no work
chunk. Decree-class statements must reach the compiled record, not just
Claude's memory; a session that ends conversationally is the exact case
this rule exists for. Since 2026-07-21 (swarm-review chunk 3) the same
decree also gets piped into the RUNTIME decision journal so recall can
surface it to runs:
`PYTHONPATH=src python3 -m knowledge_lens decision "<decree>" --rationale "<why>"`.

**Capability-capture rule (2026-07-11, Jeremy):** when work surfaces a
real ask or a missing capability mid-session — a user-shaped request, a
run failure that names a skill we don't have, a "we should be able to
just ask it X" moment — capture it in `docs/CAPABILITIES.md` as-phrased
(with tier + verified/target/aspirational mark) in the same session.
This is the middle ground between testing and backlog Jeremy asked for:
more concrete than an idea, not yet a test goal. Don't wait for a
dedicated capabilities pass; the phrasing is the value and it evaporates.
