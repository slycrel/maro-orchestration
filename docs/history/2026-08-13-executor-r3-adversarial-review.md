---
status: record
---

# Adversarial review — executor image r3 (baked verbs + spin-up key injection)

2026-08-13. Commit under review: `9f5e645`. Reviewers: 2 codex CLI lenses
(Skeptic + Architect) per skill sizing (medium-large, secrets-touching).
Reviewer hallucination: **0/9 raw findings** (deduped to 5); the arc's
zero-hallucination streak holds at seven rounds.

## Intent

Implement Jeremy's 2026-08-13 decree: bake the maro verbs into executor
image r3 (never keys), inject hosted-free provider keys at container
spin-up as ENV values from host-held storage. Secrets never in image
layers/argv/logs, consent decided host-side only, planner/prompt teaching
never advertises a command the executing environment can't run.

## Verdict: REJECT → fixed same session

Three high-severity findings with cross-lens consensus; all verified
against the code before fixing (verify-before-fix). Fix commit follows.

## Findings (deduped)

1. **[high, both lenses — FIXED] The consent carrier was a second
   host-side consent surface.** `hosted_free_enabled()` honored
   `MARO_HOSTED_FREE_ENABLED` whenever config was unset — on ANY process,
   including the host. A stray host export plus a stored key would have
   authorized egress (Skeptic reproduced the path). Fix: the env flag is
   honored only inside a container, gated on the `/.dockerenv` marker
   docker itself creates — not acquirable by exporting anything. Pin:
   host-side flag alone never enables.
   Principle: boundary-discipline / fix-root-causes.

2. **[high, both lenses — FIXED] Degraded lanes over-advertised the baked
   verbs.** Prompt render and plan-time teaching keyed on config intent +
   image revision, but docker-down and auth-breaker degrades happen inside
   `resolve_container_run` WITHOUT setting run suppression — so a mode-on
   degraded lane rendered the verbs prompt (and could write plans teaching
   `maro-read`) for steps that execute on the host, where the baked names
   are not on PATH. Pre-r3 this direction was under-advertisement (safe);
   the verbs variant silently flipped it. Fix: lane availability
   (docker probe + breaker clear) joins both gates; degraded/uncertain →
   honest-absence prompt / no teaching. Remaining window is the true
   TOCTOU (lane dies between render and dispatch) — loud
   command-not-found, worker falls back; filed residual stands.
   Principle: prove-it-works / redesign-from-first-principles.

3. **[high Architect + medium Skeptic — FIXED (scrub) + reworded
   (claims)] Secret exposure claims overstated; transcript leak channel
   real.** (a) In-container, keys are in the worker's env by design (the
   decree's accepted exposure) — but a goal-driven `env` would persist the
   values into transcripts/receipts/memory records that outlive the
   container. Fix: `_scrub_secret_values` replaces injected values with
   `[REDACTED:<NAME>]` at the capture seam (`_read_captured`), so nothing
   downstream ever sees them. (b) "Never in host process listings" was
   false as stated: the docker client's env is owner/root-readable via
   /proc for its lifetime. No privilege boundary is crossed (same trust
   domain as the on-disk .env the keys come from), so this is a WORDING
   fix — comments/docs now claim exactly what holds: argv clean, client
   env owner-scoped, container env accepted-by-decree, transcripts
   scrubbed. Principle: prove-it-works.

4. **[low/medium — ACCEPTED, documented] Tag revision is operator
   declaration, not capability evidence.** `maro-executor:<anything>-r3`
   matches the regex, and docker tags are mutable — an operator retagging
   r2 as r3 asserts verbs that aren't there. Accepted: config is a
   host-trust surface here, exactly like `executor.container` itself; a
   runtime shim probe is the named upgrade if that trust proves misplaced.
   `image_bakes_verbs()` docstring now says so.

5. **[medium Architect — ACCEPTED, filed] Container hosted-free config
   diverges from host config.** Only the keys + consent carrier transport;
   provider order/model overrides don't, so in-container `maro-read` runs
   on defaults even when the host configures otherwise. Works today
   (defaults are the live path); filed in BACKLOG as a known gap with the
   obvious upgrade (transport the non-secret config knobs the same way).

## What went well

Neither reviewer found fault with: the no-pip supply-chain stance and its
apt-only dependency line; keys-never-in-layers (.dockerignore verified);
bare `-e NAME` argv hygiene; the suppression-never-selects-lane invariant
in the planner; conservative-False on unparseable tags; the fail-closed
exception paths on both gates.

## Lead judgment

Findings 1–3 accepted and fixed — all three are the change failing its own
stated guarantees, not style. Finding 4 accepted-not-built: tag trust is
the same trust we already extend to every executor config knob; a probe
adds spin-up cost to defend against an operator lying to themselves.
Finding 5 accepted-not-built: real gap, defaults-only is honest today, and
the fix rides the existing env-transport pattern when config divergence
first bites. Skeptic's "regression test for the carrier" ask is satisfied
by the new host-side pin.
