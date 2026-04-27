# Thread Architecture

**Status:** Active design space. Sketched 2026-04-26 → 2026-04-27 in a planning-mode conversation between Jeremy and Claude. No implementation yet — this doc is the reference + decision log + open-question list. Branch: `arch/thread-navigator`.

**Companion docs:**
- `docs/conversations/2026-04-26-thread-architecture.md` — literal conversation transcript that produced this doc. Read it for tone, examples, and where each idea came from.
- `docs/DRIVER_AND_WATCHER.md` — the prior framing that named the gap. Most of what's here sharpens or revises that.
- `docs/INTENT_RESOLUTION_DESIGN.md` — the side-quest / resolved-intent sketch. Folds into "sub-threads" here.
- `docs/CONSTRAINT_ORCHESTRATION_DESIGN.md` — scope-as-pre-plan-armor; partially absorbed and partially superseded.
- `docs/KNOWLEDGE_CRYSTALLIZATION.md` — Stages 1–5 (Fluid → Lesson → Identity → Skill → Rule). The navigator's improvement path *is* this.
- `docs/MEMORY_ARCHITECTURE.md` — three memory types (episodic / procedural / identity); the recall() interface here sits on top of those.

---

## The unifying noun: **thread**

Every interaction with Poe is a **thread**. Threads have a beginning (the user opens one), a body (turns accumulate), and a closing (a deliverable lands or the thread is set aside). Threads can fork into sub-threads.

The thread is the unit of orchestration. Today's "goals," "loops," "missions," "tasks," "plans," "executions" are all *shapes a thread can take*, not separate primitives.

**Three modes the user originally named** collapse into one mechanism:
- **1-shot** = thread of length 1 that closed cleanly
- **2-prompt plan + execute** = thread whose first turn emitted a plan-shaped artifact, then expanded into N children
- **Meandering stream** = thread with no closed shape yet, extending and forking as it goes

Same machine, different lifecycle states.

---

## Per-turn loop

Every turn of every thread is the same shape:

```
navigator → work → navigator
```

- **Navigator decides what to do** (extend the thread, execute work, fork a sub-thread, close the thread, escalate).
- **Work LLM does it** — either a planning move or an execution move. Returns a result + a recommendation.
- **Navigator absorbs the result** and either closes the turn or moves on to the next one.

This collapses the algorithm-selection question from `DRIVER_AND_WATCHER.md`: there's no upfront algorithm decision, the navigator chooses each turn.

---

## The navigator

**Role:** the authoritative decider for "what happens next in this thread."

**Substrate (where it runs):**
- **Today / starting point:** a separate cheap-LLM call, Haiku-class. Hard-prompted to make the decision.
- **Over time:** patterns crystallize per `docs/KNOWLEDGE_CRYSTALLIZATION.md` Stages 1–5 — fluid prompt → lesson-injected prompt → identity → skill → rule. The navigator gets cheaper for stable patterns over time.
- **Long-arc aspiration:** a tiny custom LLM trained on Poe's own navigation history. Speculative, baby-steps later.

**Decisions it picks from each turn:** *extend, execute, fork, collate, close, escalate.* (Names are working; will sharpen with the prompt.)

**Inputs (working list, will firm up when we draft the prompt):**
- The thread's build folder (current state, prior turns, accumulated artifacts).
- Memory recall against the thread state (see Memory section).
- The work LLM's last result + recommendation (if any).
- Ancestry / parent-thread context if this is a sub-thread.

**Authority:** the navigator's choice wins. The work LLM's recommendation is **data**, not authority. The work LLM is zoomed in on its task; the navigator holds the wider context (ancestry, scope, memory, constraints).

**Tiered escalation — "idunno":**
The navigator can return *idunno*. That doesn't go straight to Director or user. It re-runs the navigator step at a higher tier (Haiku → Sonnet → Opus). Only after the highest tier still can't decide does it escalate to Director / human. The whole step's output is treated as data and reconsidered at higher tiers — same input, more horsepower.

This means most navigation is cheap-tier; expensive tier only fires when the cheap one explicitly admits it can't.

---

## The work LLM

**Role:** does the actual work. Either:
- **Plan** — produces an artifact: a plan, a scope, a resolved-intent, an open-questions list, a refinement.
- **Execute** — does the thing: runs a tool, writes code, fetches a page, sends a message.

