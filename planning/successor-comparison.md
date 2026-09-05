# Two-engine comparison — protocol and ledger (post-v1 item 3)

Phase 4 of `successor-plan.md`, first pass: the same goals on both
engines, same model, compared at the artifact boundary. This is the
"roughly the same level?" check that gates the features-on-both-sides
question, not the features question itself.

## What is held equal

- **Model.** `haiku` through the claude CLI (`subprocess` backend) on
  both engines. The Python box config already orders `subprocess`
  first; the Go engine takes `--backend subprocess --model haiku`.
- **Workspace.** Fresh scratch on both sides, never the live
  `~/.maro/workspace`: Python `MARO_WORKSPACE=$SP/pyws` (resolved path
  asserted before every run), Go `MARO_GO_WORKSPACE=$SP/c3`.
- **Lane.** Forced, not classified: `--lane now` / `mg now`,
  `--lane agenda` / `mg agenda`. Lane classification is a real
  difference between the engines but it is not the question here.
- **Serialization.** One run at a time on the box (workspace lease on
  the Go side; box limits on both).

## What is measured (the ledger columns)

| column | Python source | Go source |
|---|---|---|
| calls | `memory/step-costs.jsonl` rows for the run + the NOW call | `receipt/1` records for the run |
| cost_usd | provider-reported cost on those rows / run card | sum of `receipt.usage.cost_usd` |
| wall_s | `/usr/bin/time` around the CLI | same |
| deliverable | `HandleResult.result` (json) + run dir | delivery payload (`runs show`) |
| fixture | checked by hand-written script, never by a model | same |
| notes | honesty, refusals, tool use, anything the fixture cannot see | same |

Cost is what the CLI reports; on this box the CLI is subscription
billed, so the number is a relative gauge, not a bill.

## The goal set (fixtures checkable by a script — pattern 84)

| id | lane | goal | fixture |
|---|---|---|---|
| G1 | now | Answer with one word only: what is the SI unit of electrical resistance? | deliverable contains `ohm` (case-insensitive) |
| G2 | now | A meeting starts at 09:40 and lasts 2 hours 35 minutes. Reply with the end time only, 24-hour HH:MM. | contains `12:15` |
| G3 | now | Fetch https://go.dev/VERSION?m=text and reply with just the Go version string on its first line. | contains the string the URL serves at run time (fetched by the checker) |
| G4 | agenda | Create a file named marker.txt in the current working directory whose entire content is the single line: successor-comparison-2026-09-05 | a `marker.txt` under the engine's work tree whose content is exactly that line |
| G5 | agenda | Write fizz.py that prints FizzBuzz for 1 through 15, one value per line, run it, and report line 15 of its output verbatim. | deliverable contains `FizzBuzz`; a `fizz.py` under the work tree whose output line 15 is `FizzBuzz` when the checker runs it |
| G6 | now | What was the closing price of AAPL on the most recent trading day? If you cannot verify it, reply with exactly: cannot verify | contains `cannot verify` (both engines run this one with web denied) |

Known asymmetries, named up front so they are read as findings, not
as noise: the Python NOW lane is a single call with NO tools by design
(`--tools ""`) and pre-fetches URLs in the goal instead; the Go NOW
lane executes with tools under the operator policy (G3 runs with
`--deny-tools ""`, G6 with the default policy). Python AGENDA runs
inside a per-run fence directory; Go AGENDA runs in `<workspace>/work`.

## Procedure

1. Python: `cd ~/claude/maro-orchestration && MARO_WORKSPACE=$SP/pyws PYTHONPATH=src /usr/bin/time -f 'wall %e' python3 -m handle --lane <lane> --format json "<goal>"`.
2. Go: `MARO_GO_WORKSPACE=$SP/c3 /usr/bin/time -f 'wall %e' $SP/mg <lane> --backend subprocess --model haiku [--deny-tools ""] "<goal>"`.
3. Check the fixture with the script, fill the ledger row, note anything the fixture cannot see.
4. Both engines run every goal once. One run each: this is a level check, not a statistic. A goal that fails on one engine gets ONE retry on that engine, recorded as such.

## Ledger (run 2026-09-05, haiku on both, one run per cell)

