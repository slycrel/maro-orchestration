---
status: record
---

# Chunk-3 Adversarial Review — decisions.jsonl writers (fe0072d)

Per-chunk review discipline (Jeremy's /goal 2026-07-21). Three Codex lenses
(`codex exec`, opposite-model rule) against the chunk-3 diff
(2f4913b..fe0072d), prompts = stated intent + lens + mapped brain principles
+ full diff + verify-against-tree instruction. Raw reviewer output
preserved at session scratchpad `adv-review-c3.JTkTWT/` (ephemeral; this
record is the durable summary).

## Intent

Give the decision journal its first runtime writers (executor DECISION
directive with uncompressed carry, scope proxy commitment at the creation
seam, SF-13 CLI pipe), consumer-first: liveness pinned end-to-end through
recall with no read-side mocks.

## Verdict: REJECT as reviewed — remediated same session

High-severity finding with full three-lens consensus (F1). All six deduped
findings verified against the tree before fixing: **6/6 real, 0
hallucinated** — third consecutive clean round (historical reviewer
hallucination rate 30–78%; the verify-against-the-actual-tree prompt line
plus principle files appears to be holding).

## Findings (deduped, severity order)

1. **[high — Skeptic+Architect+Minimalist, unanimous]** Executor decisions
   silently dropped on every parallel surface. The fan-out lived only in
   `_process_done_step` (sequential); `_run_parallel_batch`,
   `_run_parallel_path`, and the DAG walk build outcomes directly — a
   parallel step's `decisions` field reached the outcome dict and died
   there: no journal row, no shared-context carry, no thread-brain line.
   Principle: boundary-discipline / prove-it-works.
   **Fixed**: fan-out extracted to `loop_post_step.record_step_decisions`
   (single seam, never raises), called from the sequential path and both
   parallel outcome walks (post-join, single-threaded — no lock needed).
   Pins: `test_parallel_batch_indices.py::TestParallelDecisionFanOut`
   (batch + fan-out paths; blocked members excluded; distinct keys per
   batch member).
2. **[high — Skeptic]** `record_decision` used bare `open('a').write()` on
   what is now a multi-writer runtime ledger; `file_lock.py`'s own header
   documents that as unsafe past PIPE_BUF, and every sibling ledger uses
   `locked_append`. Principle: serialize-shared-state-mutations.
   **Fixed**: `locked_append` (same .lock as rewrite callers).
3. **[medium — all three]** The SF-13 CLI printed `recorded …` and exited 0
   even when the journal write failed (record_decision swallows write
   errors by design for loop callers). A decree pipe whose entire job is
   the write must fail closed. **Fixed**: `strict=` kwarg on
   `record_decision` (loop callers stay best-effort); CLI passes
   `strict=True`, prints to stderr and exits 1 on failure.
4. **[medium — all three, two halves]** (a) Scope-proxy decisions were
   written `domain=""` — blank-domain rows inject into EVERY project's
   recall, so a one-off interpretation ("finalize the existing branch")
   could surface as a Prior Decision in unrelated projects. (b) TF-IDF
   ranking searched only `decision + rationale`, but for the proxy writer
   the original goal's terms may live only in `goal_context` — undercutting
   "future runs of similar goals inherit it". **Fixed**: `goal_context`
   joined into the ranked text; `decision_domain` threaded
   handle → `generate_resolved_intent` → `generate_scope` →
   `record_decision` (project slug — SF-13 decrees still default blank =
   global, which is correct for decrees).
5. **[medium — Architect]** The uncompressed carry had no budget: every
   `decision:` key rendered verbatim into every later prompt, growing
   monotonically (20-step worst case ≈ 20KB per prompt). **Fixed**:
   chronological render (earliest decisions constrain the most downstream
   work) under a 2000-char budget; overflow collapses to an omission note —
   the journal and thread brain keep the full list. Pinned.
6. **[low — all three]** The max-2 cap sliced `[:2]` before validation, so
   malformed entries consumed the budget and a trailing valid decision was
   lost. **Fixed**: cap counts valid decisions; pin updated to the
   `[valid, junk, junk, valid, valid]` → 2-survivors shape.

## What Went Well

- No reviewer contested the consumer-first read-side wiring or the
  no-mock liveness pins — the record→recall→rendered-block round trip
  held up under attack.
- The never-perturb-the-loop guard structure (per-writer try/except) was
  endorsed as correct for loop callers; the CLI was the only seam where
  fail-open was wrong.
- Root-cause placement of the scope writer (creation seam, not closure)
  drew no findings.

## Lead Judgment

- F1 accept in full — the unanimous high; the live verification run
  happened to be sequential, which is exactly how this class of gap
  survives smoke tests.
- F2 accept — house convention existed and was skipped; no argument.
- F3 accept — Minimalist's framing ("the only outcome of the command IS
  the write") is the decisive version.
- F4 accept in part — the domain thread-through and search-text fix are
  both cheap and correct. The stronger Architect claim (a decision *kind*
  taxonomy with per-kind blast radii) is deferred: three writers do not
  yet justify a type system; revisit if a fourth writer lands.
- F5 accept — my own pin test had encoded the bug as intended behavior;
  the review caught the spec error, not just the code.
- F6 accept in part — budget cap shipped; the full lifecycle ask
  (supersede/dedup of decisions mid-run) is deferred as
  build-when-a-run-actually-hits-it.

Skeptic's sandbox could not run pytest (read-only mount) — noted, not
silently skipped; all six findings were re-verified here against the tree
and the affected suites run locally.
