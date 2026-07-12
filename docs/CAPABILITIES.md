---
status: living
---

# Capabilities & Example Goals

**Status: living catalog** — this doc grows with use. Add real asks as they
happen; delete nothing (mark superseded instead).

Two things live here, deliberately together:

1. **A catalog of real example goals** — actual questions and tasks a person
   would hand the system, simple through complex. These are the test cases,
   the learning corpus, and the capability target all at once: a goal listed
   here is something Maro should handle well, and each one is concrete enough
   to run and judge.
2. **The blank-slate capability target** — the small-ish pre-installed skill
   set a fresh install should ship with, so day-one Maro is useful before it
   has learned anything.

Why they're one doc: the lesson funnel and skill promotion only learn from
*friction on real work* (the 2026-07-10 live batch proved a synthetic "write
a haiku" batch teaches nothing — 9 runs, zero lessons, correctly). The
example catalog is where real work gets captured so testing, learning, and
the shipped skill set all pull from the same well.

**Grounding rule (house discipline: claimed ≠ probed):** every example marks
whether it has actually been run. `verified` = ran end-to-end with the
expected UX; `target` = we believe current machinery covers it, unproven;
`aspirational` = needs capability that doesn't exist yet. No pointer, no
claim.

---

## The canonical simple case

> **"Where can I get non-ethanol gas in or around Manti, Utah?"**
> *(Jeremy, from the car, 2026-07-10 — status: `target`)*

Why this is the north-star simple case: the information exists publicly, but
collecting it takes a passenger several steps — a search, cross-referencing a
couple of station-finder sites, checking which results are actually near
Manti, maybe a phone number to confirm. A person asks one sentence and wants
one answer: *station names, locations, and any caveats, in a minute or two.*

That's the UX contract for the whole simple tier: **one conversational ask →
orchestrated multi-step research → one direct answer with sources.** No
plan narration, no "here are ten links," no follow-up questions unless the
ask is genuinely ambiguous.

What it exercises: NOW-vs-AGENDA routing, web research with source
triangulation, answer synthesis, knowing when the answer is good enough to
stop.

### First live runs (2026-07-10)

**Run 1 — natural routing (NOW lane, 0.85 confidence): FAILED the contract.**
16s, ~$0.016. Answered from model knowledge with "I don't have real-time
access... here's how you could find out" — a how-to-search list. That is the
exact anti-pattern the contract exists to kill: the passenger does the steps.
The router saw a short factual question and never recognized
needs-live-external-data as an AGENDA signal. **The gap is routing, not
capability.**

**Run 2 — forced `--lane agenda`: PASSED the contract on content.**
7 steps, ~24 min, $2.47 (blew through the $2.00 cost budget + slush; run
ended on the cost hard stop after the brief was already written). Delivered
`research-brief.txt`: bottom line ("drive to Maverik #536, 89 N Main St,
Ephraim — 7.3 mi, open 24h"), 4 ranked stations with per-station confidence,
live store-page verification (not chain-level marketing), stale-source
dissent (Pure-Gas.org phone numbers corrected against official pages), open
questions, next actions. One ask → one sourced answer. This is the UX we
want.

**What the pair proves:** capability `verified`, delivery `target`. Two gaps
before the canonical case is honestly `verified` end-to-end: (1) routing —
NOW must detect needs-live-data asks and escalate (or research inline);
(2) envelope — ~24 min/$2.47 is a research-project cost for an errand
question (MODEL_POWER was resolving to subprocess `claude -p`; the
token_explosion introspection flagged worker re-read churn). A passenger in
a car wants this in ~1–3 min for cents.

**Run 3 — post-fix envelope measurement (2026-07-11, run 5126986b):**
after the orphan-read-step folding, latency breaker, closure evidence
attachment, and budget-breaker fixes: **6 steps (was 11), 16m43s wall (was
~28), $1.52 (was $2.47 hard-stop)**, status done, goal achieved, closure
complete=True 0.95 with 5/5 checks and zero gaps — first Manti run with a
completely clean card. Envelope arc: $2.47/24min → $2.00-capped/28min →
$1.52/16.7min. Still an order of magnitude from ~1–3 min/cents; remaining
levers are routing (unchanged) and the worker's own tool-loop churn
(introspection now correctly attributes fresh-token growth to artifact
re-read/rewrite inside worker subprocesses, not cache re-reads — steer
toward patch/diff edits).

---

## Example catalog

Format per entry: the goal as a person would say it · what success looks
like · what it exercises · status. Add new entries in whatever tier fits;
real phrasing beats cleaned-up phrasing.

### Tier 1 — errand research (one ask, one answer, minutes)

| Goal (as asked) | Success looks like | Exercises | Status |
|---|---|---|---|
| "Where can I get non-ethanol gas in or around Manti, Utah?" | 1–3 named stations w/ locations + confidence caveats, sourced | multi-source research, synthesis, stop criteria | `target` — content verified live 2026-07-10 (forced agenda lane); routing + cost envelope still fail the contract, see canonical-case section |
| "What are the library hours in [town] this Saturday, and do I need an appointment for [service]?" | direct answer + source link, flags stale pages | freshness judgment, official-source preference | `target` |
| "Compare the three cheapest ways to ship a 40lb box from Utah to Ohio this week." | small table, prices dated, winner recommended | structured comparison, quantitative extraction | `target` |
| "Is [product] compatible with [other product]? People online seem to disagree." | verdict + why the disagreement exists | conflicting-source adjudication | `target` |
| "Give me the top five breakfast restaurants in [city] according to Reddit, sources included, no fake links." | 5 real places, every link HTTP-validated, aggregator-sourced | live local search, URL validation, fabrication resistance | `target` — from failure corpus 1.5 (ChatGPT: 1 fake restaurant, 100% fake links) |
| "My [exact appliance model] is doing [symptom] — what's wrong?" | diagnosis grounded in that model's spec/manual, not the category-majority guess | instance-specific lookup, majority-case-guess flagging | `target` — from failure corpus 2.1 (dual-compressor fridge diagnosed as single) |
| "Find a source for this claim: [claim]. If you can't verify it, say so." | 2–3 independent sources or an honest "unable to verify" | source triangulation, negative-result honesty | `target` — from failure corpus 1.3 |
| "Tell me about [obscure book/author]." | catalog-grounded description or "insufficient information found" — never invented | long-tail entity handling, retrieval-before-describe | `target` — from failure corpus 1.6/1.10 |

### Tier 2 — research + artifact (multi-step, one sitting)

| Goal (as asked) | Success looks like | Exercises | Status |
|---|---|---|---|
| "Summarize this repo's commits since the last tag into an operator digest." | digest artifact, accurate counts, notable-change selection | tool use on local data, summarization judgment | `verified` (live batch 2026-07-10, changelog_digest — promoted to skills-lite) |
| "Census this JSONL ledger: totals per day, anomalies flagged, two sentences of interpretation." | correct table + honest interpretation | deterministic computation + narrative honesty | `verified` (live batch 2026-07-10, step-costs census) |
| "Read this design doc and write a one-page operator summary: what changed, what's left." | faithful compression, no invented claims | long-doc comprehension, fabrication resistance | `verified` (live batch 2026-07-10, RECONCILIATION summary) |
| "Research [topic] and give me a decision brief: options, tradeoffs, your recommendation." | brief with a real recommendation, not an option table | research depth, opinionated synthesis | `target` |
| "Search Reddit/HN/X for first-person accounts of [phenomenon]; build a sourced catalog, verbatim quotes only." | concrete-instance entries w/ author+date+link, honest audit trail, rejects listed | social_search skill, filter-bar discipline, evidence-depth honesty | `verified` (run 692bd96f 2026-07-11, ai-failure-task-patterns) |
| "Examine your own run [id] and propose what would make it faster, without cutting steps." | proposals with code-level premises, each premise verified before acting | self-analysis, verify-before-fix gate | `verified` shape (self-speedup dogfood 2026-07-11 — 4 proposals, 2 had false code premises caught in adjudication; the gate IS the use case) |

### Tier 3 — standing/recurring work

| Goal (as asked) | Success looks like | Exercises | Status |
|---|---|---|---|
| "Watch [site/feed] and tell me when [condition]." | fires on condition, silent otherwise, no re-notification spam | heartbeat-driven checks, state across runs | `target` (heartbeat + scheduler exist; needs a lived example) |
| "Each morning, digest of: [my feeds/ledgers], anything anomalous flagged." | short, substantive, skips no-news days | recurring synthesis, novelty detection | `target` |
| "Track [market/price] and research any move bigger than X." | ledger-driven, compounds across runs | persistent-workspace pattern | `verified` shape (polymarket-edges workspace, 2026-06) |

### Tier 4 — build tasks

| Goal (as asked) | Success looks like | Exercises | Status |
|---|---|---|---|
| "Write a script that [transforms X to Y], with tests, in this repo." | working code, tests pass, honest about limitations | build worker, verification, write fence | `verified` shape (PM/dev recipe workflow, orchestrator-test-recipes) |
| "Build a skill that [does a task you just did] so future runs can reuse it." | skill-shaped artifact that passes both promotion gates | skill synthesis, skills-lite lane | `verified` (live batch 2026-07-10) |
| "Take this failing test suite and fix what's actually broken, don't paper over it." | root-cause fixes, no test deletion, report of what was wrong | debugging judgment, integrity under pressure | `target` |
| "Extract [fields] from this filing/document into JSON matching this schema." | schema-valid AND field-level content-verified against the source — two separate checks | structured extraction, content-vs-format verification split | `target` — from failure corpus 1.11 ("valid JSON, wrong answer") |
| "Iterate on this parsing code until it produces [expected output] on [input]." | every iteration's claimed output is sandbox-executed, not hand-written; auto-loop on mismatch | execution grounding, claimed-vs-actual diff | `target` — from failure corpus 3.2 |
| "Figure out how to get [blocked/undocumented data source] working, then capture the recipe as a reusable skill." | working access path + a skill file future runs can load | probe-driven exploration, self-bundling ("learn a language to get things done") | `target` — done by hand 2026-07-11 (Reddit RSS door, X CT-cache reseed → skills/social_search.md); the target is Maro doing this loop itself |

### Tier 5 — long-horizon compound projects

| Goal (as asked) | Success looks like | Exercises | Status |
|---|---|---|---|
| "Research [broad topic] over the next week; maintain a ledger; deepen one thread and add one new one each run." | compounding ledger, visible thread ancestry | multi-day continuity, goal ancestry, self-direction | `verified` shape (deepen-1-add-1 pattern, polymarket-edges) |
| "Plan and execute [multi-milestone project]; escalate only on genuine blockers." | milestones tracked, escalations rare and real | director/worker delegation at horizon, escalation judgment | `aspirational` (escalation channel undesigned — 1.0 item (a)) |
| "Coordinate with [other instance/agent] on [shared goal]." | clean handoffs, no duplicated work | cross-instance sharing | `aspirational` (post-1.0; see below) |

---

## External failure-pattern corpus

`research/ai-failure-task-patterns.md` (v2 2026-07-11, four Maro runs +
operator X sweep): 21 real tasks that single-turn AI assistants got wrong,
sourced from verbatim HN/Reddit user complaints — all full-content evidence
(post body + comment thread read). Grouped into 5 pattern families with a
root-cause taxonomy extended in v2: full-content re-reads corrected 2 of the
5 title-only diagnoses and split Family 4 into three distinct mechanisms
(transient platform outage / statelessness-by-design needing external
memory / unscoped cascading deletion). An X sweep (160 tweets, 8 query
angles) yielded 0 keepers — Top-ranked X is engagement bait, documented
honestly rather than force-fit. Each entry names the concrete task, the
concrete failure, and the orchestration capability that would have prevented
it (live retrieval, execution grounding, citation-vs-source diffing,
retrieval-on-correction, external state checkpointing).

Why it's here: the catalog above captures asks *we've* had; the corpus
captures asks *the world* has that assistants fail — the exact gap
orchestration exists to close. Seven corpus entries are folded into the
tiers above (marked "from failure corpus N.N"). The rest map onto existing
entries or onto capabilities Maro already exercises (Family 4's
state/session losses are what run cards + checkpoints already solve; Family
5's regenerate-instead-of-reverify is what closure evidence attachment
already counters). Known skew, per the corpus's own audit trail:
HN-and-ChatGPT-heavy by data availability; Reddit entries are title-only
evidence.

---

## Blank-slate capability target (pre-installed set)

The goal: a fresh `maro bootstrap` is *useful the same day*, before any
learning has happened. That means shipping a small, curated skill/capability
set — not everything we've ever promoted, but the ones that cover the catalog
above. Draft target (to react to, not final):

- **errand-research** — the Tier 1 contract: multi-source lookup → one
  sourced answer (the Manti case is its acceptance test)
- **research-brief** — topic → decision brief with a recommendation
- **repo-digest** — commits/changes → operator digest (exists: changelog_digest)
- **ledger-census** — JSONL/CSV → totals, anomalies, interpretation
- **doc-summary** — long doc → faithful one-pager
- **watch-condition** — feed/site + condition → notify-on-fire
- **code_review** — adversarial, evidence-gated (exists: graduated 2026-07-09)

Selection principle: each pre-installed skill should be the crystallization
of a catalog tier, so the shipped set and the test corpus verify each other.
Anything not exercised by a catalog entry doesn't ship in the default set.

**Later (post-1.0, direction not design):** a shared, *trusted* skill
directory instances can pull from — crowd-sourced or curated. Trust boundary
notes so we don't relearn them: entries need provenance + the same
injection/dangerous-pattern gates as skills-lite (a shared directory is a
supply chain — cs-r2-01's whole threat model, at internet scale), and
imported skills should arrive as reviewable candidates, never auto-trusted
(the `maro-import` quarantine posture already models this). Sharing
*learning* across instances (lessons, not just skills) rides the same rails.

---

## How to add an example

When a real ask happens (in the car, mid-session, from anyone): capture the
goal **as phrased**, note what a good answer would have looked like, guess
the tier, mark it `target`. Run it when convenient; promote to `verified`
with a date + run pointer, or file what broke. Real phrasings carry the
ambiguity and context-dependence that synthetic test goals launder out —
that's exactly what the router, scope generation, and lesson funnel need to
be tested against.
