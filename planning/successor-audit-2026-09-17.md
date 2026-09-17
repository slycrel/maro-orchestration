# Branch audit — main vs successor (2026-09-17)

Asked by Jeremy: *"do an audit of the main branch and what's intentionally
in successor (and what is not). We're in an odd transition phase and it's
too easy to get confused here… essentially it's via contract,
implementation details are a side effect, functionality and compatibility
at the high level are the target; we want to refactor both sides where we
can to keep them compatible, but the new successor should find its own
implementation, separate from a line by line port."*

The convention this audit follows is the plan's (D1 contract-not-port, D5
reference-allowed clean room, D10 separate workspaces with the SPEC shared,
D16 process artifacts vs thoughts): a feature is judged **at its edges** —
what facts get recorded, what a later process re-derives, what fails
closed — and the Go engine may reach the same edge by a different road.
"Same functionality, own implementation" is the target; "same code" is
explicitly not.

## 1. The branches

| branch | head (2026-09-17) | what it is | rule |
|---|---|---|---|
| `main` | f811a79a | the Python engine, production on this box; docs + decisions land here first when a session works Python | D8: contract-sharpening + real defects only; no behavior redesign |
| `successor` | 6a5b5df0 (after today's merge) | **the active dev branch** (Jeremy 2026-09-17). Go engine under `go/`, its planning docs under `planning/`, PLUS the whole Python tree | engine work targets `go/`; lands by `git push origin successor` |
| `jev` | 4597e85a, based on successor@efda42f0 | the typed-judgment seam (Jev / hosted / PCD) + shadow arm, 3 review rounds, `tools/pcd-sidecar/` | decree: push, don't merge (Jeremy). Touches `go/internal/run` heavily — its rebase cost grows with every run-package change on successor |
| `go-port` | 3b6efb46 (2026-08-28) | the frozen first port | D4: mined through design notes, never continued. Has not moved. |
| `hermes/pcd-formal-methods-backlog` | cdf4ec9b | one backlog entry; already in main | done |
| `hermes/pcd-formal-methods-backlog-entry` | bfa2e067 | same entry, older PR shape | dead; delete when convenient |
| `hermes/fix-run-report-link` | cd8f33dc (2026-08-05) | one-line viz fix | stale; not in scope |

**Drift before today.** The branch point was 826f8fa5 (2026-09-04).
main had 119 commits successor never saw; successor had 71 commits main
never saw. Only ONE file conflicted when merged (BACKLOG.md, both tails
appended) — the Go engine and the Python engine never touch the same
files, which is the point of D9. But the shared *documents* had forked:
successor's GOAL_BRAIN.md lacked 24 decision lines that main carried
(D10–D17 onward, every 09-05..09-16 decree), main lacked `planning/`
entirely, and BACKLOG.md differed by 1150 lines.

**Done today:** `main` merged into `successor` as 6a5b5df0 (both sides
kept, verified by a distinctive string from each). From here the Python
tree on `successor` IS main's Python tree; docs and decisions can land on
`successor` and flow back to main by the same merge in the other
direction when Jeremy wants main to have them. Nothing under `go/`
changed in the merge.

## 2. Main → Go coverage, cluster by cluster

Every main commit since the branch point, grouped. Status vocabulary:

- **HAS** — the Go engine has the functionality (commit named).
- **OWN WAY** — the Go engine reaches the same edge by a different road,
  on purpose; the divergence is named and the reason cited.
- **BY CONSTRUCTION** — the thing the Python commit fixed cannot happen in
  the Go design; the property is stated so it can be falsified.
- **OWED** — functionality main has that the Go engine lacks; ordered in §3.
- **N/A** — Python-substrate or Hermes-side machinery with no Go edge.
- **DOCS** — decisions/records; arrived with today's merge.

| main commits | what landed | Go status |
|---|---|---|
| ee76e444 … 8e2c7bce (09-04) | GOAL_BRAIN D1–D17, drift review, successor v1 approval | DOCS (merged; the same text already lived in `planning/successor-plan.md`) |
| 73f4da36 | lineage-scoped memory (`--after`; mints + recall walk the lineage) | HAS — 30393f57, f4397e9b |
| c19d619e, bd43ad31, 7e5752ac, 99b3ba16 | the landscape: Maro decides fresh/related/rerun at plan time | HAS — 0133a8d0, ca1501e2 (parser contract v3 shared) |
| 45c9ba0e, 8be02a76, 848b2ea0, fbfe6136, 3efadc54 | the shadow lane runs the Go engine as a third challenger arm | N/A on the Go side (it is the Python harness that fires `maro-go`); the Go surface it needs is HAS — 09890c93 (`runs show --json`, run summary) |
| 7b4ebb34 | no daily budget that stops work | BY CONSTRUCTION — D13/D15: the Go meter has a target and an overage record (step 13a), no cutoff exists to disable |
| 0f2d9aa4, 55551109, 84c994d9, 80ba87e7 | mailbox-arc records, secrets hand-off frame doc, app-password verdict | DOCS |
| 662a2fa3, 0d23d74c, cc888436 | secrets store (sops+age, inject policy, presence index, drop-file hand-back); host-lane file hand-off; sops/age off PATH | HAS — 7d9be8d8, 853dee82, c5da1d7e (same store, same drop file: the one place the engines share a live artifact by design) |
| 0429e073, e7dff271, 13c3c472 | operator-question lane: ask by file, pause with a time box, answer by handle | HAS — f47ae820, **OWN WAY**: no paused process; the attempt ends on the question and `answer` runs the goal again after the asked run with the answer as context (build-log pattern 109). Reason: everything the fold knows about lineage, recall and the landscape applies unchanged; the only new state is two record kinds. |
| 07cf9d8e, 3db8b45f, 8d1b509a | ask grounding gate (an ask is a claim: links resolve, a code ask says `sent`, bounce once then `unverified`); LIVE in-step ask (`$MARO_ASK_ANSWER`, `delivered`); a code is bound to the session that asked | **OWED** — `docs/OPERATOR_ASK_DESIGN.md` §7–§8 says so itself ("Go successor parity for §7–§8 is owed"). The grounding gate ports as a contract on the Question record. The live ask collides with OWN WAY above: a time-boxed input (2FA) dies with the attempt, and the Go lane ends the attempt. See §3 item 3. |
| a050cbb3, 661cf820 | answer resumes wait for the project slot; re-drive a refused resume by stamped job id | N/A — Python's project-slot scheduler; the Go serve lane queues submissions itself (step 7b) and `answer` submits a normal run |
| 996b0611, 2651d58c, 873a5fe9 | Hermes SKILL / inbox fixes | N/A (mini2 side) |
| 11199a35, cce046bd, 8e5a06fb | env-request lane: Maro installs what a job needs, root at image build only, escalate to the orchestrator; `browsers` bakes Playwright | **OWED**, behind the container executor (next row) |
| 74a2c616, 72aac36d, 9efe6f8b … 4b3706f0 (r1–r22) | container lane `executor.container: require`; typed container-auth-expired pause; heartbeat expiry warning; 22 rounds of CLI-capture / failover / accounting fixes | **OWED** as a strand: the Go engine has ONE executor kind (the claude subprocess backend, host lane, per-run work dir + tool policy). The r1–r22 fixes are the Python CLI-capture reader's — the Go stream-json parser (step 3) was built with the same failure classes as declared contracts and does not inherit them. Phase 3 "platform breadth". |
| 136fa077, 0352ff71, 1a5cf7bb | dispatch navigator binds on any move; dead-run sweep gives a killed worker an honest terminal status; follow-up dispatch lands in a fresh project | N/A (dispatch lane) / dead-run honesty is HAS — supervisor + Sheriff stuck verdicts (step 7a), a killed attempt reconciles to a terminal on restart (step 3) |
| 1dc74714, 0390fbe1 … e6999d78 (r1–r31) | landscape binding: the landscape's decision binds the loop's PROJECT (operator > landscape > named > minted), stamped `project_binding`; 31 rounds on card publication, ledger guards, config snapshots, pause API | see §2a |
| 0fb2190d, 8af73b5e | review-loop postmortem; budget decree | DOCS |
| c4beb004 (half) | LoopsBench item 1: prerequisite gate in both execute lanes | HAS — 93467e10 (plan edges as an execution contract, re-derived by the fold at the StepDone and the Fork) |
| c4beb004 (other half) | LoopsBench item 2: regression obligations at closure | **OWED** — §3 item 1 (today) |
| aa28a0ab, f11f5e96, 0f657df1, 4accf527, dd1ad6f8, 98d8ccf4, 280416ff, e855c018 | checkpoint chunks 2–9: resume by plan position, crash-safe write, durable node ids, parallel lanes write it, explicit resume fails closed, a resume claims its source, a failed mark is durable debt, the run that ends a resume settles its source | see §2b |
| cdf4ec9b, 42bb106c, f811a79a | PCD backlog entry; Jev evaluation docs | DOCS (the Jev docs were on both branches already) |

### 2a. Landscape binding — projects vs the work dir

The Python engine has *projects* (`~/.maro/workspace/projects/<slug>/`,
NEXT.md, decisions, risks) and the landscape's "this goal continues run
X" decision binds the run to X's project instead of minting a new one.
The Go engine has no projects: a run works in a **work dir** (post-v1
item 1, `--work`, per-run announced) and the landscape decides the
*relation* (fresh / related / rerun) that the plan step reads. The
functional edge Python added — *a continuing run works where the run it
continues worked, and says so in a record the fold checks* — is the part
that ports. The 31 review rounds were about the Python card/ledger/pause
machinery around that decision, not the decision. Status: **OWED (small)**,
§3 item 4 — bind the work dir to the chosen run's work dir when the
relation is rerun, record it, refuse a forged binding.

### 2b. Checkpoint / resume — what the journal answers and what it does not

| Python chunk | Go status |
|---|---|
| crash-safe checkpoint write; explicit resume fails closed on an unreadable file (f11f5e96) | BY CONSTRUCTION — the journal is framed CRC envelopes with torn-tail recovery (step 2); an unreadable record is refused, never guessed. Falsifier: `TestRecoveryTruncatesOnlyAShortTail`, `TestRecoveryRefusesForgedEnvelopes` (journal), `TestReconcileFinalizesLostReceipt` (invoke). |
| resume selects steps by plan position, not item number; durable plan-node ids; a resumed suffix keeps items/edges/binding (aa28a0ab, 0f657df1) | BY CONSTRUCTION — a Go attempt recovers from the journal by plan position (`Outcome.Steps` = settled positions; gate re-derived from the plan's own edges; 93467e10). Falsifier: `TestAgendaReuseSurvivesASecondKill`, `TestForgedPlanEdgesAreRefused`. |
| the parallel lanes write the checkpoint; a crash mid-DAG resumes at the unfinished nodes (4accf527) | BY CONSTRUCTION — fork/join over attempt refs with a kill matrix between every transition (step 8). Falsifier: `TestTwoLevelScenarioSurvivesEveryKill`, `TestAgendaKillMatrix`. |
| explicit resume end to end; refusals end like finished runs (dd1ad6f8); a resume CLAIMS its source before executing (98d8ccf4); a failed NEXT.md mark is durable debt (280416ff); the run that ends a resume SETTLES its source (e855c018) | **OWED (contract)** — this is the part the same-run recovery does not cover: a run that STOPPED (blocked, failed, asked) being continued by a LATER run, with the source claimed and then settled. The Go analog is the rerun relation + lineage; today a rerun re-plans and the prior run is never told it was continued. §3 item 2. |

## 3. The Go build queue from this audit (value order)

1. **Regression obligations at closure** (LoopsBench item 2). Contract in
   §4.1. Was already the next chunk on this branch.
2. **Continuation: a rerun claims and settles the run it continues.**
   Contract in §4.2.
3. **Ask grounding gate** (Question carries what was verified; a code ask
   without `sent` is announced `unverified`; links probed). The LIVE ask
   is a design item, not a port: the Go lane ends the attempt on the
   question by design (pattern 109), which is exactly the case a
   session-bound code cannot survive. Decide with Jeremy: a live window
   inside the attempt (Python's road) or a worker-side re-request rule.
   Recorded here; not built today.
4. **Work-dir binding under the rerun relation** (§2a).
5. **Container executor + env-request lane** — a strand (Phase 3
   platform breadth), not a chunk. Owed; not today.

## 4. Contracts for the chunks built from this audit

(filled per chunk as it lands — see the build-log entries of the same date)

### 4.1 Regression obligations at closure (LoopsBench item 2) — LANDED 2026-09-17

**Contract (shared with main — the functionality, not the code):**

| Clause | Python main (chunks 2–9) | Go successor (this chunk) |
|---|---|---|
| What is owed | the exact runner argv a FINAL-done step ran in cwd D, from real shell tool events | same: every shell `tool_effect` of a done step (AGENDA) or the complete execute (NOW) whose input parses and whose result carries positive evidence |
| Grammar | `[cd DIR &&] [NAME=value…] [uv\|poetry\|pipenv run] RUNNER ARGS`; programs refused | `internal/regression.Parse` — same grammar, same refusals (pipes, chains, redirects, globs, `$`, backticks, comments, collect-only/dry-run/no-run, `bash -c`, > 400 bytes) |
| Positive evidence | result_seen, not error, non-empty, no failure tally, family pass tally | `Passed` — same five |
| When | at restart and at closure | at closure, before the closure judge (restart = the journal: a re-run an earlier unrecorded attempt made is reused by key) |
| How | argv, shell=False, recorded cwd | `exec.CommandContext`, no shell, recorded cwd, process env + recorded assignments, driver Timeout |
| Classify | tally-first; fail beats exit 0 | `Classify` — same |
| Effect on closure | fail downgrades achieved (confidence floor 0.7); inconclusive never downgrades or vetoes | fail ⇒ Observation refuted@1 ⇒ resolver refutes `achieved` (`refuted_by_observation:regression_rerun`); inconclusive ⇒ could_not_observe@0, no effect; the judge also SEES the re-runs in its prompt |
| Record | checkpoint obligation ledger + closure decision text | `regression_rerun` record + `observation` in one journal command; the fold re-derives both and the closure prompt |
| Off switch | policy flag | none (doctrine: remove, don't disable) |
| Truncated capture | n/a (unbounded) | 4 MiB tail kept; a failure tally in the tail is still a Fail, a pass tally proves nothing |

**Intentional differences:** no carried obligation ledger (derived from the
journal at closure, so it cannot drift from the tool events); no
confidence floor arithmetic (the resolver's observation rule is the
downgrade); no per-restart re-run (the journal's reuse rule makes a
restart's re-run the same re-run); observations are first-class records
the fold checks, where main folds the decision into closure text.

