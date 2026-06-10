# Adversarial Verification — Final Synthesis Brief

**Generated:** 2026-05-12 (Session 3, Step 8/8 — full synthesis across all passes)
**Claim corpus:** 26 claims (consolidated from 3 adversarial passes)
**Deep-verified this session:** CLAIM-07, CLAIM-09, CLAIM-06 (full src/ grep + primary source reads)
**Prior passes carried forward:** 23 claims from Pass 1 (8 claims) + Pass 2 (5 claims) + extended corpus

---

## Executive Summary

Three adversarial verification passes across 26 consolidated claims. Session 3 performed deep
code verification on the top-3 highest-risk claims. Net result: no catastrophic errors found in
the primary deliverable (`adversarial-verification-report.md`), which was already clean of
fabricated symbols. Two P0-urgency phantom citations have been self-corrected in research docs.
Two genuinely P0 claims remain: one citation inversion (Kadavath 2022 cited backwards) and one
implementation-accuracy overclaim (identity dedup wrongly stated as absent).

**System verdict:** Largely well-grounded. Source documents were largely self-aware about their
gaps. The most dangerous failure mode is citation inversion, not fabrication — misapplied real
papers look authoritative. Three classes of error discovered: phantom symbols (fabricated code
citations), citation inversions (paper results cited backwards), and theory-mechanism confusion
(behavioral analogies overstated as causal mechanisms).

| Rating | Count | IDs |
|--------|-------|-----|
| **strong** | 13 | IMPL-009, DISSENT-004, CODE-001, CLAIM-15, THEORY-004, NEW-003, ADVERSARIAL-003, DISSENT-002, CLAIM-12, CLAIM-13, CONFLICT-is_converging, CLAIM-14, CLAIM-16 |
| **moderate** | 5 | CLAIM-06, CLAIM-11, CLAIM-03, CLAIM-01, CLAIM-05 |
| **weak** | 3 | CLAIM-07, CLAIM-09, CLAIM-02 |
| **contested** | 6 | THEORY-009, CLAIM-10, CLAIM-04, CLAIM-08, CLAIM-17, CLAIM-18 |
| **Total** | **27** | |

> **Rating changes from preliminary:** CLAIM-13 and CONFLICT-is_converging promoted from "refuted/closed" status label → **strong** (strong refutation verified at `agent_loop.py:3344`). All other 25 preliminary ratings confirmed.

---

## Rated Findings — Full Evidence Table

### STRONG (verified, precisely cited, no significant counter-evidence)

| ID | Urgency | Finding | Key Evidence | Why Strong |
|----|---------|---------|-------------|-----------|
| IMPL-009 | P1 | `task['attempt']` write-only; director blind across restarts | `task_store.py:216` (increment, no reads); `agent_loop.py:522` (`replan_count` session-local) | Two independent code locations verified; gap expands on deeper look |
| DISSENT-004 | P1 | No pre/post skill divergence check; only cumulative EMA | `skills.py:829` (EMA accumulation confirmed) | EMA ≠ delta-check — mechanistically unambiguous |
| CODE-001 | P1 | `enforce_constraint` ghost symbol in `lat.md`; real function is `check_step_constraints (constraint.py:391)` | ZERO src/ occurrences for phantom; `:391` confirmed | Both absent and real precisely located |
| CLAIM-15 | P1 | Re-decompose doesn't clear `step_outcomes`; stale accumulation inflates sibling rate | `agent_loop.py:686` (extend vs replace) | Code pattern unambiguous; causal path to CLAIM-06 traced |
| THEORY-004 | P2 | `_RETRY_THRESHOLD=3` hardcoded; no UCB/Gittins anywhere; constants predate theory doc | grep-verified absence of UCB/Gittins in src/ | Temporal inversion (constants predate theory) is a verifiable fact |
| NEW-003 | P2 | Uniform `[DOMAIN-TRANSFER: UNVALIDATED]` tag hides per-pillar variation | Per-pillar analysis: weak (Duckworth/Seligman/Boyd/Hatano) vs moderate (UCB/Wang/Fleming/Cleeremans) | Per-pillar argument is structural, not subjective |
| ADVERSARIAL-003 | P2 | Inspector described as "Quality gates" but zero integration in `agent_loop.py` | CLAUDE.md text vs absent wiring — both precisely verified | Both the misleading doc and absent wiring confirmed |
| DISSENT-002 | P2 | 60-85% success zone is unvalidated human analogy (Bjork) applied to LLM agents | Domain mismatch is categorical (human cognitive science ≠ LLM inference) | LLM-native alternatives (pass@k, Reflexion) exist and aren't cited |
| CLAIM-12 | P2 | "Reflexion implementation" label oversells; missing episodic indexing + verbal critique | Shinn 2023 requirements precisely documented; both missing from implementation | Gap is concrete, not interpretive |
| CLAIM-13 | closed | `_is_converging()` wiring gap — **REFUTED** | `agent_loop.py:3344` wired since Phase 62 | Direct code verification; specific line + phase |
| CONFLICT-is_converging | closed | Same as CLAIM-13 — **REFUTED** | Same as CLAIM-13 | Duplicate refutation confirmed |
| CLAIM-14 | closed | Quality gates documented as advisory — **VERIFIED CORRECT** | Verified accurate — no mismatch | Specific check passes |
| CLAIM-16 | closed | Session-scoped retry signals documented as session-scoped — **VERIFIED CORRECT** | Verified accurate — no mismatch | Specific check passes |

