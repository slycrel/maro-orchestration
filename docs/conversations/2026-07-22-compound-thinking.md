---
status: history
---

# Conversation — Compound Thinking (the self-surveying map)

> *"Named for Publius Vergilius **Maro** — the poet Virgil, who in Dante's*
> Inferno *guides the traveler down through the dark and safely out the other
> side. Maro does the same for autonomous agents: hand it a goal and it finds
> the path through — decompose, execute, recover, report."*
> — the project's own README epigraph

**The name was the model all along.** This conversation spent two hours building
up a picture — believe an exit exists, descend into the fog, pathfind between
landmarks, recover past blocked routes, come out the far side — and only at the
end did Jeremy re-read the README and notice: *Publius Vergilius **Maro*** is
Virgil, and "guides the traveler down through the dark and safely out the other
side" is the fog-of-war maze we'd just spent the whole conversation describing.
We unconsciously named the concept before we said it out loud.

**What this is:** a full verbatim transcript, kept because we've repeatedly gone
back to raw conversation logs and gleaned new edges once new context arrives
(Jeremy's gut: this will be one of those). The *synthesis + recommendations* live
in `docs/COMPOUND_THINKING_DESIGN.md`; this is the raw material behind it.

- **Participants:** Jeremy ↔ Claude (Opus 4.8, 1M ctx)
- **When:** 2026-07-21 → 07-22, on the dev M1 while the swarm-review arc
  (chunks 1–5) ran on the box
- **Product:** `docs/COMPOUND_THINKING_DESIGN.md` (chunk 6 — the next spiral)

---

## Turn 1 — Jeremy

> I'd like to discuss what I'm calling compound thinking a bit, in regards to
> maro orchestration.

## Turn 1 — Claude

*(after re-reading the maro-orchestration project memory)*

Where I *think* you might be going, given maro's existing grain — three pretty
different animals that rhyme:

- **Thinking that compounds across runs** — the VERIFY_LEARN_ARC instinct: each
  run deposits reasoning the next run stands on. Compounding in the interest
  sense.
- **Thinking that compounds across agents/models** — the Codex-reviews-Claude
  pattern. Compound in the *ensemble* sense: distinct lenses stacked so the
  product is more correct than any single pass.
- **Thinking that compounds within a single problem** — the C1→C4 staging, where
  each chunk's design constrains and de-risks the next, versus one monolithic
  leap.

Questions back: Is this a *mechanism* you want maro to have, or a *principle* to
evaluate the design against? What's the unit that's compounding? What triggered
it?

## Turn 2 — Jeremy

> The third is the closest, though all have some merit and are interesting. I
> think this stems a bit from the "ask -> taste (what to do) + judgement (did it
> do) -> result" pattern we've identified. Using the manti example from the repo,
> that's compound thinking; composing a number of specific small asks together to
> get the desired result. Not a leap, a meandering to find a specific result. No
> single step is particularly difficult, but all together it creates a complex
> mechanism that isn't easily leap-able on it's own. Seeded with educated guesses,
> pre-existing background context (training data, memories, etc) along with
> intentionally built knowledge (research tasks) along the way.

## Turn 2 — Claude

*(after pulling the Manti runs from GOAL_BRAIN)*

**The distinction that makes the definition load-bearing:** a compound goal isn't
un-leap-able because it's *big*. It's un-leap-able because **you can't write its
spec up front.** The target gets co-discovered with the path; each small ask
produces a partial result *and* refines what the next ask should be. Corollary:
**you cannot route your way out of a compound goal with a bigger model** — the
missing ingredient is specification, not horsepower. The Manti run proves it — the
naive router "sent it NOW (0.85 conf)" to a capable model and FAILED the Tier-1
contract with the "passenger-does-the-steps" anti-pattern.

