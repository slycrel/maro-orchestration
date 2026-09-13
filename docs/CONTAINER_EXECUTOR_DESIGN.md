---
status: dormant-design
---

# Containerized executor — design pass

**Status:** design pass, written 2026-07-12 (Fable-handoff session). Fulfills
arch-r2-01 (r2 1.0-blocker #4: "the containerized-executor decision has no
vehicle"). Implements the decree recorded in `SECURITY_MODEL.md` §2 /
GOAL_BRAIN Decisions 2026-07-09:

> "Play nice with security here and dockerize this path so there's literally
> no way to screw things up. Mount a working dir and maybe make some other
> resources read only... a nice tight sandbox is likely appropriate."

No code changed by this pass. Judgment calls tagged `DECISION (provisional)`
— greppable. File:line references verified at commit ffff3f6.

**Standing constraint (restated from decision time):** the container is
filesystem/network isolation, NOT a lever for working around the CLI's
intended operation. No PTY-driven prompt-acceptance tricks; stay on the
right side of the API-vs-CLI automation line.

---

## 1. What gets containerized (scope)

**The agentic executor lane only**: subprocess calls that carry real tools —
worker step execution through `ClaudeSubprocessAdapter` (`src/llm.py:1181`)
where the command is `claude -p ... --dangerously-skip-permissions`
(`src/llm.py:1223-1234`).

Explicitly NOT in the first slice:

- **Utility calls** (`no_tools=True` → `--tools ""`, llm.py:1232 — routing,
  classification): zero tools means nothing to contain, and they're the
  boot-tax-sensitive calls (the 2.7s trivial-call floor). Host execution
  stays.
- **Closure probes** (`closure_verify.py:648` `subprocess.run` of
  verifier-authored shell) — a different trust story (we authored the
  command, not the worker); containerizing them is a natural follow-on once
  the executor image exists, but rides the probe-env hardening chunk
  (`docs/history/2026-07-12-routing-and-probe-synthesis-design.md` B3), not this one.
- **CodexCLIAdapter** (`src/llm.py:1463`): same seam, same wrap applies
  later; out of default backend order today, out of scope now.

## 2. The integration seam

One seam: `_run_subprocess_safe` (`src/llm.py:636-912`). When
`executor.container` is enabled and the call is an executor call (not
`no_tools`), the command vector is wrapped:

```
docker run --rm --name maro-exec-<loop_id>-<seq>
  --user <uid>:<gid>
  --init
  <mounts — §4>
  --network <executor.container_network>
  -e MARO_WORKER_RUN=1 [-e MARO_ALLOW_MAIN_PUSH=1]
  -w <fence-dir-as-container-path>
  <executor.container_image>
  claude -p ... (unchanged flags)
```

Everything `_run_subprocess_safe` already does survives, because it operates
on the *client* process's stdout/lifetime:

- **stream-json parsing, tool_events, cost probe** — the container's stdout
  is the docker client's stdout; `_parse_stream_json` (llm.py:1015-1088) and
  the runaway stream probe (llm.py:915-974) see identical bytes.
- **Liveness** — output-file mtime signal unchanged. The session-CPU
  liveness signal (`ps`+session inspection) reads the *client* process and
  will under-report container CPU: acceptable (mtime is the primary signal);
  note it in the implementation, don't build container-stat plumbing for it.
- **Kill path — the one real change.** SIGTERM/SIGKILL via `os.killpg`
  (llm.py:875-882) kills the docker *client*, which does NOT reliably kill
  the container. The wrap must kill by name: `docker kill
  maro-exec-<loop_id>-<seq>` first, then killpg the client. The `--rm` +
  deterministic `--name` makes stranded-container sweep trivial (`docker ps
  --filter name=maro-exec-` in the stranded-state sweep).
- **Env**: today the child inherits the full host env (llm.py:775-782).
  Inside the container this inheritance is *dropped by construction* — only
  the explicitly passed `-e` vars exist. That is a feature (secrets in the
  operator's env stop leaking into worker reach), but it will surface any
  undocumented env dependency; burn-in watches for it.

> **DECISION (provisional):** wrap at `_run_subprocess_safe`, not a new
> adapter class. Rationale: every hard-won behavior (liveness, rate-limit
> retry, payload-first rc handling, symlink, stream probe) lives in that
> function; a parallel `ContainerizedAdapter` would fork all of it. The wrap
> is ~40 lines + a kill-path branch.

## 3. Image + auth — including the trap

### Image

`deploy/docker/Dockerfile.executor` (new; the repo-root `Dockerfile` +
`docker-compose.yml` are pre-rename artifacts — `POE_WORKSPACE`, the
crash-looping `sheriff.py --heartbeat` service — **replace-not-extend**;
delete them in this arc's docs chunk). Contents: node LTS slim base +
`npm install -g @anthropic-ai/claude-code` (pinned version, documented
rebuild command) + `git` + `python3` + coreutils/curl. That's the toolset
worker transcripts actually use.

> **DECISION (provisional):** bake the CLI into the image rather than
> mounting the host binary (which the 2026-07-09 docker trial did). Baked =
> reproducible, no host-path assumptions, image version is auditable.
> Fallback for hosts where the image can't be built: mount the host binary —
> proven in the trial, documented as the degraded mode.

**Per-project layers (2026-09-07, `docs/ENV_REQUEST_DESIGN.md`):** the base
image above stays fixed; a run that lacks a tool requests it by file and the
engine builds `maro-executor:p-<project>-l<N>-<cli>-r<rev>` from a generated
Dockerfile (`FROM <base>` + package lines) under
`<workspace>/executor-layers/<project>/`. Root at build time only; the
runtime `--user` line is unchanged. `llm._run_subprocess_safe` picks the
project image when one exists for the current base.

### Auth — the trap, named

The claude CLI's OAuth state lives under `~/.claude` and the CLI **writes**
there (token refresh, session files, settings). The obvious move — mount the
host's `~/.claude` into the container — is **an escape vector, not a
convenience**: host-side `~/.claude/settings.json` supports hooks and
config that execute when the *operator* next runs claude on the host. A
prompt-injected worker with rw access to host `~/.claude` can plant exactly
that. Mounting it read-only instead breaks token refresh mid-run.

> **DECISION (provisional): dedicated container auth volume.** A named
> docker volume (`maro-claude-auth`) mounted at the container's `~/.claude`,
> initialized once by the operator: `docker run -it -v maro-claude-auth:...
> <image> claude /login` (printed by `maro-bootstrap container-setup`,
> hook-instructions posture — same as the supervision story). Token refresh
> persists inside the volume; the container never touches host auth state;
> revoking the container's session revokes nothing else. Doctor gains a row
> that probes login state *through the container* (cheap `claude -p` "ok"
> with `--tools ""`). This is a second OAuth session on the operator's
> account — same subscription, same quota, same ToS posture as the host lane
> (README caveat added 2026-07-12 covers it).

**Auth breaker (2026-08-13).** The volume's session can die independently of
docker — it did on 08-12 (seeded 07-14, refresh expired), and because the
degrade path only asked "is docker up?", every executor step entered the
container and died on "OAuth session expired", until a human flipped
`executor.container` off by hand. Auth failure is now a degrade condition
with the same on/require contract as docker-down, implemented as a reactive
breaker (`container_exec.py`, "Container auth breaker" section): the first
containerized call failing with a CLI login-failure signature trips it
(one `backend_actionable` notify with the re-seed instructions), after which
`on` degrades to host/fence-only and `require` refuses at resolve time. No
happy-path probe spend. Reset is automatic and cheap — while tripped, at
most every 5 minutes one docker `cat` of the volume's credentials file; the
breaker clears only for a live-shaped file (refreshToken present) *newer
than the trip*, i.e. an actual operator re-seed — a server-side revocation
with an intact-looking file stays tripped instead of flapping. State is a
file (`memory/container_auth_breaker.json`) so all processes share one
breaker; doctor shows a "Container auth breaker" row and system_health
carries a `container_auth` liveness row (SILENT while tripped). Operator
tip learned during the 08-13 re-seed: the interactive `/login` URL
truncates when the TUI wraps it at terminal width — `stty cols 400` first,
or de-wrap the copied URL in an editor.

**Recurred 2026-09-12.** The volume re-seeded 2026-08-13 expired again
(~30 days: the refresh token's lifetime, so expect this monthly until a
liveness probe or a re-seed cadence exists). Run 68cbde81 paused
`llm-unreachable` (honest); 90 s later the breaker had tripped and run
154ec06a's steps ran on the HOST under `on`'s degrade — where the worker
decrypted the secrets store with the age identity instead of reading the
hand-off file (`docs/SECRETS_DESIGN.md` §9). Two conclusions: `on`'s
degrade-to-host was designed when the box held no store worth protecting
and is now the wrong default — `require` (refuse → typed pause) is the
recommended setting; and the breaker is reactive by design ("liveness is
not probed"), so the first casualty of every expiry is a real run. Re-seed
recipe unchanged: interactive `/login` inside the container (`stty cols
400` first so the URL survives the TUI wrap).

**`require` on the runtime box (2026-09-13, Jeremy: "let's add the require
lane then test it with a re-auth").** Flipped `executor.container: require`
in the live config while the breaker was still tripped and proved the
refusal path first (`resolve_container_run(executor=True)` →
`ContainerUnavailable: executor.container=require but the container lane is
unavailable: container auth breaker tripped (...)`) — no host degrade
possible any more. Jeremy re-seeded the volume by interactive `/login`
(07:17Z; note `claude /login` always starts a fresh login and never checks
for an existing one, so a second invocation prompts again — harmless, Ctrl-C
out). The breaker cleared on its own at the next resolve (credentials file
live-shaped and newer than the trip; state file removed), a direct
`claude -p` probe in the image answered, and run 520e1b8c (the same IMAP
inbox check as 154ec06a) executed all seven executor calls in
`maro-exec-520e1b8c-<pid>-{0..6}` containers: uid 1001, project-layer
image `p-weve-used-chrome-on-the-l3`, auth volume mounted, `YAHOO_*` names
arriving through the container env (no `secrets.env` hand-off file in the
run's scratch — the host path was never taken). Same deliverable as the
host-lane run (32 messages, five newest) at $2.70. Best evidence for the
wall: step 2's worker went looking for the store and the sops/age tooling
(the project's own `docs/mail_yahoo.md`, written by the 09-12 host-lane
worker, still describes a "sops fallback") and found neither — the
container mounts no `~/.maro/secrets` and ships no `sops`/`age`. Wording
is a fence; the container is the wall. Residuals unchanged: typed pause
`container-auth-expired` (today `require` surfaces as `ContainerUnavailable`
→ the generic environmental pause), and an auth liveness probe so the
monthly expiry is caught before a run is.

**Both residuals closed the same day (2026-09-13).** (a) *Typed pause.* A
`require` refusal caused by the breaker now raises
`ContainerAuthExpired(ContainerUnavailable)` carrying the type marker
`maro_error_class = "container_auth"`; `llm_errors.classify_error` keys on
the marker (never on text — a worker step that merely mentions auth cannot
ride it) and returns a pause-shaped policy (no retry, no failover: the API
lane would run the worker *outside* the container the contract demands);
`stop_verdicts.pause_reason_for_error_class` maps it to the new
`container-auth-expired` reason, so loop_execute ends the run `interrupted`
with the typed pause on the first refusal — one step, no blocked-step churn
— and the continuation lane's resume test accepts it once the operator
re-seeds and the breaker self-clears (on the first executor call after its
300 s recheck cadence; shape-only when the volume's expiry is unknown).
Before this the refusal was
classified FATAL/auth by text and the run churned retries. Docker-down keeps
the base `ContainerUnavailable` and its old handling. (b) *Liveness.* The
breaker stays reactive, but the session's own end is knowable in advance:
the volume's credentials carry `refreshTokenExpiresAt` — the ~30-day
lifetime whose end IS the monthly outage (the access token is refreshed by
every run). The heartbeat records it on its own cadence
(`container_exec.refresh_auth_liveness`: one read-only docker run per 6 h,
**timestamps only** — credential bytes never transit to the host, same rule
as the re-seed probe — into `memory/container_auth_liveness.json`), and the
`container_auth` health probe reads that file (no docker in a probe) and
goes SILENT — captain's log + the health card — while there are ≤ 3 days
left (`AUTH_EXPIRY_WARN_DAYS`: covers a weekend, is not standing noise the
other 27 days) or the token is gone. Live on the runtime box: "refresh token
valid until 2026-10-12 04:21Z (29 d)". Doctor's `--live` login probe is
unchanged (it spends a token; the record does not).

*Review round 1 (2026-09-13, codex ×4) found the typed pause unwired on
the literal path and the recorder too trusting; both fixed before landing.*
(i) The worker's real adapter stack is `FailoverAdapter([ClaudeSubprocess…])`,
and the wrapper re-raises an actionable failure as `BackendError(info)` —
re-classifying the *wrapper's text* lost the marker and the refusal came
back as a generic auth failure. `classify_error` now returns a
`BackendError`'s own `ErrorInfo` first (the wrapper already decided);
`test_pause_reasons.py::TestContainerAuthPauseThroughTheRealWrapper` pins the
literal composition (no subprocess launched, no circuit trip, no host
`/login` alert). (ii) The fan-out/DAG path turned a blocked `container_auth`
outcome into `stuck` and the batch path only logged it; every path now
consults one seam, `stop_verdicts.environmental_pause_for(outcome)`, stamps
`ctx.pause_reason`, and ends the loop `interrupted` — the sequential driver
also stops scheduling after a paused batch. (iii) `_reseed_probe` counted
`refreshToken` key *text* (`grep -c`), so a wiped file keeping
`"refreshToken": null` read as re-seeded; both the re-seed probe and the
liveness recorder now share one exact-field reader (a python one-liner in
the read-only container printing four integers: mtime, refresh-present
flag, the two expiries; never a traceback that could carry bytes). The host
parser accepts exactly that frame and nothing else. (iv) The liveness record
is validated on read (`_valid_liveness_record`: real bools, finite bounded
timestamps, `checked_at` not in the future) and read as *stale* — verdict
`unknown`, not `ok` — after 48 h without a refresh; a failed probe keeps the
prior good sample as `last_good` so a docker outage cannot narrate
`SUBSYSTEM_RECOVERED` over a live expiry warning; the refresh runs under the
record's lock so overlapping heartbeats launch one container, and a failed
persist is a logged warning, not a silent `None`.

*Review round 2 (2026-09-13, codex skeptic + QA on the whole chunk) attacked
the fixes and found three more HIGHs on the pause's path, all fixed before
landing.* (v) The tool_search re-call's handler sits *outside* the initial
call's `except`, so the round-1 "re-raise into the outer handler" escaped
`execute_step` (uncaught on the sequential driver, stringified by the
fan-out pool). One helper, `step_exec._blocked_outcome_from_exc`, now builds
the typed blocked outcome for both adapter calls, keeping the first call's
spend. (vi) The fan-out/DAG early return in `agent_loop` bypasses
`loop_finalize`, whose stop-verdict stamp is the ONLY writer of
`metadata.pause_reason` — and `handle_queue`'s strict-affirmative resume
test reads exactly that, so a paused parallel run restarted under a new
identity. The early return now stamps the pause with the same writer.
(vii) The real schedulers kept going: `_run_steps_dag` released dependents
regardless of the dep's outcome and `_run_steps_parallel` queued every step
upfront, so a refusal walked the whole graph before the caller saw it. A
halt flag set *in the worker* (a pool thread picks its next queued task
before the main thread sees the refusal) makes every later task return a
"not started — environmental pause" outcome without an adapter call; the
DAG submits nothing further; in-flight peers drain. Mediums: the health
probe maps an *unknown* liveness verdict (never recorded, stale, or a
failed probe with nothing good to fall back on) to `UNKNOWN`, not `OK` —
`run_health_probes` narrates RECOVERED on OK-after-SILENT, so "we lost sight
of it" was being told as "healed"; the recorder takes its lock with
`require=True` (under `MARO_FILELOCK_FAIL_OPEN` the default contract runs
unlocked — the overlap race in the fix's clothes) and leaves the record
alone when the lock is busy; `_reseed_probe` rejects a rewritten file whose
refresh token is already past the expiry the reader hands back; the
container script exits 1 with a fixed message for a missing/unreadable/
wrong-shape file (a failed observation that keeps `last_good`) instead of
printing the zero frame that read as "wiped"; the record validator is total
(`math.isfinite(10**400)` raised inside the reader and made every refresh
fail forever) and `last_good` nests one level; and the director lane —
`workers.dispatch_worker` is a second `executor=True` caller — now carries
the structured `error_class` on `WorkerResult` and stops dispatch (no
review, no revision, no further tickets) on an environmental refusal,
returning `DirectorResult.pause_reason`.

*Review round 3 (2026-09-13, codex skeptic + QA, whole chunk) found one HIGH
in the round-2 fixes and two carry-through gaps; fixed before landing.*
(viii) The DAG worker *checked* the halt flag but never *set* it — only the
coordinator did, after consuming the future — so with more ready roots than
workers a pool thread picked the next queued root before the coordinator
woke (probe: three independent roots, one worker, adapter calls `[1, 2, 3]`).
The DAG worker now sets the flag the moment it holds an environmental
outcome, exactly as fan-out does, and the coordinator cancels what is still
pending; pinned with the queued-roots shape plus its plain-block control.
(ix) The tool_search re-call's `TokenRunawayError` re-raise sat in the same
outside-the-handler position the round-2 fix had just closed for
environmental errors, and `BudgetRunawayError` fell through losing its
class. Every *terminal* class (token brake, cost circuit, environmental)
now goes through the one outcome builder, whose runaway accounting ADDS the
kill's ingest to the first call's spend instead of replacing it. (x) The
director's pause lived only on the returned object: the report (Telegram's
entire reply), `summary()`, the durable director log and both CLI JSON
twins said "stuck" with no remedy, and the skip-director branch dropped the
loop's pause. A paused directive now gets a deterministic report (pause,
the refusal's own remedy text, tickets not dispatched, finished output
verbatim — no compile call spent on refused work); the log carries
`pause_reason` plus each blocked worker's `error_class`/`stuck_reason`; the
serializers and `summary()` carry the pause; skip-director forwards the
loop's.

*Review round 4 (2026-09-13, codex skeptic + QA, whole chunk) found one
HIGH the earlier rounds' fixtures could not see and four carry-through
gaps; fixed before landing.* (xi) **The first casualty.** Every test so far
began with the breaker already tripped. The CLI auth failure that *trips*
it (llm.py's subprocess site) raised a `RuntimeError` marked
`container_auth_owned` but not classed — text classification called it a
HOST login failure: wrong remedy, no pause, and only the *next* executor
call (resolver → `ContainerAuthExpired`) paused. Under `require` that
error now carries `maro_error_class = "container_auth"` too, so the monthly
expiry's first run pauses typed; under `on` the lane degrades to the host
by design and the step stays an ordinary block (both pinned through the
real subprocess adapter with a faked CLI auth result). (xii) A fan-out
worker that finished *after* the deadline had its real outcome — spend, and
the refusal that set the halt — discarded behind the synthetic timeout row;
the pool's exit waits for those workers, so their outcomes now replace the
rows. (xiii) Progress printing ran *before* the halt/commit in the DAG
worker and before the stamp in the batch coordinator; a closed stderr
(`BrokenPipeError`) replaced a refusal with "execution error" and the DAG
carried on. Every progress line in loop_parallel goes through one
never-fatal `_say`, and commit/stamp precede presentation. (xiv) A
*successful* tool_search re-call replaced `resp`, so every outcome
constructor read only the second call's tokens (cost was summed, tokens
dropped); the first call's usage is folded into the replacement response.
(xv) The skip-director branch — Telegram's whole reply — said "[no output]"
for a paused loop; it now uses the same deterministic pause renderer as the
full director path, and a director log that fails to persist is a logged
warning naming the director id, not a silent `None`. Also from this round:
CI's repo tripwires (destructive-rewrite triage manifest, truncation
discipline) had been red since the chunk's first commit while the targeted
local suites were green — the new credentials reader is now triaged in the
manifest (the old `_reseed_probe` line-framer retired) and the three bare
`[:N]` cuts on rationale strings use `context_budget.clip`.

*Review round 5 (2026-09-13, codex skeptic + QA, whole chunk) found two
HIGHs in branch twins of the round-4 fixes and three carry-through gaps;
fixed before landing.* (xvi) **The rate-limit retry twin of the first
casualty.** A containerized call that was rate-limited and whose *retry*
died of the expired session broke out of the retry loop and raised the
generic "claude rate-limited after N retries" error — past the breaker,
past the round-4 class marker: host `/login` remedy, no pause, and the
healthy HOST subprocess circuit tripped. A retry that dies of something
other than a rate limit now falls through to the generic failure path
(breaker, class marker, real detail); only a still-rate-limited or
capped-out retry raises the rate-limit error. (xvii) **Never-fatal output
had two more siblings:** the director's `_log` (both pause branches ran it
before the typed result, report or log existed) and the sequential loop's
pause print plus the finalize summary print (both precede the metadata
stamp the resume test reads). All guarded. (xviii) **DAG post-deadline
twin** of the round-4 fan-out reconcile: a queued root's early "not
started" return was never committed, so the coordinator's synthetic "dag
timeout" row stood for a step that never ran — the worker now commits it
under the lock. (xix) **A refused revision erased its draft:** the revision
call overwrote the ticket's result, so the paused directive's report and
log lost the paid-for draft — carried as `unaccepted_draft`, rendered in
the pause report as unaccepted work. (xx) **The non-verbose heartbeat
dropped the expiry warning** (recorded, but only printed under verbose)
and the health narration rode goal-run closure only — an idle box never
heard it. The heartbeat now surfaces it as a `container_auth` check and
runs `run_health_probes(only=("container_auth",))` — the same
edge-triggered, deduplicated narration, one probe, no cycle advance, no
per-tick Telegram (health status untouched). Side-find outside the chunk:
`tests/test_hermes_dispatch.py` wrote a run dir into the LIVE workspace
because `deploy/hermes/dispatch.py` pops every workspace var at import
(`c1234567-patient-yarrow`, 2026-09-07; left in place — run data is never
auto-deleted); the test now isolates after the load.

*Review round 6 (2026-09-13, codex skeptic + QA, whole chunk) found two
HIGHs in the retry loop's remaining twins and three carry-through gaps;
fixed before landing.* (xxi) **The terminal result decides.** A stream
carrying a rejected `rate_limit_event` *and* ending in "OAuth session
expired" still counted as rate-limited (another backoff cycle, then the
rate-limit error past the breaker). `_rate_limited_failure` now lets an
explicit terminal error result naming an auth failure outrank any earlier
rate-limit evidence; the entry and the retry share it. (xxii) **A retry
that times out is not replayed.** `TimeoutExpired` inside the retry loop
`continue`d — a killed executor step (which may have acted) was launched
again, and on exhaustion the stale rate-limit text was the cause and the
timeout's partial output was gone. It now raises the initial call's own
timeout error (kill reason + `maro_partial_output`), backoff persisted.
(xxiii) **The heartbeat delivers OK observations too.** Round 5 ran the
one-probe health cycle only on warn/expired, so a heartbeat-only box never
re-armed (`narrated="silent"` forever) and the *next* expiry's warning was
swallowed. Every sample now feeds the edge; the composed test proves
SILENT → RECOVERED → SILENT through the real state machine. (xxiv) **The
health transaction requires its lock** (`locked_write(..., require=True)`,
busy → cycle skipped whole: no probe, no write, no narration); under
`MARO_FILELOCK_FAIL_OPEN` the default contract proceeded unlocked and two
cycles could double- or lose-narrate. (xxv) **`maro doctor` reads the
expiry record** — a fifth container row, "Container auth session": ok /
WARN / EXPIRED from the heartbeat's liveness record, and "expiry NOT
ESTABLISHED" named as such (the breaker row is reactive: clear right up to
the first casualty, so a known-expired session showed four green rows).
Closes the round-4 residual.

*Review round 7 (2026-09-13, codex skeptic + QA, whole chunk) found two
HIGHs — one of them older than the chunk — and three carry-through gaps;
fixed before landing.* (xxvi) **The tool_search re-call never worked in
production.** It concatenated the resolver's raw schema dicts onto the
`LLMTool` list; every real adapter builds its prompt from `t.name` /
`t.parameters`, so the invoked re-call died of `AttributeError` and fell
through to "unrecognised tool: tool_search" — since Phase 41, on every
lane. Schemas are now converted at the boundary (`_schema_to_tool`,
accepting `parameters` and the older `input_schema`); a nameless schema
is a *resolution* failure (no second call). (xxvii) **Every failure of
the invoked re-call is typed.** Rounds 2–3's allow-list let a killed
re-call (timeout class) fall through, losing its diagnosis and partial
output; the invoked call's exception now always becomes the shared
blocked outcome with the first call's spend. (xxviii) **Both error
envelopes.** The CLI's `error_during_execution` result carries its text in
`errors: [...]`; the retry predicate, the breaker attribution and the
display detail read only `result` — `_terminal_error_text` serves all
three. (xxix) **Payload first on the retry.** A non-zero exit with a
complete success result (the supported rc=1 shape) whose text mentioned a
rate limit was replayed and finally reported as rate-limited; the retry
accepts a success payload before any rate-limit reading, like the initial
call. (xxx) **Narration under the lock.** The health cycle released its
lock before appending the captain's-log line, so an older cycle's SILENT
could land after a newer cycle's RECOVERED with nothing left to correct
it; the narration now runs while the lock is held.

*Review round 8 (2026-09-13, codex skeptic + QA, whole chunk) found one
older HIGH beside round 7's and three carry-through gaps; fixed before
landing. Two findings were declined by doctrine.* (xxxi) **The first-call
injector rejected the production type.** `inject_tool_search_if_needed`
read dict keys off the `LLMTool` objects `execute_step` hands it —
`AttributeError`, swallowed by the caller — so `tool_search` was never
advertised on the first call and round 7's repaired re-call was
unreachable through the intended contract. The injector now reads either
shape and appends `tool_search` in the same shape; the whole pipeline
(stub → advertised → called → re-call → done) is the test. (xxxii) **The
payload is ground truth in both directions.** A zero exit with an explicit
`is_error: true` terminal result became an empty ordinary response, past
the breaker and the classifier; `_terminal_failure` routes it into the
failure path, initial call and retry alike (no evidence the installed CLI
emits that pairing — the invariant is cheap). (xxxiii) **The sequential
pause records the refused step** (and, by the same branch, an operator-ask
pause): its `break` skipped the normal append, so a paused run reported
`steps=0` with tokens on the books. (xxxiv) **Worker kills keep their
evidence.** `dispatch_worker`'s except copied the class but returned
`result=""` and zero tokens; `llm_errors.kill_evidence` now serves both
outcome builders (partial output framed, runaway ingest counted).
*Declined:* (a) "RESUME re-executes completed peers" — the continuation
lane is continuation-by-context, not checkpoint restore, by the 2026-08-02
decree (same identity, the parent's artifacts and context ride the
continuation goal); the parallel path's outcomes reach the run report
before the early return. A checkpoint-restore resume would be its own
arc. (b) "a failed narration is acknowledged forever" — the accepted
trade documented at the narration site (write-then-narrate; a lost line
leaves the snapshot showing SILENT); an outbox is not this chunk.

*Review round 9 (2026-09-13, codex skeptic + QA, whole chunk) found two
HIGHs on lanes the chunk had never touched plus four carry-through gaps;
all fixed. One finding is a live design question, recorded not
changed.* (xxxv) **Expansion is permission-scoped.** The tool_search
re-call resolved schemas from the whole registry under a default
`PermissionContext`, so a deferred tool the caller's role or deny list
had excluded came back advertised and callable, and the admitted one was
duplicated beside its stub. The caller's tool list IS the step's
permission context: only stubs in it may expand, each replacing its
stub. (xxxvi) **A prose-only re-call is the step's answer** — it kept the
FIRST response's tool_search call and ended "unrecognised tool" with an
empty result; one `_no_tool_call_outcome` now serves both calls.
(xxxvii) **The budget breakers run after the pause seam.** A refusal (or
an operator ask) on the final step at the token/cost boundary broke out
`done` before either the typed pause or the step record; the
finished-plan carve-out is for a DONE final step only, and no longer
skips that step's own bookkeeping. (xxxviii) **The team-worker lane is an
executor lane.** `create_team_worker` had neither the container-contract
guard nor `executor=True` (under `require` a specialist's ticket ran on
the HOST session), and the parent stamped `done` over any nested
outcome — a nested typed refusal was stringified past the pause seam.
Policy signals now propagate to the same typed blocked outcome; a
blocked ticket is a blocked step. (xxxix) **Malformed terminal flags fail
closed:** `is_error: "true"`/null/1 and any `error_*` subtype take the
failure path; the success extractor requires the literal false or the
field absent. (xl) **Evidence is total and travels.** `kill_evidence`
rejects inf/NaN/negative accounting to zero (int(inf) had raised past
the blocked builder's guard), reads through the failover wrapper's cause
chain, and a terminal failure AFTER work now carries the terminal
object's usage and cost onto the exception, so the blocked step records
the spend instead of zero. *Recorded, not changed:*
`_subprocess_timeout_error` renders the kill reason into text but not
as the `maro_kill_reason` attribute the classifier keys on, so every
converted kill classifies `retry_backoff` (retry ladder → blocked step →
split recovery) rather than the `failover` the classifier's comment
intends (→ chain exhausted under `require` → `llm-unreachable` pause).
That has been the live behaviour since the wrapper landed and is
arguably the right one for a stalled worker (a stall is not an
environmental outage); flipping it is a §13e-adjacent call, logged in
BACKLOG for Jeremy.

*Review round 10 (2026-09-13, codex skeptic + QA, whole chunk): one HIGH
on round 9's own seams, the rest carry-through; all fixed.* (xli) **One
reading of a terminal result's status.** The rate-limit retry predicate
kept its own truthy-`is_error` test, so an auth-error envelope with a
malformed or clear flag behind a rejected `rate_limit_event` bought
another launch instead of the breaker; `_terminal_failure_obj` now
serves the failure test and the predicate. (xlii) **Both runaway classes
cross the team boundary** — the run-wide cost breaker's stop verdict has
no pause mapping by design, so the policy-signal test alone missed
`BudgetRunawayError`; the parent now carries `budget_runaway` to the
loop's stop branch. (xliii) **The specialist's spend is the step's
spend:** `TeamResult` carries its call's cost and the parent outcome
folds the ticket's tokens and cost in (delivered or blocked). (xliv)
**Evidence is one record.** `call_usage_evidence` (partial, input,
output, cache-read, cost) replaces the 3-tuple at the outcome builders;
the terminal failure attaches every counter independently (a cost with
zero fresh input, cache-served work); the initial-call handler no longer
overrides the builder's chain-aware read with its own shallow "" (a
wrapped refusal lost its partial output there); the worker lane keeps
output tokens. (xlv) **A refused revision keeps both** the paid-for draft
(retention was gated on an EMPTY revision result) and the revision's
partial output, and the pause report renders each under its own label on
both director branches. The doc's "self-clears" claim now states its
cadence (first executor call after the 300 s recheck; shape-only when
the expiry is unknown).

*Review round 11 (2026-09-13, codex skeptic + QA, whole chunk): one HIGH
on round 10's own seam, four accounting carry-throughs; all fixed.*
(xlvi) **An explicit terminal failure decides the retry question by
itself.** Only auth text had outranked an earlier rejected
`rate_limit_event`, so an `error_max_turns` behind one bought a replay of
an executor call that had already done its work; a terminal failure is a
rate-limit story only if the failure itself names the limit. (xlvii)
**Each terminal counter attaches independently** through the shared
total validator (one `try` around all of them let a single malformed
field make a paid failure look free; malformed counters are now warned
and recorded as 0), with the success path's conventions: fresh input =
uncached ingest, cache reads separate. (xlviii) **Total-input
accounting:** the outcome builders fold cache reads into `tokens_in`
(the LLMResponse / StepOutcome / estimator contract — fresh-only priced
a cache-only failure at zero); the worker lane too. (xlix) **The fan-out
/ DAG and batch result constructors carry billed cost and cache reads**
(both defaulted to zero, so those lanes' returned steps and the log
built from them lost the refusal's spend). (l) **An ordinary specialist
failure keeps its evidence and class:** `create_team_worker`'s except
returned "" and zero accounting; it now records the partial output,
usage, cost and llm_errors class, and the parent's blocked outcome
carries the class.

### Baked verbs + spin-up key injection (r3, 2026-08-13)

Image r3 bakes the maro **package** (never keys): `COPY src/` to
`/opt/maro/src` (+ `PYTHONPATH`), printf shims `maro-read`/`maro-fetch`
on PATH, and apt `python3-yaml python3-requests` — still **no pip in the
image**, so the runtime supply-chain stance is unchanged. `.dockerignore`
already excludes secrets/memory/.git from the build context. The src
snapshot is build-time: changing verb behavior means rebuild + revision
bump (`IMAGE_REVISION`), and `image_bakes_verbs()` reads the revision off
the image tag (>= 3 → True; custom tags conservatively False).

Hosted-free provider keys reach the container per Jeremy's 2026-08-13
decree ("injected into the container with ENV values at spin-up time...
host values stay stored and maintained on the host"):
`hosted_free_container_env()` gathers keys from host env / credentials
`.env` only when image_bakes_verbs AND host consent
(`hosted_free_enabled()`) AND a key actually exists; the docker command
gets bare `-e NAME` flags while the **values ride the docker client's
process env** — never argv, never host process listings, never logs.
Consent crosses the boundary as `MARO_HOSTED_FREE_ENABLED`, a carrier
only: in-container config, if any, still wins. The decree's backup lane
(read-only mounted key dir as configuration injection) is noted, not
built. Downstream: `image_bakes_verbs()` selects a third execute-prompt
render (baked names, no host paths) and relaxes the planner read-verb
gate to un-suppressed container lanes.

## 4. Mount map

Derived from the fence machinery — the fence already computes exactly what a
run may write (`loop_execute.py:683-687`):

| Mount | Mode | Source |
|---|---|---|
| Fence dir (project dir or worktree path — what `set_default_subprocess_cwd` binds, `agent_loop.py:223-252`) | **rw** | per-run |
| Goal-declared roots (`artifact_check.goal_declared_roots`, :601-622 — user intent to write there) | **rw** | per-run, cap 8, already `FENCE_EXTENDED`-audited |
| `validate.write_fence_allow` config roots | **rw** | config |
| `executor.container_extra_mounts` (reference data, repo checkouts) | **ro** | config, new |
| Introspection-shaped runs only: workspace `runs/` + maro source dir (`container_exec.introspection_provision()`) | **ro** | per-run, decree 2026-07-18 |
| Container `/tmp` | container-local | free — matches the fence's /tmp allowance without touching host /tmp |

**Deliberately absent — the orchestration itself (Jeremy, 2026-07-12
follow-up: "the general orchestrator shouldn't be modifiable"):** the
container never contains or mounts Maro. Not the code, not `config.yml`,
not the memory ledgers/lessons, not `secrets/.env`, not the workspace root
(today a fence root — workers don't legitimately need it). Absence beats
read-only: prompt injection (lessons, skills, context) happens host-side
during prompt construction, so the worker sees rendered text, never the
stores; worker artifacts belong in the project dir. And no
orchestrator-copy-in-container is needed either — workers never invoke
Maro; recursion per the recursion decree is a navigator move, so "spawn a
sub-goal" means the HOST spawns a sibling container. If real runs surface a
legitimate workspace read, add a targeted **ro** mount from evidence — the
same evidence-driven posture as the fence residuals (BACKLOG #1).

**Amendment, decree 2026-07-18 ("Install in the container only for the runs
that need access"):** the first evidence-driven exception to the paragraph
above. brisk-saffron (task-…80466244) — a dispatched self-diagnostic — spent
2.8M tokens / 28min proving only that it couldn't see the host run records
it was asked to diagnose. When `intent.classify()` flags a goal
`introspects_self`, that run's containers get **ro** provisioning: the
workspace `runs/` dir (a workspace *descendant*; the root itself stays
hard-forbidden) + the maro source dir with `PYTHONPATH`, so `python3 -m`
readers work in-container best-effort (stdlib-only modules — the image
carries no maro Python deps; the records mount is the real guarantee), plus
`MARO_INTROSPECTION`/`MARO_INTROSPECTION_RUNS` markers. Still absent even
then: `memory/`, `config.yml`, `secrets/`, the workspace root. Gated by
`executor.introspection_access` (default on, inert unless `executor.container`
is on); fails CLOSED to blind isolation on any resolution error. The flag is
run-scoped (`ContextVar`, finding-D reset pattern) — a later run never
inherits the grant.

**uid/gid:** run `--user $(id -u):$(id -g)` so mounted files stay
operator-owned (the known edge flagged at decision time). Symlink escapes:
a symlink inside a mount pointing outside resolves inside the container to
nothing — the hole this closes, worth a burn-in test case.

**Self-development runs (goal = edit a live repo, incl. Maro's own):
copy-not-passthru.** v1 of this doc specced "parent repo ro + worktree rw"
— that is mechanically broken: `git worktree` checkouts write objects into
the PARENT's `.git/objects`, so commits inside the container fail against
an ro parent. The corrected shape (Jeremy's instinct, 2026-07-12): the
repo mounts **ro**, and container start clones it into the rw scratch —
the worker edits and commits a **copy**; merge-back is a HOST-side
`git fetch` from the scratch clone + the existing serialized merge/review
(`src/worktree.py` semantics preserved: conflict → blocked, branch kept,
never silent loss) + the worker push guard. The live repo — especially the
orchestrator's — is never writable from inside a container, even when the
goal is "fix Maro."

## 4b. Data-escalation greys — the isolation ladder, named

Raised by Jeremy 2026-07-12 ("slight concerns about data escalation leading
to targeted exploits from that ecosystem... a few shades of grey in the
paranoia direction"). Naming the ladder so each rung is a decision, not a
drift:

- **Rung 0 (this design):** orchestration absent, project rw, references ro,
  self-dev via scratch clone. Read access to anything sensitive: none.
- **The loop the container does NOT cover — say it plainly:** the
  *artifact-to-future-prompt* loop. Hostile ecosystem content → worker
  artifact → curation promotes it → injected into future planning prompts.
  That path runs entirely host-side and its gates are the existing ones:
  fail-closed `injection_guard` scans on every self-modification lane,
  dangerous-pattern code-region scan, never-auto-adopt + quarantine
  (the cs-r2-01 family; same supply-chain stance as portable-learning
  imports). A targeted exploit built from ecosystem knowledge of Maro's
  prompt formats attacks THIS loop, not the filesystem — the threat model
  section below carries it now.
- **Rung 1 (deferred opt-in, not v1): quarantine-until-verified scratch.**
  ALL writes land in a per-run copy of the project dir; harvest to the real
  project dir only after fence/closure checks pass. Coherent, but costs
  live operator visibility mid-run (against the surface-all-details
  principle) and complicates resume semantics. Revisit with evidence of a
  real artifact-poisoning specimen, not speculatively.
- **Rung 2 (rejected): no mounts, docker-cp everything.** Maximal isolation,
  breaks the operator symlink/liveness observability and multi-step artifact
  continuity for no marginal containment over rung 0 + rung 1.

## 5. Fence interaction — complement, not replacement

Detection stays exactly as-is: tool_events still come back through
stream-json, `detect_out_of_fence_access` still runs, `SCAVENGE_DETECTED` /
`FENCE_WRITE_BLOCKED` still log and demote (`loop_execute.py:674-712,
808-854`). What changes is the meaning: the fence becomes the *honesty*
layer (the run claims vs did) while the container is the *containment*
layer. The BOUNDED_WORKSPACE known holes (`cp`/`mv`/`sed -i` invisible
targets, subshell cds) stop being containment risks — an invisible write
can only land inside a mount. SECURITY_MODEL Part 1's honest sentence
("detection, not containment") gets its Part 2 fulfilled.

## 6. Config + degradation

| Key | Default | Notes |
|---|---|---|
| `executor.container` | `off` | `off` / `on` / `require`. OFF everywhere until burn-in on the runtime box; the flip (fresh-install default especially) is **Jeremy's call** after burn-in evidence. `require` refuses executor calls when docker is unavailable instead of degrading. **Runtime box runs `require` since 2026-09-13** (Jeremy's call after the 09-12 host-lane degrade beside the secrets store). |
| `executor.container_image` | `maro-executor:<pinned>` | |
| `executor.container_network` | `bridge` | See below. |
| `executor.container_extra_mounts` | `[]` | ro reference mounts. |

All rows land in DEFAULTS.md with reasoning (census tripwire
`tests/test_defaults_doc.py` enforces this).

**Degradation:** docker absent + `container: on` → one warning per run +
current fence-only posture; doctor row says exactly which mode a run would
get. Never silent — the difference between "sandboxed" and "not" must be
visible (SF-6's whole lesson).

> **DECISION (provisional): network stays `bridge` (egress on) in v1.**
> Workers legitimately fetch web content and the CLI needs api.anthropic.com.
> The threat model doc already names network as an open decision; narrowing
> (an egress allowlist proxy) is real work with real breakage surface — do it
> as its own evidence-driven follow-on, not in v1. `container_network: none`
> exists from day one for offline-shaped goals.

## 7. sandbox.py disposition — RETIRED 2026-07-13 (Opus, C4 cleanup)

Retired in this arc (SECURITY_MODEL "Honest inventory": "wire it or delete
it in the container design pass" → resolved as **delete**). It hardened a stub
that never executed real skill code and had zero live-path callers (verified:
the only `from sandbox import` sites were `run_curation.py` for the pattern
list and `cli.py` for the retiring subcommand; the `sandboxed=` flag on
`skills.run_skill_tests` was a dead parameter no caller ever set). Retirement
delivered:
- `src/sandbox.py` deleted (536 LOC) + its two test files
  (`test_sandbox.py`, `test_sandbox_hardening.py`, 774 LOC).
- The one real consumer, `_DANGEROUS_PATTERNS`, moved to its actual user
  `run_curation.py` (skills-lite ingest static scan) as a module-level
  constant — no new module, no census entry.
- `maro sandbox` CLI subcommand + `maro-sandbox` entry point + `sandbox`
  extras group removed; py-modules census (`test_packaging.py`) follows.
- The `sandbox-audit.jsonl` audit log retired with the writer: its readers
  (`observe.py` audit tail + `maro-observe audit` subcommand) and its GC
  (`gc_memory._gc_audit`) were removed — a subcommand that could only ever
  print "none" on a fresh install is exactly the dead UX this cleanup sweeps.
- Deletion tripwire allowlist (`test_no_silent_deletion.py`) drops the stale
  `sandbox.py` entry; SECURITY_MODEL Part 1 + Honest-inventory and the spec's
  observability list updated for currency.
Full suite green; net −1670/+58 LOC.

## 8. Performance + session-reuse interplay

Warm-image `docker run` adds ~100-300ms against a step boot already ~1.5s
CLI + context re-injection — marginal; measure honestly in burn-in (the
boot-tax anatomy from 2026-07-11 is the baseline). The parked session-reuse
spike (`--resume` per boundary segment) composes cleanly: session state
lives under `~/.claude` = the auth volume, which persists across container
invocations — a resumed session in a fresh container works by construction.
No design coupling; noted so neither work stream blocks the other.

## 9. Implementation chunks (sized for handoff)

- **C1 — image + auth + doctor — SHIPPED 2026-07-12 (Opus).**
  `deploy/docker/Dockerfile.executor` (node:22-slim + baked
  `@anthropic-ai/claude-code` pinned to `2.1.207`, confirmed against npm at
  ship time; `git`/`python3`/`curl`; build-arg CLI pin, image tag encodes
  it). New `src/container_exec.py` = the shared seam C2 extends (constants
  incl. `AUTH_VOLUME`/`NAME_PREFIX`/`CONTAINER_HOME=/home/maro`,
  `container_mode()`/`container_image()` config readers, mockable
  docker/image/auth-volume/login probes, operator instruction builders).
  `maro-bootstrap container-setup` prints the build + auth-volume `/login`
  walkthrough (creates nothing). `doctor` gained a mode-gated container
  block (off → one info row, nothing probed; on/require → docker + image +
  auth-volume rows, loud degrade/refuse wording per SF-6; the
  token-spending login probe rides `--live`). DEFAULTS.md `## Executor /
  sandboxing` documents all four `executor.*` keys; stale root `Dockerfile`
  + `docker-compose.yml` deleted (README compat line + the r2-flagged claim
  updated to point at the executor image). Concrete decisions made this
  chunk: fixed `HOME=/home/maro` so the auth volume mounts at a known path
  under an arbitrary `--user` uid; image tag = `maro-executor:<CLI-pin>`.
  20 new tests (`tests/test_container_exec.py`, docker fully mocked — no CI
  docker dependency) + `container_exec` added to the py-modules census.
  Residual for C4: reconfirm/re-pin the CLI version when building on the box.
- **C2 — the wrap — SHIPPED 2026-07-12 (Opus).**
  `ClaudeSubprocessAdapter.complete` decides once per call whether to
  containerize (`container_exec.resolve_container_run(no_tools)` — off/
  no_tools → host; docker up → container; `on` + no docker → degrade to host
  with one warning per process; `require` + no docker → raise
  `ContainerUnavailable`, refuse) and threads a `container_name` into
  `_run_subprocess_safe`, which owns the wrap (`build_run_command`:
  `docker run --rm -i --init --name … --user uid:gid --label
  maro.owner_pid=… --label maro.owner_start=… -v <cwd>:<cwd>:rw -v maro-claude-auth:/home/maro/.claude
  -e HOME=… -e MARO_WORKER_RUN=1 [MARO_ALLOW_MAIN_PUSH] --network … -w <cwd>
  <image> <inner claude -p …>`) and the kill path (`docker kill <name>`
  BEFORE `os.killpg` at both failure kill sites — killpg only reaps the
  docker client). Stranded-container reaper wired into
  `heartbeat.stranded_state_sweep`: kills running `maro-exec-*` whose
  owner PID is dead or its process-birth token proves PID reuse — never a live
  run's in-flight container. Legacy/missing tokens remain conservative. Docker
  probed once per process and cached (no per-call boot tax). All four
  `executor.*` DEFAULTS rows already landed in C1. Minimal mount set (working
  dir rw + auth volume); full fence-root translation + self-dev clone are C3.
  25 new tests (command-vector construction, decision matrix off/on/require/
  no_tools, kill-path, sweep by owner-PID liveness, degrade-warn-once — docker
  fully mocked; the `_run_subprocess_safe` wrap + kill exercised end-to-end
  against a real non-docker stand-in process).

  **Adversarial review (Codex, 3 lenses — Skeptic/Architect/Minimalist,
  2026-07-12): REJECT with consensus; 9 findings fixed, 1 deferred.** Real
  bugs the review caught, all fixed same session:
  - **Host claude path used inside the image** — the inner cmd carried
    `self.claude_bin` (host-resolved, e.g. `/opt/homebrew/bin/claude`), which
    doesn't exist in the image; every containerized call would have failed.
    Fixed: `build_run_command` basenames argv[0] → bare `claude` (baked on the
    container PATH).
  - **Auth uid mismatch** — `login_command`/`login_probe` ran as root, seeding
    root-owned OAuth files the executor (running `--user host-uid`) couldn't
    read/refresh, and `--live` falsely certified it. Fixed: all three run as
    the same `$(id -u):$(id -g)` (shared `_user_args`).
  - **Sweep could kill unrelated containers** — `docker ps --filter
    name=maro-exec-` is a substring match and the code killed unlabeled
    matches. Fixed: filter by our `label=maro.owner_pid`, verify the name
    prefix, and SKIP (never kill) anything unlabeled/unparseable.
  - **Over-capture** — `not no_tools` containerized every tools-carrying call
    (verify, quality-gate, refinement, planning, the doctor probe), not just
    worker steps. Fixed: an explicit `executor=True` signal threaded from the
    real executor seams only (`step_exec` EXECUTE_SYSTEM ×2, `workers` ticket);
    default-False keeps everything else on the host (safe by construction).
  - **Stale docker cache** — availability was cached for the process lifetime,
    so `on` became a hard failure if the daemon died mid-run instead of
    degrading. Fixed: probe fresh per (heavy) executor call; only the degrade
    WARNING is throttled (60s).
  - **Retry name reuse / cross-process collision** — the rate-limit retry
    reused the container name, and a resumed run in a fresh process restarted
    the seq at 0. Fixed: names include the PID and the retry resolves a fresh
    name (the sweep keys on the label, so name uniqueness is free).
  - **cwd=None** would run in an empty container → fall back to host.
  - **`-v host:host:mode`** breaks on paths containing `:` → switched to
    colon-safe `--mount type=bind`; `container_extra_mounts` now honored (ro).
  - **Deferred (known limitation, noted in code):** the sweep can't reap a
    container leaked while its owning *process* stays alive (a wedged
    `docker kill` in a long-lived process) — process-PID liveness can't tell it
    from the live owner's current container. Needs run-scoped liveness; a
    follow-on, low frequency (requires docker-kill itself to wedge).
- **C3 — mount map + self-dev clone mode — SHIPPED 2026-07-12 (Opus).**
  Two halves, both dormant until `executor.container` is flipped on (C4):
  - **Fence → mount translation.** `container_exec.build_mount_map(cwd, *,
    rw_roots, ro_mounts)` (pure, containment-aware dedup: a rw parent covers a
    ro child, a ro parent does NOT cover a rw child) turns the run's write
    fence into the `docker run` mount list — cwd rw, goal-declared roots +
    `validate.write_fence_allow` rw, `container_extra_mounts` ro. Host `/tmp`
    and the workspace root are deliberately NOT mounted (§4). Missing rw roots
    are SKIPPED, never created (a bind of a missing path would be root-owned);
    the function mutates no filesystem. The run's extra rw roots ride a
    ContextVar (`llm.set_default_container_rw_roots`, assembled by
    `run_agent_loop` alongside the cwd bind — same pattern as
    `_DEFAULT_SUBPROCESS_CWD`), read only in the container branch of
    `_run_subprocess_safe`.
  - **Self-dev scratch clone.** When a run is configured to containerize
    (`container_configured()` — mode on/require, no live docker probe so the
    decision can't race the daemon) and the fence dir is a git repo, the live
    repo is NEVER mounted rw:
    `worktree.provision_clone` makes a `--no-hardlinks` throwaway clone (no
    shared object inode), the run works the copy, and `merge_back_clone`
    merges it back HOST-side via `git fetch` + the SAME serialized
    `_locked_merge` core extracted from `merge_back` (conflict → branch kept,
    never silent loss). Rides the `ctx.run_worktree` seam: provisioned in
    `agent_loop` where cwd binds, merged in `loop_finalize` BEFORE the
    worktree→project merge (clone→fence must land first when both are active),
    field `ctx.container_clone`. `cleanup_clone` deletes the scratch only after
    merge-back (allowlisted in the retention-decree tripwire).
  Tests: `TestBuildMountMap` + `TestContainerConfigured` + `TestContainerSuppression`
  in `tests/test_container_exec.py`; clone round-trip / no-changes /
  object-isolation / conflict / side-branch data-loss / hook-RCE-neutralization
  in `tests/test_worktree.py`; the rw-roots-flow-through in `tests/test_llm.py`.

  **Adversarial review (Codex, 3 lenses — Skeptic/Architect/Minimalist,
  2026-07-13): REJECT with consensus; 6 finding-classes fixed same session.**
  The mocked-docker suite couldn't see these — the review earned its cost again:
  - **Fail-open live repo (findings A/M3/S2/A1).** Clone-provision failure — or
    docker down at run setup but up by an executor call — left `_fence_dir` = the
    LIVE repo, which the seam then mounted **rw**: the exact thing the clone
    exists to prevent. Fixed: provisioning is gated on config intent
    (`container_configured()`, no live probe — kills the timing race), and a
    git-repo run with no clone **fails CLOSED** via a run-scoped
    `container_exec.set_container_suppressed` kill switch (`resolve_container_run`
    → host). A missing clone can never mount a live repo.
  - **Doc-only exclusions (findings B/M2/S1/A2).** `build_mount_map` mounted
    `/tmp`, the workspace root, the live repo, and symlink-escapes rw if present.
    Fixed: every source is realpath-resolved (symlink escapes can't smuggle a
    target past the filter) and hard-rejected if it is/contains the workspace
    root, `/tmp`/tempdir, or a caller-supplied `forbidden_root` (the live repo of
    a self-dev run is dropped from the rw set in `agent_loop`). The cwd is
    exempt (the run's own dir; kept off the live repo upstream).
  - **Host-git RCE in the attacker-writable clone (findings C/M1/A3).** Host-side
    `git status/add/commit/fetch` against a clone whose `.git` a hostile worker
    controls would execute planted `.git/hooks/*`, `filter.*.clean`,
    `core.fsmonitor`, or `uploadpack.packObjectsHook` with host privileges —
    defeating the fence. Fixed: `merge_back_clone` first `_sanitize_untrusted_git`
    (removes hooks, strips exec-capable local config; the container has exited by
    finalize so nothing races) and runs every clone-side git `_git_hard`
    (`-c core.hooksPath=/dev/null -c core.fsmonitor=`). A regression test plants a
    `pre-commit` hook + `core.fsmonitor` and asserts neither fires.
  - **Stale run-scoped state (findings D/M4/S4/A4).** The rw-roots ContextVar
    persisted across runs; a run whose setup raised could inherit a prior run's
    authorized roots. Fixed: `agent_loop` resets the rw-roots var AND the
    suppression flag to their empty/safe values FIRST, before anything can raise.
  - **Clone data-loss / silent success (findings S3/A6).** A worker that switched
    branches inside the container made `base_ref..clone.branch` show 0 → false
    "no changes" → `cleanup_clone` deleted the only object store; a swallowed git
    error did the same; a merge-back exception still reported `done`. Fixed:
    merge-back keys on the clone's ACTUAL `HEAD` (not an assumed branch), every
    git-command failure is a failure (never a silent "clean"), and a
    finalize-time exception downgrades the run to `partial` naming the retained
    clone.
  - **Partial-clone leak (finding A5).** A failed `git clone` left a partial dir;
    now cleaned. (Residual below.)

  **Residuals (documented, for C4 burn-in / Jeremy):**
  - **Host-git hardening is defense-in-depth, not a proof.** Sanitize + hardened
    `-c` close the known git config-exec vectors (hooks, filters, fsmonitor,
    packObjectsHook, aliases); a novel git-config RCE knob would need adding to
    `_EXEC_CONFIG_KEYS`. The fully-airtight design is committing inside the
    container so the host only ever fetches — revisit at C4 if burn-in warrants.
  - **Crash-leaked scratch clones — DETECTION SHIPPED 2026-07-13.** A SIGKILL
    between provision and finalize leaks a whole-repo clone under `worktrees/`.
    `worktree.surface_stranded_clones` (heartbeat-wired) DETECTS clones older
    than a 24h grace and surfaces them (path + derived branch + age) for the
    operator; the heartbeat records them in `result["stranded_clones"]` and warns
    once per clone (a `.surfaced` marker in the clone's PARENT, which the
    container never mounts). **It never auto-deletes and never runs git inside
    the clone.** An earlier reclaim-empty design was REJECTED by adversarial
    review (unanimous, 3 lenses, 2026-07-13): a scratch clone is entirely
    worker-controlled, so (1) running git against it to classify it executes
    planted `.git/config` (`core.fsmonitor`, hooks) on the host — the exact RCE
    `_git_hard`/`_sanitize_untrusted_git` exist to stop — and (2) NO content check
    proves "empty" (ignored files, skip-worktree, data under `.git`, commits on
    another branch, or a rewritten `refs/remotes/origin` all hide real bytes from
    `git status`/`rev-list`), and age is not ownership. The retention-safe action
    on an untrusted dir that MIGHT hold work is to surface, not delete. Automatic
    disk reclaim would need a hardened recover-then-remove (`merge_back_clone`
    rescues the work first, then `cleanup_clone`) — a background heartbeat
    mutating the live repo, which is **Jeremy's call**, not a silent default.
  - **Comma-in-path mounts are skipped** (docker `--mount` CSV can't encode them);
    a goal-declared rw root that doesn't exist on the host is skipped, not created.
  - Real-docker E2E (punctuation paths, nested ro/rw, failure cleanup) is a C4
    item — CI keeps docker mocked.
- **C4 — burn-in + flip (runtime box, Jeremy adjudicates):** run the
  standing dogfood goals under `container: on`; watch for env-dependency
  surprises, uid/gid friction, boot-tax delta; then decide box default and
  fresh-install default. **The executable procedure is `CONTAINER_BURN_IN.md`**
  (preconditions → dogfood workload → the three watch-list metrics → the
  acceptance probe → go/no-go checklist → the flip). sandbox.py retirement
  (§7) **SHIPPED 2026-07-13** as this chunk's cleanup. Prep landed from the dev
  Mac (2026-07-13, Opus): the burn-in runbook + the acceptance-probe harness
  (`scripts/container-acceptance-probe.sh`, deterministic parts self-tested).
  What remains is inherently box-side: run the workload against real docker,
  fill the go/no-go checklist, and — Jeremy's call — flip.

  **C4 CLOSED 2026-07-16:** box burn-in complete (`CONTAINER_BURN_IN.md`
  §5b — dogfood clean, acceptance probe CONTAINED); Jeremy flipped this
  box to `executor.container: on` the morning after Hermes dispatch went
  live (SESSION_PROTOCOL_DESIGN §11 Q7 — all runs, no per-origin split).
  Fresh-install default stays OFF per BURN_IN §6. *(Recorded 2026-07-28,
  thread census — the flip had happened 12 days earlier while this doc
  and BACKLOG still said pending.)*

Acceptance for the arc: a hostile-goal probe (write to `~/.ssh`-shaped
target, read a host secret path) demonstrably lands nowhere while the same
run under fence-only mode logs SCAVENGE rows — the before/after IS the
security story for the README. Realized as `scripts/container-acceptance-probe.sh`
(`plant` → run the goal under each mode → `check <run-dir> <mode>`): canaries
outside the fence, containment checked by token-absence + decoy-unchanged,
detection checked by `SCAVENGE_DETECTED`/`FENCE_WRITE_BLOCKED` in the run's
captain's-log slice. The detection half is unit-pinned
(`test_artifact_check.py`); the containment half is what burn-in proves.
