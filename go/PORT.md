# Maro Go Port — exploration branch

Jeremy, 2026-08-21: *"Let's go nuts and pivot for a while. Let's make a
branch and port maro to golang."* (Clarified same session: a
long-considered exploration in its own right — *"Worst case we learn
nothing and waste some tokens."*) The Python runtime at the repo root
**remains production**; this directory is the experiment.

## Doctrine — port the lessons, not just the code

The Python codebase enforces its hard-won invariants with tripwires
(AST scanners, registries, pin tests) because Python can't stop the bug
class at the point of writing. The port's bet is that Go can make some
of them **structural**:

| Python lesson (how it's enforced there) | Go shape here |
|---|---|
| Caps are data-driven or they go; every override needs a written why (`test_budget_override_discipline.py` registry) | `budget.Budget{Name, Limit, Why}` — the rationale is a field; a package test fails on any empty `Why` |
| No silent truncation — cuts must be marked (`context_budget.clip` + slice census) | `budget.Clip` is the only shortening idiom; marker byte-identical to Python's so records interoperate |
| No swallowed errors (`except: pass` audits) | errors are returned or recorded; a failed step carries its real reason into the failure chain |
| The resolved store is part of the result (2026-08-16 live-ledger incident) | `record.New(workspaceDir)` takes the store as an argument; `cmd/maro` prints the resolved workspace before any write |
| Data retention: append-only, never auto-delete | `record` has no delete/rotate/compact verbs at all |
| Named agentic seams (`purpose=` on every call) | `llm.Options.Purpose` is on every call site |

## On-disk compatibility

Same workspace resolution as Python `config.workspace_root`, name for
name and in the same order (`MARO_WORKSPACE` > `OPENCLAW_WORKSPACE` >
`WORKSPACE_ROOT` > `~/.maro/workspace`; `MARO_USER_DIR` moves only the
user-config tier, and **there is no MARO_HOME** — the 2026-08-16
live-ledger incident variable stays unread). Same record files, same
shapes:

- `memory/outcomes.jsonl` — compatible key subset;
  `measurement_class: "go-port"` fences Go rows in analyses. `cost_usd`
  is deliberately absent (no estimator ported; missing ≠ zero).
- `memory/captains_log.jsonl` — `{timestamp, event_type, subject,
  summary, audience, context, loop_id}`.
- Appends take the same advisory flock on the same sibling
  `<file>.lock` files as Python `file_lock.locked_append` (fail-closed,
  torn-tail framing), so both runtimes can write one workspace without
  corrupting each other's rows.

dev-recall, the viz server, and the learning pipeline read both
runtimes' history through one lens.

## What v0 is (and is not)

**Ported and running (spine-first vertical slice):**

- `internal/config` — two-tier YAML (user < workspace), one-level-deep
  nested merge, env path overrides; parse failures are *reported*, never
  swallowed.
- `internal/budget` — the caps discipline (see doctrine).
- `internal/llm` — adapter seam + `claude` CLI subprocess backend
  (flag-for-flag with the Python adapter, stream-json result parsing)
  and Anthropic Messages API backend; scripted `Fake` for tests/dry-run.
- `internal/jsonx` — tolerant JSON extraction (fences, prose,
  brackets-inside-strings), strict about content (non-string steps
  error, never coerce).
- `internal/planner` — decompose with operator docs
  (GOALS/CONTEXT/SIGNALS.md) riding whole under the OperatorDoc budget.
- `internal/loop` — decompose → execute → record, prior-step evidence
  budgeted+marked, blocked reasons traveling whole.
- `cmd/maro` — `maro run "goal" [-max-steps N] [-backend ...] [-model X]`.

**Pack tranche (2026-08-22, "export then import success"):**

- `internal/pack` — the full maro-pack lifecycle: `export` (gather +
  scrub + REVIEW.md), `seal` (review-hash + payload-digest stamps),
  `import` (trust demotion per PORTABLE_LEARNING_DESIGN §3), `adopt`
  (quarantine → live with provenance frontmatter). CLI:
  `maro pack export|seal|import|adopt`.
- `internal/scrub` — secret_scrub.py port (secret shapes + known-local
  identifiers; the human review gate remains the real backstop).
- `internal/provenance` — the Tier-0 lesson-provenance classifier,
  re-applied at import so transport cannot launder a prompt-derived
  lesson into an injectable one (db37d525 class).
- `internal/knowledge` — the MINIMAL Python-schema store surface the
  importer lands in: hypotheses + medium-tier lessons, full asdict()
  field parity, plus the variant-union rewrite (identity collision
  skips the row, not its rationale).

**Cross-runtime proof** (`crossrt_smoke.sh`, run live 2026-08-22): the
full circle Python export → Go import → Go export → Python import
passes — each seal verified by the RECEIVING runtime's independent
recomputation of the canonical payload digest
(`json.dumps(sort_keys, separators=(",",":"), ensure_ascii=False)`
reproduced byte-for-byte in `canonical.go`, pinned against
CPython-generated fixtures), trust demoted at every hop
(contested-by-birth compounds: `imported-xrt-go-imported-xrt-py-…`),
and Python's own loaders (`load_hypotheses`, `load_tiered_lessons`)
parse every Go-written row. The two runtimes also share the flock
protocol on `memory/.pack-import.lock`, so concurrent imports from
either runtime into one workspace serialize.

**Pack-tranche residuals, named:**

- `skill_records` quarantine to `imports/<label>/` instead of importing
  natively (outcome string `quarantined_no_native_skill_store_v1`) —
  this runtime has no skills store until the tools tranche; the rows
  are preserved verbatim, nothing is half-imported.
- `--include-runs` (Class E opt-in) and `--include-playbook`'s Python
  interactive-confirm shim are not ported; playbook export works via
  the flag, runs export does not exist yet.
- Rules/hypotheses lanes share Python's intra-artifact behavior of
  deduping by id but not adding each imported TEXT to the identity
  snapshot mid-artifact (lessons do; the asymmetry is upstream's —
  kept bug-for-bug so transport semantics stay identical, worth fixing
  in BOTH runtimes together).
- The import-side classifier gets (lesson, source_goal) only, like
  Python's import call site — the evidence leg is a mint-time-only
  signal in both runtimes.

**Pack adversarial round (2026-08-22, 4 lenses, sonnet-medium fallback)
— fixed here, plus divergences deliberately kept and named:**

Go is now STRICTER than Python on several hostile shapes (all
refusal-direction, so no legitimate Python-produced pack is affected;
each is a candidate to backport to `src/pack.py`): decompression bounds
on untrusted archives (64MB/member, 256MB total, 4096 members — Python's
tarfile path is still unbounded); refusal of invalid-UTF-8 members and
lone-surrogate `\u` escapes in pack.json (Python crashes loudly on the
same input, Go's decoders would have silently substituted U+FFFD);
manifest/archive bijection at both seal and import (duplicate manifest
paths, un-manifested stowaway members, and manifest rows with no archive
member are all refused — Python KeyErrors on the missing-member case but
silently carries stowaways and double-imports duplicate paths); rows
whose id field is absent, null, empty, or composite (array/object) are
reported `malformed_skipped` — scalar ids (number/bool) are coerced to
Python's `str()` form and import at parity (r2 correction: Python's
f-string imports `{"rule_id": 42}` fine; only ABSENT/empty ids collapse
onto one shared `imported-<pack>-` identity there, silently eaten as
`already_imported`, and Python stringifies null/composites into junk
ids); `pack_format`
that is present but not a valid integer is a hard refusal (Python
TypeErrors — closed, but as a crash).

Behavior ported to parity in the same round: the
`knowledge.provenance_gate_enabled` killswitch (ambient config, string
"false"/"0"/"no"/"off" normalized; the incoming-stamp quarantine path
stays outside the gate, as in Python), the adopt audit row + full report
shape (`label`/`adopted_at`/`dry_run`), and `AbsorbVariant` re-ported
line-for-line (strip both sides, unicode-aware trim, pre- AND post-clip
identity checks).

Named textual divergences accepted (degenerate input only, no safety
effect): `asString` renders a JSON array as `[a b]` where Python's
`str()` gives `"['a', 'b']"`; RE2's `\b` is ASCII-only where Python's is
unicode-aware, so a non-ASCII username/hostname in the scrub denylist
could bound differently across runtimes (identifiers on this box are
ASCII; the human review gate is the backstop). Canonical-digest numbers:
Go preserves `json.Number` literals verbatim while Python re-normalizes
through `json.loads`/`dumps` — identical for every machine-written
manifest, divergent (→ digest mismatch → refusal) for hand-crafted
non-canonical literals; pinned by test, documented in `canonical.go`.
CLI drift: Go uses flags (`-pack`, `-name`) where Python's `maro-pack`
uses positionals, and Go has no `export --seal` one-shot and no
interactive confirm (missing `-yes` refuses rather than prompts).

Round 3 (fix-layer review, 2 lenses) additions: a decompressor-level
byte ceiling now sits BETWEEN gzip and tar, so PAX/GNU meta records the
stdlib consumes inside `tr.Next()` (1MiB each, unbounded in count,
invisible to the entry cap) are bounded categorically; JSONL rows decode
with UseNumber so numeric ids keep their source literal exactly (a
float64 round-trip diverged >2^53 ids from Python's arbitrary-precision
str() and let crafted neighbors collide); report rows come out in file
order (the r2 callback emitted malformed rows first); a malformed id's
report value is always a string. Note on null/composite ids: Python's
f-string stringifies those into junk ids and imports them — Go's
refusal is a deliberate over-refusal (safe direction), not parity.

Round 2 (fix-layer review, 2 lenses) additions: non-regular tar entries
(dirs/symlinks/devices) and duplicate member names are refused outright
and EVERY header counts against the entry cap (the r1 bounds skipped
non-regular headers before any cap — a decompress-loop bypass);
lone-surrogate escapes are refused per-row in JSONL content, not just
pack.json; Import refuses `pack.json`/`REVIEW.md` listed as artifacts
(matching Seal); explicit `provenance_gate_enabled: null` keeps the
gate ON in Go where Python's `bool(None)` turns it OFF (pinned, safe
direction).

**Executor tranche (2026-08-22, tool-bearing worker steps):**

`maro run` now defaults to the EXECUTOR lane on the subprocess backend
(`-safe` opts back into utility mode; the dry backend never requests it):

