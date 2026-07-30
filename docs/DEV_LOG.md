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

**Skill-stats measurement honesty — run-verdict attribution shipped** —
thread: `next-leap-packaging` / `measurement-honesty`. The disease
under the router symptom: skill "success" has meant "a step completed
while this skill keyword-matched the step text" — bystander credit,
per-step × per-run, failures only on hard-block — which is how the
store reached 99.4% positive and the top skills sit at 852 uses @
~1.0. Worse, every outcome was double-counted (`update_skill_utility`
recorded stats internally while both live callers also recorded them
directly). Shipped, in Jeremy's approved order (thread 1 of 3): the
double-count fix; an honest counter pair on SkillStats
(injected_runs / injected_successes / injected_success_rate) recorded
at the closure-verdict seam — when `stamp_outcome_verdict` lands a
FULL-trust verdict (era-10 `verdict_trust` single-gate law), each
skill in the run's `source/skills_manifest.jsonl` (skills that
ACTUALLY entered a prompt, written at injection time) gets the run's
goal verdict as its label, idempotent across verdict re-stamps via a
`source/skill_attribution.json` marker; and the packaging readout now
prefers the honest regime wherever it exists, labeling every rendered
number `[run-verdict evidence]` vs `[legacy step counts]`. Legacy
counters keep accruing (single-count now) so breakers/escalation stay
fed; consumer migration is a BACKLOG item with the consumers named.
Side effect worth knowing: the attribution markers give (goal, skill,
verdict) joins on disk — the training data the goal-aware router
retrain was blocked on now accretes for free. Pinned end-to-end: seam
gates (FULL / directional / unjudged / no-manifest / no-run-dir /
re-stamp / manifest dedup), counter math, utility-writes-no-stats,
readout regime preference. Suite green via test-safe.sh (exit 0).
**Surprised by:** the fix needed zero new plumbing — the injection
manifest (shipped for A/B variant routing) and the verdict seam (shipped
for contradiction wiring) were already the two halves of honest
attribution; nobody had ever joined them.

**Degenerate skill router — root-caused and benched** — thread:
`next-leap-packaging`. The packaging readout's day-one catch (same 3
skills @0.992 for every goal) turned out to be two structural defects,
not one: the training corpus is 99.4% positive (166/167 — success_rate
labels over a store where nearly everything succeeds), AND
`route_skills` never puts the goal into the feature vector — it scores
`skill.description` alone, so the model's output is a goal-independent
per-skill prior by construction. The docstring claimed the goal was
"used as query context"; it was dead code's promise. Fix is a
discrimination guard, not a retrain: score spread < 0.05 across the
candidate set → results degrade to keyword-method, and
`find_matching_skills`' existing router check falls through to
goal-sensitive matching. Live delta before/after: haiku goal went from
"Codebase-to-Proposal Gap Mapping" to `haiku_to_file`; the readout's
would_include buckets went from one constant trio to 46 distinct
skills in builder×agenda. Goal-aware retraining is BACKLOG'd honestly:
skill-stats rows carry no goal text, so the model *cannot* learn
(goal, skill) → outcome until the store records goals. Surprised-by:
the guard beat the retrain so cleanly — a ranking with no spread is
worse than no ranking, and the cheap deterministic check that says so
covers every future degeneracy cause, not just these two.

Same afternoon, the review's other 3-lens finding closed: persona
dispatch rows now stamp the run's handle_id, and the readout joins
dispatch→outcomes on it (goal-prefix stays as legacy fallback, hit rate
reported in coverage). Both landed with Jeremy's M1 parallel-runner
cleanup merged underneath — first landing race resolved in a worktree
per the shared-tree rules, worked as written.

