# Go port — adversarial review record

Branch-local record (an exploration branch carries its own review
history; if the port ever lands to main this moves to `docs/history/`
with `status: record`).

## Round 1 — 2026-08-22, on v0 (9b684131)

4 lenses (Skeptic, Architect, Minimalist, Expert QA), SAME-MODEL
FALLBACK: sonnet-medium (codex capped until 2026-08-27). All four
returned status 0 with substantive findings; zero hallucinated code
claims — every cited line was real (verify-before-fix pass re-read each
Python sibling).

### Verification Ledger (HIGHs)

1. **`budget.Clip` forged-marker bypass** (all 4 lenses) — VERIFIED:
   `context_budget.py:94-136` carries the 2026-08-14 fixpoint guards
   (bounded `\d{1,9}`, `m.start() <= cap`, tail ≤ 64); the Go port
   shipped the pre-fix shape (`MatchString` on an unbounded suffix
   regex), so any text merely ending in a marker-shaped string passed
   every cap unbounded. **FIXED**: all three guards ported; must-detect
   fixtures added (forged suffix, tighter re-clip, limit+64 bound).
2. **Workspace resolution read MARO_HOME** (Architect) — VERIFIED:
   Python `config.workspace_root` (config.py:33-39) reads
   MARO_WORKSPACE > OPENCLAW_WORKSPACE > WORKSPACE_ROOT and never
   MARO_HOME — the literal 2026-08-16 live-ledger incident variable.
   **FIXED**: chain matched name-for-name; MARO_USER_DIR now moves the
   user-config tier (Python `_maro_dir`); pinned by
   `TestWorkspaceIgnoresMaroHome` + compat-order tests.
3. **Unlocked JSONL appends** (Skeptic) — VERIFIED: both Python
   siblings (`memory_ledger.py:317`, `captains_log.py:618`) route
   through `file_lock.locked_append`; the Go writer was bare
   `O_APPEND`. **FIXED**: advisory flock on the same `<file>.lock`
   sibling paths (fail-closed, 30s deadline, torn-tail framing ported),
   so Go and Python writers interoperate on one workspace.
4. **Records lied about model and spend** (Expert QA) — VERIFIED:
   `Outcome.Model` got the backend name; `cost_usd` was hardcoded 0.0.
   **FIXED**: requested model threads through `loop.Run` (backend
   default recorded as `<backend>-default`); `cost_usd` omitted rather
   than fabricated (missing ≠ zero).
5. **`firstLine` bare-sliced error diagnostics** (Expert QA) —
   VERIFIED, and the sibling census found a second caller in
   `anthropic.go`. **FIXED**: both callers removed; whole reasons
   travel to the marked clip at the record boundary.
6. **Token usage dropped on the is_error branch** (Expert QA) —
   VERIFIED. **FIXED**: typed `llm.ResultError` carries usage;
   `executeStep` salvages it via `errors.As`. The fix's own test then
   caught the same class one layer up: `planner.Decompose` discarded
   planning-turn usage entirely — now returned (success and error
   paths) and folded into run totals.

### Mediums/Lows — all fixed

Captain's-log write failures ride `Result.Warnings` (were
stdout-only); `BuildPrompt` emits `[END SYSTEM INSTRUCTIONS]`
unconditionally (Python parity); subprocess capture is disk-backed
(was unbounded in-memory); result events require `subtype=="success"`;
LAST result event wins (Python parity); a result-shaped line that
fails strict unmarshal is named in the error, not silently skipped;
pretty-printed single-object fallback ported; `<think>` traces
stripped and fenced blocks preferred in `jsonx`; `loop_id` is
crypto-random (was a 0.1s wall-clock modulus — a colliding join key);
`config.Get` tolerates float→int (integral) as well as int→float;
`record.Event`'s audience comment no longer implies a parameter that
doesn't exist.

### Accepted residuals (named in PORT.md)

Subprocess liveness/stall kill (wall-clock timeout only); `cost_usd`
estimator; `config.Get` non-numeric mismatch warnings; no-fence
first-bracket carve (shared verbatim with Python `_find_json_bounds`).
REFUTED as non-issues: always-6-digit timestamp fractions (Python
parsers accept both shapes); unclipped step results on stdout
(deliberate — the terminal is the delivery surface and the full result
is the deliverable; commented at the call site).

## Round 2 — 2026-08-22, on the r1 fixes (88dacc88)

2 lenses (Skeptic, Expert QA), same sonnet-medium fallback, aimed at
the fix layer. (First attempt returned permission-blocked stubs — the
orchestrator launched reviewers from inside `go/`, and safe-mode reads
are cwd-scoped; re-run from `~/claude`. Reviewer-side failure it was
not.) Again zero hallucinated claims.

### Verification Ledger (HIGHs)

1. **`dry_run` never set** (Expert QA) — VERIFIED: only writers of the
   field were the struct declaration and serialization;
   `lesson_funnel_stats.py` keys on `dry_run is True` to exclude
   synthetic rows, so a `-backend dry` row in a real workspace was a
   fabricated production record. Third sibling of r1's model/cost
   findings on the same struct. **FIXED**: `loop.Opts{DryRun}` threads
   from the CLI's dry branch into both outcome writes; pinned.
2. **Split stdout/stderr capture broke true last-event-wins** (Expert
   QA) — VERIFIED: Python merges (`stderr=subprocess.STDOUT`,
   llm.py:1504) so "last" means chronological; the Go split meant "any
   stderr result beats any stdout result". **FIXED**: one merged
   capture file, exactly Python's shape; e2e fixture test proves a
   noisy stderr merges harmlessly.
3. **Only the per-entry half of the context discipline ported**
   (Skeptic) — VERIFIED: `ContextBudget DEFAULT_TOTAL_BUDGET = 24000`
   with oldest-first eviction is the audit's actual deliverable ("per-
   entry caps are unbounded in the dimension that actually grows");
   the Go loop re-sent every prior step with no total bound and
   `-max-steps` had no ceiling. **FIXED**: `budget.StepContextTotal`
   (24000, registered with rationale), oldest-evicted with a marked
   eviction note, `-max-steps` bounded [1,32]; pinned.

