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

## Adversarial round 12 (2026-08-20, five codex seats — the fix layer again)

Five seats on the r11 fix layer. **REJECT.** Eight findings, every one
reproduced by probe before being touched — the eighth consecutive round
with zero hallucinations. Three seats converged on the writer/reader gap
from three different stores, and all five seats independently bypassed
the r11 decoder rule, each with a *different* spelling.

- **The proof proved parse-clean, not admission** (Skeptic + Minimalist,
  HIGH, probed). r11 moved `load_skills` onto `validate_skill_row` and
  left `_prove_line` on bare `_loads_clean` — so a constructible Skill
  with `tier=7` (hash-excluded, JSON-clean) was emitted, REPLACED the
  healthy row, and stranded on the next load. The writer now proves the
  COMPLETE admission predicate: `validate_skill_row(_loads_clean(line))`.
- **Every JSONL writer could outrun its reader** (four of five seats,
  HIGH, probed from four call sites). `json.dumps` serializes a lone
  surrogate as a clean-looking `\udcXX` escape and (by default) writes
  the CPython `NaN` token — rows `loads_clean` strands. r11 fixed this
  for skill rows only. New shared door `jsonl_utils.prove_record_line`
  (serialize with `allow_nan=False`, re-read through `loads_clean`,
  require a dict), now behind `memory_backends.append`, `rewrite`, and
  `_write_skill_stats`. The payload is built before the write, so a
  refusal aborts with the store untouched.
- **The queue accepted under a weaker predicate than its consumer
  delivers by** (three seats, HIGH, probed). r11 proved deliverability
  at poll/clear/peek and left `post()` on bare `json.dumps` — so an
  operator STOP holding a lone surrogate was acknowledged at the door
  and never deliverable by anyone. `_append` now serializes, proves the
  line through the reader's door, and proves deliverability BEFORE
  `locked_append`.
- **The strand rule mirrored the parser, not the reader** (QA + one
  other, MEDIUM, probed). `read_all` announces-and-skips clean non-dict
  JSON (`null`, arrays, strings), so those rows were never in the
  caller's list — and r11's re-read pass stranded only parse failures,
  deleting a `null` row the round before had just taught it not to.
  The re-read now strands everything `read_all` would exclude.
- **The stats store was the skills store, one round behind** (two
  seats, MEDIUM, probed). The keyed stats read fed
  `SkillStats.from_dict`, a COERCING constructor — `float("1.0")`
  passes, `bool("false")` is True — so a schema-drifted row was
  silently rewritten with laundered values by the next counter bump,
  and the injection recorder flipped a stored `"false"` to `true`. New
  `validate_skill_stats_row` (raw values, no coercion) strands drift
  verbatim; census before the flip: 203/203 live rows pass. Both
  recorders also now refuse a non-string or non-encodable `skill_id`
  at the door (`record_skill_outcome(1, ...)` wrote a row every future
  read carried as an unreadable strandee), and duplicate string ids —
  still compacted last-wins, matching the keyed read — are counted and
  announced instead of collapsing in silence.
- **Provenance, not final call syntax, decides** (all five seats, one
  spelling each, all probed: import alias, object alias, bound method,
  AnnAssign, `raw_decode`). The r11 decoder rule matched the literal
  `JSONDecoder` constructor and the literal `decoder.decode(...)` call
  shape. `_decoder_ctors` now tracks `from json import JSONDecoder as
  X`; `_decoder_names` resolves instance aliases and bound-method
  bindings to a fixpoint; `_bindings` learned AnnAssign and walrus;
  `raw_decode` counts as a parse. All five spellings (plus walrus) now
  read RISK; the bytes-`.decode` negative control holds; blast radius
  unchanged at 72 RISK.
- **The resurfaced leg was blind to an OK resurfacer** (Skeptic,
  MEDIUM, probed). `resurfaced` intersected the RISK-only `live` set
  with MOVED — but the move's premise is that the outer name is absent
  from the scan, which is falsified by the name coming back at ANY
  verdict. An OK resurfacer means framing returned to the outer scope
  with a superficially clean parse beside it: more suspicious, not
  less. The leg now reads `seen`, falling back to `live` for three-leg
  callers.

Two findings were judged rather than fixed:

- **Hash equality stays OUT of the admission predicate**
  (accept-with-reason; three seats wanted mismatches stranded). The
  `content_hash` is a tamper-EVIDENT tripwire, not a boundary — there
  is no secret key, so anyone who can forge a row can also write a
  valid hash, and stranding on mismatch would stop no attacker. The
  case it WOULD catch is a legitimate operator hand-edit whose hash is
  merely stale, which the rehash-on-update flow handles correctly.
  Warn-and-load is the decided behavior, now pinned with this
  reasoning on `test_load_skills_warns_on_hash_mismatch`.
- **The poll-ack crash window is real and deferred** (Failure
  Operator). `poll()` durably writes `applied=True` before the loop
  side applies anything; a crash between poll and apply loses a fully
  deliverable STOP, and the retry converges to "nothing pending". This
  is a pre-existing delivery-semantics design gap (lease/ack, not a
  byte-safety door) — BACKLOG'd as design work, out of this arc's
  scope.

Sweep: **185/185 accounted for on the FIRST pass** — 179 detected plus
all six marked equivalents (five standing, one new this round)
surviving as claimed. Twelve rounds
in, this is the first sweep with zero survivors and zero SKIPs on its
first run: the eight re-anchors were done BEFORE the sweep (a SKIP is
not a pass), and each new mutant was written against a fixture that
already existed. Suite and manifest green; blast radius 72 RISK.

## Adversarial round 13 (2026-08-20, five codex seats — the fix layer again)

Five seats on the r12 fix layer. **REJECT**, but the findings are
narrowing: no unanimous HIGH, and most of the round is r12's own new
code plus config/sibling twins the arc had not yet visited. Every
probed claim was real — the ninth consecutive zero-hallucination round.

- **Presence is not absence** (three seats, HIGH, probed).
  `validate_skill_stats_row` read fields with `d.get(name)`, so an
  explicitly stored JSON `null` was indistinguishable from an absent
  field: it rode the absence exemption, `bool(None)` laundered it to
  `false` on the next counter bump — the exact r12 laundering class,
  through `null` instead of `"false"` — and a `null` counter would make
  the NEXT update raise mid-recorder. Every modeled field now checks
  `name in d` and refuses a present null; the null row strands verbatim.
- **The recorders trusted their callers' types** (Architect, probed).
  `success="false"` is truthy, so a stringly-typed caller recorded a
  FAILURE as a success — permanently wrong evidence that type-checks
  clean forever after; and `cost_usd=NaN` sailed to the emission door,
  whose refusal the never-raise write wrapper swallowed into a normal
  `None` return with a pathless warning. Both refused at the door now;
  the wrappers name the store path and the discarded skill.
- **The writer/reader gap, one layer up** (Architect, probed).
  `_write_skill_stats` proved clean-object JSON while its reader admits
  via `validate_skill_stats_row` — the destructive writer had a weaker
  contract than its own reader. It validates the full predicate now.
