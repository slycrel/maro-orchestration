# Go twins of the Python landscape-binding HIGHs (r26–r29) — star v9 exercise, 2026-09-16

**What this is.** The first run under star skill v9 (`.claude/skills/star/SKILL.md`,
version bump requires an exercised contract). Real question, not a drill: main
landed four adversarial rounds today on the Python landscape/project-binding
chunk (7716cd31, 0043b38a, e1f2632d, d3cbee0d). The Go engine on `successor`
carries its own landscape (feature 2), lineage and operator-question lane —
which of those defects have twins here?

## Invocation contract

1. **Goal**: for each HIGH from r26–r29, decide twin / guarded / absent /
   n/a-by-design in `go/internal` (and `go/cmd/maro-go`), with the site or
   the probe that settles it.
2. **Done-means** (falsifiable): every row below carries a `go/...:line`
   citation or a named probe with its result; the wrong result it catches is
   a verdict without a probe. Positive control: one planted row (F22)
   describes a Go site known to exist (`landscapeCandidates` recording a
   below-floor count); an instrument that misses it makes every `absent`
   untrusted.
3. **Cuts**: no code changes (Go or Python) this run; the Python fixes are
   not re-litigated; Go may legitimately lack a seam.
4. **Budget**: 5 delegations.
5. **1-shot bet**: no — one bare pass over 21 findings would name-match and
   under-probe; predicted fewer than half its rows with a real probe.

**Landscape check (row 0): fresh.** No prior star ledger or planning doc
covers Go twins of these rounds (docs/history, planning/ searched).

## Run ledger

| # | Task (outcome) | Flavor | Criteria stated? | Verdict | Map Δ | Surprise |
|---|----------------|--------|------------------|---------|-------|----------|
| 0 | Landscape check | — | — | fresh | landmark: none prior | — |
| 1 | Canonical HIGH list from the four commits (Agent subagent, same model) | commit | yes (table, per-commit count reconciliation, no invention) | **accept** — 7 spot-probed sites present in the diffs; count mismatches (r26 one HIGH split in two; r27 unlabeled per item) reported, not reconciled | 21 findings | r30 not yet landed at run time → recut |
| 1a | Recut: r26–r29 only (r30 landed later as 6a09e8bd, after task 1 ran) | taste | — | on the record | cuts edit | licensed by row 1's surprise |
| 2 | Go twin census of 22 rows incl. blind control (Agent subagent, same model) | commit | yes (four verdicts, cite-or-probe, honest not_examined) | **accept-with-amendment** — control F22 found at landscape.go:233-238; F9/F20 confirmed by master read; F17 downgraded to partial (execution sees the answer; only the similarity scan is blind) | 3 twins, 9 guarded, 4 absent, 6 n/a | delegate volunteered that F22 "reads as fix-shaped" — the control was visible as a control |
| 2b | Coverage probe (master-side, different axis): lock/LLM/submit sites in tail, sheriff, learn — the subsystems the delegate did not read | recon (VOI: an F1-class twin there would add a row) | — | no mutex anywhere near an Invoke or Submit in those three packages | edge: F1 absent extends to tail/sheriff/learn | none |

Worker per delegation: Agent-tool subagent ×2 (same model as master → mandatory refutation on each accept, done as the spot-probes above). Codex not used — the other live session holds the codex lane for round 31.

## The twin ledger

Verdicts are the master's after judging. Line numbers are as of 84a7c12a.

