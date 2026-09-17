# Backlog — Completed Archive

## The run that ends a resume settles its source — SHIPPED 2026-09-17 (LoopsBench chunk 9)

**Found:** chunk-7 r1 Architect 3: the CLI proved-overwritten-or-consumed
after a done resume, but a library resume (`run_agent_loop(
resume_from_loop_id=)`) claimed its source in `_load_resume` and never
consumed it — a finished API resume left the source claimed-not-consumed,
so a later resume was refused as live/superseded (correct, but opaque, and
a different answer from the CLI's "already resumed as X"). Two entry
points, two endings for one fact.

**Doctrine:** consumption belongs to whoever makes the run's LAST status
decision, through ONE function (`checkpoint.settle_resume_source`): the
loop's own finalize for library callers (the end of Phase G, after the
merge-backs that can demote), the CLI for its lane (after its closure
verification, which can demote). A run that ends other than done keeps
its claim untouched: the claim IS the replay barrier. Consumption is a
compare-and-consume on the claim's nonce under the file lock — the
permit authorizes the mutation it represents.

**Shipped:**
- `checkpoint.ResumePermit` (source path, nonce, source_loop_id) replaces
  the (source, permit) tuple in `ctx.resume_claim_release`
  (`Optional[ResumePermit]`); `permit_of` builds it from a claimed object
  (None without a claim or a loop id); `finalize_refusal` releases
  through it.
- `checkpoint.source_is_settled` (re-read of the PINNED canonical path —
  never re-resolved, a symlink there is refused: the complete successor,
  or the consumed source naming this successor; anything else False),
  `consume_claimed` (under `locked_write(require=True)`: the file must
  record the permit's loop AND a claim with the permit's nonce, and not
  be consumed by another successor; checkpoint-module `atomic_write`, dir
  fsync, read-back) and `settle_resume_source` (settled → True; else
  consume, then re-prove — a consume that reports success without a
  record is not trusted).
- `loop_finalize.settle_resume_claim` at the LAST status decision of
  Phase G (after both merge-back blocks; a demotion stamps
  `external-interrupt` like they do), on the parallel lane's result in
  `agent_loop`, and after an auto-recovery child returns
  (`successor_loop_id` = the child that finished the work). Deferred by
  `LoopContext.defer_resume_settlement` (new `run_agent_loop` kwarg,
  threaded through `loop_init`).
