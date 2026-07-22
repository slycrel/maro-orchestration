# Milestones — Prioritized Work Queue

What to do next, in what order. Updated each session. Deferred ideas live in BACKLOG.md; completed phase history in docs/history/ROADMAP_ARCHIVE.md (ROADMAP.md is a stub). This file is the executable queue.

Last updated: 2026-07-21 — **swarm-review arc, chunk 4 SHIPPED** (of 8; plan:
`~/.claude/plans/abundant-gathering-lagoon.md`). Chunk 4 (contradiction
wiring — `contradict_pattern` finally has a runtime writer; the
contested→refight lifecycle is reachable for the first time): recall's loop
slice stamps durable citation IDs (`rules_cited` via new
`standing_rules_with_ids`, `lesson_ids_cited`) into RECALL_PERFORMED + writes
run-keyed `source/recall_citations.json`; `stamp_outcome_verdict` emits
CONTRADICTION_CANDIDATE on a FULL-trust (verdict_trust — era-10 law, pinned)
goal_achieved=False for a citation-bearing run;
`adjudicate_contradiction_candidates` (evolver cadence, BEFORE the refight
scan so candidate→contested→refought completes in one maintenance pass —
pinned end-to-end; cap 3/cycle; `knowledge.contradiction_adjudication_enabled`
default ON) renders tri-state verdicts — only exact "yes" mutates; UNDECIDED
is unjudged, never contested (law iv, pinned). Prereqs in-chunk: battery-V2
domain fix (promotion writes ""; 4 live rules migrated agenda→"" with archive
copy — they now actually inject on project-scoped runs) and era-09 provenance
(`StandingRule.source_lesson_ids` keeps all contributors). EVENT_TYPES
66→68; 24 new pins (test_contradiction_wiring.py + promotion/recall);
full suite green (185 items). **Next: chunk-4 adversarial review, then
chunk 5a.**

Previous checkpoint — 2026-07-21 **chunk 3 SHIPPED**. Chunk 3 (decisions.jsonl
writers — the store's read side was always live, recall substrate #3; it
never had a runtime writer): (1) executor DECISION directive — `decisions`
field on complete_step (max 2/step, 200/300-char caps), fan-out in
`_process_done_step` to the durable journal (`record_decision`), shared
context (`decision:{step}:{n}` — carried UNCOMPRESSED into every later
step's prompt via `decisions_block`; completed_context's 100-char compression
was how design calls evaporated), and the thread brain; (2) scope
director-proxy commitment journaled at the creation seam (binding for
planning AND closure); (3) SF-13 decree pipe: `PYTHONPATH=src python3 -m
knowledge_lens decision "<decree>" --rationale "<why>"` (CLAUDE.md rule
amended; blank-domain rows match all scoped reads, pinned). Consumer-first
liveness pins: record → recall → text in as_loop_block AND as_context_block
with no read-side mocks (test_recall.py). Fork-contract design note in
THREAD_ARCHITECTURE.md (leaf-local / parent-owned / evidence-based
escalation triggers — NOT parent-always-wins); ancestry write-side
unification BACKLOG'd (fork prerequisite, not this arc). REFACTOR_PLAN's
"record_decision (no writer)" removal row struck. **Chunk-3 adversarial
review DONE same day** (3 Codex lenses vs fe0072d, REJECT-as-reviewed →
remediated same session, 6/6 verified real, 0 hallucinated): parallel/DAG
paths were silently dropping decisions (fan-out extracted to
`record_step_decisions`, both parallel walks wired, pinned);
`locked_append` on the journal; SF-13 CLI fails closed; scope-proxy
decisions domain-scoped + goal_context in the TF-IDF ranked text;
2000-char decisions_block budget; valid-first 2-cap
(`docs/history/2026-07-21-chunk3-adversarial-review.md`). **Next:
chunk 4 (contradiction wiring, prerequisites i–v).**

Previous checkpoint — 2026-07-21 **chunk 2 SHIPPED**. Chunk 2 (playbook repair —
the live bug): `inject_playbook` is now RANKED selection (learned-over-seed,
newest learned first, dedup by normalized core, greedy 800-char budget fill —
kills the head-window horizon bug AND battery V6 seed-overflow; entry parser
`parse_entries` + pins in test_playbook.py); `curate_playbook` curation verb
rides `maybe_consolidate` (dream cycle): free deterministic dedup always +
size-gated (>4000 chars, `playbook.curation_min_chars`) CHEAP-tier LLM
compress with hard validation (headers/attributions preserved, ≤1.1× length,
bullets ≥60%) — invalid compression keeps the deterministic result;
archive-before-write to `playbook_history/` (append-only; abort curation if
archive fails), `PLAYBOOK_CURATED` captain's-log event, config kill-switch
`playbook.curation_enabled` (DEFAULTS census green); one-time live curation
done (5239→3172 chars, spam-free, original archived); decree-stale seed Cost
line fixed to the MID-floor decree. Live-path verified: loop recall block
renders learned entries ranked in, zero dupes. Side-find → BACKLOG:
record-mode never fires on single-backend boxes (record seam only exists in
FailoverAdapter; this box's bare subprocess adapter skips it — every run has
`n_calls: 0`). Wiring row 17 (director omits playbook) BACKLOG'd
consumer-first. **Chunk-2 adversarial review DONE same day** (3 Codex
lenses vs 257b34d, CONTESTED, 6 verified findings all accepted ≥ in part,
0/6 hallucinated): LLM call moved outside the playbook write lock
(snapshot → compute → compare-and-swap), compression guard made structural
(exact-line/Counter/ceil), rank-order dedup, hard 800-char cap,
atomic_write on both rewrite paths, newest-first rendering
(`docs/history/2026-07-21-chunk2-adversarial-review.md`).

