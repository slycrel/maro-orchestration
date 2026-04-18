# Intent Resolution — the shape we've been circling

**Status:** exploratory sketch. Not a shipping design. Written 2026-04-18 after
run 7 of slycrel-go exposed (again) that our "done" signal doesn't mean
"goal delivered" — it means "the plan we guessed up front got executed."

## The problem, clearly

A user goal has two parts:

1. **Stated deliverables** — what they said: "create a branch for a headless
   server with the browser as a client."
2. **Unstated dependencies** — what has to be true for the deliverable to
   actually be useful: a browser client exists, a test user can log in, the
   wire format is documented, the server runs.

Today the orchestrator treats the goal as a 1-shot decomposition: parse →
plan → execute → check. It has no phase for "what do I not know yet, and
what do I need to find out before I can plan honestly?" The closure check
was intended to close this gap — and it partially does, by asking "did the
deliverables land?" — but the scope it checks against was itself a guess,
so it can only catch gaps the planner was already worried about.

The "learn a language to draw a 3-word kanji" example is the clearest shape:

- Stated: "draw this kanji."
- Unstated: to draw it correctly you have to know what the characters mean,
  which means knowing which language, which script conventions, which
  stroke order, which tool, which medium. Each of those is itself a
  side-quest with its own unknowns.

A human doing this naturally alternates between:

- probing unknowns ("is this kanji traditional Chinese or simplified?
  different stroke order")
- committing partial work that depends on resolved unknowns
- occasionally backtracking when a probe reveals the original plan was
  wrong

The orchestrator does none of this. It runs the straight line.

## What we already have (and why it's not the shape)

We keep building *pieces* that nibble at this and stopping there.

| Piece | What it does | Why it isn't the shape |
|---|---|---|
| `scope.py` | Inverts the goal for failure modes + in/out-of-scope | Still a 1-shot LLM guess. No probing. |
| Closure check (`director.verify_goal_completion`) | Asks "did checks pass?" after execution | Checks are greps against plan-time assumptions. Can't catch "we built the wrong thing." |
| Inversion in adversarial review | Surfaces contested claims post-hoc | Runs *after* done, not before planning. |
| Ralph loops | Retry-until-passes | Only handles *known* failure modes. If the unknown was "no browser client exists" it can't retry that into existence. |
| Director restart (continuation_depth) | Respawns loop with injected gap context | Injects at planner level; planner still does 1-shot decomposition. |

Each piece is a feature added *around* a decomposition. The shape we
haven't built is: **delay decomposition until intent-resolution
side-quests have settled the unknowns.**

## The shape, sketched

Three structural phases, not one:

### 1. Intent resolution (new phase, before decompose)

Input: goal text + available workspace/context.

Output: a **resolved-intent artifact** that names:

- **Assumed** — what we're taking for granted without checking (with
  reasons).
- **Verified** — what we confirmed via a probe.
- **Unknown-but-accepted** — risks we're proceeding with eyes open.
- **Deliverable map** — concrete artifacts the goal implies, with their
  preconditions.

This phase can spawn **side-quests**: self-contained sub-goals whose only
purpose is to resolve one unknown. Each side-quest has its own scope,
execution, and closure — but its success criterion is "the unknown is
answered," not "deliverable produced."

### 2. Side-quest DAG (conditional)

If resolution produced side-quests, execute them (possibly in parallel)
and merge their outputs back into the resolved-intent artifact. Side-quest
outputs are **first-class artifacts** — they live in the workspace and are
reusable.

### 3. Main execution (what we have today)

Plan → decompose → execute → closure-check, but operating against a
resolved-intent artifact instead of a guessed one. Ralph loops belong
*inside* step execution (per the feedback memory on ralph-within-structure).

## Pivot handling — the "now make it pink" case

When the user twists the goal, the orchestrator should:

1. **Diff the new intent against the resolved-intent artifact.** What changed?
   What's still valid?
2. **Reuse side-quest outputs that still apply.** If we learned "this user
   wants a browser UI" via a probe, that's still true for the pink version.
3. **Spawn new side-quests only for the delta.** The pink-and-unicorns
   twist might need: "is there a unicorn asset? is pink acceptable as
   primary-palette or accent?"

This is close to Paperclip's goal ancestry + workspace persistence. The
workspace survives across runs; each run of "same goal family" inherits
the resolved-intent artifact and the side-quest artifacts.

## Why it's been hard to name

The pieces we've shipped are all *around* a decomposition. What's missing
is the *structural phase* that sits before decomposition and produces the
thing decomposition operates against. We keep trying to fix the decomposer
or the verifier, but the gap is upstream of both.

It's an unknown-unknown in the exact sense Jeremy described: we know the
shape is missing, but because we've spent our time building the pieces
adjacent to it, we don't have a precise spec for what to ask for. This
doc is the attempt to name it so the next iteration has a target.

## Minimum experiment (how to learn something before building)

Pick **one goal** from the blind-test set. Before running it:

1. Manually write a resolved-intent artifact: unknowns, probes, deliverable
   map.
2. For each unknown that needs probing, run a side-quest goal (existing
   handle.py path) and capture outputs.
3. Run the main goal with the resolved-intent + side-quest artifacts
   injected as ancestry context.
4. Compare output quality + closure verdict + adversarial review against
   the same goal run without the artifacts.

If quality improves measurably, the shape is worth building. If not, we've
learned the ceiling isn't here — maybe the bottleneck is in the
verification-sibling or the decomposer itself.

Cost of the experiment: ~1 day of manual work on one goal. Avoids
speccing an orchestration system before knowing the lift is real.

## What not to build yet

- Full intent-resolution LLM prompts (1-shot guess moved one level up
  isn't the point).
- A side-quest orchestration framework before we've run one by hand.
- A new set of captain's log events until the shape is validated.

## Relation to existing backlog

- **Phase 65** (constraint/scope orchestration) — resolved-intent artifact
  subsumes scope. Phase 65 is a subset of this shape, not an alternative.
- **Verification sibling** (Phase 65 review item) — closure against a
  resolved-intent artifact is stronger than closure against a guessed one.
  Same hole, different entry point.
- **Pivot reuse / workspace persistence** — already surfaces in
  `polymarket-edges` pattern (memory: project_polymarket_edges.md). That
  pattern is the proof-of-concept for persistent workspaces; this
  generalizes it.

## What I'd want Jeremy to push back on

- Is this the right shape, or am I drawing a larger box around pieces we
  already have without adding new structure?
- Is the minimum experiment *minimum* enough, or should it be even smaller
  (e.g. just the resolved-intent artifact, no side-quests yet)?
- Are there existing goals in the backlog whose completion is gated on
  this shape existing? If yes, priority goes up.
