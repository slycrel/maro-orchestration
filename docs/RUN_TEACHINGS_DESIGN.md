---
status: dormant-design
---

# Run teachings — learning at the altitudes the funnel misses

**Design brief, 2026-08-01.** Opened on Jeremy's direction after UU-2
(BACKLOG LT arc): *"our learning is sort of a 1-trick pony, it's only
trying to learn what we told it to try learning, rather than meaningful
lessons that shake out for a run… might be something there that starts at
the goal level — 'what the run overall teaches', that then can feed into
the general learning system to see if it applies to the greater
orchestration (or possibly just a certain class of work)."*

Judgment calls tagged `DECISION (provisional)` per house convention.
Nothing here is built; the specimen and root-cause sections are verified
against code and the live run.

---

## 1. The specimen

Run `4bf7f761-merry-magpie` (chlorination claim, 2026-08-01, **$5.91** —
originally written here as $24.88; see the cost retraction in BACKLOG under
LT-1) spent a large share of itself rediscovering that HathiTrust, Google
Books, Archive.org and CourtListener are infrastructure-blocked from this
box — each block honestly documented with HTTP codes in `source_trail.md`.

**Measured, not estimated** (2026-08-02, `scripts/run_readout.py` replaying
`src/terrain.py` over the run's real 11-step tool transcript): **7 hosts
hard-blocked, and 23 tool calls aimed at a host already recorded blocked in
an EARLIER step.** The first draft of this section said "~6 steps and dozens
of attempts" from reading the trail; the countable number is 23 avoidable
retries. Same replay across the arc: phrase-varied **24**, #5 Systemantics
**1**, warm chlorination **0** — so terrain's value is large on
archive-heavy research goals and near-zero on goals that find their sources
first try. Any prediction about what §5b saves is falsifiable against that
counter.

The lesson funnel then minted 3 lessons. All three are task-strategy
("plans are step budgets, not fixed checklists"; "inconclusive verdicts are
acceptable when the goal says so"; "pre-digital-record claims need
archival-grade sources"). **None captured the blocked-archives fact** — the
single highest-value thing the run learned, and the one a repeat run pays
full price to relearn.

## 2. Root cause is structural, three ways

Verified against `src/memory.py` / `src/knowledge_web.py` at the time of
writing:

1. **Input starvation.** `extract_lessons_via_llm` receives
   `goal + status + result_summary[:500]`. The trail — where every
   operational fact lives — is never shown to the extractor. It cannot
   distill what it cannot see. (Per-step extraction,
   `extract_step_lessons`, sees individual step results but is gated to
   runs failing the learnability gate, and inherits the same taxonomy.)
2. **Taxonomy blindness.** `_LESSON_TYPES = {execution, planning,
   recovery, verification, cost}` — every type is a *how-to-work* type.
   There is no type whose answer is a *fact about the world* rather than
   an adjustment to behavior. The extractor is typed into strategy space.
3. **No scope concept.** A lesson is task_type-scoped, full stop. "This
   box cannot reach HathiTrust" is true for *every* task type on *this
   box* and false elsewhere; "plans are step budgets" is plausibly true
   everywhere. Nothing in the store can say which is which, so nothing
   downstream can inject one at box scope and the other globally.

One consequence worth stating plainly: **this is not a prompt-tuning bug.**
Feeding the same prompt a better summary would still emit strategy-typed,
unscoped lessons. The fix touches input, taxonomy, and scope together or
it doesn't work.

## 3. Taxonomy — the altitudes of what a run teaches

Jeremy's lighthouse metaphor is the organizing idea: *"we can recognize a
lighthouse when one comes up through the fog of war and have an idea of how
to approach it; be it a stone tower with a bonfire on top or a modern steel
monolith."* Recognizing the **kind** and carrying approach knowledge for
the kind — independent of the instance's particulars — is a different
altitude of learning than either "how I execute steps" or "this specific
URL is dead."

| Kind | What it is | Example from the specimen | Truth scope | Goes stale when |
|---|---|---|---|---|
| **task-strategy** *(exists today)* | how to work | "plans are step budgets" | task_type | contradicted by outcomes (existing machinery) |
| **terrain** *(new)* | a fact about the environment | "HathiTrust/Google Books/Archive.org are blocked from this box (HTTP 403/Cloudflare, N attempts, 2026-08-01)" | box / network / project | the world changes — an archive unblocks, a key is added |
| **landmark-class** *(new — the lighthouse)* | an obstacle/opportunity KIND + approach knowledge for the kind | "paywalled-archive: primary sources pre-~1925 cluster behind HathiTrust/GBooks; approach = CourtListener/state repositories first, name the gap honestly, escalate the access ask rather than retrying mirrors" | goal-class | the approach stops working for new instances of the class |
| **self** *(partially exists)* | a fact about the orchestration itself | "timeout-killed calls leave no record" (UU-1 — today these are found by human forensics) | orchestration | the code changes |

The tri-state fog (COMPOUND_THINKING §2a) already names where terrain
teachings live: **grey fog — cached observation with a decay.** This design
gives grey-fog intel a mint path, a store shape, and a re-scout rule; the
concept was already decreed.

## 4. Design

### 4a. The goal-level extraction pass ("what did this run teach?")

One additional LLM call at finalize, sibling to the existing deferred
extraction and gated identically (verdict-aware, killswitch, idempotent
stamp). Different in two ways:

- **Input is the trail, not the summary.** The pass reads the run's
  evidence artifacts — deliverables matching the trail shape
  (`source_trail.md`, verdict reports), step summaries, the diagnosis, and
  the per-step HTTP/failure codes already extracted by the fabrication/
  claim machinery. Budgeted and truncated, but *from the evidence layer*,
  never from a 500-char summary.
- **Output is typed teachings, not lessons:**

```json
{
  "teaching": "HathiTrust full-text search is blocked from this host",
  "kind": "terrain",
  "scope": "box",                    // box | project | goal-class:<class> | global
  "evidence": "6 attempts, HTTP 403 + Cloudflare challenge, steps 3-5",
  "verify_probe": "curl -s -o /dev/null -w '%{http_code}' https://babel.hathitrust.org/cgi/ls",
  "expires_hint": "re-probe on next use, not calendar decay"
}
```

`verify_probe` is the field that makes terrain teachings different in kind:
**a terrain fact is cheaply re-checkable.** DECISION — **RATIFIED
2026-08-02 as amended** (Jeremy, post-dig: "agree, let's do as you
proposed"): probes are stored, never executed at mint; they run under the
existing read-only probe guard (claim_probe's mechanical gate) at
*injection* time — both as the staleness re-check when a confirmed fact is
older than its class's freshness window, and as the confirmation gate for
provisional terrain (§4d). §14 consistency requirement, pinned: on probe
FAILURE the machinery only flips the teaching to grey and records the
contradiction — route-arounds, side-quests, and any bridge-building stay
planner-owned moves. Trust decays, data never does.

> **History:** Jeremy held this pair 2026-08-02 on a recollection that
> checks were decided to be *planned as steps* (bridge-building arc,
> proving-vs-gathering exception). The record dig verified his
> recollection exact (2026-07-27 §14 diagnosis ownership split, decision
> `7061e85e`; forensics:
> `docs/history/2026-08-02-probe-timing-record-dig.md`), named the §4d
> deadlock (provisional never injects + probes only at injection ⇒
> terrain could never confirm), and recommended probe-gated first
> injection. **Jeremy ratified the recommendation same day** — §4a as
> amended above, §4d rewritten below.

### 4b. Storage — extend, don't build

DECISION — **RATIFIED 2026-08-02** (Jeremy: "Yep, I like this… I think
there will be multiple kinds of learning just like there are multiple
kinds of steps, and the same mechanism should handle the lifecycle (and
likely there will be nuance between the flavors)"): teachings land in the
**existing tiered lesson store** with two additive fields (`kind`,
`scope`) and two new `lesson_type` values (`terrain`, `landmark_class`),
rather than a new store. Reasons: the provisional lifecycle, contradiction adjudication,
reinforcement receipts, and decay machinery all already exist there, and
the 2026-07-31 decay decree (competence-redundancy anchoring, "repeat
patterns should be examined... better-than-luck shapes") applies to
teachings verbatim. A new store would re-earn all of that.

Scope uses the slash-path idea the memory port already models
(`visible_at()`): `box/<hostname>`, `project/<slug>`,
`goal-class/<intent class>`, `global`. A teaching is injected only where
its scope is visible.

### 4c. Injection — terrain surfaces at CUTS time

The specimen says precisely where terrain belongs: **before planning.**
The cold run's cuts/plan would have looked different if "these 3 archives
are unreachable" had been on the table at `CUTS_DRAWN` time. DECISION
(provisional):

- **terrain** → injected into the cuts/planning context (bounded, top-K by
  recency×reinforcement, scope-filtered). This is the consumer that makes
  chunk 1 measurable.
- **landmark-class** → injected at recall when `intent.classify`'s class
  matches the teaching's `goal-class` scope — the moment the lighthouse
  shape is recognized in the fog.
- **self** → not injected into runs; routed to the dev surface
  (BACKLOG/reading queue candidates). A run shouldn't be reasoning about
  the harness's bugs mid-goal.

### 4c, expanded — worked examples (added 2026-08-02; Jeremy asked for more context)

The decision above compresses three separate calls. Concretely, on the
chlorination specimen:

1. **Terrain at cuts.** The cold run burned budget discovering
   "HathiTrust full-text search is blocked from this host" at steps 3–5.
   With that teaching on the table at `CUTS_DRAWN` time, the plan never
   routes through the blocked archive — the cut is drawn around it, or a
   route-around (mirror, cache) becomes a planned first-class step. The
   alternative surface (inject at step-execution time) saves nothing: the
   doomed steps are already in the plan by then.
2. **Landmark-class at recall.** A landmark teaching is shaped "goals of
   class X have hazard/shape Y" (e.g. purchase-ready research lives or
   dies on per-field verification). It's only meaningful once the goal's
   class is recognized, so it fires at recall when `intent.classify`
   matches — injecting it everywhere would be noise for every other class.
3. **Self-teachings out of runs.** "complete_step isn't wired, step
   counts are unreliable" is true and useful — to the *dev loop*, not to a
   run mid-goal. Injected into runs, it produces the M18/M19 flavor of
   self-referential hedging inside deliverables. So self-kind routes to
   BACKLOG/reading-queue candidates instead.

The genuinely decidable parts for Jeremy: **(a)** does terrain belong at
its own pre-planning surface (vs riding the normal recall slice with
everything else)? **(b)** is routing self-teachings entirely OUT of runs
right, or should a run at least see a caveat-grade "your step counter is
broken"? (a) is what chunk 1's acceptance test measures; (b) is taste.

DECIDED — **2026-08-02 (Jeremy, decree-class; GOAL_BRAIN entry same
day).** **(a)** Terrain gets its own surface: "a separate call in
addition to the existing planner (maybe parallel)… seems maybe cleaner
to have them separate to start." His tension, named for whoever builds
it: "the overall planner should be doing this already IMO, so this
might be a pre or post step for the planner, depending on the
approach." Separate-to-start keeps the seam visible; integration stays
open as a later move. **(b)** Not fully out — caveat-grade
self-teachings are OK: "injectable as a single win type seed for a
learning", and this should factor with import/export of maro data in
general, "as long as it has no solely time-based expiration… really we
want learning to be data driven in all the shapes." (No time-only
expiry is now a standing constraint on any teaching lifecycle.)
Folded-in NEW ASK, BACKLOG'd for design: the planner needs
**non-action item types** — two world-fact kinds, anecdotal/
accidentally-found and hypothesis-type findings (pattern recognition /
ideas). Plans are not only actions.

### 4d. Validation — the existing loop, plus the probe shortcut

Teachings mint **provisional** (excluded from injection until confirmed,
per the shipped provisional-lesson lifecycle) — no exceptions at mint.
DECISION — **RATIFIED 2026-08-02 (probe-gated first injection**, replacing
the drafted mint-time shortcut; Jeremy: "agree, let's do as you
proposed"): a provisional terrain teaching MAY be offered for injection
iff its `verify_probe` passes at that moment, under the read-only probe
guard. **The pass is the confirmation event** — the teaching enters
confirmed-with-evidence on first use, which is also the freshest possible
moment to check it. A failed probe flips it to grey with the failure
recorded (never silent, never a machinery-owned recovery — §14).
Everything else rides the normal receipts: `times_applied` at injection,
reinforcement on re-observation, contradiction flow on conflict.

## 5. Acceptance test — already scheduled

The warm arm of the chlorination pair is the natural measure, and its
prediction is already registered in BACKLOG: lessons carrying the
blocked-source fact ≈ $3–6; artifact reuse only ≈ $15–20; neither = $25.
Today's system predicts the middle tier *at best*, because the blocked-
archives fact was never minted. **After chunk 1, re-run the same goal
phrase-varied (to defeat artifact reuse) — the delta attributable to the
terrain teaching alone should pull a fresh instance of the goal-class
toward the $3–6 tier.** That is the whole design in one number.

## 5b. Within-run terrain memory (Jeremy 2026-08-02 — "a goal short term memory issue")

*"Should be a simple memory-related thing to remember that certain sites
are blocked rather than churning on learning that over and over; seems
like a goal short term memory issue would be a straightforward fix;
potentially promotes like other learning, and maybe that's already
there?"*

**Answer: the parts exist, unconnected — and this is a SEPARATE, cheaper
fix than §4, worth doing first.** The waste is not only cross-run
(§1's $15 relearn); it is *within* a single run. The cold chlorination
run re-attempted the same blocked archives across ~6 steps, and #5's
run wandered `STEP_TOO_BROAD ×4` at $32 — each step rediscovering the
same environmental facts because **nothing carries a fact from step N to
step N+1 except the step's own prose result.**

What already exists, and why none of it closes this:
- `knowledge_web.short_set/short_get/short_all` — a genuine
  session-scoped store, **but effectively dead code**: the only writer is
  `persona.py` (persona_name/persona_goal). It is an in-process dict, so
  it also would not survive the subprocess-worker boundary.
- `ContributionLedger` (§6 injection seam) — the *right shape*, already
  typed and provenance-stamped, drained per step. Current contributors:
  `budget`, `reorientation`, `prereq`, `hook`, `escalate_reply`,
  `blocked_retry`, `user_note`, `time`. **No terrain contributor.**
- `SCAVENGE_DETECTED` / the fabrication guard already parse worker tool
  transcripts for paths — so per-step observation machinery exists.

**Proposed slice 0 (smaller than chunk 1, no LLM call, no new store):**
a run-scoped terrain accumulator. When a step's tool transcript shows a
host returning a hard block (403/429/Cloudflare/DNS-fail) or a tool
reporting unavailable, record `{host, code, step}` on the LoopContext;
render the accumulated set as ONE `terrain` contribution into each
subsequent step's prompt ("Known blocked this run: hathitrust.org 403,
books.google.com quota — do not retry; state the gap instead").
Deterministic, cheap, and it rides the seam that already exists.

**Then it promotes exactly as Jeremy guessed:** at finalize, a terrain
fact observed ≥N times (or across ≥2 steps) is a candidate `terrain`
teaching per §3/§4 — the run-scoped accumulator becomes the *evidence
source* for the durable mint, which also gives §4a a deterministic
input instead of asking an LLM to notice blocks in prose. Short-term
memory feeds long-term memory; the two designs compose rather than
compete.

DECISION (provisional): slice 0 ships before chunk 1 — it is
deterministic, testable without live runs, and independently valuable
even if §4 never lands.

## 6. Build order (consumer-first, smallest real slice)

1. **Chunk 1 — terrain only:** extraction pass (kind=terrain only) +
   `kind`/`scope` fields + cuts-time injection + the mint-time probe
   shortcut. Acceptance: the phrase-varied warm run above.
2. **Chunk 2 — landmark-class:** the lighthouse lane; needs a small
   goal-class vocabulary (start with the classes intent already emits) and
   the recall-time injection. Acceptance: a *different* goal in the
   paywalled-archive class (e.g. LT-1 #5, obscure book) benefits from the
   class teaching minted by the chlorination pair.
3. **Chunk 3 — self:** route self-teachings to the dev surface. Cheap,
   but only worth wiring once 1–2 prove the extraction pass produces
   signal.

## 7. Non-goals / rejected

- **No new store, no daemon, no scheduler** — everything rides finalize
  and existing cadences (standing invariants).
- **No auto-executing probes at mint beyond the one read-only check**, and
  none at all for probes failing the mechanical read-only guard.
- **Not a replacement for task-strategy lessons** — an additional altitude,
  same funnel discipline.
- **Not the UU-1 fix** — kill-path record loss is a paper-trail bug
  (BACKLOG LT arc) regardless of what learning does with the data.
- **Deliberately unbuilt until decided:** cross-box terrain sharing (a
  terrain fact is exactly the thing pack.py's portable-learning lane wants
  to ship, and exactly the thing that's FALSE on the other box — scope
  must travel with it; see the shared-skill-directory trust notes before
  wiring).
