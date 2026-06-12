# Backlog — Deferred Items, Ideas, and Known Issues

Single canonical location for everything we've identified but haven't done yet.
Read this at the start of every session. Update it as items are completed or new ones emerge.

**Completed items live in [BACKLOG_DONE.md](BACKLOG_DONE.md)** — move items there with their full context when they ship; that file is the archive of what we've already decided, tried, or superseded, and it's ingested by `dev-recall` for historical context.

Last reviewed: 2026-06-10 (session 40 — memory lifecycle fixes + dry-run hermeticity; see entry below).

---

### Entropy / decay-by-invalidation (2026-06-11, queued behind navigator)

Steering context in GOAL_BRAIN.md Intent (entropy quote). Crystallized artifacts
(skills, standing rules, playbook entries) rot when the world changes under them —
distinct from decay-by-disuse, which tiered lessons already have. The most-reinforced
artifact is the most dangerous one at world-shift time, because reinforcement and
validity are different signals and we only track one.

- [x] **Decay v0 — re-fight on collision (Jeremy's pinned first pass).** When a
  crystallized artifact fails, inject the existing mechanism + the failure into the
  prompt and re-derive. *"at worst we have better context, at best it's a slight
  tweak and we fix forward."* **Done 2026-06-11 for the rule layer:** a contradicted
  standing rule is *contested* — immediately demoted from "apply unconditionally" to
  a verify-before-relying injection block (read-time trust derivation, data untouched),
  and `knowledge_lens.refight_rule()` re-derives it against its contradiction evidence
  (pulled from the captain's log) with verdicts keep / revise / retire (retire demotes
  back to hypothesis — must re-earn promotion). Runs from `run_skill_maintenance` in
  the evolver cycle (adapter-gated, max 3/cycle), beside `rewrite_skill` — the skill
  seed it generalizes. `RULE_REFOUGHT` event is the audit trail. No cron — collision
  detection rides on contradiction recording, repair rides on the evolver cycle.
  Note: no standing rules exist on this box yet (accretion only became possible in M2),
  so first live exercise awaits a real rule + collision.
- [x] **Freshness signal on crystallized artifacts.** `last_verified` (last
  successful run against the real world) distinct from `last_reinforced`. Trust at
  injection time = f(score, time-since-verified); stale-but-promoted gets a
  "verify before relying" flag, not silent confident injection.
  **Done 2026-06-11 for the rule layer:** `StandingRule.last_verified` stamped at
  promotion, on production re-confirmation, and on re-fight keep/revise. The
  anchoring fix: post-promotion re-confirmations never reached the rule —
  `observe_pattern` only matched hypotheses, so a re-confirmed promoted lesson
  seeded a *duplicate hypothesis* (which could re-promote into a duplicate rule)
  while `rule.confirmations` stayed frozen at its promotion value. Now an
  observation matching an existing rule verifies the rule (`RULE_VERIFIED`
  event, 46th type). At injection, an uncontradicted rule unverified for
  `knowledge.rule_staleness_days` (default 30, 0 disables) joins a "Stale rules
  (unverified for N+ days — verify before relying)" block; contested takes
  precedence. Read-time derivation only, data untouched; `promoted_at` is the
  fallback anchor for pre-field rules. Skill/playbook layers still open —
  skills have score+circuit-breaker already; revisit if staleness shows up there.
- [ ] **Design constraint, not a task: decay trust, never data.** Append-only
  evidence layer stays perfect (the computerization edge over human forgetting);
  only compiled-truth confidence decays. Crystallization Stages 4–5 must be
  demotable back to language form — world-change is the frequent trigger,
  model upgrades the rare one.

---

### Live orchestration run findings (2026-06-11, first post-suite-green session)

From real task-path runs (enqueue → drain_task_store → handle_task → handle).
Fixed same day: task-path runs never finalized, poisoning recall's all_failing
(9402d3d); lesson extraction silently returned [] on every real run — safe_list's
str default dropped the typed lesson dicts the prompt asks for (verify→learn was
dead at the extraction step since Phase 59 S1). Remaining observations:

- [x] **GOVERNANCE: a vague goal pipeline-executed into an unreviewed mainline
  push as Jeremy.** "improve things" (deliberately vague test goal) decomposed
  itself into "pick an improvement from MILESTONES/BACKLOG and implement it
  end-to-end", wrote a real fix (CLOSURE_VERDICT skip-path emission, 06c3764,
  reviewed post-hoc: good code, kept), committed as author "Jeremy Stone" and
  **pushed to origin/main** — 4.09M tokens, no human or quality gate between a
  worker and a public push under Jeremy's identity. The live navigator shadow
  said **escalate (0.95)** at dispatch — the pipeline executed anyway and
  declared done. **RESOLVED 2026-06-11 (cfab080):** Jeremy's call — workers
  authoring as him is fine ("haven't made that distinction yet, not sure it
  matters"); the gate is about unreviewed mainline pushes, not identity.
  Shipped as branch policy: `_run_subprocess_safe` marks all Poe-spawned
  subprocesses `POE_WORKER_RUN=1`; `scripts/hooks/pre-push` (installed via
  `scripts/install-git-hooks.sh`, part of harness install) blocks worker
  pushes to main/master with a redirect to work branches; explicit bypass
  via config `workers.allow_main_push` (default false) → `POE_ALLOW_MAIN_PUSH=1`.
  Humans/interactive sessions unaffected. Still a strong cutover data point
  for the dispatch decision class.
- [ ] **Workspace boundary: build-goal artifacts landed in the repo root** —
  run_health.py + example output were written to cwd (the repo) instead of the
  run's artifact dir; goal even said "as an artifact file". Moved them into
  `e1b9f95e-humble-lantern/artifact/` post-hoc. Existing bounded-workspace
  BACKLOG item covers the general fix; this is a concrete repro.
- [ ] **NOW-lane runs produce no learning data and no artifact discipline** —
  the run_health build goal (e1b9f95e-humble-lantern) was classified NOW, which
  (a) skips `reflect_and_record` entirely — reflection only fires in the agenda
  loop's finalize (agent_loop.py:3515), nothing on the NOW path calls it, so the
  run finalized `done` with no outcome/lesson record — and (b) writes relative
  to cwd (the workspace-boundary repro below is the same run). **(a) fixed
  2026-06-11:** NOW path records a slim outcome (record_outcome, task_type
  "now", no LLM lesson extraction — quick-answer lane must not pay a
  reflection call per request). Still open: `_is_complex_directive`
  thresholds — a multi-step "write a script AND run it AND save outputs" goal
  is not a NOW request (heuristic-tested: it does NOT catch that goal today).
- [x] **NOW lane recorded an honest "this cannot be done" as `done`** — found
  by the impossible-binary probe batch (3× "run /usr/bin/nonexistent-binary-xyz"):
  intent routed the execution goal NOW, the completion honestly replied "the
  goal is incomplete... cannot be fulfilled", and the run was recorded `done`
  in 18s — NOW status meant "the completion call returned", not "the goal was
  achieved". The false done then poisoned every judgment layer above it:
  recall reported a done prior, so the dispatch guard could never trip, and
  the navigator's attempt-2 `close` was reasonable-on-poisoned-input. (The
  navigator still said `escalate 0.95` on attempts 1 and 3 — attempt 3
  explicitly caught the contradiction "prior attempts are marked done" vs an
  impossible goal. Divergences #2 and #3, both adjudicated navigator-right.)
  **Fixed same day:** (1) `now_lane.escalate_to_director` default flipped to
  True — complex directives route to the agenda lane; (2) autonomous NOW runs
  (origin present, no human reading the text) get a cheap self-verdict and
  demote to `incomplete` when the response reports non-fulfillment
  (`_verify_now_outcome`, fails open). Interactive NOW calls keep raw speed.
  **Agenda twin fixed same night (02b0263):** the same goal re-run through
  the loop still finalized done — closure said complete=False at 0.95–0.99
  but restarted loops were never re-verified and the verdict never gated
  status. Now: re-verify after closure restart; final complete=False at
  conf ≥0.7 demotes done→incomplete. **Both fixes live-verified:** the
  impossible-file probe finalized `incomplete` end-to-end, and the 4th
  attempt at the binary goal drew the first live RECALL_GUARD_TRIPPED
  (6 honest non-done priors) with the navigator concurring (close 0.99 /
  guard_refused).
- [ ] **LLM classifier routes trivial questions AGENDA — quick-lane economy
  inverted** — live probe 2026-06-12: "What is 17 multiplied by 23? Reply with
  just the number." paid the full loop + closure + quality gate (~3.5 min,
  run ecd4a7bd-eager-kestrel) because the cheap-LLM classifier said agenda;
  the heuristic classifier says now (0.65, "short or simple request"). One
  observation, not a pattern yet — but if the LLM classifier systematically
  out-conservatives the heuristic, the NOW lane is dead on the task path and
  every quick question costs loop overhead. Check NAVIGATOR_DECIDED /
  metadata lane distribution once organic volume accumulates. Related:
  `_is_complex_directive` over-matches imperative-shaped *questions* ("What
  number am I thinking of? Answer with one number only." escalates) — same
  economy cost from the other direction. Escalation overriding an explicit
  `force_lane="now"` fixed 2026-06-12 (force wins; escalation protects
  classified routing only).
- [ ] **Closure demotion doesn't reach the outcome store** — when handle's
  closure verdict demotes done→incomplete (02b0263), run metadata is honest
  (recall/guard read that) but the loop already called reflect_and_record
  with status=done from inside agent_loop's finalize — so outcomes.jsonl and
  any lessons extracted carry the un-demoted framing. Small mismatch, noted
  not fixed: moving reflection after closure would delay it for every run to
  serve the rare demotion; an outcome-amendment hook is probably the right
  shape if this starts to matter.
- [x] **NOW artifacts write to a stale prototype path** — `_write_now_artifact`
  resolved orch_root and appended `prototypes/poe-orchestration/artifacts/now/`,
  landing files at `~/prototypes/poe-orchestration/prototypes/poe-orchestration/…`
  (doubled segment, outside the workspace). **Fixed 2026-06-12:** NOW artifacts
  now land in the run dir's `artifact/` subtree (current_run_dir, falling back
  to run_dir(handle_id) — both workspace-honoring); artifact_path is absolute.
