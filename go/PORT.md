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

**Named residual:** `pack.json`'s key ORDER. The manifest is a
`map[string]any` across ~15 call sites including a foreign-file decode,
so `FromPlain` sorts it. Escaping and indent are Python's; order is not.
Nothing hashes those bytes (the payload hash is separate), and the fix is
a typed manifest, not a renderer change.

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
| `loop_init.py:186` | `escalation` (daily budget gate) | the pre-start daily-cap refusal is unported (no `metrics.spend_today` twin) |
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
| `heartbeat.py` | 1653 | the periodic driver |
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
| `system_health.py` | 693 | self-health lane |

`correspondence.py` is dev tooling by decree and does not belong in a
runtime port at all; the other 25 do.

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
