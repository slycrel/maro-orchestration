---
status: record
---

# Review — the two Telegram-dispatched self-diagnosis runs (2026-07-28)

Jeremy dispatched two maro runs via the Telegram/Poe concierge on
2026-07-28 and asked for an independent review of both the runs and
the ideas behind their source links. Method: one delegated review
agent read both run dirs end-to-end and spot-checked ~17 concrete code
claims against the tree; the lead then independently re-verified the
three most load-bearing findings (lesson-store contamination,
recall content-blindness, quality-gate unconditional/scope-blind)
before synthesis. Discussion of dispositions was split to a forked
session — this doc is the evidence record, not the decision record.

## The runs

1. **384d2ebb-tawny-ferret** — self-diagnosis against the LLM-wiki
   pattern in Karpathy's gist (immutable raw sources; LLM-maintained
   interlinked "compiled wiki" between raw data and answers;
   ingest/query/lint operations). 8 steps, done-unverified, ~30 min,
   4.02M tokens in.
2. **dc8b85ef-dapper-oak** — complementary self-diagnosis against the
   Erdős-problems workflow X thread by @Qiaoqiao2001 (Shouqiao Wang,
   Columbia): claimed 6 of ~13 open Erdős problems solved in 5 days
   via unattended 6–32hr GPT-5.6 runs, with tractability
   pre-filtering, pre-registered "what counts as solved" prompts,
   parallel incompatible routes, and terminal adversarial audit.
   8 steps, done-unverified, ~23 min, 2.64M tokens in.

## Headline findings

### 1. Both memos are decision-grade, and both runs committed the
### failure class they were diagnosing

Inventory tables with file:line evidence, ranked changes with
acceptance tests, terminal adversarial passes that each caught real
errors in their own drafts. The large majority of ~17 spot-checked
code claims VERIFIED exactly (line numbers included). But:

- **dapper-oak's five-part memo never reached `artifact/`** — emitted
  as `[RAW_OUTPUT_DETECTED]` raw model output, it survives only in
  `build/loop-3e67fa5c-step-07.md`. A run about artifact/evidence
  handling misfiled its own deliverable.
- **tawny-ferret shipped a wrong headline finding because of its own
  sandbox**: it claimed the tire project dirs "no longer exist"
  (evidence for raw-source ephemerality) when both dirs sit on disk
  with FINAL_ANSWER files intact — the worker sandbox binds only its
  own project dir, so `ls` returned ENOENT. Sandbox masking, not
  deletion. A diagnosis about evidence durability was misled by its
  own evidence environment.

### 2. Poe's prompt scaffolding: factually false in one place, and
### compounding into maro's memory

- **The "already recovered" claim was false.** tawny-ferret's prompt
  said "The gist has already been recovered, so this is NOT blocked on
  external URL access" — but no gist text exists anywhere in the run
  (no fetch-raw/, nothing in source/). The run diagnosed against
  Poe's one-paragraph paraphrase, and its own `source/cuts.md`
  hard-committed: "no external fetch is needed **or permitted as a
  blocker**." The entire Karpathy comparison rests on an unverified
  summary — the exact anti-pattern the gist warns about.
- **Scaffolding → lesson store (lead-verified).** Lesson `db37d525`
  (minted from the 2026-07-27 tire run, live in
  `~/.maro/workspace/memory/lessons.jsonl`): "When a prompt explicitly
  says 'do not escalate/stop because X is inaccessible'... treat that
  as a hard constraint..." — and dapper-oak's recall injected it.
  Poe's per-prompt babysitting is becoming persistent learned
  behavior.
- **Consequence: the diagnosed pathway is untested.** Both runs
  dispatched `execute` at 0.95/0.82 confidence — the scaffolding
  routes around the dispatch-escalate failure mode every time, so
  these runs can no longer tell us whether it still exists.
- **dapper-oak's central source has zero on-disk provenance**: the
  "authenticated CLI thread fetch" grounding ~90% of the workflow
  claims left no fetch-raw file and no recorded tool call
  (transcripts demonstrably lossy — one 186K-token step logged 3
  calls). Unrecorded-vs-confabulated is undeterminable from the
  record. Violates artifacts-over-streams exactly where it mattered.

### 3. The narrow, verified gaps (vs. the broad claimed ones)

- **quality_gate is scope-blind and single-scalar** (lead-verified:
  zero `scope` references in `src/quality_gate.py`). Maro DOES
  pre-register done-criteria at dispatch (`source/scope.md`, consumed
  by `closure_verify.py` via `scope_supplied`) — the gate just never
  reads them, and judges with one confidence scalar conflating
  completeness and correctness.
- **Best-evidenced adoptable item, converged on independently by both
  runs: decouple answer-completeness from gate escalation.** The
  swift-echo tire incident is fully verified: complete
  FINAL_ANSWER.md on disk while `WEAK_ESCALATE` at 0.72 plus a
  closure RuntimeError (1500-token output cap) reported the run
  stuck.
- **recall is content-blind by design** (lead-verified:
  `src/recall.py:460` "File contents are deliberately not read") —
  the durable-knowledge recommendation is our retrieval/graph-memory
  near-term handle wearing another coat, with one new amendment:
  **any durable cross-run artifact must be bound into worker
  sandboxes** or it will be invisible exactly like the tire briefs
  were.
