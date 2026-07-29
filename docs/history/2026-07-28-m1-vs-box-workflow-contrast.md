---
status: record
---

# M1-local vs maro-box workflow — contrast pass (2026-07-28/29)

> **Read §7 and §8 first if you want the conclusions.** §§1–6 are the
> first pass as written on 2026-07-28, kept as the record; §7 is Jeremy's
> answers, and §8 is the corrected picture his answers produced. The first
> pass's spine — "two lanes" — is superseded there, deliberately in place
> rather than edited away: how the reconstruction was wrong is part of what
> the exercise was measuring.

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

---

## 7. Jeremy's answers (2026-07-28) — and what they do to §§1–6

All four answered the same day, plus a follow-up round. Full adjudication in
GOAL_BRAIN Decisions 2026-07-28; this section records what changes *here*.

**The reframe that supersedes the whole spine.** In his words: *"just because
I'm using 2 boxes with different contexts, we shouldn't feel tied to one or
the other — they're different contexts that happen to look the same."* And on
the purpose of the exercise: *"to help us firm up what we might be missing,
not push each box to be clones of each other, but an interleaved workflow
taking the benefits of both (assuming there are some — I suppose it's possible
one is just better than the other categorically, I'm not sure, thus the
check)."*

**§3.3 — half right, and the useful half is not the half this doc emphasised.**
The observation SURVIVES: diff review cannot see a defect that exists only in
the composition of two layers; only executing the thing can. The proposed
remedy is WITHDRAWN. Jeremy: *"I don't really want a merge gate or a
master/slave style work relationship between boxes, outside of maybe a
multi-box delegation of sorts which hasn't even been discussed."* It was also
a category error on this document's part — the one merge gate that exists
(Hermes/mini2 propose-only, decreed 2026-07-20) exists because an agent that
can rewrite its own orchestration needs a human at merge-to-main, which has
nothing to do with one dev box certifying another's work. The real remedy is
**give the M1 the ability to run a run** — clean clone plus seeded data — not
a hierarchy.

**§3.4 — REJECTED as too heavy.** *"we can hypothesize and make a best guess,
then create follow-up work to confirm or pivot."* Replacement: a magic number
lands with its provenance marked (`reasoned` vs `measured`) and a paired
backlog row naming the measurement that would confirm or pivot it.

**§3.1 — answered structurally rather than as a habit question,** by the
out-of-the-box decree below: cheap reversal matters less than every context
being able to verify the same work.

**§5 / §6.4 — what this reconstruction missed: an entire context.** It
compares two lanes; Jeremy runs three. The Poe/Telegram live prototype
appears nowhere above, and it is the only context with a *real user* in it —
therefore the only source of live-usage evidence rather than test evidence.
His own fair hit on question 4: *"I feel like you've both told me that you've
missed things and now are asking what you missed."* His substantive answer:
the gap is *"simple test-based run data, which we can generate if we want
to"*, and ideally shared rather than forcing a pairing.

**New decree this pass produced — the out-of-the-box invariant:**
*"Functionality that we add should presume that it's 'out of the box'
functionality for the project day 1 — unless it's specifically functionality
gated on prior learning data for whatever reason."* Corollary: *"the work
we're doing should be able to be verified on a clean clone of the maro
repository."* He had never stated it — *"I don't think that was a bad
assumption, but maybe it needs to be declared?"* — and it needs a tripwire,
because it was silently violated for months until the 2026-07-09 docker
clean-machine trial found pip had never installed a single module.

