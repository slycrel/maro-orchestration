# Backlog — Deferred Items, Ideas, and Known Issues

Single canonical location for everything we've identified but haven't done yet.
Read this at the start of every session. Update it as items are completed or new ones emerge.

**Completed items live in [BACKLOG_DONE.md](BACKLOG_DONE.md)** — move items there with their full context when they ship; that file is the archive of what we've already decided, tried, or superseded, and it's ingested by `dev-recall` for historical context.

Last reviewed: 2026-07-14, eighteenth pass — lesson-funnel intake now has outcome-attributed deferred/completed/failed state, tiered-write counts, dry-run exclusion, and an archive-aware rolling report; the old empty-row “0 lessons” claim was unprovable and is corrected. Previous seventeenth pass: the captain's-log viewer residual shipped as a sortable per-event archive-spanning TSV/JSON slice and moved to BACKLOG_DONE after real Claude review. Previous sixteenth pass: the formal same-corpus M1 validator sweep selected VibeThinker-3B-4bit, rejected three smaller/alternate builds, and exposed/fixed an unsafe local certainty-threshold mismatch; only hardware-gated Linux/Ollama burn-in remains. Previous fifteenth pass: closure-rejected restart attempts are now verdict-stamped before replacement, and deterministic provenance failures outrank narrative closure passes; the closure-demotion residual moved to BACKLOG_DONE after real Claude review. Previous fourteenth pass: escalation-class notifications now carry the immediate originating run handle when one exists and an explicit blank legacy fallback; R4's final residual moved to BACKLOG_DONE. Previous thirteenth pass: the skill-candidate sweep now holds one fail-closed per-workspace flock across scan, paid extraction, and consumption; moved to BACKLOG_DONE and R3 residuals closed. Previous twelfth pass: one neutral learnability policy now serves curated run cards and raw outcome records; evolver no longer synthesizes fake success status; moved to BACKLOG_DONE. Previous eleventh pass: CuratorSpec runtime contracts now distinguish mandatory/optional outputs and dependencies, execute transactionally, and reject contract drift; moved to BACKLOG_DONE. Previous tenth pass: the R3 origin-dictionary residual shipped as one plain-JSON `Origin` TypedDict used across task/run/recall/thread boundaries; moved to BACKLOG_DONE. Previous ninth pass: PID-reuse-safe container/clone ownership shipped with Linux boot+start ticks and Darwin kernel microsecond timestamps; moved to BACKLOG_DONE. Previous eighth pass: durable O(1) run-reference index shipped with one-time legacy migration, exceptional fallback, import/prune maintenance, partial-migration state, and concurrency/corruption coverage; moved to BACKLOG_DONE. Previous seventh pass: `test-safe.sh` now runs its full chunked suite on macOS while preserving Linux affinity; moved to BACKLOG_DONE. Previous sixth pass: R5 run-curation side-effect boundary and runtime dependency semantics shipped and moved to BACKLOG_DONE; pure card construction is durably checkpointed before explicit maintenance. Previous fifth pass: R5 execution-fence setup now refuses visibly on mandatory cwd/policy failures, with five fault-injection tests; full suite green. Previous fourth pass: independent holistic review of the rolling 48-hour range `d717915e..8aa9876` added R5 (portable-pack review seal bypass reproduced end-to-end; pack import concurrency/global-routing breach; implicit hosted-free data egress + unenforced latency cap; run-curation atomicity/hidden-side-effect boundary; execution-fence setup fail-open). Canonical `pytest tests/ -q`, real-Docker container E2E, and `git diff --check` green. Previous same-day pass: session close (R4 final capstone adversarial-review across the ENTIRE day's changeset, `b2dc34d..HEAD`, run cross-model via the real `/adversarial-review` skill per the session's closing instruction: 3 more real bugs fixed live — enqueue-failure dead-chain now surfaces to the operator instead of silently completing, check-in payload off-by-one fixed [all 3 reviewers converged independently], skill-candidate consumed-on-crash bug fixed; 1 architectural residual documented [handle_id absent from escalation-class notify payloads, pre-existing, not a regression]; also closed out the one un-triaged batch-1 finding — navigator_shadow's analyze_live_agreement/analyze_planning_depth_agreement duplication, extracted shared _tabulate_agreement helper. Full suite green, 169/169, after every fix this session). Second pass same day (post-1.0 /goal session: recursion check-in + planning-depth shadow shipped, R1 architectural cleanup shipped, 1.6 /loop trace closed with evidence, knowledge-web read side traced and re-scoped — not built, real prerequisite gap found; R3 adversarial-review of all of the above — 5 bugs fixed live, 3 architectural residuals documented; #19/#20/#21 fully-shipped stubs archived to BACKLOG_DONE (content was already there or wholesale-done); #0's stale "mining passes TODO" bullet corrected — all 4 miners are shipped; -1's stale unchecked "Cosmetic sweep... SHIPPED" checkbox fixed; triaged the rest of the Actionable Stack — nothing else is both unblocked and ready without Jeremy's input). Previous same-day pass: #10, #14, #18 shipped and archived to BACKLOG_DONE; #17 trimmed to its O(all runs) residual; #22 residual (blank-slate skill set) and hist-r2-02 checked off; #25 code shipped, stays open pending Jeremy's API keys; container-executor C4 mechanics merged, C4-BOX real-goal burn-in stays Jeremy-gated. Previous: 2026-07-09 (decision-cleanup session with Jeremy: #19 thread-arch decisions all resolved + recursion decree recorded, intent-resolution A/B dropped, orch.py trio deprecated, host-check wired+scheduled — four entries → BACKLOG_DONE; fastembed lane confirmed stays-gated). Previous full triage: 2026-07-04.

Current pass (2026-07-14, nineteenth): done-vs-achieved now has explicit
prospective organic/smoke/control/benchmark provenance in run metadata and the
durable outcome ledger, plus an honest n≈30 standing gate that keeps historical
unknowns unknown.

Current pass (2026-07-14, twentieth): benchmark/eval cells now reserve
collision-resistant per-cell projects or retained direct-Director workspaces,
so repeated experiments cannot inherit the launch repo or earlier artifacts.

---

## Actionable Stack

Ordered open work that matters. Top of the list is next.

### R5. Independent holistic + adversarial review of the rolling 48-hour changeset (2026-07-13)

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
BACKLOG_DONE). Full reports: `output/adversarial-review-2026-07-13-batch1-{skeptic,architect,minimalist}.md`.
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
`output/adversarial-review-2026-07-13-final-{skeptic,architect,minimalist}.md`.

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

- [ ] **`maro resume` has no structural serialization against concurrent
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
  parallel-invoked) rather than a blind structural fix.

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

- [ ] **Full acceptance probe with a real goal** — `scripts/container-acceptance-probe.sh`
  end-to-end: `/login` the auth volume, then a real goal under `executor.container: on`
  vs fence-only, and `check` both. Expect: fence-only leaks + logs SCAVENGE;
  container contains (token absent, decoy unchanged). This is the README security
  before/after that a shell-only proxy can't fully stand in for.
- [ ] **Dogfood no-regression run** — the standing dogfood goals under
  `container: on`; same `status`/`goal_achieved` as their fence-only baseline,
  correct artifacts. Watch env-dependency surprises + native-Linux bind-mount
  uid/gid (Mac used VirtioFS) + the self-dev clone merge-back in a live run.
- [ ] **Fill the `CONTAINER_BURN_IN.md §5` go/no-go checklist**, then **the flip**
  (box default, then the higher-stakes fresh-install default) — Jeremy's call on
  the evidence. Record in GOAL_BRAIN Decisions; mark C4 shipped in the design
  doc §9 + MILESTONES. The full runbook is `docs/CONTAINER_BURN_IN.md`.

### -1. Purgatorio r2 graduates (2026-07-10 re-run; docs/audit-2026-07-r2/RECONCILIATION.md)

All adversarially verified (41/42 confirmed). The two 1.0-blockers first:

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

### 25. Hosted-free small-LLM tier: Groq + Gemini free tiers (2026-07-12, from item 24 decision)

**Code SHIPPED 2026-07-13** (`src/hosted_free.py` + `GroqAdapter`/`GeminiAdapter`
in `src/llm.py`, wired into `step_exec.verify_step` as Tier 1b between local
and paid). Fully inert unless explicitly enabled, and inert with no key set —
the only thing left is Jeremy setting `validate.hosted_free.enabled: true`,
creating `GROQ_API_KEY`/`GEMINI_API_KEY`, and confirming the free-tier RPM
numbers below still hold against the live endpoints (they were verified
2026-07-12 from research, not yet from a real call). See BACKLOG_DONE for
implementation detail once keys are live and this has a real-traffic pass.

Jeremy: "I'm open to Groq or Gemini free tiers for small LLM work in the
orchestrator." Wire the free tiers as a hosted-free rung for the
non-agentic call classes (validation ladder, classify/routing, cheap
verification) — the $0 replacement for the old OpenRouter-free-model
headache, with no credits to babysit.

Design sketch (from the 2026-07-12 research, details in
docs/MODEL_ROUTE_EXPLORATION.md round 2):
- **Groq** (primary): OpenAI-compatible; free tier verified 2026-07-12 at
  llama-3.1-8b-instant 30 RPM / 14.4K req/day (classification volume) and
  gpt-oss-20b/120b 30 RPM / 1K req/day with strict JSON-schema constrained
  decoding (verification-grade structured output). Model churn is real
  (Kimi K2 was removed Mar 2026) — keep model IDs in config, not code.
