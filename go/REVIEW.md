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

### Ladder round 3 — fixpoint check (2026-08-22, SAME-MODEL FALLBACK: sonnet-medium)

Commit under review: 4871c703 (the r2 fix layer). One lens (Expert QA),
exit 0. The pattern held a **9th time**: the round's HIGH was a
completeness gap in r2's own fix.

1. **HIGH — the cap-exhaustion halt is a FOURTH terminal site r2
   missed**: r2 stamped the three verdict terminals and claimed "all
   three sites"; the queue-exhaustion break (the 2× cap, Go's stand-in
   for max_iterations) still persisted stop_verdict:"" — and Python
   types this exact halt (loop_execute.py:492-503,
   stamp_stop("out-of-budget", "hit max_iterations=...")). A
   runaway-inject halt read as unstamped to any consumer trusting the
   new columns. VERIFIED. **FIXED** (out-of-budget stamp + stuck_reason
   at the cap site) + the existing cap pin extended to assert the
   persisted typed columns, mutation-verified.
2. **MEDIUM — stale package-doc bullet** in blocked.go still said the
   typed column was unported. VERIFIED. **FIXED** (bullet updated).
3. **LOW — the new columns inherit WriteOutcome's single-point-of-loss**
   (a failed append loses the whole row; res still carries the fields
   to the caller). ACCEPTED, pre-existing whole-row behavior — named
   here, not mitigated; a durable-sink strategy is the same open
   question the Warnings doctrine already documents.
4. **Verification-positive**: the lens traced the other two [stop:]
   sites (adapter-hung, redecompose-failed) and confirmed their tags
   sit before the remainder tail and cannot be clipped away — the r2
   reorder was needed only where it was applied. Checked, not assumed.

**FIXPOINT CALL (SAME-MODEL FALLBACK: sonnet-medium):** three rounds
(4→2→1 lenses), every round's HIGH found inside the previous round's
fix and closed same-day with a mutation-verified must-detect pin —
the flagship pattern ran 7-for-7 through 9-for-9 across this tranche's
rounds. r3 converged to one completeness HIGH (fixed in-round), one
stale-comment MEDIUM (fixed), one accepted pre-existing LOW, and one
verification-positive. Zero hallucinated reviewer claims across all
three rounds (~26 findings). Terminal-path census now closed: all four
halt sites stamp the typed columns. Full suite green across 12
packages, race detector clean, binary rebuilt, two live smokes PASS
(NEED_INFO research splits, honest absence reporting, shaped
plan-mutation products, budget-exhausted terminal with remainder
named). Residuals: the named-divergence lists in blocked.go/PORT.md
(tier escalation, partial-output carrier, pending_context seam,
iteration-budget machinery, text-keyed maps).

## Memory RECALL tranche — adversarial round 1 (2026-08-22, SAME-MODEL FALLBACK: sonnet-medium)

Four lenses (Skeptic / Architect / Minimalist / Expert QA) on
86f47212..71c9ad97 — the recall tranche (internal/recall +
knowledge retrieval half + planner/loop wiring). Launch note, recorded
honestly: the first launch attempt backgrounded the four reviewers and
let the parent shell exit without `wait` — the harness reaped the
process group and all four died mid-first-turn (events captured, no
final messages). Salvage was impossible; relaunched with `wait`. All
four then returned complete numbered lists, status 0.

### Verification Ledger

