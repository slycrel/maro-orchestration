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