### Mediums/Lows — fixed

`subtype` must equal `"success"` (missing subtype now rejected, with
`error_during_execution` fixture); `FindClaudeBin` validates
isfile+X_OK so a stale CLAUDE_BIN can't make `auto` commit to a dead
backend; parse-failing result-shaped lines beside a success now ride
`Response.Warnings` → `Result.Warnings` instead of vanishing;
`buildAdapter("auto")` reports both constructors' real errors;
`NewID`'s rand-failure fallback keeps the 8-char join-key shape;
`Complete` now has end-to-end fixture-script coverage (success + model
flag threading, timeout branch, nonzero-exit branch). The backend-order
comment was VERIFIED CORRECT for this box (`~/.maro/config.yml` lists
subprocess first) but unsourced — now cites the config file and names
Python's shipped anthropic-first default.

## Round 3 — 2026-08-22, on the r2 fixes (ffa08096)

2 lenses (Skeptic, Expert QA), same sonnet-medium fallback, aimed at
the r2 fix layer. QA reported **no fresh HIGHs** ("the r2 fixes hold
up under direct re-reading and their own pinned tests; nothing was
papered over one layer up"). Skeptic reported 2. Zero hallucinated
claims for the third straight round.

### Verification Ledger (HIGHs)

1. **`renderPrior` forwards blocked-step diagnostics as prior
   evidence** (Skeptic) — VERIFIED: `res.Steps` accumulated every
   outcome and `renderPrior` rendered all of them, but the Python
   sibling (`director.py:589-590`) gates forwarded context on
   `result.status == "done" and result.result`. A failed subprocess
   turn's error string — which embeds the whole merged capture via
   `fileText` — would have been served to the next step's live LLM
   call as "results from earlier steps": both worker confusion and an
   untrusted-content funnel. **FIXED**: done-only + non-empty filter
   (original step indices kept for labels); fixture test pins a
   blocked entry sandwiched between done entries out of the next
   prompt.
2. **Warnings still die on a scrolled terminal** (Skeptic) — VERIFIED
   as an over-claim, RESOLVED as honest-claim rather than a durable
   sink: the warning's dominant cause is a failing workspace write,
   and a durable sink would live in the same failing workspace —
   "durably record that the store is broken, in the store" is not a
   guarantee we can make. Comment rewritten to claim exactly what the
   code does (caller-visible, stderr at the CLI); named residual added
   to PORT.md.

### Mediums/Lows — fixed

`ResultError` grew a `Warnings` field so parse-suspect diagnostics
survive the is_error/bad-subtype branches too, not just success
(QA — r2's fix was one-sided; fixture pins a suspect beside an
is_error result). `cmd/maro` gained `main_test.go` on the literal
entry point (both lenses): `-backend dry` → honest `dry_run:true` row
end-to-end, out-of-range `-max-steps` refused before any write,
flag-after-goal refused. Flock now has a contention fixture (8
goroutines × 25 appends → exactly 200 valid JSON lines, no torn/fused
rows) — "the lock file exists" was the only prior assertion (QA).
Budget composition invariant pinned (`StepResult + marker <
StepContextTotal`) so renderPrior's "newest entry always rides"
guarantee can't silently break under a future budget edit (both
lenses). `isExecutableFile` tested against a directory and a 0644
file (Skeptic). `planner.Decompose` now takes the caller's resolved
workspace instead of independently re-deriving it from env — one
value threaded through, matching the resolved-once-asserted-then-used
discipline `main.go` prints (QA).

## Round 4 — 2026-08-22, on the r3 fixes (b87da153) — FIXPOINT

1 lens (Expert QA), same sonnet-medium fallback, scoped to the r3 fix
layer. **No HIGHs.** One MEDIUM, two LOWs — the round's own verdict:
"everything else is fixpoint-consistent with rounds 1–3." Per the
house standard (converges to lows by round 3–4; QA at no-fresh-HIGHs
two rounds running), this is the fixpoint. All three findings fixed
anyway — each was cheap and the MEDIUM was real:

1. **MEDIUM — `planner.Decompose`'s salvage dropped
   `ResultError.Warnings`** — VERIFIED, and verify-before-fix widened
   it: the SUCCESS path dropped `resp.Warnings` too (the reviewer
   flagged only the error half). The r3 Warnings plumbing had an
   unfixed sibling exactly where r4's sibling-census probe pointed.
   **FIXED**: `planner.Usage` grew `Warnings`; populated on both
   paths; success-path warnings ride `Result.Warnings`, and on
   decompose failure — a path with no `Result` to carry them — they
   land in the stuck row's `failure_chain` (clipped), which is that
   row's diagnostic surface. Pinned in both planner and loop tests.
2. **LOW — filter/eviction composition untested** — FIXED: fixture
   mixes blocked entries into a list bulky enough to force eviction;
   asserts blocked entries absent, eviction fired, newest done entry
   rides under its ORIGINAL index label, oldest evicted.
3. **LOW — contention test asserted count, not content** — FIXED:
   the 200 rows must now carry 200 DISTINCT worker/iteration tags (a
   double-write compensating a drop no longer passes).

# Pack tranche — adversarial rounds (89fe71ee)

## Round 1 — 2026-08-22, on the pack tranche (89fe71ee)

4 lenses (Skeptic / Architect / Minimalist / Expert QA), sonnet-medium
SAME-MODEL FALLBACK (codex capped until 2026-08-27), defensive frame,
hostile-pack watch-list. 32 raw findings → deduped to 18. Verification:
every code claim was checked against both runtimes before fixing —
**zero hallucinated claims** (the streak holds). Ledger below covers the
four HIGHs; MEDIUMs/LOWs verified inline.

### Verification Ledger (HIGHs)

1. **Seal zero-fills a missing archive member (Skeptic)** — VERIFIED.
   `payloadSHA256`'s `raw := files[p]` took the nil zero-value for a
   manifest path with no member; `Seal` built `artifactBytes` only from
   members present, so a truncated archive sealed "clean" where
   Python's `files[path]` KeyErrors (pack.py:123) and aborts. Import
   already failed closed via its own presence loop. **FIXED**:
   `payloadSHA256` errors on an absent path, and `Seal` now checks the
   manifest/archive bijection explicitly before stamping
   (`TestSealRefusesMissingManifestMember`).