- `cli._cmd_resume` passes `defer_resume_settlement=True`, takes the
  permit right after claiming (canonical path, before admission nulls the
  object's permit) and settles with `settle_resume_source` right AFTER
  `_closure_verdict_pass` and BEFORE deferred learning; not settled →
  `incomplete` with `RESUME_UNSETTLED_REASON` + `external-interrupt`
  stamped; the run's close status stays `error` until the settlement
  decides. The CLI never consumes an alias.
- `_load_resume` refuses a dry-run resume before any lookup or claim.

**Review:** r1 Skeptic + Architect (7 + 7): the settlement sat at the HEAD of Phase G (consumed sources for runs the merge-backs then demoted), the CLI's verify-only path could not tell an overwritten source from a consumed one, the (source, permit) tuple was untyped, a dry run could claim and consume a real checkpoint — all fixed in the design above. r2 Skeptic on the fix diff (5 HIGH + 2 MED): cheap fixes landed — the CLI settles right after closure and BEFORE deferred learning, `_status` stays `error` until the settlement decides (an interrupt in between never closes the run as done), a CLI demotion stamps `external-interrupt`; `consume_claimed` locks with `require=True` (the fail-open escape hatch never applies to the compare-and-consume); the permit's canonical path is PINNED at admission — never re-resolved, a symlink appearing there is refused by proof and consume alike; four must-detects added. Design residue queued (below). STOP RULE: no r3.

**Residue (BACKLOG, chunk-7 residue list):** a HANDLE resume overwrites
its source with the successor's checkpoints before closure can demote
(pre-existing chunk-7 shape; lead: provisional successor address until
closure accepts); Phase G's artifacts / ledger / learning record `done`
before the settlement can demote it (merge-back precedent; lead: a
structural Phase-G split — gates, then settlement, then status-bearing
records); the auto-recovery claim names the parent while settlement
names the child (lead: transfer the claim to the child before recovery
starts); the loop's checkpoint writer is still outside the per-file lock
claim / release / consume share (identity-level admission ledger).

Tests: tests/test_resume_settlement.py (13) + updates in
test_resume_claim.py, test_resume_lookup.py, test_stranded_sweep.py.

## A NEXT.md mark a row still owes is durable state, settled from the checkpoint — SHIPPED 2026-09-17 (LoopsBench chunk 8)

**Found:** chunk-5 r2 lead: a `mark_item` that failed at step time (NEXT.md
locked) was retried only by the parallel lane's next snapshot and never by
the sequential lane, and nothing durable recorded it — a crash in between
left the checkpoint saying done while NEXT.md said TODO, so NEXT.md-driven
work (heartbeat drain, a later run over the project) could execute the item
again. Review r1 widened the class: producers that never marked (the
milestone-advisor `skipped` row), marked and swallowed the failure (the
parallel-batch branch) or could not record the result (the blocked
"advance" flow) all reported the mirror as up to date by default; the first
cut's verify-then-mark read the ledger outside the lock the rewrite took
(a check that proved nothing), and read an unreadable ledger as drift.

**Doctrine:** the checkpoint is the authoritative execution record and
NEXT.md its mirror; the mirror catches up FROM the record, never the other
way round; a mirror whose identity drifted (ledger edited) is surfaced, not
blindly marked.

**Shipped:**
- `StepOutcome.item_mark` / `CompletedStep.item_mark` ∈ {`applied`,
  `pending`, `drifted`, `attempt`} (loop_types, checkpoint). A terminal row (done /
  blocked / skipped) on a real item is BORN `pending` unless its producer
  records the applied mark (`_process_done_step`, the terminal blocked
  mark in `loop_blocked`, the gate / stuck / batch sites) — a producer
  that records nothing yields one redundant idempotent mark, never a lie;
  attempt rows (retry / re-decompose / split / stuck-advisor) are born
  `attempt` — not the item's verdict: they owe nothing AND supersede
  nothing (r3: born `applied` they counted as the item's latest row and
  erased a carried pending row's debt without a mark). On load only the
  literal strings `pending` / `drifted` / `attempt` survive (absent, bool,
  anything else → `applied`). `export_human` renders both owed states.
- ONE settler, `loop_planning.settle_item_marks`: blocked → `!`, done /
  skipped → `x`; the LATEST VERDICT row per item carries the obligation
  (an earlier pending row is superseded; `attempt` rows are skipped when
  finding the latest). Every settlement is a
  COMPARE-AND-MARK: `orch_items.mark_item(expected_text=)` checks under
  the ledger lock that the item exists, still names this text
  (`normalize_item_text`: first physical line, `[boundary]` removed,
  whitespace collapsed; the settler strips `[resume note: …]` prefixes
  first) and that the text is unique — `ItemIdentityError` (a ValueError)
  otherwise, nothing written. Identity failure → `drifted` (WARNING +
  DECISIONS.md line, never retried); any other failure → stays `pending`.
- Retry / settle sites: before every sequential snapshot
  (`_write_iteration_artifacts`), at every parallel snapshot for carried
  rows (the lane's own nodes are `_mark_node`'s, and their rows carry
  `_row_mark` = applied ⇔ the lane recorded that very state, in the file
  AND the returned rows, refreshed after the final pass — this hop's rows
  only, r3: the carried prefix was being re-indexed by node), at loop exit
  (`_execute_main_loop` settles and writes the checkpoint once more when
  anything is still owed OR a row was appended after the last snapshot —
  a `continue` after the last step never reached a snapshot; r3: a retry
  cut short by `max_iterations` owes nothing and was lost), and by the resume in `_preflight_checks` BEFORE any step
  executes (foreign carried rows → `drifted`; a settler failure is a
  warning, never a refusal).

**Review:** r1 Skeptic+Architect (10 findings: 4 HIGH — parallel result
rows disagreed with the file; unreadable ledger read as drift; verify-then-
mark race; producers born "applied" — all fixed as ONE design change:
tri-state + compare-and-mark + born-pending) → r2 Skeptic on the fix diff
(6: one REGRESSION — born-pending attempt rows re-marked a finished item
blocked → attempt rows born applied + latest-row coalescing; skipped last
step never snapshotted → loop-exit flush; `[boundary]` false drift →
identity normalization; parallel final-pass retry; terminal blocked mark
recorded; foreign carried rows drifted) → r3 Skeptic (regression check:
1 HIGH — attempt rows born `applied` superseded a carried pending row /
were lost at a max-iteration cut → 4th state `attempt` + verdict-only
coalescing + exit flush on unsnapshotted rows; 2 MED — parallel final
refresh re-indexed the carried prefix → fixed; lossy identity
normalization → residue) → STOP RULE, no round 4. Suite 83 green.

**Residue (BACKLOG, under the chunk-5 lead):** lossy identity
normalization (shared first line / `[boundary]` twin → false `drifted`);
max-iteration termination reaches no verdict (NEXT.md keeps DOING); the
milestone-advisor REPHRASE (c) executes a text the ledger never held;
step-time marks still trust the item index; the dead parallel-batch
branch; `append_next_items` multi-line numbering; the stuck path's
post-break checkpoint.

Tests: tests/test_item_mark_debt.py (34) + tests/test_parallel_checkpoint.py.

## A resume claims its source checkpoint before it executes — SHIPPED 2026-09-17 (LoopsBench chunk 7)

**Found:** chunk-6 r3 finding 2: after a `done` resume whose consumption
FAILED (disk full / EACCES / lost run-dir context) the run was demoted to
`incomplete`, but the source checkpoint stayed intact, unconsumed and
resumable AGAIN — a second `maro resume` replayed steps whose external
effects had already happened. Nothing durable said "someone already ran
this". The review rounds widened the class: the API path
(`run_agent_loop(resume_from_loop_id=)`) never recorded anything;
`branch_checkpoint` copied a mid-resume source into a fresh replayable
file; a handed-in `resume_checkpoint=` object was trusted as a snapshot.

**Fix:** `Checkpoint.resume_claim = {handle_id, pid, claimed_at, token,
nonce, successor_loop_id, successor_path?}` written into the EXACT
selected source by `checkpoint.mark_checkpoint_claimed(loop_id, path=,
handle_id=, expected=<the admitted CheckpointLookup>, successor_loop_id=,
successor_path=, reclaim=)` — a compare-and-swap under the per-file lock
(`file_lock.locked_rmw`) keyed on the sha256 DIGEST of the bytes admitted
(`CheckpointLookup.digest`; semantic equality was wrong — a file with no
`timestamp` parses as "now" every read), refusing consumed / complete /
claim-gated files, validating the proposed claim before any write,
fsyncing the directory entry, and reading the file BACK; it returns the
read-back object carrying two transient fields (`resume_permit` = the
nonce, `resume_source` = the path) that make the claim read as OURS —
never the pid. `resume_claim_status(ckpt) -> (state, detail)`: None (no
claim / consumed / our permit), `live` (claimant alive by pid + start
token), `superseded` (a successor checkpoint naming another loop exists at
`run_dir(handle)/build/checkpoint.json`, `ckpt_<successor_loop_id>.json`
or the recorded `successor_path` — read with the successor's identity),
`unresolved` (claimant dead, every successor address PROVEN absent),
`indeterminate` (a successor address unreadable / mismatched — no override
opens it). Consumers: `maro resume` refuses all four, `--reclaim` (new
flag) overrides only `unresolved`; the loader `_load_resume` is the policy
boundary for the API path too — it admits under the CLI's pidfile lock
(`checkpoint.resume_lock_name`), probes the source's own owner (run lease,
then in-flight pid), claims after EVERY refusal check (never for a
complete file), and for a preloaded object REQUIRES the permit (taken
atomically, one-shot), re-reads the exact source once and requires the
on-disk nonce to match; `branch_checkpoint` refuses a claimed source; the
heartbeat skips `live`/`superseded` and SURFACES `unresolved`/
`indeterminate` rows (legacy sources too, with `claim_state`,
`claim_handle`, `finalized_status`) and its `stranded_run` notification
names the recovery (`--reclaim` or repair). A refusal before the first
step releases the claim: `finalize_refusal` → `release_checkpoint_claim`
(locked, nonce-checked, never rewrites a file that no longer carries our
claim), `run_agent_loop` releases on an `_initialize_loop` early return or
exception, and the CLI builds the adapter BEFORE claiming so nothing
fallible sits between the claim and the loop. `run_agent_loop(loop_id=)`
lets the CLI pre-mint the successor loop id the claim names (grammar
`checkpoint.ID_REF_RE`, which the CLI's ref grammar now aliases). A
present-but-malformed `resume_claim` makes the file LOOKUP_INVALID, not
"unclaimed". Consumed dominates: a consumed file's claim gates nothing.

**Tests:** `tests/test_resume_claim.py` (claim on disk before the loop,
durable, permit one-shot; unwritable claim → nothing ran; live claim
refuses at CLI/API/heartbeat and the same pid is NOT our own; dead
claimant + successor → superseded, `--reclaim` does not apply; dead
claimant + proven-absent successor → unresolved, `--reclaim` re-claims;
unreadable / EACCES / third-loop successor → indeterminate; pid-reuse
token; malformed claims incl. path-escape handles → INVALID; CAS by
bytes incl. the legacy no-timestamp file and a pre-write malformed handle;
refusal-before-first-step releases on API (project mismatch never claims;
restore failure releases) and CLI (init early return, init exception,
adapter failure, release failure keeps the claim); a permitted object
admits exactly one run; two threads cannot both take one permit; API
admission under a held lock refuses; API refuses while the source loop is
alive; API claim names the ambient run dir's checkpoint address; heartbeat
surfaces unresolved / legacy / finalized rows; branching a claimed source
refuses; `--reclaim` parsed) + `tests/test_resume_lookup.py` (the chunk-6
write-failure test splits "claim fails → nothing ran" from "claim lands,
later writes fail → demoted, claim kept, source refuses again"; the
preloaded test claims first and pins ONE exact-source re-read) +
`tests/test_plan_node_ids.py` unchanged (complete-file API resume runs
nothing, cross-project refusal leaves no claim).

**r1 (Skeptic + Architect, codex gpt-5.6-sol):** 21 findings → 9 classes
fixed (CAS + read-back, permit-not-pid, indeterminate successor, strict
claim parse + handle grammar, API path claims + branch refusal, successor
at its id address, heartbeat surfaces unresolved, exact-path test spy).
**r2 (Skeptic, fix diff):** 10 findings — one REGRESSION of the r1 fix
(semantic CAS refused legacy no-timestamp files) → digest CAS; permit
required + one-shot on the preloaded path; API admission lock; claim
validated before write; claim after project check; id-address successor
identity; release on pre-step refusal; heartbeat legacy rows; pre-minted
id grammar. **r3 (Skeptic, fix-2 diff):** 9 findings → all cheap, landed
(atomic permit take; digest between the CLI's two reads; adapter before
claim; API owner probe; locked RMW for claim + release; init-exception
release; `successor_path`; heartbeat reads metadata for claimed rows;
"no second read" contract corrected). STOP RULE: no round 4; design
residue queued in BACKLOG under the chunk-3 bullets.

## Explicit resume is fail-closed end to end — SHIPPED 2026-09-16 (LoopsBench chunk 6)

**Found:** three chunk-3 leads (r2 findings 1 and 3, the class): (1) a torn
`<run-dir>/build/checkpoint.json` carries its loop_id INSIDE the JSON, so
the run-dir scan could not attribute it and `_load_resume` read it as
ABSENT → started fresh and replayed every step's side effects; (2) `maro
resume` validated one file under its admission lock, then the loop
RE-READ the id (a torn write in between took the fresh branch); (3) both
pre-execution refusals (cost gate, resume refusal) returned after
`_initialize_loop` acquired the project slot, run lease, running marker,
busy-policy worktree and container scratch clone, and relied on
destructors to release them.

**Fix (doctrine: explicit resume may start fresh ONLY on ABSENT — unknown
is not absent; a refused run ends like a finished run):**
`checkpoint.find_checkpoint(loop_id) -> CheckpointLookup(state, ckpt,
path, detail)` is the ONE loader, states `found / absent / invalid /
mismatch / io_error`. The id-addressed file decides on its own (torn →
invalid, unreadable → io_error, names another loop → mismatch — never
"absent, keep looking"); a run-dir file that parses and names another loop
is not ours; a DAMAGED run-dir file is ours when its run dir's
`metadata.json` names this loop (`loop_ids` / `loop_id`, schema-checked),
not ours when the metadata names only others, and UNATTRIBUTABLE when the
metadata cannot say — reported with its path and "cannot say" instead of
absent, because it cannot rule this loop out. The lookup calls the raw
`runs` helpers inside its own try (any exception → io_error) and lists run
dirs with `os.scandir` (an unreadable runs root raises; `Path.glob`
swallowed it). `load_checkpoint` is the lossy wrapper. `Checkpoint.from_dict`
requires a non-empty string `loop_id` and a string `goal` (an empty id used
to resume as loop '' → fresh). `_load_resume(ctx, id, *, preloaded=None)`:
FOUND proceeds, ABSENT starts fresh, everything else refuses with the
detail; `preloaded` is the `Checkpoint` the CLI validated under its lock,
used as-is (must be a `Checkpoint` naming the requested id), and
`run_agent_loop(resume_checkpoint=)` always routes it through the resume
load. CLI: `_lookup_resume_checkpoint(ref)` reads the HANDLE-addressed
file first (it decides when it exists; an embedded `handle_id` that differs
from the ref is MISMATCH), only ABSENT falls through to the loop-id
lookup; `_load_resume_checkpoint(ref)` stays the lossy seam (Checkpoint or
None) and `_cmd_resume` names the damaged file on None; after the re-read
under the lock the lock identity is recomputed and a change refuses.
`loop_finalize.release_loop_resources(ctx)` (slot → lease → running
marker) is the ONE release path — `_finalize_loop`, `finalize_refusal`
and the fence refusal all use it; `finalize_refusal(ctx, result)` copies
the typed stop verdict onto the result, stamps it into run metadata,
discards the scratch clone then the busy-policy worktree (nothing to
merge), releases, wakes the heartbeat. `_refuse_resume` stamps
`external-interrupt`, the cost gate `out-of-budget` (was unstamped).
Side-find: `runs.open_run` pins the run-dir contextvar and `_cmd_run` /
`_cmd_resume` then wrapped the loop in `scoped_run_dir(_rd)`, which
restores what was current at ITS entry — the pin outlived the command;
both unpin to the prior value right after `open_run`.

**Tests** `tests/test_resume_lookup.py` (new): the five lookup states of
the id-addressed file (torn / other loop / dir-as-file) and the lossy
wrapper's contract; run-dir attribution (ours / theirs / unattributable /
found); a raising lookup is io_error; an unattributable torn run-dir file
refuses an explicit resume through the REAL loop, attributed-to-other
starts fresh; a preloaded checkpoint is used without a second read (and a
wrong-id / empty-id / non-Checkpoint hand-over is refused); JSON-valid
non-checkpoints and invalid UTF-8 are INVALID; malformed metadata shapes
cannot attribute; an unreadable runs root is io_error; `maro resume
<handle>` through the real parser succeeds when an unrelated run dir holds
unattributable damage (and the run-dir scope does not leak), and refuses a
handle file embedding another handle; the lock-identity re-read refusal
releases the lock; the CLI names the damaged file and hands the object
over; a refusal releases `clone → worktree(keep_on_failure=False) → slot →
lease → running` with the verdict stamped; the cost gate through the real
loop clears the marker and stamps `out-of-budget`; the fence refusal goes
through the shared helper.

**Round-1 review (4 lenses, gpt-5.6-sol) → fixed in the same chunk:** (A)
the CLI seam's return type change broke two `test_stranded_sweep` tests —
seam restored, lookup alongside; (B) empty / non-string `loop_id` was
FOUND and the loop's truthiness gate skipped the resume — strict
`from_dict`, unconditional resume load, type-checked hand-over; (C) a
string `loop_ids` iterated char by char attributed the damage to loops
"a","b",… — schema check; (D) lossy `_runs_root` / `_rundir_checkpoint_path`
and `Path.glob` read an unreadable runs root as absent — raw helpers +
`os.scandir`; (E) loop-id-first resolution let unrelated unattributable
damage shadow a valid handle file, and the handle read trusted the
embedded `handle_id` — handle-first + mismatch; (F) the locked re-read
could change lock identity — recomputed and refused; (G) refusals leaked
the container scratch clone; the fence refusal had its own release trio —
clone cleanup in `finalize_refusal`, fence refusal through the shared
helper; (H) invalid UTF-8 escaped the classifier as io_error — decoded
inside the parse try; release failures now log at warning. Declined by
doctrine: garbage `completed` rows stay dropped-not-refused (chunk-2
decision, pinned by `test_from_dict_negative_controls_drop_or_demote_garbage`).

**Round-2 Skeptic on the fix → round 3 (the fix regressed / missed):** (1)
`find_checkpoint` still existence-checked id addresses with `Path.exists()`
and filtered run-dir candidates with `is_file()` — both swallow EACCES and
dangling links into False → absent → fresh — every address is now READ
(no preflight; a dangling link at a checkpoint address is io_error; the
scan `stat()`s each candidate and only "nothing lexists" skips); (2) the
lock re-read compared identity only — a same-identity snapshot with FEWER
finished positions (stale writer) was handed to the loop — now refused
("lost finished positions [..]"), as is a different source path; (3) a
successful handle resume was never consumed nor proven (checkpoint writes
swallow failures, so a failed final write reported `done` with the source
still resumable) — the source path is re-read after `done`: proven only if
it now holds the complete successor, else consumed in place via the new
`mark_checkpoint_consumed(path=)`, else demoted to `incomplete`; (4) the
handle-first resolution interpolated the raw CLI ref into `runs.run_dir`
(`/tmp/owned`, `../victim`, `a/b` escaped the runs root) — ref grammar
`[A-Za-z0-9][A-Za-z0-9._-]{0,127}` validated first; (5) an existing run
dir with no checkpoint fell through to the loop-id scan and was shadowed
by unrelated damage — an existing run dir decides (ABSENT for that
handle); (6) the restored lossy seam made `_cmd_resume` look up TWICE on
failure, so the reported path need not be the observation that refused —
one typed lookup per read (the two seam-patching tests now patch the typed
resolver with real `Checkpoint`s); (7) the fence refusal still had its own
ending and never woke the heartbeat — it now stamps and returns through
`finalize_refusal`. Eight more tests (28 total in the file).

**Round-3 Skeptic on the round-2 fix → cheap fixes landed, design residue
queued (stop rule: 3 rounds is the budget):** (1) the re-read guard
compared only finished positions — an older between-step snapshot that
dropped the in-flight marker (or the plan text, or a row's identity)
passed — now the two pre-execution snapshots must be IDENTICAL
(`to_dict()` equality, after the completed/consumed messages); (3)
consumption was policy only in the CLI — `_load_resume` (the API path)
now refuses a consumed checkpoint, `branch_checkpoint` refuses to branch
one, the heartbeat's resumable-run scan skips them; (4) `path=`
consumption replaced a symlink instead of its target — `realpath` first;
(5) `lexists` on the final path missed a dangling ANCESTOR (`build ->
missing`), and `root.is_dir()` / `_old_checkpoint_dirs`'s `is_dir()`
swallowed EACCES — `_classify_missing` walks the address top-down (first
un-stat-able component: exists → dangling → io_error, else absent), the
runs root is `os.stat`'ed, the old root is read unfiltered, the CLI's
handle-dir probe uses the same classifier; (6) handles and loop ids come
from ONE 8-hex generator — a ref that is both a run handle and another
loop's id with its own file is refused as ambiguous (hint: the loop's
own handle), an empty handle dir no longer hides a loop file or a damaged
id-addressed file. Declined / queued: (2) a failed consumption after a
`done` run demotes the status but leaves the source replayable (a durable
resume claim BEFORE execution is the design — BACKLOG); (7) the proof
re-read and the consumption run outside any lock shared with checkpoint
writers (the chunk-3 "consumption can race a late writer" lead, still
queued). Four more tests (32 in the file). Suites 73/74/75 green.

## Parallel lanes write the checkpoint — SHIPPED 2026-09-16 (LoopsBench chunk 5)

**Found:** chunk-2 r1 finding 8 / chunk-3 re-examination: the sequential
loop's parallel-batch branch is unreachable in production and the DAG /
fan-out lane (`loop_parallel._run_parallel_path`, Phase D, returns before
Phase E/F) wrote NO checkpoint — a crash mid-DAG resumed as nothing done and
re-ran every finished node — and marked NEXT.md items only at the very end
(a crash left every finished node TODO). Blocked on durable plan-node ids
(chunk 4) until today.

**Fix (doctrine: every finished node is durable, in every lane; one loader
resumes both lanes' files):** `_run_steps_dag` routes every row commit
(done, execution error, timeout, not-started, pre-gated, gated dependent)
through one `_commit(step_idx, outcome)` under `results_lock`, which calls
`on_progress(snapshot)` in the same critical section (two workers finishing
together cannot race an older snapshot over a newer one);
`_run_steps_parallel` calls `on_progress` on its coordinator thread per
landed outcome. `_run_parallel_path(carried_outcomes=)` supplies
`_write_progress`: marks each newly committed node's item once
(`_mark_node`) and writes `write_checkpoint(TAGGED plan, carried rows + one
`_fanout_row` per committed node, world_facts, regression,
step_indices=items, plan_items=ctx.plan_items)`, plus a final write with
every returned row. The plan is the tagged text because a resume re-parses
`[after:N]` from it and verifies carried items against NEXT.md (which
mirrors the tagged text). Carried rows now lead the lane's LoopResult too.
Written only when the lane runs on real items; direct callers without
`step_indices` keep the old behaviour (positional rows, no write).
`agent_loop` passes `_resume_completed` into the lane.

**Tests** `tests/test_parallel_checkpoint.py`: fresh DAG whose worker for D
dies with a BaseException after A/B/C committed → three growing writes,
the file resumes with only D remaining, A/B/C already marked, the resume
finishes every item and its checkpoint is complete; resumed DAG suffix
writes carry A's row in front with the suffix plan/items and the original
binding; `_commit` unit: every commit path reaches `on_progress`, snapshots
strictly grow, an `OSError` from the hook never fails the lane; fan-out
lane per-outcome progress; direct call without items writes nothing.

**Round-1 review (4 lenses, gpt-5.6-sol) → fixed in the same chunk:**
(A) a node's row was durable BEFORE its decisions / world facts /
regression obligations were recorded (the parallel path never harvested
regression at all) — `_node_effects(k, oc)` now runs ONCE per done node
before its row is written, direct callers included; (B) `_marked` was a
once-only set recorded before `mark_item` succeeded — now `Dict[int,str]`
= the last APPLIED NEXT state, so a failed mark is retried at the next
snapshot and a DAG timeout row later replaced by the worker's real result
moves the item `!` → `x`; (C) the status domain is closed at the lane
boundary (`_TERMINAL_STATUSES = {done, blocked, skipped}`,
`_normalize_outcome`: anything else → blocked with `stuck_reason
"unrecognized outcome status …"`; `skipped` finishes its position AND
marks its item done); (E) the fan-out lane's six result-mutation sites go
through one coordinator-side `_commit`, persistence latency is excluded
from the workers' deadline (`wait(FIRST_COMPLETED)` loop, deadline extended
by each landed outcome's processing time), and a late future that RAISED is
reconciled as `parallel execution error`, not left as a timeout; (F)
execution policy travels with the checkpoint — `Checkpoint.parallel_fan_out`
(persisted by every writer from the new `LoopContext.parallel_fan_out`),
restored by `cli._cmd_resume`, so `maro resume` of a DAG-written file
re-enters the DAG lane when the unfinished suffix still has a parallel
level (a pure chain runs sequentially, correctly); (H) the whole
`_write_progress` body incl. the final write and `_mark_node` is one
non-fatal boundary. Seven more tests (12 total): timeout-then-late-success
`['!','x']`; mark failure retried once; status domain; a committed node's
world fact in its own write and in `ctx.world_facts` before the crash;
marker RuntimeError on the final write contained; fan-out slow hook does
not time out a finished peer + late raise → execution error; a REAL
`cli._cmd_resume` of a DAG-written file (crash on D, suffix D..G with a
parallel level) re-enters `_run_steps_dag` with exactly the four
unfinished nodes and marks their items (by loop id through
`cli._cmd_resume`; the parser / handle-id / `team:` origin is not on the
test path).

**Round-2 Skeptic on the fix → round 3 (the fix regressed two things):**
(3) the DAG worker's `_commit` now runs `_node_effects` on the completing
WORKER thread, so `record_step_decisions` mutated `loop_shared_ctx` while a
peer iterated it in `step_exec` context assembly ("dictionary changed size
during iteration" → the innocent peer ended as an execution-error row) —
fixed at the readers (`list(shared_ctx.items())`, one C-level copy under
the GIL; `WorldFactLedger` iterations likewise, since `observe()` can now
run on a lane thread); (4) the DAG timeout path's check-and-commit was two
lock sections after the `_commit` refactor, so a worker's real `done`
landing in the gap was overwritten by the synthetic `blocked` — fixed with
`_commit(..., only_if_absent=True)` (one critical section; the post-pool
fill uses it too); (2) `_effects_done` was acknowledged BEFORE the effects
ran, so a transient failure was never retried — acknowledged only after
every effect ran without raising; (6) a worker returning None hit
`AttributeError` in gating — `_contract_row` at the executor boundary in
both workers, both `_commit`s and `_normalize_outcome`; (5) at the fan-out
timeout a future that is already done is landed as itself (`_land`) before
any synthetic row; the deadline extension stays (bounded by #outcomes ×
hook time) and `_fanout_timeout` is advisory by construction — the pool's
exit waits for running workers. Three more tests (15 total): None-returning
worker → contract row in both lanes + its declared dependent gated; the
deterministic legal interleaving (scheduler lock wrapped so the worker's
`done` lands the first time the coordinator leaves the lock) → the row and
snapshot stay `done`; a transient world-fact recorder failure → recorded on
the retry.

**Round-3 Skeptic on the round-2 fix (last round):** (1, HIGH) the
status domain was closed AFTER scheduling — a `pending` prerequisite dict
was committed unchanged, the scheduler did not see it as UNMET, and its
declared dependent ran (the status-domain test faked the scheduler) —
fixed: `_normalize_outcome` IS the executor boundary in both workers
(non-dict and non-terminal alike) and at both `_commit`s; a real
`_run_steps_dag` test asserts the dependent's adapter is never called;
(2, HIGH) one live `loop_shared_ctx` iteration remained
(`team.firewall_shared_ctx`, reachable from a worker's team-worker
creation) — snapshotted; (3, MED) a whole-bundle effects retry re-appended
decisions to the append-only journal — per-family acknowledgement
(`decisions` / `facts` / `regression`), a family that ran is not re-run
when a later one fails; (4, MED) `_run_in_step_worktree` merges back
unconditionally, so a blocked (incl. contract) row's partial file changes
merge — pre-existing policy, BACKLOG. Two more tests (17 total).

**Residue → BACKLOG:** a failed NEXT.md mark followed by a crash before
the next snapshot leaves a done row with a TODO item (process-local retry
cannot cover it; a durable mark debt reconciled on resume is the lead —
same two-file shape as the sequential lane); decision-journal rows have
no idempotency key (a crash between a node's effects and its row, or any
re-run, appends duplicates — `(loop_id, item, ordinal/content hash)` is
the lead); worktree merge-back ignores the outcome status (a contract /
blocked row's partial changes merge); no in-flight marker in the parallel lanes (a
missing row re-runs — the safe direction); the dead sequential
parallel-batch branch; a hand-forged carried row whose item collides with
a suffix item promotes that suffix position (same shape in the sequential
lane — row provenance / persisted position is the fix); the run report's
"N/M done" counts carried rows against the suffix denominator (same shape
as sequential finalize); the two-file kill window between a NEXT.md mark
and the checkpoint `os.replace` (errs toward re-running).

This is the history of shipped items. When something gets completed in BACKLOG.md, it moves here with its context intact so we keep the "why" / "how" / "source" for future reference.

Live items are in [BACKLOG.md](BACKLOG.md). This file is ingested by the correspondence module so `dev-recall` can surface prior decisions, rejected approaches, and "already-tried" context during new work.

Last split: 2026-04-16 (session 34).

Rotation policy (2026-08-16): when this file outgrows whole-file readability (256KB Read limit), older records rotate verbatim to `docs/history/backlog-done-*.md` segments at a session boundary; recent records (roughly the last two weeks of ship dates) stay here. Segments so far: `backlog-done-2026-04-to-08-p{1,2,3}.md`.

---

## Durable plan-node ids — SHIPPED 2026-09-16 (LoopsBench chunk 4)

**Found:** the root residue every review this week converged on (chunk-1
r2 findings 6–7 `/tmp/adversarial-review.K4zIne`, chunk-1 r3 "DAG lane
schedules a resumed suffix by re-numbered tags", chunk-2 r2 duplicate-text /
permuted-mapping leads, chunk-3 "build the ids first"). A resumed suffix was
re-numbered from 1 while its `[after:N]` tags still named the original plan:
the gate degraded every declared edge to SOFT on any resume
(`plan_identity_intact(resumed=True)`), the DAG lane scheduled the suffix by
self-depending tags ("upstream dep did not complete"), and — found while
scoping — a resume RE-DECOMPOSED the goal (paid planner call, plan thrown
away), appended a second copy of the plan to `NEXT.md` and paired the suffix
with those fresh items by position, so the original items were never
marked.

**Fix (doctrine: a plan node IS its NEXT.md item; the original numbering is
bound to items once and travels with the checkpoint; identity uncertainty
degrades to SOFT, never refuses; only a checkpoint that NAMES another
project or cannot be read refuses):**
`Checkpoint.step_items` (item per plan step) + `Checkpoint.plan_items` (the
ORIGINAL number→item binding, verbatim, never recomputed), both through ONE
validator `checkpoint.validate_identity` at writer and loader (unique
non-negative ids, `-1` may repeat, bound step items in strictly increasing
plan order, else the offending list is dropped WHOLE; never manufactures
`-1`); `from_dict` raises on a non-string step so the resume refuses instead
of crashing after the restore. `loop_planning._load_resume` runs BEFORE
Phase B and owns the one carry decision: the remaining steps become the
preset plan (`preset_source="resume"`, an empty suffix plans nothing); a
checkpoint naming a different project refuses; each carried (item, text)
pair is verified against the current NEXT.md (`_items_name_these_steps`) and
the whole identity is dropped on any mismatch. `_mirror_plan_items` (one
boundary for every lane — extracted from `_prepare_execution`, called before
Phase D for the fan-out/DAG lanes) keeps the carried items or appends fresh
ones and sets `LoopContext.plan_items`; `_run_parallel_path(step_indices=)`
rows carry the item as their index and mark it done/blocked.
`step_gate.prerequisite_verdict(plan_items=)` resolves a tag's number to an
ITEM and reads its latest outcome, carried rows included; the implicit edge
is the original plan's {k-1}. `step_gate.remap_suffix_deps` re-keys a
suffix's edges to suffix positions (finished carried prerequisite =
satisfied; carried blocked/skipped = pre-gated when enforced) before
`build_execution_levels`, the `use_dag` decision and
`_run_steps_dag(declared=, pre_gated=)`. Every checkpoint writer passes the
binding. Deleted: `plan_number_of`, the `_preflight_checks` id-only loader
branch.

**Review:** round 1 four Codex lenses (`/tmp/adversarial-review.dMNbXh`) →
classes A (identity validated after the parallel lanes ran), B (validators
accepted duplicate/swapped/fractional identity → hard gate on an ambiguous
binding), D (line-offset ids), F (non-string step), G (dead compatibility
paths) fixed in one fix diff; C (unreadable run-dir checkpoint reads absent)
pinned out-of-scope (chunk-3 residue). Round 2 one Skeptic on the fix diff
(`/tmp/adversarial-review.CrC7H0`): 2 HIGH + 4 MED + 1 LOW, all verified,
5½ fixed in one small diff — a reordered binding survived validation (the
binding is monotone by construction: consecutive NEXT.md lines, so
`plan_items` must be strictly increasing); any negative other than -1 is
corruption; a duplicate task text at the original's line offset verified in
its place (duplicate texts now read as ambiguous → identity dropped);
`_mirror_plan_items` keeps a defensive project check for direct callers;
`maro resume` now prints `stuck_reason` (json field / text → stderr) with a
CLI refusal test; the gate-writer site is asserted through the gate flow
test. Pinned, not fixed: an unreadable NEXT.md still crashes fresh
mirroring after the loader degraded (pre-existing — `append_next_items`
reads utf-8 on every fresh run); the rotation writer site has no flow test.
No round 3 (nothing regressed). Tests `tests/test_plan_node_ids.py`:
loader must-detects (dup / swapped / fractional / bool / short + negative
controls), writer never manufactures `-1`, non-string steps refuse, verdict
through the binding with a fresh-run equivalence control, remap cases incl.
the self-dependence must-detect, DAG pre-gating, loop flows (resume keeps
NEXT.md items with the planner patched to raise; empty suffix; declared edge
ENFORCED after resume; DAG lane takes the remapped suffix and marks the
original items; cross-project refusal with fan-out 0 and 2; NEXT.md drift
degrades to fresh items; writer spy on the binding at every site; the real
`cli._cmd_resume` through the loop).

**Residue → BACKLOG:** NEXT.md ids are line offsets (immutable id lead);
reshaping after the DAG decision; DAG lane checkpoint write (now
unblocked); discriminated checkpoint loader; duplicate-text interrupt
re-pairing; pre-execution refusals bypass finalize (admission half);
unreadable NEXT.md crashes mirroring (typed mirroring failure lead).

## Checkpoint write torn-file window + fail-open resume — FIXED 2026-09-16 (LoopsBench chunk 3)

**Found:** chunk-1 QA round (write in place → a kill mid-write left a torn
file the resume path read as "no checkpoint"); chunk-2 / chunk-3 reviews
(an explicit resume of an unreadable or unrestorable checkpoint silently
started fresh = replayed every step's side effects).

**Fix:** `write_checkpoint` and `branch_checkpoint` write through
`file_lock.atomic_write` (mkstemp beside the target + fsync + os.replace;
process-kill safe, not power-loss durable, needs a writable parent dir; a
failed write is now a WARNING, still non-fatal). `loop_planning` fails an
explicit resume CLOSED (early-return `stuck` with the path) when the
checkpoint file exists but cannot be read, or loaded but its restore raised;
an absent checkpoint still starts fresh. Two Skeptic rounds (r2: fallback-file loop-id mismatch now refused, lookup errors refuse, refusal stamps a stop verdict + trace edge, warning names the path; structural leads → BACKLOG); tests in
`tests/test_checkpoint_atomic.py` (no temp files; simulated crash before the
rename keeps the previous checkpoint byte-identical with a negative control
on the attempted rename; mechanism pin on both writers; torn-file resume →
stuck with no step executed; absent → fresh; restore-raises → stuck).

**Residue → BACKLOG:** consumption racing a late writer; DAG/fan-out lane
writes no checkpoint (the sequential batch branch is unreachable); durable
plan-node ids.


## Checkpoint resume never skipped completed rows — FIXED 2026-09-16 (LoopsBench chunk 2)

**Found:** round-2 Skeptic of the LoopsBench chunk-1 diff
(`/tmp/adversarial-review.K4zIne`, finding 7), pre-existing HIGH. Every
checkpoint row stored `StepOutcome.index` — the NEXT.md item the loop
assigned — while `Checkpoint.remaining_steps` / `next_step_index` /
`export_human` read it as a 1-based plan position. Live checkpoints on this
box held `completed idx = [13, 49, 11, 12]` for 2–7-step plans, so
`resume_from` returned the WHOLE plan and a resumed run re-executed
finished steps (duplicate side effects), and the chunk-1 gate's blocked row
never made a resume skip anything.

**Fix (landed with this entry):** `CompletedStep.position` (1-based plan
position, 0 = not a plan step) written from the loop's own
`step_indices` mapping at all four writer sites; a checkpoint-level
`positioned` marker (serialized, carried by `branch_checkpoint`) so a
positioned file whose rows all sit at 0 is never read as legacy; a
positioned file finishes a position only when its LATEST row is
`done`/`skipped` — a blocked row never finishes one (retry-requeued,
superseded by sub-steps, or gate-refused: a resume is the operator's retry
and re-runs beats skips); `is_complete()` = nothing remains (a row count
over-reported after one resume and the CLI refused the second resume);
`done_count` for the progress surfaces; `from_dict` coerces persisted rows
(integral-only ints, non-dict rows dropped, string marker = legacy);
duplicate item ids in the mapping resolve to position 0; the interrupt
handler re-pairs text ↔ item index by text (a priority interrupt prepended
text but concatenated indices old+new, so the urgent step wore the next
planned step's item number). Legacy files (no marker) keep the pre-fix
reading exactly.

**Review:** two Skeptic rounds (r1 8 findings → 7 class fixes; r2 6
findings → 4 fixes + 2 direction decisions recorded in
`checkpoint._done_positions`'s docstring). No round 3: r2 surfaced no
regression in the r1 fix, only the direction dispute (decided: blocked
never finishes) and residue that belongs to durable plan-node ids.
Suites 58–60. Tests: `tests/test_checkpoint_resume_positions.py` (live
shape, legacy negative control, carried-only positioned file, blocked
never finishes, two-hop crash→resume→crash→resume through the loop, exact
text↔position pairing captured at the one writer all four sites alias,
gate-site capture, interrupt re-pairing, CLI `_cmd_resume` guard).

**Residue:** parallel-batch checkpoint boundary; duplicate step texts /
permuted mappings (durable plan-node ids); corrupt-resume fail-open policy;
`write_checkpoint` in-place write — all pinned in BACKLOG under the
LoopsBench chunk-1 residue section.


### SHIPPED 2026-09-07 — Live (in-step) ask for time-boxed inputs — a 2FA code dies with the step that asked for it (FOUND 2026-09-07, mail arc design)

The ask lane ends the step, the container dies, and the resume takes minutes (post-pause tail + admission + pre-flight). A 2FA code is consumed by the session that requested it, so a browser login cannot survive the pause: the resumed run gets a fresh challenge and a fresh code. Needed: a time-boxed in-step variant — the worker writes the ask, keeps its process alive and polls for an answer file in scratch; the engine watches the ask file mid-step, fires the same card + Hermes leg, and drops the operator's reply into scratch when it arrives (`maro answer` writes the file instead of enqueueing a resume when the asking step is still live). Jeremy 2026-09-07: not willing to tie his SMS number to the mini, willing to relay a code by hand — so the loop has to close inside the code's lifetime (~10 min). Design owed; the mail goal cannot finish without it (`docs/ENV_REQUEST_DESIGN.md` §8). **Live evidence 2026-09-07 04:18Z:** run 084d3c1f, on its self-built browser image, drove a real Playwright login and landed on Yahoo's challenge-selector page, then wrote the ask ("Yahoo 2FA code required") through the pause lane — the question is pending and any code relayed into it arrives at a dead session. This is now the only piece between the mail goal and delivery.

**Shipped 2026-09-07** with the ask-grounding gate (`docs/OPERATOR_ASK_DESIGN.md` §7–§8; decision c6a3bb47). Trigger: 084d3c1f's fourth question carried a dead link and no code had been sent; after Jeremy's "I never received a code" the run asked the identical question again.

**Telegram answer loop — the operator-question lane, SHIPPED both engines 2026-09-06.**
Decree `1d1ad8b0` (Jeremy): build it out, and keep it "the rare exception,
not the norm". Design: `docs/OPERATOR_ASK_DESIGN.md`. Python:
`src/operator_ask.py` — the execute frame names `$MARO_ASK`; a worker's
JSON ask file (question / why / no_input_alternative / tried) becomes the
typed pause `awaiting-clarification` with a 24 h time box
(`ask.timeout_hours`), an `operator_question` escalation-class event
(Telegram card + Hermes inbox), and `maro answer <handle> "<text>"`
resumes the run by handle through the continuation lane (RESUME, same
identity, answer in the ancestry context). Hermes gate verb `answer`
(`dispatch.py answer`, source `hermes-ssh`); inbox prompt + SKILL say
reply = `answer`, never a fresh dispatch. `maro asks [--sweep]` is the
ledger. Go: `question` / `answer` records, `needs answer:` terminal,
`maro-go answer` re-runs the goal `--after` the asked run with the answer
as `--context`, `maro-go asks`. First live firing owed (the mail re-ask).

*Original entry:* Hermes polls the bot; Maro's `telegram_listener.py` runs
nowhere; `dispatch.py` has no resume verb; a `clarification_needed`
returns the question and only a fresh dispatch follows. Needs: a
pending-question pause (`pause.*` family) with a time box, a resume path
keyed on the run, and the navigation class Jeremy named — wait for the
answer, or try a path that needs no input, and record which.

---

**`scripts/mutate.py` negative control — SHIPPED 2026-08-16.**
Filed and fixed the same day.

*Original entry: the runner had no negative control — a sweep where
pytest never RAN reported the same green as a clean one* (found
2026-08-16 by the Experimentalist lens reviewing the path_rewrite
sweep, reproduced twice). The runner treats "tests failed" as DETECTED,
so an environment where the test command fails for an unrelated reason
(no `PYTHONPATH`, no pytest on the chosen interpreter, a collection
error) marks every mutation detected and exits 0. That is the
guard-that-cannot-fail shape, one level up from the code it audits: the
instrument built to find false confidence can manufacture it. **Fix
shape:** run the test target ONCE unmutated before applying anything
and require it to pass — a sweep whose baseline is red has nothing to
say. Cheap, and it makes every "N/N accounted for" mean something. (The
path_rewrite sweeps are not in doubt: both rounds produced SURVIVED and
SKIP verdicts alongside the DETECTED ones, which is the discrimination
a broken runner cannot show. That is luck, not a design property.)

**Shipped as filed:** `_baseline_ok()` runs every distinct `tests` target
once unmutated before any mutation is applied and refuses the sweep if it
does not pass, printing pytest's last 15 lines so the reason is visible.
Two negative controls run against the fix itself — a nonexistent test
file, and a nodeid that doesn't resolve — both now abort with the reason
instead of reporting a clean sweep; before the fix each would have
reported 1/1 accounted for.

**The three existing green specs were re-run under the gate and all
hold**: `scope_14a` 35/35, `path_rewrite` 40/40, `dispatch_envelope`
20/20. So the greens on record were real, not artifacts — the concern
was right and the audit was worth doing anyway. Baseline cost is one
extra pytest run per distinct target.


**`tests/mutation/provenance_gate.json` — CLOSED 2026-08-16 at 19/19.**
Landed deliberately red at 6/18 the same day; closing it was the named
follow-up and it is now done.

*Original entry:*

Close `tests/mutation/provenance_gate.json` — the contamination
  gate's enforcement is largely unpinned.** First tier-1 sweep, run
  2026-08-16, **landed deliberately red**: 6 of 18 accounted for. The
  classifier's *behavior* is well covered against the four incident
  lessons; what is not covered is everything around it. Confirmed gaps:
  four of the five `_PROMPT_AUTHORITY_RE`/`_OBEDIENCE_RE` sub-patterns
  can be deleted with a green suite (the adverb slot, `forbids|prohibits`,
  the optional-article leg, and the pronoun-object requirement whose
  whole job is keeping domain facts like "rate limits are a hard
  constraint" clean), and two of three killswitch behaviors are unpinned
  — a quoted `"false"` reading as truthy, and a config error failing
  OPEN instead of closed. Four enforcement-site survivors are marked
  `unverified` in the spec, not claimed as holes: the one site that WAS
  probed (`promote_lesson`) turned out to have a redundant downstream
  guard, so the others need the same one-line probe before anyone writes
  a test for them. Do the probes first, then write only the tests that
  pin something real. **Do not delete survivors to make the run green** —
  the ledger is the deliverable.

### Goals cannot carry attachments — a screenshot is not an input (Jeremy, 2026-08-16: "a little concerning that maro can't ingest attachments")

**How it closed.** Probing the four `unverified` enforcement survivors
behaviourally split them two and two, which is exactly why they were
marked rather than reported. `promote_lesson_by_effect`'s pre-check and
`_post_reinforce_hooks`' screen are redundant — the in-lock `_guards` and
`promote_lesson()` respectively refuse the row, nothing observable
differs, both recorded equivalent with the probe. `search_graveyard`'s
screen is real and has NO downstream guard (`resurrect_archived_lesson`
carries no quarantine check). `run_decay_cycle`'s screen is real in a way
reading would have missed: the tier move is refused, but `promoted_ids`
feeds the returned count and the change_log audit entry, so dropping it
makes the cycle report promoting lessons it did not promote (probed:
reported 2, moved 0).

Pinning the live graveyard leg surfaced a mirror the first spec missed —
the archived scan screens separately — and one mutation of my own was
badly built: deleting the last line of the prompt-authority alternation
left a trailing `|`, giving the group an empty branch, so the mutation
WIDENED the regex and read as a survivor. A mutation that makes code more
permissive tests nothing and looks identical to a real hole.

Classifier gaps closed with their own specimens (the verdict was well
covered, but every fixture was db37d525-shaped so several regex legs
matched at once). The load-bearing new test is the negative one: "treat
rate limits as a hard constraint" must stay clean, because widening that
leg quarantines ordinary domain advice and a quarantined lesson is
silently invisible to every injection surface. Killswitch pinned on the
shape it was written for: a quoted "false" from YAML is truthy, and a
config error must fail CLOSED with the gate ON.


## Path-token rewriting — SHIPPED 2026-08-16 (shape (b), both transfer lanes)

Filed 2026-08-13 as a low-priority residual of the workspace
export/import arc; built 2026-08-16 when Jeremy called the deferral
itself: *"let's hit that path cleanup; the intent was to do it later,
not kick it down the road perpetually."*

**The problem, measured.** A workspace embeds the absolute paths of the
machine that produced it. In the 2026-08-13 box copy: 5,841 text files,
~62k occurrences — 44,941 naming `/home/clawd/.maro/workspace`, 9,242 the
checkout, the rest older installs and loose `$HOME` paths. Both transfer
lanes carried those bytes perfectly and handed over data that only made
sense on the box.

**The three shapes, and why (b).** (a) export-time rewrite of text files
— fast import, but the archive stops matching the source and
reconciliation against the box gets harder. (b) import-time rewrite,
text only, recorded in custody — archive stays a faithful copy, leans on
`source.workspace_root`/`maro_user_dir`, which provenance had recorded
for exactly this. (c) no byte munging at all, a runtime path-alias layer
where consumers translate — zero corruption risk but touches every
consumer. Built (b); (c) stays the durable end-state if cross-machine
sharing becomes routine.

**The traps, pre-documented 2026-08-13 and all honored in the build:**
- Content-addressed stores must NEVER be rewritten — archived project
  repos carry `.git/objects/**`, where the name IS the content hash.
  Skipped by path component, at any depth.
- Binaries and sqlite hold paths INSIDE database pages; regexing one
  corrupts it. Screened by suffix AND a NUL sniff over the first 8 KiB,
  which also catches an unknown-extension database.
- The manifest digest: rewriting at import means the digest no longer
  describes the on-disk result. Resolved by construction — the shape
  digest is computed from archive member sizes, so an honest archive
  still verifies OK after a rewrite (pinned by a test), and the custody
  event carries `transformed` so a later reader knows disk deliberately
  differs from the archive.

**Two additions the build made on top of the filed design:** bare `$HOME`
is deliberately NOT a root (only roots the source recorded about itself
are mapped — a stale absolute path is inert, a confidently-wrong one
resolves somewhere real), and a recorded root is treated as hostile input
(it arrives in someone else's archive) — validated absolute, ≥2
components, not a system directory, so a provenance claiming
`workspace_root: "/"` is dropped with a reason rather than rewriting the
world. Export also began recording `repo_root`, the second-largest
population.

**Both lanes, not one** (the sibling-lane lesson, applied up front):
`maro-export import` rewrites only the files that import extracted, so a
merge import never touches what was already there, and writes the full
per-file list to `path-rewrite.json` beside the staged meta; `inspect`
previews the mapping before you commit to it. `maro-import --source`
rewrites copied runs, quarantined curated files and daily logs, and
rewrites ledger rows BEFORE the exact-line dedup — after would compare
raw source rows against already-rewritten target rows and append a
near-duplicate on every re-run.

**Verification.** Suite green (9,167 after the review round); 40-mutation
file-derived sweep (`tests/mutation/path_rewrite.json`), all accounted
for, 2 recorded equivalents. The sweep earned its keep before the review
even started: the test pinning "normalization runs before the denylist"
could not fail, because `/usr/` is refused by the shallowness rule either
way — a refusal-only assertion could not tell a working denylist from a
bypassed one. Same class as the §14a scope finding that opened the
mutation-coverage item.

**Adversarial review 2026-08-16 — 6 lenses (Skeptic, Architect,
Minimalist, Expert QA + Security-and-Abuse and Experimentalist as bonus
personas), all on sonnet-medium per the 08-15 decree while codex is
exhausted. Verdict: REJECT, 4 real defects, all fixed same session.**
Two carried independent consensus from lenses that had not seen each
other's work:
1. **HIGH (QA + Minimalist) — `os.utime` shared `os.replace`'s try
   block**, so a metadata failure AFTER the atomic commit returned
   `("unreadable", 0)` for a file whose bytes had already changed. The
   custody `transformed` stamp and the per-file `path-rewrite.json` are
   both computed from that count, so the one record the design exists to
   produce would have said *nothing happened* while disk differed from
   the archive. The pre-existing test only exercised failure BEFORE the
   commit point — the safe half.
2. **HIGH (Architect + Security) — `validate_root`'s denylist was
   exact-match**, so `/home/<user>`, `/Users/<user>`, `/usr/lib`,
   `/var/log`, `/tmp/foo` all validated as source roots. A crafted
   archive declaring `workspace_root: "/usr/lib"` rewrote every traceback
   path in the imported data into the importer's own workspace — the
   exact over-rewrite the module's stated doctrine claims to avoid.
   Fixed with a depth-below-a-shared-directory rule applied to SOURCE
   roots only: the destination is our own computed path, and refusing it
   would disable the rewrite on a legitimately-configured machine to
   defend against an input we generated ourselves.
3. **HIGH (Architect + Security) — the match had no LEFT boundary.** A
   root fired as a suffix of a longer path: `/mnt/backup/home/clawd/.maro/
   workspace` (a backup mirror) and `notes/Users/jeremy/x` (a relative
   path) were both rewritten into confidently-wrong absolute paths.
4. **HIGH (Skeptic) — the NUL sniff read only the first 8 KiB**, so a
   binary with a NUL-free header got a path spliced into its tail, while
   the docstring claimed it caught every unknown-extension binary. The
   whole file was already in memory; checking all of it costs nothing.

Also fixed: `--dry-run` in the merge lane reported a partial rewrite
count as if it were a total (3 lenses); a symlinked `--source` silently
no-opped the whole rewrite because `run_import` resolves its arguments
while the source's files embed the unresolved path (Experimentalist);
`unreadable` failures were tallied without filenames, so an operator
could not find which file still cited the old machine (QA); a leftover
`*.maro-rewrite.tmp` from a killed process was treated as ordinary text
by a later pass (QA); `..` segments passed validation (Minimalist).

**Then real data found what neither had.** The first live box→Mac import
(2026-08-16, 24,553 files) rewrote 4,787 files / 54,010 occurrences — and
left **5,543 occurrences of the primary root untouched**. Cause: the
review's new left boundary. Inside a captured step transcript a path
almost always follows an escape (`"…notes.md\n\n/home/clawd/.maro/
workspace/projects/…"`), so the byte before the root is the `n` of `\n`,
which the lookbehind read as an identifier character and blocked. Fixed
by making an escape sequence count as a boundary. No synthetic fixture in
either the 40-mutation sweep or six review lenses produced that shape;
one run against the real corpus did, immediately. **A transform over a
corpus is not verified until it has run over the corpus** — Jeremy's
instinct to regenerate the box copy rather than keep a stale one is what
surfaced it.

**The reusable lesson, and it is about the instrument:** the sweep was
green at 30/30 *before* this review. A file-derived mutation sweep bounds
what the tests pin, not what the code does — every mutation in it was
derived from behavior the author had already thought of, which is exactly
the blind spot a second reader is for. Neither tool substitutes for the
other: the review found the four defects, and the sweep is what now keeps
them fixed (10 mutations added, all detected).

---

## BACKLOG.md split — DONE 2026-08-16 (393KB → 237KB; archive rotated too)

The overdue split, executed as two moves. (1) BACKLOG_DONE.md itself had
grown to 606KB — past the same 256KB Read limit — so it rotated first:
records through the 2026-08-05 archived batch moved verbatim to
docs/history/backlog-done-2026-04-to-08-p{1,2,3}.md (byte-compared,
45/45 records conserved). (2) The sweep proper: three read-only agents
partitioned every large arc (exact anchors, checkbox census, Jeremy-
quote audit); 26 long-shipped blocks (167KB) moved verbatim to segment
p4 via a manifest harness that refuses to move any unchecked `- [ ]`
line (90/90 open items conserved, verified against HEAD). Each arc
keeps a live stub: open residuals verbatim + archive pointer.

Deliberately NOT swept, per the read-before-moving rule: house-style
(6 unchecked decree bullets wrap everything, incl. standing policy like
the no-re-raising-the-rename instruction); MH taxonomy (open residuals
interleave mid-sentence — needs a rewrite pass, not a cut); verifier
synthesis, next-leap, NOW retry rung, subsystem liveness, container
verb parity (2 days old), Conservative holding pen — all genuinely live.
Also fixed in passing: a concurrent session's burn-down status block
had been inserted mid-word inside Jeremy's truncation-audit quote
(restored); LeAct's stale "remaining: doorless canon threshold" line
corrected (it shipped 2026-08-13 with its own archive record); the
Poe-steal-list "match-tier item below" pointer retargeted.

## Expert-QA 4th review persona — TRIED + RATIFIED 2026-08-16

The 08-01 try-once decree, closed same-day-as-trial. First outing
(mint-grounding slice-2a review, sonnet fallback, 4 lenses): all four
of its findings were unique — the unprobed-rendered-as-refutation HIGH,
the consumption-surface laundering gap, the zero-logging finding, the
forged-shape probe — zero overlap with the other lenses, zero
hallucinations. Jeremy ratified: "I do care about that persona very
much. Let's add it to the /adversarial-review skill." Now permanent on
the LARGE roster (SKILL.md size table + Expert QA lens in
reviewer-lenses.md, provenance noted inline). Trial record:
docs/history/2026-08-16-mint-grounding-slice2a-review.md.

## Moved from BACKLOG 2026-08-16 (the overdue split) — records in docs/history/backlog-done-2026-04-to-08-p4.md

26 long-shipped arcs/records (167KB) swept out of BACKLOG.md with
context intact — REPL-reading, LeAct, LT-5/LT-0(a,b,c)/LT-4 records,
the arbitrary-truncation audit trail, typed dispatch envelope,
re-run identity, async post-run tail, workspace export/import,
closure Signal-1, session-fork, MEDIUM→LONG fix, open-thread
structure, and smaller closed records. Stubs in BACKLOG.md point at
this section; the verbatim blocks live in the p4 segment (dev-recall
ingests it).

## land.sh ci-watch inert on the dev Mac — FIXED 2026-08-16

Found 2026-08-16 while landing chunk 3 from the Mac: the post-land spawn
was `( setsid nohup scripts/ci-watch.sh ... & ) || true` and macOS ships
no `setsid`, so the subshell died while the "ci-watch: spawned" line
still printed — a watch that reports armed but isn't (our own taxonomy
pattern 7). Harmless on the box (setsid exists; the box is the primary
landing host), but the dev Mac landed a full chunk sequence blind.

Fix (scripts/land.sh): `command -v setsid` picks the branch — setsid
where it exists, bare `nohup ... &` fallback where it doesn't (the
double-fork subshell already detaches from job control; the new-session
bit only guards terminal signals nohup blocks anyway). Bonus honesty
fix: the spawn is now also gated on `[ -x scripts/ci-watch.sh ]`, so a
checkout without the watcher (every test fixture, historically) no
longer prints a false "spawned" line.

Probes (tests/test_land_rebase.py, must-fail verified via stash-rerun):
a symlink-farm PATH puts setsid's presence under the TEST's control on
every platform — `test_ci_watch_spawn_survives_missing_setsid` (red on
old code) proves a marker-writing stub watcher actually RUNS with no
setsid on PATH; `test_no_spawn_claim_without_watcher` (red on old code)
pins the honesty gate; `test_ci_watch_spawn_with_setsid` pins the
primary branch (real setsid on Linux, pass-through stub on the Mac).
Live fire: the landing of this very fix, from the Mac, is the first
dev-Mac land with a real watcher — /tmp/ci-watch.log has the trail.

## Closure claimed-but-unwired probe — SHIPPED 2026-08-16 (chunk 3 of 3; sequence complete)

The sanctioned 2026-08-16 sequence (author fix checklist → pre-flight
class-or-instance probe → closure claimed-but-unwired check, ordering as
proposed 2026-08-15) is complete: chunk 1 = HOUSE_STYLE claims protocol
(70d49d2), chunk 2 = pre-flight class_gaps dimension (eee98f1 +
9fd70b7), chunk 3 = this. Source: review-miss taxonomy pattern 5,
"prose guarantees with no executing line"
(docs/history/2026-08-15-review-miss-taxonomy.md) — receipts/provenance
already found executing evidence for files and commands; this extends
the question to stated behaviors.

What shipped (src/closure_verify.py + tests/test_closure_claim_coverage.py):

- The closure PLAN call gains reasoning item 5: extract
  `stated_guarantees` — behaviors the work summary / deliverable prose
  ASSERTS ("warns when X", "logs errors to Y", "retries on failure") —
  and map each to the 0-based index of the check that exercises it, or
  null when none mechanically can. Explicitly told: never fabricate a
  check to cover a guarantee, and a grep finding the WORD is not the
  behavior executing. Rides the existing call — no new adapter call, no
  config gate (chunk-2 precedent); plan max_tokens 512 → 768 so the new
  key can't starve the checks array (one JSON object — a mid-list cut
  loses the CHECKS too).
- Every executed check row now carries `plan_index` (its index in the
  plan's own checks array). `_claim_coverage()` joins guarantees to
  outcomes on that key — NOT positionally, because failing preconditions
  PREPEND synthetic rows and empty-command checks produce no row (the
  off-by-injection class the backchain arc hit). Statuses: exercised /
  contradicted / inconclusive / unwired; bool-typed indices rejected by
  exact-type check (the audit `agrees` discipline).
- Unwired guarantees are named to the verdict judge in a
  "Stated-but-unexercised guarantees" block, with verdict-system
  doctrine: unverified claim, not a fact — and not failure evidence
  either (same insufficient-coverage doctrine as inconclusive probes).
  Exercised/contradicted guarantees stay out of the block: their
  evidence is already in the check results.
- `claim_coverage` (per-guarantee records + per-status counts +
  `overflow` beyond the 8-guarantee cap) rides ClosureVerdict, the
  CLOSURE_VERDICT event, and the persisted closure_verdicts.jsonl row —
  per-kind firing data, per the 2026-08-16 claims-audit eval-hook-schema
  rule.
- Advisory v1 (the receipts posture): recorded and surfaced, never flips
  a verdict deterministically. The evidence gate stays open in BACKLOG:
  star-v8 keep/kill on whether the coverage data DISCRIMINATES on real
  box runs.

Suite 8831 passed / 0 skipped (+9 tests, written red-first).

**Adversarial review of the chunk (2026-08-16, sonnet-medium ×3 lenses —
codex capped until ~08-19; claims-audit framing):** 16 findings, 0
hallucinated, fixed to green same session — full record in
`docs/history/2026-08-16-closure-claim-coverage-review.md`. The HIGHs
were all in seams the author's mocked-judge tests structurally cannot
see, exactly as the claims-audit protocol predicts: (1) the advisory
block's own wording matched `_RUNTIME_GAP_ADMISSION`, so a judge echo
in gaps would have converted advisory into a deterministic True→False
flip — fixed with regex-safe wording, the "Unverified claim:" marker
convention, and `_strip_coverage_echo_gaps` at both detector call
sites (organic admissions still fire, pinned both directions); (2)
three-lens convergence: surfacing only unwired entries made a
mismapped/fabricated "exercised" link — the laundering direction —
LESS scrutinized than an honest unknown; the block now shows every
mapping with link-scrutiny doctrine, and both audit lanes receive it
(must-not-fork); (3) LLM shape drift (bare-string guarantees) parsed
as silence via safe_list, biasing the keep/kill data toward kill —
now unwired entries + a `malformed` counter; plus counts-beyond-cap,
prompt-side "at most 8" + max_tokens 1024 pinned, plan_index scrubbed
from the judge payload (C2 restored). Keep-bar tightened on the
BACKLOG gate entry: discrimination required, not mere firing; evasion-
by-vague-narration recorded as a known corpus bias. Suite 8845 / 0
after fixes (+5 tests).

## M1 complement stretch 2026-08-15 (PM): three chunks + review loop to fixpoint

Jeremy: "at least 3 significant pieces, review after each large chunk
(review in a loop until we actually fix the problems), find more
patterns in what we are missing in the reviews, don't guess — prove."
All landed; every review round ran sonnet-medium ×1-3 lenses WITH the
new watch-list injected.

1. **Review-miss taxonomy** (a4d8c58) — see the GOAL_BRAIN journal +
   `docs/history/2026-08-15-review-miss-taxonomy.md`; watch-list in
   DEV_PATTERNS.md. Its falsifier got same-day evidence: all six
   findings across the day's rounds map to taxonomy patterns 1/2/5/7,
   each probe-confirmed, each in a fix layer or a sibling.
2. **Verdict-bypass burn-down** (46a4590 → 448553c → ff07e12 →
   e58390b): census 12 → 1 sanctioned site (audit_repair's alignment
   patch, justified at the site); four new runs.py owners
   (stop-tuple replace/clear + pause preserve + atomic refine-note +
   in-lock evidence_out; unjudged-source; contested re-stamp; verdict
   extra rider with warn-and-strip guard); director.close's bare
   locked_rmw (which also skipped index_run_dir) routed; scanner
   var-flow + module-attr + dict()/update/**kwargs + path-var
   locked_rmw scoped to tuple-key-writing merge bodies (same-name defs
   unioned), all with must-detect fixtures + negative controls.
3. **Truncation tranche 1** (02ccd7e + e58390b): knowledge_web (22) +
   loop_execute (15) + knowledge_lens's starvation pair swept to
   honest clip(); ceiling 176 → 135. The compose-then-clip-once
   correction mid-tranche: per-field clips with NO outer cut where a
   composed cut can starve the second field (both evidence-row sites,
   pinned).

**Review loop record (fixpoint REACHED):** r2 3 findings (1H: director
re-read TOCTOU; swallowed guard raise; scanner evasions) → r3 1H
(scanner same-name collision) + tranche round 1H (judge-field
starvation) → r4 fix layer CLEAN, 1H in adjacent frozen debt (the
knowledge_lens verbatim sibling, fixed) → **r5 clean, nothing above
low** — exit condition met in the fix layer for two consecutive
rounds. 0 hallucinated findings across all rounds; every accepted
finding probe-confirmed by the reviewer before filing.

**Stale-test archaeology (Jeremy's eval fallback, run as bonus):**
(a) tests/test_container_e2e.py vs REAL docker: 20/20 (suite grew 4 →
20 since the 2026-07-13 burn-in; image current at 2.1.210-r3, built
2 days ago) — Mac container lane healthy, no pin drift; (b)
tests/integration/ IS collected by the main suite (not a stale corner;
the 2026-04 "review looked only in tests/" worry is moot for the run
path) and 50/50 green standalone; (c) scripts/smoke.sh: smoke=ok —
note: the deprecated `maro tick` lane logs the exec command 3x in one
note (dup accumulation; deprecated lane, filed not fixed); (d)
scripts/test-cov.sh: 80.19% vs the 70% floor and the 2026-07-14
78.04% baseline — coverage IMPROVED ~2 points in a month.

Tranche-2 targets carried forward (BACKLOG audit entry): claim_probe's
composed [:400] (the one other starvation-shape member, found by r5's
shape-based AST sweep, already inventoried), knowledge_lens's 8
single-field cuts, loop_blocked (12), and the r5-noted coverage gap
(the refight-evidence test only exercises short strings).


## Whole-changeset review 2026-08-13 — the five accepted-not-fixed residuals, ALL FIXED 2026-08-15

The "for fun" whole-changeset adversarial pass (35 commits, 3 codex
lenses) confirmed 9 real findings; 5 were fixed same session (verdict doc
`docs/history/2026-08-13-whole-changeset-adversarial-review.md`), five
accepted-not-fixed and filed in BACKLOG. All five closed 2026-08-15
(M1 session, complement lane while the box built the treasure map),
every fix carrying a test that FAILS against the pre-fix code
(verified by stash-and-rerun):

- **Container `require`/verb parity beyond the subprocess adapter.**
  `LLMAdapter.container_capable = False` base attribute (subprocess
  True) — a new backend is refused for executor work under `require`
  by construction. `container_exec.enforce_backend_container_contract`
  guards both executor seams (step_exec + workers): require+incapable →
  ContainerUnavailable; on+incapable → throttled SF-6 warning.
  `FailoverAdapter` walk skips incapable inners for require-mode
  executor calls (failover can never migrate an executor call onto the
  host) and raises ContainerUnavailable when everything was
  capability-skipped. Verb advertisement gated on the backend too:
  `execute_system_for_lane(adapter)` and the planner's maro-read teach
  both return honest absence for an incapable backend (under-advertises
  in every outcome — a failover hop could still containerize).
  *Accepted residual:* `FailoverAdapter.container_capable` is
  ANY-inner (matched to the require-seam consumer); for advertisement
  under mode `on` that's optimistic when a mixed list's first healthy
  adapter is incapable — the throttled walk warning keeps that shape
  visible at runtime.
- **`--expect-sha256` TOCTOU.** `import_workspace` now opens the
  archive ONCE and hands the same descriptor to both the digest and
  `tarfile.open(fileobj=…)` — a concurrent pathname swap can no longer
  make extraction read bytes the digest never saw. Test replaces the
  file at tarfile.open time and asserts the ORIGINAL bytes import.
- **`<3.11.4` unfiltered-extraction fallback.** Refused outright:
  `hasattr(tarfile, "data_filter")` gate before any mutation ("Nothing
  was changed"), and the per-member `except TypeError → unfiltered
  extract` fallback is deleted. Pre-3.11.4 targets get a clear refusal
  instead of a symlink-order escape surface.
- **Canon door TOCTOU + marker false-verify.** `_stamp` re-checks text
  identity in-lock (concurrent refight/revise between candidate read
  and stamp → partial-fail with an honest curate-by-hand reason; the
  appended entry stands per data retention). Marker membership is now
  CANON-SECTION-scoped via new `playbook.section_text()` — a
  same-marker entry in another section no longer false-verifies a
  deduped append; a marker already in Canon from a prior partial
  promotion still passes (that IS the retry path, pinned by test).
- **Exporter hardlink drop.** `_deref_hardlink` helper rewrites
  LNKTYPE tarinfo to REGTYPE with real on-disk size, applied on EVERY
  add lane (workspace tree filter, sqlite-snapshot gettarinfo, raw-db
  fallback, sidecars, experiments) — a workspace with two paths to one
  inode restores BOTH as independent files instead of silently losing
  the second.

Suite 8667 passed / 0 skipped after the chunk (+22 tests).

**Adversarial review of the chunk (2026-08-15, sonnet-medium ×3 lenses
per Jeremy's 5-day decree — codex capped until ~08-19):** two
probe-confirmed HIGHs, both in the chunk's own new code, both the
sibling-lane class — (1) 3-lens consensus: the documented mode-`on`
SF-6 warning was never wired for the production shape (build_adapter
always wraps in FailoverAdapter, whose ANY-inner capability report
no-ops the seam guard on a mixed list; the walk had no on-mode warn
branch) — fixed by moving the warn INTO the walk per serving inner
(`warn_backend_host_run`, one throttle, two callers) + a composition
test on the literal production path; (2) Skeptic executed probe:
`section_text` substring matching was spoofable by an embedded
`## Canon` inside LLM-derived entry text — my helper had faithfully
copied `append_to_playbook`'s own naive lookup — fixed with ONE shared
line-anchored `_section_span` used by both, plus the documented
one-line entry contract enforced at append (newlines in entry/source/
key collapse, closing the header-injection lane). Also took the
Minimalist's dedup (one `_tar_add_deref` for the three raw-add lanes).
All five defect tests verified failing against pre-fix code. Suite
8696 / 0 after fixes. Reviewer meta: sonnet round again produced
0 hallucinated findings — and both HIGHs were exactly "the diff and
comments read as correct, the tests asserted the wrong thing" (no
log-capture assertion; no injected-content case), i.e. the
claimed-but-unwired pattern.


## Executor image ships no pytest — SHIPPED 2026-08-06; stale entry found + archived 2026-08-13

**Resolution.** Both halves of the filed item shipped 2026-08-06 in two
commits, in the order the entry asked for:

- `fd57a56` "Make the executor tag name exactly one image" — the
  tag-scheme call: tags became `maro-executor:<cli-version>-r<revision>`
  (`IMAGE_REVISION` in `src/container_exec.py`; the CLI pin answers
  "which claude", the revision answers "which image"), enforced by the
  Dockerfile digest census test in `tests/test_container_exec.py`
  (KNOWN table maps `(pin, revision) → digest`; any Dockerfile change
  without a bump fails the suite).
- `0c23a5a` "Bake pytest into the executor image" — `python3-pytest`
  via apt (bookworm's 7.2.1), r2. Transcript evidence, not guesswork:
  run `d9607baa` bootstrapped pip and installed bare `pytest` FIFTEEN
  times (once per step — fresh container each step), never a plugin or
  a second package, so exactly that got baked. apt NOT pip,
  deliberately: shipping pip would hand every step arbitrary run-time
  PyPI installs — a supply-chain surface far wider than the problem.
  The rationale lives as a comment block in
  `deploy/docker/Dockerfile.executor`.

**Why the entry went stale.** Both commits were briefly reverted the
same day by a concurrent session committing stale dirty files (the
2026-08-06 tree-triage war story now in CLAUDE.md §concurrent-sessions),
then restored; in the churn the BACKLOG entry never moved here. Found
still-open 2026-08-13 when the item was picked up to build — the work
was already on main, wearing r3 (`9f5e645` baked the maro verbs on top).

**Verified 2026-08-13:** `IMAGE_REVISION = 3`, digest census green on
main; live probe of the dev Mac's `maro-executor:2.1.210-r3` answers
`pytest 7.2.1`; the box rebuilt its r3 2026-08-13 from the same
Dockerfile state, so the runtime image carries it deterministically.

Original entry, verbatim:

- [x] **Executor image ships no pytest** (found 2026-08-02 via `d9607baa`).
  `maro-executor:2.1.210` has git/python3/curl by design — the Dockerfile
  comment says the toolset is "what worker transcripts actually use". A
  transcript now shows otherwise: the #7 run bootstrapped pip from
  `get-pip.py --user --break-system-packages` (no root, no apt, no
  ensurepip, no venv), **twice**, because the install does not survive
  between steps — each step is a fresh container. It solved the problem
  resourcefully, and it paid steps and money to do it; every "run the
  tests" goal pays that tax. Two wrinkles before acting: adding packages
  widens a deliberately minimal isolation surface, and the image tag
  encodes the **CLI pin**, not image contents, so a rebuild under the same
  tag makes `maro-executor:2.1.210` ambiguous — which the Dockerfile's
  own "image version auditable" goal cares about. Needs a tag-scheme call,
  not just a `RUN apt-get install`.

## Moved 2026-08-11 — maintenance sweep

Closed `[x]` sub-blocks archived out of still-live BACKLOG sections
(the parent sections stay in BACKLOG.md with their open remnants).
Each block below is verbatim, headed by its source section.

### from: Live-writer census findings
- [x] ~~**4. Scan suggestions have no dedup-on-save — noise generator on
  frozen inputs.**~~ **SHIPPED 2026-08-07** — content-key dedup in
  `_save_suggestions` (category, target, suggestion text): identical
  findings skip the append whether the prior row is pending, applied,
  or dismissed (so re-derivation can't resurrect a reviewed row), and
  a moved input changes the derived text so real updates still save.
  Store-level, covers all three producers (evolver, harness_optimizer,
  loop_finalize) without per-scan freshness logic. Ride-alongs NOT
  done: calibration.jsonl test-row purge stays opt-in (data-retention
  decree — Jeremy's call) and the orphaned canon high-hit stats note
  (census doc §6) stands.

### from: SP. Session-protocol arc — preflight/container day-one findings
Preflight/observability findings (2026-07-16 evening, hermes X-thread dispatch
task-…b414ccab / run cobalt-pine — five-link failure chain, four fixed live):
- [x] **BLE imperative heuristic false positive** — bare `next\s` matched the
  noun phrase "exact safe next action" and dragged an outcome-shaped goal
  through the rewriter. FIXED: `next` now only counts clause-initially;
  pins `test_next_action_noun_not_imperative` / `test_clause_initial_next_is_imperative`.
- [x] **Rewriter dropped the goal's URL** — cheap-model rewriter went
  off-script (role confusion: "I cannot browse the URL"), its embedded JSON
  replaced the URL with "the referenced thread"; rule 3 (preserve constraints)
  is advisory-only. FIXED: enforced URL-preservation guard
  (`_rewrite_loses_referent`) rejects any rewrite that loses a URL; pins
  `test_rewrite_dropping_url_rejected` / `_preserving_url_accepted`.
- [x] **Clarity check ran on the rewritten goal** — system-introduced
  ambiguity became a clarification question back at the user for info they
  had provided. FIXED: clarity now runs on the goal as submitted, before the
  rewrite; pin `test_clarity_judges_goal_as_submitted_and_question_is_persisted`.
- [x] **Clarification question never persisted** — it lived only in the
  ephemeral HandleResult; run metadata/card and the hermes dispatch record
  all showed a bare `clarification_needed`. FIXED: question stamped into run
  metadata → carried on the run card (`clarification_question`), and
  dispatch.py worker now records `result_excerpt` from any preflight-
  terminated result; `status` verb surfaces both plus `goal_verdict_gaps`
  (new: closure gaps ride the card so the not-achieved "why" survives the
  300-char summary truncation — merry-nettle showed goal_achieved=false
  beside a summary whose visible prefix read "Goal achieved.").
  Tests: `tests/test_hermes_dispatch.py` (new).
- [x] **Task-store status vs dispatch status disagree** — SHIPPED 2026-07-16
  (same evening, parallel-fork pass): queue "done" = drained stays, annotated
  with `result_status` carrying the handle-result outcome; handle-level
  `error` results route to the existing `fail()` path with the result text.
  Both call sites (dispatch worker + `drain_task_store`) share the semantics;
  enqueue's 500-char display goal now marks truncation with `…`. Pins in
  `test_task_store` / `test_hermes_dispatch`.
- [x] **subprocess adapter ignores max_tokens** — SHIPPED 2026-07-16 (same
  evening, parallel-fork pass). Root cause: accepted-but-never-passed (the
  `-p` flag set has no cap flag). Utility (`no_tools`) calls now set
  `CLAUDE_CODE_MAX_OUTPUT_TOKENS` (live-probed: overrun = hard CLI error, not
  truncation — callers all fall back safely, which is the right direction for
  JSON-contract calls). **Correction 2026-07-17 (live, dapper-heron answer
  synthesis):** on this CLI version the env cap acts PER API MESSAGE
  (thinking tokens included) and the CLI auto-continues past it — the `-p`
  result is then only the LAST chunk (1005 tokens out vs a 350 cap; content
  began mid-sentence). Not a hard error, worse than truncation: it
  decapitates. Treat the env cap as a warning-seam companion, not an
  enforcer — callers needing shape safety must check
  `output_tokens > requested` and fall back (run_curation._llm_answer does;
  pin in test_run_curation). Agentic calls deliberately uncapped. Every call
  record now carries `max_tokens_requested`; the FailoverAdapter seam warns
  on utility overrun for backends with no enforcement (codex CLI
  accepts-and-ignores, no cap flag exists). Anthropic SDK and
  OpenAI-compat adapters verified honoring. Pins in `test_llm` /
  `test_record_mode`.
- [x] **Closure prose can contradict its own flag** — SHIPPED 2026-07-16
  (same evening, parallel-fork pass): `_verdict_first_summary()` in
  closure_verify makes the FLAG write the summary opener deterministically
  ("Achieved: " / "Not achieved: " / "Not judged (…): "), stripping a
  leading LLM verdict restatement so nothing doubles; downgrade-branch
  summaries (already code-written verdict-first) pass through; fail-open
  skip sentinels untouched. Prompt also asks for verdict-first prose
  (secondary). Pins in `test_director` incl. the merry-nettle
  contradiction case.
- [x] **Container executor walls off introspection goals — SHIPPED
  2026-07-18 (same day as the decree)** — brisk-saffron (task-…80466244)
  spent 2.8M tokens / 28min proving only its own isolation. **Jeremy's call
  (GOAL_BRAIN Decisions 2026-07-18): "Install in the container only for the
  runs that need access."** Shipped shape: `intent.classify()` gained an
  `introspects_self` schema field (4-tuple return; fails open False on the
  heuristic path — isolation is the safe default) → handle threads it as
  `introspection_access` into `run_agent_loop` → run-scoped ContextVar in
  container_exec (finding-D reset pattern) → `introspection_provision()`
  adds ro mounts (workspace `runs/` + maro source dir + PYTHONPATH +
  MARO_INTROSPECTION markers) to the container branch in llm.py. Gated by
  `executor.introspection_access` (docs/DEFAULTS.md row; default on, inert
  unless containers are on); provision output still passes build_mount_map's
  forbidden filter (workspace root/memory/config/secrets stay out); fails
  CLOSED to blind isolation. Design doc §4 amended. In-container maro CLI is
  best-effort (stdlib-only modules — image has no maro deps); the records
  mount is the real guarantee. Tests: TestIntrospectionProvision
  (test_container_exec.py), TestIntrospectsSelf (test_intent.py),
  agent_loop threading pins. Adversarial review (Codex ×3) same day:
  confirmed-and-fixed = conductor dropped the grant before run_agent_loop;
  pipeline/team/direct handle paths didn't thread it; provisioning failed
  partially OPEN (source-only mount + MARO_INTROSPECTION=1 with no records
  — now all-or-nothing None); symlinked runs/ could ride the grant past the
  descendant allowance (now refused). Accepted known gap: `--lane agenda`
  forces past the classifier → no grant (comment at the force_lane branch).
- [x] **Per-loop restart alert reads as terminal failure** — SHIPPED
  2026-07-17 (same night): loop_finalize's per-loop "Mission complete"
  telegram block (terminal language + per-loop totals for a NON-terminal
  event) replaced with a restart-only "🔄 Replanning … the run continues"
  ping. Completion is announced once, at run level, by
  notify.emit(run_completed) → notify_telegram with the curated card.
- [x] **Hermes never notified on run completion** — SHIPPED 2026-07-17
  (same night): SESSION_PROTOCOL §3 push leg wired for real.
  notify.command → `deploy/hermes/notify-hermes.sh` (two legs: rich
  ops-channel message via notify_telegram + ssh push of the job_id-enriched
  card to mini2 `~/bin/maro-inbox.sh` → `~/.hermes/inbox/maro/`); for
  dispatched-run completions + escalation-class events the inbox script
  also DMs Jeremy deterministically (fields quoted, no brain turn).
  notify_telegram rewritten user-grade: plain-language outcome header,
  verdict, gaps, findings excerpt (goal-echo stripped), cost+duration,
  per-run viewer link (notify.viewer_url). Verified live end-to-end with
  dapper-heron's real card. SKILL.md tells Hermes to read the inbox before
  ssh-polling.
- [x] **Viewer links: base URL posture (Jeremy 2026-07-17)** — "the detail
  link is a local IP. probably should be a tailscale link or a
  (configurable?) DNS base URL." The knob already existed
  (`notify.viewer_url`); this box now points at the tailscale IP
  (`http://100.82.68.63:8787`) and DEFAULTS.md documents the posture
  (localhost for same-box installs — the common case — tailscale/DNS over
  LAN IP for split setups). Found + fixed on the way: viz_server's
  allowlist 403'd `artifact/**`, so every "Full report" deliverable link
  sent before 2026-07-17 was dead on arrival regardless of base — now
  serves `<run>/artifact/*.{md,txt,html,json,csv}` only (repo.bundle et al
  stay denied; pins in test_viz_server). Remaining, both Jeremy-side:
  (a) MagicDNS doesn't resolve on the tailnet (both boxes fail on
  `maro.taile7bacd.ts.net`) — enable in the tailscale admin console for a
  stable pretty name; (b) the phone isn't on the tailnet (only maro +
  jers-16-mbp), so tailscale links open on the MacBook but not the phone
  until it joins — if that's a problem in practice, swap viewer_url back
  to the LAN IP in ~/.maro/config.yml (one line).
  **Update (same night):** superseded-in-flight by the public route —
  Caddy + caddy-security now runs on this box (SSO-as-floor decree;
  deploy/caddy/) serving https://mc.feifdom.com/maro/* behind an auth
  portal. **LIVE 2026-07-17 (same night):** Jeremy forwarded 80+443; the
  first ACME timeouts predated the forwards (external TCP probes then
  confirmed both ports reachable; a caddy restart got "certificate
  obtained successfully"); portal verified from off-LAN; viewer_url
  flipped to https://mc.feifdom.com/maro. Still Jeremy-side: first
  portal login (bootstrap credential retrieval + password change —
  README) and, when wanted, the GitHub OAuth upgrade. LAN caveat: the
  public name from inside the house needs router NAT-loopback
  (hairpin); if his router lacks it, in-house devices fall back to the
  tailscale/LAN base while messages carry the public link.
  **CLOSED 2026-07-17 (morning):** final shape is a dedicated subdomain —
  Jeremy added maro.feifdom.com at Namecheap and created the GitHub OAuth
  app; viz now serves at the subdomain ROOT (no /maro prefix), cert
  obtained, GitHub OAuth ENABLED live (pinned to slycrel; webadmin local
  login = break-glass, password in ~/claude/credentials-backup/caddy/).
  mc.feifdom.com was reclaimed for the kids' Minecraft server — no
  redirect kept, pre-swap message links are dead by choice. viewer_url →
  https://maro.feifdom.com. **VERIFIED by Jeremy 2026-07-17: "github
  SSO is working as intended."** Arc closed end-to-end. He expects
  "another UX pass at some point for the goal runs" — unscheduled, no
  open work.
- [x] **Completion excerpt should be the deliverable, not the step log** —
  SHIPPED 2026-07-17 (same night, answer-first pass). Two new curators:
  `locate_deliverables` (FS-diff of the run's project dir via
  `files_modified_since`, ranked by name-hint/prose/size; top copy lands in
  `<run>/artifact/` and `deliverable_link_path` on the card) and
  `synthesize_answer` (`answer_summary` = the ANSWER to the goal — gated
  cheap LLM call under `curation.answer_synthesis`, deterministic
  deliverable excerpt otherwise/on failure). Both message surfaces lead
  with the answer; verifier self-grade only shown when NOT achieved.
  Two-tone contract (Jeremy 2026-07-17): raw-orchestrator surfaces render
  human-readable; the Hermes push carries DATA (goal + answer_summary +
  full `deliverable_content` ≤16KB) and a detached Hermes brain turn
  composes the user DM from it (deterministic DM kept as spawn-failure
  fallback). Verified live: Hermes composed, sent, and filed the event to
  processed/ unprompted. Pins in test_run_curation / test_notify_telegram.

Container-on day-one findings (2026-07-16, two dispatched verification runs):
- [x] **Provenance freshness-window false demotion** — run 123bf935 demoted
  `incomplete` because `_run_window_start` reconstructed the window as
  now − elapsed − buffer and a slow post-loop closure slid it past artifacts
  the run's own early steps wrote. FIXED same morning (8393883): wall-clock
  start recorded in `_handle_impl`, preferred at both provenance call sites;
  pin `test_run_window_start_prefers_wall_anchor`. Re-verified live on run
  d2f4e2f4 (no mtime flag).
- [x] **Closure downgrade reason never reaches the run card** — SHIPPED
  2026-07-16 (code swept into `5c3a886`, review fixes follow-up commit; full
  adversarial-review record in BACKLOG_DONE). Summary now leads with
  "Downgraded to not-achieved — {reason}"; `goal_verdict_downgrade_reason`
  rides metadata → card → report → CLI, replace-semantics on re-stamp
  (resume stale-key bug found by review, fixed). The "detector too
  shape-strict" side-question became the compound-command item below.
- [x] **Step-count constraint ignored in decompose** — SHIPPED 2026-07-16
  (code swept into `5c3a886`, review fixes follow-up commit; full record in
  BACKLOG_DONE). `goal_step_ceiling()` detector + binding directive + prompt
  clamp + re-ask-then-truncate enforcement on all six decompose lanes,
  carries across boundary AND milestone expansion; byte-identical prompts
  when no ceiling stated.
- [x] **Probe-modality classifier counts run-then-grep compound commands as
  static** — SHIPPED 2026-07-16 (Jeremy's call, same day, after the
  verdict-shift measurement; full record incl. the measurement and the
  original DECISION-FLAGGED analysis in BACKLOG_DONE). Per-segment
  classification with a quote-aware top-level splitter; exactly one
  historical verdict flips (d2f4e2f4's own false downgrade).
- [x] **Precondition pre-flight strings leak into the closure check list** —
  SHIPPED 2026-07-16 (full record in BACKLOG_DONE, incl. a mechanism
  correction: the strings were never run as shell — the real chain was
  scope.py's naive comma split shredding prose preconditions +
  `_classify_precondition`'s absence-of-spaces command gate letting the
  fragments (`wc)`, `PYTHONPATH=src`) through to shutil.which as synthetic
  inconclusive rows). Fixed both layers: paren-aware top-level comma split
  + positive binary-name command match; pinned with d2f4e2f4's exact
  deliverable lines producing zero synthetic rows.

- [x] **Stuck advisor block is dead code** — RESOLVED 2026-07-16, decided
  by Jeremy twice in two parallel sessions with the same outcome ("fix +
  config-gate... turn it on by default for ourselves" in the morning
  session; "let's fix + enable that stuck lane's advisor. I thought that
  was on" in the afternoon one — he thought it was on because he'd already
  turned it on). Shipped in `3d35ba0`: attribute access fixed, context
  build outside the try (shape bugs loud), except narrowed to the LLM call
  at warning level, (b)-retry records the stuck-flagged execution, the
  folded restart-break residual fixed (`_append_stuck_step_outcome()`
  before the break), `advisor.stuck_step` gate default OFF
  (docs/DEFAULTS.md) with this box opted in, 3 gate tests. Afternoon
  session added the missing restart-break pin
  (`test_stuck_restart_records_stuck_step_outcome`, mutant-verified —
  the 3d35ba0 tests covered the gate and (b)-retry but not the break
  exit's record guarantee).

### from: LT arc — skill dead-drop
- [x] **Workers write skill files to a directory nothing reads
  (`projects/<slug>/skills/`) — SHIPPED 2026-08-06.** Jeremy decided
  per recommendation ("I don't have a strong opinion... let's go with
  your recommendation"): **promotion-side ingest.** One-dir fix:
  `_lite_candidate_files` now scans `projects/<slug>/skills/` (listed
  before the project artifacts dir so the scan cap can't starve it),
  routing dead-drop skills through the ONE vetted skills-lite lane —
  shape check, dangerous-pattern scan, injection guard, overlay copy +
  provisional companion with validation stamping. Two pins
  (`TestDeadDropIngest`): end-to-end promote + direct scanner
  (red-verified — the end-to-end alone passed incidentally because the
  2026-08-05 serve-all-artifacts change copies project .md files into
  `rd/artifact/`, which the lite scan covers; that path depends on
  deliverable-ranking luck under the 12-slot cap, so the scanner pin is
  the load-bearing one). Original finding preserved below. — Both
  B1 cold and B1w, told to "capture the access path as a reusable
  skill file … future runs can load", wrote genuinely generic,
  high-quality skill docs (`source-verification-ladder.md`,
  `quote-verification-ladder.md`) into their project's `skills/`
  subdir. Runtime skill surfaces are `workspace/skills/` (.md overlay)
  and `memory/skills.jsonl` (injection store) ONLY — verified in src;
  no code path scans project skills dirs. The auto-promotion pipeline
  partially masks the gap (it minted 3 thin skills from B1 cold that
  DID inject into B1w and amortized), but the worker-authored rich
  versions are strictly better documents and are stranded. Fix
  directions (pick one): teach workers the real surface (prompt/skill
  the path), OR have promotion ingest project-local skill docs, OR
  make `projects/*/skills/` a real overlay source.
  **Done 2026-08-05:** both stranded ladders hand-reviewed and rescued
  into `~/.maro/workspace/skills/` (the overlay the runtime reads),
  each with a provenance header naming this finding — reversible
  curation, not a pipeline. **Decision made 2026-08-06: promotion-side
  ingest** (option 2, recommended and shipped above) — at run end
  the promotion pipeline already owns the skill store and its
  guardrails (provisional tier, circuit breakers, dedup); scanning the
  run's project `skills/` dir for worker-authored .md docs keeps one
  vetted write-path, vs. option 1 (prompt-shaping, fragile) or option
  3 (unvetted files leaking straight into the global overlay with no
  tier/breaker protections).

### from: LT arc — budget-pause breach note
- [x] **Budget-pause breach note is denominated in tokens** (observed
  2026-08-02 while tracing the ladder) — **FIXED 2026-08-06** (this
  commit). `_ladder_pause` stamps `budget-decision`, records the breach
  note, and emits an operator question naming the three resolutions —
  so Jeremy's "we need data capture at pause time so an unanswered
  pause still explains itself" was substantially satisfied. The
  wrinkle: the internal note was in **tokens**, and this arc spent five
  runs proving tokens are the misleading unit (cache reads bill at
  0.1×, so `tokens_in` is ~3–4× the real cost). The token breach note
  now carries the cache-aware `total_cost_usd` accumulator alongside
  the token count (`…, ~$X.XXXX est. spend after step K`); the cost
  breach note was already dollar-denominated. Pinned by
  `test_token_budget_breach_note_carries_dollars`.

### from: LT arc — closure partial-view
- [x] ~~**Closure judges a partial view of an artifact and can attribute
  adjacent content to fields**~~ — **FIXED 2026-08-03 (`f7b775c`).**
  Diagnosis first, and it moved: the verdict prompt gets `Work done`
  (the run's narration) plus `check_results` (probe stdout). In
  `2738d9c0` the only probe was a structural validator printing
  `SCHEMA OK`, so the field VALUES appeared nowhere in evidence and the
  judge reasoned from the narration — which quoted the artifact's
  optional `notes` array. It then stamped False at 0.80, over
  `VERDICT_CONFIDENCE_FLOOR`, demoting a correct run at FULL trust.
  Shipped both halves, each completing doctrine the module already had:
  **prompt** — "you may only assert what a file CONTAINS when that
  content is in front of you" (the converse of the existing
  `target_file_content` is-ground-truth rule); **structural** — when
  `complete=False` contradicts EVERY executed probe and no file content
  is in evidence, cap confidence just below the floor. Nothing hidden:
  complete/gaps/summary recorded verbatim and the summary states the cap.
  What is denied is **standing** — it can no longer demote a run or teach
  a failure. Applied to the LLM verdict only, before the deterministic
  downgrades (modality/diagnosis-derived, which IS evidence). Verified
  the restart path cannot engage on this shape (it requires a hard-failed
  check), so the cap adds no cost. 9 pins, 3 red on revert.
  **Chose the confidence cap over "put the artifact bytes in the prompt"
  deliberately:** the cap is a statement about what a verdict may claim,
  and holds for every deliverable shape; feeding bytes only fixes the
  cases where we guessed the right file to feed.

### from: LT arc — claim-verifier relative paths
- [x] **claim-verifier: relative claimed paths resolve only at the run
  root** (found 2026-08-02, `d9607baa`) — **FIXED 2026-08-06** (this
  commit). A report saying "replaced tests/test_ledger.py" was flagged
  FILE_CLAIMS_NOT_FOUND when the goal pointed at a subdirectory.
  Advisory-only, so it cost noise, not verdicts — but it was the third
  false positive in this family and the first two DID cost verdicts.
  Narrow widening, same shape as the glob fix that landed for
  `9d88acf2`: the existing bounded walk (`_tree_index`, same skip-dirs +
  2000-dir cap) now also indexes relative posix paths; a non-absolute
  claim with a dir component that misses at the root verifies iff the
  WHOLE claimed path matches an indexed relpath or its "/"-suffix.
  "docs/module.py" still cannot match "src/module.py", ".."-escaping
  claims never suffix-match, and a root dir named `src` cannot satisfy
  "src/module.py" against its own top level. Pins in
  tests/test_claim_verifier.py (subdirectory-resolved, wrong-dir,
  parent-dir-name, deep-suffix).

### from: LT arc — LT-2 capability ladder
- [x] **LT-2 — `docs/CAPABILITY_LADDER.md` SHIPPED 2026-08-01.** C0–C5
  checkpoints + four ladders (A web-reading, B here-and-now grounding, C
  self-inspection, D remember-across-runs) with per-rung status, indexed in
  `docs/INDEX.md`. Two findings worth carrying out of the writing: **(1) C4
  (persist & reuse) is the first rung orchestration earns that a good
  single-shot model plus tools cannot approximate** — everything below it is
  table stakes, so C4 is both the prize and where our evidence is weakest.
  **(2) Ladder D rung D1 (record what was injected) is not a capability any
  user would ask for** — it exists solely to make D2/D3 measurable, which is
  precisely why it was the thing quietly broken at 10% coverage. Original
  spec follows.

### from: LT arc — LT-2 stale duplicate
- [x] ~~LT-2 — `docs/CAPABILITY_LADDER.md`.~~ **STALE DUPLICATE, closed
  2026-08-08** — its own text was already struck through and the shipped
  twin sits directly above (`LT-2 … SHIPPED 2026-08-01`, C0–C5 + four
  ladders, indexed in `docs/INDEX.md`). The checkbox simply never got
  flipped when the strikethrough went in. Original text kept below for
  the record. Broad checkpoints, each a
  small named goal set that must read `verified`, not `target`: **C0**
  know what you don't know → **C1** fetch & ground → **C2** triangulate &
  resist fabrication → **C3** execute & check → **C4** persist & reuse →
  **C5** self-direct across ticks. Doubles as the acceptance tests for the
  blank-slate pre-installed skill set (same selection principle already in
  CAPABILITIES.md: the shipped set and the test corpus verify each other).

### from: NOW retry rung — closed blocks
- [x] Verify `_is_complex_directive` escalation actually fires live —
  **DONE 2026-07-29 (autonomous batch), with a twist.** The gate IS
  live-reachable: config default ON, and `force_lane` (which bypasses
  it) is only ever passed by eval.py and the CLI `--lane` flag —
  dispatch/telegram traffic flows through it. But the zero-firings
  recon was structurally inconclusive: NOTHING durable was written at
  flip time (`classification_reason` is a HandleResult field, not
  metadata), so never-fired and fired-invisibly were indistinguishable.
  Fixed: the flip-time metadata write now stamps `now_escalated: true`
  (mirroring the verdict-escalation's existing `now_verdict_escalated`),
  pinned by `test_escalation_stamps_now_escalated_metadata`. Best
  current read: zero firings because the upstream classifier already
  routes complex directives to agenda (the escalation is a backstop for
  classifier misses, and those are rare post-BLE-fix) — now measurable
  instead of argued.
- [x] Provenance-stamp NOW-lane runs — **ALREADY LIVE, checkbox was
  stale (verified 2026-07-29).** Both halves shipped with the
  `open_run` unification after the 2026-07-28 scan: `open_run` stamps
  `measurement_class` into run metadata before lane routing (verified
  on live runs dba0386d/08f214c3, both `mc=organic`; older runs
  predate it), and the NOW-lane `record_outcome` call (handle.py)
  passes `measurement_class` onto the durable outcome row. Historical
  corpus stays unstamped — any organic A/B should filter to
  prospective rows, as verdict-gap-stats already does.
- [x] Build the rung — **shallow half SHIPPED 2026-07-29** (trio-triage
  PARTIAL verdict: artifact-seeded retry only; star port explicitly
  OUT). On NOW self-verdict demotion: one seeded NOW retry (ask +
  failed answer[:1500] + demotion reason), re-judged against the
  ORIGINAL ask; complex directives skip (structural → escalation);
  errored retry keeps attempt 1's answer; both attempts fully recorded
  (`-retry` artifact + outcome row + `NOW_ARTIFACT_RETRY` event).
  Gated `now_lane.artifact_retry` (default OFF, ON this box). A
  still-failed retry falls through to `escalate_on_not_achieved`
  unchanged, escalation context names both attempts. The star-executor
  arm (ii) stays open below with the experiment.

### from: Next-leap packaging — closed blocks
- [x] **Slice 1 — packaging readout (report-only) SHIPPED 2026-07-29**:
  `src/packaging_readout.py` + CLI (`PYTHONPATH=src python3 -m
  packaging_readout [--persona N] [--json]`). Persona × goal-type cells
  with would_include / would_exclude / insufficient_evidence buckets;
  evidence = the LIVE selector's own picks (find_matching_skills) joined
  to skill-stats.jsonl (the real usage store — skills.jsonl use_count is
  unmaintained, 2/314 nonzero), plus a supplementary open-circuit scan
  the live selector can't show. Coverage block reports every join's hit
  rate: dispatch→outcomes 133/140 goals; persona-outcomes loop_id join
  DEAD (0/40); verdicted outcomes thin (22 achieved / 18 not / 156
  unverdicted). Liveness-tested (real writers, no read-side mocks) +
  live-verified non-empty on this box.
- [x] **Slice 1 catch — degenerate router FIXED 2026-07-29:** root cause
  was two structural defects, both verified live: (1) training corpus
  99.4% positive (166/167 rows — labels bucket success_rate>0.6→1.0
  over a store where nearly everything succeeds), so the classifier
  predicts ~0.992 for everything; (2) `route_skills` vectorizes
  `skill.description` only — the goal NEVER enters scoring, so scores
  are a goal-independent per-skill prior (a haiku goal and a pytest
  goal got identical top-3). Fix: discrimination guard in
  `route_skills` (score spread < DISCRIMINATION_EPSILON across ≥2
  candidates → results degrade to method="keyword", so
  find_matching_skills' existing router check falls through to
  goal-sensitive keyword/tf-idf matching). Deterministic, reversible,
  no retraining. Live delta: research goal → research skills, pytest
  goal → Test-Driven Code Fix, haiku → haiku_to_file; readout buckets
  went from one constant trio to 46 distinct would_include skills in
  builder×agenda alone. 4 pin tests incl. end-to-end consumer pin.

### from: Next-leap packaging — skill-stats honesty
- [x] **Skill-stats measurement honesty SHIPPED 2026-07-29** (the
  disease under the router symptom): legacy counters credit every
  keyword-matched skill with a step completion (bystander attribution,
  per-step × per-run, failures only on hard-block) — 99.4%-positive
  store, top "skills" at 852 uses @ ~1.0. Shipped: (1) double-count
  fix — `update_skill_utility` no longer writes stats rows internally
  (both live callers already call `record_skill_outcome` directly);
  (2) honest counter pair on SkillStats
  (injected_runs/injected_successes/injected_success_rate) recorded at
  the closure-verdict seam (`stamp_outcome_verdict` →
  `_maybe_record_skill_injection_outcomes`) for skills ACTUALLY in the
  run's `source/skills_manifest.jsonl`, label = the run's FULL-trust
  goal verdict (era-10 verdict_trust gate), idempotent via
  `source/skill_attribution.json` marker (verdicts are re-stampable);
  (3) packaging readout prefers the honest regime whenever
  injected_runs > 0 and labels which regime each number came from.
  Legacy counters keep accruing (single-count now) so existing
  consumers stay fed. Pinned: counter math, no-stats-from-utility,
  seam end-to-end (FULL/directional/unjudged/idempotent re-stamp/
  manifest dedup/no-manifest/no-run-dir), readout regime preference.

### from: Next-leap packaging — verdict-blind lanes + closed follow-ons
- [x] **NOW + evolver_verify lanes are verdict-blind (census
  2026-07-29)** — **CLOSED 2026-08-06 (`1031cf1`)**, mostly by work that
  had already shipped. Re-censused against 1,493 rows before touching
  anything, and both the item's headline and its proposed fix were stale:

  - **Denominator artifact.** "12% of NOW rows judged" pools 20 pre-ship
    rows with 4 post-ship ones. Chunk B shipped 2026-07-31; **every NOW
    row before that date is unjudged and every one after is judged.**
    Post-ship coverage across all lanes is 41/45 (91%) judged, 42/45
    carrying a verdict source. Same trap as the LT-arc cost denominator —
    *check the ship date before believing a rate.*
  - **Wrong fix.** The item said "wire loop_id stamping". Both lanes stamp
    their verdict **inline at record time**, so loop_id was never the
    blocker — NOW via `_verify_now_outcome`, evolver_verify via
    `goal_achieved=passed` + `goal_verdict_source="deterministic_tests"`
    (the test suite IS the judge). Their rows still carry no loop_id and
    are judged anyway.
  - **The residual was in `agenda`** — the lane the census did not flag.
    3 rows since the ship date, all with loop_id, all silently unjudged.
    Two real bugs behind them, both fixed in `1031cf1`: the tripwire
    watched `done` only (though `_closure_eligible_statuses` includes
    `stuck`, `partial`, `restart` — so an unjudged stuck run is the same
    gap, and the old pin asserting otherwise was wrong against the code),
    and the honesty event went to the captain's log while the **outcomes
    row stayed silent** — the ledger being the thing a denominator counts.
    Rows now stamp `goal_verdict_source="closure_never_stamped"` with
    `goal_achieved=None`: absence with a reason is a fact, absence alone
    is a hole indistinguishable from "not judged yet".

  ~~Left open deliberately: `error`-status runs (backend death) still carry
  no marker~~ — **CLOSED 2026-08-06 (`e9e6828`)** with its own reason code,
  `VERDICT_SOURCE_RUN_ERRORED`, kept distinct from the tripwire's
  `closure_never_stamped` on purpose: there closure owed a verdict and did
  not deliver one (a closure bug worth hunting), here nothing was owed
  because there was no finished work to judge. Collapsing them would send
  someone chasing a closure bug that is really a backend outage. Run
  metadata only — measured, `"error"` never appears as an outcome status at
  all (the run dies before `reflect_and_record`), so a ledger stamp would
  be dead code pretending to be a guard. Scope stated in the code as well
  as here: 132 runs, **129 of them in 2026-05 and none since 2026-07-04**,
  so this closes the gap for the next one rather than a live bleed.

  ~~Still open: the tripwire has a named false positive~~ — **CLOSED
  2026-08-06 (`e769dc4`)**, and my note calling it "a small share" was
  wrong by an order of magnitude. Measured before building: **259 of the
  345 unverdicted agenda runs on the box have zero completed steps — 75%
  of the tripwire's firings were false positives**, which is the rate at
  which a tripwire stops being read. Closed by naming the skip at the frame
  that evaluates the condition (`handle.py`, which has `_ran_any_step` in a
  local) rather than inferring it later from the loop log; the tripwire
  itself needed no change, since it already skips anything carrying a
  `goal_verdict_source`. Third member of the family, kept distinct because
  they route to different places: `closure_never_stamped` (hunt a closure
  bug), `run_errored` (backend death, nothing owed),
  `closure_skipped_no_steps` (no work to judge).

  **Method note worth keeping:** every one of the three residuals in this
  item was mis-sized in the filing and re-sized by measurement — the NOW
  rate was a ship-date artifact, the errored population was historically
  dead, and this one was 15x larger than "small". Filing an estimate is
  fine; acting on one without re-measuring is what keeps going wrong.
- [x] **"done" runs occasionally skip closure silently (census
  2026-07-29)** — SHIPPED 2026-08-02 as LT-0 (b): `DONE_WITHOUT_VERDICT`
  honesty event in `runs.close_run` (see the LT arc entry for detail).
- [x] Durable dispatch→outcome join SHIPPED 2026-07-29:
  `record_persona_dispatch` stamps handle_id (handle.py passes the run's
  id), readout joins handle_id-first with goal-prefix fallback for
  legacy rows, coverage line reports the durable-join hit rate (0 at
  ship — accrues as new dispatches land). Pinned both sides + an
  end-to-end divergent-goal-text case where only the durable key joins.
- [x] **A/B variant subsystem un-starved SHIPPED 2026-07-29** (arena
  sweep headline): the whole Agent0 variant lifecycle (frontier rewrite
  → create_skill_variant → 50/50 routing → record_variant_outcome →
  retire_losing_variants) was wired end-to-end on live paths with green
  tests and had NEVER executed — `frontier_skills` gated on
  `use_count >= 3`, and `increment_use` (the only use_count writer) had
  zero callers since birth (2/314 nonzero live, both leaked test
  fixtures). Fix: frontier gate reads
  SkillStats.injected_runs/injected_success_rate (honest verdicted
  evidence; first legacy-rate consumer migrated), `increment_use`
  removed (use_count legacy-frozen on the dataclass), pin test:
  legacy use_count confers no candidacy. Live effect: the router's
  former constant trio (0.67–0.68 over 18–19 verdicted runs) lands in
  the 0.4–0.7 frontier band — the first organic A/B variants will
  target exactly the most-used below-bar skills.
- [x] **persona-outcomes.jsonl retired 2026-07-29**: writer removed
  (`record_persona_outcome` + conductor call site — sole caller sat on
  the unexercised conductor path; store dead since 2026-04-04, 0/40
  loop_id join). Superseded by the durable handle_id dispatch join.
  File kept on disk as historical data (data-retention rule). If
  persona-outcome tracking is ever rebuilt, do it at the verdict seam
  like the skill-injection counters.

### from: Shipped 2026-08-04 stub — records duplicated from BACKLOG_DONE
- **Playbook exit paths** — all three shipped (alarm keyed re-read +
  silence expiry `playbook.alarm_ttl_days=14`; held signals gated with
  `--dismiss` as the exit; `GUIDANCE_FORM_RULES` in the generator).
  Pins: `test_playbook.py::{TestGuidanceForm,TestAlarms}`, `test_evolver.py`.
- **Dynamic-guardrail lane never ran** — prose-as-regex + ISO-vs-epoch
  stamp, fixed together; lane now warns-never-blocks with the circuit
  breaker still applying. Live behavior unchanged by the fix ([] before,
  [] after, for a stated reason).
- **Re-sighting could silently un-contest a lesson** — stale-write in
  `_reinforce_tiered_lesson` fixed; 4 pins verified red on HEAD first.
  The `6287e494` disappearance it was found while chasing stays OPEN
  below — the hazard was real but is NOT proven to be that cause.

### from: Shipped 2026-08-04 stub — records (cont.)
- **Generic-slug disambiguation** (`resolve_project_slug`) — two-guard
  deterministic fix, blast radius measured (757 records → 0 splits), two
  metadata holes closed on the way. Named cut: subject match is ONE
  shared word, biased toward continuity by design.

### from: Test corpus — record mode
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

### from: Test corpus — mining passes
- [x] **Mining passes on the parked data — ALL SHIPPED, stale bullet
  corrected 2026-07-13.** All four miners named here are live in
  `run_curation._CURATOR_SPECS` (9 curators total, topo-sorted since
  the R1 batch-1 fix this session): `flag_skill_candidate` (skill
  scraper, now with a real consumer — `evolver.promote_skill_candidates`,
  shipped this session), `scrape_scripts` (script scraper),
  `index_decision_prior` (decision-prior indexer — the owner-ask half,
  Jeremy 2026-07-04 "task failures being retried, with the old task
  context available"), `rescue_partial` (partial-run rescue).
- [x] **Unify rung-4 step I/O** — DONE 2026-07-04. `FailoverAdapter.complete`
  stamps the record path onto the response (`resp.call_record`) when
  `record_llm_call` captures; `execute_step` carries it on the outcome dict;
  `StepOutcome.call_record` threads it through all construction sites (main
  loop, stuck-repeat, parallel batch + fan-out); `_write_loop_log` emits it
  per step. The loop view's truncated excerpt now links straight to
  `<run-dir>/build/calls/call-NNNNN.json`. 4 tests in test_record_mode.py.

### from: Test corpus — replay slices
- [x] **Wire more slices into real tests** — DONE 2026-07-03. Five replay
  tests added to tests/test_orchestration_corpus.py: too-broad breach
  conjunction (113 recs, floor-division boundary documented), metacognitive
  convergence-heuristic tail replayed against 281 recorded decisions (0
  mismatches; diagnosis-path out of scope — events don't carry loop state),
  claim-verifier outcome/action pairing, diagnosis subjects pinned to the
  current FAILURE_CLASSES taxonomy, closure-verdict internal consistency +
  proof the BACKLOG #5 restart predicate discriminates on real history.

### from: Run-visibility residuals — goal search
- [x] **Goal search in the run visualization — SHIPPED 2026-07-13** (Jeremy,
  2026-07-10, rider on the retention decree): `maro viz search` filters run
  summaries by goal text / lane / status / date, plus a client-side filter
  bar in the HTML index. See BACKLOG_DONE for full detail.

### from: Two small refactors — classify dataclass
- ~~**`intent.classify()`'s widening tuple wants a dataclass.**~~ SHIPPED
  2026-07-29 (autonomous batch): frozen `ClassifyResult` dataclass with the
  five named routing facts; `classify()` returns it on all four paths,
  handle/conductor read attributes, all test unpacks + mock patches
  migrated. Deliberately NOT iterable (stale tuple-unpack fails loudly —
  pinned in test_classify_returns_named_result). Bonus: `needs_live_data`
  is now exposed to callers instead of being swallowed inside classify(),
  and the file-output override no longer drops it. Side-find while
  migrating: `persona.py:412` imports nonexistent `intent.classify_intent`
  → always excepts → `task_type` template var is permanently "general" —
  this CONFIRMS the wiring-inventory "persona template seam dormant" claim
  (item in the 8-claim verify list).

### from: Post-Purgatorio batch — skills-lite promotion
- [x] **Skills-lite two-tier promotion — SHIPPED 2026-07-10.** Jeremy rider
  on the graduation precedent: "we want things promoted to skills that the
  local orchestration can pick up and use while waiting for user review...
  looked at as skills-lite, and degraded the same as regular skills that
  get broken or stop working." Implementation:
  `run_curation.promote_skills_lite` (new curator, also the first BACKLOG
  #0 miner — the skill scraper): skill-shaped .md artifacts (frontmatter
  name+description+triggers/roles) from successful runs (success /
  done-unverified only; done-not-achieved excluded) copy into the
  workspace skills overlay stamped `tier: skills-lite` +
  `promoted_from: <handle_id>` — skill_loader injects them immediately.
  Each promotion registers a companion provisional Skill in skills.jsonl
  so the normal stats/decay/circuit-breaker machinery tracks it;
  `degrade_skills_lite()` quarantines the .md to `skills/_quarantine/`
  when the companion trips (circuit open) or vanishes (gc/culled) — the
  "degraded the same as regular skills" half, riding the exact demote
  signals. Fail-closed: sandbox `_DANGEROUS_PATTERNS` scan (first real
  consumer of that lane), never overwrites an existing skill name, unsafe/
  colliding candidates recorded as skipped on the run card
  (`card["skills_lite"]`) so they surface for human review. Provenance
  sidecars (create/demote) via `write_skill_provenance`. Config
  `skills.lite_promotion` default ON by decree (docs/DEFAULTS.md row;
  census green). 10 tests in tests/test_run_curation.py; live no-op smoke
  on the real workspace (no false-positive promotions on re-curation).
  SF-10 demand side: companion Skills enter the funnel with real
  trigger_patterns, so promotion pressure now has a source.

### from: Post-Purgatorio batch — user/ readers migration
- [x] **Migrate the two remaining repo-copy user/ readers to
  `config.user_file()` — DONE 2026-07-10.** All three call sites
  (`handle._load_user_config`, handle's COMPLETION_STANDARD injection,
  heartbeat's mcp_servers read) now resolve workspace-overlay-first via
  `config.user_file()`; a workspace `user/CONFIG.md` /
  `COMPLETION_STANDARD.md` is honored everywhere. user/README.md caveat
  removed, DEFAULTS.md lane note updated. Test:
  `test_load_user_config_reads_workspace_overlay` (tests/test_config.py).
- [x] **Orphan scope A/B datasets — WRITTEN OFF 2026-07-12 (Jeremy,
  handoff decision batch; closes arch-03).** `~/.maro/experiments/
  scope-ab-2026-04-25-v0/` and `scope-ab-2026-04-26-v1/` hold full PAID
  treat/control run dirs with no ANALYSIS.md. Explicitly written off: the
  inject decision was already made on the 2026-04-22 evidence and shipped
  (SF-4 flip). Spend acknowledged, not silently forgotten; data stays on
  disk per the retention decree if a future analysis wants it.

### from: Decay-trust — retention audit
- [x] **Retention-decree audit — 3 violations found and FIXED 2026-07-10**
  (Jeremy: "let's fix, no time like the present"). Sweep of every deletion
  site in src/ against the retention decree found the same auto-deletion
  family the step-artifact bug belonged to:
  1. **Lesson decay-GC deleted memory** (the path that once ate the whole
     38-lesson MEDIUM store): `run_decay_cycle` + `gc_memory` now archive
     to `memory/lessons_archive.jsonl` before dropping from the live store;
     `search_graveyard` reaches the archive and resurrects via
     `resurrect_archived_lesson()`; `forget_lesson` archives as
     `user_forget` (excluded from auto-resurrection — forgetting is the
     user's call); `maro-knowledge` stats surface the archived count.
  2. **Skill island culls + A/B variant retirement hard-deleted skills**:
     both now archive to `memory/skills_archive.jsonl` + write a `retire`
     provenance record.
  3. **Finalize deleted the checkpoint on done**, but closure verification
     runs after finalize — a run demoted done→incomplete had already lost
     its resume state. Checkpoints now kept on completion (stranded-sweep
     already skips finalized runs via metadata status; `checkpoint delete`
     CLI remains the user-level removal path).
  Enforcement so the class can't recur silently:
  **tests/test_no_silent_deletion.py** — AST census of every file-deletion
  call in src/ (unlink/rmtree/os.remove/rmdir incl. aliased/bare-import
  forms) against a justified allowlist, plus a pin that nothing outside
  checkpoint.py references `delete_checkpoint`. Same pattern as the
  DEFAULTS.md census tripwire. Known limit: record-level rewrites aren't
  generically detectable — the two known record-level deleters are the
  ones fixed above, pinned by unit tests.

### from: Decay-trust — V3 graduation verification
- [x] **Graduation-specific behavioral verification (VERIFY_LEARN_ARC V3) —
  SHIPPED 2026-07-14.** V1 (expectation stamping), V2 (cadence verdicts +
  auto-revert), and V3 all landed the same day. `verify_applied_suggestions()`
  now verdicts an applied graduation row on its *own* `failure_class_rate`
  (per-class rate over timestamped-diagnosis windows — diagnoses gained a
  `recorded_at` stamp), instead of the class-neutral global stuck-rate on which
  a single class is noise (why graduation rows only ever parked `unverifiable`).
  Falls back to the stuck-rate when class windows are thin. Lifecycle +
  symmetric authority reused from V2; structural `verify_pattern` stays pure
  observability. Knob `evolver.verify_use_class_signal` (default ON).
  **Deliberately NOT built (owner call, Jeremy 2026-07-14): autonomous *apply*
  of graduation rows.** Graduation stays advisor-gated — a human applies via
  `maro evolver apply`; nothing auto-applies a standing rule, so a degraded
  graduation row surfaces for review (never auto-reverted). This is a safety
  posture, not a gap; revisit only if the auto-apply-threshold owner call
  changes. (The live workspace still has no *applied* `graduation:` suggestions
  to verdict yet, but the class-rate time axis is live: `_load_dated_diagnoses`
  now recovers 1274/1277 real diagnoses via the events-log join, so the metric
  is exercisable on real data the moment a graduation row is applied.)

### from: Tire-runs — SSRF depth
  - ~~**SSRF depth.**~~ **FIXED 2026-07-29** (autonomous batch; codex
    trio unanimous DO_NOW). Resolve-then-pin shipped at the HTTP layer:
    `_vet_resolved_ips` (all-or-nothing — a [public, private] split
    answer is treated as rebinding-shaped and refused outright) +
    `_PinnedHTTP(S)Connection` (connects to the vetted IP, never a
    second DNS answer; TLS still validates against the NAME) +
    `_SafeRedirectHandler` (re-gates every hop). Writing it surfaced a
    WORSE pre-existing hole: `fetch_url_content`'s Tier-3 raw fetch
    called `_http_get_bytes` with no gate at all, so a private-literal
    URL the proxy tiers refused fell through to a DIRECT fetch —
    `_http_get_bytes` now owns its own gate. `_resolve_redirect`'s
    manual hop-follower pinned + gated too. 17 regression tests; live
    check: example.com 200 via pin, 169.254.169.254 refused.

### from: Tire-runs — viz symlink containment
  - ~~**`viz_server` symlink containment.**~~ **FIXED 2026-07-28.**
    Containment was checked against the runs root only, and every denied
    sibling (`source/`, `fetch-raw/`, `metadata.json`) also lives under
    that root — so a `build/report.html` symlinked to
    `../fetch-raw/<digest>.html` was served, handing unscrubbed
    third-party HTML to the operator's browser as same-origin content.
    Now checked against the specific allowed subtree, AND the subtree
    itself must sit at its literal path: writing the test surfaced a
    second hole where symlinking `<run>/build` -> `<run>/source` put every
    denied sibling back in reach. 5 regression tests.

### from: Tire-runs — NOW lost-the-plot stamp
  - *NOW-lane lost-the-plot stamp* — **CLOSED 2026-08-02.** The
    provenance branch of `_verify_now_outcome` now stamps
    `lost-the-plot` + evidence, and `_run_now` forwards it to all three
    homes the agenda rail uses (result dict → run metadata → outcome
    row; `record_outcome` had accepted the kwargs since the verdict
    shipped, the NOW call site just never passed them). The 2026-07-27
    "5-line add" estimate held.
    **Two things worth keeping from the fix.** (1) It was never a
    vocabulary gap: `stop_verdicts.py`'s own docstring already names the
    provenance guard as a lost-the-plot source, and the agenda twin has
    stamped it all along — same evidence, typed on one lane and untyped
    on the other. (2) The **judge** branch (`fulfilled: false`) is
    deliberately left unstamped, with a pin saying so: a one-shot "I
    couldn't" carries no observation about the map, and the four
    verdicts are observations about the map. Inventing a fifth verdict
    for it would dilute the taxonomy the way `external-interrupt` was
    deliberately kept out of `GOAL_VERDICTS`.
    Sibling hole closed in the same commit: `record_outcome` validated
    `pause_reason` against its vocabulary but **not** `stop_verdict` —
    the 2026-07-31 slice-1 review (#6, "stores disagreeing instead of
    rejecting at ingress") fixed one field and not its twin. Reachable
    the moment the NOW lane became a direct caller.

### from: Tire-runs — outcome-row ordering gap
  - *Outcome-row ordering gap* — CLOSED by the adversarial-review round
    (2026-07-27, Skeptic finding 2): finalize now re-stamps the
    already-written row post-hoc when a merge-back block adds a verdict,
    and `is_learnable_outcome` fails closed on external-interrupt
    without a positive goal verdict. The row-write ordering itself is
    unchanged (load-bearing for lesson dedup).

### from: Tire-runs — skill auto-promote validation
  - ~~[JEREMY DECISION] Skill auto-promote validation is dead code~~ —
    **RESOLVED 2026-08-01, Jeremy: "let's fix the promote validation."**
    Adapter wired at the evolver call site (production passes it via
    loop_finalize → run_skill_maintenance), so the Voyager harness +
    repair loop are live for the first time; promotions can newly fail
    and be held provisional. Fail-open kept (validation error degrades
    to the old numeric-gates-only behavior) but stamped: SKILL_PROMOTED
    events carry `validation: passed|unjudged|skipped`. Pins in
    test_skills.py::TestSkillValidationHarness.

### from: Tire-runs — recon-aware readout
  - ~~*Recon-aware readout*~~ — CLOSED 2026-08-01 (same day the cuts
    fix made the corpus real): `discretion_readout.recon_summary` reads
    `runs/*/build/loop-*-log.json` step rows (the durable carrier is the
    step TEXT — the tag is the schema); reports recon share, VOI-missing
    rate, and done/blocked split by flavor with SINCE-FIRST-SEEN
    denominators (the LT retraction lesson — pre-emission loops are
    counted, shown, never pooled in). Per-step VERIFY verdicts still
    aren't durably joined to step identity — step status is the proxy,
    named in "Not computable today". A typed StepOutcome field remains
    deliberately NOT added (dual source of truth; 2026-08-01 adversarial
    review F2 rejected).

### from: Tire-runs — cuts-first probes
  - ~~*Cuts-first probes untagged*~~ — CLOSED 2026-08-01: live smokes
    showed the cuts lane intercepts exactly the survey-shaped goals and
    shipped textbook recon probes untagged (the lane returns before the
    taught decompose). Fixed deterministically in `_cuts_plan` (VOI =
    the boundary plan), gated on the same `planner.recon_flavor`
    emission killswitch; boundary-expansion carry verified (probe text
    rides `- Probe:` evidence lines verbatim, nothing exact-matches);
    milestone expansion exempts recon steps (text rebuilds strand the
    tag — same class as the `_shape_steps` skip).
  - ~~*`strip_recon_tag` has no runtime consumer*~~ — CLOSED 2026-08-01:
    the recon readout's sample lines are the consumer (step text rendered
    stripped, the decision as its own column). Viz step lists /
    thread-brain lines remain tag-inclusive — fine, the tag is
    informative there.

---

## Run-card cost lane ~2×-low estimate — truth lane plumbed through (FOUND 2026-08-09, SHIPPED same day)

`run_card.total_cost_usd` = `spend_for_loops` = sum of
`step-costs.jsonl`, whose rows were `estimate_cost()` outputs: cache
reads at 0.1× but **no cache-creation-write term** (billed 1.25×; the
subprocess backend re-writes cache every step). Full-history measurement
at close (43 successful runs with both figures): **mean card/truth
ratio 0.44** — worse than the −37% the two spot runs suggested — card
p90 **$2.36** vs truth p90 **$4.95**. Both the persisted lane AND the
live breaker ran on the same low estimator, so the auto thresholds and
the running total were consistently-low together (partially cancelling;
readouts comparing card vs loop-log were the visible casualty).

Fix (all lanes truth-preferring, estimate as fallback):

- `record_step_cost` gained `provider_cost_usd`; when > 0 it becomes
  the row's `cost_usd` (`cost_source: "provider"`, estimator kept as
  `estimated_cost_usd` so drift stays measurable). All three loop call
  sites (sequential post-step, blocked, parallel batch) pass the step
  outcome's backend-reported figure.
- The live budget accumulator (loop_execute sequential + batch paths)
  prefers the same figure — a truth-priced threshold judged against an
  estimate-priced running total would have made the breaker ~2× looser
  in real terms. `_run_parallel_batch` returns a provider-cost delta.
- Pinned: provider-wins + negative-junk fallback + spend_for_loops
  inheritance (test_metrics), breaker-prefers-provider loop pin
  (test_agent_loop).

Transitional note: the auto lines re-anchor as truth-priced cards
accrue (mtime-sorted newest 200). Until then p90 sits at the mixed-era
$2.36 → warn (`max($2.50, p90)`) fires earlier against truth-priced
runs — warn is notify-only, and the extension ladder means breaches
extend rather than kill, so the skew is noise, not risk. Expected
steady state on today's data: warn ≈ $4.95, breaker ≈ max($10, ~$19.8).

## Untracked test-artifact skills in repo skills/ — writer found + fixed, files relocated (FOUND 2026-08-08, SHIPPED 2026-08-09)

Seven (grown to **22** by close — the writer was live) untracked `.md`
files accumulating in the repo's `skills/` dir, same isolation-leak
class as the repo-local `memory/` writes (fixed 2026-08-06).

**Writer identified:** `skills.py` promotion sweep (provisional →
established) auto-exports each promoted skill as a curated SKILL.md via
`skill_loader.export_skill_as_markdown(s)` — and that function's
default target was the repo-absolute `SKILLS_DIR`, not the workspace
overlay. Two lanes leaked through it: live-run promotions, and
unpatched test-suite promotions (a repo-absolute path ignores tmp
workspaces entirely — fresh files appeared during suite runs the
morning of close, which is how the count grew 7 → 22). The default
also bypassed the "human review gates repo skills/ graduation"
doctrine (run_curation.py's ship-set rule).

**Fix:** default target is now `config.skills_dir()` (the workspace
overlay, which respects `MARO_WORKSPACE`/`OPENCLAW_WORKSPACE`);
explicit `skills_dir=` remains for deliberate ship-set exports. Pinned
(`test_default_target_is_workspace_overlay_not_repo`). Loader
resolution is workspace → repo, so overlay exports inject identically.

**Files:** all 22 relocated (not deleted — data retention) from repo
`skills/` to `~/.maro/workspace/skills/`, zero name collisions, repo
tree clean. Same basenames at the new path if any ever needs to come
back: analyzer, cli_list_test_skill, codebase_quality_audit,
deduplication-first_issue_filing, failure_tracker, full_field_test,
github_issue_sweep, hash_storage_test, increment_test,
incremental_bug_fix_run, issue_filing_workflow, iterative_build,
n1_query_fix, polymarket_analyzer, prioritized_github_issue_fix,
pydantic_validation_schema_fix, quality_criteria_validate,
repository_code_audit, repository_code_survey, research_tool,
test-driven_bug_fix_cycle, test-driven_code_fix.

## Planner non-action item types — world facts — SHIPPED (slice 1 2026-08-08, slices 2+3 2026-08-09)

(Jeremy ask, §4c decision batch 2026-08-02: *"we need to add non-action
types to the planner. I think 2 types of world facts:
anecdotal/accidentally found and hypothesis type findings (pattern
recognition/ideas)."*) Plans were action-only; a run that stumbled onto
a fact ("archive X is blocked") or formed a pattern hypothesis had no
plan-native place to put it — it leaked into step prose or died with
the step.

Design sketch written 2026-08-03 (`docs/WORLD_FACTS_DESIGN.md`,
reading-queue row same day); §7 decisions taken by Jeremy 2026-08-06
(hypothesis quarantine mirrors the provenance pattern; planner FACT:
emission last; cap sizes build-time tuning). Shipped in three slices
exactly as ordered:

1. **Capture + ledger + injection** (go-nuts stretch chunk 6, 97bb780):
   completion-tool `world_facts` side-channel mirroring the decisions
   directive, run-scoped ledger on LoopContext with checkpoint carry,
   anecdotal-only render (§7.1 hypothesis quarantine), injection_guard
   at every ingress (fail-closed), `world_facts.enabled` default ON.
2. **Finalize landing** (2026-08-09): `land_facts` — anecdotal →
   knowledge-bridge CANDIDATE nodes (V3 promotion = the
   generalizability filter; the §4 teaching mint stays dormant, bridge
   is the landing per the design's census); hypothesis →
   `observe_pattern` with `world_fact:<loop_id>` provenance.
   Verdict-independent; capped 5/3; idempotent per run dir
   (`world_facts_landed` metadata stamp — a demoted-then-resumed run
   must not self-confirm its own hypothesis to the standing-rule
   threshold); one WORLD_FACTS_LANDED event. Watch item: the
   promotion bar is the lane's RULE_PROMOTE_CONFIRMATIONS=2 — two
   cross-run declarations of the same guess promote a rule; pinned,
   contradiction-check + refight are the backstops.
3. **Planner FACT: emission** (2026-08-09): `WORLD_FACT_RULES` taught
   behind `planner.world_facts` (emission only; `parse_steps` plucks
   FACT entries unconditionally — never steps, never counted against
   max_steps). Seeded facts carry sticky `source="planner"` and are
   REFUSED by land_facts: planner facts come from injected context, so
   landing them back would launder stored knowledge into fresh
   confidence (provenance-stamp lesson); a step restating a rendered
   fact never upgrades provenance. Named residual: loop_execute
   re-decompose lanes (boundary/milestone/replan) drop rather than
   ledger FACT lines.

Full state + falsifiers: `docs/WORLD_FACTS_DESIGN.md`. Defaults rows:
`world_facts.enabled`, `planner.world_facts`. Tests:
`tests/test_world_facts.py` (47 pins).

---

## Skill pedigree + discovery metadata — SHIPPED 2026-08-08 (go-nuts stretch chunk 4)

(opened 2026-08-08, **Jeremy:** *"seems important in both a 'this pattern
matches' sort of way as well as domain-relevant sorts of ways… a little
surprised we don't have some of that metadata already; otherwise skills are
difficult to discover"*). Surfaced by the scout read: a world-sourced skill
would have no honest provenance home. Pairs with match-tier telemetry
(both "record what we already know but throw away").

**Design call on the item's should-we** (origin stamp vs generalizing
`imported` into a `provenance` dict): **stamp, mirroring lessons'
`minted_from`**. `imported` is portable-learning's §3 contract (claimed-stat
laundering defense rides on its exact shape) — generalizing it would churn
pack import/export for zero new information. Three new `Skill` fields:

- `origin`: `"crystallized"` (extract_skills + run_curation companion
  mints) / `"synthesized"` (synthesize_skill + evolver skill_pattern
  suggestions) / `"imported"` (pack import; claimed kind survives as
  `imported["original_origin"]`, same pattern as `original_tier`) / `""`
  legacy. Derivation-at-read in `dict_to_skill`: blank origin + imported
  dict present → `"imported"` (certain evidence); crystallized-vs-synthesized
  is NOT retro-derivable (both carry `source_loop_ids`) so legacy rows stay
  `""` — positive-evidence, no guessing. "Graduated" kind turned out not to
  exist: graduation.py mints no jsonl skills. The curated SKILL.md store is
  separate and self-describing (frontmatter); untouched.
- `domain` + `tags`: discovery axis, filled by the extraction/synthesis LLM
  calls (JSON shapes extended — same call, no new spend; normalized
  lowercase, capped 40 chars / 6 tags). **Live consumers in the same
  change** (consumer-first): tags count as trigger phrases in keyword-tier
  matching (substring-in-goal only — reverse test would be noise), tags +
  domain join the TF-IDF document and `assign_island` text. A/B challenger
  clones round-trip through `skill_to_dict`/`dict_to_skill` so variants
  inherit pedigree free.

Pack export copies `skills.jsonl` raw, so pedigree travels in packs with
zero export changes. Tests: TestSkillPedigree (round-trip, legacy defaults,
imported-derivation, mint stamps, keyword-tag match, tfidf doc), synthesis
stamp assertions, pack import origin/discovery carry. No config keys.
Backfill of the ~376 legacy rows deliberately NOT done (would be guessing);
upgrade edge: a one-shot LLM backfill pass if discovery quality wants it.

---

## Adversarial review of items 5 + 5b — fixes SHIPPED 2026-08-06 (same session)

Two parallel review agents over 83c208c + ed7cea3; every acted-on claim
re-verified against code first (all key claims held this round). One
real HIGH design flaw plus a cluster of hardening finds:

- [x] **HIGH — repo-local mode leaked into the production workspace**:
  ed7cea3 routed `checkpoint._checkpoint_dir` and
  `hooks._default_hooks_path` through `config.workspace_root()`
  unconditionally, ignoring the MARO_ORCH_ROOT-only pin (build-loop.sh
  no-arg repo-local mode, containers). Worst case was hooks:
  `loop_init` loads AND EXECUTES the registry every agent loop, so a
  repo-local loop would have executed *production* hooks. Fix: new
  `orch_items.data_root()` — orch_root() when MARO_ORCH_ROOT is the
  only pin, else `config.workspace_root()`; checkpoint + hooks now
  route through it. Pinned by tests (checkpoint dir + hooks path under
  MARO_ORCH_ROOT-only).
- [x] **sheriff probed the wrong root**: workspace_writable health probe
  still checked orch_root(); now probes `config.workspace_root()` —
  the tree the writers actually land in.
- [x] **pip-install repo_root() hazard**: flat py-modules install makes
  `repo_root()` = the venv lib dir; git subprocesses there walk UP to
  whatever repo contains the venv, and convo_miner could inject into a
  stranger's BACKLOG.md. Guards: `.git`-existence checks in
  captains_log + convo_miner `scan_git_log`; `inject_into_backlog`
  default path now requires an *existing* BACKLOG.md.
- [x] **list_checkpoints dup gap**: same loop_id at current + pre-move
  locations produced two entries; now deduped, newest-mtime wins
  (matches `_find_checkpoint_path` preference). Pinned by test.
- [x] **stale-doc sweep**: SUBSTRATE_INTEGRATION §workspace-sanitization
  rewritten (the "any pin flips into the prototypes layout" claim was
  backwards post-unification), maro-dispatch.sh + hermes dispatch.py
  comments updated (the unset-all is still correct — a pin now means
  "the workspace IS <ws>", which is the wrong workspace for a
  dispatch), CLAUDE.md memory/ row, BACKEND_RESILIENCE_DESIGN
  checkpoint-path gaps marked CLOSED, checkpoint.py "legacy" wording
  disambiguated (pre-move dir vs non-run-dir).

**Accepted residuals (deliberate, reviewed):**
- Old-location fallbacks are env-relative (orch_root()-derived, never
  repo_root()-pierced): a PINNED env does not see files written
  unpinned. Each workspace owns its data; piercing the pin would leak
  the real repo's 52 checkpoints into every isolated test env.
- Orphaned shadow stores under `<ws>/prototypes/maro-orchestration/`
  from the pre-unification era are not auto-migrated (retention
  decree); on this box they held no live data.
- `convo_miner.inject_into_backlog` still targets the real BACKLOG.md
  by design — CLI-only surface, zero programmatic callers, and now
  gated on the file already existing.
- Refuted/downgraded: R1-F4 (inject under pins) — manual-command edge
  only, no programmatic callers.

**Known follow-on (not fixed here, noting for BACKLOG 5b residual when
it unlocks):** `orch.py:413` validation-summary fallback writer still
anchors on orch_root; fold into the deferred guard-drop cleanup.

## 5b writers migration: runtime data off orch_root — SHIPPED 2026-08-06 (same session as item 5)

Follow-on from the resolution unification: five consumers still anchored
runtime data on `orch_root()`, so production (unpinned → orch_root =
repo) wrote state into the checkout — the gitignored `artifacts/` and
`checkpoints/` dirs full of real run data were the evidence. Jeremy
green-lit the writers + `repo_root()` slice, deferring the churny
orch_root guard-drop (residual stays in BACKLOG as 5b).

- [x] **`repo_root()` introduced** (orch_items): the source checkout,
  pin-independent — for CODE concerns. Moved onto it: captains_log git
  cwd, convo_miner `scan_git_log` cwd + `scan_maro_memory` +
  BACKLOG.md locator (all were orch_root — correct only by coincidence
  when nothing was pinned). Pin: repo_root ignores every pin and must
  contain src/agent_loop.py.
- [x] **hooks registry** → `<ws>/.hooks/hooks.json`, legacy
  read-fallback: an existing registry at `orch_root()/.hooks` keeps
  being used in place (reads AND writes — never auto-migrated).
  Nothing existed at either location on this box.
- [x] **persona agents manifest** → `output_root()/agents/` (generated
  artifact, only consumer is the CLI command that prints the path — no
  fallback needed).
- [x] **projectless director logs** → `output_root()/artifacts/director/`
  (write-only; old repo files left in place). Existing metadata test
  switched from orch_root-joining to `resolve_artifact_path()` — the
  canonical display-form inverse.
- [x] **non-run-dir checkpoints** → `<ws>/checkpoints/`; old
  `orch_root()/checkpoints` stays readable, consumable
  (replay-protection stamp works in place), listable, and
  user-deletable via `_find_checkpoint_path`/`_old_checkpoint_dir` —
  which is never mkdir'd (pinned by test: fallback resolution must not
  recreate the old dir).
- Pins in tests/test_checkpoint_paths.py (new), test_hooks.py,
  test_persona.py, test_director.py, test_config.py
  (TestWorkspacePinLayout::test_repo_root_is_the_checkout_regardless_of_pins).

## Shadow-workspace hijack / resolution unification — SHIPPED 2026-08-06 (census item 5)

Found via the live-writer census follow-on (record:
`docs/history/2026-08-06-live-writer-census.md`): pinning a legacy
workspace var (`OPENCLAW_WORKSPACE`/`WORKSPACE_ROOT`) — even to the REAL
workspace — routed `orch_items.memory_dir()`/`projects_root()`/
`output_root()` into `<ws>/prototypes/maro-orchestration/`
unconditionally, violating memory_dir's own docstring invariant ("must
resolve to the SAME directory as config.memory_dir()"). Discovered when
a dry-run probe pinned `OPENCLAW_WORKSPACE=~/.maro/workspace` and read
an empty shadow skills store (0 rows vs 134 real). The husk's
`outcomes.jsonl.lock` showed a live write 2026-08-02; `build-loop.sh`
with a workspace arg was the known live pinner. `deploy/openclaw/
maro-dispatch.sh` had already been defensively unsetting all five vars
against exactly this bug.

- [x] **Husk archived** (Jeremy's call) to
  `~/.maro/workspace/archive/prototypes-husk-2026-08-06/` — 25 files
  incl. March poe-orchestration NOW artifacts, retention decree
  honored (e22db73).
- [x] **Resolution unified**: `_legacy_ws_pinned()` →
  `_orch_root_pinned()` — ANY workspace pin (canonical or legacy) now
  means "the workspace IS x", matching `config.workspace_root()` which
  always treated all three vars identically; the 2026-07-03 BACKLOG #-1
  unification extended to the legacy vars. The one remaining
  orch-layout data pin: `MARO_ORCH_ROOT` set alone (containers /
  build-loop no-arg repo-local mode — deliberate). `orch_root()` itself
  unchanged (code-root resolver; see follow-up 5b for making it pure).
- [x] **autonomy.py rode along**: `_config_path()` joined
  `orch_root()/memory` directly instead of `memory_dir()` — autonomy
  state split from the learning data in production. Now routes through
  `memory_dir()` (autonomy.json existed nowhere on disk, so the move
  was free).
- [x] **build-loop.sh** now exports `MARO_WORKSPACE` (says what it
  means) for a workspace arg; smoke.sh's vestigial `OPENCLAW_WORKSPACE`
  export dropped.
- [x] **Contract tests rewritten**: `TestWorkspacePinLayout` legacy-pin
  test flipped to workspace layout + four new pins (WORKSPACE_ROOT pin,
  legacy-agrees-with-config, MARO_ORCH_ROOT-only keeps orch layout,
  workspace-pin-wins-over-orch-root-pin for data). Test blast radius
  was tiny: conftest's autouse `MARO_WORKSPACE=tmp_path` already
  shadowed legacy setenvs everywhere except the dedicated contract test
  (only `test_phase21.py` clears MARO_WORKSPACE, and only to test
  `config.workspace_root()` itself — unaffected).

## Battery side-finds V3/V4 (2026-07-21 phase-05 battery) — both SHIPPED, moved from BACKLOG 2026-08-02

Evidence in `docs/history/2026-07-21-phase05-battery.md`.

- [x] **V3 — NODE_CANDIDATE promote path. SHIPPED 2026-08-02.** Original find: bridged knowledge nodes are born `NODE_CANDIDATE` (conf 0.3) and nothing ever promotes them; live readers filter ACTIVE-only, so every live write was invisible to every live read. Liveness pin shipped 2026-07-29 (`test_knowledge_bridge.py::TestCandidateInvisibilityPin`) with a structural tripwire against unwired promotion symbols. Promotion criteria decided 2026-08-02 (Jeremy, decree-class): **mirror skills** — "same as skills, promoted to maro-local usable, up to the user to pick permanence"; the user gates permanent-vs-useful ("I think we need the user involved for permanent vs useful"); UX refinement later ("smells like auto-mode settings for prompting"; possible extra layer/process down the road). **Build (same day):** `knowledge_web.promote_knowledge_candidates()` — gates times_applied ≥ 2 (bridge dedup-upsert re-observations; the injection bump touches ACTIVE only, so on candidates this counts independent re-derivations) AND confidence ≥ 0.4 (epsilon-tolerant; two float bumps land at 0.3999…), lf- reference nodes never promote, optional LLM gate stamped passed/unjudged/skipped (skills' fail-open contract), cap 10/sweep, `KNOWLEDGE_NODE_PROMOTED` event (census 80), rides `run_skill_maintenance` beside skill promotion with the same adapter. Pin rewritten to exercise earning: born invisible → fresh mint doesn't qualify → re-observed 2× promotes and surfaces in the live recall chain. The permanent-vs-useful user gate remains a deferred UX layer (revisit with the auto-mode-settings discussion).
- [x] **V4 — knowledge.py canon-candidate dict-vs-attr + phantom `canonize` command.** SHIPPED 2026-07-29 (autonomous batch). Degradation verified worse than noted: `get_canon_candidates` returns dict rows, so `_stage3_data`'s attr access errored exactly and only when candidates existed (empty list → clean zeros; non-empty → `{"error": ...}`) — the report broke precisely when there was something to report. Fixed to dict access (rows arrive pre-sorted; redundant re-sort dropped) + non-empty pin test (old tests only exercised the empty path, and one asserted `or "error" in result`). Phantom `canonize` hint replaced with the honest human gate ("no auto-writer by design; promote by editing AGENTS.md by hand" — matches `get_canon_candidates`' never-auto-written docstring). Building a real `canonize` AGENTS.md-writer would be a design decision (entry format, placement) — deliberately not taken.

## Green-lit 2026-08-02 (Jeremy, surprise-read follow-through) — two lesson-store chunks, both SHIPPED 2026-08-02

Moved from BACKLOG 2026-08-02 (the move itself waited a day for the file
to stop being another session's dirty copy).

- [x] **What-not-how mint-form pass. SHIPPED 2026-08-02.** Shared
  `_LESSON_FORM_RULES` block (observation-not-procedure, repeated-failure-
  as-evidence, no self-credit without a named observation, procedure form
  only when the goal asked for one) composed into `_REFLECT_SYSTEM` +
  `_STEP_LESSON_SYSTEM` ("actionable" dropped from both;
  `extract_deferred_lessons` rides `_REFLECT_SYSTEM`); thinkback got a
  scoped clause — `key_lessons` mint as observations, step reviews /
  retry_strategy stay prescriptive (the decree's "asking for work" case).
  Deterministic templates reframed via `_recovery_plan_lesson_text` /
  `_auto_diagnosis_lesson_text` (diagnosis stated as observation, action
  marked "advisor-proposed (unverified)"; same-prefix so existing pins
  hold, deterministic so dedup-reinforce still works). Structural M14 fix:
  every reflect/deferred/finalize tiered mint now stamps
  `evidence_sources=[loop:<id>]` (they all minted `[]` before — Phase 60's
  citation penalty had nothing to reward), and dedup re-sightings merge
  their evidence refs (cap 8, contested rows excluded) so a row
  accumulates *where* it repeated, not just how often. Side-finds: seed
  reader could serve a contested LONG lesson (L4) as the style example —
  filtered. Live acceptance (real adapter): M13-shape minted the
  disjoint-sources requirement as observation; M9-shape minted "blocker
  in both this attempt and the prior dispatch; recurred despite retry";
  M14-shape produced no self-credit clause. 15 pins in
  `tests/test_mint_form.py`. Residual: LLM output can still carry a soft
  "should" grounded in named evidence — acceptable form, not the defect.
- [x] **Retirement-by-contradiction for lessons. SHIPPED 2026-08-02**
  (same session as the green-light). `TieredLesson.contested` +
  `contest_lesson()` mirror the standing-rule grey flip: out of every
  injection surface (both stores — flat ledger included via UU-4 shared
  ids), never promotes/confirms, dedup re-sightings count
  `times_reinforced` only (score + decay anchor freeze so MEDIUM retires
  on schedule; for decay-free LONG this IS retirement). Adjudication's
  lesson-branch honest no-op replaced with a real contest; operator verb
  `maro-memory contest`. Acceptance corpus applied to the live store:
  all six contested with Jeremy's verbatim reads as reasons (L4=6287e494,
  M6=9d6b63fe, M8=c304b9b2, M9=c85c9a09, M13=47e8f5e3, M14=655ea616);
  injection/query leak-check clean. Tests
  `tests/test_lesson_contested.py`; arch skill updated.
  **Deliberate V1 cut:** no un-contest path — contested is sticky until
  a lesson-refight slice exists (rules have `refight_rule`; a future
  slice can use the frozen `times_reinforced` re-sighting count as its
  evidence input). Competence-redundancy decay decree (2026-07-31)
  overlap deferred with it.

*Older records (2026-04 → the 2026-08-05 archived batch) rotated to `docs/history/backlog-done-2026-04-to-08-p{1,2,3}.md` on 2026-08-16 — verbatim, original order, still ingested by dev-recall. Rotation, not deletion.*

## Doorless canon threshold (V3's lesson-side twin) — completed 2026-08-13

Surfaced 2026-08-03 (LeAct contrast audit), shipped 2026-08-13 (1f34ca9)
as the second piece of Jeremy's 2026-08-12 LeAct go. Original problem:
V3 gave knowledge NODES an earned promote path
(`promote_knowledge_candidates`) 2026-08-02; the canon-LESSON path never
got one — `get_canon_candidates` surfaced rows at
`CANON_APPLY_THRESHOLD = 10` recommending "PROMOTE TO AGENTS.md" while
no `promote_canon_*` existed anywhere (verified 2026-08-03, re-verified
2026-08-08; all consumers were readers). It stopped being cosmetic when
the receipt write-back (2026-07-29) made `times_applied` actually accrue,
so candidates would genuinely arrive at a threshold with no door — and
the consumer-first read is that a threshold with no door is worse than
no threshold. The 2026-08-03 Opus contrast praised the node path without
noticing this twin, which is how it survived two reviews.

Door-or-no-door decided: DOOR, and the sequencing note ("the Δ-gate
would give this path a better gate than V3's if it lands first") cashed
in — the Δ-gate had landed by build time. Shape shipped:
`promote_canon_lesson(lesson_id, dry_run)` operator verb (CLI
`maro-memory canon-promote`) appends the lesson to playbook.md's Canon
section (the always-active ranked-injection surface — the honest maro
equivalent of the old "AGENTS.md identity" language) and stamps the row
`canon={promoted_at, target}` so it leaves the candidate list.
Eligibility delegates wholesale to `get_canon_candidates` (one bar
definition), which now ALSO excludes Δ-demoted and Δ-inert rows
(measured harmful/redundant ≠ identity) and annotates `measured_delta`
when a positive replay measurement exists. Human stays in the loop by
construction: the evolver surfaces candidates as Suggestions, nothing
ambient calls the verb. CANON_PROMOTED captain's-log event;
CANON_CANDIDATE stays reserved for a future candidate-surfacing emitter.


### Execution receipts — six cross-model review findings ADJUDICATED 2026-08-13 (all six verified real against the tree; 5 fixed, 1 honest-labeled)

Adjudication (verify-before-fix pass; 6/6 code claims held — reviewer
hallucination 0 again):
1. **FIXED — zero-executions is now a POSITIVE state.** `load_receipts`
   counts `readable_calls` and `capture_calls` (backend=="subprocess",
   the only tool-event-relaying adapter — verified against a live run:
   event names Bash/Read/Write, backend stamps on every record). A
   clean capture-capable record with zero shell executions renders
   "RECORD PRESENT, ZERO executions … does not support that claim";
   unknown/missing backend stays conservative no-signal.
2. **HONEST-LABELED (v1) — attempt-blindness named at the surface.**
   Digest carries "Scope: RUN-WIDE — may span restarted/resumed
   attempts; not scoped to the final attempt", and the pass-audit
   system bullet tells the judge to tie receipts to the claim, not the
   run. Attempt-SCOPED records need recorder-side loop stamps in
   runs.record_call — real fix, upgrade edge, not built (recorder
   schema change; do it when a live cross-attempt false-support case
   shows up or the recorder is next open anyway).
3. **FIXED — shell-tool events only.** Rows require event
   name=="Bash" (`_SHELL_TOOL_NAMES`); an MCP/custom tool with a
   `command` arg is not an execution; a NAMELESS command event is
   shape-corrupt and counts malformed.
4. **FIXED — marked head/tail clips.** `_clip()` renders
   `head …[+N chars]… tail40` at every listing site (the `|| true;
   echo '100 passed'` suffix survives); capture-time output truncation
   stamps `output_clipped` → "…[output continues]" marker.
5. **FIXED — trust claim matches mechanism.** Prompt now says
   recorder-written / least-reachable / NOT tamper-proof (no hash
   chain; host-lane filesystem access exists) — "cannot edit the
   record" removed here and in the closure_verify bullet. Hash-chained
   or out-of-workspace records stay future work.
6. **FIXED as accepted-v1-scope, stated where the auditor reads it.**
   Non-capturing-backend records render "N call(s) recorded, but none
   rode a tool-event-capturing backend — receipts cover the subprocess
   lane only in v1". Codex-lane capture remains an upgrade edge.
12 new pins (TestReviewRound4). Remaining upgrade edges from this
round: recorder-side attempt/loop stamps (2), hash-chained or
out-of-workspace records (5), codex-lane tool-event capture (6).

**Round 5 (2026-08-13, skeptic re-review of the fixes — 3/3 real,
reviewer hallucination 0 for the fifth round running; all FIXED):**
(a) mixed-backend records no longer fire the ZERO-executions
refutation — full capture coverage required, else "PARTIAL COVERAGE …
{blind} call(s) invisible to receipts / treat as no signal"; (b) a
shell event missing/empty its command is shape corruption past the
name filter (counts malformed → record incomplete, never affirmative
absence); (c) module docstring's "cannot forge / cannot reach
post-hoc" overclaim rewritten to least-reachable/strong-corroboration
(pinned so a revert trips). +8 pins (TestReviewRound5). Fixpoint
looks reached: round 5 findings were all consequences of round 4's
own new code, none reach back further.

**MOVED TO DONE 2026-08-13 (overnight):** round 5 reached fixpoint (r5 findings were all artifacts of r4's own code); landed 32e3455 + 5c0e714. Upgrade edges (recorder-side attempt/loop stamps, hash-chained/out-of-workspace records, codex-lane capture) are filed in the item body above — evidence-gated, not owed.
