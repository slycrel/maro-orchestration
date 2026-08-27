# Goal-Brain Addendum: the Go port

**Scope: branch `go-port` only.** This file is an addendum to
`GOAL_BRAIN.md`, not a replacement. It exists because the port is a
long-running arc on a branch that may never merge, and its compiled truth
would otherwise live only in a session's memory or be spread across a
13,000-line `go/PORT.md`.

**Merge contract.** Every section below has the same name and the same
ownership rules as its twin in `GOAL_BRAIN.md`. If the port lands, each
section merges into the corresponding section there — Intent and
Invariants as quoted additions, Decisions and Journal appended in date
order, Compiled truth folded in with its bases intact, Threads and Open
questions merged as lists. If the port is abandoned, this file moves to
`docs/history/` whole and the Decisions section is still the record of
what was decided and why.

Same discipline as the parent file: **Intent and Invariants are Jeremy's
words, quoted verbatim, and a session may add but never paraphrase or
retire them.** Everything else is system-maintained. Compiled truth
carries a verification basis per claim; a claim with no basis does not
belong here. When this file disagrees with `go/PORT.md`, `BACKLOG.md`, or
any narrative doc, **this file wins until corrected**.

---

## Intent (human-steerable)

> "keep going until we have the first pass of the go port completely
> implemented and each tranche review-fixed. Then maybe we can do some
> test runs on both engines and compare."
> — Jeremy, standing goal for this arc

> "Alright, I think our review arc is complete. Let's continue with the
> golang port, keeping in mind lessons are data"
> — Jeremy, 2026-08-21, opening the arc

> "On this side of the weeked, I'm questioning a little the wisdom of the
> port; hopefully we're spending time to find meaningful edges, though
> feels a little like one step forward and 3 back. Will reserve full
> judgement once we actually try using the port. Thanks for humoring me
> with python-maro attempts, feels not quite right and also fitting to try
> anyway."
> — Jeremy, 2026-08-22

> "The port seems to be a bit more intensive on this side of it than I had
> supposed. I had thought for some odd reason we were closer to done a
> couple of days ago. Apparently not. High level, roughly where are we
> at?"
> — Jeremy, 2026-08-26

> "Let's see what we can do to speed up this port process a bit -- I'll
> put my $$ where my mouth is on this one since you're in the mines."
> — Jeremy, 2026-08-26

**Reading these together:** the judgement is *reserved*, not granted and
not withdrawn. The condition Jeremy named for settling it is **actually
using the port**, and he has since funded parallel capacity rather than
called it off. So the arc's job is to reach a usable second engine as
cheaply as honesty allows — not to maximize surface ported, and not to
quietly narrow scope either.

---

## Invariants (human-steerable, quoted)

> "lessons are data"
> — Jeremy, 2026-08-21. The port's learned outputs are shared workspace
> DATA (JSONL under `~/.maro/workspace/`), never Go constants. Both
> runtimes interoperate on one store. This is why byte-level divergence in
> anything written to that store is a real defect and not pedantry.

> "When we run multiple reviews, let's start doing the entire chunk +
> fixes, not just the latest round of changes… see if that helps the fixes
> be less granular to the change and more wholistic."
> — Jeremy, 2026-08-22. Every review round after the first reviews the
> WHOLE chunk including prior rounds' fixes. Basis for finding r6's MEDIUM
> and r7's MEDIUM, both of which lived in landed, reviewed, green code.

> "On same-model fallback, escalate reviewer tier after round 1
> (sonnet→opus / raise effort), don't grind many rounds at the cheapest
> tier."
> — Jeremy, 2026-08-22.

> "branch or no, a rebase + conflict resolution + FF merge is the answer
> here. no mechanism will save you from the conflict resolution work...
> you can mask it to pretend it doesn't exist, but it's going to be there
> one way or another."
> — Jeremy, 2026-08-16. Applies directly to this branch's eventual
> landing, and to the parallel build lanes opened 2026-08-26.

