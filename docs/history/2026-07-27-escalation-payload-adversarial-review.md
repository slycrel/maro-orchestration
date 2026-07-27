---
status: record
---

# §9.6 escalation payload — adversarial review (2026-07-27)

Post-land review of 839da56 (`docs/history/2026-07-27-escalation-payload.md`)
per the per-chunk discipline. Medium chunk (~155 src lines, 5 src files)
→ 2 Codex lenses (Skeptic + Architect) via `codex exec`, prompts built
from the skill's brain principles + lens definitions, diff 29f63f0..839da56.

## Intent

An escalation asks the human ONE decision about ONE chasm plus one line
of family-recurrence context (Jeremy's item-3 decision, "simple first").
Constraints: additive payload fields; pure/deterministic, no LLM/config/
new persistence; enrichment never breaks the emit; telegram leads with
the ask; legacy payloads render unchanged.

## Verdict: CONTESTED → remediated same session

3 distinct code findings, **3/3 verified real against the tree, 0
hallucinated** (fourteenth clean round). One HIGH (single-lens), two
mediums (both lenses independently). All accepted and fixed.

## Findings

1. **[high — Architect] `diagnose_loop` consult writes a captain's-log
   event.** CONFIRMED: `introspect.py` logs a DIAGNOSIS event for
   non-healthy classes, so payload enrichment had a persistence side
   effect — and could duplicate the metacognitive consult's event for
   the same loop. Violates the chunk's own "no new persistence"
   constraint (principle: boundary-discipline — a notification formatter
   must not produce diagnostic records). **Fix:** `emit_log_event: bool
   = True` param on `diagnose_loop`; the enrichment path passes False;
   all existing callers unchanged. Pinned by
   `test_diagnose_loop_log_event_suppressible` (which also documents
   that the artifact_missing early return never reached the log block).

2. **[medium — both lenses] Unreadable ledger rendered a confident false
   "first on record".** CONFIRMED: `load_diagnoses` swallows read
   failures into `[]`, which the first cut treated as "no prior rows";
   the shipped test monkeypatched a raise that the production loader
   never produces (prove-it-works violation — the test proved the wrong
   path). **Fix:** an existing non-empty ledger that yields nothing
   readable now renders silence; "first on record" only for a
   missing/empty ledger or a readable ledger with no matching class.
   Pinned with a real garbage-bytes ledger, no monkeypatch.

3. **[medium — both lenses] `limit=200` capped the count while the text
   claimed "on record".** CONFIRMED, and the cap bought nothing:
   `read_jsonl_tail` reads the whole file regardless (the limit only
   truncated dataclass construction). Live ledger is already 1418 rows,
   so family rows older than the newest-200 would re-render as false
   "first on record". **Fix:** count raw rows over the whole ledger via
   `read_jsonl_tail` directly; construction-failing rows still count
   (they are still on record). Pinned by a 230-noise-row test + a
   construction-failing-row test.

Also raised (Skeptic, low): the reviewer sandbox could not run pytest
(no usable tmp dir) so it could not independently execute the tests.
Noted for honesty; the box's full suite gates the land.

## Lead Judgment

- Accept 1: the constraint violated is the chunk's own stated contract;
  the enrichment path duplicating the metacognitive path's DIAGNOSIS
  event is real event-ledger pollution. Suppression flag over a pure
  extract-function refactor: minimal churn, callers unchanged.
- Accept 2: textbook prove-it-works miss — the test exercised a
  synthetic failure mode instead of the swallowing loader. The fix keeps
  "first occurrence is signal" while making silence the answer to
  can't-read.
- Accept 3: both lenses converged independently; the fix is simpler than
  the bug (the cap was doing nothing but lying).
- The low: environment limitation, not a code claim — no action beyond
  recording it.

Lesson (fits the running series): a helper that *reads through* another
module's fail-soft loader inherits its failure semantics — "returns []"
is three different truths (empty, absent, unreadable) and an honest
renderer must distinguish them.

## What went well

Reviewers found no issue with the decision-line templates/options, the
emit-site wiring shape (inner try/except, ask-before-diagnosis
ordering), the telegram precedence rules, or the additive-fields
contract. The never-breaks-the-emit constraint held under both lenses.