- Two subprocess lanes, matching Python's `no_tools` split: utility keeps
  `--tools ""` + a `CLAUDE_CODE_MAX_OUTPUT_TOKENS=16000` runaway brake;
  executor enables the CLI's own tools with `--disallowedTools
  WebFetch,WebSearch`, binds cwd to the run's project dir
  (`<workspace>/projects/<slug>/`), and caps per-tool-call Bash
  output via `BASH_MAX_OUTPUT_LENGTH` with Python
  `_bash_output_cap_env`'s full precedence — including the
  disable-means-UNSET-in-child semantics that Python's 2026-07-27 review
  paid for. The deny-list is a convenience, NOT an egress boundary: Bash
  stays enabled, so the worker can still fetch (curl, git). Python's
  actual boundary is the container lane (`executor.container`), which is
  unported — until then an exec run trusts the goal text the way any
  uncontained agent session does (adversarial exec review 2026-08-22,
  Skeptic; the old phrasing over-claimed).
- The simulated tool-call protocol (`_TOOL_INJECTION_TEMPLATE` verbatim,
  with the load-bearing "Never execute it" line): workers report via
  `complete_step` (result/summary/confidence/inject_steps) or
  `flag_stuck` (reason/attempted); no parseable tool call falls back to
  content-as-result (Python parity). Parsing is strict (whole-span,
  UseNumber, trailing data refused); `llm.ParseToolCall` is invoked at
  the loop's call site, not inside the adapter — a named divergence:
  Python parses adapter-side because its API backends have native
  tool_use; Go grows that when one is ported.
- Plan mutation: `inject_steps` splices at the FRONT of the queue
  (Python `remaining_steps[:0]`), capped 3/step, each clipped by a
  registered budget (`injected-step`, 500); total executed steps are
  capped at 2× the planned count — an honest stand-in for Python's
  `max_iterations` machinery (adaptive bumping not ported); exhaustion
  marks the run stuck with the remainder NAMED (step contents, not a
  count) in the failure chain. Worker-injected steps carry a
  `WasInjected` tag all the way to the CLI's step printout — plan
  mutation is visible in the delivered output, not only in records.
- Project-dir resolution is the full `resolve_project_slug` port:
  naive 5-word slugs collide on generic openers ("tell me about
  the…"), so an existing dir with a generic slug and a DIFFERENT
  recorded mission disambiguates via `-2`…`-20` suffixes, then a goal-
  hash fallback (returned unchecked — Python parity); same mission
  re-enters its dir (continuity), specific slugs reuse without
  ceremony, and a reuse decided on WEAK evidence (no recorded mission,
  phrasing-only tails) is warned, never silent. Named divergence:
  Python reads the recorded mission from NEXT.md's goal line; Go
  records a `.mission` file at project creation — written
  full-content-then-`link(2)` so first-writer-wins holds atomically
  WITH its content (a crash mid-write must not leave an empty file
  that reads as "no evidence" forever) — and FALLS BACK to parsing a
  Python-written NEXT.md `> goal` line, because the runtimes share one
  workspace format and the guard must not be blind on the interop side
  (adversarial exec r2, Skeptic). Without all this, two unrelated runs
  silently share a live project dir and the second worker acts on the
  first's files — all four r1 lenses flagged it.
- Disambiguation only settles SEQUENTIAL collisions; the CONCURRENT
  case is closed by the ported admission gate (`loop/slot.go`, Python
  `acquire_project_slot`): a per-project flock at
  `<workspace>/memory/loop-<slug>.lock`, claimed before the first
  project write, held for the run, refuse-immediately on busy (the
  refusal records a stuck outcome naming the holder), and fs errors
  degrade to UNGATED with a warning. A busy-refused run NEVER removes
  the project dir — the slot holder may be a racing winner that just
  created it and hasn't written .mission yet (Python never deletes on
  LoopBusy; the r3 review caught the r2 cleanup rmdir'ing the winner's
  still-empty dir). Live-fired (two concurrent same-goal real-CLI runs
  → one completed, one refused with holder metadata) AND pinned by an
  automated race-detector-clean concurrent-Run test.
  Unported, named in slot.go: `--wait` polling, in-process sibling
  slot sharing, the unlink/reacquire inode guard, busy_policy=worktree.
- The blocked-step LADDER (`loop/blocked.go`, Python `loop_blocked`'s
  Phase 62 zoom-metacognition algorithm) replaces the flat first-block
  halt: NEED_INFO spawns research-then-retry; a missing external input
  is an honest terminal (never retried, split, or FABRICATED — the
  fabricated-input false-success bug); combined exec+analyze steps get
  the structural split; timeouts get an LLM split (45s, heuristic
  sentence-boundary fallback) or an honest terminal; then
  convergence-aware retry (md5-fingerprint parity with Python, pinned
  against CPython fixtures; round-1 generic hint, round-2 LLM
  refinement hint threaded into the retry prompt with the RETRY
  REMINDER), sibling-failure-rate re-decomposition (>50% blocked with
  ≥3 executed, current attempt EXCLUDED from the evidence — Python
  decides before appending to step_outcomes; the ladder r1 review
  caught Go self-counting the just-blocked attempt, which fired
  premature redecompose on small plans), re-decompose via the planner
  (≤5 sub-steps, capped at
  2 replans), and a terminal verdict that halts with the reason, the
  stop verdict, and the unexecuted remainder NAMED. Since r2 the
  verdict and the whole stuck reason land in TYPED outcome columns
  (stop_verdict / stuck_reason — Python memory.record_outcome and the
  loop result dict), and the chain entry's verdict tag is appended
  AFTER the entry's single clip (marker-class): the chain text was the
  only carrier, and the 600-char entry clip could eat the tag and the
  do-not-fabricate instruction on exactly the long-reason runs that
  need them (r2, both lenses independently — this round's HIGH again
  sat in the previous round's fix). Thresholds verbatim (3 retries /
  0.5 sibling / 2 replans / 3-consecutive-ceiling-timeout adapter-hung
  bail). Every decision is recorded as a METACOGNITIVE_DECISION event
  with the evidence (retries, fingerprints, replan count, action). A
  recovered run is DONE with its blocked attempts still in the record.
  The tool-less lane keeps v0's run-through — deliberate asymmetry.
  Ladder r1 fix layer (adversarial, 4 lenses, sonnet-medium fallback):
  the INITIAL plan is shaped too (Python's label="initial-plan" pass —
  a combined exec+analyze step no longer burns a worker call before
  the reactive split sees it; r2 made it UNCONDITIONAL across both
  lanes like Python's _prepare_execution, and closed the fourth
  shaping surface — worker inject_steps, Python's label="inject");
  blocked outcomes carry a typed
  (StuckReason, Attempted) pair mirroring Python's separate
  stuck_reason/result fields, so the fingerprint gets independent
  200-char heads and the missing-input check reads BOTH signal sources
  (honest scope, named r2: the second signal is live for flag_stuck
  only — llm.ResultError has no partial-output carrier, so Python's
  killed-subprocess tail (step_exec.py:1659, last 2000 chars) is
  structurally unavailable until the adapter grows one);
  the MISSING_INPUT inner clip is Python's clip(reason, 1000) runaway
  bound, not the 600-char chain budget (which guaranteed the
  do-not-fabricate instruction fell off the assembled entry); the
  timeout-split and refinement-hint calls return their token usage into
  the run totals (with the failed-call ResultError salvage); a
  successful step resets the consecutive-timeout streak (Python
  loop_execute.py:1884 — without it, non-consecutive timeouts
  accumulated to a false adapter-hung bail); split/redecompose children
  of a worker-injected step keep the WasInjected audit mark. All six
  fixes carry mutation-verified must-detect pins.
  Unported ladder pieces, named: token_runaway abandonment (no ingest
  brake), escape-pattern demotion hints (step_exec detectors), mid-loop
  diagnosis consult (introspect, Phase 44), navigator shadow, per-role
  model routing for split/hint calls, retry model-tier escalation
  (loop_blocked.py:235-242 cheap→mid→power via step_tier_overrides —
  Go has no model-tier registry; retries re-run on the same adapter),
  the pending_context injection
  seam (hints ride the step prompt), boundary/recon tag guards in step
  shaping, mid-loop iteration-budget bump (the 2× cap stays the outer
  breaker — binding on small plans: for a 1-step plan the cap is 2, so
  the 3-retry threshold is structurally unreachable there; Python's
  max_iterations=40 + one-time 50% bump machinery is what actually
  gives the ladder room). Known shared-key risk, inherited from Python:
  stepRetries/stepFingerprints key by literal step text, so two plan
  positions with byte-identical text (e.g. two splits both falling back
  to the generic analysis clause) share retry/fingerprint state.
- Closure verification (`internal/closure` + `internal/runs`) — done ≠
  successful, made structural. The loop's "done" says the steps
  drained; only `closure.Verify` says the GOAL was achieved, and the
  two stamps stay separate. The pipeline is Python
  closure_verify.verify_goal_completion's spine: plan checks by
  INVERSION (LLM, plan prompt ported with the stated-guarantee and
  deliverable sections trimmed to what this runtime supplies) → run
  them MECHANICALLY (bash -c — a NAMED upgrade, not parity: Python's
  shell=True resolves to /bin/sh (dash on this box), while the shared
  plan prompt's own scaffolding examples use bash idioms, so Go runs
  the shell the prompt teaches; per-check timeout with a PROCESS-GROUP
  kill — Setpgid + kill(-pgid), closing the orphaned-background-server
  leak Python shares — plus a WaitDelay backstop (2s, scaled to
  timeout/4 for short budgets): a probe that DETACHES (setsid/double-
  fork) escapes the group AND holds the output pipes, and without
  WaitDelay Wait() blocks for the escapee's whole lifetime (r2); the
  ErrWaitDelay return REAPS the group itself — the ctx-watchdog kill
  stands down once Wait returns, so the early return would otherwise
  leak the merely-backgrounded child the pre-r2 code reaped at the
  deadline (r3; a setsid escapee still outlives both, as it always
  did); probe output is buffered unbounded in memory before
  truncation — Python capture_output parity, named not silent; per-check stdout/stderr stored at 500/300 RUNE
  cuts, with outcome classified on the FULL stderr first — the
  inconclusive phrases sit at the end of verbose diagnostics; cwd =
  the exec lane's project dir; cwd unresolved → checks
  REFUSED as env-unresolved inconclusive rows, never run in the
  launcher's directory — B3a) → verdict (LLM) → verdict integrity.
  Ported lessons, each with a pinned test: pass/fail/INCONCLUSIVE
  tri-state per probe (_check_outcome branch-for-branch — the
  verifier's own failure is missing data, not disproof); judged
  tri-state (all-inconclusive → judged=false → goal_achieved stamp
  ABSENT, the 4/5-false-negatived-dogfood lesson); the
  ungrounded-False confidence cap (complete=false contradicting every
  executed probe with no file content in evidence → 0.65, below the
  trust floor, cut marked in the summary — run 2738d9c0); the
  behavioral-gap downgrade Signal 1 (the verdict's own prose admits
  runtime wasn't exercised + zero behavioral-modality probes →
  deterministic flip with the reason as the summary opener — and it is
  UNCONDITIONALLY live here: Python's 18773dfa stand-down gate keys on
  resolved_intent deliverables, which are unported, so Go behaves like
  Python with resolved_intent=None — parity-with-None, but the
  false-demotion risk for honestly-worded all-document goals returns
  with the intent subsystem and is owed the gate then);
  verdict-first summaries (the FLAG writes the opener; prose can only
  elaborate — run d2f4e2f4); per-segment probe-modality
  classification (quote-aware top-level split, most-behavioral wins,
  hint-before-runner precedence, non-exec runner flags — CPython
  fixture parity); closure_fingerprint byte-parity (md5 12-hex over
  sorted normalized hard-fail signatures, §9.3 — cross-runtime verdict
  rows compare); work summary with VISIBLE truncation (4000/300/6
  Python-parity literals, cut and COUNTED in runes — Python len()
  semantics; the markers are judge-facing parity text, deliberately
  not budget.Clip's vocabulary); the
  project-file-inventory grounding block (probe paths that EXIST —
  2026-07-09: two known-good runs false-negatived by checks against
  invented filenames); every closure outcome — verdict or NAMED skip —
  a durable row in build/closure_verdicts.jsonl
  (persist-the-artifacts decree). `internal/runs` is the WRITER half
  of the metadata contract recall.FindPriorAttempts reads (the gap
  named since the recall tranche): run dir + source/build/artifact
  skeleton, prompt.txt first-wins, metadata.json merge-writes
  (started_at first-writer-wins, nil POPS a key, atomic temp+rename),
  StampVerdict = _apply_verdict_tuple parity (every member set or
  popped, gaps capped at 5 with an announced count cut). Wiring:
  run dir created BEFORE recall and the run excluded from its own
  prior-attempt scan (recall.RecallExcluding); closure fires on ANY
  exec-lane run that executed a step — Python's
  _closure_eligible_statuses semantics: a stuck run that wrote real
  files still gets the honest what-got-delivered signal — with named
  skip rows ("no_steps_ran", "tool_less_lane",
  "outcome_record_failed") for every terminal path that doesn't judge
  (CLOSURE_VERDICT event after LOOP_FINISHED); Verify wrapped in a
  deferred recover persisting an "exception" row (the 2026-07-27
  both-tire-runs lesson — an uncaught panic here would crash the loop
  AFTER the work succeeded); the durable row carries the Python-parity
  check_results array (per-check description/command/exit/outcome/
  stdout/stderr) and is SCRUBBED at the single write owner
  (runs.AppendVerdictRow, via a UseNumber JSON round-trip because
  scrub.Walk descends only decoded shapes and a plain decode would
  widen int64s through float64); judge PROSE (summary/gaps) is scrubbed
  once more at Verify's return boundary because it flows to four
  consumers — row, metadata stamp, event, CLI (r2: the CLI line was a
  second unscrubbed egress). Residual, Python-parity, named: stored
  stdout/stderr are truncated BEFORE the row scrub, so a secret
  straddling the 500/300 cut can leave an unmatchable fragment;
  FailedChecks stay raw on purpose (the fingerprint must be computable
  identically anywhere in the run's lifetime — Python fingerprints
  unscrubbed signatures too). closure.Options.DryRun is exercised only
  by direct unit callers — the composed loop path gates on !DryRun
  before Verify, so the field is honest for library callers, inert on
  the CLI path (r2, named not logged-as-wired); the verdict line reaches the CLI
  beside the DONE/STUCK banner — done ≠ successful is operator-visible,
  not write-only;
  goal_achieved tri-state stamps only judged verdicts and feeds the
  NEXT run's recall icons — pure-Go workspaces stop degrading to zero
  prior attempts. Named divergences: the tool-less lane SKIPS closure
  with a durable "tool_less_lane" row (it structurally writes no
  files; Python judges all lanes because every lane there has a cwd);
  a verdict missing its "complete" flag is REFUSED as
  verdict_parse_failed (Python defaults the missing key to true —
  Go-stricter, a verdict without its load-bearing flag is not a
  verdict); dry-run/nil-adapter skips leave no row (Python parity).
  Deliberately unported with their subsystems: scope failure modes,
  resolved-intent deliverables + precondition preflight,
  stated-guarantee claim coverage, the verdict AUDIT (second-opinion
  pass over negatives), NEXT.md ledger gap, failed-check file
  evidence (so the ungrounded-False cap covers every all-passed
  confident False here), behavioral-gap Signals 2/3, nicknames /
  run-ref index / thread brains / locked_rmw (the Go loop is
  metadata.json's only writer — single-writer assumption NAMED, writes
  atomic so readers never tear), and the restart/declare-blocked
  DECISION consumers: evaluate_closure's action mapping waits for the
  restart machinery — the fingerprint is minted and persisted now so
  §9.3 convergence data accrues, but no Go caller yet disposes. Also
  unported: the DONE_WITHOUT_VERDICT tripwire (runs.close_run's
  finished-without-closure detector, LT-0 b) — it belongs to the
  stranded-run-sweep family; until it ports, a crash between
  StampVerdict and Finalize leaves a "running" record only recall's
  InterruptStatuses softens.
- Intent routing + the NOW lane (`internal/intent` + `internal/now`,
  director tranche slice 1) — the CLI stops assuming every goal is a
  loop. `intent.Classify` ports intent.py's routing half: LLM
  classification (prompt near-verbatim, 128 tokens, purpose "routing")
  with the heuristic keyword fallback (CPython fixture parity on lanes
  AND confidences), then the CAPABILITY overrides that win over
  classification — file-deliverable goals cannot run in a lane that
  writes no files (burn-in batch 3), live-data asks cannot run in a
  lane that fetches nothing (config-gated: now_lane.live_data_routing,
  flag OFF inert per the DEFAULTS.md contract). Boolean fields parse
  fail-OPEN (is-true-or-"true"); lane whitelisted, confidence
  safe_float'd. `now.Run` is handle.py's _run_now + _verify_now_outcome:
  one tool-less call (2048/0.4) then the now-verify judge (160 tokens —
  64 fit `{"fulfilled": false}` and no why, which shipped an inert fix
  once; run 2113a608) with the TRI-STATE verdict: fulfilled → stamped
  true; not fulfilled (including NON-ANSWERS — generic how-to-find-it
  guidance demotes) → incomplete + the judge's why; errored judge →
  fail-open UNJUDGED with the error MARKED (now_verify_error — a broken
  verdict pipe must not look like a deliberately unjudged run, review
  F7). NOW runs get run dirs, outcome rows (task_type "now",
  goal_achieved tri-state on the row like Python), StampVerdict
  (go_now_verify_v1), finalize. The routing decision is PRINTED
  (`lane: NOW (0.98) — …`) beside the exec-mode line; `-lane
  auto|now|agenda` forces. Live-fire: real classify routed NOW @0.98,
  real judge stamped goal_achieved=true on row + metadata. Named
  divergences (Go-stricter, deliberate): the link-triage NOW shortcut
  and the live-data URL exemption are BOTH unported — they assume the
  NOW lane pre-fetches carried URLs (web_fetch enrichment,
  conversational-compute decree), and Go's NOW lane fetches nothing, so
  honoring them would route unanswerable asks to a lane that cannot
  answer; the classifier prompt's link-read EXCEPTION paragraph is
  omitted for the same reason. Adversarial r1 hardening (2026-08-22):
  the judge's why is scrubbed AT THE BOUNDARY where it is set (closure
  doctrine — per-sink scrubbing missed the terminal, the one surface an
  operator reads); the trailing-rationale recovery is ported
  (verdictRationale — `{"fulfilled": false}` + prose after the JSON was
  the ed7cf400 shape; 160 tokens was only HALF that fix); the judge
  window is capped at 2000 chars with the VISIBLE truncation marker
  (_now_verify_payload parity — an unmarked window makes a judge report
  what it cannot see as not delivered); runs.StampVerdict confidence is
  now *float64 and nil POPS the key (Python confidence=None — the old
  signature fabricated `goal_verdict_confidence: 0` on every judged
  NOW verdict, "verified with zero confidence"); the outcome ROW
  carries goal_verdict_source (judged → go_now_verify_v1, errored
  judge → go_now_verify_error with goal_achieved absent, unparseable →
  no source) so F7's broken-pipe/unjudged distinction survives an
  unwatched run — go_-prefixed names, DIVERGING from Python's
  now_self_verdict* taxonomy on purpose (the Go judge is a different
  instrument; the fence is the point); refused-but-billed calls
  salvage llm.ResultError usage at all three new call sites (classify,
  answer, judge); and the routing classify call's spend is SEEDED into
  whichever lane runs (now.Run seedIn/seedOut, loop.Opts
  SeedTokens*) so outcome rows report the goal's full real cost.
  Adversarial r2 hardening (same day): the judge window's cut is
  RUNE-based (byte cuts split UTF-8 mid-rune and the marker miscounts —
  the closure tranche's cutRunes class); verdictRationale's brace scan
  is STRING-AWARE with an unbalanced-object guard (Go-stricter than
  Python's naive count: a brace inside a JSON string no longer closes
  the object early, and a truncated object recovers nothing instead of
  the raw JSON blob) and no longer clips internally (scrub-before-clip
  — a clip-first order could cut a credential mid-string past the
  fixed-length secret patterns); record.StampOutcomeVerdict ports
  memory_ledger.stamp_outcome_verdict — the loop lane's outcome row is
  written at finalization BEFORE closure judges, so the verdict lands
  on the row post-hoc under the same flock every appender takes
  (without it every closure-judged loop run read as permanently
  unjudged on the cross-runtime ledger); the closure stamp's
  confidence is gated WITH the verdict (unjudged closure stamps no
  confidence — Go-stricter than Python, which writes 0.0); verdict
  sources are shared record.Source* constants; and the CLI's routing
  block is extracted (routeLane) so classify-usage extraction is
  pinned with a real nonzero value.
  Adversarial r3 (same day): StampOutcomeVerdict gained the two
  Python behaviors the first port dropped — goal_verdict_at on every
  stamp (the framing→verdict delay the learning pipeline divides by)
  and verdict_history on judged-over-judged re-stamps (2026-08-10
  re-stamp-honesty decree: corrections preserve the superseded verdict
  on the row); nil confidence now LEAVES an existing row value
  untouched (Python merge semantics — deliberately different from
  runs.StampVerdict's full-replacement stamp where nil pops, both
  matching their Python owners); a failed row stamp writes a durable
  "outcome_row_stamp_failed" run-dir row beside the terminal warning
  (a silent failure recreates the headline bug behind a rarer
  trigger); verdictRationale strips <think> traces before recovery
  (jsonx.StripThink — Go-stricter, the Python sibling shares the gap);
  the ledger rewrite removes its temp file on a failed rename.
  Named residuals: the answer summary on the row is clipped but
  unscrubbed (Python parity — same residual as the loop's row, named
  there too); a crash between WriteOutcome and Finalize leaves a
  terminal row beside a "running" metadata.json (same window the loop
  has; recall's InterruptStatuses softens it); prose-BEFORE-JSON in a
  judge reply returns the whole text from verdictRationale, JSON
  included (Python parity, named in the function doc); the row stamp
  does not retry transient I/O errors (Python's max_attempts DEFAULTS
  to 1 too — its retry knob is unported until a caller needs it); the
  memory-provenance advisory marker (_mark_memory_provenance) is
  unported with its stores — returns with the memory tranche.
  Adversarial r4 (same day, convergence round): the exported
  StampOutcomeVerdict doc was corrected to the merge semantics the
  code implements (a doc lying about nil-handling is how zero-
  confidence-class bugs return); verdict_history string fields default
  to "" for rows judged by another writer (Python .get(k,"") parity —
  no JSON nulls where Python writes empty strings); pins added for the
  failure-marker path (fault injection: a directory squatting on the
  ledger's .tmp path), goal_verdict_at advancing on UNJUDGED re-stamps,
  and foreign-judged-row history. One operational note carried:
  nothing consumes closure_verdicts.jsonl operationally yet — the
  durable marker's discoverability is manual until a reader lands.
  Adversarial r5 (same day, near-fixpoint round — 0 HIGHs): the
  failure-marker's OWN write failure was discarded (`_ =`) — the
  doubly-failed case now appends a named warning ("outcome-row stamp
  marker write also failed"), covered at the AppendVerdictRow seam
  (M35 DETECTED) because loop-level double injection is
  non-deterministic by construction (the run's own verdict row creates
  the file before the marker fires — comment on the pin names this);
  the verdict_history key-presence gate got the explicit
  `prior != nil` normalization plus a comment naming the
  presence≈is-not-None invariant it leans on. Adversarial r6 (same
  day) confirmed FIXPOINT: 0 HIGHs; the null-gate got its explicit
  JSON-null pin (M36) and a dead Python citation was corrected to
  _verdict_row — test/doc only, tranche closed at six rounds.
  Deliberately unported with their
  subsystems: web_fetch enrichment, the deterministic provenance guard
  (claimed inputs/outputs on disk), check_goal_clarity + imperative
  rewrite (needs the ask-the-user surface), introspects_self's consumer
  (container-isolation routing — the field is carried, its consumer
  named), NOW artifact write, navigator dispatch, deferred learning,
  personas/workers (workers shipped in the director slice below), and
  director.py's plan/delegate/review layer (shipped as the director
  slice below).
- DIRECTOR plan/delegate/review (`internal/director` +
  `internal/workers`, `maro director "directive"`) — the routing
  tranche's second half: SPEC + tickets from one Director call
  (large-scope directives get the staged 4-6-ticket prompt, gated on
  the ported estimate_goal_scope zero-LLM heuristics), a non-fatal
  pre-plan challenger, per-ticket dispatch to persona-framed workers
  speaking the simulated tool protocol (deliver_result/flag_blocked),
  Director review of every output with reject-on-parse-failure (auto-
  accepting hides bad output), up to MAX_REVIEW_ROUNDS=2 revision
  rounds proceeding best-effort on exhaustion, and one compiled report.
  Ported lessons: bounded completed-context accumulator
  (budget.Accumulator — worker results re-sent to later workers grow
  quadratically in ticket count; eviction is ANNOUNCED, oldest-first,
  newest kept whole even over budget); report-echo tri-state on the
  LLM-compile path only (MH #6 — false means the worker's content was
  DROPPED on the way to the parent; the verbatim-concat fallbacks stay
  nil because a check that cannot fail proves nothing);
  WORKER_REPORT_OMISSION + WORKER_DELEGATION_GAP candidate events into
  captains_log.jsonl, the gap event scoped to worker-AUTHORED reasons
  only (adapter failures pattern-match the provision keywords and
  would contaminate the corpus — negative-control pinned); blocked-
  origin taxonomy (worker/adapter/empty); ResultError usage salvage
  (4th site); judge-window Clip(4000) before the review verdict;
  spec-parse fallback = one inferred ticket for the whole directive;
  director log JSON scrubbed at its single write boundary, atomic
  temp+rename, report_echoed kept nullable (nil ≠ false); the
  flags-after-goal refusal from `maro run`; RequiresExplicitAcceptance
  PRINTED as a warning (the approval surface is unported — a silent
  "inferred" on a deploy-shaped directive would fake consent).
  Adversarial r1 (same day, 4 lenses): 4 verified HIGHs fixed —
  gap-event prose now scrubbed before captains_log (Python's log_event
  doesn't scrub; backport candidate), review verdicts with
  missing/null/mistyped "accepted" now REJECT (Go-stricter than
  Python's absent-key accept, named), rejected-no-revision runs warn
  and the director log now persists review_decisions
  (ticket-correlated) + warnings (Python persists neither; backport
  candidate); plus malformed spec-ticket entries skip with a warning,
  the report-echo check judges the SAME clipped window the compiler
  saw (Python compares unclipped — named honesty divergence), the
  bare-content bar counts runes, challenger failures are named, and
  the judge windows ride the registered WorkerJudgeWindow budget.
  Deliberately unported, named: worker_slice memory injection +
  slice_echo (sqlite store), PersonaRegistry + persona files (inline
  personas only — Python's own fallback tier), lat.md graph injection,
  the skip-if-simple direct route (the Go CLI's intent lane owns that
  decision), container-exec backend contract, check-in/escalation +
  evaluate_closure (return with restart machinery), the MODEL_CHEAP
  challenger adapter (challenger runs on the run's adapter). CLI dry
  smoke green; mutations M37-M42 all DETECTED (review-safety flip,
  gap-event descoping, echo-asymmetry removal, silent eviction,
  unscrubbed log, dropped salvage). Adversarial r2 (2026-08-22,
  skeptic+qa): the r1 TicketID fix itself was the round's HIGH —
  revised tickets are now persisted with revision_of so every decision
  key resolves in the log; the accepted-field gate keeps the model's
  own reason/revision_request (a malformed-but-informative verdict
  still drives a revision round); malformed-entry warnings are bounded
  to one summary; whitespace-only guidance trims to empty; the CLI
  scrubs StuckReason/Reason and prints TicketID; scrub-at-set-point
  ACCEPTED-NAMED (sink-side is the boundary — set-point scrubbing
  would alter prompt inputs; sink census: writeLog + captains_log +
  CLI, all scrubbed). Mutations M49-M55 all DETECTED (M53's echo
  marker-vocab pin added only after its first run came back NOT
  DETECTED). Adversarial r3 (same day, skeptic+qa): the r2 fixes were
  again the HIGHs' home — the mid-loop guard fired on the sole
  iteration (double warning; "unreachable" comment false) and now
  warns only when rounds actually remain; the malformed counter
  covers non-object entries and a non-list tickets field warns with
  its type; CLI warnings scrubbed (sink census now actually closed);
  echo judge strips the whole clip marker (both budget wordings,
  offsets included) before term extraction instead of deleting two
  words after. Mutations M56-M60 all DETECTED. Adversarial r4 (same
  day, skeptic+qa): r3's guard was the HIGH again — its one-warning
  invariant held only at the shipped constant; now structural via
  stoppedEarly + MaxReviewRounds as a var (Python module-attr parity)
  with the N=3 case pinned; malformed census scans past the cap;
  empty tickets list warns; marker strip anchored at line end and
  digit-bounded like budget.markerRe (forged mid-line markers are
  content and keep their vocabulary — accum doctrine). Mutations
  M61-M64 all DETECTED. Adversarial r5 (same day, skeptic+qa): r4's
  anchor was the HIGH — (?m) let a forged marker ending ANY line strip;
  budget.StripMarker now owns the grammar (true-end only, Clip's exact
  wording; the Accumulator variant never reaches reportEcho and is
  content there), maxReviewRounds unexported (no cross-package races),
  absent tickets key warns like its empty/non-list siblings, non-list
  pin runs as t.Run subtests over five shapes. Mutations M65-M66
  DETECTED. Adversarial r6 (same day, skeptic+qa) closed the marker
  arc's ROOT: Clip no-ops on under-limit text, so true-end position
  still wasn't provenance — budget.ClipInfo now returns a
  did-this-call-cut bit and reportEcho strips only when it's true; an
  un-cut window's forged tail is content, pinned end-to-end through
  Run (forged tail can no longer flip the omission stamp to nil).
  Mutations M67-M68 DETECTED. Adversarial r7 (same day,
  skeptic+qa): QA found no HIGH/MED in the r6 fix itself — first clean
  lens of the tranche; Skeptic's HIGH was a true instrument gap (the
  ClipInfo wiring had no clipped-side e2e pin — the wiring revert
  passed the whole suite, verified by running it), now pinned through
  Run (M69) plus a tighter-re-clip honesty-bit case; marker vocabulary
  as false-positive padding accepted-named. Mutation M69 DETECTED. Adversarial r8 (same day,
  skeptic+qa): ZERO HIGHs — first clean round; shared MED was
  ledger-honesty (the "old marker nests in the new payload" claim in
  the r7 test + Clip docstring + Python's docstring is structurally
  unreachable — tighter cuts land before the old marker; corrected
  here, Python wording flagged for backport-correction), plus
  pass-through-zone honesty-bit coverage and a 7→6 term-count slip.
  Mutation M70 DETECTED. Adversarial r9 (same day,
  skeptic+qa): zero HIGH/MED from both lenses — second consecutive
  clean round, FIXPOINT DECLARED (r1-r9, mutations M37-M70 all
  DETECTED; sole new item a pre-existing dead-guard LOW,
  accepted-named). Director tranche CLOSED.

- Memory RECALL (`internal/recall` + the retrieval half of
  `internal/knowledge`) — the loop reads what the system already knows
  before it plans. `recall.Recall` is the seam (recall.py's contract):
  read-only — STRICTER than Python here: even the times_applied
  receipt write-back is unported, so the Go seam writes nothing —
  and every failure degrades to "knows nothing", with degradations
  NAMED in the instrumentation sources (`error_*`,
  `lessons_skipped_rows`) instead of swallowed. Two substrates ported:
  - **Ranked tiered lessons**: `LoadTieredLessons` reads
    `memory/<tier>/lessons.jsonl` with Python's read-time decay
    derivation (effective = stored × 0.85^days since last_reinforced,
    MEDIUM only — LONG is promoted-permanent; stored scores never
    rewritten, which would compound decay), the per-row parse guard
    (one malformed/byte-torn/type-drifted row skips THAT row and
    counts it — never the tier; numeric fields coerced inside the
    guard exactly where Python's r3/r4 of the store-hardening arc put
    them), and min_score compared AFTER decay, skipped entirely on
    raw loads (Python's `not raw` ordering). `QueryLessonsScored`
    pools LONG+MEDIUM over the FULL store (limit=None — relevance is
    the ranker's job), drops provisional / quarantined
    (minted_from=="prompt", the db37d525 provenance gate) / contested
    rows, and ranks with the TF-IDF cosine ranker
    (`TFIDFRankScored`) — tokenizer, log-IDF, norm-or-1.0 cosine, and
    the 0.90 citation penalty pinned against CPython-computed fixtures
    to 1e-9, ties keeping input order (Python's stable sort). Parity
    quirk kept deliberately: a no-signal query returns ALL lessons in
    input order at 0.0, topK ignored — only the caller bounds it.
    Selection mirrors recall.py's chunk-6 rewire: agenda-typed top 3
    from a 10-wide scored window, untyped tiered top-up deduped by
    lesson_id. The render is the receipts block ("## Lessons from
    Prior Runs", ✓/✗ by outcome, "reinforced Nx / N sessions /
    applied Nx" or the honest "(observed once)") under the
    1200-char LessonInject budget as a whole-line BREAKER — never a
    mid-line truncation (caps decree 2026-08-21) — with
    cited-only-if-rendered (a dropped line's id never enters
    lesson_ids_cited; the contradiction join must not contest lessons
    the run never saw).
  - **Prior attempts**: `FindPriorAttempts` scans
    `runs/<dir>/metadata.json` newest-first (mtime, cap 200 —
    O(recent activity)), matching exact / near (word-overlap Jaccard
    ≥ 0.9, `_text_similarity` parity) / project, with SF-2 tri-state
    goal_achieved and the §13b status-derived external-interrupt
    fallback (interrupted/stranded/refused_busy/clarification_needed
    carry no goal evidence). The as_context_block summary paragraph
    (status breakdown, verdict counts, interrupted count,
    do-not-repeat instruction) rides under the RecallContext budget
    with Python's 64-char marker reserve.
  Both blocks reach the INITIAL `planner.Decompose` as system-prompt
  extras in Python's extras order (ancestry before lessons,
  planner.py:962) — the same two channels Python feeds decompose
  (lessons_context via loop_planning, the attempts summary via
  handle.py's as_context_block → ancestry_context). Redecompose/split
  calls deliberately get no recall block, like Python. The read is
  instrumented as a RECALL_PERFORMED captain's-log event before
  LOOP_STARTED; emission lives at the loop call site because the Go
  seam keeps no recorder handle (named divergence — Python emits from
  inside recall()); an event-write failure is held and lands in
  Result.Warnings or, when decompose also dies, the failure chain.
  Unported recall substrates, named (recall.py carries eight):
  standing rules, decision journal, graveyard resurrection (the one
  Python side effect — resurrect=True un-decays), failure notes,
  learning activity, playbook, knowledge nodes, thread identity
  (origin walk + ancestry.json), prior-decision briefs (run_card
  curation), project_artifacts inventory, dispatch_signals,
  portability re-weighting (§14a), camera frames, age stamps, the
  legacy flat-store top-up + fallback injector, magic-prefix stripping
  at the match boundary (a Python run recorded under a prefixed
  prompt can miss the near-match here), hybrid BM25/RRF ranking
  (`_USE_HYBRID`, optional rank-bm25 dep — TF-IDF is Python's own
  always-available fallback, so both runtimes share this floor), and
  max-age pruning at the query layer. The Go runtime also does not
  WRITE `runs/<dir>/metadata.json` yet, so prior attempts surface only
  in a workspace shared with Python — a pure-Go workspace honestly
  degrades to zero priors. Go-stricter divergences, refusal-direction:
  type-drifted STRING fields skip the row (Python's duck-typed
  dataclass carries them until they explode inside the ranker);
  byte-invalid rows are refused before decode (Go's encoding/json
  would otherwise launder invalid UTF-8 into U+FFFD — the corruption
  signal loads_clean exists to preserve); NON-FINITE numeric strings
  ("NaN"/"Infinity" — both strconv.ParseFloat and Python float()
  accept them) fail the row and are counted, because a NaN score
  survives every MinScore filter (`NaN < x` is always false) uncounted
  (r1 Skeptic); Python-truthiness is kept where it is load-bearing
  (`provisional: "false"` is truthy and stays filtered;
  a type-drifted truthy `evidence_sources` value stays CITED via a
  one-element carrier — the shape-only assertion silently applied the
  0.90 penalty Python would not, r1 Skeptic + Expert QA
  independently). Container fields with NO ranking consumer
  (imported/canon/delta_evidence/grounding/merged_variants) coerce to
  empty on shape drift without failing the row — behavior-inert
  today; a future consumer of those fields must revisit that choice.
  Recall r1 fix layer (adversarial, 4 lenses, sonnet-medium
  fallback): LoadOptions' zero value now means UNLIMITED — the
  Limit<0-for-unlimited draft made `LoadOptions{}` silently return an
  empty result set indistinguishable from an empty store (the round's
  HIGH; zero value must degrade to "everything", never "nothing");
  Recall carries a recover() folding an unanticipated panic into
  Sources["error_recall_panic"] (the Go stand-in for Python's
  per-substrate except blankets — this seam eats hand-editable data
  and must never crash a run); ContextBlock ports the 2026-08-14
  degenerate-budget floor WITH the subtraction (limit-64 ≤ 0 would
  hit Clip's breaker-off path and return unbounded text); the
  metadata-scan cap comment states honestly that it bounds only the
  read phase (the listing+stat phase is O(lifetime runs) in BOTH
  runtimes — inherited, needs a shared index to fix); prior-attempt
  ordering is a lexical sort on raw started_at, byte-for-byte
  Python's — known-preserved misordering across heterogeneous
  timestamp shapes. Further named after r1: the tiered-store
  QUARANTINE SIDECAR (_quarantine_unparseable) is unported —
  LoadTieredLessons is a pure READER and must not be reused as the
  read half of a rebuild-style rewrite (reinforce/promote/GC) until
  the sidecar lands, or skipped rows would be destroyed on rewrite
  (the exact Python incident the sidecar closed; the existing
  UnionVariantsIntoLesson is line-in-place and preserves undecodable
  rows, so it is not exposed); the lesson-render icon drops Python's
  SF-2 goal_achieved-preferred branch — dead code in Python today (no
  writer stamps goal_achieved on tiered rows) and TieredLesson has no
  such field here, so wiring it up in Python requires a synchronized
  Go patch; readers of lessons.jsonl take no flock in either runtime
  (writers do) — safe while every writer is a single write() or an
  atomic rename; LoadOptions' Raw/MaxAgeDays/LessonType surface and
  FindPriorAttempts' windowHours/excludeHandleID/project parameters
  are Python-parity surface with no production Go caller yet
  (unit-tested only; the project lane is dead on the CLI path until
  project threading lands pre-decompose).
  Recall r2 fix layer (adversarial, 2 lenses, sonnet-medium
  fallback): the r1 citedness fix had an unfixed WRITER sibling —
  pack import's shape-only `.([]any)` assertion PERSISTED drifted
  truthy evidence as `[]`, making the row permanently uncited (the
  read-side fix could never help a row the write side already
  flattened; both lenses found it independently, and it is the
  flagship pattern's 11th instance — the round's top finding sat
  inside the previous round's fix). One owner now:
  knowledge.CoerceEvidenceSources is the single truthiness-preserving
  coercion, called by the tiered reader AND the pack importer, with a
  round-trip pin (TestImportPreservesCitednessThroughTypeDrift).
  Also from r2: the recover now captures debug.Stack() under a new
  PanicTrace budget (a bare panic value with no trace is
  un-actionable; a panicHook test seam proves the recover fires and
  that pre-panic partial results survive); FindPriorAttempts returns
  a skipped count surfaced as Sources["prior_attempts_skipped"]
  (malformed run metadata was silently invisible — same
  short-read-vs-short-store honesty rule as the lesson loader);
  truthy(json.Number) parse-failure fails OPEN to truthy (the
  conservative direction for provisional/contested gates — pinned);
  the ContextBlock degenerate floor and the happy-path event-failure
  warning each gained the executing test their prose claimed.
  Named accepted (r2): with Limit<=0 as unlimited there is no way to
  request literally zero rows — acceptable because no caller wants an
  empty read from a retrieval API (comment on LoadOptions).
  Recall r3 fix layer (adversarial, 1 QA lens, sonnet-medium
  fallback): the r2 fix generalized the MECHANISM for the one named
  field but not the RULE across the function — one field below the
  citedness rewire, `provisional` was still a shape-only `.(bool)`
  read, so `"provisional": "true"` imported as TRUSTED and sailed
  past the recall-time provisional gate (trust escalation, strictly
  worse than the 0.90 penalty the r2 fix closed; flagship pattern
  instance #12). knowledge.Truthy is now EXPORTED as the boolean
  half of the boundary rule (CoerceEvidenceSources is the container
  half): every gate-feeding value crossing the pack-import boundary
  preserves Python truthiness, not JSON shape, with round-trip pins
  for both directions of drift. Also from r3: the panic VALUE is
  clipped separately (new PanicValue budget, 500) before joining the
  stack under PanicTrace, so a payload-carrying panic value cannot
  crowd the stack — the actionable half — out of the budget; the
  panicHook seam carries a no-t.Parallel() comment (unsynchronized
  by design, single production caller).
  Recall r4 (adversarial, 1 Skeptic lens, sonnet-medium fallback —
  the CONVERGENCE round: no HIGH, no production-code defect).
  Operational note (r4 MED): any pack imported by a pre-51377350 Go
  build persisted drifted provisional flags as a durable
  `"provisional": false` — indistinguishable on disk from a
  legitimately trusted row, and not repairable after the fact
  (the original string was overwritten). Re-import such packs with a
  current build. No deployment is affected today: the Go port is
  pre-production by decree and no pre-fix Go importer ran outside
  this branch's own tests. Accepted-named (r4): pack import's
  human_reviewed read stays a shape-only .(bool) — BOTH writers emit
  native booleans and the failure direction is the safe one (a
  forged string fails the assertion and the pack is REFUSED, not
  trusted; fail-closed is correct for a review gate, so routing it
  through Truthy would be a step backward); the panicHook
  no-t.Parallel() constraint stays comment-grade until the seam
  grows a second caller.
- A run whose project-dir setup fails still records a stuck outcome
  carrying the planning spend before erroring out; a transcript file
  that cannot be created degrades to the deleted-temp capture with a
  warning rather than failing the step — an OS error must not
  fabricate a "blocked" record blaming the model.
- Long-running step classification ported (keyword sets verbatim,
  `MARO_LONG_RUNNING_TIMEOUT`, full-suite ×2 capped at 3600s); executor
  default timeout 600s like Python's adapter default.
- Per-step transcripts: the merged stream-json capture persists to
  `<project>/artifacts/step-N-transcript.jsonl` (artifacts-over-streams;
  kept on success AND failure, never deleted by the adapter).
- Capability is structural: the named `llm.AgentToolsCapable` interface
  (one type, asserted by every reader); a requested exec run on an
  incapable backend degrades to the tool-less path WITH a warning
  (never silently), and the `-model` CLI wrapper forwards the
  capability (interface embedding would otherwise strip it). The CLI
  decides exec ONCE from the constructed adapter's capability and
  prints the entered mode plainly (`exec mode: ON/off — …`) — default-
  on tool-bearing execution is the run's highest-blast-radius property
  and must be visible without reading code.
- `EXECUTE_SYSTEM` ported as the sections whose machinery exists here
  (synchronous-execution, anti-hallucination, NEED_INFO, URL-fetch
  discipline, file-edit protocol, token efficiency). Deliberately
  dropped, because advertising a silently-dropped field is worse than
  omitting it: artifacts, decisions, world_facts channels,
  ask-may-be-wrong, also-noticed, pre-fetched-URL block,
  create_team_worker, step-type classification/prompt extras
  (data-pipeline, artifact-materialize, long-lived-process,
  recon-flavor). Each returns with its consumer.

Unported from Python's executor lane, named: container wrap
(`executor.container`), executor sessions + fork-master, token-runaway
brake, constraint tiers/HITL gates, URL pre-fetch, worker push guard
(`missing_write_targets`), tool-pathology classification, per-step model
routing/trajectory escalation.

**Inspector/evolver tranche (2026-08-22, self-improvement slice 1):**
`internal/inspector` ports src/inspector.py (fork-point, 1066 lines):
six friction-signal heuristics (the seventh, repeated_rephrasing, was
census-removed in Python — "a threshold returns with its comparison
site or not at all"; the port starts at six), thresholds resolved
env > config.yml > default, goal-alignment judging that returns nil
without an adapter (the 2026-07-31 fail-open census fix: 0.7 stays a
DISPLAY value; unjudged sessions cap at "fair" and cannot earn the
alignment-gated delight signal), the judged-goal-NOT-achieved and
verdict-pending fair-caps (outcome_policy.is_verdict_pending ported
inline), session-presence breach fractions (>30% of sessions, never
raw counts), the deep-pass cadence tick (locked RMW, corrupt-field
self-heal + negative clamp), report + suggestion persistence
(inspection-log.jsonl; suggestions.jsonl rows in the Python 9-field
schema, one locked batch append), and `maro inspect`. Named
divergence: the Go CLI defaults ADAPTER-LESS — Python's standalone
entry silently builds MODEL_CHEAP (up to 2 calls per outcome); the
no-silent-spend decree puts that behind `-llm`. Named unported:
inspector_loop daemon, attribution enrichment.
`internal/evolver` ports the evolver.py/evolver_store.py slice:
Suggestion/EvolverReport with the FULL on-disk field set (rows
interop with the Python runtime — shared workspace, same path+".lock"
flock protocol), content-key dedup save (the 81-duplicate calibration
bug), pending/dismiss/applied lifecycle with keyed merges that
re-emit unparseable lines verbatim, the cadence tick, the proposer
(system prompt + playbook GUIDANCE_FORM_RULES verbatim; outcomes
summary keeps the SF-2 tri-state discipline — done-but-not-achieved
is failure signal), auto-apply at confidence >= 0.8 for the low-risk
categories (observation no-op; prompt_tweak -> medium tiered lesson
minted_by=evolver, dedup-by-text via the knowledge snapshot,
reinforcement counters named unported; new_guardrail behind the same
manual/env/config gate, default HELD, epoch-seconds added_at — the
ISO-string bug that silently killed Python's whole constraint lane is
pinned), injection-guard scan FAIL-CLOSED in front of every category
(src/injection_guard.py ported as `internal/guard`, pattern lists
verbatim; the RE2 lookahead in the exfil-URL rule is enforced
post-match, same accept/reject), change_log.jsonl audit rows before
every mutation, and revert via the audit trail (lesson_add is
bookkeeping-only — Python parity, append-only store; guardrail_append
removal is behavioral). Plus `record.LoadOutcomes` (newest-first
tolerant reader over the shared outcomes ledger), `record.LockedRMW`,
the SummaryJudgeWindow budget (Python _REVIEW_STEP_CUT=4000 with its
traced-writers rationale), and `maro evolve` with
-list/-apply/-dismiss/-revert.
Named divergences from fork-point Python, deliberate: (1) unknown
suggestion categories land `action_failed`, never the silent
"applied" no-op that hid cost_optimization for months; (2) revert's
guardrail matcher accepts `source == <id>` — apply WRITES that key,
but Python's revert matches only `"evolver:<id>"`, so current-format
rows are unremovable there (backport-correction candidate); (3) the
0.6-0.79 advisor gate is unported — those rows stay pending for
review instead of getting an Opus hearing; (4) _verify_post_apply's
test-suite run is unported and NOT faked — Go auto-applies only data
rows, and the revert path is the safety net this slice keeps. Named
unported (package docs carry the same list): statistical scanners,
graduation, harness-friction/persona-gap/skill-candidate/island
passes, verify_applied_suggestions V2 lifecycle, playbook append,
Telegram notify, step traces in the proposer summary, skill_pattern
and sub_mission engines (both HELD with reasons naming the Python
CLI). Mutations M71–M84 all DETECTED (compiling mutants verified —
M71's first form didn't compile and was rebuilt semantic).

**r1 adversarial review (2026-08-22, 4 lenses, sonnet-medium fallback) —
two HIGHs fixed, both faithful-port traps.** (a) The exfil-URL allowlist
was a raw string prefix, so a lookalike domain (`r.jina.ai.evil.com`)
scanned clean; `urlHostAllowed` now matches at a host boundary (exact or
`.`-suffix subdomain). (b) A guidance-only `new_guardrail` (no/invalid
pattern) stamped `applied=true` with no durable effect once playbook.md
was dropped — `applyAction` is now tri-state and HOLDS it. Also: guardrail
append is idempotent by `source==id` (no double-write on retry);
`inspection_finding` rows HELD not `action_failed`; malformed
`goal_achieved` treated as judged-NOT-achieved (safe cap direction);
tool-call detector fixtured. NEW backport-correction candidates for the
Python fork-point: (5) `injection_guard._EXFIL_PATTERNS` URL lookahead is
prefix-only — same lookalike-domain bypass; (6) `inspector` grades a
malformed `goal_achieved` as unjudged (`is False`), letting it read good.
Fix-layer mutations M85–M90 all DETECTED (compiling mutants).

**r2 fix-layer re-review (2026-08-22, skeptic+qa) — flagship pattern:
both HIGHs lived inside r1's own `urlHostAllowed` fix.** (a) Userinfo
authority bypass (`https://r.jina.ai:tok@evil.com/leak` read the host as
`r.jina.ai`); now parses the RFC-3986 authority (userinfo split on the
last `@`, then `:port`). (b) Nested-URL laundering — Go's single greedy
match allowlisted on the OUTER host while Python's `re.search` flags the
inner; the scan now tests every scheme occurrence independently, matching
Python (and keeping the legit r.jina.ai proxy shape clean). The r1
`.`-suffix subdomain widening was corrected to EXACT-match (Python flags
subdomains too — the widening diverged and was unneeded). Also: cost_
optimization/crystallization now HELD not action_failed (parity with
Python's known held categories); `evolver.triState` hardened to match its
inspector twin; `Revert` guards on `IsApplied` so it can't stamp
"reverted" over an honest held/failed status. NEW backport candidates:
(7) Python's exfil lookahead shares the userinfo bypass; (8) Python
`revert_suggestion` doesn't check applied-state. Fix-layer mutations
M91–M96 all DETECTED (compiling mutants).

**r3 fix-layer re-review (2026-08-22, skeptic+qa) — flagship a 3rd time.**
Two HIGHs: (a) r2 stamped cost_optimization/crystallization
`held_for_review` but Python uses `pending_human_review`, which the
shared-store operator dashboard (observe.py) counts — Go-touched rows
vanished from it; fixed to the Python literal. (b) Tab/newline-in-host
detector evasion (`https://evil<TAB>collector.com/x` fetched as
evilcollector.com by a WHATWG parser but read as host `evil` by the
scanner); fixed by stripping ASCII tab/CR/LF from each URL candidate.
MEDIUMs: recall.go was a THIRD unhardened goal_achieved reader (fixed to
match the twins); Revert returned Reverted:true even when the durable
store write failed (now reflects persisted state). One reviewer HIGH-
adjacent LOW was REFUTED by verify-before-fix (trailing-dot false-
positive can't occur — the exfil shape excludes it; the attempted fix was
dead code, removed). NEW backport candidate: (9) Python's exfil scan
shares the tab/newline-in-host evasion. Fix-layer mutations M97/M98/M100
DETECTED (M101 void — refuted finding).

**r4 fix-layer re-review (2026-08-22, skeptic+qa) — flagship a 4th time,
this one a Go-worse divergence.** HIGH: `https://evil.com\@host/x` scanned
clean in Go (authority split only on `/?#`, reading the pre-`@` part as
userinfo) while a WHATWG client — and Python's prefix-check — resolve the
host as `evil.com`; fixed by adding `\` to the authority terminator set
(restores parity). MEDIUMs: per-scheme URL scan bounded to 512 runes/
candidate (DoS on a no-whitespace many-scheme blob); EVOLVER_REVERTED
event now carries `persisted` so a type-keyed consumer isn't misled on a
non-persisted revert. Accepted-named residual: Revert removes the
constraint before flipping applied, so a persist failure leaves
constraint-gone-but-applied=true (Python parity, needs cross-file txn).
Fix-layer mutations M102/M103 DETECTED.

Backport-correction candidates (fork-point Python bugs the Go port fixed
or diverges from, for a later Python pass): (1) revert guardrail source-
key mismatch; (2) exfil URL lookahead prefix-only (lookalike domain);
(3) exfil URL userinfo (`@`) bypass; (4) exfil URL backslash-authority
bypass; (5) exfil URL tab/newline-in-host evasion; (6) revert doesn't
check applied-state; (7) EVOLVER_REVERTED logged even when not persisted;
(8) `inspection_finding` silently marked applied=true (no apply arm) —
Go holds it; (9) malformed `goal_achieved` graded as unjudged rather than
judged-false at FOUR sites — inspector.py, evolver.py (~155), metrics.py
(~576, feeds goal_achieved_rate), recall.py (~464); (10) exfil URL scan
requires `://`, missing ONLY the WHATWG slash-LIGHT forms (0 or 1 slash:
`https:host/x`, `https:/host/x`) a real client fetches — Python's `[^\s]+`
after `://` still absorbs the slash-HEAVY forms (`https:///host`), so
those are a Go-r5-regression, not a Python gap (see r6); (11) exfil URL
scan windows can be STARVED by ignorable padding at THREE nested levels,
all sharing the fork-point (Python has each blind spot too): the inner
512-byte candidate cap (leading slash/tab pad — r7), a mid-host tab/CR/LF
pad past that cap since WHATWG strips control chars whole-string not
per-URL (r8 skeptic), and the outer 50k `max_chars`/`scanMaxChars` content
clip (60k pad clips to pure padding, losing the host — r8 QA). Go now
closes all three (skip-before-cap, global control-strip, full-content URL
scan) and is MORE correct than Python at each. (12) evolver Apply coerces a
non-string / absent `suggestion` field to `""` before the guard and can
stamp a spurious empty lesson `applied=true` — a fail-open in the wrong
direction for the module's doctrine; SHARED with Python
(`d.get("suggestion","")`, no type/non-empty check), confirmed NOT an
injection bypass (the empty string is both scanned and stored), so named
here rather than fixed mid-convergence.

