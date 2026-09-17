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
   without `sent` is announced `unverified`; links probed). Contract in
   §4.4. Grounding gate LANDED 2026-09-17. The LIVE ask window is the
   design decision owed: the Go lane ends the attempt on the question by
   design (pattern 109), which is exactly the case a session-bound code
   cannot survive. Decide with Jeremy: a live window inside the attempt
   (Python's road) or a worker-side re-request rule.
4. **Work-dir binding under the rerun relation** (§2a). Contract in
   §4.3. LANDED 2026-09-17.
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

### 4.2 Continuation: a run that continues a stopped run claims it, and its own end settles it — LANDED 2026-09-17

**Contract (shared with main — chunks 6, 7 and 9 of the checkpoint arc):**

| Clause | Python main | Go successor (this chunk) |
|---|---|---|
| What is continued | a run whose checkpoint shows it did not finish (`--rerun`/resume of a stopped run) | a run that STOPPED: its recorded execution failed or ended partial, or its closure resolved `not_achieved`. A complete execution with closure `unknown` FINISHED (the self claim cannot promote it, nothing says it fell short; a judge-less NOW run ends this way every time) and is followed, never continued |
| Who continues | the operator's explicit resume | the operator (`--after`, which `answer` uses) and Maro's own landscape `rerun` decision; a `related` decision is a tangent, a fork child / replay arm continues nothing |
| Claim | one-shot durable claim on the source BEFORE execution; classifies live / superseded / unresolved / indeterminate | one `continuation` record per run, before attempt 1, naming source, how (`after` / `rerun`) and the journal head it was decided over; the fold refuses a claim on a source that is live, finished, or already continued, and refuses attempt 1 of a run that follows a stopped run without one |
| One per source | CLI refuses all four states; `--reclaim` re-takes `unresolved` only; the API has no override | one continuation per source, ever: while it is live the second is refused; after it ends the chain moves FORWARD — a finished continuation means "follow it instead", a stopped one means "continue it instead" (it carries the source's lineage and context). No `--reclaim` |
| Refusal | ends like a finished run with a typed verdict | recorded (the same record, `refused` set to the exact state text) and the run ENDS on it: attempt 1 (or a resumed attempt) records `failed` with reason `continuation refused: <text>`; the fold binds record and outcome both ways. The CLI also refuses before intake (`--after`, `answer`) so no goal is taken in |
| Settlement | written by whoever makes the run's last status decision; a run ending other than done keeps its claim as a replay barrier | DERIVED: the source's state is the continuation run's own outcome (live / finished / stopped) — no settlement record to forget or forge; a stopped continuation keeps the source claimed (the barrier) and is itself the thing to continue |
| Context | the resumed run reads the checkpoint (plan, done items) | the continuation's requests carry `## Continues prior run (handle, how)`: where it stopped, its goal, answer (or "recorded no answer"), its plan, where each step ended, the operator question it asked; a landscape rerun's block already carries goal/answer/plan, so only the stop line and step outcomes are added. The fold re-derives the block like the related block |
| Surface | `--reclaim`, resume status | `asks --json` rows carry `follow_up` / `follow_up_state`; `runs show` says "continued by X: state" on the source and "continues X (how)" / "continuation refused: …" on the continuation; `now --after` prints "continues: run X (stopped)" |
| Kill safety | checkpoint written before execution | crash seam `after_continuation`; a resume claims nothing twice; a kill between the landscape and the claim claims on resume |

**Intentional differences:** settlement is derived, not recorded (the
continuation's outcome IS the settlement); no `--reclaim` (the chain
moves forward instead — the thing to continue is always the newest run in
the chain, never a re-take of the source); the refusal is a record the
run ends on, not a verdict class; `unknown` closure counts as finished
(main's status vocabulary has no such state — a judge-less Go NOW run
would otherwise have every follow-up claim it and refuse the next);
Maro's own `rerun` decision is a continuation too (decisions belong to
Maro, 2026-09-05), and a rerun the judge chooses of an already-continued
source ends refused rather than silently re-claiming.

**Residue:** the landscape prompt does not yet show the judge which
candidates are already continued (prompt v4) — until it does, a rerun of
a superseded run ends as an honest refused run; `follow_up_state` in
`asks` is the derived state at read time, not a stamped one; AGENDA step
prompts carry no riders (the planner consumes them; the plan is the
executor's contract — pre-existing, its own chunk); the Answer and the
follow-up's claim are two commits (a claim landing between them leaves
the answer recorded and the follow-up ending refused); `HandleOf`
collisions are unhandled across every handle-addressed verb.

### 4.3 The work-dir binding: a run works in one directory, a continuation works where its source worked — LANDED 2026-09-17

**Contract (shared with main — the landscape binding arc, §2a):**

| Clause | Python main | Go successor (this chunk) |
|---|---|---|
| What is bound | the run's PROJECT (`~/.maro/workspace/projects/<slug>/`): NEXT.md, decisions, risks | the run's WORK DIR: the absolute directory every invocation of the run that carries a working directory runs in. Go has no projects; the directory is the whole of it |
| Precedence | operator > landscape > navigator/parent > named > minted, stamped `project_binding` | operator (`--work`) > continued (the dir the run it continues worked in) > default (the workspace's own `work/`); recorded on the attempt config as `work` + `work_binding` (`default` / `operator` / `continued`) |
| When it binds | at loop start, from the landscape decision | at attempt 1, from the continuation claim (§4.2): an unrefused claim on a source that recorded a dir binds `continued` to exactly that dir — the source's attempt-1 config, or for a run that predates the binding, the one dir all its calls that carried one recorded (the planner's too — §4.4 review r3); a `related` decision, a plain follow of a finished run, a fork child, a replay arm bind nothing (default — fork children and replay arms inherit the parent's / runner's default). Attempts after the first bound one REPEAT it — a resumed attempt works where the run works, not where the resuming process defaults to; a run whose early attempts predate the binding adopts one at its first bound attempt (`operator` to where its calls ran, else the resuming driver's choice), and that adopted binding is what later attempts, continuations and `runs show` read |
| What the fold checks | ledger guards around the card/stamp | `continued` sits on a run with an unrefused continuation and names the source's dir exactly; `default` on such a run whose source recorded a dir is refused (the override is `operator`, which the fold cannot check and does not); a later attempt that moved the dir is refused, and a bound attempt after an unbound attempt 1 is held to the binding's meaning and to where the run's executes ran; once the journal shows a bound config, an unbound one is refused (watermark, like the continuation's); every execute invocation ran in exactly the attempt's dir, every other invocation that carries a cwd carries that one, and an invocation that arrives BEFORE its attempt, or names attempt 0 of a run for anything but the landscape, is refused (review r1/r2: the lens and backend rules had the same hole — a call nothing attached could still be cited by an outcome, intent, plan or step). The door refuses a relative dir, a binding out of vocabulary, `operator`/`continued` with no dir, a dir with no binding |
| Surface | `project_binding` in the loop record, the card | `runs show` prints "works in <dir> (<binding>)"; `runs show --json` carries `work` / `work_binding`; the driver emits event stage `work` at attempt start |
| Kill safety | — | the binding is on the attempt record itself (same commit as the attempt); a resume reads attempt 1's, so no seam can lose it. A `continued` dir that is gone — and, since §4.4 review r3, ANY bound dir some call of the run has already run in — stops the run BEFORE its next attempt, first or resumed (`ErrConfig` "… is gone: restore it and resume"); it is never re-created empty under the old name; the claim stands and a resume after restoring works there. A bound dir no call has run in yet is made as usual |

**Intentional differences:** no projects are minted (the default stays
the workspace's single `work/`; a per-run dir is its own decision, not
this chunk's); the binding is a field of the attempt config, not a new
record kind (the config is where every other binding the fold checks
lives — lens, frame, backend, policy); a source that recorded no dir
(an old run, or a test) yields `default`, not an error; judge requests
carry no cwd (unchanged: a judge reads, it does not work); the operator
override is recorded as unverifiable and trusted as the operator's own
flag.

**Residue:** a run that started with `--work X` and died before attempt
1 resumes under the resuming process's choice (the flag lives nowhere
before the attempt record); two spellings of one directory (a symlink)
are two strings to the fold, and a retargeted symlink is a move the fold
cannot see; `default`/`operator` are self-attested — the journal's
writers are the engine's own, the fold detects inconsistency, not a
hostile writer; the binding watermark, like the continuation's, means a
mixed-version writer set on one journal is unsupported; AGENDA judge
calls drop the cwd the same way NOW's do (pre-existing).

### 4.4 The ask grounding gate: an operator question is a claim the engine checks before the operator sees it — LANDED 2026-09-17

**Contract (shared with main — `docs/OPERATOR_ASK_DESIGN.md` §7):**

| Clause | Python main | Go successor (this chunk) |
|---|---|---|
| What is checked | `operator_ask.ground(ask)` on the host before any card goes out: every link in `question`/`why`/`no_input_alternative`/`sent` (≤5; HEAD then GET, 8 s) must answer < 400; a request for a code (`asks_for_code`) must carry `sent`; a code request must be LIVE (§8) | `ground` (`go/internal/run/ground.go`) after the execute that wrote `$MARO_ASK`: the same link probe (same fields, cap, method order, timeout; `Driver.ProbeURL` seam) and the same code-request rule (`asksForCode` = Python's regex + "code" in the lowercase question) on a new `sent` field of the ask file. The live rule cannot be a rule here (below) |
| What a failure does | bounce once: the step re-runs with "Your question to the operator was NOT sent — it failed a check: …" at the top of its context; the ask is archived beside the next one (`-1` suffix on a same-second archive); a second failure passes through with the problems on the record and the card as `unverified` | the same shape as a RECORD: `question_bounce/1` (attempt n, `step`, the execute `invocation` whose worker wrote it, the `ask` as written, `problems` [{check, link, detail}]) is committed BEFORE the re-run; the step runs once more with the bounce block in its request (NOW: after the goal, before the riders; AGENDA: after "## Your step", before the recall block); the ask is archived under a unique `-N` suffix. A second failure passes through on the `question/1` record as `unverified` (hard problems + soft notes); the run's reason reads "needs answer: <q> [unverified: …]" |
| What the Question record carries | `unverified`, `sent`, `live` | `invocation` (the execute that wrote it — identity), `sent` (display), `unverified` (routing). ABSENT invocation = a question that predates the gate |
| What the fold checks | — (the gate is a host-side function; the ledger records its trace edge) | `checkQuestionBounce`: attempt Executing, no Question yet, one bounce per step, invocation = a landed execute of this run (attempt ≤ the bounce's), not already bounced, not cited by a StepDone; each problem re-derives from the ask (a `link` is one of the ask's links; `code_unsent` iff the ask asks for a code with no `sent`, and never left out; `code_lane` never in a bounce). `checkQuestion`: an invocation, once the journal shows grounded questions (watermark), is required; it is a landed execute of this run, not bounced; `unverified` re-derives from the ask; a bounced kind rides through only after a bounce of the same step; a code request with no `sent` must declare `code_unsent`; the lane note iff the question asks for a code. A bounced call is CONSUMED: an Outcome or StepDone citing it is refused, and the fold renders the re-run's request WITH the bounce block (`checkExposure`, `stepRequest`, the step-verdict evidence search) |
| Recovery | the retry idiom of the loop | a bounce is its attempt's: a crash after the bounce resumes with a fresh call and no block (one call's cost). A crash after the execute landed but before its ask was read leaves the file where the call put it: the resumed attempt grounds it as that call's — its question (zero new calls), or a bounce the resumed attempt commits citing the recovered call, then one re-run there. A crash after the question was committed (NOW; AGENDA: after the step's judge, after the step) resumes to the journal's question, not the file's, and the run does not go on past it. Any ask file present BEFORE a fresh execute is archived first (`ask_stale_archived`): a call is never credited with a file it did not write |
| Frame | `instructions` in the ask frame | the ask instructions live in the execute frame; every execute request — NOW's and, since review r1, every AGENDA step's — begins with the attempt's frame, and a resumed attempt runs under the RUN's frame (the first attempt's) when the resuming process has none |
| Frame | "links must resolve; a code request says in `sent` how YOU triggered delivery (choose the SMS or authenticator option first) and what you saw" | the same sentence, verbatim, in `AskInstructions` |
| Surface | the card + Hermes leg: `unverified` lines | `maro-go asks` rows `sent`/`unverified`/`bounced`; `runs show` the same three lines; events `ask_bounced` then `ask` |

**Intentional differences:** the live rule (§8: a code request must come
from a live ask) is a SOFT note here, `code_lane` — "a code is consumed by
the session that asked for it, and this run ended on the question: the
follow-up run may have to request a fresh one" — because the Go lane ends
the attempt on the question by design (pattern 109); it rides on every code
request as `unverified`, never bounces, and is the honest stand-in until the
live-ask decision (a window inside the attempt, Python's road, or a
worker-side re-request rule) is made with Jeremy; the bounce is a record
kind, not a schema version of `question`, because the fold must re-derive
the re-run's request from it; a bounce is attempt-scoped (a crash after it
costs one call), which keeps recovery a reuse of landed calls with no
bounce state to carry across attempts; the invocation on the question is a
watermark field, like the continuation's and the work binding's.

**Residue:** the live ask window (owed decision — with review r1's added consequence: a bounce for an unrelated link problem makes the re-run trigger a second code delivery, which may invalidate the first or hit a provider's resend limit; a delivery receipt that survives the bounce is part of that design); the URL probe runs on the
host with the worker's own URLs — the same SSRF posture as Python's gate,
not widened, not narrowed; Go's `\w`/`\s` are ASCII where Python's are
Unicode (a code request phrased in non-ASCII word characters could differ
at the margin); a failed step's ask file is not read (pre-existing: the
ask lane reads the ask only after a landed execute); a crash after a bounce
re-runs the step from scratch on resume; the five-link cap is silent
(Python's is too); replay arms inherit the frame's ask path and have no
ask channel (pre-existing; owed with the replay strand); judge calls
carry no cwd and `WorkOperator` is self-attested (chunk 4's stated
choices, re-filed by r1 with no new consequence).