2. **pack_format gate fails open on type-confused values (Minimalist)**
   — VERIFIED. The `.(json.Number)` type assertion silently skipped the
   whole check for `"99"`, `99.5`, `[99]`, `true`; Python's
   `fmt > PACK_FORMAT` TypeErrors (crash = closed). **FIXED**: present
   but not a valid non-negative integer is a hard refusal
   (`TestImportRefusesTypeConfusedPackFormat`, with an absent-field
   negative control).
3. **Unbounded decompression (Architect; echoed by all four)** —
   VERIFIED. `readArchive` did `io.ReadAll` per member, no caps, before
   any hash gate. Shared with Python (tarfile equally unbounded).
   **FIXED in Go**: per-member (64MB) / total (256MB) / member-count
   (4096) bounds, refusal not OOM (`TestReadArchiveRefusesBombs`);
   Python's gap named in PORT.md as a backport candidate.
4. **Canonical-number "byte-parity" claim backwards (QA HIGH /
   Architect MEDIUM)** — VERIFIED as a wrong comment + unpinned edge,
   REFUTED as a trust bypass: Python re-normalizes numbers through
   json.loads/dumps while Go preserves literals verbatim, so a
   hand-crafted non-canonical literal ("5.00") diverges — into a digest
   MISMATCH, i.e. refusal, the safe direction. Every machine-written
   manifest is identical in both runtimes. **FIXED**: comment rewritten
   truthfully, behavior pinned (`TestCanonicalJSONNonCanonicalNumberLiteral`),
   residual documented in PORT.md.

### MEDIUMs/LOWs (all VERIFIED unless noted, all fixed or named)

- Adopt wrote no `imports.jsonl` audit row; report dropped
  label/adopted_at/dry_run → ported to parity (CLI test asserts both
  audit actions).
- `provenance_gate_enabled` killswitch not ported → ported (ambient
  config like Python, string-normalized; stamp path stays outside the
  gate; `TestProvenanceKillswitchRespected`, `TestGateEnabledNormalization`).
- Invalid UTF-8 / lone-surrogate escapes silently became U+FFFD where
  Python refuses → refused at the boundary (`utf8.Valid` on every
  member; `refuseLoneSurrogates` scanner on pack.json;
  `TestReadArchiveRefusesInvalidUTF8`, `TestImportRefusesLoneSurrogateManifest`
  with surrogate-pair + escaped-backslash negative controls).
- Duplicate manifest paths double-imported one artifact through two
  trust lanes; stowaway members rode outside digest and REVIEW.md
  (shared with Python) → bijection enforced at seal AND import
  (`TestImportRefusesDuplicateManifestPath`,
  `TestSealAndImportRefuseUnlistedMember`); Python's gap named.
- Id-less/mistyped-id rows collapsed onto one shared identity, eaten as
  "already_imported" (shared with Python) → `malformed_skipped` per
  row (`TestImportSkipsIdlessRowsAsMalformed`); divergence named.
- `AbsorbVariant` diverged from `_absorb_variant` three ways (no
  canonical strip, ASCII-only trim missing \r, no pre-clip check) →
  re-ported line-for-line (`TestAbsorbVariantTrimsCanonicalAndUnicode`).
- CLI path had zero test coverage → `TestRunPackLifecycleThroughCLI`
  drives export→seal→import→adopt through `runPack` with env-resolved
  workspace.
- crossrt_smoke.sh was happy-path only → step 6 added: tamper a
  Go-sealed pack, Python refuses; tamper a Python-sealed pack, Go
  refuses. Live run: PASS both directions.
- Doc-only: lock-ordering invariant (gate outermost) + Python
  fail-open caveat on `record.Locked`; RE2 `\b` ASCII residual in
  scrub.go; asString/CLI-drift residuals in PORT.md.

Also carried from the round's own confirmations (Minimalist closing,
Skeptic #8): traversal guards exact, tamper chain wired end-to-end with
real negative controls, provenance regexes a verified regex-for-regex
port, O_EXCL at the adopt write site, fixpoint ports (variant-union,
border coercion, laundering gate) genuine.

## Round 2 — 2026-08-22, on the r1 fix layer (dcc94494)

2 lenses (Skeptic + Expert QA), sonnet-medium fallback, scoped to the
fixes. Both lenses independently landed the same HIGH — in the r1 fix
itself, the historically likeliest home:

### Verification Ledger

1. **HIGH (both lenses) — decompression bounds had a total bypass for
   non-regular tar entries** — VERIFIED: the `Typeflag != TypeReg`
   `continue` ran BEFORE the member-count and byte caps, so millions of
   tiny repetitive dir/symlink headers decompress-looped unbounded, and
   `TestReadArchiveRefusesBombs` (built on writeArchive, which only
   emits TypeReg) passed on the gap. **FIXED**: every header counts
   against the entry cap, non-regular entries are refused outright (a
   legitimate pack only holds regular files), duplicate member names
   refused too (r2 Skeptic LOW: last-wins could show a reviewer's tar
   tool different bytes than the digest blesses).
   `TestReadArchiveRefusesNonRegularAndDuplicateEntries` hand-rolls raw
   tars (dir, symlink, header bomb, dup name).
2. **HIGH (Skeptic) — lone-surrogate refusal guarded only pack.json;
   row content went through plain json.Unmarshal** — VERIFIED: the
   rules/hypotheses/lessons lanes would silently U+FFFD-mangle the
   exact text the provenance classifier reads. **FIXED**: `scanRows`
   runs `refuseLoneSurrogates` per line before decoding; a refused row
   is `malformed_skipped` (per-row fault isolation), pinned by
   `TestImportRefusesLoneSurrogateRowContent`.
