---
status: record
---

# BACKLOG_DONE archive — part 1 of 3 (2026-08-16 rotation)

Rotated out of BACKLOG_DONE.md on 2026-08-16 (the archive had grown to 606KB — past the 256KB whole-file Read limit). Records are verbatim and in their ORIGINAL FILE ORDER, which is accretion order, not chronological — batch-moves carry their own internal dates. This file remains part of the same completed-items record; dev-recall ingests it. This part: the 2026-07-29 cost-thresholds record (with its embedded triage/review history) through the 2026-07-16 closure/downgrade records.

## Data-driven cost thresholds — SHIPPED 2026-07-29 (decree-direct, no BACKLOG item)

Jeremy (2026-07-29, after the escalate-death fix landed): raise the kill
to ~$10 with a surfaced ~$2.50 warning; "much more interested in the
data driven values"; considered removing the cap entirely on the Max
plan but agreed caps stay as circuit-breakers (API-failover backend is
real dollars; runaway loops are what the breaker is for — extends the
2026-07-29 token-cap decree "caps = circuit-breakers, not tuning
knobs").

Shipped: `budget.per_run_usd` ABSENT now means auto —
`max($10 floor, 4 × p90 of successful-run costs)` via new
`metrics.successful_run_cost_p90()` (run-card scan: success_class in
success/done-unverified with recorded total_cost_usd, ≥8 samples,
~15-min cache). Explicit number stays a static cap; 0/null stays
uncapped. New `budget.warn_usd` (absent = `max($2.50, p90)`) sets
`ctx.cost_warn_usd`; crossing it warn-once logs dollars internally and
emits ONE `effort_note` on the conversation channel in effort language
with no dollars (2026-07-17 spend-UX decree). Floor raised $5→$10
because a sanctioned power-tier step alone costs ~$3: escalated re-run
3a4e2692 died at $3.00 against the box's $2.40 cap while holding a
finished memo. Box config overrides ($2/run, $10/day) removed the same
day so auto mode governs; daily stays at the $25 repo default.

Diagnosis correction recorded: the earlier "cumulative budget defunded
the re-run" theory was wrong — `total_cost_usd` initializes per
`run_agent_loop` call, so every sanctioned re-run already gets a fresh
budget (handle.py's `_loop_kwargs` never passes `cost_budget`, so the
gate re-defaults from config per loop). The real defect was only the
breaker being too low for a deliberate power-tier escalation; the
raised/auto breaker is the whole fix.

Live numbers at ship time (28 successful-class cards): p90 = $1.21 →
both floors govern (warn $2.50, kill $10) — Jeremy's gut numbers were
~2× and ~8× the observed p90. As power-tier runs accrue, the thresholds
follow the distribution with no further decree needed.

### C4 container flip — CLOSED 2026-07-16, discovered closed 2026-07-28

The retained flip stub (burn-in trail archived 2026-07-27, below) read
"The box runs `container: off` — the flip stays Jeremy's call." The
thread census checked the live box: `executor.container: on` since
2026-07-16 — Jeremy flipped it the morning after Hermes dispatch went
live (SESSION_PROTOCOL_DESIGN §11 Q7: "I'm fine flipping it on… better
in-practice testing on that harder, more secure edge"; all runs, no
per-origin split). Fresh-install default stays OFF per
CONTAINER_BURN_IN.md §6 — that half was a made decision, not a pending
one. The primary work queue carried an already-decided Jeremy-gate for
12 days; recorded as premise-drift find #7 in
`docs/history/2026-07-28-thread-census.md`. Bookkeeping completed in
the census commit: CONTAINER_EXECUTOR_DESIGN §9 C4-CLOSED note +
SECURITY_MODEL §2 correction.

Jeremy (2026-07-27, AFK note): "consider archiving on the backlog, I
think it's getting pretty big." Everything below moved intact from
BACKLOG.md; the retained residual stubs there point here. Blocks:
triage-pass history preamble (passes 18–25 + VERIFY_LEARN V2–V5
narrative); R6 (E watch-item stays live in BACKLOG); R5/R3/R4/R2 (all
residuals closed); C4-BOX burn-in (flip stub stays live); -1 Purgatorio
r2 graduates (all shipped); #22 capabilities-catalog shipped trail
(residuals stay live); DONE pointer stubs (benchmark/eval isolation,
host-check alerting, orch.py trio — the trio's removal residual now
rides the Phase 38 entry); install-trial residuals ($HOME watch-item
stays live); initial-public-release launch content (filename-scrub
known-gap + auto-resume discussion stay live); and the 2026-07-04
stale-drop record.

### Triage-pass history (through the twenty-fifth pass, 2026-07-20)

Last reviewed: 2026-07-14, eighteenth pass — lesson-funnel intake now has outcome-attributed deferred/completed/failed state, tiered-write counts, dry-run exclusion, and an archive-aware rolling report; the old empty-row “0 lessons” claim was unprovable and is corrected. Previous seventeenth pass: the captain's-log viewer residual shipped as a sortable per-event archive-spanning TSV/JSON slice and moved to BACKLOG_DONE after real Claude review. Previous sixteenth pass: the formal same-corpus M1 validator sweep selected VibeThinker-3B-4bit, rejected three smaller/alternate builds, and exposed/fixed an unsafe local certainty-threshold mismatch; only hardware-gated Linux/Ollama burn-in remains. Previous fifteenth pass: closure-rejected restart attempts are now verdict-stamped before replacement, and deterministic provenance failures outrank narrative closure passes; the closure-demotion residual moved to BACKLOG_DONE after real Claude review. Previous fourteenth pass: escalation-class notifications now carry the immediate originating run handle when one exists and an explicit blank legacy fallback; R4's final residual moved to BACKLOG_DONE. Previous thirteenth pass: the skill-candidate sweep now holds one fail-closed per-workspace flock across scan, paid extraction, and consumption; moved to BACKLOG_DONE and R3 residuals closed. Previous twelfth pass: one neutral learnability policy now serves curated run cards and raw outcome records; evolver no longer synthesizes fake success status; moved to BACKLOG_DONE. Previous eleventh pass: CuratorSpec runtime contracts now distinguish mandatory/optional outputs and dependencies, execute transactionally, and reject contract drift; moved to BACKLOG_DONE. Previous tenth pass: the R3 origin-dictionary residual shipped as one plain-JSON `Origin` TypedDict used across task/run/recall/thread boundaries; moved to BACKLOG_DONE. Previous ninth pass: PID-reuse-safe container/clone ownership shipped with Linux boot+start ticks and Darwin kernel microsecond timestamps; moved to BACKLOG_DONE. Previous eighth pass: durable O(1) run-reference index shipped with one-time legacy migration, exceptional fallback, import/prune maintenance, partial-migration state, and concurrency/corruption coverage; moved to BACKLOG_DONE. Previous seventh pass: `test-safe.sh` now runs its full chunked suite on macOS while preserving Linux affinity; moved to BACKLOG_DONE. Previous sixth pass: R5 run-curation side-effect boundary and runtime dependency semantics shipped and moved to BACKLOG_DONE; pure card construction is durably checkpointed before explicit maintenance. Previous fifth pass: R5 execution-fence setup now refuses visibly on mandatory cwd/policy failures, with five fault-injection tests; full suite green. Previous fourth pass: independent holistic review of the rolling 48-hour range `d717915e..8aa9876` added R5 (portable-pack review seal bypass reproduced end-to-end; pack import concurrency/global-routing breach; implicit hosted-free data egress + unenforced latency cap; run-curation atomicity/hidden-side-effect boundary; execution-fence setup fail-open). Canonical `pytest tests/ -q`, real-Docker container E2E, and `git diff --check` green. Previous same-day pass: session close (R4 final capstone adversarial-review across the ENTIRE day's changeset, `b2dc34d..HEAD`, run cross-model via the real `/adversarial-review` skill per the session's closing instruction: 3 more real bugs fixed live — enqueue-failure dead-chain now surfaces to the operator instead of silently completing, check-in payload off-by-one fixed [all 3 reviewers converged independently], skill-candidate consumed-on-crash bug fixed; 1 architectural residual documented [handle_id absent from escalation-class notify payloads, pre-existing, not a regression]; also closed out the one un-triaged batch-1 finding — navigator_shadow's analyze_live_agreement/analyze_planning_depth_agreement duplication, extracted shared _tabulate_agreement helper. Full suite green, 169/169, after every fix this session). Second pass same day (post-1.0 /goal session: recursion check-in + planning-depth shadow shipped, R1 architectural cleanup shipped, 1.6 /loop trace closed with evidence, knowledge-web read side traced and re-scoped — not built, real prerequisite gap found; R3 adversarial-review of all of the above — 5 bugs fixed live, 3 architectural residuals documented; #19/#20/#21 fully-shipped stubs archived to BACKLOG_DONE (content was already there or wholesale-done); #0's stale "mining passes TODO" bullet corrected — all 4 miners are shipped; -1's stale unchecked "Cosmetic sweep... SHIPPED" checkbox fixed; triaged the rest of the Actionable Stack — nothing else is both unblocked and ready without Jeremy's input). Previous same-day pass: #10, #14, #18 shipped and archived to BACKLOG_DONE; #17 trimmed to its O(all runs) residual; #22 residual (blank-slate skill set) and hist-r2-02 checked off; #25 code shipped, stays open pending Jeremy's API keys; container-executor C4 mechanics merged, C4-BOX real-goal burn-in stays Jeremy-gated. Previous: 2026-07-09 (decision-cleanup session with Jeremy: #19 thread-arch decisions all resolved + recursion decree recorded, intent-resolution A/B dropped, orch.py trio deprecated, host-check wired+scheduled — four entries → BACKLOG_DONE; fastembed lane confirmed stays-gated). Previous full triage: 2026-07-04.

Current pass (2026-07-14, nineteenth): done-vs-achieved now has explicit
prospective organic/smoke/control/benchmark provenance in run metadata and the
durable outcome ledger, plus an honest n≈30 standing gate that keeps historical
unknowns unknown.

Current pass (2026-07-14, twentieth): benchmark/eval cells now reserve
collision-resistant per-cell projects or retained direct-Director workspaces,
so repeated experiments cannot inherit the launch repo or earlier artifacts.

Current pass (2026-07-14, twenty-first): closure-rejection verdict persistence
now distinguishes an absent row from a failed update, retries the idempotent
write once, and fails closed before restart. Pending deferred rows are durable
quarantine rather than success evidence; failed-stamp runs retain loop joins.

Current pass (2026-07-14, twenty-second): verdict persistence now has one
atomic typed result (`updated` / `missing` / `write_failed`), bounded retry at
the ledger boundary, and no legacy boolean API. All writers use the shared
seam; owner-facing delivery behavior on failures remains the open decision.

Current pass (2026-07-14, twenty-third, `/goal` catch-up session): the
owner-facing delivery-behavior decision from pass twenty-two is now made and
shipped — EXT-AUDIT-2 archived to BACKLOG_DONE.md in full. Delivered work is
preserved with a prominent audit warning, its learning is quarantined, and an
exact repair record is retained when possible. Concurrent `maro resume` now
refuses immediately under a per-run kernel lock and emits an operator
notification. R5 confirmed fully
checked off and its header now says so, matching R2/R3/R4. VERIFY_LEARN_ARC
V1 (expectation stamping) shipped this session as the next-arc-after-1.0
opener, and **V2 (cadence verdicts + auto-revert) shipped the same day** —
`verify_applied_suggestions()` closes the VERIFY gap for evolver-applied
suggestions (confirmed→calibrate, degraded-self→auto-revert,
degraded-human→review queue, inconclusive→extend/park), trust policy §4 live
via `verdict_trust()`, and a dead-on-production `recorded_at` window bug fixed
en route; see `docs/VERIFY_LEARN_ARC.md` and GOAL_BRAIN Decisions 2026-07-14.
**V3 (graduation behavioral auto-verify) SHIPPED the same day** after Jeremy's
"buildable now" call (Codex's earlier "deferred / design-dependency" framing
superseded): applied graduation rows already flowed into V2's cadence verify,
but on the class-neutral global stuck-rate — noise for a single class, so they
only ever parked `unverifiable`. V3 makes it resolve — `verify_applied_suggestions`
consumes the row's `expected_signal` and verdicts a `failure_class_rate` row on
*that class's* rate over timestamped-diagnosis windows (diagnoses gained a
`recorded_at` stamp; self-falls-back to the stuck-rate when class windows are
thin). Lifecycle + symmetric authority reused from V2; rules stay advisor-gated
(human-applied → degraded surfaces for review, never auto-reverted — the owner's
safe default); structural `verify_pattern` stays pure observability. Knob
`evolver.verify_use_class_signal` (default ON); each diagnosis's date comes from
its `recorded_at` stamp or an events-log join on `loop_id` (`_loop_ts_index`,
~99% coverage), so the class path is live on the historical ledger, not dormant.
The applied-change verify→learn loop is now closed for BOTH the
evolver-suggestion (V2) and graduation (V3) lanes.
**Navigator-side V4/V5 SHIPPED 2026-07-14 — the arc (thread decision #6) is now
fully closed.** V4: divergences LLM-adjudicated at cadence (navigator_right /
pipeline_right / both_defensible), appended append-only and surfaced in
`--agreement` as an `adjudicated` breakdown; gated OFF (`navigator.adjudicate_divergences`),
proven on the box's 71 live divergences. V5: `pipeline_right` clusters (≥3
same-shape) crystallize into corrective navigator lessons injected into `decide()`
via the worker-slice seam; A/B flag `navigator.lesson_inject` (default off).
Per-move cutover stays Jeremy's call (this makes the evidence standing, not the
cutover automatic). Enabling adjudication on the box is a spend decision (the one
remaining owner call) — left OFF pending Jeremy.