Returns:
- The result.
- A recommendation for next move ("done," "I need X," "this should fork").

The recommendation is consumed by the navigator. The work LLM does not decide the next turn — it advises.

**Persona-dressed:** see Personas section. The work LLM is invoked with whatever persona the navigator chose for this turn, on whatever model tier the persona warrants.

---

## Personas as navigator-selected (key reframe)

Today personas are static YAML, manually selected via prefixes (`garrytan:`, `direct:`, etc.). The architecture overview already flags this as drift: "personas aren't auto-selected based on goal type."

**In this architecture, persona is a navigation primitive.** The navigator picks per-turn:
- "This turn is research-into-X — use the research-assistant persona on Sonnet."
- "This turn is ad-copy refinement — use the marketer persona on Opus."
- "This turn is reading a config file — use a minimal persona on Haiku."

Personas are *perspectives + context bundles + tool affordances* the navigator dresses the work LLM in. They constrain the search space the work LLM operates over (a marketer doesn't try to write production Python; a researcher doesn't try to optimize ad spend).

**Persona library shape:** open question. Today curated YAML. Future could be navigator-evolved (skill+persona creation as part of self-improvement). Jeremy's gut: 5–10 core personas/skills used heavily, evolving over time.

---

## Director's reduced role

Director becomes a **two-callsite escalation surface**, not the engine:

1. **Thread kickoff:** when a user opens a thread, Director can do initial intent-disambiguation — the `/make-me-rich` example from `DRIVER_AND_WATCHER.md`. Three targeted questions to separate high-variance branches. Hands a resolved-intent (or an explicit "go on the literal interpretation") to the navigator.

2. **Navigator escalation:** when the navigator returns *escalate* — genuine ambiguity, conflicting goals, irreversible decision, exhausted tiered idunno chain. Director surfaces to user via channel.

**Routine per-turn navigation does not pass through Director.** Today's `skip_if_simple=True` (which de-facto bypasses Director) becomes the explicit pattern: navigator runs, Director only on edge cases.

---

## Sub-threads and collation

When a turn's work is a collection — "check Reddit, Facebook Marketplace, and Craigslist for item X" — the navigator picks **fork**:

- Spawns N child threads, each with its own build folder, navigator, persona.
- Children execute (possibly in parallel).
- When all return, parent navigator picks **collate** — fires a work-LLM turn that consumes the N artifacts and produces a synthesized one.
- Collation is itself a planned/executed turn that can fail, retry, or further fork.

