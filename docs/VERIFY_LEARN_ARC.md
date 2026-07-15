---
status: living
---

# Verify→learn arc — design brief

**Status:** design brief, written 2026-07-12 (Fable-handoff session). **The
whole arc SHIPPED 2026-07-14.** V1 (expectation stamping), V2 (cadence verdicts
+ authority-aware auto-revert), and V3 (graduation behavioral auto-verify —
per-class failure_class_rate verdicts) closed the applied-change verify→learn
loop for both the evolver-suggestion and graduation lanes; V4 (navigator
divergence adjudication) and V5 (navigator lessons) closed the navigator half of
#6 — divergences are LLM-adjudicated at cadence and navigator-wrong clusters
crystallize into lessons injected back into `decide()`. See §7 for what each
chunk landed. This was the arc decreed "the next design arc after 1.0 (not
folded into 1.0, not parked)" — thread-architecture decision #6, "how the
navigator improves." Judgment calls tagged `DECISION (provisional)`. File:line
references from the original design pass verified at commit ffff3f6.

The two-things-conflated framing (CLAUDE.md): Maro-as-tool works today;
Maro-as-self-improving-system is "infrastructure 80% built; verify→learn
loop not closed." This brief is the design for closing it.

---

## 1. The loop as it exists — where each leg actually stands

`docs/SELF_REFLECTION.md`'s cycle: **observe → classify → fix → verify →
learn/graduate**. Audit of each leg against the tree (the doc itself lags —
its "to build" layers 2–4 are largely built):

| Leg | State | Evidence |
|---|---|---|
| **Observe** | DONE | events/outcomes/diagnoses JSONL, run dirs, record mode, attribution. |
| **Classify** | DONE | `introspect.diagnose_loop` (10-class taxonomy, cache-aware thresholds, introspect.py:225-453), lenses + aggregation, inspector friction signals (inspector.py:407-489). Wired at every finalize (loop_finalize.py:409-476). |
| **Fix (propose)** | DONE, verdict-aware | evolver meta-cycle + 5 statistical scanners + graduation templates (≥3 occurrences → suggestion, graduation.py:188-367) + inspector findings — all write suggestions.jsonl. Lesson extraction is verdict-aware since data-r2-01 (defer → post-closure `finalize_deferred_learning`, loop_finalize.py:702-767). |
| **Fix (apply)** | DONE, gated | `apply_suggestion` (evolver_store.py:394-577): injection-guard fail-closed; guardrails held for review unless `evolver.auto_apply`; skill mutations behind a real test gate; change_log.jsonl records before_state; `revert_suggestion` replays it. |
| **VERIFY** | **CLOSED for applied changes (V2+V3 shipped 2026-07-14)** | Hole (2) is fixed for the suggestion lane: `scan_evolver_impact`'s before/after comparison is no longer warn-only — `verify_applied_suggestions()` renders per-change verdicts at cadence and **reverts** degraded self-applied changes (§3/§7). Hole (3) is now closed too: an applied graduation row gets a *behavioral* verdict on its own `failure_class_rate` (V3), demoted under symmetric authority (human-applied → surfaced for review); the structural `verify_pattern` grep stays pure observability. Hole (1) stands by design — `_verify_post_apply` remains the orthogonal "tests still green" gate, distinct from the behavioral verdict. |
| **Learn** | Half-closed | Verdict-aware extraction shipped; but verdict *trust* is uncalibrated (§4) and nothing feeds verified-change outcomes back into proposal confidence beyond `_record_suggestion_outcomes`. |

**The arc in one sentence: give applied changes the same lifecycle
discipline lessons already have — an expectation at birth, a verdict at
cadence, and demotion when contradicted.**

## 2. Design principles (all already decreed — this arc inherits, not invents)

- **Decay trust, never data** — verification demotes/reverts compiled
  changes; evidence rows are append-only.
- **No daemons** — every verification pass rides run finalizations
  (`evolver_cadence_tick` pattern, evolver_store.py:126-167) or existing
  hooks. Nothing schedules itself.
- **Verdicts are calibrated inputs, not truth** — raw `goal_achieved`
  understates organic success 20–30 points (2026-07-09 done-vs-achieved
  analysis); learning must consume verdicts through the trust policy (§4),
  never raw.
- **Consumer-first** — no verification machinery lands without the thing
  that acts on its output in the same chunk.
