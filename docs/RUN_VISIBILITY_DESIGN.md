---
status: living
---

# Run Visibility — per-run HTML report + static runs index

Replaces the archived `observe.py` HTTP dashboard's read-only half
(`archive/observe_dashboard.py`, killed 2026-07-02 — Jeremy: "proof of concept
that sort of failed"). See BACKLOG.md "Observability dashboard — archived" for
the post-mortem this design responds to. This doc covers the report/index
feature only; the general-purpose viz-server idea it surfaced is tracked
separately (BACKLOG.md "General-purpose visualization server").

## Why the old dashboard failed, and what this does differently

The old dashboard was a ~950-line stdlib `http.server`, unauthenticated, bound
to `0.0.0.0` by default, that mixed read-only observability (cost, memory,
ancestry, eval trends) with a live control surface (goal submission, replay-as-
factory-mode). Two concrete mistakes on record: no auth on a surface meant to
be read-only, and conflating observability with live control in one surface.

This design is deliberately narrower:
- **Static files only, no listener process.** Both surfaces below are plain
  HTML written to disk, viewed however the box is already accessed. No new
  network-exposed surface, so no auth problem to solve.
- **Read-only, no control surface at all.** Nothing here submits goals,
  replays runs, or mutates state. If a control surface is wanted again later,
  that's a separate decision, made deliberately, not organic growth of this
  feature.
- **Rides existing lifecycle events, no new background process.** Generation
  is hooked into the same three call sites that already regenerate the
  markdown plan manifest after each step. This matches the standing
  guardrail in GOAL_BRAIN.md's Invariants ("app, not OS" — prefer an existing
  lifecycle event over new background infrastructure).

## Data this relies on (already exists, verified against code 2026-07-08)

- **Byte-level prompt/response capture**: `record_llm_call()` (`src/runs.py:325`,
  called from `src/llm.py` ~line 410) captures every LLM call to
  `<run-dir>/build/calls/call-NNNNN.json` whenever `record.enabled` is true
  (default on). `StepOutcome.call_record` (`src/loop_types.py:93`) is the path
  to that file for the step that produced it.
- **Per-step outcome data**: `StepOutcome` (`src/loop_types.py:81`) —
  `status` ("done"/"blocked"/"skipped"), `elapsed_ms`, `tokens_in`/`tokens_out`,
  `cache_read_tokens`, `iteration`, `confidence`, `call_record`. Missing today:
  a per-step start/end timestamp (see "Additive field" below).
- **Captain's log**: `log_event()` (`src/captains_log.py:272`) writes an
  optional `loop_id` per entry (line 292-293). Entries without a `loop_id`
  are cross-run reflections, not this-run noise — they need a place in the
  report (collapsed "global context" section), not silent dropping.
- **The existing freeze/regenerate precedent**: `_write_plan_manifest()`
  (`src/loop_artifacts.py:63`) already does exactly the "rewrite after each
  step, stop mutating once terminal" pattern this feature needs, from three
  call sites:
  1. `src/loop_planning.py` ~line 230 — initial write right after decompose,
     before step 1 (`step_outcomes=[]`).
  2. `src/loop_post_step.py` ~line 230, inside `_write_iteration_artifacts()`
     — rewrite after every step.
  3. `src/loop_finalize.py` ~line 52, inside `_build_result_and_finalize()`
     — final write with terminal `status` + `elapsed_ms`.
- **Path resolution**: `runs.artifact_dir(project, project_root_fn=...)`
  (`src/runs.py:262`) resolves to `<run-dir>/build/` when a run-dir is
  pinned, else `projects/<slug>/artifacts/` — same directory every other
  per-run artifact (plan manifest, loop log, per-step markdown) already
  lands in. `runs.runs_root()` (`src/runs.py:69`) is
  `~/.maro/workspace/runs/`, one dir per handle.
- **Terminal status values**: `LoopResult.status` is
  `"done" | "stuck" | "error" | "interrupted" | "restart"` — freeze rule is
  "anything other than `running` is terminal," not a `done`/`blocked`/`failed`
  tri-state (`blocked` is a `StepOutcome` status, not a `LoopResult` status).

## Product decisions (settled 2026-07-08)

1. **Two-tier view.** Summary is eager/embedded: a horizontal timeline
   (GitLab-CI-pipeline-style Gantt), one segment per step, positioned/sized
   by time, colored by status, with adornments for retries/errors/replans/
   escalations, plus a step table (tokens, cost, elapsed) below it — the same
   data `_write_plan_manifest` already computes. Detail is lazy: full
   prompt/response text loads only on interacting with a step, read from the
   existing `call_record` file — never inlined into the main report.
