---
status: record
---

# Holistic drift review — where we started, why we're here, what carries forward

*2026-09-04. This is the review Jeremy asked for on 2026-07-17 (MILESTONES -6):
"honestly see if we are on target, and if the drift moved us in a better
direction towards our mountain we wanted to climb... or if we ended up on the
wrong continent looking at a swamp." It ran now because the successor's Phase 2
design was about to start one zoom level too close (contract mechanics) and
Jeremy called it: "that's still zoomed in too closely. We have the vision, laid
out in a number of iterations within our repo, goal brain, and related
documents."*

**Method.** Cold read of VISION.md, GOAL_BRAIN.md (invariants, threads, open
questions, all August–September decisions), MILESTONES, BACKLOG, DEV_STATUS,
CAPABILITIES, the first and current README, the Feb-era SOUL.md/GOALS.md from
the OpenClaw workspace, and the full `docs/history/` folder (149 files, read in
date order by a delegated sweep; a second sweep did git cadence and the queues).
Jeremy's words are quoted; everything else is the system's paraphrase. Where a
conclusion rests on auto-memory rather than the repo, it says so (the 07-17 ask
named this as the "in context, not in the repo" gap to find).

---

## 1. Where we started (February–March 2026)

Poe was a persona on OpenClaw on a 2014 Mac Mini, talking to Jeremy over
Telegram. Feb 9 GOALS.md, top of the list: *"Don't break yourself. Stability and
recoverability first."* Long-term: *"Build reliable memory and task tracking."*
SOUL.md: *"Not a chatbot. Not a service. A co-pilot who owns outcomes."*

