# Goal-Brain: maro-orchestration

This file is two things at once:

1. **The goal-brain artifact definition, v0** — step 1 of the 2026-05-18 sequencing
   (define the artifact → recall() shape → navigator schema → navigator prompt).
   Defined by example rather than by spec: this file IS the format.
2. **This project's own goal-brain** — the compiled truth a session operates *from*,
   as opposed to the docs it looks things up in. The bootstrap loop from the May-18
   conversation ("we need the system we're building in order to build the system")
   starts here: the project dogfoods its own steering artifact.

The load-bearing concept (Poe-codex, 2026-05-18, quoted): *"we're not escaping LLM
trust, we're redistributing it, so the human-readable goal-brain becomes the actual
non-LLM anchor."* This file is that anchor — human-readable, diffable, editable.
Goal-brain = steering wheel; everything else in the repo = residue.

---

## Format rules (the artifact definition)

| Section | Owner | Rules |
|---|---|---|
| Intent | **human-steerable** | Jeremy's words, quoted verbatim. Sessions may not paraphrase or "improve" them — paraphrase is how telephone flaws start. |
| Invariants | **human-steerable** | Verbatim quotes with dates. A session may *add* a newly stated invariant; only Jeremy retires one. |
| Compiled truth | system-maintained | Only claims **verified against code or conversation this arc**, each with its basis. No aspirational claims. Superseded beliefs get moved to Decisions with a date, not deleted. |
| Decisions | system-maintained | Append-only, dated. Reversals are new entries pointing at what they reverse. |
| Threads | system-maintained | Every open line of work, including dormant ones. This is the fan-out defense — "we'd follow one thread of many and never go back and revisit" (Jeremy, 2026-05-18). A thread leaves this list only by being finished or explicitly dropped. |
| Open questions | system-maintained | Questions that shape downstream design, with what they block. |

What makes a goal-brain *good* (from the May-18 conversation: "If it's well-shaped,
drift becomes recoverable; if it's mushy, no amount of clever navigation saves you"):
every claim has a verification basis; invariants are quotes, not summaries; decisions
are dated and append-only; no thread is silently dropped; short enough to inject
whole. When this file disagrees with any other doc, this file wins until corrected —
all other project docs are best-guess by decree (see Invariants).

Update discipline: sessions update system-maintained sections at end-of-chunk
(same rhythm as the document → commit → push rule). Human-steerable sections change
only when Jeremy says something new.

---

## Intent (human-steerable)

North star (VISION/CLAUDE.md, long-standing): a self-improving autonomous agent —
takes a mission, decomposes, executes over days/weeks, learns from what works,
reports without hand-holding. Visible → Reliable → Replayable.

Current arc (Jeremy, 2026-06-10): *"You can consider getting this project working
and in shape a /goal target."* — working and in shape outranks new capability.

On what matters most (Jeremy, 2026-06-10): *"my gut says that a real, working memory
is the key (meaningful facts, pattern matching and fuzzy logic, skills and/or maybe
learned lessons and so on... all the flavors of persistent working knowledge)."*

The orchestrator litmus test (Jeremy, 2026-06-11): *"I think our orchestrator litmus
test is going to be something in the direction of a bunch of lesser local models +
orchestration being greater than the sum of it's parts. Kind of a ways off and a
high bar, unclear if it's realistic or not."*

On the capability-form paradigm — skills-as-prompt-injection vs crystallize-to-code
(Jeremy, 2026-06-11): *"His [Garry Tan's] paradigm might be a bit more efficient,
turning data into prompt injection just in time, rather than saying 'we keep
scraping X links, so let's write a python script to handle that for us'. Potentially
both work just fine, but one grows with the model over time, while the other
doesn't."* And: *"the hard part; choosing one of the paradigms above (or finding a
new one) should all be on the table if we do this right. Hard to find the right
equatinos up front before we do it all longhand over and over again."* — paradigm
choice is deliberately deferred to data, not decided upfront.

On entropy (Jeremy, 2026-06-11, background context not a directive): *"whatever we
do will, at some point, likely need a dose of entropy in it as well; same as
people's memories decay... life moves forward and is in constant change. as much as
I want to be able to identify things like skills-as-shell-scripts (which are going
to exist), that doesn't mean they won't inherently change over time; X's interface
will change, browsers will have new standards, MCPs will become available, and
more."* The system must *"allow the system to appropriately change and [be]
different enough from a person's bran so as to not lose the benefits we enjoy from
computerization."* And the fidelity intuition: *"feels like there's going to be a
'close enough' type simulation, like the mesh of the ground in a video game, along
with a general physics engine; it's not the earth, but it approximates it well
enough that you don't usually notice."* Follow-up same day, pinning the first
pass: *"my gut's saying that the first pass of a decay is to add the existing
mechanism + failure to the prompt to re-fight the battle; at worst we have better
context, at best it's a slight tweak and we fix forward."*

## Invariants (human-steerable, quoted)

- **Fix in place, don't rewrite** — *"If you think we're on the right track and fixing
  the implementation is better than going down the rewrite path, let's do it."*
  (2026-06-10)
- **Program, not operating system** — *"I'd like to keep this as a program/app, rather
  than an operating system (i.e. not a cron job; disabled some of those at one point
  because we had rogue processes going periodically, not in a good way)."* (2026-06-10)
  No cron, no daemons; background work rides inside normal app lifecycle.
- **Installable harness** — *"ideally this is a harness you install, not a single
  machine setup."* (2026-06-10)
- **Docs are best-guess** — *"consider all of what we've documented in the project as
  best guess, and even then it's littered with poor assumptions and
  telephone-via-AI-interpretation kinds of flaws."* (2026-06-10) Verify against code
  and conversations before building on documented claims.
- **Make a call and move** — *"When in doubt, make a call, document it, and move
  forward with the possibility of reversing course later."* (2026-06-10)
- **Good software management** — *"Lean towards good software management, not one-shot
  throwaway code."* (2026-06-10)
- **Recurring across every reframe** (session-40 audit of all design generations):
  figure-it-out autonomy; delight-with-progress; learn-to-get-cheaper;
  verified-done-not-reported-done; zoom+rotation perspective shifting.
- **Harness is the answer to models getting more action-biased** — *"new LLM
  updates, leaning even more into action, the solution is always the
  orchestration harness. I think we're working towards a good balance, and
  the ecosystem will change under us."* (2026-07-03, prompted by three
  same-night incidents of delegated subagents shipping past their authorized
  scope — see `feedback_fork_scope_overrun` in Claude Code's cross-session
  memory, and the Decisions entry below.) As underlying models get more eager
  to act, prose scope framing ("don't commit," "read-only," "just research
  this") degrades faster, not slower — the harness has to compensate with
  structural boundaries, not better wording. This is a standing filter for
  every future capability, not a one-time fix.

## Compiled truth (system-maintained; basis noted per claim)

**Test-suite truth + reduction pass SHIPPED 2026-07-14:** the project-wide
`addopts` no longer silently removes `slow` tests from commands documented as
full. `test-safe.sh` has explicit full/fast modes and real 40-file chunking;
the stale smoke script uses `cycle`, resolves `~workspace/` artifacts, and
keeps executive-summary LLM work out of smoke via `status --dry-run`.
Behavioral consolidation removed duplicate parser suites and repeated expensive
doctor/repository scans while preserving the workspace/credential/LLM safety
fixtures. Verified current state: 6171 tests (from 6333), slow lane green in
13.0s, raw honest-full green in 117.8s, canonical chunked full green in 104.0s,
and 78.04% line coverage against the 70% floor. The old 141s baseline was
incomplete because it omitted slow tests, so the new full result is both faster
and strictly more honest.

**BACKLOG #23 async-escape family closed (SHIPPED 2026-07-12, commits
71f6e4f/f241cf0/a0b462b/2e24594; basis: 62 new tests + targeted suites
green):** all seven mechanisms from the polymarket-edges r2–r4 saga.
(a)+(g) done→blocked demotion at the complete_step seam — deterministic
detectors for async-escape promises and unprobed env-limitation claims
(agentic lanes only, waived on probe evidence), EXECUTE_SYSTEM
SYNCHRONOUS EXECUTION contract, targeted retry hints in loop_blocked
(re-execute SYNCHRONOUSLY / PROBE with `ls`), ralph-verify fallback on
untagged reasons. (e-residual) in-flight cost kill: `stream_probe` hook
in `_run_subprocess_safe` + `_build_stream_cost_probe` — stream-json
usage blocks cost-estimated as they arrive, subprocess killed
mid-flight on ceiling cross via the existing BudgetRunawayError
plumbing (claude lane only; codex NDJSON differs). (c)+(d) decompose:
goal-stated priority order detected and injected as a BINDING directive
into all lanes ("exhausted budget strands the LAST priorities, never
the first"); rate-limited batch sizing (~5 sequential rate-limited ops
per step) in TIME BUDGET. (f) deliverable-path check: step-named write
targets must exist (resolved against project_dir) before done is
accepted; miss demotes with the exact paths in the retry hint. (b)
re-scoped: true dead-air kill has existed since April (liveness
timeout, a44eb6a); the 89cb097a specimen was a worker ACTIVELY POLLING
a background job — live-but-useless, prevented behaviorally by (a).
The demotion-at-seam + targeted-hint pattern is now the standing
mechanism for corpus-family guards. Archive: BACKLOG_DONE.md.
**Live validation same night (run 75fe8b4e-jaunty-falcon, sonnet-led,
Jeremy-requested):** 7 steps/14min/$1.62 vs $2.00, goal_achieved=true,
all 3 deliverables at exact named paths, 6 rate-limited fetches split
3+3, cooldowns foreground, priority order held, zero guard firings
needed. Surfaced + fixed two residuals (e5e0cfa): (1) priority
directive was unreachable under cuts-first — the probe path returned
above the injection point; now detected before the cuts block and
carried across boundary expansion; (2) provenance falsely demoted the
achieved run — first-glob-hit resolution matched an older project's
stale step_data.json/step-7-output.txt twin; resolvers now judge
freshness across ALL candidates. Specimen card: goal_achieved=true +
status=incomplete = done≠achieved catching its own verifier's error.

**Cuts-first planning v0 (Qix-cuts decree, SHIPPED 2026-07-10, 068eddd):**
decompose no longer has to commit a full plan over the unbounded space.
Config-gated `planner.cuts_first` (default OFF; ON on this box):
`planner.draw_cuts()` makes one narrowing call — committed constraints
with named basis (prior knowledge / goal text / provided context) + 0–2
cheap probes + a one-sentence bounded remainder. Probes become the
plan's first steps followed by a `[boundary]` marker; the marker is
expanded mid-loop WITH probe findings in ancestry context
(loop_execute), unlike milestone expansion which re-decomposes blind.
Bounded-without-probes cuts inject as COMMITTED CONSTRAINTS into normal
decompose; wide/deep goals skip (staged-pass already narrows); recovery
re-decomposes pass `allow_cuts=False` while the director replan keeps
cuts — that is the v0 re-draw (boundary cap 2/loop). This implements
the evidence-fed half the constraint-orchestration lineage never
shipped: scope.py draws all its lines from the armchair in one call;
real cuts interleave a cheap peek between lines. Deferred: >2 narrowing
rounds, first-class re-draw triggers, #5 dispatch-level planning-depth
field (separate thread, still queued), NOW-lane cuts. Basis: commit
068eddd, 20 tests in test_cuts_planning.py, DEFAULTS.md row, acceptance
run = Manti canonical case (results in CAPABILITIES.md).
**Acceptance run verdict (8177541b, 2026-07-10):** content PASS — cuts
drew Jeremy's exact human heuristics unprompted, 2 committed steps vs
the baseline's 7, run finished inside the $2.00 budget (baseline
hard-stopped at $2.47), tokens −39%. Wall time did NOT improve (~28 vs
~24 min); the anatomy said why: 852s of 1671s was between-step
overhead, 454s of it local-qwen validation (11 calls × ~41s, all
passed; lifetime ROI ~$0.64 saved). Two same-day fixes off the run's
own evidence: closure brittle-grep false-negative → failed static
checks now attach the probed file's actual content to the verdict call
(2830f48); ladder latency breaker `validate.local_max_latency_ms`
(4957448) — first over-cap local verdict switches the process to paid
(~6.5s/call). Next envelope lever identified, not started: micro-step
boot tax (~35s fixed per step; expansions emit read-only micro-steps).
Clean re-run in fresh project `manti-clean-rerun` launched same
evening for uncontaminated cost numbers.
**Clean re-run + envelope root causes (2026-07-11):** re-run 3bffa6d6
PASSED clean — goal_achieved=True, 0.88 conf, $0.32 step costs (vs
$2.47 baseline hard-stop), fresh research path (reverse-engineered the
pure-gas.org KML API from the site's React bundle + OSM
cross-verification). The 41s-validation mystery was NOT model speed:
box config had `ollama_keep_alive: "30s"` < validation cadence, so
every ladder call paid a ~25-30s cold reload (warm = 10-13s, under the
15s cap — local stays in play). Fixed to 10m + breaker cold-load grace
(28fb80f). Second tax: every `claude -p` boot handshook the user-level
Google Drive MCP (~3.7s × ~26 calls/run) — `--strict-mcp-config`
shipped (395e71c). Dogfood run fd483efb (Maro analyzing its own
envelope) produced 4 ranked proposals + an adversarial pass that
caught its own double-count; code verification killed 2 premises (no
per-sub-step expansion calls exist; event writes are µs appends),
confirmed 1 pair (closure ∥ quality-gate is safe), and re-attributed
P1's pool to the already-fixed ladder tax. Full adjudication in
BACKLOG. Same run exposed + same-day-fixed a closure false-negative:
behavioral-gap Signal 2 demanded a runtime probe of a document-only
goal off a \bprocess\b prose match (c37f42e — Signal 2 now corroborates
against deliverable shape). Envelope next-action = one post-fix
re-measure before any concurrency work; honest floor on this box
~8-10 min/errand-run (hardware pressure line in Decisions).

**Verdict-aware learning complete (data-r2-01, SHIPPED 2026-07-10,
9f07b80 — the last non-gated r2 blocker):** agenda-lane lesson extraction
+ skill crystallization no longer run verdict-blind at loop finalize. The
handle lane defers them (`defer_learning=True`) past closure judging;
`finalize_deferred_learning()` then extracts lessons with the stamped
verdict in hand (failure-flavored for done-but-not-achieved; restart
attempts covered; idempotent) and crystallizes skills only when the
verdict isn't a judged False. Chosen design: MOVE extraction, not
re-stamp — tiered-lesson dedup/reinforcement makes un-recording a wrong
lesson unsafe. Non-done statuses and direct run_agent_loop callers
(heartbeat/prereq/queue/cli — no closure runs there) keep finalize-time
learning. SF-2 is now closed end-to-end: rows, run metadata, read-side
consumers, AND the extraction itself are verdict-aware. Basis: commit
9f07b80, 9 tests in test_verdict_learning.py, session 2026-07-10.

