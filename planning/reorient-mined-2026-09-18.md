# Re-orient, step 1 and 2: what the mining found (2026-09-18)

Companion to `planning/reorient-2026-09-18.md`, which is the seed and the
decision. That file lists four first moves and says not to pass step 3
without Jeremy. This file is steps 1 and 2 done: the orphaned goal brain
mined off `origin/go-port`, the three work-side compatibility documents
read (their distilled content is in
`planning/contract-compatibility-successor.md`), and the compiled truth
**re-measured** on `successor` instead of carried across.

It also reports three things the measuring found that nobody was looking
for, one of which means a lane Jeremy believes is running has not run
since 2026-09-07.

---

## What the live box is actually doing (measured 2026-09-18)

**1. The Go shadow arm has been dead for eleven days.** The last three
firings exited 1 after two seconds each:

| fired | arm | exit | wall |
|---|---|---|---|
| 2026-09-06 09:15 | go | ok | 24.0s |
| 2026-09-06 09:20 | go | ok | 28.0s |
| 2026-09-07 09:20 | go | ok | 64.1s |
| 2026-09-12 03:50 | go | exit:1 | 2.0s |
| 2026-09-13 07:30 | go | exit:1 | 2.0s |
| 2026-09-16 10:20 | go | exit:1 | 2.0s |

Source: `~/.maro/workspace/memory/shadow_ledger.jsonl`, rows with
`arm=go`. The cause is confirmed, not guessed. The shadow lane gives the
Go engine ONE persistent workspace on purpose (`shadow.go.workspace`,
default `<workspace>/shadow-go`) so its landscape and memory accrue
across shadows. Ask that workspace for its runs today and the engine
refuses to fold its own journal:

```
maro-go: run: 01M1V00ACYB3YA57B62J86GHFE attempt 1 intent invocation
01M1V00AFR1ZQX63MRA24E01WT was not asked the intent prompt
```

