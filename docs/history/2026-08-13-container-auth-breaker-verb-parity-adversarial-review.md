---
status: record
---

# Adversarial review — container verb parity + auth breaker (23c59fa, fdb64ff, cfb52f6)

*2026-08-13, 3-lens Codex review (Skeptic + Architect + Minimalist), defensive
framing per the skill's 2026-08-13 update — no cyber-filter trips. Reviewer
CLI: `codex exec`, read-only sandbox. All three output files present.*

## Intent

Two coupled fixes from the 2026-08-13 A/B-4 diagnosis (container dispatch lane
silently DOWN ~2 days on expired OAuth; containerized executors taught CLI
verbs that cannot exist in-container):

1. **23c59fa** — lane-aware execute prompt: `execute_system_for_lane()` selects
   by config intent; the container render advertises NO fetch/read CLI and
   teaches grep/sed extraction + honest absence. Degrade-to-host only
   UNDER-advertises.
2. **fdb64ff** — reactive auth breaker: first auth-failed containerized call
   trips a marker; `on` degrades to host/fence-only, `require` refuses; one
   `backend_actionable` Telegram; self-clears on a real re-seed (live-shaped
   creds newer than trip, ≤1 docker cat / 5 min, zero tokens); doctor +
   system_health rows.
3. **cfb52f6** — breaker marker allowlisted in the deletion census.

## Verdict: REJECT

Consensus high-severity findings across all three lenses: the `require`
contract has two host-execution bypasses, and the feature's single actionable
notification is not actually guaranteed to reach the operator (swallowed on
write failure, duplicated with contradictory instructions by the generic
failover lane, rendered as a bare status line by the Telegram formatter, and
filtered out by the starter config). The mechanism core (trip → degrade →
self-clear) is sound; the edges violate the feature's own contract.

## Findings (deduped, severity-ordered; lens tags = who raised it)

1. **[high] `require` can still execute on the host.** (Skeptic)
   `resolve_container_run` returns `None` on `container_suppressed()`
   (container_exec.py:712-716) BEFORE the `require` check at :726, and
   `_run_subprocess_safe` falls back to host when the requested container has
   no resolvable cwd (llm.py:1261-1268) regardless of mode. Verified in-tree.
   Failure attribution also keys on the REQUESTED `_container_name`
   (llm.py:2686), so a host-fallback auth failure would trip the CONTAINER
   breaker. *Principle: fix-root-causes / prove-it-works.*
   → Fix: both seams enforce `require` (refuse, never host); attribution
   follows the lane the call actually ran on.

2. **[high] The one actionable notification is not guaranteed.** (Architect,
   Skeptic, Minimalist) Marker persisted before `emit()`, emit result ignored
   (container_exec.py:572-596); a trip-write `OSError` jumps the notify
   entirely; a transient Telegram failure leaves a permanently "already
   notified" trip with zero deliveries. *Principle: prove-it-works.*
   → Fix: notify independent of persist success; `notified` flag persisted in
   breaker state, retried from `auth_breaker_blocks` while unset.

3. **[high] The notification's consumer side is broken.** (Minimalist)
   `notify_telegram.format_message` has no `backend_actionable` branch — the
   payload's `summary`/`reason` (the re-seed instructions) are dropped and the
   message renders as `ℹ run auth_expired`. The bootstrap starter config
   documents `events: [run_completed, escalation]`, filtering the event out
   for any operator who copied it. Verified in-tree. The exact
   consumer-first wound (write side shipped without its read side).
   → Fix: render escalation-class events with their summary; add
   `backend_actionable` to the starter events line.

4. **[high] One failure, two contradictory emits + circuit misattribution.**
   (Minimalist) The subprocess seam emits the precise breaker notification,
   then raises; `FailoverAdapter` independently classifies the same exception
   as AUTH_ACTIONABLE and emits a second `backend_actionable` instructing a
   host `claude /login` (llm.py:830-region) — wrong fix for a container-lane
   death. `_circuit_trip` also trips the process-wide subprocess-backend
   circuit on a container-only auth failure, silently rerouting healthy HOST
   calls to the next (paid) backend. Verified structurally in-tree.
   → Fix: container-attributed failures carry a marker; the generic failover
   actionable lane and the backend circuit stand down for them (the breaker
   owns the story).

