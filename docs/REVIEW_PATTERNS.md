# Review patterns — the lens catalog

*Started 2026-08-26 (Jeremy: "we should be identifying and writing down
patterns for our review cycles; so we can address them and tighten those
loops").*

**What this is.** Every adversarial review round produces two things: a
list of findings, and — more valuable and until now entirely undurable — a
*pattern* that explains why the defect was invisible. The findings get
fixed and forgotten. The patterns recur across unrelated subsystems, and
they are what actually tightens the loop: a named lens turns a defect class
into something a reviewer can look for on purpose instead of stumbling
onto.

Before this file, the catalog lived in conversation context and was
re-derived from scratch after every compaction. That is the bug this file
fixes.

**How to use it.**

- *Reviewing:* walk the lenses against the chunk before writing findings.
  They are ordered by how often they have actually fired, not by severity.
- *After a round:* if a finding did not fit an existing lens, add one.
  A lens with one instance is still a lens; a lens is retired only when
  the shape it names becomes structurally impossible.
- *Recording:* each round's findings go into `review/findings.jsonl` via
  `scripts/review-ledger.py` (see "The ledger" below) so the recurrence
  counts here can be re-derived from data rather than from memory.

**Status legend on each lens:** `instances` is how many independent
findings have been attributed to it in `review/findings.jsonl`. The
2026-08-26 backfill seeded 573 rows — 562 mined out of `go/PORT.md`'s
review record plus 11 recorded live — and live recording has taken it to
**596**. The counts below were regenerated from `report --by lens`, not
recalled.

**Two things the counts are not.** They are lower bounds: 309 of the 596
rows carry no lens, because `PORT.md` names a review ROLE ("Skeptic",
"QA") far more often than it names a shape. And the backfill is
*survivorship-biased by construction* — `PORT.md` records findings that
were acted on, so its 1% hallucination rate is an artifact of what got
written down, not a measurement. P1's ~30–50% stands as the remembered
figure until live recording produces a real denominator. That bias is
itself L1: a number that reports agreement because the thing that would
have disagreed was never in the sample.

---

## A. Tests that agree instead of measuring

The largest family by a wide margin. Every one of these produces a GREEN
test suite that is evidence of nothing.

### L1 — A test reporting AGREEMENT may be testing nothing
*instances: 50 — the most frequent single defect in the Go port*

A differential that passes because both sides were skipped, both returned
empty, or the assertion could not fail.

**Canonical instance.** `internal/introspect`'s whole differential passed
in 0.011s on its first run. The `pyprobe.Marker` was `"src/introspect.py"`
where the helper wanted `"introspect.py"`, so every probe took the honest
"python source tree unavailable" SKIP and the suite reported `ok`.

**Tripwire.** Look at the wall-clock. A suite that shells out to CPython
30 times cannot finish in 11ms. Assert non-emptiness of anything the
comparison is derived from (`if len(keys) == 0 { t.Fatal(...) }`).

### L2 — An enumeration is not a class
*instances: 1*

Sweeping the cases a fixture happens to name proves nothing about the ones
it does not.

**Canonical instance.** `metrics.COST_BY_MODEL` is three naming systems for
the same models (versioned IDs, adapter short forms, tier constants). A key
mistyped in the port does not fail — it falls through to the Sonnet default
and prices an Opus step at a fifth. The sweep now enumerates the **CPython**
table, so a key the port never heard of shows up as a mismatch instead of
never being tested.

**Tripwire.** Derive the sweep from the OTHER runtime, or from the source of
truth — never from the list the code under test consults.

### L3 — A fixture that derives its input from the list the code consults cannot disagree with it
*instances: 0 recorded*

The self-referential form of L2.

**Canonical instance.** `TestLoadOutcomesMatchesCPython` spells the six
required `Outcome` fields out as a literal rather than calling
`outcomeRequiredFields` — because whether that list is right is the test's
whole subject.

**Zero is the honest count, and it is informative.** This lens was written
from a decision made while WRITING a test, not from a finding a reviewer
raised — so nothing in `PORT.md` records it. Kept as a lens because the
shape is real and the tripwire is cheap; treat it as a self-review prompt
rather than as a measured recurrence.

### L4 — A guard that cannot fire is not evidence the danger is gone
*instances: 7*

**Canonical instance.** Deleting the `is_error` check from
`classify_tool_pathologies`' hallucination scan survived a 37-mutant
battery, because every fixture carrying the phrase `No such tool available`
also carried `is_error: true`.

**Tripwire.** For every gate, ask what fixture makes it *matter*. If none
exists, the gate is decoration.

### L5 — A duplicated guard is what makes a wrong bound unobservable
*instances: 4*

Two checks for the same thing; break one and nothing fails.

**Canonical instance (test-side).** The introspect differential normalized
nil-vs-empty away with `if len(want) == 0 && len(got) == 0 { return }`,
carrying a comment defending it. `json.dumps([])` is `"[]"` and a nil Go
slice marshals to `null`, and that list gets written to a shared store. The
normalization was the only reason a "return nil for a clean transcript"
mutant survived.

### L6 — A test that shares a fixture with the thing it measures against is measuring the fixture
*instances: 1*

### L7 — A detector that cannot see the case you already have is agreeing, not measuring
*instances: 1*

### L8 — A mutant that cannot change an answer is a bad mutant, not a test gap
*instances: 18*

The battery's own failure mode, and it costs real time to misread.

**Canonical instance.** Aliasing `worstNames` to the live streak slice is
*unkillable* in Go, because the reset branch is `streakNames = nil` — every
later streak appends into a fresh backing array, so the alias can never be
written through. The plausible port is the PAIR (reset by truncation AND
alias) and only the pair is observable. Separately: three mutants in the
metrics battery left an identifier unused and reported BUILDFAIL, which is
a fault in the battery, not a survivor.

**Tripwire.** Before recording a MISS, prove the mutant changes behaviour on
*some* input. If it cannot, fix the mutant.

### L9 — Derive must-detect mutations from the FILE, not the diff
*instances: standing (Jeremy, 2026-08-16)*

A guard derived from what changed cannot catch what was always wrong.

### L10 — A test helper is code, and a guard it repeats is a guard nothing pins
*instances: 2*

### L11 — A deadlocked test is worse than a failing one
*instances: 2*

**Canonical instance.** `zz_adv_mint_test.go` (30 cases × 2 CPython
subprocesses) blew the 10-minute package timeout and took the whole
`go test ./...` red with a goroutine dump instead of a diff.

---

## B. Half a reader / half a writer

Porting or refactoring one layer of a two-layer operation. The highest-value
family: every instance so far was a real behavioural divergence in
production, not a test problem.

### L12 — Half a reader
*instances: 6*

**Canonical instance.** Python's `_rows_as` is TWO readers stacked: an
announced framing read AND a dataclass construction that EXCLUDES rows and
counts them as schema drift. The Go port had only the framing half at both
`load_outcomes` and `load_suggestions`. Consequence: the evolver minted a
cycle off three rows CPython excludes, and `get_suggestion` handed the
auto-revert guard `applied_manually=false` for a row a human had applied.

**Tripwire.** When porting a function that returns typed objects, ask what
happens to a row the constructor rejects. Silence is the wrong answer.

### L13 — A fix at the site that has the fixture is not a fix for the class
*instances: 5*

**Canonical instance.** `dailylog.go` already carried the outcome schema
filter, measured and correct, with a comment quoting CPython's own warning —
applied one layer too low, with the reasoning "LoadOutcomes' tolerance is
right for its other consumers" written down. That reasoning was wrong, and
the comment recording it had to be corrected in place.

### L14 — A helper you did not look for is a helper you will write again
*instances: 17*

**Canonical instance.** `pyval.Clip` is the shared Python-semantics rune
slicer. Six packages carry a private `clipRunes` copy (`scans`,
`graduation`, `playbook`, `evolver`, `skills/utility`, `director`). The
introspect port deliberately did not become the seventh.

### L15 — A helper that fixes a class does not fix the class — it fixes the callers that reach it
*instances: 5*

### L16 — A field is TWO claims (the writer's and the reader's)
*instances: 5*

### L36 — A hardening is a fork
*instances: 3 attributed; ~4 in the mined cluster*

Tightening one reader — a strictness check, a truthiness unification, an
allowlist — silently forks it from the other readers of the same data. The
fix looks local and the divergence is not.

**Canonical instance.** `recall.go` was found to be *a third* unhardened
`goal_achieved` reader (r3, medium) after two earlier rounds had hardened
the first two. Related shape at `internal/graduation` r1 (high): CPython
shells out whatever `verify_pattern` string `suggestions.jsonl` carries and
the port forces the compiled-in pattern — a hardening that is a real
behavioural fork, now carried as a named divergence rather than as parity.

**Tripwire.** After hardening a read, grep for every other site that reads
the same field. The hardening is not done until they agree or the
disagreement is written down as a divergence.

### L37 — Two runtimes share a store and nothing tests the crossing
*instances: 2 attributed; ~5 in the mined cluster*

The port and CPython write into the same JSONL. Each side's tests are
self-consistent; the boundary is what nobody exercises.

**Canonical instance.** `internal/skills` r2 (high): minting a fresh zeroed
stats record over a stranded id put the reset row LAST, where it won the
last-row-wins keyed read *in both runtimes* and zeroed live stats.
`internal/tasks` (high): CPython reads a task file with subscripts, so
every missing key raises — the raise IS the contract — while the port's
`.get`-shaped reads synthesise a default and carry on.

**Tripwire.** For any file both runtimes touch, ask what the OTHER runtime
does with a row this one just wrote. A test that only round-trips through
its own reader cannot see it.

---

## C. Types and values arriving differently than they left

### L17 — A fixture built from LITERALS arrives with types the reader never produces
*instances: 2*

**Canonical instance.** `TestContentKeyCoercesLikePython` passed Go-native
`int 0` / `float64 1.5` where the disk reader yields `json.Number`.
Fixtures for a reader must be LINES, not literals.

### L18 — A value arrives with a type, and something reads the type away
*instances: 12*

**Canonical instance.** Switching to an announced ordered read made numbers
`json.Number`, so `intOf`'s `float64` arm stopped matching and a human
surface rendered "Total tokens: 0".

### L19 — A zero value that must mean two things means neither
*instances: 20*

**Canonical instance.** `load_outcomes(limit=0)` in Python returns NOTHING
(`[:0]`). The port reads `limit <= 0` as "everything" — a deliberate
divergence, now pinned by a named-divergence test rather than left implicit.

### L20 — Python's operators are not Go's
*instances: 13*

Truthiness vs `== true`; identity deciding a dict lookup; `str()` vs
`repr()` agreeing on `None` and disagreeing on everything else; `%` on
negatives.

**Canonical instance.** `te.get("is_error")` is TRUTHINESS. A stamp of the
string `"no"` is truthy in Python; a port reading it through `v.(bool)`
calls the transcript clean.

### L21 — A generic instantiated at `any` is a different constraint
*instances: 1*

### L22 — An exception's class is part of its behaviour
*instances: 3*

---

## D. Boundaries and limits

### L23 — A limit with no case at its OWN boundary is a limit nothing pins
*instances: 8*

A limit's NEIGHBOURS are not its boundary.

**Canonical instance.** `estimate_cost` clamps with
`max(0, min(cache_read_tokens, tokens_in))`. The two clamps COMMUTE for
every `tokens_in >= 0` — so swapping them is invisible to any fixture built
from plausible inputs. They part only on a negative input count, which
nothing sane writes. The fixture pins the ORDER; it is not a defence.

**Tripwire.** For every clamp/limit, find an input where the two candidate
spellings disagree — even if that input is outside the realistic domain.
If none exists, say so in the comment instead of implying coverage.

### L24 — A measurement is only evidence about the call you actually made
*instances: 1*

### L25 — A skip by name is a claim you never re-examine
*instances: 2*

### L33 — The limit is right and the ORIGIN is wrong
*instances: 3 attributed; ~7 in the mined cluster*

Distinct from L23. A budget spent from the front starves whatever the
caller cared most about, and every boundary case still passes because the
*size* of the window is correct — it is anchored in the wrong place, or
shared with something that eats it first.

**Canonical instance.** `internal/loop` r1 (medium): the `MISSING_INPUT`
inner clip used the 600-char *chain* budget instead of Python's
`clip(reason, 1000)`, so the do-not-fabricate instruction fell off the end
on exactly the long-reason runs that needed it. Same shape at
`internal/director` r1: the report-echo check judged an unclipped window
the compiler never saw.

**Tripwire.** For every budget, name what is at the START of the window and
what is at the END, and ask which one the caller would rather lose. If the
answer is "the end" and the important thing is at the end, the origin is
wrong.

---

## E. Prose, names and identity

### L26 — Content-key PROSE divergence
*instances: 21*

Byte-diff the emitted STRINGS, not the logic. Two runtimes describing the
same event differently reads as two different problems, and where prose
feeds a content key it changes dedup identity.

**Canonical instance.** `FAILURE_CLASSES`' descriptions are not
documentation: `diagnose_loop` assigns
`recommendation = FAILURE_CLASSES.get(failure_class, "")` — the prose IS a
field on a persisted row.

### L27 — A name is a key the moment anything reconstructs it
*instances: 3*

### L28 — A comment that ASSERTS COVERAGE is a claim, and it decays
*instances: 32*

**Canonical instance.** `matchesLookUp`'s doc comment still said "the words
list above carries the two common spellings" after those spellings were
removed from the list.

### L29 — An idiom is not a defect — the defect is a spelling that does not match the spelling at ITS OWN site
*instances: 2*

### L30 — A fixture travels through a channel, and the channel has opinions about what it carries
*instances: 2*

**Canonical instance.** `pyprobe.RunJSON` has no `UseNumber`, so a known-gap
pin comparing re-decoded numbers saw `1.0` come back as `1`. The fix was to
emit the compared fields as a Python-side JSON *string*.

### L31 — Sometimes the answer to a survivor is DELETING production code
*instances: 2*

A distinction nothing reads is a second guard making the first unobservable.

### L34 — A clip marker is not the end of the string
*instances: 3 attributed; ~5 in the mined cluster*

Something appended AFTER a truncation is eaten by it, or the marker itself
is miscounted — so the surface silently loses exactly the part that was
added last, which is usually the part that was added because it mattered.

**Canonical instance.** `internal/loop` r2 (high): the chain entry's
verdict tag was appended *before* the entry's single 600-char clip, so tag
and instruction were both eaten on long-reason runs. `internal/now` r2
(medium): the judge window's cut was byte-based, splitting a UTF-8 rune and
miscounting the marker.

**Tripwire.** Order the operations explicitly: clip, THEN append the marker
and anything that must survive. And count the marker in the budget.

### L35 — Row order is part of the answer
*instances: 3 attributed; ~8 in the mined cluster*

Emitted rows carry an order that a consumer depends on — dedup identity,
last-row-wins, a prompt's salience gradient, a human reading a report — and
a port that re-derives the set correctly in a different order has changed
the answer.

**Canonical instance.** `internal/skills` r1 (medium): skills rode the
decompose prompt LAST where Python's `extras` order puts them first — a
silent A/B confound, not a cosmetic difference. `internal/pack` r3 (low):
the report callback emitted malformed rows before clean ones.

**Tripwire.** Where a Go map replaces a Python dict, the insertion order
was load-bearing until proven otherwise — Python has preserved it since
3.7 and Go actively randomises. Compare RENDERED sequences, not sets.

---

## F. Ambient state, concurrency and failing open

The family the catalog was missing entirely until the 2026-08-26 backfill.
These do not show up as a wrong value; they show up as a right value at the
wrong time, or a wrong value nobody is told about.

### L32 — A read-decide-act window is a lock you did not take
*instances: 3 attributed; ~10 in the mined cluster*

Between the read and the write, the world moved. The code is correct for
one actor and this system never has one actor.

**Canonical instance.** `internal/scans` r1 (high): two overlapping verify
cadences let the LOSING revert overwrite a truthful `degraded` stamp with
`degraded_revert_failed` and fire a false BLOCKING alarm. `internal/record`
r11 (medium): `StampOutcomeStopVerdict` decided its miss ABOVE the lock
under a comment claiming Python reads first — Python's lock comes first, so
the port bought a statistic it had no right to.

**Tripwire.** For every read that feeds a later write to the same place,
say out loud what a second writer does in between. "Nothing writes this
concurrently" is a claim; find the writer list before believing it.

### L38 — A failure that fails OPEN
*instances: 3 attributed; ~6 in the mined cluster*

The error path returns the permissive answer: the input unchanged, the
default allow, the empty filter. It is invisible in tests because the error
path is the one nobody fixtures.

**Canonical instance.** `internal/guard` r17 (high): the decode-budget
counter failed OPEN — `decode()` returned its input unchanged on
exhaustion, so draining the budget and then appending an encoded payload
cleared the scanner. Counterpoint in the same family: `internal/knowledge`
r2 deliberately failed a truthiness parse OPEN, because for provisional
gates *that* is the conservative direction. The lens is not "always fail
closed"; it is "name which direction is conservative HERE, and prove the
code takes it."

**Tripwire.** For each `except` / `if err != nil`, ask what the caller
concludes from the value returned. If it concludes "safe", say why.

### L39 — A verb that takes a workspace argument but reads ambient config
*instances: 1 attributed; ~4 in the mined cluster*

Half the resolution is explicit and half is global, so the verb works in
production and lies in tests — or works in tests and picks the wrong store
in production.

**Canonical instance.** `internal/playbook` r9 (high): `Curate` resolved
its file, archive dir and lock from its workspace ARGUMENT and its three
config gates from ambient `MARO_WORKSPACE`.

**Tripwire.** Grep the body of any workspace-taking function for env reads
and package-level config lookups. The argument is a promise about where the
whole operation happens.

### L40 — A counter that reports success while losing rows
*instances: 2 attributed; ~6 in the mined cluster*

Something is dropped on a path with no signal, and the summary line the
operator reads is computed from what survived.

**Canonical instance.** `internal/recall` r2 (medium): `FindPriorAttempts`
silently dropped malformed run metadata; the skipped count is now surfaced.
`internal/director` r3 (medium): the malformed-entry counter ignored
non-object entries entirely.

**Tripwire.** Every filter needs a denominator. If a loop can `continue`,
the thing it skipped is either counted or lost.

---

## G. Process patterns (not about code)

### L41 — An OVER-DETERMINED fixture measures none of the rules that agree

*instances: 4*

Distinct from L1, and the distinction is what makes it findable. L1 is a
test whose *assertion* cannot fail. Here the assertion is fine and the
INPUT is the problem: it varies several things at once, several rules
force the same answer, and a mutant that breaks one of them is carried by
the others. The test goes on passing for a reason that has nothing to do
with what it was written to check.

**Canonical instance.** `notes_diff_test.go`'s Unicode-split fixture
separated goal tokens with `\x1c`, U+00A0 *and* U+3000 at once, to show
that Python's `.split()` is not `strings.Fields`. But Go's `Fields` splits
the latter two as well, so the matching token survived under either
spelling and the mutant that swapped the splitters walked through. `\x1c`
is the only discriminator between the two, and it now appears alone.

Three more in the same round: the healthy-diagnosis filter's fixture used a
row with *no evidence*, so keeping the row still produced nothing; `"by"`
is the twentieth stopword but every case also overlapped on a content word,
so a set short by one changed no answer; and the ranking sort's fixtures all
had a single distinct overlap value, which leaves both the comparison's
direction and its stability unmeasured.

**Tripwire.** For each rule a fixture is meant to exercise, ask what the
answer would be if *only that rule* were wrong. If it does not change, the
fixture has a confound — split it until every case has one variable.

### L42 — Two implementations that agree at small n need a fixture past the threshold

*instances: 1*

Library internals routinely fall back to a simpler algorithm on small
inputs, and the simpler algorithm often has the property the caller was
relying on the sophisticated one NOT to have. A fixture below the threshold
cannot tell the correct call from the wrong one, and reports a survivor
that is really a sizing problem.

**Canonical instance.** `sort.Slice` versus `sort.SliceStable` on
`find_relevant_failure_notes`' ranking. Under 13 elements Go's pdqsort
delegates to insertion sort and is therefore *accidentally* stable. Worse,
with every key TIED its partitioning happens to leave the order alone at
*any* size — measured on this box up to n=60. Only the combination
permutes: twelve tied rows plus one scoring higher. The fixture is now 13
rows with the higher-scoring one oldest.

**Tripwire.** When a mutant swaps one library call for a near-neighbour,
find the neighbour's small-input fallback before believing a MISS. The
threshold is a number in someone else's source; measure it rather than
reasoning about it.

### P1 — Verify each finding's code claim before fixing
*standing; measured ~30–50% of adversarial findings are hallucinated*

### P2 — Later rounds review the WHOLE chunk + fixes, not the latest diff
*standing (Jeremy, 2026-08-22)*

Granular per-diff review produces granular fixes; whole-chunk review catches
the split-control-flow seam class.

### P3 — Escalate reviewer tier after round 1 on same-model fallback
*standing (Jeremy, 2026-08-22)*

Don't grind many rounds at the cheapest tier.

### P4 — A running battery owns the working tree; do not read it OR write it
*instances: 3*

Its restore set does not include test files, so a test-file edit mid-run
produces a spurious BUILDFAIL. Do not edit a battery's `FILES` while it runs.

**And do not run the suite either.** The 2026-08-26 notes round spent three
exchanges diagnosing a "misaligned fixture list" that was a live mutant: a
`go test` issued while the battery was between restores compared against
whichever mutation happened to be on disk, and the answer CHANGED between
consecutive runs because the battery moved on. The tell was there and got
read past — the failure named one case while quoting another's expectation,
which no test-side bug can produce.

**Tripwire.** A battery run is exclusive. Poll for the process to exit
before touching the package — and pipe its output to a file rather than
through `tail`, which buffers everything until the run ends and makes
"still running" indistinguishable from "produced nothing".

### P5 — Rounds converge to lows by round 3–4
*standing*

The fixpoint is real and it arrives. Rounds after that are cheap insurance,
not discovery.

### P6 — The round's HIGH lives inside the previous round's own fix
*instances: 1 attributed; ~25 in the mined cluster — the largest, and the reason P2 exists*

The single most common shape in the whole record. A round fixes something;
the next round's most severe finding is *in that fix*. It recurs at
`internal/guard` r2, r3, r4, r6, r7, r8, r9, r13, r14 and r16; at
`internal/pack` r2 and r3; `internal/budget` r5 and r6; `internal/evolver`
r2 and r3; `internal/graduation` r2 and r3; `internal/pyval` r2.

**Canonical instance.** `internal/guard` r14 (high): r13's anchor widening
was out of lockstep with `urlHostAllowed`, which terminates on a backslash
and strips `:port` — so both of those forms scanned clean through the new
anchor. The r13 fix created the r14 hole.

**Why it happens.** A fix is written under time pressure, by whoever most
recently loaded the context, against the ONE case the finding named. It
gets less scrutiny than the original code because it is small and because
it is "just the fix". And the next round is usually pointed at the diff.

**What to do about it.** This is the mechanism behind Jeremy's P2 decree —
review the whole chunk plus its fixes, not the latest diff — and P6 is the
measured justification for it. Concretely: a fix is a change, so it gets
the same lens walk the original code got, and the fix's own boundary cases
get fixtures before the round is called done. A round that only re-reads
the diff is structurally unable to see this class, because the diff is
exactly where it lives and "the diff is the new code" is precisely the
assumption that lets it through.

**Tripwire.** Before closing a round, list the fixes it made and ask of
each: what did this fix newly make possible? Then check that.

---

## The ledger

`scripts/review-ledger.py` appends one row per finding to
`review/findings.jsonl` so the recurrence counts above stop being recalled
and start being computed:

```bash
# record a finding
python3 scripts/review-ledger.py add \
    --arc go-port --round 4 --target internal/evolver \
    --reviewer opus --severity high --lens L12 \
    --verdict confirmed --fix-site production \
    --summary "load_suggestions was half a reader"

# what recurs, and how much of a round survives verification
python3 scripts/review-ledger.py report
python3 scripts/review-ledger.py report --arc go-port --by lens
```

### Collecting it without remembering to

A ledger that depends on someone typing `add` after each round collects
nothing. `prompt` prints the block a review subagent is given — the output
contract plus the whole catalog, ordered hottest-lens-first — so a round
returns ledger-shaped JSON instead of prose and the round ends with an
`import`, not with transcription:

```bash
python3 scripts/review-ledger.py prompt --arc go-port --round 5 --reviewer opus
# ...paste into the review agent; it returns a JSON list...
python3 scripts/review-ledger.py import /tmp/round5.json
```

Two things that block belongs to. It hands the reviewer the catalog, so a
lens stops being something only the orchestrator remembers. And it asks for
RETRACTED findings to be recorded as `hallucinated` — without those rows
the denominator in (1) below never exists, which is exactly why the
backfill could not supply it.

Fields per row: `arc`, `round`, `target`, `reviewer`, `severity`, `lens`,
`verdict` (`confirmed` | `hallucinated` | `known-gap` | `wontfix`),
`fix_site` (`production` | `test` | `battery` | `doc` | `none`),
`summary`, `recorded_at`.

Two numbers make the loop tighter, and both need this data:

1. **Per-reviewer hallucination rate** — P1's ~30–50% is a remembered
   figure, not a measured one. If it varies by tier or by target, the
   escalation rule (P3) should be keyed off that rather than off round
   number. **The backfill cannot supply this**: it was mined from
   `PORT.md`, which records findings that were ACTED ON, so its 1% is a
   measurement of the record's selection rule. Only live recording — every
   finding, including the ones verification kills — produces a real
   denominator. This is the ledger's main open job.
2. **Lens recurrence by arc** — a lens that keeps firing in one subsystem
   is a structural problem in that subsystem, not a review problem. The
   `clipRunes` tally (L14, six copies) is the shape: once counted, the fix
   stops being "look harder next time" and becomes one commit.

### What the first backfill actually said (2026-08-26)

562 rows mined out of `go/PORT.md`, 253 of them now attributed to a lens
(229 by the miner, 24 more when the nine new lenses below gave their
clusters somewhere to go).
Three things it changed:

- **L1 is the runaway leader at 45**, more than the next two combined. The
  catalog had claimed L26 (prose divergence) was the most frequent defect
  in the port; that was a memory artifact from one arc, and the claim has
  been corrected. Tests that agree instead of measuring is not merely the
  largest family — its root lens alone outnumbers every other single lens.
- **Nine lenses did not exist** (L32–L40) and one process pattern (P6) was
  invisible despite being the largest cluster in the record. An entire
  family — ambient state, concurrency, failing open — had no home in the
  catalog at all, which means ~26 findings had nowhere to be filed and so
  were never counted as recurring.
- **P6 explains P2.** Jeremy's whole-chunk-review decree was made on taste
  in August; the data says ~25 of the record's findings are a round's HIGH
  living inside the previous round's own fix. The decree was right and now
  it has a number.

Regenerate any count in this file with:

```bash
python3 scripts/review-ledger.py report --by lens
python3 scripts/review-ledger.py report --by reviewer
```