- **Gemini API free** (secondary): official OpenAI-compat endpoint
  (`generativelanguage.googleapis.com/v1beta/openai/`), ~10 RPM
  Flash-class. Rate-limit numbers are no longer published — probe, don't
  assume.
- Implementation shape: two small `OpenAICompatAdapter` subclasses in
  llm.py (mirror OpenRouterAdapter: base URL + `GROQ_API_KEY` /
  `GEMINI_API_KEY` + `_MODEL_MAP` entries), NOT in the global
  `backend_order` failover — plug in as a hosted-free rung of the
  validation ladder alongside the local-models tier (step_exec.py
  ladder + local_models.py latency-breaker pattern; reuse
  `latency_guard_tripped` semantics and validation_shadow scoring so
  free-vs-paid agreement is measured from day one, same as the local
  lane).
- Free-tier RPM caps mean a rate-limit-aware gate (429 → trip like the
  latency breaker, retry-after aware), not naive failover.
- Needs: Jeremy to create the free API keys (no card for Groq; Google
  account for Gemini). Zero recurring cost.

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
- [ ] **Manti follow-ups (2026-07-10, Jeremy adjudicated the NOW answer
  "an abject failure... Siri for about 15 years"; decreed NO new errand
  lane):**
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
    Clean re-run (fresh project `manti-clean-rerun`, no artifact reuse)
    in flight same evening — pre-breaker code, so its envelope still
    carries the ladder tax; treat it as clean-cost baseline for plan
    shape, and the NEXT run as the breaker A/B.
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
- [ ] **(post-1.0, Vision)** Shared trusted skill directory + cross-instance
  learning share — see Vision section entry.

### 0. Test corpus — capture the missing layers (forward record-mode + full archive)

**Shipped 2026-06-26 (the "now" half):** `scripts/harvest_corpus.py` distills the
live workspace history (`runs/` 569 captains-log slices + `projects/`) into
deduped fixture slices under `tests/fixtures/orchestration_corpus/` (thinned
slices committed, full git-ignored + reproducible). 24 slices, 5,646 raw records;
`tests/test_orchestration_corpus.py` proves consumability + regression-guards the
quality-gate escalate formula against 122 real verdicts (0 mismatches). Workspace
data is preserved, not deleted.

**Shipped 2026-06-26 (forward record-mode + curation):**
- [x] **Forward byte-level record mode.** `FailoverAdapter.complete` now captures
  `{prompt, response, tool_events, tokens}` per call to
  `<run-dir>/build/calls/call-NNNNN.json` via `runs.record_llm_call`. One seam
  over every backend; secret-scrubbed through the new single-source
  `src/secret_scrub.py` (harvester now imports the same module — no divergence).
  **Default ON**; off via `MARO_RECORD=0` or config `record.enabled: false`
  (`runs.recording_enabled`). Future runs now yield true byte-level replay
  fixtures. Tests: `tests/test_record_mode.py`.
- [x] **Post-goal curation pass.** `src/run_curation.py` runs at goal-end (hooked
  in handle.py finalize), writes `run_card.json` (outcome class + mineable
  inventory) so the paid-for capture is parked for later mining, not discarded.
  Miner registry (v0: classify + inventory). User-visible/prunable CLI
  (`python3 -m run_curation list|show|curate|prune`). Tests:
  `tests/test_run_curation.py`.

