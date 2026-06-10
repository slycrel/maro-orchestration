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
   Mechanical fix queued in BACKLOG: parent/thread fields on run metadata, threaded
   through the requeue path.

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

## Threads (system-maintained — nothing leaves this list silently)

Active:
- **M5 — portability pass**: no hardcoded machine paths (`_CODEX_BIN` etc.),
  `pip install -e` works, installable harness. Last of the session-40 arc.
  Status 2026-06-10: hardcoded paths removed (llm.py, backtester.py,
  backtest_metrics.py, doctor.py), fresh-venv install verified under a foreign
  HOME, rc=1 payload-first fix shipped. Remaining: codex-side payload check
  decision (deferred — JSONL format differs, no observed repro), final sweep.
- **Goal-brain sequencing, steps 3–5**: recall() shape (now with the step-2
  requirement that it include a dispatch-time hook); then navigator schema; then
  prompt. Step 2 (pressure test) done 2026-06-10 — findings in Compiled truth.
- **Run↔thread linkage**: parent/thread fields on run metadata + the requeue path
  carrying ancestry (pressure-test findings 1 and 4). Mechanical prerequisite for
  recall() at dispatch time.

Dormant (deliberately parked, not dropped):
- Thread Architecture implementation (`arch/thread-navigator`) — parked pending
  goal-brain sequencing; fix-in-place arc takes precedence.
- Phase 65 constraint orchestration — paused 2026-04-23.
- Mage correspondence memory — v1 sketch exists (typed-edge graph walk, sympathy
  weights); downstream of recall() shape.
- Backlogged repairs: 10 pre-existing test failures; fragile fail-safes in
  parallel/DAG step runners (BACKLOG.md, 2026-06-10).

## Open questions (system-maintained)

- **recall() shape** — what slice of goal-brain + correspondence the navigator sees
  per turn. Blocks: navigator schema, correspondence edge vocabulary.
- **Fan-out recoverability mechanism** — how unfinished sub-threads stay visible and
  get revisited, concretely (this file's Threads section is the manual v0; the
  runtime mechanism is undesigned). Blocks: thread architecture implementation.
- **When to pull full work-LLM output** — criteria for the "sometimes" in the
  2026-06-10 visibility decision. Deliberately unpinned until examples accumulate.
- **End-to-end standing-rule observation** — does the medium → long → standing-rule
  path actually fire in real runs post-M2? Needs production runtime, then check
  `standing_rules.jsonl`.