**Taste and judgement are not equally delegable — that's the whole autonomy
question.** Judgement ("did it do X") is verification-shaped and maro automates it
(closure_verify, quality_gate, adversarial review). Taste ("what to do at this
fork") is scarce and historically yours. Most interesting data point in the Manti
arc: cuts planning "drew Jeremy's exact human heuristics unprompted — 2 committed
steps vs the baseline's 7." That's taste replayed from crystallized heuristics.

**Seeding is the actual lever:** the quality of the meander is bounded by the
quality of the seeds. Want cheaper compound thinking? Make the seeds better, not
the model smarter. Resolution of the meander-vs-cost tension: **meander cheaply in
taste-space, commit expensively rarely.**

Question back: (a) make maro better at *supplying its own taste* at forks, or (b)
make the human taste-forks *cheaper and better-placed*?

## Turn 3 — Jeremy

> My answer is... Both and maybe more. :) go ahead and pull, I've got fable running
> some concrete implementation steps right now. (5 chunks, I think we might be on
> chunk 2 or 3) There's some back story and discussion that might help get you up
> to speed from earlier today on the maro box (we're here on the M1). I think I'd
> like to explore some tangential-but-related stuff at the higher level before
> dipping into implementations around refinements to maro directly. In particular
> my mind goes to our step planner, educated guesses and discoverability. I keep
> bringing up the whole "learn a language to draw kanji" example from our early docs
> this week. I think because it captures the meandering-like-but-not nature of what
> I'm trying to describe. If you take a leap approach, it's ridiculous to try and
> learn a language to do... just about anything. Yet that might be the exact
> opposite-but-necessary meander that gets you to what you want (even if there might
> be more optimal ways like "hire this out via contract on the internet" or "ask
> someone who already knows the language on reddit for help"). You have to have some
> degree of certainty that the goal is possible (difficult != impossible, unknown !=
> impossible, unclear != avoid, etc). Then you have to use some base assumptions on
> where to start (the Qix cuts, aka an initial plan), then you have to ... prove the
> thesis so to speak, in small "meandering" chunks; I think this is why the maze
> traversal keeps coming to mind--you don't need to know the path to the exit, just
> have a pretty good idea that there is one. You may discover the path is blocked or
> unreachable along the way, doesn't mean the exit (or idea of one) is a poor or
> impossible goal that justifies a non-start. There's probably more here as well,
> and I'm not sure if we've fully/properly identified all of the nuance here, thus
> the conversation.

## Turn 3 — Claude

*(after reading the kanji example in `INTENT_RESOLUTION_DESIGN.md`, the fresh
`DEV_PATTERNS.md`, the `star` skill, and today's GOAL_BRAIN entries)*

You've already built the *accelerator* today — Possible-now bias, cuts-first, and
star's "0..n steps discovered as you go." The nuance not yet named is the
**steering and the brake**. Four pieces:

**1. The maze smuggles in a belief that has to be updatable — only one side is
built.** Possible-now bias guards *under*-persistence. But star's stops are budget
cap (a *cost* stop) or the master's taste "declares blocked" (the one place left to
raw vibes). Your belief wants to be a posterior that blocked routes update
*downward* — slowly/asymmetrically, because N dead ends is weak evidence of no exit.
Too eager = quit a possible goal (Jeremy's fear); too reluctant = grind budget on
an impossible one.

**2. Two kinds of probe — discoverability lives in the one not in the loop.**
Star's TASTE step is hypothesis-directed. Discoverability needs *reconnaissance* —
a probe whose success is "found an unknown I hadn't named." The kanji punchline:
closure "can only catch gaps the planner was already worried about." Two currencies:
probe-cost (cheap) vs commit-cost (expensive); recon can delete committed branches
before you pay.

**3. Cuts-first should be cuts-continuously — Qix demands re-cutting.** Every probe
that resolves an unknown is license to redraw the cuts. Authority distinction:
re-cutting is a master taste act, logged; drift is a sub-agent doing it silently.

**4. The possibility estimate is where Jeremy's deferred edge bites first** —
taste maturation needs consequence-coupled reps; the `surprise` column + verified
outcomes are the training signal.

Question: the possibility-brake (end of the meander) or the reconnaissance gap
(its middle)?

## Turn 4 — Jeremy

> This is good, thanks. Helping me see the thing I'm trying to name, that's
> semi-intuitive inherent in the way that I think. I think you're right and there's
> more. :) 1- agree here, we're not fully baked on this yet. In this direction, I
> was thinking of how people talk about "connecting the dots" and "identifying
> milestones" along a journey. We need to add that ability, and it goes both ways...
> you're as likely to find parts of the path walking backwards from the goal as you
> are forwards towards it; in that sense there is no start and end of the maze, only
> starting positions trying to pathfind to each other, and the "plan" is either the
> string of milestones, the dots between them, or both. Come to think of it, we
> literally visualize it that way intuitively as people; see every treasure-hunt or
> pirate map ever... multiple destinations with dashes or dots indicating a path to
> each (sometimes very explicitly, sometimes indicating they are connected in a
> particular order, but the path isn't as straight as the broken lines between)...
> we're going to need dynamic pathfinding (from both ends), educated guessing
> (theories / faith-based plans?), along with building an initial context (up front
> Qix cuts) and then maintaining the changes over time as you mentioned... and more.
> Still, and always... Lots here, heh. And yeah, I did touch on a few of the other
> points here I suppose. 2- I think this is in the direction but it needs more
> refinement; not that you're necessarily off track here but it doesn't feel fully
> complete. Naming the steps and anti steps is important and I think we need to name
> reconnaisance as a type/flavor of step, as it might need some different bounds,
> verification or prompting when it comes back (not that it's a new step shape).
> Aditionally you're dead right that cost isn't really related, and that's important
> (impossible declaration is the result of a discovered $10k-possible-but-unreachable
> token spend goal) 3- this one's pretty important and I'm disappointed fable didn't
> get this one right. I'd frame it a little differently--the plan is a thesis up
> front, whose details can overall change over time (new or moved cuts) as the path
> becomes more clear towards the goal. I like your observation about Qix directly
> though -- the entire game revolves around calculated risk, avoiding obstacles and
> random blocks (death) as a setback. And the LLM 1-shot is like splitting two Qix
> on either side of a full board line; an insta-level-win condition that isn't always
> possible or likely. 4- also with 3, the new surprise mechanism is a little fuzzy to
> me in implementation terms, and I think you're right is only part of the picture
> here. I agree that we need a way to refine that; partly that can be crowd-sourced
> live in a step, partly that's training data, and partly (and most impactful for
> our project?) learned lessons we can apply to our dynamic methodology. I like the
> idea to test some of this with the star skill, but I think we're not quite there yet.

## Turn 4 — Claude

**The crystallizing move: the plan was never a path — it's a map, a *surveyed*
one.** A path is a linear sequence; a map is *landmarks you're confident exist*
plus *dashed edges you're guessing at*, and the confidence gradient is **inverted**
— you're surer of the destinations than of the route between them.

- **"No start and end, only positions pathfinding to each other"** is bidirectional
  search; the backward half is *goal regression*. The kanji tower is literally a
  backward chain (draw kanji ⇐ stroke order ⇐ script ⇐ language).
- **Frontier convergence is the possibility estimate** — this quietly solves the
  point-1 brake. Connecting landmarks → P(path) up, structurally. Both frontiers
  stall → the non-vibes signal to lower it. Blocked = "the two searches stopped
  converging," *not* "spent enough." Handles the $10k case: the path *connected*
  (thesis TRUE), but the connecting edge carries a discovered cost above value →
  **reachable-but-not-worth-it**, distinct from *unreachable* and from
  *out-of-budget*. Three separate stops.
- **Recon is a step-flavor** whose *return type* is map edits (landmarks, edges,
  reachability+cost, new unknowns), verified differently. Backward-chaining is a
  recon flavor; cost-discovery is a recon output field, not a budget event.
- **Thesis vs cuts:** possible-now bias protects the *thesis*, not the cuts; cuts
  are liquid. The 1-shot is a rare board-split in the same game, not a different
  game — reading whether the board allows it is the router's NOW call.
- **A theory is a conjectured interior landmark** — earned by connection, discarded
  by isolation. Generalizes "no start and end" past two ends (five-X pirate map =
  multi-source bidirectional).
- **Lessons are milestone-shaped** — the durable memory unit is the landmark, not
  the step.

Closing question: the **fourth wall** — the map is sound, thesis holds, every
landmark connects, and you've *lost the plot* (self-inflicted "now make it pink").
Is *coherence of the assembled path* its own signal, or does it collapse into
re-checking done-means at each milestone?

## Turn 5 — Jeremy

> I like your angle here and it's very valid. "yep, I did want a picture of a rabbit,
> but bugs bunny isn't what I had in mind... at all" might be a flavor of this sort
> of result if I'm hearing you right. In addition to that, my mind was jumping up and
> down somewhere shortly after your second paragraph; we don't have a start and end
> that traverses -- we have N starts that are all trying to connect and they're shaped
> like milestones/landmarks. And overlayed on top of _that_ is a fog-of-war style
> discovery process (and the tri-state version of that probably holds best, and is
> equally as annoying -- undiscovered, visited with last known state, and directly
> shown state). Our cuts make assumptions on both the likely locations of milestones
> yet to be discovered and the data pointing towards them; be that shoreline, old
> recon data, training data, or freshly cached "seen" data. in that sense, it's all
> connected, just waiting to be discovered, and we've talked about that framing
> earlier today even; the knowledge is there we just need to observe it from the right
> angle... and in a compositional context, from a series of angles that create our
> path to the goal. In that sense, we also know when we hit true dead ends (no
> milestone connections, all avenues exhausted) and when we have alternate routes to
> try. Oof and one alternate route might be "learn how to dig under a wall" or "build
> a hot air balloon to fly over the chasm" type things... just depends on how
> persistent we want to be (and those sorts of edges are where I might say the user
> should be escalated to... not if we should keep trying other avenues, but if we
> level up our tech tree a bunch, so we have capabilities to overcome soft dead ends
> that don't immediately have a path, but are within sight of another milestone worth
> connecting to. Yeah, I think we're on to something here.

## Turn 5 — Claude

Bugs Bunny confirmed the fourth wall — all-green locally, wrong story globally;
structurally invisible to done-means because done-means lives *on the pins*, not
the *string*, and gets *more* likely the better local verification is.

- **Kill "start and end" outright** — N landmarks, all the same type, all trying to
  connect. "Path to the goal" = whichever connected subgraph contains the goal pin.
- **Tri-state fog** = maro's memory-staleness problem in a costume. Grey middle
  ("last-known-state") is a cached observation with a decay — where learned lessons
  live. Taste includes the re-scout decision.
- **Cuts are the prior over the fog** — shoreline, old recon, training priors, fresh
  cache are fog-reading heuristics.
- **The sharpest line: not every edge is discovered — some are built.** "It's all
  connected, waiting to be discovered" holds for the *information* layer but stops at
  the **capability frontier**. A tech-tree edge (dig under the wall / build the
  balloon) changes *what you're capable of*.
- **Escalate at the observe→construct crossing.** Revealing/walking with tools you
  *have* is autonomous; *acquiring a capability* to cross a soft dead end is the
  level-up = the human's call. Possible-now bias is the gate *before* it (exhaust
  current tech-tree compositions first). "Within sight of a worthwhile landmark"
  guards the rabbit-hole and writes the escalation payload.
- **Capability edges are the only edges that persist off the map** — the tech tree
  IS the skill library / evolver; a level-up amortizes across the goal-family.

Closing question: is the escalation altitude "should maro *become the kind of agent
that can do this class of thing*" (goal-family ROI), or does that overload a moment
that should sometimes just be "yes/no, cross this one chasm"?

## Turn 6 — Jeremy

*(shared a screenshot: a Claude Code session "Cooking… 2h 19m 42s · ↓ 346.9k
tokens" — fable mid-run)*

> I think we're at the point of needing to capture this conversation to the repo as
> an addendum doc to today's work; the maro work is proceeding and a little scary to
> watch (heh) [image], but it's going to leave off where we started the conversation;
> let's hold on making hard decisions, but definitely document the conversation here
> along with a summary of recommendations for more planning the system with these
> things in mind. Maybe we call this design work chunk 6 -- the start of the next
> spiral upwards. (I hope we're not building the tower of babel here...!)

## Turn 6 — Claude

Ran pre-commit safety checks (public-repo identity = noreply, not the work-email
gotcha; fast-forwarded local main past fable's chunk-5a), wrote
`docs/COMPOUND_THINKING_DESIGN.md` (exploratory, no decisions ratified; framed as
chunk 6), and landed it on `main` at `c6d6537` — rebased cleanly over fable's
chunk-5b push that arrived mid-commit. Deliberately no GOAL_BRAIN Decisions lines,
no decision-journal entries, no MILESTONES/BACKLOG/INDEX edits (holding on hard
decisions). Tower-of-babel guardrail put on record: the tell would be
recommendations that build capability the maps don't demand — so §9 keeps them as
questions tethered to existing machinery. Answer-then-build, not build-then-justify.

## Turn 7 — Jeremy

> Good. I'll look that doc over. Please capture a full transcript of the conversation
> as well -- a couple of times now we've revisited such things and gleaned new edges
> and information from them when new context or perspectives come to light. My gut
> says this will be one of those. In addition, I refreshed the readme and noticed the
> virgil quote about Maro -- literally all this was staring us in the face and we
> unconsciously named it before saying it out loud. Go us. :)

## Turn 7 — Claude

Noted the Virgil resonance (Publius Vergilius **Maro** = Virgil; "guides the
traveler down through the dark and safely out the other side" = the fog-of-war maze
we'd just described), wrote this transcript, and cross-linked it from the design
doc. → *this file.*

---

## Threads left open for a future revisit

The three we explicitly did not resolve (carried into
`COMPOUND_THINKING_DESIGN.md` §10):

1. **Escalation altitude** — per-chasm yes/no vs goal-family "should maro become
   the kind of agent that can do this class of thing?"
2. **Coherence vs done-means** — is lost-the-plot its own tracked signal or
   emergent from milestone checks?
3. **Taste/possibility calibration** — how do `surprise` + verified outcomes
   accumulate into calibration that changes the next meander? (Jeremy's deferred
   edge: consequence-coupled reps; bottleneck = verified-outcome density.)

And the meta-note Jeremy flagged: **come back to this raw log when new context
lands** — the Virgil catch is proof that a re-read from a fresh angle surfaces
edges the live conversation walked right past.