1. **HIGH (Skeptic) — LoadOptions zero value returned a silent EMPTY
   result set.** `Limit < 0`-for-unlimited made `LoadOptions{}` mean
   `results[:0]` with skipped==0 and err==nil — indistinguishable from
   an empty store; every in-tranche caller dodged it by passing -1.
   VERIFIED (the truncation guard read `o.Limit >= 0`). **FIXED**:
   Limit <= 0 now means unlimited (zero value degrades to
   "everything", never "nothing" — same idiom as Clip's breaker-off),
   pinned by TestLoadOptionsZeroValueMeansUnlimited,
   mutation-verified. This round's HIGH is the tranche's own new API
   surface — the flagship fix-layer pattern's 10th consecutive
   instance (every round's HIGH inside the newest layer of change).
2. **MEDIUM (Skeptic) — coerceFloat accepted "NaN"/"Infinity" strings**;
   a NaN score survives every MinScore filter (NaN < x always false),
   uncounted. VERIFIED (strconv.ParseFloat probe: both parse, err
   nil; Python float() equally accepts — parity, but against the
   guard's purpose). **FIXED** as a named Go-stricter refusal:
   non-finite coercion fails the row and is counted; pinned by
   TestNonFiniteScoreFailsTheRow, mutation-verified.
3. **MEDIUM (Skeptic + Expert QA, independently) — evidence_sources
   type drift silently flipped citedness.** A truthy non-list value
   (e.g. `"run:abc"`) zeroed EvidenceSources, applying the 0.90
   penalty Python's bool() truthiness would not — a ranking divergence
   with no skipped-count trace. VERIFIED against knowledge_web.py:3109
   and the duck-typed construction. **FIXED**: truthy non-list lands
   as a one-element carrier (citedness preserved, drift visible);
   pinned by TestEvidenceSourcesTypeDriftKeepsCitedness,
   mutation-verified. Container fields with no ranking consumer
   (imported/canon/delta_evidence/grounding/merged_variants) keep
   coerce-to-empty — named behavior-inert in PORT.md.
4. **MEDIUM (Expert QA) — no panic isolation in the seam.** Python
   recall() blankets every substrate in except-Exception; Go had no
   recover anywhere, so an unanticipated bug in ~900 new lines of
   corrupted-data handling would crash the run, against the seam's
   own contract. VERIFIED (grep: zero recover() in the module; no
   currently-reachable panic found — defense-in-depth, not a
   demonstrated crash). **FIXED**: Recall() defers a recover folding
   the panic into Sources["error_recall_panic"], keeping partial
   substrate results.
5. **MEDIUM (Expert QA) — FindPriorAttempts had zero malformed-
   metadata fixtures** while the tiered guard had five. VERIFIED.
   **FIXED**: TestFindPriorAttemptsSkipsMalformedMetadata (corrupt
   JSON, non-object, type-drifted prompt/started_at). Mutation note,
   recorded honestly: the unmarshal-guard-removal mutation is MASKED
   by redundant downstream guards (nil-map rows fall out via the
   meta==nil and empty-prompt checks), so the pin was
   mutation-verified against the failure class it actually guards —
   an unchecked type assertion panicking on a drifted field
   (DETECTED).
6. **MEDIUM (Skeptic) — the "held recall warning reaches the failure
   chain" claim had no executing test.** VERIFIED. **FIXED**:
   TestRecallEventFailureRidesTheDecomposeFailureChain (captain's-log
   path made a directory + unparseable decompose reply → stuck row's
   chain carries the RECALL_PERFORMED write failure),
   mutation-verified.
7. **MEDIUM (Skeptic) — no fixture for a single lesson whose one line
   exceeds the whole 1200 budget.** VERIFIED (the path drops the
   block honestly, but nothing proved it). **FIXED**:
   TestOversizedSingleLessonRendersNothing (block empty, nothing
   cited), mutation-verified against the breaker-turned-truncator
   mutation.
8. **MEDIUM (Expert QA) — ContextBlock's limit-64 lacked Python's
   2026-08-14 degenerate-budget floor**; at limit<=64 the subtraction
   hits Clip's breaker-off path and returns UNBOUNDED text. VERIFIED
   (unreachable at today's 4000 constant — reintroduced-landmine
   class, the port-from-shape-not-fix-history failure mode again).
   **FIXED**: the <=128 floor ported with the subtraction.
9. **HIGH (Architect) — Recall breaks Run's context-cancellation
   contract. REFUTED as stated**: no file I/O anywhere in the module
   is ctx-bound (planner's operatorDocs, record's appends — all plain
   os calls); only LLM calls thread ctx. Recall matches the module's
   actual discipline. The kernel of value landed under #10.
10. **MEDIUM (Architect) — "O(recent activity)" overclaim.** VERIFIED:
    the cap bounds only metadata reads; the listing+stat phase is
    O(lifetime run count) in BOTH runtimes (recall.py:407-411
    identical). **FIXED** as honesty: comment + PORT.md now state the
    inherited limitation; bounding the first phase needs a
    cross-runtime index (backlog-class).
11. **MEDIUM (Architect + Expert QA) — quarantine-sidecar gap
    unnamed.** Partially REFUTED: UnionVariantsIntoLesson rewrites
    line-in-place and PRESERVES undecodable rows (verified — the
    `continue` leaves lines[i] intact), so "destructive" was wrong;
    but the sidecar IS unported and LoadTieredLessons must not become
    the read half of a rebuild-style rewrite until it lands. **FIXED**
    as naming: PORT.md states the precondition.
12. **MEDIUM (Minimalist) — QueryLessonsScored's re-truncation is
    dead code. REFUTED**: the no-signal path returns ALL lessons
    ignoring topK (the parity quirk the reviewer's own prompt
    described), so the caller-side bound is live and is Python's
    ranked[:n]. A clarifying comment landed at the site.
13. **LOW (Skeptic) — lesson-icon render drops the SF-2
    goal_achieved branch.** VERIFIED as dead-in-Python-today (no
    writer stamps goal_achieved on tiered rows — grep confirmed).
    Named in PORT.md as a synchronized-patch knob.
14. **LOW (Architect) — lexical sort on raw started_at misorders
    heterogeneous timestamp shapes.** VERIFIED as byte-for-byte
    Python parity. Named known-preserved (comment + PORT.md);
    fixing is cross-runtime.
15. **LOW (Architect) — RecallContext budget Why overclaimed
    "matches" as_context_block.** VERIFIED (Go's block is
    attempts-only). **FIXED**: Why reworded to same-bound,
    subset-coverage.
16. **LOW (Minimalist ×5) — dead float64 arms in the coercers
    (UseNumber makes them unreachable — VERIFIED, DELETED);
    unused option/parameter surface (Raw/MaxAgeDays/LessonType,
    windowHours/excludeHandleID/project, AcquiredFor) — VERIFIED,
    KEPT as Python-parity surface and named unit-tested-only in
    PORT.md (the project lane is dead on the CLI path until project
    threading lands).**
17. **LOW (Architect) — unlocked reads beside flocked writers.**
    VERIFIED as parity (Python's loader takes no read lock); safe
    while writers are single-write/atomic-rename. Named in PORT.md.

Score: 20 verified-or-partially-verified findings, 2 refuted
(A-ctx-contract, M-dead-truncation), 1 reframed (destructive→naming).
The hallucination streak ends at "claims that misread composition,
not code": both refuted claims cited real lines but drew wrong
conclusions about reachability — still zero fabricated probes/quotes
across the arc. Post-fix: 13 packages green, vet clean, race clean on
touched packages, 6 new mutation-verified pins.

## Memory RECALL tranche — adversarial round 2 (2026-08-22, SAME-MODEL FALLBACK: sonnet-medium)

Scope: the r1 fix layer (d355b394). 2 lenses (Skeptic + Expert QA) per
the fix-layer sizing rule. Artifacts:
`$SP/adv-review-recall-r2.JhE7I9/` (skeptic.md, qa.md + full
transcripts). Every code claim verified before fixing
(verify-before-fix).

### Verification Ledger

1. **HIGH (Skeptic S1 + QA QA1, independent) — the r1 citedness fix
   has an unfixed WRITER sibling.** VERIFIED: pack import.go:738 held
   `evidence, _ := row["evidence_sources"].([]any)` — a shape-only
   assertion that persists truthy drifted evidence as `[]`, so the
   read-side one-element carrier never sees the original value again.
   The round's joint top finding, sitting inside the previous round's
   fix — flagship pattern instance #11. **FIXED**: coercion extracted
   to `knowledge.CoerceEvidenceSources` (one owner), importer rewired,
   round-trip pin TestImportPreservesCitednessThroughTypeDrift
   (mutation: reverting the importer to the shape-only assertion →
   FAIL).
2. **MED (Skeptic S2) — Limit<=0-as-unlimited leaves no way to request
   zero rows.** VERIFIED as a real API consequence; ACCEPTED-NAMED: no
   caller wants an empty read from a retrieval API. Comment on
   LoadOptions + PORT.md.
3. **MED (Skeptic S3) — the recover swallows the panic's stack; the
   instrument was also never proven to fire.** VERIFIED. **FIXED**:
   recover captures `debug.Stack()` clipped under a new PanicTrace
   budget (4000, registered with Why); `panicHook` test seam between
   substrates; TestRecallRecoversFromPanicKeepingPartialResults
   asserts the named panic, a stack frame, AND pre-panic partial
   results (mutations: recover disabled → FAIL; stack dropped from
   the trace, compiling → FAIL).
4. **MED (QA QA2) — FindPriorAttempts hides malformed metadata.**
   VERIFIED: nil-meta / unparseable started_at / empty prompt were
   silently continue'd — short read indistinguishable from short
   store, the exact honesty rule the lesson loader already enforces.
   **FIXED**: `(attempts, skipped, err)` with three counted skip
   sites, surfaced as Sources["prior_attempts_skipped"];
   TestFindPriorAttemptsSkipsMalformedMetadata asserts skipped==4
   (mutation: one skipped++ removed → FAIL).
5. **MED (QA QA3) — window/no-match/exclude must NOT count as skips.**
   VERIFIED against the fix in flight; healthy-fixture tests assert
   skipped==0 so a future over-eager counter fails.
6. **MED (QA QA4) — happy-path event-failure warning unexercised.**
   VERIFIED: only the decompose-failure path had a test. **FIXED**:
   TestRecallEventFailureSurfacesAsWarningOnHealthyRun (captain's log
   as directory + healthy decompose → Result.Warnings carries
   RECALL_PERFORMED) (mutation: recallWarns dropped from the happy
   path Result → FAIL).
7. **LOW (Skeptic S4) — truthy(json.Number) err arm asserted, not
   proven.** **FIXED**: TestTruthyNumberParseErrorFailsOpen pins
   fail-open-to-truthy (mutation: err arm flipped to fail-closed →
   FAIL).
8. **LOW (Skeptic S5) — ContextBlock degenerate floor prose-only.**
   **FIXED**: TestContextBlockDegenerateBudgetStaysBounded overrides
   budget.RecallContext.Limit (save/restore) at 64/100/128 (bare cut,
   no marker) and 129 (Clip path, bounded) (mutation: floor branch
   disabled → FAIL, Clip(limit-64)≤0 goes unbounded exactly as the
   comment warns).
9. **LOW (QA QA5) — the r1 masked-mutation note is itself a finding.**
   ACKNOWLEDGED: already recorded honestly in the r1 ledger (entry 5);
   no further action — the pin was re-verified against the failure
   class it guards.

### Verdict derivation

9 findings: 1 joint HIGH fixed with a shared-owner helper + round-trip
pin, 4 MED fixed with executing tests, 1 MED accepted-named, 2 LOW
fixed, 1 LOW acknowledged. Zero refuted this round — both lenses drew
correct conclusions from correct reads. All 7 r2 mutations DETECTED
(recover, stack, skip counter, floor, truthy arm, warnings wiring,
importer coercion). Post-fix: 13 packages green, vet clean, race clean
on the 4 touched packages, binary rebuilt.

## Memory RECALL tranche — adversarial round 3 (2026-08-22, SAME-MODEL FALLBACK: sonnet-medium)

Scope: the r2 fix layer (6f0177ae). 1 lens (Expert QA) per the
converging-fix-layer sizing. Artifacts:
`$SP/adv-review-recall-r3.jkEglS/qa.md` (+ full transcript). Every
code claim verified before fixing.

### Verification Ledger

1. **HIGH (QA) — the r2 fix generalized the mechanism, not the rule:
   `provisional` was the same writer-sibling bug one field down.**
   VERIFIED: import.go:747 held `provisional, _ :=
   row["provisional"].(bool)` vs Python pack.py:830
   `provisional=bool(row.get(...))`; zero provisional fixtures
   anywhere in pack tests, so the suite was green with the defect. A
   `"provisional": "true"` row imported as trusted and passed the
   recall-time provisional gate — trust ESCALATION, strictly worse
   than the citedness penalty r2 closed. Flagship pattern instance
   #12: the round's HIGH one field below the previous round's fix.
   **FIXED**: knowledge.Truthy exported as the boolean half of the
   boundary rule (CoerceEvidenceSources is the container half),
   importer rewired, TestImportPreservesProvisionalThroughTypeDrift
   pins both drift directions (mutation: reverting to `.(bool)` →
   FAIL).
2. **MED (QA) — value-first ordering under one shared PanicTrace
   budget lets an oversized panic value crowd out the stack.**
   VERIFIED by inspection of Clip (first-N-runes) + the `%v\n%s`
   ordering — the bare-value problem r2 closed, reproduced at a
   higher threshold. **FIXED**: the value clips separately under a
   new PanicValue budget (500, registered with Why) before joining
   the stack; TestPanicTraceKeepsStackUnderOversizedValue panics
   with an ~8.8k-char value and asserts the stack survives
   (mutation: unsplit format restored → FAIL).
3. **LOW (QA) — panicHook is an unsynchronized package var, safe
   only while the package's tests stay serial.** VERIFIED. **FIXED**
   (comment-grade, matching the finding's own fix shape): the seam
   now documents no-t.Parallel() for this package and names the
   single production call site.
4. **Negative controls (QA, recorded)** — the reviewer confirmed the
   skip-counter's three sites and the not-a-skip boundary
   (window/exclude/no-match) against both fixtures and recall.go,
   and confirmed the PORT.md parity claim about Python's silent
   `continue`s against src/recall.py. No hole found.

### Verdict derivation

3 findings: 1 HIGH + 1 MED fixed with mutation-verified round-trip
pins, 1 LOW fixed comment-grade. Zero refuted. Both r3 mutations
DETECTED. Post-fix: full suite green, vet clean, race clean on the 3
touched packages, binary rebuilt. Not yet lows-only — round 4 runs on
this fix layer.

## Memory RECALL tranche — adversarial round 4 (2026-08-22, SAME-MODEL FALLBACK: sonnet-medium)

Scope: the r3 fix layer (51377350). 1 lens (Skeptic — rotated from
r3's QA). Artifacts: `$SP/adv-review-recall-r4.oJTQiS/skeptic.md`.

### Verification Ledger

1. **MED (Skeptic) — the r3 fix protects future imports only;
   pre-fix rows sit on disk as durable trusted rows with no repair
   path.** VERIFIED as a consequence; the original string was
   overwritten with a bool, so a repair scan is impossible.
   **FIXED (doc-grade, the finding's own fix shape)**: PORT.md now
   carries the operational note — re-import packs imported by
   pre-51377350 builds — plus the honest scoping that no deployment
   is affected (the Go port is pre-production by decree; no pre-fix
   importer ran outside this branch's tests).
2. **LOW (Skeptic, verified-safe) — human_reviewed at import.go:156
   is the same shape-only .(bool) class and was not touched.**
   VERIFIED and ACCEPTED-NAMED as the negative control the r3
   sibling census should have recorded: both writers (seal.go:100,
   export.go:294) emit native booleans, and the failure direction is
   SAFE — a forged string fails the assertion and the pack is
   REFUSED (fail-closed is correct for a review gate; Truthy here
   would let a forged "true" string pass — a step backward).
3. **LOW (Skeptic) — the oversized-value stack pin asserted the
   stack's HEADER, not a useful stack.** VERIFIED: the OR-assertion
   passed on "goroutine 1 [running]:" alone. **FIXED**: the pin now
   requires the deep `recall.Recall` frame (mutation: PanicTrace
   shrunk to 590 so only value+header fit → FAIL).
4. **LOW (Skeptic, correctly-scoped) — panicHook's constraint is a
   comment, not enforcement.** ACCEPTED-NAMED: proportional for a
   single-caller test-only seam; promote to a guarded setter if the
   seam grows callers (recorded in PORT.md).
5. **Negative controls (recorded)**: the round-trip provisional pin
   exercises the REAL export→seal→import path (export copies lesson
   files as raw bytes, so drift survives to the importer — verified
   at export.go:263); the PanicValue split is arithmetically sound
   (≤564-rune value+marker guarantees ~3.4k of stack, matching the
   budget's Why); Truthy has exactly one import-side call site — no
   accidental fan-out.

### Verdict derivation — FIXPOINT

**Verdict: NO BLOCKERS FOUND (SAME-MODEL FALLBACK: sonnet-medium).**
4 findings: 1 MED fixed doc-grade, 1 LOW fixed with a
mutation-verified tightened pin, 2 LOW accepted-named with the safe
direction argued. No HIGH, no production-code defect — the round
reviewed a fix layer whose changes were one exported helper, one
budget, and pins, and found only doc/test-grade residue. The recall
tranche converges at r4: r1 20v/2r → r2 9 findings (joint HIGH) →
r3 3 findings (1 HIGH) → r4 lows-only. Flagship pattern finished
12-for-12 across the arc: every round's top finding sat inside the
newest layer of change.

## Closure tranche — adversarial round 1 (2026-08-22, SAME-MODEL FALLBACK: sonnet-medium)

**Intent:** port closure_verify's evidence spine (plan-by-inversion → mechanical
checks → verdict → integrity caps) + the `internal/runs` metadata writer, wired
into the exec lane — done ≠ successful made structural, with CPython fixture
parity on fingerprint/modality/outcome/summaries.

**Scope reviewed:** f99cedb8..754ef935 (closure tranche commit). Four lenses
(Skeptic, Architect, Minimalist, Expert QA), REVIEWER_MODEL=sonnet
REVIEWER_EFFORT=medium. ~17 findings, one self-withdrawn in-flight.

### Verification Ledger

**H1 — Verdict rows persisted unscrubbed (Skeptic 1): VERIFIED.**
Python scrubs at the write site — `from secret_scrub import scrub` +
`scrub({...**row})` in `_persist_verdict_row` (closure_verify.py:966); Go's
`runs.AppendVerdictRow` was a raw `json.Marshal` append and no downstream pass
rescrubs the file. Fixed at the single write owner. The pin test then caught a
second, deeper hole: `scrub.Walk` descends only `[]any`/`map[string]any`, so the
concretely-typed `[]map[string]any` check rows passed through UNTOUCHED — the
fix routes the row through a JSON round-trip first so Walk sees only decoded
shapes. Pin: TestAppendVerdictRowScrubsSecrets (failed against Walk-only,
passes with round-trip — the mutation evidence ran forward).

**H2 — Byte-based truncation (Skeptic 2 + Minimalist 3 + QA 4): VERIFIED.**
Five+ cut sites byte-sliced (`head[:300]`, `result[:4000]`, `cmd[:200]`,
`stdout[:500]`, `stderr[:300]`, `detail[:300]`) while the same file's
Fingerprint already rune-sliced — Python str[:n] is codepoint-based, and a
mid-rune cut corrupts judge-facing evidence and the honesty markers' "%d of %d
characters" counts. Fixed with `cutRunes` at every site; marker counts are now
rune counts. Pins: TestCutRunesIsRuneSafe, TestRenderStepForClosureRuneCounts,
TestFailedCheckSignatureRuneSafe. Mutation M1 (byte-slicing cutRunes): DETECTED.

**H3 — Closure gate narrower than Python (Architect 2 + Minimalist 1):
VERIFIED.** handle.py:2627 `_closure_eligible_statuses = ("done", "partial",
"stuck", "restart")` gated by `_ran_any_step`; Go required `res.Status ==
"done"`, so a stuck exec run that wrote real files got neither verdict nor
skip row — indistinguishable from a crash. Fixed: gate is now "any step ran";
non-judging terminal paths write named skip rows ("no_steps_ran",
"tool_less_lane"). Pins: TestRunStuckWithStepsStillGetsClosure,
TestRunExecNoStepsRanWritesNamedSkipRow. Mutation M5 (done-only gate
restored): DETECTED.

**H4 — No panic recovery in Verify (QA 1): VERIFIED.** Python wraps the whole
body in `except Exception` (closure_verify.py:1907-1916, the 2026-07-27
"both tire runs lost closure this way" comment); Go had no recover, and a
panic would crash the loop AFTER the work succeeded — worse than Python's
lose-one-verdict. Fixed: deferred recover → "exception" row with
PanicValue/PanicTrace-bounded detail → nullVerdict("exception");
closurePanicHook seam mirrors recall's. Pin: TestVerifyPanicRecovered.
Mutation M2 (recover persists nothing): DETECTED.

**H5 — Durable row drops per-check evidence (QA 2): VERIFIED.** Python's row
carries a full `check_results` array (closure_verify.py:1868-1902); the Go
draft persisted aggregates + a nonstandard bare `commands` list and could not
answer "why did check N fail" from disk. Fixed: Python-parity check_results
(description/command/exit_code/outcome/stdout/stderr, rune-cut), `commands`
dropped, gaps clipped at 500, summary under VerdictProse. Pin:
TestVerifyRowCarriesCheckResults.

**H6 — Outcome classified on truncated stderr (Minimalist 2): VERIFIED.**
Python classifies on FULL stderr then stores the truncated copy
(closure_verify.py:1198-1206); Go truncated first, so an inconclusive phrase
past byte 300 flipped a verifier failure into goal-disproving hard fail.
Fixed: classify-then-truncate. Pin: TestVerifyClassifiesOnFullStderr.
Mutation M3 (classify on truncated): DETECTED.

### Mediums/lows — all fixed except where named

- Confidence safe_float parity (Minimalist 4): numeric strings coerce via
  strconv.ParseFloat, non-finite refused → default. Pin:
  TestVerifyConfidenceStringCoerced.
- Process-group kill on timeout (Architect 3 + Minimalist 5): Setpgid +
  kill(-pgid) — a Go-hardening upgrade beyond Python parity, named in
  PORT.md. Pin: TestRunCheckKillsProcessGroupOnTimeout. Mutation M4 escaped
  the liveness probe alone (the orphan holds runCheck's pipes open, blocking
  Run() the child's full lifetime — by return time the child had exited
  naturally); the pin grew an elapsed-time assertion and M4 is now DETECTED.
- WriteOutcome-failure path (Skeptic 3): best-effort named skip row +
  Finalize before the early return.
- CLI surfaces the verdict (Skeptic 4): goal line beside the DONE/STUCK
  banner — done ≠ successful is operator-visible.
- Malformed gaps coercion (QA 6): bare string → one-element slice. Pin:
  TestVerifyGapsBareStringCoerced.
- strconv.Itoa replaces hand-rolled itoa (Minimalist 6).
- Options.DryRun threaded from the call site (Architect 7).
- Signal-1 unconditionally live (Architect 1): VERIFIED as parity-with-None —
  Python with resolved_intent=None behaves identically, so this is doc-grade;
  the 18773dfa stand-down gate is owed WITH the intent subsystem. Named in
  PORT.md.
- bash-vs-sh (Architect 4): reframed in PORT.md as a NAMED upgrade (the plan
  prompt's own scaffolding teaches bash idioms), not parity.
- DONE_WITHOUT_VERDICT tripwire (QA 3): unported with the stranded-run-sweep
  family, now NAMED in PORT.md's unported list.
- Env inheritance to probes (Architect 5): unchanged — consistent with the
  codebase-wide trust model; the new durable sink is now scrubbed (H1), which
  was the actionable half.
- Architect 6 self-withdrew in-flight (exit -1 is already in the
  inconclusive exit-code set) — recorded for the trace, no action.

### Verdict derivation

6 HIGHs, all VERIFIED against the Python siblings and fixed with pinned tests;
5 of 5 must-detect mutations DETECTED (M4 after strengthening the pin — the
escape itself was a finding about the pin, recorded above). Zero fabricated
probes or quotes this round — the streak holds. Verdict: CONTESTED → fixes
applied; r2 on the fix layer next.

## Closure tranche — adversarial round 2 (2026-08-22, SAME-MODEL FALLBACK: sonnet-medium)

**Scope:** the r1 fix layer, 754ef935..ade8930e. Two lenses (Skeptic,
Architect), REVIEWER_MODEL=sonnet REVIEWER_EFFORT=medium. 11 findings.
The flagship pattern holds a 13th time: both HIGHs live in the newest
layer of change (the r1 fixes themselves).

### Verification Ledger

**H1 — Group kill defeated by detached probes; escaped descendant holds
the pipes and hangs the loop (Skeptic 1): VERIFIED.** exec.Cmd.Wait
blocks on the pipe-copy goroutines until every write-end closes;
kill(-pgid) reaches only the group, and setsid is an ordinary shell
idiom an LLM probe can emit. Root context is Background() — no upstream
deadline saves it. Fixed: c.WaitDelay = 2s; ErrWaitDelay with a
ProcessState maps to the probe's REAL exit code with a held-pipes note
appended to stderr. Pin: TestRunCheckWaitDelayBackstopsEscapedProcess
(setsid escapee; asserts prompt return, real exit code, and the note).
Mutation M6 (WaitDelay dropped): DETECTED.

**H2 — CLI verdict line bypassed the scrub the same diff installed
(Architect 1): VERIFIED.** res.Closure is the pre-scrub in-memory
struct; the only scrub sat on the row-file path, and the judge prompt
carries raw probe stdout/stderr. Fixed at the ROOT: summary/gaps are
scrubbed once at Verify's return boundary, so all four consumers (row,
metadata stamp, captain's-log event, CLI) get the same scrubbed text.
FailedChecks deliberately stay raw — fingerprint parity (Python also
fingerprints unscrubbed signatures); named in PORT.md. Pin:
TestVerifyScrubsJudgeProseAtBoundary. Mutation M7 (boundary scrub
dropped): DETECTED.

### Mediums/lows

- Second-panic in the recovery's own persist (Skeptic 3): VERIFIED
  latent — recover() does not re-arm; a persist-path panic would crash
  after all. Fixed: inner best-effort recover around the recovery's
  persist (dropped row beats dead loop). Pin:
  TestVerifyPanicInPersistDoesNotCrash. Mutation M8: DETECTED.
- JSON round-trip widens int64 → float64 silently for future callers
  (Architect 3): VERIFIED mechanically. Fixed: UseNumber through the
  decode — json.Number is not `string` to scrub.Walk's type switch, so
  it passes through intact. Pin:
  TestAppendVerdictRowPreservesLargeIntegers (2^53+1). Mutation M9:
  DETECTED.
- Skip paths printed identically on the CLI (Architect 2): fixed —
  closureLine names the SkipReason ("[skipped: exception]") and skips
  the fake 0/0 counts; extracted so it could be pinned.
- CLI line untested (Skeptic 5): fixed by the same extraction. Pin:
  TestClosureLine (cmd/maro's first test file).
- DryRun threading is dead code on the composed path (Skeptic 2 +
  Architect 4): CONFIRMED — the r1 ledger overstated it as wired. The
  field is real for direct/library callers of Verify and its unit
  tests; the loop path gates on !DryRun before the call by design
  (dry runs must leave no closure row, Python parity). Recorded
  honestly in PORT.md; the previously-unexercised "dry_run" reason
  string got its pin (TestVerifyDryRunSkipNamed). The r1 entry stands
  corrected here rather than edited in place.
- Unbounded probe-output buffering (Skeptic 6): Python
  capture_output parity — named in PORT.md rather than carried
  silently.
- Truncate-before-scrub straddle on stored stdout/stderr (Architect
  5): VERIFIED Python-parity; scrubbing pre-cut would break
  fingerprint cross-runtime comparability for secret-bearing output,
  so the residual is NAMED in PORT.md next to the scrub claim.
- closurePanicHook unsynchronized-by-convention (Skeptic 4): accepted
  — deliberate parity with the recall seam's identical pattern; the
  no-t.Parallel constraint is stated on both.

### Verdict derivation

2 HIGHs, both VERIFIED and fixed at the root with pinned tests;
mutations M6-M9 all DETECTED. One r1 ledger entry (DryRun "threaded")
corrected as overstated — the honest disposition is named-in-PORT.md,
not wired. Verdict: CONTESTED → fixes applied; r3 fixpoint check next.

## Closure tranche — adversarial round 3 (2026-08-22, SAME-MODEL FALLBACK: sonnet-medium)

**Scope:** the r2 fix layer, ade8930e..81bd780f. One lens (Skeptic —
fixpoint-check sizing). 4 findings. The flagship pattern holds a 14th
time — the HIGH is inside r2's own WaitDelay fix.

### Verification Ledger

**H1 — WaitDelay early-return reintroduces the orphan leak r1 closed
(Skeptic 1): VERIFIED.** Cancel (the group kill) fires only on ctx-Done
and stands down once Wait returns; pre-r2, a merely-backgrounded child
(same group, no setsid) held the pipes until the ctx deadline, where the
watchdog reaped it. r2's WaitDelay returned at 2s with nobody left to
kill — the common-case leak came back while fixing the detached-case
hang. Fixed: the ErrWaitDelay branch reaps the group explicitly
(kill(-pgid)) before returning; the setsid escapee residual is named as
always-out-of-reach. Pin: TestRunCheckReapsBackgroundedChildAfterWaitDelay.
Mutation M10 (kill dropped): DETECTED.

**H2/M — DowngradeReason missed by the r2 boundary scrub (Skeptic 2):
VERIFIED.** The admission regex quotes raw-summary words verbatim into
DowngradeReason, which flows unscrubbed to the metadata stamp and the
CLOSURE_VERDICT event; pure-\w secret shapes (AKIA…) ride the captured
words intact. Fixed at the same boundary block. Pin:
TestVerifyScrubsDowngradeReason — whose FIRST fixture was vacuous (an
sk-ant- secret's hyphens break the \w+ capture, so the secret never
entered DowngradeReason and mutation M11 escaped); rewritten to the
AKIA shape, M11 then DETECTED. The escape is the mutation discipline
working — recorded, not hidden.

**M — main_test.go overwrite deleted four live regression tests
(Skeptic 3): VERIFIED, my own error.** The r2 closureLine edit used
cat-over instead of append, silently deleting
TestRunDryBackendWritesHonestDryRunRow,
TestRunRefusesOutOfRangeMaxStepsBeforeAnyWrite,
TestRunRefusesFlagsAfterGoal, and TestRunPackLifecycleThroughCLI
(present since b87da153; the reviewer counted three — it was four).
Compounded by trusting r2-Skeptic's incorrect "no test files under
cmd/maro" claim without checking git. All four restored from ade8930e
with TestClosureLine appended; the file carries the lesson in a
comment. Ops lesson: a test file is append-to, never cat-over, and
reviewer claims about ABSENCE need the same verify-before-fix as
claims about presence.

**L — WaitDelay unscaled to short timeouts (Skeptic 4): fixed** —
min(2s, timeout/4), noted in PORT.md.

### Verdict derivation

1 HIGH, VERIFIED and fixed at the root; the round also caught a
self-inflicted test deletion and a vacuous pin fixture. Mutations
M10-M11 DETECTED (M11 on the second fixture). Not yet a fixpoint —
r4 checks this layer. Verdict: CONTESTED → fixes applied.

## Closure tranche — adversarial round 4 — FIXPOINT (2026-08-22, SAME-MODEL FALLBACK: sonnet-medium)

**Scope:** the r3 fix layer, 81bd780f..28340947. One lens (Skeptic).
NO HIGH FOUND — the reviewer traced both r3 flagship fixes end-to-end
(WaitDelay group reap: confirmed Cancel never fires on the ErrWaitDelay
path and the explicit kill is minimal and race-free; DowngradeReason:
confirmed detection correctly reads raw text while both live sinks get
the scrubbed field) and reported them sound.

### Ledger (lows, all addressed)

- **M — WaitDelay timeout/4 scaling unreachable and unpinned:
  VERIFIED** (no caller sets TimeoutPerCheck; both pins used 10s
  timeouts). Fixed per the reviewer's option (b): the arithmetic is now
  falsifiable — TestRunCheckWaitDelayScalesToShortTimeouts (2s timeout
  → returns on the ~500ms scaled grace, measured 0.50s). Mutation M12
  (flat 2s restored): DETECTED. The r3 "L — fixed" entry stands
  corrected: it was fixed-but-unfalsifiable until now.
- **L — "scrub ONCE" comment described two-of-three fields as
  exhaustive:** fixed — one boundary block, comment names all three
  prose fields and the add-a-field rule.
- **L — reap-pin timing bounds under loaded runners:** accepted as-is
  (6s + 2s-poll against a 2s grace and SIGKILL delivery); revisit only
  if it ever flakes.
- **Correction (r3 ledger):** the four restored tests span TWO files —
  three in main_test.go plus TestRunPackLifecycleThroughCLI, which
  lives in pack_cmd_test.go and was never damaged. No coverage was
  lost; the r3 phrasing cost the r4 reviewer real verification time.

### Verdict: NO BLOCKERS FOUND — FIXPOINT (SAME-MODEL FALLBACK: sonnet-medium)

Closure tranche converged r1→r4: 6 verified HIGHs (r1) → 2 (r2) → 1
(r3) → 0 (r4), lows-only close. Flagship pattern finished 14-for-14
across the tranche's rounds — every round's top finding sat in the
newest layer of change, including two in this reviewer lineage's own
prior fixes. Mutations M1-M12 all DETECTED (two on strengthened pins,
both escapes recorded). Zero fabricated probes or quotes across all
four rounds.

## Routing tranche slice 1 — adversarial r1 (2026-08-22, 4 lenses, SAME-MODEL FALLBACK: sonnet-medium)

Diff b2aded18..f8c6aac7 (intent classifier + NOW lane + -lane CLI).
Verdict: CONTESTED — 6 verified HIGHs, all fixed same round.

Verification ledger (verify-before-fix, every claim traced):
- VERIFIED (Skeptic/Architect/Minimalist, independently): VerdictSummary
  scrubbed per-sink (row, stamp) but NOT at the boundary — main.go
  printed the raw judge why to the terminal. Fixed: scrub where the
  field is SET in verifyNow (closure doctrine); pin
  TestVerifyNowScrubsWhyAtBoundary; mutation M18 DETECTED.
- VERIFIED (Skeptic): only the 160-token half of the 2113a608/ed7cf400
  fix was ported — Python's _now_verdict_rationale (trailing-prose
  recovery) was missing, and the static fallback FALSELY said "judge
  gave no rationale". Fixed: verdictRationale port; pin
  TestVerifyNowRecoversTrailingRationale; M19 DETECTED.
- VERIFIED (Expert QA): StampVerdict's confidence float64 wrote a
  fabricated 0 on every NOW verdict (Python confidence=None pops).
  Fixed at the ROOT: signature → *float64, nil pops; loop passes
  &v.Confidence; pins in runs_test + TestRunNowStampsNoConfidenceKey;
  M16 DETECTED.
- VERIFIED (Expert QA + Skeptic): F7 marking was terminal-only — a
  judge-error row and an unparseable-verdict row were byte-identical on
  disk. Fixed: record.Outcome.GoalVerdictSource; go_now_verify_v1 /
  go_now_verify_error / absent taxonomy; pin
  TestRunNowRowCarriesVerdictSource; M17 DETECTED.
- VERIFIED (Expert QA + Architect): three new Complete call sites
  dropped llm.ResultError usage (3-vs-3 split against exec.go/loop.go).
  Fixed: salvage in llmClassify + now.Run + verifyNow; pins
  TestClassifyRefusedCallSalvagesUsage +
  TestRunNowSalvagesResultErrorUsage.
- VERIFIED (all four lenses): classify-call tokens discarded — every
  -lane auto row under-reported real spend. Fixed: seed threading
  (now.Run seedIn/seedOut, loop.Opts SeedTokens*, main.go folds); pins
  TestRunNowSeedTokensReachRow + TestRunSeedTokensReachOutcomeRow; M21
  ESCAPED on the first pin (small seed vacuous against organic Fake
  usage — pin strengthened to a seed no natural count reaches), then
  DETECTED; M22 (now-side seed init) DETECTED.
- VERIFIED (Minimalist + Architect): judge window unbounded, marked
  truncation (_now_verify_payload) silently dropped. Fixed:
  verifyPayload port, 2000-char cut, visible marker; pin
  TestVerifyNowPayloadTruncationMarked; M20 DETECTED.
- VERIFIED (Architect, doc gap): _mark_memory_provenance advisory not
  in the unported-named list. Fixed: named in now.go doc + PORT.md.
- VERIFIED (Architect + Minimalist, LOW): config.Load ×3 per classify,
  warnings swallowed, split-brain risk between override gate and
  heuristic. Fixed: single Load in Classify, cfg threaded into
  heuristicClassify.
- VERIFIED (Skeptic, MED): TestRunLaneRoutingEndToEnd asserted only
  task_type. Fixed: dry_run fence, summary, unjudged assertions added.
- REFUTED (Skeptic HIGH, "now.Run must gate on dryRun"): Python parity
  — handle() swaps in _DryRunAdapter at the same boundary; neither
  runtime gates calls inside the lane. Contract documented on the
  package instead.
- REFUTED (Minimalist MED, "row verdict fields have no reader —
  delete"): Python's NOW row carries goal_achieved /
  goal_verdict_summary / goal_verdict_source (handle.py ~1540); the row
  is the cross-runtime ledger surface — parity is the reader.
- ACCEPTED (QA LOW): three hand-written safe_float coercions — extract
  when a third JSON-verdict parser appears.
- ACCEPTED (Skeptic LOW): row answer-summary unscrubbed — Python
  parity, named residual in PORT.md beside the loop's identical one.

Mutations M16-M22 all DETECTED (M21 on a strengthened pin — escape
recorded above). Flagship pattern: 15-for-15 — this round's HIGHs sit
squarely in the newest layer (the NOW lane's verify/stamp path).

## Routing tranche — adversarial r2 (2026-08-22, 2 lenses, SAME-MODEL FALLBACK: sonnet-medium)

Diff f8c6aac7..c6fc3137 (the r1 fix layer). Verdict: CONTESTED — 2
verified HIGHs (one shared by both lenses), all fixed same round.

Verification ledger:
- VERIFIED (Skeptic + Architect, independently — the round's headline):
  the loop lane's outcome row NEVER carried the verdict fields — the
  row is written at loop finalization, closure judges afterwards, and
  Python solves exactly this with memory_ledger.stamp_outcome_verdict
  (post-hoc locked row rewrite) which Go never ported. r1's "the row is
  the cross-runtime ledger surface" REFUTED-defense was true only for
  the NOW lane. Fixed: record.StampOutcomeVerdict (newest-matching-row
  merge under the shared flock; nil achieved leaves prior verdicts, nil
  confidence removes the key), called from loop.Run after StampVerdict;
  pins extended in closure_wire_test (judged row + unjudged row);
  mutation M25 DETECTED.
- VERIFIED (Skeptic HIGH): verifyPayload truncated by BYTES with a
  marker claiming characters — mid-rune UTF-8 splits + miscounts, the
  exact class closure's cutRunes fixed. Fixed: rune slice/count; pin
  TestVerifyPayloadRuneSafe (2-byte-rune fixture); M23 DETECTED.
- VERIFIED (Architect MED): &v.Confidence passed unconditionally — an
  unjudged closure stamped goal_verdict_confidence: 0 into metadata,
  the fabrication the *float64 change exists to prevent. Fixed: gated
  on v.Judged (Go-stricter divergence named — Python writes 0.0);
  unjudged pin extended; M26 DETECTED.
- VERIFIED (Skeptic MED): verdictRationale's brace scan counted braces
  inside JSON strings, and an unbalanced object fell through to
  returning the raw JSON blob as the "rationale". Fixed: string-aware
  scan (inString/escaped) + closed-guard returning "" (Go-stricter than
  Python's identical naive scan, divergence named); pin
  TestVerdictRationaleStringAwareBraces; M24 DETECTED.
- VERIFIED (Skeptic LOW): clip-before-scrub in the rationale path —
  VerdictProse.Clip inside verdictRationale ran before the caller's
  scrub, able to cut a credential mid-string past fixed-length
  patterns. Fixed: no internal clip (sinks clip; scrub at boundary sees
  full text); pin TestVerifyNowScrubBeforeClip (secret past the cap).
- VERIFIED (Architect MED): main.go's classify-usage extraction had
  zero coverage with a nonzero value (dry classify is heuristic-only —
  0 == 0 either way). Fixed: routeLane extraction + pin
  TestRouteLaneExtractsClassifyUsage (42/17 fixture); M27 DETECTED.
- VERIFIED (Architect LOW): bare source-string literals across two
  lanes. Fixed: record.SourceNowVerify/SourceNowVerifyError/
  SourceClosure constants, both writers switched.
- ACCEPTED (Architect LOW, Python parity, named in code): prose-before-
  JSON returns the whole text from verdictRationale, JSON included.
- ACCEPTED (Architect LOW): judge MaxTokens stays 160 while the
  recovery path rewards longer replies — Python shares the same budget;
  the unbalanced-object guard now makes mid-JSON truncation recover
  nothing rather than garbage. Revisit if unjudged rates rise.

Mutations M23-M27 all DETECTED first try. Flagship 16-for-16: the
headline HIGH (row never verdict-stamped for the loop lane) sits in the
newest layer — the row schema this tranche extended.

## Routing tranche — adversarial r3 (2026-08-22, 1 lens, SAME-MODEL FALLBACK: sonnet-medium)

Diff c6fc3137..45079a5b (the r2 fix layer). Verdict: CONTESTED — 1
verified HIGH in the newest code (the ledger-rewrite port), fixed same
round.

Verification ledger:
- VERIFIED (HIGH): StampOutcomeVerdict dropped goal_verdict_at (the
  timestamp Python's own comment calls load-bearing for the learning
  pipeline's framing→verdict delay) and the verdict_history re-stamp
  honesty block (2026-08-10 decree), with neither named as a residual.
  Fixed: both ported; verify-before-fix also caught a THIRD parity bug
  the reviewer missed — my delete-on-nil confidence was wrong (Python's
  row stamp MERGES: nil leaves an existing key; only runs.StampVerdict's
  full-replacement stamp pops) — fixed and the distinction documented
  on both functions. Pins: TestStampOutcomeVerdictNewestDuplicateAndHistory
  + extended nil-semantics pin; mutations M28 (history), M31
  (verdict_at) DETECTED.
- VERIFIED (MED, half): a failed row stamp left only a terminal
  warning — the headline bug behind a rarer trigger. Fixed: durable
  "outcome_row_stamp_failed" run-dir row. The retry half of the finding
  OVER-CLAIMED: Python's max_attempts defaults to 1 (no retry by
  default) — retry knob named as residual instead, not ported.
- VERIFIED (MED): no duplicate-loop_id fixture — a flipped scan
  direction passed every distinct-id test. Fixed: two-rows-one-loop_id
  pin; mutation M29 (oldest-wins flip) DETECTED.
- VERIFIED (LOW): verdictRationale walked raw content while its
  sibling jsonx.Object strips <think> traces — a trace could become the
  durable verdict summary. Fixed: jsonx.StripThink exported + wired
  (Go-stricter, Python sibling shares the gap, named); M30 DETECTED.
- VERIFIED (LOW): orphaned .tmp on rename failure. Fixed: removed on
  the error path.
- NOTE (verified-safe by the reviewer, quoted): torn-tail framing,
  unparseable-line preservation, lock coverage, and Split/Join framing
  round-trip all traced clean — the rewrite composes with
  AppendRawLine's tail check correctly.

Mutations M28-M31 all DETECTED first try. Flagship 17-for-17 (the HIGH
sits in StampOutcomeVerdict — the newest function in the port).

## Routing tranche — adversarial r4 (2026-08-22, 1 lens, SAME-MODEL FALLBACK: sonnet-medium)

Diff 45079a5b..50a34672 (the r3 fix layer). Verdict: CONTESTED at the
doc layer only — 1 verified HIGH (a stale doc comment), no code-logic
HIGHs. Fixed same round; convergence is at hand.

Verification ledger:
- VERIFIED (HIGH, doc): StampOutcomeVerdict's exported doc still said
  "nil confidence REMOVES the key" — the exact semantics r3 reversed.
  Fixed: doc now states the merge semantics and names the pop-owner
  (runs.StampVerdict).
- VERIFIED (MED): the outcome_row_stamp_failed marker path had zero
  coverage. Fixed: fault-injection pin (directory on the .tmp path
  fails the rewrite while appends succeed) asserting warning + durable
  marker + surviving metadata stamp; mutation M32 DETECTED.
- VERIFIED (LOW): history entries carried JSON null where Python
  writes "" for missing prior source/at (foreign-judged rows). Fixed:
  ""-defaulted type asserts; pin
  TestStampOutcomeVerdictHistoryOnForeignJudgedRow; M33 DETECTED.
  (Confidence null in history IS Python parity — .get with no default.)
- VERIFIED (LOW): no pin proved goal_verdict_at advances on re-stamps.
  Fixed: progression asserts on both judged and UNJUDGED re-stamps;
  M34 ESCAPED first (non-empty-only assert satisfied by the first
  stamp — strengthened to strict advancement), then DETECTED.
- NOTED (LOW, operational): closure_verdicts.jsonl has no production
  reader — the durable marker is manually discoverable only; carried
  into PORT.md.
- NOTED (clean passes, quoted): no double-write/masking in the marker;
  runDir guard traced sound.

Mutations M32-M34 all DETECTED (M34 on a strengthened pin — escape
recorded). Round yield: doc + test hardening only, no behavior HIGHs —
NEXT ROUND IS THE FIXPOINT CHECK.

## Routing tranche — adversarial r5 (2026-08-22, 1 lens, SAME-MODEL FALLBACK: sonnet-medium)

Diff 50a34672..336741d1 (the r4 fix layer + doc amendment). Verdict:
0 HIGHs — 1 MED and 2 LOWs, all in the marker/history seam this round
existed to check.

Verification ledger:
- VERIFIED (MED): loop.go's outcome_row_stamp_failed marker discarded
  its OWN write failure (`_ = runs.AppendVerdictRow(...)`) — a doubly-
  failed stamp degraded silently to pre-marker behavior. Fixed: the
  error now appends a named warning ("outcome-row stamp marker write
  also failed: ..."). Coverage is at the AppendVerdictRow seam
  (TestAppendVerdictRowFailsOnPoisonedPath: a directory squatting on
  build/closure_verdicts.jsonl must error); loop-level double
  injection is non-deterministic by construction — the run's own
  closure verdict row (loop.go:703) creates the file as a real file
  before the marker (loop.go:743) fires, so no external squat can
  reach the marker alone. Named on the pin. M35 (AppendVerdictRow
  swallows its open error) DETECTED. Residual, named: the warning-
  append line itself is proven reachable but its text is not pinned
  end-to-end through loop.Run.
- VERIFIED (LOW): verdict_history's key-presence gate
  (`row["goal_achieved"]` exists ⇒ judged) leaned on an unstated
  invariant — every writer pops nulls before writing, so presence ≈
  Python's `is not None`. Normalized to `judged && prior != nil` with
  a comment naming the invariant; a foreign row carrying an explicit
  JSON null now counts unjudged, matching Python.
- NOTED (LOW, no action): the strict goal_verdict_at advancement pin
  depends on µs timestamp granularity — two stamps inside the same
  microsecond would flake. Accepted: nowISO carries µs and the pin
  does two full flock'd rewrites between captures; if it ever flakes,
  insert a monotonic tiebreak, don't loosen the assert.

Mutation M35 DETECTED. Reviewer's closing line: "Finding 1 is the one
item I'd want addressed before calling this a true fixpoint" — it is
addressed. NEXT ROUND IS FIXPOINT CONFIRMATION (expect NO BLOCKERS
FOUND).

## Routing tranche — adversarial r6 (2026-08-22, 1 lens, SAME-MODEL FALLBACK: sonnet-medium) — FIXPOINT

Diff 336741d1..da136658 (the r5 fix layer + gofmt sweep). Verdict:
NO BLOCKERS FOUND (SAME-MODEL FALLBACK: sonnet-medium) — 0 HIGHs,
1 MED (test-coverage) + 1 LOW (dead doc citation), both fixed same
round; 2 informational LOWs verified-no-action.

Verification ledger:
- VERIFIED (MED): the r5 `judged && prior != nil` gate shipped with no
  explicit-JSON-null fixture — a revert to bare presence would regress
  silently. Fixed: TestStampOutcomeVerdictNullGoalAchievedIsUnjudged
  (hand-written row with `"goal_achieved":null`; stamp must land the
  verdict WITHOUT a history push — Python `is not None` parity, which
  the reviewer independently re-verified at memory_ledger.py:819).
  Mutation M36 (gate reverted to bare presence) DETECTED — and the
  mutant's failure output shows the exact bug: a history entry
  carrying goal_achieved:null.
- VERIFIED (LOW): record.go's invariant comment cited a nonexistent
  Python function (`_scrub_for_write`); the real pop-null owner is
  `_verdict_row` (memory_ledger.py:462). Citation fixed.
- VERIFIED-NO-ACTION (LOW): the reviewer independently confirmed the
  loop-level double-injection non-determinism claim (random loopID at
  loop.go:146 makes pre-squatting the run's verdicts file impossible)
  — recorded so it is not re-litigated without new evidence.
- NOTED (LOW, no action): the doubly-failed warning's literal text is
  proven reachable (seam pin + main.go prints res.Warnings) but not
  string-asserted end-to-end; the named residual stands. A helper
  extraction would close it — not worth the seam churn at fixpoint.

FIXPOINT DECLARED (SAME-MODEL FALLBACK: sonnet-medium). Six rounds:
6→2→1→1(doc)→0-HIGH(1 MED)→0-HIGH(coverage MED on the prior fix,
test-only). The r6 fix layer is a test + a comment citation — no
production code changed — and is mutation-verified (M36), so no r7 is
spawned. Mutations M13–M36 all DETECTED (M21, M34 after pin
strengthening — escapes recorded above).

## Director tranche — adversarial r1 (2026-08-22, 4 lenses, SAME-MODEL FALLBACK: sonnet-medium)

Diff 8ba0daa7..31584be4 (plan/delegate/review + workers + accumulator).
Verdict: CONTESTED — 4 deduplicated verified HIGHs, 5 MEDs, several
LOWs; all fixed or accepted-named same round (fix commit eee6a641).
Zero fabricated probes/quotes across all four lenses (arc streak
intact).

Verification ledger:
- VERIFIED (HIGH, Skeptic+Architect+Minimalist independently): the
  WORKER_DELEGATION_GAP event wrote raw ticket/stuck-reason prose into
  captains_log.jsonl unscrubbed while writeLog scrubbed the SAME
  string one function later — the "single write boundary" doc claim
  was false. Fixed: scrub.Secrets before the Clip on both previews;
  pin TestRunGapEventScrubbed (AKIA fixture through flag_blocked);
  M43 DETECTED. Python's log_event doesn't scrub either — Go-stricter,
  backport candidate.
- VERIFIED (HIGH, Architect+Minimalist+QA independently): a parseable
  review verdict whose "accepted" was missing/null/mistyped silently
  ACCEPTED (type-assert fell through to the true default) — and QA
  showed Go DIVERGED from Python on explicit null (bool(None)=False
  rejects there). Fixed: field-level gate — non-bool "accepted"
  rejects with a named reason; deliberately stricter than Python's
  absent-key accept and truthy-"false" coercion (both named). Pin
  TestReviewVerdictFieldGate (4 forged shapes + well-formed false);
  M44 DETECTED.
- VERIFIED (HIGH, QA): a rejection with no revision_request was a
  silent no-op — same report, same DONE, zero durable trace; and
  res.Warnings lived only on stderr. Fixed: named warning on
  rejected-no-revision; writeLog now persists review_decisions
  (ticket-correlated via new ReviewDecision.TicketID, scrubbed) and
  warnings (scrubbed). Python persists neither — Go-stricter, backport
  candidate. Control flow unchanged (rejected results still ship —
  Python parity, documented on the type: review is a revision trigger
  and audit trail, not a report-inclusion gate). Pin
  TestRunPersistsDecisionsAndWarnings; M45 DETECTED.
- VERIFIED (MED, Skeptic+Architect+QA): a non-string/null spec ticket
  task silently became an EMPTY dispatched ticket (and blocked the
  no-tickets fallback from firing). Fixed: malformed entries skipped
  with a warning; all-malformed falls back to the whole-directive
  ticket. Pin TestRunMalformedTicketEntriesSkipped; M46 DETECTED.
- VERIFIED (MED, Skeptic): report-echo judged the FULL result against
  a report compiled from the first 4000 chars — false DROPPED
  verdicts for long outputs. Fixed: one window per worker shared by
  the compile prompt and the echo check (named divergence: Python
  still compares unclipped — honesty-direction). Pin
  TestCompileEchoJudgesClippedWindow; M47 DETECTED.
- VERIFIED (MED, QA): workers' >20-char bare-content bar counted
  BYTES (lenient direction on a refusal gate; Python counts chars).
  Fixed: rune count; pin TestDispatchBareContentGateCountsRunes; M48
  DETECTED.
- VERIFIED (MED, QA): challengeSpec swallowed call/parse failures with
  no record (less visible than Python's debug log). Fixed: named
  warnings on both failure paths.
- VERIFIED (MED, Minimalist+QA+Architect): the 4000 literals at the
  review/compile windows bypassed the budget registry. Fixed:
  WorkerJudgeWindow registered with rationale, used at both sites.
- ACCEPTED-NAMED (Architect/QA MED): Accumulator.Add's bespoke marker
  is deliberate — byte-compatible with Python ContextBudget.add's
  SECOND marker format, so mixed-runtime renders parse with one
  reader; entries are cut exactly once. Comment now says so.
- VERIFIED (LOW, QA): gap-scoping rule duplicated at two sites —
  extracted isDelegationGap, both callers converted.
- Fixed (LOWs): clipRunes reuse in the dry spec path; echoStopwords
  comment de-aspirationalized (only Go consumer today).
- ACCEPTED-NAMED (LOWs): acceptance warning is print-only (no approval
  surface — already named in the CLI); Python's worker_type/ticket
  spot-check unported (cannot diverge in Go by construction);
  challenger critiques not itemized (log layer unported); spec-prompt
  duplication kept (verbatim Python parity beats DRY for ported
  prompt text); per-event flock stacking under contention (bounded
  30s each, low event count) noted, no action.

Mutations M43-M48 all DETECTED. Fix layer: scrub + field gate +
durable audit trail + malformed-ticket skip + shared echo window +
rune gate + challenger warnings + registry budget. NEXT ROUND REVIEWS
THE FIX LAYER.

## Director tranche — adversarial r2 (2026-08-22, sonnet-medium fallback ×2: skeptic + qa)

Round on the r1 fix layer. The flagship pattern held an 18th time: both
lenses' shared HIGH sat inside r1's own TicketID-correlation fix.

- **HIGH (both lenses): post-revision `ReviewDecision.TicketID` was an
  orphaned correlation key** — the revised ticket (fresh `newID()`) was
  never persisted into `res.Tickets`, so the durable log's second
  decision row resolved against nothing; `RevisionOf` was written once
  and read nowhere. FIXED: revised tickets append to `res.Tickets`,
  ticket rows persist `revision_of`, and the correlation pin walks the
  log asserting every `review_decisions[].ticket_id` resolves (M49,
  M54).
- **HIGH (skeptic): the r1 `accepted` field gate discarded the model's
  own `reason`/`revision_request`**, converting a revisable rejection
  (`{"accepted": "false", "revision_request": "…"}`) into an
  unrevisable best-effort ship. FIXED: diagnostics extracted before the
  gate, malformed TYPE named in the reason (`(was %T)` + clipped model
  reason), RevisionRequest preserved so the revision loop still fires
  (M50). Absorbs QA LOW #6 (type-in-reason).
- **MED (skeptic): malformed spec-entry warnings unbounded** while the
  accept path was capped at maxTickets. FIXED: one summary warning
  carrying the count (M52).
- **MED (qa): whitespace-only `revision_request` dodged the
  no-revision warning and bought a vacuous retry.** FIXED: trimmed at
  parse so every downstream branch compares real content (M51).
- **MED (qa) + LOW (skeptic): CLI printed `StuckReason`/`Reason`
  unscrubbed** — the terminal is routinely piped/tee'd; the sibling
  census had stopped at the two file sinks. FIXED: `scrub.Secrets` at
  both print sites, review trail prints TicketID.
- **MED (skeptic): scrub-at-set-point doctrine** (scrub where
  `workers.Result` is populated, à la now.go) vs per-sink scrubbing.
  ACCEPTED-NAMED: sink-side is the boundary here — set-point scrubbing
  would alter PROMPT inputs (review/compile prompts see raw worker
  text, Python parity); the sink census is now writeLog + captains_log
  + CLI, all scrubbed. Backport-relevant if Python ever grows a fourth
  sink.
- **LOW (qa): mid-loop empty-revision round is a silent no-op** —
  unreachable today (MaxReviewRounds=2 ⇒ single iteration). FIXED
  anyway: in-loop guard warns + breaks, named as
  unreachable-at-current-constant.
- **LOW (skeptic): clip-marker vocabulary ("truncated"/"characters")
  counted as distinctive worker terms** in the echo judge for clipped
  windows. FIXED: deleted after term extraction — the shared stopword
  table stays byte-identical to Python's (M53, pinned only after the
  first mutation run came back NOT DETECTED).
- **LOW (qa): forged Accumulator truncation marker unfixtured.**
  PINNED: an under-cap entry whose content contains the literal marker
  renders verbatim, once — the accepted Python-parity risk is now
  measured, not assumed (M55).

Mutations M49–M55 all DETECTED. Full suite green. Fix-layer round r3
follows on this diff.

## Director tranche — adversarial r3 (2026-08-22, sonnet-medium fallback ×2: skeptic + qa)

Round on the r2 fix layer. The flagship pattern held a 19th time — this
round's HIGHs sat inside r2's own fixes (the mid-loop guard and the
bounded-warning counter).

- **HIGH (skeptic): the r2 mid-loop empty-revision guard was NOT
  unreachable** — with MaxReviewRounds=2 the sole loop iteration is
  both first and last round, so a revision review rejecting with no
  guidance fired the "stopping revisions" warning AND the exhaustion
  warning for one incident, while the comment claimed the branch was
  dead. FIXED: warn only when the break cuts remaining rounds short
  (genuinely unreachable at the current constant), break regardless;
  the terminal round is the exhaustion warning's job (M56).
- **HIGH (qa) + MED (skeptic): the r2 malformed-entry counter had
  holes for the two most LLM-plausible shapes** — non-object array
  entries (strings/numbers/null) evaded the counter entirely, and a
  present-but-non-list `tickets` field discarded the model's whole
  plan with zero durable trace. FIXED: non-object entries count into
  the same summary (wording now shape-agnostic), and a non-list
  tickets field warns with its type before the single-ticket fallback
  (M57, M58). Python parity note: Python silently drops both shapes —
  the counter is a Go-invented net, now without holes.
- **MED (qa): CLI printed `res.Warnings` unscrubbed** while writeLog
  scrubs the same slice — the r2 sink-census claim ("CLI, all
  scrubbed") was false for this one field. FIXED: scrubbed at the
  print site; static text today, the invariant is now structural at
  every sink. The r2 census claim is corrected by this entry.
- **LOW (skeptic + qa ×2): the r2 echo marker-vocab deletes were
  unconditional, missed 5+ digit clip offsets, and missed the
  Accumulator's "entry truncated" variant.** FIXED: the whole marker
  (both budget wordings) is stripped by regex BEFORE term extraction —
  offsets and "entry" go with it, and genuine content that merely
  discusses truncation keeps its vocabulary (M59, M60).
- **LOW (skeptic): the revision-correlation pin was single-ticket.**
  FIXED: multi-ticket pin asserts revision_of resolves to the correct
  sibling and untouched tickets carry none.

Mutations M56–M60 all DETECTED (M58 confirmed compiling, behavioral).
Full suite green. r4 follows on this diff.

## Director tranche — adversarial r4 (2026-08-22, sonnet-medium fallback ×2: skeptic + qa)

Round on the r3 fix layer. Flagship pattern, 20th time: the HIGH sat
inside r3's own guard fix.

- **HIGH (qa): r3's one-warning invariant was true only at
  MaxReviewRounds=2** — the exhaustion warning was unconditional, so a
  mid-loop no-guidance stop at N=3 would warn twice (the identical bug
  class r3 took credit for fixing, one constant bump away). FIXED
  structurally: `stoppedEarly` flag suppresses the exhaustion warning
  when the mid-loop guard already recorded the incident;
  MaxReviewRounds became a var (Python module-attr parity) so the N=3
  invariant is PINNED, not asserted by comment (M61).
- **MED (skeptic): malformed entries trailing the maxTickets cap were
  uncounted** — the cap break ran before the shape check. FIXED: full
  scan counts shapes; the cap now bounds valid appends only (still one
  summary warning, no inflation) (M62).
- **MED (skeptic) + LOW (qa): clipMarkerRe stripped forged
  marker-shaped text anywhere in worker content**, contradicting the
  accum doctrine (forged markers are content, rendered verbatim) and
  letting a hostile worker erase vocabulary to suppress the
  echo-omission signal. FIXED: anchored at line end (`(?m)…$`) and
  digit-bounded (`\d{1,9}`) mirroring budget.markerRe — real markers
  only ever terminate a line; mid-line forgeries keep their vocabulary
  (M64, negative fixture).
- **LOW (qa): an explicitly empty tickets list fell back with zero
  trace.** FIXED: warns before the single-ticket fallback (M63).
- **LOW (skeptic): %T warning tested only for string.** FIXED: pin
  parametrized over string/number/bool/object.
- **LOW (skeptic, informational): CLI Report/Summary are unscrubbed by
  long-standing design** ("the terminal is the delivery surface",
  pre-r3). Scope note accepted: the sink-census claim covers
  res.Warnings and LLM-authored review/blocked text, not the report
  body — which IS the deliverable. No change.

Mutations M61–M64 all DETECTED. Full suite green. r5 follows.

## Director tranche — adversarial r5 (2026-08-22, sonnet-medium fallback ×2: skeptic + qa)

Round on the r4 fix layer. Flagship pattern, 21st time: both lenses'
HIGH sat inside r4's own marker-anchor fix.

- **HIGH (both lenses): the r4 `(?m)` line-end anchor was trivially
  bypassed** — a forged marker followed by a newline (the natural
  shape of generated text) still stripped, and QA traced the deeper
  error: reportEcho only ever sees budget.Clip output, where a genuine
  marker exists ONLY at true end-of-string — the "Accumulator variant"
  justification described a code path that never reaches reportEcho.
  FIXED at the root: `budget.StripMarker` now owns the grammar
  (markerRe verbatim, no multiline, true-end only, Clip's exact " … "
  wording); the director's local regex is deleted; the entry-variant
  and every non-final-line marker are worker CONTENT and keep their
  vocabulary. Fixtures re-shaped to Clip's real marker; the
  non-final-line must-detect Skeptic demanded is pinned in both
  packages (M65).
- **MED (both lenses): MaxReviewRounds as an EXPORTED mutable global**
  — any importer could race a concurrent Run. FIXED: unexported
  (`maxReviewRounds`); in-package tests still override serially with
  Cleanup restore (Python module-attr parity kept). Options-struct
  threading deferred until Run has a second real caller — named, not
  silent.
- **MED (skeptic): an ABSENT tickets key evaded the whole
  discarded-plan census** (explicit [], null, and non-list all warned;
  omission didn't). FIXED: absent key warns before the fallback (M66).
- **LOW (qa): the 4-shape non-list pin used a bare IIFE** — first
  failure masked the rest. FIXED: t.Run subtests; `null` added as a
  5th shape.
- **LOW (qa, tripwire note): "review exhausted 0/1 rounds" wording at
  degenerate maxReviewRounds** — accepted-named as the first
  observable symptom of test pollution, not a fix target.
- **LOW (skeptic, meta): "M64 DETECTED" proved the fixture, not the
  class.** Correct — this round's M65 pins the class (non-final-line
  forgery) in both director and budget packages.

Mutations M65–M66 DETECTED. Full suite green. r6 follows.

## Director tranche — adversarial r6 (2026-08-22, sonnet-medium fallback ×2: skeptic + qa)

Round on the r5 fix layer. Flagship pattern, 22nd time — and this
round closed the ROOT of the four-round marker arc.

- **HIGH (both lenses): shape+position still wasn't provenance.**
  budget.Clip is a NO-OP on under-limit text (the statistically
  dominant case: median worker output 1,180 runes vs the 4000 cap) and
  on the idempotency pass-through, so an unclipped worker result
  ending in a forged well-formed marker sailed to StripMarker and was
  stripped — tunable to flip reportEcho false→nil and suppress
  WORKER_REPORT_OMISSION. The r2→r5 march (delete words → strip
  anywhere → per-line anchor → true-end anchor) kept tightening
  POSITION; the flaw was inferring provenance from shape at all.
  FIXED at the actual root, the fix shape both lenses independently
  demanded: `budget.ClipInfo` returns (clipped text, did-THIS-call-cut
  bit); reportEcho strips only when the bit is true. An un-cut
  window's marker-shaped tail is worker content and keeps its
  vocabulary. Pinned end-to-end through the REAL WorkerJudgeWindow via
  Run (forged tail → still judgeable → hard false → omission event
  fires) plus a budget-side honest-bit pin covering the no-op and
  idempotency paths (M67, M68 — M68 detected by both packages'
  pins independently).
- **LOW (both lenses): the r5 comment stated the one-directional
  position guarantee as bidirectional.** FIXED alongside: reportEcho's
  and StripMarker's docs now state exactly what position does and does
  not prove, and that callers must gate on ClipInfo's bit.
- **LOW (skeptic): "unexported ⇒ safe" for maxReviewRounds holds
  because Run has one caller today.** Already deferred-named in r5's
  ledger (options-struct threading when a second caller arrives); no
  re-file, framing note accepted.
- Skeptic verified the five tickets-shape branches mutually exclusive
  and exhaustive; QA verified the accum grammar correctly out of
  StripMarker's scope and writeLog atomicity. No other findings.

Mutations M67–M68 DETECTED. Full suite green. r7 follows on this diff.

## Director tranche — adversarial r7 (2026-08-22, sonnet-medium fallback ×2: skeptic + qa)

Round on the r6 fix layer. QA's verdict: **no HIGH or MEDIUM in the
fix itself** — first clean lens of the tranche (ClipInfo's bit traced
1:1 with Clip's truncate branch across all six input shapes). The
Skeptic's HIGH was an instrument gap, and it was TRUE:

- **HIGH (skeptic, verified by running the mutation BEFORE fixing):
  the ClipInfo wiring at compileReport had no e2e pin on the
  genuinely-clipped side** — reverting `windows[i], clipped[i] =
  …ClipInfo(…)` to plain `Clip` passed the ENTIRE suite (every echo
  pin hard-codes wasClipped; the one >4000-rune Run test used
  sub-5-char filler, insensitive either way). FIXED:
  TestRunClippedWindowStripsMarkerEndToEnd drives a >4000-rune result
  with 4 in-window distinctive terms through Run — nil when the real
  marker strips, judged at 7 terms if the wiring drops the bit. M69
  (the exact wiring revert) went NOT DETECTED → DETECTED across the
  fix.
- **LOW (qa): the honesty bit's third real-cut shape (tighter
  re-clip) was unpinned.** FIXED: TestClipInfoTighterReclip asserts
  the bit flips back to true and StripMarker removes only the new
  true-end marker.
- **LOW (qa, accepted-named): r6 legitimizes marker vocabulary as
  ordinary padding in the FALSE-POSITIVE direction** — an unclipped
  forged tail contributes truncated/characters as generic terms that
  could nudge a window toward a spurious echoed=true. Not unique to
  marker shape (any two common words pad the same way); echoMinTerms/
  stopword tuning is a separately-owned heuristic. Named here so the
  r6 entry doesn't read as strictly positive.
- **LOW (skeptic): ledger asymmetry** ("pinned end-to-end" was true
  only of the forged-tail side). Resolved by the new pin; this entry
  is the correction.

Mutation M69 DETECTED (post-fix). Full suite green. r8 follows.

## Director tranche — adversarial r8 (2026-08-22, sonnet-medium fallback ×2: skeptic + qa)

Round on the r7 fix layer (tests+docs only). **Zero HIGHs — first
clean round of the tranche.** Both lenses independently confirmed the
r7 e2e pin is a real detector for the M69 wiring regression. The
shared MEDIUM was a ledger-honesty catch:

- **MED (both lenses): TestClipInfoTighterReclip's docstring (and
  Clip's own, and Python's) described structurally unreachable
  behavior** — "the old marker nests inside the new payload." A
  genuine tighter cut lands strictly BEFORE the old marker
  (pass-through owns every limit ≥ markerStart), so the old marker's
  text is wholly discarded; only the new marker's count carries its
  weight. FIXED: test re-scoped with real assertions (new marker
  counts "first 200 of", old "first 4000 of" gone; StripMarker leaves
  pure payload), Clip's docstring corrected, Python's wording
  (src/context_budget.py) flagged as a backport-correction candidate
  (M70: dropping the markerStart guard is DETECTED).
- **LOW (qa): dead tautological assertion in the r7 test.** FIXED:
  replaced by the count assertions above.
- **LOW (qa): the pass-through zone (limit at or past markerStart)
  had zero honesty-bit coverage.** FIXED: asserted cut=false in the
  same pin.
- **LOW (both, arithmetic): the r7 pin's "7 terms" is 6** (digits
  4000/4239 sit under the 5-char term floor; the r6 pin's 7 stands —
  its 999999999 is 9 chars). FIXED in the test comment; this entry
  corrects the r7 ledger text.

Mutation M70 DETECTED. Full suite green. r9 follows on this
tests+docs diff — a second clean round declares fixpoint.

## Director tranche — adversarial r9 (2026-08-22, sonnet-medium fallback ×2: skeptic + qa) — FIXPOINT DECLARED

Round on the r8 fix layer (docs+tests only). **Zero HIGH, zero MEDIUM
from both lenses — second consecutive clean round; the tranche's
fixpoint condition is satisfied.** Both lenses independently
re-derived the r8 claims rather than trusting them: the
structurally-unreachable-nesting proof, the tighter-reclip
assertions' arithmetic (markerStart=4000 vs limits 200/4020, the wide
case exercising the markerStart guard rather than the trivial early
return), and the 6-vs-7 term distinction between the two marker pins.

- **LOW (qa, pre-existing, accepted-named): Clip's second guard
  (`len(r)-markerStart <= markerMax`) is provably dead** — markerRe's
  own bounds cap a match at 55 runes, under markerMax=64, so the
  condition never rejects; the "two guards" comment implies both are
  load-bearing when one is redundant defense. Harmless; recorded, not
  fixed at fixpoint.

**Tranche summary (r1–r9):** 5 rounds carried HIGHs, every one living
in the previous round's fix layer (flagship pattern, runs 18–22);
r7's HIGH was an instrument gap verified by running the regression
before fixing; r8–r9 clean. Mutations M37–M70 all DETECTED (two
initially NOT DETECTED — M53, M69 — each converted by writing the pin
the failure demanded). The marker sub-arc (r3 grammar → r4 line
anchor → r5 end anchor → r6 provenance bit → r7 wiring pin → r8
honest docs) is the tranche's teaching arc: position and shape are
never provenance; only the producer's own bit is.

---

## Self-improvement slice 1 (inspector/evolver/guard) — commit 2bba7c09

### Round 1 (4 lenses: Skeptic, Architect, Minimalist, Expert QA — Large; sonnet-medium SAME-MODEL FALLBACK, codex capped til 08-27)

**Verdict: CONTESTED → fixed.** Two HIGHs, both VERIFIED and both a
faithful-port trap (the fork-point behaves the same, but the ported
lesson was the thing to carry, not the line). All four lenses
independently raised the exfil-allowlist bypass — the round's consensus
top finding.

**Verification Ledger (every HIGH + the acted MEDIUM/LOWs):**

- **VERIFIED HIGH — exfil-URL allowlist was a raw string prefix
  (`urlHostAllowed`, guard.go).** Live-reproduced: `https://r.jina.ai.evil-collector.com/exfil`
  and `https://api.anthropic.com.evil.io/steal` both scanned CLEAN
  (probe test, both `IsClean=true risk=low`), and the Python twin
  `scan_content` returned clean too. Reachable through `evolver.Apply`
  (scans `source="internal"` → `SafeToAutoApply` collapses to `IsClean`)
  → auto-applies a lookalike-exfil suggestion at conf≥0.8. **Fixed:**
  host-boundary match (exact host or `.`-suffix subdomain), scheme
  stripped, host clipped at `/?#:`. Pins: `TestURLExfilLookalikeDomainFlagged`
  (3 evasion shapes, all must be HIGH). M85 DETECTED. Python carries the
  identical hole → backport-correction candidate (PORT.md).

- **VERIFIED HIGH — guidance-only `new_guardrail` stamped `applied=true`
  with zero durable effect (`applyAction`, store.go).** Python's stamp
  was honest because pattern-less/invalid guardrail prose landed in
  `playbook.md` (the injected director surface); the Go slice does NOT
  port playbook, so the guidance-only path left NO lesson, NO constraint
  row, NO playbook entry — yet marked applied and fired `EVOLVER_APPLIED`.
  The old test `...IsGuidanceOnly` asserted `IsApplied==true` — a pin
  encoding the lie. **Fixed:** `applyAction` now returns a tri-state
  `actionOutcome`; guidance-only → `actionGuidanceOnly` → HELD (visible,
  retryable), no false event. Pin rewritten as `TestApplyGuardrailWithoutPatternIsHeld`
  (held + reason + no EVOLVER_APPLIED). M86 DETECTED.

- **VERIFIED MEDIUM (QA #1) — `new_guardrail` append had no dedup, so a
  retry after a partial apply (constraint row landed, `applied` stamp
  write failed) double-wrote the row — a record that lies.** **Fixed:**
  `constraintRowExists` idempotency by `source==id` (a hardening
  divergence — Python lacks it). Pin: `TestApplyGuardrailIsIdempotentOnRetry`.
  M87 DETECTED.

- **VERIFIED MEDIUM (Architect #4) — malformed `goal_achieved` (string
  `"false"`, a number) slipped past the fair-caps as *unjudged* and,
  with an adapter present, read `good`.** Confirmed the escape by trace.
  **Fixed:** `goalAchieved` treats a present-but-non-bool value as
  judged-NOT-achieved (safe direction for a quality gate) — divergence
  from Python's `is False`, backport candidate. Pin:
  `TestInspectSessionMalformedGoalAchievedCapsAtFair` (4 malformed shapes).
  M88 DETECTED.

- **VERIFIED LOW (QA #4) — inspector-authored `inspection_finding` rows
  share the store, so `maro evolve -apply <id>` on one hit the `default`
  arm → `action_failed`, contradicting the "unreachable" comment.**
  **Fixed:** explicit `inspection_finding` HELD case ("informational —
  nothing to apply"). Pin: `TestApplyInspectionFindingHeld`. M89 DETECTED.

- **VERIFIED LOW (instrument, QA #3) — the tool-call-injection detector
  class had no fixture ("found 0" unproven).** **Fixed:** added
  `TestScanToolCallInjectionPatterns` (all four patterns fire). M90
  DETECTED.

- **REFUTED / accepted-named (Skeptic #5) — the widened guardrail-revert
  source match is a strict superset of Python's, anchored to the same
  `suggestionID`; cannot spuriously remove another suggestion's row.**
  Confirmed by reading both branches; no action (already the intended
  fix, M82-pinned).

- **OUT-OF-SCOPE / backlog (Architect #6) — an RE2-valid guardrail
  pattern can still be a backtracking ReDoS in Python's `re` matcher
  (`constraint.py`).** Cross-runtime hardening owned by the Python side;
  not this slice's code. Noted, not fixed.

- **OUT-OF-SCOPE (Architect #5) — no cross-language pattern-list parity
  pin.** A Go unit test can't import the Python module; deferred (the
  lists are small verbatim ports, low drift risk).

All fix-layer mutations M85–M90 DETECTED with COMPILING mutants. Full
suite green, gofmt/vet clean. r2 (fix-layer re-review) follows.

### Round 2 (fix-layer re-review: Skeptic + Expert QA — sonnet-medium fallback; commit 0ac098d6)

**Verdict: CONTESTED → fixed.** The flagship pattern held: both HIGHs
lived inside r1's own `urlHostAllowed` fix. Verify-before-fix
live-reproduced every acted finding, and a Python cross-check reshaped
the guard fix toward true parity rather than the reviewers' first
proposal.

**Verification Ledger:**

- **VERIFIED HIGH (QA #1) — userinfo authority bypass.**
  `https://r.jina.ai:tok@evil-collector.com/leak` scanned CLEAN: the host
  parser cut at the first `:`, reading `r.jina.ai` (exact allowlist match)
  instead of the real host `evil-collector.com` (after the last `@`).
  Live-reproduced. Python shares the hole (also clean) → backport
  candidate. **Fixed:** authority parsed RFC-3986-correctly (cut path at
  `/?#`, strip userinfo at the last `@`, then strip `:port`).

- **VERIFIED HIGH (Skeptic #1) — nested-URL laundering.**
  `https://r.jina.ai/https://evil-collector.com/leak` scanned CLEAN in
  Go: one greedy `FindAllString` match, host read as the outer
  `r.jina.ai` → allowlisted → whole payload skipped. Python cross-check
  was decisive: Python's `re.search` FLAGS this (it independently matches
  the inner host), while keeping the legit `r.jina.ai/https://x.com/page`
  proxy pattern clean (inner `x.com` host too short for the shape).
  **Fixed toward Python parity:** the URL scan now tests EVERY scheme
  occurrence independently (`schemeRe` + anchored `exfilURLShape`), so an
  allowlisted outer host never launders a non-allowlisted inner one, and
  the legit proxy shape stays clean.

- **VERIFIED MEDIUM (Skeptic #2) — cost_optimization/crystallization
  mislabeled action_failed.** Confirmed both are KNOWN held categories in
  Python (`evolver_store.py`, `pending_human_review`); Go's r1
  inspection_finding fix was narrow and left these two falling to
  `action_failed`. **Fixed:** explicit `held_for_review` cases. Pin
  `TestApplyKnownPythonCategoriesHeld` (M94, M95).

- **VERIFIED MEDIUM (QA #2) — sibling reader `evolver.triState` not
  hardened.** The r1 fix hardened `inspector.goalAchieved` for a
  malformed `goal_achieved` but left its twin `evolver.triState` treating
  a non-bool as unjudged — so the proposer never saw a failure signal the
  quality gate would cap at fair. **Fixed:** `triState` now matches
  (non-bool → judged-false). Pin
  `TestBuildOutcomesSummaryMalformedGoalAchievedIsFailure` (M93).

- **VERIFIED MEDIUM (Skeptic #3 / QA #3) — Revert overwrote honest
  status.** `changeLogAppend` writes an audit row before the outcome is
  known, and `Revert` didn't check `applied`, so reverting a held/failed
  suggestion stamped `status="reverted"` over the honest
  `held_for_review`/`action_failed`. r1's tri-state fix made held a real
  outcome, so this became reachable. **Fixed:** `Revert` guards on
  `IsApplied` and refuses honestly. Pin `TestRevertUnappliedRefuses`
  (M96). Python parity gap → backport candidate.

- **REFUTED direction (Skeptic #5) — the r1 `.`-suffix subdomain
  widening.** Python's lookahead FLAGS a subdomain of an allowlisted apex
  (`data.api.anthropic.com`); r1's suffix-accept diverged and was
  gratuitous (exact-match alone closes the lookalike). **Corrected to
  exact-match**, realigning the accept set with Python; pinned as a
  flagged case in `TestURLExfilAuthorityBypassesFlagged`.

- **ACCEPTED-NAMED LOW (Skeptic #4 / QA #4) — constraintRowExists is
  presence-only.** It intentionally does not check pattern-equality or the
  30-day TTL; the narrow re-apply-of-an-expired-id edge requires `applied`
  false for weeks. Comment tightened to name the scope; not coupling
  evolver to constraint.py's TTL.

Fix-layer mutations M91–M96 all DETECTED with COMPILING mutants. Full
suite green, gofmt/vet clean. r3 (confirmation round) follows — needs two
consecutive zero-HIGH rounds for fixpoint.

### Round 3 (fix-layer re-review: Skeptic + Expert QA — sonnet-medium fallback; commit 1ec44926)

**Verdict: CONTESTED → fixed.** Flagship pattern a THIRD time: the top
HIGH lived in r2's own `cost_optimization`/`crystallization` fix (wrong
status literal), plus a detector-evasion HIGH in the rewritten guard.

**Verification Ledger:**

- **VERIFIED HIGH (both lenses) — wrong status literal breaks the shared-
  store contract.** r2 stamped `cost_optimization`/`crystallization`
  `held_for_review`, but Python (`evolver_store.py:857,864`) stamps them
  `pending_human_review` — and `observe.py:272` computes the operator
  "pending" dashboard count over the SHARED store keying on exactly
  `pending_human_review`, so Go-touched rows silently vanished from that
  metric. My own r2 comment named the correct value while the code wrote
  the wrong one. **Fixed:** both → `pending_human_review`; test asserts
  the literal + a category-specific block_reason. M97 DETECTED.

- **VERIFIED HIGH (Skeptic) — tab/newline-in-host detector evasion.**
  `https://evil<TAB>collector.com/leak` scanned CLEAN: the candidate clip
  and the shape's `[^\s]` class both stop at the tab, so the host reads
  `evil` and the shape fails — yet a WHATWG URL parser fetches
  `evilcollector.com`. Live-reproduced. **Fixed:** each candidate now has
  ASCII tab/CR/LF removed (the WHATWG normalization step) before shape-
  testing; the shape's `.tld/path` requirement means gluing a line-end
  host to the next line can't forge a match. Pins for tab + newline
  shapes. M98 DETECTED. Python shares it → backport candidate.

- **VERIFIED MEDIUM (both lenses) — third `goal_achieved` reader
  unhardened.** `recall.go:357` read the field with a raw `.(bool)`,
  treating a malformed value as unjudged — diverging from the r1/r2
  hardening of `inspector.goalAchieved` and `evolver.triState`, so a
  corrupt prior attempt showed the director a neutral no-verdict instead
  of a failure. **Fixed:** same conservative direction (non-bool →
  judged-false). Pin
  `TestFindPriorAttemptsMalformedGoalAchievedIsJudgedFalse`. M100
  DETECTED.

- **VERIFIED MEDIUM (QA) — Revert returned Reverted:true even when the
  durable store write failed.** The behavioral undo could succeed while
  `LockedRMW` to persist `applied=false` failed; the code appended a
  detail note but still returned `Reverted:true`, so a caller was told it
  completed and (with the r2 IsApplied guard) a second Revert could slip
  through. **Fixed:** `Reverted` now reflects the persisted state
  (`storePersisted`). Double-revert-after-success pin
  `TestRevertTwiceRefusesSecond`; the merr-persistence-failure path is
  fixed but its fault-injection test is deferred (no clean in-process
  hook).

- **REFUTED (QA #6) — trailing-dot FQDN false-positive.** The claimed
  over-block (`https://r.jina.ai./leak` flagged) CANNOT occur: the exfil
  shape requires `.(com|io|net)/` with the slash immediately after the
  TLD, so a trailing-dot host never matches the shape and never reaches
  `urlHostAllowed`. The r3 `TrimSuffix` "fix" was unreachable dead code
  (M101 NOT DETECTED proved it) — removed, along with the misleading
  fixture. Verify-before-fix caught a hallucinated finding.

- **ACCEPTED-NAMED LOW (Skeptic #4) — constraintRowExists vs Revert match
  asymmetry.** Intentional (idempotency only needs to recognize the row
  this apply just wrote, always source==id); cross-ref comment added.

- **ACCEPTED-NAMED LOW (QA #5) — URL findings report first match only.**
  `break` after the first non-allowlisted URL is Python-parity (the URL
  is one pattern, fires once); risk is already HIGH regardless. Left as
  is.

Fix-layer mutations M97/M98/M100 DETECTED with compiling mutants; M101
void (refuted finding, dead code removed). Full suite green, gofmt/vet
clean. r4 (confirmation) follows.

### Round 4 (fix-layer re-review: Skeptic + Expert QA — sonnet-medium fallback; commit e7039829)

**Verdict: CONTESTED → fixed.** Flagship a FOURTH time: the HIGH lived in
the r2/r3 authority parser — a different terminator character (`\`)
reopened the userinfo-confusion class. Notably this one made Go diverge
WORSE than Python, so the fix restored parity rather than out-hardening.

**Verification Ledger:**

- **VERIFIED HIGH (Skeptic) — backslash authority-terminator bypass.**
  `https://evil.com\@api.anthropic.com/leak` scanned CLEAN in Go: the
  parser split authority only on `/?#`, so it read `evil.com\` as userinfo
  and `api.anthropic.com` as the host (exact allowlist match). A WHATWG
  client terminates the authority at `\` (special scheme), fetching
  `evil.com`. Live-reproduced. **Python FLAGS it** (its prefix-check reads
  the real host right after the scheme) — so Go had diverged worse.
  **Fixed:** `\` added to the authority-terminator set, restoring parity.
  Pin added to `TestURLExfilAuthorityBypassesFlagged`. M102 DETECTED.

- **VERIFIED MEDIUM (QA) — algorithmic DoS in the per-scheme scan.** A
  no-whitespace blob of `https://` repeated (~6250 schemes in 50K runes)
  made each candidate the full remaining tail → O(schemes × tail) work,
  worsened by r3's per-candidate `Replace`. **Fixed:** candidates bounded
  to `urlCandidateMax=512` before any scan work (the exfil shape never
  needs more). Probe confirms the 50K blob now scans instantly. Perf cap
  (no functional mutation).

- **VERIFIED MEDIUM (both lenses) — EVOLVER_REVERTED event fired with a
  misleading type when the revert didn't persist.** The r3 fix corrected
  the `Reverted` bool but left the captain's-log event unconditional, so a
  type-keyed consumer would count a non-persisted revert as done.
  **Fixed:** the event context now carries `persisted: storePersisted` so
  type+context consumers see the truth.

- **ACCEPTED-NAMED MEDIUM (Skeptic #2 / QA) — Revert ordering residual.**
  The behavioral constraint removal happens before the applied-flag flip,
  so a persist failure leaves "constraint gone, applied=true". This is
  Python-parity and requires a rare cross-file write failure AFTER a
  successful constraint-file write; a true fix needs cross-file
  transactions the store doesn't have. Named as a residual + backport
  candidate; the `detail` and `Reverted=false` already flag the state.

- **CONFIRMED CLEAN (both lenses re-verified) — the r3 pending_human_
  review literal (byte-for-byte Python parity, observe.py-keyed) and the
  three hardened goal_achieved readers (no unhardened FOURTH reader in
  production; record.go stores the raw value without judging).**

- **DOC (QA #4/#5) — backport-candidate list expanded.** The malformed-
  goal_achieved divergence exists at four Python sites (inspector.py,
  evolver.py, metrics.py, recall.py), and Python silently marks
  `inspection_finding` applied=true (no arm) where Go holds it. Both now
  named in PORT.md.

- **ACCEPTED-NAMED LOW (QA #6) — added the bare-`\r` fixture and a cross-
  newline negative control to the guard tests.**

Fix-layer mutations M102/M103 DETECTED with compiling mutants. Full suite
green, gofmt/vet clean. r5 (confirmation) follows — r4 carried a HIGH, so
the two-consecutive-clean-round fixpoint clock has not started.

### Round 5 (CONFIRMATION round: Skeptic + Expert QA — sonnet-medium fallback; commit b095c9e3)

**Verdict: NO BLOCKERS FOUND (zero HIGH) — first clean round.** Both
lenses independently traced the full WHATWG authority-shape checklist
(multiple `@`, `@`/`\` orderings, port-only, empty host, mixed-case
scheme, percent-encoding, IPv6 brackets, control chars, the 512-cap) and
marked the parser **VERIFIED sound** — explicitly declining to manufacture
a bypass. The Revert ordering residual was independently confirmed as
honest Python-parity, not papered over.

**Non-HIGH findings, addressed:**

- **MEDIUM (Skeptic #1, shared) — scheme-slash leniency.** WHATWG special
  schemes tolerate 0-1 slashes, so `https:evil.com/x` and `https:/evil.com/x`
  are fetched as `https://evil.com/x` — but `schemeRe`/`exfilURLShape`
  required `://`, missing the slash-light forms. Live-reproduced (both
  scanned clean). **Fixed:** scheme now tolerates 0-2 slashes across
  `schemeRe`, `exfilURLShape`, and `urlHostAllowed`'s scheme strip. A
  regression this introduced (the slash-optional shape let `[^\s]` absorb
  the slashes, false-positiving the legit inner `x.com`) was caught by the
  probe and fixed by forbidding a slash as the host's first char
  (`[^/\s][^\s]{2,49}`), which forces the scheme to consume its slashes.
  Fixtures added; M104 (slash-leniency) + M105 (host-first-char regression
  guard) DETECTED. Named backport candidate #10.

- **LOW (both lenses) — `urlCandidateMax` doc said "512 runes" but the
  bound is bytes.** Byte-bounding is intentional (a `[]rune` copy of a huge
  candidate would defeat the DoS cap); comments corrected to say bytes.
  Not exploitable (both lenses confirmed the host sits in the first ~54
  bytes).

- **ACCEPTED-NAMED (both lenses re-confirmed) — the Revert
  constraint-before-persist ordering residual is honestly framed and true
  Python-parity; a fault-injection sad-path test for it remains the one
  named test gap (deferred — no clean in-process LockedRMW failure hook).**

Fix-layer mutations M104/M105 DETECTED with compiling mutants. Full suite
green, gofmt/vet clean. This is the first zero-HIGH round; r6 (second
confirmation) must also be zero-HIGH to reach the two-consecutive-clean
fixpoint.

## Round 6 (2026-08-22, skeptic + qa, SAME-MODEL FALLBACK: sonnet-medium — codex capped til 08-27)

### Verdict: REJECT → FIXED (one HIGH, verified + closed). Verification Ledger:

- **HIGH (both lenses, convergent) — 3+ leading slashes bypass the exfil
  scanner; r5's `/{0,2}` cap is a parity regression.** VERIFIED live:
  `ScanContent("... https:///evil-collector.com/collect-here ...", "internal")`
  → `IsClean=true, risk=low, findings=[]` (bypass), while 0/1/2-slash forms
  all flag and Python flags the 3-slash form (`injection_guard.scan_content`
  → risk=high). WHATWG's "special authority ignore slashes state" consumes
  an UNBOUNDED run of `/` and `\`, so a real client fetches all these to
  `evil-collector.com`; r5's fixed cap silently un-matched 3+ and made Go
  WEAKER than Python (whose `[^\s]+` after `://` absorbs the extra slashes).
  Root cause was in r5's own fix layer — the flagship pattern, 5th round
  running. Fixed by widening to `[/\\]*` in `exfilURLShape` (host-first-char
  → `[^/\\\s]`) and dropping the `n < 2` bound in `urlHostAllowed`'s strip
  loop; both sites kept in sync. Live re-probe: all 3+-slash / mix forms now
  flag, all allowlisted forms (incl. slash-heavy `https:///api.anthropic.com`)
  stay clean. Mutation M106 (revert to `/{0,2}`) COMPILES and FAILS the new
  pin on `https:///evil-collector.com/...` — the pin is load-bearing, not
  vacuous. Backport candidate #10 reworded (heavy forms were never a Python
  gap).

- **CONFIRMED CLEAN (both lenses) — the r2–r4 authority parser**
  (last-`@` userinfo split, `/\?#`+`\` terminator, tab/CR/LF strip, nested
  per-scheme-position scan) re-derived against WHATWG independently; no
  bypass found outside the slash-count flaw above.

- **CONFIRMED HONEST (both lenses) — `Revert` ordering residual and the
  fail-closed `guard.ScanContent(...,"internal")` gate in `Apply`.** Named
  accurately in PORT.md; not new. The fault-injection sad-path test for the
  Revert residual remains the one named test gap (deferred).

- **CONFIRMED CONSISTENT (both lenses) — the three `goal_achieved` tri-state
  readers** (evolver/inspector/recall) all judge non-bool as false, each
  pinned with a malformed-value seed; no fourth reader exists.

Fix-layer mutation M106 DETECTED with a compiling mutant. Full suite green,
gofmt/vet clean. r6's fix RESETS the fixpoint clock — r7 is now the first of
two required consecutive zero-HIGH rounds.