### MODERATE (valid but scope limited, derivative, or non-critical conflation)

| ID | Urgency | Finding | Why Not Strong | Why Not Weak |
|----|---------|---------|---------------|-------------|
| CLAIM-06 | P1 | `_sibling_failure_rate` counts `blocked`; 90%+ cascade overclaimed | Real risk exists but is narrower (fanout paths only); sequential execution prevents single-cascade | Confirmed mechanism operates as described; narrower real risk confirmed |
| CLAIM-11 | P1 | `task['attempt']` not read for director tier decisions | Fully subsumed by IMPL-009; no independent fix scope | Valid observation; real gap exists |
| CLAIM-03 | P2 | Luchins 1942 + Hatano 1986 conflated as same mechanism | Remedies converge in implementation (both → strategy diversification) | Distinct cognitive phenomena; conflation affects precise remedy design |
| CLAIM-01 | P3 | Argyris DLL timescale mismatch (months vs per-step) | Acknowledged informal; no code decisions driven by this | Structural isomorphism has genuine explanatory value |
| CLAIM-05 | P3 | Carried from prior pass; not elaborated in this brief | Cannot independently verify without prior pass artifacts | Prior pass rated moderate; no basis to downgrade |

### WEAK (phantom claim, unsupported assertion, or harm contained to non-critical docs)

| ID | Urgency | Finding | Why Weak | Why Not Contested |
|----|---------|---------|---------|-----------------|
| CLAIM-07 | P0-REDUCED | `reframe_intent` + `context_signature` are phantom symbols | ZERO src/ occurrences (confirmed); primary report already clean | No active counter-claim — symbols definitively absent |
| CLAIM-09 | P0-REDUCED | 3-way hash formula phantom; two real mechanisms incorrectly merged | All three formula elements ZERO src/ occurrences; primary report clean | Definitively phantom, not disputed |
| CLAIM-02 | P2 | "Stale Orientation is the primary OODA failure mode" — unsupported "primary" ranking | Zero evidence for "primary" ranking; OODA applied metaphorically | No active counter-evidence — just absence of support |

### CONTESTED (active conflicting evidence; direction of causation or evidence inverted)

