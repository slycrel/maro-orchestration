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
**742**. The counts below are regenerated from the ledger, not recalled.

That regeneration is a claim this file has already failed twice: at the
2026-08-26 refresh L23 read `8` against a ledger holding `10`, and two
rounds later L37 had drifted the same way. Both times a round recorded
rows without re-deriving the counts, and the sentence above went on
asserting they were derived. It is no longer a discipline:

```bash
python3 scripts/review-ledger.py sync-catalog        # or --dry-run
```

rewrites every `*instances:*` line in this file from the ledger, preserving
the editorial clause some of them carry. Run it after every import. The
hand-editing that let the drift in twice is now the wrong way to do it.

**Two things the counts are not.** They are lower bounds: 314 of the 742
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
*instances: 63 — the most frequent single defect in the Go port*

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
*instances: 3*

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

**A Go type switch is an enumeration, and Python's `isinstance` is a class.**
Found twice in one file by artifactcheck r1 (2026-08-26). `isinstance(te,
dict)` admits every mapping; the port wrote `case ToolEvent` — one Go
spelling of the same thing — and a transcript that arrived through
`json.Unmarshal` (a plain `map[string]any`, which is what every real caller
produces) matched no case at all. The fabrication check silently found
nothing on real input while every fixture passed, because the fixture table
is written in the OTHER spelling. The same shape at `te.get("input")` one
level down.

This is the enumeration failure with no list to look at: nothing in the Go
reads like a list of cases the author chose, so the tripwire above does not
fire. The one that does:

> **Wherever the Python asks `isinstance(x, dict)` (or `list`, or `str`),
> the port has as many arms as there are Go spellings of that class, and
> the fixture table probably only builds ONE of them.** Send a fixture
> through `json.Unmarshal` and see whether the answer changes.

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
*instances: 14*

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
*instances: 34*

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

**And the reviewer's half of it, added round 4: an exemption is a claim,
so execute it.** A battery that skips sites "because no input can observe
them" is asserting something about the whole input space, in a comment,
usually from reading. Round 4 took the six exemptions written into the
introspect battery, turned each back into a real mutant in a scratch tree,
and fuzzed it over 11,620 argument lines: all six held at 0 diffs. That is
a different fact from the argument that produced them, and it is the one
worth writing down — so the comments now carry the number rather than the
reasoning. A reasoned exemption and a measured one are indistinguishable
in prose, which is exactly the gap L28 names.

### L9 — Derive must-detect mutations from the FILE, not the diff
*instances: 8 — plus standing (Jeremy, 2026-08-16)*

A guard derived from what changed cannot catch what was always wrong.

**Canonical instance.** The round-2 battery for `internal/introspect/cli.go`
derived its 27 argument-layer mutants from the *findings list* of the review
that preceded it — the inversion this lens names, one level up. It scored
92/95 and every rule nobody had listed went unmutated: the `\.?` in the
negative-number matcher, the negative overflow bound, the terminator's fate
in the extras. Round 3 found all three by walking the file's decision sites
in order.

**Tripwire.** Write the battery by reading the file top to bottom and
mutating each decision — not by reading what the last round found. If a
site is deliberately unmutated because no input can observe it, say so in
the battery, in a comment, next to the sites it covers (L8); an unexplained
absence and a considered exemption look identical six weeks later.

### L10 — A test helper is code, and a guard it repeats is a guard nothing pins
*instances: 2*

The sharpest form: the test does not merely repeat a guard, it
**re-implements the production mapping and then asserts its own copy**
against the reference. Both sides of the comparison are then the test's,
and the code the operator runs is not in the picture at all.

**Canonical instance.** `runIntrospect` exists for one reason, stated in
its own comment: map a usage error onto argparse's stderr block and exit
code 2. It had no test. Next door, `cli_diff_test.go` wrote its own
`errors.As` → `2, ue.Stderr()` switch and compared THAT to CPython —
which passes whatever the wrapper does. Round 4 proved it: `os.Exit(2)` →
`os.Exit(1)` and `ue.Stderr()` → a bare `"error\n"` both left
`go test ./cmd/maro/ ./internal/introspect/` green.

**Tripwire.** When a test needs a piece of production logic to interpret
the result, CALL the production function — do not restate it. If it is
not exported or not callable, that is the finding: extract it, so the
comparison runs through the same code the binary does. And a wrapper
whose whole job is a mapping needs a test that EXECUTES the mapping; if
`os.Exit` is in the way, re-exec the test binary as a child rather than
skipping the two claims only the wrapper makes.

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
*instances: 8*

**Canonical instance.** Python's `_rows_as` is TWO readers stacked: an
announced framing read AND a dataclass construction that EXCLUDES rows and
counts them as schema drift. The Go port had only the framing half at both
`load_outcomes` and `load_suggestions`. Consequence: the evolver minted a
cycle off three rows CPython excludes, and `get_suggestion` handed the
auto-revert guard `applied_manually=false` for a row a human had applied.

**Tripwire.** When porting a function that returns typed objects, ask what
happens to a row the constructor rejects. Silence is the wrong answer.

### L13 — A fix at the site that has the fixture is not a fix for the class
*instances: 8*

**Canonical instance.** `dailylog.go` already carried the outcome schema
filter, measured and correct, with a comment quoting CPython's own warning —
applied one layer too low, with the reasoning "LoadOutcomes' tolerance is
right for its other consumers" written down. That reasoning was wrong, and
the comment recording it had to be corrected in place.

### L14 — A helper you did not look for is a helper you will write again
*instances: 21*

**Canonical instance.** `pyval.Clip` is the shared Python-semantics rune
slicer. Six packages carry a private `clipRunes` copy (`scans`,
`graduation`, `playbook`, `evolver`, `skills/utility`, `director`). The
introspect port deliberately did not become the seventh.

**The base package can be the one that did not look.** artifactcheck r1
(2026-08-26): the Unicode-16-minus-15 `\w` supplement — 5004 code points in
27 ranges — existed as three byte-identical hand copies, in `artifactcheck`,
`internal/metrics` and `internal/skills`. `internal/pytext` is the package
all three already import for exactly this class of skew, and it carried only
the 80-code-point DIGIT subset, under a doc comment arguing that the honest
fix was a newer Go toolchain rather than "a hand-copied list that rots".

Two things make this worth its own instance. The argument in the base
package had already been overruled twice, with measurements, by its own
dependents — the comment was load-bearing in the wrong direction and nobody
reading pytext could see that. And the partial helper was worse than none:
the 80 digits are a SUBSET of the 5004, so `DigitClass` matched U+10D40 and
`WordClass` did not, which is a state CPython has no spelling for. A helper
that covers part of a class silently splits the class.

> **When you find a private copy of something, grep for the OTHER copies
> before writing the seventh — and when the base package declines to hold
> it, read that refusal as a claim (L52) rather than as a decision.**

### L15 — A helper that fixes a class does not fix the class — it fixes the callers that reach it
*instances: 9*

### L16 — A field is TWO claims (the writer's and the reader's)
*instances: 7*

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
*instances: 4 attributed; ~5 in the mined cluster*

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
*instances: 16*

**Canonical instance.** Switching to an announced ordered read made numbers
`json.Number`, so `intOf`'s `float64` arm stopped matching and a human
surface rendered "Total tokens: 0".

### L19 — A zero value that must mean two things means neither
*instances: 23*

**Canonical instance.** `load_outcomes(limit=0)` in Python returns NOTHING
(`[:0]`). The port reads `limit <= 0` as "everything" — a deliberate
divergence, now pinned by a named-divergence test rather than left implicit.

### L20 — Python's operators are not Go's
*instances: 27*

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
*instances: 16*

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
*instances: 26*

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
*instances: 75*

**Canonical instance.** `matchesLookUp`'s doc comment still said "the words
list above carries the two common spellings" after those spellings were
removed from the list.