The fold re-renders the intent prompt over the recorded goal and compares
it to the request the record names (`go/internal/run/fold.go:945`). That
is the interpretation boundary being re-executed on read, and it is the
right instinct. But the prompt's WORDING changed in `84a7c12a`
(2026-09-07 10:26, "the engine knows its own name — intake/planner/
executor prompts say 'named Maro'"), which is four hours after the last
successful shadow. Every record written before that commit now fails to
re-derive, the fold refuses at the first one, and every later run in that
workspace dies before it does anything. No prompt stored in that
workspace's thought store contains the new wording, which is the direct
confirmation.

**This is a compatibility failure, not a bug in the check.** A reader
tightened what it accepts on data that was already published. The
distilled standard calls the missing rule *readers only loosen*
(`planning/contract-compatibility-successor.md`); the same class is
already queued as residue from chunk 1 ("the plan request is not
re-derived by the fold — needs versioned prompt templates") and from the
landscape work ("prompt v4"). It has now cost a live lane eleven days,
silently, because a fast exit looks like a fast run.

Not fixed here, deliberately: the fix is a design choice (version the
templates and fold against the version a record was written under; or
fold the interpretation boundary only for records at or after the
template's own epoch; or stop pinning prompt bytes and pin the parsed
interpretation instead). That choice belongs in the plan. The operator
half — the stale `shadow-go` workspace — is Jeremy's call too, because
restoring the arm means rotating live run data, which nothing here may do
unasked.

**2. The shared container login is expired.** A real container call with
the shared auth volume comes back 401:

```
Failed to authenticate: OAuth session expired and could not be refreshed
```

while the cached observation in `~/.maro/workspace/memory/container_auth_liveness.json`
still reports a refresh token good until 2026-10-11. So the cached fact is
stale and the refresh itself is failing. This is not a Go-side finding:
the volume, the image and the login are shared, and Python main has run
`executor.container: require` on this box since 2026-09-13, so the next
container-required run on EITHER engine fails at its first tool-bearing
call. The fix is an operator action (a login inside the image against the
volume) and it needs Jeremy's hands or his authorization.

Worth naming precisely because the Go preflight is honest about it: it
proves the volume holds a credentials file of the right shape with a
refresh token, and says in its own contract that an unexpired SESSION is a
live question it cannot answer. Today's evidence is that the live question
is the one that matters.

**3. The host lane still runs a goal end to end.** Verified 2026-09-18
with a real run on the installed binary (a fresh workspace, haiku, tools
denied): delivered, terminal complete, closure unknown, one tail proposal.
The Go engine is not broken; its shadow lane's workspace is.

---

## Compiled truth, re-measured on `successor` (2026-09-18)

Every row measured today. The go-port file's numbers are NOT carried
across: that branch is frozen and its figures describe a different
codebase built to a different contract.

| Claim | Value | How measured |
|---|---|---|
| Branch | `successor`, 101 commits since the merge base with `main`; first commit `d8934664` (2026-09-04) | `git rev-list --count $(git merge-base origin/successor origin/main)..origin/successor` |
| Go packages | 21 | `go list ./...` |
| Production lines | 31,846 | `find go -name '*.go' -not -name '*_test.go' \| xargs wc -l` |
| Test lines | 22,268 (0.70 : 1) | same with `-name '*_test.go'` |
| Declared contracts | 57 declared, 56 generated, no drift, 0 errors / 0 warnings | `maro-go contracts gen && check && report` |
| Python modules with no mention anywhere in the Go tree | 141 of 189 | `for f in src/*.py; grep -rqi $(basename $f .py) go/` — the loosest possible test, so a FLOOR |
| Does it run a goal end to end? | Yes, host lane, verified live today | the run in finding 3 above |
| Does it run a goal end to end in a container? | Not today — the shared login is expired (finding 2). The lane itself launches, mounts and is ended correctly (chunk 5a's docker tests, and the CLI starts and reaches the API inside the container) | live probe |
| Live shadow arm | DEAD since 2026-09-07 (finding 1) | shadow ledger |

**The 141-of-189 row deserves its caveat in words, not a footnote.** On
`go-port` that measure was the honest denominator, because the goal was a
port. On `successor` the goal is a spiritual successor to the same
contract, so a Python module with no Go namesake may be fully answered by
a different arrangement (the journal answers several of the checkpoint
modules; the fold answers several of the loop modules) or may be genuinely
absent. The number is a bound on how much has NOT been thought about, and
open question 2 below is what turns it into a denominator.

---

## Intent — Jeremy's words, carried across verbatim

Mined from `origin/go-port:GOAL_BRAIN_GOPORT.md`. Same discipline as its
parent file: **a session may add to these, never paraphrase or retire
them.**

> "keep going until we have the first pass of the go port completely
> implemented and each tranche review-fixed. Then maybe we can do some
> test runs on both engines and compare."
> — Jeremy, standing goal for the port arc

> "Alright, I think our review arc is complete. Let's continue with the
> golang port, keeping in mind lessons are data"
> — Jeremy, 2026-08-21

> "On this side of the weeked, I'm questioning a little the wisdom of the
> port; hopefully we're spending time to find meaningful edges, though
> feels a little like one step forward and 3 back. Will reserve full
> judgement once we actually try using the port."
> — Jeremy, 2026-08-22

> "The port seems to be a bit more intensive on this side of it than I had
> supposed. I had thought for some odd reason we were closer to done a
> couple of days ago. Apparently not. High level, roughly where are we
> at?"
> — Jeremy, 2026-08-26

> "Let's see what we can do to speed up this port process a bit -- I'll
> put my $$ where my mouth is on this one since you're in the mines."
> — Jeremy, 2026-08-26

> "I don't want 'A faithful port' if that means 'line-by-line
> reimplementation of CPython semantics'... What I want is for us to look
> at what our python project does, and implement that in go — the
> reasoning, the pattern, the modules (and even then, that's not strictly
> necessary)."
> — Jeremy, 2026-08-28. **This is the quote the orphaned file never
> carried in its Intent section**, because it stopped being updated the
> day it was said. It is the decree the whole `successor` branch is built
> on (D1, contract-not-port).

> "new plan is to make a real plan before we execute. I'll have to think
> about it."
> — Jeremy, 2026-09-18

> "The plan is the goal for the moment, then we commit to how to implement
> the plan as phase 2. … I think you're designed with action in mind (2-3
> prompts at most and then go). I want to fight that a little, so maybe I
> should be explicit."
> — Jeremy, 2026-08-28

**Reading them together, 2026-09-18.** The reserved judgement from
2026-08-22 is still reserved, and the condition Jeremy named for settling
it — actually USING the port — is the thing finding 1 says has not been
happening. Eleven days of shadow data that everyone assumed was accruing
is the most expensive consequence of any decision on this branch, and it
was invisible because nothing watches the arm's own exit status. That is
an argument for the plan starting at the evidence, not at the next layer.

## Invariants — quoted, carried across

> "lessons are data"
> — Jeremy, 2026-08-21. Learned outputs are shared workspace DATA, never
> engine constants. Both runtimes read one store, which is why any
> byte-level divergence in what is written there is a real defect.

> "When we run multiple reviews, let's start doing the entire chunk +
> fixes, not just the latest round of changes."
> — Jeremy, 2026-08-22. (Amended by the 2026-09-16 budget decree: 2–3
> rounds, round 2 onward reads the fix diff. Both are live; the budget
> governs HOW MANY, this governs WHAT a round reads when it is not a
> fix-diff round.)

> "On same-model fallback, escalate reviewer tier after round 1."
> — Jeremy, 2026-08-22.

> "branch or no, a rebase + conflict resolution + FF merge is the answer
> here. no mechanism will save you from the conflict resolution work."
> — Jeremy, 2026-08-16. Applies to this branch's eventual landing.

> "Derive must-detect mutations from the FILE not the diff."
> — Jeremy, 2026-08-16. A guard that cannot fail is worse than no guard.

> caps are circuit-breakers, not truncators
> — Jeremy, 2026-08-21 (flagged as a paraphrase in the source file; the
> verbatim decree is in auto-memory).

**Ops invariants that are not quotes but are load-bearing**, kept separate
so the quoted set stays clean:

- **Python stays production.** Nothing on this branch changes what runs.
- **The Python tree is the specification and another session's tree.**
- **Never write under `~/.maro/` or `~/.maro-go/` from a test or probe.**
  Live ledgers. Probes get a scratch workspace by env override.
- **A build or test run owns the entire working tree.** A Go module builds
  through its import graph, so "a different package" is not an exemption.
  Parallel agents get separate worktrees.

## Threads — re-derived, not carried

The go-port thread list is about tranches and CPython differentials that
do not exist on this branch; it is preserved in git and not restated here.
What is open on `successor` today, in layer terms (the seed's layer map):

| Thread | Layer | State |
|---|---|---|
| Prompt/record compatibility across template changes | 0 (ledger) | **the live regression, finding 1.** Design choice owed; the class is also chunk 1's and the landscape's queued residue |
| Shared container login | 3 (execution) | expired, finding 2; operator action owed |
| Container executor residue | 3 | re-run an obligation inside the recorded container; no durable quarantine after a failed stop; the worker's prompt does not say it is containerized; no per-call `/tmp` bind — and a work dir under `/tmp/claude-<uid>` now has a named live failure (below) |
| Env-request lane | 3 | the other half of audit item 5; NOT started, per the 2026-09-18 decision |
| Live ask window | 1 (intent) | Jeremy's decision, owed since chunk 3a |
| Adaptation: a discovery mid-plan cannot reach the plan | 7 | the hole the seed names; nothing built |
| Falsifiers elicited at intent time | 1 | the seed's small concrete lead on the rabbit/Bugs Bunny example |
| `runs resume <run>` ignores the handle | 8 | found by chunk 5a's review, out of scope there |
| Regression classification does not consult `rerunWorld` | 4 | same |
| supervise `gave_up` race | 3 | found 2026-09-17, not fixed |
| Resolution completeness check | 6 | owed since post-v1 item 3 |
| Dead branch `hermes/pcd-formal-methods-backlog-entry` | — | needs Jeremy's authorization to delete |

**New, found by chunk 5a's live probe:** a work dir under
`/tmp/claude-<uid>` cannot be used with the container lane. Binds are
identity-mapped on purpose, so binding `/tmp/claude-1001/<...>/work`
makes docker create the missing `/tmp/claude-1001` inside the container
owned by root, and the CLI then refuses its own temp directory ("owned by
uid 0, expected 1001"). It is narrow — a real run's work dir is not
there — but it is the first concrete instance of the owed per-call `/tmp`
bind, and the cheap fix is to give the container its own tmpdir rather
than to forbid a path.

## Open questions for Jeremy

The first two have waited three weeks. Questions 3–5 are the seed's.

1. **Where is the boundary?** Does the successor stop at the
   deterministic core, with Python keeping the I/O and LLM-mediated half,
   or continue to a second engine that can run the box? Everything about
   scope depends on it, including whether the 141 unmentioned Python
   modules matter at all.
2. **What does "completely implemented" mean, in modules?** Until the
   unported set is classified into "answered differently", "dev tooling",
   and "genuinely absent", the standing goal has no denominator.
3. **Are the seed's layer boundaries right?** Specifically whether intent
   and decomposition are one layer or two, and whether adaptation is a
   layer or a responsibility inside decomposition.
4. **Does the goal brain come back as a standing separate file on this
   branch, or fold into `GOAL_BRAIN.md`?** (The port has not landed and
   Python is still production, which argues for separate. This file is
   deliberately not claiming to BE the goal brain; it is the mining
   result.)
5. **What consumer could act on a reframe?** Nothing today can act on
   "the premise changed", and a vocabulary with no consumer is decoration.

Two more the measuring added:

6. **Restore the shadow arm how?** Rotating the stale `shadow-go`
   workspace restores the lane today and loses the accrued landscape;
   versioned templates restore it without losing anything but are a build.
   Both, in which order?
7. **Is the container lane meant to be on for the Go engine's own runs?**
   It is wired and off by default. Python main runs `require`. If the Go
   engine is to be a real challenger arm, the shadow lane's Go invocation
   does not pass `--executor` at all today.