- **The three shipped precedents are the vocabulary** — this arc
  generalizes patterns that already work, it does not invent a framework:
  1. `refight_rule` (knowledge_lens.py:594-690): contradiction evidence →
     keep / revise / retire-to-hypothesis.
  2. Skills circuit breaker (skills.py:978-1002): consecutive failures →
     open → quarantine/rewrite, recovery earns closed.
  3. Navigator shadow adjudication (navigator_shadow.py:450-499):
     divergence is eval data; agreement tables earn cutovers.

## 3. Core mechanism: expectation-stamped changes + cadence verdicts

**V1 — expectation at birth.** Every applied suggestion/graduation row gains
`expected_signal`: which observable should move, direction, and scope —
e.g. `{"metric": "failure_class_rate", "class": "retry_churn", "direction":
"down"}` or `{"metric": "verdict_rate", "scope": "project:X"}`. Graduation
templates already carry `verify_pattern` (a structural grep); this adds the
*behavioral* expectation. Templates get them statically; LLM-authored
suggestions declare them at generation time (schema field in
`_EVOLVER_SYSTEM`, evolver.py:85-111); rows without one default to the
class-neutral pair (stuck-rate, cost/run).

**V2 — verdict at cadence.** At the existing `evolver.run_cadence` hook
(no new scheduling), a verification pass walks applied-but-unverified
changes older than a minimum window (default: 10 finalizations post-apply)
and renders per-change verdicts by comparing the expected signal's
before/after windows — this is `scan_evolver_impact` promoted from
warn-only to verdict-bearing:

- **confirmed** → stamp `verified_at` + feed `_record_suggestion_outcomes`
  calibration (proposal sources whose changes keep confirming earn
  confidence; sources whose changes keep reverting lose it).
- **degraded** → **auto-revert** via the existing `revert_suggestion`
  before_state replay + a VERIFY-class captain's-log event + notify. The
  reverted row stays (append-only); the change *reproposal* arrives
  contested, refight-style.
- **inconclusive** (not enough post-apply runs in scope) → window extends;
  after 3 extensions, park as `unverifiable` and say so — an honest
  unverifiable beats an eternal pending.

> **DECISION (provisional): symmetric authority for auto-revert.** Anything
> the system applied *without* a human (auto-apply band, advisor-gated
> band) it may revert without a human. Anything a human applied
> (`manual=True`, held-for-review approvals) is never auto-reverted — the
> verdict surfaces as a notify + review-queue item instead. Rationale: the
> gate asymmetry mirrors the apply-side gates exactly; the system cleaning
> up its own mess is the point, overriding the operator is not.

**V3 — graduation auto-verify.** `verify_graduation_rules` runs in the same
cadence pass (its checks are cheap greps). Failing rules: revert +
demote-to-hypothesis (the refight shape), notify. This closes the "rules go
live with zero automatic verification" hole with ~no new machinery — the
function exists, the hook exists, they've just never met.

**2026-07-14 precursor:** the function and cadence hook have now met for
rows whose durable state is actually `applied=true`. Results emit
`GRADUATION_VERIFIED`; failures may notify, but are labelled structural-only
and never mutate state. Manual-apply provenance is persisted for the
authority policy.

**Owner decision 2026-07-14 (Jeremy):** V3 was **buildable now** — not a design
dependency — and **SHIPPED 2026-07-14**. Both prerequisites Codex named as
"must define first" were already present: the *behavioral expectation* a
graduated rule carries is V1's `expected_signal` (all 9 templates declare
`failure_class_rate ↓`), and the *authority-aware, crash-safe demotion target*
is V2's symmetric-authority revert plus the `behavioral` flag. V2's verify path
was already category-agnostic, so an applied graduation row flowed through it —
but on the class-neutral *global* stuck-rate, in which a single failure class's
movement is noise, so graduation rows only ever parked `unverifiable`. **What
V3 built:** `verify_applied_suggestions` now consumes a row's `expected_signal`
and verdicts a `failure_class_rate` row on *that class's* rate over
timestamped-diagnosis windows (self-falling-back to the stuck-rate when the
class windows are thin — a sparse class parks honestly, never verdicts off
noise). Diagnoses had no time axis of their own; V3 gives each one a coordinate
two ways — a go-forward `recorded_at` stamp *and* an events-log join on
`loop_id` (`_loop_ts_index`, ~99% coverage on real data) — so the class path is
live on the full historical ledger (1274/1277 diagnoses on this box), not
dormant waiting for new diagnoses. The confirmed/degraded →
calibrate/demote lifecycle and symmetric authority are reused from V2 unchanged.
**The owner call landed as its safe default:** graduation rules stay
advisor-gated (a human applies them via `maro evolver apply`) — nothing
auto-applies a standing rule — so a degraded graduation row takes the
human-applied branch: surfaced for review, never auto-reverted. The structural
`verify_pattern` check stays pure observability (`run_graduation_verification` /
GRADUATION_VERIFIED) — a grep miss ≠ the applied lesson failed, so it must not
gate state. Knob: `evolver.verify_use_class_signal` (default ON).