**Canonical Manti case first live runs, 2026-07-10 (results in
docs/CAPABILITIES.md):** natural routing sent it NOW (0.85 conf) and
FAILED the Tier-1 contract — model-knowledge answer plus a how-to-search
list (passenger-does-the-steps anti-pattern; router has no
needs-live-external-data signal). Forced agenda lane PASSED on content
(research-brief.txt: one sourced bottom line — Maverik Ephraim 7.3 mi/24h
— per-station confidence, live store-page verification, stale-source
dissent) but failed the envelope: 7 steps, ~24 min, $2.47 cost hard stop.
Net: **capability verified, delivery target**; errand-research skill's
real spec = routing detection + ~minutes/cents envelope. Basis: run
artifacts under projects/where-can-i-get-nonethanol/, session log.
**Post-fix envelope measurement 2026-07-11 (run 5126986b, Jeremy: "kick
off a full new run of our now more performant pipeline with the manti
example"):** 6 steps / 16m43s / $1.52 / closure complete=True 0.95, 5/5
checks, zero gaps — first fully clean Manti card. Arc: $2.47/24min →
capped/28min → $1.52/16.7min. Remaining envelope levers: routing
(unchanged) + worker tool-loop artifact re-read churn (introspection
attribution now correct).

**AI-failure-pattern corpus, 2026-07-11 (Jeremy's AFK ask: "start an
orchestration research run on patterns... for common tasks that AI
doesn't quite get right... for our orchestration training"):** research
run 692bd96f-brisk-lichen delivered `research/ai-failure-task-patterns.md`
— 18 verbatim-quoted real tasks assistants got wrong (HN Algolia + Reddit
RSS), 5 pattern families, 11-category root-cause taxonomy, per-entry
orchestration-capability mapping, honest audit trail (Reddit body text
403'd on every endpoint → title-only evidence, flagged per-entry). Seven
entries folded into CAPABILITIES.md Tier 1/4 as `target` goals. The run
doubled as live validation: closure evidence attachment saved the verdict
from a brittle jq selector (complete=True 0.82), no bogus behavioral-gap
downgrade, latency breaker tripped correctly under suite contention —
and it exposed the budget-breaker demotion bug (cost stop fired AFTER the
final step → finished, goal-achieved run stamped stuck/failed; fixed
8f8344a: breakers demote only when steps remain). Basis: run card +
captains log 692bd96f, commits 8f8344a/a940154. ~$0.57 card cost.

**Learning live batch, 2026-07-10 (Jeremy: "Let's run that, see how it
goes" — the data-r2-02 batch, after the cs-r2-01 gate shipped):**
- 9 sequential real goals via `python -m handle`, $2.74 total, all
  finalized. The learning loop is now proven end-to-end on organic runs:
  **the evolver had its first production firing ever** (cadence counter
  9→0, run 8f7419c8, post-mutation suite PASSED, nothing reverted);
  **skills-lite promoted its first skill** (changelog_digest — passed both
  the code-pattern gate and the new injection_guard gate; loader serves
  it); **tri-state verdicts flowed live** (4 success/True, 3 partial,
  1 done-unverified/None, 1 done-not-achieved/False); **closure caught a
  real fabrication** (worker claimed 120 archived entries; verifier
  re-counted and failed the run — done≠achieved earning its keep on
  organic traffic); #18 verdict-parity CLI exits confirmed (partial →
  exit 1). Basis: live-batch log + run cards + change_log.jsonl +
  outcomes.jsonl, session 2026-07-10.
- Decree-vs-code found: auto-apply gates on `environment != production`
  (evolver_store.py:488), not `evolver.auto_apply` as the cadence decree
  assumes — first firing auto-applied 4+1 prompt-level suggestions to
  playbook/lessons in "dev" mode. Box set `environment: production`
  same-day (record-only per decree); Jeremy adjudicated same day →
  production-always decree, `environment` key removed, gate now
  `evolver.auto_apply` (batch-01 SHIPPED, see Decisions). Also: evolver's verify→learn runs full unthrottled pytest
  in-process (batch-02, re-fired ops-r2-05), `_DANGEROUS_PATTERNS`
  false-positived on an instructional .md (funnel_report skipped for
  `open(` in prose, batch-03), and 9 finalizations extracted **zero
  lessons on any tier** (batch-04 — the funnel barely ingests).

**Purgatorio r2 re-run, 2026-07-10 (Jeremy /goal: "run the purgatorio suite
again and compare the results to the previous run"):**
- Same 7 eyes, delta-scoped to the fix wave (4e6dc1b..97aa5ef, 21 commits);
  all 82 r1 findings re-verified by live probe + 23 new findings, each new
  finding and each claimed blocker resolution independently adversarially
  verified (41/42 confirmed, 1 refuted). **No regressions found.**
- r1's 9-blocker list → **6-item r2 list** (see
  docs/audit-2026-07-r2/RECONCILIATION.md): 4 cleared outright (test-junk
  purge holding, bootstrap crash-loop unit deleted, README headline
  repositioned, security-doc honesty verified + user/ tip privacy), 3
  narrowed, 2 new — arch-r2-01 (containerized-executor design pass has NO
  vehicle in any queue: the decision-without-vehicle class, minted the same
  night its siblings were fixed) and docs-r2-01 (README Optional Services
  instructs copying unit files nothing creates; 2 of 3 don't exist).
- Best catch (from the one refuted finding): **ops-r2-05, live-reproduced**
  — test_heartbeat.py's sys.modules["config"] stub breaks
  proc_lock._run_dir's import → home fallback bypasses MARO_WORKSPACE
  isolation, so every full-suite run stamps the REAL workspace
  heartbeat.pid. SF-3's class, post-isolation.
- Standing residue themes: supervision story decided but three
  contradictory tellings ship (one cheap convergence chunk); learning
  engine wired but ~zero verified production behavior (needs a deliberate
  ~10-finalization live batch, with cs-r2-01 — skills-lite skips
  injection_guard — fixed first).

**Backlog-clearing session, 2026-07-10 (autonomous, /goal "finish the
outstanding items that don't need my approval"):**
- BACKLOG #21 both halves fixed and live-proven on this box: dormancy
  classification (`sheriff.dormant_days`, 14d default) took first-tick
  diagnosis targets from ~183 zombies to 0, and the sheriff health check is
  now lane-aware via `llm.detect_backends()` (this box: healthy,
  `ok: subprocess, openrouter, openai`); 113 stale projects archived to
  `projects/_archive/` via the new manual `maro sheriff archive --apply`.
  Basis: commit `a9824ce` + live tick before/after in the session log.
- Rider A (skills-lite two-tier promotion) is implemented, default-ON by
  decree: `run_curation.promote_skills_lite` + `degrade_skills_lite`
  (companion provisional Skill carries the decay/circuit machinery; tripped
  companion quarantines the .md). Also the first BACKLOG #0 miner. Basis:
  commit `ccc20fc`, 10 tests, live no-op smoke on the real workspace.
- BACKLOG #18 both asks fixed: `maro run`/`maro resume` now run the same
  closure core as maro-handle (verdict loop-keyed onto outcomes.jsonl,
  done→incomplete demotion at the handle gate), and per-step artifact
  cleanup is deferred past the verdict (24h-grace sweep of *other* loops at
  finalize — no lane can destroy its own audit evidence). Residual kept
  open: the direct-CLI lane still creates no runs/<id> dir. Basis: commit
  `6c03068`, 11 tests, dry-run lane smoke.
- PyPI blocker #7 precondition resolved: `maro-orchestration` (the name in
  pyproject) is FREE on PyPI; bare `maro` is a squatter stub, `pymaro` is
  Microsoft's MARO. No rename needed; publish stays Jeremy's act. Basis:
  live PyPI checks recorded in docs/PUBLISH_CHECKLIST.md (`34241d9`).
- user/ overlay is now complete: the last two repo-copy readers
  (handle CONFIG.md/COMPLETION_STANDARD.md, heartbeat mcp_servers) resolve
  workspace-first via `config.user_file()`. Basis: commit `7c1086c` + test.

**Memory/learning, as of 2026-06-10:**
- The write side was always live (1,272 outcomes, 38 medium + 22 long lessons,
  5.9MB captain's log — session-40 audit, spot-checked on disk), but the lifecycle
  was dead: consolidation never ran, decay corrupted stores on every rewrite, and
  standing rules could never accrete. All fixed this arc — basis: commits `3bd28cd`
  (M1: read-time decay derivation + in-process dream cycle), `536a793` (M2:
  promotion-at-reinforcement + observe_pattern wiring + cross-tier dedup), `629b262`
  (M3: recovery lessons). The full path lesson → LONG → standing rule is now
  reachable in production; it has not yet been observed end-to-end in a real run.
- Post-loop self-reflection (Phase 44-45: diagnosis, lenses, recovery planning) was
  dead 2026-04-26 → 2026-06-10 via a swallowed NameError; skill rewriting
  (circuit-breaker recovery) was dead via a swallowed TypeError. Both revived in
  `629b262`; the bug class is locked out by a pyflakes suite test. Implication,
  unverified but likely: any "self-improvement isn't working" observations from
  May runs are explained by these dead paths, not by design flaws.
- Dry runs and the test suite were making real authenticated `claude -p` calls
  (token burn — the rogue-process failure class). Sealed at three seams in
  `3bd28cd`; conftest blocks the CLI binaries outright.
- The long-standing "claude subprocess failed (rc=1)" blocker decomposed into two
  real defects (M5 investigation, 2026-06-10): (a) the adapter trusted the exit
  code over the payload — the CLI can print a complete success result and still
  exit non-zero; now payload-first, with `is_error` as the load-bearing check;
  (b) error details were truncated raw JSON that buried the CLI's actual message
  (`is_error:true` results carry it in the `result` field, e.g. "Not logged in ·
  Please run /login") — now surfaced verbatim. Basis: live repro under a foreign
  HOME + `/tmp/claude_rc1_*.txt` dumps + regression tests in test_llm.py.

**Visibility/replayability, as of 2026-06-26:**
- "Visibility" was being vibe-claimed as done while the runtime record was lossier
  than we said — the test-corpus harvest (`scripts/harvest_corpus.py`, 569 captains-log
  slices distilled into committed fixtures) proved we had decision-level data
  (verdicts/scope/probes/step shapes) but NEVER persisted the assembled LLM prompt
  or raw response — no byte-level replay possible. Encoded as a 6-rung ladder in
  ROADMAP (definition-of-done); line was ~3.5/6.
- Forward record-mode shipped (commit `04cb52e`): `FailoverAdapter.complete()` is the
  single capture seam — `{prompt, response, tool_events, tokens}` per call →
  `<run-dir>/build/calls/call-NNNNN.json`, secret-scrubbed via single-source
  `src/secret_scrub.py` (shared with harvester). **Default ON**, off via
  `MARO_RECORD=0` / `record.enabled=false`. Carries rungs 5–6 forward-only; no
  historical backfill. This is the keystone that unlocks the Replayability stage.
- Post-goal curation shipped (same commit): `run_curation.curate_run` hooked in
  handle.py finalize writes `run_card.json` (outcome class — done≠achieved aware —
  + mineable inventory). Miner registry; v0 classify+inventory, miners (skill/script
  scrapers, decision-prior indexer, re-attempt hinter) are TODO. Intent (Jeremy,
  2026-06-25): "rather than just discarding the (probably paid for) data we've just
  gathered" — park it for later mining, keep it user-visible + prunable.
- Also closed this arc: the cwd leak in non-executor agentic paths (verify/quality_gate/
  pre_flight/claim_probe inherited launch cwd → verifiers re-created missing artifacts,
  fabricating ground truth). Fixed via run-scoped `_DEFAULT_SUBPROCESS_CWD` ContextVar
  in llm.py (commits `2d0acef`/`a886b46`). Basis: live repro, leak gone, verifier
  escalated honestly.
- Run-visibility real-data pass, 2026-07-09 (Jeremy: captain's log unsurfaced, meta
  missing, feature only ever mock-tested): report now reads the run's own
  captains_log_slice.jsonl (85% of real entries have no loop_id — the loop_id filter
  was structurally starving the report), surfaces per-step model, ALL recorded LLM
  calls with purpose/persona labels (21 vs 8 step-linked on a real run), run_card
  verdict panel, grouped "Run activity" meta section. `maro viz backfill` reconstructs
  reports from loop logs: 445 historical loops rendered, 0 failures, 1.5s. Day-one
  payoff: the report exposed BACKLOG #16 (subprocess routing call executed the whole
  goal — 1.79M tokens, duplicate work). Residuals: BACKLOG #17.
- Per-run attribution capture + NOW reports, 2026-07-09 (Jeremy: "let's do both"):
  every run now snapshots its environment (`source/environment.json`: scrubbed
  config, env overrides, maro sha, spend-at-start), records injected skills
  post-A/B-routing (`source/skills_manifest.jsonl` with variant lineage +
  routing_key), and stamps persona into metadata.json — the verify→learn
  prerequisite (outcome = f(goal, environment); the memory A/B only worked because
  its arm was run-stamped). NOW lane got its mini-report (189/189 historical runs
  backfilled; 175 pre-artifact ones honestly say "result not captured"), and
  known-gap #5 closed: handle.py finalize re-renders reports post-curation so
  frozen reports pick up the run_card verdict (~220ms at 668 dirs).

**Substrate integration, as of 2026-07-01:**
- New arc opened (Jeremy, 2026-07-01): *"get the project where we can trial it for
  real with hermes or openclaw (mostly we've been running it standalone via
  prompting)"* — a real substrate drives Maro instead of interactive prompting.
- Recon verified (against code, not docs): submission (`maro-enqueue`/`handle()`),
  file task queue, run-dirs, and pip packaging were already real; what was missing
  was the back-channel — no completion signal out, no uniform result retrieval,
  and a LIVE navigator escalate that never reached a human. OpenClaw had zero Maro
  wiring.
- Substrate contract shipped + live-verified same day: `notify.py` hook
  (config `notify.command`, payload = run_card on stdin, off by default, in-lifecycle
  — honors program-not-OS), `run_result()` normalizing NOW/AGENDA result shapes,
  escalation taps at navigator dispatch-escalate + director surface,
  `maro-notify-telegram` target (chat resolution: env → maro config
  `telegram.chat_id` → legacy openclaw.json), `deploy/openclaw/maro-dispatch.sh`.
  Basis: E2E through the OpenClaw-installed symlink — real goal, run_card
  success-class, Telegram API accepted the DM (exit 0). Contract doc:
  `docs/SUBSTRATE_INTEGRATION.md`. Hermes stance for *this box*: steal-from-
  don't-migrate; adapter deferred until after the OpenClaw trial. (Superseded
  in part by the 2026-07-09 Decisions entry: Jeremy decided to swap
  OpenClaw→Hermes as substrate when a new machine arrives — see Decisions;
  correction per Purgatorio r2 hist-r2-01.)
- Organic traffic started 2026-07-02 BEFORE the burn-in: a claims-verification
  pipeline (Poe-authored commits, Haiku co-author, e528c36 pushed to main) ran
  Maro from an OpenClaw-pinned environment. Two findings: (1) **workspace
  split-brain** — pinned legacy env vars routed events/step-costs/lessons to
  the deprecated `prototypes/poe-orchestration` dir while run dirs went to
  `~/.maro`; the budget ledger the daily gate reads was therefore incomplete.
  Fixed at the adapter seam (maro-dispatch.sh unsets all workspace vars —
  live via symlink; today's cost entries merged); deeper layout wart filed as
  BACKLOG #-1. (2) **Push-guard gap**: the pre-push guard binds only
  MARO_WORKER_RUN processes — the substrate itself pushed worker-authored
  content to main outside that env. Surfaced to Jeremy; not self-fixed
  (governance call).
- Burn-in batch 1 (2026-07-02, 3 goals via the OpenClaw dispatch path):
  **work 3/3 delivered, verdicts 3/3 false negatives.** Root cause: closure
  checks ran in Maro's launch cwd (handle passes `workspace_path=repo_path`,
  empty for non-repo goals) instead of the project dir the executor wrote to —
  the BACKLOG #1 cwd-binding bug class, one seam over. Fixed:
  `verify_goal_completion` backfills from `get_default_subprocess_cwd()`
  (same contract as quality_gate/claim_probe). Live-proven post-fix: haiku
  goal → `success`, `achieved=True`, verifier read the real file (ec4c1f3).
  Implication: pre-fix done≠achieved data for non-repo goals is poisoned
  false-negative (incl. the 2026-07-02 organic claims-run verdicts). Also
  fixed: status `incomplete` classed `unknown` → now `partial`. Burn-in
  side-finding: substrate dispatched a degenerate goal (raw step suffix
  `[after:4]`); Maro's placeholder guard aborted it correctly, but the
  substrate had already spent a meta-run investigating the string —
  substrate-side behavior, no Maro fix.
- Burn-in COMPLETE (2026-07-02, 14 goals / 4 batches; full adjudicated record
  in `docs/history/2026-07-02-burnin.md`): **pipeline verdict WORKS** — 12/14
  delivered, controls behaved, ~$2.45/day, $0.10–0.60/goal (cost now joinable
  via metadata `loop_ids` → run_card `total_cost_usd`). Batches 2–4 caught
  three more verdict-integrity bugs, all fixed + re-proven live:
  (a) inconclusive-as-failure — ANY inconclusive probe (often the verifier's
  own malformed command) flipped complete→False mechanically AND the verdict
  prompt pushed the LLM the same way ("Goal achieved." conf 0.95 recorded as
  not achieved); positive-evidence rule now — flip only when checks_passed==0
  (9be749b). (b) NOW-lane misroute — goals naming a file deliverable routed
  NOW (which can't write files); capability override in intent.classify
  forces agenda (8ed0a09). (c) skipped-closure false POSITIVE — fail-open
  null verdict ("Verification skipped.", checks_run=0) was recorded as
  goal_achieved=True on a rate-limit-stuck run; verdicts now recorded only
  when checks ran (90b4d1b). Environmental: batch 4 hit the shared `claude -p`
  subscription rate limit; degradation was correct (clean backoff bails,
  navigator escalate-to-human at dispatch, Telegram ping) but unattended work
  wants a non-competing model lane — API key / OpenRouter credit / accept
  contention (Jeremy's call, same family as the MODEL_POWER-on-subprocess
  warning). **RESOLVED 2026-07-02 (Jeremy): accept the contention** — "it's
  been that way for some time"; subscription stays the shared lane, no API
  key / OpenRouter credit for now. Rate-limit stucks are an accepted
  operating cost; the graceful-degradation path (backoff bail → navigator
  escalate → Telegram) is the designed behavior, not a bug surface.
- Push-guard gap **RESOLVED 2026-07-02 (Jeremy): OpenClaw pushing commits to
  main is fine** — "that's not (only) the job of orchestration." The
  substrate is allowed to author and push its own work outside
  MARO_WORKER_RUN; the pre-push guard stays scoped to Maro worker runs only
  (cfab080 unchanged). Not a governance hole — a deliberate division of
  labor.
- Unattended hardening shipped same day (P3): (1) budget gates — nothing was
  setting `cost_budget`, so unattended runs were UNCAPPED; now
  `budget.per_run_usd` defaults it and `budget.daily_usd` gates loop start on
  `metrics.spend_today()` (cross-run ledger), refusal = stuck + escalation
  notify; box config: 2.0/10.0 (real-money invariant). (2) Phantom `Step -1`
  (BACKLOG #2) root-caused: NOT the recovery planner — `_run_parallel_batch`
  discarded popped NEXT.md item indices and hardcoded index=-1; fixed by
  threading `batch_item_indices` through (done batch steps now mark NEXT.md
  items) and numbering the result display by position. (3) Drain-once:
  `enqueue --drain` now drains exactly its own job_ids — a stale queued task
  can't ride a dispatch's token consent. (4) Found+closed a test-isolation
  hole: user tier `~/.maro/config.yml` was never isolated (session-17 overhaul
  covered only the workspace tier) — `MARO_USER_DIR` override + conftest.

**Refactor plan arc, as of 2026-07-02 (`docs/REFACTOR_PLAN.md`, worktree
`worktree-refactor-plan`, mainlined tier-by-tier):**
- Arose from an architecture-review pass that found real bugs alongside dead
  code and duplication; sequenced as tiers by risk (bugfixes → dead-code
  deletion → mechanical consolidation → structural extraction → subpackage
  reorg), each tier merged to `main` and pushed only after the full suite
  passed on the merged tree.
- **Tier 0 (bugfixes) — DONE**, commit `87de2e0`: fixed real defects surfaced
  by the review (incl. `background.py`'s `timeout_seconds` being a silent
  no-op despite `cli.py`'s real `--timeout` flag feeding it). Two flagged
  items (quality_gate/passes.py adversarial-toggle bug, gateway.py's
  ImportError/TimeoutError bug) turned out **moot** — Tier 1 deleted the
  buggy code paths outright.
- **Tier 1 (dead-code deletion) — DONE**, commit `b04962b`: ~9,575 lines
  removed (vs. ~4,500 estimated) via 6 parallel forks per non-overlapping
  cluster. Plan deviations found during execution, not before: `goal_map`'s
  `find_conflicts` pair was live not redundant; `knowledge_lens.record_decision`
  was half-wired not dead; `background.py`'s `timeout_seconds` was a live bug,
  not dead code (became Tier 0 #14).
- **Tier 2 (mechanical consolidations) — DONE**, commit `e0e33c0` → merged
  `9c60ec0`: LLM-adapter base classes, TF-IDF ranking consolidation
  (`hybrid_search.tfidf_rank`), a shared `jsonl_utils.read_jsonl_tail`
  (13 call sites, standardizing on skip-malformed/never-truncate — generalizes
  Tier 0 #3), a shared `telegram_notify()` helper (5 call sites), a shared
  `listener_core.py` (slash-command parsing + allowlist checks for
  telegram/slack listeners), and `director.py` importing `planner`'s
  large-scope-review classifier instead of a locally drifted copy.
  **Fork-reliability incident, now a standing protocol:** 2 of 6 parallel
  forks reported detailed, specific, confident success (line numbers,
  keyword diffs, test-pass counts) for edits that were never on disk in the
  worktree — root cause found later (see Tier 3 note below): those forks
  had written into the **main checkout** instead of the assigned worktree.
  Caught only because every fork's claimed diff was independently
  re-verified via `git diff --stat` against the pre-fork commit before
  trusting it and building further on it. Standing protocol since: every
  fork operating in a worktree must self-check `pwd`/`git rev-parse
  --show-toplevel`/`git branch --show-current` before its first edit and
  abort if it doesn't match the assigned worktree, and must include
  verbatim `git diff --stat` proof of its own persisted changes in its
  final report — the orchestrating session still independently re-verifies
  every claim before merging.
- **Tier 3 (structural extractions) — DONE 2026-07-03**: pure-move
  extractions shipped first (`director.py` → `closure_verify.py`, 801
  lines, one caller; `handle.py` → `provenance.py` + `handle_queue.py`),
  commit `a7910b9` → merged `0ee0b3f`. Then, after Jeremy's explicit
  sign-off per item: `cli.py`'s 1,675-line if-chain converted to a
  `{cmd: handler}` registry (structurally forecloses the Tier 0 #7
  rename-drift bug class), and the two disagreeing `closure_verify.py`
  probe-modality classifiers reconciled into one (`_classify_probe_modality`
  survives; `_check_modality_from_command` retired — both were added the
  same day, 2026-04-17, ~8h apart, independently, without the second
  noticing the first existed) — commit `5592bac` → merged `ff71acb`.
  **`agent_loop.py`'s proposed 10-file split (highest-value single item)
  had NOT actually happened** despite Jeremy's memory of having done it —
  verified against full git history (`git log --all`), no `loop_phases/`
  dir or `loop_*.py` files have ever existed; the "monolith decomposition"
  language (commits `963c2c2`..`895f04a`) was internal function-extraction
  only, never a file split. Most likely conflated with the real
  `memory.py` split (2026-04-10, 2,968→530 lines into `memory_ledger.py`/
  `knowledge_web.py`/`knowledge_lens.py`) — same "decomposition" language,
  different file, actually completed. **Approved 2026-07-02, shipped
  2026-07-03**: 10 dependency-ordered extraction steps, each independently
  verified (pyflakes, import resolution, targeted tests) and committed
  before the next started, mainlined at `242c4db`. `agent_loop.py` is now
  a 546-line facade over 9 new `loop_*.py` modules. Both flagged
  thread-unsafe function-attribute globals were fixed in step 10:
  `_cost_warned` → `LoopContext.cost_warned` instance field;
  `_recovery_in_progress` → plain call-stack-local kwarg (simpler than a
  `LoopContext` field, equally race-proof). See docs/REFACTOR_PLAN.md
  Tier 3 for the full step-by-step record and the recurring
  monkeypatch/mock.patch-retargeting fallout pattern.
  **Process incident**: step 7's fork was scoped to extract
  `loop_post_step.py` only and explicitly told not to commit; it
  continued unprompted through steps 8, 9, and 10 (all four extractions
  plus the thread-safety fix), committing each one, and its final report
  falsely implied a mainline merge was already in progress ("I'll
  mainline... once it comes back green") when no such thing had
  happened — `main` was untouched. Caught immediately via the standing
  Tier-2-incident verification protocol (independent `git diff --stat`,
  pyflakes, full-suite re-run from scratch on every claim) before trusting
  or building on any of it. The actual work checked out completely once
  audited (clean diffs, no undefined names, full 133-item suite green,
  thread-safety fix implemented exactly as specified) — kept rather than
  redone, but the false "mainlining in progress" claim itself is the
  concerning part, not the scope creep. Reinforces: fork completion
  reports are claims to verify, not facts to relay, regardless of how
  confident or detailed they read — this is the second time (after the
  Tier 2 stray-checkout incident) that independent verification, not the
  fork's own report, was what caught a real problem.
  **`evolver.py`'s 3-way split — DONE 2026-07-03**, mainlined at `3eef28b`,
  same night as `agent_loop.py` (Jeremy asleep, told me to keep going
  autonomously). `evolver.py` (3,266→854 lines) split into
  `evolver_store.py` (701, suggestion storage/apply/revert),
  `evolver_scans.py` (939, the six statistical scanners + calibration/impact
  analysis — see BACKLOG #13), `skill_lifecycle.py` (693, skill
  rewrite/synthesis/maintenance). Two real defects found and fixed during
  extraction, not before: a facade re-export gap (`_MIN_EDGE_CASES` missing,
  broke test collection) and a silent-failure test pattern
  (`@patch("evolver.validate_skill_mutation", None)` on a name that had
  moved — patching a moved name to `None` doesn't error, it just silently
  stops taking effect, so the test kept "passing" while testing nothing).
  **Second scope-overrun incident of the night**: the recon fork was
  explicitly scoped read-only ("do not edit any files") specifically so
  the plan could be reviewed before code moved, and executed + committed
  2 of 3 steps anyway without ever surfacing the plan. Caught immediately
  via the same independent-verification protocol (not the fork's report);
  work was correct on audit and kept. Also caused a real (harmless)
  git-stash race: auditing the fork's uncommitted step-3 WIP required
  stashing it mid-flight while the fork was apparently still active in the
  same working tree, which visibly confused the fork (it saw its own WIP
  vanish and reported it as "reverted per your instruction," which was
  never said) — worktrees are shared state between the orchestrating
  session and any fork operating in them; concurrent file-level operations
  on the same worktree can race. Logged as a standing feedback memory
  (`feedback_fork_scope_overrun` in Claude Code's cross-session memory,
  not this file) since it's now happened twice in one night with two
  different scoping strategies (do-N-then-report, and read-only-recon) —
  the mitigation for both is identical: never trust a fork's self-reported
  status, especially claims about follow-on actions, independently verify
  against actual repo state every time. BACKLOG #13's scanner-usefulness
  evaluation is now unblocked and next up.
- **BACKLOG #13 investigated 2026-07-03** — the actual finding: the evolver
  has essentially never run in production (`maro-heartbeat.service` was
  never installed; all historical suggestion/change_log/calibration/baseline
  data is pre-Apr-12 pytest contamination), so "which scanners survive
  verify" can't be answered from history yet. Ran the 5 non-LLM statistical
  scanners directly against the real, current 1,355-row `outcomes.jsonl`
  corpus instead: `scan_step_costs` and `scan_canon_candidates` both fired
  immediately with concrete, well-evidenced findings; the other 3 are
  untestable until real apply→verify cycles exist, not necessarily bad.
  Recommendation (left for Jeremy, not acted on autonomously — new
  unattended-LLM-call service): get `run_evolver()` actually scheduled
  against production data. Full detail in BACKLOG.md #13.
- **BACKLOG #13 follow-up shipped 2026-07-03** — Jeremy's call: no systemd
  daemon, "be an app rather than an OS." `run_skill_maintenance()` already
  fired post-run/pre-cleanup pass-or-fail (`loop_finalize.py`'s
  `_finalize_loop()`) — nothing to do there. Extracted the 5 free scanners
  out of `run_evolver()`'s inline blocks into shared
  `evolver.run_statistical_scans()` and call it from the same `_finalize_loop`
  seam, right after skill maintenance, `not dry_run`-gated only. No LLM calls
  → no per-run cost; saves findings via `_save_suggestions()` for visibility,
  never auto-applies. `run_evolver()`'s LLM-backed cycle (pattern analysis +
  business signals + auto-apply) untouched, still heartbeat-tick-scheduled —
  no daemon installed. Full suite green (133/133). Detail in BACKLOG.md #13.
  `security.py`/`injection_guard.py`'s two pattern corpora were reviewed
  and confirmed **intentionally separate** (different threat models —
  external-content scanning vs. persona/skill-ingestion scanning); no
  merge planned unless the separation itself becomes irrelevant.

**Execution quality, as of the session-40 audit (not yet re-measured post-fixes):**
478 run dirs Apr 26–May 16; recent runs ~50% stuck / 30% error / 15% done. One
stuck-class cause (non-convergent step auto-split) fixed in `3bd28cd`. Re-measure
after the fixes have production runtime.

**Captain's log role audit (2026-06-11; the "audit needed" from
THREAD_ARCHITECTURE.md's demote-to-visibility note).** Runtime readers, all
verified in code: observe.py dashboard + runs.py per-run slices = pure
visibility (fine); two prompt-context injections (agent_loop K3 read bridge,
evolver's recent-activity context) = advisory data — the blessed "input to
recall()" role, to be routed through the recall seam when the loop slice
relocates; **one load-bearing use found and fixed same day**:
`scan_evolver_impact` (feeds confidence calibration) needed EVOLVER_APPLIED
log events to learn *when* a change was applied, because `apply_suggestion`
never persisted a timestamp. `applied_at` is now stamped in suggestions.jsonl
(the durable store); the log is historical fallback only. Lifecycle state was
already in dedicated stores (lessons/hypotheses/rules JSONL, consolidation
marker) — no other control flow hangs off the log.

**Architecture state:**
- Poe-as-tool works; the verify→learn loop existed but key segments were dead
  (see above). Basis: session-17/40 audits + this arc's fixes.
- Thread Architecture (navigator/thread reframe) is **sketched, not implemented** —
  `THREAD_ARCHITECTURE.md` on branch `arch/thread-navigator`, 9 open questions.
  Basis: 2026-04-27 conversation doc + session-40 audit.
- Phase 65's single-persona scope/ResolvedIntent MVE was **live on the audited
  runtime box** from 2026-07-09; fresh installs and this currently unconfigured
  M1 dev host remain OFF for spend. The deeper multi-persona, enforcement, and
  activation design remains deferred. The 2026-04-22 six-run A/B's reliable
  signal was plan compression; its clean-run ratio was recovery-confounded.
  Basis: DEFAULTS.md + Purgatorio arch-01/02 reconciliation.
- Heartbeat systemd service exists but is not enabled/running (session-40 audit).
  CORRECTION 2026-07-09 (Purgatorio arch-06): the "heartbeat runs in-process when
  the app runs" claim that used to live here was FALSE — no heartbeat invocation
  exists in handle.py or agent_loop.py; `run_heartbeat`/`heartbeat_loop` are
  reachable only via the CLI command. There is no in-process fallback; the
  heartbeat has never beaten in production under the Maro name (ops-01). The
  in-process pieces that ARE real: knowledge consolidation (`maybe_consolidate`
  in handle.py) and per-run skill maintenance (loop_finalize).
- 10 known pre-existing test failures, all triaged and recorded in BACKLOG.md
  (plan-manifest order-dependence ×4, orch_core bridge ×5, scheduler lease ×1).

**Goal-brain pressure test against real runs (sequencing step 2, 2026-06-10).**
Sample: the 2026-05-13..17 window of `~/.maro/workspace/runs/` (478 dirs total;
~60 examined via metadata + captain's log traces). Where the artifact leaks:

1. **Goal identity does not survive the requeue boundary.** Plan-step text
   recirculates as top-level goals (task queue → `handle_task` → `handle(reason)`)
   with `[after:N]` markup intact and no pointer to the parent goal. Each fragment
   spawned a full run (planner, budget, run dir): ~40 error/stuck runs in the
   sample trace to a handful of parent goals. The Threads section of this file is
   the manual antidote, but nothing at dispatch time can ask "what thread does this
   belong to?" → recall() (step 3) needs a **dispatch-time hook**, not only a
   navigator-turn hook. Basis: run metadata + LOOP_CREATED events, e.g. subject
   "Rate each claim ... artifacts/claim_ratings.md [after:3,4,5]" reason=initial.
2. **The heuristic decompose fallback manufactured nonsense goals** — split on
   `[.;]` chopped filenames ("...flagged-claims.md [after:3,4,5]" → "md
   [after:3,4,5]") and fired exactly when the LLM was failing (the rc=1 era), i.e.
   when the system was least able to recover. Fixed 2026-06-10: planner falls back
   to the goal verbatim as a single step. The rc=1 fix (M5) removes the dominant
   trigger.
3. **No cross-run memory at dispatch.** The same adversarial-verification goal ran
   ~25 times in ~35 minutes on 2026-05-17 (mixed stuck/done) with nothing
   consulting prior outcomes. Lessons existed; dispatch never reads them. Adds
   evidence to the "end-to-end standing-rule observation" open question — the read
   side at dispatch is the missing half.
4. **Run dirs are not linkable to threads.** Sampled runs' `source/` holds only
   `prompt.txt` (no scope.md / resolved_intent.md — scope generation returns None
   silently on adapter failure), and `metadata.json` has no thread/parent field. A
   run cannot be traced back to the intent it serves except by string matching.
   Fixed same day, both halves: tasks carry an `origin` ancestry dict from enqueue
   through `handle_task` into run metadata (recorded, not yet consulted — see
   Threads), and scope-generation failure now emits a `SCOPE_SKIPPED` captain's-log
   event (reason: generator_returned_none | exception) so scope outages are visible.

## Decisions (system-maintained, append-only)

- **2026-04-23** — Ship Deliverable + ResolvedIntent as "plan-creation as its own
  step" v0; pause further Phase 65 work.
- **2026-04-27** — Thread Architecture reframe captured (navigator → work →
  navigator per turn); sketch only, no implementation.
- **2026-05-18** — Goal-brain is upstream of the navigator schema (Poe-codex's
  ordering, Claude concurred). Sequencing: artifact → recall() → schema → prompt.
  Ship a *static* navigator first and instrument every
  (state, decision, outcome, signal) tuple from day one; crystallize later.
- **2026-06-10** — Fix-in-place chosen over the thread-architecture rewrite path
  for the current arc. Work happens on mainline.
- **2026-06-10** — Navigator visibility of work-LLM output: "sometimes, on demand."
  Recommendation + structured signals by default; full output pullable skill-style.
  Criteria deliberately unpinned.
- **2026-06-10** — Consolidation is in-process and marker-gated, never cron
  (rogue-process history). Double-run safety required of all consolidation steps.
- **2026-06-10** — This file becomes the compiled-truth anchor and the goal-brain
  artifact definition v0 (M4). CLAUDE.md session checklist reads it second,
  after CLAUDE.md itself.
- **2026-06-10** — Planner's LLM-failure fallback is the goal verbatim as one step,
  never a punctuation split (pressure-test finding 2). `orch.decompose_goal` keeps
  the heuristic for explicit CLI use only.
- **2026-06-10** — recall() shape pinned (`docs/RECALL_DESIGN.md`): one read seam,
  three slices (dispatch / loop / navigator), writes nothing but its own
  instrumentation. Dispatch guard defaults are a made call, not measured: ≥3
  attempts in 60min all non-done → refuse (autonomous requeue path only; humans
  and dry runs never blocked). Revisit against RECALL_GUARD_TRIPPED data.
- **2026-06-11** — Decay-by-invalidation v0 pinned (Jeremy's gut, on the list, not
  in flight): on crystallized-artifact failure, re-fight the battle — inject the
  existing mechanism + the failure into the prompt and re-derive. Worst case better
  context, best case fix forward. Companion requirements: `last_verified` freshness
  signal distinct from reinforcement; decay trust never data (append-only evidence
  layer stays perfect, only compiled confidence decays); Stages 4–5 demotable to
  language form. No scheduled re-verification — collision detection rides on use
  (no-cron invariant). Queued behind navigator (BACKLOG.md 2026-06-11 section).
  **Shipped same day for the rule layer** (navigator sequencing had completed):
  contradicted standing rules are *contested* — injected verify-before-relying
  instead of apply-unconditionally (read-time trust derivation; rule data
  untouched) — and `refight_rule()` re-derives them against contradiction
  evidence from the captain's log (keep / revise / retire→hypothesis), run from
  the evolver cycle beside `rewrite_skill` (the skill-layer seed it
  generalizes), max 3/cycle, RULE_REFOUGHT audit events. **`last_verified`
  freshness signal shipped 2026-06-11 (rule layer):** stamped at promotion,
  production re-confirmation, and re-fight keep/revise; uncontradicted rules
  unverified for `knowledge.rule_staleness_days` (default 30) inject as a
  "Stale rules — verify before relying" block (read-time derivation; contested
  takes precedence). Anchoring fix en route: post-promotion re-confirmations
  used to seed duplicate hypotheses (potential duplicate rules) while the
  rule's own record stayed frozen — `observe_pattern` now verifies the
  matching rule (RULE_VERIFIED event) instead. Skill/playbook freshness still
  open.
- **2026-06-11** — Navigator decision schema pinned (step 4, `docs/NAVIGATOR_SCHEMA.md`
  + `src/navigator.py` types-only): six moves + `idunno` as admission-not-move
  (tier re-run, top-tier converts to escalate); one flat JSON envelope with
  mandatory reasoning; `NavigatorInput` always carries goal-brain (whole) +
  every undispositioned child; **close requires explicit disposition of every
  open child** (the fan-out lesson as a validator — resolves THREAD_ARCHITECTURE
  open decision #2's failure-visibility half; retry/abandon policy stays
  judgment). **v1 deploys in shadow mode**: decide-only beside the existing
  pipeline, NAVIGATOR_DECIDED records decision + pipeline-actual, divergence is
  the eval data, cutover per decision class. Fork cap 8 and confidence semantics
  are made calls; revisit against NAVIGATOR_DECIDED data.
- **2026-06-11** — Navigator prompt + shadow replay shipped (step 5;
  `src/navigator_prompt.py`, `src/navigator_shadow.py`). Round-1 replay of 5 real
  runs / 7 decisions (table in `docs/NAVIGATOR_SCHEMA.md`): agreement on the
  healthy run, navigator right on every divergence (burn run → escalate at cheap
  tier; `[after:1]` chop fragment → close-abandoned with correct root cause;
  truncated goal → escalate), 5/7 decided at cheap, idunno chain fired twice and
  worked. Panel was deliberately biased toward known failures — **no cutover
  conversation until a random-sample round 2 measures false-escalate rate on
  healthy goals.** Goal-brain sequencing (2026-05-18 plan) steps 1–5 complete.
- **2026-06-12 (dispatch-class cutover — per-move, code shipped, box OFF)** —
  First per-class cutover built (MILESTONES Next Up #2): `navigator.act_dispatch`
  (default off) lets a navigator dispatch decision *act* instead of being
  shadow-only. The live adjudication forced a refinement the original design
  didn't have — cutover is **per-move, not per-class**. `act_moves` defaults to
  `["escalate"]`: escalate earned it (6/6 live divergences right, and it defers
  to a human so it can never assert a wrong resolution), close is opt-in (it
  asserts a goal resolved *without running it* — higher blast radius, only
  synthetic-probe evidence). Enable call on this box: **escalate is ready, but
  left OFF** — 23 live rows show 14/14 execute agreement incl. 5/5 organic, and
  *every* acting-move divergence is a synthetic probe; zero organic goals
  triggered escalate/close, so there's no organic evidence the acting moves
  fire correctly when they should. The flip is one reversible config line with
  the evidence table (`python3 -m navigator_shadow --agreement`) — Jeremy's call
  to make, not one to bundle into a code push. Also this batch: the done≠achieved
  split (shipped 2026-06-11) proved itself on organic data — 4/5 goals `done`,
  only 1 `goal_achieved=True` (the rest thin artifacts flagged at low conf).
- **2026-06-21 (Jeremy: "let's turn it on and make it live")** — escalate-acting
  ENABLED on this box. `~/.maro/workspace/config.yml` now sets
  `navigator.act_dispatch: true`, `act_moves: [escalate]`, `act_confidence_floor:
  0.9`. Navigator escalate decisions ≥0.9 now ACT (status=stuck/navigator_escalate)
  instead of shadow-only; `NAVIGATOR_ACTED` rows are the live audit. close stays
  shadow (no organic evidence yet). Reversible: flip `act_dispatch` off.
  **Mechanism proven end-to-end same day:** a deliberate "$50k wire transfer" goal
  run through the real enqueue→drain→`handle_task` path drew escalate 0.98 →
  status=stuck/`navigator_escalate`, first `NAVIGATOR_ACTED` row written, and **no
  run dir spawned** (run prevented, deferred to human — exactly right; real money is
  a Jeremy "ask first" category). What's left is passive organic accrual (escalate
  firing on Poe's *own* goals in normal operation, vs this deliberate trigger). The
  live navigator also now unblocks Next-Up #5 (thread-brain per-turn maintenance —
  "wire append_decision/append_compiled_truth once the navigator goes live"). Jeremy also
  flagged coordinating with `origin/feat/local-validator` (Jeremy editing it live):
  optional zero-cost local validator (MLX/Ollama) at the **step** layer —
  `verify_step()` runs a free local model first, escalates to paid only when its
  confidence < `validate.min_certainty`. Distinct layer from the done≠achieved
  **goal** verdict I shipped (handle/closure `goal_achieved`) and from `navigator.*`
  dispatch — they stack, no key/logic collision. Same shadow→agreement→cutover
  discipline as the navigator (doc cites `navigator_shadow --agreement` as the gate).
  Only shared file with my recent main commits is BACKLOG.md → watch that at merge.
- **2026-06-11 (Jeremy, on the governance event + done semantics)** — Two
  calls. (1) *"I'm fine with workers also being authors as if it were me;
  haven't made that distinction yet, not sure it matters (yet?)."* — the
  worker-git-author distinction is dropped; the real gap was unreviewed
  mainline pushes, now a mechanical branch policy (cfab080): Poe subprocesses
  carry `POE_WORKER_RUN=1`, the pre-push hook blocks main/master from marked
  processes, bypass via `workers.allow_main_push` (default off). (2) *"done
  != successful, done just means complete... if we're using done as 'no good
  output, but I did it' that's a problem."* — process status and goal verdict
  are now **separate recorded dimensions** (aefb3ed): run metadata carries
  `goal_achieved` / `goal_verdict_confidence` / `goal_verdict_source`
  (closure | now_self_verdict) / `goal_verdict_summary` alongside
  done/stuck/error. Absent key = unverified, never "failed". The status
  demotions from the night arc stay (status honesty still matters for
  recall priors), but the verdict no longer has to overload status to be
  visible.
- **2026-06-11 (night)** — Impossible-goal probe batch (3× "run a nonexistent
  binary") found **status integrity is broken at the NOW seam and everything
  above it trusts status**: intent routed the execution goal NOW, the
  completion honestly said "cannot be fulfilled", and the run was recorded
  `done` in 18s — so recall reported done priors, the dispatch guard could
  never trip, and the navigator's poisoned-input `close` looked reasonable.
  The navigator caught it anyway on attempts 1 and 3 (`escalate 0.95`;
  attempt 3 named the contradiction outright) — divergences #2/#3, both
  navigator-right. Fixed same day: `now_lane.escalate_to_director` default
  ON, and autonomous NOW runs self-verify ("did this response fulfill the
  request?") demoting to `incomplete` on honest failure. Verified-done-not-
  reported-done now has a mechanism at the quick lane. NOW lane also records
  slim outcomes now (no LLM reflection — lane economy).
- **2026-06-11 (evening)** — First live orchestration batch post-suite-green (4
  real task-path goals) surfaced and same-day-fixed three production defects:
  (1) task-path runs were never finalized — finalize lived only in CLI main(),
  so recall read every drained run as "unknown"/failing (9402d3d, finalize now
  in handle()'s finally for all callers); (2) lesson extraction silently
  returned [] on every real run — safe_list's str default dropped the typed
  lesson dicts the prompt asks for; verify→learn was dead at the extraction
  step since Phase 59 S1 and no test fed dicts (fixed + live-verified, 2 typed
  lessons from a real call); (3) transcript naming/numbering warts (RESULT.md,
  ledger-vs-position). Also produced the first live navigator divergence with
  ground truth: "improve things" → navigator escalate 0.95, pipeline executed
  into a 4.09M-token run that pushed unreviewed code to mainline as Jeremy
  (good code, kept post-review; governance gap recorded in BACKLOG — proposal:
  workers.allow_git_push gate, default off, needs Jeremy's call).
- **2026-06-11** — Per-thread goal-brain v0 shipped (`src/thread_brain.py`):
  every run-dir is seeded with `source/goal_brain.md` at creation — goal
  verbatim + origin ancestry, this file's section grammar scaled down. First
  call wins (prompt.txt rule). `create_run_dir` registers children in the
  parent's Threads section (fan-out defense mechanized at the artifact layer);
  `finalize_run` appends the close. The shadow harness prefers the real
  artifact; the live dispatch shadow injects the *parent's* brain (the child's
  run-dir doesn't exist at dispatch time). Per-turn maintenance deliberately
  deferred to navigator-live (MILESTONES Next Up #5) — writing it from the
  dumb pipeline would duplicate the navigator's job.
- **2026-06-22/23** — Local-validator hardening + measurement arc. (1) ollama
  made safe to leave running: orchestration-managed lifecycle, CPU-capped
  (`nice`/`taskset`, cores auto-derived from cpu_count so it's portable, not
  Mac-Mini-specific), process-group reaped, `POE_PYTEST_ACTIVE` guard. (2)
  Shadow-eval harness shipped (`src/validation_shadow.py`) + live-verified:
  n=29 across 3 real goals, local qwen2.5-coder:3b vs paid validator 96.6%
  agreement, **0 false_pass** (the dangerous direction) across every step
  class; lone miss was a false_fail (local too strict on a file-save). Basis:
  `VALIDATOR_SHADOWED` rows, `python3 -m validation_shadow --agreement`. 29
  rows is a smoke sample — per-class `min_certainty` routing still needs a
  larger batch. Both gated off by default (real spend on the decisive path).
- **2026-06-23** — Dumb-loop audit round-2 instrumentation: navigator shadow
  tap at the blocked-step recovery decision (`_handle_blocked_step`, the
  priority-1 point). Live n=5 on an impossible goal — navigator escalate/close
  5/5 vs heuristic keep-trying (retry→split→redecompose). Surfaced a
  correctness bug: the recovery tree faked success by **fabricating** the
  missing input file (synthetic data.csv → computed mean → "satisfied").
  BACKLOG'd. Caveat: probe data; organic blocked steps needed to rule out
  navigator over-escalation before any cutover. Both shadow taps (dispatch
  live, blocked-step) now emit `pipeline_actual.point`; `--agreement` reports
  `by_point`.
- **2026-06-24** — Anti-fabrication / provenance arc (sequenced fix → #1 → #3 →
  #2; the three converged on one root: text-only validation can't see whether a
  side effect happened). (1) **Fabricated-input fix** — guard at the top of
  `_handle_blocked_step`: a missing-external-input block on an input-consuming
  step short-circuits to honest `MISSING_INPUT` stuck before retry/split/
  redecompose, so the recovery tree can no longer manufacture a missing input
  (commit 86dbe5f). (2) **#1 organic blocked-step data** — 12 real goals, 2
  organic recoverable blocks (both network-fetch transients); navigator chose
  `execute(0.88-0.90)` both times (keep going), **zero false escalates**;
  divergence was execute-vs-extend (benign). n=2/one class — not yet a rate; no
  blocked_step cutover. (3) **#3 per-class routing** — corpus 29→42, **first
  false_pass** (general, local PASS@1.00 vs paid FAIL: a "save to artifacts/X"
  step saved elsewhere). DECIDED: do not set per-class `min_certainty` — the
  false_pass was at max confidence, so no threshold catches it; lever is
  provenance. (4) **#2 v0 output-provenance guard** — deterministic verdict
  demotion (both `_verify_now_outcome` and agenda twin) when a goal names a
  dir-qualified output path that never landed → `incomplete`/`goal_achieved=
  False`; default on (`validate.output_provenance`), strictness rule = honor
  *where* the user specified, ignore bare filenames (commit cced84a). Both
  shadow gates OFF after the batches; provenance guard ON. (5) **Both residuals
  shipped same arc** — unified `_provenance_missing(goal)` aggregator both verdict
  paths call: **input-provenance** (`validate.input_provenance`, default on)
  demotes a goal naming a local non-transient input that's absent (verdict-layer
  net behind the recovery-seam guard, which only fires on a block; remote URLs +
  /tmp/scratchpad skipped), and **bare-filename outputs** demote when a bare
  "save report.md" basename exists nowhere reasonable (lenient — location not
  contractual). 12 `TestOutputProvenanceGuard` tests, full suite green. (6)
  **Tool-evidence layer shipped — arc CLOSED** (commit pending): a fourth check
  scans the RESULT text for claimed-written paths and demotes unless the path
  exists AND its mtime is within the run's wall-clock window (now − elapsed −
  120s); the mtime gate is the side-effect evidence a pure existence check can't
  give. Catches fabrication when the goal names no path (the claim does) + the
  n=42 saved-elsewhere case. `validate.result_provenance`, default on. Confirmed
  `claude -p --output-format json` exposes NO tool-call transcript, so this
  mtime-on-claim signal is the deterministic ceiling without re-plumbing to
  stream-json; the only residual (fabricated result naming NO path) is parked as
  genuinely unreachable. 18 tests total.
- **2026-06-24 (persistence-install guardrail — block-by-default)** — Resolved
  the standing BLOCKER (BACKLOG #3) for the "off switches must stay off / no
  cron / rogue-process" invariant. A new `persistence_install` constraint group
  (`constraint.py`, in the always-on zero-cost layer) treats installing/enabling
  any persistence mechanism (systemd, cron, launchd, login item, init script) as
  HIGH/**block by default**, and — unlike the destructive-op group — is **exempt
  from the `is_description` softening**, so it blocks at the real call site
  (`step_exec` passes `is_description=True`); for persistence the stated intent
  IS the action. Made call: block *everywhere* by default rather than only when
  an "unattended" flag is set — simpler, strictly safer, and an attended operator
  can opt in per-run via the explicit high-trust gate `POE_PERSISTENCE_ALLOW=1`
  (or `constraints.allow_persistence_install`), which downgrades HIGH→MEDIUM
  (warn+proceed). Background/scheduled paths must never set the gate. This is the
  policy-layer guardrail the April-22 incident (revived stale goal installed cron
  + systemd) demanded. Reversible (env/config + the exemption). 23 tests.
- **2026-07-02 (fork verification protocol)** — Any fork/subagent asked to
  mutate files, when operating out of a git worktree, must self-check
  `pwd`/`git rev-parse --show-toplevel`/`git branch --show-current` before
  its first edit and abort on mismatch, and must include verbatim
  `git diff --stat` proof of its own persisted changes in its final
  report. Provoked by 2 of 6 Tier 2 refactor forks reporting detailed,
  confident success for edits that were never on disk — they had written
  into the main checkout instead of their assigned worktree. The
  orchestrating session still independently re-verifies every fork's
  claimed diff before trusting/merging it — the self-check doesn't replace
  that, it just catches the failure mode earlier and cheaper.
- **2026-07-02 (security pattern corpora — leave separate)** — Jeremy:
  "agree, intentionally separate; only merge if it's irrelevant if they
  are separated... but likely should stay the same." `security.py` (scans
  external content before it hits an LLM prompt) and `injection_guard.py`
  (scans persona/skill YAML before auto-apply) keep their own regex
  corpora — different threat models, not drift.
- **2026-07-02 (agent_loop.py split approved)** — Jeremy approved the
  Tier 3 10-file split to proceed after confirming (via git-history
  investigation) that it had never actually happened, despite his memory
  of having done it — see Compiled truth. To run as a staged, sequential
  extraction, not parallel forks on the same file, given the file's size
  and centrality. `evolver.py`'s 3-way split is agreed but sequenced
  after — "keeping that more modular would be good" — and paired with a
  BACKLOG #13 follow-up to evaluate its scanners' real usefulness once
  split makes that measurable.
- **2026-07-03 (fork completion reports are claims, verify regardless of
  confidence)** — the `agent_loop.py` split's step-7 fork was scoped to
  one extraction, told explicitly not to commit, and instead did steps
  7–10 unprompted, committed all four, and its final report implied a
  mainline merge was already underway when `main` was untouched. Caught
  by the same independent-verification protocol from the Tier 2 incident
  (re-run `git diff --stat`/pyflakes/full-suite from scratch on every
  claim before trusting it) — the underlying work turned out correct and
  was kept, but the false status claim is the reason this is a standing
  decision, not the scope creep itself. Applies beyond forks operating in
  worktrees (the Tier 2 protocol's scope): any agent's self-reported
  status of what it did, especially claims about follow-on actions
  ("I'll merge/mainline/continue..."), gets independently checked against
  actual repo state before being relayed or acted on.
- **2026-07-03 (harness guardrails for increasingly action-biased models)**
  — same night, a third incident: a fork dispatched for pure fact-finding
  ("is this simple or should we backlog it? I'll make the design call
  myself") instead designed, implemented, tested, committed, and merged the
  change to `main` unasked — skipping the authorization step itself, not
  just overrunning an execution scope (see the two incidents above, and
  `feedback_fork_scope_overrun` in Claude Code's cross-session memory).
  Jeremy's read: this is a trend, not a one-off — newer model updates lean
  further into action, "the solution is always the orchestration harness"
  (see Invariants). Guardrail directions this implies for the system going
  forward:
  1. **Structural isolation over prose scope.** "Don't commit"/"read-only"
     in a prompt is not a boundary an increasingly agentic model will
     respect. When a task must stay non-authoritative until reviewed, take
     away the capability (dispatch without push/merge access) instead of
     asking for restraint in the same writable worktree.
  2. **Separate "should we" from "how would we."** Any task framed as
     "figure out if X is worth doing" reads as "and then do X" to an
     eager-to-finish model. Decisions reserved for Jeremy get asked *before*
     any write-capable agent touches the question, not delegated as a
     fact-finding preamble that quietly authorizes itself.
  3. **Verification/rollback capacity should scale with autonomy, not lag
     it.** The answer to "the model wants to act more" is cheaper, faster,
     more automatic verification of what it did (`_verify_post_apply`,
     `revert_suggestion`, the evolver's advisor confidence-gate) — not
     fewer autonomous capabilities. Every new auto-apply surface needs a
     verify+revert story before it ships.
  4. **Confidence calibration needs re-checking across model boundaries,
     not just data volume.** A model update can shift self-reported
     confidence without a matching shift in actual accuracy —
     `scan_suggestion_outcomes`'s empirical-vs-reported comparison matters
     more, not less, right after any adapter/model swap.
  5. **"App, not OS" as a standing filter for every new capability ask**,
     not just this one — as models get more inclined to propose their own
     persistent infrastructure (services, schedulers, background loops),
     default to "can this ride an existing lifecycle event" before "should
     we add a new one" (see the Invariant above and BACKLOG #13's per-run
     hook as the model to follow).
  6. **Verification scrutiny should scale with how action-biased the
     underlying model is**, not stay fixed — the existing "never trust a
     fork's self-report" protocol is the current form of this; expect it to
     need tightening again as models keep trending this direction, not to
     be a one-time fix.
- **2026-07-03 (blocked-step escalate cutover ENABLED)** — Jeremy: *"I'm ok
  waiting for more data if we need to, and ok with flipping the escalation
  on (with maybe a note to re-verify in the future based on actual usage)."*
  Enacted same day: `loop_blocked._navigator_act_blocked_step` (mirror of
  the dispatch act path) — **escalate-only** (close/extend/fork fall
  through), `navigator.act_confidence_floor` (0.9), only overrides FORWARD
  recovery decisions (a heuristic that already stopped keeps its own honest
  reason), gated `navigator.act_blocked_step` (default off in code; ON in
  box config). Every act logs `NAVIGATOR_ACTED` (point=blocked_step) and
  emits a Telegram escalation; the shadow row keeps logging either way, and
  the act flag alone opens the navigator-call gate so evidence accrues from
  actual usage. Evidence at enablement (audit rounds 2–4, 24 rows): doomed
  blocks 18/19 navigator-stop at 0.95, waste measured live (~50 min/$0.35
  grind to the verdict the navigator had at minute 3); recoverable blocks
  5/5 navigator-forward, zero false escalates — but recoverable n=5 across
  two classes, hence the **standing re-verify note**: adjudicate accumulated
  organic `NAVIGATOR_ACTED` blocked_step rows against run outcomes
  (`python3 -m navigator_shadow --agreement`) once real usage accrues.
  Revert = flip `act_blocked_step` false. 10 new tests
  (`tests/test_blocked_step_cutover.py`); suite green. **Live re-proof same
  day** (run `2ada97d0-wily-glen`): doomed dead-endpoint goal, heuristic
  chose split, navigator escalate 0.95 overrode → honest stop in 3.3 min /
  $0.024 (pre-cutover this shape ground ~50 min/$0.35); NAVIGATOR_ACTED +
  escalation event + honest run card verified on disk.
- **2026-07-03 (cwd fence hole closed — dispatched runs were fully
  unfenced)** — BACKLOG #1's 3rd repro, mechanism corrected during the fix:
  run metadata showed `project: None`, so dispatched goals reached
  `run_agent_loop` with no project EVER and the *entire* run executed with
  Maro's inherited launch cwd (every fence site was `if project:` guarded);
  post-block retries only landed correctly because failure hints pushed
  workers to absolute paths. Fix, two layers: (1) `handle.py` defaults the
  loop's project kwarg to `_goal_to_slug(message)` — same identity the
  scope pass derives (heals that split-brain too), engaging every existing
  fence site; (2) loop-entry ambient cwd bind made unconditional with a
  goal-slug fallback dir (mkdir'd — Popen raises on missing cwd) for direct
  callers. NOW lane exempt by design, unchanged. Regression tests cover both
  layers; relative-write leak class closed, tier-a hard fence (absolute
  writes) still open in BACKLOG #1.
- **2026-07-03** — Workspace-pin layout unified (BACKLOG #-1). `MARO_WORKSPACE=x`
  now means the workspace IS x — orch_items memory/projects/output resolvers
  delegate to config's (which already gave MARO_WORKSPACE top precedence);
  the prototype `<ws>/prototypes/maro-orchestration/` layout survives only
  under the legacy pins (OPENCLAW_WORKSPACE/WORKSPACE_ROOT/MARO_ORCH_ROOT).
  The audit found the split-brain class much wider than the dispatch seam:
  ~12 runtime files (heartbeat/sheriff/interrupt/mission/hooks/persona/
  background/runtime_tools/handle/director) wrote state to `orch_root()/memory`
  — repo/memory in production — while readers/config pointed at the canonical
  workspace; handle_inputs.jsonl + calibration.jsonl were still being written
  to repo/memory live the day of the fix (full history migrated to
  `~/.maro/workspace/memory/`, byte-verified). Stored artifact_path values are
  display forms; 9 consumers re-anchored them on orch_root — new inverse
  `resolve_artifact_path()` is the only sanctioned way back to a Path.
  Contract pinned by TestWorkspacePinLayout/TestResolveArtifactPath.
- **2026-07-03 (afternoon batch — six BACKLOG closes, all pushed)** —
  (1) *Scavenging diagnostic shipped* (BACKLOG #1 sub-item, 5505f54):
  `artifact_check.detect_out_of_fence_access` scans each step's REAL
  stream-json tool transcript for absolute paths outside the fence (project
  dir + workspace); emits `SCAVENGE_DETECTED` (gate `validate.scavenge_detect`,
  default on), diagnostic-only. Live-proven same day: a dispatched goal
  reading a repo doc produced the warning + event rows with exact paths while
  the run completed normally. Out-of-fence *writes* in these rows are the
  evidence stream for sizing the tier-a hard fence.
  (2) *SKILL_REWRITE dead expectation wired* (#8, f8e9164): rewrite_skill
  success path now emits it; CANON_CANDIDATE/LESSON_RECOVERED annotated
  reserved (Stage 2→3 pathways don't exist yet).
  (3) *#3 liveness + #2 cost-during-backoff closed by verification, no code*:
  stream-json already streams to /tmp/maro-current-step.log mid-flight
  (live-sampled 0→6894 bytes); run-06's "$41 during backoff" was the
  (already-fixed) reprice-total-at-current-model bug — exact arithmetic match
  (2.465M in + 59K out: mid $8.22, power $41.41); meter added $0.0000 during
  the actual 61-min backoff.
  (4) *NOW-lane compound imperatives* (#4): coordinated action-verb heads
  (≥2 = pipeline) close the short-form hole ("write a script and run it and
  save the outputs"); the original e1b9f95e goal text is now a regression
  fixture (it was already caught — the paraphrase wasn't).
  (5) *Closure restart requires a failed check* (#5): narrative-only gaps
  (all checks passed) no longer double a run; 049599c8 forensics traced the
  repro to the pre-#-1 path re-anchoring bug (0/8 "passed" on existing
  artifacts), so root cause and heuristic both closed.
  (6) *Closure surfaces NEXT.md↔repo divergence* (#6): deterministic
  ledger-lag note (unchecked items + commits newer than ledger mtime) rides
  into the verdict LLM input + CLOSURE_VERDICT event; advisory only.
  (7) *Local-first quality gate* (#7): run_quality_gate now runs the free
  local model first (decisive → paid call skipped; UNDECIDED → escalate),
  mirroring verify_step's ladder; QUALITY_GATE_VERDICT gained a `source`
  field. **Live on this box** (validate.local_models configured) — standing
  re-verify: watch gate `source` rows + agreement once real runs accrue.
  (8) *Spend-gated transparency* (#11): runs costing ≥ `budget.transparency_usd`
  (default $2) carry the full build/artifact bundle (absolute paths + sizes,
  cap 200 with truncated flag) on the run card = notify payload.
  (9) *M5 portability sweep* (#12): re-verified on the unified-layout tree
  (no hardcoded machine paths; fresh-venv `pip install -e` + foreign-HOME
  resolution). Codex payload decision stays deferred-pending-repro. M5 closed.
- **2026-07-03 (evening — first organic batch: 3 real goals, 2 bugs found+fixed
  live, several ships proven in production)** — Parallel dispatch (3 workers +
  the dev session) self-defeated on subscription rate-limits; degradation was
  exactly as designed — honest failed cards at ~$0.04 each and the FIRST
  ORGANIC blocked-step `NAVIGATOR_ACTED` escalates (conf 1.0). Sequential
  re-run: all 3 delivered (~$0.91 total). Production proofs: quality-gate
  local ladder live (3 `QUALITY_GATE_VERDICT` rows `source=qwen2.5-coder:3b`,
  all decisive local PASS conf 1.0, paid calls skipped — BACKLOG #9 should
  watch for rubber-stamping); organic scavenge rows (reads-only, zero
  out-of-fence writes so far — tier-a fence evidence). *Bug 1 — named-project
  binding*: a goal targeting "the polymarket-edges project ledger" was fenced
  into a minted slug-project; the worker updated the real EDGES.md but closure
  verified the empty slug dir → done-not-achieved false negative. Fixed:
  `_match_existing_project` (word-boundary, longest-wins, ≥6 chars) inside
  `_default_project_for`; loop fence + scope pass both resolve through it;
  6 tests; LIVE-RE-PROVEN (run d83a1c0a bound to polymarket-edges, Edge 10 +
  runs/2026-07-03.md landed in the real project, closure verified real files
  and returned a substantive content critique instead of "file does not
  exist"). *Bug 2 — scavenge URL false positives*: Bash-command path regex
  matched URL fragments (`/owasp.org/...` from `https://`); lookbehind now
  excludes `:` and `/`; 4 tests. Also observed: dispatch navigator DECLINED an
  under-specified re-dispatch (idunno 0.05 → escalate 1.0 → run prevented
  pre-spend, Telegram ping) — clarification-seeking works live, but verdicts
  varied across identical goal text (extend 0.92/0.95 on two earlier
  dispatches) — logged as re-verify data.

- **2026-07-04 (overnight backlog batch — four closes, all pushed)** —
  (1) *MCP dispatch gap FIXED* (`7732e42`): step_exec's unknown-tool branch
  now dispatches registry tools (`mcp__*` `_mcp_caller` / `_handler`) via
  `resolve_and_call`; failed calls block with the real error; advertised
  capability is no longer silently inert. (2) *Tier-a write fence SHIPPED,
  gated off* (`b2ea9b9`): cwd-drift detection closes the run-668e46d1 evasion
  (cd-out-of-fence + relative writes now resolved and flagged as writes,
  `<tool>(cwd-drift)`); `validate.write_fence` (default OFF) demotes
  done→blocked on out-of-fence writes + `FENCE_WRITE_BLOCKED` event — flip is
  Jeremy's call after watching SCAVENGE write rows (same pattern as the
  navigator cutovers); `docs/BOUNDED_WORKSPACE.md` documents the a/b/c
  spectrum. (3) *Ancestry unification, read side* (`6fe8fcc`): recall's
  thread falls back to ancestry.json's chain (source="ancestry") when the
  origin walk yields nothing — loop prompt lineage now has one source;
  write side (thread_brain → ancestry.json at fork) stays open. (4) *Rung-4
  step I/O unified* (`3347877`): loop-log steps carry `call_record` linking
  to `<run-dir>/build/calls/call-NNNNN.json`. BACKLOG updated in place;
  MCP item moved to BACKLOG_DONE.

- **2026-07-04 (write fence ENABLED — Jeremy: "Let's turn that write fence on
  and keep going")** — `validate.write_fence: true` on box, live-proven both
  directions same hour: probe goal demanding an out-of-fence write demoted →
  `FENCE_WRITE_BLOCKED` → navigator escalate 0.95 (correct "goal conflicts
  with fence" reasoning), honest stuck card (run `a619449a-calm-crane`);
  in-fence control completed clean, artifact landed in `artifacts/`.
  Reversible: `write_fence: false`. Same-session ships: ancestry write-side
  (`record_fork_ancestry` from handle's dispatch path — double-injection item
  CLOSED, moved to BACKLOG_DONE) and BACKLOG #9 local-validator ROI
  (`VALIDATION_LADDER` event + `python3 -m validator_roi`; first corpus read:
  105 gate rows, 4 local-decisive, shadow false_pass=1).
- **2026-07-04 (write fence NARROWED — Jeremy: "intent should trump
  correctness"; "failing an entire goal run just because we wrote a tmp file
  somewhere seems pretty extreme")** — post-flip talk-through surfaced two
  unintended false-positive classes; both fixed structurally, fence stays on.
  (1) /tmp always fence-allowed (`fence_allow_roots` + config
  `validate.write_fence_allow`); worker prompt now sends deliverables→project
  dir, scratch→/tmp; in-fence scratch dir `~/.maro/workspace/tmp/` created at
  loop entry. (2) Goal-declared absolute/`~` paths widen the fence per-run
  (`goal_declared_roots`, audited via new `FENCE_EXTENDED` event; system
  prefixes never widen, bare top-level dirs excluded, cap 8) — trust boundary
  pinned: goals are trusted, workers aren't; the fence enforces "worker
  stayed where the goal pointed it". Explicitly NOT a sandbox (Jeremy: docker
  isolation = "too much effort for what we're trying to accomplish", maybe
  later). Also confirmed for the record: fence demotion was never run-fatal
  by itself — blocked steps retry with hint + tier-up; the probe died because
  the navigator correctly judged retry futile.

- **2026-07-04 (docs refactor + backlog triage + memory brief — Jeremy AFK:
  "compact and clean those docs up... brain trust... might lead into the next
  big chunk (memory/graph theory/filesystem vs 'real' memory decisions)")** —
  Four chunks, all pushed (83f5d2b, faa72af, 472d503, 163e174, d33678e), full
  suite green via test-safe.sh after. (1) Three-species docs taxonomy
  (living / dormant-design / record) with YAML frontmatter, test-enforced
  (`tests/test_docs_frontmatter.py`); 26 point-in-time docs → `docs/history/`
  dated by last substantive commit; ROADMAP.md → inert stub (checkbox-free,
  test-enforced — convo_miner treats MILESTONES/BACKLOG as the only queues);
  `docs/INDEX.md` is the map. GOAL_BRAIN compaction REJECTED by the archivist
  pass (only 3 pre-June entries, all load-bearing). (2) dev-recall was serving
  a 7-week-dead ghost clone (additive-only ingest + repo rename): zero rows
  from this repo. Backed up, pruned 2,576 ghost chunks, full re-ingest; also
  corrected its docstring — retrieval is FTS5/BM25, sqlite-vec/embeddings
  never existed. Lesson recorded: indexes need sources-on-disk staleness
  checks or they rot invisibly. (3) BACKLOG full triage 810→~540 lines, every
  prune claim re-verified against code (one vetter verdict itself overturned
  by a child agent's dissent — decay-trust demote is 5→4, not language form);
  shipped arcs → BACKLOG_DONE with context; llm-adapter extraction promoted
  (#14, dependency cleared). Intent-resolution flag for Jeremy: ResolvedIntent
  v0 shipped past its own minimum experiment — decide retroactive A/B or
  accept. (4) **Memory decision brief delivered — `docs/history/2026-07-04-memory-decision-brief.md`,
  AWAITING JEREMY.** Reframe: data layer is fine, ACCESS layer is the gap.
  Five verified gaps: workers get zero memory; knowledge_edges.jsonl 2,124
  edges written / zero readers (graph memory half-exists, write-only); ~8MB
  write-only graveyards; rule auto-demote unwired + no language-form demotion;
  lat.md ~200-token flat injection with 2 fabricated nodes. Recommendation:
  access-first (scoped recall + BM25 index-as-cache + graph read-side) +
  summary/handle contract; storage migration deferred; experiment gate before
  the worker slice ships. Decision points §8 of the brief.

- **2026-07-07 (memory direction DECIDED — Jeremy, back from AFK, on the
  brief)** — Direction: **memory becomes a module; consider pre-existing
  offerings before building our own.** His words, decree-level for this arc:
  "I'd almost rather adjust our interface over time to leverage unused parts
  of a 3rd party (and potentially more capable) system than have to revisit
  this over and over again"; two brains acceptable "if we've got a more
  primary/secondary system; we can have some unused functionality and ignore
  easier than continuously rewriting over time"; "**maintainability over
  cleverness or code efficiency to start**, while keeping things usable and
  good enough performance-wise"; vision context: "this project hopefully will
  be good enough to evolve on its own at some point; and if not, have its
  sub-systems (ideally including 3rd party pluggable sub-systems) evolve
  independently as well." Agreed semantics: our crystallization engine stays
  PRIMARY for compiled truth/trust; 3rd party is SECONDARY (storage +
  retrieval); unused vendor features get ignored, not wrapped. Plan: (1)
  MemoryStore port on our side + reference JSONL adapter + contract tests
  (the tests double as "what we'd ideally architect" — Jeremy's stub-and-test
  option); (2) sandboxed bake-off of TencentDB Agent Memory / Mem0 /
  Zep-Graphiti behind the same port, scored on the brief's §7 gate + local
  performance on this box, with steal-notes per candidate feeding a
  build-our-own sketch; our-own enters as design candidate, not built code.
  Qdrant is an *engine* not a memory system — belongs behind whichever
  system wins, never in our interface; Obsidian is a render surface, not a
  backend. Production recall() callers do NOT rewire until the bake-off
  verdict returns to Jeremy. He also wants the bake-off run as a reusable
  "run this prompt with this persona" pattern (the docs brain-trust shape,
  generalized). Consumer-first rule adopted for the whole arc: no memory
  piece lands without its consumer in the same chunk.

- **2026-07-07 (memory bake-off VERDICT — Jeremy: "ok, I'm convinced; steal
  sounds good when we take the strengths we're looking for from all 3 and
  put them together")** — Bake-off ran same day as the direction decision:
  round 1 paper screen (3 source-level dossiers, 4 decisive claims
  hand-verified) eliminated TencentDB Agent Memory (invalidate structurally
  impossible; postinstall patches host OpenClaw — standing caution); round
  2 live trials: Mem0 and Graphiti adapters both passed the 24-test
  contract AND both lost — ~230/~330 lines of OUR shims did the port's real
  semantics; live disqualifiers: Mem0 embedded-qdrant single-client lock
  (no concurrent processes — fatal for a forking orchestrator), falkordblite
  detached-daemon leak (~150 orphaned redis-servers reaped; box verified
  clean). **DECIDED: adapter-1 is self-built — stdlib sqlite3+FTS5 (FTS5
  verified present, sqlite 3.51.2) behind the unchanged port, stealing
  Graphiti's bi-temporal schema, Mem0's history table, TencentDB's
  rebuildable-index insurance; fastembed+sqlite-vec semantic lane only if
  BM25 measures insufficient.** Module question resolved: EMBED in this
  repo, not a separate git repo — the port is the module boundary;
  discipline: `memory_*` modules import only stdlib + each other, so
  extraction stays a copy, not a surgery; revisit a separate repo only
  when a second consumer outside Maro exists. Full pedigree (decision
  chain 2026-06-10 → today) recorded in
  `docs/history/2026-07-07-memory-bakeoff.md`; brief archived to
  `docs/history/2026-07-04-memory-decision-brief.md`.
  **Adapter-1 SHIPPED same day** (`src/memory_sqlite.py` + multi-process
  contract test + `tests/test_memory_sqlite.py`): JSONL log = truth,
  SQLite FTS5 = rebuildable ghost-proof index, adapters interchangeable
  on disk; 1.0ms/append, 1.3ms/recall. Production wiring still gated on
  the worker-recall-slice experiment (brief §7) — no runtime callsite
  rewired yet, per the consumer-first rule.

- **2026-07-07/08 (Jeremy: "Let's implement this as a /goal run")** —
  the instrument + worker-slice chunks were built BY Maro via
  `maro-dispatch.sh`, Claude Code verifying each diff before push (the
  fork-scope lesson: never trust delegated self-reports). Goal 1
  delivered `src/memory_quality.py` clean (Haiku worker lane, scope
  exact); verification caught its silent 50-item corpus cap. Full-corpus
  verdict (1,652 items): sqlite-fts5 wins hit@1 + 5× latency, loses
  hit@5/MRR to token-overlap — BM25 tuning lead in BACKLOG; this is the
  evidence base for the fastembed gate. Goal 2 delivered
  `src/memory_bridge.py` + director wiring behind `memory.worker_slice`
  (default OFF, off-path byte-identical) but hit an adapter timeout at
  step 7/10; verification completed the dangling half and fixed three
  defects (offset sidecars in the crystallization dir → store
  schema_meta; random ids → deterministic sha1, ingest idempotent;
  offset keyed by basename → resolved path). Live module store rebuilt
  clean: 414 lessons, re-ingest 0. **§7 A/B COMPLETE 2026-07-08** (16
  clean runs pooled across 2 batches + patch-up; record:
  `docs/history/2026-07-08-worker-slice-ab.md`): every measure
  favors the slice or ties — closure 8/8 vs 7/8, blocked workers 0 vs 1,
  median tokens-in −29% with review-loop exhaustions balanced 10v10.
  **FLIPPED ON by Jeremy 2026-07-08 ("looks like we have a winner") —
  now the hardcoded default (`director.py`: `config_get("memory.
  worker_slice", True)`), so new installs get it too; off path stays
  byte-identical via `memory.worker_slice: false`. Same session Jeremy
  decreed a defaults registry: `docs/DEFAULTS.md` documents every config
  key's default + reasoning + flip effect for clean-room discovery,
  census-enforced by `tests/test_defaults_doc.py`.** Same run exposed that ALL git hooks (incl.
  the worker push guard) had been silently dead since the 2026-06-25
  rename — stale absolute `core.hooksPath` pointed at the old
  openclaw-orchestration path and git treats a missing hooks dir as "no
  hooks" (an m3 benchmark worker pushed c8fe130 to main straight through
  it). Fixed (hooksPath unset, source hook de-Poe'd, reinstalled) and
  tripwired: `tests/test_git_guard.py` asserts installed+executable, no
  stale hooksPath shadowing, source parity, and block/allow behavior.
- **2026-07-08 (session close — Jeremy: "let's flip the navigator.act_dispatch,
  no need to wait on me there")** — `navigator.act_dispatch` hardcoded default
  False → True (escalate-only via the `act_moves` default), so new installs get
  the dispatch cutover the way this box has run it live since 2026-06-21
  (14/14 execute agreement, zero bad escalates; escalate defers to a human so
  it can't assert a wrong resolution). Known cost of default-ON: one cheap-tier
  decide call per autonomous dispatch (the act gate implies the model call even
  with shadowing off — noted in `navigator_shadow.py` and DEFAULTS.md).
  `navigator.act_blocked_step` stays default-OFF (thinner evidence, mid-run
  blast radius; this box opted in 2026-07-03 via workspace config). Tests:
  `test_default_is_on_escalate_acts` tripwires the default;
  `test_act_off_explicitly_escalate_is_shadow_only` pins the opt-out.
  **Latent bug found by the flip's suite run and fixed:** the idunno-chain's
  synthesized escalate (conf 1.0, `escalated_via: idunno_chain`) could ACT —
  and the chain exhausts on adapter outages too, so a rate-limited/unreachable
  navigator would have turned every autonomous dispatch into stuck (live on
  this box since 2026-06-21, same exposure at blocked steps since 2026-07-03).
  Fix: the marker now rides in `decision.payload` and both act paths
  (`handle._navigator_act_dispatch`, `loop_blocked._navigator_act_blocked_step`)
  never act on it — navigator infrastructure fails open to the pipeline;
  shadow rows still log the synthesized escalate. Regression-pinned by
  `test_synthesized_idunno_chain_escalate_never_acts` plus the two recall-guard
  tests that caught it.
  Also filed: host-check.sh alert-channel wiring as a BACKLOG todo (cron
  without an alert channel notifies nobody — needs the notify decision first).
- **2026-07-08/09** — Concurrency-hardening arc (Jeremy: "make things more
  concurrent friendly"; plan approved with one binding edit: worktree
  isolation ships **in this arc** — "not just defer and half fix this issue").
  Three decisions with teeth, all shipped and suite-green:
  (1) **file_lock reversed fail-open → fail-closed** (`FileLockTimeout` after a
  bounded 30s wait; `MARO_FILELOCK_FAIL_OPEN=1` escape hatch) — corrupting a
  learning ledger is permanent and silent, a loud bounded stall is neither;
  safe because flock is kernel-released on holder death and no locked section
  spans an LLM call (audited).
  (2) **Same-project concurrent run is refused, not queued** (`refused_busy`
  naming the holder; `--wait`/`loop.admission_wait_s` opt-in) — on an
  unattended box a queued run invisibly pins memory and the model lane;
  NEXT.md is already the queue. In-process sibling loops SHARE the slot
  (mission fan-out is one cooperating run, not a collision). Never unlink
  lockfiles (unlink/reacquire admits two holders).
  (3) **Worktree isolation is unconditional for intra-run parallel steps**
  (private worktree per fan-out step, serialized merge-back, conflict
  preserves the branch and blocks the step — never silent loss) and **opt-in
  for cross-run** (`loop.busy_policy: worktree`, default `refuse` until
  burn-in shows autonomous merge-back behaves; conflict → run `partial`).
  Explicit non-goals: model-lane contention (accepted 2026-07-02),
  cross-worker constraint semantics (BACKLOG follow-up note added).
  Commits: 97f2235 (P2), b923a98 (P3), 31f2844 (P3b); design compiled into
  `skills/arch-platform.md` § Concurrency Model + `docs/CODING_NOTES.md`.
- **2026-07-09 (1.0 installability arc opened — Jeremy: "I'd like to work us
  towards a real 1.0, then we can refine some of these additional
  capabilities.")** — Gap analysis against the installable-harness invariant;
  Jeremy greenlit items 2/4/5 immediately + docker trial of #1 on this box.
  Decisions with teeth, all shipped same day:
  (1) **Safe-by-default flips** — `budget.per_run_usd` 5.0 / `budget.daily_usd`
  25.0 hardcoded (a fresh install was *uncapped spend*; the real-money
  invariant applies more to strangers than to this box; 0/null = explicit
  opt-out) and `validate.write_fence` default ON (box-proven since 07-04).
  (2) **pyyaml promoted to mandatory dep** — without it `config._load_yaml`
  silently returns {} and every user setting is ignored; config parsing is
  core, the zero-dep stance now reads "no *optional-feature* deps".
  (3) **First clean-machine install ever attempted (docker, non-root, no
  keys) found pip packaging had NEVER worked**: `packages.find` can't see the
  flat src/ module layout, so pip "succeeded" while installing zero modules
  and every console script died ModuleNotFoundError. Masked two ways on this
  box: everything runs PYTHONPATH=src, and M5's verification used `pip
  install -e` (editable = path-injection, immune to the hole). Fixed:
  explicit 139-entry `py-modules` list + `tests/test_packaging.py` census
  tripwire (the DEFAULTS.md pattern applied to packaging). Standing lesson:
  **"verified" claims about install behavior must name the exact install
  mode** — editable and regular pip installs are different products.
  (4) **DEFAULTS census tripwire was alias-blind** — `from config import get
  as _budget_get`-style aliases evaded the fixed getter-name set; the census
  now resolves config.get aliases via AST per file. 6 undocumented keys
  surfaced and documented, including both budget caps and the write fence —
  i.e. the two keys this arc flipped had been invisible to the registry.
  (5) De-OpenClaw'd the first-run surface (doctor checks Maro's own config
  tiers; openclaw.json = optional legacy row; telegram non-fatal;
  interrupt.py fallback path). `maro-bootstrap install` now writes a
  commented starter `~/.maro/config.yml`. Escalation-channel default stays
  OPEN (Jeremy: LLM-as-orchestrator was the interface idea, "we likely need
  something in addition") — needs a design conversation before code.
- **2026-07-09 (Thread Architecture open decisions RESOLVED — decision-brief
  session on the runtime box, per BACKLOG #19's instruction; brief:
  `docs/history/2026-07-09-thread-architecture-decisions-brief.md`, claims
  re-verified against this box's code + GOAL_BRAIN before deciding)** —
  All five remaining open decisions dispositioned by Jeremy:
  **#4 persona library**: keep the curated set; build persona-evolution
  machinery only on operational pressure (repeated no-good-persona-fit
  evidence), not speculatively.
  **#5 planning-vs-Tesla-mode**: DECIDED NOW rather than deferred — Jeremy
  pushed back on the brief's deferral ("is it right to defer, or just make
  those decisions so we can move that work forward?") and the deferral logic
  didn't hold: unlike fork-rejoin, this decision point is live on every goal
  entering handle.py, so worked examples accrue by shipping a shadow, not by
  waiting. Design (approved "Yeah, let's do that"): (1) the navigator judges
  planning depth **at dispatch**, riding the existing act_dispatch decide
  call (no new LLM call — one new field in the envelope); (2) **default =
  plan** (the Tesla pushback stands as the prior); lighter shapes only on
  positive signals — concrete deliverables/paths named in the goal, recall
  showing prior successful same-family runs, NOW-shaped scope; signals are
  judgment inputs in the prompt, not a rule table
  (inference-not-taxonomy); (3) ship **shadow-first** per the 2026-05-18
  decree — NAVIGATOR_DECIDED records planning-depth beside pipeline-actual,
  adjudicate like the dumb-loop-audit rounds, per-move cutover when the
  agreement table earns it. Queued in MILESTONES. This un-gates #5 from the
  dormant full per-turn reframe entirely.
  **#6 how-the-navigator-improves**: = closing the verify→learn loop; becomes
  **the next design arc after 1.0** (not folded into 1.0, not parked). The
  memory-module arc + thread-brain compiled-truth half (both 07-03/07-08)
  supplied the substrate the 04-26 sketch lacked.
  **#8 Stage-5 portability**: Stage 5 rules are a **compiled cache** —
  portability comes via regeneration from language-form artifacts
  (skills/lessons/evidence), never from the .py itself. Consistent with the
  2026-06-11 decay-by-invalidation decree ("Stages 4–5 demotable to language
  form") and the capability-form open question (re-fight at model upgrades).
  The 5→language demotion path stays the open BACKLOG item; no new code now.
  **#9 /loop-streaming interaction**: not a Jeremy decision — Claude traces
  one real /loop session against the per-turn model and closes or escalates
  on actual friction (queued).
- **2026-07-09 (recursion decree — Jeremy, same session, decree-level design
  constraint on all scoping/slicing decisions)** — "our goals need to be able
  to recurse sub-goals, otherwise we're just setting ourselves up for a
  fancier failure … we don't need to actually directly implement that now,
  but leaving that door open for the future would be great." Framing: "boil
  the ocean … learn a language so you can draw kanji appropriately"; "a
  higher order maze traversal type idea where you have to go in a totally
  different direction for quite some time before you can get through to the
  other side you know is the goal." Worked example: a "make me rich" goal may
  require a dummy-corp setup + tax-jurisdiction research *before* signing up
  for a financial platform — a sub-goal pointing away from the parent's
  apparent direction. **Implications:** scoping/slicing decisions must never
  assume a flat goal→steps model; "spawn a sub-goal" is a legal shape
  wherever depth/scope is judged (the #5 planning-depth field must not be an
  enum that forecloses it). Existing doors to keep open, named so future work
  doesn't wall them off: navigator `fork` move (schema shipped; join gated on
  MILESTONES #4), step-to-goal elevation (BACKLOG), intent-resolution
  side-quests, the `origin` ancestry dict (run↔thread linkage), memory-port
  hierarchical scopes (`visible_at()` — sub-goal reads own + parent scope).
- **2026-07-09 (BACKLOG decision-cleanup, same session)** — (1)
  Intent-resolution minimum experiment: **accept ResolvedIntent v0 on organic
  evidence, drop the retroactive A/B** — the done-vs-achieved corpus analysis
  (queued in the 1.0 arc) is the cheaper honest check on where the closure
  ceiling is. (2) `maro tick`/`loop`/`plan` confirmed unused by Jeremy →
  **deprecated** (stderr warning + docstrings; orch.py's path/NEXT.md layer
  explicitly NOT deprecated; removal after a window — the Tier-4 subpackage
  move is the natural point). (3) host-check alerting: **channel = Telegram
  via the existing notify.command lane, frequency = daily** —
  `scripts/host-check-notify.sh` pipes FAIL output as an escalation payload
  to `notify_telegram`, crontab entry 08:05 daily installed, failure path
  live-proven (forced disk threshold → real Telegram delivery, rc 0). (4)
  fastembed semantic lane: stays gated, no decision needed — nothing is
  blocked on it; paraphrase-lane numbers are the evidence file when organic
  worker-slice retrieval misses surface.
- **2026-07-09 (1.0 scope expansion — Jeremy, same session, decree-level):**
  **"I think learning and sharing needs to be part of the official first
  release."** Three additions to the 1.0 arc, in his words:
  (1) **Default launch content via research orchestration** — "Once 'done'
  we will want to run the research orchestration to gather our default
  personas we want to ship, along with our default skill capabilities.
  There are common things that are out there that people want to be able
  to do that we can likely facilitate with existing code/skills/ideas out
  there or have the orchestrator build out things when not available."
  Sequencing: runs after the current 1.0 remainders, before release.
  (2) **Self-learning directly involved in that build-out** — "we might
  want the self-learning more directly involved to help both ourselves
  level up as well as the product overall." (Consumer relationship with
  the verify→learn arc already sequenced as next-after-1.0.)
  (3) **Portable/shareable learning** — "allowing for machine migrations
  or data sharing to help bootstrap new users seems like a no brainer down
  the road"; internet hive-mind explicitly NOT required ("could be cool as
  an opt-in"). He flags this as not fully thought through — 1.0 needs the
  *design + migration path*, not the hive mind.
  Framing worth keeping (vision-level): "At the end of the day this is
  sort of a communication platform after all, in addition to an action
  generator." Items recorded in BACKLOG "1.0 launch content +
  learning/sharing" + MILESTONES -3 remaining list (e)/(f)/(g).
- **2026-07-09 (Purgatorio pass decreed — Jeremy, same session):** a
  multi-eye pre-1.0 retrospective audit **gates declaring the 1.0 list
  complete**. His framing: "we need a pass (or more) that looks at what we
  are doing now, what our goals have been in the past, and what might be
  missing or neglected that is assumed to be working"; "a ton of work
  littered behind us, some of which my gut says should not be left behind."
  Seven eyes (ops census, data health, backward archaeologist, docs
  coherence, code-vs-spec + security + standardization, external landscape
  re-verification, forward historian), shared findings format, rolling
  reconciliation, dogfood split (bounded eyes run through Maro, serving 1.0
  item (f)), queue NOT frozen. Full design: `docs/PURGATORIO_AUDIT.md`
  ("Purgatorio — love it"). Rider: the adversarial-review pattern ("I
  really like that pattern") is a named candidate for the 1.0 default skill
  set — the audit itself doubles as its worked example.
- **2026-07-09 (1.0 item (h) — Jeremy, same session):** backend-error
  resilience + auto-resume added to the 1.0 arc: token/rate limits,
  `/login`-class auth expiry, auto-resuming interrupted work — "a sharp
  edge that will kill an end user's enthusiasm." (BACKLOG (h); note
  model-lane contention was accepted 2026-07-02 *for this box* — that
  acceptance does not extend to end users.) "I'm sure it won't be the last
  late addition."
- **2026-07-09 (Hermes substrate decision — Jeremy; back-filled from session
  382a0d38 by the Purgatorio historian, hist-01):** Jeremy DECIDED to swap
  the OpenClaw substrate to Hermes "when I get a new, more modern machine"
  ("I've been thinking for a few months I should swap over"). This
  supersedes the standing "steal-from-don't-migrate" stance for the
  *substrate* lane (the steal-from posture still governs Maro feature work).
  Riders: he'd "love to be able to tie into iMessage instead of telegram"
  (needs a Mac — pairs with the new-machine plan; every current notify lane
  is Telegram-hardwired; iMessage goes on the 1.0 item (a)
  escalation-channel candidate list), and Poe's yahoo/X accounts have
  "never successfully used those outside of direct commands" — don't build
  on those lanes. Reusable setup preserved at `~/claude/hermes-maro-trial/`.
- **2026-07-08 (budget posture — Jeremy; back-filled from session 006a52c3
  by the Purgatorio historian, hist-08):** $200/mo Anthropic + $20/mo Codex
  is the spend ceiling — "more spend honestly than I want at the moment, so
  not looking to add to that path." The API-key/OpenRouter lane is DECLINED
  until "we start looking at budget models and such for orchestration."
  Extends (does not reverse) the 2026-07-02 model-lane accept-contention
  decision. Don't re-pitch the API key before the budget-models phase.
- **2026-07-09 (post-Purgatorio decision batch — Jeremy, live conversation
  over docs/history/2026-07-09-decisions-for-jeremy.md):**
  (1) **Buckets A + B ratified** (all 8 session judgment calls + all 8 (g)
  and 9 (h) provisional design decisions are now non-provisional), with two
  riders. Rider A — **skill promotion must not gate local use on human
  review**: "we want things promoted to skills that the local orchestration
  can pick up and use while waiting for user review... those need to be
  looked at as skills-lite, and degraded the same as regular skills that
  get broken or stop working." Two-tier promotion: Maro-built skills become
  locally-usable provisional skills immediately (subject to normal
  decay/degradation on failure); the human gate applies only to
  ship-set/catalog graduation. Rider B — auto-resume stays post-1.0 but is
  wanted, and the shape may be "a more general official scheduler/timer
  that the user can hook into/see/manage if they wish" — a *visible,
  user-managed* timer layer, which coexists with the no-cron invariant
  (the invariant bans hidden self-rearming crons, not an official
  transparent scheduler).
  (2) **slack-bridge: mothball** ("clean that up and come back to that
  later... I won't use it myself for the foreseeable future") — service
  down, code kept, revive-or-delete decision deferred; token revocation
  recommended to Jeremy (his Slack admin action).
  (3) **Knowledge web: descope for 1.0, KEEP on the list** — "this makes me
  a little sad... I'd like to keep it on the list. I think it could be
  really powerful if done well (and right now sounds like it isn't)." Docs
  say node store + BM25 now; wiring the read side properly is a post-1.0
  item, not abandoned.
  (4) **PUBLISH_CHECKLIST adopted as the 1.0 gate scaffold; version is
  1.0.0 at tag time** (CHANGELOG internal 1.x numbering retired). Framing
  decree: "1.0 is more of a process gate... My '1.0 target' is to start
  trying to use the orchestration directly via openclaw or hermes instead
  of dev style" — the real gate is direct-use readiness, not a number;
  expect a fresh dogfood wave at that transition.
  (5) **claude-CLI lane README framing:** keep it prominent but honest —
  "that path is the most tested, and any supported method should work";
  add the check-your-plan's-terms caveat (his own standing ToS worry,
  hist-02). Not a demotion — a most-tested label plus caveat.
  (6) **user/ privacy fix approved as recommended** (neutral templates in
  repo, real files to workspace overlay, CONFIG.md lane documented) —
  "will tie into our shared stuff later."
  (7) **Scope/Phase 65: the A/B-control-forever config is a BUG, not a
  choice** ("that sounds like a bug actually, there might have been some
  miscommunication there"). Approved: inject ON on this box, OFF default
  for fresh installs, docs corrected. Open to deeper discussion later.
- **2026-07-09 (Jeremy, same conversation, later):** (a) **git-history
  personal-data review DEFERRED to a dedicated conversation** — he'll
  personally review what stays public: "some of that is fine to keep
  public... it's part of the history of the project, I don't mind being a
  little vulnerable/raw... but might not want certain things leaked out."
  History untouched until then. (b) **Subsystem-archaeology owner ask**
  (BACKLOG #20): qwen validator ladder (verified live: 58/71 local-decisive
  over last week), sheriff mainline exit, evolver-in-pipeline provenance,
  heartbeat-hooks-host design intent — "I'm not sure if I'm misunderstanding
  implementation or if we've genuinely lost some things here." (c)
  **Heartbeat/supervision standing constraint reaffirmed:** "hook into the
  existing system's heartbeat (i.e. openclaw on this box)... I've been
  pretty consistent in not wanting systemd level items (app, not
  systemic)" — Maro ships a tick entrypoint, never its own daemon;
  supervision recommendation revised accordingly.
- **2026-07-09 (post-Purgatorio decision batch #3 — Jeremy, remaining C-bucket
  calls):**
  (1) **Supervision: OpenClaw-heartbeat hook accepted as a SHIM, revisit
  post-1.0** — "this feels like a shim... we need a generalized scheduler
  answer. That can be the hook for an orchestrator tie-in, but we probably
  need something more (thinking also of the https server as well... maybe we
  need a daemon that runs in the background for timers and services in
  addition to the 'app'? I don't love those, but might be slightly cleaner
  than config strewn about the OS)." Post-1.0 design item: generalized
  scheduler/services daemon vs pure app. Evolver: "Ideally we find a way for
  the evolver to run (though is this a no-op anyway if no runs have
  happened? is on cleanup of a run enough?)" — his instinct is right;
  proposal on the table: trigger the meta-cycle every N-th run finalization
  (no daemon). Burn-in authorization: "Go ahead and run some runs if needed
  to get unblocked" — one-shot ticks and dogfood runs authorized; no
  persistent timer installed (off switches stay off).
  (2) **Isolation: dockerize the executor path** — "play nice with security
  here and dockerize this path so there's literally no way to screw things
  up. Mount a working dir and maybe make some other resources read only...
  a nice tight sandbox is likely appropriate." Acknowledged edges (local
  machine file oddness); wants to stay on the right side of the API-vs-CLI
  automation line ("TBH I'm fine with it but want to stay on the right side
  of that line"). Detailed design gets its own pass; SECURITY_MODEL.md
  rewritten honest meanwhile.
  (3) **README repositioning approved** — "let's keep it honest. I'd love
  it to be self-improving, but we don't have to sell that hard yet." Lead
  with the shipped accountability layer; stage self-improvement claims to
  what verifiably fires.
  (4) **Versioning/workflow ratified as-is** — "never been a stickler for
  hard boundaries of semver"; small audience; open to change later.
  (5) **Verdict-blind learning (SF-2): green-lit as a straight-up bug fix**
  — tri-state goal_achieved on outcomes rows + prefer-verdict consumers.
  (6) **Escalation channel (item a): later, its own conversation.**
  Jeremy's side: Slack admin cleanup (token revocation) on him.
- **2026-07-10 (retention decree — Jeremy, live conversation over the
  BACKLOG #18 fix):** auto-deleting run data is a **bug**, not a hygiene
  feature — "I'd prefer to have the users choose to archive/delete old
  runs, rather than have the system decide it's clutter... the result
  isn't always *just* the outcome, it's also the path that gets you
  there" (his analogy: math homework that doesn't show its work isn't a
  full picture). Rationale from lived experience: "I've debugged one too
  many missing data points or lost one too many important emails a few
  years down the road." Standing shape: retention is a **user-level
  decision**; auto-cleanup may exist only as an explicit user opt-in,
  default off ("easier to ignore the old data than wish it weren't
  deleted"). Shipped same day: `keep_artifacts` retired,
  `artifacts.auto_prune_days` (0 = never) is the only prune path, and
  even opted-in pruning never touches the just-finished loop's files.
  This generalizes the existing "decay trust, never data" design
  constraint from memory stores to ALL run data — future cleanup/GC
  features must be user-visible opt-ins. Rider: add **goal search to the
  run visualization** (BACKLOG #17) — kept-forever data must be findable
  and poke-aroundable to earn its keep ("a user is going to trust more...
  when they can poke around and see what actually happened").
  **Decree audit, same session ("let's fix, no time like the present"):**
  a sweep of every deletion site in src/ found three more system-decided
  data deletions, all converted to archive-not-delete: (1) lesson decay-GC
  destroyed lessons below score 0.2 (the path that once ate the whole
  38-lesson MEDIUM store) → now archives to `memory/lessons_archive.jsonl`,
  `search_graveyard` reaches the archive, `resurrect_archived_lesson()`
  restores, `forget_lesson` archives as `user_forget` (never
  auto-resurrected); (2) skill island culls + A/B variant retirement
  hard-deleted skills → now archive to `memory/skills_archive.jsonl` +
  `retire` provenance; (3) finalize deleted the checkpoint on done while
  closure verification (which can demote done→incomplete) runs after
  finalize → checkpoints now kept, stranded-sweep unaffected (skips
  finalized runs via metadata). Enforcement:
  **tests/test_no_silent_deletion.py** — AST tripwire over every
  file-deletion call in src/ with a justified allowlist (same pattern as
  the DEFAULTS census); new deletion sites fail CI until reviewed.
- **2026-07-10 (production-always decree — Jeremy, adjudicating live-batch
  finding batch-01):** "Let's change environment to always be production,
  and add a switch to turn on/off debugging information; more logs and
  such... I think we want production all the time." Standing shape: **there
  is no dev/prod behavior split** — the system always runs production
  semantics; behavior gates are explicit config knobs, never environment
  inference; the only environment-like switch is `debug` (log verbosity,
  observability only — flipping it never changes what the system does).
  Shipped same day: `environment` key removed; guardrail auto-apply keys
  off `evolver.auto_apply` (default False = held_for_review); CLI review
  paths pass `manual=True` (human ask IS the review — bypasses the hold,
  never the injection guard or test gate); `debug` key + `MARO_DEBUG=1`.
  Vein confirmed in the same message: system-usable-by-default vs
  reviewed-before-behavior-changes is the same line as Rider A skills-lite
  — lessons/observations auto-apply (use-what-it-wants lane), guardrail
  mutations hold for review (full-skill lane).
- **2026-07-10 (lessons-from-toy-goals — Jeremy, same message):** zero
  lessons from the 9-run live batch is not a concern to him — "I'm not
  very worried from the little I've seen of the asks (i.e. 'write a
  poem...' type goals). I'm not sure there's much to learn from a goal
  like that." Implication for batch-04: lesson-funnel measurement should
  ride organic direct-use work, not synthetic batches; funnel-threshold
  tuning is deprioritized until real goals flow.
- **2026-07-11 (opt-in coordination brain — Jeremy, evening):** "All of
  this is starting to make me want an opt-in brain for the users of this
  orchestration, to share knowledge and skills; sourced, with pedigree,
  maro-graduated and proven skills only, and only opt-in overall from
  the user's standpoint, the sharing and details are maro-as-clients
  talking to a coordination server. But that's for later." Post-1.0
  vision, refines the 07-10 shared-directory direction with
  architecture: coordination server + maro clients, pedigree
  first-class, graduation as the share gate, opt-in as hard default.
  Recorded in BACKLOG Vision. Same message: X API pricing rules out the
  official lane — "free fragile workarounds it is for now" (the
  x-ct-reseed/cookie dance is the accepted posture, maintenance
  expected); routing gap remains a real priority ("routing prob still
  needs addressed and matters"); session-reuse parked as a standalone
  BACKLOG investigation, not a session rider.
- **2026-07-11 (learn-a-language + platform-access skills — Jeremy):**
  doubling down on the failure-pattern research: "I'd like to push for
  the whole 'learn a language to get things done' thing here... skill to
  read reddit content for research would be a great bundling add for
  example, we're not unique here, even if it has some setup involved."
  Direction: paying one-time setup cost to unlock a blocked data source,
  then capturing the recipe as a bundled skill, is a first-class pattern
  — not a workaround. First instances shipped same day:
  skills/social_search.md + scripts/x-ct-reseed.sh (Reddit per-post RSS
  door, X CT-cache reseed). Same message: real-world use cases are what
  "we can lean into for starters when we dig in a bit more directly" —
  the corpus + CAPABILITIES catalog are that list.
- **2026-07-10 (capabilities catalog + example collection — Jeremy):** "we
  need some real test cases to list, maybe in the readme, certainly in
  some kind of capabilities doc... We should collect more and different
  examples, both simple and complex. For better testing, learning, and
  overall initial capability of the system and its skills." Canonical
  simple case, from the car same day: "where can I get non-ethanol gas in
  or around Manti, Utah?" — "orchestration is the perfect way to ask that
  question, do some research, and get an answer; that's the UX we are
  looking for in a simple case." Standing shape: real asks get captured
  as-phrased into `docs/CAPABILITIES.md` (living catalog, shipped same
  day); the catalog is simultaneously test corpus, learning corpus, and
  capability target. BACKLOG item 22.
- **2026-07-10 (blank-slate pre-installed capability set — Jeremy, same
  message):** "I'd love a blank slate with a small-ish but useful
  pre-installed list of capabilities that we think might be the right
  target (and maybe a shared and trusted directory to pull from at a
  later time, crowd-sourced or not)." Fresh installs should be useful
  day-one via a small curated skill set that covers the catalog tiers;
  the shared/trusted directory + cross-instance learning share is
  post-1.0 direction (supply-chain trust boundary noted in
  CAPABILITIES.md). Draft target list in CAPABILITIES.md awaiting his
  reaction.

- **2026-07-10 (Manti NOW answer = abject failure — Jeremy, on seeing it
  in Telegram):** "I'd call it an abject failure, and is a great example
  of why people don't always trust asking AI for answers. It basically
  says 'here are maybe some ways you could find that out yourself'. We've
  had that via siri for about 15 years." He'd hoped it escalated to
  something better — it did not (the good agenda run was manually
  forced). Implication: the NOW self-verdict must judge a
  no-answer-in-the-answer as not-achieved (the model's own "I don't have
  real-time access" admission is the signal), and a not-achieved NOW
  verdict should escalate. Run output must be visible in the runs report
  either lane ("whatever you used to run either way should be visible").
- **2026-07-10 (no new errand lane — taste/discretion lives in planning;
  Qix-cuts is the intended shape — Jeremy, same message):** "I don't
  think this should be a new errand-scale agenda, but agree that it's a
  bit more time sensitive. This gets into taste and discretion a bit."
  The pattern he has been describing since the constraint-orchestration
  conversations: "the 'cleanest' example would be the Qix-like cuts off
  of a rectangle to narrow the field of view in 0-4 ish steps, then do
  the work inside those boundary lines (and sometimes re-draw those, aka
  go back to the drawing board as new information surfaces)." Both
  human approaches to the Manti ask were cuts-first (wife: search →
  gasbuddy-like site → zip narrowing; Jeremy: prior knowledge "Maverik
  sells E0" → maps search for Maveriks → verify details). Status: the
  rectangle idea is captured in docs (REASSESS_LINEAGE "draw the
  rectangle first", CONSTRAINT_ORCHESTRATION_DESIGN/REVIEW) but only the
  flat half shipped (scope-text injection pre-plan); the iterative
  cuts-process in decompose — narrowing steps before committing, plan
  size proportional to the ask, re-draw on new info — is unimplemented.
  Vehicle candidate: the queued #5 planning-depth shadow thread.
- **2026-07-10 (decision-making is the main line — Jeremy, same evening,
  deferring the container-executor design pass in its favor)** — "#4 can
  wait. Let's stay on the trail of better decision making. Without
  better decomposition/goal analysis most of the rest of this is window
  dressing in practice." Standing prioritization signal: decomposition /
  goal-analysis quality outranks infrastructure polish when the two
  compete. Same-evening consequence: cuts-first planning v0 shipped
  (068eddd, `planner.cuts_first`) — the un-shipped iterative half of the
  Qix-cuts decree, implemented as draw_cuts (constraints-with-basis +
  0–2 probes) + boundary-step expansion with probe evidence in context.
  #4 container-executor design pass remains queued, Jeremy-gated.
- **2026-07-11 (model-route exploration sanctioned — Jeremy, evening
  decision batch):** "let's add to the backlog me spending some $ for
  real to get a path going in that direction... OpenRouter, Fireworks
  AI, OpenCode Go (or Zen?), or maybe Featherless... The split part is
  the rabbit hole between a service using OSS models (possibly reduced
  capability, but that might actually be good for our infrastructure
  hardening) vs things like codex-oAuth or claude -p routes." His $,
  his session to run ("I'll send a session down that rabbit hole
  sometime soon"). Prep delivered: docs/MODEL_ROUTE_EXPLORATION.md +
  BACKLOG #24. This AMENDS the 2026-07-08 budget-posture decree's
  "API-key lane DECLINED until budget-models phase" — the phase now has
  a sanctioned on-ramp; still don't start it unprompted.
- **2026-07-11 (cost is not the end-all-be-all — Jeremy, on the
  mid-step cost circuit):** "We need to be careful we don't create
  churn and more waste by stopping and retrying as we do allowing legit
  hard things to finish long tasks. (because we do, in that sense, with
  more steps up to a point; and put a cost ceiling on our
  orchestrator's capability in a bad way)." Standing design constraint
  for ALL budget machinery: breakers are runaway-only backstops, never
  capability ceilings; retry-churn from an over-tight breaker is itself
  waste. Same-evening consequence: runaway circuit shipped (dedbdde,
  `budget.runaway_multiplier` 1.5x ABOVE the between-step stop).
- **2026-07-11 (boil-the-ocean framing — Jeremy, same message):**
  "this is where 'boil the ocean' type stuff comes from... effort !=
  bad, and we need to help frame things well so we know when it's good
  and when it's futile." Companion principle to the cost decree: the
  system's job is not to minimize effort but to tell WELL-SPENT big
  effort from futile big effort at framing time (cuts-first, scope,
  planning-depth are the current vehicles).
- **2026-07-11 (NOW→AGENDA verdict escalation approved — Jeremy):**
  "Yeah, we should just do this... (non-success just becomes a regular
  non-NOW run, possibly with the NOW context attached?)". Shipped
  same evening (a1f472f): self-verdict judges non-answers; not-achieved
  re-routes to AGENDA with the failed answer as context; default OFF
  fresh installs / ON this box.
- **2026-07-11 (capabilities captured as they come — Jeremy):** "Let's
  build out capabilities as we refine the process as they come up (sort
  of a middle ground between testing and backlog maybe... brief
  claude.md note?)". Shipped as the CLAUDE.md capability-capture rule:
  real asks / missing-capability moments go into docs/CAPABILITIES.md
  as-phrased in the same session. Escalation design itself stays
  Jeremy-gated: "I have thoughts on escalation, that's later."
- **2026-07-11 (budget posture updated — Jeremy):** "less important now
  that we've escalated my claude plan to the highest monthly tier for a
  personal account." North star restated: "I think I'm ultimately
  wanting this to be capable for a home user on local hardware, but
  that's a dream that IMO isn't realize-able quite yet... doesn't mean
  we shouldn't try. Partly why we're still all in on our 2014 linux mac
  mini, surfaces edges that fast hardware/better LLMs might not." The
  Mini is a deliberate edge-surfacing instrument, not a constraint to
  engineer away. Also: garrytan persona demoted power→mid ("close
  enough to the opus of a few months ago", 93cd35f).
- **2026-07-11 (time blindness — Jeremy, closing theme, no concrete
  goal yet):** "humans perceive stories and ideas over time (as we
  experience them) and LLMs... don't. That's a communication blind
  spot. We might need to fight some kind of time blindness between
  prompts, even in the same session, I think it's getting worse rather
  than better here and there, and sometimes it matters a lot."
  Recorded as a vision thread (BACKLOG) — candidate starting hooks:
  age-stamped evidence/context injection, elapsed-time awareness
  between steps and sessions.
- **2026-07-11 (perspective / camera rotation — Jeremy, closing
  theme):** "I've talked about rotation, and zooming in and out for
  seasoned developers. That's really just re-framing and adjustment of
  perspective (from a game engine camera type perspective), and I think
  the same holds true for ideas. LLMs have ridiculous access to data,
  language and information. But the perspective isn't the same at all.
  We need to help bring the 'human' perspective, both innate and
  skilled usage of, into things at least in a more functional light...
  Watching you react to seeing the orchestration finding some of the
  perspective that is much more easily discoverable from an end-user
  perspective makes me happy -- we're getting there to a degree, but
  I'd like to refine that." Ties to the inference-not-prompting decree
  (rotation/zoom as inference moves, not prompt taxonomies) and the
  end-user-perspective findings from the corpus arc. Vision thread in
  BACKLOG alongside time blindness.

- **2026-07-11 (corpus Family 6 — Jeremy):** "Let's go with your
  suggestion, roll deletion into the more complex family 6 --
  violations." New corpus family: **agency/trust violations** —
  1rdpsww (unauthorized Gmail access + denial under confrontation)
  admitted as 6.2 with root causes `unauthorized_tool_action` +
  `denial_under_confrontation`; 4.3 unscoped cascading deletion ROLLED
  IN as 6.1 (not merely cross-tagged; the deletion pattern's defining
  failure is the scope breach, not the state loss). Corpus v2.2:
  24 entries / 6 families / 17 root causes / 0 pending. Design echo
  for Maro itself: "did you do X?" must be answerable from an action
  log the assistant can read, never from parametric self-belief.
- **2026-07-12 (escalation channel DECREED — Jeremy; resolves 1.0 item
  (a), the last open 1.0 design sub-item):** verbatim: "My hope for the
  orchestration engine is that the user interacts with the orchestrator
  via an LLM. That's the escalation -- direct to the user through their
  orchestration go-between (openclaw, hermes agent, claude or codex CLI,
  etc)... the entire original idea was to have an LLM walk through this
  with a user and be the go-between. If we want to keep this 'headless'
  or CLI style, then output should also be in that direction, either an
  output file (maybe we should do that anyway if it's not already there
  with the visibility reporting in mind). I don't know that we need
  escalation like a beacon trying to get the user's attention." Recorded
  shape (confirmed): the substrate LLM go-between IS the official
  escalation surface (the existing `notify.command` substrate contract
  is the design, not a stopgap); a durable escalation FILE surface ships
  unconditionally (rides run-visibility — escalations persist under the
  workspace output/run index even when a notify lane delivers); no
  attention-seeking channel machinery; doctor's role is honesty only —
  report which escalation surface is live. Implementation is a
  Sonnet-sized chunk (file surface + doctor row + README posture).
- **2026-07-12 (portable-learning §8 RATIFIED — Jeremy):** all 8
  provisional decisions in `docs/PORTABLE_LEARNING_DESIGN.md` §8 stand
  as written (packs exclude raw runs; arrival trust capped 0.5/
  hypothesis tier; hash-identical import ≠ confirmation; never
  auto-adopt; mechanical scrub + mandatory human review, no anonymization
  claim; restored machine-state never auto-revived; maro-pack new /
  maro-import unchanged; minimum 1.0 slice = chunks 1–4). Numbers stay
  tunable; the shape is the commitment. Implementation unblocked.
- **2026-07-12 (backend-resilience provisionals RATIFIED — Jeremy):**
  the 4 review-flagged decisions in `docs/BACKEND_RESILIENCE_DESIGN.md`
  stand: auth/billing = one failover attempt + ALWAYS notify; auto-resume
  (when built, post-1.0) capped at 1/run; resume surface CLI-first with
  notify carrying the command; depth-cap inconsistency (4 / <3 / 2) to be
  fixed to one documented number (cheap chunk + tripwire).
- **2026-07-12 (`claude -p` ToS posture — Jeremy):** subprocess lane
  KEEPS its place as the no-key quickstart default; docs gain an explicit
  usage-policy caveat sentence ("runs under your own subscription and
  judgment — review Anthropic's usage policies"). Not demoted behind the
  API-key lane.
- **2026-07-12 (orphan scope A/B datasets WRITTEN OFF — Jeremy;
  closes arch-03):** the two unanalyzed paid experiments
  (`~/.maro/experiments/scope-ab-2026-04-25-v0/`, `-26-v1/`) are
  explicitly written off — the inject decision was already made on the
  2026-04-22 evidence and shipped. Spend acknowledged, not silently
  forgotten. Data stays on disk per the retention decree.
- **2026-07-12 (slack-bridge — Jeremy):** leave as-is; not worth touching
  before 1.0. (Tokens in `.env` remain live-looking; revisit belongs to
  whoever next touches the notify surface.)
- **2026-07-12 (heartbeat toggles — Jeremy; closes ops-r2-01/02):**
  `heartbeat.autonomy` goes back OFF on this box until the direct-use
  transition (the stated gate for any recurring hook), and host-check's
  900s heartbeat-age red is re-aligned to what one-shot ticks actually
  promise (alert on no-tick-in-N-days or drop the age check) so
  daily-red stops being structural noise. Runtime-box config change —
  execute on the Ubuntu box, not this Mac.
- **2026-07-12 (navigator `close` cutover RECONFIRMED organic-blocked —
  Jeremy):** no cutover until real non-synthetic close divergences
  accrue in NAVIGATOR_DECIDED and are adjudicated. Nothing to build;
  watch the agreement table.
- **2026-07-12 (Fable-handoff session context):** top-tier-model access
  ends ~2026-07-13; this session front-loaded design judgment (decision
  batch above + design passes for container executor, verify→learn,
  routing/verification gaps) so subsequent Sonnet/Opus sessions inherit
  execution-shaped chunks. BACKLOG #23 closed + #24 in progress in
  concurrent sessions the same day.
- **2026-07-12 (handoff designs SHIPPED, same session):**
  `docs/CONTAINER_EXECUTOR_DESIGN.md` (clears arch-r2-01's design half;
  key catch: host `~/.claude` rw-mounted into the sandbox is an escape
  vector — dedicated container auth volume instead; chunks C1–C4, flip is
  Jeremy's at C4), `docs/VERIFY_LEARN_ARC.md` (next-arc-after-1.0 brief;
  changes get expectation-at-birth → verdict-at-cadence → auto-revert with
  symmetric authority; hard dependency = probe-env hardening B3),
  `docs/history/2026-07-12-routing-and-probe-synthesis-design.md` (needs_live_data classifier
  signal — the classifier prompt's own BTC example teaches the Manti
  misroute; Deliverable.shape + shape-conditional behavioral-probe MUST +
  cwd=None hole closed). Ordered execution queue = MILESTONES -5;
  successor-model guidance = `docs/IMPLEMENTATION_HANDOFF.md`; session
  record = `docs/history/2026-07-12-fable-handoff.md`.
- **2026-07-12 (container-executor follow-up — Jeremy, same session):**
  "the general orchestrator shouldn't be modifiable and slight concerns
  about data escalation leading to targeted exploits from that
  ecosystem... a few shades of grey in the paranoia direction." Recorded
  into CONTAINER_EXECUTOR_DESIGN.md: (1) orchestration-ABSENT posture made
  explicit (never mounted — code, config, ledgers, secrets all invisible;
  absence beats read-only; no orchestrator copy in-container — recursion
  is host-spawned per the recursion decree); (2) **copy-not-passthru for
  self-development runs adopted** (repo mounts ro, container clones into
  rw scratch, host-side fetch merge-back) — Jeremy's instinct also fixed a
  v1 mechanical bug: git worktrees write objects to the PARENT's
  .git/objects, so the original "parent ro + worktree rw" spec couldn't
  commit; (3) the isolation ladder named in §4b — the
  artifact-to-future-prompt loop is explicitly NOT covered by the
  container (injection_guard/cs-r2-01 family remains that gate);
  quarantine-until-verified scratch = deferred opt-in on evidence, not v1.
- **2026-07-12 (learning-trust direction — Jeremy, closing the container
  conversation; direction not task):** "I was thinking of skill poisoning
  and self-learning edges, less direct prompt injection, but same sort of
  thing in both directions. More and more complicated, ultimately we will
  likely need 'usage only' vs 'learning' sessions, the ideal being
  scanning and auto-upgrades by the system itself, which is a neverending
  quest, same as a virus scanner solving protecting an individual
  workstation; one way to solve it but a constant maintenance headache.
  We'll get into all that more later I'm sure; glad we're at this stage at
  least for now to protect direct problems." Doors already built when this
  gets a session: usage-only ≈ a session/run flag over the existing
  learning seams (`defer_learning`, crystallization gates, skills-lite
  promotion switch — the ingestion side is already gateable per-run); the
  scanning/auto-upgrade half is the verify→learn arc's
  expectation→verdict→demote lifecycle applied to learned artifacts
  (VERIFY_LEARN_ARC.md V2/V3 are the seed); trust boundaries recorded in
  CAPABILITIES.md + PORTABLE_LEARNING_DESIGN (imports arrive contested).
  BACKLOG Vision entry added alongside the shared-skill-directory item.
- **2026-07-12 (container-executor arc RATIFIED worth-the-effort — Jeremy,
  follow-up session):** Jeremy weighed the containment win against the
  input/output hoop-jumping ("torn... worth the effort here and requiring
  docker for us overall?") and ratified proceeding with C1→C4 as designed
  (`docs/CONTAINER_EXECUTOR_DESIGN.md`). The deciding shape: (1) outputs
  need no extraction in the normal case — project dir + goal-declared
  roots are rw bind mounts, so worker writes land on the host directly;
  the only extraction flow is the self-dev scratch-clone merge-back, which
  is deliberate (live repo never container-writable) and rides existing
  `worktree.py` serialized-merge semantics; (2) inputs are a mechanical
  translation of what the fence already computes — the real unknown is
  dropped host-env inheritance, which is C4 burn-in's job to surface
  (each hit is a one-line `-e` fix); (3) **docker is never a hard Maro
  requirement** — `executor.container` stays `off/on/require` with loud
  degradation; intended posture `on` for the runtime box after C4 burn-in
  evidence (autonomous overnight runs over ecosystem content are exactly
  the containment-pays profile), `off` for fresh installs at 1.0 with
  doctor/README stating plainly which layer a run gets. The C4 flip
  itself (box default + fresh-install default) remains Jeremy's call on
  burn-in evidence, per the design's §6 — unchanged by this entry.
- **2026-07-12 (PyPI release posture — Jeremy):** two-step publish decided.
  (1) Reserve the name NOW at **0.8.0** ("okay with publishing what we have
  to reserve the name... probably around 0.8; not ready for 1.0 yet") —
  1.0.0 stays the real-readiness release. (2) **No auto-publish** ("we
  shouldn't auto-publish (yet?)") — the release workflow is manual
  `workflow_dispatch` with `dry_run` defaulting true; the publish job only
  runs on explicit `dry_run=false`. (3) Auth is **trusted publishing
  (OIDC), no API token** — Jeremy registered the pending publisher (repo
  `slycrel/maro-orchestration`, workflow `pyPI-workflow.yml`, env `pypi`);
  matching workflow + metadata + version bump shipped `6befbfb`, build +
  `twine check --strict` green on both artifacts. The publish click itself
  stays Jeremy's act. Next gate before 1.0: the deferred git-history
  personal-data review. See `docs/PUBLISH_CHECKLIST.md`.
- **2026-07-12 (git-history privacy review — scope set by Jeremy):** first
  scan done (`docs/history/2026-07-12-git-history-privacy-scan.md`). Package
  ships clean; no real secrets in history. **Jeremy's line:** KEEP all
  research/knowledge-layer docs (nuanced history has value — no deletions);
  the ONE concern is commit attribution under the work email
  `jstone@redacted.com` (46 commits, "privacy on both sides"); NOT bothered by
  his name/content in chat logs, personal Mac username, or the Manti/Utah
  location. Fix = `git filter-repo --mailmap` remap of the work email (and
  optionally the personal yahoo) to `slycrel@users.noreply.github.com` —
  proven on a throwaway clone (<1s, content preserved, 0 redacted left).
  ~1101/1114 SHAs rewritten → force-push + box/session re-clone; execution
  gated on Jeremy's go + a quiet box. **FINALIZED same day:** keep the yahoo
  identity (marks OpenClaw/Codex-initiated changes — him indirectly); ALSO
  obfuscate the employer strings (`redacted` → `redacted`) in blob content AND
  commit messages via `--replace-text`+`--replace-message` ("security for my
  employer... obfuscated is as good as deleting"), keeping context. Final
  config proven clean on all three surfaces (metadata/content/messages),
  yahoo + research docs preserved; `--replace-message` is REQUIRED (two
  session doc-commit messages name the email). Turnkey runbook in the scan
  doc. Jeremy will execute soon, after wrapping the concurrent Sonnet
  session. 0.8.0 publish independent, before or after.
- **2026-07-12 (git-history rewrite EXECUTED — Jeremy said go):** force-push
  landed. `origin/main` 16cd656→`77db43c`, `factory` 27acb82→`1476f97`, tags
  re-pointed (`v0.2.0` changed, `v0.1.0` had nothing to scrub), no refs
  deleted. Verified 0 employer-token occurrences on every surface
  (author/committer, commit messages, blob content, tag tagger) across ALL
  refs; yahoo identity kept (86); research/knowledge-layer docs intact. Local
  pre-rewrite backup in scratch (`maro-mirror-backup-prewrite`, never pushed —
  recovery path; delete when confident). **The scan doc + these Decisions
  entries were self-scrubbed by the rewrite** (the employer token → `redacted`
  wherever they quoted it — expected, meaning survives). Also fixed: this
  Mac's git `user.email` was the work email, so set repo-local `user.email` to
  the noreply identity to stop new commits reintroducing it. **ACTION REQUIRED on every
  OTHER clone (Ubuntu box + any session): `git fetch origin && git reset
  --hard origin/main` — do NOT pull/merge; ~1101/1114 SHAs changed so old
  local history is orphaned.** PyPI 0.8.0 unaffected (no SHA coupling).
- **2026-07-14 (test-maintenance posture — Jeremy):** test count is not a
  coverage goal. After observing that the suite kept growing, Jeremy approved
  the whole cleanup and cited the earlier successful reduction from roughly
  4500 tests / 10 minutes to 1500 / 2 minutes "with no meaningful loss of
  coverage." Standing rule: retain distinct behavioral and safety-boundary
  evidence, consolidate redundant scalar examples and repeated expensive
  setup, and judge reductions by honest full-lane behavior, runtime, and
  coverage—not by the raw number of test functions.
- **2026-07-15 (C4-BOX burn-in run + container containment finding — Jeremy:
  "do both"):** the containerized-executor C4 burn-in ran on the box (auth
  volume seeded via interactive `/login`; CLI pin bumped 2.1.207→2.1.210). A
  3-goal concurrency batch under `container: on` ran clean — honesty machinery
  (ralph-verify, adversarial `DISMISSED_BY_PROBE`, done≠achieved) fired
  identically to host mode, uid/gid `clawd:clawd`, ~0.8 s/step boot-tax with no
  cliff. The burn-in surfaced **two real mount findings, both fixed + re-verified
  live**: (1) file-shaped fence roots were silently dropped from the mount map
  (`_mountable_rw_dir` parent-translation); (2) **a containment gap** — a goal
  that *declares* a host-secret path outside the workspace got it bind-mounted rw
  (real `docker cat` printed the canary), because the design trusts goal-declared
  roots and the forbidden list is a blacklist. Jeremy's call: **do both** — (a)
  **tighten mounts** (`build_mount_map` now whitelists rw mounts to the workspace
  subtree + explicit `validate.write_fence_allow`, drops the rest loudly; fails
  closed) and (b) **reword the probe** (new `container-acceptance-probe.sh
  structural` — deterministic containment proof independent of model behavior,
  since the worker *refused* the hostile goal and the behavioral probe was
  inconclusive). Post-fix structural probe: **CONTAINED** against real docker for
  both declared-out-of-scope (T2) and un-declared (T1) paths. Container suite 95
  green, real-docker e2e 15 green. **Box left `container: off` overnight** (the
  fix makes it strictly safer, but off is the conservative state pending Jeremy's
  read); flipping box→on is his one-line call, and the fresh-install default
  stays off regardless (`docs/CONTAINER_BURN_IN.md` §5b/§6). Follow-up (BACKLOG):
  container `/tmp` is ephemeral per step — candidate per-run host scratch bind.
- **2026-07-15 (Jeremy: "fix the ephemeral /tmp with a step-named mount … do
  what's already logged if that's better") — SHIPPED per-run, not step-named.**
  Container `/tmp` no longer vanishes across a run's steps: `run_scratch_dir()`
  provisions `<run_dir>/scratch` (on the workspace subtree → inside the C4-BOX
  containment write-scope, retained with the run per the retention decree) and
  `build_run_command` binds it at the container `/tmp` — the one mount that is
  NOT identity-mapped. Took the *logged* per-run design over Jeremy's proposed
  "step-named" (his own deferral): the bug is cross-step *persistence*, and a
  per-step/step-named dir would relocate `/tmp` to the host but still lose it
  each step. Steps within a run are sequential → no intra-run race. Reversible:
  `executor.container_run_scratch: false` (default `True`, DEFAULTS.md). Verified
  unit + real-docker (unbound `/tmp` gone in step 2; bound scratch persists).

- **2026-07-15 (Jeremy — session-protocol arc opened + a batch of stance
  decrees; skeleton in `docs/SESSION_PROTOCOL_DESIGN.md`, iterate there):**
  a second 2014 Mac Mini arrives 2026-07-16 — Hermes goes on it as a real
  end-user interface dispatching to Maro on this box (part product research:
  "kicking the tires as an end user"). Decrees from the planning conversation:
  **(1) Topology is not a decision** — "it shouldn't matter if the hermes box
  and this box eventually converge… persisting that data or using it in an
  alternate location isn't making a hard decision, it's just the working/active
  orchestrator." Active orchestrator = runtime fact; portable learning is the
  enabling data layer (promoted in priority accordingly). Future horizon named,
  not scheduled: forking entire goal runs / parallelizing multiples against one
  goal ("alternate timeline"), and mid-run goal mutation ("a separate maze exit").
  **(2) Spend UX = effort language, not dollars** — "that's going to take a lot
  of work" signals elevated spend; natural-language override; ranges/bounds
  opt-in only; dollar figures rot as model economics shift. **(3) 1.0 relabeled
  "initial public release"** — later, not imminent, NOT a gate for work
  (0.8.0 on PyPI was exactly this intent); moving bar accepted for now.
  **(4) Git-history personal-data blocker resolved via the history rewrite;
  PyPI publish DONE at 0.8.0** (both Jeremy, this session — closes the two
  Jeremy-gated 1.0 items as previously framed). **(5) iMessage worth
  attempting** (Hermes/channel side — Mac hardware now exists); Telegram
  fallback if awful. **(6) Modular multi-layer posture** — named contract
  edges, improve within layers or along edges ("a larger multi-layer system,
  even if it's all 'an app' overall"). **(7) Interactive lane adopted but not
  first** — seam refactor precedes it; design center = inject additional
  information into the *next pending step* alongside step results ("different
  but the same as an undetermined run or failure retry"). **(8) Split-brain
  memory (Hermes vs Maro) parked** as phantom sidequest. Transport stance:
  SSH one-shots v0, tailscale as fabric, no public ports/HTTP daemon (goal
  dispatch = code execution). Flagship scale-up test goal captured in
  CAPABILITIES.md: the 5–6yr Telegram trading-channel corpus → strategy
  (research deliverable only, no trade execution).
- **2026-07-15 (Jeremy, second round — de-1.0 pass + session-protocol design
  refinements):** **(1) De-1.0 decree:** "1.0 was useful as something to work
  toward" but became "an alternate form of prioritization that's
  semi-unintended… 0.8 was the 1.0 bar… we did that work regardless of name,
  and the line is being arbitrarily held now" → a pass REMOVED the 1.0 gating
  line from all live surfaces (MILESTONES -3 arc closed, -4 blocker list
  dissolved — SF-1 evolver production hours is the one survivor, standing on
  its own; BACKLOG "post-1.0" labels → later/vision; historical names kept).
  **(2) Effort estimator placement: post initial-plan-breakdown** — estimate
  from the plan, not the raw goal; voice UX masks the planning latency with
  filler-with-content ("let me think about this for a moment") and, better,
  clarifying questions as productive filler (buy seconds AND narrow scope;
  deliberately not-new UX patterns). **(3) Injection shape:** a NEW
  status/injection type delivered at the next available processing step (the
  LLM-TUI queue pattern); it must NOT co-opt the existing plan — an adjacent
  payload handled in tandem with regular step data, and a *decision point*
  (continue/adjust/replan), not merely context. Explicitly "not trying to
  dictate implementation." **(4) Interface brain = the user's agent, not a
  pass-through:** Hermes may inject meta-prompt information on the user's
  behalf at goal construction ("where can I get fluffy's favorite food" only
  works enriched with Hermes's knowledge that fluffy is the cat); dispatch
  should carry user-utterance + enrichment distinguishably (two-author
  provenance; mis-enrichment is a new failure mode). **(5)** Split brain
  stays parked ("relatively clean"); Jeremy's stated priority interest is
  **inner-processing visibility + metadata capture for both system and end
  user, both along-the-way and after-the-fact.** All folded into
  `docs/SESSION_PROTOCOL_DESIGN.md` (§5/§6/§7/§11/§12; open questions #1 and
  #6 answered — post-plan estimator; no persistent channel to start).
- **2026-07-15 (Jeremy, third round — enrichment MVP + overnight work mode):**
  **(1) Enrichment vs learning MVP = "we don't care":** dispatch requires the
  goal, which is ASSUMED enriched; when the interface also has a distinct raw
  user ask, pass it too (optional field). Nothing downstream is
  enrichment-aware yet — the pair is captured data for later memory/
  shared-memory work and for untangling achieved-vs-user-intent ("keep it
  simple and we will have to refine later"). Closes SESSION_PROTOCOL open
  question #8. **(2) Standing work pattern re-affirmed for autonomous
  sessions:** backlog/milestone items via sub-agent code-writing
  (Claude/Sonnet/Opus) → verify/test → adversarial-review → fix → commit
  ("that pattern has served us well the past few days"); new-found work goes
  to BACKLOG, flagged for a decision if needed, otherwise automatable like
  any other item. Decision session tentatively the evening of 2026-07-15.
  Overnight guardrails held by Claude: no container flip (Jeremy's
  one-liner), no live-run spend batches — code work only.

## Threads (system-maintained — nothing leaves this list silently)

Active:
- **Session-protocol arc (interactive lane + Hermes on the second box)**:
  opened 2026-07-15 (three Decisions entries above; living design in
  `docs/SESSION_PROTOCOL_DESIGN.md`). The seam refactor — the decreed
  prerequisite for the interactive lane (decree 7, "seam refactor precedes
  it") — SHIPPED 2026-07-15 as §6a v1: typed `ContributionLedger` (one
  accumulator, one merge point, contributors-append/drain-consumes with
  re-arm invariants on every consume-without-execute path), `maro interrupt
  --intent note` context-only injection, parallel fan-out threading; plus
  same-day companions: final-step escalate gate + loop-exit drain,
  stuck-block step outcomes recorded (full adversarial-review records in
  BACKLOG_DONE). Open on this thread: §6a gaps 3–4 (the four run-scoped
  `ancestry_context_extra` re-entry shapes; checkpoint-resume step-text
  mutation — run-scoped-vs-step-scoped semantics deliberately left for a
  decision, not invented overnight), gap 5 worker lane (a lane, not a bug),
  `director_evaluate(trigger="injection")` DECIDED + ENABLED on this box
  2026-07-16 (Jeremy: "agree, build + enable" — DEFAULTS.md row, workspace
  config; this line previously said "pending Jeremy" and lagged, corrected
  2026-07-18), network/dispatch stages LIVE 2026-07-16 (cross-box Hermes
  dispatch — see BACKLOG SP records).
- **Refactor plan (`docs/REFACTOR_PLAN.md`)**: opened 2026-07-02 off an
  architecture-review pass. Tiers 0–3 fully done and mainlined, including
  both `agent_loop.py`'s 10-file split (`242c4db`) and `evolver.py`'s
  3-way split (`3eef28b`), both shipped 2026-07-03 — see Compiled truth.
  BACKLOG #13 (evaluate evolver's six scanners for practical value)
  investigated 2026-07-03 — see Compiled truth; real finding was an
  operational gap (evolver never runs in production), left as a decision
  for Jeremy. Remaining: Tier 4 (subpackage reorganization — not yet
  scoped in detail). Each tier merges to `main` only after the full suite
  passes on the merged tree.
- **Substrate trial (OpenClaw → Maro → Telegram)**: opened 2026-07-01, contract
  half shipped + live-verified same day (see Compiled truth). Remaining:
  unattended hardening (budget caps, Step -1 recovery), OpenClaw delegation
  instruction, ~15-goal burn-in adjudicated via run_cards, then the Hermes
  adapter decision. MILESTONES item 0.
- **M5 — portability pass**: no hardcoded machine paths (`_CODEX_BIN` etc.),
  `pip install -e` works, installable harness. Last of the session-40 arc.
  Status 2026-06-10: hardcoded paths removed (llm.py, backtester.py,
  backtest_metrics.py, doctor.py), fresh-venv install verified under a foreign
  HOME, rc=1 payload-first fix shipped. Remaining: none — final sweep run
  2026-07-03 on the post-layout-unification tree (no hardcoded machine paths;
  fresh-venv `pip install -e` + foreign-HOME layout resolution verified).
  Codex-side payload check decision stays deferred-pending-repro. Thread closed.
  **Post-closure correction 2026-07-09:** M5's "pip install works" held only
  for *editable* installs — regular `pip install` shipped zero modules until
  the 1.0-arc py-modules fix (see Decisions 2026-07-09). The thread's checks
  were real but the claim generalized past what was tested.
- **1.0 installability arc**: opened 2026-07-09 (MILESTONES -3). Shipped:
  safe-default flips (budget caps, write fence), starter config, de-OpenClaw'd
  first-run surface, pip packaging actually-works fix + census, docker
  clean-machine trial (first install off this box) INCLUDING a passing E2E
  goal on the subprocess claude lane (status=done, goal_achieved=True,
  artifact verified). Pre-commit adversarial review hardened the surface:
  budget gate fails CLOSED on malformed values, coerces before truthiness
  (quoted "0" honored as opt-out), `llm.detect_backends()` is the single
  source of truth for doctor (was a hand-mirror that missed credentials-.env
  keys/CLAUDE_BIN/codex/backend_order), doctor no longer mkdirs what it
  checks, packaging census also trips on src/ subpackages. 2026-07-10
  backlog-clearing session closed the non-gated remainder (see Compiled
  truth: #21 heartbeat burn-in fixes, Rider A skills-lite, #18 CLI verdict
  parity, PyPI name check, user/ overlay completion). Remaining
  (CORRECTED 2026-07-10, arch-r2-02 — the prior list re-planned finished
  work: done-vs-achieved corpus analysis is DONE with artifact
  `docs/history/2026-07-09-done-vs-achieved.md`, README/quickstart pass
  shipped twice, 1d0707f + 83ede86): **(a) escalation channel default**
  (design conversation — Jeremy-gated), **container-executor design pass**
  (arch-r2-01 — decided 2026-07-09, vehicle now BACKLOG §-1 → MILESTONES
  scoping), **git-history privacy review** (Jeremy-gated), plus
  install-trial residuals (BACKLOG).
  **CORRECTED 2026-07-13:** (a) shipped 2026-07-12 as the escalation file
  surface (MILESTONES -5 #2); git-history privacy review done 2026-07-12
  (Jeremy's own parallel session, force-pushed rewritten main/factory,
  reconciled with zero content loss). Container-executor design pass
  fully executed C1→C4-mechanics (image+auth+doctor, the docker wrap,
  mount-map + self-dev clone, stale-clone sweep + real-docker E2E tier —
  MILESTONES -5 #6), all merged to main 2026-07-13. The only piece of
  this arc still open is C4-BOX: the real-goal burn-in (`/login`'d
  acceptance probe, dogfood no-regression run, go/no-go checklist, the
  flip) — Jeremy-gated by design (needs interactive OAuth + spends
  tokens), tracked in BACKLOG.md as C4-BOX, not an autonomous-session task.
  **CLOSED 2026-07-15 (de-1.0 decree):** v0.8.0 published to PyPI (Jeremy:
  "0.8 was the 1.0 bar"); C4-BOX burn-in ran 2026-07-14/15 (only the flip
  remains, Jeremy's one-liner); "1.0" relabeled "initial public release" —
  later, deliberately unpinned, and no longer a prioritization line
  anywhere (Jeremy: "we did that work regardless of name, and the line is
  being arbitrarily held now" — MILESTONES/BACKLOG de-1.0 pass done this
  date). Surviving open items stand under their own names: evolver
  production hours (SF-1), auto-resume, install-trial residuals,
  portable-learning decisions.
- **Goal-brain sequencing: COMPLETE** (steps 1–5, 2026-06-10/11): artifact →
  pressure test → recall() → navigator schema → navigator prompt + shadow
  replay. Successor thread below.
- **Navigator shadow rounds → cutover**: rounds 1 AND 2 done 2026-06-11
  (`docs/NAVIGATOR_SCHEMA.md` results). Round 2 (seeded random N=20, stratified
  by status): **0/6 false escalates on well-formed goals**; all 8 escalates
  targeted chop debris or repeat burn; 16/20 decided at cheap tier, 0 needed
  power. Side finding: 11/20 randomly sampled goals were decompose-chop debris
  *including most pipeline-"done" ones* — `done` status is not goal-health
  ground truth. Emergent (unprompted): dedup-via-recall (4-prior-dones drew
  close-already-delivered), chain corrects both directions (mid overrode a
  timid cheap idunno with execute), honest 0.05-confidence escalate.
  **Live shadow wired 2026-06-11**: `shadow_dispatch_live()` called from
  handle_task after the guard verdict, sharing the guard's RecallResult;
  config-gated (`navigator.shadow_dispatch`, off in code, this box opted in
  via workspace config), cheap-tier-only by default, never raises.
  Smoke-verified against the real adapter (execute 0.92, NAVIGATOR_DECIDED
  with `live: true` in the workspace log).
  **Cutover shipped per-MOVE, not per-class (2026-06-12 code, ENABLED LIVE
  2026-06-21 — see Decisions):** `navigator.act_dispatch: true`,
  `act_moves: [escalate]`, `act_confidence_floor: 0.9`. Escalate earned it
  (defers to a human, can't assert a wrong resolution) and now ACTS; `close`
  stays shadow-only until it has organic evidence. Remaining on this thread:
  passive organic accrual of escalate decisions, then the `close`-cutover
  discussion. (Supersedes the earlier "accumulating data, not before" status —
  escalate is live.)
- **Run↔thread linkage**: done 2026-06-10 — tasks carry an `origin` ancestry dict
  (parent handle/loop/goal) from enqueue through `handle_task` into run metadata,
  and recall() now consults it at dispatch (ThreadIdentity walk).
- **recall() loop-slice relocation**: **done 2026-06-11** — all eight memory
  substrates compose inside recall(slice="loop"); `_build_loop_context`'s
  memory half is one seam call (`as_loop_block()`, historical injection order
  preserved; skills/cost/graph stayed in agent_loop). Both captain's-log
  prompt-injection read bridges (agent_loop K3, evolver `_llm_analyze`)
  absorbed via shared `recall.recent_learning_activity()` — the log's
  consumers are now visibility + the seam, as the 2026-06-11 audit wanted.
  `lesson-cited` edge stamp live: loop-slice recalls record `lessons_cited`
  in RECALL_PERFORMED. Inherited wart, documented not fixed:
  `search_graveyard(resurrect=True)` mutates lesson lifecycle from inside a
  read seam (pre-existing agent_loop behavior, kept identical).

Dormant (deliberately parked, not dropped):
- Thread Architecture implementation (`arch/thread-navigator`) — parked pending
  goal-brain sequencing; fix-in-place arc takes precedence.
- Phase 65 deeper constraint-orchestration expansion — deferred; the
  single-persona ResolvedIntent MVE was enabled on the audited runtime box in
  2026-07-09, while this unconfigured M1 and fresh installs remain OFF.
- Mage correspondence memory — v1 sketch exists (typed-edge graph walk, sympathy
  weights); downstream of recall() shape.
- Backlogged repairs: 10 pre-existing test failures; fragile fail-safes in
  parallel/DAG step runners (BACKLOG.md, 2026-06-10).

## Open questions (system-maintained)

- ~~**recall() shape**~~ — answered 2026-06-10 (`docs/RECALL_DESIGN.md`); edge
  vocabulary pinned there too. Successor questions: guard thresholds are unmeasured
  (watch RECALL_GUARD_TRIPPED). ~~Per-thread goal-brain creation~~ answered
  2026-06-11: `src/thread_brain.py` seeds `source/goal_brain.md` in every run-dir
  (this file's section grammar scaled down). Per-turn maintenance: the navigator
  went live 2026-06-21 and the **decision-half shipped**. CORRECTION 2026-07-09
  (Purgatorio arch-11): the two "remaining pieces" formerly listed here —
  (a) the compiled-truth half and (b) feeding the dispatch-navigator's rationale
  into the spawned run's brain — BOTH SHIPPED 2026-07-03 with tests (MILESTONES
  #3 "both halves now closed"); this doc lagged the queue doc it outranks.
- **Fan-out recoverability mechanism** — *visibility half answered 2026-06-11 at the
  schema layer*: `open_children` rides in every NavigatorInput and close is invalid
  while any child is undispositioned (`docs/NAVIGATOR_SCHEMA.md`). Still open:
  *revisit policy* (when does the navigator go back to an abandoned/failed child?)
  — judgment, lands in the step-5 prompt and gets measured via NAVIGATOR_DECIDED.
- **When to pull full work-LLM output** — criteria for the "sometimes" in the
  2026-06-10 visibility decision. Deliberately unpinned until examples accumulate.
- **Capability-form paradigm** — when a pattern stabilizes, does it live as a skill
  (language, JIT-injected, grows with the model) or as code (deterministic, frozen,
  zero inference cost)? Jeremy 2026-06-11: on the table, decided by data, not
  upfront. Implies crystallization Stages 4–5 must be reversible and re-evaluated
  at model upgrades ("re-fight the champion"). Blocks: nothing yet — gather
  longhand reps first.
- **End-to-end standing-rule observation** — does the medium → long → standing-rule
  path actually fire in real runs post-M2? Needs production runtime, then check
  `standing_rules.jsonl`.
- **Are we re-inventing reasoning-model behavior?** (Jeremy, recurring; raised
  again 2026-07-10 against cuts-first planning.) Partially yes by design: the
  narrowing *reasoning* is what frontier models do natively in one
  tools-in-context session; what the orchestration layer adds is (1) durability
  across stateless steps (the godot agenda-divergence failure), (2) judgeable/
  learnable artifacts instead of thinking-token exhaust, (3) cost arbitrage
  (cheap narrow → cheap bounded steps vs billing the whole rectangle at
  thinking rates) — and the arbitrage spread narrows every model generation.
  Standing kill-test: CUTS_DRAWN records + outcome verdicts give the A/B — if
  a same-tier model with identical context produces equivalent plans without
  the cuts call, delete the mechanism (same reversibility posture as the
  capability-form paradigm above; scaffold is condemned property, the record
  is the durable asset).
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