**The other half: an enumeration can be wrong at BIRTH, not only by decay.**
Found by r2 of `internal/syshealth` (2026-08-26), where four of five
findings were this and none of them had drifted. `nextCycle`'s doc said
"three lanes" and there were four — the missing one silently wrapped a
counter past int64 and WROTE the negative. `asDict`/`asList`'s doc ended
"the day a caller appears the arms are already right"; `[]string` was
missing, and `pyval` calls a `[]string` a list in three helpers. A fixture
comment said "the only fixture where the 200-char clip does anything" when
three fixtures reach it — and correcting it uncovered a real unported
`%.200R` truncation in `pyval.intFromString`. Two counts in `PORT.md` were
off (five constants for seven, 37 fixtures for 47), and a guard's
justification named a fixture that does not do what the justification says.

Decay needs history to diagnose. This does not, and the check is mechanical:

> **For every number a comment states, count it against the file as it
> stands today.** Not against the diff, not against what it said last
> round — against the file.

Six numbers were stated in that chunk; six had never been counted; four were
wrong. Counting them took minutes and one of the four led to a fix in a
shared primitive eleven packages call. Cheapest lens in the list.

**The third direction: correct when written, with no way to stay that way.**
artifactcheck r1 (2026-08-26). Two comments in one file said "191
fixtures"; both were exactly right the hour they were written, and the
table reached 192 the same day. Neither decay nor a birth defect — a
conclusion whose truth was made to depend on a count that nothing
maintains. The fix is not to re-count it:

> **If a comment's point survives without the number, delete the number.**
> Both now say "the whole fixture table", which is what the sentence
> actually meant and cannot go stale.

A number earns its place only when the number IS the finding — "26.97% of
timestamps differ by an ulp", "5004 code points" — and those should carry
the measurement that produced them.

### L29 — An idiom is not a defect — the defect is a spelling that does not match the spelling at ITS OWN site
*instances: 2*

### L30 — A fixture travels through a channel, and the channel has opinions about what it carries
*instances: 3*

**Canonical instance.** `pyprobe.RunJSON` has no `UseNumber`, so a known-gap
pin comparing re-decoded numbers saw `1.0` come back as `1`. The fix was to
emit the compared fields as a Python-side JSON *string*.

### L31 — Sometimes the answer to a survivor is DELETING production code
*instances: 5*

A distinction nothing reads is a second guard making the first unobservable.

**And sometimes the answer is to KEEP it and say why** — the two cases look
identical from the battery's side, so the lens has to carry both. Delete
when the branch is unreachable *and the seam is closed*: the lens
registry's `costs` lookup had a single writer of both maps and no plausible
second one, so the dead default went. Keep, with the proof in a comment,
when the guard states an intent at a seam a later edit will open.
`internal/introspect/cli` r1 retired two unkillable mutants this way — the
`healthy` guard in `RenderRecovery` (subsumed by the `PlanRecovery` lookup
below it, which has no `healthy` row *yet*) and the graduation-candidate
branch (a tautology, because an upstream `continue` already filtered on its
negation). Deleting the first would make a future `healthy` row silently
start emitting recovery plans for healthy loops.

### L34 — A clip marker is not the end of the string
*instances: 2 attributed; ~5 in the mined cluster*

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
*instances: 5 attributed; ~8 in the mined cluster*

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
*instances: 8 attributed; ~6 in the mined cluster*

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
*instances: 3 attributed; ~6 in the mined cluster*

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

*instances: 8*

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

*instances: 5*

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

**Second instance, same threshold.** `find_recurring_patterns` sorts
failure classes by count with `sort.SliceStable`, and the stability IS the
tie-break Python's `list.sort` gives it — equal counts come back
most-recently-seen first. Swapping in `sort.Slice` survived a battery whose
largest fixture had four classes. The fixture is now thirteen: twelve tied
at one occurrence plus one ahead, with the leader written OLDEST so the
sort has to move it and leave the other twelve alone.

That two unrelated ports of two unrelated Python functions both landed on
this exact boundary is the argument for the lens: it is not a quirk of one
sort call, it is what happens whenever a Go port relies on stability and
tests it at the size a fixture is comfortable to write.

**Tripwire.** When a mutant swaps one library call for a near-neighbour,
find the neighbour's small-input fallback before believing a MISS. The
threshold is a number in someone else's source; measure it rather than
reasoning about it. For Go's sort that number is 13 — and with every key
tied it is unbounded, so the fixture needs a non-tied element too.

### L43 — A guard's threshold is not where its rule starts working

*instances: 2*

A rule sits behind a gate — `if len(xs) >= 3`, `if n > 0` — and the gate
reads as the answer to "what is the smallest input that exercises this?".
It usually is not. The arithmetic *inside* the rule can be unsatisfiable
across part of the region the gate admits, and a fixture placed at the
threshold then enters the branch, computes, finds nothing, and passes.
Coverage tools count the branch as covered. Every mutation of the rule
survives, and the fixtures that are supposed to cover it are the reason
nobody looks.

This is not L41. An over-determined fixture reaches its rule and is carried
by a confound; here the fixture never reaches the rule at all, and no
amount of splitting variables fixes it — the input is the wrong *size*. It
is not L42 either: nothing about a library's internals is involved, the
vacuity is in the reviewed code's own algebra.

**Canonical instance.** The architecture lens's token-outlier rule: gate
`len(tokens) >= 3`, bar `t > 3*(sum // len)`. With exactly three
token-bearing steps no step can ever clear the bar, because
`3*floor(S/3) > S-3` and `S >= t` together give a bar of at least `t`.
Four steps is the first width that can fire, and only when the other three
are small. Three fixtures sat here named "an outlier over both bars", "an
outlier over the ratio but under 50000" and "a step over 50000 but under
the ratio" — all three-step, and **none of them ever entered the finding**.
Six separate mutations of the rule survived behind them. The same algebra
retired a seventh mutant that relaxed the gate to 2: with two values the
bar is at least `1.5x` the larger, so the relaxed gate admits a branch
that can only find nothing.

**Tripwire.** For any gated rule, solve for the smallest input that
*satisfies the rule*, not the smallest that passes the gate — and assert
the rule actually fired. A fixture named for a finding should fail if the
finding is absent; three that quietly produced empty output is what this
cost. When gate-width and rule-width disagree, that gap belongs in a
comment, because it is a property of the source, not of the test.

### L44 — A fixture's name is a coverage claim, and nothing checks it

*instances: 4*

The fixture feeds the code through a field the code does not read. Its
inputs are shaped like the real thing but keyed the way the *writer* names
them rather than the way the *reader* reads them, so the case runs, both
sides read the same nothing, and the differential agrees. The fixture's
name goes on asserting coverage of a rule its input never reached.

What makes this survive review is the second half: the mutants for that
rule usually die anyway, to some *other* fixture that happens to supply
the value properly. So the battery reports full detection, the case list
reads as thorough, and the one fixture written for the rule is decorative.
A green mutant is evidence that *some* fixture covers the rule — never
evidence that the one named for it does.

Distinct from L43: there the input reaches the code and is the wrong size
for the rule's arithmetic. Here it never reaches the code at all. Distinct
from L28: that is a comment asserting coverage; this is a *test name*
doing it, which is worse, because a comment is at least read as prose
while a test name is read as a fact.

**Canonical instance.** `internal/introspect/cli` r1: the CLI
differential's `loop-gamma` fixture — "the bar widths" — gave four steps
`"tokens": 4999 / 5000 / 900000 / 0` to exercise the token bar's floor and
its `min(50, ...)` clamp. `BuildStepProfiles` computes a step's tokens as
`tokens_in + tokens_out` and never reads a `"tokens"` key, so all four
steps profiled at **zero** and the fixture had never rendered a bar in its
life. Four bar mutants (divisor, both clamp spellings, the `Grouped` call)
died regardless — to `loop-alpha`, which prices its steps properly.

**Tripwire.** For a fixture named after a rule, assert the rule's own
intermediate, not the field you wrote: read back what the code computed
(the profile, the parsed row, the derived key) and check it is non-trivial
before trusting the case. And when a mutant dies, ask *which* fixture
killed it — narrow the run to the case that claims the coverage. The
cheapest version of this is a battery run filtered to one fixture; if the
mutant survives that, the name is a lie even though the suite is green.

