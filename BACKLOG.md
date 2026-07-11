# Backlog — Deferred Items, Ideas, and Known Issues

Single canonical location for everything we've identified but haven't done yet.
Read this at the start of every session. Update it as items are completed or new ones emerge.

**Completed items live in [BACKLOG_DONE.md](BACKLOG_DONE.md)** — move items there with their full context when they ship; that file is the archive of what we've already decided, tried, or superseded, and it's ingested by `dev-recall` for historical context.

Last reviewed: 2026-07-09 (decision-cleanup session with Jeremy: #19 thread-arch decisions all resolved + recursion decree recorded, intent-resolution A/B dropped, orch.py trio deprecated, host-check wired+scheduled — four entries → BACKLOG_DONE; fastembed lane confirmed stays-gated). Previous full triage: 2026-07-04.

---

## Actionable Stack

Ordered open work that matters. Top of the list is next.

### -1. Purgatorio r2 graduates (2026-07-10 re-run; docs/audit-2026-07-r2/RECONCILIATION.md)

All adversarially verified (41/42 confirmed). The two 1.0-blockers first:

- [ ] **arch-r2-01 (blocker):** containerized-executor design pass has no
  vehicle — batch #3 decision recorded in GOAL_BRAIN + SECURITY_MODEL.md §2
  but absent from MILESTONES/BACKLOG/threads. This entry IS the minimum fix;
  scope the actual design pass into MILESTONES.
- [ ] **docs-r2-01 (blocker, cheap):** README "Optional Services" tells
  strangers to `sudo cp` unit files from `~/.maro/workspace/deploy/systemd/`
  — a dir nothing creates (config.deploy_dir() has zero callers); 2 of 3
  units exist nowhere. Rewrite to the printed hook-instructions posture.
  Fold in the supervision-convergence chunk: delete/rewrite deploy/systemd/
  + heartbeat-ctl.sh (ops-r2-04, docs-r2-04), reset left-on
  `heartbeat.autonomy: true` on this box (ops-r2-02), align host-check
  900s threshold with the one-shot-ticks decree (ops-r2-01 — else daily-red
  is structural).
- [x] **cs-r2-01: SHIPPED 2026-07-10** (promotion-time guard + loader-side
  backstop) — moved to BACKLOG_DONE with context.
- [x] **ops-r2-05 SHIPPED 2026-07-10:** proc_lock._run_dir fallback now
  mirrors config.workspace_root()'s env resolution (MARO_WORKSPACE /
  OPENCLAW_WORKSPACE / WORKSPACE_ROOT) before Path.home() — a partial
  config stub can no longer stamp the real workspace's heartbeat.pid.
  Tripwired in tests/test_proc_lock.py (reproduces the exact stub shape);
  live-verified: full test_heartbeat.py run leaves the real pidfile mtime
  untouched.
