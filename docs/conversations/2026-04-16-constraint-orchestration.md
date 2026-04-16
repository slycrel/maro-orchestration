================================================================================
  Session Log — Constraint Orchestration: Systematizing Good Judgment
  2026-04-16
  Jeremy Stone + Claude (Sonnet 4.6)
================================================================================

BACKGROUND
----------
This conversation followed Phase 64 (adaptive execution) implementation and a
regression test against the original slycrel-go goal that motivated Phase 64.
The slycrel-go test produced two useful outcomes: a real bug in adaptive
execution's NEXT.md index handling was found and fixed, and a PR was opened
against slycrel-go for a headless-server branch.

But the deeper output was the conversation that followed, where we stepped
back from the implementation to examine whether what we're building actually
gets us closer to the north star, and if not, what's missing.

This log captures that conversation because the synthesis — documented in
docs/CONSTRAINT_ORCHESTRATION_DESIGN.md — loses the thinking that produced it.
Future work in this area should read both.


OPENING — IS THIS FANCY HAND-WAVING?
=====================================

JEREMY:
  Honest opinion. Is this going to get us closer to what we're trying to do?
  Or just more hand-waving?

CLAUDE:
  Phase 64 itself is real — having the director watch mid-loop instead of just
  at start/end is the right architecture. And we found and fixed a real bug
  that would have silently corrupted NEXT.md on any goal that triggered
  adaptive execution. But the slycrel-go PR is a test artifact, not a
  deliverable. The code compiled and the structure looks reasonable but nobody
  reviewed it at depth — it's "plausible" not "correct."

  The more honest concern: in both test runs, adaptive execution didn't
  actually intervene. The goals completed cleanly without the director needing
  to course-correct. The original problem it was built to solve — loop stops
  at phase 1 — didn't reproduce.

  Phase 64 is real infrastructure but it's unproven on the cases that
  motivated it.


REFRAME — ARE WE BUILDING AN EXPENSIVE MODEL TRAINER?
======================================================

JEREMY:
  So... have we built a really fancy model-trainer for an LLM, but crazy
  inefficient because we're just using tokens to thrash around to get the
  training data?

CLAUDE:
  There's real truth to that. What the system actually does is accumulate
  structured text (skills, lessons, playbook entries) and injects it back
  into prompts. The model weights never change. The "learning" is in-context —
  paying tokens to discover things that then live in .md files and get
  retrieved into future context windows. It's prompt engineering with extra
  steps and a persistence layer.

  The uncomfortable version: yes, we're running RLHF in the most expensive
  and lossy way possible. Each lesson costs hundreds of thousands of tokens,
  gets stored as natural language, retrieves imperfectly, and disappears if
  the workspace gets wiped. A fine-tuned model would make that learning
  permanent and cheap.

  But two things break that framing:
    1. The orchestration layer has real value independent of learning.
       Decompose → execute → verify → retry is genuinely hard to get from a
       single LLM call. A fine-tuned model still needs a loop.
    2. You don't have training access. In-context learning is the only lever
       you can pull.

  Honest reframe: what we've built is closer to a reliable autonomous
  execution environment than a self-improving system. The "self-improvement"
  framing is aspirational but the mechanism (lessons in context) is weak.


JEREMY:
  I have to object a little at the "reliable" part. If I can't trust the
  small things we're piping through here, how would I expect that the more
  complex stuff is going to be closer to complete? Those would get better
  training data, but not necessarily better results. Sonnet/opus should have
  all the training data needed and available to fully realize a complete
  slycrel-go client/web interface, fully working. The LLM in a single prompt
  likely can't do it... and what we've built is missing the mark as well.
  Claude Code isn't quite right harness-wise, but might get us closer. I
  want to believe, and I can see some edges like... deep thinking in LLM
  models types of behavior we're trying to re-create. But we seem to be
  missing the perspective shifting that would allow us to leverage everything
  we have in the models or to help us plan the proper orchestration runs.

