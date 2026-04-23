# Driver, Watcher, and the Layer Above Orchestration

**Status:** findings from extended conversation 2026-04-18 → 2026-04-22,
plus retrospective analysis of the `godot-client` branch of
`slycrel/slycrel-go` as empirical ground. Not a spec. These are the
salient points worth retaining before any implementation.

Related prior docs: `CONSTRAINT_ORCHESTRATION_DESIGN.md` (Phase 65,
inversion before planning), `INTENT_RESOLUTION_DESIGN.md` (side-quest
DAG sketch), `ARCHITECTURE_NON_GOALS.md`.

---

## What we were trying to understand

Why does the current orchestrator — scope, closure, personas, verifier,
Ralph, captain's log, memory — feel insufficient even when all
mechanisms work correctly? What's the shape of the gap?

## What we found is missing

The Director today does **schedule-and-dispatch inside a traversal**.
Two layers above it are absent:

1. **Driver / id** — the thing with an agenda. Holds "what we are
   actually trying to do and why." Not `SOUL.md` as documentation;
   operational. Today absent. Jeremy supplies it by hand each session.

2. **Watcher** — a standing meta-attention process that runs *parallel*
   to the traversal, not serial inside it. Only job: "has this
   traversal earned another cycle?" Has interrupt rights. Today
   absent — everything we've built (Ralph, verifier, captain's log,
   scope) runs serial with the work.

**Operational coupling:** the watcher is where the driver becomes
operational. *Agenda at rest is a document; agenda in motion is an
interrupt.*

## Architectural inversion