2. **Persistence**: the per-run HTML is a flat artifact living alongside the
   run's other artifacts (same dir as `loop-<id>-plan.md`/`loop-<id>-log.json`),
   part of that run's permanent record — no separate pruning policy. No
   history of every intermediate regeneration by default; only current state
   matters, converging to the frozen final version.
   - **Debug mode** (config `report.debug_snapshots`, default off; env
     override `MARO_REPORT_DEBUG=1`): when on, each regeneration additionally
     copies to a timestamped snapshot under
     `<artifact_dir>/loop-<loop_id>-report-debug/report-<UTCstamp>-<seq>.html`.
     When off, that debug dir is never created, and if it exists from a prior
     debug session it's deleted (`shutil.rmtree`, best-effort) at the start of
     the next write for that loop — so turning the flag off self-cleans, no
     separate GC job needed. These snapshots are developer/debugging data
     only, never part of the durable record end users see.
3. **Global index**: a second static HTML file
   (`~/.maro/workspace/runs/index.html`), regenerated via the same lifecycle
   hooks, listing all goals/runs with a brief synopsis (goal text, project,
   status, started/elapsed, cost/tokens) and a link into each run's own
   report. Per-run/session granularity — explicitly **not** a timeline
   aggregating multiple runs of the same project over time (discussed,
   deliberately out of scope for v1; "meta-of-meta" visualization, maybe
   interesting later).
4. **Primary audience for the "current" view** is an end user who mainly
   wants the latest state of their goal — the in-place-overwritten file
   during a run and the frozen final file after are the main UX. Debug
   snapshot history is a developer-only concern.
5. **No live server.** Both surfaces are pure static files. If a server is
   wanted for convenience later, see BACKLOG.md "General-purpose
   visualization server" — a separate, deliberately deferred decision, not
   bundled into this build.

## New/modified files

- **New `src/loop_report.py`** — the feature's core. Kept separate from
  `loop_artifacts.py` (documented as "extracted verbatim" writers) since this
  is templating-heavy and will run several hundred lines.
  - `write_run_report(project, loop_id, goal, planned_steps, start_ts,
    step_outcomes, *, status="running", elapsed_ms=0, replan_count=0) ->
    Optional[str]` — same call shape as `_write_plan_manifest` so all three
    hook sites pass what they already have. Resolves output path via
    `runs.artifact_dir()`, writes `loop-<loop_id>-report.html`. Never raises
    (swallow-and-return-None, matching every writer in `loop_artifacts.py`).
  - `write_runs_index() -> Optional[str]` — scans `runs.runs_root()`, writes
    `<runs_root>/index.html`. Never raises.
  - Internal: `_gather_log_markers(loop_id, start_ts)`, `_render_timeline(...)`,
    `_render_step_rows(...)`, `_report_is_frozen(path)` (sentinel check),
    `_maybe_snapshot(...)` / `_clear_debug_snapshots(...)` for debug mode.
  - Config: `report.enabled` (default True), `report.debug_snapshots`
    (default False).
- **Modified `src/loop_planning.py`** (~line 230) — after the initial
  `_write_plan_manifest` call, add a guarded `write_run_report(...,
  step_outcomes=[])` call.
- **Modified `src/loop_post_step.py`** (`_write_iteration_artifacts`, ~line
  230) — after the manifest rewrite, call `write_run_report(...)` then
  `write_runs_index()`, each independently guarded.
- **Modified `src/loop_finalize.py`** (`_build_result_and_finalize`, ~line
  52) — after the final manifest write, call `write_run_report(...,
  status=loop_status, elapsed_ms=elapsed_total)` (stamps the freeze
  sentinel), then `write_runs_index()`.
- **Modified `src/loop_types.py`** — additive `StepOutcome.ended_ts: str =
  ""` (ISO UTC), set where outcomes are constructed in `loop_execute.py` /
  `loop_parallel.py`. Needed because `elapsed_ms` alone can't correctly
  position a step on a timeline: inter-step work (ralph verify, hooks,
  reflection, replans) isn't inside any step's `elapsed_ms`, so cumulative
  summation would drift and hide real gaps. With `ended_ts`, each segment is
  `[ended_ts - elapsed_ms, ended_ts]`. Old/missing data falls back to
  cumulative approximation, visually flagged as approximate.