**Settled-chunks batch — four trio-triaged items shipped, and the trio
became a skill** — threads: `adversary-trio`, `dispatch-envelope`,
`knowledge-receipts`, `now-retry-rung`, `next-leap-packaging`. Jeremy's
"get through some of these chunks... and maybe make a skill? for the
trio" produced both halves. The skill first:
`.claude/skills/adversary-trio/SKILL.md` packages the forced-opposed
review panel (advocate/skeptic/scoper — three Codex seats given opposed
dispositions, not three costumes on one caution) with a keep/kill
adjudication ledger; its first run (recorded in
`docs/history/2026-07-29-settled-chunks-trio-triage.md`) materially
re-scoped and re-ordered the four settled items versus the solo prior —
run 1 of 5 keep-signal fired. The four chunks, in the trio's order:
(4) envelope delivery rendering — `cmd_result` now emits a `delivery`
block ({you_asked verbatim, dispatched_with}) for typed-envelope
dispatches only, so the human boundary shows the ask and the operator
prompt separately; (2) certainty receipts — injected lessons and
standing rules now cite their backing (confirmations, last-verified,
reinforcement counts) or read as "observed once"; zero plumbing, the
fields already rode the injected objects; (3) NOW artifact-retry rung
(shallow half) — one seeded same-lane retry on a self-verdict failure,
re-judged against the original ask, both attempts fully recorded,
gated `now_lane.artifact_retry` default OFF (star port + 3-arm matrix
explicitly out per triage); (1) next-leap packaging slice 1 —
report-only `packaging_readout` CLI (persona × goal-type would-include /
would-exclude / insufficient-evidence buckets over the live selector +
skill-stats). The readout's first live run caught a real one: the
trained skill router is degenerate — the same 3 skills score 0.992 for
every goal, so slice-2 wiring would package identical skills into every
persona (BACKLOG'd as a slice-2 blocker). Also surfaced: skills.jsonl
`use_count` is not the evidence store (2/314 nonzero; skill-stats.jsonl
is), and the persona-outcomes→outcomes loop_id join is stone dead
(0/40). **Surprised by:** the readout paying for itself on its first
execution — built to inspect packaging evidence, it instead found that
the selector the packaging would sit on has been returning a constant
answer, which nothing else in the system was positioned to notice.

**Autonomous batch — trio-adjudicated work items shipped without Jeremy**
— threads: `dispatch-envelope`, `house-style`, `knowledge-liveness`.
Jeremy's standing directive ("implement the work items available without
me... I'll check back in when I can") ran through a Codex adversary-trio
triage of the eight open items: unanimous DO_NOW on dispatch-envelope
box-side intake, SSRF resolve-then-pin, and the errand re-measure;
PARTIAL on the wiring-claims docket (verify-only), HOUSE_STYLE.md v1
(repo-visible only), and the NODE_CANDIDATE invisibility pin (no promote
path — criteria are Jeremy's); DEFERRED B (playbook→director substrate
mix is a prompt-authority design call) and G (next-leap packaging needs
his explicit word, not a generic directive). All six actionable items
shipped and landed: typed `maro-dispatch/v1` intake (user_ask IS the
goal everywhere; operator context rides the ancestry channel pre-labeled;
extraction exclusion by construction, interface-pinned; attachments to
dispatch-artifacts/ with provenance sidecars; malformed declared
envelopes bounce at every boundary), the docket verified 7/8 wiring
claims with one mischaracterization caught (RULE_GRADUATED has a working
CLI emitter, zero live firings), and the errand re-measure showed 7m12s
vs 16m43s with safe-pair NOT the lever — the tail is adversarial claim
review, remaining distance is plan-shape. Combined post-land codex
review (3 lenses vs the whole batch, CONTESTED, 0 hallucinated code
claims — thirteenth clean round;
docs/history/2026-07-29-autonomous-batch-adversarial-review.md) found
three real HIGHs, all the same species — a correct mechanism with one
unwired entry point: the explicit-api_key branch escaped always-wrap,
the SSRF pin trusted ambient proxy env, truncated declared envelopes
fell silently to prose. All fixed same session with tests.
**Surprised by:** the suite-wrapper incident — a trailing `echo
"exit=$?"` masked the suite's exit code, the task notification reported
success, and main went red for ~2 minutes until the census tripwires
(frontmatter, packaging) caught my own new files. The tripwire system
worked exactly as designed, against its author.

**Persist-the-artifacts chunk — lineage and closure evidence become
durable** — threads: `compound-thinking`, `adaptive-execution`,
`artifacts-over-streams`. Jeremy's adjudication of the §9.3 fragility
finding: "we should fix the lineage, not the wording — I want all of
the artifacts we can persisted," for debugging/dev work and for
showing a doubting user the path a run took (decree recorded,
a07c6c74; the fuzzy fingerprint-coarsening BACKLOG item declined in
its favor). Two writers shipped: every loop a run dir hosts now
appends its lineage — loop_id, loop_reason, parent_loop_id,
continuation_depth, created_at — to metadata.json `loops[]` (restart
ancestry previously lived ONLY in captain's-log events; zero of 728
live run dirs carried it), and every closure verdict appends its full
evidence — per-check command/exit/outcome/output rows, failed-check
signatures, fingerprint — to build/closure_verdicts.jsonl (per-check
detail previously survived only in record-mode call transcripts,
which don't exist on single-backend boxes; 7 of 10 live restart pairs
had no recoverable child checks anywhere). Both writers best-effort,
both liveness-pinned. Consequences: the §9.3 main-gate join's
material is now a local read of the same run dir, and the
fingerprint's live hit/miss-rate becomes measurable offline instead
of argued about. Post-land codex review (3 lenses, 4/4 verified real,
0 hallucinated — twelfth clean round;
docs/history/2026-07-29-persist-artifacts-adversarial-review.md)
caught the chunk failing its own decree three ways, all fixed same
session: skipped verifications persisted nothing (skip rows now
written), the ground-truth file excerpts the verdict LLM judges were
dropped from the rows (now carried), and the evidence went to disk
unscrubbed while every other persisted record passes secret_scrub
(now scrubbed — the sharpest catch, given the precedent sat twenty
lines from code read the same day). Fourth finding accepted-as-noted:
queued continuations detach from run dirs on purpose, so both writers
are inert there until that lane gets the shared run lifecycle
(existing BACKLOG item, rider added). **Surprised by:** how small the
fix was once named — two append writers, ~sixty lines, against a gap
three separate recon/audit passes had walked past because the
captain's log *looked* like coverage.

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