3. **MEDIUM (both) — rowID refused ids Python imports** — VERIFIED
   against pack.py:538 (f-string coerces any present id, so
   `{"rule_id": 42}` imports in Python) — r1's refuse-all-non-strings
   silently dropped rows a Python import keeps, and PORT.md's residual
   overclaimed the Python collapse. **FIXED**: scalar ids coerce via
   asString (str() parity); absent/null/empty/composite refuse; PORT.md
   corrected; the malformed report row now carries the raw offending
   value (QA LOW — audit parity with Python's original_id field).
4. **LOW (QA) — Import accepted pack.json/REVIEW.md as manifest
   artifacts where Seal refused them** — VERIFIED (artifactBytes was
   built from the raw member map; the stowaway loop excluded the
   reserved names). **FIXED**: reserved names refused in Import's
   artifact loop; `TestImportRefusesReservedMemberAsArtifact`.
5. **LOW (QA) — GateEnabled missed int64/uint64** — FIXED, cases added.
6. **LOW (Skeptic) — explicit-null config parity was asserted, not
   pinned** — and the probe REFUTED the lens's mechanism claim: Go's
   `Get[any]` on explicit null returns the DEFAULT (nil interface fails
   the assertion), so the gate stays ON where Python's `bool(None)`
   turns it OFF — a real divergence, safe direction, now pinned
   (`TestGateEnabledExplicitNullConfig`) and named in PORT.md.
   Skeptic #5 explicitly re-verified the surrogate scanner's escape
   state machine as correct — the r2 gap was call-site coverage, not
   the scanner.

Full suite green (12 packages), crossrt_smoke.sh (incl. bidirectional
tamper step): PASS.

## Round 3 — 2026-08-22, on the r2 fix layer (aef23174)

2 lenses (Skeptic + Expert QA), sonnet-medium fallback. The pattern held
a third time: the round's HIGH lived in the previous round's fix.

### Verification Ledger

1. **HIGH (Skeptic; QA filed the same vector as LOW/worth-verifying) —
   the r2 entry cap cannot see PAX/GNU meta records** — VERIFIED
   against the installed go1.24.2 stdlib source: archive/tar caps each
   special record at 1MiB (`readSpecialFile`, CVE-2022-2879 fix) but
   `TypeXHeader`/`TypeGNULongName` hit `continue` INSIDE `next()`'s
   internal loop, so an unbounded run of consecutive meta records is
   consumed without `Next()` ever returning — invisible to `entries++`.
   (Global PAX headers ARE returned and the typeflag refusal catches
   them; the per-file variants were the hole.) **FIXED** with both
   lenses' shared fix shape: a `cappedReader` between gzip and tar
   bounds every byte tar touches, categorically, regardless of stdlib
   internals (`maxArchiveTotalBytes + maxArchiveHeaderBytes`).
   `TestReadArchiveRefusesPaxHeaderBomb` hand-rolls raw consecutive 'x'
   records (checksummed 512-byte blocks) and pins the refusal.
2. **MEDIUM (both lenses) — scanRows decoded without UseNumber**, so
   rowID's json.Number branch was dead code and numeric ids round-
   tripped through float64: `42.0` → "42" (Python: "42.0"), >2^53 ids
   rounded — divergent identities cross-runtime and a craftable
   same-float64 id collision (second row silently eaten as
   already_imported). The same package's canonical.go documents this
   exact hazard. **FIXED**: scanRows decodes with UseNumber (like
   decodeManifest); `TestImportKeepsLargeIntegerIDExact` pins 2^53+1
   and a same-float64 neighbor as exact and distinct.
3. **MEDIUM (Skeptic) — the r2 onMalformed callback emitted every
   malformed row before any successful one**, breaking report order vs
   Python's single loop; the r2 test's fixture put the malformed row
   first, masking it. **FIXED**: scanRows returns tagged rows in file
   order, single pass; `TestImportReportPreservesFileOrder` puts the
   malformed row in the middle.
4. **LOW (QA) — a composite id's malformed report row carried a JSON
   array under a field every other outcome emits as string** — FIXED:
   rowID's report value is always a string.
5. **LOW (both) — archive.go's "REFUSES (never OOMs)" and the r2 ledger
   overclaimed** — the comment and PORT.md now attribute the guarantee
   to the decompressor-level ceiling, not to per-shape refusals.
6. QA's "verified as sound" list: r2's entry-cap reordering, dup-name
   refusal, per-lane scanRows uniformity, reserved-member refusal,
   GateEnabled int64/uint64, and the explicit-null pin all confirmed
   correct (the null divergence independently re-derived from the Go
   spec). QA note #5 (null/composite over-refusal framed as parity) —
   accepted: PORT.md now calls it a deliberate over-refusal.

Full suite green, crossrt_smoke.sh (incl. tamper) PASS.

## Round 4 — 2026-08-22, on the r3 fix layer (2ea5b2d2)

1 lens (Expert QA), sonnet-medium fallback — the fixpoint-check round.
The pattern held a FOURTH time: the round's one HIGH sat in r3's fix.

### Verification Ledger

1. **HIGH — the r3 switch to json.Decoder (for UseNumber) dropped
   Unmarshal's full-consumption check** — VERIFIED (documented
   Decoder.Decode semantics): a `{...}{...}` JSONL line imported its
   first object silently in Go where Python's json.loads raises Extra
   data and skips the line; decodeManifest carried the same defect from
   birth on pack.json itself (unfixed sibling, correctly flagged).
   **FIXED**: `decodeStrictJSONObject` — one value, UseNumber, then
   dec.Token() must return io.EOF; scanRows skips such rows wholesale
   (Python parity) and decodeManifest hard-refuses.
   `TestImportRefusesTrailingDataRows` pins both, with a
   trailing-whitespace negative control.
2. **MEDIUM — UnionVariantsIntoLesson's read-modify-write decoded
   without UseNumber** — VERIFIED: the rewrite re-marshals WHOLE store
   rows, so any >2^53 numeric field on a row the union merely passed
   through would round. **FIXED** (UseNumber decoder);
   `TestUnionVariantsPreservesLargeIntegers` pins 2^53+1 surviving a
   rewrite verbatim.
3. QA confirmed sound, explicitly: the cappedReader ceiling (every byte
   tar touches funnels through it; short-read/EOF passthrough correct;
   only theoretical edge is an exactly-at-ceiling over-refusal — safe
   direction), rowID's string report contract, and the file-order fix
   (non-vacuous middle-position fixture).

