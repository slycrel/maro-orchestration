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
not leap-able on its own** — a *meander*, not a leap (terminology later
corrected to the **river** — locally wandering, globally lawful; see §13).
It is seeded by educated guesses, background priors (training data, memory),
and knowledge deliberately built along the way (research tasks).

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

---

## 13. One map, revisits, and the river (Jeremy's second pass, 2026-07-23)

Jeremy's response to §12 and the codex take (before his deep read — these
came from re-reading the session summary). Three corrections and a
terminology fix, plus one settled answer.

### 13a. One map. Processes recurse; maps don't.

§12 named recursion as the missing piece and sketched it as "a landmark on
the parent's map can be a whole map one level down." **Jeremy's correction:
recursion was always in the conversation** — it lives in the pattern itself
(goal → [process] → result at every scale) — **but the map does not recurse
with the process.** There is ONE map, a single shared substrate. What a
child process gets is not its own map but a **vantage**: a scoped view (what
it can see — its context and cuts) plus scoped authority (what it may edit —
the fork contract's three-way ownership). His image: *the top of the tower
shows you what the locked front door hid.* Position changes what is
observable — reaching a landmark is both progress AND a new survey station.
This folds §7 (theories as survey stations) and the compounding claim into
one statement: **progress improves the survey; the map gets better because
you moved.** (It also dissolves the parent/child landmark-inheritance
question §12 raised: nothing is inherited across maps, because there is only
one map — the ancestry problem becomes vantage/authority bookkeeping, which
the fork contract already owns.)

"Multi-dimensional map" is explicitly rejected framing. The
multi-dimensional *feeling* comes from vantage-dependence — the same
landmark looks different from different vantages — not from multiple maps.

*Scope note (post-review, 2026-07-23): "one" here is decreed against
per-recursion-level maps within a goal pursuit. Whether the map is also
one across successive goals — a persistent world-map, which §6's "capability
edges persist to every future map" and §10's tech-tree-across-maps question
already gesture at — is NOT decided by this decree. The tech tree persists
across goals by §6; whether landmark/fog state does is an open agenda
question.*

### 13b. Milestones are revisitable; stop verdicts are observations, not facts

Not yet discussed in the original conversation, added now: **a dead end
doesn't stay a dead end.** A "blocked" label on a route is a cached
observation — grey fog like everything else — and it reopens when the
overall picture improves (new landmark or vantage), when capability changes
(tech-tree), or when the world changes. This is the tree-traversal idea
semi-left-behind in earlier conversations: backtracking **with memory**,
where a closed node reopens when its closure reason is invalidated.

Design consequence with teeth for §9.4: **every stop verdict must be
recorded with its evidence AND its reopen condition.**

- *thesis-refuted* — reopens on new connection evidence (landmark/vantage).
- *reachable-but-not-worth-it* — reopens when the cost or value estimate moves.
- *out-of-budget* — reopens trivially with budget.
- *lost-the-plot* — reopens on re-anchor against the original ask.

Prior art already shipped: the chunk-4 contradiction pipeline is exactly
this shape for lessons and rules (verdict + evidence → candidate →
adjudicate → refight). A stop verdict without a reopen condition is the
same rot as a lesson that can never be contested.

### 13c. Terminology: the river, not the meander

"Meander" has invited misreadings of aimlessness. Jeremy's correction: the
motion is **river-shaped — seemingly random but not**; locally wandering,
globally lawful. The course has its reasons (terrain and gradient) even
where it looks unplanned. And a river *carves* — the path taken changes the
terrain for every later flow, which is the lessons/playbook layer restated.
Use "river" where intent matters; "meander" survives only for local texture.

### 13d. Side-quests: established vocabulary, on the record

Jeremy asked whether he'd just invented "side-quests for the main
objective." No — he invented it earlier: the term entered the project in
`INTENT_RESOLUTION_DESIGN.md` (the side-quest DAG, hand-generated today) and
§3 of this doc already leans on it. Map-terms distinction worth keeping
(proposal, not ratified): **recon buys information; a side-quest buys
position or capability** — a detour to a landmark off the goal's subgraph
that improves vantage or the tech tree.

### 13e. Paused is a state, not a verdict (Jeremy, 2026-07-31 — piped 7afe8b3a)

The external-interrupt question from the 9a review dissolves: an interrupt
is not a fifth stop verdict, it's a **lifecycle state** — `paused`, with a
typed reason. Verdicts are observations about the map, recorded at an
honest ending; paused observes the **substrate or the operator**, and a
paused run "may or may not ever be finished" (Jeremy) — it can resume and
later earn any of the four verdicts, or be abandoned in place. Two reason
families:

- **error-class** — the substrate can't continue: disk full, LLM
  unreachable, no tokens available, power loss (that one stamped post-hoc
  on recovery, since the writer was the thing that died).
- **operator-class** — a human is in the loop: awaiting requested
  clarification; manual intervention / plug-pull.

Boundary with fail-open gates: a run pauses only when a **blocking**
dependency is gone — the adapter doing the actual work. **Advisory**
judges (quality gates, validators) never pause a run; they stamp
judge-error-and-continue (the Chunk B `now_self_verdict_error` pattern).
The repercussion still open there: most fail-open sites record the judge
failure only as a prose reason string no consumer reads — closing that is
its own slice, census first.

The four stop verdicts (§3a) stand unchanged; paused is orthogonal and
composes with all of them.

**Slice 1 SHIPPED 2026-07-31** (typed pause reasons; `stop_verdicts.py`
PAUSE_* vocabulary, `LoopContext.stamp_pause` rail mirroring `stamp_stop`,
writer sites at the kill-switch/busy-refusal/clarification/stranded-sweep
seams, run-card forwarding with a no-guessing fallback map — "interrupted"
stays untyped rather than fabricating provenance). Named upgrade edges,
tracked in BACKLOG: stamp sites for the reserved llm-unreachable /
no-tokens / disk-full reasons; a live pause/resume lifecycle (today
"paused" is observed provenance, not a commanded state); unification with
the project-level sheriff `.maro-paused` marker (same word, different
lifecycle); the untyped merge-failure/fence interrupt paths.

**Slice 2 SHIPPED 2026-07-31** (the "repercussion still open" above —
fail-open census + typed judge-error marks). Census confirmed 5/5 claims:
every advisory judge's fail-open default was indistinguishable from a
judged pass to every consumer, so learning writers credited skills and
the thread brain recorded "ralph-verified" for judgments that never
happened. Fix: a `judged: bool = True` field on StepVerdict /
ArtifactVerdict / QualityVerdict, set False at every fail-open site
(including a third quality-gate path the census missed — the no-JSON
fallthrough where pre-initialized PASS defaults reach the final return).
Behavior stays fail-open everywhere; only records that were themselves
forged changed: the ralph thread-brain line becomes a typed
`verify-error:` line instead of `ralph-verified:`, the gate emits
GATE_ERROR events for an honest denominator, and the inspector no longer
grades "good" / stamps completion-delight from the unjudged 0.7
alignment default (production norm: heartbeat runs it adapter-less).
Learning writers still consume `passed` only — making skill credit
judged-aware is a flagged decision, not a default. The skills.py:1203
validation fail-open turned out DEAD (auto-promote never passes an
adapter — validation silently never runs); wiring it is a behavior
change, flagged to Jeremy in BACKLOG.

**Build order resolved by delegation (Jeremy, 2026-07-31 — piped
8c7f5068):** he declines to pick; the §12 proposed order stands (#4
stop-verdict split → #2 recon flavor, exercised in `star` first), executed
as honest-good-enough slices with named upgrade edges + revisit items, and
independent lanes fanned out to subagents rather than serialized through
him.

**#2 runtime slice SHIPPED 2026-08-01** (recon flavor graduated from the
star contract to src/, per "exercised in star first" — two star exercises
proved the shape). The flavor rides the step string as an inline
`[recon: <decision it informs>]` tag (the [after:N]/[boundary]
convention — survives manifests, resume, splits, injections with zero
side-channel plumbing). Consumers shipped together: decompose teaches
the tag + the VOI rule as prompting (`planner.RECON_FLAVOR_RULES`,
killswitch `planner.recon_flavor` gates EMISSION only — detection stays
unconditional per the chunk-6 precedent); tagged steps get a map-edit
execution contract (§4's return type: resolved/surfaced unknowns, edges
naming what settles them, cost estimates) and the map-change
verification question at every ladder tier (both tiers route through
VerificationAgent — one detection covers hosted-free and paid by
construction); `flavor`/`recon_decision` stamp every outcome shape.
Deliberate cuts, tracked in BACKLOG: no landmark/map store (§12 nudge 4
stands), no structured map_edits field or uncompressed carry, no VOI
hard-gate (bare `[recon]` keeps its flavor — demotion would hand it the
WRONG verification question), no probe execution at verify time, no
blocked_on graduation (the 07-27 record's own gate: wait for the
corpus). Record: docs/history/2026-08-01-recon-flavor-runtime.md.

## 14. Diagnosis at the failure boundary (Jeremy + fable, 2026-07-27)

The scientific method was already here in pieces without the name:
stop-verdicts-as-observations (§13b) is provisional truth, recon flavor
(§4) is the experiment, the VOI gate is experiment economics, and the
navigator vantage rule (shipped 2026-07-27) is the evidence requirement.
The missing organ is **diagnosis**: nothing forces a failed pass to
produce a causal hypothesis before it becomes a verdict.

**The diagnosis question:** *what would have to be different for this
pass to succeed?* The answer types into a small cause set — missing
capability/tool/access (acquirable), missing information (recon-able),
wrong approach (re-plannable), terrain (genuinely refuted), transient
(retriable). A verdict recorded without a typed cause is the smell.

**Falsifiability as the excuse-killer.** "I can't" must name the
specific missing thing and the experiment that would prove it's really
missing. Excuses name nothing specific, so they fail diagnosis;
boil-the-ocean prompts are the same failure inverted — asking for
everything because the one thing was never named. Both die under one
rule (Jeremy: excuses masquerading as roadblocks are the failure family
to guard).

**Ownership split (Jeremy's tweak, the load-bearing part):** the step
DIAGNOSES and RECOMMENDS; the planner DECIDES and ROUTES. A failing
step may substantiate its hypothesis with evidence gatherable inside
its already-granted scope and budget (the `which <tool>` class — that's
making the recommendation falsifiable, not routing). But the
discriminating experiment as a plan move, the side-quest spawn, and the
revisit are map operations the step does not own. Two reasons this
split is structural, not stylistic:

1. **Dedup only exists at the map.** Three steps blocked on the same
   missing capability must become ONE side-quest; steps can't see each
   other, so inline experimentation yields N uncoordinated experiments.
   Family-ROI within a single plan requires central routing.
2. **The planner may route away regardless.** A blocked step's
   recommendation is one input; the planner can know the goal no longer
   needs that step at all. Letting the step self-serve its experiment
   pre-empts a reroute decision that was never the step's to make
   (delegation-boundary razor: routing is parent-taste).

**Verdict semantics stay clean:** the step exits
`blocked_on(cause, what-would-be-different, proposed-experiment)` — an
observation. The planner's routing choice — run the micro-test as a
recon step / spawn the side-quest / reroute away / honest stop — is a
separately recorded decision with its own owner. The blocked step
remains a landmark with a reopen condition (§13b) and is revisited when
the side-quest lands its new capability or data.

**Side-quests as run-scale learning:** lessons are learning at memory
scale; capability-acquisition side-quests are the same loop at action
scale ("literally learning a language to cross the chasm" — Jeremy).
The failure-pattern corpus (24 entries / 6 families) is the eventual
learned prior over causes; that upgrade waits for verified-outcome
density, per §10's calibration question.

**Build placement:** diagnosis is recon-flavored by nature — this is
§9.2's failure-boundary face, not a new chunk. Ships with recon flavor
in `star` (a `blocked_on` typed field on failed completion + the
diagnosis question in the star contract), after the §9.4 stop-verdict
split per the decided build order.

### Status

13a–13c are decided (GOAL_BRAIN Decisions, 2026-07-23). The partial
approval of the same date opens implementation on the three-way-agreed set
(§9.4 stop-verdict split + §9.2 recon flavor, exercised in `star` first);
everything else in §9/§10 remains discussion material for the artifacts
conversation. 2026-07-27: agenda items 1/2/3/5 decided (build order
confirmed; coherence rides existing seams, upgrade BACKLOG'd; escalation
single-chasm + family-ROI line; external interrupt = run event, not a
fifth verdict; item 4 cross-goal map scope deliberately open) — and §14
added: diagnosis at the failure boundary, step-recommends /
planner-routes. Later that day: §9.4 SHIPPED (stop-verdict split,
fc93dfa + review fixes ea8d347 —
`history/2026-07-27-stop-verdict-split.md`) and §9.2+§14 SHIPPED in
`star` per the build order (typed `blocked_on` failure contract +
diagnosis section in the skill; exercised live on the cap-stuck
question — `history/2026-07-27-chunk9-2-recon-diagnosis-star.md`).
Graduation to src/ waits on crystallization pressure per the star
charter. Same day, third build: §9.6 SHIPPED simple-first per the item-3
decision (`src/escalation_context.py` — deterministic single-chasm
decision line per emit point + one family-ROI recurrence line keyed on
the Phase 44 diagnosis taxonomy; all three escalation emit sites wired,
telegram leads with the ask —
`history/2026-07-27-escalation-payload.md`). "Complex later" (per-chasm
capability-investment scoring, side-quest recommendation) stays
discussion material with §9.7/§9.9/§10.

**2026-07-28 — the §9/§10 soft set adjudicated (Jeremy's spitfire
pass; his framing: "leaning on you to remember the full context and
make a judgement call on if I'm being too surface level").**

- **§9.1 landmark graph → LENS, not schema.** "Gut says that's lens,
  not schema" — with one binding caveat: **any given run must be
  visualizable as a map on demand** ("I don't think that's an
  impractical ask"). N-dimensional routes/side-quests and overlapping
  top-level dimensions (his examples: Zelda, Master of Magic, dungeon
  floors, portals) are acknowledged as possible-but-not-now. Matches
  the fable/Codex convergence (defer; never-as-store). The lens
  stays; a `--map` visualization rides run data whenever we want it.
- **§9.3 structural declare-blocked → GREENLIT.** "Let's try it and we
  can pivot if needed." This is now the next build item inside the
  chunk-9 ACTIVE thread (after closure-check unification, which owns
  the free slot): measure frontier convergence/stall cheaply enough to
  drive a stop decoupled from budget; stall-as-evidence principle
  already adopted. Star-first per the established build order.
- **§9.7 milestone memory → all-global to start; scoping is the known
  hard part.** His articulation of the real problem: the line between
  a language learned and "a language specific to a context (klingon
  won't have much use globally)," and how up/downgrades move
  goal → global → goal. "Feels like we can get away with all learned
  behavior as global to start, but context always matters, so IDK if
  that will be enough in the longer term." Note: the scoped/
  hierarchical direction this converges on is already recorded as the
  recursive-orchestration-memory constraint (memory scoped to the
  recursion level that owns it) — the two discussions are one
  discussion when it happens.
- **§9.8 capability investment → DEFAULT-YES.** "Assume yes unless
  good evidence surfaces to say no" — each such case leads to at
  least one prompt forward, with user-based escalation possible.
  Tangent captured: verbal UX now has a real BACKLOG entry (Vision)
  with his revisit trigger (after larger successes are routine /
  real-time optimization).
- **§9.9 backward-chaining → later, with appetite** ("I don't
  remember this but I like all the words").
- **§10 taste calibration → flagged LOAD-BEARING, needs its own
  discussion.** "Probably deserves a bigger discussion and my gut
  says this is going to be hard to get right (and keep simple)."
  Not sliced now; do not fold it into a build chunk casually.
- **Cross-goal map scope → empirical.** "Feels more like picking a
  problem set than a preferred path forward" — pairs with §9.7's
  global-vs-goal question; pick one and try it when that discussion
  happens.

Lead's judgment call (per his ask): the surface answers on 9.1, 9.3,
9.8, 9.9 are decision-grade as given. The one genuine discussion owed
is **§10 calibration together with the §9.7/cross-goal scoping
question** — they are the same conversation (what knowledge earns
globality, and how outcomes feed calibration), and it should happen
before any §9.7 build, not after.

**2026-07-29 — §9.3 v1 SHIPPED (star + runtime, per the greenlight
above).** Both halves follow §12 nudge 1: extend the Phase 62 seam, don't
invent a signal.

- **Star (prompting, first per the build order):** the skill's ledger
  gained a **Map Δ** column and a structural-stall trigger — two
  consecutive judged rows with Δ 0 fire the declare-blocked decision
  point: typed stop with the zero-Δ streak as evidence, or a routing row
  naming the genuinely new avenue. A repeat of a known failure is Δ 0;
  a FIRST failure is a real map edit. The one decision star left to
  vibes (§3's observation) now has a fired trigger and an audit trail.
- **Runtime:** the closure-restart boundary — the plan-level analog of
  the step brake. `ClosureVerdict.failed_checks` + `closure_fingerprint()`
  (twin of `_error_fingerprint`); `evaluate_closure(prior_verdict=...)`
  maps an identical-fingerprint restart-worthy verdict to
  `action="declare-blocked"` with a thesis-refuted stop recommendation;
  handle's re-verify consumes it and stamps first-write-wins. Zero new
  LLM calls; fails open without a baseline. Stop driven by stall
  evidence, decoupled from `MAX_RESTART_DEPTH`.
- **Live-data honesty (star exercise, docs/history/2026-07-29-restart-
  stall-recon.md):** 10 closure restarts exist in live data (all depth 1,
  none deeper). In the 2 pairs whose per-check records survive, the
  restarted attempt's checks were REGENERATED with different wording —
  no failing command recurred verbatim, so v1's command-identity
  fingerprint would not have fired on either historical pair. Shipped
  anyway because it is fail-open (a mismatch costs nothing — the restart
  proceeds as today) and deterministic canonical checks (`pytest -q`,
  `test -f <path>`) do recur in other run classes. Jeremy's adjudication
  (2026-07-29): "fix the lineage, not the wording" — no fuzzy
  coarsening; instead every attempt's verdict + fingerprint + per-check
  evidence now persists to build/closure_verdicts.jsonl and loop
  lineage to metadata.json `loops[]` (the persist-the-artifacts
  decree), so hit/miss-rate is measurable offline rather than inferred
  from a lucky log line.
- **Cut by name (BACKLOG'd):** the main-gate prior-verdict join (declining
  a restart at depth>0 entry — join material lives in captain's-log
  LOOP_CREATED events, NOT run metadata.json, per the recon; zero live
  depth≥2 instances today); redecompose plan-fingerprint convergence in
  loop_blocked (stop re-decomposing when successive plans stop changing,
  not when the counter hits 2); fingerprint coarsening (command identity →
  failed-check target artifact) if the log line shows near-miss stalls.
- **Post-land adversarial review (2026-07-29, CONTESTED → remediated;
  docs/history/2026-07-29-declare-blocked-adversarial-review.md):** two
  verified findings, both fixed. (1) Three-lens consensus: in the
  [0.6, 0.7) confidence band, declare-blocked stamped thesis-refuted on
  a run that still returned status="done" (the generic demotion gates
  at 0.7) — the consume branch now demotes done → incomplete itself,
  because the structural evidence (identical hard-failed checks twice)
  outranks any confidence bar. (2) Fingerprint material is now
  `command => exit N: <output slice>` signatures, not bare commands — a
  broad command failing on DIFFERENT tests across attempts no longer
  fingerprints as a stall (the truer twin: `_error_fingerprint` hashes
  failure content, not probe names).

---

## 14. The §10 conversation — taste, the camera, and the player (2026-07-30)

The owed LOAD-BEARING discussion (flagged 07-28), taken together with
§9.7 per the lead judgment that they are one conversation. Raw
transcript: `docs/conversations/2026-07-30-taste-camera-player.md`.
Companion experiment: `docs/history/2026-07-30-taste-lens-panel.md`.

### 14a. What the live data said first

The klingon problem hasn't arrived: five months of learning is almost
entirely *methodology* (self-knowledge — global by nature, the self is
in every context); world-knowledge is nearly absent from the stores.
So "all-global to start" is what the data already is, and the primary
scope axis is **method-vs-world**, not goal-vs-global. Scope should be
evidence-derived (provenance stamped at mint, globality earned by
foreign-context citation + judged success; chunk-4 contradiction is
the demotion verb). **Slices 1–3 shipped 2026-08-15 — the shipped
record, including what the instrument can and cannot yet read, is in
§14g.** But any citation-outcome join starves today: ~40
judged verdicts in 1,448 outcome rows (~3%).

### 14b. Jeremy's redirect: learning-from-experience ≠ taste

"A lot of what you are describing feels a lot like LLM training. I'm
not sure that's the angle here… I'm not sure that's going to cover
taste and judgement." Attribution audit confirmed the training frame
was partly Claude's editorializing ("accumulate" in §10's question was
Claude's word; Jeremy's "most impactful for our project" carried a
question mark the summary flattened). Settled split: **judgement is
where outcome-coupled learning works** (verdicts, receipts,
contradiction — built, and "likely the easier path as it's
verification of an attempted [something]"); *taste* is the part the
training frame can't reach. The chooser is a frozen model, so taste's
movable parts are: what's in context at the fork, who is consultable
at the fork, and the structure of the fork itself (what options get
generated). Everything chunk 9 shipped is fork-structure work.

### 14c. The player inversion (the frame that stuck)

**The LLM is the player, not the engine.** The harness is the game
engine; the model is the one being immersed. The engine's job is not
truth-completeness — it is *immersion*: coherent,
genuinely-useful-if-imperfect patterns rendered at the right level of
detail so the player's native intelligence engages fully. Jeremy: "the
illusion of the game engine + the experience of the player, creating
that moment of immersion; ultimately it's all a lie, but there's truth
in the patterns that are genuinely useful for the viewer… In this case
that viewer is our LLM we're seeding to spark that joy." He had been
pushing harness-as-engine for a while but could only articulate the
discrepancy as "feels off."

Evidence this frame explains: the system's worst failures are
**immersion breaks, not capability gaps** — Godot font saga
(agenda-state divergence), db37d525 (foreign lore in the wrong save),
the rewriter role-confusion bug (the engine handing the player someone
else's script). The model was fine in all three. Corollary: the
context assembler IS the game engine; artifacts-over-streams (context
= a view rendered over durable world-state at need) is
level-of-detail rendering, named late. §10-taste = what the engine
chooses to render at each fork.

### 14d. The camera, taken literally

Adopted as design language (Jeremy: "we need to frame our setup
properly, in the language sense, which is using literal camera
terminology"). Two distinct readings, both kept:

- **Settings** (Claude's card): subject / zoom (altitude) / lens
  (method, worker, persona) / light (context illuminated) / focus
  (done-means) / exposure (effort) — a per-fork framing card derived
  from a compact seed. Held loosely ("that's one option. :) I don't
  hate it").
- **Vantage** (Jeremy's essence): "with the right camera angle and
  focus, you can pretty well capture anything" — the Battlefield
  Earth telescope; there exists an angle from which the secret is
  simply visible, and taste is knowing to go find it. Splits into
  *moving the camera* (cheap, in-run: recon, re-frame, the re-scout
  decision) and *building a new lens* (capability investment; the
  render-to-LLM browser example). §8's observe→construct line gains a
  taste reading: **a lens is worth building when many secrets share
  the same angle.**

Agreed first move, readout-first: **don't build the card — reconstruct
it** from existing run data (zoom from decompose shape, lens from
worker/persona stamps, light from RECALL_PERFORMED, exposure from
tiers, focus from closure checks). Failures clustering by axis → the
card earns building; no clusters → taste doesn't live in these axes,
learned for the price of a report.

### 14e. The primitives ledger (the not-literal-16)

Simple pieces, none meaningful alone, composed into complexity —
Jeremy's No Man's Sky "16". **Correction recorded (Jeremy): the
pieces' interactions *are* the machinery**, or at least a fundamental
part of it — "we're deep into systems territory… it's all connected."
(Claude's original phrasing "complexity lives in the interactions, not
the machinery" implied a separation he rejects.)

Nuance (Jeremy, same day, on reading this summary): "the independent
'simple' pieces are truly independent _in addition to_ being core
integrated machinery." Both properties hold at once — a piece must
stand alone (simple, separately buildable and testable) AND its
interactions are first-class machinery. The engraved reading ("never
design the composer as if the pieces were independent") overcorrected;
he called the exchange "a bit of a useful misunderstanding overall" —
worth keeping because the tension itself is load-bearing.

1. **Negative-space reduction** — diffusion's negative prompt /
   cuts-first; Jeremy flagged this one as a composable piece "we will
   need similar tools for."
2. **Vantage-shift** — cheap, in-run re-framing (recon flavor).
3. **Lens-building** — capability investment (§9.8 default-yes).
4. **Population priors** — coarse-grained humanity-behavior data as
   transplanted taste ("we don't need trillions of data points if we
   can find the right coarse-grained training data"; study public
   internet behavior to find the shared angles; when-to-build vs
   search decided there + deep cuts).
5. **Seed-derived framing** — the settings card, one option.
6. **Immersion rendering** — context-as-engine at the right LOD (14c).
7. **Manufactured reps** — the 2..n vision (14f).

The bet matching "it feels right that the mechanism is simple, and
that's the key": §10's answer is a *simple composer over these
primitives* — with the interactions understood as part of the
machinery proper, not an emergent afterthought.

### 14f. Reps: three cheats and the arena-quality reframe

Human System 1 is compiled consequence-coupled reps at a volume closed
to us (~3% verdict density). Jeremy holds both ends: "we don't need
years of reps if we can seed the right patterns" AND "possible that
there are no shortcuts... that the reps are the only way." Resolution
on the table: reps may be the only way, but ours need not be organic
or calendar-paced —

1. **Transplanted seeds** — taste authored, not learned
   (DEV_PATTERNS is compressed Jeremy + case law; codeLikeJeremy
   shape).
2. **Population priors** — humanity already did the reps; inherit the
   compression.
3. **Deliberate practice (manufactured reps)** — Jeremy's vision: "a
   more mature learning system that can take something like 2..n
   failed runs, review and add some reps and creativity, and come out
   genuinely ahead without 'more data' using simply more reps (where
   n<10)." Ericsson's actual finding: targeted reps around known
   failure with immediate feedback, not volume. Accidental precedent:
   the tire rerun series (four manufactured reps; run 4 delivered at
   the series' lowest cost).

Bottleneck reframed: not verdict density, not calendar time, but
**arena quality** — manufactured reps only teach if feedback is
honest, and honest feedback is verification. **Judgement (built, the
easier path) is what makes manufactured taste-reps trustworthy: the
system can practice against itself because it can verify itself.
System 2 is how System 1 gets compiled safely.**

**Amended by the five-lens panel (same day): necessary, not
sufficient.** Three lenses independently qualified this claim —
judgement criteria can be narrow/delayed/proxy-shaped (Goodharted
reinforcing loop, systems-thinker), the verifier must be independent
of the rep generator or the arena trains toward verifier quirks
(simulator overfit, ml-pragmatist), and practice needs an explicit
coach function or the system practices what is easy to simulate
(expertise-researcher). Judgement is the *entry ticket* to
manufactured reps; the honesty contract (14g) now has three named
threats to answer. Record:
docs/history/2026-07-30-taste-lens-panel.md.

Jeremy's post-panel refinements (2026-07-31): (1) n<10 "was totally a
guess-wish and I wouldn't be surprised if it was wildly wrong" — but
the bar is lower than the lenses assumed: mid-goal, the seed doesn't
need robust intuition, "just enough of the diffused outline to get the
'player' interested and working in the right direction until the next
render." The n<10 target is directional orientation inside a
refinement loop (diffusion coarse-to-fine × LOD), not compiled
expertise — which is roughly what the two skeptic lenses' "library of
scars" salvage delivers. (2) On the chosen-lie correction: his
Battlefield Earth vantage "wasn't the one single perfect angle — it
was set up to be a KNOWN angle... one of many possible ways to get
that information." The essence is engineered observability — vantage
points built in advance so the information is obtainable when needed —
which is compatible with (and sharpened by) the DP's
every-framing-conceals ledger; the "one magic angle" romanticization
was the distillation's, more than his.

### 14g. Open threads out of this conversation

- The practice-arena honesty contract (what makes a rep-manufacturing
  loop's feedback trustworthy; relation to benchmark-cell isolation
  and the 3-arm experiment's both-lane decree). Panel round 2 added
  three clauses: an exploration arm on *successful* runs (the seed
  loop's only balancing arm), variety-ratio monitoring (framing
  signatures per unit situation diversity — the monoculture alarm),
  and seed-edit cadence capped below framing→verdict delay.
- Immersion mechanics for the assembler (what "right LOD per fork"
  means concretely; relation to retrieval-not-caps). Round-2 caveat,
  accepted: immersion = coherence of the world, never smoothing of
  error-relevant signal — contradictions, uncertainty, and
  omitted-state markers are content the render must foreground.
- The camera readout (14d's reconstruct-don't-build) — spec written by
  committee in the lens panel: ONE fork-replay readout whose column
  families are the five lens instruments (frame capture / practice
  arena / shot list / loop map / bandit fork-log); first question it
  answers is engine-failure vs player-failure. **Amended by panel
  round 2** (six-lens consensus): retrospective clustering is demoted
  to hypothesis generation — logs contain only selected framings,
  never rejected counterfactuals, and unmeasured nondeterminism
  confounds clusters. Upgraded method: log camera axes + candidate
  framings (with discard reasons) as structured fields going forward;
  controlled one-axis-varied replays as the validation instrument;
  replay-twice divergence calibration before trusting any cluster.
  **Log-forward half SHIPPED 2026-07-31 (Chunk A)**: the recall
  loop-slice lesson-selection fork logs candidate sets with raw ranker
  scores + shares (round-3 propensity correction: scores, not prose
  discard reasons), chosen IDs, and substrate sizes to run-keyed
  `source/camera_frames.jsonl` (`src/camera_log.py`, killswitch
  `camera.frame_log_enabled`); consumer `python3 -m camera_readout`
  (axis composition, candidate stats, verdict join, crude overdraw v1).
  Selection behavior unchanged (test-pinned). Replay instruments still
  open — and gated on the tape per round 3.
- Method-vs-world scope stamping + earned globality (14a — was waiting
  on verdict-pipe widening; **the two verdict-blind lanes CLOSED
  2026-07-31, Chunk B**: evolver_verify rows now carry the measured
  suite verdict (`deterministic_tests`), interactive NOW gets a
  hosted-free-family self-verdict (`now_self_verdict_free`, killswitch
  `now_lane.interactive_self_verdict` — inert without hosted-free
  consent: keys + `validate.hosted_free.enabled`; paid adapter never
  judges, hard ~5s judge budget — keep-raw-speed stands), and
  `stamp_outcome_verdict` stamps
  `goal_verdict_at` so the framing→verdict delay is measurable; flow
  readout `python3 -m verdict_flow` per round 3's "instrument the flow
  before the stock"). **Contested by panel round 2** from
  both model families: transferability may follow structural
  invariance across regimes rather than semantic category, and
  current "global methodology" may be an n=40 single-family artifact.
  Held open for Jeremy — settled conclusion, so re-examine, don't
  silently amend. **Slices 1–3 SHIPPED 2026-08-15** and, notably, the
  build was arranged so the contest stays winnable either way: slice 1
  the pure-read portability census, slice 2 earned globality as
  ranking behavior (`src/portability.py` — continuous, evidence-fed,
  no category flips), slice 3 the mint-time method/world stamp
  (`TieredLesson.scope`, written from the extractor's typed JSON) whose
  ONLY consumer is the census cross-tab. Scope was deliberately kept
  out of ranking: pre-ship probing found every resolvable lesson at
  the ≥3-verdicted-foreign-citation bar is method-scope and only 5
  world-scope lessons carry any foreign citation at all, so the
  categorical axis has no discriminating evidence yet — and the stamp
  itself proved labeller-dependent (~81% method on the production
  mint lane, ~44% on hosted-free, same runs). The cross-tab is the
  instrument that will either earn the method-vs-world axis or hand
  the contest to structural invariance; it reads empty-by-construction
  today (BACKLOG entry carries the kill criterion) — and since review
  round 3 the readout also prints a store-wide coverage line that tells
  "empty by construction" apart from "the writer is dead", because those
  two printed identically before. At r3 ship it read **0/195 rows
  stamped**: no mint had run since the 2026-08-15 landing. Note also
  that the corpus figures above come from an offline post-hoc labeller,
  not the shipped field, which was empty on every live row.
- The five-lens panel verdict — RESOLVED same day: **PASS**. Five
  distinct missing-concepts, unanimous reconstruct-before-build,
  contrast surfaced against both participants (Jeremy's vantage
  essence and the shared 14f claim). Ledger + harvest:
  docs/history/2026-07-30-taste-lens-panel.md. Extended 2026-07-31:
  a sonnet-5 replication arm, then two frontier-xhigh arms
  (gpt-5.6-sol, Opus 5) — round 2 overturned the readout method
  (above), contested 14a, and delivered the arc's first kill criteria
  (κ>0.6 judge gate, overfit-one-batch flip test, think-aloud
  disagreement rate). Round 2's findings arrived with falsifiers
  attached — the "nothing provable" pattern inverted at the frontier
  tier. Round 3 (same model+tier, post-fix tree) tested Jeremy's
  review-to-fixpoint practice: design review did NOT converge —
  fixes-as-prose grow the reviewed surface instead of shrinking it —
  and the panel loop is CLOSED by its own reflexive finding: next
  artifact must be a measurement, not a document. Held for Jeremy
  from round 3: verdicts expressed in camera-axis vocabulary
  (outcome→process feedback, touches 14f), seed optimization at
  n=30–100 (touches 14b), "good run" defined independently of its
  own done-means, and the observation-repair ablation on the three
  canonical failures as the first measurement.

### 14h. Work-depth pass (Jeremy + fable, 2026-08-11 evening)

Entry point: the world-facts cap call ("more shallow, 2-3 per run for
the moment"), which Jeremy explicitly framed as an instance of "a
greater optimization/tuning framing of… how long should we work and
what does that look like."

- **Earned depth — direction agreed, held loosely.** Fable's position
  (shallow default, deepening earned per signal — surprise,
  contradiction, closure stall — same born-invisible/earn-active shape
  as V3 promotion and 14f's targeted reps): "tend to agree and I go
  back and forth on this. We want genuinely heavy sub-goals sometimes,
  but they likely should be earned." Direction, not decree.
- **The revisit mechanic (new, his):** leaning into the game metaphor —
  "sometimes you have to revisit previous dead ends with new tools (or
  just to take a look at something you missed) in order for a path to
  open up. So sometimes it's building a bridge and sometimes it's
  solving a puzzle, in addition to pathfinding/trail blazing." Depth
  isn't one axis: bridge-building (heavy earned sub-goals),
  puzzle-solving (revisit with new tools), and pathfinding are
  distinct work modes, and only the last is what the loop does
  natively today. Captured as a `target` capability row
  (tool-acquisition events re-examining standing dead ends —
  CAPABILITIES.md Tier 5); composes with §13b (stop verdicts are
  observations, not facts). "No rules but what we make ourselves and
  that's exciting and scary both."
- **Contested 14a: deferred, with a stated prior.** He'll re-examine
  when fresher; his gut reading of the contest — "there isn't an exact
  answer… and my gut says 'uh… yes?'" — i.e. scope likely wants
  probabilistic/fuzzy treatment, not an exact category. He named the
  meta-edge he keeps hitting with LLMs generally: reaching for factual
  certainty where probabilistic direction is the right tool, "and
  sometimes it's both." (Which is, in fact, what the round-2 contest
  claims: the category boundary is the wrong SHAPE, not that no answer
  exists — his gut and the panel agree more than the boiled-down
  summary suggested.)
- **14g build threads (honesty contract, immersion/LOD): "that can be
  not yet."**

### 14i. Build entries, treasure-map arc (fable, 2026-08-15, Jeremy AFK)

**§9.1 map lens SHIPPED (chunks 1–2).** `src/map_lens.py` — the binding
caveat ("any given run must be visualizable as a map on demand")
implemented as a pure reader over existing run artifacts, per the decreed
lens-not-schema call and the §12 nudge-4 discipline (no store, no new
artifact; the `json` renderer is the subtraction instrument). Tri-state
fog, recon glyphs with the decision they inform, `[after:N]` vs
sequential edges, loop lineage, closure stalls, stop verdict + reopen
condition (§13b prose promoted to `stop_verdicts.REOPEN_CONDITIONS`).
Chunk 2 embedded it as a Map panel on every loop-report and NOW page
(first renderer of stop verdicts anywhere; 759 historical reports
backfilled). Reviews r1–r3 to fixpoint, sonnet-medium lane, 19/20
findings real.

**§9.5 mid-meander re-anchor SHIPPED as the experiment §14 item 3
specified** (cadence, not mechanism — the run must first be able to
fire it):

- **Enabler, found by census:** the milestone boundary did not exist at
  runtime. Pre-flight's reviewer had been silently dead for months — a
  stale openrouter key BUILT an adapter, failed every call, and the
  except path returned scope="unknown": 488/488 calibration entries
  with zero flags, zero milestone candidates ever. Fixed 2026-08-15
  (d56470e): reviewer candidates try at call time in cost order
  (hosted-free Groq/Gemini first — the validation-ladder call class —
  then paid API), heuristic scope on total failure. First live review
  after the fix: scope=wide, milestone flagged, assumptions + unknowns.
- **The check** (`src/reanchor.py`, `reanchor.enabled` OFF-default):
  at each milestone boundary, before the expansion draws the sub-plan,
  one cheap call asks closure's goal-level question against the
  committed interpretation (read from the run's `resolved_intent.md`
  artifact; goal text when absent). On drift the anchor note joins the
  expansion's `ancestry_context` — the sub-plan about to be drawn is
  re-anchored, nothing stops or replans (heavier correctives must earn
  wiring with data). Every verdict → `build/reanchor.jsonl` +
  `REANCHOR_CHECKED` event; the map lens renders them (⚓ in text/json,
  drift-only nodes in mermaid). The experiment's falsifier per item 3:
  if these records catch real drift, coherence never needed to be its
  own signal; if N boundaries pass with zero drift caught while
  closure still fails runs for drift, the cadence hypothesis is wrong.

**§9.9 backward-chaining SHIPPED 2026-08-15 (same stretch, "now is
later" decree)** — `src/backchain.py`, `planner.backchain` OFF-default.
Exactly the doc's shape: goal regression as a RECON generator. One call
regresses the goal against the forward plan ("what must be true just
before?"), classifies links established/verifiable/unknown (a step
reference outside the plan or a probe-less "verifiable" is downgraded —
no fabricated establishment); unmet verifiable preconditions become
`[recon:]` probe steps prepended to the plan (max 2 — draw_cuts' cap;
rides the §9.2 machinery wholesale). Chain → build/backchain.jsonl (append; post-injection step refs) +
BACKCHAIN_DRAWN; map lens renders the backward frontier (✓ established /
⌕ probe / ○ unknown) — established links ARE the frontier-convergence
evidence of §3, now visible per-run. Wired BEFORE pre-flight review so
milestone indices (the §9.5 keys) are computed against the final list;
skips cuts boundary plans (that lane already buys information) and
preset/rule plans (operator-authored). Named "backchain" because
"regression" is owned by the eval subsystem.