- **The archive was a writer nobody had audited** (Skeptic, probed).
  `_archive_skills` — the retention decree's own mechanism — used bare
  `json.dumps`, so a skill holding a lone surrogate archived as a row
  the strict reader strands and was then removed from the live pool.
  Every line is proven before any append; a refusal aborts the
  caller's removal too (archive-before-remove order verified at both
  call sites).
- **The rewrite's audit lied twice** (QA + Failure Operator, probed).
  Strandees were re-homed to the tail, where a keyed last-row-wins
  consumer would let a later-repaired legacy row outrank the caller's
  records (they ride FIRST now, the same ordinal decision skills made
  in r7); and the carry-through warning fired BEFORE the payload was
  proven, so a refused emission left an audit line about a rewrite
  that never happened (announce-after-commit now). `append` and
  `rewrite` also converted I/O failure into apparent success — they
  propagate loudly with the path now.
- **The bare composition is an undecidable race** (Skeptic, probed).
  A record appended between `read_all()` and `rewrite()` is CLEAN, so
  the strandee pass cannot distinguish "the caller dropped it" from
  "the caller never saw it" — with a bare list API that is
  undecidable, and the concurrent append was silently deleted. New
  `transform(collection, fn)` runs read → fn → write under ONE lock,
  making the decision decidable by construction; a lock-held pin keeps
  the no-lock revert out. `rewrite()` remains for whole-store
  replacement. (No production caller composes the pair today — the
  backend is the memory-port bake-off surface — so this landed as API
  hardening, not a live-data fix.)
- **The SQLite twin had none of it** (Minimalist, HIGH, probed).
  `MARO_MEMORY_BACKEND=sqlite` selected a backend whose `read_all`
  silently dropped a damaged row (`except JSONDecodeError: pass`) and
  whose `rewrite` DELETE-all'd the collection — the standard
  composition permanently destroyed the damaged row, with no
  announcement, one env flip from live. Full doctrine parity now:
  announced strict reads, rewrite deletes only rows the reader vouches
  for and carries damaged `data` verbatim, emissions proven before the
  transaction, loud failures.
- **Provenance is a lattice, not a spelling list** (four seats, one
  bypass each, all probed). r12's provenance rules still fell to
  ordinary alias chains: `Ctor = json.JSONDecoder; decoder = Ctor()`,
  a re-aliased bound method (`rebound = raw`), tuple destructuring,
  `parser: object = json.loads` (r12 taught `_bindings` AnnAssign and
  walrus — and `_parser_names` kept its own private Assign-only walk:
  the r12 half-conversion lesson, one round later), and an instance
  stored on `self` in `__init__` and used in `rewrite`. Constructor
  aliases now resolve to a fixpoint; destructuring binds element-wise;
  attribute chains resolve as dotted paths; and a class-level map
  carries `self.*` provenance across sibling methods (normalized to
  the receiver name, so `self`/`cls` spelling does not matter). Blast
  radius unchanged at 72 RISK; both negative controls hold.

Judged, not fixed:

- **Admission stays TYPE-level** (QA wanted semantic invariants —
  non-negative counters, successes ≤ total). A row claiming
  `total_uses=-4` is faithfully representable and faithfully
  re-emitted; the reader can vouch for its BYTES, which is what
  admission means in this arc. Plausibility auditing is an inspector's
  job, and stranding implausible-but-readable rows would misfile
  legitimate legacy data behind a corruption warning. Documented in
  the validator docstring.