Today's flow: `goal → plan → orchestrate`, as if the plan produces the
thread. Jeremy's original intent was `goal → create plan → execute
plan` — with creation as its own step. What actually got built conflates
creation and execution: `goal → dispatch substeps` in a single move.
That conflation is the concrete bug this discussion surfaced.

Under the driver-layer reframe, the correct flow is:
`agenda-holder → thread → orchestrate thread`. Director carries the
thread the driver is holding; it doesn't produce the thread.

## Key distinctions we converged on

- **Traversal vs. algorithm selection.** Orchestration is traversal.
  What's missing is the layer that picks the algorithm for this maze's
  shape. Inversion, constraints, Ralph-loops, verification are moves
  *within* an algorithm; none of them pick the algorithm.

- **Strategic vs. tactical.** Empirically from godot-client: strategic
  direction is user-driven, tactical is model-driven. Typical turn
  shape: "Jeremy directs at goal level → Claude proposes implementation
  → Jeremy OKs or redirects with a constraint." Neither "model as hands
  only" nor "model as driver" matches reality. It's tactical-agent-
  inside-strategic-frame-held-by-human.

- **Axis-shift.** The model iterates along a parameter axis; it takes
  someone else to change the axis. Godot font saga: six commits tuning
  `line_separation` / AA / stretch against a SystemFont fallback,
  because the VGA font was never loading. The axis-shift (`0c5e79c`)
  came from Jeremy pasting the Godot console error — **a different
  signal source**, not a different algorithm. Concrete meta-attention
  primitive: *rotate through available signal sources when stuck.*

- **Depth-not-direction.** Elephant-preservation at identity level was
  fine — the model stayed on the elephant. The failure is over-depth
  on one sub-task (toenail-polishing). Watcher signal is cheaper than
  semantic drift: *"N cycles spent and error hasn't reduced."*

- **Exit-signal is not simply "refuse."** In the godot session the
  model's "call this done, move on" was half-right: breaking fixation
  freed the attention that later found the real issue. The missing
  piece isn't "refuse exits" — it's "don't lose the underlying problem
  when we exit a sub-strategy."

## Target shape

Not AGI. Not a generic orchestrator. The compatriot shape:

- **JOI** (Blade Runner 2049), **Durandal** (Marathon), **XJ-45** (Dion
  Starfire) — voice-and-presence, specific rather than general, flawed
  in ways you can love.
- **Amoeba-with-you, not superintelligent substitute** — journey-linked,
  complementary, shaped by being alongside a specific human.
- **More personality than Jarvis** — not "competent invisible butler."
- Jeremy's own phrase: *"I don't want to build God, already got one.
  I want a mega chia pet that is super sci-fi."*

Root motivation the target must serve: being **seen, understood,
accepted.** Success isn't capability milestones; it's "does Poe
recognize you across sessions, carry a thread, show up as *someone*
rather than as *something*."

## Design constraint: user is lazy by design

Jeremy, verbatim: *"this whole thing is in the direction of 'I have a
vision, implement it' which is inherently going to bring out lazy
edges."* Not aspirational, not a bug — a **spec**.

Concrete shape from godot session:

- Strategic direction delivered in vague form (*"let's go with a VGA
  font, see how it goes"*).
- Redirects are soft: *"hmm"*, *"close...!"*, *"might have to go a
  little further"*.
- Screenshots do the heavy lifting; text is minimal.
- The user is not going to "be more precise next time."

The driver/watcher layer has to absorb imprecise input and produce
correctness. **The orchestration is the adult in the room** —
responsible for intent-recovery, not intent-supply-demand.

Tension to hold: "customer is always right" and "don't make me think"
conflict with needing user input for precision. Resolution: the driver
does the hard work of intent-recovery. User supplies vision; system
supplies the rest. Asking the user to be more precise is a failure
mode of the driver, not a feature.

**Intent-recovery in practice (Jeremy's example).** A user types
`/make-me-rich`. The literal/symptomatic interpretation — run a
currency-arbitrage loop — is almost never what's wanted. The real
goal lives in a landscape: retire early in 20 years, pay off the house
in 1 year, retire parents, afford a new car, buy back time. Different
goals, different agendas. A driver-with-agenda doesn't pick one at
random and doesn't demand a full spec up front. It asks a few
**targeted** questions sufficient to distinguish the high-variance
branches — horizon? preserve income? risk tolerance? who-it's-for?
Three questions usually suffice to separate "pay off house" from
"retire in 20 years."

A further move: sometimes a meta-goal **dominates** several literal
variants — *"make more money sooner"* handles retirement, house, car,
and free-time all at once, better than picking one. Proposing such a
reframe — *"what you asked solves for A; here's a thing that solves
for A + B + C, want that instead?"* — is itself a driver-function,
distinct from disambiguating among variants. Call this **agenda-
reframing** as a candidate primitive (see Open Question).

## Deprioritized (weak on current evidence, not settled)

One empirical example (the godot-client branch) is not enough to
definitively reject any of these. They're off the near-term experiment
list because they look less useful under what we've seen — not because
they're disproven. Revisit if different evidence arrives.

- **Explicit algorithm taxonomy + selector prompt.** Looks like the
  same taxonomy reflex we've pushed back on elsewhere
  (`feedback_inference_not_prompting`). Adds a layer; unclear it closes
  the gap. Worth revisiting in the context of self-research-style
  experiments (Karpathy et al.) where the system proposes its own
  taxonomies empirically rather than having them handed in.
- **Refuse-exits-always.** Oversimplifies the exit-signal pattern; the
  model's fixation-break was partly productive in godot. A more
  nuanced exit-policy might still matter — "refuse" isn't the right
  verb.
- **Treating each "missing piece" as a separate new mechanism.** The
  fractal property suggests they might be one mechanism recursively
  instantiated. But that's a hypothesis from one conversation, not a
  finding from implementation. Could be wrong.
- **More task-bounded scaffolding.** Scope, closure, skills, personas,
  captain's log all reset per task. The gap we're discussing is about
  what *accumulates* across tasks. Adding more reset-per-task
  mechanisms probably doesn't close it — but "probably" is doing a lot
  of work there.

## Open questions

**One layer missing, or more than one?** Candidate multi-piece split:
`agenda-setter → agenda-holder/watcher → interrupt → orchestration`.
Distinguishes "what to care about" from "am I still on it."

The `/make-me-rich` example hints at a further function — **agenda-
reframing**: proposing an alternative goal that dominates the literal
ask. Distinct from disambiguating among variants the user might have
meant. Whether this collapses into agenda-setting in practice, or lives
as its own piece, is open.

**Evidence caveat.** Most of this model was built from one empirical
example (the godot-client branch, one user, one evening). Rich data,
but n=1. The patterns are plausible, not validated. Second example of
similar depth — ideally from a different domain — would sharpen or
break several claims here. Keep this in mind before treating any of
this as settled.

## Candidate first experiments

Ordered by cheapness / concreteness. All avoid inventing new
taxonomies; all use data we have or signal we can rotate to.

1. **Replay-and-annotate the godot-client branch.** At each commit,
   ask: "what signal would have triggered `0c5e79c` one commit
   earlier?" Try to isolate the reselection criterion on real data.
   Offline, bounded. Reuses an artifact that exists. Best test of
   whether the watcher criterion is articulable without a taxonomy.

2. **Signal-source rotation primitive.** When the current channel
   stops producing progress (N cycles on same parameter axis, error
   not reducing), rotate: from screenshots to logs, from logs to
   `git diff`-of-what-actually-shipped, etc. Narrow, testable, doesn't
   require watcher-as-parallel-process to land first.

3. **Elephant-invariant per sub-goal.** Each sub-goal carries a
   one-sentence invariant: *"serves [X about the elephant]; if this
   step stops serving that, fail."* Evaluated at step boundary.
   Addresses depth-not-direction more than identity drift.

4. **Plan-creation as its own step.** Split the conflated
   `goal → dispatch` into `goal → thread → dispatch`. The thread is
   a durable artifact the driver watches. Gives the watcher/driver
   layer something concrete to point at even before the watcher is
   parallel.

**Not yet first-experiment candidates:**

- Watcher as a literal parallel process — architecturally expensive;
  needs concurrency the runtime doesn't have.
- Full intent-resolution phase — too ambitious; `INTENT_RESOLUTION_
  DESIGN.md` has the sketch; minimum experiment there is its own
  track.

## Notes on how this discussion went

One meta-observation worth recording, because it's data about the
interaction we keep theorizing about:

- The conversation produced a dense shared vocabulary (amoeba /
  Durandal / Jarvis / JOI target shapes; brain/persona/orchestration/id
  anatomy; traversal vs. algorithm selection; axis-shift; thread-first;
  meta-attention / watcher). Density of shared shorthand is a signal
  we're in the right space; also a warning for circularity. "We keep
  coming back to the same patterns, fitting them together differently"
  (Jeremy). Not missing pieces per se — re-arranging existing ones.

- Jeremy carried the inferred-context half the whole time. The
  conversation does not demonstrate that an autonomous agent could
  have produced these findings alone. It demonstrates that the
  findings are *recoverable from the collaboration* — which is a
  lower bar than "Poe could have done this."