- **New `tests/test_loop_report.py`** — synthetic `StepOutcome` lists under
  workspace-isolated tmp dirs. Cases: written while running vs. frozen at
  terminal; frozen file not rewritten on a later call; captain's-log entries
  with/without `loop_id` land in the right section; debug snapshots created
  only when the flag is on, cleaned when off; index links resolve relative
  to `runs_root()`; HTML-escaping of goal/step text (e.g. a step containing
  `<script>`).

## Data flow

```
loop_planning (decompose done)   loop_post_step (each step)   loop_finalize (terminal)
        |                               |                            |
        +-------------+-----------------+----------------------------+
                      v
        loop_report.write_run_report(project, loop_id, goal, planned_steps,
                                      start_ts, step_outcomes, status, ...)
                      |
   in-memory StepOutcome[]   captains_log.load_log() filtered      StepOutcome.call_record
   (status, tokens,          on loop_id (markers) / no loop_id     (path only, not read
    elapsed_ms, ended_ts,    + in-window (collapsed global block)  here — lazy detail tier)
    iteration, confidence)
                      v
        <artifact_dir>/loop-<loop_id>-report.html   (rewritten in place, frozen at terminal)
                      v
        loop_report.write_runs_index()
          scans runs_root()/*/metadata.json (+ run_card.json if present,
          + glob build/loop-*-report.html for the link target(s))
                      v
        <runs_root>/index.html
```

**Summary tier**: pure server-side rendering in Python, no client-side data
fetching. CSS-only horizontal bar (absolutely-positioned divs, `left`/`width`
as percentages of run duration), one segment per step colored by status,
badges for retries (`iteration > 0`), confidence, and captain's-log markers
(replans, escalations, fence trips, quality-gate verdicts) as point markers
on the timeline. Step table below reuses the same computation
`_write_plan_manifest` already does (done/blocked counts, tokens,
`metrics.estimate_cost`).

**Captain's-log markers**: `load_log()` filtered in Python on
`entry.get("loop_id") == loop_id`. Entries with no `loop_id` whose timestamp
falls in `[start_ts, now]` go into a collapsed `<details>` "Global context
(not attributed to this run)" block — visible on demand, never silently
dropped. Known limitation: `load_log()` reads the active file only; rotation
keeps roughly a 1000-entry tail, so an extremely long/busy run could lose its
earliest markers. Acceptable for v1 — `query_log()` is the escape hatch if
this ever matters in practice.

**Detail tier**: each step row carries a relative path to its
`call-NNNNN.json` (both live under `<run-dir>/build/`). Click handler
`fetch()`s the relative path and renders prompt/response inline. **Known
gap**: `fetch()` against a sibling file is blocked under a `file://` origin
in Chrome/Firefox (opaque-origin rule) — this only works over `http://`.
Fallback for the `file://` case: on fetch failure, render a plain `<a href>`
that opens the raw JSON directly in a new tab — preserves "nothing inlined
up front" in both viewing modes, just degrades to raw JSON instead of a
formatted panel when viewed directly off disk. The general-purpose viz
server (see BACKLOG.md) would remove this degradation entirely once it
exists; not required for v1. When a step has no `call_record` (record-mode
off, or a non-LLM step), the row says "no call record" instead of a dead
link.

**Index synopsis per run**: from `metadata.json` (goal text, lane, status,
started_at/ended_at), enriched from `run_card.json` when present, else from
`build/loop-*-log.json` totals. Runs with no report (pre-feature runs, or a
run that never hit a hook, e.g. non-project dispatches) are listed without a
link rather than skipped.

## Templating approach: stdlib only, no new dependency

Follows the `archive/observe_dashboard.py` precedent: inline HTML/CSS/JS
string templates, `html.escape()` on all interpolated text. No template
engine (Jinja2 et al.) — this is two single-page documents, a dependency
buys nothing here and this codebase is deliberately light on runtime deps in
this area. No charting library either — proportional CSS positioning is
sufficient for the Gantt view and renders identically from `file://`. JS
footprint is small: expand/collapse + lazy fetch with the `file://` fallback
above. No polling/auto-refresh loop is required — reloading the page is the
refresh mechanism for an in-flight run. An optional
`<meta http-equiv="refresh" content="30">`, emitted only while
`status == "running"`, is a cheap nicety and simply isn't emitted in the
frozen version.

## Paths and cross-linking