## 4. Verdict trust policy — which verdicts learning may consume

One policy function (single source, like `secret_scrub`), consumed by
deferred-lesson extraction, skill crystallization gates, V2 windows, and
any future consumer:

| Verdict shape | Trust |
|---|---|
| Judged True/False, confidence ≥ 0.7, no environment-error cap | Full — the tri-state the machinery was built for. |
| Judged, conf < 0.7 | Directional only — may flavor lesson framing (already does, memory.py:274-277), never gates crystallization or counts in V2 windows. |
| `done-unverified` (verdict absent) | Neutral — present state, keep. |
| `closure_unverifiable` (`judged=False`, verifier-own failure) | **Excluded** from all learning consumers and V2 windows. |
| Environment-error-capped verdicts (shipped via probe-env hardening, `docs/history/2026-07-12-routing-and-probe-synthesis-design.md` B3) | Excluded, same as unverifiable. |

**Hard dependency, sequencing-critical — SATISFIED 2026-07-12:** B3
probe-env hardening shipped before any V2 verdict-window logic goes live.
The 4/5 dogfood false-negative rate meant the pre-B3 verdict stream would
have taught the verifier's cwd bugs as behavioral regressions. The
done-vs-achieved caveat ("re-run at organic n≈30") is still the calibration
gate for V2 itself.

## 5. The navigator half (#6 proper — "how the navigator improves") — SHIPPED

The agreement table earns cutovers; what was missing was the table *learning*.
V4/V5 closed it (2026-07-14; see §7 for the landed detail):

**V4 — divergence adjudication at cadence (SHIPPED).** Un-adjudicated
NAVIGATOR_DECIDED divergences get a capped, cheap-tier LLM adjudication pass in
the evolver cadence hook: navigator_right / pipeline_right / both_defensible,
appended append-only as a `NAVIGATOR_ADJUDICATED` row (joined by `div_key`).
Consumer in the same chunk: the `--agreement` table grew an `adjudicated`
breakdown — the evidence surface Jeremy already uses for cutover calls
(dumb-loop-audit rounds, done by hand, now standing).

**V5 — navigator lessons, consumer-first (SHIPPED).** Adjudicated
navigator-wrong-pattern clusters (`pipeline_right`, ≥3 same-shape — the
graduation threshold) crystallize as navigator-scoped lessons injected into
`decide()`'s prompt context via the same seam worker slices use. Lesson +
injection + A/B flag landed in one chunk (`navigator.lesson_inject`,
shadow-comparable like `memory.worker_slice` was, via the `lessons_injected`
marker on the decision row).

