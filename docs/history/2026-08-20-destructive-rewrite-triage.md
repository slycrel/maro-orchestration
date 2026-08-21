---
status: record
---

# Finishing the destructive-rewrite sweep — the 70-site triage (2026-08-20)

`scripts/scan_destructive_rewrites.py` was written on 2026-08-17 to find the
**destructive subset** of the silent-drop census: not silence, but *drop +
write-back*. A row dropped from a read is recoverable — the bytes are still on
disk. A row dropped from a loop whose result is written back is gone.

That chunk fixed its top hit (`skills.py`) and left the rest of the scanner's
output explicitly untriaged in BACKLOG. This record closes that: all 70 RISK
sites read by hand — **three real defects (6 scanner sites), 64 false
positives** — with the reason written down so nobody re-reads them next month.

## The three real ones (all probed live before the fix)

### 1. `doctor.cleanup_workspace_skills` — destructive

Third instance of the skills-store family, and the sharpest framing of it yet,
because this is a **repair verb**: an operator runs it *because* the store looks
wrong.

| probe | before |
|---|---|
| one non-UTF-8 byte in skills.jsonl | `UnicodeDecodeError` — verb dies |
| one *truncated* row (what a crashed append actually leaves) | 4 lines in, **3 out** — row destroyed |
| what the operator was told | `Cleaned: 3 skills remain (0 stale-hash + 0 duplicate(s) removed, 0 total)` |

The last line is the worst part. The summary counts only what the verb *meant*
to remove, so a destroyed row is reported as "0 removed". The fix strands and
carries unreadable rows verbatim, prints them as kept, and writes under the
file's lock via `atomic_write(errors="surrogateescape")` — the bare `write_text`
it replaced also raced `save_skill`, which fires on every skill match.

**Side-find, fixed here:** this verb *and* doctor's duplicate check both
hardcoded `~/.maro/workspace/memory/skills.jsonl`, while every runtime caller
resolves through `config.workspace_root()`. Under a `MARO_WORKSPACE` override,
doctor reported on — and rewrote — a store the running system was not using.
Now `_workspace_skills_path()`; byte-identical behaviour with no override set.

### 2. `interrupt.InterruptQueue` — the operator's control channel

`_read_lines()` strict-decoded the queue, and it feeds `peek()`, which **gates**
`poll()`, `clear()` and `is_empty()`. So one crash-torn append raised
`UnicodeDecodeError` out of every consumer: stop/pivot messages, and the kill
switch's own STOP interrupt, stopped reaching a running loop until someone
repaired the file by hand.