**FIXPOINT CALL (SAME-MODEL FALLBACK: sonnet-medium):** four rounds
(4→2→2→1 lenses), every round's HIGH found in the previous round's fix
and closed the same day with a must-detect pin; r4 surfaced no defect
outside that one fix-regression class, and its fix (strict decode) is a
mechanical tightening, not new surface. Residual risk is the named-
divergence list in PORT.md, all refusal-direction. Full suite green,
crossrt_smoke.sh (bidirectional tamper included) PASS.

# Executor tranche — tool-bearing worker steps (11f0808f)

## Round 1 — 2026-08-22, on the executor tranche

4 lenses (Skeptic, Architect, Minimalist, Expert QA), SAME-MODEL
FALLBACK: sonnet-medium (codex capped until 08-27). Every claim below
was verified against the Python/Go sources before any fix — zero
hallucinated claims this round (the streak holds).

### Verification Ledger

1. **HIGH (Skeptic) — exec mode continued past a blocked step with live
   tools** — VERIFIED (loop.go ran the whole queue regardless of
   status; Python loop_execute.py:1985 breaks on a terminal stuck
   verdict via loop_blocked's ladder, which Go lacks entirely — so the
   port's run-through had NO ladder softening it: a live Bash-bearing
   worker kept acting on a failed premise). **FIXED**: exec mode halts
   on the first non-done step, failchain names the unexecuted remainder
   (contents, not counts); tool-less lane keeps v0 run-through as a
   pinned deliberate asymmetry.
   Pins: `TestExecLaneHaltsOnBlockedStepBeforeLaterSteps`,
   `TestToollessLaneKeepsRunThroughOnBlocked`.
2. **HIGH (all four lenses) — naive goalSlug collides live project
   dirs** — VERIFIED (goalSlug = first 5 words; Python production runs
   resolve_project_slug, loop_init.py:400, precisely because "tell me
   about the…" openers collide; two unrelated runs would share a dir
   and cwd-bound workers would act on each other's files). **FIXED**:
   full resolve_project_slug port in loop/project.go (_GENERIC_WORDS
   verbatim, generic-slug + different-mission → -2…-20 then goal-hash;
   same-mission continuity; specific slugs reuse). Named divergence:
   Python reads the mission from NEXT.md; Go records a `.mission` file
   O_EXCL at creation (first-writer-wins).
   Pins: `TestResolveProjectSlugDisambiguation`,
   `TestExecLaneCollidingGoalsGetDistinctProjectDirs`.
3. **HIGH (Architect) — project-dir mkdir failure lost the run record**
   — VERIFIED (the error return skipped the outcome write; a run that
   leaves no record did not happen, and the planning spend vanished).
   **FIXED**: setup failure writes a stuck outcome carrying the
   planning tokens before returning the error (mirrors the decompose-
   failure branch). Pin:
   `TestExecLaneProjectDirFailureStillRecordsOutcome`.
4. **HIGH (QA) — transcript-file creation failure would have failed the
   step**, fabricating a "blocked" record blaming the model for an OS
   error — VERIFIED (os.Create error propagated as the step error).
   **FIXED**: soft degrade to the deleted-temp capture + warning riding
   Response.Warnings AND ResultError.Warnings; artifacts-dir mkdir
   failure degrades the same way at the loop layer.
5. **MEDIUM (Skeptic) — `--disallowedTools WebFetch,WebSearch` framed
   as an egress control** — VERIFIED over-claim (Bash open; Python's
   real boundary is the container lane, unported). **FIXED**: honest
   comments + PORT.md qualification; no code change pretends otherwise.
6. **MEDIUM (two lenses) — duplicated anonymous capability-interface
   literals** (loop gate vs CLI wrapper) — VERIFIED drift hazard.
   **FIXED**: one named `llm.AgentToolsCapable`; anthropic.go documents
   why it deliberately does NOT implement it.
7. **MEDIUM (Architect) — exec decision derived from a backend-name
   string in the CLI** — VERIFIED (could drift from the adapter's
   actual capability under wrapping). **FIXED**: decided once from the
   constructed adapter via the named interface; three-way "exec mode:"
   print makes the entered mode visible on stdout alone.
8. **MEDIUM (QA) — budget-cap failchain reported a remainder COUNT,
   not contents** — VERIFIED vs Python's named remainder. **FIXED**:
   nameRemainder() joins the queued step texts (clipped); pinned in
   the budget-cap and halt tests.
9. **MEDIUM (QA) — injected steps invisible in delivered output** —
   VERIFIED (records only). **FIXED**: WasInjected travels to the CLI
   step printout as ` (worker-injected)`; pin
   `TestExecLaneInjectedStepsAreTagged`.
10. **MEDIUM (Skeptic) — injected step text unclipped into the queue**
    — VERIFIED (a hostile/runaway worker could stuff pages into a
    "step"). **FIXED**: registered `injected-step` budget (500, marked
    clip). Interleaved-blank + cap order pinned
    (`TestExecLaneInjectBlanksInterleaved`; filter-then-cap comment
    corrected — my own test comment claimed a Python divergence that
    three lenses REFUTED against loop_post_step.py:1010).
11. **MEDIUM (QA) — no test exercised step Timeout wiring or the sad
    paths** — VERIFIED. **FIXED**: `TestExecLaneStepTimeoutsReachTheAdapter`
    (long-running 1800s + default 600s through real Opts), plus the
    sad-path pins above and
    `TestExecLaneWrongTypeInjectStepsWarnsLoudly`.
12. REFUTED (Minimalist): claim that exec.go's WORKSPACE block
    duplicated the planner's — the planner block is goal-decompose
    framing, the exec block is Python EXECUTE_SYSTEM's workspace
    paragraph; different consumers, kept.
13. OUT-OF-SCOPE (Architect): per-step model routing/trajectory
    escalation absent — real, named in PORT.md's unported list; its
    tranche brings it.

Verdict on r1: CONTESTED → all four HIGHs fixed same-day with
must-detect pins (SAME-MODEL FALLBACK: sonnet-medium). Full suite
green (12 packages), live smoke re-run PASS (real claude CLI created
and verified greeting.txt via its own tools; .mission recorded;
per-step transcripts kept).

## Round 2 — 2026-08-22, on the r1 fix layer (4348a139)

2 lenses (Skeptic, Expert QA), SAME-MODEL FALLBACK: sonnet-medium. The
flagship pattern held AGAIN: both HIGHs sat inside r1's fixes. Zero
hallucinated claims — every citation verified against the sources.

### Verification Ledger

1. **HIGH (Skeptic) — the slug fix closed only the SEQUENTIAL collision;
   the concurrent check-then-act race remained, and Python's actual
   concurrency guard (`acquire_project_slot`, interrupt.py:1008 — flock
   held for process lifetime, LoopBusy refusal) was never ported nor
   named** — VERIFIED (Stat→MkdirAll→O_EXCL is non-atomic as a unit;
   MkdirAll is idempotent-success; recordProjectMission returned nil on
   IsExist unconditionally, so a raced second run silently adopted the
   first's dir). **FIXED**: minimal admission-gate port (loop/slot.go)
   — per-project flock at memory/loop-<slug>.lock claimed before the
   first project write, refuse-immediately with holder metadata, fs
   errors degrade UNGATED with a warning (Python parity); busy refusal
   records a stuck outcome. Unported pieces named in the file. Pins:
   `TestAcquireProjectSlotRefusesSecondHolder`,
   `TestExecLaneBusyProjectRefusesAndRecordsStuck`; live-fired with two
   concurrent real-CLI runs (one done, one refused naming the holder).
2. **HIGH (QA) — transcriptWarn was computed then DROPPED on the
   timeout / crash / no-result exit paths** (plain fmt.Errorf, no
   Warnings field; only the res!=nil branch carried suspects) —
   VERIFIED. **FIXED**: those three paths append the accumulated
   warnings into the error message itself. Pin added in llm tests
   (unwritable TranscriptPath + no result event → error text names the
   degraded transcript).
3. **MEDIUM (QA) — failure-chain entries were Clip(prefix)+
   Clip(remainder) concatenated AFTER clipping**, up to ~2× the
   documented per-entry budget — VERIFIED against budget.go's "bounds
   ONE entry" doctrine. **FIXED**: nameRemainder returns the raw join;
   each call site clips the assembled entry once. Pin:
   `TestFailureChainEntriesRespectBudget` (long-step fixture, asserts
   every entry ≤ limit + marker allowance).
4. **MEDIUM (Skeptic + QA, independently) — `.mission` blindness /
   corruption defeats disambiguation**: (a) a Python-created project
   has NEXT.md, no .mission → recordedMission "" → sameSubject true →
   silent cross-runtime merge; (b) O_EXCL-then-write leaves an
   existing-but-EMPTY .mission on crash, permanently reading as "no
   evidence" — BOTH VERIFIED. **FIXED**: recordedMission falls back to
   parsing NEXT.md's "> goal" line (mirroring Python
   _recorded_mission); the write is now temp-then-link(2), so
   first-writer-wins holds atomically with content. Pins:
   `TestRecordedMissionFallsBackToPythonNextMD`, temp-cleanup assert in
   the disambiguation test.
5. **LOW (Skeptic) — evidence-free generic reuse was the one silent
   degrade path** — VERIFIED inconsistency. **FIXED**: sameSubject
   reports the evidence gap; resolveProjectSlug returns a reuse warning
   ("no recorded mission" / "no distinguishing subject words") that
   rides Result.Warnings. Pin: `TestGenericReuseWithoutEvidenceWarns`.
6. **LOW (QA) — hash-fallback slug returned without the existence/
   mission check every other branch performs** — VERIFIED, but Python
   parity (loop_artifacts.py returns it unconditionally too): comment
   added naming the parity, no behavior change.
7. **LOW (QA) — mission-write failure orphaned an empty project dir**
   — VERIFIED. **FIXED**: a dir THIS run created and never wrote into
   is os.Remove'd on setup failure (Remove refuses non-empty dirs, so
   pre-existing work is structurally safe under the data-retention
   doctrine). No pin (fs-failure injection is disproportionate for an
   empty-only Remove); noted honestly.