- [ ] **First in-process consolidation gc'd the whole MEDIUM lesson store** —
  5 weeks of decay-age applied in one cycle (decayed 38, promoted 0, gc 38).
  Arguably correct on stale data (M2 promotes at reinforcement time, LONG
  survived: 22), but a gentler policy for long-gap catch-up (cap effective
  decay-days? amnesty pass?) is worth considering before the store matters.

- [x] **`loop-*-PARTIAL.md` is misnamed on done runs** — fixed same day: the
  transcript is `loop-<id>-RESULT.md` when the loop finished done, `-PARTIAL.md`
  otherwise. Verified no production code reads the filename (only synthetic-name
  tests + cleanup glob, which matches neither).
- [ ] **Closure restart doubled a trivial run** — the standing-rule report goal
  (049599c8-sturdy-ridge) finished done 4/4 in loop 1 (~300k tokens), then the
  closure-restart heuristic (handle.py:1091–1180) ran a full second loop (6/6,
  ~370k more) to chase "gaps" on a goal whose artifact already existed. The
  navigator's close judgment is the structural replacement (DUMB_LOOP_AUDIT.md
  priority list); until then consider a cheap "artifact exists + verifier passed"
  short-circuit before restarting. One repro so far — the 2026-06-11 verification
  run (c677fda8) also doubled, but that was the *quality gate* tier escalation
  (ESCALATE 0.90) and it correctly caught loop 1 writing its summary to the
  wrong location — gate working as intended, don't conflate the two.
- [ ] **"Count the files" closure blessed two different answers** — same goal,
  loop 1 counted 45 (docs/ top-level), gate-escalated loop 2 counted 80
  (recursive); *both* closure verdicts called their count "correct and
  verified". Ground truth: both defensible readings of an ambiguous goal —
  but closure verification inherits the executor's interpretation instead of
  pinning one. Resolved-intent/scope is the existing seam that should pin
  countable deliverables ("N = recursive count") before execution.
- [x] **Step numbering in transcripts starts mid-sequence** — root-caused same
  day: `s.index` is the NEXT.md *ledger line* (orch_items.append_next_items
  returns file-line offsets, headers included), not plan position. Display-only
  fix: transcripts and the execution log now render `Step <pos>/<n> (ledger #i)`;
  the ledger index stays untouched (load-bearing for get_item/_by_idx).

### Goal-brain pressure-test findings — runtime gaps (2026-06-10)

From sequencing step 2 (GOAL_BRAIN.md Compiled truth has the full findings; sample = the 2026-05-13..17 run-dir window). The decompose-fallback chop is fixed; these are the remaining mechanical gaps:

- [x] **Run↔thread linkage** — fixed 2026-06-10: tasks carry an `origin` dict (parent_handle_id via `runs.current_handle_id()`, parent_loop_id, parent_goal) set at the agent_loop continuation/escalation enqueues and propagated by director escalation follow-ups; `handle_task` threads it (plus source/job_id/parent_job_id) into `handle(origin=...)`, which stamps it into run-dir `metadata.json` and `handle_inputs.jsonl`. Every requeued run is now traceable to the work that spawned it. Note: ancestry is *recorded*, not yet *consulted* — the dispatch-time read is the recall() work.
- [x] **Dispatch-time dedup/memory** — the same agenda goal ran ~25× in ~35 min on 2026-05-17 (mixed stuck/done) with nothing consulting prior outcomes. **Fixed 2026-06-10 (goal-brain step 3):** `src/recall.py` dispatch slice — `handle()` injects prior-attempt history + thread ancestry into context on every run; `handle_task()` guards the autonomous requeue path (≥3 attempts in 60min all non-done → task errors with a readable reason instead of running; `RECALL_GUARD_TRIPPED` event). Design: `docs/RECALL_DESIGN.md`. Follow-up done 2026-06-11: `_build_loop_context`'s memory half (8 substrates, not 4 sites) relocated behind recall(slice="loop"); evolver's captain's-log bridge absorbed; lesson-cited stamp live in RECALL_PERFORMED.
- [x] **Scope generation fails silently** — `generate_scope` returns None on any adapter failure, so during the rc=1 outage no run got a scope.md and nothing recorded that scope was skipped. **Fixed 2026-06-10:** new `SCOPE_SKIPPED` captain's-log event emitted from handle.py when scope generation is enabled but yields nothing — both the returned-None path (`reason: generator_returned_none`) and the raised-exception path (`reason: exception`, with error preview). Outages now show up in the captain's log alongside `SCOPE_PARSE_FAILED`.

### Dry-run hermeticity — fixed two leak sites, two more fail-safe-by-accident (2026-06-10)

