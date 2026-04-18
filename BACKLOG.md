# Backlog — Deferred Items, Ideas, and Known Issues

Single canonical location for everything we've identified but haven't done yet.
Read this at the start of every session. Update it as items are completed or new ones emerge.

**Completed items live in [BACKLOG_DONE.md](BACKLOG_DONE.md)** — move items there with their full context when they ship; that file is the archive of what we've already decided, tried, or superseded, and it's ingested by `dev-recall` for historical context.

Last reviewed: 2026-04-16 (session 34 — split into active + done).

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

### Introspect-sees-no-action: `decomposition_too_broad` (and siblings)

- [ ] **`decomposition_too_broad` fires but nothing acts on it.** Full slycrel-go run (2026-04-16, loop `85ac29ee-*`) completed with introspect warning `decomposition_too_broad` logged and then ignored — loop continued, shipped 2 commits, closure never consulted the warning. This is the shape of self-improvement theater: the system knows something is off and does nothing. Frame this honestly as "not yet handled" rather than "working as designed."

  **What should happen (candidates, unordered, not committed):**
  - a warning of this severity should at minimum surface as a captain's log event the orchestrator can react to (not just a log line)
  - should gate or influence decompose: retry with tighter bounds, or demand explicit scope acknowledgement of the breadth
  - should decrement closure confidence or force an extra behavioral probe — breadth without coverage is precisely where slycrel-go regressions survived
  - related introspect signals likely have the same shape (surfaced, ignored). Audit them together: grep `introspect` in agent_loop.py + introspect.py for "warn"/"observation" emissions and catalog which ones actually change behavior vs which ones are log-only.

  **Evidence:** `~/.poe/workspace/projects/for-this-project-httpsgithubcomslycrelslycrelgo-id/artifacts/loop-85ac29ee-*/` — the warning is visible in the loop log, ends up with no handler. Same run also had scope parse failure silently discarding the raw LLM response (fixed 2026-04-16) — these are sibling observability gaps.

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