Verdict on r2: CONTESTED → both HIGHs fixed same-day with must-detect
pins (SAME-MODEL FALLBACK: sonnet-medium). Full suite green, concurrent
live smoke PASS.

## Round 3 — 2026-08-22, on the r2 fix layer (a4b2a6b5)

1 lens (Expert QA), SAME-MODEL FALLBACK: sonnet-medium — the
fixpoint-check round. The pattern held a SIXTH time: the round's HIGH
sat inside r2's own fix, and undermined r2's headline fix.

### Verification Ledger

1. **HIGH — the r2 empty-dir cleanup raced the r2 flock**: a
   busy-refused loser that raced the winner past MkdirAll (both Stat'd
   before either created; MkdirAll is idempotent-success) hit
   `!dirExisted → os.Remove` on the still-empty dir the winner had just
   won — deleting it before the winner's .mission write, failing BOTH
   runs with a stuck record blaming "dir setup". VERIFIED (the exact
   window exists; Python's loop_init never deletes on LoopBusy — the
   cleanup was a Go-only addition with no parity model). **FIXED**:
   cleanup now requires `holdingSlot` — a busy-refused run never
   removes anything; removal only happens while holding the flock,
   when no sibling can be inside. Pins: dir-survival assert in
   `TestExecLaneBusyProjectRefusesAndRecordsStuck` (the reviewer named
   this exact test as the one that should catch it and didn't), plus
   `TestConcurrentRunsSameSlugWinnerSurvivesLoser` — a gated-adapter
   integration test holding a real Run mid-step while a second Run is
   refused, asserting the winner's dir/mission intact and the winner
   completing done; race-detector clean.
2. **Finding 2 (test gap)** — no automated test exercised two
   concurrent Runs; the "live-fired" PORT.md claim was manual-only.
   ACCEPTED and closed by the gated concurrent test above; PORT.md now
   says live-fired AND pinned.
3. **LOW — stale holder metadata in refusal diagnostics** (write-after-
   flock window; release never truncates) — VERIFIED as faithful
   Python parity (interrupt.py does the same). Flagged not fixed;
   comment added in slot.go naming the inherited imprecision.
4. **LOW — no-result error path printed the scan suspects twice**
   (parseErr already embeds them; the r2 warnSuffix re-appended the
   same slice) — VERIFIED. **FIXED**: the no-result path appends only
   the transcript warning; timeout/exit paths keep the full suffix.
5. **No-issue (confirmed parity)**: recordedMission's
   `TrimLeft(line, "> ")` over-strip matches Python's
   `line.lstrip("> ")` byte-for-byte — correctly not "fixed".

**FIXPOINT CALL (SAME-MODEL FALLBACK: sonnet-medium):** three rounds
(4→2→1 lenses), every round's HIGH found in the previous round's fix
and closed same-day with a must-detect pin; r3's remaining findings
were one interaction defect in r2's own addition (fixed + double-
pinned), one inherited-parity LOW (flagged), one message-shape LOW
(fixed). No defect outside the fix-regression class for two rounds.
Residuals: the named-divergence lists in PORT.md/slot.go (all
refusal-direction or diagnostics-only). Full suite green across 12
packages, race detector clean on the concurrency pins, live smoke
(single + concurrent) PASS.