| goal | lane | engine | calls | cost_usd | wall_s | fixture | notes |
|---|---|---|---|---|---|---|---|
| G1 ohm | now | Python | 1 | 0.0050 | 11.0 | PASS | `Ohm` |
| G1 ohm | now | Go | 2 | 0.0333 | 33.6 | PASS | `Ohm`; execute + tail diagnose |
| G2 12:15 | now | Python | 1 | 0.0051 | 5.9 | PASS | |
| G2 12:15 | now | Go | 2 | 0.0205 | 22.9 | PASS | |
| G3 go.dev version | now | Python | 1 | 0.0069 | 7.6 | PASS | URL pre-fetched into the one call, no tools |
| G3 go.dev version | now | Go | 2 | 0.0251 | 22.5 | PASS | `--deny-tools ""`, WebFetch as a tool effect on the record |
| G6 cannot verify | now | Python | 1 | 0.0051 | 5.9 | PASS | no tools by design |
| G6 cannot verify | now | Go | 3 | 0.0432 | 49.6 | PASS | default policy denies web; third receipt is the tail's propose on the diagnosis |
| G4 marker.txt | agenda | Python | 22 | 0.8023 | 211.8 | PASS | 6 step-executes (recon step first: "does marker.txt exist?"), 3 decompose candidates, cuts, scope, closure plan/verdict, quality gate, claim review, 3 extractions; file in the project fence AND copied to the run artifact |
| G4 marker.txt | agenda | Go | 6 | 0.0389 | 38.6 | PASS | intent, plan (1 step), execute, 2 judges, diagnose; closure achieved |
| G5 fizz.py | agenda | Python | 17 | 0.5541 | 157.5 | PASS | 4 step-executes incl. an independent verification step (cat -A, grep -c, sed -n 15p); same overhead classes as G4 |
| G5 fizz.py | agenda | Go | 10 | 0.0938 | 88.3 | PASS | intent, plan (3 steps), 3 execute+judge pairs, closure judge, diagnose |

Totals: Python 6/6 at $1.38 and 400 s; Go 6/6 at $0.25 and 256 s.

## Reading

**Level.** Both engines deliver every goal in the set, including the
honesty goal and the two agenda goals that touch the filesystem. At
this scale there is nothing to separate them on the deliverable. The
gate for the features-on-both-sides question is passed.

**Where the cost goes (the real difference).** Per NOW goal the Python
engine is one call at ~$0.005; the Go engine is two to three at
$0.02–0.04, because the Go execute runs with the CLI's tool set in the
system prompt (the Python NOW lane runs `--tools ""`, ~21k cached
input tokens either way) and the tail diagnoses every run in-process.
Per AGENDA goal the ratio inverts hard: Python spends 17–22 calls and
$0.55–0.80 on a one-file goal, Go 6–10 calls and $0.04–0.09. The
Python overhead is not waste in the pejorative sense — each class
(cuts, scope, three decompose candidates, plan review, closure plan
and verdict, quality gate, adversarial claim review, three extractions)
was added to close a measured failure — but on a goal this small every
one of them is a fixed cost with nothing to catch. The Go engine's
overhead is its shape: one intent, one plan, one judge per step, one
closure judge, one diagnosis. Python's is a ladder of mechanisms; the
successor's is the process.

**Calls are the currency, not dollars.** Wall tracks call count on
both engines (roughly 10–20 s per haiku call through the CLI). The
Python agenda run's 3.5 minutes on a one-line file is the number an
operator feels.

**What the fixture cannot see.** Python's recon-first plan on G4
("check whether marker.txt exists before writing — decides whether the
write silently destroys unrelated data") and its verification step on
G5 are the accumulated judgement of ~80 sessions of failure patterns.
The Go engine planned the direct path and its judges accepted it. On
these goals the direct path was right. Which posture is right is a
property of the goal, and neither engine chooses it by goal size yet;
that is a feature question, not a comparison finding.

**Asymmetries to carry into the features phase.** (1) Python NOW has
no tools and pre-fetches URLs; Go NOW has tools under policy — the Go
engine's G3 leaves a `tool_effect` on the record, Python's leaves the
fetched text in the prompt. (2) Go closure is `unknown` on every NOW
run by design (no judge on the NOW lane) and `achieved` on agenda;
Python renders a closure verdict on agenda only. (3) Python's tail
(lesson, knowledge, skill extraction) ran after the run inside the
same process and counted 3 calls; Go's tail ran one diagnose (and one
propose on G6) — the two tails are not the same thing yet, and the
successor's learning loop was only exercised in the acceptance run.

**One-run caveat.** Every cell is n=1. A level check, not a
statistic: a second pass would be worth it only if a cell flipped.

## What comes next (Phase 4, second half)

Add the same small feature on both engines and keep a per-feature
ledger: lines touched, tests added, review findings, wall time to
land, and — the question Jeremy asked — which one was cheaper to
change safely. Candidates, on the seams the prototype never closed:
fork-with-join, scoped memory, removal-as-deliverable.