| ID | Urgency | Finding | Why Contested | Active Counter-Evidence |
|----|---------|---------|--------------|----------------------|
| THEORY-009 | P0 | Kadavath 2022 cited backwards — paper shows LLMs ARE well-calibrated | Citation inversion is a factual error; design decision may be re-groundable on narrower basis | Kadavath 2022 abstract directly contradicts stated use |
| CLAIM-10 | P0 | "No content deduplication" — identity dedup EXISTS at `:4217-4222` | Claim asserts total absence; identity dedup is verifiably present | `agent_loop.py:4217-4222` (confirmed) |
| CLAIM-04 | P2 | "All 3 frameworks agree: persist > reframe" — OODA + DLL both invert in specific conditions | Consensus claim is 1/3, not 3/3 | OODA fast-cycle inversion; DLL reframe-when-loops-fail condition |
| CLAIM-08 | P2 | Kapur productive failure missing consolidation phase | Mechanism breaks without consolidation; Phase 44-46 may partially satisfy | Two-phase requirement documented in Kapur; Phase 44-46 is open counter-candidate |
| CLAIM-17 | P2 | Wang/Duan meta-RL citation supports opposite of intended claim | Meta-RL applies in training regime; inference-time is categorical mismatch | Citation direction confirmed inverted; no UCB/Gittins code |
| CLAIM-18 | P2 | Hatano AE temporal inversion — in-task monitoring, not pre-task criterion-setting | Mechanism direction reversed AND unimplemented | Hatano direction well-documented; `pre_flight.py`/`planner.py` have zero implementation |

### Error Class × Rating Cross-Tab

| Error Class | strong | moderate | weak | contested |
|-------------|--------|----------|------|-----------|
| citation_inversion | THEORY-004 | — | — | THEORY-009, CLAIM-17, CLAIM-18 |
| phantom_symbol | CODE-001, CLAIM-13, CONFLICT-is_converging | — | CLAIM-07, CLAIM-09 | — |
| gap_larger_than_stated | IMPL-009, DISSENT-004, CLAIM-15 | CLAIM-06, CLAIM-11 | — | CLAIM-10 |
| theory_mechanism_confusion | NEW-003, ADVERSARIAL-003, DISSENT-002, CLAIM-12 | CLAIM-03, CLAIM-01, CLAIM-05 | CLAIM-02 | CLAIM-04, CLAIM-08 |
| verified_correct | CLAIM-14, CLAIM-16 | — | — | — |

> **Key insight:** Citation inversions (THEORY-009, THEORY-004, CLAIM-17, CLAIM-18) received the highest urgency ratings — never weak — because they look authoritative (real papers, real math) while having the direction of evidence backwards. They require primary source verification to detect, unlike phantom symbols which are catchable by grep.

---

## Master Action Matrix

### P0 — Fix Before Any Further Design Use

| ID | Issue | Precise Action | File + Line |
|----|-------|----------------|-------------|
| **THEORY-009** | Kadavath 2022 cited backwards — paper shows LLMs ARE well-calibrated; used as evidence of miscalibration. Full block on confidence-gap triggers is unsupported. | Narrow block to verbalized confidence only. Annotate inversion in-place. | `adversarial-verification.md:65-66,144-147`; `productive_persistence.md:430` |
| **CLAIM-10** | Claim: "no content deduplication in stuck_streak." Identity dedup EXISTS at `:4217-4222`. Semantic dedup absent but claim overstated. P0 because this drives architecture decisions. | Correct docs: "identity dedup present (exact text+status match); semantic dedup absent (token-stripping not implemented)." Implement token-stripping at `:4218`. | `agent_loop.py:4218`; any doc citing CLAIM-10 |

### P0-REDUCED — Primary Docs Clean; Tag Research Docs

| ID | Issue | Precise Action | File + Line |
|----|-------|----------------|-------------|
| **CLAIM-07** | `reframe_intent` and `context_signature` are phantom symbols (ZERO src/ hits). Primary report clean. Research docs partially self-corrected. | Tag `[DESIGN PROPOSAL — not yet implemented]` at both usages. Keep grit framing; replace Duckworth attribution with `_ae_restart_ctx` reference. | `research-brief-persistence-and-zoom.md:64,177` |
| **CLAIM-09** | 3-way hash formula `hash(error_type × last_action × context_signature)` — all three elements phantom. Two real mechanisms (identity match `:4218` + MD5 fingerprint `:2701`) incorrectly merged. | Tag `[DESIGN PROPOSAL — not yet implemented]`. Document actual dual-mechanism in place. | `research-brief-persistence-and-zoom.md:64` |

