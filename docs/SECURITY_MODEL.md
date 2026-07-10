---
status: living
---

# Security Model

What Maro actually enforces today, what was decided for 1.0, and what the
threat model looks like on both sides of that line. Rewritten 2026-07-09 after
the Purgatorio audit (cs-04 / SF-6): the previous version of this doc claimed
per-skill sandboxing that does not exist on the live path.

---

## 1. Current state: trusted-operator model

**There is no sandbox on the live executor path.** The work executor spawns
the Claude CLI with permissions pre-granted and full built-in tool access:

- `claude -p ... --dangerously-skip-permissions` — `src/llm.py:1007`
- The only tool restrictions: `--disallowedTools WebFetch,WebSearch` on
  executor calls (`src/llm.py:1017`), and `--tools ""` (no tools at all) for
  utility calls like routing/classification (`src/llm.py:1015`) so a
  classifier can't act on the goal it's classifying.

That subprocess runs as the operator's user, with the operator's filesystem,
network, and credentials. Maro is currently safe to run only under a
**trusted-operator** assumption: you trust the goals you feed it the way you
trust the commands you type. An LLM-driven shell with your permissions is the
honest description.

`src/sandbox.py` (static analysis, RLIMITs, soft network block, venv
isolation) exists but is an **unwired prototype**: its only callers are the
manual `maro sandbox` CLI subcommand (`src/cli.py:1764`), an off-by-default
`sandboxed=` flag on the skill test runner (`src/skills.py:1664`), and its own
tests. Nothing on the goal-execution path invokes it.

### What the code does enforce today

These are real, on by default, and verified against the tree:

| Control | What it does | Evidence |
|---|---|---|
| **Spend caps, fail-closed** | Fresh installs are capped: $5/run, $25/day (`src/loop_init.py:30`). The `_budget_gate` (`src/loop_init.py:34`) refuses to start a loop once the cross-run ledger (`metrics.spend_today`, `src/metrics.py:193`) hits the daily cap; a malformed config value falls back to the default cap rather than uncapped (`_coerce_cap`, `src/loop_init.py:54-70`). Mid-loop, the per-run cap hard-stops the loop at budget + 20% slush (`src/loop_execute.py:688-697`); a token budget stop exists too (`src/loop_execute.py:675`). Opt out only by explicit `budget.per_run_usd: 0` / `budget.daily_usd: 0`. |
| **Worker push guard** | Every Maro-spawned agentic subprocess gets `MARO_WORKER_RUN=1` (`src/llm.py:649`); the pre-push hook (`scripts/hooks/pre-push`, installed at `.git/hooks/pre-push`) blocks worker pushes to `main`/`master`. Humans are unaffected; explicit bypass via `MARO_ALLOW_MAIN_PUSH=1` or `workers.allow_main_push` config. Advisory: a worker that edits or skips the hook defeats it. |
| **Secret scrubbing in record mode** | Everything the run recorder persists is scrubbed first: captured LLM calls (`src/runs.py:414`) and environment snapshots (`src/runs.py:474`) pass through `secret_scrub.scrub()` (`src/secret_scrub.py`), which redacts key-shaped strings (sk-, ghp_, xox, AKIA, bearer/token/password assignments). Single function by design so capture paths can't diverge. |
| **Bounded worker writes (the fence)** | Executor subprocesses have their cwd bound inside the run's workspace so relative writes land in-fence (`src/llm.py:1030`, binding in `_run_subprocess_safe` `src/llm.py:662-670`). Out-of-fence access is detected from the real tool transcript (`artifact_check.detect_out_of_fence_access`, `src/artifact_check.py:620`); an out-of-fence **write** demotes the step done→blocked, default ON since 2026-07-09 (`validate.write_fence`, `src/loop_execute.py:721-724`). /tmp and goal-declared paths widen the fence (`src/artifact_check.py:548`). See `docs/BOUNDED_WORKSPACE.md`. This is drift detection, not containment — a hostile worker can still write anywhere; the fence makes it visible and fails the step. |
| **Prompt-injection scanning** | External content is scanned before entering step context (`security.scan_external_content`, live at `src/loop_execute.py:248` and `src/loop_parallel.py:360`) — flag/redact, not block. The self-modification path is stricter: evolver suggestion application (`src/evolver_store.py:387`), skill synthesis (`src/skill_lifecycle.py:463`), and skills-lite promotion (`src/run_curation.py`, gate added 2026-07-10 per Purgatorio r2 cs-r2-01) run `injection_guard.scan_content` and **discard/quarantine** on findings or scan failure. Backstop: the skill loader re-scans workspace-overlay skills at load time and at `load_full` (`src/skill_loader.py`, `_workspace_skill_clean`) — write-time gates can't see post-promotion edits or unknown producers; repo `skills/` are git-reviewed and not re-scanned. |
| **Concurrent-write integrity** | Shared ledgers are written under fail-closed `flock` (`src/file_lock.py`, `locked_append` at `:236`) — a lock timeout raises rather than corrupting the spend/learning ledgers the budget gate depends on. |

