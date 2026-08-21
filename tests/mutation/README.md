---
status: living
---

# Mutation specs

Must-detect mutation lists, one JSON file per surface, run by
`scripts/mutate.py`. The runner's docstring is the format reference; this
file is the convention and the coverage ledger.

```bash
python3 scripts/mutate.py tests/mutation/scope_14a.json
python3 scripts/mutate.py tests/mutation/scope_14a.json --only quarantine
```

These are **not** collected by pytest and do not run in CI — a sweep is
minutes of targeted test runs and the value is in the reading behind the
list, not in re-running it on every push. Run one when you change the
surface it covers, and when you add coverage to a new surface.

## Why the spec file is the artifact

Derive the mutation list from **reading the file, not from your own
diff** (HOUSE_STYLE step 3; Jeremy 2026-08-16). A diff-derived list only
tests whether your fixes are pinned. A file-derived list tests whether
the behavior is — and in the arc that produced this directory, five
adversarial review rounds and two diff-derived harnesses walked past
twelve real gaps, the worst being a decree guarded by two tests that
could not fail.

Committing the spec answers "what did we actually probe?" months later.
Before this, each round wrote a throwaway harness in a scratchpad and
the answer was unrecoverable by the next session.

## Conventions

- **One anchor, one match.** The runner refuses an anchor matching 0 or
  2+ times, because a mis-applied mutation silently reads as a pass.
- **The baseline must be green.** Every distinct `tests` target is run
  once unmutated before anything is applied, and a red baseline aborts
  the sweep. DETECTED is only "pytest exited non-zero", so without this
  a broken environment reports a perfect run — the instrument built to
  find false confidence manufacturing it. Added 2026-08-16 after the
  Experimentalist lens found the hole; the three green specs on record
  were re-run under the gate and all held.
- **A survivor is a hole in the suite**, not a mutation to delete.
  Strengthen the test.
- **Mark genuinely equivalent mutants `equivalent`** with the reason
  they cannot fail. They are still applied and run; a *detected*
  equivalent fails the run, because it means the stated reason is wrong.
  Contorting a test to kill an equivalent mutant is how a suite starts
  testing its own mocks.
- **The sweep runs against HEAD, not your tree.** `mutate.py` mutates a
  clean copy of the committed state, so uncommitted work is invisible to
  it: anchors on lines you have only edited locally come back `SKIP —
  anchor matched 0x`, and mutations of code you have only locally fixed
  come back SURVIVED. Commit first, then sweep. (The skip message is
  loud for exactly this reason — a skipped mutation is not a passed one.)
- **Name the surface, not the file.** Coverage below is per *surface* —
  a spec covering the scope paths in `knowledge_web.py` says nothing
  about the other 85% of that module. Do not read "swept" off a
  filename.

## A SURVIVED verdict is a lead, not a fact

Same discipline as verify-before-fix on a reviewer finding, and it bites
for the same reason. Two ways this runner reports a survivor that isn't
a hole, both hit on the first sweep after the tool landed:

1. **Too narrow a `tests` target.** A mutation aimed at
   `knowledge_web.py` but run only against `test_lesson_provenance.py`
   "survives" work that `test_knowledge_web.py` does pin. Scope the
   field to every file that plausibly covers the site — the run is
   slower and the verdict means something.
2. **A redundant downstream guard.** Removing `promote_lesson`'s own
   `_is_quarantined` check still refuses the promotion, because another
   check later in the flow catches it. Neutering the *predicate*
   promotes the row, so the invariant IS tested; the individual guard is
   defense in depth. Probe before calling it a hole: monkeypatch the one
   thing and see whether the behavior actually changes.

A third shape showed up on the dispatch-envelope sweep, and it is the one
worth arguing with: **two guards, one test.** `_safe_name` prevents
traversal twice — a basename call and a character whitelist — and
`test_traversal_names_are_flattened_to_basenames` passes with either one
removed. So the test pins *neither*, and both mutations survive. The
lazy resolution is to mark both equivalent and move on; the right one is
to ask what job only ONE of them does. The whitelist also scrubs spaces,
`;`, newlines and `$(...)` out of a dispatcher-chosen filename, nothing
tested that, and pinning it turned one survivor into a real test while
leaving the other honestly equivalent. Redundancy in the *code* is fine;
redundancy that makes a test unable to distinguish its subject is not.

Both are recorded in the spec rather than in someone's memory —
`equivalent` for a settled one, `unverified` for a survivor that hasn't
had the probe yet. An unprobed survivor left unmarked reads as a
confirmed hole to the next reader, which is the same false-confidence
failure the sweep exists to find.

## Coverage ledger