| Surface | Path (run-dir active — normal case) | Fallback (no run-dir) |
|---|---|---|
| Per-run report | `~/.maro/workspace/runs/<handle>-<nick>/build/loop-<loop_id>-report.html` | `~/.maro/workspace/projects/<slug>/artifacts/loop-<loop_id>-report.html` |
| Call records (detail) | same dir: `calls/call-NNNNN.json`, relative link | relativized via `os.path.relpath` |
| Debug snapshots | `.../build/loop-<loop_id>-report-debug/` | `.../artifacts/loop-<loop_id>-report-debug/` |
| Global index | `~/.maro/workspace/runs/index.html` | n/a — always at runs_root |

Cross-links are relative paths throughout (survive `scp`/`rsync` of the whole
workspace, work over `file://`): index → report is
`./<run-dir-name>/build/loop-<loop_id>-report.html`; report → index is a
relative link computed with `relpath`, only emitted when the report actually
lives under `runs_root()` (the projects/ fallback omits the backlink rather
than emit a broken one); report → plan manifest / loop log siblings are
same-directory relative links.

## Freeze semantics

- The finalize-path write embeds a sentinel as the first line:
  `<!-- maro-report: final status=<status> -->`.
- `write_run_report` reads the existing file's first ~256 bytes before
  writing; if the sentinel is present, it returns immediately without
  writing — idempotent freeze, cheap insurance against any late caller
  (ordering bugs, salvage/curation re-entry, a resumed process re-touching a
  finished loop).
- Crash-interrupted runs (checkpoint resume reuses the loop_id with status
  still `running`) correctly remain unfrozen and keep regenerating — matches
  the plan manifest's existing behavior.

## Build order

1. `src/loop_report.py` with `write_run_report` — **summary tier only**
   (timeline from StepOutcomes + step table + totals; no captain's-log
   markers, no detail panel yet), plus the `StepOutcome.ended_ts` field, the
   three call-site hooks, the freeze sentinel, and tests. This alone delivers
   the primary UX (decision 4).
2. Detail tier: `call_record` relative links + lazy fetch/fallback JS +
   per-step expand.
3. Captain's-log markers (loop_id-filtered decision points on the timeline +
   collapsed global-context section).
4. `write_runs_index()` + its call sites + report→index backlink.
5. Debug snapshot mode (flag, snapshot write, off-cleanup) — safe to defer
   last; developer-only.
6. Deferrable entirely, not required for v1: auto-refresh meta tag,
   mtime-cached index scan (avoid full `runs_root()` rescan every step at
   scale), `ended_ts` also added to `_write_loop_log`'s `steps` array for
   parity.

## Known gaps / accepted risk (carried forward, not blocking v1)

1. **`fetch()` under `file://`** — see "Detail tier" above. Degrades to a
   raw-JSON link when viewed directly off disk; full inline experience needs
   `http://` (either an ad hoc `python -m http.server` you start yourself, or
   the eventual general-purpose viz server in BACKLOG.md).
2. **loop_id vs. run dir is not strictly 1:1** — a run dir can contain
   sibling loop reports (fan-out/parallel-loop children, director replans
   producing a new loop_id in the same handle). The index's
   `build/loop-*-report.html` glob should list all matches under one run
   entry, not assume exactly one report per run.
3. **Index full rescan per step** — `write_runs_index()` scans all of
   `runs_root()` every time it's called (every step, across every active
   run). Fine at current scale; cheap future guard (mtime-based skip) is
   listed in build order as deferred.
4. **Captain's-log rotation** can drop a very long run's earliest markers
   from `load_log()`'s tail-limited read. Acceptable v1 tradeoff — the
   implementation's own cap was corrected 2026-07-08 to actually match this
   stated ~1000-entry limit (see review below; it had been a stricter 500).