> "Derive must-detect mutations from the FILE not the diff."
> — Jeremy, 2026-08-16. A guard that cannot fail is worse than no guard.

> caps are circuit-breakers, not truncators
> — Jeremy, 2026-08-21 (paraphrase flagged as such; the verbatim decree is
> recorded in auto-memory `project_retrieval_graph_memory_direction`).
> Named here because the port inherits the rule wherever it transcribes a
> cap.

**Ops invariants that are not Jeremy quotes but are load-bearing** (kept
separate on purpose, so the quoted set stays clean):

- **Python stays production.** The Go port is a parallel spine. Nothing in
  this arc changes what runs.
- **`maro-orchestration/` is READ ONLY from any port worktree.** It is the
  specification and another session's tree.
- **Never write under `~/.maro/`** from a test, probe, or build agent.
  Live ledgers; overwritten once, 2026-08-16.
- **A build or test run owns the ENTIRE working tree** (P4, amended
  2026-08-26): a Go module builds through its import graph, so "a
  different package" is not an exemption. Parallel agents get separate
  worktrees, never a shared one. Only prose (`docs/`, `PORT.md`,
  `BACKLOG.md`, `review/`) is safe to edit while a battery runs.

---

## Compiled truth (system-maintained; basis noted per claim)

Measured **2026-08-26** unless stated. Every number here was computed on
that date, not recalled. `go/COVERAGE.md` carries the re-derivation
scripts so these can be recomputed rather than trusted.

