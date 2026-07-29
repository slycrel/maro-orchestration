---
status: record
---

# M1-local vs maro-box workflow — first contrast pass (2026-07-28)

BACKLOG "M1-contrast pass": *"Would be interesting to contrast my M1 local
workflow with the maro box workflow... I think there's overlap but it's not
going to be quite the same, maybe that's worth a pass as well to surface some
additional insight."*

This is the **M1 side, written from the M1 side** — the one vantage the box
cannot supply. It is grounded in one long session (2026-07-27, the
`token-lean-fetch` arc: Cloudflare/markdown fetch → raw capture → mid-step
token brake → three adversarial rounds → landed), not in recollection. Where a
claim comes from that session's evidence it says so; where it is inference it
says that too.

Boundary decree respected: patterns only. No work-side artifacts, no work
content, nothing from the work laptop — "M1" here means this dev host, per
BACKLOG.md's own usage ("this M1 dev host").

---

## 1. What the M1-local loop actually was (observed)

Reconstructed from the session, in the order it happened:

1. **Question, not ticket.** Entry was a half-formed ask ("check my link-farm
   repo... seems like there's a way through Cloudflare that's free?"), pointed
   at the *wrong repo*. Two turns of evidence-gathering redirected it to maro.
2. **Diagnose before building.** Read the live code paths, found the capped
   fetch chain already existed and was unreachable from the `claude -p`
   backend. The repo's own BACKLOG had independently reached the same
   conclusion — convergent diagnosis, arrived at separately.
3. **Chunk → land.** Small commits, full suite green each time, push to a
   branch.
4. **Cross-model adversarial review.** 3 Codex reviewers (skeptic / architect /
   minimalist), opposite model by construction.
5. **Verify each finding before acting on it.** Reviewer claims were
   re-derived locally — including two that turned out to be *wrong or
   overstated*, and one of my own suspicions that was wrong.
6. **Remediate → re-review → land.**
7. **Delegate the separable chunk** (the token brake) to a sub-agent on the
   top tier, in an isolated worktree, with an explicit "report what you could
   NOT enforce" clause.

Steps 4–7 are the part that is *not* just "write code carefully".

## 2. What the box loop is (from the repo, not observed here)

CLAUDE.md end-of-chunk discipline; `scripts/land.sh` ff-only self-landing;
the autonomous agent loop (decompose → execute → introspect); heartbeat and
evolver cadence; the runtime workspace at `~/.maro/workspace`; per-run
artifacts under the run dir; cost/token circuits (now including the brake this
arc added).

## 3. The contrasts that actually mattered

### 3.1 Interactive redirect vs committed plan
The M1 session changed target repo entirely on turn two, on the strength of a
pasted screenshot. The box's loop commits to a decomposition and then defends
it (redecompose/replan exist precisely because reversing is expensive there).
**Insight:** the M1 lane's cheapest superpower is *being wrong early and
cheaply*. The box's is *not needing a human to continue*. These optimize for
different failure modes, so "make the box more like the M1 lane" is probably
the wrong ambition — the right one is making the box's early-wrongness cheaper.

### 3.2 The adversarial round is where the value concentrated — on both sides
Hard numbers from this arc:
- Round 1 (fetch/capture): REJECT. ~10 findings. Suite was **6612 green** and
  had caught **none** of them.
- Round 2 (token brake): REJECT. Consensus on `token_runaway` being retried.
  Suite was **6642 green**; again caught none.
- Every round found at least one thing the author (me, and the sub-agent) had
  stated *with confidence* and gotten wrong.

**Insight:** the green suite is near-useless as a proxy for "is this safe to
land" on security- or contract-shaped changes. It pins behavior the author
already understood. The adversarial pass pins the behavior they *assumed*.
This is not new to the repo (the C1–C4 container arc records four REJECTs)
but this arc is a fourth and fifth independent replication.

### 3.3 The M1 lane's blind spot, demonstrated
My `token_runaway` fix delivered no-retry but **not** run-continues: I returned
`loop_status=""` and asserted on the *decision object*, never driving the loop
to see what the terminal handler did with a falsy status (it coerces to
`"stuck"`, which breaks the loop). A brake-killed step silently stopped the
whole run.

That was caught **box-side**, by the merge-gate round — not by the M1 lane's
own three-reviewer pass.

**Insight, and the most useful thing in this document:** the M1 lane reviews
*diffs*; the box lane reviews *runs*. A defect that only exists in the
composition of two layers is invisible to diff review and obvious to run
review. The two lanes are not redundant — the merge gate caught exactly the
class the local review structurally cannot.

### 3.4 Calibration is box-only
Three numbers in this arc (fresh ceiling, weighted ceiling, Bash cap) could not
be set from the M1 side at all: the healthy-step distribution lives on the box.
The M1 lane produced *reasoned* numbers; the box produced *measured* ones
(123 cost-metered steps; healthy p95 259K, max 478K).
**Insight:** anything whose correctness is a threshold should be shipped from
the M1 lane in observe-only mode by default, and calibrated box-side. The M1
lane should be structurally discouraged from picking magic numbers.

### 3.5 Verification asymmetry
The M1 lane can run the real Claude Code CLI interactively and probe undocumented
behavior — three live probes settled `BASH_MAX_OUTPUT_LENGTH`'s semantics and
corrected a false finding of mine in the process. The box lane can run *real
goals* with real cost. Neither can do the other's check.

## 4. Where they genuinely overlap

Chunk discipline, land-don't-linger, adversarial review before merge,
verify-before-fix, record-the-open-items-honestly. These already read as one
workflow; HOUSE_STYLE.md can state them once for both lanes.

## 5. What I could not determine from here

- Whether the box lane's *subjective* feel matches this reconstruction — I only
  have its artifacts, not the experience of running it.
- Whether the redirect-early pattern (§3.1) is a deliberate habit or a
  consequence of the interactive medium.
- Whether the M1 lane's heavier delegation (sub-agents, worktrees) is taste or
  a response to context limits.

## 6. Questions for Jeremy

Per the recorded iteration protocol (interview-shaped elicitation — "I'm a much
better question answerer than an essay writer"):

1. §3.1 — when you redirect hard early on the M1 (wrong repo, wrong framing),
   is that a *habit you'd want the box to have*, or is cheap-reversal simply
   not available there and you'd rather the box commit harder?
2. §3.3 — does the diff-review / run-review split match your intuition? If so,
   should the M1 lane stop trying to certify anything cross-layer and just
   hand it to a merge gate by default?
3. §3.4 — do you want a hard rule that the M1 lane never ships a live threshold
   (observe-only until box-calibrated), or is that too rigid?
4. What do you do on the M1 that this reconstruction *missed* entirely? The
   session is one sample and a self-report — the failure mode of §3.3 applies
   to this document too.
