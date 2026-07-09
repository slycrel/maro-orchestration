# Backlog — Deferred Items, Ideas, and Known Issues

Single canonical location for everything we've identified but haven't done yet.
Read this at the start of every session. Update it as items are completed or new ones emerge.

**Completed items live in [BACKLOG_DONE.md](BACKLOG_DONE.md)** — move items there with their full context when they ship; that file is the archive of what we've already decided, tried, or superseded, and it's ingested by `dev-recall` for historical context.

Last reviewed: 2026-07-09 (decision-cleanup session with Jeremy: #19 thread-arch decisions all resolved + recursion decree recorded, intent-resolution A/B dropped, orch.py trio deprecated, host-check wired+scheduled — four entries → BACKLOG_DONE; fastembed lane confirmed stays-gated). Previous full triage: 2026-07-04.

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

Two asks: (a) every execution lane that can mark `done` must run the same
closure/verdict path as `maro-handle` (or be demoted to `done_unverified`);
(b) cleanup must never delete step artifacts before the verdict is recorded.
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

- [ ] **Curated skills (`skills/*.md`) aren't packaged** — pip installs ship
  no skills dir; doctor honestly flags it. Ship as package data (needs a
  data-files approach for the flat layout) or auto-seed
  `~/.maro/workspace/skills/` from a bundled resource at bootstrap.
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
- [ ] **E2E run left a second haiku.txt at `$HOME`** alongside the in-project
  one — an out-of-fence relative write that the (now default-ON) write fence
  either allowed via the goal-declared-path widening ("a file named
  haiku.txt") or missed. Pull the run's captain's log / FENCE rows next time
  the trial runs and classify: legit widening vs detection hole.
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

- [ ] **(e) Default personas + skills — research orchestration run.** Survey
  the common jobs people actually want from an autonomous agent (link-farm
  FIRST per standing rule, then web research; run through Maro itself where
  practical — dogfood + self-learning involvement per (f)); curate the ship
  set: 5–10 default personas (fits the #4 thread-arch decision — curated
  set, evolution on pressure) + default skill capabilities. Where a
  capability exists in OSS/ideas: swipe code, not deps (standing feedback).
  Where it doesn't: have the orchestrator build it — each gap is itself a
  test goal. Ships through the existing workspace→repo resolution order
  (repo = shipped defaults, workspace = evolved overrides). Depends on the
  skills-packaging residual above (how defaults physically ship: package
  data vs bootstrap seeding).
- [ ] **(f) Self-learning involved in the launch build-out.** Use the
  learning machinery while building (e) — lessons/skills/rules earned
  during the persona/skill build-out become product content, and the
  friction found becomes verify→learn arc input (that arc is already
  sequenced next-after-1.0; this item is its first real consumer). Concrete
  minimum: run (e)'s build goals through `maro-handle` with learning ON and
  audit what crystallizes — the audit doubles as the honest "does
  self-learning ship anything usable" number for 1.0 messaging.
- [ ] **(g) Portable/shareable learning — design + migration path.**
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
- [ ] **(h) Backend-error resilience + auto-resume (Jeremy, 2026-07-09
  late addition).** Research + design pass on the errors an end user will
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