### L45 — A battery measures only what the harness can EXPRESS

*instances: 2*

A mutation score is a statement about the fixtures. But the fixtures reach
only as far as the harness lets them describe an outcome — and a case
struct with no field for some result cannot hold a fixture that produces
it. Every behaviour behind that wall is unmeasured, and the detection rate
never says so. It reports a high number about a smaller space.

The tell is an assertion branch that reads *"this shouldn't happen"*. A
`t.Fatalf("the other runtime exited: %v")`, a `continue` past a shape the
comparison "can't handle", a normalisation that folds two answers into one
before comparing — each is a wall, and walls are invisible from inside.
They look like defensive hygiene rather than scope.

**Canonical instance.** `internal/introspect/cli` r2 (high): the CLI
differential's probe captured CPython's `SystemExit` faithfully — and the
assertion treated *any* exit as a test bug. So no fixture could describe an
argument line CPython refuses, and the entire error-parity surface sat
outside the battery. A 75-mutant battery reported 72 detected while six
separate argparse-vs-`flag` divergences went unseen, four of them silent
(wrong output, no error). The fix was one `wantExit bool` on the case
struct; the six fixtures followed immediately, and so did the mutants.

**Second instance, and it is the more useful one: a wall can MOVE instead
of falling.** The `wantExit bool` that fixed the above was still a boolean,
and the next round's battery found `C78` surviving because of it. Deleting
the guard that refuses `-latest` left every fixture green — the mutant
still *refused* the input, just at a different offset and with a different
message, and "refuses" was the only thing the harness could say. One step
back from where the wall had been. The real fix was to compare the exit
code as a NUMBER and stderr in FULL, which then forced the port to render
argparse's usage and help blocks verbatim — and, with the messages finally
being compared, surfaced eight more behavioural divergences.

So the enumeration has to go one level past the first answer. "Refuses" is
not an outcome; "refuses with this message and this code" is. Any time a
harness collapses a rich result into a boolean, the mutants can only ever
probe the boolean.

**Tripwire.** Before quoting a detection rate, ask what OUTCOMES the case
struct can represent — not what inputs it can supply. Enumerate the result
space of the thing under test (renders / refuses / raises / writes /
returns empty) and check the harness has a way to say each one. Then ask
the same question of each branch of that enumeration, because a boolean
that answers "did it refuse" is itself a wall. And read every `t.Fatal` in
the comparison path as a scope declaration, because that is what it is.

### L46 — Substituting a local library for the ported one costs a divergence per rule nobody enumerated
*instances: 12*

A port that reaches for the host language's equivalent library — `flag`
for `argparse`, `regexp` for `re`, `filepath.Match` for `fnmatch` — is not
porting the original. It is encoding the differences its author happened to
think of, and treating that enumeration as the class. Every rule of the
original that nobody named survives as a silent divergence, and the ones
that survive are by construction the ones nobody would think to test.

The tell is a **normalizing pre-pass**: a function that fixes up the input
before handing it to the local library. Its length is a measure of how much
grammar is being approximated rather than ported, and each new case bolted
onto it is evidence the approximation is not converging.

**Canonical instance.** `internal/introspect/cli`. The argument layer was
`splitArgs` — a pre-pass that rewrote argv and then called Go's `flag`. An
outside review found six argparse-vs-`flag` divergences in it, four of them
silent (wrong output, no error). Fixing those six left the pre-pass in
place. Porting `_parse_optional` / `_get_option_tuples` / the consume loop
wholesale — deleting `flag` from the file entirely — surfaced **eight
more**: `-hh` prints help; unknown options are deferred to the end of
parsing while ambiguous ones fail where they are consumed (so `-h --l`
prints help and `--l -h` is an error); a value-taking option will not
swallow an option-looking token but will swallow a lone dash; only the
first `--` terminates; `_get_action_name` joins every option string the
action owns. None of the eight is exotic and none was findable by
enumeration, because the enumeration was the bug.

**Tripwire.** When the port substitutes a library, port the ORIGINAL's
control flow — its own function boundaries, in its own order — even when
that means writing more code than the substitution would. Measure the
grammar against the real interpreter rather than reasoning about it; every
one of the fourteen rules above came out of a CPython transcript, and
reasoning had produced confident wrong answers about several of them. If a
pre-pass is unavoidable, its length is the finding.

**Round 3 postscript, and why this lens grew a sibling.** The rewrite that
deleted `flag` obeyed half the tripwire and not the other half: it ported
`_parse_optional` and `_get_option_tuples` as their own functions, and then
folded `consume_optional` and `consume_positionals` — a nested loop and a
pattern match — into one flat pass over the tokens. Five more divergences
came out of that flattening, and they were the LAST five, found only when
the loop and the span were written the way CPython writes them. See L48:
the substitution is one way to lose the original's shape, and rewriting it
by hand is another.

### L47 — The source you ported is not always the source you test against
*instances: 4*

A constant lifted from a standard library is pinned to the VERSION it was
lifted from. The differential runs against whatever interpreter is on the
box, so a port can be faithful to a source nobody in the room is running —
and it fails in the one direction reviews are worst at, because both the
code and the comment describing it are *correct*, about the wrong release.

**Canonical instance.** `negativeNumber` carried
`^-\d+$|^-\d*\.\d+$`, the anchored-at-both-ends
`_negative_number_matcher`. The box's `python3` is 3.14, where it is
`-\.?\d` applied with `.match`, anchored only at the start: every token
beginning with a dash and a digit is a positional, so `-1latest` is a loop
id and not an unknown flag. Second instance in the same file: the help
block's `options:` heading is 3.10+, where 3.9 says `optional arguments:`.

**Round 4 sharpened this twice, and the second one is the real lesson.**
First, the *fix's own comment* got the boundary wrong — it said the
anchored spelling held "through CPython 3.11", and `python3.12` still
carries it, so the change landed in 3.13. A version claim written while
fixing a version bug is not automatically measured.

Second, and worse: **python3.12 is installed on this machine**, and the
probe invokes bare `python3` off PATH. The port is correct for whichever
interpreter PATH resolves to today and would fail its own differential
under the other one — an interpreter sitting one PATH entry away. The
third instance is the same shape one layer down: `_get_nargs_pattern`
*builds then strips* the `-*` runs through 3.12 and *selects the pattern
directly* in 3.14. Same result for the nargs this parser uses, different
mechanism — and the comment beside the ported code named 3.12's.

**Tripwire.** When a constant or a regex comes out of a stdlib, write the
VERSION it came from in the comment next to it, and check that version
against the interpreter the differential actually invokes. `python3
--version` is the cheapest review step in this catalog — and follow it
with `ls /usr/bin/python3.*`, because the versions that are merely
INSTALLED are the ones a PATH change turns into a silent red suite.
Bisect the boundary instead of asserting it: run the snippet under every
interpreter on the box rather than trusting a changelog reading. Where the
difference is real but out of scope, pin it in the comment (as the
`options:` heading is) rather than leaving the reader to assume the
newest.

### L48 — A flattened control flow is a different program
*instances: 14*