> **DECISION (provisional):** per-move cutover decisions themselves stay
> human (Jeremy's) — this arc makes the evidence cheaper and standing, it
> does not automate the cutover. Same posture as close-move (organic-blocked,
> reconfirmed 2026-07-12).

## 6. Explicit non-goals

- No new LLM self-reflection lane — the evolver IS that; this arc gives its
  output a verdict.
- No lens-registry expansion (SELF_REFLECTION's removal test stands).
- No daemon/scheduler — post-1.0 scheduler design is a separate item.
- No auto-tuning of closure thresholds (keep 0.7 — done-vs-achieved verdict).
- Not part of 1.0 (decree) — but B3 (its hard dependency) IS pre-arc work.

## 7. Chunks (sized for handoff; order matters)

- **V0 (pre-arc) — SHIPPED 2026-07-12** (`docs/history/2026-07-12-routing-and-probe-synthesis-design.md`
  B3): probe-env hardening — the verdict stream is now honest before
  anything downstream consumes it.
- **V1 — expectation stamping — SHIPPED 2026-07-14 (Sonnet).** `Suggestion`
  gains `expected_signal: List[dict]` (evolver_store.py), additive/empty-
  default so every pre-existing row and producer stays valid unchanged. All 9
  `_GRADUATION_TEMPLATES` declare `[{"metric": "failure_class_rate", "class":
  <own key>, "direction": "down"}]`, derived once from the template dict's own
  keys (graduation.py) so the class name can never drift from the template it
  describes — threaded through `run_graduation()`'s entry dict. `_EVOLVER_SYSTEM`
  teaches the LLM proposer the same field (optional; omit if no concrete
  observable), and `run_evolver()`'s raw-suggestion parse passes it through via
  `safe_list(..., element_type=dict)`, same sanitization as every other LLM-
  authored field. No new config default — this is a data-schema addition, not
  a runtime knob, so nothing to register in DEFAULTS.md; "DEFAULTS rows" in
  this chunk's original sizing note turned out to mean the per-class default
  values living in the templates themselves. Deliberately did NOT touch the
  ~7 other `Suggestion`-emitting call sites (calibration/cost/canon/
  suggestion-calibration/drift/signal/harness-friction/persona-gap scanners)
  — the design's own "rows without one default to the class-neutral pair" is
  a V2 (trust-policy/consumer) read-time concern, not a V1 write-time one;
  stamping it onto every producer now would be presumptuous about a policy V2
  hasn't decided yet. 8 new row-shape unit tests (`tests/test_evolver.py`,
  `tests/test_graduation.py`); full suite green.
- **V2 — cadence verdicts + auto-revert — SHIPPED 2026-07-14 (Opus).**
  `verify_applied_suggestions()` (evolver_scans.py) rides the existing evolver
  cadence hook — no daemon. It walks every `applied` suggestion without a
  terminal `verified_at`, compares the class-neutral stuck-rate over count-based
  before/after windows keyed to each row's own `applied_at`, and drives the
  lifecycle: **confirmed** → stamp `verified_at`/`verify_verdict` + feed
  `_record_suggestion_outcomes` (positive calibration); **degraded** →
  symmetric-authority action — self-applied (`applied_manually=False`) is
  `revert_suggestion`'d + `EVOLVER_VERDICT` event + non-blocking notify, a
  human-applied row is stamped `degraded_needs_review` and surfaced to the
  escalation queue (blocking notify) but **never** auto-reverted; **inconclusive**
  → bump `verify_extensions`, park `unverifiable` past the cap. The trust
  policy (§4) shipped with it as its first consumer: `verdict_trust()` in
  memory_ledger.py (single source), consumed via `_verify_counts` so
  `closure_unverifiable`/env-capped and low-confidence verdicts never count in
  a window. **Prerequisite bug fixed in the same chunk:** `scan_evolver_impact`
  windowed on `created_at`/`timestamp`, but real `Outcome`s carry
  `recorded_at` — so the warn path had been dead on production data (only test
  fakes matched). `_outcome_ts` now prefers `recorded_at`. Knobs (all in
  DEFAULTS.md): `evolver.verify_cadence_verdicts` (default ON — a *safety*
  mechanism, only reverts what the system itself applied), `verify_min_post_apply`
  (10), `verify_max_extensions` (3), `verify_delta_threshold` (0.05). Operator
  surface: `maro evolver verify [--apply]` (dry-run by default). The metric is
  the class-neutral stuck-rate pair (the declared fallback); `failure_class_rate`
  windows still need timestamped diagnoses, which don't exist — evaluating what
  we can and parking the rest as `unverifiable` is honest. 17 new tests
  (trust buckets, `_verify_counts`, every verdict/authority path, the
  recorded_at regression, disabled-skip, dry-run). Both acceptance legs —
  one confirm AND one degrade→revert with calibration feedback — exercised.
  **Adversarial-review hardening (same day, 3 Codex reviewers):**
  (a) the post-apply window is now bounded to the FIRST `min_post_apply`
  *trusted* outcomes (symmetric to the baseline) — an unbounded "all outcomes
  after apply" let a later, unrelated regression bleed into an old row's
  verdict and trigger a spurious auto-revert; (b) `revert_suggestion` now
  returns an additive `behavioral` flag (True only when the change's effect was
  actually undone — not for append-only prompt/lesson tweaks or a missing audit
  trail). V2 keys off it: a degraded self-applied change whose revert could not
  behaviorally undo it is stamped `degraded_revert_failed`, left live, and
  surfaced **blocking** for manual repair — never falsely counted as reverted;
  (c) an irreversible auto-revert re-reads the row (`get_suggestion`) to
  re-confirm authority just before acting, so it can't fire off a stale
  snapshot; (d) baseline floor raised to `max(3, min_post_apply//2)` so an
  auto-revert can't fire off a 3-sample baseline; (e) `scan_evolver_impact`'s
  insufficient-data gate fixed (`and`→`or`: each window needs the minimum).
  6 more regression-lock tests.
- **V3 — graduation auto-verify (Opus) — SHIPPED 2026-07-14.** The behavioral
  verdict for an applied graduation row was already reachable via V2 — but on
  the class-neutral global stuck-rate, on which a single class is noise, so
  graduation rows only ever parked `unverifiable`. V3 makes the verdict
  *resolve*: `verify_applied_suggestions` consumes the row's `expected_signal`
  and verdicts a `failure_class_rate` row on that class's rate over
  timestamped-diagnosis windows (diagnoses gained a `recorded_at` stamp;
  self-falls-back to the stuck-rate when the class windows are thin). Lifecycle
  + symmetric authority reused from V2 unchanged; rules stay advisor-gated
  (human-applied → degraded surfaces for review, never auto-reverted — the
  owner's safe default). Structural `verify_pattern` stays pure observability
  (a grep miss ≠ the applied lesson failed). Knob
  `evolver.verify_use_class_signal` (default ON). **Time axis (hardened same
  day):** each diagnosis's date comes from its `recorded_at` stamp *or* an
  events-log join on `loop_id` (`_loop_ts_index`) — the join covers 1274/1277
  diagnoses already on disk, so the class path is live on the historical ledger,
  not dormant waiting for post-V3 rows to accrue. 13 tests
  (`test_evolver.py::TestVerifyClassSignal`, `test_introspect.py`).
- **V4 — divergence adjudication (Opus) — SHIPPED 2026-07-14.** At evolver
  cadence (no daemon), `adjudicate_navigator_divergences()` (navigator_shadow.py)
  takes un-adjudicated NAVIGATOR_DECIDED divergences (navigator move ≠ pipeline
  move_equivalent), runs a capped, cheap-tier LLM pass, and appends an
  append-only `NAVIGATOR_ADJUDICATED` row per verdict (navigator_right /
  pipeline_right / both_defensible), joined to its divergence by a stable
  `div_key`. **Consumer in the same chunk:** `analyze_live_agreement()` grows an
  `adjudicated` breakdown and attaches each verdict to its divergence row, so
  `python3 -m navigator_shadow --agreement` — the surface Jeremy already uses for
  cutover calls — now shows adjudication standing instead of by-hand. Nothing is
  acted on/reverted (evidence only). Idempotent (already-adjudicated rows
  skipped), capped per cycle. Gated OFF by default (`navigator.adjudicate_divergences`
  — an LLM spend, no silent cost for strangers; this box opts in). CLI:
  `--adjudicate [--dry-run]`. Knobs: `adjudicate_max_per_cycle` (5),
  `adjudicate_tier` ("cheap"). Proven end-to-end on the box's 71 live
  divergences. 8 tests.
- **V5 — navigator lessons (Opus) — SHIPPED 2026-07-14.**
  `crystallize_navigator_lessons()` clusters `pipeline_right` adjudications
  (the navigator-wrong shapes) by `(point, move, pipeline)` and materializes a
  corrective navigator lesson for each cluster at/above the graduation threshold
  (≥3 same-shape) into `navigator_lessons.jsonl` (a derived view over the
  append-only adjudications — full rewrite, recomputable, so it never loses
  evidence). **Consumer in the same chunk:** `decide()` injects those lessons
  into its prompt via the same recall-style seam worker slices use — no new
  model call, gated by the A/B flag `navigator.lesson_inject` (default off). The
  NAVIGATOR_DECIDED row records `lessons_injected`, so `--agreement` can compare
  move-agreement with vs without injection (worker-slice A/B methodology).
  Crystallization refreshes at cadence right after adjudication. 11 tests.
- **Trust policy function** rode V2 (its first consumer) — consumer-first.

Acceptance for the arc: one full organic cycle observable in the ledgers —
a friction class diagnosed, a change proposed and applied, its expectation
verified at cadence (at least one confirm AND at least one degrade→revert
exercised, synthetic if needed), and the calibration feedback visible in
`_record_suggestion_outcomes`. That cycle IS the "self-improving" claim the
README was repositioned to stop over-selling — when it demonstrably runs,
the headline can earn itself back.
