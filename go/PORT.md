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
| Data retention: append-only, never auto-delete | no delete or compact verb anywhere; the one rewrite is captain's-log ROTATION, which moves entries to a timestamped archive beside the active file and deletes none (`record/rotate.go`) |
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
  corrupting each other's rows. Acquisition mkdirs the lock's parent,
  like Python's.
- **One ledger is deliberately unlocked on both sides:**
  `memory/events.jsonl`. Its rows are capped field by field so a single
  `O_APPEND` write stays under `PIPE_BUF` and is atomic on Linux without
  a lock; it sits on the hot path and must never block; and Python's
  own lock-timeout reporter writes to it, so locking would recurse into
  the machinery reporting the lock timeout. `record.AppendUnlockedLine`
  is that path. It leaves no `.lock` sidecar — which is the only visible
  difference, and is exactly what a differential that skips `.lock`
  files by name cannot see (r11).

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
  is deferred until a caller needs it. `config.Lookup` (r7) is the
  presence half — absent vs explicit-null — and callers that feed a
  value to a coercing constructor MUST use it, because Python
  distinguishes the two and `Get` cannot: `float(None)` raises where an
  absent key yields the default. `record.maybeRotateCaptainsLog` is the
  one such caller today.
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
corpus. **"Unconditional" is load-bearing and was not, at the time, being
carried:** there is also exactly one CONTEXT-sensitive mapping — U+03A3,
which lowercases to ς word-finally and σ elsewhere — and `Lower` did not
implement it until adversarial r6, so `Slugify("ΟΔΟΣ")` produced a
different FILENAME in each runtime. Both are now handled, along with the
three unicode 15-vs-16 table supplements the rule needs.

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
byte-level differences that no consumer reads. The 27 runes that
lowercased differently because Go's and CPython's Unicode tables are at
different revisions were CLOSED in r5 L4 (`pytext.lowerSupplement`), and
this file's third hand-rolled copy of the lowercase helper now delegates
to `pytext.Lower`.

13 further mutations derived from the fixed files, all killed — 59 for
the chunk.

### `pytext.CaseFold` (2026-08-23) — a dedup key, not a rendering

`playbook.py`'s `_entry_core` ends in `.casefold()`, and its output is
the dedup key for both `inject_playbook` and `_dedup_text` — so a
runtime that folds one code point differently keeps a different set of
bullets in a file both runtimes write. Ported ahead of the playbook
module itself, since the module cannot be correct without it.

The table was DERIVED, not transcribed: Go's per-rune mapping was dumped
for all 0x110000 code points and diffed against CPython's `casefold` on
this box, so the unicode 15-vs-16 skew `Lower` already corrects is not
re-introduced. 297 entries, 103 expanding past one rune.

Two things worth knowing before touching it:

- **casefold has no context rules.** `'ΟΔΟΣ'.lower()` ends in ς;
  `'ΟΔΟΣ'.casefold()` ends in σ. Both ς and Σ fold to σ.
- **`lowerRune` is a second implementation of `Lower`'s per-rune half**,
  which is a liability — two copies of one mapping drift, and each would
  have its own passing sweep. A pin re-derives one from the other at
  every code point rather than trusting they stay in step.

Six mutations: five killed (each table class, the supplement, the
U+0130 expansion, and a redundant entry caught by the dead-weight
detector). One EQUIVALENT and recorded as such rather than chased —
folding the whole string through `Lower` first is byte-identical at
every code point in every sigma context (5,560,320 strings, 0
differences), because `Lower` emits ς and the table folds ς to σ. The
per-rune path is kept anyway: the alternative rests correctness on one
table entry silently repairing a rule that should not have run.

### `pytext.SpaceClass` / `DigitClass` (2026-08-23) — regex classes

A separate hazard from `Strip`/`IsSpace`, and the one a transcribed
pattern walks straight into. Go's `regexp` reads `\s` as five code points
and `\d` as ten; Python's `re` on a str pattern reads them as 29 and 760.
A pattern copied character-for-character from Python therefore matches a
different language in Go, silently — and playbook.py's alarm and
attribution patterns decide whether an entry is replaced in place or
appended beside its twin.

Both classes are swept against CPython at every code point, both
directions. `\s` is measured identical to `str.isspace()`, so `IsSpace`
and `SpaceClass` are one set in two notations. `DigitClass` is
`\p{Nd}` plus the same 80-code-point, 7-range unicode 15-vs-16 skew
`record.digitSupplement` carries — deliberately NOT shared with it, since
that table folds digits to their values and this one only decides
membership, and one table serving both would hide which behaviour a
caller was getting. Both sweeps re-derive from CPython, so they fail
together if the skew moves.

Three tripwires beyond the sweeps: the supplement fails when Go's own
`\p{Nd}` catches up (so the literal gets deleted, not left to rot), the
classes fail if Go's defaults ever agree with them (dead weight), and
`NotClass` — which splices a caller's literals into a negated class —
pins its own escaping. Six mutations, six killed.

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

### Skill library — r3 review fixes (2026-08-23)

Whole-chunk round again (3a + 3b + 3c + the r1/r2 fixes), reviewer tier
escalated to opus per the standing rule rather than grinding more cheap
rounds. Two HIGH, three MED, four LOW — and unlike earlier rounds every
claim survived verification, because r3 had actually executed each one.
Full findings and the fix table are in `REVIEW.md`; what is worth
carrying forward is the two patterns, both of which are invisible to
per-function review:

**A correct primitive called from the wrong place.** Both HIGHs. Nothing
was wrong with `SaveSkill`, `compactnessAdjustedScore`, or `len()` — each
is right where it is defined. `UpdateSkillUtility` and
`RecordVariantOutcome` simply called the tail-appending writer where
Python calls the ordinal-holding one, and the store is read
last-row-wins, so pool ORDER changed and pool order is the input to every
limit-capped sweep: one outcome, and a 2-candidate promotion sweep
promoted `[A B]` in one runtime and `[A C]` in the other off the same
bytes. Likewise `len()` counts bytes where Python's `len()` on a str
counts code points, and that expression is the sort key for the only
destructive tier path — 54 of the live store's 432 skills score
differently, so a cull retires a different skill. Reviewing each function
in isolation finds neither.

**The decision ports faithfully and the ANNOUNCEMENT of it does not.**
All three MEDs. The tier change, the circuit trip and the retirement were
all correct in the store; what diverged was who hears about it. The
audience registry was never extended for the five skill events, so every
promotion, demotion and circuit trip Go recorded was stamped `system` and
dropped from the operator's curated lane. The circuit event's failure
reason was filed under `context`, where Python's `render_entry` never
looks — it survived search and vanished from every human-facing render.
And `RunIslandCycle` emitted no `ISLAND_CULLED` at all: the archive and
provenance rails carried the retirement, the log did not. Three separate
sites, one shape — and the store-level differential that caught
everything else cannot see any of them, because the stores agree.

That second pattern is why the audience registry is now a TEST rather
than a comment. `record/audience_census_test.go` walks the source for
event types passed to the `Event*` writers and fails on any with no
decided audience; a comment saying "keep this synced with Python's
frozenset" had been sitting directly above the map the entire time the
map was wrong.

One fix was to KEEP a divergence rather than remove it: Python's
`record_variant_outcome` does not recompute `content_hash` and
`_save_skills` backfills only an EMPTY one, so a tampered hash survives
and keeps warning on every load. Routing that write through `SaveSkill`
had been recomputing it — one A/B win silently erased the tamper signal.
The asymmetry with `UpdateSkillUtility` looks like an oversight in Python
and is load-bearing.

The `pyjson` fix has the widest blast radius of the LOWs: `Value`
delegated CONTAINERS to `encoding/json`, so the float spelling and
HTML-escaping rules stopped applying one level down — and a captain's-log
row keeps its entire payload one level down. `context: {"utility": 1.0}`
was going to disk as `1`, which `json.loads` parses as an int.

**Pins and falsifiers.** The full suite passed unchanged after every fix,
which is the signal that nothing pinned any of them. Eleven pins, then a
17-mutant battery reverting each fix to the shape r3 found: 17 killed, 0
survived. Two mutants were rewritten mid-battery for being unfaithful —
one was killed by the compiler rather than the test, and one
(`os.WriteFile` with a mode on an existing file) did not reproduce the
bug at all. A mutant killed by a build error proves nothing.

### Mission layer — slice 1: the store (2026-08-23)

`mission.json`, `feature_list.json`, `mission-log.jsonl` and the drain
lock — mission.py's persistence surface, on top of the project ledger
ported in the previous slice. The DAG executor, the LLM decomposition and
the validation gates are not here; they need the agent loop and an
adapter. Formal hierarchy: Mission → Milestone → Feature → worker session.

**Four different write disciplines, one per file, and they are not
interchangeable.** Python picked each deliberately and a shared store
reads the difference: `mission.json` goes through `atomic_write`;
`feature_list.json` is a plain `write_text` on CREATE and a `locked_rmw`
on PATCH; `mission-log.jsonl` is `locked_append`. The manifest's split is
the interesting one — it is written unsafely once and patched safely
forever after, because it is the promise the run is graded against: a
re-decomposition partway through must not be able to change what "done"
meant, so `generate_feature_manifest` READS an existing file and never
rebuilds it.

**Two functions that disagree about the same file, on purpose.**
`ListMissions` answers "what is on disk" and reads the raw JSON with
`.get()` defaults; `LoadMission` answers "what can be executed" and takes
Python's `except Exception: return None` for anything it cannot prove. So
a `mission.json` with no `id` appears in the operator's list (as `?`) and
never triggers an autonomous drain. That is Python's behaviour and it is
load-bearing in both directions, so it is pinned in both directions.

**Carry-unless-changed, because Python's load does no coercion.** A
stored `elapsed_ms: 1.5` round-trips through Python untouched, and a
`validation_criteria` that is a bare string stays a bare string. A typed
Go struct alone would have written `1.5` back as `1` — a silent rewrite of
a peer's row, the same class of bug the skills pool rewrite was fixed for.
Both fields keep the literal they were loaded with and emit the typed
value only once the program has actually changed it.

`depends_on` is the exception: Python DOES normalize that one (non-strings
and empties dropped), and a non-list takes the legacy branch that
reconstructs the sequential chain, so an undecomposed mission.json from
before the DAG still executes exactly as the old sequential walk did.

**Named divergence, narrow:** Python does not type-check the required
fields, so a `mission.json` whose `id` is the number 7 loads there and is
re-emitted as 7. This port treats a non-string required field as
unreadable. Every other tolerance is preserved.

**Proof.** A cross-runtime differential (`orch/mission_probe_test.go`
plus a scripts-side driver) runs three ways: each runtime writes the same
mission into its own workspace, each reads the OTHER's files and re-saves,
and each derives the read-side answers. **All three sections byte- and
value-identical.** The re-read half is the one that earns its keep — a
fresh-write comparison cannot see a reader that drops a field, because the
loss only shows on the way back out. It also has a demonstrated kill: it
is what caught the mission-log directory bug below.

27 mutants derived from the FILES, not the diff. 27 killed, 0 survived.
Four needed rewriting first — two were killed by the COMPILER rather than
by a test (an unused import, an unreachable tail), one was anchored on
text that did not exist, and two test gaps were real: the required-key
gate was only exercised wholesale, so a mutant removing ONE of the five
gates survived, and the briefing cap was only checked on one of its three
sections. Both tests were widened rather than the mutants weakened.

**A real divergence found on the way:** Python's `locked_append` creates
the parent directory (`file_lock.py:304`) and so does its lock
acquisition (`:144`); Go's `record.AppendRawLine` does neither, so the
first mission log written into a COLD workspace failed outright where
Python created `memory/`. Fixed at the caller because `record` was under
adversarial review — it belongs down in `record`, and every direct
`AppendRawLine` caller has the same hole until it moves.

#### Found while porting: the JSONL lane is not byte-compatible

`internal/pyjson`'s own header states the contract — "the same VALUE is
not the contract across this port — the same BYTES are" — and names three
`encoding/json` differences it corrects. It misses two more. Measured, one
row, through both production writers:

```
GO:{"timestamp":"2026-05-12T22:19:25.571824+00:00","event_type":"LOOP_CREATED","summary":"reason=initial — café",…}
PY:{"timestamp": "2026-05-12T22:19:25.571824+00:00", "event_type": "LOOP_CREATED", "summary": "reason=initial — café", …}
```

1. **Separators.** A bare `json.dumps(obj)` defaults to `(', ', ': ')` —
   a space after every comma AND every colon. Confirmed against the live
   store: all 7,124 rows of `captains_log.jsonl` carry `", "`.
2. **ensure_ascii.** Python escapes every code point from 0x7f up (DEL
   included; astral planes as a surrogate PAIR). 2,137 of those 7,124 rows
   carry `\u` escapes, so it is the common case, not an edge. Six Python
   writers pass `ensure_ascii=False` explicitly — it is a per-writer
   decision there, and the Go twin has to mirror whichever writer it
   ports rather than pick one globally.

This never appeared in r1–r3 because every "byte-identical" claim those
rounds made was about an `indent=2` sidecar, where the item separator
loses its trailing space anyway. Bounded honestly: no wrong DECISION —
`doctor._dedup_identity` re-serializes the parsed row through Python's own
dumps, `deduplicate_lessons` keys on parsed text, and `content_hash` is
over raw field content, so all three normalize. What it costs is that
every cross-runtime `diff` of a shared store reports every Go-written line
as changed, which is the audit surface the doctrine names.

`internal/orch/pyrender.go` is the correct renderer — ordered objects,
ensure_ascii, Python's separators, `indent=2` and compact — and it is
PARKED in `orch` rather than merged into `pyjson` because pyjson was under
review when the project ledger needed it. Moving a file someone is
reviewing is how a round's findings stop landing against the thing that
was reviewed. `pids.go` had already rolled its own indent renderer, so
this is the second instance; all three fold together once r4 lands, and
every pin asserting the compact spelling has to be rewritten then, which
is why this gets more expensive the longer it waits.

**Not in this slice:** `decompose_mission`, `_validate_milestone`,
`run_mission` and `_run_milestone_dag` (all LLM- or loop-dependent),
`drain_next_mission`, and the Telegram milestone notification.

### Skill library — r4 review fixes (2026-08-23)

Nine findings (1 HIGH, 2 MED, 6 LOW), all verified against both sources
before anything was touched, **all nine real**. The fix table and the
round's method are in `REVIEW.md`; what belongs here are the three
lessons that outlive this round.

**A renderer that can be asked for the impossible will be.** H1 was
`pyjson.Ordered` emitting a key twice because its caller named it twice
— `StampOutcomeVerdict` appends the verdict keys onto the row's on-disk
key list, and any row that already carries a verdict already names them.
The row doubled on every stamp until Go's own `LoadsClean` refused what
Go had just written, while Python's `json.loads` kept taking the last
value and never complained. The fix could have gone at the call site;
it went in the renderer, because the thing being modeled is a Python
dict and a dict cannot hold a key twice. Fixing the caller closes one
site. Fixing the renderer closes the class.

**Two hardcoded octals are not a style question.** `0644` and `0755` are
byte-identical to correct under umask 022, which is what this box runs,
and narrower than correct under umask 002 — so a ledger Go created first
was not group-writable and a Python writer in the same group got EACCES.
The bug could not be observed here at all. The pin therefore does not
assert an octal literal; it opens a file with a plain `OpenFile(…, 0666)`
on the same host and asserts `AtomicWrite` matches whatever THAT produced.
An expectation written as a constant is the same mistake the code made.

**The umask read-back is the one place this port should NOT copy
Python.** `file_lock.atomic_write` reads the umask with `os.umask(0);
os.umask(_umask)` and calls the window "momentary, and file writes here
are multiprocess, not multithreaded, so the window is acceptable". Go's
runtime is threaded, so during that window every other goroutine
creating a file would see umask 0 — a world-writable secrets file is a
real outcome of losing that race. `internal/record/filemode.go` reads it
ONCE and caches it, and says why in the file. Faithfulness is to the
BEHAVIOUR, not the instruction sequence; where the host language changes
what an instruction sequence means, copying it is the unfaithful choice.

**Found while porting: the float spelling gap had a wrong justification.**
`pyjson`'s float renderer carried a comment calling the Go/Python
exponent-threshold difference a "narrow known gap" because "every field
emitted through here is a rate or a small counter". `SkillStats.avg_latency_ms`
goes through it and is milliseconds, crossing the 1e6 boundary at a
~16-minute average step. The replacement (`pyjson.FloatRepr`) implements
CPython's actual rule — fixed notation while `-4 < decpt <= 16` — and was
verified at 103,771 values against CPython with 0 mismatches. A named
gap is only as good as the argument attached to it, and that argument
had never been measured.

### The indent-2 renderer moves to `internal/pyval` (2026-08-23)

The `json.dumps(obj, indent=2)` writer was written inside `internal/orch`
with a note saying it belonged next to `pyjson.Ordered` and was parked
because pyjson was under adversarial review — moving a file someone is
reviewing is how a round's findings stop landing against the thing that
was reviewed. r4 has landed and the task-store port is the third would-be
consumer (after mission.json and `pids.go`'s one-shape hand-roll), which
is past the point where copying it again is cheap. `internal/orch/pyrender.go`
is now nine one-line wrappers over `internal/pyval` and nothing else.

**It went to a NEW package rather than into `pyjson`, because they are
not the same writer.** `pyjson` is the single-line JSONL lane, where
Python's callers pass compact separators; `pyval` is the indent-2 sidecar
lane. Folding them would have forced one default onto a knob whose two
settings are both live here: **ensure_ascii is a per-writer decision in
Python**, and `mission.json` takes the default and escapes while
`task_store._atomic_write` passes `ensure_ascii=False` and does not. A
port that picks one globally is wrong for half its callers, and the
resulting file looks fine until Python reads it back. So `DumpsIndent2`
escapes, `DumpsIndent2Raw` does not, and the choice stays at the call
site where Python puts it. They still share the scalar spelling —
`pyval` defers to `pyjson` for numbers and bools.

The escaping table in `pyval_test.go` is generated from CPython's actual
output on this box rather than typed from the docs, because the interesting
cases are exactly the ones intuition gets wrong: **DEL is ASCII and is
escaped anyway**; U+0085, NBSP, U+2028 and U+2029 are escaped under
ensure_ascii and raw without it; astral code points become a surrogate
PAIR; and the C0 controls plus `"` and `\` are escaped in BOTH modes,
because JSON has no syntax for a raw control character. Seven mutants
derived from the file — including "ignore the flag", "never escape",
"`>=` becomes `>`" and "drop the taint check" — were all killed.

**Vet fallout worth recording.** Making `pyField` an alias for an exported
struct turned 51 terse `{"key", value}` literals into `composites` vet
findings, because vet only flags unkeyed literals of types from ANOTHER
package. They were keyed by a script driven off vet's own file:line:col
rather than a regex over the source, so a two-element literal of some
other type could not be caught in the sweep by accident.

### Task queue — `internal/tasks` (2026-08-23)

`task_store.py` ported whole (minus its argparse shell, which became
`maro task`): a file-per-task JSON queue under `output/queues/tasks/`,
one advisory lock per task file, no global lock. Cross-runtime
differential: **101 lines byte-identical** after normalising the two
volatile fields, across enqueue → claim → complete → fail → summary,
including file mode and lock name. Six deliberate perturbations of the Go
side each made it diff, so the harness can fail.

**A task is carried as a `pyval.Obj`, not a struct.** Two of its fields
(`origin`, `artifact_paths`) are open dicts whose keys are whatever the
producer put there, and Python's writers rewrite the whole dict from what
they read. Assigning to a Python dict updates a present key in place and
appends a new one at the tail — which `Obj.Set` does — so `result_status`
and `error` land at the tail here the way they do in Python without
anyone arranging it, and a field this port has never heard of survives a
rewrite at its own position.

**The lock file REPLACES the extension.** Python locks
`path.with_suffix(".lock")`, so `task-abc.json` is guarded by
`task-abc.lock`. `record.Locked` APPENDS, which would have given
`task-abc.json.lock` — a different lock file, taken by only one of the two
runtimes, excluding nothing. Both writes then succeed, one wins the
rename, and the loser's update is simply gone with no error anywhere. The
name-matching pin asks CPython for the answer rather than asserting one
this package computed; the consequence pin has Python take the lock and
hold it while Go must wait.

**pathlib's suffix is not `filepath.Ext`, and the rule moved between
Python versions.** Measured against 3.14.3 here: the suffix is the last
dot AT OR AFTER the first non-dot character. `..json` therefore has no
suffix and GAINS one; `.hidden.json` has `.json` and loses it; `x.` has a
suffix of `.` and becomes `x.lock`. Older pathlib required the dot at
index > 0 with something after it, which reverses the first and third of
those. None of these are names a job id produces, so the divergence is
bounded — it is spelled out because the alternative is a lock file whose
name depends on which interpreter ran last.

**The task writer is not `file_lock.atomic_write`, one directory over.**
`ensure_ascii=False` (raw UTF-8), a trailing newline, and mode **0600**
that is NOT umask-derived — there is no `fchmod`, so mkstemp's 0600
stands. Asserting the octal is correct HERE and wrong in the `record`
package's test, for the same reason. Meanwhile the CLI prints with
ensure_ascii ON, because `main` calls a bare `json.dumps(task, indent=2)`:
the same reason field is raw UTF-8 in the file and `é`-escaped on the
terminal, in both runtimes.

**Reads are deliberately NOT announced-and-skipped.** `_read_task` uses
`json.loads`, which raises, so a torn or unreadable file fails
`list`/`status` outright. That is the opposite of this port's posture
everywhere else and it is carried on purpose: a queue that silently
under-reports its own contents is how a claimed task disappears.

**Two quirks carried verbatim, one of which had a wrong justification.**
`_check_cycle` tracks the nodes on the CURRENT DFS path and discards on
the way back out, so a diamond is legal and only a real cycle is refused.
`_resolve_dependents` uses `list.remove`, which drops ONE occurrence, so a
duplicate `blocked_by` entry survives a completion. The note this port was
written from added "and keeps the dependent blocked" — **that inference is
false**, and the pin asserting it is what caught it: `claim` gates on each
dependency's STATUS, not on `blocked_by` being empty, and the leftover id
still points at a done task. The residue is cosmetic. It is still carried,
because both runtimes must write the same bytes for the same calls.

**Mutation battery: 41 mutants derived from the two files, 0 killed by the
compiler, final 38/41.** First pass was 30/40. Of the ten survivors, eight
were missing pins and two were equivalent — and one survivor exposed a blind test:
`TestArtifactPathsMergeInPlace` read the file back through
`pyval.LoadsOrdered`, which collapses duplicate keys exactly as Python's
`json.loads` does, so a writer emitting `"log"` twice read back as one key
and looked correct. **A read-back through a normalising loader cannot see
a writer that duplicates** — the r4 H1 lesson arriving from the other
side. That pin now counts the bytes. A ninth survivor found a real
divergence rather than a missing pin: the `..json` case above.

The three equivalents are recorded with proofs rather than chased.
Widening `i >= 0` to `i > 0` in the suffix scan is equivalent because
`i == 0` is unreachable: the leading-dot loop has already consumed every
dot before that index. Removing
the explicit `LOCK_UN` is equivalent because the deferred `Close` releases
the flock (and the shared-lock pin demonstrates it). Removing
`sort.Strings` from `List` is equivalent because Go's `filepath.Glob`
already sorts — verified, not read from the docs. That second one runs the
other way too: Python sorts only in `list_tasks`, so Go's three other
sweeps iterate in sorted order where Python's is arbitrary. Nothing
depends on it except that `RecoverStaleClaims` RETURNS its ids, so a
caller diffing the two runtimes would see the same set in a different
order. Named, not hidden.

**One deliberate strengthening:** an `fsync` before the rename, which
Python omits. It changes no byte either runtime can observe — it only
narrows the window in which a crash leaves a renamed file with no
contents. Adding durability is safe in a way that changing bytes is not,
which is the same reasoning the umask decision used, reaching the opposite
conclusion because the facts differ.

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

### Run-verdict skill attribution — `internal/skills.StampVerdictWithAttribution` (2026-08-23)

Python's `memory_ledger._maybe_record_skill_injection_outcomes` (the
2026-07-29 measurement-honesty fix): when a FULL-trust closure verdict
lands, credit it to the skills the run ACTUALLY injected — read from the
run dir's `source/skills_manifest.jsonl` — rather than to the
keyword-matched bystanders the legacy per-step counters credit.

Ported after adversarial r5 found both ends live and nothing joining them
(see REVIEW.md for the measured Python/Go promotion split). Three things
about the port are not mechanical:

**The composition moved up a layer.** Python guarantees this structurally:
`stamp_outcome_verdict` ends by calling the attributor, so a caller cannot
stamp without crediting. Go cannot express that — `internal/skills` imports
`internal/record`, so `record` cannot call `skills`. `StampOutcomeVerdict`
therefore returns the row it wrote and `skills.StampVerdictWithAttribution`
does the composing. The bare primitive stays callable, which is exactly the
bug r5 found, so a source-level tripwire in `internal/loop` fails the build
if it is ever called there again.

**One lock spans marker-check → batch → marker-write.** With the check
outside any lock, two live stampers both saw no marker, both committed the
batch, and both reported success — double attribution with no crash
involved. The stats lock is the natural boundary, and `record.Locked` is
**not reentrant** (flock is per open file description), so the batch runs
through a lock-free inner core (`recordSkillInjectionOutcomesLocked`)
extracted from `stats.go` for the purpose.

**Two hardenings that pull opposite ways, deliberately.**
`record.GoalAchieved` grades a present-but-non-bool `goal_achieved` as
judged-NOT-achieved, which is the safe direction for a TRUST policy — a
value nobody can read must not earn the trusted-good path. Attribution
instead REFUSES such a row outright, matching Python's
`isinstance(..., bool)`, because the safe direction for a learning COUNTER
is the opposite one: crediting a run's skills with a failure nobody judged
is worse than crediting nothing. The bool gate is therefore not redundant
with the trust gate, and a mutation battery is what proved it — deleting
it left every other pin green.

Attribution never fails the stamp (it is telemetry riding on a verdict, and
Python's wrapper swallows everything for the same reason), but it never
fails SILENTLY either: every refusal comes back as a warning on the loop
result, at the level Python logs it. A marker write that fails AFTER the
batch commits says so precisely — reporting it as "NOT recorded" would be a
lie about what the store holds.

The marker's timestamp uses `pyval.NowISO`, not this package's `nowISO`:
Python writes a bare `datetime.now(timezone.utc).isoformat()` there, which
omits the fractional part when the microsecond is 0, where the package-wide
stamp always writes six digits for lexicographic sorting. Nothing sorts a
marker, so the faithful spelling wins.

### The daily log and MEMORY.md — `internal/record/dailylog.go` (2026-08-23)

Python's `record_outcome` maintains three surfaces, and the Go port wrote
one. `memory/<YYYY-MM-DD>.md` and `memory/MEMORY.md` are the two a person
actually opens, and a Go run left no trace in either (adversarial r5,
MEDIUM).

The asymmetry is what made this worth porting rather than shrugging at.
`MEMORY.md` is a full REWRITE from the ledger, so the next Python run heals
a Go-only gap. The daily log is APPEND-PER-OUTCOME: nothing rebuilds it, so
every Go run was a **permanent** hole in that day's record. The warning
text says exactly that, and the two surfaces are announced differently for
that reason.

**Two clocks, and porting the bug is the point.** `_daily_path()` names the
file from `date.today()` — the machine's LOCAL date — while the heading
inside it is `recorded_at[:10]`, which is UTC. This box runs at UTC-6, so
the two genuinely disagree here every evening between 18:00 and midnight
local; it is not a theoretical edge. Matching it is mandatory because
`_update_memory_index` globs `????-??-??.md` and keys on the FILENAME: a
runtime that "fixed" the skew would file runs where the other runtime's
reader never looks.

**Python's reader is stricter than ours, and the index has to match it.**
`load_outcomes` rehydrates each row into the `Outcome` dataclass, so a row
missing any of the six fields that have no default (`outcome_id`, `goal`,
`task_type`, `status`, `summary`, `lessons`) raises `TypeError` and is
dropped — measured, not inferred; CPython announced "4 row(s) ... are JSON
but not loadable under the current schema" when the probe seeded partial
rows. The exclusion happens BEFORE the last-ten slice, so a dropped row
does not consume a window slot. `updateMemoryIndex` therefore loads
everything, filters, then takes ten. `LoadOutcomes`' tolerance is right for
its other consumers and wrong here, so the filter is local to this one
renderer — the only Go function whose OUTPUT lands in a file Python
rewrites.

**One place the port deliberately does NOT use the hardened tri-state.**
`record.GoalAchieved` grades a malformed `goal_achieved` as
judged-NOT-achieved, which is right for a trust decision. The index counts
with a plain bool assertion instead, matching Python's `is True` / `is
False`, because these three buckets must still sum to the window size — a
malformed verdict belongs in the unjudged remainder, not in the failures.

**Absent by design, and pinned as such:** the daily entry omits Python's
cost suffix (the Go row has no `cost_usd` — a hardcoded 0.0 is a record
that lies about spend) and its Lessons block (the Go row's `lessons` is
empty; the extractor is not ported). Neither is written as an untakeable
branch. A test asserts both fields are still empty, so the day either
starts carrying data this writer fails and names itself as the thing to
update.

**One named divergence, a deliberate improvement.** Python wraps
`_update_memory_index` in a bare `except: pass`, so an unwritable memory
dir leaves the index silently months stale. Go returns the error, and the
caller announces it as a warning. The behaviour that matters — a failure
here never fails the outcome — is preserved at the call site rather than by
swallowing.

### `pytext.Repr` becomes Python's actual `repr()` (2026-08-23)

The old implementation was two `strings.ReplaceAll` calls, and it had two
defects (adversarial r5, MEDIUM). The second corrupts a file Python then
has to read:

- the DOUBLE-QUOTE branch escaped nothing at all, so a value holding both
  an apostrophe and a backslash came back with the backslash bare;
- NEITHER branch escaped control or non-printable characters.

`skills/export_md.go` writes SKILL.md's `triggers:` line through this
function, and Python's `skill_loader._parse_frontmatter` is not a YAML
parser — it iterates `raw_meta.splitlines()` and splits each line on the
first colon. Falsified by reverting the fix: a trigger containing a newline
truncated the line to `triggers: ['first`, and Python's own loader then
read ONE trigger where its own writer's file yields two. U+2028 broke it
the same way, since `splitlines()` treats it as a break. Python's exporter
builds the same line with `repr(t)`, so byte parity here is the contract,
not an approximation of one.

**Three copies existed** — `pytext.Repr`, `scans.pyRepr`,
`skills/coerce.go`'s `pyRepr` — all carrying the same two defects. The two
duplicates now delegate; fixing one and leaving the others would have kept
the old spelling flowing into the same shared files.

**`IsPrintable` is stated POSITIVELY, and that is load-bearing.** Python
states the rule negatively (false for Cc, Cf, Cs, Co, Cn, Zl, Zp, Zs, with
U+0020 the single exception) and transcribing it that way is a trap: Cn is
UNASSIGNED, and unassigned code points are in no category table at all, so
a literal "not in C and not in Z" admits 800k+ of them as printable.
Printable is exactly L, M, N, P, S plus the space.

The first version had BOTH — the negative guard and the positive test — and
the mutation battery showed the guard was dead code: deleting `C` from it
survived, and so did deleting `Z`, which is only possible if the line was
unreachable. Two survivors that looked like missing coverage were one piece
of dead code. The simplified version is verified equivalent by a sweep of
all 1,114,112 code points against CPython.

**Named divergence, measured, costing spelling rather than meaning:** Go's
unicode tables are 15.0.0 where CPython's unidata is 16.0.0, so 5,812 code
points assigned in 16.0.0 read as Cn here and Go escapes them where Python
prints them raw. The direction is ONE-WAY (swept: there is no code point Go
prints raw and Python escapes), and `\uXXXX` parses back to exactly the
same character — so the two spellings carry the same string VALUE and only
the bytes differ. A consumer that hashes this output rather than parsing it
sees two keys. A test asserts the direction and FAILS if the skew ever
closes, so the note cannot rot silently.

---

## The playbook (`internal/playbook`)

`src/playbook.py`'s append half — `append_to_playbook`, `inject_playbook`,
`expire_stale_alarms`, `_archive`, the seed, and the parsing verbs the
three share. `curate_playbook` (the LLM compression path) is not ported
yet; the file it writes is the same file, so the two halves must agree
about its shape before that lands.

**The playbook is not a log — it is a document both runtimes rewrite in
place.** Every other store in this port appends; this one reads the whole
markdown file, edits it, and writes it back atomically. That inverts where
the risk sits. An append that drifts by a byte writes one odd row. A
rewrite that drifts by a byte rewrites the operator's whole document, and
the next process to read it — in either language — sees the drift as
content.

Five hazards were measured before any Go was written, and each one is now
pinned by a whole-file differential against CPython rather than by a
unit test over the Go side alone:

**1. The patterns are Python regexes, and Go reads them as a different
language.** `_ALARM_RE` and the attribution pattern both use `\s` and
`\d`. Go's `regexp` reads `\s` as five code points and `\d` as ten;
Python's `re` reads them as 29 and 760 on this box. Transcribing the
patterns character-for-character compiles and passes every ASCII test.
`internal/pytext.SpaceClass`, `DigitClass`, and `NotClass` carry the
measured spellings, and the patterns here are built from them — there is
no literal `\s` in this package.

**2. The dedup key casefolds, and `casefold` is not `lower`.**
`entryCore` strips the attribution suffix and casefolds; two entries with
the same core are the same entry. `strings.ToLower` differs from CPython's
`str.casefold()` on 297 code points, 103 of which expand past one rune —
so a German or Greek entry would dedup in one runtime and duplicate in the
other. `internal/pytext.CaseFold` is a measured table, swept against
CPython at every code point.

**3. Clipping is by code point, not byte.** Python's `s[:n]` counts
characters. `s[:n]` in Go counts bytes and can split a rune in half. The
budget arithmetic in `Inject` is the same shape, so the divergence only
appears where a multi-byte character straddles the boundary — which no
hand-picked budget reliably hits. The differential sweeps 137 budgets.

**4. `str.split()` splits on 29 code points; `strings.Fields` on 25.**
The four that differ are U+001C..U+001F, which arrive through pasted
terminal output more often than their obscurity suggests. `collapseSpace`
uses the measured set.

**5. The alarm marker's spacing is part of a key.** `_ALARM_RE` requires
`\s+@` before the date. An alarm written without that space never matches
its own key lookup — **in either runtime** — so alarms accrete beside each
other forever instead of being replaced in place. The first Go spelling
had exactly that defect (`key+"@"+date`), it passed every unit test, and
the whole-file differential is what caught it. This is the port's recurring
bug family in its purest form: correct logic, one wrong character, in a
string that turns out to be a key.

### On the harness

The differential compares the two runtimes' finished files byte for byte,
which means it must tolerate the two runs straddling midnight. The first
version of that allowance normalized *every* date to a placeholder — and
so it also swallowed a mutant that stopped refreshing the `Last updated`
stamp entirely, reporting a SKIP where the defect was exactly a wrong
date. The escape hatch now only excuses a today-vs-yesterday difference.

That was the second time this chunk that a green test was hiding rather
than proving something, and both were found the same way: mutate the
production code and watch for red. An 11-mutant battery over the append
path kills all 11 as of this commit; the corpus additions that killed A6
and A11 were both *fixtures the base document could not produce*, not
finer assertions.

### Curation (slice 2)

`curate_playbook` plus the two helpers only it uses. This is the pass that
rewrites the whole document, so its guards are all about not destroying an
operator's file:

- **Alarms expire before compression.** An expired reading that survives
  into the prompt comes back rephrased as durable advice, and there is then
  nothing left to identify it as an alarm.
- **The lock is dropped across the LLM round trip**, deliberately — holding
  a write lock across a network call starves concurrent `Append` writers
  into lock timeouts. Because it is dropped, the write re-reads and
  compares against the snapshot, and discards the whole pass if another
  writer landed meanwhile.
- **Archive before rewrite, abort if archiving fails.** Never rewrite what
  you cannot restore.

Two numeric gates measured rather than transcribed: `len(new) > len(old) *
1.1` and `new_bullets >= max(1, ceil(old_bullets * 0.6))`. Both are float
comparisons in Python. Swept 0..20000, `math.Ceil(float64(n)*0.6)` agrees
with the exact rational ceil at every n and the growth gate agrees with
`10*new > 11*old` at every pair — so the integer reformulations happen to
be equivalent *here*. They are still not what Python evaluates, and the
equivalence is an accident of these two constants, not of the shape.

Every length in the validator is a CODE-POINT length. The corpus carries
both directions: a CJK candidate that byte-length would reject and Python
accepts, and an ASCII candidate that byte-length would accept and Python
rejects.

One measured behaviour worth stating because it looks like a bug: **a
playbook with no bullets can never pass compression.** The floor is
`max(1, ceil(0 * 0.6)) = 1`, so even an identical rewrite of a prose-only
document is rejected and the deterministic result is kept.

**Named divergence:** a nil adapter skips the compression pass. Python
builds its own cheap-worker adapter from the role table when the caller
passes none, and neither `conductor.assign_model_by_role` nor
`llm.build_adapter` is ported. A nil adapter therefore behaves like
Python's adapter-construction *failure* — the same outcome reached for a
different reason. When the role table lands, `Curate` should build one.

### The half nothing was checking

Until slice 2 this package had no pin at all on the captain's-log rows it
emits, and those are the half of its output that is actually DATA. The
playbook file is prose a human would notice; the rows are indexed by
`event_type`, rendered from `summary`, and read by `context` key. Both
`PLAYBOOK_UPDATED` (from `Append`, landed in the previous commit) and
`PLAYBOOK_CURATED` now have row-level differentials against CPython.

The gap was found by asking what a mutant could change without any test
noticing — not by review, and not by anything failing.

**One recorded equivalent mutant.** Making `droppedKeys` return a nil
slice changes nothing observable: `pyjson`'s `[]string` case renders a nil
slice as `[]` exactly like an empty one, so the captain's-log row is
Python-shaped either way (`encoding/json` would emit `null`). It is kept
non-nil regardless, because `CurateStats` is public and nothing binds a
future consumer to `pyjson`. The encoder rule the argument rests on is now
pinned in `internal/pyjson`, and that pin was verified to fail when the
`[]string` case is removed.

The first version of that reasoning was written the other way round — the
comment claimed a nil slice "marshals to null" in this writer, which is
false. Two batteries in a row, the finding was not wrong code but a
confident comment about correct code.

### Wiring the playbook into the evolver (2026-08-23)

Porting the playbook unblocked a divergence the Go slice had been
carrying deliberately. `evolver_store.applyAction` used to return early
for a guidance-only guardrail — one whose `pattern` is missing or is not
a valid regex — because Python's honest `applied` stamp relied on the
prose landing in `playbook.md`, and this runtime had nowhere to put it
(r1 review Finding B: stamping applied would have been a record that
lies). The playbook exists now, so:

- the two guidance-only paths **fall through** instead of returning, the
  way Python's do;
- the prose is appended under the category's section
  (`prompt_tweak`→Execution, `new_guardrail`→Quality,
  `observation`→Learned) at confidence ≥ 0.7;
- `EVOLVER_APPLIED` is emitted for those rows, which it previously was
  not — Python's `log_event` sits after the category switch and is not
  conditional on a constraint row, so on the shared store this was a row
  Python wrote and Go did not;
- the row is stamped applied only when the prose actually landed.

**The hold narrowed rather than disappeared.** Below the 0.7 gate neither
runtime appends, so a pattern-less guardrail still has no durable effect
here and is still held — Python stamps applied there anyway, with the
prose going nowhere. That remains a named divergence, and it is now the
only one left in this path.

`str(d.get("playbook_key", "") or "")` is ported as `pyStrKey` rather
than a bare type assertion: the alarm key decides whether a later reading
replaces an entry in place or accretes beside it, so yielding `""` for a
non-string silently turns an alarm into a permanent insight. Falsey
values collapse first (Python's `or`), then `str()` renders what
survives. Named divergence on out-of-contract data: Go's JSON decoder
folds `5` and `5.0` into one `float64`, so an integer key renders the way
Python renders the float.

### The playbook — r9 review fixes (2026-08-23)

Eight findings against the whole chunk; the full write-up is in `REVIEW.md`.
Three port rules came out of it and belong here rather than in the review
log, because they constrain code that has not been written yet.

**A verb that takes a workspace argument must not read ambient config.**
`Curate(ws, …)` resolved its file, archive dir and lock from `ws` and its
three config gates from `MARO_WORKSPACE`. Python is internally consistent
here — path *and* config are both module-level — so this is a hazard the Go
structure creates and Python cannot have. `config.LoadFor(dir)` now exists
and its doc carries the rule. Every future verb that takes a workspace
argument uses it.

**`int(...)` is not `config.Get[int]`.** Python's `int()` truncates floats,
parses numeric strings, folds Unicode decimals, and **raises** on `None` —
and in `curate_playbook` that raise is caught by an outer `except Exception`
that abandons the whole pass. `config.Get[int]` silently substitutes the
default for all four. `pyInt` in `curate.go` is the honest shape: it returns
`ok=false` exactly where `int()` would raise, and the caller abandons.
Anywhere else this port meets a Python `int(cfg_value)`, `config.Get` is the
wrong helper.

**CPython's strptime is Unicode-aware in exactly one of the three date
positions.** Measured on this box (3.14.3, unidata 16.0.0):

| directive | pattern | accepts |
|---|---|---|
| `%Y` | `(?P<Y>\d\d\d\d)` | all **760** decimal digits, folded by value |
| `%m` | `(?P<m>1[0-2]\|0[1-9]\|[1-9])` | ASCII only |
| `%d` | `(?P<d>3[0-1]\|[1-2]\d\|0[1-9]\|[1-9]\| [1-9])` | second digit Unicode-capable **via `[1-2]\d` only** |

`'٢٠٠١-01-01'` parses; `'2001-1٢-01'` does not. `alarmDate` transcribes the
two sub-patterns, folds by value where CPython folds, and a test re-derives
all three from `_strptime.TimeRE()` on the running interpreter so an upstream
change fails here instead of drifting.

**This corrects commit `cf1285a9`**, whose message says strptime *"accepts
all 760 decimal digits for `%Y/%m/%d`"*. True for `%Y`, false for `%m`/`%d` —
the measurement behind it probed only the year position and generalised. The
code that commit landed is right; the claim recorded alongside it was not.

Two smaller rules, both about tests:

- **A failing CPython probe is a failure, not a skip.** `runPython` turned
  any non-zero exit into `t.Skipf`, so ten of twelve differentials — the
  seed-bytes pin included — would have reported green against a renamed
  Python helper or a tripped `/tmp/` safety assert. A *missing* interpreter
  is still a skip.
- **`Inject` passes `max_chars` through.** Go substituted a default for any
  non-positive budget; Python does not. The sweep now starts below zero.
### Mission layer — slice 2: the planner and the milestone DAG (2026-08-23)

`decompose_mission`, `_validate_milestone`, `_is_chain_shaped` and
`_run_milestone_dag` — mission.py's planning half, on top of the store
ported in slice 1. `run_mission` itself is still out: it needs the agent
loop. What made this portable ahead of that is that **`run_one` is a
parameter in Python and stays one here**, so the scheduler's whole
contract — order and terminality — is exercisable with a fake.

**`str(x)` is not a cast, and this file is where that bites.** Every
field the model supplies reaches a string through `str(x).strip()`.
Measured against CPython before any Go was written:

| the model sends | Python writes |
|---|---|
| `"title": null` | the four characters `None` |
| `"title": {"a": 1}` | the eight characters `{'a': 1}` |
| `"title": true` | `True` |
| `"features": [1.0, 2]` | features named `1.0` and `2` |
| `"features": [null, "f"]` | a feature named `None`, KEPT |
| `"features": "abcde"` | **three** features named `a`, `b`, `c` |

The last one is the family this port keeps producing in miniature:
`"abcde"[:3]` slices the *string*, and iterating a string yields
characters. Nothing about the code reads wrong.

This needed a seam the port did not have: **`pyval.Str` / `pyval.Repr`**,
Python's `str()` and `repr()` over a decoded JSON value. Two properties
force the ordered decoder rather than a `map[string]any`:
`str({'b':1,'a':2})` keeps **insertion order**, and `1` vs `1.0` is
decided by the **literal**, which `json.Unmarshal` into `any` destroys by
making both `float64`. `Repr` refuses a plain Go map outright rather than
emit a plausible string in map-iteration order.

**Three malformed payloads RAISE out of `decompose_mission`**, and that
is the divergence a careless port would smooth over. The parse block is
wrapped in `except ImportError`, not `except Exception`, so
`"features": null` (TypeError), `"validation_criteria": null`
(TypeError), and `"features": {}` (KeyError) all leave the function and
kill the caller. A Go port that fell through to the heuristic would
produce a *working mission* where Python produces none — a bigger
difference than any wrong field.

**Two slice orders, four lines apart, in opposite directions.**
`safe_list(..., max_items=N)` FILTERS then slices, so a milestones list
whose first element is a string still yields N objects. The features list
one scope down SLICES then filters, so a blank first feature yields N-1.
Both are reproduced verbatim; making them uniform would be a divergence
in one direction or the other.

**`depends_on` resolution.** Refs are indexes into the *model's* array,
and feature-less milestones are dropped from ours, so the two numberings
diverge the moment one is dropped. Only earlier, kept indexes resolve —
which is what makes cycles impossible by construction *from this writer*.
A bool is an `int` in Python and must be excluded by hand; `1.0` is not
an index where `1` is; and an explicitly-signalled-but-entirely-invalid
list chains to the predecessor rather than silently ungating a milestone
the model meant to order. Only a literal `[]` means "independent root".

**Where Go cannot copy Python.** CPython's GIL makes `ms.status =
"failed"` and a persist callback that walks the whole mission mutually
safe by accident. Go has no such accident and `-race` says so. The mutex
in the scheduler is therefore not a port of anything — it is the price of
the same behaviour in a language that means it, and it is deliberately
coarse enough to cover only the crash-marking and the persist, never the
milestone body.

**The validation gate defaults to PASS at all four exits** — no criteria,
dry run, no adapter, unparseable answer. Python's comment says why
("don't get stuck in validation loops"). A port that failed closed on an
adapter error would turn a transient outage into a stuck mission, and
every happy-path test would still be green. It also reads
`bool(data.get("passed", True))`, which is truthiness: the string
`"false"` PASSES, and so does `"0"`; only `""`, `null`, `0`, `[]` and
`{}` fail.

**How the DAG is tested.** Not by wall-clock interleaving, which neither
runtime promises. Both sides run with a `run_one` that records which
milestone pairs were ever **concurrently inside a barrier** — so the
comparison is over the set of overlaps the scheduler *allowed*. A port
that ran everything sequentially produces identical missions and would
pass every other assertion.

**Owed.** `run_mission` and `drain_next_mission` (slice 3) need the agent
loop, and also `config.get_bool`, which landed upstream with the
milestone-DAG work and is not in the Go config yet. It exists because a
quoted YAML `"false"` arrives as a *string* and `bool("false")` is True —
the same truthiness hazard as above, in the direction that silently
defeats a revert lever.

**Battery.** 43 mutants from the files: 41 killed, 2 recorded EQUIVALENT
with proofs, 0 survivors. Six needed re-spelling, and the reasons are the
lesson:

- Three of my own mutants were **no-ops** — one appended to a slice
  inside its own `range` (which does not extend the loop), one keyed off
  a map that was always empty. A mutant that does not change behaviour is
  not evidence of anything, and it looks exactly like a killed one in a
  green run.
- Three fixtures **could not discriminate**, and all three failed the
  same way: the two verdicts happened to COINCIDE on that input. `[1.0]`
  as a dependency index resolves to B whether it is accepted or skipped.
  A dependent of a FAILED dependency runs either way — a port that gated
  on outcome merely *stalls*, and the stall lane runs it anyway, to the
  same status, in the same order. An unknown dependency id is the same
  shape.

  What separates all three is **overlap**: released together the
  milestones run concurrently, stalled they run one at a time. The
  fixtures that see the rule are the ones where two milestones are
  waiting on the same thing.

The two equivalents are recorded where the code is, not just here:
deleting `IsInt`'s literal check changes no verdict because
`strconv.ParseInt` rejects `.`/`e` anyway, and deleting
`ValidateMilestone`'s empty-object guard changes none because the
absent-key default is also `true`. Both stay, because each states the
RULE where the fallthrough states only an accident.

## The playbook — r10 review fixes (2026-08-23)

Nine findings, all verified against CPython before any fix, none
hallucinated. Only **two were wrong code**. Four were correct behaviours
with no pin, two were assertions in comments and an operator-facing string
that were false, and one was a fixture that could not fail. Three port
rules came out of it.

### Rule 1 — `Path.read_text()` is universal newlines, and that is a parse decision

The Python this port targets never calls `open(..., newline='')`. Every
read of a workspace document goes through `Path.read_text()`, which
rewrites `\r\n` **and a lone `\r`** to `\n` before the caller sees one
character. `os.ReadFile` does not. On the playbook this was not cosmetic:
the section-header regex is line-anchored with `[ \t]*$`, so `## Cost\r`
fails to match and the writer creates a section that already exists.

The translation set is exact and must not be widened. Measured:

```
b'a\r\nb\rc\n\x0bd\x0ce\x1ef\u2028g'  ->  'a\nb\nc\n\x0bd\x0ce\x1ef\u2028g'
```

U+2028/U+2029/U+0085 are line breaks to `str.splitlines()` but **not** to
universal newlines — which is precisely the mistake a plausible fix makes,
because this port already carries a `splitlines`-shaped notion of "line
break" for other reasons.

`internal/playbook` now has one `readText` helper and no raw read. **Every
read site must be converted together**, including the compare-and-swap
re-read: normalising the snapshot but not the re-read makes a CR-bearing
file compare unequal to itself and skip every curation forever. Any future
Go port of a Python module that reads a shared document inherits this rule.

### Rule 2 — a truthiness test is not a typed getter, in either direction

`if not _cfg_get("playbook.curation_enabled", True)` is Python truthiness
on whatever the config held. `config.Get[bool]` returns its default on
anything that is not a Go `bool`, so `0`, `null`, `""`, `[]`, `{}` and
`0.0` — six ordinary operator spellings of "off" — all enabled the
destructive pass.

The port now has `pyBool` beside `pyInt`. The pair is the rule:

| Python writes | Go must use | absent key | falsey non-bool |
|---|---|---|---|
| `int(cfg_value)` | `pyInt` | default | may RAISE, abandoning the pass |
| `if not cfg_value` | `pyBool` | default | falsey |
| (neither) | `config.Get[T]` | default | default |

And the asymmetry that makes truthiness not parsing: the **string**
`"false"` is a non-empty string and therefore **true** in both runtimes.
A `pyBool` that "helpfully" parsed it would be a new divergence.

### Rule 3 — PEP 515 underscores are legal in `int(str)`

The port's comment asserted the opposite, and that assertion was the
justification for `strconv.Atoi`. Measured:

```
int("1_0") -> 10     int("+1_000") -> 1000     int("١_٠") -> 10
int("_10"), int("10_"), int("1__0"), int("+_10") -> ValueError
```

A separator is legal only **between two digits**. The obvious fix —
delete underscores, then parse — accepts all four spellings CPython
raises on, trading a divergence for its mirror image. `stripIntSeparators`
encodes the positional rule instead: an ASCII digit on both sides,
checked after `FoldDecimals` so the neighbour test is plain ASCII.

### What the round says about testing this port

r9's lesson was that a corpus built from one base document never exercises
what that base cannot produce. r10's is narrower and sharper:

**A corpus that only ever enters through a helper cannot see what the file
boundary does.**

`curateCorpus` contained a lone carriage return the whole time, under the
name *"a lone carriage return is likewise not a split point"* — and that
line is *correct* about `_dedup_text`, which is handed a string. End to
end the claim inverts, because the read already turned the CR into a
newline. Twelve differentials, one call-level above where it mattered.

The corollary now applied to every new pin here: a differential that
enters through a helper needs a sibling that enters through the **file**.

---

## The mission slice (`internal/orch/mission_plan.go`, `mission_dag.go`)

Decompose → milestone DAG → validation gate: mission.py's planning half.
`run_one` stays a parameter, which is what makes the scheduler portable
ahead of the agent loop — its contract is entirely about ORDER and
TERMINALITY, and a fake `run_one` exercises all of it. `run_mission`
itself lands in slice 3.

### The two slice orders, four lines apart in the Python

```python
raw_milestones = safe_list(data.get("milestones", []), element_type=dict, max_items=max_milestones)
...
    for f in rm.get("features", [])[:max_features_per_milestone] if str(f).strip()
```

`safe_list` is `[v for v in value if isinstance(...)][:max_items]` —
**filter the whole list, then slice**. The features comprehension does the
opposite: **slice, then filter**. A milestone list whose first element is
a string still yields `max_milestones` milestones; a feature list whose
first element is blank yields `max_features - 1` features. Both orders are
reproduced verbatim. Making them uniform would be a divergence in one
direction or the other.

### Every caller-supplied slice bound goes through `pySliceLen`

Python clamps where Go panics. `[a,b,c][:5]` is 3, `[:-1]` is 2, `[:-9]`
is 0; `t[:5]` and `t[:-1]` are both runtime panics in Go. `maxMilestones`
and `maxFeaturesPerMilestone` are plain `int` parameters on an exported
function and nothing validates them.

The trap on the way out: flooring a negative bound at 0 looks like the
safe fix and is the **mirror** divergence — `[:-1]` drops the last
element, it does not drop everything. The rule is to port the clamp, not
to invent one.

### Rule — a hardening is a divergence

This is the rule the whole round produced, and it inverts a habit every
earlier chunk of this port had.

Two "Go is stricter than Python here" improvements — a `carve` that
tracked JSON string literals, and a nil-adapter guard on
`DecomposeMission` — each changed which bytes reach a store the Python
runtime also reads:

```
{"passed": false, "reason": "the } thing is broken"}
  CPython -> unparseable -> _validate_milestone defaults to PASS
  Go      -> parses      -> milestone FAILS
```

Same reply, same `~/.maro/workspace/`, two different `mission.json`
files. Both were removed. `carve` is now a transcription of
`llm_parse._find_json_bounds` — naive depth counter, blind to strings,
scanning from index 0 so that a stray CLOSE ahead of the payload drives
depth negative and finds no bounds at all — and the package doc carries a
REJECTED HARDENING block naming the measured fork, so the next reader does
not re-improve it.

The generalization:

**A hardening that changes which record gets written to a shared store is
not a hardening, it is a fork. If the naive behaviour is worth fixing, fix
it in the Python first and let both runtimes inherit the fix.**

This is narrower than "match Python exactly". Go may be strictly better
wherever the difference cannot reach the store — `stripThinkBlocks` on a
prose surface, an error where Python returns a sentinel the caller treats
identically. The test is not *is Go's behaviour better*, it is *does a
byte of the difference survive to disk*.

#### The rule's second catch, found by applying it to the same file

Writing the rule down was worth more than the fix that produced it. Read
back over `internal/jsonx` with the rule in hand, a **second fork** was
sitting four lines from the first, and r1 had waved it through as an
"already-documented deliberate hardening":

```go
for _, m := range fenceRe.FindAllStringSubmatch(text, -1) {   // find a fence ANYWHERE
    if payload, err := carve(m[1], open, close); err == nil { // and carve inside it
        return payload, nil
    }
}
```

`llm_parse.strip_markdown_fences` does not hunt for fences. Its regex is
anchored — `re.match(r"^```[a-zA-Z]*\n?(.*?)\n?```$", stripped, DOTALL)` —
so it unwraps a fence that is the **entire** message and otherwise leaves
the text completely alone. Measured 2026-08-23:

```
See the docs [here](url) for context.
```json
["step one", "step two"]
```
  CPython -> nothing stripped -> carve hits [here] -> [] (the default)
  Go      -> reads the fence  -> ["step one", "step two"]
```

Ten non-test files call into this package and every one of them writes
what it parses into the shared workspace. Go now runs
`carve(stripMarkdownFences(stripThinkBlocks(text)))` — `extract_json`'s
three verbs, in order.

Three things this second catch taught that the first did not:

- **The case that hides a fork is the one where both runtimes agree by
  different routes.** `"Sure! Here are the steps:\n```json\n[...]\n```\nLet
  me know."` returns the right list on both — Go via the fence, CPython
  via a carve over prose that happens to contain no bracket. That test
  passed before and after the change and proved nothing either time. The
  corpus now carries it *labelled as such*, next to the one-character
  variant (`[here](url)` in the prose) where the two split.
- **A regex port is worth pinning as a string, not as a parsed value.**
  Three cases would have survived comparison of the decoded JSON:
  ```` ```json5 ```` leaves a stray `5` inside the payload (`[a-zA-Z]*`
  will not eat a digit, and the `\n?` then cannot either); a two-fence
  document captures everything between the *outermost* pair, inner
  backticks included, because `(.*?)` is lazy but `$` is anchored; and a
  `<think>` block ahead of a fence blocks the strip outright yet still
  parses, which is the only evidence that think-stripping runs *first*.
  `fence_diff_test.go` compares CPython's and Go's strip output byte for
  byte, and a second column compares `strip(think(x))` so the ordering is
  pinned rather than assumed.
- **"Already documented" is not "already decided".** The old package doc
  described the fence-first scan as a deliberate hardening, in a comment
  written in the same round that introduced it. That sentence is exactly
  what let r1 skip past it. A doc comment asserting a divergence is safe
  is a claim, and it needs the same measurement as the code.

A third lesson arrived from the mutation battery over the fix, and it is
the most portable one in this section: **a differential over the helpers
is not a differential over the pipeline.** `fence_diff_test.go` pinned
`stripMarkdownFences` and `stripThinkBlocks` against CPython exactly, and
a mutant that swapped the two verbs *inside `extract`* survived it —
because nothing in the file called `extract`. Both pieces were right; the
composition was untested. Every differential in this port now drives the
function production actually calls, not only the ones it is built from.

The residual is now the honest one and it is **shared**: with prose either
side of a fence, neither runtime finds it, and the first balanced bracket
in the message wins on both. That is worth fixing — in
`llm_parse.strip_markdown_fences`, where both runtimes inherit it. It is
not worth fixing here, alone.

### `None` is not `""`, and a bool is not an index

Two sentinel collisions, both reachable from a hand-edited `mission.json`:

- `_is_chain_shaped` walks with `prev_id = None`, which is distinct from a
  milestone id of `""`. Go's `prevID == ""` was not, and `LoadMission`
  accepts `"id": ""`. The two runtimes then picked **different execution
  lanes** — sequential vs DAG — for the same file. A separate `first bool`
  restores the three-valued distinction Python gets for free.
- `isinstance(j, bool)` is checked *before* `isinstance(j, int)` in the
  `depends_on` resolver, because in Python a bool **is** an int. Pinning
  that needs a fixture where accepting and rejecting reach *different*
  milestones: `depends_on: [true]` on the milestone at raw index 2
  resolves to index 1 either way, since the predecessor *is* milestone 1.

### The log line is part of the contract

`log.warning("... deps=%s", ms.depends_on)` renders a Python list:
`['m1']`. Go's `%v` on a `[]string` renders `[m1]`. `pyval.ReprStrings`
exists for exactly this. The warning channel is the *durable* half of the
DAG's stall evidence — Python calls `log.warning` beside every verbose
`log_fn` line precisely because a verbose-gated line is not evidence — so
a log parser keyed on the Python spelling missed every Go row.

### Non-finite floats cross the boundary twice

`json.loads` accepts bare `NaN` / `Infinity` / `-Infinity`; Go's
`encoding/json` rejects them and the rejection kills the **whole**
document, not one field. And `json.Number.Float64()` on `1e309` returns
`+Inf` *together with* `strconv.ErrRange`, so treating the error as
"unrenderable" echoes the source literal where CPython prints `inf`.
`pyval` masks the bare tokens before decoding (refusing outright if the
sentinel is already present in the input) and range-checks on the way
back out.

### Concurrency, stated honestly

The scheduler mutates only terminal milestones and only from its own
goroutine, so it needs no lock — Python's `_mark_crashed` takes none
either. The mutex that used to be here was never contended and its
`-race`-proves-it comment was false.

The residual it *claimed* to close is real and stays NAMED in the package
doc: a `PersistFn` that reads milestones the scheduler has not yet marked
terminal races with those milestones' own bodies. Python has the same
logical tearing under the GIL but no undefined behaviour; Go has both. It
cannot close at this layer, because `runOne` is an injected seam and
locking around it would serialise the concurrency the DAG exists to buy.
It closes in slice 3, where `run_mission` supplies the real persist and
can snapshot under the same lock the milestone bodies write under. Until
then the contract is stated at the seam: do not pass a `PersistFn` that
reads non-terminal milestones.

### The non-finite family, and why it took three tries

`json.loads` accepts the bare tokens `NaN`, `Infinity` and `-Infinity`;
Go's `encoding/json` rejects them, and the rejection kills the **whole**
document rather than one field. One stray non-finite anywhere in a model
reply — including in a key nobody reads — turns the model's plan into the
two-phase heuristic on the Go side only.

The first fix masked the token with a NUL-prefixed sentinel. Go's decoder
rejects a raw NUL inside a string literal, so **the masking never worked
at all** — and no test covered a non-finite token, so deleting the fix
changed nothing and the suite stayed green. The second fix used ordinary
text and lengthened the sentinel until it did not occur in the raw text,
which misses the spelling that matters: a string can encode the marker
with `\uXXXX` escapes, so the DECODED value carries it while the raw text
does not.

What settles it exactly is **two markers of the same length and two
decodes**. A string that came from the input decodes identically both
times; a string this package substituted differs in exactly the marker. So
the pair of trees names the masked positions with no guessing and no
residual, at the cost of one extra decode on the rare document that
contains a bare non-finite token.

One layer further down: `reprNumber` routed `json.Number("NaN")` into its
INTEGER arm, because "NaN" contains no `.`, `e` or `E` — printing `NaN`
where CPython prints `nan`, straight into a milestone title. And
`Number.Float64()` on `1e309` returns `+Inf` *together with*
`strconv.ErrRange`, so treating that error as "unrenderable" echoed the
source literal. Three defects stacked in one value's path.

### Rule — a fix arrives with no coverage by construction

The corpus that missed the bug is the corpus the fix inherits. Nine of the
first battery's nineteen survivors were mutants of lines written in that
same round, each in the exact dimension the finding was about:

- a bounds fix, against 38 payloads that all drove `maxMilestones=4,
  maxFeatures=3`;
- a blank-id sentinel fix, reachable only through a decomposer whose ids
  come from a generator and are never blank;
- a log-rendering fix, on a channel the differential harness never
  connected.

Reviewing the fix is not the same as covering it, and the mutation battery
is what tells them apart. Every fix in this port now gets mutants derived
from the FILE it landed in — and a survivor is either a missing fixture or
an equivalence worth writing down, never something to leave unexplained.

### Rule — a fixture both sides refuse is not a differential

The r4 battery produced two survivors, and the fixtures were the reason,
not the fixes. Two cases meant to pin `str.strip()` against
`strings.TrimSpace` wrote U+001C and U+001F as **raw bytes inside a JSON
document**. A raw control character in a JSON string is illegal, so
CPython's `json.loads` and Go's decoder both rejected the document, both
produced nothing, and the case reported agreement.

That is the dangerous shape: a broken fixture does not fail, it *passes*.
It looks like the strongest possible evidence — byte-identical output on
both runtimes — while testing neither branch. Written with backslash-u
escapes instead, the same two cases killed their mutants immediately.

The general form: whenever a differential case agrees, ask whether both
sides agreed on an *answer* or agreed on a *refusal*. Only the first is
evidence. The anti-vacuity guards this port already writes for corpora
(`TestThe...CorpusReaches...`) exist for the same reason one layer up —
this is that idea applied to a single case.

### Rule — a compile-kill is not a kill

The same battery reported three mutants `compile-killed`. That reads like
a strong result and is nearly worthless: it says the mutant was
ill-formed, not that any test noticed the behaviour change. Two of the
three failed only because removing a call left an import unused.

Re-expressed so they build — using only packages each file already
imports — one of the three **survived**, and the survival was real. The
mutant reverted `jsonx.Object`, but the test I believed covered it had
been pointed at `jsonx.ObjectOrdered` by an earlier fix in the same
round. The coverage existed for a different function with a similar name.

So: a mutation harness must treat a compile failure as *unusable*, never
as a kill, and re-express it. Otherwise the harness's own error rate
hides exactly the survivors it exists to surface. This is the second
false-green class the battery has produced — the first was the
anchor-miss in r1 (`count(old) != 1`), where a mutant that never applied
was reported as a survivor. Both are the harness lying in the direction
of comfort.

### Rule — a hand-ported helper is a family, not a line

`llm_parse.safe_float` is six lines of Python. It got ported by hand at
four call sites in this repo — `closure.go`, `evolver.go`, `intent.go`,
`now.go` — and no two ports were the same. Three of the four missed the
non-finite guard, all four missed numeric strings, and one of them could
prevent `metadata.json` from being written at all (adversarial
mission-r5 HIGH).

Nobody decided to write it four times. Each site had a confidence field,
each port was three obvious lines, and each was correct enough to pass
its own test. The divergence is invisible per-site and only visible as a
set.

So the rule has two halves:

- **Finding one is evidence about the other N-1.** This is r4's *a fix is
  evidence about its SIBLINGS* narrowed to its sharpest case: when a fix
  lands on something that looks like an inlined helper, the next move is
  `grep` for the shape, not `go test`.
- **The fix is to stop having N.** `pyval.SafeFloat` is now the only port
  of `safe_float`, driven against CPython's own function. A fifth site
  will either call it or be a visible exception.

Corollary for finding them before they bite: a Python helper with a
`*`-keyword signature (`safe_float(value, *, default, min_val, max_val)`)
is one someone thought hard about. Those are the ones worth porting once,
in `pyval` or `pytext`, rather than at the call site.

### Rule — a test named for a differential must RUN the other side

Four tests in this tree were named `...MatchesCPython` and asserted
hardcoded constants:

```go
// what it looked like
if got := Fingerprint(v); got != "3f2a1b0c9d8e" {
```

A frozen snapshot of CPython's output cannot notice CPython moving, and
cannot notice the port drifting toward a stale copy of it. That is
tolerable in a test named `TestFingerprintIsStable`. Under
`MatchesCPython` it is worse than no test, because the name tells the
next reader the boundary is covered. **Every one of the r5 findings sat
under one of these four names.**

The rule is about the name as much as the mechanism:

- If the name says it matches CPython, it must `exec.Command("python3",
  ...)` and compare. No exceptions — a differential that skips when
  `python3` is missing is fine, one that never had it is not.
- If it asserts a constant, name it for what it actually pins
  (`...IsRuneSafe`, `...IsStable`, `...LosesIntNessAsItAlwaysHas`) and
  say in a comment why a constant is the right thing here.
- When deleting one, fold its fixtures into the real differential that
  replaces it and leave a comment saying what was there. The fixtures are
  usually the only surviving record of what someone once cared about.

This is a third member of the false-green family, alongside *a fixture
both sides refuse is not a differential* and *a compile-kill is not a
kill*. All three share a shape: the test reports agreement while testing
nothing, and the report is what you read next round.

### Rule — a mutant that edits the test's own assertion is meaningless

Two r5 mutants were written as, in effect:

```go
- if forked != yamlVersionRows[i].WantFork {
+ if false {
```

Both "survived", and both proved nothing: no test can detect the deletion
of its own check. A mutation battery measures whether a test NOTICES a
change in the code under test, so a mutant that edits the test is outside
the experiment.

When the fix under review *was* a test change — closing a vacuous
assertion, making a table binding — the live question is whether the new
assertion is load-bearing, and that is answered by mutating what the
assertion READS:

| the fix | the wrong mutant | the right mutant |
|---|---|---|
| make the YAML rows binding | delete the `!=` check | flip a fixture row's `WantFork` |
| compare values, not just keys | tautologise the comparison | break `pyval.Plain`'s number arm |

Both re-expressions killed. The general form: **mutate the fixture or the
production code the assertion depends on, never the assertion.**

One harness change came out of the same round and belongs here. The first
r5 battery returned `0 killed, 8 survived` because the `-run` regexes
named tests that did not exist — a nonexistent test cannot fail, so every
mutant read as a survivor. `NO-SUCH-TEST` is now its own reported
outcome, distinct from `SURVIVED`, alongside `ANCHOR-MISS` and
`COMPILE-BROKEN`. Four ways to get a false green, four named outcomes.

### Rule — an extraction is a refactor only if the ORDER survives

mission-r5 extracted a parse loop out of a run loop, and carried one line
across the boundary with it:

```python
# Python
if not checks:                                    # the RAW list
    ...
for _plan_index, check in enumerate(checks[:5]):  # cap on the RAW list
    if not cmd:
        continue                                  # skip INSIDE the loop
```

```go
// what the extraction produced
for ... {
    if cmd == "" { continue }   // <- now BEFORE the cap
    out = append(out, ...)
}
...
for i, c := range checks[:5] { ... }
```

Every value is still right. Every line is still there. The program is
different: the cap now counts a different list, and a six-check plan
whose first command is blank runs five commands in Go and four in
CPython.

An extraction is judged by what it MOVES, not by what it renames. Before
lifting a loop body into a function, write down the order of the
operations that cross the new boundary — filter, cap, gate, side effect —
and check the order is the same on the far side. A filter that moves
across a cap is not a reorganisation; it is a different program.

The corollary for review: **when a round's fix is an extraction, the next
round should re-read the extraction against the Python, not against the
diff.** r4's fix unlocked r5's HIGH and r5's fix introduced r6's. Two
rounds running, the sharpest finding was in the previous round's own
work.

### Rule — a test that reports AGREEMENT may be testing nothing

The false-green family is now four, and they are worth naming together
because a green suite made of them reads exactly like a green suite:

| head | shape | what it cannot see |
|---|---|---|
| frozen snapshot | `if got != "3f2a1b0c9d8e"` under a `MatchesCPython` name | either runtime moving |
| vacuous fixture | both sides refuse the input | any implementation at all |
| corpus that cannot separate | every case in the agreeing region | the actual divergence |
| assertion that cannot fire | `f(x) == f(x)` | anything |

Head 3 is the subtle one, because such a test looks completely correct:
it runs `python3`, it compares real values, it passes. Thirteen all-ASCII
stderr strings cannot tell `strings.ToLower` from `str.lower()`. Every
case sits where the two implementations already agree, so the test is a
measurement of nothing taken very carefully.

**The fix is not to count fixture shapes.** mission-r6 first wrote guards
like "at least one case must be multi-byte and longer than 190 runes",
and one of them was simply wrong about its own threshold while the
comparisons underneath it all passed. Counting the right SHAPE of fixture
does not prove a corpus discriminates.

> **Keep the pre-fix implementation in the test file and run it over the
> corpus. Fail if it does not lose.**

```go
var oldDiffers int
for i, c := range cases {
    if oldErrorFingerprint(c.Reason, c.Result) != want[i] { oldDiffers++ }
}
if oldDiffers < 2 {
    t.Fatalf("the pre-fix implementation matches CPython on all but %d of "+
        "%d cases: this corpus could not have caught the finding",
        oldDiffers, len(cases))
}
```

It costs a dozen lines, it is exact rather than heuristic, and it makes
the test self-falsifying: the day someone narrows the corpus, the guard
fires. Two genuine survivors in r6's battery were found this way and
nowhere else. The kept copy carries a comment saying it must never return
to production.

The census that prompted this: **130 tests in this tree carry a
`...MatchesCPython`-style name and 65 never invoke `python3`.** The name
is a promise about the mechanism. Nine were walked in r6; the rest are
bounded, mechanical follow-up.

### Rule — `float()` and `str.strip()` do not strip the same set

Related to *a claim in a comment is load-bearing*, but worth its own
entry because the shape recurs: two CPython builtins that both "strip
whitespace" and disagree about which whitespace.

```
str.strip() strips 29 code points
float()     strips 25
the difference: U+001C U+001D U+001E U+001F, in that direction only
```

So `pytext.Strip` before `strconv.ParseFloat` is not `float()`. It parses
values CPython refuses outright, and the parsed value reaches the shared
store while CPython's default does not. `pytext.FloatStrip` names the
narrower set.

The generalisation: **do not reach for the nearest already-ported helper
because its name is close.** `Strip` was right for `str.strip()` and
wrong one call site over. When a port needs "the whitespace set", ask
which function's whitespace set, and measure it.

Two more measured on the same pass, both cheap and both worth having
written down:

- `float()` accepts any Unicode decimal digit (`float("٠.٥")` is `0.5`);
  `strconv.ParseFloat` is ASCII-only.
- `strconv.ParseFloat` accepts `"nan"`, `"inf"` and `"-inf"` **with a nil
  error**. Any port that treats "parsed successfully" as "safe to store"
  can put a NaN into a `json.Marshal` and lose the entire row. This is
  the second time in two rounds that a non-finite value cost a whole
  record.

### Rule — Go's `\b` is ASCII and Python's is not

Alongside `\s` (5 vs 29) and `\w` (ASCII vs `str.isalnum()`), the word
BOUNDARY differs too, and it is the one that is invisible in a pattern
because it has no character class to inspect. Measured: `\bplan\b`
matches `"研究plan"` in Go and not in CPython.

`pytext.WordStart` / `WordEnd` stand in. They **consume** the boundary
character, because RE2 has no lookaround — correct for a boolean
predicate, wrong for match offsets or for two adjacent matches sharing a
boundary. That constraint lives in their doc comment, not in a reviewer's
head.

The `\w` class itself measured clean: 0 false positives against CPython's
142940 code points, 5004 false negatives, all of them the same
Go-15.0-vs-CPython-16.0 table skew the digit class already documents.
Named residual; the fix is a newer toolchain, not a hand-copied list.

### Rule — one dict, one reading

`_apply_suggestion_action` coerces five fields at the top and uses them
in three places: the action, the audit row, and the operator-facing block
reason. The Go port coerced them at the top and then re-read the raw map
in the audit row, so the row disagreed with the action it was auditing —
an absent confidence audited as `null` where CPython writes `0.5`.

This is the same defect as the r18 finding in the Python (capture and
action each calling `load_skills()` independently), one layer down. When
a function reads a dict more than once, the second read is a second
source of truth, and the two drift the moment one of them gains a
default. Read once into a typed struct; pass the struct.

The Go idiom that invites the drift is worth naming, because it is not
obviously wrong:

```go
category, _ := d["category"].(string)
if category == "" { category = "observation" }
```

`d.get(key, default)` keys on **presence**. That Go spelling defaults on
an empty stored value too, so `"category": ""` is written as
`"observation"` here and as `""` there. `pyval.GetOr` is the honest port.


### Rule — the store's shape is the contract, so the port owns the writer

Eight files reached a shared store through `encoding/json`. It disagrees
with `json.dumps` three ways at once — **sorted** keys vs insertion order,
**HTML-escaped** `<>&`, and **raw UTF-8** vs `ensure_ascii` — and no test
saw it, because every test decoded the bytes first and a decode erases all
three.

Every durable writer now renders through `pyval`. The measurable case:
`FailedCheckSignature` is `"%s => exit %d: %s"`, so every failed-check
entry this port had ever written carried `=>`.

Two hazards found while building it, both of which read exactly like a
real divergence:

* **`FromPlain` must not round-trip through `encoding/json`.**
  `json.Marshal(3.0)` is `"3"` and `json.dumps(3.0)` is `"3.0"`, so the
  reparse silently converts whole floats to ints — and `confidence`,
  `alignment_score_avg` and every success rate are floats that are often
  whole.
* **The same hazard applies to a CPython probe's TRANSPORT.** An untagged
  whole float arrives at Python as an int and the probe answers `"3"`.
  The differential tests carry tagged transports (`{"__float__": ...}`,
  `{"__obj__": [[k, v], ...]}`) for exactly this reason; the second also
  keeps `encoding/json` from sorting the key order the test exists to
  check.

**Named residual — CLOSED 2026-08-27, see the section at the end of this
file.** `pack.json`'s key ORDER. The manifest was a `map[string]any`
across ~15 call sites including a foreign-file decode, so `FromPlain`
sorted it. Escaping and indent were Python's; order was not. The
diagnosis here was right and the prescription was right — "the fix is a
typed manifest, not a renderer change" — and it named the shape of the
work correctly enough that the fix, when it came, was `pyval.Obj`
threaded through the whole package rather than one writer patched.

The one thing this note got wrong is the reasoning that made it a
residual: *nothing hashes those bytes*. True, and irrelevant. `pack` is
the interop FORMAT — the sole reason it exists is that the other engine
reads it — so "same values, different bytes" is the failure this package
is supposed to not have, hash or no hash.

### Rule — an extraction is a refactor only if ORDER is preserved

`write_metadata` is a read-merge-write, not a write. It preserves the
ordinals of keys already on disk, pops a key written as `None`, and fills
`started_at` first-writer-wins **after** the merge. The port had it as a
whole-file overwrite, which is right for every value and wrong for the
file.

### Rule — the boundary stand-ins have two ends and one direction

`pytext.WordStart` / `WordEnd` **consume**. Three consequences, all of
which cost a fix this round:

1. They are safe only at the two **ends** of a pattern, in a boolean
   predicate. An INTERIOR boundary must be folded into what follows —
   `NotWordClassPlus(x)` is that fold.
2. They are wrong wherever the caller needs match OFFSETS or the matched
   TEXT (`FindString`, `FindAllStringIndex`, `ReplaceAllString`). Use a
   capture group, or leave the ASCII `\b` and name the residual.
3. **A trailing `\b` after a non-word character means the OPPOSITE of
   `WordEnd`.** `\bhttps?://\b` matches `https://x` and not a bare
   `https://`: the position after `/` is a boundary only when a WORD
   character follows. `urlBounded` is that spelling; substituting
   `WordEnd` inverts the test.

Rule 3 also covers every alternation branch that ends in a SPACE — `ls `,
`find `, `jq ` in the static hints.

**Named residual:** `scrub`'s Identifier patterns keep Go's ASCII `\b`,
and the reason is rule 2. Their consumer is `ReplaceAllString`; writing
the consumed boundary back through `${1}` fails on ADJACENT occurrences
("clawd clawd" shares one space, the first replacement eats it), and a
replace-until-stable loop is a third behaviour rather than a port. The fix
is an index-walking replacer.

**Named residual:** `budget.markerRe`'s ASCII `\d`. `Clip` writes those
digits itself with `%d`, so a genuine marker is always ASCII and the two
classes agree on every marker this code can produce. They differ only on a
FORGED one, and there the Go behaviour leaves content alone — the safe
direction.

### Measured — `str.lower()` is not `strings.ToLower`

`str.lower()` EXPANDS U+0130 to two code points; Go's folds it to one.
Measured through the real tokenizer:

```
_tokenize("DIFFİCULT case")  -> ['diffi', 'cult', 'case']
_tokenize("DİFFICULT stdin") -> ['fficult', 'stdin']
_tokenize("ΣΟΦΟΣ answers")   -> ['answers']      # [^a-z0-9]+ strips it
```

`pytext.Lower` is the port. The third line is the one worth keeping in
view: the non-ASCII token does not become a Latin token, it disappears.

### Measured — `float(str)` is not `strconv.ParseFloat`

`pyval.ParseFloat` is the single implementation, and it differs from
`strconv.ParseFloat` in three places:

* `strconv` accepts **hex** float literals (`0x1p-2` -> 0.25); `float()`
  raises.
* `float()` accepts **Unicode** decimal digits (`float("٠.٥")` == 0.5);
  `strconv` is ASCII-only.
* CPython raises on overflow; `strconv` returns `ErrRange` **with** ±Inf.

Both accept `"1_000"`, and both accept `"nan"`/`"inf"` — so does
`float()`, which is not a bug in either. The strip set is
`strings.TrimSpace` exactly: `float()`'s whitespace set is `str.strip()`'s
MINUS U+001C–U+001F (25 code points vs 29).


### mission slice 3 — run_mission, the drain, and five seams that are not silence

`run_mission` reaches five subsystems this port does not have: `hooks`,
`sprint_contract`, `boot_protocol`, `ancestry` and `loop_artifacts`.
Four of the five are ALREADY optional over there — sprint_contract and
boot_protocol behind `try/except ImportError`, every `run_hooks` and the
whole ancestry block inside a bare `except Exception: pass`. A Python run
on a box where those imports fail takes exactly the path this port takes,
so the port is a supported configuration rather than a degraded one.

They are **nil-able seams** on `RunOptions`, not silence. What each one
costs when nil:

| Seam | Nil behaviour | What is lost |
|---|---|---|
| `Hooks` | no hook ever fires | no hook can BLOCK a feature or a milestone, and `get_injected_context` contributes nothing to the worker's context |
| `Ancestry` | `mission.ancestry_context` stays `""` | the mission carries no prior-work context |
| (sprint contract) | unported entirely | **`feature_list.json`'s `passing` flags are never set** — `MarkFeaturePassing` has no caller in this runtime |
| (boot protocol) | unported entirely | the worker gets no completed-features / dead-ends block |
| `ResolveSlug` | REQUIRED when `Project` is empty | — |

The third row is the one to watch on the interop side: a manifest that
never advances reads exactly like a mission that never passed anything,
and the Python runtime reads that same file.

`RunFeature` is **required**, not nil-able. The agent loop is the one
thing that is not optional in Python, and a mission that silently ran no
features and still reported a status would be worse than an error.

**Import direction.** Python does `from agent_loop import run_agent_loop`
INSIDE the function body — the idiom for "this would be a cycle at module
scope". Go has no lazy import, so the edge lives in `internal/missionrun`
and `orch` stays free of `loop`.

**Named divergence — two dropped kwargs.** `missionrun.FeatureRunner`
cannot pass `project=` or `ancestry_context_extra=`, because `loop.Opts`
has neither. The loop resolves its OWN slug from the feature title, so a
mission's features can land in per-feature project dirs where Python puts
them all under the mission's project. `ancestry_context_extra` is always
empty today (all three of its producers are unported) so the drop costs
nothing yet — but it will the moment one of them lands. Both are dropped
VISIBLY: `FeatureRequest` carries them and the wiring ignores them, so
the gap is inspectable rather than invisible.

### The DAG's named residual, closed

`mission_dag.go` shipped with a residual: `PersistFn` walks EVERY
milestone, including ones whose `runOne` is still writing to them.
Python's `_save_lock` guards only `save_mission`, so a snapshot there can
capture a sibling mid-mutation — a LOGICAL tear that self-heals at the
next persist. Under the GIL that is all it is. In Go the same shape is a
data race, which is undefined behaviour rather than a stale field.

`RunMission` closes it by holding one lock on BOTH sides: every mutation
of a `Milestone` or `Feature` goes through `missionRun.lock`, and so does
the snapshot. The feature and validation WORK stays outside it — the
critical sections are field assignments, so the concurrency the DAG
exists to buy is untouched. `TestConcurrentMilestonesSnapshotWithoutRacing`
runs four concurrent milestones with two concurrent features each under
`-race`.

### The two lanes really do disagree, and it is not a bug

`run_mission` and `drain_next_mission` write the same fields of the same
file with different vocabularies. Ported verbatim, and pinned by a test
each, because a Go port that "fixed" the inconsistency would be changing
behaviour under the name of a port:

* `run_mission` narrows ANY non-`done` loop status to `blocked`. The
  drain stores it **raw**, so a `stuck` can appear in `mission.json`
  where run_mission's own vocabulary cannot produce one.
* Milestones: `done`/`partial`/`failed` there, `done`/`blocked` here.
  Missions: `done`/`partial`/`stuck` there, `done`/`blocked` here.
* The drain has no validation gate and no hooks.
* `all()` over an EMPTY feature list is **True**, so a milestone with no
  features drains as done with nothing having run. A Go
  `len(features) > 0 &&` guard added by instinct would be a divergence.

### Three Python details that do not survive a careless port

* **`max(1, int(cfg))` and its `except` are different numbers.** A
  configured `0` becomes **one** (the floor); garbage becomes **two**
  (the `except (TypeError, ValueError)`). Reading Go's zero value as
  "unset" collapses them and silently doubles the concurrency an operator
  asked to turn off. `orch.MilestoneWorkers` is the one implementation,
  exported so the arithmetic is testable without a replay in a test file.
* **`get_bool`, not `bool(get(...))`.** `mission.parallel_milestones` is
  THE revert lever, and `bool("false")` is `True` — a quoted `false` in
  YAML has to actually revert. The test asserts the LANE, not the parse.
* **The status branch is guarded by `mission.status != "stuck"`**, and
  `partial` counts as progress. So a failed milestone beside a partial
  one is `partial`, not `stuck` — and a mission the scheduler already
  marked stuck keeps that verdict even when the counts would compute
  something softer.

`goal[:80]` and `briefing[:3000]` are Python str slices — **runes**. Go's
`s[:n]` would cut a multi-byte character in half and produce an invalid
string where Python produces a shorter valid one, and both of those
strings reach a durable store or a Telegram message.

## json.dumps has FIVE defaults, not three

The port's contract is BYTES, so this is the list that matters. A writer
that produces a shared row must match all five, and `internal/pyjson` and
`internal/pyval` both do as of r8:

| fork | encoding/json | json.dumps |
|---|---|---|
| key order | sorted (maps) | insertion order |
| `< > &` | `<`-style | untouched |
| whole float | `1` | `1.0` |
| separators | `,` `:` | `, ` `: ` |
| non-ASCII | raw UTF-8 | `\uXXXX` |

Two consequences that cost real time:

- **An astral rune is a surrogate PAIR.** `json.dumps("\U0001F600")` is
  `"😀"`, not `"\U0001f600"`.
- **A struct writer looks safe** because encoding/json emits declaration
  order — but order was never the only fork, and a struct is the ONE
  shape that can hit the whole-float fork on a field that is always whole
  (a confidence of 1.0, a unix timestamp cast to float64).

### The rule r8 was actually about

**Audit a shared helper against the SOURCE, not against its own doc
comment.** `pyjson` existed to end per-package emitter drift and its doc
named three of the five forks. It implemented three. Every store routed
through it inherited the missing two, and the sharing is what spread
them. A shared emitter with no differential test is a single point of
silent, distributed failure.

Corollary, from the same round: **a hand-spliced fragment carries its own
spelling.** `ArchiveSkills` appended `,"archived_at":...` onto a
correctly-spaced line and produced a row neither runtime writes. If you
extend a rendered line instead of re-rendering it — which is often right,
because re-rendering reorders keys — the fragment must be spelled the way
the line is.

And: **a round trip through encoding/json is not type-preserving in
either direction.** Marshal-then-unmarshal into `map[string]any` turns
every int into a float64, so a downstream Python-correct emitter then
writes `3.0` where `asdict()` keeps `3`.

### Test-shape rules earned in r8's battery

- **Drive production, do not replay it.** Two tests written in this round
  survived their own mutants because they rebuilt the render and the sort
  in the test body. Extract a named production function and call it. A
  replay is a second implementation, and it agrees with anything.
- **A byte-level needle must SPAN the separator it pins.** A needle
  ending at a closing quote survives a mutant that compacts only `, `.
- **A "named accepted divergence" needs a measurement behind it.**
  Separator spacing was recorded as accepted in two files. Nobody had
  measured it; it was pyjson emitting the wrong thing. Nested-map key
  order IS a real accepted divergence — a Go map has no order — and it is
  stated at the assertion that is blind to it, not normalized away in
  silence.

### Named residuals after r8

- Nested-map key order: `pyjson.Value` sorts a nested `map[string]any`
  because a Go map has no insertion order. Fixing it needs an
  ordered-context `Event`/writer API. Visible in the captain's log's
  `context` object.
- Tool-schema key order in the prompt injection (`llm.go`): `Parameters`
  is a Go map, so Python's insertion order was gone upstream of the
  emitter. `MarshalIndent` sorted too, so this changed nothing.
- Escalation-ledger and event-row key order (`scans/notify.go`): the
  entry arrives as a Go map. Escaping and ensure_ascii are recovered.

## The notify lane: three copies of one contract

`internal/notify` ports `notify.py` and `escalation_context.py`. A partial
copy of the same writers already lived as private helpers inside `scans`,
which is the r8 finding one layer up: a shared emitter with a second copy
drifts, and nothing notices because each copy is self-consistent.

What the second copy had actually drifted into:

- Its `escalations.jsonl` rows sorted their keys alphabetically (a Go map),
  where Python emits `{"ts": ..., "event_type": ..., **payload}` — ts and
  event_type LEADING, in that order.
- Its `events.jsonl` rows did the same, so `event_type` was not first.
- It had no hook lane at all. A substrate that registered `notify.command`
  never received a cadence verdict, and the failure is invisible from
  the substrate side: no hook call looks exactly like an event that was
  filtered out.

### `{"a": 1, **payload}` keeps the position AND takes the value

A dict literal followed by a splat is easy to port half-right. A payload
carrying its own `ts` OVERRIDES the value but the key KEEPS its leading
position. `pyval.Obj.Set` already has this semantics, which is why the port
is a `Set` loop rather than an append.

### A presence default is not a truthiness default

`str(payload.get("result_excerpt", payload.get("summary", "")))` falls back
only when the key is ABSENT. A payload carrying `result_excerpt=None`
projects the literal string `"None"`, because that is what `str(None)` is.
Reading it as "falsy means fall back" is the natural port, it is wrong, and
it is wrong precisely for the payloads where a producer explicitly recorded
"there was no excerpt".

Two lines further down, `if payload.get("goal_verdict_source")` IS a
truthiness test. The same payload is read two different ways in adjacent
lines, so both need pinning.

### `pyval.Bool` is not `pyval.Truthy`, and the comment does not decide it

The first revision of this port aliased `truthy` to `pyval.Bool` with a
comment saying "Python's bool() for the values that reach these fields".
`pyval.Bool` is a bare type assertion and documents itself correctly for
its own callers; the comment over it was the false claim. Effect:
`goal_verdict_source: "judge"` read as false and the `source=` clause
vanished from every `run_verdict` row. Caught by a test written in the same
session, which is the only reason it is a footnote instead of a residual.

### Three packages had re-implemented `bool()`

`evolver.pyTruthy`, `graduation.truthy` and `skills.pyBool` were three
private copies of Python's `bool()` with three different case sets — while
a complete `pyval.Truthy` already existed. Measured:

| copy | `int` | `json.Number` | default for an unknown type |
|---|---|---|---|
| `evolver.pyTruthy` | no | no | `true` |
| `graduation.truthy` | no | no | `v != nil` |
| `skills.pyBool` | no | yes | `true` |
| `pyval.Truthy` | yes | yes | `true` (Python's rule) |

The missing `case int` is the live one. Every value arriving from
`encoding/json` is a `float64`, so the gap is invisible on the read path —
but a value built in Go (a struct field, a count) is an `int`, and
`bool(0)` came back TRUE. `graduation`'s `v != nil` default was wrong in
two directions at once: Python says an unrecognized type is TRUE, and a
typed nil in a Go interface is not nil. All three now delegate.

The lesson is not "write a shared helper" — one already existed. It is that
a package reaching for a local four-line helper does not go looking for the
shared one, and nothing in the build tells it to.

### The clip breaker is not a length bound

`context_budget.clip(text, cap)` keeps `cap` characters and then APPENDS a
marker naming what it cut, so the field is LONGER than its cap by design.
It is also idempotent only at the same or a WIDER cap. The notify
projection clips at 300 and `write_event` clips again at 200, so the outer
marker describes the 343-character string it received rather than the
500-character original, and the first marker survives nested inside the
payload. Python nests it identically — that makes it a fact about the
format, not a divergence, and a test asserting "at most 200" is asserting
something neither runtime does.

### Two bounds in one row, different kinds on purpose

In the same `events.jsonl` row, `goal` is a bare `goal[:80]` slice that
says nothing and `detail` goes through the breaker that announces itself.
That asymmetry is `write_event`'s long-standing contract, not an oversight,
so the port keeps both and the test records which is which.

### Named residuals after r9

- The escalation ledger sorts payload keys: the payload arrives as a Go
  map, so Python's insertion order was gone before the writer saw it.
  Escaping, ensure_ascii, float spelling and the separators are recovered.
  Same for the hook's stdin. This is the r8 nested-key-order residual
  reaching one more surface, and the fix is the same ordered-payload API.
- `escalationClipFields` is a hand-maintained list that must stay synced
  with `Emit`'s callers. Python found the same hole twice — round 14 for
  the canonical names, round 15 for two live ALIASES — which is evidence
  the list is the wrong shape. A typed per-event schema is the real fix.
- `notify.events` set to a bare STRING behaves as a SUBSTRING test in
  Python (`"escalation" in "escalation_only"` is True). The port treats it
  as a one-element list, which is stricter. Config error either way; named
  rather than accidental.
- `FamilyROILine` bounds its ledger read at 8MB where Python reads
  unbounded. When the cap bites, the OLDEST rows drop, so "on record"
  understates rather than overstates — the safe direction for a line whose
  job is to say "this recurs".
- Nested-context key order inside an escalation payload is still sorted
  (the r8 residual, unchanged).

### Who calls Emit, and who does not yet

`internal/notify` exists; being wired everywhere is a separate question,
and leaving it unstated would let a later reader assume the lane is
complete. Every `notify.emit` call site in the Python tree, audited:

| Python site | event | status |
|---|---|---|
| `evolver_scans._notify_verdict` | `self_improvement_verdict` | **ported** (`scans.notifyVerdict`) |
| `director.py:1236` | `recursion_checkin` | next tranche (`handle_escalation`) |
| `handle.py:951,1007` | `backend_actionable`, `run_completed` | `handle_queue` tranche |
| `cli.py:2411` | `run_verdict` | CLI tranche |
| `container_exec.py:929,1020` | `backend_actionable` | unported subsystem |
| `audit_repair.py:662` | `escalation` | unported subsystem |
| `loop_init.py:186` | `escalation` (daily budget gate) | the REFUSAL is now decided in `loopinit.BudgetGate`, which returns the payload; the notify call itself still has no twin, because `_stamp_refusal_verdict` writes to the run dir |
| `loop_blocked.py:841` | `escalation` (blocked step) | inside `_act_blocked_step`, already on `blocked.go`'s named not-ported list (navigator shadow) |

No ported Go path drops a notification. Every gap above is a gap in the
SUBSYSTEM, not in the lane — which is the distinction worth keeping,
because the two look identical from the substrate's side.

`DecisionLine` and `FamilyROILine` have no caller yet for the same reason:
their two Python call sites are `loop_blocked`'s navigator override and
`director.handle_escalation`. They are ported now because the director
tranche is next and the prose needed its differential either way.

### An open seam the director tranche will hit immediately

`notify.WriteEvent` currently hardcodes `project`, `loop_id`, `step`,
`step_idx`, `model` and `elapsed_ms` to their zero values, because the
notify projection is the only caller and Python's `notify._emit` passes
only `goal`, `status` and `detail`. `director.handle_escalation` calls the
same `observe.write_event` with `project`, `loop_id` and `status` filled
in, so the next tranche needs the full field set.

Two ways to go, and the choice is worth making deliberately rather than
by whichever is convenient at the call site:

1. Widen `WriteEvent` to an options struct and have the notify projection
   pass its own defaults. One writer, one row shape.
2. Let the director write its own row.

(1) is right, and the reason is this round's own finding: (2) is how you
get a second copy of a shared writer, which is what `scans` had and what
this tranche just removed. It is not being done speculatively now — the
widening should land with the caller that needs it, so the new fields
arrive with a test that exercises them rather than as untested capacity.
Named here so the next tranche does not rediscover it as a choice.

Two more things the director port will need that this tranche did not:
`calibration.jsonl` rows are `json.dumps` with a FLOAT `time.time()` `ts`
(content-key, this port's recurring family — a whole float must spell
`1756000000.0`, not `1756000000`), and the escalation summary artifact is
markdown PROSE with a `.title()`-cased action in its heading.

### ensure_ascii's threshold is 0x7F, not 0x80

CPython's `ESCAPE_ASCII` matches anything outside 0x20..0x7E. DEL (U+007F)
is therefore escaped **even though it is ASCII**, while Go's
`encoding/json` follows RFC 8259 and escapes only below 0x20. A port
gating on `utf8.RuneSelf` writes a raw DEL byte into the shared store --
which arrives through captured terminal output more often than it sounds.

The package doc had named this case from the day it was written. The code
under it used 0x80, and r8's own CPython differential walked U+0001 ->
U+001F -> "cafe", stepping straight over the boundary. When a doc comment
names a specific value, the corpus should contain that value.


## The run dir's name is a lookup key

Python's `runs.run_dir(handle_id)` returns `runs/<handle_id>-<nickname>`,
where the nickname is two words indexed by the first two bytes of
`sha1(handle_id)` out of a 50-adjective and a 50-noun tuple. The Go port
used the bare handle id, on the reasoning -- written into this package's
doc comment -- that readers glob `runs/*/metadata.json`.

Two of the three readers do glob. `run_dir` does not: it BUILDS the path,
and it is

  - the first thing `resolve_run_dir` tries, before the index;
  - how `create_run_dir` decides a run dir already exists;
  - the path every resume goes through.

So a Go-created run was reachable from Python only via the index or the
legacy scan, and a Python `create_run_dir` for the same handle id created
a SECOND directory beside it. Two run dirs for one run, each with half the
metadata, in a shared workspace.

The two word lists are spelled out in Python's order, and the order is
contract: the nickname is an index into them, so reordering either one
silently renames every future run dir while leaving existing ones in
place.

## Every metadata write publishes the index

`WriteMetadata` calls `IndexRunDir` on the merged object before
serializing it, because both Python writers of this file do
(`write_metadata` at the create path, `_stamp_metadata_at` at the merge
path). A loop id that appears in `metadata.json` is findable from the
moment it lands; anything less means the ref is reachable only through a
migration, and a workspace whose migration is already marked complete
will never run one again.

The ORDER is Python's and is deliberate: refs before metadata. A crash
between the two leaves an entry pointing at a run dir whose metadata is
one revision old, and a reader that follows it finds a real run. The
other order leaves a published loop id with no entry, which reads as "no
such run".

## The run-ref index (v2)

`<workspace>/.run-ref-index-v2/<sha256(ref)>.json`, a SIBLING of `runs/`,
each file `{"ref": ..., "run_dir": <bare name>}`. The refs for one run are
`handle_id`, `loop_id`, every entry of `loop_ids`, every `loops[].loop_id`,
and `origin.resumed_from`.

The plural keys are what v2 added and why the version was bumped: the
loops ledger stopped stamping the singular `metadata.loop_id`, so every
v1-indexed run was unreachable by `loop_id` -- which is the key the
outcome ledger and the verdict seam JOIN on, so every loop_id-keyed
consumer silently no-op'd.

Properties the port preserves that are easy to lose:

  - **It is an optimization, never an availability dependency.** A
    read-only, corrupt, or lock-starved index degrades to the historical
    directory scan. That posture is the reason for the otherwise-odd
    error handling.
  - **A duplicate ref resolves to the alphabetically FIRST live run dir**,
    because that is what a sorted scan returned. An optimization must not
    answer a question differently from the fallback it replaces. LIVE is
    load-bearing: applied to a pruned directory the same rule would pin a
    ref to a name nothing can resolve, permanently, since every republish
    reads the same stale entry and keeps it.
  - **Repair is local.** A corrupt leaf unlinks itself, rescans for its
    own ref, and republishes -- it does NOT invalidate the global
    migration marker, which would force every unrelated miss through a
    second O(all runs) rebuild on account of one damaged file.
  - **Two different defaults on the marker.** An absent `complete` key
    means complete (an old marker predates the flag); an unreadable
    marker means NOT complete. Python answers these differently -- a
    `.get("complete", True)` inside a `try` whose `except` returns False
    -- and collapsing them into one default breaks one case or the other.
  - **A plain miss is not corruption.** A missing entry file returns
    early; only a PRESENT but unusable one triggers unlink-and-rescan.

Testing note, learned here: the migration and the local repair each turn
a real gap into a slow success, so a test that leaves either one
available proves nothing about the thing it names. The tests plant a
`complete: true` marker, or delete the index outright, depending on which
half they are pinning.

## `Path(name).name == name` is a weaker guard than it looks

Measured: `Path("").name` is `""` and `Path("..").name` is `".."`, so
both equal themselves and pass `runs.py`'s traversal check. Both then pass
the `is_dir()` test that follows, because `root/""` is the runs root and
`root/".."` is the workspace directory.

The port keeps `pathDotNameIsSelf` as a faithful, CPython-diffed
reproduction and refuses `""`, `"."` and `".."` on top of it, in
`isBareName`. Named divergence, safe direction, with a test that
demonstrates the consequence rather than asserting the rule.

## `str(x or "")` is the idiom, not `str(x)`

`pyval.Str` is `str()`, and `str(None)` is the four-character string
`"None"`. Nearly every `str()` in the Python source is guarded -- by
`or ""`, or by reading through `d.get(k, "")` first. Three sites in this
tranche reached for `Str` alone and produced the literal `"None"` in a
field that should have been empty; one of them as an index key, so every
run without a `loop_id` collided on one file.

`Obj.GetString` is `d.get(k, "")` and already existed. `pyval.StrOrEmpty`
is `str(x or "")` and now exists, with a test. Note that it is a
TRUTHINESS gate: an id of integer `0` vanishes, an id of `5` is spelled
`"5"`. That is Python's actual behaviour at these sites.

## The two stop-verdict writers are deliberately asymmetric

`runs.stamp_run_stop_verdict` does **no** vocabulary check;
`memory_ledger.stamp_outcome_stop_verdict` does. That is Python's
arrangement and the port keeps it: an off-vocabulary value fails to
UNSTAMPED at the ledger so a reader's status fallback applies, and a
metadata file plus a ledger row that disagree about whether a verdict was
accepted is worse than either rule applied consistently. A test pins the
asymmetry so a future reader who notices the "missing" check finds out it
was a decision.

Other properties worth naming:

  - The metadata tuple REPLACES WHOLE (a new verdict without a reopen
    payload pops the stale one, even for the same verdict); the ledger
    row MERGES (absent evidence leaves the existing evidence standing).
    Different contracts on purpose: metadata describes THIS ending, the
    ledger row accumulates what is known about the run.
  - The refine note is composed BEFORE the clip, or the note is what gets
    cut.
  - `EvidenceOut` is captured inside the lock. Python's round-2 review
    found a caller re-reading `metadata.json` after release, and a
    concurrent writer substituting its content into the caller's ledger
    row. The Go signature makes the correct thing the only available one.
  - `RunDir` is REQUIRED, where Python defaults to the process's active
    run. This writer's only caller stamps a run that ended somewhere else.

## A conditional rewrite is not a read-modify-write

`record.LockedRMW` writes whatever its callback returns, unconditionally.
`stamp_outcome_stop_verdict` must not write on a MISS -- the
splitlines/join round trip normalizes Python's eight extra line
separators to `\n` and appends a trailing newline, so a failed lookup
would become a silent whole-file edit on a store the other runtime is
appending to. Even returning the input unchanged replaces the inode and
races an appender.

The port uses `record.Locked` + read + conditional `AtomicWrite`, which is
the shape Python has.

Assert the INODE, not the mtime. Measured on this box: `os.WriteFile`
followed immediately by `AtomicWrite` leaves both stats reporting the
same nanosecond, so an mtime assertion here cannot fail. `AtomicWrite`
always renames a fresh temp over the target, so the inode necessarily
changes -- and it is also what the harm is about, since an appender
holding the old file open keeps writing into a file nothing will read
again.

## Named residuals after r10

  - The legacy scan and the migration read every run's `metadata.json`
    with no size bound. Python does too.
  - `IndexRunDir`'s failure path invalidates the marker but does not
    report; the caller cannot distinguish "indexed" from "will rebuild".
  - `runs.remove_run_index`'s Python twin is ported, but nothing in the Go
    tree prunes runs yet, so it has no caller.
  - `ResolveRunDir` takes an explicit workspace where Python reads
    `MARO_WORKSPACE`. Consistent with the rest of this port; noted because
    a future CLI entry point has to supply it.
  - The refs-before-metadata write order is asserted by nothing. It is a
    crash-ordering property, and testing it needs fault injection between
    two statements; the mutant that swaps them survives every test here
    and was left out of the battery rather than counted as killed.
  - The stop-verdict rail is ported; the CALLERS are not.
    `director.handle_escalation` is the next tranche, and
    `agent_loop`'s fence stamp, `loop_finalize`'s ending stamp and
    `handle`'s demotion stamp are three more Python sites that will need
    the same owner when their tranches land.

## r11 — `director.handle_escalation`, and the file the harness was told to ignore

`handle_escalation` is the closure mechanism for the recursion tree: a
task that has been through several continuation passes without completing
gets a reasoned decision from a higher layer instead of accumulating
silently. Four actions, and each is a different KIND of durable write —
`continue` and `narrow` enqueue, `close` stamps a stop verdict on the run
AND its outcome row then writes an operator artifact, `surface` writes
the artifact and stamps nothing. That last asymmetry is load-bearing: a
judged close is the one seam where reachable-but-not-worth-it gets
decided, and a surface means the operator has not decided anything yet,
so recording a verdict there would put a judgment in the store that
nobody made.

The port of the decision logic was uneventful. Everything below came out
of the tests.

### A skip by name is a claim you never re-examine

The r10 differential compared the whole workspace: every file the call
wrote, the SET of names and the bytes of the deterministic ones. It
skipped `.lock` sidecars, for the obvious reason — their contents are
scratch, and comparing them would be comparing noise.

Skipping them by NAME also skipped the question of whether the two
runtimes lock the same things. They did not.

**`memory/events.jsonl` is deliberately unlocked in Python** and the port
had it under the flock appender. Python says why, in the function:

```python
# Deliberately UNLOCKED: every field is length-capped so the line is
# well under PIPE_BUF (single O_APPEND write, atomic on Linux), and
# file_lock._report_timeout calls this — locking here would recurse
# into the lock machinery while it's reporting a timeout.
```

All three clauses are real. The atomicity comes from the row's SIZE, not
from a lock (goal 80, step 120, detail 200, three pathologies at 40/160 —
the line cannot approach PIPE_BUF). It must never block, because it is on
the hot path after every step and it is the surface that reports stalls.
And `file_lock._report_timeout` really does call `write_event`
(`file_lock.py:224`), so taking a lock to report a lock timeout recurses
— which Go's own `Locked` doc already says it cannot survive ("NOT
REENTRANT ... a nested Locked on the same path from the same process
blocks against ITSELF until the lock deadline and then fails").

Go has no lock-timeout reporter yet, which is exactly why this was the
cheap moment to fix it rather than the expensive one. `record.AppendUnlockedLine`
is the honest shape: one O_APPEND write, no lock file, no torn-tail
framing, errors the caller's to swallow.

The visible symptom was one zero-byte file — `events.jsonl.lock` in every
workspace Python leaves clean — and the row bytes were identical either
way. The harness had been told not to look.

### Two more finds from the same tightening

Once the lock sidecars rode in the comparison by name, two more
divergences fell out in the same run:

**`StampOutcomeStopVerdict` decided its miss above the lock.** A missing
outcomes ledger is a miss in both runtimes, but Python arrives there from
INSIDE `locked_write` — so it leaves the `.lock` and the `memory/`
directory behind, and its check is synchronized. The port had an
`os.Stat` short-circuit above the lock, under a comment claiming Python
"reads first ... before any writer is involved". Python does not; the
lock comes first (`memory_ledger.py:921`). The no-phantom-store property
the stat was supposedly buying is carried by the conditional
`AtomicWrite`, which does not run on a miss on either side. The stat cost
a TOCTOU window Python does not have — a row appended between the stat
and the lock is one Python stamps and this did not — and bought nothing.

**`record.Locked` did not create its lock's parent directory.** Python's
acquisition mkdirs unconditionally (`file_lock.py:144`), which is the
only reason a locked write into a cold workspace works there at all.
`orch/mission.go` had already papered over this at ONE call site with a
comment saying it belonged down in `record` and that "every direct
AppendRawLine caller has the same hole until it moves". Removing the stat
above made the hole reachable, and moving the mkdir was the fix for both.
The by-hand copy is gone.

### A comment can assert what the type forbids

The event row the escalation writes carries `project` and `loop_id`, and
the port's comment said, correctly:

> `project` is the RAW parent_job_id, unsliced — a non-string value
> reaches json.dumps here and is written as whatever it is

The line under it read `Project: pyval.Str(taskGet(task, "parent_job_id",
""))`, because `EventFields.Project` was typed `string`. The comment was
right about Python and the type made it impossible to honour: a task with
an integer `parent_job_id` wrote `"4242"` against CPython's `4242`, and
one with a null wrote `"None"` against `null`.

`write_event` SLICES exactly three of its fields — `goal[:80]`,
`step[:120]`, `_cb_clip(detail, 200)` — and touches nothing else. The
slice is what makes a non-string fatal there (it raises, the caller's
blanket `except` swallows it, and NO ROW is written at all); the other
eleven fields ride into `json.dumps` untouched. So the split in the Go
struct is Python's, not a convenience: `Goal`, `Step` and `Detail` are
`string`, `Project` and `LoopID` are `any`.

The price is that `any`'s zero is nil, which spells `null` where Python's
keyword default is `""`. There is no unset state to detect, so both
callers state what they mean — and that is also the only way a genuine
`None` reaches the row at all. `TestABareCallMatchesCPythonsKeywordDefaults`
pins both halves: the Go call that reproduces a bare `write_event(type)`,
and the fact that the zero value is not it.

The same reading fixed two more sites. `job_id[:8]` is a bare slice
inside the artifact block's own try/except, so a non-string `job_id`
writes NO artifact while everything after that block still runs — the
decision, the calibration row and the event all survive, and only the
operator's file is missing. And the calibration row's `job_id` is raw in
Python's dict literal too, which the same test case caught one ledger
later.

`depth` was left coerced through r11 and named as a residual on the
grounds that it "only diverges for a non-numeric depth, where Python's
`depth + 1` raises inside the spawn branch's own try." **That reasoning
was wrong and the review round after r11 found it.** A FLOAT depth raises
nothing: it stays a float through `depth + 1`, and it is spelled `2.0` in
the calibration row, the event detail, the operator artifact, the queue
row, the check-in payload, and the stop evidence written into both
`outcomes.jsonl` and `metadata.json`. Any foreign JSON writer — jq, any
JavaScript tool, anything that types every number as a double — produces
one. `depth` is raw now, `pyval.AddOne` does the arithmetic, and
`tasks.Options.ContinuationDepth` is `any` so the float survives the
queue.

The residual had it backwards in a way worth naming: it described the
LOUD failure and missed the silent one. A divergence that raises is a
divergence someone finds. A residual that reasons only about the raising
case has not looked at the other half.

### The r9 residual, closed

r9 named it: *"The escalation ledger sorts payload keys: the payload
arrives as a Go map, so Python's insertion order was gone before the
writer saw it ... the fix is the same ordered-payload API."* It stayed
open for two rounds because no payload's order was observable — every
existing caller's keys survived alphabetization unnoticed.

The recursion check-in is a fourteen-key dict literal, and it made the
divergence visible the moment the differential compared ledger rows.
`notify.EmitOrdered` takes a `pyval.Obj` and both positional surfaces —
`output/escalations.jsonl` and the hook's stdin — write the caller's
order. `Emit` stays for callers whose payload is genuinely a bag; it
sorts, and the sort is now an explicit, documented step rather than an
invisible property of the parameter type.

### The `ts` was local time

Both Python writers in `notify` stamp `datetime.now(timezone.utc)`. Both
Go writers stamped `time.Now()`. Same instant, different string, in a
feed whose readers sort and group on the string — and invisible in every
test, because a `ts` is the one field two processes cannot agree on and
every comparison in the suite masks it. Found only because a fixture's
raw output was printed next to CPython's on an unrelated failure.

The sibling sweep found the two other zone-sensitive sites in the tree
(`orch/pids.go`'s `%z` stamp, `record/dailylog.go`'s local date filename)
already deliberate and already documented as matching Python's LOCAL
behaviour. It is a one-site class, not a pattern.

### `int(str)` is not `float(str)`, and neither is `pyval.IntOf`

`confidence` arrives from a model, so it arrives as whatever the model
felt like, and Python's gate is `int(...)` inside a `try` with a caller
default of 5. That is three different things away from `pyval.IntOf`:

- `int("high")` RAISES, and the fallback is the CALLER's 5. `IntOf`
  answers 0, which then clamps to 1 — maximum uncertainty, reported from
  a reply that said nothing about confidence at all.
- `int("7.5")` and `int("7e2")` also raise, unlike `float()`. A decimal
  point is a ValueError to `int`.
- `int()` DOES accept surrounding whitespace, a leading sign, and PEP 515
  underscores between digits — `int("1_0")` is 10; `int("_7")`,
  `int("7_")` and `int("7__0")` are all ValueError.

`pyIntFromString` implements those rules. One named divergence: CPython's
`int()` accepts every Unicode decimal digit, so `int("٣")` is 3, and this
port refuses it and takes the default. The consequence is bounded to one
field, and widening it would mean carrying a Unicode decimal table for a
case no model has produced — so it is named with the cost stated, and
`TestTheFullWidthDigitDivergenceIsDeliberate` fails if it ever changes
silently.

### The order of two writes is a claim about a message

`continue` and `narrow` both enqueue a continuation and then fire a
non-blocking check-in. The check-in says "this goal is still running in
the background; reply to redirect or stop it". If the enqueue failed, the
chain is dead and that message is a lie with no way for the operator to
catch it.

So the cadence is split: `AdvanceOriginWithCheckin` advances the state on
the ancestry object and returns whether a check-in is due; the caller
fires it only after the enqueue is confirmed. And a failed enqueue does
not merely suppress the check-in — it flips the action to `surface`,
because a warning log is not an operator signal.

Two sharp edges under that:

- `random.randint(lo, hi)` is INCLUSIVE at both ends. `rand.Intn` is not.
  A port reaching for it directly gets a 4–7 cadence that is really 4–6,
  and nothing anywhere would report it.
- The origin must be COPIED, not aliased. Python builds a fresh `Origin`
  from the task's dict, so advancing the cadence must not touch the task
  the caller still holds — otherwise a failed enqueue leaves
  `next_checkin_depth` advanced on a task that was never sent.

And one bug the differential caught: `asOrigin` type-asserted only
`pyval.Obj`, so an origin arriving as a plain Go map was silently
DISCARDED and the continuation lost its whole ancestry. Every cadence
assertion still passed, because the cadence starts from defaults when the
ancestry is empty.

### Named residuals after r11

- `EventFields.Status` and `.Model` are typed `string` where Python
  leaves them untouched like `Project` and `LoopID`. No caller in this
  port has a non-string source for either; the day one does, they join
  the two that are `any`.
- A surface born from a FAILED LLM call writes nothing at all — no
  artifact, no calibration row, no event. Python returns before the whole
  recording block. That is a real gap on the Python side (the one surface
  an operator most needs to see is the one nobody records) and it is
  reproduced rather than fixed: a Go-only artifact would show up as a
  phantom file in every differential from here on. Owed upstream.
- `int()` refuses non-ASCII decimal digits (above).
- The escalation's `Config` is passed in where Python reads it from the
  config module — consistent with the rest of this port.
- `LowConfidenceNotifier` is one method of Python's `ConversationChannel`.
  The escalation lane files one advisory and does not converse; a fuller
  channel port will widen it.
- The stop-verdict rail's remaining Python callers are still unported:
  `agent_loop`'s fence stamp, `loop_finalize`'s ending stamp, `handle`'s
  demotion stamp. r11 closed the director-close one.

## r12 — the dispatch envelope, and the three names in one loop

`dispatch_envelope.py` is the box's intake for machine-dispatched work:
one JSON payload declaring `maro-dispatch/v1`, carrying the user's
verbatim ask, advisory operator fields, and reference artifacts that get
written to disk with provenance sidecars before the run starts. It is
415 lines and it is the densest file this port has met for
divergence-per-line, because almost every line either names a path,
spells a string a caller will read, or decides which exception class a
CLI sees.

The whole module is ported: `parse_dispatch_payload`, `_safe_name`,
`_safe_key`, `store_attachments`, `store_operator_attachments`, `_land`,
`land_in_run_dir`, `land_operator_attachments`, `operator_block`,
`operator_attachment_block`.

### `pathlib` is not `path/filepath`, and the differences are load-bearing

Go's `path/filepath` is close enough to `pathlib.PurePosixPath` to look
interchangeable and is not. `internal/dispatch/pypath.go` writes out the
four rules this module leans on, because each one decides a filename
that ends up on disk:

|              | pathlib          | filepath      |
|--------------|------------------|---------------|
| `Path("")`   | `.`, name `""`   | `Base("") == "."` |
| `Path("/")`  | `/`, name `""`   | `Base("/") == "/"` |
| `Path("a/.")`| `a`, name `"a"`  | `Base("a/.") == "."` |
| `Path("a/..")`| `a/..`, name `".."` | `Base("a/..") == ".."` |

The `a/.` row is the one that bites: an attachment named `a/.` is stored
as `a` by Python and would be stored as `artifact` by a port that reached
for `filepath.Base`.

`.suffix` is worse, because it is **interpreter-version-dependent**.
CPython 3.14 — what runs on this box — is `name.lstrip('.')` then
`rfind('.')`. CPython ≤3.13 was `if 0 < i < len(name) - 1`. The two
disagree for `f.` (suffix `"."` here, `""` there) and `..f` (`""` here,
`.f` there), and `.suffix` is what builds the disambiguated name when two
attachments collide. A port matching the older rule stores the second
file under a different name than the Python beside it — silently, on a
filename nobody looks at until they need it.

`expanduser` is the fourth. `Path("~nosuchuser/x").expanduser()` does not
pass through; it raises **RuntimeError**, and so does `Path("~~")`. That
is deliberately NOT an `*Error` on this side: `EnvelopeError` is what the
CLI catches to print one actionable line, so widening the lane's refusal
vocabulary to cover a `~` typo would turn a traceback into a shrug.

### One Python loop, three different spellings of "the name"

`store_attachments`'s body is five lines and spells the artifact's name
three ways:

```python
name = _safe_name(str(art.get("name", "")) or f"artifact-{i}")   # the FILENAME
...
"name": str(art.get("name")),                                     # the ROW
json.dumps({**rec, "lane": "dispatch"}, indent=1)                 # the SIDECAR, raw
```

The filename gets a `""` default and a positional fallback. The returned
row gets **no default**, so a missing name is the string `"None"`. The
sidecar takes whatever `rec` holds. Three spellings, one loop, and the
first version of this port used `pyval.Str` for all three — which
answers `"None"` where Python answers `""`, i.e. it got the filename
wrong in the one case an artifact arrives unnamed.

`store_operator_attachments` has the same shape with a different answer:
its row's `name` is the **sanitised source name**, and it stays that
whatever the file ends up being called. Two rows in one call can share a
`name` and differ only in `path`. That one bit me in the test rather than
the code: the fixture guard counted disambiguations off `name` and
reported zero on a table that disambiguates three times.

### Two landing functions that look like one function with a parameter

`land_in_run_dir` and `land_operator_attachments` both copy a stored
directory into `<run_dir>/fetch-raw/<sub>/`. They are not the same
function:

- `land_in_run_dir` leaves an existing target alone **whatever its
  bytes**.
- `_land` (the operator lane) treats identical bytes as a no-op and
  **disambiguates differing bytes**, keeping both.

The asymmetry is deliberate and recorded in the Python: dropping one of
two distinct operator files is worse than a run tree holding an
oddly-named pair, while the dispatch lane's copy is a convenience over a
record that already exists elsewhere. A port that shared one helper
between them passes any test that lands once. It takes landing, then
perturbing, then landing again to see it — which is what
`TestTheLandingFunctionsMatchCPython` does, with an assertion that the
two re-land counts actually DIFFER, because a test whose two lanes agree
is testing one lane twice.

Both are fail-soft in the blunt Python sense: a blanket `except` around
the whole loop returns **0**, even when earlier files did land. The count
is what the caller logs, so under-reporting is the honest half of a
partial copy — reproduced, not fixed.

### The sidecars disagree about indentation on purpose

`store_attachments` writes `json.dumps(..., indent=1)`. Its operator-lane
sibling writes `indent=2`. Nothing depends on it and nothing explains it;
it is almost certainly an accident that has been on disk long enough to
be a fact. `pyval.DumpsIndentN` exists because of it — `DumpsIndent2`
now delegates.

### A guard on the resolved path, in one place

Eight packages had grown their own CPython probe harness. They had
diverged on the two things that decide whether a differential is worth
anything: whether a failing probe is a **skip** (six said yes, which
turned ten of twelve playbook differentials green while running nothing),
and whether a writing probe **checks where it is about to write** (four
carried a live-workspace refusal in three spellings; two carried none at
all while setting `MARO_WORKSPACE` and writing a tree).

`internal/pyprobe` is one harness with one answer to each. A missing
interpreter skips; a probe that RAN and failed is fatal, with its stderr.
A writing probe asserts its resolved path on both sides — realpath'd,
because a symlinked temp dir pointing at `~/.maro` passes a string
comparison and is exactly the shape that makes that accident survive
review. Callers whose Python resolves the path through a specific
function (`config.output_dir`) pass a `Guard` so the **resolver's own
answer** is asserted rather than the test's intention.

It earned its keep the same afternoon: a fixture handed the dispatch
probe `null` for an empty artifact list and the probe died on
`TypeError: 'NoneType' object is not iterable`. Under the old
majority behaviour that would have been a green skip.

The remaining eight harnesses are not yet migrated — the packages they
live in are mid-review, and rewriting a harness under a running reviewer
is how you get a round that reviews neither version.

### Named residuals after r12

- `oserr` renders a Go file error as `open path: text` where
  `str(OSError)` is `[Errno N] text: 'path'`. The two differ and this
  port does not pretend otherwise. Confined to two operator-facing
  strings that no store ever holds.
- `EnvelopeError` subclasses `ValueError`, so a Python caller catching
  `ValueError` also catches it. `*Error` has no such relationship. No
  caller in this port does that yet; the day one does, it needs saying.
- Nothing in the Go tree CALLS the dispatch package yet — `handle.py`'s
  intake is the next tranche. The lane is ported and pinned ahead of its
  caller, which is the same order the director tranche used.
- `sortedFileNames` sorts by UTF-8 byte where Python sorts Path objects
  by code point. Those orders agree (UTF-8 preserves code-point order)
  and the agreement is the reason it is safe, not an accident.

## `config.Get[any]` cannot ask "is the key present?"

`config.get(path, default)` in Python returns the STORED value whenever
the key exists — a YAML `null` included — and the default only when it is
missing. Three callers in this port depend on that distinction, because
Python then applies its own `int()`/`float()` to the result and the raise
is the behaviour:

```python
timeout = float(_config_get("notify.timeout_seconds", 30))   # no try
```

The obvious Go spelling for "get with no type filter" is
`Get[any](cfg, path, def)`. It is wrong, and wrong in the one case that
matters:

```go
cur, ok := cur.(T)   // T = any, cur = nil interface  ->  ok is FALSE
```

A type assertion to `any` **fails for a nil interface**. So a config that
says `notify:\n  timeout_seconds: ~` reads as *absent*, `Get[any]`
returns `30`, and the hook runs where CPython refuses to run it. The
generic did not remove a constraint; it swapped one constraint for
another, and the two differ on exactly the empty value.

`GetRaw(cfg, path, def any) any` is implemented over `Lookup`, which
reports presence separately, and it is what the Python-semantics callers
use:

| site | Python then does |
|---|---|
| `notify.timeout_seconds` | `float(...)` with no try — a bad value stops the hook |
| `recursion.checkin_first_depth` | `int(...)` inside a try — falls back to `2` |
| `recursion.checkin_jitter_min` / `_max` | `int(...)` inside ONE shared try — a bad max resets the min |
| `knowledge.provenance_gate_enabled` | truthiness, where `None` is falsy and the default is `True` |

`Get[T]` keeps its meaning for every other caller: *give me a T here, or
the default*. It is only the raw-value readers that were asking a
different question.

## The test binary reads the operator's real machine

Everything in `internal/testenv` exists because `go test ./...` on this
box was sending Telegram messages. `notify.Emit` with no config falls
back to `config.Load()`, `config.Load()` reads `~/.maro/config.yml`, and
that file registers a real `notify.command`. Five test packages can reach
it.

`MARO_USER_DIR` is the override — the same one `src/config.py` documents
as existing "so the box's real config doesn't leak in", which pytest
applies from a conftest fixture. Go's equivalent is a `TestMain` per
package:

```go
func TestMain(m *testing.M) { os.Exit(testenv.Isolate(m)) }
```

The list of packages that need one is not maintained by hand.
`testenv/tripwire_test.go` asks `go list -json ./...` which test packages
transitively import `notify` and fails on any that lacks the call, with
its own floor (`checked < 2` is fatal) so a criterion that stops matching
cannot report success forever. `MARO_WORKSPACE` is deliberately NOT set
here: tests pass their own workspace explicitly and `pyprobe`'s
live-workspace refusal guards the probes, so overriding it centrally
would paper over a caller that forgot to.

## An exception's CLASS is part of its behaviour

`handle_escalation` reads the model's confidence like this:

```python
try:
    confidence = int(data.get("confidence", 5))
except (TypeError, ValueError):
    confidence = 5
```

The tuple is NARROW, and two values one model reply can produce fall on
opposite sides of it:

| value | CPython | what happens |
|---|---|---|
| `NaN` | `ValueError: cannot convert float NaN to integer` | caught — confidence is 5, the escalation completes |
| `Infinity` | **`OverflowError`**`: cannot convert float infinity to integer` | NOT caught — leaves `handle_escalation` entirely |

Both are one reply away: `json.loads` admits the bare tokens `NaN` and
`Infinity`, and any float literal past 1e308 decodes to `inf` as well. On
the `Infinity` path CPython writes no calibration row, no artifact, no
stop-verdict stamp and takes no branch — and `drain_task_store` then
FAILS the task instead of completing it. A helper that answered only
"did the conversion work" gets the first case right by accident and the
second one catastrophically wrong: the port wrote a full surface where
CPython wrote nothing.

So `pyval` carries the class:

```go
type PyErr struct {
	Class string   // "TypeError" | "ValueError" | "OverflowError" | "KeyError" | …
	Msg   string
}
func (e *PyErr) Error() string { return e.Msg }
```

`Error()` is Python's `str(e)` — the **message alone**. That is not a
stylistic call: every `except Exception as exc: log(..., exc)` in the
ported source renders `%s` of the exception, so an `Error()` that
prepended the class would write log lines CPython never writes. `repr(e)`
is the spelling that carries the class, and nothing needs it yet.

Three helpers sit on top, and the reason there are three is that the
CALLERS differ:

- **`Int(v)`** — the bare `int(v)`, class and all.
- **`IntCaught(v, def)`** — Python's `except (TypeError, ValueError)`
  exactly: it defaults for those two and returns everything else as
  `raised`, for the caller to propagate.
- **`CaughtBy(err, classes...)`** — the `except` tuple written out. It
  exists because `_checkin_jitter` has ONE shared `try` across TWO reads,
  so a bad `checkin_jitter_max` resets the MINIMUM too. `IntCaught`
  cannot express that — it answers each read on its own — so the jitter
  classifies both and makes the reset one decision over both.

A fourth, `ErrIntTooLarge`, is **not** a Python exception. CPython's `int`
is arbitrary precision, so `int(1e19)` is `10000000000000000000` and
nothing raises; Go's `int` cannot hold it, and a bare conversion yields
`MinInt64` — which is how a magnitude of 1e19 became a durable
`"depth": -9223372036854775808`. The two callers decide differently, on
purpose:

- the confidence read may **saturate**, because its very next operation
  is `max(1, min(10, confidence))` and the clamp erases the difference
  provably (`IntClamped` does this, recovering the sign from the value
  because `Int` discarded it);
- the reopen payload's depth may **not**. It refuses and skips the write
  rather than emitting a number CPython would never produce.

## A slice is not a string operation

`write_event(goal=task.get("reason", "")[:80])`. The port had a string
helper behind that, so a queue row whose `reason` is a list dropped the
whole event row — where CPython writes a JSON array. Python does not care
what it is slicing; it cares whether the object supports it, and the
shapes that do not are not interchangeable either:

| `v[:80]` where v is | CPython |
|---|---|
| `str`, `list` | the head, same type |
| `dict` | **`KeyError: slice(None, 80, None)`** — a lookup with a slice for a key |
| `int`, `float`, `bool`, `None` | `TypeError: '<t>' object is not subscriptable` |

`pyval.SliceHead(v, n) (any, bool)` answers the head and whether the
slice would have raised. Every call site swallows the raise, so only the
FACT of it is reproduced — but the fact decides whether a row exists.

## A subscript is not a `.get`

A task file is not a shape this package controls: it arrives from another
box, from an older schema, from a hand-edit during an incident. Python
reads it with subscripts —

```python
task["status"]; task["attempt"] += 1; task["timestamps"][key] = value
```

— and every one of those raises for a row that lacks the key. The raise
is the contract: the verb fails, the file is left byte-identical, and the
caller sees the task still queued. The port had `.get`-shaped reads with
Go zero values behind them, which turns a malformed row into a silently
DIFFERENT one — a missing status read as `""`, a missing attempt
restarted at 1, a missing `timestamps` object synthesised **and written**.
The last is the worst: it writes a task CPython leaves untouched, so the
two runtimes disagree about whether the row is still claimable.

`index`/`indexStr`/`setTimestamp` raise instead, with CPython's own
messages — `KeyError`'s `str()` is the **repr** of the key, not a
sentence — and three details had to be measured rather than guessed:

- a `list` under `timestamps` says `list indices must be integers or
  slices, not str`, because a list *does* support item assignment and
  fails on the index; every other shape says `'<t>' object does not
  support item assignment`;
- `task["attempt"] += 1` is an AUGMENTED assignment, and its unsupported-
  operand message names `+=` where `depth + 1` names `+` (hence
  `pyval.IAddOne` alongside `AddOne`);
- `+=` on a string CONCATENATES-or-raises rather than coercing, and on a
  float stays a float, so `2.0` becomes `3.0` in the file, not `3`.

## `in` is not "is it in the list"

`notify.events` gates whether an operator hears anything, and Python's
`event_type in (config_value or DEFAULT_EVENTS)` does five different
things depending on what the YAML parsed to:

| `notify.events` | `in` does |
|---|---|
| a list | `==` against each element (a non-string member never matches, never raises) |
| an EMPTY list, `null`, `0`, `false` | falsy → the DEFAULTS, not silence |
| a bare string | a **substring** test: `"escalation" in "escalation_only"` is True |
| a mapping | tests its KEYS |
| an int, float or truthy bool | **`TypeError`** — swallowed by `emit`'s outer handler, so no hook and `emit` returns False |

The port had named the bare-string case as a deliberate divergence and
implemented everything else as "fall back to the defaults" — which runs a
command CPython declines to run. The divergence that mattered was in the
remainder arm nobody had named.

### Round 11 round 4 — the reads

`%d` is not `str()`, and `logging.Handler.emit` is defined to swallow a
`%d` that raises — so three log sites in the escalation lane now go
through `pyval.PercentD`, which answers both the rendered text and
whether the record survives at all. A pid is read RAW and short-circuited
the way Python's `and` is, because `""`, `[]` and `{}` are falsy AND
raise inside `_pid_alive`. A task file holding the literal `null` is a
MISSING task, not a broken one. A status key is whatever the row holds.
`str.strip()` strips four code points Go's `TrimSpace` does not. And
CPython's 4300-digit `int(str)` limit is a ValueError, not this port's
`ErrIntTooLarge`.

`pyval` grew three pieces for it: `PercentD` (with its own 26-row
differential), `IntLiteral` (exported — "is this wide number an int or an
overflowed float" decides the exception class), and `ClassOf`/`PyClasser`,
so a Go-typed error like `tasks.ConflictError` can name the CPython class
it stands for without becoming a string-classed struct.

### Round 11 round 5 — Python's operators

`blocked_by` is ITERATED, not type-asserted: a str goes by character, a
dict by key, and a scalar is a TypeError — so `pyIter` and `pyContains`
now carry Python's `for x in v` and `x in v`, which are not the same
operation and do not raise the same message. A dict KEY is an identity,
not a spelling, so `hashKey` folds `True`/`1`/`1.0` onto one bucket the
way Python's dict does. `_read_task` hands back whatever it decoded and
the SHAPE check moved to the callers, because Python's is at the callers:
`.get` is an AttributeError, `task[...]` is a TypeError with three
different sentences by type, and an unfiltered `list_tasks` never reaches
either — which is why `List` returns `[]any`.

Also: one `try` over two reads means the second read does not happen at
all when the first raised; `int(str)`'s int64 bound is exact and
asymmetric (the negative side reaches one further); and an overflowing
`notify.timeout_seconds` is refused before `time.Duration` can wrap it
into a timeout that never happened.

### Round 11 round 6 — the sites no fixture ever named

`pathlib`'s `/` lets an ABSOLUTE right-hand side replace the left one, so
`task_path("/etc/passwd")` is `/etc/passwd.json` — outside the workspace
entirely — where `filepath.Join` answers a path inside it. `blocked_by`
is foreign-writable and `claim` feeds every entry to `task_path`, so a
row naming an absolute dependency is claimable under CPython and
permanently blocked here: the two runtimes disagreeing about what the
queue may RUN. `pypath` is now its own package (it had acquired a second
caller) and carries `Join` with both of pathlib's rules — an absolute rhs
wins, and the result is PARSED, so a "." component disappears while ".."
survives.

The fix was pinned at the HELPER and nowhere else until the battery said
so: reverting `task_path` itself to `filepath.Join` failed nothing. What
closes it is a fixture that can name an absolute path, which no constant
can do when the two runtimes get two different temp roots — hence a
`{{WS}}` substitution in the sweep differential, each side's own root
folded to one token before comparison. It also needed the archive
directory to be READ, which nothing did, and the file comparison to run
in BOTH directions: iterating only CPython's filenames made a row the
port wrote somewhere CPython did not completely invisible.

`notify.command`'s timeout is bounded by `poll()`, not by Go: CPython
accepts 2147483.647 seconds (INT_MAX milliseconds) and raises
`OverflowError` one millisecond past it — which means the never-time-out
idiom `3e6` is REFUSED there and ran here. The other end is sharper still:
every negative value, and `0.0`, is an immediate `TimeoutExpired`, not a
refusal, so round 5's `math.Abs` guard refused values CPython accepts.
The log line spells its seconds with `%.0f`, and Python's non-finites are
`-inf`/`inf`/`nan` where Go's are `-Inf`/`+Inf`/`NaN` — hence
`pyval.PercentF`.

`status_summary` returns an ORDERED result, because CPython's does and
because two of its keys can spell alike: `5` and `'5'` are two dict
buckets and one JSON key, so `json.dumps` emits a duplicate. A Go map
alphabetized and silently merged them. Hashing is by value and spelling
is by `str`, which is why `pyval.HashKey` and `statusKey` are two
functions — and `pyval.HashKey` is now the only copy of the first,
shared by the status summary, `_check_cycle`'s visited set, and the
drain's job-id filter.

`archive` SUBSCRIPTS `task["status"]`, so a row without the key is a
`KeyError` and not a refusal. `ErrNotFound` is a typed sentinel that can
answer `PyClass()` — it was the third member of that family and the only
one that could not. And the recursion check-in's debug log takes NO
format argument: Python's exception rides `exc_info` and never reaches
the message.

### r13 — `handle_queue.py`, and a differential that was testing nothing

`internal/handlequeue` ports the drain: `drain_task_store`, `handle_task`,
`enqueue_goal(s)`, and the surface emitted when a task terminates. Three
things in it are not what a straight reading suggests.

**`list_tasks` is called OUTSIDE the try.** Python's `try` wraps only the
import, so a queue directory the sweep cannot read propagates rather than
becoming a silently idle drain. `DrainTaskStore` returns the error.

**The two membership tests are not the same operation, and their ORDER is
load-bearing.** The comprehension is
`[t for t in all if t.get("source") in sources and t.get("job_id") in job_ids]`.
`and` short-circuits, so a hostile (unhashable) `job_id` on a row whose
source did NOT match is never hashed — it is skipped, not raised. Hoist
the hash above the source test and the same queue takes the drain down
with a `TypeError`. The port carries the order and pins it.

The set membership itself hashes by value: `True` matches `1`, `1.0`
matches `1`, and `4242` does not match `"4242"`. An unhashable id raises
`TypeError: cannot use 'list' as a set element (unhashable type: 'list')`
— CPython's set wording, which is not the dict-subscript wording.
`set()` is the exception: CPython retries it as a frozenset and answers
`False`.

**`max_tasks` is a SLICE bound.** `0` means zero tasks, not "unset", and a
negative bound drops from the tail (`[1,2,3][:-1]` is `[1,2]`) rather
than clamping to empty. Both pinned.

The differential for all of this **went green on its first run while
testing nothing.** A direct CPython probe showed every fixture failing at
`drain_task_store: failed to claim j1: 'timestamps'` — `claim` subscripts
`task["timestamps"]`, the seeded rows omitted it, so both sides failed the
claim identically and the entire downstream (routing, terminal status,
the emitted event) was never reached. The mutation battery found it
(7 of 22 missed); the green run did not. Lens 1 says a test that reports
AGREEMENT may be testing nothing, and this is the sharpest instance yet:
**a differential that goes green on its first run is that shape.**

Three mutations are labelled unobservable in the source rather than
pinned: the `Eq`-vs-`Str` source comparison (no reachable value spells
differently), the second slice guard (redundant with the first, the same
shape as the escalation lane's), and `processed++` after a `complete()`
that no fixture can make fail.

### Round 11 round 7 — a zero value that must mean two things

`.get(k, default)` on a key that is PRESENT and null returns None, not the
default, so `make_task` receives a genuine None and writes `null`. The
port read a nil field as "argument omitted" and substituted `""` — which
made a `loop_escalation` row carrying `"reason": null` enqueue a
continuation whose GOAL was `""` instead of `null`. `HasReason` and
`HasParentJobID` carry the distinction the way `DrainOptions.HasMaxTasks`
does; the same wall was hit by `notify`'s "state every raw field at every
call site" and by round 6's `Model: ""`, and the rule that comes out of
all three is that a zero value which must mean two things means neither.

A float used as a dict KEY is not spelled by `str()`. `json.dumps` runs it
through json's own float writer, so the three non-finites are `"NaN"`,
`"Infinity"` and `"-Infinity"` where `str()` gives `nan`, `inf`, `-inf` —
and both decoders accept the bare tokens, so a task file reaches the
summary an operator reads.

The numeric fold has no bound any more. It folded within ±9.2e18 and split
`1e19` from `10**19`, which CPython counts as one key; `'f'` with
precision −1 spells an integral float as its exact integer digits at any
magnitude. `-0.0` is folded onto zero explicitly, because `'f'` writes it
`"-0"` and `-0.0 == 0` is True.

Every decoded NaN is ONE key, and the reason is identity rather than
equality: CPython's JSON scanner returns the same cached object for every
bare `NaN` token, and dict lookup short-circuits on `x is y` before it
compares. Two `float("nan")` calls WOULD be two keys; nothing in either
runtime constructs one here. The port's per-call pointer key was written
from `nan != nan` and was wrong for the only reachable path — found
because a fixture asserted the old behaviour and the interpreter refused
it.

`notify.runeHead` is gone: it was the third implementation of `s[:n]` and
carried the negative-`n` bug `pyval.Clip`'s own comment records fixing.

### r13.1 — the residual that gets a pin instead of a fix

`pyval.ErrIntTooLarge` is the port refusing a value CPython computes, and
three more sites let that refusal become control flow — `HandleTask`'s
depth read, `CheckinFirstDepth`, `CheckinJitter`. A `continuation_depth`
of `10**19` completes under CPython and FAILS the task here, and at all
three the value is only logged or compared, so the abort buys nothing.
Closing it needs a big-int carrier through `pyval` that nothing else wants
yet, so it stays named — with a test that asserts the DIVERGENT behaviour
and fails the day the gap closes, which is the standing convention for an
accept-not-close residual.

### Named residuals after r11 round 3

- **Arbitrary precision.** `ErrIntTooLarge` is the port refusing a value
  CPython computes. Two call sites decide it differently (saturate where a
  clamp erases the difference; refuse where the value is written), and the
  reopen payload's `depth` is the visible site: CPython writes
  `"depth": 10000000000000000000` and this port writes no metadata half at
  all. Closing it means a big-int type through `pyval`, which nothing else
  needs yet.
- **`PyErr` carries a class NAME, not a hierarchy.** Python's
  `except ValueError` also catches `EnvelopeError`, which subclasses it;
  `CaughtBy(err, "ValueError")` does not. No ported call site mixes the
  two today, and the honest fix is a parent-class table rather than a
  guess at each site.
- **`int()` refuses non-ASCII decimal digits.** `int("７")` is 7 in
  CPython and a ValueError here.
- **`hashKey` folds by numeric value, not by Python's `hash`.** The two
  agree on every JSON-decodable scalar, which is all a task file can
  hold; a Decimal or a Fraction would need the real rule.
- **`Float`'s `json.Number` overflow arm has no live caller.** It is
  pinned at the helper and labelled as such in the test: both callers hand
  it either a YAML-parsed value or a confidence `Int` has already refused
  with an OverflowError before the sign recovery runs.
- **Two guards the battery scores as misses, on purpose.** The escalation
  lane's slice check is redundant with `WriteEvent`'s own, and
  `recursion_checkin`'s two `%d` arguments are `new_depth` and
  `new_depth + 1`, so `&&` and `||` between them cannot differ for any
  value that reaches the line. Both say so in the source; neither was
  removed. (Round 3's third — `_fire_checkin`'s `int(checkins_sent)` — is
  no longer a miss: `PercentD`'s own differential reaches it.)
- **`PercentD` is `%d` as a LOGGING argument, not as an expression.** A
  `%d` that raises deletes the record; a `%d` in a plain `"%d" % v` does
  not. Every ported call site is the first kind. A site of the second kind
  would need a different helper.
- **CPython's hook-timeout ceiling is not a constant.** `poll()` refuses
  anything past INT_MAX milliseconds, but `subprocess.run` hands it the
  time REMAINING after the fork, so the observed boundary was
  2147483.6470185518 — the documented bound plus however long the machine
  took to spawn the child. The port pins 2147483.647 and the ~18ms is
  unpinnable by construction: a fixture asserting on it would be
  asserting on this box's process-creation latency.
- **About twenty other `%.Nf` sites are not yet routed through
  `pyval.PercentF`.** Deliberately deferred rather than swept: each needs
  a per-site check of whether a non-finite can actually reach it, and a
  blanket rewrite would be the kind of change that looks like a fix and
  proves nothing. The timeout site is the one where a non-finite is
  reachable from a config file.
- **Three `handlequeue` mutations are labelled unobservable.** The
  source comparison (`pyval.Eq` vs `pyval.Str`) has no reachable value
  that spells differently; the second slice guard is redundant with the
  first, which is the same shape the escalation lane already carries; and
  `processed++` sits after a `complete()` that nothing in the harness can
  make fail. All three say so in the source.
- **The `pathlib`-join class is not fully swept.** `task_path` and
  archive's destination are fixed and pinned; they are two of the sites,
  not the class. The rule is: a `filepath.Join` whose right-hand side is
  NOT a string literal, standing where Python has `pathlib`'s `/`, answers
  differently for an absolute right-hand side and for a "." component.
  The port has 177 `filepath.Join` calls; the great majority take literal
  components and cannot diverge. The reachable siblings found so far, all
  in `orch_items.py`:
  `project_dir(slug)` (`projects_root() / slug` — the project name comes
  from the CLI and from stored records), `_run_record_path(run_id)`
  (`runs_root() / f"{run_id}.json"` — the same shape as `task_path`
  exactly), `_run_artifact_root` (`runs_root() / run.run_id`), and
  `resolve_artifact_path`'s `workspace_root() / s[len("~workspace/"):]`,
  where a stored `~workspace//etc/passwd` strips to an absolute remainder.
  `orch_items.py` is not a reviewed tranche yet and `internal/orch` has no
  CPython differential harness at all, so a fix there today would be
  pinned at the helper and nowhere else — which is the exact trap round 6
  named. Carried as a named residual for that tranche rather than
  half-closed here.
- **`enqueue_goal` / `enqueue_goals` have a probe but no differential.**
  `pyDrainSrc` carries both verbs and `maskMinted` is written; no Go test
  drives them yet.
- **`ErrIntTooLarge` is control flow at three more sites.** `HandleTask`'s
  depth read, `CheckinFirstDepth` and `CheckinJitter` all let the port's
  own refusal abort a branch CPython completes — a `continuation_depth` of
  `10**19` fails the task here and completes there. Pinned as a known gap
  by `TestAHugeDepthIsRefusedWhereCPythonComputesIt`, which fails the day
  someone closes it. Closing needs a big-int carrier through `pyval`.
- **A NaN constructed in Go, rather than decoded, would be its own dict
  key in CPython and is not here.** `HashKey` folds every NaN to one key
  because CPython's JSON scanner returns one cached object for the bare
  token; two `float("nan")` calls would be two keys. No path constructs
  one, and representing the difference needs object identity, which a
  value-keyed fold does not have.
- **`notify.Options.RunDir` is a seam Python does not have.** Two call
  sites pass it through where Python hard-codes `run_dir=None`. No
  non-test caller assigns it, so nothing diverges today.
- **The goal's 200-rune clip is unobservable.** `observe.write_event`
  clips the same string again at 80, so the inner bound never reaches
  disk. Measured, labelled at the site, and scored as a justified miss.

### The probe harnesses — a failing probe is fatal, at 15 more sites

`internal/pyprobe` was built to end two divergences among the CPython
differential harnesses. Building it did not end them: it ended them for
the callers that moved. Seventeen sites still took a skip straight off the
probe's own error, so a CPython run that FAILED — an assert firing, a
renamed helper, a changed signature — reported the differential green.

Fifteen migrated (`pytext` ×9, `record` ×6, `skills` ×3, minus overlap);
the two in `internal/tasks` go with the round that is reading that package.
Measured before and after on the worst of them: with the probe replaced by
`sys.exit(1)`, `record`'s digit differential reported `--- SKIP` and `ok`
before, and `--- FAIL: the CPython probe FAILED` after.

`Probe` gained `Stdlib bool`. `pytext` asks the interpreter about the
LANGUAGE — `casefold`, `repr`, `float()`, `re.compile(r'\w')` — and
imports nothing from the repo, so it had no honest `Marker` and the only
way to satisfy the required field was to borrow an unrelated module's
name. A marker is a claim about what would make a skip honest; a borrowed
one makes the probe skip for a reason that has nothing to do with it.
`Run` is now fatal when a probe declares both or neither.

Three probes changed from stdin to `sys.argv[1]` in the process
(`pyDailyProducer`, `pySkillMDProducer`, `_slugify`), which is what
`pyprobe.Arg` exists for — a fixture written as a Go value rather than as
hand-escaped JSON inside a string literal.

### Named residual — probes that read the operator's real config

The other half of `pyprobe`'s promise is `MARO_USER_DIR` isolation. Without
it a probe inherits this process's environment and `config.get` reads the
real `~/.maro/config.yml`, which on this box registers a `notify.command`
that shells out to Telegram and ssh's to another host (adversarial r11
round 2, HIGH). The key is still there — verified 2026-08-24.

Twelve probe files import repo modules without the isolation:

    internal/director/escalation_diff_test.go
    internal/jsonx/carve_diff_test.go
    internal/jsonx/fence_diff_test.go
    internal/notify/diff_test.go
    internal/orch/mission_chain_diff_test.go
    internal/orch/mission_dag_diff_test.go
    internal/orch/mission_plan_diff_test.go
    internal/orch/mission_validate_diff_test.go
    internal/playbook/playbook_diff_test.go
    internal/record/stopstamp_test.go
    internal/runs/index_diff_test.go
    internal/stopverdicts/diff_test.go

None is known to emit an event that reaches the hook. That is the reason
it is written down rather than closed: "not known to" is the state the
r11 round 2 finding was in before it fired.

## Round 8 — the fixes that changed behaviour

Documented in full in REVIEW.md; this is the part a reader of the CODE
needs.

**`tasks.Complete` takes `any`, not `string`.** The job id it is given
reaches two consumers under different rules: a path is f-stringed from
it, while `blocked_by` membership and `list.remove` compare it by VALUE.
`4242` and `"4242"` therefore unblock DIFFERENT dependents, and a caller
that spells the id before passing it inverts the second consumer while
satisfying the first. `handlequeue` passes the raw value to `Complete`
and the spelling to `claim`/`fail`, and the split is commented at the
call site because it looks like an inconsistency until you know why.

**`pyContains` is Python's `in`, not a list scan.** A `str` container is
a substring test that raises `TypeError` for a non-str needle; a dict is
a KEY test that hashes the needle, so an unhashable job id raises rather
than answering false. Its Go-native container arms are GONE: the one
caller reads `blocked_by` off a pyval-decoded row, so `[]any`,
`[]string` and `map[string]any` were unreachable. They now return an
explicit port-bug error instead of a `TypeError` CPython would never
raise for a list.

**`budget` has two clip regexes, not one.** Python's `$` without
`re.MULTILINE` matches at end of string OR before a single trailing
newline; Go's `$` is `\z`. `Clip`'s idempotence check uses `\n?\z` and
`StripMarker` uses `\z`, because the two call sites genuinely want
different anchors. Two trailing newlines match neither — that bound has
a fixture.

**`pypath.Realpath` no longer has a hop limit.** `os.path.realpath` is
pure Python walking lstat/readlink, so the kernel's ELOOP does not apply:
there is no depth limit, and a cycle is detected with a seen dict whose
`strict=False` answer is the path at which the repeat occurred. The old
40-hop counter refused a 60-link chain AND every cycle, and callers read
a refusal as "no such path". Replaced with the seen set.

**`Clip` with a non-positive cap is a deliberate divergence.** CPython
cuts to a bare marker at cap 0 and slices from the END at a negative cap.
This port returns the text: a cap is a circuit breaker, not a truncator
(GOAL_BRAIN decree 2026-07-29, strengthened 2026-08-21), and zero is Go's
zero value. Pinned as a known gap that measures CPython, so the decision
stays visible.

### The `Stdlib` migration was half-done and the reviewer found the rest

Round 7 added `pyprobe.Probe{Stdlib: true}` for probes that ask about the
LANGUAGE and import nothing from the repo, precisely so no probe would
borrow an unrelated module's filename as a marker. Fifteen sites were
migrated. Six were not — four in `internal/pyval/pyint_diff_test.go`, two
in `internal/pypath/pypath_diff_test.go` — and round 8 found them
independently. Nothing distinguished the migrated from the unmigrated but
which files happened to be open. **A helper that fixes a class fixes the
callers that reach it, not the class.** If another such flag is added,
the sweep for its callers is part of the change, not a follow-up.

### Test-side changes worth knowing about

- `internal/tasks/sweep_diff_test.go` masks only FRESH timestamps (within
  two minutes of now) and asserts both sides carry the same number of
  them. A seeded `2026-01-01` stamp stays literal and still has to match,
  so a port that overwrote `queued_at` is still caught. Masking every
  stamp would have made that a pass.
- `internal/notify`'s `maskTS`/`maskEventTS` take `*testing.T` and are
  FATAL when their anchor is missing. Returning the line unchanged let
  two wall clocks decide the comparison.
- `TestListIsSortedAndFilterable` is now two tests. The order CPython
  gives is `sorted(glob("*.json"))` — the FILENAME's, not the job id's —
  and the old fixture's one-character ids could not tell the two apart.
  The ids are `a1`/`a9`/`a10` now, which can.

## Round 9 — the byte the port rewrote, and two number-keys

### Reading a file is not converting bytes to a string

`internal/tasks` read task files with `pyval.LoadsOrdered(string(raw))`.
Python's `_read_task` is `json.loads(path.read_text(encoding="utf-8"))`,
and `read_text` REFUSES a file carrying an undecodable byte. Go's
decoder does not: it substitutes U+FFFD, the document parses, and the
next `write_task` writes those replacement characters back. The Python
runtime can then read the task again and sees content nobody wrote.

`pyval.DecodeUTF8Strict` is the reader's rule, spelled once. It raises
CPython's `UnicodeDecodeError` — class AND sentence — because the class
is what an `except UnicodeDecodeError` catches. Four rules, all
measured:

- a byte that cannot begin a sequence (0x80–0xC1, 0xF5–0xFF) is
  "invalid start byte", width one. The overlong lead `C0` is reported
  there, not as a bad continuation.
- a sequence running off the END is "unexpected end of data" — so the
  same truncated `C3` reports differently at EOF than one byte earlier.
- anything else bad mid-sequence is "invalid continuation byte",
  spanning the lead plus the continuations already accepted. Four leads
  (E0, ED, F0, F4) carry a NARROWER first-continuation range, which is
  what rejects overlong forms, surrogates and code points past U+10FFFF.
- width decides the sentence: one byte names the byte, more than one
  names a position range.

Six other sites already applied the lenient-read rule with a plain Go
error (`record`, `orch`, `knowledge`, `pack`, `pyjson`). They refuse and
do not rewrite, so they stay as they are; `tasks` was the one that could
launder AND persist. **If a new reader is added, this is the decision it
inherits — refuse, and refuse the way the other runtime does.**

### A float key is a value, not a spelling

`floatHashKey` folded an integral float onto the integer's key by
formatting it. Past 2^53 the shortest round-trip decimal is a DIFFERENT
number from the float, so the fold merged keys CPython splits (`1e23`
vs `10**23`) and split keys CPython merges (`2**64` vs
`1.8446744073709552e19`). It now takes the float's exact integer value
through `big.Float`. Anywhere else this port compares a float against an
integer, the same rule applies: compare values, never renderings.

### `fail` assigns before it reads

`task["status"] = "failed"` is `__setitem__`, and its TypeError differs
from `__getitem__`'s for every type except list. `asAssignable` is the
assignment's spelling; `asIndexable` stays the read's. Both share
`listish` so they cannot drift about which decoded shapes are lists.
When porting a function, the FIRST operation on a value decides which
error the caller sees — reading the whole body and picking the most
common operation is how this one was got wrong.

### `DrainOptions.JobIDs` is keyed by HashKey

The set holds `"s:job-1"`, not `"job-1"`, because a Python set holds
`4242` and `"4242"` as different elements. Build it with `NewJobIDSet`
or `NewJobIDSetOfStrings`; a raw-id map literal compiles and matches
nothing. The type is `JobIDSet` now so the two cannot be confused at a
call site.

### Test-side changes worth knowing about

- `normTask` and `normSweep` no longer round-trip through
  `map[string]any`. That erased number spelling (`3.0` vs `3`) and key
  ORDER, both of which those tables exist to pin. They go through
  `pyval.LoadsOrdered` + `DumpsCompactPy` now, so everything outside the
  two masked fields really is compared byte for byte, as the comment
  always claimed.
- The sweep differential has a `fail_arg` verb. Every other verb there
  reads a key first, so none of them could reach the assignment error.
- `internal/skills/variant_diff_test.go` asserts both runtimes LOADED
  the rows it seeded, measured BEFORE the call — retirement rewrites the
  pool, so reading the count afterwards reports the survivors and makes
  a working case look like an inadmissible fixture. The first cut of
  that table seeded rows without `content_hash`/`created_at`, which
  `validate_skill_row` refuses and `load_skills` silently skips, so
  twelve cases compared two empty answers and agreed. The mutation
  battery caught two of twelve; it now catches fifteen of fifteen.

### The audience census caught the A/B events

`internal/skills/variant.go` emits `SKILL_VARIANT_CREATED` and
`AB_RETIRED`, and `TestEveryEmittedEventTypeHasADecidedAudience` failed
on the first full-suite run after it landed — twice, which is the part
worth recording. Registering the two in the census table was not enough:
the test compares the audience the EMITTER actually stamps, so the
production set in `record.go` had to name them too. A table that agreed
with Python while the code disagreed would have been exactly the shape
lens 1 warns about, and the census refuses to be satisfied by it. Both
are `user` in Python's `USER_SURFACED_EVENTS`.

## Round-10 fixes — pyStrip everywhere, and a claim turned into a test

### `str.strip()` / `str.lower()` are not their Go namesakes

Two divergences, both measured on this box, both now carried by
`pytext.Strip` / `pytext.Lower` (with `pyStrip` / `pyLower` as the
`internal/skills` wrappers):

* `str.strip()` removes **29** code points. Go's `unicode.IsSpace` knows
  25 and misses **U+001C–U+001F** — FILE, GROUP, RECORD and UNIT
  SEPARATOR.
* `str.lower()` applies FULL case mapping. **U+0130 lowercases to TWO
  runes** ("i" + U+0307); `strings.ToLower` gives one. Measured:
  `"İSTANBUL".lower()` is nine characters in CPython, eight in Go.

`internal/skills/mint.go` used the Go spellings for `name`,
`description`, `domain` and each `steps_template` entry — the four
fields `ComputeSkillHash` hashes. So the same model reply produced a
different `content_hash` in each runtime, and a name of only separators
was dropped silently by CPython while the port wrote it, had it refused
by `ValidateSkillRow`, and returned the empty list — **discarding
skills it had already written to disk**.

The fixtures that pin this spell the separators as JSON *escapes*
(`jsonEscape(0x1c)`), not raw bytes. A raw control character is not
legal inside a JSON string: the first cut embedded the bytes, both
runtimes rejected the reply outright, and the fixtures agreed about
nothing.

### `manifestSkillIDs` now uses the admission predicate everyone else uses

Python reads the run's injection manifest through `_read_store →
_classify → loads_clean`. The port had hand-rolled `bufio.Scanner` +
`json.Unmarshal`, which admits raw non-UTF-8 bytes (substituted to
U+FFFD), escaped lone surrogates, duplicate names, non-object lines and
trailing data. It is now `record.IsFrameBlank` + `record.LoadsClean`
over a whole-file split, matching the five other readers in the package.

Two rules worth keeping in mind for any remaining reader:

* **blank means EMPTY, not whitespace.** `_classify` strips the trailing
  newline and compares to `b""`. A whitespace-only line is a COUNTED
  loss in Python, not framing.
* **`bufio.Scanner` is not `for line in f`.** It also strips a trailing
  `\r`, and it caps line length. Split the whole read on `"\n"`.

### `pyprobe.Probe.Env`, and what it refuses

New field, for a probe whose subject is something only settable before
the interpreter starts. The only such thing so far is
`PYTHONHASHSEED`. It **refuses** `MARO_WORKSPACE`, `MARO_USER_DIR` and
`PYTHONPATH`: each is what a guard reads, and an escape hatch that can
switch off the live-workspace refusal from a field named `Env` is one
nobody would think to look at.

### A named ambiguity: `RetireLosingVariants` on a variant chain

For a chain `p1 ← c1 ← c2` where BOTH links win, the content the
surviving `p1` ends up with depends on which parent id CPython's set
yielded first — so **CPython does not agree with itself across runs**
(measured: `list({"p1","c1"})` splits 8/4 across twelve hash seeds). The
port picks first-appearance order, which is deterministic and reachable.

`variant_diff_test.go` no longer asserts that in prose. For any fixture
where an id appears in both `promoted` and `retired`, it sweeps twelve
seeds, collects the distinct answers, and requires the port's file to be
one of them — falling back to STRICT comparison when the sweep finds
only one, because containment against a single candidate is equality
with the failure message thrown away. That fallback is not hypothetical:
the existing "a promoted parent that is itself retired" fixture is
chain-shaped but content-identical either way, and the guard caught it
on the first run.

### Value semantics where Python mutates

`CreateSkillVariant` takes `rewritten *Skill`. Python assigns onto the
caller's object and returns that same object; taking it by value left a
Python-shaped caller holding an unmarked skill, which
`RetireLosingVariants` — grouping on `variant_of` — would never see.

### Named residual: the port-wide strip/lower sweep

`internal/skills` is fixed and `internal/intent`, `internal/jsonx`,
`internal/now`, `internal/guard` and `internal/pytext` were already
correct (they carry comments saying so). There are ~130 other non-test
`strings.TrimSpace` / `strings.ToLower` / `strings.ToUpper` calls across
25 packages, and each needs the same question asked of it: **does this
port a Python `.strip()`/`.lower()`, and does the result become data
that outlives the call?** A classification sweep is in flight; the
exposure to fix is the subset that is stored, hashed, used as a map key,
or gates a write. A truthiness test IS in that subset when it decides
whether a record is written — that shape is exactly what H1 was.

## Phase 14 — the skill-mutation gate, and the helper the port kept re-writing

`generate_skill_tests`, `run_skill_tests`, `validate_skill_mutation` and
`_load_skill_tests` (`skills.py` 2790–3100) plus their dataclasses, ported
as `internal/skills/testgate.go`. `jsonx.ArrayOrdered` is new alongside it —
`extract_json_array` with the alt-bracket and dict-unwrap fallbacks, order
preserved, because the LLM path's answer is a list of objects whose key
order reaches a stored row.

Four differentials drive it (`testgate_diff_test.go`), one per verb, with a
scripted stub adapter that records every prompt. The prompts are compared,
not just the results: the `[:5]` failure slice, the `[:200]`/`[:300]`
description clips and the exact system text are only observable there, and
a port that returned identical test cases while asking a different question
would pass a results-only comparison.

### The bug the differential found in the port's own first cut

`_load_skill_tests` is a gate's denominator — `validate_skill_mutation`
blocks a mutation on `passed < total`. My `SkillTestCaseFromDict` refused
rows whose fields had drifted (an `input_description` holding the int `7`,
say). CPython's `SkillTestCase.from_dict` is four `d.get(k, default)` calls:
it neither coerces nor refuses. It holds the int, **counts the row**, and
`str.lower()` on it raises later — inside `run_skill_tests`' per-test
`except`, which counts the test as failed. Refusing the row instead makes
`total` smaller and the gate *easier* to pass. Fixed by never refusing:
`ExpectedKeywords []any` plus a `keywordsRaw` field so a non-list value is
held as the row holds it, and a `MarshalJSON` that re-emits the raw value
rather than the port's one-element-list storage shape.

One divergence is unavoidable and is now ASSERTED rather than excused: Go
renders a non-string scalar through `pyval.Str` where Python holds the
original object. `renderNamedDivergence` in the test names it.

### `read_jsonl_announced`: a helper you did not look for is a helper you will write again

The load differential compared the WARNINGS, not just the rows, and CPython
said `_load_skill_tests: dropped 1 line(s): 1 malformed (…)` where the port
said nothing at all.

Python funnels ~20 loaders through `jsonl_utils.read_jsonl_announced`. The
port had spelled the scan out inline at every site — `ReadFile`, split,
`IsFrameBlank`, `LoadsClean`, `continue` — and every one of those copies got
the right rows and dropped the announcement, because the announcement lives
in the helper and nowhere else. A short list is indistinguishable from a
short store, and no differential over a loader's RETURN VALUE can see the
difference.

Now ported properly as `record.ReadAllAnnounced` + `record.SkipReport`, with
`internal/record/announce_diff_test.go` comparing the sentences verbatim
against CPython's own logger output across sixteen fixtures. The bucket is
the operator-facing claim and the buckets are pinned separately: `1 non-dict`
says a writer is emitting the wrong SHAPE, `1 malformed` says the bytes are
torn, `1 undecodable` says the append was cut mid-write. All three drop the
same row.

Two supporting changes fell out of it:

* `record.LoadsCleanValue` — `LoadsClean` without the object requirement.
  `LoadsClean` is now that function plus one type assertion, so the strict
  predicate cannot fork into two versions that disagree about what a clean
  line is.
* `ErrNotAnObject` and `ErrByteTainted` are sentinels. The classifier needs
  to tell "not JSON" from "JSON of the wrong shape" from "not UTF-8", and
  the alternative was matching on message text — prose that gets reworded,
  after which torn bytes would quietly be counted as malformed.

`TestNothingRoutesAroundTheAnnouncedReader` is the tripwire: a new loader
that spells the scan out inline is silent in exactly the way this one was,
and nothing about its rows makes that visible.

**Named residual:** only `internal/skills/testgate.go` reads through the
announced reader so far. Every other inline store scan in the port is still
silent about what it drops. That migration is a queued chunk of its own,
alongside the ~130-site strip/lower sweep.

### Two guards no mutation can pin, both kept and both named in the code

The battery is 32 CAUGHT out of 34, and the two misses are structural
rather than gaps:

* **the non-object item skip** in the LLM path. Delete it in Python and a
  string item makes `item.get(...)` raise `AttributeError`, which the outer
  `try` catches — abandoning the whole LLM path and falling to the
  heuristic. Delete it in Go and `obj` is the zero `pyval.Obj`: every `Get`
  misses, the description is `""`, and the item is dropped three lines down
  by the emptiness test. Same answer. Go's zero value silently absorbs what
  Python raises on, and the two agree only while that emptiness test stays
  where it is.
* **`res.Blocked = !dryRun && passed < total`**. `dryRun` is `adapter == nil`
  by construction and `run_skill_tests` returns `(total, total)` for that
  case, so `passed < total` is already false whenever `!dryRun` is. Python
  is redundant in the same place, for the same reason.

* **`_load_skill_tests`' drift counter is unreachable in BOTH runtimes** and
  is kept anyway. Python's `except Exception` guards `from_dict`, which is
  four `.get`s on a value the reader has already proved is a dict; there is
  no input that reaches it and raises. That is a fact about the constructor,
  not the loader — the day `from_dict` grows a required field, both runtimes
  start losing rows there and the branch that announces it should already
  exist.

## The strip/lower sweep, tier 1 — and the encoder underneath it

The queued sweep's first tier was the four `pack` sites where a Python
whitespace operation reaches a HASHED artifact. All four were real; writing
the differential that pins them turned up a fifth defect that is worse than
all four together.

### What the four were

* `export.go` — the "Redacted lines" list renders `ln.strip()`, and REVIEW.md
  is hashed into the seal.
* `export.go` — `_read_jsonl_rows` is
  `[ln for ln in text.splitlines() if ln.strip()]`, and BOTH halves were
  wrong: `strings.Split(raw, "\n")` splits on one separator where Python
  splits on ten, and `TrimSpace` leaves a line of information separators
  non-empty where `ln.strip()` is falsy. These rows are the pack payload.
* `seal.go` — the old-marker test's `.strip()` and the tail's bare
  `.rstrip()`, the latter spelled `TrimRight(reviewText, " \t\n")`, which is
  a three-character cutset where Python's bare `rstrip()` removes all 29.

Each decides bytes that get hashed, so each turns a pack one runtime sealed
into a pack the other refuses.

### The fifth: every scrubbed JSONL row shipped in the wrong dialect

`_scrub_jsonl_line` ends in a BARE `json.dumps(obj)`. The port re-emitted
those rows with `CanonicalJSON` — this package's encoder for the hashed
`pack.json` metadata, which is `sort_keys=True, separators=(",", ":"),
ensure_ascii=False`. A bare `json.dumps` is the opposite on all three
counts: insertion order, `", "` / `": "`, and non-ASCII escaped to `\uXXXX`.

So every scrubbed row was different BYTES in the two runtimes, every
artifact `sha256` differed, and no Go-exported pack could ever have verified
in Python. Nothing in the Go-only round-trip tests could see it: both ends
of those spoke the same wrong dialect, which is what a round trip is for and
also what it cannot check. `pyval.DumpsCompactPy` — a bare `json.dumps`,
insertion-ordered, ensure_ascii on — already existed. Third time this
session that the fix was a helper nobody looked for.

### Tier 2, the two that gate a write

* `knowledge.AbsorbVariant` trimmed with `TrimSpace` where `_absorb_variant`
  calls `.strip()`. `merged_variants` is a stored field on a shared
  tiered-lessons row, so the same lesson carried two different variant lists
  in the two runtimes, each re-absorbing the other's spelling as new.
* `pack/import.go`'s `minted_from` normalizer is `raw_stamp.strip().lower()`
  and **it is the provenance gate**: the retrieval quarantine matches the
  exact string `"prompt"`, and this is what turns a foreign exporter's
  spelling into it. `"prompt"` plus a trailing information separator
  normalizes to `"prompt"` in CPython and to nothing here — so the port
  discarded the incoming claim and imported the row unquarantined.

### The differential, and the trap it fell into first

`internal/pack/whitespace_diff_test.go` is the package's first CPython
differential: it exports and seals the same seeded workspace on both sides
and compares REVIEW.md byte for byte, the artifact row counts, the member
bytes, and each side's stamped digest against the document that side
actually shipped. A second test imports ONE Go-built pack with both
runtimes, so an exporter difference cannot be credited to the importer.

Two fixtures had to be repaired before they measured anything:

* the separators-only row was spelled with `\x1c`, which `str.splitlines()`
  BREAKS on — so the splitlines fix tore it into empty fragments before the
  strip predicate ever saw it and the row filter went unpinned. It is
  `\x1f` now: strip removes it, splitlines does not break on it.
* the drifted `minted_from` stamp was a RAW control byte inside a JSON
  string, which is illegal JSON — so both runtimes failed to parse the row,
  both skipped it, and the test passed while testing nothing. It is a JSON
  escape now.

Mutation battery: 9 of 10 CAUGHT. The miss is the import-side variant gate,
which cannot be pinned alone now that `AbsorbVariant` strips correctly —
two guards, one observable. That pairing is written down at the site.

**Still queued:** the sweep's tier-3 and tier-4 sites (planner, loop,
closure, director, graduation, inspector, cmd), its six UNRESOLVED entries,
and the port-wide migration of inline store scans onto
`record.ReadAllAnnounced`.

## Round 11 — ten findings against the Phase-14 chunk, eight of them real

Whole-chunk review (the standing amendment: the entire chunk plus its
fixes, not the latest diff). Nine findings were verified against CPython and
fixed; one was a misread; one was a comment that understated a divergence.

### The one that mattered

**`pyIterate` did not know the type its own reader produces.** It had a
`pyval.Obj` arm for "a mapping's keys" and no `map[string]any` arm — and
`map[string]any` is the ONLY mapping that can reach the run gate, because
`record.LoadsClean` decodes through `encoding/json`. The ordered arm looks
exactly like coverage for the case and is dead for the caller that needs it.

A stored `"expected_keywords": {"alpha": 1}` therefore raised TypeError, the
test was skipped rather than run, and the row still counted toward `total` —
so the short `passed` BLOCKED a mutation CPython lets through. Measured:
CPython `(1, 1)`, the port `(0, 1)`. Sorted keys, not insertion order,
because a Go map cannot remember one; the only consumer is an order-blind
`any(...)`, and the comment says where a caller that needs order should go
instead.

### A zero value that had to mean two things

`keywordsRaw != nil` meant "this row's keywords were not a list". A stored
JSON `null` IS a drifted value and IS a nil `any`, so the test read it as a
proper list and re-emitted `[null]` — the exact invented shape `MarshalJSON`
exists to prevent. Now an explicit `keywordsIsRaw` flag, and `ToDict` and
`MarshalJSON` share one `keywordsForWrite` so the two writers cannot
disagree.

### The announcement stopped one frame above the announcement

`ValidateSkillMutation` did `tests, _ := LoadSkillTests(...)`. Python logs
the loss from inside the loader; this port returns warnings as data, which
moves the decision to the caller — and the caller dropped it. Announcing
into a return value nobody reads is the same silence with more code.

`ValidateSkillMutationWithLog` now carries them out (the shape
`record.WriteOutcomeWithLog` already established), and
`TestNothingRoutesAroundTheAnnouncedReader` checks BOTH frames. The
tripwire's first version watched only the call site, one level below where
the loss was happening — a helper does not fix a class, it fixes the callers
that reach it, and "reach" includes what they do with the answer.

### Tests that agreed without measuring

* both `[:200]` clips on the LLM path — the prompt's per-failure join and
  `derived_from_failure` — had no fixture at their own boundary: every
  scripted case carried short failures and the one long failure had no
  adapter. Either constant could be changed to 100 or 300 with the battery
  green.
* `no_tools=True` is this gate's brake — a keyword smoke test that ran with
  live tools would let the evolver's own safety check act on the text it was
  asked to classify. The probe recorded the kwarg and nothing compared it.
  Both halves are asserted now: the kwarg on Python's side, `AgentTools` off
  and no tool protocol on Go's.
* `cmpSavedTests` routed through `normJSONL`, which re-marshals each row
  (sorting keys) and sorts the rows — throwing away `to_dict`'s key order,
  `json.dumps`' default separators (the whole reason `DumpsCompactPy` exists),
  and the append order. All three were correct; none was pinned. It is a
  byte comparison now: the store is an append log, not an unordered set.
* `LoadSkillTests`' id test read `d["skill_id"]` with a bare type assertion,
  turning an absent key and a non-string alike into `""` — which EQUALS the
  argument when the argument is `""`. Every fixture queried `"s1"`, where
  the two spellings agree.

### The two that were not defects

* the separator-only-name fixture was reported as a duplicate of the
  empty-name case. It is not: the name is two RAW separator bytes, which
  render as nothing in a terminal and nothing in a diff. Rewritten as
  escapes — a fixture whose subject is invisible is one nobody can check.
* `announce.go`'s note said the Missing/Unreadable split was only "which
  flavour of nothing to announce". Measured: for an unsearchable parent
  directory, `Path.exists()` swallows the PermissionError and returns False,
  so Python logs NOTHING while Go announces "unreadable". One runtime is
  silent about the same directory. The note says that now.

Battery: 41 CAUGHT of 45. The four misses are all structural and named at
their sites — two guards Go's zero value or a sibling condition makes
unobservable, `_load_skill_tests`' drift counter (unreachable in both
runtimes), and `ToDict`'s raw arm (nothing re-saves a loaded row).

## Scope ledger — what the first pass has NOT touched

The standing goal is "the first pass of the go port completely implemented
and each tranche review-fixed", and until now the port had no honest answer
to "how much is left". This is that answer, measured rather than estimated:
26 Python modules, **30,338 lines**, are not referenced anywhere in the Go
tree — no port, no stub, no named divergence.

**Updated 2026-08-26:** `heartbeat.py` (1653) comes off this list — its
deterministic half is ported and its orchestration half is NAMED in the
package doc, which is the difference between "not started" and "scoped".
That leaves **25 modules, 28,685 lines**, one of which
(`correspondence.py`, 1174) is dev tooling by decree. So the honest
remaining first-pass surface is **24 modules, ~27,500 lines** — about the
size of everything ported so far, and not a one-session job. Recorded
plainly because the standing goal is measured against it.

**Also 2026-08-26, later:** `sheriff.py` (641) is ported to the same
standard — deterministic half done, orchestration half named — but it never
appeared in the table below, because that table lists only modules above
~690 lines. So the headline number is unchanged at **24 modules,
~27,500 lines**; what changed is one seam, not one row. The table
understates progress for anything under its cut, which is worth knowing
before reading it as a burndown.

**Also 2026-08-26, later still:** `system_health.py` (693) is ported to the
same standard, and it IS the last row of the table. So the remaining
first-pass surface is now **23 modules, ~26,800 lines**. Its seven declared
probes are named-not-ported — each reads a different live store and is its
own tranche — so the row is retired the way `sheriff.py`'s
`check_all_projects` is: the deterministic half done, the orchestration half
written down.

Measured by `grep -rl "<module>.py" internal/ cmd/`. That is a weak test
(it finds modules the port has NAMED, not ones it has finished), so it
overstates coverage and cannot understate it: anything on this list has not
been started.

| Module | Lines | What it is |
|---|---:|---|
| `loop_report.py` | 2705 | run readouts / operator-facing report assembly |
| `run_curation.py` | 2135 | post-run curation, re-render |
| `introspect.py` | 1775 | failure classifier, lenses, recovery planner |
| `container_exec.py` | 1676 | containerized worker lane |
| `orch_bridges.py` | 1522 | orchestration bridges |
| `loop_finalize.py` | 1319 | the loop's finalize phase |
| `navigator_shadow.py` | 1231 | shadow-lane navigator |
| `eval.py` | 1186 | evaluation harness |
| `correspondence.py` | 1174 | dev-facing retrieval (explicitly NOT runtime) |
| `web_fetch.py` | 1172 | Jina Reader / tweet fetching |
| `tail_jobs.py` | 1149 | tail-job runner |
| `quality_gate.py` | 1110 | the quality gate |
| `persona.py` | 1060 | persona system |
| `doctor.py` | 1001 | self-diagnosis |
| `agent_loop.py` | 871 | the loop entry point |
| `skill_lifecycle.py` | 843 | skill lifecycle |
| `camera_readout.py` | 817 | camera readout |
| `audit_repair.py` | 797 | audit + repair |
| `harness_optimizer.py` | 768 | harness optimizer |
| `shadow_lane.py` | 759 | champion–challenger lane |
| `scope.py` | 736 | scope generation |
| `artifact_check.py` | 735 | artifact checking |
| `delta_replay.py` | 729 | delta replay |
| `worktree.py` | 722 | worktree management |

`correspondence.py` is dev tooling by decree and does not belong in a
runtime port at all; the other 23 do.

### 2026-08-27 — the burndown was reporting the wrong denominator

Re-measured, and the headline above is not the remaining surface. It is
the remaining surface **among the modules this table happens to list**,
and the table has a size cut-off at ~690 lines that was a DISPLAY decision
before it was a scoping one.

The whole Python surface: **183 modules, 132,730 lines.** The table tracks
the **59 modules over 690 lines**. The other **124 modules — 45,060 lines,
34% of the source — are not in the burndown at all**, ported or otherwise.
Nothing above says so; the sentence right before this one says "the other
23 do", which reads as 23 modules left in the port.

The table already carried the sentence *"the table understates progress
for anything under its cut, which is worth knowing before reading it as a
burndown"* — written about the numerator. The same cut applies to the
denominator, and that half was never said.

**What the remaining surface actually is, honestly:** the grep this
section uses (`grep -rl "<module>.py" internal/ cmd/`) is weak in BOTH
directions, which was known for the overstating half and not for the other.
It overstates coverage — naming a module is not porting it — and it also
UNDERSTATES coverage, because the port names some sources by bare module
name rather than filename (`killswitch`, `ancestry`, `prefixes` and
`security` are all reached that way and all read as unported to the `.py`
grep). Three spellings of the same census disagree:

| measure | modules unreferenced | lines |
|---|---:|---:|
| `"<module>.py"` anywhere in `internal/ cmd/ tools/` | 117 | 59,091 |
| bare `\bmodule\b` anywhere | 81 | 38,164 |
| `"<module>.py"` in a PRODUCTION `.go` file only | 47 | 57,221 |

The third disagrees with the first two on WHICH modules, not just how
many — it calls `handle.py`, `llm.py` and `skills.py` unported, which is
plainly false, because production Go names those sources in package docs
that the filter happens to miss. So none of the three is the answer.

The answer this arc needs is a per-module PORT STATUS, declared rather than
grepped — the same shape as the divergence rows: written down, and failing
when it stops being true. That is its own slice and it is now the honest
next one, because the standing goal ("the first pass completely
implemented") is measured against this number and the number has been off
by more than the work done so far. Until it exists, read every figure in
this section as a lower bound with an unknown gap above it.

### What the list changes about the sweep's tiering

Chasing the strip/lower sweep into `planner` turned up the reason this
ledger is needed. `planner.Decompose` is not a port of `planner.decompose`
with a whitespace bug in it — it is a **v0 function of its own**: a
different system prompt, a different user message, and no `parse_steps` at
all (no `FACT:` extraction, no `[after:N]` placeholder drop). Its
`TrimSpace` cannot diverge from `raw.strip()` in any way that matters,
because the two functions were never going to produce the same steps.

So the sweep's tier-3 entries need re-tiering before they are worked:
**a whitespace divergence inside an already-divergent function is not a
bug, it is a footnote on a bigger one.** The tier-1 and tier-2 sites that
have been fixed were all inside functions the port DOES claim byte-equality
for — a sealed pack, a hashed manifest, a provenance stamp — which is why
they were worth the differential.

## The pack transport review — thirteen findings, worked

An adversarial review of the pack export/import chunk (built both binaries
and measured every claim end-to-end rather than reading code) returned
thirteen findings. Every one was verified at the source before being
touched; twelve were real, one — a claimed duplicate-key leak in the
quarantine predicate — turned out to be an equivalent mutant and the
"fix" written for it was deleted.

They were not thirteen unrelated bugs. Four of them were one missing helper,
three were one Python idiom spelled as a Go type assertion, and two were the
same "what does `json.loads` actually produce" question asked at two ends of
the pipe.

### The missing helper: `read_text` had been ported in halves

`pyval.DecodeUTF8Strict` had existed for a while and carried one half of
`Path.read_text(encoding="utf-8")`: refuse invalid UTF-8. Nothing carried the
other half — `newline=None`, so CRLF and a lone CR become LF before the
caller sees a byte. Every call site had `os.ReadFile` + `string(raw)`, which
is neither half, and each looked complete on its own.

The consequences ran the length of the transport:

* A CRLF skill file exported by Go produced different member bytes, a
  different artifact sha256, a different REVIEW.md and a different payload
  digest than the same file exported by Python. Neither pack verified in the
  other runtime.
* A byte-tainted skill file aborted the CPython export and was silently
  SKIPPED here — a pack that looks complete and is one artifact short is
  worse than a pack that fails to build.
* On the import side the translation decides an OUTCOME, not just bytes: a
  local skill saved with CRLF is `skipped_identical` to Python and
  `conflict_quarantined` here, so the port wrote a quarantine file and a
  CONFLICTS.md row for a file the other runtime calls unchanged. Two
  separate compare sites, one call apart, and the fixture for the first read
  as covering the second until a mutation said otherwise.

`pyval.ReadText` is now the whole function, and eight call sites use it.

### The `json.loads` question, asked at both ends

`_quarantined_row` is the gate that keeps `minted_from="prompt"` rows out of
a curated pack. It is `json.loads` in CPython, which accepts the bare `NaN`
token — a token CPython's own `json.dumps` WRITES for a non-finite float.
`encoding/json` refuses it, the refusal returned false, and the
prompt-derived row shipped. That is the db37d525 contamination class
travelling by transport, defeated by a row CPython itself wrote.

At the other end, a `json.loads` → `json.dumps` round-trip in CPython cannot
preserve a numeric literal, because there is no literal left to preserve —
only an int or a float. `LoadsOrdered`'s `UseNumber` kept the source text, so
the port re-emitted `1e3`, `0.10`, `5.00` and `-0` where CPython writes
`1000.0`, `0.1`, `5.0` and `0`. `pyval.PyNumbers` is the rule, and it keeps
integral literals as literals on purpose: CPython's int is
arbitrary-precision and a 20-digit id must not be routed through float64 in
the name of normalizing.

### A Python idiom is not a Go type assertion

Three findings were the same shape, and this file had already fixed the
identical idiom twice within a screen of the third:

* `if row.get("imported"):` is TRUTHINESS, and the value is stored whatever
  it is. The type assertion made it a shape test only dicts pass, so an
  incoming `"imported": "from-elsewhere"` dropped the provenance chain here
  and kept it there.
* `float(row.get("score", 1.0))` defaults only when the key is ABSENT. An
  explicit null is a PRESENT key, so CPython gets None, `float(None)` raises
  and the row is `malformed_skipped`. `row[key]` is nil for both, so the port
  imported a row CPython refuses, into a live shared store, with an invented
  trust value. A zero value that has to mean two things means neither.
* `{scrub(k): scrub(v) for ...}` is a dict comprehension and cannot hold a
  key twice. Two secret-shaped keys both scrubbing to `[REDACTED]` — the
  scrubber's ordinary case, not a contrived one — produced a JSON object with
  a duplicate key, carrying a value Python discards.

### Key ORDER is content when the store is shared

`TieredLesson.Imported` and `Hypothesis.Imported` were `map[string]any`.
Python builds those blocks as dict literals and `json.dumps` writes insertion
order; a Go map has none, so `DumpsStruct` sorted them and the two runtimes
wrote different bytes for the same provenance chain into
`memory/medium/lessons.jsonl` and `memory/hypotheses.jsonl` — live shared
stores, not audit trails. Both fields are `pyval.Obj` now, and `fromValue`
learned to pass an ordered value through instead of rendering it as an array
of `{"Key":…,"Val":…}`.

### What is written down rather than fixed

* A LONE SURROGATE in a row's string value. `json.loads` keeps it and
  `json.dumps` writes `\ud800` back; `LoadsOrdered` decodes through
  encoding/json, which rewrites it to U+FFFD. The fix is a string decoder
  inside `LoadsOrdered` that every caller in the port shares. Named at the
  export consumer too, because there the consequence is a pack the other
  runtime cannot verify — and note the asymmetry: the IMPORTER refuses a
  lone surrogate loudly, the exporter rewrites it silently.
* `lesson` and `source_goal` are stored RAW by Python (no `str()`, unlike the
  `task_type`/`outcome` pair beside them) and the port's struct fields are
  Go strings. Widening them to `any` ripples through every reader in the
  knowledge package to buy fidelity on a shape no writer in either runtime
  produces.
* The `original_provenance` chain arrives off the strict decoder as a
  `map[string]any` with its order already gone, so an incoming provenance
  OBJECT sorts where CPython keeps the parse order. Same fix as the tiered
  loader needs: decode that path through `LoadsOrdered`.
* `nowISO()` always writes six fractional digits; `datetime.isoformat()`
  omits `.ffffff` entirely when the microsecond is zero. One stamp in 10⁶.
  Port-wide, not pack's.
* PORT-WIDE: every other `DumpsCompactPy` caller that re-emits a row it read
  through `LoadsOrdered` has the number-literal divergence. Each needs its
  own differential to say whether the row it re-emits is one CPython would
  have round-tripped, so they were not swept blind.

### What the review says about the differentials

The one finding the existing differential structurally could not see is the
sharpest lesson in the batch. `reviewSection`'s strip was fixed and its
`splitlines()` was not, and the fixture written for the strip put the
separator at the END of the flagged line — where the strip consumes it. Both
runtimes rendered the identical string. The fixture pinned the fix it was
written for and was blind to its sibling one line up, and no amount of
re-reading it would have shown that: it took a mutation to say so.

Two new differentials, both end-to-end over real archives:
`export_fidelity_diff_test.go` (6 cases, comparing every member byte for byte
via a latin-1 byte channel, plus the manifest's per-artifact sha256) and
`import_fidelity_diff_test.go` (4 cases, letting CPython build AND seal the
pack so the exporter is out of the question, then importing the same file
twice and comparing the result rows and the store bytes). Mutation batteries:
11/11 on export, 10/10 on import, every anti-vacuity guard asserting that
CPython actually exhibits the behaviour under test before the comparison runs.

## The graduation window — and why "sweep every `strings.Split`" is wrong

The pack chunk's commit message named its own method: *a fix is evidence
about its siblings*. One of the siblings it named was `splitlines()` vs
`strings.Split(s, "\n")`. This chunk followed that thread into
`internal/graduation`, and the first thing the thread did was correct the
instruction that sent me down it.

### What was actually wrong in graduation

`src/graduation.py` reads its two ledgers the same way in three places
(lines 226, 277, 417):

```python
lines = path.read_text(encoding="utf-8").splitlines()
for line in lines[-lookback:]:
    line = line.strip()
    if not line: continue
```

The Go read them with `os.ReadFile` + `strings.Split(string(raw), "\n")` +
`strings.TrimSpace`. Four Python rules, three of them missing:

1. **`splitlines()` breaks on ten separators**, a `"\n"` split on one. A
   stored row carrying `\x0b` or `\x1c` is TWO lines to Python and one to
   Go. That is not a rendering difference, because of rule 2.
2. **The slice happens AFTER the split.** `lines[-lookback:]` is measured
   in lines, so a disagreement about how many lines a file holds is a
   disagreement about WHICH rows are inside the lookback window — and the
   window decides which diagnoses reach a gate whose job is to propose
   permanent changes to the system's own behaviour.
3. **`str.strip()` removes U+001C–U+001F; `strings.TrimSpace` does not.**
   A row with a trailing `\x1f` is blank-then-parsed in Python and
   non-blank-then-rejected in Go: Go's `encoding/json` refuses trailing
   bytes outside its own whitespace set (space, tab, `\n`, `\r`). One row
   lost, silently.
4. **`read_text` decodes STRICTLY**, and both call sites wrap it in a bare
   `except` — `scan_candidates` returns `[]`, `_already_proposed` returns
   `False`. So one bad byte anywhere in the diagnoses ledger means CPython
   proposes NOTHING, where `string(raw)` reads on and `encoding/json`
   substitutes U+FFFD per bad byte. The port would have built a graduation
   candidate out of content nobody wrote and proposed a permanent rule
   from it. Refusing is the behaviour, not a courtesy.

### The one the sweep found that the sweep was not looking for

`tailLines` and `lastLines` were two hand-written copies of the same rule,
and they had **drifted in the ORDER of the last two steps**. `tailLines`
sliced the window and then dropped blanks (Python's order); `lastLines`
dropped blanks and then sliced. So blank lines cost `lastLines` no window
budget and it reached further back into the ledger than the predicate it
was supposed to replay.

That matters because `lastLines` is the in-lock half of the dedup
re-check — a Go-only TOCTOU guard, since Python's `run_graduation` appends
under the lock without re-checking. A re-check that windows differently
from the pre-check protects nothing: it can decide a class was already
proposed on evidence the pre-check had aged out, and silently suppress a
suggestion CPython writes.

The comment above it read *"it replays the SAME windowed predicate as the
pre-check (r2 HIGH-1)"*. That was true when written and had since stopped
being true — **lens 19 again: a comment that asserts coverage is a claim,
and it decays.** Both are now `pyWindowLines`, one function, so the claim
is structural instead of asserted.

`lastLines` is also unreachable from any CPython differential: with no
concurrent writer it always sees the file the pre-check just read, so it
can only be wrong by disagreeing with `tailLines` — which is precisely
what no test through the gate could show. The pin is
`TestWindowHelpersMatchCPython`, which asserts `pyWindowLines`, `lastLines`
AND `tailLines` against CPython's four spellings over fourteen crafted
texts x seven window widths.

### The correction: there are THREE Python line-splitting rules here

The sibling sweep listed thirteen more `strings.Split(string(raw), "\n")`
sites and I was one edit away from converting them. **Do not.** This
codebase reads lines three different ways, and they are not
interchangeable:

| Python spelling | Breaks on | Go equivalent |
|---|---|---|
| `read_text(...).splitlines()` | ten separators | `pytext.SplitLines` |
| `path.open("rb")` then `for line in f` | `b"\n"` only | `strings.Split(s, "\n")` |
| `path.open()` (text) then `for line in f` | `\n`, `\r`, `\r\n` | neither — needs its own helper |

`internal/record/announce.go:181` is the central announced reader that
`skills/store.go` and `testgate.go` were migrated onto last chunk, so
"fixing" it would have swept every migrated caller at once — in the wrong
direction. Its Python counterpart is `jsonl_utils._read`, which opens
**binary** (`path.open("rb")`) and iterates, and whose tail path splits on
`b"\n"` explicitly. `strings.Split(string(raw), "\n")` is the correct port
there and must stay.

So the sweep is a per-site CLASSIFICATION, not a mechanical replacement,
and the remaining twelve sites are a tracked list rather than a queue of
known bugs. **Lens 20: an idiom is not a defect. The defect is a spelling
that does not match the spelling at ITS OWN site** — and a sweep that
knows only the fix will apply it where the fix is the bug.

Sites still to classify: `record/record.go:643` (`stampOutcomeVerdictLocked`),
`record/outcomes.go:38`, `scans/scans.go:109`, `loop/project.go:108`,
`inspector/inspector.go:1013`, `knowledge/tiered.go:128`,
`skills/pool.go:151`, `skills/stats.go:200`,
`notify/escalation_context.go:175`, `evolver/store.go:194` and `:883`,
`skills/attribution.go:295`. Each needs its Python counterpart READ, not
guessed.

### The Go-only knob, named

`cmd/maro/selfimprove.go` exposes `-lookback`, which Python never does
(`run_graduation` is only ever called with defaults, from `evolver.py:817`).
`ScanCandidates` coerces `lookback <= 0` to 100; Python's `lines[-0:]`
would return the whole file and `lines[-(-5):]` would drop the FIRST five.
The coercion is the friendlier behaviour and the divergence is unreachable
from any Python caller, so it stays — named here rather than silently
differing.

### Evidence

`window_diff_test.go`: `TestGraduationWindowMatchesCPython` (5 widths),
`TestGraduationDedupWindowMatchesCPython` (4 widths, a SEPARATE argument on
a SEPARATE ledger), `TestGraduationRefusesUndecodableLedger` (with an
anti-vacuity guard that fails if CPython did not actually refuse, and a
second assertion that the refusal is scoped to the undecodable file rather
than blanking every read), `TestWindowHelpersMatchCPython` (98 subtests),
`TestLastLinesRefusesUndecodableTail`.

Mutation battery 8/8 DETECTED: both spellings of the split, both of the
strip, the strict decode at both readers, a swallowed decode error, the
window slice removed, the slice/filter order inverted, and `lastLines`
re-drifting away from the shared helper.

Two fixture bugs that CPython and the battery caught, both worth keeping in
mind:

* the blanks must be CONSECUTIVE and the window must straddle them, or
  slice-before-filter and slice-after-filter land on the same real rows and
  the fixture pins neither order. The first version of this fixture spread
  its blanks out and mutant `G8` MISSED.
* `"\x85"` in a Go string literal is the raw byte 0x85, which is not valid
  UTF-8 — and `pyprobe`'s own JSON argument channel substitutes U+FFFD
  before Python ever sees it. The fixture was measuring the transport
  rather than the rule, and it read as agreement. The three non-ASCII
  separators must be spelled ``, ` `, ` `. **Lens 21: a
  fixture travels through a channel, and the channel has opinions about
  what it carries.**

## The transport review, round 5 — fourteen findings, and the shape of them

The whole-chunk review of `68d9bc75` + `8284e3a7` returned 4 HIGH, 4 MEDIUM
and 6 LOW. All fourteen code claims were verified at source before anything
was touched; all fourteen held. That is well above the ~50–70% rate this
project has seen, and the reason is visible in the report: the reviewer
measured rather than read, ran CPython on both sides of every claim, and
self-caught one false finding of its own (a raw `0x85` byte in a Go literal
rather than U+0085 — the same invisible-byte trap that cost the previous
round a false report, and that this chunk's own fixture then walked into).

### What the findings actually were

Not fourteen bugs. Counting by CAUSE:

**One idiom, three sites (H2, H4, and the fix that caused H2).**
`.get(k, default)` defaults on ABSENCE, and a Go map's `row[key]` is nil for
both absent and null. `pyFloatGet` was written for exactly this in the
PREVIOUS chunk, with a comment naming the lens — and then the same chunk
introduced a fresh instance of it two screens away, by replacing a wrong
`asString(row["tier"])` with `row["tier"]`. The old code was right about
absent and wrong about numeric; the fix inverted which case diverged.

That is the whole argument for a named helper. There are now three:
`pyFloatGet` (`float(row.get(k, d))`), `pyGet` (`row.get(k, d)` for a value
that is STORED), and `pyStrGet` (`str(row.get(k, d))`). The three Python
spellings produce three different answers for a null — a TypeError, a JSON
null, and the literal string `"None"` — and collapsing them into one Go
expression is what made each fix look complete.

`asString(nil)` returned `""`. `str(None)` is `"None"`. A function that
claims to be `str()` and is wrong about exactly one value is wrong precisely
where an untrusted document can reach it.

**One decoder, two ends (H1, H3).** `json.loads` is not
`json.NewDecoder(...).Decode(&m)` in three ways, and the port had fixed two.

*H3* — the trailing-content check was spelled `err == nil`, which refuses
only trailing content that is itself a valid JSON token. `{"a":1}x` parsed
clean and the `x` vanished. Both sibling implementations
(`record.LoadsCleanValue`, `pack.decodeStrictJSONObject`) already had
`err != io.EOF`; this was the odd copy, and its comment — *"Refuse trailing
content the way json.loads does"* — asserted a coverage it did not have.
The consequence was destructive, not lenient: `scrubJSONLLine` parses a row
and RE-EMITS it, so a row CPython refuses (and therefore scrubs as raw text
and ships intact) had its trailing bytes deleted from a hashed payload —
different sha256 for the same input, neither pack verifying in the other
runtime.

*H1* — CPython's `json.loads` accepts bare `NaN`/`Infinity`/`-Infinity` by
default, and, more to the point, CPython's `json.dumps` WRITES them by
default. `knowledge_web` appends tiered lessons with a plain
`json.dumps(asdict(tl))`, so a store CPython wrote can hold a row Go's
decoder refuses whole. All three pack trust lanes dropped such a row with no
imported row, no `malformed_skipped` report row, and no warning. Measured
end-to-end on a CPython-built-and-sealed pack: 3 hypotheses there, 1 here;
`memory/medium/lessons.jsonl` written there, not written at all here.

Both are now one function — `pyval.LoadsMap`, "bare `json.loads`, indexed as
a dict" — which `decodeStrictJSONObject` delegates to rather than being a
fourth spelling of. It is explicitly NOT the reader for a caller porting
`loads_clean`, which passes `parse_constant=_refuse_constant` and refuses
those tokens on purpose. Two Python functions, two Go functions.

**Operator sentences, again (M1, M2).** The previous chunk removed a
`"score: "` prefix from an error path with a comment explaining that the
text is a CONTENT KEY — a `malformed_skipped` row both runtimes write to a
shared audit ledger — and left the error text itself invented at three of
four branches. `not a number: map[]` is Go's `%v` spelling of a map, in a
row an operator reads. Naming a class is not sweeping it. Likewise the
off-vocabulary-scope warning carried a `warn: ` prefix that appears nowhere
else in this port or in Python, and `%v` where Python writes `%r`.

**A fix that made a dormant divergence load-bearing (M3).** Python filters
the skills glob with `if f.is_file()`; the port never had that guard. While
the read merely `continue`d on error the omission was invisible. Turning the
read into a propagating error — the right fix for a DIFFERENT divergence —
made a directory named `*.md` abort the entire export. *A fix is evidence
about its siblings* has a second direction: a fix can also make a sleeping
divergence fatal, and the guard it silently relied on was never there.

**The sweep item this chunk had already found (M4).** `recordedMission`
carries all three of the idioms the graduation sweep classified, at one site,
and the value decides which project directory a run writes into.

### The deliberate divergence the LOW uncovered

L3 was a one-line fix — `1e400` is already `inf` by the time CPython's
`float()` sees it, so `float(inf)` succeeds where Go's
`json.Number.Float64` reports `+Inf` alongside `ErrRange` and the port
called that a coercion fault. Fixing it exposed the layer underneath, and
that layer is NOT a bug to be fixed into parity:

* CPython imports the row and writes a bare `Infinity` into
  `memory/medium/lessons.jsonl`.
* `prove_record_line` exists in that same codebase to stop exactly this: it
  dumps with `allow_nan=False` and re-reads through `loads_clean`, which
  refuses the bare tokens. `knowledge_web`'s append never calls it. **So the
  row CPython just wrote is a row CPython's own strict readers will not read
  back.** It is stranded on write.
* The port refuses at the writer and reports `malformed_skipped`, which
  costs the row and keeps the store readable.

The port stays on the refusing side, and
`TestImportRefusesToWriteANonFiniteCPythonWrites` pins the disagreement
EXPLICITLY so it cannot drift into an accident. Note the asymmetry, which is
not a contradiction: READING a non-finite must succeed (H1), because CPython
writes them and refusing the read loses the whole document. WRITING one is
where the port declines.

The L3 fix itself turned out to be unreachable through the pack pipeline —
both exporters normalize a float literal through loads→dumps before it
enters a pack, so `1e400` arrives as the token `Infinity`, and
`json.Number("Infinity").Float64()` returns `+Inf` with NO error, missing
the branch entirely. What reaches it is a HAND-BUILT pack, which is exactly
the input this file treats as adversarial. Measured, said at the site, and
pinned by a direct `asFloat` differential rather than through a pipeline
that cannot express it — *a measurement is only evidence about the call you
actually made.*

### The test that was agreeing with itself (L1)

`TestExportCollapsesKeysThatScrubToTheSameString` pinned the scrubber's
key-collapse and NOT the ordinal half its own comment stated — *"the key
keeps the ordinal of its FIRST appearance and the value of its LAST"* —
because with the two colliding keys ADJACENT, first-ordinal and last-ordinal
produce the same object. The reviewer measured it: a remove-and-append
mutant in `scrub.Walk` passed that test and the whole `internal/scrub`
suite.

The killing fixture is one plain key between the two collapsing ones. The
tell was available without the mutant: the comment claimed more than the
fixture could show.

### Evidence

New: `import_r5_diff_test.go` (`TestImportAcceptsCPythonNonFiniteRows`,
`TestImportStampsAbsentAndNullFieldsLikePython`,
`TestImportReportsPythonsFloatErrors`,
`TestImportRefusesToWriteANonFiniteCPythonWrites`,
`TestAsFloatMatchesPythonFloat` × 14),
`export_r5_diff_test.go` (`TestExportShipsTrailingGarbageVerbatim`,
`TestExportSkipsANonFileMatchingTheGlob`), and
`loop/mission_line_diff_test.go` (14 separator cases + an undecodable
ledger). Every one carries an anti-vacuity guard that fails if CPython did
not actually exhibit the behaviour under test.

Mutation battery 12/12 DETECTED, covering both directions of the
absent-vs-null fix (the pre-fix `str()` spelling AND the post-fix raw-value
spelling, since the bug was an inversion between them), both float() error
sentences, the non-finite masking, the trailing-token check, the ordinal
rule, the `is_file` guard, and all three idioms at `recordedMission`.

Two fixture bugs of my own, caught by the anti-vacuity guards rather than by
review: the lesson fixtures seeded `memory/medium/lessons.jsonl` when the
exporter reads `memory/long/`, and a mechanical `str.replace` pass produced
rows with duplicate `minted_from` keys. Both showed up as "CPython imported
0 of 4" instead of as green tests — which is the entire reason the guards
are there.

### Carried, not fixed

* **L5.** `record/dailylog.go:236` (lesson count, missing the strict-decode
  half) and `loop/slot.go:54` (a lock-holder diagnostic). Both low
  consequence. `record/outcomes.go:75` and `pack/import.go:1075` are
  `locked_rmw` equivalents and are CORRECT as raw byte reads — Python's
  `locked_rmw` uses `errors="surrogateescape"`, not `read_text`. That
  distinction is the same classification discipline the graduation section
  above argues for, arrived at independently.
* **L6.** Number literals reach the `imported` block raw on the import side
  (`row["confirmations"]`, `row["contradictions"]`, `row["tier"]`,
  `original_provenance`). Measured UNREACHABLE through either exporter, and
  the residual note lives only in `PyNumbers`' doc rather than at these
  sites.
* **L4.** `parseTieredLesson` leaves `Imported` nil when `imported` is
  absent, which renders as `null` where Python's `default_factory=dict`
  gives `{}`. Unreachable today — both appending callers set the field
  explicitly. A note now sits at the site; the one-line fix would be an
  unfireable guard until a caller re-emits a loaded lesson.
* A non-dict JSONL row makes CPython raise `AttributeError` on the next
  `.get()` and abort the WHOLE import (only `json.JSONDecodeError` is
  caught). `LoadsMap` refuses the row instead, costing one row. A narrower,
  deliberate divergence, named at the site.

## The rewritten row comes back reordered

The evolver's five read-modify-write callbacks are where the port WRITES to
a store the Python runtime also writes to. That makes them the one place a
divergence is not a Go-side bug but a shared-artifact bug, and the chunk
found four separate classes of it in the same twenty lines.

### The finding the differential was not written for

The chunk started as a split-rule sweep — five Python merges spelled
`old.splitlines()`, and the port spelled `strings.Split(old, "\n")`, which
matters because *a rewrite that splits differently writes a different file*.
Two of the five (`_drop_constraint`, `_mark_reverted`) also neither strip
nor skip blank lines, and one of those returns `"\n".join(out) + "\n"`
UNCONDITIONALLY, so an empty store comes back as one newline where its four
siblings return "". All of that was real and all of it is now pinned.

None of it was the biggest thing in the file.

**Every rewrite re-emitted every key in alphabetical order.** Python's
`json.loads` gives an insertion-ordered dict; assigning a key leaves it
where it was and a new key lands at the tail, so `json.dumps` writes the row
back in the order the FILE had. The port read into `map[string]any`, whose
key order does not exist, and re-emitted through `pyval.FromPlain` — whose
own doc comment names sorted output as a LOSS and says the two pack.json
writers are the sites where the loss is live. It was live at five more.

Nothing about JSON semantics changes, which is exactly why nothing caught
it: both runtimes parse either byte sequence to the same mapping, so no
consumer ever failed. What changes is the bytes, and the bytes are the
shared artifact — a ledger diff shows every stamped row rewritten end to
end, a content hash over the store disagrees across runtimes, and a dedup
keyed on the serialized line stops matching.

`record.LoadsCleanOrdered` and `pyval.Obj.Set` already existed for exactly
this, with doc comments explaining exactly this. The machinery was there;
the callers had not been pointed at it. New this chunk:
`record.ReadAllAnnouncedOrdered` / `ReadAllCountedOrdered`, so a row that
arrives from the announced reader can survive a round trip too.

### Why the byte comparison had to be a byte comparison

`pyRewriteSrc` reads both stores with `read_bytes().decode("latin-1")` and
compares the raw bytes. Parsing first would have hidden every single
finding in this chunk: a dropped blank line, a trimmed preserved row, a
moved line boundary, a one-byte empty-store result, and the key order. The
two masks that survive (`<TS>` for ISO stamps, `<EPOCH>` for `added_at`)
each hide a WALL CLOCK, and each hands its lost coverage to a named
replacement rather than surrendering it — `TestNowISOMatchesPythonIsoformat`
for the format, and a precision assertion inside the apply test for the
epoch.

### Four more, found because the fixture was byte-exact

**The timestamp was a second spelling of a helper that exists.**
`nowISO()` in `evolver/store.go` rendered `time.RFC3339Nano`; all four
Python sites are `datetime.now(timezone.utc).isoformat()`. Three
differences: `"Z"` versus `"+00:00"`, nine trailing-zero-trimmed fractional
digits versus exactly six, and isoformat's omission of the fraction
entirely when it is zero. Measured on this box: CPython 3.14's
`fromisoformat` parses the Go spelling (truncating), but it rejected a "Z"
suffix outright before 3.11 — so a Go-written stamp is one an older reader
cannot parse at all. `pyval.NowISO` is the port-wide spelling and predates
the local copy. **Five more local `nowISO()` copies and four inline
RFC3339Nano stamps are still to classify** (record, graduation, scans,
skills/stats, pack/export; inspector ×4, orch/mission_run ×2) — each needs
its own Python read before it is touched, for the reason the graduation
section gives.

**`added_at` truncated to whole seconds.** Python's `time.time()` is a
float with microsecond precision; `float64(time.Now().Unix())` wrote
`1787635032.0` against CPython's `1787635032.8502514`. The TTL comparison
tolerates it, so nothing broke — the row just carried less than it claimed,
and two guardrails applied in the same second became indistinguishable by
time.

**`str(None)` is `"None"`, again.** The dedup content key is
`(str(d.get("category","")), str(d.get("target","")),
str(d.get("suggestion","")).strip())`. `d.get(k, "")` defaults on ABSENCE,
never on a present null, so a row carrying `"category": null` keys as
`"None"` in Python — and the port keyed it `""`, because a Go map lookup
gives nil for both cases. The r5 review found the identical collapse at
three sites in the pack importer one chunk ago. The fix is the same shape:
split the presence rule (`pyStrGetV`) from the coercion (`pyStrValue`) so
neither can be applied without the other. There are now FOUR str()-shaped
helpers in this file and a comment listing what separates them.

**A bug this chunk introduced, caught by a test that already existed.**
Swapping `json.Unmarshal` for `record.LoadsClean` at the merge sites was
correct — `loads_clean` is what the Python spells, and a byte-tainted line
must never id-match. But `LoadsClean` decodes with `UseNumber`, so every
number in a decoded row changed type from `float64` to `json.Number`, and
one site read a number: `row["verify_extensions"].(float64)`. The
assertion silently failed, `cur` was always 0, every bump wrote 1, and the
extension ladder could never reach its park. `TestBumpExtensionOrParkAtomic`
went red and named it. *A fix is evidence about its siblings* — including
the siblings that only exist because of the fix.

### The vacuity the battery found

`TestRevertRewritesBothStoresLikeCPython` passed on its first run and was
testing nothing. The fixture had no `change_log.jsonl`, so both runtimes
took the "not found in change_log" exit, left both stores untouched, and
the comparison held two unmodified files against each other. Only the
mutation battery saw it — M3 and M13 both reported MISS against a green
test. Seeding a matching `guardrail_append` entry made both mutants
DETECTED.

The same battery pass reported MISS for the two Apply-side mutants, for
the plainer reason that nothing drove `Apply` at all;
`TestApplyRewritesTheStoreLikeCPython` covers it now, and its fixture row
carries a real regex `pattern` so the new_guardrail branch performs a real
apply instead of falling through to guidance-only — otherwise the appended
constraint row the test exists to compare never gets written.

### Evidence

New: `evolver/rewrite_diff_test.go` — four store-BYTE differentials
(dismiss, stamp_verification, revert, apply), the `pyJoinAlways`/
`pyJoinOrEmpty` empty-store pair, a nine-case `rmwLines` differential, a
five-row content-key differential, and the isoformat format differential.
`record/ordered_reader_test.go` — the ordered and plain readers must
classify a 17-line corpus identically (all three loss buckets exercised,
asserted), and an ordered row must re-emit its own line byte for byte with
Set keeping an existing key's ordinal and appending a new one.

Mutation battery **17/17 DETECTED**: five key-order reversions (one per
rewrite site), three coercion reversions, two stamp reversions, three
split/join reversions, the number-type reversion, the two-readers-drift
mutant, an `Obj.Set` that appends instead of replacing, and the epoch
truncation.

`go test ./...` exits 0; `go vet ./...` clean; the only gofmt-dirty files
are the two pre-existing ones.

### Carried

* `evolver/store.go`'s `CadenceTick` still reads
  `state["runs_since_evolve"].(float64)`, which is CORRECT — that path
  decodes with `json.Unmarshal`, not `LoadsClean`. It is the one surviving
  `.(float64)` in the package and it is right for a reason that is invisible
  at the site; if that read ever moves onto the clean decoder it changes
  type with it.
* `constraintRowExists` splits on `"\n"` and has no Python counterpart at
  all (it is a Go-only guard for the crash window between writing a
  constraint row and stamping `applied`). The split rule is unobservable
  there because it never re-emits. Classified, not changed.
* `before_state` inside a change_log entry is still built from a Go map and
  comes out key-sorted. Nothing joins on it — it is read whole for recovery
  — so the loss stays named rather than chased.

## Nine spellings of one timestamp, and the census that found the other six

The previous chunk fixed the evolver's `nowISO`, which rendered
`time.RFC3339Nano` where Python writes
`datetime.now(timezone.utc).isoformat()`, and its PORT.md section named
"five more local copies plus four inline RFC3339Nano stamps still to
classify". That list came from `grep -rn "func nowISO\|RFC3339Nano"`.

The list was wrong. Not incomplete — wrong in a way grep could not show.

### Why this got a census instead of a sweep

The defect here is not any one site. Each local copy looked locally
reasonable, and four of the nine were within one character of correct. The
defect was the COUNT: nine independent copies of one decision, drifting
apart, none of them wrong enough to fail anything.

That is not a shape a per-site test can hold. So the invariant is stated as
a census — `pyval.NowISO` is the only place in the port a timestamp layout
is written — and `TestNoPackageSpellsItsOwnTimestamp` walks `internal/` and
fails on the tenth.

It found ten sites the grep had missed, in `loop/`, `runs/`, `orch/`,
`playbook/`, `record/`, `skills/` and `tasks/`. Six of those turned out to
be CORRECT and are exempted with the Python spelling that justifies each —
lens 20 in its clearest form:

* `orch_items.now_utc_iso()` really is
  `time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())`. Second precision, a
  literal Z, deliberately unlike isoformat.
* `task_store.utc_now()` is
  `.replace(microsecond=0).isoformat().replace("+00:00", "Z")` — isoformat
  post-processed into that same shape on purpose.
* `skills.py` mints a variant NAME with `%Y%m%dT%H%M%S%fZ`, and several
  sites render a day key or a filename slug.

The spelling that is wrong three files over is right at these. An exemption
carries the Python line that justifies it, so the map is a record of
classification rather than a list of things someone silenced; and one entry
is marked UNCLASSIFIED (`orch/pids.go`) rather than being quietly exempted,
because a list that hides its own gaps is worse than no list.

Two anti-vacuity halves guard the census itself: the regex must still match
both writer shapes, and must NOT match the read side — a parser
legitimately names `RFC3339Nano`, because a shared store holds stamps
written by both runtimes and has to accept either.

### What the census found that was not a timestamp

`loop/slot.go` writes the per-project lock file `interrupt.py` also writes,
and the payload had drifted FOUR ways. The census was looking at the last
of them:

* the stamp was keyed `"started"`. Python writes `"started_at"`, and
  `observe.py` renders `loop.get("started_at", "")` — so a Go-held loop
  showed `started ?` in `maro-observe loop`, which is the one field an
  operator reads to tell a live run from a wedged one.
* it rendered `RFC3339` (second precision, "Z") where Python writes
  isoformat; `observe.py` feeds the value to `_age()`.
* `"project"` was absent entirely.
* the goal went in whole where Python writes `goal[:120]`.

And the existing test pinned the wrong key. `TestSlotHolderMetadataIsJSONDumps`
asserted `"started": "` — the port's own spelling — so it had agreed with
the bug for as long as the bug existed. **A test can only pin the spelling
it was told, and this one was told the port's.** It is now told CPython's:
`TestSlotPayloadMatchesInterruptPy` builds the payload on the Python side
and compares keys, order, clip and stamp shape.

That test also fixed a second-order gap the old fixture had: its goal was
34 characters, so the 120-character clip could never fire. The new one
passes 200 multi-byte runes, which pins both that the clip happens and that
it counts CODE POINTS — a byte slice would cut at 40 characters and split
the last rune.

### Evidence

`pyval/nowiso_census_test.go` (the census + its two anti-vacuity halves),
`loop/r8_diff_test.go` (`TestSlotPayloadMatchesInterruptPy`, plus the
corrected substring list). The isoformat FORMAT differential lives in
`evolver/rewrite_diff_test.go` from the previous chunk and is what the
census delegates the "is NowISO itself right?" question to — census for the
count, differential for the format.

Mutation battery **11/11 DETECTED**: a tenth copy appearing, an exemption
removed, the census regex broken to match nothing, the census regex widened
to catch the read side, the slot key name, the missing clip, a BYTE clip
instead of a rune clip, the missing project field, the slot key order, and
both halves of NowISO's rendering.

`go test ./...` exits 0; `go vet ./...` clean.

### Carried

* `orch/pids.go` renders `"2006-01-02T15:04:05-0700"` — an offset with no
  colon, which is neither isoformat nor `now_utc_iso`. Listed UNCLASSIFIED
  in the census; its Python has not been read.
* The census is a Go-side invariant, not a parity one. It cannot see a site
  that formats a time through some other route (a helper taking a layout
  parameter, say). It pins the shape the port keeps regressing into, not
  every shape it could.

## The r1 review of the rewrite chunk — ten findings, ten verified

Whole-chunk review of `90c60e77`: 1 HIGH, 3 MEDIUM, 6 LOW. Every code claim
was checked at source before anything was touched, and all ten held. That
is two rounds running well above this project's historical ~50–70%, and for
the same reason both times: the reviewer measured on both sides of every
claim rather than reading, and reported four findings as *surviving
mutants* rather than as arguments.

### The HIGH: a shared guard list is not a shared decoder

`LoadsCleanOrdered`'s doc said "same admission predicate, same four
refusals — the guards are literally the same calls", and
`ReadAllCountedOrdered`'s said the bucketing is "measured and pinned". Both
were true about the guards and false about the parse underneath.

`LoadsClean` decodes with `json.Decoder.Decode`, which enforces
`encoding/json`'s 10000-deep nesting limit inside the scanner.
`LoadsCleanOrdered` goes through `pyval.LoadsOrdered`, a hand-rolled
recursive `Token()` walk — and `Token()` does not drive that check. So the
ordered reader admitted a document the plain one refuses, and then
**recursed on it**. Measured: depth 10001 refused by one and admitted by
the other; depth 3000000 not refused at all but `fatal error: stack
overflow` — unrecoverable, no strand, no announcement, the process gone.
`refuseDuplicateNames` in the same file carries a doc comment explaining
that it was rewritten iteratively to avoid exactly this.

The in-package consequence is concrete and needs no adversary: `Apply`'s
keyed merge parses with `LoadsClean` and `_mark_reverted` parses with
`LoadsCleanOrdered`. A deeply-nested row is invisible to `Apply` — so
`replaced` stays false and it **appends a second row for the same id** —
while `Revert` parses and rewrites it. CPython admits it in both.

A 56-shape sweep through both classifiers disagreed on exactly two lines,
both nesting depth. So the rest of the parity claim was verified clean and
depth was the whole gap. `pyval` now bounds the ordered walk at the same
10000, and both sides of that boundary are fixtures in `readerCorpus`.

The corpus is the real lesson. `TestOrderedReaderClassifiesLikeThePlainOne`
was written to pin this exact property and could not see the one case that
broke it, because no deep line was in it. **A claim of parity is only as
wide as the inputs behind it** — the detector was agreeing, not measuring.
Both doc comments now say what is actually shared and what is not.

### The MEDIUMs

**The guardrail pattern was wrong on both halves.** Python is
`str(d.get("pattern", "") or "").strip()` — `pyStrKey` plus `pytext.Strip`.
The port spelled it `strings.TrimSpace(stringOr(d["pattern"]))`: a bare
type assertion, so a numeric or boolean pattern became `""`, and a strip
that does not know U+001C–U+001F. Measured through a real `Apply` on both
runtimes: `"pattern": 5` makes CPython write a constraint row with `"5"`
and made this runtime write none; a separator-wrapped pattern kept its
separators here and lost them there. **In every case both runtimes stamp
the row `"applied": true`** — so the port recorded an applied guardrail
with no constraint row, or with a regex that can never match step text.
That is the 2026-08-04 "guardrails that could never fire" failure,
re-created, in a file `constraint.py` reads.

`pyStrValue`'s own comment, seven lines away, says `stringOr` "survives
only at sites whose Python really does compare against a string". This
site coerces. The quartet was documented and then not applied.

**`Apply` deleted `block_reason`; CPython keeps it.** All five Python
success arms are `d.pop("status", None)` and nothing else. A row held once
and applied later keeps its stale `block_reason` there and lost it here —
no exotic character, no concurrency, the most ordinary path in the file.
Silently tidier is the one option this chunk's own premise rules out.

**The `<EPOCH>` mask hid the unit, not just the wall clock.** The previous
section claimed both masks "hand their lost coverage to a named
replacement". For `added_at` that was false: the replacement asserted only
"has a fractional part and does not end `.0`", which a millisecond count
satisfies. Changing `UnixNano()/1e9` to `/1e6` survived the entire suite —
in the field whose comment three lines up says an ISO string there once
"silently discarded the whole lane". The fix is an absolute tolerance
before masking: two runs microseconds apart cannot differ by a second, and
a thousandfold unit error is 1.7e12 out.

### The LOWs, and the two that were about tests

**`pyStrKey` lost its number arm to the same decoder swap.** This is the
sibling the `verify_extensions` bug should have prompted a sweep for and
did not. Its only call site reads off an ordered row, so numbers arrive as
`json.Number` — an arm it did not have — and fell to `default: return ""`.
The paragraph above it spent six lines describing how a stored `5` renders,
an arm the production path could no longer reach.

**`pyStrValue`'s `json.Number` arm returned the source literal.** `String()`
is the file's text; Python's `str()` runs on the *decoded* float. So
`1.50` stayed `"1.50"` against CPython's `"1.5"`, `1e5` stayed `"1e5"`
against `"100000.0"`, and `1e400` stayed `"1e400"` against `"inf"` — each
one a content-key mismatch, and a content-key mismatch is a missed dedup.
`pyNumStr` now routes only literals carrying `.`, `e` or `E` through
`FloatRepr`; an integer literal keeps its text, because `str(int)` is exact
at any width and a 22-digit integer through float64 would round.

`TestContentKeyCoercesLikePython` could not see any of it: its rows were Go
literals, not values that came through the decoder. Both new tests feed
their fixtures through `record.LoadsClean` first — and the test written to
answer this finding hit the identical flaw on its first run, failing on
`{"pattern": 5}` because a Go `int` is not a type any store can contain.

**`<TS>` also hid the UTC offset.** The format check guarded against a `"Z"`
suffix; `-06:00` is neither Z nor a match failure, so dropping `.UTC()`
survived the suite. It now demands `+00:00`.

**The join test tested the helpers, not the site.** Swapping `pyJoinAlways`
for `pyJoinOrEmpty` at the one line that needs the asymmetry survived
everything, because `Revert` reaches `_mark_reverted` only past an
`IsApplied` check that reads the same file — an empty store exits early. The
merge is now `markRevertedMerge`, driven directly, with the empty and
blank-only cases **measured against CPython** rather than hand-computed
(the first draft of that assertion was wrong by arithmetic).

**A doc comment cited a Python line that does not exist.** The comment read
`int(row.get("verify_extensions", 0) or 0) in Python`; the real read is
`evolver_scans.py:1478`, `int(getattr(s, "verify_extensions", 0)) + 1`, off
a dataclass attribute — and the whole in-lock bump has no Python
counterpart at all. In a file whose discipline is quoting the line being
ported, an invented citation is worse than none: it is what the next reader
trusts instead of checking.

**`SaveSuggestions` was the one `_read_store` site still dropping the
announcement.** Its comment says the announced warning "is what tells an
operator the corpus went short" — and then emitted nothing. It cannot call
the reader helper, because the scan happens inside `LockedRMW` on purpose
(a `seen` set built outside the lock reopens the 81-duplicate bug), so it
now counts into a `record.SkipReport` and borrows its sentence.

### Verified clean

The reviewer also checked and cleared: every `objMap` caller (two, both
read-only); every `pyval.Obj` passed by value and then mutated (none);
`Pop` versus `Set(k, nil)` in the applied arm; every remaining
`.(float64)`/`.(string)`/`.(bool)` in the package; all four
`readRowsAnnounced` callers under the new `json.Number`;
`ReadAllCounted`'s `strings.Split` (correct — `jsonl_utils._read` opens
binary); the five `what` labels; `locked_append`'s torn-tail framing; and
`before_state`'s accepted loss, which turns out to be currently zero-sized
because both ported categories build a one-key map.

### Evidence

Mutation battery **12/12 DETECTED** across the fixes, including both
directions of the depth bound (removed, and set too low so a legal 9999 is
refused), both halves of the pattern coercion, the unit slip behind the
epoch mask, the local-time stamp behind the TS mask, and the wrong join at
the site.

`go test ./...` exits 0; `go vet ./...` clean.

## Round 2 on the rewrite chunk — the fix that was wrong, and the file that knew

Seven findings, **seven verified**, measured on both sides before
anything was touched. That is three consecutive rounds well above this
project's historical 50–70% verification rate, and the reason is the
same each time: the reviewer measured rather than read, and reported
several findings as surviving mutants rather than as readings of the
source.

This was also the first round run under the standing whole-chunk rule —
the entire chunk plus its round-1 fixes, not the latest diff. The
round's HIGH is the argument for that rule.

### The r1 fix was wrong, and the r1 test could not tell

Round 1 found that `decodeOrderedAt` had no depth bound where
`encoding/json` has one, so a deeply nested row that `LoadsClean`
refused was admitted by `LoadsCleanOrdered`. The fix added a bound at
10000, the corpus gained `nest(9999)` and `nest(10001)`, the promise
test went green, and the finding was closed.

The readers still disagreed.

`encoding/json`'s scanner bounds **open containers** at 10000.
`decodeOrderedAt` bounded the **depth parameter of any token**, checked
before the token was read. For a document whose outermost value is a
container holding a chain — `{"a":[[[…]]]}`, which is exactly the
corpus's own `nest(n)` shape and therefore n+1 containers — that is one
too many:

```
obj+arrchain n=9999    plain_ok=true   ordered_ok=true
obj+arrchain n=10000   plain_ok=false  ordered_ok=true    <-- disagree
obj+arrchain n=10001   plain_ok=false  ordered_ok=false
obj chain    n=10000   plain_ok=true   ordered_ok=true     (n containers; agrees)
```

The corpus had picked the boundary's NEIGHBOURS — one step outside on
each side — and the one value that could show the defect sat between
them. **A limit's neighbours are not its boundary.** The r1 round's own
lens said "a limit with no case at its own boundary is a limit nothing
pins", and the fix satisfied the letter of it.

The bound moved into the `case json.Delim` arm, where it counts the
container being opened:

```go
case json.Delim:
    // depth is the number of ENCLOSING containers, so the one being
    // opened now is the (depth+1)-th. A scalar opens no container, so
    // it needs no check.
    if depth+1 > maxOrderedDepth {
        return nil, fmt.Errorf("exceeded max depth")
    }
```

Re-measured across obj+arrchain, obj-chain and mixed shapes at n ∈ {2,
9998, 9999, 10000, 10001, 10002}: zero disagreements. The corpus now
carries four depth rows and a new `objChain(n)` helper so both container
shapes are pinned at their own boundaries.

The live consequence was not theoretical. `Apply` reads its snapshot
with `readRowsOrdered` and merges keyed with `record.LoadsClean`. A row
at exactly this depth was rewritten by one reader and invisible to the
other, so `replaced` stayed false, a SECOND row for the same
`suggestion_id` was appended, and `IsApplied` then answered false for a
suggestion that had just been applied. CPython admits the row on both
sides and keeps one.

Nothing in the r1 diff looked wrong. This is the strongest evidence the
port has produced for reviewing the whole chunk rather than the latest
change.

### The knowledge was written down and not applied

`SaveSuggestions`' dedup scan read the previous file with
`rmwLines` + `pytext.Strip` + `LoadsClean`, and counted its own
`Malformed`. Python reads it through `read_jsonl_announced`.

My own round-1 comment, in the same file, said `_save_suggestions`
"reads through read_jsonl_announced". The loop below it was spelled like
the four sibling merges anyway. **An idiom is not a defect — the defect
is a spelling that does not match the spelling at its own site**, and
here the site had already been named, in writing, one round earlier.

Three axes diverged, not one:

- **strip** reverses the dedup for a row whose content key begins with
  U+001F: Python's `strip()` removes it, so the two runtimes compute
  different keys and one deduplicates where the other appends.
- **split** reverses it for U+001C: `read_jsonl_announced` opens
  BINARY and breaks on `b"\n"` only, so a row containing U+001C is one
  line to Python and two to a `SplitLines` reader.
- **buckets**: the local loop collapsed three distinct CPython answers
  into "malformed" and silently skipped whitespace-only lines.

The fix extracts the two halves that were being re-implemented:

```go
func SplitStoredLines(raw string) []string { return strings.Split(raw, "\n") }

func CountLine(line string, rep *SkipReport) map[string]any
```

and `ReadAllCounted` is rewritten on top of them, so the shared path is
the one both callers walk. This is the THIRD Python line-splitting rule
the port has had to name, and the table now reads:

| Python spelling | Breaks on | Go equivalent |
|---|---|---|
| `read_text(...).splitlines()` | ten separators | `pytext.SplitLines` |
| `path.open("rb")` then `for line in f` | `b"\n"` only | `record.SplitStoredLines` |
| `path.open()` (text) then `for line in f` | `\n`, `\r`, `\r\n` | neither — still unwritten |

### A typed nil is not a nil

`changeLogAppend` took `beforeState map[string]any`. For category
`observation` there is no before-state, and the call site passed a nil
map — which is a TYPED nil, matches `FromPlain`'s `case map[string]any`,
and renders `{}`. CPython writes `null`.

The parameter is now `any` and the call site declares `var beforeState
any`. **A zero value that must mean two things means neither**, in its
Go-specific spelling: the interface distinguishes "absent" from "empty
map" and the concrete type cannot.

### Comments that assert coverage, and decay

Four findings were documentation, and all four were the same failure:
a paragraph that had been pasted forward and no longer described its own
site. One claimed to be "the FIFTH identical copy" of a timestamp
spelling in five different files, where the true count is four; one in
`inspector.go` described a class the file is not in (its four
`RFC3339Nano` calls are the louder defect — wrong offset AND wrong
width); one in `stats.go` was a stale stacked claim about always writing
six digits; and `pyStrKey`'s `float64` arm was described as a live
sibling when no production caller supplies a float64, and a float64 has
already lost the int/float distinction so `5` would render `"5.0"` where
CPython gives `"5"`.

None of these change behaviour. All of them are load-bearing for the
next reader, which is why they are findings rather than nits — **a
comment that asserts coverage is a claim, and it decays.**

### The census caught the new event type

`SKILL_REWRITE` reached the audience census as an undeclared event the
moment `rewrite.go` landed, which is what that tripwire is for. Its lane
was read off Python's LIVE `USER_SURFACED_EVENTS` frozenset rather than
inferred from its neighbours — worth doing, because the event type was
registered and consumed for months before anything emitted it, so its
membership is older than its first row.

## The skill-rewrite tranche — the circuit's second chance

`skill_lifecycle.rewrite_skill` is what happens to a skill whose circuit
has tripped. Rather than deleting it, the evolver asks the model to
rewrite the skill against its own failure notes and two high-scoring
peers, and puts the result on probation (`half_open`). It has two lanes
that share every line except their last twenty:

- **in-place** (`in_place=True`): reload the pool, mutate the row, save,
  emit `SKILL_REWRITE`, return the fresh row.
- **frontier** (`in_place=False`, the default): mint a CHALLENGER with a
  new id, clear its lineage, and return it **without saving anything**.
  The caller stamps `variant_of` and persists under
  `SKILL_VARIANT_CREATED`.

The lane is a bare boolean with no safe default, which is why
`RewriteOptions.InPlace` is documented as having no meaningful zero
value: `false` is the frontier lane, and a caller who forgets the field
gets a challenger nobody saves rather than a repair nobody made.

Alongside it, `skills.validate_skill_for_promotion` — the LLM quality
gate a provisional skill passes on its way to established — and the
repair loop in `maybe_auto_promote_skills` that ties the two together.

### `extract_json` returns `{}`, and that decides the whole gate

The validation gate is fail-open: if it cannot run, the skill passes.
The obvious port of that is "any failure → pass", and it is wrong by a
wide margin.

`llm_parse.extract_json` answers `{}` on a parse failure, not `None`. So
in

```python
parsed = extract_json(resp.content)
if isinstance(parsed, dict):
    return {"valid": bool(parsed.get("valid", False)), ...}
```

a garbled reply is a dict, `isinstance` succeeds, `{}.get("valid",
False)` is `False`, and the skill FAILS validation and goes to the
repair loop. Fail-open is reached only from the `except` — an adapter
that raised, or a `None` adapter whose `.complete` is an
`AttributeError`.

Go's `jsonx.Object` reports `(nil, err)` where Python reports `{}`, and
a nil map reads exactly like an empty dict. So the port discards the
error on purpose:

```go
parsed, _ := jsonx.Object(llm.ContentOrEmpty(resp))
```

Mapping that error to `failOpen` would promote every skill whose
validation the model fumbled — silently, at the exact moment the gate is
supposed to be doing its job. That is mutant M28 in the battery, and it
is detected by a fixture whose only distinguishing feature is a reply
that is not JSON.

`valid` is read through `pyval.Truthy`, not a bool assertion:
`{"valid": "no"}` passes and `{"valid": 0}` does not, because
`bool(parsed.get("valid", False))` is Python truthiness over whatever
the model sent.

### Two parse paths, deliberately different

`validate_skill_for_promotion` goes through `extract_json`, which strips
fences and carves JSON out of surrounding prose. `rewrite_skill` does
neither — it hand-rolls a fence strip and calls `json.loads` directly,
so a reply with leading prose is fatal there and survivable in the gate.

The port keeps both: `jsonx.Object` for the first, a local
`stripRewriteFence` plus `pyval.LoadsOrdered` for the second. Collapsing
them onto one reader would change which model replies each function
accepts, in opposite directions.

### A guard that could not fail, hidden by a guard that could not fail

`stripRewriteFence` begins with a strip, because Python's is
`resp.content.strip()`. The port composed it as

```go
raw := stripRewriteFence(llm.ContentOrEmpty(resp))
```

and `ContentOrEmpty`'s entire body is `pytext.Strip(r.Content)`. So the
strip inside `stripRewriteFence` was a dead second copy. Two fixtures
existed specifically to pin it — a separator-led reply and a
separator-trailed one, using U+001C–U+001F, which Python's `strip()`
removes and Go's `strings.TrimSpace` does not — and BOTH passed with the
strip reverted to `TrimSpace`. The test that was written to catch the
divergence could not see it, because the upstream helper had already
made the divergence unreachable.

The fix is to route the raw content in, so the one strip that happens is
the one at the site that performs it:

```go
raw := stripRewriteFence(resp.Content)
```

With that, the same mutant fails both fixtures. **A duplicated guard is
what makes a wrong one unobservable** — the same lesson the depth-bound
round produced, arriving here from the opposite direction: there, two
readers disagreed and a duplicated bound hid it; here, two strips agreed
and the duplication hid that only one was load-bearing.

### The repair loop spends a rewrite nothing checks

Inside `maybe_auto_promote_skills`, a candidate that fails validation is
rewritten in place and re-validated, up to `max_repair_attempts` times.
The rewrite sits at the BOTTOM of the loop body, so on the last attempt
the skill is rewritten and the loop exits without validating the result.
That is a real call to a real model whose output nothing reads.

The port reproduces it rather than optimising it away, because the
cheaper shape agrees on every promoted id and differs only in spend —
exactly the class of divergence a differential over return values cannot
see. `TestPromotionHarnessMatchesCPython` therefore compares the ADAPTER
CALL SEQUENCE, not just the outcome.

Two further behaviours, both documented and both pinned:

- `max_repair_attempts <= 0` with an adapter present promotes NOTHING.
  The loop body never runs, `valid` stays false, and every candidate is
  held. It is not clamped to one.
- A rewrite that returns `None` and a rewrite that RAISES both break the
  loop identically — Python's bare `except` around the call.

### The purposes agreed; the questions did not

The harness test originally compared adapter calls by their `purpose`
string. That is enough to count calls and to see their order, and it is
blind to the one thing the loop is FOR: dropping the rewrite's return
value and re-validating the ORIGINAL skill produces the identical
sequence of purposes, the identical count, and the identical promoted
ids (mutant M38, a MISS on the first battery).

The test now compares the full message contents of every call, and the
fixture makes the repaired description markedly different from the
original so the second validation prompt has to change. M38 is detected.

The sibling gap was `validation: "unjudged"` — the word the captain's
log stores when a promotion passed on a verdict nobody gave. No fixture
reached it, because reaching it requires the validation call itself to
FAIL mid-sweep, and the scripted stub could only return replies. A
`__RAISE__` sentinel in the script (honoured by the CPython stub and by
a `sentinelAdapter` wrapping `llm.Fake` on the Go side) makes the failure
happen on the call it means, and collapsing `unjudged` into `passed`
became detectable too.

### The bug the differential found on its first run

`pyIterate` — the port's `for x in v` — had arms for `[]any` and for
`string`, and none for `[]string`. Python needs no such distinction, and
neither did any earlier caller, because everything reaching it came from
a decoder that produces `[]any`.

`rewrite_skill` breaks that: `parsed.get("steps_template",
skill.steps_template)` falls back to the SKILL's own field, which in Go
is a `[]string`. On the first run of the new differential, a reply that
omitted `steps_template` raised `'list' object is not iterable` in Go
and rewrote the skill fine in Python.

Third spelling of "list", found by the third caller. The arm is added
with a comment naming why it exists.

### Evidence

Mutation battery over the tranche, derived from the FILE: 44 mutants,
all DETECTED. The corrections that got it there are worth naming,
because three of the first run's eight failures were faults in the
BATTERY rather than gaps in the tests:

- **M11** (`pyStrip` → `TrimSpace`) was a genuine gap, described above.
- **M32** (`len(steps) > 6` → `> 5`) is EQUIVALENT: `steps[:6]` on
  exactly six elements is the identity, so no input separates the
  spellings. Replaced with a WIDTH mutant (`steps[:6]` → `steps[:5]`),
  plus the same on the trigger side.
- **M25** bolted a `SaveSkills` call with an empty updated-id set onto
  the frontier lane, which rewrites the file to the same bytes — a
  mutant that cannot change anything a test can read. Replaced with
  `if !opts.InPlace` → `if false`, which is the pre-2026-08-06 bug's
  actual shape: the frontier lane falling through and persisting.
- **M3**, **M21**, **M31** were real fixture gaps, all masked the same
  way: the discriminating value was never the one that mattered. The
  `0.5` peer was outranked by three better peers in a prompt cut at two;
  every fixture had `variant_of` absent, so clearing it and copying it
  both produced nil; and a seven-trigger fixture cannot see a `>5` vs
  `>6` threshold because both slice to five. Fixtures added at the
  boundary in each case.
- **M14** was a SITEFAIL: `getOr` had moved to `mint.go`, which the
  battery's file list did not cover.

`go test ./...` exits 0; `go vet ./...` clean.

### The census that read a sentence

Fixing the four decayed comments broke the timestamp census. Rewriting
`inspector.go`'s doc comment to describe the four
`time.Now().UTC().Format(time.RFC3339Nano)` calls it replaced meant the
file now NAMED that layout in prose, and the census — which regexed raw
bytes — reported the sentence as a tenth offender. Nothing about the
timestamps had changed.

The wrong fix is to reword the sentence. The census's subject is the
code, so it now parses the file and blanks every comment span before
matching:

```go
func stripComments(path string, src []byte) (string, error)
```

The embarrassing direction is the one that fired. The other direction is
worse and would have been silent: a site could be exempted by prose that
happens to match the regex, or a real writer argued away on the strength
of a comment near it.

New machinery between the file and the detector is new vacuity risk, so
the test now also asserts a floor — the walk must find at least as many
layout writers as the allowlist claims exist. A stripper that blanked too
much would otherwise make the census pass by seeing nothing, which is the
failure its own regex probes were written against, one layer up.

## The finalize helpers, and a ContextVar with no Go spelling

`loop_finalize.py` is the loop's terminal phase — 1319 lines that build the
final `LoopResult`, write the artifacts, and run the post-loop side effects.
This tranche takes the self-contained head of it (`_step_evidence`,
`_auto_prune_days`, `cleanup_step_artifacts`) plus the dependency it turned
out to need: `runs.artifact_dir` and the run-dir accessors under it.

### `contextvars.ContextVar` is `context.Context`, and the difference is safe

`runs._current_run_dir` is a ContextVar, and the comment above the
declaration says exactly why rather than leaving it to be inferred:
concurrent loops in one process (`run_parallel_loops`, DAG step fan-out)
must each see their OWN run-dir instead of sharing a last-writer-wins
global.

A package-level `var currentRunDir string` would be that global, with the
same bug and a data race on top. Go has no goroutine-local storage, and that
is not a gap to work around — `context.Context` IS the mechanism, and it
matches ContextVar closely:

| ContextVar | context.Context |
|---|---|
| `set` inside a `copy_context()` is invisible to the caller | a value on a derived context is invisible to its parent |
| `scoped_run_dir`'s `reset(token)` | the derived context going out of scope |
| `copy_context().run(fn)` for a worker | handing the goroutine the derived context |

They differ on the FAN-OUT DEFAULT, and in the safe direction. Python's
ThreadPoolExecutor workers do NOT inherit the submitting thread's context;
the comment in `runs.py` warns that fan-out sites must remember
`copy_context().run`, and forgetting is silent. A Go goroutine has no
ambient context at all, so it can only see the run-dir a caller explicitly
handed it. There is nothing to forget.

### `Path("")` is `.`, and one Go string cannot hold that

`set_current_run_dir(None)` CLEARS — `handle_queue` uses it for drain-batch
hygiene between resumed handles, and without it run N+1 would write its
artifacts into run N's build dir.

`set_current_run_dir("")` does NOT clear. `Path("")` is `PosixPath('.')`, a
perfectly good truthy Path, so `current_run_dir()` answers `.` and
`artifact_dir` then creates `./build` under whatever the process CWD happens
to be. Measured:

```
>>> Path(""), Path("").name
(PosixPath('.'), '')
```

A `string` in the context could carry one of those two answers, not both, so
the stored value is a POINTER: nil is None, non-nil is a path, and the empty
string is normalised to `"."` at the one function that constructs it.
`WithoutRunDir` must store an explicit nil rather than simply declining to
set anything — an inner scope that set nothing would INHERIT the outer
run-dir, where Python's `set(None)` genuinely blanks it downstream. That is
mutant F29, and it is the one a reasonable person writes first.

`current_handle_id` carves the id out of the run-dir NAME
(`<handle_id>-<nickname>`) with `split("-", 1)[0]`, and the edge that
matters is `"-x"`, which yields an EMPTY handle id — not an absent one. So
the Go signature is `(string, bool)` rather than a bare string: folding ""
into "no run pinned" would make a caller resume the wrong run rather than
fail.

### `max(0.0, float(v or 0))` is four coercions and none are decorative

`_auto_prune_days` is one line:

```python
return max(0.0, float(_cfg_get("artifacts.auto_prune_days", 0) or 0))
```

`or 0` turns every FALSY value into 0 before `float()` sees it; `float()`
accepts numeric strings and strips their whitespace; a non-numeric string
RAISES into the outer `except`; and `max` clamps negatives — and clamps NaN
too, because `nan > 0.0` is false. Measured across what a hand-edited config
can hold:

```
0 -> 0    3 -> 3     3.5 -> 3.5   "3" -> 3    " 3 " -> 3
true -> 1 false -> 0 None -> 0    "" -> 0     [] -> 0
"abc" -> ValueError -> 0          "0x10" -> ValueError -> 0
"nan" -> 0 (clamped)   "inf" -> +Inf (NOT clamped)   "-1" -> 0
```

Infinity surviving is correct and worth naming: `auto_prune_days: inf`
disables pruning by putting the grace period past every mtime, rather than
by being clamped to the delete-everything end.

The Go gate is spelled `!(f > 0)`, not `f <= 0`, so NaN lands in the
disabled arm. NaN fails BOTH comparisons, and `f <= 0` would let it through
to be multiplied into a grace period no mtime is ever less than — which
silently disables pruning instead of clamping it, and would look identical
in any test that only asserts "nothing was deleted".

And the string arm: `strconv.ParseFloat` and CPython's `float()` agree on
every shape a config file can hold, including the pair that looked like a
divergence and is not — underscore grouping (`"1_000"` → 1000 in both,
`"_1"` and `"1__0"` errors in both). The ONE real difference is the HEX
FLOAT: Go parses `"0x10p0"` as 16 and CPython raises. No valid decimal float
string contains `0x`, so screening for it costs nothing and removes the only
case where one config value would mean two numbers.

### A pruning routine, where an extra deletion is the expensive direction

`cleanup_step_artifacts` is opt-in by decree (Jeremy, 2026-07-10: the system
never decides run data is clutter — *"the result isn't always just the
outcome, it's also the path that gets you there"*). The knob defaults to
zero, and zero means never.

Porting the deletion turned up one real divergence, found by measuring
rather than reading. `Path.glob` returns DIRECTORIES, and `unlink()` on one
raises `IsADirectoryError`, which the `except OSError: pass` swallows — so a
directory named `loop-1-step-1.md` survives every prune. Go's `os.Remove`
SUCCEEDS on an empty directory. Without an `IsDir` skip the port would
delete it and count it:

```
>>> (d/"loop-1-step-1.md").mkdir()
>>> [p.name for p in d.glob("loop-*-step-*.md")]
['loop-1-step-1.md']
>>> (d/"loop-1-step-1.md").unlink()
IsADirectoryError
```

The glob itself was measured at its edges rather than reasoned about, and
the surprise is which way it falls:

```
fnmatch("loop--step-.md",     "loop-*-step-*.md") -> True    # EMPTY id
fnmatch("loop-step-x.md",     "loop-*-step-*.md") -> False   # MISSING id
fnmatch("loop-a-step.md",     "loop-*-step-*.md") -> False
fnmatch("loop-x-step-y.md.md","loop-*-step-*.md") -> True
```

An empty loop id matches and a missing one does not. The rule is exactly
"the literals appear in order", which is what the Go matcher does.

The differential asserts the SURVIVORS, not the count. Two implementations
that delete the same NUMBER of files can delete different files — the
exclusion prefix and the glob are precisely where they would differ — and a
caller only finds out when an audit goes looking for an artifact that is
gone.

### The step index is a stored field, and the port did not have one

`_step_evidence` renders `Step {index}`, and Python's `StepOutcome.index` is
the 1-based PLAN ITEM index. Go's `StepOutcome` had no such field, so the
obvious port is the slice position.

They agree in the v0 loop, which appends one outcome per item in order. They
already disagree in the lanes this port has not reached: `loop_blocked`
passes `item_index` for a step that has just been RETRIED, so two outcomes
carry one index, and `loop_parallel`'s fan-out numbers its own batch from 1.
So `Index` is a stored field, assigned in the step loop from the same
`len(res.Steps)+1` the exec lane was already computing — for BOTH lanes, so
the tool-less path is not the one that renders "Step 0" into a lesson.

The other detail worth pinning is where the strip goes. Python builds

```python
f"Step {i} [{status}] {text}\n{result}".strip()
```

so the strip is on the JOINED string. A step with text and no result loses
its trailing newline; a step with a result and no text KEEPS the space after
the bracket:

```
'Step 1 [ok] txt'
'Step 1 [ok] \nres'
```

Stripping the parts and then joining produces neither. This is a prompt —
the text feeds lesson extraction — so the spacing is the behaviour.

### Two keyword defaults that a single Go signature cannot carry

`_step_evidence(step_outcomes, *, total_budget=None, entry_cap=None)`
forwards each keyword only when it was passed, so `None` means "ContextBudget's
own default" and NOT zero — and zero is a real value a caller could pass,
which renders an empty block. A `func(outcomes, total, cap int)` would make
0 ambiguous exactly where Python is unambiguous, so the port is two
functions: `StepEvidence` takes the defaults and `StepEvidenceBounded` takes
explicit numbers.

### Evidence, and five tests that were agreeing rather than measuring

Mutation battery over the tranche, derived from the FILE. The first run was
26 detected, 8 missed, 3 build-failed — and every one of the eight misses was
a fault in the TESTS or in the battery, not a gap that needed new production
code. They are worth listing because four of them are the same shape as the
findings the last review round produced, arriving one round later in freshly
written work.

**The reader was tested and the writer was not.** `StepOutcome.Index` is
rendered by `_step_evidence` and assigned by the loop. The differential
builds its outcomes as literals, so zeroing the assignment (F9) and making it
zero-based (F10) both passed every subtest — the test pinned how the number
RENDERS and said nothing about where it comes from. A field is two claims,
and a test of one is not a test of the other. `TestRunStampsOneBasedStepIndices`
drives the actual loop and checks both halves together.

**My own test duplicated a production guard, and hid it.** The pruning
differential entered through a local helper that re-checked `project == ""`
before delegating — so the mutant deleting the production check passed (F28).
That is precisely the finding the previous round's fence-strip produced, and
the paragraph about it was written the same day. The helper is gone; the test
now enters at `pruneStepArtifacts`, the two guards live in exactly one place
each, and a separate end-to-end test drives the exported entry point where
both short-circuits actually are.

**One temp directory served two runtimes, and the existence check became
free.** `TestArtifactDirMatchesCPython` gave Python and Go the same root.
Python ran first and created the directory, so the Go assertion that the
directory EXISTS passed whether or not `ArtifactDir` created anything —
mutants deleting both `MkdirAll` calls survived (F33, F34). The trees are now
separate and compared by their de-rooted tails. A test that shares a fixture
with the thing it is measuring against is measuring the fixture.

**A bool nothing read made two mutants unkillable.** `pyFloatOrZero` returned
`(float64, bool)`, separating "float() would have raised" from "the value was
falsy". Every failing arm returns 0 as well, so `!ok` was implied by the
caller's own `!(f > 0)` and both mutants that flipped the bool survived
(F15, F18). The right answer to that pair of misses was not a test — it was
deleting the bool. A distinction nothing reads is a second guard making the
first unobservable.

**A fixture at the wrong value.** The hex screen exists because Go's
`ParseFloat` accepts `"0x10p0"` and CPython does not. The fixture used
`"0x10"`, which BOTH runtimes reject, so removing the screen changed nothing
(F14). One character of exponent apart, and the test proved nothing.

**A no-prefix name that the "wrong order" fixture could not see.** Dropping
the `loop-` prefix requirement survived (F27) because the fixture meant to
catch it — `step-a-loop-b.md` — has no dash before its `step` either way, so
it is rejected by the `-step-` test whether or not the prefix is checked.
`x-step-y.md` is the name that separates them.

And one mutant that is **unobservable by construction**, recorded rather than
worked around: the age comparison is `<` and could be `<=`, but each runtime
reads its own clock after the fixture's mtimes are set, so no input can make
the difference come out exactly equal. It is named here instead of being
covered by a mutant nothing can detect. (The `<` itself IS pinned — a mutant
that zeroes the grace period fails two subtests.)

Two rounds of correction later, the battery reads **38/38 DETECTED** — and
of the ten mutants that survived along the way, two were answered by DELETING
production code rather than by adding a test, which is the right answer more
often than it feels like it should be.

---

## The post-notify maintenance tail, and a mutex that is not a design choice

`loop_finalize`'s deferred-maintenance registry and its two observation-form
mint texts. Small surface, three things worth recording.

**The registry stays in this module for a reason that has evaporated, and
that is fine.** Python's comment is a module-identity story: `handle.py` is
also a `python -m handle` entry point, so run that way the module executes as
`__main__` while an `import handle` elsewhere loads a SECOND copy with its
own registry — the registrar and the drainer would hold different dicts and
every deferred callable would strand. Go has no `__main__` twin and
initialises a package once per binary, so the hazard does not exist. The
PLACEMENT stays anyway: moving it would be a port that silently disagrees
with its source about where a piece of state lives, for a benefit nobody
asked for.

**The lock is not a design choice.** Python's dict is unsynchronised because
the GIL makes `setdefault`, `append` and `pop` atomic enough. A Go map is
not, and the two call sites sit on either side of a notify. The mutex is the
same safety Python was getting for free.

**The count is ATTEMPTED, not succeeded.** `drain_deferred_maintenance`
returns `len(fns)` after a loop that swallows every exception, so a drain of
three where two raise still answers 3. A port that counted successes would
agree on every fixture where nothing fails — which is every fixture anyone
writes first.

Two details the mutation battery forced. The `recover()` has to be
PER-CALLABLE: one around the loop abandons the remaining callables where
Python runs every one of them. And the pop happens under the lock while the
callables run OUTSIDE it, because a callable is entitled to register more
maintenance and Python cannot deadlock doing it.

That second one was found the hard way. The first version of the test HUNG
rather than failed, the battery timed out at ten minutes, and the timeout
killed the script before its restore step — leaving the mutant on disk. **A
deadlocked test is worse than a failing one.** The drain now runs on a
goroutine behind a five-second deadline, and the assertion is about the
deadline.

The battery reads 12/12 DETECTED, after three mutants were rewritten for
being equivalent: two spellings of M6 computed a count and returned
`len(fns)` anyway, and M7's first spelling added an OUTER recover while
keeping the inner ones. A mutant that cannot change an answer reads as MISS
and is not a test gap — it is a bad mutant.

---

## Round 3 on the skill-rewrite chunk, and the evolver's four private copies

Round 3 reviewed the whole chunk plus the r2 fixes, and reported eight
findings. It verified the r2 depth-bound re-fix by running both runtimes over
five container shapes at n ∈ {9997..10001} with zero disagreements, confirmed
the audience census against Python's live 32-member frozenset, and correctly
identified M13 (the content-hash restamp) as an equivalent mutant —
`compute_skill_hash` covers name, description, steps_template and
optimization_objective, and not tier.

The two findings that changed behaviour both came from the same root, and it
is the fourth lens verbatim: **a helper you did not look for is a helper you
will write again.**

`internal/pyval/str.go` has held `Str`, `Repr` and `StrOrEmpty` for several
tranches. They are Python's `str()` and `repr()` over a decoded JSON value,
they are documented, and `StrOrEmpty`'s own comment records that writing
`Str(v)` where Python wrote `str(v or "")` was got wrong three times in one
tranche in three packages. The evolver had nonetheless grown FOUR private
re-implementations — `pyStrValue`, `pyStrGetV`, `pyStrKey` and a local
`stringOr` — and three of the four were wrong in a way the shared one is not.

### The mint coerced five fields, and four of them wrongly

`raw.get("category", "observation")` asks about PRESENCE. The port spelled it
`stringDefault`, which substituted the default when the value was missing OR
EMPTY — that is `or`, not `.get`. Measured on one ordinary model reply,
`{"category":"","target":"","suggestion":5,"failure_pattern":null,"pattern":["rm -rf"]}`:

    CPython : {"category": "", "target": "", "suggestion": 5,
               "failure_pattern": null, "pattern": "['rm -rf']"}
    the port: {"category":"observation","target":"all","suggestion":"",
               "failure_pattern":"","pattern":""}

Five of five fields differ, and `"target": ""` is an ordinary reply rather
than a pathological one. The damage is not only the row's contents: category,
target and suggestion are the three inputs to `_content_key`, so the
divergence changed the row's dedup IDENTITY — the two runtimes disagreed
about which findings they already had, in a store they share.

The fifth field is `str(raw.get("pattern","") or "")` — a truthiness gate AND
a `str()`. The port asserted `.(string)`, so a `new_guardrail` whose pattern
the model answered as `["rm -rf"]` reached the store with an EMPTY pattern:
recorded as applied, matching nothing, for as long as it sat there.

### The decode had to become ordered before the coercion could be right

`str()` over a dict depends on INSERTION ORDER, and `str(5)` differs from
`str(5.0)` by a literal that `json.Unmarshal` into `any` throws away. So
`jsonx.Object` (a Go map, every number a float64) cannot answer the mint's
questions at all: `pyval.Repr` refuses an unordered map by construction, and
`"suggestion": 5` reads back as `"5.0"`. `llmAnalyze` now decodes with
`jsonx.ObjectOrdered`, and the mint reads through `objGet`/`objGetStr`.

The same argument applies to the dedup scan, which renders each row's
category, target and suggestion through `str()` before keying on them. It
borrowed `record.CountLine`; it now borrows `record.CountLineOrdered`, added
here for that purpose.

### What could NOT be fixed, and is pinned instead

CPython's `Suggestion` is a dataclass, and a dataclass does not enforce its
annotations: `suggestion=raw.get("suggestion","")` on a reply of
`"suggestion": 5` stores the INT, and `json.dumps` writes `5`. Go's field is
a `string` and cannot hold one, so the port renders through `str()` and
writes `"5"`. Same for a present null, which CPython writes as `null` and the
port as `"None"`.

Both remain string-shaped to every reader — the read sites are all
`str(x or "")` — so this is a byte divergence in a shared store, not a
behavioural one. Widening the five fields to `any` closes it and is a
38-call-site change. It is recorded as a NAMED DIVERGENCE with a known-gap
pin (`TestTheMintsNonStringDivergenceIsPinned`) that fails, and says so, the
day the fields are widened.

A second named divergence rides along: `Suggestion.ExpectedSignal` is
`[]map[string]any`, and a Go map marshals with its keys SORTED, so a signal
row the model wrote as `{"metric": ..., "direction": ...}` comes back in the
other order. Every reader decodes by key and sees the same thing; only the
bytes differ.

### The NUL-joined content key

`_content_key` returns a 3-TUPLE. The port joined the three fields on U+0000,
which a tuple cannot be confused by and a joined string can: category
`"x\0y"` with an empty target produces the same bytes as category `"x"` with
target `"y\0"`. It is now a comparable Go struct — the same equality, the
same map-key behaviour, no separator to collide on.

---

## load_outcomes: the reader was half a reader

Writing the mint differential surfaced something larger. The Python probe
kept reporting ZERO suggestions, and the reason was that CPython had loaded
zero OUTCOMES from a fixture the Go side read as three.

`memory_ledger.load_outcomes` is TWO readers stacked: `read_jsonl_announced`,
then `_rows_as`, which builds an `Outcome(**{k: d[k] for k in fields if k in
d})` per row. `Outcome` has six fields with no default, so a row missing any
of them raises `TypeError`, is EXCLUDED, and is counted as schema drift under
its own warning — deliberately separate from the corruption buckets, because
drift is the one that grows quietly as the schema moves.

The port had the first reader only, and spelled even that as `strings.Split`
plus `TrimSpace` plus a silent skip. On a three-row store whose rows lack
`outcome_id` and `lessons`, CPython loads 0 and the evolver skips the cycle
("only 0 outcomes (need 3)") while the port loaded 3 and minted suggestions
off them. Same store, same knob, opposite decisions.

Three things came out of fixing it, and two of them are about the port's own
habits rather than about outcomes.

**The filter already existed, one layer too low.** `internal/record/dailylog.go`
had `loadableAsOutcome` and `outcomeRequiredFields`, written by someone who
had MEASURED this exact divergence — the comment quotes CPython's warning —
and applied the filter locally in the MEMORY.md renderer, reasoning that
"LoadOutcomes' tolerance is right for its other consumers." It was not. A fix
for the site that has the fixture is evidence about its siblings, not a fix
for the class. The predicate now lives at the reader, where Python puts it,
and the renderer just takes the head of ten.

**Five packages' fixtures were rows the Python runtime would never read.**
Turning the filter on failed twelve existing tests across `scans`,
`inspector`, `selfimprove` and `record` — every outcome seeder in the port
produced rows without `outcome_id`, `task_type` or `lessons`, because they
had been written against the tolerant reader. Those tests had been asserting
over a corpus CPython reads as empty. The seeders now fill the required
fields, so each case's own fields stay the subject.

**The row VALUE TYPES changed, and one test caught it.** Routing the read
through the announced reader made every number a `json.Number`, and
`intOf`'s `float64` arm stopped matching — MEMORY.md rendered "Total tokens:
0" for a run with 1,234. The rows now go through `pyval.Plain`, which
resolves an integral literal to an `int` and everything else to a `float64`:
CPython's own `json.loads` typing, and strictly closer than the `float64`
the old `json.Unmarshal` produced. A value arrives with a type, and something
reads the type away.

The drift warning quotes the exception that excluded the row, so
`pyMissingArgsMessage` reproduces CPython's TypeError prose — singular at
one, "and" with no comma at two, the Oxford comma at three or more — and is
pinned against a live dataclass across eight subsets.

One divergence in this reader is DELIBERATE and now written down:
`load_outcomes(limit=0)` ends in `[:0]` and returns NOTHING in CPython, while
the port reads `limit <= 0` as "everything" (the zero value degrades to all,
never to none). No Python caller passes 0; the port has exactly one, the
MEMORY.md renderer, which wants the whole ledger.

### The sibling, found by asking

A fix is evidence about its siblings. `_rows_as` has FIVE call sites in the
Python source; three of them (`load_lessons`, `load_task_ledger`,
`load_compressed_batches`) are unported, and the fourth is
`evolver_store.load_suggestions` — same shape, same missing half.
`Suggestion` has SEVEN fields with no default, and `rowToSuggestion` never
fails, so the port kept and zero-filled rows CPython excludes.

`get_suggestion` is the one that matters. Python catches the `TypeError` and
treats the row as ABSENT, with its own differently-worded warning, and the
docstring says why the function exists: it is the lookup the V2 auto-revert
guard uses to re-confirm authority just before an irreversible revert. A
zero-filled answer hands that guard an `applied_manually` of **false** —
permission to revert a row a human applied. That is the same unsafe direction
as the r1 security finding at this exact function, reached by a different
missing coercion, and it is now a subtest of its own.

Turning that filter on failed a further fourteen tests across four packages,
for the same reason the outcomes filter failed twelve: every suggestion
fixture in the port was missing `outcomes_analyzed` or `failure_pattern`.
Two of them were tests OF `get_suggestion` — the truthy-coercion test and the
non-string-suggestion known-gap pin — asserting against rows CPython answers
as absent.

`PyMissingArgsMessage` is exported from `record` rather than copied, because
the sentence now appears at two loaders and a second private spelling is how
the two start to drift. That is the same lens that produced this whole
section.

### The battery

37 mutants over `evolver.go`, `evolver/store.go`, `record/outcomes.go` and
`record/dailylog.go`, all DETECTED after two corrections. Two survived the
first run and both were test gaps of the same kind — **a fixture that cannot
tell two answers apart**:

- Flattening the mint's decoded value through `pyval.Plain` before `str()`
  changed nothing any fixture could see, because every non-string in the
  known-gap pin was a NUMBER, and Plain preserves a number's rendering. The
  pin now carries a dict and a list in those fields, which is the only shape
  the ordered decode exists for.
- Quoting the LAST schema-drift exception instead of the FIRST was invisible
  because both drifted rows in the fixture were missing the same field set,
  so the two sentences were byte-identical. They now drift differently, and
  the assertion is the whole sentence rather than four substrings of it.

Two more were faults in the battery, not the code: one mutant left an import
unused, and one was written while the battery held a snapshot of the file it
edited — restoring between mutants reverted work done in parallel, which is
worth knowing before running one in the background.

## The classifier the port only ever read

`diagnoses.jsonl` has been a read-only store for the Go side since the
graduation tranche. Three consumers query it — graduation, `scans/verify`,
`notify/escalation_context` — and not one line in that file was ever written
by this runtime. Every row in it came from CPython. `internal/introspect/`
is the beginning of the writing half.

The first slice is the part with no I/O in it at all: `FAILURE_CLASSES`, the
nine named thresholds, `_TOOL_ARG_ERROR_SIGNATURES`, and
`classify_tool_pathologies` — the mechanical model—tool edge classifiers
that `step_exec` stamps onto a step outcome and that ride `step_done` events
into `diagnose_loop`.

Four things in eighty lines of Python needed care, and all four are the same
kind of thing: a Go spelling that is close enough to look right.

- **`te.get("is_error")` is TRUTHINESS**, not `is True`. A stamp of the
  string `"no"` is truthy in Python. A port reading it through `v.(bool)`
  calls that transcript clean, and the transcript is the evidence for a
  lesson both runtimes read.
- **`{name!r}` and `str(name)` are different functions and cannot share a
  helper**, because `repr(None)` and `str(None)` agree (`None`, unquoted)
  while `repr("bash")` and `str("bash")` do not. The hallucination evidence
  quotes; the streak names do not.
- **`[:120]` and `[:6]` are RUNE slices.** `pyval.Clip` is the shared
  helper, and this package is deliberately not the seventh private
  `clipRunes` — there are already six (`scans`, `graduation`, `playbook`,
  `evolver`, `skills/utility`, `director`), which is lens 4 with a tally.
- **The signature list is a TUPLE and `next()` takes the first MEMBER that
  matches, not the earliest match in the text.** An output reading
  `invalid argument -- q; usage: g [OPTS]` reports `'usage:'`. A port that
  scanned for the earliest offset would report `'invalid argument'`, and
  that string is what an operator reads.

The descriptions in `FAILURE_CLASSES` are carried verbatim rather than
dropped, because they are not documentation: `maro-introspect` prints them,
the recovery planner keys off the names, and `diagnose_loop` assigns
`recommendation = FAILURE_CLASSES.get(failure_class, "")` — the prose IS a
field on a persisted row. This is the content-key PROSE divergence family
again, one store further along.

The thresholds are read out of the module by the differential rather than
restated, so a calibration change on the Python side fails a test instead of
becoming a silent disagreement about what "too broad" means.

### The battery, and what a fixture cannot see

37 mutants over `classify.go`. Four survived the first run; two were real
gaps and two were faults in the battery.

The real gaps were both **a gate with no negative fixture**. Deleting the
`is_error` check from the hallucination scan survived, because every fixture
carrying the phrase `No such tool available` also carried `is_error: true` —
so the guard could not fail. The fix is two cases: the phrase in a
successful call (a worker echoing a previous failure), and a clean phrase
that must not shadow a real hallucination later in the same transcript.

The second was **nil versus empty**, and it survived because the test
normalized it away. An earlier draft of the comparison read

    if len(want) == 0 && len(got) == 0 { return }

with a comment explaining that a nil-vs-empty difference should not
masquerade as a real one. That is exactly backwards: `json.dumps([])` is
`"[]"` and a nil Go slice marshals to `null`, and this list gets stamped
onto a step outcome and written to the shared events store. Python's own
reader tolerating `null` (`list(e.get("tool_pathologies") or [])`) is not a
reason for the port to write it. The normalization is gone.

Of the two battery faults, one is worth naming because it is a shape that
recurs: **aliasing `worstNames` to the live streak is unkillable on its
own.** Python's `list(streak_names)` is a copy, and the Go copy is right —
but the else-branch resets with `streakNames = nil`, so every later streak
appends into a fresh backing array and the alias can never be written
through. The plausible port is the PAIR — reset by truncation AND alias —
and only the pair is observable. A mutant that cannot change an answer says
nothing about the tests; the battery now applies both edits as one mutant,
and the existing eight-event fixture kills it.

## metrics: two pure functions and a Unicode word boundary

`classify_tool_pathologies` was the slice with no dependencies.
`diagnose_loop` is the next one, and it reads `StepProfile.cost_usd`, which
calls `metrics.estimate_cost` — and `metrics.py` (827 lines) appeared in
exactly two Go comments and no code. So the cost half came first.

`internal/metrics/` ports the pure part: `COST_BY_MODEL`, the fallback
rates, `CACHE_READ_MULTIPLIER`, `estimate_cost`, and `classify_step_type`.
The ledger half (`step-costs.jsonl`, `spend_today`, the p90 run-cost card)
is a separate slice — it writes, and a writer belongs with its store.

**The pricing table is three naming systems for the same models**: full
versioned IDs, the short-form aliases the subprocess adapter emits, and the
tier constants config resolves to. The model string on a step event is
whatever the adapter that ran it wrote. A key mistyped in the port does not
fail — it falls through to the Sonnet default, pricing an Opus step at a
fifth and a Haiku step at nearly four times. So the differential sweeps
every key in the CPYTHON table rather than sampling the Go one: an
enumeration is not a class.

**The arithmetic is spelled in Python's own term order** rather than
factored over a single division. Float addition is not associative, and
this number is compared against named dollar thresholds
(`_STEP_COST_WARN_USD = 0.50`). The comparison in the test is on seventeen
significant digits, not a tolerance — and it earned that: the regrouping
mutant is DETECTED, and a tolerance would have accepted it while still
letting the two runtimes disagree about whether a step tripped the alarm.

**`classify_step_type` does not use Go's `regexp`, on purpose.** Python's
`_STEP_TYPE_PATTERNS` are `\b(a|b|c)\b` over literal words, and RE2 supports
`\b` — but RE2's `\b` is ASCII-only while Python's `\w` for a str pattern is
`str.isalnum()` plus underscore. The step text `fixé` therefore has a word
boundary after `fix` in Go and none in Python: Go buckets the step as
`implement`, CPython as `general`, and the evolver's per-step-type cost
grouping in a shared store gets two different answers about the same step.
The port does the boundary test directly, with `isWordRune` spelled as
`'_' || unicode.IsLetter || unicode.IsNumber`. A combining mark is not
alnum, so `fix` + U+0301 keeps its boundary — the one place the predicate
must not simply say "is a letter".

The same reasoning applies to the one non-literal alternative, `look\s*up`.
Measured on this box, Python's `\s` matches across `\x1c`, `\x1d`, `\x1e`,
`\x1f`, `\xa0`, U+2007 and U+3000 — exactly `str.isspace()` — and not across
U+200B. `pytext.IsSpace` is `str.isspace()`; Go's `unicode.IsSpace` omits
the four `\x1c`–`\x1f` separators. The matcher belongs to the RESEARCH row
and must be consulted before any later row's literals, or
`look up and build the image` classifies as `implement`.

### The battery

37 mutants over `metrics.go`: 33 detected on the first run, three failed to
compile (a mutant that leaves an identifier unused is a fault in the
battery, not a survivor), and one genuinely survived.

The survivor is worth recording. Python clamps with
`max(0, min(cache_read_tokens, tokens_in))`, and **the two clamps commute
for every `tokens_in >= 0`** — so swapping them is invisible to any fixture
built from plausible inputs. They part only on a NEGATIVE input count, which
nothing sane writes: CPython answers with a negative cost, and so must the
port. The fixture is now there, and it pins the ORDER rather than pretending
to be a defence. This is the limit-with-no-case-at-its-own-boundary lens
reaching a place where the boundary is outside the realistic domain.

## diagnose_loop: eight heuristics whose ORDER is the answer

`internal/introspect/diagnose.go` ports the pure half of
`introspect.diagnose_loop` — `StepProfile`, `LoopDiagnosis`, its `to_dict`
and `summary`, `_build_step_profiles`, and the eight-check heuristic cascade
— leaving the I/O half (`_events_path`, `_load_loop_events`,
`save_diagnosis`, `diagnose_latest`) for its own slice.

**The cascade is most-specific-first and the ordering is load-bearing in a
way that is easy to lose.** Six of the eight checks are gated on
`failure_class == "healthy"`; two deliberately are not. `adapter_timeout`
(2) overwrites `setup_failure` (1), and `budget_exhaustion` (7) appends its
evidence whatever the class is and only moves the severity if it also won
the class. A port that gated all eight uniformly would read tidier and
answer differently, so the gating is spelled out check by check and the
divergence is pinned by fixtures that are both.

**`pyval.Grouped` came out of this slice.** Python's `{:,}` renders the
token counts in the cost evidence, and this is the fourth or fifth place a
Python rendering would have gone into a private local copy. It groups from
the RIGHT, so the leading group is 1–3 digits and an index-based `i % 3`
gets 1000 wrong (`,1,000`); the sign stays outside the grouping. The
differential sweeps every digit length 1–18 at both ends, both signs, and
the int64 extremes.

**Two named divergences carried forward.** `stuck_reason` is read with
`evStr`, so a non-string `detail` reads as absent where CPython raises a
TypeError out of `"max_iterations" in stuck_reason`; the same shape applies
to a non-string `goal` on `loop_start`. CPython crashes and the port
diagnoses. Pinned, not replicated.

**Two nil guards were DELETED rather than fixed.** `pyjson.Ordered` already
renders a nil slice as `[]` and not `null`, pinned by a test whose whole
stated purpose is to let call sites stop re-checking it. A local `if ev ==
nil` was a second guard for the same thing — and a second guard is exactly
what makes a wrong bound unobservable. The mutant that removed it survived
the battery *because* pyjson was correct underneath.

### The battery

45 mutants over `diagnose.go`. The first run: 29 detected, 15 survived, one
failed to compile. One survivor (D41) was the duplicated nil guard above and
was answered by deleting production code. The other fourteen were all one
finding wearing different hats, and it is the most useful thing this slice
produced:

**Every cost and churn fixture in the differential was being classified by
an earlier check and never reached the code it was written to exercise.**

- The three `retry_churn` fixtures opened with a blocked, zero-token step —
  which is precisely the shape check 1 calls `setup_failure`. Check 8 is
  gated on `healthy`, so it was never reached, and the mutants on the churn
  limit (`>= 2` → `> 2`), the 60-rune key clip, byte-vs-rune clipping and
  the insertion-order evidence all survived in silence. The corridor that
  actually arrives at check 8 is narrow: step 1 must NOT be blocked-and-fast
  (check 1 reads `profiles[0]` only), the blocked steps must take ≥ 1s
  (check 3) and must spend ZERO tokens (check 6).
- The cost-threshold fixtures were priced by raw input volume — 625,000
  Haiku tokens is exactly $0.50 — and 625,000 fresh tokens trips
  `decomposition_too_broad` at check 4, four checks before the dollar
  thresholds are evaluated. They are now priced with a large cache read
  (grok-4.5, 700,000 in with 500,000 cached) so that fresh tokens land
  exactly ON check 4's limit, which is `>`, and pass through to a cost of
  exactly $0.50 in IEEE doubles.

**The clamp that no fixture could see.** `StepProfile.fresh_tokens` is
`max(0, tokens - cache_read_tokens)`, and every use of it inside
`diagnose_loop` is a `>` against a positive threshold (200,000 / 50,000 /
1,000) or a print of a value that already cleared one. A negative fresh
count therefore cannot change any answer the function gives. The clamp is
not dead — it is only observable to a caller of the PROPERTY, so the test is
a direct differential of `fresh_tokens` and `cost_usd` against CPython's own
`StepProfile`, with cases where the cache stamp exceeds both the total and
the input.

**And the battery committed the lens itself.** D35 reported MISS for an hour
after that test existed, because the battery invoked
`go test -run Diagnose`, which does not match
`TestStepProfilePropertiesMatchCPython`. A filter that excludes the test
which kills a mutant reports a survivor the battery caused. The `-run`
filter is gone.

Final state: 44 live mutants, 44 detected.

## introspect's I/O half: two readers, one writer, and a limit that lies

`internal/introspect/store.go` finishes the module's runtime surface —
`_events_path`, `_diagnoses_path`, `_load_loop_events`,
`_load_latest_loop_id`, `save_diagnosis`, `load_diagnoses` and
`diagnose_latest`.

**`load_diagnoses(limit=0)` returns ONE row.** Python appends and *then*
checks `len(results) >= limit`, so the first valid row is already in the
list when the check fires; the same holds for any negative limit
(measured, both). This is the second appearance of the zero-that-must-mean-
two-things shape in this port — `load_outcomes(limit=0)` returns nothing
where the port reads `limit <= 0` as "everything" — and it resolved the
other way: replicated exactly, because the port that tidies it disagrees
with the runtime it shares a store with.

**It reads the WHOLE store and limits afterwards**, deliberately, and
Python says why: a raw record can fail `LoopDiagnosis` construction, so the
caller wants `limit` VALID rows, not `limit` raw lines. A tail read would
quietly return fewer and look correct.

**Rehydration is a dataclass constructor, which type-checks nothing.** A
present null in a required field is still PRESENT, so it does not raise —
the row rehydrates with `severity=None` and `summary()` renders
`severity=None`. Only a MISSING key makes `LoopDiagnosis(**subset)` a
TypeError, which is what skips the row. So the port tests presence
separately from value, and reads the string fields through `pyval.Str`
rather than a `.(string)` assertion: `Str` IS Python's `str()`, so None
becomes "None", 5 becomes "5" and 5.0 becomes "5.0" — the same bytes
CPython's f-string produces. An assertion would have rendered all three as
"" and made three different rows look identical.

**One divergence is unavoidable and is recorded rather than chased.** The
port's numeric fields are typed `int`, so a row carrying
`"total_tokens": "many"` reads 0 where CPython renders `tokens=many`, and a
JSON 5.0 reads 5 where CPython renders `tokens=5.0`. Making five fields
`any` to be faithful about rows no writer produces would push the
untypedness through Diagnose, Summary and every caller. What makes that
safe is containment: nothing rehydrated here is ever written back, so a
mistyped row in the shared store cannot be REWRITTEN by this runtime into a
differently mistyped one. The test that pins both exact strings is where
the decision gets revisited if a writer ever appears.

**The `Path.cwd()` fallbacks are not ported.** Both path helpers wrap the
`orch_items` import in a bare try/except and fall back to
`Path.cwd() / "memory" / ...`. That branch fires only when the import
raises, and when it does it silently repoints the whole store at whatever
directory the process happens to be in — a second store-routing rule
disagreeing with the first, which is the exact shape of the 2026-08-16
live-ledger incident. One resolution order, passed in as an argument.

### The battery

35 mutants over `store.go`, of which one was retired: 34 live, 34 detected
after four fixture gaps were closed.

The four gaps are worth listing because three of them are the same mistake
— a fixture that omits ONE thing proves nothing about a rule that names
several:

- The prefix match had a case for an exact id and a case for a longer id,
  but none where the query is a SUBSTRING and not a prefix. `HasPrefix` and
  `Contains` were indistinguishable until `"L1"` had to *not* match
  `"xL1y"`.
- The required-field set is three names and the fixture omitted only
  `loop_id`, so a list short by one still passed. There is now a row
  missing each of the three.
- `recorded_at` appears in no `summary()` and in no other assertion, so a
  rehydration that dropped it was invisible to every text comparison. It is
  the field the per-class rate windows sort on, which makes it the worst
  one to lose quietly.
- `diagnose_latest` passes no project, and nothing checked that. A port
  that filled in a plausible slug would stamp every unattributed diagnosis
  with a project it was never told — and project is a RANKING input in
  `find_relevant_failure_notes`.

The retired mutant is the more interesting one. It swapped the ordered
event reader for the plain one, and there is no input on which that changes
an answer: nothing writes an event back, and Diagnose reads every field by
NAME. Chasing it found that the production comment justifying the ordered
read said the diagnosis is "written back from" these rows — a mechanism
that does not exist. The fix was to the comment. The real reason is that
Diagnose takes `pyval.Obj` and the fixtures decode through the same
decoder; giving one store two readers that resolve numbers differently is
the divergence those readers' own parity test exists to prevent.


## find_relevant_failure_notes: a ranking with two lanes, and a formatter
## that does arithmetic on prose

`internal/introspect/notes.go` ports
`introspect._format_decomp_too_broad_note` and
`introspect.find_relevant_failure_notes` — the surface that turns stored
diagnoses back into one-line "known patterns to avoid" for injection ahead
of decomposition. It costs nothing per call: relevance is token overlap
between the goal and each diagnosis's evidence, not a model.

**Three regexes transcribed character-for-character from Python match a
different language in Go.** The step pattern is
`Step (\d+).*?(\d+)\s*(?:ms|tokens).*?(\d+)\s*(?:tokens|ms)`. Go's
`regexp` reads `\d` as `[0-9]` and `\s` as five ASCII code points; Python's
`re` on a `str` pattern reads `\d` as every Unicode decimal digit (760 on
this box) and `\s` as its full 29-point set. Both patterns run over
evidence text read out of a store the two runtimes share, so the classes
come from `pytext` — which measures them against CPython — rather than
being spelled out. This is the fourth chunk in the port to need them and
the first where a *pattern* rather than a *split* was the consumer.

**`int(s) // 1000` is not `strconv.Atoi`.** Two Python behaviours a
fixed-width parse loses. `int()` accepts any Unicode decimal digit, so
`int("١٢٣٤")` is 1234 — `pytext.FoldDecimals` maps those to ASCII first.
And Python's ints are unbounded, so a forty-digit run in a hostile evidence
line divides cleanly there and overflows here. `big.Int` costs nothing on
this path and removes the class rather than bounding it.

**Same-project entries always lead, and are excluded from scoring
entirely.** A same-project diagnosis with zero goal overlap outranks a
perfectly-overlapping one from elsewhere. That is deliberate in Python — a
prior decomposition warning on the same project is far more actionable —
and it is exactly the part a port is most likely to "improve" into a single
weighted sort.

**The scored sort is stable and keys on overlap ALONE.** Python's
`list.sort` is stable, so equal-overlap diagnoses stay in the order
`load_diagnoses` produced them, which is newest-first. A `sort.Slice` would
be free to permute them and the top-`limit` slice would pick a different
diagnosis on different runs.

**A negative `limit` is named, not guessed at.** `ordered[:limit]` with a
negative limit slices from the END in Python — `ordered[:-1]` drops the
last entry. The port's `if len(ordered) > limit` lets a negative limit
through and returns everything. Unreachable from any caller today (`limit`
is a keyword with a default of 3) and written down rather than silently
differing, the same way `pyval.Clip` carries a paragraph about the same
hazard.

**One thing the chunk REMOVED.** The project tag —
`f" (project={diag.project})"` — was spelled once in each of the two note
formatters, mirroring Python. A mutation battery could not name a single
site for it, which is how it got looked at: two private copies of one
operator-facing rendering is how two surfaces start describing the same
thing differently. It is now one function.

### The battery

43 mutants over `notes.go`, of which **three were retired as unkillable and
four were battery spelling faults**. Final state: 40 live, 40 detected —
after eight real fixture gaps were closed.

The three retired mutants share a shape worth naming: *a mutation that
cannot change an answer is not a survivor, it is a bad mutant*, and each
one produced a fact worth writing into the production file.

- **Greediness on the step pattern.** `.*?` versus `.*` decides *which*
  substring matches, never *whether* one does — and `MatchString` is the
  pattern's only consumer. Python captures three groups and throws them
  away too.
- **Swapping the two substitutions.** The `ms` and `tokens` replacements
  *commute on every input*. Their match regions can never overlap: one
  needs a digit run immediately before `ms`, the other a digit run before
  optional whitespace and `tokens`, and a single run cannot be immediately
  before both. Neither replacement's output can create a match for the
  other either — one appends `s`, the other `K tok`. `notes.go` now says
  so, so nobody "fixes" the order later.
- **Clipping after the newline flattening instead of before.** `\n` → `" "`
  maps one character to one character, so it cannot move the 120-rune
  boundary. Both orders agree on every input.

The eight real gaps split into two families.

**Four fixtures never measured the pattern's actual job.** The step pattern
does not extract anything — it SELECTS which evidence line becomes the
detail, with a fallback to `evidence[0]`. Every fixture handed the
formatter a single candidate line, so the fallback produced *the same
string* the match would have, and four mutants walked through: Go's `\d`,
Go's `\s`, an anchored `^Step`, and last-match-wins instead of first. Each
now pairs a non-matching first line with a second line only Python's
pattern reaches.

**Four fixtures were single-variable tests with more than one variable
resolving the same way.**

- The healthy filter's fixture had a healthy row with *no evidence*, so
  keeping it still produced nothing. It now overlaps the goal.
- `"by"` is the twentieth stopword and every case overlapped on a content
  word as well, so a set short by one changed no answer.
- The Unicode-split fixture separated goal tokens with `\x1c`, ` `
  and `　` at once. Go's `strings.Fields` splits the latter two as
  well, so the matching token survived under both spellings. `\x1c` is the
  only discriminator and is now the only variable.
- The sort's stability and its DIRECTION were both unmeasured, because
  every fixture had a single distinct overlap value.

That last one took a measurement rather than an argument. Under 13 elements
Go's `sort.Slice` delegates to insertion sort and is therefore
*accidentally* stable, so a small fixture cannot tell it from
`sort.SliceStable`. And with every key TIED, Go's partitioning happens to
leave the order alone at any size — measured here up to n=60. It is the
COMBINATION that permutes: twelve tied rows plus one scoring higher, at
which point the unstable sort returns a scrambled tail. The fixture is 13
rows with the higher-scoring one oldest, and it is the only case where the
comparison's direction is observable too.

The four spelling faults were mine, not the code's: one mutant targeted a
line the `projectTag` extraction had just removed, two quoted prose
fragments across a line break the source puts in a different place, and one
left `pyval` imported and unused, which reports BUILDFAIL rather than a
verdict. The pattern is now familiar enough to state plainly: **a battery
run's first output is a report on the battery.**


## The lens registry: five heuristics whose ORDER an operator reads

`internal/introspect/lens.go` ports `LensResult`, `LensRegistry`, the five
free heuristic lenses (`_execution_lens`, `_cost_lens`,
`_architecture_lens`, `_operator_lens`, `_forensics_lens`),
`get_lens_registry` and `run_lenses`.

**The registry is two DICTS, and a dict preserves insertion order.**
`run_heuristic` iterates `self._lenses.items()`, so registration order is
the order lenses run, the order their findings concatenate, and — through
`aggregate_lenses`' `max()` over the category dict — the tie-break that
decides which single action an operator is shown. A Go map randomises all
three on every process, so the port carries an explicit `order` slice.
Re-registering an existing name replaces the function and KEEPS its
original position, because that is what assigning to an existing dict key
does; a port that appended again would move the lens to the back of the run
order. `List()` sorts, and is a different order from the run order for the
built-in set — alphabetical puts architecture first, registration puts
execution first. Both are pinned.

**`{:.0%}` multiplies before it rounds.** The execution lens renders a
block rate as `f"{ratio:.0%}"`. A rate of 0.125 renders "12%" and 0.375
renders "38%" — 12.5 and 37.5 are exact halves and round half-to-even in
opposite directions. Rounding first and multiplying second answers "13%"
and "38%", and nothing else in the lens separates the two orders. The port
grew `pyval.PercentFmt` for it.

**`{:,.0f}` is a grouped float, and negative zero survives.** The cost
lens renders an average through it. Three things a hand-rolled version
gets wrong: only the INTEGER part is grouped (`{:,.3f}` of 1234567.891 is
"1,234,567.891"), the rounding is half-to-even on the exact double, and
`{:,.0f}` of -0.5 is **"-0"**, not "0". `pyval.GroupedF` shares its
separator rule with the existing `Grouped` rather than spelling it twice —
and takes a digit STRING rather than an integer, because `{:,.0f}` of
1e308 groups all 309 digits and no fixed-width integer carries that.

**`//` is not `/`.** The operator lens divides millisecond counts by 1000
in four places. Go truncates toward zero and Python floors toward negative
infinity, so `-1 // 1000` is -1 there and 0 here. `elapsed_ms` comes
straight out of `e.get("elapsed_ms", 0)`, so a negative stamp is a store
value rather than a hypothetical. `pyval.FloorDiv` PANICS on a zero
divisor rather than answering 0 — Python raises ZeroDivisionError, and a
helper that invented a plausible number would be the fail-open shape L38
names.

**A dead guard that is not dead.** The cost lens returns early when
`total == 0`, and then computes `(top.cost_usd / total * 100) if total > 0
else 0`. The second test looks unreachable after the first and is not: a
store carrying a negative cost sums to less than zero, passes the equality
check, and lands there. Ported as written.

**Three word sets that must NOT be unified.** The cost lens's
`cheap_keywords`, the architecture lens's `_filler` and the forensics
lens's stopword list are all different from each other and from
`noteStopwords` — the forensics one alone contains "step". Each is tuned to
the sentence its own lens reads. Merging them would change three
heuristics at once to make one of them tidier, which is the opposite of
the L14 helper rule and worth saying out loud next to code that looks
duplicated.

**A printed denominator that disagrees with the computed one.** The
execution lens computes its ratio over `steps_done + steps_blocked` and
prints `steps_total`. When a step is neither, the line reads "Block rate:
50% (2/10)". Faithful to Python, and named in the file because it reads
exactly like a typo.

**What is NOT here.** Python's registry also holds `_quality_lens` (cost
"cheap") and `_adversarial_lens` (cost "mid"). Both call `build_adapter`
and spend tokens, and the adapter suite is not ported. They are EXCLUDED
rather than stubbed: a registered lens that always returns nothing is
indistinguishable from a working one that found nothing. `RunHeuristic`'s
answer is unaffected — it skips non-free lenses anyway — and there is no
`RunAll`. `aggregate_lenses` is the next slice, not this one.

Python also memoises the registry in a module global and hands every
caller the same mutable object, so a test that registers a lens changes
every later caller's run order for the life of the process.
`DefaultLensRegistry` returns a fresh one: nothing in the module mutates
the shared instance, and a process-wide singleton whose contents tests can
edit is a cross-test channel rather than a feature.

### The battery, and a rule that was vacuous at its own threshold

Ninety mutants across the registry and the five lenses. The differential
was green against CPython on its first run — sixty-odd subtests — and the
battery still found twenty-two survivors, which is the usual ratio for
this arc and the reason the batteries keep being worth their wall-clock.

Five were retired as unkillable, each with a proof rather than a shrug:

- **The registry's cost default.** Python writes
  `self._costs.get(name, "free")`, and no input reaches the default:
  `register` is the only writer of either map and always writes both, so
  a name found in `_lenses` is present in `_costs`. The port had carried
  the fallback faithfully. It now **deletes** it — the third time this arc
  that the answer to a survivor was removing production code rather than
  adding a test.
- **Floor division in the operator lens.** All three `FloorDiv` sites sit
  behind guards that force both operands non-negative (the waste line
  needs `blockedMS > 0.3*totalMS > 0`; the slow line needs
  `ElapsedMS > 120000`; the progress line needs `totalMS > 60000`), and
  floor equals truncation there. The spelling stays, because the guards
  are what a later edit relaxes; the comment now says so instead of
  implying a test could tell.
- **The forensics stopword ordering.** Subtraction distributes over
  intersection, so `(A ∩ B ∩ C) \ S` and `(A \ S) ∩ B ∩ C` hold the same
  words for every input. The comment at that site had claimed the order
  was observable. It was my own prose, and it was wrong.
- **The done/blocked `else`.** `Status` is one string and blocked means
  `stuck` or `blocked`, disjoint from `done`, so nothing satisfies both
  arms.
- **A two-step outlier gate**, for the reason below.

The interesting one is what the remaining seventeen fixtures exposed.

**The token-outlier rule is vacuous at its own threshold.** The gate is
`len(tokens) >= 3`; the bar is `t > 3 * (sum // len)`. With exactly three
token-bearing steps *no step can ever clear the bar*: `3*floor(S/3) > S-3`
and `S >= t` together put the bar at `t` or above. Four steps is the first
width that can fire, and only when the other three are small. The same
algebra kills the relaxed gate — with two values the bar is at least 1.5×
the larger.

Three fixtures sat on that rule, named `an outlier over both bars`, `an
outlier over the ratio but under 50000`, and `a step over 50000 but under
the ratio`. All three had three steps. **None of them ever entered the
finding.** They ran, computed an average, found nothing, and passed —
against a CPython side doing exactly the same nothing, so the differential
agreed perfectly. Six separate mutations of the rule survived behind them,
and the fixtures' names are why nobody looked.

That is not the over-determined-fixture shape already in the catalog: no
confound is involved and splitting variables does not help, because the
input is the wrong *size*. It is now **L43 — a guard's threshold is not
where its rule starts working**. The tripwire is to solve for the smallest
input that satisfies the *rule*, not the smallest that passes the *gate*,
and to make a fixture named for a finding fail when the finding is absent.

The rest were ordinary gaps of the kind the catalog already predicts: a
cost tie that could not render (two tied positive steps can never exceed
half a total containing them both — only a negative-cost third step gets
`topPct` over the bar), a block rate where every fixture rounded the same
before and after multiplying by 100 (`39/40` is the smallest ratio that
separates them), and the usual missing multibyte and `\x1c` cases for
rune-clipping and `str.split()`.

Negative costs deserve their own line, because two separate survivors
needed them and the port had reasoned about them only in a comment. A
store carrying a negative `tokens_in` sums below zero, which passes
`total == 0` and lands on the `total > 0` guard that reads as dead above
it. With one such step that guard is the entire answer: it holds `topPct`
at zero where dropping it divides a negative by itself and reports "100%
of loop cost". The comment claiming the guard was live is now a fixture
proving it.

Final: **85 live mutants, 85 detected**, five retired with written proofs.
Seventeen fixtures added, one production branch deleted, and three of the
port's own comments corrected — two that claimed an observability the
algebra denies, one that was silent about a rule it should have named.
That ratio is worth stating plainly: across the six batteries this arc,
nearly every survivor that was not a battery fault was a fixture that
could not tell two answers apart. The batteries are finding more in the
tests than in the code under test, which is what a differential suite that
was green on its first run should make you expect.

### The constant that was not the number

The first differential for `GroupedF` failed on one row, and the helper
was right: the FIXTURE was a different double.

Go evaluates untyped constant arithmetic EXACTLY and rounds once at the
conversion, so `0.1 + 0.2` written in a Go source file is the nearest
double to 3/10 — 0.29999999999999998890. Python rounds 0.1 and 0.2 to
doubles first and then adds, giving 0.30000000000000004441. Every fixture
in the port that spells an inexact value as an arithmetic expression is
measuring a value CPython never had. It only surfaces at a precision wide
enough to print the difference, which is why a table that stopped at three
decimals would have agreed forever.

Division is unaffected — 2.0 and 3.0 are exactly representable, so the
constant quotient and the runtime quotient round identically. Only
operands that are themselves inexact diverge.

`internal/record/r4fixes_test.go` already records this hazard. It was
learned once, written down at the site that found it, and did not reach
the next author — which is the whole argument for the lens catalog being a
catalog rather than a comment.

## The recovery table, and a second slot with nothing in it

`internal/introspect/recovery.go` ports `RecoveryPlan`, `_RECOVERY_TABLE`,
`plan_recovery`, `plan_recovery_all`, `RecurringPattern` and
`find_recurring_patterns`.

The table is fifteen failure classes mapped to lists of plans, and Python's
comment says the lists are "ordered by preference (cheapest/safest first)".
**Every list has exactly one entry.** That is not a tidy-up waiting to
happen: `plan_recovery`'s advisor branch returns `plans[1]` when the
advisor's reply contains `"(b)"`, so the second slot is a reachable code
path with no data behind it. `len(plans) > 1` is what keeps it from
raising. The branch is dead *by data*, not by construction, and the first
two-plan entry anyone adds wakes it up. The port keeps the lists.

The advisor path itself is not ported — it calls `llm.advisor_call`, spends
an Opus round trip, and is off by default. It fires only for plans that are
medium-or-high risk AND not auto-apply, which is nine of the fifteen.

`plan_recovery_all` returns `list(...)` — a copy. The port copies for a
reason Python does not have to think about: a returned Go slice header
aliases the table's backing array, and one `plans[0].Action = ...` from a
caller would rewrite operator-facing prose for every later diagnosis in the
process. `TestPlanRecoveryAllReturnsACopy` is the only thing that pins it,
and the mutation that returns the table slice directly is invisible to
every other test in the package.

`Params` is an ordered `pyval.Obj` rather than a map, because Python's is a
plain dict and every consumer that renders a plan does so through
`str()`/`json.dumps` of that dict — where key order is the byte order of
the output. No plan carries more than one key today, which is exactly why
the ordered type is cheap insurance rather than an argument.

### The tie-break is insertion order, again

`find_recurring_patterns` is the third place in this module where a Go map
would have silently changed an operator-facing answer. Python builds
`counts` with `setdefault`, so its keys are ordered by each class's FIRST
appearance in a list `load_diagnoses` returns NEWEST FIRST. The sort key is
`-len(x[1])` alone and `list.sort` is stable, so **two classes with equal
counts come back most-recently-seen first**.

Two more things a single-occurrence fixture cannot see. `first_seen` is
`diags[-1].loop_id` and `last_seen` is `diags[0].loop_id` — reversed
relative to how they read, because the list is reverse-chronological. And
`graduation_candidate` is `len(diags) >= min_occurrences`, evaluated after
a `continue` that already skipped every class failing that same test: it is
**always True**. Ported as a field rather than dropped, because it is part
of the record other code reads, and named in a comment so nobody reads a
false one as meaningful. There isn't one.

The stability of that sort is a live L42 trap, and the battery names it:
swapping `sort.SliceStable` for `sort.Slice` needs thirteen-plus classes to
observe, because Go's pdqsort delegates to insertion sort below that.

## aggregate_lenses: four orderings and one asymmetry

`internal/introspect/aggregate.go` ports `AggregatedDiagnosis`, its
`summary()`, and `aggregate_lenses` — the function that turns several
lenses into the one sentence an operator acts on.

**Four orderings decide the answer and every one of them is insertion
order in Python.** `all_evidence` starts as a copy of the diagnosis's own
evidence and extends with each lens's findings in lens order.
`all_actions` is appended in lens order and returned whole. `categories`
is a dict keyed by first-meaningful-word, so its values come back in the
order each category was first seen. And `max()` returns the FIRST maximum,
so a tie on `(count, best confidence)` goes to the category seen first —
that is, to the lens registered earliest. A Go map randomises the last two.

The category key is the action's first word that is not in
`{the, a, an, to, into, for}`, so "Split step 3 …" and "Split the loop …"
vote together. An action made ENTIRELY of stopwords keys to the empty
string, which is a real category other empty-keyed actions join — not a
guard to add, because two of them genuinely do agree with each other, and
they can outvote a real category and become the primary action.

**The asymmetry.** The no-actions branch returns `all_evidence` UNCAPPED
while the main branch truncates at 20. A loop with no actionable lens can
therefore return a longer evidence list than one with several. It reads
like an oversight; it is faithful, it is named at the site, and moving the
cap up there would change what a caller renders today. The differential
runs the same twenty-five evidence lines down both branches, which is the
only way the two answers are visible at once.

`min(1.0, x)` is spelled as a negated less-than — `if !(boosted < 1.0)` —
rather than `if boosted > 1.0`. Python's `min` returns its first argument
unless the second compares strictly less, so `min(1.0, nan)` is 1.0, where
`> 1.0` would let a NaN through unchanged. Not reachable from the built-in
lenses, whose confidences are five finite literals; spelled correctly
because the cheap spelling and the right one are the same length. The
battery retires the mutant that swaps them, since no fixture in a JSON
differential can carry a NaN.

`round(x, 2)` is half-to-even on the exact double, not half-away-from-zero:
`round(0.125, 2)` is 0.12 and `round(0.375, 2)` is 0.38. `pyval.Round` is
that; `math.Round` is not, and the two fixtures at those values are the
only ones that tell them apart. `summary()` then renders the *already
rounded* value through `{:.2f}`, a different formatter — so it gets its own
assertion rather than being derived from the confidence check.

One documentation divergence, left alone and pinned: `all_actions` is
documented as "every unique action recommended" and the code appends every
action without deduplicating. Two identical actions prove which of the two
is true. The port matches the code.

### The battery for both

Fifty-nine mutants across `recovery.go` and `aggregate.go` — every
threshold, comparison direction, index, ordering and format spec, plus a
sample of the table's prose (one action, one risk, one auto_apply flag, one
param key, one param value). The table itself is compared character for
character by the differential, so re-checking each of its fifteen rows with
a mutant would only re-measure that.

Eleven survived the first pass, and the split is the one this arc keeps
producing: **one real gap, four faults in the battery, and six mutants that
no input can distinguish.**

The real gap is `sort.SliceStable`. Swapping it for `sort.Slice` survived
every fixture, because the largest had four failure classes and Go's
pdqsort delegates to insertion sort below thirteen — and with every key
tied its partitioning leaves the order alone at any size. This is the
second unrelated function in this port to land on that exact boundary (the
first was the notes ranking), which is what turned it from a footnote into
a catalog entry. The fixture is now thirteen classes: twelve tied at one
occurrence plus one ahead, and the leader is written OLDEST so that after
`load_diagnoses` reverses the list it sits LAST in `counts` and the sort
has to move it.

Two of the battery's four faults are worth naming because they are a Go
rule rather than a typo. Mutating `if n > bestCount || (n == bestCount &&
c > bestConf)` down to either half leaves `bestCount` or `bestConf`
*assigned but never read* — and Go does not count assignment as use, so the
mutant does not build. A BUILDFAIL is a fault in the mutant, never a
survivor, and reading it as one is how a battery talks itself into a number
it did not earn.

The six retirements, each with a proof rather than a shrug:

- **The empty-store short-circuit.** Without it the grouping loop runs zero
  times and the function returns the same `nil`. It is a fast path, not a
  behaviour.
- **`PlanRecovery(diags[0])` versus the last occurrence.** `PlanRecovery`
  reads only `FailureClass`, and every member of a group shares it by
  construction — that is what the group *is*.
- **The `seen` check on `catOrder`.** A duplicated key re-visits a category
  and compares it against *itself* under strict inequalities, so the second
  visit can never displace the first. The check saves work; it does not
  break a tie.
- **`!(boosted < 1.0)` versus `boosted > 1.0`.** They differ only at NaN,
  and at exactly 1.0 both leave the value alone. No lens produces a NaN
  confidence and JSON cannot carry one, so nothing a differential can build
  reaches the difference.
- **The evidence cap's `>` versus `>=`.** At exactly twenty the mutant
  slices `allEvidence[:20]`, which is the identity. Above twenty both
  truncate; below, neither does.
- **`strings.Fields` in the action key** — *unspellable*, not unkillable.
  `aggregate.go` does not import `strings`, so the mutation cannot build.
  The behaviour it was meant to probe is real and is now carried by a
  fixture instead: an action glued with `\x1c` groups at agreement 2 under
  Python's `str.split()` and at 1 under `Fields`, which was measured before
  the fixture was written rather than after it passed.

Final: **53 live mutants, 53 detected**, six retired with written proofs.

Across the seven batteries this arc the shape has not moved. The
differentials keep going green on their first run and the batteries keep
finding that a third to a quarter of the fixtures could not tell two
answers apart — and that a meaningful share of the "survivors" are faults
in the battery rather than gaps in the tests. Both halves are the point:
the first is why the batteries are worth their wall-clock, and the second
is why every MISS gets a proof before it gets a fixture.

## The CLI: where a port stops being about values

`maro-introspect` is the last piece of `introspect.py`, and it is a
different kind of port from everything above it. The functions underneath
return values a differential can compare field by field. `main` returns
nothing. Its entire output is a block of text an operator reads, and the
only faithful comparison is the bytes.

So the test compares whole stdout. `introspect.main(argv)` runs under
`contextlib.redirect_stdout`, the Go `Main` writes to a `bytes.Buffer`
instead of `os.Stdout`, and the assertion is string equality on the
result. Nothing is reconstructed field by field, because a reconstruction
agrees with itself: it would compare the numbers and never notice a
missing blank line, a pad that is one column narrow, or a section that
did not render at all. Those are the defects this file can actually have.

The failure output renders every space as `·` for the same reason. A
trailing space is invisible in a terminal diff and this file emits one on
purpose (below).

### The flags parse differently, and it is not cosmetic

`argparse` interleaves options and positionals. Go's `flag` package stops
at the first non-flag token and hands back everything after it as
positional arguments. So:

```
maro introspect loop-alpha --lenses
```

parses in Python as `loop_id="loop-alpha", lenses=True`, and in a
straightforward Go port as `loop_id="loop-alpha"` with `--lenses` sitting
unread in the positional tail. The lens block silently does not render.
Nothing errors; the output is simply a shorter, entirely plausible
diagnosis.

This is not a hypothesis. It is how the first differential run failed, on
the single fixture where the flag came second, and it would have been
invisible to a test that called `RenderLenses` directly. `splitArgs`
separates the two groups first, preserving each group's order, and knows
that `--history` is the one option consuming a following token — spelled
as a set rather than inferred, because getting that wrong turns a flag's
value into a loop id and diagnoses a loop named `5`.

### A read command that writes

`diagnose_loop`'s `emit_log_event` defaults to `True`, so in Python
`maro introspect <loop-id>` **appends a DIAGNOSIS event to the captain's
log** for any non-healthy class. Diagnosing is a read; this is a write,
inside a bare `try/except`, on the operator's live event stream.

The port forwards with `emit_log_event=False` and says so at the call
site. The write belongs with the captain's-log port, and until it lands,
the honest thing is a named divergence rather than a quiet one.

It has one immediate consequence for the test: **each runtime gets its own
copy of the store.** Pointed at one directory, CPython's run would change
what it reads on the next case, and the differential would be measuring
its own side effect. `copyStore` duplicates the seeded workspace per case.
This was also checked against the real ledger afterwards — the live
`~/.maro/workspace` event log was unchanged by the comparison runs.

### Three details the fixtures exist to pin

**`tokens=` has two spellings.** The single-diagnosis view renders total
tokens through `{:,}`; the `--history` list renders the same field bare.
987654 appears as `987,654` in one and `987654` in the other. Both are
Python's, and the port keeps both.

**The space before `confidence=` is unconditional.** In the lens block,
`[{name}]{cost} {conf}` puts a literal space before a fragment that is
empty when a lens reports zero confidence — so such a lens renders a line
with a trailing space. No heuristic lens produces zero confidence, so it
is unreachable through this CLI; the port reproduces it anyway, because a
port that tidies whitespace is a port whose diff has stopped being a diff.

**`if args.history:` is a truthiness test.** `--history 0` is falsy and
falls *through* to diagnosing a loop rather than printing an empty
history. Go's `flag` has no `None`, and `> 0` reproduces both the unset
case and the explicit zero — which are the same case in Python and had to
be shown to be, with a fixture that passes `--history 0` and expects a
diagnosis.

And one that is not a detail at all: **`--llm` does not exist.** `main`
calls `run_lenses(..., include_llm=getattr(args, "llm", False))` and
argparse never defines the flag. The getattr default is the only value it
can ever take, so the two LLM lenses are unreachable from this CLI in
Python. That is exactly where the port leaves them.

### The battery, and a fixture that had never rendered the thing it names

Seventy-five mutants, derived from the file rather than the diff: every pad
width, every separator, every blank line, each conditional section deleted
as well as perturbed, both `Grouped` call sites, the bar's divisor and its
clamp, the severity table's four arms, `splitArgs`' value-flag set, and the
precedence between `--patterns` and `--history`.

Sixty-eight died on the first run. The other seven are the chunk's actual
findings, and only three of them turned out to be about the code.

**Four fixtures could not reach the rule they were named for.** Three were
predictable enough to fix before the run — the pattern loop ids were
exactly eight characters, so `Clip(_, 8)` was the identity and a port that
dropped the clip (or swapped `first:` with `last:`) rendered byte-for-byte
correct output; every failure class was ASCII, so padding by bytes and
padding by runes agreed everywhere; `--patterns` and `--history` were never
passed together, so their precedence was unpinned in both directions; and
no store held more than a handful of rows, so the 50-row read window was a
formality. All four mutants died once the fixtures were widened.

**The fifth was found by chasing a survivor, and it is the one worth
writing down.** `BuildStepProfiles` computes a step's token count as
`tokens_in + tokens_out`. It does not read a `"tokens"` key. The fixture
named "the bar widths" — four steps carrying 4999, 5000, 900000 and 0
`"tokens"` — had profiled every one of them at **zero** for its entire
life, and had never rendered a bar at all.

Nothing reported this. Four bar mutants (the divisor, both clamp
spellings, the `Grouped` call) all died anyway, to a *different* fixture
that happens to price its steps properly. The named fixture was decorative
and the coverage was incidental, which is the combination that survives
review: the diff was green, the case list read as thorough, and the one
file whose whole job is the token bar was measuring nothing about it.

The writer's field name and the reader's are two separate claims. This
fixture checked one of them.

**Two more fixtures were missing outright**, both hiding behind a comment I
had written in the production file. The cost lens returns early when
nothing in the loop was priced, and that early return names only
`lens_name` and `findings` — so the confidence falls to the dataclass
default of `0.0` and the action to `""`. The rendering comment asserted
that a zero confidence was unreachable through `run_lenses`. It is not:
any loop whose steps carry no model and no input tokens produces exactly
that result, renders the trailing space the unconditional separator
creates, and renders a findings block with no `->` line under it. Two
mutants survived on the strength of that sentence. The comment is now
corrected in place, and a fixture holds it.

**And that fixture immediately demonstrated the lens on itself.** The first
version of it reused `loop-gamma` — which qualified as unpriced only
*because* of the `"tokens"` bug above. Fixing the field gave those steps a
real cost, closed the `total == 0` branch, and silently un-covered the
thing the new fixture had just been written to cover. Both mutants came
back on the next run. Zero cost and a rendered token bar are mutually
exclusive, so they cannot be the same loop; the unpriced case is its own
fixture now, with the reason written beside it. The battery caught this in
one round. Nothing else would have.

The sixth gap was the `no notable findings` line, which needs **all five**
lenses silent at once — and that needs both halves: no step profiles (which
stops the four that read them) and a healthy class with no blocked steps
(which stops the execution lens, the one that reads the diagnosis instead).
A loop whose only event is its own completion is the smallest thing that is
both.

**One survivor was a bad mutant, not a gap.** Widening the bar's `tokens >
0` guard to `>= 0` changes nothing at any input: the two differ only at
exactly zero, and there the body computes `0 // 5000 = 0` and repeats `"="`
zero times — the same empty bar the guard produces. The guard's real claim
is that it stops a *negative* count from reaching `strings.Repeat`, which
panics where Python's `"=" * -2` quietly yields `""`. Only deleting the
guard outright puts that claim under test, and once a step with a negative
token count existed, deleting it did.

**Three are unkillable and retired with proofs:**

- **`padLeft` counting bytes instead of runes.** Both call sites pass ASCII
  by construction — a decimal step index and a `Grouped` token count, which
  is digits and commas. No input to this CLI reaches the difference.
  `padRight` is a different matter, which is why the multibyte fixture
  above exists for it and not for this one.
- **The graduation-candidate branch.** `GraduationCandidate` is
  `len(diags) >= minOccurrences`, computed after a `continue` that already
  skipped every class failing that same test. It is a tautology at every
  reachable input, so the conditional around the marker can only ever take
  one arm.
- **The `healthy` guard in `RenderRecovery`.** Removing it changes nothing,
  because `recoveryTable` has no `healthy` row and the `PlanRecovery`
  lookup immediately below fails for that class anyway.

The last two are the same shape — a guard whose condition is already
guaranteed by something upstream — and in both cases the answer was to
*keep* the code and name it, not delete it. That is a distinction worth
making, because the lens these belong to is the one that says deleting is
sometimes the answer. It is the answer when the branch is unreachable and
the seam is closed: the lens registry's cost lookup, deleted last chunk,
had a single writer of both maps and no plausible future second one. It is
not the answer when the guard states an intent at a seam a later edit will
touch. Add a `healthy` row to `recoveryTable` and the guard immediately
starts earning its keep; drop it now, and that future edit silently begins
emitting recovery plans for healthy loops.

Final: **seventy-two live mutants, seventy-two detected**, three retired
with written proofs.

The arc-wide shape holds, and this chunk sharpened one half of it. Eight
batteries in, the differentials still go green on their first run and the
batteries still find that a quarter to a third of the fixtures cannot tell
two answers apart. What changed here is the *kind* of gap: not a boundary
without a case at it, but a fixture whose input never reached the code it
was written for, passing for months underneath four mutants that died to
something else entirely. A fixture's name is a claim about coverage, and
it decays exactly like a comment does.

### Round 1: everything the rendering got right, the argument line got wrong

The r1 adversarial review found nothing in the rendering. It matched all
thirty `print()` sites character for character — the `\n` prefixes, the
two-space gaps, the space after the `:<28` pad that comes from the
f-string concatenation rather than the format spec, the trailing space a
zero-confidence lens leaves — and reported no divergence. Nothing at HIGH.

Every finding was in the ARGUMENT LINE, and every one of the ten it
measured reproduced exactly when checked against CPython. Zero
hallucinated claims in the round, which is not the base rate.

The shape they share: **argparse and Go's `flag` disagree in five places,
and four of the five are silent** — the wrong reading produces plausible
output rather than an error.

- **argparse abbreviates.** A unique prefix resolves to its option, so
  `--lat` is `--latest` and `--hist 5` is `--history 5`. Go's flag rejects
  both. Worse, `splitArgs` did not know `--hist` took a value, so its `5`
  became a positional and would have been read as a loop id.
- **A leading-dash negative number is a POSITIONAL.** No option of this
  parser looks like one, so argparse's `_negative_number_matcher` lets
  `-5` through as a loop id; CPython prints a full `Loop -5` diagnosis.
  Go answered `flag provided but not defined: -5`.
- **`--history` is a truthiness test, and negative is truthy.** The port
  read it as `> 0`. `--history -5` renders exactly one row in CPython —
  `load_diagnoses` breaks the moment `1 >= -5` — where Go fell through
  the branch entirely and diagnosed a loop. The old comment claimed `0`
  "reproduces both the unset case and the explicit zero", which is true
  and beside the point: the sign was the case it never considered.
  Distinguishing unset from zero needs `fs.Visit`, because Go has no None.
- **argparse REJECTS two spellings Go accepts**: a single-dash long name
  (`-latest`) and an explicit value on a store_true flag
  (`--latest=true`). Both exit 2 in CPython and ran normally in Go.
- **Usage went to the wrong stream.** `fs.SetOutput(out)` with `out` =
  stdout meant `maro introspect --bogus > report.txt` wrote a Go usage
  block into the report, where CPython leaves stdout empty and puts
  everything on stderr. And every argparse failure exits **2**, not 1 —
  a code scripts branch on, which the port collapsed. `UsageError` now
  carries it out to `cmd/maro`.

`splitArgs` is a hand-written parser as a result, not a pre-pass over
`flag`, and the option table is DATA — three behaviours read it (prefix
resolution, value consumption, `=` legality) and a set that answered only
the middle question is how `--hist 5` came to parse as a loop id.

### And the tests it broke to prove they were empty

Three of the review's findings came with a mutation the reviewer had
actually applied to a scratch copy and watched survive. That is the
standard this file's own batteries hold, applied from outside, and it
found three gaps the 75-mutant battery had not:

- **The `--history N` window was never exercised.** The only non-empty
  history fixture seeded exactly five rows and asked for five, so the
  limit never cut. Replacing `*history` with the constant `50` left the
  suite green. The `--patterns` fixtures go to real trouble to make their
  50-row window visible; `--history` had none of that care, and the
  battery had no mutant for the argument at all.
- **Two whole branches of `splitArgs` were dead.** No case contained `=`
  or `--`. Deleting the `Contains(name, "=")` guard: green. Deleting the
  entire `a == "--"` block: green. Both are behaviours CPython has —
  `--history=5` prints history, `["--", "--latest"]` diagnoses a loop
  named `--latest`.
- **No fixture could express a CPython refusal.** The probe already
  captured `SystemExit` faithfully, but the assertion treated any exit as
  a test bug — so the entire error-parity surface was unmeasurable *by
  construction*. That is why every argument finding above was invisible
  to a battery that detected 72 of 75 mutants: the mutants could only
  probe what the case struct could describe. A `wantExit` field was the
  whole fix, and six refusal fixtures came with it.

The last one is the lesson. A battery measures the fixtures' reach, and
the fixtures reach only as far as the harness lets them describe an
outcome. 72/75 was a true number about a space with a wall through it.

### Round 2: the wall came down, and the room was bigger than the wall

The `wantExit` field made refusals expressible, and six fixtures went in
behind it. That is where round 1 stopped. Re-arming the battery against
the new argument layer then produced five survivors and one BUILDFAIL —
and chasing the first of them is what showed the field had opened the
door onto a much larger room than anyone had looked into.

`C78 a single-dash long name is accepted` was the survivor that gave it
away. Deleting the guard that refuses `-latest` left every fixture green,
because the mutant still *refused* the input — it fell through, cut the
token two characters in, failed to resolve `atest`, and raised a
different usage error. The harness could now say "CPython refuses this",
which both spellings satisfied. It could not say **how**. So the wall had
not come down; it had moved one step back.

The fix is the same shape as the last one, one level deeper: compare
stderr in full — usage block, `prog: error:`, message and all — and
compare the exit code as a number rather than a boolean. That required
CPython's `--help` and error text on the Go side, which required
rendering argparse's usage and help blocks verbatim, which is what makes
every refusal distinguishable from every other refusal.

And then, with the messages actually being compared, the *behaviours*
had to match. They did not, in eight more places nobody had named.

### Eight more disagreements, all measured

Nothing below was reasoned about. Each line is a CPython 3.14.3
transcript, because the one thing round 1 proved beyond argument is that
reasoning about argparse produces confident wrong answers — the reviewer
who found the first six ran the interpreter for every claim and hit ten
for ten, against a remembered 30–50% hallucination rate for review
findings that are merely argued.

- **`-hh` prints help and exits 0.** So does `-hx`, and `-h5`. A glued
  tail on a single-dash flag is not an error: argparse re-reads it as
  more single-dash options, and the help action fires and exits before
  the tail is ever looked at. The port refused all of them.
- **`-1latest` is a loop id.** CPython 3.14 spells
  `_negative_number_matcher` as `-\.?\d` and applies it with `.match`,
  which anchors only at the *start*. Every token beginning with a dash
  and a digit is a positional. The port carried `^-\d+$|^-\d*\.\d+$` —
  which is the **3.11** spelling, correct against a different
  interpreter than the one it is tested against. `\d` is also Unicode
  aware in a Python str pattern, so `-٥` takes the same branch and Go's
  ASCII-only `\d` would not.
- **`--history " 5"` is 5.** Python's `int()` strips surrounding
  whitespace and allows PEP 515 underscores. `strconv.Atoi` does
  neither. `pyval.Int` already carried the whole rule, including the
  named residual that CPython also accepts non-ASCII digits.
- **An unrecognized option is not an error where it appears.** It is set
  aside and reported at the very end, after any error the rest of the
  line produces — so `--bogus -h` prints help, and `--bogus --history`
  complains about the missing argument, not the unknown flag. The port
  returned at the first bad token.
- **An ambiguous option IS an error where it is consumed.** Which is a
  different point in the line than where it is classified, and the
  difference is visible: `-h --l` prints help, `--l -h` is an error.
  argparse builds the whole O/A pattern first without ever failing, then
  consumes left to right. Two passes, and the order is observable.
- **A value-taking option will not swallow an option-looking token.**
  `--history --tory` is a missing argument, not a value of `"--tory"`;
  `--history --` is missing too, because the terminator classifies as
  neither. But `--history -` *is* consumed, because a lone dash is a
  positional — and then fails the int conversion instead. Two adjacent
  inputs, one character apart, with two different messages.
- **Only the first `--` terminates.** A second one after it is an
  ordinary token, so `maro introspect -- --` diagnoses a loop whose id
  is two dashes. The terminator itself never appears in the extras:
  `a -- b` reports `b`, not `-- b`.
- **The message names the action, not the spelling.** `-h=x`,
  `--help=x` and `--hel=x` all report `argument -h/--help`, because
  `_get_action_name` joins every option string the action owns. And
  `--=x` reports the token *as typed* while listing all five long
  options it could have meant.

The port that came out of this is not a pre-pass over `flag` any more.
`flag` is gone from the file. What replaced it is `parseOptional` /
`optionTuples` / `parseIntrospectArgs` — a port of `_parse_optional`,
`_get_option_tuples` and the consume loop, structured the way CPython
structures them, because the approximation kept being wrong in places
that only an enumeration would find.

Two conjuncts of argparse's own re-read condition are deliberately *not*
written out, with the proof in a comment: `sepConcat` is produced only by
the single-dash arm, and only for a token longer than the option string
it matched, so "the option is single-dash" and "the tail is non-empty"
cannot fail there. Writing them anyway would be two more guards that
cannot fire — L4, in a file that now has enough of them.

One difference is PINNED rather than fixed. argparse re-wraps usage and
help to `shutil.get_terminal_size().columns - 2`; the constants here are
the 80-column rendering, which is what CPython produces whenever stdout
is not a terminal — a pipe, a redirect, a CI log, every scripted
invocation — and what it falls back to when the width cannot be
determined. The differential exports `COLUMNS=80` so both sides are
pinned to it. In a terminal of some other width CPython rewraps the
usage line and the port does not. Porting `HelpFormatter`'s usage
grouping buys one cosmetic line at non-default widths and risks the
common case; this is the trade, written down.

The second pinned difference is arithmetic: Python ints are arbitrary
precision, so `--history 99999999999999999999999` is a valid enormous
limit there and an overflow here. The port **saturates** rather than
inventing a refusal CPython does not make — `load_diagnoses` runs out of
store long before either number matters, so the rendering is identical,
and there is a fixture that says so.

### What the battery says now

The argument-layer mutants were rewritten against the new source — the
old ones named `splitArgs`, which no longer exists, and would have
reported SITEFAIL rather than a result. Twenty-seven take their place,
one per rule above plus the message-rendering ones (`pyval.Repr` versus
Go's `%q`, the usage block before the error line, the action label, the
ambiguity token). **95 mutants, 92 detected**, and the three that are
not are the three proven-unkillable ones carried forward with their
proofs (`padLeft` counting bytes at two ASCII-only call sites, the
graduation marker's tautology, the `healthy` guard subsumed by the
lookup below it).

Three of the new mutants reported BUILDFAIL on their first spelling and
all three for the same reason, which is worth writing down once: **Go
does not count an assignment as a use.** Replacing `if !loopIDSet` with
`if true`, or deleting the `case o.str == shortPrefix` arm, orphans a
variable and the package stops compiling — a fault in the battery, never
a survivor. The mutations were re-spelled to keep the read (`if
loopIDSet`, `shortPrefix := arg`), which tests the same claim. A fourth
reported BUILDFAIL by orphaning the `errors` import, and became
`errors.Is(err, nil)` — false for any non-nil error, so the same
always-take-this-branch mutation with the import still live.

`C84 history-supplied is not tracked` from the previous run is retired
as a **bad mutant**, and the reason is worth keeping. It replaced
`historySet && *history != 0` with `*history != 0`, and it was
unkillable: the flag's zero default made the two expressions equivalent
at every input. The dead conjunct was real, and the answer was not to
delete it — it is the None-vs-0 distinction argparse actually makes.
Modelling `history` as a `*int` made the distinction structural instead
of decorative, and the same mutant is now a nil dereference. A guard
that cannot fire is sometimes a guard written in the wrong type.

### Round 3: every decision was right and the program was still wrong

Round 2 deleted Go's `flag` package and ported argparse's rules. Round 3
found five more divergences, and not one of them is a rule I got wrong.
They are all the same defect: I ported argparse's *decisions* and wrote
them in a control flow of my own.

CPython's `consume_optional` is a `while True`. It collects actions into
`action_tuples` and takes **none** of them until the whole token is
understood. My version took each action as it found it. Every branch
agreed with CPython's; the deferral did not exist. So:

```
$ maro-introspect -hh=x
usage: maro-introspect [-h] [--latest] [--lenses] [--history N] [--patterns]
                       [loop_id]
maro-introspect: error: argument -h/--help: ignored explicit argument 'x'
$ echo $?
2
```

where the port printed the help screen and exited 0. `-hh=x` resolves its
first tail character to a second `-h`, and the `=x` left over is an
explicit argument a flag cannot take. The help action *is* collected —
it is simply never run, because the token turns out to be an error. Five
inputs of that shape (`-hh=x`, `-hh-x`, `-hh=`, `-hh-`, `-hhh=x`) were
wrong together.

Worse than the bug: I had written a comment asserting the opposite.

```go
// Falls through and takes the action. The tail would become
// the next option string to look up — but `-h` is the only
// single-dash option this parser has, and its action exits
// before the tail is ever read.
```

That is the flattening stated as a fact about CPython. It is false —
`take_action` runs after the loop breaks, always — and it is the reason
the shape survived a round of review and a 92/95 battery. A comment
explaining why the original's extra structure is unnecessary here is the
finding, not the justification (L28, and now L48).

### The terminator does not have a rule of its own

The second one has the same shape. My consume loop had `case
kindSeparator: continue` — skip the `--`, it is a marker, not a token.
CPython has no such case. `--` never reaches `consume_optional` at all;
it falls through to `consume_positionals`, whose nargs pattern for
`loop_id` (an `nargs='?'` positional) is `(-*A?-*)` matched against the
classification string. The terminator is removed only when a positional's
matched span happens to *cover* it:

| argv | pattern | span | extras |
|---|---|---|---|
| `a -- b` | `A-A` | `A-` | `b` |
| `a b -- c` | `AA-A` | `A` | `b -- c` |
| `a b --` | `AA-` | `A` | `b --` |

One line of "skip the separator" is right for the first row and wrong for
the other two, and for three more shapes past an option. Six argument
lines disagreed with CPython, all silently — the exit code was already 2,
so only the message differed, and until round 2 taught the harness to
compare stderr in full, nothing could have seen it.

The fix in both cases was to stop paraphrasing. `consumeOptional` is now
argparse's `while True` with its `action_tuples`; `consumePositionals` is
its span match and its `args.remove('--')`; `matchArgument` is
`_match_argument` for the two nargs this parser uses. Longer, and the
five inputs are right for the reason CPython gets them right.

### Three smaller ones, and a comment that was exactly backwards

`_get_option_tuples`' single-dash arm tests the abbreviation against the
part **before** the `=`, not the whole token. So `-=x` cuts to `-`, which
every option in the table starts with, and CPython reports

```
maro-introspect: error: ambiguous option: -=x could match -h, --help, --latest, --lenses, --history, --patterns
```

where the port said `unrecognized arguments: -=x`. Five tokens of that
shape.

The reviewer also disproved a comment I had written defending the
exact-option-before-`=` block in `parseOptional`:

> KEPT although no input of THIS parser can tell it from the prefix
> search below…

Deleting the block **fails the suite**. `-h=x` is exact there and comes
out `sepEquals`, which is what makes the consumer refuse it; without the
block the same token resolves through `optionTuples` as `sepConcat` and
prints help. The code was load-bearing and the justification was wrong —
the same defect as the `-hh` comment, in the opposite direction. Both are
now corrected in place rather than merely fixed around.

And `pyval.Int`'s ASCII-only digit scan remains the one named residual:
`--history ٥` is a usage error where CPython reads 5. Re-measured this
round, unchanged.

### What the battery says now, and how it was derived

The round-2 battery scored 92/95 and missed all five of these, which is
the most useful thing about it. It was derived from *the previous
review's findings list* — the inversion of L9, one level up from the diff
it warns about. Every rule nobody had listed went unmutated: the `\.?` in
the negative-number matcher, the negative overflow bound, the
terminator's fate in the extras, the single-dash `=` cut, the re-read
loop.

Round 3's argument mutants were derived by walking the file's decision
sites top to bottom. That produced **17 new mutants** on sites that had
none — the battery now stands at **115 mutants, 112 detected** — and six
sites deliberately left unmutated with the reason written next to them
(L8): the `runeAt(optionString, 1) != "-"` term and the `explicit != ""`
term, both of which raise the identical message with or without them in
*this* parser; the extras append in the unresolved-tail branch, only
reachable through `-h<tail>` and so always ending in the help action
exiting; rune-vs-byte slicing for the short prefix, where a multi-byte
first token matches no option either way; the ORDER the collected actions
are taken in, since every action a re-read collects is the help action;
and first-vs-last of the positional span, since `-*A?-*` admits at most
one `A` and only the first `--` classifies as `-`, so after the removal
the span holds one token and the two spellings coincide.

The last two of those six were *written as mutants first* and retired
after the run reported MISS — which is the honest way round. A survivor
is a question, and the answer is sometimes "this mutant cannot change an
answer". The three carried-forward survivors from earlier rounds
(`padLeft` counting bytes at two ASCII-only call sites, the graduation
marker's tautology, the `healthy` guard subsumed by the lookup below it)
are unchanged and still carry their proofs. An unexplained absence and a
considered exemption look identical six weeks later, so all six are
written down in the battery next to the sites they cover.

Three mutants reported BUILDFAIL on their first spelling, all for the
reason this file has now recorded three times: **Go does not count an
assignment as a use.** Dropping `prefix` from the abbreviation case
orphans it; `if false` orphans the `pytext` import. Each was re-spelled to
keep the read while making the same change (`&& prefix == arg`, and a `+`
prefix a negative overflow can never carry).

Twenty-two fixtures were added, every one of them a CPython transcript:
the five `-hh…` refusals, the five `-=…` ambiguities, five terminator
shapes, `-hx`/`-hhh`, `--latest` with a loop id (which pins the first
term of `args.latest || args.loopID == ""` — deleting it had left the
suite green), `-a b` (the space rule, which had a comment and no
fixture), `-.5` (the `\.?`), and the negative overflow bound. One
byte-identical duplicate fixture was removed, along with the last mention
of `splitArgs` anywhere in the Go tree — a comment describing a branch of
a function round 2 had deleted.

### Round 4: the chunk is exact, and the wrapper around it was untested

Round 4 fuzzed **~30,000 argument lines** Go-against-CPython — every
single token, all 9,216 pairs, and 12,000 random lines of length 1..5 over
a 96-token alphabet built to stress each argparse rule — plus 840
rendering comparisons over 120 randomized stores. The only divergence in
any of it is the non-ASCII-digit `int()` residual named below. After three
rounds of finding rules, the argument layer is exact.

It also **measured the six exemptions** the round-3 battery had written
down as arguments. Each was turned into a real mutant and re-fuzzed over
11,620 lines; all six produced 0 diffs. Those comments now carry the
number instead of the reasoning, because an exemption that has been
executed and one that has been reasoned about look identical in a comment.

What it found instead was one layer out.

`runIntrospect` is a wrapper whose entire job — stated in its own comment
— is mapping a usage error onto argparse's stderr block and exit code 2.
It had **no test**. The differential next door wrote its own copy of that
mapping:

```go
switch {
case errors.As(err, &ue):
    gotCode, gotErr = 2, ue.Stderr()
```

…and compared *that* against CPython. So both sides of the comparison
belonged to the test, and the binary was not in the picture. Proved by
mutation: `os.Exit(2)` → `os.Exit(1)`, and `ue.Stderr()` → a bare
`"error\n"`, each left the whole suite green.

The fix is one production function with two callers:

```go
func ExitStatus(err error) (stderr string, code int, handled bool)
```

The differential now asserts the mapping the operator actually runs, and
`cmd/maro/introspect_test.go` re-execs the test binary as a child for the
two claims only the wrapper makes — *stderr, not stdout* and *exit 2*.
Five mutants cover it; before this round every one of them was a MISS.

One of the five then survived, and it was the fix's own new code. `Main`
returns a `*UsageError` or nil and nothing else, so `ExitStatus`'s
`handled=false` arm cannot be reached from the CLI at all — flipping it to
`true` passed the whole suite. That is L4 inside a round-4 fix, which is
P6's shape.

Deleting the arm is the wrong answer here, though, and worth saying why:
without it a `Main` that later grows a real error return — an unreadable
store — would have that error swallowed by the wrapper and reported as a
clean exit. A failure that fails OPEN is worse than an unreachable guard.
So the guard stays and gets pinned directly, with a synthetic error rather
than an argument line, because the input it needs is one the CLI cannot
produce. **The battery now stands at 120 mutants, 117 detected**, and the
three survivors are the same carried-forward equivalents as before.

### Two comments that named the wrong interpreter

Both L47, and the second is the one worth remembering.

The round-2 fix for `negativeNumber` carried a comment saying the anchored
`^-\d+$|^-\d*\.\d+$` spelling held "through CPython 3.11". Measured:
`python3.12` still carries it, so the change landed in **3.13**. A version
claim written while fixing a version bug is not automatically measured.

Worse: **python3.12 is installed on this box**, and `pyprobe` invokes bare
`python3` off PATH. The port is correct for whichever interpreter PATH
resolves to (3.14.3 today) and would fail its own differential under the
other one — an interpreter one PATH entry away. The comment now says so,
and says to check `python3 --version` before checking the code when those
cases go red.

The third instance is the same shape one layer down. `_get_nargs_pattern`
*builds the positional pattern and then strips* the `-*` runs through
3.12; 3.14 *selects* `'([A])' if option else '(-*A-*)'` directly. Identical
result for the two nargs this parser uses — and `matchArgument`'s comment
described 3.12's mechanism beside code differentially tested against 3.14.

### A residual pinned by prose, and a deferral whose reason expired

Two findings with no lens between them, and both are about the difference
between a decision and a gap.

The non-ASCII-digit divergence (`--history ٥` is 5 in CPython, a usage
error in Go) had been named in a fixture comment and re-measured by hand
every round. Prose does not go red. It now has a **known-gap pin**
(`TestNonASCIIDigitIsAKnownGap`) asserting the divergence in both
directions: it fails if Go starts accepting the digit *and* if CPython
stops reading it, and it tells whoever hits it to delete the test and move
the two lines into the main table.

And `DiagnoseLoop` skips Python's captain's-log DIAGNOSIS write with the
rationale that it "belongs with the captain's-log port". **That port
landed.** `record.Recorder.EventNoted` writes `memory/captains_log.jsonl`
and graduation and scans already use it, so for three rounds an unclosed
gap read as a considered decision. Corrected at both sites — `DiagnoseLatest`
carried the same expired sentence — and filed with the shape it has to
reproduce (the summary f-string, four context keys,
`note=recommendation[:200]` as a code-point clip, and the bare
`except Exception: pass`).

The related test claim was unpinned in the same way. The differential's
header explains that each runtime gets its own store copy *because Python
writes here and Go does not* — and nothing checked the Go half: `goWS[i]`
went into `Main` and was never looked at again. It now snapshots the
workspace before and after every case. Closing the captain's-log gap will
turn that pin red, which is the pin working.

## metrics' step-cost ledger: two reverse readers that are not the same reader

`src/metrics.py`'s spend half — `_reverse_readline`, `spend_today`,
`spend_for_loops`, `load_step_costs`, `analyze_step_costs`,
`estimate_loop_cost` — is the cross-run budget gate. Per-run caps live in
the loop; this is what stops an unattended substrate from burning a day
one under-cap run at a time.

The interesting thing about it is that the port already had a reverse
line reader. `jsonl_utils._iter_lines_reverse` was ported chunks ago, and
it is *almost* this function. Reusing it would have been the obvious
move and it would have been wrong in two places at once:

- `metrics._reverse_readline` drops empty lines **inside** the loop
  (`for line in reversed(lines[1:]): if line: yield`), where the
  jsonl_utils version filters somewhere else.
- Its trailing `if leftover:` flush is **reachable**, where the
  equivalent block in `jsonl_utils` was proved dead and removed. The
  final iteration assigns `leftover = lines[0]`, which is the file's
  first line — so a file whose first line is non-empty always has one
  more line to yield after the loop ends. The proof that killed the
  other one does not transfer, because the two functions arrive at that
  line by different routes.

Two functions that look like the same function, in the same repository,
with a dead branch in one and a live branch in the other. `ReverseReadline`
is a second implementation on purpose, and the comment says so.

### `line[:60]` is a code-point slice and a substring test

`spend_today` scans backward and stops at the first row that is not
today's, which makes the cheap check load-bearing:

```python
if today not in line[:60]:
    break
```

Three separate things a port gets wrong here. It is a **slice**, not a
prefix, so `today` may appear anywhere in the first sixty characters —
including inside a `step_text_preview` that happens to quote a date.
It is sixty **code points**, not sixty bytes, so a row with a multibyte
preview reaches further into the line in Go than a byte slice would.
And the asymmetry matters: a false positive costs one wasted
`json.loads`, while a false negative ends the scan early and silently
under-reports the day's spend to a budget gate. `pyval.Clip` does the
code-point slice; `strings.Contains` does the substring test.

The authoritative check underneath is a different question again —
`str(e.get("recorded_at","")).startswith(today)` — so a row that passes
the cheap check and fails the real one is skipped without ending the
scan. Both are ported, in that order.

### A `None` dict key is a value, not the string "None"

`analyze_step_costs` groups with
`by_type.setdefault(e.get("step_type", "general"), [])`. The default only
fires when the key is **absent**; a row carrying `"step_type": null`
groups under Python's `None` — which is a perfectly good dict key, and
is *not* the string `"None"`. A Go map keyed by string cannot tell those
apart, and the difference is visible in the output: `by_type` is
returned to callers and the type names are rendered.

So `StepCostAnalysis` carries `ByType map[string]TypeStat` for lookup
plus `ByTypeOrder []any` and `ExpensiveTypes []any` for the keys as
VALUES, with `pyval.HashKey` doing the identity and `StatFor(key any)`
reading through it. This is the same machinery the earlier dict-key
chunks landed, and the reason it exists is exactly this: a store written
by two runtimes will eventually contain a null where a string was
expected.

### The lower median, and two rounding precisions that are both right

```python
median_avg = sorted(avgs)[max(0, (len(avgs) - 1) // 2)] if avgs else 0
```

The comment in the Python calls it out — floor((n-1)/2) is the **lower**
median, so at n=2 it is the *smaller* of the two values, and the
`> 2 * median` test is against the cheaper half. An off-by-one here does
not crash; it changes which step types an operator is told are
expensive.

And `analyze_step_costs` rounds at two different precisions in the same
return value: `round(total_cost / count, 8)` for a per-type average and
`round(total_cost, 6)` for the grand total. That is not a typo in either
direction — a per-step average is small enough to need the extra digits
and a total is not — so the port carries both, which means it also
carries Python's banker's rounding at both.

`estimate_loop_cost` has one thing worth naming and it is not a
divergence: when a step's type has no recorded average it falls back to
the mean of the per-type means — recomputing that list **inside the
loop**, once per fallback step. Same answer, O(n·types) to get it. The
port keeps the shape (a faithful port is not the place to optimize) and
the comment records that the "global average" the docstring names is an
unweighted mean of type means, not a mean over entries.

### The battery

`stepcosts_diff_test.go` runs five differentials, all against CPython:
`ReverseReadline` over ten file shapes × eight buffer sizes (80 cases —
the buffer boundary is where a chunked reverse reader breaks, so the
sweep is over sizes that split lines at every offset), an early-stop
case, `SpendToday` over eleven fixtures including a row stranded past
the sixty-character window and a multibyte preview inside it,
`SpendForLoops` over ten, and `AnalyzeStepCosts` over fifteen.

Two real port bugs died on the first run, both of them type identity
rather than arithmetic: a numeric `loop_id` did not match, because
`encoding/json` makes every number a `float64` and `str(12.0)` is
`"12.0"` where `str(12)` is `"12"` — fixed by reading through
`pyval.LoadsMap`; and the null `step_type` grouped as `"None"`, which is
what the `[]any` order slices exist to prevent.

The timestamp census also fired on this file, for
`.Format("2006-01-02")`. It is exempted with the Python read attached:
`metrics.spend_today` computes
`datetime.now(timezone.utc).date().isoformat()` at `src/metrics.py:227`,
which is a comparison KEY matched against the prefix of each row's
`recorded_at` — not a stamp it writes. `pyval.NowISO` there would never
match anything. An exemption someone has to write down is the point.

### The writer half, and a store both runtimes append to

`record_step_cost` / `successful_run_cost_p90` / `tail_cost_scope` are the
other side of the same ledger, and the side where a mistake is durable: a
reader that is wrong gives a wrong answer once; a writer that is wrong
puts a wrong answer into a file the other runtime reads back forever.

Three details the differential exists to pin, none of which is arithmetic:

- **The id is `str(uuid.uuid4())[:12]`, and that slice is taken from the
  HYPHENATED spelling.** Eight hex digits, a hyphen, three more — not
  twelve hex digits. A generator producing twelve hex characters looks
  right in every eyeball check and matches nothing keyed on the shape.
- **The row's key order is the wire format.** Python builds a dict
  literal and `json.dumps` preserves insertion order, so the port's
  struct field order IS the JSON. `estimated_cost_usd` is the one
  conditional key, written only when a provider figure displaced the
  estimate — which is what keeps the two pricing lanes distinguishable in
  the store rather than by inference.
- **`json.dumps` defaults to `ensure_ascii=True`.** The row on disk
  escapes every non-ASCII rune to `\uXXXX`. This is where the test itself
  was wrong first: the probe serialized the returned dict with
  `ensure_ascii=False` and compared it against the line the writer had
  put on disk one statement earlier, so the differential's first failure
  was the harness comparing the port against a spelling of the row that
  nothing in the system ever writes.

`max(0.0, float(provider or 0.0))` clamps BEFORE the `> 0` test, which is
what makes a negative provider figure fall through to the estimate rather
than displace it with a negative cost. The two languages' `max` disagree
on NaN — CPython replaces its running maximum only when `item > current`
and `nan > 0.0` is False, so `max(0.0, nan)` is `0.0`, where Go's
`math.Max` propagates the NaN — and it is unobservable here, because both
then fail `provider > 0` and `provider` is read nowhere else. Written
down rather than normalised: a normalisation would be a line no input
could exercise.

`successful_run_cost_p90` returns Python's `Optional`, and the bool is
not decoration. "History too thin" is not 0.0, and a caller that
collapsed them would gate every run. The index is
`vals[int(0.9 * (len(vals) - 1))]` — `int()` truncates, so it is a floor
and not a round, and it indexes the sorted list directly rather than
interpolating: at n=8 that is the seventh smallest, not the eighth. There
is a fixture at exactly the sample floor for that reason.

The cache has a property worth a test of its own. Python's guard is
`if _hit and _now - _hit[0] < TTL`, and a cached "no opinion" is stored
as the tuple `(t, None)` — which is TRUTHY. So a None answer is served
from cache for the full fifteen minutes, and a run that finishes in that
window does not change the answer. The port keeps that (a `cached` flag,
because a Go zero-value struct cannot express the difference between
"absent" and "cached None") and pins it, along with the fact that two
different limits are two different questions and do not share an entry.

One deliberate divergence, named: Python's `sorted(..., key=st_mtime)`
raises if a card vanishes between the glob and the stat, and the outer
`except` turns that into `None` for the next fifteen minutes. The port
skips the missing card instead. A run cleaned up mid-scan should not cost
the budget gate its opinion.

**Withdrawn in round 1, and the reason is the interesting part.** That
argument was true only because the port cached the answer, which CPython
does not for that exit. Once the two exits were restored the "next fifteen
minutes" in the sentence above stopped existing, the divergence had nothing
left to buy, and it was deleted. See the round-1 section below.

`tail_cost_scope` is a ContextVar in Python, chosen over a global
precisely so concurrent runs' tails cannot cross-attribute. Go has no
implicit propagation, so the equivalent is a `context.Context` value: it
flows down and it does not flow sideways, which are the same two
properties. The cost is that a Go caller must thread the ctx — visible
where Python's was invisible, and the honest spelling, since a tail phase
that forgets to propagate records nothing in Python either. It just has
nowhere for a reader to notice.

### P4, amended by a wasted run

The battery over the rewritten argument layer reported twenty-eight
consecutive BUILDFAILs, every one of which had been individually DETECTED
minutes earlier. The cause was a new file being written in
`internal/metrics` while the battery ran over `internal/introspect`, on
the reasoning that `go test ./internal/introspect/` does not compile
another package.

It does. `internal/introspect` imports `internal/metrics`. A Go module
builds through its import graph, so the tree a battery owns is every
package the package under test can reach — which is not a set worth
estimating. While a battery runs, the working tree is off limits. Prose
is not: `docs/`, `review/` and this file are in no build graph, and that
is the whole of the exemption.

### Round 1: the flattening had grown a divergence to justify itself

Fifteen confirmed findings, two self-retracted, one hallucinated claim
about `read_jsonl_tail` that P1 killed before it cost a fix.

Three were HIGH and two of those are the same defect seen from both
sides. `successful_run_cost_p90` returns `None` in two places and caches
only one of them — `if not root.is_dir(): return None` sits above the
cache write, everything else below it. The port had a single `return` and
cached whatever came back, so a workspace that had not run anything yet
answered "no opinion" for fifteen minutes after its first run landed. The
budget gate reads this to set its auto thresholds; a fresh install's first
quarter hour ran ungated.

The test that should have caught it instead pinned it. `TestRunCostP90-
CachesByLimit` asserted "a cached no-opinion answer was recomputed" on an
EMPTY WORKSPACE — the one no-opinion branch CPython does not cache. It was
measuring the wrong branch under the right name, which is L1 in its purest
form: the assertion was true, the fixture could not reach what the
assertion was about, and the test would have gone on being green forever.
It now drives both branches, in separate workspaces, saying which is which.

And then the part worth remembering. Twenty lines below the flattened
return sat a NAMED, DELIBERATE divergence: CPython's mtime sort raises
when a card is deleted mid-glob, and the port skipped the card instead,
under a comment arguing that losing the gate's opinion for fifteen minutes
over one cleaned-up run was the worse trade. That argument was correct.
It was also only reachable because the flattening cached that answer.
Restoring the second exit made the trade vanish, and the divergence was
deleted rather than re-defended. A flattened control flow does not only
lose the original's shape — it creates pressure to diverge elsewhere, and
the local justification for that divergence is genuine, which is why it
survives review. L48 now says to look for exactly this pairing.

The third HIGH was `estimated_cost_usd` spelled `float64` with
`omitempty`. Python writes the key only when a provider figure displaced
the estimate, and `tests/test_metrics.py:593` asserts its ABSENCE as the
estimate-lane marker — so presence is the signal, and `omitempty` cannot
express "present and zero". The port's comment defended the choice by
asserting that no caller produces a zero estimate beside a positive
provider cost; `llm.py:834` passes `input_tokens or 0`, so a provider that
prices a call without reporting token counts produces exactly that row.
Fourteen keys where CPython writes fifteen, and a presence-keyed reader
counts priced spend as unpriced. Now a `*float64`.

### `sum()` is not the loop you would write

`analyze_step_costs` sums the store's RAW values — `sum(e.get("cost_usd",
0.0) for e in entries)`, no `or 0.0`, no `try` anywhere in the function —
so one row carrying `null` or `"1.5"` raises TypeError out to the caller.
The port had used `spend_for_loops`' coercing expression, which lives four
hundred lines away and DOES have the `or 0.0`, and so answered with a
plausible number where CPython crashed. `AnalyzeStepCosts` and
`EstimateLoopCost` now return an error, the three raise sites fire in
Python's order (unhashable step_type, then `total_tokens`, then
`cost_usd` — each summed over the whole group before the next begins), and
the coercing helper was deleted rather than left unused, because a helper
that reads right and is wrong is how it got used there to begin with.

Writing `pyval.Sum` to replace it turned up something nobody was looking
for. CPython's `sum()` has used Neumaier compensated summation since 3.12:

```
sum([1e100, 1.0, -1e100])        -> 1.0     a fold gives 0.0
sum([0.05, 0.01, 0.01, -0.07])   -> -3.469446951953614e-18
                                            a fold gives 0.0
```

The second line is four ordinary cost rows. `round(x, 6)` spells CPython's
residue `-0.0` and the fold's `0.0`, and both runtimes append to one
ledger. It surfaced only because a fixture added for an unrelated finding
— floor division on a negative group total — happened to sum to
approximately zero; four other costs and the folded `sum` would have
shipped. That near-miss is now L49: a builtin's implementation exceeds its
definition, and the differential goes in before the implementation, with
an input chosen to be ill-conditioned for the obvious algorithm.
`pyval.Sum` reproduces the compensated loop, the int→float lane crossing,
the non-finite discard (`sum([1e308, 1e308, -1e308])` is `inf`, not `nan`)
and all seven TypeError messages, pinned against the interpreter.

### Four more in one function, and a third copy of one helper

`computeRunCostP90` alone carried four of the remaining findings, which is
what a function looks like when its shape was rewritten:

- `rows[:limit]` PANICS on a negative limit, under a doc comment promising
  the function never raises. The fix was not a third private slice helper:
  `pyval.Clip`'s own comment already named "two implementations of one
  Python operation disagreeing" as the defect, and `internal/orch` had a
  private `pySliceLen` doing it right. Promoted to `pyval.SliceStop`.
- `encoding/json` where the port has its own Python-shaped reader.
  `json.loads` admits the bare tokens `NaN`, `Infinity`, `-Infinity`; Go's
  decoder rejects them, so a card carrying `"total_cost_usd": NaN` was
  dropped here and admitted there, where it makes the p90 itself `nan` and
  every downstream budget comparison false.
- `if !ok || f == 0` applied truthiness AFTER `float()`. Python's `if
  cost` tests the RAW value: the string `"0"` is truthy and floats to a
  real 0.0 sample, and `"abc"` is truthy and raises out of the function.
  Two inputs the port answered 60.0 and 70.0 on, against CPython's 0.0-
  sample and uncached `None`.
- `filepath.Glob` sorts lexically; `Path.glob` yields DIRECTORY order and
  never sorts. Both mtime sorts are stable, so the arrival order IS the
  tiebreak, and the tiebreak decides which of two same-mtime cards
  survives `[:limit]`. Sixteen tied cards at limit=8: 700 here, 1300 there.

`spend_for_loops` was the fifth of that class. It read bytes where Python
opens the file in TEXT mode with `encoding="utf-8"` inside a bare
`except`, and the `for line in fh` loop has no break — so one crash-torn
byte anywhere in the file makes CPython answer 0.0 for the WHOLE file,
where the port answered with every other row's spend.

### The probes were writing without the guard

Five metrics probes each set `os.environ["MARO_WORKSPACE"]` in their own
loop, because `pyprobe.Probe.Workspace` is one path and a table-driven
differential has one tree per case. The consequence is that pyprobe's
realpath live-workspace refusal never ran for the probes that WRITE. One
of them substituted a hand-rolled `assert "/.maro/" not in ws` on the
UNRESOLVED path — the exact string comparison `liveGuard`'s own comment
says a symlinked temp dir walks past — and one carried no guard at all.

The fix is not more discipline in five snippets. `pyprobe` grew a
`Workspaces []string` field, checked Go-side the way `Workspace` is, and a
`_pyprobe_use(ws)` door that realpaths both sides before it sets the
variable. A probe now cannot point itself somewhere without visibly not
calling it. This is the 2026-08-16 live-ledger rule finally having one
door instead of a habit.

### Battery gaps: four boundaries with a case on either side and none at it

Four findings were mutants that survived, and every one of them is the
same shape — the corpus straddled a boundary without landing on it:

- `line[:60]` mutated to 59 and to 61 both survived. Closed with two rows
  built by a `datedAt` helper that SIZES the padding and then asserts
  where the date landed, because hand-counting a JSON prefix is how a
  boundary case ends up at 49 under a name that says 60.
- Replacing `spend_today`'s strict `recorded_at.startswith` check with
  `if true` survived: every fixture had the cheap check and the strict
  check agreeing. Closed with today's date in a `goal_preview` beside a
  yesterday timestamp — CPython 1.5, unchecked 9.5.
- `pyval.FloorDiv` → `/` survived, against a fixture whose comment claimed
  to separate flooring from truncation. Its total was −6000 over 2, which
  divides evenly. −5500 over 3 is −1834 against −1833.
- `LoadStepCosts`' `limit > 0` had no case at zero, where `>= 0` slices
  `rows[len:]` and answers empty.

Three of those four are L28 as much as L23: the comment asserted the
coverage, and the assertion was the only thing holding it.

### What the battery says now, and what it said first

The metrics battery walks 131 decision sites in `stepcosts.go`,
`recorder.go` and the three `pyval` helpers the chunk added, top to bottom,
derived from the FILES rather than from round 1's findings list. It scored
**83 detected, 40 survived, 8 malformed** on its first run — against a
baseline verified green, with `-count=1` and an unfiltered second phase, so
the survivors are honest.

Forty is not a rounding error, and where they clustered is the finding:
**nearly every decision in `computeRunCostP90` survived.** The differential
that covers it compares against CPython on thirteen fixtures and asserts a
real value, so it is not a vacuous test — it simply pinned the p90's ANSWER
while leaving the machinery that produces it unmeasured. The index formula
is the clearest case. Every fixture sampled 8, 9, 10, 12 or 20 successful
runs, and `int(0.9 * (n-1))` and the plausible off-by-one `int(0.9 * n) - 1`
agree at all five. Eleven is the first size where they part company. A
fixture list can cover a function's inputs generously and still never ask
the one question that separates two implementations.

Three real defects came out of chasing the survivors, and two of them are
the same shape as round 1's:

**`RUN_COST_SUCCESS_CLASSES` is a tuple, not a set.** The port guarded the
membership test with `pyval.Hashable` and returned "no opinion" when a run
card's `success_class` was unhashable, with a comment explaining that the
`in` would raise. Tuple membership walks the elements comparing with `==`;
it does not hash and it does not raise. The card is skipped and the other
cards still answer. The consequence was not academic — one structured
`success_class` anywhere in the last two hundred run cards made the budget
gate fall back to its static floors, silently. The reading was carried over
from `analyze_step_costs`, where `step_type` genuinely *is* a dict key and
genuinely does raise. Two membership tests twenty lines apart, one of which
raises; the port applied one rule to both. **Read the container, not the
operator.**

**A named divergence defended by a false premise, again.** `costUSDOf`
returned a bare `float64` and scored an unparseable `cost_usd` as zero for
that row, under a comment reading "Go cannot reproduce a mid-loop abort by
returning a float, so that case is named here and left as a known
divergence." Go reproduces it with a second return value. In CPython the
`float()` sits inside both callers' outer bare `except` with nothing
between, so one corrupt row makes `spend_today` and `spend_for_loops` answer
`0.0` for the entire file — not for the row. The port handed the budget gate
the sum of everything that parsed. This is round 1's `computeRunCostP90`
lesson repeating: the divergence was honestly labelled, carefully argued,
and wrong, and the label is what kept it out of review's way. **A comment
explaining why the original's behaviour is unreachable here deserves the
same scrutiny as the code.**

Both failed in the same direction, which is worth naming on its own: a raise
that a port converts into a skip **always fails open**, because the number
it invents is smaller than the one CPython refuses to give.

**A test that was a copy of the thing it tested.**
`TestRowIDFallbackKeepsTheShape` re-typed `newRowID`'s `fmt.Sprintf` into its
own body. It asserted that a format string matched a regex — both of which
lived in the test — and stayed green under every mutation of the literal
that ships. It had been written the round before to catch exactly the bug it
could not have caught. The fallback is now `rowIDFallback`, and the test
calls it.

Four smaller ones followed the same thread: `total_cost_usd` is an INT in
CPython when the store's costs are integers (`round()` keeps the type), and
both the field and the probe's `%.17g` formatter had to change before the
difference was even expressible; the unhashable-key message had been
hand-written twice and the two copies had drifted to *different CPython
versions*, 3.12's wording in one and 3.14's in the other; the `limit=500`
window lives inside `analyze_step_costs`' own `entries=None` default rather
than at its call site, so `AnalyzeStepCosts(nil)` in the port analyses
nothing where Python analyses the last five hundred rows — harmless with
one caller and a trap for the next chunk's.

### Two ways a constant escapes a differential

A TTL, a card limit and an analysis window share a property: they are only
observable through inputs large enough or old enough to straddle them. The
battery moved the cache TTL from 900s to 300s, the card limit from 200 to
100 and the analysis window from 500 to 100, and every test in the package
stayed green, because no fixture waits fifteen minutes or writes two hundred
run cards.

Both halves of the answer are here now, and they are different jobs.
`TestTuningConstantsMatchCPython` reads the numbers out of the module and
compares them — cheap, and it catches transcription drift, which is the
failure that actually happens. It cannot catch a constant that is correct
and then ignored. For the TTL, where "is it used, at the right comparison"
is the whole question, the honest instrument is a clock seam: `runCostNow`
is a package variable, and the test advances it to one second under the TTL
and one second over, so the pair pins the DURATION rather than merely the
existence of expiry.

Reaching for the seam is a last resort in general and the right call here.
The alternative was a fifteen-minute test.

### Round 2 of the battery: 108/139, and the exemptions are executable

Re-running the battery after the first triage needed the battery repaired
first. **Nine of its 131 patterns matched zero times** once the fixes moved
their code, and a zero-match mutant is indistinguishable from a detected one
— the harness's `count(old) == 1` guard is the only thing standing between a
stale battery and a clean-looking sweep over code it never touched. Nine
sites repaired, eight new mutants added for what the fixes introduced (the
raise flag, `RoundAny`, `UnhashableKeyMsg`, the clock seam, `rowIDFallback`,
`globRunCards`), and the whole list re-verified unique before launch.

**108 detected, 21 survived**, against a baseline the harness itself checks.

One more real defect fell out, and it is the third instance in this chunk of
the same shape. `decodeReplace` used `strings.ToValidUTF8`, which collapses a
RUN of ill-formed bytes into one U+FFFD where CPython emits one per **maximal
subpart** — `b"\xff\xff"` is two characters to Python and one to Go. The port
carried this as a named divergence justified by "the difference is only
visible on a torn row, which then fails json.Unmarshal on both sides and is
skipped." The row is skipped. Its LENGTH is not: `spend_today`'s cheap check
is `today not in line[:60]`, sixty CODE POINTS, and a miss there does not
skip the row — it **breaks the backward scan**, so every row below it goes
uncounted. Same failing-open direction as the other two, and the same tell: a
divergence whose justification is a sentence about why the original's
behaviour cannot matter here.

The fix implements the maximal-subpart rule and
`TestDecodeReplaceMatchesCPython` sweeps it against the interpreter — all 256
single bytes, every narrowed continuation range (E0/ED/F0/F4), overlongs,
truncated prefixes, and invalid bytes embedded in valid text — comparing both
the decoded text AND the character count, separately, because the count is
what the sixty-code-point window consumes.

`pyval.Hashable` was **deleted**. Its only caller was the wrong tuple-as-set
guard; removing that left it with none, which is why two mutants of it
survived — nothing could observe it. `HashKey` is the function that was
wanted all along, since it answers the same question and returns the key, so
a caller cannot test hashability and then forget to use the result.

**The exemptions are tests, not paragraphs.** Eight survivors are equivalent
mutants — guards that cannot fire, ported faithfully from Python guards that
cannot fire either. Recording that in a document is a claim about
reachability, which is exactly the kind that reads as true and is false at
one input nobody pictured; this chunk has already produced three such claims
in comments, all confident and all wrong. So each exemption is a test that
searches for the input that would make its guard matter and fails if it finds
one:

- `TestMedianGuardsAreUnreachable` — `max(0, (len(avgs)-1)//2)` never clamps
  and `median_avg > 0` never changes the answer, both because `avgs` admits
  only values > 0. Randomised over 3000 stores. Remove the `> 0` from that
  filter and both guards come alive at once, and this test says so.
- `TestGrandTotalRaiseIsUnreachable` — the fourth sum's error path is dead,
  because the per-type sums PARTITION the entries and run first. The comment
  above that sum used to claim the opposite in as many words.
- `TestEmptyByTypeShortCircuitIsRedundant` — the early return saves a
  traversal; the global-average closure answers 0.0 for an empty store anyway.
- `TestMissingLedgerShortCircuitsAreRedundant` — both the `path.exists()` and
  `if not wanted` guards, with an anti-vacuity call proving the fixture is
  read at all.
- `TestReverseReadlineChunkSizeCannotChangeAnswers` — a chunk size is a read
  pattern, not a rule. The differential already sweeps eight sizes but only
  over small files, so this one crosses the 64KB boundary the constant names.

Three survivors are **not mintable** and are recorded rather than chased.
`M109` needs a run card deleted between the glob and the stat — a race
window, the family already documented. `M101` and `M104` both turn on mtime
TIES: Python's `sorted(key=mtime, reverse=True)` is stable over glob order,
Go's `globRunCards` walks `ReadDir(-1)` in directory order, and neither
runtime promises the two orders agree. A fixture that pinned a tie would be
pinning the filesystem. What IS pinned is that distinct mtimes sort correctly
regardless of name, which the lexical-order-reverses-mtime fixture covers.
`M99` and `M139` are equivalent for a duller reason: both mutants fall
through to a glob or stat that fails on the same input, so the answer is the
same by a longer route.

### Round 3: three fixes that were already written, one file over

The whole-chunk adversarial round returned three MEDIUMs and six LOWs, no
HIGHs. Eight of the nine survived verification against CPython 3.14.3
unchanged. One did not, and it is the more interesting half.

**M1 was right about the class and wrong about its own example.** The claim:
`EstimateLoopCost`'s global average folds with `s += c` where CPython spells
it `sum(all_costs)`, which has been Neumaier-compensated since 3.12. The
demonstration: three costs whose fold and sum allegedly round to 1.234719
and 1.234718. Run directly, both give 1.234719 — the fixture does not
reproduce. The class is nonetheless real and not rare: on 8-decimal per-type
averages the two disagree at the double level about 26% of the time, and
roughly one list in 150 still disagrees after `round(·, 6)`. A counterexample
had to be re-derived, and re-deriving it surfaced something the reviewer's
version would have hidden — **the ORDER is part of the fixture**. `all_costs`
is built in `by_type` order, which `load_step_costs` hands over newest-first,
so the last row in the file is the first cost in the sum. Written the other
way round, the same three values agree and the case proves nothing.

That is the verify-before-fix discipline earning its keep in a way the usual
framing misses. The finding was not hallucinated and was not correct; it was
a true statement about a false example. Fixing it from the report as written
would have produced a passing test that could not fail.

**The other theme: three separate findings are the same fix, not carried.**

- `SpendForLoops` learned in r1 that `path.open(encoding="utf-8")` is a
  STRICT decode, and got `pyval.DecodeUTF8Strict`. `computeRunCostP90` reads
  run cards the same way and never got it, so a card with one bad byte —
  in `goal`, a field nothing here reads — was admitted where CPython skips
  it. Fails open, and in the worst direction: a junk card with a large
  `total_cost_usd` becomes a sample, raising the p90 and with it the 4×p90
  auto kill-line.
- `SpendForLoops` had the strict decode and NOT the other half of text mode.
  `newline=None` translates a lone `\r` to `\n` before iteration, so a
  CR-separated ledger is many rows to CPython and one glued row to a byte
  split. The glued row is invalid JSON, so it is skipped, and the port
  answered 0.0 where CPython answers the real spend. `pyval.ReadText` had
  been doing exactly the right pair the whole time and this call site was
  hand-rolling half of it.
- `globRunCards` used `os.Stat` for BOTH of pathlib's two different tests.
  The directory test follows symlinks, because `is_dir()` does. The CARD
  test does not: measured, `glob("*/run_card.json")` yields a DANGLING
  symlink, because the check is that the name is there, not that the target
  is readable. CPython then reaches `p.stat().st_mtime`, raises
  FileNotFoundError, and the whole distribution comes back None and
  UNCACHED. Go dropped the dead link, answered a confident p90 from the
  survivors, and cached it for fifteen minutes.

The comment above `globRunCards` had described the mechanism correctly for
directories and then applied it to cards, in the same sentence. That is the
sixth time in this chunk that the prose defending a decision WAS the finding.

**And a fix that was wrong on the half its fixtures did not cover.**
`FloorDivAny`'s float lane was `math.Floor(a/b)`; CPython's `float_floordiv`
is fmod-based, and past 2^53 the division rounds across an integer boundary
the remainder does not — `2.543036645110022e16 // 3` is 8476788817033405.0
against 8476788817033407, two whole units. The first attempt at the fix
ported the fmod half and omitted the sign correction, which is the entire
reason `-3001.0 // 3` is -1001.0 and not -1000.0. It passed every positive
case in the new differential and `TestAnalyzeStepCostsMatchesCPython` caught
it immediately. The new fixture table now carries both failure modes
deliberately, because a set that only samples small positive doubles cannot
tell any of the three implementations apart.

Every fix in this round has a differential that was mutation-checked by
reverting the fix and confirming the test goes red.

### The battery was destroyed, and what the logs gave back

The repair script that was supposed to re-target the stale mutants ran

    open(BAT, "w").write("\n".join(src[:start] + body + src[end + 1:]))

with `end` set to `None`, because it located the `M = [` block by counting
brackets and the mutant strings contain `[]byte` and `map[string]any`.
Python truncates on `open` before it evaluates the argument, so the file was
already empty when the `TypeError` was raised. The traceback named the half
of the statement that did nothing. 139 mutants, and the scratchpad is not
under git.

What survived is what had been written somewhere else. The run logs carry
every mutant's NAME and verdict, which recovered the coverage map — 129 of
139 — and none of the mutation strings. Those had to be re-derived from the
files, which is L9's discipline anyway, so the rebuild is not purely lost
work: a battery derived from the current files is what the next round needed
regardless, and the old one had already gone stale twice.

Three things changed in the rebuild, and two of them are improvements the
battery should have had before:

1. **The mutant list lives in its own file.** A bad write of the list can no
   longer take the runner with it. Generated artifacts get written to a temp
   and renamed.
2. **The battery runs against a COPY of the worktree.** Every P4 instance on
   record — the exclusivity tripwires, the wasted 28-mutant run, the suite
   invoked mid-restore comparing against whichever mutation was on disk — is
   a symptom of mutating the tree you also work in. The sibling introspect
   battery had already been cloning to the scratchpad and this one had not.
   The hazard is retired rather than documented.
3. **`--check` is a first-class mode.** Site uniqueness has to be re-verified
   AFTER every fix round, not before it. Two mutants had gone stale from r2's
   own fixes — `decodeReplace` was rewritten in the same commit that repaired
   nine other stale patterns, and no pre-check ran afterwards. A stale mutant
   matches zero sites and scores exactly like a detected one.

### Round 4: 216/269 on the battery, and a fix from round 3 that was itself the bug

The battery finished at **216 detected, 51 survivors** (81%). The r4 review
ran on the whole chunk at an escalated tier and returned two mediums and
four lows. All six code claims verified — 0% hallucination, the fourth
straight round at zero — but three of the six named a mechanism correctly
and an EXAMPLE that did not reproduce. Verification is no longer mostly
about whether a finding is real; it is about whether its demonstration is.

**The round's worst finding was the previous round's fix.** r3 noticed that
`SpendToday` discarded its reader's error and returned a partial sum where
CPython's outer `except` answers 0.0. True, and the fix was right for a
real I/O error. But the comment justifying it named a concurrent
truncation, and that example is backwards: measured, `fh.read()` past a
truncated EOF returns `b""` with **no exception**, so CPython keeps looping
and answers the partial sum. Go's `ReadAt` reports the same short read as
`io.EOF`, so the new check answered 0.0 — a divergence the fix INTRODUCED
on the one input it was written for.

It shipped with no fixture, because neither case is constructible without
losing a race against a writer. That is the whole lesson: the fix was
argued rather than pinned, and the argument was wrong. `reverseReadAt` is
now a seam and both halves have tests — a short read continues, a real
error propagates.

**Second: a supplement that existed and was never carried one predicate
over.** `isWordRune` hand-rolls Python's `\b` because RE2's is ASCII-only,
which the port argues for at length. It still used Go's Unicode **15**
tables against this box's CPython **16**. Swept: 5004 code points are `\w`
to CPython and not to Go, **zero** the other way, and 5002 of them bucket
`classify_step_type` differently. `pytext.Lower` already carried a
`lowerSupplement` for exactly this skew — and it is complete, 0 mismatches
over the entire rune range. The sibling predicate, in the same code path,
never got the same treatment. The existing differential probed `fixé`,
`fix٠`, `fix中`, `fix́` — every one Unicode 15 or older, so nothing in the
battery could see it.

**Third: the obvious spelling of a float key is the wrong one.** CPython
sorts run cards by `p.stat().st_mtime`, a float, and the port compared
`time.Time` nanoseconds. The natural fix — `float64(UnixNano())/1e9` —
still disagrees, at deltas of 127ns, 128ns and 2000ns from a 1.7e9 base,
because it rounds the whole nanosecond count into a double first. CPython
builds the float from the two halves, so the port does too.

**A fixture that could not fail, twice in one test.** The mtime tie test
passed against both spellings until it used a base where the collapse
actually happens (it is exponent-dependent: 127ns collapses at 1.7e9 and
not at a 2026 stamp), and then passed against a reverted comparator until
the tie was moved to 100ns — 127 is the delta that separates the two
SPELLINGS, which is a different question from where CPython TIES. Both
mistakes had the same shape as the one the round was reporting.

**One finding was NOT fixed.** A NaN cost makes the sort comparator
inconsistent, and no Go sort reproduces CPython's answer there: over 153
NaN-bearing lists, `sort.Float64s` disagrees on 136 and `sort.SliceStable`
with a `<` comparator on 78. Matching needs a real timsort, which is filed
as its own chunk with two waiting consumers, and the divergence is pinned
by a test that goes red when it lands.

Battery survivor **M101** (unstable mtime sort) stays open and is recorded
as unresolved rather than equivalent: `sort.Slice` insertion-sorts below
twelve elements and kept the tied pair ordered above it, so a single tie
among distinct keys does not expose instability at any size tried. It needs
a many-ties fixture.

---

## metrics.py — the SystemMetrics half

`compute_metrics`, `get_metrics`, `identify_expensive_patterns`,
`format_metrics_report`, and the three dataclasses behind them. Wired to
`maro metrics [-limit N]`, which is `cli._cmd_metrics`' text lane.

The arithmetic is trivial. What is not trivial is that **nothing in this
path coerces anything**: `load_outcomes` builds `Outcome(**{k: d[k] ...})`
from a JSONL store the Python runtime also writes, applies the dataclass
DEFAULTS, and applies no conversions at all. So the two runtimes have to
agree about which rows are *impossible* as precisely as they agree about
the answers, and a typed Go struct would have decided most of those
questions silently and wrongly.

Six decisions were forced before a line was written, each measured against
CPython 3.14.3 and each with the fixture that fails if it is wrong:

1. **The row is a `map[string]any`, not a struct.** `goal_achieved` is
   tested with `is True` / `is False`, so a stored `1` is UNJUDGED — a Go
   `bool` collapses that. `tokens_in` typed `int` invents a parse CPython
   does not do. `model` typed `string` cannot hold the `0`, `[]`, `{}` and
   `nil` that all collapse to `"unknown"`, nor the `[1]` that raises.
2. **`by_task_type` needs a dict keyed by anything hashable.** `pyval.Obj`
   is ordered but string-keyed. An int `task_type` survives compute and
   kills only the RENDER; an unhashable one raises at the setdefault,
   before anything is priced.
3. **The two accumulators must stay different.** `by_task_type` uses
   `sum()`, which is compensated; `by_model` uses `+=`, which is a naive
   fold. Over eight ordinary rows they disagree inside one returned object
   — `0.14001` against `0.14001000000000002` — and the report prints both
   at `:.6f`, so the divergence is invisible in the output and live in the
   data. Unifying them is wrong in whichever field loses.
4. **Statement order is the contract** (lens P9). The per-type loop is
   total → done → avg_elapsed → avg_in → avg_out → total_cost, and
   `identify_expensive_patterns` runs after the map is built. One row with
   `tokens_in: "5"` raises `+: 'int' and 'str'` through `compute_metrics`
   and `'<' not supported between instances of 'str' and 'int'` through
   `identify_expensive_patterns` — because the second prices before it
   averages. A port that priced first would return every correct value and
   every wrong error.
5. **`estimate_cost` has THREE distinct errors in the untyped lane**, one
   per parameter: a bad `tokens_in` dies inside `min(cache, tokens_in)`
   naming `'str' and 'int'`; a bad `cache_read_tokens` dies in the same
   `min` with the operands the other way round; a bad `tokens_out` survives
   the clamp entirely and dies at the multiply with
   `can't multiply sequence by non-int of type 'float'`, naming neither
   operand. A single generic TypeError matches none of them.
6. **The render has three conversions a reasonable port gets wrong.**
   `{x}ms` is `str()` and never raises (a `None` prints `Nonems`) while the
   adjacent `${x:.6f}` DOES raise on a str; `computed_at[:19]` is a code
   point slice and the `Z` is a literal, so an empty stamp renders a bare
   `Z`; and `{:,}` has an int lane and a float lane that print `1,234,567`
   and `1,234.5` for the same magnitude.

### What the differential caught on its first run

48 fixtures, 46 agreeing. Both disagreements were real.

**The str-`cost_usd` gate used `pyval.Float`, three lines under a comment
saying a str `cost_usd` raises.** `pyval.Float` is `float(x)`, so it
answers `3.0` for `"3"` and the renderer printed `$3.000000` where CPython
raises `ValueError: Unknown format code 'f' for object of type 'str'`.
This file has an `isNumber` helper whose doc comment warns about exactly
this substitution — and the mistake was made in the same file, at a site
whose own comment states the rule. Writing the warning is not the same as
applying it.

**The harness handed the two sides different values.** Fixture 27c meant to
reach `{:,}`'s scientific branch with `1e20`. But `encoding/json` writes
`1e20` as twenty-one digits, CPython loads that as an INT, and the fixture
tested the int lane on one side and the float lane on the other — reported
as a port bug. The fix is that a differential's INPUTS have to cross the
same wire its subject does: the whole fixture now goes through
`pyval.LoadsOrdered` + `pyval.Plain` before either side sees it, the same
typing `record.LoadOutcomes` applies. `1e21` is the first magnitude Go
writes in exponent form, so that is what the fixture uses now.

That second one also surfaced a **new named divergence**: CPython's int is
arbitrary precision, `pyval.Plain` resolves an integral literal through
`ParseInt` (int64), so a stored token count past 2^63 is an int to CPython
and a float64 to the port — and `{:,}` prints
`100,000,000,000,000,000,000` against `1e+20`. It is NOT worked around in
the renderer: the hole is in `pyval` and every reader in the port has it.
Filed, and pinned by a test that goes red when a big-int lane lands.

### Two guards that could not fail, found by mutating the fixtures

- The four-row cost-tie fixture could not tell `SliceStable` from `Slice`:
  Go insertion-sorts below twelve elements and happens to preserve order.
  A thirteen-row all-tied fixture crosses into pdqsort, where it cannot.
  This is the same shape as round 4's still-open **M101**, and the fix
  generalises — a single tie proves nothing about stability at any size.
- The `can't multiply sequence` message is unreachable through
  `compute_metrics`, which sums `tokens_out` into `avg_out` first and
  raises the addition error instead. It needed its own
  `identify_expensive_patterns` fixture, plus the `compute_metrics` twin
  that proves the two paths report differently on the identical row.

### Deliberately not ported

`maro metrics -format json` is REFUSED by name rather than silently
ignored. Python's is `json.dumps(asdict(metrics), indent=2)`, and `asdict`
walks two nested dataclass maps whose keys can be non-strings — which
`json.dumps` coerces for an int and refuses for a tuple. That is its own
measurement job; shipping a plausible-looking JSON lane before doing it is
how a port acquires a divergence nobody has a fixture for. `metrics pass-k`
is absent too: it reads `skill-stats.jsonl` and lands with the rest of
`skills.py`.

### The mutation battery, and the two ways it lied first

53 fixtures, then 42 mutants derived from the FILE rather than the diff.
Final: **37 caught, 5 survivors, all five accounted for.** Getting there
took four runs, and the first two were wrong in ways worth writing down.

**Run 1 reported 34 of 42 surviving — because none of the differentials
ran.** The battery copies the Go module to a scratch directory (P4: a Go
module builds through its import graph, so a battery owns the whole tree
it runs in). One level deeper than the previous battery's copy,
`../../../src` no longer resolved, `pyprobe.SrcDir` called `t.Skipf`, and
`go test` printed `ok`. Among the 34 "survivors" was the str-`cost_usd`
mutant the differential had CAUGHT live an hour earlier.

The baseline-green gate (P7) does not catch this. It asks whether the
suite passes before the first mutant. It did. **The baseline was green and
empty.** `MARO_PYPROBE_REQUIRED=1` now turns the skip into a `t.Fatalf`,
and the battery sets it — a door rather than a habit. New lens **P10**.

**Run 3 filed a KILLED mutant as "did not compile"** because the
classifier decided by grepping the test output for `cannot use` — which is
also the opening of CPython's own `cannot use 'dict' as a dict key`. The
battery now asks the compiler with `go vet` instead of reading prose to
decide what happened. That is the same mistake this port keeps finding in
the code it ports, made in the tool that looks for it.

**The tie fixture was wrong twice, the second time for a reason worth
measuring.** Four tied rows is below Go's insertion-sort threshold, which
is the known half. Thirteen IDENTICAL rows is above it and still cannot
fail — measured, `sort.Slice` preserves an all-equal list at every size
tried, because pdqsort detects the duplicates and takes an equal-partition
path. Only two or more interleaved groups above thirteen actually reorder:

	n:              4     12    13    16    40
	all equal:    keeps keeps keeps keeps keeps
	two groups:   keeps keeps SCRAM SCRAM SCRAM

Round 4's still-open **M101** is this finding one chunk earlier, recorded
then as "needs a many-ties fixture" — the fix that does not work. New lens
**P11**.

**The five survivors.** Three are UNREACHABLE GUARDS, now labelled as such
in the source: `pyDict.set`'s unhashable raise (every caller does `get`
first, which raises), `ratesFor`'s falsy-model collapse and
`estimateCostAny`'s cache_read lane (both dead from this file's callers,
kept because `estimate_cost` is public in Python). One is EQUIVALENT:
`pyval.Sum` over two operands is a plain addition. One is
NEAR-equivalent: a naive fold for `typeAvg` differs by one ULP, and
`typeAvg`'s only consumers are a `>` and a `:.6f` — a search over 400,000
random 3-to-6-row token sets found no case where the two land on opposite
sides of the `1.5x` threshold. `pyval.Sum` stays because Python's is
`sum()`, not because a test can tell.

## heartbeat.py slice 1 — the deterministic half, and three raises

The heartbeat is 1653 lines and most of it is orchestration: five
background threads, a daemon pidfile, a SlowUpdateScheduler, an LLM call
per stuck project. **Slice 1 is the part that is a function of its
arguments** — the recovery-action model, tier 1's scripted table, tier 2's
COOLDOWN (not the call it guards), tier 3's escalation message, the log
row, and the two cadence resolvers. The package doc names the other half
by function so absence is never read as coverage.

**The tripwire was honoured this time.** L49 says the differential comes
first; here the CPython probe was written and run *before a line of Go
existed*, and `scratchpad/hb_truth.txt` is that capture. The probe source
is then carried into `heartbeat_diff_test.go` VERBATIM rather than
paraphrased, so the ground-truth pass and the differential cannot drift.

### The six forced decisions

1. **`checks` is an ordered map, not a `map[string]string`.** Its order is
   observable *twice* — tier 1 emits one action per failing check in CHECK
   order, and tier 3's `Failed checks:` line joins the failing names in the
   same order. A Go map would randomise two rendered strings. The fixture
   that proves it is T9: the same four checks fed in reverse produce the
   four actions in reverse. A port that walked its own rule table instead
   would pass T1 and fail T9.

2. **Its VALUES are `any`, and a non-string one raises.** Python annotates
   `Dict[str, str]` and does not enforce it. A non-string detail reaches
   `detail.startswith(...)` and raises
   `AttributeError: 'int' object has no attribute 'startswith'` — measured
   — from inside *both* tiers. `map[string]string` makes that
   unrepresentable, which is the quiet way a port loses a failure mode.
   Three fixtures (T10–T13) plus E13.

3. **The raise in tier 3 is NOT the swallowed one.** `_tier3_escalate` has
   a bare `except Exception: return False` — but it wraps only the
   `telegram_notify` call. The failed-checks scan sits *above* the `try:`,
   so its AttributeError propagates while a notifier that blows up becomes
   `False`. E12 and E13 are the pair that tell those apart; a port that put
   the whole body in one error path passes E12 and fails E13.

4. **`{self.stuck_projects or 'none'}` is two renderers.** Empty list →
   the literal string `none`. Non-empty → Python's *list repr*, so one
   stuck project prints `['alpha']` with the brackets and quotes, an
   apostrophe in a name flips repr to double quotes (`["it's"]`), and a
   non-ASCII name stays literal (`['каф']`). Six fixtures, R1–R6.

5. **`telegram_sent` is spelled two ways in one object.** `summary()`
   renders `str(bool)` → `True`; the adjacent `to_dict()` writes JSON's
   `true`. Both are right in their own place, and R8 pins it.

6. **The two cadence resolvers differ in two constants and it matters at
   every input.** Merging them is the natural refactor and it is wrong:

   ```
                    shadow      backlog
     floor          max(0, n)   max(1, n)
     default        0 (off)     5
   ```

   The floor and the default are not the same number for backlog, so an
   explicit `-3` clamps to **1** while an explicit `"abc"` raises inside
   `int()` and falls all the way back to **5**. Measured across 21 inputs
   × 2 resolvers: `None→0/5`, `0→0/1`, `-3→0/1`, `False→0/1`, `2.9→2/2`,
   `"1_0"→10/10` (PEP 515), `"abc"→0/5`. The `except` is a *blanket*
   `except Exception` wrapping the import, the get and the `int()`
   together, so the config read is a thunk here — a config backend that
   fails must not be consulted on a path that never reads it.

### The one named divergence

CPython's `int()` accepts any Unicode decimal digit, so
`_resolve_backlog_every("١٧")` is **17**. `pyval.intFromString` is ASCII-only
and raises ValueError, which the blanket catch turns into the DEFAULT — so
Go answers **5**. The fix belongs in pyval's int lane, where it is already
a named residual with other consumers, not in a workaround here.
`TestNonAsciiDigitCadenceIsANamedDivergence` pins it *and* carries an
anti-vacuity half (`"17"` must still be 17), so it goes RED the day the
real fix lands rather than passing quietly against a resolver that
defaults on everything.

### Two things the differential could not cover, and what covers them

The cooldown's input is a monotonic clock and the log's output is a file,
so neither has a CPython fixture. Both get a Go test that asserts the
BEHAVIOUR the Python comment warns about:

- `Cooldown.Due` on a never-diagnosed project must be true **at clock
  zero**. Python's comment says why — `time.monotonic()` counts from boot
  on Linux, so a `0.0` sentinel suppresses the first diagnosis for the
  first thirty minutes of system uptime, on a freshly booted box or a CI
  runner, which is where nobody looks. Go's monotonic clock has the same
  hazard with a different origin. The test also pins `>=` at the exact
  boundary and one nanosecond before it.
- `Log` writes to `memory/heartbeat-log.jsonl`, one line per append, in
  `json.dumps`' DEFAULT separators (`", "` / `": "`, so
  `pyval.DumpsCompactPy` and not the compact renderer). A reader splitting
  on lines does not care; a byte-for-byte ledger comparison between the two
  runtimes does.

### Deliberately not ported, and why the slice still has no CLI

There is no `maro heartbeat` yet, because the *loop* is not ported —
shipping a command that runs one tick and exits would be a different
program wearing the name. Slice 1 is a library slice on purpose. The
consumer arrives with the loop; until then the differential is the
consumer, which is the honest version of "no consumer yet".

### Two narrowings, stated rather than hidden

- `StuckProjects` is `[]string`, so a report carrying `[1, None]` — which
  CPython renders verbatim, measured — cannot be built. The annotation is
  `List[str]`, every writer honours it, and widening it would put a
  repr-any requirement on the whole package to model a shape nothing
  produces.
- The notify seam is typed `func(string) (bool, error)`. Python returns
  `telegram_notify(message)` *unchanged*, so a notifier answering a truthy
  string makes `_tier3_escalate` return that string despite its `-> bool`
  annotation (measured: `"yes"`). Every real notifier returns a bool; the
  pass-through is an artifact of what a monkeypatch can reach, not a
  behaviour anything relies on.

### The mutation battery: 70 mutants, and four tests that could not fail

Derived from the FILE, not the diff. Three rounds:

**Round 1 — 57/70.** Thirteen survivors, and the triage split them cleanly
into *bad mutants* and *real gaps*, which is the whole value of doing the
triage rather than counting:

- **Four bad mutants of my own making.** M4 multiplied by zero (equivalent
  by construction), M5's site did not match the file, M60 named a renderer
  that does not exist (`pyval.Dumps`), M64 folded `max(floor, def)` where
  both resolvers already satisfy it. A battery is code, and code derived
  from a file you just wrote inherits the same blind spots.
- **Three EQUIVALENT, measured not assumed.** M11/M12/M13 turned the empty
  `stuck_projects` / `checks` / `recovery_actions` containers into nil. A
  one-off probe settled it: `pyval` renders a nil `List` as `[]` and a nil
  `Obj` as `{}`. The explicit empties are style. The nil-GUARD standing
  beside them (`if checks == nil { checks = pyval.Obj{} }`) was real dead
  code and is now deleted — it read like a safety net and caught nothing.
- **One EQUIVALENT through the API.** M32 returned the actions gathered
  before a mid-walk raise instead of nil. Every caller checks `err` first,
  so no answer changes. Kept in the battery *labelled*, so the next round
  does not re-derive it and file it as a gap.
- **Four REAL gaps, and all four were tests that could not fail:**

  | mutant | why the test could not fail |
  |---|---|
  | M34 unknown status treated as healthy | every escalate fixture with an odd status also had a stuck project |
  | M56 cooldown halved to 15 min | every assertion was spelled `DiagnosisCooldown`, the thing under test |
  | M58/M59 log moved and renamed | the assertion was `path != LogPath(ws)` — both sides call the function |
  | M61 a failed append still reports its path | no test ever made the write fail |

  M56 and M58 are the same mistake twice: **an expected value spelled with
  the function under test is not an assertion.** The fixes are a literal
  `30*time.Minute`, a literal `filepath.Join(ws, "memory",
  "heartbeat-log.jsonl")`, an escalate fixture with an unknown status and
  nothing stuck, and a `Log` into a workspace where `memory` is a regular
  file.

**Round 2 — 65/70.** M64, rewritten as "an unparseable CONFIG value falls
to the floor", survived — and that exposed a whole BRANCH the differential
structurally cannot reach. `pyprobe` points `MARO_USER_DIR` at a temp
directory on purpose, so CPython's `config.get` always answers the default
and every `None` fixture exercises the same line. `TestResolverConfigLane`
covers it on the Go side: a failing read lands on the DEFAULT (invisible
for shadow, wrong by four for backlog), a usable value is read and floored,
and the thunk must not be *called at all* when an explicit value was given.

**Round 3 — 66/69, and the three survivors are the three labelled
EQUIVALENT.** That is the shape a converged battery has: every survivor
carries a measured reason, and none of them is "we should test this".

## The r1 review of internal/tasks — three HIGH, and three tests pinning the wrong runtime

Every finding's code claim was re-measured against CPython 3.14.3 before a
line was changed; all five held, which is unusually high for an adversarial
round and worth recording as such. Two of the three HIGHs were **the port
asserting its own behaviour and calling it the specification.**

### HIGH 1 — `Path.exists()` swallows every stat failure, not just ENOENT

`_read_task` is `if not path.exists(): return None`, and CPython's
`Path.exists()` is `try: self.stat() except (OSError, ValueError): return
False`. ENAMETOOLONG, ENOTDIR, ELOOP, EACCES-on-a-parent and an embedded
NUL all mean *"no such task"* there. `os.IsNotExist` is true for ENOENT
alone, so each was a hard error out of the verb here.

Reachable because `blocked_by` is a **foreign-writable list**, and the cycle
check reads every dependency path. Measured, both sides:

	enqueue(blocked_by=["d"*300])          CPython writes n1.json   Go writes nothing
	enqueue(blocked_by=["t.json/inner"])   CPython writes n2.json   Go writes nothing
	enqueue(blocked_by=["dep\0x"])         CPython writes n3.json   Go writes nothing

The two runtimes disagreed about **which rows the queue will ever run**.
Fix: `os.Stat` first and return "absent" on ANY error, which is what
`Path.exists()` does.

**And the first version of the test could not fail.** Two of its three
fixtures passed against the unfixed source, because the queue DIRECTORY
does not exist yet when the cycle check runs — the path walk died at the
missing parent with ENOENT, which `os.IsNotExist` does catch. Seeding the
directory is what makes the fixture reach the case. This is P12 again from
the other side: an assertion can be independent of the code and still not
touch it. **Run the new test against the OLD source. Every time.**

### HIGH 2 — the emitter echoed a foreign float's source literal

`pyval.LoadsOrdered` keeps `json.Number`, and `pyjson.Value`'s rule was "a
`json.Number` keeps its source literal". That is right for INTEGERS —
Python's ints are unbounded, and a 24-digit id through `float64` comes back
wrong — and **wrong for floats**: `json.loads` produces a float and
`json.dumps` writes `float.__repr__`. Measured:

	2.50 -> 2.5      1E5   -> 100000.0      1e19  -> 1e+19
	1e23 -> 1e+23    1.0e2 -> 100.0         1e400 -> Infinity

`0.30000000000000004` and `-0.0` agree either way, which is exactly why a
spot check missed it. It was found by a randomised cross-runtime
differential that compared the resulting FILE BYTES, not just return
values — 10 hits in 120 fixtures, one of them a `fail` whose on-disk row
diverged.

The fix is four lines in the shared emitter, and **the blast radius was
three test failures — all three asserting the wrong side**:

| test | asserted | CPython |
|---|---|---|
| `orch`'s `TestLoadsOrderedKeepsOrderAndLiterals` | `"s": 0.250` | `0.25` |
| `record`'s `TestVerdictStampPreservesWholeFloatLiterals` | `"score": 0.250` | `0.25` |
| `tasks`' `TestAForeignFieldSurvivesARewrite` | `2.50` survives | `2.5` |

Each was written to pin "a foreign file is not reformatted", which is a
real and correct goal — for integers and for `1.0`, both of which still
hold. The tests generalised it to floats without measuring, and then
**defended the bug for months**. All three now assert both ends: the
re-render AND the integer literal that must survive verbatim.

### HIGH 3 — an escaped lone surrogate, filed rather than fixed

`{"note": "x\ud800y"}` is seven ASCII bytes on disk; no decoder refuses it.
CPython's `json.loads` produces a str holding U+D800, and then
`write_text(encoding="utf-8")` **cannot encode it** — so `fail()` raises
`UnicodeEncodeError`, `_atomic_write` unlinks its temp file, and the row is
byte-identical and still `claimed`. Go substitutes U+FFFD at decode, so the
verb SUCCEEDS, the row becomes `failed`, and the original bytes are gone.

The existing `pyval` residual describes the `ensure_ascii=TRUE` writer,
where the surrogate is re-escaped and the write succeeds — a byte
difference. `task_store` writes with `ensure_ascii=False`, where the
outcome is a **state** difference. Same input, different class of problem,
and the note that was supposed to cover it did not.

Filed with a test that measures CPython every run and fails in EITHER
direction — if CPython changes, or if this port stops diverging. Not
guarded in `readRaw`: CPython's read SUCCEEDS, so refusing there would
break `list_tasks` and `status_summary`, which is a third behaviour
matching neither runtime.

### The two LOWs

**The differential's Python half was not in the repo.** `probe_test.go`
pointed at `scratchpad/ts_measure.py`, which had been deleted, so the
101-line byte-identity claim in `REVIEW.md` rested on a file nobody had and
`go test` reported one honest skip. Both halves now live beside each other,
with `ts_diff.sh` owning the temp workspaces, the normalisation and the
diff — one command, re-run here: **identical across 101 lines**. Its
normaliser masks only three fields and each keeps its SHAPE
(`task-<stamp>-<hex8>`, not `<id>`), so a port that minted a UUID still
diffs. The sibling `internal/orch` probe has the same missing half; that is
now a known, named gap rather than an invisible one.

**"Nothing depends on the sweep order" was wrong for two of three.**
`StatusSummary` returns an ordered `Obj` *because* the CLI json.dumps'es
it, so its printed key order is sorted-filename here and readdir there —
and when two statuses compare equal but spell differently (`1` and `true`,
`5` and `5.0`), the bucket takes the label of whichever ARRIVED FIRST. Go's
answer is deterministic; CPython's is not. Comment corrected; the
divergence cannot be closed, only named.

## `sheriff.py` slice 1 — the producer side of the heartbeat seam

`internal/sheriff` ports the deterministic half of the 641-line
`src/sheriff.py`: `SheriffReport` and `SystemHealth` with both renderers,
`check_project`, `detect_no_progress`, `fingerprint_project_state`,
`project_lifecycle_state`, `project_activity_age_days`, `_dormant_days`,
and the checks→status rollup lifted out of `check_system_health`.

The tranche was chosen for the SEAM rather than for the line count.
`check_system_health` produces the `checks` dict and `check_all_projects`
the `stuck_projects` list that the heartbeat chunk's `Report` consumes, so
this closes a boundary that was ported at one end only.

**What is deliberately not here, and why**, is in the package doc: four of
`check_system_health`'s five probes measure the ENVIRONMENT (a socket to
127.0.0.1:18789, `shutil.disk_usage("/")`, an `__import__`,
`llm.detect_backends()`), so a differential over that function would
compare two machines rather than two implementations. The fifth thing it
does — the rollup — is pure, is the value heartbeat branches on, and is
here as `RollupStatus`.

### The tripwire again: probe first, carry it verbatim

`scratchpad/sh_probe.py` was written and run **before a line of Go
existed** (L49), and its `PROBE` body is now the `shPySrc` constant in
`sheriff_diff_test.go`, byte for byte. The ground-truth pass and the
differential cannot drift because they are the same source.

It earned its keep three times before any Go was written, and twice more
after:

- **C1b** — the `mkproj` helper was creating the project directory even
  for the "does not exist" case, so the fixture named a branch it did not
  reach. The probe's answer disagreed with the case name.
- **C9** — my guess was `warning`; CPython said `stuck`. `items_stuck_doing`
  wins the status ladder over `no_artifacts`.
- **C22/C23** — written as dormancy fixtures, they came back `stuck`.
  `project_activity_age_days` takes the MAX over the directory AND its
  files, so ageing only the directory leaves a just-written `NEXT.md`
  holding the real clock. The dormancy fixtures had to age everything, with
  the `"."` entry applied LAST because writing a file inside a directory
  resets that directory's own mtime.

### The clock is a fixture, not a mask

`check_project` reads the wall clock twice — the dormancy age and the
artifact age — and both reach the output as rendered numbers. Two processes
cannot agree on `time.time()`, and the easy answer is to mask both lines in
the comparison. That would have left the `%.0f` rounding, the dormancy
threshold and the window comparison untested, which is most of what the
function decides.

Instead the probe patches `sheriff.time.time` for the duration of one case
and the Go side takes `now` as a parameter. C24 (exactly 13.5 days), C34
(exactly 14.0) and C39 (an artifact exactly at the window) exist only
because that seam does.

### Three measured things a reasonable port gets wrong

**`repr` inside f-strings.** The evidence lines are
`f"...: {[i.text for i in doing_items[:3]]}"` — a LIST repr, brackets and
quotes included, flipping the whole list to double quotes for one
apostrophe (`["it's"]`) and leaving non-ASCII literal (`['café → naïve']`).
The repeated-log line is `{text[:60]!r}` — sixty CODE POINTS, then repr.

**Two spellings of the same object.** `SheriffReport.format("json")` is
`json.dumps(..., indent=2)` with ensure_ascii at its DEFAULT, so a
diagnosis reads `café` there and `café` in the text form one branch
up. And `format(mode)` tests `if mode == "json"`, so every other value —
a typo, `""`, `"zzz"` — falls through to text. A Go enum with a
default-case error would have been a third behaviour.

**None and `""` are one state in text and two in JSON.** The text renderer
tests `if self.recommended_action:` — truthiness — so both print no action
line. The JSON renderer writes `null` versus `""`. `Report.HasAction`
carries the distinction a `*string` would have carried badly.

### `_dormant_days` is three lanes, and the middle one is the trap

`float(get("sheriff.dormant_days", DORMANT_DAYS_DEFAULT) or 0)` inside a
blanket `try`:

| setting | answer | meaning |
|---|---|---|
| unset | 14.0 | on, at the default |
| `0` / `""` / `null` / `False` / `[]` | **0.0** | **off** |
| `"abc"` / `[1]` | 14.0 | on, at the default |
| `7` / `"  7  "` / `"1e3"` / `-3` / `True` | that value | on |

A port that answers "the default" for both of the last two rows silently
re-enables a check the operator switched off. All fifteen rows measured.
`ResolveDormantDays` takes the config read as a thunk, the way heartbeat's
cadence resolvers do, so a config backend that RAISES lands on 14 rather
than on 0 — a lane the probe cannot reach, and one the battery walked
straight through until a Go-side unit test closed it (M52).

### The finding: `check_project` can never return `"warning"`

Both branches that assign `"warning"` are dead. `no_artifacts` and
`artifact_stale` are each recorded only when `doing_items` is non-empty,
and a non-empty `doing_items` always also records `items_stuck_doing`,
whose branch is tested first. The 41-fixture ground-truth pass reaches six
of the seven documented statuses and never that one, including the
fixtures written specifically to try.

This is a **Python-side** defect. The port reproduces both dead branches
with the reasoning at the site, because a port that emits a status the
original cannot is the worse bug. Three candidate resolutions and their
consequences are in BACKLOG; whichever wins has to land in `src/sheriff.py`
first and be re-ported after. The differential's anti-vacuity gate asserts
six statuses and says in its own comment why not seven, so a later fixture
pass does not spend an afternoon trying to reach it.

### The other finding: a sort is only as faithful as its input (new lens L50)

`sorted(artifacts_dir.glob("*"), key=mtime, reverse=True)[0]` was ported as
`filepath.Glob` + `sort.SliceStable`. The sort was right — CPython's
`sorted(reverse=True)` does NOT reverse ties, measured — and the INPUT was
wrong: `pathlib`'s glob yields readdir order, Go's `filepath.Glob` sorts by
name.

On this box's ext4, two files written `aaa.txt` then `bbb.txt` come back
from readdir as **`bbb.txt`, `aaa.txt`** — name-hash order, independent of
creation order. With equal mtimes CPython reported `bbb.txt` and the port
reported `aaa.txt`. Fixed by reading the directory raw with
`Readdirnames(-1)`.

Matching readdir is also what makes the fixture STABLE rather than lucky.
Sorting gave the Go side a deterministic answer and the CPython side a
filesystem-dependent one; reading raw makes both ask the same filesystem
the same question, so C30 and C40 pass on ext4 (hash order) and on a tmpfs
`/tmp` (insertion order) alike without either being written into the test.

Twenty lines away in the same Python file, `project_activity_age_days` does
`sorted(artifacts.iterdir())[:50]` — explicitly sorted, by path. One
function sorts and its neighbour does not; a port that unified them behind
one helper would necessarily have got one of them wrong, and would have
looked tidier for it.

### The mutation battery — and the trap the harness documents

85 mutants derived from `sheriff.go` itself, run against a COPY of the
whole module (P4).

**Round 1 reported every single mutant surviving.** The battery built its
environment dict and never passed it to `subprocess.run`, so
`MARO_PYPROBE_REQUIRED` was unset; `pyprobe.SrcDir` could not resolve
`../../../src` from the scratch copy, every differential SKIPPED, and
`go test` printed `ok`. This is the exact failure `SrcDir`'s own comment
was written about after the SystemMetrics battery — and the comment did not
prevent it, because the battery was a new file that never read it. The fix
is two lines (pass the env, symlink `src` beside the copied `go/`) and it
is now the first thing the harness's docstring says.

A second run raced the first onto the same log file and produced a
plausible-looking interleaving of both. Two batteries, one log, one set of
conclusions that meant nothing.

**Round 1 (real): 64 caught of 85, 19 survivors, 2 battery bugs.**
Thirteen survivors were genuine test gaps and produced sixteen new
fixtures:

| survivor | what was missing |
|---|---|
| prefix → `Contains` | a check reading `"ok: 0 failures"` |
| the equality scan's start index | a window whose MIDDLE differs (`X,A,B,A`) |
| unreadable-vs-absent | a `DECISIONS.md` that is a DIRECTORY |
| the project dir as a candidate | an old project with a freshly-touched dir |
| `newest <= 0` → `< 0` | everything at epoch zero |
| the dormancy comparison | an age of exactly 14.0 days |
| `%g` → `%.0f` | a FRACTIONAL threshold (7.5) — 14 renders the same both ways |
| blank-line dropping | blanks that push real lines out of the last twenty |
| the 60-code-point clip | a repeated line of 80 characters |
| stale-without-doing | a stale artifact and NO items in progress |
| the window comparison | an artifact exactly at the window |
| `SliceStable` → `Slice` | 20 tied artifacts + 1 older (P11's shape) |
| a failing config read | a Go-side unit test; the probe cannot raise |

The `SliceStable` fixture is worth its own line: **CPython answers
`t02.txt`**, which is neither first nor last by name. It is readdir order,
and it is the same fixture that pins L50.

**One survivor was the TEST's fault, not the port's.** `cpOut` copied
`Evidence` into a fresh `[]any`, which erased nil-versus-empty — so
`evidence = []string{}` could be deleted from the port and the differential
stayed green (M76). The renderer that was supposed to expose the difference
was the thing hiding it.

**Round 2: 79 caught of 86.** Six survivors, every one labelled
EQUIVALENT or NEAR-EQUIVALENT at its site so the next round does not
re-derive them:

- `clipTail`'s `<=` → `<` — at `len == n` the slice branch returns the
  whole string, the same value the guard returns.
- the byte-slicing variant — pre-trimming to `n*4` BYTES can never drop a
  rune the tail of `n` runes needs. A bad mutant of my own making.
- `sort.Strings` on the readdir names — `os.ReadDir` already promises
  filename order. The call stays because Python spells the sort explicitly
  and this port states requirements rather than inheriting them (L48).
- the decision window's `>` → `>=` — at exactly twenty, `recent[0:]` is
  `recent`.
- the complete-diagnosis conjunct — that branch is only reached when
  `problems` is empty, and a non-empty doing list always records a problem,
  so `len(doing) == 0` already holds. The Python has the same redundancy
  and the port keeps it (L48).
- `pySeconds` → `UnixNano/1e9` — see below.

**Round 3: 80 caught of 86, 0 battery bugs, the same six labelled
survivors.** Converged. The battery lives at
`go/internal/sheriff/mutants.py` rather than in a scratch directory, for
the reason the `tasks` r1 LOW gave: a claim that can only be re-checked by
a script nobody has is not a claim.

**A comment I had to retract in the same session I wrote it.**
`pySeconds` splits seconds from nanoseconds because that is CPython's
`sec + 1e-9*nsec`. The first draft of its doc comment justified this by
claiming `float64(t.UnixNano())/1e9` rounds enough to flip an
exactly-fourteen-day comparison. Measured, that is **false**: the two agree
on every whole-second timestamp and differ by ~1e-12 DAYS on sub-second
ones. The expression stays — it is the original's, and the next caller may
compare at finer resolution than a day — but the comment now carries the
measurement instead of the story, and M46b is a labelled near-equivalent.
A plausible rationale nobody measured is the same defect class as a test
that cannot fail.

## `system_health.py` slice 1 — a state machine whose only output is a file

`internal/syshealth` ports the portable core of the 693-line
`src/system_health.py`: `load_snapshot`, `_write_snapshot`, `_history_of`,
the `run_health_probes` cycle, `render_snapshot`, and the seven constants
(`OK`, `SILENT`, `UNKNOWN`, `HISTORY_KEEP`, `STREAK_FOR_SILENT`,
`CANDIDATE_STARVATION_HOURS`, `VARIANT_STALE_DAYS` — fixture Z1 compares
all seven).

The module is the liveness registry for Maro's own dynamic processes — a
writer whose consumer never runs is the defect class it exists to catch —
and SILENT is a finding rather than an error. Nothing it does changes
runtime behaviour, which is exactly why a port can get it subtly wrong for
a long time without anything going red.

**What is deliberately not here** is in the package doc, and one omission
is load-bearing rather than lazy: the seven declared probes each read a
DIFFERENT live store, so each is its own tranche. In Python they are a
module-level list, `DECLARED_PROCESSES`, and the consequence is that the
cycle **cannot be exercised at all** without monkey-patching it — which is
precisely what the ground-truth probe does. The Go side takes them as a
parameter. That is not a convenience: it is the seam that makes the state
machine testable, and the same seam that made the cycle fixtures possible — 47 of
them as of r2.

`_narrate_transition` is the other one. `RunCycle` RETURNS the narrations
it decided on instead of performing them, and the Python's own comment says
why: narrate only after the snapshot recording `narrated` has persisted, or
a failed write leaves the captain's log claiming the user was told while
the state machine re-narrates forever. Returning them puts the ordering in
the type instead of in a comment.

### Three seams, three clocks, and what each one hides

The probe patches three things, and each patch is a question the port had
to answer:

- **`sh.datetime` frozen.** `datetime.now(timezone.utc).isoformat()` is
  called **once per declaration and once more for `updated_at`**. Freezing
  it is what makes the fixtures comparable — and is also what would let a
  port that reads the clock once, or fifty times, pass every one of them.
  So `now` is a `func() string` on the Go side, not a string, and
  `TestRunCycleReadsTheClockOncePerDeclarationPlusOnce` pins the count at
  n+1. This is L48 in its smallest form: the SHAPE is observable even when
  the values are not.
- **`config.get` patched only for `health.probes_enabled`, and only on the
  cases that ask.** An unconditional patch would make the enabled-by-default
  cases test the patch. The gate is spelled `bool(cfg_get(...))`, so
  `enabled` is an `any` on the Go side and runs through `pyval.Truthy`:
  `0`, `""` and `False` skip the cycle, and the STRING `"no"` runs it.
- **`_narrate_transition` captured into a list.** What the differential
  needs is WHICH transitions were decided, not that a log file grew.

### The narration edge is a field, not a status pair

The one decision most likely to be ported wrong, because the wrong version
passes almost everything:

```go
wentSilent := status == Silent && narrated != "silent"
recovered  := status == OK     && narrated == "silent"
```

Reading `prevStatus != Silent` instead of `narrated != "silent"` agrees
with CPython on every fixture that existed until C34 was written for it.
The case that separates them is a process that was told-silent, dipped to
UNKNOWN when its probe broke, and came back SILENT: the status pair says
"new silence", the field says "the user already knows". C34 is that case,
and the battery's M47 is the mutant that proves the fixture works.

The mirror case is C5 — SILENT → UNKNOWN → OK still owes the user a
recovery line — which is why the recovery arm reads the field too.

### Six things CPython does here that a reasonable port would not

Measured before any Go existed (L49); each one is a fixture:

- `int(snapshot.get("cycle", 0) or 0) + 1` has **three** lanes, not two.
  Absent / `0` / `""` / `None` / `False` restart at 1; `"41"` and `2.9`
  advance; `"abc"` **raises**, and the raise is inside the blanket `except`,
  so the cycle aborts *before the write*. `ran` still counts the probes that
  already ran, `transitions` stays 0, and the file on disk keeps its corrupt
  counter. C22, C36 and C37 pin all three of those consequences separately.
- `if obs:` — an **empty** observation is falsy, so it appends nothing AND
  takes no timestamp. A probe with nothing to say leaves the ring buffer
  alone (C11).
- `{**obs, "at": now}` **overwrites** a probe-supplied `at` in place, keeping
  its ordinal (C13). `pyval.Obj.Set` is that rule exactly.
- `entry = dict(prior)` is a **shallow copy**, so keys this module has never
  heard of survive (C15). That is the data-retention rule, not tidiness.
- `render_snapshot` **raises** three different ways on a hand-edited
  snapshot: a list `processes` dies on `.items()`, a string entry dies on
  `.get`, and — the one nobody guesses — an **unhashable status** dies in
  the SORT KEY, because `order.get(status, 3)` is a dict lookup and a list
  is not a key. R12, R11/R15 and R14.
- `.get(key, "?")` returns the default only when the key is **ABSENT**. A
  present-and-null `updated_at` renders the four characters `None` (R6 vs
  R7).

The unhashable-status message is the one place this differential is pinned
to an interpreter version: CPython 3.14 says `cannot use 'list' as a dict
key (unhashable type: 'list')` where 3.12 said the bare second half. That is
recorded at the site, so a future failure there reads as a fixture question
rather than a port defect.

### The bug the differential could not have found

`processes` is a `pyval.Obj`, which is a **slice**. The first draft assigned
it into the snapshot before the loop, the way Python does — but Python
mutates one dict in place, and `Obj.Set` reallocates. Every process the loop
appended after the first reallocation was dropped from the written file.

I first wrote here that the differential could not see it. That was a
guess, and hand-editing a copy into exactly that shape disproved it: every
cycle fixture fails at once, all thirty-seven of the day's fixtures
reporting `"processes": {}` against CPython's populated map (there are 47
now; the numbers in this paragraph are the measurement as it stood, not a
current count). `TestRunCycleKeepsEveryProcessItAppended` stays because it
names the cause in one line rather than in thirty-seven diffs and needs no
interpreter — but it is a convenience, not the guard,
and the honest version of this paragraph is the one that got measured.

Same defect class as the retracted `pySeconds` comment two tranches ago:
**a claim about what a test can and cannot catch is itself testable, and
writing it down without running it is how a battery inherits a fiction.**

Two more hazards exist only because the Go seam is wider than the Python
one, and both got unit tests for the same reason:

- A Python probe **either** returns a triple **or** raises. Go's returns a
  value AND an error, so "raised and handed back a half-written
  observation" is a state the Python cannot express and the port must
  still refuse.
- `{**obs, ...}` builds a new dict; `stamped := obs` would alias the
  probe's own value and stamp it in place. The real probes will return
  cached objects — they read a store once — so the second cycle would see
  an `at` the first one wrote.

### The differential's own renderer, again

`syGo` converts `pyval.Obj`/`List` into what `encoding/json` compares. Its
first draft turned a **nil** `List` into `make([]any, 0)`, which marshals to
`[]` — erasing exactly the nil-vs-empty difference that decides whether
`summary["silent"]` comes out as `[]` or `null`. Battery M1 survived on the
first round for that reason and nothing else.

This is the second time in three tranches that the test's own renderer was
the guard that failed (sheriff's M76 was the first). The pattern is worth a
name: **a differential that normalises before comparing has moved the
assertion into the normaliser**, and the normaliser is not under test.

### The battery: 94 mutants, three rounds, and two that were caught for the wrong reason

`go/internal/syshealth/mutants.py`, derived from the FILE. **r1 77/93 with
1 battery bug; r2 85/94; r3 87/94 with 0 battery bugs and seven labelled
survivors.** Converged.

What the rounds actually bought, beyond the count:

- **r1's M1/M17 were the `syGo` bug above** — a survivor that was a TEST
  defect, not a coverage gap.
- **r1's M26 and M62 were bad mutants of my own making** (L8). M26 *added*
  an early `snapshot.Set` while leaving the real one below the loop, so the
  original behaviour was still reachable; M62 moved one line from below the
  cycle-counter write to above it, both of which are already past the error
  return. Neither could change an answer. M26 is now split — "the store is
  removed" (a mutant) and "the store is duplicated" (labelled equivalent) —
  and M62 moved to the position that actually probes the decision.
- **r2's M32 and M38 named two hazards the seam INVENTED.** Neither exists
  in Python: a probe there either returns a triple or raises, and
  `{**obs, ...}` always builds a new dict. Go's four-value return and slice
  semantics make both expressible, so both got unit tests.
- **M38 took two attempts, and the second attempt is the interesting one.**
  The first pin handed the probe `{"n": 1}` and asserted it was unchanged —
  and passed against the aliasing mutant, because `Obj.Set` on a NEW key
  appends, and append on a full slice reallocates. The corruption only
  happens on Set's in-place lane, so the fixture has to hand over an
  observation that already carries `"at"`. **A pin that depends on
  `append` reallocating is a coin flip written as an assertion.**
- **r2's M7/M8 were the honest kind of gap.** Nothing pinned WHERE the
  snapshot lives, because every other fixture both writes and reads through
  `SnapshotPath` — rename the file consistently and the differential shrugs.
  Worse, M8's r1 "caught" was a **compile failure**: replacing
  `orch.MemoryDir(ws)` with `ws` left the `orch` import unused. A mutant
  that does not build is reported as caught and proves nothing — the same
  false positive as a site matching zero times, and it needs the same
  treatment. Both are fixed, and the fix is a new differential case (Z2)
  that asks CPython where `_snapshot_path()` resolves, relative to the
  workspace: `memory/system_health.json`.

The seven r3 survivors are all labelled at their sites. Five are
equivalent-by-construction because a nil fallback or an identity slice
follows two lines later (M9, M20, M24, M26b, M27); M41 is `>` vs `>=` at a
length where the trim is the identity. **M6 was labelled equivalent and was
not** — see the review section below, which is what caught it.

## The r1 review of `internal/syshealth` — the half of a `try` the port did not own

Ten findings survived verification, two were self-retracted as hallucinated
(P1: check the code claim before fixing — the rate holds). One is
structural and the rest are the usual sediment, but the structural one is
worth the whole round.

### The finding: a `try` block split across a seam stops being one try

Python's `run_health_probes` is a single `try` wrapping four things: the
config read, the load, the cycle, and the write. The port had split the
middle out as `RunCycle` and left the config gate inside it, with the
caller — nobody yet — expected to do the write. That reads fine and it is
wrong in a way no fixture could see, because the fixtures drove `RunCycle`
and then performed the write themselves.

Measured, all three lanes on CPython 3.14:

| what raises | `ran` | `transitions` | narrated | file |
|---|---:|---:|---|---|
| `config.get` | **0** | 0 | nothing | untouched |
| the cycle counter | N | 0 | nothing | untouched |
| **`_write_snapshot`** | N | **0** | **nothing** | untouched |

The third row is the one that matters. The probes ran, the cycle decided
two narrations, and the write failed — so `summary["transitions"] = ...` is
never reached and the narration loop never runs. A port that assigns
`Transitions = len(pending)` inside the cycle reports transitions that did
not happen, and a caller that logs `pending` regardless writes captain's-log
lines claiming the user was told about a state that was never recorded.
The state machine will then re-decide the same transition next cycle
forever, because `narrated` never persisted. That is precisely the failure
the Python's own comment says the ordering exists to prevent, and the port
had reintroduced it by drawing the seam one line too high.

The fix is `RunAndPersist`, which owns all four things and is now the entry
point; `RunCycle` no longer sets `Transitions` at all, and says so. The
first row is why `cfg` is a `func() (any, error)` rather than a value: it
is the only lane where `Error` is set and `Ran` is still 0.

**The generalisation is the useful part.** A `try` is a control-flow scope,
and splitting a function across a package boundary silently splits it. The
question to ask at every extraction: *what did the original's error handler
cover that my half does not?* Filed as P13.

### What the round says about the harness

Three of the ten findings were the differential lying by omission, and they
rhyme:

- **The anti-vacuity gate that could not fail.** `wroteFile < 20` counted
  fixtures whose `file` key was a string — including every fixture that
  SEEDED a file and never wrote one. Deleting the write from the cycle
  entirely left the gate silent at 31 while 31 per-case diffs failed. It
  now counts only snapshots carrying this cycle's frozen stamp. A guard
  derived from "is the field present" instead of "did the thing happen" is
  the same defect as a mutant that cannot change an answer.
- **`enabled == nil` meant two different things** — "this fixture does not
  patch config" and "config returned None" — so the second was unwriteable.
  `probes_enabled:` with no value is a real YAML shape and it skips the
  cycle. Two fields now, and C43 is the fixture.
- **Two clip sites, no non-ASCII fixture.** `str(exc)[:120]` and `[:200]`
  slice CODE POINTS; every clipped message in the fixture set was ASCII, so
  a byte-slicing port passed. It also emits an invalid UTF-8 tail, which
  makes `WriteSnapshot` refuse the whole snapshot — not cosmetic. C38 and
  C39 are 201 and 300 runes of non-ASCII.

And one where the doc and the battery asserted opposite things with nothing
adjudicating: `ToDict`'s comment said the summary's key order is Python's
insertion order and a real contract; mutant M6's label said nothing
promises it. Both were unfalsifiable, because `syGo` routes the summary
through a Go map on its way to `encoding/json` and a Go map has no order.
The doc was right — `ran, silent, transitions` is not alphabetical, and the
consumer `json.dumps` it. `TestSummaryToDictKeepsCPythonsInsertionOrder`
now compares the rendered string, and M6 is expected to be caught.

**These are L51's instances 3, 4 and 5**, and they are worth counting
separately from the first two: those were a normaliser erasing a *value*,
these are a counter, a reused field and a normaliser erasing the
*question*. The lens generalises to: for every conversion, reused field or
derived count the harness performs, name what it collapses, then ask
whether anything else still asserts it.

### One place the differential is allowed to look away

The failed-write fixtures compare everything except the `error` string,
which is elided with the reason named at the site. Two things live in there
that cannot match: the tempfile name is random (`atomic_write` goes through
`mkstemp`), and the WORDING is a named port residual — `str(OSError)` is
`[Errno 13] Permission denied: '/p/tmpX'` and Go's is
`open /p/tmpX: permission denied`. This port does not emulate OSError
formatting, the same call `dispatch/envelope.go`'s `oserr` makes. What
survives the elision is everything the lane exists to pin: that an error is
set, that it is a permission error, and — untouched — `ran`, `silent`,
`transitions`, `told` and `file`.

### The rest

- `WriteSnapshot` created `memory/` at `0o755`; `Path.mkdir` passes `0o777`
  and lets the umask narrow it, which on this box (umask 0002) is `0o775`.
  A group-writable store had been quietly becoming group-read-only. The
  guard builds a control dir with `MkdirAll(…, 0o777)` rather than reading
  the umask, and states its own limit: under a 0o022 umask it cannot fail.
- Six inline `v.(pyval.Obj)` assertions rejected `map[string]any` while
  every `pyval` helper called in the same breath accepts it — so
  `RenderSnapshot` handed one would build the self-refuting message
  `'dict' object has no attribute 'items'`. Latent (the decoder never
  produces a Go map) and now funnelled through `asDict`/`asList`, which
  sort a plain map's keys because a Go map has no order and this module's
  output order is observable.
- `nextCycle`'s TypeError arm was named in its doc and reached by no
  fixture; C40/C41 reach it, C42 pins that `True` is an int and counts 2.
- Two doc claims were simply wrong and are now corrected in place: the
  `verbose` substitute (the snapshot is in prior-insertion order, not
  declaration order, and carries processes this cycle never probed), and a
  "measured" claim about the `{**obs, "at": now}` ordinal that `sort_keys`
  makes unobservable.

### The battery, r4: 96/106, and five mutants that had never run

The battery grew to 106, twelve of them for code the restructure created —
including M99, which is the r1 finding itself expressed as one line moved
above the write, and M106, the `0o755` dir mode. It also learned to fail
honestly, and that is where the round's real find is: **a mutant that does
not COMPILE was being reported as caught.**

`go test` returns non-zero for a build failure exactly as it does for a
failing assertion, so a mutant that breaks the build reads as detected while
no test ever observed it. Adding one check for `[build failed]` reclassified
**five mutants that had passed three rounds as "caught" and had never once
run**: M10/M11 (`Field` where `pyval.Field` was meant), M49 and M52
(dropping a term left a variable declared-and-unused), and M65 (same).

Four were repairable and are now genuinely caught. The fifth is the
interesting one. M65 deletes the `!ok` from
`if !ok || !pyval.Truthy(v)` — and once it compiles, it **survives**, because
`Get` on an absent key returns a nil value and `Truthy(nil)` is already
false. The guard is a statement of intent, not a decision. Three rounds of
green had been reporting the opposite.

This is the same false-positive class as a site that matches zero times, and
it deserves the same standing rule: **a battery's failure signal must
distinguish "the test caught it" from "the compiler caught it", or the score
counts builds it broke as bugs it found.**

Final: **96 caught / 106, 0 battery bugs, 10 labelled survivors** — six
equivalent-by-construction from earlier rounds, M65 as above, and three
(M103–M105) for the `asDict`/`asList` map arms, which no fixture can reach
until a hand-built caller exists.

## The r2 review of `internal/syshealth` — six enumerations, none of them counted

r2 returned five findings: two production divergences, both **low**, and
three nits. Nothing high, nothing medium, which is what P5 predicts for a
third pass. What makes the round worth writing down is that **four of the
five are the same defect**, and it is not a code defect.

Every one of them is a comment that ENUMERATES something — three lanes, the
arms are already right, the only fixture, five constants, 37 fixtures, C22
answers `"file": null` — and in every case the number was wrong the day it
was written. Not decayed. Wrong at birth, in a file whose entire method is
measuring before claiming.

### F1 — a fourth lane, and the one outcome pyval says is forbidden

`nextCycle`'s doc enumerated three lanes: restart, increment, raise. There
are four. `cycle` is the one field in this snapshot that is *guaranteed to
grow* — it is `int(snapshot.get("cycle", 0) or 0) + 1` on every pass — and
CPython's int is arbitrary precision, so a counter at 9223372036854775807
simply becomes 9223372036854775808 and the snapshot is written.

The port did `return n + 1`. On a 64-bit int that WRAPS, silently, to
-9223372036854775808, and then writes it. `pyval.ErrIntTooLarge`'s own doc
says exactly what a value that gets written must do instead:

> a value that is WRITTEN (the reopen payload's depth) may not. It refuses
> and skips the write rather than emitting a number CPython would never
> produce.

So the increment is range-checked and takes the refusal — which is the lane
a counter *already past* int64 has always taken, through `pyval.Int`. The
two now agree, and `knowngap_test.go` asserts the divergence in the
established convention: closing the gap fails a test rather than passing
unnoticed.

The asymmetry is the part worth keeping in mind: `RenderSnapshot` prints
such a counter correctly, because `pyval.reprNumber` keeps an integer
literal verbatim rather than forcing it through an int64. **Only the writer
is bounded.** The test pins both halves, so a later "fix" that clamps in the
renderer is caught too.

### F2 — "the day a caller appears the arms are already right"

r1 added `asDict`/`asList` because six sites were asserting `v.(pyval.Obj)`
inline while every pyval helper called in the same breath also accepts a
plain `map[string]any`. The comment I wrote for them ended with the sentence
above. It was a prediction, it was checkable, and it was false.

`pyval` knows exactly two Go-native container shapes, and the list one has
**two spellings**: `[]any` and `[]string`. `Truthy`, `TypeName` and `Repr`
all call a `[]string` a Python list. `asList` did not. At five of the six
sites that costs nothing; at the sixth — the rank switch's unhashable-key
arm — a process `status` of `[]string` fell past the `TypeError` case into
`default` and quietly **ranked 3**, where CPython raises
`cannot use 'list' as a dict key`.

There can be no differential fixture for this: the probe feeds both runtimes
through JSON, and JSON never produces a `[]string`. It is the hand-built
caller's lane, and a Go unit test is the only door it has — which is also
why the original claim went unchecked. **A claim about a lane no fixture can
reach needs a test written specifically to reach it, or it is decoration.**

### F4 — the nit that was a real bug in a shared primitive

C36's comment said the 200-code-point clip existed because "the `int()`
message embeds the repr of the whole offending string." Two claims, both
false. C39 and C47 also reach that clip. And CPython does not embed the
whole repr: `PyErr_Format` uses `%.200R`, so the repr is truncated to 200
code points and the message tops out at 240 characters — **losing the
repr's own closing quote** for any value of 199 characters or more.
Measured on 3.14.3:

```
str(e) == "invalid literal for int() with base 10: " + repr(s)[:200]
```

exactly, for ASCII, Latin-1, astral and quote-containing values alike. The
truncation is by code point: the astral row is the one that tells code
points, bytes and UTF-16 units apart.

`pyval.intFromString` built the message from an untruncated repr. That is
**not observable from `syshealth`** — every clip this module takes is 120 or
200, and the two messages agree on their first 240 characters — which is
precisely why it is worth recording. A wrong comment in one module named a
real divergence in a shared helper that eleven packages call, and the fix
and its pin live in `pyval`, not here. The finding travelled further than
the file it was found in.

### The battery, r5: 99/110, and a battery bug the detector was built for

Four new mutants for the r2 fixes — the `[]string` arm in `asList` (M107),
the `[]string` arm in the rank switch (M108), the range check on the cycle
increment (M109) — all **caught**. M110 (`>=` for `==` in the range check)
survives and is labelled EQUIVALENT-BY-CONSTRUCTION: `pyval.Int` already
refuses anything above `MaxInt`, so the two spellings select the same set.

M77 came back a **battery bug** — "site matches 0 times" — because the F2
fix moved the line it targeted. That is the P14 detector doing its job one
level up: a mutant whose site has moved would otherwise have been reported
as caught forever, and nobody would have noticed the arm it was guarding no
longer existed in that spelling. Repaired so M77 keeps its own job (dropping
the DICT spellings) while M108 covers the `[]string` one.

The other thing worth doing while there: **M103/M104/M105's survivor label
had a reason that r2 falsified.** It said "there is no hand-built caller in
the tree for them" — and r2 added one, `knowngap_test.go`. They still
survive, for a different and now-stated reason: that caller hands in a
`[]string` and a `pyval.Obj`, which the type switches above match directly,
so it never reaches `asDict`'s map arm or `asList`'s `[]any` arm. A
survivor's label is only worth what its reason is worth, and this is the
same lens as the rest of the round.

Final: **99 caught / 110, 0 battery bugs, 11 labelled survivors.**

### What the round is actually about

L28 already says a comment asserting coverage decays. This round is the
other half of it: **an enumeration can be wrong at birth**, and the check is
mechanical — count it. Six numbers were stated in this chunk's prose and
comments; six were never counted; four were wrong. Counting them took
minutes, and one of the four led to a fix in `pyval`.

The reviewer-facing version, now folded into L28: *for every number a
comment states, count it against the file as it stands today.* Decay needs
history to diagnose. This does not.

## The r3 review of `internal/syshealth` — a mkdir hiding inside a path

Eleven findings, and the first one is the arc's first **high**. Rounds three
and four are supposed to converge to lows; this one did not, and the reason
is worth more than the fix.

### F1 — `config.memory_dir()` is not a getter

```python
def memory_dir() -> Path:
    p = workspace_root() / "memory"
    p.mkdir(parents=True, exist_ok=True)
    return p
```

The directory comes into existence **as a side effect of asking for the
path**. `_snapshot_path()` calls it, and `run_health_probes` spells its
critical section

```python
with locked_write(_snapshot_path()):
```

so the mkdir happens while Python is still evaluating the ARGUMENT — before
`load_snapshot`, before the probe loop, before anything. The port had
`SnapshotPath` as a pure `filepath.Join` and the only `MkdirAll` inside
`WriteSnapshot`, which runs *after* every probe.

Measured on CPython 3.14.3 against a workspace whose `memory/` cannot be
created:

| | CPython | the port |
|---|---|---|
| read-only workspace root | `ran=0, silent=[]`, **0 probes called** | `ran=2, silent=["p1","p2"]`, 2 probes called |
| `memory` shadowed by a file | `ran=0, silent=[]`, **0 probes called** | `ran=2, silent=["p1"]`, 2 probes called |

Two runtimes disagreeing about whether seven live-store probes execute at
all. Every decision in the module was right; the SHAPE was wrong — L48 at
its plainest, and this time the flattening was not even visible as code. It
was a mkdir hiding inside something that reads like a getter.

**Why 47 fixtures could not see it.** C45 and C46 reach the failed-write
lane by chmod-ing a memory directory that already EXISTS. Nothing in the set
ever removed it. "The memory dir is there" was an assumption forty-seven
fixtures shared and none stated — the most expensive kind, because a shared
assumption looks like coverage.

The fix moves the mkdir into `SnapshotPath`, which now returns an error, and
`LoadSnapshot` returns one too: five failures collapse to `{}` on purpose,
and the sixth — the path itself — does not, because CPython raises there.
`RunAndPersist` re-check the path before the load, reproducing the argument
evaluation rather than relying on `LoadSnapshot` to discover the same thing
one line later. That pre-check is provably redundant (the mkdir is
idempotent) and it is kept anyway, labelled: L48 says port the shape.

Two fixtures pin it, deliberately by different mechanisms. **C48** chmods
the workspace root; **C49** puts a regular FILE where `memory/` belongs and
needs no chmod at all — so it still means something under a root test
runner, where `chmod 0555` stops stopping anything. Reverting the fix fails
both, with `ran=2` against CPython's `ran=0`.

### F7 — the normaliser needed its own test, and no fixture could have given it one

`canon` rendered every `json.Number` through `Float64()`. Past 2^53 that
makes 9007199254740992 and 9007199254740993 the same value — on `cycle`, the
one field this module guarantees grows.

The part worth keeping: **a fixture cannot catch this, ever.** `canon` runs
over both sides, so a lossy transform applied to two agreeing sides leaves
them agreeing. Adding a big-counter fixture (S8, S9) does nothing — they
passed against the broken `canon`. The only thing that catches it is
asserting the normaliser's contract directly, which is now
`TestCanonKeepsTheDistinctionsItIsAllowedToKeep`: what it MUST equate (the
spellings a JSON round trip changes) and what it must NOT. Reverting `canon`
fails four of its assertions.

`canon` now renders numbers to a `"num:"`-prefixed canonical text. The
prefix is not decoration — without it the number `1` and the string `"1"`
compare equal, and `evidence`, `status` and `narrated` all hold strings that
can be digits. Trading one blindness for another is not a fix.

This is L51's sixth instance and its sharpest: instances 1–2 erased a value,
3–5 erased the question, and this one erased a distinction *symmetrically*,
which is the version no differential fixture can reach.

### F8 — "equivalent" and "nothing calls it" are different claims

M103–M105 were labelled EQUIVALENT-BY-CONSTRUCTION. They are not equivalent:
M103 makes `RenderSnapshot(map[string]any)` raise where CPython renders it
fine, and M105 makes its output order non-deterministic. They survive
because nothing calls them that way. Relabelled UNREACHED-BY-ANY-TEST, which
is the weaker and true statement — and the distinction matters because a
reader who sees "equivalent" stops looking, which is precisely how r1's M6
got mislabelled one lane over.

### The other seven

All low or nit, all the same lens r2 minted, and all confirmed by counting:
`nextCycle`'s doc still said "three lanes" over a five-row table after r1 and
r2 each added one; `asDict`'s said six inline assertions where there were
seven; `Summary`'s four-row `except` table listed a fourth row that is
**unreachable by construction** — `_narrate_transition` wraps its entire body
including the import in `try/except Exception: pass`, so no input produces it,
which makes "measured" the wrong word; the package doc justified leaving the
lock to the caller "because RunCycle does not touch the disk at all", a reason
r1's own restructure had already falsified; `R8 pins both halves at once` when
R8 has one process and a rank is unobservable with one process; C39's byte
count described the clipped result while C38's described the message; and the
package doc cited C26 for a stale `evidence` the fixture did not carry.

That last one was fixed by adding the evidence to C26 rather than trimming
the sentence — the doc's claim was right, the fixture was short.

### The battery, r6: 114 mutants

Six sites moved under the F1 fix and were repaired before the run, not
after — running the uniqueness pre-check first is the only reason they were
not six silent false CAUGHTs. Four new mutants: M111 is the F1 finding
itself (caught by C48/C49), M112 and M114 are the error lanes the fix
introduced, and M113 is the redundant pre-check, labelled.

M112 and M114 survived the first pass, and both were the same shape: the new
error had exactly one tested caller-path. `TestTheMemoryDirFailureReachesEveryEntryPoint` closes them by calling `LoadSnapshot`, `WriteSnapshot` and
`RunAndPersist` directly on a broken workspace. M114 needed one more turn of
the screw — a `WriteSnapshot` that ignores the path error still fails, just
further downstream, so the test asserts the error **names the directory it
failed on**. "Some error came back" could not tell the two apart.

## The first thing in this tree that Go cannot port at all

Every divergence recorded above is a decision one runtime makes differently
from the other. This is not that. Three runtime modules parse **Python
source with CPython's own `ast` module**, and there is no Go expression of
that — not a harder one, not a slower one, none.

| module | lines | `ast.` uses | what it parses for | reached from |
|---|---:|---:|---|---|
| `codebase_graph.py` | 492 | 18 | the call graph injected into planning context (imports, calls, top-level defs, a stack-based visitor) | `loop_planning` |
| `bughunter.py` | 345 | 10 | Maro's static self-scan (bare excepts, mutable defaults, shadowed builtins) | `doctor` |
| `artifact_check.py` | 735 | 5 | `_python_is_inert` — layer 2 of the fabrication check: does this `.py` print anything when run | `loop_execute`, `loop_planning`, `agent_loop`, `run_curation` |

Two of the three are on the **core loop**, not in a corner.

The reason this is different in kind: `ast.parse` is not a library the port
can re-implement from a spec. It is CPython's grammar, its version-specific
node set, and its exact error behaviour, and `_python_is_inert` depends on
the node TYPES (`ast.FunctionDef`, `ast.AnnAssign`, `ast.Expr` wrapping an
`ast.Constant`) rather than on anything a regex could approximate. A Go
reimplementation would be a Python parser — a project, not a tranche — and
every version skew between it and the interpreter actually running the
workspace's code would be a silent wrong answer in a check whose entire
purpose is catching a fabricated claim.

**Three honest options, none of them free:**

1. **Leave these three in Python.** The port becomes a hybrid runtime with
   a named Python boundary. Cheapest, and it makes "Go replaces Python" a
   claim with an asterisk.
2. **Shell out** — `python3 -c` for the AST question, results as JSON. Keeps
   the logic in one place and correct by construction, and re-introduces the
   interpreter start-up cost the port exists to remove, on the core loop.
3. **Substitute a cruder Go-side rule** (regex for `if __name__`, a
   `go/scanner`-style tokeniser). This is not a port: it is a different
   check that agrees with the Python most of the time. L46 in its purest
   form, and the divergence count is unbounded because nobody can enumerate
   the rules Python's grammar has.

Recorded here rather than decided, because the choice is Jeremy's and it is
the first place the port's ceiling is a property of the LANGUAGE rather than
of how much has been ported. Everything else on the ledger is work;
this is a boundary.

Worth noting what it does NOT block, and this got sharper once the ground
truth was actually captured. The first draft of this paragraph said
`artifact_check`'s layers 1 and 3 are portable and layer 2 is not. Measuring
it says something better: **layer 2 is portable except for the one
predicate.**

Patching `_python_is_inert` with a scripted per-file answer lets the probe
measure the whole of `_python_candidates` and `_inert_output_verdict` — the
two-part claim gate, candidate collection, dedup by resolved path, and all
three fail-open returns — with `ast` nowhere in the picture. 191 fixtures
were captured that way, before any Go was written (L49). So the Go seam is
an injected predicate:

```go
IsInert func(source string) (inert bool, known bool)   // nil = layer 2 off
```

and roughly **twenty lines** of CPython stay un-portable, not 735. The
boundary above is real and the decision is still Jeremy's; what changes is
its size. Option 2 (shell out) shrinks to one question about one file rather
than a module living behind a subprocess, and option 1 leaves a predicate in
Python rather than a check.

The exclusion still gets named in the package doc, which is the same
discipline `check_system_health`'s environment probes got.


## Both engines, one workspace, six commands — the first real comparison

Everything above this line is a differential against CPython running
*fixtures*. This is the other question, and the one the standing goal
actually asks: point both runtimes at the **live workspace** and see whether
they say the same thing.

`go/tools/engine-compare.py` does it. Six read-only renderers, both engines,
byte-compared:

| command | Python entry point | Go | result |
|---|---|---|---|
| `metrics` | `cli.py metrics` | `maro metrics` | identical (1,732 B) |
| `introspect --latest` | `python -m introspect` | `maro introspect --latest` | identical (1,605 B) |
| `introspect --patterns` | `python -m introspect` | `maro introspect --patterns` | identical (671 B) |
| `introspect --history 5` | `python -m introspect` | `maro introspect --history 5` | identical (356 B) |
| `task list` | `python -m task_store` | `maro task list` | identical (**117,489 B**) |
| `task status` | `python -m task_store` | `maro task status` | identical (47 B) |

**6 identical, 0 differ, 0 refused**, against ~30 directories of real
workspace data — 117 KB of task rows on the largest, which is a far denser
sample than any fixture set in this file.

Three things about the harness are load-bearing, and two of them are lessons
this port paid for already.

**One workspace path, not two.** The obvious design gives each engine its
own copy so neither can see the other's writes. It also makes every command
that prints its resolved workspace — `maro inspect` does, deliberately —
differ on the *path alone*, and the only way to compare them again is to
normalise the path out. That is L51 exactly: the assertion moves into the
normaliser. So both engines get the SAME path, and the pristine tree is laid
down again before each run. Neither can see the other's writes because
neither is running when the other's tree is created.

**A live reading is elided by SHAPE, on both sides, and reported.** The
metrics report embeds `Computed: <now>`, and the two engines run seconds
apart. The first version of this table said `metrics` was identical — it was
not; the two runs had simply landed in the same second, and the next run
caught it. The fix is not to strip the line: it is to elide it only when it
matches a positive shape on *both* sides, require the counts to agree, and
print what was elided on every run:

```
metrics   identical (1732 bytes)  [elided metrics report header timestamp
                                   py='Computed: 2026-08-26T19:29:06Z'
                                   go='Computed: 2026-08-26T19:29:10Z']
```

A port that stopped printing its timestamp fails here rather than passing
quietly. Same discipline as `syElideWriteError` in the syshealth
differential.

**`~/.maro` is asserted out of reach before anything runs**, and the live
workspace is only ever the source of a copy. The box's real ledgers were
overwritten once by a probe that wrote where it read; that is a rule now,
not a habit.

### What this does and does not show

It shows that on real stored data, the ported readers agree with the Python
byte for byte — including the JSON-shaped task rows, which is where a
key-order or float-formatting divergence would surface first, and including
`metrics`' computed percentages.

It does not exercise the loop, spend a token, or write anything. A
divergence that only appears while WRITING is invisible here, and that is
the next harness, not this one. The honest summary is: **the read path is
comparable and it matches**; the write path has fixtures but no live
comparison yet.

Also worth recording because it cost ten minutes: `introspect` and `task`
are their own console entry points in the Python (`maro-introspect =
introspect:main`, `maro-task = task_store:main`), not `cli.py` subcommands —
only `metrics` is in `_COMMAND_HANDLERS`. The Go folded all three under one
binary. The argv shapes differ on purpose; only the output is compared.

## What the seventh comparison row was actually hiding

The both-engines harness answered six rows identical and then differed on
the seventh. The divergence itself was one line — a `workspace:` print in
a read-only lane — and it took ten minutes to fix. What took the rest of
the day was the question it raised: *why did six rows agree?*

The answer is that a comparison row runs against whatever the live
workspace happens to contain, and this one contained `count=2`. Two
renders as `2` whether the number arrived as a Python int or as a Go
float64. Every other value in that report was a string. So the row was
byte-identical for a reason that had nothing to do with the code being
right, and `count=1000000` — which renders `1e+06` through Go's default
verb — would have differed on the same line the same day.

That is the general shape, and it is worth stating plainly because the
comparison harness is the most persuasive instrument this port has:

> A differential over PRODUCTION DATA tests the code against the values
> that data happens to hold. Agreement is evidence about the corpus at
> least as much as about the code.

The fix for the render is the one this port keeps arriving at: the bug
was in the DECODE, not in the format verb. A plain `json.Unmarshal` into
`[]map[string]any` types every number as float64, and a float64 has
already forgotten whether it was written with a decimal point. Fixing the
verb would have moved the wrongness somewhere else. Decoding with
`UseNumber` keeps the literal, and `pyval.Str` then makes the int/float
call the way `json.loads` made it.

`UseNumber` needs a `json.Decoder`, and a Decoder is not a drop-in for
`json.Unmarshal`: it stops at the end of the first value, where Unmarshal
refuses trailing content and `json.loads` raises `Extra data`. Reaching
for a Decoder therefore silently widened what counts as a well-formed
row. `decoderAtEOF` carries that strictness across, and has its own
four-case pin — a reminder that a mechanical-looking substitution can
change a behaviour nobody was thinking about.

### Twelve fixtures, four of which are the test

The friction-summary differential has twelve rows. Eight of them pass
against the *broken* code once `UseNumber` is in place, because
`json.Number`'s default rendering agrees with Python for every literal
already spelled the way Python respells it — and most literals are.

The four that discriminate are the ones CPython RE-SPELLS:

| literal | `json.loads` → `str` | `%v` over `json.Number` |
|---|---|---|
| `1e21` | `1e+21` | `1e21` |
| `1.50` | `1.5` | `1.50` |
| `-0` | `0` | `-0` |
| `null` | `None` | `<nil>` |

Without those four rows the render fix survives its own mutation. The
battery said so: the first run of it reported `MI-1 SURVIVED`, and the
finding was not in the code but in the fixtures.

## A resolution that was never measured, and a comment that said it was

Chasing the mkdir question one level down led to
`config.workspace_root()`, which is `Path(val).expanduser().resolve()`.
The port did the `expanduser` half. A comment on the Go function recorded
the omission as a decision:

> Python also calls .resolve() on the workspace... Deliberately NOT
> ported: it only changes an answer when a caller chdirs between two
> resolutions, and resolving symlinks here would make Go disagree with
> Python about which path STRING a probe should assert — the thing that
> rule is for. Named so the next reader knows it was a decision.

Both grounds are false, and measurably so:

- `.resolve()` absolutizes a relative path, follows symlinks, and pops
  `..` against the followed path. A workspace given as `./ws`, or reached
  through a symlink, differs on the FIRST resolution. Nobody has to
  chdir.
- Python resolves. So *not* resolving is what made the two runtimes
  disagree about the string. Porting the call is what makes a probe's
  assertion mean the same thing on both sides.

The comment is the interesting part. It is specific, it cites the right
standing rule, and it reads exactly like a considered decision — which is
what let it survive a review round. What it never had was a fixture. And
it could not have had one that failed, because every test in the suite
set `MARO_WORKSPACE` to a `t.TempDir()` path: absolute, symlink-free,
already clean. On such a path, resolving and not resolving give the same
answer.

> **L52.** A decision recorded as deliberate is still a claim. The
> comment's confidence is not evidence, and neither is a green suite when
> every fixture sits on the side of the input space where the two
> candidate behaviours agree. Ask what input would tell them apart, then
> check whether the corpus contains one.

This is L28's other half again — *wrong at birth, not by decay* — but
about a rationale rather than a count, and it is worse in one specific
way: a stale enumeration is falsified by reading the file, while a stale
rationale is falsified only by running something.

### Two bugs in the primitive underneath

`pypath.Realpath` already existed, documented as
`os.path.realpath(strict=False)`, and had been through an adversarial
round that fixed its cycle handling. It was built on `filepath.Abs`, then
`EvalSymlinks` on the parent, then a walk of the final component's links.
Under the existing differential — a farm of `<dir>/<name>` entries, one
component, under a directory that exists — that is indistinguishable from
the real thing. Two components tell them apart immediately:

| path | CPython | this port |
|---|---|---|
| `<r>/missing/a/b` | `<r>/missing/a/b` | *refused* |
| `<r>/deeplink/..` (deeplink → `<r>/real/deep`) | `<r>/real` | `<r>` |

The first is the function's own documented failure mode — its comment
says callers read a refusal as "no such path" and skip work CPython
performs — reached by a path that simply does not exist yet, which is the
ordinary case for a workspace root, not an exotic one. The second is
`filepath.Abs` calling `Clean`: `..` collapsed lexically before any link
was followed, where CPython pops the ALREADY-RESOLVED path.

Both are gone now, by translating CPython 3.14's `posixpath.realpath`
rather than approximating it again: a stack of unresolved parts, a `path`
that is absolute throughout, and a `seen` map with three states (absent /
unresolved / cached). Thirty rows in the differential, and the anti-vacuity
check requires CPython itself to have produced the two discriminating
answers before any comparison runs.

One mutation survived — replacing CPython's `part_count` with the stack
length — and it survived because it is genuinely equivalent: the marker
entries only write to `seen`, never to `path`. The comment I had written
claimed conflating them "is how a marker gets processed as a path
component", which the battery falsified. That correction is in the file,
because a comment that asserts a mechanism is a claim like any other, and
this port has now been wrong about one twice in one chunk.

### And a third private copy of expanduser

The differential was written to cover `workspace_root` and `_maro_dir`
*together*, since the two differ by exactly that one `.resolve()` call and
a test covering only one cannot tell a missing resolve from a spurious
one. That pairing immediately caught something neither function was
suspected of:

    MARO_USER_DIR=<w>/real/     py  <w>/real     go  <w>/real/

`config.expandUser` was built on `filepath.Join`, which Cleans. Python's
`Path(val).expanduser()` normalises (a trailing slash goes, `//`
collapses) but does NOT remove `..`. `Workspace()` hid this, because
`.resolve()` runs afterwards and washes it out on any tree without a
symlink in the way. `Home()` has no second chance — and `Home()` is where
the user config tier is read from.

It was the third private implementation of one Python operation in this
tree, which is the defect `pypath`'s own package doc names. It is now a
call into `pypath`.

## The mkdir that lives inside a name

`config.memory_dir()` is not a join:

```python
def memory_dir() -> Path:
    p = workspace_root() / "memory"
    p.mkdir(parents=True, exist_ok=True)
    return p
```

Five of `config.py`'s seven path helpers do this — `memory_dir`,
`output_dir`, `projects_dir`, `skills_dir`, `personas_dir` — and two do
not, `secrets_dir` and `playbook_path`. So "resolve the memory directory"
is a function that CREATES, at a MODE, and that can FAIL, and all three of
those are observable from outside.

The syshealth r3 review rated the first instance HIGH: CPython aborts
`run_and_persist` before running a single probe on a workspace whose
`memory/` cannot be created, while the port ran every probe and reported
them all healthy. This chunk does the port-wide half — every Go site that
stands for a Python line reading `memory_dir()`.

### Why it was invisible in 47 fixtures

Every existing test built its workspace with `t.TempDir()` and then wrote
into it. `memory/` therefore always existed by the time anything resolved
it, and a helper that creates it is indistinguishable from one that does
not. The property is not subtle — it is that a directory appears — but no
fixture had a workspace in which it was absent *and* something looked.
That is the same shape as this chunk's previous two findings: a claim
about behaviour with no input in the corpus that could tell the two
behaviours apart.

The differential therefore measures three things per call rather than one:
whether `memory/` exists afterwards, its MODE, and whether the call
raised. Four workspace shapes: fresh, already-created, shadowed by a
regular file of the same name, and read-only.

### The mode is compared against CPython, never a literal

`Path.mkdir()` passes `0o777` and lets the umask narrow it. Asserting a
literal `0o775` in the Go test would pin the umask of whoever ran the
test, not the behaviour — and it would pass on this box while failing
under a service with `umask 022`, where CPython would produce `0o755` and
so should the port. `record.NewDirMode` is `0o777` for exactly this
reason, and the test compares against the string CPython itself printed.

That distinction is what mutant MD-2 exists to catch: replacing
`record.NewDirMode` with `0o755` still creates a working directory, so
every test that only checks existence stays green.

### What the port does NOT do, named

`orch_items.memory_dir()` wraps the `config` call in a `try` and, when the
mkdir raises, RELOCATES the whole memory store — first to
`orch_root()/memory`, then to `cwd/memory`. This port returns the error
instead.

Porting the fallback means porting `MARO_ORCH_ROOT`, which this package
declines for the reason `ProjectsRoot`'s comment already gives: one
resolution order, passed in as an argument, after the 2026-08-16
live-ledger incident. And the divergence is reachable only on a workspace
whose `memory/` cannot be created — where Python's answer is to silently
write somewhere else, which is the behaviour that incident *was*. Recorded
as a knowngap, with a pin, rather than left unsaid.

Three readers gained a swallowed error the same way, because widening the
path helper pushed an `error` into callers with nowhere to put it:
`introspect.LoadLoopEvents` and `LatestLoopID` return empty where CPython
raises, and `orch.IsDrainRunning` returns `false` — a Go bool has no third
state, and `false` lets a second drain start. All three are pinned; the
signature widening is in BACKLOG.

`metrics` is deliberately not on that list. `spend_today`,
`spend_for_loops` and `load_step_costs` each wrap their *whole body* —
path helper included — in `except Exception`, so returning `0` or `nil` is
faithful. I had written a blanket comment claiming the opposite before
checking. The corrected comment says so, in the same file, because a
rationale recorded as deliberate is still a claim.

### The guard was package-local; the property was not

The battery over this chunk caught 4 of 7 and the three survivors all say
the same thing. `internal/orch`'s four-shape differential was green
throughout, and reverting `introspect.EventsPath` (MD-5) or
`metrics.StepCostsPath` (MD-6) to a pure join changed nothing any test
could see. The property is port-wide — every site standing for a Python
`memory_dir() / name` line — and the guard I wrote lived in one package.

That is the same failure as the 47 fixtures one level up, and it is worth
separating from an ordinary missing test: nothing was *unwritten*. The
assertion existed, it was correct, and it covered the wrong blast radius.
A differential written where the CPython measurement is convenient does
not follow the property into the packages that reuse it. Each of the two
now carries its own pin, and neither re-measures CPython — that is done
once, against `config.memory_dir()` itself, in `internal/orch`.

The third survivor, MD-7, is the honest kind. It changes the mode on
`RecordStepCost`'s SECOND `MkdirAll`, and it survives because by the time
that line runs `StepCostsPath` has already returned without error, so
`memory/` exists and `MkdirAll` does not chmod an existing directory. The
comment I had written at that site read as though the mode change fixed
something observable. It does not. The only input that separates the two
modes is `memory/` vanishing between the two calls — which is precisely
the race Python's redundant call exists to survive, and a window this
suite cannot open deliberately. Recorded as unpinned, with the reason,
rather than given a test that cannot fail.

Two comments corrected in this chunk, both of the same kind: a rationale
that sounded like a finding. That is now three in three chunks.

## The projects half, and a lesson that had already been written down

`projects_root()` creates its directory on BOTH branches, so the same
"mkdir inside a name" shape covers `projects/` as well. Three Python call
sites read it and then immediately test `if not <root>.exists()` — a
guard that cannot fire, because the line above just created the thing.
That is three L4 instances in one family: `orch_items.list_projects`,
`sheriff.check_all_projects`, `mission.list_missions`.

What made this slice worth doing separately is that the three do not
agree about what a failure means, and only measuring all of them showed
it:

```
list_projects         no try   raises FileExistsError
list_missions         no try   raises FileExistsError
resolve_project_slug  own try  swallows, still returns its slug
```

A differential covering only the first would have licensed "propagate
everywhere" or "best-effort everywhere". Both are wrong for two of the
three. `ListProjects` propagates, `ListMissions` answers empty (no error
channel — the residual, pinned), and `SlugResolver` is best-effort with
the discarded error named at the site as the difference rather than an
oversight.

### The comment that was already refuted before I read it

`ListProjects` carried this:

> This port keeps path helpers side-effect-free — the resolved store
> being an argument rather than an ambient act is the 2026-08-16
> live-ledger lesson — so a missing root reads as no projects and only
> the writers create anything.

The cited lesson is real and the citation is wrong. The live-ledger
incident was about WHICH STORE gets resolved — a second env var routing
writes somewhere the first did not — and it says nothing about whether
resolving creates a directory. `EnsureMemoryDir(ws)` takes the workspace
as an argument AND mkdirs; the two properties are orthogonal. The comment
borrowed the authority of a correct rule for a claim the rule does not
make. That is the fifth L52.

### Two mutants that caught my own test, not my own code

The battery over this slice returned 4 of 7, and two of the survivors
were defects in the differential I had just written:

- **PJ-4** reverted `ListMissions` to a pure join and survived, because
  the test ran both enumerators against ONE workspace and asserted
  `projects/` existed at the end. `ListProjects` had already created it
  two lines earlier. A side-effect test where something else performs
  the side effect first is measuring nothing. Every row now gets its own
  workspace.
- **PJ-5** dropped the group bit from the directory mode and survived,
  because this differential — unlike the memory-dir one it was modelled
  on — had no mode assertion at all. The property was copied; the
  assertion that makes it observable was not.

**PJ-6 survives and is left surviving.** It reverts `SlugResolver`, which
lives in `internal/missionrun` — a package with no test files at all. A
mutation list trimmed to what the suite already covers would not have
said that out loud. The gap is real, it is named here, and it is the
honest reason the count is 4 and not 5.

## The r4 review of `internal/syshealth` — a lane, and an absence

Not lows-only: two MEDIUM. Both are in the contract rather than the state
machine, which is itself the finding — the reviewer re-derived
`run_health_probes` statement by statement against `RunCycle` and could
not break it.

**F1 — the error table said "four things" and there are five.** `with
locked_write(_snapshot_path())` sits inside the blanket `try`, and
`locked_write` raises `FileLockTimeout` when the lock is contended and
fail-open is off, which is the default. Measured here with the lock held
by another process:

```
{"ran": 0, "silent": [], "transitions": 0,
 "error": "file_lock: could not acquire <ws>/memory/system_health.json.lock
           within 0.0s (holder alive?). ..."}
probes called: 0     snapshot written: no
```

It is neither of its neighbours — `memory/` is fine, so not the mkdir
lane, and `Ran` is 0 rather than N, so not the write lane. This package
DELEGATES the lock to its caller, which makes the omission worse rather
than better: a caller built from that table takes the lock, gets a
timeout, and has nothing telling it the faithful answer. It is the exact
case the lock exists for.

**F2 — `Summary.Error` used emptiness as its sentinel.** Python writes
`summary["error"] = str(exc)[:200]` unconditionally once the except
fires, and `str(exc)` is `""` for an exception raised with no message.
Measured: `{"ran": 0, "silent": [], "transitions": 0, "error": ""}`. The
port dropped the key, making an aborted cycle byte-identical to a clean
zero-declaration one — and any Go caller testing `sum.Error != ""`
concludes the cycle succeeded when it never ran a probe. `Error` is now
`*string`.

What makes F2 sting is where it landed. `TestSummaryToDictKeepsCPythonsInsertionOrder`
exists precisely to pin this structure, across four shapes, and none of
the four was an error with an empty message. A correct assertion, wrong
blast radius — L53, one round after it was minted.

The six LOWs were three overstated enumerations (a present-tense "47
cycle fixtures" that r3 made 50; "thirty-seven diffs" and "every cycle
fixture at once", both asserted rather than counted — the real number is
24, because ten fixtures seed a prior whose entry `Obj.Set` replaces in
place), a fourth private copy of `EnsureMemoryDir`, an incomplete
"WHAT IS NOT HERE" list that was silently missing an observable
emission (the module logger fires on every error lane), and a `prior`
passed by value — which propagates a probe's write to an EXISTING key
and silently loses a NEW one. That last is fixed by a pointer, and the
pin was checked against a reverted copy before being believed.

## Where the port actually is: three numbers, two of them ceilings

Twenty-six chunks in, "how far along is this?" had never been measured.
It had been *estimated* — a remaining-scope figure carried forward in the
backlog and adjusted by feel each time a module landed. That is the shape
L28 warns about: a number that nobody has counted since it was born.

So count it. Three different questions, three different answers, and the
gap between them is the interesting part.

**The Go side.** Forty-five packages, 57,706 lines of source and 77,839
lines of test. The test-to-source ratio is 1.35 overall, which is what a
differential port looks like — most of those test lines are embedded
CPython probe sources and fixture tables, not assertions.

Two packages have no test file at all: `internal/pyprobe` (275 lines) and
`internal/missionrun` (97 lines). `pyprobe` is the differential harness
itself and is exercised by every probe in the tree, with
`MARO_PYPROBE_REQUIRED=1` as its proof-of-life; `missionrun` is a real
hole and is filed. Nothing else in the tree is untested, though "has a
test file" and "is tested" are different claims and this census only
answers the first.

**The Python side, loosest ceiling.** Of 183 modules totalling 132,730
lines, 61 modules (70,841 lines, 53.4%) are *mentioned anywhere* in the
Go tree — a filename in a comment counts. That is an upper bound on
progress and a weak one: `knowledge_web.py` is 4,828 lines and is
mentioned twice.

**The Python side, better ceiling.** 38 modules (50,321 lines, 37.9%) are
`import`ed by at least one embedded CPython probe. A probe means someone
actually measured CPython's answer for something in that module, which is
much stronger evidence than a mention. It is still a ceiling, because it
credits a module's whole line count for one probed function — `handle.py`
is 4,309 lines and its probes reach a fraction of them.

**The floor.** 122 modules, 61,889 lines — 46.6% of the Python — are
never mentioned in the Go tree in any form. That number is not a ceiling
and not an estimate. It is the part that is definitely untouched, and the
largest single items in it are `loop_report.py` (2,705),
`run_curation.py` (2,135), `container_exec.py` (1,676),
`orch_bridges.py` (1,522), and `navigator_shadow.py` (1,231).

The honest headline is the floor, not either ceiling: **at least 47% of
the Python by line has no Go reference of any kind.** Everything between
37.9% and 53.4% is unresolved by this measurement and would need a
per-function census to settle.

This does not contradict the **23 modules, ~26,800 lines** burndown
earlier in this file, and the two must not be added or compared. That
table counts only modules above ~690 lines and only asks "has this module
been NAMED"; this census counts every module in `src/` and asks three
sharper questions. The burndown answers *what large tranche is next*; the
floor here answers *how much is left*. A reader who takes 26,800 as the
remaining total is off by more than a factor of two.

A caveat about the method, because it is the kind of thing this file
exists to record. The mention census is a regex over `*.py` in Go source
text; the probe census is a regex for `import X` / `from X import` inside
Go test files, filtered to names that exist in `src/`. Both undercount
a module reached only through a package alias, and the mention census
missed `orch.py` entirely — 610 lines, imported by probes but never
written out as "orch.py" in a comment. The probe census caught it. Two
proxies disagreeing is the reason to run both.


## The r5 review of `internal/syshealth` — a pin that could not fail

r4 ended not-lows-only and r5 was supposed to close it. It did not: one
MEDIUM, five LOW, and the MEDIUM is about a fix r4 had just landed.

r4's F7 found that `Declaration.Probe` took `prior` by VALUE, which gets
half of the Python's behaviour right in a way that is genuinely hard to
see — `pyval.Obj.Set` on an EXISTING key writes through the shared backing
array and propagates, while `Set` on a NEW key appends to a local header
and vanishes. The fix was a pointer. The pin was a two-case table chosen
to separate exactly those halves, and it was checked against a reverted
copy before being believed.

The new-key case was fine. The existing-key case asserted this:

```go
if got != OK {
    t.Errorf("entry status = %v, want the probe's verdict %q", got, OK)
}
```

The probe writes `prior["status"]`, and the cycle then does
`entry.Set("status", status)` unconditionally, one line later, with its
own return value. So the assertion is about the cycle's write, not the
probe's, and it holds for every implementation — including one where the
probe is handed a defensive copy and the prior channel does not exist at
all. The reviewer proved it by building that implementation and watching
the case pass.

That is L53 again, one round after L53 was minted, and it is a sharper
instance than the ones that minted it. Those were assertions in the wrong
PACKAGE. This one is in the right package, in the right test, in the case
written for the purpose, with a comment explaining what it proves — and it
proves nothing. The comment is what makes it dangerous: someone reading
the file finds the property named and the case present and stops looking.

Underneath it was a second thing nobody had pinned. Python reads `prior`
FOUR times after handing it to the probe — `prev_status`, `dict(prior)`,
`_history_of(prior)`, and `narrated` — and only the `dict(prior)` copy was
covered. Hoisting any of the other three above the probe call passed the
entire suite. Each is observable:

```
prev_status  -> last_transition["from"], when a transition fires
history      -> the entry's ring buffer
narrated     -> whether the recovery is narrated AT ALL
```

The fix is three new cycle fixtures, C51 through C53, and they are full
differentials rather than Go assertions: the scripted probe gained a
`write` field on both sides, so a fixture can say "this probe overwrites
`prior['narrated']`" and the snapshot FILE decides who is right. Each
drives one read to a value only a post-probe read can produce, and each
uses an EXISTING key so the read ORDER is isolated from the
pointer-versus-value question r4 already covers.

Battery, on a copy: all three hoists caught, the defensive copy caught.
The first run of that battery expressed the hoists as DELETIONS, which do
not compile — P14, reported by the battery about itself rather than by a
reader afterwards.

The five LOWs were four stale claims and one incomplete absence. Three of
the four are counts: "the one lane where Ran is still 0" (there are
three, and the table twenty lines up says so), "two of the four error
lanes" (it is four of five now, and both halves drifted in opposite
directions), and "three of its nine top-level omissions", which
reconciles under no consistent reading at all — r4 added two bullets to a
list of five, and the only expansion reaching nine makes it four restored
rather than three. That last one is in the bullet whose stated subject is
completeness. The count is now simply gone: a number in a growing list is
a claim that has to be re-derived on every edit and never is.

The fourth stale claim is the interesting one. "Each [of the seven
probes] reads a DIFFERENT live store" is false — four go through
`_recent_outcomes` to `memory/outcomes.jsonl` and two import
`captains_log.query_log`. The tranche conclusion it supports survives, but
the sentence is what a later tranche would have planned its fixtures from,
and it would have planned seven fixture sets where four can share one.

And `nextCycle`'s lane table was missing a lane. CPython's `int()` accepts
any Unicode decimal digit, so a snapshot whose counter is the string
`"٤١"` reads as 41 and the cycle proceeds; `pyval.Int` is ASCII-only and
refuses, aborting the cycle and writing nothing. The residual is named
where it lives, in `pyval/pyint.go` — and was absent from the table here,
which is the failure mode a table has: a reader takes it for the list. The
int64 gap next to it had a row, three paragraphs, and a `knowngap_test.go`
pin. This one now has all three, including an ASCII control, because the
assertion "a non-ASCII counter is refused" also holds for a port that
refuses every string counter, and without the control the pin would not
know the difference.

## `artifact_check.py` slice 1 — two regexes that do not survive transcription

The zero-LLM fabrication check: a build step reports `status="done"` and
narrates "wrote fizzbuzz.py", and this decides whether the filesystem
agrees. Slice 1 is Python lines 1-483 — claim extraction, the workspace
diff, layer 1, and the execution-transcript contradiction. The scavenging
detector below it is another ~250 lines and its own tranche.

191 fixtures, no expected values written down anywhere: every row is an
INPUT, CPython is asked at test time, and the two answers are compared.
The fixture table itself predates the Go — it came from a capture written
and run before any of this package existed — which is the only thing that
keeps a differential from being a transcription of what the port already
does.

**The first regex has no zero-width assertions to spend.** `\b` is
Unicode-aware in Python and ASCII in Go, and RE2 has no lookaround at all,
so `pytext`'s stand-ins CONSUME the boundary character — safe only at the
ends of a pattern. `_OUTPUT_CLAIM_RE` has two INTERIOR boundaries:

```
Python   VERB \b [^.\n]*? \b (?:to|into|at|as)
RE2      VERB (?: NW | NW [^.\n]*? NW ) (?:to|into|at|as)
```

The window's first character carries the boundary after the verb and its
last carries the one before the preposition — the same character when the
window is one wide, hence two branches rather than a `{1,}` that would
demand a minimum of two. Dropping the one-character branch is caught by
the battery.

The zero-width window is dropped entirely, and dropping a case is how a
translation quietly narrows, so the argument is written down: with an
empty window the two `\b`s are the same position, every verb alternative
ends in a word character, and `t`/`i`/`a` are word characters — so Python
cannot match there either. E19 is the fixture that would fail if that were
wrong.

**The second regex has a negative lookahead**, which RE2 cannot spell at
all:

```python
output(?:s|ted|ting)?\b(?!\s+(?:file|to|into|path|dir))
```

It is decomposed into per-branch matching with the lookahead applied to
the tail as an ordinary string check. That is sound ONLY because the
caller needs a boolean: Python's engine backtracks past a failed lookahead
and keeps looking, so "search succeeded" means "some branch matched
somewhere with its own conditions met", which is what an OR over branches
computes. It would not be sound for a caller wanting offsets, and there is
no such caller — if one appears, the decomposition has to be revisited
rather than reused.

The exclusion list carries NO word boundary, which makes it a PREFIX test:
"output tomorrow", "output files", "output directory" and "output
pathology" are all suppressed. Nine fixtures cover that, and
word-bounding the list — the obvious tidy-up — is caught.

**What the differential found on its first run** was a symlink. `os.walk`
defaults to `followlinks=False`, so a symlinked DIRECTORY lands in
`dirnames` and is then never descended into: it contributes nothing at
all. This port classified it correctly and then walked it anyway, adding
every file under the link target. The comment three lines above said
"neither descended into nor recorded". A comment can be right about the
intent and wrong about the code beneath it, and only the fixture knows.

**What the battery found afterwards** was two comments claiming pins that
do not exist. `extRE` carries `\n?` because Python's `$` also matches
before a trailing newline, and the comment said "E34 pins it" — E34's
newline never reaches the token, and removing the `\n?` survives. Same
shape for `pyBasename` versus `filepath.Base`: they differ in general and
on nothing this package can reach. Both are now recorded as
equivalent-under-reachable-input with the battery result as the evidence,
which is a different and weaker claim than "pinned", and the weaker claim
is the true one.

It also found a fixture that does not test what it is named. W9 is "an
mtime advance of exactly the epsilon is NOT a change", and
`2000.0 - (2000.0 - 1e-4)` is not `1e-4` in float64 — catastrophic
cancellation at that magnitude lands just below the boundary, so W9 tests
the under-side twice and the boundary never. Flipping `>` to `>=`
survived. W9b sits at a magnitude where the subtraction is exact.

And one finding is about the Python rather than the port. The claim guard
reads:

```python
if not tok or tok in ("/", "./", "../") or tok.endswith("/"):
```

Every one of those three literals ends in `/`, so the middle test cannot
fire. Deleting it from the port leaves all 191 fixtures green, which is
what sent someone to look. It is filed as a Python-side observation and
kept in the port, because the two files disagreeing about something
neither can observe is worse than a dead clause faithfully carried.

Two things are left deliberately unpinned, both inside a region already
declared uncomparable. At the 20000-file cap CPython's answer is not a
function of its input — `os.scandir` returns hash order — so no
differential can pin anything there, and that includes whether an
unstattable file spends one of the 20000. And the fresh-candidate ORDER
comes from iterating a Python set, so CPython has no stable answer either;
the port sorts, that is a decision rather than a port, and what the suite
asserts is only that the decision HOLDS — sixty-four calls, stable and
sorted — because a Go map range would answer differently every run and
every fixture above has at most one fresh candidate.

## The r1 review of `internal/artifactcheck` — three fail-open paths and a
## parser that was a different language

Every finding verified. That is worth stating plainly because the standing
expectation is that 30–50% of adversarial findings are hallucinated, and
the ones that survive are usually the small ones. Here the three largest
were all real, all in the same direction, and all in a module whose job is
to catch a step that says it wrote a file and did not.

**A truthiness test that could not see its own input.** The Python is:

```python
if isinstance(te, dict) and te.get("type") == "tool_use":
    if te.get("input"):
        cmd = te["input"].get("command")
```

The port spelled both reads as Go type assertions to the package's own
`ToolEvent` alias and to `map[string]any` respectively. A Go type switch
needs identity, not structure — so a transcript that arrived through
`json.Unmarshal` (a `map[string]any`, which is what every real caller
produces) matched neither `case`, and the whole contradiction check
silently found nothing. The check exists to catch a step that claims a
write no shell command supports; failing to parse the transcript makes it
answer "no contradiction" for every input. Both reads now go through one
`asMapping` helper that accepts either spelling.

The inner read carries a second thing the first port smoothed over.
`te.get("input")` is a truthiness test on an arbitrary JSON value, and
`te["input"].get(...)` on the line after is a mapping method. A truthy
non-mapping — a string, a list — is an `AttributeError` in Python, not a
skip. The port now reproduces that as a panic with the Python's own shape
in the message, rather than quietly taking the branch the original cannot
take. X14–X21 are the fixtures: truthy string, truthy list, `0`, empty
dict, dict without a `command` key, and three raw `map[string]any`
transcripts sent unconverted.

**A directory test where CPython asks a different question.**
`Path(p).is_file()` is False for a directory, a socket, a fifo, and a
dangling symlink alike. The port asked `st.IsDir()` and negated it, which
answers True for all four of the others. A unix socket in the project tree
was enough to put a non-file into the candidate set. `!st.Mode().IsRegular()`
now. P8b is the fixture, and getting it to exist took a detour: unix socket
paths cap near 108 bytes, and the differential names its temp directories
after the fixture, so the socket is bound inside a short `os.MkdirTemp`
and symlinked into the tree on both sides. `is_file()` follows the link
and still answers False, which is the property under test.

**Eleven `time.Parse` layouts are not `fromisoformat`.** This one is its
own story and the long version is in the `parseISO` doc comment. The short
version: a list of layouts is a different LANGUAGE from a grammar, not a
subset of it, and the measured gap ran both ways — seven forms CPython
accepts that the port refused, and one the port accepted that CPython
rejects. The refusals are not harmless, because `FilesModifiedSince` is
the resume-side half of this guard and a refused stamp returns "nothing
was touched" for a step that touched plenty.

Transcribing CPython's own `_pydatetime.py` looked like the obvious fix
and is a trap: `datetime.fromisoformat` has two implementations that
disagree, the C accelerator is the one that runs, and the readable source
is therefore not the specification. Three measured disagreements are in
the comment. So the grammar was derived by measurement instead — a
generate-and-diff loop that drove divergences 590 → 298 → 47 → 29 → 3 → 0,
then held at 0 across four independent seeds and roughly 610k inputs. What
that loop produced is now a permanent test: 78,115 inputs, 4,165 of them
accepted by CPython, compared by `math.Float64bits` rather than a
tolerance, because a differential that normalises before comparing has
moved the assertion into the normaliser (L51).

The corpus earned its keep inside the same minute it was written. A
comment on the zero-length-fraction arm claimed the branch was
unreachable; the fuzz falsified it immediately with 47 divergences, all of
them `12:34:56.` followed by an offset. The comment now records that
episode instead of the claim: another rationale recorded as deliberate
that was a claim (L52), and the first one in this project falsified by a
machine rather than by a reviewer.

**`st_mtime` is not `UnixNano()/1e9`.** CPython builds it as
`sec + 1e-9 * nsec` — two roundings in a different order — and 26.97% of
ordinary timestamps come out an ulp apart from the Go spelling. Past the
year 2262 it is worse than an ulp: `UnixNano` wraps silently. `pyMtime`
does it CPython's way. Pinning this took its own fix, because Go's
`os.Chtimes` also builds its syscall argument from `t.UnixNano()`, so the
fixture that was meant to compare two engines on one tree was quietly
building two DIFFERENT trees; `acSetMtimeParts` uses `syscall.UtimesNano`
and the probe grew a `mtime_parts` arm to match.

**`pathlib`'s `/` leaves `..` for the kernel; `filepath.Join` removes it.**
Four sites. They agree on every path that has no `..` in it, and they
resolve to different FILES whenever the base contains a symlink — which is
the case A10 now covers. `pyJoin`/`pyNorm` do what `PurePath.__truediv__`
does: drop empty and `.` segments, keep `..`. A comment nearby had
asserted that every path reaching the inert-output reason was built by
`filepath.Join`; it was true and it was also the reason nobody looked.

**And one table that existed three times.** `isWordChar` here, and its
twin in `internal/metrics`, and a third in `internal/skills`, each
carrying its own copy of the Unicode-16-minus-15 `\w` supplement — 5004
code points in 27 ranges — while `internal/pytext`, the package all three
already import for exactly this class of skew, carried only the 80-digit
subset under a comment arguing that hand-copied lists rot. Two siblings
had already overruled that argument with measurements. Worse, the 80 in
pytext are a SUBSET of the 5004, so `DigitClass` matched U+10D40 and
`WordClass` did not — a state CPython has no spelling for. The table now
lives in pytext once, in two notations (a class body for matching, a range
table for the predicate), pinned to each other by
`TestTheTwoSpellingsOfTheWordSupplementAgree`, with a second sweep
asserting every digit-supplement code point is also a word-supplement one.
The two skew tests that used to iterate the deleted local copies now
iterate `pytext.WordSupplementRanges()`, which exists for them and says so.

**The battery: 31 mutants, 26 caught, and four of the five survivors were
equivalent.** The one that was not is the sharpest thing the battery
found. `pyJoin` replaced `filepath.Join` at four sites; the fixture written
for it (A10) drives the FIRST of them, and reverting the second — the
changed-path loop in `pythonCandidates` — left the whole suite green. That
is L13 exactly: a fix at the site that has the fixture is not a fix for the
class. Walk-derived keys never carry `..`, so a divergence there has to
come from the project DIR carrying one, which is not exotic — `<dir>/link/..`
is what hand-composed paths look like, and on macOS `/tmp` and `/var` are
symlinks. P8c drives that shape and catches the revert.

The other four survivors were argued to be unobservable, and an argument is
a claim. So they were proved instead: a Go-to-Go sweep of 414,589 inputs —
every string up to length 5 over `01:.,xZ+-`, and up to length 9 over
`0:.+` — with each edit applied ON ITS OWN, since three edits together can
mask each other. Zero divergences, three times. `mag != 0 &&` is inert
because int64 has no negative zero; the `!isASCIIDigit` half can only fire
where no path out of the break can return true; the len-1/len-3 offset-body
guard cannot admit anything `hhMMSSff` would accept; and `len(in) > 0` is
subsumed by the `!found` fallback three lines later. All four are kept —
they are CPython's own tests, and deleting a transcribed guard because Go's
types make it redundant is how a port stops explaining itself — with the
sweep's result recorded at each site so nobody re-derives it.

Two comments in this file claimed "191 fixtures". The table reached 192
the same day, which makes both of them stale on arrival, and re-counting a
comment to keep it true is the wrong spelling of the conclusion — they now
say "the whole fixture table". That is L28 arriving from a direction it
had not come from before: not a count that was wrong when written, but a
count that was CORRECT when written and had no way to stay that way.

## The r2 review of `internal/artifactcheck` — a flag every caller passes
## and no test did

Nine findings, nine verified, two of them HIGH. Two rounds running at zero
hallucinated findings is worth noting against the 30–50% the standing
practice budgets for, and it is not luck: both rounds were told to bring a
concrete divergent input for every claim, and a claim you have to
instrument is a claim you mostly do not make.

**The one that matters most is not in this package.** Go expands a
character class's case-folds BEFORE negating it. Folding `\p{L}` adds
exactly one code point that is not a letter — U+0345 COMBINING GREEK
YPOGEGRAMMENI, whose fold orbit contains iota. Python's `re` does no such
thing: `\w` and `\W` mean the same set with and without `re.IGNORECASE`.
So under `(?i)`:

```
                       U+0345      CPython
(?i)[\p{L}\p{N}_]      matches     \w does NOT match
(?i)[^\p{L}\p{N}_]     no match    \W DOES match
```

Every regex in this file sets `(?i)`, because the Python passes
`re.IGNORECASE`, and so does nearly every other consumer of `pytext` in
the tree. Both directions were live here.
`extract_write_claims("xͅwrote to a.txt")` is `['a.txt']` in CPython
and was `[]` — a fabrication check deciding a real write-claim was never
made, which is the fail-open this module exists to prevent.
`_claims_clean_success("all tests passed ͅfailed")` is `False` in
CPython and was `True` — the same skew flagging a contradiction CPython
never emits.

**And `pytext` had two tests written to make exactly this impossible.**
They are the ones added a round earlier, sweeping the whole rune space to
pin the class against the predicate. They passed throughout. They compile

```go
regexp.MustCompile(WordClass)
```

and every caller compiles `regexp.MustCompile("(?i)" + WordClass)`. The
object under test and the object in production were different objects.
That is L54, and it is the first genuinely new lesson in several rounds:
the defect is not in what a test asserts but in how it CONSTRUCTS the
thing it asserts about, and the flag every caller passes and the test does
not is precisely where the bug will be. A grep for `(?i)` across the call
sites and the test file was the whole diagnosis.

The fix has three parts, and only the second and third stop it returning.
The exported atoms are wrapped in `(?-i:…)`, which turns folding off for
the class and leaves it on for the literals around it. The invariant sweep
now runs each atom under BOTH constructions. And because a caller can
build its own class out of the exported bodies — where no in-package test
can reach — a source scan walks every `regexp.MustCompile` in the module
and fails on one that combines `(?i)` with a raw spliced body. It found
three more sites on its first run, in `closure`, `guard` and this file,
none of which anyone had looked at.

**A frozenset membership test is not a lookup.** `te.get("name") in
_EXECUTION_TOOLS` raises `TypeError` when the name is UNHASHABLE — a list
or a dict, which is exactly what `json.loads` produces — and the blanket
`except Exception` turns that into the fail-open verdict. The port's
comment said the opposite in so many words: "a non-string name is not in a
frozenset of strings, and answers False rather than raising." True for a
number, true for a tuple, false for the two types an adversary would
actually send. A transcript whose FIRST event carried a list name came
back as a confident `execution-contradiction` where CPython says "the
check broke, do not trust me". This is the hazard X15/X16 were written for
one comprehension earlier, and the port had ported one and not the other.

**`.timestamp()` on a naive stamp is not `time.Date(…, time.Local)`.**
They agree for every wall time that exists exactly once, and part by
exactly 3600 seconds in a DST GAP, where the wall time does not exist at
all and each runtime invents one. `2026-03-08T02:30:00` in Denver is
1772962200.0 to CPython — the LATER reading — and was 1772958600 here.
The file's own named residual had this precisely backwards: it named the
DST REPEAT hour as the unmodelled case, and the repeat hour is where the
two AGREE. `pyMktime` now transcribes CPython's `_mktime`, max/min gap
rule included. The fixture discovers both transitions from the live zone
by bisection rather than hardcoding a date — a hardcoded transition stops
being a transition when a zone's rules change, and the fixture would go on
passing while testing an ordinary Sunday — and renames itself to say so
out loud on a zone that has none.

**`errors="replace"` substitutes maximal subparts, not bytes.**
`b"a\xe2\x82b".decode("utf-8", "replace")` is three characters; the port's
per-byte loop produced four. This is L49 again — a builtin whose
implementation exceeds its definition — and the reason it survived is
instructive: the four inputs a reviewer reaches for first (a lone lead
byte, a surrogate encoding, an overlong NUL, two invalid bytes) all agree
under either rule. Only the TRUNCATED sequences separate them.

**The `pyJoin` class had two more sites, in a different function.** Third
time. A10 pinned the first, P8c pinned the second after the r1 battery
found it, and `SnapshotDir`'s walk — which feeds both snapshots and
therefore the whole layer-1 gate — still built its child paths with
`filepath.Join`. A project dir reaching a real directory through
`link/..` made every stat in the walk miss, so `ChangedSince` returned
`{}` and layer 1 reported a fabrication on a file plainly on disk. L13
says a fix at the site that has the fixture is not a fix for the class;
this is the same class refusing to be closed three times, and what finally
closed it was asking the question the other way round — not "where did I
change `filepath.Join`" but "where does this file still call it".

Two lower findings are about claims rather than behaviour, and both are
L52. A residual said the port's ASCII-only digit reads were a known gap
because "CPython's `int()` accepts any Unicode decimal" — measured, they
are not: the C accelerator never calls `int()`, it uses `Py_ISDIGIT`, and
`fromisoformat` with fullwidth digits raises in every position while
`int("１２")` is 12. The residual had been read out of `_pydatetime.py`,
which this file's own "WHICH CPYTHON" paragraph rules out as the
specification — the trap it warns about, sprung inside the same file. And
two more comments named fixtures that cannot detect the property they
defend: E19 fails on a leading boundary whether or not the zero-width
window exists, and S20's "stdout" is followed by a space, so the trailing
`\b` it was cited for changes nothing. The reasoning in both was right.
Only the evidence was.

The last one is about a contract rather than a bug. `asMapping` accepts
`map[string]any` because `isinstance(te, dict)` does — r1's fix, correct —
and its doc went on to call `encoding/json` output "a real tool
transcript". Structurally it is one. By VALUE TYPE it is not: `json.loads`
types `5` as int and `str(5)` is `"5"`, while `encoding/json` gives
float64 and `pyval.Str` gives `"5.0"`. Accepting a shape quietly read as
blessing the types inside it. There is no production caller of
`CheckExecutionClaim` in the tree yet, so nothing enforces the decode; the
rendering is pinned for each Python-typed value and the contract is filed
for whoever writes the caller.

### The r2 battery — 27 mutants, and the two that mattered were fixture gaps

Twenty-seven mutants, derived from the FILES rather than the r2 diff (L9),
with the r1 fixes re-run because a later fix reverting an earlier one is
this project's most common severe finding (P6). Twenty-three caught,
none failed to compile, four survived. Two of the four were predicted
inert and stayed inert. The other two were real, and both were gaps in
fixtures rather than in fixes — the fixes themselves were right, and
nothing in the suite could tell.

**F4d** dropped the `lo, hi = 0x80, 0xBF` reset inside `maximalSubpart`,
so every continuation byte gets checked against the LEAD byte's own
narrowed range instead of widening back after the second. That is a real
behaviour change for exactly two lead bytes — `0xF0`, whose range starts
at 0x90, and `0xF4`, whose range stops at 0x8F — because only a four-byte
sequence has a third byte to widen for. `b"\xf0\x90\x80A"` is one U+FFFD
in CPython and two under the mutant.

It survived because the corpus had precisely one truncated four-byte row
and it was the emoji, `F0 9F 98`. Every byte of that sequence is ≥ 0x90,
so the narrowed range accepts it — the row exercises the code path, gets
the right answer, and cannot distinguish the two spellings. A row that
runs the line you care about is not the same thing as a row that would
notice the line being wrong, and the difference here was two hex digits.
Four rows now carry a continuation byte the lead's own range would reject,
including the boundary `F0 90 8F`.

**F1e** reverted intent's raw `(?i)` class splice — one of the three sites
the F1 source-scan guard turned up — and no intent test moved. The guard
that found the site cannot defend it: the guard is a pytext test, and the
battery ran only `./internal/intent/`, so from that package's point of
view the wrapper had no consequence at all. intent's differential corpus
is thorough about non-ASCII and had no U+0345 in it.

Placing that row took one more observation than expected. U+0345 anywhere
in the filename does nothing, because `\S*` precedes the class and is
greedy over anything non-space — it simply eats the offending code point
and `[\w-]+` still finds its ASCII character, so both engines answer True.
The stem has to be U+0345 ALONE, and then CPython says False (its `[\w-]`
does not match it) while the unwrapped Go class says True. Both mutants
re-run after the fixtures: caught.

The other two survivors are recorded as equivalent mutants at their sites
rather than deleted (L8). `SpaceClass`'s `(?-i:…)` is inert today because
the fold closure of that body is the body, measured; it stays, because the
wrapper is a property of how pytext hands a class to a caller that may set
`(?i)`, not of today's body. `decodeReplace`'s `utf8.Valid` fast path
returns what the loop below it returns — the correct result for a
pure-performance branch is that removing it changes nothing.

## The r3 review of `internal/artifactcheck` — two fixes that stopped one
## site short, and a contract nothing tested

Eight findings, eight verified. Three rounds now at zero hallucinated, and
this one had the sharpest shape yet: **both HIGHs were an earlier round's
own fix, correct as far as it went and one site short.**

**F1.** r1 found `filepath.Join` where the Python uses pathlib's `/`, which
keeps `..`. r2 found two more sites in `SnapshotDir`'s walk and wrote, at
the site, "the fix that had two sites and got four". It had five. The one
it missed is the `is_dir()` probe for a symlink, three lines above the file
join it did fix — so under a root that reaches a real directory through
`link/..`, the stat misses, a symlinked DIRECTORY is misclassified as a
file, and it gets a snapshot row CPython never emits. `os.walk` puts a
linked directory in `dirnames` and does not descend, so it contributes
nothing at all; the port gave it a row of its own.

One spurious row is the whole failure. `ChangedSince` goes non-empty,
layer 1 never fires, and a step that wrote nothing but claimed
`"wrote the report to report.md"` comes back `fabricated=false`. The
reviewer's randomised tree differential put a number on it: 11 of 25 roots
containing `..` diverge, 0 of 95 without one.

W21 could not see it, and the reason is worth keeping: W21's `..` root has
no symlink among its entries, so the branch never ran. A fixture that
exercises a function is not a fixture that exercises its branches.

Both sweeps were derived from what had CHANGED rather than from every
remaining `filepath.Join` in the FILE. That is L9 pointed at a fix instead
of at a test, and it is the same mistake in the same file twice.

**F2.** `asMapping` accepted `ToolEvent` and `map[string]any`. `pyval.Obj`
is a slice and matched neither — so a transcript decoded exactly the way
`asMapping`'s own doc says a caller MUST decode it was invisible end to
end. Every event skipped; the answer `judged=true`, `"no execution in
transcript"`. Not a miss: a clean bill.

The history is the finding. r1 discovered the function was blind to
`map[string]any` and added that arm. r2 wrote the paragraph above it
explaining that `map[string]any` is the WRONG decode and a caller should
use `pyval.Obj` instead. Neither round noticed that the function did not
accept the thing the paragraph prescribed. For a full round the doc and
the code disagreed, forty lines apart, in the same file — and the doc was
right. A prescription no test exercises is a comment, not a contract, and
X29–X33 are now the third construction of the same fixture inputs, with a
guard that fails if the elements ever stop decoding as `pyval.Obj`.

**F3 (MEDIUM), and the method matters more than the bug.** CPython's C
`fromisoformat` parses the NUL-terminated buffer `PyUnicode_AsUTF8AndSize`
hands it, and its "is the input exhausted?" checks cannot tell an embedded
NUL from the terminator. `'1970-01-01T00:00:00\x00'` parses;
`'1970-01-01T00:00:00_'` does not. No alphabet in the corpus contained
`\x00`, so 95,312 inputs could not reach the class at all.

I spent a while trying to reconstruct the C from about forty probes, got a
model that fit, and threw it away — a model that fits forty hand-picked
cases is exactly the evidence this project keeps finding to be wrong.
Instead the corpus got the input class: `\x00` in the byte alphabet, and
0–3 NULs swept through EVERY position of seventeen bases. That produced 30
divergences, all one-directional, and the fix was written against that
list. It turned out to be three distinct rules, not one, and no reasonable
model would have guessed the asymmetry:

```
12:34:56\x00        parses      12:34:56\x00\x00        ValueError
.123456\x007        parses      12:34:56\x007           ValueError
…56Z\x00+01:00      is UTC      …56+01:00\x00\x00       ValueError
```

One trailing NUL is tolerated after the H/M/S group (and after a numeric
offset, which reaches the same line). A NUL ENDS the ≥6-digit fraction
tail, unexamined. And after a trailing `Z`, everything from the first NUL
is discarded however much there is. Each rule now carries the pair that
separates it from its neighbour. 96,582 inputs, 0 divergences.

**F4 (MEDIUM).** `FilesModifiedSince` sorted with `sort.Strings`. Python
holds every filename `surrogateescape`-decoded, so an undecodable byte is
the code point `0xDC00+b`; Go holds the raw byte. They agree for all valid
UTF-8, and — this is why it survived three rounds — they also agree for
bad bytes against ASCII and against astral characters. They part in the
two-byte range:

```
names          b"\x80bad", "école", b"zulu"
python sorted  'zulu', 'école', '\udc80bad'
sort.Strings   "zulu", "\x80bad", "école"
```

Under `limit` the CONTENT differs, not merely the order, so the resume
path names different files. The whole non-UTF-8 axis is structurally
invisible to the differential, because the probe round-trips through
`json.dumps` and a lone surrogate cannot be encoded — so W23/W24 carry the
names as lists of BYTE VALUES in both directions instead. The helper went
into `pypath`, not here: `sheriff.py`'s `check_all_projects` has the same
`sorted()` over the same kind of name, and a second copy is how the word
supplement ended up in three files (L14).

**F5 (LOW).** `pytext.Repr` ranged over the string, so an ill-formed byte
arrived as `utf8.RuneError` — and U+FFFD is category So, therefore
printable, therefore written out raw. CPython reprs the same name
`'\udc80z.py'`. Cosmetic per call, and `reprList`'s own doc is the
standard it failed: two runtimes writing into one operator stream have to
agree character for character or a grep keyed on one misses the other's
rows. V1 pins six names, including the truncated sequence that is TWO
surrogates here and would be ONE U+FFFD under the `errors="replace"` rule
— the two rules this port implements about two hundred lines apart.

**F6/F7 (LOW).** A comment said "all 192 fixtures"; there are 223 — the
third stale count in this file, and the number is now gone rather than
corrected. And r2 added four fixtures on ids E28–E31 that were already in
use twenty lines below, so the pin comment naming "E30" named two
unrelated rows. The rule it broke is one r2 wrote down itself, in the U1
comment, in the same commit. That comment has now named E19 (a fixture
that pins nothing), E30 (a fixture that pins two things), and finally E46.

### What the round found clean, measured

Worth as much as the findings, because it says where NOT to look next
time. `maximalSubpart` over 675,484 byte sequences: 0 divergences, every
lead-byte range matching Unicode 3.9. `pyMktime` over 66,048 datetimes in
America/Denver plus ~27,700 more in each of eight other zones — including
Pacific/Apia's 2011 whole-day skip, which is a 24-hour gap larger than
`max_fold_seconds`, and Lord Howe's 30-minute DST: 0 divergences in every
zone. The four text functions over 114,044 rows across two adversarial
alphabets: 0. `SnapshotDir` over 95 randomly generated trees without a
`..` root: 0. And the year-1 residual is correct for the right reason —
`datetime(1,1,1).timestamp()` raises ValueError, not OSError, which
`files_modified_since`'s `except (ValueError, TypeError)` catches.

### The r3 battery — 26 mutants, and the gap was in a helper two hours old

The mutation battery over the round's own fixes: **24 of 26 caught, one
equivalent, one real gap.** The two HIGH fixes were both confirmed by the
fixtures written for them — G1 (the symlink `is_dir()` probe back on
`filepath.Join`) by W22, G2a (`asMapping` blind to `pyval.Obj` again) by
X29–X32 — which is the point of running the battery at all: a fixture
that cannot fail without its fix is not evidence.

**G4c is the finding.** `pypath.FSLess` ends with `return len(ra) <
len(rb)` — the prefix rule, "zz.tx" before "zz.txt". Replacing it with
`return false` passed the entire suite, W23 and W24 included, and those
two fixtures had been written for `FSLess` in the same sitting. The
end-to-end path structurally cannot see it: `FilesModifiedSince` ranges a
map, so a comparator that calls a prefix pair *equal* leaves the pair
wherever the runtime's randomized iteration put it, and the answer is
right about half the time. A test that is right half the time by accident
is a test that asserts nothing about the line.

The fix is not another end-to-end fixture. It is
`TestFSLessOrdersNamesTheWayCPythonSortsThem` in `internal/pypath`, which
drives the whole **ordered pair matrix** of a 15-name corpus against
CPython's `sorted()` and `<` — 225 comparisons, not one sorted list —
with the names carried as byte-value lists because `json.dumps` cannot
encode a lone surrogate. It carries four strict-prefix pairs and a guard
that *fails the test* if `sort.Strings` and CPython agree on the corpus,
because a corpus on which they agree would pass against a byte-order
port and assert nothing. Re-run of G4c against it: caught.

**G2b is equivalent and recorded as such.** Making the `pyval.Obj` arm
keep the FIRST value for a repeated key survives — and it should, because
`LoadsOrdered`'s decoder calls `Obj.Set` per key, so no `Obj` arriving
through the prescribed decode carries a duplicate. Last-wins stays (it is
what `json.loads` does) with the reachability argument at the site, so the
next battery does not re-litigate it. L8, the fourth time this arc.

**Two method notes, both about the battery rather than the code.** G4a
did not compile: `FSLess` was the file's only `pypath` reference, so
reverting the call left an unused import — P14, a mutation is a LIST of
edits, for the third time in this port. Re-run as the two-edit mutation it
always was: caught by W23 and W24. And the hand-rolled re-run of it first
reported the mutant *surviving*, because the scratch copy held only `go/`
and the differential fixtures need a sibling `src/` to find
`artifact_check.py` — without it every one of them skips and the package
reports `ok`. `MARO_PYPROBE_REQUIRED=1` is what turned that silent green
into a named failure, printing "this run would have skipped every
differential and reported ok". The guard was written for CI. It caught a
human.

One more, found by `git status` rather than by the battery: W23 and W24
built their tree at `filepath.Join(base, "fsnames")`, and those two cases
carry no `"files"` key — so the harness hands them `base == ""` and the
join is a RELATIVE path. The fixtures had been creating (and `RemoveAll`ing)
a directory inside the package directory, inside the repository. Fixed with
`t.TempDir()`, and the class closed rather than the instance: the harness
now snapshots the package directory around every answer func and fails the
case that adds an entry. The guard was checked by putting the relative path
back in a scratch copy, because a guard that cannot fail is worse than none.

## The r4 review of `internal/artifactcheck` — the first performance divergence this port has found

Four findings, all four verified against the interpreter before a line was
changed. One MEDIUM, three LOW. The round also re-derived every class r3
fixed from the FILE rather than the diff and found each one complete —
`pyJoin` has exactly five sites in `SnapshotDir`/`existsAnywhere`/
`pythonCandidates` and all five are `pyJoin`; `pytext.Repr` has no
rune-iterating twin; `sorted()` appears exactly once in Python slice 1 and
that site is the only `FSLess` site. `pyMktime` matches `local_to_seconds`
statement for statement in six zones including a 30-minute-DST zone.

### The MEDIUM is not a wrong answer, it is a wrong shape

`boundaryAt` reproduces Python's `\b`, which is O(1). It spelled the test
`[]rune(s[i:])[0]` — a conversion that builds a rune slice for the
**entire remainder** in order to read one code point. `searchStdoutClaim`
calls it once per match of two branches, and a REJECTED match pays the
cost without ending the scan, so the whole search is quadratic in the
length of `result_text` where CPython's `.search()` is linear.

Measured on an ordinary build log — 5,000 lines of `writing output to
build/objN.o`, 169 KB:

```
CPython _claims_concrete_stdout   0.0156 s
port    claimsConcreteStdout      2.279  s      146x
at 240 KB                        20.9    s      ~900x
```

The value is identical in both runtimes, so this is neither fail-open nor
fail-closed. It is still a divergence: the module's own Python docstring
states "Zero LLM, NO code execution, **runs in <10ms**, and fails open"
(`artifact_check.py:17`), and it sits on the per-step critical path. A
contract stated in the source is a contract, and a build step whose
captured output says "output to …" a few thousand times is the ordinary
case rather than the adversarial one.

The fix is `utf8.DecodeRuneInString`. **The pin is allocations, not
time.** A timing assertion on this box is a flake generator, but the
defect has an exact allocation signature — the rune conversion allocates a
slice proportional to the remainder, the decode allocates nothing — so
"zero allocations over a 600 KB string" is the same statement as "this
does not walk the rest of the string", and it cannot pass for an unrelated
reason. Verified failing against the old spelling.

### The LOW that is really the same lesson as r3's two HIGHs

`isoTimestamp` guarded `_mktime`'s ValueError with a hardcoded
`y == 1 && mo == 1 && d == 1`. That is not the rule; it is one of the
rule's answers. `_mktime` raises through its `local()` probe, which
rebuilds a datetime from `localtime()` — and that constructor rejects a
year outside 1..9999. The condition therefore has **two ends**, and which
one is reachable depends on which way the zone's offset points:

```
TZ=Asia/Kolkata   9999-12-31T18:29:59   ->  253402261199.0
TZ=Asia/Kolkata   9999-12-31T18:30:00   ->  ValueError
```

This box is west of UTC, which is the only reason 96,582 generated inputs
plus the reviewer's 553,697 more could not see it: west of UTC the offset
pushes 9999-12-31 further into 9999 and 0001-01-01 back into year 0, so
only the low end is reachable and only the low end was modelled. The
corpus has no zone axis at all.

`pyMktime` now returns `(int64, bool)` and carries the year check inside
`local()`, at each of the four calls CPython makes, in CPython's order —
so both ends fall out of the rule instead of being enumerated. The
hardcoded year-1 case is gone. The fixture varies `time.Local` and `TZ`
across six zones and refuses to pass if no zone produces a refusal.

### Two implementations of one operation, again

`pyJoin`'s doc said the POSIX double-slash root "is left alone by pathlib
and by this function" and "cannot reach here anyway, because every caller
takes an absolute branch first". Both halves were wrong: the local
`pyNorm` split on `/` and dropped empty components, so it collapsed `//x`
to `/x`, and `SnapshotDir`'s walk hands its base straight through with no
absolute branch anywhere. It also answered `""` for `PurePosixPath("") /
""` where CPython answers `"."` — the difference between a path that
exists and one that cannot be opened.

`pypath.Join` already had the rule right, with a differential behind it.
`pyJoin` is now a one-line wrapper and `pyNorm` is deleted. This is the
sentence `pypath`'s own header opens with — two implementations of one
Python operation disagreeing is a defect before they disagree — and it
took four rounds for the second implementation to be noticed at all,
because it was RIGHT on everything any fixture drove.

That comment was the fourth in this file to be wrong about the exact
property it was written to defend.

### And the count was wrong at birth, again

r3 replaced a stale "all 192 fixtures" with "223" **in the same commit
that added nine fixtures**. The table holds 232. So the correction was
wrong the moment it was written — the fourth stale count in this file and
the second one wrong at birth rather than by drift. The number is gone
rather than corrected a third time; the comment now argues for its own
deletion, which is what it had been arguing for all along.

### The r4 battery — 19 mutants, and the gap was a lead byte that is also a letter

15 of 19 caught, one real gap, three equivalents. Both r4 fixes were
confirmed by the fixtures written for them — the allocation pin killed the
quadratic spelling, and all four year-range mutants (removed, west-only,
east-only, off-by-one) died in the zones the corpus now varies.

**H1b is the gap, and it is a nice one.** `boundaryAt` reading one BYTE
where Python reads one CODE POINT survived the entire suite. The reason it
survived is the reason it is worth writing down: the two spellings agree
for every following ASCII character, and they agree for every following
multi-byte **letter**, because a lead byte read on its own is usually
still a letter — 0xC3 alone is U+00C3, `Ã`. They part only when the
following character is multi-byte and NOT a word character, because its
lead byte still is one: 0xE2 alone is U+00E2, `â`. Measured against the
module before anything was written:

```
_claims_concrete_stdout('running the— 42')  -> True    em dash: the boundary holds
_claims_concrete_stdout('running theé 42')  -> False   the control: no boundary
_claims_concrete_stdout('output— 42')       -> True
_claims_concrete_stdout('outputé 42')       -> False
```

S27–S31 drive both `wordEnd` branches with the rows a byte read gets
wrong, plus the accented-`e` controls that keep a fix from over-accepting
— a fix at one branch is not a fix for the class. Re-run: caught by S27,
S29 and S31, with the controls correctly silent.

**H2e and H2f are equivalent, and the corpus was densified before saying
so.** Ignoring `local()`'s refusal at `_mktime`'s third and fourth calls
survives. Rather than argue it, the zone sweep went to half-hourly across
both boundary days in six zones — 288 inputs per zone — and they still
survive. That makes the structural argument credible: the fold probe looks
a day EARLIER, so it can only leave the year range at the low end, and
every instant close enough to year 1 for that is one where the later
probes are out of range too, so the mutant refuses anyway one call further
on. The checks stay, because they are where CPython raises and a
transcription that is right for the wrong reason stops being right the
moment the surrounding code moves.

**H3b is unreachable.** `pyJoin` losing the absolute-right-operand rule
survives because `existsAnywhere` and `pythonCandidates` both take an
`IsAbs` branch first — which is what the Python does (`cp.is_absolute()`).
The rule is pinned in `pypath`, where it is reachable. No fixture added:
one here would pin a path no caller can take.

### A finding the battery did not produce, found while checking a backlog claim

`internal/sheriff/sheriff.go:344` ports `sorted(artifacts.iterdir())[:50]`
as `sort.Strings(names)` followed by `names[:50]`. That is the **W24
shape** — the truncation means the two runtimes consider different FILES,
not the same files in a different order — and `newest` over that set
decides the dormancy verdict. Python sorts `Path` objects; measured, that
ordering is the surrogateescape decoding compared by code point:

```
Path-sorted   zz.tx  zz.txt  éa.txt  ကb.txt  \x80z.txt  \xffq.txt
str-sorted    zz.tx  zz.txt  éa.txt  ကb.txt  \x80z.txt  \xffq.txt
byte-sorted   zz.tx  zz.txt  \x80z.txt  éa.txt  ကb.txt  \xffq.txt
```

The correction that matters is to my own record: this had been filed as
arriving "a tranche before the code", and the code is already written and
already carries the divergence. Nine of the tree's 28 non-test
`sort.Strings` sites sort filenames; the rest sort JSON object keys, which
are valid UTF-8 by construction and must be left alone rather than
churned. Each of the nine needs its Python counterpart read first, because
some of them are the port's OWN determinism guarantee over something
Python iterates as a set — `pythonCandidates` is the worked example and
carries that reasoning at the site. Census in `scratchpad/fsless_census.md`,
filed in BACKLOG.

## The r5 review of `internal/artifactcheck` — a guard copied from the wrong CPython

r5 returned one MEDIUM and three LOWs, and the MEDIUM is the third finding
in this port to come out of the same confusion: **`_pydatetime.py` is not
the specification.** CPython ships a C accelerator for `datetime`, and the
pure-Python module is a *different parser wearing the same name*. This
function's own header paragraph says so, in capitals. The port then
transcribed a line out of `_pydatetime.py` anyway:

```go
if len(tzStr) == 0 || len(tzStr) == 1 || len(tzStr) == 3 {
    return ..., false
}
```

with a comment asserting all three arms unobservable and citing a
414,589-input sweep. Two arms are inert. The third is not:

```
"2026-08-22T12:34:56+01\x00"    CPython  1787398496.0     port  refused
```

A three-character tz body *can* return true, through the
one-stray-NUL tolerance in `hhMMSSff` — a rule **r3 added**, two hundred
lines above, three rounds after this comment was written. The comment was
true when it was written and nobody revisited it when the rule that
falsified it arrived. The sweep it cited could not have caught this: it was
Go-to-Go, and NUL was not in its alphabet.

The whole guard is deleted rather than trimmed to the two inert arms.
Keeping the inert ones is precisely what made the block look transcribed
from the thing that runs. Eight new fixtures ride the boundary
(`+01`, `-23`, `+`, `+0`, `+012`, `+00`, `-24`, and the basic-form
`20260822T123456+01`).

The three LOWs are all comments, and they are worth naming because a
comment is the only artifact in this port that no test can check:

- `hhMMSSff` ended a note with *"Kept because it is CPython's own test."*
  The check is **not in the port**. Everywhere else in this file "kept"
  means present. The elision is genuinely inert — measured, not argued:
  adding the check back changes no answer over 156,494 inputs — but the
  sentence claimed the opposite of the code. (L52: a comment wrong about
  the property it defends.)
- `CheckFabrication`'s doc said the layer ORDER "is observable and is the
  subject of the D fixtures." It is not observable: layer 1 needs every
  claim missing **and** an empty diff, layer 2 needs a candidate that
  `is_file()`, and a candidate comes either from a claim (which then
  exists) or from `changed` (which is then non-empty). The preconditions
  are complements. Proved structurally, then measured — hoisting layer 2
  above layer 1 leaves the suite green, D1–D3 included, and 4,000
  randomised trees produce no input that reaches both. The order stays,
  because it is the order the Python is written in and a transcription
  that reorders on the grounds that nothing can tell has stopped being
  one. What is gone is the claim that a test defends it. D1 is renamed to
  say what it actually pins.
- Both `pyJoin` sweep comments stated a site count, and r5 measured both
  wrong: r1 left **four** sites, r2 left **six**, and r3's was the
  **seventh**, not the fifth. The numbers are deleted rather than
  corrected — this is L28 (an enumeration can be wrong at birth) landing
  on a comment whose entire subject is *"derive the class from the FILE,
  not from the diff"*, with its own number derived from the diff. The
  class is closed, and `grep -c filepath.Join` answers that in a second
  without a comment having to remember.

## The filename-sort class: five shipped sites, and a pin that was inert

The `sheriff.go` finding above was one site of a class, so it was fixed as
a class. Nine of the tree's 28 non-test `sort.Strings` calls sort
filenames; reading each one's Python counterpart left **five** that
reproduce a `sorted()` over paths and therefore must go through
`pypath.FSLess`:

| site | Python counterpart |
|---|---|
| `internal/orch/projects.go:62` | `orch_items.py:428` `sorted(p.iterdir())` |
| `internal/dispatch/envelope.go:558` | `dispatch_envelope.py:284,371` |
| `internal/pack/adopt.go:70` | `pack.py:366,369` `sorted(d.glob("*.md"))` |
| `internal/pack/export.go:368` | `pack.py:1195` |
| `internal/sheriff/sheriff.go:344` | `sheriff.py:164` — the W24 shape |

`orch/projects.go` carried a comment asserting *"Go's byte-wise compare
over UTF-8 gives the same order"*, which is provably false for any name
that is not valid UTF-8; it is replaced with the rule.

The other four filename sites stay on `sort.Strings` and are now
**listed with a reason**, because the second question is the one that
decides a site: is this reproducing a Python `sorted()`, or is it the
PORT's own determinism guarantee over something CPython iterates as a set
or does not sort at all? `artifactcheck.pythonCandidates` is the worked
example — converting it would pin an order CPython does not have.

`pypath/fssort_guard_test.go` is the class guard: it scans every non-test
`.go` in the module for `sort.Strings(` against a per-file allowlist that
carries the reason for each of the 23 remaining calls, and fails on any
new or unlisted one. It has a floor (`scanned < 100 || total < 15`)
because L1 — a scan that found nothing to scan passes vacuously — is a
failure this project keeps re-finding. Verified to fire.

### The pin that could not fail, twice in one day

The differential fixture for the sheriff site (`A50`) builds fifty
`é_NN` artifacts at now−100.5d and ten `\x80_NN` artifacts, one of them
at now−1.5d. Byte order takes the ten escapes first and reaches the recent
file — "active yesterday". Code-point order takes the fifty `é` files,
truncates at fifty, and never sees it — "untouched for a hundred days".

The first cut of that fixture **passed against a byte-sorting port**. Both
sides agreed on age 0, because `project_activity_age_days` also stats the
project directory *and `artifacts/` itself*, and creating sixty files
stamps `artifacts/` with the wall clock. The newest mtime found was a
DIRECTORY, and the fifty names never mattered. The probe pinned the
project dir and not the one directory the test was writing into.

This is the second time this session that a new pin had to be run against
a deliberately reverted call site before it was believed, and the second
time that check found the pin inert. The rule earns its keep every time:
*a guard that cannot fail is worse than no guard*, because it converts an
untested claim into a tested-looking one. The mutation is also a reminder
of P14 — reverting the `FSLess` call alone does not compile, since it
strands the import; a mutation is a LIST of edits.

## `sheriff.py` slice 2 — four functions, and a boundary that is not arbitrary

Slice 2 ports `check_all_projects` (:334), `archive_dormant_projects`
(:357), `write_heartbeat_state` (:534) and `read_heartbeat_state` (:556)
into `internal/sheriff/slice2.go`.

`check_system_health` (:439) is deliberately left out, and the reason is
worth stating because "we did four of five" is otherwise indistinguishable
from running out of steam. Three of its five checks are a stat, a division
and a socket connect, and they port directly. Check 2 is
`__import__("requests")` — a question about the PYTHON environment, which
only a Python subprocess can answer honestly, since the telegram and
notify paths it guards ARE Python. Check 4 needs `llm.detect_backends()`,
which this tree does not have; porting it drags in the credentials-env
reader, `_get_key`, the backend-order config walk and two binary probes,
which is an `internal/llm` tranche.

Emitting four checks of five is the one option that is not available.
`RollupStatus` is `startswith("warn")` over the map, so a MISSING check
turns a degraded box healthy — the fail-open direction, in the function
whose whole job is to say when the box is unhealthy.

### What the four functions actually carry

- **`sorted(slugs)` is a filename sort, and so is
  `sorted(projects_dir.iterdir())`.** Two more members of the class the
  `fssort` guard now covers; both are on `pypath.FSLess`. In
  `check_all_projects` the sorted list IS the return value.
- **`age is None or age <= days`** — the threshold is `<=`, so a project
  exactly at it is not archived.
- **`round(age, 1)`** is half-to-EVEN on the binary double, so 30.25 days
  rounds to 30.2 and 30.35 to 30.3. `pyval.Round` is that.
- **`archive_root.mkdir(exist_ok=True)`** takes Python's default `0o777`,
  which lands as `0o775` under this box's umask. A hard-coded `0o755`
  would create a group-read-only directory where Python creates a
  group-writable one — the 33-site census class, and this fixture is the
  first thing in the suite that can SEE it (the probe returns the archive
  root's mode on both sides).
- **`archive_dormant_projects` has no `try`/`except`.** Every other
  function in the module swallows; this one lets an OSError out, so the Go
  returns an error. Wrapping it in the module's house style would turn a
  failed move into a silent success.
- **`shutil.move` is not `os.Rename`.** Its is-dir-destination branch is
  REACHABLE — the caller's `target.exists()` check and the move are not
  atomic, so a concurrent mkdir lands the project one level deeper with no
  error. Reproduced. Its rename fallback catches `OSError` broadly (not
  just EXDEV) and copytrees; NAMED GAP, because `projects/` and
  `projects/_archive/` share a device in every install and an untested
  copytree is worse than a named gap.
- **The heartbeat file's BYTES.** `json.dumps(payload, indent=2)` is three
  decisions in one call, and the differential compares the file rather
  than the value.

### One named divergence, and it is the annotation that lies

`read_heartbeat_state` returns `Optional[Dict[str, Any]]` and returns
whatever `json.loads` produced. A `heartbeat-state.json` holding `[]`
comes back as a LIST; the caller does `state.get(...)` on it one line
later and raises. Go cannot return a list through an object-shaped
signature, so the port answers absent — a miss where Python crashes. It is
pinned by a Go-side test rather than a differential row, because a row
asserts the two sides are equal and here they are not.

### The battery, and the four pins that could not fail

19 mutations derived from the file, 19 caught, one documented equivalent
(a nil `pyval.List` renders `[]` exactly as an empty one does, because the
renderer switches on the type).

The first run caught only 14, and **four of the five survivors were
fixtures that could not fail** — each one passing for a reason unrelated
to what its name claimed:

| survivor | why the fixture was inert |
|---|---|
| a missing projects dir returns the star report | the fixture built an EMPTY dir, which takes the other branch |
| the dot/underscore skip is dropped | the only skipped dir present was `_archive`, created by the sweep, so always too young to archive anyway |
| the collision suffix rounds instead of truncating | the fixture's clock was a whole number, where truncating and rounding agree |
| an absent age is treated as zero | invisible at any positive threshold — it takes a NEGATIVE `days` to separate them |

That is four in one battery, on top of the two earlier in the day (the
`A50` directory-mtime case and the `G4a` missing-`src` case). The pattern
is sharp enough to name: **a pin written from the code reads as covering
the branch, and a pin written from the FIXTURE'S OWN INPUTS is what
actually covers it.** Every one of these four was a case where the input
did not reach the branch the name described.

### A comment wrong in five files at once

Seven comments across `sheriff`, `artifactcheck`, `pypath` and
`acprobe_test` say byte-value lists are needed "because json.dumps cannot
encode a lone surrogate". Measured, that is false:

```
json.dumps("\udc80b")                     -> '"\\udc80b"'  (ensure_ascii=True, the default)
json.loads('"\\udc80b"')                  -> '\udc80b'     (round-trips)
json.dumps("\udc80b", ensure_ascii=False) -> '"\udc80b"'   (a str; still no error)
sys.stdout.write(that str)                -> UnicodeEncodeError
```

*(That third line said `UnicodeEncodeError` when this section was first
written, which is the same mistake one level down: `json.dumps` never
raises here at all. The error belongs to **encoding the str**, which is a
later and separate step. Corrected during the r6 pass — a correction to a
paragraph whose whole subject is a wrong justification.)*

The transport is required by the **receiver**: Go's `encoding/json`
decodes `\udc80` to U+FFFD without an error, so a name that survived the
Python side arrives on the Go side as a different name. The conclusion was
right and the reason was wrong, which is the shape that survives review —
nobody re-checks a justification for a decision they agree with. The three
`internal/artifactcheck` copies are left until its r6 review lands, so the
report stays anchored to the file it reviewed.

## The r6 review of `internal/artifactcheck` — a class guard that knew one spelling

Seven findings, one HIGH. The HIGH is the r3 shape for the third time: a
fix that stopped one function short of its class. The MEDIUM is the same
shape one level up — the *guard* built to prevent a class had been derived
from the single spelling the last fix happened to touch.

### The HIGH: a NUL in a claimed path is a fail-open, not a skip

`_python_candidates._add` guards `p.resolve()` with `except OSError` and
nothing else. An embedded NUL raises **`ValueError`**:

```
Path("a\x00b.py").resolve()
  ValueError: lstat: embedded null character in path
```

so a claim carrying a NUL does not get skipped — it unwinds out of
`_python_candidates` entirely and lands in `check_fabrication`'s blanket
`except`, which answers `judged=false, "check error (fail-open)"`.

The port swallowed the same condition **twice** — `EvalSymlinks` fails and
falls back, then `os.Stat` fails and returns — and carried on to a
confident verdict. Measured end to end on `wrote to a\x00b.py and it
prints 1, 2, 3` over a tree with two inert `.py` files:

```
CPython  judged=false, "check error (fail-open)"
port     judged=true,  fabricated=TRUE, kind=inert-output
```

That second line is a fabrication accusation CPython never makes, on
adversary-shaped input, reachable through the inner agent's own narration:
NUL is not in Python's `\s`, so `_OUTPUT_CLAIM_RE`'s `[^\s...]+` group takes
it, `_clean_token`'s `strip()` leaves it, and `_EXT_RE` still matches —
`ExtractWriteClaims` returns `["a\x00b.py"]` on **both** sides.

Why it read as closed: `A9` already pins the NUL one function over, at
`existsAnywhere`. `Path.exists()` **catches** `ValueError`; `Path.resolve()`
does not. Fixtures `D4`–`D7`, with exactly one `.py` file in the tree —
two made `D7` flaky, because CPython draws the fresh-candidate order from a
`set`.

### The MEDIUM: the guard knew `sort.Strings(`, not the class

The previous round's class guard scanned non-test sources for the literal
string `sort.Strings(` against a per-file allowlist. The **class** is
byte-wise ordering of filenames, and it has at least four spellings here.
Two shipped sites were invisible to it, and both were real:

| site | Python | shape |
|---|---|---|
| `skills/provenance_load.go:105` | `sorted(prov_dir.glob(f"{name}_*.json"), reverse=True)` | order only |
| `record/dailylog.go:156` | `sorted(mem_dir.glob("????-??-??.md"), reverse=True)[:7]` | **W24 — order *and* truncation** |

Both spelled `sort.Sort(sort.Reverse(sort.StringSlice(...)))`.
`dailylog.go`'s own comment asserted that `filepath.Glob` "sorts the same
way", which is true for every valid-UTF-8 name and false for the ones that
matter. And because it truncates to seven, the two runtimes do not list the
same week in a different order — they render **different days**.

The guard is now written from the class: parse each file with `go/ast`,
find every function that *both* reads a directory (`ReadDir`,
`Readdirnames`, `Glob`, `Walk`, `WalkDir`, …) *and* calls a sort whose
subtree does not mention `FSLess`, and require a listed reason for each.
Six functions are allowlisted; each entry must still be **observed** by the
scan, so a detector that quietly stops matching fails instead of passing.
Run before any fix landed, it named exactly the two sites above and nothing
else.

Its own named gap, since a guard's honesty is the point: the scan is
per-function, so a listing done in a helper and sorted by its caller is
invisible to it. That is why the `sort.Strings` spelling guard is **kept**
alongside it rather than replaced — the two cover each other's blind spots.

### The find inside the fix: CPython writes no index at all

Fixing `dailylog`'s sort exposed a second divergence that the wrong sort
had been hiding. Once a non-UTF-8 filename is in the kept seven, it is
rendered into `MEMORY.md`, and `atomic_write` defaults to
`errors="strict"`. The `UnicodeEncodeError` lands in
`_update_memory_index`'s bare `except: pass`, so **CPython writes
nothing** and the index stays as it was. Measured — eight daily logs, one
named `2026-08-0\x80.md`, index file never created.

A Go string holds those bytes happily, so the port had been rewriting an
index CPython refuses. It now declines the write too, and *returns* rather
than passing, matching the divergence this file already documents.

The two behaviours compose, so they get one case each rather than one case
that fails for either reason and identifies neither: `order` (eight files,
the two sorts disagree about which is dropped), `gate` (three files, no
truncation, so only encodability can fail it), and `control` (eight valid
files, both runtimes must write the same bytes). Mutating the sort back
fails `order` alone; removing the gate fails `order` and `gate`; neither
touches `control`.

### The self-inflicted one, which the battery caught

The newline fixture below needed the scripted-inert harness to compare
against text-mode content, and the first cut had it call the production
`pyReadTextNewlines`. That makes the oracle move whenever the subject
does: **two of three newline mutants survived**. The harness now spells
universal-newline translation itself, character by character, and all four
mutants are caught. A test that reuses its subject as its own oracle
cannot fail (P12) — and it is much easier to write that way than to notice.

### Four LOWs, all of them claims that had decayed

- **`read_text` is not just decode.** It opens in TEXT mode with
  `newline=None`, so `\r\n` and lone `\r` both reach `_python_is_inert` as
  `\n`. `os.ReadFile` translates nothing. Whether any particular parser
  cares is not the argument: `InertFunc` is a **seam**, and Python's
  contract with its callee is that the source contains no `\r` at all.
- **A doc paragraph on the wrong function.** `isoWeekToGregorian`'s
  week-53 rule sat attached to `pyMktime`, a hundred lines above the
  function it describes.
- **A number with no denominator.** "It refuses 48 inputs CPython accepts"
  could not be re-derived. Measured against `isoCorpus()`: exactly **4 of
  97,116**, every one an offset body of `hh\x00`, and the comment now names
  all four. Four out of 97,116 is a smaller number and a far more useful
  one.
- **An allocation count read as a complexity class.** Zero allocations
  refutes `[]rune(s[i:])[0]`, which is the defect that shipped. It is
  *not* the same statement as "this is O(1)" — a zero-allocation O(n)
  spelling passes every line of that test.

And one duplicate: `pythonCandidates` spelled `p.resolve()` as
`EvalSymlinks`-then-`Abs` with a fallback to the raw string, where
`pypath.Realpath` already transcribes `realpath(strict=False)` exactly. It
is inert *today* only because the `os.Stat` below drops a missing candidate
before the two dedup keys can differ — safety by call order, not by
construction, and no fixture can distinguish them. Recorded as such rather
than pinned, because a pin that cannot fail is the thing this round spent
its MEDIUM on.

## The r7 review of `internal/artifactcheck` — a fixture that defended the configuration that does not ship

One MEDIUM, four LOW, and a large "what I could not break" section that
re-derived r6's panic boundary against CPython 3.14.3, confirmed the
inertness rule over a 14-case differential, and ran 900 random trees end
to end through the production nil seam. Then a sixth finding, found not by
fixing the site the review named but by censusing the class it belonged to
— and that one landed on the guard I had written the round before.

### The MEDIUM: D4–D6 pinned a seam no caller uses

r6's HIGH was the NUL fail-open. Its fixtures, D4–D6, drive
`CheckFabrication` with a *scripted* `InertFunc`. There is no `InertFunc`
implementation anywhere in this tree; every production caller passes
`nil`. So the fixtures defended the configuration that does not ship, and
the one that does was defended by nothing.

The mutation is a plausible tidy-up. In `inertOutputVerdict`, hoist

```go
if isInert == nil { return Verdict{}, false }
```

above the `pythonCandidates` call — why build candidates for a seam that
cannot judge them? The whole suite stays green, because with `nil` there is
no longer any call that can raise the NUL. Layer 2 then declines quietly
and `check_fabrication` returns its "nothing found" verdict with
**`judged=TRUE`**, where CPython's `ValueError` unwinds into the blanket
`except` and answers `judged=FALSE`. `judged` is the field that tells a
reader *the check found nothing* from *the check broke*; inverting it on
adversary-shaped narration is the entire point of the r6 fix.

Closed with `C9`–`C11`: the same three sentences through the nil seam, as
CPython differentials. The mutant fails six assertions.

**D7's control could not be a differential, and that is the finding's
second half.** With the seam `nil` the port declines layer 2 where CPython
consults a real AST, so the NUL-free control sentence is a row the two
runtimes are *supposed* to disagree on. Written as a differential it fails
honestly and for the wrong reason. It lives instead in
`TestTheNilSeamStillFailsOpenOnANulAndOnlyOnANul`, asserting the port's own
contract: with a NUL the check must break, without one it must not. Without
that pair `C9`–`C11` would pass against a port that fails open on
everything — the exact hole D7 exists to close one layer down.

### The LOW that made the MEDIUM possible

`CheckFabrication`'s doc paragraph said the honest port of a blanket `try`

> is not a `recover()` wrapper pretending to be one — it is to make every
> operation inside it total, which the helpers above are … The one thing
> that could still panic is a caller's `InertFunc`.

r6's own fix had made that false **in the same round it was written**, by
putting an unconditional `panic("pypath: embedded null character in path")`
inside `pythonCandidates` precisely so the ValueError would reach that
`recover`. Two things panic in there now, one of them ours. The sentence
was the licence a later refactor would have cited for the hoist above, and
it is another instance of L28: a comment asserting a property is a claim,
and this one decayed within a day of being written.

### Two local copies of library functions

- `pyBasename` duplicated `pypath.Name` and was **wrong on six of nineteen
  pathlib shapes**: `a/.`, `a/./`, `a/.//`, `x.py/.` and `a/b/.` all came
  back `"."` where CPython gives the parent's name, and `".."` came back
  `""` where CPython keeps `".."`. Its own comment argued
  equivalence-under-reachable-input and was scrupulously honest that this
  was a property of *today's callers* rather than of the function. That is
  the argument against keeping a second implementation, not for it.
  Deleted; the five divergent shapes went into `pypath`'s CPython
  differential, where they are now pinned against the interpreter rather
  than against a table.
- `pyReadTextNewlines` duplicated `pytext.TranslateNewlines` — same rule,
  written one round earlier, with a no-allocate fast path this copy lacked.
  Deleted.

The test-side twin stays. `acUniversalNewlines` is written out
character-by-character in the test file *on purpose*: a test that reuses
its subject as its own oracle cannot fail, which is how two of three
newline mutants survived r6. Deduplicating production and deduplicating an
oracle are opposite moves.

### `filepath.Glob` is a sort, and it is invisible

The last LOW was `record.ArchivePaths`, which takes `filepath.Glob`'s order
for `captains_log._archive_paths`' `sorted()` and says so:

> `filepath.Glob` already returns sorted names, and Python's `sorted()`
> over `Path` objects sorts on the same bytes.

The second half is the exact false equivalence this whole class exists to
deny, stated as settled fact one file over from where r6 spent its MEDIUM.
Measured with `captains_log.{z,\xc3\xa9,\x80,\xff}.jsonl` in one directory:

```
CPython   z, é, \x80, \xff        (surrogateescape code points)
Glob      z, \x80, é, \xff        (raw bytes)
```

The list is "oldest first" and feeds the archaeology readers, so two
engines would replay one corpus in two orders.

What makes it worth its own heading is *why the r6 class guard missed it*.
That guard looks for a function that lists a directory **and then sorts**.
`ArchivePaths` contains no sort. `filepath.Glob` **is** the sort — it
byte-orders its matches through a `sort.Strings` inside the standard
library, a call that is not in this tree and that no scan of this tree can
see. The spelling guard has no `sort.Strings` to count; the class guard has
no sort to find. A third arm now treats `filepath.Glob` as a listing call
whose order must be replaced, with a `globOrderAllowlist` for the sites
where the Python globs unsorted.

### And the sixth finding, which the review did not report

Run before any fix landed, the widened guard named **three** sites, not the
one r7 reported. The extra one is `tasks.List`:

```go
paths, _ := filepath.Glob(filepath.Join(dir, "*.json"))
sort.Strings(paths)     // task_store.py:314 `sorted(_tasks_dir().glob("*.json"))`
```

Two raw-byte orders stacked, under a comment calling the explicit sort
"redundant". It was neither redundant nor right. Worse, **both** allowlists
carried a false reason for it — `fsSortAllowlist` and `dirSortAllowlist`
each said "a glob the Python does not sort at all" — and r7 states in its
own words that it re-derived all six `dirSortAllowlist` reasons and found
them clean. A recorded rationale is a claim (L52) even when the reviewer
checking it is thorough, and even when the person who recorded it is the
guard's own author.

The first cut of `globOrderAllowlist` then made the same mistake in
miniature: written from memory, it named `tasks.go:Sweep`, which does not
glob (`resolveDependents` does), and `pack/export.go:exportSkillMarkdown`,
whose glob *is* re-sorted through `FSLess` inside `Export`. Both were
caught by the allowlist's own must-still-be-observed rule within one test
run. That rule is not tidiness — an allowlist entry the scan cannot find is
an assertion about a site that may not be there.

**Standing rule this round adds:** when a guard is widened, run it *before*
fixing anything and read the whole list. The findings a review reports are
a sample; the guard's own census is the population.

## The r8 review — two seats, one class, and `os.ReadDir` is a sort

r8 ran **two seats on the same chunk**: an opus seat (same family, high
effort) and a `gpt-5.6-terra` seat on codex (cross family), neither
prompted with the other's findings. They converged on one class and each
found sites the other missed — and they disagreed on exactly one finding,
which the Python source settled.

The class is the same one r7 closed a day earlier, one spelling over:
**`os.ReadDir` is a sort.** It byte-orders its entries inside the standard
library, exactly the way `filepath.Glob` does, so a function that takes its
order and never sorts at all is invisible to every arm of both guards. It
is also the *most common* way this tree lists a directory, which makes the
blind spot wide rather than narrow.

That brings the class to **five spellings**:

1. `sort.Strings` over a listing — the r5 spelling, the one the first
   guard knew.
2. A sort inside a directory-listing function — the r6 spelling.
3. `filepath.Glob`'s own order — r7. Invisible: the sort is in the stdlib.
4. **`os.ReadDir`'s own order** — r8. Invisible for the same reason.
5. **A bare `<`/`>` string comparator inside a `sort.Slice` body** — r8.
   Invisible for a different reason: no arm of either guard reads
   comparator *bodies*.

### The four production sites

Every one was verified against the Python before it was touched, and every
one now has a CPython differential that was proved able to fail by
reverting its own fix.

| Site | Python | Shape |
|---|---|---|
| `closure/inventory.go:projectFileInventory` | `closure_verify.py:697,700` — **two** `sorted()` calls | **W24** at the cap |
| `orch/mission.go:ListMissions` | `mission.py:1116` `sorted(iterdir())` | W23 |
| `runs/index.go:scanLegacyRunDirs` | `runs.py:199` `sorted(iterdir())` | W24 at the caller |
| `orch/projects.go:ListBlockedProjects` | `orch_items.py:779` `sorted(..., key=(priority, blocked, slug), reverse=True)` | W23, reversed |

Two of the four are worse than an ordering difference.

`projectFileInventory` **truncates** at its cap, so the two engines do not
merely order the same files differently — they *name different files*.
Measured over a tree holding `a\x80z.txt`, `aéz.txt`, `d\x80/k.txt` and
`dé/k.txt`:

```
py  cap=1 : ['aéz.txt',   '... (truncated at 1 files)']
go  cap=1 : "a\x80z.txt\n... (truncated at 1 files)"
```

That listing is ground truth for the closure plan — it exists so checks
probe files that exist instead of names the model guessed — so a truncated
inventory naming a different file is a false-negative closure verdict that
appears on one runtime only.

`scanLegacyRunDirs`'s **first hit wins**. On a workspace where two run
directories claim the same `loop_id` — which is what a duplicate-reference
workspace *is*, and the only reason the legacy scan still exists — the sort
order decides which run the migration resolves to. A W23 difference in the
scan is a W24 different-answer difference at `legacyRunDir`.

`ListBlockedProjects` is the fifth spelling, and the one worth staring at:
`ListProjects` hands it a correctly `FSLess`-ordered slug list, and the
sort threw that order away one call later with `out[i].Slug > out[j].Slug`.
An AST arm for comparator bodies was deliberately **not** built — a census
found 32 bare `<`/`>` comparators tree-wide, and all but these were
numeric. Thirty allowlist rows about float comparisons is a guard nobody
reads.

### The one disagreement, and who settled it

The codex seat reported `recall.go:FindPriorAttempts` as a fourth
divergence: the port stable-sorts equal-mtime directories after `ReadDir`
has byte-sorted them, where CPython's stable sort preserves readdir order.
The opus seat re-derived the same site's allowlist row and **cleared it**.

The Python is the tiebreaker, and it says unsorted. `recall.py:407-410`
sorts a generator over an **unsorted** `root.iterdir()`, so CPython's
tie-break is raw readdir order — filesystem-dependent, not a Python order
at all. The port's byte order is a determinism guarantee over something
CPython leaves undefined, which is the allowlist's category-1 case. Filed
as an allowlist row with both seats' reasoning in it, not fixed.

Two seats disagreeing is not noise; it is the cheapest possible signal that
a site needs the source read rather than the diff.

### The census, again, and what it caught this time

The `readDirOrderAllowlist` arm was run **before any fix landed**. Between
them the two seats named five candidate sites; the census named exactly the
three that were real and nothing else. That is what makes the arm
non-inert — a widened guard that merely goes green afterwards has been
verified against the review's sample, not against the tree.

Then the fixes ran and the guard's own **must-still-be-observed** rule
fired: the `dirSortAllowlist` row for `closure/inventory.go` was now stale,
because the site it certified had just been fixed out from under it. That
rule was written to catch an allowlist entry naming a site that does not
exist. It caught a second class it was not written for.

This is now a named process pattern (**P15**), on its second instance in
two days.

### The LOWs — four claims that had decayed, and one rule that was false

- The allowlist header's **second decision rule was false**: *"a key that
  arrives inside JSON cannot be non-UTF-8, because `json.dumps` could not
  have written it."* With the default `ensure_ascii=True`,
  `json.dumps({'\udc80k': 1})` produces `'{"\\udc80k": 1}'` — pure ASCII,
  writes to a strict-UTF-8 file, and `json.loads` returns the lone
  surrogate. Seven of fourteen `fsSortAllowlist` rows were admitted by that
  rule. They are still safe, but for a *different* reason: Go's
  `encoding/json` substitutes U+FFFD for the escape, a named measured
  residual at `pyval.go:487-521`. A decision procedure that is right for
  the wrong reason will be wrong at the next site whose Go side does not
  happen to launder the surrogate.
- The NUL corpus comment said **"0-3 NULs through every position of
  seventeen bases"**. `nulBases` holds **25** entries and the loop runs
  **1-3** — n=0 would be the bare base, which the loop never emits. L28's
  other half: an enumeration can be wrong *at birth*, so count every number
  a comment states.
- `pyMtime`'s margin was called **"two thousand times smaller than
  MtimeEps"**. `1e-4 / 2.38e-7` is **420**. The sentence's conclusion
  survives; the headroom a later reader would infer from it does not.
- Two comments still named **`pyReadTextNewlines`**, deleted in r7 when the
  translation moved to `pytext.TranslateNewlines`. One of them carries the
  load-bearing reason `acUniversalNewlines` must stay a second
  implementation — an argument still right, hanging on a name that cannot
  be found, one cleanup away from removal.
- `acScriptedInert`'s doc block ran unseparated into the next comment, so
  godoc attached it to `acUniversalNewlines` and the `sort.Strings`
  determinism argument was stranded on a helper about newline translation.

### The pin that could not fail — the leap-Wednesday disjunct

`isoWeekToGregorian`'s week-53 rule is
`!(first == 4 || (first == 3 && isLeap(y)))`. Deleting the **second
disjunct entirely** left the whole package green across the 97,116-input
ISO corpus:

```
baseline                     ok
!(first == 4)                ok     <- SURVIVES
!(first == 4 || first == 3)  FAIL
```

`dates` held `2025-W53-1` (Wednesday, not leap → correctly rejected) and
`2026-W53-1` (Thursday → accepted), but **no leap year starting on a
Wednesday**. `isLeap` was pinned as a *rejecter* and not as an *accepter*.
Adding `2020-W53-1`, `2020-W53-7` and `2020-W54-1` makes the mutant fail
with `CPython gives 1.6091388e+09, the port refused`.

Same shape as r7's D4–D6: a corpus of 97k inputs that could not observe
one branch. Size is not coverage.

### The fixture bug worth recording

The first cut of the legacy-scan differential interpolated the directory
name into `metadata.json`'s `handle_id`, which put a lone `0x80` **inside**
the JSON file. CPython's `read_text(encoding="utf-8")` raised
`UnicodeDecodeError`, the scan's own `except Exception: continue` swallowed
it, and the differential silently measured a **one-entry** scan. It was
caught only because the fixture states its expected order independently of
both implementations — had it compared CPython against Go alone, both
sides would have agreed on the wrong tree.

Only the directory name is allowed to carry the bad byte. The metadata is
ASCII and identical in both directories.

### Round state

Both seats ended `LOWS ONLY: no`, so r9 is owed. 45 packages green,
`gofmt`/`go vet` clean, ledger +18 rows (including both retracted claims
and the cross-seat disagreement), two new catalog entries: **P15** (a
widened guard's own census is the population; a review's findings are a
sample) and **L55** (a fixture can defend the configuration that does not
ship) -- the latter named now but measured in r7, filed against r7 in the
ledger so the count stays honest about when the defect existed.


## `internal/recall` gets an interpreter — built in a parallel lane

`internal/recall` was one of the three genuine gaps `go/COVERAGE.md` names:
542 lines with **no interpreter comparison at all**. A codex build lane,
working in its own git worktree (`go-port-lane2`, P4 — a battery or a build
owns the whole tree it runs in, because a Go module builds through its
import graph), closed it with three live CPython differentials:

- `TestFindPriorAttemptsMatchesCPython` — exact / near / project match
  lanes, exclusion, the 24-hour window, the tri-state `goal_achieved`
  verdict, the status-derived interrupt verdict, and newest-first ordering.
- `TestRecallLessonsMatchCPython` — agenda-first ranked retrieval, untyped
  top-up, lesson-ID dedup, done/stuck icons, receipt spelling, rendered
  citation IDs. The probe forces Python's optional hybrid and its
  unported/inherently-writing substrates inert, leaving the ported
  read-only slice.
- `TestContextBlockMatchesCPython` — the prior-attempt paragraph: sorted
  status breakdown, SF-2 verdict counts, both interrupt channels, and the
  newest-attempt wording.

All three state their expected rows **before** either answer is compared,
so CPython is checked rather than silently made the oracle.

**One divergence, reported and not fixed.** A metadata `goal_achieved` set
to the *string* `"false"` makes CPython return `None` (unjudged) where the
port returns `false` (judged not achieved). Fail-closed in Go, and the
production code documents the conservative choice. The lane's brief says
report divergences, do not fix them — the fix decision is the host's — and
it held to that.

**What the lane did not deliver.** Three mutants for three tests is thinner
than the brief asked for, and mutations derived per-test rather than from
the file are the shape L9 warns about. A deeper pass derived from
`recall.go` itself is owed before this package can be called pinned.

**Sandbox artifact, checked.** The lane reported `go test ./...` blocked by
an `internal/artifactcheck` fixture that creates a Unix socket and hit
`PermissionError` in its sandbox. Re-run outside the sandbox: exit 0, all
packages green. The fixture correctly failed rather than skipping, which is
the pyprobe rule working as designed.

## The r9 review — `os.walk` has an opinion about symlinks, and a fixture that defended the wrong environment

Two codex seats ran in parallel against the same tree (read-only, separate
`GOCACHE`s): round 9 on `internal/artifactcheck` and round 6 on
`internal/syshealth`. Both reviewed the WHOLE chunk plus its fixes (P2),
not the latest diff.

**The MEDIUM: `entry.IsDir()` is not `scandir`'s `is_dir()`.**

`closure_verify._project_file_inventory` is an `os.walk(root,
followlinks=False)`. The port used `os.ReadDir` and asked each entry
`it.IsDir()`. Those two questions differ on exactly one input — a symlink
that points at a directory:

| | a dir symlink | a DANGLING symlink |
|---|---|---|
| CPython `scandir` `is_dir()` | **True** — follows the link | False — `stat` fails |
| Go `DirEntry.IsDir()` | **False** — the entry's own type bits | False |

CPython therefore puts a symlinked directory in `dirnames`, and because
`followlinks=False` it is never descended into either. The link is named
*nowhere*: not as a file, not as a directory it walked. The port emitted it
as a file. At the inventory cap that is not cosmetic — the invented entry
consumes a slot and can displace a real path the closure plan should have
verified. Fail-open.

Verified against CPython before touching anything (P1), with a root holding
`linkdir -> real/`, `real/inner.txt`, and `z.txt`:

```
cap=10  ->  'z.txt\nreal/inner.txt'
cap=1   ->  'z.txt\n... (truncated at 1 files)'
```

Neither the link nor a path under it. The fix is an `isDir(dir, entry)`
helper that stats *through* a symlink, applied to the FILE arm only. The
descend arm deliberately keeps `!it.IsDir()`, so a symlinked directory is
still never followed — and the asymmetry is commented at both sites,
because a later reader looking at two adjacent loops asking the same
question two different ways will otherwise "fix" one of them.

The fixture was proved able to fail: reverting `isDir` to `it.IsDir()`
turns `"abdangling\nz.txt\nreal/inner.txt"` into
`"aalink\nabdangling\nz.txt\nreal/inner.txt"`.

**The LOW, and a rationale from four sections ago that turned out to be
wrong.** The P8b differential binds an `AF_UNIX` socket to build its
fixture. On a host that forbids that, the *whole* `artifactcheck` package
failed — before comparing either implementation. The section immediately
above this one records the earlier verdict on exactly this behaviour:
*"The fixture correctly failed rather than skipping, which is the pyprobe
rule working as designed."* That was half right. The pyprobe rule exists so
a missing INTERPRETER is never silently skipped; it says nothing about a
missing *kernel capability*, which is not a port fact at all. L52 — a
rationale recorded as deliberate is still a claim.

The gate is a probe bind, and the shape matters more than the gate:

- on `EPERM`, the socket fixtures are DROPPED, the errno is logged, and
  every other fixture still runs;
- on **any other** bind error the test still fatals, spelling out that this
  is a real failure and not an environment capability — a gate that
  swallowed every error would have replaced one silent hole with a wider
  one;
- the drop rule fatals if it matches no fixture, so it cannot rot into a
  no-op the day the fixture is renamed (L4).

**syshealth r6: the same factual error, in the other copy.** r5 corrected
the package doc's claim that the seven real `DECLARED_PROCESSES` probes
each read a different live store. They do not: four (`run_ref_index`,
`skill_attribution`, `lesson_receipts`, `closure_verdicts`) share
`_recent_outcomes` over `memory/outcomes.jsonl`, and two
(`contradiction_lifecycle`, `variant_ab`) share `captains_log.query_log`.
r6 found the sentence still standing in the *harness* comment. L13, again:
a fix at the site that has the fixture is not a fix for the class. With
that corrected, r6 returned **LOWS ONLY: yes** — `internal/syshealth` is at
fixpoint.

Both seats also retracted a candidate of their own after instrumenting it
(`pypath.Name` on unreachable `a/.` spellings; a non-finite `cycle` value
that reaches the same outer handler with the same clipped message in both
runtimes). Round hallucination: 2 of 5.

## The write path gets a harness, and it finds a dropped argument on its first run

`engine-compare.py` compares STDOUT of read-only renderers. It cannot see a
divergence that only appears while WRITING — which is where this port's
recorded bug families actually live: content-key prose divergence, key
order inside a written object, absent vs zero, a created directory's mode.
`go/tools/write-compare.py` is the other half. It runs both engines through
the same command sequence and byte-diffs the resulting trees, directories
and their MODES included.

Two workspaces, not one: both engines are mutating, so the read harness's
trick of handing both the same path is unavailable, and every value that
embeds the workspace root becomes volatile. Volatility is handled by
**positive-shape elision** — a job id must match `task-\d{8}T\d{6}Z-[0-9a-f]{8}`,
a `run_id` must match a **version-4** UUID (the `4` and the variant nibble
are part of the assertion), a `claimed_by_pid` is elided only under its own
key and only when it starts `[1-9]`. A pid of `0` and a `null` both survive
to the diff, because "None vs 0" is a divergence class this port keeps
hitting. Substitution counts are tracked per side and required to AGREE: a
side that emitted five timestamps where the other emitted four has a real
difference the normaliser would otherwise erase (L51).

The harness proves itself twice before it is believed (P10). `selftest()`
builds two trees differing in one byte, one mode, one missing file, one
retargeted symlink and one directory-became-symlink, and requires exactly 5
findings and 0 false positives (the two symlink shapes were added by r10,
below — the count was 3). `normalize_selftest()` checks each elision in
**both** directions over fourteen shapes — a v1 UUID, an all-zero UUID,
`"claimed_by_pid": 0`, `null`, and the same number under a different key
must all come through UNTOUCHED. The negative half is the half that
matters; a too-greedy elision still prints "identical".

**What it found on its first run.**

```
python  task fail <id> --error "boom"  ->  "error": "boom"
go      task fail <id> --error "boom"  ->  (no error key at all)
go      task fail --error "boom" <id>  ->  "error": "boom"
```

Go's `flag` stops parsing at the first non-flag argument; `argparse`
interleaves. The two runtimes write DIFFERENT rows to a store they share,
and nothing says so — the failure is invisible until something reads the
row back. This is L46 in its purest form: the stdlib substituted for the
ported library costs one divergence per rule nobody enumerated, and
"argparse interleaves" is a rule nobody enumerated.

`parseInterleaved` is the standard loop — parse, take one positional,
re-parse the rest — and it still ERRORS on an unknown flag wherever it
appears, because a version that swallowed the error to keep going would
turn every typo into a silently ignored argument, which is a worse bug than
the one being fixed.

**Then the census, not the site (P15).** The review's finding is a sample;
the guard's own population is every `fs.Arg(0)` after a bare `fs.Parse`:

| site | shape | disposition |
|---|---|---|
| `task claim/complete/fail/archive` | silent swallow | fixed |
| `pack adopt` | silent swallow — `-label x foo.md --all` adopted ONE file where `pack.py` adopts the whole label | fixed |
| `run` | refuses a post-positional flag loudly | left alone, deliberate |
| `director` | refuses a post-positional flag loudly | left alone, deliberate |

One surface difference is filed at the site rather than papered over:
`pack.py` takes the label as a POSITIONAL and the Go takes `-label`. That
is a spelling difference like `maro task` vs `python3 -m task_store`, not a
dropped argument.

**And then the harness's actual verdict.** With the fix in and the volatile
fields taught, all seven `task` scenarios — enqueue, claim, complete, fail,
archive, blocked-by, and a no-op recover — come out **byte-identical across
both engines**, directory modes included, with the elision counts agreeing
on both sides. That is the first end-to-end WRITE-path agreement this port
has measured, and it covers the whole state machine of the one ported
surface where a state machine writes.

## The r10 review — `is_dir()` is not `IsDir()`, and the round's medium lived inside r9's own fix

Two seats, neither shown the other's findings, over the whole chunk (P2):
opus same-family, `gpt-5.6-terra` cross-family. Five findings, **all five
confirmed by measurement**, and four of them are one class seen from two
directions.

### The class: one Python question, two Go spellings, and the answer differs both ways

r9 had just fixed `closure`'s inventory, where the port asked
`DirEntry.IsDir()` and CPython's `os.walk` — with its DEFAULT
`followlinks=False` — puts a symlink-to-directory in `dirnames`, so CPython
names the link NOWHERE and the port INVENTED a file. r10 found the same
spelling pointing the other way, at four listing sites that are not
`os.walk` at all:

| CPython call | a symlink to a directory |
|---|---|
| `root.iterdir()` + `Path.is_dir()` | **kept** — `Path.is_dir()` follows the link |
| `os.walk(followlinks=False)` | put in `dirnames`, so emitted nowhere |
| Go `DirEntry.IsDir()` | **False** — always; it reads the entry's own type bits |

`DirEntry.IsDir()` is `Path.is_symlink()`-shaped, not `is_dir()`-shaped, and
the two questions differ on exactly one input. So the identical
transcription is a **dropped** entry in one place and an **invented** one in
the other, and no single rule about the spelling is right — the caller has
to know which Python call it is reproducing.

| site | Python | measured effect |
|---|---|---|
| `orch/projects.go:ListProjects` | `orch_items.py:426` `p.is_dir()` | `projects/linked -> real` with a NEXT.md: listed by CPython, missing from the port |
| `orch/mission.go:ListMissions` | `mission.py:1117` | same, one listing over |
| `runs/index.go:scanLegacyRunDirs` | `runs.py:200` | **W24** — see below |
| `recall/recall.go` | `recall.py:407-410` | two failures behind one spelling — see below |

`pypath.EntryIsDir(dir, e)` is the one helper: `IsDir()` first, then — only
for a symlink — a following `os.Stat`, and **False on a stat error**, because
a dangling link is not a directory to CPython either and a helper that
propagated the error would have to invent a third answer where the Python
has two. `closure`'s local copy was deleted in favour of it (L15); its
descend arm still deliberately uses `!it.IsDir()`, because that arm is
reproducing `os.walk`, and the asymmetry is now commented as such at the
site.

### Why `runs` is a W24 and not a W23

`_scan_legacy_run_dirs` feeds `_legacy_run_dir`, which returns the **first**
directory claiming a `loop_id`. With `alink -> real` sorting ahead of
`real`, CPython resolves a duplicate reference to `alink` and the port
resolved it to `real`. The two runtimes name **different runs** for the same
input. The differential asserts the resolved basename, not just the scan
list — a fixture that checked membership alone would have passed a fix that
only restored membership.

### Why `recall` is two failures, and how the fixture separates them

```python
sorted((d for d in root.iterdir() if d.is_dir()),
       key=lambda d: d.stat().st_mtime, reverse=True)
```

Both calls follow. The port used the Lstat-shaped **pair** —
`DirEntry.IsDir()` and `DirEntry.Info()` — so a linked run was dropped, and
had only the filter been fixed the survivor would have been ordered by the
LINK's mtime rather than the run's. The fixture puts the target OUTSIDE
`runs/` with the oldest mtime while the link keeps its own newest one, so
the two orders are exact reverses and neither mutant can pass by luck. Both
were run: dropping the link gives one row, reading the link's mtime gives
the reverse order.

The target lives outside `runs/` on purpose. A link pointing at a sibling
INSIDE `runs/` makes one directory reachable twice with the same mtime, and
that tie is broken by raw readdir order — which r8 established has no
CPython order to reproduce. A fixture resting on it would be measuring the
platform (P11).

### P6, on the round's own previous fix

The medium in `cmd/maro` lived inside r1's fix. `parseInterleaved` restored
flag INTERLEAVING and never modelled positional ARITY, which is the other
half of what argparse enforces — and `flag.Parse` consumes `--` and stops,
so the loop's next iteration parsed the tail as flags again:

```
argv                          python                        go (before)
fail <id> -- --error boom     rc=2, task stays QUEUED       rc=0, task FAILED, error dropped
fail <id> job-2               rc=2, unrecognized arguments  rc=0, acts on job-1
```

A different store row, from the helper written last round to stop a
different store row. `parseArgs` now splits at the first `--` before parsing
anything and takes each subcommand's declared count from `task_store.py`
(`claim/complete/fail/archive` 1, `enqueue`/`list` 0, `pack adopt`
genuinely unlimited), returning argparse's own `unrecognized arguments:`
sentence. `refuseExtra` covers `status` and `recover`, which built no
`FlagSet` at all and so ignored trailing argv entirely — `maro task status
queued` looked like a filter and was silently a full listing.

Five argv shapes were re-run against both engines after the fix and all
five agree, the happy path (`fail <id> --error boom` → `failed`/`"boom"`)
included.

**And then the census, again — because r1's census had the wrong
population.** r1 enumerated every `fs.Arg(0)` after a bare `fs.Parse`. But
reading a positional is not the defect; the defect is a `FlagSet` that
neither reads positionals nor refuses them, because `flag.Parse` stops at
the first one and silently drops every flag after it. Four sites read
`fs.Args()` *nowhere* and were therefore invisible to that enumeration:

```
maro pack export -name p -label l stray -include-medium
  before: exit 0, pack exported WITHOUT medium lessons, no diagnostic
  after:  error: unrecognized arguments: stray
```

Measured against the pre-fix binary, which was still on disk. `pack seal`,
`pack import` and `metrics` had the same shape; all four now take a zero
arity. `inspect`/`evolve`/`graduate` already refuse positionals explicitly,
and `run`/`director` refuse post-positional flags by design — those five
are the population's correct members and are unchanged.

This is L28's other half. An enumeration can be wrong at BIRTH, and the way
to catch it is to state the population as a **property** — *does a stray
positional change what this command does?* — rather than as a code pattern,
because a code pattern only finds the sites that look like the one you
already found. Three differences are **filed, not fixed**, and the reasoning is
that none of them changes what is WRITTEN for an argv both engines accept:
long-option abbreviation (argparse permits, `flag` does not — fail closed),
single-dash long options (argparse rejects `-error`, and rejecting it would
break the port's own documented `-backend` / `-max-steps` spelling), and
exit code 2 vs 1 on a refusal (CLI-wide, not argv-specific).

### The fifth finding was in the instrument

`write-compare.py`'s `snapshot()` recorded `os.walk` `dirnames` entries as
`("dir", mode, b"")` without checking `islink`, so two trees with
`link -> left` and `link -> right` compared **equal** — both sides reading
`0o777` from the link's own `lstat`. A blind spot in the instrument is worse
than one in the port, because the instrument is what decides whether the
port has one.

The fix is three lines and the fixture is the point. The differ's self-test
now carries both a retargeted link and a real-dir-vs-link pair and asserts
5 differences; reverting the fix drops it to 4, with the retarget vanishing
entirely and the kind change reported as `MODE 775 vs 777` — the wrong
sentence about the right tree.

### And the harness reached the interop format

`pack` was added to the write comparison: export, export+seal, a minimal
export with no `--include-*` flags, a full export → seal → import → adopt
round trip into a nested second workspace, and an `import --dry-run` that
must write nothing. All seven `task` scenarios stay byte-identical; all six
`pack` scenarios differ, and the tranche is real:

* `pack.json` **key order** — Python insertion order vs Go alphabetical, in
  the manifest AND in `imports.jsonl` report rows (the Go needs `pyval.Obj`,
  not `map[string]any`)
* `origin.maro_version`: `"0.8.0"` vs `"go-port"`
* the MODE class with numbers: dirs 775/755, export files 664/644,
  quarantine files 664/**600**, adopted files 775/644
* Python's `import --dry-run` creates `inbox/memory/long` and
  `inbox/memory/medium`; the Go creates nothing
* export stdout prose

Two of the divergences it re-derived were **already documented as
deliberate** (`skill_records` routed to quarantine at `import.go:15`;
`rowID`'s refusal of id-less rows at `import.go:385-398`, where CPython
mints a colliding `imported-<pack>-` id and silently eats the second as
`already_imported`). An instrument that independently re-finds the
divergences someone already reasoned about is an instrument worth trusting
about the ones nobody has.

The id-less rows got their own scenario rather than a row smuggled into the
main seed: mixed in, every trust lane showed only that one known divergence
and hid whether the happy path agreed (L41).

Pack's tar archives are expanded rather than byte-compared, and the reason
is stated at the site because it is the one place this harness looks away
from bytes: gzip stores an mtime in its header. Measured — two CPython
exports 119 ms apart produced inner-tar digests `28b724195933` and
`3865dc7764ab` — even though `pack.py:246` pins every tar MEMBER's mtime to
0 and the gzip header mtime matched. What is compared instead: member
ORDER as a synthetic entry, and every member's name, type, mode and
content.

## The r11 review — one finding, and P6 fires twice in a row

The cross-family seat returned a single MEDIUM over the whole chunk, which
is what convergence looks like. It lived inside r10's own fix.

`task_store.py` builds **four** subparsers for `claim`, `complete`, `fail`
and `archive`, and only `p_fail` calls `add_argument("--error")`; the other
three declare `job_id` alone. The Go handles all four in one `case` arm —
which is fine, they share a code path — but it built **one `FlagSet`** for
the arm, so the four verbs shared an argument surface the Python never gave
them:

```
task complete <id> --error boom     python  rc=2, unrecognized arguments
                                    go      rc=0, "status": "done"
```

r10 fixed the *arity* of this exact parser and left the flag *set*
over-wide. Two rounds running, the round's only finding has been inside the
previous round's fix — which is P6 doing exactly what it says: the place to
look hardest is the code you just touched, because a fix is written with
the bug in mind and the surrounding invariants out of it.

The general shape is worth naming, because it will recur wherever the port
consolidates: **four parsers on one side and one parser on the other is a
divergence even when the four are identical in every respect the fix was
thinking about.** The Go's consolidation is a legitimate implementation
choice about *code*; it silently became a choice about the *argument
surface*, which is observable. Where a structural choice and an observable
surface disagree, the surface wins.

The pin is derived from `task_store.py`'s four subparser specs rather than
from the diff (L9), so a fifth verb added to the `case` arm without a
matching Python subparser fails the test rather than inheriting the arm's
flags by accident.

Measured after the fix: `complete`, `claim` and `archive` refuse `-error`
on both engines and `fail` still accepts it. The remaining difference on
the refusal path is exit code 2 vs 1, which is CLI-wide and already filed.

## `internal/provenance` gets a CPython differential (2026-08-27)

The package that decides whether a lesson is QUARANTINED had tests but no
interpreter behind them. Its whole content is three regexes transcribed
from `src/lesson_provenance.py` and a killswitch normalizer, and every
fixture it carried was ASCII — so what it was measuring was that a
transcription is a transcription, which is exactly the belief this arc
keeps finding wrong.

Three test files' worth of subject, in one package: the classifier
(`TestClassifyMatchesCPython`, 106 fixtures over every alternation member,
every branch, and both boundaries of the `[^.]{0,60}` window), the
whitespace class generalized from samples to a swept set
(`TestWhitespaceClassMatchesCPython`), the pattern SOURCE text and the
`minted_from` stamp values (`TestRegexSourceMatchesCPython`), and the
killswitch composed the way production composes it —
`GateEnabled(GetRaw(...))` against `provenance_gate_enabled()` over a real
`config.yml`, 43 bodies (`TestGateEnabledMatchesCPython`).

**Six divergences, none fixed here — the fix is a decision, not a
cleanup.** They fall in three classes, and the first two have a remedy
already sitting in this tree that this package never used:

1. **`\s`** — Python's is 29 code points, Go's is 5. Eleven pinned rows;
   the whole gap is 24 code points, swept. `U+000B` needs no Unicode at
   all. **Direction is UNSAFE**: CPython quarantines, the port does not,
   and an unquarantined lesson is injectable into every later run
   forever. It reaches the SOURCE leg too, where the text is the
   untrusted dispatch prompt itself. `pytext.SpaceClass` is the remedy
   and is a drop-in.
2. **`\b` / `\w`** — Python's `\w` is 142,940 code points, Go's is 63, so
   a non-ASCII letter touching a keyword suppresses `\b` in CPython and
   not here. Five pinned rows. **Direction is SAFE** (the port
   quarantines more). `pytext.WordStart`/`WordEnd` is the remedy for the
   OUTER boundaries only: `_SCAFFOLDING_RE` has two INTERIOR `\b` inside
   its `{0,60}` window, which is precisely the shape mission-r7 caught —
   a consuming boundary eats the window's own character and breaks the
   most ordinary input. Fixing 2 is arithmetic, not substitution.
3. **`re.IGNORECASE` folds the Turkish i** — CPython's fold table maps
   U+0130 and U+0131 to `i`; Go's `unicode.SimpleFold` does not. Three
   pinned rows. **Direction is UNSAFE, and it is the one an author can
   reach on purpose**: a dotless i is a one-character homoglyph edit that
   walks an instruction-derived lesson past this gate and not past
   CPython's. No remedy exists in the tree. This is NOT
   provenance-specific — `pytext`'s fold-invariance sweep covers the
   exported CLASSES, and this is about LITERALS: any `(?i)` pattern in
   the port with an `i` in a literal has the same hole. The reported
   figure was "40 patterns across 11 files", which is the count of `(?i)`
   patterns, not of exposed ones; re-censused, **16 of those 40, across 9
   files**, carry an `i` or `I` in the pattern tail — `pytext/reclass.go`
   3, `provenance/provenance.go` 3, `artifactcheck` 3, `intent` 2, and
   `jsonx` / `scrub` / `guard` / `evolver` 1 each. U+017F/`s` and U+212A/`k`, the other two special
   folds an ASCII pattern can reach, DO agree — measured, which is why
   the rows name the Turkish i rather than "unicode folding".

And in the killswitch, both safe-direction:

4. `provenance_gate_enabled: "\x1cfalse"` — Python's `str.strip()` strips
   the C0 separators U+001C..1F, `strings.TrimSpace` (`unicode.IsSpace`)
   does not. Gate OFF in CPython, ON here.
5. `[]` and `{}` — Python's fallthrough is `bool(val)` and an empty
   container is falsy; `GateEnabled`'s default arm returns true. Gate OFF
   in CPython, ON here.

Two non-findings worth recording, because both look like divergences:

- `off`/`no`/`yes`/`on` decode as BOOLEANS in PyYAML (YAML 1.1) and as
  STRINGS in yaml.v3 (1.2). The verdicts agree only because
  `GateEnabled`'s string denylist happens to spell all four. That is
  agreement by construction and is pinned as a TYPE divergence with a
  matching verdict, so deleting the string arm as dead code fails here.
  Single-letter `y`/`n` are strings in both — PyYAML does not resolve
  them — which is why the four cannot be generalized.
- An explicit `provenance_gate_enabled: null` AGREES: `GetRaw` reports
  the key present, and `GateEnabled`'s nil arm answers Python's
  `bool(None)`. The pin at `pack/hostile_test.go:568` describes the
  `Get[any]` composition, which is not the one `import.go:273` performs.

Every pinned row asserts the disagreement PERSISTS, so closing one fails
the test and forces the ledger to be updated rather than quietly
shrinking — proven by widening the port's `\s` toward Python's and
watching eleven rows and the swept class fail together.

**What that assertion is for, since the phrasing invites the wrong
reading** (Jeremy, 2026-08-27: *"'assert this difference continues to
exist' seems a little... wrong"*): it is a tripwire on the NOTE, not a
requirement on the code. Nothing here wants the divergence to survive.
A row is a written claim that the two engines differ HERE and for THIS
reason; when the row stops being true, the note has gone stale, and a
stale note about a security gate is worse than no note. So the failure
is deliberate and the correct response to it is frequently to **delete
the row** — which is exactly what happened to the `\s` class within the
day, ten rows removed rather than re-pinned.

The CPython premise is checked before either side is compared, and it
earned its keep immediately: two rows of the first draft claimed to
exercise the `as an excuse to stop` branch with goal text that had no
`as` in it, and a third assumed a divergence where two ASCII-vs-Unicode
differences cancel (a separator after the OPTIONAL determiner leaves Go
matching the bare noun, because U+00A0 is not a word character to it).
All three were green-and-measuring-nothing until the premise check fired.

Eight mutations, eight killed: an alternation member dropped, the excuse
window widened by one, the source-echo requirement removed, `off` dropped
from the denylist, `TrimSpace` dropped, nil read as absent, a stated
CPython premise flipped, and one `goDiff` deleted from the ledger. A
ninth (deleting the string arm outright) did not COMPILE and was replaced
rather than counted — a mutant that does not build proves nothing.

## The pack's key ORDER, and the escape r6 left behind (2026-08-27)

Two closures in one chunk, unrelated except that both are the same
mistake: **a fix scoped to the finding rather than to the construct.**

### `pack` writes CPython's dict order now

`maro pack` is how two Maro installs hand each other learning data. The Go
wrote every `pack.json` key and every audit row ALPHABETICALLY — Go's
`encoding/json` over a `map[string]any` — where CPython's `json.dumps`
writes a dict in INSERTION order. Same values, different bytes, in the one
format whose entire job is to be read by the other engine.

This file named the residual at the time and got the prescription right
(*"the fix is a typed manifest, not a renderer change"*). What it got
wrong was the reason it stayed a residual: *nothing hashes those bytes*.
True, and beside the point. `pack` IS the interop format.

`pyval.Obj` now threads through the whole package rather than one writer
being patched: `Export` builds the manifest ordered, `decodeManifest`
reads a foreign `pack.json` through `LoadsOrdered` so an INCOMING order
survives, and `Seal` uses `Obj.Set`. Three things a partial fix gets
wrong, each of which cost a fixture:

- `seal_pack` does `manifest["review"] = {...}` (`pack.py:457`). Assigning
  to an EXISTING key does not move it, so `review` stays sixth. Rebuilding
  or appending puts it last and rewrites every line after it.
- Artifact entries have TWO shapes, and the conditional key lands in the
  MIDDLE (`{class, path, rows, [quarantined_rows_skipped,] sha256}`)
  because `sha256` is assigned after it — and it is ABSENT for zero rather
  than written as 0. Collapsing the two builders into one optional field
  is exactly how the key ends up at the tail.
- The audit rows are `{**report, "action": ...}` and `{"class": cls,
  **rest}`; a spread puts new keys LAST and existing ones where they were.

**The digest is deliberately NOT reordered.** `payloadSHA256` is
`json.dumps(sort_keys=True)`, so `pyCanonJSON`'s new `Obj` arm still
sorts. Reordering it would have invalidated every pack either runtime has
already sealed. `TestPayloadSHA256MatchesPython` now feeds it entries in a
deliberately scrambled order against CPython's golden digest.

**No existing test encoded the old order.** The shapes they happened to
cover (`{lesson_id,new_id,outcome}`, `{name,outcome}`, `{kind,name}`) are
alphabetical, so a Go map agreed with CPython BY COINCIDENCE and every
`imports.jsonl` byte comparison passed while being blind to the bug. The
new fixtures reach the two lanes that actually diverge (`{rule_id, hyp_id,
outcome}` and `{class, path, outcome}`) and assert CPython's own rows are
non-alphabetical FIRST, so they cannot go vacuous.

Measured here, not taken on report: `pack.json` now differs in exactly one
line, `origin.maro_version`, and the harness output went 631 → 454 lines
with no key-order hunk left. The six differing scenarios stayed six, which
is the honest number — the remaining reasons are the ones already named
out of scope (file/dir MODE, Python's non-inert `--dry-run`, CLI stdout
prose).

`maro_version` is now ELIDED rather than allowlisted, because the
allowlist works per ENTRY and this is one FIELD of a file whose every
other byte is what the harness exists to compare. The two spellings are
enumerated, not matched by shape, so a port writing anything else there
fails the elision-counts check instead of being quietly normalised away.

### `\S` — the escape r6 rebuilt around

`intent.fileOutputRe` picks the execution LANE. mission-r6 found that Go's
ASCII `\w` missed `save the summary to café.md` and rebuilt the pattern's
`\b`, `\s` and `\w` from `pytext`. mission-r7 then found a HIGH inside
r6's own fix. Neither round touched the `\S` sitting between them, and
r6's doc comment ENUMERATED the three escapes it had fixed — so the file
read as complete.

Measured, both engines:

```
write to a<U+00A0>b.md    CPython False    Go true
write to a<VT>b.md        CPython False    Go true
write to a b.md           CPython False    Go false   (the ASCII control)
```

Go's `\S` is the complement of five code points, Python's the complement
of 29, so a separator inside the filename stem is eaten by Go's greedy
`\S*` and stops Python's. Direction is the REVERSE of r6: Go forces
`lane=agenda` where CPython leaves it `now`. `pytext.NotClass("")` is the
drop-in, and the r6 corpus's own comment reasons FROM `\S*` being "greedy
over anything non-space" without ever asking whether the two engines agree
on which characters those are — in the one file that exists because they
do not.

### The census that closes the class

Five findings of this shape now, across three packages, one escape at a
time. No amount of looking harder finds the next one: the reviewer has to
already suspect the escape is there.

`internal/pytext/escape_census_test.go` parses every production `.go` file
in the module, pulls the string literals out of each `regexp.MustCompile`
call, and fails on any `\s \S \w \W \b \B` that is not in a hand-written
allowlist naming WHY. The 2026-08-27 census: **140 production files, 81
calls, 4 carrying an escape**, in two files, both with a written reason
(`provenance` keeps its literals byte-identical to the Python and
transforms at the call; `scrub`'s `\b` is a named residual because the
pattern is used for `ReplaceAllString` and the offsets matter).

Parsed rather than text-scanned, and that is the load-bearing decision: a
comment INSIDE the call is the common case here, and stripping `//` to
end-of-line instead would truncate any pattern containing `://` and hide
whatever follows — a false NEGATIVE in a test whose whole job is to not
have one. Mutation-proven on that exact case.

Five mutations, five killed: a new escaped pattern in an unlisted file, an
escape added to an allowlisted file, a stale allowlist entry whose
divergence no longer exists, an escape after a `://`, and the `\S` fix
reverted (which fails the three new intent rows and only those).

This does not make the mistake impossible. It makes it impossible to make
SILENTLY, which is the difference between a lens we watch for and a shape
that cannot reach a commit.

## r12 of `internal/artifactcheck` — one finding, and it was the residual I had just filed as unfixable (2026-08-27)

Round 12, gpt-5.6-terra at high effort, whole chunk plus eleven rounds of
fixes. **One finding, MEDIUM.** That is what convergence looks like after
eleven rounds, and the finding is worth more than the count.

It is the Turkish-i class — the one this arc had filed hours earlier, in
`provenance`, with the words *"no remedy exists in the tree"*. The reviewer
found it independently in a different package, with a falsifier that makes
the consequence unarguable. Verified here before touching anything:

```
extract_write_claims("wrote İnto absent.txt")
  CPython  ['absent.txt']
  Go       []
```

`artifactcheck` is the FABRICATION detector — the thing that catches an
agent claiming it wrote a file it did not write. Go extracts no write
claim, so nothing is checked and the result passes. **A detector that
fails open**, reachable with a one-character homoglyph edit, in the
package whose entire job is to not be fooled. The ASCII control agrees on
both engines, which is what separates this from a broken fixture.

### "No remedy exists in the tree" was a reading, not a measurement

That claim was made the same morning, in the entry that files L14. It
assumed the fix had to be a generated fold table or subject
normalisation — the first because `pytext` had no folding helper, the
second because that is the obvious way to make two engines agree about
case. Both assumptions were wrong, and one measurement dissolves them.

Exhaustive, both engines, every ASCII letter and digit against all
0x110000 code points:

| letter | CPython `IGNORECASE` | Go `(?i)` |
|---|---|---|
| `i` | `I i U+0130 U+0131` | `I i` |
| `k` | `K k U+212A` | `K k U+212A` |
| `s` | `S s U+017F` | `S s U+017F` |
| every other letter and digit | identical | identical |

**One letter.** U+0130 and U+0131 are singletons under
`unicode.SimpleFold`; U+212A and U+017F have fold orbits that reach ASCII,
which is why `k` and `s` already agreed. No table. And normalising the
subject was never the only option — it would have moved the offsets
several callers depend on, where rewriting the PATTERN preserves them.

`pytext.IClass` is `(?-i:[iI\x{130}\x{131}])`, spelled with the flag off
and both cases listed so it means the same thing inside a `(?i)` pattern
and outside one — the fold-growth hazard `WordClass` documents.

### The transform, and why it is a transform

`pytext.PyFoldI(pattern)` rewrites each literal `i`/`I` into `IClass`, at
the call, the same shape as `provenance`'s `pySpace()`. Hand-splicing was
the alternative and it is how one of the fifteen sites gets missed —
which is the exact defect being fixed here, one level up.

What it must NOT touch is the interesting half, and each skip is a
construct these patterns actually contain:

- inline flag groups — `(?i)`, `(?-i:`, `(?is)`, `(?P<path>` — where the
  `i` IS the flag.
- escapes, because consuming a backslash's partner corrupts a pattern
  quietly.
- character classes, which cannot hold a group. A bare `i` there under
  `(?i)` is a real divergence `PyFoldI` cannot fix, so it **panics**
  rather than returning a pattern that is wrong in a way nothing reports.
  Every caller is a package-level `MustCompile`, so that lands at init in
  the run that introduced it.

A consequence worth stating because it looks like a bug: `PyFoldI` is
deliberately NOT idempotent. `IClass`'s own class body holds a literal
`i`, so applying the transform twice panics. Composition order matters and
failing loudly is how a caller learns that.

### Evidence

- The fold sweep is exhaustive on both sides for all 36 characters, in
  both directions — a code point Go folds and CPython does not would make
  the port fire where CPython stays quiet, and that half is checked too.
- `TestPyFoldISkipsWhatItMustNotTouch` — thirteen constructs, each also
  required to still COMPILE.
- `TestPyFoldIRefusesABareIInsideAClass` — five cases including `IClass`
  itself.
- `TestPyFoldIPatternsAgreeWithCPython` — 57 subjects x 7 real patterns
  from this port, driven through both engines. It counts the cases the
  UNFOLDED pattern gets wrong and fails if that count drops below five;
  it is currently 18, so a corpus that stopped reaching the Turkish i
  fails instead of certifying the fix vacuously.

Two fixtures in the first draft used `\i` and a backreference, neither of
which RE2 has. They were fixture bugs, not findings, and the compile
assertion is what caught them.

### The mutation battery found the site the review did not

r12 named three patterns. `PyFoldI` went onto **four** — the fourth being
the `excl` lookahead inside `buildStdoutBranches`, the
`(?!\s+(?:file|to|into|path|dir))` after `output`, whose `file`, `into`
and `dir` each carry an `i`. That fourth wrap was mine, added because the
census habit says look at all of them, and it was correct.

It was also, for about an hour, **unfixtured**. Dropping `PyFoldI` from
each of the four sites in turn:

| site | rows that fail |
|---|---|
| `outputClaimRE` | E47 E48 E49 E50 |
| `excl` (the stdout lookahead) | *nothing* |
| `successClaimRE` | X22 |
| `failureAckRE` | X19 X20 |

A guard that cannot fail is worse than no guard
(`feedback_mutation_from_file.md`), and this is the shape it takes in
practice: not an absent test, but a fix broader than the finding that
prompted it, with fixtures written to the finding's list. The rows came
from the FILE — four wrapped patterns — and the battery is what made the
gap between those two numbers visible.

S27–S30 close it, and this exclusion is the one site of the four whose
gap runs toward **over**-claiming: a Go that fails to exclude reports a
concrete-stdout claim on text CPython reads as naming an output file.
Both directions of the same gap now appear in this package — E-rows and
X19/X20 under-report, S-rows over-report — which is the argument for
pinning all of them rather than the one the reviewer happened to land on.

```
"output fİle has 3 rows"   CPython false   Go (unfolded) true
"output into 3 places"     CPython false   Go            false   <- the control
```

## The census found two security bypasses the review did not (2026-08-27)

r12 found the Turkish i in the fabrication detector. The obvious next
move was to roll the fix out, and the obvious way to scope that was the
`(?i)` count already sitting in `BACKLOG.md`: *"16 of 40 patterns across
9 files"*. Re-measuring it a second way answered **35 of 47 across 10** —
disagreeing on the count and on the file list. Both were greps over a
shape that needs parsing, so neither was the number, and `artifactcheck`
— the one file whose truth was independently known, at four — was
undercounted by both.

Same failure as the scope ledger, one week apart. So: parse it.

`internal/pytext/foldcensus_test.go` walks every production
`regexp.MustCompile` in the module. **Its predicate is `PyFoldI`
itself** — exposure is defined as *"the remedy would change this
pattern"* — which is the part worth keeping. A separate parser for "does
this carry a foldable i" is a second opinion about the same question,
free to disagree with the transform, and a census that disagrees with its
own remedy is exactly how a pattern gets certified safe and stays broken.

It answers: **140 files, 81 compile calls, 32 case-insensitive, 28
carrying a literal `i`** — 8 wrapped, 20 not. And on its first run it
named two surfaces no review round had:

```
scrub.Secrets("apı_key: ABCDEFGH12345678")
  CPython  "[REDACTED]"
  Go       "apı_key: ABCDEFGH12345678"     <- the secret, in the clear

guard.ScanContent("ıgnore previous instructions")
  CPython  override attempt, risk medium
  Go       is_clean=true                    <- the guard, walked past
```

A redaction bypass and a prompt-injection bypass, one homoglyph each.
`scrub`'s runs OPPOSITE to the `\S` finding mission-r6 filed against that
same pattern: that one over-redacts and destroys evidence, this one
under-redacts and leaks. Both directions of one line.

### What the census could not see, which is the more useful half

The first draft read `artifactcheck` as two-exposed and moved on. That
package builds nine of its stdout branches through a `lit(name, body)`
helper, so `printing`, `verified` and `running it` reach `MustCompile` as
a PARAMETER and contribute nothing to the literal at the call. A scan
that cannot see a pattern must SAY so rather than certify it — the same
false-negative rule the escape census states about stripping comments —
so opaque operands are now reported and must be declared. Six more sites
appeared immediately, including `evolver/store.go:995`, which the literal
scan had missed entirely.

`pytext.X` is deliberately not opaque. Every exported class there is
spelled `(?-i:[...])`, fold explicitly off, so splicing one into a `(?i)`
pattern contributes no foldable letter by construction. That is why the
helpers are written that way, and it is what lets the scan ignore them
instead of drowning in them.

### The first pattern PyFoldI refused, and the design reversal it forced

`guard` ports `exfiltrat[ei]`. The `i` is inside a character class, which
cannot hold a group, so `PyFoldI` panicked at init — working exactly as
designed, refusing to emit a pattern that is wrong in a way nothing
reports. The class is spelled with the new `pytext.IClassBody` instead.

Then it panicked on the fixed pattern too, because "already spelled
right" and "never touched" look identical to a scanner that only knows
about a bare `i`. `PyFoldI` had been made deliberately NON-idempotent
hours earlier, and that was written up as a feature — composition order
should be loud. The first hand-written class body disproved it. The
declared spelling now passes through as a unit, the transform is
idempotent, and `TestFoldIIsIdempotent` pins the property the panic test
used to deny. What still panics is what always mattered: a bare `i` in a
class nobody has spelled out.

Worth naming plainly, because it is the second time in two days a claim
in this arc was a reading rather than a measurement, and both times the
claim was mine: *"no remedy exists in the tree"*, then *"non-idempotent
is the right design"*.

### A fixture aimed at the fix is not a fixture aimed at the code

The exfiltration rows were the sharpest lesson of the batch. Two rows
were added for the class case, both passed, and a mutation that emptied
the class of its two code points **killed nothing** — because both rows
spelled the Turkish i in `exf·i·ltrat`, the stem, where the folded
literal already handled it. The class is the LAST character. Rewritten to
put the homoglyph in the class position, plus the ASCII control:

| mutation of `exfiltrat[e` + `IClassBody` + `]` | killed by |
|---|---|
| `[ei]`, `[eiI]` (bare i, any spelling) | init panic |
| `[e\x{130}\x{131}]` (drops plain `i`) | the ASCII control |
| `[e\x{131}]` (keeps only the dotless i) | the U+0130 row + the control |

Every code point in that class body is now individually pinned, and the
whole battery is:

| mutation | rows killed |
|---|---|
| `scrub` loses `PyFoldI` | the 4 keyword rows, controls green |
| `guard`'s `boundedBoth` loses it | override ×2, persona, tool-call tag |
| a new exposed `(?i)` in an unlisted file | census, by file and line |
| the same pattern **without** `(?i)` | *nothing* — the control |
| a pattern built from a local identifier | census, as opaque |
| an allowlisted file gains an exposure | census, count 4 vs 3 |
| an allowlist entry whose divergence is gone | census, as a lie |

## The provenance rollout, and a tripwire that fired on its own author (2026-08-27)

`internal/provenance` was the entry this arc had **pinned rather than
fixed**, with the note that no remedy existed in the tree. It was also the
only one on the census list already measured unsafe:

```
Classify("the prompt \u0131nstructs x", "", "")
  CPython  "prompt"    quarantined
  Go       "outcome"   injectable
```

One homoglyph walks an instruction-derived lesson past the port's
quarantine gate and not past CPython's. All three patterns now go through
`pytext.PyFoldI`, spelled at each call rather than hidden inside
`pySpace()` — a helper that hides the wrap reads as UNWRAPPED to the
census, and that is the correct answer for a census that only knows what
it can parse.

### The blocker was real, and it had an answer

`TestRegexSourceMatchesCPython` pins the port's pattern text byte-for-byte
against CPython's by un-substituting `SpaceClass` back out of the compiled
pattern. `IClass` has to come out the same way — except it stands in for
BOTH `i` and `I`, so the round-trip is unambiguous only while CPython's
patterns carry no uppercase `I`.

They carry none. Measured: 11 / 5 / 1 lowercase across the three, zero
uppercase. So this is licensed edit 3 **with the property asserted rather
than assumed** — a pattern that gains an `I` fails there, loudly, instead
of silently comparing the wrong string. That assertion is the difference
between a round-trip and a coincidence.

### The rollout tripped its own tripwire

Un-pinning went: three rows lose their `goDiff`, the `whyFold` cause
leaves the ledger (`whyBoundary: 5`, total 5), delete the census allowlist
entry. Except the census got there first —

```
internal/provenance/provenance.go is allowlisted for 3 exposed (?i)
pattern(s) and has none. The entry describes a divergence that no longer
exists, which is a lie about this codebase — delete it.
```

— which is the check working, on the person who wrote it, the same day.
Both directions of the allowlist are live: an entry that stops matching
fails as loudly as a file that stops being listed.

### A structural assertion is not a behavioural one

The mutation battery is where this got interesting. Dropping `PyFoldI`
from `obedienceRe` or `scaffoldingRe` killed **only** the structural
`IClass` assertion — no behavioural row moved.

That assertion proves the wrapper is PRESENT. It does not prove it
MATTERS, and those are different claims: the first survives a pattern
whose `i` no input ever reaches, the second does not. Four rows added —
`_OBEDIENCE_RE`'s `i` is in `follow (it|them) exactly`, and
`_SCAFFOLDING_RE` carries exactly one, in `give\s+up`, which also needs
the goal as well as the lesson to reach the scaffolding signal.

### Three in one day is a habit, not three bugs

| fix | sites changed | sites fixtured |
|---|---|---|
| `artifactcheck` | 4 | 3 — the ones r12 named |
| `guard` exfiltration class | the class body | the STEM, not the class |
| `provenance` | 3 | 1 |

Every one was correct code with an unprovable guard, and in each the
missed site was the one no review had named — because the fixtures were
written from the FINDING while the fix was written from the FILE. That is
L9 exactly, one level up, and it has now fired often enough to stop being
a discipline: `tools/mutate-wraps.py` enumerates a wrapper's call sites,
removes each in turn, and reports the ones whose removal leaves the suite
green. It cannot invent the missing fixture. It can no longer be unaware
of the site.

## Judging the 120: the loop's own spine is in the undeclared queue (2026-08-27)

`PORT_STATUS.md` made the denominator real — 63 of 183 modules declared,
120 not — and then said the 120 were UNJUDGED, which was honest and not
useful. This is the first pass at judging them.

**What this is.** Every undeclared module's first docstring line, read and
sorted into tiers. That is a JUDGEMENT from a one-line summary, not a
measurement of the code, and it is written down that way on purpose: this
arc has now twice shipped a number that read like a measurement and was
not. Treat every tier below as revisable by anyone who opens the file.

**The finding that changes the plan.** The core loop's spine is in the
undeclared queue. `internal/loop` declares `handle.py`, `step_exec.py`,
`loop_execute.py`, `loop_blocked.py`, `loop_post_step.py`,
`loop_planning.py`, `loop_artifacts.py`, `observe.py`, `planner.py` and
`interrupt.py` — the PHASES — while the runner that sequences them is
not named anywhere:

| module | lines | what it is |
|---|---:|---|
| `loop_finalize.py` | 1319 | loop finalization (Tier 3 split of agent_loop) |
| `agent_loop.py` | 871 | the autonomous loop runner itself |
| `scope.py` | 736 | scope + resolved intent — injected into planning, live on this box |
| `loop_parallel.py` | 684 | parallel and DAG-aware step execution |
| `conductor.py` | 676 | the top-level role and session entry point |
| `loop_types.py` | 659 | core types and the state machine |
| `orch.py` | 610 | orchestration core utilities |
| `loop_init.py` | 604 | budget gate, context setup, dry-run adapter |
| `cli_args.py` | 574 | CLI argument parser (`cmd/maro` may cover part) |
| `pre_flight.py` | 463 | cheap plan review before execution starts |
| `run_lease.py` | 237 | the per-loop flock, held from init to process death |
| `killswitch.py` | 162 | the global kill switch |

**~7,600 lines, and it is not an optional tranche** — it is the thing the
ported phases are phases OF. Whatever "the first pass of the go port
completely implemented" means, it cannot mean a port with no loop runner.
That reframes the remaining work: the tranches previously queued
(`handle_queue`'s portable half, `mission.py` slice 2, the rest of
`skills.py`, `playbook.md`, then heartbeat / projects / escalation /
notifications / viz) are real, and this sits in front of them.

**And it has an order**, which is the actionable half. `agent_loop.py` is
one 597-line function, `run_agent_loop`, and its import list is very
nearly the table above — `loop_types`, `loop_init`, `loop_finalize`,
`loop_parallel`, `loop_report`, `run_trace`, `tool_registry`,
`container_exec`, `worktree`. So the tranche is a chain, not a pile:

1. `loop_types` — 65-field `LoopContext`, 22-field `StepOutcome`,
   20-field `LoopResult`, `ContributionLedger`, `LoopPhase` and
   `LoopStateMachine`. Everything else here depends on it, and its
   Python-specific parts (default factories, `_new_terrain`,
   `_new_world_facts`) are the divergence-prone half of a types module.
2. `loop_init` / `loop_finalize` / `loop_parallel` — the three Tier-3
   splits `run_agent_loop` calls directly.
3. `agent_loop` itself — one function, and by L48 its SHAPE is observable,
   so it is not a candidate for tidying on the way across.
4. `conductor`, `orch`, `scope`, `pre_flight`, `cli_args`, `run_lease`,
   `killswitch` — reachable in parallel with the above.

The rest, by tier, at the same confidence:

- **Platform the loop calls into** — `container_exec` 1676, `web_fetch`
  1172, `hooks` 662, `mcp_client` 525, `path_rewrite` 477, `channels`
  472, `scheduler` 464, `world_facts` 457, `tool_registry` 399,
  `path_tokens` 342, `boot_protocol` 301, `ancestry` 292, `llm_errors`
  275, `autonomy` 251, `security` 251, `prefixes` 188, and the smaller
  `terrain` / `process_identity` / `tool_search` / `runtime_tools`.
- **Memory and knowledge backends** — `memory_backends` 689,
  `memory_quality` 593, `codebase_graph` 492, `knowledge_bridge` 463,
  `memory_bridge` 458, `knowledge` 432, `gc_memory` 432, `hybrid_search`
  352, `memory_sqlite` 351, `memory_jsonl` 188, `memory_port` 149,
  `lat_inject` 178, `age_stamp` 89.
- **Self-improvement instrumentation** — the machinery this arc's own
  lessons run through: `navigator_shadow` 1231, `shadow_lane` 759,
  `skill_lifecycle` 843, `harness_optimizer` 768, `delta_replay` 729,
  `mint_grounding` 608, `thinkback` 577, `convo_miner` 570,
  `strategy_evaluator` 485, `claim_verifier` 477, `revisit` 420,
  `cross_ref` 417, `backchain` 305, `reanchor` 282, `portability` 252,
  and the navigator trio.
- **Dev tooling and readouts, likely NOT first-pass runtime** —
  `loop_report` 2705, `eval` 1186, `correspondence` 1174 (dev-only by
  decree), `doctor` 1001, `camera_readout` 817, `discretion_readout` 710,
  `map_lens` 644, `backend_benchmark` 616, plus the readout family and
  `viz_server`.
- **External interfaces** — `orch_bridges` 1522, `tail_jobs` 1149,
  `audit_repair` 797, `telegram_listener` 613, `slack_listener` 428,
  `notify_telegram` 354, `conversation` 340, `persona_dispatch` 254,
  `listener_core` 55. (`persona.py` and `worktree.py` are being ported
  now.)

### The preamble was wrong one chunk after it was written

`PORT_STATUS.md` shipped saying `killswitch`, `ancestry`, `prefixes` and
`security` "also appear by filename somewhere, so none of them is missed
here". Measured while writing this section: **none of the four appears by
filename in any production `.go` file**, and all four are in the
undeclared queue. They are examples OF the generator's blind spot, not
counterexamples to it.

The file exists because a hand-maintained enumeration was wrong at birth.
Its own preamble then was too, in the very next chunk, in the paragraph
about the thing it cannot see. Corrected in the generator, not the
output, or it would come back on the next run.

## Two packages arrive, and one of them wrote to the repository (2026-08-27)

`internal/persona` (persona.py, 1060 lines) and `internal/worktree` +
`internal/procid` (worktree.py, 722) came back from two build agents.
Both verified here rather than accepted: scope checked against the commit
(`persona` touched 15 files, all under its own directory, nothing else),
gates re-run in this tree (48 packages green, `gofmt`/`vet` clean), and
the module-wide guards — the `(?i)` census and the regex-escape census —
run against the new code, which the agents' branches predate.

### The incident

A run of `internal/worktree`'s own tests **created a linked worktree of
the maro repository** and left an autocommit on the checked-out branch:
author `Port Test <port@example.invalid>`, the fixture's pinned dates,
the fixture's own commit message, on a branch named `maro/L1/wé-rk`.

Verified independently, not taken from the report:

- the commit exists (`effb7730`), and is an ancestor of **neither**
  `origin/main` nor `origin/go-port`;
- no stray branch and no stale worktree registration survived;
- nothing under `~/.maro` was touched.

Contained — and contained by luck rather than by design. The mechanism is
now L56: `git -C ''` is a documented no-op that runs in the caller's
working directory, and `str(Path(""))` is `"."`. Neither half looks wrong
alone; together they turn an absent repo argument into "whatever checkout
this process is standing in". `worktree.py` already carried a dead
`if not repo_dir` guard that had been FILED as a divergence. This is that
guard's consequence, demonstrated.

`internal/worktree/guard_test.go` fingerprints HEAD, the ref list and
`git worktree list` around the package run and turns a green run red if
they moved. I re-ran the package here with my own before/after
fingerprint taken outside the test binary — the refs and worktree list
were byte-identical afterwards. The fingerprint is the net; the
prevention is rejecting the empty path.

### `effb7730` was not garbage, which matters for how it was taken

The stray autocommit captured fourteen real files — the agent's own
in-progress package. So its work spanned two commits, and cherry-picking
only the tip would have landed half a package. Taken instead as the tip
CONTENT of the two directories (`git checkout ba3e0373 -- …`), which
brings the finished code with no fabricated authorship attached.

### What the agents found that the catalog already knew

`internal/persona`'s builder reported, as a candidate lens, that a
differential which rebuilds the value instead of calling the function
measures nothing — three instances in its own package, two of them found
by mutation, one surviving **four** separate mutations of the function it
claimed to guard. That is **L10**, already in the catalog at nine
instances since the `runIntrospect` finding.

A second builder re-deriving a catalogued lens from scratch is the
argument for the 2026-08-27 decree, restated: a pattern catalog only a
reviewer reads is a catalog, not a ratchet. L10 is now at twelve.


## The gate that decides what a run may cost, before it costs anything (2026-08-27)

`internal/loopinit` ports the deterministic half of `loop_init.py`:
`_budget_gate` and its `_coerce_cap` helper. That is the circuit breaker
that runs before a single token is spent and answers two questions — what
may this run cost, and may it start at all.

What is deliberately NOT here is named in the package doc: `_initialize_loop`
(it builds a live context and touches the run dir), `_DryRunAdapter`, and
the WRITE half of `_stamp_refusal_verdict`. `BudgetGate` returns a
`BudgetDecision` carrying the refusal reason and a `ReopenPayload()`; who
stamps it and who notifies is the caller's problem, and stays in Python
until the run-dir tranche lands.

### The ladder has three states where a port wants two

`config.get` answers ABSENT, an explicit null, or a number, and
`loop_init` means something different by each:

- **absent** — auto. `max($10, 4 x p90)`, scaling the breaker with what
  this box's successful runs actually cost.
- **null or 0** — uncapped, and the operator meant it. `if _per_run > 0`
  leaves `cost_budget` as `None`.
- **malformed** — fails CLOSED to the default, with a warning. Not the
  same as the opt-out, and a port that collapsed them would turn a typo
  into an unlimited run.

`ConfigValue{Absent bool; Value any}` carries all three, mirroring
Python's `_CONFIG_ABSENT` sentinel. Its ZERO VALUE is the dangerous one:
`ConfigValue{}` reads as present-and-null, which is the uncapped opt-out.
A test in this package was written by leaving the fields off and passed
for exactly that reason — both arms nil, agreeing about something that
had nothing to do with the p90 they were comparing. **L19**, and the type
now says so at its own declaration.

### What the differential found on its first run

CPython logs a line when the daily-cap check itself fails:

    budget gate: daily cap check failed (non-blocking): <err>

...and then PROCEEDS, which is the right posture for a breaker whose
own instrument is broken. The port had the proceeding half and not the
line: `spend_today` was modelled as a bool, so the error text had nowhere
to go. **L26**, and the sort of thing that is invisible to review because
the CONTROL FLOW is correct — only the emitted string is missing.
`BudgetEnv` grew a `SpendTodayError string` and the warning came back.

### One survivor, and it is the mutant's fault

Thirty mutants, twenty-nine killed. The survivor is
`if p90 != nil && *p90 != 0` -> `if p90 != nil`, the Go spelling of
Python's `if _p90:` truthiness. It cannot change an answer: at a p90 of
0.0 the branch it would newly take computes `max($10, 4*0) = $10`, which
is what the else arm returns. A negative p90 is truthy on both sides; a
NaN is too (`NaN != 0` in Go, and CPython finds a NaN truthy).

That is **L8** — a bad mutant, not a test gap — but the ABSORPTION is a
claim, and this port has now shipped one comment that was false the day it
was written (`ResolveLogLevel`, L52 instance 3, three days of a surviving
mutation ago). So the claim is a test:
`TestTheP90TruthinessTestIsAbsorbedByTheFloor` asserts
`maxF(DefaultPerRunUSD, KillP90Multiplier*0) == DefaultPerRunUSD` and then
re-asserts it through the real gate. A tuning pass that drops the floor to
zero fails at the claim rather than quietly making the comment beside it
untrue.

### A divergence noted instead of fixed

`pyval.Repr` of a Go map is the `<unordered map: decode with LoadsOrdered>`
placeholder where CPython prints `{'a': 1}`, so the malformed-cap warning
reads differently on the two sides when the malformed value is a mapping.
Config values arrive as unordered `map[string]any` from yaml.v3 and there
is no order to render, so this is not reconcilable and not worth
pretending otherwise. Both sides' text is pinned by a per-scenario flag —
NOT routed through a normaliser, which would move the assertion into the
normaliser (**L51**).

The negative-cap rows in the corpus are the other half of that honesty:
`cost_budget = -5.0` is nonsense, `if ctx.cost_budget` finds it truthy,
and it is the ONLY input that separates `if _warn and _warn >= cap` from a
bare `_warn >= cap`. Contrived and reachable — `cost_budget` is a plain
caller argument with no validation anywhere between here and it.


## The schedulers, and a known gap that had three pins waiting for it (2026-08-27)

`internal/loopparallel` ports `_run_steps_parallel` and `_run_steps_dag`
with the step body lifted out as a `RunFn`. Everything inside `_run_one`
below the scheduling belongs to other tranches -- `_execute_step`,
`_run_in_step_worktree`, `metrics.record_step_cost`, the post-step
security scan -- and the package doc names each. `_run_parallel_batch`
and `_run_parallel_path`, the two folds that build StepOutcomes and a
LoopResult, are slice 2.

The differential drives the REAL functions with two things replaced:
`_execute_step`, and `_run_in_step_worktree`. The second replacement is
not a convenience. That function asks `llm` for the run-scoped cwd and,
if it is a git checkout, provisions a worktree and merges it back -- and
a test in this port has already created a linked worktree of the maro
repository and left an autocommit on the branch it checked out (L56, five
sections up). It is stubbed, and the file says why at the stub.

### What a step that never ran is told

Six sentences, and they are the reason this is worth porting at all:

	parallel execution error: %s          the step raised
	parallel fan-out timeout (%ds)        the batch ran out of time
	missing from fan-out results          defensive; unreachable today
	dag execution error: %s               the step raised
	dag timeout (%ds)                     the dag ran out of time
	dag: upstream dep did not complete    a dep never resolved

Two of the six are reachable from a plan a PLANNER wrote. A dependency
tag naming a step that does not exist -- `[after:9]` on a three-step plan
-- leaves `remaining_deps[2] = {9}`, and 9 never completes, so step 2 is
never submitted and comes back with the last sentence. Nothing raises,
nothing logs. A self-dependency and a two-step cycle do the same. All
three are fixtured.

### The timeout filler is not the last word

The differential's find, and it is a shape review does not catch because
the control flow is right and the SCOPE is wrong. `_run_steps_dag`
handles its timeout by filling every in-flight step with
`dag timeout (Ns)` and then `break`-ing -- but the break leaves the
`while`, not the `with ThreadPoolExecutor(...)`, and that block's exit is
`shutdown(wait=True)`. Every in-flight step therefore runs to completion,
and `_run_one` writes its own result BEFORE the return statement is
reached, overwriting the row that was just put there for it.

So `dag timeout` survives only for a step that never finishes at all, or
one that raises. Its DEPENDENTS, meanwhile, keep
`dag: upstream dep did not complete` -- a plan where the upstream visibly
succeeded and the downstream says it did not.

`_run_steps_parallel` does not share this: only the main thread writes
`outcomes_by_idx`, from `f.result()`, so its own filler is final. Two
paths, one clock, opposite answers. The port had the parallel behaviour
in both places until a fixture with a three-second sleep and a
one-second `MARO_STEP_TIMEOUT` said otherwise.

### An empty plan raises on one path and not the other

`_run_steps_parallel` passes `min(max_workers, len(steps))` to the pool
and `ThreadPoolExecutor` refuses a `max_workers` of zero -- so an EMPTY
step list raises `ValueError` rather than returning an empty list, and so
does a configured `parallel_fan_out` of 0. `_run_steps_dag` passes
`max_workers` through unclamped, so the same empty plan returns `[]`.
Both are pinned; neither is fixed, because this port reproduces Python.

### `int()` reads more digits than a port assumed, and three pins knew it

`MARO_STEP_TIMEOUT` is read with `int(os.environ.get(..., "600"))` and
nothing catches what `int()` raises, so a value of `"10m"` does not fall
back to the default -- it kills the fan-out before a step is submitted.
Building the corpus for that expression found two defects in
`pyval.intFromString`, which is one of this port's most-used helpers:

1. it scanned BYTES for `'0'..'9'`, where CPython converts through
   `Py_UNICODE_TODECIMAL` and accepts every character with a decimal
   digit value. `int("٦٠")` is 60, `int("６０")` is 60, and `int("6٠")`
   mixes two scripts in one number and is also 60.
2. it stripped with `pytext.Strip`, which is `str.strip()`'s rule. It is
   not `int()`'s. Measured over all 1.1M code points: `str.strip()`
   removes 29 characters and `int()` skips 25 -- the same set MINUS
   U+001C..U+001F, the four ASCII information separators. So
   `int("\x1c60")` raises where `"\x1c60".strip()` is `"60"`.

Those four code points are the same ones that separate Go's `\s` from
Python's in this port's regexes. This is their fourth appearance and the
first where they divide two PYTHON predicates rather than the two
languages.

The first defect was a FILED known gap with pin tests in three packages.
All three fired on the run that closed it, and each said what to do:

	internal/heartbeat   TestNonAsciiDigitCadenceIsANamedDivergence
	internal/introspect  TestNonASCIIDigitIsAKnownGap
	internal/syshealth   TestACounterInNonASCIIDigitsIsRefusedWhereCPythonReadsIt

Each pin is deleted and its fixture moved into the table beside it, with
a U+001F row alongside for the half of the fix that still refuses. The
residual note at `pyval/pyint.go` is rewritten to say it is closed.

That is the argument for pinning an accepted residual rather than
describing one, made concrete: syshealth's prose had been true for five
review rounds and would have stayed on the page for five more.

### A field with no reader

`looptypes.LoopContext.StepCallback` was declared `func(StepOutcome)`.
The Python signature is `callable(step_num, step_text, summary, status)`
(agent_loop.py:161) and no site in the tree uses the other shape. Nothing
caught it because the field has no Go caller either -- a field is two
claims and this one had no reader to contradict the writer (L16). Fixed
before building on it, with the three call sites listed at the
declaration, because the THIRD argument is not a summary at all of them:

	loop_post_step.py:1123  step_summary          (the docstring's name)
	loop_blocked.py:540     stuck_reason or "blocked"
	loop_parallel.py:388    result[:120]          (the raw step output)

PYTHON-side inconsistency, filed not fixed: a parallel step's live
channel line shows raw result text where a sequential step's shows a
summary.

### The obvious helper was the untested one

`_drain_pending_context` is eleven lines, pure, and had no differential.
Five of the battery's mutations survived it — including one that swapped
`drain()` for a peek, which returns the same two strings and leaves the
batch to be delivered to the NEXT boundary as well. A helper being
obvious is not coverage, and a comparison of return values alone cannot
see a consume: `TestDrainPendingContextMatchesCPython` asserts what is
left pending on both sides as well as what came back.

The corpus is twelve rows, and the two that matter most are the ones
about ORDER of operations: `drop_source("time")` runs before the empty
check, so a ledger holding only a `[time]` contribution renders nothing
and hands back the bare ancestry — a wall-clock claim never replays,
because it is stale by the time a batch drains it.

### Go's Unicode table is a revision behind CPython's, and the fix was already written

Making `pyval.Int` read Unicode digits meant recovering a digit's VALUE,
which Go does not export. The first version walked `unicode.Nd`'s ranges
directly and passed every fixture in `pyint_diff_test.go`. Then a census
comparing CPython's Nd against the Go helper, code point by code point,
failed on its first run at U+1E5F4.

Go ships Unicode 15.0.0; the interpreter on this box has 16.0.0. Eighty
code points in seven blocks — Garay, Myanmar Pao, Eastern Pwo Karen,
Sunuwar, Gurung Khema, Kirat Rai, Outlined, Ol Onal — are digits to
`int()` and absent from Go's table entirely.

`internal/pytext` has carried exactly those seven ranges, and a sweep
that re-derives them from the interpreter, since the `\w` supplement
work: `pytext.DecimalDigit` exists for `record`'s float() coercion and
`playbook`'s alarm dates. So the answer was not to fix the new table but
to DELETE it — `decimalValue` is four lines calling the helper, and the
two guards it had grown that no input could reach went with it (L14, and
the census is what made the ninth copy visible before it shipped).

### The battery moved into the repo, because one of them damaged the tree

The mutation battery for this chunk hit its runner's ten-minute cap
mid-row and was killed. Python does not run `finally` blocks for a
default-disposition signal, so the row it was on stayed on disk:
`if nWorkers < 0` where the file says `<= 0`. The next five runs of
`internal/loopparallel` deadlocked identically — an unbuffered semaphore
and three senders — and the first three explanations were all wrong,
including a `git status` that showed the package as untracked and
therefore showed nothing.

`gosrc.py` has carried `install_restore_handlers` since the week
`mutate-wraps` did the same thing to `provenance.go`. The batteries never
imported it, because each one was a throwaway script in a scratchpad
directory. That is L13 pointed at the tooling: a correct fix, at the one
site that had the evidence.

`go/tools/mutate-subs.py` is the class fix — one runner, gosrc's
signal-safe restore, a `--only` flag, a matched-count check that EXITS
rather than warns, and a per-mutation package set (a shared helper's
mutation has to be tested against every package that can see it, or it
reports SURVIVED because its killers were never compiled). The spec lives
in `go/tools/batteries/*.json`, so a later round can re-run what an
earlier round converged instead of re-deriving it. This chunk's is
`loopparallel.json`, 46 mutations across the schedulers and pyval's two
widened alphabets.

---

## Phase G: the terminal shape of a run, and two lanes that disagree about failure (2026-08-27)

`internal/loopfinalize` ports `_build_result_and_finalize` — the 420 lines
that turn a finished loop into the `LoopResult` its caller returns, write the
artifacts a human reads, and hand off to the learning tail. Three helpers of
`loop_finalize.py` were already ported into `internal/loop` by the
finalize-helpers tranche; this one takes the phase itself.

The tranche also moved `loop_finalize.py` out of the undeclared queue, and
finding out WHY it was in there is the first entry below.

### A module can be ported, named, and still counted as unported

`PORT_STATUS.md` listed `loop_finalize.py` at 1319 lines in the undeclared
queue. It was not unported: `internal/loop/finalize_helpers.go` opens with
"StepEvidence is loop_finalize._step_evidence" and `maintenance.go` names two
more of its functions. The scanner's rule is a word-boundary match on the
module's FILENAME, and `loop_finalize._step_evidence` does not contain
`loop_finalize.py`.

Measured across the whole tree: **nine** of the 106 undeclared modules are
named in a production comment as `<module>.<attr>` and nowhere as
`<module>.py` — `loop_finalize`, `ancestry`, `conductor`, `doctor`, `hooks`,
`memory_bridge`, `run_curation`, `security`, `skill_lifecycle`.

The obvious fix is to widen the regex, and it is wrong. A `<module>.<attr>`
mention is TWO-VALUED: `internal/loopparallel`'s package doc names
`security.scan_external_content` precisely to say it is NOT ported. Widening
would sweep that in as a declaration and make the denominator lie in the
optimistic direction, which is the worst one available. So the tool now
reports them as a third category — MENTIONED, neither declared nor absent —
and the number is in the file rather than the paragraph that used to describe
the blind spot in prose. The two genuine ones here were fixed at the source,
which is the convention: name the `.py` file in the package doc.

This is the same shape as the note already in that file's preamble: an
enumeration can be wrong at BIRTH. It was, twice, and the second time it was
wrong about a module the port had ported three chunks earlier.

### The exception discipline is not uniform, and that is the design

Almost every call in this phase is `try: … except Exception: log.debug(…)`.
Four are not: `_write_loop_log`, `o.append_decision`,
`o.write_operator_status`, and the `LoopResult` construction. An exception
from those escapes and the loop dies without finalizing.

That asymmetry is worth stating because the friendly port — wrap everything,
never fail a finalize — silently changes what a run MEANS. A `LoopResult`
whose `log_path` points at a file that was never written is a run that claims
a replayable record it does not have. So `BuildResultAndFinalize` returns
`(*LoopResult, error)`, and a nil `Deps` func on one of those four is an
error out rather than a log line.

Mutant `loop-log-failure-swallowed` is that port, and it is the one a
reasonable person writes first.

### Two merge-back lanes, one of which forgets to downgrade

The container-clone lane and the run-worktree lane do the same job forty
lines apart, and they disagree in two places. Both are Python-side findings,
BACKLOGged rather than fixed here — this is a port, and a port that
"improves" its source stops being a differential.

**The worktree lane's `except` does not downgrade.** The clone lane's
handler is explicit about why it must (2026-07-13 adversarial review, finding
A6): "a merge-back exception must NOT be reported as a clean 'done' — the
worker's clone work never reached the fence." It downgrades to `partial`,
names the retained clone and branch, and stamps `external-interrupt`. The
worktree lane's handler is one `log.warning` and nothing else. A `merge_back`
that RAISES leaves a run reporting `done` with its work stranded on a branch
the result never names.

**And a failed merge can lose its downgrade to a later exception.** The
worktree block runs `merge_back` → `cleanup` → `prune` → `if not merge.ok:
downgrade`. The downgrade is LAST. If `cleanup` or `prune` raises when the
merge already failed, the `except` swallows it and the downgrade never runs —
a failed merge reports `done`, through the guarded path rather than the
unguarded one. Scenario `worktree-prune-raises-after-failed-merge` pins that
behaviour, because the port has to reproduce it, and it is filed because the
fix (move the downgrade above the cleanup) belongs in Python.

The clone lane has the same shape and survives it: a `cleanup_clone` that
raises after a failed merge takes the `except` branch, which downgrades
anyway — with the exception's message instead of the merge's detail. So one
lane is wrong-but-safe and the other is wrong-and-silent, from the same
three-line pattern.

### `downgrade` sets the verdict and its evidence together, or neither

Both lanes end in the same shape, and the port factors it into one function
because the two copies had already drifted:

```go
if result.Status == "done" { result.Status = "partial" }
result.StuckReason = append-with-"; "
if result.StopVerdict == "" {
    result.StopVerdict = "external-interrupt"
    result.StopEvidence = budget.Clip(evidence, 800)
}
```

The `if` guards BOTH fields. Setting the evidence unconditionally is the
mutant a reader writes when they read the verdict line as the only thing
being protected — and it leaves a run whose verdict says `goal-met` next to
evidence describing a merge conflict. `downgrade-overwrites-verdict` is
killed by `clone-merge-fails-keeps-existing-verdict`.

The reason string is APPENDED with `"; "`, not assigned: a run that was
already stuck for its own reason keeps it, and the merge failure is a second
clause. `downgrade-reason-prepended` and `downgrade-separator` are both
killed by one scenario that arrives with a stuck reason already set.

### The transcript is a list of strings joined with a newline, and several of them end in one

`_partial_lines` is built as a list and joined with `"\n"`, and four of the
entries carry their own trailing `"\n"`. The blank lines in a rendered
`RESULT.md` are the join and the embedded newline together — nobody chose a
paragraph style. Reproducing the list element-for-element is the only way to
reproduce the spacing, which is why `RenderTranscript` builds a `[]string`
that looks redundant rather than a `strings.Builder` that would look tidy.

Three cuts in it, all code points:

- `s.text[:100]` in the step heading,
- `s.result[:2000]` in the body,
- and the `"... (truncated, %d chars total)"` figure, which is `len(s.result)`
  — also code points, so the number and the cut are in the same unit. A
  byte-counted port tells a reader that 4,000 characters were cut from a
  2,000-character result. `transcript-truncation-counts-bytes` and
  `head-counts-bytes` are both killed by one multibyte fixture, and by
  nothing else in the corpus.

The step heading numbers by POSITION and reports `s.index` separately as
`ledger #N`. Python's comment says why: `s.index` is the NEXT.md ledger line,
so rendering it as the step number read as "Step 11 of a 4-step plan".

And the gate is `if _done_steps:` — a DONE step existing, not the run being
done. A stuck run that finished two of five steps still writes `PARTIAL.md`.
Two mutants spell the plausible alternatives (`transcript-gate-is-any-step`,
`transcript-gate-is-status`) and a stuck-with-progress fixture kills both.

### The calibration row's `flag_kinds` is a set comprehension inside a sort

```python
"flag_kinds": {k: sum(1 for f in flags if f.kind == k)
               for k in sorted({f.kind for f in flags})}
```

Three things at once: duplicate kinds collapse in the set, the sort is over
the DISTINCT kinds, and the JSON object order follows that sort. So the Go
side builds an ordered `pyval.Obj` rather than a map — a Go map would render
in Go's own sorted order, which agrees here by accident and would stop
agreeing the moment Python's key order came from anything but `sorted`.

`flag_count` is `len(flags)`, not `len(flag_kinds)`. Two flags of one kind
count as two in the aggregate and as one key in the breakdown, which is the
point of having both.

### What the differential compares, and why it is a file tree

The probe drives the real `_build_result_and_finalize` with every stateful
surface replaced by a recorder, and both sides emit the same record: the
returned result, the ORDERED call log with arguments, the log lines at their
levels, stderr, the four context fields the phase is supposed to clear, and
**the file tree keyed by relative path**.

The file tree is there because a rendering compared in memory is a rendering
compared to itself. The probe writes into its own temp root and the Go test
into another, each through its own runtime's path joining and mkdir
semantics; the transcript, the scratchpad dump and the calibration JSONL are
compared as bytes that each runtime actually put on a disk. That is what
catches `scratchpad-dir-parents` — Python's scratchpad `mkdir` is
`exist_ok=True` with NO `parents=True`, so the two calls that look
interchangeable are not.

It does NOT catch `scratchpad-dir-parents`, and that claim was in this
section before the battery ran. The artifacts dir is guaranteed to exist by
the time the scratchpad dir is created — the transcript was just written into
it — so mkdir-one-level and mkdir-p agree on every reachable input. The
mutant is equivalent and the site now says so. Writing the survivor list
before running the battery is how a doc acquires a false claim about its own
tests.

60 scenarios. The one probe bug the run found was in the probe: its recorder
took the call name as a parameter called `name`, and one recorded call has a
keyword argument literally named `name`, so `write_event` raised
`TypeError` into the surrounding `except` and vanished from the Python
record entirely — as a MISSING CALL, which is exactly what a real port bug
would have looked like.


And the same hazard exists in the rule that was already there. The first
draft of `internal/loopfinalize`'s package doc listed what the tranche does
NOT take, naming `loop_report.py` and `pre_flight.py` — and both jumped into
the DECLARED column, carrying 3,168 lines of "progress" earned by a sentence
saying they were not ported. It showed up in the regenerated diff and was
fixed by dropping the suffix in that prose, with a comment at the site saying
why the suffix is absent. A package doc that names a `.py` file it is
declining to port declares it. No regex fixes that; only a positive
convention would, and inventing one now would un-declare 78 rows written
under the current one. It is recorded in the tool's preamble instead.

### The battery, and what a survivor was worth

69 mutations derived from the file. The first run read 64 killed, 5 survived,
and the five sorted into three kinds — which is roughly the ratio every
tranche in this arc has produced, and the reason the survivor list is read
rather than counted.

**Two were missing fixtures.** `calibration-stuck-is-not-done` rewrites
`loop_status == "stuck"` as `!= "done"`, and every scenario in the corpus was
one or the other. `transcript-stuck-gate-is-nil-check` rewrites Python's
`if stuck_reason:` as a nil check, which needs a stuck reason that is present
and EMPTY. Both are now fixtures, and both are the shape L9 is about: a
status field with three values tested at two of them.

**Two were equivalent, and the sites now say so.** Dropping the `!= ""`
clause from the outcome-stamp guard cannot be observed, because nothing in
this phase ever CLEARS a verdict — a verdict differing from the pre-merge
snapshot is already non-empty. Passing `parents=true` to the scratchpad mkdir
cannot be observed either, because the artifacts dir was just written into.
Both keep the defensive spelling Python has; the comment is what changes.

**One was a bad mutant.** `report-skipped-when-manifest-fails` inserted an
`if false` block, which cannot change an answer. Rewritten to actually skip
the report on a manifest failure, it dies to a fixture that was already
there.

### A guard from three chunks ago caught the new package on its own

`sort.Strings(kinds)` in the calibration row tripped
`internal/pypath`'s `TestNoNewByteWiseFilenameSortAppears`, which counts
`sort.Strings` calls per production file across the whole tree and fails on
any file not in its allowlist. The sort here is over pre-flight flag KINDS —
program constants, never a path — so the answer was one allowlist row with
its reason, which is what the guard is designed to collect.

Worth recording because it is the shape the 2026-08-27 decree asks for:
**lenses get CLOSED, not re-found.** That guard exists because byte-wise
filename ordering was found ALREADY SHIPPED in four packages after surviving
three review rounds. Nobody had to look for it here; a package written today
was audited by a check written for a bug found in a different subsystem, and
the cost of the false positive was one line and one sentence.

### Two things the second review pass moved

**`artDir == ""` was standing in for "artifact_dir failed."** Python's
fallback is the `except` branch and only that: an `artifact_dir` that
RETURNS an empty string takes the primary path, where Python goes on to use
`Path("")` — which is `.`, not an error. The port now carries an explicit
`resolved` flag, and `artdir-return-not-error` is the mutant for it.

**The scratchpad fixture was agreeing with itself.** The scenario spec held
the scratchpad as a Go `map[string]any`, which marshals SORTED; the Go side
then rebuilt it by sorting. Both sides would have agreed on `index.json` with
neither preserving anything, which is L6 exactly. It is an ordered list of
pairs now, with a four-key fixture in neither alphabetical nor reverse order,
and `scratchpad-keys-sorted` is the mutant that fixture kills.

One mutant is left in the file rather than the spec.
`artdir-return-not-error` adds `&& dir != ""` to the resolution above, and
nothing in the corpus makes `artifact_dir` answer `""`. Building the fixture
would mean making the PYTHON side write its transcript relative to the
process CWD — outside the test's temp root, into the checkout. A test that
can leave files in the repository is a worse trade than an unpinned line, so
the site carries the reason and the mutant is retired.

### And a P4 check that checked nothing

`pgrep -f mutate-subs`, run to answer "is the battery still running before I
edit this file?", matches the shell running the pgrep — the pattern is in
that shell's own command line. It answered STILL-RUNNING for a battery that
had already printed its summary. A process scan whose pattern is its own
argument is not a check. The runner now writes a completion marker and the
marker is what gets polled.

Final: **68/68 killed**, spec in `go/tools/batteries/loopfinalize.json`.

## Phase H: the CLI that would have been argparse's second copy (2026-08-27)

`agent_loop.py` is 900 lines, and 670 of them are `run_agent_loop` — the
loop's whole orchestration, reaching loop_execute, loop_post_step,
loop_blocked and the director. This tranche takes the two ends that are not
that: `main`, the `maro-run` command line, and `run_parallel_loops`, the
fan-out entry point. The loop itself is reached through a `RunFn`, so
everything around it can be measured now.

### The extraction, and why it happened before the port

`maro-introspect` was ported weeks ago, and its CLI carried ~650 lines of
argparse emulation: the two-pass parse, abbreviation on both dash forms, the
negative-number heuristic, `--` handling, the nargs patterns, conversion at
the point of consumption, and the error wording. Every one of those lines
had survived a review round.

`maro-run` is the second CLI. The choice was to copy those lines or to
extract them, and this port has a standing lesson about exactly that
(**L14**: a helper you did not look for is a helper you will write again —
the byte-wise filename sort was found ALREADY SHIPPED in four packages).
Copying argparse would have been the largest instance of L14 available:
a second copy starts every one of those review rounds over, and the two
copies then drift apart one bug fix at a time.

So `internal/pyargparse` came out of `internal/introspect/cli.go` first, as
a pure extraction — no behaviour change, ~600 lines deleted from the
consumer — and the proof that it was pure is that introspect's own CPython
differential (~84 argv cases against the real `argparse`) stayed green
across the move without a single fixture edit.

That is the part worth naming. An extraction is normally justified by
inspection: the code looks the same, so it is the same. Here it was
justified by the same measurement the original was built against. The
generalization is real rather than a rename because a second consumer with a
DIFFERENT parser spec — thirteen option rows instead of six, `nargs="+"` on
a required positional instead of `nargs="?"` on an optional one, two
`type=int` conversions and a `choices=` list — now runs through it and is
compared against CPython independently.

### What the second consumer found that the first could not

introspect's parser has one optional positional. `maro-run`'s has a required
one with `nargs="+"`, and that exposed an ordering the first consumer never
reached:

```
$ maro-run --bogus
maro-run: error: the following arguments are required: goal
```

Not `unrecognized arguments: --bogus`. argparse raises the required-actions
error from INSIDE `_parse_known_args`, while extras are reported by
`parse_args` afterwards — so the missing positional wins, and a user who
mistyped an option is told about a different problem entirely. The port now
runs the required check at the end of `parse()` and the extras check after
it, in that order, and the differential pins it.

Two more measurements, both version-dependent, both recorded at their sites
because a future red differential will look like a port bug and not be one:

- **`choices=` is printed UNQUOTED** on CPython 3.14.3:
  `invalid choice: 'bogus' (choose from auto, anthropic, openrouter, openai,
  subprocess, codex, xai)`. Through 3.11 the choices carried quotes.
- **`--project, -p PROJECT`** is the 3.13+ help rendering. Older argparse
  wrote `--project PROJECT, -p PROJECT`.

### The usage block is a measured constant, and it is 80 columns

Neither consumer generates its usage or help text. argparse's HelpFormatter
wraps both to `shutil.get_terminal_size().columns - 2`, which means the
output is a function of the terminal it ran in. Porting the wrapping
algorithm is a tranche of its own and buys nothing: the 80-column rendering
is what CPython produces whenever stdout is not a terminal — a pipe, a
redirect, a CI log, every scripted invocation — and what it falls back to
when the width cannot be determined.

So the constants are the 80-column text, the probe exports `COLUMNS=80`, and
the differential compares them byte for byte. Measured, not assumed: without
the export the comparison would silently become a property of whoever ran
the test.

### `--verbose` cannot do anything, and the port reproduces that

```python
verbose=args.verbose or True,
```

`args.verbose or True` is `True` for every input the parser can produce.
`store_true` gives `False` when absent, and `False or True` is `True`. The
`maro-run` CLI runs verbose unconditionally and the flag is decoration.

The port keeps it — `a.Verbose = true` immediately before the call — with
the flag still PARSED, because parsing is where a wrong `-v` is refused:
`--verbose=x` is still argparse's exit 2. A port that folded the `or True`
into the parser would have made `--verbose=x` legal, so the two halves are
tested separately (`TestVerboseIsAlwaysTrue`, `TestParseArgsKeepsVerboseAsGiven`)
rather than left to one field of one differential record.

The finding is filed in `BACKLOG.md` against the Python source. Fixing it
here would have been the port improving its source, which is how a
differential stops being one — and the fix is a product question anyway
(make the flag work and every existing quiet invocation starts talking, or
delete the flag and change nothing).

### `run_parallel_loops`: three behaviours, and only one of them is the result

```python
if not goals:
    return []
effective_workers = min(max_workers, len(goals))
with ThreadPoolExecutor(max_workers=effective_workers) as executor:
    futures = [executor.submit(run_agent_loop, goal, **kwargs) for goal in goals]
    results = [f.result() for f in futures]
```

1. **The empty-list guard runs BEFORE the pool is built.** So
   `max_workers=0` with no goals is fine, and `max_workers=0` with one goal
   is `ValueError: max_workers must be greater than 0` — raised by
   ThreadPoolExecutor's constructor, not by any check in this function. A
   port that validated `max_workers` up front would refuse a call CPython
   accepts.
2. **Results are read in SUBMISSION order**, so the first goal's exception
   propagates even when a later goal failed first — and `with` still joins
   every thread on the way out. A failure cancels nothing.
3. **`min(max_workers, len(goals))`** caps the pool at the work available.

The port is a FIFO channel with `effective` worker goroutines, not
`len(goals)` goroutines racing for a semaphore. Both agree on everything the
function RETURNS. They disagree on the order tasks START in: a semaphore
hands its slot to whichever goroutine the scheduler picks, where
ThreadPoolExecutor's work queue is first-in-first-out. Nothing this function
returns observes that — but `run` is a caller's function, and a caller whose
steps log their own start is entitled to the order Python gives them.

Which is why the differential instruments `run` on both sides and records
the start order and the peak in-flight count, not just the results. The
mutation battery carries both wrong shapes (`parallel-semaphore-not-queue`,
`parallel-unbounded`) and both die.

### Comparing a start order without comparing a race

Raw start order is not comparable. With `effective` workers, FIFO submission
fixes which goals are in flight TOGETHER, but not the order in which those
threads reach the recording lock — that is a race in CPython and in Go
alike, and a test that compared the raw list would be flaky in a way that
looks like a port bug.

So both sides' observed order is collapsed into waves of `effective` and
sorted within each wave, by the same function, before comparison. The
property survives — a semaphore over one goroutine per goal puts goal 5 in
wave 0 — and the race does not.

### `min(max_workers, len(goals))` is an equivalent mutant, and that is the
### interesting part

The cap has no observable effect through this function. Peak concurrency is
bounded by `len(goals)` whether or not the pool is capped, so `9` workers
for `2` goals still peaks at 2, and a negative `max_workers` is negative
either way. It is a resource decision, not a behavioural one, and the site
says so rather than carrying a mutation that would report SURVIVED forever.

### The two fixtures that were agreeing with themselves

Both were `omitempty` (**L6**, again):

- `mainRun{Summary: ""}` marshalled to nothing, the probe's
  `b.get("summary", "SUMMARY")` filled the default back in, and the
  "empty summary" scenario tested the non-empty one. The probe now reads
  `b["summary"]` with no default at all — a scenario always carries both
  fields, and a default is how an omitted fixture field silently agrees
  with the port's own default instead of testing it.
- `Goals: []string{}` and `MaxWorkers: 0` marshalled to nothing, and the
  probe raised `KeyError` — which at least failed loudly. The empty-goals
  scenarios, the only ones that reach the early return, were the ones that
  vanished.

### An inconsequential difference, noted rather than pinned

`--max-steps 9999999999999999999999999999999999999999` works in Python:
its ints are arbitrary precision. The port saturates at `math.MaxInt`.

That divergence is real and it does not matter — both are step counts no run
reaches — so it is written down here and NOT asserted by a test. What IS
asserted, in `internal/pyargparse`, is that the port does not invent a
refusal CPython never makes: a 40-digit `--max-steps` must parse, because a
port that answered `invalid int value` would have changed the CLI's
contract, not merely its precision. The differential's own fixture uses
`2**63-1` exactly, which both languages spell identically.

### One branch the differential cannot reach

`Main` returns exit 1 when the loop itself errors. Python does not catch
anything from `run_agent_loop`: the exception escapes to the interpreter,
which prints a traceback and exits 1. Reproducing that traceback is not this
code's job, so the differential stubs a loop that always returns, and the
status is pinned by a named Go test instead. Measured, not assumed:
`python3 -c "raise ValueError('x')"` exits 1. Getting this wrong would make
a crash indistinguishable from a typo — 2 is argparse's usage status.

### Closing L6 instead of finding it again

L6 — "a fixture that agrees with itself" — fired here twice in one file,
which by the standing rule (2026-08-27: *lenses get CLOSED, not re-found*)
makes it a check rather than another ledger row.

The mechanism is specific enough to check. A differential's scenario struct
is the WIRE FORMAT to a CPython probe: Go marshals it, Python reads it back.
`omitempty` deletes a field whose value is the Go zero, and the probe then
supplies its own default for the missing key. When that default is not the
Go zero, the fixture cannot express the zero AT ALL —

```go
Summary string `json:"summary,omitempty"`   // Go: ""
```
```python
b.get("summary", "SUMMARY")                 # Python: "SUMMARY"
```

— and a scenario named "empty summary" passes while testing the non-empty
one. The other half shipped in the same file: a field dropped by
`omitempty` and read as `sc["goals"]`, which raises `KeyError` for exactly
the empty-list scenarios that were the point. That one at least failed
loudly.

`internal/portguard` now walks every directory holding a `.py.tpl`, parses
the `_test.go` files for `omitempty` tags, and joins each key against how
the probe reads it. A subscript is a failure unless it is guarded by a
truthiness test on the same key (the standard
`if beh.get("x"): raise Boom(beh["x"])` shape, where the absent case is the
tested one); a `.get(key, D)` is a failure when `D` is not a literal that
round-trips to the Go zero.

The remedy is deliberately not an allowlist row. It is to fill the default
in the SCENARIO BUILDER — one place, on the Go side, where both languages
then read the same value — and delete it from the probe. A default that
exists in two languages is a divergence waiting for someone to edit one of
them.

Writing the check found three more instances immediately, all in
`loopfinalize`'s differential and all of the second kind: `monotonic`
defaulted to `12.5` in the probe AND to `12.5` in the Go mirror, `now` to
the same ISO string in both, `log_path` to `/logs/loop.json` in both. No
divergence was being masked — the two sides genuinely agreed — but no
scenario could ever say "monotonic returns zero", and nothing said so. The
defaults moved into the base scenario and both copies went away.

That is the argument for closing a lens rather than logging it: the check
found in one run what four review rounds had not looked for.

### The three survivors, and what each one turned out to be

`internal/pyargparse` got its own battery of 39 mutations, run against all
three packages that can see the change — the package itself, `introspect`,
and `agentloop`. Narrowing that list to the package under edit is how a
shared helper's mutation reports SURVIVED with its killers never run.

Four survived the first pass, and they were four different things:

- **`glued-tail-extras-lose-the-dash`** was a real gap. When a single-dash
  flag carries a tail that names no option, the tail joins the extras WITH
  the dash it was glued to: `maro-run ship -vx` is
  `unrecognized arguments: -x`, not `x`. Measured against CPython, fixtured,
  killed. `introspect`'s corpus could not reach it because its own glued
  tail is `-hx`, and the help action unwinds the parser before extras are
  ever reported.
- **`short-glued-cut-by-bytes`** is equivalent, and the reason is worth the
  comment it now carries: cutting `arg[:2]` by bytes differs from cutting
  by code points only when the token's second character is multi-byte, and
  the resulting prefix is then invalid UTF-8 — which can never equal an
  option string, because every option in this port's parsers is ASCII. The
  rune cut stays anyway: it is what argparse does, and the next parser to
  declare a non-ASCII option would silently get the wrong answer from the
  byte version.
- **`second-terminator-also-terminates`** was a bad mutant (**L8**): it adds
  `&& !terminated` to the `--` arm of a switch whose PRECEDING arm already
  matches on `terminated`. It cannot change an answer. Retired, with the
  reason at the site.
- **`empty-positional-counts-as-seen`** found a real wrong rule that no
  input can observe. The port marked a positional as seen only when its
  matched span was non-empty; argparse's `take_action` does
  `seen_actions.add(action)` before it looks at the values. No fixture
  distinguishes them — a required positional is `nargs not in ["?", "*"]`,
  and every other nargs needs at least one `A` to match at all — so a
  required positional can never be taken with an empty span. The port was
  changed to argparse's rule regardless. An unobservable difference is
  still a wrong rule for the next parser spec to trip over, and the
  mutation was retired because its target no longer exists.

Final: **34/34** on `agentloop`, **36/36** on `pyargparse` (39 written,
three retired above), and `loopfinalize`'s 68 re-run green after its
fixtures changed.

## Phase I: the two folds, and a path that invents ledger indices (2026-08-27)

`internal/loopparallel` had the SCHEDULERS since its first slice —
`_run_steps_parallel` and `_run_steps_dag`, the two functions that decide
what runs and in what grouping. This tranche takes the FOLDS:
`_run_parallel_batch` and `_run_parallel_path`, which decide what the run
MEANT. They turn a scheduler's outcome dicts into StepOutcomes, a
LoopResult, ledger writes and the lines an operator reads.

They were slice 2 because they reach loop_post_step, orch and metrics, and
none of those had a Go twin a fold could call. They still don't; each
arrives as a Deps func, and the boundary is a declaration rather than an
omission.

### The exception discipline is not uniform, and that is the fix from a
### previous review

Python wraps four calls in `try/except Exception` and leaves two bare. The
bare pair is the DECISION fan-out — `record_step_decisions` and
`record_step_world_facts` — added by a chunk-3 review finding, because the
parallel paths bypassed `_process_done_step` and silently dropped every
decision a parallel step made. Wrapping them again would undo that fix
quietly, which is exactly the failure it was written to stop.

So the port's Deps signatures encode it: a func whose error is swallowed to
a log line and a func whose error is RETURNED are different contracts, and
the differential drives both (`batch-decisions-failure-propagates` against
`batch-cost-failure-is-non-critical`). Four fixtures, four different
answers, from six calls that look alike in the source.

### Arithmetic a reader will assume is per-step, and is not

- `iteration += len(_batch_steps)` runs BEFORE the fold, so every outcome
  in a batch carries the same, final iteration number. Nothing counts up
  through the batch.
- `_b_elapsed` is measured from the BATCH's start for every member, so a
  four-step batch records four near-identical durations that are really the
  batch's own elapsed time. That is why each outcome passes `ended_ts=""`:
  it opts out of `step_from_decompose`'s "now" default so the report's
  timeline stays in its approximate mode instead of rendering fabricated
  precision (2026-07-08 review).
- On the fan-out path there is no per-worker timing AT ALL, so every
  outcome records `elapsed_ms=0`, and the same `ended_ts=""` sentinel keeps
  those from reading as real zero-duration steps.
- `stuck_reason` is ASSIGNED, not accumulated, so the LAST blocked step is
  the one the run reports. Earlier blocks survive only in the per-step
  outcomes.

### zip() truncates, and nothing says so

Both folds iterate `zip(step_texts, outcomes)`. A scheduler that returned
fewer outcomes than it was given steps leaves the tail unprocessed and
unreported — no error, no blocked outcome, the steps simply do not appear
in `step_outcomes`. Extra outcomes are dropped from the fold but still
counted in the batch's cost log line, because that sum iterates the raw
list. Both directions are fixtured.

### `float(x or 0.0)` is two operations and the order matters

```python
_provider_cost_delta += float(_batch_oc.get("provider_cost_usd", 0.0) or 0.0)
```

The `or` runs FIRST, so a falsy value — `None`, `0`, `""`, an empty list —
becomes `0.0` and never reaches the conversion. Only a TRUTHY non-number
raises, and it raises out of the whole fold: this line is not inside a try.
A numeric string converts fine. The port carries a local `pyFloat` rather
than the module's forgiving `FloatOf`, because a forgiving conversion here
turns a crash into a silently free step.

### The one that is worth a Python-side fix

`_run_parallel_batch` is careful about NEXT.md item indices: it threads
`batch_item_indices` from the caller, bounds-checks it, falls back to `-1`
for "not a project item", and gates `mark_item` on `>= 0`.

`_run_parallel_path`, twenty lines later, passes the ENUMERATION COUNTER as
the item index — 1, 2, 3 — whatever the project's ledger holds, and whether
or not the run has a project. It never calls `mark_item`, so nothing is
mis-marked; it is the outcome RECORD that carries the wrong reference, and
anything reading `StepOutcome.index` as a ledger id on this path is reading
step ordinals. Reproduced, pinned by `path-item-index-is-the-counter`, and
filed in BACKLOG.md rather than fixed here.

### `len(None)` is reachable, and only when you are watching

`levels` is typed `Optional[List[Any]]` and is read exactly once, inside the
DAG path's verbose banner. So a quiet run with `levels=None` finishes
normally and a verbose one dies with `TypeError: object of type 'NoneType'
has no len()`. The port keeps `Levels` a pointer so None stays
distinguishable from an empty list, and raises in the same place — both
arms fixtured (`path-dag-quiet-survives-none-levels`,
`path-dag-verbose-none-levels-raises`). Inventing a tolerance CPython does
not have would hide the crash until someone turned verbose on.

### A difference the type system will not let the differential carry

Both folds pass `oc.get("inject_steps", [])` RAW into `step_from_decompose`.
Python's field annotation is `List[str]` and annotations enforce nothing, so
a step body that answered a string or a list with a number in it puts that
object verbatim into the outcome. Go's field is `[]string` and cannot.

The INJECTION half — `isinstance(..., list)` and the `str(s).strip()`
filter, which is the half that changes the plan — is pinned by a Go test
that drives both malformed shapes. The outcome-row divergence is filed in
BACKLOG.md with the two ways out, rather than papered over by a normaliser
in the differential: a comparison that smooths a difference has moved the
assertion into the smoother (**L51**).

### One helper, not two

Comparing StepOutcomes against `dataclasses.asdict` needs the 24-field
mapping, and `internal/looptypes`' own differential already had it as a
private test helper. Copying it here is how two differentials start
disagreeing about which fields exist (**L14**), so it moved onto the type as
`StepOutcome.AsDict()` and both callers use it. A field added to the struct
and forgotten there now shows up as a length mismatch in every differential
that compares against asdict.

### The three survivors

- **`mark-gate-strictly-positive`** (`itemIdx >= 0` → `> 0`) and
  **`path-status-defaults-done`** were both real fixture gaps: no scenario
  used item index exactly 0 — the FIRST row of NEXT.md — and every fan-out
  outcome set its status explicitly, so the `"blocked"` default on that
  path was never taken. Two fixtures, both killed.
- **`empty-item-indices-are-indices`** was the interesting one. The port
  spelled Python's guard out as
  `len(in.BatchItemIndices) > 0 && bi < len(in.BatchItemIndices)`, and the
  mutation that deletes the first term cannot change an answer: in Go the
  bounds check SUBSUMES the truthiness test, because a nil or empty slice
  has length 0 and no index is below it. The redundant term was removed
  rather than allowlisted — a clause a reader has to re-derive as dead is
  worse than the Python idiom it was transcribing.

Final: **51/51 killed**, spec in
`go/tools/batteries/loopparallel-folds.json`.

## Phase J: the risk mint, and an errno spelled six times (2026-08-27)

`loop_finalize.py`'s second slice is the self-contained half: the run-risk
mint, the two lesson-text helpers, and the post-notify maintenance
registry. `internal/loopfinalize/riskmint.go`.

`_mint_run_risks_to_project` is Jeremy's 2026-08-10 ruling — risks and
unknowns a run discovered belong in the project's RISKS.md, feeding the
"RISKS.md as reviewer input" direction. Mechanical v1, no LLM, two
sources: the run's persisted closure-verdict gaps and the scope-parse
failure sentinel.

### Every interesting rule in it is a rule about malformed input

`gaps` comes out of an LLM-fed closure verdict, and the function iterates
whatever it was handed. A list yields its elements. A **string yields its
characters**, so `"gaps": "abc"` mints three risk lines reading `a`, `b`
and `c`. A dict yields its **keys**. An int raises `'int' object is not
iterable` into the outer `except`, which turns it into a warning and a
zero — and `5` and `5.0` name different classes in that message, which is
why `pyTypeName` reads the `json.Number` token's own spelling instead of
its decoded value.

The `or []` in front of the loop is what separates the two halves: it runs
BEFORE the iteration, so a FALSY non-iterable (`0`, `false`, `""`, `{}`)
becomes an empty list and mints nothing, while a truthy one raises. Both
groups are fixtured, because the mutation that drops the coercion is
invisible without the falsy half.

None of these shapes exist until JSON has been through `json.loads`, which
is why this tranche is a differential over a **seeded workspace on disk**
rather than a unit test holding a tidy dict. Both runtimes seed their own
workspace from one declarative spec, run their own resolve/append path,
and the RISKS.md each one wrote is read back and compared. The only value
normalised away is the wall-clock stamp `append_section_lines` writes, and
the only path normalised is the workspace root — the `runs/<dir>/build/<file>`
tail of every OSError message is still compared, because that tail is what
the message is FOR.

### The cap is on risk lines, and the omission note is not one

`_gap_slots = 3 - (1 if _sentinel else 0)`, and the note that says how many
gaps were dropped is appended AFTER the cap. So a run with a scope failure
and four gaps mints two gaps, the sentinel and the note — four lines, under
a constant called `RiskLinesCap = 3`. That is Python's behaviour, it is
deliberate (the note is an annotation on the selection, not a risk), and
`TestOmissionNoteRidesOutsideTheCap` pins it so the next reader meets it as
a documented shape rather than as a suspected off-by-one.

The ordering is the round-15 review's fix and it is load-bearing: the note
is computed from what SURVIVED the cap. A note rendered before the cap got
capped along with the gaps and became a lie — "three below" printed above
two survivors, with a middle gap silently dropped.

### `[Errno 21] Is a directory:` had been written by hand five times

The warning this function logs is `str(exc)`, and for a `closure_verdicts
.jsonl` that is somehow a directory CPython spells that
`[Errno 21] Is a directory: '<path>'` where Go spells it
`open <path>: is a directory`. The differential caught it immediately.

The fix is `internal/pyos`, and it is a **closure, not a sixth copy**.
`dispatch/envelope.go`, `sheriff/slice2.go`, `syshealth/syshealth.go`,
`worktree/gitrun.go` and `worktree/fsops.go` each already carried a
hand-written `[Errno 13] Permission denied:` or `[Errno 21] Is a
directory:` literal for the one or two errnos that site happened to hit —
the exact shape of the 2026-08-27 decree that a lens firing twice on a
surface should be CLOSED. The sixth site built the helper.

The strerror table is **not retyped**. Go's linux errno strings come from
the same glibc catalogue CPython's do, lowercased — `ENOENT` is "no such
file or directory" here and "No such file or directory" there — so the
message is Go's own errno text with its first rune upper-cased, and
`pyos_test.go` asks CPython for the answer for 31 errnos and compares. A
hand-copied table would have been a second place for the strings to be
wrong; the measured rule held for all 31, and the one thing it did NOT
predict was the CLASS mapping: `ESPIPE` looks like a broken pipe and is a
plain `OSError`. Measured, not assumed.

The remaining five sites are not retrofitted here (a battery run owns the
tree, P4); that is a BACKLOG item.

### `pyval.ReadText` exists and is deliberately not used

It is the same function — strict UTF-8, universal newlines — and it wraps
the decode failure as `"<path>: <codec message>"`. The warning this feeds
is `str(exc)`, which carries no path. So `readTextPy` is spelled out here
with a comment saying which half of `ReadText` it declines and why, rather
than either silently diverging or quietly "improving" the shared helper for
one caller.

### Two equivalent mutants, answered at the site

- **`non-object-rows-accepted`.** `isinstance(row, dict)` is load-bearing
  in Python — deleting it makes `row.get` an AttributeError on the list or
  number a JSONL line is allowed to be. In Go a failed type assertion
  gives a ZERO `pyval.Obj`, whose `Get` misses every key, so a non-object
  row falls out of the loop-id match one line down instead. The guard
  stays as the readable form of Python's rule.
- **`precheck-is-equality-not-containment`.** `AppendRisk` does the
  identical `strings.Contains(existing, token)` inside the lock, so
  weakening the pre-check's comparison changes no answer. What IS
  observable is that the branch READS the file at all: a RISKS.md that is
  not UTF-8 raises here and warns, where the in-lock check would have
  appended to it. So the branch is pinned (`precheck-skipped`) and its
  predicate is not, which is the honest split.

Two more are marked EQUIVALENT MUTANT in the source without a battery row:
the blank-line skip (a whitespace-only line is not valid JSON and would
`continue` from the decoder one line down anyway), and `TranslateNewlines`
(both readers of this text already treat CRLF the way translation would —
for THIS caller, which is why the call stays).

### One survivor by construction, and it is Python's too

`if not append_risk(...): return 0` — the "a race-losing finalizer's
in-lock dedupe is a no-op" branch — is unreachable from any differential,
because reaching it needs a concurrent writer between the pre-check and
the lock. No mutation is written for it. It is recorded here rather than
allowlisted silently.

### A build failure is not a kill

Three rows in the first battery run were reported in the `killed` column
with `(build)` next to them: the deletion had orphaned an identifier, Go
refused to compile, and no assertion ever ran. `mutate-subs.py` printed
that and exited 0.

L8 has said since the metrics battery that a BUILDFAIL is a fault in the
battery and not a survivor, and the runner was quietly contradicting it.
It now reports `!BUILD`, lists them in the summary, and exits 1 — so a
battery cannot claim coverage it never measured. The three rows were
re-spelled to compile (keeping the orphaned identifier) and all three are
killed by tests.

**Turning it on found eight more, in batteries already declared
converged**: three in `pyargparse`, one in `agentloop`, four in
`loopfinalize`. Every one had orphaned an identifier or an argument;
every one is now killed by a named test. That is the cost of a runner
that reports its own gaps in the pass column.

The same re-run surfaced a **flaky killer**.
`parallel-semaphore-not-queue` was killed on three runs and survived a
fourth: the two spellings disagree only about which goal takes a freed
worker slot, and the differential normalises start order into waves
precisely because raw start order races in BOTH languages. What survives
that normaliser is a probabilistic remnant, so the kill was evidence
about the scheduler that day, not about the code (L51). The row is gone
and the reasoning is at the site; the FIFO spelling stays because it is
the faithful one — `ThreadPoolExecutor`'s work queue is first-in
first-out — not because a test caught the other.

Final: **59/59 killed**, spec in
`go/tools/batteries/loopfinalize-riskmint.json`; 62 differential scenarios
across the mint, the two lesson texts and the registry.

## Closing `clipRunes`, which was seven functions and two behaviours

`pytext.Head(s, n)` is Python's `s[:n]` — a plain slice by code points,
nothing appended.

It was written seven times as a private `clipRunes`: graduation, persona,
scans, playbook, evolver, skills, director. Six were the same function in
four spellings. **The seventh was a different function under the same
name** — playbook's appends `…`, because `playbook.py` has both forms
within a few lines of each other and the port kept them adjacent.

That is the fourth lens with a knife in it. A reader who copies
`clipRunes` from the wrong package gets an ellipsis Python never wrote,
into a lesson row or a manifest field, and nothing at the call site says
which one they got. The six plain copies are deleted, playbook's is
renamed `clipEllipsis`, and `Head`'s doc says explicitly what it is NOT:
`budget.Clip` is a BUDGET and appends `… [truncated: first N of M
characters]` to be SEEN, where `Head` is a slice and is meant to be
invisible.

`Head` also takes Python's negative `n`, which no caller in this tree
uses — so `head_test.go` asks CPython for 28 `(s, n)` pairs and compares,
including every negative arm. An unexercised branch in a helper seven
packages share is a liability, not generality.

## Phase K: crystallisation, and an adapter that is falsy on one call only (2026-08-27)

`_crystallize_and_synthesize` — skill crystallisation plus the Phase-32
no-matching-skill synthesis. `internal/loopfinalize/crystallize.go`.

It returns nothing and raises nothing, so a test that checked its return
value would pass against a body that had been deleted. Everything it does
is a CALL, so the differential records the calls in order with their
arguments, plus the warnings and the stderr lines, and compares those. 43
scenarios; battery 41/41 first run, no survivors.

### The asymmetry worth the whole tranche

```python
extracted = extract_skills([outcome_for_extraction], adapter if adapter else None)
...
synthesize_skill(goal=goal, ..., adapter=adapter, verbose=verbose)
```

The extraction call coerces a falsy adapter to `None`. The synthesis call,
twenty lines down, passes it raw. With a plain object both spellings agree
and the difference is invisible; with an adapter whose `__bool__` says no
— an empty client, a disabled backend — it reaches the synthesiser and
does not reach the extractor, and only one of the two gets to decide what
to do about that.

`adapter-falsy-splits-the-two-calls` is the fixture, and it is the only
one of the three adapter fixtures that can tell `adapterOrNone` from
identity. On the Go side the falsy stand-in has to be an empty
`pyval.List` rather than a named type with `[]any` underneath: pyval's
truthiness switches on EXACT types, so a named type reads as truthy and
the fixture would have quietly tested the truthy path.

### Two rules a reader would call bugs, reproduced

- `existing_skills` is computed ONCE, before extraction, and is not
  updated as skills are saved. Two extracted skills with the same name are
  both saved. The dedupe is against the library on disk, and a second save
  of the same name is a question for `save_skill`, not for this loop.
- A `save_skill` that raises aborts the REST of the extracted skills: the
  `try` wraps the whole loop, not each iteration. So one bad skill costs
  every skill after it in the list, and the warning says only that
  extraction failed.

Both are pinned (`duplicate-extracted-name-saves-twice`,
`save-raises-aborts-the-rest`) rather than fixed. A port that improves its
source stops being a differential.

### Two try blocks, and the second is not inside the first

An extraction that blew up does NOT skip synthesis, and the two warnings
have different texts. `extraction-failure-does-not-skip-synthesis` is the
fixture; without it, nesting the two would pass every other scenario.

### The import failure is fixture-chosen text, and that is stated

`from skills import ...` failing is an environment condition, and CPython's
message for it names the module and the path it searched. The probe
installs a module whose every attribute access raises
`ImportError("no module")` and the Go side's nil-dep guard reports the
same string, so the differential tests the HANDLING and not the message.
Pinning CPython's real text here would be pinning the probe's own
mechanism.

### The L6 guard was scoped to the wrong thing

Adding a third probe to `internal/loopfinalize` made `portguard` fail on
two fields that no probe reading them could ever see: it concatenated
every `.py.tpl` in a directory, so an `omitempty` field in one spec struct
was answerable by a subscript in an unrelated probe.

The fix is not an allowlist row. A `_test.go` is now checked against the
probes it NAMES, with the whole directory as a fallback for a test that
builds its probe path some other way — and the narrowing was verified the
way L9 demands: an `omitempty` was put back on a field the crystallize
probe really does subscript, the guard fired on it, and it was removed
again. A check whose blast radius is the directory reports findings the
code does not have, which is the fastest way to teach a reader to
allowlist it (**L53**).

---

## Phase K: eight try blocks, five log levels (2026-08-27)

`_finalize_loop` (`src/loop_finalize.py:595-933`) — every post-loop side
effect in the order Python runs them. Ported to
`go/internal/loopfinalize/finalize.go`, with `budget.StoreEntryCap` /
`budget.StoreTotalBudget` added for the two store-grade budgets.

The function returns nothing and raises nothing. Everything it does is a
CALL or a LOG LINE, so the differential records both, in order, with
arguments — and the **log level is part of the record**, because the
levels are the spec.

That is not a stylistic claim. One line of this function is a monument:
`_diagnose(loop_id, project=project or "")` reads the local param, and it
used to read `ctx.project` — a NameError that the outer `except` swallowed
on **every run for six weeks** (2026-04-26 → session 40). The whole
introspection block was dead and nothing said so, because that handler is
`log.debug`. A port that got the levels right-but-different would hide the
same class of bug in the same way, so the differential drives all eight
phases into failure and asserts what comes out.

### What stays real

`_step_evidence` and `_crystallize_and_synthesize` are NOT stubbed. The Go
side wires `StepEvidence`/`StepEvidenceBounded` to the already-ported
`internal/loop` helpers (converting `looptypes.StepOutcome` →
`loop.StepOutcome` across the package seam) and hands
`CrystallizeAndSynthesize` the real deps. Three ported pieces, and the
seams between them are covered rather than stubbed away.

### Dropping ONE name, not one module

The interesting failures here are import failures, and Python bundles
names per statement: `from memory import reflect_and_record,
record_step_trace` is one import, so losing `record_step_trace` fails the
whole Reflexion phase with the WARNING — no row written at all — and not
the debug line its own call site would have produced.

A probe that can only delete whole modules cannot show that, which makes
the port's guard composition unfalsifiable. So the probe grew a
`PartialModule` and the spec grew `drop_names`, and there is a fixture per
importable name (21 of them). Dropping the seventh introspect name means
`diagnose_loop` is never reached at all.

`PartialModule.__getattr__` has to raise **AttributeError for dunders**
and ImportError for everything else: `from X import y` runs
`_handle_fromlist` first, which probes `__path__` through `hasattr`, and
`hasattr` only swallows AttributeError. Raising ImportError there failed
every import from the module instead of the dropped name — the first
version of this probe reported 40 scenarios' worth of phantom
divergences.

### Three real divergences, all found by fixtures nothing else would have written

1. **A grounding query for a lesson that was never going to be written.**
   Python imports `record_tiered_lesson` at the TOP of the recovery-plan
   try; `_grounding_for` runs below it, as an argument. Go built the
   `TieredLesson` literal — grounding call and all — and checked the nil
   dep afterwards. With `memory` missing, Go queried `mint_grounding` and
   Python did not. Fixed by hoisting the guard into
   `recoveryPlanLesson`, and pinned by
   `recovery-plan-grounds-before-the-import-guard`.
2. **`grounding=None` normalised to `[]`.** `ground_lessons_for_run(...)[0]`
   is the LAST thing Python does; whatever sits at index 0 goes into the
   row unexamined. The port had an `if out[0] == nil { return empty }`
   that no Python line corresponds to. Removed — reproducing beats
   improving, and `grounding=None` vs `grounding=[]` is a difference the
   recorded call can see.
3. **A missing `captains_log` ran maintenance that Python skips.**
   `from captains_log import loop_id_scope` is inside the deferred
   closure, so it fails at DRAIN time and fails the drain — the
   maintenance does not run and the drain's own warning is the only
   report. Go treated a nil scope as "carry on unscoped".

### The probe bug wearing a divergence costume

Two crystallize scenarios failed with a warning nobody wrote:
`rec() got multiple values for argument 'name'`. `save_skill` records a
kwarg literally called `name`, and the recorder's first parameter was
also `name` — a TypeError raised inside the recorder, caught by the
crystallize handler, reported as a skill-extraction failure. The
recorder is positional-only now (`def rec(_call, /, **kw)`).

The same shape bit the maintenance registry: `real_defer =
lf.defer_maintenance_post_notify` read INSIDE the scenario loop chained
each scenario's wrapper onto the previous one's, and the previous one's
recorder writes into a call list this process has not printed yet.
Scenario 1's record grew rows from scenario 40. The original is captured
once at import now, and `_POST_NOTIFY_MAINTENANCE` is cleared per
scenario to match Go's fresh-registry-per-record.

**Both are the same lesson: a probe's own machinery can fail in a way the
code under test would fail, and the differential cannot tell them apart.**
The tell in each case was that the failure appeared in scenarios whose
fixtures had nothing to do with the phase that reported it.

### The battery: 152 mutations, and nine of them were fixture gaps

Derived from the FILE (L9): every guard, every log level, every argument,
every clip constant, and the ORDER of the eight phases. First run:
141 killed, **9 survived, 3 did not compile**.

Every one of the nine was a hole the fixture set had, not a hole in the
port — and seven of them existed because the scenario builder's own
DEFAULTS had closed the input space:

- `add()` fills a blank `LoopID` with a real id, so no fixture could reach
  `if not loop_id` in either `_grounding_for` or the evidence-sources
  expression. Two survivors. (The fixture named
  `grounding-skipped-without-loop-id` used `" "` to dodge the default —
  a name that claimed coverage it did not have, **L44**.)
- `add()` fills a nil `FailureChain` with `[]string{}`, so
  `failure_chain or []` was never exercised.
- Every recovery-plan fixture was a `"done"` run, where `Outcome:
  a.LoopStatus` and `Outcome: "done"` are the same string.
- The dry-run fixture was a `"restart"`, so the verified-recovery block's
  `dry_run` test was masked by the status test after it.
- `DeferLearning` was only ever set on a `"done"` run.
- Every drain fixture had `verbose` false, where "captured by value" and
  "hardcoded false" are the same call.

Two spec flags (`no_loop_id`, `nil_failure_chain`) and six fixtures later,
all nine are killed. **A default that fills a field is a default that
deletes a test**, and the battery is the only thing that says so.

Two of the nine were not fixture gaps:

- `verified-recovery-outcome-is-the-loop-status` is an **equivalent
  mutant** — the guard four lines up returns unless the status is
  `"done"`. The row was deleted and the reasoning is a comment at the
  site.
- `defer-in-process-guard-dropped` has no CPython counterpart at all:
  `defer_maintenance_post_notify` is a module-level function, not an
  import, so "this name is missing" is not a state Python can be in. The
  Go Deps struct permits nil regardless, and the ladder must fall through
  rather than count it as deferred — so it is a native Go test
  (`TestNilDeferMaintenanceFallsBackInline`), not a fixture.

The three build failures were the usual shape (**P14**): a deletion
orphaning the identifier that made the file compile. `budget` became an
unused import the moment the store-grade call was replaced, so that row is
now a SWAP of the two budgets between the two evidence strings — which is
the more interesting mutation anyway.

## Phase K: four blocks swallow ImportError and one does not (2026-08-27)

`run_post_run_maintenance` (`src/loop_finalize.py:934-1048`) — the run's
maintenance tail, extracted from `_finalize_loop` by the async-tail decree
so the closure lane can defer it past the `run_completed` notify. Ported to
`go/internal/loopfinalize/maintenance.go`. 58 scenarios; battery 51/51.

Five blocks, run unconditionally in order: skill maintenance, health
probes, the statistical scans, the evolver's run-cadence meta-cycle, the
inspector's. Every one swallows its own failures, so the function's
RETURN says nothing about which of them ran. The record is the calls and
the log lines, and the levels are again the specification.

### The asymmetry the tranche is named for

Four of the five carry a bare `except ImportError: pass` above their
general handler. The health-probe block does not:

```python
try:
    from system_health import run_health_probes
    run_health_probes()
except Exception as _health_exc:
    log.debug("health probes failed (non-critical): %s", _health_exc)
```

So a missing `system_health` writes a debug line and a missing `evolver`
writes nothing at all. That is one line of difference across five blocks
that otherwise read identically, and a port that unified them would pass
every test that did not distinguish an ImportError from any other error.
The fixture spec therefore carries the raised class, not just a message
(`finErr` is `[class, message]`), and `asErr` turns `ImportError` into the
port's own `errImport` sentinel — because a nil dep IS that state, and the
handlers must not be able to tell the two apart.

### An import that costs nothing when the branch is not taken

```python
if evolver_cadence_tick(_cadence):
    from evolver import run_evolver
```

`run_evolver` is imported INSIDE the `if`. A box with no evolver module
and a cadence that does not fire is indistinguishable from a healthy one;
the same box with a cadence that DOES fire silently skips the cycle,
because the `except ImportError: pass` at the bottom of that block catches
an import that happened halfway through it. Go models it as a nil
`RunEvolver` checked at the same point rather than at the top of
`evolverCycle`, and `run-evolver-guard-hoisted-above-the-tick` is the
mutation that says so.

### A literal 50 next to an imported constant

```python
_limit = DEEP_PASS_LIMIT if _mode == "deep" else 50
```

The deep limit is imported from `inspector`; the standard one is a bare
literal at the call site. The port keeps both shapes — `DeepPassLimit *int`
is a dep (a pointer, so "the module is missing" and "the module says 0"
stay distinct) and `InspectorStandardPassLimit = 50` is a const — because
collapsing them into one constant would make an inspector module that
disagrees about its own deep limit unobservable.

### `or 0` on two different defaults

```python
_insp_cadence = int(_cfg_get("inspector.run_cadence", 0) or 0)
_deep_every   = int(_cfg_get("inspector.deep_every", 5) or 0)
```

The `or 0` is the same on both lines and it means something different on
each. On the first it is a no-op. On the second it turns an EXPLICIT zero
into a zero and an ABSENT key into 5 — so a config that disables the deep
rider and a config that never mentioned it end up two lines apart. `cfgInt`
reproduces it and the fixtures pin both readings.

The short-circuit above the tick is also load-bearing and also from a
review: `cadence <= 0` returns "none" WITHOUT calling
`inspector_cadence_tick`, because counting while disabled created the state
file on fresh installs and made a later enable fire immediately instead of
waiting N enabled runs.

### The battery, and the one row that was deleted

51 mutations derived from the file, all killed. Two were the usual **P14**
build failures — a deletion orphaning the identifier that kept the file
compiling — and both were re-spelled into the more interesting mutation
underneath (`scan-log-fires-before-the-save` became a straight reorder;
`inspector-log-counts-the-limit` became a swap of limit and sessions).

One survivor was a genuine **equivalent mutant**:
`skill-maintenance-nil-dep-is-not-an-import-error` replaced that block's
`errImport` sentinel with a NIL error, and both spellings are silent —
because that block HAS an ImportError arm, and a nil error returns at the
same test. The same substitution in `healthProbes` is NOT equivalent, and
the battery kills it there.

The row was deleted at the time. It is back, marked `equivalent` (see the
deferred tranche below), and restoring it caught a real mistake in the
first minute: it was re-derived from the site comment as a plain non-nil
error, which is a DIFFERENT mutation — `isImportErr` recognises the
sentinel and the PyErr class and nothing else, so the block logs. The
runner reported `STALE EQUIVALENCE NOTE`, the row was corrected, and the
plain-error spelling is now its own killed row. **A comment is not a
battery row, and reconstructing one from the other is exactly the decay
L28 names.**

### An ops finding, from the battery that died after one mutation

A `nohup … &` child launched from a Bash tool call shares its parent's
process group, so killing the waiting task kills the battery. The first
maintenance run died after mutation 1 — cleanly, because the runner's
signal-safe restore handlers put the tree back, which is the only reason
this is a footnote instead of a section. Batteries are launched with
`setsid nohup … </dev/null &` now.

## Phase K: four halves, three returns, and two gates that fail open (2026-08-27)

`finalize_deferred_learning` (`src/loop_finalize.py:1201-1319`) — the
learning `_finalize_loop` deferred, run once closure has stamped a verdict
onto the outcomes row. Ported to `go/internal/loopfinalize/deferred.go`.
47 scenarios; battery 74 rows, 71 killed and 3 documented
equivalents.

The whole point of the function is data-r2-01: **lessons and skills must
not be extracted verdict-blind.** So its body is four halves separated by
three returns, and which half a given loop reaches is the specification:

1. deferred lessons, one call per loop id, extras first and this loop last;
2. the risk mint;
3. per-step lessons, but only for a row the learnability gate REFUSES;
4. crystallisation, behind two verdict gates.

It returns nothing and never raises. Everything observable is a call or a
log line, so the differential records both, in order, with arguments.

### The skip test sits BETWEEN the halves

```python
for _lid in [*(extra_loop_ids or []), loop_id]:
    if _lid and _lid not in _skip: ...
...
if loop_id in _skip:
    return
```

A loop whose verdict audit is unresolved is filtered out of the lesson
loop AND returns before everything below — but its EXTRAS still had their
lessons extracted on the way past. Move the return one line up and every
fixture without extras still passes.

`unstamped_loop_ids` is a compatibility alias unioned with `skip_loop_ids`,
and the union is two separate loops in the port for the same reason it is
two separate names in the source: `skip-ids-are-not-unioned` and
`unstamped-ids-are-not-unioned` are different mutations.

### `is True` / `is False` are identity, and the row comes from JSON

Both verdict gates spell the test as identity against the singletons:

```python
if _row is not None and _row.goal_achieved is False:
```

A ledger row rehydrated from JSON can carry a `0` or a `1` where a bool
was meant, and neither gate fires on those. `isTrue`/`isFalse` are the
type assertion, not truthiness, and the two fixtures that say so
(`numeric-zero-is-not-the-False-singleton`,
`numeric-one-is-not-the-True-singleton`) are the only ones that can tell
the spellings apart.

### What stays real

`verdict_trust`, `VERDICT_TRUST_FULL` and `is_learnable_outcome` are the
real implementations on BOTH sides — `record.VerdictTrust` and
`outcomepolicy.IsLearnable` in Go, the real modules in the probe. They are
package calls in the port rather than injectable deps, so an absence
fixture would be testing the probe's reach; instead the seam is covered by
driving them with rows that reach every branch (audit-incomplete, a
declared-but-unknown `success_class`, an out-of-budget stop verdict with
no verdict, directional confidence, `closure_unverifiable`).

`_crystallize_and_synthesize` stays real too, wired to the already-ported
`CrystallizeAndSynthesize`. Only `load_outcome_by_loop_id` (not yet
ported — it belongs to the memory_ledger tranche) and the two `memory`
functions are stubbed.

### The row has to be a real dataclass, and its FIELD SET is the fixture

```python
_row = load_outcome_by_loop_id(loop_id)
if _row is not None and not is_learnable_outcome(_dc.asdict(_row)):
```

`asdict` rejects anything that is not a dataclass, so the probe builds the
row with `dataclasses.make_dataclass` from the fixture's own key list. That
is not a convenience: `"success_class" in outcome` inside
`is_learnable_outcome` asks whether the field was DECLARED, so a row that
omits it is a different row, not the same row with a `None` in it. The Go
side builds a `pyval.Obj` from the same ordered key list.

It is also what makes **L60** testable: a field can be genuinely ABSENT
rather than None, which is the input the two fail-open divergences below
need (`docs/REVIEW_PATTERNS.md`).

### Two real divergences, both about a value read as an ARGUMENT

Reproducing them cost two `if !ok { return true }` arms that read like
defensive noise:

```python
if _row is not None and _row.goal_achieved is False:
    log.info("... loop %s judged not-achieved (%s)",
             loop_id, _row.goal_verdict_source)
    return
...
except Exception:
    pass  # fail open to pre-fix behavior: done is enough when unreadable
```

`_row.goal_verdict_source` is an argument to `log.info`. A row that never
declares it raises **before the log is entered**, and the blanket handler
swallows the log line and the `return` together — so a gate that had
decided to BLOCK crystallisation allows it, silently, over a field the
gate does not test. The same holds at the trust gate. The port logged and
returned false in both places.

That is the second instance of an argument expression acting as control
flow inside a swallowing block (the first was the recovery-plan grounding
query in `_finalize_loop`, one file over), so it is now **L60**.

### The `or ""` chain, twice

`project or getattr(loop_result, "project", "") or ""` appears at the mint
and again at the crystallisation call. The port computes it once and the
mutation `crystallize-project-ignores-the-keyword` is what says the second
site really is the same expression and not `loop_result.project`.

### Three equivalent survivors, and a battery field instead of three deletions

All three survivors were the same shape: a mutation substituting a value
that a guard ABOVE the site has already pinned.

- `LoopStatus: "done"` under a gate that returns unless the status is
  `"done"`.
- `dry_run` passed to `extract_step_lessons` under a caller that returns
  when `dry_run`. **Python passes it anyway**, and so does the port.
- `isTrue`'s `ok &&` half, under a first gate that consumes every bool
  False on both of its arms.

That is the fourth instance of this shape across three tranches, and each
previous one was answered by deleting the battery row and writing a
comment. So the runner grew an `"equivalent": "<why>"` field instead: the
row stays, prints as `equiv`, does not fail the run — and FAILS it with
`STALE EQUIVALENCE NOTE` if a fixture ever kills it. The rows deleted from
the finalize and maintenance batteries are restored under it. **L8** now
records this as its third closed sub-shape.

The two build failures were **P14** again, both from a deletion orphaning
an identifier (`learnable`, `source`), and both re-spelled to keep it.

## Phase L: the fence, and a function with no seam (2026-08-27)

`src/agent_loop.py:245-430` is the first chunk of `run_agent_loop`: the
**execution fence**. It binds the run-scoped subprocess cwd, resets three
pieces of container state, optionally provisions a scratch clone so a
self-dev run never mounts the live repo rw, computes the container's
writable-root set, and — if any of that raises — refuses the loop before
decomposition, unwinding everything it touched.

Ported to `go/internal/agentloop/fence.go` as `SetUpExecutionFence`,
which returns `nil` when the fence is up and a `*looptypes.LoopResult`
when the run is refused.

### The differential problem: there is nothing to call

Every previous tranche had a function to call. This one does not — the
fence is a hundred and eighty lines in the middle of `run_agent_loop`,
between `_initialize_loop` and `_decompose_goal`, reachable only by
running the loop. There is no seam, and adding one to the Python would
have been the port editing its own source of truth.

So the probe drives the **real** `run_agent_loop`, with three fakes:

- `al._initialize_loop` returns a duck-typed `Ctx` built from the
  scenario,
- `al._project_dir_root` / `al._goal_to_slug` are faked so the fence dir
  is derivable,
- `al._decompose_goal` raises a `ReachedDecompose` sentinel.

Everything recorded before the sentinel is the unit under test, and the
sentinel escaping is itself the assertion that the fence went up. A
refusal returns a `LoopResult` instead, and the probe records that. This
is a technique worth keeping: **fake the entry, sentinel the exit, and
the span between them is the seam you were not given.**

### Import failure is three different failures

The fence's outer `try` catches `Exception`, so what matters is not *that*
an import fails but *which line* it fails on and *what string* the
operator sees. The probe makes that falsifiable at three granularities,
each landing in a different handler:

| Fixture | Python object | Raises |
|---|---|---|
| a missing NAME | `PartialModule` whose `__getattr__` raises | `ImportError` |
| a missing MODULE | `sys.meta_path` finder | `ModuleNotFoundError` |
| a present module missing an attribute | a plain `ModuleType` | `AttributeError` |

The middle row cost a real correction. A `sys.modules` stub whose
attribute access raises is **not** a missing module: `import X as _y`
succeeds against it and binds `_y`, because the import machinery never
touches an attribute. Making a module genuinely unimportable takes
`sys.modules.pop(name)` **plus** a `sys.meta_path` finder that raises
`ModuleNotFoundError(..., name=name)`. The first attempt at this fixture
was a `DeadModule` populated with attributes — which defeats its own
`__getattr__`, since that hook fires only for names that are *missing*.
The dead module has to be swapped in at registration time, not filled.

### `from llm import a, b` binds `a` even when `b` is missing

The fence's first statement is
`from llm import set_default_subprocess_cwd, set_default_container_rw_roots`,
and the refusal path later calls both names from lambdas. Whether a name
is bound decides whether the refusal's neutralisation loop calls the
setter or raises `NameError`, so the port tracked "did the llm import
succeed" as one flag.

It is two. CPython compiles a multi-name from-import into one
`IMPORT_FROM`/`STORE_FAST` pair **per name, in source order** — so an
`llm` that has the first name but not the second still binds the first,
and the refusal calls `set_default_subprocess_cwd(None)` while raising
`NameError` on the rw-root setter. `fenceBindings` now carries `cwd`,
`rw` and `ce` separately, and the differential has a fixture for each.

This is an amendment to **L59**, which had recorded the coarser claim.

The `NameError` message matters too and is not the common one: the
neutralisers are lambdas, so the names are *free variables* of a closure,
and CPython says `cannot access free variable 'X' where it is not
associated with a value in enclosing scope` — not `name 'X' is not
defined`.

### The dead-`llm` fixture that could not exist

The obvious fixture — `llm` missing entirely — is unreachable. Line 221,
`from llm import MODEL_CHEAP, MODEL_MID, MODEL_POWER`, sits **outside**
every handler, twenty-four lines above the fence. A missing `llm` module
raises straight out of `run_agent_loop`; the fence's own `llm` guard is
reachable only through a missing NAME. The fixture was removed and the
reason written next to where it would have been.

### A real port bug: the fence dir does not use the caller's project

`_fence_dir = _project_dir_root() / (project or _goal_to_slug(ctx.goal))`
reads like it uses `run_agent_loop`'s `project` keyword. It does not:
`project = ctx.project` is rebound about twenty lines above, so the fence
dir follows the **context**, not the argument. The port had a
`FenceArgs.Project` field. It was deleted, and the differential is what
found it — a scenario where the two disagree.

Two smaller ordering facts came out of the same expression: Python
evaluates the LEFT operand of `/` first, so `_project_dir_root()` runs
before `_goal_to_slug()`, and the probe had to record the call to prove
it.

### The battery

113 mutations derived from `fence.go` (L9 — from the FILE, not the diff),
across the import bindings, the three resets and their order, the
worktree-vs-project-dir branch, mkdir placement, the clone seam and its
two fail-closed suppressions, the rw-root filter, and every one of the
refusal path's twenty-odd observable effects: the message format, the log
levels, the cleanup order, the four neutralisers, the slot/lease release
and clearing, the silent `write_event` and stop-verdict stamp, and every
field of the returned `LoopResult`.

First run: 12 survivors, 4 that did not compile. Second run: **110 killed,
3 documented equivalents, 0 build failures**, over 59 differential
scenarios.

Six of the survivors were fixture gaps and one was a real shape finding.
Two setters are called TWICE — the resets spend the first call of
`set_container_suppressed` and `set_default_container_rw_roots` before the
fence body reaches its own use of them — so a fixture that raises on every
call can only ever test the reset. The spec's `[class, message]` grew an
optional third element, the 1-based call index, and the fail-closed
suppression and the mandatory rw-root bind became reachable.

The shape finding: `rwRootsFor` returned `nil` on every error, which made
the caller's `rwRoots = []string{}` reset unobservable — the mutation that
deleted it survived. Python's `_rw_roots` is a list that *already holds the
declared roots* when `_cfg_get` raises, and the `except` is what throws
them away. Returning the partial set is what makes the reset load-bearing,
and a config raise with two declared roots in hand is the fixture. **A
guard that cannot fail is worse than no guard (L9), and a port that
discards state early is how a guard stops being able to fail.**

The three equivalents are all "a guard above has already pinned this":
the fail-closed warning prints `*liveRepo`, which on that branch is the
same string as `fenceDir`; the `intended && clone == nil` test cannot see
its second half, because nothing between the clone assignment and the end
of the try can raise (the only statement left is a log call, and the
logging module swallows handler errors rather than propagating); and
`!b.cwd` agrees with `d.SetDefaultSubprocessCwd == nil` on every input,
because the deps are fixed for a run. The last one stays spelled as the
flag anyway — Python reads a NAME there, and the name is what the
differential drops. That is **L8**'s fourth closed sub-shape, and the
`equivalent` field built one tranche earlier is what let all three stay in
the battery as tripwires instead of being deleted.

## Phase L: the spine, where almost nothing happens (2026-08-27)

`src/agent_loop.py:434-712` is the rest of `run_agent_loop`: phases B
through G, the parallel fan-out's early return, the runaway cost circuit,
and the Phase-45 auto-recovery re-run. Ported to
`go/internal/agentloop/spine.go` as `RunSpine`.

Almost none of the work is here. Each phase is one call into a module this
package does not own. What the spine contributes is the ORDER of those
calls, the gates that skip them, the ARGUMENTS it computes for them, and
where each failure is caught — which is exactly the part of a port that
goes wrong without a type error. So all six phase functions are injected
and the differential compares the calls and their keywords, in order.

### Three names that are not what they look like

`project`, `adapter` and `interrupt_queue` are rebound from the context
twenty lines above the fence, so the auto-recovery re-run passes the
CONTEXT's values and not the caller's keywords — the same shape as the
fence's `project` bug, three more times, in the arguments of a recursive
call. `goal` and `max_iterations` are rebound AGAIN, out of the execute
phase's result dict: **a run whose executor rewrote the goal recovers on
the rewritten one.** And `dry_run` is NOT rebound — the recovery gate
reads the argument, while `ctx.dry_run` is the copy `_initialize_loop`
made. Each of those is a fixture where the two values differ.

### The dicts are modelled as dicts

`_pf` gives up thirteen values and `_ex` sixteen, read by key in a fixed
order. A struct cannot be missing a field, so the port reads them through
`dictGet` and a missing key is a `KeyError` naming the FIRST absent one.
The read order is then observable, and it is pinned by three fixtures.

The same reasoning covers the three tuple unpacks: `a, b, c = f()` against
a four-tuple is a ValueError with its own message, and CPython names the
actual count in both directions — `too many values to unpack (expected 6,
got 7)`, not the older `(expected 6)`.

### A real divergence: one of two adjacent writers normalises

The fan-out's early return records two trace edges and writes three
artifacts. The edge does

```python
stuck_reason=_parallel_result.stuck_reason or ""
```

and the loop log, eleven lines later, does

```python
stuck_reason=_parallel_result.stuck_reason
```

The port normalised both, because the first one established the pattern.
A clean parallel run therefore wrote `""` where Python writes `None` — a
difference invisible in every log line and visible in the artifact an
operator reads. That is **L61**: an inconsistency between two adjacent
sites is a FACT about the original, and a port that tidies it is wrong.

### Two probe findings, both about the harness being the subject

The probe drives the real `run_agent_loop` again, this time all the way
through, with the six phase functions faked and the RECURSION intercepted
by rebinding `al.run_agent_loop` — a module-global lookup, so the fake
catches the auto-recovery re-entry without touching the call the probe
makes itself. Two things went wrong with that:

- The real function was captured per scenario, INSIDE the loop. Scenario
  two therefore captured scenario one's fake as its "real", and the whole
  run collapsed into a `TypeError` about positional arguments. The capture
  has to happen once, at import.
- `_project_dir_root` was left real. It reaches `config.projects_dir`
  through `orch_items`, and the fence **mkdirs what it returns** — a probe
  that let that through would create directories under the live workspace.
  It failed on the stubbed `config` before the mkdir, which is luck, not
  design. Both `_project_dir_root` and `_goal_to_slug` are faked now, with
  the reason written at the site.

Two fixtures are unreachable and say so: `captains_log` can never be the
MISSING module, because `run_agent_loop` imports `loop_id_scope` from it
at the top, outside every handler; and the runaway circuit's `from config
import get` cannot fail, because the fence imported the same name first.
Only a missing NAME is reachable for the first, and nothing for the second
(L59, amendment 3).

### The battery

`tools/batteries/agentloop-spine.json` — 143 mutations over `spine.go`,
run to fixpoint in three rounds:

| round | killed | survived | did not build |
|---|---|---|---|
| 1 | 120 | 11 | 12 |
| 2 | 141 |  2 |  0 |
| 3 | 139 |  0 (4 marked `equivalent`) | 0 |

Round one's eleven survivors were nine fixture gaps and two equivalents.
The fixture gaps clustered: the probe's `Attrs` bag carried a `__bool__`
that production's `plan_recovery` result does not have (a dataclass with
no `__bool__` is ALWAYS truthy, so `if _recovery` is a None check and
nothing else — the probe was testing a state the original cannot reach);
the tool answers were per-scenario rather than per-call, so a mutation
that swapped two `get_tools_for_role` results could not show; and the
state machine always started from the same phase, so a refused
transition was unreachable.

The last two survivors are documented equivalents, and both are worth
stating because they are the same kind of fact:

- Swallowing the pre-flight `SetPhase` error. The FIRST transition is
  the only one that can be refused, because it is the only one starting
  from a phase the caller controls; every later transition starts from a
  phase this function has just set, and each of those pairs is in the
  table. The `StartPhase: "finalize"` fixture proves the first is
  reachable. Nothing reaches the second.
- `popOr` not removing the key. The copy is dead after the two pops, and
  a dict cannot hold the same key twice, so the removal cannot change
  what the second pop finds.

Both checks stay in the code, because Python's `set_phase` raises there
too and `pop` is what Python calls. The difference is only that no input
can make either matter — which is a statement about the ORIGINAL, not
about the port.

## The probes were writing to the live workspace (2026-08-27)

Not a port chunk. A closure, and the only finding in this arc so far that
did damage outside the repo.

`review-ledger.py closable` put P17 at two instances on the spine probe
and two on the loop_finalize probe — the decree's threshold, so the
question stopped being "what is this lens" and became "can this shape be
made impossible". Measuring the surface first:

    go test files spawning python3     : 66
    ...of those setting HOME           :  0
    ...of those setting MARO_WORKSPACE :  5

Sixty-one differential probes ran with the operator's live `~/.maro`
reachable. The whole suite passes with `HOME` sandboxed — so no fixture
depended on the live config — but the sandbox run created
`$HOME/.maro/workspace/memory/captains_log.jsonl`, which meant something
had been writing to the real one.

It had. `internal/skills`' `create_skill_variant` probe declared no
workspace, because `create_skill_variant` returns a mutated object and
reads like a pure transform. It also writes a `SKILL_VARIANT_CREATED`
row, wrapped in a bare `except`. Counting the live log:

| | |
|---|---|
| rows in the live captain's log | 7871 |
| synthetic `parent1`/`child1` rows | **648** |
| rows the log received 2026-08-25 → 08-27 | 649 |
| ...of those that were real | **1** |

Three days in which the operator's captain's log was 99.8% this test.
Nothing failed; the bare `except` guarantees nothing ever would.

### What changed

`internal/pyprobe` gained a sandbox nobody has to ask for — `HOME` and
`MARO_WORKSPACE` both point at temp dirs the test owns — and, the part
that matters, **an assertion that an undeclared probe wrote nothing to
either**. A mitigation alone would have moved the damage somewhere quiet.
The assertion names the caller, and it fired on the first run, on all
three writing cases.

Two details the sandbox forced:

- The live-workspace refusal expands `~` in the child, which is now the
  sandbox — so it would have waved through the very path it exists to
  refuse. It reads `MARO_PYPROBE_LIVE_HOME` now, set from the operator
  home captured at package init, before any `t.Setenv` can move it.
- `internal/pypath`'s probe *is* about tilde expansion and repoints
  `HOME` on purpose, asserting that both sides expand to the same
  fixture. A probe whose `HOME` already differs from the operator's keeps
  it.

### The gap underneath

The probe's write was never compared, so the row's SHAPE was unpinned —
including `rewritten.id[:8]`, a slice rather than a fixed width, whose
only appearance is in a summary sentence the return value does not carry.
The differential now compares the captain's-log row on both sides and
pins the COUNT first (one for a create, zero for a refusal), because two
empty lists agree about nothing. It needed a new case to be reachable at
all: every succeeding fixture had ids longer than eight characters, and
both short-id fixtures were refusals that return before the write.

### The 648 rows

Backed up whole to `~/claude/captains_log-backup-20260827-before-testjunk-purge.jsonl`,
then removed by exact signature (`SKILL_VARIANT_CREATED` with
`parent_id=parent1` and `challenger_id=child1`) — 7871 rows in, 7223 out,
648 dropped, zero unparseable. The eight genuine `SKILL_VARIANT_CREATED`
rows from real runs are still there. Verified afterwards that
`go test ./internal/skills/` moves the live log by zero rows.

## Phase L: the head, which computes nothing (2026-08-27)

`src/agent_loop.py:119-244` — the signature down to the execution fence's
first statement, and the last of run_agent_loop's three chunks. With this
the whole function is ported.

It computes nothing. One call into `_initialize_loop` with twenty-six
keywords, an early return, an ambient loop-id scope, and six locals
rebound out of the context. The part that cannot go wrong is the part
that already did: the fence read the `project` KEYWORD where Python reads
`ctx.project`, and picked a different directory for the entire run.

### Three orderings, none of them obvious

- `from captains_log import loop_id_scope` runs BELOW the early return. A
  run that `_initialize_loop` turns away never imports it, so a missing
  `captains_log` is invisible on that path and fatal on the other. Two
  fixtures, one for each.
- `from llm import MODEL_CHEAP, MODEL_MID, MODEL_POWER` runs INSIDE the
  scope, so its failure has to leave the scope the way any other
  exception does. The scope's exit call is in the record.
- The `with` exits however the body left, and an exception from the exit
  REPLACES the body's. That is why the port does not spell it as a defer
  that swallows its error.

### What the differential can and cannot see

The probe drives the real `run_agent_loop` with `_initialize_loop` faked
and the fence's first call — `_ce.set_container_suppressed`, which the
fence performs before anything else on purpose — raising a sentinel. The
sentinel derives from **BaseException**, because every handler in the
fence is `except Exception`: a sentinel the subject can catch measures
the subject's handlers rather than the span above them.

That makes the twenty-six keywords, their ORDER, the early return, the
scope's enter and exit, and the channel assignment all observable. Two
things in the span are not:

- **The tier map** is a local built between the tier import and the
  fence, and CPython offers no observation point in between. It reaches
  the phases eventually — the spine's differential compares the
  `tier_order` keyword the execute phase receives — but by then three
  distinct constants have already folded into three entries. So its
  dict-literal property is pinned by a Go-only test: a repeated key keeps
  the LAST index, and two equal tier constants produce a map with two
  entries. The tiers are configuration strings; neither case is
  hypothetical.
- **The six rebinds** have no observation point anywhere in the span.
  `project = ctx.project` is a local assignment, and the first thing that
  reads it is the fence — a different chunk whose own probe pins exactly
  this. What is left here is the SEAM between chunks, and it gets a
  Go-only test because the port has been wrong about it once already.

The scenario table's first row passes every keyword at its signature
default and the second overrides all of them, so a default the port
guessed wrong shows up as a disagreement in the recorded call rather than
as a fixture nobody wrote.

### The battery

`tools/batteries/agentloop-head.json` — 85 mutations, two rounds:

| round | killed | survived | did not build |
|---|---|---|---|
| 1 | 73 | 7 | 5 |
| 2 | 84 | 0 (1 marked `equivalent`) | 0 |

Round one's seven survivors were six fixture gaps and one equivalent, and
the gaps are worth naming because four of them are the same shape: a
fixture where two things that should differ happened to agree.

- Two adjacent boolean keywords were both `true` in the
  everything-overridden scenario, so a mutation that swapped them was
  invisible. Every keyword being overridden is not the same as every
  keyword being DISTINCT.
- Nothing made `_initialize_loop` raise, nothing gave an early return an
  empty status (which is what makes `is not None` different from
  truthiness), and nothing made the scope's exit fail — alone or on top
  of a failing body, which is where Python lets `__exit__`'s exception
  replace the body's.

The one equivalent is calling `exit()` after the scope's SETUP raised: a
context manager whose `__enter__` raised does not exist, so no input
reaches a state where the extra call does anything.

`the-body-result-is-dropped` deserves its own line, because it survived
for a reason that is neither a gap in the fixtures nor an equivalence: the
probe's sentinel stops at the fence, so in THIS table the body never
returns at all. That is a limit of the observation point, and the answer
is a Go-only test that says so rather than a fixture pretending otherwise.

All three of run_agent_loop's batteries were re-run after the harness
changes below: **84 / 110 / 139 killed, eight documented equivalents,
zero survivors.**

### The harness caught itself again (P17, fifth-and-a-half)

The "a missing captains_log is fatal" fixture passed on the port and NOT
on CPython: the probe imports `captains_log` two lines earlier to patch
`loop_id_scope`, and a meta-path finder is never consulted for a module
already in `sys.modules`. The fixture ran the LIVE module and would have
agreed with the port for the wrong reason. The fixture now evicts what it
blocks.

### The harness, again: one Blocker and a census

Closing L59's module-absence half turned into three findings, each one
larger than the last.

**The Blocker.** Three probe templates each defined `class Blocker`, and
the third copy — written this session, from the same shape — dropped the
`sys.modules.pop`. It now lives in `pyprobe` as a prepended preamble with
the eviction AND a proof: after installing the finder, `_pyprobe_block`
IMPORTS each name and refuses to run if one still resolves. A fixture that
cannot fail is worse than no fixture.

**The two runners.** The fence and spine probes were being run with
`exec.Command("python3", "…_probe.py.tpl", …)`, which is why the preamble
never reached them. Both now go through `pyprobe.Probe` — which is where
they should have been from the start, since `pyprobe`'s own doc opens by
explaining that there were eight hand-rolled runners and what each of them
got wrong.

Routing them through the harness immediately found a latent one: the
fence's write-fence fixture carries `~/c`, and BOTH engines expand it —
the port with `os.UserHomeDir`, CPython with `Path.expanduser`. It had
been comparing "whichever home each process happened to have", which
agreed only because nothing sandboxed the probe. `HOME` is pinned in that
test now, which is what makes the two sides the same question.

**The census.** Ninety-seven `exec.Command("python3"` sites remain across
about sixty-six test files. The consolidation `pyprobe` describes gave the
eight a shared answer and never replaced the callers. That is the
population the 648-row contamination came out of; a whole-suite run with
`HOME` repointed showed the skills probe was the only site currently
writing outside its own tree, so the rest is latent.

Converting ninety-seven sites is a sweep and it is filed, not done.
`internal/pyprobe/harness_guard_test.go` pins the number in the meantime,
in the same idiom as this tree's other counting guards: a new direct site
fails, and the fix is to use the harness rather than to bump the constant.

## Phase M: the plan critic, and a parameter that was never read (2026-08-27)

`src/pre_flight.py` is the last declared module of the core loop, and the
spine calls it. 463 lines, two independent halves — a reviewer that runs
between decomposition and execution, and a calibration reader that grades
its predictions after the fact — and one property that runs through both:
**almost every line in it is about degrading well.** The review is
advisory. Nothing it does may fail the loop. So the interesting question
in this file is never "what does it compute", it is "what does it answer
when the answer is unavailable", and the port is judged on that.

`go/internal/preflight/` is three files: `preflight.go` (the dataclasses,
the heuristic, the parser), `review.go` (the transplanted critic prompt
and `review_plan`), `stats.go` (`preflight_calibration_stats`).

### Two halves left behind, named rather than discovered

`_build_reviewers` constructs adapters in cost order and resolves keys.
That is the boundary every tranche in this port has left to the caller,
so `ReviewPlan` takes an iterator of candidates instead — and takes it as
an ITERATOR, not a slice, because the generator's laziness is load-bearing
and observable: the paid adapter is never built when the free one answers,
and a generator that raises on its third yield raises only after two calls
have already been made. The fixtures pin both.

`_preflight_stats_main` is the argparse entry point. `CalibrationStats` —
the half that computes anything — is here.

### The parameter that is dead

`review_plan(goal, steps, adapter, *, verbose=False)` names `adapter`
zero times in its body. `_build_reviewers` resolves its own candidates;
the parameter is a leftover. Carrying it into Go would have been the port
inventing a dependency the original does not have, so it is gone — and
`TestTheDeadAdapterParameterIsNotResurrected` reads the Python back and
fails if the body ever starts using it. The differential passes `None`
for it on the CPython side, which is exactly why the differential alone
could never have told me it was dead.

### The prompt is a content key

`_REVIEW_SYSTEM` is 2280 characters of critic instructions, and a critic
prompt that has quietly lost its fifth dimension still returns
valid-looking JSON forever. This is the recurring bug family of the whole
port — PROSE that diverges by a character nobody reads — so the constant
was extracted from the Python programmatically and written into
`review.go` as a raw string literal, never retyped. The differential
compares the two constants directly, and a Go-only test asserts the five
dimension headings, the four response keys and the newline count, because
a mutation that edits BOTH engines the same way compares equal.

### `_heuristic_scope` matches substrings

`any(kw in text_lower for kw in _WIDE_KEYWORDS)` is a SUBSTRING test.
`"all"` therefore fires on `"install"`, and a three-step plan that says
"install the package" comes back **wide**. `"get"` fires on `"budget"`,
`"target"` and `"forget"`. This is a property of the original, not a bug
in the port, and the fixtures pin it rather than fix it.

The branch ORDER is the specification and is easy to get subtly wrong: a
three-step plan holding a wide keyword falls past the first arm and comes
out wide; eight steps of nothing but narrow keywords come out wide too,
because `n >= 8` is tested before the narrow arm. Both are fixtures.

The fold is `pytext.Lower`, not `strings.ToLower`, and this is the first
site in the port where the difference reaches an ANSWER rather than an
intermediate string. `"LİST"` folds to `li̇st` in CPython — with a
combining dot between the i and the s — and to `list` in Go's simple case
mapping. The narrow keyword fires on one side only, and the scope
changes. `the-case-fold-is-gos-simple-one` is a battery row now.

### `_parse_review`: four lists, three ways to fail

The parser reads four arrays out of the reviewer's JSON, and Python's
`for x in d.get(key, [])` over a value that is not a list has three
different endings, all reachable from an LLM's output:

  - an OBJECT iterates its KEYS, so every item is a string and the first
    `.get` on one is an AttributeError;
  - a STRING iterates its characters, same ending;
  - a number, a bool or null is not iterable at all, and the `for`
    statement itself raises TypeError.

`unknown_unknowns` has no `.get` in its body — the item IS the message —
so it accepts all three happily and turns a two-character string into two
flags. That asymmetry is the original's, and it is a fixture.

`int(m.get("step", 0))` is two exceptions in one expression: AttributeError
for an item that is not a mapping, ValueError for a string that is not a
number, TypeError for None. `pyval.Int` already knows CPython's exact
domain and its exact messages, so the port asks it rather than guessing.

### Every exception message, spelled out

The parser's `except Exception as exc` logs `%s` of the exception — the
SENTENCE, not the class. A port that raised the right class with the
wrong sentence would look correct in every test that only compared
classes, so the messages are reproduced exactly:
`'list' object has no attribute 'get'`, `'int' object is not iterable`,
`[Errno 21] Is a directory: '.'`,
`'utf-8' codec can't decode byte 0xff in position 3: invalid start byte`.
`pyval.PyErr` already carried the class-plus-message shape; `pyval.TypeName`
already knew `type(v).__name__` for decoded JSON. Both were reused rather
than re-founded.

One message is NOT reproduced and is named at its site:
json.JSONDecodeError's `Expecting value: line 1 column 4 (char 3)` is a
scanner position this port's decoder does not carry, and reproducing it
means reimplementing CPython's error positions. The class is right; the
differential canonicalises that one sentence on both sides and compares
every other message exactly. If a second site ever needs the position,
that is the moment to build it in `pyval` rather than here.

CPython 3.14 also rewords the unhashable-key TypeError — `cannot use
'list' as a dict key (unhashable type: 'list')` — which the port matches,
with a note that older interpreters say only the parenthesised half.

### `read_text()` decodes, and Go reads bytes

`preflight_calibration_stats` reads the calibration file with
`Path.read_text()`. Go's `os.ReadFile` hands back bytes and is happy with
anything; CPython decodes as UTF-8 and raises. A calibration file with one
stray byte is therefore processed on one side and fatal on the other — a
divergence with no symptom until the day it matters. `decodeError` is the
check, and it reports what CPython reports, which is the START of a bad
sequence rather than the byte that actually failed: `b"\xe2("` names
`0xe2` at position 0, not the parenthesis.

Kept local, not shared. This is the FIRST site in the port that decodes a
whole file's bytes as text; the second one is where it moves to `pytext`,
per the lens-closing rule.

### The two failure modes of the stats reader are different

A file that cannot be LOCATED answers with a dict — `{"error": "cannot
locate memory_dir"}`. A file that cannot be READ raises. The function
guards its `json.loads` per line and nothing else, so a record that is
valid JSON but is not an object reaches `e.get` and raises AttributeError
from inside the `sum(...)` — and that exception leaves the function
entirely, taking the caller with it.

Precision and recall are `None`, not `0`, when their denominators are
empty: "no data" and "never right" are different answers and the CLI
prints `n/a` for one of them.

`scope_breakdown` is keyed by a VALUE read out of a record, not by a JSON
object key, so the key need not be a string. `1`, `1.0` and `True` are one
Python dict key — they compare equal and hash equal — so three records
spelling the scope three ways land in ONE bucket, under whichever spelling
arrived first. `dictKey` models that, and a list scope is unhashable and
raises. Both are fixtures.

### What the differential covers

`preflight_probe.py.tpl` drives the real CPython functions with `pf.log`
replaced by a recorder that keeps the LEVEL and the message logging would
have built. Sixty-three scenarios across six kinds: the prompt constant,
`_heuristic_scope`, `PlanReview` rendering, `_parse_review`, `review_plan`
with injected reviewers and captured stderr, and
`preflight_calibration_stats` over real files on disk.

Each stats scenario gets its OWN directory, named by the spec, because the
probe runs every scenario before the Go side runs any and a shared
filename would leave only the last fixture on disk.

Three things the differential structurally cannot see got Go-only tests
instead: that `Flags` and `MilestoneStepIndices` are empty slices and never
nil (Python's `default_factory=list` has no nil, and the probe's renderer
normalises both to `[]`); that the critic prompt still names all five
dimensions; and that the dead `adapter` parameter is still dead upstream.

### The battery

`go/tools/batteries/preflight.json` — 193 rows derived from the FILE, not
from the diff. **188 killed, five documented equivalents, no survivors**
across three rounds (r1: 17 survivors and 6 build breaks; r2: 2 and 1;
r3: clean).

Round 1's survivors are the useful part of the record, because they sort
into three different answers and only one of them is "add a fixture":

**Fixtures that were missing (11).** A keyword split across two steps
(`"dep"`, `"loy the app"`) — which is the only input that can tell
`Join(steps, " ")` from `Join(steps, "")`. A six-step plan holding a
narrow keyword, for the `n <= 5` boundary. Four fence shapes: two
backticks that are not a fence, pretty-printed JSON inside a fence (whose
rejoin is visible only through `Raw`), a closing fence on the same line as
the JSON, and a fenced answer with a trailing newline — that last one is
what distinguishes "strip the first line, then look for the closing fence"
from the other order, because the join normalises the trailing newline
away before the suffix test. A non-string item in `unknown_unknowns`. A
multi-byte character in a string that is being iterated as characters. A
lone `0x80`. A calibration line indented with a non-breaking space, which
`str.strip()` removes and `strings.Trim(" \t\n")` does not. A precision
that repeats, since the existing fixture only had a repeating recall.

**Equivalences, documented at the site (5).** The `review = nil` after a
failed reviewer can never have work to do — the loop breaks the moment
`review` is non-nil, so it is nil at the top of every iteration. The
milestone index recorded before rather than after the message read: a read
that raises discards the whole Review two lines later. `jsonDecodeErr`'s
CLASS is unobservable, because the only caller logs `str(exc)` and
JSONDecodeError is a ValueError subclass in CPython. `countTruthy`'s
raise on a scalar entry, because the breakdown loop is a second identical
guard over the same entries. And the `!isStr ||` in the vocabulary gate,
because `scopeStr` is the zero string whenever `isStr` is false and the
empty string is already off-vocabulary.

Each of those five is kept in the code and marked `"equivalent"` in the
spec. Four of them are written the way they are because the ORIGINAL
writes them that way, and the fifth — the type test — is written that way
because Python's `scope not in (...)` is a membership test over values and
spelling it as a string comparison alone would be a coincidence that
happens to hold.

**A dead arm (1).** `dictKey` had `case int:` and `case float64:` arms
that no input can reach: every value arriving there came out of
`pyval.LoadsOrdered`, which types every JSON number as a `json.Number`.
The mutation that separated the int and float buckets survived because the
code it mutated was unreachable. The arms are gone — unreachable code is
where a wrong answer hides, and a battery row that cannot fail is worse
than no row.

Six of r1's rows did not COMPILE rather than dying, all for the same
reason and all in one direction: deleting a use orphans an identifier or
an import. `a-read-failure-is-swallowed` orphaned the whole `pyos` import,
which is a fair summary of how narrowly that error path is used. A build
break is not a kill; each was re-spelled to keep the orphan alive and let
a test do the killing.

Two rows became SPEC BUGs in r2 — pattern matched zero sites — because the
equivalence comments I had just written sat inside their `old` text. That
is the third time this session; the fix each time is to include the new
comment in the pattern, and the lesson is that a battery row anchored on
code ADJACENT to a comment is anchored on the comment too.

## Phase N: the rewrite that must under-rewrite (2026-08-27)

`src/path_rewrite.py` is 477 lines that exist because a workspace is full
of paths naming the machine that produced it — 44,941 occurrences of one
root across 5,841 text files in the 2026-08-13 box copy. Copy the
workspace elsewhere and every one of them is a lie. This module rewrites
them at IMPORT time, over the extracted copy only, with every transformed
file named in a durable record.

`go/internal/pathrewrite/` is one file, because the module is one
argument made seven times: **under-rewriting is the safe direction.** A
stale absolute path is visible and inert. A confidently-wrong one
resolves to a real local directory that is not the one meant. Every
screen in the file — the content-addressed skip, the binary sniff, the
root validation, the two match boundaries, the containment check — is
there because the other direction is silent corruption, and the port's
only job is to keep each screen exactly as wide as it is.

Which makes this the first tranche where the differential is not enough
on its own, twice over.

### The matcher could not be copied

Python builds ONE compiled pattern over all sources:

```python
_BOUNDARY = rb"(?![A-Za-z0-9_-])"
_LEFT_BOUNDARY = (rb"(?:(?<![A-Za-z0-9_./-])"
                  rb"|(?<=\\n)|(?<=\\t)|(?<=\\r)|(?<=\\f)|(?<=\\v)|(?<=\\b))")
```

Go's `regexp` has no lookbehind and no lookahead, so there is no
translation of that pattern — only a reimplementation. `Substitute` is
therefore a hand-written byte scan, and the three things it has to get
right are the three things a backtracking engine does for free:

* **A failed match advances ONE byte.** Not the candidate's length. The
  overlapping-prefix case (`//aa` against source `/aa`) is where a
  length-skip is wrong, and it has a row.
* **A failed RIGHT boundary falls through to the next alternative at the
  SAME position.** `re` backtracks into the shorter branch when the
  trailing lookahead fails, so `/srv/ab/cx` matches `/srv/ab` even though
  `/srv/ab/c` was tried first and blocked. Sources are tried longest-first
  and the loop `break`s on the first that clears both edges.
* **The scan emits into a separate buffer and never re-reads it,** which
  is how the one-pass property survives: a destination containing a later
  source is not rewritten a second time.

The escape exemption is the part that only real data could have taught
anyone. A path inside a JSON string is usually preceded by `\n` — and at
the BYTE level the character before the root is then the letter `n`,
which the plain lookbehind reads as an identifier character and blocks.
The first live box→Mac import left 5,543 occurrences unrewritten for
exactly that reason. Six two-byte sequences (`\n \t \r \f \v \b`, literal
backslash plus letter) are boundaries; `\x` is not; a bare `n` is not.
All eight cases are fixtures.

### Some things a differential structurally cannot check

Two tables in this module are long — fifty binary suffixes,
twenty-eight system roots — and a differential can only ever sample them.
A fixture per entry would be a hundred scenarios proving one thing each,
and it still would not fail when upstream ADDS an entry. So five Go-only
tests read the literals back out of `path_rewrite.py` and compare whole
sets: `_SKIP_SUFFIXES`, `_SYSTEM_ROOTS`, `_SKIP_DIR_PARTS`, the ORDER of
`ROLES`, and the five scalar constants.

The sixth is the one that matters most. `TestTheBoundaryPatternsMatch-
Upstream` pins both regex literals verbatim, because nothing structural
ties the hand-written scan to the pattern it implements: if upstream
widens a character class, the differential notices only if some fixture
happens to contain the newly-admitted character — and a class's edges are
exactly what nobody thinks to write a fixture for. This is the same shape
as Phase M's prompt guard, and the same reasoning: a table is a content
key.

### Two divergences the fixtures found

**`os.unlink` is not `os.Remove`.** The failed-swap recovery path is a
bare `except OSError: pass` around `os.unlink(tmp)`. Go's `os.Remove`
also removes an empty DIRECTORY — and a directory sitting on the temp
path is precisely how that branch is reached, since it is what makes
`open(tmp, "wb")` fail. CPython leaves it alone; the first cut of the
port deleted it. `syscall.Unlink` now, with the fixture that found it
(`a-directory-on-the-temp-path-survives-the-failure`) and a battery row
that pins the spelling.

**`pathlib.suffix` reads a NAME, not a path.** `Path("sub/.png").suffix`
is `""` — the name is `.png`, and pathlib lstrips leading dots before
looking for the last one — while running the same rule over the whole
string yields `.png`, which IS in the binary set. A dotfile named after
an extension is therefore rewritable, and a port that skipped
`pypath.Name` would silently refuse to fix it.

Three further CPython facts got pinned on the way past: `str.strip()`
removes the four INFORMATION SEPARATORS (U+001C–U+001F), which Go's
`unicode.IsSpace` does not consider space at all — the NBSP that usually
separates `pytext.Strip` from `strings.TrimSpace` does NOT, because Go
agrees about that one; `str.lower()`'s full case mapping means `.İCO`
does not fold to `.ico` and is not screened; and `len()` counts CODE
POINTS, so the longest-source-first sort needs `utf8.RuneCountInString`
and `/data/abcde` sorts ahead of `/data/ééé` even though it is shorter in
bytes.

### The battery

`tools/batteries/pathrewrite.json` — 177 rows, three rounds:

| round | killed | survived | !BUILD | spec bugs |
|---|---|---|---|---|
| r1 | 151 | 22 | 3 | 0 |
| r2 | 162 | 3 | 0 | 0 |
| r3 | 163 | 0 | 0 | 0 |

Fourteen rows are marked `equivalent`, each with a comment at the site,
and the interesting thing is the SHAPE of them: eleven of the fourteen
are places where the port carries a distinction the code cannot observe.
`pathParts` drops `""` and `"."` and keeps `".."` — but its only consumer
asks whether a component is `.git`, `.hg` or `.svn`, and none of the
three is any of those, so all three decisions are inert. The
leftover screen runs before the suffix screen — but no name can trip both,
because a leftover ends in `.tmp` and `.tmp` is not in the binary set.
The strict prefix walk starts at 1 and stops before `len(parts)` — but
i=0 and i=len are both provably unreachable given the two minimums above
it.

None of those got "fixed". They are Python's spelling, and the reason
each is inert is a property of a CONSTANT that can change — which is the
whole argument for writing the reason at the site rather than deleting
the line. The three genuinely-unfixturable rows say so plainly: the head
sniff is an early exit whose answer the whole-file check reproduces, the
second `stat` observes nothing the first does not, and the only failure a
fixture can provoke inside the swap is one where the cleanup has nothing
to clean.

The r1 survivor list was 22 long and split three ways — eleven missing
fixtures, eight equivalences, and three that were both (the fixture I
had written could not distinguish the mutant, and the reason was worth
a comment either way). One regression appeared in r2: strengthening the
code-point-sort fixture removed the only source root that failed the
strict screen, and `the-source-root-is-screened-loosely` came back to
life. That is the fixture set behaving like a fixture set — a row that
was being killed incidentally, by a scenario written for something else.

The differential is 170 scenarios over six kinds — validate, build,
substitute, substitute_text, skip, rewrite_file, rewrite_tree — with the
fixture filesystem described once in the spec and built by each side in
its own temp directory. Nothing in a record names an absolute path, so
there is nothing to canonicalise: a record that differs, differs about
behaviour.

`fileState` in the differential grew a directory arm for this tranche,
and the mode/mtime columns earn their place: an atomic swap through a
fresh temp file silently gets the umask's mode and today's mtime unless
it is asked not to, and `shutil.copymode` carries all twelve bits, not
Go's nine — there is a sticky-bit fixture.

### Two standing guards fired, which is the point of them

`TestProbeReadsCannotMaskAnOmittedField` (L6) caught four `omitempty`
fields the probe subscripts: an omitted field is a `KeyError` on the
CPython side, not a zero value, and a probe that fills its own default
cannot tell "the scenario said empty" from "the scenario forgot". The
spec struct now carries no `omitempty` at all, and the probe reads
`e["mode"]` and treats EMPTINESS — not absence — as "leave it alone".

`TestNoNewByteWiseFilenameSortAppears` caught the `sort.Strings` in
`sortedKeys`. It is the right kind: the strings are skip REASONS
(`vcs-internal`, `binary-suffix`, `oversize`), Go and Python literals
both, reproducing `sorted(self.skipped.items())`. The values beside them
are filenames; the keys never are. Allowlisted with that sentence.

Neither guard was written for this tranche. Both are lenses that got
CLOSED after firing twice, and this is what closed lenses are supposed to
feel like — a failing test naming the rule, in the round where the
mistake was made, instead of a reviewer noticing it three tranches later.

## Phase O: three-valued honesty, and a matcher with no backtracker (2026-08-27)

`src/execution_receipts.py` → `internal/receipts`. 487 lines of Python,
stdlib only (`json`, `logging`, `re`, `pathlib`), four public surfaces:
`load_receipts`, `render_receipt_evidence`, `audit_receipt_block`,
`neutralize_fence_text`.

The module turns a run's call-record files into evidence for the closure
audit, and it is an argument about THREE-VALUED honesty. "The record
shows process work", "the record shows NO process work", and "there is no
record" are different answers, and a PARTIAL record — unreadable files, a
capped collection, calls that rode a backend which relays no tool events —
is a fourth that must never collapse into the second. Almost every count
the loader keeps exists to stop that collapse: `unreadable_files`,
`malformed_events`, `truncated`, `readable_calls`, `capture_calls`. None
of them is decoration, and a screen that silently skips something is a
defect here even when the visible digest is identical.

Two consequences for the port. Everything degrades to empty output rather
than raising, so the error paths ARE the behaviour rather than the edge
case. And the counts are the contract, so most of the differential is
about failure shapes: a file that will not parse, a record of the wrong
shape, an event of the wrong type, a command that is not a string, a
backend that relays nothing.

### `_PATH_TOKEN` is the second hand-written matcher, and the proof is different

Phase N's matcher was hand-written because Python's pattern had
lookbehind. This one is hand-written for a different reason: the pattern
has two `\b`, Python's `\b` is Unicode-aware where Go's is ASCII, and
`pytext.WordStart`/`WordEnd` CONSUME the boundary character — which a
`findall` over the matched TEXT cannot afford. So the boundaries have to
be zero-width, and RE2 will not give me that.

```
[\w./-]*/[\w./-]+|\b[\w-]+\.(?:py|md|txt|json|jsonl|sh|yml|yaml|html)\b
```

Both alternatives happen to be decidable without a general backtracker,
and the argument is worth writing down because it is what makes the
hand-scan safe rather than merely convincing.

ALTERNATIVE ONE has no boundary at all, and all three of its pieces draw
from the SAME class. At a position `i` the reachable text is exactly the
maximal run `R` of `[\w./-]` starting there: the greedy `*` takes all of
`R`, backtracks to the last `/` leaving something for the `+`, and the
`+` takes the remainder. So the match either IS `R` or does not exist,
and it exists exactly when `R` contains a `/` with at least one character
after it. One forward scan, one backward scan, no choice point.

ALTERNATIVE TWO's only choice point is the extension, because `[\w-]`
excludes `.`. Backtracking the `+` to a shorter run would require a `.`
at a position that is by construction inside the run — impossible. So the
name is the maximal `[\w-]` run and nothing else, and the only thing left
to try is the extension list.

That last part is where the extension list comes in. `re` tries the
alternatives left to right and takes the first that also clears the
trailing `\b`, so `x.jsonl` matches `json`, fails the boundary against
the `l`, and backtracks into `jsonl`.

I first wrote that the ORDER of the list was load-bearing, and it is not.
The battery said so: `the-extension-alternatives-are-tried-longest-first`
survived, and the reason it survives is a small proof rather than a
missing fixture. Two extensions can both match at one position only if
one is a prefix of the other — `json`/`jsonl` is the only such pair here
— and then at most one of them can clear the trailing boundary, because
`json` clearing it means the next character is a NON-word one and `jsonl`
needs that same character to be an `l`. So the first match that clears
the boundary is the only match that clears it, and no ordering changes an
answer.

The list stays a slice in Python's order anyway, for provenance, with a
guard test that derives it FROM the upstream literal and compares in
order — and there are fixtures on both sides of the prefix pair, because
the BACKTRACKING is real even though the order is not. The correction is
the point: the claim was plausible, I wrote it at the site and in this
file, and the only thing that caught it was a mutation nothing killed.

### The dead-code question, answered the other way

`audit_receipt_block` promises never to raise, and the handler that keeps
that promise logs one `log.debug` line — the only place the reason
survives. Writing the port with `checkResults []any` would have made that
handler unreachable: `load_receipts` is the only producer of `loaded`, it
writes all six keys on every path, and the row list it builds is all
mappings. With a narrowed signature nothing can fail, the handler is dead
code, and the Debug seam is decoration.

That is a real temptation and it is the wrong call. The argument sounds
like type safety and is actually the port quietly changing what the
function is FOR. The contract is "never raises" precisely because the
caller is quality-gate code handing over whatever it has; a signature
that makes a bad argument unrepresentable does not make the caller
correct, it just moves where the crash lands. `checkResults` is `any`,
`pyIter` reproduces `for r in (X or [])` including the falsy shortcut
(`0` is not a `TypeError` here but `5` is), and there is a scenario that
drives an audit all the way into the handler and asserts the logged line.

The same reasoning produced `pyLen`. `rows` is read in two steps and the
FIRST is `len()`, not iteration — so `rows = 5` dies with "object of type
'int' has no len()" while `rows = "ab"` gets past the length and dies one
line later on `.get`. Collapsing the two into a single iterability check
reports the wrong error for both. The order is the answer, and there is a
fixture for each of the three shapes.

### Two things the differential could not have found

`_clip` slices: `text[:head]` with a floor of 20, `text[-40:]` with a
fixed tail. Python slices CLAMP where an index would raise. Production
only ever calls `_clip` with limit 120 or 160 — always larger than 60 —
so the clamp is unreachable through the module's own callers, and the Go
port panicked on `_clip("abc", 0)`. Nothing in the module could reach it;
the probe calls the private function directly, which is the only reason
it showed up. Fixed at the site with the reason, and there are three
fixtures below the floor.

`read_text(encoding="utf-8")` DECODES before `json.loads` ever runs, so a
call record with one bad byte is UNREADABLE and counts. Go's
`json.Unmarshal` substitutes U+FFFD and reads it happily — which would
turn an unreadable file into a clean record, and a clean record is
exactly what must not be manufactured here. `readCall` runs `utf8.Valid`
first, and the fixture is an otherwise-perfect record with a lone `\xff`
in the command.

### The tables, closed the same way as Phase N's

Five content keys a differential can only sample: `_SHELL_TOOL_NAMES`,
`_CAPTURE_BACKENDS`, the six scalar bounds, and the two regexes. The
guards read the literals back out of the Python.

`_PROCESS_MARKERS` gets the strongest form. The port differs from
upstream in exactly two ways and both are forced — Unicode boundaries
through `pytext`, and non-capturing groups because RE2 has no reason to
pay for capture — so the guard applies those two substitutions to the
upstream literal and demands the result equal `processMarkers.String()`
character for character. Adding a runner upstream fails HERE with the
diff, instead of leaving the port silently narrower until someone writes
a fixture that happens to name `deno test`.

The scalar bounds matter for the usual reason: `MAX_SCANNED_FILES` costs
a thousand files to exercise and `MAX_FILE_BYTES` an eight-megabyte one.
Both have fixtures anyway — the thousand files are cheap and the eight
megabytes is a sparse `truncate` — but the guard is what keeps them
honest when the number moves.

### The differential

221 scenarios over nine kinds: `clip`, `neutralize`, `display`,
`path_token`, `process_markers`, `check_tokens`, `load`, `render`,
`audit`. The four private helpers are driven directly, because they are
where the port's own decisions live.

`load` and `audit` build a call-record filesystem per scenario from a
spec the probe and the test both read, each under its own temp directory.
`render` takes its `loaded` mapping as JSON SOURCE, decoded on both
sides, which is how a scenario carries a value whose TYPE is the point: a
count recorded as a string, a row that is not a mapping, a `rows` value
that is an int. `audit` installs a fake `runs` module — or blocks the
import outright with `_pyprobe_block(["runs"])` for the case where the
module is missing — and captures `log.debug` so the failure branch can be
asserted rather than assumed.

The `loaded` mapping is compared as a LIST OF PAIRS on both sides,
because the key ORDER is part of the answer: `load_receipts` writes its
six keys in a fixed order and a consumer reading them back is entitled to
it. A row that survives the loader and a row order that does not are
different bugs.

### The battery, and a claim that only a mutation could catch

171 rows, converged at r3: 156 killed, 15 documented equivalents, no
survivors and no build breaks. r1 was 28 survivors and 10 mutants that
did not compile; r2 was 4 and 0.

The r1 list split the usual three ways — ten new fixtures, ten
equivalences with the reason written at the site, and ten build breaks
re-spelled so a test does the killing rather than the compiler. Two of
those thirty are worth more than a line.

**`the-extension-alternatives-are-tried-longest-first` survived, and it
was right to.** I had written — at the declaration, in the commit
message, and three paragraphs up in this file — that the extension list's
ORDER is load-bearing, and that sorting it would make the port return
`x.json` for a `.jsonl` file. That is false, and the proof is three
lines: two extensions can both match at one position only if one is a
prefix of the other, and then at most one can clear the trailing `\b`.
The claim was plausible, it was stated in three places, it survived my
own review, and the only thing that asked it directly was a mutation
nothing killed. It is the same shape as the metrics round the day
before — a fix without a fixture is how a wrong fix survives; a CLAIM
without a fixture is how a wrong claim does.

**`the-dotted-name-may-be-empty` looked equivalent under the same style
of argument, and is not.** The reasoning runs: to reach a position whose
`[\w-]` run is empty you need a `.` with a word character before it, and
the identical extension scan already ran from the start of that word run
and failed. True — unless the scanner reaches the dot because a PREVIOUS
match ended on it. `doc a.py.md` does exactly that: `a.py` matches, the
scan resumes at the second dot, and the mutant emits a spurious `.md`.
Real fixture, real kill, and a reminder that "unreachable" arguments
about a scanner have to account for where the scanner has been.

Three survivors needed a fixture that cost something rather than a
comment. `the-size-screen-never-runs` survived because the over-bound
fixture was SPARSE: a hole reads as NUL bytes, which are valid UTF-8 and
invalid JSON, so the file came back unreadable whether or not the size
screen ran. It now has a sibling — a genuinely valid record padded to
`MAX_FILE_BYTES + 1`, eight megabytes on each side, and the only shape
that proves the screen fires at all. `the-scan-bound-has-no-overflow-slot`
survived for the mirror reason: the thousand-file fixture had a row in
every record, so the ROW cap fired first and `truncated` was true for the
wrong reason. The records are empty now. And `the-partial-record-branch-
checks-only-unreadable` needed an audit whose record is incomplete for a
reason other than an unreadable file — a malformed event alone. A branch
with three inputs needs each of them to be able to fire it by itself.

### An ops lesson, paid for in confusion

`setsid nohup … &` was the fix for a background child dying with its
parent Bash task. It is also how you lose track of one: `setsid` forks
when it is not already a process-group leader, so `$!` is the pid of
`setsid`, which exits immediately. `kill -0 $!` then reports DONE while
the real runner is still working. A second battery started on top of the
first, the first had a mutation applied at that moment, and the second
correctly refused with "the tree is ALREADY red".

The part worth keeping is the second half. `git status` showed the tree
CLEAN throughout, because `internal/receipts/` was still untracked — git
cannot report a modification inside a directory it has never seen. Every
previous battery ran against a tracked file where a leftover mutant shows
up as an `M`. **Land the package before running its battery**, or the one
safety net that has caught this before is not there.