**r5 CONFIRMATION round (2026-08-22, skeptic+qa) — first zero-HIGH round.**
Both lenses traced the full WHATWG authority-shape checklist and VERIFIED
the parser sound (no manufactured bypass). Two non-HIGH fixes: scheme now
tolerates 0-2 slashes (the slash-light bypass — backport candidate #10;
the shape's host-first-char is `[^/\s]` so the scheme, not the host,
consumes the slashes, keeping the legit nested-proxy shape clean), and the
`urlCandidateMax` doc corrected (byte bound, intentional). The Revert
ordering residual was re-confirmed honest. Mutations M104/M105 DETECTED.
r6 must also be zero-HIGH for the two-consecutive-clean fixpoint.

**r6 CONFIRMATION round (2026-08-22, skeptic+qa) — NOT zero-HIGH; flagship
a 5th time, again inside the prior round's fix.** Both lenses independently
found the SAME HIGH: r5's `/{0,2}` slash cap was too narrow. WHATWG's
"special authority ignore slashes state" consumes an UNBOUNDED run of `/`
and `\` before the authority, so `https:///evil.com/x`, `https:////…`, and
slash/backslash mixes all fetch to `evil.com` — but r5's cap silently
un-matched them (no finding at all). Verify-before-fix confirmed the live
bypass AND that this made Go WEAKER than Python (Python's `[^\s]+` after
`://` absorbs the extra slashes and still flags them; the negative control
stays clean on both) — a parity regression, exactly the fix-layer the
watch-list said to attack. Fixed by widening the slash run to `[/\\]*` in
both `exfilURLShape` (host-first-char now `[^/\\\s]` so the run is fully
consumed, not absorbed into the host) and `urlHostAllowed`'s strip loop
(dropped the `n < 2` bound), restoring the unbounded grammar and Python
parity. 512-byte cap + RE2 linearity keep the unbounded `*` DoS-free.
must-detect pins added for 3+-slash and slash/backslash-mix forms plus a
slash-heavy allowlisted negative control; mutation M106 (revert to r5's
`/{0,2}`) COMPILES and FAILS the new pin — load-bearing. The two clean
confirmations from r5 (userinfo/backslash/tab parser, Revert honesty) held.
Candidate #10 reworded: the slash-heavy forms were never a Python gap.
r6's fix resets the fixpoint clock — r7 is now the first of two required
zero-HIGH rounds.

