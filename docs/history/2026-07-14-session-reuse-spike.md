---
status: record
---

# Per-segment Claude session reuse — measured spike (2026-07-14)

## Question

Is the parked idea worth engineering: keep one headless `claude -p` session
inside a cut/boundary segment, then rotate to a fresh session seeded from the
segment's distilled state at the next boundary?

This is an investigation result, not production session reuse. The current
worker model remains one fresh subprocess/session per step.

## Protocol

Reproducible runner: `scripts/session-reuse-spike.py`.

- Claude Haiku, production-like `--strict-mcp-config`, no tools, permissions
  bypassed so permission UI cannot contaminate timing.
- Five sequential recall questions over approximately 38K cache-creation
  tokens of reference context (CLI/system context included in reported usage).
- Fresh arm: the full context is sent to five independent sessions.
- Resumed arm: full context is sent once; steps 2–5 use `--resume <session_id>`
  with only the next question.
- Arm-specific/per-run text prevents the full user context from sharing a
  prompt-cache entry across arms or replications.
- Correctness is gated: exact scalar answers and a complete four-field JSON
  synthesis. A fast arm that forgets state does not pass.
- Raw JSON for every call plus `summary.json` is retained locally under
  `.run-workspace/session-reuse-spike-20260714/`.

The first attempted run is retained as a failed protocol specimen. Its wording
(`forbidden action` plus repetitive `inert padding`) triggered prompt-injection
refusals in two fresh sessions, and its checker rejected otherwise-correct
fenced JSON. Neither row is included below. The neutralized protocol and
fence-aware semantic checker were tested before the two valid runs.

## Results

| Valid run | Order | Resumed | Fresh | Wall reduction | Resumed cost | Fresh cost | Cost reduction | Correct |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| run-2 | resumed first | 15.983s | 24.509s | 34.8% | $0.095385 | $0.412525 | 76.9% | 5/5 both |
| run-3 | fresh first | 18.141s | 25.352s | 28.4% | $0.133423 | $0.503191 | 73.5% | 5/5 both |
| combined | counterbalanced | 34.124s | 49.861s | **31.6%** | $0.228808 | $0.915716 | **75.0%** | **10/10 both** |

Across steps 2–5 (after each arm's context-bearing first call), resumed wall
time was 22.581s versus 36.399s fresh: a 38.0% reduction. Combined cache-
creation input was 77,374 tokens resumed versus 356,231 fresh; resumed calls
instead read the already-created conversation cache.

The CLI's summed `duration_api_ms` occasionally exceeded measured process wall
time on individual fresh rows, so wall-clock timing uses the runner's monotonic
process measurement. Cost is the CLI/provider's reported `total_cost_usd`, not
derived from either timing field; the raw provider fields remain captured for
audit.

An invalid-session probe (`--resume 00000000-0000-4000-8000-000000000000`)
exited 1 in 0.99s with `No conversation found with session ID ...` and did not
run the supplied prompt. The missing-ID case therefore fails explicitly rather
than silently starting fresh. Expiry/eviction behavior still needs the same
fresh-fallback handling in the prototype.

## Decision

**Worth the effort as a bounded per-segment prototype.** The effect survived
arm-order reversal with equal correctness and is large enough in both latency
and spend to matter. It does not justify per-run monotonic sessions: the earlier
1.4M-token step is still evidence that context needs rotation. Cut boundaries
remain the natural reset point.

Before production wiring, the prototype must answer these correctness questions:

1. Session identity and crash recovery: where the session ID lives, what happens
   when it expires, and when a fresh fallback is mandatory.
2. Tool/permission/cwd continuity: resume only when model, persona, project
   fence, repository/worktree, permission policy, and tool configuration match.
3. Step provenance: keep per-step transcripts, token/cost attribution, claim
   verification, and inspector events even though the provider conversation is
   shared.
4. Boundary rotation: distill verified probe evidence to a state artifact,
   start a fresh session at expansion/replan boundaries, and cap turns/context
   so a segment cannot become the per-run ditch.
5. Failure isolation: a poisoned or confused session must be discarded on a
   failed step/replan signal rather than resumed into later work.

Do not implement this as a blind `--resume` flag on every subprocess call.

## Adversarial review

A real Claude Haiku review **APPROVED** the spike and the bounded-prototype
decision. It verified the counterbalanced results, cache create/read direction,
retained failed protocol, arithmetic, and explicit production unknowns. The
review called out the failed run's injection-sensitive wording as remediated,
not a defect in the two valid rows; real tool-bearing/adversarial step content
remains a prototype test. Session expiry and state poisoning were already
correctly deferred to that prototype. One accepted LOW hardening made the JSON
correctness gate require exactly the four expected fields rather than allowing
extras. The invalid-session probe above sharpened the fallback contract. The
review's concern that timing noise weakens cost was rejected: billed cost comes
directly from `total_cost_usd`, not from `duration_api_ms`.