CLAUDE:
  The slycrel-go server "passed" because it compiled and the loop declared it
  done. Nobody ran a browser against it. That's the actual gap: verification
  against reality vs. verification against LLM judgment. The system is very
  good at producing plausible things and very bad at knowing whether they
  work.

  The "perspective shifting" you're describing is two distinct things:

    Zoom out — "Is this the right architecture? Are we building the right
    thing?" Requires stepping back from implementation and holding the full
    picture. Current orchestration destroys this by chopping execution into
    isolated steps.

    Ground truth feedback — "Did this actually work?" Requires running the
    thing and getting real signal back. Not LLM judgment. Compile errors,
    test failures, runtime behavior.

  Claude Code gets closer on both because it maintains context across the
  whole task and can actually run things iteratively. But it still struggles
  with the zoom-out move — when you're 40 tool calls deep in implementation,
  something needs to say "stop, look at what you've built, is this coherent?"

  Extended reasoning models do the zoom-out naturally in their thinking
  stream. The chain-of-thought holds competing framings simultaneously —
  architect + implementer + critic — without having to explicitly orchestrate
  between them.


THE CORE INSIGHT — ZOOM + ROTATION
====================================

JEREMY:
  My hope was that we could have the system provide the zoom in/out... and
  personas to add 360 degree rotation around the goal. Rotation alone isn't
  enough, and zoom in/out for granularity/clarity at different levels isn't
  enough. Both together might be enough, or at least closer. This gets into
  the next level stuff that maybe we're getting ready for (personas and
  skills that are built in; and a way to choose them in a meaningful way).

CLAUDE:
  The zoom + rotation frame maps directly onto how expert teams actually
  work. A senior engineer working alone on a complex problem does this
  naturally: zoom out to architecture when something feels wrong, rotate to
  the user perspective when unsure what "done" means, zoom into a specific
  failure when debugging, rotate to "what would break this" when reviewing.
  They don't do all of these simultaneously — they know WHEN to switch.

  That's the part we haven't solved. We have primitive versions of the
  pieces:
    - Director = architect perspective, but only at planning time
    - Inspector = QA perspective, but only looking for friction signals
    - Advisor = fresh eyes, but only when stuck
    - Ralph verify = critic, but only at step level

  What's missing isn't more personas — it's the selection trigger. Knowing
  that THIS moment calls for a zoom-out, or that THIS output needs a
  user-perspective rotation before continuing.

  The other thing almost completely absent: the user/behavioral perspective.
  Every persona we have is production-facing. Nobody is asking "would a
  human actually want to interact with this?" That's why the slycrel-go
  server passed — no perspective that cared about the player experience was
  invoked.


THE RECTANGLE — CONSTRAINT PROPAGATION AS SCOPE REDUCTION
===========================================================

JEREMY:
  There's a skill that I've talked to a co-worker about recently that people
  use, mostly unconsciously, that you develop as a builder. Hard to describe,
  I usually draw on a whiteboard for it. It's something in the direction of...
  a rectangle, with a slash through it, top left to bottom right-ish,
  dividing 40% from the rest. Then another line, from 40% up the first line,
  drawn up to the upper right, slicing off another 30-40% of the remaining
  space. Repeat that counter-clockwise twice more and you're left with a
  much smaller area left in the middle.

  That remaining area represents a goal you are working towards. Drawing the
  lines represent constraints on the work towards a goal. The constraints
  themselves can be almost anything, and they serve a sort of binary search
  type constraint of sorts; you can ignore all of the context "outside" of
  the boundary because you've chosen what is inside the boundary.

  An example line might be choosing REST API calls — known, bounded
  communication context. Everyone interacting on the project now has guides
  in that regard and can skip all the problems that don't have anything to
  do with REST and also don't have to ask for details on how that all might
  work, maybe outside of data payloads and data format within that context.
  4-5 good decisions suddenly gets your scope down from "anything is
  possible" to "we're in the space, now how do we meaningfully implement?"

  I think this is part of what we need to build, and relates to what we are
  talking about; it's essentially "good judgement" systematized.

