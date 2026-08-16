---
status: record
---

# Adversarial review — mint-grounding claim-shape gate (af5ad47), 2026-08-16

Four reviewers, sonnet-medium fallback via `claude -p` (codex capped
until Aug 19), the newly-ratified Large roster: Skeptic, Architect,
Minimalist, expert QA.

## Intent

Slice 2b opened by measuring the seven skill-mint census sites' actual
corpus, which refuted the slice's premise; the commit under review ships
the prerequisite instead — a claim-shape gate that stops
`extract_claims` from firing on prescriptive text — plus the census tool
and the re-scoped slice plan.

## Verdict: CONTESTED → fixed to green same session

Two HIGHs, both verify-before-fix CONFIRMED by reproduction, both fixed.
One of them falsified a claim in the commit message itself, which is the
outcome the house-style review doctrine is aimed at. 0 hallucinated
findings (streak intact).

### 1. [HIGH, expert QA] Polarity — a stamp that lies

A retrospective marker says the sentence *reports*; it never said what it
reports. `The fetch was not authenticated.` extracted an `auth` claim and
`ground_text` stamped it **`supported` with a real receipt** off an
unrelated authenticated event. Same for `was not executed`, and for the
modal-perfect hedge `could have fetched` (which slipped `_MODAL_GOVERNS`
because its linking group lacked `have`, then matched `_RETRO_AUX`'s own
`have … been` arm). The commit's whole subject is false *doubt* — stamps
of "unsupported" on advice; this is the inverse and worse failure, a
false *affirmation* backed by genuine receipts. Predates the gate.
**Fix:** clause-local negation veto (`_NEGATOR_BEFORE` + `_clause_tail`)
and `have|has|had|having` in the modal veto. The window is clause-local
so that "did not produce uniform confidence: 12/14 ideas confirmed as
STRONG matches" — a true claim whose negation belongs to another clause
— keeps minting. Pinned both directions.

### 2. [HIGH, Minimalist; corroborated Skeptic + Architect] Closed verb list on an open class

`_is_instruction` vetoed orders only when the first word sat in a
hand-curated ~90-verb list, so every verb nobody enumerated walked
through: `download` (the actual, undiagnosed cause of the census's one
surviving skills.jsonl hit — "Download a source document … and confirm it
was retrieved in full"), `draft`, `install`, plus Skeptic's sequencing
evasion ("**Then** record the date each price was checked") and
Architect's subordinator evasion ("Retry the fetch **until** the page was
fetched successfully"). **Fix:** the primary net is now
vocabulary-independent — a retro marker inside a subordinate clause
(complementizer within two words, or a subordinating conjunction anywhere
in the clause) is not the sentence's own report, which kills
"confirm it was retrieved", "log which values were tested", "record the
date each price was checked" and "until the page was fetched" without
knowing a single verb. The list stays as a backstop (widened, with
sequencer stripping and a subject guard so "Build 42 was verified" still
reports). Must-detect fixtures now probe verbs deliberately outside the
list, per watch-list #7.

### 3. [HIGH, Skeptic] "All nine already-stamped rows survive" was false

Row `1f702cc1`'s stamped claim sits in "…existed and needed **to be**
checked", which the modal veto refuses — correctly, it is policy
language. The honest count as landed was **8/9**, not 9/9: the claim came
from reading a total (103→24) rather than diffing per row. After the
round-1 fixes it is 6/9; the two further drops are a negated claim and an
absence claim, both of which should stop minting. **Fix:**
`scripts/mint_grounding_census.py --recheck` re-extracts every stored
stamp's claim and lists what no longer mints, so the audit is an
instrument rather than an eyeball; the three drop shapes are pinned
verbatim with their rationale, and the design doc's numbers are
corrected.

### Accepted, labeled, not fixed

- **[MEDIUM, Skeptic] Present/past homograph openers** (read, set, split,
  put, cut, hit) are read as orders, so "Read the source and confirmed
  the total matched" mints nothing. Structural English morphology, not
  corpus luck. Dropping is the narrowing direction, and removing those
  words would re-admit the far more common "Read the file and confirm it
  was retrieved". Pinned as a known-gap test and named in the module's
  residue list — a future fix now has a failing target.
- **[MEDIUM, Architect] The census measures persisted store text, not the
  writer sites' pre-persistence candidate text.** True; the two are the
  same string for every lane that stamps at mint, and slice 2b is
  deferred whole, so the gap has no live consumer. Recorded.

### Reviewer-process findings

- **[Skeptic, finding 0] The tree was dirty under the re-run.** Two
  lenses returned only a closing summary on the first pass (their
  numbered findings lived in intermediate turns that `claude -p` does not
  capture), so they were re-run with an explicit "your final message IS
  the deliverable" instruction — by which time round-1 fixes were already
  in the working tree, and the Skeptic's early probes read WIP code
  before it isolated af5ad47 in a scratch worktree. It caught this
  itself and re-probed cleanly. Two durable lessons: put the
  final-message instruction in the reviewer prompt template from the
  start, and do not start fixing while a reviewer's clock is still
  running.

## What went well (reviewers found no issue)

Every call site inherits the gate through the single `extract_claims` →
`ground_text` chokepoint — Architect censused the writers (memory,
world_facts, prereq, knowledge_bridge, thinkback, loop_finalize) and
found no missed sibling. The subset property (the gate only ever removes
matches) held under adversarial probing by two lenses. C1, C2 and C4
reproduced exactly against the isolated commit, including the census
counts and the red-then-green stash check.

## Probes

Round-1 fixes carry 23 new pins across `TestClaimShapeGate` and
`TestGatePolarityAndVocabulary`; 18 of them verified red against the
landed module before the fix. Suite 9010 / 0 skipped.
