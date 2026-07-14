---
status: record
---

# Executor session-reuse production prototype — 2026-07-14

## Decision

Keep `executor.session_reuse` **default off**. Retain the opt-in prototype for
broader burn-in: one fixed-model production pair suggests lower provider cost,
but it showed no executor speed benefit and was slower end to end. This is a
cost hypothesis, not a demonstrated latency optimization.

## Protocol

Both arms used `ClaudeSubprocessAdapter(model="sonnet")`, the same goal,
`REFERENCE.md`, and three preset steps. The third was a cuts `[boundary]` that
expanded after the first two steps, so the treatment had two bounded segments.
Each segment was expected to start fresh and resume once. Automatic validation
and adaptive execution were disabled; deferred learning removed post-run lesson
spend. The treatment ran first, reversing the order of the earlier exploratory
pair. Raw workspaces are retained under ignored
`.run-workspace/session-prototype-ab/rep2-{treatment,control}`.

Correctness was checked directly: both `FACTS.md` and `FINAL_REPORT.md` contain
all four source facts and both verification steps reported exact agreement with
`REFERENCE.md`. This tiny four-fact task is an integration check, not a broad
quality evaluation.

## Fixed-Sonnet result

| Metric | Treatment | Control | Interpretation |
|---|---:|---:|---|
| Status | 4/4 done | 4/4 done | tied on the bounded artifact check |
| Provider-reported executor cost | $0.4413621 | $0.6124104 | treatment 27.9% lower, n=1 point estimate |
| Executor-step wall time | 65.625s | 65.523s | no measurable speed benefit |
| End-to-end wall time | 78.122s | 73.909s | treatment 5.7% slower; boundary planning varied |
| Resumed calls | 2/4 | 0/4 | exactly one fresh + one resume in each treatment segment |

The provider's `total_cost_usd` is per CLI invocation, not cumulative across a
resumed session. Retained spike data proves this directly: the same session's
five calls reported $0.0775, $0.0046, $0.0044, $0.0043, and $0.0046 rather than
a monotonically increasing total.

An earlier exploratory production pair used per-step model routing rather than
a fixed model. It was deliberately treated as inconclusive: treatment took
188.6s in the executor loop versus 180.6s control and its internal estimated
cost was $0.3190 versus $0.3088. The fixed-Sonnet replication removed that model
variance but still did not reproduce the synthetic spike's latency win.

## Adversarial review and hardening

Three core opposite-model reviewers and three discretionary personas were
launched. Skeptic, Architect, Security/Abuse, and Experimentalist completed;
Minimalist and Failure Operator hit the ten-minute ceiling. The verdict was
`REJECT (INCOMPLETE REVIEW)` before fixes. Accepted findings produced these
changes:

- Missing-session fallback now requires Claude's short, plain-text,
  pre-execution diagnostic. A quoted marker inside NDJSON/model/tool output can
  no longer replay an effectful step.
- Every delta carries the loop's latest authoritative audited prior-step state,
  not only the worker's raw prior narration.
- High-risk external-content sanitization or scanner failure rotates and
  immediately checkpoints the session, because raw hostile content remains in
  provider history.
- Dynamic tool expansion rotates explicitly, stays stateless for the one-off
  re-call, accumulates every provider call's cost, and preserves whether any
  call in the step resumed.
- In-flight checkpoints omit provider session state structurally. Restored IDs
  and signatures are shape-validated at the checkpoint boundary.
- All rotations are persisted immediately, preventing a crash from resurrecting
  a discarded session. Goal/context identity is recomputed per step.
- `executor.session_max_turns` defaults to six, bounding clean segments that
  never encounter a semantic boundary.
- Run evidence records only an eight-character session prefix; the full ID
  remains confined to the resumability checkpoint.

The feature must remain opt-in until more counterbalanced real goals show a
repeatable cost benefit without correctness or latency regression.