CLAUDE:
  What you're describing is constraint propagation as scope reduction. Each
  decision eliminates a large portion of the solution space — not by solving
  problems but by making them irrelevant. "We're using REST" doesn't solve
  the data format problem, it eliminates every non-REST problem from the
  conversation entirely. Four or five of those decisions and you've gone
  from infinite possibility to a bounded, navigable space where the remaining
  work is implementation, not discovery.

  The distinction you're drawing at the end is key:

    Refinement decisions (the lines on the rectangle) — "what are we NOT
    doing?" Require judgment, perspective, understanding of tradeoffs. An
    architect draws a different line than a product manager. Personas are
    the right frame here.

    Implementation decisions — "how do we do this specific thing within the
    committed constraints?" This is where skills come in.

  What we've built does almost none of this. The planner gets a goal and
  immediately tries to fill the full solution space with steps. There's no
  "what are we NOT doing" phase. The constraints need to be legible to all
  downstream personas — a constraint that isn't legible doesn't actually
  reduce scope, it just creates ambiguity.


INVERSION — CONSTRAINTS VIA FAILURE MODES
==========================================

JEREMY:
  There's an article I grabbed but didn't read that is probably relevant.
  Five prompts about inversion thinking.

  https://x.com/MillieMarconnni/status/2044358003714097601

  "Inversion is the most powerful thinking tool most people never use
  correctly. They invert the goal. They don't invert the system. I turned
  Claude into a full inversion engine that runs Charlie Munger's method on
  any problem mapping every path to failure so precisely that the path to
  success becomes obvious by elimination."

  I agree that we've built an orchestrator for inside our constraints. I
  think we need to be able to add constraints, and this is integral to the
  planning step, but might apply in general to all director touches, both
  up front and along the way as new information becomes available.

CLAUDE:
  Inversion doesn't ask "how do we succeed?" — it asks "what are all the
  ways this definitively fails?" Those failure modes, once enumerated, ARE
  the constraint lines in your rectangle. The path to success isn't designed,
  it's what's left after you've systematically eliminated the failure modes.

  For the slycrel-go server, an inversion pass upfront might have produced:
    - Fails if game goroutine blocks on I/O the browser never responds to
      → constraint: timeouts on every I/O operation
    - Fails if WebSocket drops and game state is lost
      → constraint: session persistence before any game logic
    - Fails if browser can't handle ANSI escape codes
      → constraint: terminal emulator or strip ANSI server-side
    - Fails if we can't test without a live browser
      → constraint: IOProvider must be mockable

  Four constraints set before a single line of code. The decomposition that
  follows is completely different — and much higher confidence — because
  it's operating in a bounded space.

  There needs to be a pre-planning phase explicitly separate from planning:
    1. Inversion pass — what are the 5-7 ways this goal definitively fails?
    2. Constraint extraction — what decisions eliminate the most failure
       modes?
    3. Human gate (unless yolo) — "Here are the constraints I'm working
       within. Correct anything before I start."
    4. Bounded planning — decompose inside those constraints.

  One-shot prompting can't work for exactly this reason. It conflates the
  inversion/constraint phase with the execution phase into a single inference
  pass. Separating them makes those decisions explicit — which is also where
  the human touch is most valuable. You don't want Jeremy approving
  individual steps. You want Jeremy at the constraint gate, where decisions
  are at the right abstraction level.


ANGLES BEFORE DOCUMENTATION
============================

CLAUDE:
  Before we document, some things I think are load-bearing:

  When NOT to do this. Constraint-setting is expensive. Simple AGENDA goals
  shouldn't pay the inversion tax. The gate decision is itself judgment.

  Constraint conflicts between personas. If architect draws "must be testable
  in isolation" and PM draws "must work end-to-end as a unit" — those lines
  cross. Need a resolution path that isn't just "whoever ran last wins."

  Emergent constraints during execution. Not all failure modes are visible
  upfront. The director needs a new trigger: constraint-review (distinct from
  goal-drift).

  Violation detection during execution. A constraint is only valuable if the
  system catches violations. Currently nothing catches "this code violates
  the 'IOProvider must be mockable' constraint." Constraints become
  aspirational without enforcement.

  Composition with existing phases. I don't want this to become a parallel
  track. Is constraint-setting an extension of pre-flight? Does it replace
  completion-standard-injection?


