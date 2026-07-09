---
status: record
---

# Thread Architecture — Open Decisions Brief (2026-07-09)

**For:** Jeremy. **Decision status: NOT YET DECIDED — this is a triage brief, not a verdict.**
Drafted on the Mac dev checkout (`/Users/jeremy/claude/openclaw-orchestration`), which is
**not** where most of the sessions that produced `docs/THREAD_ARCHITECTURE.md` ran — per this
repo's own `CLAUDE.md`, the runtime host is the headless Ubuntu box. **Before taking these to
Jeremy for real, re-verify this brief's "what shipped since" claims against a session on that
box** — it holds fuller history (captain's log, prior conversations, possibly newer code) than
this dev checkout does. Treat this doc as a starting point for that session, not a finished
input.

Full record: `docs/THREAD_ARCHITECTURE.md` (the design doc + original open-decisions list) and
`docs/conversations/2026-04-26-thread-architecture.md` (the transcript that produced it).

---

## Why this brief exists

`BACKLOG.md`'s "DESIGN SPACE — Thread Architecture" entry (2026-04-26 sketch) says the *narrow*
navigator shipped but the *full reframe* did not, and that the doc's "9 open decisions need
re-scoping against what shipped before any further implementation." Nobody had done that
re-scoping pass. This brief does a first pass at it from the Mac checkout, so a future session
doesn't start from zero.

## What's actually shipped vs. still a sketch

**Shipped (per `docs/THREAD_ARCHITECTURE.md` §"What this architecture preserves, shifts, and
might shrink" cross-referenced against code on this checkout):**
- Narrow navigator: dispatch + blocked-step judge (`src/navigator_shadow.py`)
- Per-thread goal brain, seeded at creation (`src/thread_brain.py`)
- Persona auto-selection (`persona_for_goal`)
- `recall()` interface (`src/recall.py`)
- Navigator decision schema — schema half (`docs/NAVIGATOR_SCHEMA.md`): v1 join is sync, failed
  children stay visible in `open_children`, partial-collate is legal, close must disposition
  every open child

**Not built:** the full per-turn `navigator → work → navigator` loop as *the* unit of
orchestration (thread replacing goal/loop/mission/task as primitives), sub-thread fork/collate
as a general mechanism (gated on MILESTONES #4 async fork join per `BACKLOG.md`), navigator
picking persona *per turn* (today: per-goal), captain's-log demotion from infrastructure to
pure visibility.

## Original 9 open decisions — status

| # | Decision | Status |
|---|---|---|
| 1 | Navigator's prompt + decision schema | **Half-resolved** — schema shipped (`NAVIGATOR_SCHEMA.md`); the prompt half (step 5) is still open |
| 2 | How forks rejoin (sync/async, failure semantics) | **Half-resolved** — schema layer shipped 2026-06-11; retry-vs-abandon policy remains navigator judgment, undesigned |
| 3 | Recall() interface signature | **Resolved** 2026-06-10 → `docs/RECALL_DESIGN.md` + `src/recall.py` |
| 4 | Persona library shape (fixed curated set vs. navigator-evolved) | **Open** — see below |
| 5 | When upfront planning is appropriate vs. skipped ("Tesla mode") | **Open** — see below |
| 6 | How the navigator improves | **Open** — see below |
| 7 | Captain's-log demotion audit | **Resolved** 2026-06-11 — one load-bearing use found and fixed; everything else confirmed visibility-only |
| 8 | Stage 5 rule portability | **Open** — see below |
| 9 | `/loop` and streaming primitives interaction | **Open** — see below |

## The 5 open decisions

### #4 — Persona library shape

Today: curated YAML, `persona_for_goal` auto-selects per-goal (not per-turn — that's still the
unbuilt full reframe). Open question: does the library stay a fixed curated set, or does it
become navigator-evolved (personas created/refined as part of self-improvement, alongside
skills)? Jeremy's prior lean in the design conversation: "5–10 core personas/skills used
heavily, evolving over time" — a middle ground, not purely static or purely generative.
**What a decision needs to settle:** whether persona creation goes through the same
graduation/evolver machinery skills already use (`src/evolver.py`, `src/graduation.py`), and
whether that's worth building now or waiting for operational pressure (a goal type with no good
persona fit showing up repeatedly).

### #5 — Upfront planning vs. "Tesla mode"

Not binary. The doc explicitly preserves upfront planning as one navigator-selectable move-shape,
not the default — Jeremy pushed back hard on deleting planning scaffolding (`decomposition_too_broad`,
mid-loop redecompose, scope-as-armor): "confident-sounding LLM ideas without critical-thinking-edges
drift, because people's context ≠ LLM context." **What a decision needs to settle:** concrete
heuristics the navigator uses to pick "drive ourselves" (user has clearly thought this through)
vs. "Tesla mode" (user wants to be driven, needs the plan-as-forcing-function). This is likely a
navigator-judged scale, not a rule — probably needs a few worked examples before it can be pinned,
similar to how fork-rejoin needed worked examples (kanji, reddit/marketplace) before its schema
landed.

### #6 — How the navigator improves

Tied directly to the verify→learn loop, which multiple docs (including `CLAUDE.md`'s own
subsystem table) flag as still not closed: "Infrastructure 80% built; verify→learn loop not
closed." Crystallization Stages 1–5 (`docs/KNOWLEDGE_CRYSTALLIZATION.md`) is the *what* — the
*how* (data flow: which navigator decisions get attributed to which outcomes, when a pattern is
stable enough to harden a stage) is undesigned. **What a decision needs to settle:** whether this
gets designed as part of the thread-architecture reframe, or whether it's actually the
prerequisite that has to land first (the doc itself says "designing the navigator without
designing how it improves means we ship a smart-but-static navigator and re-discover the same
gap" — i.e., this may not be decidable in isolation from the broader verify→learn work already
tracked elsewhere in the backlog).

### #8 — Stage 5 rule portability

Jeremy's portability requirement (2026-04-27): self-learned artifacts should survive HDD loss /
orchestrator switch. Skills (.md), personas (YAML), lessons (JSONL) already are portable. Stage 5
rules are compiled Python — not portable as-is. **What a decision needs to settle:** does Stage 5
move to a declarative form (data + interpreter, portable but loses "zero inference, pure code"
cheapness), or does it stay Python but become regenerable-from-skill-artifacts (portable via
regeneration, not via the artifact itself)? This has a real cost/durability tradeoff either way
and ties to the still-open Stage 4→5 promotion path (per `GOAL_BRAIN.md`'s open questions: demotion
only goes Stage 5→4 today, not the reverse promotion machinery either).

### #9 — `/loop` and streaming primitives interaction

How "always-on," "long-running," and "user-paced" (Telegram threads, chat, async, the `/loop`
skill itself) interact with a per-turn navigator model. The doc calls this "probably fine; worth
a worked example" — lowest-load-bearing of the 5, likely resolvable by tracing one real `/loop`
session against the per-turn model rather than needing a judgment call from Jeremy at all. Flagged
here for completeness, but a runtime-box session should try the trace first and only escalate if
it actually finds friction.

## What this brief deliberately does NOT do

It does not attempt to answer any of the 5 — that's Jeremy's call, informed by whatever
additional context the runtime-box session surfaces. It does not touch code. It does not
re-litigate the 4 already-resolved decisions.

## Where the verdict lands

When Jeremy actually decides these (in whole or in part), record decree-level calls in
`GOAL_BRAIN.md`'s Decisions section (dated, quoted, per the file's existing format — see the
2026-07-07 memory-architecture entries as the template) and update
`docs/THREAD_ARCHITECTURE.md`'s "Open decisions" list inline to mark each resolved with a
pointer to the rationale, matching how decisions #1, #2, #3, #7 are already annotated in that
doc.