| Spec | Surface | Mutations | Swept |
|---|---|---|---|
| `scope_14a.json` | §14a scope/stamp/portability: `knowledge_web` scope screens, tiered-store load/rewrite/mutate, quarantine, reinforce heal, `_tfidf_rank_scored`; `camera_readout` census, `_lesson_origins`, `_stamp_coverage`, `_scope_rollup`, `_print_portability`; `pack` lesson transport border | 34 + 1 equivalent | 2026-08-16, all accounted for |
| `provenance_gate.json` | The db37d525 contamination gate: `lesson_provenance` classifier sub-patterns + killswitch string/exception handling, the `_is_quarantined` predicate and its six enforcement sites in `knowledge_web` (both graveyard legs, both promote scans, effect-promotion, canon), the `memory_ledger` mint choke point | 16 + 3 equivalent | 2026-08-16, all accounted for — **CLOSED** (landed red at 6/18, reopened and closed same day) |
| `path_rewrite.json` | Embedded-path rewriting on transfer: `path_rewrite` root validation + ordering + both match boundaries + skip screens + atomic swap and its post-commit half, the `maro-export import` wiring (extracted-files-only list, provenance gate, custody `transformed`), the `maro-import --source` wiring (rewrite-before-dedup, marker verbatim, per-file quarantine, dry-run honesty, unresolved-source mapping) | 39 + 2 equivalent | 2026-08-16, all accounted for (10 added after the review round, 1 after the live import) |
| `dispatch_envelope.json` | The typed dispatch boundary: `parse_dispatch_payload` shape/version/type screens, `_safe_name`, `store_attachments` dedup + provenance sidecar, `land_in_run_dir` idempotence, `operator_block` authority label, and the extraction-exclusion decree at the `handle_queue` intake | 19 + 1 equivalent | 2026-08-16, all accounted for (12/20 on first pass, 7 gaps closed) |
| `defaults_census.json` | The DEFAULTS.md registry tripwire itself (`tests/test_defaults_doc.py`): forward census getter set + alias resolution + rglob + dotless leg + `config.py` exemption, reverse census table-cell parse + read-evidence shapes + per-file keying, and the living-frontmatter check | 15 | 2026-08-16, all accounted for (3/14 on first pass, seam + 16 fixtures added) |
| `deliverable_guard.json` | `step_exec`'s write-target extractor: the read-opener exemption, the verb lookbehind (path components and hyphenated dirs must not supply a verb), and the preposition-separation rule | 3 | 2026-08-16, all accounted for |
| `dev_status.json` | The project-state readout: CAPABILITIES row-mark parsing (the shape that must never re-admit prose), the backlog three-way census and stop-rule detection, trend degradation to unknown, staleness rendering, and the DEV_LOG managed-block splice | 11 | 2026-08-16, all accounted for (2 gaps closed — an evasion fixture and one badly-built mutation of my own) |
| `operator_attachments.json` | The `handle --attach` lane: local-file store (binary fidelity, refusal on missing/oversize, collision handling, basenaming), run-tree landing including the open_run wiring, the read-only mount seam that makes an attachment reachable at all, and the advisory block's mounted-path + operator-supplied labeling | 8 + 1 equivalent | 2026-08-16, all accounted for (the mount seam was EXTRACTED because the sweep proved the inline version untestable) |
| `retention_decree.json` | The 2026-07-10 retention decree's tripwire (`tests/test_no_silent_deletion.py`): the AST deletion scanner's API legs + function attribution + nested-package scan, the (module, function) allowlist key, the stale-entry census, and the `delete_checkpoint` no-auto-caller pin | 15 | 2026-08-16, all accounted for (4/13 on first pass, seam + 21 fixtures added) |
| `delta_gate_floors.json` | The Δ-gate's acting floors: `EFFECT_PROMOTE_MIN_DELTA/MIN_CALLS`, `EFFECT_DEMOTE_MAX_DELTA`, `EFFECT_INERT_MAX_ABS_DELTA/MAX_SPREAD`, and every guard in the four routes (`promote_lesson_by_effect`, `confirm_lesson_by_delta`, `demote_lesson_by_effect`, `inert_lesson_by_effect`) plus `resolve_remint_watch` — killswitches, finite-only, call floor, spread, stratum, replay-errors, boundary flags, text binding | 34 + 2 equivalent | 2026-08-16, all accounted for (33/35 on first pass — best of the arc) |
| `jsonl_utils.json` | The shared JSONL reader (9 callers): `_iter_lines_reverse` chunk stitching + order + boundary hold-back, the one `_classify` ladder (blank / undecodable / malformed / non-dict) and which bucket each lands in, `_read`'s missing-vs-unreadable split and tail/full-scan branch, `SkipReport.dropped/__bool__/summary`, and the WARNING that names the store | 32 | 2026-08-16, all accounted for (32/32 first pass — see the note below on what that does and does not mean) |

| `silent_drop_census.json` | The silent-drop tripwire itself (`tests/test_no_silent_drop.py`): the parse-call set and its nested-call walk, the control-flow-only silence test, scope walking (nested-def boundary, enclosing-loop requirement, loop propagation, `async for`/`while`), attribution to the owning function, rglob, the REVIEWED/UNREVIEWED allowance arithmetic, both censuses, and the SRC wiring | 32 | 2026-08-16, all accounted for (30/32 on first pass, both gaps were ambiguous anchors, not survivors) |

| `lesson_sweep.json` | The flat lesson corpus's two rewriting maintenance paths: `deduplicate_lessons`' preserve-in-place rewrite, unparseable count and warning, dry-run and no-op gates, and the exact/near merge semantics (task_type scoping, reinforcement accumulation, variant absorption, the 0.8 threshold); `compress_old_outcomes`' parsed-only delete set, batch membership, and `keep_recent` floor | 24 | 2026-08-16, all accounted for (21/24 on first pass, 3 real survivors closed) |

| `stamp_preserve.json` | The read half of the same module plus the census key: `_read_store`/`_rows_as` (loss announced, store named, corruption vs. schema drift kept apart, one bad row not aborting the load), the six in-place stampers' preserve-and-rejoin rewrite and its scan-traversal premise, the byte-safety layer (`_store_text` / `locked_rmw` / `atomic_write` surrogateescape round-trip), the census's qualified-name walk (`_child_scopes` boundaries, `outer.inner` composition, relative-path module keys), and the REVIEWED-coverage check | 23 | 2026-08-17, all accounted for (9/15 on first pass — a 16th mutation was added between passes, which is why the fraction and the total differ; +7 more after the adversarial round: rebuild mutants for the two stampers the first spec skipped, a verdict-scan vacuity mutant, three byte-safety reverts, the basename-fallback census key) |

| `knowledge_web_preserve.json` | The tiered-lesson and node-store byte-safety layer: the tier's family-A read revert, the quarantine's blind-read destruction path (one torn byte + one mutate = wiped tier — probed live before the fix), both launder guards (`loads_clean` in the quarantine round trip and the tier read), the stranded-rows carry on the mutate rebuild and the quarantine failure path's honesty about them, all four surrogateescape pairings (mutate write, sidecar append, bump write), the three family-B strict-read reverts (bump, promote scan, node/edge loaders), and the three remaining family-A loader reverts (archive, remint lineage, canon stats) | 23 | 2026-08-17, all accounted for (18/18 first pass — killing tests written against live probes of each failure before the spec; +5 across two adversarial rounds: dup x2 for the quarantine partial-failure consensus HIGH, sidecar-count strict revert, the for_rewrite rmw abort, dedup-degrade — one survivor along the way was honest and forced the both-writes-fail convergence test) |