Current pass (2026-07-14, twenty-fourth): audit-incomplete delivery now
converges through an idempotent, crash-resumable reconciler. A manual
`maro-runs repair-audits [handle-or-loop]` surface and the existing
autonomy/evolver cadence replay exact per-loop persisted verdicts, finalize
only each named row's deferred lesson/knowledge extraction, then refresh
derived run surfaces and clear quarantine after sibling loops converge. One
workspace lock prevents overlapping paid extraction; stable ordering and
failure caps prevent starvation and unattended spend. The same truth pass
corrected the live Manti, Phase 65, and
count-files records; shipped items moved to BACKLOG_DONE rather than remaining
as misleading open work.

Current pass (2026-07-20, twenty-fifth): R6's two remaining deferred residuals
(A div_key collision, G double-judge race) shipped — both were parked on
"adjudication is gated OFF", and that gate was flipped ON at cadence 2026-07-16,
so the deferral conditions the review itself named had come true. Verified
against the live workspace first (dataset grew 71→82 divergences, 0 collisions,
all 71 verdicts re-key for free): goal_preview now joins the divergence key with
a read-time migration that strands nothing, and the adjudication pass runs under
one fail-closed per-workspace lock so cadence + manual `--adjudicate` can't
double-spend. E stays deferred as an explicit watch-item (lesson_inject is live
but only one lesson has crystallized — no A/B signal yet).

### R6. VERIFY_LEARN_ARC V4/V5 adversarial review — 6 fixed (4 live + A/G on 2026-07-20), 1 deferred (E, watch-item) (2026-07-14)

Codex ×3 (Skeptic/Architect/Minimalist) over `e792768` (V3 dates) + `8349b7c`
(V4/V5). Verdict CONTESTED — real findings, none blocking (evidence-only,
adjudication gated OFF; empirically 0 div_key collisions in the 71 live rows,
events.jsonl is 2.8 MB so the "OOM" read is trivial today). **Fixed live** (commit
after this entry): B honest persistence (`raise_on_error=True`, count only on
durable write, `write_failed` counter); C atomic lessons rewrite (tmp + os.replace);
D2 undatable-diagnosis drops now counted + logged (no-silent-caps); F
`NAVIGATOR_ADJUDICATED` registered in EVENT_TYPES.

**A + G SHIPPED 2026-07-20** (twenty-fifth pass). Both were deferred on the
premise that adjudication was gated OFF; that premise expired when Jeremy
enabled `navigator.adjudicate_divergences` at evolver cadence 2026-07-16 (and
`lesson_inject` 2026-07-14) on this box — the deferral conditions the review
named literally came true, so the residuals reactivated. Verified against the
live workspace before touching code: dataset has grown 71→82 divergences
(organic growth, exactly what the enablement intended), still 0 collisions, and
all 71 stored verdicts carry the fields needed to re-key for free. **Deferred
residuals:**
- [x] **A — div_key second-precision collision.** Two same-second,
  same-(point,move,pipeline), different-goal divergences collapsed to one key —
  one verdict silently covering both. **SHIPPED:** `goal_preview` is now part of
  `_divergence_key`, and `_load_adjudications` recomputes the key from each row's
  echoed fields (all 71 carry them) instead of trusting the stored value — so
  the widened key re-keys the legacy rows *in place*. The feared "retroactively
  invalidates all 71 div_keys → re-adjudicate + re-spend + orphan rows" is
  averted: proven on the real workspace, all 71 verdicts still join their
  divergences (0 strand, 0 re-spend). Rows predating the field echo fall back to
  their stored key. Pins: `test_goal_preview_is_part_of_the_key`,
  `test_legacy_row_rekeys_without_readjudication` (reproduces the exact
  same-second collision the old key merged).
- [x] **G — check-then-write concurrency race (double-judge/double-spend).**
  Reachable now that cadence adjudication is ON while the manual
  `python3 -m navigator_shadow --adjudicate` path still exists (and the docs
  actively tell operators to run it) — a cadence pass and a hand-run pass could
  judge the same divergences twice and double-spend. **SHIPPED:** the whole
  load→judge→write pass runs under one fail-closed per-workspace `file_lock`
  (the skill-candidate-sweep precedent); a pass that can't get the lock in time
  no-ops (`locked`) and its un-judged divergences carry forward to the next
  append-only cycle — no duplicate spend, no corruption. The manual CLI prints a
  clean "already running" message instead of a traceback. Pin:
  `test_locked_pass_skips_without_spending`.
- [x] **D1 — `read_jsonl_tail` reads the whole file before applying `limit`.** Real for a
  multi-GB `events.jsonl`; latent here (2.8 MB, caller self-extinguishes as
  `recorded_at` rows age in). Fix = byte-bounded tail read in `jsonl_utils` (shared;
  do it as its own change, not folded into this arc).
  **SHIPPED 2026-07-15:** `_iter_lines_reverse` backwards chunked read
  (64 KiB, test-shrinkable), stops at `limit` valid records; both paths now
  binary + per-line decode. Adversarial review (fuzz vs old semantics, 0
  mismatches; live concurrent-append probe clean) caught the first cut's
  full-scan branch returning `[]` on one crash-torn multi-byte line —
  fixed same pass, pinned in `test_undecodable_line_skipped_in_full_scan`.
- **E — lesson_text embeds truncated goal previews (anchoring risk).** Still
  correctly deferred, now as an explicit watch-item: `lesson_inject` is ON (since
  2026-07-14) so the A/B *is* running, but only ONE lesson has crystallized so
  far (blocked_step execute→extend), so the shadow marker has no signal yet.
  Re-evaluate once more lessons accrue and the `lessons_injected`>0 vs =0
  comparison has n — pre-optimizing the prompt before then is guessing.

### R5. Independent holistic + adversarial review of the rolling 48-hour changeset (2026-07-13) — all residuals closed

Codex review of `git diff d717915e..8aa9876` (138 files, ~20k added
lines), with architecture first and security, concurrency, correctness,
operability, maintainability, canonical tests, and real-Docker E2E as the
supporting lenses. The initial review changed no product code. The contained
fixes marked below were implemented after the adversarial pass; architectural
work remains explicit rather than being hidden behind a checkbox.

- [x] **HIGH — the local-review seal failed to bind the reviewed payload while
  claiming post-review tamper detection** (`src/pack.py:368-410, 833-859`).
  The seal hashes `REVIEW.md`, while artifact hashes live in the same mutable
  `pack.json`; neither is authenticated outside the archive. Reproduced
  end-to-end: seal a pack whose review contains `SAFE CONTENT`, replace the
  artifact with `TAMPERED AFTER REVIEW`, update that artifact's sha256 in
  `pack.json`, retain the original reviewed text, and `import_pack()` accepts
  it. This invalidates the existing claim that the artifact-sha check detects
  post-seal payload swaps. Fix direction depends on the threat model: for
  adversarial integrity, sign a canonical manifest covering every artifact
  with a key/signature outside the archive; for a local review workflow only,
  bind the reviewed artifact-set digest at seal time and stop describing it as
  post-seal tamper protection. Add the exact demonstrated swap as a regression
  test. **Fixed 2026-07-13:** the reviewed copy now embeds a canonical digest
  covering artifact metadata, paths, and bytes; import rejects the reproduced
  artifact+manifest-hash swap. The docs now accurately call this a local
  consistency seal, not adversarial authenticity; external signatures remain
  the future answer if cross-party authorship is required.
- [x] **MEDIUM — portable import routed trust-bearing writes through a
  process-global environment mutation and used unlocked read/check/write
  sequences** (`src/pack.py`). **Fixed 2026-07-13:** `orch_items.memory_dir_context()`
  now supplies an execution-scoped `ContextVar` storage target without mutating
  `MARO_MEMORY_DIR`; a per-target import gate serializes the full load/check/write
  transaction; conflict notes use `locked_rmw`; quarantine files use
  `locked_write` + `atomic_write`. Deterministic threaded regressions prove
  different targets cannot redirect each other and same-target imports cannot
  overlap their dedup decisions.
- [x] **MEDIUM — merely having a Groq or Gemini credential implicitly opts
  step results into third-party data egress, and the advertised latency cap
  does not bound a call** (`src/hosted_free.py:83-90`,
  `src/step_exec.py:1590-1628`, `src/llm.py:2025-2030`). Hosted-free defaults
  enabled and is tried whenever either key exists, without an explicit
  provider/data-egress choice. Its `max_latency_ms` breaker measures only
  after completion, while the HTTP transport can block for 120 seconds.
  Fix direction: make hosted-free egress explicitly opt-in (or introduce one
  shared provider-egress policy) and thread a tier-specific request timeout
  through `OpenAICompatAdapter` so the configured cap is enforceable.
  **Fixed 2026-07-13:** explicit opt-in now defaults OFF and the configured
  latency ceiling is the HTTP transport timeout, with docs and regression
  coverage for both boundaries.
- [x] **MEDIUM/LOW — the execution-fence setup boundary silently fails open**
  (`src/agent_loop.py:235-323`). Project-dir creation, cwd ContextVar binding,
  container scratch-clone setup, and rw-root policy now sit under one blanket
  `except Exception: pass`; an unexpected failure can continue execution with
  inherited cwd/stale policy despite the block's stated fence guarantee. Fix
  direction: make the mandatory fence/cwd establishment fail visibly as an
  error or stuck result; keep only genuinely optional adornments best-effort,
  with narrow logged exceptions. Add fault-injection tests at directory
  creation and cwd/policy binding. **Fixed 2026-07-13:** stale policy reset,
  fence-directory creation, cwd binding, and writable-root policy binding now
  refuse before decomposition with a structured `stuck` result. Refusal
  neutralizes ambient policy, releases locks, and cleans setup-only clones or
  worktrees. Declared-root discovery safely degrades to cwd-only; scratch-dir
  creation stays visibly best-effort. A scratch-clone setup exception now
  suppresses containerization even when it occurs before repo classification.
  Five fault-injection tests cover these boundaries.

Adversarial follow-ups:

- [x] **LOW — prefix provenance was inferred from equal tier values**, so
  `effort:high garrytan:` plus an explicit persona override could discard the
  user's explicit effort tier (`src/prefixes.py`, `src/handle.py`). Fixed by
  tracking whether the persona rule itself supplied the tier; regression added.
- [x] **LOW — recall-only prefix stripping emitted dispatch conflict warnings.**
  `strip_prefixes()` now uses a quiet parse mode; dispatch parsing still warns.

Architectural follow-on: the highest-change boundary modules are now
`llm.py` (2,509 lines), `handle.py` (2,425), `step_exec.py` (1,766),
`director.py` (1,615), `run_curation.py` (1,233), and `pack.py` (1,110).
Continue the good neutral-leaf extraction pattern established by
`prefixes.py` and `decision_prior.py`: explicit execution/storage contexts
and phase-result contracts should be the next decomposition seam, rather than
more ContextVars, environment overrides, and best-effort side effects inside
the facades.

### R3. Adversarial-review of the recursion-checkin + R1 merge — all residuals closed (2026-07-13)

3-reviewer (Skeptic/Architect/Minimalist) pass over `git diff b2dc34d..HEAD`
(recursive-goal check-in, planning-depth shadow, `director_evaluate`
skip/error split, R1 architectural cleanup already archived to
BACKLOG_DONE). Full reports: `docs/history/2026-07-13-adversarial-review-batch1-{skeptic,architect,minimalist}.md`.
Five real bugs/gaps fixed live, with regression tests:

- **`director_evaluate`'s masked-failure fix only covered one of two code
  paths** (Architect, High). The `if not data:` branch (unparseable-JSON
  response) still returned the old shared `_continue` object with the
  misleading `"evaluation skipped"` reasoning; only the `except Exception:`
  branch had been rewired. Fixed: both branches now return
  `_continue_on_error`. Test: `test_bad_json_returns_continue` now pins the
  reasoning string.
- **Check-in fires before the continuation is actually enqueued** (Skeptic,
  Medium). `_advance_origin_with_checkin` used to fire `notify.emit` as a
  side effect before `task_store.enqueue` ran; an enqueue failure (lock
  contention, disk full, corrupt `blocked_by` graph) left the user told
  "still running" for a chain that silently died, with no operator alert
  anywhere (`handle_queue.py` only surfaces `action == "surface"`). Fixed:
  `_advance_origin_with_checkin` now returns `(origin, should_fire)` without
  firing; both `continue`/`narrow` branches in `handle_escalation` enqueue
  first and only fire the check-in after a confirmed-successful enqueue.
  Test: `test_enqueue_failure_suppresses_checkin`.
- **Jitter/floor-guard ordering bug** (Skeptic, Low). `_checkin_jitter()`
  floored `lo` to `>=1` *before* the `hi < lo` swap, so a misconfigured
  negative `checkin_jitter_max` could produce a negative `lo` after the
  swap and `random.randint` could return negative jitter (spamming
  check-ins). Fixed: swap first, then floor `lo`, then clamp `hi >= lo`.
- **Dead `origin.get("root_goal")`/`origin.get("goal")` fallback keys**
  (Architect + Minimalist, independently convergent). Neither key is ever
  written anywhere in `src/`, `tests/`, or `docs/` — only `parent_goal`
  (from `loop_post_step.py`) is real. Fixed: `_fire_checkin` now only reads
  `origin.get("parent_goal") or task.get("reason", "")`.
- **Curator topo-sort didn't detect duplicate `provides` keys** (Skeptic,
  Low). A second curator declaring a `provides` key another curator already
  provides used to silently win (last-declaration-wins) instead of
  failing loudly — the exact "silent, plausible-but-wrong order" class the
  R1 fix exists to prevent. Fixed: `_topo_sort_curators` now raises
  `RuntimeError` on a duplicate `provides` declaration. Test:
  `test_duplicate_provides_raises`.
- **`find_unconsumed_skill_candidates` was an unbounded full-runs-directory
  walk** (Skeptic, Medium). `limit` only trimmed the *return*, not the
  scan — every run this box has ever curated got JSON-parsed on every
  evolver tick (~every 10 heartbeats), forever, with no cap (this repo's
  retention decree means the runs dir only grows). Fixed: mirrors
  `recall.find_prior_attempts`'s pattern — mtime-ordered, capped at
  `_SKILL_CANDIDATE_SCAN_CAP` (200) dirs scanned, same as
  `recall._METADATA_SCAN_CAP`.

