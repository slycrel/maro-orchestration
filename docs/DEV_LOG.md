---
status: living
---

# Dev Captain's Log

One entry per session close, newest first, append-only — the dev-side
analog of the runtime captain's log. Entries sit under `## <date>`
headers (sessions span days; the date is when the entry was written),
carry a short bold title, name their thread slugs so `dev-recall` and
the next census can join against them, and each ends with a
**Surprised by:** line (Jeremy 2026-07-28: "your data omniscience
aside, there are always angles that surprise"). Plain-language rule
(Jeremy 2026-07-28): first use of a codename or internal term in an
entry gets a short gloss — the reader has less context than the
writer. This is a narrative record, not a work queue — obligations
still live in BACKLOG/GOAL_BRAIN; a session that ends conversationally
still gets a line (same spirit as SF-13). Rendered to the viz server's
Dev log tab at maro.feifdom.com alongside Runs and Reading.

## 2026-07-29

**§9.3 review round — status honesty joins the declaration** — threads:
`compound-thinking`, `adaptive-execution`. Post-land codex review of
0965a7c (skeptic/architect/minimalist): 2/2 findings verified real, 0
hallucinated — eleventh clean round, both accepted and fixed. The
headline was a three-lens consensus: the declare-blocked stamp
(thesis-refuted, structural evidence) could land on a run that still
reported status="done", because status demotion belonged to a separate
rail gated at confidence ≥ 0.7 while the restart predicate starts at
0.6 — a run could be simultaneously "done" and declared blocked in
that band. Fixed by making the consume branch demote status itself:
the evidence behind declare-blocked is deterministic (the same hard
check failed in BOTH attempts), which outranks any LLM-confidence bar
— verified-done beats reported-done, the same principle the demotion
rail was built on. Second find (architect): command-only fingerprints
would false-stall a broad command (`pytest -q`) that failed on
DIFFERENT tests across attempts — the one direction (fail-closed) the
design promised to avoid; fingerprint material is now
command+exit+output-slice, the truer twin of `_error_fingerprint`,
which hashes failure content rather than probe names. **Surprised
by:** my own BACKLOG entry nearly argued me out of the second fix —
"fingerprint coarsening is evidence-gated" was written about
*loosening* the match, and the reviewer's finding was the opposite
defect wearing the same word.

**§9.3 structural declare-blocked ships (star + runtime)** — threads:
`compound-thinking`, `adaptive-execution`, `star`. The chunk-9
remainder build, ratified by Jeremy's "proceed as you've outlined."
Star half first per the build order: the ledger gained a Map Δ column
and a two-consecutive-Δ0 stall trigger — the one decision star left to
vibes (§3's own observation) now fires structurally, with typed-stop-
or-justified-continuation as the forced choice. Runtime half extends
the Phase 62 seam one level up exactly as §12 nudge 1 prescribed:
`ClosureVerdict.failed_checks` + `closure_fingerprint()` (twin of
`_error_fingerprint`), `evaluate_closure(prior_verdict=...)` mapping an
identical-fingerprint restart to `action="declare-blocked"` with a
thesis-refuted recommendation, handle stamping it first-write-wins —
stall-driven stop, decoupled from MAX_RESTART_DEPTH, zero new LLM
calls, fails open. The star exercise (restart-stall recon, 1/4
delegations, spot-verified clean) grounded the ship record honestly:
10 closure restarts live, all depth 1, and in both fully-recoverable
pairs the checks were REGENERATED with different wording — v1's
command-identity match would not have fired there. Shipped anyway on
fail-open grounds with the log line as the pivot readout; coarsening
BACKLOG'd evidence-gated. **Surprised by:** the recon overturning the
BACKLOG entry I'd written an hour earlier — the main-gate join I
described as "parent_loop_id → run metadata" is impossible as written
because metadata.json carries no restart lineage at all; the ancestry
lives only in captain's-log LOOP_CREATED events. The persistence map I
assumed and the one that exists diverged exactly where the design
leaned on it.

**Contamination repair — the provenance gate ships (forked review
session, part 2)** — threads: `dispatch-contamination`,
`memory-knowledge`, `hermes-swap`. Jeremy granted all three asks from
part 1 of this forked session — direction (channel separation +
maro-side guards), extraction of the contaminated lesson ("reminds me
of mcaffee quarantining files back in the day"), and priority ("before
we do further damage") — so this session built the repair. The
incident, glossed: Poe (the Telegram concierge on the second Mac Mini)
wrote a dispatch prompt containing anti-escalation orders ("Do NOT
escalate or stop merely because a linked page cannot be accessed");
Maro's lesson extractor generalized those ORDERS into stored lesson
`db37d525` ("when a prompt explicitly says... treat that as a hard
constraint"), and the recall system injected that lesson into the next,
unrelated run. Instruction text had rewritten persistent memory. The
fix is a provenance gate (`src/lesson_provenance.py`): every minted
lesson is classified outcome-derived vs prompt-derived by a few
documented regexes, and prompt-derived ones are quarantined — stored
and visible in readouts, but excluded from every surface that injects
lessons into prompts, barred from permanent-tier promotion, unable to
reinforce existing lessons. The classifier's ground truth is the four
real lessons the incident minted: the contaminated one must trip it,
its three outcome-shaped siblings must not — the gate discriminates,
it doesn't blanket-block. The live store rows (`db37d525` flat +
`9d6b63fe` tiered twin) are quarantined and verified unservable.
Alongside: a typed dispatch envelope spec (docs/DISPATCH_ENVELOPE.md
— user ask verbatim, operator context labeled, artifacts travel with
provenance; machine-to-machine only per Jeremy's UX call), and the
maro-dispatch skill (the "skill we provide to help an orchestrator get
started" he remembered) got goal-authoring rules — while merging, we
found Poe had self-patched its live copy with advice to write
recovery-ladder directives INTO goals, i.e. instructions to produce
exactly the scaffolding class that caused the incident; folded the good
parts back, rewrote that part, installed 0.2.0 to mini2 with a backup.
**Surprised by:** the self-patch find — the contamination loop had a
third leg nobody was looking at. Poe learned "write firmer scaffolding"
from the same incident Maro learned "obey scaffolding" from; two
learning systems reinforcing each other's worst lesson through one
shared prompt, each invisible to the other.

**Provenance gate round 2 — adversarial review closes five holes** —
threads: `dispatch-boundary`, `memory`. Post-land codex review of the
gate (skeptic/architect/minimalist), findings verified per the
~30–50%-hallucinated rule: 5 of 7 survived. Two were high and real:
(1) the killswitch could re-arm quarantined rows — with the gate off a
duplicate write carried `minted_from=""`, and both stores' dedup-clear
branches accepted any non-prompt value as citizenship; clears now
require an affirmative `"outcome"`. (2) the worker-recall sqlite
mirror (memory_bridge) ingested with no minted_from filter — the next
director run would have re-injected db37d525 into worker prompts
through the one surface the gate didn't cover; ingest now skips
quarantined rows and `invalidate_lesson_mirror()` cleans copies
ingested before a stamp (live mirror verified clean — last ingest
predates the mint). Also fixed: tiered mint callers truncated
`source_goal` to 120 chars, starving the scaffolding-echo signal
(callers now pass full goals, the store truncates the row excerpt);
the extractor's LONG style-example seed and `get_canon_candidates`
didn't filter quarantine. Rejected as designed: text-only
obedience-shaped lessons can't earn citizenship via re-record — that's
the quarantine bias working; the escape hatch is the explicit `--clear`
verb. **Surprised by:** the mirror finding — "every injection surface"
was audited on the JSONL stores while a sqlite copy of the same data
sat behind a bridge, one director run away from serving the exact
lesson we'd just quarantined.

## 2026-07-28

**Late evening — closure-check unification shipped (the free-slot
build)** — threads: `closure-unification`, `adaptive-execution`,
`compound-thinking`. With Jeremy holding the two discussion lanes (the
forked telegram-runs review, the M1 workflow contrast), this branch
took the build lane and shipped the census-assigned free-slot chunk:
closure-check unification. The census one-liner said "fold
`verify_goal_completion` into `director_evaluate`, retire
`ClosureVerdict`" — the pre-reads said otherwise. The April design
spec predates months of verdict-integrity burn-in (the judged
tri-state that keeps verifier failures from blaming the goal,
deterministic downgrades, the positive-evidence restart gate), all of
it living on the verdict object the spec wanted retired, consumed at
four call sites, not the three the census counted. So the honest
unification is a decision layer: `director.evaluate_closure()` runs
the untouched evidence pipeline and deterministically maps the verdict
into the director's shared action vocabulary; the verdict rides the
decision as its evidence record. Handle's restart gate now consumes
the decision's action — pinned by a test that would catch it silently
re-deriving from verdict fields — and the choke point is where §9.3
declare-blocked verdicts (greenlit earlier tonight) and a possible
gate-reads-scope check plug in later. Existing closure tests all
passed unchanged through the new layer, by design, not luck: the
monkeypatch seam (`director.verify_goal_completion`) was chosen as the
call target precisely so the old suite would exercise the new path.
**Surprised by:** how asymmetric the two "unified" functions turned
out — the census framed them as siblings, but one is a single cheap
JSON decision and the other is a three-phase pipeline wearing four
layers of burn-in armor; the real unification content was never the
fold, it was giving the armor a shared vocabulary.

**Evening — telegram-runs review, proposals 2–4 adjudicated, chunk-9
spitfire** — threads: `telegram-runs-review`, `open-thread-structure`,
`compound-thinking`, `dev-log`. Reviewed the two self-diagnosis runs
Jeremy dispatched via Telegram (tawny-ferret: Karpathy LLM-wiki
pattern; dapper-oak: the Erdős-problems X-thread workflow) — full
record in `docs/history/2026-07-28-telegram-runs-review.md`. Headline:
both memos are decision-grade and mostly verify clean, but Poe's
prompt scaffolding claimed a gist was "already recovered" when no gist
text exists anywhere in the run, and its anti-escalation boilerplate
has compiled itself into maro's lesson store (lesson `db37d525`) — the
dispatch-escalate pathway the runs were meant to diagnose is no longer
being exercised at all. Review discussion split to a forked session.
Jeremy then adjudicated the remaining thread-structure proposals: the
narrative spine (this doc) ratified with formatting fixes, date
headers, and its own viz tab; the `THREAD[slug]` marker/collector
machinery declined for now ("let's not overengineer our dev workflow")
with a revisit rider on the next census; working-set promotions stay
self-serve with notification. His spitfire pass on the compound-thinking
soft items landed five dispositions (lens-not-schema for plan maps;
greenlight to try structural declare-blocked; learned-behavior global
to start; capability-investment default-yes; verbal-UX to BACKLOG) —
recorded in COMPOUND_THINKING_DESIGN.md §15 and GOAL_BRAIN.
**Surprised by:** the scaffolding-contamination find — a concierge's
per-prompt babysitting quietly becoming persistent learned behavior is
a failure mode none of our reviews had a category for; the lesson
store faithfully learned the wrong lesson from a false premise.

**Daytime — thread census, reading tab, drift batch** — threads:
`thread-census`, `house-style`, `docs-surfacing`, `dev-log`. Landed
the thread census (third real use of the star mini-orchestrator
skill): 56 threads / 7 states, 7 premise-drift finds — the headline
being that park reasons rot silently (C4's "box runs container off"
gate was 12 days stale; closed on the spot against live config).
Jeremy ratified the ACTIVE/PARKED split (cap 7), corrected the
quiz-decree as too strong (style matches purpose — runtime journal
amended, decision 83f06acf), and offered his work-side CodeLikeJeremy
proof-of-concept, which yielded six house-style patterns including the
headline steal: a style doc with a regression harness. Then a detour
on his "reading .md over ssh isn't a perfect experience" — the viz
server gained a Reading tab (docs/READING_QUEUE.md → reading.html,
GitHub-rendered links, nothing self-hosted), live at maro.feifdom.com
same session. AFK loose-ends pass closed drift find #5 (full suite
exit 0; the fragile parallel-runner seam turned out deleted by swarm
chunk 1) and examined the free-slot trio: two of three (depth-cap #31,
backend-resilience #32) were already shipped weeks ago behind stale
doc annotations, so the free slot goes to closure-check unification
(#30) by elimination. Jeremy adjudicated the drift batch on return:
all four re-parked on falsifiable reasons — his summary "all claims
and doc-guidance as much as anything" — with the thread-architecture
work getting a Remaining-pieces ledger (THREAD_ARCHITECTURE.md L1–L5,
census-tested) as the answer to "I'm a little concerned it will get
lost," and next-leap packaging holding first claim on the next free
slot. The reading queue's first row completed its full lifecycle
(queued → read → decided → Done) within one day of the feature
existing. **Surprised by:** the recon-delegation contract
(verbatim-quote + file:line + disposition) producing the repo's first
10/10-clean spot-verification sample — the historical 30–78%
hallucination rate apparently wasn't a model property but a
contract-shape property. And its immediate qualifier: perfect
quote-accuracy still inherited two stale "still open" doc annotations
into the census as live threads — currency is a separate check from
accuracy.
