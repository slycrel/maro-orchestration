---
status: record
---

# Adversarial review — persist-the-artifacts chunk (b4a1828)

Post-land review per chunk discipline. 3 Codex lenses via `codex exec`
over the chunk diff (10 files, 300 insertions). Findings verified
against the tree before acting: **4/4 distinct findings verified real,
0 hallucinated** — twelfth clean round.

## Intent

Jeremy's persist-the-artifacts decree ("fix the lineage, not the
wording"): durable per-loop lineage in run metadata (`loops[]`) and
full closure evidence per attempt (`build/closure_verdicts.jsonl`),
best-effort, append-only, for debugging and for showing a doubting
user the path a run took.

## Verdict: CONTESTED → remediated same session

No high-severity findings, but two two-lens consensus mediums that cut
directly against the chunk's own stated purpose. Three fixed, one
accepted-as-noted with a BACKLOG rider.

## Findings

1. **[medium — skeptic + architect consensus] Skip paths persisted
   nothing.** The JSONL writer ran only on the full parsed-verdict
   path; `no_checks_generated`, `no_check_results`,
   `verdict_parse_failed`, and the exception fallback returned `_null`
   with a captain's-log skip event but no run-dir row — so "closure
   never ran" and "closure ran and produced nothing" were
   indistinguishable from the run dir alone. **Fixed:** persistence
   factored into `_persist_verdict_row()`; `_emit_skip` now writes a
   `{skipped: <reason>, skip_detail}` row through it. Dry-run/no-adapter
   still writes nothing (intentional skip, same rule as the log event).
   Test: `test_skip_paths_persist_row`.

2. **[medium — skeptic + architect consensus] Rows dropped
   `target_file_content`.** Failed static checks attach ground-truth
   file excerpts that the verdict LLM actually judges — the exact
   "show why the verdict went this way" material — and the row builder
   silently omitted it. **Fixed:** rows now carry
   `target_file_content` (bounded [:2000]) when present. Test:
   `test_persisted_row_scrubs_secrets_and_keeps_file_evidence`.

3. **[medium — minimalist] Evidence persisted unscrubbed.** Every
   other persisted run record (call records, env/config snapshots)
   passes through `secret_scrub.scrub()`; the new JSONL wrote raw
   stdout/stderr. An LLM-generated check like `env | grep TOKEN` would
   have persisted token-shaped output into a file whose stated purpose
   includes being shown to users. **Fixed:** the whole row is scrubbed
   in `_persist_verdict_row()`. Same test pins `[REDACTED]` present /
   raw secret absent.

4. **[medium — minimalist] Queued continuations persist neither
   artifact.** `handle_queue` deliberately enters `scoped_run_dir(None)`
   (stale-run-dir hygiene), so the budget-ceiling `loop_continuation`
   lane has no run dir for either writer. **Accepted-as-noted:** this
   is the pre-existing "continuation lane lacks the shared run/closure
   lifecycle" gap already in BACKLOG (2026-07-14 finding); both new
   writers key off `current_run_dir()` and start working there the
   moment that lifecycle lands. BACKLOG rider added; building a run-dir
   lifecycle for that lane is the existing item's scope, not this
   chunk's.

## What Went Well

All three reviewers verified the core mechanics clean: both writers
best-effort (persistence exceptions cannot affect loop or verdict
paths), `locked_rmw`/`locked_append` serialization with parent-dir
creation, `write_metadata` preserving the `loops` key across refreshes,
and bounded row sizes. No reviewer challenged the design (append-only
JSONL in the run dir, no config keys).

## Lead Judgment

- Findings 1–3: **accept.** All three are the chunk failing its own
  decree — a persistence chunk that loses skip outcomes, drops the
  judged evidence, or leaks secrets into a user-facing artifact is
  lip service with extra steps. The scrub finding is the sharpest
  catch: the precedent existed twenty lines from code I read today.
- Finding 4: **accept the observation, reject the scope.** The lane
  detaches from run dirs on purpose for a different reason; giving it
  the shared lifecycle is an already-named larger item. The rider
  records that the fix is free once that lands.
