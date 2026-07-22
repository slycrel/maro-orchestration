---
status: dormant-design
---

# Compound Thinking — the self-surveying map (planning model)

> *"Named for Publius Vergilius **Maro** — the poet Virgil, who in Dante's*
> Inferno *guides the traveler down through the dark and safely out the other
> side."* — the project README. The name encodes this whole model: an exit
> believed-in but unseen, a descent through the dark (fog of war), a path found
> by pathfinding rather than known in advance, recovery past each obstacle, out
> the far side. We named the concept before we said it out loud.

**Status: EXPLORATORY design sketch.** Captured from a conversation between
Jeremy and Claude (Opus 4.8, 1M ctx) on 2026-07-21 → 07-22, on the dev M1
while the swarm-review arc (chunks 1–5) was still landing on the box. **No
decisions are ratified here.** This opens the next design spiral ("chunk 6")
and extends `INTENT_RESOLUTION_DESIGN.md`, `DEV_PATTERNS.md`, and the `star`
skill — it does not supersede them. GOAL_BRAIN.md remains the record of what
is actually decided.

> On-record framing worry (Jeremy): *"I hope we're not building the tower of
> babel here."* The guardrail is baked into the recommendations below: every
> block of this model maps to a mechanism maro already has or a metaphor that
> **bounds** it, and the recommendations are framed as **questions to answer**,
> not systems to build. Possible-now bias applies to this doc too — don't build
> capability the maps don't demand. The doc's success is convergence, not sprawl.

---

## 1. The thing we're naming