Session 40 found `dry_run=True` runs making **real authenticated `claude -p` CLI calls** (subprocess adapter needs no API key, so conftest key-isolation didn't stop it). test_handle.py alone took 2h06m of real token burn. Fixed:

- [x] `_decompose_goal` planner-lift: `build_adapter()` was called unconditionally, replacing `_DryRunAdapter` with a live adapter. Now guarded on `ctx.dry_run`.
- [x] `_select_step_adapter` (Phase F5): `_DryRunAdapter` has no `model_key` attr → `getattr(..., "")` slipped past the explicit-model check → live adapter per step. Now early-returns on `ctx.dry_run`.
- [x] conftest guard: `tests/conftest.py` now blocks `claude`/`codex` binaries at the `llm._run_subprocess_safe` seam (other commands pass through so its unit tests still run). Tests needing LLM behavior must mock the adapter.
- [x] Adapter-swap seam made principled: the decompose planner-lift and Phase F5 per-step selection now only re-tier adapters that are `isinstance(_, LLMAdapter)` (i.e. build_adapter products they know how to rebuild). Injected test doubles and `_DryRunAdapter` are plain classes and pass through untouched — this is the injection contract.
- [x] Step-shape auto-split was non-convergent: analysis-first steps with an incidental exec keyword (e.g. "Analyze findings from build X") split into a replacement that re-tripped the detector every iteration until max_iterations → stuck. `_split_exec_analyze` now strips analysis clauses from the run part, and the executor-side leak guard executes as-is when a split wouldn't converge. (Also fixed `lstrip('Rr un')` char-set bug.)
- [ ] **Fragile fail-safes left in place** (deliberately, don't-refactor-mid-feature): `_run_steps_parallel`/`_run_steps_dag` only avoid building live adapters in dry-run because `adapter.model_key` raises AttributeError on `_DryRunAdapter` and the except-path falls back. Same for `_generate_timeout_split` (unreachable in dry-run today). Make these explicit when next touching agent_loop step execution.
- [x] **Hardcoded `_CODEX_BIN = "/home/linuxbrew/.linuxbrew/bin/codex"`** in llm.py — fixed in M5 (2026-06-10): `_find_codex_bin()` resolves CODEX_BIN env → PATH → common locations → bare name, mirroring `_find_claude_bin()`.
- [x] **5 pre-existing worker_session_bridge failures in test_orch_core.py** — root-caused + fixed 2026-06-11. Regression from `a799871` ("support worker manifest args arrays"): the refactor funneled *string* manifest commands through the list-argv quote-join, so the whole shell line became one `shlex.quote`d token and `/bin/sh -c` looked for a program literally named `printf "%s" ... > ...` (exit 127, surfaced as validation 'blocked'). Every string command containing shell syntax (`$VAR`, `>`, heredocs) broke; bare names like `./run.sh` survived because quote was a no-op, and the timeout test passed for the wrong reason (127 also raises → blocked). Fix in `_load_worker_session_manifest`: string commands pass verbatim (matching the top-level-string manifest form), args (if any) appended quoted; list commands keep quote-join. All existing string+args pins (`"python3" + ["-m","worker"]` → `python3 -m worker`) unchanged.
- [x] **Pre-existing: test_scheduler.py `test_inflight_job_not_returned_until_lease_stale`** — root-caused + fixed 2026-06-11. Time-of-day-dependent test: `mark_job_dispatched` stamped the lease at real wall clock while the test probed staleness at synthetic `next_run + 5min`; with the 6h lease the first probe only read fresh between 03:05–09:00 UTC. Fix: `now` seam param on `mark_job_dispatched(job_id, *, now=None)` (mirrors `check_due_jobs`); test stamps the lease at the synthetic probe time.
- [x] **Pre-existing: 4 plan-manifest tests in test_agent_loop.py are order-dependent** — root-caused + fixed 2026-06-11. Not an orch-root cache: `runs._current_run_dir` (module global, pinned by `handle()` via `set_current_run_dir`) leaked across tests, so `runs.artifact_dir()` routed later tests' plan manifests into the stale run's `build/` instead of `projects/<p>/artifacts/`. Production contract is deliberate (CLI clears; programmatic callers clear themselves — handle.py comment) and tests are exactly such callers: autouse conftest fixture now resets the global after every test. Whole pollution class closed, not just these 4.

---

### Memory lifecycle was write-dead / decay-corrupting — core fixed, wiring shipped (2026-06-10)

Session 40 audit confirmed consolidation **never ran** (only entry point was the `poe-memory decay` CLI, never invoked) and the lifecycle had three latent data-corruption bugs, all fixed in knowledge_web.py:

- [x] Tier-blind decay on load: LONG-tier lessons decayed on read despite "no decay by design" (22 long lessons were reading at ~0.85^46 effective score).
- [x] `run_decay_cycle` persisted decayed scores without moving the `last_reinforced` anchor → compounding rot on every RMW write (reinforce/forget/promote all re-persisted decayed bystander scores). Decay is now strictly a read-time derivation; rewrites use `raw=True`.
- [x] RMW paths loaded with default `limit=50` → stores >50 lessons would be silently truncated on rewrite. All rewrite paths now load `raw=True, limit=None`.
- [x] In-process consolidation ("dream cycle"): `maybe_consolidate()` marker-gated to once per `memory.consolidation_interval_hours` (default 24h), wired into `handle()` (post-request, never affects outcome), heartbeat tick, and `poe-memory consolidate [--force]`. In-process by design — **no cron/daemon** (Jeremy: rogue-process history).
- [x] Promotion timing race (M2, shipped 2026-06-10): promotion now evaluated at reinforcement time via `_post_reinforce_hooks` — score is freshly re-anchored, so eligibility is real. Consolidation-cycle promotion stays as a backstop.
- [x] Standing rules accrete (M2, shipped 2026-06-10): LONG re-confirmation calls `observe_pattern`; `record_tiered_lesson` dedups cross-tier so re-learning a promoted lesson reinforces the LONG record instead of duplicating into MEDIUM. Full path medium → long → standing rule now reachable in production.

---

### Build-loop wiring — cron wakeups are hitting the wrong abstraction (2026-05-06)

The repeated `poe-orchestration-build-loop` duty-cycle alerts finally coughed up a concrete diagnosis: the 5-minute cron is not running a dedicated autonomous build loop. It is waking the main session with the generic reminder text:

> Read HEARTBEAT.md if it exists (workspace context). Follow it strictly. Do not infer or repeat old tasks from prior chats. If nothing needs attention, reply HEARTBEAT_OK.

That explains the observed pattern:
- `last_status=ok`
- duty cycle usually ~5–7%, occasionally a bit higher
- background checkpoints fine
- repo often clean

In other words: the system is succeeding at the wrong thing. A reminder wake can only do opportunistic work; it is not a real build-runner substrate.

- [x] **Route build-loop cron to a dedicated autonomous runner/supervisor.** Completed 2026-05-06.
  - The dedicated runner lives in `src/build_loop_runner.py` with a lockfile/status contract plus a default `workers/handle.sh` bridge.
  - `python3 src/cli.py build-loop` is the first-class entrypoint and `scripts/build-loop.sh` is the stable cron-facing wrapper.
  - The live OpenClaw cron job `poe-orchestration-build-loop` now targets the dedicated persistent session with a payload instructing it to run the build-loop wrapper instead of the old generic HEARTBEAT reminder text.
- [ ] **Define the success condition operationally.** "Fixed" means more than cleaner logs: while active work exists, measured duty should stay above the 60% floor (target 85%+) without relying on human-visible heartbeat chatter.
- [ ] **Preserve health-only heartbeat semantics.** The 2026-04-22 split was correct; do not regress into making every generic heartbeat wake an autonomy daemon again. The fix needs to be explicit build-loop wiring, not re-coupling everything.

---

### ACTIVE DESIGN SPACE — Thread Architecture (2026-04-26 → 2026-04-27, Jeremy + Claude)

**Branch:** `arch/thread-navigator`
**Doc:** `docs/THREAD_ARCHITECTURE.md` (the sketch + decisions + open list)
**Conversation log:** `docs/conversations/2026-04-26-thread-architecture.md` (literal transcript)

The 1-shot-first DISCUSS item (formerly here) expanded into a full architectural sketch over a 7-turn planning conversation. Rather than just inverting the planning default, the conversation reframed the unit of orchestration to **thread**, with a per-turn `navigator → work → navigator` loop, navigator-selected personas, sub-thread fork/collate, build-folder-as-thread-residence, and crystallization (Stages 1–5) as the navigator's improvement path.

Don't implement yet — the architecture doc has 9 open decisions to work through first, starting with the navigator's prompt + decision schema (Open Decision #1). Backlog-style detail items will be added under this entry as the design firms up.

**1-shot-first** is preserved as one move-shape the navigator picks per turn (not the default; navigator decides whether to plan or execute). Existing planning scaffolding (`decomposition_too_broad`, mid-loop redecompose, scope-as-armor) probably shrinks but does not delete — Jeremy pushed back on aggressive deletion (Tesla-vs-driver: confident-sounding LLM ideas without critical-thinking-edges drift, because people's context ≠ LLM context).

**Adjacent items that should be re-evaluated under this frame** (most are below in this backlog):
- Intent resolution (next entry) — folds into "fork+collate" sub-thread mechanism
- Captain's log infrastructure-vs-visibility (new) — should be demoted to data, not infrastructure
- Persona auto-selection (existing drift in `architecture overview`) — becomes load-bearing, not optional
- Recall() interface (new) — single seam over memory substrates the navigator queries
- Crystallization Stage 5 (existing gap in `KNOWLEDGE_CRYSTALLIZATION.md`) — the navigator's cheaper-over-time mechanism
- Shared-learning portability (new) — self-learned artifacts should survive HDD loss / orchestrator switch

---

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

- [ ] **Minimum experiment (before building orchestration):** take one
  blind-test goal. Manually produce a resolved-intent artifact
  (unknowns / probes / deliverable-map). Run side-quests by hand using
  the existing `handle.py` path, capture outputs. Run the main goal with
  resolved-intent + side-quest artifacts injected as ancestry context.
  Measure: does output quality + closure verdict + adversarial review
  improve measurably vs the same goal without? If yes, build
  orchestration. If no, the ceiling isn't here.
- [ ] **Small-scope deliverable-map LLM prompt:** dedicated prompt that
  asks "what artifacts does this goal *literally* imply?" separate from
  scope generation. Cheap to try and might catch the slycrel-go "no
  client exists" class of miss without any other structural changes.
- [ ] **Resolved-intent artifact schema.** After the experiment, if we
  want to build the orchestration, spec the artifact (fields,
  persistence, merge rules on pivot).
- [ ] **Pivot reuse / workspace persistence as first-class.** The
  `polymarket-edges` pattern proves the value of persistent workspaces
  (project_polymarket_edges.md memory). Generalize: every goal's
  side-quest outputs live in `~/.poe/workspace/projects/<slug>/
  artifacts/` and survive across reruns of the same goal family.

### Modular refactoring (AFK-friendly chunks, queued 2026-04-18)

Jeremy's framing: LLMs don't feel rework cost the way humans do, so our
codebase has accumulated seams that are hidden (not broken, just hostile
to the next edit). These chunks are sized so one session can ship one of
them cleanly without needing real-time direction. Pick any of them when
looking for an AFK-friendly chore. Principles in `docs/CODING_NOTES.md`.

- [ ] **llm.py adapter protocol extraction.** Four adapters
  (Anthropic / OpenAI / OpenRouter / Subprocess) share patterns by
  convention, not by interface. Extract an `Adapter` Protocol with
  `complete(messages) → iterator_of_events` so streaming is first-class
  and liveness/kill logic lives in one wrapper instead of per-adapter.
  Port subprocess adapter first (we just touched it), others
  incrementally. Dependency: stream-json parsing lands first (see
  separate item) — the streaming shape is the point of the extraction.
  Size: ~half day per adapter once protocol is spec'd.
- [ ] **handle.py prefix registry.** `apply_prefixes()` is a chain of
  if/elif on magic strings (`ralph:`, `verify:`, `pipeline:`, `strict:`,
  `effort:`, `ultraplan:`, `btw:`, `direct:`, `mode:thin`). Collect into
  a `PREFIX_HANDLERS: dict[str, Callable]` registry so "what modifiers
  exist?" is a one-line grep. Preserve stacking semantics. Tests
  already cover every modifier so regression risk is low. Size:
  half-day. Good starter AFK chunk.
- [ ] **agent_loop.py phase extraction (continuation).** `LoopPhase` +
  `LoopContext` shipped (memory: project_monolith_extraction.md).
  Next 2 phases to extract: `scope_generation_phase` and
  `step_execution_phase`. Module is ~74KB; want to get under 40KB per
  file. Size: one phase per session, ~half-day each.
- [ ] **Captain's log event contract doc.** We have 36+ event types
  emitted across 10+ modules. No single doc says "here's every event,
  here's the field schema, here's when it fires." Blind collaborator
  can't reason about observability without reading every emitter.
  Produce `docs/CAPTAINS_LOG_EVENTS.md` with one table: event / fields /
  emitter / when-it-fires. Pure documentation chunk — zero code risk.
  Size: half-day. Excellent AFK starter.
- [ ] **Test clutter trim.** Jeremy's outside-in-testing posture
  applied to the suite: tests that poke private functions with mocked
  collaborators and assert call-shape are performative. Sweep tests
  touched during recent refactors and mark ones that would break on
  a rename-without-behavior-change — delete the clearest offenders,
  keep anything covering a module boundary or regression. Don't do
  a mass pass; trim opportunistically when editing neighboring code.
  (Tracked as a posture, not a standalone chunk.)

---

### Comprehensive run transparency (audit phase, queued 2026-04-26)

When a user pays real money for a run, they should be able to reconstruct exactly what was done — not because the test framework needs it, but because that's what spending non-trivial dollars demands. The 2026-04-25 scope A/B 1+1 surfaced this concretely: treat made commits, control's setup-reset wiped them, and we couldn't tell after the fact what code each arm produced. That's not a test-tooling problem; it's a systemic transparency gap.

**Mental model (Jeremy's framing):** treat a run like a project compile.
- **Source** — the inputs: prompt, scope, resolved-intent (deliverable map), plan(s).
- **Build** — interim objects: per-step outputs, tool calls + results, captain's log slice, agent reasoning, intermediate artifacts (scratchpad/PARTIAL files), recovery decisions.
- **Artifact** — the final result: code diff (or a branch with the commits), report, decisions log, NEXT.md state.

Every paid-spend run should produce all three, durably, in one inspectable bundle. Some pieces exist (NEXT.md, scratchpad, captain's log slice when a runner extracts it); the gap is comprehensive coverage + a default per-run capture, not opt-in test instrumentation.

**Design principle (Jeremy, 2026-04-26):** route writes to the run-dir *from the start*. Don't capture-at-end. The system already does the work — scratchpad, PARTIAL files, scope.md, resolved_intent.md, step outputs, NEXT.md — it just writes them to scattered locations. The fix is organization, not new instrumentation: pick the destination at run-start, point all the existing writers at it, and the bundle falls out the other end with everything already collected. **No copy/extract phase at end of handle. No "if a runner extracts it." The run-dir is the destination.**

A per-run nickname (memorable 2-word label, deterministic from handle_id) makes runs referenceable in conversation without copy-pasting UUIDs. Run-dir shape: `~/.poe/workspace/runs/<handle_id>-<nickname>/` containing source/ (prompt, scope, resolved_intent, plans), build/ (per-step outputs, scratchpad, PARTIAL files, captain's log slice, recovery decisions), artifact/ (final code diff or repo.bundle, NEXT.md state, decisions log).

- [x] **Per-run isolation: branch-name front-loaded into the prompt.** Shipped in `scope_ab_runner.py` 2026-04-26 as the test-side affordance — `scope-ab-r{NN}-{arm}-{TS}` branch pre-created and named in the prompt. Generalized variant for non-test invocations of handle.py is part of the next backlog wave (`--repo-branch-prefix` or auto-derived from goal slug + handle_id).
- [x] **Run-dir as the write destination (not a copy target).** Shipped 2026-04-26 (commits `13a6470`, `8a68e37`). `src/runs.py` creates `~/.poe/workspace/runs/<handle_id>-<nickname>/` at handle start; `set_current_run_dir` pins it as a process-level context var; `artifact_dir()` and `source_dir()` route writes there from agent_loop (PARTIAL.md, scratchpad, step files, plan manifest, loop log) and handle.py (scope.md, resolved_intent.md). Fallback to project_dir/artifacts when no run-dir is active — behavior-preserving for existing callers.
- [x] **Run nickname module.** Shipped 2026-04-26 (commit `13a6470`). 50 adjectives × 50 nouns = 2500 combos; sha1-hashed handle_id for even distribution. 13 tests.
- [x] **Per-run repo bundle.** Shipped 2026-04-26 (commit `a99771b`). `record_repo_base()` at run start when `--repo` is given; `snapshot_repo_bundle()` on finalize writes `repo.bundle` (`git bundle --all`), `git_log.txt`, `branch_diff.patch`, `base_sha.txt` into `<run-dir>/artifact/`. Restorable with `git clone repo.bundle`. 5 tests.
- [x] **Per-run captain's log slice.** Shipped 2026-04-26 (commit `17fb0e9`). `record_log_offset()` at run start, `slice_log_for_run()` on finalize writes `<run-dir>/build/captains_log_slice.jsonl` covering only this run's events. Same pattern `scope_ab_runner.py` used externally — now centralized so every paid run gets a slice. 4 tests.
- [x] **Quality-gate verdict as a captain's log event.** Shipped 2026-04-26 (commit `c644d82`). `QUALITY_GATE_VERDICT` event with verdict/confidence/escalate/reason/step_count/loop_id; emitted from `quality_gate.py::run_quality_gate` after pass1 verdict parsing.
- [x] **`LOOP_CREATED` captain's log event with `reason` + `parent_loop_id`.** Shipped 2026-04-26 (commit `c644d82`). Emitted in `agent_loop._initialize_loop` with reason ∈ {initial, director_restart, closure_restart, quality_gate_escalate}, parent_loop_id, project, max_steps, continuation_depth, dry_run. Threaded through handle.py spawn sites for closure-restart, director-restart, and quality-gate escalation.
- [ ] **Captain's log viewer (low-priority; partially covered by command center).** Render a slice as a sortable timeline (ts, event, loop_id, slug, key fields). Until cross-run queries become a pattern, this is a thin reader over JSONL — no storage migration warranted.
- [ ] **NEXT.md ↔ git activity sync.** Control's NEXT.md showed steps 6–8 unchecked while the repo had matching commits. Either NEXT.md updates lag, or the agent didn't reflect the work back. Either way: closure should compare claimed-done against repo activity and surface the divergence.
- [ ] **Storage decision (deferred).** JSONL captain's log is fine for within-run analysis. Sqlite *indexer* on top (not replacement) is the right pattern when cross-run queries become routine — "median treat-vs-control delta across N runs," "all CLOSURE_VERDICT < 0.5 in last 30 days." Defer until we have a concrete query we keep wanting.
- [ ] **Spend-gated transparency mandate.** Define a threshold (e.g., $2 estimated spend) above which the full source/build/artifact bundle is mandatory and visible to the user without grep. Below that, current behavior is fine.

Items already shipped that fit this frame are listed under **Runtime visibility** below — that section becomes the historical record of partial coverage; this section is the umbrella spec for completing it.

---

### Runtime visibility (tracked 2026-04-17)

- [x] **Current-step symlink.** `/tmp/poe-current-step.log` → active
  streaming merged-output file, updated atomically as each subprocess
  starts. (Shipped 2026-04-17 — commit 58a91dd; symlink target extended
  to merged stream in b188e5f.)
- [x] **Claim-verifier outcome event.** Structured CLAIM_VERIFIER_OUTCOME
  event now emitted with step id + file_not_found/symbol_not_found lists
  + downstream action taken. (Shipped 2026-04-17 — commit 58a91dd.)
- [ ] **Rolling reviewer-calibration metric.** `scripts/probe-stats.sh`
  scans last N days of captain's log, reports
  `dismissed/validated/unprobed` rates for CLAIM_PROBED. Tells us if the
  adversarial reviewer is getting more or less trustworthy over time —
  the reason we built the grounding. (ITEM #3 — deferred; revisit after
  more probe data accumulates.)
- [ ] **Stream-json token visibility (next up, per Jeremy 2026-04-18).**
  `claude -p --output-format stream-json` emits newline-delimited JSON
  events (init, assistant chunks, tool calls, result). Switch the
  subprocess adapter + `/tmp/poe-current-step.log` to this mode so
  operators see live tokens instead of 0 bytes until burst-at-end. Each
  new line becomes the primary liveness signal; CPU-activity demotes to
  a fallback for adapters that don't stream. Parser work: line-reader
  that accumulates assistant deltas + handles tool_use events + parses
  the final `result` event for usage stats. Tests need fake streaming
  fixtures. Size: ~half-day. Coordinates with the adapter protocol
  extraction (stream shape is the point of the Adapter interface).
- [x] **Closure + quality_gate run on partial/stuck/restart.** Previously
  gated on `status == "done"`; metacognitive-recovery paths produced
  material work but emitted no CLOSURE_VERDICT / CLAIM_VERIFIER_OUTCOME /
  CLAIM_PROBED events because terminal status wasn't "done". Widened to
  run on any terminal state that produced ≥1 successful step; kept the
  *escalation* branches gated on "done" only. (Shipped 2026-04-18 —
  commit 7f907bd.)
- [x] **Merged stdout+stderr stream.** `_run_subprocess_safe` pipes both
  streams into a single temp file via `stderr=subprocess.STDOUT`.
  Operator view via `/tmp/poe-current-step.log` now matches what the
  subprocess would print to a terminal. JSON parser tolerant of
  interleaved non-JSON prose. (Shipped 2026-04-18 — commit b188e5f.)
- [x] **CPU-activity liveness signal.** Secondary liveness check sums
  utime+stime across every proc whose session == subprocess pid. A
  silent-but-computing local model burns CPU → last_seen advances →
  liveness timer doesn't fire. Protects slow/local-model inference paths
  from false-kills. (Shipped 2026-04-18 — commit b188e5f.)

### Step-process visibility + elevation (discovered 2026-04-17)

Run 5 of slycrel-go lost step 9 to a hard 600s wall-clock kill of the
`claude -p` subprocess. No way to distinguish "hung" from "working hard",
no partial output captured. Jeremy's framing: "if a step is going to take
that long, it should probably be a sub-milestone/goal on its own, not
just a step" — mirrors the ralph-within-structure feedback (a step that
needs 10+ minutes is a goal the decomposer miscategorized).

- [x] **Heartbeat / liveness timeout.** Stream step subprocess stdout+stderr
  to disk instead of buffering. Kill on *no output for N seconds*, not
  wall clock. Partial output survives the kill. See
  `src/llm.py::_run_subprocess_safe`. (Shipped 2026-04-17 — commit
  a44eb6a.)
- [ ] **Step-to-goal elevation.** When a step's elapsed time or token
  spend crosses a threshold, pause it, capture its state, respawn as a
  child goal with its own decompose/execute/verify loop, merge result
  back. Invasive (state handoff + result merge + parent-loop resumption);
  wait for heartbeat signal to tell us *which* steps actually need this
  before building.

### Bounded workspace / sandboxing (discovered 2026-04-17)

Run 4 of slycrel-go blind test was contaminated by stale local clones. Four
`slycrel-go` trees existed on disk (`~/slycrel-go`, `~/.openclaw/.../slycrel-go`,
`~/.poe/workspace/projects/slycrel-go`, `/tmp/slycrel-go`) — the worker
surveyed one of them instead of cloning fresh into the expected workspace
`repo/` subdirectory. Result: step 1 asserted "project already has a
complete headless server implementation" from the stale tree.

Right behavior: orchestrator should clone the repo into its own workspace,
not scavenge from elsewhere on the filesystem.

- [ ] **Low-effort: workspace-folder constraint option.** A config flag /
  per-goal setting that restricts file access (or at minimum, search paths)
  to the project workspace `repo/` subdir. Not full sandboxing — just
  "don't wander." Cheap win.
- [ ] **Medium-effort: document the bounded-workspace spectrum.** Three
  tiers worth naming: (a) docker/container (full isolation, heavy setup),
  (b) orchestrator workspace only (soft fence — honor convention, no
  enforcement), (c) full machine (current default). Short doc in
  `docs/` noting when to use which and what each protects against.
- [ ] **Diagnostic: detect scavenging.** Captain's log event when a worker
  reads a file outside the project workspace root. Cheap instrumentation,
  makes contamination visible.

Not ambitious; the goal is "constraint to a folder isn't a bad option to
have" not "build a sandboxing subsystem."

---

### Session 20 (2026-04-14) — adversarial review findings (`output/self-review-report-20260414T040637Z-blind.md`)

- [~] **HIGH: Test coverage width not depth** — PARTIAL. pytest-cov with 70% floor: DONE (session 20.5, .coveragerc). Concurrent task_store tests: DONE (session 20.5, +5 tests). End-to-end integration tests: DONE (test_integration.py, 23 tests). Remaining: mutation testing (aspirational, no tooling) and real-LLM-fixture tests (expensive, defer). Item substantially closed.
- [~] **MINOR: Persona auto-selection missing** — Hallucinated. Auto-selection already exists: `persona.py:793` (`persona_for_goal`) with keyword routing + scoring + LLM fallback + freeform creation; called from `handle.py:615` in AGENDA flow. NOW lane intentionally skips persona injection (1-shot path). No fix needed.

### Phase 65 — Constraint/Premise Orchestration (proposed, not yet implemented)

See `docs/CONSTRAINT_ORCHESTRATION_DESIGN.md` + `docs/CONSTRAINT_ORCHESTRATION_REVIEW.md`. Items below are the review's sharp findings that must be resolved before code lands.

- [ ] **BLOCKER: Autonomous-path behavior.** Design says "human gate (unless yolo)" as if binary. Heartbeat/cron path has no channel. Document the behavior: skip? auto-approve after N? block+fail? Default should probably be "log inversion output for post-hoc review, continue with it as planner context, no gate."
- [ ] **BLOCKER: Persistence-install guardrail for autonomous runs.** Background/scheduled paths (heartbeat, cron-owned jobs, timers, backlog drains) must not be allowed to install or enable persistence mechanisms such as systemd units, launchd agents, cron entries, login items, or long-lived daemon processes without an explicit high-trust gate. April 22 live-box cleanup showed a stale scheduled goal (`Monitor BTC price`, originally created April 4) was later revived and installed both cron and systemd automation. Need a policy-layer guardrail in constraint/orchestration so unattended runs can propose persistence changes but cannot apply them silently.
- [ ] **BLOCKER: A/B mechanism.** Cannot evaluate "bounded planning produces measurably better outcomes than unbounded planning" without running goals both ways. Build the A/B capability before enabling anywhere. Probably a config flag or `inversion:` prefix.
- [ ] **BLOCKER: Cost ceiling.** Given April 7-9 token burn, do not ship a feature adding per-goal LLM calls without a per-goal token budget + circuit breaker. Instrumentation first.
- [ ] **Gate heuristic.** Design's "AGENDA goals above N words" is wrong (short goals often benefit most, long ones often don't). Needs an actual judgment signal — possibly complexity classifier, or "use for goals with ≥3 deliverables."
- [ ] **Triad vs. single persona.** Design calls for PM/engineer/architect triad. Review says start with one persona; only add triad if ablation shows the extra personas produce different constraint lines. Cost: 3x LLM calls for premise-setting. Signal: unvalidated.
- [ ] **Persona content vs. costumes.** Design assumes personas produce genuinely different perspectives. Current `persona.py` is largely system-prompt overrides + skeptic modifier. Validate that PM/engineer/architect personas *actually* draw different inversion lines (not just prompt flavor) before investing in triad.
- [ ] **Scope: verification sibling.** Design addresses the *planning* phase. Biggest defect in the system is in the *verification* phase — slycrel-go "passed" because nobody ran a browser. Constraint-setting alone won't close this gap. Needs sibling design for ground-truth verification (real browsers, real endpoints, real test execution — not LLM judgment).
- [ ] **Completion-standard coexistence.** Design says "completion standard is subsumed." Migration plan needed: does completion-standard still run during rollout? If both, do they contradict?
- [ ] **continuation_depth interaction.** Phase 64 restart carries ancestry context across boundaries. Constraints/premises must also be preserved (or explicitly refreshed) across restart. Design is silent.
- [ ] **Concurrent-loop interaction.** `team:` and DAG executor run parallel workers. Do they share the constraint set? Who catches cross-worker conflicts that individually-satisfy-but-together-violate? Unspecified.

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

  Related: BDD (Given/When/Then framing), TDD (red-green cycle), property-based testing (∀ operation, property holds), mutation testing (probe-of-probe bounded version). Sibling of Phase 65 "Scope: verification sibling" blocker above — this IS that sibling.

  **Replay raw numbers** (evidence for the bias finding above): `~/.poe/workspace/projects/slycrel-replay/artifacts/summary.json` — `complete=False, confidence=0.35, 3/5 checks passed`. The two failing probes: (i) overly-strict grep for `!RemoteAddr.*username` false-positived on a legit log line `log.Printf(... username, r.RemoteAddr)`; (ii) `grep -qi xterm web/*` correctly caught that the work summary hallucinated xterm.js integration. The `_CLOSURE_PLAN_SYSTEM` prompt at `director.py:1137` says "Commands must be fast (<15s), safe (read-only or self-cleaning), exit 0 on success. Wrap background processes with `timeout` and always clean up PIDs" — permits live probes but nudges toward grep via path-of-least-resistance.

  **Second full run (2026-04-17, after observability fixes) — modality chart is stark.** CLOSURE_VERDICT event recorded `modality_distribution={"static": 4, "process": 1}`, zero http/ws/browser — on a goal explicitly about "headless server with browser as a client." Closure's own summary admits: *"Gap: runtime validation (server startup + browser connection) was not performed."* Yet it still returned `complete=True confidence=0.92`. Manual post-hoc runtime probe (3 curl calls, ~5 seconds): `/health → 200`, `/ → 200`, `/ws → 101 upgrade`, server logs `player "test" connected/disconnected`. The thing works; closure lucked into being right via static checks. The cheap, mechanical proof would have been three curls — and the system *had time*: the loop ran 810s / 3M tokens / 39 steps. Budget was not the constraint; scaffolding was.

  **Cross-cutting: adversarial review was the hallucinator on this run.** The loop's own adversarial review contested "Go not installed on this machine" and "headless-browser-client branch does not exist" — both false (Go 1.24.2 at `~/go/bin/go`, branch at `origin/headless-browser-client@4fdf0202`). Step output was substantially accurate; the review fabricated contradictions. Suggests the review path needs the same inversion-at-verification discipline: dispute a claim → run the probe that settles it. Currently reviews reason from priors without grounding.

- [ ] **Closure treats failed-to-run commands as checks-passed (silent-verification bug).** Scope A/B run-00 (2026-04-22 baseline, `~/.poe/experiments/scope-ab-2026-04-22/run-00-treat-preFix-baseline/`) ran closure's generated behavioral-verification commands as subprocesses that inherited a PATH *without* `/home/clawd/go/bin`. Every `go build ... && /tmp/test-server ... && curl ...` compound died at the first `&&` with `go: command not found`. Closure's own summary captured it: *"integration verification failed due to missing Go toolchain in test environment — cannot confirm end-to-end HTTP flow actually works."* Closure then returned `complete=True, confidence=0.75, checks_passed=5/5, gap_count=3`. Deliverable was actually correct (manual post-hoc: server builds, serves `/` HTTP 200 / 5444 bytes, creates sessions on POST `/api/session`) — so this didn't burn us; but the verdict was infra-blind.

  **Not a hallucination; equivalent in effect.** Model's claims matched reality; the verification layer just couldn't observe them. The bug is in closure's probe-result interpretation: "command returned (even with exit 127)" is being conflated with "check passed." The path fix is trivial (symlinked for this experiment); the detection-mechanism fix is the real work — closure needs to distinguish *"probe affirmatively confirmed the claim,"* *"probe ran and affirmatively refuted,"* *"probe failed to execute / produced no usable signal."* The third case must NOT count toward `checks_passed`. Candidates for implementation:
  - probes emit a structured verdict (PASS / FAIL / INCONCLUSIVE) instead of free-text output parsed by regex; INCONCLUSIVE never counts as passed
  - pre-flight: resolve every binary referenced in a probe command via `shutil.which`; if any is missing, mark the probe INCONCLUSIVE and surface "missing tool: X" as a closure gap
  - detect shell command-not-found via exit 127 + "command not found" in stderr; auto-demote to INCONCLUSIVE
  - when `checks_passed < checks_run` OR any probe is INCONCLUSIVE, closure should NOT return `complete=True` at confidence > ~0.5 without explicit re-plan

  **Related:** sibling to the "runtime-probe bias" item above. That one is about closure *choosing* static over behavioral probes; this is about closure *mis-reading* the behavioral probes it does choose. Both resolve to: the verification verdict is decoupled from whether the thing was actually verified. Fix one without the other and the same failure surfaces on a different axis.

- [x] **Step runner has no hang protection / no long-lived-process affordance.** Partially closed 2026-04-26 (commit TBD): step_exec.py now classifies long-lived steps via `_is_long_lived_step` (phrase set + verb-noun regex catching "start/launch/run/spawn/boot the X server/service/daemon/listener/broker/worker/api"); when matched, injects `_LONG_LIVED_PROCESS_EXTRA` into user_msg telling the executor to (a) background-spawn (`run_in_background`/`& disown`/`nohup &`), (b) probe readiness via curl/nc/log-grep, (c) call complete_step on readiness signal — not on exit. 14 new tests in `tests/test_step_exec.py::TestIsLongLivedStep` cover the audit case ("Start server with --headless flag on localhost:8080"), each long-lived phrase, the verb-noun regex, and false-positive guards (test/read/analyze steps).

  Original audit case: scope A/B run-02-control (2026-04-23, `~/.poe/experiments/scope-ab-2026-04-22/run-02-control/`) hit step 27 "Start server with --headless flag on localhost:8080", hung indefinitely until SIGTERM (rc=-15). Planner treated "start the server" as a discrete decompose step; the executor had no signal to spawn-and-detach.

  **Still open** (deferred — escalate if observed in the next A/B run):
  - step-runner hard timeout: per-step wall-clock cap that produces a `requires_background_mode` outcome rather than a generic timeout (currently the adapter-level 600s cap fires, but the step is marked blocked rather than actionable)
  - decompose-time classification: emit `background=true` on the step manifest so introspection sees the structural mismatch when later steps depend on a non-terminating one
  - planner prompt change: instruct the decomposer to *not* emit "start server" as a terminal step — servers should start inside a verification step that also probes and shuts down

  **Why this matters:** until this is fully closed, any blind-test goal that produces a long-running binary remains a hazard on the control arm. Scope-injected arms compress to 8 steps and keep server startup inside the verification phase, so they sidestep it; the prompt nudge above should help control arms too.

- [ ] **Rate-limit recovery has no total-backoff cap; recovery path emits phantom `Step -1`.** Scope A/B run-06-control (2026-04-23, `~/.poe/experiments/scope-ab-2026-04-22/run-06-control/`) hit 6 rate-limit retries with exponential backoff (60→120→240→480→960→1800s = 61 min total wall-clock in backoff alone). Per-attempt cap is enforced; **total-backoff-wall-clock is not.** After step 20 finally completed, the recovery path fired with `recovery[NEEDS-REVIEW] risk=medium: Retry with smaller step scope or switch to API adapter` — and produced a `Step -1` marker that the main loop doesn't know how to handle. Run exited rc=1 with no closure verdict. Total runtime: 2h30m for 20 completed steps.

  **Candidates:**
  - cap total backoff wall-clock at ~10 min; if exceeded, bail cleanly (soft-fail with "rate-limited, retry later" rather than another 30-min sleep)
  - recovery path should trigger an actual replan (fewer steps, smaller scope) or adapter switch, not a phantom `Step -1` ordinal
  - while in rate-limit backoff, pause the cost meter or at least annotate "backoff-idle tokens=0" — run-06 showed $41 cost accumulating during 61 min of no real work (cost meter is probably accumulating during retries before the API call; worth auditing)

  **Related:** `decomposition_too_broad` miscalibration (next item). Both are recovery-layer bugs that only surface on long plans.

- [ ] **`decomposition_too_broad` threshold is miscalibrated post-scope.** Scope A/B 2026-04-23: every treat run (scope injected) got `DIAGNOSIS: decomposition_too_broad (warning). 8/8 steps done.` — despite 8 being the *narrowest* decomposition achieved across the whole experiment (controls were 15/37/40). The diagnostic threshold was tuned on pre-scope runs; scope-injected plans are now systematically compressed enough to trip the threshold as a baseline. The warning has become noise.

  **Candidates:**
  - re-tune the threshold against the post-scope decomposition distribution (8 steps for a medium-complexity blind-test goal is fine; treat that as the new normal)
  - condition the threshold on `scope_supplied=true` — scope-gated plans should be *expected* to be tighter
  - separate "too few steps" from "too many steps" — current single-dimension warning fires on both ends ambiguously

- [x] **`run-03-treat` didn't emit CLOSURE_VERDICT despite reaching adversarial review.** Scope A/B run-03 (2026-04-23): 8/8 steps completed, adversarial review fired (3 claim probes), `decomposition_too_broad` diagnosis logged, rc=0 — but no `CLOSURE_VERDICT` event in captain's log and no `closure check: complete=...` line in handle.log. **Root cause (2026-06-11):** `verify_goal_completion` had three silent `return _null` early-exit paths (`no_checks_generated`, `no_check_results`, `verdict_parse_failed`) and an outer-except path that all returned without emitting CLOSURE_VERDICT. Run-03-treat hit `no_checks_generated` (LLM plan returned empty checks). **Fix:** added `_emit_skip(reason)` local helper emitting CLOSURE_VERDICT with `skip_reason` context before each silent return; outer except now also emits. 4 regression tests added (`test_closure_verdict_emitted_when_no_checks_generated`, `…_no_check_results`, `…_on_exception`, `…_not_emitted_on_dry_run`). Shipped 2026-06-11.

### Introspect-sees-no-action: `decomposition_too_broad` (and siblings)

- [x] **`decomposition_too_broad` fires but nothing acts on it.** Partially closed 2026-04-26: introspect now stamps `LoopDiagnosis.project` so retrieval can prioritize same-project history; `find_relevant_failure_notes` ranks same-project diagnoses above goal-token overlap; `decomposition_too_broad` notes render with concrete numbers (e.g. "Step 8 took 534s with 277K tok") and append the actionable cap (`≤120s/200K tok per step; split if a step touches >3 files`). The next loop on the same project sees this in `lessons_context` ahead of all other failure-pattern injections. Phase 62 (mid-loop redecompose on `_handle_blocked_step`) was already live for the blocked path; this closes the *post-mortem → next-decompose* feedback that was previously generic-lesson-only. Original Apr 16 finding (`loop 85ac29ee-*`) is the canonical case this addresses.

  **Mid-loop visibility added 2026-04-26 (commit TBD):** new `STEP_TOO_BROAD` captain's log event fires the moment a `done` step exceeds both caps (>120s elapsed AND >200K tokens). Wired in `_write_iteration_artifacts` after march-of-nines. Visible in the per-run `captains_log_slice.jsonl` and as a project decision. The post-mortem path already feeds the next decompose; this closes the visibility gap on the in-flight loop. 7 new tests in `tests/test_agent_loop.py` cover the predicate (above caps, below caps, only-one-cap, blocked/skipped/zero-metric guards, EVENT_TYPES registration).

  **Still open** (deferred — needs more A/B data before committing to mid-loop intervention): actually *acting* on the signal mid-loop (kill + replan vs continue with warning logged). Visibility-first is the cheapest credible upgrade today; the action question deserves data on how often the signal fires and whether the loop completes successfully despite it.

### From X research (2026-04-11 — 10 posts, live orchestration, 2 loops)

Full report: `~/.poe/workspace/output/x-research-20260411T081706Z.md`

- [ ] **Large Memory Models (LMMs)** — Engramme: new architecture beyond RAG. Watch list — monitor for API release. **Priority 6/10.** Source: @svpino.
- [ ] **Google MCP Toolbox** — Opinionated MCP server for data tools. Forward-looking; adopt when JSONL memory needs structured DB. **Priority 5/10.** Source: @_vmlops.
- [ ] **Polymarket 36GB dataset + backtester** — 72M trades, free on GitHub. Useful for polymarket-backtest skill. **Priority 4/10.** Source: @recogard.

### From real-world regression runs (2026-04-12, session 18 — 4 parallel goals)

Ran 4 live goals: Polymarket research, nootropic synthesis, recipe site build, self-audit.


**Test goal results:**
- Polymarket: 8/8 done, 1.47M tokens, 16min, quality gate PASS (0.85), 3 contested claims
- Nootropic: 8/8 done, 544K tokens, 12min, quality gate PASS (0.80), 5 contested claims
- Recipe site: 10/10 done (pending confirmation)
- Self-audit: 11/11 done, found 5 contradictions + structural bugs, 2 critical races

**Test goals (future runs):**
- [ ] **Local LLM research** — Research tiny LLMs suitable for bundling with the orchestrator or self-hosting on cheap hardware (e.g. local network). Evaluate: inference speed, quality at orchestration tasks (step decomposition, lesson extraction), memory footprint, quantization options. Goal: reduce API dependency for cheap-tier work.
- [ ] **Recipe site PM agent** — Recurring goal against slycrel/orchestrator-test-recipes: review code, open issues for missing features, review PRs, suggest architectural improvements. Tests GitHub integration + multi-step judgment.
- [ ] **Recipe site dev agent** — Recurring goal: pick open issues, implement on branches, open PRs, maintain running Docker instance on this machine. Tests code generation + git workflow + deployment.


### From adversarial review (2026-04-12, 3 rounds — haiku + full model)
- [~] **Semantic memory deduplication** — SUBSTANTIALLY ADDRESSED. `record_lesson()` already does at-write-time near-dedup: exact-text match + word-overlap Jaccard ≥ 0.8 within most-recent 100 lessons. Unbounded growth prevented. Embedding-based similarity (true semantic) remains aspirational P3 — requires API call at every write, cost not justified given current lesson volume.

### Self-Extensibility / Decision Point Hooks (design exploration)
- [ ] **Composable decision-point hooks** — The system currently has pre/post step hooks (step_events.py), inspector observation, quality gate, and prompt injection (standing rules/lessons/skills into decompose). But these aren't composable: you can't say "after decompose, before execution, run extra verification on steps 3 and 5." MTG-style stack where effects can be intercepted at targeted points. For now, prompt-stage injection is sufficient. Revisit when operational experience shows which decision points actually need interception. Key constraint: any self-extensibility must be human-gated (see evolver guardrail auto-apply fix).

### Phase Transition Contracts (architecture — revisit after operational data)
- [ ] **Formal stage contracts between pipeline phases** — Currently phase transitions are implicit: decompose outputs strings, execute takes strings, finalize takes outcomes. No typed contracts, no hard validation gates between phases. Pre-flight is advisory-only (loop proceeds regardless). Trajectory check is the first real mid-pipeline gate. Need: (1) typed output contracts per phase (not just "a list of strings" but "atomic steps that cover the goal scope"); (2) hard gates that re-plan or abort instead of proceeding with garbage input; (3) audit which existing checks are load-bearing vs noise. The Starship optimization: delete the advisory checks that never change behavior and replace with fewer, harder gates. Defer until operational data shows which gates actually matter.

### From X research runs (2026-04-09)

Six X posts researched via live Poe missions. Actionable items extracted:

- [ ] **TOOLS.md + STYLE.md gaps** — @imjustinbrooke's "7 files to run your business" framework maps to Poe: SOUL.md ✓, AGENTS.md ✓, USER.md ✓, MEMORY.md ✓, HEARTBEAT.md ≈ heartbeat scripts. Missing: explicit TOOLS.md (tool registry covers this partially) and STYLE.md (persona covers this partially). Consider whether explicit files add value.
- [~] **Eval-driven harness hill-climbing** — Superseded by "Harness hill-climbing as autonomous loop" below (session 30 entry has the concrete wire-up plan: `run_nightly_eval()` → failure trace analysis → harness proposal → evolver suggestion → `_verify_post_apply`). Left here for source attribution: @mr_r0b0t/@ashpreetbedi/@Vtrivedy10.
- [ ] **Letta API comparison** — @carsonfarmer/@sarahwooders: Anthropic's Managed Agents API mirrors Letta's 1yr-old API. Provider-managed memory = lock-in. Poe's file-based memory is aligned with "memory outside providers" thesis. Monitor Managed Agents API for useful features without adopting their memory model.
- [ ] **Team OS / shared context layer** — @aakashgupta: 250+ structured docs/quarter compound into organizational knowledge. Validates knowledge layer K1-K2 investment. The "learning flywheel" pattern (each commit makes the repo smarter) is the vision for standing rules + lesson promotion.

### Infrastructure
- [ ] **Phase 38 subpackage move** — src/ is flat with 49 modules. Deferred (33+ imports per group), revisit when it causes real problems.

### Links fetched but not fully digested
- [ ] **Polymarket behavioral analysis** (hrundel75) — 400M trades / 2400 wallets. Good prompt for different Polymarket test: "find behavioral patterns not picks."
- [ ] **Build-your-own-X** (agenticgirl) — 484k star repo, learning methodology. Low priority.

### Steal-list items from Miessler/Zakin (grok-response-3.txt)
- [ ] **Dashboard: replay as factory mode** — "Replay this run as factory mode" button. Re-runs the original goal but lets evolver inject one self-generated sub-goal from recent signals. Instant Mode 3 visibility. Low priority until dashboard gets real usage.
- [~] **Eval-driven harness hill-climbing** — Superseded by "Harness hill-climbing as autonomous loop" in the 18-link research runs section below; that entry has the concrete wire-up plan. Source: @mr_r0b0t/@ashpreetbedi.

### From link-farm (2026-04-09–11 batch)

- [ ] **Latent Briefing — KV cache compaction for multi-agent memory** — Ramp Labs paper (quoted by @vral). KV cache compaction technique for efficient context sharing across hierarchical multi-agent systems; eliminates need to pass full .md files between agents. Currently Poe passes context as text files; this approach would let child agents share a compressed parent context layer directly. **Priority 5/10 — monitor until implementation details are public.** Source: @vral.

- [ ] **Isolated worktree per sub-agent** — from Alpha Batcher's breakdown of Claude Code's architecture (@alphabatcher). Each sub-agent gets its own git worktree so writes don't collide. Relevant to concurrent run safety (Phase 62 project isolation). Current `is_project_running()` + per-project lock file is a simpler version; worktree isolation is stronger. **Priority 6/10 — revisit when parallel missions are actually running.** Source: @alphabatcher.


- [ ] **Kronos financial foundation model** — Nav Toor (@heynavtoor). Open-source time-series model trained on 12B candlestick records from 45 exchanges, 93% more accurate than leading models, zero-shot across any asset/timeframe, 4M–499M param sizes. Available on HuggingFace. **Watch list — if Polymarket research resumes, evaluate as price-prediction layer instead of LLM-based price inference.** Source: @heynavtoor.

### From 18-link research runs (2026-04-14, session 30)

Full reports: `docs/research/ai-agent-memory-synthesis.md`, `docs/research/ai-agent-memory-steal-list.md`, `docs/research/x-posts-steal-list-20260414.md`


- [ ] **Eval harness + holdout discipline** — evals = new training data (@realsigridjin/Better Harness). Reward-hacking risk: evolver currently evaluates on the same outcomes it was trained on. Fix: add train/holdout split to `run_nightly_eval()` → evolver validates on holdout set only. Prevents self-congratulatory loops where evolver improves its own eval metrics without improving real behavior. **Priority 6/10 — addresses reward-hacking as system matures.** Source: @realsigridjin.

- [ ] **Harness hill-climbing as autonomous loop** — @ashpreetbedi/@mr_r0b0t: use eval benchmark scores as autonomous hill-climbing signal for harness improvement (LangChain TerminalBench 2.0: 52.8→66.5% with no model change). Poe has `eval.py` + `evolver.py` but they're not wired as an autonomous feedback loop. Fix: `run_nightly_eval()` → failure trace analysis → harness proposal → evolver suggestion → `_verify_post_apply`. **Priority 6/10 — closes the verify→learn loop that's currently 80% done.** Source: @ashpreetbedi + @Vtrivedy10.

- [ ] **Associative JSONL memory links (related_ids)** — Engramme: associative recall surfaces neighboring memories without explicit query. Portable: add `related_ids` field to JSONL memory nodes; cosine-similarity pass in `reflect_and_record()` links new nodes to nearby existing ones; when a node is accessed, inject linked neighbors. Approximate associative recall at file-based scale. **Priority 5/10 — medium effort, enhances memory depth.** Source: @svpino/Engramme.

- [ ] **Dumb loop audit (scaffolding designed to be removed)** — Alpha Batcher breakdown of Claude Code: Anthropic's deliberate "thin harness" philosophy. Each scaffold should pass the future-proof test: dropping in a more powerful model should improve performance WITHOUT requiring harness complexity changes. Run a scaffolding audit on agent_loop.py — label each check as load-bearing vs removable. Manus precedent: rebuilt agent 5× in 6 months, each rewrite removed complexity. **Priority 5/10 — strategic/architectural, no code cost.** Source: @alphabatcher/@akshay_pachaar.

### Grok Round 4 feedback (2026-04-07)
- [ ] **SERV model family** — (open_founder tweet) "SERV-nano matched GPT-5.4 at 20x lower cost and 3x speed." New model family worth tracking as potential OpenRouter routing option. Research: is there an API? What benchmarks? Low priority until available.

## Test Ideas

- [ ] **Polymarket behavioral test** — "Analyze 400M+ Polymarket trades to find behavioral patterns among top wallets — what do winners do differently?" (from hrundel75 link)
- [ ] **"Get Jeremy rich" prompt** — long-term, after trading patterns are validated and backtested. Baby steps.

## Completed (archive)

Items moved here when done, for reference:


From jeremy (clean up and integrate with the above later)
- [ ] Examine the research in research/orchestration-knowledge-layer, and the follow-up research in docs/knowledge-layer (and consolidate into one or the other location). document proper implementation paths and implement the framework, with notes on how to flesh this out as needed. **Note (2026-04-10):** Tracked above in "Memory / Knowledge Layer" section. K0 baseline done; memory.py decomposition + K1-K8 implementation plan needed.