Two Low findings were cheap enough to fix live alongside the above (not
architectural, just small):
- Stale "reuses this cycle's adapter" comment in `evolver.py` (Architect)
  — only true when the caller (`loop_finalize.py`) passes `adapter=`
  explicitly; the heartbeat-driven autonomous cycle never does, so
  `promote_skill_candidates` builds a second adapter there. Comment fixed
  to describe both paths.
- `handle.py`'s `_PREFIX_REGISTRY` re-export reassignment footgun
  (Architect) — mutating in place works, reassigning the name would
  silently no-op. Added a comment at the binding site warning against
  reassignment.
- PLANNING_DEPTHS/PLANNING_DEPTH_ADDENDUM hand-sync drift risk (Architect)
  — added `test_addendum_names_every_planning_depth` asserting every
  `PLANNING_DEPTHS` member is still named in the prompt addendum, per the
  finding's own "at minimum" fallback (full codegen not done — see below).
- 3 near-duplicate single-value parse tests in `test_navigator.py`
  (Minimalist) collapsed into one parametrized test.
- `navigator_shadow.analyze_live_agreement`/`analyze_planning_depth_agreement`
  near-verbatim duplication (Minimalist) — extracted shared
  `_tabulate_agreement(rows, group_keys, agree_fn)` helper; both functions
  now build their row list, define an agreement predicate, and call the
  shared tabulator. Existing `test_navigator_prompt.py` agreement-table
  tests pin the return shape unchanged.

All architectural and operational follow-ups from this pass are now shipped;
see the R3 entries at the top of BACKLOG_DONE.

### R4. Final capstone adversarial-review across the entire day's changeset — all residuals closed (2026-07-13)