- [ ] **data-r2-01 (SF-2 residual, r2 blocker #2):** agenda-lane lesson
  extraction + skill crystallization run at finalize BEFORE closure judges;
  retro-stamp reaches only outcomes.jsonl — lessons/skills still extracted
  verdict-blind. Move post-closure or re-stamp.
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
- [ ] **batch-03:** `_DANGEROUS_PATTERNS` false-positives on instruction
  .md: funnel_report skill skipped for containing `open(` in prose about
  reading a ledger. Skip-for-review is the right failure mode, but the
  code-substring gate needs an instruction-artifact-aware pass (pair with
  cs-r2-01's guard, which is the right tool there).
- [ ] **batch-04:** lesson-funnel intake observation: 9 finalizations →
  0 lessons extracted on every tier. The funnel isn't just not-promoting
  (SF-10), it's barely ingesting. Fold into the funnel-rate measurement
  item; data-r2-01 (pre-verdict extraction) got no specimens either way.
- [ ] **hist-r2-02:** hist-05 owner ask ("run this prompt with this
  persona" as a first-class pattern) dropped for the third time — in
  neither the decision brief nor any backlog. This entry ends that.
- [ ] **docs-r2-02:** user/CONFIG.md lane docs over-claim 4 dead keys
  (research_step_model, max_steps, always_skeptic, notify_on_complete) —
  wire or de-document.
- [ ] Cosmetic sweep rides the above chunks: docs-r2-03 (CONFIG.md template
  self-contradiction), docs-r2-05 ("alert Jeremy" in stranger README),
  docs-r2-06/hist-r2-04 (PUBLISH_CHECKLIST missing from INDEX.md),
  land-r2-01 (state trusted-operator boundary in README safety section),
  land-r2-02 (pymaro disambiguation line), hist-r2-03 (SF-13 standing rule
  → put in a living doc, likely CLAUDE.md close-out), hist-r2-05 (bucket D4
  lessons.jsonl name collision), data-r2-03 (atomic_write 0600 perms),
  arch-r2-02 (GOAL_BRAIN 1.0-arc Remaining list stale), ops-r2-03-replaced
  pidfile litter.

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
- [ ] **Mining passes on the parked data.** The curation registry is the hook;
  the actual miners are TODO — skill scraper, script scraper, decision-prior
  indexer (feed a similar/rephrased re-attempt — this half is an OWNER ASK:
  Jeremy 2026-07-04, "task failures being retried, with the old task context
  available... I'm a little surprised the failure is so brittle"; Purgatorio
  hist-09 — prioritize accordingly), partial-run rescue. Append to
  `run_curation.CURATORS` (now 4: classify, inventory, excerpt,
  spend_transparency — the last two added since v0).
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

### 20. Subsystem archaeology — memory-vs-implementation divergence (2026-07-09, Jeremy)

Jeremy's recollection diverged from the Purgatorio audit on four subsystems.
Owner ask: "I'm not sure if I'm misunderstanding implementation or if we've
genuinely lost some things here." **Commit-dig COMPLETE 2026-07-09** — verdict:
nothing accidentally deleted; two subsystems alive and measurable, two starved
by the never-scheduled heartbeat.

- [x] **Qwen local-validator ladder — EXISTS-AND-LIVE, memory accurate.**
  Shipped `ae23f6b` 2026-06-21; expanded to the quality gate `d0328f5`
  2026-07-03; survived the loop_phases split intact (loop_post_step.py:25→645).
  Never broken, never disabled. Production 07-04→07-09: 71 VALIDATION_LADDER
  rows — 58 local-decisive (82%), 9 escalated, 4 paid-only; shadow-eval n=29,
  96.6% local-vs-paid agreement, 0 false_pass. Scope note: default for
  *validation surfaces* only (never planner/director reasoning). Residual →
  item #10 (tune local_max_tokens).
- [x] **Sheriff — misremembered: never in the goal pipeline, nothing pruned.**
  Born `12a7a90` 2026-03-23 into heartbeat+CLI; `git log -S sheriff` over full
  history shows zero agent_loop/handle/loop_* consumers ever; scoping-refactor
  and cron-diagnosis eras clean. Only deletion: `b04962b` 2026-07-02, two
  unused test-only state-markers, documented. It *feels* phased out because its
  vehicles (heartbeat, `maro sheriff`) have ~zero production hours — starved,
  not pruned. Standing day-one bug: bootstrap.py:188 generates a unit exec'ing
  `sheriff.py --heartbeat`, a flag that has NEVER existed in any commit —
  fix rides SF-1 (should exec `maro heartbeat`).
- [x] **Evolver-in-pipeline — EXISTS-AND-LIVE, memory accurate; the session is
  `ca7b327` 2026-07-03** ("Per-run evolver statistical scans instead of a
  systemd heartbeat daemon", quoting Jeremy's "app rather than an OS").
  Per-run half fires in production: memory/suggestions.jsonl has 197 rows,
  resumed exactly at Jul 3 (23/32/12/13 rows Jul 3/4/8/9). ops-02's
  "never run" is precise only for `run_evolver()`'s LLM meta-cycle +
  nightly-eval (heartbeat-only, never scheduled). Residuals: suggestions all
  `applied: False` (apply side dormant), arch-04 (finalize passes no adapter →
  refight_rule unreachable), synthesize_skill fires but has yielded zero
  skills on-box. SF-1/README language must separate the two halves.
- [x] **OpenClaw-heartbeat hook — never coded (hist-07 confirmed), design
  intent stands.** Jeremy (consistent): fire Maro's tick from the HOST's
  heartbeat (OpenClaw here, via `system event`); "app, not systemic" — Maro
  ships a tick entrypoint, never its own daemon. Entrypoint already exists:
  `maro heartbeat` = exactly one beat (cli.py:556; `--loop` is daemon mode;
  `--dry-run`, `--no-escalate` available). Supervision story (SF-1) redesigned
  around this; official scheduler/timer (decision batch, post-1.0) is the
  generalization for hosts without OpenClaw.

### 21. Heartbeat burn-in findings (2026-07-09, first real ticks)

First-ever production heartbeat ticks (one dry, one real) after the
supervision-shim ship. The tick machinery works — health check, tier-2
diagnosis fired correctly (the monotonic-sentinel fix is why it fired on the
first tick at all). Two findings before any recurring hook goes live —
**both FIXED 2026-07-10**; the recurring-hook blocker is cleared, but the
hook itself stays uninstalled per decree (one-shot ticks only, no
persistent timer — installation is Jeremy's call at the direct-use
transition):

- [x] **Recurring-hook blocker: diagnosis spends on zombie projects — FIXED
  2026-07-10.** Sheriff now classifies projects with no file activity for
  `sheriff.dormant_days` (default 14, docs/DEFAULTS.md) as `dormant`
  instead of stuck/warning — excluded from tier-2 diagnosis AND tier-3
  escalation (a recurring hook would have bought Telegram spam too, not
  just diagnosis calls). Cheap stat scan (`project_activity_age_days`),
  short-circuits before the expensive checks. Archiving sweep shipped as
  `maro sheriff archive [--days N] [--apply]` — manual-only, dry-run by
  default (off switches stay off); `check_all_projects` +
  `list_projects` skip `_archive/`. Live-proven on box: 238 projects →
  183 dormant / 0 diagnosis targets (was diagnosing `test`, `test-goal`,
  `vis-test`); sweep applied at 30d moved 113 stale goal-slug workspaces
  (all Apr–May regression junk, `polymarket-edges` untouched) →
  125 live. Real tick post-fix: healthy, 0 stuck, 0 recovery actions,
  198ms, zero LLM/Telegram spend. Tests: test_sheriff.py dormancy+archive
  block.
- [x] **Sheriff health check isn't lane-aware — FIXED 2026-07-10.**
  `pkg_anthropic` + `api_key` checks replaced with one `llm_backend` check
  over `llm.detect_backends()` (the doctor's single source of truth —
  sheriff can no longer disagree with what a run would do). Warns only
  when NO lane is usable; heartbeat tier-1 escalates on it (was:
  "suggested" for a missing API key). This box: degraded → healthy
  (`ok: subprocess, openrouter, openai`).

### 10. Local-validator measurement — tune `local_max_tokens` per model

- [ ] **Tune `local_max_tokens` per model.** Live finding (2026-06-21 verify run):
  VibeThinker's `<think>` trace on *real* (long) step results overran the 1024
  floor → empty content → conf 0.00 → spurious escalation on 2/5 steps (the other
  3/5 validated free at conf 1.00). Bumped default to 2048; deep-eval should find
  the floor that maximizes decisive-local rate without wasting generation latency.


### 14. llm.py adapter protocol extraction (promoted from Modular refactoring, 2026-07-04)

- [ ] **Streaming-iterator `complete()` on the shared adapter base.** The four
  adapters (Anthropic / OpenAI / OpenRouter / Subprocess) DO share an
  `LLMAdapter` base class (`llm.py:300`) — the remaining work is the streaming
  shape: `complete(messages) → iterator_of_events` so liveness/kill logic
  lives in one wrapper instead of per-adapter. The old dependency is CLEARED:
  stream-json parsing shipped (`_parse_stream_json`, `llm.py`; subprocess
  transcripts ride `resp.tool_events`). Port subprocess adapter first, others
  incrementally. Size: ~half day per adapter.

### 17. Run-visibility residuals (2026-07-09 real-data review)

All four sub-items shipped 2026-07-09 (two concurrent sessions — see
BACKLOG_DONE for both): contextvar loop_id threading + purpose stamping
(this session), live-report post-curation refresh + NOW-lane mini-reports
(concurrent session, superseding this session's own narrower post-curation
refresh attempt — see BACKLOG_DONE for the reconciliation note). One residual
surfaced by the NOW-lane work:

- [ ] **Index rebuild is O(all runs) at every finalize** (~277ms at 668
  dirs, via the post-curation hook). Fine now; revisit around ~10k run dirs
  (incremental index, or rebuild only on viz/backfill).
- [ ] **Goal search in the run visualization** (Jeremy, 2026-07-10, rider
  on the retention decree): with run data now kept forever, old runs must
  be *findable* to be worth keeping — "easier to ignore the old data than
  wish it weren't deleted (assuming we surface it in a meaningful way)."
  Search runs by goal text (and probably project/status/date) in the viz
  surface. Pairs with the surface-all-details principle: users trust what
  they can poke around in — the path, not just the outcome.

---

### 18. Project-loop lane escapes the done≠achieved machinery (2026-07-09 hermes trial, live specimen)

Found driving Maro through Hermes-in-docker. Third-party harness invoked the
project loop (`maro run` path, project `hermes-haiku`, loop 315ebffb) instead
of `maro-handle`. Result: `status=done 3/3`, artifact written — but content was
semantically off-target ("fresh hermes install" → a haiku about *Ruby gems /
bundle install*), and:

- **No goal_achieved verdict was produced** on this lane (no `_verdict_*`
  metadata; `maro inspect-run 315ebffb` → E_RUN_NOT_FOUND).
- **No `runs/<id>/` dir** — per-run attribution capture never engaged.
- **Artifact cleanup deleted the 3 per-step artifacts**, destroying the
  evidence needed to audit the miss after the fact.
- Verification that did run was structural-only (line count — the extracted
  lesson literally says "verify against stated structural constraints"), so
  the semantic miss sailed through. Static-probe bias on an unguarded path.

Two asks — **both SHIPPED 2026-07-10**:
- [x] **(a) verdict parity:** `cli._closure_verdict_pass` runs the same
  closure core (`verify_goal_completion` → `annotate_outcome_verdict` →
  demote done→incomplete on judged contradiction at conf ≥ 0.7, mirroring
  handle.py's status-honesty gate) on both `maro run` and `maro resume`.
  Honesty-only — no closure-restart machinery. When closure can't run (no
  adapter/LLM error) the verdict is absent, which run history already
  classifies as done-unverified — never verified done. Verdict surfaces in
  the command output (`goal_achieved` + summary). 8 tests
  (tests/test_cli.py TestClosureVerdictPass).
- [x] **(b) evidence-safe cleanup — superseded same day by the retention
  decree (Jeremy, 2026-07-10):** the first fix deferred deletion past a 24h
  grace window; Jeremy then ruled auto-deletion itself the bug — "I'd
  prefer to have the users choose to archive/delete old runs, rather than
  have the system decide it's clutter... the result isn't always *just*
  the outcome, it's also the path that gets you there." Final shape:
  per-step artifacts are **kept forever by default**; `keep_artifacts`
  retired; pruning is user opt-in via `artifacts.auto_prune_days` (0 =
  never), and even opted-in pruning never touches the just-finished
  loop's files (verdict is judged post-loop). DEFAULTS.md row carries the
  decree. Tests rewritten (kept-by-default, opt-in age gate, 0/negative =
  never).

Residual (kept open, smaller): this lane still creates **no `runs/<id>/`
dir** — `maro inspect-run <loop_id>` stays E_RUN_NOT_FOUND and per-run
attribution capture doesn't engage outside `maro-handle`. The verdict now
lands loop-keyed on outcomes.jsonl, so learning consumers see it; run-dir
capture for the direct-CLI lane is a separate (deliberate) lift.
Outcome row: outcomes.jsonl 20aae85f (workspace of the hermes trial container,
importable via `maro-import` from
`~/claude/hermes-maro-trial/data/home/.maro/workspace`).

### 19. Thread Architecture open decisions — RESOLVED 2026-07-09 → BACKLOG_DONE

All 5 dispositioned by Jeremy on the runtime box (brief claims re-verified
first). Decrees in GOAL_BRAIN Decisions 2026-07-09 (incl. the **recursion
rider**: goals must be able to recurse sub-goals — a standing design
constraint on all scoping/slicing work, doors named there). Annotations
inline in `docs/THREAD_ARCHITECTURE.md`. Live follow-ups: #5 planning-depth
shadow (MILESTONES), #9 /loop trace (MILESTONES), #6 verify→learn = next
arc after 1.0. Full context in BACKLOG_DONE.

## Vision / Deferred

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
- [ ] **Orphan scope A/B datasets: adjudicate or write off** (arch-03,
  resurfaced by the SF-4 flip). `~/.maro/experiments/scope-ab-2026-04-25-v0/`
  and `scope-ab-2026-04-26-v1/` hold full PAID treat/control run dirs with
  no ANALYSIS.md — two experiments bought and never read. Either adjudicate
  them against the 2026-04-22 result (which decided the inject flip) or
  write them off explicitly so the spend isn't silently forgotten.
- [ ] **Knowledge-web read side: wire it properly (post-1.0, KEEP).**
  Descoped from 1.0 docs (node store + BM25 is the honest claim) but
  explicitly kept: "I'd like to keep it on the list. I think it could be
  really powerful if done well (and right now sounds like it isn't)."
  The write side + 2124 edges exist; the read side
  (load_knowledge_edges) has zero callers. Adjacent-knowledge retrieval
  ("Correspondence" in the Mage sense) is the payoff if done well.

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

- [ ] **Smaller retention residuals** (from the same audit, decree-compatible
  but same smell): `maro-memory gc` retention windows (outcomes retain-days,
  narrative 180d) are system-chosen constants — could become user-config
  keys with DEFAULTS rows; `graduation.verify_graduation_rules()` is
  reachable only via the CLI `--verify` flag, so graduated intervention
  rules go live with zero automatic verification (belongs to the
  verify→learn arc).

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

### Captain's log viewer (low-priority; partially covered by command center)

- [ ] **Captain's log viewer (low-priority; partially covered by command center).** Render a slice as a sortable timeline (ts, event, loop_id, slug, key fields). Until cross-run queries become a pattern, this is a thin reader over JSONL — no storage migration warranted. (2026-07-04: `timeline()` + a `--timeline` CLI flag exist but are aggregate-only — event counts over time, not the per-event sortable slice this asks for. Still open, but smaller than it reads.)

### Storage decision — sqlite indexer (deferred)

- [ ] **Storage decision (deferred).** JSONL captain's log is fine for within-run analysis. Sqlite *indexer* on top (not replacement) is the right pattern when cross-run queries become routine — "median treat-vs-control delta across N runs," "all CLOSURE_VERDICT < 0.5 in last 30 days." Defer until we have a concrete query we keep wanting.

### Rolling reviewer-calibration metric

- [ ] **Rolling reviewer-calibration metric.** `scripts/probe-stats.sh`
  scans last N days of captain's log, reports
  `dismissed/validated/unprobed` rates for CLAIM_PROBED. Tells us if the
  adversarial reviewer is getting more or less trustworthy over time —
  the reason we built the grounding. (ITEM #3 — deferred; revisit after
  more probe data accumulates.)

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

- [ ] **Verifier synthesis phase.** Dream-level: orchestrator builds its own verifier when none exists, rather than degrading to LLM judgment or failing as "hard." Framing: BDD + TDD. Scope declares Given/When/Then (what must be true for "done"). Execution includes a mandatory red-green pair: synthesize an executable probe, break the code on purpose to confirm it catches the failure, fix the code, probe goes green. The probe is a first-class checked-in artifact.

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

### Model bake-off

- [ ] **Model bake-off.** Compare candidate local validators (VibeThinker-3B 8bit vs
  4bit vs 1.5B; a Qwen2.5-Coder tune; an Ollama option for the Linux box) on the same
  eval set. Confirm a 3B-class model is "good enough" on a generally modern machine
  (≥16 GB RAM; 4-bit for 8 GB) before standardizing on one. (Partially done:
  qwen2.5-coder:3b vs paid shadow-eval, n=42, in `docs/LOCAL_VALIDATOR.md` +
  per-class-routing item below — that settled the box's *current* pick; the
  formal multi-variant sweep is what remains.)

### Closure demotion doesn't reach the outcome store

- [ ] **Closure demotion doesn't reach the outcome store** — when handle's
  closure verdict demotes done→incomplete (02b0263), run metadata is honest
  (recall/guard read that) but the loop already called reflect_and_record
  with status=done from inside agent_loop's finalize — so outcomes.jsonl and
  any lessons extracted carry the un-demoted framing. Small mismatch, noted
  not fixed: moving reflection after closure would delay it for every run to
  serve the rare demotion; an outcome-amendment hook is probably the right
  shape if this starts to matter.

### "Count the files" closure scope

- [ ] **"Count the files" closure blessed two different answers** — same goal,
  loop 1 counted 45 (docs/ top-level), gate-escalated loop 2 counted 80
  (recursive); *both* closure verdicts called their count "correct and
  verified". Ground truth: both defensible readings of an ambiguous goal —
  but closure verification inherits the executor's interpretation instead of
  pinning one. Resolved-intent/scope is the existing seam that should pin
  countable deliverables ("N = recursive count") before execution.

### First in-process consolidation gc policy

- [ ] **First in-process consolidation gc'd the whole MEDIUM lesson store** —
  5 weeks of decay-age applied in one cycle (decayed 38, promoted 0, gc 38).
  Arguably correct on stale data (M2 promotes at reinforcement time, LONG
  survived: 22), but a gentler policy for long-gap catch-up (cap effective
  decay-days? amnesty pass?) is worth considering before the store matters.

### Benchmark/eval missions need a read-only or scratch-dir fence

- [ ] **A/B benchmark runs mutated the repo and each other (2026-07-08):**
  the m3-host-monitoring §7 mission ("design a monitoring checklist") was
  interpreted by workers as *write repo files* — later runs found earlier
  runs' artifacts ("already scaffolded"), contaminating 4 of 16 cells with
  cross-run carryover, and one worker committed+pushed to main (which is
  how the dead-hooks bug surfaced — silver lining, but not a pattern).
  Benchmark harnesses should either phrase missions read-only, point
  goal-declared paths at per-run scratch dirs, or set the write fence to
  reject repo writes for eval-tagged runs. Decide the seam when the next
  eval batch is designed; record in the A/B record's m3 caveat
  (`docs/history/2026-07-08-worker-slice-ab.md`).

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

### Sandbox hardening guards a stub, not real skill execution

- [ ] **Sandbox executes a stub, not real skill code.** `src/sandbox.py`'s
  536-line hardening stack (rlimits, venv isolation, network blocking, audit
  log) runs a script that puts skill steps into *comments* and prints a
  canned `"Executed skill: {name} on input: ..."` string — the hardening
  protects a simulation, not live execution. `is_skill_safe`'s static-safety
  verdict is recorded in the audit log but never gates anything. Decide:
  build real skill execution to match the existing hardening, or shrink the
  sandbox to match what it actually does. Revisit later — no immediate
  action needed, hardening layers are well-built and may be intentional
  groundwork for real execution. Source: refactor-plan architecture review
  (docs/REFACTOR_PLAN.md), 2026-07-02.

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
- [ ] **(g) Portable/shareable learning — design + migration path.**
  **DESIGN SHIPPED 2026-07-09 → `docs/PORTABLE_LEARNING_DESIGN.md`** (8
  provisional decisions collected in its §8, awaiting Jeremy; recommended
  1.0 slice = its §7 chunks 1–4: migration runbook + doctor checks,
  provenance fields + `scrub_identifiers`, `maro-pack export/seal`,
  `maro-pack import/adopt`). Item stays open until reviewed + sliced.
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
  resume-surface (CLI vs notify), depth-cap inconsistency (4 / <3 / 2).
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