| id | round | Python defect (class) | Go verdict | Go site / probe |
|----|-------|-----------------------|-----------|-----------------|
| F1 | r26 | LLM call + copies under the state lock | absent | probe: every `.Invoke(`/`drive(` site in run/ has its preceding Lock matched by an Unlock; journal `j.mu` spans one frame write (journal.go:238-297); tail/sheriff/learn have no mutex near Invoke/Submit (master probe) |
| F2 | r26 | publish from a stale early snapshot | guarded | projector.go:182-187 (head read under LaneLock), :225-238 (generation swap); no mutable state file — state is folded |
| F3 | r26 | similarity on raw input (prefixes in denominator) | absent | goalText is the stored goal thought (driver.go:395-399, :446); lane is a CLI subcommand, not a text prefix (main.go:316); `grep strip\|prefix landscape.go` → 0. Caveat: nothing strips anything — mode words a caller embeds go into both goal and denominator |
| F4 | r26 | mode branch returns before binding | guarded | driver.go:446-449 lineage precedes drive; fold.go:654-655 refuses an attempt with no landscape |
| F5 | r26 | manifest load/merge/save without a transaction | n/a-by-design | no project store (`grep -rli project go/internal` → projector only); view manifests written to a fresh generation dir + rename |
| F6 | r26 | unjudged-guard fails open on malformed rows | absent | record.Validate at write (journal.go:223-237) and read (:379); StepDone invariant agenda.go:196-198; `grep -rni placeholder` → 0 |
| F7 | r27 | clarification pause exits before binding | guarded | agenda_driver.go:171-174 sits after lineage (driver.go:446-455); resume binds by `--after` (cmd/maro-go/ask.go:145 → driver.go:428) |
| F8 | r27 | clarified goal not re-stamped | n/a-by-design | goal is an immutable thought (driver.go:395-399); a question ends the attempt (ask.go:29-31); answer rides as Context (ask.go:170-173). Shared observable → F17 |
| F9 | r27 | special branches build prompts without prior/related context | **twin** | agenda_driver.go:332 `stepPrompt(goal, steps, k, results, block)` omits `rs.riders()` (Context+Related, fold.go:177) that intent (:149), plan (:186) and the NOW executor (driver.go:683) include; fold re-derives it byte-for-byte (fold.go:1084) → fix is a prompt-template version bump. Fork children likewise carry no riders (fork.go has no riders/Prompt site) |
| F10 | r27 | curation copies a neighbour run's deliverable | n/a-by-design | `grep -rni curat go/internal` → 0; deliverable is a thought bound to the run's own DeliveryPrepared (driver.go:1125-1132) |
| F11 | r27 | continuation mints instead of inheriting | guarded | driver.go:428 inherits Parent/Root from `--after`; fold.go:319-325 refuses a follower whose root differs |
| F12 | r27 | exclusion-only row treated as repairable | n/a-by-design | no repair mechanism (driver.go:1001 "refused rather than repaired around"); experiment/records.go:460-461 validates the shape |
| F13 | r27 | changed corrupt bytes replaced anyway | n/a-by-design | journal.go:44, :127-137 — corruption refuses to open; only a torn tail is truncated (:148-156) |
| F14 | r27 | deliverable copies written in place | guarded | thought.go:180 `WriteFileDurable` → fsync.go:12-41 (temp, fsync, rename, dir fsync) |
| F15 | r27 | meta-command returns a binding it never made | absent | lineage/landscape read end-to-end: three return paths (driver.go:1230-1233; landscape.go:430-434, :471-480), all committed or intake-stamped |
| F16 | r28 | branch executes pre-clarification text | guarded | driver.go:683 execute prompt = goal + riders (answer as Context); fold checks it (fold.go:1563). Residual = F9 for the AGENDA step executor |
| F17 | r28 | queued answer never publishes the clarified goal | **partial twin** | cmd/maro-go/ask.go:115-116 (Answer carries Text only), :146 (follow-up re-runs `Goal.Text`); landscape.go:229-234 scans `Goal.Text` only, never Context. Execution DOES see the answer (Context rider) — only the related-run similarity scan is blind to it |
| F18 | r28 | failed sidecar still authorizes replacement | n/a-by-design | journal.go:132-137 (no replacement); lease.json is derived, replaced only under flock (lease.go:262-267) |
| F19 | r29 | provenance stamped before admission | guarded | driver.go:459-470 ValidateIntake first; IntakeCommand :157-178 decides then commits as one command; lease acquired before journal.Open (main.go:548) |
| F20 | r29 | failure between answer stamp and resume enqueue swallowed | **twin** | cmd/maro-go/ask.go:117 commits the Answer, :139-146 launches the follow-up in a SEPARATE `withJournal`/`cmdNow`; if that fails the run stays answered (:187-188) with no committed follow-up goal (Resume drives only committed goals, driver.go:1177-1186) and `answer` refuses a retry (:103-105). First half (nil stamp still enqueues) is guarded (:117-119). No `--retry` equivalent to Python's |
| F21 | r29 | live clarification failure falls through to execute | guarded | agenda_driver.go:154-160, :171-174 — every clarity-gate failure ends the attempt as failed; NOW lane has no clarity gate by design |
| F22 | control | (planted) below-floor count recorded separately from scanned | guarded / **control found** | landscape.go:233-238, :105-106, :133 (`BelowFloor > Scanned` refused), fold.go:1705 |

## Result block

- **Deliverables**: this file (the ledger).
- **Done-means verdict**: PASS — 22/22 rows carry a citation or a probe
  (check: rows without a `go/`, `.go:` or `probe` token = 0, run at close);
  positive control F22 found by the instrument.
- **Residuals**: r30 (6a09e8bd, landed after task 1) not censused — its five
  HIGHs are failure-path twins of r29's, so the F19–F21 rows are the place
  to start. Delegate 2 read fold.go and driver.go in slices, not whole; a
  learn-ledger revision-row twin of the F6/F12 class was not looked for
  beyond the placeholder grep. F3's caveat (no stripping at all) is a
  design fact to keep in view if Go ever gains text-level modes.
- **Cost**: 2 delegations of 5 (+2 master-side probes). Both workers same
  model as the master.
- **1-shot verdict**: `loop-earned`. The judged moves a bare delegation
  could not have made: the r30 recut (surfaced by task 1's coverage line),
  the F17 downgrade (master read against the delegate's claim), and the
  tail/sheriff/learn coverage probe (from the delegate's honest
  not_examined line). The blind control worked but was recognisable as a
  control — next time plant a defect-shaped one.
- **Findings** (for the skill and for the Go port):
  - Go: **two real twins to fix** — F9 (AGENDA step executor and fork
    children get no Context/Related riders; template version bump) and
    F20 (answer-then-launch is not atomic and has no retry verb). F17
    partial (landscape scan blind to the answer) is a design question,
    not a bug.
  - Skill: the twin-census refutation fired usefully as a PROBE (the
    delegate's own census + my prompt-site grep), and the positive
    control caught nothing this run — which is the expected result of a
    working instrument, not evidence the rule is dead. Granularity: task 1
    could have been folded into task 2's prompt as context, but keeping
    it separate is what surfaced the r30 recut.
