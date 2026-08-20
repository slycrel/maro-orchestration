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

The second lesson is from the three rounds, not the scan: **every round's top
finding was a defect the previous round's fix introduced** — r1 found the lock
that r0's fix scoped too narrowly, r2 found the `applied` flag that r1's fix
inverted, r3 found that r2's "validation" validated nothing. None was in the
original code. Review the fix layer first; it is the only part of the change
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