This handles:
- The reddit/marketplace/craigslist fan-out.
- The kanji recursion (sub-threads acquire knowledge before parent execution; results merge into parent's resolved-intent).
- The side-quest DAG from `INTENT_RESOLUTION_DESIGN.md`.

Same mechanism end-to-end — recursion is just thread structure.

---

## The build folder

**Every thread lives in a build folder.** Source / build / artifact.

This is already the skeleton in `src/runs.py` (shipped 2026-04-25/26 as transparency artifact). The architecture promotes it from "where transparency lands" to **"where the thread actually resides."**

```
~/.poe/workspace/runs/<handle_id>-<nickname>/
  source/      # original goal, user context, scope.md, resolved_intent.md
  build/       # plans, scratchpads, captains_log_slice, intermediate artifacts
  artifact/    # final deliverables, repo bundles, output the user gets
  children/    # sub-thread folders (recursive structure)
```

**Programmer mental model:** source files (initial goal + context). Intermediate object files (sub-thread artifacts, scope, deliverables, log slices). Linker (the navigator) ties it together at close time. Output (artifact directory) is what ships.

The build folder is durable. Threads can be reopened, forked, or referenced. Closure = artifact written.

---

## Memory: three layers + one swappable read seam

Three layers, already named in `docs/MEMORY_ARCHITECTURE.md`:

| Layer | What | Storage today |
|---|---|---|
| **Episodic** | What happened, lessons learned | medium/long-tier lessons, captain's log |
| **Procedural** | How to do things | skills library, eventually crystallized rules |
| **Identity** | Who Poe is | AGENTS.md / system prompt |

The architecture doesn't change these. It adds **one unified read interface**:

```python
recall(thread_state) -> list[relevant_artifact]
```

One function the navigator (and possibly the work LLM via context injection) calls. Behind it: TF-IDF, vector retrieval, graveyard re-search, lat.md graph traversal, captain's log targeted slice, correspondence (Mage-sphere adjacency) — composed however we want, swappable behind the signature.

**Why one seam:** today these substrates exist (memory.py, knowledge_web.py, knowledge_lens.py, correspondence.py for dev) but are read piecemeal at different injection sites. The navigator needs a unified "what do I know that's relevant *right now*." Without the seam, every navigator decision has to know about every memory backend.

**Correspondence** (per `feedback_inference_not_prompting` and the project_correspondence_meaning memory) is both:
- Literal: vector retrieval over docs/conversations/lessons.
- Mage-sphere: adjacency between things that don't know they're related.

Today correspondence is dev-recall only. The runtime analog (Poe querying its own correspondence) is the unbuilt cross-cutting concern from the architecture overview. The recall() interface is where it lives.

**Portability requirement** (Jeremy 2026-04-27): self-learned artifacts should be portable across orchestrators. Lose the HDD, restore from backup, keep going. Skills (.md), personas (YAML), lessons (JSONL) already are. **Stage 5 rules** (compiled Python) need to either stay portable (declarative form?) or be regenerable from skill artifacts.

---

## Captain's log: visibility, not infrastructure

The captain's log was originally designed as **human-readable commentary alongside the thread** — visibility for the user, narrative for what's happening. It got co-opted as system infrastructure (events, structured records, lifecycle hooks).

**Demote it back to visibility / data.** It can stay useful as:
- Human-readable thread narrative.
- Auxiliary data for inspection / debugging / replay.
- Input to recall() (one substrate among several).

It should **not** be the wire that carries system decisions. Anything that's currently relying on captain's log as the source of truth needs another path. (Audit needed; not done yet.)

---

## Crystallization as the navigator's improvement path

The navigator gets cheaper over time via `docs/KNOWLEDGE_CRYSTALLIZATION.md` Stages 1–5:

```
Stage 1: Fluid           — full LLM reasoning on every navigator turn (power tier)
Stage 2: Lesson          — patterns extracted, injected into navigator prompt (cheaper tier viable)
Stage 3: Identity        — promoted to AGENTS.md-level, always-active (no retrieval)
Stage 4: Skill           — deterministic enough to express as code (sandboxed Python)
Stage 5: Rule            — hardcoded path, zero inference (lookup, conditional, dispatch)
```

This is the **prompt → skill → script** hardening Jeremy mentioned and worried had been left behind. It's documented; what's missing is the active machinery to graduate things automatically (Stage 5 path is unimplemented; Stage 4→5 is conceptual only).

**The navigator is the primary consumer of crystallization:** stable navigation patterns (e.g., "marketing-keyword research goals always fork into 3 sub-threads then collate") harden through the stages. Each crystallization removes a class of decisions from the LLM-priced critical path.

**Self-improvement requires the verify→learn loop closure** that the architecture overview flags as broken. Designing the navigator without designing how it improves means we ship a smart-but-static navigator and re-discover the same gap. Closing that loop is part of this design space, not adjacent to it.

---

## What this architecture preserves, shifts, and might shrink

| Today | Tomorrow | Note |
|---|---|---|
| `handle.py` classifies NOW vs AGENDA upfront | Navigator decides per-turn; classification is implicit | NOW = single-turn thread that closed; AGENDA = thread with sub-structure |
| Director plans the whole thing | Director is escalation + thread-kickoff only | Matches today's `skip_if_simple=True` de-facto state |
| `planner.decompose()` upfront | Decomposition is one move-shape the navigator picks | "Fork-with-N-children" |
| `agent_loop.run_agent_loop()` is the one algorithm | Loop becomes `while thread_open: navigator.next_move()` | Algorithm selection per turn |
| Persona via prefix / manual | Navigator picks persona per turn | Closes existing drift |
| Captain's log is infrastructure | Captain's log is visibility/data | Audit needed |
| Build folder = transparency artifact | Build folder = thread residence | Promotes runs.py |
| Crystallization Stage 5 unimplemented | Stage 5 is how the navigator gets cheap | Unblocks self-improvement |
| Recall is fragmented | One `recall()` seam | Multiple substrates compose behind it |

**On "does this delete upfront planning?":** No. Jeremy pushed back on my earlier framing. The Tesla analogy: confident-sounding LLM ideas without critical-thinking-about-edges leads to drift, because **people's context ≠ LLM context**. Some upfront planning is critical-thinking-forcing — even when not strictly necessary, it makes the user think and bridges the context gap.

The navigator doesn't *force* planning on every thread (today's bug). It also doesn't *delete* planning — it picks planning when warranted. A lot of today's planning scaffolding (`decomposition_too_broad`, mid-loop redecompose, scope-as-armor) may shrink because it was patches on forced upfront planning. But the *capability to plan when warranted* stays first-class.

