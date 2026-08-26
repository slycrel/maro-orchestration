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
- *Recording:* each round's findings go into
  `memory/review_findings.jsonl` via `scripts/review-ledger.py` (see
  "The ledger" below) so the recurrence counts here can be re-derived from
  data rather than from memory.

**Status legend on each lens:** `instances` is how many independent
findings have been attributed to it so far. These counts start from the Go
port's r1–r4 rounds and the mutation batteries; they are lower bounds.

---

## A. Tests that agree instead of measuring

The largest family by a wide margin. Every one of these produces a GREEN
test suite that is evidence of nothing.

### L1 — A test reporting AGREEMENT may be testing nothing
*instances: many (the family's root)*

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
*instances: 6+*

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
*instances: 3*

The self-referential form of L2.

**Canonical instance.** `TestLoadOutcomesMatchesCPython` spells the six
required `Outcome` fields out as a literal rather than calling
`outcomeRequiredFields` — because whether that list is right is the test's
whole subject.

### L4 — A guard that cannot fire is not evidence the danger is gone
*instances: 4*

**Canonical instance.** Deleting the `is_error` check from
`classify_tool_pathologies`' hallucination scan survived a 37-mutant
battery, because every fixture carrying the phrase `No such tool available`
also carried `is_error: true`.

**Tripwire.** For every gate, ask what fixture makes it *matter*. If none
exists, the gate is decoration.

### L5 — A duplicated guard is what makes a wrong bound unobservable
*instances: 3*

Two checks for the same thing; break one and nothing fails.

**Canonical instance (test-side).** The introspect differential normalized
nil-vs-empty away with `if len(want) == 0 && len(got) == 0 { return }`,
carrying a comment defending it. `json.dumps([])` is `"[]"` and a nil Go
slice marshals to `null`, and that list gets written to a shared store. The
normalization was the only reason a "return nil for a clean transcript"
mutant survived.

### L6 — A test that shares a fixture with the thing it measures against is measuring the fixture
*instances: 2*

### L7 — A detector that cannot see the case you already have is agreeing, not measuring
*instances: 2*

### L8 — A mutant that cannot change an answer is a bad mutant, not a test gap
*instances: 4*

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
*instances: 3*

**Canonical instance.** Python's `_rows_as` is TWO readers stacked: an
announced framing read AND a dataclass construction that EXCLUDES rows and
counts them as schema drift. The Go port had only the framing half at both
`load_outcomes` and `load_suggestions`. Consequence: the evolver minted a
cycle off three rows CPython excludes, and `get_suggestion` handed the
auto-revert guard `applied_manually=false` for a row a human had applied.

**Tripwire.** When porting a function that returns typed objects, ask what
happens to a row the constructor rejects. Silence is the wrong answer.

### L13 — A fix at the site that has the fixture is not a fix for the class
*instances: 3*

**Canonical instance.** `dailylog.go` already carried the outcome schema
filter, measured and correct, with a comment quoting CPython's own warning —
applied one layer too low, with the reasoning "LoadOutcomes' tolerance is
right for its other consumers" written down. That reasoning was wrong, and
the comment recording it had to be corrected in place.

### L14 — A helper you did not look for is a helper you will write again
*instances: 7*

**Canonical instance.** `pyval.Clip` is the shared Python-semantics rune
slicer. Six packages carry a private `clipRunes` copy (`scans`,
`graduation`, `playbook`, `evolver`, `skills/utility`, `director`). The
introspect port deliberately did not become the seventh.

### L15 — A helper that fixes a class does not fix the class — it fixes the callers that reach it
*instances: 2*

### L16 — A field is TWO claims (the writer's and the reader's)
*instances: 3*

---

## C. Types and values arriving differently than they left

### L17 — A fixture built from LITERALS arrives with types the reader never produces
*instances: 3*

**Canonical instance.** `TestContentKeyCoercesLikePython` passed Go-native
`int 0` / `float64 1.5` where the disk reader yields `json.Number`.
Fixtures for a reader must be LINES, not literals.

### L18 — A value arrives with a type, and something reads the type away
*instances: 4*

**Canonical instance.** Switching to an announced ordered read made numbers
`json.Number`, so `intOf`'s `float64` arm stopped matching and a human
surface rendered "Total tokens: 0".

### L19 — A zero value that must mean two things means neither
*instances: 2*

**Canonical instance.** `load_outcomes(limit=0)` in Python returns NOTHING
(`[:0]`). The port reads `limit <= 0` as "everything" — a deliberate
divergence, now pinned by a named-divergence test rather than left implicit.

### L20 — Python's operators are not Go's
*instances: 5*

Truthiness vs `== true`; identity deciding a dict lookup; `str()` vs
`repr()` agreeing on `None` and disagreeing on everything else; `%` on
negatives.

**Canonical instance.** `te.get("is_error")` is TRUTHINESS. A stamp of the
string `"no"` is truthy in Python; a port reading it through `v.(bool)`
calls the transcript clean.

### L21 — A generic instantiated at `any` is a different constraint
*instances: 1*

### L22 — An exception's class is part of its behaviour
*instances: 2*

---

## D. Boundaries and limits

### L23 — A limit with no case at its OWN boundary is a limit nothing pins
*instances: 5*

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
*instances: 3*

### L25 — A skip by name is a claim you never re-examine
*instances: 2*

---

## E. Prose, names and identity

### L26 — Content-key PROSE divergence
*instances: 8 — the most frequent single defect in the Go port*

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
*instances: 4*

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
*instances: 1*

A distinction nothing reads is a second guard making the first unobservable.

---

## F. Process patterns (not about code)

### P1 — Verify each finding's code claim before fixing
*standing; measured ~30–50% of adversarial findings are hallucinated*

### P2 — Later rounds review the WHOLE chunk + fixes, not the latest diff
*standing (Jeremy, 2026-08-22)*

Granular per-diff review produces granular fixes; whole-chunk review catches
the split-control-flow seam class.

### P3 — Escalate reviewer tier after round 1 on same-model fallback
*standing (Jeremy, 2026-08-22)*

Don't grind many rounds at the cheapest tier.

### P4 — A battery that snapshots production files clobbers concurrent edits
*instances: 2*

Its restore set does not include test files, so a test-file edit mid-run
produces a spurious BUILDFAIL. Do not edit a battery's `FILES` while it runs.

### P5 — Rounds converge to lows by round 3–4
*standing*

The fixpoint is real and it arrives. Rounds after that are cheap insurance,
not discovery.

---

## The ledger

`scripts/review-ledger.py` appends one row per finding to
`memory/review_findings.jsonl` so the recurrence counts above stop being
recalled and start being computed:

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

Fields per row: `arc`, `round`, `target`, `reviewer`, `severity`, `lens`,
`verdict` (`confirmed` | `hallucinated` | `known-gap` | `wontfix`),
`fix_site` (`production` | `test` | `battery` | `doc` | `none`),
`summary`, `recorded_at`.

Two numbers make the loop tighter, and both need this data:

1. **Per-reviewer hallucination rate** — P1's ~30–50% is a remembered
   figure, not a measured one. If it varies by tier or by target, the
   escalation rule (P3) should be keyed off that rather than off round
   number.
2. **Lens recurrence by arc** — a lens that keeps firing in one subsystem
   is a structural problem in that subsystem, not a review problem. The
   `clipRunes` tally (L14, six copies) is the shape: once counted, the fix
   stops being "look harder next time" and becomes one commit.
