---
status: record
name: 2026-08-22-milestone-dag-adversarial-review
description: Adversarial review round on milestone-DAG parallelism (3202221) — 4 seats on sonnet-medium fallback, REJECT with verification ledger, fixes landed same session.
---

# Adversarial review — milestone-DAG parallelism (commit 3202221)

Date: 2026-08-22. Four seats (Skeptic, Architect, Minimalist, Expert QA) on
sonnet-medium (SAME-MODEL FALLBACK — codex usage-capped until 08-27,
re-probed live this session). Fix pass landed same session; fixes verified
by 10,264-green suite. Artifacts: `/tmp/adversarial-review.kpupea/` (five
artifacts per seat, transcripts included).

## Intent
Milestones execute as a dependency DAG: decompose declares `depends_on`
(ordering only, never gating), ready milestones run concurrently behind
`mission.parallel_milestones` (default ON per Jeremy's flip decree — the
flag is the revert lever), with an undecorated decomposition behaving
exactly like the old sequential walk.

## Verdict: REJECT → fixed to green same session (SAME-MODEL FALLBACK: sonnet-medium)

One review round surfaces roughly 75–80% of what two rounds find (measured
n=2, 2026-08-17). This review pushes the work to be better; it does not
certify it correct. Fixes made in response to these findings are themselves
unreviewed — across ~50 recorded rounds, the prior round's fix layer was
the single likeliest home of the next round's worst finding.

## Verification Ledger (HIGHs)

1. **String-typed boolean defeats the revert lever** (Skeptic #1, Architect
   #4, QA #2 — convergent, independently grounded) — **VERIFIED**:
   `config.get` (src/config.py:204) returns the raw YAML value; probe
   `python3 -c "print(bool('false'))"` → `True`; a quoted
   `mission.parallel_milestones: "false"` keeps parallelism ON. The
   codebase already owns the hardened pattern (`tail_jobs._strict_bool`,
   :874) and the one flag whose purpose is "flip me back under pressure"
   didn't use it — wrong error direction for a revert lever. **FIXED:**
   new `config.get_bool` (string normalization, unrecognized → default +
   warning), mission.py uses it, unit tests (test_config.TestGetBool) +
   integration test (`test_string_false_flag_actually_reverts`).
2. **Stall-fallback lane runs `run_one` unguarded** (Skeptic #2, QA #3) —
   **VERIFIED** by code read: the pool lane had try/except-and-mark-failed,
   the stall lane did not, while the docstring claimed a backstop for both.
   **FIXED:** shared `_mark_crashed` backstop on both lanes +
   `test_stall_fallback_contains_crash`.
3. **Crash backstop was silent by default and unpersisted** (QA #1) —
   **VERIFIED**: `log_fn` is verbose-gated (`_log` no-ops at default
   `verbose=False`), the crash branch had no `log.warning` (its stall
   sibling did), and `ms.status="failed"` reached disk only via the
   end-of-mission save, itself `except: pass`. **FIXED:** `_mark_crashed`
   emits `log.warning("mission_dag_thread_crash …")` and calls the new
   `persist_fn` immediately; `test_dag_crash_persists_and_logs` (caplog +
   persist counter).
4. **Concurrent milestones share one working tree; the comment claiming
   worktree coverage is wrong for this path** (Architect #1) — **VERIFIED**:
   `ctx.run_worktree` is set only on the `LoopBusy` path
   (loop_init.py:430-451, `busy_policy=worktree`), which in-process
   siblings never take (interrupt.py:962 — they share the slot); the
   interrupt.py comment over-claimed phase-3b coverage. Pre-existing at
   feature level (2 same-checkout workers since Phase 10); this commit
   widens it to LLM-declared-independent milestones. **FIXED (v1 scope):**
   `_DECOMPOSE_SYSTEM` now forbids declaring independence over contended
   files ("when in doubt, keep the dependency"), interrupt.py comment
   corrected, DEFAULTS row states the shared-checkout reality.
   Unconditional worktree-per-sibling is a BACKLOG follow-up (build-sized,
   reuses `loop_parallel._run_in_step_worktree`'s pattern), not a silent
   deferral.
5. **`drain_next_mission` never learned the DAG** (all four seats — the
   sibling-lane miss) — **VERIFIED**: drain's own loop (mission.py:1303+)
   walks list order, reads no `depends_on`; heartbeat.py:1475-1487 is a
   live caller. Not a correctness bug — decompose emits earlier-index refs
   only, so list order is always a valid topological order — but the flip
   claim was over-broad. **FIXED (v1 scope):** drain docstring now says
   DAG-NAIVE BY DESIGN with the reconciliation prerequisites named
   (divergent loop: no validation gate, no hooks, own status vocabulary),
   DEFAULTS row discloses it, BACKLOG follow-up filed.

## UNSETTLED (with settling probe)

- **Real-adapter interleaving under the new width** (Skeptic #4): milestone
  validation calls can now race feature calls across milestones through the
  real `build_adapter` / subprocess fork-master lane (masters keyed by
  `(binary, model, no_tools, cwd)`, already shared across concurrent
  feature calls today). All new tests stub the adapter, so composition with
  the real stack is unproven. **Settling probe:** first box burn-in mission
  whose decomposition declares independence — watch
  `subprocess_fork_masters.json` churn and per-call latencies during
  overlap; the premise expires if the fork-master keying or session-reuse
  config changes.

## Fixed mediums/lows

- Chain-shaped missions now **bypass the DAG entirely** (`_is_chain_shaped`)
  — resolves Architect #3 (exception-absorption semantics changed for
  undecorated missions) and Minimalist #2 ("byte-identical" was false:
  thread hop + `copy_context` isolation) structurally instead of by
  wordsmithing: undecorated missions take the literal pre-DAG code path,
  and the flip is genuinely inert until independence is declared.
  `test_chain_shaped_mission_bypasses_dag` pins it.
- All-invalid non-empty `depends_on` now chains instead of silently
  ungating (Skeptic #6); dropped-milestone-ref test updated to the new
  semantics.
- Save-lock claim scoped honestly to IN-PROCESS (QA #4, Skeptic #5, QA #7:
  atomic_write's own docstring demands locked_write for cross-process
  writers — pre-existing, disclosed not changed) and the lock finally has a
  failing test (`test_save_lock_serializes_concurrent_persists`,
  Minimalist #3).
- `mission-status` CLI surfaces `depends_on` in JSON and text
  (Minimalist #5).
- "Cycle-free by construction" rescoped to the decompose WRITER; the load
  path is defended by the stall fallback, not prevented (Architect #6,
  QA #6).

## Rejected findings (with rationale)

- **Minimalist #4 (drop the `mission.milestone_workers` knob):** rejected —
  the operator's explicit posture on this feature is flip-and-gate-back;
  width is the second lever of the same kind, and the DEFAULTS row already
  labels it a width prior, not a measured optimum. Revisit if the knob
  never moves.
- **Minimalist #6 (trim the stall fallback as unreachable):** rejected —
  hand-edited `mission.json` is a real load path, the fallback is ~15
  lines, and it now carries the same crash backstop; a deadlock here would
  hang an unattended box.

## What went well
No seat found a defect in the core scheduler's ordering logic
(`_ready`/terminal-set progression), the decompose index→id resolution, or
the legacy-load chain reconstruction; the ordering/failure-parity design
decision (ordering, never gating) survived all four lenses unchallenged.