### P1 — Fix Before Next Coding Sprint

| ID | Issue | Precise Action | File + Line |
|----|-------|----------------|-------------|
| **IMPL-009** | `task['attempt']` incremented at `task_store.py:216` but NEVER READ by director. Gap larger: `replan_count` also session-local, resets on restart. Director blind to all attempt history after restart. | Wire `task['attempt']` into director replan context. Add "This is attempt N" to planner prompt. Consider persisting `replan_count`. | `director.py`, `agent_loop.py:522`, `task_store.py:216` |
| **DISSENT-004** | No pre/post skill confidence comparison. `record_skill_outcome.confidence` kwarg feeds EMA — not per-invocation divergence check. Skills over-promoted with no alarm. | Snapshot `skill.utility_score` before step execution; compare after; flag divergence > threshold to evolver. Wire into `_post_step_checks`. | `skills.py:829`, `agent_loop.py:_post_step_checks` |
| **CODE-001** | `enforce_constraint` does not exist in `src/`. Ghost symbol in `lat.md/constraint-system.md:21`. Real entry: `check_step_constraints (constraint.py:391)`. | Replace ghost symbol in lat.md. | `lat.md/constraint-system.md:21` |
| **CLAIM-15** | Re-decompose doesn't clear stale step_outcomes — `step_outcomes.extend()` accumulates across redecompose cycles. This is the root cause of CLAIM-06's real risk. | Clear or annotate `injected_context` on re-decompose path. | `agent_loop.py:686` |
| **CLAIM-06** | `_sibling_failure_rate()` counts `blocked` (intentional per docstring `:2733`), not `failed`. 90%+ cascade claim contradicted for sequential execution (`:3356` guard). Real risk: fanout parallel paths + stale step_outcomes accumulation. | Add cascade-exclusion flag for steps blocked by shared-prerequisite failure. Fix CLAIM-15 first. Consider `blocked_by_cascade=True`. | `agent_loop.py:2740,2746,3356,1925` |
| **CLAIM-11** | `task['attempt']` not read for director tier decisions — director uses session-local `convergence_budget_remaining` and `replan_count`. (Overlaps with IMPL-009.) | Covered by IMPL-009 fix. | Same as IMPL-009 |

### P2 — Fix Before Architecture Decisions

