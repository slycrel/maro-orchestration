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
  (`docs/ROUTING_AND_PROBE_SYNTHESIS_DESIGN.md` B3), not this one.
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

## 4. Mount map

Derived from the fence machinery — the fence already computes exactly what a
run may write (`loop_execute.py:683-687`):

| Mount | Mode | Source |
|---|---|---|
| Fence dir (project dir or worktree path — what `set_default_subprocess_cwd` binds, `agent_loop.py:223-252`) | **rw** | per-run |
| Goal-declared roots (`artifact_check.goal_declared_roots`, :601-622 — user intent to write there) | **rw** | per-run, cap 8, already `FENCE_EXTENDED`-audited |
| `validate.write_fence_allow` config roots | **rw** | config |
| `executor.container_extra_mounts` (reference data, repo checkouts) | **ro** | config, new |
| Container `/tmp` | container-local | free — matches the fence's /tmp allowance without touching host /tmp |

**Deliberately absent**: the workspace root. Today `workspace_root()` is a
fence root, but workers don't legitimately need it — prompt injection
(lessons, skills, context) happens host-side during prompt construction;
worker artifacts belong in the project dir. Its absence inside the container
converts a fence-detection concern into a hard wall. If real runs surface a
legitimate workspace read, add a targeted **ro** mount from evidence — the
same evidence-driven posture as the fence residuals (BACKLOG #1).

**uid/gid:** run `--user $(id -u):$(id -g)` so mounted files stay
operator-owned (the known edge flagged at decision time). Symlink escapes:
a symlink inside a mount pointing outside resolves inside the container to
nothing — the hole this closes, worth a burn-in test case.

**Worktree caveat:** `busy_policy: worktree` fence dirs are `git worktree`
checkouts whose metadata references the parent repo's `.git` by absolute
path — a worktree mount alone is broken git. Mount the parent repo **ro** +
worktree **rw** in that mode (the merge-back happens host-side after the
step, `src/worktree.py`, unaffected).

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
| `executor.container` | `off` | `off` / `on` / `require`. OFF everywhere until burn-in on the runtime box; the flip (fresh-install default especially) is **Jeremy's call** after burn-in evidence. `require` refuses executor calls when docker is unavailable instead of degrading. |
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

## 7. sandbox.py disposition

Retire it in this arc (SECURITY_MODEL "Honest inventory": "wire it or delete
it in the container design pass"). It hardens a stub that never executes
real skill code (`sandbox.py:328-343`) and has zero live-path callers. Keep
the one real consumer: `_DANGEROUS_PATTERNS` moves to its actual user
(`run_curation.py:379` skills-lite scanning) or a small `static_scan.py`.
The audit-log JSONL and `maro sandbox` CLI subcommand retire with it.

## 8. Performance + session-reuse interplay

Warm-image `docker run` adds ~100-300ms against a step boot already ~1.5s
CLI + context re-injection — marginal; measure honestly in burn-in (the
boot-tax anatomy from 2026-07-11 is the baseline). The parked session-reuse
spike (`--resume` per boundary segment) composes cleanly: session state
lives under `~/.claude` = the auth volume, which persists across container
invocations — a resumed session in a fresh container works by construction.
No design coupling; noted so neither work stream blocks the other.

## 9. Implementation chunks (sized for handoff)

- **C1 — image + auth + doctor (Sonnet, 1 session):**
  `deploy/docker/Dockerfile.executor`, `maro-bootstrap container-setup`
  (prints build + login instructions; creates nothing itself), doctor rows
  (docker present / image present / container login ok), delete stale
  root `Dockerfile` + `docker-compose.yml`. Tests: doctor-row units;
  no docker dependency in CI (probe mocked).
- **C2 — the wrap (Opus, 1 session):** `_run_subprocess_safe` container
  branch behind `executor.container`, named-container kill path,
  stranded-container sweep line in the existing stranded-state sweep,
  DEFAULTS rows. Tests: command-vector construction, kill-path, degradation
  warning — docker mocked; this seam runs everything, treat it that way.
- **C3 — mount map (Sonnet, 1 session):** fence-root → mount translation
  incl. goal-declared rw, extra ro mounts, worktree parent-repo case,
  uid/gid. Tests: translation-table units against fence fixtures.
- **C4 — burn-in + flip (runtime box, Jeremy adjudicates):** run the
  standing dogfood goals under `container: on`; watch for env-dependency
  surprises, uid/gid friction, boot-tax delta; then decide box default and
  fresh-install default. sandbox.py retirement (§7) rides this chunk's
  cleanup.

Acceptance for the arc: a hostile-goal probe (write to `~/.ssh`-shaped
target, read a host secret path) demonstrably lands nowhere while the same
run under fence-only mode logs SCAVENGE rows — the before/after IS the
security story for the README.
