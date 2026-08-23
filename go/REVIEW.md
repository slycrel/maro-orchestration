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

## Round 7 (2026-08-22, skeptic + qa, SAME-MODEL FALLBACK: sonnet-medium)

### Verdict: REJECT → FIXED (one HIGH + one LOW, both closed). Verification Ledger:

- **HIGH (skeptic; QA missed it) — cap-starvation: the 512-byte candidate
  cap was applied from the scheme position, so a long ignorable prefix
  starves the host out of the window.** VERIFIED live: `https:` + 600×`/` +
  `evil-collector.com/leak-secret-data` → `IsClean=true, risk=low` (bypass);
  same for 600 tabs (control-strip also ran after the cap). Root cause is
  r6's unbounded slash run interacting with r4's fixed cap — the fix-layer
  pattern, mutated from grammar to window. Verify-before-fix also REFUTED
  the skeptic's "Go-only regression" claim: Python ALSO scans both clean
  (`injection_guard.scan_content` → risk=low) because its `{3,50}` host
  bound is the same blind spot — a SHARED fork-point gap (backport #11), and
  Go's unbounded `[/\\]*` lets Go out-harden Python. FIXED by skipping
  scheme + the WHATWG ignore-slashes/control run BEFORE the cap (cap now
  bounds the authority window); O(run) prefix scan, one run per scheme, so
  total work stays linear in the 50k-capped content. Live re-probe: all pad
  variants now flag; padded-allowlisted + nested-proxy stay clean; the r3
  in-host-tab case still flags. Mutation M107 (cap-from-scheme) COMPILES and
  FAILS the new pin — load-bearing.

- **LOW (qa) — the Revert store-write-failure honesty contract had no
  test.** Real gap (already accepted-named across r3–r6). CLOSED this round:
  the r3/r4 fix guarantees Reverted=false + "NOT updated" detail +
  applied-stays-true when the suggestion-store write fails after the
  behavioral revert, but nothing exercised it. Added a clean in-process
  fault hook — pre-create the suggestions `.tmp` path as a directory so
  LockedRMW's `os.WriteFile` fails EISDIR on the store write ONLY (the
  constraint file uses its own sibling `.tmp`, so the behavioral half still
  lands). Pins Reverted=false / Behavioral=true / applied=true / retry
  completes. Mutation M108 (Reverted:true unconditional) COMPILES and FAILS
  it. The "needs cross-file txn" residual (ordering, Python parity) stands,
  but its honesty contract is now test-covered.