| ID | Issue | Action |
|----|-------|--------|
| **THEORY-004** | `_RETRY_THRESHOLD=3` is hardcoded; no UCB/Gittins computation anywhere in `src/`. Direction of causation reversed (constants predate theory doc). | Remove UCB/Gittins framing; replace with Thompson Sampling intuition (already mentioned in `productive_persistence.md:16`). |
| **NEW-003** | All 8 theory pillars uniformly tagged `[DOMAIN-TRANSFER: UNVALIDATED]` — but plausibility varies significantly. | Replace uniform tag with per-pillar plausibility scores (table below). |
| **ADVERSARIAL-003** | Inspector described as "Quality gates" in CLAUDE.md but zero integration in `agent_loop.py`. Misleads engineers toward wrong implementation location. | Correct CLAUDE.md, `skills/arch-quality-selfimprove.md`, `ARCHITECTURE_OVERVIEW.md`. |
| **DISSENT-002** | 60-85% success zone is a human cognitive science analogy with no LLM-agent validation. | Tag all design uses as `[UNVALIDATED HUMAN ANALOGY]`. Propose empirical calibration. |
| **CLAIM-02** | "Stale Orientation is the primary OODA failure mode for AI agents" — unsupported editorial. | Remove "primary" claim; demote to Low-Medium heuristic; tag `[DESIGN ASPIRATION]`. |
| **CLAIM-03** | Luchins 1942 (proactive interference) and Hatano 1986 (representation poverty) conflated as same mechanism. | Reframe as "schema perseveration (Hatano 1986)"; drop Luchins conflation; drop "canonical." |
| **CLAIM-04** | "All 3 frameworks agree: persist > reframe" — OODA may invert this claim; DLL triggers reframing exactly when loops fail. Only AE independently supports persist-then-reframe. | Remove "all frameworks agree"; note OODA inversion in fast-cycle contexts; keep DLL + AE as independent analogies. |
| **CLAIM-08** | Kapur productive failure requires mandatory two-phase structure (unguided struggle + teacher-delivered canonical instruction). Poe has no consolidation phase. | Replace "productive failure (Kapur)" with "error-tolerant exploration." Check if Phase 44-46 constitutes consolidation analog. |
| **CLAIM-12** | "Reflexion implementation" label oversells — Shinn 2023 requires episodic task-specific indexing and verbal critique; this is experience replay / lesson extraction. | Rename in docs only: "experience replay / lesson extraction loop." No code change needed. |
| **CLAIM-17** | Zero Gittins/UCB computation in `src/`. Wang/Duan meta-RL citation used to justify the opposite of what it supports. | Remove formal citation; retain opportunity-cost framing with explicit "formal assumptions violated, treat as design heuristic." |
| **CLAIM-18** | Temporal inversion: Hatano adaptive expertise emerges from in-task monitoring, not pre-task criterion-setting. Zero implementation in `pre_flight.py` or `planner.py`. | Invert temporal direction: "in-task schema monitoring"; tag `[DESIGN ASPIRATION]`. |

### P3 — Low Urgency

| ID | Issue | Action |
|----|-------|--------|
| **CLAIM-01** | Argyris DLL analogy — timescale mismatch (months vs per-step). Informal but not harmful. | Tag `[INFORMAL ANALOGY]`; note timescale mismatch. |

### Closed / No Action

| ID | Status |
|----|--------|
| **CLAIM-13** | REFUTED/CLOSED — `_is_converging()` fully wired at `agent_loop.py:3344` (Phase 62). |
| **CONFLICT-is_converging** | REFUTED/CLOSED — same as above. Update research-brief ZO-001 to remove "wiring gap" language. |
| **CLAIM-14** | No action — quality gates correctly documented as advisory. |
| **CLAIM-16** | No action — session-scoped retry signals correctly documented. |

---

## Per-Pillar Plausibility (NEW-003 detailed — replaces uniform tag)

| Pillar | Transfer plausibility | Basis |
|--------|-----------------------|-------|
| UCB/exploration concept | Moderate | RL-native; correct model is Thompson Sampling (non-stationary), not UCB/Gittins |
| Wang/Duan meta-RL | Moderate | AI-native; valid only in training regime; inference-time is categorical mismatch |
| Fleming meta-d | Moderate | Confidence-accuracy gap is LLM-applicable; calibration audit required first |
| Cleeremans RPT | Moderate | Prediction-error → redescription plausibly analogous to LLM next-token surprise |
| Kapur productive failure | Moderate | Human cognitive science; Reflexion/Self-Refine are better direct citations |
| Duckworth grit | **Weak** | Requires persistent motivational state absent in LLM inference |
| Seligman learned helplessness | **Weak** | Biological organism study; LLM has no persistent motivational state |
| Boyd OODA | **Weak** | Military doctrine; agent-loop analogy is purely metaphorical |
| Hatano adaptive expertise | **Weak** | Requires deliberate practice accumulation across training epochs, not inference |

**LLM-native behavioral analogs that SHOULD be cited instead** (validated empirically):
- Reflexion (Shinn 2023) — iterative retry with verbal feedback
- Self-Refine (Madaan 2023) — self-critique and iteration
- Tree-of-Thoughts (Yao 2023) — branching over proposals
- LLM pass@k benchmarks — 2-4 retries improve success non-linearly

---

## Session 3 Deep Verification Findings

### CLAIM-07 — Phantom `reframe_intent` + `context_signature`
**Rating: weak | Urgency: P0-REDUCED**