What this stack is: cost containment, provenance, and drift *detection*.
What it is not: isolation. Nothing here stops a worker from reading
`~/.ssh` or running `rm -rf` outside the fence — it will just be detected,
demoted, and logged after the fact.

---

## 2. Decided 1.0 direction: containerized executor

**DECIDED 2026-07-09 (Jeremy), DESIGN PENDING — gets its own pass.**

> "Play nice with security here and dockerize this path so there's literally
> no way to screw things up. Mount a working dir and maybe make some other
> resources read only... a nice tight sandbox is likely appropriate."
> — GOAL_BRAIN.md, Decisions 2026-07-09

Shape of it:

- The executor subprocess (`claude -p`) runs inside a container.
- The run's working directory is mounted **read-write**; anything else the
  run needs (repo checkouts, reference data) mounted **read-only**; the rest
  of the host is simply not there.
- `--dangerously-skip-permissions` becomes acceptable *because* the blast
  radius is the mount, not the machine.

Known edges, flagged at decision time:

- **Host-file oddities across the mount boundary** — uid/gid mapping,
  symlinks that escape the mount, tools that expect host paths. Local-machine
  file weirdness is the acknowledged rough edge; design pass owns it.
- **Standing constraint: stay on the right side of the API-vs-CLI automation
  line.** The container is filesystem/network isolation, not a lever for
  auto-accepting permission prompts or otherwise hacking around the CLI's
  intended operation. No PTY-driven prompt-acceptance tricks.

Until this ships, Part 1 is the whole security story. Don't point Maro at
untrusted goals or untrusted external content and expect containment.

---

## 3. Threat model (with the container)

What the containerized executor will and won't protect against:

**Protected:**
- **Host filesystem** — a prompt-injected or malfunctioning worker cannot
  touch anything outside its mounts; read-only mounts can't be modified.
- **Secrets outside the mount** — `~/.ssh`, `~/.config/gh`, `secrets/.env`,
  browser state: unreachable if not mounted. (Corollary: don't mount them.)
- **The machine** — no host package installs, no service tampering, no
  crontab persistence.

**Not protected:**
- **The mounted workdir itself.** A prompt-injected worker can still trash,
  exfiltrate-into-artifacts, or subtly corrupt everything read-write in the
  container. Git history and review remain the recovery story for the
  workdir; the container bounds the damage, it doesn't prevent it.
- **Spend.** Tokens are burned from inside a container just as fast. The
  budget gates (Part 1) remain the control.
- **Output trust.** A compromised worker produces compromised artifacts and
  self-reports. Verification (write fence, artifact checks, verdicts) remains
  the control — containment and honesty are different problems.
- **Network, unless the design pass restricts it.** Default Docker networking
  gives the worker egress; whether/how to narrow that is a design-pass
  decision.

---

## Human gates (unchanged)

Actions requiring explicit operator approval regardless of autonomy tier:
money/real trades, posting publicly as the operator, identity-file writes
(AGENTS.md/SOUL.md), irreversible deletion of non-git-tracked data, exposing
private data externally, canon promotion. Everything else: act first,
forgiveness over permission.

## Secrets handling (unchanged)

- Credentials live in `<workspace>/secrets/.env` — outside git, never in
  `projects/`; `config.load_credentials_env()` is the single entry point.
- If a path is being `cat`'d, logged, or written to an artifact, check it
  doesn't contain a secret first. Record mode scrubs (Part 1), but scrubbing
  is a regex net, not a guarantee.

---

## Honest inventory

- `src/sandbox.py` — unwired prototype (see Part 1). Either wire it or
  delete it in the container design pass; a hardening module with zero
  runtime callers is documentation debt pretending to be defense.
- `memory/sandbox-audit.jsonl` — only populated by manual `maro sandbox`
  runs; not evidence of live-path sandboxing.
- The previous version of this document claimed "every skill runs sandboxed"
  and network isolation per skill. Neither was true of the live path. This
  rewrite is the correction (Purgatorio cs-04, docs-01 / SF-6).
