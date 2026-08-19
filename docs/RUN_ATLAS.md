---
status: living
---

# Run Atlas — the process map, with real runs overlaid

**What this is.** A single self-contained HTML page showing the whole
orchestration pipeline as nine collapsible phase bands, with any individual
run's route lit on top of it. Built 2026-08-18 from Jeremy's ask: *"I'd like to
visualize the process… full diagram of how it works that can overlay runs (so
you can see how a given run traveled the possibilities of each part of the
process). May require coarse and fine-grained encapsulation (zoom in/out)."*

It answers two questions the per-run HTML report (`src/loop_report.py`) does
not:

1. **Where did this run actually go?** — not just which steps ran, but which
   gates it passed, which branch each blocked step took, which verdict layer
   stamped it.
2. **Which possibilities does the process actually use?** — filter to a cohort
   (lane / outcome / month) and every node's bar becomes that cohort's share
   passing through it. Comparing the `success` cohort against `failed` shows
   where the process really diverges, rather than where we assume it does.

Tooling lives in `scripts/run_atlas/`. It is read-only over a workspace and
writes nothing back into it.

```
python3 scripts/run_atlas/extract_paths.py process_paths.json
python3 scripts/run_atlas/build.py       process_paths.json run_atlas.html
```

`MARO_WORKSPACE` selects the workspace (default `~/maro-box-copy/workspace`;
on the box, `~/.maro/workspace`). Neither generated file is committed — the
inlined page runs ~2.2 MB for 788 runs.

---

## The reconstruction problem

> **Superseded in part, 2026-08-18.** Runs from that date carry a recorded edge
> trace (`build/trace.jsonl`, `docs/RUN_TRACE.md`) — including the phase track,
> which `set_phase` now persists. For those runs the atlas reads the real
> traversals instead of inferring them, and draws them solid. Everything below
> still describes the ~788 runs that predate the trace, and the fallback path
> for any run whose trace is missing or marked degraded.

**`LoopPhase` was never persisted.** `ctx.phase` was in-memory only; `set_phase()`
mutated it and nothing wrote it to disk. This is the single fact that shaped
the whole tool: with no recorded phase track to replay, every node in the atlas
had to be *inferred* from artifacts that do survive —

| Source | What it establishes |
|---|---|
| `metadata.json` | lane, origin, persona, verdict source, stop verdict, pause reason, the `loops[]` attempt ledger |
| `run_card.json` | `success_class`, curation stages that ran, costs (2026-07 onward) |
| `build/loop-<id>-log.json` | the step spine: per-step status, iteration, ledger index, tokens, cost |
| `build/closure_verdicts.jsonl` | closure verdict + per-check outcomes |
| `build/calls/call-*.json` | `purpose` — the richest per-call phase label we have |
| `build/captains_log_slice.jsonl` | the event stream (`METACOGNITIVE_DECISION`, `CLOSURE_VERDICT`, …) |
| file presence | `source/scope.md`, `source/cuts.md`, `RESULT.md` vs `PARTIAL.md`, … |

That write now exists (`src/run_trace.py`, wired into `set_phase` itself), so
the inference layer below is the *fallback*, not the primary path.

## Evidence strength: attributed vs windowed

`runs.slice_log_for_run` (`src/runs.py:1764`) copies bytes from the run's
recorded start offset **to the current end of the global captain's log**. A
slice is therefore not run-scoped: it carries concurrent runs' events and a
post-`ended_at` learning tail. Only ~11 of ~48 `log_event()` call sites pass a
`loop_id`, so most log lines cannot be attributed by id at all.

The extractor marks every node visit accordingly, and the page renders the
difference rather than flattening it:

- **attributed** (`e:"a"`, solid) — the node came from a run-scoped file, or
  from an event carrying this run's own `loop_id`.
- **windowed** (`e:"w"`, dashed) — the node came from an event with no
  `loop_id` that merely fell inside this run's time window. It may belong to a
  concurrent run.

**Measured: 600 of 11,892 node visits are windowed (5%).** The share is low
because most nodes are established by run-scoped *files*, not by log lines —
the 85%-unattributed figure applies to the log, not to the atlas. The
event-only nodes (notably `exec.redecompose`, `gate.claims`) are where the
dashes concentrate, and those are exactly the nodes not to quote a number from
without checking.

