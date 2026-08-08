---
status: record
---

# 2026-08-08 — Adversarial review: Δ-demotion route (910d69a) + session-fork lane (d7a193d)

Cross-model review (Claude host → 4 Codex reviewers, `codex exec`,
read-only, `codex-cli 0.146.0`). Two lenses per commit — **correctness**
(Skeptic: what breaks, what's claimed but not enforced) and **failure
scenarios** (Failure-Operator: unbounded growth, observability, what the
operator sees at 3am). All four reviewers returned non-empty output;
none timed out.

Every accepted finding below was re-verified against the repo before
being written down, per verify-before-fix. A reviewer "high" that both
fork lenses raised independently did **not** survive that pass and is
recorded under *Refuted* with the measurement that killed it — as is one
of my own hypotheses, which died the same way.

One finding was additionally confirmed **against live production state**
on the maro box (`~/.maro/workspace`) by executing the shipped code
against the real lesson store, not just reading source. It is marked
LIVE.

Reviewer transcripts: session scratchpad `adversarial-review/` (not
retained in repo).

---

## Part 1 — `910d69a` Negative-delta demotion route

**Intent:** mirror the existing positive-Δ promotion route. A MEDIUM
lesson whose replay-measured Δ clears a negative bar gets a
`route="effect-demote"` stamp on `delta_evidence`, which excludes it
from `inject_tiered_lessons` and blocks the tenure route to LONG.
Surface-scoped by decree: no score mutation, no deletion, flat ledger
and `query_lessons` untouched.

**Verdict: REJECT** — four confirmed defects, three with reviewer
consensus. The stamp does not hold the surface it claims to hold, and
two of the commit's stated guarantees are falsifiable from the source.

### Accepted findings

1. **[high, CONFIRMED — LIVE]** The demotion filter runs *after* the
   load limit, so every demoted row silently costs an injection slot
   instead of yielding it to the next healthy lesson.
   `knowledge_web.py:1724-1729` loads `limit=max_medium *
   _pool_multiplier` and only then filters `_is_delta_demoted`;
   `load_tiered_lessons` sorts by score and slices *before* returning
   (`knowledge_web.py:705-706`). Because demotion deliberately does not
   touch score, a demoted row keeps its rank and keeps winning the slot
   it is then filtered out of.

   **Measured on the live store** (216 MEDIUM rows, the three stamped
   negatives at ranks 0, 3, 79): with `goal=None` the pool is
   `max_medium * 1 = 3` rows, two of which are stamped → **1 MEDIUM
   lesson injected instead of 3.** Three stamps out of 216 rows cost
   two-thirds of the MEDIUM decision surface. With a goal the pool
   widens to 9 and the loss is currently absorbed, so the damage is
   worst for the goalless callers.

   *Both lenses raised this.* `test_stamped_row_leaves_injection_surface`
   (`tests/test_delta_replay.py:375-381`) seeds exactly one lesson, so
   filter-before-limit and filter-after-limit are indistinguishable to
   it. The suite already pins this same limit-before-filter shape for
   `query_lessons` (`tests/test_knowledge_web.py:470-486`).

2. **[high, CONFIRMED]** TOCTOU: tenure promotion can carry a demote
   stamp into LONG, where nothing filters it. `promote_lesson` reads
   the store **unlocked** (`knowledge_web.py:1108`), checks
   `_is_delta_demoted` on that snapshot (`:1131-1137`), then pops the
   *fresh on-disk row* under the lock (`:1150`) and appends it to LONG
   (`:1153-1154`) without re-checking. A stamp landing in that window
   rides into decay-free, always-injected LONG carrying
   `route="effect-demote"` — and the LONG branch of
   `inject_tiered_lessons` (`:1699-1702`) has no demote filter.

   This falsifies two stated guarantees at once: "tenure must not
   launder a measured-negative lesson into LONG" (`:1132-1136`) and "a
   LONG row's `delta_evidence` can only carry `route='effect'`"
   (`:1721-1723`). The window is not narrow: `run_effect_route` holds it
   open for the duration of a measurement pass, which is `limit × 2 arms
   × samples × n_calls` LLM replays — minutes — while any reinforcement
   on the box can call `promote_lesson` via `_post_reinforce_hooks`.

   *Both lenses raised this.* It is the same read-outside-the-lock class
   this file already fixed once in `_reinforce_tiered_lesson` — see its
   own comment at `:561-566` ("repro'd 2026-08-04"). The fix landed on
   the reinforce path only; `promote_lesson` still checks every guard
   (provisional / quarantined / contested / Δ-demoted) outside the lock.
   `test_tenure_promotion_blocked` (`tests/test_delta_replay.py:392-407`)
   runs demotion to completion *before* promotion starts, so it cannot
   reach the interleaving.

3. **[high, CONFIRMED]** Replay failures manufacture a qualifying
   negative Δ. A failed sample appends `None`
   (`delta_replay.py:427-431`) and `_match_rate` counts `None` in the
   denominator (`:309-312`), so an outage that hits the with-lesson arm
   scores `with_match=0, without_match=1` → **Δ = −1 with
   jackknife_spread = 0**, clearing the −0.05 bar and the
   `spread < |delta|` guard trivially.

   The failure is *biased*, not random: the with-lesson arm carries a
   longer prompt (the lesson is injected into it), so context-length and
   output-cap errors hit it preferentially, and the two arms run
   sequentially in the same loop (`:414-417`) so a transient outage
   need only span one of them.

   `demote_lesson_by_effect` validates only delta, n_calls, spread and
   stratum (`knowledge_web.py:1332-1342`) — `replay_errors` is never an
   eligibility guard. Worse, it is **dropped from the persisted stamp**:
   `as_dict()` carries `replay_errors` (`delta_replay.py:356`) and it
   reaches the census row, but the stamp written at
   `knowledge_web.py:1350-1356` keeps only delta/spread/n_calls/stratum/
   measured_at/route. The durable record of a demotion therefore loses
   the one field that would let anyone later tell whether the
   measurement was sound.

   *Both lenses raised this.* `test_replay_failure_scores_no_match_and_counts`
   (`tests/test_delta_replay.py:237`) covers only *symmetric* failure
   (both arms fail → Δ=0); the acting census test (`:461`) uses an
   error-free adapter.

4. **[high, CONFIRMED]** The stamp has a ~10-day life, after which the
   measurement is erased rather than replaced. Because demotion
   deliberately does not mutate score, a demoted row keeps decaying
   normally, and the demote guard in `run_decay_cycle` pushes it into
   the GC branch: the `if` at `knowledge_web.py:1402-1407` now fails on
   `not _is_delta_demoted(tl)`, so control falls to `elif tl.score <
   GC_THRESHOLD` (`:1413`). Excluded from injection, the row is also
   cut off from its main reinforcement path.

   At `DECAY_FACTOR = 0.85` and `GC_THRESHOLD = 0.2`, the three
   currently-stamped rows (effective 0.916 / 0.901 / 0.784) cross the GC
   floor in **10 / 10 / 9 days**. GC archives and drops the live row
   (`:1449-1455`). Re-derivation then calls `record_tiered_lesson`,
   whose dedup scan reads only the live MEDIUM and LONG files
   (`:381-391`, `:412-414`) — never `lessons_archive.jsonl` — so the
   same lesson text is re-minted under a fresh `lesson_id` with
   `delta_evidence = {}` and walks straight back onto the injection
   surface with the tenure route open again.

   "Measurement replaces measurement" is therefore false across the
   ordinary lifecycle: decay plus re-minting erases the stamp with no
   new evidence at all. *Raised by the failure lens; verified here.*

   The unifying observation across findings 1 and 4: **because demotion
   changes no score, a demoted row has only two fates — it is re-derived
   often enough to stay near the top of the ranking, where it
   permanently eats injection slots (finding 1); or it isn't, and it
   decays out and returns clean (finding 4).** Both are consequences of
   the no-score-mutation decree, which is worth revisiting on its own
   terms rather than patching at each surface.

5. **[medium, CONFIRMED]** A demotion leaves no audit row. The two
   sibling exclusion mechanisms both emit captain's-log events —
   `LESSON_QUARANTINED` (`knowledge_web.py:997`) and `LESSON_CONTESTED`
   (`:1085`) — and `run_decay_cycle` writes `change_log.jsonl`
   (`:1420-1434`). `demote_lesson_by_effect` writes neither: one
   `log.info` (`:1364-1366`) and nothing durable outside the stamp
   itself. An operator asking "what got demoted this month, and on what
   evidence" has no log to read; they must grep the lessons store, and
   the stamp has already dropped `replay_errors` (finding 3).

6. **[medium, CONFIRMED]** The documented eligibility of
   provisional/quarantined rows is unreachable. `demote_lesson_by_effect`
   deliberately omits the provisional/quarantined/contested guards on
   the argument that "those rows are already off every surface, and the
   stamp is a true measurement either way" (`knowledge_web.py:1313-1316`).
   But the only shipped path that produces a measurement,
   `run_effect_route`, filters those rows out of the candidate list
   *before* the `--lesson-id` selection is applied
   (`delta_replay.py:493-499`), so even naming the lesson explicitly
   cannot measure it. When a confirming re-record later clears
   provisional or quarantine (`knowledge_web.py:553-569`), the row
   returns to injection with no measurement ever recorded.
   *Contract and implementation disagree; one of them should move.*

### Refuted

- **"`demote_lesson_by_effect` silently no-ops when the row was
  concurrently promoted out of MEDIUM."** True but harmless in
  isolation — and it is the *weaker* half of finding 2. The damaging
  interleaving is the reverse order (promote reads stale, then pops a
  stamped row), which is what finding 2 records. Filed as one finding,
  not two.

---

## Part 2 — `d7a193d` Session-fork lane for `claude -p`

**Intent:** a bare `claude -p` call re-creates ~12k cache tokens every
call because the CLI's per-invocation context is never a byte-stable
prefix across processes. Seed one master session per
`(binary, model, no_tools, cwd)` shape, then serve each stateless call
as `--resume MASTER --fork-session` so the master transcript becomes
that stable prefix. Default OFF, opt-in until burn-in.

**Verdict: REJECT** — the mechanism is real and the measurement supports
the headline, but the seed is a new agentic spawn seam that bypasses a
documented security control, and it sits outside both enforcement
boundaries it should be inside. Nothing here blocks the *idea*; finding
1 blocks the flip.

Production state at review time: `subprocess.session_fork` is **OFF**
(no override in `~/.maro/config.yml` or the workspace config), so every
finding below is pre-flip. `state/subprocess_fork_masters.json` exists
with one entry from the 10:17 live smoke.

### Accepted findings

1. **[high, CONFIRMED]** The seed is an agentic spawn seam that the
   worker push guard cannot see. `_run_subprocess_safe` sets
   `child_env["MARO_WORKER_RUN"] = "1"` on every call it makes
   (`llm.py:1197-1198`), with the comment "Mark Maro-spawned agentic
   subprocesses so git guards (`scripts/hooks/pre-push`) can tell a
   worker from a human." The hook's first line is
   `[ -z "${MARO_WORKER_RUN:-}" ] && exit 0` (`scripts/hooks/pre-push:10`)
   — no marker means the push is treated as a human's and allowed.

   The seed calls raw `subprocess.run` with no `env=`
   (`llm.py:2220-2222`), so it inherits the parent environment **without**
   the marker. It also runs with `--dangerously-skip-permissions` and,
   on the `no_tools=False` branch, without the real call's
   `--disallowedTools` (finding 2). So the seed is a fully-permissioned
   agentic Claude Code invocation, in the caller's cwd, with more
   authority than the call it exists to accelerate — including the
   ability to push to `main` where the real call would be blocked.

   This falsifies `docs/SECURITY_MODEL.md:50` as written: "**Every**
   Maro-spawned agentic subprocess gets `MARO_WORKER_RUN=1`."
   `docs/DEFAULTS.md:79` likewise says the guard is "enforced ... at the
   spawn seam in llm.py" — there are now two spawn seams and only one is
   enforced. `tests/test_git_guard.py` is the liveness tripwire added
   after the 2026-07-08 hooksPath incident, but it exercises the *hook's*
   behaviour given the variable (`:111-124`) and hook installation
   (`:77-105`); nothing asserts that a spawn seam sets it, so no test
   can catch a new seam that doesn't.

   On realism: the seed prompt is "Session seed. Reply with the single
   word OK," so a model that follows it does nothing. That is the same
   "it will obviously just classify the goal" assumption that already
   failed once here — BACKLOG #16, the routing prompt that executed its
   goal and produced a "## Done" report, which is the documented reason
   `--tools ""` exists at all (`llm.py:2291-2298`). The guard is
   documented as advisory (a worker can `unset` it), so this is not a
   sandbox escape; it is the silent removal of a named control at a new
   seam, which is exactly what the C3/C4 reviews established as the
   thing to refuse.

2. **[high, CONFIRMED]** The seed is a paid LLM call outside both the
   cost circuit and the caller's timeout. Every other CLI invocation in
   this adapter goes through `_run_subprocess_safe`
   (`llm.py:2434-2437`), which supplies a process-group kill, a liveness
   timeout, `env_extra`, and the `stream_probe` that carries the runaway
   cost circuit and the per-call token brake (`llm.py:1057-1080`). The
   seed uses raw `subprocess.run` (`llm.py:2220-2222`) and gets none of
   them; its usage is parsed only for `session_id` (`:2225`) and
   otherwise discarded.

   The author's own benchmark prices this precisely: the seed call cost
   **$0.0373 with 16,391 cache_creation tokens — more than a bare call
   ($0.0315)** (`~/.maro/workspace/output/claude-fork-bench/results.jsonl`).
   That spend is invisible to `metrics.py` and to any armed budget
   ceiling. Concretely: a run at $4.99 of a $5.00 ceiling seeds a new
   shape, spends $0.037 unmetered, and the meter still reads $4.99.

   The timeout half compounds it: `_FORK_SEED_TIMEOUT_S = 120`
   (`llm.py:2134`) is spent *before* the real call starts and is not
   drawn from the caller's `timeout`, so a `timeout=30` call can take
   150s. And a persistently failing seed is never negative-cached —
   `_get_fork_master` returns `""` without recording the failure
   (`:2231-2233`), so **every** stateless call re-attempts it.

3. **[high, CONFIRMED]** Agentic masters are seeded with a different
   tool contract than the forks that inherit them. The seed command
   (`llm.py:2212-2219`) adds `--tools ""` when `no_tools`, but adds
   **nothing** otherwise; the real call (`:2289-2305`) adds
   `--disallowedTools WebFetch,WebSearch` on the agentic branch. So
   every `no_tools=False` master is seeded with the full built-in tool
   set and each fork then runs with two tools removed.

   Tools precede system and messages in the cache hierarchy, so a
   differing tool block invalidates everything after it — which is
   exactly the prefix this lane exists to stabilise. The benchmark
   cannot detect this: it ran neither flag on either arm (`bench.py`
   uses plain `claude -p --model haiku --output-format json`).
   `test_no_tools_and_agentic_get_separate_masters`
   (`tests/test_llm.py:2785-2796`) asserts only that the agentic seed
   lacks `--tools`; nothing compares the two commands.

   The `no_tools` security boundary itself is **intact** — `--tools ""`
   is correctly carried into the seed. That was the thing most worth
   checking here, and it holds.

4. **[medium, CONFIRMED]** `_invalidate_fork_master` deletes by key
   without checking which master it is invalidating
   (`llm.py:2182-2197`), so a straggler can delete a healthy master
   seeded by someone else. Processes A and B both hold evicted master
   `M`. A hits the missing-session error and removes key `K`. C sees no
   entry, seeds valid master `N`, stores `K=N`. B's older call finally
   returns its missing-session error and pops `K` — deleting `N`.
   D cold-seeds again, at $0.037 a time. Each staggered stale holder
   repeats the cycle, so recovery need not converge after the first
   successful reseed. This contradicts "seeding races between processes
   are harmless" (`llm.py:2150-2152`). The function is never passed the
   id it believes is stale, so the ownership check cannot be made
   without a signature change.

5. **[medium, CONFIRMED]** In-process masters never expire. The key is
   `(claude_bin, model_str, no_tools, cwd)` (`llm.py:2152-2158`) and
   `_get_fork_master` checks the in-memory cache first, then disk, then
   seeds (`:2204-2210`) — there is no TTL, no content hash, and no
   invalidation on anything except an eviction error. The master
   transcript is the replayed prefix, and the benchmark shows it is
   replayed *from cache* rather than rebuilt (fork `cache_read` is a
   constant 34,185 vs bare 22,149, with fork `cache_creation` ~215).
   So the CLI's per-invocation context as of seed time — CLAUDE.md
   included — is what every later fork sees. CLAUDE.md changed twice
   during this review session alone. An operator edits it, lands it,
   and every stateless call keeps replaying the pre-edit copy with no
   log line and no way to notice. The repo's own currency rule is the
   thing being silently defeated.

6. **[medium, CONFIRMED]** Forked calls are not conversationally
   stateless. `_FORK_SEED_PROMPT = "Session seed. Reply with the single
   word OK."` (`llm.py:2133`) and its `OK` response are the first
   user/assistant turn of every forked call; the caller's `full_prompt`
   is appended as turn two (`:2422-2425`). "Forks are stateless by
   construction" (DEFAULTS.md:185) is true about *isolation between
   forks* and false about *what the model sees*. No maro prompt is
   turn-index-sensitive in the way the reviewer's synthetic repro
   assumes, so this is medium, not high — but the `no_tools` lane exists
   precisely because the CLI's conversational framing once caused a
   routing prompt to execute its goal (BACKLOG #16, `llm.py:2291-2298`),
   which is the same category of risk.

7. **[medium, CONFIRMED]** The map is written non-atomically, so a
   crash mid-write silently destroys every other shape's master.
   `_store_fork_master` holds `locked_write` but then calls
   `path.write_text` (`llm.py:2169-2180`), where the house pattern in
   this codebase is `atomic_write` **under** `locked_write` — compare
   `knowledge_web.py:719-727` and the temp-file/fsync/replace mechanism
   `file_lock.py:216-246` exists to provide. `write_text` truncates
   first: a process killed between truncate and write leaves an empty or
   partial file, the kernel releases the flock, and every later
   `_load_fork_masters` converts the parse failure to `{}` with no
   warning (`:2161-2166`). The next seed writes only its own key, so
   masters for all other shapes are dropped from the index and their
   still-resumable sessions are orphaned. The same swallow makes the
   milder case — an unlocked read (`:2208`) landing inside the write
   window — cost a redundant $0.037 seed.

8. **[low, CONFIRMED]** The headline ratio does not transfer to the
   shipped shape. The benchmark's bare arm re-sends a 4,000-char
   preamble on every call that the fork arm sends once
   (`bench.py:21-24`); the shipped lane hoists **nothing** into the
   master — it seeds an 8-word throwaway and still sends the complete
   `full_prompt` on every forked call (`llm.py:2425`). The ~11.8k tokens
   of cache_creation the fork actually eliminates is a *fixed* per-call
   saving, so the "~5× lower cost" figure in DEFAULTS.md:185 holds only
   for calls as small as the benchmark's. For the large agentic prompts
   that dominate spend, the same absolute saving is a much smaller
   fraction. The mechanism claim survives; the multiplier should be
   restated as an absolute.

### Refuted

- **"Every fork creates durable session state, growing
  `~/.claude/projects` without bound"** — *rated high by **both** fork
  lenses, independently, each citing the CLI's session docs. Refuted by
  measurement; I am overruling the consensus.* A bare `claude -p` call
  already writes
  one session transcript per invocation, which is why the box sits at
  **607 MB / 5,433 session files with the lane OFF**. Fork files are not
  materially larger either: from the benchmark's own recorded session
  ids, fork transcripts are 28,117 and 28,726 bytes against bare
  transcripts at 27,305 and 31,034. The lane's marginal contribution is
  approximately zero. What survives is a documentation defect, not a
  resource one: `DEFAULTS.md:185` says "nothing written back", while
  `BACKLOG.md:58-59` — same commit — records residual (3), "fork session
  files accrete under `~/.claude/projects` — opt-in cleanup only, per
  data retention." The BACKLOG line is the accurate one. Downgraded to
  **low, doc accuracy**.

- **"The retry-bare path can strip the wrong `--resume` pair."**
  `fresh_cmd.index("--resume")` (`llm.py:2460`) finds the first match,
  and both the fork id and an executor resume id are appended with that
  flag. But they are mutually exclusive by construction: `_resume_id` is
  assigned only inside `if _session_active:` (`llm.py:2344-2384`) and
  the fork lane is gated on `not _session_active` (`:2418`). Only one
  `--resume` can ever be present. Not a bug.

- **(mine)** I expected fork sessions to embed a copy of the master
  transcript and multiply per-call disk cost. The size measurements
  above killed it before it was written up.

---

## What went well

- **The `no_tools` security boundary survived.** `--tools ""` *is*
  carried into the seed command (`llm.py:2214-2216`), so the lane cannot
  hand real tool access to a classification call. BACKLOG #16 is the
  reason that flag exists, and it was the first thing worth checking in
  `d7a193d`. It holds. (The agentic branch of the same seed is a
  different story — findings 1 and 3 — but the utility lane, which is
  where the documented incident happened, is clean.)
- **The demotion route is honestly scoped.** The flat ledger,
  `query_lessons`, and extraction genuinely are untouched, and
  `test_flat_query_surface_untouched` pins it. The decree was
  implemented as stated; the defects are all at seams the decree didn't
  reach.
- **`d7a193d` was measured before it was built,** and the benchmark
  artifact was kept. Finding 1's cost figure and the refutation of the
  disk-growth claim both came *out of the author's own results.jsonl* —
  the review was cheaper and sharper because that file exists.
- **Both commits ship a killswitch and a default-OFF/opt-in posture**
  where the blast radius warranted it.

---

## Lead judgment

Accepting all six Part 1 findings and all eight Part 2 findings as
written. Adjudications worth recording:

- **Part 1 finding 1 is the one to fix first.** It is live, it is
  costing two-thirds of the goalless MEDIUM surface right now, and the
  fix is a one-line reordering (filter inside the load, or over-fetch
  then filter then slice). Everything else in Part 1 can wait a day;
  this is degrading decisions today.
- **Part 1 findings 1 and 4 are the same root cause** — demotion
  changes no score — and I would not patch them independently. The
  surface-scoping decree is sound as a *policy* about which surfaces a
  decision-replay Δ licenses; it is being used as an *implementation*
  constraint it was never meant to be. Worth putting back to Jeremy as
  a decision rather than absorbing as two patches.
- **Part 1 finding 2 should be fixed as a class, not an instance.**
  `promote_lesson` checks four guards outside the lock; Δ-demotion just
  made the window wide enough to hit. Re-validating inside `_pop` fixes
  all four at once. The reinforce path already learned this lesson on
  2026-08-04 and the fix wasn't propagated — that is the
  `fix-root-causes` "check for the pattern, not just the instance" miss.
- **Part 2 finding 1 is the flip blocker and the only finding here I
  would call urgent even at default-OFF.** Everything else costs money
  or latency; this one silently removes a control that
  `SECURITY_MODEL.md` names and `DEFAULTS.md` claims is enforced. The
  fix is small — route the seed through `_run_subprocess_safe`, which
  simultaneously closes findings 2 and 3 — and the durable fix is a test
  asserting the *seam* sets `MARO_WORKER_RUN`, since the existing
  tripwire only tests the hook. This is the third time in this project's
  history that a new code path skipped an established trust boundary in
  a module that already had one (C3's `_git_hard`, C4's stale-clone
  reintroduction, now this). The rule that came out of C4 — *when adding
  code to a module with an established trust boundary, apply it* —
  applies verbatim.
- **Rejecting both fork lenses' disk-growth high** on measurement, as
  recorded above. Worth flagging as a review-process note: two
  independent reviewers, on the same lens-family, both rated it high and
  both cited real CLI documentation, and the consensus was still wrong
  on magnitude. Reviewer agreement is not evidence when both reviewers
  reason from the same unmeasured premise; the three commands that
  settled it (`stat` on the benchmark's own recorded session ids) were
  available to them and to me.
- **Part 2 findings 2, 3 and 7 are all one fix.** Seed through
  `_run_subprocess_safe` with the real call's flag set and
  `atomic_write` the map. Fixing them separately would be three patches
  for one root cause: the seed was written as a special case instead of
  reusing the adapter's own spawn path.
- **Finding 5 (no master invalidation) is the one that will bite
  quietly** — a stale CLAUDE.md replayed into every stateless call
  produces no error, no log line, and no wrong-looking output. It should
  earn a TTL or a context hash before the flip, not after an incident.
- **The `no_tools` check is the finding I most wanted to be wrong
  about,** and I verified it twice for that reason. `--tools ""` is
  carried into the seed; that boundary holds.

---

*Method note: 4 reviewers, `codex exec -s read-only`, one lens ×
one commit each, prompts assembled from
`~/.claude/skills/adversarial-review` (Skeptic lens + a Failure-Operator
bonus persona) with the governing principle files inlined. Findings 1
and 2 of Part 1 were confirmed by executing the shipped code against the
live workspace on the maro box; Part 2 findings 1 and 7 and both Part 2
refutations were confirmed against
`~/.maro/workspace/output/claude-fork-bench/`.*

---

## Fix pass (same day, verify-before-fix)

Load-bearing claims spot-verified against source before acting (worker
marker seam, pre-push gate, `atomic_write` existence, load-slice order,
`_match_rate` None handling): all TRUE. Fixes landed same day:

**Part 1 (demotion):**
- F1 slot-eating — FIXED: both `inject_tiered_lessons` branches now
  filter before the pool slice (excluded rows yield their slots). Pin:
  `test_demoted_rows_do_not_eat_injection_slots` (4 rows, top-2 demoted,
  healthy rows fill the block).
- F2 TOCTOU — FIXED as a class: all boundary guards re-validated on the
  fresh row inside the locked pop, in `promote_lesson` AND
  `promote_lesson_by_effect` (the effect route deliberately omits the
  demote flag from its in-lock set — its own fresh evidence is the
  replacement). Pin: `test_promote_revalidates_guards_under_lock`
  (stale-snapshot shape reproduced via a first-read-only stale loader).
- F3 error-manufactured Δ — FIXED both routes: `replay_errors != 0`
  refuses to act (demote AND promote), and the stamp persists
  `replay_errors` for audit. The three live stamps were re-verified
  against the recorded census rows (0 errors each) and re-stamped with
  the audit field — no re-measurement needed; the record answers the
  question the stamp couldn't.
- F5 no audit row — FIXED: `LESSON_DELTA_DEMOTED` captain's-log event
  (registry + contract doc + census tests updated); the re-stamps above
  are its first three live rows.
- F6 unreachable eligibility — FIXED: explicit `--lesson-id` naming now
  measures any row (provisional/quarantined/contested excluded only from
  the unnamed sweep); measurement is evidence-gathering, the act routes
  keep their guards.
- F4 + the findings-1/4 root cause (no-score-mutation ⇒ slot-pressure or
  decay-erasure) — **DEFERRED TO JEREMY** per the lead judgment: the
  surface-scoping decree is being used as an implementation constraint it
  wasn't meant to be; that is a policy question, not a patch. F1's fix
  removes the live bleeding; F4 (GC in ~9–10 days erases the stamp,
  re-mint returns clean) remains open until the decree question is
  answered.

**Part 2 (fork lane):**
- F1+F2+F3 — FIXED with one reshape, as the judgment prescribed: the
  seed now rides `_run_subprocess_safe` with the CALLER'S exact flag set
  (worker push-guard marker, runaway breakers, process-group kill,
  identical tool contract → stable prefix). Seam pin:
  `test_seed_inherits_callers_exact_flagset` asserts seed cmd ==
  real cmd minus resume flags. Residual: seed usage is logged
  (cost in the log line) but not yet in the metrics ledger.
- F2 (retry storm) — FIXED: failed seeds negative-cached 15 min
  (`_FORK_SEED_RETRY_S`).
- F4 straggler deletion — FIXED: invalidation is owner-checked
  (`stale_id` must match the stored master).
- F5 stale masters — FIXED: 1h TTL (`_FORK_MASTER_TTL_S`); legacy
  entries count as expired.
- F7 non-atomic map — FIXED: `atomic_write` under `locked_write`.
- F6/F8 + refuted-disk-growth doc drift — FIXED in the DEFAULTS row:
  savings restated as fixed ~12k tokens/call, "stateless" scoped to
  fork isolation, transcript accretion stated plainly.

The default-ON flip (Jeremy's decree, journal 8aa8463b) lands WITH this
fix pass — the review's flip-blocker (Part 2 F1) is closed by it.
