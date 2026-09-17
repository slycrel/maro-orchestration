---
status: record
name: 2026-09-17-judgment-providers-adversarial-review
description: Adversarial review of the Go judgment-provider seam (branch jev, successor..HEAD) — 4 Codex seats, REJECT with verification ledger, fixes + pins same session, round 2 on the fix diff.
---

# Adversarial review — judgment providers (branch `jev`, `successor..3b162cdc`)

Date: 2026-09-17 (overnight). Four seats (Skeptic, Architect, Minimalist,
Expert QA) on Codex, opposite-model, all four exited 0 with numbered
findings in the final message. Artifacts: `/tmp/adversarial-review.2zTbqH/`
(five per seat, transcripts included). The Go tests could not run inside
the reviewers' read-only sandbox (`/tmp` build cache), so their Go findings
are source-traced; the sidecar findings ran as in-memory handler probes.

Per the TypeSafe guardrails, nothing here quotes a Jev agreement, latency
or cost number; the private replay lives under `~/.maro/workspace/judgment/`.

## Intent

One typed judgment seam (`go/internal/judgment`) behind which the engine's
three judges ask their one question — with the incumbent LLM judge
refactored to be a provider like the new ones (`llm`, default), a cheap
hosted model (`hosted`), TypeSafe Jev (`jev`) and the experimental local
sidecar (`pcd`). A shadow arm asks configured second opinions the same
question and records them where the resolver cannot read. Fresh installs
behave exactly as before.

## Verdict: REJECT → fixed same session, round 2 on the fix diff

All four seats converged on the same five HIGHs from independent probes
(source census of the invocation closure, `git show successor:` of the
old renderer, `rg` of the terminal guards and the transcript path). The
convergence is not counted as evidence; each entry below was re-probed
in the tree.

## Verification Ledger (HIGHs)

1. **AGENDA never asks the configured primary provider** — **VERIFIED.**
   `agenda_driver.go:72` chose `d.judge(a)` for every tool-less call,
   `PurposeJudge` included; `d.primary(` was called only by the NOW
   closure (`driver.go:931`). `agenda --judge-provider jev` would have
   rendered the wire body and posted it to the subprocess judge, and the
   fold would then have refused the recorded binding. In range
   (70eba74d). **FIXED:** the closure selects `d.primary(a)` for a judge;
   pin `TestAnAgendaPrimaryProviderIsTheOneAsked` (the judge backend
   scripts only intent + plan, so any judge reaching it exhausts the
   script; the run folds).
2. **Existing judged journals no longer fold** — **OUT-OF-SCOPE
   (pre-existing class).** Settling probe run: the `successor` binary
   (efda42f0) on a copy of the live `~/.maro/workspace/shadow-go` journal
   fails at the FIRST run with `intent invocation … was not asked the
   intent prompt` — the 2026-09-07 intake-prompt change (84a7c12a)
   already did this, with no version dispatch. The `jev` binary fails at
   the same record. This chunk breaks nothing that folds today. Lead
   recorded below: journal↔template versioning is an engine-level gap.
3. **Validation after the terminal is committed; partial replies can
   verdict** — **OUT-OF-SCOPE** for the guards (`!= TerminalFailed` at
   the three AGENDA sites and the NOW closure are byte-identical on
   `successor`); the invocation terminal is the transport's outcome and a
   parse refusal is `unjudged`, as designed. The one new exposure —
   hosted `finish_reason: length` with a parseable body — errs toward
   accepting a JSON object that did fully parse. Accepted, recorded.
4. **The resolved key could reach a transcript** — **VERIFIED.**
   `openai.go:121` stored the raw error body as `Transcript`, which
   `shell.go:277` persists; both scrubs matched only a literal
   `"bearer "` prefix. Both files in range. **FIXED:** `invoke.Redact`
   strips the resolved key VALUE (and any bearer token) from the reason,
   the transcript and the body in both clients; pins
   `TestAnEchoedKeyNeverReachesTheTranscript`,
   `TestAnEchoedKeyNeverLeavesTheProvider`, `TestRedact…`.
5. **The registered one-minute judgment timeout was twenty minutes in
   production** — **VERIFIED.** The shadow passed `d.Timeout` (20 min
   from both CLI constructions) and both HTTP clients preferred
   `req.Timeout` over their own. **FIXED:** a provider's timeout is a
   ceiling over the caller's budget (both clients), and the shadow asks
   under `judgment.DefaultTimeout`; the registry entry now says so; pins
   `TestTheProviderTimeoutIsACeilingOverTheRequestBudget`,
   `TestTheHostedTimeoutIsACeiling`. Shadows still run inline after the
   primary — bounded now to one minute per shadow, which is the accepted
   cost of measurement on this branch.

## Fixed mediums

- **Fork children and `runs resume` dropped the judgment binding**
  (fork.go:583, main.go:531) — VERIFIED. Child construction extracted to
  `childDriver` (the one place a child inherits from its parent) and the
  binding inherited; resume wires every provider at its defaults. Pin
  `TestAForkChildInheritsTheJudgmentBinding`.
- **`now --judge-provider X` without `--judge-model` never judged**
  (main.go:423 `ModelJudge: jb != nil`) — VERIFIED; a non-default
  provider now switches the NOW closure judge on.
- **"Strict" decoder: `Decoder.More()` is not EOF; duplicate keys are
  last-wins; a distribution's total was never checked** — VERIFIED
  (encoding/json semantics). EOF required, duplicate keys refused at any
  depth, distributions must sum to 1 ± 0.05; six refusal fixtures and a
  rounding negative control added.
- **Sidecar framing** (server.py:111) — VERIFIED by the reviewers'
  executed probe. Bounded non-negative `Content-Length` (413 past 4 MiB),
  a 30 s socket read deadline (408), invalid UTF-8 → 400; two tests
  including a body that never arrives.
- **Report hid the unmeasured population** — the summary now counts
  judge verdicts with no shadow record (`Unshadowed`), so a shadow lost
  between the primary verdict and its control record is visible as a
  gap. Crash-time re-asking of a shadow is deliberately NOT added:
  measurement is never re-run to look complete.

## Out-of-scope leads

- Journal ↔ prompt-template versioning: every prompt-template change on
  `successor` since 84a7c12a orphans earlier journals at the fold. The
  live shadow-go workspace is already in that state. Either a recorded
  template version with legacy renderers kept, or an explicit "journals
  are per engine version" decree with the workspace rotated on upgrade.
- Partial judge terminals: whether a `partial` judge reply should ever
  verdict is a successor-wide question, not this seam's.

## What went well

- The shadow-isolation test's adversary (a wire provider that always
  answers the LAST option) meant the one finding it could not catch was
  precisely the one every seat found: the primary path had no
  non-default coverage. The new pin closes that hole rather than adding
  another shadow case.
- Reviewers settled the "old journals" HIGH with a `git show
  successor:` probe; the ledger settled it further with the binaries.
  A finding that is true and pre-existing routes to a lead, not a fix.