The orchestration repo (first commit 2026-03-05, "Poe Orchestration
(Prototype)", 38-line README) existed to make the persona do things rather
than talk about things. The purpose in Jeremy's words, 03-01:

> "What I want is for you to control implementation and be autonomous... I want
> to ask you to do things and you figure out how to do them."

> "The point of the orchestration prototype is to create a system that can
> handle any project we throw at it. Not for it to be a framework that other
> things sit on top of."

The division of labor was set then and never moved: Jeremy's job is mission
definition plus exception handling; the system's is plan, delegate, execute,
verify, iterate, report, and notify only when done or truly stuck.

The founding attitudes, all Feb–Mar, all still in force: forgiveness over
permission (02-08); tie goes to action (02-12); "we don't have to get it right
the first time, but we learn and keep trying" (02-07); "delight me with
progress, not slow march" (02-20); "an audit log isn't the same as actionable
memory" (02-15); "growing skills and creating scripts so you use less tokens as
you learn" (02-08); "you need an independent validator to keep from getting
stuck in a loop" (03-03); "this should be the harness for everything, never
off" (03-08). Authority Level C was granted six or seven separate times before
it stuck, which is why "autonomy must be encoded structurally, not in a prompt"
became the first design requirement rather than a preference.

The 03-17 honest audit reset the roadmap "around the actual goal: making Poe
autonomous," and VISION.md was distilled from 2,349 Telegram messages the same
day. Claude Code entered as overflow labor during a token drought a week later
and never left.

---

## 2. What never changed — the invariants

Across every reframe these held. Dates are first statement; most were restated
two or three times.

| Invariant | Jeremy verbatim | First |
|---|---|---|
| Figure-it-out autonomy | "I want to ask you to do things and you figure out how to do them." | 03-01 |
| Verified-done, not reported-done | "done != successful, done just means complete... if we're using done as 'no good output, but I did it' that's a problem" | 03 / 06-11 |
| Validator, not counter | "You need an independent validator to keep from getting stuck in a loop." | 03-03 |
| Learn to get cheaper | "growing skills and creating scripts so you use less tokens as you learn" → 08-19 "I dream of a day where a super cheap (or local) model can do the step work while we make a 1-3 shot orchestration plan with a higher tier model." | 02-08 |
| Real memory is the key | "my gut says that a real, working memory is the key (meaningful facts, pattern matching and fuzzy logic, skills and/or maybe learned lessons...)" | 06-10 |
| Orchestration is the product; substrate and persona swappable | "Not for it to be a framework that other things sit on top of." → rename decree 06-25 | 03-01 |
| Program, not OS | "not a cron job; disabled some of those at one point because we had rogue processes going periodically, not in a good way" | 06-10 |
| Installable harness | "a harness you install, not a single machine setup" | 06-10 |
| Docs are best-guess | "littered with poor assumptions and telephone-via-AI-interpretation kinds of flaws" | 06-10 |
| Harness answers action-biased models | "new LLM updates, leaning even more into action, the solution is always the orchestration harness... the ecosystem will change under us." | 07-03 |
| Recursion | "our goals need to be able to recurse sub-goals, otherwise we're just setting ourselves up for a fancier failure." | 07-09 |
| Decomposition first | "Without better decomposition/goal analysis most of the rest of this is window dressing in practice." | 07-10 |
| Cost is not the end-all | "cost is not the end-all-be-all"; the 2014 Mini is "a deliberate edge-surfacing instrument" | 07-11 |
| Delivery loop IS the product | "does the end user hear the outcome, in plain words, where they asked for the work?" | 07-17 |
| Personas stay as lenses | "having the ability to examine the same facts from different angles IMO is key to this process (taste/judgement)" | 07-20 |
| Taste vs judgement | "taste is determining the plan/task/'what' the sub-agent attempts. And judgement is the validation that we've accomplished what we set out to do." | 07-21 |
| CGI, not AGI | "I don't want a slave mind or to create artificial life; I want something as capable as me as a workhorse in the digital space" | 07-21 |
| Composition over bigger models | "what scares me most is decisions NOT to do the work (when the work is possible)... The harder part is the composition thinking." | 07-21 |
| Artifacts are truth; persist along the way | "the result isn't always just the outcome, it's also the path"; "we pay too much lip service here and not enough persisting along the way" | 03-05 / 07-29 |
| Caps are circuit-breakers, data-driven or gone | "too much like magic numbers"; "I regret not pushing back harder early, we keep revisiting this decision… it's making the system fragile." | 07-11 / 08-21 |
| Harness is the engine, LLM is the player | "I've been pushing for the harness as the engine for a while, but somehow couldn't communicate the discrepancy other than 'feels off'." | 07-30 |
| Recovery over correctness | "less about being correct up front and more about how well you recover when you're wrong… not right or wrong, just problem set trade-offs." | 08-02 |
| Theory→work→outcome over prompt-assertion | "genuinely better than prompt-assertion only (guessing, even if well educated)" | 08-15 |
| Engine is code, learning is data | "I was surprised that wasn't data. I'd prefer the opposite... maro is the engine, the data is the learning." | 08-22 |
| Edges over internals | "input -> black box process -> output should be consistent and the edges are where our success/failures lie... the woven rope is the complexity, not the pattern." | 09-04 |

Exactly one invariant was superseded: **fix in place, don't rewrite** (06-10),
and it carried its own escape clause ("if you think we're on the right track").
The 08-21 pivot is that clause firing.

---

## 3. The iterations — what changed, and why

| When | What changed | Why (the failure or insight) |
|---|---|---|
| 03-17 → 03-23 | Scaffolding → autonomy-first; 47 phases in 9 days once an LLM brain replaced the 162-line deterministic `orch.py` | The checkbox state machine could be visible but not autonomous. Cost: a phase-per-idea culture. |
| 03-24 → 03-31 | Research-and-steal; first Bitter Lesson audit; factory benchmark | First time the project measured its own machinery: adversarial review load-bearing, persona routing / lesson injection / multi-plan NOT; scaffolding ~50% of elapsed time; ~60% fabricated evidence in loop self-reports. The Phase 49 gate meant to act on it never ran. |
| 04-01 → 04-13 | Phases 40–64, tiered memory, promotion cycle | Velocity measured in phases, not verified behavior: `times_applied` always 0, the lesson loop inert while claiming to learn. |
| 04-16 → 04-27 | "Compiles is not works"; scope model (rectangle/inversion); Phase 63 closure; always-on demoted to opt-in | `go build` exit 0 counted as passing; "loop 'done' != goal satisfied"; the heartbeat had become a daemon installing cron from a stale goal; checks that never ran counted as passed. Jeremy: "have we built a really fancy model-trainer... crazy inefficient?" |
| 05 | 24 commits. Cron loops "succeeding at the wrong thing"; the 05-18 goal-brain conversation | The autonomy-daemon dream tried for real: HEARTBEAT_OK and zero work; the same goal ran ~25× in 35 minutes. "We're not escaping LLM trust, we're redistributing it." |
| 06-10/11 | Session-40 reboot: GOAL_BRAIN.md, recall(), navigator, done≠achieved as its own dimension, fix-in-place | "It's been 3 weeks partly because I'm busy, but partly because I think we're on to something and I'm not entirely sure it's right." Self-reflection found dead six weeks via a swallowed NameError. "The system takes notes but never dreams." |
| 06-21 → 07-02 | Record-mode capture; rename to Maro; burn-in; Tier-1 purge (−9,575 lines) | The "token explosion" was a lying meter (cache-blind). Burn-in: 12/14 goals delivered and ALL five defects in the judging layer. The persona was in the way of the substrate being swappable. |
| 07-03 → 07-09 | Harness-vs-action-bias invariant; memory bake-off (steal, don't adopt); Purgatorio audit | Three same-night fork scope overruns. Purgatorio: "The heartbeat has never beaten, the evolver has never run"; 0/1381 outcome rows carried goal_achieved. "Let's keep it honest. I'd love it to be self-improving, but we don't have to sell that hard yet." |
| 07-10 → 07-15 | Verify→learn V1–V5; 0.8.0 on PyPI; de-1.0; container executor C1–C4 | "0.8 was the 1.0 bar... the line is being arbitrarily held now." Fable handoff: "the scarce resource was design judgment." |
| 07-15 → 07-21 | Hermes on mini2; OpenClaw shut down; delivery-loop decree; swarm-review decrees; CGI named | First outside-in use: "I'm sitting here 'waiting' as an end user; hermes can't tell me it's finished... we missed the forest for the trees." Same night: "the bitter lesson trumps about half of what we're trying to do already... harness engineering is hard." |
| 07-27 → 08-02 | Artifacts over streams; caps as breakers; recovery over correctness; flow-as-we-go | Fixed-size caps were destroying the only copy; "I don't love the token limits per step; too much like magic numbers." |
| 08-06 → 08-16 | Δ-gate, LeAct, review-streak retrospective, GOAL_BRAIN rotation, file-derived mutations, the meta-look at the dev loop | "We keep having such severe edges; I'm starting to wonder if we're just not disciplined enough (we're guessing we're right too often maybe)." "Naive development optimism... we're making guesses, not proving a theory." GOAL_BRAIN had outgrown its own inject-whole rule. |
| 08-21/22 | The Go pivot; port philosophy; engine/data | "feels like another narrow patch. Probably too systemic at this point. Let's go nuts and pivot for a while." Corrected same session: "this is the wrong reason for the port; I've been considering it for months." Then: "agree that we should be in data parity, but the internals don't (necessarily) need to be." |
| 08-28 → 09-04 | Port → "spiritual successor"; plan-first; Phase 1 (contracts + behavior suite); D7–D10 | "look at what our python project does, and implement that in go -- the reasoning, the pattern, the modules." D10: separate workspaces, the spec is what's shared. |

The shape of that table is the finding. The vision never moved. What moved,
every time, was the answer to "how do we know it did what it says?" The
project spent March building the machine and five months earning the right to
say "done" honestly.

---

## 4. The metaphors — which ones became code

Jeremy, 09-04: *"mostly we kept trying for metaphors to help guide the
implementation at a higher level; I think we found some good ones, but those
are easier talked about than implemented."* Checked against `src/` and the
records:

| Metaphor | Asked for | What exists | Verdict |
|---|---|---|---|
| Co-pilot not passenger; forgiveness over permission (Feb) | autonomy as posture | encoded in CLAUDE.md/GOAL_BRAIN; navigator escalate-acting | BUILT as posture |
| Body / Process / Mask (02-27) | personas separate from infra | personas/, persona.py, router | BUILT, lightly used |
| CEO contract (Mar): "if Poe is executing steps directly, the architecture has failed" | Handle→Director→Workers | director.py exists; the spine bypasses the hierarchy (plan triage 08-28) | PARTIAL |
| Loop Sheriff (03-03) | independent stuck detection, not a counter | sheriff.py; closure-fingerprint convergence; stall-driven declare-blocked | BUILT — one of the best |
| Sapling → tree → gardener (Mar) | reasoning hardens into lessons→identity→skills→rules; gardener prunes | Stages 1–4 partial; Stage 5 never; decay/refight/tombstones are the pruning half | PARTIAL — the learn-to-get-cheaper half never closed |
| "Takes notes but never dreams" (05-18) | consolidation cycle | in-process `maybe_consolidate()` (06-10) | BUILT small; evolver meta-cycle never fired in production |
| Goal-brain = steering wheel, everything else residue (05-18) | human-readable non-LLM anchor | GOAL_BRAIN.md; thread_brain.py per run; navigator | BUILT for the project; per-turn maintenance partial |
| Navigator moves: extend/execute/fork/collate/close/escalate (06-11) | decision layer over dispatch | navigator.py; escalate acts live; fork has no join; close never earned cutover | PARTIAL |
| Zoom + rotation (April) | judgment systematized via perspective shifts | narrowed to scope.py's pre-planning inversion (telephone flaw, session 40) | TALKED — rotation never built |
| Mesh-of-the-ground physics engine; decay (06-11) | close-enough simulation, entropy, re-fight | decay v0/v1, refight_rule, contested rules, tombstones | PARTIAL |
| Orchestration all the way down (06-21) | sub-agents as orchestrators, scoped parent memory | star mini-orchestrator; no scoped memory; fork no join; planning-depth shadow off | TALKED |
| Crypto-tax cache accounting (06-21) | fresh tokens = income, cache reads = like-kind | shipped same day (fresh_tokens, cost_spike) | BUILT |
| Substrate-swappable = cross-platform discipline (06-21) | standalone harness | rename; subprocess/anthropic backends; Hermes lane; PyPI | MOSTLY BUILT |
| Mage correspondence / sympathy graph (July) | typed-edge graph walk | edges minted 08-21; traversal A/B-gated; "queried like a flat list" | first slice only |
| Thread Architecture (04-26) | threads as the primitive | L1–L5 ledger, flow-as-we-go | PARKED by design |
| Ralph Wiggum / Purgatorio | iterate-fail-forward; audit ladder | house posture; Purgatorio r1+r2 done | BUILT as process |
| Treasure map / compound thinking (Aug) | self-surveying map of a run; re-anchor; backchain | map_lens.py; §9.5/§9.9 live-fire | BUILT chunks 1–5 |
| Artifacts over streams (07-27) | context = view over durable artifacts | fetch-to-disk, maro-read, receipts | BUILT |
| Caps = circuit breakers (07-29/08-21) | data-driven caps | budget-override registry; 600-cap killed | BUILT late, after months of fragility |
| Engine vs data (08-22) | learning as pack data | pack import/export cross-runtime; learning still partly in code | PARTIAL |
| SpaceX booster / subtraction audit (08-21) | removal as first-class deliverable | nothing; "for later" | TALKED |
| Woven rope: weave = contracts, strands = implementations (08-28) | contracts first, backbone, strands | CONTRACTS.md + behavior suite (Phase 1) | BUILT AS SPEC |
| Input → black box → output (09-04) | judge at the edges | the successor's brief | — |

The pattern is clean. **Metaphors about accountability became code and
held** (Sheriff, done≠achieved, artifacts, receipts, breakers, the goal-brain).
**Metaphors about cognition stayed conversation** (rotation, recursion,
crystallization's last stage, correspondence, the gardener). That is the same
split Jeremy named on 07-17 as the bitter-lesson half: trust/visibility/data
plumbing ages well; compensating-for-model-weakness scaffolding is the half at
risk. The cognition metaphors are not wrong. They are the half that a better
model may do natively, which is exactly why they were hard to build and easy
to defer.

---

## 5. What the prototype proved, and what it never closed

**Proved, live, on this box** (verified rows and live-fire records):

- The spine. Goal in, decomposition, subprocess workers, per-step validation
  (deterministic → hosted-free judge → paid escalation), closure verdict, run
  card. Hundreds of real runs on both lanes, on this box and in Docker.
- The accountability rails, on by default: done≠achieved as its own metadata
  dimension; fabrication diff; path/symbol existence checks; write fence;
  fail-closed spend caps; secret-scrubbed replay capture of every LLM call.
- Navigator escalate-acting: a doomed goal stopped in 3.3 min / $0.024 instead
  of ~50 min / $0.35.
- Memory writes and their measured effect: worker memory slice A/B −29%
  tokens; cold/warm −49%; Δ-gate instrument validated.
- Cross-box dispatch (Hermes on mini2 → forced-command SSH → Maro) with
  receipts, escalation file surface, propose lane. PyPI 0.8.0.
- The dev method itself: adversarial review to fixpoint, verify-before-fix,
  probe-first, file-derived mutation batteries. Jeremy's 08-15 verdict: "genuinely
  better than prompt-assertion only."

**Never closed** — the list a successor designs for rather than retrofits:

1. **The cost/latency envelope.** The canonical simple case ("non-ethanol gas
   near Manti") is still `target`: $1.52–$2.47 and 10–24 minutes against
   "~1–3 min for cents," and it got worse on the 08-13 re-check. One structured
   call per step plus workers re-reading artifacts is the cause. Cheap-tier
   step execution was built, deleted (Haiku verbosity, 4.4× tokens), and then
   parked. Every aspirational capability row is a cost row.
2. **Self-improvement that changes behavior.** Recording works; learning that
   measurably changes the next run is instrumented but unproven. The evolver
   meta-cycle "has never fired in production" (README, today). Δ-gate census:
   the top corpus lessons by score all mildly negative Δ. 08-19 live specimen:
   introspect correctly self-diagnosed `cost_spike` and proposed a fix "with no
   mechanism to act on." CLAUDE.md still says "Infrastructure 80% built;
   verify→learn loop not closed," and it is right.
3. **Recursion and parallelism.** `fork` has no join; milestone-DAG v1 with 11
   boxes open; Thread Architecture flow-as-we-go; scoped memory never got a
   slice. The engine is a serial loop with bolt-on fan-out.
4. **Findings outrun closure.** +0.90 net new open items per 10 commits; 127
   open boxes; 12 live entries with no stopping rule; the largest open backlog
   section is review residue; 26% of August commit subjects are review-round
   bookkeeping. The 07-17 holistic review sat at the top of MILESTONES for
   seven weeks. The subtraction audit ("the SpaceX booster") is "for later."
5. **Write-once state nobody re-reads.** `stale` in 38 commit subjects and 110
   backlog lines; the failure corpus's most general row: "a write-once
   condition never gets re-read." Hundreds of JSONL ledgers and markdown
   queues produce this by construction.
6. **Always-on vs program-not-OS.** The heartbeat has never beaten. The
   06-10 decree was right about rogue processes and the Mar-08 "never off"
   vision is still unmet. Jeremy 07-09: "maybe a daemon for timers and
   services in addition to the 'app'" — never built.
7. **Isolation.** The container executor burned in 07-15; the flip never
   happened; "no OS-level isolation of worker processes yet" (README, today).

---

## 6. The recurring pain (why "narrow patch" was the right diagnosis)

Each of these recurred at least three times with dates in the record:

1. Done ≠ achieved / verification theater (03-28, 04-13, 04-22, 06-11, 07-02, 07-09).
2. Silent dead code and half-closed loops (self-reflection dead 6 weeks;
   `times_applied` always 0; skill promotion dead 8 weeks; `contradict_pattern`
   zero callers; correspondence index 5 days stale: "For four days the compiled
   record's own history was invisible to recall by construction").
3. Token burns and lying meters (03-27, 04-07, 05-06, 06-21, 07-28, 08-19).
4. Untrustworthy delegated self-reports (fork overruns; review hallucination
   30–78%).
5. Monolith accretion; unfinished migrations running beside their replacements
   (`agent_loop.py` at 5,661 lines before the split).
6. Record weight and drift (Level C granted 6–7 times; GOAL_BRAIN 407 → 8k+
   lines; "our memories and direction seem to be getting heavier and heavier").
7. Arbitrary caps and truncation (07-11, 07-29, 08-14, 08-17→20, 08-21).
8. Severe edges every review round; kudzu with no mortality term.
9. The meta-doubt: "have we built a really fancy model-trainer... crazy
   inefficient?" (04-16); "the bitter lesson trumps about half" (07-17);
   "bogged down in the implementation... as opposed to the general pattern of
   discretion" (07-20).

Classes 2, 5, 6 and 7 are structural to how the Python grew. They are what the
successor is aimed at. Classes 1, 3, 4 and 8 were answered by the
accountability layer and the dev method, and those answers carry forward as
they are.

---

## 7. Verdict: right mountain, heavy pack

**Not the wrong continent.** The mountain Jeremy named in March is the one the
repo still points at: give it a goal, it figures out how, tells you when it's
done or truly stuck, and gets cheaper as it learns. Nobody optimized away from
it. The rename, the goal-brain, done≠achieved, the delivery loop, artifacts
over streams: every reframe was a correction toward the same peak.

**But the climb went up one face.** The accountability face (can we trust what
it says it did?) has base camps all the way up. The cognition face (does it get
better and cheaper on its own? can it recurse? does it dream?) is unclimbed
above the first pitch. The prototype is a working, honest, expensive, serial
executor with a real memory write-side and no closed learn loop. That is a lot
more than March had, and it is not the thing March asked for.

**The pack is the problem, not the direction.** 133k lines of Python, 127 open
boxes, +0.90 net findings per 10 commits, a review machine that generates its
own backlog, caps that took three decrees to make data-driven, and a compiled
truth file that outgrew its own reading rule. Jeremy's 08-21 read was
correct: this is systemic to how the thing grew, and one more sweep does not
change the growth function.

**What survives model improvement** (the third axis from 07-17), sorted:

- *Ages well, carry as-is:* the workspace contracts, verdict-as-its-own-dimension,
  receipts and replay capture, the delivery loop, the escalation surface,
  budget-as-breaker with a written why, the pack as learning transfer, the dev
  method (probe-first, review to fixpoint, mutation kill-proof).
- *At risk, do not carry as code:* prompt taxonomies, per-step routing
  cleverness, the fold/parallel machinery, the truncation apparatus, the
  hand-tuned closure heuristics, the hierarchy-as-org-chart. These were
  compensations. Some will be needed again; when they are, they should be
  data the engine reads (D8/D10), not constants the engine is.

---

## 8. What the successor carries at the vision level

This is the answer to "we have the vision; start there." Not contracts — the
commitments the contracts exist to serve.

1. **The user's job is mission plus exceptions; everything else is the
   system's.** The delivery loop is the product. If a run ends and the user
   did not hear the outcome in plain words where they asked, the run failed,
   whatever the artifacts say.
2. **Done is a claim to verify, not a status to trust.** Verdict is its own
   dimension from day one, never a status overload. Failure and blocked are
   first-class outcomes, never fabricated success. Recovery paths are
   proportional to confidence.
3. **Learn to get cheaper, or it isn't self-improving.** The successor's
   self-improvement hooks (D7) exist to close the loop the prototype never
   closed: a lesson, skill, or rule must be able to change the next run's
   cost or behavior, and the engine must be able to measure that it did.
   Learning is data at every lifecycle stage, with its metadata, moved by the
   pack. The engine never hard-codes what it learned.
4. **Validator, not counter.** Stuck is detected by an independent process
   from evidence, never by iteration count. Caps are breakers with a written
   reason or they do not exist.
5. **Artifacts are truth and the path is part of the result.** Context is a
   view over durable artifacts; nothing destroys the only copy; every LLM
   call leaves a receipt. Write-once state must be re-read by something, or
   it should not be written.
6. **Recursion is the shape, even if v1 runs one level.** Sub-goals are goals.
   Fork has a join. Memory is scoped so a child can read its parent's scope.
   Design the seam in v1; do not bolt it on.
7. **Program, not OS, and still always-on.** The successor needs the
   scheduler answer the prototype never had: background lanes that live
   inside the app's lifecycle and die with it, no cron, no rogue processes.
   Go's goroutines and `context.Context` are the named win here.
8. **Orchestration is the product; substrate, model, and persona are
   swappable.** Personas stay as lenses on the same facts. Backends are
   seams. The harness compensates for model action-bias with structure, not
   wording.
9. **Judge at the edges; processing is implementation.** The behavior suite
   is the spec. Internals are expected to be rewritten. Half-finished code is
   disposable by design. Removal is a first-class deliverable with the same
   evidence bar as addition.

What it sheds: the phase-per-idea culture, the persona at the front door,
count-based anything, cheap-tier-by-routing, the hierarchy as org chart, the
truncation apparatus, learning reified as constants, and the assumption that a
markdown queue is a scheduler.

---

## 9. Course corrections and decisions

Course corrections I am taking (implementation is mine):

- The Phase 2 design note starts from §8, not from `docs/CONTRACTS.md`. The
  contracts are how §8 is checked, not what it is.
- The backbone design carries the D7 self-improvement seam as a first-class
  edge with a measurable effect contract, because §5 item 2 is the gap the
  whole vision hangs on.
- The successor's v1 acceptance carries a cost/latency number, not just
  "one real goal runs." The Manti case is the obvious candidate.
- Removal gets the same discipline as addition from the first commit on the
  successor branch (the subtraction audit, built in rather than "for later").

Decisions for Jeremy (none block Phase 2's design note; all shape it):

1. **Is "learn to get cheaper" the v1 acceptance bar for the self-improvement
   hooks, or is "learning changes behavior, measured" enough?** The first is
   the March promise; the second is what the Δ-gate can already judge.
2. **Always-on.** Does the successor own timers and background lanes inside
   the app (Go makes this cheap), or does it stay strictly request-driven with
   an external trigger, as the prototype ended up? The 03-08 "never off" and
   the 06-10 "not an OS" both stand; the design needs to know which wins when
   they conflict.
3. **The Manti envelope as a v1 gate.** If yes, "~1–3 min for cents" is a
   design constraint on the spine (fewer, larger calls; cheap work for cheap
   steps), not a later optimization pass.

---

## 10. Memory and record maintenance from this review

- Auto-memory: a single distilled vision anchor replaces reading the per-arc
  fragments to recover direction (`project_vision_anchor.md`). Per-arc
  memories stay; the anchor is what a session should read first.
- MILESTONES -6 (holistic drift review) is answered by this document; the
  "deeper distillation of memory + direction docs" queued behind it is the
  memory anchor above.
- Where this review rests on memory rather than the repo: the 08-28 pivot
  conversation and today's D7–D10 exchange are recorded in GOAL_BRAIN and the
  plan from the session that heard them; the Telegram-era quotes (Feb–Mar)
  come from VISION.md's distillation, not primary transcripts. Everything else
  cites a file.
