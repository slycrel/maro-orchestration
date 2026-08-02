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

Run `4bf7f761-merry-magpie` (chlorination claim, 2026-08-01, $24.88): spent
roughly **$15 of its $25 discovering, across ~6 steps and dozens of
attempts, that HathiTrust, Google Books, and Archive.org are
infrastructure-blocked from this box** — each block honestly documented
with HTTP codes in `source_trail.md`.

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
**a terrain fact is cheaply re-checkable.** DECISION (provisional) — **HELD
2026-08-02 pending record dig**: probes are stored, never auto-executed at
mint; they run at *injection* time under the existing read-only probe guard
(claim_probe's mechanical gate) when the fact is older than its class's
freshness window. Trust decays, data never does — a contradicted terrain
fact flips to grey with the contradiction recorded, exactly like rules.

> **Jeremy 2026-08-02:** recalls the earlier probe discussions concluding
> checks should be *planned as steps* ("we decided to not have the steps
> test directly, that was a possible step itself based on research
> findings"), tied to the bridge-building sub-goal arc; possible exception
> where a hypothesis already has data behind it (proving existing data vs
> gathering as the step work). Session-record dig in flight to recover the
> original decision before ratifying. Note this section as written also
> contradicts §4d ("never auto-executed at mint" vs the mint-time probe
> shortcut) — the dig settles both.

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

### 4d. Validation — the existing loop, plus the probe shortcut

Teachings mint **provisional** (excluded from injection until confirmed,
per the shipped provisional-lesson lifecycle) with one exception worth its
own decision: DECISION (provisional) — **HELD 2026-08-02, rides on §4a's
record dig** (a mint-time probe execution is exactly what Jeremy recalls
deciding against; his candidate exception — hypothesis already backed by
data, proving rather than gathering — is close to what this shortcut
describes, so the recovered record decides it) — a terrain teaching whose
`verify_probe` passes at mint (one cheap read-only check confirming the
claimed state) skips provisional and enters at confirmed-with-evidence,
because unlike a strategy lesson its truth is checkable *now*. Everything
else rides the normal receipts: `times_applied` at injection,
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