**Compound thinking** (Jeremy's term): the way a valuable result is reached by
**composing many small, individually-easy asks into a complex mechanism that is
not leap-able on its own** — a *meander*, not a leap. It is seeded by educated
guesses, background priors (training data, memory), and knowledge deliberately
built along the way (research tasks).

The atomic unit is the delegation-boundary loop from `DEV_PATTERNS.md`:

```
ask → taste (what to do) + judgement (did it do it) → result
```

Compound thinking stacks many of these, where each result does double duty: it
produces partial progress **and** refines the specification of what to ask next.

### Why "not leap-able" is about specification, not size

A compound goal is un-leap-able not because it is *big* but because **you cannot
write its spec up front** — the target is co-discovered with the path. Each small
ask sharpens the understanding of the goal at the same time it advances toward it.

Corollary with teeth for the router: **you cannot route your way out of a compound
goal with a bigger model** — the missing ingredient is specification, not
horsepower. The canonical Manti run is the evidence: naive routing "sent it NOW
(0.85 conf)" to a capable model and FAILED the Tier-1 contract with the
"passenger-does-the-steps" anti-pattern — a leap attempted on a compound goal. The
clean card only came from composing small asks (cuts → multi-source research →
closure).

---

## 2. Central metaphor: a plan is a self-surveying map

The linear plan (parse → plan → execute → check) is the wrong shape. The right
shape is a **map that draws itself as you explore it** — a pirate/treasure map,
not a route.

- **Landmarks (milestones), not steps.** There is no privileged start or end.
  There are **N landmarks, all the same type, all trying to connect**: the goal
  is a landmark, the current position is a landmark, a recalled milestone is a
  landmark, a guessed theory is a landmark planted in the dark. "The path to the
  goal" is just *whichever connected subgraph contains the pin labelled goal*.
- **Inverted confidence gradient.** On a pirate map you are sure of the
  *landmarks* and only guessing at the *dashed edges* between them — the opposite
  of a linear plan, which trusts its sequence. Milestones are high-confidence,
  low-resolution anchors; the routes between them are low-confidence hypotheses
  that need probing.
- **The "plan" is the string of landmarks, the dots between them, or both** — a
  graph refined continuously, not a sequence executed front-to-back.

### 2a. Tri-state fog of war

Overlaid on the landmark field is a **three-state fog**, and the middle state is
the hard one (Jeremy: "equally as annoying"):

1. **Undiscovered** — never observed.
2. **Last-known-state (grey fog)** — observed before, remembered, **but possibly
   stale**. A *cached observation with a decay* — and this is literally maro's
   memory-staleness problem (the "N-days-old, verify before asserting" stamp).
   Learned lessons live in this grey band: durably useful, never fully trusted,
   cheaper than re-observing and staler than live.
3. **Live / directly-observed** — seen now, trusted.

Part of taste is the **re-scout decision**: trust the grey intel, or spend recon
to refresh it.

### 2b. Cuts are the prior over the fog

`DEV_PATTERNS.md`'s "cuts-first" (Qix decree) lands cleanly here: **cuts are a bet
on where the undiscovered landmarks probably sit and what points at them** —
shoreline (terrain implying structure), old recon (grey data), training priors,
fresh cache. They are the fog-reading heuristics that let the next observation go
to the highest-EV angle instead of scanning uniformly.

**But cuts must be cuts-*continuously*.** In real Qix you make *successive* cuts;
each claim redraws the board and what is worth claiming next. Every probe that
resolves an unknown is license to redraw the cuts. This collides with star's
current guardrail (a child's cuts must be a *superset* of the parent's — "no
sideways drift"). Resolution is an **authority distinction, not a prohibition**:
re-cutting is a taste act the master explicitly owns and **logs** (name the
surprise that licensed it); silent drift by a sub-agent is the violation. Same
motion, different authority — and the `surprise` field is the audit trail that
separates them. Today "cuts" reads like a static contract term; it wants to be a
living one only the master may edit, on the record.

---

## 3. Bidirectional search and the possibility brake

"No start and end, only positions pathfinding to each other" is **bidirectional
search**; the backward half is **goal regression** ("for the goal to hold, what
must be true just before it?"). The kanji tower is *literally* a backward chain:
draw kanji ⇐ know stroke order ⇐ know script ⇐ know language. Backward search is
not a nicety — it is where the richest side-quests already come from
(`INTENT_RESOLUTION_DESIGN.md`'s side-quest DAG, generated by hand today).

**Frontier convergence is the possibility estimate.** When forward and backward
searches keep connecting landmarks, P(a path exists) rises *structurally*, before
the whole path is walked. When both frontiers stall despite probing, that is the
non-vibes signal to lower it. This gives `declare-blocked` a **structural basis**
instead of raw taste — the one place star currently leaves to vibes.

### 3a. Four honest stops, currently collapsed into one

Cost is **not** the brake. Untangling the stop-verdicts:

1. **Thesis-refuted** — frontiers stopped converging; no landmark connects after
   avenues are exhausted. The genuine dead end.
2. **Reachable-but-not-worth-it** — the path *connected*, so the thesis is TRUE,
   but the connecting edge carries a **discovered cost** above the goal's value
   (Jeremy's "$10k-possible-but-unreachable token-spend goal"). This is an *edge
   label*, not a budget event.
3. **Out-of-budget** — hit a preset spend cap. A cost stop, not a possibility
   stop; it cannot tell "impossible" from "expensive."
4. **Lost-the-plot (coherence)** — the fourth wall (see §6).

All of 1–3 are **probabilistic under fog** — in a large map you never fully clear
the dark, so even a "hard" dead end is really "no connection found AND the EV of
clearing more fog fell below threshold."

---

## 4. Reconnaissance is a step *flavor* (not a new step shape)

Recon is the same node contract (goal / done-means / cuts / budget → result), but
a distinct **flavor**, because its **return type is different**: a recon step
returns **map edits** — a new landmark, a connected/failed edge, a
reachability-and-cost estimate, a freshly-surfaced unknown — not
deliverable-progress. Consequences:

- **Different verification.** You check a deliverable against done-means; you
  check a recon return against "did the map actually change, and is that new edge
  real or hallucinated?"
- **Different bounds / prompting on return** (Jeremy's ask — recon "might need
  some different bounds, verification or prompting when it comes back").
- **Two probe modes; discoverability lives in the second.** Hypothesis-directed
  recon *resolves a named unknown*; **reconnaissance proper** exists to *surface
  an unknown you had not named* — the unknown-unknown. The kanji punchline is the
  ceiling this breaks: closure "can only catch gaps the planner was already
  worried about." The star ledger's `surprise` column is the *retrospective* seed
  of this; a *prospective* "what don't I know yet" allocation is missing.
- **Backward-chaining is a recon flavor** (it emits landmarks, not progress).
  "Crowd-source it live in a step" (ask the world / reddit) is recon that surveys
  *the world* instead of the tree.
- **Two currencies.** Probe-cost (cheap, buys possibility-information) vs
  commit-cost (expensive, buys deliverable-progress). Recon is the highest-leverage
  probe spend because it can **delete whole committed branches before you pay for
  them**. Manti's "2 committed steps vs the baseline's 7" is exactly this.

---

## 5. Thesis vs cuts, and the leap as a rare in-game move

- **Possible-now bias protects the *thesis*, not the cuts.** The thesis (the
  treasure is real / the exit exists) is the stable belief you refuse to abandon
  over a few dead ends. The cuts are liquid — demolish and redraw them without
  ceremony as the board changes. This is why freezing cuts-as-static is the wrong
  thing frozen.
- **The 1-shot leap is a rare move in the same game, not a different game** — the
  full-length Qix line that wins instantly when the board allows and gets you
  killed when the threat is still loose. Reading whether the board allows it *is*
  the NOW-vs-meander router call; "sent it NOW and FAILED" is attempting the split
  with the Qix uncornered.

---

## 6. The fourth wall: coherence of the assembled path

The map is sound, the thesis holds, **every landmark still connects, and the
assembled path no longer serves the original goal.** Jeremy's image: *"I wanted a
picture of a rabbit, but Bugs Bunny isn't what I had in mind… at all."*

This is structurally invisible to `done-means`, because done-means lives **on the
pins**, not on the **string** between them. It gets *more* likely the better local
verification is — clean pins lull you. Coherence of the *assembled* path likely
needs its own signal, distinct from per-milestone done-means. (Open — see §9.)

---

## 7. Educated guesses / theories = conjectured interior landmarks

A **theory (faith-based plan)** is a landmark planted in the *middle* of the map
and connected to both frontiers. Its value is **earned by connection, discarded by
isolation**: if forward reaches it and backward regresses to it, the guess was
load-bearing; if it stays an island after honest effort, drop it with zero shame.
This keeps "faith" honest — a cheap bet on a *waypoint*, never a commitment to a
*route* — and generalizes "no start and end" past two ends: every recalled or
guessed milestone is another survey station (multi-source bidirectional search;
the pirate map with five X's).

---

## 8. The observe → construct boundary (the escalation line)

The sharpest line in the conversation: **not every edge is discovered. Some are
built.** Revealing a latent connection (observe) and digging a tunnel that did not
exist (construct) are different kinds of move.

"It's all connected, just waiting to be discovered" is the right operating faith
for the **information layer** — the connections do exist in latent space / the
world, and recon just moves your vantage to reveal them. It **stops being true** at
the **capability frontier**: a tech-tree edge ("learn to dig under the wall,"
"build a hot-air balloon to cross the chasm") does not reveal a pre-existing
tunnel; it changes *what you are capable of*.

- **Escalate at the capability-frontier crossing.** Walking edges and revealing
  connections with tools you *have* is autonomous. **Acquiring a capability you do
  not have** to cross a soft dead end is the level-up — and *that* is the human's
  call. The escalation is not "should I keep trying other avenues" (autonomous),
  it is "should we level up the tech tree."
- **Soft vs hard dead end.** Soft = no path with *current* capabilities, but a
  visible landmark sits across the gap, reachable with a capability jump. Hard =
  no path even with jumps (thesis-refuted).
- **Possible-now bias is the gate *before* the level-up.** Before "we need the
  balloon / a better model" is legitimate, prove you **composed the tech-tree
  nodes you already own** and none reach. The balloon is a valid ask only after
  the ladder, the existing bridge, and going-around are ruled out. (Jeremy's fear
  in both directions: don't quit a reachable landmark; don't ralph a soft dead end
  as if it were hard.)
- **The "within sight of a worthwhile landmark" constraint** guards against
  capability-acquisition rabbit holes — build the balloon only when the fog has
  already revealed a treasure across the chasm worth the reach. It also writes the
  **escalation payload**: *"I can see landmark L, worth W, across a soft dead end;
  routes to it require capability C; here's the current-tech composition I tried
  and why it falls short; authorize the level-up?"*
- **Capability edges are the only edges that persist off the map.** A revealed
  connection is good for this goal; a *built* capability — the tunnel skill, the
  balloon, the learned language — becomes a permanent tech-tree node available to
  *every future map*. **The tech tree IS the skill library / evolver.** A level-up
  paid once amortizes across the whole goal-family, which rewrites its ROI: you are
  not paying to reach landmark L on *this* map, you are buying a node that crosses
  every future chasm of that shape.

---

## 9. Recommendations for further planning (NOT decisions)

Ordered rough-first. Each is a **question to answer / direction to probe**,
deliberately not a build order.

1. **Represent the plan as a landmark graph** (confidence + fog-state per
   node/edge), not a step list. Minimal schema? Relation to the existing
   ResolvedIntent / Deliverable artifacts?
2. **Type recon as a step-flavor** with a **map-edit return** and its own
   verification/bounds. What does its result block look like vs a deliverable
   step's?
3. **Structural `declare-blocked` from frontier convergence**, decoupled from
   budget. Can convergence/stall be measured cheaply enough to drive the stop?
4. **Separate the four stop-verdicts** (thesis-refuted / reachable-but-not-worth-it
   / out-of-budget / lost-the-plot) as distinct outcomes, each with its own
   handling and (for the first two) its own evidence.
5. **A coherence check on the assembled path**, distinct from per-milestone
   done-means (§6). Does it collapse into re-checking done-means at each milestone,
   or is it its own signal?
6. **Escalation payload schema for capability level-ups**, with possible-now bias
   as the enforced pre-escalation gate. Slots into the existing escalation-surface
   decree (substrate LLM go-between, durable file).
7. **Milestone-shaped durable memory.** Landmarks (not steps) as the reuse unit;
   grey-fog decay/freshness on cached landmark state. Ties to memory_port / recall.
8. **Tech-tree = skill library / evolver**, made explicit; capability-acquisition
   ROI computed at goal-*family* altitude.
9. **Backward-chaining taste mode** in the planner / star (goal regression), as a
   first-class generator alongside forward "next task."

---

## 10. Open questions we did not resolve

- **Escalation altitude.** If the tech tree persists across maps, a level-up's ROI
  is a goal-*family* calculation — so the escalation is really *"should maro become
  the kind of agent that can do this class of thing?"* Is that the altitude to
  pitch escalations at, or does it overload a moment that should sometimes just be
  "yes/no, cross this one chasm"?
- **Coherence vs done-means.** Is lost-the-plot its own tracked signal, or an
  emergent property of milestone-level checks? (Instinct: separate — done-means
  checks each pin locally; nothing yet checks that the string still tells the
  original story.)
- **Taste / possibility calibration.** Three sources — live crowd-sourcing,
  training priors, and (most impactful for maro) **learned lessons fed back into
  the methodology**. The `surprise` field is one fuzzy input; how do surprises +
  verified outcomes accumulate into calibration that actually changes the next
  meander? (Jeremy's deferred edge: taste maturation may need *consequence-coupled
  reps*; bottleneck = verified-outcome density, not calendar time.)

---

## 11. Provenance

- Conversation: Jeremy ↔ Claude (Opus 4.8, 1M ctx), 2026-07-21 → 07-22, on the dev
  M1 while the swarm-review arc (chunks 1–5) ran on the box. **Full verbatim
  transcript:** `docs/conversations/2026-07-22-compound-thinking.md` (kept for
  future revisits — we reliably glean new edges from raw logs once new context
  lands; the Virgil catch is the proof).
- Extends / relates: `docs/INTENT_RESOLUTION_DESIGN.md` (side-quests, "what does
  done mean"), `docs/DEV_PATTERNS.md` (taste/judgement, cuts-first, possible-now
  bias), `.claude/skills/star/SKILL.md` (star mini-orchestrator, node contract,
  recursion turn-on conditions), and the GOAL_BRAIN 2026-07-21 entries
  (delegation-boundary razor, CGI north-star, possible-now values statement).
- Metaphor ladder (for future readers): taste/judgement loop → Manti (compound
  goal; leap fails) → kanji (backward tower of unknowns) → Qix (cuts, calculated
  risk, the rare 1-shot split) → maze (believe the exit; a blocked route ≠ no exit)
  → pirate map (waypoints > edges) → bidirectional / N-landmarks → tri-state fog of
  war → observe vs construct + tech tree → escalation altitude.

---

## 12. Grounding pass — the conversation vs the shipped tree (fable, 2026-07-22)

The conversation ran while chunks 1–5 were landing and could not see chunks 6–8.
This section is the first of the "revisit with new context" passes Jeremy
predicted — written after the full arc landed, with every claim below verified
against the tree, not memory. **Verdict up front: conceptually on target. The
map model unifies rather than invents — and that's its strength.** The nudges
are almost all of one kind: *a recommendation framed as a question to answer is
actually a seam that already exists — point at it instead of building.*

### Where the spiral genuinely moved up

1. **The inverted confidence gradient** (§2) — sure of landmarks, guessing at
   edges — is a real inversion of how the planner trusts its sequence today.
   Nothing shipped thinks this way yet.
2. **The four-stop untangling** (§3a) extends the done≠successful split
   (session 40) one level up, and that split paid for itself. Distinct verdicts
   with distinct evidence is a proven move here.
3. **The observe→construct line** (§8) — escalate at capability acquisition,
   not at difficulty — is the cleanest statement of the escalation boundary
   this project has produced, and it composes with possible-now bias instead of
   competing with it.
4. **Recon's return type is map edits** (§4) — the type distinction, not the
   step flavor itself, is the new part. It's what makes recon verifiable.

### Nudges — each one verified against the tree

1. **Rec §9.3 (structural declare-blocked) is not greenfield.** Phase 62
   already ships this brake at step level: error-fingerprint convergence
   detection (`loop_blocked.py` — `_is_converging`, escalate-only navigator
   cutover), and the chunk-7 readout measured it live: **0 evidence-free
   retries in 1,253 metacognitive decisions**. "Blocked = stopped converging,
   not spent enough" is already how steps stop. The plan-level version wants
   the same shape, one level up: **map-edit rate** — consecutive probes
   returning zero new landmarks/edges = the stall signal. Extend the seam,
   don't invent a signal.
2. **§10.3 (calibration) is further along than the doc knows — chunk 6 shipped
   mid-conversation.** Lesson extraction now *leads* with the
   expectation-mismatch question (memory.py:200); novelty = 1 − max store
   similarity is measured on every recorded lesson (knowledge_web.py:13 — the
   killswitch kills only the boost, never the measurement); the chunk-7
   readout has a novelty section plus an honesty line for what's missing. The
   open question reduces to a **join, not a mechanism**: surprise/novelty at
   capture-time ⋈ verified outcome at closure-time. The negative half already
   exists — the chunk-4 contradiction pipeline (cited lesson + FULL-trust
   failure → contested → refight) IS "the meander punishing a bad prior." The
   positive half is reinforcement. What's missing is only the readout over the
   join, and data density (all 350 live lessons predate the novelty field).
3. **Rec §9.5 (coherence check) half-collapses into an existing seam.**
   closure_verify already treats the director-proxy commitment as a *binding
   goal definition* (closure_verify.py:648-655) and verifies at goal level
   against the original ask — that IS the anti-Bugs-Bunny check. The hole is
   **cadence, not mechanism**: closure fires at the END, so a long meander can
   burn its budget on a drifted string before anything re-anchors. First
   experiment: run the existing goal-level question mid-meander at milestone
   boundaries, against the journaled commitment. If that catches the drift,
   coherence never needed to be its own signal.
4. **Rec §9.1 (landmark graph) is the tower-of-babel candidate — build it
   last, not first.** Consumer-first: nothing reads a landmark graph today,
   and the tree already holds landmark-shaped artifacts (ResolvedIntent
   deliverables, the side-quest DAG, decisions.jsonl, thread_brain). A graph
   store built before its consumer is the exact rot pattern chunks 3–4 just
   spent two chunks repairing. Order the work so the schema emerges by
   subtraction: ship the stop-verdict split (§9.4) and recon flavor (§9.2)
   first — their return/verdict types FORCE the minimal map schema into
   existence, and the graph becomes whatever those two actually needed.
5. **§2b's authority resolution already has a decided ancestor.** The fork
   contract (THREAD_ARCHITECTURE.md, chunk 3) defines the escalation-trigger
   lane: a child with *evidence* against a parent-owned decision surfaces it
   and the parent decides — explicitly not parent-always-wins. Master-recuts-
   on-the-record vs child-drift is the same three-way ownership applied to
   cuts. Owning the miss: star's alpha guardrail conflated no-silent-drift
   with no-re-cutting — the conversation's correction is right, and it's
   **fixed in `.claude/skills/star/SKILL.md` as of this pass** (re-cutting is
   a logged master taste act; children surface cut-evidence, never edit).
6. **§10.1 (escalation altitude) — not a rivalry; the payload carries both.**
   The per-chasm yes/no is the decision; the goal-family ROI is one line of
   context inside the payload. The escalation-surface decree (substrate
   go-between, durable file) already fixes where it lands. Don't gate the
   yes/no on the family calculus — supply it and let the human read at
   whichever altitude they're standing at.

### What the conversation missed entirely

**Recursion.** The map is described as one flat map, but the standing recursion
decree means every sub-goal is its own map: landmark and cut inheritance across
parent/child IS the ancestry problem (thread_brain → ancestry.json, already an
open GOAL_BRAIN thread), and the fork contract already answers the authority
half. If the map schema isn't stated as **recursive from the start** — a
landmark on the parent's map can be a whole map one level down — it will bake
in single-map assumptions that the recursion decree forbids. This should be a
§9 recommendation in its own right.

### Proposed build order (input to the discussion, not decisions)

(a) **Stop-verdict split (§9.4)** — smallest, proven precedent, and every
other piece wants its honest data. (b) **Recon flavor (§9.2)** with
claim-probe-shaped verification — chunk 5b's `settled_by_command` discipline is
the existing answer to "is that new edge real or hallucinated." (c) **Exercise
the map moves in `star` first** — as pure prompting, per its charter;
crystallization-pressure findings decide what graduates to src/. Never-build
list (current belief): §9.1 as a standalone graph store; §9.8 as new ROI
machinery (a payload line suffices until proven otherwise).

An independent contrast take from the opposite model family (Codex, xhigh
reasoning) is recorded at
`docs/history/2026-07-22-compound-thinking-codex-take.md` — commissioned
per Jeremy's ask, summarized there rather than here so this section stays
fable's own read.