| Claim | Value | Basis |
|---|---|---|
| Branch age | first port commit `9b684131`, **2026-08-21**; 206 commits | `git rev-list --count $(git merge-base go-port main)..go-port` |
| Go packages | 46 | `go/COVERAGE.md` |
| Production lines | 59,447 | ditto |
| Test lines | 81,834 (**1.38 : 1**) | ditto |
| Packages with a live CPython differential | **37 of 46** — 56,870 lines, **95.7% of production** | ditto; "live" = the test starts a real interpreter and compares, not a transcribed expectation |
| Python modules with **no Go reference anywhere** | **118 of 183** — 59,962 lines, **45.2%** | ditto. This is a **FLOOR**: "reference" is the loosest test (filename appears anywhere in the Go tree, comments included), so the true unported figure is worse and nothing yet measures how much worse |
| Review-ledger rows, this arc | **872** (836 at the 08-26 audit, +18 r8, +2 recall, +9 r9/sh6/census/CLI, +5 r10, +1 r10 census, +1 r11) | `review/findings.jsonl`, arc `go-port`; counted by `review-ledger.py report`, not added up — the first draft of this row said 862 by arithmetic and the ledger said 854 |
| Measured hallucination rate | **15 of 807 judged rows** retracted as `hallucinated` — **2%** | ditto. Well under the ~30–50% historical baseline; the delta is attributed to briefs that require a runnable repro. Still a floor: only retractions a reviewer VOLUNTEERS reach the ledger |
| Tranches needing 4+ review rounds | **33 of 66** targets | ditto. Deepest: `guard` 18, `handlequeue` 13, `evolver` 12, `dispatch` 12 |
| Both engines compared, READ path | **6 renderers, 6 identical, 0 differ, 0 refused** | `go/tools/engine-compare.py` over a copy of the live workspace, 2026-08-26 |
| Both engines compared, WRITE path | **7 `task` scenarios byte-identical; 6 `pack` scenarios DIFFER** — after fixing the divergence its first run found, and a second (the `--` separator hole) that r10 found in that fix | `go/tools/write-compare.py`, 2026-08-26. Trees byte-diffed including directory MODES; volatile fields elided by positive shape with per-side counts required to agree, and the harness self-tests its differ AND its normaliser (both directions) before comparing anything. The `pack` tranche is characterised and unfixed: manifest/report key ORDER (Python insertion order vs Go alphabetical), `origin.maro_version`, the MODE class (dirs 775/755, files 664/644, quarantine 664/**600**), Python's `import --dry-run` creating two `inbox/memory` dirs the Go does not, and export stdout prose |
| Can the port run a mission end to end? | **No** | `go/COVERAGE.md` |
| Interpreter every differential runs against | CPython **3.14.3** (bare `python3` is linuxbrew's; `/usr/bin/python3` is 3.12.3) | measured 2026-08-26 |

**Named genuine gaps** (as opposed to structural absences): ~~`internal/recall`
(542 lines, no interpreter comparison)~~ **CLOSED 2026-08-26** — three live
differentials landed from the lane2 build (`73cf930b`), then a 51-mutant
census derived from `recall.go` itself took it from 29/51 to **49/51**, the
two survivors being equivalent mutants named before the re-run;
`internal/provenance` (94 lines), `internal/missionrun` (no test file at
all). The other six packages without a differential have nothing to compare
against — LLM adapters, the probe harness itself, test scaffolding,
composition-only wiring.

**The strongest argument the port has, stated plainly:** the ledger records
divergences that are bugs or latent bugs in behaviour **both** runtimes now
share, found only because two implementations had to agree. That is the
"meaningful edges" half of Jeremy's 2026-08-22 question, and it has an
affirmative answer with 872 rows behind it. `docs/REVIEW_PATTERNS.md`
computes the recurrence counts.

**The fair complaint, stated equally plainly:** 45% of the Python has no Go
at all, and the tranches that exist keep costing four to seven review
rounds each.

---

## Decisions (system-maintained, append-only)

**2026-08-21 — the port is a parallel spine, not a replacement.** Python
stays production. Learned outputs are shared workspace DATA so both
runtimes read one store. *Reversal condition: none stated.*

**2026-08-22 — whole-chunk review, not diff review.** Rounds after the
first review the entire chunk plus all prior fixes. Jeremy's words in
Invariants. Basis for two MEDIUMs found in already-green code.

**2026-08-22 — escalate reviewer tier on same-model fallback.** Do not
grind rounds at the cheapest tier.

**2026-08-26 — `go/COVERAGE.md` created as the answer to "where are we".**
The question had been answered from memory for weeks. The file carries its
own re-derivation scripts specifically so the next answer is computed.
Queued to Jeremy in `docs/READING_QUEUE.md` **asking for no decision**.

**2026-08-26 — Codex upgraded to the $100/mo plan; the cross-family
reviewer seat reopens.** Jeremy: *"I've updated codex to the $100/mo plan.
Feel free to start running reviews there again."* The cap that had forced
same-family opus/sonnet fallback since ~08-19 is lifted. `codex exec` is
the default reviewer seat again per `docs/HOUSE_STYLE.md` step 6.

**2026-08-26 — parallel build lanes opened.** Jeremy: *"feel free to start
farming out chunks of work there as well and reviewing here if that makes
more sense. Let's see what we can do to speed up this port process a bit
-- I'll put my $$ where my mouth is on this one since you're in the
mines."* Codex is now both a reviewer seat and a builder.

**2026-08-26 — the working shape: plan → orchestrate → build → review.**
Jeremy: *"maybe we can take a different tact here.. worth spawning a fable
sub-agent to create a plan, then orchestrate here the pieces of that plan,
with sub-processes testing and bugfixing finished chunks as
appropriate?"* Adopted. Mechanics that make it safe, and which are not
optional:

- **One git worktree per build lane.** First one: `go-port-lane2` at
  `~/claude/maro-wt-goport-lane2`. Two agents cannot share a tree (P4).
- **Every brief repeats the hard boundaries verbatim** — never the other
  worktree, never `maro-orchestration/`, never under `~/.maro/`.
- **Build agents report divergences; they do not fix them.** A divergence
  found between the two runtimes is worth more than the tests that found
  it, and whether to close it is the host's call.

**2026-08-26 — this addendum exists.** Jeremy: *"Let's make a
goal-brain-addendum for this branch that we can then merge (if it comes to
that) with the port overall."* The parenthetical is load-bearing and is
recorded as-is: merging is conditional, and the file is written to survive
either outcome.

**2026-08-27 — `origin.maro_version` stays `"go-port"`, as an ASSERTED
divergence rather than an unexplained one.** The write-path harness reported
CPython writing `"0.8.0"` and the port writing `"go-port"` into every pack
manifest. Censused before deciding: `maro_version` is **written and never
read** — `grep -rn maro_version src/` returns exactly two hits, both inside
`pack.py`'s own `build_manifest`. Nothing gates on it, so matching the
string buys no compatibility. What it buys is a lie: a pack produced by the
Go would claim to have been produced by Python 0.8.0. The field's only
purpose is provenance, and the honest value is the one that says which
engine wrote the artifact.

So it stays, and it moves into `write-compare.py`'s known-divergence
allowlist with its reason. That allowlist carries a **must-still-be-observed**
rule: a row that stops matching anything fails the run. So the decision is
not "ignore this difference", it is "assert this difference continues to
exist" — if the two engines ever agree on that field, something changed
that nobody decided.

---

## Threads (system-maintained — nothing leaves this list silently)

| Thread | State |
|---|---|
| `internal/artifactcheck` review to fixpoint | **r11 landed** — ONE finding, MEDIUM, and it lived inside r10's own fix (P6 twice running: the four task verbs shared one FlagSet where argparse gives them four subparsers, so `complete --error` was accepted). `LOWS ONLY: no`, so **r12 is the open gate** — but the round returned a single finding over the whole chunk, which is what convergence looks like |
| `internal/syshealth` review to fixpoint | **AT FIXPOINT.** r6 returned `LOWS ONLY: yes` with one doc LOW (r5's factual correction had not reached the harness copy — L13) and one self-retraction. Fix landed |
| `internal/recall` CPython differential | **CLOSED.** Landed `73cf930b`; the owed deeper mutation pass then ran — 51 mutants derived from `recall.go` itself, 29/51 → **49/51** after `census_test.go`, the 2 survivors equivalent mutants named before the re-run. One divergence stands reported and NOT fixed: `goal_achieved: "false"` (a string) → CPython `None` (unjudged), Go `false` (judged not achieved), fail-closed in Go |
| `internal/provenance` CPython differential | **IN FLIGHT** — dispatched to a worktree-isolated subagent 2026-08-27 |
| `internal/missionrun` has no test file at all | open, unscheduled |
| Write-path comparison harness | **BUILT and paying.** `go/tools/write-compare.py`; found a silently dropped CLI argument on its first run, then all 7 `task` scenarios byte-identical. Extended to `pack` (the interop format): 6 scenarios, all differ, tranche characterised in Compiled truth and unfixed. r10 found the harness itself blind to directory symlinks; fixed, and the differ's self-test now asserts 5 findings instead of 3. Next targets: the `pack` tranche's own review cycle, then a third write surface (`orch_items` / `record`) |
| Remaining first-pass tranches, sequenced | **planner returned** → `scratchpad/PORT_PLAN.md`. First pass = 74 modules / 37,777 py lines, ~29 review units, **~120–170 review rounds**. Recommends Option B (comparable core: 62 modules, 25,972 lines, ~90–120 rounds) stopping at a mission dry-run comparison. Unacted |
| Directory-mode fix: 33 `0o755` + 7 inline `0o777` → `record.NewDirMode` | filed in BACKLOG |
| `check_system_health` | blocked on `llm.DetectBackends` plus a python3 shell-out seam |
| `artifact_check.py` slice 2 (~250 lines, :483–736) | design captured, unbuilt |
| inspector/evolver guard slice-1 review | **PAUSED at r14**, r15 gated on Jeremy |
| Stale comment `internal/pack/export.go:385` | trivial, open |
| `pack` write-path divergence tranche | **key-order half IN FLIGHT** (worktree-isolated subagent, 2026-08-27); the other four named differences still open. **Characterised.** 5 named differences (key order, `maro_version`, modes, dry-run mkdir, stdout prose). Needs BACKLOG entries and its own review cycle; the key-order one is a `pyval.Obj`-vs-`map[string]any` fix, which is the port's oldest recurring family |
| argv surface: abbreviation, single-dash long options, exit code 2 vs 1 | **FILED, not fixed** (BACKLOG, with the measured table). None changes what is WRITTEN for an argv both engines accept |

---

## Open questions (system-maintained)

**1. Where is the boundary?** Does the port stop at the deterministic core
— with Python keeping the I/O and LLM-mediated half — or continue to a
runnable second engine? *Raised 2026-08-26 with a recommendation for the
boundary. No answer received; Jeremy instead funded parallel capacity,
which is evidence about direction but is not an answer to this question.*
Blocks: how much of the remaining 45% is in scope at all. The fable
planner has been asked to argue its own cut line, which will sharpen the
question but not settle it.

**2. What does "first pass completely implemented" mean, in modules?** The
standing goal says every tranche review-fixed, but 118 unported modules
include things with nothing to port against (LLM adapters) and things that
are dev tooling. Until that set is classified, "completely" has no
denominator. *Assigned to the planner.*

**3. Does the write path agree?** **Answered for `task`, 2026-08-26: yes,
now.** Seven scenarios covering the whole state machine come out
byte-identical across both engines, directory modes included — but only
after the harness's first run found Go's `flag` silently dropping
`--error "boom"` when it followed the job id. So the honest form of the
answer is: the write path agrees where it has been MEASURED, it did not
before it was measured, and six of the eight-plus other writing surfaces
(`orch_items`, `record`, the memory ledger, the captain's log…) remain
unmeasured.

---

## Journal (system-maintained, chronological)

**2026-08-21** — Port opened. v0 spine.

**2026-08-22** — v0 spine through DIRECTOR, r1–r9.

**2026-08-23** — Self-improvement slice 2 to fixpoint r1–r5, 0%
hallucination across all five rounds.

**2026-08-26** — Metrics and introspect tranches landed with batteries.
Both engines compared on the READ path for the first time: 6 renderers, 6
identical. `go/COVERAGE.md` written, and with it the first honest,
computed answer to "where are we": deep where it is deep, roughly half the
surface, cannot run a mission. Review ledger audited and found
**under-counting by 38 rows** — syshealth r4/r5 and artifactcheck r1–r4
had never been recorded — and backfilled; both structural causes filed.
`internal/artifactcheck` r6 and r7 landed. r7's MEDIUM: r6's own fixtures
defended a seam configuration no production caller uses. A sixth finding
came from running a widened guard *before* fixing anything and reading its
whole census rather than the one site the review named — the standing rule
that came out of it is **the findings a review reports are a sample; the
guard's own census is the population**. Jeremy funded parallel capacity;
lanes opened; this file created.

**2026-08-27** — `internal/artifactcheck` **r8** to a landed round, run as
a **two-seat panel** (opus same-family + codex `gpt-5.6-terra`
cross-family, neither prompted with the other's findings). They converged
on one class and each found sites the other missed. The class: **`os.ReadDir`
is a sort** — the exact twin of r7's `filepath.Glob` blind spot, one
spelling over, and the most common way this tree lists a directory. Four
production fixes (`closure.projectFileInventory`, `orch.ListMissions`,
`runs.scanLegacyRunDirs`, `orch.ListBlockedProjects`), each with a CPython
differential proved able to fail by reverting its own fix. Two of the four
are **W24**, not W23: the inventory truncates at its cap so the engines
*name different files*, and the legacy scan's first-hit-wins decides which
run a duplicate-reference migration resolves to.

The seats **disagreed on one finding** (`recall.FindPriorAttempts`) and the
Python settled it: `recall.py:407-410` sorts a generator over an *unsorted*
`iterdir()`, so there is no CPython order to reproduce. Filed as an
allowlist row, not fixed. Two seats disagreeing is the cheapest signal that
a site needs the source read rather than the diff.

The census discipline paid a second time: the new `readDirOrderAllowlist`
arm was run **before any fix**, named exactly the three real sites out of
the five the seats between them proposed, and then the guard's own
must-still-be-observed rule caught a `dirSortAllowlist` row that the fixes
had just made stale. Two catalog entries minted: **P15** (a widened guard's
own census is the population) and **L55** (a fixture can defend the
configuration that does not ship — named now, filed against r7 where it was
measured).

Also corrected: an allowlist decision rule that was **false** (`json.dumps`
with `ensure_ascii=True` escapes a lone surrogate to pure ASCII, so a key
*can* arrive inside JSON non-UTF-8; seven allowlist rows had been admitted
by that reason and are safe only because Go's `encoding/json` substitutes
U+FFFD), two comment counts wrong at birth (25 bases not seventeen; 420×
not 2000×), and a 97,116-input ISO corpus that could not observe the
leap-Wednesday disjunct — `isLeap` was pinned as a rejecter and not as an
accepter until `2020-W53-1` was added.

**2026-08-26 (later)** — Two codex seats in parallel, then the write path
gets an instrument.

`internal/syshealth` reached **fixpoint** (r6, `LOWS ONLY: yes`). Its one
LOW is the same factual error r5 had already corrected in the package doc,
still standing in the harness comment — L13 measured again, in the plainest
possible form: a fix at the site that has the fixture is not a fix for the
class.

`internal/artifactcheck` r9 returned a real MEDIUM in `closure`:
`os.DirEntry.IsDir()` reads the entry's own type bits, while CPython's
`os.walk` asks `scandir`'s `is_dir()`, which **follows** the link. A
symlinked directory therefore lands in `dirnames` and, with
`followlinks=False`, is named nowhere at all — while the port emitted it as
a file, where at the inventory cap it could displace a real path. Fail-open.
Verified against a live CPython probe before touching anything, and the
descend arm was deliberately left asking the *other* question, with the
asymmetry commented at both sites. r9's LOW also **overturned a rationale
this file's own prose had recorded as deliberate** three sections earlier:
"the fixture correctly failed rather than skipping" was half right — the
pyprobe rule exists so a missing INTERPRETER is never skipped, and says
nothing about a missing kernel capability, which is not a port fact. L52.

The owed deeper mutation pass on `internal/recall` ran: 51 mutants derived
from `recall.go` itself (L9, not from the diff), **29/51 → 49/51**, and the
two survivors are the two equivalent mutants `census_test.go` names in its
doc block *before* the re-run.

**The instrument, and what it cost to believe it.** `write-compare.py` runs
both engines through the same command sequence and byte-diffs the trees,
directory MODES included. It self-tests twice — the differ must find
exactly 3 planted differences and 0 false positives, and the *normaliser*
is checked in both directions over nine shapes, because a too-greedy
elision still prints "identical" (L51). On its first run it found Go's
`flag` silently dropping `--error "boom"` when written after the job id,
where argparse interleaves: two runtimes writing different rows to a store
they share, invisible until something reads the row back. Fixed, then
**censused** rather than patched at the site (P15) — `pack adopt` had the
same shape, `run` and `director` already refuse loudly and were left alone.

With that in, all seven `task` scenarios come out byte-identical. That
answers open question 3 for one surface and makes the honest form of the
answer visible: the write path agrees where it has been measured, it did
not before it was measured, and most writing surfaces are still unmeasured.
The directory-mode thread — 33 `0o755` sites against Python's
`0o777 & ~umask` — now has something that can actually observe it.

### 2026-08-26 (late) — r10: the same spelling, wrong in both directions

Five findings, two seats, all five confirmed by measurement. Four are one
class, and the class is the sharpest thing this arc has found about what a
behavioural port actually is.

`Path.is_dir()` FOLLOWS a symlink. `os.DirEntry.IsDir()` reads the entry's
own type bits and never does. r9 had just fixed `closure`, where the port's
`IsDir()` **invented** a file CPython never names, because `os.walk`'s
default `followlinks=False` puts the link in `dirnames` and emits it
nowhere. r10 found the identical transcription at four `iterdir()` sites,
where the same spelling **drops** an entry CPython lists. One Python
question, one Go spelling, and the error runs in opposite directions
depending on which library call is being ported. `pypath.EntryIsDir` answers
the question; the CALLER still has to know which call it is reproducing,
and `closure`'s descend arm deliberately keeps `!it.IsDir()` for exactly
that reason.

Two of the four are worse than an ordering difference. `runs.py:200` feeds
`_legacy_run_dir`, which takes the FIRST match — so the two runtimes
resolved the same duplicate loop reference to **different runs**. And
`recall.py:407-410` uses the Lstat-shaped *pair* (`IsDir()` + `Info()`),
which is two independent failures behind one spelling: a dropped row, and
an order taken from the LINK's mtime rather than the run's. The fixture is
built so those two orders are exact reverses, and both mutants were run.

**P6 again, and it stings.** The round's medium lived inside r1's own fix.
`parseInterleaved` restored flag interleaving and never modelled positional
ARITY — and `flag.Parse` consumes `--` and stops, so the tail was re-parsed
as flags. `task fail <id> -- --error boom` exits 2 on CPython and leaves the
task queued; the port marked it FAILED and dropped the error. A different
store row, from the helper written last round to stop a different store
row. Fixed by splitting at `--` before parsing and taking each subcommand's
declared count from `task_store.py`. Three remaining argv differences are
FILED with their measured table, on the stated line that none of them
changes what is written for an argv both engines accept.

**The fifth was in the instrument**, which is the one that should worry us
most: `snapshot()` recorded a directory symlink as an ordinary directory, so
two trees with different link targets compared EQUAL. A harness blind spot
decides whether the port has one. The self-test now plants both symlink
shapes and asserts 5 differences; reverting the fix drops it to 4, with the
retarget vanishing and the kind change reported as a MODE difference — the
wrong sentence about the right tree.

**And the harness reached `pack`**, the interop format, where it
immediately produced a tranche: manifest and report **key order** (Python
insertion order vs Go alphabetical — the port's oldest recurring family,
`pyval.Obj` vs `map[string]any`), `maro_version`, four distinct mode
classes, a `--dry-run` that creates two directories on one engine and none
on the other, and stdout prose. Two of the divergences it re-derived were
already documented as deliberate, which is the strongest available evidence
that the instrument works: it independently found what someone had already
reasoned about.

Seven `task` scenarios still byte-identical. Six `pack` scenarios differ,
and that is now a thread with numbers rather than a suspicion.

**A postscript that is really the round's best lesson.** Censusing the argv
class again turned up four more sites — and the reason r1's census missed
them is that it enumerated a code PATTERN (`fs.Arg(0)` after a bare
`fs.Parse`) rather than the PROPERTY that defines the defect (*does a stray
positional change what this command does?*). `pack export`, `pack seal`,
`pack import` and `metrics` read positionals nowhere at all, so they looked
nothing like the site that was found, while behaving exactly like it:
measured against the pre-fix binary, `pack export -name p -label l stray
-include-medium` exits 0 and ships a pack without medium lessons. An
enumeration can be wrong at BIRTH, which is L28 pointed at the census
itself.
