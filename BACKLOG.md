# Backlog — Deferred Items, Ideas, and Known Issues

Single canonical location for everything we've identified but haven't done yet.
Read this at the start of every session. Update it as items are completed or new ones emerge.

**Completed items live in [BACKLOG_DONE.md](BACKLOG_DONE.md)** — move items there with their full context when they ship; that file is the archive of what we've already decided, tried, or superseded, and it's ingested by `dev-recall` for historical context.

Last reviewed: 2026-07-03 (shipped #-1 workspace-pin layout unification → BACKLOG_DONE.md; the audit also closed the wider orch_root()/memory split-brain class across ~12 runtime files).

---

## Actionable Stack

Ordered open work that matters. Top of the list is next.

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
  indexer (feed a similar/rephrased re-attempt), partial-run rescue. Append to
  `run_curation.CURATORS`.
- [ ] **Unify rung-4 step I/O.** loop-log still stores a truncated result excerpt;
  cross-reference the full captured call so the loop view links to the byte-level
  record.
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

### 1. Bound worker writes to run-dir / workspace (artifacts leaking into repo root)

**Evasion specimen (2026-07-04, first organic batch):** run 668e46d1's worker
`cd`'d into the repo and wrote `scripts/count-lines.py` with a *relative* path
— invisible to both the structured-tool scavenge check (no absolute path
input) and the Bash regex (only the cd target surfaced, recorded as a read).
The cwd fence binds per-step launch cwd, but a worker can cd elsewhere
mid-command. Stray removed (project dir had its own in-fence copies). Any
tier-a design must handle cwd drift inside a single Bash command, not just
absolute-path writes.

- [ ] **Workspace boundary: build-goal artifacts landed in the repo root** —
  run_health.py + example output were written to cwd (the repo) instead of the
  run's artifact dir; goal even said "as an artifact file". Moved them into
  `e1b9f95e-humble-lantern/artifact/` post-hoc. Existing bounded-workspace
  BACKLOG item covers the general fix; this is a concrete repro.
  **2nd organic repro 2026-06-12:** the BACKLOG-claim-audit goal wrote
  `backlog_claim_audit.md` (a genuinely good 230-line audit, verdict ACCURATE
  with file:line evidence) to the *repo root* — its run dir
  `140d2a4f-warm-pebble/artifact/` was empty. Moved post-hoc. This keeps
  happening to agenda build-goals: the agent's cwd is the repo, and nothing
  constrains where it writes. The NOW-lane artifact path was fixed (writes to
  the run dir now) but the agenda loop's worker writes are still cwd-relative.
  The fix is the bounded-workspace item below; this is the strongest case yet
  that it's not theoretical — good output is landing in version control.

  **Root cause found + soft-fence shipped (2026-06-26):** the agentic
  subprocess (`claude -p` / `codex exec`) was spawned with **no `cwd`** —
  `_run_subprocess_safe` → `Popen` inherited the parent's cwd, so relative
  writes landed wherever that happened to be (repro: a fizzbuzz build wrote
  `fizzbuzz.py` to `/tmp/claude-1001/` instead of the workspace, while the
  prompt's "save to {project_dir}/" was simply ignored). Fixed by threading
  `cwd` through `complete()` → `_run_subprocess_safe(cwd=...)` → `Popen(cwd=)`,
  and binding it to `project_dir` in `step_exec.execute_step` (makedirs first;
  non-existent cwd is ignored, no regression). This is the **soft-fence (tier b)**
  from the spectrum below — relative writes now land in-workspace by default,
  but nothing stops an agent from writing an absolute path elsewhere. The
  scavenging diagnostic + hard fence (tier a) are still open.

  **Soft-fence extended to ALL agentic paths (2026-06-26):** the executor fix
  above bound cwd *only* for `step_exec.execute_step`. The done≠achieved
  verification runs caught the gap on camera: the non-executor agentic paths
  (`verification_agent` verify/adversarial/quality, `quality_gate`
  council/debate/adversarial, `pre_flight`, `step_exec` refinement, and
  `claim_probe`'s `settled_by_command` runner) still inherited the launch cwd.
  When a verifier couldn't find the cited artifact at the workspace path (wrong
  cwd) it **re-created the script and re-ran it to "verify"** — leaking files
  into the launch dir AND fabricating ground truth (then its own probe dismissed
  the correct path-mismatch contestation). Confirmed by experiment: leak follows
  the launch dir (repo root vs scratchpad). Fixed with a run-scoped ambient cwd:
  `llm._DEFAULT_SUBPROCESS_CWD` (ContextVar) resolved in `complete()` as
  `kwargs["cwd"] or get_default_subprocess_cwd()`; `run_agent_loop` sets it to
  the project dir, `handle.py` scopes it around `run_quality_gate`, `claim_probe`
  reads it for `subprocess.run(cwd=…)`. NOW lane leaves it unset → inherits
  launch cwd (correct for an interactive ask). Tests reset it via an autouse
  conftest fixture. This closes the leak *and* an anti-hallucination hole (a
  verifier that can see ground truth stops fabricating it). Tier-a hard fence
  still open.

  **3rd repro 2026-07-03 — fence hole: `if project:` guards leave
  no-project-yet iterations fully unfenced.** Post-fence leaks confirmed by
  mtime: repo-root `artifacts/` strays from 2026-07-02 burn-in goals
  (`coding-notes-digest.md` 10:41, `substrate-explained.md` 10:10,
  `comm-examples.md` 11:03 — the last is the NOW-misroute goal, which DID
  write its file, to launch cwd; that's why closure found nothing) and from
  the 2026-07-03 blocked-step batch (goal "Create artifacts/raw.json… repair
  system": its **first, blocked iteration** wrote `raw.json` 03:46:07,
  `repair.py` 03:46:13, `clean.json` 03:46:15 to launch-cwd relative paths;
  the post-block retry landed everything correctly in
  `projects/implement-a-json-repair-system/`). Mechanism: every fence site is
  conditional on a truthy project — `agent_loop.py` sets the ambient cwd only
  `if project:`, `loop_execute.py` leaves `_proj_artifact_dir=""` without one,
  so `step_exec` gets `project_dir=""` and skips the `Popen(cwd=…)` bind. A
  run that enters the loop before its project identity is established
  executes unfenced; once blocked/retried (project by then assigned) it's
  fenced — matching the strays-only-from-early-iterations pattern. Two
  project dirs per goal (`create-artifactsrawjson-containing-exactly` empty
  stub vs `implement-a-json-repair-system` real) point at the goal-slug vs
  plan-derived-name split-brain (see #-1) as the reason project is empty at
  entry. **Fix direction:** never run an AGENDA worker step with inherited
  launch cwd — bind the ambient cwd unconditionally at loop entry (fall back
  to goal-slug project dir, or the run dir, when project is unset); NOW lane
  stays exempt by design. Evidence preserved:
  `scratchpad/cwd-leak-evidence/` (session dbbb5f5c) + repo `artifacts/`
  strays left in place (gitignored).

  **Fence hole FIXED 2026-07-03** (correction to the 3rd-repro mechanism:
  run metadata showed `project: None` for the whole run — dispatched goals
  reach `run_agent_loop` with NO project ever, so the entire run was
  unfenced, not just early iterations; the post-block retry only landed
  correctly because the failure hint pushed the worker to absolute paths).
  Two layers: (1) `handle.py` defaults the loop's `project` kwarg to
  `_goal_to_slug(message)` — the same identity the scope pass derives, so
  scope + execution stop pointing at two different project dirs and ALL
  existing `if project:` fence sites engage (ambient cwd, `_proj_artifact_dir`,
  per-step `Popen(cwd=)`, prompt project_dir); (2) `agent_loop` loop-entry
  ambient bind is now unconditional — a project-less run (direct callers)
  falls back to the goal-slug project dir, mkdir'd first (Popen raises on a
  missing cwd). NOW lane untouched. Tests:
  `TestProjectlessDispatchFence` (handle) +
  `test_loop_projectless_run_still_fences_cwd` (loop). Tier-a hard fence
  (absolute-path writes) still open — this closes the relative-write class.
  **Live-proven 2026-07-03** (run `07d14464-misty-finch`): dispatched
  project-less goal "write a 4-line limerick to artifacts/limerick.txt" —
  deliverable landed at `projects/write-a-4line-limerick-about/artifacts/`
  (the goal-slug dir), zero new launch-cwd strays, run done/achieved
  honestly. Run-dir metadata `project` stays None by design (HandleResult
  reports the caller's ask; the loop runs with the slug).

**Bounded workspace / sandboxing (discovered 2026-04-17)**

Run 4 of slycrel-go blind test was contaminated by stale local clones. Four
`slycrel-go` trees existed on disk (`~/slycrel-go`, `~/.openclaw/.../slycrel-go`,
`~/.maro/workspace/projects/slycrel-go`, `/tmp/slycrel-go`) — the worker
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
- [x] **Diagnostic: detect scavenging.** ~~Captain's log event when a worker
  reads a file outside the project workspace root.~~ **DONE 2026-07-03:**
  `artifact_check.detect_out_of_fence_access` scans each step's REAL tool
  transcript (stream-json tool_events) for absolute paths outside the fence
  (project dir + workspace) — structured tools by path input, Bash by
  command-string scan with system prefixes filtered, deduped + capped at 20.
  Emits `SCAVENGE_DETECTED` (loop_execute, gate `validate.scavenge_detect`
  default on, never blocks). Reads and writes flagged separately — an
  out-of-fence *write* in the transcript is exactly the tier-a evidence the
  hard fence needs; watch these rows to size that work.

Not ambitious; the goal is "constraint to a folder isn't a bad option to
have" not "build a sandboxing subsystem."



### 9. Local-validator measurement — token/cost delta report

- [ ] **Token/cost delta report.** Quantify tokens saved vs escalation rate vs added
  latency, on Poe's own task corpus — the actual ROI of running this.

### 10. Local-validator measurement — tune `local_max_tokens` per model

- [ ] **Tune `local_max_tokens` per model.** Live finding (2026-06-21 verify run):
  VibeThinker's `<think>` trace on *real* (long) step results overran the 1024
  floor → empty content → conf 0.00 → spurious escalation on 2/5 steps (the other
  3/5 validated free at conf 1.00). Bumped default to 2048; deep-eval should find
  the floor that maximizes decisive-local rate without wasting generation latency.


### 13. Evolve the evolver — evaluate its own scanners for actual practical value

- [x] **Investigated 2026-07-03** (after the `evolver.py` split landed). Original
  question — "which scanners survive `_verify_post_apply` vs. generate noise"
  — **can't be answered empirically yet**, and that's the actual finding.

  **The evolver has essentially never run in production.** `run_evolver()` is
  only wired into `heartbeat.py` (every `evolver_every=10` ticks) or manual
  `cli.py` invocation — and `maro-heartbeat.service` (in `deploy/systemd/`)
  was never installed (`systemctl list-unit-files` / `find` for it: nothing).
  All historical evolver data in `~/.maro/workspace/memory/` —
  `suggestions.jsonl` (117 rows), `change_log.jsonl` (406), `evolver-baselines.jsonl`
  (638), `calibration.jsonl` (3,312) — is timestamped exclusively 2026-04-04
  through 2026-04-12, in tight sub-second bursts. That's pytest contamination
  from before the Apr-12 test-isolation overhaul (CLAUDE.md's own changelog
  names that fix), not real usage — confirmed by `success_rate: 1.0` /
  `avg_cost_usd: 0.0` on every baseline row. Zero of the 117 suggestions were
  ever `applied` (and the 116 non-trivial ones are `category="inspection_finding"`
  from `inspector.py`, not from any of the evolver's own scanners at all).
  `suggestion_outcomes.jsonl` — the file `scan_suggestion_outcomes()` and
  `_verify_post_apply()` both depend on — doesn't exist. There is no
  apply→verify track record to mine, in either direction.

  Real production data *does* exist and is current (`outcomes.jsonl`: 1,355
  rows, through 2026-07-03) — the evolver just isn't being pointed at it.
  Ran the five non-LLM statistical scanners directly (read-only, zero cost)
  against that real corpus to get a first honest read:

  | Scanner | Result on real data | Read |
  |---|---|---|
  | `scan_step_costs` | 1 finding: `research` steps avg 174K tokens/step across 20 steps, ~$0.56 total Haiku-routing savings | Fired immediately with a concrete, correct, actionable suggestion. Looks genuinely useful. |
  | `scan_canon_candidates` | 3 findings: lessons applied 48–80x across 4–5 task types, promotion-to-AGENTS.md candidates | Same — fired immediately with well-evidenced output. Looks genuinely useful. |
  | `scan_calibration_log` | 0 findings | Inconclusive — `calibration.jsonl`'s only data is the pre-fix contamination window, so there's no real escalation-decision data to score yet. |
  | `scan_quality_drift` | 0 findings | Inconclusive — needs a warm rolling baseline (`evolver-baselines.jsonl`) that doesn't exist post-fix; can't judge on one cold cycle. |
  | `scan_suggestion_outcomes` | 0 findings (expected) | Structurally can't produce anything until real apply→verify cycles happen — this is the chicken-and-egg scanner. |

  **Recommendation**: don't prune anything on theory. 2 of 5 statistical
  scanners already prove out on first real invocation; the other 3 are
  untestable until the loop actually runs, not necessarily bad. The real
  blocker is operational, not scanner quality: get `run_evolver()` actually
  executing against production data on a schedule (install/enable
  `maro-heartbeat.service`, or start with periodic manual `cli.py` invocations
  if an always-on daemon isn't wanted yet) so `scan_evolver_impact()` and
  `scan_suggestion_outcomes()` — both already built for exactly this — have
  real data to compute over. Left this as a decision for Jeremy rather than
  enabling a new unattended-LLM-call service autonomously.

- [x] **Shipped 2026-07-03 (Jeremy's call): per-run hook instead of a systemd
  daemon** — "I'm not a huge fan of taking over the system... let's try and
  be an app rather than an OS." Investigation found `run_skill_maintenance()`
  (promote/demote/rewrite) was *already* firing post-run/pre-cleanup,
  pass-or-fail, at `loop_finalize.py`'s `_finalize_loop()` — that part needed
  nothing. What was missing was the 5 free statistical scanners from the
  finding above; they only ran on `heartbeat.py`'s tick schedule. Extracted
  them out of `run_evolver()`'s inline blocks into a shared
  `evolver.run_statistical_scans()` (no behavior change to `run_evolver()`
  itself — same flags, same wrapping, just de-duplicated) and call it from
  `_finalize_loop()` right after `run_skill_maintenance()`, gated only on
  `not dry_run`. No LLM calls in these 5 scanners, so per-run cadence costs
  nothing; findings are saved via `_save_suggestions()` for visibility only —
  this hook never auto-applies (matches `scan_canon_candidates()`'s existing
  "not automatic" contract). No daemon installed, no new service, no change
  to heartbeat.py — `run_evolver()`'s full cycle (LLM pattern analysis +
  business-signal scan + auto-apply) is untouched and still tick-scheduled.
  This finally gives `scan_suggestion_outcomes()`/`scan_evolver_impact()`
  real per-run data instead of the empty/contaminated history documented
  above. Full suite green (133/133) after the change.

---

## Vision / Deferred

### Graph memory + recursive-orchestration scoped memory (2026-06-21, vision)

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

### Design constraint: decay trust, never data

- [ ] **Design constraint, not a task: decay trust, never data.** Append-only
  evidence layer stays perfect (the computerization edge over human forgetting);
  only compiled-truth confidence decays. Crystallization Stages 4–5 must be
  demotable back to language form — world-change is the frequent trigger,
  model upgrades the rare one.

### File-claim fabrication — FS-diff ground-truth guard SHIPPED (2026-06-26)

- [x] **Write-claim fabrication (v1 shipped 2026-06-26).** A step that claims to
  write a file but produces no artifact is now demoted `done`→`blocked` by a
  zero-LLM filesystem-diff guard (`src/artifact_check.py`, wired into the AGENDA
  build loop in `agent_loop.py` before ralph verify; config gate
  `validate.artifact_check`, default on, fail-open). Conservative v1 rule:
  flag iff (≥1 file write-claim) AND (empty `project_dir` before/after diff) AND
  (no claimed path exists on disk). Emits captain's-log `FABRICATION_DETECTED`.
  This is the AGENDA-loop sibling of handle.py's NOW-lane `_provenance_missing`.
  Enabled by the #1 cwd fix: writes are bounded to `project_dir`, so its diff is
  reliable ground truth.
  - Side fix: removed a leaked `fizzbuzz.py` test artifact accidentally committed
    to repo root in 7ea4d7b (the #1 cwd-fix commit); its basename collision in
    cwd actually surfaced the design flaw that `_exists_anywhere` must NOT consult
    `Path.cwd()` (orchestrator cwd = repo root, full of unrelated files).

- [x] **Inert-output fabrication (shipped 2026-06-26, same module).** New layer
  in `artifact_check.py`, still zero-LLM and NO code execution. The actual
  organic repro: a step writes `fizzbuzz.py` (so the missing-artifact layer
  passes — the file exists) then narrates *"verified output: 1,2,Fizz,4,Buzz"*,
  but the file has no `__main__`/top-level code and prints nothing when run.
  Caught by **static AST analysis**: if the result asserts concrete stdout (a
  runtime verb like prints/output/"when run" — NOT a function `returns` claim —
  AND concrete content like digits/quotes) while every produced `.py` is provably
  inert (`_python_is_inert`: body is purely defs/imports/assigns/docstring), it
  cannot have produced that output. `ArtifactVerdict.kind` distinguishes
  `missing-artifact|inert-output`. Tests: `tests/test_artifact_check.py` +
  3 full-loop integration tests in `tests/test_agent_loop.py` (missing /
  inert-output / real-write).

- **REJECTED: no-path-write layer.** Prototyped (write-ish words + empty diff +
  no path named) and reverted same day: it is **absence-based, not
  evidence-based** — an empty workspace diff does not prove fabrication
  (analysis/planning steps and out-of-workspace writes legitimately leave it
  empty). It false-positived on 4 real test completions in the full suite. A
  verifier that hallucinates is its own failure mode; the guard now only fires on
  positive evidence (a named-but-absent file, or an inert file vs a concrete
  output claim).

- [x] **No-path *execution* fabrication — v1 SHIPPED 2026-06-26
  (`check_execution_claim`).** The residual hole: "ran the tests: 142 passed"
  naming no file and producing none. Unblocked by item #3 (real tool transcript
  on `resp.tool_events` / `outcome["tool_events"]`). v1 ships ONLY the
  unimpeachable positive-evidence contradiction: the step claims the run
  SUCCEEDED, yet every command it actually ran FAILED (non-zero exit / is_error)
  and the result never acknowledges a failure. Wired into the agent_loop
  fabrication guard as a fallback after the FS/AST layers (kind
  `execution-contradiction`); blocks the step the same way. Execution-free
  (reads the transcript, never re-runs).
- [ ] **Remaining exec-fabrication shapes (deliberately deferred — false-positive
  risk).** Two cases the v1 contradiction check intentionally does NOT flag,
  because each can fire on legitimate runs (same lesson that killed the
  no-path-write layer): (a) **"claims execution but ran nothing"** — the per-step
  transcript can't see a prior step's legitimate run, so absence ≠ proof; (b)
  **partial** — some commands succeeded and a later/key one failed; telling the
  test command from setup needs intent modeling, and fix-then-succeed is
  legitimate. Revisit only with a sharper signal (e.g. matching the claimed test
  count against the real `tool_result`), not a looser gate.

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
  side-quest outputs live in `~/.maro/workspace/projects/<slug>/
  artifacts/` and survive across reruns of the same goal family.

### Modular refactoring (AFK-friendly chunks, queued 2026-04-18) — deferred chunks

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
- [ ] **Test clutter trim.** Jeremy's outside-in-testing posture
  applied to the suite: tests that poke private functions with mocked
  collaborators and assert call-shape are performative. Sweep tests
  touched during recent refactors and mark ones that would break on
  a rename-without-behavior-change — delete the clearest offenders,
  keep anything covering a module boundary or regression. Don't do
  a mass pass; trim opportunistically when editing neighboring code.
  (Tracked as a posture, not a standalone chunk.)

### Captain's log viewer (low-priority; partially covered by command center)

- [ ] **Captain's log viewer (low-priority; partially covered by command center).** Render a slice as a sortable timeline (ts, event, loop_id, slug, key fields). Until cross-run queries become a pattern, this is a thin reader over JSONL — no storage migration warranted.

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

### Phase 65 — Constraint/Premise Orchestration (proposed, not yet implemented)

See `docs/CONSTRAINT_ORCHESTRATION_DESIGN.md` + `docs/CONSTRAINT_ORCHESTRATION_REVIEW.md`. Items below are the review's sharp findings that must be resolved before code lands. (Persistence-install guardrail pulled out to the Actionable Stack as a standalone safety item.)

- [ ] **BLOCKER: Autonomous-path behavior.** Design says "human gate (unless yolo)" as if binary. Heartbeat/cron path has no channel. Document the behavior: skip? auto-approve after N? block+fail? Default should probably be "log inversion output for post-hoc review, continue with it as planner context, no gate."
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

  Related: BDD (Given/When/Then framing), TDD (red-green cycle), property-based testing (∀ operation, property holds), mutation testing (probe-of-probe bounded version). Sibling of Phase 65 "Scope: verification sibling" blocker above — this IS that sibling. **Cross-link:** also the sibling of the Actionable "Closure treats failed-to-run commands as checks-passed" item — runtime-probe bias is closure *choosing* static over behavioral probes; the closure-failed-to-run item is closure *mis-reading* the behavioral probes it does choose. Same root: the verdict is decoupled from whether the thing was verified.

  **Replay raw numbers** (evidence for the bias finding above): `~/.maro/workspace/projects/slycrel-replay/artifacts/summary.json` — `complete=False, confidence=0.35, 3/5 checks passed`. The two failing probes: (i) overly-strict grep for `!RemoteAddr.*username` false-positived on a legit log line `log.Printf(... username, r.RemoteAddr)`; (ii) `grep -qi xterm web/*` correctly caught that the work summary hallucinated xterm.js integration. The `_CLOSURE_PLAN_SYSTEM` prompt at `director.py:1137` says "Commands must be fast (<15s), safe (read-only or self-cleaning), exit 0 on success. Wrap background processes with `timeout` and always clean up PIDs" — permits live probes but nudges toward grep via path-of-least-resistance.

  **Second full run (2026-04-17, after observability fixes) — modality chart is stark.** CLOSURE_VERDICT event recorded `modality_distribution={"static": 4, "process": 1}`, zero http/ws/browser — on a goal explicitly about "headless server with browser as a client." Closure's own summary admits: *"Gap: runtime validation (server startup + browser connection) was not performed."* Yet it still returned `complete=True confidence=0.92`. Manual post-hoc runtime probe (3 curl calls, ~5 seconds): `/health → 200`, `/ → 200`, `/ws → 101 upgrade`, server logs `player "test" connected/disconnected`. The thing works; closure lucked into being right via static checks. The cheap, mechanical proof would have been three curls — and the system *had time*: the loop ran 810s / 3M tokens / 39 steps. Budget was not the constraint; scaffolding was.

  **Cross-cutting: adversarial review was the hallucinator on this run.** The loop's own adversarial review contested "Go not installed on this machine" and "headless-browser-client branch does not exist" — both false (Go 1.24.2 at `~/go/bin/go`, branch at `origin/headless-browser-client@4fdf0202`). Step output was substantially accurate; the review fabricated contradictions. Suggests the review path needs the same inversion-at-verification discipline: dispute a claim → run the probe that settles it. Currently reviews reason from priors without grounding.

### Composable decision-point hooks (design exploration)

- [ ] **Composable decision-point hooks** — The system currently has pre/post step hooks (step_events.py), inspector observation, quality gate, and prompt injection (standing rules/lessons/skills into decompose). But these aren't composable: you can't say "after decompose, before execution, run extra verification on steps 3 and 5." MTG-style stack where effects can be intercepted at targeted points. For now, prompt-stage injection is sufficient. Revisit when operational experience shows which decision points actually need interception. Key constraint: any self-extensibility must be human-gated (see evolver guardrail auto-apply fix).

### Phase Transition Contracts (architecture — revisit after operational data)

- [ ] **Formal stage contracts between pipeline phases** — Currently phase transitions are implicit: decompose outputs strings, execute takes strings, finalize takes outcomes. No typed contracts, no hard validation gates between phases. Pre-flight is advisory-only (loop proceeds regardless). Trajectory check is the first real mid-pipeline gate. Need: (1) typed output contracts per phase (not just "a list of strings" but "atomic steps that cover the goal scope"); (2) hard gates that re-plan or abort instead of proceeding with garbage input; (3) audit which existing checks are load-bearing vs noise. The Starship optimization: delete the advisory checks that never change behavior and replace with fewer, harder gates. Defer until operational data shows which gates actually matter.

### Phase 38 subpackage move

- [ ] **Phase 38 subpackage move** — src/ is flat with 49 modules. Deferred (33+ imports per group), revisit when it causes real problems.

### Isolated worktree per sub-agent

- [ ] **Isolated worktree per sub-agent** — from Alpha Batcher's breakdown of Claude Code's architecture (@alphabatcher). Each sub-agent gets its own git worktree so writes don't collide. Relevant to concurrent run safety (Phase 62 project isolation). Current `is_project_running()` + per-project lock file is a simpler version; worktree isolation is stronger. **Priority 6/10 — revisit when parallel missions are actually running.** Source: @alphabatcher.

### Harness hill-climbing as autonomous loop

- [ ] **Harness hill-climbing as autonomous loop** — @ashpreetbedi/@mr_r0b0t: use eval benchmark scores as autonomous hill-climbing signal for harness improvement (LangChain TerminalBench 2.0: 52.8→66.5% with no model change). Poe has `eval.py` + `evolver.py` but they're not wired as an autonomous feedback loop. Fix: `run_nightly_eval()` → failure trace analysis → harness proposal → evolver suggestion → `_verify_post_apply`. **Priority 6/10 — closes the verify→learn loop that's currently 80% done.** Source: @ashpreetbedi + @Vtrivedy10. (Collapsed in the eval-driven harness hill-climbing duplicates from the X-research sections.)

### Dumb loop audit (scaffolding designed to be removed)

- [ ] **Dumb loop audit (scaffolding designed to be removed)** — Alpha Batcher breakdown of Claude Code: Anthropic's deliberate "thin harness" philosophy. Each scaffold should pass the future-proof test: dropping in a more powerful model should improve performance WITHOUT requiring harness complexity changes. Run a scaffolding audit on agent_loop.py — label each check as load-bearing vs removable. Manus precedent: rebuilt agent 5× in 6 months, each rewrite removed complexity. **Priority 5/10 — strategic/architectural, no code cost.** Source: @alphabatcher/@akshay_pachaar.

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
  (≥16 GB RAM; 4-bit for 8 GB) before standardizing on one.

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

### Standing test-goal menu (future ideas)

- [ ] **Recipe site PM agent** — Recurring goal against slycrel/orchestrator-test-recipes: review code, open issues for missing features, review PRs, suggest architectural improvements. Tests GitHub integration + multi-step judgment.
- [ ] **Recipe site dev agent** — Recurring goal: pick open issues, implement on branches, open PRs, maintain running Docker instance on this machine. Tests code generation + git workflow + deployment.
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

### MCP tools registered/advertised but never dispatchable (bug)

- [ ] **MCP tool dispatch gap.** `heartbeat.py` loads configured MCP servers
  and advertises their tools into prompts, but `step_exec.py`'s tool dispatch
  has no `mcp__*` branch — falls through to "unrecognised tool: blocked."
  The execution bridges that would actually run them
  (`tool_registry.ToolRegistry.resolve_and_call`,
  `mcp_client.dispatch_mcp_call`) have zero production callers; only tests
  exercise them. An entire advertised capability is silently inert. Fix:
  wire `resolve_and_call` into step_exec's unknown-tool branch, or stop
  advertising MCP tools until it's wired. Source: refactor-plan architecture
  review (docs/REFACTOR_PLAN.md), 2026-07-02.

### `orch.py`'s tick/loop engine is legacy; its path/bookkeeping layer is not

- [ ] **Split `orch.py`'s two concerns.** Git-history confirmed: `orch.py`
  predates `agent_loop.py` by 18 days (2026-03-05 vs 2026-03-23), and its
  original docstring — "durable project state and a loop-until-blocked
  executor without arbitrary iteration limits" — is exactly the
  heuristic-decomposition design `agent_loop.py`'s first commit explicitly
  says it replaces ("LLM decomposes goal into steps, replaces dumb
  heuristic"). `run_tick`/`run_loop` are still live via `maro tick`/`maro
  loop`/`maro plan` CLI commands (`cli.py:610-660`), but no scripts/, cron,
  or heartbeat call site invokes them today — only manual CLI use, if any.
  **Action:** confirm with Jeremy whether `maro tick`/`loop`/`plan` are still
  used in practice; if not, deprecate just `run_tick`/`run_loop` (and the
  three CLI subcommands) as the superseded loop.
  **Do NOT touch the rest of the file** — `orch_root`, `project_dir`,
  `parse_next`, `projects_root`, and NEXT.md parsing (now in
  `orch_items.py`) are live, load-bearing infrastructure with 8+ current
  importers (`persona.py`, `heartbeat.py`, `telegram_listener.py`,
  `autonomy.py`, `goal_map.py`, `director.py`, `sheriff.py`,
  `build_loop_runner.py`) — this is the real `orchq`/paths subsystem, not a
  competing main loop, and should be promoted/renamed as such in the Tier 4
  subpackage move rather than deprecated. Source: refactor-plan git-history
  investigation, 2026-07-02.

### Unify fragmented web/content-fetch capability into one skill

- [ ] **Three uncoordinated fetch implementations, never unified.**
  `web_fetch.py` (generic URL fetch+strip via Jina/BS4, plus a built-in
  X/Twitter fallback chain: direct fetch → oEmbed → t.co resolve; sole
  production caller `step_exec.py:803`), `channels.py` (GitHub/Reddit/YouTube
  structured queries via raw `urllib`; docstring falsely claims these are
  "registered for agent use" — zero references in `tool_registry.py` or
  `skills.py`, only `doctor.py` pings it as a health check), and
  `orch_bridges.py`'s x-capture salvage bridge
  (`x_capture_salvage_validation_bridge`, added 2026-03-20) — the last of
  which doesn't fetch anything itself, it just reads an
  `x-capture-salvage.json` artifact written by an **external, out-of-repo**
  X-capture pipeline that doesn't exist anywhere in this repo. Not literal
  duplicated code, but three disconnected one-off builds with different
  failure modes depending on which path a goal happens to hit — this is
  plausibly the "failing left and right with webfetch or wget type calls"
  experience. No formally tracked "standard skills" initiative was found
  (grepped BACKLOG + full git log + `STEAL_LIST.md`) — this is new scoping,
  not a resumed thread.
  **Action:** consolidate into one general fetch skill registered in
  `tool_registry.py`/`skills.py` with sub-verbs — generic URL, X/Twitter
  (preserving the oEmbed→salvage-retry escalation path), and channels.py's
  platform queries — so callers stop hand-rolling fetch logic per feature.
  Register channels.py's functions as real tools or delete the false
  "registered for agent use" claim. Revisit later. Source: refactor-plan
  architecture review, 2026-07-02.

### Ancestry double-injection: two disagreeing lineage sources in the loop prompt

- [ ] **`agent_loop.py` injects ancestry twice per loop from two independent,
  potentially-disagreeing sources.** `ancestry.py`'s `build_ancestry_prompt()`
  reads the per-project `ancestry.json` chain; `recall.py`'s
  `_resolve_thread()` independently walks a *different* data source (run
  metadata `origin`) to build its own ancestry string. `recall.py`'s own
  docstring admits the gap: "goal-brain injection + correspondence walk are
  still future work." No single source of truth exists today. See the new
  "Goal Lineage" section in `docs/ARCHITECTURE_OVERVIEW.md` for the full
  four-mechanism map (`ancestry.py`, `goal_map.py`, `thread_brain.py`,
  `recall.py`). **Action:** have `recall.py`'s `_resolve_thread` call into
  `ancestry.py`'s chain instead of independently re-deriving it; wire
  `thread_brain.py`'s per-thread origin to also write/consult
  `ancestry.json` at thread-fork time as Thread Architecture matures. Source:
  refactor-plan architecture review, 2026-07-02.

### Observability dashboard — archived, revisit the visibility goal with a different implementation

- [ ] **Dashboard archived 2026-07-02, underlying goal still open.** The
  stdlib-HTTP `maro-observe serve` dashboard (BACKLOG_DONE.md "Dashboard as
  real tool" / "Replay with factory mode" / "Dashboard captain's log panel",
  now flagged needs-revisited) was moved to `archive/observe_dashboard.py` —
  Jeremy's call: "proof of concept that sort of failed." Original intent
  (still valid, worth pursuing differently): give an end user both a
  high-level view of what the orchestrator is doing and visibility into the
  detailed work being done on their behalf. What made this implementation
  fail: grew into an unauthenticated ~950-line stdlib http.server bound to
  0.0.0.0 by default, mixing read-only observability with a live
  goal-submission/replay control surface. `maro ancestry` CLI is the
  surviving visibility primitive in the meantime (see "Goal Lineage" in
  `docs/ARCHITECTURE_OVERVIEW.md`). Revisit with a fresh design — likely
  needs auth, a narrower read-only default, and a decision about whether
  the goal-submission/replay controls belong in the same surface at all.
  No urgency; needs product discussion first. Source: refactor-plan review,
  2026-07-02.

---

## Stale — dropped this triage

Titles deleted as obsolete (auditable; full history in git):

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