| `evolver_store_preserve.json` | The suggestions ledger's byte-safety layer: the two family-A reverts (`load_suggestions`, `get_suggestion` — the row the V2 auto-revert authority guard re-reads), the dedup-scan blinding that resurrects the 81-duplicate bug, the three family-B strict-read reverts (`suggestion_is_applied`, apply's snapshot, revert's change_log read), and all FIVE keyed rewrites' launder + preserve-branch pairs (apply's `_merge`, revert's `_drop_constraint` and `_mark_reverted`, dismiss's `_merge`, and `stamp_verification`'s `_merge` — each with its unparseable-line-discard twin) | 16 | 2026-08-17, all accounted for (12/12 first pass; +4 after adversarial r1's 5/5-consensus HIGH found the dismiss/stamp merges shipped unconverted — sites the census structurally cannot see, so these mutants are their only mechanical tripwire; the `_mark_reverted` launder mutant was caught pre-sweep by dry-reading the spec against the tests: a torn line dies in both parsers, so the fixture gained a tainted-but-VALID bystander row before the sweep ran) |

| `interrupt_gc_doctor_preserve.json` | The three real finds from the 70-site destructive-rewrite triage: `doctor.cleanup_workspace_skills` (family-B crash, the destroyed-truncated-row revert, the stranded-carry revert, the write-back join, the surrogateescape round trip, the operator announcement, the deliberate-drop direction so a strand-and-carry cannot quietly no-op the verb, and the hardcoded-`~/.maro`-path revert); `interrupt.InterruptQueue` (the family-B revert that killed the whole control channel, both merges' launder reverts, both preserve-branch discard twins, the announcement, and the delivery direction — poll must still mark healthy rows applied); and `gc_memory._gc_outcomes` (the family-A revert that silently disabled retention forever, the conservative-keep revert, the launder revert that lets an untrusted timestamp authorize its own delete, the announcement, and the collection direction) plus, after adversarial r1, the lock-scope revert (the read back outside the lock — a lost update), the stale-by-id revert (a healthy row sharing a stale row's id destroyed), the raw-carry revert ("verbatim" that strips), the U+2028 framing revert, the strict-`applied` revert (the string "false" swallowing a STOP), the non-object-JSON reverts, the GC count-source revert, and the uncollectable-visibility revert, and after adversarial r2 the tri-state `applied` reverts (a legacy `"true"`/`1` row re-delivered and applied twice; an unreadable flag silently read as pending), the schema revert (a dict that is not a Skill winning dedup and deleting a healthy row), and the `freed`-sampled-outside-the-lock revert (a successful collection reporting NEGATIVE bytes freed), plus the two drift-gate mutants in `scripts/triage_manifest.py` — a fixed site turning destructive again, and a brand-new RISK site inheriting "already triaged", and after adversarial r3 the validator reverts (`dict_to_skill` treated as validation again, the hash check dropped, the finite-number check dropped — each lets a forged row win dedup and DELETE a healthy skill), both locked-transform announce reverts in interrupt (poll and clear), the GC delta-instead-of-absolute announce revert (it can print a negative row count), the GC no-op-rewrite revert (freed=-1 for collecting nothing), the `--check` **wiring** revert in `triage_manifest.main` (report the drift, exit 0 anyway), and the scanner framing revert (`split("\\n")` walking out of the detector's own field of view), and after adversarial r4 the dedup-identity reverts (dedup keyed on the row's own declared `content_hash` again, and its id fallback — either one lets a forged row nominate itself as a duplicate of a healthy skill and evict it), the validator's remaining field reverts (the str-field loop, the empty-`id`/`content_hash` check, the `created_at` timestamp check — each admits a row that a RANKING input then reads), and the `locked_rmw` no-write sentinel revert (a pass that removes nothing rewrites the file anyway), and after adversarial r5 the dedup-scope reverts (an operational field — `circuit_state`, which decides whether a skill matches at all — or an evidence field slipping back into the ignorable set, so rows that behave differently count as identical copies and one is deleted), the validator's raw-value revert (the checks reading the COERCED Skill again, so a stored `"7"` is proven to be an int), both `loads_clean` reverts (duplicate object names silently keeping the last value; a surrogate written as a `\uDCxx` escape admitted — plus the keys-only variant of the walk), both interrupt stranded-only reverts (a queue holding nothing but unreadable rows returning in silence, on poll and on clear), the preflight double-announce revert, and three scanner reverts (file iteration walking out of view, an unresolvable separator buying silence, and the OK verdict clearing a bare `json.loads` because the function mentions the guard somewhere), and after adversarial r6 the required-field revert (an absent `content_hash`/`created_at` treated as a default again, so a clone that omits the hash counts as non-stale and evicts the verified row), the text-tiebreak revert (`created_at` ranked as a string, so two legal ISO-8601 offsets sort opposite to real time and the older row wins), both `loads_clean` robustness reverts (the recursive taint walk, which raises RecursionError — not a JSONDecodeError, so no caller catches it — on JSON that `json.loads` itself parses; and the raw scan narrowed back to the surrogateescape quarter of the surrogate block), and three more scanner reverts (the separator resolved by last-assignment-wins rather than one-binding-proves, an unresolvable binding no longer meaning framing, and the unguarded-parse rule narrowed back to the literal spelling `json.loads` so an import alias walks past it), and after adversarial r7 the order reverts (the stranded rows appended after the admitted ones again, and every admitted row written at one position — either one promotes a legacy row over its verified twin, in a run that removes nothing and says so), both undecidable-group reverts (a naive timestamp ASSERTED to be UTC; the mixed-awareness group ranked anyway — a row deleted on an invented instant), both announcement reverts (the removed rows unnamed, and the kept row named only by the hash and name the whole group shares), both summary reverts (an unprovable row reported as corruption; the reasons never collected), three `loads_clean` robustness reverts (the parser's own `RecursionError` left uncaught, so a deeply nested row kills `poll()` instead of stranding; the refusal raised as something no caller catches; and the walk pushing every element instead of an iterator, so memory follows the widest container), and five scanner reverts (safety read off the identifier again, an aliased import binding the original name, and the annotated / augmented / walrus / loop / context-manager binding forms going invisible so a live JSONL rewrite vanishes from the scan entirely), and after adversarial r8 the two denylist reverts in `loads_clean` (only the exception classes we have MET are translated, so a 5000-digit integer kills `poll()` instead of stranding; and a refusal this layer raised on purpose re-wrapped as one the parser raised), the doctor's kind reverts (a validation refusal filed as byte corruption, every stranded row recorded as the same kind, and a readable row announced as unreadable), the four path reverts (the store missing from the rewrite header, the per-row strand line, the strand summary and the closing count — the retention decree's own sentence, one line at a time), the stale-row timestamp revert, three scanner scope reverts (a module-wide proof surviving a local rebinding, nothing counting as a shadow, and a function-local import of the wrapper misread AS a shadow — the must-detect other half, since half this codebase imports it that way), two provenance reverts (any module may supply a name spelled `loads_clean`; the wrapper called through its own module left unproven), and five census reverts (only plain assignment targets counted again, and the match-capture / mapping-rest / except-alias / local-import / parameter binders each going invisible) and after adversarial r9 the whitespace-launder reverts in all five readers (doctor parsing a stripped copy, doctor/gc/interrupt-poll/interrupt-clear treating a Unicode-whitespace row as framing and dropping it from the rewrite, gc reading the delete-authorizing timestamp out of the laundered copy, and skills stripping the row it carries — in `save_skill` and in `_save_skills`'s stranded list), the frame-blank revert in the shared helper, both non-standard-constant reverts (`NaN`/`Infinity` admitted again; the refusal announced but not raised), the two path reverts on the lines that announce a DESTRUCTION, and six scanner reverts (the census and the parser proofs walking into nested scopes again, a dotted proof outliving its receiver, both module-identity suffix reverts, this function's own parameters not counting as bindings, and the call-graph's one-function-per-name dict that made a destructive helper vanish), and after adversarial r10 the shared-reader reverts (`_classify` back on bare `json.loads`, and its framing back on `bytes.strip()` so a line no parser accepts is admitted as a record — the launder chain reassembled from the READ side, which is where four of five seats found it), the two skill-writer proof reverts (`validate_skill_row` back to `dict_to_skill` / to a bare `.get("id")`, either one letting a row nobody can prove decide its own removal), the skills ordinal revert (strandees appended after every live skill, which in a last-row-wins store is a promotion), the load-shadow revert (a broken row claiming its id before it fails to load, hiding the newest WORKING row for that id), the three skill-stats reverts (`splitlines()` breaking a row at its U+2028, `strip()` deleting a whitespace-only row as framing, and the stranded row stripped on the way through), the interrupt PREFLIGHT revert (the `l.strip()` filter r9 removed from the merge loops but not from their sibling, so a queue of nothing but an unreadable row reports empty in silence), the `read_all` revert (family A and B at once, next to the `rewrite()` that makes each silent drop a deletion), four scanner reverts (cycle detection keyed by bare name again — reversing two definitions makes a destructive reader vanish; and the proof / shadow / framing scans walking back into nested scopes), the manifest `blind` revert (a moved site's new home allowed to leave the scan too), and the escape-gate case revert (the uppercase `\uDCFF` spelling walking past the narrowed `\ud` gate), and after adversarial r11 the rewrite-composition reverts in memory_backends (the strandee re-read pass dropped so read_all's silence deletes again — the round's unanimous HIGH, all five seats; the carried rows going quiet; and the writer minting the NaN token), the admission reverts in skills (load_skills readmitting via the constructor — the tolerant-loader/strict-writer gap that minted a NORMALIZED CLONE which won last-row-wins; and the id claimed before the proof, the shadow-delete where a construct-ok/hash-fail row hid an older valid row from the writer that then deleted it), the writer-proof reverts (_prove_line no longer re-reading its own emission through the reader's door; the proof line re-admitting NaN; save_skill and the bulk writer each emitting an unproven line — json.dumps writes CPython NaN and clean \udcXX escapes by default, rows the strict reader then strands), the stats reverts (JSON 1 and true sharing one Python dict key again so a keyed rebuild silently deletes a row; both outcome recorders rebuilding the row from the model so every field it doesn't know — an operator's note — dies on the next routine bump; non-finite telemetry writing the NaN token), the interrupt deliverable reverts (poll marking an undeliverable row applied — the write that recorded an operator's STOP as delivered and lost it on retry; clear acknowledging one; peek handing out a row nobody can act on), the scanner deferral revert (a proof inside a generator expression certifying again — deferred code may never run; raw-in-genexp still poisons and eager comprehensions keep proof value), the decoder revert (json.JSONDecoder().decode invisible again), and the two manifest resurfaced reverts (the sixth leg silenced, and --check reporting the drift but exiting 0 — a MOVED site back under its outer name is the one direction blind cannot see, and the only watch on the twinless llm.py:_run_subprocess_safe), and after adversarial r12 the producer-door revert in interrupt (post() back on bare json.dumps — an operator STOP holding a lone surrogate acknowledged at the door and never deliverable by any consumer), the skills schema-leg revert (_prove_line back on bare _loads_clean, so a constructible tier=7 row ships again and strands on the next load) and the full-proof revert, the backend emission-door reverts (append and rewrite back on bare json.dumps — the writer outrunning its reader with clean-looking \udcXX escapes), the non-dict strand revert (the re-read stranding only parse failures again, so a `null` row rides out of the store), the three prove_record_line legs (the re-read dropped, the object requirement dropped, and the NaN leg — a marked twin-lock equivalent, loads_clean's parse_constant refusing the token allow_nan=False would not mint, same shape as r11's), the stats admission revert (the coercing constructor laundering drifted rows again — bool("false") is True), both compaction reverts (duplicate string ids collapsing uncounted, and the announce going quiet), both recorder-door reverts (each recorder minting non-string / non-encodable ids the reader strands as keyless), seven decoder-provenance reverts (the import alias forgetting its asname, the alias fixpoint and the bound-method binding and its call dispatch each going blind, raw_decode no longer counting as a parse, and AnnAssign / walrus no longer counting as bindings), and both manifest resurfaced-source reverts (the leg reading live instead of seen — an OK-verdict resurfacer invisible again — and the seen-fallback dropped), and after adversarial r13 the four presence-is-not-absence reverts (each modeled-field family back on d.get(), so a stored JSON null rides the absence exemption and bool(None) launders it), the three recorder-door reverts (a truthy "false" counting as success / as goal_achieved again; non-finite telemetry reaching the writer), the stats-writer predicate revert (the destructive writer back on generic clean-object proof, weaker than its own reader) and its surrogate second-lock fixture, the archive revert (the retention decree's own writer back on bare json.dumps), the rewrite audit reverts (strandees re-homed to the tail; the carry-through announced before it happens; append and rewrite converting I/O failure into apparent success), the transform no-lock revert (the undecidable lost-update race back, killed by a lock-held pin rather than a timing test), the four SQLite parity reverts (the silent drop, DELETE-all, and both emission proofs), and six scanner provenance reverts (the ctor-assignment fixpoint dropped, the method-alias chain and destructuring going blind, _parser_names back on plain Assign, dotted attribute chains unresolvable, and the class-level self.* map no longer crossing methods) | 290 | 2026-08-20, all accounted for (19/19 first pass; +10 from r1; +6 from r2; +9 from r3; +15 from r4 — 57 detected + the 2 standing equivalents). Round-by-round history: 19/19 first pass; +10 from r1; +6 from r2 — 33 detected + 2 marked equivalent surviving as claimed. The second equivalence is itself a finding: `doctor shape` was DETECTED in r1 and became UNFALSIFIABLE in r2, because the r2 fix added `dict_to_skill(row)` validation on the next line and every non-dict JSON value raises inside it — two guards, one detector, so it is marked with that reason and row-shape detection is carried by `doctor schema`. Four mutations also came back SKIP on the r2 sweep — stale anchors from my own rewrites — and were re-anchored before the sweep was called green; a SKIP is not a pass. 19/19 first pass; +10 from r1; +6 from r2; +9 from r3, **44/44 on the first pass** — 42 detected + the 2 standing equivalents. Two r3 entries are corrections to r2's own spec rather than new ground: `gc freed` was a `locked["freed"] = 0` stand-in that any assertion would catch, replaced with a FAITHFUL revert of the pre-fix shape (sample the UNLOCKED snapshot's size) — which then exposed that the r2 test appended before the old code's `stat()` and so passed on the defect it named; and the manifest mutants all lived inside `compare()`, so the gate's own `return 1` was never pinned. The r4 sweep needed two passes: four validator mutants SURVIVED the first one, because the r4 dedup fix means a junk row can no longer evict a healthy one whether or not the validator admits it — so every end-to-end test passed with the field checks deleted. That is a hole in the suite, not a dead guard: admission has its own consequence (an admitted row is re-serialized into the rewrite, a stranded one rides through byte for byte), and the fix was direct rejection tests on `validate_skill_row` plus a byte-for-byte strand assertion. One of them, `doctor validator (hash)`, only died to a LONE-SURROGATE field: every field `compute_skill_hash` touches is also in `_STR_FIELDS`, so a non-str is rejected either way — but a surrogate IS a str, passes every isinstance check, and dies on `.encode`. The mutant that looked equivalent was the one pointing at this arc's own subject. r5 needed a re-anchor pass first: eight EXISTING mutants came back SKIP because r5's own rewrites moved the lines they bound to. A SKIP is not a pass — the runner separates the two on purpose — and a spec that silently skips is the same failure as a scanner that reports zero. Re-anchored against the current files, then 69/69, then 76/76 after r6 (two more SKIPs from r6's own rewrites, re-anchored the same way), then 93/93 after r7 on the first sweep — eleven r6 anchors moved by the r7 fixes were re-anchored before the sweep, and the new `scanner parser identity :: a raw binding no longer poisons the clean set` is marked equivalent: the raw-wins ordering in `_parse_calls` already decides that case, so the `clean - raw` subtraction is a second lock on the same door. Recording that beats contorting a test to kill it. r8 is the first round in this spec where the sweep did real work rather than confirming the fixes: **six survivors on the first pass**, five of them holes in the new tests. Four were one test asserting the store path appeared on "at least three" lines — which passes with any ONE of the four announcements stripped; a count-based assertion cannot fail in the direction it was written for. The fifth showed that removing r7's spelling fallback had quietly disarmed the r6 fixtures: their guard mention no longer earned clean status, so they read RISK for the wrong reason and stopped exercising the branch they were written for. 110/110 after. r9 repeated the pattern — **five survivors**, all holes: both interrupt merge mutants lived because the test's "whitespace row" was a literal SPACE and every `json.dumps` row in the fixture already contains spaces, so the assertion could not fail (it uses an explicit U+00A0 now); the skills stranded mutant lived because the test exercised `save_skill` rather than `_save_skills`, the other writer; and the two parameter-binding mutants lived because they were redundant with each other, which is its own finding — the redundant loop is gone and one mutant is kept. 129/129 after | r10 repeated it a third time — **seven needing work on the first sweep**, five of them holes in the tests the round had just written: the U+2028 stats fixture never contained a U+2028 (`json.dumps` escapes it to six ASCII characters and `splitlines()` does not break on those); the ordinal test put the carried row FIRST, where appending to the tail lands it in the same place, so it could not fail; the shadow test's "broken" row used a field `dict_to_skill` assigns without complaint, so it loaded fine; nothing distinguished `validate_skill_row` from `dict_to_skill` because every fixture used a row BOTH reject (the distinguishing shape is one the constructor accepts and the proof does not, carrying a key `skill_to_dict` never writes); and the deep-nesting test went vacuous IN THIS ROUND, when tightening the taint walk's gate from `\u` to `\ud` moved it past the test's own payload — a fixture can be killed by a change to the code it guards, and only the sweep says so. The other two were re-anchors. 146/146 after. r11 re-anchored five moved mutants before its sweep (a SKIP is not a pass) and added 19; **163/165 on the first pass, both gaps teachers**: the clear-launder survivor was a hole r11's OWN fix created — `_prove_deliverable` strands a field-poor row on its own, so the old torn fixture stopped distinguishing the taint door from the proof door, and the killing fixture must be deliverable-shaped with one raw byte in `message` (the poll twin's already was, which is why it died on schedule; a guard added in front of another guard disarms the second guard's tests, and only the sweep says so) — and `the proof line re-admits NaN` is a marked twin-lock equivalent (`_loads_clean`'s `parse_constant` refuses the token `allow_nan=False` would have refused to mint — same abort, same direction, nothing observable). 165/165 accounted for after. r12 re-anchored eight moved mutants before its sweep and added 20 (1 marked equivalent at write time — the twin-lock NaN leg, recorded rather than contorted into a test). **185/185 accounted for on the first pass** (179 detected + all 6 marked equivalents surviving as claimed) — the arc's first zero-survivor, zero-SKIP first run: the re-anchors were done before the sweep, and every new mutant was written against a fixture that already existed. r13 re-anchored seven and added 24; one killing fixture was written PRE-sweep on a prediction (the r13 front validator twin-locks the NaN-writer mutant for modeled fields, so a surrogate-in-skill_name fixture pins the second lock separately). 208/209 on the first sweep; the survivor was `sqlite read :: the silent drop returns` — the composition test asserted "unreadable row", a substring BOTH the read and the rewrite emit, so the read-side revert hid behind the rewrite's carry-through (guard-in-front-of-a-guard, third appearance). The fixture pins the read's own "excluded from the result" line now; 209/209 after. r14 re-anchored seven and added 21 (the contract-travels reverts: SQLite transform's write-lock-after-read / delete-all / unproven emissions, read errors becoming an empty store again, the JSONL transform lock no longer required, transform retreating from the abstract contract; the stats reverts: identity leaving the validator, the map key unchecked, strandees riding last, a pure read claiming a rewrite, the carry announcement disappearing; the router coercing evidence again; the archive batch splitting; and six scanner reverts: class-body seed dropped, inheritance no longer carrying provenance, posonly receivers forgotten, ctor targets narrowed to bare names, dotted constructor calls unmatched, shadow re-proof narrowed to plain Assign, decoder-name seeds discarded, and the class walk running a single pass). 229/230 on the first sweep; the survivor taught about the FIX rather than the tests — the inner class-walk fixpoint was a redundant second loop (scan_module's outer class-graph fixpoint converges either way), so the redundant loop was removed and the mutant retargeted at the outer fixpoint, which the reversed-order chain fixture genuinely exercises. 230/230 after. r15 re-anchored three and added 14 (both recorder swallow reverts and both bare-lock reverts, the read-claims-a-rewrite and compacted-count-stops-travelling reverts, the router unadmitted-rows revert, the scanner alias and generic-base reverts, the archive page-cache revert plus both file_lock durable legs, and the sqlite committed-flag pair). **244/244 accounted for on the first sweep** (238 detected + the 6 standing marked equivalents surviving as claimed) — the arc's second zero-survivor, zero-SKIP first run: re-anchors done before the sweep, every new mutant written against a fixture that already existed. r16 re-anchored eight (its own restructures moved the recorder try-blocks and the scanner base loop) and added 14 (the unnamed-absence-deletes revert, the pool-lock and save_skill fail-open reverts, the pool-writer quiet-failure revert, both per-id attribution reverts in the ledger and the batch writer, the attribution-failure-goes-DEBUG revert, the torn-tail fuse revert, the locked_rmw fail-open revert, the commit-boundary claims-unchanged revert, the copy-protocol drops-compacted revert, both scanner scope/rebinding reverts, and the rollback stops-naming-drops revert). 257/258 on the first sweep; the survivor taught about the FIX again — the copy-protocol mutant zeroed __getnewargs__'s third element and could not fail, because pickle restores the instance __dict__ (which carries .compacted) after __new__: the method's EXISTENCE is the fix, and the mutant was retargeted at removing it. 258/258 after. r17 re-anchored eleven (its own rewrites moved the stats-compaction, sqlite-commit, pool-carry, ledger-call, file_lock-tail, scanner-scope and evolver anchors — three sites needed following-line context because the fixed pattern now exists more than once) and added 21 (the possession-is-ownership revert — updated_ids gone, a stale snapshot copy replacing a concurrently revised live row — plus the unnamed-id-resurrects tail revert, the contradictory-intent doors, the carry-verbatim revert, the drop-announcement revert, the tail-inspection fails-open revert, the batch bare-string and duplicate-double-count reverts, the marker trusted-by-presence / outside-the-lock / write-failure-lies reverts, the manifest str()-coercion reverts in both readers, the sqlite append/rewrite commit-boundary reverts, the evolver rollback name-match and no-archive-before-delete and bookkeeping-swallow reverts, and the scanner flattened-alias-map revert that minted false RISK from an unrelated scope). 278/279 on the first sweep; the survivor was `r17 pool :: contradictory intent slips through` — the overlap ValueError is behaviorally a SECOND LOCK (any overlapping id is either in the caller's list, tripping the still-present door, or not, tripping the absent-from-list door), so a raise-only test could not fail in the direction it was written for; the door's real contribution is its MESSAGE — the operator reads "contradictory intent", not a misleading sibling diagnosis — and the test now pins each door's message with pytest.raises(match=...). 279/279 after. r18 re-anchored four (its own rewrites moved the r16 unnamed-absence, r17 tail-resurrection, r17 drop-announcement and r17 marker-presence anchors) and added 11 (the resurrection-tail and ghost-announcement reverts, both divergence-warning reverts, the physical-row-count and backfill-unscope reverts, the verdict-matches-by-type and absorbed-correction reverts, both evolver two-reads reverts, and the append-boundary narrows-to-sqlite-errors revert). **290/290 accounted for on the first sweep** (284 detected + the 6 standing marked equivalents surviving as claimed) — the arc's third zero-survivor, zero-SKIP first run: re-anchors done before the sweep, every new mutant written against a fixture that already existed.
| `rules_background_preserve.json` | The rules.jsonl and background-tasks.jsonl keyed upserts (adversarial r2 sibling find on the evolver_store chunk): the family-A rules-read revert, both launder reverts (`save_rule._upsert`, `_append_task_log._merge` back on plain `json.loads` — the old shape also DELETED torn lines on every rewrite), both preserve-branch discard twins, the `_load_task` family-B revert (UnicodeDecodeError into every poll caller), and the drift-reads-as-absent revert (`return None` back to the stale-serving `continue` — adversarial r3's convergent MEDIUM: that deliberate semantic was untested) | 7 | 2026-08-17, all accounted for (6/6 first pass + 1 from r3; each mutant dry-read against its killing assertion before the sweep) |

| `loop_report_truth.json` | The run report + cross-run index's truth surface (tier-3 opener): freeze sentinel ×3 (detector, emission, write-site check), the byte-safety layer (too-broad strict-read revert, torn-slice-as-missing revert, both launder reverts to plain json.loads), every operator honesty note (slice loss both directions, torn-card panel, manifest loss, degraded index row: vanish / status / pointer / card-drop), verdict truth (achieved mapping, R2-5 omissions), R2-6 source-join, overcap loop scoping, unreadable-call-record row, timing honesty (mixed ended_ts, approx marker slotting, finalizing badge + refresh), backfill (running→interrupted, skip-unless-force), NOW result honesty, status preference at both layers, index ranking/containment/totals/count, date bounds, debounce both directions, audience-filter overreach, torn-card row marker + status fallback (adversarial r1) | 39 | 2026-08-17, all accounted for (27/37 first pass + 2 anchor SKIPs; all 8 survivors real, closed same day — cluster was exactly the tier-3 thesis: printed fields and index plumbing nothing asserted; +2 from adversarial r1: the Architect's torn-card index marker and the Expert QA de-vacuized status-fallback pin) |

| `step_flags_truth.json` | The step-flags curation lane (adversarial r1 named it the next tier-3 stop: `run_curation` strict-reads the same slice file loop_report just fixed): the strict whole-file read revert, torn-slice-as-missing revert, the lower-bound warning, the loss-only-still-writes-key contract ("absent = nothing fired" is only truthful when every line read clean) and its slice_loss card note, the STEP_TOO_BROAD filter, and the backfill `_merge`'s launder + destroy reverts (the old `except: card = {}` REPLACED a torn run_card with a step_flags-only stub — probed live: a 51-byte curated card became a 193-byte stub), and the classification-refresh preserve-then-rebuild layer from adversarial r2's HIGH (launder revert, sidecar skip, merge-becomes-replace), and the consumed-stamp launder revert from r3 | 12 | 2026-08-17/18, all accounted for (8/8 first pass — co-written with its fixes, the weak green; each fix probed live before code, three probes → four pins; +3 from r2: the Architect's sibling find one function up; +1 from r3: the consumed-stamp's tainted-valid launder) |

| `run_card_readers.json` | The two read-only strict run_card readers the step-flags r2 named (shadow_lane cost-comparison field, camera_readout frames walk): launder-direction reverts in both (tainted-valid twins pinned), the shadow WARN and camera count/print honesty notes, and the absent-by-age-stays-silent contract | 6 | 2026-08-18, all accounted for (6/6 first pass — co-written with its fixes, the weak green) |

| `skills_preserve.json` | The skill library and skill-stats stores: the stats destruction chain (blind keyed read -> next counter update rebuilds the store from one record), the stranded-row carry in all three of its parts (unparseable rows, keyless rows, the write-back join) and its announcement, `save_skill`'s family-B write-lock revert, its launder and preserve-drop pair, and the `load_skills` family-B revert, and (after adversarial r4) the `_save_skills` strand-and-carry pair plus both directions of the OSError contract — the writer's refuse-to-rebuild and the reader's degrade-to-empty | 13 | 2026-08-17, all accounted for (9/9 first pass; +4 from r4. One re-anchor along the way was the runner earning its keep: the r4 fix added a 16-space twin of an existing 12-space anchor line, the anchor became a substring of both, and the exactly-once rule reported SKIP — *"a 'pass' here would mean nothing"* — instead of a silent mis-mutation reading as a pass) |
| `tail_jobs.json` | The async post-run tail's durable job store (`src/tail_jobs.py`): recordability (no run-dir means the caller keeps the work, never a phantom run), seq assignment and the learning-before-maintenance drain order, done-marking in both directions (a completed job never repeats; a job that RAISED is recorded rather than left for a sweep to half-repeat), the one-tail-per-handle claim and all three of its liveness legs (host scope, release, EPERM-means-alive), the kinds filter the escalation lane drains through, the surface refresh's ran-something precondition, the handoff's fidelity (unknown fields filtered rather than dropping a step; the lossy fallback shape distinguished from `asdict`), adapter-identity replay and its two fallbacks, all three detachment flags on a real spawn plus the child's PYTHONPATH, the dispatch's three modes (off / spawn-failed / nothing pending), the OFF-by-default config read, the sweep's grace window and its live-claim filter, dry-run honesty, and the announced read that keeps one torn byte from taking the store | 28 | 2026-08-20, all accounted for (27/28 first pass; the one survivor was real — `find_stranded`'s live-claim filter could be deleted with a green suite, so a long tail that outlives the grace window would be reported stranded while its child was still working) |

Note on `stamp_preserve.json`: the highest-value sweep of the arc so far,
and the one that most clearly paid for itself. Six of fifteen came back
NEEDS WORK, and the four ordinary holes were the honest kind — the fix to
`_read_store` had been confirmed by **probe** and never by a test, so
deleting its warning outright survived. Probing tells you the code works
now; only a test tells you it still will.

The sixth is the one worth remembering. `vacuity :: the torn line stops
the scan` (`continue` -> `break`) SURVIVED against a fixture whose torn
line sat at the top of the store — because five of the six stampers scan
in **reverse** (newest row wins), so they found the target row before ever
reaching the torn line. Every preservation assertion was passing on a file
the scanner had never had to step over. This is the `lesson_sweep.json`
lesson again in a new costume — a test passing for a reason unrelated to
its docstring — but with a sharper edge: the vacuity guard I had written
specifically to prevent it (*"the stamp still lands past the torn line"*)
also passed, because it shared the fixture's blind spot. **A premise
assertion built from the same fixture inherits the fixture's defects.**
The store now brackets its target with torn lines on both sides, so
direction cannot matter.

The adversarial round on this chunk then found the same lesson at a
third depth: the torn fixtures were all **valid UTF-8** — truncated
JSON, the failure the stampers already handled — while the failure they
didn't handle (a torn BYTE, which kills the strict whole-file decode
before any per-line scan runs) never appeared in any fixture. Every
stamper raised UnicodeDecodeError on a store one crash-torn append could
produce, and the whole pinning class was green. The fixture family is
the test's vocabulary: **a failure shape missing from the fixtures is a
failure the suite cannot speak about**, no matter how adversarial the
assertions over the shapes it has. The store now carries three torn
shapes (leading ASCII, trailing ASCII, raw bytes), and the byte-safety
layer has its own revert mutants.

Note on `lesson_sweep.json`: this spec exists because the silent-drop
census pointed at `memory_ledger.py` and reading the file found
`deduplicate_lessons` **deleting rows it could not parse** — rebuilding
`lessons.jsonl` from the parsed rows alone, with `before` already
excluding the losses so neither the stats nor the log could show them.
Its three sibling rewrite paths had preserved unparseable rows for
months. The lesson for future sweeps is that **the outlier is invisible
from inside the module**: the repo's own §14a r4 note describes the flat
store as preserving these, which was true of three paths and false of the
fourth, and no amount of reading that note would have found it.

One survivor here also earned its keep the hard way. `gate :: the file is
rewritten even when nothing was removed` survived a round *after* I wrote
a test for it, because the fixture's "legacy" row omitted two required
fields and so never parsed — it was counted as unparseable, the rewrite
gate was never reached, and the mutation re-emitted it verbatim. **A test
can pass for a reason unrelated to its docstring, and the sweep is what
says so.** The fixture now asserts its own premises (`before == 1`, and
that the round trip differs at all) before asserting the behavior.

Note on `silent_drop_census.json`: the two "survivors" on the first pass
were neither survivors nor holes — the fixture proving each parser is
covered spelled its list as `("yaml", "safe_load"), ("yaml", "load"),`,
byte-identical to a line of `_PARSE_CALLS`, so the mutation that deletes
that line matched twice and was SKIPPED. **A fixture can disarm its own
mutation.** It reads as NEEDS WORK in the runner output exactly like a real
hole, which is why the skip message is loud and why the count line
distinguishes "accounted for" from "passed". Fixed by respelling the
fixture's list as dotted strings; a comment in the fixture says why, since
tuples are the obvious way to write it and someone will change it back.

Note on `jsonl_utils.json`: 32/32 on the first pass is the weakest kind of
green in this directory, and the row says so on purpose. The tests and the
mutation list were written in the same sitting against the same reading of
the file, so the sweep mostly proves the guard matches the list its author
wrote — not that the list is complete. Compare `defaults_census.json`
(3/14) or `retention_decree.json` (4/13), where the sweep audited guards
written months earlier by someone not thinking about mutations. **A sweep
co-written with its surface is a design tool; a sweep run against an older
surface is a measurement.** The one genuine find here came from the gap
between the two: `mutate.py` sweeps a copy of HEAD, so the first run
scored the *committed* reader, and a survivor there
(`if position > 0` hold-back removed) turned out to be rescued by a
trailing `if buffer: yield buffer` block that is unreachable in unmutated
code — dead since it was written, and now removed.

Note on `path_rewrite.json`: the first sweep of it ran green at 30/30 and
a six-lens review still found four real defects afterward, two of them
HIGH with independent consensus. **A green sweep bounds what the tests
pin, not what the code does** — every mutation in that first pass was
derived from behavior the author had already thought of, which is
exactly the blind spot a reviewer is for. The ten mutations added after
the review are the durable half of that round: each one now fails the
suite if its fix is ever undone.

And then the first LIVE run over the real corpus found a defect neither
had: the review's new left boundary blocked a genuine path start whenever
the path followed an escape inside a JSON transcript (`…notes.md\n\n/home/
clawd/…`), leaving 5,543 occurrences unrewritten across 24,553 imported
files. Every synthetic fixture in the sweep and in six review prompts
wrote paths preceded by a space, a quote, or start-of-line. **Sweep,
review, and one run against real data are three different instruments** —
a transform over a corpus is not verified until it has run over the
corpus.

`provenance_gate.json` was deliberately landed red at 6/18 and closed
the same day, and the interval is the point. Its four `unverified`
enforcement survivors split two and two under the probe: two were
redundant guards whose removal changes nothing observable, two were real.
Had they been reported as holes on the strength of the SURVIVED verdict,
half the follow-up work would have been writing tests for behavior
already enforced elsewhere — tests that pass for the wrong reason, which
is the failure this directory exists to find.

The run_decay_cycle survivor is the one worth remembering, because
reading the code would have gotten it wrong in the *other* direction. The
tier move IS refused downstream, so "redundant guard, mark equivalent"
looks right — but `promoted_ids` is built from the screen and feeds the
returned count and the change_log audit entry. Remove it and the cycle
reports promoting lessons it did not promote: a clean store with a lying
audit trail. **Probe behaviourally, not by reading; a guard can be
redundant for state and load-bearing for the record of that state.**

Everything else in `src/` (178 modules) has never had a file-derived
sweep. The ordered plan for covering it is the top item in the BACKLOG
Actionable Stack: tests that claim to enforce a decree first (silent by
construction), then data-integrity boundaries, then operator-facing
output, then churn.

Four tier-1 decree surfaces are now swept, and the spread is the point.
The e2b83703 scope decree was guarded by two tests that could not fail.
The dispatch envelope's extraction exclusion held under both mutations
aimed at it (leak operator prose into the goal; drop the operator
channel) — a surface that passes clean is the result, not a failed hunt.
The DEFAULTS.md census scored 3/14 and the retention tripwire 4/13. The
sweep is not a formality that always finds something, and it is not a
formality that never does.

**Three of the five were tripwires, and two of those could not fail;
the two production surfaces scored 12/20 and 33/35.** That is the
standing lesson of this arc: the code a decree protects tends to be
fine, and the guard claiming to protect it tends not to be. Enforcement
written as a test attracts less scrutiny than enforcement written as a
feature, because a passing test looks like evidence.

The Δ-gate floors are the control that makes the claim mean something.
Five numeric floors, five killswitches and every screen across four
parallel routes — all already pinned, on code that had five adversarial
review rounds. Review does work on features. It is enforcement-as-test
that slips through it.

## Sweeping a tripwire: mutate the guard, not the guarded

Two of the three tier-1 surfaces were production code; the defaults
census is a *test*, and that changes the question. You are not asking
"do the tests catch a change in the code" — you are asking **"can this
guard fail at all?"**

The census scored 3/14 because it had no seam: every helper reached for
`REPO_ROOT` itself, so there was no way to hand it a known violation. It
was untestable by construction, which is exactly why months of review
walked past it — nothing looks wrong, and the test does pass. The three
mutations that WERE caught fired by accident, having broken the census
hard enough against real repo data to raise a false positive. Accidental
detection is not coverage, and the sweep is what tells them apart.

The fix generalizes to any tripwire: give the checker its inputs as
parameters (defaulting to the live ones, so the deployed guard is
unchanged), then add must-detect fixtures that inject one violation each
and assert it is named. Pin the *exemptions* the same way — a quiet
census is only trustworthy if you can show what it stays quiet about.
**A detection shape with no fixture is a claim, not a guard**, so add
the fixture in the same commit as the shape.