- **Adversarial pass fires once, terminally, unconditionally**
  (lead-verified: `if run_adversarial:` at `quality_gate.py:775`, not
  confidence-gated — both run_cards' "once confidence clears 0.75"
  summaries are wrong). Fork exists (`navigator.py:257`,
  FORK_CHILD_CAP=8) but is used for decomposition, not competing
  hypotheses.
- **Cost accounting bug lead**: both run_cards' `total_cost_usd` is
  ~half the loop logs' summed `provider_cost_usd` ($2.36 vs $4.52;
  $1.57 vs $3.29) — one path is wrong. Also: run_card
  `answer_summary` fields contain errors the memos don't — the LLM
  summaries are less trustworthy than the artifacts they summarize.

### 4. Rejected / already-implemented

Rejected: literal single global index.md (both memos agree);
run-until-no-gaps unbounded budgets; tawny-ferret's ephemerality
framing (sandbox artifact); its dispatch-retry recommendation (jumps
to a mechanism whose causal role was never confirmed — dapper-oak's
log-only classification experiment is the right first move).

Already implemented (both memos partially missed): pre-registered
scope at dispatch; terminal adversarial step on every run (both runs
had one and derived real corrections from it); fork/collate;
append-only captain's log; task ledger (`memory_ledger.py` — which
dapper-oak nominates, with the better argument, as the seam where
within-run route ledgers and cross-run compiled knowledge should
meet: "structured, evidence-linked claims... never freeform
self-authored prose promoted to fact").

## Overall verdict

The runs vindicate maro's machinery more than they indict it — most
of the "missing" wiki pattern exists but is pointed at orchestration
telemetry rather than research content, and the real gaps are three
narrow seams (gate never reads scope; recall content-blind by design;
sandbox binding hides cross-project artifacts). The actionable
tonight-item is the Poe finding: well-intentioned scaffolding,
factually sloppy, now self-reinforcing through the lesson store — a
contamination vector into exactly the memory system we care about.
Dispositions (BACKLOG rows, lesson-store handling of `db37d525`, the
cost-accounting bug, gate-reads-scope riding closure-check
unification) deliberately deferred to the forked discussion with
Jeremy's view of the Poe conversation as input.

## Full delegated-review report

The complete agent report (per-run prompt anatomy, trajectories, the
full spot-check tables with VERIFIED/WRONG/PARTLY-RIGHT/UNCHECKABLE
verdicts, and source-idea reconstructions) is preserved below
verbatim for the record; the synthesis above is the lead's.

---

### RUN 1 — 384d2ebb-tawny-ferret (Karpathy LLM-wiki self-diagnosis)