---

## Open decisions (revisit list)

These are the questions we deferred. Ordered roughly by load-bearing-ness.

1. **Navigator's prompt + decision schema.** The single most load-bearing artifact. State it sees, decisions it returns, return-shape for *idunno* vs concrete moves, how it represents thread state compactly. Drafting this will force concrete answers to a lot of the rest.

2. **How forks rejoin.** Sync vs async. Failure semantics (one child fails → parent retries that one? abandons? promotes failure?). Partial-collate when only some children return. Probably needs a couple of worked examples (kanji, reddit/marketplace) before pinning.

3. **Recall() interface signature.** Pin it before building. What it takes as input (thread state, current turn, query?). What it returns (ranked list with provenance? typed artifacts?). Behind it can swap; the seam shouldn't.

4. **Persona library shape.** Fixed curated set vs. navigator-evolved. Today YAML + prefix selection. If navigator picks per-turn, what's the registry? Skill+persona creation as part of self-improvement is a real concern (Jeremy: 5–10 core, evolving).

5. **When upfront planning is appropriate vs. skipped.** The Tesla edge case. Heuristics that distinguish "user has thought about this" (drive ourselves) from "user wants to be driven there" (Tesla mode). Not a binary; probably a navigator-judged scale.

6. **How the navigator improves.** Tied to verify→learn loop closure (currently broken). Crystallization Stages 1→5 is the *what*; the *how* (data flow, attribution, when patterns harden) needs design.

7. **Captain's-log demotion audit.** What's currently relying on captain's log as infrastructure that needs another path? Probably touches inspector, evolver, structured event listeners.

8. **Stage 5 portability.** If rules are Python code, how do they survive HDD loss / orchestrator switch? Declarative form? Always-regenerable from skill artifacts?

9. **`/loop` and similar streaming primitives.** How "always-on" + "long-running" + "user-paced" (Telegram threads, chat, async) interact with the per-turn navigator model. Probably fine; worth a worked example.

---

## How to pick this up cold (new session)

1. Read this doc top to bottom.
2. Read `docs/conversations/2026-04-26-thread-architecture.md` for the tone and examples that produced it.
3. Skim the companion docs at the top — they're the prior framings this synthesizes.
4. Check the **Open decisions** list. The session that picks this up should pick one to draft, not implement multiple at once.
5. If implementing, the **Navigator's prompt + decision schema** (Open Decision #1) is the first thing — drafting it forces almost everything else into focus. Do it as a `docs/THREAD_ARCHITECTURE_NAVIGATOR_PROMPT.md` companion, not in this doc.
6. Branch is `arch/thread-navigator`. Don't merge to main until at least the navigator prompt + one worked thread end-to-end is reviewed.

---

## Provenance / what's settled vs. fresh

**Decided in conversation 2026-04-26 → 2026-04-27:**
- Thread is the unit
- Navigator (cheap LLM) is authoritative; work LLM proposes
- Persona is navigator-selected per turn
- Director is escalation + thread-kickoff only
- Sub-threads via fork+collate
- Build folder = thread residence
- Captain's log demoted to visibility
- Tiered idunno escalation in navigator
- Don't ditch upfront planning (Tesla pushback)
- Skill+persona creation needed for self-learning
- Self-learned artifacts should be portable across orchestrators

**Not yet decided** (Open Decisions list above).

**Pre-existing material this builds on** (worth re-reading):
- `DRIVER_AND_WATCHER.md` — the gap framing
- `INTENT_RESOLUTION_DESIGN.md` — sub-threads, resolved-intent
- `KNOWLEDGE_CRYSTALLIZATION.md` — Stages 1–5
- `MEMORY_ARCHITECTURE.md` — three memory types
- The `runs.py` source/build/artifact tree (shipped) — the build-folder skeleton
- `feedback_inference_not_prompting` (memory) — don't patch with taxonomies
