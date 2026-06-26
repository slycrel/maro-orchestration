# Backlog — Deferred Items, Ideas, and Known Issues

Single canonical location for everything we've identified but haven't done yet.
Read this at the start of every session. Update it as items are completed or new ones emerge.

**Completed items live in [BACKLOG_DONE.md](BACKLOG_DONE.md)** — move items there with their full context when they ship; that file is the archive of what we've already decided, tried, or superseded, and it's ingested by `dev-recall` for historical context.

Last reviewed: 2026-06-24 (full triage + reorg; follow-up code-verified audit moved 2 silently-completed items — closure inconclusive-probe handling, handle.py prefix registry — to BACKLOG_DONE.md. Then shipped the persistence-install guardrail (was BLOCKER #3) and moved it to BACKLOG_DONE.md; stack renumbered 1–12).

---

## Actionable Stack

Ordered open work that matters. Top of the list is next.

### 1. Bound worker writes to run-dir / workspace (artifacts leaking into repo root)

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
- [ ] **Diagnostic: detect scavenging.** Captain's log event when a worker
  reads a file outside the project workspace root. Cheap instrumentation,
  makes contamination visible.

Not ambitious; the goal is "constraint to a folder isn't a bad option to
have" not "build a sandboxing subsystem."

### 2. Rate-limit recovery: no total-backoff cap + phantom Step -1

- [ ] **Rate-limit recovery has no total-backoff cap; recovery path emits phantom `Step -1`.** Scope A/B run-06-control (2026-04-23, `~/.maro/experiments/scope-ab-2026-04-22/run-06-control/`) hit 6 rate-limit retries with exponential backoff (60→120→240→480→960→1800s = 61 min total wall-clock in backoff alone). Per-attempt cap is enforced; **total-backoff-wall-clock is not.** After step 20 finally completed, the recovery path fired with `recovery[NEEDS-REVIEW] risk=medium: Retry with smaller step scope or switch to API adapter` — and produced a `Step -1` marker that the main loop doesn't know how to handle. Run exited rc=1 with no closure verdict. Total runtime: 2h30m for 20 completed steps.

  **Candidates:**
  - ~~cap total backoff wall-clock at ~10 min; if exceeded, bail cleanly (soft-fail with "rate-limited, retry later" rather than another 30-min sleep)~~ **DONE 2026-06-24** — `llm.py` subprocess rate-limit loop now tracks cumulative sleep and bails before the next sleep would exceed `POE_CLAUDE_RATE_LIMIT_TOTAL_CAP` (default 600s). Soft-fails with a "bailed after Ns of backoff … retry later" RuntimeError. `=0` disables (falls back to retry-count). Tests in test_llm.py.
  - recovery path should trigger an actual replan (fewer steps, smaller scope) or adapter switch, not a phantom `Step -1` ordinal
  - while in rate-limit backoff, pause the cost meter or at least annotate "backoff-idle tokens=0" — run-06 showed $41 cost accumulating during 61 min of no real work (cost meter is probably accumulating during retries before the API call; worth auditing)

  **Related:** `decomposition_too_broad` miscalibration (now archived). Both are recovery-layer bugs that only surface on long plans.

### 3. Stream-json token visibility

- [ ] **Stream-json token visibility (next up, per Jeremy 2026-04-18).**
  `claude -p --output-format stream-json` emits newline-delimited JSON
  events (init, assistant chunks, tool calls, result). Switch the
  subprocess adapter + `/tmp/maro-current-step.log` to this mode so
  operators see live tokens instead of 0 bytes until burst-at-end. Each
  new line becomes the primary liveness signal; CPU-activity demotes to
  a fallback for adapters that don't stream. Parser work: line-reader
  that accumulates assistant deltas + handles tool_use events + parses
  the final `result` event for usage stats. Tests need fake streaming
  fixtures. Size: ~half-day. Coordinates with the adapter protocol
  extraction (stream shape is the point of the Adapter interface).

### 4. `_is_complex_directive` threshold for NOW-lane misrouting

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

### 5. Closure restart short-circuit (artifact exists + verifier passed)

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

### 6. NEXT.md ↔ git activity sync at closure

- [ ] **NEXT.md ↔ git activity sync.** Control's NEXT.md showed steps 6–8 unchecked while the repo had matching commits. Either NEXT.md updates lag, or the agent didn't reflect the work back. Either way: closure should compare claimed-done against repo activity and surface the divergence.

### 7. Extend local-validator ladder to post-loop quality gate

- [ ] **Extend the ladder to the post-loop quality gate.** Same local-first pattern
  for `quality_gate.run_quality_gate` / `run_llm_council` (3-persona trio) escalation,
  reusing the `WEAK_ESCALATE` decision state. (verify_step done; quality_gate pending.)

### 8. Captain's-log event-type registry integrity

Surfaced by the 2026-06-24 inventory that produced `docs/CAPTAINS_LOG_EVENTS.md`.
Two drift classes, both cheap to fix:

- [x] **3 emitted-but-unregistered events.** ~~`EVOLVER_REVERTED` (evolver.py:664),
  `EVOLVER_VERIFY` (evolver.py:2072), `PLAYBOOK_UPDATED` (playbook.py:235) fire in
  production via string literals not in `captains_log.EVENT_TYPES`.~~ **DONE
  2026-06-24:** added the 3 constants + registered them in `EVENT_TYPES`, switched
  emitters to the constants, bumped the count-guard test (49→52) + added a
  membership test.
- [ ] **3 defined-but-unemitted events.** `CANON_CANDIDATE`, `LESSON_RECOVERED`,
  `SKILL_REWRITE` are in `EVENT_TYPES` but nothing emits them. `SKILL_REWRITE` is
  worse — it's referenced by consumers (`recall.py:54`, `evolver.py:995`) yet never
  produced (dead expectation). Either wire the emitter or remove the constant +
  consumer references. (CANON_CANDIDATE / LESSON_RECOVERED map to the known Stage
  2→3 crystallization gaps — may be intentionally-pending rather than dead.)

### 9. Local-validator measurement — token/cost delta report

- [ ] **Token/cost delta report.** Quantify tokens saved vs escalation rate vs added
  latency, on Poe's own task corpus — the actual ROI of running this.

### 10. Local-validator measurement — tune `local_max_tokens` per model

- [ ] **Tune `local_max_tokens` per model.** Live finding (2026-06-21 verify run):
  VibeThinker's `<think>` trace on *real* (long) step results overran the 1024
  floor → empty content → conf 0.00 → spurious escalation on 2/5 steps (the other
  3/5 validated free at conf 1.00). Bumped default to 2048; deep-eval should find
  the floor that maximizes decisive-local rate without wasting generation latency.

### 11. Spend-gated transparency mandate

- [ ] **Spend-gated transparency mandate.** Define a threshold (e.g., $2 estimated spend) above which the full source/build/artifact bundle is mandatory and visible to the user without grep. Below that, current behavior is fine.

### 12. M5 portability final sweep

- [ ] **M5 portability final sweep** — codex-side payload check decision (deferred) + final sweep (per GOAL_BRAIN active thread).

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

### No-file-claim fabrication (parked — backend changes required)

- [ ] **No-file-claim fabrication.** A run that fabricates a result naming no
  path at all ("ran the tests: 142 passed", writing nothing) leaves no
  deterministic trace — `claude -p --output-format json` returns only final
  text, no tool-call transcript (investigated 2026-06-24 and ruled out). Would
  require `--output-format stream-json` parsing (re-plumb the subprocess
  pipeline) or a filesystem-snapshot diff with a fabrication-shape classifier.
  Out of proportion to the risk for now; revisit if it shows up organically.

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