**The flattening you cannot see as code** (syshealth r3, 2026-08-26 — the
arc's first HIGH after two rounds of lows). Python's `config.memory_dir()`
looks like a getter and is not:

```python
def memory_dir() -> Path:
    p = workspace_root() / "memory"
    p.mkdir(parents=True, exist_ok=True)
    return p
```

`run_health_probes` spells its critical section
`with locked_write(_snapshot_path())`, so the directory is created while
CPython is still evaluating the ARGUMENT — before the load, before the probe
loop. The port had the path as a pure `filepath.Join` and the only `MkdirAll`
inside the writer, which runs after every probe. Result: on a workspace whose
`memory/` cannot be created, CPython reports `ran=0` with **zero probes
called** and the port ran all of them.

Nothing about the Go read as a flattening. There was no collapsed `if`, no
merged return, no rewritten loop — the divergence was a **side effect that
lived inside a name**.

> **Read the helpers the original calls, not just the original.** A function
> whose name is a noun can still mutate the world, and when it does, WHERE it
> is called is part of the control flow.

Corollary on fixtures: 47 of them shared an unstated assumption ("the memory
dir exists") because the two that touch it chmod a directory that is already
there. A shared assumption looks exactly like coverage. When a fixture set
sets something up the same way every time, that setup is an untested premise.

A port can get every DECISION of the original right and still be wrong,
because the original's answer depends on the SHAPE the decisions are made
in: what loops, what defers, what runs before what. Flattening a nested
loop into one pass, or two mutually-recursive consumers into a single
switch, preserves each rule and loses their sequencing — and sequencing is
observable whenever an action has an effect (printing, exiting, consuming
a token) that a later rule would have prevented.

This is L46's sibling. There the original's shape is lost to a library
substitution; here it is lost to an author who read the original, understood
each branch, and then wrote the branches in a structure of their own. The
second is harder to catch, because the code looks like a careful port and
every individual line can be defended.

**Canonical instance.** `parseIntrospectArgs` consumed each option token in
one pass. CPython's `consume_optional` is a `while True` that collects
actions into `action_tuples` and takes NONE of them until the token is
fully understood. Every decision inside my flat version matched; the
deferral did not. `-hh=x` collects a help action, re-reads the tail to
another `-h`, and then refuses the leftover `=x` — CPython exits 2, and the
flat port printed help and exited 0, because it took the help action the
moment it found it. Five inputs of that shape were wrong, and a comment I
had written asserting "`-h`'s action exits before the tail is ever read"
was the flattening stated as a fact.

**Second instance, same rewrite.** `--` was skipped unconditionally, which
is the right answer for `a -- b` and the wrong one for `a b -- c`. CPython
never special-cases the terminator at consumption at all: it falls to
`consume_positionals`, whose nargs pattern for `loop_id` is `(-*A?-*)`, and
the `--` is removed only when a positional's matched span happens to cover
it. One line of "skip the separator" stood in for a regex match against a
classification string, and six argument lines disagreed.

**Third instance — and the one that shows what the flattening COSTS.**
`successful_run_cost_p90` has two ways of saying "no opinion" and caches
only one: `if not root.is_dir(): return None` returns BEFORE the cache
write, every other answer after it. The port had ONE return and cached
whatever came back, so a workspace that had not run anything yet stayed
"no opinion" for fifteen minutes after its first run landed — the budget
gate blind for a quarter hour of a fresh install's life.

What makes this the instructive instance is the second defect it had
already grown. The same function's mtime `stat()` raises in CPython when a
card is deleted mid-glob, and the port deliberately DIVERGED there, with a
comment arguing that fifteen minutes of no-opinion over one cleaned-up run
was worse than losing the sample. That argument was true — but only because
the flattened single exit cached the answer. Restoring the second exit made
the divergence unnecessary, and it was deleted rather than defended. A
flattening does not just lose the original's shape; it creates PRESSURE to
diverge elsewhere, and the divergence looks locally justified because the
justification is real. Look for a named divergence sitting next to a
flattened control flow: it may be load-bearing only for the flattening.

**Fourth instance — the shape lives in a library call.** (artifactcheck
slice 1, 2026-08-26.) `os.walk` defaults to `followlinks=False`. That one
default is a control-flow fact with two halves: a symlinked DIRECTORY is
yielded in `dirnames` and is never descended into, so it contributes
nothing at all — not its own name, not one file beneath it — while a
symlinked FILE lands in `filenames` and is stat'd through the link. The
port wrote the loop by hand with `os.ReadDir`, whose `DirEntry.IsDir()`
answers about the LINK, and so descended into link targets and counted
every file under them.

There was no simplification to see. The Go is a faithful-looking
transcription of a walk, and its own comment three lines above already
stated the correct rule. What was flattened was a distinction the
original never spells out because a default spells it for it: the port
classified an entry ONCE where CPython classifies by following and then
descends by not following. Two decisions collapsed into one.

**Tripwire.** Port loops as loops and functions as functions, with the
original's names, before simplifying anything. If the original defers an
effect — collects work and runs it later — the deferral is a rule, not a
style: write it down as one and find the input that observes it. Two
`return`s in the original are two returns here, even when they carry the
same value, because what differs may be what happens on the way out. When
you catch yourself writing a comment that explains why the original's extra
structure is unnecessary here, that comment is the finding (L28); the
structure is load-bearing until an input proves otherwise, and the proof is
a fixture, not an argument.

### L49 — A builtin's implementation exceeds its definition
*instances: 9*

When a port hand-writes one of the original's BUILTINS, it implements the
author's model of that builtin — the one-line definition anyone would give
— and CPython's builtins routinely do more than their definition says.
`sum` is not a left fold. `sorted(reverse=True)` does not reverse ties.
`max` and `min` disagree with IEEE on NaN. `int()` walks every Unicode
decimal digit, not `0-9`. None of these are documented at the call site,
none are visible in the source being ported, and every one of them is the
kind of thing a reviewer reads past because the Go line says exactly what
the Python line says.

This is L46 inverted. There the divergence comes from reaching for the
host's library instead of porting; here it comes from NOT reaching for a
library and porting a definition instead of an implementation. Both end in
the same place — a set of rules the author enumerated standing in for a set
nobody did — and this one is worse camouflaged, because the hand-written
version is the "careful port" the reviewer was hoping to see.

**Canonical instance.** `analyze_step_costs` sums `cost_usd` over the
entries. The port accumulated with `+=` in a loop; CPython's `sum()` has
used Neumaier compensated summation since 3.12, so it is not that loop.
`sum([0.05, 0.01, 0.01, -0.07])` — four ordinary cost rows off a real
ledger — is `-3.469446951953614e-18` in CPython and `0.0` under a fold,
and `round(x, 6)` spells those `-0.0` and `0.0`. Two runtimes writing
different bytes into one shared store for the same four rows. Nothing in
metrics.py hints at this; the only way to find it was to run the fixture.

The near-miss is worth recording: the divergence surfaced because a NEW
fixture, added for an unrelated finding (floor division on a negative
total), happened to sum to approximately zero. Had its four costs summed
to anything else, the port would have shipped a folded `sum` and the
catalog would have no L49.

**A second instance, from the same chunk's battery.** `successful_run_cost_p90`
tests `card.get("success_class") in RUN_COST_SUCCESS_CLASSES`. The port read
`in` as SET membership, guarded it, and raised TypeError on an unhashable
class. `RUN_COST_SUCCESS_CLASSES` is a **tuple**: membership walks the
elements comparing with `==`, never hashes, and never raises. One structured
`success_class` in the last two hundred run cards made the port answer "no
opinion" where CPython answers a p90, dropping the budget gate to its static
floors — silently, because "no opinion" is a legitimate answer.

The operator was not the problem; the CONTAINER was, and the container was
one line away in the same module. What made it survive review is that the
port had a genuine instance of the set reading twenty lines off in the same
package — `analyze_step_costs` uses `step_type` as a dict key, where the
raise is real — so the wrong rule arrived with a correct sibling vouching for
it. **`in`, `[]`, `+`, `*` and `%` all mean different things by operand type;
resolving one call site does not resolve the next one.**

**Tripwire.** List every Python BUILTIN the chunk reimplements — `sum`,
`sorted`, `min`, `max`, `round`, `int`, `divmod`, `any`, `all` — and for
each one write the differential BEFORE the implementation, with at least
one input chosen to be ill-conditioned for the obvious algorithm: a
cancelling pair for a sum, a tie for a sort, a NaN for a comparison, a
half-way value for a round. "I know what sum does" is the claim being
tested, and it is the claim that has never once survived contact.

### L50 — A sort is only as faithful as the order of its input
*instances: 1*

`sorted(X, key=k)` is two decisions, not one: the key, and the order X
already had. Reviewers check the key. The port gets X from whichever Go
call "obviously" does the same thing as the Python one — and the
difference between *returns the entries* and *returns the entries sorted*
is not in either name.

Python's listing calls do NOT sort. `os.listdir`, `os.scandir`,
`Path.iterdir` and `Path.glob` all yield readdir order. Go's do:
`filepath.Glob` sorts, `os.ReadDir` sorts by filename. So a port that
reads `sorted(dir.glob("*"), key=mtime, reverse=True)` and writes
`filepath.Glob` + a stable sort has faithfully reproduced the sort and
silently replaced its input — invisible until two keys tie, and then the
two runtimes name different files.

This is distinct from P11, which is about the FIXTURE (how many elements,
what shape) needed to expose an unstable sort. Here the sort is correct
and stable; what diverged is what it was handed.

**Canonical instance.** `sheriff.check_project` picks the newest artifact
with `sorted(artifacts_dir.glob("*"), key=lambda p: p.stat().st_mtime,
reverse=True)[0]`. The port used `filepath.Glob` + `sort.SliceStable`.
Measured on this box: two files written as `aaa.txt` then `bbb.txt` come
back from readdir as **`bbb.txt`, `aaa.txt`** — ext4 orders by name hash,
not by name and not by creation. With equal mtimes CPython reports
`bbb.txt` and the port reported `aaa.txt`. Fixed by reading the directory
raw (`f.Readdirnames(-1)`).

The fix is also what makes the differential *stable* rather than lucky.
Sorting made the Go answer deterministic and the CPython answer
filesystem-dependent — two rules that agree nowhere in particular. Reading
raw makes both runtimes ask the same filesystem the same question, so the
fixture passes on ext4 (hash order) and on a tmpfs `/tmp` (insertion
order) alike, without either being written into the test.

**The same file argues both ways, twenty lines apart.**
`project_activity_age_days` does `sorted(artifacts.iterdir())[:50]` —
explicitly sorted, by path. `check_project` does not. A port that unified
the two listings behind one helper would necessarily get one of them
wrong, and would look tidier for it.

**Tripwire.** For every `sorted(...)`/`min`/`max`/`[0]` in the chunk, name
the order of the argument and say which Go call promises it. If the key
can tie — mtimes, counts, scores, anything quantised — the tie fixture is
required (P11), and it is the ONLY thing that can catch this.

### L51 — A differential that normalises before comparing has moved the assertion into the normaliser
*instances: 6*

**Instance 6 is the one no fixture can reach** (syshealth r3, 2026-08-26).
The first five erased a value or a question on ONE side. This one erased a
distinction **symmetrically**: `canon` rendered every `json.Number` through
`Float64()`, so two integers differing only past 2^53 compared equal — and
because the normaliser runs over BOTH sides, applying it to two agreeing
sides leaves them agreeing. Adding a big-number fixture does nothing; the
fixtures added alongside the fix passed against the broken normaliser.

> **A normaliser needs its own test, and it needs both halves: what it MUST
> equate, and what it must NOT.** Nothing downstream of it can supply
> either.

The must-not half is the one that gets forgotten, and it is where the
regression lives. Watch also for the fix that trades one blindness for
another — canonicalising numbers to bare text would have made the number `1`
and the string `"1"` compare equal, on a record whose `status`, `evidence`
and `narrated` fields all hold strings that can be digits. The fix carries a
`"num:"` prefix for exactly that reason.

A cross-runtime differential almost always has a shim between the two
answers: something that turns the port's native shapes into whatever the
comparison eats. That shim is production-adjacent code that nothing tests,
and every simplification it makes is a divergence the differential can no
longer see.

The failure is silent in the worst way. The suite is green, the fixture
count is high, and the specific distinction the shim erased is exactly the
one a reader would assume the differential covers.

**Instance 1 (`sheriff`, M76).** The test's `cpOut` copied `Report.Evidence`
into a fresh `[]any`. A nil slice marshals to `null` and an empty one to
`[]` — CPython's distinction between "no evidence key" and "evidence: []" —
and the copy made them the same. `evidence = []string{}` could be deleted
from the port with every fixture still passing.

**Instance 2 (`syshealth`, M1/M17).** `syGo` turned a nil `pyval.List` into
`make([]any, 0)`, so `var sil pyval.List` in `Summary.ToDict` — which would
write `"silent": null` where CPython always writes `[]` — survived the whole
first battery round.

**Instances 3–5 (`syshealth` r1) generalise it past normalisers.** The
anti-vacuity gate `wroteFile < 20` counted fixtures whose `file` field was
a *string* — which every fixture that SEEDED a file satisfies without any
write happening; deleting the write from the cycle left the gate silent at
31 while 31 per-case diffs failed. The same harness spelled "this fixture
does not patch config" as `enabled == nil`, which is also how you spell
"config returned None", so a real config lane had no expressible fixture.
And `syGo`'s route through a Go map left the summary's key order
unasserted, which let the production doc and the battery's own M6 label
carry OPPOSITE contracts for a full round with nothing able to adjudicate.

None of the three is a normaliser erasing a value. Two are the harness
collapsing two meanings into one field and then asking that field a
question it can no longer answer; the third is a collapse that made a
disagreement invisible rather than a difference.

All five are the same shape: **the harness rounded off a distinction the
code under test is responsible for making** — sometimes on the way to the
comparison, sometimes on the way in, and sometimes taking a whole contract
out of scope with it.

**Tripwire.** For every conversion the harness performs — and every field
it reuses for two purposes, and every count it derives — name what it
collapses, then ask whether anything still asserts the collapsed thing.
For conversions specifically, ask whether the port is allowed to collapse
the same thing.
The three that recur: nil vs empty (`null` vs `[]`/`{}`), int vs float
(`1` vs `1.0`), and key order. A collapse is fine only where BOTH runtimes
are free — semantic key order in a dict both sides build — and never where
one of them commits to an answer in a file.

**Corollary — a claim about what a test can catch is itself testable.**
Twice now a comment asserted that some guard was the only thing standing
between the port and a bug, without anyone running the experiment. The
`syshealth` slice-header note said "the differential's own fixtures cannot
see it"; hand-editing a copy showed all thirty-seven cycle fixtures fail at
once. Same defect class as the `pySeconds` rationale retracted one tranche
earlier: **a plausible claim nobody measured is a test that cannot fail,
written in prose.** Mutate the copy and find out; it costs one minute.

### L52 — A rationale recorded as deliberate is still a claim

*instances: 15*

A comment that says "deliberately NOT ported, named so the next reader
knows it was a decision" reads as settled. It is not evidence. It is an
assertion about behaviour, and it decays — or is wrong at birth — exactly
the way L28's coverage counts do, with one difference that makes it
harder: a stale enumeration is falsified by READING the file, while a
stale rationale is falsified only by RUNNING something.

**Instance 1** (paths, 2026-08-26). `config.Workspace()` skipped the
`.resolve()` half of `Path(val).expanduser().resolve()`, under a comment
giving two grounds. Both were false. "It only changes an answer when a
caller chdirs between two resolutions" — no: resolve absolutizes a
relative path, follows symlinks, and pops `..` against the followed path,
all on the first call. "Resolving would make Go disagree with Python
about which path STRING a probe asserts" — backwards, since Python
resolves.

What made it survive a review round is the part worth keeping: the
comment was specific, cited the correct standing rule
(`feedback_live_store_probes`), and named itself as a decision. It had no
fixture, and it could not have had a failing one, because every test in
the suite set `MARO_WORKSPACE` to a `t.TempDir()` path — absolute,
symlink-free, already clean. On that side of the input space, the two
candidate behaviours give the same answer.

**Instance 2** (artifactcheck slice 1, 2026-08-26) — the same shape one
notch sharper, because the comment does not say "this does not matter",
it says *"fixture E34 pins this"*. Two of them did. `extRE`'s trailing
`\n?` (Python's `$` also matches before a final newline) named E34; E34's
newline never reaches the token, because the tokeniser has already split
on whitespace by then. `pyBasename` implied a reachable divergence from
`filepath.Base`; there is none. Both mutants survived the whole suite,
which is what a named-but-absent pin looks like from the outside.

A comment naming a specific fixture is the most credible form this
failure takes, and the cheapest to check: the fixture is right there.
Nobody checked, twice, in a file written that hour.

**The check.** For any comment asserting that a difference does not
matter, ask: *what input would tell the two behaviours apart, and does
the corpus contain one?* If the answer to the second half is no, the
claim is untested regardless of how carefully it is worded. This is the
same move as L51's ("what distinction is the normaliser erasing"), one
level up — there the assertion had moved into a helper, here it has moved
into prose.

**Corollary, from the same chunk.** A green suite over production data is
evidence about the corpus at least as much as about the code. The
both-engines comparison reported six byte-identical rows; the seventh
differed, and chasing it showed the live workspace held `count=2`, a
value that renders identically as a Python int and a Go float64. A
`count` of 1000000 would have differed on the same line the same day.

### L53 — A correct assertion can still cover the wrong blast radius

*instances: 5*

The ordinary missing-test finding is "nobody wrote one". This is not
that. The assertion existed, it was correct, it ran on every suite, and
it was green while the property it asserts was reverted three packages
over.

**Instance 1** (memory-dir chunk, 2026-08-26). `config.memory_dir()`
mkdirs, so every Go site standing for a Python `memory_dir() / name` line
must too. The differential was written in `internal/orch`, where
measuring CPython was convenient — four workspace shapes, mode compared
against CPython's own answer, an anti-vacuity gate. It caught its own
package's mutants (MD-1 through MD-4, all four). Mutants that reverted
`introspect.EventsPath` and `metrics.StepCostsPath` to pure joins
SURVIVED, with that differential green the entire time.

**Why it is a distinct failure from L28.** A stale enumeration is wrong
about a number. This is a test that is right about everything it says and
silent about two thirds of where it applies. Reading it tells you
nothing — it looks exactly like adequate coverage, because for its own
package it is.

**The check.** When a property is implemented by a shared helper and
reused across packages, ask *which package would fail if this were
reverted?* If the answer is only the one where the differential lives,
the other call sites are unguarded. The fix is not to move the
differential — CPython should be measured once, at the helper. It is a
cheap structural pin per package that asserts the site still routes
through the helper, with the measurement left where it is.

**Instance 2** (syshealth r4, 2026-08-26). `Summary.Error` used `""` as
its sentinel, so an exception raised with no message — CPython emits
`{"ran": 0, ..., "error": ""}` — lost the key entirely, making an aborted
cycle indistinguishable from a clean one. It landed inside the ONE
structure the chunk had pinned deliberately:
`TestSummaryToDictKeepsCPythonsInsertionOrder` asserts the exact
insertion-order contract across four shapes, and not one of the four was
an error with an empty message. The test is right about everything it
says.

**Instance 3** (projects slice, 2026-08-26). Two mutation survivors were
defects in a differential written minutes earlier: one ran both
enumerators against a single workspace and asserted the directory existed
at the end, so the *first* enumerator's side effect satisfied the
*second* one's assertion; the other copied the property from a sibling
differential without copying the mode assertion that makes it
observable. A side-effect test where something else performs the side
effect first is measuring nothing.

**Instance 4** (syshealth r5, 2026-08-26) — the sharpest one, and it is
about the fix for instance 2's own round. r4 changed `Declaration.Probe`
to take `prior` by POINTER and pinned it with a two-case table built to
separate the halves a value receiver splits. The new-key case was real.
The existing-key case asserted that the entry's `status` was the probe's
VERDICT — which the cycle sets unconditionally one line later, from its
own return value. The assertion holds for every implementation, including
one handed a defensive copy where the prior channel does not exist at
all; the reviewer built exactly that and watched the case pass.

Instances 1 and 3 were assertions in the wrong PACKAGE. This one is in
the right package, in the right test, in the case written for the
purpose, under a comment explaining what it proves. The comment is what
makes it worse: a reader finds the property named and the case present
and stops looking. Underneath it, three further reads of the same object
(`prev_status`, `_history_of(prior)`, `narrated` — all made AFTER the
probe returns) were unpinned, and hoisting any of them above the probe
call passed the entire suite.

**Instance 5** (artifactcheck slice 1, 2026-08-26) — the fixture-name
variant. `W9` is named "an mtime advance of exactly the epsilon is NOT a
change" and does not land on the epsilon: `2000.0 - (2000.0 - 1e-4)` is
not `1e-4` in float64, so it tests the under-side of the boundary twice
and the boundary never. Flipping the comparison from `>` to `>=` survived
the whole suite. A fixture NAME is a claim like any other.

**Tripwire.** Derive the battery's mutation list from the FILE SET the
change touched, not from the package the tests live in. That is what
surfaced instance 1: the mutants were written per changed file, so three
of them landed in packages the guard did not reach. For a *fixture*
table, the sibling tripwire is one setup per row — shared setup is how
one row's effect pays for another row's assertion.

**Second tripwire, from instances 4 and 5.** For any assertion whose
comment says what it PROVES, ask what else in the code path could make it
true. If the value being asserted is one the code under test writes
unconditionally on a later line, the assertion is about that line. The
mechanical version is cheaper and catches both: write the mutant that
reverts the property and run it. An assertion that has never failed is
not yet an assertion — and neither instance was found by reading.

### L54 — A test that COMPILES the artifact differently from every caller is testing a different artifact
*instances: 1*

**Canonical instance** (pytext, artifactcheck r2, 2026-08-26). `pytext`
carried two invariant tests written for exactly one purpose: to make it
impossible for the `\w` character class and the `IsWordChar` predicate to
drift apart. They passed. They kept passing while the class the CALLERS ran
disagreed with the predicate about U+0345 — because the tests compiled

```go
regexp.MustCompile(WordClass)
```

and every caller in the tree compiles

```go
regexp.MustCompile(`(?i)` + WordClass)
```

since the Python they port passes `re.IGNORECASE`. Go expands a class's
case-folds *before* negating it, and `\p{L}` under `(?i)` gains one
non-letter. Python's `re` does not fold classes at all. So the object under
test and the object in production were different objects, and the test that
existed to prevent this exact family could not see it.

This is not L1 (a test reporting agreement that tests nothing) — the test
DID measure something real. It is not L6 (sharing a fixture with the thing
it measures) — the fixture was the whole rune space. The defect is one step
earlier, in CONSTRUCTION: the artifact was built with different flags,
options, or wrappers than the callers build it with, and everything
downstream of that is measuring a sibling.

The tripwire is a question about the test's first line, not its assertions:

> **How do the callers construct this thing? Enumerate the flags, options
> and wrappers they pass. If the test does not pass the same ones, it is
> not testing the same object — and the flag the callers all pass and the
> test does not is where the bug will be.**

Corollaries seen in the same round:

- A grep is a fine way to answer it. `(?i)` appeared at every call site and
  nowhere in the two tests; that alone was the finding.
- Once found, the fix has two halves: correct the artifact, and add the
  construction the callers use to the invariant sweep. Only the second half
  stops it coming back.
- Where callers construct the thing THEMSELVES out of exported parts, no
  in-package test can cover it. A source scan can: the guard added here
  walks every `regexp.MustCompile` in the module and fails on one that
  combines `(?i)` with a raw spliced class body. It found three more sites
  in its first run, in three packages nobody had looked at.

### P13 — A `try` split across a seam stops being one `try`
*instances: 1 (`syshealth` r1)*

Extracting the middle of a function into another package silently splits
whatever error handler wrapped it. The extracted half can no longer observe
the failures of the halves left behind, and — this is the part that bites —
it will still happily assign the fields that the original only assigns
AFTER those halves succeed.

`run_health_probes` is one `try` around four things: read config, load the
snapshot, run the cycle, write the file. The port extracted the cycle,
left the config gate inside it, and assigned `transitions` there. CPython
assigns `transitions` after the write returns, so a failed write leaves it
0 and discards the pending narrations. The port reported them — and a
caller logging them would claim the user was told about a state that never
persisted, then re-decide the same transition every cycle forever, which is
exactly what the Python's own ordering comment says it exists to prevent.

It is invisible to a differential built the obvious way, because the
harness performs the missing half itself and can therefore never fail it.

**Tripwire.** At every extraction, ask: *what did the original's error
handler cover that my half does not?* Then, for each thing it covered,
which fields does the original assign only on the success path? Those
fields belong outside the extracted half, or the seam is drawn wrong.
The check is cheap and mechanical: read the `try`'s extent in the source,
not the function's.

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
*instances: 6*

Its restore set does not include test files, so a test-file edit mid-run
produces a spurious BUILDFAIL. Do not edit a battery's `FILES` while it runs.

**And "a different package" is not an exemption.** The 2026-08-26 argparse
round wrote a new file in `internal/metrics` while a battery ran over
`internal/introspect`, on the reasoning that the battery's `go test
./internal/introspect/` does not compile another package. It does:
`internal/introspect` IMPORTS `internal/metrics`. Twenty-eight consecutive
mutants reported BUILDFAIL — every one of which had been individually
DETECTED minutes earlier — because a half-written file three directories
away was in the same build graph. The whole run was wasted.

A Go module builds through its import graph, so the tree a battery owns is
every package the package under test can reach, transitively, plus every
package that reaches IT. That set is not something to estimate: while a
battery runs, the working tree is off limits, full stop. Prose is fine —
`docs/`, `review/`, `PORT.md` are not in any build graph.

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

**Better than the tripwire: run the battery against a COPY.** The rebuilt
metrics battery (2026-08-26) clones the worktree into the scratchpad and
mutates that, so the exclusivity problem does not exist — ordinary work
continues in the real tree while the battery runs, and a killed run cannot
leave a mutation on a file anyone else is editing. Every P4 instance above
is a symptom of mutating the tree you also work in. Retire the hazard
rather than documenting it; the sibling `bat_r4.py` had already been doing
this and the metrics battery had not.

**Sixth instance, and the one that shows why the copy is not optional: a
killed battery leaves the tree mutated, and the check that says otherwise
is usually written by hand.** The 2026-08-26 memory-dir battery was
SIGKILLed by a 2-minute harness timeout partway through MD-3, so its
`finally: F.write_text(src)` never ran and `MissionLogPath` stayed in its
mutated form — a pure join — in the live tree. The post-mortem was a
`git diff --stat` plus a grep, and it reported CLEAN, because it looked at
`paths.go`: the file the *first two* mutants touch, not the one the
interrupted mutant did. The mutation survived a relaunch and only surfaced
as a red baseline eight minutes later, one wasted run downstream.

Two separate failures, and the second is the transferable one:

1. The battery mutated the tree it lives in (P4's standing hazard).
2. **The restore check was not derived from the battery's own mutation
   table.** A hand-written verification checks the sites you remember. The
   battery already holds the exact list — enumerate it, assert each
   original site still appears exactly once, and compare a pre-run hash of
   every touched file. That check is four lines and cannot fall out of
   date with the mutations, because it reads the same table.

A `finally` is not a restore guarantee; it is a restore guarantee against
*exceptions*. Against SIGKILL there is no in-process answer, which is the
whole argument for mutating a copy.

### P7 — A battery that never proves its baseline reads a broken tree as a perfect score

*instances: 2*

A mutation battery reports DETECTED when the test suite fails with the
mutant applied. It does not ask *why* it failed. If the tree was ALREADY
failing before the mutant went on, every single mutant reports DETECTED and
the run prints a flawless 100% kill rate while measuring nothing at all.
The output of a perfect battery and the output of a broken one are
byte-identical.

**Canonical instance.** 2026-08-26, `internal/introspect/lens.go`. Two runs
of the lens battery were stopped with `kill`, and **Python's default SIGTERM
handler terminates the process outright — a `try/finally: restore()` does
not run**. Each kill left the in-flight mutant on disk; the next run's
`snapshot()` then copied that mutated file as its "clean" backup, and every
`restore()` afterwards wrote the mutation back. The battery reported 85 of
85 DETECTED. Adding a baseline gate turned the same battery into 68
DETECTED and 22 MISS — twenty-two real fixture gaps that the flawless run
had hidden.

**Two tripwires, and both are needed.**

1. Run the suite CLEAN before the first mutant and refuse to continue if it
   fails. One extra run per battery buys the only evidence that a kill means
   anything.
2. Install a SIGTERM/SIGINT handler that raises, so the restore actually
   happens. `finally` is not a promise the signal respects.

A third habit falls out of the same incident: **a suspiciously perfect
battery result deserves the same scepticism as a suspiciously perfect test
suite.** This is L1 wearing a different hat — the battery is a test of the
tests, and it agreed because nothing could disagree.

### P10 — A harness whose subject is an external process must prove it ran
*instances: 1*

Every differential in this port is `go test` plus a live CPython. When the
Python side cannot be found, `pyprobe.SrcDir` calls `t.Skipf` — the right
answer on a machine without the source tree, and catastrophic anywhere the
whole point is that the differentials RAN.

Measured, 2026-08-26: the SystemMetrics mutation battery copied the Go
module to a scratch directory (P4 — a Go module builds through its import
graph, so a battery owns the whole tree it runs in), one level deeper than
the previous battery's copy. `../../../src` no longer resolved, every
differential skipped, `go test` printed `ok`, and the battery reported **34
of 42 mutants surviving — including one the differential had CAUGHT live an
hour earlier.**

**The baseline-green gate does not catch this.** That gate (P7) asks whether
the suite passes before the first mutant. It did. The baseline was green
*and empty*. A green baseline proves the tests do not fail; it says nothing
about whether they ran.

The fix is a door, not discipline: `MARO_PYPROBE_REQUIRED=1` turns the skip
into a `t.Fatalf`, and every battery sets it. A caller whose result depends
on the differentials having run now says so, once, in one place — rather
than each battery author remembering to check a skip count they cannot see.

Generalised: **when a harness delegates its verdict to something outside the
process, "no failure" and "no verdict" look identical from the outside.**
Ask what the harness prints when the external thing is absent. If the answer
is the same string it prints on success, the harness has no verdict.

This SHARPENS an existing rule rather than repeating it. `go/PORT.md`'s
"a test named for a differential must RUN the other side" already says a
differential that skips when `python3` is missing is *fine* — and it is,
in ordinary use, which is why the skip survived. What that rule does not
say is that the same skip is lethal to any caller whose result is a
statement ABOUT the tests. The false-green family gains a fourth member
alongside *a fixture both sides refuse is not a differential*, *a
compile-kill is not a kill*, and *a test named for a differential must run
the other side*.

### P14 — A mutant that does not compile is reported as caught and proves nothing
*instances: 3 — one ledger row, but it covers six mutants across two batteries*

`go test` exits non-zero for a build failure exactly as it does for a failed
assertion, so a mutation battery that judges on the return code counts every
mutant it broke the build with as a mutant it detected. No test observed
those. This is the false-green family's *"a compile-kill is not a kill"*
(named in P10's discussion) promoted to its own rule, because it now has a
mechanism and a measurement rather than a caution.

**Measured, `syshealth` r4.** Adding one check for `[build failed]` in the
runner's output reclassified five mutants that had passed three rounds as
"caught" and had never once run: two named `Field` where `pyval.Field` was
meant, and three deleted a term from a condition, leaving a variable
declared-and-unused. Four were repairable and are now genuinely caught. The
fifth — deleting `!ok` from `if !ok || !pyval.Truthy(v)` — **survives** once
it compiles, because `Get` on an absent key returns nil and `Truthy(nil)` is
already false. Three rounds of green had been asserting the opposite about a
guard that turns out to be intent, not a decision.

**Measured again, `syshealth` r5** — and this time the battery's own
mutation SPEC was the bug, not a typo in it. Three mutants were meant to
hoist a read of `prior` above the probe call, to prove the port really
does read the object AFTER the probe has written to it. Each was written
as a DELETION of the read at its original site, which leaves the variable
undefined and kills the build. All three reported DID-NOT-COMPILE.

A hoist is not a deletion. It is two edits — introduce the read early,
and make the original site consume it — and the battery's record could
not express that, because a mutation was a single `(old, new)` pair. The
fix was to the data structure: a mutation is a LIST of edits. Run 2
caught all three, which is the finding the first run was reaching for and
could not state.

The shape is identical to a mutation site that matches zero times: the
battery reports on a mutant it never applied. Both are the harness scoring
its own failures as findings.

**Tripwire.** The runner must classify three outcomes, not two: caught,
survived, and *never ran* (build failure, setup failure, site match ≠ 1).
Grep the output for `[build failed]` / `[setup failed]` and report those as
battery bugs. Then, when repairing one, make the mutant consume what it
orphans (`_ = ok`, `(recovered && false)`) rather than deleting the term —
and re-check the verdict, because a compile-killed mutant has no history.

The r5 half of the tripwire is upstream of all that: **let a mutation be
a list of edits.** A one-pair `(old, new)` record can only express
substitutions, so every mutant whose property needs two coordinated edits
— a hoist, a move across a lock, a split of one statement into two —
arrives at the runner already broken, and is reported as a build failure
rather than as the battery bug it is.

### P12 — An expected value spelled with the thing under test is not an assertion
*instances: 3*

Two findings, one file, one battery round. The heartbeat log test asserted
`path != LogPath(ws)` — both sides call `LogPath`, so moving the log out of
`memory/` and renaming the file were BOTH invisible. The cooldown test
spelled every boundary as `DiagnosisCooldown`, so halving the constant
changed the expectation in lockstep and nothing failed.

This is not the same as P7's vacuous fixture. The fixtures here are real,
the code paths run, the values are compared. What is missing is an
INDEPENDENT statement of the answer: the test says "the function agrees
with itself", which it always will.

The tell is syntactic and cheap to grep for: **the identifier under test
appearing on the RIGHT of a comparison**, or a constant exported by the
package under test standing in for a number the specification fixes. The
fix is the literal — `30*time.Minute`, `filepath.Join(ws, "memory",
"heartbeat-log.jsonl")` — with the specification's own value in a comment
if it needs one.

Corollary for ports specifically: the literal must come from the ORIGINAL,
not from the port. A Go constant transcribed wrong from Python is exactly
the mistake this catches, and only if the test names Python's number.

### P11 — The tie fixture must have the shape the sort actually reorders
*instances: 1*

"A tie test needs more elements than the insertion-sort threshold" is half
the rule, and the missing half is the one that bites. MEASURED on Go 1.24,
`sort.Slice` against the stable answer:

	n:              4     12    13    16    40
	all equal:    keeps keeps keeps keeps keeps
	two groups:   keeps keeps SCRAM SCRAM SCRAM

An **all-equal** list never exposes instability at any size — pdqsort
detects the duplicates and takes an equal-partition path that happens to
preserve order. Only **two or more interleaved groups**, above the
twelve-element insertion-sort threshold, actually reorder.

Both drafts of the SystemMetrics tie fixture were wrong in this way: four
tied rows (below the threshold), then thirteen identical rows (above it, but
the wrong shape). Neither could fail. Round 4's still-open **M101** is the
same finding one chunk earlier, recorded then as "needs a many-ties fixture"
— which is exactly the fix that does not work.

The construction rule: **at least 13 elements, at least two distinct key
values, interleaved, and the assertion must read the order WITHIN one key
group.** Asserting the group boundaries only tests the comparator.

### P9 — The exception MESSAGE names which statement ran first
*instances: 1*

When two statements read two different fields and either can raise, the
error text says WHICH RAN FIRST. That makes statement order testable with
no value-level assertion at all — and statement order is observable
behaviour a port can get wrong while every value it returns is right.

**The construction rule.** To pin the order of lines A and B, build ONE
input where both fields are bad **in different ways**, so the two candidate
messages differ. Two fields bad the SAME way pin nothing:

```
Outcome(elapsed_ms="5", tokens_in="5")
  -> TypeError: unsupported operand type(s) for +: 'int' and 'str'
```

`sum(o.elapsed_ms ...)` and `sum(o.tokens_in ...)` are consecutive lines
and produce identical text, so a port that swapped them passes. Make them
differ and the fixture discriminates:

```
Outcome(elapsed_ms=None, tokens_in="5")
  -> ... for +: 'int' and 'NoneType'    (elapsed_ms won)
Outcome(goal=None,       tokens_in="5")
  -> ... for +: 'int' and 'str'         (tokens_in won, goal[:80] lost)
```

**The stronger form: the ENTRY POINT decides the message.** The same row,
`tokens_in="5"`, through two functions over the same data:

```
compute_metrics([o])              +: 'int' and 'str'                 (sum runs first)
identify_expensive_patterns([o])  '<' not supported: 'str' and 'int' (estimate_cost runs first)
```

Neither is `estimate_cost`'s own message in the first case. A port that
priced before averaging would return every correct value and every wrong
error.

**Canonical instance.** 2026-08-26, scoping metrics.py's SystemMetrics
half. Found before any code was written, which is the point — the port's
loop order was chosen from this rather than discovered by a later round.

**Where it applies.** Any ported function whose body is a sequence of
independent reads over one record: loaders, formatters, aggregators. Ask
which field it touches first, and whether you can prove it. If two
orderings give the same message, the fixture is coverage, not a
differential.

Sharpens L48 ("a port can get every DECISION right and still be wrong,
because the original's SHAPE is observable") from an observation into a
technique. Same family as P8: both are about WHEN something happens, not
what it computes.

### P8 — `open(path, "w")` truncates before the argument is evaluated
*instances: 1*

Python evaluates `open(BAT, "w")` — which **truncates the file to zero
bytes** — before it evaluates the expression being written. So this line:

```python
open(BAT, "w").write("\n".join(src[:start] + body + src[end + 1:]))
```

destroys the file when `end` is `None`, and the traceback names the
`TypeError` in the argument, not the deletion that already happened. The
error message points at the half of the statement that did nothing.

**Canonical instance.** 2026-08-26, the metrics mutation battery. A repair
script rebuilt the mutant list by locating the `M = [` block with a bracket
counter. The counter was wrong — mutant strings contain `[]byte` and
`map[string]any`, so the depth never returned to zero and `end` stayed
`None`. The write raised; the file was already empty. 139 mutants, gone.

The scratchpad is not under git, so there was no undo. What survived was
what had been written somewhere else: the run logs, which carry every
mutant's NAME and verdict. Those recovered the coverage map (129 of 139)
and none of the mutation strings — which had to be re-derived from the
files, which is L9's discipline anyway, so the rebuild was not wasted work.

**Three tripwires, in order of how much they would have helped.**

1. **Write to a temp and rename.** `open(tmp, "w")` then `os.replace(tmp,
   path)` is atomic and cannot destroy the original when the argument
   raises. Use it for every generated artifact, not just important ones —
   you find out which ones were important afterwards.
2. **Never parse a structure out of source text with a bracket counter.**
   The battery's own list is data; it should live in its own file that a
   rewrite can replace wholesale, not a region of a larger file that a
   rewrite has to find. The rebuilt battery splits the harness from
   `metrics_battery_mutants.py` for exactly this reason — a bad write of
   the list can no longer take the runner with it.
3. **An artifact worth an hour is worth a copy.** Anything in the
   scratchpad that took real work to build has no version control behind
   it. The logs saved this one by accident; that is not a plan.

The general shape, beyond Python: **a destructive operation that is
lexically part of a larger expression still happens when the rest of the
expression fails.** Truncation, `DELETE` before a failing `WHERE`, a
`shutil.rmtree` in a call whose other argument raises.

### P5 — Rounds converge to lows by round 3–4
*standing*

The fixpoint is real and it arrives. Rounds after that are cheap insurance,
not discovery.

### P6 — The round's HIGH lives inside the previous round's own fix
*instances: 3 attributed; ~25 in the mined cluster — the largest, and the reason P2 exists*

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
