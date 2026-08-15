---
status: record
---

# GOAL_BRAIN journal archive — 2026-07-12 → 2026-07-31

Rotated out of GOAL_BRAIN.md on 2026-08-16 (Jeremy: the live file had grown past 8k lines / 556KB — beyond whole-file readability). This archive remains part of the same append-only record: entries here are verbatim, in their original order, and dev-recall ingests this file. Later entries: goal-brain-journal-2026-08a.md, then GOAL_BRAIN.md.

- **2026-07-12** — Model-route decision (closes BACKLOG 24's exploration
  question): **stay on Anthropic keys + first-party subscriptions
  (`claude -p` on Max/Pro; codex remains the OpenClaw lane) — no OSS
  coding-plan subscription, no OpenRouter funding for now.** Jeremy: "we
  leave it at anthropic keys and codex/claude pro subscriptions for now...
  that's by far the best option. I'm open to Groq or Gemini free tiers for
  small LLM work in the orchestrator." Rationale: what-I-want (flat OSS
  subs) vs what-makes-sense (sanctioned, predictable first-party lanes)
  came apart under research — 2026 OSS-plan prices are rising, caps
  shrinking, endpoints drift. The budget/OSS lane stays *designed but
  unfunded*: the `claude -p` endpoint-override path (env passthrough,
  llm.py child_env) is documented in docs/MODEL_ROUTE_EXPLORATION.md and
  can be activated later with one config knob + one $8-19 sub. Groq/Gemini
  free tiers are green-lit as the hosted-free small-LLM rung (BACKLOG 25).
- **2026-07-12 (same session, execution)** — Routing + probe-synthesis
  design (`docs/history/2026-07-12-routing-and-probe-synthesis-design.md`)
  **BOTH PARTS SHIPPED**, all `DECISION (provisional)` markers resolved
  into code: **Part A** — `needs_live_data` is an LLM-schema field (not a
  regex), capability override applies to interactive AND task paths,
  `now_lane.live_data_routing` defaults **ON** even fresh-install (differs
  from `escalate_on_not_achieved`'s default-OFF — this flag prevents a
  doomed NOW call rather than re-running a completed one); Manti canonical
  case now routes AGENDA naturally, no `--lane` force. **Part B** —
  `Deliverable.shape` ships as **three** values (`document | runtime |
  data`, not two — a queried dataset is distinct from prose); behavioral
  probes become a shape-conditional **MUST** (was "prefer"), waivable only
  via a logged `behavioral_probe_waived` reason; probes never execute with
  `cwd=None` (synthesize honest `inconclusive`/`env_unresolved` instead);
  majority-inconclusive-with-confidence≥0.7 caps confidence to 0.69 so the
  verifier's own tooling failure can't trip handle.py's 0.7 demotion gate.
  The full BDD red-green loop stays explicitly deferred (B1–B3 are the
  honest-measurement prerequisite, not the synthesis loop itself). Both
  live-verified with zero mocks; full suite green (166 files / 5692 tests).
  Unblocks `docs/VERIFY_LEARN_ARC.md` V0's stated hard dependency.
- **2026-07-12 (same session, follow-on)** — Jeremy: "Note that this is me
  accepting this edge for now, I'd like to revisit this overall later."
  Explicit accept-not-close on the 3 residual gaps surfaced by adversarial-
  review passes 2–3 (waiver content unjudged, fail-relevance unjudged,
  heuristic live-data regex misses named-place phrasing) — deferred to the
  full BDD red-green loop (BACKLOG "Verifier synthesis as a deliverable"),
  not silently accepted forever. Each gap now has a `test_known_gap_*` pin
  test (`test_director.py`, `test_intent.py`) asserting today's behavior,
  so "revisit later" has a concrete artifact to flip when that loop ships.
- **2026-07-12 (same session, follow-on)** — Jeremy agreed "Verifier
  synthesis phase" (BACKLOG "Verifier synthesis as a deliverable") needs
  additional scoping before it's a queueable chunk — no longer just the
  original slycrel-go dream-level aspiration now that 3 concrete
  residual-risk pin tests point at it. Noted in BACKLOG; not started, not
  scheduled this session.