Earlier checkpoint — 2026-07-21 **chunk 1 SHIPPED** (Phase 0 knowledge
journey + Phase 0.5 DEV_PATTERNS/battery landed earlier same arc). Chunk 1: execution
defaults unified at MID (handle entry, loop, thin mode; per-step cheap
downgrade removed; `classify_step_model` deleted; user-CONFIG cheap pin
unset — smoke-verified `model=mid` per step, hosted-free rung decisive,
zero ollama); local-model wiring REMOVED (`local_models.py` + bakeoff
scripts/tests deleted; ladder = Tier-0 → hosted-free → paid; revival trigger
in the retired `docs/LOCAL_VALIDATOR.md`); dead config keys deleted
(DEFAULTS census green); battery V5 planner persona-wrap fix; typed
finding-code vocabulary (`src/finding_codes.py` + DEV_PATTERNS convention);
factory adjudication — Phase 49's gate fired, branch archived as tag
`archive/factory-2026-03-31`, mode:thin/minimal kept as instruments
(`docs/history/2026-07-21-factory-adjudication.md`); report-only wiring
inventory saved (27 stores/events, 8 verify-before-fix surprises —
`docs/history/2026-07-21-wiring-inventory.md`); side-channel first-party
failure corpus folded into CAPABILITIES.md; stale-doc sweep (debate pass,
~5400-line claim, bootstrap_context, all-passes-cheap); BACKLOG batch adds
(10 revival dispositions, V3/V4, era C-tier drops, wiring surprises;
local burn-in item superseded → BACKLOG_DONE). Post-land adversarial
review (3 Codex lenses, per-chunk discipline via Jeremy's /goal): 6
verified findings fixed — three MORE residual cheap paths (factory_thin
CLI default, blocked-step hint/split recovery, classifier riding the MID
worker adapter), strict finding-code read boundary, hosted-free-aware
validator ROI (`docs/history/2026-07-21-chunk1-adversarial-review.md`).

Previous checkpoint — 2026-07-14 (test-suite truth + reduction pass SHIPPED). Pytest's
global marker filter had made every claimed "full" run silently exclude the
slow lane; `test-safe.sh` also advertised chunking while its 1000-file default
produced one chunk. Full is now genuinely full, `--fast` is explicit, the safe
runner uses five 40-file chunks, and the stale shell smoke follows the current
`cycle`/workspace contract without making a live executive-summary call.
Redundant parser suites collapsed into 11 behavioral matrices; repeated doctor,
source-graph, symbol-scan, timeout, and subprocess waits now reuse work or use
bounded test seams. Result: 6333→6171 tests, raw honest-full 117.8s versus the
old incomplete-default 141s, canonical chunked full 104.0s, slow lane 13.0s,
coverage 78.04% (70% floor). One duplicate budget test was also exposed as an
empty-file setup bug and corrected. No isolation/credential guards weakened.

Previous checkpoint — VERIFY_LEARN_ARC **V4 + V5 SHIPPED** — the navigator
half of thread decision #6, closing the whole arc. **V4 (divergence
adjudication):** at evolver cadence, un-adjudicated NAVIGATOR_DECIDED divergences
(navigator move ≠ pipeline) get a capped, cheap-tier LLM verdict (navigator_right
/ pipeline_right / both_defensible), appended append-only as `NAVIGATOR_ADJUDICATED`
and joined back into `python3 -m navigator_shadow --agreement` as an `adjudicated`
breakdown — the cutover-evidence surface, now standing instead of by-hand.
Gated OFF by default (LLM spend; `navigator.adjudicate_divergences`), CLI
`--adjudicate`; proven end-to-end on the box's 71 live divergences. **V5
(navigator lessons):** `pipeline_right` clusters (navigator-wrong shapes, ≥3
same-shape) crystallize into corrective navigator lessons (`navigator_lessons.jsonl`,
a derived view over the append-only adjudications) injected into `decide()` via
the worker-slice recall seam — A/B flag `navigator.lesson_inject` (default off),
`lessons_injected` marker on the decision row for shadow-comparison. Both ride the
existing cadence hook (no daemon); nothing is acted on/reverted (evidence only);
per-move cutover stays Jeremy's call. Knobs in DEFAULTS.md; 19 tests. Also this
session: **V3 dates hardening** — the class path's time axis now recovers
1274/1277 real diagnoses via an events-log join (`_loop_ts_index`), no longer
dormant. **The verify→learn arc (thread decision #6) is now fully closed.**
See `docs/VERIFY_LEARN_ARC.md` §5/§7.

Previous checkpoint — audit-incomplete convergence + backlog truth pass.
`maro-runs repair-audits [handle-or-loop]` and the existing autonomy/evolver
cadence consume exact per-loop repair records, replay verdicts, finalize only
the named row's deferred lesson/knowledge extraction, and clear quarantine
only after siblings converge. One workspace lock serializes sweeps; fair
ordering and failure caps prevent starvation/unbounded spend; all run-metadata
writers share locked RMW. See
`docs/history/2026-07-14-audit-delivery-and-resume-admission.md`.

Previous checkpoint — 2026-07-14 (VERIFY_LEARN_ARC **V3 — graduation behavioral
auto-verify SHIPPED**, the Opus chunk following Jeremy's "buildable now" call).
Applied graduation rows already flowed into V2's cadence verify — but on the
class-neutral *global* stuck-rate, in which a single failure class is noise, so
they only ever parked `unverifiable`. V3 makes the verdict **resolve**:
`verify_applied_suggestions` now consumes a row's V1 `expected_signal` and
verdicts a `failure_class_rate` row on *that class's* rate over
timestamped-diagnosis windows (self-falls-back to the stuck-rate when the class
windows are thin → a sparse class parks honestly, never verdicts off noise).
Each diagnosis's time coordinate comes from a go-forward `recorded_at` stamp
*or* an events-log join on `loop_id` (`_loop_ts_index`, ~99% coverage) — so the
class path is live on the full historical ledger (1274/1277 diagnoses on this
box), not dormant waiting for new rows to accrue. Confirmed/degraded →
calibrate/demote lifecycle + symmetric authority reused from V2 unchanged.
**Owner call landed as its safe default:** graduation rules stay advisor-gated
(human applies via `maro evolver apply`; nothing auto-applies a standing rule) →
a degraded graduation row is surfaced for review, never auto-reverted. Structural
`verify_pattern` grep stays pure observability (a grep miss ≠ the applied lesson
failed). Knob `evolver.verify_use_class_signal` (DEFAULTS.md, default ON). 13
tests (`test_evolver.py::TestVerifyClassSignal`, `test_introspect.py`). **The
applied-change verify→learn loop is now closed for BOTH the evolver-suggestion
(V2) and graduation (V3) lanes; next is V4/V5 — the navigator half of thread
decision #6.** See `docs/VERIFY_LEARN_ARC.md` §3/§7.
Previous checkpoint — 2026-07-14 (VERIFY_LEARN_ARC **V2 — cadence verdicts +
auto-revert SHIPPED**, the judgment-heavy Opus chunk). At each evolver cadence,
`verify_applied_suggestions()` renders a behavioral verdict on every
applied-but-unverified change (class-neutral stuck-rate, count-based
before/after windows keyed to each row's `applied_at`, trust-filtered) and acts:
**confirmed** → stamp + feed confidence calibration; **degraded self-applied** →
auto-revert + `EVOLVER_VERDICT` event + non-blocking notify; **degraded
human-applied** → surfaced to the review queue (blocking notify), NEVER
auto-reverted (symmetric-authority §3 decision); **inconclusive** → extend, park
`unverifiable` after 3. Trust policy (§4) shipped as first consumer:
`verdict_trust()` in memory_ledger.py (single source) → `closure_unverifiable`/
env-capped + low-confidence verdicts never count in a window. **Prerequisite
bug fixed same chunk:** `scan_evolver_impact` windowed on `created_at`/
`timestamp` but real `Outcome`s carry `recorded_at` — the warn path had been
dead on production data; `_outcome_ts` now prefers `recorded_at`. Knobs in
DEFAULTS.md (`evolver.verify_cadence_verdicts` default ON — safety mechanism,
only reverts what the system applied; `verify_min_post_apply`=10,
`verify_max_extensions`=3, `verify_delta_threshold`=0.05). Operator surface
`maro evolver verify [--apply]`. 17 new tests; both acceptance legs (one
confirm AND one degrade→revert with calibration) exercised. **Adversarial-review
hardening SHIPPED same day (584b902, 3 Codex reviewers):** bounded post-apply
window (no later-regression bleed → spurious revert), honest reverts (additive
`behavioral` flag → un-revertable/append-only degradations surface blocking as
`degraded_revert_failed`, never falsely "reverted"), authority re-check before
the irreversible revert, baseline floor `max(3,min//2)`, `scan_evolver_impact`
`and`→`or` gate; 6 regression-lock tests; reconciled clean with Codex's parallel
audit/admission work, full box-safe suite green (181).
Previous checkpoint — /goal catch-up session — EXT-AUDIT-2 residual
SHIPPED: `_stamp_verdict_tracked` quarantines deferred learning per-loop_id
when a closure/provenance/post-escalation verdict stamp write-fails or raises,
instead of silently falling back to unjudged; closure-restart boundary was
already fail-closed and untouched; 10 new tests, archived to BACKLOG_DONE.md.
Same session, VERIFY_LEARN_ARC **V1 — expectation stamping SHIPPED**: `Suggestion.
expected_signal` (additive, empty-default), all 9 graduation templates declare
`{"metric": "failure_class_rate", "class": <own key>, "direction": "down"}`
derived from their own dict key, `_EVOLVER_SYSTEM` teaches the LLM proposer the
same field (optional). 8 new row-shape unit tests.
Previous checkpoint — stale sandbox-stub backlog
decision RECONCILED: already resolved by C4 retirement (`69265f6`); open item
archived and living/dormant design references corrected. The real replacement
is the separately tracked container-executor burn-in, not skill-stub wiring.
Previous checkpoint — lesson-funnel intake
measurement SHIPPED: durable per-outcome extraction state/counts, dry-run
exclusion, completed-zero idempotency, archive-aware rolling text/JSON report,
and honest historical unknowns. Live 30-day evidence is 3/3 unknown, so yield
is `n/a`; real Claude follow-up APPROVED after persistence-order fixes.
Previous checkpoint — applied-only graduation
structural verification SHIPPED as a safe VERIFY_LEARN_ARC V3 precursor:
cadence events/optional notification, durable manual authority, and held-row
accounting fixed. Multiple Claude adversarial passes additionally forced idempotent but
retryable primary actions and claim/deliver/ack notification semantics. Full
behavioral verdict/revert was open here pending V1/V2; V1–V5 subsequently
shipped 2026-07-14 (see top checkpoints), so this historical precursor is
closed rather than an active queue item.
Previous checkpoint — first-consolidation
long-gap policy CLOSED with no amnesty: decay is already read-time state and
GC-eligible lessons are below the live injection floor; archive+resurrection
now protects evidence. Real Claude review required two caveats: the historical
38/38 event was infra-confounded, and archived keyword recall is weaker than
live ranked/task-scoped recall. Previous checkpoint — rolling reviewer
calibration SHIPPED: `scripts/probe-stats.sh` compares current and prior
archive-spanning `CLAIM_PROBED` windows, with honest missing-evidence buckets,
verdict-retention/coverage rates, deltas, JSON, and eight deterministic
regressions. Retention is explicitly not reviewer accuracy because a nonzero
exit may also mean a weak probe. Live
60-day data has only six decisive rows, all dismissed, so it is signal to watch
rather than a trend conclusion. Previous checkpoint — captain's-log event viewer
SHIPPED: `maro-log --events` provides a sortable archive-spanning TSV/JSON
slice over timestamp/event/loop/slug/key fields, preserving the existing
aggregate `--timeline` and adding no storage migration. Two real Claude
reviewers drove broader sort/cap/malformed-row/CLI coverage; follow-up found no
remaining HIGH or MEDIUM issues. Previous checkpoint — formal M1 local-validator
bake-off COMPLETE: one committed 14-case corpus replayed through four exact-
protocol candidates. VibeThinker-3B-4bit alone achieved 14/14 with full
decisive coverage and zero unsafe false-passes at 8.83s average; 1.5B, 8-bit,
and Ollama Qwen2.5-Coder-3B were rejected. The sweep also found and fixed the
0.60–0.74 RETRY→decisive-PASS threshold gap. Only production-Linux on-box
burn-in remains hardware-gated. Previous checkpoint — closure verdict ancestry
SHIPPED: every closure-rejected attempt is stamped false before restart, and
deterministic provenance failure cannot be overwritten by a positive closure
pass. Two end-to-end regressions plus real Claude Skeptic and follow-up review
approved. Previous
checkpoint: R4 escalation handle
correlation SHIPPED: check-ins and surfaced queued escalations carry their
typed immediate-parent handle, while live navigator deferrals use the active
run handle and legacy/no-run paths remain explicitly blank. Focused tests and
Claude follow-up review approved. Previous checkpoint: R3 skill-candidate
sweep ownership SHIPPED: a fail-closed per-workspace flock now spans scan,
paid extraction, and consumption; overlapping evolvers skip before reads while
failed extraction remains retryable. Real cross-process test and Claude review
approved after observability fixes. All R3 residuals are now closed. Previous checkpoint: R3 shared
learnable-outcome policy SHIPPED: curated cards and raw ledger rows now use one
neutral fail-closed predicate, and evolver no longer invents `status: done`.
Three-lens Claude review moved the first version out of the semantically wrong
decision-prior module; focused follow-up approved. Previous checkpoint: R3 runtime-honest
curator contracts SHIPPED: mandatory/optional outputs and dependencies are
distinct, every curator runs against an isolated card transaction, and only a
validated delta commits. Claude review found and fixed overwrite blindness,
ambient authorship checks, shallow rollback, and optional-dependency ambiguity;
follow-up approved. Previous checkpoint: R3 typed origin
ancestry contract SHIPPED: one optional-field `Origin` TypedDict now spans
task, run, recall, navigator, and thread-brain boundaries without changing the
plain-JSON runtime format. Three-lens Claude review caused the custom merge
helper and duplicate navigator payload type to be removed; follow-up approved.
Previous checkpoint: R5 PID-reuse-safe
ephemeral ownership SHIPPED: Docker labels and scratch-clone sidecars pair PID
with portable kernel birth tokens; legacy/ambiguous state remains conservative.
Critical cross-model false-deletion findings fixed; real Darwin ABI and M1
libproc call verified. Previous checkpoint: R5 durable run-reference
index SHIPPED: hashed O(1) lookup, lock-serialized legacy migration, resumable
fallback, explicit partial state, and import/prune/corruption/concurrency
coverage. Three-lens Claude review findings fixed; follow-up approved. Previous
checkpoint: R5 `test-safe.sh`
portability SHIPPED: the real full chunked suite now completes on macOS without
`taskset`, using the repo venv and BSD-compatible chunk handling; Linux keeps
CPU affinity. Shell probes cover both command shapes and CLI overrides. Claude
Skeptic review found one flag-order regression, fixed before checkpoint.
Previous checkpoint: R5 run-curation runtime
semantics SHIPPED: pure card construction is separated from explicit
maintenance/promotion, dependency failures cause structured transitive skips,
and the pure card is atomically durable before maintenance begins. Three-lens
Claude adversarial review completed: four accepted findings fixed
(shared metadata snapshot, maintenance provenance precondition, strict registry
lookup, precomputed provider map). Final full raw suite green. Previous
checkpoint: R5 portable-import
concurrency residual SHIPPED: process-global `MARO_MEMORY_DIR` mutation replaced
by an execution-scoped storage ContextVar, per-target import transaction lock,
and locked/atomic quarantine writes; deterministic different-target and
same-target thread races pass. Bonus M1 local-validator bake-off selected
VibeThinker-3B-4bit as the Apple Silicon reference: 1.83 GB peak, 14/14 bounded
eval, 8.2s exact-protocol average; 8-bit and 27B candidates rejected for this
role. A smallest-useful follow-up rejected 1.5B-4bit: 4/8 canonical cases and
12.5s/call, with low-confidence outputs that escalate rather than save paid
calls. Cross-model adversarial review completed 1/3 lanes (Minimalist); its two
findings fixed, Skeptic/Architect timed out empty after 10m and are recorded as
failed rather than approval. Full raw suite green; `test-safe.sh`'s Linux-only
`taskset` wrapper captured in BACKLOG. Previous: 2026-07-13 (later same day — full-day /goal arc CLOSED: **1.5
planning-depth shadow** and **1.6 /loop trace** below both SHIPPED/CLOSED,
plus the recursive-goal check-in mechanism (director.handle_escalation) and
R1 architectural cleanup (prefix registry unification, curator topo-sort,
skill_candidate consumer). Two full 3-reviewer adversarial-review passes ran
against all of it — R3 (5 bugs fixed, 3 residuals documented) and a closing
R4 capstone pass over the ENTIRE day's diff run via the real cross-model
`/adversarial-review` skill (3 more bugs fixed: escalation enqueue-failure
now surfaces to the operator instead of silently completing a dead chain,
check-in payload off-by-one, skill-candidate consumed-on-extraction-crash;
1 pre-existing architectural gap documented). Full detail in GOAL_BRAIN
Decisions 2026-07-13 and BACKLOG.md's R3/R4 sections. Full suite green
(169/169) throughout. Nothing else in the Actionable Stack was both
unblocked and ready without Jeremy's input. Previous: 2026-07-13 (Claude
Code /goal backlog-clearing session,
autonomous per standing directive, continued: **batch-2 `/adversarial-review`
SHIPPED** over the five-subagent merge chunk below (#14/#17/#18-residual/#25/#10)
— 3 codex reviewers (Skeptic/Architect/Minimalist) independently converged on
the same root bug (`MARO_LLM_MAX_RETRIES` silently overriding the hosted-free
adapters' deliberate `max_retries=0` fail-fast contract); fixed, plus two more
confirmed findings (hosted-free latency breaker missing on non-HTTP failures,
`resolve_run_dir` not resolving a resumed run's pre-resume `loop_id`); one
pre-existing gap (`maro resume` lacked structural serialization) was documented
for an owner decision, then shipped 2026-07-14 as a per-run nonblocking flock
with immediate refusal and operator notification. Picked up BACKLOG's deferred
low-severity item (i) (depth-cap tripwire
test coupling) meaning to add one test; investigation found the actual gap
is bigger: handle.py's two restart gates can never reach their own
`MAX_RESTART_DEPTH` cap within a single call (each is a single `if`, not a
loop), and the separate queue-based escalation-continuation path
(`director.handle_escalation` → `task_store` → `handle_queue.handle_task`)
has **no depth cap at all** — an LLM escalation that keeps choosing
"continue" recurses without bound. Pinned with a known-gap test
(`tests/test_escalation.py`), not fixed — design call on which layer owns
the check. Also corrected a stale BACKLOG #22 bullet (blank-slate skill set
draft list is fully resolved, nothing left to build). Full suite green
(169 items) throughout. Previous: 2026-07-13 (same day: **-5 #6
container-executor C4 mechanics SHIPPED** — merged the Opus dev-Mac burn-in prep into main (3-way conflict:
`worktree.sweep_stranded_clones` supersedes the old surface-only detection,
`tests/test_container_e2e.py` grew 4→15 real-docker tests, all pass live on
this box); C4-BOX real-goal burn-in stays Jeremy-gated (see -5 #6, BACKLOG).
Five worktree-isolated subagents (each merged individually, full suite green,
pushed) cleared: BACKLOG #14 (streaming-iterator `complete()` on the shared
LLMAdapter base), #17 (goal search in `maro viz`), #18-residual (`runs/<id>`
dir parity for the direct-CLI lane via shared `open_run`/`close_run` in
`src/runs.py`), #25 (Groq/Gemini hosted-free adapters + Tier 1b in
`step_exec.verify_step` — code-complete, inert without Jeremy's API keys),
and #10 (`local_max_tokens_for(model)` resolver from a real 45-call
empirical sweep). BACKLOG.md/BACKLOG_DONE.md reconciled to match (#10/#14/#18
archived with full context, #22-residual + hist-r2-02 checked off). Batch-1
`/adversarial-review` findings (prefix-tier leak, match-strip drift, silent
stream truncation) fixed same session; batch-2 review over this chunk is
next. Previous: 2026-07-13 (Opus session: **-5 #6 container-executor C4 in
progress** — §7 sandbox.py **retired** (unwired prototype, −1670 LOC) + burn-in
prep from the dev Mac: `docs/CONTAINER_BURN_IN.md` runbook +
`scripts/container-acceptance-probe.sh` hostile-goal harness. What remains is
box-side: run the workload under real docker, fill the go/no-go checklist, and
— Jeremy's call — flip. Stale-clone detection SHIPPED (surface-only; the
reclaim-empty design was REJECTED by adversarial review as unsafe on a
worker-controlled clone — RCE + can't prove "empty"); real-docker E2E scaffold
SHIPPED (box-only, skips in CI). See -5 #6.). Previous: 2026-07-12 (Opus parallel session: **-5 #6
container-executor C3 SHIPPED** — fence→mount-map translation
(`container_exec.build_mount_map`, pure + containment-aware dedup) + self-dev
scratch-clone (`worktree.provision_clone`/`merge_back_clone` riding the
serialized `_locked_merge`; live repo NEVER mounted rw — `--no-hardlinks`
throwaway clone, host-side `git fetch` merge-back). Full suite green.). Previous: 2026-07-12 (Sonnet execution session:
-5 #5 Verifier-synthesis Part B SHIPPED — B1 `Deliverable.shape` first-class field, B2
shape-conditional behavioral-probe MUST + waiver logging + timeout split,
B3 probe-env hardening (never probe with cwd=None + majority-inconclusive
confidence cap); live-verified with zero mocks against a real mid-tier
adapter; full suite green (166 files / 5692 tests); design doc closed to
`docs/history/2026-07-12-routing-and-probe-synthesis-design.md`; unblocks
`docs/VERIFY_LEARN_ARC.md` V0; see -5 #5 for full detail). Previous:
2026-07-12 (Opus parallel session, alongside Sonnet on -5 #4/#5
routing/probe-synthesis: **-5 #6 container-executor C1 + C2 SHIPPED** — C1
image+auth+doctor, C2 the wrap (`complete()` decision → `_run_subprocess_safe`
docker wrap + kill-by-name + stranded-container reaper); disjoint file set
(`container_exec.py`/`llm.py`/`heartbeat.py`/`step_exec.py`/`workers.py` vs
`intent.py`/`closure_verify.py`/`scope.py`); Codex adversarial review REJECT →
9 findings fixed same session. C3 = mount map + self-dev clone is next. See
-5 #6.). Previous: 2026-07-12 (Sonnet execution session: -5 #4
Routing Part A SHIPPED — needs_live_data classifier signal + capability
override close the Manti canonical-case routing gap, live-verified at 0.95
confidence with no `--lane` force; see -5 #4 for full detail). Previous:
2026-07-12 (git-history
privacy scrub completed by Jeremy in a parallel session — force-pushed
rewritten `main`/`factory`; this session reconciled all three local git
worktrees on the box, verified zero content loss). Previous: 2026-07-12
(Sonnet execution session: real `adversarial-review`
skill installed globally on this box + run against all 4 of this session's
commits per Jeremy's ask ["not assume we caught everything the first time"]
— 5 more real findings, all fixed same session; see -5 #3's "Adversarial
review — two passes" note for full detail). Previous: 2026-07-12 (same
session: post-handoff queue item -5 #3 depth-cap unification SHIPPED — see
-5 #3 for full detail). Previous:
2026-07-12 (same session: -5 #2 escalation file surface SHIPPED — see -5 #2
for full detail). Previous: 2026-07-12 (same session: -5 #1
supervision-convergence chunk SHIPPED — see -5 #1 and BACKLOG -1 for full
detail). Previous: 2026-07-12 (Fable-handoff audit session: queue staleness reconciled — **-4 Purgatorio is COMPLETE through r2**, -1 memory arc and 0 substrate trial no longer carry "current arc" labels; design-pass vehicles for the remaining 1.0 blockers land below as they ship this session. Context: top-tier-model access ends ~2026-07-13; this session front-loads design judgment so Sonnet/Opus sessions inherit execution-shaped chunks). Previous: 2026-07-09 evening (decision-cleanup session: BACKLOG #19 thread-arch decisions ALL RESOLVED by Jeremy + recursion decree — see GOAL_BRAIN Decisions 2026-07-09; new queue items **1.5** planning-depth shadow + **1.6** /loop trace below; verify→learn design decided as the next arc after 1.0). Previous: same day (concurrency-hardening arc COMPLETE — see **-2** below). Previous: 2026-07-07 (memory direction DECIDED by Jeremy — module + 3rd-party consideration; port + adapter-0 + contract tests shipped as chunk 1 of the new **-1. Memory module arc** below; bake-off is next). Previous: 2026-07-04 (overnight: MCP dispatch fixed, tier-a write fence, ancestry read-side, rung-4 call-record link. Morning: write fence ENABLED + live-proven (Jeremy's flip); ancestry write-side CLOSED; BACKLOG #9 validator ROI (`python3 -m validator_roi`); fetch unification (`fetch` tool). Midday: fence NARROWED per Jeremy — /tmp allowed + goal-declared paths widen fence (`FENCE_EXTENDED`), intent trumps; workspace scratch `~/.maro/workspace/tmp/`. Afternoon (AFK arc): docs refactor (three-species taxonomy, docs/history/, test-enforced frontmatter), dev-recall ghost-index rebuilt, BACKLOG full triage 810→~540 lines every-claim-verified, **memory decision brief delivered → `docs/history/2026-07-04-memory-decision-brief.md` AWAITING JEREMY** — that decision gates the next big chunk. See GOAL_BRAIN Decisions 2026-07-04. BACKLOG remaining: #0 mining passes + raw archive, #1 residual Bash write shapes (evidence-driven), #10 local_max_tokens tuning, #14 llm-adapter streaming (promoted, unblocked)).

Truth anchor: GOAL_BRAIN.md Threads. History: docs/history/ROADMAP_ARCHIVE.md.

---

## Active Queue

-6. **Holistic drift review (Jeremy, 2026-07-17) — do this in a CLEAN
   session, first thing.** After the delivery-loop/two-tone arc: "Might be
   time to do a wholistic review, and honestly see if we are on target, and
   if the drift moved us in a better direction towards our mountain we
   wanted to climb... or if we ended up on the wrong continent looking at a
   swamp." Method matters as much as verdict: cold-read the repo the way a
   stranger would — VISION, GOAL_BRAIN, MILESTONES, BACKLOG, DEFAULTS,
   docs/ — WITHOUT leaning on conversational backstory, and test (a)
   whether the recorded state alone carries the true state (his other
   observation: "what we've got 'in context' and what is just sort of...
   'in the repo'"), and (b) whether the accumulated arcs still climb toward
   the north star (self-improving autonomous agent; visible → reliable →
   replayable) or optimized locally away from it. Honest verdict wanted,
   including "wrong continent". Also carry in: his caution that the
   delivery-loop fixes should be "identifying the right pattern, as opposed
   to dialing in this specific example" — the review should check new
   surfaces against pattern-vs-example. And his framing on wrap (2026-07-17):
   "I think we've got something that works, great in some areas, just enough
   in others, and the bitter lesson trumps about half of what we're trying
   to do already... harness engineering is hard." So a third axis: sort the
   machinery by *what survives model improvement* — trust/visibility/data
   plumbing (delivery loop, records, run capture, auth) ages well;
   compensating-for-model-weakness scaffolding (prompt taxonomies, planning
   crutches, routing cleverness) is the half at risk. Name which half each
   major arc is in. Output: a short findings doc + proposed course
   corrections, decisions to Jeremy. Caveat: the session cannot be fully
   clean — the auto-memory index (MEMORY.md) is injected regardless. Treat
   that as instrumentation, not contamination: whenever a conclusion rests
   on a memory rather than something in the repo, SAY SO — each such case
   is one of the "in context, not in the repo" gaps this review exists to
   find. (Memory was pre-gardened 2026-07-17: 15 dead-arc files archived
   out of the index; a deeper distillation of memory + direction docs is
   queued to follow THIS review, using its findings — deliberately not
   done before it.)

-5. **Post-handoff execution queue (2026-07-12)** — *the ordered chunk list
   from the Fable-handoff session; every entry has a design doc or decided
   spec behind it — implementation sessions should not need to invent
   architecture (see `docs/IMPLEMENTATION_HANDOFF.md` for chunk discipline +
   model-tier guidance). Order:*
   1. **Supervision-convergence chunk — SHIPPED 2026-07-12** (r2 blocker #5):
      `deploy/systemd/{maro-heartbeat,maro-observe}.service` +
      `scripts/heartbeat-ctl.sh` deleted (ops-r2-04/docs-r2-04), README's
      Optional Services section already matched the posture (docs-r2-01
      half-shipped 2026-07-10); `skills/arch-platform.md` +
      `scripts/viz-ctl.sh` references fixed; runtime-box config
      (ops-r2-01/02): `heartbeat.autonomy` back to False,
      `scripts/host-check.sh`'s heartbeat-age check re-aligned 900s→7d
      (was firing a structural-noise FAIL/Telegram-page daily — details in
      BACKLOG -1). Full context + verification in BACKLOG -1.
   2. **Escalation file surface — SHIPPED 2026-07-12** (1.0 item (a)
      implementation): `notify.py` — `ESCALATION_FILE_EVENTS` (`escalation`,
      `backend_actionable`, `stranded_run`; `run_completed` excluded, it
      already has a durable home via `run_card.json`) write unconditionally
      to `output/escalations.jsonl` via `escalations_path()` +
      `locked_append` (`file_lock` house convention), independent of whether
      a `notify.command` lane is configured or succeeds — best-effort,
      never blocks emit. `doctor.py` now reports two rows: "Escalation file
      surface" (path + row count, always live) and "Escalation push lane"
      (the renamed notify.command/Telegram detection, unchanged logic).
      README Optional Services section + `docs/SUBSTRATE_INTEGRATION.md`
      (files table + Notify section) both document the new surface.
      6 new tests (`test_notify.py` ×5, `test_doctor.py` ×1), full suite
      green. Substrate notify contract unchanged — it IS the design.
   3. **Depth-cap unification — SHIPPED 2026-07-12** (backend-resilience
      ratification residual): three independently-drifted magic numbers —
      `MARO_MAX_CONTINUATION_DEPTH=4` (loop_post_step.py), hardcoded `< 3`
      ×2 (handle.py director-restart + closure-restart gates),
      `director_budget_ceiling=2` (loop_types.py, in-loop director replan
      budget — a distinct counter from continuation_depth, kept distinct,
      just given the same value) — unified to one new shared constant
      `loop_types.MAX_RESTART_DEPTH = 3` (majority value, all three sites
      now import/reference it; `doctor.py`'s status line updated to match).
      Fixes a real inconsistency: continuation re-enqueue previously
      tolerated one more pass (depth 4) than director/closure restart
      would ever reach (capped at depth 3) — now aligned. 4 new tripwire
      tests (`test_depth_cap_unified.py`, source-scan for stray literals)
      + stale numeric mentions fixed in `skills/arch-interface-routing.md`
      and `docs/ADAPTIVE_EXECUTION_DESIGN.md`. Full suite green (166).

      **Adversarial review — two passes.** Pass 1 (ad-hoc, before the real
      skill was installed on this box): 3 Sonnet subagents applying Maro's
      own `skills/code_review.md` discipline, one per chunk above. Found +
      fixed 2 issues (`cf31d4c`): host-check.sh duration-formatting/
      unit-mismatch bug (bash integer division truncated sub-day
      `MARO_HEARTBEAT_MAX_SEC` overrides to "0d", and age/threshold
      printed in mismatched units), and a self-defeating depth-cap
      tripwire test (aggregate `MAX_RESTART_DEPTH`-count assertion stayed
      green even when one of handle.py's two restart gates was silently
      reverted to a hardcoded value). Jeremy then had a real
      `adversarial-review` skill (cross-model: Claude spawns Codex
      reviewers) installed globally on this box (`~/.claude/skills/`, not
      present before this session) and asked for a **second pass against
      all 4 commits together** — explicitly not assuming pass 1 caught
      everything. It didn't: 3 Codex reviewers (Skeptic/Architect/
      Minimalist, "Large" tier — 21 files, 409+/245-) found 5 more
      real issues, all fixed same session (no high-severity findings;
      verdict PASS with mediums to address):
      - **host-check.sh accepted any malformed numeric env override
        silently** (`MARO_HEARTBEAT_MAX_SEC=abc` printed bash errors to
        stderr but still exited 0 / "ALL OK" — a monitoring false
        negative; live-reproduced). Fixed: `_require_num()` validates all
        4 numeric thresholds at the config boundary, exits 2 with a clear
        message on malformed input. Live-reverified: malformed input now
        fails loudly; valid overrides (including decimal
        `MARO_DAILY_USD_CAP`) still behave identically.
      - **`output/escalations.jsonl`'s "durable/unconditional" framing
        overstated what the code guaranteed** — `notify.py` swallows
        write failures (lock timeout, permission error) at debug level,
        and `doctor.py` only checked the parent dir existed, never that
        the file was actually writable (2 reviewers, independently).
        Fixed: `doctor.py` now checks real writability (`os.access`, not
        a live probe write — avoids contending with a real writer or
        polluting the log); write failures now log at `warning`; README
        + `docs/SUBSTRATE_INTEGRATION.md` clarified to say what's
        actually guaranteed (attempted unconditionally, not guaranteed to
        land on an fs error).
      - **docs/BACKEND_RESILIENCE_DESIGN.md left stale** — still described
        the pre-unification 4/<3/2 caps as current and the "three depth
        caps" question as open, after this session shipped the
        unification (2 reviewers, independently; a direct instance of
        this repo's own currency rule — CLAUDE.md: "if a doc states a
        fact you've just proven stale, fix it in the same commit"). Fixed:
        the table row, the "values unchanged" line, and the open-question
        line all updated to reflect what shipped.
      - **`MARO_MAX_CONTINUATION_DEPTH` remains an independent env
        override**, so an operator can still diverge continuation-pass
        depth from the restart gates' hard-locked `MAX_RESTART_DEPTH`
        (1 reviewer) — real observation, but the ratification was "one
        documented **number**" (the shared default), not "remove the
        pre-existing override knob." Documented as intentional in
        `loop_types.py`'s constant comment rather than changed.
      - **The depth-cap tripwire test is source-shape coupled, not
        behavior coupled** (1 reviewer, low severity) — deferred to
        BACKLOG (i) rather than built this session: low severity, single
        reviewer, current regex tripwire adequate for the regression
        class it was written for.
      Full suite green (166) after fixes. Unrelated finding surfaced by
      the full-suite run (not part of either review, not fixed here):
      `test_entry_points_reference_real_modules` fails pre-existing —
      introduced by the concurrent PyPI-publish-prep merge (`6befbfb`),
      the test's regex matches `[project.urls]` string values, not just
      `[project.scripts]` entries. Flagged to Jeremy, not folded into this
      commit (different file, different commit's fault).
   4. **Routing Part A — needs-live-data signal — SHIPPED 2026-07-12**
      (`docs/ROUTING_AND_PROBE_SYNTHESIS_DESIGN.md` Part A): `intent.py` —
      `needs_live_data: bool` added to the classifier's JSON schema
      (`_CLASSIFY_SYSTEM`), parsed in `_llm_classify` (absent/malformed
      fails open to `False`, a stray string `"true"` from a sloppier model
      still parses correctly); capability override in `classify()` (same
      template as the file-output override) forces `lane=="now"` +
      `needs_live_data` to AGENDA at `max(confidence, 0.8)`. Teaching
      examples fixed — the old NOW example ("what is the current BTC
      price?") was literally teaching the misroute; moved to AGENDA, NOW's
      example swapped to a stable-knowledge lookup ("what does HTTP 429
      mean?"). Heuristic fallback (no-LLM path) gets the small lexical
      approximation the design doc calls for: the `current/latest/today's`
      pattern flipped from a NOW-indicator to an AGENDA-indicator, gated by
      new `now_lane.live_data_routing` (default **ON**, including fresh
      installs — DEFAULTS.md row explains why this one differs from the
      no-silent-spend OFF-by-default posture: it decides lane *before* any
      NOW spend, not after). 8 new tests (`TestLiveDataOverride`,
      `tests/test_intent.py`, beside `TestFileOutputOverride`) — both
      directions, absent-field default, string-bool parsing, heuristic
      flip + its opt-out. **Acceptance verified live** (real cheap-tier
      adapter, no `--lane` force): the exact Manti sentence now classifies
      `agenda` at 0.95 confidence; stable-knowledge NOW lookups and
      creative generation still route `now`; the design doc's own BTC-price
      teaching example now correctly routes `agenda`. `docs/CAPABILITIES.md`
      Manti canonical-case section updated (Run 4: routing gap CLOSED, cost
      envelope is the only remaining gap before `verified`). Full suite
      green (166) — also caught + fixed one pre-existing, unrelated
      failure surfaced by the run: `docs/history/2026-07-12-git-history-
      privacy-scan.md` had `status: history` (invalid) instead of `status:
      record`, from the parallel git-history-scrub session; one-line fix.
   5. **Probe-synthesis B1 → B2 → B3 — SHIPPED 2026-07-12**
      (`docs/history/2026-07-12-routing-and-probe-synthesis-design.md` Part
      B — "probe honesty"; the closure design doc closed to history the
      same session both its parts shipped). Executed by Sonnet directly
      (not delegated to Opus as the doc's chunk-sizing suggested — same
      continuity call as Part A, live-verified below to confirm the
      simpler-model call didn't cost correctness).
      **B1** (`src/scope.py`): `Deliverable.shape: Optional[str]` —
      `document | runtime | data`, three values not two (a queried
      dataset/ledger is distinct from prose). `[shape: ...]` bullet
      annotation parsed in `_parse_deliverable_line` (parallel to
      `[preconditions: ...]`, either order, unrecognized values dropped
      rather than trusted); `_SCOPE_SYSTEM` prompt teaches the annotation +
      worked example. `closure_verify._deliverables_corroborate_runtime`
      now consults declared shape FIRST (`runtime` arms Signal 2 even with
      no keyword hint in prose; `document`/`data` suppress a prose keyword
      hit outright) and only falls back to the original keyword-regex
      inference for legacy/unshaped deliverables. Shape also surfaces in
      the closure-plan prompt's deliverables block.
      **B2** (`_CLOSURE_PLAN_SYSTEM`): item 4 rewritten from "prefer" to a
      shape-conditional MUST — any `[shape: runtime]` deliverable requires
      ≥1 behavioral probe (http/ws/process/browser), waivable only by
      setting `"behavioral_probe_waived": "<reason>"` in the plan JSON,
      which now flows into the `CLOSURE_VERDICT` captain's-log event beside
      `modality_distribution` (empty string when nothing was waived, not a
      missing key). The `<15s` speed rule was a prompt/code disagreement
      the design doc flagged (code already allowed `timeout_per_check`,
      default 30s) — split into static-checks-stay-`<15s` vs.
      behavioral-probes-get-the-real-budget, interpolated via a
      `__TIMEOUT_PER_CHECK__` token replace (not `.format()`, which would
      collide with the JSON schema's literal braces in the same string).
      **B3** (`verify_goal_completion`): (a) the check-execution loop no
      longer runs `subprocess.run(cwd=None)` when the full cwd-resolution
      chain (workspace_path → run-scoped ContextVar → project-slug dir)
      comes up empty — every planned check is instead marked
      `inconclusive`/`env_unresolved` without executing, so an unresolved
      cwd can't silently probe Maro's own launch directory and manufacture
      a confident wrong-directory verdict. (b) new confidence cap: when a
      negative verdict has checks_run>0, >half of them inconclusive,
      confidence≥0.7, AND `_fail_count==0` (no check cleanly failed —
      narrowed post-adversarial-review, see below; originally shipped
      without the fail-count guard), confidence is capped to 0.69 with a
      summary note — mirrors the `judged=False` tri-state gate immediately
      above it (same variable, same line: a clean fail is real mechanical
      evidence, never environmental noise, so it must never be capped below
      the demotion threshold; only a self-contradiction-driven downgrade
      diluted by environment noise gets capped). "Environment reasons"
      reuses `_check_outcome`'s existing inconclusive classification as-is
      (missing tool, permission denied, timeout, verifier-authored syntax
      error) rather than inventing a narrower subset — that taxonomy's own
      comment already frames every branch as "the verifier's own tooling
      failed," so no new classifier was needed.
      **Tests:** `tests/test_scope.py` (+9: annotation parsing incl.
      either-order/shape-only/unrecognized-value/markdown-listener);
      `tests/test_director.py` `TestDetectBehavioralGap` (+6: declared
      shape overriding/arming the keyword inference), `TestVerifyGoalCompletion`
      (+4: plan prompt carries shape, timeout token interpolation, waiver
      logged / logged-empty), new `TestProbeEnvHardening` (+5, beside the
      existing cwd-fallback tests: unresolved cwd never executes,
      false-positive downgrades to honest-unjudged, resolved cwd unaffected,
      majority-inconclusive caps confidence, minority-inconclusive does
      not). **Acceptance verified live** (real mid-tier adapter, no mocks):
      a `[shape: runtime]` server deliverable that was actually a
      print-only stub produced 2 checks including a real HTTP probe against
      it (the MUST firing, not just "prefer") and correctly verdicted
      incomplete; a genuinely unresolved cwd (no workspace_path, no
      ContextVar, no project) produced 3 inconclusive/`env_unresolved`
      checks and an honest `judged=False, confidence=0.1` "verification
      could not run" verdict instead of a false pass or false fail. Full
      suite green (`bash scripts/test-safe.sh`: 166 files, same count as
      Part A — B1–B3 tests landed in the existing `test_scope.py` /
      `test_director.py`, no new files; 5692 individual tests collected
      repo-wide) before commit. Unblocks `docs/VERIFY_LEARN_ARC.md` V0 (its
      stated hard dependency) and clears the wrong-cwd class of the
      2026-07-09 dogfood false-negatives (4/5 runs) going forward — the
      historical batch itself isn't retroactively re-verified, only new
      runs benefit.

      **Adversarial review — second pass, combined Part A + Part B diff.**
      Per Jeremy's standing "don't assume either pass caught everything"
      instruction (see item 3's two-pass note above for the first
      instance), the real `adversarial-review` skill (3 Codex reviewers —
      Skeptic, Architect, Minimalist — "Large" tier, `src/closure_verify.py`
      + `src/intent.py` + `src/scope.py` + their tests) ran against the
      combined `f0c63a1`+`155e4d9` diff. **Verdict: PASS with mediums** (1
      High, 2 Medium, 1 Low — all 3 reviewers converged on the same 3
      substantive findings independently, 0 disagreement, 0 hallucinated
      against live code — a positive deviation from this session's
      historical ~30-50% adversarial-finding hallucination rate). All 4
      code-bearing findings fixed same session:
      - **[High] B2's MUST was prompt-only, not enforced** — a
        `[shape: runtime]` deliverable with neutral failure-mode prose (no
        server/process/http keyword) and no logged waiver could close with
        only static checks and nothing would catch it; Signal 2's
        deliverable-corroboration gate only ever fires after a scope
        keyword hint arms it first. Fixed: new Signal 3 in
        `_detect_behavioral_gap` (`_any_declared_runtime_deliverable`) —
        an explicit `shape=="runtime"` declaration is authoritative on its
        own, independent of Signal 2's keyword gate; the logged waiver
        remains the only escape. 4 new tests.
      - **[Medium] `now_lane.live_data_routing` didn't gate the LLM-path
        override** — the heuristic fallback honored the flag, but
        `classify()`'s primary LLM-path override fired unconditionally,
        contradicting `docs/DEFAULTS.md`'s documented "flag OFF makes both
        paths inert" contract. Fixed: same `_config_get(...)` gate added to
        the LLM path. 1 new test (mirrors the existing heuristic-flip test).
      - **[Medium] Precondition preflight leaked the B3(a) cwd=None fix** —
        `_run_precondition_preflight` predated B3(a) and still fell back to
        `Path.cwd()` (Maro's own launch dir) for relative path-shaped
        preconditions when the verification cwd was unresolved — the exact
        wrong-cwd bug class B3(a) exists to eliminate, just in a sibling
        function B3(a) didn't touch. Fixed: same guard — relative paths
        with unresolved cwd now synthesize `inconclusive`/`env_unresolved`
        instead of probing the wrong directory; absolute paths and
        command-shaped preconditions (cwd-independent) unaffected. 3 new
        tests.
      - **[Low] A test's name overclaimed coverage** — the Minimalist
        (independently, Architect + Skeptic too) flagged
        `test_runtime_shaped_deliverable_keeps_signal2_armed` as exercising
        the pre-B1 keyword-inference path (no `shape=` passed to the test's
        `_intent()` helper), not the declared-shape path its name implied —
        it wouldn't have caught a regression in the thing it claimed to
        cover. Renamed to `test_legacy_keyword_inference_keeps_signal2_armed`
        with a docstring pointing at the real declared-shape test.
      - **Not a code fix — reviewed and confirmed already-accepted scope:**
        the heuristic fallback's `_LIVE_DATA_RE` only catches
        `current/latest/today` phrasing, so a named-place availability ask
        (the Manti canonical case itself) still falls through to NOW when
        the LLM path is unavailable. The design doc's own DECISION marker
        (line 70) explicitly scopes this as "a *small* lexical
        approximation... only because it must work with no LLM" — not an
        oversight. Added an in-code comment at `_LIVE_DATA_RE` citing the
        decision and noting 3 independent reviewers still flagged it, so
        the next reader doesn't rediscover the same non-bug.
      - **Deliberate self-correction surfaced by this pass, not by a
        reviewer:** narrowing the [Medium] confidence-cap trigger (item 5's
        B3(b) above) to require `_fail_count==0` meant one of Part B's own
        pre-existing tests
        (`test_majority_inconclusive_caps_confidence_below_demotion_threshold`)
        was asserting the now-corrected-away behavior — a real fail diluted
        by unrelated inconclusive noise used to get capped below the
        demotion threshold, silently protecting exactly the false-negative
        masking risk this pass's own reasoning (prompted by re-reading the
        Minimalist's concrete scenario, not a distinct finding) identified.
        Rewritten into two tests: one confirming a real fail is never
        capped, one confirming the genuine noise-only case (self-
        contradiction downgrade, zero clean fails) still is.
      11 new/updated tests total across the fix set (4 Signal 3, 1
      live-data LLM-path flag, 3 preflight cwd=None, 1 test rename, 2
      confidence-cap rewrite/addition); full suite green
      (`bash scripts/test-safe.sh`) before commit.

      **Adversarial review — third pass, scoped to the pass-2 fix commit.**
      Jeremy asked whether the pass-2 fixes themselves were worth
      reviewing; scoped to 1 Codex reviewer (Skeptic lens only — the fix
      diff is small/Medium-tier and the riskiest content is one judgment
      call, not structural or complexity concerns Architect/Minimalist
      would target) against commit `0621417` alone. Found 2 real, verified
      findings — both symmetric risks in the exact mechanisms pass 2 just
      changed, judged as accepted residual risk rather than further code
      changes (a fix would either require new verifier-LLM-judgment scope
      or reintroduce the external-taxonomy anti-pattern this file's own
      docstrings warn against — both out of scope for B1-B3):
      - Signal 3's waiver check (and the pre-existing B2 waiver convention
        it reuses) validates only that `behavioral_probe_waived` is
        non-empty, never its content — a pretextual waiver bypasses the
        MUST as easily as a genuine one. Pre-existing since Part B shipped,
        not introduced by pass 2; Signal 3 inherited the same convention.
      - The pass-2 B3(b) narrowing (`_fail_count == 0`) trusts ANY clean
        `outcome=="fail"` as real evidence, but a fail only proves a check
        executed cleanly, not that the check was relevant/well-written — a
        brittle irrelevant check now uncaps the cap exactly like a
        meaningful one would. Judged acceptable on an explicit
        asymmetric-cost argument (over-eager demotion costs one bounded
        `closure_restart`; a wrongly-suppressed real failure silently
        poisons `goal_achieved` — the worse failure mode this file exists
        to prevent). Both documented in-code at their exact decision points
        and captured in BACKLOG "Verifier synthesis as a deliverable" for
        the eventual full-BDD-loop pass that would actually resolve them
        (needs an LLM judge or a relevance signal neither exists today).
        No test/behavior changes this pass — comments only.

      **Known-gap pin tests (2026-07-12, Jeremy: "make sure those are
      documented to revisit [test against] later").** Comments/BACKLOG
      prose aren't discoverable or checkable the way a running test is —
      added 3 `test_known_gap_*` tests that assert TODAY's gap-exhibiting
      behavior explicitly, so each is a concrete artifact to flip (not just
      re-read) once its underlying gap is actually closed. New convention;
      extends the pre-existing in-code "known gap" phrase (`loop_report.py`,
      `handle.py`) into the test suite for the first time:
      - `test_director.py::TestDetectBehavioralGap::
        test_known_gap_pretextual_waiver_still_suppresses_signal3` (pass-3
        Finding 1)
      - `test_director.py::TestProbeEnvHardening::
        test_known_gap_irrelevant_fail_still_exempts_confidence_cap`
        (pass-3 Finding 2)
      - `test_intent.py::TestLiveDataOverride::
        test_known_gap_named_place_live_data_not_caught_by_heuristic`
        (pass-2 Finding 5 — same documented-but-untested-gap pattern,
        added for consistency as the answer to "anything else we should
        look at?"). All 3 pass against current code (proving the gaps are
        real, not hypothetical); BACKLOG bullets cross-reference the test
        names both directions.
   6. **Container executor — C1 + C2 SHIPPED 2026-07-12 (Opus); C3 → C4 next**
      (`docs/CONTAINER_EXECUTOR_DESIGN.md`; C4 = runtime-box burn-in, Jeremy
      adjudicates the flip). Clears r2 blocker #4. **C1** (image + auth +
      doctor) landed the container's *description*: `Dockerfile.executor`
      (CLI pin `2.1.207`), `src/container_exec.py` (the shared seam),
      `maro-bootstrap container-setup`, mode-gated `doctor` rows, DEFAULTS.md
      `## Executor / sandboxing` (4 keys), stale root docker artifacts deleted.
      **C2** (the wrap) made it run: `complete(executor=True)` — threaded ONLY
      from the real worker-executor seams (`step_exec` EXECUTE_SYSTEM ×2,
      `workers` ticket) — routes through `resolve_container_run` (off / not-
      executor / no_tools → host; docker → container; `on`+no docker → degrade
      w/ warning; `require`+no docker → refuse) and threads a `container_name`
      into `_run_subprocess_safe`, which wraps the inner `claude -p` in
      `docker run` (`build_run_command`) and kills by name (`docker kill`
      before killpg — killpg only reaps the docker client). Stranded-container
      reaper (dead `maro.owner_pid` label, label-filtered) wired into
      `heartbeat.stranded_state_sweep`, never touching a live run's or a
      3rd-party container. Minimal mounts (cwd rw + auth vol + configured ro
      extras). **Codex adversarial review (3 lenses) → REJECT, 9 findings
      fixed same session** (host claude-path→basename; auth uid match; sweep
      label-filter; `not no_tools` over-capture → explicit `executor` gate;
      cached docker→per-call probe; retry/cross-process name collisions;
      cwd=None→host; `--mount` colon-safety). ~65 tests, docker fully mocked.
      **C3 — mount map + self-dev clone — SHIPPED 2026-07-12 (Opus).**
      `container_exec.build_mount_map` (pure, containment-aware) translates the
      run's write fence → the docker mount list (cwd rw + goal-declared /
      write-fence-allow rw + `container_extra_mounts` ro; host /tmp + workspace
      root deliberately absent; missing rw roots skipped, never created), fed by
      a run-scoped `llm.set_default_container_rw_roots` ContextVar. Self-dev:
      when `container_configured()` + the fence dir is a git repo, the live repo
      is NEVER mounted rw — `worktree.provision_clone` (`--no-hardlinks`,
      no shared object inode) → work the copy → `merge_back_clone` host-side via
      `git fetch` + the SAME serialized `_locked_merge` extracted from
      `merge_back` (conflict → branch kept). Rides the `run_worktree` seam
      (`ctx.container_clone`, merged in finalize before worktree→project).
      Deletions allowlisted in the retention tripwire. **Codex adversarial
      review (3 lenses) → REJECT with consensus; 6 finding-classes fixed same
      session** — fail-open live-repo mount (clone-fail/docker-timing → fail
      CLOSED via config-intent gate + `set_container_suppressed`); doc-only mount
      exclusions (realpath + hard-reject workspace/tmp/live-repo); host-git RCE in
      the attacker-writable clone (`_sanitize_untrusted_git` + hooks/fsmonitor
      disabled); stale rw-roots ContextVar; clone data-loss on branch-switch
      (HEAD-based merge-back) + silent-success; partial-clone leak. Residuals
      (host-git hardening is defense-in-depth, crash-leaked clones, comma paths,
      real-docker E2E) documented for C4. Full suite green. **C4 mechanics
      SHIPPED 2026-07-13** (§ 7 **sandbox.py retired**, unwired prototype,
      −1670 LOC; `_DANGEROUS_PATTERNS` relocated to run_curation; `maro sandbox`
      CLI + `sandbox-audit.jsonl` retired; burn-in prep from the dev Mac:
      **`docs/CONTAINER_BURN_IN.md`** executable runbook + the hostile-goal
      acceptance harness **`scripts/container-acceptance-probe.sh`**
      [`plant`/`goal`/`check`/`clean`; deterministic parts self-tested]; then
      merged into main same day by the Claude Code /goal session, resolving a
      3-way conflict). **Stale-clone sweep SHIPPED** — the earlier
      surface-only `worktree.surface_stranded_clones` was **superseded** by
      `worktree.sweep_stranded_clones` (`CloneSweepResult`: recovered /
      removed_empty / preserved / skipped_live / skipped_young / surfaced),
      heartbeat-wired with a `stranded_sweep` report line surfacing all six
      counts (the recovered/removed_empty/preserved counts were silently
      swallowed in the first cut — fixed same merge). It still NEVER runs git
      inside a worker-controlled clone (the RCE invariant from C3 holds
      transitively via `merge_back_clone`'s existing sanitization) and only
      ever removes a clone it can prove is genuinely empty post-merge-back —
      the earlier blanket reclaim-empty design stays **REJECTED by
      adversarial review** for anything it can't prove empty. **Real-docker
      E2E tier SHIPPED for real** — `tests/test_container_e2e.py` grew from
      the original 4-test scaffold to **15 tests**, box-only (skip-in-CI),
      and all 15 pass live on this runtime box (docker reachable here, not
      just the Mac) — no longer just a scaffold. **C4-BOX burn-in RAN
      2026-07-14/15** (`docs/CONTAINER_BURN_IN.md` §5b): auth volume seeded,
      CLI pin 2.1.207→2.1.210, a 3-goal concurrency batch under `container: on`
      clean, go/no-go checklist filled. Surfaced + fixed live: file-shaped
      fence roots dropped from the mount map (`_mountable_rw_dir`); a
      **containment gap** (goal-declared host-secret path mounted rw) →
      `build_mount_map` now whitelists rw mounts to the workspace subtree +
      `write_fence_allow` (Jeremy: "do both" — tighten + reword probe to a
      deterministic `structural` mode; verdict CONTAINED); container `/tmp`
      ephemeral-per-step → per-run scratch bind (`run_scratch_dir`, 2026-07-15).
      **All that remains is the flip itself** — box `container: off→on` and the
      fresh-install default — explicitly Jeremy's call after reading §5b/§6.
      The arc is otherwise complete.
   7. **Portable-learning chunks 1–4** (`docs/PORTABLE_LEARNING_DESIGN.md`
      §7, §8 ratified 2026-07-12) — 1.0 scope-decree item (g).
      **Chunk 1 — migration runbook + doctor checks — SHIPPED 2026-07-12
      (Sonnet).** `docs/MIGRATION.md` (5a empty-new-machine + 5b
      merge-into-existing procedures verbatim from the design doc §5, plus
      the doctor-row pointers). `maro-doctor` gained 3 new rows, all
      informational (never a hard FAIL — a live running box legitimately
      has jobs/heartbeat/lock state; the supervision-convergence fix
      already established structural-noise FAILs on normal-operation state
      as a standing anti-pattern here, see item 1 above): **Config paths
      on this box** (`_scan_config_paths` walks the merged config, flags
      string values that are path-shaped — start with `/`or `~`, no
      whitespace, so a `notify.command` with args is never mistaken for a
      bare path — and don't resolve on this machine); **Stale machine
      state** (`jobs.json`, `heartbeat-state.json`, `telegram_offset.txt`,
      any `*.lock` — reports presence + a pointer to the runbook's delete
      step, never auto-deletes); **Memory index sync** (opens the
      `SqliteMemoryStore`, which is itself what triggers the designed
      self-heal catch-up/rebuild, then reports which happened — the check
      and the heal are the same action by design). 12 new tests
      (`test_doctor.py` — `TestScanConfigPaths` + 3 `TestRunDoctor`
      additions); `docs/INDEX.md` row added; full suite green.
      **Chunk 2 — provenance fields + `scrub_identifiers()` — SHIPPED
      2026-07-13 (Sonnet).** Pure-additive per the design doc's own framing
      ("unblocks both following chunks"): added `imported: Dict[str, Any] =
      field(default_factory=dict)` to the four rewrite-on-change dataclasses
      named in §3's implementation caveat — `StandingRule`/`Hypothesis`
      (`src/knowledge_lens.py`, hand-written `to_dict()` updated to include
      the new key; `from_dict()` needed no change, its existing
      declared-field filter already defaults absent keys via the dataclass
      default), `TieredLesson` (`src/knowledge_web.py`, serializes via
      `dataclasses.asdict()` everywhere — field addition alone was
      sufficient), `Skill` (`src/skill_types.py`, `skill_to_dict()` /
      `dict_to_skill()` module functions updated). The bug this closes:
      without a declared field, an `imported` key stamped onto a raw row
      would be silently dropped the first time any of these stores
      rewrites itself — confirmed by reading `_rewrite_rules()` /
      `_rewrite_hypotheses()` / `_rewrite_tiered_lessons()`, all of which
      round-trip through `to_dict()`/`asdict()`, not the raw dict. Also
      added `scrub_identifiers()` to `src/secret_scrub.py` (same module as
      `scrub()`, per its own single-source-rule founding constraint):
      redacts the caller's `$HOME` path + derived username + hostname with
      stable tokens (`[HOME]`/`[USER]`/`[HOST]`, word-bounded so e.g.
      username "jeremy" doesn't clobber "jeremyville"), plus a
      caller-supplied deny-list (emails/handles) redacted to
      `[REDACTED]` — deny-list is assembled by the exporter from config +
      environment, nothing hardcoded here. Explicitly NOT anonymization —
      mechanical redaction of *known* strings; the honesty framing from
      §4 ("we do not claim mechanical anonymization... a pack is a letter,
      you proofread letters") is preserved in the docstring for chunk 3 to
      cite verbatim. 14 new tests (`tests/test_secret_scrub.py` — new
      file, `TestScrub` baseline + `TestScrubIdentifiers`;
      `tests/test_promotion_cycle.py` — 4 provenance round-trip tests for
      `StandingRule`/`Hypothesis`; `tests/test_knowledge_web.py` — 2 for
      `TieredLesson`; `tests/test_skills.py` — 2 for `Skill`). No new
      config defaults introduced (provenance fields are structural, not a
      runtime knob; `scrub_identifiers()`'s deny-list is caller-assembled,
      not a config key) — nothing to register in `docs/DEFAULTS.md` this
      chunk. Full suite green (168/168 files, incl. the new
      `tests/test_secret_scrub.py`).
      **Chunk 3 — `maro-pack export` + `seal` — SHIPPED 2026-07-13
      (Sonnet).** New `src/pack.py` (+ `maro-pack` entry point in
      `pyproject.toml`, both `[project.scripts]` and the flat-module
      `py-modules` census — `tests/test_packaging.py` catches drift on
      either). `export` gathers Class C (skill records, standing rules,
      hypotheses, long-tier lessons — medium-tier/knowledge/playbook/runs
      all opt-in per §2b) + Class A (`skills/*.md`, `personas/*.md`) from a
      workspace, applies `secret_scrub.scrub()` then the chunk-2
      `scrub_identifiers()` to every string, and writes an UNSEALED
      `<name>.maropack.tar.gz` (`pack.json` v1 + `REVIEW.md` +
      `artifacts/<workspace-relative-path>`) plus a loose
      `<name>.REVIEW.md` companion — the actual thing a human reads, since
      nobody should have to untar an archive to review it. Empty JSONL
      artifacts (e.g. a young workspace with zero standing rules yet) are
      skipped rather than shipped as noise. `seal` requires an explicit
      confirmation (interactive prompt or `--yes`; refuses on unreadable
      stdin, e.g. captured test runs) and stamps `review.human_reviewed`
      + `reviewed_at` + `review_manifest_sha256` — the hash is taken from
      the loose `REVIEW.md` companion if present (so pre-seal human edits
      count), then the archive is rewritten with the stamped manifest;
      re-hashing the archived `REVIEW.md` later against the stamped value
      is how chunk 4's import will detect post-seal tampering. `inspect`
      (read-only manifest dump) shipped alongside as a low-cost companion
      to export/seal, not separately scheduled but cheap enough to include.
      One new config default: `pack.export_denylist` (`[]`) — extra
      emails/handles to redact, layered on top of auto-derived
      `$EMAIL`/`$GIT_AUTHOR_EMAIL`/`$GIT_COMMITTER_EMAIL`/`git config
      user.email` (§4: "assembled from config + environment, never
      hardcoded"), registered in `docs/DEFAULTS.md`. Honesty framing from
      §4 preserved verbatim in the module docstring, `REVIEW.md` header,
      and `docs/MIGRATION.md`'s new "Sharing a curated learning pack"
      section: mechanical scrub + mechanical identifier redaction + a
      mandatory human review gate — not mechanical anonymization; a pack
      is a letter, you proofread letters. 29 new tests
      (`tests/test_pack.py` — manifest shape, default vs. opt-in artifact
      inclusion, secret + identifier scrubbing, sha256 integrity, seal
      confirm/refuse/tamper-detection plumbing, CLI export→seal→inspect
      round trip). `docs/INDEX.md` + `docs/MIGRATION.md` updated. Full
      suite green.
      **Chunk 4 — `maro-pack import` + `adopt` — SHIPPED 2026-07-13
      (Sonnet). Closes the loop — minimum 1.0 slice (chunks 1–4) complete.**
      Extended `src/pack.py` (no new module, same CLI surface). `import`
      gates hard before touching anything: refuses a newer `pack_format`
      than this install supports outright (never best-effort a format it
      doesn't understand on trust-bearing data, §6), refuses an unsealed
      pack unless `--allow-unreviewed` (the self-to-self-transfer escape
      hatch), and refuses if the archived `REVIEW.md` no longer hashes to
      the sealed `review_manifest_sha256` — chunk 3's seal-time hash is
      what makes this tamper check possible. Trust demotion per §3's
      arrival-trust table, applied per artifact class: standing rules
      demote to `Hypothesis` with `confirmations`/`contradictions` reset to
      0 and `source_lesson_ids=["imported:<pack>/<rule_id>"]` (exact-string
      rule content already known locally is skipped, not double-counted);
      already-hypothesis rows get the same reset — contested-by-birth
      applies uniformly, not just to the rules-demotion path; lessons
      always land in MEDIUM tier regardless of origin tier, score capped
      at 0.5, `sessions_validated=0`, and critically `last_reinforced` is
      stamped to *import* time not preserved from the origin — decay math
      (`knowledge_web._days_since`) reads `last_reinforced`, so this is the
      one field that actually implements "a 3-month-old import isn't born
      half-decayed" rather than just asserting it in prose; skill records
      import with stats moved to `imported.claimed_use_count` /
      `claimed_success_rate`, local counters reset to 0/1.0/closed-circuit,
      content-hash-identical rows skipped via the existing Phase-14
      `content_hash` machinery (no new dedup logic needed — reused as-is).
      Skills/personas (`.md`) never land live, full stop: always
      quarantined to `imports/<label>/`; a same-name/different-content
      collision leaves the local file untouched and appends a note to
      `imports/<label>/CONFLICTS.md` (local always wins — adoption is
      editorial, not automatic, same posture as `maro-import`). Classes
      chunk 3 produces but chunk 4 doesn't merge into a live trust-bearing
      store (`knowledge_nodes`/`knowledge_edges`/`playbook`/`run_artifact`)
      quarantine to their natural workspace-relative path — kept distinct
      from genuinely *unrecognized* classes (the §6 forward-compat seam for
      future additive `pack_format` growth), which quarantine under
      `imports/<label>/unknown/` instead. `adopt <label> [items... | --all]`
      copies from quarantine into the live workspace with a provenance
      header (`imported_from`/`adopted_at`) stamped into the file's
      frontmatter, never overwrites an existing live file of the same
      name, and records an audit row. Both `import` and `adopt` append to
      the same `memory/imports.jsonl` ledger `maro-import` already uses
      (distinguished by an `action` field: `pack_import` vs. `adopt`) —
      one audit trail for all provenance-changing operations, not a second
      ledger to remember. `--dry-run` on both, matching `maro-import`'s
      convention. No new config defaults (nothing here is a tunable knob).
      41 new tests (`tests/test_pack.py` — `TestImportPack` covers every
      row of the arrival-trust table plus the three refusal gates plus
      collision/quarantine/dry-run/audit-row behavior; `TestAdopt` covers
      named/stem/`--all` adoption, never-overwrite, missing-label/missing-
      item refusal, dry-run, audit row; a CLI round-trip test drives
      export→seal→import→adopt entirely through `main()`). Full suite
      green. `docs/MIGRATION.md`'s pack section and `docs/INDEX.md` updated
      to reflect the closed loop. Design doc §7's "recommend all four" is
      now the shipped state, not a recommendation.
      **Adversarial review across all 4 chunks — SHIPPED 2026-07-13
      (Sonnet, 3 Codex reviewers: Skeptic/Architect/Minimalist).** Neither
      this chunk-1-4 span nor any individual chunk had received a dedicated
      review before this pass. 3 high-severity findings with unanimous
      3-lens consensus, all confirmed real and fixed: `--target` wasn't
      honored by the trust-bearing writers (rules/hypotheses/lessons/skills
      wrote through global `$MARO_MEMORY_DIR` helpers, not the resolved
      target — initially contained via `_memory_dir_override()`, then replaced
      2026-07-13 with the concurrency-safe `memory_dir_context()` ContextVar
      plus a per-target import transaction lock); sealed-pack artifact *contents* were never
      integrity-checked on import (only `REVIEW.md`'s hash was, so
      post-seal artifact tampering went undetected — fixed with per-
      artifact sha256 verification before any mutation); manifest
      `relpath`/label were untrusted path components with no traversal
      guard (fixed via `_safe_relpath`/`_safe_label` at the single
      dispatch choke point). Plus 6 medium/low fixes: malformed rows mid-
      import could leave partial unaudited state (now contained per-row,
      `malformed_skipped` outcome); incoming provenance was discarded
      instead of nested under `imported.original_provenance` per design
      doc line 168; `adopt()`'s never-overwrite check had a TOCTOU race
      (now atomic `O_CREAT|O_EXCL`); `secret_scrub` scrubbed dict values
      but not keys; `maro-import`'s audit rows lacked an `action` field
      that `maro-pack`'s already had; imported skill records kept origin
      tier instead of resetting to `provisional` (now consistent with
      every other contested-by-birth field, original tier preserved under
      `imported.original_tier`). One medium finding — artifact filenames /
      manifest path strings aren't identifier-scrubbed — deferred as a
      documented known-gap (BACKLOG): fixing it correctly requires a
      filename-rewrite design decision that touches how `adopt()` derives
      live filenames from quarantined names, not something to rush into
      the same pass. Full 169-file suite + `tests/test_pack.py` (57) green
      after fixes. Full verdict + Lead Judgment:
      `output/adversarial-review-2026-07-13-portable-learning.md`
      (gitignored, box-local).
   8. Opportunistic riders: time-blindness first slice + perspective
      end-user seat (BACKLOG Vision vehicles, sized 2026-07-12).
   9. **Verify→learn arc V1–V5** (`docs/VERIFY_LEARN_ARC.md`) — **SHIPPED IN
      FULL 2026-07-14** (V1–V5 + adjudication run on the box's 71 divergences,
      `lesson_inject` enabled by Jeremy; see the 2026-07-14 checkpoint at top).
   *Still Jeremy-gated (not in the queue): evolver production hours (rides
   live batches), #24 model-route session (in progress), container flip
   (C4-BOX burn-in complete — flip is a one-line call, see -5 #6).*
   *DONE 2026-07-15 (Jeremy): PyPI publish — **v0.8.0 shipped**, "0.8 instead
   of 1.0, my call"; 1.0 relabeled "initial public release", later, NOT a
   work gate (GOAL_BRAIN Decisions 2026-07-15 — items formerly framed "for
   1.0" stand on their own priority now).*
   *DONE 2026-07-12: git-history privacy review — scrubbed the work email +
   employer strings from all history via `git filter-repo`, force-pushed;
   0 on every surface, verified from a fresh clone. See
   `docs/history/2026-07-12-git-history-privacy-scan.md`.*
-4. **Purgatorio pass — DECREED 2026-07-09, gates 1.0 completeness** — *Jeremy: the pre-1.0 retrospective audit; "what might be missing or neglected that is assumed to be working."* Seven eyes sequenced by yield (ops census → data health → backward archaeologist → docs coherence → code-vs-spec+security+standardization → external landscape → forward historian), shared findings format, rolling reconciliation every 2-3 eyes, verify-before-fix on the audit's own output. Full design: `docs/PURGATORIO_AUDIT.md`. Read-only — the queue below keeps moving; only new arcs wait for reconciliation. The final reconciliation re-triages the -3 list. **COMPLETE 2026-07-10 (r1 + r2):** r1 all seven eyes + FINAL reconciliation (`docs/audit-2026-07/RECONCILIATION.md`, 82 findings → 14 super-findings → 9 blockers); r2 re-run decreed by Jeremy same week (`docs/audit-2026-07-r2/RECONCILIATION.md`, FINAL 2026-07-10) — every r1 finding re-verified by live probe (34 resolved, no regressions), 23 new findings (41/42 adversarially confirmed), **final 1.0-blocker list cut 9 → 6**: evolver zero production hours (SF-1, narrowed), pre-verdict lesson extraction (data-r2-01 — since SHIPPED 2026-07-10, see BACKLOG), git-history personal-data review (Jeremy-gated), containerized-executor design pass (arch-r2-01 — vehicle below), README Optional Services / supervision-story convergence (docs-r2-01, half-shipped), PyPI publish act (Jeremy's act at tag time). **BLOCKER LIST DISSOLVED 2026-07-15 (de-1.0 decree — GOAL_BRAIN Decisions):** of the 6 — pre-verdict lesson extraction SHIPPED 2026-07-10; git-history RESOLVED via the 2026-07-12 filter-repo rewrite; containerized-executor design pass DONE (C1–C4 + burn-in, -5 #6); README/supervision convergence SHIPPED 2026-07-12 (-5 #1); PyPI publish DONE at v0.8.0 (Jeremy 2026-07-15). The one live remainder, **evolver production hours (SF-1)**, stands as its own open item on its own merits — nothing is "1.0-blocked" anymore; 1.0 = "initial public release", later, not a gate.
-3. **1.0 installability arc — OPENED 2026-07-09** — *Jeremy: "I'd like to work us towards a real 1.0, then we can refine some of these additional capabilities."* Gap analysis (this session): the backlog is mostly polish; the real 1.0 gaps were install/first-run, safe defaults, and the honest success-rate number. **Shipped today:** (1) de-OpenClaw'd first-run surface — `maro-doctor` now checks Maro's own two-tier config first (openclaw.json demoted to optional legacy row), reports per-backend LLM availability, telegram → non-fatal "Notification channel" row, `interrupt.py` fallback path de-openclaw'd; (2) safe-by-default flips — `budget.per_run_usd` 5.0 / `budget.daily_usd` 25.0 hardcoded defaults (fresh installs were UNCAPPED; 0/null = explicit opt-out; box overrides 2.0/10.0 unchanged) + `validate.write_fence` default ON; both in docs/DEFAULTS.md, census tripwire now resolves `from config import get as X` aliases (was blind to them — 6 undocumented keys surfaced+documented); (3) `maro-bootstrap install` writes a commented starter `~/.maro/config.yml` (backend order, caps, notify — never overwrites); (4) **docker clean-machine trial ran — first install ever attempted off this box — and found pip packaging had NEVER worked** (flat src/ layout invisible to `packages.find`: pip "succeeded" installing zero modules, every entry point ModuleNotFoundError; masked locally by PYTHONPATH=src). Fixed: explicit py-modules list (139) + `tests/test_packaging.py` census; pyyaml promoted to mandatory dep (without it config.yml was silently ignored). Post-fix trial: pip → bootstrap → doctor → goal all behave on a cold machine; E2E goal via mounted claude CLI lane PASSED — cold container, real goal → agenda lane, status=done, goal_achieved=True, correct artifact on disk (details + residuals in BACKLOG "1.0 install trial residuals"). **Remaining for 1.0:** (a) escalation-channel default — **DESIGN DECREED 2026-07-12 (Jeremy — GOAL_BRAIN Decisions): the substrate LLM go-between IS the official escalation surface** (the existing `notify.command` contract is the design, not a stopgap); a durable escalation FILE surface ships unconditionally (rides run-visibility); no beacon machinery; doctor just reports which surface is live. Implementation = one Sonnet-sized chunk (escalation file surface + doctor row + README posture) — **SHIPPED 2026-07-12, see -5 #2**; (b) done-vs-achieved analysis over the ~68-run verdict corpus — the honest success number decides whether 1.0's gap is packaging or closure quality; (c) BACKLOG install-trial residuals (skills packaging, service-template location, clean no-backend error, smoke-test honesty); (d) README/quickstart — first-run revamp DONE 2026-07-09 (1d0707f: installed-entry-point quickstart, maro-doctor step, spend-cap/write-fence posture, seeded-config docs); re-touch only if (a)–(c) change the surface. **Scope expanded 2026-07-09 evening (Jeremy: "learning and sharing needs to be part of the official first release" — decree in GOAL_BRAIN, full items in BACKLOG "1.0 launch content + learning/sharing"):** (e) default personas + skills via a research-orchestration run (after (a)–(d), before release; link-farm first, swipe-not-deps, orchestrator builds the gaps); (f) self-learning involved in that build-out (first real consumer of the verify→learn arc; the crystallization audit = the honest self-learning number for launch); (g) portable/shareable learning design + migration path (maro-import/JSONL-truth/Stage-5-regenerable/secret_scrub are the doors; hive-mind explicitly out, opt-in someday); (h) backend-error resilience + auto-resume (token/rate limits, /login-class auth expiry, resume of interrupted work — "a sharp edge that will kill an end user's enthusiasm"; BACKLOG (h) has the doors + known evidence). **This list is provisional pending Purgatorio reconciliation (see -4).** **STATUS SWEEP 2026-07-09 (autonomous /goal session):** (a) SKIPPED by decree (Jeremy involvement needed); (b) DONE (`docs/history/2026-07-09-done-vs-achieved.md`); (c) DONE; (d) DONE; (e) SHIPPED + REMAINDER CLOSED (13 personas + 12 skills as maro_assets package data, census-tripwired; survey + details in BACKLOG (e) — incl. the gitignore landmine that had silently unshipped every skill; adversarial-review decree satisfied by run-4's graduated code_review skill); (f) **COMPLETE** (5/5 learning-ON dogfood runs produced verified-correct deliverables; 3 skills graduated into the ship set — 3/12 shipped skills are Maro-built; 28 dogfood-born lessons crystallized; honest verdict-noise number: closure false-negatived 4/5 good runs on wrong-cwd/privilege verifier errors while the adversarial layer went 2/2 — full scorecard in BACKLOG (f)); (g) DESIGN SHIPPED (`docs/PORTABLE_LEARNING_DESIGN.md`, 8 provisional decisions await Jeremy); (h) MINIMUM 1.0 SLICE SHIPPED (llm_errors classifier + run-dir checkpoints w/ in-flight marker + stranded sweep + `maro resume` + `doctor --live`; auto-resume deliberately post-1.0; 9 provisional decisions await Jeremy). Purgatorio: **ALL SEVEN EYES COMPLETE + FINAL reconciliation** (`docs/audit-2026-07/RECONCILIATION.md` — 82 findings → 14 super-findings; final 9-blocker list: self-improvement zero-hours story, verdict-blind learning stores, user/ personal-data shipping, no-sandbox-vs-SECURITY_MODEL, bootstrap heartbeat crash-loop unit, test-junk purge [resolved], PyPI/version, empty CI, README headline repositioning; eye 7 added SF-13 record-boundary systemic fix + SF-14 release amnesia; consolidated decision brief: `docs/history/2026-07-09-decisions-for-jeremy.md` buckets A–E). Full suite green (152 files) after all flips. Pre-commit adversarial review (8 finder angles, verified) then hardened the same surface: budget gate coerces before truthiness and fails CLOSED to the defaults on malformed values (a typo can't silently uncap), `llm.detect_backends()` added as the single source of truth doctor consumes (doctor previously hand-mirrored build_adapter and missed credentials-.env keys / CLAUDE_BIN / codex / backend_order), doctor's output-dir check no longer mkdirs-then-asserts-exists, channels/notify checks fail on machinery errors instead of masking them, starter template interpolates the real cap constants, packaging census also fails if src/ grows a subpackage py-modules can't express. **ARC CLOSED 2026-07-15 (de-1.0 decree):** the work this arc named is done or stands on its own — v0.8.0 published (Jeremy: "0.8 was the 1.0 bar"); "1.0" relabeled "initial public release", later, deliberately unpinned, and **no longer a prioritization line** (Jeremy: "we did that work regardless of name, and the line is being arbitrarily held now"). Open residuals from (a)–(h) live in BACKLOG under their own names/priorities (auto-resume, install-trial residuals, portable-learning decisions, evolver production hours).
-2. **Concurrency-hardening arc — COMPLETE 2026-07-09** — *Jeremy 2026-07-08: "make things more concurrent friendly"; plan approved with one edit: worktree isolation ships in-arc, "not just defer and half fix this issue".* Multiple concurrent runs (cross-process AND in-process threads) are now safe by construction; full design in `skills/arch-platform.md` § Concurrency Model, rules in `docs/CODING_NOTES.md`. Four commits, each suite-green: **Phase 1** (run-dir → ContextVar, `copy_context().run` at every thread fan-out, `atomic_write`); **Phase 2** (97f2235) file_lock flipped fail-open → **fail-closed** (`FileLockTimeout`, 30s deadline, `MARO_FILELOCK_FAIL_OPEN=1` hatch) + `locked_rmw` + every known unsafe writer fixed (memory_ledger, evolver_store, knowledge_web, background, orch_items NEXT.md RMW — the heartbeat-vs-run race, loop_finalize preflight); **Phase 3** (b923a98) admission gate: `acquire_project_slot` flock held for run lifetime closes the set_loop_running TOCTOU — busy → `refused_busy` naming the holder (heartbeat reverts item to TODO; `--wait N`/`loop.admission_wait_s` opt-in polling; NOW lane ungated), in-process siblings SHARE the slot (mission fan-out), pidfile singletons for heartbeat/scheduler (`src/proc_lock.py`); **Phase 3b** (31f2844) `src/worktree.py` — parallel fan-out steps each run in a private git worktree with serialized merge-back (conflict → step blocked, branch preserved, never silent loss) + opt-in `loop.busy_policy: worktree` for cross-run isolation (conflict → run `partial`). Live-proven on-box: cross-process refused_busy in 1.3s with correct holder info. Explicit non-goals: model-lane contention (accepted 2026-07-02), cross-worker constraint semantics (BACKLOG "Concurrent-loop interaction" — flagged follow-up).
-1. **Memory module arc — COMPLETE 2026-07-08** (residuals: fastembed lane stays gated, brief §8 #2–#6 ride along) — *(Jeremy 2026-07-07, on the decision brief: memory becomes a module; consider pre-existing offerings before building our own; "maintainability over cleverness"; our crystallization engine PRIMARY, 3rd party SECONDARY storage+retrieval — full decree in GOAL_BRAIN Decisions 2026-07-07).* **Chunk 1 SHIPPED 2026-07-07:** `src/memory_port.py` (MemoryStore protocol — 5 verbs, hierarchical slash-path scopes via `visible_at()`, invalidate-never-delete, `format_block`) + `src/memory_jsonl.py` (adapter-0: event-sourced single-JSONL reference impl, token-overlap ranking, no deps — doubles as the "our own" bake-off candidate) + `tests/test_memory_port.py` (24 contract tests, parametrized `ADAPTERS` hook — the suite IS the spec; candidate adapters add one entry and run identically). Production `recall()` callers deliberately NOT rewired — gated on bake-off verdict. **Chunk 2 bake-off COMPLETE 2026-07-07** (`docs/history/2026-07-07-memory-bakeoff.md`, full dossiers + trial reports inside): round 1 paper screen (3 source-level dossiers, 4 decisive claims hand-verified) eliminated TencentDB (invalidate structurally impossible — 3 hard-delete paths; Node-22 sidecar; local embedding deliberately unreachable; postinstall patches host OpenClaw — CAUTION); round 2 live trials (real adapters in `bakeoff/`, sandboxed venvs): **Mem0 24/24 and Graphiti 24/24 on the contract — and both lost anyway**: ~230 and ~330 of the lines doing the port's real semantics were OUR shims, frameworks reduced to storage pass-throughs, plus live disqualifiers (Mem0 embedded-qdrant single-client lock = no concurrent processes; falkordblite leaks detached redis-servers — ~150 orphans reaped, box verified clean). **VERDICT 2026-07-07 (Jeremy): steal-and-build** — "take the strengths we're looking for from all 3 and put them together"; embed in-repo (port is the module boundary; `memory_*` imports stdlib-only so extraction stays a copy). Pedigree recorded in the bake-off doc. **Chunk 3 SHIPPED 2026-07-07: adapter-1 = `src/memory_sqlite.py`** (~300 lines stdlib sqlite3+FTS5): JSONL event log stays SOURCE OF TRUTH (same file format as adapter-0 — the two stores are interchangeable on disk, proven by test), SQLite is a rebuildable index with `schema_meta` versioning + ghost-proofing (deleted index rebuilds; shrunken log resyncs — the dev-recall lesson, test-enforced); Graphiti's bi-temporal columns (created/expired transaction time + valid/invalid event time); Mem0's history = the event log itself. **Multi-process contract test added** (two processes, one store — the bar Mem0's embedded qdrant failed; both adapters pass). Bench: 1.0ms/append, 1.3ms/recall, 1.5ms cold reopen (trial adapters: hundreds of ms–seconds/op). fastembed+sqlite-vec semantic lane stays GATED on BM25 measuring insufficient. **Chunks 4–5 SHIPPED 2026-07-07/08 — built BY MARO ITSELF via /goal dispatch (Jeremy: "implement this as a /goal run"), verified + hardened by Claude Code:** goal 1 → `src/memory_quality.py` retrieval-quality instrument (hit@1/hit@5/MRR/latency, jsonl vs sqlite, report to output/memory_quality/; run: `PYTHONPATH=src python3 -m memory_quality`); verification caught a silent 50-item cap → `--limit` flag, full-corpus numbers in BACKLOG (sqlite wins hit@1 63.6% + latency 3.2ms vs 15.6ms; LOSES hit@5 77.9% vs 86.7% to token-overlap — BM25 tuning lead, suspects noted). Goal 2 → worker recall slice (brief Phase 1 + §7): `src/memory_bridge.py` (incremental lessons→SqliteMemoryStore ingest at `~/.maro/workspace/memory/module/`) + director wiring behind `memory.worker_slice` (DEFAULT OFF everywhere; off = byte-identical prompts) injecting capped top-K recall + parent goal_brain into worker context, with WORKER_SLICE_INJECTED captains-log event + per-worker token fields for the A/B. Goal 2 hit adapter timeout at step 7/10 (committed core, left thread-scope/goal-brain half uncommitted — completed in verification); verification also fixed: offset sidecars littering the crystallization dir → offsets live in store schema_meta; random item ids → deterministic sha1 (re-ingest idempotent, live-proven 414 items/second-run-0); offset key by basename → resolved path (three lessons.jsonl files were clobbering each other). **§7 A/B batch 1 RUN 2026-07-08** (`scripts/worker_slice_ab.py`, in-process flag patch — shared config never touched; record: `docs/history/2026-07-08-worker-slice-ab.md`): 2 missions × 2 arms, slice injected 4/4 B-workers (5 items, ~1k chars). Closure signal favors slice (m1: A stuck, B done); m2 token delta confounded by review-loop variance. n=2 = directionally positive, NOT conclusive. Flag stays OFF; next batch before any flip. Also shipped same day: paraphrase query lane in memory_quality (self-retrieval was rigged toward lexical overlap; fair lane: sqlite-fts5 wins ALL metrics, both adapters ~15% hit@5 on paraphrase = fastembed-gate evidence with adversarial-by-construction caveat) + goal/-scope trap removed + trust clamped [0,1]. Persona-panel retrieval round explicitly skipped (structural grounds decided; it's the tiebreaker if contested). Brief's §8 points #2–#6 ride along. **§7 A/B COMPLETE 2026-07-08 (batches 1+2 pooled, 16 clean runs, 8/arm — full record + methodology-warts log in `docs/history/2026-07-08-worker-slice-ab.md`):** closure 8/8 B vs 7/8 A, blocked workers 0 vs 1, median tokens-in −29%, wall −17%, review-loop exhaustions balanced 10v10 (token delta NOT exhaustion-explained this time); m2 (mission nearest the store's lesson content) favors B in all 3 pairs at −38..−40%; m3 cells low-weight (workers wrote repo files; later runs saw earlier artifacts). **Verdict: recommend flip ON — FLIPPED by Jeremy 2026-07-08, now the hardcoded default (off = `memory.worker_slice: false`, byte-identical path, test-enforced). Rider decree same day: every config default documented with reasoning + flip effects in `docs/DEFAULTS.md` (census tripwire `tests/test_defaults_doc.py`).** Ops fallout worth keeping: overnight OOM (14.3G Claude session) killed attempt 1 → batches now run `setsid`-detached; plan rate limit contaminated 3 cells (excluded by worker-token signature, re-run as patch-up rows); and an m3 worker pushed to main, exposing that ALL git hooks (incl. the worker push guard) had been silently dead since the 2026-06-25 rename via a stale absolute `core.hooksPath` — unset, hook source de-Poe'd + reinstalled, liveness+behavior tripwired in `tests/test_git_guard.py` (6 tests).
0. **Substrate trial (OpenClaw → Maro → Telegram) — SHELVED** — *(Jeremy, 2026-07-01: "get the project where we can trial it for real with hermes or openclaw").* Substrate contract SHIPPED + live-verified 2026-07-01: notify hook (`notify.command`, run_completed/escalation, payload = run_card), uniform result retrieval (`run_result` + `maro-runs status|result`), escalation delivery (navigator dispatch-escalate + director surface both ping Telegram), `maro-notify-telegram` target, `deploy/openclaw/maro-dispatch.sh` symlinked into OpenClaw's scripts. E2E proven: dispatch script → enqueue → drain → run → run_card → Jeremy's Telegram DM (verified success-class goal). Docs: `docs/SUBSTRATE_INTEGRATION.md`. **Unattended hardening SHIPPED 2026-07-01:** budget gates (`budget.per_run_usd` default cost_budget + `budget.daily_usd` cross-run gate via `metrics.spend_today()`, refusal emits escalation notify; box config set 2.0/10.0), phantom `Step -1` root-caused + fixed (parallel batch discarded NEXT.md indices — threaded through, done items marked, display numbers by position; BACKLOG #2), drain-once contract (`enqueue --drain` → `drain_task_store(job_ids=...)`: a dispatch runs exactly what it enqueued, stale queued tasks can't piggyback). Also: user-config test-isolation hole closed (`MARO_USER_DIR`). **Delegation instruction SHIPPED 2026-07-01** (installed in `~/.openclaw/workspace/AGENTS.md`, canonical snippet in `deploy/openclaw/README.md`). **Burn-in COMPLETE 2026-07-02** — full record + adjudicated table in `docs/history/2026-07-02-burnin.md`. 14 dispatched goals over 4 batches; pipeline verdict: WORKS (12/14 delivered; controls behaved). Caught + fixed same-day, each re-proven live: closure cwd FN (ec4c1f3), inconclusive-as-failure FN + verdict prompt (9be749b), NOW-lane file-deliverable misroute (8ed0a09), skipped-closure FP (90b4d1b), cost-per-run join `loop_ids`→`total_cost_usd` (2989bb0). ~$2.45/day total, $0.10–0.60/goal. **Both open items RESOLVED 2026-07-02 (Jeremy):** model lane — accept the subscription contention (rate-limit stucks are an accepted operating cost; graceful degradation is the designed behavior); push guard — OpenClaw may push its own commits to main ("not (only) the job of orchestration"), guard stays scoped to MARO_WORKER_RUN. **Next:** (d) Hermes adapter — contract makes it cheap; steal-from-don't-migrate. **SHELVED 2026-07-04 (Jeremy): revisit ~next week.** *(2026-07-12 status: superseded by the 2026-07-09 Hermes decision — swap OpenClaw→Hermes as substrate when Jeremy gets the new machine; his call/timing. Not an open queue item until then.)*
1. **Per-decision-class cutover** — *code shipped default-off, refined to per-MOVE granularity 2026-06-12.* `navigator.act_dispatch` + `act_confidence_floor` (0.9) + `act_moves` (default `["escalate"]`): escalate earned cutover (defers to human, 6/6 divergences right), close is opt-in (asserts resolution without running; probe-only evidence). Guard keeps first word; `NAVIGATOR_ACTED` audit event; `python3 -m navigator_shadow --agreement` is the evidence table. **Enable decision (23 live rows, 14/14 execute incl. 5/5 organic, all acting-move divergences synthetic probes): escalate ENABLED LIVE 2026-06-21** (Jeremy's call) — `navigator.act_dispatch: true`, `act_moves: [escalate]` in `~/.maro/workspace/config.yml`. Reversible: flip `act_dispatch` off. **MECHANISM PROVEN end-to-end 2026-06-21:** first `NAVIGATOR_ACTED` row written via the real enqueue→drain→`handle_task` path — a "$50k wire transfer" goal drew escalate 0.98, status=stuck/`navigator_escalate`, **no run dir spawned** (the run was prevented, deferred to human). Wiring is live and correct. Remaining is *passive organic accrual* — escalate firing on Poe's own self-generated goals during normal operation (the validation run was a deliberate trigger, not organic). Then → revisit close cutover once it has non-synthetic evidence. Closure decision class stays shadow-only (no live closure callsite yet). **DEFAULT ON 2026-07-08 (Jeremy's flip):** `act_dispatch` hardcoded default now `True` (escalate-only via `act_moves` default) — new installs get the cutover as this box runs it; opt out via `navigator.act_dispatch: false`. See `docs/DEFAULTS.md`.
1.5. **Planning-depth shadow (thread-arch #5, DECIDED 2026-07-09 — see GOAL_BRAIN)** —
   **SHIPPED 2026-07-13.** Adds a `planning_depth` judgment to the existing
   dispatch `decide()` call — no new LLM call, one new envelope field
   (`src/navigator.py`: `PLANNING_DEPTHS = {plan, one-shot, thin-plan,
   spawn-sub-goal}`, default `"plan"`, fails closed on absent/malformed
   values at parse time rather than through `validate_decision`'s hard-fail
   contract — advisory shadow field, not core decision mechanics). Prompt
   guidance for the lighter shapes (`src/navigator_prompt.py`
   `PLANNING_DEPTH_ADDENDUM`) is judgment text, not a hardcoded signal
   scorer: concrete deliverables/paths in the goal, recall-visible prior
   successful same-family runs, NOW-shaped scope — inference, not taxonomy.
   `spawn-sub-goal` shipped as a first-class value per the recursion decree.
   Ships shadow-first via `navigator.shadow_planning_depth` (default
   `False`, documented in `docs/DEFAULTS.md`) gating a `judge_planning_depth`
   flag into `decide()`; wired only at `navigator_shadow.shadow_dispatch_live()`
   per the decided dispatch-only scoping — `shadow_blocked_step_live()`
   deliberately does not judge depth. `pipeline_actual.depth_equivalent`
   records the constant `"plan"` baseline (the default autonomous dispatch
   pipeline is always the full-plan pipeline today; `skip_if_simple` is
   opt-in only via `telegram_listener.py`). Agreement tooling
   (`analyze_planning_depth_agreement()`, extended `python3 -m
   navigator_shadow --agreement`) mirrors the existing move-agreement table.
   Full mechanism + rationale: `docs/NAVIGATOR_SCHEMA.md` "Planning-depth
   shadow (wired 2026-07-13)". No cutover this chunk — same shadow-first
   posture as dispatch-class before its own cutover; next step is
   accumulating live rows for the per-move cutover discussion.
1.6. **/loop trace (thread-arch #9 disposition)** — **CLOSED 2026-07-13.**
   Traced real /loop sessions against the per-turn seam the navigator
   inherits (`_record_loop_decision` / Phase 64 `adaptive_execution`,
   dormant/off-by-default per `docs/DEFAULTS.md`). Found + fixed one real
   bug the trace surfaced: `director_evaluate`'s exception fallback reused
   the same "evaluation skipped" reasoning as the deliberate
   dry_run/no-adapter no-op, masking real failures (a rate-limited LLM
   call in the traced run) as intentional skips — now split into distinct
   reasoning strings, pinned in `tests/test_director.py`. No design
   question surfaced; the per-turn concept is sound. Full trace + evidence
   in GOAL_BRAIN Decisions 2026-07-13.
2. **Dumb-loop audit** — *static half done 2026-06-11; data half round 1 done 2026-06-21* (`docs/DUMB_LOOP_AUDIT.md`). Static: full decision-point inventory, navigator-move mapping, high-consequence priority order. Data round 1: dispatch boundary agreement table from 28 live `NAVIGATOR_DECIDED` rows — execute 14/14 agree, all 13 escalate/close divergences are correct navigator catches on synthetic/probe/impossible/dangerous goals (zero false-escalates on healthy work). **Bounded by coverage:** dispatch is the only live shadow point with data. **Round 2 instrumentation wired 2026-06-23:** `_handle_blocked_step` tree (agent_loop.py:3137–3366, the priority-1 point — step-2 pressure test quantified ~40 wasted runs there) now has a live navigator shadow tap (`navigator_shadow.shadow_blocked_step_live`, config-gated off via `navigator.shadow_blocked_step`); `--agreement` breaks down `by_point`. Heuristic→move map: retry=extend, redecompose/split=fork, stuck=close. **Rounds 3–4 done (2026-06-24, 2026-07-03):** gate-windowed accrual batches; round 4 (6 goals, ~$1.33) fixed the yield problem with doomed-but-dispatch-plausible shapes → 17 rows in one batch. Cumulative blocked-step corpus: 24 rows — doomed 18/19 navigator-stop at 0.95 (waste measured live: heuristic ground ~50 min/$0.35 to reach the verdict the navigator had at minute 3), recoverable 5/5 navigator-forward incl. first exact extend agreement, **zero false escalates**. Full tables + cutover assessment in `docs/DUMB_LOOP_AUDIT.md` Round 4. **Cutover ENACTED 2026-07-03 (Jeremy's call):** blocked-step escalate live on box — `loop_blocked._navigator_act_blocked_step`, escalate-only, ≥0.9 floor, forward-decisions-only, `NAVIGATOR_ACTED` + Telegram per act, 10 tests. Shadow rows keep accruing under the act flag. **Standing re-verify (Jeremy's note):** adjudicate organic blocked_step NAVIGATOR_ACTED rows vs outcomes after real usage — recoverable-class evidence was n=5 at enablement. Revert = config flip. **Live re-proof same day:** doomed goal (dead CI endpoint) escalated at first blocked step in 3.3 min/$0.024 vs the pre-cutover ~50 min/$0.35 grind; NAVIGATOR_ACTED + escalation event + honest run card all verified on disk (run `2ada97d0-wily-glen`).
3. **Thread-brain per-turn maintenance** — *decision-half shipped 2026-06-21.* `agent_loop._record_loop_decision()` appends the director's live mid-loop course-corrections (replan/adjust/escalate/restart on stuck / verify_failure / step_threshold) to the active thread's goal-brain Decisions section via `current_run_dir()`. The director is the live supervisor (Phase 64) and the single clean seam the navigator takes over when it goes per-turn — not the dumb pipeline, so no duplication. Bounded volume (fires on director triggers, not per-iteration). Never-raise; 5 tests (TestLoopDecisionSeam). **(b) dispatch-navigator rationale SHIPPED 2026-07-03:** the live dispatch decision (move/conf/reasoning[:300]) rides the origin dict from `handle_queue.handle_task` through `handle()` into `runs.create_run_dir`, which appends it to the new thread's goal-brain Decisions ("why this run was allowed to exist") — also lands in run metadata for free. Fires only when the navigator actually decided (shadow or act gate on) and the act path fell through to execute. 3 tests. **(a) Compiled-truth half SHIPPED 2026-07-03:** three verified-claim sources append to the thread brain's Compiled truth — ralph-verify PASS (per-step line, capped at `_RALPH_TRUTH_CAP`=8/run), closure verdict with checks_run>0 (one line: achieved/NOT + conf + checks), and the deterministic provenance demotion. Volume filter = only-these-sources + per-run cap. Synergy with the blocked-step cutover: the navigator reads the thread brain at blocked steps, so verified "actually done" claims sharpen its stop/forward calls. 4 tests. **#3 both halves now closed.**
4. **Async fork join + `wait`** — `fork` exists in the navigator schema; the runner has no join semantics. *Reconciled 2026-06-11 with NAVIGATOR_SCHEMA.md's recorded deferral ("until a real thread needs it; sync join in v1"): the navigator is shadow-only, so no thread can issue a fork yet — this is gated behind per-class cutover (#1), not ahead of it. Don't build join semantics for a move that can't fire.*
5. **Skill/playbook freshness layers** — only if staleness shows up there in practice (rules have it; skills have score + circuit breaker).

### Live observation tasks (from GOAL_BRAIN)

- **End-to-end standing-rule observation** — does the medium → long → standing-rule path actually fire in real runs post-M2? Needs production runtime, then check `standing_rules.jsonl`.
- **Recall guard thresholds** — guard thresholds are unmeasured; watch `RECALL_GUARD_TRIPPED` and revisit the made-call defaults.
- **Fan-out revisit policy** — when does the navigator go back to an abandoned/failed child? Judgment call; lands in the step-5 prompt and gets measured via `NAVIGATOR_DECIDED`.
- **When to pull full work-LLM output** — criteria for the "sometimes" in the 2026-06-10 visibility decision; deliberately unpinned until examples accumulate.

6. **Closure check unification** (Phase C leftover) — `director_evaluate(trigger="closure")` wraps `verify_goal_completion`; `ClosureVerdict` retired. Low-priority code hygiene. (hygiene, low priority)

---

## Dormant

See GOAL_BRAIN.md Threads → Dormant (Thread Architecture impl, Phase 65 constraint orchestration, Mage correspondence memory, backlogged repairs).

---

## Changelog pointer

Full session-by-session Done log archived in docs/history/ROADMAP_ARCHIVE.md (still ingested by dev-recall).