5. **Index write ordering relative to `handle.py`'s later finalize steps**
   (2026-07-08 review, finding #3, second half): `metadata.json`'s terminal
   status and `run_card.json` (cost, success class) are written in
   `handle.py`'s `finally` block *after* `run_agent_loop()` returns —
   outside `loop_finalize.py`'s control entirely. The forced index write at
   loop-finalize time is correctly ordered relative to this run's own
   `loop-*-log.json` now (fixed — see review below), but it still can't see
   `run_card.json`/final `metadata.json` yet, since those don't exist until
   later in `handle.py`. The index entry for a just-finished run can show
   stale cost/status until the *next* run anywhere triggers a rescan (or a
   subsequent forced write). Properly closing this needs a hook in
   `handle.py` itself, after curation — a bigger, separate-module change,
   intentionally not bundled into this pass.
6. **Debug-snapshot cleanup on disable is per-loop-revisit, not a standalone
   sweep** (2026-07-08 review, finding #9): turning `report.debug_snapshots`
   off now reliably cleans up a loop's leftover snapshot dir the *next* time
   `write_run_report()` is called for that loop — including while
   `report.enabled` is also off (fixed 2026-07-08; previously the
   `report.enabled` check short-circuited before the cleanup ran at all).
   What's still true: if a loop's report is never written again after
   debug mode is turned off (the loop already finished, or reports are
   disabled going forward), its snapshot dir has no independent trigger to
   get cleaned. A standalone periodic sweep would close this fully but is
   more machinery than this developer-only debugging aid warrants —
   revisit only if debug-snapshot disk usage is ever actually a problem in
   practice.

## Explicitly out of scope for this build

- A live HTTP server for either surface (see BACKLOG.md "General-purpose
  visualization server" — separate, deferred decision).
- Any goal-submission or replay control surface (the exact thing that sank
  the old dashboard).
- A timeline/visualization aggregating multiple runs of the same project
  over time ("meta-of-meta" view) — discussed, deliberately deferred.
- Extending real per-step wall-clock timing into `_run_steps_parallel()`
  itself (parallel/fan-out workers don't currently report individual
  start/end times to the caller at all) — the 2026-07-08 review fix makes
  the timeline correctly flag these steps as approximate rather than
  fabricating false precision, but doesn't add the underlying
  instrumentation. That's a larger change to the parallel executor, not
  this report.
- Fixing the rest of `_build_result_and_finalize`'s side effects (telegram
  notify, introspection/diagnosis, Reflexion memory recording, skill
  crystallization) for parallel/DAG runs — `_run_parallel_path`'s early
  return skips *all* of these, not just the run-visibility report/index.
  The 2026-07-08 fix only wires the report/index back in, scoped to this
  feature; the broader gap (parallel runs get none of the rest of finalize)
  is a separate, pre-existing issue this build didn't introduce and isn't
  positioned to fix.

## Adversarial review (2026-07-08)

Ran `/adversarial-review` against the full implementation (all 6 build-order
stages) before merge, per Jeremy's request. 5 reviewers on the opposite model
(Codex `gpt-5.5`), each reading the actual repo files, not just the diff: the
3 default lenses (Skeptic, Architect, Minimalist) plus 2 project personas
added because this change touches shared runtime state in a system with other
agents actively working in it — **Plan Critic** (`personas/plan-critic.md`,
adapted post-hoc to check the implementation against this settled design
doc rather than its usual pre-flight role) and **Reality Checker**
(`personas/reality-checker-evidence-gate.md`, evidence-gating the specific
claims made about test results and guarantees — it independently ran the
test suite and tried to falsify the freeze/self-clean/no-server claims).

**Verdict: REJECT** (of the pre-fix state) — one high-severity finding had
unanimous consensus across every reviewer that read the execution-flow code.

**Findings and resolutions:**

1. **[high, unanimous — Skeptic/Architect/Minimalist] Parallel/DAG-mode runs
   never got a finalized report or forced index write.** `run_agent_loop()`
   returns directly from `_run_parallel_path()` (`agent_loop.py`), bypassing
   `_build_result_and_finalize()` entirely — where the terminal report/index
   hooks live. **Fixed**: `agent_loop.py` now writes the report (frozen,
   terminal status) and forces the index write right at the parallel-path
   early-return point, scoped narrowly to this feature (see "out of scope"
   above for what's deliberately not fixed alongside it). Regression-tested
   in `tests/test_agent_loop.py::test_parallel_path_still_writes_frozen_report_and_index`
   — verified to actually fail without the fix before confirming it passes
   with it.
2. **[high, Plan Critic + Reality Checker] Even with #1 fixed, parallel-path
   step timing was fabricated, not real.** DAG-batch outcomes assigned every
   step in a batch the same `elapsed_ms` (measured from batch start, not
   that step's own duration); fan-out outcomes passed no `elapsed_ms` at
   all. Combined with `ended_ts` defaulting to "now" at construction time,
   steps rendered as near-identical or near-zero-width segments — false
   precision, not the promised "real time window." **Fixed**: `ended_ts` on
   `StepOutcome`/`step_from_decompose()` is now a real optional sentinel —
   omitted (`None`) still defaults to "now" (correct at every call site that
   constructs the outcome immediately after the step finishes); passing
   `ended_ts=""` explicitly opts OUT of that default. Both parallel
   construction sites in `loop_parallel.py` and the checkpoint-resume
   reconstruction in `loop_planning.py` (see finding #5) now pass
   `ended_ts=""`, so `_step_windows()`'s existing approximate-mode fallback
   correctly covers them instead of fabricating precision. No attempt made
   to add real per-worker timing to the parallel executor itself — see
   "out of scope" above.
3. **[medium, Skeptic + Architect] The forced index write happened before
   the data it summarizes existed.** `loop_finalize.py` called
   `_write_runs_index(force=True)` *before* `_write_loop_log()` wrote that
   run's own totals. **Fixed** (the in-module half): reordered so the index
   write happens after the loop log write. **Not fixed** (the
   cross-module half): `metadata.json`/`run_card.json` finalize even later,
   in `handle.py`, outside this module's reach — documented as known gap #5
   above rather than rushed into a `handle.py` change in this pass.
4. **[medium, 4 of 5 reviewers] Shared static-file writes weren't safe
   across concurrent processes.** Plain `write_text()`, only an in-process
   lock/debounce dict — no cross-process serialization, no atomic replace.
   **Fixed**: both `write_run_report()` and `write_runs_index()` now hold
   `file_lock.locked_write()` (this codebase's existing tool for exactly
   this class of problem) across their entire check-then-write sequence,
   and write via a temp-file-then-`os.replace()` pattern for atomicity —
   closing both the concurrent-process race and the crash-mid-write /
   partial-read failure modes Reality Checker and Architect both raised.
5. **[medium, Architect + Minimalist] Checkpoint-resumed steps got a
   fabricated "real" timestamp instead of falling back to approximate.**
   Checkpoints don't persist `ended_ts`; resume reconstructed steps via
   `step_from_decompose()` long after they actually ran, but the factory's
   old unconditional "now" default made them look precisely timed. **Fixed**
   as part of finding #2's sentinel change — the checkpoint-resume call site
   now explicitly passes `ended_ts=""`.
6. **[medium, 3 of 5 reviewers] `report.enabled=false` didn't gate
   `write_runs_index()`.** Turning off the documented kill-switch stopped
   per-run reports but the index kept scanning/writing regardless. **Fixed**
   — `write_runs_index()` now checks `_reports_enabled()` too.
7. **[low-medium, Minimalist + Plan Critic] Client-side fetch-failure
   fallback built `innerHTML` from an unescaped `call_record` attribute
   value.** Not exploitable today (`call_record` is always an internally-
   generated `calls/call-NNNNN.json` path), but a boundary-hygiene gap given
   the server-side render escapes the same value correctly. **Fixed** — the
   fallback now builds its DOM via `createElement`/`createTextNode`/
   `setAttribute` instead of string-concatenated `innerHTML`.
8. **[low, Skeptic + Plan Critic] Captain's-log marker cap (500) was
   stricter than this doc's own accepted-risk framing (~1000-entry rotation
   tail).** **Fixed** — bumped `load_log(limit=...)` from 500 to 1000 to
   actually match what was documented.
9. **[low, Reality Checker] Debug-snapshot "self-cleans when toggled off"
   was narrower than documented** — see known gap #6 above for the fix
   applied and what remains true.
10. **[low, Skeptic] A naive (non-tz-aware) `ended_ts` would raise
    `TypeError` when compared against captain's-log's tz-aware timestamps**,
    silently failing the whole report write. Currently latent (every live
    call site produces tz-aware timestamps) but cheap to close structurally.
    **Fixed** — `_parse_iso()` now normalizes a naive datetime to UTC rather
    than leaving it to fail a comparison later.

**What reviewers found no issue with**: no new server/listener/control
surface was introduced (Reality Checker specifically tried to falsify this
and couldn't); the `file://` lazy-detail fallback works as designed; test
coverage for the sequential (non-parallel) path was solid going in.

All fixes covered by new tests in `tests/test_loop_report.py` and
`tests/test_agent_loop.py` (2026-07-08); full suite green except the same 8
pre-existing macOS/fcntl-timing failures noted in the original build (verified
unrelated, reproduce identically on pristine `main`). A second adversarial
review pass was run after these fixes — see BACKLOG.md / commit history for
that verdict.