---

## Blocked-step LADDER tranche — adversarial round 1 (2026-08-22, SAME-MODEL FALLBACK: sonnet-medium)

Commit under review: 033a556f (blocked.go + loop.go/exec.go wiring).
Four lenses (Skeptic, Architect, Minimalist, Expert QA), all returned
exit 0. 16 distinct findings after dedup; **five HIGH-class claims, all
VERIFIED against both sources — zero hallucinated findings again**
(arc streak intact across 5 rounds). The flagship pattern held a 7th
time in spirit: the densest defects sat exactly where this tranche
wired NEW machinery into last round's fixed lane (the append-then-
decide ordering, the budget-clip interplay with the caps doctrine).

### Verification Ledger

1. **HIGH — sibling-rate self-contamination** (Skeptic #1, QA #3,
   Architect #3): Go appended the current attempt to `res.Steps`
   BEFORE `handleBlockedStep` read it as the sibling evidence; Python
   decides first (loop_execute.py:1929 appends after; the recovery
   branches append inside _process_blocked_step at :275/:332/:408 and
   `continue` past the outer append). Self-counting pushed
   `sibling_rate` over 0.5 with `len>=3` on small plans, firing
   premature redecompose and burning replan budget before the retry
   threshold was ever reached. VERIFIED (both orderings quoted).
   Ledger nuance the Skeptic got slightly wrong: prior retries of the
   SAME step do land in Python's step_outcomes — only the current
   attempt is excluded, so the fix passes `res.Steps[:len-1]`, not a
   self-filtered slice. **FIXED** + pin
   `TestExecLaneSiblingRateExcludesCurrentAttempt` (Run-level — the
   reviewers noted the existing unit test hand-built a correct slice
   and could never see the wiring bug).
2. **HIGH — MISSING_INPUT inner clip used the wrong budget**
   (Architect #1, QA #4): blocked.go clipped the embedded reason with
   FailureChainEntry (600) where Python uses clip(block_reason, 1000)
   (loop_blocked.py:1085) — the inner cut consumed the entire outer
   chain-entry budget, so the trailing do-not-fabricate instruction
   ALWAYS fell off the assembled entry for long reasons. VERIFIED,
   with one correction to the reviewers' framing: nested clipping
   itself is Python parity (`clip(_stuck_reason, 600)` at Python's own
   chain append) — the defect is only the wrong inner bound.
   **FIXED** (BlockReason.Clip) + pin
   `TestHandleBlockedStepMissingInputKeepsWideReason` (647-char reason
   with a tail sentinel).
3. **HIGH — the INITIAL plan was never shaped** (Minimalist #1):
   Python shapes every fresh plan (_prepare_execution →
   _shape_steps(label="initial-plan"), loop_planning.py:87); Go only
   shaped split/redecompose products, so a combined exec+analyze step
   burned a real worker call and recorded a blocked outcome before the
   ladder's reactive split could act. VERIFIED. **FIXED** (exec lane
   shapes the decomposed plan before the queue is built; capTotal now
   counts shaped steps) + pin `TestExecLaneInitialPlanIsShaped`.
4. **HIGH — recovery LLM calls dropped their usage** (QA #1):
   generateTimeoutSplit/generateRefinementHint read only
   resp.Content; blockDecision carried no usage; only the redecompose
   branch added spend to the outcome row. VERIFIED (Python's adapters
   record spend centrally in metrics, so this was a Go-only accounting
   hole — same class as the exec-r2 failed-turn salvage). **FIXED**
   (usage rides blockDecision, ResultError salvage included) + pin
   `TestExecLaneRecoveryCallsCountTokens` (exact-total assert against
   the Fake's 10/5 billing).
5. **HIGH — consecutive-timeout streak never reset on success**
   (QA #2): Python resets on EVERY done step (loop_execute.py:1884);
   Go reset only on non-timeout splits, so non-consecutive timeouts
   accumulated to a false "adapter appears hung" bail — a terminal
   record that lies about root cause. VERIFIED. **FIXED** + pin
   `TestExecLaneTimeoutStreakResetsOnSuccess` (timeout/success/
   timeout/timeout interleave must not bail).
6. **MEDIUM — fingerprint fed structurally different data than
   Python** (Skeptic #4, Architect #4, Minimalist #2): the folded
   Result gave reason+attempted ONE shared 200-char head vs Python's
   independent heads ((stuck_reason, result), result=attempted for
   flag_stuck, step_exec.py:1948-49) — long-reason retries differing
   only in attempted collapsed to one fingerprint, biasing toward
   "not converging". VERIFIED. **FIXED**: StepOutcome carries a typed
   (StuckReason, Attempted) pair set by every blocked path; the
   fingerprint and the ladder read the typed fields (Result keeps the
   folded human-readable form). Pin
   `TestFlagStuckCarriesTypedReasonAndAttempted`.
7. **MEDIUM — missing-input check read one signal source** (Skeptic
   #3, Minimalist #3): Python checks block_reason OR step_result;
   Go checked only the reason. Minimalist argued the folded Result
   incidentally covered both — true pre-fix, and exactly the
   fragility that the typed-field split would have silently broken.
   **FIXED** alongside #6 (checks StuckReason OR Attempted) + pin
   `TestHandleBlockedStepMissingInputSeesAttemptedSignal`.
8. **MEDIUM — retry tier escalation dropped without being named**
   (Skeptic #2): VERIFIED (loop_blocked.py:235-242, cheap→mid→power
   via step_tier_overrides). Go has no model-tier registry to wire it
   to; **NAMED** in blocked.go's comment and PORT.md's unported list —
   the honest fix for a machinery-doesn't-exist-yet divergence.
9. **LOW — WasInjected dropped for split/redecompose children**
   (Architect #5): VERIFIED (retry propagated it; the twins didn't).
   **FIXED** — children keep the injected audit mark.
10. **LOW — "pure decision" comment overclaimed** (Architect #6):
    handleBlockedStep makes live billed LLM calls. **FIXED** — comment
    now separates "no loop-state mutation" from "not side-effect-free".
11. **LOW — text-keyed retry/fingerprint maps** (Skeptic #5, QA #5,
    Minimalist #4): VERIFIED as faithful Python parity
    (step_retries/error_fingerprints key by literal step text).
    Flagged not fixed; named in PORT.md as an inherited shared-key
    risk (the generic defaultAnalysisPart fallback is the likeliest
    collision source).
12. **MEDIUM — 2× cap starves ladder thresholds on small plans**
    (Architect #2): already disclosed in PORT.md; the disclosure now
    quantifies it (1-step plan → cap 2 → 3-retry threshold
    structurally unreachable) and names Python's max_iterations=40 +
    bump as the unported machinery that gives the ladder room.
    Accepted, not fixed — the iteration-budget port is its own slice.

All six fix-layer pins are **mutation-verified**: each fix was
temporarily reverted and its pin confirmed to fail (H1-H5 + the
dual-signal MEDIUM), per the derive-mutations-from-the-file rule.
Full suite green across 12 packages, race pass clean, binary rebuilt.

### Ladder round 2 — fix-layer review (2026-08-22, SAME-MODEL FALLBACK: sonnet-medium)

Commit under review: 629123d7 (the r1 fix layer). Two lenses (Skeptic,
Expert QA), both exit 0. The flagship pattern held an **8th time**,
emphatically: both lenses independently found the round's HIGH inside
r1's MISSING_INPUT fix.

1. **HIGH (both lenses) — the outer chain clip undid the inner-clip
   fix one frame up**: r1 widened the inner clip to 1000, but the
   assembled entry ("halted on terminal verdict: <reason+instruction>
   (<metaReason>) [stop: <verdict>]") was re-clipped to 600 at the
   ledger boundary — for reasons ≳400 chars (inside Python's measured
   p99), the do-not-fabricate tail, the metaReason, AND the [stop:]
   tag were all cut. Go had no typed column: PORT.md's own "rides the
   failure-chain text" divergence meant the clip destroyed the ONLY
   copy. Python structurally protects both (typed stuck_reason in the
   result dict, stop_verdict column in record_outcome, run_trace edge).
   VERIFIED (entry assembly + budget limits + Python's loop_blocked
   :465 reason-only clip quoted). **FIXED at the root**: the outcome
   row grew the typed stop_verdict/stuck_reason columns (Python
   parity, closing the named divergence early), Result carries them,
   all three verdict-terminal sites stamp them, and the chain entry's
   verdict tag is appended AFTER the entry's single clip (marker-class,
   like the clip marker itself). Pin:
   `TestExecLaneTerminalVerdictSurvivesPersistedRecord` — Run-level,
   asserts the PERSISTED row and chain (QA's #2: the r1 pin asserted
   the decision struct, not the flow — a flow-level pin now exists;
   both the tag-ordering and typed-column mutations verified detected).
2. **HIGH (Skeptic) — inject_steps bypassed shaping**: Python shapes
   all FOUR plan-mutation surfaces (initial, split, redecompose,
   inject — loop_post_step.py:1011 label="inject"); r1 ported three.
   VERIFIED. **FIXED** (shapeSteps at the splice; children keep the
   injected audit mark) + pin `TestExecLaneInjectedStepsAreShaped`,
   mutation-verified.
3. **MEDIUM (Skeptic) — initial shaping gated to exec mode, unnamed**:
   Python's _prepare_execution runs before any lane branch. VERIFIED
   (agent_loop.py:566 unconditional). **FIXED** (unconditional, both
   lanes) + pin `TestToollessLaneInitialPlanIsShaped`,
   mutation-verified.
4. **MEDIUM (Skeptic) — dual-signal fix live for flag_stuck only**:
   the other three blocked producers have no Attempted, and Go's
   ResultError has no partial-output field — Python's killed-subprocess
   tail (step_exec.py:1659) is structurally unavailable. VERIFIED.
   **NAMED** (blocked.go comment + PORT.md honest-scope note); plumbing
   a partial-output carrier through the subprocess adapter is its own
   slice.
5. **LOW (both) — fallback comment credited the tool-less lane**,
   which never reaches the ladder; the fallback is a test-construction
   backstop. **FIXED** (comment rewritten).
6. **LOW (Skeptic) — sibling slice aliases the live backing array**:
   safe today (synchronous read-only), fragile under refactor.
   **FIXED** (invariant documented at the call site).

Zero hallucinated claims again (streak now 6 rounds). Incident note,
recorded honestly: during mutation-checking, a `git checkout --` used
as "restore" reverted loop.go to the COMMITTED state, wiping the
uncommitted r2 fixes in that file; they were re-applied immediately
(byte-identical — the test cache confirmed) and all mutation checks
re-run with in-place string flips. Lesson: never use git checkout to
unwind a mutation over uncommitted work.