The learning tail is clipped separately: `LESSON_RECORDED` / `SKILL_PROMOTED` /
`KNOWLEDGE_NODE_PROMOTED` events are only counted when attributed **and**
timestamped at or before `ended_at`, because that tail routinely fires after the
run closes and belongs to whatever ran next.

## What the atlas does not claim

- **Edges light when both endpoints were visited** — *for runs with no recorded
  trace*. That is an inference from node presence, not a recorded transition: a
  lit edge means "this run was at both ends", not "it crossed here at this
  moment". Runs carrying a trace light exactly the edges they recorded, drawn
  solid, and the inspector says which of the two you are looking at.
- **Retries are counted from the step log, not from the event field.**
  `METACOGNITIVE_DECISION.context.retries` is the *prior* count and tops out at
  1 in the corpus; true attempts come from counting duplicate step `text` at
  consecutive iterations.
- **Durations are approximate wherever any step lacks `ended_ts`.** The page
  marks those loops `approx timing`. Cumulative-sum fallback silently absorbs
  inter-step overhead — replans, verification, hooks — into the preceding
  step's segment.

## Coverage — what is reconstructable, by month

Measured over 788 runs in the 2026-08-16 box export (% of that month's runs):

| artifact | 04 | 05 | 06 | 07 | 08 |
|---|---|---|---|---|---|
| loop log | 100% | 65% | 43% | 83% | 93% |
| run card | 0% | 0% | 0% | 92% | 100% |
| closure verdicts | 0% | 0% | 0% | 3% | 85% |
| call records | 0% | 0% | 0% | 81% | 100% |
| **runs in month** | **2** | **476** | **141** | **115** | **54** |

**Full reconstruction — loop log *and* run card *and* closure checks — is
available for 50 of 788 runs (46 in August, 4 in July).** April–June maps are
genuinely sparser because the artifacts did not exist yet, not because those
runs traveled less. Read a sparse old map as missing instrumentation, never as
a simple path.

## The two cost numbers still disagree

Re-measured here rather than inherited: of **77 runs carrying both figures, 6
agree within 10% (8%)**. The `card/log` ratio spans 0.10× to 2.21×, **median
0.48×** — the run card typically reports about half what the loop log's
backend-reported figure says. (This corroborates, from a different direction,
the run-card-cost-runs-low finding in the LT arc.) `run_card.total_cost_usd`
sums `memory/step-costs.jsonl` by `loop_id`; the loop log carries the
provider-reported number. The atlas shows **both, labeled**, and prefers
neither. Do not sum either across runs expecting them to reconcile.

## Maintaining the topology

The map itself is declared in `scripts/run_atlas/template.html` as `PHASES`
(nine bands, each node carrying a label, grid position, kind, and a **code
anchor**) and `EDGES`. When the pipeline changes, edit it there — the anchor on
each node is what makes the map auditable against the code rather than a
drawing that drifts. `docs/EXECUTION_FLOW.md` is `dormant-design` and predates
the `loop_*.py` split; the `PHASES` table is the current structural map.

Adding a node means: give it an id and anchor in `PHASES`, wire its edges, then
teach `extract_paths.py` how to detect it (event type, file presence, or
metadata field) and mark whether that detection is attributed or windowed.

## Probe ledger

Claims above, and how to re-check them:

| Claim | Probe | Result |
|---|---|---|
| `LoopPhase` never persisted | `grep -rn "\.phase\b" src/*.py \| grep -iE "json\|dump\|write\|stamp\|metadata"` | no hits, 8 `set_phase` sites |
| slice runs to EOF of the global log | read `slice_log_for_run`, `src/runs.py:1764` | confirmed in code + docstring |
| windowed share | count `e` field across all `visits` in the extract | 600/11,892 = 5% |
| cost disagreement | compare `run_card.total_cost_usd` vs summed `loop log totals.provider_cost_usd` | 6/77 within 10%, median ratio 0.48× |
| coverage by month | presence census over 788 rundirs | table above |