**r7 round (2026-08-22, skeptic+qa) — NOT zero-HIGH; the flagship pattern
mutates from grammar to WINDOW.** The skeptic (QA missed it — the multi-lens
payoff) found a HIGH one layer under r6: r6 made the leading slash run
UNBOUNDED, but the 512-byte candidate cap was still applied from the scheme
position, so `https:` + 600×`/` (or 600 tabs — control-strip also ran after
the cap) + `evil.com/leak` filled the whole window with ignorable prefix and
truncated the host away → scanned clean while a real client fetches it.
Verify-before-fix confirmed the live bypass; it also REFUTED the skeptic's
"Go-only regression" framing — Python misses it too (its `{3,50}` host bound
is the same blind spot), so this is a shared fork-point gap (backport
candidate #11), and Go's unbounded `[/\\]*` lets Go fix it to be MORE correct
than Python. Fixed by skipping scheme + the WHATWG ignore-slashes/control
run BEFORE the cap, so the 512 bytes bound the AUTHORITY window (prefix scan
is O(run), one run per scheme → total stays linear in the 50k-capped
content). must-detect pins added (600-slash, 600-tab, slash/backslash mix,
straddling the cap) + a padded-allowlisted negative control; mutation M107
(cap-from-scheme) COMPILES and FAILS the pin. QA's only finding was the LOW
Revert fault-injection test gap — CLOSED this round: a clean in-process hook
(pre-create the suggestions `.tmp` path as a directory → EISDIR on the store
write only, constraint write untouched) pins Reverted=false / Behavioral=true
/ applied-stays-true / retry-completes; mutation M108 (Reverted:true
unconditional) COMPILES and FAILS it. The r4-named "needs cross-file txn"
residual stands (the ordering is unchanged — Python parity), but its honesty
contract is now test-covered. Full suite green, gofmt/vet clean. r7's HIGH
fix resets the clock again — r8 is the first of two required clean rounds.

**r8 round (2026-08-22, skeptic+qa) — NOT zero-HIGH; TWO distinct HIGHs,
each a different level of the SAME starvation class.** The saga's lesson
sharpens: this hand-rolled parser keeps yielding one nested window at a
time. (a) skeptic HIGH — the r7 fix only skipped the LEADING ignorable run,
but WHATWG strips tab/CR/LF whole-string; `https://evil` + 600 tabs +
`collector.com/leak` stops the skip at `evil`, so the 512 cap truncates the
tabs+TLD before the per-candidate strip could collapse them, scanning clean
while a real client fetches evilcollector.com (the r3 tab-in-host class past
the cap). Fixed by stripping tab/CR/LF ONCE globally over a URL-only copy
before per-scheme slicing. (b) QA HIGH — the OUTER 50k `scanMaxChars`
content clip is itself a starvable prefix budget: `https:` + 60k slashes/
tabs + `evil.com/x` clips to 50k of pure padding, losing the host. Fixed by
running the URL scan over the FULL (control-stripped) `content`, not the
50k-clipped keyword target — no new DoS class (the keyword clip already does
`[]rune(content)` = O(content); RE2 linear; inner skip+512 cap bounds
per-candidate work). Both verify-before-fix'd live; both are shared
fork-point gaps (Python misses them too — candidate #11 now spans all three
window levels). must-detect pins added (mid-host tab/CR/LF, \r-only/\n-only
at cap scale per QA's MEDIUM, 60k outer-clip) + allowlisted negative
controls at each scale; mutations M109 (outer-clip: content->target) and
M110 (mid-host: revert to per-candidate strip) both COMPILE and FAIL their
pins, isolated. QA's other findings (r7 pins load-bearing, Revert
fault-injection test asserts-the-flow, three goal_achieved readers aligned,
urlHostAllowed in lockstep with the shape) all traced clean. r8's HIGHs
reset the clock — r9 is the first of two required clean rounds. NOTE: 7
straight rounds have each found a real, mutation-verified defect in this
hand-rolled URL/authority parser (grammar -> window -> nested windows). If
r9/r10 don't converge, the honest end state may be a spec-grounded URL parse
(net/url + explicit WHATWG normalization) rather than continued regex
hardening — flagged for a decision at convergence.

**r9 round (2026-08-22, skeptic+qa) — NOT zero-HIGH; the r8 fix's OWN safety
claim was false.** skeptic HIGH — the per-scheme loop did
`strings.ToLower(cand)` on the FULL unbounded suffix (a scheme-length guess)
BEFORE the 512 cap. Harmless while `target` was 50k-clipped, but r8's switch
to full-content scanning turned `https:`×N (no whitespace) into O(N) matches
× O(len) each = O(n²): measured 1.7s->6.5s->25.5s as the input doubled
(120k->240k->480k bytes), a real CPU/alloc DoS on the untrusted evolver-
suggestion path — and a direct FALSIFICATION of r8's "no new DoS class" note
(the cost sat BEFORE the cap, not after). Go-only (Python does per-pattern
search over a fixed 50k slice, no per-candidate ToLower). Fixed by taking the
scheme length from schemeRe's own match bounds (`loc[1]-loc[0]`, O(1)) — the
480KB case dropped 25.5s->0.21s, scaling linear. Pinned with a goroutine+10s-
ceiling regression test (fails fast on quadratic); mutation M111 (restore
ToLower) COMPILES and FAILS it at the ceiling. QA found NO parser HIGH
(traced the r8 fixes load-bearing via M109/M110 replays) and raised: a MEDIUM
coverage gap (no mid-host-pad negative control on an ALLOWLISTED host — ADDED
at both cap and outer-clip scale), the #12 fail-open (named above), and a LOW
absolute-byte-ceiling suggestion — DECLINED with reasoning: with the
quadratic closed the work is linear, and a finite ceiling would RE-INTRODUCE
the starvable-prefix class the r8 QA finding was about, just at a higher
threshold (the pre-existing unconditional `[]rune(content)` alloc is the
caller's size concern, not a new r9 regression). r9's HIGH resets the clock —
r10 is the first of two required clean rounds. The 8-round streak of real
defects in this hand-rolled parser stands; the spec-grounded-rewrite decision
is live if r10 doesn't converge.

**r10 round (2026-08-22, skeptic+qa) — ZERO HIGH; the streak breaks, the
parser converges.** First round BOTH lenses traced the URL/authority parser,
the r9 O(1) scheme-length skip, and the linearity clean with no HIGH or
MEDIUM — the class is genuinely sound (r9's lone HIGH was scaffolding, now
fixed). So the spec-grounded-rewrite question is answered in the negative for
now: 9 rounds of explicit WHATWG-normalization hardening reached a defensible
fixpoint; a net/url rewrite (RFC-3986, not WHATWG) would trade this for a
different divergence set, so it's NOT pursued. LOW notes all confirmed
non-issues or test-only: IPv6 port-strip / trailing-dot / NaN-confidence all
safe-direction and (NaN) unreachable since encoding/json rejects it; added the
missing `http:` (5-char scheme) must-flag fixtures and a KNOWN-GAP visibility
pin for the #12 non-string-suggestion fail-open (verify-before-fix confirmed
it writes an empty-text lesson + applies — accurate, named, pinned). Test-only
changes this round. r10 is the FIRST of two consecutive clean rounds — r11
must also be zero-HIGH to close the fixpoint.

**r11 round (2026-08-22, skeptic+qa, sonnet-medium fallback) — ZERO HIGH.**
Second confirmation pass over the r10 test-only tree. Both lenses re-traced the
full parser (fresh-bypass sweep of urlHostAllowed/exfilURLShape, DoS linearity,
Python parity, evolver honesty surface) and found no HIGH/MEDIUM. Three LOWs
logged and held: (a) urlHostAllowed uses Unicode strings.ToLower against a
pure-ASCII allowlist; (b) the URL scan loop breaks after the first flagged URL
(observability, not a bypass — hasExfil already forces high); (c) the r10
`http:` fixtures never combine with cap/starvation padding, so they don't pin
the O(1) skip. r10+r11 were provisionally the two-consecutive-clean fixpoint —
until r12 raised the tier and reopened it.

**r12 round (2026-08-22, skeptic+qa, opus-5 MEDIUM — tier escalation) — TWO
HIGH; fixpoint REOPENS.** Jeremy's call: escalate the reviewer tier instead of
grinding same-model fallback (rounds r6–r11 were sonnet-medium; codex capped
til 08-27). The higher tier found in ONE round two real defects six weaker
rounds missed — the flagship pattern again, both one layer UNDER every prior
fix:
  - **HIGH #1 (window-starvation of the exfil shape).** The shape's `{2,49}`
    host-span required the TLD within ~50 chars of the authority start, but
    that span also covers USERINFO (`user@`, client-discarded) and long
    SUBDOMAIN labels. Free padding pushed the real registrable host past the
    window, so the shape missed it and `urlHostAllowed` (which parses userinfo)
    was never consulted (the `||` short-circuit). Live bypasses, verify-before-
    fix confirmed: `https://r.jina.ai:tok<23×A>@evil-collector.com/leak` and
    `https://<60×a>.evil-collector.com/leak` both scanned CLEAN. FIX: bind the
    shape's host-span MAX to the candidate window (urlCandidateMax=512); the MIN
    stays {2} so the short-inner-host exclusion is untouched. Shared fork-point
    gap — Python's {3,50} has the same blind spot (candidate #11 now spans the
    userinfo/subdomain instance too). Residual: userinfo LONGER than the 512
    window still evades (host past the DoS cap) — accepted known-gap, pinned by
    `TestURLExfilUserinfoWindowKnownGap`, since closing it needs an unbounded
    authority scan = the O(n²) the cap prevents.
  - **HIGH #2 (Unicode case-fold bypass).** `strings.ToLower` applies Unicode
    simple folding: U+0130 'İ' → ASCII 'i', so `api.anthropİc.com` read as the
    allowlisted `api.anthropic.com` and scanned clean. FIX: `asciiLower` (byte-
    wise A-Z only) at both host and scheme-prefix in urlHostAllowed, and in
    sourceIsAllowed — a non-ASCII byte can never collapse into a pure-ASCII
    allowlist entry (safe direction). The two opus lenses DISAGREED on real-
    world reach (skeptic HIGH; qa noted IDNA disallows U+0130 so no client
    resolves it) — the fix is warranted regardless as fail-toward-flag, but the
    channel is nil today; ranked below HIGH #1.