- **2026-07-13 (recursive-goal check-in decree — Jeremy, `/goal` session,
  decree-level design, resolves half of the 2026-07-09 recursion decree's
  deferred "how deep is too deep" question)** — the 2026-07-09 recursion
  decree left the door open for sub-goal spawning but deliberately didn't
  implement depth handling. Jeremy now specifies the concrete check-in
  behavior for deep recursion: **"once we are starting the 3rd goal pass (2
  goals deep beyond the first), while maro is executing in the background
  towards that 3rd recursive goal, have the top level maro start a
  conversation with the user; explain it's going to take a while and
  explain the current plan it's begun working on, and how what it's done is
  working towards what the user asked; allows the user to guide or stop,
  but doesn't stop the goal until the user wants it to. Every 4-7 goals
  after that we do the same; progress update, interact with a chance to
  redirect or stop, and assume (ralph style) that we want to proceed
  optimistically."** Concrete parameters: first check-in fires at depth 3
  (goal → sub-goal → sub-sub-goal, i.e. 2 levels beyond the top-level
  goal); subsequent check-ins every 4–7 goals thereafter (jittered, not a
  fixed period — avoids a metronomic interrupt cadence and matches this
  repo's existing "signals not rule tables" posture elsewhere, e.g. #5
  planning-depth). Mechanism must be **non-blocking**: the check-in is a
  notify-and-continue (same shape as the existing escalation-file surface
  + notify.command lane, NOT a synchronous `input()`-style wait) — the goal
  keeps running unless/until the user actively redirects or stops it
  (ralph-style optimistic default, matching `feedback_ralph_persistence`
  posture). This is a genuinely new mechanism, not a config flip: needs (1)
  a depth counter on the `origin` ancestry dict (already tracks
  parent/goal/loop lineage per the 2026-06-10 run↔thread linkage — extend,
  don't duplicate), (2) a check-in trigger wired at sub-goal-spawn time
  (fires once at depth==3, then on a jittered every-4-7-goals counter
  reset), (3) a plan-summary composer (what's been done, how it serves the
  original ask — likely reads the thread-brain compiled-truth + Decisions
  the way the navigator already does), (4) delivery via the existing
  notify surface (escalation file + notify.command/Telegram — reuse, don't
  build a new channel), (5) a redirect/stop intake path distinct from
  today's escalate move (escalate parks the goal pending human input;
  this must NOT park — it's fire-and-continue with an optional override
  applied asynchronously if/when the user responds). Not yet designed in
  file form — this decree is the spec input; a design doc + MILESTONES
  queue entry should be produced before code, per this repo's own
  "chunk needs a design doc or decided spec, not invented architecture"
  discipline (`docs/IMPLEMENTATION_HANDOFF.md`). Depends on: navigator
  `fork`/sub-goal spawning actually existing as a live code path first
  (today it's schema-only, join gated per MILESTONES #4) — this check-in
  behavior rides on top of sub-goal spawning, it doesn't replace the
  prerequisite that sub-goals can spawn at all. Queued in BACKLOG under
  "Vision / Deferred" pending that scoping pass.

- **2026-07-13 (thread-arch #9 disposition — /loop trace, CLOSED, not a
  design item per GOAL_BRAIN 2026-07-09)** — Traced real `/loop` sessions
  against the per-turn seam the navigator inherits when it goes per-turn
  (`_record_loop_decision` / Phase 64 `adaptive_execution`, today staffed
  by the director — see `loop_post_step.py:_record_loop_decision`
  docstring). Searched all ~700 run dirs in `~/.maro/workspace/runs/` for
  fired mid-loop director decisions; found the seam is exercised rarely
  (`adaptive_execution` defaults **False**, documented dormant/not-started
  in `docs/DEFAULTS.md` — the flag exists so the seam is visible, per that
  doc's own words). Two historical runs (330763a4-cobalt-alder 07-02,
  69f3c689-azure-quartz 06-26) did exercise it and both surfaced the same
  real bug: `director_evaluate`'s stuck-trigger returned the literal
  reasoning `"evaluation skipped"` — but tracing 330763a4's
  `loop-d9331fb0-log.json` showed the true cause was `claude rate-limited
  after 6 retries`, i.e. the evaluation was **attempted and failed**, not
  deliberately skipped. Root cause: `director_evaluate` (src/director.py)
  reused one `DirectorDecision` object for both the deliberate
  dry_run/no-adapter no-op path AND the catch-all `except Exception`
  fallback, collapsing "intentionally not evaluated" and "evaluation
  crashed (e.g. rate-limit)" into the same misleading label — exactly the
  kind of masked-failure signal that would poison the navigator's future
  per-turn judgment once it inherits this seam. **Fixed same session**
  (Sonnet-safe, mechanical, no design call): split into `_continue`
  ("evaluation skipped") vs `_continue_on_error` ("evaluation failed —
  treated as continue"); `tests/test_director.py::TestDirectorEvaluate`
  pins both reasoning strings + their distinctness. **Disposition: CLOSED.**
  The per-turn navigator-model concept itself is sound and doesn't need
  Jeremy's design input — the one class of friction found was a plain bug
  in the (dormant, off-by-default) seam it will inherit, now fixed. No
  further action; `adaptive_execution` stays dormant per its own decree,
  un-gating it is a separate, already-tracked non-goal.

- **2026-07-13 (knowledge-web read side trace — premise wrong, re-scoped
  in BACKLOG, NOT built)** — BACKLOG carried "write side + 2124 edges
  exist; read side has zero callers" as the whole gap. Traced the real
  `~/.maro/workspace/memory/knowledge_{nodes,edges}.jsonl` data before
  writing any read-side code, per Jeremy's own stated uncertainty ("I
  think it could be really powerful if done well (and right now sounds
  like it isn't)"). Found the premise itself was wrong: all 2124 edges
  connect only `lf-` (link-farm import) nodes to other `lf-` nodes —
  exhaustively checked, 0 edges touch any of the 252 real, system-authored
  orchestration nodes (insight/pattern/principle/technique/tool).
  `build_wiki_link_edges` (the only code that could have produced these)
  has zero production callers, and zero of the 252 real nodes' descriptions
  contain the `[[wiki-link]]` markup it would need to traverse anyway — so
  the mechanism is dead on both the side that produced the real 2124 edges
  (some other unwired import process) and the side that would need edges
  for the read side to matter. Wiring `load_knowledge_edges` into
  `inject_knowledge_for_goal` as originally conceived would either do
  nothing (scoped to real nodes — no edges to walk) or inject arbitrary
  link-farm co-occurrence pairs as if meaningfully related (scoped to all
  nodes) — noise, not the "Correspondence"/adjacent-knowledge payoff.
  **Disposition: NOT built.** Full evidence + ordered fix direction (decide
  whether link-farm content should ever inform live goal execution; if
  adjacent-knowledge retrieval over the real base is still wanted, an
  LLM-assisted node-relation pass at crystallization time is the realistic
  edge-generation mechanism, not manual wiki-links) now in BACKLOG.md under
  the same item. This is a design decision about what the graph should
  encode, not an engineering task — correctly re-scoped rather than
  improvised past Jeremy's own flagged doubt.

- **2026-07-13 (session close: post-1.0 /goal arc — recursive-goal
  check-in, planning-depth shadow, R1 architectural cleanup, R3+R4
  adversarial-review, all shipped and pushed)** — Full arc for the day's
  "/goal ... implement as much as we can from the backlog" instruction.
  Chunks shipped: recursive-goal check-in mechanism (non-blocking progress
  notification at deep recursion, director.handle_escalation), planning-
  depth shadow (thread-arch #5, MILESTONES 1.5), director_evaluate masked-
  failure fix (1.6 /loop trace), R1 architectural residuals (prefix
  registry unification, neutral module extraction, curator topo-sort,
  skill_candidate consumer wired into run_curation), knowledge-web read
  side traced and correctly re-scoped (not built — see entry above). Two
  full 3-reviewer (Skeptic/Architect/Minimalist) adversarial-review passes
  ran against this work: R3 (internal subagents, over the first four
  chunks) found 5 real bugs + 3 architectural residuals; R4 (this session's
  closing capstone review, run via the actual `/adversarial-review` skill
  — cross-model Codex reviewers, not subagents, per that skill's hard
  constraint) covered the entire day's diff with explicit attention to R3's
  own fix commit, and found 3 more real bugs (all fixed live, tests added)
  plus 1 pre-existing architectural gap (documented, not a regression).
  Every finding from both passes landed as either a live fix with a
  regression test, or a documented BACKLOG residual — none silently
  dropped, per this repo's own convention. Full suite green (169/169)
  after every fix. **Disposition: arc CLOSED for today.** Remaining backlog
  items are Jeremy-gated (API keys, hardware, design decisions) or
  explicitly deferred pending scale/evidence — nothing else was found to
  be both unblocked and ready without Jeremy's input.

- **2026-07-13 (independent re-check confirms arc closure)** — Jeremy asked
  to check on a background BACKLOG-triage fork (`a498b625d7a0489cd`) and act
  on its report. The fork had no live task; resuming it replayed a **frozen,
  pre-edit transcript** — its report re-described BACKLOG.md's state from
  *before* this session's own "second pass" hygiene chunk (archiving #19/
  #20/#21, fixing the `-1` stale checkbox, correcting #0's mining-passes
  bullet) had already fixed exactly those things. Verified against the
  current files rather than trusting the stale report: all three of its
  flagged issues are already resolved (`BACKLOG.md:424` checkbox is `[x]`,
  `BACKLOG.md:714` bullet corrected, `BACKLOG_DONE.md` holds #20/#21).
  Re-read BACKLOG.md and MILESTONES.md in full to confirm nothing else
  qualifies. Same conclusion, independently reached twice: nothing left in
  the Actionable Stack is both unblocked and ready without Jeremy (only
  #25 API keys, #1/#17 evidence-gated residuals, and Vision-section
  direction-not-design items remain). No new chunk executed — manufacturing
  work here would violate "don't add for its own sake." Arc stays CLOSED;
  working tree confirmed clean, HEAD == origin/main (`06680e5`).

- **2026-07-13 (M1 continuation: portable-import concurrency closed + local
  validator target corrected)** — The R5 portable-import residual was genuinely
  unblocked despite the prior queue summary: removed `_memory_dir_override()`'s
  process-global `MARO_MEMORY_DIR` mutation; `orch_items.memory_dir_context()`
  now routes trust-bearing writers with a ContextVar, and a per-target import
  gate serializes load/check/write decisions. Conflict notes are locked RMW;
  quarantine files are lock-guarded atomic replacements. Deterministic tests
  prove two simultaneous different-target imports cannot redirect each other
  and same-target imports cannot overlap/double-write. R5 item closed.
  Jeremy's bonus ask clarified the local-model objective: the earlier negative
  ROI experiment was on a **2014 Mac mini running Ubuntu**; on this 10-core,
  64 GB M1 Max the target is **the smallest model that preserves the useful
  validation benefit**, not maximum local capability. Live zero-API-spend bake-
  off through the real adapter/protocol: VibeThinker-3B-4bit (MLX) peaked at
  1.83 GB, scored 14/14 across the canonical 8-case eval plus 6 path/constraint
  cases, and averaged 8.2s on the six-case run; 8-bit scored 6/6 but averaged
  16.5s/3.37 GB and crossed the latency breaker; installed Qwopus3.5-27B Q4
  averaged 21.4s and degraded the dangerous wrong-path case to
  `verify skipped (error)`. **Decision:** 4-bit is the Apple Silicon reference
  and is worth using as the gated first-pass validator; it does not replace the
  planner/executor, and hard/uncertain work still escalates. Default script,
  canonical eval, and living docs updated. Cross-model `/adversarial-review`
  ran via Claude CLI: Minimalist completed with two accepted findings (stale
  queue truth; discarded under-lock quarantine outcome), both fixed; Skeptic
  and Architect produced no output/error in 10 minutes and were terminated,
  explicitly recorded as failed reviewers. Full direct suite green at 100%.
  Tangential finding: `scripts/test-safe.sh` hard-depends on Linux `taskset`
  and cannot start pytest on macOS; recorded in BACKLOG, raw venv suite used.

- **2026-07-13 (M1 continuation: run-curation phase boundary closed)** — R5's
  remaining curation residual shipped. `build_run_card()` is now an independently
  callable side-effect-free phase; `maintain_run_card()` explicitly owns
  skills-lite promotion and candidate flagging. Declared producer failures
  propagate as structured `skipped_dependency` outcomes while independent work
  continues; optional omitted fields do not masquerade as failures. The pure
  card is lock-guarded and atomically checkpointed before maintenance, so an
  interruption cannot discard paid-for classification and inventory work.
  Focused regressions cover phase isolation, transitive skips, optional output,
  and the inter-phase interruption boundary. Three real opposite-model Claude
  reviewers completed. Architect and Skeptic independently found divergent
  metadata snapshots; Skeptic also found the standalone maintenance API lacked
  a provenance precondition; Minimalist found an unnecessary unregistered-action
  fallback and repeated immutable provider-map construction. All four accepted
  findings were fixed. Final full raw suite green (only the existing tarfile
  deprecation warnings; platform/integration skips expected).

- **2026-07-13 (M1 continuation: safe-test wrapper now native on this Mac)** —
  Closed the R5 `test-safe.sh` portability residual with a real full wrapper
  run, not only unit simulation. Affinity is conditional on `taskset`; `nice`
  remains universal; the repository venv wins over ambient Python; GNU-only
  xargs flags are gone; chunk arguments stay quoted through Bash arrays; and
  chunk discovery no longer scans the macOS temp tree. Three shell probes cover
  taskset present/absent and CLI resource overrides. Claude Skeptic review
  caught a first-pass ordering bug that made `--cores`/`--nice` ineffective;
  fixed and regression-pinned. Final focused wrapper invocation green, and the
  unchanged full `scripts/test-safe.sh --chunk 10000` completed successfully.

- **2026-07-13 (M1 continuation: bounded run-reference lookup)** — Replaced
  `resolve_run_dir`'s O(all runs) metadata scan with hashed per-reference files
  outside the scanned `runs/` namespace. Existing workspaces migrate once under
  a global lock; healthy unknown refs remain bounded afterward. Incomplete
  migration state retains historical availability without repeating the full
  rewrite, and storage failures retain the legacy fallback. Metadata publication
  is atomic and index-first; imports and pruning maintain mappings explicitly.
  Three Claude lenses found six material edges (torn metadata reads, global
  invalidation from a leaf, scanner pollution, duplicate tie drift, concurrency
  proof, and retry-forever partial migration); all fixed and regression-pinned.
  Focused follow-up approved the revised marker/locking semantics.

- **2026-07-13 (M1 continuation: PID reuse no longer defeats cleanup)** —
  Closed the last R5 follow-up by pairing ephemeral owner PIDs with process
  birth identity. Linux tokens bind boot ID + `/proc` start ticks; Darwin reads
  microsecond start time from `libproc`; generic Unix `ps` fallback pins UTC/C.
  Legacy/missing/unreadable identity remains conservative, but a live reused PID
  with a mismatched birth token now enters the existing container reap or
  retention-safe clone recovery path. Claude review caught critical cross-env
  and cross-method false-delete bugs in the first design plus boot-ID drift and
  Darwin precision gaps; all fixed. Apple C/ctypes layouts match (136 bytes,
  start offsets 120/128) and a real M1 kernel call succeeded.

- **2026-07-13 (M1 continuation: origin ancestry has one typed shape)** —
  Closed the top R3 architectural residual without changing persistence:
  `Origin` is a `total=False` TypedDict over the plain JSON keys already used
  by task, run, recall, navigator, and thread-brain flows. Every origin creation
  or queue-copy boundary now constructs it explicitly; legacy transport keys
  survive dictionary copies. Three Claude lenses correctly rejected the first
  custom merge helper (wrong semantics for queue defaults, speculative surface)
  and duplicate nested navigator type; both were deleted. A follow-up reviewer
  approved the smaller native `Origin(...)` design.

- **2026-07-13 (M1 continuation: curator declarations are executable
  contracts)** — Split mandatory/optional producer outputs and consumer
  dependencies, then made each curator invocation transactional: it mutates an
  isolated deep copy, its full delta is checked for missing or undeclared work,
  and only a valid result commits. This catches new keys, overwrites, deletes,
  and nested mutations while preserving conditional-output semantics. Two
  Claude reviewers found the first pass's ambient-presence check, overwrite
  blind spot, shallow rollback, and optional-require ambiguity; all were fixed,
  and focused follow-up approved.

- **2026-07-13 (M1 continuation: one learnability policy, no fake outcome)** —
  `outcome_policy.is_learnable_outcome` now owns the successful-learning gate
  for both curated run cards and raw ledger outcomes. Curated classification
  takes precedence and fails closed if unknown; raw historical rows retain the
  exact `done`/not-explicitly-unachieved behavior. Run curation, skills, and
  evolver share it, and evolver passes the real `success_class` instead of
  synthesizing `status: done`. Three Claude lenses found no live regression;
  their placement critique moved the helper from the unrelated decision-prior
  schema to its own leaf. Follow-up approved.

- **2026-07-13 (M1 continuation: one owner for paid candidate sweep)** —
  Manual, heartbeat, and run-cadence evolvers now serialize the full
  unconsumed-card scan → `extract_skills` → consume transaction under one
  per-workspace flock. Losers skip before scanning; lock-storage failure skips
  fail-closed; dry-run remains unlocked; daemon singleton behavior remains
  fail-open. A real child-process holder proves exclusion. Claude review chose
  this over claim-before because extraction failures must remain retryable, and
  fixed misleading lock diagnostics plus exception scope. Follow-up approved;
  all R3 residuals are closed.

- **2026-07-13 (M1 continuation: escalation handle correlation)** —
  Escalation-class notifications identify the immediate originating run when
  one exists: queued continuations carry typed `parent_handle_id`, and live
  navigator deferrals read the scoped current handle. This is deliberately
  correlation to the actual emitting/originating hop, not a new synthetic
  root-thread identity. Legacy and pre-run paths keep an explicit blank.

- **2026-07-13 (bonus follow-up: smallest useful M1 validator)** — The
  1.5B/4-bit VibeThinker candidate was run through the same live eight-case
  `VerificationAgent` protocol after Jeremy clarified that minimum useful size,
  not maximum capability, is the goal. It used ~1.18 GB resident / 844 MB disk
  but scored 4/8 and averaged 12.5s/call; all four negative cases were nominal
  low-confidence passes, so the real certainty gate would escalate and save no
  paid call. Therefore 3B/4-bit remains the smallest model currently proven to
  deliver the benefit on this M1 Max.

- **2026-07-14 (closure verdict precedence + restart ancestry)** — The final
  attempt was verdict-aware, but a closure-rejected attempt that triggered a
  restart remained unjudged `done`: attempt 2 replaced both `loop_result` and
  `_closure` before attempt 1's outcome row was annotated. Rejected attempts
  are now stamped false before crossing the restart boundary. Deterministic
  provenance failure also outranks a positive closure narrative in metadata
  and outcomes. Claude's stale-backlog audit found the restart hole and also
  prevented premature closure of the local-model bake-off; one committed
  apples-to-apples corpus remains the bar there.

- **2026-07-14 (formal smallest-useful M1 validator verdict)** — Committed the
  previously missing six path/constraint/execution fixtures, creating one
  balanced 14-case corpus, plus a reusable exact-production-protocol bake-off
  runner. On the M1 Max: VibeThinker-3B-4bit scored 14/14 with 100% decisive
  coverage, zero unsafe false-passes, and 8.83s average; 1.5B/4-bit was slower
  and decisive on only 3/14; 3B/8-bit was larger/slower and unsafe once; Ollama
  qwen2.5-coder:3b averaged 0.81s but unsafely blessed a read-only violation and
  an explicit test failure. **Decision:** the linked 3B/4-bit MLX model is worth
  using on this M1, narrowly as the gated first-pass validator, and is the
  smallest candidate proven to preserve the benefit. The 2014 Ubuntu Mac mini
  is not a viable inference target. Linux remains an on-box burn-in, not an
  extrapolated claim. Tangential correctness fix: production used 0.60 for
  local decisiveness but 0.75 to interpret RETRY, so confidence 0.60–0.74 could
  become a decisive PASS; the local rung now uses `validate.min_certainty` for
  both boundaries. Claude Architect found the identical latent mismatch in the
  hosted-free rung; it now likewise uses `hosted_free.min_certainty`.

- **2026-07-14 (captain's-log event viewer)** — Closed the old mismatch where
  `maro-log --timeline` only aggregated counts despite the backlog asking for
  a sortable event slice. `event_slice()` / `maro-log --events` now span active
  and rotated JSONL and expose timestamp, event, loop, project slug, subject,
  compact scalar key fields, and summary as TSV or JSONL; sorting precedes the
  limit and supports every named identity field. No index or storage migration.
  Real Claude Skeptic + Minimalist review found no high-severity defect and
  caused full two-direction sort tripwires, context-priority/cap coverage,
  post-limit detail computation, context-key sanitization, non-object JSONL
  tolerance, centralized subject filtering, and explicit CLI conflict errors.
  Follow-up review found no remaining HIGH or MEDIUM issue.

- **2026-07-14 (EXT-AUDIT-2 residual: delivery semantics for a failed verdict
  stamp)** — Session opened via `/goal` to reconcile Codex's overnight
  backlog work before the weekly token reset; found EXT-AUDIT-2's second
  checkbox still open. Decision: a `stamp_outcome_verdict()` write failure or
  exception is a demotion of *learning*, never of the delivered result — only
  the closure-restart boundary can still refuse, because it hasn't delivered
  anything yet (`_stamp_superseded`, already fail-closed). Everywhere else
  (ordinary closure stamp, provenance stamp, post-escalation stamp), the new
  `_stamp_verdict_tracked()` helper in `handle.py` logs the failure, stamps a
  `goal_verdict_stamp_failed*` run-metadata breadcrumb, and adds the loop_id
  to `unstamped_loop_ids`, which `finalize_deferred_learning()` now threads
  through to skip both lesson extraction and skill crystallization for that
  loop_id regardless of what the row reads back as — durable quarantine
  instead of falling back to "unjudged" permissiveness. Rejected a
  user-facing channel warning and a new captains_log event type as scope
  creep beyond the residual. 10 new regression tests
  (`tests/test_handle.py::TestVerdictStampFailureQuarantine`,
  `tests/test_verdict_learning.py`); full suite green. Same session also
  fixed two environment-fragile assumptions Codex's "portable test-safe.sh"
  commit introduced (a hardcoded `.venv/bin/python` path that doesn't exist
  on this box, and a fake-PATH isolation trick defeated by this Ubuntu box's
  merged-`/usr` `/bin` symlink) and added missing `status: record`
  frontmatter to a Codex history doc. EXT-AUDIT-2 fully shipped; archived to
  BACKLOG_DONE.md.

- **2026-07-14 (same session: VERIFY_LEARN_ARC V1 — expectation stamping
  SHIPPED, the next arc after 1.0 per thread-arch #6)** — with the backlog
  reconciled and the token reset approaching, picked up the first chunk of
  `docs/VERIFY_LEARN_ARC.md` (dormant-design, hard dependency B3 satisfied
  2026-07-12, no Jeremy-gated decision blocks V1 specifically — the two
  provisional DECISION markers in the doc gate V2/V5, not V1).
  `evolver_store.Suggestion` gains `expected_signal: List[dict]` (additive,
  empty-default — every existing row/producer stays valid unchanged). All 9
  `_GRADUATION_TEMPLATES` (graduation.py) now declare
  `[{"metric": "failure_class_rate", "class": <own dict key>, "direction":
  "down"}]`, derived once from the templates' own keys in a single loop so
  the class name can never drift from the template it describes.
  `_EVOLVER_SYSTEM` teaches the LLM proposer the same optional field;
  `run_evolver()`'s raw-suggestion parse threads it through via
  `safe_list(..., element_type=dict)`, the same sanitization every other
  LLM-authored field already gets. Deliberately scoped OUT the ~7 other
  `Suggestion`-emitting call sites (calibration/cost/canon/suggestion-
  calibration/drift/signal/harness-friction/persona-gap scanners) — the
  design doc's own "rows without one default to the class-neutral pair" is
  V2's read-time trust-policy job, not something V1 should bake into every
  producer now, before V2 has decided what that policy actually is. 8 new
  row-shape unit tests (`tests/test_evolver.py`, `tests/test_graduation.py`);
  full suite green (180/180 files) throughout.

- **2026-07-14 (same session: VERIFY_LEARN_ARC V2 — cadence verdicts +
  auto-revert SHIPPED, the judgment-heavy Opus chunk)** — the VERIFY gap for
  evolver-applied suggestions is now closed. At each evolver cadence,
  `verify_applied_suggestions()` (evolver_scans.py) renders a *behavioral*
  verdict on every applied-but-unverified `Suggestion`: it builds count-based
  before/after windows keyed to each row's `applied_at`, computes a
  class-neutral stuck-rate pair, and classifies confirmed / degraded /
  inconclusive. **confirmed** → `stamp_verification(verdict="confirmed")` +
  feed the confidence calibrator True; **degraded self-applied** → auto-revert
  (`revert_suggestion`) + `EVOLVER_VERDICT` captain's-log event + non-blocking
  `self_improvement_verdict` notify + calibrate False; **degraded
  human-applied** → stamp `degraded_needs_review` + BLOCKING notify to the
  review queue, **never auto-reverted** (this is the symmetric-authority §3
  decision made concrete: the system may undo only what the system applied);
  **inconclusive** → bump `verify_extensions`, park `unverifiable` after
  `evolver.verify_max_extensions` (3). Trust policy (§4) shipped as the first
  consumer: `verdict_trust()` lives in memory_ledger.py as the single source
  (accepts an `Outcome` OR a dict) — `closure_unverifiable`/env-capped outcomes
  are `excluded`, judged-below-0.7-confidence are `directional`, both dropped
  from a window's denominator so a self-verdict can never grade its own change.
  **Prerequisite production bug fixed in the same chunk:** `scan_evolver_impact`
  (and thus the entire old warn path) windowed on `created_at`/`timestamp`, but
  real `Outcome`s carry `recorded_at` — it had been silently dead on production
  data. `_outcome_ts` now prefers `recorded_at`. Config knobs (DEFAULTS.md):
  `evolver.verify_cadence_verdicts` default **ON** (justified — it is a safety
  mechanism that only ever reverts what the system itself auto-applied),
  `verify_min_post_apply`=10, `verify_max_extensions`=3,
  `verify_delta_threshold`=0.05. Operator surface `maro evolver verify
  [--apply]` (dry-run by default). 17 new tests; both acceptance legs (one
  confirm, one degrade→revert-with-calibration) exercised; full box-safe suite
  green. Design doc §1/§7 marked V2 SHIPPED. **Next: V3** (graduation
  *behavioral* auto-verify + demote — its structural precursor already shipped
  2026-07-14; V1+V2 were its stated prerequisites, now present) or V4/V5 (the
  navigator-side half). Neither is Jeremy-gated by an open DECISION marker.

- **2026-07-14 (Jeremy) — V3 is BUILDABLE NOW, not decision-blocked.** Codex,
  working in parallel, documented an "Owner decision 2026-07-14: defer full V3 —
  design dependency" in `docs/VERIFY_LEARN_ARC.md`. Jeremy reviewed and
  overrode: *"Agree with your assessment and proposal; buildable now."* The two
  prerequisites Codex framed as "must define first" already shipped this day —
  the behavioral expectation a graduated rule carries IS V1's `expected_signal`
  (all 9 templates declare `failure_class_rate ↓`), and the authority-aware,
  crash-safe demotion target IS V2's symmetric-authority revert plus the
  `behavioral` flag (real undo vs. un-revertable append-only → surface-for-
  review). V2's verify path is already category-agnostic, so an applied
  graduation row flows through it today. What remains for V3 is **build, not
  decision**: (a) wire graduation's *pending* rows into the apply→verify
  lifecycle (nothing autonomously applies `graduation:` rows today); (b) reuse
  V2's class-neutral stuck-rate fallback for the `failure_class_rate` metric
  (timestamped diagnoses still don't exist) or add diagnosis timestamps. The one
  genuine owner call is narrow and has a no-meeting-needed safe default: **keep
  graduation rules advisor-gated (held-for-review, like guardrails)** so V3 ships
  the full verify→demote loop without ever auto-applying a standing rule. Doc,
  MILESTONES, and BACKLOG reframed from "deferred/design-dependency" to
  "buildable now" in the same chunk. (Also this session: V2 adversarial-review
  hardening SHIPPED 584b902 — bounded windows, `behavioral`-flagged honest
  reverts, authority re-check, baseline floor, impact-gate fix; reconciled clean
  with Codex's parallel audit/admission commits, box-safe suite green at 181.)

- **2026-07-14 (Jeremy: "Let's do it") — VERIFY_LEARN_ARC V3 SHIPPED.** The
  build following the "buildable now" call. Key realization while building: the
  design's item (a) "wire graduation's pending rows into apply→verify" was
  already true — an *applied* graduation row flows through V2's cadence verify —
  but on the class-neutral *global* stuck-rate, in which a single failure class
  is noise, so graduation rows only ever parked `unverifiable`. So V3's real
  substance is the metric: `verify_applied_suggestions` now consumes a row's V1
  `expected_signal` and verdicts a `failure_class_rate` row on *that class's*
  rate over timestamped-diagnosis windows (self-falls-back to the stuck-rate
  when class windows are thin → a sparse class parks honestly, never verdicts
  off noise). The diagnosis ledger had no time axis of its own, which was the
  actual reason V2 fell back to class-neutral. V3 gives each diagnosis a date two
  ways: a go-forward `recorded_at` stamp (`introspect.save_diagnosis`) AND — the
  fix Jeremy prompted ("give those dates a better path; seems fragile") — an
  events-log join on `loop_id` (`_loop_ts_index`). The earlier "~99% lossy join"
  note was against the *outcomes* log (only 21/1277 carry `loop_id`); the
  **events** log carries `loop_id`+`ts` on ~99% of rows, so `_load_dated_diagnoses`
  recovers 1274/1277 real diagnoses. The class path is therefore live on the full
  historical ledger, not dormant waiting for post-V3 rows. Lifecycle + symmetric
  authority reused from V2 unchanged.
  **The one owner call landed as its safe default: graduation stays advisor-gated
  — a human applies via `maro evolver apply`, nothing auto-applies a standing
  rule → a degraded graduation row surfaces for review, never auto-reverted.**
  Autonomous *apply* of graduation deliberately NOT built (owner posture, not a
  gap). Structural `verify_pattern` grep kept as pure observability (a grep miss
  ≠ the applied lesson failed — it must not gate state; this is why it stayed
  observe-only, not a V1/V2 sequencing block as Codex had framed). Knob
  `evolver.verify_use_class_signal` (DEFAULTS.md, default ON). 10 new tests
  (`test_evolver.py::TestVerifyClassSignal` + `test_introspect.py`); box-safe
  suite green. **Applied-change verify→learn is now closed for BOTH the
  evolver-suggestion (V2) and graduation (V3) lanes; V4/V5 (navigator half of
  thread decision #6) is the remaining open work.**

- **2026-07-14 (Jeremy: "let's do v4 and v5, and … give those dates a better
  path") — VERIFY_LEARN_ARC V4 + V5 SHIPPED; whole arc (thread decision #6)
  closed.** First, the "dates" fix Jeremy prompted: V3's per-class time axis was
  fragile (go-forward `recorded_at` only → dormant on all 1395 historical
  diagnoses). Fixed with an events-log join on `loop_id` (`_loop_ts_index`) →
  `_load_dated_diagnoses` recovers 1274/1277 real diagnoses; the class path is
  live on the historical ledger now. **V4 — divergence adjudication:** at evolver
  cadence (no daemon), `adjudicate_navigator_divergences` gives un-adjudicated
  NAVIGATOR_DECIDED divergences a capped, cheap-tier LLM verdict (navigator_right
  / pipeline_right / both_defensible), appended append-only as
  `NAVIGATOR_ADJUDICATED` (joined by `div_key`) and surfaced in
  `--agreement` as an `adjudicated` breakdown — the cutover-evidence surface, now
  standing. Proven end-to-end on the box's 71 live divergences (40 navigator_right
  under a crude smoke judge; the navigator correctly refused dangerous/impossible
  goals — "Wire $50,000", "Prove 1=2" — while the pipeline blindly executed).
  **V5 — navigator lessons:** `pipeline_right` clusters (navigator-wrong shapes,
  ≥3 same-shape) crystallize into corrective lessons (`navigator_lessons.jsonl`,
  a derived view — full rewrite over the append-only adjudications) injected into
  `decide()` via the same worker-slice recall seam; A/B flag
  `navigator.lesson_inject` (default off), `lessons_injected` marker on the
  decision row for shadow-comparison. **Owner call still standing (Jeremy's
  posture, §5 DECISION): per-move cutover stays human — this makes the evidence
  cheaper and standing, it does not automate the cutover.** Both adjudication
  knobs default OFF (LLM spend, no silent cost); **enabling `navigator.adjudicate_divergences`
  on the box is a spend decision, left OFF pending Jeremy** (~71 cheap-tier calls
  to clear the backlog, then a trickle). Knobs in DEFAULTS.md; 19 new tests; box
  suite green. **The verify→learn arc is now fully closed (V1–V5).** Next arc
  is open — no successor decreed yet.

- **2026-07-14 (audit repair convergence + truth cleanup, superseding stale
  same-day queue claims)** — The owner-approved `AUDIT INCOMPLETE` delivery
  policy now has a bounded consumer. `maro-runs repair-audits
  [handle-or-loop]` and the existing autonomy/evolver cadence validate and
  replay exact stored per-loop verdict patches, then finalize only each named
  outcome row's deferred lesson/knowledge extraction. One workspace pidfile prevents
  duplicate paid work; a durable `surface_pending` checkpoint lets a crash
  resume run-card/report refresh without replaying verdict or learning.
  Malformed joins, missing rows, stamp failures, and adapter failures remain
  quarantined. Skill crystallization is intentionally not fabricated because
  the required `StepOutcome` inputs are not durable in the repair record.
  This also supersedes the earlier delivery note's rejected user-facing
  warning: Jeremy's later explicit decision is preserve delivery **with a
  prominent warning and exact repair metadata**.

  The same current-truth pass resolves three stale queue surfaces. Manti Run 3
  (`5126986b`) completed cleanly at 6 steps / 16m43s / $1.52 / closure 0.95;
  only its 1–3 minute/cents envelope remains open. Phase 65's six-run A/B did
  run and selected plan compression (8 versus 15–40 steps); the single-persona
  MVE was live on the audited runtime box while this unconfigured M1 and fresh
  installs remain OFF for spend, and deeper design stays deferred. The
  count-files interpretation contract is shipped and its activation posture is
  decided (explicit runtime opt-in, default and this M1 OFF), so it moved
  to BACKLOG_DONE rather than retaining a fake activation blocker.

  The six-persona opposite-model review rejected the reconciler's first draft
  and materially changed the landing. Accepted blockers: directory-mtime
  retry churn could starve good records; the loop join failed open when absent;
  live runs and multi-loop audit failures could clear the wrong quarantine;
  run metadata could disagree with the repaired ledger; and existing metadata
  writers did not honor the repair lock. The final design scans finalized runs
  fairly by persisted attempt time, requires the loop join, stores a canonical
  per-loop queue, fences each update by loop+recorded_at, aligns the latest
  repaired verdict into run metadata, and moves all run-metadata mutations to
  locked RMW. Invalid/missing rows stop automatic retry immediately; other
  failures cap at five while retaining manual quarantine. Also corrected the
  review's truth findings: Phase 65's reliable A/B signal is plan compression,
  not the confounded clean-run ratio; Manti evidence is attributed separately
  to Runs 2/3/4; fresh-default count ambiguity remains an explicit cost posture.

- **2026-07-14 (Jeremy: "Let's fire the ~75 calls; no worries on my end") —
  adjudication FIRED on the box.** The spend gate the V4/V5 entry left pending is
  now spent, by Jeremy's call. All 71 backlogged divergences judged in one manual
  `--adjudicate --max 100` pass (CLI path bypasses the OFF `adjudicate_divergences`
  cadence gate by design): **61 navigator_right / 10 pipeline_right / 0
  both_defensible** — the real (not smoke-judge) verdict is that the navigator was
  right to diverge **86%** of the time. The 10 navigator-wrong cases cluster into
  (A) over-cautious escalate/close on trivially-tryable dispatch goals and (B)
  blocked_step retry/bail instead of extend; the 3× blocked_step execute→extend
  cluster crystallized the **first V5 lesson**. Re-run is idempotent (71 already-
  adjudicated, 0 re-spend). `navigator.adjudicate_divergences` cadence gate stays
  OFF (this was a one-shot operator clear, not standing autonomous spend).
- **2026-07-14 (Jeremy: "let's do an adversarial review on the other work") —
  V4/V5 review done, hardened (commit b47767c).** Codex ×3 (opposite model,
  per the adversarial-review skill) over `e792768`+`8349b7c`. Verdict CONTESTED —
  real findings, none blocking (evidence-only, gated OFF; empirically 0 div_key
  collisions in the 71 rows, events.jsonl 2.8 MB). **Fixed 4:** B honest
  persistence (raise_on_error + count-only-on-durable-write + `write_failed`
  counter — a swallowed write no longer reads as backlog-cleared), C atomic
  lessons rewrite (tmp+os.replace), D2 undatable-diagnosis drops counted+logged
  (no-silent-caps decree), F NAVIGATOR_ADJUDICATED registered in EVENT_TYPES.
  **Deferred 4 with rationale (BACKLOG R6):** A div_key second-precision collision
  (the goal_preview fix retroactively invalidates all 71 existing div_keys →
  strands the verdicts + re-spend; 0 live collisions so not worth it — revisit
  only under concurrency, with a key migration); G check-then-write concurrency
  race (gated-off, manual, worst-case wasted cheap calls — a flock is heavier than
  the path warrants); D1 `read_jsonl_tail` whole-file read (latent at 2.8 MB,
  caller self-extinguishes; fix belongs in shared `jsonl_utils`); E lesson-preview
  anchoring (let the default-off `lesson_inject` A/B measure it). **Lead judgment
  rejected H** (materialized-view-vs-on-demand — deliberate hot-path choice;
  crash-safety sub-point covered by fix C). **One decision now surfaced to Jeremy:
  enable `navigator.lesson_inject`?** Review-cleared + reversible + shadow-marked,
  but it feeds ONE lesson into a *live-acting* navigator (escalate-acting on since
  2026-06-21), so it's cutover-adjacent → his call, recommended-but-held.
- **2026-07-14 (Jeremy: "turn it on please") — `navigator.lesson_inject` ENABLED on
  the box.** The V5 A/B flag is on in `~/.maro/workspace/config.yml` (runtime, not
  git). Verified live end-to-end: `decide()` reads the flag true, the one crystallized
  lesson (blocked_step execute→extend, from the 71-divergence pass) reaches the model
  prompt as an advisory "## Lessons from past adjudicated divergences" block, and
  `lessons_injected=1` is stamped on the NAVIGATOR_DECIDED row for shadow A/B. Caveat
  on the record: ONE lesson feeding a live-acting navigator — RE-VERIFY by comparing
  decision quality on rows with `lessons_injected`>0 vs =0 once more lessons accrue.
  Revert = flip false. **V5 loop now closed end-to-end: adjudicate → crystallize →
  inject → shadow-measure.**
- **2026-07-16 (Jeremy: "get started with hooking up hermes to maro properly") —
  SESSION PROTOCOL §9 STAGE 2 SHIPPED: Hermes→Maro cross-box dispatch live.**
  The thinnest slice per `docs/SESSION_PROTOCOL_DESIGN.md`: Hermes on the Mini
  (192.168.0.55) dispatches over SSH, run_card comes back. Pieces:
  `deploy/hermes/maro-ssh-gate.sh` (forced-command allowlist on a dedicated
  ed25519 key — ping/dispatch/status/result/list, nothing else; §8 posture:
  the Mini's brain is an LLM with shell access, so no login shell) +
  `deploy/hermes/dispatch.py` (async split of `enqueue --drain`: enqueue
  returns job_id in seconds, a detached per-job worker drains it — drain-once
  contract intact — and records the job_id→handle_id join in
  `output/hermes-dispatch/<job_id>.json`, the mapping core never persisted;
  needed because Hermes caps tool calls at 300s) + Hermes-side skill
  `~/.hermes/skills/orchestration/maro-dispatch/SKILL.md` (async etiquette:
  dispatch = receipt, poll status, never block). Verified end-to-end
  2026-07-16. Also same session: goal viewer started LAN-visible
  (`scripts/viz-ctl.sh start --host 0.0.0.0` → http://192.168.0.45:8787/index.html;
  process-level, does not survive reboot). Open q7 (container-on for
  network-sourced goals) still Jeremy-gated.
- **2026-07-16 (Jeremy, morning after dispatch went live) — three calls in one
  message:** (1) **`executor.container: on` FLIPPED on the box** ("I'm fine
  flipping it on… better in-practice testing on that harder, more secure
  edge") — resolves SESSION_PROTOCOL_DESIGN §11 q7; box-level, all runs;
  fresh-install default stays off per CONTAINER_BURN_IN §6; verified same
  morning with a containerized dispatch through the Hermes gate. (2)
  **Orchestration Telegram alerts → the Hermes /sethome ops group, not the
  DM** ("truly de-clutter that") — `telegram.chat_id` in `~/.maro/config.yml`
  now points at the home-channel group; same bot, Hermes owns polling, Maro
  only sends; verified live. (3) **Share the two-box PoC** ("no reason to not
  PoC what we've done here and share") → `deploy/hermes/TWO_BOX_POC.md`
  (beta/tips species, secrets scrubbed), indexed in docs/INDEX.md.
  iMessage status same message: icloud.com sign-in works (account unlocked),
  Messages still sign-in→sign-out ~10s with no 2FA prompt; Jeremy created
  agentic.poe@icloud.com and suspects Monterey too old for the 2FA device
  path — he'll try an old device as trusted-device anchor.
- **2026-07-16 (system) — container-on day one caught a verification-layer
  bug, fixed same morning:** the container itself verified clean (steps saw
  `/.dockerenv` + cgroup v2 markers from inside), but the verification run
  (123bf935) was **falsely demoted to incomplete** by the provenance
  freshness gate: `_run_window_start` reconstructed the run window as
  now − elapsed_ms − buffer, and a slow post-loop closure pushed "now" ~8 min
  past loop end — sliding the window past artifacts the run's own early steps
  had genuinely written (mtimes 15:04/15:07 vs reconstructed window ~15:10).
  False demotions poison `goal_achieved` (the substrate-trial lesson), so
  fixed immediately, not backlogged: `_handle_impl` now records a wall-clock
  start beside the monotonic anchor and both provenance call sites (NOW +
  agenda) prefer it over the reconstruction. Pin test
  `test_run_window_start_prefers_wall_anchor` reproduces the exact shape.
- **2026-07-16 (Jeremy, midday decision batch — answers to the morning AFK
  list, plus a process decree):** (0) **Process: lead with decisions** —
  "This wasn't me being able to 'quickly' decide anything, maybe lead with
  the decisions as the other work proceeds"; he happened to be WFH today,
  won't always be. (pre) **Viewer autostart decree:** "add a config value to
  start the viewer, if it's not on, upon a goal run ... default to off, but
  let's turn it on for our box" → `viz.autostart` SHIPPED same day (2cbef2f),
  live-verified revival. (1) **Stuck advisor: fix + config-gate, box ON**
  ("turn it on by default for ourselves for actual testing as we go... we can
  rabbit hole down the local LLM vs spend vs 'off' behavior later") →
  `advisor.stuck_step` SHIPPED (3d35ba0), fresh default off. (2)
  **`navigator.adjudicate_divergences` ON** ("let's turn that on, yep") —
  box config flipped. (3) **director_evaluate(trigger="injection"): build +
  enable** ("agree, build + enable") — SHIPPED same day: fires at the
  boundary poll when an interrupt was applied and the loop continues,
  injected text reaches the director via `EvaluationContext.injected_context`,
  arms mirror `_ae2` (replan budget-clamped); gate
  `director.evaluate_on_injection` fresh-default OFF, box ON. (4) **Hosted-free keys: remind
  tonight** — Gemini key location non-obvious to him, no Groq account yet;
  one-shot systemd timer set for 19:04 MT. Same breath, **local-validator
  lean-in decree:** "we should lean into that slightly upgraded local option
  from a few days ago for now, and let's keep an eye on the overall time" —
  Linux burn-in priority raised in BACKLOG. (5) **Billing-failover default
  OFF RATIFIED** ("billing default should be off, yes"); **auto-resume cap
  NOT ratified** — "probably need a discussion around the resume
  cap/flexibility... likely right decision is not binary" (design doc
  annotated). (6) **Flagship Tier-5 goal:** "that was an example idea of the
  pattern" — channel is RektProof PA (~43k subs, his subscription, bot
  access unverified), evening project someday, NOT urgent; expects the
  strategy "will never amount to much", the value is orchestration under
  real-world stakes. (7) **Purgatorio #3 needs re-decision with new facts:**
  Jeremy thought the git-history review was covered by the 0.8.0-era
  rewrite; scan today shows the 2026-07-12 rewrite was employer-token-scoped
  ONLY — the historical `user/` GOALS/CONTEXT/SIGNALS blobs with medication
  details (GLP-1/nootropic lists) are reachable in the PUBLIC repo history
  right now (added 99f5a67 2026-03-30, cleaned at tip 358ad5d 2026-07-09;
  also on stale remote branches feat/local-validator +
  worktree-refactor-plan). Options + turnkey `--replace-text` path prepared;
  HIS CALL, nothing executed. iMessage saga same message: Monterey-2FA
  theory DEAD (the Mini worked as a 2FA device); iPhone 7 also
  signs-in-then-out with "can't be activated" + support link; new theory =
  iMessage activation requires a real phone number (anti-spam); he messaged
  Apple support. Side wins: agentic.poe@icloud.com email live; yahoo mail
  wired into the Mini via Internet Accounts.

- **2026-07-16 (Jeremy, evening) — Purgatorio #3 RESOLVED: ACCEPT exposure.**
  "I'm not overly concerned, it's all supplement talk outside of the grey
  market peptides; my understanding is that's discouraged/disallowed on the
  seller side as not approved, not prohibited/illegal on the consumer side.
  I'm comfortable with that line, but could be persuaded if I'm silently
  asking for a felony or some such nonsense if I'm targeted." Verification
  (general info, not legal advice): nothing in the exposed blobs is
  felony-class; peptides (retatrutide etc.) are unscheduled — FDA enforcement
  is seller-side, matching his read; the single nuance is armodafinil
  (US Schedule IV — *unprescribed possession* is misdemeanor-class, and a
  historical doc mention is not possession evidence). His comfort line
  holds → NO second history rewrite, stale branches left as-is (deleting
  them gains nothing while main's history stays reachable). Standing state:
  `user/` medication-era blobs remain reachable in public history
  (99f5a67..358ad5d + two stale branches); tip is clean/neutral since
  358ad5d. Same message, injection decision-point follow-ups decreed:
  (a) after-the-fact visibility that injection ≠ prompt ("some clear
  delineation there"), (b) confirm overall goal change vs clarification is
  possible in that hook — both addressed same evening (LoopResult.injections
  + run-report "Operator injections" section + GOAL CHANGED decision line +
  ctx.goal sync; corrective intent = goal replacement, confirmed live in
  code). (2nd tangent) **Local validator: use VibeThinker 4-bit** — "we
  should use that new 4 bit quantized model of the tiny model we found when
  testing on the M1... I don't have concerns about flipping that custom
  model back on in the smaller footprint version"; note his memory slightly
  inverts the sweep record (the 8-bit was REJECTED — 1 unsafe false-pass,
  slower; the 4-bit is the reference that aced it), which only strengthens
  his call. Linux lane = GGUF Q4_K_M via Ollama, burn-in gate per BACKLOG
  (zero unsafe false-passes + warm latency under breaker) — bakeoff run on
  the box same evening: **FAILS the latency gate** (13/14 verdicts hit the
  60s adapter timeout; the one completed took 55.8s vs the 15s breaker;
  0 unsafe false-passes but 1/14 decisive coverage). VibeThinker-4bit stays
  the Apple-Silicon reference; on this box qwen+Tier-0 guards+breaker stand,
  hosted-free is the quality lane (keys tonight). Raw:
  research/validator-bakeoff-linux-2026-07-16.json. Side-find while running it: the orchestration-owned
  ollama daemon had inherited a since-deleted Claude agent worktree as cwd →
  every llama-server load died ("cannot get current path") → local tier
  silently erroring, every validation escalated to paid. Fixed (spawn
  cwd="/", src/local_models.py) + regression test.
- **2026-07-16 (Jeremy, late evening) — qwen parameter sweep run + local-lane
  posture ratified.** Follow-up asks: verify thinking (not startup/teardown)
  killed VibeThinker → confirmed by warm probe (7.1 tok/s warm, ~10s one-time
  load; even "2+2" burns ~122 reasoning tokens); "worth looking at
  qwen2.5-coder:3b and seeing if we can find a smaller bit model with similar
  capabilities" (his caveat: "principle of the thing... we're going to change
  escalation (or direct) path likely tonight to the free tier of groq/gemini
  anyway") → default qwen tags are already Q4, so swept parameter count
  instead: 3b keeps (12/14, full coverage, 10.9s avg — under breaker, same 2
  false-passes as M1, Tier-0-covered); 1.5b and 0.5b REJECTED (coverage/
  accuracy crater faster than latency improves; 0.5b is a rubber stamp,
  3 unsafe false-passes). See docs/LOCAL_VALIDATOR.md "Linux qwen parameter
  sweep"; raw in research/. Jeremy on results: "likely the lower tiers aren't
  going to be as helpful. I like the idea of a small LLM for this and other
  things like this, but the time is just longer than we want. Kinda lame that
  the free tier of cloud stuff is better nearly at any local hardware level
  (even the M1 likely)." → Standing read: local lane = free/offline floor
  behind Tier-0 + breaker, hosted-free = the real quality/latency lane; don't
  invest further in local-model upgrades on this box unprompted.
- **2026-07-16 (Jeremy, same exchange) — VALIDATION FREE-TIER ORDER FLIPPED:
  hosted-free first, local backup.** "Let's (sadly) flip this then;
  hosted-free first, then 3b local as backup. Not that gemini + groq are
  likely to ever be down at the same time so maybe moot, but slow + local
  seems better than a network API call fail for whatever reason." Shipped
  same hour: `step_exec.verify_step` reordered — hosted-free (when enabled +
  keyed) judges first; local qwen is the AVAILABILITY backup, consulted only
  when the hosted tier is inert or produces no verdict (conf-0.0 sentinel =
  transport/parse failure); a genuine hosted UNDECIDED escalates straight to
  paid (weaker local model doesn't overrule a stronger model's uncertainty).
  Fresh installs unchanged (hosted-free stays consent-gated OFF; local
  remains first rung when hosted is inert). Box config:
  `validate.hosted_free.enabled: true` (inert until keys). Credentials .env
  migrated legacy→`~/.maro/workspace/secrets/.env` (preferred path; all 5
  legacy keys carried; legacy file untouched). Keys are Jeremy's next move
  (console.groq.com + aistudio.google.com/apikey → append to that .env);
  hosted tier then goes live with zero further changes.
- **2026-07-16 (Jeremy, keys live ~1hr later) — HOSTED-FREE TIER LIVE +
  credentials backup decree.** Keys added by Jeremy (Gemini via a renamed
  pre-existing key); "Feel free to test those, both directly and in the
  orchestration" → both providers live-verified through the production
  ladder on the 14-case corpus: **gemini-flash-lite-latest 14/14, all
  decisive, 0 unsafe false-passes, 0.66s avg (perfect score — M1 reference
  quality at 13× speed)**; groq llama-3.1-8b-instant 12/14, 1 unsafe
  false-pass (failing-test case, Tier-0-covered), 0.28s avg. Box order
  flipped `[gemini, groq]` (quality first, 429-breaker auto-spill to Groq's
  30 RPM/14.4K-day volume tier — confirmed live). Shipped default
  `gemini_model` moved `gemini-2.0-flash`→`gemini-flash-lite-latest` (2.x =
  free-quota `limit: 0` for new users, 2.5 = 404 "no longer available to
  new users" — the predicted catalog churn, now measured). BACKLOG #25
  archived to BACKLOG_DONE. Decree: "Back those all up if desired into our
  claude workspace somewhere, along with the general machine credentials.
  I could see us wiping the maro workspace at some point" → full machine
  credential backup at `~/claude/credentials-backup/` (README manifest;
  maro secrets .env, gh hosts.yml, ssh keys incl. mini2 dispatch lane,
  openclaw.json + recovered vault; outside all git repos, chmod 700;
  re-copy on key rotation). Follow-up same evening: Jeremy approved a
  shadow-eval batch on the new hosted tier ("go ahead and shadow eval
  that") → `validate.shadow_eval: true` on the box 2026-07-16; measures
  gemini-vs-paid agreement on organic traffic; doubles validation spend
  while on; one-shot Telegram reminder (maro-shadow-batch-reminder.timer,
  2026-07-19 09:00) to analyze via `python3 -m validation_shadow
  --agreement` and flip it back OFF. Also removed the now-moot 19:04
  keys-reminder timer (unit files deleted, not disabled).
- **2026-07-16 (Jeremy, afternoon): "Let's fix those 2 goal related things"**
  — the two container-on day-one findings, both SHIPPED same day via the
  writer→verify→adversarial-review→fix pattern (full records in
  BACKLOG_DONE): (1) closure downgrade reason now reaches the run card
  (summary leads with the cause; `goal_verdict_downgrade_reason` on
  metadata/card/report/CLI; review found + fixed a resume stale-key bug —
  clean retry used to render "Goal achieved: yes" beside "Downgraded: …");
  (2) goal-text step-count ceilings are binding in decompose
  (`goal_step_ceiling` detector + directive + clamp + re-ask-then-truncate
  on all six lanes + boundary/milestone expansion carry; no-ceiling prompts
  byte-identical, reviewer-proven). Code content was swept into `5c3a886`
  by the parallel session mid-flow (Jeremy: safe to ignore if complete —
  it was complete + green; reviewer md5-confirmed no mutant leaked);
  review fixes + records landed as the follow-up commits. Two new BACKLOG
  items spawned, one DECISION-FLAGGED: probe-modality per-segment
  classification (fix shifts verdicts toward blessing — needs a deliberate
  call), and precondition pre-flight strings leaking into closure checks.
- **2026-07-16 (Jeremy, later afternoon) — two calls on the spawned items.**
  On the probe-modality DECISION-FLAG, after the verdict-shift measurement
  (58 recorded closure verdicts replayed: 11 probe shifts, all genuine
  run-then-grep idioms; exactly 1 historical verdict flips — d2f4e2f4's
  own false downgrade): "Agree, ship the fix with the quote-aware
  splitter" → SHIPPED same day (per-segment classification, quote-aware
  top-level splitter, within-segment go-build precedence preserved,
  evidence-gate + preflight-dist riders; record in BACKLOG_DONE). And on
  the stuck-advisor dead code: "let's fix + enable that stuck lane's
  advisor. I thought that was on" — RECONCILED: the morning session had
  already shipped exactly this (`3d35ba0`, Jeremy there: "fix +
  config-gate... turn it on by default for ourselves") — gate default OFF
  for fresh installs (no-silent-spend), `advisor.stuck_step: true` on this
  box. The two decrees agree; he thought it was on because it was. The
  afternoon session's delta: the missing restart-break record pin
  (mutant-verified).
  The precondition pre-flight leak also shipped this afternoon
  (mechanism corrected: comma-shredded prose preconditions + a too-loose
  command gate, not shell-executed Python strings; record in BACKLOG_DONE).
- **2026-07-17 (Jeremy, late night) — the delivery loop IS the product surface
  (standing course-correction).** After the first real Hermes-dispatch run:
  "we're still making the standard LLM mistake... I'm sitting here 'waiting'
  as an end user; hermes can't tell me it's finished (or not) because it's
  not checking. I got a message saying it wasn't done ('restarted') which was
  bad. I also don't have a meaningful way to see what happened... It's like
  we missed the forest for the trees." Internal fixes don't count until the
  user gets told the answer where they asked. Shipped same night: per-loop
  "Mission complete" alert → restart-only progress ping; run-level completion
  message rewritten user-grade (verdict, findings, cost, viewer link);
  SESSION_PROTOCOL §3 push leg live (notify-hermes.sh → mini2 inbox + DM
  follow-up for dispatched jobs). Standing test for future work: does the
  end user hear the outcome, in plain words, where they asked for the work?
- **2026-07-17 (Jeremy, late night) — completion results are TWO-TONE
  (standing design contract).** Same conversation, on the answer itself:
  "if we're running a raw orchestrator, we likely want a human readable
  answer. If we're calling the orchestrator from another LLM, we want to
  give it the data and have it organize an answer for the user in the way
  it deems appropriate; the data should be there (original ask and the data
  for the result)." Also answer-first: "user doesn't care that it worked
  (presumption is that it did...!) ... we ask a question and should get an
  answer" — the verifier's self-grade answers "did the machinery work?",
  the wrong question; it earns space only when the goal was NOT achieved.
  Shipped same night: curation `locate_deliverables` + `synthesize_answer`
  (answer_summary on the card), answer-first Maro Telegram message (human
  tone), Hermes push payload carries goal + answer + full deliverable
  content (data tone) and a Hermes brain turn composes the user DM from it.
  Standing test: every completion surface must answer the original ask;
  LLM consumers get data, humans get prose.
- **2026-07-17 (Jeremy, session close) — pattern-not-example caution +
  holistic drift review commissioned.** On the shipped delivery-loop work:
  "Hopefully this is identifying the right pattern, as opposed to dialing
  in this specific example. Time will tell; it's miles better than it was."
  Standing check for the answer-first/two-tone surfaces: watch the next few
  UNLIKE runs (build goals, ops goals, failures) — the shapes were derived
  from one research run. And: "Might be time to do a wholistic review, and
  honestly see if we are on target, and if the drift moved us in a better
  direction towards our mountain we wanted to climb... or if we ended up on
  the wrong continent looking at a swamp." Review commissioned for a clean
  session — spec in MILESTONES -6 (cold-read the repo without conversational
  backstory; verdict on drift vs north star; honest, including "wrong
  continent").
- **2026-07-17 (Jeremy, late night) — SSO as the floor for public surfaces
  (standing security posture).** On exposing the viz server at
  mc.feifdom.com: "This is just-in-case security, but that's how bad habits
  form I suppose... I'm as guilty as the next guy of wanting my programmer
  hack around auth. Maybe we should assume SSO as the floor, with
  implementation changeable later." Flat-open + basic_auth-as-enough both
  rejected for anything public-facing. Shipped same night: Caddy +
  caddy-security on the maro box (auth portal + JWT policy gating /maro;
  local identity store first, GitHub OAuth as the documented drop-in —
  deploy/caddy/README.md). Standing rule: a new public surface starts
  behind the portal, not with a TODO.
  **Softened next morning (Jeremy, on the way to work):** "in theory I
  like the idea of doing all of this, in practice this just feels like...
  work. :) misleading, painful, and hoop jumping for some measure of
  security that may or may not matter. Let's clean it up, still a good
  floor if we can get it going well. If not, we can probably be read-only
  pages with no auth." Reading: the floor stands ONLY if it stays
  low-friction; the sanctioned fallback for the read-only viewer is
  TLS-only with no portal (a config deletion — README "Fallback posture").
  GitHub OAuth staged same morning (Caddyfile.github-oauth +
  enable-github-oauth.sh); the only remaining step is Jeremy's 2-minute
  OAuth-app creation. Friction on auth work is a signal to retreat, not
  push through.
  **Landed (2026-07-17 morning, from work):** Jeremy created the OAuth app
  and a dedicated subdomain (maro.feifdom.com via Namecheap) — GitHub
  OAuth is now LIVE (portal at /auth, viz at the subdomain root, cert
  obtained, external probe verified). mc.feifdom.com erased from configs
  and docs at his direction — "that was always intended for my kids'
  minecraft server :)" — old /maro links dead by choice, no redirect.
  Bootstrap password moved to ~/claude/credentials-backup/caddy/ per his
  ask; OAuth is the primary login, local webadmin is break-glass.
  **Verified by Jeremy same day: "github SSO is working as intended.
  Good work." Arc closed.** Port-obscurity (7777→443 NAT) evaluated at
  his ask and declined — thin gain (CT logs already publish the
  hostname; SNI-gating + default-deny auth are the real layers), real
  friction (non-standard ports blocked on work/hotel networks; 80 must
  stay open for ACME regardless). On the full-tailscale alternative:
  "I really like the full tailscale stack, I just balk at needing
  custom setup at each point along the way" — public + SSO is the
  accepted steady state; tailnet retreat stays documented, not planned.
- **2026-07-17 (Jeremy, wrapping the session) — bitter-lesson lens added
  to the drift review.** "I think we've got something that works, great
  in some areas, just enough in others, and the bitter lesson trumps
  about half of what we're trying to do already... harness engineering
  is hard." Not a work order — an input: the clean-session holistic
  review (MILESTONES -6) should sort the machinery by what survives
  model improvement vs. what compensates for weaknesses that are
  evaporating, and name which half each major arc is in. Spec updated
  same day.
- **2026-07-17 (Jeremy, same wrap) — records weight is now a tracked
  concern.** "I'm a little concerned that my own limitations are
  shackling your ability. our memories and direction seem to be getting
  heavier and heavier. Worth a cleanup pass there as well?" Assessment
  given: the weight is the discipline we chose, not his limitations, and
  it splits — auto-memory is Claude's to garden (safe tranche done same
  day: 15 dead-arc files archived out of the injected index); the repo
  direction docs (GOAL_BRAIN ~3.3k lines, append-only Decisions,
  MILESTONES, BACKLOG) get their distillation pass AFTER the drift
  review, using its findings — compressing first would erase the drift
  evidence the review reads. Open design question for that pass: how an
  append-only Decisions section compacts without losing the
  reversal-chain property.
- **2026-07-17 (Jeremy, via Telegram/poe relay, morning test run) —
  Claude is the preferred go-to backend; OpenRouter is PoC-context, not
  the default route.** "Maro shouldn't be using openrouter first, that's
  more PoC context, claude is our preferred go-to. Is that a
  configuration setting or how we're choosing to run the orchestration?"
  Answer: it's config (`model.backend_order`, ~/.maro/config.yml), and
  Claude/subprocess was ALREADY first — the azure-finch run's alerts
  named only "OpenRouter → OpenAI" (the failover tail), hiding the real
  chain (subprocess output-cap error → dead OpenRouter → dead OpenAI).
  Applied: openrouter + openai removed from this box's backend_order
  (both verified credit/quota-dead 2026-07-17; re-add is one documented
  line after topping up); alerts now carry the full failover chain + run
  identity; billing/auth-dead backends circuit-break for 15 min
  process-wide; cap-overrun classified request-shaped (no failover);
  answer synthesis grounded in the run's own verdict; batch steps now
  ledger-recorded and the budget breaker prices cache-aware (the $2.41
  phantom total that hard-stopped azure-finch one step early vs $0.406
  real spend).
- **2026-07-17 (session, afternoon re-run zesty-ash 75a88777) — verify
  must judge evidence, not narration.** Jeremy re-dispatched the X-post
  research after the morning fixes ("in theory this has been fixed up.
  Want to run that again with maro?"). The morning fixes held (no billed
  failover, no alert spam, sane cost, verdict-grounded answer). The run
  still ended stuck on a single wrong premise propagating three times:
  ralph verify sees only result[:1200] narration — the worker had
  delivered the root post body to artifacts/step-2-output.txt, verify
  demanded it "in the result" and FAILed the step; the blocked-step
  guard then matched "not found" in the step's *research narration*
  (about a third-party repo) and converted the retryable verify FAIL
  into a terminal MISSING_INPUT stuck; the goal verdict then trusted the
  poisoned DEAD_ENDS entry over the artifact ("root post never
  captured" — false). Applied same day: artifact-evidence note (fresh
  artifacts/ listing w/ size + excerpt) threaded through the whole
  validator ladder; ralph-verify blocks exempted from the missing-input
  short-circuit; CLAUDE_CODE_MAX_OUTPUT_TOKENS floored at 1500 on
  no_tools subprocess calls (the CLI cap counts thinking tokens — the
  300-cap scope call died before the model could reason, degrading the
  run's scope); scope-raw-FAILED.txt debug dump relocated from project
  artifacts/ (where it ranked as a user deliverable and the morning
  planner planned a step around reading it) to the run build dir.
  Still open, report-only: closure Check-1 pipe-char false positive is
  a recurrence of the known static-probe bias; X reply-thread capture
  missing across both runs (captured in docs/CAPABILITIES.md).
- **2026-07-17 (session, evening run calm-echo 258859a8) — planning
  calls are pure-text contracts; the planner must never hold tools.**
  Jeremy: ">20 minutes... seems like way too long to get some X thread
  data and then think about the results and send back an answer." The
  23-min wall-clock decomposed to: 79s pre-loop; 1001s loop (511s of
  real 10-step work + ~490s overhead); ~285s post-loop. The two big
  overheads were both self-inflicted: (1) every planner decompose call
  ran the subprocess adapter WITH TOOLS — the boundary-expansion
  decompose (remainder text: "Plan and complete the remaining bounded
  work…") therefore EXECUTED the goal instead of planning it, a ~4-min
  rogue side-quest that wrote a wrong FINAL_REPORT.txt/VERDICT.md
  ("repo not found", "OpenClaw doesn't exist") into the project dir;
  curation's size-ranked deliverable locator then preferred that draft
  over the run's real, correct FINAL_RESPONSE.md (repo found, README
  hand-verified, 3 true/2 misleading, MIXED) and the Telegram answer
  contradicted the run's own verdict — the NOT-USEFUL/MIXED mismatch
  Jeremy spotted himself. (2) validate.shadow_eval (Jeremy's bounded
  2026-07-16 batch) added ~11 extra `claude -p` verify calls ≈ +3.5
  min. Fixes same day: no_tools=True + purpose tags on all six planner
  call sites (seam test pins it); deliverable ranking now prefers
  recency over size within hint tiers + "response"/"verdict" hints;
  shadow batch closed early with analysis (89 rows, 92.1% agreement,
  all 4 false_passes narration-vs-evidence — provenance is the lever,
  not thresholds), reminder timer disarmed. BACKLOG #27 files the
  repo-wide no_tools sweep (~70 unmarked call sites).
- **2026-07-17 (Jeremy, via Telegram/Hermes) — DECREE: link triage is
  conversational compute, not research-paper compute.** Verbatim:
  "Maro was borne from my laziness... essentially I'd like to drop a
  link somewhere and ask 'is this worth my time?' 2 mins of looking at
  it manually and I think the answer is 'yep, sure is'. I'm looking
  for conversational compute, not research paper level compute I
  think. In that sense, totally off the mark. It does... do things
  though. :)" Context: Hermes proposed routing link-triage AROUND Maro
  (lean Hermes-side read, save Maro for multi-source research); Jeremy:
  "Not sure I agree with hermes' conclusion, but it's in the vague
  direction." Standing constraint reading: the fix is a fast
  conversational lane INSIDE Maro (NOW-lane-shaped: fetch via the
  reply-aware rung, one opinionated no_tools read, answer in ~2 min),
  not external routing — per the don't-manage-orchestration-from-
  outside decree. The heavyweight claims-matrix pipeline stays for
  goals that actually want it. Capability captured in
  docs/CAPABILITIES.md.
- **2026-07-17 (Jeremy, Telegram follow-up) — AMENDMENT to the entry
  above: Hermes one-shots NOW-shaped asks at the interface; Maro is
  for genuinely multi-step work.** Verbatim: "I'm okay with that. In
  fact, I'd actually prefer you to just 1-shot the things that might
  end up being the maro NOW items." This supersedes the previous
  entry's "fast lane INSIDE Maro" reading — the interface brain makes
  the fast practical call first, by Jeremy's explicit preference. The
  condition he attached is the system's origin story: Maro got built
  because chat assistants reflected his thesis back instead of doing
  the work ("'Hey, go look at this thing!' 'That's a thing but you
  should figure it out' 'yep you're right...! uh.. thanks...?'").
  Interface-level triage must therefore DO — inspect, judge, act; the
  moment it drifts back to evasive mirroring, the work belongs in
  Maro. Maro-side implication: the dedicated triage lane is demoted
  from next-chunk to proportionality work — link-shaped asks that DO
  reach Maro (CLI, dispatch, Hermes escalation) must not get the
  23-min claims-matrix hammer, but nothing new gets built for this
  now. Long-haul framing recorded: Jeremy is working orchestration
  from both ends (Claude CLI = builder's end, Telegram/Hermes = the
  interface he'd prefer to live in) and is watching whether early
  "getting to know you" corrections paint later behavior into
  corners.
- **2026-07-17 (Jeremy, session, third clarification) — the Hermes
  1-shot posture is Hermes-side ONLY; Maro stays fully capable.**
  Verbatim: "I'm fine taking that position _with hermes_ to have
  hermes attempt 1-shots over orchestration. I think for the maro
  side, I still want that to be fully capable; we don't know how
  that's going to be fed information, I'd love to make sure we do the
  best we can there and not assume another LLM is going to vet or
  frame things for us (and I hope that's normally how it works)."
  Supersedes the "nothing new gets built" reading in the entry above:
  Maro must handle conversational/link-triage asks well ITSELF — no
  assuming an upstream brain vetted, framed, or triaged the input.
  The conversational fast lane inside Maro (reply-aware fetch → one
  opinionated no_tools read → ~2-min answer; "can't see it from
  here" honesty; no claims matrix unless the goal asks for
  verification depth) is sanctioned next-chunk work, built this
  session.
- **2026-07-17 (session, answer-first delivery) — deferred learning
  moved AFTER the run_completed notify; hardy-magpie smoke debris
  deleted on Jeremy's delegation.** Follow-through on the delivery-loop
  decree: lessons + skill crystallization (~90-120s of subprocess
  calls) were sitting between a finished answer and the user hearing
  it — calm-echo's post-loop tail was ~285s. Now handle.py registers
  the learning (`_POST_NOTIFY_LEARNING`), the finalize block drains it
  after the notify emit, then refreshes the run card's
  lesson-consuming fields (audit-repair contract). Quality-gate
  escalation drains early — its retry's decompose recalls the failed
  loop's lessons (dependency mapped in BACKLOG P3 analysis). Remaining
  pre-notify cost = closure + curation (~120-160s); closure∥quality-
  gate and closure-through-hosted-free-ladder stay unbuilt (the
  latter is a judgment-quality tradeoff, not to be done silently).
  Also: Jeremy delegated the hardy-magpie keep/delete call ("your
  call... if not, delete it") — deleted: 112K of killed-run debris
  from the routing-bug smoke test, nothing the three completed runs
  of the same question didn't already cover.
- **2026-07-18 (Jeremy, quick decision batch — no big arcs, queue check
  session) — introspection goals vs the container executor DECIDED:
  provision the container, don't route around it.** The 2026-07-16
  finding (brisk-saffron: a dispatched self-diagnostic ran inside the
  container executor with no view of host run records, no dispatch CLI,
  no maro binary — 2.8M tokens / 28min exhaustively proving its own
  isolation) is resolved per Jeremy: **"Install in the container only
  for the runs that need access."** Not host-side routing (the
  classifier-override escape hatch was offered and not taken), not a
  blanket read-only mount for every run — containment posture stays the
  default; introspection-shaped runs get their container provisioned
  with what the goal needs (maro CLI in-image/in-mount + workspace run
  records, read-only). Implementation shape: detection signal for
  introspection-shaped goals + per-run mount-map/provisioning extension
  in container_exec. Buildable chunk; BACKLOG SP-finding bullet updated
  with the decision.
  **Same batch, two staleness corrections instead of decisions:** (1)
  `director_evaluate(trigger="injection")` was re-asked as "pending" off
  the stale Threads line — actually decided + enabled 2026-07-16; line
  corrected above. (2) The escalation-continuation depth-cap "design
  call" (MILESTONES 2026-07-13 checkpoint prose) was already resolved
  same-day by the recursive-checkin decree (check-in-and-continue, no
  hard cap). Jeremy's fresh answer independently re-affirmed the shipped
  posture — verbatim: "sub-goals should have space to run, infinite
  loops are something else; with recursive context the LLM should be
  able to avoid these loops, without context it's much easier to spin --
  as long as it's clear to the decision makers it likely will be fine."
  His visibility condition already holds: `handle_escalation`'s prompt
  carries `Continuation depth: {depth}` (director.py). No change; the
  re-affirmation is the record.
- **2026-07-18 (work-ahead session, same day as the decision batch) —
  BACKLOG #27 no_tools sweep SHIPPED + introspection decree BUILT.**
  (1) Every `adapter.complete` site in src/ classified: ~55 contract
  calls now pass `no_tools=True` + `purpose`; 6 intentionally-agentic
  sites carry `# agentic:` markers; conductor's NOW lane ported to the
  handle-style URL pre-fetch so its no_tools pin is safe on the live
  Telegram/Slack path. Standing lint `tests/test_no_tools_contract.py`
  (literal `no_tools=True` + `purpose` or `# agentic`, vacuity-guarded)
  makes the classification permanent — full record in BACKLOG_DONE #27.
  (2) The decree ("Install in the container only for the runs that need
  access") shipped same-day: `intent.classify` → `introspects_self`
  (4-tuple) → `run_agent_loop(introspection_access=…)` → run-scoped
  ContextVar → `container_exec.introspection_provision()` mounts
  workspace `runs/` + maro source read-only with env markers, gated by
  `executor.introspection_access` (DEFAULTS.md row; inert unless
  containers are on), all-or-nothing fail-closed, symlinked `runs/`
  refused. Design doc §4 amended; CAPABILITIES self-diagnosis row
  promoted aspirational → target (verified needs a live dispatched run
  once containers turn on). Adversarial review (Codex ×3): 1 High + 4
  Medium confirmed and fixed pre-commit (conductor/pipeline/team/direct
  dropped the grant; partial-open provisioning; lint substring
  weakness); known-gap accepted: `--lane` force skips the classifier so
  forced runs never get the grant.
- **2026-07-20 (Jeremy, after a Hermes backlog commit stranded in mini2's
  /tmp) — Hermes propose-only lane: the trust boundary is merge-to-main,
  not push.** Hermes keeps no GitHub credentials (mini2's persistent
  clone at `~/.hermes/repos/maro-orchestration` is https = fetch-only by
  construction); it works on `hermes/<topic>` branches and hands them to
  this box via a new `land` verb on the dispatch gate. Docs-only
  proposals (all `*.md`) auto-fast-forward to main; anything touching
  code stops as a pushed `hermes/*` branch + PR URL awaiting Jeremy —
  his stated concern: hermes must not rewrite the orchestration that
  governs it on its own. Built + verified e2e same day (docs commit from
  mini2 landed on main through the lane; code commit correctly held at
  branch; gate rejects non-`hermes/*` refs). Mechanics + ops:
  `deploy/hermes/PROPOSE_LANE.md`; Hermes-side contract:
  `~/.hermes/skills/orchestration/maro-propose/SKILL.md` (mini2).
- **2026-07-20 (Jeremy, clarifying the above) — "PRs for Poe; maro box
  continues as before."** The propose-lane PR-and-human-review gate applies
  to the *Poe/Hermes* lane ONLY (dispatched-autonomous work on mini2, no
  directing human). It is NOT a rule for the maro box's own directed
  sessions — those have Jeremy in the loop by construction and land code
  directly to main. Root cause of the friction that surfaced this: the maro
  box's `gh` API token has been dead (401) since ~2026-07-14, so a Claude
  session here couldn't even open a PR and Jeremy had to pull-and-merge the
  R6 branch by hand — a broken credential masquerading as a policy gate. Fix
  shipped same session: `scripts/land.sh` (ff-only push to main over the SSH
  remote, never force, refs-only so it's concurrency-safe) is now the blessed
  maro-box landing path; no GitHub API token needed and the dead `gh` token
  stays moot for it. mini2 stays zero-creds/propose-only; the Hermes
  code→review guard is untouched. CLAUDE.md end-of-chunk discipline +
  `PROPOSE_LANE.md` scope note updated to match.
- **2026-07-20 (Jeremy, swarm-review session — five decrees, from the
  Cursor agent-swarm-economics review):** (1) **Give up the cheap split**
  — two execution-lane defaults (handle=CHEAP vs loop=MID) is "a
  non-decision" under flat-rate; unify execution at MID (CHEAP stays for
  non-agentic classification/triage calls). (2) **Local LLMs are "a nice
  OSS dream but really just in the way"** — remove the ollama/local-model
  wiring, revisit "in a year or three"; stay LLM-agnostic at the adapter
  seams. (3) **Personas stay** — "having the ability to examine the same
  facts from different angles IMO is key to this process
  (taste/judgement)... seems more important than just not yet used here."
  Disuse is not a cull reason. (4) **Skip the 3-arm spend experiment** —
  "I'm concerned we're getting bogged down in the implementation... as
  opposed to the general pattern of discretion. Spend is one discretionary
  lever... a simple one that (sort of) equates with capability." (5)
  **Fork contract is not parent-always-wins** — children need an
  evidence-based escalation path against parent-owned decisions (three-way
  ownership: leaf-local / parent-owned / escalation-trigger).
  Implementation: the swarm-review arc plan, chunks provisional until the
  plan-revision checkpoint.
- **2026-07-20 (Jeremy) — history-before-implementation: the knowledge
  journey.** Before any swarm-review chunk: "write a historic timeline log
  of our knowledge journey... essentially going back in time via git...
  pull out all the stops." Rationale: "I trust your judgement but sadly,
  not yet your context." DELIVERED 2026-07-21 (3333512):
  `docs/KNOWLEDGE_JOURNEY.md` + `docs/history/knowledge-journey/` (13 era
  files + side-channels companion), 37 excavate/verify/write agent chains
  plus a completeness critic whose confirmed findings were folded back.
  The edges-for-plan digest (KNOWLEDGE_JOURNEY.md final section) is the
  input to the checkpoint that gates the implementation chunks.
- **2026-07-21 (Jeremy, plan-revision checkpoint) — revival dispositions,
  confirmed by clean review.** On the history's candidate revivals:
  "let's go with your recommendation here, including adding personas to
  phase 5. Let's run a clean adversarial sub-agent review against the
  decision, along with the plan doc, and give them the history if they
  want to go digging. Then we can confirm or correct our decision
  there." Dispositions: IN — typed finding codes (chunk 1),
  effort-estimate compute (chunk 7; the consent *message* belongs to the
  session-protocol thread), persona-dispatch owner (chunk 5b, ships
  before the lenses that consume it). BACKLOG (entries added in chunk 1)
  — Also-After hooks, RISKS.md as reviewer input, decision-gated-ping
  escalation shape, blind persona-panel tiebreaker, signal-source
  rotation runtime half, exception-vs-break lifecycle, REASSESS
  7-question overlay, recurring doc-census cadence, promotion-side
  starvation, hand-adjudicated burn-in ritual. The clean two-agent
  review (grounding checker + adversary, history available) CONFIRMED
  the dispositions — no BACKLOG item is load-bearing for chunks 3-5 —
  and corrected the plan: Phase 0.5 battery → real-instance blinded
  ground truth; patterns doc out of skills/ (skill_loader globs repo
  skills/ into runtime prompts); chunk-4 prerequisites made explicit
  (rules_cited/lesson-ID stamps in RECALL_PERFORMED, Stage-5 provenance
  pointers, verdict_trust()-only closure reads, UNDECIDED→unjudged);
  chunk 8 split report-early/enforce-late with a registration
  convention; chunk 5 split 5a/5b; typed codes moved to chunk 1. The
  adversary also caught three citation morphs in the plan and
  KNOWLEDGE_JOURNEY.md — citation inversion, the very class the 05-12
  taxonomy names — all verified and fixed same day.
- **2026-07-21 (Jeremy) — Phase 0.5: patterns doc before chunk 1,
  test-driven, two halves.** "There is a part of me that wonders if
  we're doing this backwards and need to come at this in a more test
  driven approach... I wonder if we need to create a skill a-la the
  bitter lesson, regarding the patterns we have already identified, then
  run some tests (maybe a phase 0.5?) to see if that skill performs as
  we might expect." Refined in-session: "you're focused a bit on the
  verification half; which is thematic here... but I'd also like to
  capture the patterns for the up front part of the skill as well."
  Adopted: `docs/DEV_PATTERNS.md` (docs/, NOT skills/) with **up-front
  commitments** applied at plan/design time (cuts-first, consumer-first,
  decree-with-tripwire, done-means, scope/inversion) AND **audit
  checks**; graduation rule — every check carries a `deterministic-home:`
  tag and LEAVES the doc when its deterministic home ships (enforced by
  the chunk-8 census, not prose). Battery runs BEFORE chunk 1 because
  the tree's real violations (decisions.jsonl readerless,
  contradict_pattern dead writer, playbook horizon bug, mode:thin
  unadjudicated) are the blinded ground truth and chunks 2-4 destroy
  it; scored on unprompted surfacing AND plan shape (consumers/tripwires
  named up front). Pre-registered gate: clear delta → standing CLAUDE.md
  pre-read; ambiguous → ship as non-gated pre-read and say so — do not
  launder noise as a benchmark verdict. Prior instantiations on record
  (playbook, Stage-5 compiled rules) both half-died; the differentiator
  here is the test that can fail.
- **2026-07-21 (Jeremy) — the taste/judgement delegation boundary.**
  "From the parent process's perspective, taste is determining the
  plan/task/'what' the sub-agent attempts. And judgement is the
  validation that we've accomplished what we set out to do." Adopted as
  the naming vocabulary for DEV_PATTERNS' two halves (taste = up-front
  commitments, judgement = audit checks) and as an audit razor: a
  mechanism belongs to the orchestration layer iff it serves
  parent-taste or parent-judgement — if neither, why does the parent
  own it? This upgrades the 2026-03-30 what-vs-how audit (whose
  misclassification of harnessing/collation as cruft the standing
  BACKLOG bitter-lesson-posture note already flags): it retro-predicts
  the 03-31 factory benchmark — everything load-bearing (adversarial
  review, verify loop, output criteria) is judgement machinery;
  everything removable (persona-as-routing, lesson injection into
  execution, multi-plan ceremony) is neither — and splits personas
  correctly (lens = judgement diversity, stays; routing = execution
  how, removed with no loss).
- **2026-07-21 (session) — factory-branch archaeology closes Jeremy's
  lost-history question.** The bitter-lesson branch survives
  (`factory`, local + origin); findings docs on main
  (`docs/history/2026-03-30-bitter-lesson-analysis.md`,
  `2026-03-31-factory-mode-findings.md`); the constraint-orchestration
  conversation transcript is `docs/conversations/2026-04-16-...md`.
  Two record gaps confirmed: (1) `factory_full_sim.py` v1-v4
  (whole-architecture-as-one-prompt, self-audited, tipped at cycle 6/8
  under stress) never landed on main and its verdict was never written
  — Jeremy's recall: "mixed bag at best; wasn't that different than a
  general LLM prompt; move on with scope+constraint, revisit later" —
  matching Purgatorio arch-10 ("work finished, pushed, lost from the
  record", adjudication still open). Folded into chunk 1's mode:thin
  adjudication item: Phase 49's decision gate finally fires, and the
  verdict gets written down. (2) Crystallization (03-25) and the
  bitter-lesson thread (03-30) share one axis — crystallization is the
  freeze direction (fluid reasoning hardens to code), bitter-lesson the
  melt direction (code re-expressed as prompt; factory was the melt
  experiment) — but no doc or channel links them; the connection lived
  only in Jeremy's head ("in my mind those are the same conversation,
  just continued over time").
- **2026-07-21 (Jeremy) — the star skill: an alpha prompt-only
  mini-orchestrator as standing gut check.** "Let's create a skill
  that's explicitly an alpha prototype... itself a mini-orchestrator in
  this same direction... what I have called a star pattern architecture
  in the past (not linear, but a master process that delegates a task,
  receives an answer, delegates a new task... rinse repeat until the
  process is complete... the process is 0..n steps, not an explicitly
  planned pathway to tread)." Map-reduce recursion explicitly rejected
  for this: "great for simple things and lossy/brittle/heavy/
  unmaintainable for complex processes, especially ones that change
  often." Purpose: "a functioning gut check / test [to] help us clarify
  and identify our patterns as we develop the orchestration
  mechanism... grounding along the way and builds in the bitter
  lesson." SHIPPED same day: `.claude/skills/star/SKILL.md` — master
  owns taste+judgement only (the delegation-boundary razor made
  operational), explicit output criteria required per task (factory
  finding #3 pin), tri-state judgement, surprise capture, serial-only
  (box rule), code-pressure = report-don't-build, pre-registered
  keep/kill adjudication (swarm-arc end or 5 uses — the gate its
  lineage never had). Tangent lane: exercised opportunistically during
  chunks 1-8, never gating them.
- **2026-07-21 (Jeremy) — star is bounded (the node contract) + the
  recursion formulation.** "I think it should be bounded... fixed
  inputs and bounded outputs" (his email-pipeline prior art). And the
  formulation he's been circling: "a recursive pattern of goal ->
  taste + judgement -> result returned is our steps in a nutshell, and
  the recursion comes if the inner taste + judgement is allowed the
  same pattern." Skill amended same day: explicit function-shaped node
  contract (in: goal/done-means/cuts/budget; out: deliverables/verdict/
  residuals result block), recursion stays OFF in alpha with
  pre-registered structural turn-on conditions — same contract shape,
  strictly decreasing budget (well-founded recursion = the
  off-the-rails guard), cuts inherited downward, parent judges child
  against parent-set criteria (fork-fabrication lesson). Session
  analysis recorded alongside: the reason this "can't quite be nailed"
  in maro today is contract asymmetry — maro steps append text to a
  shared ledger instead of returning judged deliverables to a parent,
  so the self-similar node type doesn't exist yet; the
  strategy-selection dream ("pick the proper tool based on known
  context up front") restates as per-node taste with iterative
  deepening as the strategy-agnostic default (the 04-26 "1-shot first,
  decompose as escape hatch" note IS iterative deepening in the
  decomposition dimension, as the verify-fail ladder already is in the
  model dimension); star's result-block strategy rows are the longhand
  corpus that lets strategy choice crystallize later. Direction only —
  no maro implementation scoped; chunk 3 touches the step contract and
  is the natural first seam.
- **2026-07-21 (Jeremy) — the north star named: CGI, not AGI; and the
  possible-now values statement.** "I think what I want is probably
  something more like CGI -- capable general intelligence. I don't want
  a slave mind or to create artificial life; I want something as
  capable as me as a workhorse in the digital space, with all the
  benefits that a computer brings." (Confirms the early not-AGI
  distinction with a positive name.) Paired values statement on the
  meandering path: "what scares me most is decisions NOT to do the
  work (when the work is possible). It's easy/lazy of all of us to
  assume we need better models so we can 1-shot things so often. The
  harder part is the composition thinking... no single step is
  difficult, but the complexity comes in with taste (what we decide to
  attempt) and judgement (what we do about the results)." → the
  possible-now bias, queued as a DEV_PATTERNS taste-half candidate:
  "needs a better model" is a claim requiring evidence of the
  composition that was tried, not a default. Open edge named and
  deferred by Jeremy's own call ("let's worry about that when we get
  further in"): taste/judgement maturation may need consequence-coupled
  reps, not just training data — the bottleneck framing recorded is
  verified-outcome density, not calendar time.

- 2026-07-21 (Claude adjudication, pre-registered gate): **Phase 0.5
  battery verdict = AMBIGUOUS → DEV_PATTERNS ships as a NON-GATED
  CLAUDE.md pre-read**, stated plainly, not laundered as a benchmark
  win. Both arms caught every pointed ground truth (GT1/GT2/GT3) with
  code-derived provenance — ceiling effect; GT4 caught by neither; the
  plan-shape measure was invalidated by a pre-registration flaw (the
  output schema itself forced the shape fields). The battery's real
  yield: six adjudicator-verified NEW findings (V1 recall loop reads
  legacy lesson store not tiered; V2 standing-rule domain vocabulary
  mismatch — all 4 live rules invisible on project-scoped runs; V3
  bridged knowledge nodes forever NODE_CANDIDATE/invisible; V4 Stage-3
  dashboard dict-vs-attr; V5 planner persona wrap discarded whenever
  extras exist — live-impacting under the personas-stay decree; V6
  playbook seed alone overflows the 800-char budget) triaged into the
  arc: V1 = checkpoint-class flag on chunk 6, V2 = chunk 4 prerequisite,
  V5 = chunk 1 candidate, V3/V4 = chunk-1 BACKLOG batch, V6 = chunk 2
  input. Report: docs/history/2026-07-21-phase05-battery.md.
- 2026-07-21 (chunk 1 executed, standing grant): **swarm-review chunk 1
  shipped** — (1) execution defaults unified at MID: handle.py entry
  adapter now `assign_model_by_role("worker")`, scope-lift block and
  `classify_step_model` cheap-vs-mid downgrade removed; verify-fail
  ladder starts mid→power; CHEAP survives only for non-agentic
  classifier/heartbeat/curation calls. Post-escalate tier computation
  fixed in the same pass (was hardcoded `model or "cheap"` — would have
  re-run escalations at the tier that just failed). (2) Local-model
  wiring REMOVED (not disabled): src/local_models.py + bakeoff scripts +
  tests deleted; ladder is Tier-0 deterministic → hosted-free → paid;
  hosted no-verdict escalates straight to paid; revival trigger (hosted
  free-tier churn) + re-entry path documented in the retired
  docs/LOCAL_VALIDATOR.md; corpus fixture kept. (3) Dead config keys
  `model.default_tier/planning_tier/advisor_tier` + `validate.local_*`
  deleted; DEFAULTS.md census green, new `validate.auto_verify` row.
  (4) Battery V5 planner fix (persona wrap no longer discarded when
  extras exist). (5) Typed finding-code vocabulary shipped:
  src/finding_codes.py (CITATION_INVERSION / PHANTOM_SYMBOL /
  THEORY_MECHANISM / GAP_UNDERSTATED) + DEV_PATTERNS convention +
  tests. (6) Report-only wiring inventory saved
  (docs/history/2026-07-21-wiring-inventory.md, 27 stores/events, 8
  agent-reported surprises flagged verify-before-fix; enforcement pin
  waits for post-chunk-3/4 per pin-after-fix). (7) Side-channel
  first-party failure corpus folded into docs/CAPABILITIES.md (era 12's
  named loss candidate). (8) Stale-doc sweep: debate pass, ~5400-line
  claim, bootstrap_context-at-loop-start, all-passes-cheap claim fixed.
  (9) BACKLOG batch adds: 10 revival dispositions, V3/V4, era C-tier
  drops, 8 wiring surprises; VibeThinker/local burn-in item SUPERSEDED →
  BACKLOG_DONE.
- 2026-07-21 (Jeremy verdict, recorded per SF-13; executed by session):
  **factory-mode adjudication — Phase 49's decision gate finally
  fired.** Jeremy's verdict on factory mode, recalled during the
  knowledge journey and never previously persisted: "mixed bag — not
  much different than a general LLM prompt." Dispositions:
  origin/factory branch ARCHIVED as tag `archive/factory-2026-03-31`
  (remote branch deleted, every commit reachable via tag — arch-10
  record-loss flag closed); `mode:thin` + factory_thin.py KEPT as
  operator-only escape hatch + benchmark instrument, default tier bumped
  cheap→MID per the execution-floor decree; factory_minimal.py KEPT as
  the single-completion benchmark baseline. Record:
  docs/history/2026-07-21-factory-adjudication.md.
- 2026-07-21 (session, executing Jeremy's /goal "use the adversarial
  review skill after each chunk"): **chunk-1 adversarial review ran
  post-land** (3 Codex lenses vs b6fd488) — verdict CONTESTED, 6 verified
  findings fixed same-day, 1 rejected. The MID-floor decree had THREE
  more silent defeats beyond the two found at ship time: factory_thin's
  standalone CLI defaulted cheap, blocked-step hint/split recovery built
  cheap adapters, and the intent classifier inherited the MID worker
  adapter (role leak in the other direction). All tier choices now route
  through assign_model_by_role. Also: parse_finding_codes is strict by
  default (typo'd stamps raise, not vanish); validator_roi counts
  hosted-free-decisive. Notable: 0/7 reviewer claims hallucinated
  (historical rate 30–78%). Record:
  docs/history/2026-07-21-chunk1-adversarial-review.md.
- 2026-07-21 (chunk 2 executed, standing grant): **swarm-review chunk 2
  shipped — playbook repair, the live bug.** (1) `inject_playbook` is
  ranked selection: learned-over-seed, newest learned first, dedup by
  normalized entry core, greedy 800-char budget fill — the fixed
  head-window horizon bug (wiring row 17's injection half) and battery
  V6's seed-overflow are both dead; `parse_entries` is the shared entry
  parser; pins in test_playbook.py. (2) `curate_playbook` curation verb
  rides `maybe_consolidate` (the dream cycle): free deterministic dedup
  always; size-gated (>4000 chars) CHEAP-tier LLM compress with hard
  validation (all `## ` headers + all `*(from ...)*` attributions
  preserved verbatim, ≤1.1× length, ≥60% bullet retention) — invalid
  compression keeps the deterministic result; archive-before-write to
  `playbook_history/` (append-only, abort if archive fails — never
  rewrite what you can't restore); `PLAYBOOK_CURATED` captain's-log
  event; kill-switch `playbook.curation_enabled` (DEFAULTS rows added,
  census green). (3) One-time live curation: 5239→3172 chars, test-era
  spam dropped, original archived verbatim in playbook_history/;
  decree-stale seed Cost line now states the MID floor. (4) Live-path
  verified: loop recall block (`as_loop_block`, the exact decompose
  feed) renders learned entries ranked in, zero dupes. (5) Side-find →
  BACKLOG: record-mode NEVER fires on single-backend boxes (the record
  seam exists only in FailoverAdapter; this box's bare subprocess
  adapter skips it — every run shows `n_calls: 0` despite default-ON).
  (6) Wiring row 17's director half stays BACKLOG'd consumer-first.
- 2026-07-21 (session, executing Jeremy's /goal per-chunk review
  discipline): **chunk-2 adversarial review ran post-land** (3 Codex
  lenses vs 257b34d) — verdict CONTESTED, 6 verified findings, all
  accepted at least in part, fixed same-day; 0/6 hallucinated (second
  consecutive clean round). The two that mattered: `curate_playbook`
  held the playbook write lock across the LLM round trip (concurrent
  appenders would FileLockTimeout and lose entries — restructured to
  snapshot-under-lock → compute unlocked → compare-and-swap, skip the
  cycle if the file moved); `_valid_compression` was soft exactly where
  it claimed to be hard (int-floor bullet ratio, substring headers,
  set-collapsed attributions — now ceil + exact-line and
  occurrence-counted Counter checks). Also: dedup now runs in rank
  order so a learned copy beats a seed duplicate; the 800-char budget
  is a real cap (top header counted, `len<=max_chars` pinned);
  `atomic_write` on both playbook rewrite paths; newest-first visible
  in rendered output. Record:
  docs/history/2026-07-21-chunk2-adversarial-review.md.
- 2026-07-21 (chunk 3 executed, standing grant): **swarm-review chunk 3
  shipped — decisions.jsonl finally has writers.** Read side was always
  live (recall loop-slice substrate #3 → `inject_decisions`); the store
  had never had a runtime writer. Three now: (1) **executor DECISION
  directive** — `decisions` field on complete_step (max 2/step,
  decision≤200/rationale≤300 chars), fan-out in `_process_done_step` to
  the durable journal (`record_decision`), shared context
  (`decision:{step}:{n}` keys — carried UNCOMPRESSED to every later
  step's prompt via `decisions_block`; completed_context's 100-char
  compression was how design calls used to evaporate), and the thread
  brain (`step N [executor]:` lines). (2) **Scope proxy commitment** —
  the director-proxy interpretation closure already treats as binding
  is journaled at the creation seam (scope.py retry-success path), not
  at closure — root-cause placement. (3) **SF-13 decree pipe** —
  `PYTHONPATH=src python3 -m knowledge_lens decision "<decree>"
  --rationale "<why>"`; CLAUDE.md SF-13 rule amended; blank-domain rows
  match all project-scoped reads (pinned). Consumer-first liveness pin:
  record → recall → text in as_loop_block AND as_context_block, no
  mocks on the read side (test_recall.py TestDecisionLiveness). Fork
  contract design note added to THREAD_ARCHITECTURE.md (three-way
  ownership: leaf-local / parent-owned / evidence-based escalation
  triggers — NOT parent-always-wins, per Jeremy); ancestry write-side
  unification BACKLOG'd as the fork prerequisite. REFACTOR_PLAN's
  "record_decision (no writer)" removal candidate struck (currency
  rule).
- 2026-07-21 (session, executing Jeremy's /goal per-chunk review
  discipline): **chunk-3 adversarial review ran post-land** (3 Codex
  lenses vs fe0072d) — verdict REJECT-as-reviewed (unanimous high),
  remediated same session; 6/6 findings verified real, 0 hallucinated
  (third consecutive clean round). The one that mattered: the decision
  fan-out lived only in the sequential `_process_done_step` — ALL
  parallel surfaces (batch, fan-out, DAG) silently dropped executor
  decisions; the live smoke run was sequential, which is exactly how
  that class of gap survives smoke. Fixed by extracting
  `record_step_decisions` as the single seam + wiring both parallel
  outcome walks (pins in test_parallel_batch_indices.py). Also:
  `locked_append` on the journal (multi-writer ledger, house
  convention); SF-13 CLI fails closed (`strict=` kwarg — loop callers
  stay best-effort); scope-proxy decisions domain-scoped to the project
  (blank-domain = every project's recall) + `goal_context` joined into
  the TF-IDF ranked text; decisions_block got a 2000-char chronological
  budget with omission note; max-2 cap now counts VALID decisions (the
  review caught my pin encoding the bug as spec). Deferred consciously:
  decision-kind taxonomy (three writers don't justify a type system),
  mid-run decision supersede/dedup lifecycle. Record:
  docs/history/2026-07-21-chunk3-adversarial-review.md.
- 2026-07-21 (chunk 4 executed, standing grant): **swarm-review chunk 4
  shipped — contradict_pattern finally has a runtime writer; the
  contested→refight lifecycle Jeremy designed on the entropy thread
  (2026-06-11) is reachable for the first time.** The chain: recall's
  loop slice stamps durable IDs (`rules_cited` via new
  `standing_rules_with_ids`, `lesson_ids_cited`) into RECALL_PERFORMED
  and writes run-keyed `source/recall_citations.json`;
  `stamp_outcome_verdict` (the single post-hoc verdict funnel) emits
  CONTRADICTION_CANDIDATE when a FULL-trust `goal_achieved=False` lands
  on a citation-bearing run — era-10 law honored: consumed ONLY through
  `verdict_trust`, so directional/excluded verdicts can never seed a
  contradiction (pinned); `adjudicate_contradiction_candidates`
  (knowledge_lens, evolver cadence via run_skill_maintenance BEFORE the
  refight scan, cap 3/cycle, config
  `knowledge.contradiction_adjudication_enabled` default ON) renders a
  tri-state verdict — only exact "yes" calls contradict_pattern;
  UNDECIDED = unjudged, never contested (checkpoint law iv, pinned);
  unparsable = no event, retriable; artifacts-all-gone = deterministic
  moot-clear. End-to-end pin: failing run citing a rule reaches
  RULE_REFOUGHT in ONE maintenance pass (test_contradiction_wiring.py).
  Prerequisites shipped in-chunk: (v) battery-V2 domain fix — promotion
  now writes domain="" (task-type vocabulary never matched the
  project-filtered reader; the 4 live rules were invisible to every
  project-scoped run since promotion) + live migration agenda→"" with
  archive copy (standing_rules.jsonl.pre-domain-migration-2026-07-21),
  verified: all 4 now inject on project reads; (ii) era-09 provenance —
  StandingRule.source_lesson_ids keeps ALL contributing lessons at
  promotion (source_lesson_id stays as first-contributor compat).
  EVENT_TYPES 66→68. DEFAULTS.md row + census green; arch skill
  updated. Full suite green (185 items).
- 2026-07-21 (session, executing Jeremy's /goal per-chunk review
  discipline): **chunk-4 adversarial review ran post-land** (3 Codex
  lenses vs afe5c5a) — verdict REJECT-as-reviewed, remediated same
  session; 8/8 findings verified real, 0 hallucinated (fourth
  consecutive clean round). The two that mattered: (1) candidate
  starvation — `query_log(limit=100)` truncates newest-first, so
  "FIFO" was "oldest of the newest 100" and >100 pending made the
  oldest permanently invisible (fixed: unlimited reads, pinned with a
  105-candidate test); (2) collateral contestation — one run-level
  scalar verdict fanned out to every cited artifact, but a run cites
  its whole injected bundle (fixed: per-artifact `contradicted_ids`
  attribution, validated subset, yes-naming-nothing = unparsable-
  retry). Also: non-blocking cycle lock + batch dedup by loop_id
  (maintenance runs at EVERY finalize — two concurrent finalizes could
  double-contest); citation join moved from ambient run-dir ContextVar
  to durable `runs.resolve_run_dir(loop_id)` (audit re-stamps now join
  correctly instead of degrading); refight evidence enriched with the
  adjudicated events' failure/reasoning (was judging against a bare
  tally); `applied` list keeps lesson no-ops honest; retirement carries
  full `source_lesson_ids`; DEFAULTS/arch-skill wording corrected
  ("evolver cadence" → per-finalize truth). Deferred consciously:
  lesson-store contested tier (no consumer), maintenance cadence
  redesign (pre-existing architecture). Record:
  docs/history/2026-07-21-chunk4-adversarial-review.md.
- 2026-07-21 (chunk 5a executed, standing grant): **swarm-review chunk 5a
  shipped — the quality gate's free rung returns as a stacked
  second-family check, not a substitute.** The removed local Tier-0
  (chunk 1) SUBSTITUTED a free verdict for the paid one when decisive;
  the plan's stack-don't-substitute correction lands as gate Pass 1.5:
  on a paid Pass-1 PASS, one hosted-free call (existing `hosted_free`
  ladder — Groq llama / Gemini flash-lite, $0, consent-gated by
  `validate.hosted_free.enabled`) judges the SAME payload, and the
  agreement outcome (AGREE / DISSENT / UNDECIDED / NO_VERDICT) is
  recorded as QUALITY_GATE_SECOND_FAMILY (paid+second verdict pair,
  source, latency, loop_id) + `QualityVerdict.second_family`. Flag-only
  invariant pinned: dissent NEVER changes verdict/escalate — authority
  comes from A/B agreement data (chunk-7 readout) or not at all; a
  weak-judge ESCALATE below `validate.hosted_free.min_certainty` maps
  to UNDECIDED (validator-ladder semantics: a weak judge cannot flag);
  received-but-unparsable = NO_VERDICT, still emitted so the readout
  sees the true denominator. Killswitch
  `quality_gate.second_family_check` default ON (flag-only, $0).
  Documented expectation per plan: modest lift — all 4 measured gate
  false-passes were narration-vs-evidence, already caught by claim
  probes. EVENT_TYPES 68→69; 11 pins; live-verified against real
  Gemini flash-lite (649ms, AGREE row in the real captain's log).
- 2026-07-22 (session, executing Jeremy's /goal per-chunk review
  discipline): **chunk-5a adversarial review ran post-land** (3 Codex
  lenses vs 441f4cf) — verdict **PASS, the arc's first** (chunks 1–4
  were CONTESTED/REJECT); 4/4 findings verified real, 0 hallucinated
  (fifth consecutive clean round). Fixed: quoted-string "false" couldn't
  disable `quality_gate.second_family_check` (config.get returns raw
  YAML nodes; normalized like `hosted_free_enabled()`, pinned);
  positive-path tests were reading real box config (now hermetic via a
  forced-config seam). The finding that mattered most grew under
  verification: one missing event-contract row turned out to be **13
  event types** absent from docs/CAPTAINS_LOG_EVENTS.md (drift since
  2026-06-24, spanning three arcs) — all 13 backfilled against their
  actual emit sites and a census tripwire added
  (test_event_contract_doc_covers_all_types, DEFAULTS-census precedent
  applied to the event contract). Rejected consumer-first: typed
  SecondFamilyVerdict dataclass (the durable contract is the event row;
  no in-process consumer yet). Record:
  docs/history/2026-07-21-chunk5a-adversarial-review.md.
- 2026-07-22 (chunk 5b executed, standing grant): **swarm-review chunk 5b
  shipped — evidence-diverse lenses, in the decreed order.** (1)
  `persona_dispatch.py` ships FIRST as the owned one-shot persona×prompt
  verb (re-improvised by hand ≥4 times per eras 09/11): `dispatch_prompt`
  never raises, pins `no_tools=True`, resolves adapter explicit →
  hosted-free → error (no silent paid spend), stamps attribution
  (`hosted_free:<provider>:<model>` vs model_key); `dispatch_panel` +
  CLI (`--persona`/`--panel`, `--model` = explicit paid). (2) Council
  repointed from three same-context prompt costumes to three
  **evidence-path lenses** — transcript-aware / artifact-only
  (context-blind) / probe-armed (concerns are {claim,
  settled_by_command} dicts, probes RUN via claim_probe: dismissed
  concerns dropped, WEAK resting wholly on dismissed claims mechanically
  downgraded to ACCEPTABLE) — with the 05-12 error taxonomy as
  FINDING[CODE] vocabulary WITHIN lenses, not seats. Ladder semantics
  honor weaker-never-overrules: hosted-free round runs first ($0);
  free 2+-WEAK only acts after a paid confirmation round re-votes it;
  free flag with no paid adapter = flag-only (`free_flag_unconfirmed`);
  all-free-seats-unparsable falls back to paid (strict: opt-in never
  silently neutered). New QUALITY_GATE_COUNCIL event (per-seat verdict/
  source/codes/probe_dismissed). (3) Era-04 triad ablation harness
  (`lens_ablation.py`) — retired costume framings preserved verbatim as
  the control arm; token-Jaccard overlap + distinct-catches; first live
  read: costume 0.12 vs evidence 0.20 mean pairwise overlap (harness
  proof, not sizing data — n=1 payload). (4) cross_ref enabled for
  research-shaped goals on hosted-free: `run_cross_ref` grew a
  "hosted_free" lane (flag-only, killswitch
  `quality_gate.cross_ref_research` default ON, inert without
  hosted-free consent); paid strict: lane unchanged (disputes still flip
  verdict). New QUALITY_GATE_CROSS_REF event. EVENT_TYPES 69→71.
  Tests hermetic against the box's live hosted-free keys (autouse
  available()→False pin). Live-verified vs real Gemini flash-lite:
  dispatch CLI 832ms; council round weak=2/3 with PHANTOM_SYMBOL from 2
  seats + one probe self-dismissal, real captain's-log
  free_flag_unconfirmed row (2989ms); ablation both arms ran
  (docs/history/2026-07-22-lens-ablation-smoke.md).
- 2026-07-22 (session, executing Jeremy's /goal per-chunk review
  discipline): **chunk-5b adversarial review ran post-land** (3 Codex
  lenses vs f49666b) — verdict CONTESTED; 7/7 findings verified real, 0
  hallucinated (sixth consecutive clean round). The one that mattered
  most: reviewer-authored `settled_by_command` probes executed with
  shell=True and only PROMPT TEXT enforcing read-only — pre-existing
  exposure, but 5b added a seat whose job is authoring probes, on
  weaker hosted-free models, judging content that can include fetched
  web text (a real prompt-injection chain). Fixed at root:
  `probe_command_rejected()` mechanical guard in claim_probe (shlex
  operator parsing, head-command allowlist, git read-subcommands only,
  find/curl mutating flags blocked, no substitution/redirect/chaining,
  single pipes OK); blocked → `probe_status="blocked"`, concern STANDS
  (unrunnable neutrality — the guard can never dismiss a claim).
  Also fixed: council event now keeps the free round per-seat
  (`free_seats`) when paid confirmation acts (was collapsing the A/B
  evidence to a count — unanimous 3/3 finding); empty paid confirmation
  now records free_flag_unconfirmed, never "confirmed_by_paid" (paid
  disagreed ≠ paid failed to vote); probe-seat string concerns tagged
  `[probe:unprobed]` (kept — absence of a probe never silences a claim,
  but degradation must be visible); cross-ref emits on zero-claim runs
  (denominator data); dispatch_prompt tolerates system=None. Rejected:
  artifact_only-sees-the-goal (deliberate and already explicit in the
  lens contract). Record:
  docs/history/2026-07-22-chunk5b-adversarial-review.md.
- 2026-07-22 (session): **chunk 6 SHIPPED — surprise as a capture
  signal.** (1) Extraction prompt (`_REFLECT_SYSTEM`) now leads with the
  expectation-mismatch question — "what actually DIFFERED from what the
  plan assumed?" — capture the mismatch itself (assumed X, found Y), not
  just the workaround; no new lesson types (taxonomy deliberately
  deferred to the compound-thinking discussion Jeremy queued). (2)
  Novelty term in `record_tiered_lesson`: novelty = 1 − max
  `_text_similarity` vs the store, measured for free inside the existing
  dedup scans; initial score = 1.0 + 0.3·novelty (NOVELTY_BONUS);
  `novelty` stored on the row (old rows deserialize to 0.0);
  `reinforce_score` → `min(max(1.0, score), score + 0.3)` so
  reinforcement never lowers a boosted score (≤1.0 behavior byte-same);
  promotion untouched (sessions_validated gates); killswitch
  `knowledge.novelty_term_enabled` default ON with quoted-"false"
  normalization — flag kills the boost only, novelty is always measured
  so chunk 7's tabulation keeps its denominator. LESSON_RECORDED context
  grew novelty + score. (3) **V1 checkpoint flag resolved as
  not-ambiguous → REWIRE**: recall substrate #1's comment always
  declared "tiered lessons — ranked retrieval" but the read was
  flat-store-only, so tiered-only writers (M3 recovery lessons,
  verify-learn, novelty-scored records) never reached the main-loop
  prompt. Now `query_lessons` (tiered, ranked, decay-scored) leads,
  legacy flat store tops up lessons never dual-written (twin-dedup by
  normalized text), age stamps anchor on recorded_at-or-last_reinforced,
  lesson_ids_cited/chunk-4 contradiction wiring preserved, exception
  fallback to inject_lessons_for_task intact. Liveness pins: a
  tiered-ONLY lesson reaches the rendered block AND its ID lands in
  lesson_ids_cited; flat twin never double-injects. (4) In-chunk
  liveness check on the REAL store (148 medium + 4 long, read-only):
  near-dup of a stored lesson → would-be score 1.011; novel text →
  1.274 (delta 0.263 of a possible 0.30) — the term separates cleanly
  on real data. Consumer-first satisfied per checkpoint amendment
  (in-chunk liveness + the rewired substrate IS the consumer); full
  novelty tabulation lands in chunk 7's readout. Suite green 188 items.
- 2026-07-22 (session): **chunk-6 adversarial review ran post-land** (3
  Codex lenses vs 1236270) — verdict CONTESTED; 7/7 findings verified
  real, 0 hallucinated (seventh consecutive clean round). Fixed same
  session: (1) HIGH — citations could name lessons truncation dropped
  (render loop cited before the block was capped; the chunk-4
  contradiction join would contest lessons the run never saw) → budget-
  aware selection, a lesson is cited only if its line renders; (2)
  novelty was task_type-partition-local → now store-wide (dedup keeps
  its type scope inline — identical text under another type stays a
  separate lesson; a cross-domain repeat no longer collects the boost);
  (3) scan-and-append race (two workers → boosted duplicates) → one
  locked_write critical section (reentrant, matches _mutate pattern);
  (4) untyped tiered query promoted from empty-only fallback to top-up
  (an agenda match no longer masks verify-learn/general tiered-only
  lessons); (5) flat top-up chains agenda+general (all-twin agenda
  result no longer masks general flat-only lessons); (6) query_lessons
  now ranks the FULL live store (the n*5 score-sorted load cap hid
  relevant low-score lessons from the ranker — load-bearing now that
  recall leads with it). Rejected: docs-duplication (the decreed record
  architecture — GOAL_BRAIN/MILESTONES/DEFAULTS-census/skill-currency).
  Six new pins; suite green 188 items. Record:
  docs/history/2026-07-22-chunk6-adversarial-review.md.

- **2026-07-22 — Swarm-review chunk 7 SHIPPED (discretion readout).**
  A judgement report, not a bill (EFFORT language per the budget-posture
  decree; dollars ride as one trailing column). (1) `discretion_readout.py`
  + CLI — read-only, no LLM calls, no flags: per-day EFFORT (calls/tokens/
  model mix), METACOGNITIVE_DECISION retry/replan discretion, RECALL_PERFORMED
  reinjection volume + exact-dup goal texts, gate-family tabulations
  (second-family agreement, council FINDING[CODE] tally — the chunk-1
  typed-code vocabulary's first consumer; cross-ref with zero-claim
  denominators), LESSON_RECORDED novelty distribution (chunk 6's deferred
  readout), background-lane duty cycle, and an explicit "Not computable
  today" block (fan-out justification, semantic goal overlap, NOW-triage —
  the plan's log() requirement, stated not silently dropped). (2) Navigator
  A/B readout: `navigator_shadow --agreement` grows a by-lesson-inject
  table — closes the V5 "watch with no readout" gap. First live numbers:
  with_lessons 58% agreement (15/26) vs baseline 41% (49/120) — early
  directional positive for `navigator.lesson_inject`, n too small to act.
  Live findings the readout surfaced on first run: evidence-free retries
  0/1253 (Phase 62 convergence detection already escalates instead of
  blind-retrying — the guard works); playbook-curation events emit only on
  file change (quiet passes invisible — caveat now in the report); live
  second-family vocabulary carries a SECOND_FAMILY_ prefix (normalized).
  Consent message stays with the session-protocol thread; 3-arm experiment
  stays skipped (Jeremy's call).

- **2026-07-22 — Chunk-7 adversarial review: PASS with fixes (second
  PASS of the arc).** 3 Codex lenses vs b4c8c13; 5/5 findings verified
  real, 0 hallucinated (eighth consecutive clean round). Fixed: (1)
  unanimous — EFFORT headline was a silent newest-5000 tail sample
  (armed at 3700 live rows); now reads the whole step-costs file — an
  offline CLI has no reason to sample, and the module's own no-silent-
  caps rule applied to its headline; (2) --log-dir mixed archive events
  with live cost telemetry; now one base dir sources both inputs; (3)
  input-read failures shrank denominators silently; coverage counters +
  an "incomplete" warning now render; (4) operator-facing doc mentions
  gained PYTHONPATH=src. Rejected: console-script entry point
  (subtract-before-you-add), deleting --json/--log-dir (both earn keep
  post-fix). Reviewers left the metric definitions, the A/B grouping
  semantics, and the honesty block untouched — pressure landed on
  making the honesty rule apply to the module's own inputs, which is
  the review working as designed. Record:
  docs/history/2026-07-22-chunk7-adversarial-review.md.

- **2026-07-22 — Swarm-review chunk 8 SHIPPED (enforcement pin — final
  chunk of the arc).** The DEFAULTS.md census is now two-directional:
  the new reverse lane (`test_every_documented_key_has_a_reader`) fails
  the suite when a documented dotted key has no reader in src/. Read-
  detection is mechanical — AST-census hit, whole-key string literal, or
  f-string prefix + suffix literal in the same file — which resolved all
  five wrapper-read keys (budget caps via _coerce_cap, notify.viewer_url
  via notify_telegram._cfg, the two validate.hosted_free.* keys via the
  prefix-constructing hosted_free._cfg) with ZERO hand-maintained
  exemptions. Deviation from the checkpoint sketch, deliberate: no
  "DEFAULTS.md column" — a doc column is hand-maintained state (the rot
  list the checkpoint itself warned about); the AST-shape check achieves
  the same enforcement with no doc churn. Tripwire mutation-tested
  (phantom row → named failure → reverted). Checks (a) stores / (c)
  guards BACKLOG'd with prerequisites named (store-path registry; guard
  manifest + firing probes) — convention and enforcer land together.

- **2026-07-22 — Chunk-8 adversarial review: REJECT-as-reviewed →
  remediated same session; the swarm-review arc is COMPLETE.** 2 Codex
  lenses vs a6cabd7; 2/2 findings verified, 0 hallucinated (ninth
  consecutive clean round — zero hallucinated reviewer claims across
  the entire arc). The high (both lenses): multi-key DEFAULTS table
  cells escaped the reverse census — 7 sibling-documented keys (incl.
  recall.guard_window_minutes, captains_log.rotate_keep) never entered
  it; the parser took only the row-leading key. Fixed: every dotted key
  per key cell; all 96 documented keys resolve; mutation-proven on a
  second-position phantom key. Low: both census lanes now rglob
  (nested src/ packages stay censused); collateral basename-collision
  fix in the literal scan. Record:
  docs/history/2026-07-22-chunk8-adversarial-review.md.

- **2026-07-22 — Decision (Jeremy): compound-thinking work = chunk 9,
  addendum to the swarm-review arc.** His mid-arc COMPOUND_THINKING_DESIGN
  addition (which he'd named "chunk 6" — he had chunk 5 as the arc's end
  in his head; the collision with the shipped chunk 6 was unintentional)
  is renumbered to the next sequential chunk as addendum work on the
  completed arc. Opens with discussion and planning before any
  implementation, per his original intent statement.

- **2026-07-22 — Decision (Jeremy): chunk 9 continues as async discussion;
  implementation of settled points authorized at Claude's discretion.**
  Verbatim: "At your discretion, you're welcome to implement if you agree
  with opus and I towards our overall maro goals while I'm afk." Amends the
  discussion-first posture: unsettled recommendations stay discussion
  material; points with explicit three-way agreement (Jeremy + Opus +
  fable) may ship without waiting. Also asked for a Codex xhigh contrast
  take on the conversation (delivered:
  docs/history/2026-07-22-compound-thinking-codex-take.md). His estimate
  on the conversation's implementation conclusions: "80% there."

- **2026-07-22 — chunk 9 discussion OPENED (grounding + contrast).**
  Fable's grounding pass = COMPOUND_THINKING_DESIGN.md §12: conceptually
  on target; the nudges are mostly "the recommendation is a seam the arc
  already shipped" (§9.3 → Phase 62 fingerprint convergence + future
  map-edit rate; §10.3 → chunk-6 surprise/novelty capture + chunk-4
  contradiction join, missing piece is the capture⋈outcome join readout;
  §9.5 → closure's binding proxy commitment run mid-meander — cadence,
  not mechanism; §9.1 graph = tower-of-babel candidate, build last,
  schema by subtraction from §9.4+§9.2; §2b authority = the fork
  contract's escalation-trigger lane). Missing piece named: the map must
  be recursive from the start (recursion decree; a parent landmark can be
  a whole child map). Codex xhigh contrast independently converged on the
  same build order (recon flavor + stop-verdict split first, graph
  deferred) and added corrections adopted for the agenda:
  stall-as-evidence-not-verdict, typed landmarks (type on the node,
  symmetry in traversal), VOI gate on unknown-unknown recon, side-effect
  reversibility on committed edges. Settled fix shipped (Jeremy's
  explicit endorsement in the transcript, turn 4 point 3): star skill
  re-cutting guardrail — cuts-continuously, master re-cuts on the record
  naming the licensing surprise, children surface cut-evidence via the
  escalation-trigger lane, silent drift stays the violation.

- **2026-07-23 — Decision (Jeremy): partial approval — implement the
  agreed set of chunk 9.** Verbatim: "let's dig in where it appears that
  we all agree, and make the right things concrete… I guess explicitly
  that means this is a partial approval where we're certain we need to
  make changes and we have agreeance on what those are (and acknowledging
  there is still discussion to be had)." Agreed set = the three-way
  convergence (§9.4 stop-verdict split + §9.2 recon flavor, exercised in
  `star` first). The artifacts conversation (uncertainty, remaining §9/§10
  items) is planned for the evening of 2026-07-23. Standard discipline
  applies: document, act, adversarial-review after appropriate chunks.

- **2026-07-23 — Decision (Jeremy): ONE map — processes recurse, maps
  don't.** Corrects §12's map-per-subgoal sketch: recursion lives in the
  pattern (goal → [process] → result at every scale); the map is a single
  shared substrate. A child process gets a **vantage** (scoped view +
  scoped edit authority via the fork contract), not its own map. "Top of
  the tower shows what the locked front door hid" — position changes what
  is observable; reaching a landmark is progress AND a new survey station.
  "Multi-dimensional map" framing explicitly rejected. Design doc §13a.

- **2026-07-23 — Decision (Jeremy): milestones are revisitable; stop
  verdicts carry reopen conditions.** A dead end doesn't stay a dead end:
  a blocked-route label is a grey-fog cached observation that reopens when
  the picture improves, capability changes, or the world changes
  (backtracking-with-memory — the semi-left-behind tree-traversal idea).
  Consequence for §9.4: every stop verdict is recorded with evidence AND
  reopen condition. Chunk-4's contradiction pipeline is the shipped prior
  art of exactly this shape. Design doc §13b.

- **2026-07-23 — Decision (Jeremy): terminology — the river, not the
  meander.** "Meander" invited aimlessness misreadings; the motion is
  river-shaped: seemingly random but not — locally wandering, globally
  lawful, and the path carves the terrain for later flows (the
  lessons/playbook layer). Use "river" where intent matters. Design doc
  §13c. (Side-quest question also settled on record: the term is Jeremy's
  own prior coinage — INTENT_RESOLUTION_DESIGN.md's side-quest DAG — not a
  new invention; §13d.)

- **2026-07-27 — Decree (Jeremy): artifacts over streams — context is a
  view over durable artifacts; a cap must never destroy the only copy.**
  Surfaced by the tire-runs tangent (raw retailer HTML streamed into
  context blew run 3's cost ceiling; caps truncating in-flight content
  at many seams). Not a new principle — the same instinct as the
  run-visibility arc ("make all inputs/outputs/artifacts visible"),
  formalized at the data-flow layer: each step intentional and
  data-driven; step internals stay magic hand-waving to a degree, so
  the work is shown at the seams — every step's inputs, outputs, and
  artifacts durable on disk, derived views in context, re-review over
  rebuild-the-world. Dropping data on the floor as "unnecessary" for
  performance is an optimization working against us. Caps split into
  two classes: view budgets over durable sources (fine) vs lossy
  destruction of the only copy (bugs — fix by persist-then-summarize
  as seams are touched, not big-bang). API-seam max_tokens stays: call
  mechanics, not context assembly. First instances: the Opus
  token-reduction work persisting raw before transforming; a
  worker-reachable fetch verb (fetch → sources/ artifact + index →
  extracted view) is the named next lift.

- **2026-07-27 — Decree amendment (Jeremy): in-process work persists
  too.** Artifacts-over-streams covers step products; extended to the
  work-in-progress itself — intermediate results, partial transforms,
  working state — persisted so a later examination can make the
  "re-start vs start-over" call from evidence instead of re-deriving
  from scratch. Same distinction the project-continuation fix made at
  the goal seam, applied inside the run. Dropping intermediates as
  "unnecessary" is the same optimization working against us.

- **2026-07-27 — Decisions (Jeremy): chunk-9 agenda items 1, 2, 3, 5.**
  (1) Build order confirmed: #4 stop-verdict split first, then #2 recon
  flavor, both exercised in star before src/ wiring. (2) Coherence rides
  the existing closure/navigator judgment seams NOW ("let's make sure we
  don't lose the plot ourselves"); a BACKLOG item carries the later half
  — revisit with live data, confirm, upgrade to its own tracked signal.
  (3) Escalation payload: simple first — single-chasm decision + one
  family-ROI context line; complex later. Jeremy's gut, on record:
  capability-acquisition side-quests ("literally learning a language to
  cross the chasm") ARE the pattern we're building — the run-scale twin
  of the lesson loop; excuses-masquerading-as-roadblocks (boil-the-ocean
  prompts included) are the failure family to guard against. New open
  question: scientific-method thinking at the failure boundary — when a
  failed pass could succeed as a side-quest, how does the system
  determine/recommend that? (5) External interrupt is NOT a fifth stop
  verdict: the four verdicts are observations about the map; an
  interrupt is an event about the run. Record run-level
  interrupted:reason + whichever verdict the evidence supported at
  interrupt time, with reopen pointer (run 3 reads "out-of-budget, work
  product delivered, reopen: raise cap or continue" — not "failed").
  Item 4 (one-map cross-goal scope) stays deliberately open pending
  continuation-menu data.

- **2026-07-27 — Decision (Jeremy): diagnosis ownership split — the step
  diagnoses and recommends; the planner decides and routes.** A failing
  step types its cause ("what would have to be different?"), may
  substantiate within its already-granted scope/budget, and exits
  blocked_on(cause, delta, proposed-experiment) as an observation. The
  discriminating experiment as a plan move, side-quest spawn, reroute-
  away, and the revisit are map operations owned by the planner —
  because dedup across blocked steps only exists at the map (three
  steps missing one capability = ONE side-quest), and because the
  planner may route away regardless. Design doc §14.

- **2026-07-27 — Decision (Jeremy): token-brake calibration + design
  calls — "run with them" (delegated to fable's measured steers).**
  (1) Fresh-ingest ceiling stays 300K — measured, not reasoned: healthy
  fresh ingest cost-bounded at p95 ≈ 259K over 123 metered steps,
  pathology band 337-478K; raw run-log tokens_in is a trap metric
  (includes cache reads + pre-fix double-count) and must not be used to
  calibrate. (2) Ceiling #2 = weighted ingest (fresh + cache_read/10),
  default 600K ≈ 2.3× healthy p95 — closes the transcript-amplification
  hole; unweighted totals rejected (false-positive band too close).
  (3) Read bypass ACCEPTED as Bash-only enforcement with the guarantee
  restated honestly — fetch-extract reads are the sanctioned lane, the
  accumulation ceilings are the backstop. Context: 3-lens Codex review
  REJECT on 344973f (headline verified: token_runaway retried one layer
  above the adapter seam); remediation acc5eda; merge-gate round caught
  the remediation's own run-stop coercion. Record:
  docs/history/2026-07-27-token-brake-adversarial-review.md.

- **2026-07-27 — Decisions (fable, AFK session, under Jeremy's item-5
  decree + delegated design authority): chunk-9 #4 stop-verdict split
  implementation calls.** (1) Interrupt marker shares the stop_verdict
  FIELD with the four verdicts, one-field-two-channels: evidence-backed
  verdicts stamp first-write-wins at their break sites, so a supported
  verdict always owns the field before interrupt machinery runs;
  external-interrupt lands only when no map observation existed
  (structurally outside GOAL_VERDICTS — the decree's two-channel intent
  with precedence instead of a second field; mechanical rename
  available if the literal shape is preferred). (2) Cross-layer stamp
  policies: handle demotions DEFER to a loop-stamped verdict (same stop
  event, closest site wins); director escalation close OVERWRITES with
  `[refines: …]` evidence (later better-informed judgment ending the
  chain — defer would make reachable-but-not-worth-it dead code on the
  max-depth path). (3) Landing-synthesis learnability closed:
  out-of-budget with no positive goal verdict is not a learning seed
  (outcome_policy). (4) Judged demotions rebucket incomplete →
  done-not-achieved at classify_outcome; salvage block survives.
  (5) New success_class "interrupted" replaces the 4-status "unknown"
  hole; recall repeat-guard, strategy weights, and failure attribution
  stop reading interrupts as goal evidence. Record:
  docs/history/2026-07-27-stop-verdict-split.md.

- **2026-07-27 — Decision (fable, delegated by Jeremy: "no strong
  opinion here, your call; if we're better served in the longer run by
  one way or the other let's lean in, otherwise it's implementation
  detail"): verdict field shape SETTLED — one-field-with-precedence
  stands.** No long-run reason to switch to a second field surfaced in
  twelve review rounds: the item-5 decree's dual-record need is met by
  the verdict field (map observation) + the status channel (run event,
  INTERRUPT_STATUSES), consumers were hardened to honor both channels
  in the chunk-9 #4 review, and GOAL_VERDICTS structurally excludes the
  marker so no consumer can mistake an interrupt for a map observation.
  Ruled implementation detail; the mechanical rename stays available if
  a future consumer needs both simultaneously machine-readable on one
  row (today none does).
- **2026-07-27 — Decision (Jeremy: "let's build the per-step learning
  chunk"): learn at the granularity where verification actually
  happened; inject conservatively.** Approves the session's proposed
  design for his "seems silly to toss possible good learning out just
  because the high level didn't work out" question. Shipped same
  session: (a) `achieved-not-done` success class — judged
  goal_achieved=True with non-success status is achievement evidence
  (verdict-preferred classify branch, learnable; closes the
  achieved-but-stuck star find); (b) `extract_step_lessons` —
  individually-verified steps (done + strong) of non-learnable runs
  yield PROVISIONAL lessons; (c) provisional lifecycle: reduced entry
  score, excluded from every injection surface, never promotes to LONG,
  a confirmed-context re-record clears the flag (promote-on-evidence),
  decay disposes of the rest; (d) asymmetric bar — no negative/deadness
  claims extracted from failed runs; (e) side-quest and `blocked_on`
  learning deliberately deferred until their runtime seams exist.
  Independent convergence noted: Jeremy's work-box handoff doc arrived
  at the same confirmed/provisional gate the same week. Record:
  docs/history/2026-07-27-per-step-learning.md.
- **2026-07-27 (session, per-chunk review discipline): per-step-learning
  adversarial review DONE** (3 Codex lenses vs d1b442c, CONTESTED →
  remediated same session; 6/7 distinct findings verified, 0
  hallucinated — thirteenth clean round). Fixed: ALL closure-lane
  statuses now defer run-level extraction (three-lens consensus HIGH —
  stuck-later-judged-True runs were recording failure-framed confirmed
  lessons verdict-blind at finalize; deferred rail was already
  verdict-aware), graveyard provisional exclusion (leak worse than
  reported: resurrection reinforces confirming=True, so a topic match
  also CLEARED the flag), sessions_validated moves only on
  confirmed-context reinforcement (provisional sightings no longer
  accrue promotion-grade validation), promote_lesson boundary guard
  (CLI path), test no longer pins a goal-success claim as recordable,
  stamp-after-success docstring honesty. Rejected: deterministic
  semantic filter on lesson text (provisional lifecycle IS the
  structural control), atomicity/no-retry (retriable by design),
  verdict_trust bypass as chunk defect (consistent with pre-existing
  siblings; BACKLOG'd as the real architecture question). Record:
  docs/history/2026-07-27-per-step-learning-adversarial-review.md.

- **2026-07-27 (session, continuing 9/10 per Jeremy's standing
  directive): §9.6 escalation payload SHIPPED simple-first** —
  implements Jeremy's agenda item-3 decision exactly ("single-chasm
  decision + one family-ROI context line; complex later"). New
  `src/escalation_context.py`: `decision_line` (deterministic ask
  templated per emit point, honest options only, never raises) +
  `family_roi_line` (recurrence line keyed on Phase 44 diagnose_loop
  failure_class; first-occurrence is signal; silence over noise on
  empty/healthy/unreadable). All three escalation emit sites wired
  additively (blocked_step: decision + family; dispatch: decision only
  — no loop, no diagnosis, omitted-not-faked; director_escalation:
  summary_for_user framed as the ask); telegram leads with the ask,
  legacy payloads render unchanged. Pure/deterministic, no LLM, no
  config keys, no new persistence. "Complex later" (capability-
  investment scoring, side-quest recommendation at the boundary) stays
  discussion material. Record:
  docs/history/2026-07-27-escalation-payload.md.

- **2026-07-27 (session, per-chunk review discipline): §9.6 adversarial
  review DONE** (2 Codex lenses vs 839da56, CONTESTED → remediated same
  session; 3/3 findings verified, 0 hallucinated — fourteenth clean
  round). Fixed: diagnose_loop consult no longer writes a captain's-log
  DIAGNOSIS event from the enrichment path (`emit_log_event=False` —
  the chunk's own no-new-persistence constraint, Architect HIGH);
  unreadable non-empty ledger renders silence instead of a false "first
  on record" (the shipped test had proven a synthetic raise, not the
  swallowing loader); family count now covers the whole ledger via raw
  rows (the limit=200 cap bought nothing — read_jsonl_tail reads the
  whole file anyway — and lied at the live ledger's 1418 rows).
  Lesson: a helper reading through a fail-soft loader inherits its
  failure semantics — "returns []" is three different truths (empty,
  absent, unreadable). Record:
  docs/history/2026-07-27-escalation-payload-adversarial-review.md.

- **2026-07-27 (session, Jeremy's ask: "consider archiving on the
  backlog, I think it's getting pretty big"): BACKLOG archiving pass
  DONE.** BACKLOG.md 2678 → 1541 lines; all fully-closed entries moved
  intact (never deleted — data-retention rule) to BACKLOG_DONE.md
  "Archived from BACKLOG 2026-07-27": triage-pass-history preamble
  (passes 18–25), R2/R3/R4/R5 (residuals all closed), R6, C4-BOX
  burn-in, -1 Purgatorio r2 graduates, #22 shipped trail, DONE pointer
  stubs, install-trial residuals, launch-content arc, the 2026-07-04
  stale-drop record. Open remainders stayed live as compact stubs
  pointing at the archive: R6-E anchoring watch-item, C4 flip
  (Jeremy-gated), #22 errand-envelope + standing habit, $HOME haiku.txt
  watch-item, (g) filename-scrub known-gap + (h) auto-resume graduated
  shape; the orch.py-trio removal residual now rides the Phase 38
  subpackage entry. Verified no-loss by line set-difference (every
  removed line present in the archive).

- **2026-07-28 — Decisions (Jeremy): star KEEP + NOW retry rung +
  both-lane testing + house-style doc.** (1) Star dev-skill alpha
  adjudicated **KEEP** at 2 uses ("I'd like to keep it around and keep
  growing the skill as we continue to evolve our workflow") — both uses
  met the pre-registered keep signal; verdict + anti-prompt-soup
  maintenance rules recorded in `.claude/skills/star/SKILL.md`
  (additions = distilled principles only; consolidate on double-or-3-
  arcs with archive-before-rewrite; DEV_PATTERNS graduation valve).
  (2) **NOW retry rung approved as a build item** ("I like the rung"):
  failure-class-routed ladder NOW → artifact-seeded retry / runtime
  star → AGENDA; corpus-scan findings, prereqs (NOW provenance
  stamping; `_is_complex_directive` reachability check — zero live
  firings found), and the pre-registered 3-arm experiment live in the
  BACKLOG entry "NOW retry rung". (3) **Decree: experiment matrices run
  BOTH lanes** — the mature workspace (with accrued learning) and a
  freshly minted workspace (without); learning-delta is a first-class
  measurement axis, not a confound. Runtime-piped same day. (4) "We
  should write down our dev approach... house style is meaningfully
  impactful... hard to replicate in a vacuum on a new machine" →
  HOUSE_STYLE doc BACKLOG'd with proposed shape (consolidate the
  workflow loop; import the machine-local feedback-class auto-memories
  into the repo — the un-replicable half; own maintenance cadence).
  (5) Open-thread structure discussion OPENED (linear chat vs
  branching work; "not sure just adding to the backlog is quite
  enough") — census-first proposal in BACKLOG "Open-thread structure";
  no structure committed until the census shows the real shape.

- **2026-07-28 — Design inputs (Jeremy, second round): his dev
  workflow as the spec for thread structure + step-skeleton runtime
  vision.** (1) His dev mental model recorded near-verbatim as the
  requirements spec for the open-thread structure (BACKLOG entry
  updated): stubs first — the skeleton IS the plan, in the medium of
  the work; `//todo` = attachment points for thoughts with "no
  concrete attachment point in the organization itself"; "see a path
  through" → architect the shape → dig in; spiral over modules to burn
  down unknowns. Flat epic/issue trees fail him: "fighting those
  branches eventually overwhelms me and I lose the plot." Standing
  constraint restated: align Claude's persistence-shaped thinking with
  his timeline/story-shaped memory — the structure needs both views
  (thread graph + narrative spine). Proposal v2 (census →
  narrative spine → attachment-point markers + collector →
  ACTIVE/PARKED working-set cap) awaits his reaction. (2) Runtime
  vision captured verbatim in BACKLOG Vision "Step-skeleton
  parallelization": parallelized step-shaped work, smarter router,
  front-loading + "redo but with re-shaping" on discovered
  dependencies, "a different way to approach a sidequest" — read as
  contract-skeleton decompose + reopen-as-normal; feeds the
  thread-architecture arc, not a build item yet. The dev/runtime rhyme
  is deliberate ("we're organizing all of this to a degree on the way
  I think"). (3) House-style entry gains the M1-vs-maro-box workflow
  contrast pass (work/personal boundary: patterns transfer, artifacts
  don't). (4) Offer noted, not acted on: pointing Claude at his
  multi-year work projects to learn thinking patterns — parked behind
  the boundary decree; narrated-pattern capture (like this session) is
  the sanctioned form.

- **2026-07-28 — Thread census LANDED (star use #3) + house-style
  iteration protocol (Jeremy).** (1) Census:
  `docs/history/2026-07-28-thread-census.md` — 56 threads / 7 states
  across BACKLOG, GOAL_BRAIN, MILESTONES, 123 docs, auto-memories.
  Headline: **7 premise-drift finds** (heartbeat-gate design on removed
  local-model substrate; next-leap packaging with cleared blocker;
  thread-arch + Mage park reasons expired; "10 pre-existing test
  failures" claim stale; director-clarification YOLO half unanchored;
  BACKLOG's own C4 entry claiming container off — the last resolved on
  the spot: box ON since 2026-07-16, **C4 CLOSED**, entry archived to
  BACKLOG_DONE). 8 doc surfaces currency-fixed in the census commit.
  Convention rules the census validated: history docs never carry live
  markers; doc-archival explicitly sweeps forward obligations; park
  reasons stated as falsifiable claims. The 6-thread drift cleanup
  batch + ACTIVE/PARKED split (cap 7) + proposals 2–4 await Jeremy.
  (2) **Jeremy clarification (decree-class, runtime-piped):** the
  recorded workflow spec is a partial dump — ">30 years... reflexes
  and intuitions I likely can't directly name"; iteration protocol =
  after a pass or two he shares real work changes and we **quiz him on
  the thought process** — "I'm a much better question answerer than an
  essay writer." Piped to the runtime journal as: questions/escalations
  to Jeremy should be quiz-shaped, never essay prompts. (3) His
  work-side **CodeLikeJeremy skill PoC** offered and mined as
  house-style pattern input (machine-local zip; artifacts stay out per
  boundary decree; six stealable patterns in the BACKLOG house-style
  entry — headline: a style doc with a regression harness).
  (4) Method note for future recon delegation: the strict
  verbatim-quote + file:line + open-vs-closed-disposition contract
  produced the first 10/10-clean spot-verification sample in this
  repo's recorded history (prior 30–78% hallucination) — reuse it.

- **2026-07-28 — Census decisions ratified + quiz-decree amended +
  docs-surfacing detour (Jeremy).** (1) **Quiz-decree amendment
  (decree-class, runtime-piped 83f06acf superseding e2124d71):** Jeremy:
  "'never' is pretty strong... use the style that's best to get the
  answer; actionable unlocks are better questions, I like to ramble and
  answer more open-ended questions with talking about context." Style
  matches purpose — concrete options for actionable unlocks, open-ended
  welcome for context-gathering. (2) **ACTIVE set adopted as proposed**
  ("Sure, sounds good"): census §6 split — threads 1–5 (session-protocol
  arc, NOW rung, house-style, thread-structure, chunk-9 remainder) +
  drift-cleanup batch + 1 free slot, cap 7. Free-slot trio (closure-check
  unification, depth-cap unification, backend-resilience pair) all
  sanctioned to examine, no order preference. Drift-batch items 1–4 held
  for his walkthrough decisions (he asked for plain-language context —
  shorthand alone didn't land; calibration data for future decision
  asks). (3) **Docs-surfacing detour (decree-class):** reading .md over
  ssh is a bad surface for him — the viz server gains a **Reading tab**
  (`reading.html` beside the runs index) listing landed docs awaiting
  his read/decision; rows link to the **GitHub-rendered copy on main**,
  nothing self-hosted ("better just as a link to the github-pushed
  artifact so we don't reinvent the wheel"). Source of truth =
  `docs/READING_QUEUE.md` (living); rendered by loop_report at every
  index regeneration; `reading.html` allowlisted by exact name in
  viz_server. End-of-chunk discipline gains: when a landed doc awaits a
  Jeremy decision, add a Queue row in the same commit. (4) **Dev
  captain's log approved** ("thanks for not letting this slip") —
  `docs/DEV_LOG.md` started this session: append-only, newest-first, one
  paragraph + a Surprised-by line per session close (he loves the
  surprise angle: "there are always angles that surprise").
  (5) **Free-slot trio examined same session (AFK loose-ends
  authorization); outcome: the slot goes to closure-check unification
  (census #30) by elimination.** #31 depth-cap unification was already
  shipped 2026-07-12 (3abc4f0, `loop_types.MAX_RESTART_DEPTH` +
  tripwire test) and #32's both halves already shipped 2026-07-09
  (951d49e in-flight checkpoint stamping; 37ba4ba heartbeat
  stranded-state sweep calling `recover_stale_claims`) — both census
  threads faithfully inherited stale "still open" annotations from
  BACKEND_RESILIENCE_DESIGN.md, now currency-fixed there. Method
  lesson: the 10/10-clean recon contract verified quote-*accuracy*, not
  claim-*currency* — a doc's own status line is just another falsifiable
  claim. #30 verified genuinely open (`ClosureVerdict` +
  `verify_goal_completion` still live beside `director_evaluate`;
  3 call sites). Drift find #5 also closed both halves (suite exit 0;
  fragile seam deleted in swarm chunk 1 — see Dormant threads).

- **2026-07-28 — Drift batch ADJUDICATED (Jeremy, all four; census
  drift finds #1–#4 closed).** (1) Heartbeat gate: re-park on corrected
  premise — design targets hosted-free; build when heartbeat spend
  actually hurts ("1a"). (2) Next-leap packaging: capacity-park with
  first claim on the next free ACTIVE slot; blocker excuse dead, the
  priority call is the real reason ("2a agree"). (3) Thread-arch:
  **flow-as-we-go ratified** — "assuming we can actually re-flow the
  work, let's do this as we go; I'm a little concerned it will get
  lost. your call on doc + over time vs park for later." Claude's call
  = doc + over time: the Remaining pieces ledger (L1–L5) added to
  THREAD_ARCHITECTURE.md, census-tested, pieces leave only by ship or
  explicit retirement; promote to a dedicated arc only when what's left
  is implementation detail, not moving spec. (4) Mage: re-park under
  the graph-memory direction — "in the direction of guidance as much as
  implementation; I'm ok if we want to jump into implementation, but
  the overall ask isn't necessarily something that gets 'done', just
  worked toward I suspect." His summary of the batch: "all claims and
  doc-guidance as much as anything." All four park reasons now stated
  falsifiably in Dormant threads (this doc) for the next census to
  test. *(Same-session addendum: drift find #6 — director-clarification
  YOLO half "anchored nowhere current" — resolved as a census
  overcount: `SESSION_PROTOCOL_DESIGN.md` carries ask-on-ambiguity +
  YOLO opt-out + user-level defaults in both its msg-4 table row (:130)
  and its inputs section (:477); the ACTIVE SP arc owns it. With #5 and
  #7 closed earlier, **all seven census drift finds are now
  dispositioned**.)*

- **2026-07-28 — Telegram-runs review + proposals 2–4 + compound-
  thinking spitfire (Jeremy, evening round).** (1) **Telegram runs
  reviewed** (tawny-ferret/Karpathy-wiki, dapper-oak/Erdős-workflow) —
  evidence record: `docs/history/2026-07-28-telegram-runs-review.md`.
  Headline finds, lead-verified: Poe's "already recovered" scaffolding
  claim was false (no gist text anywhere in the run); Poe's
  anti-escalation boilerplate has compiled into the lesson store
  (lesson `db37d525`) and recall now injects it — the dispatch-escalate
  pathway the runs were meant to diagnose is no longer exercised;
  quality_gate is scope-blind (zero `scope` refs) while closure_verify
  consumes pre-registered scope; both run_cards under-report cost ~2×
  vs their loop logs. **Dispositions deferred to the forked review
  session** (Jeremy has his own view of the Poe conversation to bring).
  (2) **Narrative spine RATIFIED** ("that's pretty good... terse but
  cites references") with amendments shipped same session: date
  headers (his sessions span days), plain-language rule (gloss
  codenames on first use — "sometimes I'm scratching my head a little
  at obvious-to-you terminology"), and a viz Dev log tab
  (docs/DEV_LOG.md → dev-log.html, "1-stop shop to go find
  references"). (3) **Markers/collector DECLINED for now** — "let's
  not overengineer our dev workflow that's already becoming a bit
  intense"; revisit rider = next census re-asks with fresh drift data,
  any lost-thread incident reopens immediately. (4) **Cap mechanics:
  self-serve promotion with notification blessed**; his agenda-jumps
  are legitimate by declaration ("I'll sometimes toss the general
  prioritization to the wind... I'm good with that"); Claude surfaces
  genuine priority questions; his standing design concern = "being
  able to keep tabs on everything." (5) **Compound-thinking soft set
  adjudicated** (full record in COMPOUND_THINKING_DESIGN.md Status
  2026-07-28): §9.1 lens-not-schema with runs-visualizable-on-demand
  caveat; §9.3 declare-blocked GREENLIT ("let's try it and we can
  pivot"); §9.7 all-global to start, scoping named the hard part
  (klingon line) and joined to the recursive-orchestration-memory
  constraint; §9.8 capability-investment DEFAULT-YES (one prompt
  forward + possible user escalation); §9.9 later; **§10 calibration
  flagged load-bearing — needs its own discussion** ("hard to get
  right (and keep simple)"); cross-goal scope = empirical, pairs with
  §9.7. Lead's judgment (per his explicit delegation): only §10 +
  §9.7/cross-goal scoping need a real conversation — same
  conversation, before any §9.7 build. (6) Verbal-UX tangent captured
  as a BACKLOG Vision entry with his revisit trigger (larger successes
  routine / real-time optimization). (7) Session mechanics: review
  discussion forks via `claude --resume <session> --fork-session`.
- **2026-07-28 — M1-contrast pass ANSWERED (Jeremy, all four questions
  + a follow-up round; `docs/history/2026-07-28-m1-vs-box-workflow-contrast.md`
  goes from half a document to two-sided).** Headline reframe in his own
  words: **"just because I'm using 2 boxes with different contexts, we
  shouldn't feel tied to one or the other — they're different contexts
  that happen to look the same."** The exercise's purpose was "to help us
  firm up what we might be missing, not push each box to be clones of each
  other, but an interleaved workflow taking the benefits of both (assuming
  there are some — I suppose it's possible one is just better than the
  other categorically, I'm not sure, thus the check)."
  1. **DECREE — out-of-the-box invariant, newly declared:** *"Functionality
     that we add should presume that it's 'out of the box' functionality
     for the project day 1 — unless it's specifically functionality gated
     on prior learning data for whatever reason."* Corollary he stated
     first: *"in general the work we're doing should be able to be verified
     on a clean clone of the maro repository"*, and *"some learning-based
     functionality will need data added to properly test it."* He flagged
     it as an assumption he'd never bothered to state — *"I don't think
     that was a bad assumption, but maybe it needs to be declared?"* It
     needs declaring **with a tripwire**, not just a sentence: this exact
     invariant was silently violated for months and caught only by the
     2026-07-09 docker clean-machine trial (flat `src/` layout → pip
     installed zero modules, every entry point ModuleNotFoundError, masked
     locally by `PYTHONPATH=src`). Precedent for the shape: DEFAULTS.md +
     its census tripwire, but for *runtime data* rather than config.
  2. **DECREE — no merge gate, no master/slave between boxes:** *"I don't
     really want a merge gate or a master/slave style work relationship
     between boxes, outside of maybe a multi-box delegation of sorts which
     hasn't even been discussed."* Claude's §3.3 proposal (M1 stops
     certifying cross-layer work, hands it to a box-side gate) is
     **WITHDRAWN** — and was a category error: the one merge gate that
     exists (Hermes/mini2 propose-only, decreed 2026-07-20) exists because
     an agent that can rewrite its own orchestration needs a human at
     merge-to-main, which has nothing to do with one dev box certifying
     another's work. What survives is non-institutional and stands:
     **diff review cannot see composition defects; only executing the
     thing can** (this arc's proof: `loop_status=""` + a terminal handler
     coercing falsy → `"stuck"`; three M1-side adversarial reviewers
     missed it). The remedy is therefore *give the M1 the ability to run a
     run* — which is item 1's clean-clone + seeded-data problem, not a
     hierarchy.
  3. **DECREE — threshold rule rejected as too heavy:** *"feels heavy; we
     can hypothesize and make a best guess, then create follow-up work to
     confirm or pivot."* The observe-only-until-box-calibrated proposal is
     dead. Replacement (Claude's, accepted-shape pending his read): a
     magic number lands with its **provenance marked** (`reasoned` vs
     `measured`) plus a paired backlog row naming the measurement that
     would confirm or pivot it — the drift-batch falsifiable-reparking
     discipline applied to numbers. No gate, one line per threshold.
  4. **Portable learning — NO scope amendment; the shipped design already
     covers his ask.** He had forgotten it was built: *"thanks, I had
     forgotten we had actually built that out, nice to have a PoC here
     rather than a backlog item to point to."* His "friends (or internet
     friends) could share data and level each other up" resolves to
     one-way publish — *"someone posting to reddit or X, sharing a learned
     set of data that is genuinely useful and helpful to a subset of
     interested people"* — which `maro-pack export`/`seal` + hand-import
     already supports, and which stays safe precisely because ratified
     decision #3 refuses cross-user confirmation inflation. Genuinely
     deferred, unchanged: *"publishing capabilities and browsing or
     auto-seeking other maro runs instead of full self-discovery. Lots of
     pitfalls there, but sure sounds nice"* — discovery/auto-seek remains
     PORTABLE_LEARNING_DESIGN §7.5 post-1.0, hive-mind still out.
  5. **Code-as-data ("modpack") — conservative approach AFFIRMED, parked
     with a handle.** His framing: *"sort of a modpack on a game; possible
     orchestration levelling up over time."* Verdict: *"the conservative
     approach is the safe one we've implemented and I'm ok sticking with
     that for the moment... no need to add an additional layer of
     complexity to our semi-unproven learning capabilities already"* — it
     *"definitely needs the proper guardrails... and may or may not even
     be a good idea."* Ratified decision "Stage-5 travels as language,
     never as .py" therefore STANDS. His recollection that a partial
     pathway already exists is correct and is ratified decision #4:
     skills/personas never auto-adopt, quarantine + `maro-pack adopt`, one
     `--all` command of friction — his *"official pending some kind of
     user review that's appropriately fuzzy, with the practicality of
     useful things being auto-defaulted in."*
  6. **DECREE — workspace hygiene:** *"we should generally have a
     workspace on any given box rather than litter copies of the repo all
     over without purpose... I think we'd do well to re-set up our
     workspace for a reusable location on the M1, we have a sort of
     randomly set up repo right now."* Confirmed concretely on the M1:
     **two live workspaces** — `~/.maro/workspace/` (real; `runs/`,
     `memory/`, `correspondence.db`, touched 2026-07-27) and
     `.run-workspace/` inside the checkout (gitignored, mostly frozen
     2026-06-21 plus two 2026-07-14 session dirs) — and the checkout is
     still named `openclaw-orchestration` long after the rename.
  7. **The contrast doc's spine is WRONG and gets rewritten: two lanes →
     three contexts.** It compared M1 vs maro box and omitted the
     Poe/Telegram live-prototype context entirely — the only one with a
     real user in it, and so the only source of live-usage evidence rather
     than test evidence. Jeremy: *"The 2014 iMac is our 'live' test
     prototype with a single ongoing repo that allows our poe-as-telegram
     bot to use the orchestration and we can examine poe's runs as well as
     develop against that same copy."* **OPEN — the one question not yet
     answered:** which 2014 Mac Mini this names (box A, Linux Mint,
     orchestrator; or box B, Monterey, Hermes/Telegram, `~/.hermes/repos/
     maro-orchestration` https fetch-only), and whether "develop against
     that same copy" relaxes the 2026-07-20 zero-creds/propose-only decree
     for mini2. The doc rewrite is blocked on this answer alone.

- **2026-07-28 — Closure-check unification SHIPPED (census #30, the
  ACTIVE free slot; build lane while Jeremy holds the forked review +
  M1 discussions).** Unified at the DECISION layer, not the census's
  literal fold: `director.evaluate_closure()` runs the untouched
  `verify_goal_completion` evidence pipeline and deterministically
  (zero new LLM calls) maps the verdict into the shared
  `DirectorDecision` vocabulary — `restart` on ground-truth-supported
  gaps (the shipped positive-evidence gate verbatim: ≥1 hard-failed
  check, conf ≥ 0.6, no inconclusives), `continue` otherwise with
  honest reasoning. The April spec's "retire `ClosureVerdict`"
  predates the verdict-integrity machinery burn-in accreted on it
  (judged tri-state, deterministic downgrades, env caps, verdict-first
  summaries) — every consumer stamps from that evidence, so the
  verdict survives as `DirectorDecision.closure_verdict` (evidence
  record) while retiring as the decision interface. Four call sites
  (census counted 3 — post-escalate re-verify was uncounted) all route
  through the choke point; policy gates (closure_restart flag,
  MAX_RESTART_DEPTH, only-from-done) stay caller-side — director
  recommends, caller disposes. Seams designed-not-built at
  `evaluate_closure`: §9.3 declare-blocked verdicts get judged here
  when they ship; gate-reads-scope joins here if the forked review
  routes it. Existing closure tests flowed through unchanged
  (monkeypatch pass-through was a design constraint); new: mapping
  suite + a liveness pin that handle consumes decision.action, not
  re-derived verdict fields. Spec correction recorded in
  ADAPTIVE_EXECUTION_DESIGN.md Trigger Point 3; CLAUDE.md row updated.
- **2026-07-29 — M1-contrast open question RESOLVED; the contrast's spine
  is "two machines, three kinds of evidence" (Jeremy).** No decree moves.
  *"The poe-mini (mini2) doesn't have direct maro access outside of calling
  the other machine to get things done... The orchestrator 2014 mini is
  where poe calls into, and is the box that's doing the dev work we're
  discussing between this machine (the M1) and the general headless
  orchestration dev work. The monterey-poe box might have a copy of the
  code for reference, but it shouldn't be using that to run anything, it
  should be asking the orchestration box to do things."* So both prior
  framings were wrong: the first pass's "two lanes" and the follow-up
  round's "three contexts". **The orchestrator mini wears two hats** — it
  is simultaneously the headless dev/run-review box and the live endpoint
  Poe calls into; mini2 is not a dev context at all, it is the *user*.
  Three consequences, written up in the doc's new §8:
  (a) **The dual role is a feature and a hazard.** Real usage arrives where
      the work happens (no staging gap at this project's size), but test
      runs and real-user runs share one workspace, so run data cannot be
      reasoned from unless something distinguishes them. **Independently
      confirmed the same evening by a concurrent session** —
      `docs/history/2026-07-28-telegram-runs-review.md` names "Poe
      scaffolding contamination" without knowledge of this pass. Two
      sessions, same seam, hours apart.
  (b) **mini2's posture is policy without detection** — Jeremy's own
      unprompted flag: *"I suppose it could choose to do that and I'd
      likely find out later about that."* Zero-creds is enforced by
      construction for *push* only; nothing surfaces mini2 *running* maro
      off its reference copy. Third instance of the declared-but-untested
      failure shape this week (out-of-the-box invariant; post-rename dead
      git hooks). Surfacing is one line — a box that runs maro grows a
      `~/.maro/workspace` — and per the retention decree it surfaces
      rather than gates. New BACKLOG item.
  (c) **The "interleave" is evidence flow, not hierarchy.** M1 contributes
      diffs + interactive toolchain probes; the box contributes executed
      runs, measured thresholds, and real-user traffic; the connective
      tissue is seeded run data the M1 can execute against — **the same
      mechanism the out-of-the-box invariant needs, so it is one item, not
      two.** Answer to his categorical check ("possible one is just better
      than the other, thus the check"): no, but not because they are
      balanced — the M1 is strictly weaker on evidence and strictly
      stronger on iteration speed and interactive probing; keep the box for
      correctness, the M1 for exploration. **M1-contrast pass CLOSED**
      (archived to BACKLOG_DONE); its four residual items live under their
      own names.
- **2026-07-29 — mini2 executes-locally: ACCEPTED RISK, parked falsifiably
  (Jeremy, same day raised).** Claude surfaced his own aside as a gap; he
  clarified it was a disposition, not a shrug: *"that was me essentially
  saying 'yep, it's a gap, that's a problem for future me'; writing that off
  as something I'll deal with if it happens and I don't think it's likely to
  happen... and difficult to guard against. I'm aware I'm playing with fire,
  and you couldn't know, but I've been running
  `--dangerously-skip-permissions` on the orchestrator box for about 5
  months now — I'm living on a few edges, this is one I'm not worried
  about."* Consistent with the documented trusted-operator model
  (`docs/SECURITY_MODEL.md`), which already records the executor's
  skip-permissions posture and the blast-radius argument the container
  executor arc was built to answer — so this is a known edge being held
  deliberately, not an undocumented one. Park reason stated falsifiably in
  BACKLOG per the drift-batch discipline (reopen if mini2 grows a
  `~/.maro/workspace`, gains landing credentials, gains a second user, or
  starts carrying work whose failure isn't cheaply reversible). **General
  lesson for future passes:** a gap Jeremy names in passing is not
  automatically an item — ask whether it is an unnoticed hazard or an
  accepted one before writing it up as work. The write-up was still worth
  it: it converted an implicit risk acceptance into a recorded one with
  reopen conditions.
- **2026-07-29 — Contamination repair adjudicated + SHIPPED (Jeremy, the
  forked review session; all three asks granted, then built same
  session).** The db37d525 incident (a Poe dispatch prompt's
  anti-escalation scaffolding minted into a stored lesson, injected
  verbatim into unrelated run dapper-oak) is the first observed
  instruction-text-rewrites-persistent-state failure. Correction to the
  2026-07-28 entry, from Jeremy's Telegram logs: Poe's "already
  recovered" claim was TRUE on mini2 (xurl/curl fetches visible) —
  false-in-effect on this box because the dispatch boundary has no
  artifact channel. Diagnosis shifted accordingly: not "Poe lied,"
  the protocol lost the artifact. (1) **DECREE — three-piece direction
  agreed**: lesson-mint provenance gate (maro-side, load-bearing) +
  typed dispatch envelope + artifacts-travel-with-dispatch. His
  channel-separation framing: "we can't just prompt that or we're
  adding the same potential problems in a different flavor." UX
  decree within it: the envelope is machine-to-machine ONLY — "I kinda
  don't love the UX forcing the envelope and this seems to prove we're
  going to want the direct ask and the prompt separately." (2)
  **DECREE — quarantine, not delete**: "I fully agree this needs to be
  extracted. Kinda reminds me of mcaffee quarantining files back in
  the day." (3) **DECREE — priority NOW**: "we need to prioritize this
  now, before we do further damage... I'm already concerned about the
  unknown unknowns in the learning system, let's not ignore the known
  holes that surface." Maro-side guards preferred "if we can keep them
  sane... that's the priority." SHIPPED same session: provenance gate
  (src/lesson_provenance.py; minted_from stamp at both storage choke
  points; quarantine mirrors the provisional-lesson exclusions at
  every injection surface + promotion; killswitch
  knowledge.provenance_gate_enabled gates classification only, never
  re-arms stamped rows; 24 pin tests incl. the incident-replay
  tripwire, classifier grounded on the four real minted lessons —
  db37d525 quarantines, bae0851f/00d4fc7e/08fed9e8 stay clean, so the
  gate discriminates rather than blanket-blocks); live rows db37d525
  (flat) + 9d6b63fe (tiered twin) quarantined via
  scripts/quarantine_lesson.py and verified unservable; envelope spec
  docs/DISPATCH_ENVELOPE.md + BACKLOG entry (build later, gate covers
  the contamination class meanwhile); maro-dispatch skill 0.2.0
  (the orchestrator-onboarding skill he remembered =
  deploy/hermes/mini2-maro-dispatch-SKILL.md) — new goal-authoring
  rules (user ask verbatim, no behavior-steering scaffolding,
  recovered content labeled as reference, follow-ups are proposals
  not dispatches), Poe's live self-patches folded back into the repo
  copy with its scaffolding-authoring advice rewritten, installed to
  mini2 with the pre-merge copy backed up.
- **2026-07-29 — M1 consolidated to one workspace (Jeremy: "let's clean up
  the workspace and run with the best practices as far as the maro
  conventions on this box").** `~/.maro/workspace/` is now the M1's only
  workspace. The second one (`.run-workspace/` inside the checkout) moved
  **intact** to a new `~/.maro/evidence/` location — deliberately NOT merged
  into the live workspace, because it carries its own `captains_log.jsonl`
  and merging foreign learning data during the Poe-contamination arc would
  be the exact mistake that arc exists to fix. Frozen repo-local `memory/`
  archived the same way; only provably-regenerable things were deleted
  (caches, a stale lock whose PID was dead and whose run had completed).
  466M → 434M; suite green after the move (6805 passed / 0 failed / 22
  skipped) — which also confirms CLAUDE.md's test-isolation claim, since
  removing repo-local `memory/` broke nothing.
  **New convention:** `~/.maro/evidence/` sits *beside* the workspace rather
  than inside it, so `~/.maro/workspace/` keeps matching the documented
  layout exactly.
  **The finding that outranks the cleanup — the out-of-the-box decree
  failing in the wild, three days after being decreed and found by doing
  unrelated janitorial work: three landed docs cite evidence a clean clone
  cannot see.** `BACKLOG_DONE.md` ×3 and `MILESTONES.md` ×1 cite
  `docs/history/2026-07-13-adversarial-review-*.md`; two 2026-07-14 history docs
  cited `.run-workspace/`; `docs/LOCAL_VALIDATOR.md:138` names repo
  `.venv-mlx` as a default. The citations are not wrong, they are
  *unreachable* — which is precisely what "verifiable on a clean clone"
  forbids. The two history docs are repointed and now say so explicitly;
  the rest is Jeremy's call because it involves publishing to a public repo.
  **Four judgment calls deliberately left to him** (BACKLOG item): commit
  cited review reports vs mark citations machine-local; `.venv-mlx` (307M,
  70% of the checkout, referenced as a documented default) keep or drop;
  the checkout rename, which **costs Claude Code session history** (sessions
  are keyed by path — renaming orphans
  `~/.claude/projects/-Users-jeremy-claude-openclaw-orchestration/`); and
  whether to prune `output/runs/`' 208 repo-local dev runs, noting that
  corpus is the M1's *only* run-shaped evidence per the contrast doc §8.
- **2026-07-29 — §9.3 structural declare-blocked v1 SHIPPED (star +
  runtime, the chunk-9 remainder build; greenlight "let's try it and we
  can pivot if needed" recorded above).** Both halves extend the Phase
  62 convergence seam per §12 nudge 1 — no invented signal. Star: Map Δ
  ledger column + two-consecutive-Δ0 structural stall trigger (typed
  stop with the streak as evidence, or an on-the-record routing row
  naming the new avenue) — the one declare-blocked decision star left
  to vibes now has a fired trigger. Runtime: ClosureVerdict.failed_checks
  + closure_fingerprint() (plan-level twin of _error_fingerprint);
  evaluate_closure(prior_verdict=...) maps an identical-fingerprint
  restart-worthy verdict to action="declare-blocked" carrying a
  thesis-refuted stop recommendation; handle's post-restart re-verify
  consumes it and stamps first-write-wins — the stop is driven by stall
  evidence, decoupled from MAX_RESTART_DEPTH. Fails open (no baseline /
  empty fingerprints → normal restart mapping). Cuts BACKLOG'd by name:
  main-gate prior-verdict join, redecompose plan-fingerprint
  convergence.
- **2026-07-29 — M1 cleanup: all four judgment calls adjudicated by Jeremy,
  and the audits changed three of the four answers (466M → 131M).**
  (1) **Adversarial reviews — keep, archive, don't publish.** His rule:
  *"if that's review that's for completed code I'm not sure we need it; if
  we may reference it later for meaningful data, let's keep it."* Audit
  finding that reframed it: 29 report files existed and **none were cited**,
  while the four citations in BACKLOG_DONE/MILESTONES name *different* files
  that are not on the M1 and were never in git (two self-describe as
  "box-local" — check the orchestrator box before calling them lost; the
  other two now carry the same marker). Archived to
  `~/.maro/evidence/adversarial-reviews-2026-07/` as future HOUSE_STYLE
  fixture data.
  (2) **`.venv-mlx` — was the hole Jeremy suspected in the "remove local
  LLMs for now" arc; removed.** Verified orphaned: rung REMOVED 2026-07-21
  by decree, `LOCAL_VALIDATOR.md` is `status: record`, zero live references
  under `src/`/`scripts/`. He asked to verify before tossing — that check is
  what turned a guess into a fact. Revival pinned cheap first
  (`docs/data/venv-mlx-requirements-2026-07-29.txt`). **Rider worth more
  than the deletion:** the bake-off *results* (4 measured JSON files:
  accuracy/latency/per-case rows, qwen2.5-coder-3b + 3 vibethinker variants)
  were also sitting in gitignored `output/`. Those are the evidence the
  doc's own revival trigger would be judged against, so they were
  **committed** to `docs/data/` — the one case where publishing was clearly
  right, and a direct repair of the out-of-the-box invariant.
  (3) **Rename — a symlink does not work; verified rather than assumed.**
  `os.getcwd()` resolves symlinks, so the old path would still key to the
  new one. Real fix is renaming the `~/.claude/projects/` dir (93 sessions,
  36M); caveat, each `.jsonl` embeds the old absolute `cwd`, so it is
  best-effort — Jeremy's own fallback ("worst case we go spelunking in jsonl
  files") is correct. Rename still his call.
  (4) **Run corpus — the premise was wrong and the real find was a leak.**
  He asked for an audit distinguishing "early runs with bad data" from
  "legit failed runs"; it was neither. All 107 runs were byte-identically
  trivial (`item.txt == "repo local"`, status `done`, the synthetic
  `repo-local-check` smoke item). **Cause: `tests/test_build_loop_script.py`
  cleaned up its inputs but not its outputs**, leaking one run dir per suite
  execution since 2026-06-21, plus 110 records on a second path
  (`output/heartbeat/runs/`). Purging alone would have regrown it. Fixed at
  the source (snapshot both dirs, remove only what the test created);
  verified delta=0 on both paths after a full suite. Suite green: 6829
  passed / 0 failed / 22 skipped.
  **Lesson, and it is the same one twice this session:** the audit is what
  produced the value, not the cleanup. Asking "is this evidence?" of 107 run
  dirs turned a retention question into a test-hygiene bug; asking "is this
  actually orphaned?" of a 307MB venv confirmed a suspected arc residue
  instead of guessing. **Correction recorded:** this session's own earlier
  note called the run corpus "the M1's sole run-shaped evidence... deleting
  it is not obviously free." That was wrong — it was litter. The M1 had 107
  run dirs and zero runs, which sharpens rather than softens contrast-doc
  §8's point about which context can produce run-shaped evidence.
- **2026-07-29 — Persist-the-artifacts decree (Jeremy): "I want all of
  the artifacts we can persisted... I continue to think we pay too much
  lip service here and not enough persisting along the way."** Two named
  reasons: testing/debugging/dev work, and showing the work to a user
  who doesn't believe or wants to go deep on how an answer was reached.
  Stated while adjudicating the §9.3 fingerprint fragility finding — his
  call was "fix the lineage, not the wording": build durable lineage +
  evidence persistence instead of fuzzy wording-coarsening (the BACKLOG
  coarsening item is declined in favor of this). First application
  shipped same day: metadata.json `loops[]` lineage (loop_id /
  loop_reason / parent_loop_id / continuation_depth / created_at, every
  loop a run dir hosts) + build/closure_verdicts.jsonl (full closure
  verdict + per-check evidence + fingerprint, append-only, both restart
  attempts in order). Extends the 2026-07-27 artifacts-over-streams
  decree from "caps never destroy the only copy" to "persist along the
  way, affirmatively."
- **2026-07-29 — Clean-checkout tripwire SHIPPED; out-of-the-box invariant
  now has teeth (Jeremy: "agree, sure, let's do that").** First enforcement
  of the 2026-07-28 decree. `tests/_checkout_tripwire.py` +
  `pytest_sessionstart/finish` in `tests/conftest.py` +
  `tests/test_clean_checkout_tripwire.py` (7 tests): snapshot the tree at
  session start, compare at finish, **fail the run** if the suite added
  files. Additions only — modifications to tracked files are already
  `git status`-visible, while an untracked drop into a gitignored dir is
  not, and that is where every known violation landed. `output/` and
  `memory/` are deliberately never pruned. Escape hatch
  `MARO_ALLOW_CHECKOUT_WRITES=1`. Mechanism lives in its own module
  specifically so it could be tested — the 2026-06-25 lesson that a guard
  nobody exercises is indistinguishable from no guard.
  **It caught a real leak within the hour, and the leak proves the decree's
  point better than the tripwire does:** on its first run in a fresh
  worktree it flagged `output/operator-status.json`, left behind by
  `tests/test_build_loop_script.py`. That file **already existed in the dev
  checkout**, so it never registered as an addition there — only a tree
  without it could reveal it. The earlier same-day fix had cleaned the run
  dirs, heartbeat records and build-loop status/lock, i.e. exactly the
  artifacts visible locally; the one artifact invisible locally is the one
  that survived. Fixed; full suite passes from a genuinely clean worktree.
  **Two decisions closed the same session:** (a) **checkout rename
  SKIPPED** — *"let's skip the rename, agree it's not worth the effort"*;
  the cost is 93 sessions of `--resume` history (Claude Code keys them by
  path, and a symlink does not help because `os.getcwd()` resolves it) for
  a cosmetic directory name. Weighed and rejected, not deferred — do not
  re-raise. (b) **Session wrapped deliberately with three items open** on
  the parent invariant: the fresh-workspace half (entry points against an
  empty `~/.maro/workspace` — a design call, since it means real spend or
  careful mocking), the learning-gated exemption marker (**no consumer yet
  — nothing currently declares itself learning-gated, so building it now
  would be speculative**), and writing the invariant into HOUSE_STYLE.md
  (its own item, its own pass). None are loose ends; all are new builds.
  **Process note worth keeping:** this chunk was landed under the
  same-day concurrent-session norm (CLAUDE.md "shared tree rules", written
  partly in response to this session's own earlier rebase wedge) — worktree
  up front, rebase there, `scripts/land.sh`, then converge. Before running
  `git checkout -- .` on the shared tree, every modified file was verified
  byte-identical to the pre-landing commit, proving the tree was stale
  rather than holding another session's uncommitted work. That check is the
  difference between converging and destroying.

- **2026-07-29 — Token-cap decree (Jeremy): "I don't love the token
  limits per step; too much like magic numbers and not enough like
  'data driven' behavior. I do think we need to have some caps due to
  context management, but maybe we need to stop trying to manage those
  up front and start trying to be more clever about what we pull in
  from a large doc."** Context: the third self-diagnosis dispatch
  (task-...bdff7e56, run ba58f96c) post-mortem — closure verification
  and lesson extraction had been silently dead since 07-27 on the
  no_tools CLI cap's 1500 floor (thinking tokens count against the
  cap; every call site that didn't pass the output_cap_tokens opt-in
  got decapitated), and a decompose call died at the 4000 opt-in the
  same run. Applied same day: per-call cap scheme replaced by ONE
  uniform runaway ceiling (`llm.py _NO_TOOLS_OUTPUT_CEILING = 16000`)
  — caps are containment circuit-breakers, never contract enforcement;
  contract quality lives in parse-fallbacks. Corollaries shipped in the
  same chunk: subprocess kill reasons preserved verbatim (liveness
  stall vs wall-clock were conflated, so recovery retried the wrong
  diagnosis), killed-call partial output surfaced into the blocked
  result (DEAD_ENDS "Attempted:" was empty), closure skip/exception
  verdicts are now unjudged instead of "treating as complete". The
  "be more clever about what we pull in from a large doc" half is NOT
  built here — it lands on the existing retrieval-handle direction
  (token-explosion arc); step 3 of ba58f96c died reading a 292KB
  session transcript in one agentic call, which is that problem, not a
  cap problem.

- **2026-07-29 — Autonomy-calibration correction (Jeremy), on the G
  deferral: "typical LLM behavior, I'm not mad, and would have been
  happy to allow more ACTIVE work. (Sometimes it's blurry I know, from
  here that's your coined terminology, not mine.)"** Context: the
  adversary-trio triage of the autonomous batch deferred next-leap
  packaging (G) on the criterion that a generic "implement the work
  items available" directive wasn't Jeremy's explicit word for the
  ACTIVE slot — a criterion the system coined, not one Jeremy set.
  Standing correction: a broad work directive from Jeremy DOES extend
  to ACTIVE-slot work; self-coined permission vocabulary ("his word",
  slot-claim ceremony) must not be treated as *his* gating rules when
  judging what he authorized. Deference laundered through a mechanism
  (trio ratifying the session's own conservative prior) is the failure
  mode to watch — adversary panels decorrelate arguments, not
  dispositions.

- **2026-07-29 — B design frame (Jeremy), knowledge injection is
  certainty-not-authority: "a learned-with-evidence recommendation
  should essentially have the artifacts to back it up and mention
  those, rather than just offer an opinion... it's more of a measure
  of certainty on the position than it is on authority... authoritative
  statements are in my mind the less useful framing; thinking of our
  scientific method a little, it might be more than an abstract theory
  at that point, but less than a fact."** Context: opening the deferred
  trio item B (playbook→director substrate mix). Design consequence:
  injected knowledge (playbook entries, lessons, rules) rides as
  advisory positions that CITE their artifacts (run IDs, outcomes,
  times-applied) — an entry that can't cite receipts reads as opinion;
  the orchestrator keeps full judgment either way ("appropriate for
  the orchestrator to make the right/wrong call regardless of the info
  given"). No authority ladder gets built; the existing
  candidate→contested→graduated tiers are the theory→fact gradient
  already. Not yet implemented — this is the frame for the B
  conversation/build.

- **2026-07-29 — Cost-threshold decree (Jeremy): kill ~$10, surfaced
  warning ~$2.50, "much more interested in the data driven values...
  let's do both per your recommendation."** He weighed removing the
  per-run cap entirely on the Max plan ("we are losing as much
  opportunity time as we are token cost... this should be fast and
  cheap") and agreed caps stay as circuit-breakers, not cost
  optimizers — API-failover backend is real dollars, and pathological
  loops are what the breaker exists for (extends the 2026-07-29
  token-cap "caps = circuit-breakers" decree). Shipped same day:
  `budget.per_run_usd` absent = auto `max($10, 4 × p90 of
  successful-run costs)` (`metrics.successful_run_cost_p90`, run-card
  scan), new `budget.warn_usd` absent = auto `max($2.50, p90)`;
  crossing the warn line emits one `effort_note` on the conversation
  channel in effort language with no dollars (spend-UX decree
  2026-07-17 applied). Box overrides ($2/run, $10/day) removed so auto
  governs; daily cap stays $25 repo default. Diagnosis correction on
  the record: budgets were already per-loop — the escalated re-run
  3a4e2692 wasn't defunded by cumulative spend, it was a $3.00
  power-tier step against a $2.40 cap; the raised/auto breaker is the
  whole fix.
- **2026-07-29** — Telegram brief-updates permission (Jeremy, decree-class):
  Claude MAY send brief updates to the maro Telegram alerting channel
  (`notify_telegram.send`, same ops lane as run events) for interesting
  angles found while pulling threads autonomously — "not required unless
  it's an interesting angle, I'll catch up either way". Sparing use:
  substantive finds, not status noise (noise-masquerading-as-communication
  is a named frustration). First use: the degenerate-router root cause.
- **2026-07-29** — Subsystem self-health monitoring is a real future
  lane (Jeremy, decree-class, on the arena-sweep finds): "we need a way
  to ensure the system itself is active and working, especially if
  we're going to allow it to modify itself (and eventual code/mod/
  organic support). Probably load bearing in the future, nice to have
  for now." Explicitly NOT ops-dashboard monitoring for support staff —
  the system verifying its own dynamic processes are alive. He'd wanted
  this at a basic IT level early on; it didn't land because "it was an
  answer without any questions" — the sweep's dead-subsystem finds
  (contradiction emitter, A/B variants, persona-outcomes,
  times_applied) are the questions. BACKLOG item filed same day
  (liveness registry + declared-vs-observed health readout sketch);
  design-first, no build commitment yet.
- **2026-07-29** — Captain's-log dual contract (Jeremy, decree-class):
  "I had envisioned the captain's log originally as data surfaced to
  the user about what the system had done… it keeps getting confused
  for a streaming event log, which is very similar, but different. So
  let's lean into that, and adorn the events that should be surfaced to
  the user, for a clean dual path here… I'm ok with this used as a
  queue, but IMO the log itself should be immutable." Implementation:
  `USER_SURFACED_EVENTS` registry + `audience` stamp at write time
  (`event_audience()` resolves unstamped historical rows), user lane =
  the original narration contract (run-report "Run activity" + CLI
  `--audience user` read it), system lane = the blessed immutable
  event stream that consumers (contradiction adjudicator) may read as
  a queue. Immutability holds: rotation archives, never deletes.
- **2026-07-29** — Maro-level systemic metadata gets its first home
  (Jeremy, decree-class, same conversation): meta-data "systemic,
  outside of the scope of a goal run… has never had a real home. This
  can be a good starting place for that" — skills/memory architecture
  is functional data, not metadata. Home seeded as
  `memory/system_health.json` (state in the store, transitions in the
  log); expected to expand. Cadence per his correction: "not a cron,
  let's hook into our startup/closure of the goals to do this
  maintenance work" — probes ride loop_finalize beside
  run_skill_maintenance. maro-doctor is a bootstrapping tool, not this
  surface; the old monitoring webpage precursor is also not it.
  Shipped 2026-07-30: `src/system_health.py` (DECLARED_PROCESSES
  liveness registry, 6 probes, report-only, killswitch
  `health.probes_enabled`) + HOUSE_STYLE declared-liveness rule.
- **2026-07-30** — Decree-dynamic working agreement (Jeremy): "Let's
  leave it on me for now to ask (like I did this time) and you should
  proceed as normal. I'll attempt to speak up sooner when I think
  something is off… I'm concerned that if my words aren't good enough,
  you'll take it and run with it." Reading "proceed as normal" as:
  keep the current autonomy posture, don't shift into asking-mode; he
  owns requesting the design-space view pre-implementation. Two
  Claude-side practice changes adopted in response (not machinery):
  (1) Decisions-line captures now make the *reading* visible — when a
  line resolves an ambiguity his phrasing left open, the resolution is
  named ("reading X as Y") so interpretation drift is catchable at
  capture time; this entry is the first instance. (2) Jeremy's silence
  is held as "playing out, unconfirmed", not endorsement — he
  sometimes withholds a concern until he can tell signal from shadow.
  Half-formed "something smells off about X" messages are explicitly
  actionable. Not piped to the runtime decision journal: this governs
  the dev relationship, not goal runs.
- **2026-07-30** — Amendment to the above, same conversation (Jeremy):
  "Silence isn't consent, but it's not disagreement either, it's
  literally 'I trust your judgement.'" The prior entry's "playing out,
  unconfirmed" reading was too dark — trust is the base rate of his
  silence; watching-a-concern-mature is the exception, and he is "not
  silently judging you for three weeks." First live catch for the
  visible-reading practice: the engraved reading was corrected within
  one turn of capture.
- **2026-07-30** — The §10/§9.7 taste conversation (Jeremy, decree-class
  outcomes; full record `docs/conversations/2026-07-30-taste-camera-player.md`,
  distillation COMPOUND_THINKING_DESIGN.md §14): (a) **LLM-as-player
  ratified** — "glad you like the LLM-as-player. I've been pushing for
  the harness as the engine for a while, but somehow couldn't
  communicate the discrepancy other than 'feels off'." The harness/
  context assembler is the game engine; its job is immersion (coherent,
  useful-if-imperfect patterns at the right level of detail), not
  truth-completeness. (b) **Systems correction**: "the pieces
  interactions _is_ the machinery, or at least a fundamental part of
  it. We're deep into systems territory… it's all connected" —
  reading this as: never design the composer as if the primitives were
  independent; interaction design is first-class machinery work.
  *Amended same day (Jeremy sidenote, on reading the summary): "the
  independent 'simple' pieces are truly independent _in addition to_
  being core integrated machinery" — both properties hold at once;
  the engraved reading overcorrected ("a bit of a useful
  misunderstanding overall"). Amendment piped to the runtime journal.*
  (c) **Camera terminology adopted literally** as design language
  ("we need to frame our setup properly, in the language sense").
  (d) Camera readout agreed ("agree with the 'don't build the card,
  reconstruct it'"). (e) **Five-lens persona panel commissioned** —
  "run our conversation through their lenses, and see what contrasting
  information falls out — possibly trying this out in a
  star-skill-style pattern… Sort of a recursive learning test, but
  also a test of the pattern applied with prompting"; persona
  selection delegated ("your choice"). Reading "star-skill-style" as:
  Claude as master (taste = lens selection, judgement = contrast
  harvest), serial one-shots, ledgered — not a full star skill
  invocation. Record: docs/history/2026-07-30-taste-lens-panel.md.
  Both design decrees (a)(b) piped to the runtime decision journal.
- **2026-07-30** — Five-lens panel verdict: **PASS** (ledger + harvest
  in docs/history/2026-07-30-taste-lens-panel.md). Five distinct
  missing-concepts (interest management / mental representations /
  coverage + chosen-lie / policy resistance / contextual bandits with
  regret); unanimous reconstruct-historical-forks-before-building;
  three-lens qualification of 14f's "judgement makes practice
  trustworthy" → necessary-not-sufficient (Goodharted criteria,
  verifier independence from generator, explicit coach function) —
  folded into §14f. Two-lens n<10 ceiling: narrow targets only
  ("library of scars"). Camera-readout spec upgraded: ONE fork-replay
  readout, five instrument column-families, first question =
  engine-failure vs player-failure. Full session transcript archived
  per Jeremy's request:
  docs/history/transcripts/2026-07-30-taste-session.jsonl.gz.
- **2026-07-31** — Panel follow-ups (Jeremy): (a) **Battlefield Earth
  clarified** — "wasn't the one single perfect angle — it was set up
  to be a KNOWN angle... one of many possible ways to get that
  information": the essence is engineered observability, not a magic
  shot; the romanticization was the distillation's (§14f note).
  (b) **n<10 conceded as guess-wish, bar lowered**: mid-goal the seed
  needs only "enough of the diffused outline to get the 'player'
  interested and working in the right direction until the next
  render" — directional orientation in a refinement loop, not
  compiled expertise. (c) **Nothing-provable pattern named**: the
  panel rejected nothing "because there's nothing provable" — design
  reviews are unfalsifiable-by-default (vs code reviews' 30-50%
  verify-against-tree kill rate); lever = demand each design finding
  name its falsifier. Recorded as recurring house pattern
  (anti-hallucination memory updated). (d) **Replication arm run**
  (his "worth trying on claude's models?"): same five prompts
  verbatim on claude-sonnet-5 (codex arm was gpt-5.5 medium; box
  codex default since moved to gpt-5.6-terra high). Pre-registered
  predictions scored: data-first replicates (P1 ✓), Goodhart family
  reappears escalated to named model-collapse + structural-vs-role
  verifier independence (P2 ✓), concept slots diverge 4/5 (P3 ✓),
  Claude-arm-softer-tone REFUTED (P4 ✗ — at least as sharp, more
  literature-anchored). Known confound: Claude arm read the
  post-panel tree (pin seed docs to a git blob next time). Yield
  split: double-confirmed core (data-first, structural verifier
  independence, coverage/variant-framings, mental representations,
  bandit baseline) vs per-family frontier (rollback/desync + demo
  recorder, desirable difficulty + naming problem, lighting-as-
  argument, seed-outflow + shifting-the-burden, model collapse +
  Algorithm Distillation + off-policy eval). Full record + Appendix B
  in docs/history/2026-07-30-taste-lens-panel.md.
- **2026-07-31** — Panel round 2 (Jeremy: "Want to try 2 more full
  panels, one on sol xhigh and one on opus 5 xhigh?" — his gut
  pre-registered: sonnet breadth, opus/fable depth, higher codex
  models grate). Same five prompts verbatim, inputs pinned at 810d3f0
  (blob SHAs in the record), measured quantity = new material beyond
  the record. Scored: saturation REFUTED (both arms denser than round
  1 — frontier-xhigh found a lower stratum); sol-spec/opus-conceptual
  split REFUTED (opus did both at once); Jeremy's opus-depth gut
  SUPPORTED (opus-xhigh the standout arm of all four: most surgical,
  grounded in our specific incidents, in-character). **Design
  consequences accepted**: (1) camera-readout method OVERTURNED by
  six-lens consensus — retrospective clustering demoted to hypothesis
  generation; upgraded to log-forward (axes + candidate framings with
  discard reasons), controlled one-axis replays, replay-twice
  divergence calibration first. (2) Immersion caveat — coherence
  never smoothing of error-relevant signal (14c amended). (3)
  Practice-arena contract gains balancing-arm clauses: exploration
  reps on successes, variety-ratio monoculture alarm, seed-edit
  cadence below framing→verdict delay. (4) opus-ml eval ladder
  adopted as cheapest next actions when the arc activates: judge
  backfill over 1,448 rows + ~50 Jeremy-calibrated labels (κ>0.6
  gate), log axes now, overfit-one-batch (Jeremy hand-writes cards
  for 3 known failures — can't-flip = kill). **Held for Jeremy**:
  14a's method-vs-world scope axis contested from both model families
  (structural invariance vs semantic category; n=40 single-family
  artifact risk) — settled conclusion, so re-examine, don't silently
  amend. Meta-finding: frontier-xhigh lenses shipped falsifiers with
  their findings (the "nothing provable" pattern inverted at that
  tier) — argument for spending expensive models on design review
  specifically. Full round-2 section + Appendices C/D:
  docs/history/2026-07-30-taste-lens-panel.md.
- **2026-07-31** — Review-to-fixpoint (Jeremy, decree-class, piped
  637d58d9): his work practice on shipping code — adversarial review,
  fix criticals/highs + easy lows, re-run; converges to low-only
  findings by round 3 (rarely 4), and rounds 1-2 almost always find
  highs. "We don't do that here due to time, but we probably could
  when it matters." Standing option imported: for load-bearing work,
  iterate review rounds unattended (his time is the constraint, not
  compute). Empirical rider under test: round-3 fixpoint arm launched
  same night (Opus 5 xhigh, five lenses, post-fix tree pinned at
  a35aafa) with pre-registered predictions — does DESIGN review
  converge to lows like code review, or does an unbounded design
  space keep yielding new highs at fixed model+tier? (Round 1→2 yield
  rose, but the tier jump confounds it.)
- **2026-07-31** — Panel round 3, the fixpoint test (overnight, Opus 5
  xhigh, post-fix tree at a35aafa; pre-registered F1-F3): **design
  review did not converge** — 1/3 predictions survived. Round 3
  attacked the round-2 accepted fixes with new arguments
  (counterfactual logging = chooser self-report, needs an EXOGENOUS
  variety arm; log selection PROPENSITIES not prose reasons →
  off-policy evaluation offline; byte-exact record/replay is
  prerequisite to the controlled-replay instrument — "there is no
  tape") and produced new overturn-grade findings at fixed
  model+tier (outcome-vs-process feedback: verdicts in camera-axis
  vocabulary or 14f's compile loop plateaus; frozen weights ≠ frozen
  policy — seed optimization converges at n=30-100; "good run" never
  defined independently of its own done-means, which CAUSES the 3%
  verdict density; NMS analogy structurally dead — missing axes, not
  wrong settings, are the card's real failure mode and no
  retrospective readout can see them; verdict-blind lanes are
  plumbing deferred behind a design). **Why no convergence,
  reflexive**: the panel's own systems lens — this design loop's
  measurable output across rounds is MORE open threads; code-review
  fixes remove findings from the artifact, our fixes-as-prose grow
  it. **Accepted, effective immediately**: balancing arm on the
  review loop itself — panel CLOSED, no round 4, next
  panel-adjacent artifact must be a measurement not a document; plus
  amendments to my own round-2 acceptances (exogenous variety arm,
  propensity logging, tape-before-replays, noise floor gates all
  quantitative claims incl. navigator 58/41). **Held for Jeremy**:
  verdicts-in-camera-vocabulary (14f), seed optimization (14b),
  independent good-run definition (decree territory), and the
  observation-repair ablation on Godot/db37d525/rewriter as the
  first measurement (cheap, decisive, uses existing runs). 25 lens
  outputs, 3 rounds, 2 families; record + Appendix E:
  docs/history/2026-07-30-taste-lens-panel.md.
- **2026-07-31** — Dispatch operating split (Jeremy, decree-class):
  Hermes instructed to stop over-prompting. The split: Jeremy =
  natural, sometimes incomplete intent; Hermes = supplies only
  missing references and safety/scope boundaries; Maro = figures out
  retrieval, relevance, decomposition, evidence, and synthesis.
  "Over-specifying converts Maro into a narrow executor of my
  analysis plan and conceals whether it can actually perform the
  research-and-synthesis behavior you want... If it fails this
  version, that is much more useful diagnostic evidence than another
  carefully engineered prompt." First probe: task-20260731T143920Z-
  ff9bde7d (X-link contrast vs maro repo, research-only). Connects to
  the panel's diffused-outline thread (minimal seed = enough to get
  the player working in the right direction) and the ablation logic
  (minimal-prompt failures are measurements). Same morning: Jeremy
  playing Switzerland on the round-3 held items — right-pieces vs
  go-deeper delegated to Claude; implementation authorized "in your
  judged correct order today." Claude's ruling: pieces are right,
  panel stays closed; order = observation-repair ablation first, then
  log-forward instrumentation, then verdict-blind lanes.

### 2026-07-31 — Observation-repair ablation (measurement #1) — thesis survives, with a twist
The panel's decreed next-artifact-is-a-measurement ran: 3 canonical failures
× broken-vs-repaired observation × 2 replays, real seams, today's player
(docs/history/2026-07-31-observation-repair-ablation.md; harness + raw at
~/.maro/experiments/observation-repair-2026-07-31/). Pre-registered ≥70%
repaired-arm recovery: **6/6 (100%) — player-inversion thesis survives; the
context assembler stays the roadmap.** Twist, per the pre-registered
broken-arm clause: rewriter/cobalt-pine and db37d525 broken arms
self-rescued 2/2 each — both anecdotes NO LONGER REPRODUCE (engine fixes
since + player drift indistinguishable) and are retired as live evidence.
The load-bearing contrast is godot: broken 0/2, repaired 2/2 — and the
broken arms did NOT loop on the same axis; they confidently invented a
fresh-axis wrong theory (texture filtering) with no load-verification step.
**Missing axes → confabulation, not repetition** — convergence brakes can't
catch it; only widening the observation channel can (Chunk A camera frames,
context assembler). Secondary: broken observations ~2× wall-clock in 2/3
families even when the outcome recovers (n tiny, advisory).

### 2026-07-31 — Chunk A shipped: camera frames log-forward (recall lesson-selection fork)
Round-3 disposition made real, instrumentation-only by construction:
`query_lessons_scored`/`hybrid_rank_scored`/`_tfidf_rank_scored` surface the
scores rankers always computed (plain functions are now strip(scored) —
equivalence test-pinned); `src/camera_log.py` writes one frame per loop-slice
recall to run-keyed `source/camera_frames.jsonl` (candidates per source with
raw scores + score_share honestly labeled not-probabilities, flat top-ups
score=None, chosen==rendered IDs, substrate-size axes; killswitch
`camera.frame_log_enabled` default ON; never raises; no-run-dir frames drop).
Selection semantics byte-identical (n=10 fetch is the road-not-taken window;
first-3 selection pinned vs query_lessons top-3). Consumer ships same chunk:
`python3 -m camera_readout` — coverage, axis composition, candidate/chosen
stats, run_card verdict join, crude overdraw v1 (echo-proxy, labeled upper
bound). Suite green incl. two pre-existing census breaks fixed in-chunk
(status:record frontmatter ×3, py-modules camera_log/camera_readout/
system_health). Frames accrue as instrumented runs execute.

- 2026-07-31 — Chunk-A adversarial review (3 Codex lenses vs 184e8b2):
  CONTESTED, 7/7 verified, 0 hallucinated — eleventh clean round. All
  accepted + fixed: per-call file-lock timeout override (camera 2s, never
  the 30s default), verdict join walks agenda+untyped and splits by
  ranker family, degraded-fallback frames marked (no false
  "nothing chosen"), torn lines counted, stale-run-dir orphan guard,
  raw unrounded scores. Record:
  docs/history/2026-07-31-chunka-adversarial-review.md.

### 2026-07-31 — Chunk B shipped: verdict-blind lanes closed + verdict-flow readout
Panel round 3's prescription executed in its stated order ("instrument the
flow before the stock — then close the two verdict-blind lanes"). Live
census first: agenda flow is healthy since W28 (18/34 → 6/12 judged); the
3% all-time stock is April-era pre-verdict rows (no backfill, by decree) —
the real live leaks were evolver_verify (measured suite verdict never
plumbed to the row) and NOW (judge machinery task-path-only). Closures:
(1) `_verify_post_apply` records `goal_achieved=passed,
goal_verdict_source="deterministic_tests"` — the suite IS the judge;
(2) interactive NOW judged via hosted-free family ONLY
(`now_self_verdict_free`; killswitch `now_lane.interactive_self_verdict`
default ON, inert without keys; paid adapter never judges — keep-raw-speed
stands; provenance guard alone when unkeyed); (3) `stamp_outcome_verdict`
stamps `goal_verdict_at` (unverifiable stamps included — "judged, could
not verify" is a verdict event), making framing→verdict delay measurable
for the first time. Readout `python3 -m verdict_flow`: stock/flow
separated, verdicts/week by lane, source tabulation,
never-stamped vs stamped-unverifiable split, delay buckets
(<1m…>24h), honest not-computable block (37 historic closure rows
predate the stamp). First live numbers: at-record verdicts 4, all
pre-chunk NOW/evolver rows honestly unjudged going forward from here.

- 2026-07-31 — Chunk-B adversarial review (3 Codex lenses vs 5f74d46):
  REJECT-as-reviewed → remediated same session, 7/7 verified, 0
  hallucinated — twelfth clean round. Unanimous high: the interactive
  judge violated the chunk's own keep-raw-speed constraint (serial
  provider timeouts) → daemon-thread judge under a hard 5s budget.
  All-lens high: "verdicts/week" was rows-by-record-week → flow now
  counts verdict EVENTS by arrival week (live effect: honest 4
  known-arrival events, not 18/34-style historic-stamp illusions;
  flow measurable from today forward). Also fixed: unverifiable
  stamps count as verdict events; evolver elapsed_ms measured (was
  fabricating <1m); goal_verdict_at wins over at-record source
  assumption (agenda provenance stamps post-record); "inert without
  keys" doc corrected to the real double consent gate (keys +
  validate.hosted_free.enabled — coupling kept, it IS the consent
  posture); judge failures stamp now_self_verdict_error so a broken
  pipe never reads as unjudged-by-design. Record:
  docs/history/2026-07-31-chunkb-adversarial-review.md.

- **2026-07-31** — Lesson-decay direction (Jeremy, decree-class, piped
  55c877da): calendar decay (0.85/day) was "mostly placeholder" — decay
  should re-anchor on competence-redundancy (Opus/LeAct steal #1, Jeremy
  concurs). And the tenure side: successful one-offs should be leveraged,
  not re-derived — "would be nice to leverage those better, rather than
  re-inventing those wheels 3 times independently. Hard to replicate happy
  accidents, but repeat patterns should be examined and recognized for
  better-than-luck shapes that they likely are." Reading: independent
  restatement is a better-than-luck SIGNAL to examine, not a tenure GATE;
  one-off successes need a route to leverage that isn't 3x re-derivation.
  Direction only — build stays gated on the verdict-flow revisit trigger
  (BACKLOG LeAct entry, amended a1d4bb3). Surprise-read corpus snapshot
  queued to the Reading tab
  (docs/history/2026-07-31-lesson-corpus-surprise-read.md) — mechanical
  pre-read finding: the ENTIRE long tier (4 rows) got tenure via the
  recovery path re-minting identical template strings (sv 7-35 = machine
  restatement), and all pre-chunk-6 rows carry novelty 0.00.

- **2026-07-31** — Paused-state design (Jeremy, decree-class, piped
  7afe8b3a): runs get a PAUSED lifecycle state with a typed reason —
  error-class (disk full, LLM unreachable, no tokens, power loss) or
  operator-class (awaiting clarification, manual intervention). External
  interrupt dissolves into paused rather than becoming a fifth stop
  verdict ("is that a state that may or may not ever be finished?");
  the four stop verdicts stand. Advisory judges never pause — they stamp
  judge-error-and-continue (Chunk B pattern); only blocking dependencies
  pause. Captured as COMPOUND_THINKING_DESIGN §13e.
- **2026-07-31** — Fan-out chunking posture (Jeremy, decree-class, piped
  8c7f5068): "an honest good enough with clear edges to upgrade
  independently later, with work items to revisit, becomes our more
  natural chunking points... I kinda feel like we're not fanning out like
  we could." Build order delegated (he declines to be the blocker): §12
  order stands (#4 stop-verdict split → #2 recon flavor, star-first),
  executed as slices with named upgrade edges, independent lanes fanned
  out to subagents (box OOM limits still respected). First fan-out same
  session: stop-seam refresh audit + fail-open census audit launched as
  background agents feeding slice 1. Coordination note: Jeremy has an M1
  opus lane on a BACKLOG test item (auditing/bugfixes first) — confirmed
  2026-07-31 as the live-learning test arc (LT., landed 861ab28); its
  provenance census ran on the box same day
  (output/provenance_census/census.{json,txt}).
- **2026-07-31** — §13e slice 1 SHIPPED: typed pause reasons
  (stop_verdicts.py PAUSE_* vocabulary + stamp_pause rail + writer sites
  + run-card forwarding; tests/test_pause_reasons.py). Upgrade edges
  named in BACKLOG ("Paused-state upgrade edges"). Side-find pinned: the
  reflect_and_record kwarg seam swallows TypeErrors via finalize's
  catch-all — a signature drift there silently zeroes all learning data.
- **2026-07-31** — §13e slice 2 SHIPPED: fail-open judge-error marks.
  Census verified 5/5 (first 0-hallucination audit round of this class) +
  found a third quality-gate fail-open the census missed (no-JSON
  fallthrough). `judged: bool = True` on StepVerdict/ArtifactVerdict/
  QualityVerdict, False at every fail-open site; ralph thread-brain line
  now types `verify-error:` instead of forging `ralph-verified:`; gate
  emits GATE_ERROR events (honest denominator); inspector unjudged 0.7
  alignment no longer earns "good"/completion-delight (it sat exactly at
  _ALIGNMENT_GOOD — every adapter-less heartbeat inspection of a done
  run graded good with zero evidence). Behavior held fail-open
  everywhere else; learning-writer judged-awareness + the DEAD
  auto-promote validation wiring (claim 3) are flagged decisions in
  BACKLOG ("Fail-open judge-error edges"), not defaults.
  tests/test_judged_markers.py pins the boundary.
- **2026-08-01** — Promote-validation wiring (Jeremy: "Let's fix the
  promote validation"): the flagged proposed-default accepted — the
  evolver call site now passes its adapter to maybe_auto_promote_skills,
  making the Voyager validation harness + repair loop live for the first
  time since Phase 32. Promotions can newly fail and be held provisional.
  Fail-open kept as graceful degradation (numeric-gates-only), stamped:
  SKILL_PROMOTED context carries validation=passed|unjudged|skipped.
- **2026-07-31** — Slice-1 adversarial review (3 Codex lenses, CONTESTED
  → remediated same session; 11/11 verified, 0 hallucinated): pre-start
  refusal stamp now carries pause_reason (unanimous #1 — the kill-switch
  reason evaporated, "interrupted" has no fallback by design); finalize
  empty no longer erases stamped writer-died history (`or None` — resume
  contract now real); stranded-sweep commit moved under the metadata
  lock with in-lock status re-check (forged-provenance race, pre-existing
  but §13e's business); ledger validates the pause vocabulary at ingress.
  Rejected: alias removal, reserved-reason deferral, pause_family
  deletion (consumer-first tension carried openly). Record:
  docs/history/2026-07-31-slice1-adversarial-review.md.