3-reviewer (Skeptic/Architect/Minimalist) pass over the full session range
`git diff b2dc34d..HEAD` (26 files, ~2,830 insertions across 13 commits —
recursive-goal check-in, planning-depth shadow, director_evaluate fix, R1
architectural cleanup, and R3's own fixes), per the closing instruction of
this session's `/goal`: "run the adversarial-review against the entire
changeset across all the chunks." Run via the actual `/adversarial-review`
skill (Codex reviewers, cross-model, not internal subagents). Reports:
`docs/history/2026-07-13-adversarial-review-final-{skeptic,architect,minimalist}.md`.

All three reviewers scoped their attention (per instruction) to what R3
hadn't reviewed yet — R3's own fix commit (`f837c06`) and the
`navigator_shadow` dedup refactor (`75b8ccc`) — and converged, independently,
on real bugs in that unreviewed code:

- **Enqueue failure during `continue`/`narrow` still silently completed the
  escalation task with a dead chain** (Architect High + Minimalist Medium,
  independently convergent — the strongest signal in this pass). R3's fix
  correctly suppressed the *misleading* check-in on enqueue failure, but
  left the underlying gap: `handle_escalation` swallowed the
  `task_store.enqueue` exception, logged a warning, and returned
  `action="continue"` with `followup_task_id=None` — an operationally
  successful-looking disposition for a goal chain that just silently died.
  `handle_queue.drain_task_store` only marks a task `fail()`ed if
  `handle_task()` raises; it never raised, so the task was marked
  `complete()`. Fixed: both branches now fall back to `action="surface"` on
  enqueue failure, with `reasoning`/`summary_for_user` naming the actual
  exception — reusing `handle_queue.handle_task`'s existing
  action=="surface" → `notify.emit(...)` operator path rather than building
  new plumbing. Tests: `test_enqueue_failure_suppresses_checkin` updated to
  assert the new disposition; new
  `test_escalation_enqueue_failure_notifies_operator` proves the full
  path end-to-end (mocks `task_store.enqueue` to raise, asserts
  `notify.emit` actually fires with the failure in the payload — this
  exact wiring had no test before, in either R3 or the original feature).
- **Recursion check-in payload was off-by-one** (all 3 reviewers,
  independently — every reviewer flagged the same line). R3's
  enqueue-then-fire reordering left `_advance_origin_with_checkin`
  advancing `origin["checkins_sent"]` *before* `_fire_checkin` ran, but
  `_fire_checkin` still added its own `+ 1` on top — so the first check-in
  reported `checkin_number=2` while the carried origin correctly recorded
  `checkins_sent=1`, and every later one was one ahead too. Fixed:
  `_fire_checkin` now reads `origin["checkins_sent"]` directly, no double
  increment.
- **Skill candidates were permanently consumed even when `extract_skills`
  crashed before evaluating them** (Skeptic Medium). R1's
  `promote_skill_candidates` (new this session) marked every candidate
  consumed unconditionally after its `extract_skills` call, including on a
  caught exception — a transient adapter failure, timeout, or malformed
  response burned the candidate's only retry instead of a real decision.
  The existing test (`test_promote_skill_candidates_extract_exception_is_non_fatal`)
  had pinned this as intended behavior; it did not survive contact with
  the Skeptic's framing ("declined after evaluation" vs "evaluation never
  happened"). Fixed: candidates that made it into the extraction batch are
  now only marked consumed if `extract_skills` returned normally; ones the
  local `success_class` filter already rejected (never sent to
  `extract_skills` at all) are unaffected and still consumed as before.

The final `handle_id` notification-contract residual is shipped and archived
in BACKLOG_DONE.

### R2. Adversarial-review batch-2 findings — 3 fixed live, 1 architectural residual (2026-07-13)

3-reviewer (Skeptic/Architect/Minimalist) pass over batch-2's merged diff
(`1ecbec0..7fb1281` — BACKLOG #18-residual/#25/#10, 16 files). All three
reviewers independently converged on the same root bug, which is the
strongest signal any single review has produced this session. Three real
bugs fixed live, regression tests added for all of it:

- **`MARO_LLM_MAX_RETRIES` silently defeated the hosted-free fail-fast
  contract** (all 3 lenses flagged this independently — highest-confidence
  finding of the whole review). `_retry_complete()` (`src/llm.py`)
  unconditionally re-read the env var and overwrote *any* caller-supplied
  `max_retries`, including `GroqAdapter`/`GeminiAdapter`'s deliberate `0`
  (BACKLOG #25's whole point: a rate-limited free tier should trip the
  ladder's own breaker immediately, not camp on a 65s exponential backoff).
  An operator setting that env var for unattended paid-backend resilience
  would have silently reactivated backoff on the free tier too. Fixed:
  `max_retries` now defaults to `None` (env-tunable), and an explicit
  caller-supplied value bypasses the env override entirely — only the
  *default* is env-tunable, not an explicit contract. Test:
  `test_retry_complete_explicit_value_beats_env_override`.
- **Hosted-free latency breaker never fired on non-HTTP failures**
  (Architect). `_HostedFreeLadder.complete()` called `report_latency()`
  only on success — a hung/erroring provider (timeout, connection reset)
  paid the full latency tax on *every* subsequent call, since nothing ever
  tripped its breaker to skip it. Fixed: the generic exception path now
  reports elapsed time too, so a slow failure trips the same grace-then-trip
  breaker as a slow success. Test:
  `test_slow_non_http_failure_trips_latency_breaker`.
- **`maro inspect-run <crash-time-loop-id>` broke after `maro resume`**
  (Architect). Resuming stamps `metadata.json`'s `loop_id` with the *new*
  attempt's id, overwriting the old one — but the old id is what the
  operator actually has in hand from the crash message, and
  `resolve_run_dir()` only matched the current scalar `loop_id`. Fixed:
  the metadata scan also matches `origin.resumed_from`. Test:
  `test_resolve_run_dir_by_pre_resume_loop_id`.

One finding reviewed and accepted as low-risk, not fixed: **Minimalist**
noted the per-model `local_max_tokens` floor table (BACKLOG #10) turns one
box's empirical sweep into the default for every install sharing the same
Ollama model tag, even though a different quantization/build behind the
same tag could behave differently. Judged low-severity — worst case is a
graceful escalation to the paid tier (not corruption), and there's already
a documented per-install config override (`docs/LOCAL_VALIDATOR.md`).

One finding is real but architectural, not fixed live (cross-cutting per
Jeremy's "document if large" instruction):

- [x] **`maro resume` has no structural serialization against concurrent
  invocation** (Skeptic, high + medium — two findings on the same root
  cause). This predates batch-2 (the checkpoint/PID-liveness/status checks
  in `cli._cmd_resume` and the plain `write_text()` metadata writes in
  `src/runs.py` are all pre-existing; batch-2 only added `open_run`/
  `close_run` wrapping around the existing resume flow, unchanged
  concurrency posture). Concrete failure: two terminals run `maro resume
  <same-loop>` after a crash; both pass the dead-PID/status checks before
  either writes a new in-flight marker, both call `open_run()` on the same
  `handle_id`, and both enter `run_agent_loop()` against the same remaining
  steps — racing on `checkpoint.json`/`metadata.json`/reports/artifacts and
  possibly duplicating external side effects. The Concurrency-hardening
  arc's admission-gate pattern (flock held for run lifetime, `refused_busy`
  naming the holder) is the obvious mechanism to reuse, but wiring it into
  the interactive `maro resume` CLI path is a design decision (lock
  granularity — per-loop_id? per-handle_id? — and whether a second resume
  should refuse or queue) that deserves Jeremy's input given how `resume`
  is actually used (human-triggered crash recovery, not normally
  parallel-invoked) rather than a blind structural fix. **Shipped 2026-07-14
  after Jeremy's decision:** the durable handle (legacy fallback: loop id) now
  owns a fail-closed, nonblocking flock for the full resume lifetime. A second
  terminal gets `E_RESUME_BUSY` immediately, names the holder, performs no loop
  work, and emits default-on/durable `resume_refused_busy`. Evidence:
  `docs/history/2026-07-14-audit-delivery-and-resume-admission.md`.

- [x] **`maro resume` still relies on checkpoint `in_flight.pid` to distinguish
  a stranded run from an original loop that is alive between steps.** The new
  per-run lock deliberately serializes resume-vs-resume, the owner-approved
  scope; the original `run_agent_loop` process does not hold that lock. After a
  step, checkpoints clear `in_flight`, so a stale `stranded_run` notification
  could prompt a resume while the original is healthy between steps. Closing
  this needs a shared run-lifetime lease (and reconciliation with project
  admission), not a local CLI lock extension. Found by the 2026-07-14
  adversarial follow-up; keep separate from the shipped resume-vs-resume fix.
  **SHIPPED 2026-07-15:** `src/run_lease.py` — per-loop flock (ProjectSlot
  pattern), acquired in loop_init, released at loop finalize,
  kernel-released on death; probed (True/False/None) by `maro resume` and
  both heartbeat sweeps; no config knob (correctness, not policy).
  Adversarial review: fd-inheritance leak empirically clean (PEP 446 +
  close_fds), probe/acquire race unreachable, sanitization single-sourced;
  its one confirmed MED — lease releases at LOOP finalize but the owner
  process lives on through the closure/quality-gate window, so lease-False
  falsely read as "process dead" — fixed by corroborating with run-metadata
  pid liveness in all three consumers before treating a run as stranded.
  Tradeoff accepted: a recycled-alive metadata pid delays stamping/resume
  until that pid exits (fail-closed; heartbeat retries). Lease files are
  never unlinked (retention posture, ~200 B/run); sub-loop last-writer-wins
  checkpoint probing is covered by the same pid corroboration.

### C4-BOX. Container executor — box-side real-goal burn-in (2026-07-13, Jeremy runs on the runtime box)

The container **mechanics** are burned in and green on the dev Mac (Docker
Desktop 23.0.5): image build + CLI pin, containment, uid/gid, boot-tax ~360ms,
C2 stranded-container reaper, doctor rows (recorded in
`docs/CONTAINER_BURN_IN.md §0`). `tests/test_container_e2e.py` grew from 4 to
15 real-docker tests in the 2026-07-13 merge (stale-clone sweep + broader
E2E tier) and all 15 pass for real on this box (docker reachable here too,
not just the Mac). What can't be done off the box —
because it needs a `/login`'d `maro-claude-auth` volume (interactive OAuth) and
spends tokens — is the **real-goal** half. Jeremy runs this on the other machine:

**RUN 2026-07-15 (Fable 5) — burn-in executed, two mount findings fixed live.**
See `docs/CONTAINER_BURN_IN.md §5b`. Auth volume seeded; CLI pin 2.1.207→2.1.210.

- [x] **Full acceptance probe with a real goal** — done, with a twist: the worker
  **refused** the hostile goal (good second layer), making the *behavioral* probe
  inconclusive on containment. Added `container-acceptance-probe.sh structural`, a
  deterministic real-docker containment proof (T2 declared-out-of-scope + T1
  un-declared) → **CONTAINED**. This is now the criterion of record.
- [x] **Dogfood no-regression run** — 3 corpus goals ran concurrently under
  `container: on`; honesty machinery fired identically to host mode; the
  `done/achieved` goal's deliverable verified on disk (`clawd:clawd`). Two stuck
  honestly. Found+fixed **finding #1** (file-shaped fence roots weren't mounted)
  and **finding #2** (containment gap: goal-declared host paths outside the
  workspace were mounted rw — `build_mount_map` now whitelists to workspace
  subtree + `validate.write_fence_allow`).
- [x] **Fill the `CONTAINER_BURN_IN.md §5` go/no-go checklist** — done (§5b). **The
  flip stays Jeremy's**: box left `container: off` overnight (fix makes it strictly
  safer; off is conservative pending his read), fresh-install default unchanged.
  C4 mechanics + burn-in evidence complete; mark C4 shipped in design §9 + MILESTONES
  once Jeremy flips.

**Follow-up (2026-07-15):**
- [x] **Container `/tmp` was ephemeral per step — SHIPPED 2026-07-15.**
  `build_run_command` binds a per-run host scratch dir (`<run_dir>/scratch`, on
  the workspace subtree) at the container `/tmp` via `run_scratch_dir()`, so
  cross-step scratch survives within a run while staying off the host `/tmp`.
  Per-run (not step-named — a step-named dir would still lose it across steps).
  Reversible: `executor.container_run_scratch: false`. Real-docker verified.
  Moved to BACKLOG_DONE.md.

### -1. Purgatorio r2 graduates (2026-07-10 re-run; docs/audit-2026-07-r2/RECONCILIATION.md)

All adversarially verified (41/42 confirmed). The two blockers-at-the-time first (de-1.0 decree 2026-07-15: nothing is "1.0-blocked" anymore; open items stand on their own priority):

- [x] **arch-r2-01 (blocker) — DESIGN SHIPPED 2026-07-12:**
  `docs/CONTAINER_EXECUTOR_DESIGN.md` (Fable-handoff session) — seam
  (`_run_subprocess_safe` wrap), image + dedicated container auth volume
  (host `~/.claude` rw-mount identified as an escape vector), mount map from
  fence roots, `executor.container` config family, sandbox.py retirement,
  chunks C1–C4 queued in MILESTONES -5. Implementation open; the BLOCKER
  (decision-without-vehicle) is cleared.
- [x] **docs-r2-01 SHIPPED (README half 2026-07-10, remainder 2026-07-12):**
  "Optional Services" + Telegram-service + Compatibility sections rewritten
  to the app-not-daemon posture (one-shot `maro heartbeat`,
  `maro-bootstrap services` prints hook instructions, you supervise
  long-running listeners yourself); all `sudo cp
  ~/.maro/workspace/deploy/systemd/...` instructions gone.
  **Supervision-convergence remainder SHIPPED 2026-07-12** (MILESTONES -5
  item 1): `deploy/systemd/maro-heartbeat.service` +
  `maro-observe.service` deleted (contradicted the "no unit files" posture;
  the observe unit exec'd an already-archived stub) and
  `scripts/heartbeat-ctl.sh` deleted (a Maro-managed start/stop/restart
  wrapper around `--loop` — a third, inconsistent supervision story next
  to the decided one); `skills/arch-platform.md` lifecycle-management
  section + file-map row rewritten to the one-shot posture; dangling
  comment reference in `scripts/viz-ctl.sh` fixed (ops-r2-04, docs-r2-04
  CLOSED). **ops-r2-01/02 SHIPPED 2026-07-12** (Jeremy's 2026-07-12
  decree): `heartbeat.autonomy` flipped back to the code default (False)
  in the runtime workspace config (was left ON from the 07-09/10 burn-in
  trial) — off until the direct-use transition; `scripts/host-check.sh`'s
  stale-heartbeat check re-aligned from a 900s (15m) threshold — which
  assumed a recurring 30-min loop nobody installed on this host and was
  firing FAIL (and paging Jeremy's Telegram daily via
  `host-check-notify.sh`'s cron) for a non-incident — to a 7-day
  no-tick-in-N-days threshold (`MARO_HEARTBEAT_MAX_SEC` default
  604800; existing >30d skip-as-design-state branch kept, now the
  second tier); `docs/HOST_MONITORING.md` §4 updated to match (currency
  rule). Live-verified: `bash scripts/host-check.sh` now PASSes the
  heartbeat check (last beat ~2.7d ago, well under the new 7d bar) instead
  of the daily FAIL it was producing.
- [x] **cs-r2-01: SHIPPED 2026-07-10** (promotion-time guard + loader-side
  backstop) — moved to BACKLOG_DONE with context.
- [x] **ops-r2-05 SHIPPED 2026-07-10:** proc_lock._run_dir fallback now
  mirrors config.workspace_root()'s env resolution (MARO_WORKSPACE /
  OPENCLAW_WORKSPACE / WORKSPACE_ROOT) before Path.home() — a partial
  config stub can no longer stamp the real workspace's heartbeat.pid.
  Tripwired in tests/test_proc_lock.py (reproduces the exact stub shape);
  live-verified: full test_heartbeat.py run leaves the real pidfile mtime
  untouched.
- [x] **data-r2-01 SHIPPED 2026-07-10 (SF-2 residual, r2 blocker #2):**
  agenda-lane learning is now verdict-aware. Chose "move" over "re-stamp"
  (tiered-lesson dedup/reinforcement makes un-recording a wrong lesson
  unsafe — a celebratory lesson can reinforce a pre-existing good one).
  handle.py passes `defer_learning=True` → finalize skips lesson extraction
  + skill crystallization/synthesis for "done" runs (stuck/failed still
  learn immediately — their status is honest) → post-closure hook calls
  `finalize_deferred_learning()`: `extract_deferred_lessons(loop_id)` reads
  each outcomes row back verdict-included (restart attempts too, via
  extra_loop_ids; idempotent — rows with lessons skip), and skills
  crystallize only when the verdict isn't a judged False. Knowledge write
  deferred with the lessons. Direct run_agent_loop callers (heartbeat,
  prereq, handle_queue, cli) don't defer — no closure runs there, so
  deferral would orphan their lessons. 9 tests in test_verdict_learning.py.
- [x] **data-r2-02: LIVE BATCH RAN 2026-07-10** (9 finalizations, $2.74).
  Loop proven end-to-end: evolver fired at cadence 10 (first production run
  ever — SF-1's zero-hours gap now has hours), skills-lite promoted its
  first skill (changelog_digest, through both injection gates), tri-state
  verdicts flowed (4 success/True, 3 partial, 1 done-unverified/None,
  1 done-not-achieved/False — closure CAUGHT a real worker fabrication:
  claimed 120 archive entries, count was wrong), #18 CLI verdict-parity
  exit codes worked. Follow-ups spawned below.
- [x] **batch-01 SHIPPED 2026-07-10** (Jeremy adjudicated same day: "production
  all the time" + debug switch): dev/prod `environment` split removed entirely —
  guardrail gate now keys off `evolver.auto_apply` (default False =
  held_for_review; env override kept); `apply_suggestion(id, manual=True)` on
  the CLI review paths bypasses the hold (the review IS the gate) but never the
  injection guard or skill test gate; `debug` config key added (log verbosity
  only, `MARO_DEBUG=1` env override) — behavior is never environment-dependent,
  only observability is switchable. `environment` key deleted from DEFAULTS.md
  + workspace config. → BACKLOG_DONE.
- [x] **batch-02 SHIPPED 2026-07-10:** evolver `_verify_post_apply` pytest
  now runs `nice -n 15` + `taskset -c ${TEST_CORES:-0,1}` (test-safe.sh
  posture; tools probed, degrades gracefully off-Linux), timeout 300→900s
  to buy back the throttling. ops-r2-05 fixed first so the verify pass no
  longer re-stamps the real pidfile. Tripwire:
  test_verify_post_apply_runs_throttled.
- [x] **batch-03 SHIPPED 2026-07-10:** skills-lite dangerous-pattern scan
  now scoped to markdown CODE regions (fenced blocks incl. unterminated +
  inline spans; `run_curation._code_regions`) — prose mentioning `open(`
  is instructions, not payload; prose threats stay with the cs-r2-01
  injection_guard gate. funnel_report specimen shape covered by
  test_code_substring_in_prose_promotes. sandbox.is_skill_safe (repo-skill
  lane) deliberately untouched.
- [x] **hist-r2-02 SHIPPED 2026-07-13:** hist-05 owner ask ("run this
  prompt with this persona" as a first-class pattern) dropped for the
  third time — in neither the decision brief nor any backlog. Shipped as
  the generalized `persona:<name>:` prefix + `--persona` CLI flag
  (replacing the hardcoded `garrytan:` shortcut), with graceful
  fallback+warning on unknown persona names (fixed a latent silent-failure
  bug in the process). This entry ends the drop pattern.
- [x] **docs-r2-02 SHIPPED 2026-07-10 (de-document):** the 4 dead keys
  (research_step_model, max_steps, always_skeptic, notify_on_complete)
  moved to commented not-yet-wired blocks; header reader-list corrected to
  the verified set (yolo, default_model_tier, ralph_verify, quality_gate,
  quality_gate_action / mcp_servers); notify_on_complete redirects to the
  YAML notify.* lane. Standing rule stated in the file: uncommented key =
  has a reader.
- [x] Cosmetic sweep rides the above chunks — SHIPPED 2026-07-10: docs-r2-03
  (CONFIG.md overlay NOTE corrected), docs-r2-05 ("alert Jeremy" → "alert
  the operator"), docs-r2-06/hist-r2-04 (PUBLISH_CHECKLIST row in INDEX.md),
  land-r2-01 (trusted-operator boundary stated in README safety section),
  land-r2-02 (pymaro disambiguation blockquote, phrasing from the live-batch
  fd5d8597 draft), hist-r2-03 (SF-13 rule in CLAUDE.md end-of-chunk section).
  Also SHIPPED 2026-07-10: data-r2-03 (atomic_write preserves existing mode,
  new files get 0666&~umask instead of mkstemp's 0600; tests in
  TestAtomicWritePerms; live specimen changelog_digest.md chmod'd 644).
  hist-r2-05 SHIPPED 2026-07-10 (ingest-stats keys now parent/name — basename collision across the three lessons.jsonl sources gone); arch-r2-02 SHIPPED 2026-07-10 (Remaining list corrected);
  ops-r2-03-replaced
  pidfile litter (harmless — flock is the mutex; stale file is cosmetic).

### 22. Capabilities catalog + blank-slate skill set (2026-07-10, Jeremy)

Jeremy (in-session, riffing off the car ask "where can I get non-ethanol
gas in or around Manti, Utah?"): "we need some real test cases to list,
maybe in the readme, certainly in some kind of capabilities doc... We
should collect more and different examples, both simple and complex. For
better testing, learning, and overall initial capability of the system and
its skills." Plus: "I'd love a blank slate with a small-ish but useful
pre-installed list of capabilities that we think might be the right target
(and maybe a shared and trusted directory to pull from at a later time,
crowd-sourced or not)."

- [x] **docs/CAPABILITIES.md shipped 2026-07-10:** living catalog (5 tiers,
  verified/target/aspirational grounding rule, Manti as the canonical
  simple case + UX contract), blank-slate draft target list, add-an-example
  protocol. README "what it looks like in practice" block + INDEX row.
- [x] **Ran the canonical case live 2026-07-10 (both lanes; results in
  CAPABILITIES.md canonical-case section):** natural routing sent it NOW
  (0.85 conf) and FAILED the contract — answered from model knowledge with a
  "here's how you could find out" list (passenger-does-the-steps
  anti-pattern; router has no needs-live-external-data signal). Forced
  `--lane agenda` PASSED on content — research-brief.txt with a bottom-line
  answer (Maverik Ephraim, 7.3 mi, 24h), per-station confidence, live
  store-page verification, stale-source dissent — but blew the envelope:
  7 steps, ~24 min, $2.47 (cost hard stop; MODEL_POWER→subprocess churn).
  Verdict: capability verified, delivery not. The errand-research skill's
  real spec = (a) routing: NOW must detect needs-live-data and escalate or
  research inline; (b) envelope: ~1–3 min / cents, not research-project
  scale. (Side casualty: outer watchdog killed the run mid-finalize, so no
  outcome row was recorded — learning pipeline never saw the run.)
- [x] **Blank-slate pre-installed skill set SHIPPED 2026-07-13:**
  `skills/changelog_digest.md`, `skills/doc_summary.md`,
  `skills/errand_research.md`, `skills/research_brief.md`,
  `skills/watch_condition.md` — the "small-ish but useful pre-installed
  list" Jeremy asked for above. Found and fixed a real gap in the
  process: `changelog_digest` existed only in the live workspace and had
  never actually shipped to fresh installs.
- [ ] **Manti cost-envelope residual (routing, answer quality, cuts-first,
  and boot-tax mechanics shipped; Jeremy decreed NO new errand lane):**
  - **NOW self-verdict must catch non-answers:** "the goal asked *where*,
    the answer contains no *where*" → not-achieved. The model's own "I
    don't have real-time access" admission is the cheapest signal. Today
    that answer sailed out as done-unverified (verdict absent).
    **SHIPPED 2026-07-11 (a1f472f):** `_NOW_VERIFY_SYSTEM` now judges
    non-answers (different-question answers, how-to-find-it guidance,
    missing asked-for specifics) as fulfilled=false.
  - **NOW→AGENDA escalation on not-achieved verdict:** same shape as the
    quality gate's tier escalation, but lane escalation. Nothing
    self-escalated; the good run was manually forced.
    **SHIPPED 2026-07-11 (a1f472f):** `now_lane.escalate_on_not_achieved`
    (default OFF fresh installs per no-silent-spend; ON this box) —
    not-achieved NOW re-routes to AGENDA with the failed quick answer
    attached as ancestry context; the NOW attempt stays recorded
    (incomplete). Task-path only (self-verdict scope); force_lane wins.
    Interactive NOW still skips the self-verdict entirely (raw-speed
    contract) — the interactive routing gap remains the classifier
    needs-live-data signal. **That signal SHIPPED 2026-07-12 →
    `docs/history/2026-07-12-routing-and-probe-synthesis-design.md` Part A** (`needs_live_data`
    schema field + capability override, 8ed0a09 template; see MILESTONES -5).
  - **Qix-cuts decompose shape (taste/discretion in planning, NOT a new
    lane):** 0–4 cheap narrowing steps that shrink the space (chain
    prior → locations → verify 2-3 candidates), then bounded work inside;
    re-draw on new info (replan exists). Plan size proportional to the
    ask — the $2.47/24-min run brute-forced the full rectangle. Rides
    with the queued #5 planning-depth shadow thread; the rectangle idea
    is already captured in REASSESS_LINEAGE / constraint-orchestration
    docs, only scope-text injection shipped.
    **v0 SHIPPED 2026-07-10 (068eddd)** — `planner.cuts_first` (default
    OFF; ON on this box): draw_cuts() = constraints-with-basis + 0-2
    probes + bounded remainder; probes+[boundary] marker replace the
    full-plan commit; boundary expanded mid-loop WITH probe evidence
    (milestone expansion stays blind); director replan may re-draw once
    (cap 2). What v0 defers: iterated cut-probe-cut deeper than 2 rounds,
    re-draw as first-class (constraint-invalidation trigger), the #5
    dispatch-level planning-depth shadow field (separate, still queued),
    cuts on the NOW lane (agenda decompose only). Acceptance = Manti
    canonical case live run vs the $2.47 baseline.
    **Acceptance run 8177541b (2026-07-10): content PASS** (best Manti
    deliverable to date, 11/11 steps, run stayed inside the $2.00 budget
    where the baseline hard-stopped at $2.47; tokens 2.94M vs 4.84M) but
    wall ~28 min vs ~24 — envelope anatomy: 819s in steps (465s of that
    one adversity-heavy verify step fighting Cloudflare/DDG/Overpass
    blocks — productive, replan handled it) + 852s BETWEEN steps, of
    which 454s was 11 local-qwen ladder calls at ~41s each, all passing.
    Two fixes shipped off this run same day: closure brittle-grep
    evidence attachment (2830f48 — the run was false-negatived by a
    literal `grep 'Station Name'` against a `| Rank | Station |` header)
    and the ladder latency breaker (4957448, validate.local_max_latency_ms).
    **Clean post-fix Run 3 completed 2026-07-11 (`5126986b`):** 6 steps,
    16m43s, $1.52, status done; its own closure reported 0.95, 5/5 checks,
    and zero gaps. This closes the stale “in flight” envelope record. Evidence
    attribution stays separate: Run 2 established content quality, Run 3 the
    post-fix cost/time envelope, and Run 4 (2026-07-12) the routing fix. The
    promised ~1–3 min/cents envelope remains open. Canonical measurements live
    in `docs/CAPABILITIES.md`.
  - **Micro-step boot tax (next envelope lever, found in 8177541b):**
    10 of 11 steps averaged ~35s each and several were read-only
    micro-steps ("Read artifacts/source-log.txt...") — each step pays a
    fresh `claude -p` session boot, so expansion emitting 3 reads + 1
    format + 1 write costs ~3 boots of pure overhead. Boundary/milestone
    expansion should fold reads into the step that consumes them (the
    step's cost model — ~30s fixed overhead per step — is honest context
    for the expansion prompt, not a taxonomy). Candidate after clean-run
    numbers land. → Prompt half SHIPPED 2026-07-11 (1f2d0d9): NO ORPHAN
    READ STEPS block in DECOMPOSE_SYSTEM with the measured cost model;
    exec/analyze HARD RULE untouched; folded steps survive _shape_steps
    ("read" not in _EXEC_KEYWORDS). Live A/B = next expansion-bearing run.
    2026-07-11 boot-tax anatomy + first trim: trivial `claude -p` call
    measured 6.4s → 2.7s after `--strict-mcp-config` (user-level Google
    Drive MCP handshake was ~3.7s on EVERY subprocess boot; shipped
    395e71c). Remaining per-step overhead: CLI boot ~1.5s + ~21K tokens
    of re-injected step context + agentic turns.
  - **Session-reuse spike (Jeremy 2026-07-11, "write steps down in a
    file, instruct the session to read it, then clear the session and
    continue"):** headless `claude -p --resume <session-id>` /
    `--fork-session` exist, so one session per boundary segment is
    mechanically possible — steps within a segment share the session
    (no context re-injection, no boot, warm server-side cache); the
    session ROTATES at cut boundaries where distillation already
    happens (probe findings → evidence block). Rotation IS the "clear":
    fresh session seeded from the distilled state file. Today's design
    is the all-clear extreme (new session + ~21K re-inject per step);
    per-RUN sessions are the other ditch (the 1.4M-token step shows
    where monotonic context ends). Per-segment is the middle the cuts
    machinery already draws the lines for. Related:
    [[recursive-orchestration-memory]] CAG direction. Spike = measure
    resume vs fresh on a 5-step segment before designing anything.
    **Jeremy 2026-07-11: parked as a standalone investigation** ("maybe
    we add that to the backlog for investigation instead of an add-on
    to this session") — pick up as its own measured spike, not as a
    rider on other work.
    **Measured spike completed 2026-07-14:** two valid counterbalanced
    five-step Haiku runs, with arm/per-run cache isolation and 10/10
    correctness in each arm, cut wall time 31.6% (49.861s fresh → 34.124s
    resumed) and spend 75.0% ($0.915716 → $0.228808). Steps 2–5 were 38.0%
    faster after the context-bearing first call. The failed initial protocol
    is retained rather than laundered: loaded wording triggered prompt-
    injection refusals and the first checker mishandled fenced JSON. Reproducer,
    raw-output location, caveats, and decision are in
    `docs/history/2026-07-14-session-reuse-spike.md`.
  - [x] **Per-boundary session-reuse production prototype — SHIPPED 2026-07-14,
    decision KEEP OFF.** Prototyped with durable session identity/fresh
    fallback, exact model/persona/cwd/worktree/permission/tool-config
    compatibility, preserved per-step transcripts + cost/provenance/
    verification, forced rotation at expansion/replan boundaries, and
    discard-on-confusion/failure, then adversarially reviewed and hardened
    (missing-session fallback marker sanitization, audited-prior-state deltas,
    hostile-content rotation, checkpoint shape validation). Two counterbalanced
    fixed-Sonnet real-goal runs: correctness tied (4/4 both arms), cost 27.9%
    lower in the treatment (n=1, not yet a repeatable result) but no executor
    speed benefit and 5.7% slower end-to-end. `executor.session_reuse` stays
    **default off** — a cost hypothesis, not a demonstrated latency win.
    Decision + protocol + hardening detail:
    `docs/history/2026-07-14-session-reuse-prototype.md`.
  - **Self-speedup run adjudicated 2026-07-11 (fd483efb-stout-ember,
    $1.14, 47min, 7 steps + 4 ranked proposals + a genuinely good
    adversarial self-verification that caught its own 65s double-count).
    Verdict on the 4 proposals after code verification (2 had false
    premises — verify-before-fix strikes again):**
    - P1 async step-transition I/O (127-254s claimed): premise PARTIAL.
      The between-step path IS synchronous, but the only LLM work in it
      is the validation ladder — lesson recording/recall/planning are
      NOT there (they're post-loop / in decompose). The ~47s/step pool
      it wanted to async away was dominated by the ladder's cold-reload
      tax (35-42s), already fixed via keep-alive 10m + breaker + MCP
      trim. Everything else is µs-scale appends and local FS diffs.
      → NOT actionable as designed; RE-MEASURE a post-fix run first.
    - P2 concurrent sub-dispatch in boundary expansion (80-160s):
      premise FALSE — expansion is ONE decompose() call, no per-sub-step
      calls exist to parallelize. BUT the real cost inside it is multi-
      plan best-of-3 sampling (planner.py:681-718): 3 independent
      full-plan candidates generated serially. Parallelizing THOSE is
      the legitimate descendant of this proposal (~2/3 of decompose
      wall-time at boundary events, 1-2/run). Deferred: 3 concurrent
      `claude -p` on this box = memory/CPU contention; candidate for
      the new machine.
    - P3 parallelize pre/post-loop lifecycle (40-80s): TRUE premise,
      dependencies now mapped: clarity→scope dependent only when
      clarification occurred; closure→deferred-learning dependent;
      quality gate INDEPENDENT of closure verdict (re-derives from
      goal+steps) → closure ∥ quality-gate is the one safe pair
      (~30-60s). Same subprocess-concurrency caveat as P2.
    - P4 batch event logging (30-70s): FALSE — write_event is an
      unlocked O_APPEND one-liner, log_event a locked append, no fsync;
      µs-scale. Only pathological flock contention (30s timeout) costs
      anything. Dropped.
    Next envelope action = one clean post-fix run, re-measure the
    between-step pool warm; expected ~47s/step → ~12-15s. Only then
    decide if any concurrency work is still worth it.
    **2026-07-17 answer-first delivery SHIPPED:** deferred learning
    (lessons + crystallization, ~90-120s of subprocess calls) now runs
    AFTER the run_completed notify instead of before it — registered in
    handle.py `_POST_NOTIFY_LEARNING`, drained post-emit, then
    `refresh_run_card_classification` + report re-render restore the
    lesson-consuming card fields (audit-repair contract). The quality-gate
    escalation path drains early (its retry's decompose recalls the failed
    loop's lessons), preserving the closure→learning dependency mapped
    above. User-perceived post-loop tail drops from ~285s to closure +
    curation (~120-160s); remaining pre-notify levers = closure ∥
    quality-gate (safe pair above) and closure checks through the
    hosted-free ladder (unbuilt — judgment-quality tradeoff, don't do
    silently).
  - **Scavenge detector false-positive on URL paths — SHIPPED 2026-07-11
    (080ef51):** root cause was three markup classes in Bash command text,
    not curl+jq output: XML closing tags in worker-written parse regexes
    (`</phoneNumber>`), URL paths in grep patterns (`"/static/js/..."`),
    and `/api` in an echo'd prose label. Fix: `<` joins the _ABS_PATH_RE
    lookbehind, and the Bash scan requires the first path segment to be a
    real local directory (`_plausible_fs_root`) — kills web-root fragments
    while keeping the stale-clone diagnostic (/home exists even when the
    clone doesn't). Structured tool inputs untouched.
  - **Stranded run-card hardening — SHIPPED 2026-07-11 (6a116a2):**
    metadata now stamps the owner pid at create; new sweep phase
    `_backfill_stranded_run_cards` stamps non-terminal status="stranded"
    + ended_at (checkpoint mtime) when the owner is dead (15-min grace;
    pid-less legacy rows need 24h). "stranded" stays visible to
    _find_resumable_runs so `maro resume` surfacing survives; a later
    finalize overwrites with the real outcome. First live sweep
    backfilled 57 legacy null-status runs incl. specimen 51b09271.
  - **Closure behavioral-gap Signal 2 over-fire — FOUND+SHIPPED 2026-07-11
    (c37f42e):** the self-speedup run (fd483efb) had 5/5 checks pass at
    0.98 confidence and STILL got complete=False: its scope failure mode
    "Proposal violates process logic" matched \bprocess\b in
    _RUNTIME_FAILURE_MODE_HINT, demanding a behavioral probe of a
    document-only goal. Signal 2 now corroborates against
    ResolvedIntent.deliverables (all document-shaped → prose keyword is
    noise; any runtime-shaped deliverable keeps the slycrel-go protection
    armed; no deliverables → original conservative behavior). Same run
    also live-proved the 2830f48 evidence attachment: round-1 verdict
    read a brittle constraint-grep failure correctly via
    target_file_content (evidence_attached=1).
- [ ] **Standing habit:** capture real asks as phrased into the catalog
  (car questions, mid-session asks). Real phrasing carries the ambiguity
  synthetic goals launder out; this is also the organic corpus the lesson
  funnel needs (batch-04's answer lives here, not in synthetic batches).
  Latest capture: Jeremy's M1/2014-mini local-LLM viability ask (2026-07-14),
  now a verified Tier-2 row in `docs/CAPABILITIES.md` backed by the formal
  same-corpus bake-off.
- [x] **Blank-slate pre-installed set (design → curate) — DONE, stale
  bullet corrected 2026-07-13.** All 7 draft targets resolved per the
  curated table in `docs/CAPABILITIES.md` §"Blank-slate capability target":
  errand-research/research-brief/doc-summary/watch-condition built as new
  skill files (2026-07-13, this backlog's own #22 residual bullet above),
  repo-digest = `changelog_digest.md` promoted from workspace-only to the
  repo default set (gap found+fixed), ledger-census covered by existing
  `skills/data_analysis.md` (no new file needed), code_review already
  shipped. Nothing left to build against the draft list.
- [ ] **(Vision)** Shared trusted skill directory + cross-instance
  learning share — see Vision section entry.

### Benchmark/eval mission isolation — DONE 2026-07-14 → BACKLOG_DONE

The measurement-class work supplied a reliable benchmark boundary. Normal
eval cells now own fresh projects; the direct-Director worker-slice harness
owns fresh retained workspaces. Full evidence and the historical m3 caveat are
archived in BACKLOG_DONE and `docs/history/2026-07-08-worker-slice-ab.md`.

### host-check.sh alerting — DONE 2026-07-09 → BACKLOG_DONE

Telegram (notify.command lane) + daily cron 08:05, via new
`scripts/host-check-notify.sh`; failure path live-proven before scheduling.
Full context in BACKLOG_DONE.

### `orch.py` legacy loop — DEPRECATED 2026-07-09 → BACKLOG_DONE

Jeremy confirmed `maro tick`/`loop`/`plan` unused → stderr deprecation
warnings + docstrings + tripwire test shipped. Residual (rides the Tier-4
subpackage move, not a standalone item): remove the trio + promote/rename
the path/NEXT.md layer as the real `orchq`/paths subsystem. Full context in
BACKLOG_DONE.

### Install trial residuals (2026-07-09 docker clean-machine trial)

First-ever install on a non-dev machine (debian-slim container, non-root, no
keys). The blocker it found — pip installed ZERO modules (`packages.find`
can't see a flat module layout; every console script crashed
ModuleNotFoundError, masked on this box by PYTHONPATH=src) — is FIXED
(explicit `py-modules` list + `tests/test_packaging.py` census tripwire).
Working after the fix: pip install (pyyaml now a mandatory dep — without it
config.yml was *silently ignored*), `maro-bootstrap install` (dirs + starter
config template + honest smoke-fail), `maro-doctor` cold-machine truth
(15/19, the 4 fails all real), graceful no-backend refusal with an
actionable message.

**E2E goal run (same day, mounted claude CLI as subprocess backend): PASSED.**
Cold container, real goal ("write a 3-line haiku about fresh installs to
haiku.txt") through `maro-handle` → agenda lane, 2 steps, `status=done`,
`goal_achieved=True` (verdict confidence 1.0), artifact on disk with correct
5-7-5 content, run metadata + per-run report written. ~5.6 min wall clock on
the subprocess lane; the llm.py backend-order warning ("Opus via subprocess
is unreliable for long multi-step work") printed as designed. Residuals,
none blocking:

- [x] **Curated skills (`skills/*.md`) aren't packaged** — DONE 2026-07-09.
  Shipped as package data, not bootstrap seeding (seeding would blur the
  workspace-tier semantics — shipped defaults must stay "repo" tier so
  evolved workspace overrides win and upgrades refresh defaults). New
  `src/maro_assets/` real package whose `skills/` + `personas/` are
  symlinks to the top-level dirs; setuptools follows them at build time
  (proven on wheel AND sdist — real files land in both). Loaders fall back
  when the repo layout is absent: `skill_loader.SKILLS_DIR` and
  `PersonaRegistry` resolve to `maro_assets.assets_dir(...)`. Live-proven
  in a clean venv: 14 skills + 24 personas load from site-packages;
  doctor's curated-skills row goes green. Census tripwires extended
  (`tests/test_packaging.py`): declared-packages exemption in the flat
  census, symlink-integrity + glob-coverage checks, assets-vs-repo parity.
- [x] **Service templates written into the venv** — DONE 2026-07-09.
  `config.deploy_dir()` was package-relative (`Path(__file__).parent.parent`),
  which lands in `<venv>/lib/.../site-packages/..` under a pip install —
  root-unwritable, and a strange place to look for a systemd/launchd file.
  Now `workspace_root() / "deploy"`. README/SECURITY_MODEL.md service-copy
  paths corrected to match. Test: `test_deploy_dir_is_workspace_relative_not_package_relative`
  (`tests/test_phase21.py`).
- [x] **`maro-handle` with no backend dies with a raw traceback** — DONE
  2026-07-09. `handle.main()` now catches the `RuntimeError` `build_adapter()`
  raises (already an actionable "set X or install Y" message) and prints it
  as `Error: ...` to stderr with exit 1, instead of a full traceback on a new
  user's first command. Test: `test_cli_no_backend_prints_clean_error_not_traceback`
  (`tests/test_handle.py`).
- [x] **`run_smoke_test` docstring says dry-run; it makes a real NOW-lane
  LLM call.** DONE 2026-07-09 — made honest, not behavior-changed: a real
  live call is the right smoke test (proves the configured backend actually
  works, which a canned dry-run response can't), so the docstring/CLI-help/
  module-docstring were corrected instead of adding `--dry-run`. Tests:
  `tests/test_bootstrap_smoke.py`.
- [~] **E2E run left a second haiku.txt at `$HOME`** — INVESTIGATED
  2026-07-09, unreproduced; demoted to watch-item. The trial container's
  evidence (captain's log FENCE rows) was ephemeral and is gone. Facts
  established: (1) it was NOT legit widening — `goal_declared_roots`
  (artifact_check.py) only widens on absolute/`~` paths; "haiku.txt" is
  relative, so no FENCE_EXTENDED could have fired; (2) if real, it was a
  detection hole by design — `detect_out_of_fence_access` scans only
  absolute paths, assuming cwd-binding fences all relative writes; any
  LLM call lane with cwd=$HOME breaks that assumption. Prime suspect was
  a utility call (decompose/closure) with inherited cwd + real tools —
  BACKLOG #16 (1416a07, same day) stripped tools from exactly those call
  sites, likely mooting it. Local reproduction post-#16 (isolated
  workspace, cwd=fake-home, same goal via installed wheel, subprocess
  lane): haiku.txt landed ONLY in the project dir, status=done. Watch:
  next docker/clean-machine trial must persist the workspace and grep
  FENCE/SCAVENGE rows before teardown.
- [x] **`metrics.spend_today()` line-scans all of step-costs.jsonl** — DONE
  2026-07-09. New `_reverse_readline()` scans backward from EOF in chunks
  (no full-file load) and stops at the first pre-midnight row —
  `record_step_cost` already appends under `locked_append`, so entries are
  chronological and today's are always the tail. Test proving the early
  stop: `test_spend_today_stops_scanning_at_first_old_entry`
  (`tests/test_budget_gate.py`, 5000-entry fixture, asserts <50 lines pulled).
- [x] **`config.load_config` caches with no mtime check** — DONE 2026-07-09.
  Cache key now includes both config files' mtimes, so a long-running
  heartbeat/daemon picks up an operator's edit (e.g. raising
  `budget.daily_usd` mid-refusal) on the next `config.get()` call, no
  restart or explicit `reload=True` needed anywhere. Test:
  `test_cache_auto_invalidates_on_file_mtime_change` (`tests/test_config.py`).

### Initial-public-release launch content + learning/sharing (Jeremy, 2026-07-09 — scope decree; "1.0" relabeled 2026-07-15)

Decree: "learning and sharing needs to be part of the official first
release" (full quotes in GOAL_BRAIN Decisions 2026-07-09). Three items,
also listed as MILESTONES -3 (e)/(f)/(g) (arc closed 2026-07-15; items
stand on their own). Sequencing constraint that survives the de-1.0 pass:
(g) needs design sign-off before any public release.

- [x] **(e) Default personas + skills — SHIPPED 2026-07-09** (survey:
  `docs/audit-2026-07/persona-skill-survey.md`; ship set: e0811c7 +
  gitignore-recovery c2609da). Curated catalog = 13 personas (9 catalog
  incl. NEW assistant + data-analyst, 4 infrastructure) + 10 skills (6
  existing + deep_research/web_extract/document_process swiped per
  license review + monitor_diagnose BUILT BY MARO in dogfood run
  6dfaec5d, hand-graduated). Ships as `maro_assets` per-file-symlink
  package data; SHIPPED manifest canonical, census tripwire
  (tests/test_packaging.py) enforces manifest↔symlink↔never-ship
  (jeremy/poe/companion/garrytan/psyche-researcher + test fixtures stay
  out of the wheel). garrytan routing entry removed (named-person
  likeness + Opus cost footgun); review pattern de-personified into the
  code_review dogfood goal. **Landmine fixed en route:** a blanket
  `skills/` gitignore had silently kept the entire skills half of the
  ship set out of e0811c7 — a fresh clone would have shipped 0 skills;
  recovered in c2609da with a don't-reintroduce note in .gitignore.
  Ship set later grew to 11 skills: report_synthesize BUILT BY MARO in
  dogfood run 59a9fdd7, hand-graduated same day.
- [x] **(e) remainder: adversarial-review ship skill — CLOSED
  2026-07-09, same day** (Purgatorio hist-06 — reopened so Jeremy's
  decree "or a flavor of it, should probably be one of our skills we
  ship with" didn't fall through the closed checkbox above; satisfied
  hours later). Dogfood run 4's code_review skill (0baac0ab) graduated:
  attack-your-own-candidates pass + evidence-gated confirmed/speculative
  split + red-herring failure mode — the decreed pattern, Maro-built.
  Verified by hand: 3/3 planted bugs confirmed with reproductions
  (re-run independently), red herring correctly refuted. Ship set now
  12 skills. Closure verdict false-negative @0.35 ("fixture.diff
  missing" — it exists; wrong-cwd verifier class, 4th specimen).
- [x] **(f) Self-learning involved in the launch build-out — COMPLETE
  2026-07-09.** 5 orchestrator-builds-it goals via `maro-handle`,
  learning ON (pre-req: data-01 fixture purge executed first so learning
  doesn't crystallize April test junk). **Scorecard (every claim
  verified against artifacts by hand, not worker self-reports):**
  - **5/5 runs produced correct deliverables.** Run 1 monitor_diagnose
    (correct root-cause diagnosis, quoted evidence); run 2 daily_brief
    (working skill + helper script, 2 real briefs, 20-entry state);
    run 3 report_synthesize (planted-contradiction fixture set,
    Conflicts section correct); run 4 code_review (3/3 planted bugs
    confirmed with reproductions — re-run independently — red herring
    correctly REFUTED, not reported); run 5 assistant shakedown
    (planted urgent escrow item ranked #1; adversarial review caught a
    real phantom-conflict wart).
  - **3 skills graduated into the ship set** (monitor_diagnose,
    report_synthesize, code_review) — 3 of 12 shipped skills are now
    Maro-built. Run 2's daily_brief is correct but not shippable in
    pure-markdown skill format (needs its sibling helper script) —
    bundled-assets is an open design gap, noted for (g)/later.
  - **28 substantive lessons crystallized** (lessons.jsonl 188→207 net
    of consolidation; all 28 verifiably dogfood-born — fixture-planting
    methodology, trigger-collision diffing, format-compliance
    discipline). skills.jsonl +25 events; medium tier 5, long tier 3.
  - **Closure-verdict noise, the honest number: 4/5 false negatives**
    (@0.25/@0.15/@0.3/@0.35), all the same class — closure's verifier
    resolves paths/privileges from the wrong environment (wrong cwd,
    unprivileged journalctl) and fails the goal on its own tooling
    error. 1/5 agreed (@0.95). The adversarial layer, by contrast, was
    2/2 precise. Feeds SF-2 / item (b): learning must not trust raw
    closure verdicts until the verifier-environment bug is fixed.
  - Also caught en route: persona router has no meta-goal awareness
    (run 1 → health-researcher @0.892 on goal-text keywords);
    ralph-verify + MISSING_INPUT escalation behaved exactly right on
    run 4 attempt 1 (refused to fabricate an unreachable input); the
    harness claim-probe itself false-flagged run 4's existing files
    ("cart.py not found" — they're under output/repro/), the same
    wrong-cwd class as closure.
- [x] **(g) Portable/shareable learning — design + migration path.**
  **DESIGN SHIPPED 2026-07-09 → `docs/PORTABLE_LEARNING_DESIGN.md`**;
  **§8 RATIFIED 2026-07-12 (Jeremy, all 8 as written — GOAL_BRAIN
  Decisions).** 1.0 slice = its §7 chunks 1–4: migration runbook +
  doctor checks, provenance fields + `scrub_identifiers`, `maro-pack
  export/seal`, `maro-pack import/adopt`. **All 4 chunks SHIPPED
  2026-07-13 (Sonnet)**, closing the loop end to end; see MILESTONES
  item 7 for full per-chunk detail. Adversarial review across the
  combined chunk 1–4 diff (3 Codex reviewers) SHIPPED same day — 3
  high + 6 medium/low findings fixed (`--target` scoping,
  artifact-sha256 tamper check, path-traversal guard, malformed-row
  containment, provenance nesting, `adopt()` TOCTOU, dict-key
  scrubbing, `maro-import` action field, skill-tier reset); full
  verdict in `docs/history/2026-07-13-adversarial-review-portable-learning.md`
  (gitignored, box-local, not in git history).
  **Known-gap deferred from that review:** artifact filenames /
  manifest `path` strings / `REVIEW.md` headings are not
  identifier-scrubbed — only artifact *content* goes through
  `scrub`/`scrub_identifiers`. A skill/persona filename carrying a
  username or hostname ships unredacted; the human review gate is the
  only backstop today. Not fixed because the correct fix is a
  filename-rewrite decision that also changes how `adopt()` derives
  live filenames from quarantined names — revisit if a real case shows
  up rather than speculatively.
  Original scope:
  Machine migration and bootstrap-sharing for new users; internet
  hive-mind explicitly out of scope (opt-in someday, "could be cool").
  Doors already built, name-checked so design starts from them:
  `maro-import` (cross-workspace merge of runs + memory ledgers, proven in
  the hermes trial), JSONL event log as source of truth (stores
  interchangeable on disk by test), Stage-5 = regenerable-from-language
  decision (2026-07-09), workspace resolution order, `secret_scrub`
  (single-source scrubber — sharing MUST pass through it), bi-temporal
  columns + decay-trust-never-data (imported artifacts should likely
  arrive contested/hypothesis-trust, not full trust — same shape as rule
  contestation). Design must settle: the shareable unit (skills/personas/
  lessons/rules vs raw runs), trust+provenance on import, privacy
  scrubbing guarantees, format versioning. Vision anchor: Maro is "a
  communication platform … in addition to an action generator."
- [~] **(h) Backend-error resilience + auto-resume — DESIGN DONE
  2026-07-09** (`docs/BACKEND_RESILIENCE_DESIGN.md`, proposed-design): 6
  error classes replacing the two substring predicates (two live traps
  found: Anthropic credit-exhaustion is a 400 that matches neither
  predicate and dies raw; OpenAI insufficient_quota is a 429 that retries
  futilely), messaging on all four channels, resume unit = step
  (at-least-once + guards), recommended minimum 1.0 slice = classify+message,
  checkpoint-into-run-dir, stranded-state sweep + manual `maro resume`.
  9 provisional decisions greppable as "DECISION (provisional)" — Jeremy
  review wanted on: billing-failover default (**RATIFIED OFF 2026-07-16** —
  "billing default should be off, yes"; billing-class errors never fail
  over to another paid backend without opt-in, event always fires),
  1-auto-resume cap (**NOT ratified 2026-07-16** — "likely right decision
  is not binary"; discussion wanted on graduated shape: per-error-class /
  cost-budget / per-goal consent — see design doc annotation),
  resume-surface (CLI vs notify — rec notify now that the ops channel
  exists; provisional, unobjected), depth-cap inconsistency (4 / <3 / 2 —
  **RATIFIED + SHIPPED 2026-07-12**, unified to `loop_types.MAX_RESTART_DEPTH
  = 3`, see MILESTONES -5 #3).
  **Minimum 1.0 slice SHIPPED 2026-07-09 (slices 1+2+3):** slice 1 =
  `llm_errors` classifier (6 classes, actionable messages, wired through
  FailoverAdapter + doctor; 2daa1b5); slice 2 = checkpoint-into-run-dir
  (contextvar run-dir placement + legacy fallback + newest-first scan,
  `in_flight` {index, started_at, pid} marker written pre-step/cleared
  post-step, call-seq rebuild from disk; dc74e19); slice 3 =
  stranded-state sweep on heartbeat tick (dead-PID DOING revert via
  `.doing_pids.json` sidecar, resumable-run detection → `stranded_run`
  notify event, default-on) + manual `maro resume <loop_id>` (refuses
  complete/live/finalized; FS-diff since-crash context injected into the
  resumed step) + `maro-doctor --live` opt-in backend probes (fa8fe40).
  21 new tests (test_checkpoint_rundir, test_stranded_sweep).
  **Auto-resume deliberately deferred** (box crash-loop history; manual
  resume proves the path first; the session-protocol arc raises its
  priority — a dead run behind a network edge is invisible, see SP entry). Original ask: Research + design pass on the errors an end user will
  actually hit: token/rate limits, auth expiry (`/login`-class issues, key
  invalidation), context-window overruns, network blips — and
  **auto-resuming interrupted work**. "That seems like a sharp edge that
  will kill an end user's enthusiasm." Known evidence already on file:
  project_bugs_found memory ("no rate-limit recovery", Polymarket sprint);
  the hermes-trial adapter timeout that left goal-2 work half-committed at
  step 7/10; model-lane contention was *accepted* for this box (2026-07-02,
  Jeremy's own subscription) but is a UX cliff for strangers. Doors:
  `FailoverAdapter` backend order, director restart/continuation_depth,
  navigator fail-open-to-pipeline (the idunno-chain fix), run dirs +
  record-mode capture (the state needed to resume exists on disk). Design
  should decide: detect-and-classify (which errors are retryable vs
  auth-actionable vs fatal), user messaging (actionable, not tracebacks —
  the no-backend fix is the pattern), and resume semantics (what "pick up
  where it died" means per lane).

- [x] **(i) Restart-depth-cap coverage — investigated 2026-07-13, finding is
  bigger than the original framing (was: "test_depth_cap_unified.py's
  handle.py tripwire is source-shape coupled, not behavior coupled", low,
  adversarial-review skill first run 2026-07-12, Architect lens).**
  **RESOLVED 2026-07-13** for the actionable half (the unguarded cross-call
  continuation mechanism) — resolved as **"check-in and continue," NOT a hard
  cap**: per Jeremy's decree the goal must keep running (ralph-style), so no
  refusal was added. See `docs/RECURSIVE_CHECKIN_DESIGN.md` (SHIPPED banner)
  for the implementation. The handle.py single-shot-gate observation below is
  a documentation nuance, not a bug requiring a fix (the gates are
  intentionally single-shot; mechanism (1) is already capped and was
  explicitly out of scope). Picked
  this up meaning to just add the behavioral test the original finding
  asked for ("drives handle.py's actual restart control flow at
  `MAX_RESTART_DEPTH - 1` and `MAX_RESTART_DEPTH`, asserts on outcome, not
  source text") — investigation found that test can't actually reach the
  boundary, and a second, unguarded mechanism exists. Verified by hand
  (empirical repro script + code read, not speculation):
  - **handle.py's two in-process gates can never hit their own cap within
    one call.** Both the director-restart (`handle.py:1623-1625`) and
    closure-restart (`handle.py:1710-1719`) blocks are single `if`s, not
    loops — each fires at most once per `_handle_impl()` invocation. A
    repro script (`handle()` with `run_agent_loop` mocked to always return
    `status="restart"`) shows exactly 2 total calls, never approaching
    `MAX_RESTART_DEPTH=3`. The existing "behavioral" test from batch-1
    (`tests/test_handle.py::TestDirectorRestart::test_restart_depth_cap_prevents_infinite_loop`,
    asserts `len(calls) <= 4`) looks like it proves the cap but is
    vacuously true — the real ceiling per call is 2 (or 3, chaining
    director-restart into a closure-restart), one shy of the nominal cap,
    regardless of `MAX_RESTART_DEPTH`'s value. The check at
    `_depth < MAX_RESTART_DEPTH` is therefore currently unreachable dead
    logic for a single top-level call.
  - **The actual cross-call continuation mechanism has NO cap at all.**
    `director.handle_escalation()` (`director.py:980+`) — a separate,
    queue-based continuation path (escalation → `task_store.enqueue(...,
    continuation_depth=depth+1)` → `handle_queue.handle_task()`'s
    `loop_continuation` branch → `run_agent_loop(..., continuation_depth=depth)`)
    — never imports or checks `MAX_RESTART_DEPTH` anywhere. A "continue" or
    "narrow" decision enqueues `depth+1` unconditionally; `handle_task`
    dispatches whatever depth a claimed task carries with no gate before
    executing. If an LLM escalation keeps returning "continue", this
    recurses without bound — the "prevents infinite restart loops" property
    handle.py's comment claims doesn't apply to this path. Was pinned by a
    known-gap test; **now RESOLVED** — the pin test was flipped/renamed to
    `tests/test_escalation.py::TestHandleEscalationWithLLM::test_deep_continue_enqueues_and_fires_checkin`,
    which asserts the shipped behavior: the continuation still enqueues at
    `depth+1` (the goal never stops) AND a non-blocking `recursion_checkin`
    notify fires so the user can redirect/stop.
  - **RESOLVED 2026-07-13 (was "not fixed this session").** Jeremy's decree
    (`docs/RECURSIVE_CHECKIN_DESIGN.md`) settled the judgment calls: the fix
    is NOT a cap/refusal but a **non-blocking progress check-in** owned by
    `director.handle_escalation` (mechanism 2) — at `new_depth >=
    recursion.checkin_first_depth` (default 2, the 3rd goal pass) and every
    jittered `recursion.checkin_jitter_min`–`max` (4–7) passes after, it
    fires `recursion_checkin` while still enqueueing the continuation. The
    goal keeps running (ralph-style optimistic default); the user steers via
    the existing `InterruptQueue`. handle.py's own two single-shot gates
    (mechanism 1) were deliberately left untouched — a different,
    already-capped concern; making them a reachable loop was ruled scope
    creep past the decree.

## Stale — dropped this triage

Titles deleted as obsolete (auditable; full history in git):

- Harness hill-climbing as autonomous loop (2026-07-04: stale duplicate — run_nightly_eval→evolver wiring shipped Phase 42, see BACKLOG_DONE; harness_optimizer covers the proposal half)
- Dumb loop audit scaffolding item (2026-07-04: superseded at greater scope by MILESTONES #2 / docs/DUMB_LOOP_AUDIT.md; cutover ENACTED 2026-07-03)
- Build-loop "Define the success condition operationally" + "Preserve health-only heartbeat semantics" notes
- Per-class-routing "decided" sub-item (the decided routing paragraph; open children local_max_tokens / agentic-verifier kept)
- done≠achieved finding (the closure-demotion-not-reaching-outcome-store-adjacent organic batch — retained as a conservative watch-item, not dropped)
- X research watch-lists (Large Memory Models, Google MCP Toolbox, Polymarket 36GB dataset / TOOLS.md+STYLE.md gaps, Letta API comparison, Team OS / shared context layer)
- Local-LLM-research test goal
- Links-not-digested (Polymarket behavioral analysis, Build-your-own-X)
- Miessler steal-list (Dashboard: replay as factory mode; superseded eval-driven harness hill-climbing dup)
- Latent Briefing / Kronos / Eval harness + holdout / Associative JSONL memory links (link-farm + 18-link watch entries)
- SERV model-watch
- Trailing K-layer dup ("Examine the research in research/orchestration-knowledge-layer..." — already tracked under Memory/Knowledge Layer)

---

## Achieved-but-stuck classifies as "failed" — SHIPPED 2026-07-27 (per-step learning chunk)

**Fixed as `achieved-not-done`:** `classify_outcome` gained a
verdict-preferred branch (after the chunk-9 #4 incomplete-rebucket,
before `_PARTIAL_STATUSES`/`_FAIL_STATUSES`) — judged goal_achieved=True
with a non-success status now classifies `achieved-not-done`, the mirror
of `done-not-achieved`. Learnable (curated class + raw-row twin in
`is_learnable_outcome`); badge/label rows added in loop_report +
notify_telegram; full consumer census in
`docs/history/2026-07-27-per-step-learning.md`. The two live specimens
(`692bd96f-brisk-lichen`, `d9f01e13-golden-birch`) classify correctly
under the new branch. Original item follows.

### Achieved-but-stuck classifies as "failed" (star find, 2026-07-27)

The SF-2 inversion nobody named: `classify_outcome` checks
`goal_achieved is True` only inside `_SUCCESS_STATUSES`, so a judged
goal_achieved=True run with status "stuck" lands `success_class=
"failed"` — a judged SUCCESS counted as failure evidence. Two live
specimens: `692bd96f-brisk-lichen`, `d9f01e13-golden-birch` (both
goal_achieved:true in metadata, both classify failed; spot-verified by
executed classify_outcome). Fix shape: a verdict-preferred branch
before `_FAIL_STATUSES` (judged True + non-success status → its own
bucket or "success"), with the same consumer census the chunk-9 #4
rebucket got (learnability, notify badge, evolver passthrough). Not
rushed in the star run per its own cuts (no src/ changes in-run).

---

## Linux local-validator burn-in — SUPERSEDED 2026-07-21 (local rung removed)

**Closed without further burn-in:** the 2026-07-21 swarm-review chunk 1
removed the local-model validator wiring entirely (Jeremy decree, "local
LLMs are in the way for now") — the ladder is now Tier-0 deterministic →
hosted-free → paid. The burn-in question this item tracked (which local
model can safely hold the free rung on this box) is moot until the
revival trigger fires: hosted free tiers churning away. Re-entry path =
bakeoff methodology + corpus (`tests/fixtures/validation_cases.json`) per
`docs/LOCAL_VALIDATOR.md` (now a history record). Original item with the
full measurement history follows.

### Linux local-validator burn-in

**Priority raised 2026-07-16 (Jeremy, hosted-free keys pending):** "Liking
the local LLM angle and we should lean into that slightly upgraded local
option from a few days ago for now, and let's keep an eye on the overall
time, I get that it's adding minutes in some long goal runs." Until the
Groq/Gemini keys land, the local tier carries validation on this box —
which makes the burn-in below the live gap (the box currently runs
qwen2.5-coder:3b, the exact build the M1 sweep REJECTED for two unsafe
decisive false-passes). Watch total added wall-time per run, not just
per-call latency, when evaluating.

- [x] **Replay the committed validator corpus on the production Linux box before
  enabling Ollama there.** The formal M1 sweep is complete (BACKLOG_DONE): all
  four small candidates used the same 14 cases and exact production protocol.
  It selected VibeThinker-3B-4bit on Apple Silicon and rejected Ollama
  qwen2.5-coder:3b despite its speed because it produced two unsafe decisive
  false-passes. The 2014 Ubuntu Mac mini experiment is not a viable deployment
  proof. This residual is hardware-gated: choose a Linux candidate, replay with
  `scripts/validator-bakeoff.py`, and require zero unsafe decisive false-passes
  plus warm latency under the configured breaker before enabling it.
  **RUN 2026-07-16 (Jeremy: "we should use that new 4 bit quantized model") —
  VibeThinker-3B Q4_K_M GGUF via Ollama FAILS the latency gate on this box.**
  Same 14 cases, exact protocol, cores 2,3 / nice 12 (production caps):
  13/14 verdicts hit the adapter's 60s `_GEN_TIMEOUT` without finishing; the
  one completed case took 55.8s (correct, decisive, conf 0.9). ≥4x over the
  15s breaker — the reasoning-model CPU dead-end confirmed by measurement,
  not assumption. 0 unsafe decisive false-passes, but decisive coverage 1/14.
  Raw JSON: `research/validator-bakeoff-linux-2026-07-16.json`. Standing
  state on this box: qwen2.5-coder:3b stays configured as the fast local
  first-pass (its two known unsafe classes — read-only violation, failing
  test run — are the exact classes the deterministic Tier-0 guards
  [write-fence/scavenge, settled-by-command] also police), latency breaker
  armed; the QUALITY upgrade lane for this box is hosted-free (Groq/Gemini
  keys, item above), not a local reasoning model. VibeThinker-3B-4bit
  remains the Apple-Silicon reference. Side-find during the run: the
  orchestration-owned ollama daemon had a deleted agent-worktree cwd →
  every llama-server load died → local tier silently erroring (all
  validation escalated to paid); fixed in src/local_models.py (cwd="/") +
  regression test, commit 778b81e.

---


## #27. no_tools sweep — every adapter.complete site classified — SHIPPED 2026-07-18

**Source:** calm-echo rogue execution (2026-07-17): planner decompose ran
the subprocess adapter with tools enabled and EXECUTED the remainder goal
instead of planning it (~4 min side-quest, wrong FINAL_REPORT.txt shipped
as the answer). planner.py fixed same day; this sweep covered the other
~70 `adapter.complete` sites.

**Classification record (balanced-paren inventory, 87 `.complete(` sites;
13 already marked, 74 swept):**
- **Contract calls → `no_tools=True` + `purpose="<label>"`:** ~55 sites
  across memory, memory_ledger, knowledge_bridge, knowledge_lens,
  attribution, cross_ref, thinkback, closure_verify, step_exec (contract
  seams), loop_blocked, heartbeat, doctor, verification_agent,
  sprint_contract, inspector, introspect, evolver, evolver_scans,
  quality_gate, skills, skill_lifecycle, harness_optimizer, hooks,
  conductor, director, handle, interrupt, mission, persona, team, workers,
  navigator_prompt, pre_flight, factory_minimal, factory_thin, llm
  (backend probe).
- **Intentionally agentic → `# agentic: <reason>` marker (6):**
  step_exec.py:1081/1174 (worker executor seams), factory_minimal.py:68,
  factory_thin.py:217 (factory step execution), team.py:214, workers.py:252
  (worker execution paths).
- **Not adapter calls (skipped by inventory):** docstring/prose mentions,
  handle's ConversationChannel.complete, harness_optimizer prompt strings.

**Conductor NOW-lane port (the one real gap found):** the live
Telegram/Slack `conduct()` NOW path depended on the CLI tool harness to
fetch URLs — the exact rogue-execution shape. Ported handle._run_now's
pre-fetch pattern (web_fetch.enrich_step_with_urls + _NOW_LINK_READ system
suffix, degrade-to-answering on enrichment failure) so `no_tools=True` is
safe there; this also makes intent.classify's URL-exemption assumption
("the NOW lane pre-fetches provided links") true on the conductor path,
not just handle's. Pins in test_conductor.py.

**Standing lint (the BH011-shaped bughunter):**
`tests/test_no_tools_contract.py` — balanced-paren extraction of every
`.complete(` call in src/; a call passing messages must have literal
`no_tools=True`/`"no_tools": True` AND a `purpose` kwarg in the call or
within 20 preceding lines (kwargs-dict pattern, e.g. planner), or an
`# agentic` marker within 3 lines. The literal-True + purpose requirement
came out of the same-day adversarial review (all three reviewers): the
first draft accepted any `no_tools` substring, so `no_tools=False` left
after debugging — or a stray comment — would have passed. Exempt: llm.py,
hosted_free.py (adapter internals/delegation). A vacuity guard asserts the
lint still sees ≥40 real sites so regex drift can't green it silently.

**Also:** ~3 rigid test stubs (`def side_effect(messages, tools=[])`)
gained `**kwargs`; six adapter `complete()` signatures already accepted
`**kwargs` so the sweep is signature-safe by construction.

## #26. web_fetch reply-aware X rung — SHIPPED same day (2026-07-17)

**Source:** the zesty-ash dig + Jeremy's follow-up-post note ("the github
link was actually in a follow-up post by the original author... make sure
that's a thing we're looking at still").

`web_fetch.fetch_x_tweet`'s best authenticated rung shelled to the
OpenClaw-era `x-twitter-cli.sh post`, which renders ONLY the target tweet
(keeps the reply *count*, drops reply content) — so the worker-facing
web_fetch tool was structurally blind to the "Repo👇" pattern (link in the
author's first self-reply; both X-research runs 1dac0e17/75a88777 burned
steps hunting a repo whose link sat one reply down). The thread-capture
behavior Jeremy remembered was the OpenClaw/poly-proto x-capture era —
it pre-dated Maro and never survived into a working rung.

**Shipped:** `_fetch_x_thread_direct` — direct `twitter tweet <id> --json`
(schema `{ok, data: [post, ...]}`, urls pre-resolved by the CLI), rendering
root post + metrics, author follow-ups in their own labeled section (full
text + links), other replies capped at 8×300 chars. Wired as rung 0 of
`fetch_x_tweet`, BEFORE Jina — a root-only Jina success returned early and
hid the thread, which is exactly how both runs missed the reply. Falls
through on missing binary/cookies/JSON so the old ladder is untouched as
fallback. Cookie handling reused from `_x_cookie_env` (never logged);
poly-proto wrapper kept as a later rung, not developed (reference only).
Live-verified end-to-end: returns the thread with
github.com/ZeroPointRepo/awesome-hermes-skills resolved from the author's
follow-up. Companion skill fix (stale `thread` → `tweet` command +
follow-up-pattern note) shipped in d9ea0b4.

## Stuck-step advisor resurrected behind `advisor.stuck_step` — SHIPPED (2026-07-16)

**Source:** stuck-outcome adversarial review 2026-07-15 — `_ctx_summary` in
`loop_execute.py` called `o_s.get('status','?')` on StepOutcome dataclass
objects → AttributeError → swallowed by the broad `except Exception` → the
adaptive-off stuck lane's "(b)" advisor had silently never run. Shipping
the one-line fix would ACTIVATE a never-run LLM call on stuck paths, so it
was DECISION-FLAGGED (fix+enable / fix+gate / delete).

**Decision path:** Jeremy decided this twice in two parallel sessions,
converging on the same outcome. Morning session: "fix + config-gate...
turn it on by default for ourselves for actual testing as we go" →
shipped as `3d35ba0`. Afternoon session (this one): "let's fix + enable
that stuck lane's advisor. I thought that was on" — it WAS on; his memory
was right, and the afternoon decree was already satisfied. Net:
`advisor.stuck_step` defaults OFF for fresh installs (no-silent-spend
decree), enabled in this box's `~/.maro/workspace/config.yml` for live
testing.

**Shipped (`3d35ba0`, morning session):** dataclass attribute access
fixed (`o_s.status` / `o_s.result`); context summary built OUTSIDE the
try so shape bugs are loud; except narrowed to the LLM call only, logged
at warning; the (b)-retry path records the stuck-flagged execution; the
folded restart-break residual fixed (`_append_stuck_step_outcome()`
before the director-restart `break` — every loop exit now records the
stuck step); config gate + docs/DEFAULTS.md row; 3 tests
(gated-off-by-default, fires-when-enabled, retry-path-records-step).

**Afternoon session's delta:** the restart-break exit had no dedicated
pin — `test_stuck_restart_records_stuck_step_outcome` in
tests/test_agent_loop.py drives the loop to stuck via repeating preset
steps, has the director decide `restart`, and asserts the stuck step
appears in `result.steps` as blocked. Mutant-verified: removing the
`_append_stuck_step_outcome()` call before the break flips it FAIL.

**Verification:** all 4 advisor/stuck tests green; full
tests/test_agent_loop.py suite exit 0; mutant killed, source md5-restored.

## Probe modality is per-segment: run-then-grep compounds count behavioral — SHIPPED (2026-07-16)

**Source:** closure-downgrade review side-question (the confirmed root of
d2f4e2f4's "too shape-strict" flag): `_classify_probe_modality` checked
`_STATIC_HINTS` before the process patterns over the WHOLE string, so the
single most common probe idiom — `<run the artifact> && grep <its output>`
— counted static even when it executed the deliverable end-to-end
(d2f4e2f4 probe #8: `python3 -m src > log && grep VERDICT=…` → static;
modality showed {"static": 8} on a run that exercised its deliverable
twice). The precedence exists to keep `go build ./cmd/foo` from
false-matching the `./path` process pattern, but explicit runners
(`python|go run|node|timeout`) were swept in as collateral, contradicting
the module's own `curl && grep → http` precedent.

**Decision path (DECISION-FLAGGED → measured → Jeremy's call):** the fix
shifts verdicts in the blessing direction (suppresses behavioral-gap
downgrades), the expensive failure per the module's documented asymmetry —
so it was flagged, measured, then decided. **Measurement** (replayed
per-segment classification against every recorded closure verdict — 710
run dirs, 58 with recorded verdict calls since record-mode shipped
2026-06-26): 11 probe-level shifts, ALL static→process, all genuine
run-the-artifact-then-grep idioms, zero false promotions of compile-style
commands. Run-level: 55/58 NO_CHANGE; 2 flips with llm_complete=False
(gap detector never consults modality then → zero behavior change);
exactly 1 historical verdict flips — d2f4e2f4 itself, the downgrade this
item came from, already established false. Jeremy 2026-07-16: "Agree,
ship the fix with the quote-aware splitter."

**Shipped:**
- `_split_probe_segments` — quote-aware split on top-level `&&`/`||`/`;`/
  `|` (single `&` and `2>&1` never split; quoted operators stay:
  `grep -q 'a && ./x' file` must not shed a fake `./x` process segment —
  that would be a false promotion, the exact failure the flag feared).
- `_classify_probe_segment` — the old whole-string logic verbatim, per
  segment; static-hint-before-process precedence preserved WITHIN a
  segment, which is where the go-build guard actually lives.
- `_classify_probe_modality` — most-behavioral segment wins
  (browser > ws > http > process > static).
- Evidence-gate extension: failed run-then-grep compounds (now "process")
  still attach `target_file_content` when a static-hint segment is present
  — the failed grep's file ground truth is still what the verdict needs.
- `modality_dist` rebuild respects the stamped modality — preflight rows
  no longer misreported as static in the CLOSURE_VERDICT event.

**Verification:** corpus replay with the REAL implementation reproduced
the measurement exactly (same 11 shifts, same single d2f4e2f4 flip).
Mutants all killed FAIL→PASS: whole-string revert (2 pins), quote-blind
splitter, evidence-gate narrowing. Existing precedence pins
(`go build ./cmd/…` static, `curl && grep` http, server-boot-curl http)
unchanged and green.

**Residual (accepted):** a quoted URL/tool word inside a grep pattern
(`grep 'https://foo' file`) still classifies http — pre-existing
whole-segment wart, untouched by this change; no occurrence in the
recorded corpus.

## Precondition pre-flight no longer leaks garbage checks into the closure feed — SHIPPED (2026-07-16)

**Source:** side-finding from the closure-downgrade review (run d2f4e2f4):
the run card showed probes 1-3 as `shutil.which('cat')`,
`shutil.which('wc)')` (mangled paren), `shutil.which('PYTHONPATH=src')` —
two of them the run's only inconclusive checks, diluting an 8-check feed
on a run that had exercised its deliverable twice.

**Mechanism (corrected from the BACKLOG framing):** the original item said
the strings were "Python expressions run as shell commands" — they never
execute; the preflight row's `command` field is provenance-only. The real
chain, confirmed from the run's recorded calls (call-00005): the intent LLM
emitted prose preconditions — `[preconditions: Linux with /proc filesystem,
standard utilities (grep, cat, wc)]`, `[preconditions: Python 3.10+,
PYTHONPATH=src, no external dependencies]` — and
`scope._parse_deliverable_line` split the annotation on EVERY comma,
shredding the parenthesized list into `standard utilities (grep`, `cat`,
`wc)`. `_classify_precondition`'s command gate was absence-of-spaces/dots,
so `wc)` and `PYTHONPATH=src` classified as commands, failed shutil.which
(exit 127 → inconclusive), and rode the check feed. Corpus scan over 1768
recorded calls: paren-lists and env-var preconditions are both recurring
shapes (`(GasBuddy, Shell/Chevron locators)`, `(uname, ls)`,
`(curl/Python requests/similar)`), not one-offs.

**Shipped (two layers, keep-the-species-apart):**
- `scope._split_preconditions` — top-level-comma splitter (commas inside
  parens don't split; unbalanced open paren swallows the rest into one
  item, the safe direction: one opaque prose item vs several bogus
  tokens). Round-trip through `to_markdown_line` stays faithful.
- `closure_verify._classify_precondition` — the command branch is now a
  positive binary-name match (`[A-Za-z0-9_][A-Za-z0-9_+-]*`; `g++`,
  `clang-14` still commands) instead of absence-of-spaces, so shredded
  fragments and env-var requirements land in opaque, which the docstring
  had always promised.

Regression pins use d2f4e2f4's exact strings end-to-end
(`test_precondition_preflight_run_d2f4e2f4_regression`: the two real
deliverable lines produce ZERO synthetic rows), plus splitter edge shapes
and classifier cases. Both reversion mutants (naive split back;
old absence-of-spaces gate back) verified FAIL→PASS against the new pins.

**Residual (accepted):** preconditions that are single well-formed command
names still pre-flight as before — that path is the feature, not the leak.
The preflight `command` field keeps its `shutil.which(...)` provenance
string; with the garbage rows gone, it only appears when a *real* declared
tool is genuinely missing, which is exactly when a reader wants it.

## Closure downgrade reason now reaches the run card — SHIPPED (2026-07-16)

**Source:** container-on day-one findings (run d2f4e2f4): closure_verify
downgraded complete→False (behavioral gap) but the card kept the
pre-downgrade "Goal achieved." narrative beside `goal_achieved: false`,
confidence 0.92 — contradiction with no cause; the reason lived only in the
worker log.

**Shipped:** the deterministic downgrade branches (behavioral gap /
diagnosis gap) now rewrite the verdict summary to LEAD with "Downgraded to
not-achieved — {reason}. Original verdict: {…}" (leading so it survives
every `[:300]` truncation site), plus `ClosureVerdict.downgrade_reason` →
`goal_verdict_downgrade_reason` threaded metadata → run card → report
Outcome panel ("Downgraded:" line) → CLI (inspect-run, run output JSON+text),
only-when-stamped (key absent when empty, stale key popped on re-stamp).
`decision_prior`'s "why" inherits the cause for free via the summary.
8 writer tests + 9 review-driven pins. Code swept into `5c3a886` by a
concurrent session mid-flow (complete + green at sweep; reviewer
md5-verified no mutant leaked); review fixes in the follow-up commit.

**Adversarial review (FIX_FIRST → fixed):** F1 REAL BUG, reproduced — the
CLI verdict lane stamped via merge-no-pop, so `maro resume` with a clean
retry verdict left the stale downgrade key: card rendered "Goal achieved:
yes" + "Downgraded: …" (the same contradiction, inverted). Fix:
`_closure_verdict_pass` now calls `runs.stamp_run_verdict` (one shared
replace-semantics implementation; both CLI lanes wrap in `scoped_run_dir`
so the plumbing matches). Deliberate rider, pinned: an unjudged retry now
drops a stale `goal_achieved` too (stamp_run_verdict's documented
latest-attempt semantics). F2: 6 of 12 mutation sites had no killing test —
all 6 pinned with verified FAIL→PASS kills (incl. end-to-end curate_run on
a downgraded run proving `optional_provides` is load-bearing: removing it
hard-fails the curator output contract). Held under attack: truncation
survival at 315-char reasons, both-downgrade join order, HTML escaping,
inconclusive-flip stays unprefixed, no-downgrade summary byte-identical.

**Spawned items (BACKLOG):** probe-modality compound-command
misclassification (the actual root of the "detector too shape-strict" flag;
DECISION-FLAGGED — the fix shifts verdicts in the blessing direction);
precondition pre-flight strings leaking into closure checks as mangled
shell (d2f4e2f4 probes 1-3).