JEREMY:
  Agree on all. Going to be tricky to code "good judgement" but the target
  is in that direction.

  Conflicts between personas — if we had infinite tokens, I'd want a bunch
  of personas spun up, all give their take, and then have a coordinator
  (or the director ultimately) choose from a large variety of data, in
  service to the overall goal. For difficult decisions maybe 3 or 7 personas
  would be good, but I hate to hard-code that number. I have seen a few
  things out there referencing a team of 3, IIRC product, engineer, and
  architect as one example.

  Violation detection — director always to start, but ideally there would be
  orchestration delegation making those decisions in between each step.
  Implementation could be validation, custom hook by the director, skill-
  based eval, etc.

  Composition with existing phases — not looking to add complexity. My hope
  is this reduces it by eliminating things we need to worry about, bringing
  clarity and focus in both the plan and how we approach implementing the
  steps that have been broken down.

  Constraints go both ways. All rules have exceptions and sometimes it's
  elegant to "think outside the box" we are defining, and that's a different
  form of good judgement. The more extreme version might be "we need to
  rethink some steps" or even "well that didn't work, let's scrap all that
  and start again."

CLAUDE:
  Constraint-breaking is its own mechanism. There's a meaningful difference
  between two cases:
    (a) The constraint was right but this instance is a justified exception
    (b) The constraint was wrong and needs updating
  Both look like "break the rule" in the moment, but they produce different
  learning. Phase 64C's escalate action has a natural home here — same
  infrastructure, different trigger.

  Constraint representation matters more than it seems. Natural language is
  expressive but not mechanically checkable. Structured constraints are
  checkable but stiff. Hybrid (NL statement + optional check method where
  feasible) is probably right — "must compile clean" has a shell command
  behind it; "must be mockable" is LLM judgment.

  Inversion quality is its own failure mode. If the system enumerates the
  wrong failure modes, constraints will be wrong, and bad constraints are
  worse than no constraints because they actively misdirect. The human gate
  isn't approval theater — it's the quality check on the inversion pass
  itself. Over time, the memory layer could bias future inversions toward
  historically-useful failure modes.

  Constraint-level outcomes are better training signal than step-level
  outcomes. "Did step 5 succeed?" is noisy and low-signal. "Did the
  constraint set we chose produce a working system?" is structured,
  high-signal, and the right granularity to learn from. This is an argument
  for investing in constraint-record quality even if the immediate token
  cost looks steep.

  The triad (PM/engineer/architect) is worth thinking about as default.
  Three perspectives rotate the problem genuinely differently. Seven is
  probably overkill except for high-stakes or high-ambiguity goals.

  Meta-point: what we're describing has two implementation profiles. A
  minimal version bolts constraint-setting onto pre-flight with the simplest
  possible representation and a single trigger. An ambitious version
  reorganizes around constraints as first-class artifacts. Build the
  minimal version first — it can ship in a week and tell us whether the
  idea has legs.


SYNTHESIS
==========

The conversation produced one core thesis and several supporting frames:

CORE THESIS:
  Most of what makes expert engineers reliable is not that they execute
  well — it's that they execute inside a deliberately-narrowed solution
  space, and they know how to narrow it. The orchestrator currently operates
  against an unbounded space. Narrowing that space — systematically,
  inspectably, and eventually learnably — is the load-bearing move we
  haven't made yet.

SUPPORTING FRAMES:
  - Constraint propagation as scope reduction (the rectangle)
  - Inversion as the technique for generating constraints from failure modes
  - Zoom + rotation as perspective orchestration (personas across levels)
  - Refinement vs. implementation as distinct activities requiring different
    mechanisms (personas for the first, skills for the second)
  - Constraint lifecycle as set/inject/detect/revise/except/break — not static
  - Human gate at constraint level (4-5 strategic decisions) rather than
    step level (40 implementation choices)

The design document docs/CONSTRAINT_ORCHESTRATION_DESIGN.md captures the
synthesis in architectural form. This log captures the conversation that
produced it — including the tensions Jeremy surfaced that aren't fully
resolved in the design doc.


WHAT'S NEXT
===========

1. Independent agent review of the design document (fresh perspective on
   what's being inferred or missed)
2. Implementation plan for the minimal profile
3. Phase 65 entry in roadmap/backlog once the plan is reviewed

See docs/CONSTRAINT_ORCHESTRATION_DESIGN.md for the architectural frame.


================================================================================
  "The path to success isn't designed — it's what remains after you've
   systematically eliminated the failure modes."
================================================================================