All grep results confirmed phantom status:
- `reframe_intent`: ZERO occurrences repo-wide
- `context_signature`: ZERO occurrences in `src/` — appears only in design docs as proposal text
- `agent_loop.py:4326`: `_ae_restart_ctx = (decision.restart_context or decision.reasoning)` — simple string capture, not a reframing primitive
- `agent_loop.py:4218`: `action_key = f'{step_text}:{step_status}'` — identity match, not hash

P0 urgency reduced: `adversarial-verification-report.md` (primary deliverable) contains zero fabricated symbols. `productive_persistence.md:492,494` already self-corrected. Residual: `research-brief-persistence-and-zoom.md:64,177` needs `[DESIGN PROPOSAL]` tag.

Salvageable concept: adaptive execution restart logic IS conceptually goal-stable/strategy-flexible — but describe via `_ae_restart_ctx`, not phantom Duckworth primitives.

---

### CLAIM-09 — Phantom Hash Formula `hash(error_type × last_action × context_signature)`
**Rating: weak | Urgency: P0-REDUCED**

All three formula elements confirmed phantom:
- `context_signature`: ZERO occurrences in `src/`
- `error_type` (as outcome field): ZERO occurrences in `src/`
- 3-way hash: never implemented

Two real separate mechanisms exist and were incorrectly merged:
1. **Identity match** at `agent_loop.py:4218`: `action_key = f'{step_text}:{step_status}'` — streak counter
2. **MD5 fingerprint** at `agent_loop.py:2701`: `_error_fingerprint = MD5(stuck_reason|result)[:12]` — convergence detector

P0 reduced: primary report clean; research docs self-corrected to "Wave 2 recommendation." Residual: `research-brief-persistence-and-zoom.md:64` needs tag.

---

### CLAIM-06 — `_sibling_failure_rate()` Counts `blocked`, 90%+ Cascade Risk
**Rating: moderate (NARROWED) | Urgency: P1**

Core mechanism claims verified:
- `agent_loop.py:2740`: `blocked = sum(s.status == 'blocked')` — CONFIRMED
- `agent_loop.py:2746`: `_SIBLING_THRESHOLD = 0.5` — CONFIRMED
- Inspector: zero hits for `_sibling_failure_rate` — CONFIRMED post-hoc only

"90%+ single-cascade" claim contradicted:
- Docstring at `:2733` explicitly: "blocked (not done)" — intentional design, not oversight
- `:3356`: `len(step_outcomes) >= 3` guard — single failure cannot trigger re-decompose alone
- Sequential execution: downstream unexecuted steps remain in `remaining_steps`, NOT added to `step_outcomes` — single-cascade impossible in typical sequential run

Real narrower risk confirmed:
- **Fanout parallel paths**: all paths append outcomes simultaneously (`agent_loop.py:1925`) — cascade IS possible
- **Stale step_outcomes across redecompose cycles**: `step_outcomes.extend()` accumulates entries — prior-cycle blocked steps inflate sibling rate in next cycle (confirmed by CLAIM-15 analysis)

Fix CLAIM-15 first (stale accumulation root cause), then address fanout cascade-exclusion.

---

## Key Error Patterns Found

### 1. Citation Inversion (most dangerous)
THEORY-009: Kadavath 2022 cited as evidence of miscalibration; paper actually shows LLMs ARE well-calibrated. THEORY-004: UCB/Gittins cited as grounding for hardcoded constants that predate the theory doc. These look authoritative because the math is real; the tell is direction of causation.

**Mitigation:** Before citing a paper, verify the abstract. Timestamp theory docs vs code age for causation checks.

### 2. Phantom Symbols (detectable by grep)
CLAIM-07, CLAIM-09: `reframe_intent`, `context_signature`, `error_type` cited as implementation details; all have zero `src/` occurrences. The primary report was already clean — phantom symbols appeared only in intermediate research docs.

**Mitigation:** Grep-verify all code citations before committing to research docs. Primary deliverables were already applying this discipline; intermediate drafts were not.