5. **[medium] Breaker transitions are not serialized.** (all three)
   check→write→notify trip and read→probe→clear/rewrite recovery both run
   unlocked (`atomic_write` explicitly provides no locking). Two concurrent
   processes double-notify and double-probe; a stale failed probe can rewrite
   the marker after a peer's successful clear (resurrection self-heals only at
   the next TTL). Violates the repo's own file_lock rule.
   *Principle: serialize-shared-state-mutations.*
   → Fix: transitions under `locked_rmw`; probe outside the lock, commit
   conditionally on unchanged trip identity.

6. **[medium] Raw auth error text persisted/notified unscrubbed.** (all
   three) `detail` flows subprocess → marker (`reason[:300]`) → log →
   Telegram. Auth error text can carry credential-shaped material.
   → Fix: `secret_scrub.scrub()` at the `note_container_failure` boundary.

7. **[medium] Corrupt marker reads as "clear" on health surfaces.**
   (Minimalist, Skeptic) `auth_breaker_snapshot` maps unreadable/malformed
   state to `None`; doctor/system_health then report clear. The RESOLVE-path
   fail-open direction is a decided doctrine (docstring: a dead session
   just re-trips — harmless, self-healing) and stands; the health surfaces
   lying is the real defect.
   → Fix: snapshot distinguishes `unreadable` for doctor/system_health;
   resolve path keeps fail-open.

8. **[medium] `container_auth=OK` overclaims.** (Skeptic) OK merely means "no
   breaker marker"; the row's expectation text claims a live OAuth session.
   → Fix: honest wording ("no auth failure observed"), keep zero-token
   probing.

9. **[medium] Prompt-lane selection races the dispatch decision; exception
   fallback over-advertises.** (Architect, Skeptic) `execute_system_for_lane`
   and `resolve_container_run` read config independently (mtime-reload can
   flip between them), and the `except` path falls back to the HOST prompt —
   the dangerous direction (dead verbs advertised into a container).
   → Fix now: exception fallback prefers the container prompt whenever config
   intent can be read as container (under-advertise direction); typed
   per-call lane pass-through filed as residual (rare race, self-corrects
   next step).

10. **[low] Doctor hides the breaker row when Docker is down.** (all three)
    Row nested under `if _dock_ok` (doctor.py:257) though breaker state is a
    file read. → Fix: render independently for any configured mode.

11. **[rejected] Same-second re-seed can never self-clear.** (Architect,
    Skeptic) True as stated (`stat -c %Y` whole seconds vs fractional
    `tripped_at`) but requires the re-seed to land in the same wall-clock
    second as the trip — an interactive /login takes minutes. Loosening the
    comparison errs the WORSE direction (false self-clear → re-trip →
    notification loop). Comparison stays strict, rationale now in a comment.
    The adjacent real fix IS taken: the probe no longer `cat`s the full
    credentials file into the host process (stat + grep for shape instead).

## What went well

- The trip/degrade/refuse/self-clear state machine core is correct, and the
  zero-token-probe discipline held everywhere including the review probes.
- `EXECUTE_SYSTEM_CONTAINER`'s honest-absence blocks and the
  render-before-host-substitution ordering were found clean by all lenses.
- The deletion-census allowlist (cfb52f6) drew no findings.

## Lead judgment

Accept 1-8 and 10 as stated (all verified against the tree, fixes bounded).
Accept 9's fail-direction half; reject the full typed-lane refactor this
session (config flip mid-step is rare, self-correcting; filed as BACKLOG
residual instead — three similar reads is fine, per CODING_NOTES).
Reject 11's comparison change as documented above; take its probe-hygiene
half. The Minimalist's "one lock/CAS state machine with probe lease and trip
generation" is right-sized to `locked_rmw` + trip-identity re-check — a full
generation counter is machinery beyond observed need.
