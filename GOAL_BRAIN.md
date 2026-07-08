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
  `docs/SUBSTRATE_INTEGRATION.md`. Hermes stance unchanged: steal-from-don't-migrate;
  adapter deferred until after the OpenClaw trial.
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
- Phase 65 (constraint orchestration) is **paused**; its minimum experiment shipped
  2026-04-23 as `src/scope.py` + ResolvedIntent. Basis: session-38 delta audit.
- Heartbeat systemd service exists but is not enabled/running (session-40 audit).
  This is consistent with the no-daemons invariant; heartbeat runs in-process when
  the app runs.
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
  accept. (4) **Memory decision brief delivered — `docs/MEMORY_DECISION_BRIEF.md`,
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

## Threads (system-maintained — nothing leaves this list silently)

Active:
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
- Phase 65 constraint orchestration — paused 2026-04-23.
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
  went live 2026-06-21 and the **decision-half shipped**; remaining pieces are
  (a) the compiled-truth half and (b) feeding the dispatch-navigator's rationale
  into the spawned run's brain.
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