Sweep: 208/209 on the first pass (202 detected + the 6 standing
equivalents), and the one survivor was a teacher: the SQLite
composition test asserted a substring BOTH guards emit ("unreadable
row"), so the read-side silent-drop revert hid behind the rewrite's
carry-through warning — the guard-in-front-of-a-guard hole, third
appearance in this arc. The fixture now pins the read's own
announcement ("excluded from the result") before the rewrite runs.
209/209 after.

## Adversarial round 14 (2026-08-21, five codex seats — the fix layer again)

Five seats on the r13 fix layer. **REJECT, still narrowing in kind**:
one unanimous HIGH, and every one of the thirteen deduped findings
lives in r13's own new surface or a twin it named but did not convert.
Every probed claim was real — the tenth consecutive zero-hallucination
round.

- **The contract did not travel** (all five seats, HIGH, probed).
  r13 added `transform()` to JSONLBackend only — the abstract
  `MemoryBackend` never learned it, and the selectable SQLite twin
  kept the exact lost-update race the method exists to close: a clean
  append between a caller's `read_all()` and `rewrite()` was
  reader-vouched by rewrite's own re-select, deleted, and never
  reinserted. The fifteenth lesson ("doctrine that does not travel to
  the twins is local luck") applied to the fifteenth lesson's own fix
  layer, one round later. `transform` is abstract contract now;
  SQLite's implementation takes the write lock BEFORE the read
  (`BEGIN IMMEDIATE`), proves every emission, deletes only ids its own
  in-transaction read vouched for, and rolls back whole on any
  failure. A threaded barrier test pins that a concurrent append
  cannot land inside the replace window.
- **A failed read looked like an empty store** (two seats, HIGH,
  probed). SQLite `read_all()` converted every `sqlite3.Error` into
  `[]` — indistinguishable from verified-empty, so a transient lock
  during a repair's read phase handed the caller a valid-looking empty
  list and the next rewrite deleted every healthy row. It raises now;
  a read that did not happen must not look like a store with nothing
  in it.
- **The one-lock guarantee honored fail-open** (four seats, probed).
  `transform()` called `locked_write(path)` bare, and the lock's
  documented degraded mode (`MARO_FILELOCK_FAIL_OPEN=1`, or an
  uncreatable lock file) yields WITHOUT a lock — the transaction
  degrading into the exact race it exists to close. `require=True`
  now; a contended fail-open transform raises before `fn` runs, store
  untouched.
- **Identity was not in the predicate** (four seats, probed). The
  stats reader keys on a non-empty STRING `skill_id`, but
  `validate_skill_stats_row` checked only the modeled statistic
  fields — so the writer vouched for a `skill_id: null` row the reader
  immediately strands as keyless. Identity is in the validator now
  (the reader's own counters are unchanged: it routes identity
  failures to the keyless strand before validating), and the writer
  additionally refuses a map key that disagrees with the row's own id.
- **The sibling kept the old ordinal** (three seats, probed). r13
  moved generic-rewrite strandees to the head of the payload;
  `_write_skill_stats` kept them at the tail, where a same-id stranded
  legacy row overrode the freshly repaired record for any naive
  last-row-wins consumer. Strandees ride first now — same doctrine,
  same ordinal, one round late.
- **A pure read claimed a rewrite** (Architect, probed).
  `_read_skill_stats` warned "carried through the rewrite verbatim"
  from reads that rewrite nothing (`get_all_skill_stats`), and a
  recorder could log the claim and then fail its write — the r13
  announce-before-commit finding, one layer up. Reads announce
  exclusion-from-this-read now; the carry-through line moved to
  `_write_skill_stats`, after its commit.
- **The router trained on rows every reader strands** (QA, HIGH,
  probed). `build_training_data` walked skill-stats with bare
  `json.loads` and coerced with `int()`/`float()` — so a schema-
  drifted row (`"total_uses": "1", "success_rate": "1.0"`) that the
  strict reader strands and the repair path faithfully carries was
  laundered into a confident training success no operational reader
  would ever return. It rides `_read_skill_stats` now; drifted rows
  are excluded and announced. (`packaging_readout._read_jsonl` shares
  the raw-loads shape but is a display surface — BACKLOG'd, not
  fixed here.)
- **The archive batch could split** (Failure Operator, probed).
  `_archive_skills` appended line by line, so a mid-batch failure
  landed half a batch and the caller's retry duplicated the landed
  half. One locked append per batch now — a failure lands nothing.
  Residual, accepted and documented: a retry after a successful append
  still duplicates the whole batch; in an append-only retention store
  a duplicate is noise, not loss, and dedup-on-write is the direction
  the retention decree forbids.
- **Provenance stopped at four more boundaries** (four seats between
  them, all probed). The r13 class-level map fell to: a constructor
  alias held on the instance (`self.Ctor = json.JSONDecoder` —
  `_decoder_ctors` recorded bare Names only), a class-BODY decoder
  binding (read as `self.decoder` from every method, never read at
  all), a same-module BASE class (`Base.__init__`'s decoder invisible
  from `Child(Base).rewrite` — a routine base-class extraction turned
  a raw destructive parse scanner-green), and a positional-only
  receiver (`def rewrite(self, path, /)` has no `args.args`, so both
  map construction and consumption skipped the method). And
  `_parser_names` rejected tuple targets one line before
  `_expand_binding` could expose `parser, _x = json.loads, None` — the
  r13 private-copy disease again, whose fix then surfaced a THIRD
  private copy in `_shadowed` (found by this round's own negative
  control, not a reviewer). Ctor provenance now takes dotted targets
  and dotted call sites; class-body bindings seed the map; the class
  walk is a fixpoint over methods with seeds re-spelled per receiver;
  same-module bases propagate to a fixpoint (ambiguous base names
  union all candidates — RISK direction); receivers count
  posonlyargs. Blast radius unchanged at 72 RISK; negative controls
  hold (inherited bytes attribute, destructured clean parser).

Judged, not fixed:

- **The framing LF on a torn final strandee** (two seats). A final
  row that lost its terminator to a crash gains an LF on rewrite in
  every writer family. Judged accept-and-pin: once strandees ride
  first the LF is required row framing, the payload bytes are
  unchanged, and the round-trip is stable — "verbatim" means the
  payload, not the terminator state of a row torn mid-write. Pinned
  byte-for-byte in both writer families so drift into actual payload
  mutation is caught; terminator-state tracking rejected as invasive
  machinery guarding forensic metadata, not data.

Sweep: 229/230 on the first pass (223 detected + the 6 standing
equivalents), and the survivor was again a teacher — this time about
the fix, not the tests: the single-pass mutant on
`_class_decoder_sets`'s method walk survived because `scan_module`'s
class-graph fixpoint re-seeds and re-runs the walk until the sets stop
growing, so the inner while-loop was a redundant second fixpoint on
the same door. Rather than marking it equivalent, the redundant loop
is REMOVED (the caller drives convergence — simpler code, one
mechanism) and the mutant retargeted at the OUTER fixpoint, which the
reversed-order chain fixture genuinely exercises. 230/230 after.

## Adversarial round 15 (2026-08-21, five codex seats — the fix layer again)

Five seats on the r14 fix layer. **REJECT, still narrowing**: seven
deduped findings against thirteen last round, but two unanimous-shape
HIGHs, and every finding is again the prior round's fix stopping one
twin short. Every probed claim was real — the eleventh consecutive
zero-hallucination round.

- **The require-lock fix did not travel** (four seats, HIGH, probed).
  r14 put `require=True` on `JSONLBackend.transform` and left the two
  skill-stats RMW recorders on bare `locked_write(path)` — under the
  documented fail-open mode two concurrent recorders both read
  `total_uses: N`, both wrote N+1, and one outcome vanished with two
  normal returns. Both recorders require the lock now, pinned by a
  contended fail-open test and a structural keyword pin.
- **A failed write returned an ordinary None** (four seats, HIGH,
  probed). Both recorders caught `_write_skill_stats` failures, warned,
  and returned — disk-full indistinguishable from success, execution
  evidence silently gone, router training biased forever. They raise
  now; all three production callers already wrap the call in their own
  `except Exception` and degrade visibly (memory_ledger's docstring
  even says "attribution is telemetry" — the swallow belongs THERE,
  where the caller can see it, not in the writer).
- **The duplicate lane still announced from the read** (four seats,
  probed). r14 moved the strandee-carry announcement behind commit and
  left its duplicate twin: a PURE read of two same-id rows logged
  "will be compacted by the next rewrite" — a destructive claim from a
  path that changes nothing, wrong in both directions (no rewrite may
  follow; a failed rewrite already claimed it). `_read_skill_stats`
  returns a `_StatsRead` tuple-subclass carrying `.compacted`; the
  read announces an exclusion from its own result, the writer
  announces the compaction after its commit.
- **The strict-reader fix did not reach the sibling input** (four
  seats, probed). r14 made the router's STATS side strict and left the
  skills side on `read_text` + bare `json.loads` in `except: pass` — a
  JSON-valid row `validate_skill_row` rejects (`description: 7`), one
  that can never enter the live pool, still fed str()-coerced text to
  training. The skills side now rides `read_jsonl_announced` +
  `validate_skill_row`; unadmitted rows are excluded and announced.
  The site thereby left the scanner's view entirely (the framing moved
  into the shared reader) — recorded in the triage manifest, 71 RISK.
- **The class graph read bases literally** (four seats, probed).
  `Alias = Base` and the generic spelling `Base[str]` (an
  `ast.Subscript`, discarded outright) both severed decoder provenance
  at the inheritance boundary — an inherited raw destructive parse
  scanner-green again, one round after inheritance was made provenance.
  A module-level alias lattice now resolves base names (fixpoint over
  the same shared binding walk, ambiguity unions toward RISK) and
  Subscript bases unwrap; a negative control pins that an alias cannot
  MINT provenance.
- **The retention archive was less durable than the deletion it
  justifies** (two seats, HIGH, probed). `_archive_skills`' append rode
  the page cache while the live-pool removal that follows goes through
  fsyncing `atomic_write` — a power loss could keep the delete and lose
  the retention copy; and the retention writer honored fail-open.
  `locked_append` gains `require=` and `durable=` (flush + fsync the
  file, fsync the parent dir when the append created it), and the
  archive uses both.
- **A committed transform reported "store unchanged"** (one seat, LOW,
  probed). A `sqlite3.Error` raised AFTER `con.commit()` — a `close()`
  failure — fell into the outer handler, whose message invites a retry
  that would apply a non-idempotent transform twice. A `committed`
  flag branches the message: "COMMITTED … the store HOLDS the
  transform; do not retry."

One ask was rejected with reasons: the Minimalist's standing request to
treat CROSS-module `.decode` receivers as conservative RISK. That
contradicts r11's receiver-decides doctrine — `.decode` is not
parse-shaped wholesale, and the false-positive flood would train
operators to ignore the scanner. Ambiguity within the module unions
toward RISK; across modules the receiver's own module owns the proof.

## Adversarial round 16 (2026-08-21, five codex seats — the fix layer again)

Five seats on the r15 fix layer. **REJECT, and the census-by-property
lesson proved itself immediately**: nine deduped findings, three HIGH
clusters, every one of them a twin the seventeenth lesson's property
census finds and the by-file census missed. Every probed claim real —
the twelfth consecutive zero-hallucination round.

- **Absence was read as a decision** (three seats, HIGH, probed).
  Every `_save_skills` caller passes a list built from an UNLOCKED
  `load_skills()` snapshot, and the writer's contract read "proven row
  absent from the list" as "deliberately deleted" — so a skill saved
  by a concurrent process between the snapshot and the rewrite was
  silently destroyed, with no archive copy (the cull archives only
  what it SELECTED). A deliberate drop must now be NAMED: the writer
  takes `dropped_ids`, absence is carried verbatim in ordinal, and the
  three destructive callers (island cull, A/B retirement, evolver
  rollback) name their drops. Residual recorded: a named id whose row
  was updated after selection still drops, archive holding the
  pre-update version — the id was leaving the pool either way; upgrade
  edge is a transform-style primitive that re-derives selection inside
  the lock.
- **The raise travelled; the callers did not** (four seats, HIGH,
  probed). r15 made the recorders raise on failure — and
  memory_ledger's attribution loop applied a run's manifest one id at
  a time, so a mid-list failure became a reachable partial batch: id A
  committed, id B failed, the idempotence marker never written, and
  the retry credited A twice. Permanently skewed training evidence.
  `record_skill_injection_outcomes` (batch) commits every id in one
  write or none; the seam calls it once. The marker-window residual
  (crash between commit and marker) is recorded next to its BACKLOG'd
  twin, the interrupt F9 ack-before-apply design item.
- **The alias lattice stopped at the module** (four seats, probed).
  Class-body aliases (`Outer.Alias = Base`), factory-local aliases,
  and a rebound class name (`class Safe: ...; Safe = Dangerous` — the
  literal class short-circuited the alias) all severed inherited
  decoder provenance one round after aliases became provenance. Every
  scope now feeds the map (flattened — imprecision UNIONS candidates,
  the RISK direction), and a name that is both a class and an alias
  carries both provenances.
- **The durable append could fuse rows** (one seat, HIGH, probed).
  Appending onto a crash-torn unterminated tail concatenated the new
  row onto the fragment — one malformed line where the retention copy
  should be, while the live delete stood. `locked_append` now frames a
  torn tail with an LF first: fragment bytes untouched, strandable,
  new row readable.
- **The require-lock property had three more members** (two seats,
  probed). `save_skill`, `_save_skills`, and `locked_rmw` — the
  generic RMW primitive whose docstring says "without lost updates" —
  all rode bare `locked_write`. All require now; fail-open in an RMW
  primitive is self-contradictory.
- **The pool writer returned None for failure** (two seats, probed).
  `_save_skills` caught everything, warned pathlessly, returned — a
  cull could report "retired" with every skill still live. It raises
  and names the store now (the error-result twin of r15's recorder
  fix, found by the property census).
- **The recorder announcement covered one failure of three** (two
  seats, probed). r15's error log wrapped only the write, so lock and
  read failures raised unannounced — and two of the three production
  catch sites logged at DEBUG, making a lock outage indistinguishable
  from missing telemetry. The try covers the whole transaction now;
  the DEBUG sites are WARNING.
- **The commit boundary could lie** (one seat, LOW, probed). SQLite
  can report an error from `commit()` AFTER the transaction became
  durable; the inner handler then said "store unchanged" about a store
  that holds the transform. A commit that was ISSUED and raised is now
  announced as outcome-UNKNOWN — inspect before retrying.
- **The tuple subclass broke the copy protocols** (four seats, LOW,
  probed). Default tuple reduction rebuilds `_StatsRead` from one
  argument, so copy/deepcopy/pickle raised TypeError. `__getnewargs__`
  carries all three fields.

The sweep came back 257/258 (251 detected + 6 standing equivalents);
the survivor taught about the fix again — the copy-protocol mutant
zeroed `__getnewargs__`'s third element and could not fail, because
pickle restores the instance `__dict__` (which carries `.compacted`)
after `__new__`. The method's existence is the fix; the mutant was
retargeted at removing it. 258/258 after.

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

The fourteenth is round 12's, and it is the eighth lesson (denylists)
wearing AST clothes: **provenance, not final call syntax, decides — a
rule keyed on spelling invites one bypass per spelling.** Five seats
each walked past the decoder rule with a different standard Python
spelling of the same dataflow: an import alias, an object alias, a bound
method, an annotated assignment, `raw_decode`. A syntactic match on the
call shapes you have met is a denylist; the fix is to track where the
VALUE came from, resolve aliases to a fixpoint, and let every call site
inherit the receiver's verdict. Two corollaries from the same round:
**a queue must not accept under a weaker predicate than its consumer
delivers by** — the producer is a door too, and an accepted-but-
undeliverable row is a promise the store cannot keep; and **a shared
emission door beats N copies of the same proof** — once three writers
each need "serialize, re-read through the strict reader, require the
reader's shape", the proof belongs in one helper the writers cannot
drift from.

The fifteenth is round 13's: **doctrine that does not travel to the twins
is local luck.** The JSONL backend spent three rounds earning its doors
while its selectable SQLite twin — one env flip away — kept the exact
silent-drop-plus-DELETE-all composition r11 called unanimous; the
retention archive, the mechanism of the retention decree itself, was a
writer nobody had audited; and `_parser_names` kept a private Assign-only
walk one round after `_bindings` learned the other forms — the r12
half-conversion lesson, replayed on the file that teaches it. When a rule
hardens, enumerate its twins by ROLE (config twin, sibling writer, private
copy of a shared walk) and convert them in the same commit. Corollary:
**presence is not absence** — `d.get(name)` collapses "stored null" into
"not stored", and any predicate built on it exempts exactly the value the
constructor launders most quietly (`bool(None)`); check membership, then
check the value.

The sixteenth is round 14's, and it is the fifteenth turned on itself:
**the fix for "doctrine must travel" must itself travel — convert the
twins in the SAME commit, not the same arc.** r13 wrote the
twin-conversion lesson and its own fix layer shipped `transform()` to
one backend of two, an ordinal rule to one writer of two, and a shared
binding walk to two call sites of three; round 14 was almost entirely
that gap, plus the twins' own corollaries: **an error result must not
be a valid value** (a failed read returning `[]` hands the caller a
verified-empty store — raise, or return something a rewrite cannot
consume), and **a degraded mode must not include the failure the
feature exists to prevent** (a one-lock transaction that honors
fail-open is the race wearing the fix's clothes — require the lock).
Enumerate the twins BEFORE writing the fix; let the round's fixtures
double as the census; and when a negative control fails during the
fix, treat it as the census working — round 14's own control surfaced
the third private copy of the binding walk that no reviewer had found.

The seventeenth is round 15's, and it sharpens the census the sixteenth
asked for: **enumerate the twins by the PROPERTY, not by the file.**
Round 14 converted the twins it could see from the defect's own file —
and each of its fixes still stopped one twin short, because the twin
lived behind a different spelling of the same property: require-the-lock
reached the backend named in the finding but not the two recorders doing
the same read-modify-write two files away; announce-after-commit reached
the strandee lane but not the duplicate lane six lines up; strict-reads
reached the stats input but not the skills input in the same function.
The census that works is "what else HOLDS this property" (every RMW over
a keyed store, every destructive-claim log line, every training input),
not "what else is in this file". And two corollaries earned their own
sentences: **a retention copy must be at least as durable as the
deletion it authorizes** (an fsynced delete paired with a page-cache
archive is retention theater), and **a post-failure message must state
what the store HOLDS, not what the code intended** ("store unchanged"
after a successful commit is an instruction to double-apply).

The eighteenth is round 16's, and it has two halves. First: **changing
a callee's failure contract re-opens every caller as new surface — the
composition path is part of the fix layer.** r15 made the recorders
raise, correctly; the caller applying a manifest one id at a time
turned that raise into a reachable partial batch with a double-count on
retry. The callers were censused for "do they crash" (they all wrapped)
but not for "does their loop still compose" — a failure-contract change
is not shipped until the literal production path through it has been
re-read. Second: **a destructive interface must take its deletions by
name, not by omission.** "Absent from the list" is ambiguous between "I
decided to remove this" and "I never saw this" — and the writer cannot
tell which, so for years it resolved the ambiguity in the destructive
direction. An explicit `dropped_ids` makes the decision the caller's,
visibly, and turns every unnamed absence into a carry.

## Round 17 (2026-08-21): a write must be named too

Five codex seats on the r16 fix layer (087365bd + 15070f48). Twelve
deduped finding clusters, three multi-seat HIGHs, and the THIRTEENTH
consecutive zero-hallucination round — every cluster reproduced by
probe before any fix. Convergence was again judged NOT reached (three
HIGH clusters in the previous round's fix layer).

The centerpiece is the write half of r16's deletions-by-name lesson.
r16 taught `_save_skills` to carry any live row absent from the
caller's list unless the id was named in `dropped_ids` — and three
seats independently showed the collision twin: a row PRESENT in the
caller's stale snapshot still replaced the live row wholesale, so a
concurrent `save_skill(B)` was silently reverted by any unrelated
caller that loaded before it and saved after it (utility updates,
demotion, island assignment, variant outcomes — every bulk caller).
The fix is `updated_ids`, the write twin of `dropped_ids`: only a
named id takes the caller's version; every other live row — including
ids the caller's list holds a stale copy of — carries verbatim in
place, and an unnamed stale copy cannot resurrect a row another
process deleted. All eleven call sites now name their writes; the A/B
retirement site turned out to mutate its promoted parents too, which
only the census caught. Contradictory intent (an id both updated and
dropped, an updated id absent from the list, a dropped id still in the
list) is refused before the lock touches anything.

The rest of the round, by family: `locked_append`'s tail inspection
failed OPEN (`except OSError: needs_frame = False`) — an unreadable
tail now refuses the append rather than risk fusing the retention
archive's only copy. The evolver's `skill_create` rollback matched by
mutable name-or-id and deleted every match with no retention copy —
the created id is now minted at before_state capture and recorded in
the audit row, rollback removes exactly that id (legacy rows keep the
name match), and every removal archives first; its suggestions-store
bookkeeping failure was a bare `except: pass` inside a result claiming
the revert completed — now warned with the store path and carried in
the returned detail. The attribution seam trusted `marker.exists()`
(a zero-byte marker silently suppressed a run's verdicts), checked it
outside any lock (two live stampers could both pass and double-apply),
and reported a marker-write failure with the pre-commit "NOT recorded"
message after the batch had committed — check→batch→marker now runs
under the stats lock, marker content is validated (invalid = UNKNOWN,
warned, never auto-re-applied — the batch may already hold), and the
two failure legs say what the store holds. Manifest ids were
str()-coerced ("True"/"7" minted as stats identities) — both readers
now admit only non-empty strings and announce exclusions. The batch
recorder double-counted duplicate ids and would have iterated a bare
string character by character — both doors added. SQLite append() and
rewrite() joined transform's three-way commit-boundary contract
(UNKNOWN after a raised commit). And the r16 scanner's flattened alias
map let an unrelated function's `Alias = Dangerous` mint false RISK on
a module-level class — aliases now resolve per class along its lexical
chain for Name bases plus attribute-reachable bindings for dotted
bases, with the negative control pinned and every r15/r16 must-detect
fixture still passing.

One partial accept: the Architect's demand that backend `rewrite()`
stop deleting by omission. The interface's collections are unkeyed, so
carry-by-omission cannot apply; `transform()` already exists as the
sanctioned in-lock mutation API and `rewrite()` has zero production
callers outside transform's own in-lock delegation. What WAS false was
the docstring's claim that the loss is "announced by design" — no
announcement is possible through a bare List[Dict]; corrected, with
the interface upgrade (deletions by name for the backend ABC)
BACKLOG'd. One rejection: writer-side manifest validation — admission
belongs to the reader (receiver-decides, r11/r15 precedent).

Mutation spec: 11 anchors re-anchored after the restructures (the
sqlite commit pattern now exists in three write paths, so the
transform anchors gained disambiguating context; the r16 ledger
mutant's structural pin was indentation-bound and its re-nested per-id
loop would have walked past it — the pin is now regex-based and
indentation-agnostic). 21 new r17 mutants, 258 → 279.

The sweep came back 278/279; the survivor was the round's own
guard-in-front-of-a-guard: `if False:` on the contradictory-intent
overlap check passed its raise-only test, because every overlap input
also trips a sibling door — an overlapping id is either in the
caller's list (still-present door) or not (absent-from-list door), so
raise-vs-not cannot see the overlap door at all. Its contribution is
the MESSAGE: the operator reads "named both updated and dropped"
instead of a misleading sibling diagnosis. The test pins each door's
message with `pytest.raises(match=...)` now; 279/279 after. Fourth
appearance of the pattern in this arc, first time on a fixture written
in the same round as the guard it failed to distinguish.

### The nineteenth lesson

**A mutable interface must take its writes by name, not by
possession.** r16 named the deletions; r17 found the same ambiguity in
the write direction: "this id is in my list" is ambiguous between "I
changed this row" and "I happened to load it", and the writer resolved
the ambiguity in the destructive direction — the stale copy won. The
general form covers both halves: every mutation class a bulk interface
can perform (write, drop) carries explicit per-id intent, and
everything unnamed defaults to the non-destructive action (carry the
live row). The corollary that closed the marker findings: **presence
is not proof — an idempotence token must be validated against what
completion would have written, and checked inside the transaction
boundary it guards.** A zero-byte marker passed `exists()`; two live
stampers both passed it outside the lock; and a marker that failed to
write after the batch committed was reported with the pre-commit
message. The token is only meaningful as part of the transaction it
acknowledges.

## Round 18 (2026-08-21): a claim must be checked against the world

Five seats on the r17 fix layer (42b25a75 + fc96ff40). **Lane note,
declared per the skill:** codex hit its usage cap mid-round (capped
until 08-27), so the seats ran as `claude -p` **sonnet-medium** — the
same-model fallback the adversarial-review skill sanctions when the
opposite CLI is unavailable. The zero-hallucination streak is a
codex-lane metric and is not extended by this round; for what it is
worth, all six accepted clusters here reproduced under my own probes
too, and every seat supplied literal probe output.

Twelve findings deduped to six clusters, all probed real:

- **The marker validated the verdict by TYPE, not value** (Architect +
  Minimalist + Failure Operator, all HIGH, all probed independently —
  the round's headline). `isinstance(m.get("goal_achieved"), bool)`
  accepted a marker whose verdict a later stamp had legitimately
  CORRECTED — `stamp_outcome_verdict` re-stamps by design (decree
  2026-08-10: corrections may flip a verdict but be honest about it) —
  so the correction never reached skill stats and *nothing was
  logged*. The fix compares `m["goal_achieved"] == achieved`: a
  flipped verdict still must not auto-re-apply (the committed batch
  cannot be decremented in place), but it is announced ("a corrected
  verdict does NOT auto-adjust skill stats"), never absorbed.
- **Naming is not creation** (QA, HIGH, probed): r17's tail append —
  "an updated id whose live row vanished" — silently resurrected a
  row a concurrent cull/retirement/rollback had deliberately dropped,
  reasoning and archive trail gone. No call site creates rows through
  `_save_skills` (the census checked), so a named-but-absent write is
  now dropped and ANNOUNCED; the deletion stands.
- **The audit row and the action read the world twice**
  (Skeptic + Architect, HIGH, probed): `_apply_suggestion_action`
  captured before_state from one `load_skills()` and decided
  create-vs-update from a second, so a racing same-name create turned
  the action into an UPDATE that overwrote the concurrent skill while
  the audit row recorded a phantom `skill_create` with an id that was
  never written — and `revert_suggestion` then hunted that phantom id
  and silently failed. One snapshot now drives both.
- **A mutated-but-unnamed row died in silence** (Failure Operator):
  the contradiction ValueErrors cannot see an *omission*, so
  forgetting to name an edited id discarded the edit with no signal.
  Unnamed rows whose modeled content differs from the live store are
  warned post-commit (content_hash excluded — it is derived).
- **Commit-boundary honesty narrowed to sqlite errors** (Skeptic;
  test gap QA): an OSError from `close()` after a successful commit
  escaped without the "store HOLDS — do not retry" message. Handlers
  widened to `Exception`; close-bomb tests now pin the committed
  branch for append and rewrite, including the non-sqlite shape.
- **Two small honesty fixes**: the drop announcement counts physical
  rows, not just ids (Architect — duplicate legacy rows announced
  fewer removals than performed); the content-hash backfill is scoped
  to named writes (Minimalist — only named rows are serialized since
  r17, so backfilling unnamed in-memory copies stamped hashes the
  store never held).

Judged, not fixed: the global stats lock serializing ALL runs'
attribution (Architect MED) is recorded as a scale note in BACKLOG —
correctness-safe, and injection volume on this box is nowhere near the
choke point; a per-loop marker lock is the named upgrade edge.
Rejected: stamping the manifest's malformed-count into the marker
(Failure Operator LOW) — id-set equality already moves under any
admission change; the residual coincidence shape is contrived.

### The twentieth lesson

**A claim must be checked against the world it claims about.** Three
shapes of one defect this round, all in the previous round's own fix
layer: a named write claimed a row the world no longer held (and the
claim was honored as a create); a marker claimed a verdict it merely
resembled in type (and the claim was honored as proof); an audit row
claimed a create the action's world contradicted (and the claim
became the rollback's target). In each case the claim was validated
against its own SHAPE — the id is well-formed, the field is a bool,
the row parses — when the defect was in its relation to the current
world: does the row still exist, does the value match, did the action
read the same world the record describes. Shape validation catches
corruption; only world validation catches staleness. The r17 lesson
said a write must be named; r18's coda is that the name is only half
— the other half is checking the named world still holds.

The sweep came back **290/290 accounted for on the first pass** (284
detected + the 6 standing equivalents surviving as claimed) — the
arc's third zero-survivor, zero-SKIP first run, and the first for a
round whose fixes rewrote this much of the previous round's own new
code: four moved anchors were re-anchored before the sweep, and every
new mutant was written against a fixture that already existed.

## Round 19 (2026-08-21): decide from the snapshot, write from the world

Five sonnet-medium seats (codex still capped) on the r18 fix layer
(9810ef68 + 769159bb). Twelve findings deduped to four clusters, all
verified against the code, every seat carrying literal probe output:

- **The r18 divergence warning lied about its cause** (four seats, the
  round's widest agreement): "the caller's edit was NOT applied"
  cannot be distinguished, from inside `_save_skills`, from a
  concurrent NAMED write legitimately moving a row after the caller's
  snapshot — the exact case r16/r17 carry silently by design, and the
  steady-state shape under load. A warning that is usually a false
  accusation trains operators to ignore the honest firing. The message
  now states the fact and names both causes.
- **The r18 snapshot-reuse widened a real data-loss window** (four
  seats, HIGH, probed in both directions — the pre-fix code preserved
  a concurrent field advance, the r18 code reverted it): reusing the
  capture-time snapshot for the UPDATE write put the audit-trail I/O
  inside the stale window, and `save_skill`'s whole-row replace
  silently reset an open circuit breaker and undid a utility
  demotion. The snapshot now decides CLASSIFICATION only; the row
  mutated is re-read fresh immediately before the write, and a row
  that vanished in the window refuses the update — naming is not
  creation, applied to the evolver too.
- **The ghost message over-claimed** (three seats): "concurrently
  removed; the deletion stands" fired for a named id whose row was
  physically PRESENT but unprovable (stranded verbatim in the same
  commit), and for ids never created at all. Three truths now told
  apart: present-but-unprovable ("repair and retry", ids recovered
  from rows that parse but fail the proof), truly absent
  ("concurrently removed or never created" — hedged when byte-tainted
  rows whose ids are unrecoverable rode the rewrite), and nothing
  guessed.
- **transform() attributes caller fn bugs to the storage layer**
  (three seats, LOW, judged accepted): the widened handler's message
  is factually true (the rollback ran, the store is unchanged) and
  the exception propagates unwrapped; a comment now marks the
  attribution seam.

### The twenty-first lesson

**Decide from the snapshot, write from the world — and announce only
what you proved.** Two halves. r18 fixed a classification lie by
sharing one snapshot, and in doing so quietly widened the data
window: a snapshot is the right authority for a DECISION (create or
update, drop or carry, what the audit row claims) but the wrong
authority for the BYTES WRITTEN — those must come from the freshest
read the lock allows, or every field the world advanced in between is
reverted by an unrelated caller. And when a function announces what
it did, the announcement may state only what its own scan proved:
"absent from every parseable row" is provable; "concurrently removed"
and "the caller's edit" are guesses wearing the voice of fact, and a
guessed cause in an operator-facing warning is how the one true
firing gets ignored. Name the ambiguity when you cannot resolve it.

Sweep: 294/295 on the batch run, 295/295 accounted for — the lone
"survivor" (`the divergence warning goes silent`) is DETECTED reliably
when re-run in isolation against the identical commit; recorded as a
batch-run anomaly (suspect: inter-mutant state in the shared sweep
copy or a caplog capture flake), the first of its kind in this arc.

## Round 20 (2026-08-21): deletions earn the same truths

Five sonnet-medium seats (codex still capped) on the r19 fix layer
(195df396 + 821c2fce). Eleven findings deduped to four clusters, all
verified against the code, every seat carrying literal probe output —
and every cluster a hole in the PREVIOUS round's own fixes, which is
what a fixpoint loop is for:

- **Named DROPS never got r19's three-way treatment** (five seats,
  HIGH, the arc's widest agreement yet): r19 taught named WRITES to
  tell present-but-unprovable from truly-absent, and left deletions
  with r16's two-valued world. A drop naming a stranded row silently
  no-op'd — the caller believes the row is gone, the row is live in
  the store — and a drop that removed the provable row said nothing
  about an unprovable duplicate carrying the same id straight through
  the rewrite. `stranded_dropped` and `partially_dropped` now get the
  same announcements writes earned: "NOT applied — present but
  unprovable, carried verbatim; the row was NOT removed; repair, then
  confirm the drop" and "removed the provable row(s), but unprovable
  duplicate row(s) remain".
- **The ghost hedge counted only byte-tainted rows** (two seats): an
  unprovable row that parses as JSON but whose id field is missing or
  unreadable was in nobody's ledger — the ghost message asserted "no
  parseable live row holds these id(s)" over a row that might hold
  exactly that id. New `unprovable_unnamed` counter; the hedge now
  covers both unreadable-id populations.
- **r19's fresh re-read left the TOCTOU open** (three seats, HIGH):
  the re-read moved the stale window from seconds to milliseconds and
  called it closed — no lock spanned read→write, so `save_skill`'s
  blind upsert could still revert a circuit-breaker trip landing in
  the tail, and resurrect a row removed there. The whole
  read-modify-write now sits inside `locked_write(require=True)`;
  `file_lock`'s thread-reentrancy lets `save_skill`'s inner
  acquisition compose while cross-process writers are excluded for
  the full span. The pin is STRUCTURAL (lock-line precedes read
  precedes write, via `inspect.getsource`) because a behavioral race
  test cannot see reentrant composition: in-thread injection rides
  the reentrancy, cross-thread injection deadlocks by design.
- **The revert destroyed later edits and called it clean** (two
  seats, HIGH, probed): `revert_suggestion` restored the snapshot
  `old_description` over a concurrent edit made AFTER the apply,
  reporting `reverted: True` — and the apply path clobbered
  concurrent description edits with no announcement. Revert now
  refuses when the live description is no longer the suggestion's own
  text ("blind restore refused"); apply announces the clobber and
  names the audit row's snapshot basis.

Judged, not fixed: a distinct "vanished" status for suggestion rows
whose skill disappeared mid-apply (the log line already names the
cause; a status-enum change ripples through every consumer for no new
information), and zombie unprovable duplicates accumulating across
rewrites (real, slow, BACKLOG'd — the repair verb is the fix, not
more carrying).

### The twenty-second lesson

**A fix teaches one verb and the reviewers ask about its siblings.**
Every cluster this round was the previous round's fix applied to the
operation standing next to it: writes learned three truths, drops
kept two; the re-read shrank the window, the lock was the actual
answer; the apply got snapshot discipline, the revert kept trusting
its own. When a round hardens a verb, the next round's first question
is which verbs share its preconditions and didn't get the treatment —
asking it yourself before the reviewers do is how a fixpoint
converges instead of oscillating. The corollary is r20's own version
of naming-is-not-creation: a DELETION is a write. It needs the same
proof standard, the same three-way honesty, and the same refusal to
report an effect it cannot demonstrate.

## Round 21 (2026-08-21): the sibling verbs get the treatment

Five sonnet-medium seats (codex still capped) on the r20 fix layer
(2286a89e + 4e459283). Findings deduped to three clusters, all
verified — two of them probed live by the seats with mid-window
injection — and all three are the twenty-second lesson firing on its
first outing: r20's own fixes, missing from the operation standing
next to them.

- **The drop buckets were blind to tainted and id-less rows** (four
  seats, HIGH): r20 built `stranded_dropped`/`partially_dropped`
  entirely from `strand_ids`, which only unprovable rows with
  RECOVERABLE ids feed. A named drop whose sole live row was
  byte-tainted landed in no bucket — silent no-op, no line naming the
  id — and when a provable copy was removed while a tainted duplicate
  survived, "removed by this rewrite" fired as an unhedged
  completeness claim the scan never proved. New `unaccounted_dropped`
  bucket: named drops in no bucket are announced with the hedge
  ("could NOT be verified ... if one does, that row was NOT removed")
  whenever unreadable rows rode the rewrite; a provably absent drop
  stays silent (absence proven = vacuous success); the removed
  announcement hedges over carried unreadable rows.
- **The revert held no lock** (three seats, HIGH, probed live):
  `revert_suggestion`'s skill_update branch — changed in the SAME
  r20 commit that locked the apply path — kept the unlocked
  read→guard→write shape, so the r20 guard checked a stale snapshot
  and a concurrent edit landing between the check and `_save_skills`
  was still destroyed under `reverted: True`. The whole branch now
  reads fresh and writes inside `locked_write(require=True)`, with
  the same structural source-order pin and the same reentrancy
  rationale as the apply path.
- **The guard fell through when it could not verify** (five seats,
  the round's widest agreement): `if _sugg_text and ...` skipped the
  entire guard when `suggestion_text` was falsy — reachable today
  (an empty LLM suggestion writes `suggestion_text: ""` into the
  audit row), not just on legacy rows — silently reinstating the
  pre-r20 blind restore. An unverifiable revert now refuses
  ("suggestion_text unavailable — blind restore refused").

Accepted (LOW, note only): stranded-side messages count logical ids,
not physical rows — preserve the distinction if strand tracking ever
extends to tainted rows.

### The twenty-third lesson

**A guard that cannot verify must refuse — and a claim of
completeness must hedge over everything it could not read.** Two
shapes of the same failure. The falsy-`suggestion_text` bypass is the
canonical guard anti-pattern: `if evidence and evidence_says_stop:`
reads as caution and is its opposite — the less evidence the guard
has, the more freely the destructive path runs, so the guard is
strongest exactly when it is least needed. Fail closed: no evidence
means refuse, and say why. And "removed by this rewrite" over a store
carrying rows nobody could read is the message twin: an affirmative
claim whose scan had holes in it. The hedge is not hand-wringing — it
is the difference between a message the operator can act on and one
that trains them to trust a lie. Both are the twenty-second lesson's
corollary made mechanical: when you harden a verb, its guard and its
announcement are siblings too.

## Round 22 (2026-08-21): retention archives the world it deletes

Five sonnet-medium seats (codex still capped) on the r21 fix layer
(9f4a313f + f7d0fdf3). Three clusters — and for the first time in the
arc, two seats returned "confirmed sound, no defect found" on entire
clusters, with probes: the r21 drop-bucket partition and the
skill_update lock both survived five adversarial reads. The round's
real findings:

- **The create revert archived a stale snapshot** (three seats, HIGH,
  probed live): `revert_suggestion`'s skill_create branch built its
  removal — and therefore the ARCHIVED copy, the retention decree's
  only recovery path — from the shared pre-lock `load_skills()`
  snapshot. A concurrent edit racing the revert vanished from the
  live store AND the archive: the deletion was id-keyed and safe, the
  retention was not. One seat traced the same shape to the
  r16-accepted cull residual; with the lock machinery now one line
  away, acceptance is no longer the cheap answer. The branch now
  reads fresh, archives, and writes inside one
  `locked_write(require=True)` — and the shared pre-lock snapshot is
  REMOVED entirely, so no branch has a stale authority left to
  shadow.
- **Absent conflated with empty** (two seats, HIGH, probed): r21's
  fail-closed guard refused on falsy `suggestion_text`, but an apply
  whose suggestion was `""` wrote `""` as the description — a fully
  VERIFIABLE value. Refusing it made every empty-suggestion apply
  permanently un-revertible, stranding verify_post_apply's
  auto-revert safety net for a shape reachable today. Absent (key
  missing — legacy) still refuses; empty now verifies.
- **The lock had no committed behavioral pin** (Architect, MEDIUM):
  and this round's own fix attempt PROVED the standing rationale — a
  mid-window in-thread injection rode the reentrant lock straight
  into the critical section, so the behavioral test cannot exist.
  Committed instead: a no-read-precedes-the-first-lock pin (the
  outer snapshot cannot quietly return), the create-branch
  source-order pin, and a live archive-content test.

Judged: verify_post_apply's warning-only handling of a refused
revert — BACKLOG'd, not built (fix B removes the common case; the
residual is legacy-rows-only; a new verdict enum mid-arc repeats the
churn r20 rejected). The "repair, then confirm" phrasing naming a
verb that does not exist — standing r19 note; the surface grew.

### The twenty-fourth lesson

**Retention is a read too — archive the world you delete, not the
world you planned to delete.** The deletion was safe: id-keyed,
self-verifying, carried by a writer that re-reads under its own
lock. The SAFETY NET was not: the archived copy serialized whatever
the pre-lock snapshot held, which is exactly backwards — the archive
exists for the case where the deletion turns out to be wrong, and
that case is likeliest precisely when the world moved between
snapshot and delete. A recovery path built from stale bytes is a
recovery path that fails exactly when invoked. Corollary, from the
same round's other cluster: fail-closed has a precision obligation —
"cannot verify" must mean CANNOT, not "the evidence is falsy."
Refusing a verifiable case is not caution; it deletes the undo
button and calls it safety.

## Round 23 (2026-08-21): the decorative test goes; a revert that cannot recognize its row refuses

Five sonnet-medium seats (codex still capped) on the r22 fix layer
(85fc8115 + 4b9faf0e + 403f4a86). The round's shape marks the arc's
turn toward convergence: the only multi-seat agreement was about a
TEST, not production code, and every production finding was a
degenerate-input edge or a pre-existing gap the round's scope
surfaced — no fix from r22 itself was wrong.

- **The archive-content test was decorative** (four seats, the
  round's widest agreement — each independently reran it against the
  pre-fix f7d0fdf3 and watched it pass): the racing-load injection
  fires inside the single `load_skills()` call both code shapes
  make, so it cannot distinguish read-before-lock from
  read-inside-lock. DELETED. The two structural pins — which every
  seat verified DO fail on the pre-fix code and which the mutation
  spec wires as the killers — are the honest coverage, exactly as
  the r22 rationale conceded a behavioral pin cannot exist here. The
  lesson-22 corollary applies to tests too: a test that cannot fail
  in the direction it was written for is a claim, not a guard.
- **An unrecognized `before_state.type` claimed success** (Failure
  Operator, MEDIUM, probed, pre-existing): a corrupted audit row's
  type fell through both dispatch branches and the tail returned
  `reverted: True` with `detail: ""` — stamping applied=False,
  writing EVOLVER_REVERTED to the captain's log, and telling
  verify_post_apply the rollback succeeded while the mutation stayed
  live. Now refuses by name.
- **Non-string `suggestion_text` slipped every door differently**
  (Skeptic HIGH + two seats LOW, probed): `0` coerced to `""` and
  could blind-restore over a live `""`; a list refused through the
  misleading "description changed" message; an int threw
  `TypeError` into the generic handler as "revert failed:
  slice(...)". Present-but-not-a-string is now CANNOT VERIFY.

Also shipped: an undisturbed missing-text fixture (qa — the
missing-text mutant's kill was riding the description-changed door's
detail string). BACKLOG'd: the r22 lock holds a durable fsync'd
archive append inside the skills-store lock (Architect — deliberate
correctness-over-throughput trade, recorded so a future latency hunt
doesn't rediscover it from the diff).

### The twenty-fifth lesson

**Prove the test against the defect, not against the fixed code.**
Four seats caught in one round what the author missed while writing
it: the archive-content test was green from birth on both sides of
the fix, because its injection lived inside the very call whose
placement the fix changed. A regression test's birth certificate is
a run against the code it claims to catch — the same discipline the
mutation spec enforces mechanically ("a mutant is written with its
killing fixture") applied to hand-written behavioral tests, where
no runner checks it for you. The corollary closed this round's other
two holes: a dispatcher's ELSE is part of its contract (fallthrough
plus a shared success tail is a forged receipt), and evidence has a
TYPE — a guard comparing recorded text must first prove the record
is text, or the comparison launders garbage into a verdict.
