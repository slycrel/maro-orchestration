---
status: record
---

# Worker recall slice A/B — batches 1+2, POOLED VERDICT (2026-07-08)

**TL;DR (n=8 per arm): every measure favors the slice or ties; nothing favors
off. Closure 8/8 vs 7/8, blocked workers 0 vs 1, median tokens-in −29%, median
wall time −17%. Recommendation: flip `memory.worker_slice` ON (Jeremy's
call).** Pooled analysis in the "Batch 2 + pooled" section below; batch-1
record kept as written.

---

# Batch 1 (original record)

Brief §7 experiment, v0 (global-scope slice; thread plumbing intentionally
unwired). Runner: `scripts/worker_slice_ab.py`; raw rows in
`worker_slice_ab.jsonl`; injection evidence: 4/4 B-arm workers got
WORKER_SLICE_INJECTED (5 items, 768–1052 chars each).

| mission | arm | status | elapsed | tok_in | tok_out | workers |
|---|---|---|---|---|---|---|
| m1-polymarket | A-off | **stuck** | 711s | 201k | 43k | blocked + done |
| m1-polymarket | B-on | done | 622s | 211k | 38k | done + done (slice) |
| m2-ops-review | A-off | done | 682s | 918k | 47k | done + done |
| m2-ops-review | B-on | done | 432s | 569k | 27k | done + done (slice) |

## Read

- **Closure:** the one status difference favors the slice (m1: A stuck on a
  blocked worker, B done). Strongest signal in the batch.
- **Tokens:** m2's big delta (918k→569k in) is CONFOUNDED — the A arm burned
  2 director review-loop exhaustions vs B's 1; review loops dominate token
  spend. Do not read the delta as slice savings.
- **Mechanism:** injection works live end to end; blocks capped correctly;
  no errors on the off path.
- **n=2.** Directionally positive, not conclusive. Subprocess-adapter
  variance (review loops, timeouts) is large relative to the effect.

## Next

Another batch (2–3 missions, ideally repeated) before any default flip;
compare closure + review-loop counts, treat tokens as secondary until runs
are on an API adapter with cache-aware metering. Flag remains OFF.

---

# Batch 2 + pooled analysis (2026-07-08)

Batch 2 = 3 missions (m1, m2, + new m3-host-monitoring) × 2 arms × 2 reps =
12 runs. Operationally eventful:

- **Attempt 1 (overnight):** killed 10 min in — the hosting Claude Code
  session grew to 14.3 GB RSS and the kernel OOM-killed its tmux scope.
  0 rows lost-in-flight (append-after-run design). Relaunch detached
  (`setsid`) from any session lifecycle.
- **Attempt 2:** runs 1–9 clean; runs 10–12 hit Jeremy's plan rate limit
  (worker died at 0 tokens → false "stuck"; another run limped through
  60/120/240s retries at ~8% of its twin's tokens). Both contaminated rows
  EXCLUDED (identifiable by worker-level token signatures + retry lines in
  `ab_batch2_relaunch.log`); the 3 affected cells re-run post re-auth as a
  patch-up batch (`patchup: true` rows).

Clean pooled dataset: **16 runs, 8 per arm** (batch-1 4 + batch-2 9 +
patch-up 3). Raw rows: `worker_slice_ab.jsonl`.

## Pooled results (n=8 per arm)

| measure | A-off | B-on |
|---|---|---|
| closure (done) | 7/8 (m1 stuck) | **8/8** |
| blocked workers | 1/18 | **0/16** |
| slice injected | 0/18 | 16/16 |
| tokens_in median | 939k | **663k (−29%)** |
| tokens_out median | 45.3k | **37.2k (−18%)** |
| elapsed median | 692s | **574s (−17%)** |

Paired per-(mission, rep): B lower tokens-in in 5/8 pairs, ~equal 1, higher 2
(m1 rep2 +135%, m3 rep1 +25%). B faster or equal wall-clock in 7/8. The only
closure failure across all 16 runs is batch-1 m1 A-off.

## Confound check

- **Review-loop exhaustions balanced overall: A 10 vs B 10** (per-run counts
  from the batch logs) — unlike batch 1, the pooled token delta is NOT
  explained by exhaustion imbalance. Within m2 specifically A burned one
  extra exhaustion per rep (2v1, 3v2); at n=2 we can't distinguish noise
  from "slice → better first drafts → fewer review rounds", which is itself
  the causal path we're testing.
- **m2 is the cleanest evidence:** all 3 pairs favor B by −38/−38/−40%
  tokens-in. Its mission (agent-ops failure patterns) sits closest to the
  store's content (crystallized agent-run lessons) — recall relevance
  plausibly real, not lexical luck.
- **m3 caveat:** its workers interpreted "design a monitoring checklist" as
  *write repo files* — later m3 runs saw earlier runs' artifacts on disk
  (one ticket literally says "already scaffolded"), so m3's 4 cells have
  cross-run contamination and get low weight. m1/m2 workspaces were
  read-only; unaffected. (Side effect: one m3 worker committed AND pushed
  to main — the push guard was silently dead via a stale `core.hooksPath`
  from the 2026-06-25 rename; fixed + tripwired in `tests/test_git_guard.py`.)
- Subprocess-adapter token counts remain cache-blind; medians are for
  direction, not accounting.

## Verdict

Mechanism proven (16/16 injections, byte-identical off path, zero errors);
no measure favors A on clean data; the worst B outcome is "more tokens, still
done" twice. Slice cost is ~300 tokens/worker — orders below observed deltas.
**Recommend flipping `memory.worker_slice` to ON.** Flag flip is Jeremy's
call; until then it stays OFF. Re-measure after flip via captains-log
WORKER_SLICE_INJECTED + goal outcomes on organic (non-benchmark) runs.

## Isolation retrofit (2026-07-14)

The m3 caveat above is now structurally prevented for future repetitions.
`scripts/worker_slice_ab.py` gives every `(batch, mission, rep, arm)` cell a
collision-resistant retained workspace under the normal Maro output root and
scopes every Director/worker subprocess to it. A duplicate cell identity
refuses instead of consuming old files, and the JSONL row records the workspace
for inspection. The Director log remains separately linked by `log_path`.

The same convention now covers the main `eval.py` harness: each builtin or
generated cell atomically reserves a fresh Maro project before `handle()` runs,
and the resulting `BenchmarkResult.workspace` makes its artifacts traceable.
These directories are retained experimental evidence under the project's
no-silent-deletion rule, not ephemeral scratch. Raw run/cell identities carry
short digests so sanitization (`A/B` versus `A B`) and truncation cannot merge
otherwise distinct cells.