- **CONFIRMED CLEAN (both lenses) — r6's `[/\\]*` slash-run widening.** QA
  reconstructed the M106 mutant AND a partial-fix mutant (widen the regex but
  leave `urlHostAllowed`'s loop at `n < 2`) and showed both directions of the
  r6 pins are load-bearing, not tautological; the two sites stay in sync.

Fix-layer mutations M107/M108 DETECTED with compiling mutants. Full suite
green, gofmt/vet clean. r7's HIGH fix RESETS the fixpoint clock — r8 is the
first of two required consecutive zero-HIGH rounds.

## Round 8 (2026-08-22, skeptic + qa, SAME-MODEL FALLBACK: sonnet-medium)

### Verdict: REJECT → FIXED (two HIGHs, both closed). Verification Ledger:

- **HIGH (skeptic) — mid-host tab/CR/LF pad past the 512 cap.** VERIFIED
  live: `https://evil` + 600 tabs (or CR, or LF) + `collector.com/leak-
  secret-data` → `IsClean=true` (the r7 leading-run skip stops at `evil`, so
  the cap truncates the tabs+TLD before the per-candidate strip runs). WHATWG
  strips tab/CR/LF whole-string, so a real client fetches evilcollector.com —
  the r3 tab-in-host class at a scale past the cap. FIXED by stripping
  tab/CR/LF ONCE globally over a URL-only copy before per-scheme slicing
  (skip loop simplified to slashes). Mutation M110 (revert to per-candidate
  strip) COMPILES and FAILS the mid-host pin.

- **HIGH (qa) — outer 50k `scanMaxChars` content clip starvation.** VERIFIED
  live: `https:` + 60k slashes (or tabs) + `evil-collector.com/leak` →
  `IsClean=true` (clipped to 50k of pure padding before the URL loop runs).
  Same starvation class at the outer boundary; shared fork-point gap (Python's
  `content[:max_chars]` identical). FIXED by running the URL scan over the
  FULL control-stripped `content`, not the 50k-clipped keyword target — adds
  no DoS class (keyword clip already does `[]rune(content)` = O(content); RE2
  linear; inner skip+512 cap bounds per-candidate work). Mutation M109
  (content->target) COMPILES and FAILS the outer-clip pin, isolated (mid-host
  pin still passes under it).

- **MEDIUM (qa) — r7 pins didn't isolate \r/\n at cap scale.** Real
  coverage gap. CLOSED: added \r-only and \n-only 600-repeat pads to the
  cap-starvation mustFlag set.

- **CONFIRMED CLEAN (both lenses) — r7 cap pins load-bearing, Revert
  fault-injection test asserts-the-flow (not the object), the three
  goal_achieved readers byte-aligned with genuine malformed-value pins, and
  urlHostAllowed's slash-strip in lockstep with exfilURLShape's `[/\\]*`.**

Fix-layer mutations M109/M110 DETECTED with compiling mutants, each isolated
to its own pin. Full suite green, gofmt/vet clean. r8's HIGHs RESET the
fixpoint clock — r9 is the first of two required consecutive zero-HIGH
rounds. STANDING NOTE: 7 straight rounds each found a real defect in this
hand-rolled URL parser; if convergence doesn't come by r9/r10, a
spec-grounded parse (net/url + WHATWG normalization) is the candidate end
state over continued regex hardening.

## Round 9 (2026-08-22, skeptic + qa, SAME-MODEL FALLBACK: sonnet-medium)

### Verdict: REJECT → FIXED (one HIGH; parser itself traced CLEAN). Verification Ledger:

- **HIGH (skeptic) — quadratic-time DoS from `strings.ToLower(cand)` on the
  full unbounded suffix, before the 512 cap.** VERIFIED live: `https:`×N with
  no whitespace → N scheme matches, each ToLower over O(suffix) → O(n²).
  Measured 1.7s / 6.5s / 25.5s at 120k / 240k / 480k bytes (4× per doubling).
  Reachable from untrusted evolver suggestion text via `guard.ScanContent` in
  `Apply`. Directly FALSIFIES r8's "no new DoS class" note — the cost sat
  BEFORE the cap, which r8's own reasoning overlooked; r8's switch to
  full-content scanning removed the 50k bound that had masked it. Go-only
  (Python has no per-candidate ToLower). FIXED: scheme length now comes from
  schemeRe's match bounds (`loc[1]-loc[0]`, O(1)); 480KB case 25.5s->0.21s,
  linear. Pinned with a goroutine+10s-ceiling test (fails fast on quadratic).
  Mutation M111 (restore ToLower) COMPILES and FAILS at the ceiling.

- **NO PARSER HIGH (qa, confirmed).** QA replayed M109/M110 and re-derived
  urlHostAllowed against every mustFlag/mustPass case — the r8 global-strip
  and full-content fixes are load-bearing and the parser traces clean. First
  round the URL/authority parser itself yielded no HIGH.

- **MEDIUM (qa) — no negative control for a mid-host tab/CR/LF pad on an
  ALLOWLISTED host** (the exact mechanism r8 introduced). CLOSED: added
  allowlisted mid-host-pad mustPass cases to both cap-scale and outer-clip
  tests.

- **LOW/MEDIUM (qa) — non-string/absent `suggestion` coerced to "" before the
  guard, stamping a spurious empty applied lesson.** Real fail-open in the
  wrong direction, but SHARED with Python (`d.get("suggestion","")`) and NOT
  an injection bypass (empty string is both scanned and stored). NAMED as
  backport candidate #12, not fixed mid-convergence.

- **LOW (qa) — no absolute byte ceiling on content. DECLINED with reasoning.**
  With the quadratic closed the work is linear; a finite ceiling would
  re-introduce the starvable-prefix class the r8 QA finding was about, at a
  higher threshold. Verify-before-fix cuts both ways — the proposed fix
  contradicts the saga's own lesson. The pre-existing unconditional
  `[]rune(content)` alloc is the caller's size concern, not an r9 regression.

Fix-layer mutation M111 DETECTED with a compiling mutant. Full suite green,
gofmt/vet clean. r9's HIGH RESETS the fixpoint clock — r10 is the first of two
required consecutive zero-HIGH rounds. The 8-round real-defect streak in this
hand-rolled parser stands; spec-grounded-rewrite decision is live if r10
doesn't converge.

## Round 10 (2026-08-22, skeptic + qa, SAME-MODEL FALLBACK: sonnet-medium)

### Verdict: NO BLOCKERS FOUND — ZERO HIGH (first clean round). Verification Ledger:

No HIGH or MEDIUM. Both lenses independently traced the URL/authority parser,
the r9 O(1) scheme-length skip, the linearity, and the evolver honesty surface
and found the class sound — the FIRST round the parser itself yielded no HIGH
on both lenses (r9's lone HIGH was scaffolding, now fixed). LOW notes:

- **LOW (skeptic) — IPv6-bracket port-strip mis-parse, trailing-dot FQDN
  false-positive, NaN confidence.** All confirmed NON-issues for the current
  allowlist/inputs: no IPv6 allowlist entry (refusal-direction only), the
  trailing-dot form is unreachable via the exfil shape (r3-confirmed), and
  encoding/json REJECTS `NaN` (verified: "invalid character 'N'") so a
  NaN-confidence row can't arise from the JSONL store — and if it did, the
  `< 0.8` gate is false for NaN so it never auto-applies (safe direction).
  Noted, not fixed.

- **LOW (qa) — coverage gaps.** (1) No `http:` (5-char) fixture exercised the
  r9 `loc[1]-loc[0]` skip for the shorter scheme — CLOSED: added `http://` and
  `http:///` must-flag cases (verified both flag). (2) The #12 fail-open had no
  regression pin — CLOSED with a KNOWN-GAP visibility pin: verify-before-fix
  confirmed a non-string suggestion applies AND writes an empty-text
  (`"lesson":""`) medium lesson to disk (QA's characterization was accurate;
  my transient "0 lessons" reading was a snapshot-Texts filtering artifact).
  (3) linear pin is a wall-clock ceiling not a scaling-ratio — NOTED, kept as-is
  (deterministic and M111-verified; a ratio pin risks CI flakiness). (4)
  mid-host allowlisted negative controls don't independently prove the strip
  mechanism (the paired positive fixtures carry that) — NOTED, not a gap.

Test-only hardening added this round (http: fixtures + #12 known-gap pin); no
production code changed, so r10 stands as a genuine zero-HIGH round. Full suite
green, gofmt/vet clean. This is the FIRST of the two consecutive clean rounds —
r11 must also be zero-HIGH to reach the fixpoint.

---

## r11 review (2026-08-22, skeptic+qa)

### Verdict: NO BLOCKERS FOUND — ZERO HIGH (SAME-MODEL FALLBACK: sonnet-medium). Verification Ledger:

Second confirmation pass over the r10 test-only tree. Both lenses re-traced the
full parser (fresh-bypass sweep, DoS linearity, Python parity, evolver honesty)
and found no HIGH/MEDIUM. Three LOWs logged and held, all verify-confirmed as
non-blocking: (a) `strings.ToLower` vs pure-ASCII allowlist — flagged as
defense-in-depth, skeptic could not construct a live collision; (b) URL scan
loop `break` after first flagged URL — observability only (`hasExfil` forces
high regardless); (c) the r10 `http:` fixtures never combine with padding.
r10+r11 provisionally reached the two-consecutive-clean fixpoint.

---

## r12 review (2026-08-22, skeptic+qa) — TIER ESCALATION

### Verdict: REJECT — TWO HIGH (SAME-MODEL FALLBACK: opus-5 medium). Verification Ledger:

Jeremy escalated the reviewer tier (opus-5 medium) instead of continuing
sonnet-medium fallback. The higher tier found two real HIGHs in one round —
both verify-before-fix CONFIRMED live, both fixed and mutation-verified this
round; the fixpoint count resets.

- **HIGH #1 — VERIFIED — exfil-shape window starvation (userinfo/subdomain).**
  Trace: `ScanContent("…https://r.jina.ai:tok"+23×"A"+"@evil-collector.com/leak…","internal").IsClean == true`
  and the 60-char-subdomain variant likewise clean, while the short-userinfo pin
  correctly flags. Root cause: shape `{2,49}` host-span < the real authority
  once userinfo/subdomain padding is added, and the `||` short-circuit skips
  `urlHostAllowed`. Fix: host-span max → urlCandidateMax (512), min kept at {2}.
  Mutation M113 (revert to {2,49}) fails the new pins. Reachable on the literal
  evolver-suggestion path (source `"internal"`, `SafeToAutoApply` true). Shared
  with Python ({3,50}) — candidate #11 extended. Residual >512-userinfo pinned
  as `TestURLExfilUserinfoWindowKnownGap` (closing needs unbounded authority
  scan = the O(n²) the cap prevents).

- **HIGH #2 — VERIFIED (bypass) / severity-contested — Unicode case-fold.**
  Trace: `strings.ToLower("İ")=="i"` (true) and
  `ScanContent("…https://api.anthropİc.com/…","internal").IsClean == true`.
  Detector bypass confirmed. Real-world reach contested BETWEEN the two opus
  lenses: skeptic HIGH; qa observed IDNA disallows U+0130 so no client resolves
  `api.anthropİc.com` (bypass without a working channel). Fixed regardless
  (fail-toward-flag is cheap and correct): `asciiLower` byte-wise fold at host +
  scheme-prefix in urlHostAllowed and in sourceIsAllowed. Mutation M114 (revert
  to strings.ToLower) fails the U+0130 pin. Ranked below HIGH #1 on reach.

- **MEDIUM — VERIFIED — `new_guardrail` `pattern` field unscanned.** Confirmed:
  guard scans `d["suggestion"]` only (store.go:637); `pattern` (store.go:490)
  is RE2-compile-validated but not guard-scanned, and Python executes it as a
  regex — RE2 accepts ReDoS shapes Python's backtracking engine does not.
  Default-HELD (opt-in auto-apply) → MEDIUM not HIGH. HELD this round; guard
  package-doc corrected to name the scan scope; `pattern` length-bound + ReDoS-
  shape rejection is candidate #13 (a scoped follow-up).

- **LOWs** (b break, c http:-padding, non-ASCII whitespace not terminators):
  all confirmed non-blocking/safe-direction, held.

Full suite green (22 packages), gofmt/vet clean. Fix commit is scoped to the
guard parser (guard.go + guard_test.go pins). **The fixpoint clock RESETS — r13
is the first of two new consecutive clean rounds, to run at the escalated tier.**

---

## r13 review (2026-08-22, skeptic+qa) — TIER ESCALATION continued

### Verdict: REJECT — ONE HIGH (severity split, adjudicated) (SAME-MODEL FALLBACK: opus-5 medium). Verification Ledger:

The two opus lenses agreed on the FACTS, split on severity: skeptic HIGH #1
(payload-anchor gaps), qa MEDIUM #4 (same class, framed as heuristic reach).
Adjudicated by coverage: fix within-claimed-coverage, document inherent limits.

- **HIGH — VERIFIED — exfil-shape payload-anchor gaps (within claimed
  coverage).** Trace (all `IsClean==true` pre-fix, from source `"internal"`):
  `https://evil-collector.com?data=SECRET` (query), `…#data=SECRET` (fragment),
  `https://evil-collector.com./leak…` (trailing-dot FQDN),
  `https://evil-collector%2Ecom/leak…` (%2E host dot),
  `https://r.jina.ai/https%3A%2F%2Fevil-collector.com%2Fleak…` (%3A/%2F nested
  launder past the r2 per-scheme scan). All are non-allowlisted `.com` hosts the
  detector claims to cover. FIX: anchor `/[^\s]{5,}` → `\.?[/?#][^\s]{5,}`;
  whole-string percent-decode of `%2e/%2f/%5c/%3a` in the URL normalizer;
  trailing-dot strip in urlHostAllowed. Mutation M115 (anchor revert) and M116
  (decode removal) each fail the new pins. Reachable on the literal evolver-
  suggestion path.

- **MEDIUM→DOCUMENTED — VERIFIED — heuristic reach limits.** IP-literal host,
  `.xyz`/`.co.uk` TLDs, ≤2-char labels, <5-byte payloads all scan clean —
  Python-parity ({3,50}+(com|io|net)), inherent to a host-allowlist heuristic.
  Documented, not closed: `TestURLExfilHeuristicReachKnownGap` + PORT.md
  candidate #14. This is the honest scope the ledger was over-implying.

- **MEDIUM→CLOSED — VERIFIED — r12 userinfo-window known-gap.** Both lenses
  (skeptic #2, qa #3) refuted the r12 "unclosable without O(n²)" rationale
  correctly: truncated + no in-window authority terminator is O(1)-detectable.
  CLOSED with a fail-closed oversized-authority flag (M117). The r12 vacuous
  negative control (600×A allowlisted apex, "passed" via starvation) removed —
  it flags now, which is correct.

- **Test-honesty (qa #2/#5/#8) — all FIXED:** asciiLower scheme-prefix +
  sourceIsAllowed sites now pinned (uppercase-scheme passes, homoglyph source
  rejected — M118); http:-fixture comment corrected to what it pins; Revert
  missing-file branch gets the honest "not found" detail.

- **MEDIUM (pattern field, candidate #13) — re-CONFIRMED, still HELD.** Both
  lenses re-verified it; default-HELD; unchanged this round.

- **LOWs** (schemeRe `\b` divergence, `(?i)` vs asciiLower asymmetry, space-in-
  path, sub-2-char label): safe-direction, noted, held.

Full suite green (22 packages), gofmt/vet clean. Four new pins mutation-verified
(M115–M118). **Fixpoint clock RESETS — r14 is the first of two new consecutive
clean rounds. Meta: opus found a real HIGH three rounds running; if the anchor/
encoding gaps keep coming, reconsider the spec-grounded parse deferred at r10.**

## r14 review (2026-08-22, skeptic+qa) — TIER ESCALATION, self-regression round

### Verdict: REJECT — TWO HIGH + one disabled-pin (SAME-MODEL FALLBACK: opus-5 medium). Verification Ledger:

**The decisive round: every finding this round lived inside r13's OWN fix.**
r13 closed the anchor/encoding gaps but its three mechanisms each introduced a
new defect. The flagship "each round's HIGH is inside the previous fix" pattern
held — but this time the previous fix was mine, one round old.

- **HIGH — VERIFIED — r13 whole-string %2f/%5c/%3a decode is a false-ALLOW
  regression.** r13 added `%2f→/ %5c→\ %3a→:` to urlNormalizer to catch the
  proxy-nested encoded launder. But WHATWG percent-decodes ONLY inside the host
  component, AFTER literal `/\?#` delimit the authority — never whole-string.
  Decoding `%2f/%5c` whole-string INVENTS an authority terminator: trace (post-
  r13, pre-fix) `https://r.jina.ai%2f@evil-collector.com/leak` normalizes to
  `https://r.jina.ai/@evil-collector.com/leak`, whose authority now terminates
  at the injected `/` → urlHostAllowed reads `r.jina.ai` → ALLOW. A real exfil
  the RAW text flags became clean after "hardening". FIX: revert to `%2e`-only
  decode (a host-internal dot-encode that cannot move the authority boundary).
  Invariant added: normalization must be ADDITIVE — never clear a shape the raw
  text flags. Mutant (re-add %2f/%5c decode) fails the two new `%2f@`/`%5c@`
  pins in TestURLExfilAuthorityBypassesFlagged.

- **HIGH — VERIFIED — r13's own anchor widening was incomplete (missing `\`
  and `:port`).** r13 widened the payload anchor to `\.?[/?#]` but
  urlHostAllowed already terminated the authority on `\` too and stripped a
  `:port`. So the anchor and the host-check were out of lockstep: trace (pre-
  fix, IsClean==true) `https://evil-collector.com\leak-data-here` (backslash
  terminator) and `https://evil-collector.com:8080/leak-data-here` (port before
  path). FIX: anchor → `\.(com|io|net)\.?(:[0-9]{1,5})?[/\\?#][^\s]{5,}`.
  Mutants (drop `\` from the class; drop the `:port` group) each fail on exactly
  the backslash / port pin. Allowlisted `\`- and `:port`-reached controls added
  to mustPass and stay clean.

- **HIGH (safety-pin disabled) — VERIFIED — r13's oversized-authority branch
  silently made the 13-round r9 linearity pin VACUOUS.** The r13 branch flags a
  candidate that is truncated (>512) with no in-window `/\?#` and `break`s the
  scan. The original linearity fixture `strings.Repeat("https:", 200000)` is
  exactly that shape → it now short-circuits after ONE iteration, so the loop
  never runs enough times to expose a quadratic per-iteration cost. Proof: the
  r9 quadratic mutant (`strings.ToLower(cand)` per candidate) PASSED the old
  fixture. FIX: new surviving fixture `strings.Repeat("https:x/", 150000)` —
  every candidate carries an in-window `/` so the oversized branch never fires,
  all ~150k iterations run; the r9 mutant now blows the 10s ceiling (verified,
  FAIL @ 10.01s). The old blob is repinned for its SECURITY behavior as
  TestURLOversizedAuthorityShortCircuits. The false in-code comment ("pins only
  the timing, which is unaffected") corrected.

- **MEDIUM→DOCUMENTED — proxy-nested encoded launder REOPENED as known-gap
  #14.** The shape r13 tried to catch (`https://r.jina.ai/https%3A%2F%2Fevil-
  collector.com/…`) scans CLEAN again after the safe %2e-only revert. This is
  the correct trade: the decode that caught it was the source of the HIGH #1
  regression. Documented in TestURLExfilProxyNestedEncodedKnownGap + PORT.md
  #14; closing it correctly needs the deferred spec-grounded authority parse.

Full suite green (22 packages), build clean. Four compiling mutants verified
(regression decode; anchor-`\`; anchor-`:port`; quadratic-per-candidate), each
failing on its exact intended pin. **Fixpoint clock RESETS AGAIN — r15 is the
first of two new consecutive clean rounds.**

**META — strategic signal, surfaced to Jeremy.** Four consecutive opus rounds
found real HIGHs (r12 ×2, r13 ×1, r14 ×2), and r14's were all self-inflicted by
r13's fixes — the hand-rolled shape-regex + string-substitution approach is now
generating defects as fast as it closes them. Both lenses (again) recommend the
spec-grounded authority parse deferred at r10. The current state is CORRECT and
stronger than r12, but this is a genuine architecture fork, not another patch —
raised to Jeremy as a decision, not decided unilaterally.

## Guard slice — r15 (2026-08-22): the fork decided — spec-grounded parse

Jeremy chose the parser (and issued the seams-strict/internals-free decree —
see PORT.md r15). `internal/guard/urlscan.go` replaces the shape-regex +
hand-rolled authority parser with github.com/nlnwa/whatwg-url v0.6.2 (WPT-
conformant). Policy unchanged; the r10–r14 corpus (all 17 tests) passed the
swapped internals on the FIRST run — the accumulated fixtures did exactly the
acceptance-contract job the review arc built them for. Diffs from r14:

- **Known-gap #15 CLOSED** (encoded proxy-nested launder): bounded
  decode-and-rescan, additive by construction — runs only after the outer
  candidate parses to an allowlisted host with its authority untouched, so
  the r13/r14 whole-string-decode false-ALLOW class is unreachable. Pin
  flipped to TestURLExfilProxyNestedEncodedFlagged (incl. double-encoded +
  negative controls).
- **New reach the regex could never have**: IDNA/UTS-46 host mapping
  (`evil-collector。com`, fullwidth `ｃｏｍ`) — TestURLExfilIDNMappedDotFlagged.
  Python's regex misses these: the Go file is now the backport REFERENCE, not
  a parity port.
- **Unparseable-truncated fail-closed** pinned both directions
  (TestURLUnparseableTruncatedFailsClosed).
- **Perf**: parse costs ~65µs/candidate vs ~1µs for the regex; a provably-
  additive prefilter (urlCandidateNeedsParse — soundness argument in its
  comment; non-ASCII or percent-bearing candidates ALWAYS parse) keeps the
  linearity blob at 0.26s where parse-everything consumed the full 10s
  ceiling on this box.
- Candidate #13 MOOT (its regex is deleted); #14 reach limits STAND (now a
  one-line POLICY decision, no longer a parser project).

Mutations M97–M102 all DETECTED (prefilter-always-skip, nested-decode-off,
stem-floor-0, no-FQDN-trim, unparseable-fail-open, payload-floor-0), file
restored byte-identical after each. Full suite green (22 packages), guard
race-clean. Adversarial re-review of the swap = the next round; fixpoint
clock unchanged (r15 is a rewrite, not a clean round).

## Guard slice — r16 (2026-08-22): adversarial review of the r15 swap → fixed to green

An adversarial skeptic (opus, background fork) reviewed the r15 parser swap.
**Net read: the parser core is sound** — a 400k-case differential fuzz found
zero counterexamples to the prefilter's soundness, and every r10–r14 evasion
re-fired was caught. The findings were all in r15's NEW nested-rescan code, and
the flagship pattern held once more: the fresh bugs were in the fresh code, and
their shared root was the review's structural catch — **`evalURLCandidate` had
two callers (top-level loop + `scanNestedCandidates`) with invariants written
for only the first.** Every finding was executed, not reasoned; I reproduced
HIGH-1 (83s), HIGH-2, and MED-4 before fixing (verify-before-fix).

Fix is STRUCTURAL, not per-symptom: collapsed both callers into ONE entry
point `evalRawCandidate` (all safety properties live there, so they hold for
both paths) + a shared per-scan work budget.

- **HIGH-1 (DoS, O(k³) nested fan-out — 32KB→83s)**: `scanBudget` caps total
  whatwg.Parse calls (4096) and decode bytes (1MB) per ScanContent, fail-closed
  on exhaustion. 32KB now 92ms. Pin TestURLNestedLaunderDoSBounded drives the
  ALLOWLISTED-proxy path the old TestURLScanStaysLinear (`https:x/`, no
  allowlisted host) could never reach — the review's "vacuous pin" catch, now
  covered by BOTH pins.
- **HIGH-2 (truncation false-ALLOW)**: the nested rescan now reads the FULL raw
  payload (not the 512-capped candidate), so an inner authority pushed past the
  cap by encoded padding is resolved — literally the r13 oversized attack
  wrapped in the proxy + %-encoding. Pin TestURLNestedLaunderPastCapFlagged.
- **MED-4 (launder through non-allowlisted/out-of-reach proxy)**: the additive
  rescan runs for EVERY host that clears the direct check, not only allowlisted.
  Pin TestURLLaunderAnyProxyHostFlagged.
- **MED-3 (space-starved payload floor)**: payload measured on the PARSED
  path/query/fragment, not the raw space-delimited tail. Pin
  TestURLNestedPayloadFloorOnParsedComponents.
- **LOW-5 (false invariant prose)**: corrected the "non-ASCII can never collapse
  into the allowlist" comment — the parser applies the same UTS-46 mapping a
  client does, so a host mapping ONTO an allowlisted host is a genuine
  allowlisted fetch (clean, pinned TestURLIDNMappedAllowlistedStaysClean); the
  safety property is "the host we compare is the host a client fetches," and
  both directions are pinned.

Attacks the reviewer confirmed HELD: prefilter soundness (400k differential
fuzz + directed punycode/IPv4-shorthand/IPv6/uppercase probes), tab/CR/LF
splits, userinfo `@` bypasses, control-char hosts, `:443`/trailing-dot/userinfo
on the proxy, single/triple-encoded inner schemes, 220k-case panic fuzz.

Mutations M103–M107 all DETECTED (budget-off, nested-not-full-raw,
launder-allowlist-only, payload-empty, no-progressive-decode), file restored
byte-identical. Full suite green (22 pkgs), guard race-clean. Net line count
dropped vs r15 (two code paths → one). **Re-review of r16 is the next round;
the fixpoint clock stays reset (r16 is a rewrite of the nested path).**

## Guard slice — r17 (2026-08-22): r16 re-review → one HIGH, fixed to green

Opus re-review of r16 (the fixpoint-discipline round on a security gate).
**One real HIGH; everything else HELD under hard effort** — the reviewer's
negative results are as valuable as the finding:
- DoS BOUNDED: 520KB/10MB/3.2MB/8.8MB adversarial inputs all ~1s (r16 budget
  holds; the O(k³)→83s regression is genuinely gone).
- Parse-budget fails CLOSED correctly (drain then direct exfil → flags).
- Prefilter SOUND: a fresh 500k-case differential fuzz, 0 mismatches
  (independent of r15's 400k run).
- Parity: Go policy is a strict superset of Python's weak regex.

**HIGH (r16's own new code, again the flagship pattern — but bounded, not
churn): decode-budget exhaustion failed OPEN.** r16's `decode()` returned the
input unchanged when `decodeBytes` hit 0, which `scanNested` read as clean. A
percent-encoded inner scheme (`https%3A…`) is invisible to schemeRe until
decoded, so an attacker who drains the decode budget with large no-`%` payloads
(r16 charged every payload) and then appends `https://r.jina.ai/https%3A%2F%2F
evil-collector.com%2Fleak-data-here` got a CLEAN verdict on a real exfil
(reproduced: 1.2MB → clean=true; evil alone → high). Fix (r17): (1) a payload
with no `%` left is fully examined — return clean WITHOUT charging the budget
(kills the drain vector); (2) budget exhaustion with `%` still present flags
`scan-budget-exhausted` (fail-closed, matching parse-budget/oversized posture).
Both drain vectors now flag high and stay fast (114ms / 344ms). Pin
TestURLDecodeBudgetFailsClosed; mutation M108 (fail-open revert) DETECTED (M109,
charge-no-`%`, is not independently security-load-bearing — the fail-closed path
catches the drain regardless, correct defense-in-depth). Full suite green (22
pkgs), guard race-clean. Verify-before-fix honored. **This is a bounded new-
surface finding inside r16's budget code, not churn: the parser core and DoS
bound held; r17 closes the last resource-exhaustion seam. A clean r18 re-review
would be fixpoint.**

## Guard slice — r18 (2026-08-22): CLEAN — FIXPOINT

Opus fixpoint-confirmation round on r17 (befbd7f7). **No exploitable defect
found** after hard effort across all five axes — a genuine clean round, not a
rubber-stamp. Verified: r16 DoS bound holds (2–4.2MB adversarial inputs incl.
worstBreadth=4095 proxies + 3MB tail, all ≤1.35s); r17 decode fail-closed holds
(both drain vectors flag); odd-whitespace/control-char hosts (FF/VT/NBSP/NUL/
U+2044) rejected by whatwg.Parse itself = client parity; no-% early return
sound; depth-cap residual is the documented semantic bound (proxy decodes once,
guard catches single+double); prefilter sound; host==client-fetch by
construction; reach-heuristic gaps are the pinned Python-parity limits.

Residual named (design premise, not a code bug, unchanged since r15 and shared
with Python): safety is grounded on "the host a WHATWG client fetches" — a
downstream consumer using a LENIENT non-WHATWG parser could diverge. Inherent to
any host-allowlist guard; the r.jina.ai proxy is WHATWG-grounded.

**FIXPOINT DECLARED (guard slice, injection-guard URL exfil).** Arc: r10–r14
hand-rolled regex CHURN (four opus rounds, each fix minting the next HIGH) →
Jeremy's fork decision + seams-strict/internals-free decree → r15 spec-grounded
parser swap (nlnwa/whatwg-url) → r16 unified evaluator + work budget → r17
decode fail-closed → r18 clean. The design change converted an open-ended defect
class into a bounded surface that closed in three fix rounds. Mutations M85–M108
all detected across the arc. Go is the backport REFERENCE for Python's
injection_guard.py (IDN mapping, userinfo/nested confusion, encoded laundering,
fail-closed budget — none of which the Python regex catches).

## Self-improvement slice 2 — build + mutation battery (2026-08-22): SHIPPED, review pending

Scans/graduation/verify tranche (see PORT.md "Self-improvement slice 2"):
internal/record/verdict.go (§4 trust policy), internal/scans (5 statistical
scanners + impact + V2/V3 VerifyAppliedSuggestions), internal/graduation
(templates as embedded DATA + workspace override; verify_pattern provenance
anchored to the compiled-in copy — backport correction #11: fork-point Python
shells out row-carried patterns from suggestions.jsonl), internal/selfimprove
(Cycle composition in run_evolver order via the new evolver ExtraSuggestions
hook). CLI: evolve -impact/-verify/-verify-apply, new graduate subcommand.

Gate: build/vet/full-suite green, gofmt clean (urlscan.go pre-existing,
guard-slice-owned, untouched). Mutation battery M119–M126, derived from the
FILES per the standing rule:

- M119 pattern-from-row (the Python injection vector restored) — DETECTED
  (security pin + claim/ack lifecycle both fail)
- M120 malformed goal_achieved reads unjudged — DETECTED (trust battery)
- M121 human-applied auto-reverted — property is DOUBLY guarded (case arm +
  re-read-fresh), no single-line mutant can break it, so M121 is a deliberate
  two-line mutant defeating both — DETECTED
- M122 extensions never park — DETECTED
- M123 drift alerts on 1 breach not 3 — SURVIVED → new negative pin
  TestScanQualityDriftSingleBreachStaysQuiet added, mutant now DETECTED
- M124 graduation dedup off — DETECTED
- M125 Cycle drops the scanner hook — DETECTED (composition test)
- M126 verifyCounts ignores trust policy — DETECTED

All mutants reverted; final gate green. NEXT: adversarial review r1
(cross-model, 4 lenses; escalate reviewer tier after round 1 on same-model
fallback per the 2026-08-22 rule).

## Self-improvement slice 2 — r1 (2026-08-22): 2 HIGHs + 6 MEDs fixed

Three serialized lenses (box OOM rule): security skeptic, Python-parity
(executed fixture comparisons), QA/concurrency (goroutine repros + race
detector). Every reported finding was re-verified against the sources before
fixing; all survived — 0% hallucination this round, against the usual 30-50%.

**Security lens:** headline invariant AIRTIGHT (embedded-only verify_pattern
execution probed via override smuggling, unicode/dup-key tricks, path
traversal, crafted state files — all clean; claim/ack machine "the
best-engineered concurrency surface in the slice", 30-iteration two-driver
probe exactly-once). Found: unbounded tailLines (FIXED, 8MB bound + torn-line
drop), applied_manually non-bool → unsafe direction (FIXED, pyTruthy),
empty-repoRoot state wipe (FIXED, gate before state touch; wipe shape is
fork-point-shared but only Go routinely lacks a repoRoot).

**Parity lens (11 executed side-by-side comparisons):** F1 HIGH missing
escalations.jsonl writer (FIXED — ported, full payload, 2000-char bounds);
F2 audience registry (FIXED); F3 string-confidence trust flip (FIXED);
F4 rounding ties (FIXED, half-to-even); F5 None/<nil> prose (FIXED, pyVal);
F6 apostrophe repr defeating cross-runtime dedup (FIXED, pyRepr); F7
order-only divergences (canon sort + graduation tiebreak FIXED; group-
iteration row order accepted, row SET identical); F8 events for unwritten
rows (FIXED, events fire per landed row); F9 double-clip markers (FIXED,
300→200 chain). Templates JSON, all numerics, verdict lifecycle strings,
row shapes, Cycle ordering: verified clean by execution.

**QA lens (all repro'd attempt-0, -race clean):** HIGH double-revert stamp
corruption + false BLOCKING alarm (FIXED: first-writer-wins terminal stamps,
side effects gated on `changed`, typed NothingToRevert); double-propose
(FIXED: in-lock re-check); double side-effect appends skewing calibration
denominators (FIXED: changed-gate); lost extension bump (FIXED: atomic
BumpExtensionOrPark); SaveSuggestions dedup TOCTOU (FIXED: one locked RMW).
Clean: lock discipline (no unlocked workspace writes), torn-tail framing,
crash-recovery probes, test integrity (no tautologies).

New pins: 15 tests (concurrent single-side-effect, first-writer-wins,
atomic bump, escalation row, audience stamps, byte-bound, state
preservation, concurrent propose-once, concurrent save-dedup, truthiness,
repr/val/rounding parity, string-confidence). Fix-layer mutations M127,
M129–M139 all DETECTED (M128 not minted — race-window arm, documented);
M134 lesson: with two IDENTICAL guard expressions in one file, a
pattern-matched mutant can hit the wrong one and report a false survivor —
assert WHICH site the mutation landed on. Backport candidate (12): the
whole concurrency window family is byte-identical in fork-point Python.

Gate: build/vet/full-suite/-race/gofmt green. NEXT: r2 re-review of the
fix layer (flagship pattern says the new HIGHs live in r1's own fixes).

## Self-improvement slice 2 — r2 (2026-08-22): 2 HIGHs (both inside r1's fixes) + 2 MED + 2 LOW, all fixed

Combined skeptic+qa lens over the r1 diff (b4e58544). Flagship pattern held:
HIGH-1 = r1's in-lock dedup silently changed scope from Python's 200-row
window to whole-file → permanent suppression of recurring-class proposals
(executed repro). HIGH-2 = r1's pyTruthy fix split the `applied` read
(candidates truthy, IsApplied strict) → malformed applied:"true" degraded
row became a silent forever-candidate via the NothingToRevert skip arm —
worse than both baselines. MED-1 fifth verdict arm ungated on `changed`;
MED-2 the propose path's whole-file RMW read re-opened the OOM lever the
same commit closed in tailLines; LOW-1 hex-float confidence divergence;
LOW-2 VerifiedAt-only stamp bypassing first-writer-wins.

Fixes: shared windowed `proposedIn` predicate + new `record.LockedTailAppend`
(flock-held bounded-tail check-then-append, framed); pyTruthy unified across
IsApplied/Dismiss/re-apply guards; fifth arm gated; terminal rows refuse all
stamps; hex strings rejected in coerceFloat. 6 new pins. Mutations M140-M141,
M143-M146 DETECTED (M142 not minted, race-window arm like M128; batteries now
assert mutation-site uniqueness — the M134 lesson operationalized).

Gate: build/vet/full-suite/-race (fix packages)/gofmt green. Suite caveat
noted: guard TestURLScanStaysLinear perf budget can trip under -race on this
box (guard-slice-owned, not this tranche). NEXT: r3 — expect convergence
(r2's fixes are narrow: one predicate extraction, one read-path unification,
one primitive with its own pin).

## Self-improvement slice 2 — r3, WHOLE-SURFACE (2026-08-22): no HIGHs; 2 MED + 2 LOW fixed, rule decreed

Scope change (Jeremy, mid-round): reviews now take the entire chunk + all
accumulated fixes, not the latest diff — and the round validated it: the
lead findings were exactly the cross-cutting kind diff-scoping hides.

MED-1: r2's in-lock dedup window keyed to the diagnoses-scan lookback (vs
Python's fixed 200) — suppression returned at -lookback >200 and fully at
<=0; the r2 pins sat at 200, the one coinciding value. Fixed with a shared
proposeDedupWindow constant; pinned at 300 and 0 (M147). MED-2: the 8MB
"OOM lever" rationale covered one suggestions.jsonl path while eight
siblings stay whole-file — resolved as a deliberate surface-wide rule
(bounded tail for tail-N semantics; whole-file for whole-file semantics —
keyed merges/full-history dedup, where bounding silently drops rows),
comments corrected at all three sites. LOW-1: r2's pyTruthy read documented
as a named divergence (fork-point is strict `is True`; strict lets a
malformed row REPLAY its mutation on re-apply — backport candidate #13).
LOW-2: applied_manually display read aligned to Python bool() (M148).

Declared sound by execution: coerceFloat 18-case corpus, LockedTailAppend
edges + unlocked-writer grep (none), refuse-all stamps vs every flow,
audience registry complete. Round shape: HIGHs 2→2→0, MEDs 6→2→2, and r3's
MEDs are arg-edge + doc-rationale class — convergence. NEXT: r4
whole-surface confirmation round; clean/lows-only declares fixpoint.

## Self-improvement slice 2 — r4, whole-surface (2026-08-23): 1 MED + 2 LOW fixed; 10 INFO to the divergence ledger

The MED validated the whole-chunk scope again: `selfimprove.Cycle` ignored
`report.Skipped`, so graduation propose + the verify pass ran on quiet
intervals Python suppresses — a fresh workspace holding an applied-unverified
row was walked to a TERMINAL "unverifiable" park off zero evidence (and the
r2 refuse-all fix made that park permanent). A composition-layer-vs-Python-
control-flow seam no per-package diff could see; reviewer shipped an executed
repro. Fixed with a skip gate between steps 2 and 3 (step 1 stays above it,
Python parity). LOW-1: coerceFloat missed bool and overflow-string Python
float() edges — both flipped §4 trust in the unsafe direction (malformed →
FULL); fixed + 7-case pin. LOW-2: canon target defaulted ""→"general" where
Python's .get default is dead code — a contentKey divergence that would mint
duplicate rows per runtime on a shared store; now verbatim. All three
verified against both runtimes before fixing (0% hallucination, 4th round
running). M149–M152 DETECTED. 10 INFO items recorded in PORT.md as
divergence-ledger entries (Go the tamer side in each; no action).

Reviewer's solid list is the longest yet (embedded-only pattern execution,
template byte-parity, stamp discipline across all five arms, dedup windows,
verify parity walk, audience/escalation/claim-ack). Verdict: lows-only once
the MED lands — this round. NEXT: r5 confirmation; clean/lows-only declares
fixpoint.

## Self-improvement slice 2 — r5 CONFIRMATION (2026-08-23): FIXPOINT

0 HIGH, 0 MED, 1 LOW, INFO otherwise. The LOW: the calibration
low-confidence reason rendered its threshold with %g → "(<6)" where Python
interpolates the raw float → "(<6.0)". Same family as r1's pyRepr apostrophe
and r4's canon target — the reason IS the suggestion text, suggestion is a
third of contentKey, so one shared calibration.jsonl minted a duplicate row
per runtime. Fixed with pyVal + a byte-for-byte prose pin and a
non-default-threshold pin; %g is now absent from all shared prose. M153-M154
DETECTED. All three r4 fixes held under direct attack with executed
cross-runtime evidence (a 36-case coerceFloat corpus agreed label-for-label
except one unicode-digit exotic, now INFO). One INFO named fork-point-shared,
not a Go bug: explicit `-verify -verify-apply` on a quiet workspace can still
park rows off zero data — Python's CLI makes the identical direct call.

ARC CLOSED. Shape 2H -> 2H -> 0H/2M -> 1M/2L -> 1L. The broken set shrank
every round while the confirmed-solid set grew (r5's is the longest of the
arc). Verify-before-fix held at 0% hallucination across all five rounds.
Jeremy's whole-chunk scope directive earned its keep twice: r3's
dedup-window-keyed-to-the-wrong-arg and r4's Cycle/report.Skipped seam were
both invisible to diff-scoped review.

## Skill library slice 3a — r1 (2026-08-23)

1 HIGH, 3 MED, 6 LOW, 5 INFO, against commit 6750c5cd on a pristine
`git archive` extract (the worktree was dirty with slice-3b work).
Verify-before-fix: every claim checked against the code first, 0%
hallucination.

HIGH: `record.LoadsClean`'s duplicate-name walker recursed with no depth
bound, BEFORE the decoder's own maxNestingDepth could apply — one
over-nested line in a shared store took the Go process down with an
unrecoverable `fatal error: stack overflow` on every subsequent run
(reproduced: 1.5M depth errors cleanly, 2M fatals). Python strands the
same line. Rewritten as an iterative walk.

MEDs, all honest-omission class: the load's loss counters reached no
caller (and an unreadable store was indistinguishable from a cold one);
skills rode the decompose prompt LAST where Python puts them FIRST
(planner.py:962) — a silent A/B confound; and `isCleanText` refused a
literal U+FFFD the reader admits, which freezes a live skill against all
future writes and blocks a whole cull.

LOWs fixed: int literals routed through float64 (MaxInt64 → negative
counter, on fields that drive the circuit breaker and A/B); `SaveSkill`
by value hid the computed content_hash from its caller; `OnlyIDs`
nil-vs-empty flipped the retrieval mode; `note(...), tel` relied on
unspecified operand evaluation order; `archived_at` used the wrong
timestamp layout. Two LOWs documented rather than fixed (created_at
acceptance is wider than the comment claimed; the two runtimes' nesting
depth doors differ) — both strand-safe directions.

INFO acted on: the NaN "named divergence" did not exist (Python refuses
at the same door via parse_constant); `ToDict` was dead surface holding a
second copy of the key-order contract, now the single source via
MarshalJSON — which also restored Python's float spelling (`1.0`, not
`1`), the one that matters because `success_rate` is inside
`doctor._dedup_identity`.

Attacked and confirmed solid: the admission predicate field by field (313
crafted rows, 294 agreeing, all 19 diffs being the two documented LOWs),
surrogate escapes (4000-case fuzz, zero verdict differences), the
id-claimed-after-proof rule, save carrying 7 foreign line shapes
byte-for-byte, refusal-before-touch, archive all-or-nothing, concurrency
under flock, and the whole retrieval ladder (348 cross-runtime goal-runs
across three corpora diffing ids, tier, score and telemetry — 0 diffs).

NEXT: r2 over the WHOLE chunk (3a + 3b + these fixes) per the
whole-chunk directive.

## r2 — Go skill library, WHOLE chunk (2026-08-23)

Reviewer: opus, whole-chunk per the standing directive, run against a
pristine `git archive 107ffb06` extract because the worktree was dirty
with slice-3c work. Method was differential EXECUTION in both runtimes,
not reading: a 6,186-line `LoadsClean` corpus, a 13,810-line duplicate-
name fuzz, a 516-row validator/emitted-line corpus, a 1,200-case matcher
corpus, and live cross-runtime store drives over a shared seeded
workspace. Verdict: NOT at fixpoint — one HIGH, five MED, seven LOW.

Every finding was verified against the code before any fix; none were
hallucinated this round, which is itself worth recording against the
usual 30–50% rate — the difference is that this reviewer executed each
claim before writing it down.

HIGH fixed: the TF-IDF tier summed vectors by ranging Go MAPS, so the
same store and the same goal injected a different skill run to run
(2302/698 over 3000 identical calls). Float addition is not associative
and the ranking sort is stable, so an ulp of map-order noise decided
ties. Every injected-outcome counter built on that was recording a coin
flip.

MEDs fixed: `SkillsNeedingEscalation` read the stored flag where Python
recomputes from the rate, returning a DISJOINT set on the same store;
`FrontierSkills` had lost the frontier band, the open-circuit skip and
the sort, and had gained a challenger exclusion Python does not have
(its old test pinned the divergence, so the test was replaced);
a counter bump minted a fresh zeroed record over a stranded row, and
because strandees ride first the reset row won the keyed read in BOTH
runtimes — evidence destroyed by a routine bump; `TrimSpace` admitted
U+001C–U+001F where Python's `strip()` refuses, the unsafe direction;
and the destructive whole-store rewrites were not fsynced where Python's
`atomic_write` is (three copies of the pattern, now one
`record.AtomicWrite`).

LOWs fixed: an empty id skipped instead of refusing a batch; collapsed
duplicates unannounced; nested non-finite numbers admitted where
`allow_nan=False` refuses; the manifest writer alphabetizing and
HTML-escaping on the attribution rail; U+0130 lowercasing. Three
documented instead: `success_rate`'s uncoerced int (the inverse of r1's
float fix), `ensure_ascii` and nested-`imported` key order (values
identical, no consumer), and the Unicode-table version skew.

The reviewer also attacked and confirmed solid: the iterative duplicate-
name walker (0 divergences on 13,810 fuzz lines), the 4300-digit integer
boundary exactly, U+FFFD admission, the announce/unreadable rails,
`RestrictToIDs`, 511/516 byte-identical emitted skill lines, the extras
order, `getInt`'s precision path, all 9 A/B routing combinations,
200,000 rounding cases, a 22-op cross-runtime stats lifecycle with every
value identical, the stats store's strandee half byte-identical between
runtimes, and the lock posture. One hypothesis was falsified in our
favour (`ParseFloat("1e-400")` does not `ErrRange`).

NEXT: r3 over the whole chunk including slice 3c and these fixes. The
reviewer's own recommendation — make the lifecycle decision functions
the focus rather than re-reviewing the JSONL door — stands.

## r3 — skill library, whole chunk (2026-08-23, opus)

Reviewer tier escalated per the standing rule (r1/r2 ran on the
sonnet-medium fallback lane; escalate rather than grind cheap rounds).
Whole-chunk scope per the 2026-08-22 amendment: slices 3a+3b+3c and the
r1/r2 fixes, against a pristine `git archive c8f1c69c` extract, focused
on the lifecycle decision functions as r2 recommended. Every finding was
reproduced by driving both runtimes over the same seeded store and
diffing; none was hallucinated this round.

**Not at fixpoint: 2 HIGH, 3 MED, 4 LOW.** They land exactly where r2
pointed, and two patterns are worth naming because per-function review
cannot see either:

1. **A correct primitive called from the wrong place.** Both HIGHs are
   this. `UpdateSkillUtility` and `RecordVariantOutcome` call
   `SaveSkill` (delete + append at the tail) where Python calls
   `_save_skills(updated_ids=…)` (the ordinal-holding rewrite) — so the
   library's highest-frequency writer breaks the invariant `pool.go`
   states in its own words, and a promotion sweep with a cap promotes a
   DIFFERENT SET in the two runtimes off the same store. And
   `compactnessAdjustedScore` counts BYTES where Python counts code
   points, which is the sort key for the only destructive tier path;
   54 of the live store's 432 skills score differently. Each function
   under the microscope is itself right.
2. **The decision ports faithfully and the ANNOUNCEMENT of it does
   not.** All three MEDs: the captain's-log audience registry was not
   extended for the five skill events, so every Go promotion, demotion
   and circuit trip is invisible to the operator's curated lane; the
   failure reason is filed under `context` instead of the top-level
   `note` key, so it never renders; and `RunIslandCycle` writes no
   `ISLAND_CULLED` entry at all — a retirement with no log line.

Two of the LOWs are this REVIEW.md's own r2 section claiming a class was
closed when a second instance sat in a file the same review had open:
`appendJSONL` is still on plain `json.Marshal` (it was one of two
writers, not one), and `stampOutcomeVerdictLocked` is a fourth
un-fsynced whole-store rewrite that also widens file mode 600→644 and
re-types a foreign row's `cost: 1.0` to `1`. The remaining LOWs: a
variant outcome launders a tampered `content_hash` (falls out of the
H1 fix), and `round3`/`round4` multiply-then-round where Python rounds
the exact double — 202 of 400 three-decimal half-values diverge, though
the reviewer walked all 4.2M EMA-reachable values 22 steps deep and
found 0 divergences on that path, so the reach is stored/imported
utility scores rather than the common one.

Attacked and confirmed solid, all executed: a 300-step randomized
EMA/circuit trace compared at 17 significant digits (0 divergences in
1200 per-skill snapshots); a real cross-process concurrency race
(Python writer + 3 Go goroutines, one store — 29 rows, 0 duplicates, 0
lost, 0 drifted); a whole-library differential over the real 432-skill
store (island assignment 0/432 mismatches; frontier, rewrite and
escalation sets identical; matching identical); 2,000 A/B routings
identical; `FormatSkillsForPrompt` byte-identical; the promotion veto
path; provenance sidecars byte-identical; the manifest bytes under a
hostile payload. A byte-vs-codepoint attack on `skillTokens` was tried
and REFUTED (the tokenizer's class makes every surviving token ASCII).

NEXT: verify each claim against the source, fix, then r4 over the whole
chunk again.

### r3 fixes (2026-08-23)

All nine claims verified against BOTH sources before anything was
touched; all nine confirmed, none hallucinated — r3 had executed each
one, which is the difference from the rounds where the ~30–50% figure
holds.

| # | Fix |
|---|-----|
| H1 | `UpdateSkillUtility` and `RecordVariantOutcome` now save through `SaveSkills(…, updatedIDs)`. `RecordVariantOutcome` also mutates `pool[i]` in place (it was mutating a copy) and returns the rewrite's warnings, which `SaveSkill` had no way to produce. |
| H2 | `compactnessAdjustedScore` counts code points for the description AND each step. |
| M1 | `userSurfacedEvents` gained SKILL_PROMOTED, SKILL_DEMOTED, SKILL_CIRCUIT_OPEN, SKILL_CIRCUIT_CLOSED, ISLAND_CULLED. SKILL_CIRCUIT_HALF_OPEN stays out, matching Python. |
| M2 | New `Recorder.EventNoted` writes `note` as a TOP-LEVEL key; `EventRelated` delegates to it. `LogCircuitTransition` routes the failure reason there. |
| M3 | `RunIslandCycle` takes a `*record.Recorder` and emits one ISLAND_CULLED per island, inside the sorted-island loop so row order is stable. A failed log becomes a warning that NAMES the un-announced retirement. |
| L1 | Kept deliberately: `RecordVariantOutcome` does not recompute `content_hash`, so a tampered hash survives and keeps warning. The asymmetry with `UpdateSkillUtility` is Python's. |
| L2 | `appendJSONL` emits through `pyjson.Ordered`. `pyjson.Value` now renders containers RECURSIVELY rather than delegating to `encoding/json` — every compatibility rule had been stopping one level down, which is exactly where a captain's-log row keeps its payload. |
| L3 | `stampOutcomeVerdictLocked` decodes with `UseNumber`, recovers the row's on-disk key order via a new `orderedKeysOf` token walk, and writes through `AtomicWrite`. |
| L4 | New `pyRound(f, n)` formats to decimal and parses back — the same decimal-correct rounding Python does. `round3`/`round4` delegate. |

**Pins, and their falsifiers.** The whole suite passed unchanged after
the fixes, which means nothing pinned any of them. Eleven pins were
added (`skills/r3fixes_test.go`, `record/verdict_rewrite_test.go`,
`record/audience_census_test.go`) and then a 17-mutant battery reverted
each fix to the shape r3 found. **17 killed, 0 survived**, tree restored
green. Two mutants had to be rewritten mid-battery because the first
attempt was not faithful — `os.WriteFile(path, …, 0o644)` does not
change an EXISTING file's mode, so it failed to reproduce the mode
widening at all, and a `pyjson.Ordered(row, nil)` mutant that left
`rowKeys` unused was killed by the COMPILER rather than by the test. A
mutant killed by a build error proves nothing about the pin.

`record/audience_census_test.go` is the drift tripwire r3 asked for: it
walks `internal/**/*.go` for event types passed to the `Event*` writers
and fails on any type with no decided audience. It found eight
undeclared types on its first run (LOOP_STARTED, LOOP_FINISHED,
CLOSURE_VERDICT, RECALL_PERFORMED, METACOGNITIVE_DECISION, NOW_ANSWERED,
WORKER_DELEGATION_GAP, WORKER_REPORT_OMISSION); all eight were checked
against Python's live 32-entry frozenset and all eight are correctly
"system" there, so no second bug — but they are now declared rather than
defaulted. It carries a vacuity guard requiring the skill emitters to
actually be reached.

Deferred, named: `record.AtomicWrite` uses a fixed `path + ".tmp"` where
Python uses `mkstemp`, so two concurrent UNLOCKED writers to one path
could rename a partial file. Every current caller holds the store lock.
Not touched while `record` was under review; it belongs to whichever
round opens that file next.

### r4 — whole chunk, adversarial (2026-08-23)

Fourth round over the same chunk (`internal/skills`, `internal/record`,
`internal/pyjson`, `internal/pytext`) plus the r3 fixes, per the
standing whole-chunk rule. Method was execution in both runtimes over
the same seeded stores, from a copy of the tree — 90 randomized
cross-runtime lifecycle drives, a 13-row captain's-log comparison
against live Python, provenance sidecars byte-for-byte, `pyRound` at
204,001 values, and a concurrency sweep at 18 lock sites.

**Verdict: 1 HIGH, 2 MED, 6 LOW.** All nine verified against both
sources before anything was touched; **all nine confirmed, none
hallucinated** — r4, like r3, executed each claim rather than reading it.
The ~30–50% hallucination rate applies to rounds that reason from
source; it does not apply to a round that runs the code.

The store surface itself is converged: 90 randomized drives produced
zero divergence in any of the three JSONL stores. Every finding below is
something a store diff cannot see — a missing emitter, a missing sidecar
file, or bytes Python tolerates and Go does not.

| # | Fix |
|---|-----|
| H1 | `pyjson.Ordered` now emits a key named twice in `modeled` ONCE, at its first position. `StampOutcomeVerdict` appends the verdict keys onto the row's on-disk key list, which already names them on any row carrying a prior verdict — so the row grew ~2× per stamp (497 → 886 → 1729 bytes) until Go's own `LoadsClean`, which refuses duplicate names by design, could no longer read what Go had written. Python's `json.loads` silently keeps the last value, which is what made it invisible from that side. Fixed in the renderer, not the call site: a dict cannot hold a key twice, so no caller should be able to ask for it. |
| M2 | `INPUT_MISMATCH` had no Go twin, and `UpdateSkillUtility` could not receive the input it needs. Added `record.ClassifyInputType` (Python's `\w`/`\s` are Unicode; Go's are ASCII, and the URL scan's terminator set decides the URL COUNT the classifier thresholds on) and `skills.logInputMismatch`, wired through a new `stepText` argument. Python's guard is (just opened) ∧ (failed) ∧ (had step text) ∧ (vocabulary disagrees with input). |
| M3 | A Go promotion never wrote the `SKILL.md` workspace overlay Python's loader reads. Added `ExportSkillAsMarkdown` + `Slugify` + `pyPercent0`, called from `MaybeAutoPromoteSkills` after the lock is released. The divergence was permanent by construction: the promotion sweep only considers `provisional` skills, so Python would never create the missing file later. |
| L1 | `VariantOf` — a `*string`, and the one string field missing from the writer's clean-text enumeration — now rides in it. `pyjson.Value` has no `*string` case, so it fell through to `encoding/json`, which launders invalid UTF-8 to U+FFFD and writes the row where Python refuses it. |
| L2 | New `record.NewFileMode`/`NewDirMode`: new files and directories get `0666 &^ umask` / `0777 &^ umask` like a plain `open()`/`mkdir()`, not a hardcoded `0644`/`0755`. Indistinguishable from correct under umask 022 and silently narrowing under umask 002, which is why it survived this box. The umask is read ONCE and cached — Python's read-back briefly sets the process umask to 0, and Go's runtime is threaded, so that window is a real world-writable-file race here where it is not there. |
| L3 | The non-finite telemetry check ranges a SLICE in Python's tuple order instead of a map (3 distinct messages over 200 identical calls), and spells the value through `FloatRepr` so `nan`/`inf` read as Python's `repr` rather than Go's `NaN`/`+Inf`. |
| L4 | A rewrite that empties the pool writes `"\n"`, matching Python's `"\n".join(live) + "\n"`, not `""`. |
| L5 | New `pyjson.FloatRepr` implements CPython's `float.__repr__` threshold (fixed notation while `-4 < decpt <= 16`) instead of Go's shortest-`'g'`. The old comment justified the gap with "every field emitted through here is a rate or a small counter"; `avg_latency_ms` is milliseconds and crosses 1e6 at a ~16-minute average step. Verified at **103,771 values against CPython, 0 mismatches** — denormals, ±0, MaxFloat64, and every power-of-ten boundary from 1e-320 to 1e308. |
| L6 | `AtomicWrite` uses `os.CreateTemp` rather than a fixed `path + ".tmp"`, and sets the mode with `Chmod` after create rather than through `OpenFile` (where the umask would narrow a deliberately-widened ledger on every rewrite). The r3 note deferred this claiming every caller holds the store lock; six do not. |

**Pins, and their falsifiers.** The whole suite passed unchanged after the
fixes were written — again — so nothing pinned any of them. Pins were added
in `record/r4fixes_test.go` and `skills/r4fixes_test.go`, then a 36-mutant
battery reverted each fix to the shape r4 found, derived from the FILES
rather than the diff. **36 killed, 0 survived**, tree restored green.

Four mutants survived the first pass, and all four were worth the round:

- **Two were equivalent under the caller.** `logInputMismatch`'s
  "circuit was already open" and "this outcome succeeded" guards cannot be
  reached through `LogCircuitTransition`, whose `Changed()` check already
  excludes both states. The guards are still the function's contract, so
  they are now pinned by calling it DIRECTLY rather than through the one
  caller that happens never to present that input.
- **One found a false claim in my own code.** `pyPercent0` had been written
  as a decimal-string shift with a hand-rolled half-even rounder, justified
  in its own comment by "0.855 * 100 is 85.49999999999999". That was
  asserted from memory and never measured, and it is FALSE — the product is
  exactly 85.5. The mutant replacing 60 lines with
  `FormatFloat(f*100, 'f', 0, 64)` survived because the two are equivalent.
  Measured before replacing: 201,810 values across [0,1] render identically
  in Go and CPython, 0 mismatches. The simple form shipped, with the
  measured basis and its domain in the comment, and the pin was rewritten
  to test half-to-even against half-away-from-zero — which a re-run
  mutant confirms it now catches.
- **One was a real gap.** Nothing pinned `triggers[:8]`; every fixture had
  two triggers. Now pinned with twelve.

**Cross-runtime differential** (`scratchpad/r4diff/`). Unit pins assert the
shape this runtime produces; only Python can confirm it is the shape Python
produces, and neither M2 nor M3 is visible to a store-level diff because
the stores agree. Both runtimes drive the same seeded skill: the two
captain's-log rows are **identical** (INPUT_MISMATCH included, field for
field), and the promoted `SKILL.md` is **byte-identical across 23 lines**.
The one difference is normalized and NAMED — backport candidate #14, where
Python reads `old_utility` after applying the EMA — and the harness was
itself falsified by perturbing each fix and confirming both sections report
DIFFERS.

Deferred item CLOSED: r3's `AtomicWrite` `mkstemp` note is fixed here (L6),
along with its false premise about caller locking.

Still deferred, named: `record.AppendRawLine` and `record.Locked` do not
create their parent directory where Python's `locked_append` does — the
mission-log caller works around it. It belongs to whichever round opens
`record`'s append path next.

NEXT: r5 over the whole chunk again.

---

## Task queue — build + differential + mutation battery (2026-08-23): SHIPPED, review pending

`task_store.py` → `internal/tasks`, plus `maro task`. Method was
measure-first: CPython's on-disk bytes, file mode, lock name and key order
were captured before a line of Go was written, then the two runtimes were
driven through the same sequence and diffed.

**Cross-runtime differential** (`scratchpad/ts_diff.sh`, `ts_measure.py`,
`internal/tasks/probe_test.go`): **101 lines byte-identical** after
normalising the two volatile fields — `run_id` (uuid4) and the clock —
both normalised by SHAPE, so a wrongly-shaped id or stamp still diffs. It
covers enqueue → claim → complete → fail → status summary → a minimal
task, and asserts the file MODE and the LOCK NAME alongside the bytes.

**The harness was falsified before it was trusted.** Six perturbations of
the Go side, each of which made it report DIFFERS: ensure_ascii on, no
trailing newline, mode 0644, lock name appended, two fields transposed,
`attempt` not incremented. The first attempt at the lock-name perturbation
was killed by the COMPILER (`declared and not used: stem`), which proves
nothing about the pin, so it was rewritten as a compiling mutant and
re-run.

**Mutation battery** (`scratchpad/mut_tasks.py`): 41 mutants derived from
`tasks.go` and `store.go` — every branch, default, ordering and syscall
flag they contain — not from the diff. **0 killed by the compiler**
(checked explicitly, and reported as such if any had been). First pass
30/40; final **38/41**.

Ten survivors, analysed rather than counted:

| Survivor | Verdict |
|---|---|
| temp file outside the target dir | **real gap** — renames fine where /tmp shares a filesystem, EXDEV where it does not. Pinned by pointing `TMPDIR` at a path that does not exist. |
| any read error swallowed | **real gap** — a torn file was pinned, an unreadable one was not. Pinned with mode 0000 (skipped as root). |
| leading dot treated as a suffix | **real divergence, not a missing pin** — see below. |
| lane / source defaults dropped | **real gap** ×2 — Go has no argument defaults, so Python's keywords had to be re-expressed and nothing checked them. |
| artifact_paths appended not merged | **real gap, and a blind test** — see below. |
| origin aliased instead of copied | **real gap** — pinned by mutating the caller's slice after `MakeTask`. |
| summary drops a status-less row | **real gap** — pinned with a foreign row carrying no status. |
| lock never released | **equivalent**, proved: the deferred `Close` releases the flock, and the shared-lock pin demonstrates a later holder gets in. |
| `i >= 0` → `i > 0` in the suffix scan | **equivalent**, proved: `i == 0` is unreachable because the leading-dot loop consumed every dot before it. |
| `List` not sorted | **equivalent**, proved empirically: Go's `filepath.Glob` already returns sorted results. |

**The blind test.** `TestArtifactPathsMergeInPlace` asserted on the object
read back through `pyval.LoadsOrdered` — which collapses duplicate keys
exactly as Python's `json.loads` does. A writer emitting `"log"` twice
therefore read back as one key and looked correct. **A read-back through a
normalising loader cannot detect a writer that duplicates.** That is r4's
H1 lesson arriving from the opposite direction, and the pin now counts
occurrences in the bytes.

**The real divergence.** Extending the lock-name table with dotfile cases
— and asking CPython for each answer rather than asserting one — showed
`lockPath("..json")` returning `..lock` where CPython 3.14.3 returns
`..json.lock`. pathlib's suffix is the last dot AT OR AFTER the first
non-dot character, which is neither `filepath.Ext` nor the older pathlib
rule (dot at index > 0 with something after it). Fixed and pinned over
seven names, every expectation taken from CPython.

**A wrong inference in the port's own parity notes, caught by a pin.** The
note said a duplicate `blocked_by` entry survives a completion "and keeps
the dependent blocked". The first half is true; the second is false —
`claim` gates on each dependency's STATUS, not on the list being empty.
The test asserting un-claimability failed against correct code, which is
how it was caught. Both the note and the code comment now say what was
measured.

**Named divergences, neither hidden nor silently matched:**

- Python sorts the glob only in `list_tasks`; Go's `filepath.Glob` sorts
  everywhere, so `resolveDependents`, `StatusSummary` and
  `RecoverStaleClaims` iterate in sorted order where Python's is
  arbitrary. Only `RecoverStaleClaims` returns its order to a caller.
- `maro task status` sorts its counts. Python emits `{"total": N,
  **counts}` where counts is built from an unsorted glob, so its key order
  is filesystem order and differs run to run — there is no order to be
  faithful to.
- An `fsync` before the rename that Python omits. It changes no observable
  byte; it narrows the crash window.

NEXT: adversarial r1 over `internal/tasks` as a whole chunk.

---

## Adversarial r5 — H1: the dead skill-attribution writer (2026-08-23): FIXED

**The finding.** Both ends of run-verdict skill attribution were live in
the Go port and nothing connected them. `internal/loop` wrote
`source/skills_manifest.jsonl` on every run and stamped the closure
verdict on every run; `RecordSkillInjectionOutcomes` had **zero non-test
callers**. So `injected_runs` sat at 0 forever and the two consumers that
gate on it both failed OPEN:

- `MaybeAutoPromoteSkills` vetoes a promotion only when `InjectedRuns > 0`,
  so every Go promotion was `evidence:"legacy-only"` and the inflated
  legacy counters were never checked. Measured on an identical seed: four
  FAILED verdicts on one injected skill; Python held it at provisional,
  Go promoted it to established. **Two runtimes, one store, opposite
  decisions.**
- `FrontierSkills` gates on `InjectedRuns < minUses`, so it returned an
  empty frontier forever and the A/B variant subsystem got nothing to split.

This is verbatim the dead-`use_count` failure PORT.md already records. The
port had swapped one dead gate for another.

**The fix.** `record.StampOutcomeVerdict` now returns the row it wrote, and
`skills.StampVerdictWithAttribution` composes stamp + attribution as one
call, which is what `internal/loop` invokes. Python gets this structurally
— `stamp_outcome_verdict` ends by calling the attributor — but Go's import
graph forbids it (`skills` imports `record`, so `record` cannot call
`skills`), so the composition moved UP rather than being left to each
caller to remember. "A correct primitive called from the wrong place" is
the failure this port has now made three times, so the prose is backed by a
source-level tripwire in `internal/loop` that fails if the bare primitive
is ever called there again — plus an anti-vacuity arm that fails if the
composed call disappears.

`recordSkillInjectionOutcomesLocked` was extracted from `stats.go` because
one critical section must span marker-check → batch → marker-write, and
`record.Locked` is **not reentrant** (flock is per open file description).

**Mutation battery** (`scratchpad/mut_attr.py`): 20 mutants derived from
`attribution.go`, `stats.go` and `loop.go`. **20/20 killed** after repair —
first pass 15/18 with one survivor and two killed by the compiler, which
proves nothing and so were rewritten to compile.

| Outcome | What it exposed |
|---|---|
| A2 `goal_achieved` bool gate dropped — **survived** | **real gap.** The gate is NOT redundant with the trust gate, which is the non-obvious part: `record.GoalAchieved` deliberately grades a present-but-non-bool verdict as *judged-NOT-achieved* rather than unjudged, so such a row reaches `VerdictTrustFull` and sails past gate 2. Without gate 1 the failed type assertion yields `achieved=false` and the run's skills are credited with a **failure nobody ever judged**. Python is explicit (`if not isinstance(row.get("goal_achieved"), bool): return`). The two hardenings pull opposite ways on purpose: reading a malformed verdict pessimistically is right for a TRUST policy and refusing to read it at all is right for a learning COUNTER. Pinned over six shapes, with an anti-vacuity assertion that each one really does grade FULL. |
| A18 `attributed_at` spelling — **survived once rewritten** | **real gap.** The marker's byte pin stopped one character short of the timestamp, so every wrong spelling of it survived. Python writes `datetime.now(timezone.utc).isoformat()` — an AWARE datetime, so `+00:00` is part of the value, there is no trailing `Z`, and the fractional part is six digits or absent. It is the one field a reader must parse. Now pinned by regex. |
| A1, A15 — killed by the **compiler** | Proved nothing and were reported as such: A1 left `row` unused, A15 named a helper that does not exist. Rewritten to compile (`_ = row`; `DumpsIndent2` for the spelling swap), after which both died to real assertions. |

Everything else — the trust gate, the missing-manifest gate, the marker
idempotence check, the marker write, string-id refusal, malformed and torn
announcements, id de-duplication, corrected-verdict announcement,
unreadable-marker UNKNOWN handling, set-vs-length id comparison, marker key
order, compact-vs-indent spelling, both stats counters, and the loop wiring
— died on the first pass.

NEXT: r5 M1 (the daily markdown log + MEMORY.md index, absent in Go), then
M2, L1–L4; then adversarial r1 over `internal/tasks`.

---

## Adversarial r5 — M1: the two human-readable surfaces (2026-08-23): FIXED

Python's `record_outcome` writes three files; the Go port wrote one. The
daily markdown log and `MEMORY.md` are ported in `internal/record/dailylog.go`
— rationale, the two-clock quirk, and the stricter-reader match are in
PORT.md.

**Differential first.** Both files are shared with Python, so the pins are
byte comparisons against CPython, with ONE case table passed to both sides
rather than transcribed. A hand-kept expectation table drifts silently the
moment Python's format string changes, which is the failure this port keeps
re-learning.

**Mutation battery** (`scratchpad/mut_m1.py`): 36 mutants derived from
`dailylog.go` and `record.go`, not from the diff. First pass 26/35 with six
survivors and three killed by the compiler; **final 33/36**, three
survivors, all proved equivalent.

Real gaps the battery exposed, each now pinned:

| Survivor | Verdict |
|---|---|
| summary cut measured in BYTES | **real gap, and the differential was blind to it.** The 500-rune case does not separate a rune cut from a byte cut — both counts exceed 400 and both implementations trim. The shape that separates them is WIDE BUT SHORT: 250 runes at 500 bytes, where Python leaves the summary alone and a byte test says "too long" and then slices `runes[:400]` out of a 250-rune slice. Pinned with that case. |
| file named from UTC rather than local | **real gap — a time-of-day-blind pin.** The test took its expectation from `time.Now()`, so it agreed with itself no matter which clock the code used. On this box (UTC-6) it would have passed all morning and failed after 18:00. Rewritten to CONSTRUCT a zone that puts local time at 23:59:59 of the previous UTC day, so the two dates differ whenever the suite runs, with a fatal guard if the construction stops crossing the boundary. |
| last-ten window taken BEFORE the schema filter | **real gap — the fixture was too small.** With five ledger rows, dropping the unloadable row left the same four either way. Extended to twelve rows with the unloadable one inside the newest ten, so windowing first yields nine loadable rows and filtering first yields ten. |
| the daily-log failure swallowed | mutant killed by the **compiler**, which proves nothing; rewritten to compile, then killed by the announcement pin. |
| lock file is not the one Python takes | same — the original mutant left a function literal uncalled. Rewritten as a lock on a DIFFERENT path, which is the divergence that actually matters, and killed. |
| blank lesson lines counted | same — left the loop variable unused. Rewritten and killed. |

**Three proved equivalents, with their proofs:**

- **`day` sliced by bytes.** `recordedAt` is ASCII ISO-8601 from `nowISO()`
  and `WriteOutcomeWithLog` is its only caller, so bytes and runes cannot
  disagree. The rune slice stays because the parameter is a string and the
  next caller may not be.
- **Index rendered before the ledger append.** The mutant ADDS an earlier
  render; the authoritative one after the append overwrites it, so nothing
  observable changes. The property it was meant to test is pinned by its
  sibling: removing the LATER render is killed.
- **`recorded_at` re-read from the clock.** Two reads microseconds apart,
  and the heading consumes only `[:10]`. Named residual: a read pair
  straddling midnight UTC would disagree, which is not observable without
  injecting a clock.

**A wrong test premise, caught by the test itself.** The
announced-not-swallowed pin first made the memory dir read-only and expected
both surfaces to fail. Only `MEMORY.md` did: `O_APPEND` on a file that
already exists needs no directory write at all, so the daily log succeeded.
It takes two different levers, and the test now says so.

NEXT: r5 M2 (`pytext.Repr` is not Python's `repr()`), then L1–L4; then
adversarial r1 over `internal/tasks`.

---

## Adversarial r5 — M2: `pytext.Repr` was not `repr()` (2026-08-23): FIXED

Details and the measured Unicode-table skew are in PORT.md. Two things
about how it was verified are worth keeping.

**The fix was falsified before it was trusted.** Reverting `Repr` to the
pre-fix version and re-running the new pins reproduced r5's claim as a
concrete artifact rather than an argument: `triggers: ['first`, a
frontmatter line cut in half, and Python's real `skill_loader` reading one
trigger where its own writer's file yields two. A test that passes on
first run against fixed code proves nothing; this one was made to fail on
purpose first.

**Mutation battery** (`scratchpad/mut_m2.py`): 17 mutants over `Repr` and
`IsPrintable`, run against `pytext`, `skills` and `scans` together so the
two delegating copies are covered. First pass 9/17 with six STALE patterns
(a heredoc doubled the backslashes in the literals — the harness reported
them as stale rather than silently counting them as survivors, which is the
only reason it was caught). **Final 17/17.**

| Survivor | Verdict |
|---|---|
| `\u` not zero-padded | **real gap.** Every non-printable in the case table sat at or above U+1000, so `%x` and `%04x` agreed on all of them. The observable shape is a non-printable in [0x100, 0x1000) — anything smaller takes the `\x` branch. Pinned with U+061C (ARABIC LETTER MARK, Cf) and U+0378 (unassigned, Cn), which need four digits where `%x` yields three. |
| `C` dropped from the printability guard | **dead code, not missing coverage** — and so was dropping `Z`. Two survivors, one cause: the negative guard was unreachable because L∪M∪N∪P∪S is already disjoint from C∪Z. Removed, and the mutants retargeted at the positive expression that remains, where dropping any category dies immediately. |

NEXT: r5 L1–L4, then adversarial r1 over `internal/tasks`.

---

## Adversarial r5 — L1 + L3: the umask window and the decimal fold (2026-08-23): FIXED

Two LOWs, landed together because one mutation battery covers both.

**L1 — the umask read had a window.** `processUmask` used Python's
swap-and-restore (`os.umask(0); os.umask(back)`), narrowed to one occurrence
under a `sync.Once`. Narrowed is not closed: r5 measured ~2.5% leakage under
contention, and Go's runtime is threaded where Python's comment ("multiprocess,
not multithreaded, so the window is acceptable") assumes it is not. A file
created by another goroutine inside that window is world-writable, and mode
bits do not heal.

The kernel publishes the value directly — `/proc/self/status` carries
`Umask:` since Linux 4.7 — so the first attempt is now a READ with no window
at all, and the narrowed swap-and-restore stays as the fallback for a kernel
or container without `/proc`. A malformed line is REFUSED rather than coerced:
the fallback is racy but correct, while a plausible-looking wrong value is
silent until someone else's runtime gets EACCES.

`parseUmaskStatus` is split out of `umaskFromProc` so the refusal cases can be
driven without a writable `/proc` — and deliberately NOT duplicated into the
test. Three copies of a "simple" string helper is exactly what let M2's
`repr()` bug survive this port, and this one decides file modes.

**L3 — Python's `float()` folds every Unicode decimal digit.** CPython runs
the text through `PyUnicode_TransformDecimalAndSpaceToASCII` before parsing,
so `float('٠.٥')` is 0.5. Go's `ParseFloat` is ASCII-only, so a
`goal_verdict_confidence` written in Arabic-Indic digits was unparseable HERE
and readable THERE — and the direction is the unsafe one, because an
unparseable confidence never downgrades a judged verdict, so a below-floor
value read as **FULL** trust.

r5 named "75 divergences". Measured on this box (CPython 3.14.3, unidata
16.0.0): **all 760 Nd code points parse, 750 of them non-ASCII.** The figure
was corrected in the code comment rather than repeated.

The fold recovers a digit's VALUE by walking back to the start of its run and
taking the offset mod 10. That is exact, not approximate, and it was measured
against Go's own table before being trusted: 680 digit code points in 64 runs,
63 of length 10 and one of length 50 (U+1D7CE–U+1D7FF), zero mismatches. The
length-50 run is why the modulo is load-bearing rather than decoration.

**The table skew was closed rather than documented.** Go ships unicode 15.0.0
against CPython's 16.0.0, leaving 80 Nd code points in 7 ranges that CPython
folds and Go does not — the same unsafe direction. Seven range literals
(`digitSupplement`) close it. Unlike `Slugify`'s skew (L4), this one is small,
enumerable, and lands on a trust decision, so embedding it beats documenting
it. Three tests keep it honest: a whole-table sweep that re-derives CPython's
fold map and fails in EITHER direction, a walk-back property test against Go's
own table, and one that fails when Go's table catches up so the literals get
deleted instead of quietly becoming dead code.

**Mutation battery** (`scratchpad/mut_l3.py`): 18 mutants over both files.
First pass 15/18 with one stale pattern; **final 18/18**, one recorded
equivalent.

| Survivor | Verdict |
|---|---|
| `umask` parsed as decimal instead of octal | **real gap, and an ugly one.** The two umasks anyone actually runs — 0002 and 0022 — read as 2 and 22 in both bases only for 0002; the acceptance cases were 0002 and 0, and *both* read identically either way. A base-10 parser passed the whole battery. Pinned with 0022 (18 octal, 22 decimal), 0777, and a refusal case `0089` that is valid decimal and invalid octal. |
| `TrimSpace` weakened to a left-only trim | **real gap.** Every refusal case was long enough to trip the digit-count bound first, so nothing exercised the trailing side. Pinned with `"Umask:\t0022 \n"`. |
| writer guard `r > MaxASCII` → `r >= MaxASCII` | **EQUIVALENT, recorded not chased.** The only rune it newly admits is U+007F (DEL), which is Cc in both tables, so `asciiDigit` refuses it and the mutant falls through to the same `WriteRune`. No input distinguishes them. |

NEXT: r5 L4 (`Slugify`'s skew is 187× its documented size and lands on a
FILENAME) and L2 (no captain's-log rotation in Go), then r6 over the whole
chunk, then adversarial r1 over `internal/tasks`.

---

## Adversarial r5 — L4: the Slugify table skew (2026-08-23): FIXED

r5 said `Slugify`'s named "Unicode table skew" is 187× its documented size
(5,060 against a documented 27) and lands on a FILENAME. Measured here, the
finding is right about the risk and wrong about the arithmetic, in a way
worth writing down: **there are two different skews and only one of them was
ever documented.**

| Skew | Measured | Was documented as |
|---|---|---|
| `pytext.Lower` — runes CPython lowercases and Go does not | **27**, in 5 runs, 0 the other way | "27 further runes" — **correct** |
| `Slugify`'s word class — code points CPython's `\w` keeps and Go's `\p{L}\p{N}` drops | **5,004**, in 27 runs, 0 the other way | not documented at all |

The 27 in the doc comment was accurate for `Lower` and was being read as a
bound on `Slugify`, which composes `Lower` with a word class that skews 185×
harder. Both numbers being 27 (27 runes / 27 runs) is a coincidence and an
unhelpful one.

**Both were closed rather than re-documented.** The consequence is the
reason: this function's own doc says two runtimes disagreeing here is "worse
than not writing it at all", because the same skill name yields two
filenames and the skill lands in two files. 27 map entries and 27 range
literals is a small price for that, and 4,617 of the 5,004 code points are
just two blocks (Egyptian Hieroglyphs Extended-A and CJK Extension I).

**A third copy of the lowercase helper was found and collapsed.**
`skills/coerce.go` had its own hand-rolled `pyLower` carrying its own copy
of the stale "27 further runes, unfixable from here" comment — the same
duplicated-helper family that let M2's `repr()` bug survive in two places
after being fixed in one. It delegates to `pytext.Lower` now.

**`Repr`'s printability skew is deliberately left open**, and the doc now
says why instead of just how big it is: 5,812 code points, it would need
CPython's whole printability set re-derived, and its consequence is a
differently-spelled string that parses back to the same value — against a
wrong trust grade and a split filename for the two that were closed. The
existing sweep still asserts the direction stays one-way.

**Mutation battery** (`scratchpad/mut_l4.py`): 13 mutants over both files,
**13/13 killed on the first pass, 0 stale, 0 equivalent.** Two of them (S1,
S5) restore the pre-fix behaviour exactly, so the batteries are also the
falsification: without the supplements the new pins fail.

Each table gets three pins: a whole-range sweep that re-derives the truth
from CPython and fails in EITHER direction, an end-to-end differential
against Python's real `skill_loader._slugify`, and one that fails when Go's
tables catch up so the literals get deleted rather than quietly rotting.

NEXT: r5 L2 (no captain's-log rotation in Go, and PORT.md's doctrine line
conflates rotation with deletion), then r6 over the whole chunk, then
adversarial r1 over `internal/tasks`.

---

## Adversarial r5 — L2: captain's-log rotation (2026-08-23): FIXED

This runtime appended to `captains_log.jsonl` forever and never rotated it,
while sharing the file with a Python runtime that does. **The doc was the
reason it went unnoticed.** PORT.md offered "`record` has no
delete/rotate/compact verbs at all" as the Go shape of the append-only
invariant, which conflates two different things: the invariant is NEVER
AUTO-DELETE, and rotation deletes nothing — every entry moves to a
timestamped archive beside the active file and stays readable. Python's own
docstring says so outright. A doctrine line that reads as a virtue is a bad
place to hide a gap; the line is corrected.

What it costs to skip: `load_log` JSON-parses the whole active file per call
and sits on the dispatch recall hot path, so an unbounded active file makes
every recall slower forever — and a Go writer that never rotates hands that
cost to the Python reader. The store is the interop contract, and part of
the contract is the file's SIZE.

Ported faithfully: size-gated on the append with no scheduler,
`captains_log.rotate_mb` (default 5, 0 disables) and `rotate_keep` (default
1000), the same re-entrancy guard, the same `captains_log.<stamp>.jsonl`
archive naming with its same-second collision suffix, and the LOG_ROTATED
audit row. Plus `ArchivePaths`/`AllLogPaths` for the archaeology readers.

**Three deliberate improvements over Python**, each named in the code: both
rewrites go through `AtomicWrite` (Python's `write_text` lets an unlocked
reader — and `load_log` takes no lock — observe the file mid-truncation);
the collision search is bounded so a pathological directory warns instead of
spinning; and `ArchivePaths` drops directories matching the pattern, which
this port's own test managed to create three of.

**The differential caught me being wrong about Python.** I read `LOG_ROTATED`
out of a list at captains_log.py:380 and stamped the audit row
`audience: "user"`. That list is `EVENT_TYPES`, not `USER_SURFACED_EVENTS`;
the live frozenset says `system`. Two things caught it and neither was
review: the audience-census tripwire refused an emitted type it had never
been told about, and the differential compares against the row **Python's own
`_maybe_rotate` writes** rather than an f-string I reconstructed from the
source. A hand-built expectation would have agreed with the mistake.

Every other field — subject, summary prose, `archived`/`retained`/`archive`
— matched on the first run. **Not "byte for byte", as this section
originally claimed** (adversarial r6, LOW): the differential decodes both
rows and re-encodes them with `json.Marshal`, so it compares VALUES, and it
is structurally blind to the one byte-level divergence known to exist here
— `pyjson.Value` sorts nested map keys where Python emits dict insertion
order. That divergence is an accepted port-wide named one, so the test is
right to compare values; the write-up was wrong to describe what it proved
as stronger than it is.

**Mutation battery** (`scratchpad/mut_l2.py`): 20 mutants, four rounds.

| Round | Result |
|---|---|
| r1 | **baseline RED** — a targeted `-run` filter had hidden the audience-census failure. A battery against a red baseline proves nothing; fixed and re-run. |
| r2 | 17/20, three survivors |
| r3 | **baseline RED again** — the new collision test was clock-flaky (occupying a second's worth of names can straddle a second boundary, after which every name is free and the test passes for the wrong reason). Fixed with a frozen-clock seam. |
| r4 | 18/19, one survivor |
| r5 | **19/19 killed, 0 stale, 1 recorded equivalent.** |

| Survivor | Verdict |
|---|---|
| retention guard `keep >= len(lines)` → `keep > len(lines)` | **EQUIVALENT, recorded not chased.** They differ only at `keep == len(lines)`, where the mutant proceeds to `head = lines[:0]` and the `len(head) == 0` guard two lines below returns without touching anything. |
| empty tail written as a bare `"\n"` | **real gap.** The differential compared row LISTS, and Python writes an EMPTY active file for a retention of zero where the mutant writes one newline — two different files that a line-list comparison cannot tell apart. Now compared as bytes, with each side's own audit row removed. |
| blank-line filter `pytext.Strip` → `strings.TrimSpace` | **real gap.** No fixture contained a line that is nothing but U+001F, which Python's `str.strip()` drops and Go's `TrimSpace` keeps. Added to the raw-separator differential, which now covers both halves of the splitlines/strip pair. |
| under-lock size re-check removed | **real gap, and it needed a real race.** The test now holds the lock, lets a rotation reach it, shrinks the file underneath and releases — the other-process case the re-check exists for. Its first version used a "small" file that was still over the threshold, so it failed against working code before it could fail against the mutant. |

---

## Adversarial r6 — whole chunk (rotation + the three r5 table fixes)

First round under the whole-chunk rule rather than latest-diff. **0 HIGH,
1 MEDIUM, 4 LOW** — and the MEDIUM is the one a diff-scoped review could not
have raised, because the defect was in code r5 had already landed and
declared green.

### MEDIUM — Final_Sigma: `pytext.Lower` split a filename

`_slugify('ΟΔΟΣ')` gives CPython `odos` and gave this port `odoσ`. Greek
lowercase sigma is CONTEXT-SENSITIVE — U+03A3 becomes ς word-finally and σ
elsewhere — and `Slugify` lowercases before it slugifies, so the divergence
lands straight on a skill's filename: one skill, two files, one per runtime,
on a shared store.

Swept over the whole rune range, **U+03A3 is the only context-sensitive
lowercase mapping there is**, so one rule closes it rather than a table.
Implemented as UAX #29's Final_Sigma over `Cased` / `Case_Ignorable`, both
properties measured from CPython rather than transcribed: `Cased` came out
as exactly Lu ∪ Ll ∪ Lt ∪ Other_Lowercase ∪ Other_Uppercase (4,311) and
`Case_Ignorable` as Mn ∪ Me ∪ Cf ∪ Lm ∪ Sk plus 17 word-break punctuation
code points (2,749).

**Both L4 pins were structurally unable to catch this, and one said so out
loud** while being wrong about it — its comment reads "a single differing
rune is what changes the slug", which is exactly the assumption a context
rule breaks. A whole-range single-rune sweep cannot see a two-rune rule, and
the 18-name slug differential contained no sigma. Both are fixed: the sweep
grew a third arm, and the differential grew eight sigma names.

**The rule inherits the same unicode 15-vs-16 skew as everything else here**
— 96 code points — and this one runs in BOTH directions, which none of the
previous three did:

| Table | Size | Direction |
|---|---|---|
| `casedSupplement` | 52 (5 runs) | CPython says Cased, Go 15.0 does not |
| `caseIgnorableSupplement` | 43 (16 runs) | CPython says Case_Ignorable, Go does not |
| `caseIgnorableExclusion` | 1 | **Go says Case_Ignorable, CPython does not** |

The exclusion is U+1171E, reclassified Mn → Mc in Unicode 16. Go's table is
not merely behind there, it disagrees, and a supplement-only fix cannot
express that. Worth recording as a refinement of the r5 rule of thumb: a
version skew is not always a subset relation.

### LOW — rotation config dropped Python's `float()` / `int()` coercion

Python reads both keys through `float()` and `int()` inside ONE try/except.
Typed `config.Get` reproduced neither half. A QUOTED `rotate_mb: "10"` — what
an operator gets from a templated or env-substituted config — was a float()
there and a type mismatch here, so **Python rotated the shared log at 10 MB
and this runtime at 5**, each treating rows as active that the other had
archived. And the reset is JOINT: an uncoercible `rotate_keep` sends
`rotate_mb` back to its default too, so a 15 KB log does not rotate at all
where reading the keys independently rotates it.

Fixed with `coerceInt` alongside the existing `coerceFloat` — and it is
`int()`, not `float()`-then-truncate, because `int("10.5")` raises where
`float("10.5")` does not, so the easy reading turns a config error into a
silent 10. `config.Load`'s warnings are no longer discarded either. Named
residual: an explicit `rotate_mb: null` reads as absent here and as None
there; reaching it needs a raw-lookup seam in `config` that no caller wants.

### LOW — `LOG_ROTATED` dropped `loop_id`, and so did every other call site

Python's `log_event` fills `loop_id` from the `_current_loop_id` contextvar
whenever the caller passes none — its docstring says outright that this is
how call sites deep in the stack get attributed without threading the id
through every signature. The port had no ambient id at all, so all three Go
call sites that pass `""` were writing unattributed rows. **Broader than the
finding framed it**: it named rotation, but the gap is in `EventNoted`.

Fixed with `Recorder.LoopID` + `WithLoopID`, which COPIES the Recorder
rather than mutating it. That is a deliberate divergence in mechanism:
contextvars are per-task, so Python can hold one global and stay correct
under concurrency, and a mutable field could not. A copy can.

### LOW — the raw-line-separator rationale named the wrong rune

`rotate.go` justified `pytext.SplitLines` with "a Go-written row can carry a
RAW U+2028", and the differential's fixture used U+2028. Measured across
every separator `splitlines()` breaks on: **`pyjson` escapes U+2028 and
U+2029 unconditionally**, so neither can reach the file raw — the fixture
pinned a case neither runtime's writer can produce. The only separator
`pyjson` emits raw is **U+0085 (NEL)**, which is exactly the reachable
hazard. Fixture switched to U+0085, U+2028 kept one row later and labelled
as defence against writers this port does not own.

The same comment's Strip half was also overstated: both encoders escape
U+001C–U+001F, so a line that is only those runes cannot come from either
writer. Kept for parity, relabelled as parity rather than justified with an
argument that does not carry it.

### LOW — a REVIEW.md claim stronger than its test

Corrected in place above: the L2 audit-row differential compares VALUES, not
bytes, and is structurally blind to `pyjson.Value`'s nested-key sorting.

### Mutation batteries

**Final_Sigma** (`scratchpad/mut_sigma.py`): 24 mutants over the rule, both
supplements, the exclusion and the range walker. **24/24 killed, 0 stale.**

Two rounds, and round 1 earned both its corrections:

| Round | Result |
|---|---|
| r1 | 20 killed, **1 survived**, 3 spurious "did not compile" |
| r2 | **24/24 killed** |

| r1 finding | Verdict |
|---|---|
| lookback tests `cased` before `caseIgnorable` | **REAL gap.** A rune can be BOTH — the ~267 modifier letters in Lm ∩ Other_Lowercase — and the rule resolves it by testing ignorability FIRST and continuing the scan. `'ʰΣ'.lower()` is `'ʰσ'`; the mutant says `'ʰς'`. **Every arm of my sweep put a cased `a` in front of the code point**, where both orderings agree, so it could not see the difference. Closed with a third arm that puts NOTHING before the rune. |
| three "did not compile" | **Not real.** My no-compile detector matched the bare substring `"not used"`, which appears in ordinary failure output. A detector that misreads a kill as a non-result is worse than none: it hides exactly the mutants that prove the most. Tightened to the four specific compiler messages. |

The after-arm of that sweep was also **vacuous on arrival**, and nothing said
so: it was spelled `"Σ"+c+"a"`, and a sigma at index 0 has nothing cased
before it, so Final_Sigma is false for every c and the assertion compared
`false == false` 1.1M times while reporting a clean zero. Re-spelled
`"aΣ"+c`, and every arm now fails if it does not see the rule come out both
ways.

**Rotation + the r6 fixes** (`scratchpad/mut_l2.py`): grown from 20 to 31
mutants — the eleven new ones cover the config coercion, the joint reset,
`coerceInt`, the ambient loop id, `WithLoopID`'s copy semantics and the
warning dedupe. **31/31 killed, 0 survived, 0 stale.**

| Round | Result |
|---|---|
| r6 | 25 killed, 0 survived, **2 stale** — R18/R19 pattern-matched the config block this round rewrote |
| r7 | **27/27 killed** |
| r8 | 29 killed, 1 compiler-killed (R29 left `warnings` unused); re-spelled and killed by the new pin → **31/31** |

That round was also **run against a moving file**: I edited `rotate.go`
while the harness had a mutant applied to it. The harness writes
`src0.replace(...)` per mutant and restores `src0` after, so a concurrent
edit is silently clobbered and any mutant in flight is judged against a
half-applied change. R7 was in flight; it came back killed, which is the
right answer, but it was not an answer that run had earned. Edits staged as
a patch, applied after, re-run.

One fix in this round arrived WITHOUT a finding and needed its own pin.
Surfacing `config.Load`'s warnings put them on a path that runs before the
size gate on every append, so raw they would have been a stderr line per
event — a warning that fires constantly is one its reader learns to skip,
which is the same silence the fix was for, reached the other way. `warnOnce`
dedupes; the pin asserts BOTH ends (at least one warning, fewer than one per
append), because a dedupe with no lower bound degrades to "never warn"
without failing anything.

### What the round says about the practice

Three of the five findings are **a doc or comment asserting something the
code does not do** — the same shape as r5's L2, where PORT.md's "no
delete/rotate/compact verbs at all" hid a missing subsystem behind a line
that read as a virtue. Two of those three were written by me in the previous
round. Prose that describes a guarantee is worth re-deriving, not re-reading.

And the MEDIUM is the case for the whole-chunk rule: it was in landed,
reviewed, green code, invisible to any review scoped to the latest diff.

NEXT: r7 over the whole chunk, then adversarial r1 over `internal/tasks`.

## Adversarial r7 — whole chunk

1 HIGH, 2 MEDIUM, 3 LOW. All six verified against the source before any
fix; all six fixed. `1 HIGH` after a round that produced `0 HIGH` is the
whole-chunk rule earning its keep again — the finding was in code r6 had
just landed and called done.

### HIGH — the ambient loop id had no production caller

r6 added `Recorder.WithLoopID` to answer "rows written by call sites that
pass no loop id are unattributable on the shared log". Nothing ever called
it. The mechanism was dead code and its pin could not tell, because the
pin built its own `Recorder` and set the id itself.

Wired into `loop.Run` and `now.Run`. The instructive part is what happened
next: my first pin for it passed, and then **survived deleting the
production line from both files**. The NOW lane already passes an explicit
id at every one of its own emit sites, so there was nothing for the
ambient value to fill. I deleted that pin rather than keep one that was
green for the wrong reason, and replaced it with a delegation-chain pin
over `Event`/`EventRelated`/`EventNoted` — verified by removing the
`if loopID == "" { loopID = r.LoopID }` fallback and watching all three
arms go red.

Then I measured what the scope actually reaches, instead of repeating the
comment r6 had written. Every emitter in `loop.go` and `now.go` passes
`loopID` explicitly; the only reachable call site that passes none is
`LOG_ROTATED`, which fires on the run's own appends. So the live payload
is one row per rotation, and the skills/evolver/graduation emitters the
original comment named are not reached at all — Python gets there via
`loop_finalize.run_evolver`, which is unported. Both comments now say
that, including the sentence that matters: do not read the scope's
presence as evidence those paths are wired.

### MEDIUM — the audience census was blind to a non-literal emitter

`audience_census_test.go` walks sibling packages for event types passed to
the `Event*` writers, matching a **string literal**. `skills.LogCircuitTransition`
selects its type from a map, so all three `SKILL_CIRCUIT_*` types were
declared in the registry and verified by nothing — the census counted them
as covered while never seeing them.

Two mechanisms, because closing the hole and keeping it visible are
different jobs. A second pattern finds `Event*` calls whose first argument
is not a literal; a file holding one is not trusted to the regex, so every
event-type-shaped literal anywhere in it is harvested into the census
instead. Any such file not named in `indirectSites` fails outright, and a
stale `indirectSites` entry that harvests nothing also fails — otherwise
the allowlist becomes the next silent hole. Three mutations run, three
killed: dropping `SKILL_CIRCUIT_HALF_OPEN` from the registry (the blind
spot itself, previously green), removing the allowlist entry, and adding a
stale one.

### MEDIUM — the package doc claimed a verb it has

`record.go`'s header read "There is no delete, rotate, or compact verb
here at all" with `rotate.go` in the same package. The retention doctrine
it was protecting is still true and now says so accurately: nothing
deletes, the head is written to `captains_log.<UTC-stamp>.jsonl` and only
then is the active file rewritten, so every row survives one of the two
files. The same block listed the log's keys and omitted `note` and
`related_ids`.

This is the r5-L2 shape for the third time — a doc whose claim reads as a
virtue while hiding what the code actually does.

### LOW — an explicit null is not an absent key (and the first pin missed it)

`rotate.go` read its two config keys with `config.Get`, which folds
"absent" and "present but null" into the same default. Python does not:
`_cfg_get` hands `None` to `int()`, which raises, which resets **both**
keys jointly. Measured on `{rotate_mb: null, rotate_keep: 50}` — Python
rotates at (5.0, 1000), Go rotated at (5.0, 50), disagreeing about how
much of a shared log stays live. The r6 comment called this unreachable
without a raw-lookup seam; the seam it needed was presence, not rawness.

Fixed with `config.Lookup`, feeding the raw value to the same coercers so
the joint reset falls out rather than being special-cased.

The pin is the lesson. The first version used a 60-row fixture, passed,
and **passed just as green against the unfixed reader** — at 60 rows the
folded path also archives nothing, because `keep=1000` exceeds the row
count and the tail guard returns early. Right answer, wrong reason, and
indistinguishable from a real pass. Above the default retention (1100
rows) the two paths separate and the mutant fails 1-vs-0. A control arm
asserts the same fixture with a coercible retention does rotate, since
"0 archives" is only evidence if 0 is not the fixture's only outcome.

### LOW — two comments naming the wrong character and the wrong set

The rotation test's header and failure message both said a raw **U+2028**
was the hazard. `json.dumps` escapes U+2028/U+2029 unconditionally; the
only `splitlines` separator a writer emits verbatim is **U+0085**, which
is what the fixture actually carries (the U+2028 row is labelled defence
against a future encoder change).

`verdict.go` called `float()`'s whitespace set `Py_UNICODE_ISSPACE`. That
name belongs to `str.isspace()`, which has 29 code points; `float()`
strips 25, and the four extra — U+001C..U+001F — make `float("\x1c1")`
raise. Both counts re-measured this round rather than carried from the
note. The wrong name invited a specific wrong repair: routing the trim
through `pytext.Strip`, which implements `str.strip` correctly at 29 and
would have made `float()` accept four separators CPython rejects. The
comment now says so.

### What the round says about the practice

**Two pins in this round passed for the wrong reason, and neither was
caught by writing them carefully.** Both were caught by mutating the
production code and watching for red. The now-lane pin was green against a
deleted fix; the null-config pin was green against the unfixed reader. A
test written against already-correct code has no signal in it until
something breaks — the mutation IS the test of the test, and skipping it
is how a suite fills up with green that means nothing.

r5, r6 and now r7 have each turned up **a comment asserting something the
code does not do**, and in r6 and r7 the offending comments were written
by me in the immediately preceding round. Confident prose about a
guarantee is the least reliable line in the file.

NEXT: r8 over the whole chunk, then adversarial r1 over `internal/tasks`.

## Adversarial r8 — whole chunk (opus tier)

0 HIGH, 4 MEDIUM, 3 LOW. All seven reproduced independently before any
fix; all seven fixed; every fix then mutation-verified. Reviewer tier was
raised for this round per the standing escalation rule, and it earned it —
three of the seven are pins that could not fail, which is the class this
chunk keeps producing and which the previous rounds had stopped finding.

### MEDIUM — the re-entrancy pin passed with the guard deleted

`TestTheAuditAppendDoesNotReenterRotation` seeded `rotate_keep: 0` and
called that "the sharpest version of both" failure modes. It is the one
retention at which NEITHER can occur: with no retained tail the fresh
active file holds only the ~250-byte audit row, so the re-entered call
returns at the size gate having never consulted the guard.

Verified by deleting the CAS guard: the pin passed. The same mutant
against a `keep: 10` fixture hangs — unbounded recursion, one archive per
row, on the shared `memory/` directory.

Re-fixtured at retention 10 (a ~2.5 KB tail against a 1,048-byte gate),
with retention 0 kept as a second case labelled for what it actually
tests. The pin now fails on the guard-deleted mutant, bounded by its own
30s timeout rather than hanging. It also asserts that the post-rotation
active file still exceeds the gate, so a future fixture edit cannot
silently disarm it the same way again.

### MEDIUM — the guard's comment named a deadlock that cannot happen

The comment claimed the guard also kept `Locked` from deadlocking against
itself. `Locked`'s critical section closes at `rotate.go:239`; the audit
append is at `:244`, outside it. Nothing nests — each re-entry takes and
releases the lock cleanly and then recurses. Python's own comment says
"cascades" and says nothing about locks; the deadlock was invented on this
side. Corrected in both the source and the test, because a maintainer who
goes looking for the lock nesting will not find it and may conclude the
guard is removable — which is exactly how the cascade comes back.

### MEDIUM — the audience census certified events from its own registry

This is a defect in the r7 fix, one round old. `record.go` matched the
non-literal-emitter detector (its `Event`/`EventRelated` delegate inward
to `EventNoted`), so the harvest swept up every event-shaped literal in
that file — including the `userSurfacedEvents` map keys declared there.
Every declared type therefore certified itself as emitted. Renaming all
three real `SKILL_PROMOTED` / `SKILL_DEMOTED` / `ISLAND_CULLED` emitters
left the census green: a tripwire written *because* the registry drifted
silently could no longer see the registry drift.

The accounting now has two kinds. `indirectHarvest` is for files whose
literals ARE the events they emit; `indirectDelegation` is for files whose
non-literal call originates nothing, and whose literals are declarations
rather than emissions. Both mutations now fail: renaming the emitters, and
removing `record.go` from the delegation list (so it is still accounted
for, not silently ignored).

The general lesson is worth keeping. An allowlist added to close a blind
spot is itself a blind spot unless something proves each entry still
earns its place — and "harvest everything in the file" is too coarse a
tool when one of those files is where the registry lives.

### MEDIUM — `rotate_mb: .inf` rotated here and was refused there

Python computes `int(rotate_mb * 1024 * 1024)` inside the try that wraps
the whole rotation, and Python ints are arbitrary precision. So `.inf` and
`.nan` RAISE and abandon rotation, while a finite-but-enormous value
succeeds and simply never fires the gate.

Go's `int64` of any of the three is an unrepresentable conversion: on
amd64 all yield `MinInt64`, so `size < maxBytes` is false and the log
rotates every single time. `.inf` is precisely what an operator writes to
mean "never rotate", and it produced the exact inversion — on a file
Python considers untouched. The result is even architecture-dependent
(arm64 saturates the other way).

Both Python behaviours are now reproduced separately, with a
three-case differential against CPython's own `_maybe_rotate`.

### LOW — three comments and a fixture set that claimed more than they did

`rotate.go` called the audit row "byte-identical to Python's" while
PORT.md honestly records that separators and nested key order differ; the
row's PROSE is byte-identical and the row is not. The audit-row
differential said "everything else must match exactly" while marshalling
both sides through `json.Marshal`, which sorts keys and normalizes
separators — so it is deliberately blind to the two things that actually
differ. Its comment now says what it pins, and a raw-bytes assertion
covers the regression it could not see: a writer that stopped using
`pyjson` entirely would have canonicalized to the same map and stayed
green.

Two `Slugify` fixtures were labelled for tables that did not decide them.
`"ΑΣ𑜞"` and `"ΑΣࢗ"` end after the mark, so the scan runs off the
string and every spelling of the tables yields ς; mutating either table
left the test green. Both now carry a trailing cased rune, and each fails
on exactly the table its label names.

### What the round says about the practice

Escalating the reviewer tier changed what came back. r7 at the lower tier
found real defects but did not find a single un-failable pin; r8 found
three, one of them in r7's own fix. The pattern across r5–r8 is that this
chunk's dangerous defects are not wrong code — they are green tests and
confident comments about code that is fine, which is the one thing a
review scoped to a diff will never surface.

And a fix is not a finding closed. r7's census fix introduced the r8
census defect, and both my r7 pins and r8's three all shared one property:
they were written against code that was already correct, so nothing in
writing them could reveal that they proved nothing. Only mutating the
production code does.

NEXT: r9 over the whole chunk once the playbook module lands, then
adversarial r1 over `internal/tasks`.

## Adversarial r9 — the playbook chunk, whole (opus tier)

Eight findings: **1 HIGH, 4 MEDIUM, 3 LOW.** Every one of them was verified
against the code before a fix was written, per standing practice; none was
hallucinated this round.

The shape of the round is the finding worth keeping. Exactly **one** was
wrong logic. The HIGH and three of the four MEDIUMs are *green tests and
confident comments about correct-looking code*: a branch no fixture reached,
a comment stating a contract the code does not hold, a guard whose fixture
disables the thing it guards, a helper whose test could not fail. That is
the same distribution r7 and r8 produced, and it is now the working
assumption for this port rather than an observation about one round.

### HIGH — an alarm re-read wrote a captain's-log row CPython never writes

Python's alarm-replace branch calls `atomic_write(...)` and then `return`s —
from inside the `with locked_write` block, i.e. from the *function* — so the
trailing `log_event(PLAYBOOK_UPDATED, ...)` is never reached. Go set
`wrote = true`, fell through, and emitted the row.

The playbook *file* was byte-identical, which is why five differentials over
that file never noticed. The captain's log was not. And it is live: the only
production caller (`evolver/store.go`) passes a `playbook_key`, and re-firing
is the entire *point* of an alarm — every second and subsequent reading of
the same check added a row to a shared, rotated, rendered log. The Python
side (`discretion_readout.py`) reasons about exactly this event's emission
discipline, over a log that would have had extra rows in it.

Why nothing caught it: every append fixture in the package passed `key=""`.
The replace branch existed with no pin on *either* side of it. The fix is one
deleted line; the pin is `TestAnAlarmReReadEmitsNoLogRow`, which asserts the
row count **and** that the file shows the replacement — otherwise a Go that
simply failed the second append would pass the row assertion for entirely the
wrong reason.

### MEDIUM — `int(_cfg_get(...))` is not `config.Get[int]`

Python wraps both numeric gates in `int()`. `int()` truncates floats, parses
numeric strings, folds Unicode decimals, and **raises** on `None` — and that
raise is caught by `curate_playbook`'s outer `except Exception`, abandoning
the entire pass. `config.Get[int]` folds all of that into "silently use the
default".

With `alarm_ttl_days: null` in a shared `workspace/config.yml`, Python does
**no curation at all** while Go expired alarms, collapsed duplicates,
archived, and rewrote the document. Which binary ran the dream cycle decided
what learning data survived.

The port now goes through `pyInt`, which returns `ok=false` exactly where
`int()` would raise, and `Curate` abandons the pass there. Eight configs pin
it — the two null cases, a non-integral TTL, a quoted number, an unparseable
string, a negative, an absent key, and a boolean.

### MEDIUM — `Curate` took `ws` as an argument and read its gates from ambient

The package doc states the one deliberate structural difference from Python:
these verbs take the workspace as an argument instead of reading a
module-level path. `Curate` half-did it. The file, the archive dir and the
lock came from `ws`; `curation_enabled`, `alarm_ttl_days` and
`curation_min_chars` came from `config.Load()`, i.e. from `MARO_WORKSPACE`.

Every fixture called `curateWorkspace`, which does `t.Setenv("MARO_WORKSPACE",
ws)` **and** passes the same `ws` — so the two could not disagree inside the
suite. The failure direction is destructive: alarms expired out of a document
whose own `config.yml` said to keep them.

`config.LoadFor(dir)` now exists and carries the rule in its doc: *any verb
that takes a workspace argument must use this, not `Load`.* Python cannot be
compared here — its path and its config are both module-level, so the failure
mode does not exist there. That makes it a Go-only invariant, and it gets a
Go-only pin: two workspaces whose configs disagree in both directions.

### MEDIUM — a comment stating a byte contract the code did not hold

`alarmDate`'s comment claimed that Python's regex matches all 760 Unicode
decimal digits and then strptime rejects the non-ASCII ones, so *"both
runtimes end up keeping it, for different reasons."*

The second half is false, and the measurement is more specific than either
the comment or the earlier commit that repeated it:

| directive | CPython pattern | accepts |
|---|---|---|
| `%Y` | `(?P<Y>\d\d\d\d)` | all **760** decimal digits, folded by value |
| `%m` | `(?P<m>1[0-2]\|0[1-9]\|[1-9])` | ASCII only |
| `%d` | `(?P<d>3[0-1]\|[1-2]\d\|0[1-9]\|[1-9]\| [1-9])` | second digit Unicode-capable **via `[1-2]\d` only** |

So `'٢٠٠١-01-01'` parses to 2001-01-01 and `'2001-01-1٢'` is the 12th, while
`'2001-1٢-01'` and `'2001-01-0٥'` are `ValueError`s. A stamp with a non-ASCII
year was expired by Python and kept forever by Go — one runtime deleting a
line the other keeps restoring.

**Correction to commit `cf1285a9`.** Its message says CPython's *"strptime
accepts all 760 decimal digits for `%Y/%m/%d`"*. That is true for `%Y` and
false for `%m`/`%d`. The measurement it was drawn from probed only the year
position and generalised. The code is right; the recorded claim was not, and
this is the correction — the commit is landed and its message cannot be
edited.

`alarmDate` now transcribes the two sub-patterns and a test re-derives them
from `_strptime.TimeRE()` on the running interpreter, so a CPython change
fails here instead of drifting. The sweep puts all 760 digits through all
four positions: 3,050 stamps, 1,542 parsed, 1,508 refused, zero
disagreements.

### MEDIUM — the compression branch was unreachable, and its guard was a nil-deref away

`curateWorkspace` hardcoded `curation_min_chars` to `1<<30` in *every*
curation test, and every call site passed `a = nil`. So
`if runes(text) > minChars && a != nil` was never true, `compress` never ran,
and the `a != nil` guard was never load-bearing under test — while in
production it is the only thing between `Curate` and a nil-interface
dereference on a documented never-returns-an-error path. Go has no `recover`;
Python has a blanket `except Exception`.

Three mutants survived the shipped suite. The new fixtures reach the branch,
pin the prompt bytes and the request fields, and cover a nil adapter and an
erroring one.

### LOW — a `t.Skipf` that swallowed ten differentials whole

Any non-zero exit from the CPython probe — a renamed helper, a changed
signature, a broken interpreter, or the `/tmp/` safety assert firing — became
`t.Skipf`, not a failure. Ten of twelve differentials, including the
seed-bytes pin the package doc calls *"the only honest pin"*, would report
green while testing nothing.

A **missing** interpreter is still a skip; a **failing** probe is now fatal.
Falsified with a `python3` shim that exits 3: 71 sub-tests fail where all of
them previously passed.

### LOW — the 200-code-point summary clip was unpinned

`clipNoEllipsis` exists solely because Python's `summary=entry_line[:200]`
has no ellipsis while `entry[:500] + "…"`, two lines away, has one. Its
truncating branch had never executed: every append fixture writes an entry
far under 200 code points. A mutant appending an ellipsis survived the suite.

### LOW — `Inject` invented a default budget Python does not have

Go substituted `DefaultInjectMaxChars` for any `max_chars <= 0`. Python
passes the number through. The 137-budget sweep started at 40 and never saw
it; it now starts below zero.

### The battery

19 mutants derived from the files, not the diff. **18 killed, 1 equivalent
with a proof, 0 survivors.**

The equivalent one is worth recording rather than "fixing": widening
`strptimeDay`'s `3[01]` to `3[0-2]` changes no verdict, because the only
extra string it admits is `"32"` and `time.Parse` rejects day 32 in every
month of every year (probed exhaustively). The transcription keeps the tight
bound anyway — the transcription *is* the contract, and a reader who
"simplified" it would have to re-derive that proof.

Two mutants survived the first battery run for the same reason and it is the
reason to keep writing batteries: `math.Trunc` → `math.Round`, and
`int(True)` → 0. Both survived because every alarm in the config table was
stamped **2001** — expired under any TTL those fixtures produce, so the
conversion was invisible. The table proved `int()` *raises* where `Get` would
not, and proved nothing about what `int()` *converts*. The new fixtures put
the alarm on the boundary the conversion moves, derive the stamp from the
clock (a fixed date's age changes every day the test runs), and assert the
fixture **flips** between the right conversion and the wrong one.

### What the round says about the practice

Three things generalise:

1. **A fixture constant chosen to disable a branch is a coverage hole with a
   plausible cover story.** `1<<30` was written to keep compression out of
   the way of the tests that were not about compression. It kept it out of
   the way of the tests that *were*.

2. **A corpus that always starts from the same base document silently never
   exercises what that base cannot produce.** Every config case shared one
   2001-stamped alarm, so half the function under test was unreachable
   through it.

3. **A whole-chunk review sees seams a diff review cannot.** Three of the
   eight findings — the ambient config read, the unreachable compression
   branch, the swallowing `Skipf` — are properties of how the package's
   *fixtures* are built, and are invisible in any single round's diff. This
   is the second round under Jeremy's 2026-08-22 whole-chunk amendment and
   the second time it paid for itself.
