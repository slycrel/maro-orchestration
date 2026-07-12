---
status: dormant-design
---

# Backend-Error Resilience & Auto-Resume

**Status:** design pass for 1.0 arc item (h) (BACKLOG.md:672-688). Written
2026-07-09. No code changed by this pass. Settles four things: error
detect-and-classify, user messaging, resume semantics per lane, and
implementation slicing with a recommended minimum 1.0 slice.

Judgment calls are tagged `DECISION (provisional)` — greppable.

> **RATIFIED 2026-07-12 (Jeremy — GOAL_BRAIN Decisions 2026-07-12):** the
> four review-flagged decisions stand as written (auth/billing one-failover
> + always-notify; auto-resume cap 1/run; resume surface CLI-first with
> notify carrying the command; depth-cap inconsistency to be unified to one
> documented number — small chunk + tripwire, still open). Slices 1–3
> shipped 2026-07-09; auto-resume stays post-1.0.

## The problem, clearly

A stranger installs Maro, points it at a goal, and walks away. Some hours
later one of four things happens: their rate limit trips, their auth
expires, a prompt outgrows the context window, or the network blips. What
they find when they come back determines whether they ever run Maro again:

- **Today's best case:** the error was a 429/5xx on an API adapter and the
  retry ladder absorbed it (llm.py:260-287). The user never knows.
- **Today's common case (this box, empirically):** the subprocess adapter
  hung to its wall cap and the run died mid-step. `adapter_timeout` is the
  single most common critical failure class in the live diagnoses corpus —
  10 structured diagnoses, all "step blocked with 0 tokens after
  ~600-900s" (`~/.maro/workspace/memory/diagnoses.jsonl`). The concrete
  specimen: the hermes-trial goal-2 run died at step 7/10 and left
  `memory_bridge.py` wiring half-committed; a human had to finish steps
  7-10 by hand and fix three latent defects the half-run exposed
  (memory: project_memory_module_arc.md:18).
- **Today's worst case:** `claude -p` reports `"Not logged in · Please run
  /login"` — there are ~155 debug dumps of exactly this payload in
  `/tmp/claude_rc1_*.txt` spanning Jun 21 → Jul 9 on this box — and the
  run surfaces as a generic stuck/error with no hint that the fix is one
  login command. Meanwhile NEXT.md items strand in `[~] DOING` forever
  (heartbeat.py:688 sets DOING pre-run; nothing reverts it on a hard
  crash) and the checkpoint that could resume the run either wasn't
  persisted where resume looks or requires a manual parameter nobody
  passes (checkpoint.py:45-53; agent_loop.py:137).

The sharp edge is not that errors happen. It's that (a) the system can't
tell a "wait 90 seconds" error from a "run /login" error from a "top up
your credits" error, (b) when it gives up, the user gets a stuck run
instead of an instruction, and (c) interrupted work is not picked up —
not automatically, and not even manually via a documented command.

## What we already have (and why it's not the shape)

More exists than the failure stories suggest. The pieces are real; the
gaps are in classification granularity, persistence guarantees, and the
absence of any auto-resume trigger.