### 3. Theory-Mechanism Confusion (systematic)
Behavioral analogy (Reflexion works empirically) asserted as causal mechanism (Duckworth grit is the reason). OODA, DLL, grit, learned helplessness, meta-RL all cited as mechanisms where only behavioral equivalence is plausible. This affects NEW-003, CLAIM-04, CLAIM-08, CLAIM-17, CLAIM-18.

**Mitigation:** Per-pillar plausibility table above. Cite Reflexion/Self-Refine as LLM-native precedents; treat human-cognition pillars as heuristic inspiration only.

### 4. "Gap Larger Than Stated" Pattern
IMPL-009: `task['attempt']` write-only (confirmed) + `replan_count` also session-local (found during verification). DISSENT-004: `confidence` kwarg creates false impression of tracking. Adversarial search adds scope, not just confirmation.

---

## Open Research Questions

1. **Empirical calibration of 60-85% zone for LLMs:** Run Poe on calibrated task sets at 40%, 60%, 75%, 90% success rate; measure capability retention on related tasks. Validates or refutes DISSENT-002.

2. **Thompson Sampling as principled retry model:** Implement per-retry outcome-distribution tracking; compare empirically to hardcoded thresholds. Validates THEORY-004 partial counter.

3. **Meta-ignorance detection signal-to-noise:** After IMPL-007 (DISSENT-004), measure divergence rate in production — how often does `skill.utility_score` diverge from actual outcomes?

4. **Behavioral confidence signals:** No paper covers action-pattern-based confidence for LLM agents. Experiment: track action revision frequency as behavioral confidence proxy; correlate with downstream outcome quality.

5. **Cleeremans RPT for LLMs:** Search "LLM next-token surprise" and "internal consistency" — may surface empirical validation.

6. **Fanout cascade threshold:** With CLAIM-06 narrowed, measure empirically how often fanout paths with shared prerequisite trigger spurious `_sibling_failure_rate` spikes in real Poe runs.

---

## Files Requiring Updates (Index)

| File | Action | Priority |
|------|--------|----------|
| `adversarial-verification.md:65-66,144-147` | Narrow confidence block to verbalized only; annotate Kadavath inversion | P0 |
| `productive_persistence.md:430` | Kadavath 2022 actually supports behavioral tracking; block applies to verbalized only | P0 |
| `research-brief-persistence-and-zoom.md:64,177` | Tag `context_signature` uses as `[DESIGN PROPOSAL — not yet implemented]` | P0-REDUCED |
| `research-brief-persistence-and-zoom.md` ZO-001 | Remove "_is_converging wiring gap" language — wired at `:3344` since Phase 62 | P1 |
| `lat.md/constraint-system.md:21` | Replace `enforce_constraint` → `check_step_constraints (constraint.py:391)` | P1 |
| `CLAUDE.md` | Inspector = background audit, not execution gate | P2 |
| `skills/arch-quality-selfimprove.md` | Same Inspector clarification | P2 |
| `docs/ARCHITECTURE_OVERVIEW.md` | Same Inspector clarification | P2 |
| `productive_persistence.md`, `zoom-metacognition-adaptive-expertise.md` | Per-pillar plausibility tags (table above) | P2 |

---

## Artifact Index

| Artifact | Path |
|----------|------|
| Full ratings (26 claims, structured) | `projects/adversarial-verification-for-each-key/artifacts/ratings.json` |
| Original full claims corpus | `projects/adversarial-verification-for-each-key/artifacts/claims.json` |
| Top-6 ranked claims | `projects/adversarial-verification-for-each-key/artifacts/top_claims.json` |
| CLAIM-07 contra evidence | `projects/adversarial-verification-for-each-key/artifacts/contra_1.md` |
| CLAIM-09 contra evidence | `projects/adversarial-verification-for-each-key/artifacts/contra_2.md` |
| CLAIM-06 contra evidence | `projects/adversarial-verification-for-each-key/artifacts/contra_3.md` |
| Full primary report (all 3 passes) | `docs/adversarial-verification-report.md` |