**Remaining ("later"):**
- [x] **Mining passes on the parked data — ALL SHIPPED, stale bullet
  corrected 2026-07-13.** All four miners named here are live in
  `run_curation._CURATOR_SPECS` (9 curators total, topo-sorted since
  the R1 batch-1 fix this session): `flag_skill_candidate` (skill
  scraper, now with a real consumer — `evolver.promote_skill_candidates`,
  shipped this session), `scrape_scripts` (script scraper),
  `index_decision_prior` (decision-prior indexer — the owner-ask half,
  Jeremy 2026-07-04 "task failures being retried, with the old task
  context available"), `rescue_partial` (partial-run rescue).
- [x] **Unify rung-4 step I/O** — DONE 2026-07-04. `FailoverAdapter.complete`
  stamps the record path onto the response (`resp.call_record`) when
  `record_llm_call` captures; `execute_step` carries it on the outcome dict;
  `StepOutcome.call_record` threads it through all construction sites (main
  loop, stuck-repeat, parallel batch + fan-out); `_write_loop_log` emits it
  per step. The loop view's truncated excerpt now links straight to
  `<run-dir>/build/calls/call-NNNNN.json`. 4 tests in test_record_mode.py.
- [ ] **Full raw archive (optional).** If/when `runs/`+`projects/` (~79M) get
  pruned, snapshot the full (non-thinned) slices somewhere durable first — they're
  only reproducible while the workspace exists.
- [x] **Wire more slices into real tests** — DONE 2026-07-03. Five replay
  tests added to tests/test_orchestration_corpus.py: too-broad breach
  conjunction (113 recs, floor-division boundary documented), metacognitive
  convergence-heuristic tail replayed against 281 recorded decisions (0
  mismatches; diagnosis-path out of scope — events don't carry loop state),
  claim-verifier outcome/action pairing, diagnosis subjects pinned to the
  current FAILURE_CLASSES taxonomy, closure-verdict internal consistency +
  proof the BACKLOG #5 restart predicate discriminates on real history.

### 1. Bound worker writes — residual: Bash write shapes the fence can't see

**Shipped arc archived 2026-07-04** — the full write-fence history (cwd
root-cause + soft fence, projectless-run fence hole, scavenge detection,
cwd-drift tracking, tier-a demotion, Jeremy's enable + same-day narrowing
with `/tmp` + goal-declared roots) lives in BACKLOG_DONE.md ("BACKLOG #1:
write fence — shipped arc") and `docs/BOUNDED_WORKSPACE.md`.

- [ ] **Residual: Bash write shapes the regex can't see** — `cp`/`mv`/`sed -i`
  targets, subshell/pushd cds stay invisible to `detect_out_of_fence_access`
  (documented in `docs/BOUNDED_WORKSPACE.md` known holes). Extend from real
  `SCAVENGE_DETECTED` evidence, not speculation. Current state: detection
  always-on (`validate.scavenge_detect`), enforcement **code-default ON
  since 2026-07-09** (1.0 posture flip; this box had run it enabled since
  2026-07-04 with no false positives — opt out via
  `validate.write_fence: false`, see docs/DEFAULTS.md). Reads stay
  unrestricted by design (logged, not blocked); a read-restricting mode
  remains possible if scavenge read rows ever show real contamination.
  **Evidence refresh 2026-07-14:** searched the unified workspace, run slices,
  repo experiment output, committed corpus, and pre-unification workspace
  locations. No `SCAVENGE_DETECTED` / `FENCE_WRITE_BLOCKED` row or recorded
  `cp`/`mv`/`sed -i` miss survives. The only concrete historical evasion is
  run `668e46d1`'s `cd` + relative redirect, already fixed and regression-pinned
  in `TestScavengeCwdDrift`. Leave this residual evidence-gated; do not add
  speculative shell parsing until a real missed transcript lands.

### 17. Run-visibility residuals (2026-07-09 real-data review)

All four original sub-items shipped 2026-07-09 (two concurrent sessions —
see BACKLOG_DONE for both): contextvar loop_id threading + purpose stamping
(this session), live-report post-curation refresh + NOW-lane mini-reports
(concurrent session, superseding this session's own narrower post-curation
refresh attempt — see BACKLOG_DONE for the reconciliation note).

- [x] **Goal search in the run visualization — SHIPPED 2026-07-13** (Jeremy,
  2026-07-10, rider on the retention decree): `maro viz search` filters run
  summaries by goal text / lane / status / date, plus a client-side filter
  bar in the HTML index. See BACKLOG_DONE for full detail.
- [ ] **Index rebuild is O(all runs) at every finalize** (~277ms at 668
  dirs, via the post-curation hook). Fine now; revisit around ~10k run dirs
  (incremental index, or rebuild only on viz/backfill).

---

## Vision / Deferred

### Time blindness — LLMs don't experience ideas over time (2026-07-11, Jeremy)

Jeremy (closing theme, verbatim): "humans perceive stories and ideas
over time (as we experience them) and LLMs... don't. That's a
communication blind spot. We might need to fight some kind of time
blindness between prompts, even in the same session, I think it's
getting worse rather than better here and there, and sometimes it
matters a lot."

No concrete goal yet — recorded well per his ask. Candidate starting
hooks when this gets a session: (a) age-stamp injected evidence and
recalled context (a lesson from February reads identically to one from
yesterday today — staleness is invisible to the model); (b) elapsed-
time awareness between steps and between sessions (the run knows wall
clock; the model is never told "your last step was 40 minutes ago" or
"this thread went quiet for 3 days"); (c) ordering/decay in recall —
dev-recall and memory injection currently rank by relevance, with time
as a hidden variable; (d) the captain's-log slice already carries
timestamps — surfacing them *into prompts* is cheap and measurable.
Related evidence: the godot retrospective (agenda-state divergence over
a long session = time blindness inside one session), stale-source
dissent handling in the Manti runs (the system already fights data
staleness — this extends the fight to its own conversational state).

**First slice — VEHICLE (added 2026-07-12 handoff audit; sized for a
Sonnet/Opus session, no design pass needed):** hooks (d)+(a) only —
surface timestamps that already exist into prompts. Concretely:
(1) every injected lesson/recall item carries an age suffix rendered
from its stored timestamp ("(learned 2026-02-14 — 5 months ago)") at
the existing injection seams (memory_bridge worker slice, lesson
injection in decompose, recall()); (2) step context gains one line of
elapsed-time awareness when the gap is material ("previous step
finished 42 minutes ago") from the run's own wall clock. Acceptance:
byte-identical prompts when timestamps are absent (test-enforced, same
pattern as `memory.worker_slice` off-path); a captains-log-visible A/B
flag (`memory.age_stamps`, default OFF fresh installs per
no-silent-change, ON this box after eyeball check); measured via the
existing WORKER_SLICE_INJECTED / recall events growing an
`age_stamped: true` field. Hooks (b) full elapsed-time model and (c)
time-aware recall *ranking* stay in this vision entry — (c) especially
must not ship as a silent relevance-formula change; it's a
verify→learn-adjacent measurement question.

### Perspective / camera rotation — bringing the human lens functionally (2026-07-11, Jeremy)

Jeremy (closing theme, verbatim): "I've talked about rotation, and
zooming in and out for seasoned developers. That's really just
re-framing and adjustment of perspective (from a game engine camera
type perspective), and I think the same holds true for ideas. LLMs have
ridiculous access to data, language and information. But the
perspective isn't the same at all. We need to help bring the 'human'
perspective, both innate and skilled usage of, into things at least in
a more functional light... Watching you react to seeing the
orchestration finding some of the perspective that is much more easily
discoverable from an end-user perspective makes me happy -- we're
getting there to a degree, but I'd like to refine that."

Standing direction, not a task. Constraints already on record: fixes
belong in inference moves (scope, memory, inversion, rotation), NOT
prompt taxonomies (feedback_inference_not_prompting); cuts-first
planning is the first shipped rotation-like move (narrowing = zoom).
What "refine" plausibly means next: (a) named lens/rotation moves the
planner or navigator can *choose* (invert, zoom-out-to-goal,
zoom-in-to-specimen, end-user-seat) the way draw_cuts chooses probes;
(b) the corpus arc as evidence — the end-user perspective (what a
person actually asked, what they actually got) surfaced failure
patterns that code-side inspection never would; institutionalize that
seat in review/verify stages; (c) ties to the "are we re-inventing
reasoning-model behavior?" open question — same kill-test posture
applies to any lens machinery.

**First slice — VEHICLE (added 2026-07-12 handoff audit; deliberately
the smallest honest step, per the kill-test posture):** the end-user
seat (b) only, because it's the one lens with live evidence behind it.
Concretely: closure verification and adversarial review each gain an
explicit end-user-seat pass — "answer as the person who asked: did I
get what I asked for, in the form I could actually use?" — as a named
section of the existing prompts (no new LLM call, no lens registry).
The Manti NOW failure is the canonical specimen (a *where* question
answered with no *where*); the shipped `_NOW_VERIFY_SYSTEM` non-answer
judging (a1f472f) is the pattern to generalize. Acceptance: on the
existing dogfood/closure corpus, the seat catches ≥1 real miss that
current checks pass (candidate: run 315ebffb's on-topic-but-wrong-
subject haiku) without new false demotions. Named lens *moves* for the
planner/navigator (a) stay vision — they must survive the kill-test
("would the same-tier model with identical context do this unprompted?")
before earning machinery.

### Learning-trust maintenance — "usage only" vs "learning" sessions (post-1.0 vision, 2026-07-12, Jeremy)

Jeremy (closing the container-executor design conversation, verbatim —
full quote in GOAL_BRAIN Decisions 2026-07-12): skill poisoning and
self-learning edges, "same sort of thing in both directions... ultimately
we will likely need 'usage only' vs 'learning' sessions, the ideal being
scanning and auto-upgrades by the system itself, which is a neverending
quest, same as a virus scanner solving protecting an individual
workstation; one way to solve it but a constant maintenance headache.
We'll get into all that more later."

Direction, not design. Doors already built when this gets a session:

- **Usage-only mode is a flag, not an architecture:** the learning
  ingestion side is already gateable per-run — `defer_learning`
  (loop_types/handle), the crystallization gates in finalize, the
  skills-lite promotion switch (`skills.lite_promotion`). A
  `learning: off` session = those seams held closed for the run, artifacts
  still produced, nothing ingested. Cheap when wanted.
- **The scanning/auto-upgrade half** is the verify→learn arc's
  expectation→verdict→demote lifecycle pointed at learned artifacts —
  VERIFY_LEARN_ARC.md V2 (cadence verdicts + auto-revert) and V3
  (graduation auto-verify) are the seed machinery; the virus-scanner
  analogy's "constant maintenance" is exactly why it must ride existing
  cadence hooks, never a daemon.
- **Trust boundaries already on record:** imports arrive contested
  (PORTABLE_LEARNING_DESIGN §8, ratified), skills-lite injection_guard +
  quarantine (cs-r2-01 family), never-auto-adopt. The both-directions
  concern (poisoned learning leaking OUT too) is the export half —
  `secret_scrub` + pack sealing cover the mechanical side; content-level
  export scanning is unaddressed and belongs to this item.

### Shared trusted skill directory + cross-instance learning (post-1.0 vision, 2026-07-10, Jeremy)

"Maybe a shared and trusted directory to pull from at a later time,
crowd-sourced or not" — the blank-slate pre-installed set (item 22) is the
seed; sharing is the scale-out. Ties directly to "sharing learning across
instances" as a post-1.0 feature: skills are the portable unit today
(`maro-import` already merges with quarantine + provenance), lessons ride
the same rails later. Trust boundary notes recorded in CAPABILITIES.md so
we don't relearn them: a shared directory is a supply chain (cs-r2-01's
threat model at internet scale) — provenance required, same
injection/dangerous-pattern gates as skills-lite, imports arrive as
reviewable candidates never auto-trusted. Direction, not design; wants its
own pass when 1.0 ships.

**Refined 2026-07-11 (Jeremy, after the social_search arc):** "an opt-in
brain for the users of this orchestration, to share knowledge and skills;
sourced, with pedigree, maro-graduated and proven skills only, and only
opt-in overall from the user's standpoint, the sharing and details are
maro-as-clients talking to a coordination server. But that's for later."
Architecture sketch this adds to the 07-10 direction: client-server (a
coordination server, not peer-to-peer), pedigree/provenance as a
first-class field, the graduation machinery as the quality gate (only
maro-graduated skills are shareable), and opt-in as a hard product
stance — default is fully local. Trigger insight was the Reddit/X access
recipes: platform-access knowledge is exactly the kind of
expensive-to-discover, cheap-to-share, decays-over-time artifact a
coordination brain is for. Still post-1.0; still direction, not design.

### Post-Purgatorio decision batch (2026-07-09, Jeremy — quotes in GOAL_BRAIN Decisions)

- [x] **Skills-lite two-tier promotion — SHIPPED 2026-07-10.** Jeremy rider
  on the graduation precedent: "we want things promoted to skills that the
  local orchestration can pick up and use while waiting for user review...
  looked at as skills-lite, and degraded the same as regular skills that
  get broken or stop working." Implementation:
  `run_curation.promote_skills_lite` (new curator, also the first BACKLOG
  #0 miner — the skill scraper): skill-shaped .md artifacts (frontmatter
  name+description+triggers/roles) from successful runs (success /
  done-unverified only; done-not-achieved excluded) copy into the
  workspace skills overlay stamped `tier: skills-lite` +
  `promoted_from: <handle_id>` — skill_loader injects them immediately.
  Each promotion registers a companion provisional Skill in skills.jsonl
  so the normal stats/decay/circuit-breaker machinery tracks it;
  `degrade_skills_lite()` quarantines the .md to `skills/_quarantine/`
  when the companion trips (circuit open) or vanishes (gc/culled) — the
  "degraded the same as regular skills" half, riding the exact demote
  signals. Fail-closed: sandbox `_DANGEROUS_PATTERNS` scan (first real
  consumer of that lane), never overwrites an existing skill name, unsafe/
  colliding candidates recorded as skipped on the run card
  (`card["skills_lite"]`) so they surface for human review. Provenance
  sidecars (create/demote) via `write_skill_provenance`. Config
  `skills.lite_promotion` default ON by decree (docs/DEFAULTS.md row;
  census green). 10 tests in tests/test_run_curation.py; live no-op smoke
  on the real workspace (no false-positive promotions on re-curation).
  SF-10 demand side: companion Skills enter the funnel with real
  trigger_patterns, so promotion pressure now has a source.
- [ ] **Official scheduler/timer layer (post-1.0; auto-resume rides it).**
  Jeremy: "maybe we need a more general official scheduler/timer that the
  user can hook into/see/manage if they wish." A visible, user-managed
  timer surface (list/inspect/disable) — coexists with the no-cron
  invariant, which bans *hidden self-rearming* schedules, not an official
  transparent one. Auto-resume of interrupted runs ((h) deferred half)
  becomes this layer's first consumer; heartbeat scheduling may too,
  pending the SF-1 supervision decision.
- [x] **Migrate the two remaining repo-copy user/ readers to
  `config.user_file()` — DONE 2026-07-10.** All three call sites
  (`handle._load_user_config`, handle's COMPLETION_STANDARD injection,
  heartbeat's mcp_servers read) now resolve workspace-overlay-first via
  `config.user_file()`; a workspace `user/CONFIG.md` /
  `COMPLETION_STANDARD.md` is honored everywhere. user/README.md caveat
  removed, DEFAULTS.md lane note updated. Test:
  `test_load_user_config_reads_workspace_overlay` (tests/test_config.py).
- [x] **Orphan scope A/B datasets — WRITTEN OFF 2026-07-12 (Jeremy,
  handoff decision batch; closes arch-03).** `~/.maro/experiments/
  scope-ab-2026-04-25-v0/` and `scope-ab-2026-04-26-v1/` hold full PAID
  treat/control run dirs with no ANALYSIS.md. Explicitly written off: the
  inject decision was already made on the 2026-04-22 evidence and shipped
  (SF-4 flip). Spend acknowledged, not silently forgotten; data stays on
  disk per the retention decree if a future analysis wants it.
- [ ] **Knowledge-web read side: wire it properly (post-1.0, KEEP) — TRACED
  2026-07-13, premise was wrong, real prerequisite work identified before
  any read-side code can pay off.** Descoped from 1.0 docs (node store +
  BM25 is the honest claim) but explicitly kept: "I'd like to keep it on
  the list. I think it could be really powerful if done well (and right
  now sounds like it isn't)." That instinct was correct — traced the real
  `~/.maro/workspace/memory/knowledge_{nodes,edges}.jsonl` data before
  writing any code, and the backlog's own framing ("write side + 2124
  edges exist; read side has zero callers") undersold the actual gap:

  - **All 2124 edges connect only `lf-` (link-farm import) nodes to other
    `lf-` nodes — zero edges touch the 252 real, system-authored
    orchestration nodes** (insight/pattern/principle/technique/tool). Not
    a sampling artifact — checked exhaustively: 2124 both-lf, 0 both-real,
    0 mixed. `build_wiki_link_edges` (the only code that could produce a
    `related` edge with two node-id endpoints) has **zero production
    callers** — these edges came from whatever process bulk-imported the
    link-farm content, not from anything in `src/`.
  - **Zero of the 252 real nodes' descriptions contain `[[wiki-link]]`
    markup** — checked every one. `build_wiki_link_edges` would produce
    zero edges even if run against them today; nobody authors nodes with
    that convention, so the mechanism the read side was meant to traverse
    is dead on the write side that actually matters.
  - Net effect: wiring `load_knowledge_edges` into
    `inject_knowledge_for_goal` as originally conceived (walk a matched
    node's edges, inject adjacent nodes) would do **nothing** for a real
    goal (the orchestration knowledge that matters has no edges to walk),
    or — if scoped to all nodes including `lf-` — would inject arbitrary
    link-farm co-occurrence pairs (e.g. a financial candlestick model
    linked to a genome-sequencing tweet linked to an open-source research
    agent, weight uniformly 0.5, "related" for everything) into goal
    context as if they were meaningfully connected. That's noise, not the
    "Correspondence" payoff — exactly what Jeremy's instinct flagged.
  - **Separate, smaller pre-existing note (not this item's blocker, just
    surfaced by the same trace):** `inject_knowledge_for_goal`'s existing
    TF-IDF node query (`domain=None` from `recall.py`) already ranks
    `lf-` link-farm nodes in the same domains as real orchestration
    insights (`orchestration`, `tooling`, etc.) — a goal could already be
    injected raw curated-tweet content today if it scores well, independent
    of edges. Not investigated further; flagging so it isn't
    re-discovered as a surprise later.

  **Fix direction, in order:** (1) decide whether `lf-` link-farm nodes
  should ever inform live goal execution at all, or should be a
  read-only reference corpus queried separately (a domain/tag exclusion
  in `query_knowledge` is a 2-line fix once decided); (2) if adjacent-
  knowledge retrieval over the *real* knowledge base is still wanted, build
  an actual edge-generation mechanism for it — since manual `[[wiki-link]]`
  authoring isn't a convention anyone follows, the realistic option is an
  LLM-assisted "does this new node relate to an existing one" pass at
  node-creation/crystallization time (same shape as the skill_candidate
  catch-up sweep shipped this session), not the existing regex-only
  `build_wiki_link_edges`; (3) only then does wiring `load_knowledge_edges`
  into injection have real signal to traverse. Left as `[ ]` — this is a
  design decision (what should the graph even encode) before it's an
  engineering task, not something to improvise past Jeremy's own stated
  uncertainty about doing it well.

### Graph memory + recursive-orchestration scoped memory (2026-06-21, vision)

**RESOLVED 2026-07-07/08 — this entry was stale until 2026-07-09.** Direction
decided 2026-07-07 (memory becomes a module; see GOAL_BRAIN.md Decisions),
bake-off same day picked a self-built sqlite3+FTS5 adapter over TencentDB
Agent Memory / Mem0 / Zep-Graphiti (`docs/history/2026-07-07-memory-bakeoff.md`),
shipped same day (`src/memory_sqlite.py`). Worker-recall-slice §7 A/B completed
2026-07-08 (16 clean runs, every measure favors the slice or ties) and Jeremy
flipped it on as the hardcoded default (`memory.worker_slice`, see
`docs/DEFAULTS.md` and `docs/history/2026-07-08-worker-slice-ab.md`). Original
brief: `docs/history/2026-07-04-memory-decision-brief.md`. **One residual not
yet decided:** the fastembed+sqlite-vec semantic lane is still gated behind
"only if BM25 measures insufficient" — full-corpus verdict (1,652 items, see
GOAL_BRAIN.md 2026-07-07/08 entries) showed sqlite-fts5 wins hit@1 + 5×
latency but loses hit@5/MRR to token-overlap; whether that's "insufficient"
enough to build the semantic lane is unmeasured/undecided. (2026-07-09
review: confirmed stays-gated, nothing blocked on it — revisit only when
organic worker-slice retrieval misses surface, with the paraphrase-lane
numbers as the evidence file.)

Durable replacement for the fixed-size inter-step truncation caps (the 800/500/200 band-aids
above — lossy fixed-array-vs-string, the kind of thing that's bitten us). Jeremy's framing:
orchestration is likely "recursive — orchestration all the way down," so a memory layer must
support **scoped/hierarchical** access — a sub-agent reads its own scope PLUS the higher
orchestration scope, built generically enough to serve both. Pairs with CAG-style caching so
sub-agents lever cached static context instead of re-ingesting. See memory
`project_retrieval_graph_memory_direction` + `project_recursive_orchestration_memory`.
NOTE: this replaces the *caps*, not the token-explosion *leak* — justify it on its own merits
(truncation is a band-aid), not on the 485K number. Ties to hybrid-retrieval priority
(start BM25+embedding, SQLite adjacency, not Neo4j until thousands of nodes).
Input from docs refactor (2026-07-04): dev-recall (`correspondence.py`) turned out to be
pure FTS5/BM25 — no embeddings ever existed despite the old "sqlite-vec" docstring — and it
had silently indexed a pre-rename ghost clone for 7 weeks (fixed: pruned + full re-ingest).
Two lessons for this design: (a) the "hybrid" in hybrid retrieval is still 100% unbuilt,
BM25 alone is what we run on today; (b) any index needs a staleness/provenance check
(sources-on-disk assertion) or it rots invisibly. `lat.md/` + `lat_inject.py` fate also
folds into this decision (see docs/INDEX.md note).

### Design constraint: decay trust, never data

- [x] **Retention-decree audit — 3 violations found and FIXED 2026-07-10**
  (Jeremy: "let's fix, no time like the present"). Sweep of every deletion
  site in src/ against the retention decree found the same auto-deletion
  family the step-artifact bug belonged to:
  1. **Lesson decay-GC deleted memory** (the path that once ate the whole
     38-lesson MEDIUM store): `run_decay_cycle` + `gc_memory` now archive
     to `memory/lessons_archive.jsonl` before dropping from the live store;
     `search_graveyard` reaches the archive and resurrects via
     `resurrect_archived_lesson()`; `forget_lesson` archives as
     `user_forget` (excluded from auto-resurrection — forgetting is the
     user's call); `maro-knowledge` stats surface the archived count.
  2. **Skill island culls + A/B variant retirement hard-deleted skills**:
     both now archive to `memory/skills_archive.jsonl` + write a `retire`
     provenance record.
  3. **Finalize deleted the checkpoint on done**, but closure verification
     runs after finalize — a run demoted done→incomplete had already lost
     its resume state. Checkpoints now kept on completion (stranded-sweep
     already skips finalized runs via metadata status; `checkpoint delete`
     CLI remains the user-level removal path).
  Enforcement so the class can't recur silently:
  **tests/test_no_silent_deletion.py** — AST census of every file-deletion
  call in src/ (unlink/rmtree/os.remove/rmdir incl. aliased/bare-import
  forms) against a justified allowlist, plus a pin that nothing outside
  checkpoint.py references `delete_checkpoint`. Same pattern as the
  DEFAULTS.md census tripwire. Known limit: record-level rewrites aren't
  generically detectable — the two known record-level deleters are the
  ones fixed above, pinned by unit tests.

- [ ] **Graduation proposals have no autonomous consumer; full behavioral
  verification remains VERIFY_LEARN_ARC V1–V3.** Graduation writes pending
  suggestions after the current evolver auto-apply loop, and later evolver
  runs do not consume prior pending rows. The live workspace has no
  `graduation:` suggestions, so there is no organic evidence that this path
  has ever made a rule live. The 2026-07-14 precursor safely checks only rows
  already marked applied and records manual authority; it does not prove
  improvement, revert, or demote. Templates currently mix observations,
  lesson-like prompt tweaks, and gated guardrails rather than one durable
  standing-rule type, and prompt tweaks lack a true rollback target. Starting
  the full V1 expectation → V2 verdict/authority → V3 revert/demotion arc is
  an owner-level scope decision, not an incidental wiring change.

- [ ] **Design constraint, not a task: decay trust, never data.** Append-only
  evidence layer stays perfect (the computerization edge over human forgetting);
  only compiled-truth confidence decays. Crystallization Stages 4–5 must be
  demotable back to language form — world-change is the frequent trigger,
  model upgrades the rare one. Partially embodied: "Decay-by-invalidation v0"
  (`knowledge_lens.py`, 2026-06-11) decays Stage-5 rule *trust* on recorded
  contradictions without touching data — but `knowledge.py` demotion only goes
  Stage 5→Stage 4 (rules→skills), NOT back to language form (Stage 2/3). The
  language-form demotion path is the part still open. Input to the memory
  architecture decision.

### File-claim fabrication — residuals (v1 guards SHIPPED 2026-06-26, archived to BACKLOG_DONE)

The three shipped layers (FS-diff missing-artifact, inert-output AST,
execution-contradiction) moved to BACKLOG_DONE 2026-07-04; the guard lives in
`loop_execute.py` post-split (originally wired in agent_loop.py). Kept here:
the rejected design (a documented trap) and the deliberately-deferred shapes.

- **REJECTED: no-path-write layer.** Prototyped (write-ish words + empty diff +
  no path named) and reverted same day: it is **absence-based, not
  evidence-based** — an empty workspace diff does not prove fabrication
  (analysis/planning steps and out-of-workspace writes legitimately leave it
  empty). It false-positived on 4 real test completions in the full suite. A
  verifier that hallucinates is its own failure mode; the guard now only fires on
  positive evidence (a named-but-absent file, or an inert file vs a concrete
  output claim).

- [ ] **Remaining exec-fabrication shapes (deliberately deferred — false-positive
  risk).** Two cases the v1 contradiction check intentionally does NOT flag,
  because each can fire on legitimate runs (same lesson that killed the
  no-path-write layer): (a) **"claims execution but ran nothing"** — the per-step
  transcript can't see a prior step's legitimate run, so absence ≠ proof; (b)
  **partial** — some commands succeeded and a later/key one failed; telling the
  test command from setup needs intent modeling, and fix-then-succeed is
  legitimate. Revisit only with a sharper signal (e.g. matching the claimed test
  count against the real `tool_result`), not a looser gate.

### DESIGN SPACE — Thread Architecture (2026-04-26 sketch; narrow navigator SHIPPED, full reframe unbuilt)

**Doc:** `docs/THREAD_ARCHITECTURE.md` (the sketch + decisions + open list)
**Conversation log:** `docs/conversations/2026-04-26-thread-architecture.md` (literal transcript)
(The `arch/thread-navigator` branch was merged to main via 131d629 and deleted — no separate branch anymore.)

The 1-shot-first DISCUSS item (formerly here) expanded into a full architectural sketch over a 7-turn planning conversation. Rather than just inverting the planning default, the conversation reframed the unit of orchestration to **thread**, with a per-turn `navigator → work → navigator` loop, navigator-selected personas, sub-thread fork/collate, build-folder-as-thread-residence, and crystallization (Stages 1–5) as the navigator's improvement path.

**Status 2026-07-04 — distinguish shipped from unbuilt.** The *narrow* navigator
is real and live: dispatch + blocked-step judge (`navigator_shadow.py`), per-thread
goal brain (`thread_brain.py`), escalate cutovers enacted on this box (MILESTONES
#1/#2), thread-brain maintenance closed (MILESTONES #3). The *full reframe* —
per-turn navigator→work→navigator loop, sub-thread fork/collate (gated on
MILESTONES #4 async fork join), navigator-selected personas per turn, thread as
the unit of orchestration — is NOT built. The 9 open decisions in the doc need
re-scoping against what shipped before any further implementation.

**1-shot-first** is preserved as one move-shape the navigator picks per turn (not the default; navigator decides whether to plan or execute). Existing planning scaffolding (`decomposition_too_broad`, mid-loop redecompose, scope-as-armor) probably shrinks but does not delete — Jeremy pushed back on aggressive deletion (Tesla-vs-driver: confident-sounding LLM ideas without critical-thinking-edges drift, because people's context ≠ LLM context).

**Adjacent items that should be re-evaluated under this frame** (2026-07-04: two struck as shipped):
- Intent resolution (next entry) — folds into "fork+collate" sub-thread mechanism
- Captain's log infrastructure-vs-visibility (new) — should be demoted to data, not infrastructure
- ~~Persona auto-selection~~ — SHIPPED (`persona_for_goal`, c964d3b; wired in conductor + handle)
- ~~Recall() interface~~ — SHIPPED (`src/recall.py`, 9f1a43a)
- Crystallization Stage 5 (existing gap in `KNOWLEDGE_CRYSTALLIZATION.md`) — the navigator's cheaper-over-time mechanism
- Shared-learning portability (new) — self-learned artifacts should survive HDD loss / orchestrator switch

### Intent resolution — naming the "side-quests before decompose" shape (discovered 2026-04-18)

Run 7 of slycrel-go surfaced (again) that "done" means "the plan we guessed
up front got executed," not "the goal's artifact exists." The server was
built. The browser client wasn't — and the prompt explicitly said "browser
as a client." Closure missed it because closure checks against the plan's
deliverable list, and the plan's deliverable list was itself a 1-shot guess.

We keep writing pieces that nibble at this (`scope.py`, closure,
inversion, ralph, director-restart) and stopping there. The structural
phase missing is: **delay decomposition until intent-resolution
side-quests have settled the unknowns.** See
`docs/INTENT_RESOLUTION_DESIGN.md` for the full sketch + the minimum
experiment proposal.

**Partially shipped (2026-04-23, ResolvedIntent v0):** the deliverable-map
prompt + resolved-intent artifact schema subs moved to BACKLOG_DONE — shipped
as `scope.py` ResolvedIntent/Deliverable + `generate_resolved_intent()`,
persisting `resolved_intent.md`. Side-quest orchestration remains open.

- [x] **Minimum experiment — RESOLVED 2026-07-09 (Jeremy): accept v0 on
  organic evidence, retroactive A/B dropped.** The done-vs-achieved corpus
  analysis (1.0 arc) is the cheaper honest check on the closure ceiling.
  Full context in BACKLOG_DONE.
- [ ] **Pivot reuse across goal-family reruns.** (Narrowed 2026-07-04: the
  infrastructure half exists — per-project persistent dirs under
  `~/.maro/workspace/projects/<slug>/` are live and goal-slug-bound. What's
  missing is the *reuse* logic: a rerun/rephrase of the same goal family
  neither detects nor feeds prior side-quest artifacts back as context. The
  `polymarket-edges` ledger pattern (project_polymarket_edges.md memory) is
  the proof of value; generalize that.)
  **Deterministic project-family reuse shipped 2026-07-14:** every full AGENDA
  run now persists its resolved project in run metadata; dispatch/loop recall
  treats the same project as a family match even when the rephrase has low word
  overlap, and injects a bounded inventory of durable project/artifact paths so
  the planner can inspect and reuse prior side-quest products. Literal project
  names (for example `polymarket-edges`) already bind project-less dispatches
  through `_match_existing_project`. No LLM or embedding call was added.
  **Residual:** a rephrase that neither supplies nor names the old project still
  mints a new goal slug and cannot be safely joined semantically. Keep this item
  open for an evidence-backed family resolver; do not lower the 0.9 Jaccard
  threshold and risk unrelated-project context contamination.

### Modular refactoring (AFK-friendly chunks, queued 2026-04-18) — deferred chunks

Jeremy's framing: LLMs don't feel rework cost the way humans do, so our
codebase has accumulated seams that are hidden (not broken, just hostile
to the next edit). These chunks are sized so one session can ship one of
them cleanly without needing real-time direction. Pick any of them when
looking for an AFK-friendly chore. Principles in `docs/CODING_NOTES.md`.

- [ ] **Test clutter trim.** Jeremy's outside-in-testing posture
  applied to the suite: tests that poke private functions with mocked
  collaborators and assert call-shape are performative. Sweep tests
  touched during recent refactors and mark ones that would break on
  a rename-without-behavior-change — delete the clearest offenders,
  keep anything covering a module boundary or regression. Don't do
  a mass pass; trim opportunistically when editing neighboring code.
  (Tracked as a posture, not a standalone chunk.)

### Storage decision — sqlite indexer (deferred)

- [ ] **Storage decision (deferred).** JSONL captain's log is fine for within-run analysis. Sqlite *indexer* on top (not replacement) is the right pattern when cross-run queries become routine — "median treat-vs-control delta across N runs," "all CLOSURE_VERDICT < 0.5 in last 30 days." Defer until we have a concrete query we keep wanting.

### Step-to-goal elevation

- [ ] **Step-to-goal elevation.** When a step's elapsed time or token
  spend crosses a threshold, pause it, capture its state, respawn as a
  child goal with its own decompose/execute/verify loop, merge result
  back. Invasive (state handoff + result merge + parent-loop resumption);
  wait for heartbeat signal to tell us *which* steps actually need this
  before building.

### Phase 65 — Constraint/Premise Orchestration (MVE implemented, dormant)

See `docs/CONSTRAINT_ORCHESTRATION_DESIGN.md` + `docs/CONSTRAINT_ORCHESTRATION_REVIEW.md`. **Status 2026-07-04:** the MVE shipped — `scope.py` inversion behind `scope_generation` / `scope_ab_skip` config flags, both default OFF (handle.py), PAUSED by Jeremy 2026-04-23. Items below are the review's sharp findings; three of the original blockers are resolved by how the MVE shipped, the rest stay open for any expansion beyond the dormant MVE.

- [x] ~~BLOCKER: Autonomous-path behavior.~~ Resolved as shipped: no gate — scope output is logged and used as planner context, exactly the "log for post-hoc review, continue, no gate" default this blocker recommended.
- [x] ~~BLOCKER: A/B mechanism.~~ Resolved as shipped: `scope_ab_skip` flag is the A/B capability (run goals both ways). Note: capability exists; the actual A/B measurement has not been run.
- [ ] **BLOCKER: Cost ceiling.** Largely covered since: per-run + daily budget gates shipped 2026-07-01 (`budget.per_run_usd` / `budget.daily_usd`). Residual question is only whether scope-generation calls need their own sub-budget; probably not — verify before expanding Phase 65.
- [ ] **Gate heuristic.** Design's "AGENDA goals above N words" is wrong (short goals often benefit most, long ones often don't). Needs an actual judgment signal — possibly complexity classifier, or "use for goals with ≥3 deliverables."
- [x] ~~Triad vs. single persona.~~ Resolved as shipped: single persona, per the review's recommendation. Triad remains unvalidated and should stay out unless ablation shows different constraint lines (next bullet).
- [ ] **Persona content vs. costumes.** Design assumes personas produce genuinely different perspectives. Current `persona.py` is largely system-prompt overrides + skeptic modifier. Validate that PM/engineer/architect personas *actually* draw different inversion lines (not just prompt flavor) before investing in triad.
- [ ] **Scope: verification sibling.** Design addresses the *planning* phase. Biggest defect in the system is in the *verification* phase — slycrel-go "passed" because nobody ran a browser. Constraint-setting alone won't close this gap. Needs sibling design for ground-truth verification (real browsers, real endpoints, real test execution — not LLM judgment).
- [ ] **Completion-standard coexistence.** Design says "completion standard is subsumed." Migration plan needed: does completion-standard still run during rollout? If both, do they contradict?
- [ ] **continuation_depth interaction.** Phase 64 restart carries ancestry context across boundaries. Constraints/premises must also be preserved (or explicitly refreshed) across restart. Design is silent.
- [ ] **Concurrent-loop interaction.** `team:` and DAG executor run parallel workers. Do they share the constraint set? Who catches cross-worker conflicts that individually-satisfy-but-together-violate? Unspecified. *2026-07-09 note: the concurrency-hardening arc (fail-closed file_lock, admission gate, worktree isolation) made parallel workers **file/git-safe** — this item is the remaining **semantic** layer (shared constraint set, cross-worker conflict detection) and is explicitly a follow-up, not covered by that arc.*

### Verifier synthesis as a deliverable (scope's other half)

**First real slice SHIPPED 2026-07-12 →
`docs/history/2026-07-12-routing-and-probe-synthesis-design.md` Part B** (Deliverable.shape,
shape-conditional behavioral-probe MUST with logged waiver, probe-env
hardening incl. the cwd=None residual; chunks B1–B3, see MILESTONES -5). The
full BDD red-green loop below stays deferred until the honest-measurement
prerequisites ship (now satisfied) — this entry remains the long-arc record.

Two residual gaps surfaced by adversarial-review pass 3 (2026-07-12, scoped
skeptic pass on the pass-2 fix commit `0621417`) — both judged real,
in-scope for the full BDD loop below, not for B1-B3, and documented in-code
rather than fixed with a fragile heuristic:
- [ ] **Waiver content isn't judged, only presence.** `behavioral_probe_waived`
  suppresses the B2 MUST (`closure_verify._detect_behavioral_gap` Signal 3)
  on ANY non-empty string — a pretextual waiver ("static compile proves it")
  bypasses it exactly as well as a genuine one. Needs an LLM judge (new
  verifier-LLM scope) or would otherwise require the external-taxonomy
  approach this function's own docstring says to avoid. See
  `closure_verify.py` Signal 3 comment. Pinned so this is testable-against,
  not just prose: `tests/test_director.py::TestDetectBehavioralGap::
  test_known_gap_pretextual_waiver_still_suppresses_signal3` — flip the
  assertion once waiver-content judging ships.
- [ ] **A "fail" outcome isn't checked for relevance, only cleanliness.**
  B3(b)'s confidence cap (narrowed in pass 2 to exempt any clean
  `outcome=="fail"` from capping) can't distinguish a real, meaningful
  failure from a brittle/irrelevant check the plan LLM wrote badly — both
  now uncap the same way. Mechanically irreducible with only
  pass/fail/inconclusive counts; needs a check-to-deliverable relevance
  signal that doesn't exist today. Accepted per an explicit asymmetric-cost
  argument (over-eager demotion costs one bounded `closure_restart`; a
  wrongly-suppressed real failure silently poisons `goal_achieved`) — see
  `closure_verify.py` B3(b) comment. Pinned: `tests/test_director.py::
  TestProbeEnvHardening::test_known_gap_irrelevant_fail_still_exempts_
  confidence_cap` — flip once a check-to-deliverable relevance signal
  exists.
- [ ] **Heuristic live-data regex misses named-place phrasing.**
  `_LIVE_DATA_RE` (no-LLM fallback path, `intent.py`) only catches
  current/latest/today wording; asks like "where can I get non-ethanol gas
  near Manti, Utah" still route NOW even though the LLM path correctly
  routes the same question AGENDA via `needs_live_data`
  (`test_llm_needs_live_data_forces_agenda`). Confirmed still-open by 3
  independent adversarial reviewers 2026-07-12; accepted as a deliberately
  narrow lexical approximation (design doc DECISION at
  `docs/history/2026-07-12-routing-and-probe-synthesis-design.md:70`), not
  a bug to chase. Pinned: `tests/test_intent.py::TestLiveDataOverride::
  test_known_gap_named_place_live_data_not_caught_by_heuristic`.

- [ ] **Verifier synthesis phase.** Dream-level: orchestrator builds its own verifier when none exists, rather than degrading to LLM judgment or failing as "hard." Framing: BDD + TDD. Scope declares Given/When/Then (what must be true for "done"). Execution includes a mandatory red-green pair: synthesize an executable probe, break the code on purpose to confirm it catches the failure, fix the code, probe goes green. The probe is a first-class checked-in artifact.

  **Needs additional scoping (Jeremy, 2026-07-12, agreed).** Three concrete
  residual-risk pin tests now point at this item (waiver content unjudged,
  fail-relevance unjudged, heuristic regex gap — see above) on top of the
  original slycrel-go motivating anecdote below. No longer just an
  open-ended "dream-level" aspiration; worth a real scoping pass (MVE
  sizing, which open question (a)-(d) below gates first) before treating
  it as a queueable chunk. Not started — noted, not yet scheduled.

  Motivation: slycrel-go "done" run (loop `bd9b581c`, 2026-04-16, 1.55M tokens, status=done) passed `go build` while nothing exercised the binary. Three real bugs (`atomicWrite` race, silent `os.Executable` error, ignored write errors) survived untouched — caught only by the follow-up `identify-and-fix-the-3` review run. Scope alone would have named the gap; a synthesized probe would have closed it.

  Replay result after Phase 65 + closure wiring: materially better, but still half-real. The replay refused to mark the branch done, yet the decisive catch was static: closure found hallucinated `xterm.js` claims in the work summary via repo inspection, not via booting the server or exercising the client. This is progress, but it exposes the remaining defect precisely.

  **Concrete defect: runtime-probe bias.** Closure-plan synthesis defaults to static/code-inspection probes (`grep`, `test -f`, source reads) even when the prompt explicitly permits live checks. In the slycrel replay all generated checks stayed static; none started the server, hit `/health`, opened a websocket, or drove browser/client behavior. The verifier is real enough to catch hallucinated code content, but still weak on unexercised runtime behavior.

  **Likely cause:** the current prompt rewards checks that are fast, safe, read-only, and self-cleaning, but does not provide cheap lifecycle scaffolding for runtime probes (boot ephemeral server, wait for readiness, hit endpoint, clean up). The LLM is taking the path of least resistance, not refusing in principle.

  **MVE:** one goal class ("build X that does Y") requires scope to declare ≥1 executable probe (shell script, curl+WS, Playwright spec). Step graph adds a mandatory "probe-fails-on-broken-code → probe-passes-on-fixed-code" pair. Compare outcome quality + regression rate vs checklist-complete path.

  **Implementation direction for the first real slice:**
  - add lightweight runtime-probe scaffolding examples to the closure plan prompt (boot in background, readiness wait, cleanup trap)
  - require at least one behavioral probe for runtime-delivering goals unless the planner explicitly explains why it is impossible in this environment
  - log probe modality for evals (`static`, `process`, `http`, `ws`, `browser`) so closure quality can be measured instead of guessed

  **Secondary issue:** probe brittleness/calibration. One replay check false-positive'd because the grep pattern for `RemoteAddr.*username` was stricter than the real log line. After runtime-probe bias, harden probe robustness so static checks do not become noisy theater.

  **Open questions:**
  (a) recursion — who verifies the verifier? Bounded version: the "break it on purpose" step IS the verifier-of-verifier.
  (b) which goal class first — probably build/implement missions, since research/report missions have softer success criteria.
  (c) interaction with completion-standard — does the probe subsume it, or both run?
  (d) cost ceiling — synthesizing + running a probe adds LLM calls and execution time; need per-goal budget.

  Related: BDD (Given/When/Then framing), TDD (red-green cycle), property-based testing (∀ operation, property holds), mutation testing (probe-of-probe bounded version). Sibling of Phase 65 "Scope: verification sibling" blocker above — this IS that sibling. **Cross-link:** also the sibling of the Actionable "Closure treats failed-to-run commands as checks-passed" item — runtime-probe bias is closure *choosing* static over behavioral probes; the closure-failed-to-run item is closure *mis-reading* the behavioral probes it does choose. Same root: the verdict is decoupled from whether the thing was verified.

  **Replay raw numbers** (evidence for the bias finding above): `~/.maro/workspace/projects/slycrel-replay/artifacts/summary.json` — `complete=False, confidence=0.35, 3/5 checks passed`. The two failing probes: (i) overly-strict grep for `!RemoteAddr.*username` false-positived on a legit log line `log.Printf(... username, r.RemoteAddr)`; (ii) `grep -qi xterm web/*` correctly caught that the work summary hallucinated xterm.js integration. The `_CLOSURE_PLAN_SYSTEM` prompt (now at `closure_verify.py:29`, imported by director.py post-split) says "Commands must be fast (<15s), safe (read-only or self-cleaning), exit 0 on success. Wrap background processes with `timeout` and always clean up PIDs" — permits live probes but nudges toward grep via path-of-least-resistance. (2026-07-04 status: the "implementation direction" modality-logging bullet above is SHIPPED — closure logs probe modality, and `_detect_behavioral_gap` / `_detect_diagnosis_gap` exist in closure_verify.py. The scaffolding-examples + required-behavioral-probe bullets and open questions (a)–(d) remain open.)

  **Second full run (2026-04-17, after observability fixes) — modality chart is stark.** CLOSURE_VERDICT event recorded `modality_distribution={"static": 4, "process": 1}`, zero http/ws/browser — on a goal explicitly about "headless server with browser as a client." Closure's own summary admits: *"Gap: runtime validation (server startup + browser connection) was not performed."* Yet it still returned `complete=True confidence=0.92`. Manual post-hoc runtime probe (3 curl calls, ~5 seconds): `/health → 200`, `/ → 200`, `/ws → 101 upgrade`, server logs `player "test" connected/disconnected`. The thing works; closure lucked into being right via static checks. The cheap, mechanical proof would have been three curls — and the system *had time*: the loop ran 810s / 3M tokens / 39 steps. Budget was not the constraint; scaffolding was.

  **Cross-cutting: adversarial review was the hallucinator on this run.** The loop's own adversarial review contested "Go not installed on this machine" and "headless-browser-client branch does not exist" — both false (Go 1.24.2 at `~/go/bin/go`, branch at `origin/headless-browser-client@4fdf0202`). Step output was substantially accurate; the review fabricated contradictions. Suggests the review path needs the same inversion-at-verification discipline: dispute a claim → run the probe that settles it. Currently reviews reason from priors without grounding.

### Composable decision-point hooks (design exploration)

- [ ] **Composable decision-point hooks** — (2026-07-04 correction: `step_events.py` was built, accumulated zero real handlers, and was PRUNED in the repo-wide refactor — see REFACTOR_PLAN. The live interception surfaces are inspector observation, quality gate, and prompt injection of standing rules/lessons/skills into decompose.) These aren't composable: you can't say "after decompose, before execution, run extra verification on steps 3 and 5." MTG-style stack where effects can be intercepted at targeted points. For now, prompt-stage injection is sufficient. Revisit when operational experience shows which decision points actually need interception. Key constraint: any self-extensibility must be human-gated (see evolver guardrail auto-apply fix).

### Phase Transition Contracts (architecture — revisit after operational data)

- [ ] **Formal stage contracts between pipeline phases** — Currently phase transitions are implicit: decompose outputs strings, execute takes strings, finalize takes outcomes. No typed contracts, no hard validation gates between phases. Pre-flight is advisory-only (loop proceeds regardless). Trajectory check is the first real mid-pipeline gate. Need: (1) typed output contracts per phase (not just "a list of strings" but "atomic steps that cover the goal scope"); (2) hard gates that re-plan or abort instead of proceeding with garbage input; (3) audit which existing checks are load-bearing vs noise. The Starship optimization: delete the advisory checks that never change behavior and replace with fewer, harder gates. Defer until operational data shows which gates actually matter.

### Phase 38 subpackage move

- [ ] **Phase 38 subpackage move** — src/ is flat, now at ~130 modules (was 49 when this was written). Successor plan: `docs/REFACTOR_PLAN.md` Tier 4 is this same move, sized against current reality. Deferred (33+ imports per group), revisit when it causes real problems.

### Agentic verifier for large artifacts

- [ ] **Agentic verifier for large artifacts.** Today the validator sees a bounded
  in-context slice of the result (`validate.max_input_chars`, default 6000 for the
  free local path vs 1200 paid). For multi-KB artifacts, stuffing the whole thing
  into context is wasteful — a tool-using verifier that reads the artifact
  selectively (grep/read a temp file) is the better pattern. Caveat: that needs
  tool use, which a small specialist (VibeThinker) is weak at — so scope it as an
  opt-in verifier tier, not the default. (Input/output limits are separate knobs:
  `max_input_chars` = what it sees; `local_max_tokens` = what it can generate.)

### Linux local-validator burn-in

- [ ] **Replay the committed validator corpus on the production Linux box before
  enabling Ollama there.** The formal M1 sweep is complete (BACKLOG_DONE): all
  four small candidates used the same 14 cases and exact production protocol.
  It selected VibeThinker-3B-4bit on Apple Silicon and rejected Ollama
  qwen2.5-coder:3b despite its speed because it produced two unsafe decisive
  false-passes. The 2014 Ubuntu Mac mini experiment is not a viable deployment
  proof. This residual is hardware-gated: choose a Linux candidate, replay with
  `scripts/validator-bakeoff.py`, and require zero unsafe decisive false-passes
  plus warm latency under the configured breaker before enabling it.

### "Count the files" closure scope

- [ ] **"Count the files" closure blessed two different answers** — same goal,
  loop 1 counted 45 (docs/ top-level), gate-escalated loop 2 counted 80
  (recursive); *both* closure verdicts called their count "correct and
  verified". Ground truth: both defensible readings of an ambiguous goal —
  but closure verification inherits the executor's interpretation instead of
  pinning one. Resolved-intent/scope is the existing seam that should pin
  countable deliverables ("N = recursive count") before execution.
  - **2026-07-14 partial fix:** resolved-intent now requires quantitative
    deliverables to state their measurement boundary, renders any
    director-proxy commitment as a binding goal definition for the planner,
    and supplies the same commitment to both closure-plan and closure-verdict
    calls. Regression coverage proves the interpretation survives all three
    handoffs instead of becoming audit-only metadata.
  - **Remaining activation decision:** `scope_generation` is deliberately
    default-off because it adds an LLM call to every agenda run. The contract
    is fixed when that experiment is active; preventing the original incident
    on default configuration still needs either operator approval to enable it
    or evidence for a narrower activation rule. Do not hide that spend change
    behind a count-goal regex.

### Benchmark/eval mission isolation — DONE 2026-07-14 → BACKLOG_DONE

The measurement-class work supplied a reliable benchmark boundary. Normal
eval cells now own fresh projects; the direct-Director worker-slice harness
owns fresh retained workspaces. Full evidence and the historical m3 caveat are
archived in BACKLOG_DONE and `docs/history/2026-07-08-worker-slice-ab.md`.

### host-check.sh alerting — DONE 2026-07-09 → BACKLOG_DONE

Telegram (notify.command lane) + daily cron 08:05, via new
`scripts/host-check-notify.sh`; failure path live-proven before scheduling.
Full context in BACKLOG_DONE.

### Standing test-goal menu (future ideas)

- [ ] **Polymarket behavioral test** — "Analyze 400M+ Polymarket trades to find behavioral patterns among top wallets — what do winners do differently?" (from hrundel75 link)
- [ ] **"Get Jeremy rich" prompt** — long-term, after trading patterns are validated and backtested. Baby steps.

### Conservative — verify before dropping

These four are kept (not deleted) this triage pending verification against current code/data.

- [ ] **done != achieved, confirmed on organic runs — and the gap is large.** (verify before dropping)
  First organic batch through the new goal-verdict metadata (2026-06-12, 5
  real goals): 4 came back `done` but only **1** had `goal_achieved=True`. The
  three done-but-not-achieved (health-report refresh, roadmap audit, weekly
  digest) all wrote a structurally-correct artifact the closure verdict judged
  as falling short — "file created and non-empty" / "5/6 checks" — at low
  confidence (0.2–0.35). Two implications: (1) the done≠successful split is
  doing exactly its job — without it this batch reads as 80% success; with it,
  20% genuinely achieved, the rest flagged for review. Validates Jeremy's
  "done as 'I did it' not 'it worked'" concern with live data. (2) The verdict
  confidences are *low* — these are doubt flags, not definitive failures, and
  they correctly stay `done` (below the 0.7 demotion threshold) rather than
  flipping to incomplete. Open question worth watching: is the closure verifier
  systematically harsh on build-artifact goals (false-negative achievement), or
  are these outputs genuinely thin? Needs a few more organic batches + spot
  audits before trusting the rate. Don't tune the threshold on n=5.
  **Update 2026-07-04: the data now exists** — ~68 judged runs with verdict
  metadata on disk (~26 achieved). The gate is analysis, not data: re-run the
  done-vs-achieved rate check on the full corpus before touching thresholds.
  **ANALYSIS RUN 2026-07-09** (`docs/history/2026-07-09-done-vs-achieved.md`,
  1.0 item (b)): 72 verdict runs, era-segmented at 90b4d1b (55 poisoned
  excluded). Clean era n=17: done 65%, achieved 53%; organic slice n=10:
  raw achieved 40%, corrected ~60-70% after spot-audit (2 of 4
  done-but-not-achieved were verifier false negatives: verbatim-grep on
  paraphrase tasks, wrong-section grep on append-only ledgers; +1
  false-on-its-evidence at conf 0.95 via probe-env mismatch). Closure IS
  systematically harsh on build-artifact goals, but errs safe: zero false
  blessings post-fix, all false negatives below the 0.7 demotion threshold.
  **Verdict: keep 0.7; 1.0's gap is packaging, not closure quality.** Fix
  lever = probe-env hardening (cd to goal-named repo, cap confidence on
  environment-error signatures), not threshold tuning. Standing caveat: raw
  goal_achieved understates organic success ~20-30 points — don't feed it
  unadjusted into verify→learn; re-run at organic n≈30.
  **Prospective gate shipped 2026-07-14:** normal `maro handle` / `maro run`
  work now stamps `measurement_class=organic` into both run metadata and the
  durable outcome row; synthetic callers can explicitly select `smoke`,
  `control`, or `benchmark`, and dry runs are excluded. `handle_id` on the
  outcome collapses restarted loops to one run. Run
  `scripts/verdict-gap-stats.sh` (or `--json`) to see the n≈30 gate. It counts
  only judged, explicitly-organic rows; missing/legacy labels remain unknown
  and raw `goal_achieved` is named as an uncorrected verdict rate. Current
  unified ledger: 3 unknown legacy rows, 0 prospective organic rows, gate not
  due. The old n=10 hand-classified slice is historical evidence, not silently
  carried into the new counter. Keep this item open only until the report says
  the manual artifact re-audit is due.
  **Tangential architecture finding (2026-07-14 adversarial review):**
  `handle_task`'s budget-ceiling `loop_continuation` lane still calls
  `run_agent_loop` directly, outside the normal run ownership + closure-verdict
  lifecycle. This change now carries the parent handle/class explicitly, clears
  stale ambient run context, and conservatively lets the newer continuation row
  make the top-level request organic-but-unjudged. That prevents metric
  contamination, but it also exposes the larger pre-existing gap: a successful
  multi-pass continuation never receives the terminal closure verdict that
  would let that request enter the judged cohort. Fix by giving continuation
  consumption the shared run/closure lifecycle (not by teaching this report to
  bless the earlier partial pass). This belongs with the dedicated
  Verify→Learn/closure design arc; it is too large to smuggle into a stats
  report.

- [~] **`decomposition_too_broad` residual.** (verify before dropping) The cache-aware conversion (2026-06-22) removed the observed noise source; remaining open question is whether a step doing genuinely >200K *fresh* tokens on an otherwise-successful run should warn at all, or only when the loop also shows stress (blocked steps / budget exhaustion). Revisit only if a real fresh-heavy run flags spuriously. (Full block archived to BACKLOG_DONE; this is the residual watch-item.)

- [ ] **Per-class routing (gathering shadow-eval data).** (verify before dropping — open children retained) Expect high agreement on
  verifiable code/math steps, low on fuzzy research-quality steps. Once the
  `--agreement` table has enough rows, route only the classes where the local judge
  earns it (per-class `min_certainty`); keep the rest on the paid path. Don't trust
  benchmark parity globally.
  **First data (2026-06-23, n=29, qwen2.5-coder:3b vs paid):** overall agreement
  96.6%, **0 false_pass across every class** (the dangerous direction — local PASS /
  paid FAIL — never happened). Per class: analyze 4/4, exec_command 4/4, synthesize
  3/3, read_artifact 1/1 all 100%; `general` 16/17 (94.1%) with the lone miss a
  **false_fail** (local FAIL@0.90 vs paid PASS on a routine file-save — local was
  *too strict*, costs a wasted escalation, not a missed defect). Surprise: the fuzzy
  synthesize/analyze essay-critique steps held at 100% — divergence showed up on a
  mundane `general` step, not the subjective work we expected to break it.
  Calibration: 0.9–1.0 bucket = 96.6% (slightly overconfident, erring strict).
  **Caveat: 29 rows is a smoke sample, not enough to set thresholds.** Next: a larger
  deliberate batch (more runs with diverse step mixes) before committing per-class
  `min_certainty` — and watch specifically for any `false_pass`, since that's the
  only error direction that can let a real defect through.
  **Larger batch (2026-06-24, n=42):** 92.9% overall, and the **first `false_pass`
  appeared** — `general` class, local PASS@**1.00** vs paid FAIL. The step was
  "list skills/ and save the listing to `artifacts/skills-listing.txt`"; the worker
  saved to a *different* path and narrated success. Local can't see the artifact
  never landed where asked — a requirement/side-effect miss, not a confidence
  problem (it fired at max confidence). Concrete classes held: exec_command 5/5,
  analyze 5/5, synthesize 3/3 — 100%, 0 false_pass; read_artifact 4 (75%, all misses
  false_fail/safe). **Decision: do NOT set per-class `min_certainty`.** (a) The
  safe-class n (3–5) is too small to justify lowering thresholds; (b) the danger
  class `general` can't be made safe by a threshold — the false_pass was at conf
  1.00. The lever the data actually points at is **provenance verification** (did
  the side effect land / was the requirement met?), which is the same root as the
  fabricated-input bug and is exactly the closure-verdict-provenance-net item above.
  So #3 feeds #2. Keep global `min_certainty: 0.6`; revisit per-class only after the
  safe-class corpus is much larger. Full write-up: `docs/LOCAL_VALIDATOR.md`.

### `orch.py` legacy loop — DEPRECATED 2026-07-09 → BACKLOG_DONE

Jeremy confirmed `maro tick`/`loop`/`plan` unused → stderr deprecation
warnings + docstrings + tripwire test shipped. Residual (rides the Tier-4
subpackage move, not a standalone item): remove the trio + promote/rename
the path/NEXT.md layer as the real `orchq`/paths subsystem. Full context in
BACKLOG_DONE.

### Run visibility residual — general-purpose server question (main entry → BACKLOG_DONE 2026-07-09)

- [ ] **Deferred: does a live server surface belong at all?** The static
  per-run report + cross-run index (`src/loop_report.py`) shipped and merged
  2026-07-09 — full history in BACKLOG_DONE.md "Run visibility: static
  per-run report + cross-run index". What that build deliberately excludes,
  and what the 2026-07-02 dashboard archive left open: a live (auth'd,
  read-only-by-default) server view, and whether goal-submission/replay
  controls ever belong in the same surface. Needs product discussion first;
  static files are the answer until cross-run browsing becomes a real habit.

### 1.0 install trial residuals (2026-07-09 docker clean-machine trial)

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

### 1.0 launch content + learning/sharing (Jeremy, 2026-07-09 — scope decree)

Decree: "learning and sharing needs to be part of the official first
release" (full quotes in GOAL_BRAIN Decisions 2026-07-09). Three items,
also listed as MILESTONES -3 remaining (e)/(f)/(g). Sequencing: (e) runs
after the current 1.0 remainders (a)–(d); (g) needs design before release.

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
    bundled-assets is an open design gap, noted for (g)/post-1.0.
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
  verdict in `output/adversarial-review-2026-07-13-portable-learning.md`
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
  review wanted on: billing-failover default, 1-auto-resume cap,
  resume-surface (CLI vs notify), depth-cap inconsistency (4 / <3 / 2 —
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
  **Auto-resume deliberately post-1.0** (box crash-loop history; manual
  resume proves the path first). Original ask: Research + design pass on the errors an end user will
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

---

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

Full history in [BACKLOG_DONE.md](BACKLOG_DONE.md).