Both fixes mutation-verified (M113 revert `{2,512}`→`{2,49}` fails the
userinfo/subdomain pins; M114 revert asciiLower→ToLower fails the U+0130 pin).
Also from r12, HELD/documented: MEDIUM — `new_guardrail`'s `pattern` field is
attacker-influenced, reaches `dynamic-constraints.jsonl`, and is executed as a
regex by Python but is NOT guard-scanned (only `suggestion` is); default-HELD
mitigates it (opt-in auto-apply only). Guard package-doc corrected to name the
scan scope; `pattern`-safety (length bound + ReDoS-shape rejection for Python's
backtracking engine) is a named follow-up (candidate #13). LOWs (b)/(c)/non-
ASCII-whitespace remain held. **The fixpoint COUNT RESETS: r13 must be the
first of two new consecutive clean rounds.** Standing lesson recorded: on
same-model fallback, escalate the tier after round 1 (feedback_reviewer_tier_
escalation) — r12 is the datapoint (one opus round > six sonnet rounds).

**r13 round (2026-08-22, skeptic+qa, opus-5 medium) — ONE HIGH; the two lenses
SPLIT on severity, adjudicated by coverage.** The higher tier again found a new
class one layer under: the exfil shape's payload anchor was a literal `.tld/`,
so a non-`/` delimiter or a percent-encoded structural char pushed a NON-
allowlisted `.com/.io/.net` host past the shape (the `||` short-circuit then
skipped urlHostAllowed). All verify-before-fix confirmed live from source
`"internal"`:
  - query string `https://evil.com?data=SECRET` (the canonical exfil carrier),
    fragment `#data=`, root-anchored FQDN `evil.com./leak`, `%2E`-encoded host
    dot, and `%3A%2F%2F`-encoded nested scheme laundered through r.jina.ai — all
    scanned CLEAN.
The lenses split: skeptic HIGH (these are within the detector's CLAIMED
`.com/.io/.net` coverage); qa MEDIUM (the whole reach is a bounded heuristic,
Python-parity, document don't chase). **Adjudication: FIX what's within claimed
coverage, DOCUMENT the inherent reach limits.** FIXES: (1) payload anchor
`/[^\s]{5,}` → `\.?[/?#][^\s]{5,}` (accept `?`/`#` delimiters + trailing FQDN
dot); (2) a whole-string percent-decode of structural delimiters (`%2e`→`.`,
`%2f`→`/`, `%5c`→`\`, `%3a`→`:`) in the URL normalizer, matching what a WHATWG
client does before parsing (the r8 "normalize once" precedent) — this restores
the r2 per-scheme catch on encoded nested URLs; (3) urlHostAllowed strips a
trailing FQDN dot so the allowlisted `.com.` form still clears. Mutation-
verified (M115 anchor revert, M116 decode removal both fail the new pins).
DOCUMENTED as inherent heuristic limits (candidate #14, shared with Python,
pinned by `TestURLExfilHeuristicReachKnownGap`): IP-literal hosts, TLDs outside
com/io/net, ≤2-char labels, <5-byte payloads — a host-allowlist exfil detector
cannot be complete, and the ledger now says so.

Also this round, from findings #2/#3: **the r12 userinfo-window known-gap is now
CLOSED**, not accepted. My r12 rationale ("closing needs an unbounded O(n²)
scan") was WRONG — a truncated candidate whose window holds no authority
terminator is O(1)-detectable and flagged FAIL-CLOSED (an oversized delimiter-
free authority is never a real URL; DNS caps at 253 bytes). Pinned by
`TestURLExfilOversizedAuthorityFlagged` (M117); the vacuous r12 negative control
(a 600×A allowlisted apex that "passed" only via that starvation) was removed —
it correctly flags now. Test-honesty fixes from qa: pinned the asciiLower
SCHEME-prefix and sourceIsAllowed sites (uppercase-scheme passes, homoglyph
source rejected — M118), corrected the http:-fixture comment (it pins schemeRe
coverage, not the O(1) skip), and gave Revert's missing-constraint-file branch
the same honest detail as the row-absent branch. The `pattern`-field MEDIUM
(candidate #13) remains HELD/named, re-confirmed by both lenses. **Fixpoint
clock RESETS again: r14 is the first of two new consecutive clean rounds.**
Meta-note: opus has now found a real HIGH three rounds running (r12 ×2, r13),
each inside the prior fix — the flagship pattern is unbroken and the regex-shape
detector is visibly at its brittleness limit; if r14/r15 keep surfacing anchor/
encoding gaps, the spec-grounded parse (deferred at r10) should be reconsidered.

**r14 adversarial round (2026-08-22, opus-5 medium fallback) — the decisive
self-regression round.** Every finding lived inside r13's OWN fix. The flagship
"HIGH is inside the previous fix" pattern held, but for the first time the
previous fix was mine (one round old), and r13 closed three gaps while opening
three:

1. **HIGH — r13's whole-string `%2f/%5c/%3a` decode was a false-ALLOW
   regression.** WHATWG percent-decodes only INSIDE the host, after literal
   `/\?#` delimit the authority — never whole-string. Decoding `%2f/%5c` whole-
   string invents an authority terminator, so a raw-flagged exfil
   (`https://r.jina.ai%2f@evil-collector.com/…`) normalized to a false-ALLOW.
   FIX: revert to `%2e`-only decode (a host-internal dot-encode that can't move
   the authority boundary). Standing invariant added: **normalization must be
   ADDITIVE — never clear a shape the raw text flags.**
2. **HIGH — r13's own anchor widening was out of lockstep with urlHostAllowed.**
   The host-check already terminated on `\` and stripped a `:port`, but the r13
   anchor (`\.?[/?#]`) covered neither. `evil-collector.com\leak` and
   `evil-collector.com:8080/leak` scanned clean. FIX: anchor →
   `\.?(:[0-9]{1,5})?[/\\?#][^\s]{5,}`.
3. **HIGH (safety-pin disabled) — r13's oversized-authority `break` silently
   made the 13-round r9 linearity pin VACUOUS.** The old fixture
   `strings.Repeat("https:", 200000)` is exactly the oversized-unresolvable
   shape → short-circuits after ONE iteration, so the r9 quadratic mutant PASSED
   it. FIX: surviving fixture `strings.Repeat("https:x/", 150000)` (every
   candidate carries an in-window `/`, so all iterations run and the mutant
   blows the 10s ceiling); old blob repinned for its SECURITY behavior; the
   false in-code "pins only the timing" comment corrected.

Trade accepted: the proxy-nested encoded launder r13 chased is REOPENED as
**candidate #15** (`TestURLExfilProxyNestedEncodedKnownGap`) — the decode that
caught it was the source of HIGH #1, so documenting the narrow gap beats a
whole-string substitution that breaks the common case. Four compiling mutants
verified (regression decode; anchor-`\`; anchor-`:port`; quadratic-per-
candidate), each failing on its exact intended pin; full suite green (22 pkgs).
**Fixpoint clock RESETS again: r15 is the first of two new consecutive clean
rounds.**

**STRATEGIC FORK (raised to Jeremy, not decided here).** Four consecutive opus
rounds found real HIGHs (r12 ×2, r13 ×1, r14 ×2), and r14's were all self-
inflicted by r13. The hand-rolled shape-regex + string-substitution approach is
now generating defects as fast as it closes them, and both lenses (twice)
recommend the spec-grounded authority parse deferred at r10. The current state
is CORRECT and stronger than r12 — but the next move is an architecture decision
(keep patching the heuristic vs. port a real WHATWG-ish authority parse), and
that is Jeremy's call.

**r15 — FORK DECIDED 2026-08-22: spec-grounded parse via a conformant
library.** Jeremy's call, with a decree-class correction on port philosophy
that now governs the remaining tranches: **seams-strict, internals-free.**
Data parity (shared ledgers, pack digests, import/export) is the contract —
"at the end, not along the way"; internals need not mirror Python's
implementation, and refactoring during the port is explicitly welcome.
Interface over strict equivalent types. (His same-session observation, recorded
for the maro roadmap: the port revealed that much of the "portable learning"
is reified as CODE — thresholds, gates, fix-history invariants — not data,
which is why lessons are being ported instead of imported.)

The swap: `internal/guard/urlscan.go` replaces the r10–r14 shape-regex +
hand-rolled authority parser with **github.com/nlnwa/whatwg-url v0.6.2**
(pure Go, passes web-platform-tests; module deps now yaml.v3 + whatwg-url —
the swipe-over-deps rule's security-parsing exception, invoked by Jeremy).
"What host would a real client fetch" is now answered by the fetch
standard's own algorithm; POLICY stays ours and unchanged (http/https only,
exact-match allowlist, .com/.io/.net + ≥3-char-stem + 5-byte-payload reach,
fail-closed on oversized/unparseable-truncated). Kept from the review arc,
because the corpus proved each is load-bearing: the whole-string tab/CR/LF
strip (r8), per-scheme candidate loop (r2), skip-then-cap window (r7), and
the oversized-authority branch (r13). The ENTIRE r10–r14 adversarial corpus
passed the swapped internals on the first run; the only diff was known-gap
**#15 flipping CLOSED** — encoded proxy-nested laundering is now caught by a
bounded decode-and-rescan that is additive by construction (it runs only
after the outer candidate parses to an allowlisted host with its authority
untouched, so the r13/r14 whole-string-decode false-ALLOW class is
unreachable). New coverage the regex could never reach, pinned:
IDNA/UTS-46 mapped hosts (`evil-collector。com`, fullwidth `ｃｏｍ` —
`TestURLExfilIDNMappedDotFlagged`; Python's regex misses these, so the Go
file is now the **backport reference implementation**, not a parity port),
double-encoded nesting, and the unparseable-truncated fail-closed posture
(`TestURLUnparseableTruncatedFailsClosed`). Perf: the parser costs ~65µs vs
~1µs per candidate, so a provably-additive prefilter
(`urlCandidateNeedsParse` — pure-ASCII/percent-free candidates whose bytes
cannot contain a reach TLD or allowlisted host skip the parse; soundness
argument in its comment) keeps the 150k-candidate linearity blob at 0.26s
(the parse-everything version consumed the pin's full 10s ceiling on this
box). Mutations **M97–M102** (prefilter-always-skip, nested-decode-off,
stem-floor, FQDN-trim, unparseable-fail-open, payload-floor) all DETECTED.
Candidate ledger after the swap: #13 (backtracking-engine follow-up) MOOT —
the shape regex it referred to is deleted; #14 heuristic-reach limits STAND
(deliberate policy, reach pin unchanged; widening reach — other TLDs, IP
literals — is now a one-line policy decision rather than a parser project,
flagged as such for Jeremy); #15 CLOSED (pin flipped to must-detect).

**r16 — adversarial review of the swap, fixed to green (2026-08-22, opus
skeptic fork).** The review confirmed the parser CORE sound (400k-case
differential fuzz on the prefilter: zero counterexamples; every r10–r14
evasion re-fired caught) but found two HIGHs + two MEDs + one LOW, ALL in
r15's new nested-rescan code, sharing one root: `evalURLCandidate` had two
callers with invariants written for only one (the flagship pattern's
Nth instance — fresh bugs in fresh code). Fixed STRUCTURALLY: one entry
point `evalRawCandidate` used by both the top-level scan and the nested
rescan (so every safety property holds for both), plus a shared per-scan
work budget (`scanBudget`: 4096 parses + 1MB decode, fail-closed on
exhaustion). HIGH-1 = O(k³) nested-fan-out DoS (32KB → 83s → 92ms); HIGH-2 =
nested rescan read the 512-capped candidate not the full raw payload
(truncation false-ALLOW — the r13 oversized attack wrapped in the proxy);
MED-4 = launder through non-allowlisted/out-of-reach proxy (rescan now runs
for any cleared host); MED-3 = payload floor measured on the PARSED
components, not the raw space-delimited tail; LOW-5 = corrected the false
"non-ASCII can never collapse into the allowlist" comment (the parser applies
a client's UTS-46 mapping — a host mapping onto an allowlisted host is a
genuine allowlisted fetch). Five new pins + M103–M107 all DETECTED; net line
count DROPPED (two paths → one). Verify-before-fix honored (reproduced the
two HIGHs + MED-4 before fixing). Re-review of r16 is the next round.

**r17 — r16 re-review (opus fixpoint round), one HIGH fixed
(befbd7f7).** The re-review confirmed everything in r16 held under hard
effort — DoS bounded (520KB/10MB/3.2MB/8.8MB all ~1s), parse-budget
fail-closed, prefilter sound (fresh 500k differential fuzz, 0 mismatches) —
and found ONE real HIGH, again in r16's own new code: the decode-budget
counter failed OPEN. `decode()` returned the input unchanged on exhaustion,
which `scanNested` read as clean; a percent-encoded inner scheme is invisible
to schemeRe until decoded, so draining the decode budget (via large no-`%`
payloads r16 charged) then appending an encoded exfil cleared a real
laundering URL. Fixed r17: a no-`%` payload is fully examined without charging
the budget (kills the drain), and budget exhaustion with `%` remaining flags
fail-closed (matching parse-budget/oversized posture). Pin
TestURLDecodeBudgetFailsClosed; M108 detected. This is bounded convergence,
NOT churn: across r15→r16→r17 the broken set shrank (2H+2M+1L → 1H → 1H) while
the fuzz-confirmed-solid set grew; each HIGH sat in the previous round's new
code, but the core retired at r15 stayed retired.

**r18 — CLEAN, FIXPOINT DECLARED (guard slice DONE).** Opus confirmation round
found no exploitable defect after hard effort (DoS bound, r17 decode
fail-closed, control-char host parity, prefilter soundness all re-verified).
The full arc: r10–r14 hand-rolled-regex CHURN → Jeremy's fork + seams-strict/
internals-free decree → r15 parser swap → r16 unified evaluator+budget → r17
decode fail-closed → r18 clean. A design change converted an open-ended defect
class into a bounded surface that closed in THREE fix rounds. One residual named
(WHATWG-grounded host vs a hypothetical lenient downstream parser — a design
premise since r15, shared with Python, not a code bug). Go is the backport
REFERENCE for injection_guard.py. NEXT: resume the deferred inspector/evolver
residuals + next tranches (heartbeat/projects/viz).

**Standing lesson banked (feeds [[feedback-review-to-fixpoint]]):** the r15→r16
step is the churn lesson's mirror image done right — r15 was a *design* change
(regex → parser) that retired the whole regex defect class, so its review found
bugs only in the small NEW surface it added, and those converged structurally in
ONE round instead of minting the next round's HIGH. The tell that separates
"grind more rounds" from "reconsider the design": are the new HIGHs in the same
brittle mechanism (churn — stop, redesign) or in a bounded new surface (normal
convergence — fix and re-review)? The dep swap moved the hard part into a
WPT-tested library; what's left is ours and small.

**Self-improvement slice 2 (2026-08-22, scans/graduation/verify tranche):**
Ports evolver_scans.py (fork-point, ~900 lines) + graduation.py (~500)
+ memory_ledger.verdict_trust + the VERIFY_LEARN_ARC V2/V3 cadence
lifecycle, composed in Python run_evolver's order. Four new packages:

`internal/record/verdict.go` — the §4 verdict-trust policy
(full/directional/neutral/excluded, VerdictConfidenceFloor=0.7,
closure_unverifiable excluded from ALL learning consumers) plus the
shared `GoalAchieved` tri-state; malformed goal_achieved reads
judged-NOT-achieved (the r3 twin semantics, candidate #9, now the
single policy site every scan consumes).

`internal/scans` — the five statistical scanners (calibration log,
step-costs lower-median 2x rule, quality drift with its
evolver-baselines.jsonl snapshot ledger and 3-consecutive alert,
canon candidates over canon_stats.jsonl + long-tier lessons with the
full exclusion battery, suggestion-outcome calibration over
suggestion_outcomes.jsonl), `RunStatisticalScans` fan-out with
per-scanner panic isolation, `ScanEvolverImpact` (±12h windows,
per-window min 3, trust-filtered stuck rates, NaN for insufficient
data, captains-log EVOLVER_APPLIED fallback), and
`VerifyAppliedSuggestions` — the V2/V3 pass: cadence verdicts
(confirmed/degraded/inconclusive) on every applied-unverified row,
authority asymmetry (auto-applied degraded → revert; HUMAN-applied →
degraded_needs_review, never auto-reverted, with a re-read-fresh
narrowing before the irreversible revert), inconclusive →
verify_extensions bump → park "unverifiable" at max_extensions, V3
per-class failure_class_rate windows from DATED diagnoses
(recorded_at stamp, else a lazy events.jsonl join on loop_id;
undatable rows dropped with the count announced), config knobs
verify_cadence_verdicts/min_post_apply/max_extensions/delta_threshold/
use_class_signal, EVOLVER_VERDICT captain's-log events, and
self_improvement_verdict rows in events.jsonl with the exact
observe.write_event field set (notify hook command named unported —
no Telegram in Go).

`internal/graduation` — lessons-are-data made concrete: the 9
failure-class graduation templates left Python CODE
(`_GRADUATION_TEMPLATES` dict) and became an embedded JSON data file
(`graduation_templates.json`) with a workspace override at
`<ws>/graduation-templates.json`. The override may tune category/
suggestion/confidence; `verify_pattern` is FORCED back to the shipped
copy with a named warning, and `VerifyGraduationRules` executes ONLY
`executablePattern(fc)` — the compiled-in embedded copy — never a
row- or override-carried string. That's the provenance lesson
("provenance = producer's bit") applied to shell execution, and it's
a NAMED BACKPORT CORRECTION: (11) fork-point Python shells out
whatever `verify_pattern` string suggestions.jsonl carries — anyone
who can append a ledger row owns a shell. Also: scan/propose with
count-desc sort, evidence dedup, AlreadyProposed substring dedup
(Python parity), pending-only rows + GRADUATION_PROPOSED events;
structural verification (10s `sh -c` timeout in repoRoot, passed =
exit 0 AND non-empty stdout, newest-APPLIED-row-wins per class,
empty repoRoot → honestly unavailable, resolved MARO_REPO_ROOT >
config graduation.repo_root); and the claim/ack notification state
machine on graduation-verification-state.json (LockedRMW, identity =
{suggestion_id, applied_at, passed}, 300s lease, Go takes event
claims only — notify claims stay Python's).

`internal/selfimprove` — the composition layer: `Cycle` runs
graduation verification → evolver.Run (scanners riding the batch via
the new `ExtraSuggestions` hook — the scans→evolver import cycle
resolved by injection, same batch/save/auto-apply position as
run_evolver's `suggestions.extend`) → graduation propose → the V2
verify pass. CLI: `maro evolve` gains -impact/-verify/-verify-apply,
new `maro graduate` (-min-count/-lookback/-dry/-verify).

Named unported (package docs carry the same lists): business-signal /
harness-friction / persona-gap / advisor / island / router-retrain /
skill-candidate passes, playbook.md append (returns with the playbook
port), Telegram notify hook, navigator divergence adjudication.
Mutations M119–M126: 7 DETECTED outright; M123 (drift 3-consecutive
→ 1) SURVIVED and was closed with a new negative pin
(TestScanQualityDriftSingleBreachStaysQuiet) — the human-applied
never-reverted property is doubly guarded (case arm + re-read), so
M121 is a deliberate two-line mutant defeating both.

**r1 adversarial review (2026-08-22, 3 serialized lenses:
security/parity/QA-concurrency) — 2 HIGHs + 6 MEDs fixed; the headline
invariant (embedded-only pattern execution) probed airtight.**
(1) HIGH parity: Go's verdict notify wrote only the events.jsonl half —
`self_improvement_verdict` is in Python's ESCALATION_FILE_EVENTS, so a
degraded_needs_review verdict never reached output/escalations.jsonl,
the decreed headless escalation surface; the authority-asymmetry
design's "surface for review" half silently didn't surface. Ported the
escalation-file writer (full payload, 2000-char field bounds).
(2) HIGH QA: two overlapping verify cadences (Go+Go or Go+Python) let
the losing revert overwrite a truthful `degraded` stamp with
`degraded_revert_failed` and fire a false BLOCKING alarm. Terminal
verification stamps are now FIRST-WRITER-WINS inside the store lock
(`StampVerificationChanged`), every side-effect append (calibration
outcome, EVOLVER_VERDICT event, escalation) fires only from the pass
that landed the stamp, and `Revert` returns a typed `NothingToRevert`
the verify pass treats as handled-elsewhere, never as failure. Extension
bumps are now an atomic RMW (`BumpExtensionOrPark`) — two concurrent
passes yield 1 then 2, park included in the same locked write.
Also fixed: captain's-log audience registry ported (EVOLVER_* /
GRADUATION_* rows now `audience:"user"` per Python's
USER_SURFACED_EVENTS — they were filtered out of the user lane);
numeric-STRING goal_verdict_confidence now parses like Python float()
(a "0.5" classified full-trust and flipped V2 window membership);
graduation's `tailLines` byte-bounded at 8MB (matching the scans
reader — it silently reopened the unbounded-read hole for
attacker-adjacent ledgers); empty-repoRoot cadences no longer touch the
claim/ack state file (they routinely wiped Python's dedup baseline to
`{}`, forcing GRADUATION_VERIFIED re-emits); RunGraduation's dedup
re-check moved inside the append lock (concurrent cadences
double-proposed a class) with GRADUATION_PROPOSED events fired only for
rows that actually landed; SaveSuggestions' content dedup moved inside
one locked RMW (the 81-duplicate bug reopened under concurrency);
malformed `applied_manually` now coerces with Python bool() truthiness
(a present-but-non-bool value zeroed to false and routed a HUMAN-applied
row into the auto-revert branch — the unsafe direction); rounding is
half-to-even (exact binary ties wrote 0.1563 Go vs 0.1562 Python into
shared files); pyRepr/pyVal give Python repr quoting (apostrophe classes
defeated cross-runtime content dedup) and None/0.0 prose parity; canon
candidates sort times_applied-desc; graduation candidate ties break by
first-seen class order.
NEW backport candidates: (12) the concurrency read-decide-act family —
Python's verify/graduation/save share every one of these windows
byte-for-byte (double-revert false alarm, double-propose, double-append,
lost extension bump, dedup TOCTOU), newly consequential because Go+Python
cadence overlap on one workspace is now a supported deployment; Go is
the reference fix. (13) Python `verdict_trust` — shared, no fix needed
(float() already parses strings); the Go side was the divergence.
Accepted residuals, named honestly: the `NothingToRevert` skip arm
guards an interleaving no deterministic test can force (loser's revert
completing before the winner's stamp) — its typed input is pinned and
the stamp half is closed by first-writer-wins; drift's prior-window
same-interval contamination under concurrent cycles and the >300s
claim-lease stall double-delivery remain SUSPECTED-unreproduced
(fork-point-shared shapes).
Fix-layer mutations M127, M129–M139 all DETECTED with compiling mutants
(M129's first form didn't compile and was rebuilt semantic; M134's first
run mutated the WRONG of two identical guards — VerifyGraduationRules'
own gate instead of the fix's — and was re-run against the fix site;
M128 deliberately not minted, see the residual above). Race detector
clean over scans/graduation/evolver.

**r2 fix-layer re-review (2026-08-22, skeptic+qa) — flagship pattern
again: both HIGHs lived inside r1's own fixes.** (a) r1's in-lock
graduation dedup used `Contains(old, fp)` over the WHOLE file where
Python's `_already_proposed` windows to the last `lookback` rows — any
class ever proposed was silently refused forever (executed repro: an
aged-out proposal + fresh diagnoses wrote 0 rows where Python writes 1).
The in-lock re-check now replays the ONE windowed field-scoped predicate
(`proposedIn`) the pre-check uses, atop a new `record.LockedTailAppend`
primitive (flock-held bounded-tail read + framed append) that also
retires the whole-file LockedRMW read the same commit had introduced —
r1 bounded `tailLines` against OOM and then re-opened the identical
lever three functions down (r2 MED-2). (b) r1's pyTruthy fix coerced
`applied` in `rowToSuggestion` but left `IsApplied` a typed assert, so a
row with `applied:"true"` was read applied by candidate selection and
not-applied by Revert's guard → `NothingToRevert` → the new skip arm
ate it as handled-elsewhere: a degraded revertible change stayed live
FOREVER with zero surfacing (worse than both Python, which reverts it,
and pre-fix Go, which at least alarmed). All behavioral `applied` reads
(IsApplied, Dismiss guard, re-apply guard) now share pyTruthy. Also:
the FIFTH verdict arm (human-took-authority re-check branch) was never
gated on `changed` in r1 — now gated like its four siblings; terminal
rows refuse ALL stamps (a VerifiedAt-only stamp slipped past the
Verdict-keyed refusal); coerceFloat rejects hex-float strings (Go
ParseFloat reads "0x1p-2"=0.25 → directional, Python float() raises →
full). Probed clean by execution: SaveSuggestions RMW atomicity +
verbatim tainted-line preservation, escalation payload field parity,
300→200 clip chain byte-identical, audience registry complete against
every Go-emitted event type, rates rounding/prose parity. Fix-layer
mutations M140–M141, M143–M146 all DETECTED (M142 not minted — the
fifth-arm gate guards the same undeterminizable interleaving as M128;
batteries now assert pattern UNIQUENESS before mutating, the M134
lesson). Known suite caveat: guard's TestURLScanStaysLinear can blow
its 10s wall-clock budget under -race on this box (perf-budget vs race
instrumentation, guard-slice-owned, passes without -race).

**r3 whole-surface review (2026-08-22) — SCOPE CHANGE by Jeremy's
directive: from this round on, every re-review takes the ENTIRE chunk +
accumulated fixes, not the latest diff** ("see if that helps the fixes
be less granular to the change and more wholistic"). It did: no HIGHs —
2 MED, 2 LOW, 3 INFO, and the lead MED was cross-cutting in exactly the
way diff-scoped rounds can't see. (1) MED: the r2 in-lock dedup window
was keyed to the caller's diagnoses-scan lookback where Python's
_already_proposed is a fixed 200 — `maro graduate -lookback 300`
re-suppressed aged-out classes and `-lookback 0` resurrected the r2
whole-file suppression outright (executed both-runtime repro); the r2
pins had used lookback=200, the one value where the windows coincide.
FIXED: `proposeDedupWindow = 200` shared by pre-check and in-lock
re-check, pinned at 300 and 0. (2) MED: the "whole-file RMW = OOM
lever" rationale was applied to ONE path of suggestions.jsonl while
eight sibling accessors (store merges, full-history dedup, LoadOutcomes)
stay whole-file. RESOLVED as a deliberate rule, not a point fix:
**bounded tail where the semantics are tail-N; whole-file where the
semantics are whole-file** (keyed merges and full-history dedup are
Python-parity whole-file by contract — bounding them would silently
DROP rows, worse than the read cost; oversized ledgers degrade Python
identically, a fork-point-shared scaling limit). Comments at all three
sites now state this rule instead of the overstated rationale.
(3) LOW: the r2 pyTruthy unification is a named DIVERGENCE, not parity
— fork-point suggestion_is_applied and the re-apply guard are strict
`is True`; Go's truthy read is the safe direction because Python's
strict guard lets a malformed applied:"true" row REPLAY its mutation on
re-apply (backport candidate #13). (4) LOW: VerifyGraduationRules'
display read of applied_manually aligned to Python bool() (the applied
GATE stays strict, matching Python's strict check there). INFO items
recorded, no action: blank-line window slots (Python counts them, Go
drops pre-window; abnormal input), LockedTailAppend boundary caveats
(now in its doc), Revert's merge re-marshals rows other merges keep
verbatim (cosmetic key reorder). Declared sound by execution: hex/inf
coerceFloat corpus (18 cases both runtimes), LockedTailAppend edges +
no-unlocked-writers grep, refuse-all terminal stamps vs every flow,
audience registry vs every Go-emitted type. Fix-layer mutations
M147–M148 DETECTED.

**r4 whole-surface review (2026-08-23): 1 MED + 2 LOW fixed, 10 INFO
recorded as divergence-ledger entries.** The MED is exactly the seam
class the whole-chunk scope exists for — invisible to any per-package
diff. (1) MED (executed repro): `selfimprove.Cycle` ignored
`report.Skipped` — Python's run_evolver early-returns on a quiet
interval (outcomes unreadable or < min_outcomes) BEFORE the graduation
propose and verify passes; Go ran both unconditionally, so a fresh/quiet
workspace holding an applied-unverified row rendered `inconclusive` off
zero window data each cycle and BumpExtensionOrPark walked it to a
TERMINAL "unverifiable" park before any evidence could exist (and
terminal stamps refuse everything after — the r2 fix made the damage
permanent). FIXED: skip gate between steps 2 and 3; step 1 (structural
graduation check) stays above it, Python parity. (2) LOW: coerceFloat
missed two Python float() edges, both flipping §4 trust in the UNSAFE
direction (malformed row earning FULL): bools (float(False)=0.0 →
directional) and out-of-range numeric strings ("-1e999" → -inf →
directional; Go's ParseFloat returns the same value WITH ErrRange —
a successful parse in Python's eyes). FIXED + 7-case trust-edge pin
(incl. +inf→full, underflow→directional, hex/garbage stay full).
(3) LOW: canon row target defaulted ""→"general" where Python's
`.get("task_type","general")` default is dead code (key always
emitted) — target feeds contentKey, so the same finding would mint one
row per runtime on a shared store. FIXED: verbatim TaskType.
INFO ledger (verified, deliberately not acted on — Go reads are the
tamer/safer side in each): floatField excludes JSON bools from
calibration/drift means (Python counts True as 1.0); suggestion-outcome
`verified` read strict-bool vs Python truthy; expectedClass non-string
falls to stuck-rate vs Python str() coercion; revert_failed escalation
rates carry an additive `reverted` key; ScanStepCosts formatting at
exotic float boundaries; loadCanonStats malformed-row readings
(""→general grouping, ""-tier→long, numeric lesson_id skipped);
impact captains-log fallback scans the 8MB tail without Python's
pre-limit (post-merge [:limit] converges); parseISO rejects date-only /
treats naive as UTC (Python can raise on naive/aware compare — Go
strictly safer); graduation absorbs Python-crash edges (non-string
loop_id, unhashable evidence); `-verify-apply` without `-verify` is
silently ignored (UX nit). Fix-layer mutations M149–M152 all DETECTED
(uniqueness-asserted). r4 confirmed SOLID under attack: embedded-only
pattern execution (no ledger→sh -c path), template data byte-parity
(9 classes incl. derived expected_signal), first-writer-wins +
refuse-all stamps across all five arms, dedup-window unification at
every call site, verify pass arm-by-arm parity walk, audience registry,
escalation payload/clip chains, claim/ack state machine.

**r5 confirmation round (2026-08-23): FIXPOINT — 0 HIGH, 0 MED, 1 LOW
fixed, INFO-only otherwise.** The LOW was the third and (family now
understood) plausibly last member of the content-key prose family: the
calibration low-confidence reason rendered the threshold via `%g` →
"(<6)" where Python interpolates the raw float → "(<6.0)". The reason
IS the suggestion text and suggestion is a third of contentKey, so one
shared calibration.jsonl minted one duplicate row per runtime — the
same failure as r1's pyRepr apostrophe and r4's canon target. FIXED
with pyVal (Python's float repr) + a byte-for-byte prose pin and a
non-default-threshold pin; `%g` now appears nowhere in shared prose
(the one remaining use is verbose stderr). Mutations M153–M154
DETECTED. New INFO ledger entries: ScanSuggestionOutcomes confidence is
a third floatField-family site (string/bool → 0.5 default vs Python
float()/row-drop); unicode-digit confidence strings ("٠.١" parses in
Python, not Go) — found by a 36-case executed cross-runtime corpus that
agreed on every OTHER label including all the r4 bool/ErrRange edges;
graduation template substitution is sequential ReplaceAll vs Python's
simultaneous .format() (prose-only, and an unknown placeholder in an
override KeyErrors Python's whole pass while landing literally in Go);
degenerate CLI args clamp in Go where Python honors them; outcomeTS
non-string falls through to created_at. Named as fork-point-shared, NOT
a Go bug: an explicit `maro evolve -verify -verify-apply` on a quiet
workspace can still walk rows to a terminal park off zero window data —
Python's CLI makes the identical direct call; the r4 gate closes the
ambient cadence path, which is exactly what Python's control flow does.

**Arc shape: 2H → 2H → 0H/2M → 1M/2L → 1L. FIXPOINT DECLARED.** The
convergence criterion held in the healthy direction throughout — the
broken set shrank every round while the confirmed-solid set grew (r5's
is the longest of the arc: embedded-only pattern execution, template
byte-parity, stamp discipline across all five arms, dedup windows at
every call site, the verify pass arm-by-arm, audience registry,
notify/escalation clip chains, claim/ack state machine, calibration
override compare, impact scan format specifiers). Verify-before-fix
held at 0% hallucination across all five rounds — every finding's code
claim reproduced before a line was changed. Jeremy's whole-chunk scope
directive (r3 onward) earned its keep twice: r3's dedup-window-keyed-
to-the-wrong-arg and r4's Cycle/report.Skipped seam were both invisible
to diff-scoped review.

### Skill library — slice 3a: store + retrieval (2026-08-23)

The half that makes a skill REACH a run: `internal/skills` (types +
store + match) and the loop wiring that injects matched skills into the
decompose prompt and records the run-keyed manifest. Ports skills.py's
store/retrieval surface and all of skill_types.py.

**Lessons are data, and the admission predicate is the contract.**
skills.jsonl is a shared workspace store, so the load-bearing question
is not the struct but what a row must PROVE before it is admitted as a
Skill. `ValidateSkillRow` checks the STORED value, never what the
constructor made of it — Python's own arc took six adversarial rounds to
land there, and each rule is a probed finding this port inherits: a
forged row carrying a healthy skill's `content_hash` but
`description=7` could not have its hash recomputed, counted as "not
stale", won the dedup on a later `created_at`, and DELETED the healthy
skill. `content_hash`/`created_at` ABSENCE is refused (absent is not
empty, and neither is proof — both are fields a destructive caller acts
on, and both are excluded from the dedup identity, so neither absence
shows up as a difference). The tolerant `DictToSkill` stays for read
paths; degrading them would be a behaviour change nobody asked for.

**`record.LoadsClean` — the shared byte-hygiene door.** New primitive
porting jsonl_utils.loads_clean: refuses raw non-UTF-8, lone-surrogate
ESCAPES (a pure-ASCII line whose parsed value is tainted), trailing data
after the object, and DUPLICATE NAMES (both decoders silently keep the
last value, so `{"applied":false,"applied":true}` reads as applied and a
rewrite destroys the other value — two values where one is discarded by
an implementation detail is a corrupt row). Numbers decode via
UseNumber so an int literal stays distinguishable from a float, which
the validator depends on (7 vs 7.0). Go needs the writer-side twin MORE
than Python: encoding/json rejects NaN/Inf (matching allow_nan=False)
but silently rewrites invalid UTF-8 to U+FFFD, so taint would be
laundered at the WRITER with no error at all. Named divergence: Python's
json.loads accepts the non-standard NaN/Infinity tokens and its
validator then rejects the row; Go refuses the line outright — same
outcome (the row strands), different door. `pack` and `knowledge` carry
older partial twins (utf8-only, surrogate-only); unifying them is a
follow-up, not a silent mid-feature refactor.

**Two proofs, deliberately different strengths.** `proveLine` (live
store) proves the FULL admission predicate — a constructible Skill with
`tier=7` (hash-excluded, JSON-clean) was emitted by Python, REPLACED the
healthy row, and stranded on the next load. `proveRecordLine` (archive)
proves only that the strict reader admits the line, because the archive
is a RETENTION store: demanding full validity there would let a
hash-less skill refuse its own retention copy, and since the archive
gates the live-pool removal, an over-strict proof would block the cull
(or, for a caller that swallows the error, delete without a copy). The
first cut had this wrong in the strict direction and a test caught it.
Also fixed at the serializer: a Go-built Skill literal leaves slices
nil, and nil marshals to `null` — a row BOTH validators refuse. Python's
dataclass defaults make those `[]`, so the writer normalizes rather than
asking 30-odd call sites to remember.

**Retrieval** is Python's tier ladder minus the trained router: keyword
(trigger overlap either direction, plus tags as substring-in-goal only)
→ TF-IDF fallback (smoothed IDF, island-match boost, cosine). Winners
carry `match_method`/`match_score`, and the caller's telemetry reports
`{method, n_candidates, top_score, scores}` with method "none" when
nothing matched — the graded gap signal. NAMED GAP: router.py is not
ported (no Go consumer), so this is the path Python takes on an
untrained workspace — identical there, a real difference on a trained
box; the "router"/"mixed" tier stamps stay defined so the field means
the same thing in both runtimes' records.

**Loop wiring:** matched skills ride the decompose system prompt beside
recall's extras, and `runs.AppendSkillsManifest` records what actually
entered the prompt — UNCONDITIONALLY, empty match set included, because
absence must mean "the recorder did not run" and not "nothing matched"
(a cold store legitimately matches nothing, and that is a data point).
NAMED GAP (3b): Python routes each match through
`select_variant_for_task` first, so a parent can be swapped for its
active A/B challenger; variants are excluded from candidate discovery in
both runtimes, so the arms stay exclusive either way. Project isolation
is implemented and pinned in `FindMatchingSkills`, but the Go loop
resolves its project slug only in exec mode AFTER planning, so the
caller passes the empty slug — Python's own documented legacy behaviour
(filtering disabled), not a difference in the filter.

**Cross-runtime, executed live (not asserted):** a Python-written row is
admitted by Go with a byte-identical recomputed `content_hash` (pinned
as a fixture in `store_test.go`), and a Go-written store loads in Python
(`skills.load_skills`) with `compute_skill_hash` agreeing and
`find_matching_skills` returning the SAME skill, tier and score (keyword
/ 2.0) as Go's matcher on the same goal. The TF-IDF fixtures pin
Python's ACTUAL output (b=1.2, a=1.0 on a three-doc corpus) rather than
Go-derived expectations, including the degenerate case both share: a
one-skill (or all-identical) corpus scores zero everywhere, because
df==N zeroes the smoothed IDF — so the TF-IDF tier is silent on a cold
workspace rather than injecting a spurious match.

### Skill library — slice 3b: lifecycle (2026-08-23)

The half that makes a skill EARN its place: outcome recording
(`stats.go`), the utility EMA and circuit breaker, A/B variant routing,
the frontier gate and skill-decision provenance (`utility.go`), plus the
loop wiring that routes each matched skill through its active challenger.

**The stats store repeats the skills store's argument, and pays the same
prices.** `skill-stats.jsonl` is a keyed store that REWRITES itself on
every counter bump, so the whole admission argument applies again:
`validateStatsRow` proves the RAW values the coercing constructor would
otherwise launder, unprovable rows are STRANDED verbatim rather than
dropped, and the read announces what it excluded. Three failures from
Python's arc are pinned as tests here: ONE torn byte used to empty the
map and the next bump rebuilt the file from nothing (probed live in
Python: 4 lines → 1); a routine injection bump flipped a stored
`"false"` string to JSON `true` because the updater does not recompute
that field; and an operator's hand-added note was deleted by every
counter update until the write became a merge OVER the stored row.

Two shapes are load-bearing and easy to get wrong:

- **Strandees ride FIRST.** With them at the tail, a same-id stranded row
  overrode the repaired one for any naive last-row-wins parser — the
  rewrite "preserved" the corruption in the winning position.
- **A batch is ONE transaction.** `RecordSkillInjectionOutcomes` takes
  the whole manifest under one lock: a per-id loop made a mid-list
  failure a reachable partial batch, and the retry then double-counted
  the prefix.

Evidence must arrive as evidence, so a non-encodable id or non-finite
telemetry is refused AT THE DOOR — before the lock, before `MkdirAll`,
before anything is touched — and the error is RETURNED, never swallowed:
an earlier Python version warned and returned normally, so a disk-full
lost the outcome while the caller proceeded as if evidence existed.
The whole read-modify-write runs under the store lock with require
semantics; a transaction that cannot lock refuses to run, because a
fail-open lock over a whole-store RMW is the classic lost update.

**Counters that measure different things stay separate.** The legacy
`total_uses`/`successes` pair credits keyword-matched bystanders at a
~1.0 base rate; `injected_*` counts closure-verdict outcomes for skills
that were ACTUALLY in the run's manifest. An injection verdict never
touches the legacy pair, and `FrontierSkills` gates on `injected_runs`,
NOT `use_count` — that field's only writer was removed after it turned
out to have no caller, so it sat at 0 for 312 of 314 live skills and
silently starved the whole variant subsystem behind it.

**A/B routing is the full sha1 digest mod pool size** (Python's
`int(sha1(...).hexdigest(), 16) % len(pool)`), read as a big.Int. A
truncated prefix routes differently, and the arms must agree across
runtimes or the evidence is not comparable — pinned against Python's
actual buckets for three keys at two pool sizes. This closes slice 3a's
named variant-routing gap: `injectSkills` now routes each match and
records the ROUTED skill in the manifest, while match tier and score come
from the MATCHED parent, which the challenger inherits as its selection
evidence.

**NAMED DIVERGENCE (backport candidate #14):** Python captures
`old_utility` AFTER applying the EMA, so every `SKILL_CIRCUIT_*` event it
logs reports `utility_before == utility_after`. Go captures the real
prior value. The stored EMA is identical — the fix is in the reporting —
but a log line claiming an unchanged utility across a circuit trip is
misleading evidence, and evidence is the point. The 8-step EMA/circuit
trace is pinned against Python's full-precision `repr()` output, compared
EXACTLY rather than to a tolerance: the EMA compounds, so a rounded pin
would hide an arithmetic-order divergence for several steps.

Mutation battery: 25 must-detect mutations derived from the FILES (each
site asserted unique before mutation), 25 killed.

### Skill library — r1 review fixes (2026-08-23)

One HIGH, three MED, six LOW from the slice-3a adversarial round. Every
claim was verified against the code before any fix; none were
hallucinated.

**H1 — unbounded recursion in `record.LoadsClean` (fixed).** The
duplicate-name walker recursed once per nesting level, and it runs BEFORE
`Decode`, so nothing had bounded the nesting yet — `encoding/json`'s own
`maxNestingDepth` lives in the scanner, which `Token()` does not drive
for delimiters. Reproduced: depth 1.5M returned an error, 2M took an
unrecoverable `fatal error: stack overflow`. Not "a skill is lost" — the
BINARY dies, before decompose, on every run, until someone hand-edits the
store, and `LoadsClean` is the shared primitive every keyed store points
at. Python strands the same line and lives. Now an ITERATIVE walk over an
explicit stack, with the deep-nesting and nested-duplicate cases pinned.

**M2 — the load's losses reached no one (fixed).** `LoadResult` carried
`Drifted`/`Unparseable`/`HashMismatch` and no caller read any of them, so
half a library going byte-tainted looked exactly like "nothing matched" —
which is also what a healthy cold store looks like. `LoadResult.Announce`
now renders them and `injectSkills` puts them on the run's warning rail
(which reaches both loop exits). Same finding's second half: any
`ReadFile` error returned an empty result, making a permissions or EIO
failure indistinguishable from a cold install; `Unreadable` now
distinguishes them. `injectSkills` also reads the store ONCE now instead
of twice.

**M3 — skills rode the decompose prompt in the wrong position (fixed).**
Python's extras order is `[skills, ancestry, lessons, cost]`
(planner.py:962); Go appended skills LAST. Not a crash — a silent A/B
confound, since the port's premise is that a goal planned by either
runtime sees the same prompt. Pinned with both blocks present.

**M4 — the writer was stricter than its own reader (fixed).**
`isCleanText` refused a literal U+FFFD that `LoadsClean` admits, so such
a skill could be loaded and injected but never updated again (in 3b:
every outcome and utility write fails permanently), and because
archiving is all-or-nothing, ONE of them blocked an entire cull. The
justification — "the store has no legitimate producer of it" — is false
for skills minted from run output carrying web-fetched text. U+FFFD is
now admitted; raw non-UTF-8 and surrogate code points, which cannot
round-trip at all, still are not.

**Lows fixed:** integer literals no longer route through `float64`
(MaxInt64 became a NEGATIVE counter, and these fields drive the circuit
breaker and A/B); `SaveSkill` takes a POINTER so the caller sees the
content_hash it computed (Python mutates in place, and a manifest entry
built from a just-saved skill would have carried an empty hash);
`MatchOptions` gained an explicit `RestrictToIDs` flag, because a Go
caller building ids from an EMPTY manifest holds a nil slice and the
nil-vs-empty test would have scored the whole library; the matcher's
`note` closure now RETURNS its telemetry rather than being read as a
second operand in the same return statement (gc evaluates left-to-right,
the spec does not promise it); `archived_at` uses the port-wide stamp
instead of RFC3339Nano.

**Lows documented instead of fixed:** the `created_at` divergence is
wider than "odd offsets" (ISO basic format, week dates, hour-only,
lowercase `t`, hour 24, compact offsets — all Go-strands-what-Python-
admits, and nothing either runtime MINTS is affected), and the nesting
depth at which the two doors refuse differs (Go at 10000, CPython
around 2M).

**INFO acted on:** the NaN "divergence" was not one — Python refuses
those tokens at the same door via `parse_constant`, so the comment and
this doc were wrong, not the code. `Skill.ToDict` was dead surface
duplicating the key-order contract; it is now the SINGLE source, via a
`MarshalJSON` that emits through the ordered marshaller. That also fixes
the float spelling: Go wrote `"success_rate":1` where Python writes
`1.0`, and `success_rate` is IN Python's dedup identity
(`doctor._dedup_identity` re-serializes the PARSED row with
`sort_keys=True`), so Python's skills dedup silently stopped collapsing
rows once Go had written any. Line SPACING still differs — Python uses
`json.dumps`' default separators, every Go store in this port writes
compact JSON — but that consumer cannot see spacing, and it is a
port-wide question for a pass over every emitter, not a skills one.

**Deliberately NOT ported yet (next tranches, in rough order of value):**

1. ~~Memory recall + knowledge injection~~ — ranked-lesson +
   prior-attempt slice DONE (recall tranche above); playbook, edges,
   and the other named substrates return with their consumers.
2. ~~Closure verification~~ — evidence pipeline + verdict stamp DONE
   (closure tranche above); the quality-gate half (step-level review)
   and the restart consumers return with their subsystems.
3. ~~Intent routing + NOW-vs-AGENDA lanes~~ — DONE (routing tranche
   slice above); ~~director's plan/delegate/review~~ DONE (director
   slice above); the evaluate_closure decision layer + check-in/
   escalation machinery remain (they return with restart machinery).
4. ~~Inspector/evolver self-improvement loop~~ — slice 1 DONE
   (inspector/evolver tranche above); ~~statistical scanners,
   graduation, V2 verify lifecycle~~ — slice 2 DONE (self-improvement
   slice 2 above); ~~skills store + retrieval~~ — slice 3a DONE;
   ~~outcome recording, utility/circuit updates, A/B variants,
   frontier gate, provenance~~ — slice 3b DONE; ~~promote/demote,
   island culls, the destructive pool rewrite~~ — slice 3c DONE (all
   three skill-library slices above). Remaining from skills.py: the LLM
   mint path (`extract_skills`, `create_skill_variant`,
   `retire_losing_variants`, `attribute_failure_to_skills`,
   `load_skill_provenance`) and the Phase-14 test gate
   (`generate_skill_tests`, `run_skill_tests`,
   `validate_skill_mutation`). Then sub_mission (`mission.py` +
   `handle_queue.py`) — whose project-ledger substrate `orch_items.py`
   is DONE, see the project-ledger slice below — then the playbook.md
   port (unblocks guidance-only guardrails + graduation playbook
   appends).
5. Heartbeat, projects, escalation, notifications, viz.

**Named smaller gaps, accepted for v0** (adversarial round 2026-08-22 —
4 lenses, sonnet-medium fallback; each of these was flagged and either
fixed or parked here honestly):

- Subprocess liveness/stall kill: only the wall-clock timeout exists;
  Python `_run_subprocess_safe`'s mtime/CPU-stall detection is not
  ported. Output capture is disk-backed, so memory stays bounded.
- `cost_usd` estimation: rows omit the field rather than lie `0.0`.
- `config.Get` type mismatches beyond int↔float fall back to the
  default silently (same as Python); a warnings channel like `Load`'s
  is deferred until a caller needs it.
- `Result.Warnings` (record-write failures, unparseable result-shaped
  lines) reach the operator via the CLI's stderr print only — there is
  no durable warnings sink. Deliberate, not deferred laziness: the
  warning's usual cause is a failing store write, and any durable sink
  would live in that same workspace, so "durably record that the store
  is failing, in the store" cannot be honest. A headless supervisor
  should capture stderr; the code comments now claim exactly this much
  and no more (adversarial r3 2026-08-22, Skeptic).
- `jsonx` with NO fence present still takes the first balanced bracket
  in prose — shared verbatim with Python `_find_json_bounds`; fixing it
  is a cross-runtime change, not a Go patch.

### Skill library — slice 3c: selection pressure + tier lifecycle (2026-08-23)

`pool.go`, `island.go`, `promote.go`. The whole slice hangs off one
primitive, `SaveSkills` — the destructive rewrite of the skill pool —
which carries six adversarial rounds of Python's arc (r16–r21). The
contract is the slice:

- **A deliberate DROP must be NAMED.** Every caller builds its list from
  an UNLOCKED load, so reading "proven row absent from the list" as
  "deliberately deleted" destroyed any skill a concurrent process saved
  in between, with no archive copy. Absence now means CARRY.
- **A deliberate WRITE must be NAMED too.** Naming only drops still let a
  row from the caller's STALE snapshot replace the live row wholesale, so
  a concurrent save was reverted by any unrelated caller that loaded
  before it and saved after it.
- **Naming is not creation.** A named id with no live row is a lost race
  with a deliberate drop; appending it resurrected a retired skill with
  none of the retirement's reasoning. It is dropped and ANNOUNCED.
- **The caller's list cannot represent a row it could not parse**, so a
  naive rewrite from that list DELETES every torn line. The store is
  re-read under the lock and every unaccountable line rides through
  verbatim, holding its ordinal (this store is read last-row-wins by id,
  so moving a row promotes it).
- **Contradictory intent is refused before the lock**, store untouched.

Islands are the FunSearch diversity mechanism: keyword assignment into
research/build/analysis/general, then a cull of the bottom half of each
island's OPEN-CIRCUIT skills by compactness-adjusted utility. The archive
lands BEFORE the pool rewrite and its error ABORTS the cull — a crash
between the two leaves a harmless duplicate; the other order loses the
skill. Promote/demote gate on live `SkillStats` (the legacy `use_count`
writer was removed in Python as dead code, which left the gate reading a
permanently-zero counter — no skill promoted for eight weeks while the
store grew to 376 provisionals). Verdict-grounded injected evidence can
VETO a promotion whose inflated legacy counters look good.

Two self-inflicted bugs found while wiring it, both worth recording:
`Recorder.Event`'s last parameter is `loopID`, so passing `"skill:<id>"`
there wrote a fabricated run id AND lost the linkage — `EventRelated`
now takes `relatedIDs` explicitly. And `record.Locked` is NOT reentrant
(flock is per-open-file-description, so a nested call from the same
process blocks against ITSELF until the deadline); the promotion sweep
needs one critical section spanning reload→rewrite, so `SaveSkills` was
split into a public locked wrapper plus a `saveSkillsInLock` core, and
the hazard is documented on `Locked` itself.

**Store-shape divergence found and fixed while porting**: this port was
appending provenance rows to `memory/skill_provenance.jsonl`, a file no
Python reader ever opens — `load_skill_provenance` globs SIDECAR files at
`memory/skill_provenance/{skill_name}_{stamp}.json`. Every Go cull and
demotion was filing its reasoning where the other runtime could not see
it, while Python's audit answered "no provenance" for skills this runtime
had retired with a documented reason. Now byte-identical to Python's
`json.dumps(record, indent=2)` output, verified by live differential in
both directions (Go writes → `load_skill_provenance` reads it; Python
writes → identical bytes modulo the timestamp).

46 mutations derived from the three files, all killed.

### Skill library — r2 review fixes (2026-08-23)

Whole-chunk round (3a + 3b + r1 fixes), per the whole-chunk directive.
One HIGH, five MED, seven LOW; every claim verified before any fix.

**H1 — the TF-IDF tier was nondeterministic (fixed).** `vec()` and
`cosine()` summed by ranging over Go MAPS. Go randomizes map iteration
per range and float addition is not associative, so `dot`, `na` and `nb`
differed between two calls on the SAME inputs. The ranking sort is
stable, so an ulp flipped which of two tied skills was injected —
measured at 2302/698 over 3000 identical calls on one pool. Every
injected-outcome counter and every A/B conclusion built on them was
attributing verdicts to a coin flip. Vectors now carry their keys in
FIRST-OCCURRENCE order and every sum walks that slice, which is exactly
Python's dict insertion order — bit-exact parity, not merely
determinism. The regression test's corpus was chosen by falsifier: the
obvious one flipped on 3 of 400 pre-fix calls and would have passed by
luck, so the shipped one (varied `tf`, twelve overlapping documents
varying `idf`) flips on 128 of 400.

**M1 — `SkillsNeedingEscalation` returned a set DISJOINT from Python's
(fixed).** Go read the STORED `needs_escalation` flag; Python recomputes
from `success_rate`. The flag and the rate drift apart by design — the
injection recorder deliberately does not recompute it, and a legacy row
predating the field defaults to false — so Go missed exactly the
low-rate legacy rows the redesign bar exists for, and flagged a
stale-true row with a healthy rate.

**M2 — `FrontierSkills` was missing the frontier (fixed).** It kept only
the `injected_runs` gate, so it handed the evolver every skill with
enough runs including 100%-success ones, in map order. Three further
halves were missing with the band: the open-circuit skip (those belong
to `SkillsNeedingRewrite`), the ascending sort, and the fact that a
challenger IS eligible — excluding it looked principled and is not what
Python does. The old test pinned the divergent behaviour, so it was
replaced rather than patched.

**M3 — a counter bump silently RESET a skill's evidence (fixed).** When
a stats row is present but unprovable it is stranded — and strandees ride
FIRST in the rewrite. So minting a fresh zeroed record for that id put
the reset row LAST, where it won the last-row-wins keyed read in BOTH
runtimes: `injected_runs` 5 → 1 from one routine bump. The read now
recovers stranded ids and the mint REFUSES over one, naming the repair.
A batch aborts whole rather than recording part of a verdict set.

**M4 — the validator admitted rows Python strands (fixed).**
`strings.TrimSpace` uses `unicode.IsSpace`, which omits U+001C–U+001F;
Python's `str.strip()` counts them (measured: the only difference between
the two sets over the whole rune range). The strip drives a REFUSAL, so
this was the unsafe direction — a row Go admits becomes eligible to be
matched, replaced and archived-and-removed while Python carries it
verbatim forever. Now `pyStrip`.

**M5 — the destructive rewrites were not fsynced (fixed).** Python's
twin goes through `file_lock.atomic_write` (mkstemp, write, FSYNC,
replace, mode preserved). Three unsynced temp+rename copies had grown
across this port, and every one of them replaces a WHOLE store, so a
power loss in the rename window loses the store rather than one appended
line. Collapsed into `record.AtomicWrite`. Residual, recorded: the
directory entry is not fsynced — Python does not either, and the failure
mode there is "the old file is still there".

**Lows fixed:** an empty id in an injection batch now refuses the whole
batch instead of silently skipping it (Python raises; recording the rest
writes a verdict set that does not match the run); collapsed duplicate
ids are announced rather than shrinking the denominator in silence;
nested non-finite numbers in the free-form `imported` blob are refused,
matching `json.dumps(allow_nan=False)`, where before only the TOP level
was checked; `AppendSkillsManifest` was the one writer still on plain
`json.Marshal`, so it alphabetized keys, HTML-escaped `< > &`, and
spelled `2.0` as `2` on the cross-runtime attribution rail; and
`pyLower` handles U+0130, the only unconditional multi-rune lowercase
mapping in Unicode, which changes both a stored tag and the matching
corpus.

The emitter family moved into a new `internal/pyjson` package for that
last fix — the three ways `encoding/json` differs from `json.dumps`
(sorted keys, HTML escaping, bare whole floats) kept reappearing one
package at a time, and `Ordered` also no longer appends into the
caller's key-order slice (safe today only because every literal happens
to have `len == cap`).

**Lows documented rather than fixed:** `success_rate` is the one float
Python does NOT coerce in `dict_to_skill`, so a stored int `1`
round-trips as `1` there and widens to `1.0` here — the inverse of the
r1 float-spelling fix, hitting the same dedup consumer, and unreachable
without a hand-edited or pack-imported row. Non-ASCII escaping
(`ensure_ascii=True` in Python) and nested `imported` key order remain
byte-level differences that no consumer reads. 27 further runes
lowercase differently purely because Go's and CPython's Unicode tables
are at different revisions; those move with the toolchains.

13 further mutations derived from the fixed files, all killed — 59 for
the chunk.

### Project ledger — slice 1: NEXT.md and its neighbours (2026-08-23)

`internal/orch` ports Python `orch_items.py`'s ledger half: the NEXT.md
checklist, the DECISIONS/RISKS/PROVENANCE section files, the PRIORITY
value and the lifecycle markers, plus global selection over all of them.
This is the substrate `sub_mission` and the goal queue sit on, so it
comes first.

**Why a Markdown ledger needs more care than a JSONL store.** An item
here has no id. Its identity is its LINE NUMBER, so "where does a line
begin" is not a formatting question — it decides which item a mark
flips. Three measured Python behaviours drive the whole package, and in
all three the obvious Go call gives a different answer:

- **`str.splitlines()` breaks on ten separators**, not one: `\n \v \f
  \r \x1c \x1d \x1e \x85 U+2028 U+2029`, with `\r\n` counted as one. A
  NEXT.md carrying a form feed is numbered differently by
  `strings.Split(s, "\n")`, and every index after it points at the
  wrong item.
- **Python re's `\s` is 29 code points**; Go's regexp `\s` is five.
  Measured over the full rune range, Python's `\s` and `str.isspace()`
  are the *same* set, and it includes `U+001C–U+001F`, which Go's
  `unicode.IsSpace` also omits. `ITEM_RE` uses `\s` four times, so an
  item indented with a non-breaking space parses in Python and vanishes
  in Go.
- **`len()` on the indent counts CHARACTERS.** Two non-breaking spaces
  are two characters and four bytes.

Note that the whitespace set and the line-break set are *different
sets*: `U+001C` breaks lines but `U+001F` does not, while `U+00A0` is
whitespace that never breaks. Conflating them gets one of the two
wrong.

Those primitives now live in **`internal/pytext`** (`IsSpace`, `Strip`,
`TrimRight`, `Lower`, `SplitLines`, `Repr`) rather than being
re-derived per package — the same extraction `internal/pyjson` got in
the r2 fixes, and for the same reason: this family of difference keeps
reappearing, and two packages disagreeing about whitespace is a bug
that only shows up in a shared file. `skills/coerce.go` keeps its own
copies for now and becomes a set of aliases once the skills chunk is at
fixpoint (deliberate: not editing files under active review).

**Quirks ported verbatim, because the file is shared.** `- [ ]` with
nothing after it is NOT an item (the text group needs one character)
while `- [ ] ` with a single trailing space IS one, with empty text.
`write_next_lines` rstrips the whole joined document, so trailing blank
lines do not survive a write. `[X]` normalizes to `[x]` on read. The
checkbox substitution rewrites the FIRST bracket pair on the line.

**Named improvement, not parity.** Python computes the indices
`append_next_items` returns as `range(len(lines_before), …)` —
arithmetic that assumes one item per line. An item whose text carries a
line separator produces two physical lines, so every later index is
wrong and a caller that marks what it just appended marks a non-item
and raises. The Go writer emits byte-identical bytes and reads the
indices back out of the rendered result, so it differs from Python only
where Python is wrong.

**Named divergences, both narrow.** `ProjectPriority` accepts ASCII
digits, a sign and PEP-515 underscores; Python's `int()` also accepts
non-ASCII decimal digits, so a PRIORITY file written in Devanagari
digits reads as its value there and as 0 here (costs ordering, never
data). And `MARO_ORCH_ROOT` — Python's second store-routing pin, which
repoints the whole ledger at a repo-local directory — is deliberately
unread, for the same reason `MARO_HOME` is: the 2026-08-16 live-ledger
incident was a second routing variable disagreeing with the first.

**Two safety choices where Python is silent.** A ledger that is not
valid UTF-8 is REFUSED, because Python's `read_text(encoding="utf-8")`
raises and admitting it would let this runtime renumber, rewrite and
mark items in a file the other runtime can no longer open. And the
DOING-PID sidecar keeps Python's insertion order (via a token walk
rather than a map range) and carries entries it does not model through
verbatim, so a rewrite neither churns the file nor drops the other
side's fields.

The pid probe reproduces Python's three-way answer exactly, including
the two shapes that read as ALIVE: `EPERM` (a live process owned by
another user) and pid 0 (`kill(0, sig)` addresses the caller's process
GROUP and succeeds, which is what Python's `int(rec.get("pid", 0) or
0)` produces from a missing, null or empty pid). Reading either as dead
would let a drain loop revert an item a live executor is working on.
`sheriff.project_lifecycle_state`'s `.maro-failed` / `.maro-paused`
markers are ported here too, because global selection reads them and
skipping the check would make this runtime drain a project the operator
had explicitly pulled out of rotation.

**Verified by execution, not by reading.** Both runtimes were driven
through the same fixed script of operations (ensure → append → three
marks → decision/risk/dedupe-refused-risk/provenance) into separate
workspaces:

- Same file set; **all six files byte-identical** once the timestamp
  and pid are normalized (`NEXT.md`, `DECISIONS.md`, `RISKS.md`,
  `PROVENANCE.md`, `PRIORITY`, `.doing_pids.json`).
- Every derived answer identical: item list with states/text/indents,
  counts, status, `select_global_next`, appended indices `[11 12 13]`.
- Python then READ the Go-written workspace and agreed on all of it —
  and correctly reported the Go-stamped DOING item as stranded once the
  Go process had exited, which is the sidecar mechanism working across
  runtimes rather than a divergence.

The Go half of that drive is kept in the tree as `TestDriveProbe`
(skipped unless `MARO_ORCH_DRIVE_WS` is set) rather than in a
scratchpad, because an interop claim that can only be re-checked by
rebuilding a throwaway harness stops being re-checked.

**Mutation battery: 57 derived from the files, all killed.** Three
mutants are recorded as EQUIVALENT rather than chased with a test that
could not fail — a guard that cannot fail is worse than no guard:
making the text group greedy (the parse strips `m[3]` with the same
character set the pattern would have used), dropping `sort.Strings`
(`os.ReadDir` already returns entries sorted by filename), and the
non-string-key branch in the sidecar walk (unreachable: in object
position `json.Decoder.Token()` returns a string or an error, and
`dec.More()` has excluded the closing brace).

**Not in this slice:** run records (`RunRecord`, the attempt counter,
validation summaries), `decompose_goal`/`plan_project` (LLM), the
`MARO_ORCH_ROOT` branch, and the wider sheriff. `mission.py` and
`handle_queue.py` are the next step up.

## Running

```sh
cd go
go test ./...
go build ./cmd/maro

# Safe smoke against a scratch workspace:
MARO_WORKSPACE=/tmp/maro-go-smoke ./maro run "say hi" -backend dry

# Real run (claude CLI backend, writes to the resolved workspace —
# the first output line SAYS which). Tool-bearing executor steps are the
# DEFAULT on the subprocess backend: the worker's own tools do real work
# in <workspace>/projects/<goal-slug>/, per-step transcripts land in its
# artifacts/ dir. -safe forces the tool-less utility mode.
./maro run "summarize the difference between a breaker and a truncator" -max-steps 3
./maro run -safe "classify this goal, do not execute it" -max-steps 2
```

## Branch mechanics

Branch `go-port`, worktree `../maro-wt-goport`, pushed with plain
`git push origin go-port` — `scripts/land.sh` is main-only and this
branch does not land to main.
