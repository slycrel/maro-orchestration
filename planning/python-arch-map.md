# Maro Python Architecture Map — input for the Go spiritual-successor planning session

*Written 2026-08-28 against `/home/clawd/claude/maro-orchestration/` (mainline, ~183 modules
in `src/`). Method: started from CLAUDE.md, `docs/ARCHITECTURE_OVERVIEW.md`, and the five
`skills/arch-*.md` files, then verified claims against code (targeted reads of handle.py,
loop_*.py, step_exec.py, llm.py, memory.py, config.py, loop_artifacts.py, runs data) and a
derived AST-level import graph aggregated to subsystem level. Every load-bearing claim names
its module. This is synthesis for planning, not inventory — the goal is patterns and mesh,
not implementation detail.*

---

## 1. Subsystem map with real boundaries

The repo claims 5 subsystems (CLAUDE.md, ARCHITECTURE_OVERVIEW.md). The claim holds up as an
*organizing* truth but not as a *structural* one: all 183 modules are flat in `src/`, and the
subsystem assignment below is mine (seeded from the arch-skills' file maps). Roughly:

| Subsystem | Core modules (verified live) | Entry points |
|---|---|---|
| **Interface** | handle.py (~4300 lines), intent.py, director.py (~2150), workers.py, persona.py, persona_dispatch.py, rerun_identity.py, conductor.py, mission.py, handle_queue.py, dispatch_envelope.py | `maro-handle` / `python3 -m handle "<goal>"`, `maro-enqueue`, telegram_listener/slack_listener |
| **Core loop** | agent_loop.py (facade) + loop_types/init/planning/parallel/execute/blocked/post_step/finalize/artifacts.py, step_exec.py, planner.py, pre_flight.py, navigator*.py, scope.py, checkpoint.py, interrupt.py, constraint.py | `run_agent_loop()` (also `maro-run`) |
| **Memory/Knowledge** | memory.py, memory_ledger.py, knowledge_web.py (~1630), knowledge_bridge.py, knowledge_lens.py, recall.py, playbook.py, captains_log.py, ancestry.py, lesson_provenance.py, mint_grounding.py, pack.py | `reflect_and_record()`, `recall()`, `maro-memory`, `maro-knowledge` |
| **Quality/Self-improve** | inspector.py (~2065), evolver.py (~3265) + evolver_scans/store, graduation.py, introspect.py, quality_gate.py, skills.py + skill_types.py, claim_probe.py, eval.py, shadow_lane.py | evolver via heartbeat cadence; quality_gate via handle; `maro-inspector`, `maro-introspect` |
| **Platform** | llm.py (~3900 lines w/ adapters), config.py, runs.py, run_curation.py, metrics.py, heartbeat.py, orch_items.py, task_store.py, file_lock.py, notify.py, web_fetch.py, container_exec.py, observe.py, cli.py | `maro` CLI (30+ console scripts in pyproject.toml), `maro heartbeat` |

### The import matrix (derived, module-level import statements aggregated by subsystem)

```
importer ↓ / imported →   interface  coreloop   memory   quality  platform
interface                     25        17        22        7        72
coreloop                       9        88        47       23       154
memory                         2         3        60        8        79
quality                        8        10        34       29        95
platform                      17        35        32       28       198
```

What the matrix actually says:

- **Platform is the true hub.** Every subsystem leans on it heavily (72–154 inbound deps
  each). Top fan-in modules: `llm` (54 cross-boundary importers), `config` (53),
  `file_lock` (36), `runs` (35), `orch_items` (33), `llm_parse` (32), `context_budget` (30).
  These seven are the de-facto kernel API. A Go design should make them explicit packages
  with real interfaces — they already behave like one.
- **`captains_log` (35 cross-boundary importers) is nominally "memory" but functions as
  platform** — it's the append-only event bus everything stamps into. Treat it as
  infrastructure (an event log), not as memory.
- **Memory is nearly a leaf.** It imports almost nothing from interface/coreloop/quality
  (2/3/8) — data flows *into* it through function calls, not imports. This is the cleanest
  boundary in the system and the easiest to port as a standalone package.
- **Interface and coreloop are the big cross-cutters.** Top fan-out modules: `handle` (36
  cross-subsystem imports), `cli` (27), `director` (24), `loop_finalize` (23),
  `step_exec`/`loop_post_step` (17), `loop_execute` (16), `evolver` (15). handle.py is not
  an entry point so much as the *orchestrator of everything* — lane routing, closure,
  quality gate, learning drain, notify all live inline in `_handle_impl` (~2700 lines of
  one function's control flow). This is the single biggest thing a Go redesign gets to fix.
- **Platform→interface backflow (17)** is mostly cli.py/heartbeat.py calling handle/conductor
  — acceptable "app layer" edges, but llm.py also lazily imports runs (`record_llm_call`),
  a deliberately one-way seam documented in arch-platform.md ("runs does not import llm").
- Circular-import pressure is real and Python-shaped: lazy `from x import y` inside function
  bodies is pervasive (handle.py does it hundreds of times), and `skill_types.py` exists
  solely to break an skills↔evolver cycle. In Go the import graph must be designed up front
  — this codebase is the cautionary tale (memory L48/P4: the import graph is observable
  behavior).

### What crosses each boundary (the edges that matter)

- interface → coreloop: `run_agent_loop(goal, **kwargs)` → `LoopResult`. One fat call.
- coreloop → memory: `recall()` at loop start (injection slices), `reflect_and_record()` /
  `extract_step_lessons()` / `record_step_trace()` at finalize, decision-directive
  `record_decision()`. Data: goal text, StepOutcomes, verdicts, lesson dicts.
- interface → memory: `stamp_outcome_verdict()` (closure verdict onto the already-written
  outcomes row — tri-state, post-hoc), rerun_identity reads `memory/handle_inputs.jsonl`.
- quality → memory: evolver reads outcomes.jsonl/task_ledger, writes lessons/change_log;
  graduation reads diagnoses.jsonl. Quality is a *consumer of memory files* more than of
  memory APIs — several quality modules parse the JSONL directly.
- everything → platform: `build_adapter()`/`adapter.complete()`, `config.get()`,
  `locked_append()`/`atomic_write()`, run-dir helpers in runs.py, `notify.emit()`.
- inspector ↔ evolver: **famously share almost no data structures** (documented gap,
  ARCHITECTURE_OVERVIEW §4) — inspector emits friction signals, evolver reads outcomes;
  they communicate through the filesystem, not through types.

---

## 2. The backbone (primary execution route, verified in code)

```
goal (CLI / telegram / queue)
 → handle.handle() → _handle_impl()                       [handle.py:807/1096]
    prefix parse (_PREFIX_REGISTRY, _apply_prefixes)
    intent.classify()                                      [1 LLM call; deterministic
                                                            link-triage shortcut + heuristic fallback]
    ├─ NOW lane: _run_now() [handle.py:353]
    │    web_fetch.enrich_step_with_urls (Jina/X pre-fetch → disk)
    │    single no_tools adapter.complete() → self-verify (_verify_now_outcome)
    │    record_outcome (verdict at record time) → close_run → report
    └─ AGENDA lane:
         rerun_identity brief (reads memory/handle_inputs.jsonl, deterministic)
         clarity check / BLE rewrite / completion-standard injection [LLM calls]
         director plan-delegate-review (USUALLY SKIPPED via skip_if_simple)
         run_agent_loop()                                  [handle.py:2486]
           A loop_init:      budget gate (metrics.spend_today, p90 breaker),
                             build_adapter (llm.py failover chain), project dir
                             (orch_items), ancestry load
           B loop_planning:  planner.decompose() [LLM] — prompt carries recall
                             slices: skills, tiered lessons, standing rules,
                             decisions, playbook, knowledge nodes, user CONTEXT.md
           C pre_flight:     cheap plan critique [LLM, own adapter, never subprocess]
           D loop_parallel:  ThreadPoolExecutor fan-out + git-worktree isolation
           E prepare:        step shaping, plan manifest write
           F loop_execute._execute_main_loop: per step →
               step_exec.execute_step(): ONE adapter.complete() with
                 EXECUTE_SYSTEM + EXECUTE_TOOLS; the response IS a tool call:
                 complete_step | flag_stuck | tool_search | register_tool |
                 schedule_run | create_team_worker | registered runtime tools
               (real file/shell work happens INSIDE subprocess adapters —
                claude -p / codex spawn a full inner agent; tool_events on
                LLMResponse are the ground-truth receipt)
               blocked → loop_blocked._handle_blocked_step (_BlockDecision:
                 retry-with-hint / split / terminal; navigator escalate override)
               loop_post_step: budget ceiling, ralph verify [LLM opt],
                 tier escalation cheap→mid→power, artifacts write,
                 director_evaluate mid-loop supervision (config-gated),
                 mid-loop ctx.channel.ask() escalation to the human
           G loop_finalize:  memory.reflect_and_record() [LLM lesson extraction]
                             → outcomes.jsonl + medium/lessons.jsonl;
                             diagnosis/lenses/recovery lessons; per-run scans;
                             run_skill_maintenance (promotions, adjudications)
         → LoopResult back in handle:
         director.evaluate_closure()                       [handle.py:2685]
           — generates 2–5 shell probe commands [LLM], RUNS them (exit codes),
             interprets [LLM]; non-fatal by design (any exception → complete)
         stamp_outcome_verdict() — tri-state done≠achieved onto outcomes row
         quality_gate.run_quality_gate() [multi-pass LLM review, mostly passes 1–2]
         runs.close_run() → run_curation.curate_run() → run_card.json + reports
         notify.emit("run_completed", run_card)            [USER HEARS RESULT HERE]
         THEN _drain_deferred_learning()  — answer-first decree: learning is
           bookkeeping and never sits between the answer and the user
         finally: knowledge_web.maybe_consolidate()  — the "dream cycle"
           (decay/promote/GC-to-archive, ≤1/day, in-process, no daemon)
```

**Key data structures on the route:** goal string + prefix flags → ResolvedIntent /
Deliverable (intent resolution) → `LoopContext` (mutable state bundle: loop_id, project,
adapter, steps, token totals — passed to every phase fn) → `StepOutcome` (index, text,
status, result, confidence, tokens) → `LoopResult` (steps, status done/stuck/interrupted/
error, totals) → `HandleResult` → run_card dict. LLM contract: `Message`/`ToolCall`/
`LLMResponse` (llm.py:273–345 — content, tool_calls, tokens incl. cache split, cost_usd,
backend, tool_events, session_id).

**Where the side effects are:** LLM calls at ~12 distinct sites on the happy path (classify,
decompose, pre-flight, per-step execute, ralph verify, blocked decisions, closure
generate+interpret, quality-gate passes, reflection) — every one through
`adapter.complete()`, every successful one recorded by FailoverAdapter to
`<run-dir>/build/calls/call-NNNNN.json`. Subprocess execution at: subprocess LLM adapters
(the big one), director closure probes, claim_probe's guarded read-only commands, worktree
git ops, container_exec docker lane. Filesystem writes at: run dir (metadata.json,
build/calls/, build/trace.jsonl, run_card.json, artifact/, source/), project dir (NEXT.md,
DECISIONS.md, artifacts/loop-<id>-step-NN.md, plan manifest, loop-<id>-log.json — writers in
loop_artifacts.py), and ~40 JSONL stores under `memory/` (all via file_lock.py helpers).

---

## 3. External boundaries and data contracts (the compat surface)

These are where a Go engine could run against the same workspace and be compared black-box.
(This already happened once: 2026-08-26, 6 read-only renderers over the live workspace,
Python vs Go, 6/6 identical.)

### 3.1 Workspace layout (`~/.maro/workspace/`, resolved by `config.workspace_root()`)

Verified against the live workspace (805 run dirs, 204 projects):

| Path | Format | Written by |
|---|---|---|
| `memory/*.jsonl` (~40 files) | JSONL, one record/line, append via `locked_append` | see below |
| `runs/<8hex>-<nickname>/` | run dir | runs.py lifecycle |
| `  metadata.json` | `{handle_id, nickname, prompt, lane, model, started_at, ended_at, status}` — authoritative evidence | runs.py (atomic replace) |
| `  run_card.json` | classification (success/done-not-achieved/done-unverified/partial/failed) + mineable inventory | run_curation.curate_run |
| `  build/calls/call-NNNNN.json` | `{seq, backend, model, prompt, response, tool_events, tokens_in/out, purpose, cost_usd, error, ts}` — secret-scrubbed via secret_scrub.py | FailoverAdapter (record-mode, default ON, `MARO_RECORD=0` off) |
| `  build/trace.jsonl`, `captains_log_slice.jsonl`, report .html | run-local views | loop/report writers |
| `  artifact/`, `source/` | step products; fetched raw bytes (artifacts-over-streams) | step exec, web_fetch |
| `.run-ref-index-v2/` | hashed-leaf run-reference index — derived, disposable | runs.py |
| `projects/<slug>/` | NEXT.md (regex todo grammar `[ ]/[x]/[~]/[!]`), DECISIONS.md, RISKS.md, artifacts/ | orch_items, loop_artifacts |
| `playbook.md` (+ `playbook_history/`) | director wisdom, append + curated compress | playbook.py, evolver |
| `skills/`, `personas/` | .md overlays that WIN over repo copies (resolution: workspace → repo) | evolver, skills-lite promotion |
| `config.yml` | workspace-tier config | operator |
| `checkpoints/`, `run/` (pidfiles), `state/`, `output/` | resume data, daemon singletons, misc | checkpoint.py, proc_lock.py |

Key memory stores (schema owner in parens): `outcomes.jsonl` (memory.record_outcome; the
`goal_achieved` key is TRI-STATE — present True/False = judged, ABSENT = unjudged, never
null), `medium/lessons.jsonl` + `long/lessons.jsonl` (TieredLesson: score, confidence,
sessions_validated, provisional, contested, scope, evidence_sources, merged_variants —
decay is a READ-TIME derivation, never persisted), `standing_rules.jsonl`,
`decisions.jsonl`, `captains_log.jsonl` (full event contract in
`docs/CAPTAINS_LOG_EVENTS.md`), `task_ledger.jsonl`, `knowledge_nodes/edges.jsonl`,
`skills.jsonl`, `step-costs.jsonl` (metrics), `handle_inputs.jsonl` (intake identity),
`events.jsonl` (notify), `diagnoses.jsonl`. Backend is pluggable in principle
(memory_backends.py: JSONL default, SQLite alternate via `MARO_MEMORY_BACKEND`) but JSONL
is what production runs.

### 3.2 Config resolution

Two-tier YAML: `~/.maro/config.yml` (user) ← `~/.maro/workspace/config.yml` (workspace,
wins), nested dicts merged one level deep; priority env var > config > hardcoded default;
accessed as `config.get("dotted.key", default)` (config.py). `MARO_WORKSPACE` pins the
workspace root — the ONE env contract worth honoring in Go (the other legacy pins are scar
tissue, §4). Secrets from env or `<workspace>/secrets/.env`. `docs/DEFAULTS.md` is the
defaults registry (tripwire-tested).

### 3.3 LLM adapters (llm.py)

- Uniform interface: `adapter.complete(messages, tools=, tool_choice=, max_tokens=, ...) →
  LLMResponse`. Model tiers are symbolic: `MODEL_CHEAP/MID/POWER` ("cheap"/"mid"/"power"),
  mapped to native IDs per backend.
- `build_adapter()` walks `model.backend_order` (config) or
  `DEFAULT_BACKEND_ORDER = [anthropic, subprocess, openrouter, openai]` — first usable wins;
  `FailoverAdapter` wraps the chain, retries with backoff, and is the SINGLE capture seam
  for record-mode.
- Backends: AnthropicSDKAdapter, ClaudeSubprocessAdapter (`claude -p`), CodexCLIAdapter,
  OpenAICompat family (OpenRouter/OpenAI/XAI/Groq/Gemini — the last three opt-in only, not
  in the default order). **The subprocess adapters are the deep idea:** an agentic CLI is
  just another completion backend, and its inner Bash/Write/Read actions come back as
  `tool_events` — ground truth for "did it really write Y". This box runs
  `backend_order: [subprocess, anthropic]`.
- Agentic-cwd contract: subprocess work lands in `kwargs["cwd"] or` a run-scoped ContextVar
  set by run_agent_loop / handle — deliberately not reset on loop exit so post-loop
  verifiers inherit the project dir (arch-platform.md calls it load-bearing).

### 3.4 Subprocess / container / network

- container_exec.py: optional docker lane for worker execution (probes, auth volume, image
  contract); off/on via `executor.container` config.
- web_fetch.py: Jina Reader + authenticated X fetching; fetch-to-disk-first per the
  artifacts decree (`sources/` in the run dir). channels.py: GitHub/Reddit/YouTube read
  channels. telegram_listener.py / slack_listener.py: inbound goal channels.
- notify.py: the substrate hook — every emit appends to `memory/events.jsonl`; configured
  `notify.command` gets payload JSON on stdin + `MARO_EVENT_TYPE/HANDLE_ID/STATUS/RUN_DIR`
  env for `run_completed`/`escalation`. Pull-based + in-lifecycle by decree — no daemon.
  `docs/SUBSTRATE_INTEGRATION.md` is the contract. This is exactly the seam a Go engine
  must reproduce for drop-in substrate compatibility.
- Process/locking contracts (cross-process, so cross-*engine* too): flock admission gate
  `memory/loop-<project>.lock` (one run per project), `file_lock.py` fail-closed flock +
  atomic_write on every shared file, proc_lock pidfiles in `run/`. If Python and Go engines
  ever share a live workspace, they must share these lock conventions.

---

## 4. Load-bearing patterns vs incidental complexity — MY JUDGMENT

### The soul (carry forward, possibly in stronger form)

1. **Artifacts over streams** (decree 2026-07-27, ARCHITECTURE_OVERVIEW). Context is a
   derived view over durable artifacts; a cap must never destroy the only copy; remote
   fetches land on disk first. This is the system's deepest data-flow principle and it's
   cheap to make structural in Go (typed artifact store; views are functions of it).
2. **Record-mode at one seam.** Every LLM call captured (prompt, response, tool_events,
   tokens, cost) through the single FailoverAdapter chokepoint → the whole replayability
   tier falls out of one design decision. In Go: make the recorder part of the adapter
   interface from day one.
3. **Substrate-agnostic adapter hierarchy with symbolic tiers** + subprocess-agents-as-
   backends + tool_events as execution receipts. This IS the substrate-agnostic vision made
   code, and it's what let the same framework ride API models, claude CLI, codex, and
   hosted-free models.
4. **done ≠ achieved, and verdicts are tri-state.** The loop's "done" means "ran out of
   steps", closure (director.evaluate_closure, real exit codes not vibes) is the only
   sign-off, and an unjudged row omits the verdict key rather than defaulting. Paired with
   positive-evidence grounding (claim_probe executes reviewer commands; lesson mints carry
   run receipts; provenance stamps like `minted_from` quarantine prompt-derived lessons).
   These semantics should be *types* in Go, not conventions.
5. **Fail-open observability, fail-closed data.** Every learning/curation/notify/capture
   hook is best-effort and can never break the answer path; every shared-file write is
   locked and atomic and a lock timeout is a loud error. That asymmetry (observability
   fails open, ledgers fail closed) is deliberate and correct.
6. **Answer-first delivery.** notify fires with the run card *before* deferred learning
   drains (handle.py `_POST_NOTIFY_LEARNING`). Product decision, cheap to keep, easy to
   lose in a rewrite.
7. **Escalation ladders everywhere**: model-tier escalation on failure streaks, budget
   caps as circuit-breakers (auto-derived from p90 of successful runs, warn line, runaway
   multiplier) — caps are breakers, never truncators (decree). The *shape* — cheap first,
   escalate on evidence, hard-stop with an honest message — recurs in ~5 places and is a
   pattern worth one Go abstraction.
8. **Two-lane intake + prefix registry.** NOW (single-shot, no ceremony) vs AGENDA (full
   pipeline), with magic prefixes as behavior mutators. Cheap, legible, battle-tested.
9. **The lesson lifecycle *invariants*** (not the mechanism): never delete data (archive +
   graveyard resurrection), decay trust not data, decay computed at read time, promotion
   only on confirmed re-observation, contested/provisional flags exclude from injection but
   preserve the record, one owner per merge rule. The Python *implementation* of these is
   heavily scarred (see below); the invariants are the product.
10. **Workspace as the interface.** Skills/personas resolution workspace-over-repo, the
    self-evolved overlay winning over shipped defaults, everything inspectable as files.
    This is what makes black-box comparison possible at all.

### Scar tissue / Python-shaped workarounds (do NOT carry forward)

- **The flat 183-module `src/` and the loop_* physical split.** agent_loop was a monolith,
  split by refactor emergency into 10 loop_*.py files with a facade re-exporting privates;
  handle.py is ~4300 lines with one ~2700-line function; lazy in-function imports everywhere
  to dodge cycles; skill_types.py exists only to break skills↔evolver. Go gets packages and
  a designed import DAG on day one.
- **handle.py as god-orchestrator.** Lane routing, restart re-entry, closure, verdict
  stamping, quality-gate escalation re-runs, learning drain, consolidation all inline. The
  Go successor should make the post-loop pipeline (closure → verdict → gate → curate →
  notify → learn) an explicit, testable stage list — the Python code *describes* that
  pipeline but only in comments and ordering.
- **Workspace routing env-var archaeology.** `MARO_WORKSPACE` vs `OPENCLAW_WORKSPACE` vs
  `WORKSPACE_ROOT` vs `MARO_ORCH_ROOT`-alone, `orch_root()` vs `data_root()` vs
  `repo_root()`, prototype-shape fallbacks (arch-platform.md §Workspace Routing — an entire
  section of accreted resolution rules). Go: one workspace flag, one code root, done.
- **Dual/tri-written memory stores.** Flat `lessons.jsonl` + tiered medium/long with
  dual-written shared-id rows, `memory_ledger` mirroring flags into the flat ledger,
  4 disagreeing ancestry mechanisms (ancestry.json chain, goal_map, thread_brain,
  recall's independent metadata walk — injected TWICE per loop from 2 sources; documented
  known gap). Go should pick one lesson store and one lineage source.
- **ContextVar run-scoped state** (current run dir, subprocess cwd deliberately not reset).
  Load-bearing but a landmine — in Go, pass an explicit RunContext.
- **The knowledge_web mechanism accretion.** ~1630 lines where every rule (novelty boost,
  reinforcement caps, contested freeze semantics, refight tri-outcome, variant absorption,
  unparseable-row quarantine, destination-first moves) is a patch layered on JSONL
  read-modify-write. The invariants (above) are gold; the mechanism should be redesigned
  around a store that gives atomic multi-row updates natively.
- **Vestigial / near-dead (honest uncertainty noted):** `gateway.py` (OpenClaw WebSocket
  bridge — OpenClaw shut down 2026-07-16; imported only by cli) — dead. `observe.py`'s
  dashboard sibling archived (`archive/observe_dashboard.py`); observe.py itself lives on
  as event writer/snapshot CLI. `verification_outcomes.jsonl` retired 2026-08-08 (dead at
  both ends). `router.py` (Phase 17 logistic-regression skill router) — live-ish, used by
  evolver/skills, but the goal-aware retrain is explicitly gated on data that doesn't
  exist yet. `factory_minimal.py`/`factory_thin.py` — experiment lanes (scaffolding
  ablation A/B), not product. `memory_sqlite/memory_port/memory_backends` — bake-off seam,
  JSONL still production. `compact_ab.py`, `lens_ablation.py`, `backend_benchmark.py`,
  `delta_replay.py` — experiment instrumentation; valuable as *method*, not as ported code.
  Much of the quality subsystem's long tail (shadow_lane, validation_shadow, navigator A/B
  machinery) is research harness around the product, gated OFF by default; port the
  product, keep the harness pattern.
- **Over-engineering candidates (judgment, arguable):** the 5-pass quality gate where in
  practice "most runs only get passes 1–2"; 30+ console-script entry points; the run-ref
  hashed-leaf index (v1 AND v2 both present in the live workspace); milestone-expansion /
  march-of-nines / thinkback style one-arc features whose config gates default OFF. Each
  earned its existence from a real incident, but a successor should re-derive which ones
  the *Go* system needs from evidence, not port the full set.

### The documented intent-vs-implementation gaps that planning must respect

(From the arch skills, verified still claimed as of now; these are where "port the vision"
and "port the code" diverge.)

1. **Crystallization stages 2→3 and 4→5 don't exist.** Lesson→canon promotion is spec'd
   (10+ applies, 3+ task types) but uncoded (a `promote_canon_lesson` door shipped
   2026-08-13, Δ-gate-aware — the path exists now but is young); skill→hardcoded-rule is
   conceptual only. The Go design gets to decide whether the 5-stage ladder is real
   architecture or aspiration.
2. **Director is mostly bypassed** (`skip_if_simple=True`): the plan→delegate→review
   hierarchy exists, is tested, and is rarely exercised in anger. Its *closure check* and
   *adaptive supervision* roles (evaluate_closure, director_evaluate) are the parts that
   actually run. Consider: in Go, is "director" one component or three (planner-reviewer,
   closure judge, mid-loop supervisor)?
3. **Captain's log writes 11K+ events, reads coarsely.** The event taxonomy is excellent;
   targeted retrieval over it never got built.
4. **Inspector and evolver don't share data structures** — the detect→propose edge of the
   self-improvement loop is filesystem-coupled prose.
5. **Self-improvement is ~90% infrastructure, loop not organically closed.** Auto-apply +
   post-apply pytest verification work; graduation behavioral verify/demote works; but
   lesson extraction was *silently dead for months* (safe_list bug, fixed 2026-06-11) and
   the finalize-phase reflection block was dead 6 weeks from a swallowed NameError. Lesson
   for Go: fail-open hooks NEED liveness tripwires (the Python answer: pyflakes sweeps,
   census tests, `docs/DEFAULTS.md` registry, pin tests — port the *tripwire discipline*).
6. **Always-on is optional, not default** — heartbeat is one-shot (`maro heartbeat`) hooked
   to the host's scheduler; autonomy (drains, evolver) is an explicit switch. This is now
   posture, not gap: "Maro is an app, not a daemon."

---

## 5. Seams for black-box testing (Go-vs-Python comparison surface)

Already in the tree, verified:

- **`handle(dry_run=True)` → `_DryRunAdapter`** (loop_init.py:551): an in-production
  deterministic fake — pattern-matches decompose prompts to canned steps and
  `tool_choice="required"` to a canned `complete_step`. The whole pipeline runs, zero
  network.
- **`ScriptedAdapter`** (tests/regression/test_regression.py, shared with
  test_e2e_smoke.py): programmable response sequences driving `handle()` end-to-end —
  golden-path scenarios A–G (NOW happy path, 3-step AGENDA, prefixes, stuck→partial). This
  is the exact shape of a cross-engine harness: same scripted responses into both engines,
  diff the workspace after.
- **Record-mode captures as replay corpora**: `build/calls/*.json` holds exact
  prompt/response pairs for 800+ real runs; `delta_replay.py` and
  `tests/fixtures/orchestration_corpus/` already mine them. A canned backend that serves
  responses from a captured run replays reality.
- **The workspace IS the observable.** `MARO_WORKSPACE=<tmp>` (the one true pin — tests
  already isolate this way via an autouse conftest fixture that also strips API keys), run
  a goal, then assert on: `runs/<id>/metadata.json` (status, lane), `run_card.json`
  (classification), `memory/outcomes.jsonl` (+ tri-state verdict), lessons rows,
  `projects/<slug>/NEXT.md` + artifacts, `events.jsonl` (notify emissions),
  `captains_log.jsonl` event sequence. Scenario tests need never touch internals.
- **`notify.command`** gives a process-boundary event probe (payload on stdin, env vars) —
  a test harness can be the substrate.
- **Precedent:** the 2026-08-26 both-engines comparison ran 6 read-only Go renderers
  against the live workspace vs Python, byte-compared outputs, 6/6 identical. The same
  method extends to write paths in a scratch workspace.

Suggested compat tiers for planning: (1) read the same workspace (proven), (2) write
scenario-equivalent workspaces under a scripted backend (ScriptedAdapter port), (3) share
a live workspace (requires honoring file_lock/admission-gate/pidfile conventions — decide
deliberately whether that's a goal at all).

---

## 6. Surprises worth carrying into the planning session

- **step_exec's protocol is "one tool call per step", not an agentic loop.** The outer loop
  is deliberate/structured; the *inner* agency lives in the subprocess backends. A Go
  successor must decide where agency lives — this split (structured spine, agentic leaves,
  tool_events receipts at the boundary) is arguably Maro's most distinctive architectural
  bet and easy to miss reading the docs.
- **Platform is half the system.** By module count and fan-in, the "boring" substrate
  (adapters, locks, run dirs, config, curation, notify) is the majority of the load-bearing
  code — and it's the part with the crispest contracts. Port order should probably follow
  fan-in: config/file-contracts → llm adapters+record → runs/run-dirs → memory stores →
  loop → interface.
- **The memory subsystem's cleanliness is an accident worth keeping**: near-zero imports of
  other subsystems, all coupling via calls and files. It could ship as a standalone Go
  package (and the pack.py portable-learning transport already defines its exchange
  format).
- **Fail-open hooks hid multi-week outages twice.** The pattern is right; the missing half
  is mandatory liveness instrumentation. Design the Go equivalent (typed errors + a health
  census) into the skeleton, not as an afterthought.
- **A lot of "the system" is actually the method around it** — review-to-fixpoint, pin
  tests, defaults registry, mutation batteries, census tripwires. Those live in tests/ and
  docs/, not src/, and they transfer to Go regardless of what code does.