**Prompt anatomy.** 5-paragraph directive: inventory maro against the
gist's pattern pieces (working/nominal/absent, cited to repo files),
rank 3–5 highest-leverage changes, candid useful-vs-harmful
comparison, one bounded organic experiment; diagnosis-only. Operator
scaffolding: the false "already recovered" line (above); three
injected failure incidents framed as "symptoms to explain, not merely
incidents to restate" (legitimate context, pre-frames causation);
concierge guardrails ("Do not change code... or ask Jeremy for
input"). `source/cuts.md` committed at planning time to a predicted
verdict ("likely 'write-only memory'"), which the run then overturned
(final: "working but domain-mismatched") — to its credit.

**Trajectory.** 4 planned → 8 executed steps, 0 blocked, no
escalations; dispatch navigator `execute` at 0.95. Included a live
bounded rerun of the tire goal (`--max-steps 3`; timed out exit 124
in step 1's adapter call after 280s) and a terminal adversarial step
that found `_navigator_act_dispatch` and rewrote recommendation #5 in
place. run_card: done / done-unverified / goal_achieved null /
$2.356 (loop log says $4.52).

**Spot-checks** (delegated agent, against the real tree):

| Claim | Verdict |
|---|---|
| recall.py match ladder exact/near-text/project-slug; artifact context filename-only, "File contents are deliberately not read" | VERIFIED |
| quality_gate.py threshold 0.75 @505-511, WEAK_ESCALATE @633, council override ~818-825 | VERIFIED |
| swift-echo: complete FINAL_ANSWER buried by WEAK_ESCALATE 0.72 + closure RuntimeError (1500-token cap) | VERIFIED |
| handle.py `_navigator_act_dispatch` @2739, floor 0.9, default-on since 2026-07-08 | VERIFIED |
| Tire project dirs "no longer exist" → sources ephemeral | WRONG (sandbox masking; both dirs on disk with artifacts) |
| No index.md/log.md/AGENTS.md anywhere | PARTLY-RIGHT (true for ~/.maro/workspace; the "across /home/clawd" search claim false — ~/.openclaw/workspace/AGENTS.md exists) |
| knowledge*.py = 4,210-line crystallization subsystem | VERIFIED (wc -l exactly 4210) |
| Subprocess-Opus adapter fragility + exit-124 rerun stall | VERIFIED |
| captains_log.py log_event @376 / query_log @591; recall_citations write ~@802-813 | VERIFIED |

**Memo recommendations:** (1) content-aware recall matching; (2)
per-topic wiki file seeded from FINAL_ANSWER.md; (3) decouple
answer-completeness from gate escalation; (4) fix subprocess-Opus
adapter fragility; (5) bounded retry inside `_navigator_act_dispatch`.

### RUN 2 — dc8b85ef-dapper-oak (Erdős X-thread self-diagnosis)

**Prompt anatomy.** Explicitly queued after run 1; Poe pre-scripted
the retrieval strategy ("direct X page, FX/VXTwitter-style renderers,
search, quoted copies"; "A failed first retrieval path is not a
reason to stop or escalate"), injected a self-characterization of
maro ("its tendency to stop at dispatch... and to report incomplete
research as if it were usable"), and banned generic "use more agents"
advice. The dispatch pipeline amplified this: `source/scope.md`'s
first failure mode restates the anti-escalation clause, and recall
injected the lesson minted from run 1's sibling the same night.

**Trajectory.** 4 planned → 8 executed, 0 blocked; dispatch `execute`
at 0.82. fxtwitter root-tweet fetch verified on disk (1.8KB JSON);
four further retrieval paths for replies all dead or empty; the
load-bearing "authenticated CLI thread fetch (18/18 author posts +
8/31 replies)" left zero on-disk trace. run_card: done /
done-unverified / $1.569 (loop log $3.29). Delivery defect: memo
never written to artifact/; `deliverable_link_path` points at the
step-8 addendum instead.

**Spot-checks:**

| Claim | Verdict |
|---|---|
| handle.py:2753-2821 dispatch escalate halts before run dir exists, no re-route | VERIFIED |
| agent_loop.py:129 max_iterations=40 | VERIFIED |
| Fork/collate exists: navigator.py:257, FORK_CHILD_CAP=8 (step-8 self-correction) | VERIFIED |
| closure_verify.py:1052 downgrade-to-0.69 safety valve, not admission gate | VERIFIED |
| memory_ledger.py TaskLedgerEntry @191 / append @271 / load @292 / record_step_trace @331 | VERIFIED |
| Adversarial pass "gated behind confidence 0.75" | PARTLY-RIGHT (single terminal invocation true; NOT confidence-gated — runs unconditionally) |
| "Goals never define done vs disqualifying-weaker up front" | PARTLY-RIGHT (scope.md pre-registers at dispatch; true narrower gap: quality_gate never reads scope) |
| Repo "not a git repo — no .git dir" | WRONG (.git exists, HEAD matched scope.md's own citation) |
| "Authenticated CLI fetch recovered 18/18 + 8/31" | UNCHECKABLE (zero provenance) |

**Memo changes:** A dispatch filter-and-redirect (instrumentation
first); B pre-registered solve criteria (see PARTLY-RIGHT above); C
adversarial continuous not terminal; D split the single confidence
scalar (= run 1's #3). Plus the memory_ledger nomination for the
compiled-knowledge seam and a correct rejection of importing
unbounded 6–32hr budgets.

### Cross-run

Run 2 genuinely complemented run 1 (cross-run durable knowledge vs
within-run iteration discipline; read run 1's memo, reserved the
ledger/wiki seam deliberately; ~one step of duplicated dispatch-seam
grepping). No direct contradictions; they nominate different durable
substrates (per-topic wiki files vs structured memory_ledger claims)
— a design fork to resolve, with run 2's structured-claims constraint
the better-argued half. Reality contradicts run 1's ephemerality
finding; the corrected fix target is sandbox binding + content-aware
recall, not file survival.

### Source ideas (as reconstructible — both second-hand)

**Karpathy gist (per Poe's paraphrase only — never fetched by any
run):** immutable raw sources as permanent truth; an LLM-maintained
"compiled wiki" of interlinked Markdown between raw data and answers;
schema/AGENTS conventions; ingest (integrate + cross-link), query
(answer with citations, file results back), lint (contradictions,
stale claims, orphans, gaps); start with index.md + append-only
log.md; the claim is compounding work across sessions instead of
re-deriving scattered context.

**Erdős thread (root tweet verified via fxtwitter JSON; the rest
rests on the unrecorded fetch):** Shouqiao Wang claims 6 of ~13
attempted open Erdős problems (390, 486, 536, 788, 1002, 1038) in 5
days, GPT-5.6 "Ultra" via Codex, unattended 6–32hr per problem. Four
disciplines: AI-assisted tractability pre-filtering; prompts that
precisely restate the problem, define "solved," enumerate
non-qualifying weaker results, flag known traps, and require
independent adversarial challenge; search management (many
independent approaches, incompatible routes kept alive,
counterexample hunting, route blocked only when it reduces to another
unproved statement of comparable strength); attempt → failure →
diagnosis → new approach → proof draft → adversarial audit → repair,
stopping only when the audit finds no substantive gaps. Author
self-limits: 2/6 Lean-formalized, ~2 months prompt iteration
preceded, compute capped/sponsored. Repo claim never independently
fetched (correctly downgraded to author-asserted in the run's step
8).
