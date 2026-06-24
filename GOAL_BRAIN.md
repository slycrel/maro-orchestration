# Goal-Brain: openclaw-orchestration

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
Sample: the 2026-05-13..17 window of `~/.poe/workspace/runs/` (478 dirs total;
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
  ENABLED on this box. `~/.poe/workspace/config.yml` now sets
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

## Threads (system-maintained — nothing leaves this list silently)

Active:
- **M5 — portability pass**: no hardcoded machine paths (`_CODEX_BIN` etc.),
  `pip install -e` works, installable harness. Last of the session-40 arc.
  Status 2026-06-10: hardcoded paths removed (llm.py, backtester.py,
  backtest_metrics.py, doctor.py), fresh-venv install verified under a foreign
  HOME, rc=1 payload-first fix shipped. Remaining: codex-side payload check
  decision (deferred — JSONL format differs, no observed repro), final sweep.
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