**Portable learning — no amendment needed; already shipped.** *"thanks, I had
forgotten we had actually built that out, nice to have a PoC here rather than
a backlog item to point to."* His "internet friends" resolves to one-way
publish (*"someone posting to reddit or X, sharing a learned set of data that
is genuinely useful"*), which `maro-pack export`/`seal` supports today.
Discovery/auto-seek stays deferred. The code-as-data *"modpack on a game"*
idea is parked by his own call: *"the conservative approach is the safe one
we've implemented and I'm ok sticking with that for the moment."*

### Resolved 2026-07-29 — and the answer reshapes §8 below

Jeremy: *"the poe-mini (mini2) doesn't have direct maro access outside of
calling the other machine to get things done... The orchestrator 2014 mini is
where poe calls into, and is the box that's doing the dev work we're
discussing between this machine (the M1) and the general headless
orchestration dev work. The monterey-poe box might have a copy of the code for
reference, but it shouldn't be using that to run anything, it should be asking
the orchestration box to do things."* No decree moves. §8 replaces the
three-contexts guess with what this actually implies.

---

## 8. The corrected picture — two machines, three kinds of evidence

The doc above compares two lanes. The follow-up round said three contexts.
Both are wrong. There are **two machines that matter to dev work**, and
**three kinds of evidence**, and one machine carries two of them.

| Context | Kind of evidence it can produce | What it structurally cannot |
|---|---|---|
| **M1** (this host) | Diffs; interactive probes of the local toolchain (three live probes settled `BASH_MAX_OUTPUT_LENGTH` in this arc, correcting a false finding of mine) | Execute real runs; calibrate anything |
| **Orchestrator mini** (2014, Linux Mint) — *two hats* | Executed runs + measured thresholds, **and** real-user traffic arriving from Poe | Interactive toolchain probing |
| **mini2** (2014, Monterey, Poe/Hermes/Telegram) | Not a dev context — it is **the user**. Holds no execution; asks the orchestrator | Anything; by design it runs nothing |

So the third thing is not a third machine. **The third kind of evidence is
real-user traffic, and it lands on the same box that does the headless dev
work.** That single fact carries the rest of this section.

### 8.1 The dual role is a feature and a hazard, and the hazard was found twice the same night

*Feature:* real usage arrives where the work happens. No separate deployment,
no staging gap — the box that ships the change is the box a real user hits.
That is why live-usage evidence is even available to this project at its size.

*Hazard:* test runs and real runs share one workspace. Run data cannot be
reasoned from unless something distinguishes them. This is not hypothetical
and not this document's discovery — a concurrent session landed
`docs/history/2026-07-28-telegram-runs-review.md` the same evening naming
**"Poe scaffolding contamination"** in the run corpus. Two sessions, working
independently, hit the same seam within hours. That convergence is the
strongest evidence in this document that the seam is real, and it is exactly
the convergent-diagnosis pattern §1 step 2 recorded on the M1 side.

### 8.2 mini2's posture is policy without detection

Jeremy, unprompted, on whether mini2 might execute from its reference copy:
*"I suppose it could choose to do that and I'd likely find out later about
that."*

The 2026-07-20 zero-creds decree is enforced **by construction for push** —
the clone is https fetch-only and holds no credentials, so it cannot land
code. Nothing enforces or surfaces mini2 *running* maro locally. That is the
same failure shape as two other items this project has already been bitten
by: the out-of-the-box invariant (declared, never tripwired, silently false
for months) and the git hooks that were dead for a month after the rename
while everyone assumed they were live.

Cheap surfacing exists and matches the retention decree (surface, let the
operator decide): a box that runs maro grows a `~/.maro/workspace`. Its
presence on mini2 is a one-line fact to report, not a gate to build.

### 8.3 What replaces the withdrawn merge gate

Nothing hierarchical — per decree. Each context contributes only the evidence
it can produce, and the *"interleaved workflow"* Jeremy asked for is **evidence
flowing to whoever needs it**, not one box certifying another:

- M1 → diffs, and probes of tool behavior nothing else can run interactively.
- Box → executed runs, measured thresholds, and real-user traffic.
- The interleave = seeded run data the M1 can execute against, which is the
  same mechanism the out-of-the-box invariant needs. **One item, not two.**

### 8.4 The honest answer to "is one just better categorically?"

He asked directly — *"I suppose it's possible one is just better than the
other categorically, I'm not sure, thus the check."* The answer is no, but
not because they are balanced:

- The M1 is **strictly weaker on evidence** — it produces one of the three
  kinds and can calibrate nothing.
- The M1 is **strictly stronger on iteration speed and on probing the local
  toolchain**, and it is the only context where being wrong early is cheap
  (§3.1: it changed target repo on turn two off a pasted screenshot).
- The box is the **only source of two of the three evidence kinds**.

If you had to keep one for *correctness*, keep the box. For *speed of
exploration*, keep the M1. They are not redundant and neither dominates —
which is the finding the exercise was run to test.