Not silent — `loop_post_step` already logs an ERROR per step, and its comment
says exactly why ("silent failure means user-initiated interrupts could be
silently dropped while the loop keeps running"). But total: the channel stayed
dead. One torn byte should cost one interrupt, and now does.

Both `_mark_applied` merges already had the preserve branch, but re-dumped
every row they *did* parse — so a byte-tainted-but-valid twin came back as clean
`\udcXX` escapes and the corruption signal was erased forever. `loads_clean`
refuses taint, which sends those rows down the preserve branch that was already
there.

### 3. `gc_memory._gc_outcomes` — the r4 deferral, closed

A strict decode inside `except Exception: return 0, 0, 0`.

```
GC healthy   : (2, 1, 0)
GC after one torn append : (0, 0, 0)
```

"Nothing to collect", forever, with **no log line at all**, while the store grew
without bound. The trim itself was already verbatim-preserve, so this was the
silent-full-disable flavour rather than the destructive one. Announced read; and
a byte-tainted row's timestamp cannot be trusted to authorize its own deletion,
so taint now joins the keep-conservatively branch with everything else.

## The 64 false positives, by why

*(Counts below are the original 70-site scan. Adversarial r3 taught the
scanner a second framing idiom the same day, which added 5 more sites —
3 markdown, 2 read-only, all in `playbook.py` — for 75 triaged / 69 FPs.
`scripts/triage_manifest.py` is the live mapping; these tables are the
narrative.)*

The scanner's docstring already warns that OK is a hint and that markdown and
single-object rewrites match its write markers. That is what most of these are.

| shape | count | examples | why it is not the bug |
|---|---|---|---|
| markdown / prose rewrite | 10 (+3 r3) | `orch_items.parse_next` + `append_next_items`, `thread_brain._append_under`, `boot_protocol._read_completed_from_next`, `convo_miner.scan_maro_memory`, `pack._append_conflicts_note` | no JSON parse; every line is carried, and the "drop" is a regex non-match, not a discard |
| subprocess-output parser | 4 | `heartbeat._is_interactive_session_active`, `build_loop_runner._worker_session_already_active`, `container_exec._reseed_probe`, `worktree._sanitize_untrusted_git` | the parsed text is `pgrep`/`docker` stdout, not a durable store |
| stream / LLM-output parser | 9 | `llm._parse_stream_json`, `llm._stream_events`, `orch_bridges._tail_lines`, `orch_bridges._extract_session_result_from_text` | same — nothing on disk is being rebuilt |
| derived-index rebuild | 3 | `memory_ledger._update_memory_index`, `loop_report._render_devlog_html`, `portability.main` | the written file is generated *from* the source, and regenerating is the repair |
| read-only loader | 26 (+2 r3) | `knowledge_lens.load_standing_rules` / `load_hypotheses` / `search_decisions`, `evolver_scans._load_baselines`, `graduation.scan_candidates`, `shadow_lane._status`, `router.build_training_data`, `memory_quality.*`, `navigator_shadow._load_navigator_events` | flagged only via the call-graph leg; they drop, but nothing writes the result back — recoverable, and already on the silent-drop census |
| append-only importer | 5 | `pack._import_lessons` / `_import_hypotheses` / `_import_skill_records`, `workspace_import.import_ledgers` | rows are appended to a *local* store; the source pack is never rewritten |
| already verbatim-preserve | 3 | `memory_ledger.compress_old_outcomes._drop_compressed`, `captains_log._maybe_rotate`, `gc_memory._gc_outcomes._trim` | the rewrite rejoins raw lines and never parses, so there is nothing to drop |
| giant orchestrator | 4 | `handle._handle_impl`, `heartbeat.heartbeat_loop`, `doctor.run_doctor`, `sheriff.check_project` | call-graph noise: a drop loop and a write exist hundreds of lines apart with no data path between them |

Two of those FPs are worth a sentence each, because "not destructive" is not the
same as "fine":

- `constraint._load_dynamic_constraints` and `knowledge_lens.load_standing_rules`
  strict-read inside a broad except, so one torn byte silently empties a
  **guardrail** set. Recoverable (the bytes stay), already on the census, and
  wanting the announced-read treatment when someone next touches those files.
- `captains_log._maybe_rotate` disables rotation on a torn byte, but warns, and
  never parses — so the log keeps growing loudly rather than losing data.

## Receipts

- 15 tests — 6 `test_doctor.py`, 5 `test_interrupt.py`, 4 `test_gc_memory.py` —
  each written against a live probe *before* the fix.
- `tests/mutation/interrupt_gc_doctor_preserve.json`: 29 file-derived
  must-detect mutations (19 first pass, +10 from adversarial r1), including the deliberate-drop
  direction (a strand-and-carry that quietly turned the cleanup verb into a
  no-op would be a worse bug than the one it fixes).
- Census: 2 sites cleared. The scanner's RISK count falls 70 → 62.
- `scripts/triage_manifest.py` — the full site → category mapping, with a
  `--check` drift mode pinned by `tests/test_scan_destructive_rewrites.py`.
- Full suite verified in an isolated `git worktree` at HEAD + this change only:
  **9622 passed, 1 skipped**. The shared checkout shows two unrelated reds from
  another session's in-flight `captains_log.py` chunk.

## Adversarial round 1 (2026-08-20, five codex seats — the cap lifted)

Skeptic, Architect, Minimalist, Expert QA and the Experimentalist (the change
ships numbers). **REJECT — 5/5 consensus HIGH, verified and fixed**, plus five
more that held.

**The consensus HIGH was mine, and it is the same shape the previous round
caught:** the doctor fix took the lock only around the *write*, so a
`save_skill()` landing between the snapshot and the lock was overwritten by the
stale snapshot. A lost update, in a repair verb, introduced by the commit that
was fixing data loss — and the code comment claimed the race was fixed. The
read now happens inside the lock and the lock is held through the rewrite.

A probe note worth keeping: **an in-process probe of that race cannot fail.**
`locked_write` is reentrant, so a same-process writer acquires the lock the
cleanup already holds and its append is overwritten no matter how correct the
code is. The pin forks a real subprocess (which waits 1.3s for the lock);
without that it would be a guard that cannot fail, which is worse than none.

Also fixed, all verified by probe first:

- **Shape, not just bytes** (3 lenses): `loads_clean` refuses byte taint, not a
  wrong shape. `[]`, `null` and `"x"` are valid, taint-free JSON, and every one
  reached `.get()` and raised `AttributeError` — which `peek()`'s handler does
  not catch. The byte-safety fix closed one door and left the one beside it
  open: the control channel still went down, on a different input.
- **`"applied": "false"`** (2 lenses): legal JSON, and truthy, so a STOP
  interrupt was read as already-delivered and silently dropped with no warning.
  Now strictly `is True`; our own writer emits a real boolean, so nothing Maro
  wrote changes meaning.
- **Stale-by-id** (Minimalist): the cleanup removed every row carrying a stale
  row's *id*, so a healthy skill sharing that id was destroyed and the summary
  counted only the stale one. Probed: 2 rows in, 0 left, "1 removed". Filters
  by row now. Duplicate ids are not hypothetical here — a byte-tainted twin
  never id-matches on rewrite, so ids do accumulate.
- **`splitlines()` framing** (2 lenses): JSONL frames on LF, but `splitlines()`
  also breaks on U+2028/U+2029, which are legal *inside* a JSON string. A
  rewrite after such a split turns one valid row into two invalid fragments.
  Fixed in all three files; the arc-wide sweep of this idiom is BACKLOG'd,
  since our writers use `json.dumps` defaults (which escape those characters)
  and the exposure is foreign or hand-edited rows.
- **"Verbatim" was not verbatim** (2 lenses): the strand-and-carry stripped the
  line before carrying it, so padding and CRLF framing were lost even though
  the tainted bytes survived. The raw line is carried now; only a stripped copy
  is offered to the parser.
- **GC counts** (2 lenses): *half right, and the half that held is the one that
  mattered.* The claim that a post-scan append is deleted along with the old row
  it equals cannot cost data — an outcomes line identical to an old one carries
  that same old timestamp, so collecting it is correct. But the returned counts
  came from the out-of-lock scan, so GC could delete two rows and report one
  (probed: `(1, 1)` for a rewrite that removed 2). Classification now happens
  again inside the lock and the counts describe the mutation that happened,
  which also deletes the value-identity dependence entirely.
- **Uncollectable rows are now visible** (3 lenses): rows kept because they
  cannot be read can never age out, so a store accumulating them grows without
  bound. The retention decree forbids deleting them, so the answer is
  visibility, not collection — `GCReport.outcomes_uncollectable` and a summary
  line. A quarantine/repair verb is the follow-on, not a silent drop.
- **The manifest** (Experimentalist): the first version of this record shipped
  eight aggregate categories with selected examples, which is not a
  site -> classification mapping — you could not look up
  `closure_verify._detect_next_ledger_gap` and find its verdict. Now
  `scripts/triage_manifest.py`, with a `--check` mode pinned by a test, so a
  new RISK site cannot quietly inherit "already triaged".

**Deferred with reasons** (verified real, out of this chunk's scope):
`poll()` marks an interrupt applied *before* the caller applies it, so a crash
in that window loses the message permanently — at-most-once, not
exactly-once (3 lenses). Moving the mark later just trades it for
double-delivery, so the fix is a claim/ack protocol with idempotent apply
keyed on interrupt id, which is its own chunk. Pre-existing; not touched here.

Four of the five seats reported their sandbox was read-only and could not run
pytest; they compensated with in-memory and static probes. The Experimentalist
did run the mutation suite in an isolated archive and independently reproduced
19/19.

## Adversarial round 2 (2026-08-20, three codex seats — the fix layer)

Round 1's fixes got their own round, per the review-to-fixpoint practice.
Three seats (Skeptic, Expert QA, Minimalist) on the r1 diff. **REJECT** —
and the top finding was, for the second round running, a regression
introduced by the previous round's fix. That is now a pattern worth naming:
*the fix layer is the highest-yield thing to review, because it is the only
code in the change that nobody has reviewed yet.*

**Fixed, each reproduced before it was touched:**

- **The `applied` flag became tri-state** (3/3 seats, HIGH). r1 made the read
  strictly `is True` to stop `"applied": "false"` — a truthy string — from
  swallowing a STOP. That closed the drop and opened the mirror: every
  *legacy* truthy value (`"true"`, `1`) flipped from applied to pending, so a
  historical interrupt is **re-delivered and applied a second time**, then
  silently rewritten as boolean `true`. Probed: `stored_applied='true'` →
  `delivered_ids=['already']`. Two rounds in a row spent on this one flag,
  because both fixes treated a three-valued question as two-valued.
  `_applied_state()` now answers `True` / `False` / **`None`** — where `None`
  means *this flag cannot be read*, a third answer, not a default. Legacy
  `"true"`/`"false"`/`0`/`1` are recognized explicitly as the compatibility
  boundary they are; anything else routes to the preserve-and-announce path
  with the row left on disk. Pinned by three mutations (`applied-flag`,
  `legacy-applied`, `unreadable-flag`).
- **A dict is not yet a Skill** (Expert QA, HIGH). r1's shape guard accepted
  every JSON object, but `_skill_hash_is_stale()` returns "not stale" for
  anything it cannot build — so a forged object carrying a healthy skill's
  `content_hash` and a higher score **wins the dedup and deletes the healthy
  row**. Probed: `healthy survived: False`, `forged survived: True`,
  `"Cleaned: 1 skills remain"`. Confident destructive output derived from
  garbage, with no warning. The read now calls `dict_to_skill(row)` and
  strands whatever fails.
- **`freed` bytes described the wrong thing** (3/3, MEDIUM). `st_size` was
  sampled *before* the lock, so a concurrent **retained** append was charged
  against GC's freed count — a successful collection reported to the operator
  as having grown the store. Probed: `(2, 1, -4097)`. The delta is now
  computed inside `_trim` from the locked snapshot and the exact text that
  replaces it, `encode("utf-8", "surrogateescape")` on both sides.
- **The drift gate accepted regressions** (2 seats, MEDIUM). Re-introducing
  the *known real defect* at `interrupt.py:poll` passed `--check` cleanly: the
  site is in `SITES`, so it is not untriaged, and it is in `FIXED`, so it is
  not stale. A gate whose whole purpose is "a site cannot inherit already
  triaged" was blind to the one case that matters most. `compare()` now
  returns a third list, `regressed = live & FIXED`, and exits nonzero on it.
- **The lock test could pass with the lock removed** (2 seats). The r1 test
  spawned a child and slept 1.0s, then asserted both rows survived — but a
  child that appends *after* cleanup finishes leaves both rows there whether
  or not anything was serialized. Demonstrated by delaying the child two
  seconds against the lock-removal mutant: the test passed. The child now
  **handshakes** — it probes the `.lock` with `LOCK_NB` until it is refused,
  writes `blocked` to a marker, and only then appends; the parent waits for
  the marker and asserts on it. `never-contended` and `no-handshake` now fail
  the test loudly. This is the vacuous-test lesson in its purest form: the
  original assertion (`"proc" in started`) proved a subprocess was *spawned*.
- **The scanner gate had no must-detect fixture** (Skeptic, LOW). The
  committed test verified only the green baseline, so `main()` hardcoded to
  return 0 would still pass. `compare()` was split out as a pure function and
  is now unit-tested on all three failure directions.

**Accepted as designed, and now says so in the code** (Skeptic, MEDIUM): GC
skips the locked pass entirely when the unlocked pre-scan finds nothing to
collect, so a row that expires between the scan and the lock waits for the
next tick. Correct observation; it is a latency choice that avoids rewriting
the store on every GC tick, not data loss, and the authoritative
classification is still the locked one. The reasoning is now a comment at the
branch rather than a thing a future reader has to re-derive.

**Deferred, with the reason** (Skeptic, HIGH): `locked_write` **fails open** —
on a corrupt `.lock` (e.g. a directory of that name) it logs a warning and
proceeds *unlocked*, which hands the lost-update race back to every caller
including this repair verb. Reproduced. But that is documented, deliberate
behaviour in a primitive shared by the whole tree (the rationale being that a
RO-fs/permissions failure is an environment problem, not contention), so
flipping it to fail-closed is a decision about every caller at once, not a fix
inside this chunk. BACKLOG'd for Jeremy.

**Receipts for the round:** mutation spec 29 → 35, `35/35 accounted for` (33
DETECTED + 2 marked EQUIVALENT surviving as claimed). One of those two is new
and is itself a result: `doctor shape` — removing the `isinstance` guard — was
DETECTED in r1 and became *unfalsifiable* in r2, because the `dict_to_skill`
validation added on the next line raises `TypeError` on every non-dict JSON
value (`[]`, `null`, `"x"`, `3`, `true` — all probed). Two guards, one
detector: the mutation is marked with that reason rather than deleted, and
row-shape detection is carried by `doctor schema` instead. Four other
mutations came back **SKIP** — stale anchors, because the r2 rewrites moved
the code they pointed at. A SKIP is not a pass; all four were re-anchored
before the sweep was called green.

## Adversarial round 3 (2026-08-20, five codex seats — the fix layer again)

Five seats (Skeptic, Architect, Minimalist, Expert QA, Experimentalist) on the
r2 fix layer, with the r1→r2 pattern stated in the prompt as the primary
target. **REJECT — and for the THIRD round running the top finding was a
defect the previous round's fix introduced**, this time with 5/5 consensus.
Every finding below was reproduced before it was touched.

- **`dict_to_skill` is a constructor, not a validator** (5/5, HIGH). r2's
  answer to "a dict is not yet a Skill" was `dict_to_skill(row)`. Python does
  not enforce dataclass annotations, so `description=7` sails straight
  through it. Then: `compute_skill_hash` raises on the non-text field,
  `_skill_hash_is_stale` catches that and answers **"not stale"**, and the
  forgery — carrying the healthy row's declared `content_hash` and a later
  `created_at` — wins the dedup and **deletes the healthy skill**. Probed
  against the r2 code: 2 rows in, only `forged` out. `validate_skill_row()`
  now proves the row before it is admitted to any decision about which rows
  to remove: required keys, content fields that are text (proven by computing
  the hash over them), string identity/timestamp fields, list-of-string list
  fields, finite ranking numbers. It lives in `skill_types.py` because that is
  where the schema lives; read-only callers stay on `dict_to_skill`, because
  degrading them is a behaviour change nobody asked for. All 423 rows in the
  live store validate — that negative control is a test, since a guard that
  strands everything is an outage, not a guard.

  Worth stating plainly: the mechanism is the error *direction*.
  `except Exception: return False  # can't verify → keep` is the right
  retention instinct and the wrong membership answer — the row is kept AND
  admitted to the comparison. "Cannot verify" has to mean *cannot act on*.

- **The scanner had walked out of its own field of view** (Expert QA, HIGH —
  the sharpest finding of the round). It matched only `.splitlines()`. This
  arc **converted** every site it hardened to `.split("\n")` — because
  `splitlines()` also breaks on U+2028/U+2029, legal inside a JSON string —
  so the fix made the fixed sites invisible to the detector. Probed: reverting
  `interrupt.poll` to the exact destructive shape this arc removed produced
  **zero hits**, which means r2's `regressed = live & FIXED` gate, whose
  entire job is to catch that, could never fire for 6 of its 8 entries. The
  scanner now treats both idioms as framing. Blast radius was measured before
  the change, not after: +10 sites, 5 of them RISK, all in `playbook.py`
  (3 markdown rewrites that carry every line, 2 read-only) — triaged and
  added to the manifest, which now covers 75 sites / 69 FPs.

  Side-find while fixing it: the scanner's docstring claimed RISK required a
  function that "drops on a parse failure". It never tested that — framing
  plus a write-back is the whole signal. The claim had no executing line, and
  the 64-of-70 false-positive rate is its direct consequence. Docstring
  corrected rather than the code, because conservative is the right setting
  for this instrument.

- **poll() and clear() stranded rows in silence** (3 seats). Both preflight
  with an *unlocked* `peek()` and then re-read under the lock, so a row that
  becomes unreadable in that window is withheld from delivery and carried by
  the locked rewrite — both correct — with nobody told. `peek()` was the only
  path that announced, and if the interrupt that *was* delivered stops the
  loop, no later `peek()` ever runs. An operator's message parked on disk in
  silence is precisely the event this subsystem exists to make impossible.
  Both locked transforms now count and announce through one shared helper.

- **GC announced a delta as though it were a count** (2 seats). The second
  announcement passed `locked - unlocked` to a warning that formats it as
  "%d rows kept". A row repaired between the two reads printed
  **`gc: -1 unparseable/byte-tainted row(s) … kept`**. One announcement per
  run now, always absolute, always from whichever classification was
  authoritative on the path taken.

- **A locked pass that removes nothing no longer rewrites** (Architect). The
  unlocked pre-scan commits GC to `locked_rmw`; if the window closes and
  nothing is collectable under the lock, the old shape still rejoined and
  rewrote, normalizing framing — a snapshot with no trailing newline came back
  one byte *larger* and GC reported `freed=-1` for a collection that collected
  nothing. `_trim` returns the bytes untouched on that path.

- **The r2 drift-gate tests proved `compare()`, not the gate** (4 seats). Every
  new test called the pure function; the only test that ran the executable
  asserted the *clean* baseline exits 0. So `main()`'s `return 1` mutated to
  `return 0` passed the entire file — `compare()` stays correct while CI is
  told green. That is the asserted-the-helper-not-the-flow shape our own
  watch-list names, committed inside the test written to close a gate.
  `main()` is now pinned directly in both failure directions, with a
  negative control, plus a must-detect mutation.

- **The r2 freed-byte test could not fail** (Minimalist). It appended inside
  `_store_text`, which in the pre-fix code ran *before* `original_size =
  path.stat().st_size` — so the old implementation's own snapshot already
  included the concurrent row and reported a positive `freed`. The test passed
  on the exact defect it names. It now hooks `locked_rmw` so the append lands
  in the real window, and the mutation was replaced with a **faithful revert**
  of the old shape (sample the unlocked snapshot's size) instead of the
  `freed = 0` stand-in that any assertion would have caught. Deriving
  must-detect mutations from the FILE and not from the diff is a standing
  house rule; this is what violating it looks like.

**Narrowed rather than fixed** (Skeptic + Experimentalist): GC skips the locked
pass entirely when the unlocked pre-scan finds nothing, so on that branch the
reported counts are the *unlocked* snapshot. r2's comment claimed "the
authoritative classification is still the locked one below", which is false on
the path that returns above it. The claim is now the true one: nothing was
mutated, so there is no mutation for the numbers to misdescribe.

**Receipts:** mutation spec 35 → 44, **44/44 accounted for on the first pass**
(42 DETECTED + the 2 standing equivalents), including all nine new mutants —
among them the faithful `gc freed` revert, which the repointed test kills and
the old one did not. Suite: 9664 passed in the shared tree (plus the two
failures that belong to another session's uncommitted `captains_log.py`,
proven foreign in an isolated worktree earlier in this arc).

## Adversarial round 4 (2026-08-20, five codex seats — the fix layer again)

Five seats (Skeptic, Architect, Minimalist, Expert QA, Experimentalist) on the
r3 fix layer. **REJECT, 5/5 HIGH — the fourth round running whose top finding
is a defect the previous round's fix introduced.** Every finding was
reproduced before it was touched.

- **Schema validation cannot establish provenance** (5/5, HIGH). r3 answered
  the forged-row attack with `validate_skill_row`, which proves a row is
  *well-formed*. Five seats independently pointed at what that does not
  answer: dedup still grouped rows by the `content_hash` the row **declares
  about itself**, so a row that validates perfectly can still nominate itself
  as a duplicate of a healthy skill and evict it. Each seat found a different
  smuggling shape (a different `description` under the victim's hash, a
  different `steps_template`, a different `optimization_objective`, an
  extra field the schema does not name, and the id fallback path) — five
  variants of one defect, which is what a keying bug looks like from five
  angles.

  The fix is the key, not the field list. `_dedup_identity(row)` is a
  canonical dump of everything in the stored row that says what the skill
  **does** — every key except a named bookkeeping set (`id`, `content_hash`,
  `created_at`, the counters, circuit state, `failure_notes`,
  `source_loop_ids`, `imported`). Two rows are duplicates when they behave
  identically, full stop; the hash they claim never enters the decision, and
  the id fallback is gone. All five attack shapes were probed against the
  fixed code and all five now keep both rows. This kills the family, not the
  five instances — and it is the reason `doctor dedup scope` exists as a
  mutation: the bookkeeping set is now the security boundary, so a behaviour
  field slipping into it is exactly the mutation that must fail.

- **`return old` is not "decline the write"** (Architect + Expert QA, HIGH,
  probed). r3 fixed GC's freed=-1 bug by returning the unmodified text from
  the locked transform — but `locked_rmw` writes back whatever it is handed,
  so the file was still rewritten, atomically replaced, and re-inoded for a
  pass that collected nothing. `locked_rmw` gained a `None` sentinel (96 call
  sites, additive — no existing transform returns `None`), and the GC no-op
  path returns it. Pinned by spying `atomic_write` **and** asserting the
  inode is unchanged, because "did not write" is not observable from content.

- **r3 moved the announcement below the lock and orphaned the failure path**
  (Failure-lens findings from 2 seats, probed). On a failed lock, read, or
  write, `_gc_outcomes` returned `(total, 0, 0)` with no warning and no
  `uncollectable` stat — GCReport then reported zero unreadable rows while
  one sits in the store forever. Same shape, worse consequence, in
  `interrupt`: a failed commit returned `[]`, which the loop reads as "no
  interrupts", so a STOP the operator posted was on disk, had been seen by
  the preflight, and was silently not delivered. Both now log what did not
  happen, in the operator's words ("nothing was collected", "pending
  interrupts were NOT delivered this pass").

- **The queue announced twice per pass** (Minimalist). r3's announcement in
  the locked transform stacked on the one `peek()` already emits, so a single
  `poll()` warned about the same unreadable row twice — on both the poll and
  the clear path. `peek(announce=False)` is now the preflight form; the
  locked pass owns the announcement.

- **The scanner still could not see three framing idioms** (Expert QA). r3
  taught `frames_lines` about `split("\n")` after that conversion blinded it;
  r4 listed what was still missing — `readlines()`, `split(b"\n")` and the
  keyword form `split(sep="\n")`. `src/jsonl_utils.py` uses the bytes form
  itself, so this was never hypothetical. All five idioms are now pinned by a
  must-detect test with two CSV negative controls.

**The survivors are the interesting receipt.** The first r4 sweep came back
**55/59** with four validator mutants SURVIVED — delete the str-field check,
the empty-`id` check or the timestamp check and every test still passed. That
is not a dead guard; it is the dedup fix having removed the *consequence* the
end-to-end tests measured. Once a junk row can no longer evict a healthy one,
"the healthy row survives" holds whether or not the junk row was admitted.
Admission still has its own consequence — an admitted row is re-serialized
into the rewrite, a stranded one rides through byte for byte — so the answer
was direct rejection tests on `validate_skill_row` plus a byte-for-byte
strand assertion, not a weaker spec.

One of the four is worth its own line. `doctor validator (hash)` looked
genuinely equivalent: every field `compute_skill_hash` touches is also in
`_STR_FIELDS`, so a non-`str` is rejected either way. The shape that
distinguishes them is a **lone surrogate** — it IS a `str`, it passes every
`isinstance` check, and it dies on `.encode("utf-8")`. The mutant that looked
unfalsifiable was pointing straight at this arc's own subject. Marking it
`equivalent` would have been a defensible-sounding way to delete the one
guard that catches byte taint at the schema boundary.

**Receipts:** mutation spec 44 → 59, `59/59 accounted for` (57 DETECTED + the
2 standing equivalents) after the survivor pass above. Suite: green in the
shared tree, plus the two failures that belong to another session's
uncommitted `captains_log.py`, proven foreign in an isolated worktree earlier
in this arc.

## Adversarial round 5 (2026-08-20, five codex seats — the fix layer again)

Five seats (Skeptic, Architect, Minimalist, Expert QA, Failure Operator) on the
r4 fix layer. **REJECT — the fifth round running whose top finding is a defect
the previous round's fix introduced, and the second at 5/5 consensus.** Seven
findings, all seven reproduced independently before anything was touched; zero
hallucinations in the round.

- **An exclusion list is a denylist, and a denylist guarding a destructive
  decision fails open** (5/5, HIGH). r4 keyed dedup on behaviour rather than
  the row's declared hash — right idea — and implemented it by naming twelve
  fields "bookkeeping" and ignoring them. `circuit_state` is the proof: an
  open circuit **excludes a skill from matching** (`skills.py`,
  `find_matching_skills`), so two rows differing only there are not identical
  copies in any sense a user would accept. Probed both directions: a forged
  `open` row evicting a healthy `closed` one, and a forged `closed` row
  resurrecting a circuit-broken skill. `failure_notes` and `source_loop_ids`
  are the same mistake wearing a different hat — they are EVIDENCE, and
  deleting the row that carries them destroys it.

  `_DEDUP_BOOKKEEPING` is now three names — `id`, `content_hash`,
  `created_at` — the fields two rows must differ on to be two rows at all.
  Everything else must match, including fields a future commit adds. Measured
  before shipping: on the 423-row live store the tight identity finds exactly
  as many duplicate groups as r4's list did (zero), so the strictness costs
  nothing observable and the summary line's word "identical" is now literally
  true.

- **The validator proved the coerced value, not the stored one** (2 lenses,
  HIGH, probed). Every check read `getattr(skill, …)` AFTER `dict_to_skill`,
  which coerces with `int()`, `float()` and `normalize_tags()`. So
  `consecutive_failures: "7"` was proven to be an int, `utility_score: true`
  a float, `tags: "not-a-list"` a list — and those are exactly the fields r4
  excluded from the identity, so the forged row still won on `created_at`.
  Checks now run on the raw `d[name]`; construction happens after. Coercion
  stays in `dict_to_skill` because a tolerant READ path wants it — the point
  is that a caller about to delete rows must prove what the STORE says, not
  what a constructor could make of it. Re-probed against the live store:
  423 rows, 0 stranded.

- **A queue of nothing but unreadable rows went silent** (4 lenses, HIGH,
  probed). r4 silenced the preflight so the locked pass could own the single
  announcement. But `poll()`/`clear()` return BEFORE the locked pass when the
  preflight finds nothing deliverable — so a corrupt STOP alone in the queue
  produced no delivery, no warning, and no trace, which is the precise
  failure the whole arc exists to prevent. The preflight is now
  `_peek_counted()` (silent, returns the count) and the early-return branch
  announces for itself: one announcement per pass, from whichever branch
  actually runs.

- **`loads_clean` accepted two more shapes of corrupt row** (Failure
  Operator, HIGH, probed). (a) A surrogate written as a JSON **escape** —
  `{"tier": "\udcff"}` — is pure ASCII on disk, so the raw-line taint scan
  cannot see it, and it parses to exactly the string a torn byte produces.
  Only the fields that get hashed caught it. The check now also runs on the
  parsed value (keys included), behind a cheap `\u`-substring pre-filter so
  the hot path is unchanged. (b) Duplicate object names:
  `{"applied": false, "applied": true}` reads as applied, because
  `json.loads` silently keeps the last one — probed, a STOP swallowed with
  no warning — and a rewrite that re-dumps the row destroys the other value.
  Two values and no rule saying which is not a choice this layer may make;
  it is a corrupt row, and corrupt rows strand.

- **The scanner still could not see two idioms, and its OK verdict was worth
  little** (3 lenses + Architect, MEDIUM). Invisible: a separator hoisted to
  a local (`sep = "\n"`), and plain iteration over an open handle
  (`with path.open() as fh: for line in fh:`) — each one routine-refactor
  distance from any hardened site. Both now count, and an unresolvable
  separator counts too: only a separator PROVEN to be something else buys
  silence, because a false RISK costs one line of triage and a false OK
  costs a rewrite nobody can see. Separately, `OK` was a substring test over
  the whole function, so a rewrite parsing every line with bare `json.loads`
  that merely MENTIONED `loads_clean` reported OK — and the `vanished` leg
  then counted it as a watched, healthy site. A function still parsing with
  the unguarded call is not cleared, whatever else it mentions.

**Blast radius, measured before shipping** (the r3 discipline): the stricter
scanner takes the tree from 69 to 77 RISK sites, +8, none lost. All eight were
read by hand and triaged the same day — two read-only loaders and a
derived-index replay newly visible through the file-iteration leg, plus four
`memory_ledger` stampers and `doctor.run_doctor` newly RISK through the
verdict rule. The stampers earned a new FP category, `clean-then-raw`: their
scan parses every line with `loads_clean` and the bare `json.loads`
re-parses ONE line that scan already proved taint-free, an ordering the rule
cannot see and the existing preserve tests already cover.

`doctor.run_doctor` is the interesting one, and it is recorded rather than
quietly swapped: it left `FIXED`, because nothing in it regressed — r5
refuted the OK **verdict** that put it there. A drift gate cannot tell "the
code regressed" from "the rule got stricter", so the resolution is a hand
re-read and a written reason, never a widened exemption. That is the same
move `vanished` forced for `_gc_outcomes._trim` one round earlier.

**Receipts:** mutation spec 59 → 69, `69/69 accounted for`. Eight of the
existing mutants came back **SKIP** on the first sweep — stale anchors from
r5's own rewrites — and were re-anchored against the current files before the
sweep was called green; a SKIP is not a pass, and a spec that silently skips
is the same failure as a scanner that reports zero.

## Adversarial round 6 (2026-08-20, five codex seats — the fix layer again)

Five seats on the r5 fix layer, with the five-for-five pattern stated in the
prompt as the standing prior. **REJECT.** Six findings, all six reproduced
before being touched, zero hallucinations for the second round running.

- **Absence is not a default for the fields this verb acts on** (4 lenses,
  HIGH, probed). r5's move from "check the constructed Skill" to "check the
  stored value" was right, and dropped a guard on the way out: `if name in d`
  means an ABSENT field is simply fine. For `content_hash` and `created_at`
  it is not. A missing hash makes `_skill_hash_is_stale` answer "not stale"
  (it has nothing to compare); `created_at` is the tiebreaker; and **both are
  deliberately excluded from `_dedup_identity`**, so neither absence shows up
  as a difference. Probed end to end: a row identical to a healthy one but
  with `content_hash` omitted and a later `created_at` validated, counted as
  non-stale, grouped, won, and deleted the verified row. r4's constructor-
  first check had caught this by accident — `dict_to_skill` defaults the
  field to `""` and the empty check fired. Both are now required outright.
  Live store: 423/423 rows carry both, so nothing real is stranded.

  This is the sharpest instance yet of the pattern the whole arc keeps
  finding, because the guard was not overlooked — it was *load-bearing under
  a different name*, and the refactor that made the check more correct
  removed the accident that had been doing the work.

- **The taint check could take the channel down** (2 lenses, HIGH, probed).
  r5's `_carries_surrogate` recursed. JSON nested ~600 deep — which
  `json.loads` parses without complaint — blew the interpreter stack, and
  `RecursionError` is not a `JSONDecodeError`, so it flew straight through
  the `except (json.JSONDecodeError, TypeError)` that every caller uses to
  strand a bad row. Probed: one valid line took `InterruptQueue.poll()` down
  before it could announce anything. Now iterative. A shared helper with 84
  call sites does not get to raise something its callers do not catch.

- **The raw scan covered a quarter of the surrogate block** (Architect, LOW
  but exact). It checked U+DC80–U+DCFF, the range `surrogateescape`
  produces. A lone HIGH surrogate arriving from anywhere else was admitted
  and re-dumped as a clean-looking escape — the launder this helper exists to
  prevent. Now the whole U+D800–U+DFFF block; the valid-pair control
  (`😀` → 😀) proves the check is about LONE surrogates.

- **The tiebreaker compared text, not time** (Failure Operator, HIGH,
  probed). `score_skill` ranked `created_at` as a string.
  `2026-01-01T00:00:00+14:00` sorts after `2025-12-31T23:00:00-12:00`
  lexically and before it in real time, so the older row was kept and the
  newer deleted — both rows valid, both timestamps legal ISO-8601, nothing in
  the output saying which one went. Ranks by parsed instant now, naive
  timestamps read as UTC (both shapes are in the live store, and `max()` over
  mixed awareness raises).

- **Two more ways past the scanner** (4 lenses + 2 lenses). r5's "no bare
  `json.loads`" rule matched one spelling, so `import json as j` and
  `from json import loads` walked past it and an unguarded rewrite was
  certified OK for mentioning `loads_clean` elsewhere. Chasing spellings is
  the losing half of that trade — ANY `loads` call that is not the clean
  wrapper now counts as unguarded, whatever module it came from. And r5's
  separator resolution kept the LAST binding found in AST-walk order, which
  is not control flow: `if newline: sep = "\n" else: sep = ","`, and a plain
  later reassignment, each made a live JSONL rewrite vanish from the scan
  entirely — the exact must-detect shape r5 had just added. A name buys
  silence only when exactly one binding in the function proves it
  non-newline. Counting bindings is not flow analysis; it is a refusal to
  pretend.

**Receipts:** mutation spec 69 → 76, `76/76 accounted for` (two existing
mutants came back SKIP from r6's own rewrites and were re-anchored first).
Blast radius of the stricter scanner on the real tree: **zero** — 77 RISK
sites before and after, none gained, none lost, manifest still green.

## Adversarial round 7 (2026-08-20, five codex seats — the fix layer again)

Five seats on the r6 fix layer. **REJECT.** Seven findings, all seven
reproduced before being touched, zero hallucinations for the third round
running — and for the seventh round running, the top finding is a defect the
previous round's fix introduced.

- **The rewrite reordered the store, and order decides which skill is live**
  (Skeptic, HIGH, probed). `skills.load_skills` reads the file in reverse and
  lets the LAST row for an id win, so a row's POSITION decides whether it is
  the live skill or the ignored twin. The rewrite appended every stranded row
  after the admitted ones. r6's stricter validator strands more rows, so a
  legacy row sharing an id with a verified one moved from ignored to live —
  in a run that printed `0 removed` and `Kept in place: 1 row`. Nothing was
  deleted and the system still changed behaviour, which is the failure mode
  this arc's retention rules do not cover: they guard the bytes, not the
  meaning. Rows are written in read order now, admitted and stranded alike.

  The fix has a trap of its own, and it is worth writing down: the obvious
  implementation stamps the position onto the row (`row["__ordinal"]`).
  Every key a row carries is part of `_dedup_identity`, so that would make
  every row unique, silently disable the dedup this verb exists for, and
  write the bookkeeping key into the store. Positions are carried by object
  identity in a side table instead.

- **A mixed-awareness group cannot be ranked, and r6 ranked it** (Architect,
  HIGH, probed). r6's tiebreak read naive timestamps as UTC.
  `replace(tzinfo=utc)` is not a conversion — it ASSERTS a fact the row does
  not carry, and a naive value can denote either side of an aware one. r6
  deleted a row on that invented instant. Both shapes are in the live store
  and `max()` over mixed awareness raises, so "do nothing" was not available
  either; the answer the retention decree already gives is to keep both rows
  and say why. Undecidable groups are now kept whole with a named reason.

- **The parser's own recursion limit was still uncaught** (Minimalist, HIGH,
  probed). r6 fixed the taint WALK's recursion and left `json.loads`'s.
  It has its own depth limit and raises `RecursionError`, which is not a
  `JSONDecodeError` — so a row nested ~50k deep flew through every
  `except (json.JSONDecodeError, TypeError)` in the codebase. Probed:
  `InterruptQueue.poll()` died on one such row before it could strand it or
  announce anything, which is the silent control-channel loss the arc
  started from. Translated at the helper: a row this layer cannot parse is a
  row that strands, whatever shape the refusal arrives in.

- **The r6 walk's memory followed WIDTH** (Architect, MEDIUM, probed under a
  96 MiB cap). Making the walk iterative fixed depth by pushing every element
  of a container at once, so a valid five-million-item row raised
  `MemoryError` — again something no caller catches. It is a stack of
  ITERATORS now: auxiliary storage proportional to nesting depth, which is
  what the recursive version got right before r6 removed it.

- **A verdict about safety cannot be read off an identifier** (4 lenses,
  HIGH, probed). r6's answer to the spelling problem was a better spelling
  rule, and it died four ways in one round: `from json import loads as parse`
  was invisible (`parse` is neither `loads` nor `*_loads`),
  `parse_json = json.loads` likewise, and — the other direction —
  `from json import loads as _loads_clean` was TRUSTED, because the marker
  list matched the name and nothing checked where it came from. Parser
  identity now comes from the BINDING (imports, aliases, assignments), with
  raw winning over every naming convention.

- **Three binding forms were invisible to the separator census** (3 lenses,
  probed). `sep: str = "\n"`, `sep += "\n"` and `(sep := "\n")` were not
  counted as bindings at all, so a function that binds a comma once and a
  newline by any of those forms was "proven" non-framing and vanished from
  the scan entirely — neither RISK nor OK, the same disappearance r5's
  `split("\n")` conversion caused. Every binding form counts now, and an
  unrecognised one counts as unresolved.

- **A repair verb that destroys a row must name it** (4 lenses, MEDIUM).
  `keeping best of 3 identical copies of 'name'` identifies the kept row by
  the hash prefix and the name — the two things that, by construction, cannot
  tell the group apart. An operator could not tell which record had just been
  destroyed, or recover it. Each kept and each removed row is now named by id
  and `created_at`. The same finding's sibling: r6's stricter validator made
  "readable but unprovable as a skill" a common outcome, and the summary
  reported those as corruption, which points the operator at the wrong
  repair. The two counts are split.

**Receipts:** mutation spec 76 → 93, `93/93 accounted for` on the first
sweep — eleven r6 anchors that the r7 fixes moved were re-anchored before it
was called green, and one new mutant is marked `equivalent` with its reason
(the `clean - raw` subtraction is a second lock on a door the raw-wins
ordering already closes). Suite: 9811 pass. Blast radius of the stricter
scanner on the real tree: **zero** for the second round running — 77 RISK
sites before and after, manifest green.

## Adversarial round 8 (2026-08-20, five codex seats — the fix layer again)

Five seats on the r7 fix layer. **REJECT.** Six findings accepted, all six
reproduced before being touched, zero hallucinations for the fourth round
running. Three of them are the same mistake wearing three hats, which is
what makes this round worth reading.

- **A denylist of exception classes, again** (Skeptic + Architect, HIGH,
  probed). r7 translated `RecursionError` — the one class it had met — into
  `JSONDecodeError` so a row the parser refuses would strand. r8 walked past
  it with the next class: CPython caps int-from-string conversion at 4300
  digits and raises `ValueError`, so a queue row carrying a 5000-digit
  number killed `InterruptQueue.poll()` before it could strand the row or
  announce anything. That is the r7 finding verbatim, one exception class
  over, in code written to fix the r7 finding. The rule is the general one
  now: if the parser did not return a value, the line does not parse, and a
  line that does not parse strands. The class rides in the message so the
  operator still sees why, and the refusal this layer raises on purpose
  (duplicate object names) is re-raised untouched rather than re-wrapped.

- **The same denylist, in the doctor's accounting** (Minimalist + QA,
  probed). r7's split of "unparseable/byte-tainted" from "readable but
  unprovable" tested the exception's class NAME against three strings. A
  401-digit `success_rate` is valid JSON with readable bytes, is refused
  with `OverflowError`, and was reported to the operator as byte
  corruption — the exact misdirection r7 claimed to have removed. Parse and
  validation are separate `try` blocks now and the kind is recorded where it
  is known. The per-row line was carrying the same lie in the other
  direction: every stranded row was announced as `Unreadable line`, four
  lines above a summary that said "readable but unprovable".

- **The same denylist, in my own scanner fallback** (3 lenses, HIGH,
  probed). r7's parser-identity fix kept a fallback for the conventional
  SPELLING — `loads_clean(...)` with no visible import earned OK — added so
  that round's own fixtures would pass. `from untrusted_parser import
  loads_clean` was therefore trusted on the name alone, in the round whose
  entire finding was that a verdict cannot be read off an identifier. It is
  gone; a clean binding must come from `jsonl_utils`, and `import
  jsonl_utils` + `jsonl_utils.loads_clean(...)` is proven the same way.

- **A module-wide proof does not survive a local rebinding** (4 lenses,
  HIGH, probed). Parser identity was collected module-wide, so
  `def rewrite(path, loads_clean=json.loads)` and a local
  `loads_clean = lambda s: json.loads(s)` both parsed with the raw parser
  while the scanner read the module-level import and said OK. Shadowing
  revokes the proof now — with the must-detect other half pinned, because
  half this codebase imports the wrapper INSIDE the function (doctor.py and
  gc_memory.py both do) and reading that import as a shadow would have
  turned every one of them RISK, which is how a strictness change stops
  being a signal.

- **Enumerating binding forms is a denylist too** (2 lenses, probed). r7
  listed the node types that bind a name — Assign, AnnAssign, AugAssign,
  NamedExpr, For, With — and r8 found the two it had not thought of: a tuple
  target (`sep, _unused = "\n", 0`) and a `match` capture
  (`case {"separator": sep}`). Each made a live JSONL rewrite vanish from
  the scan entirely, neither RISK nor OK — the third time this arc has paid
  for that exact disappearance. The census counts Store-context names now,
  which is Python's own answer to "what binds a name", plus the short closed
  set of binders the grammar defines without a Name node (except aliases,
  match captures, imports, parameters).

- **The store path was never named** (5/5 seats, independently). r7 named
  the ROWS it destroys and never named the FILE it destroyed them in, while
  its own comment said "the path is part of the result". An operator running
  cleanup under a `MARO_WORKSPACE` override, or reading an automation log,
  could see that a record was destroyed and not which `skills.jsonl` lost
  it. The path is now on the rewrite header, on every stranded row, on the
  strand summary and on the closing count. The stale branch — the sibling r7
  left half-done — names its row's `created_at` like the duplicate branch
  does.

**Rejected:** the Minimalist's finding that `loads_clean` admits `NaN` and
`Infinity`. Both are non-standard JSON that `json.dumps` re-emits verbatim,
so a rewrite carries them faithfully — there is no launder and no loss, and
the ranking inputs that could be poisoned by a non-finite value are already
proven finite by `validate_skill_row`. The probe's "concrete failure" was a
duplicate row being removed, which is the verb's job. Recorded in BACKLOG as
a strictness question for whoever owns cross-reader compatibility, not as a
defect in this arc.

**Receipts:** mutation spec 93 → 110, and the sweep did real work this time
— **six survivors on the first pass**, all of them holes rather than dead
mutants. Four "the path is missing from line X" mutants survived because the
new test counted occurrences (`>= 3`) instead of asserting each line, so
stripping any ONE of the four still passed. The fifth showed that removing
the spelling fallback had quietly disarmed the r6 fixtures: their guard
mention (`loads_clean("unrelated")` with no import) no longer earned clean
status, so they read RISK for a different reason and stopped exercising the
unguarded-parse branch at all. The sixth is marked equivalent with its
reason. 110/110 after. Suite: 9837 pass. Scanner blast radius on the real
tree: **zero** for the third round running.

## Adversarial round 9 (2026-08-20, five codex seats — the fix layer again)

Five seats on the r8 fix layer. **REJECT.** Six findings, all six
reproduced before being touched, zero hallucinations for the fifth round
running. The top finding is the first in nine rounds that is NOT in the
previous round's fix — it is older than the arc, and it is a launder that
eight rounds of reviewers (and this author) read past every time.

- **`str.strip()` is not JSON whitespace** (QA + Architect, HIGH, probed).
  Every reader in this repo wrote `line = raw.strip()` and then parsed the
  stripped copy while carrying the raw one. JSON's whitespace is space, tab,
  CR and LF — nothing else — so `"\u2028" + a valid row` parsed AFTER
  stripping, was admitted, and came back re-serialised with those bytes
  gone, the file reported clean, nothing announced. That is the exact
  laundering this arc exists to prevent, arriving through the whitespace
  door instead of the surrogate one. The same idiom used for blank
  detection (`if not raw.strip(): continue`) deleted a row of U+00A0
  outright, because a skipped fragment is never appended to the rewrite.

  The finding named `doctor`. The census found it live in four more places,
  and two of them are worse than the one that was reported:
  `gc_memory._gc_outcomes._classify` read the timestamp that AUTHORIZES a
  delete out of the laundered copy, and `skills.save_skill` parsed AND WROTE
  the stripped copy — a carried row's bytes rewritten by a save that never
  claimed to touch them. `_save_skills` stranded a stripped copy under a log
  line that says "verbatim", and framed with `splitlines()` while doing it.
  Both interrupt merges dropped whitespace rows. One helper answers all of
  them: `jsonl_utils.is_frame_blank`, true only for the empty fragment
  `split("\n")` yields for the trailing newline every JSONL file ends with.

- **Admission is enough; laundering is not required** (Failure Operator +
  Skeptic, probed). `loads_clean` accepted `NaN`, `Infinity` and
  `-Infinity` — CPython extensions, not JSON. **r8 rejected this finding**
  on the grounds that the tokens round-trip faithfully through
  `json.dumps`, so nothing is laundered. That was true and beside the
  point, and r9 showed why by probe: the row does not need to be laundered
  to do damage, it only needs to be ADMITTED. Once admitted it takes part in
  a removal decision — `_dedup_identity` serialises the token straight back,
  the group forms, the older row is deleted. Refused now, with the blast
  radius measured first: zero rows in the live workspace carry any of the
  three.

- **A scope-aware rule that reads the wrong scope** (Minimalist + QA, HIGH,
  probed). r8's `_shadowed` and `_parser_names` used `ast.walk(fn)`, which
  descends into nested functions — so a helper defined inside a rewrite that
  imported the real wrapper re-proved the OUTER function's parameter, which
  defaulted to `json.loads`. Proofs and bindings are collected per lexical
  scope now, which is the unit Python itself uses.

- **A dotted proof outlived its receiver** (Architect + Failure Operator +
  Skeptic, HIGH, probed). `def rewrite(path, jsonl_utils)` and a local
  `pm = json` both kept `jsonl_utils.loads_clean` / `pm.loads_clean` in the
  clean set, because r8's revocation subtracted BARE names from a set
  holding DOTTED ones. A proof is now revoked with its receiver.

- **Module identity was still a spelling test** (Architect + QA, HIGH,
  probed). `(n.module or "").split(".")[-1] == "jsonl_utils"` trusts
  `from vendor.jsonl_utils import loads_clean`. Exact match now — and the r8
  must-detect fixture had tested `vendor.not_jsonl_utils`, the shape that
  cannot fire, rather than the dangerous sibling.

- **A name collision made a destructive site vanish** (Skeptic, probed). The
  call-graph leg indexed functions by bare name in a dict, so an unrelated
  `B.save` replaced `A.save`, `A.rewrite`'s write leg resolved to the wrong
  body, and `A.helper` — a destructive JSONL loop — disappeared from the
  scan entirely, neither RISK nor OK. That is the fourth time this arc has
  paid for a site disappearing rather than turning red. Ambiguous names
  resolve to "any candidate writes" now, which errs toward being looked at.

- Plus the r8 sibling left half-done: the two lines that announce a row
  being DESTROYED now name the store, not just the header above them (5/5
  seats).

**Receipts:** mutation spec 110 → 129, with **five survivors on the first
sweep** — all holes, and two of them worth naming. Both interrupt merge
mutants lived because the new test's "whitespace row" was a literal SPACE,
and every `json.dumps` row in the fixture already contains spaces, so
`assert " " in text` could not fail; it uses an explicit U+00A0 now. The two
parameter-binding mutants lived because they were redundant with each other
(`_own_scope` already yields the `ast.arg` nodes), which is a finding about
the fix rather than the test — the redundant loop is gone. Suite: 9867
pass. Scanner blast
radius on the real tree: **zero** for the fourth round running — 77 RISK
sites before and after, manifest green.

## Adversarial round 10 (2026-08-20, five codex seats — the fix layer again)

Five seats on the r9 fix layer. **REJECT.** Seven findings, every one
reproduced before being touched, zero hallucinations for the sixth round
running. Four of the five seats reached the top finding independently, by
four different routes — the strongest consensus this arc has produced.

- **The shared READER admits what every writer refuses** (Skeptic +
  Minimalist + QA + Failure Operator, HIGH, all four probed). Nine rounds
  hardened the write paths, and `read_jsonl_announced` — the single door
  most loaders in this repo use — still parsed with bare `json.loads`, on a
  `bytes.strip()`ed copy. So the whole launder chain reassembled itself from
  the read side: `load_skills` materialised a Skill from a row `loads_clean`
  rejects, `_save_skills` wrote a CLEAN re-serialised copy of it and
  stranded the raw one beside it, and the laundered twin — landing last —
  then won last-row-wins on the next load. Probed end to end:

      on disk: {"id": "s1", ..., "note": "\udcff"}
      loads_clean: JSONDecodeError byte-tainted line
      load_skills -> 1 skill
      after _save_skills: 2 rows with id s1, one of them clean

  The same door admitted `NaN`, duplicate names, and `\x0b{...}` — the
  `bytes.strip()` removed bytes JSON does not allow as whitespace, so a line
  no parser accepts was handed over as a record. Fixed at the helper:
  `_classify` frames on the physical newline only and parses with
  `loads_clean`. Census before flipping: **141,094 live rows across 1,061
  stores, zero flips.**

- **`_read_skill_stats` was the last read→rewrite pair still on the r8
  idiom** (Skeptic, HIGH, probed). The r9 census missed it, and both halves
  destroyed data: `splitlines()` broke a valid row at the U+2028 inside a
  JSON string and `_write_skill_stats` wrote the two fragments back rejoined
  with LF — the row's bytes CHANGED under a log line that says "carried
  verbatim" — while `line.strip()` deleted a U+00A0-only row outright,
  neither stranded nor counted. This is the store every outcome update
  rewrites.

- **Both skill writers let an unprovable row decide its own removal**
  (Minimalist + Failure Operator, HIGH, both probed). `validate_skill_row`
  has said since r3 that *"a caller that REMOVES rows must use this"*, and
  `save_skill` matched on `.get("id")` while `_save_skills` treated every
  parseable row as "represented by the list". A row that is valid JSON but
  not a loadable Skill (`"utility_score": "nope"`) is skipped by
  `load_skills` with a log line — so it is in no caller's list — and the
  next unrelated outcome update deleted it. Probed: `good_survives=True
  drift_survives=False`. The doctrine existed; two callers had not been
  moved onto it.

- **Carrying a row to the tail is a promotion, not preservation**
  (Minimalist, HIGH, probed). `_save_skills` appended strandees after every
  live skill, and this store is read last-row-wins by id. The doctor's
  rewrite has preserved ordinals since r7; the skills rewrite now does too.

- **The interrupt preflight kept the idiom r9 removed from its own merge
  loops** (four seats, MEDIUM, probed). `_read_lines` still filtered
  `if l.strip()`, so a queue holding ONLY an unreadable whitespace row was
  emptied before `_peek_counted` could count it: `poll()` and `clear()` took
  their no-preflight early return and reported a quiet queue with no
  warning, while an operator's undeliverable STOP sat on disk. The r9 test
  seeded a valid row alongside, which forces the locked merge — so it passed
  on the defect.

- **The scanner's cycle detection was still keyed by bare name** (three
  seats, MEDIUM, all probed). r9 taught the call graph that an ambiguous
  name means "any candidate writes" and then poisoned it with a name-keyed
  `seen`: evaluating a harmless `save` first inserted `"save"`, and the
  destructive `A.save` behind it returned False before its body was read.
  r9's own fixture put the writer first. Reversed, the destructive reader
  vanished from the scan entirely — the same disappearance r9 was written to
  fix, one definition-order edit away.

- **r9 made the scanner lexical in two places out of four** (Minimalist +
  QA, MEDIUM, both probed). `_binding_census` and `_parser_names` got
  `_own_scope`; `_parse_calls` and `_shadowed`'s re-proof loop kept
  `ast.walk`. So a `loads_clean` call — or a re-proving assignment — inside
  a nested helper that need never execute cleared the OUTER function's
  verdict while every line of its rewrite went through the raw parser:
  `nested-clean-call: [('OK  ', 5, 'rewrite')]` against
  `control: [('RISK', 5, 'rewrite')]`.

**The side-find, and the only site this round found rather than re-found.**
Making the scanner lexical throughout meant `frames_lines` had to read one
scope too, and that put `memory_backends.JSONLBackend.read_all` in view for
the first time. It was carrying three of the arc's families at once:
`read_text(encoding="utf-8")` is a strict whole-file decode and
`except OSError` does not catch `UnicodeDecodeError` (family B), the
per-line `except json.JSONDecodeError: pass` dropped rows in silence
(family A), and `rewrite()` — the method directly below, whose own comment
names the "read_all → transform → rewrite" pattern — writes the survivors
back, which turns each silent drop into a deletion. Now on the shared
announced reader.

**The gate's own drift, handled out loud.** Nine functions whose only
framing lives in a `locked_rmw` closure stopped being reported under the
OUTER name — `interrupt.poll`, `gc_memory._gc_outcomes`,
`memory_ledger.stamp_outcome_verdict` and six more. This is the r3 `_trim`
situation at nine times the size and it gets the same answer: a surface is
watched when the scanner can still SEE it, under whatever name owns the
framing. The manifest records each move with the inner site that owns it
now, and a fifth gate leg (`blind`) re-checks that each named twin is still
in the live scan — so the exemption keeps paying for itself instead of
being a third one-directional excuse. `llm._run_subprocess_safe` is the one
entry with no twin: hand-re-read, unchanged in its triage (it parses CLI
NDJSON stdout, no durable store), and recorded as such rather than deleted.

**Cost, measured rather than assumed.** Pointing the shared reader at
`loads_clean` put a stricter parse on 84 call sites. On this box's largest
live store (57,843 rows) a full unbounded read went 248 ms → 1098 ms, and
most of that turned out to be a per-character Python loop hunting
surrogates in every row. `str.isascii()` plus a UTF-8 encode is the same
answer at C speed, and tightening the escaped-surrogate gate from `\u` to
`\ud`/`\uD` (every surrogate escape's hex starts with d) took it to
**730 ms**. The remaining 2.7× is the C scanner that `object_pairs_hook`
and `parse_constant` necessarily disable — the price of the duplicate-name
and non-finite-constant refusals, paid on purpose. Tail reads with a limit
parse `limit` rows and are unaffected.

**The sweep, which did real work again.** 138/145 on the first pass, and
five of the seven gaps were holes in the tests this round had just written
— the r8 and r9 pattern for the third time running. The U+2028 stats
fixture never contained a U+2028 (`json.dumps` escapes it to six ASCII
characters, and `splitlines()` does not break on those). The ordinal test
put the carried row FIRST, where appending it to the tail lands it in the
same place, so it could not fail. The shadow test's "broken" row used
`steps_template="not a list"`, which `dict_to_skill` assigns without
complaint. Nothing distinguished `validate_skill_row` from `dict_to_skill`
in `_save_skills`, because every fixture used a row BOTH reject — the
distinguishing shape is a row the constructor accepts and the proof does
not, carrying a key `skill_to_dict` does not write, which the constructor
path silently drops on re-serialisation. And the deep-nesting test went
vacuous in this very round: tightening the taint walk's gate from `\u` to
`\ud` meant its `\u000a` payload no longer reached the walk. It carries a
valid surrogate PAIR now. That gate change also introduced case-sensitivity
where none existed — JSON hex may be uppercase — so `\uDCFF` got its own
pin and its own mutant. The other two were re-anchors; a SKIP is not a
pass.

Landed with 146 mutations in the spec (129 → 146), the manifest green at 72
RISK sites, and 21 new must-detect tests.

## Adversarial round 11 (2026-08-20, five codex seats — the fix layer again)

Five seats on the r10 fix layer. **REJECT.** Ten findings, every one
reproduced by probe before being touched — the seventh consecutive round
with zero hallucinations. The HIGH was unanimous: all five seats, by five
independent routes, converged on the same two lines of
`memory_backends.py`.

- **`rewrite(read_all(...))` deletes the strandees `read_all` announces**
  (all five seats, HIGH, probed). r10 put `JSONLBackend.read_all` on the
  announced reader, which strands a torn row correctly — and returns a
  list WITHOUT it. `rewrite()` then wrote that list back. The caller
  cannot preserve what its input never contained, so the documented
  "read_all → transform → rewrite" composition deleted the exact row the
  log had just promised was safe. Worse, the r10 test named for this —
  `test_the_torn_row_is_not_destroyed_by_a_read_then_rewrite` — never
  called `rewrite()`. Fixed where the destruction lives: `rewrite()`
  re-reads the store under its own lock and carries every raw line
  `loads_clean` refuses, verbatim, announced with the path. The test now
  runs the composition its name promises.

- **The gap between a tolerant loader and a strict writer is a launder
  mint** (four of five seats, HIGH, probed). r10 put `validate_skill_row`
  on the WRITERS; `load_skills` still admitted via the `dict_to_skill`
  CONSTRUCTOR. A stored `"utility_score": "1.0"` loaded fine — and
  `_save_skills`, proving each emission, wrote a NORMALIZED CLONE (float
  `1.0`) while stranding the raw row above it. Last-row-wins then
  promoted the clone: a row nobody wrote, winning over the bytes the
  operator owns. The round's lesson in one line: **constructible ≠
  provable ≠ deliverable**, and the only structural kill is ONE admission
  predicate on both ends — admitted == provable. `load_skills` now admits
  via `validate_skill_row` (census: 423/423 live rows pass), which also
  closed the shadow-delete the same seats probed: the loader claimed an
  id BEFORE the proof, so a construct-ok/hash-fail row hid the older
  valid row for its id from every caller, and the next `_save_skills`
  deleted it. The id is claimed only by a row that proves out.

- **A writer must not mint what its own reader strands** (three seats,
  HIGH, probed). `json.dumps` happily writes CPython `NaN`
  (`allow_nan=True` is the default) and serialises a lone surrogate in a
  hash-excluded field as a CLEAN six-character escape — both rows the
  strict reader then refuses. `save_skill` could therefore replace a
  healthy row with one no future load returns. `_prove_line` now runs
  every emitted skill row back through the reader's own door
  (`allow_nan=False` + `loads_clean`) before the store is touched;
  failure aborts with the old bytes intact. Same rule at
  `_write_skill_stats` and the backend `rewrite()`.

- **An undeliverable interrupt was recorded as delivered** (three seats,
  HIGH, probed). `Interrupt.from_dict` is a constructor too:
  `"new_steps": "not-a-list"` sailed through it, `poll()` marked the row
  applied ON DISK, and the consumer crashed on `steps +
  interrupt.new_steps`. The retry saw an empty queue — the operator's
  STOP was gone, recorded as acted on. `_prove_deliverable` now runs
  before every applied-mark (poll, clear, and peek's result): a row that
  cannot be applied strands raw, unapplied, and announced every poll
  until a human reads it.

- **JSON `1` and JSON `true` are one Python dict key** (Expert QA,
  MEDIUM, probed). `1 == True` in Python, so the keyed stats rebuild
  collapsed two distinct rows into one and silently deleted the other.
  A non-string id is not an identity this store can key on; such rows
  are strandees now, carried verbatim.

- **Routine counter bumps deleted every field the model doesn't know**
  (Minimalist, MEDIUM, probed). Both outcome recorders rebuilt the row
  from `SkillStats.to_dict()`, so an operator's note — or any foreign
  tool's stamp — vanished on the next update with no warning. The
  recorders now merge over the stored row: the updater wins on the
  fields it writes, everything else rides through.

- **A proof inside a generator expression proves nothing** (two seats,
  MEDIUM, probed). A genexp body is deferred code — `(loads_clean(s) for
  s in ())` never runs a thing, yet the scanner credited the clean call
  to the enclosing function and certified a raw rewrite OK. The rule is
  asymmetric on purpose: clean-in-genexp proves nothing, raw-in-genexp
  still poisons (if it ever runs, it runs raw), and EAGER comprehensions
  execute where they stand, so their proof value survives.

- **`json.JSONDecoder().decode(line)` was invisible** (two seats,
  MEDIUM, probed). Stdlib spelling for the same raw parse as
  `json.loads`; one rename made a destructive rewrite vanish from the
  scan. The scanner now tracks names bound from `JSONDecoder(...)` and
  flags `.decode` through them — while plain `bytes.decode` stays
  unflagged (the negative control: it is how bytes become text, not a
  parse).

- **A MOVED site coming back under its outer name passed the gate**
  (two seats, MEDIUM, probed). `MOVED` excuses a site from `stale`
  because its scan-visible name moved inward — which also excused the
  OUTER name from ever being questioned if someone puts framing back in
  the outer scope: not untriaged (it is in SITES), not stale (MOVED
  exempts it), not blind (the twin is still there). A sixth leg,
  `resurfaced`, fires on `live ∩ MOVED` — and it is the ONLY watch on
  the one twinless MOVED entry (`llm.py:_run_subprocess_safe`). The
  exemption doctrine's fifth application: FIXED→`regressed`,
  seen→`vanished`, MOVED→`blind`, and now MOVED→`resurfaced`.

- **Accepted with reason, and pinned as such:** the final torn frame
  gains a terminator LF on the way through a stats rewrite (its content
  bytes are untouched). Preserving the missing LF would let the next
  `locked_append` concatenate a fresh record INTO the torn fragment,
  corrupting both. A pin test records the decision so a future round
  cannot "fix" F4 and reopen the concatenation hole.

Censuses before the strictness flips, all zero-cost on live data: 423/423
skill rows pass `validate_skill_row`; 2/2 live interrupt-queue rows are
deliverable; 203/203 skill-stats rows are string-keyed; no `NaN` /
`Infinity` in either skill store. The scanner changes moved the blast
radius not at all: 72 RISK sites, manifest green.

**The sweep — 163/165 on the first pass, and both gaps were teachers.**
Five moved anchors were re-anchored before the sweep ran (a SKIP is not a
pass), and 19 new file-derived mutants rode in with the fixes. The
`interrupt launder (clear)` survivor was a hole THIS round's own fix
created: `_prove_deliverable` strands a field-poor row on its own, so the
old torn fixture could no longer tell the taint door from the proof door
— the killing fixture must be DELIVERABLE-shaped with one raw byte in
`message`, which is exactly why the poll twin (whose fixture already was)
died on schedule. A guard added in front of another guard disarms the
second guard's tests; only the sweep says so. And `the proof line
re-admits NaN` is a genuine twin-lock equivalent, marked with its reason:
`_prove_line`'s very next line runs the emitted text through
`_loads_clean`, whose `parse_constant` (r9) refuses the token
`allow_nan=False` would have refused to mint — same abort, same
direction, no observable difference. 165/165 accounted for after
(163 detected + 2 marked equivalents in the spec's history).

## Lesson

The scanner earned its keep by being *wrong 64 times out of 70* — because
reading 70 short functions took one session, and the three it surfaced were a
repair verb that destroyed data while reporting "0 removed", the kill switch's
delivery path, and a GC that had been reporting success while doing nothing.
A hint list that is 4% true is still worth reading when the 4% looks like that.

The corollary is the one the scanner's own docstring already makes: a "found 0"
from a tool nobody has falsified is worth nothing. This triage is the
falsification pass, and its durable output is the FP table above — so the next
person to run the scanner starts from 63 known-benign sites, not 63 unknowns.

The second lesson is from the six rounds, not the scan: **every round's top
finding was a defect the previous round's fix introduced** — r1 found the lock
that r0's fix scoped too narrowly, r2 found the `applied` flag that r1's fix
inverted, r3 found that r2's "validation" validated nothing, r4 found that r3's
validation proved well-formedness and called it provenance, r5 found that r4's
provenance key was a denylist and that its de-duplicated announcement had left
one branch mute, r6 found that r5's more-correct validator had removed the
accident that was doing the work. None was in the original code. Review the fix layer first; it is the only part of the change
that has never been read by anyone but its author, and it was written under
the pressure of a finding, which is exactly the condition that produces the
mirror-image bug. Three for three is no longer a coincidence, and the r3
prompt said so up front — which is probably why r3 landed 5/5 consensus on it.

The third is narrower and worse, and it is the one to carry forward: **a fix
can blind the detector to its own subject.** Converting the hardened sites from
`splitlines()` to `split("\n")` was correct on its own terms and walked all six
of them out of the scanner's field of view — including out of the regression
gate written the round before, specifically to catch them coming back. Nothing
about that shows up as a failing test, a warning, or a red CI run; it shows up
as an instrument that reports zero forever. After changing an idiom, re-run the
detector against the *reverted* code and prove it still finds it. "Found 0" is
a claim, and like every other claim it needs an executing line.

The fourth arrived in r5 and generalizes the third: **an exclusion list
guarding a destructive decision is a denylist, and denylists fail open on
everything nobody thought of** — including the fields a future commit adds.
r4's dedup key named twelve fields as ignorable and was refuted 5/5 on the
first one anybody checked. The same shape sat one file away in the scanner,
where `OK` meant "this function mentions the safe parser somewhere". Both
fixes are the same move: state the small set you can prove, and treat
everything else as unproven. Three names instead of twelve, and a verdict
that requires the unguarded call to be absent rather than the guarded one to
be present. On the live store the strict version costs nothing — measured,
not assumed, because "it would be too strict" is exactly the claim that needs
an executing line.

The fifth is r6's, and it is the one that makes this arc worth six rounds:
**a correct refactor can delete a guard that was load-bearing under another
name.** r5 replaced "validate the constructed Skill" with "validate the
stored row" — strictly more correct, and it silently removed the
required-field check, because the old version had been enforcing it by
accident (`dict_to_skill` defaulted a missing hash to `""`, and the empty
check fired on the default). Nothing in the diff looked like a removal.
Nothing in the tests failed. The guard that vanished was one the round
before had specifically added. When a check moves, the question is not "is
the new check better" but "what was the old one catching that nobody wrote
down" — and the only reliable way to answer it is a mutation the old code
kills and the new code does not.

The sixth is r7's, and it is the one that does not fit the arc's own frame:
**preserving every byte is not the same as preserving the meaning.** Every
rule this arc wrote down guards the bytes — strand the row you cannot read,
carry it verbatim, announce the drop, never rewrite from the short list. The
r7 top finding broke none of them. `doctor --cleanup-skills` kept all the
bytes, deleted nothing, printed `0 removed`, and changed which skill the
system executes, because `load_skills` reads the file in reverse and position
decides the winner. A rewrite is only non-destructive if the ORDER survives
too — and more generally, when a store's readers derive meaning from
anything other than a row's own content (position, adjacency, file identity),
that property is part of the data and belongs in the preserve tests. The
question to ask of any rewrite is not "did I lose a row" but "could a reader
tell the difference".

The seventh is r8's, and it is the arc's most durable one because it names
the SHAPE rather than an instance: **naming the cases you have met is a
denylist, and a denylist in a safety check fails open on the case nobody
has met yet.** r8 found the same mistake in three files at once — a list of
exception classes to translate, a list of exception names to classify by, a
list of AST node types that bind a name — and one of the three was written
INSIDE the round whose finding was "a verdict cannot be read off an
identifier". The general form was available in all three cases and is
shorter than the list: the parser either returned a value or it did not;
the kind of refusal is known where it is raised, so record it there; and
Python already marks every name it binds with `ctx=Store`. Where the
general form genuinely is not available, the honest move is the one the
scanner makes — count what you can prove and treat everything else as
unproven — never a list that grows by one each round.

The eighth is smaller and is about the tests, not the code: **a
count-based assertion cannot fail in the direction it was written for.**
The r8 path test asserted the store path appeared on "at least three"
lines; the mutation sweep stripped any one of the four and it still passed.
Assert the property on each thing that must carry it, not on a total.

The ninth is r9's, and it is the first one in this arc that is not about the
fix layer: **the idiom everyone writes is the one nobody reviews.**
`line = raw.strip()` appears in five readers here, was in the original code
of every site this arc hardened, and survived eight adversarial rounds and
several hundred probes — including rounds whose entire subject was "what can
a rewrite do to bytes it cannot read". It survived because it looks like
tidying, not like a decision. It is a decision: `str.strip()` removes
Unicode whitespace that JSON forbids, so the stripped copy can parse when
the row does not, and any verb that parses one copy and writes another has
already lost the bytes. When auditing a destructive reader, list every
transformation between the bytes on disk and the value the decision is made
from — and require each one to be either identity or announced.

The tenth is the one r9 forced on the round before it: **"it round-trips
faithfully" is not the same as "it may take part in the decision".** r8
rejected the NaN finding because nothing was laundered, which was true. r9
probed the consequence anyway and found the row being admitted into a
DELETION decision, which is the thing the doctrine actually protects. When
rejecting a finding, state the property you are relying on and then check
that it is the property that matters here.


The eleventh is r10's, and it is the arc's own shape turned around: **a
safety rule enforced on the write path and not the read path is not
enforced.** Nine rounds hardened every rewrite in this repo against
byte-tainted rows, and the whole chain reassembled itself through the
loader — a row the writers refuse became a live object, and the writers
then serialised that OBJECT faithfully, which is a laundered clean copy of
a row nobody vouched for. Four seats found it from four different call
sites in one round, which is what a rule with a missing half looks like
from the outside. When a property is stated as "this store never contains
X", the check belongs at every door into the store, and the read door is
the one that feels harmless.

The twelfth is about half-conversions: **a scope-aware rule applied to two
of its four scans is not scope-aware, and the half that still walks is the
one that decides.** r9 gave the binding census and the parser-name scan
lexical scopes; the proof scan and the shadow re-proof kept `ast.walk`, so
a nested helper that never executes certified its parent. The same
half-conversion produced r10's ordering bug (`by_name` collects every
candidate; `seen` still keyed on the name) and r10's own preflight miss
(two merge loops converted, their sibling reader left behind). When a rule
changes, the unit of work is every place that reads the thing it changed —
list them, then convert them, and make the list a test if it can be one.

The thirteenth is a rule about exemptions, and it is now the third time
this arc has learned it: **an exemption must carry a proof that keeps being
checked.** `FIXED` was one-directional until r2 added `regressed`;
`regressed` could not see a site leaving the scan until r3 added
`vanished`; and r10's `MOVED` — nine sites now watched under an inner name
— would have been a blanket "these are allowed to be missing" if the
`blind` leg did not re-check that each named twin is still visible. Write
the exemption with the counter-check in the same commit, or the exemption
is a deletion with a comment on it.