| Piece | What it does | Why it isn't the shape |
|---|---|---|
| `_retry_complete` (llm.py:260-287) | Same-backend exponential backoff 5/15/45s, 3 attempts, on `_is_retryable` errors | Only wraps the API adapters (e.g. AnthropicSDK at llm.py:1457, OpenAI-compat at llm.py:1555). Blind backoff — ignores `retry-after` and reset timestamps. |
| `_is_retryable` (llm.py:203-216) | Substring/type-name match: "429", "rate limit", "overloaded", "502/503/529", "timeout", "connection" | Two-class worldview (retry or not). OpenAI's `insufficient_quota` arrives as a 429 → matched as retryable → burns 65s of backoff on a permanent billing failure. |
| `_is_failover_error` (llm.py:219-246) | Substring match for 402/401/403/5xx/subprocess-failure → try next backend | Anthropic's credit-exhaustion error is a **400** with message "Your credit balance is too low…" — matches none of these substrings → propagates as fatal with no failover and no actionable message. Silent about *why* it failed over. |
| `FailoverAdapter` (llm.py:326-445) | Ordered adapter walk on failover errors; the record-mode capture seam (llm.py:413-430) | Only exists when ≥2 backends are available — single-backend users get a bare adapter (llm.py:1852-1853), so no failover *and* an inconsistent record seam. Failover is invisible to the user and to run metadata. |
| claude-CLI rate-limit loop (llm.py:1057-1124) | Polls structured `rate_limit_event` (llm.py:854-859), retries up to 6 cycles, total backoff cap 600s default | A weekly/session subscription limit ("resets Mon 12:00am") can't be waited out in 600s — the loop bails with a generic RuntimeError instead of deferring or failing over. Codex lane has **no** rate-limit recovery at all (llm.py:1235-1383; rc≠0 → RuntimeError at llm.py:1302-1306). |
| No-backend clean error (llm.py:1845-1850; handle.py:2196-2201) | "No LLM backend available. Set ANTHROPIC_API_KEY, … Tried backend_order=…" → stderr, exit 1 | The right pattern — but it exists for exactly one error. Auth expiry, billing, and context overrun all still surface as tracebacks or generic stuck_reasons. |
| Step-level catch (step_exec.py:891-919) | An LLM exception mid-step returns `{"status": "blocked", "stuck_reason": f"LLM call failed: {exc}"}` — the loop survives | The stuck_reason is a raw exception string. Classification is lost by the time anything user-facing sees it. |
| Checkpoints (checkpoint.py; loop_post_step.py:220-226) | Per-step `ckpt_<loop_id>.json` with full plan + completed[] outcomes; resume wired via `resume_from_loop_id` (agent_loop.py:137 → loop_planning.py:125-160); deleted on done (loop_finalize.py:251) | Three fatal gaps: (1) written to an env-dependent path (`orch_root()/checkpoints`, checkpoint.py:45-53) — 52 stale files sit in the repo checkout, **0** live ones in the workspace; (2) resume is opt-in and no caller outside auto-recovery ever passes `resume_from_loop_id`; (3) the in-flight step is invisible — a reader can't distinguish "step 7 not started" from "step 7 crashed mid-write". |
| Stale-claim recovery (task_store.py:215-223, 341-357) | Dead-PID claimed tasks reset to `queued`, inline and via sweep | Nothing calls the sweep on a schedule. And NEXT.md has no equivalent: a hard crash strands `[~] DOING` with no PID recorded and no revert (heartbeat.py:688 vs. the in-process-only exception handler at heartbeat.py:727-730). |
| Navigator fail-open (navigator_prompt.py:322-326; handle.py:2078-2082; loop_blocked.py:565-570) | Adapter outage in the navigator synthesizes `escalated_via: "idunno_chain"`; act-sites gate on it and fall through to the pipeline | This is the right resilience posture — advisory layers never block the line. It's cited here as the pattern, not a gap. Same for `advisor_call` fail-open (llm.py:1882-1943) — though it silently eats auth dropouts (see §2). |
| Restart/continuation ladders (handle.py:1419-1446, 1508-1551; loop_post_step.py:74-133; director.py:1114-1152) | Director restart, closure restart, continuation-depth respawn, escalation at cap | These handle *quality* failures (wrong approach, incomplete work), not *backend* failures. **RATIFIED + SHIPPED 2026-07-12** (MILESTONES -5 #3): unified to `loop_types.MAX_RESTART_DEPTH = 3`, replacing the previously-independent `MARO_MAX_CONTINUATION_DEPTH=4` default (loop_post_step.py:76), hard-coded `< 3` gates (handle.py:1423, 1516), and `director_budget_ceiling=2` (loop_types.py:281-282). `MARO_MAX_CONTINUATION_DEPTH` remains an intentional env override, now defaulting to the shared constant instead of its own magic number. |

The shape we haven't built: **a single classifier that turns any backend
error into one of a small set of classes, each with a fixed policy (how
to retry, whether to fail over, what to tell the user) — plus a durable,
findable checkpoint and one sweep that notices stranded work.**

## 1. Detect and classify

### The classes

Two classes (retryable / failover) are not enough. The evidence forces six:

| Class | Policy | Backoff/failover |
|---|---|---|
| `RETRY_BACKOFF` | Transient server/network fault. Retry same backend, exponential backoff. | Existing 5/15/45s ladder; on exhaustion → failover. |
| `RETRY_AT` | Rate/usage limit **with a known reset time** (`retry-after` header, `resetsAt` epoch, "resets 3:45pm"). Wait until T if T is near; otherwise treat as failover-then-defer. | New. Replaces blind 600s cap when a timestamp is available. |
| `FAILOVER` | This backend is down/degraded (persistent 5xx, subprocess binary missing, OpenRouter 502 model-down). Next backend immediately. | Existing FailoverAdapter walk. |
| `AUTH_ACTIONABLE` | Credentials invalid/expired. **Never retryable.** Fail over once if another backend exists, and always surface the exact fix command. | New surfacing; failover already partially matches (llm.py:224-232). |
| `BILLING_ACTIONABLE` | Credits/quota exhausted. Never retryable. Fail over if possible; surface "top up or switch backend". | New — today this class is misfiled (see traps). |
| `INPUT_TOO_LARGE` | Context/prompt overrun. Retry and failover are both useless (same prompt fails everywhere similar-sized). Bubble a distinct signal so the *caller* can shrink (step decomposition, summarize-context), else surface. | New — handled nowhere today. |

Anything unmatched stays `FATAL` (propagate with the raw message; the
current behavior, llm.py:434-436).

> **DECISION (provisional):** six classes, not two. Rationale: every class
> above has a *different* correct action, and the evidence shows real
> errors in all six; collapsing any pair reproduces a known failure
> (e.g. retrying `insufficient_quota`, failing over `prompt too long`).

### Classification table: real shapes → class

Every row below is a documented or empirically observed shape (sources:
Anthropic error docs; OpenAI/OpenRouter error docs; code.claude.com error
reference; live probes and `/tmp/claude_rc1_*.txt` dumps on this box).

**Anthropic Messages API** (envelope `{"type":"error","error":{"type":…,"message":…}}`; SDK typed exceptions):

| Shape | Class | Action |
|---|---|---|
| 429 `rate_limit_error` (`RateLimitError`) | `RETRY_AT` if `retry-after` header present, else `RETRY_BACKOFF` | Wait header seconds / ladder |
| 500 `api_error`, 504 `timeout_error`, 529 `overloaded_error`, `APIConnectionError`/`APITimeoutError` | `RETRY_BACKOFF` | Ladder → failover |
| 401 `authentication_error` | `AUTH_ACTIONABLE` | "ANTHROPIC_API_KEY is invalid or revoked — check the key, or unset it to use another backend." |
| 403 `permission_error` (check `error.type` for `billing_error`) | `AUTH_ACTIONABLE` / `BILLING_ACTIONABLE` by `.type` | Per class |
| **400 `invalid_request_error` + message prefix "Your credit balance is too low"** | `BILLING_ACTIONABLE` | **Trap #1.** A permanent billing failure disguised as a generic 400. Today: matches neither predicate → propagates as raw fatal (llm.py:219-246 has no matching substring). Must message-prefix-match. |
| 400 `invalid_request_error` + message prefix "prompt is too long" (e.g. "…208310 tokens > 200000 maximum") | `INPUT_TOO_LARGE` | Signal caller to shrink; never failover |
| 413 `request_too_large` | `INPUT_TOO_LARGE` | Same |
| Other 400/404/422 | `FATAL` | Propagate |

**OpenAI / OpenRouter** (via OpenAICompatAdapter — note errors currently
surface as `requests.HTTPError` text because of `raise_for_status()` at
llm.py:1552-1553, so today's classification is substring luck; the
classifier must parse the JSON body):

| Shape | Class | Action |
|---|---|---|
| OpenAI 429 `error.code: "rate_limit_exceeded"` | `RETRY_BACKOFF` | Ladder |
| **OpenAI 429 `error.code: "insufficient_quota"`** | `BILLING_ACTIONABLE` | **Trap #2.** Same HTTP status as a rate limit, permanent until billing fixed. Today `_is_retryable` matches "429" → burns the full retry ladder. Must branch on `error.code`. |
| OpenAI 400 `error.code: "context_length_exceeded"` | `INPUT_TOO_LARGE` | Shrink |
| OpenAI 401 `invalid_api_key` | `AUTH_ACTIONABLE` | "OPENAI_API_KEY invalid — check or rotate" |
| OpenAI 500 / 503 ("engine is currently overloaded") | `RETRY_BACKOFF` | Ladder |
| OpenRouter 402 ("This request requires more credits…") | `BILLING_ACTIONABLE` | "OpenRouter credits exhausted — top up or switch backend" |
| OpenRouter 401 | `AUTH_ACTIONABLE` | Key/OAuth fix |
| OpenRouter 408/429 | `RETRY_BACKOFF` | Ladder |
| OpenRouter 502 (model down) / 503 (no provider) | `FAILOVER` | Next backend (or reroute) |
| OpenRouter mid-stream error under HTTP 200 (`finish_reason: "error"`) | classify the payload | Status code alone is unusable mid-stream |

**claude -p CLI lane** (payload-first: `is_error` is the truth — the CLI
returns `subtype: "success"` with `is_error: true` on API failures,
verified live; extraction at llm.py:875-894, 1140-1148):

| Shape | Class | Action |
|---|---|---|
| `rate_limit_event` status `allowed_warning` | pre-warning | Log; optionally pause loop before the hard reject |
| `rate_limit_event` status ≠ allowed + `resetsAt` epoch (llm.py:854-859) | `RETRY_AT` | Wait to `resetsAt` if within cap; else failover/defer. Replaces the blind 600s total-cap bail (llm.py:1057-1124). |
| `result` prefix "You've hit your session/weekly/Opus limit · resets …" | `RETRY_AT` (far-future ⇒ failover/defer) | A weekly reset cannot be waited out in-process |
| `result` = "Not logged in · Please run /login" (155 live specimens in /tmp) | `AUTH_ACTIONABLE` | "Claude CLI session expired — run `claude` and then `/login`, or set ANTHROPIC_API_KEY" |
| `result` prefix "OAuth token revoked/expired · Please run /login" | `AUTH_ACTIONABLE` | Same |
| `api_error_status: 401` / "Invalid API key · Fix external API key" | `AUTH_ACTIONABLE` | Fix the external key |
| "Credit balance is too low" | `BILLING_ACTIONABLE` | Top up / switch |
| "API Error: 529 Overloaded…" (live specimen /tmp/claude_rc1_704618.txt — killed a planner call after 192s) | `RETRY_BACKOFF` | Ladder |
| "Prompt is too long" | `INPUT_TOO_LARGE` | Shrink |
| `TimeoutExpired` with `maro_kill_reason` (llm.py:739-743) — the #1 live failure (`adapter_timeout`, 0 tokens after 600-900s) | `FAILOVER` | Same prompt on the API lane is the documented mitigation ("Consider API adapter or smaller goals" — the diagnoses.jsonl recommendation string) |
| `FileNotFoundError` → "claude binary not found" (llm.py:1031-1032) | `FAILOVER` | Next backend |
| Codex CLI rc≠0 (llm.py:1302-1306) | classify stderr text; default `FAILOVER` | Codex lane gains rate-limit handling for free once classification is shared |

### Where the classifier lives

One function, `classify_error(exc, backend) -> ErrorClass` (new module
`src/llm_errors.py`), replacing the *internals* of `_is_retryable` and
`_is_failover_error` — the two predicates become one-line views over the
class (`RETRY_BACKOFF|RETRY_AT` ⇒ retryable; `FAILOVER|AUTH|BILLING` ⇒
failover-eligible), so `_retry_complete` and `FailoverAdapter` keep their
call shape. Classification inputs are (SDK exception type, HTTP status,
`error.type`/`error.code`, message **prefix**) — never exact-match:
the CLI strings have already changed between versions ("Invalid API key ·
Please run /login" → "· Fix external API key"), and the Anthropic docs
warn the type list grows.

> **DECISION (provisional):** auth and billing errors trigger **one**
> failover attempt (if another backend is available) and *always* emit a
> user-actionable event, even when failover succeeds. Rationale: silently
> absorbing "your Anthropic auth is dead" behind an OpenRouter failover
> means the user discovers it weeks later as a surprise bill or when the
> last backend dies; the run should succeed *and* the user should be told.

> **DECISION (provisional):** `advisor_call` stays fail-open (llm.py:
> 1882-1943) but counts consecutive failures and emits one notify event
> past a threshold (~5). Rationale: the 155 "Not logged in" /tmp dumps are
> largely silent advisor-lane failures; fail-open is right per-call, but a
> dead advisor lane for 18 days is a detection failure.

## 2. User messaging

The pattern is the no-backend fix: `build_adapter()` raises a RuntimeError
with an actionable sentence; `handle.main()` catches it and prints
`Error: …` to stderr, exit 1 — no traceback (handle.py:2196-2201, message
at llm.py:1845-1850). Generalize exactly that: a `BackendError` exception
type carrying `(error_class, backend, user_action, detail)`, raised when
the FailoverAdapter walk exhausts or a non-recoverable class surfaces.

**Message registry** (one place, `llm_errors.py`; prefix-matched inputs →
fixed outputs). The action strings say *exactly what to run*:

| Class | Message template |
|---|---|
| `AUTH_ACTIONABLE` (CLI session) | `Claude CLI is not logged in. Run 'claude' then '/login' on this machine, or set ANTHROPIC_API_KEY to use the API directly.` |
| `AUTH_ACTIONABLE` (API key) | `{ENV_VAR} was rejected (401). Check the key in your environment/config, or unset it to fall back to: {remaining backends}.` |
| `BILLING_ACTIONABLE` | `{Backend} credits/quota exhausted (not a rate limit — waiting will not help). Top up, or set {other backend env var}.` |
| `RETRY_AT` (deferred past cap) | `{Backend} usage limit resets at {local time}. Run deferred; it will be retried after reset.` (once auto-resume exists) or `…retry after {time} with: maro resume {run}` |
| `INPUT_TOO_LARGE` | `Step {n} exceeded the model's context window ({detail}). The step needs to be split or its inputs summarized; re-run with a narrower goal.` |
| `FAILOVER` (succeeded) | one-line notice, not an error: `Note: {backend} unavailable ({reason}); continued on {next}.` |

**Where messages surface** (all four, from one emit point):

1. **CLI stderr** — `handle.main()` catch widens from RuntimeError to
   BackendError with the same print-and-exit-1 shape (handle.py:2196-2201).
2. **Run metadata + run card** — the finalize path already stamps
   status/ended_at (runs.py:250-253, via handle.py:542-560) and curates a
   card (handle.py:566-568). Add `backend_error: {class, backend,
   user_action}` to metadata so `status: "error"` runs say *why* and *what
   to do*. New terminal status value `blocked_backend` distinguishes "your
   code/goal failed" from "your credentials failed".
3. **Notify channel** — `notify.emit` already fires `run_completed`
   (handle.py:583-590); add `backend_actionable` as an event type so a
   headless box pings the user's channel with the fix command. This is the
   only surface an away-from-keyboard user actually sees.
4. **maro-doctor** — `detect_backends()` (llm.py:1704-1731) already keeps
   doctor and runtime in agreement on *presence*. Extend doctor with a
   *liveness* probe per available backend (1-token/haiku-tier call) so
   "installed but not logged in" — invisible to presence checks, the exact
   state this box sat in — is caught before a run burns an hour on it.

> **DECISION (provisional):** step-level failures keep the run alive as
> today (blocked → stuck, step_exec.py:891-919) but the blocked outcome
> carries the structured class, not a raw `f"LLM call failed: {exc}"`
> string, so stuck_reason/diagnoses/notify can render the action.
> Rationale: today's diagnoses corpus had to *infer* adapter_timeout from
> "0 tokens after 600491ms"; the kill reason existed upstream and was
> stringified away.

## 3. Resume semantics

"Pick up where it died" means different things per lane, because each lane
persists different state.

### What exists on disk today vs. what's missing

| State | Exists | Gap |
|---|---|---|
| Goal text, lane, model, status | `metadata.json` per run dir (runs.py:157-202); goal also in `source/prompt.txt` (runs.py:112-115) | Status is only closed by `finalize_run` (runs.py:233-259) — a hard crash leaves it unset; no "crashed" is distinguishable from "running" without a PID |
| Full plan + per-step outcomes | checkpoint `ckpt_<loop_id>.json`: `steps[]` + `completed[]` with index/status/result/tokens (checkpoint.py:142-186); corroborated by `build/loop-<id>-log.json` + scratchpad | (1) env-dependent path — `orch_root()/checkpoints` resolves differently per env (checkpoint.py:45-53): 52 stale files in the repo, 0 in the live workspace; (2) deleted on done (loop_finalize.py:251) but *also* not reliably present after crashes; (3) no linkage to the run dir/handle_id; (4) call-record seq counter is in-memory (runs.py:350-371) — resets after crash |
| The in-flight step | — | **Missing entirely.** `write_checkpoint` records only completed steps; "6 done, 7+ remaining" cannot distinguish "7 not started" from "7 crashed mid-write". This is precisely the hermes goal-2 wound: step 7's partial side effects were invisible. |
| Queue item state | task_store: `queued/claimed/done/failed` + `claimed_by_pid` + dead-PID recovery (task_store.py:35, 215-223, 341-357) | Sweep (`recover_stale_claims`) has no scheduled caller |
| NEXT.md item state | `[ ]/[~]/[x]/[!]` under locked_write (orch_items.py:21-24, 480-494) | Hard crash strands `[~] DOING` — DOING is set pre-run (heartbeat.py:688; orch.py:295) with no PID recorded and no crash-time revert; `orch.finalize_run` then *refuses* to close a non-DOING item (orch.py:329-330), making stranded DOING a leaked lock |
| Continuation context | `continuation_depth` on tasks (task_store.py:59,73) and restart flows (handle.py:1419-1551) | Quality-loop machinery; carries nothing about backend death |

### Resume unit, per lane

> **DECISION (provisional):** the resume unit is the **step** within a
> loop (via checkpoint), and the **task/item** at the queue layer.
> Run-level restart-from-scratch is the fallback when no checkpoint
> exists. Rationale: the checkpoint format already supports step-resume
> (`remaining_steps`/`next_step_index`, checkpoint.py:90-105) and the
> plumbing threads all the way through (`resume_from_loop_id`,
> agent_loop.py:137 → loop_planning.py:125-160, which replays completed
> steps as context and logs "resuming from checkpoint"). Building a finer
> unit (intra-step) has no state to stand on.

- **NOW lane** (handle.py:829-830): stateless, seconds-long, user is
  present. **No auto-resume.** Correct behavior is the §2 actionable
  error, immediately. > **DECISION (provisional):** NOW-lane resume is
  explicitly out of scope. Rationale: re-issuing a NOW message costs the
  user one keystroke; resume machinery costs a lane's worth of complexity.
- **Agenda/loop lane** (handle.py:993, 1411): resume = load checkpoint,
  skip `completed[]`, **re-execute the in-flight step from scratch**, then
  continue. Requires the checkpoint substrate fixed (below).
- **Project loop / restarts** (handle.py:1419-1551): unchanged — these are
  quality restarts. A backend death inside one resumes like any agenda
  loop; `continuation_depth` is not consumed by a backend resume (a crash
  is not a quality signal). The three unrelated caps (4 / <3 / 2 — see
  table §"What we already have") were unified to one shared constant,
  `loop_types.MAX_RESTART_DEPTH = 3`, **RATIFIED + SHIPPED 2026-07-12** —
  the value moved (director_budget_ceiling 2→3, continuation default
  4→3), it was not left unchanged; "one documented number" was the point.
- **Queue/drain lane** (heartbeat.py:687-732): resume = a **stranded-state
  sweep** on the heartbeat tick: (1) call `recover_stale_claims()`;
  (2) revert NEXT.md `[~] DOING` items whose recorded PID is dead to
  `[ ] TODO` (mirroring the deliberate `refused_busy` revert at
  heartbeat.py:709-712) — which requires stamping the PID at DOING time;
  (3) surface orphaned checkpoints (checkpoint exists + no live PID + run
  metadata not finalized) as resumable, via notify.

### Idempotency — the half-committed problem

The hermes goal-2 specimen is the spec: step 7 died mid-write, and naive
re-execution risks double-applying whatever step 7 half-did.

> **DECISION (provisional):** steps are **at-least-once**; resume always
> re-executes the in-flight step from the top, and the system's job is to
> make re-execution *safe*, not to replay partial work. Three guards, all
> building on existing seams:
> 1. **In-flight marker**: `write_checkpoint` gains an `in_flight: {index,
>    started_at, pid}` field written *before* step execution, so resume
>    knows step 7 may have partial side effects (vs. never-started).
> 2. **FS-diff on resume**: the fabrication guard `artifact_check.py`
>    already diffs claimed vs. actual filesystem effects (memory:
>    project_done_vs_achieved_fixes); run the same diff over the in-flight
>    step's window and inject the result into the re-executed step's
>    context ("step 7 previously ran partially; these files changed: …").
>    The model completes idempotently instead of blindly redoing.
> 3. **Worktree isolation as the rollback seam**: runs under
>    `busy_policy=worktree` (loop_init.py:291-311) already execute in a
>    disposable copy — a crashed worktree run can be resumed clean.
>
> Rationale: exactly-once step semantics would require transactional
> filesystem+git effects — not buildable here; informed at-least-once
> matches what the human recoverer of goal-2 actually did (read what
> landed, then finish it), and one of goal-2's three defects (random ids →
> deterministic sha1 for idempotent re-ingest) shows steps can and should
> be nudged toward idempotent design rather than wrapped in transactions.

### The auto-resume trigger

> **DECISION (provisional):** auto-resume is the heartbeat sweep
> **requeueing** an orphaned run as an agenda task carrying
> `resume_from_loop_id`, capped at 1 auto-resume per run (a
> `resume_count` in metadata); a second death surfaces to the user via
> notify instead. Manual resume ships first as `maro resume <run|loop_id>`
> — the plumbing already exists end-to-end and only lacks a caller
> (agent_loop.py:137 has no external caller today). `RETRY_AT` deferrals
> reuse the same mechanism: park the task with a `not_before` timestamp,
> drain skips it until then. Rationale: one cap prevents crash-loops
> (memory: rogue-process and self-rearming-loop history on this box makes
> unbounded auto-retry a named hazard); manual-first proves the resume
> path on real corpses before automating it.

## 4. Implementation slicing

Ordered one-session chunks, cheapest-highest-value first. Each is
independently shippable and testable.

**Slice 1 — Classify + message (the 1.0 core).**
`src/llm_errors.py`: six-class `classify_error`, message registry,
`BackendError`. Rewire `_is_retryable`/`_is_failover_error` as views over
it (no policy change except: stop retrying `insufficient_quota` and
credit-exhaustion-400 — the two traps). Widen handle.main's catch;
stamp `backend_error` into finalize metadata; add the `backend_actionable`
notify event; carry the class into step blocked-outcomes
(step_exec.py:913-919). Tests: table-driven over the classification rows
above (the /tmp payloads are free fixtures).

**Slice 2 — Checkpoint substrate fix.**
Move checkpoints into the run dir (`build/checkpoint.json`), keyed to
handle_id + loop_id; keep on crash, delete only on finalized `done`;
add the `in_flight` field; rebuild the call-record seq counter from disk
(runs.py:350-371). Migration: read old `orch_root()/checkpoints` path as
fallback for one release. Tests: kill -9 a loop mid-step, assert
checkpoint present + in_flight correct.

**Slice 3 — Stranded-state sweep + manual resume + doctor liveness.**
PID stamped alongside `[~] DOING`; heartbeat-tick sweep (stale claims,
dead-DOING revert, orphaned-checkpoint notify); `maro resume <run>`
calling `run_agent_loop(resume_from_loop_id=…)` with the FS-diff context
injection; doctor gains the per-backend liveness probe (catches "installed
but not logged in"). Tests: strand a DOING with a dead PID, tick, assert
revert; resume a slice-2 corpse end-to-end.

**Slice 4 — RETRY_AT + codex parity.**
Timestamp-aware deferral: honor `retry-after` and `resetsAt` (near-future
⇒ wait; far ⇒ failover or park with `not_before`), replacing the blind
600s bail (llm.py:1057-1124); codex lane routes rc≠0 stderr through the
classifier (llm.py:1302-1306), gaining retry/failover for free.

**Slice 5 — Auto-resume.**
Heartbeat requeues orphaned runs with `resume_from_loop_id`,
`resume_count` cap = 1, `not_before` park/drain for deferrals. Requires
slices 2-4 proven on real corpses.

**Slice 6 — Failover polish.**
Always wrap in FailoverAdapter even with one backend (fixes the
record-seam inconsistency, llm.py:1852-1853); failover events in run
metadata; the advisor consecutive-failure counter.

> **DECISION (provisional): minimum 1.0 slice = 1 + 2 + 3.** After these,
> no error strands silently: every backend death yields an actionable
> message on all four surfaces, a durable checkpoint, a clean queue state,
> and a one-command resume — and doctor catches dead auth before a run
> does. Slices 4-6 are post-1.0: auto-resume (5) without a proven manual
> path risks exactly the crash-loop failure mode this box has already
> lived through, and RETRY_AT (4) optimizes waits that slices 1-3 already
> make survivable. Rationale for cutting here: the enthusiasm-killer named
> in item (h) is the *silent* death + no path back, not the suboptimal
> backoff.

## What not to build yet

- Exactly-once / transactional step effects. At-least-once with informed
  re-execution is the honest contract.
- Mid-stream failover (resuming a half-streamed completion on another
  backend). OpenRouter's own docs concede this is impossible mid-stream;
  the step is the retry unit.
- A budget/cost circuit-breaker. Real gap (arch-platform.md:116 names it)
  but a different design; billing *errors* are handled here, spend
  *prevention* is not.
- Automatic `.maro-failed` marking. Markers are deliberately manual
  (sheriff.py:109-112); auto-failing projects on backend errors would
  conflate "backend died" with "project is bad".

## What I'd want Jeremy to push back on

- Is one auto-resume (slice 5's cap) too timid, or exactly right given the
  self-rearming-loop history? The alternative is a small decay ladder
  (resume at +5m, +1h, then surface).
- Should `BILLING_ACTIONABLE` failover be *default-on*? It silently moves
  spend to a different paid backend — arguably that needs the same "ask
  first" posture as spending money.
- Is `maro resume` the right surface, or should resume hang off the run
  card / notify message ("reply 'resume' to continue") once the Slack
  bridge is in the loop?
- ~~The three depth caps: fine to just document-and-name in 1.0, or is the
  inconsistency itself a pre-1.0 bug?~~ **RESOLVED 2026-07-12** — Jeremy
  ratified unifying to one documented number; shipped as
  `loop_types.MAX_RESTART_DEPTH = 3` (MILESTONES -5 #3).
