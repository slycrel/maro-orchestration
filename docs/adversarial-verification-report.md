# Adversarial Verification Report

**Generated:** 2026-05-12 (full synthesis — steps 1-8)
**Claims corpus:** 58 total (claims.json); top 8 by adversarial stakes subjected to full adversarial search
**Methods:** Codebase grep (`src/`), research literature search (steps 4-6), source-doc direct read
**Rating scale:** strong / moderate / weak / contested

---

## Executive Summary

Eight claims were pulled from 58 total claims in the corpus and adversarially searched for
contradicting evidence. Findings are grounded in direct grep results and primary source
literature. No claims were found to be weak. One claim is **CONTESTED** (citation inversion
confirmed). Five claims are **STRONG** (verified, no meaningful counter-evidence). Two claims
are **STRONG with nuance** (core stands; one sub-claim contested).

| Rating | Count | Claim IDs |
|--------|-------|-----------|
| **STRONG** | 5 | NEW-003, DISSENT-004, IMPL-009, ADVERSARIAL-003, CODE-001 |
| **STRONG (with nuance)** | 2 | THEORY-004, DISSENT-002 |
| **CONTESTED** | 1 | THEORY-009 |
| **WEAK** | 0 | — |

**Net result:** Prior research is largely well-founded. The prior documents were largely self-aware
about their gaps — most flagged issues were already self-acknowledged. The one live error is a
citation inversion in THEORY-009 that propagated an overstated block recommendation into live design
docs and needs immediate correction.

**Three gaps are immediately fixable (no design decision required):**
- `task['attempt']` never read by director (IMPL-009) — wire it in
- No pre/post skill confidence divergence check (DISSENT-004) — add IMPL-007
- Ghost symbol `enforce_constraint` in lat.md (CODE-001) — rename to `check_step_constraints`

---

## Rating Summary Table

| Rank | ID | Stakes | Rating | Claim stands? | Action priority |
|------|----|--------|--------|--------------|----------------|
| 1 | NEW-003 | CRITICAL | STRONG | YES | P2 — differentiate per-pillar tags |
| 2 | THEORY-004 | HIGH | STRONG (nuance) | YES (partial) | P2 — remove UCB/Gittins framing |
| 3 | DISSENT-004 | HIGH | STRONG | YES | P1 — add IMPL-007 |
| 4 | IMPL-009 | HIGH | STRONG | YES (gap larger) | P1 — wire task['attempt'] |
| 5 | ADVERSARIAL-003 | HIGH | STRONG | YES | P2 — clarify in arch docs |
| 6 | THEORY-009 | HIGH | **CONTESTED** | NO (as stated) | **P0 — citation correction NOW** |
| 7 | DISSENT-002 | MODERATE-HIGH | STRONG (caveat) | YES | P2 — annotate as [UNVALIDATED] |
| 8 | CODE-001 | MODERATE-HIGH | STRONG | YES | P1 — rename in lat.md |

---

## Claim-by-Claim Findings

---

### NEW-003 — All 8 Theoretical Pillars Are Unvalidated LLM Analogies
**Rating: STRONG** | Stakes: CRITICAL | Claim stands: YES

**Claim:** All 8 theoretical pillars (Duckworth grit, Kapur productive failure, Seligman learned
helplessness, UCB/Gittins, Boyd OODA, Hatano adaptive expertise, Fleming meta-d, Cleeremans RPT)
are unvalidated analogies from human cognition with zero empirical LLM-inference-time validation.

**Adversarial search:** No counter-evidence found. Source docs self-acknowledge:
`zoom-metacognition-adaptive-expertise.md:236` — *"The AI-to-human analogy is the single largest
unvalidated assumption in the whole document."*

**Key findings:**
- No pillar has peer-reviewed empirical validation in LLM inference-time agent execution
- Wang/Duan meta-RL (2016) is AI-native but architecturally misapplied: meta-RL requires gradient
  updates during training; Poe operates at inference-time on frozen weights
- Curriculum learning (Bengio, Voyager) applies to training gradient updates, not inference-time
  retry decisions
- Duckworth/Seligman/Boyd: categorically inapplicable (require persistent motivational state or
  training accumulation)
- Cleeremans RPT and Fleming meta-d: medium plausibility — concept is analogous but no
  validation study exists

**Differentiated pillar plausibility (NOT uniform — per-pillar tagging required):**

| Pillar | Transfer plausibility |
|--------|-----------------------|
| UCB-exploration concept | Moderate — RL-native; correct model is Thompson Sampling, not UCB/Gittins |
| Wang/Duan meta-RL | Moderate — AI-native; valid only in training regime; inference-time gap is categorical |
| Fleming meta-d | Moderate — confidence-accuracy gap is LLM-applicable; calibration audit required first |
| Cleeremans RPT | Moderate — prediction-error→redescription plausibly analogous to LLM next-token surprise |
| Kapur productive failure | Moderate — human cognitive science; curriculum-learning adjacent but different mechanism |
| Duckworth grit | Weak — requires persistent motivational state absent in LLM inference |
| Seligman learned helplessness | Weak — biological organism study; LLM has no persistent motivational state |
| Boyd OODA | Weak — military doctrine; agent-loop analogy is purely metaphorical |
| Hatano adaptive expertise | Weak — requires deliberate practice accumulation across training epochs, not inference |

**Action required (P2):**
Replace uniform `[DOMAIN-TRANSFER: UNVALIDATED]` tags with differentiated per-pillar plausibility
classifications from the table above in `productive_persistence.md` and
`zoom-metacognition-adaptive-expertise.md`.

---

### THEORY-004 — UCB/Gittins as Post-Hoc Rationalization for Retry Budget
**Rating: STRONG (nuance: "no valid bandit model" sub-claim is CONTESTED)** | Stakes: HIGH

**Claim:** Tiered retry structure (`_RETRY_THRESHOLD=3`, `_REDECOMPOSE_THRESHOLD=2`) has zero
UCB/Gittins computation; direction of causation is reversed (constants predate the research doc);
non-stationary rewards and non-Markovian structure violate both models' assumptions.

**Adversarial search:** Counter-evidence found on one sub-claim.

**Key findings:**
- `_RETRY_THRESHOLD=3` and `_REDECOMPOSE_THRESHOLD=2` are hardcoded integer literals in
  `agent_loop.py` — no computed allocation indices anywhere in `src/`
- Direction of causation reversed: constants predate the research doc that claims to
  "operationalize" UCB/Gittins
- UCB stationarity assumption violated: each retry injects different failure context
  (`agent_loop.py:629-633`), changing effective reward distribution per retry
- Gittins index requires Markovian reward structure; multi-step task execution is not cleanly
  Markovian
- **GENUINE PARTIAL COUNTER:** a valid bandit framing EXISTS — Thompson Sampling (no stationarity
  assumption) and Exp3 (adversarial bandit, O(√T) regret without stationarity) could correctly
  ground the retry intuition
- `productive_persistence.md:16` already mentions Thompson Sampling: *"naturally phases out
  low-value options as evidence accumulates"* — this framing is mathematically valid

**Sub-ratings:**

| Sub-claim | Rating |
|-----------|--------|
| UCB/Gittins framing is post-hoc rationalization | Strong — zero computation; constants predate theory; stationarity violated |
| No valid bandit model applies | **Contested** — Thompson Sampling and Exp3 are valid non-stationary alternatives |
| Retry budget 2-3 is arbitrary | Moderate — directionally reasonable as engineering heuristic regardless |

**Action required (P2):**
Remove *"operationalizes UCB/Gittins"* language. Replace with: *"engineering heuristic consistent
with Thompson Sampling intuition (phase out low-value options as evidence accumulates); correct
implementation would require tracking per-retry outcome distributions, not hardcoded integer
thresholds."*

---

### DISSENT-004 — Meta-Ignorance Gap: No Pre/Post Skill Confidence Comparison
**Rating: STRONG** | Stakes: HIGH | Claim stands: YES

**Claim:** No current code mechanism compares pre-step skill confidence with post-step outcome
(Fleming 2010 meta-ignorance: skill_score high, outcome diverges, no self-detection). Skills can
be over-promoted with no alarm.

**Adversarial search:** No counter-evidence found. Confirmed by direct grep across all `src/`.

**Key findings:**
- `grep 'skill_score.*outcome|outcome.*skill_score|confidence.*diverge'` across `src/` → 0 matches
- `record_skill_outcome` (`skills.py:829`) accepts a `confidence` kwarg but stores it into
  aggregate EMA (`utility_score`) — NOT a per-invocation pre/post comparison
- `SkillStats` (`skill_types.py:46`) tracks `success_rate` and `utility_score` as running
  averages — no per-invocation pre-step prediction vs post-step result stored
- `attribution.py:310` — failure attribution only; not a confidence divergence detector
- `knowledge_bridge.py:266-294` — skill→outcome graph edges; not a divergence check
- Inspector call sites (`quality_gate.py`, `heartbeat.py`, `evolver.py`) are background/periodic
  — none triggered per-step execution

**Warning:** The `record_skill_outcome.confidence` kwarg creates a false impression that divergence
is tracked. It feeds into EMA which loses per-invocation signal. This is the exact meta-ignorance
failure mode: compounding promotion of failing skills with no alarm.

**Action required (P1 — IMPL-007):**
Add meta-ignorance detector. Minimum: snapshot `skill.utility_score` before step execution; after
outcome, compare; if divergence > threshold, flag for evolver review. Wire into
`record_skill_outcome` or `_post_step_checks`.

---

### IMPL-009 — task['attempt'] Never Read by Director
**Rating: STRONG** | Stakes: HIGH | Claim stands: YES (gap larger than originally stated)

**Claim:** `task['attempt']` is incremented at `task_store.py:216` (durable, cross-restart) but
NOT READ by director on replan — director plans without cross-restart failure history.

**Adversarial search:** No counter-evidence found. Confirmed larger than stated.

**Key findings:**
- `grep "task\['attempt'\]\|task\.get.*attempt"` across entire `src/` → only 1 match:
  `task_store.py:216` (the write). **Zero reads anywhere.**
- `replan_count` (`agent_loop.py:522`) is a `LoopState` dataclass field initialized to `int=0` —
  in-memory only, session-local, resets on every restart
- `director_replan_count` (`agent_loop.py:267`) is `LoopContext` dataclass — also in-memory only
- `grep 'replan_count.*persist|persist.*replan_count'` → 0 matches — no persistence path exists
- `task['attempt']` is the ONLY durable cross-restart counter — and it is never read by director
  or any agent_loop planning path

**GAP IS LARGER THAN STATED:** Both the durable counter (`task['attempt']`) AND the session
counter (`replan_count`) are unavailable to director after any restart. Director is blind to ALL
attempt history after restart.

**Action required (P1 — highest-priority Wave 1 gap):**
1. Wire `task['attempt']` into director replan context
2. Consider persisting `replan_count` to task_store on session close
3. Tests to add: `director_receives_attempt_count_on_replan`,
   `replan_count_persists_across_restart`

---

### ADVERSARIAL-003 — Inspector Is Analytics-Only, Not an Execution-Path Gate
**Rating: STRONG** | Stakes: HIGH | Claim stands: YES

**Claim:** Inspector is NOT imported in `agent_loop.py`. Real execution gate chain is
`pre_flight → step_exec → _post_step_checks`. Inspector = background analytics only; cannot
block or mutate in-progress steps.

**Adversarial search:** No counter-evidence found. Confirmed by direct grep.

**Key findings:**
- `from inspector|import inspector` in `step_exec.py` → 0 matches. The only `'inspector'` string
  is `EXECUTE_TOOLS_INSPECTOR` — a tools-list constant for the inspector LLM persona, NOT a call
  to `inspector.py`
- `from inspector|import inspector` in `pre_flight.py` → 0 matches
- `_post_step_checks` (`agent_loop.py:1067-1215`) calls: `observe.write_event`,
  `scan_content_fn` (security), `claim_verifier` (hallucination check), `captains_log`, hooks —
  no inspector import anywhere in this chain
- Inspector imported in: `quality_gate.py`, `heartbeat.py`, `cli.py`, `poe.py`,
  `knowledge_lens.py`, `evolver.py` — none on the execution hot path
- Inspector signals reach evolver and quality_gate — influence future steps only, cannot affect
  current step execution

**Important distinction:** `EXECUTE_TOOLS_INSPECTOR` in `step_exec.py` defines which tools the
inspector LLM role receives during an LLM call — it does NOT call `inspector.py`. There IS an
inspector LLM role; it is separate from the Python inspector module.

**Action required (P2):**
No code correction needed. Update `ARCHITECTURE_OVERVIEW.md` and
`skills/arch-quality-selfimprove.md` to explicitly state: Inspector = background analytics, not
execution-path gate.

---

### THEORY-009 — Confidence-Gap Block Overstated (Citation Inversion) ⚠️ CONTESTED
**Rating: CONTESTED** | Stakes: HIGH | Claim stands: **NO (as stated)**

**Claim:** Signal 3 (confidence-accuracy decoupling) should be BLOCKED per Kadavath 2022, Guo
2017, Xiong 2023. All confidence-gap triggers are not yet actionable.

**Adversarial search:** Counter-evidence found. **Citation inversion confirmed.**

**Key findings:**
- **CITATION INVERSION — Kadavath 2022:** *"Language Models (Mostly) Know What They Know"* — core
  finding: LLM self-assessment (P(True)) is **well-calibrated** and improves with scale. The doc
  cites Kadavath as evidence of systematic miscalibration — **the opposite of what the paper
  concludes.** This is the primary citation for the block.
- **DOMAIN MISMATCH — Guo 2017:** Studied ResNets/DenseNets on image classification
  (CIFAR/ImageNet). No transformer data, no LLM data, no language tasks. Guo's fix was
  temperature scaling — a correctable result, not a permanent block condition.
- **XIONG 2023 GENUINELY APPLIES:** Supports concern about *verbalized* confidence (LLMs stating
  explicit percentages). This citation is real and correctly supports a partial block.
- **BEHAVIORAL vs VERBALIZED DISTINCTION:** None of the three papers address behavioral confidence
  signals (action patterns, revision frequency, tool selection). Only verbalized confidence
  (explicit self-reported percentages) is covered by Xiong.
- **PROPAGATION GAP:** `adversarial-verification.md:65-66` and `144-147` still contain the
  overstated full-block recommendation with the inverted Kadavath citation. This has NOT been
  propagated to a correction.

The calibration audit prerequisite remains valid regardless of citation errors.

**Sub-ratings:**

| Sub-claim | Rating |
|-----------|--------|
| Full block on all confidence-gap triggers | **Contested** — Kadavath inverted; Guo domain mismatch |
| Calibration audit as prerequisite | Strong — sound practice regardless of citation errors |
| Block on verbalized confidence triggers | Strong — Xiong 2023 genuinely supports this narrow block |
| Behavioral confidence-gap signals | Moderate — not covered by any paper; allow with calibration audit |

**Action required (P0 — citation error in live docs):**
1. Update `adversarial-verification.md:65-66` and `144-147` — narrow block to verbalized
   confidence only
2. Annotate `productive_persistence.md:430` — Kadavath 2022 actually *supports* behavioral
   confidence tracking; block applies to verbalized confidence only
3. Add separate implementation table row: behavioral confidence tracking = allowed; verbalized
   confidence triggers = blocked pending calibration

---

### DISSENT-002 — 60-85% Success Rate Zone Is Unvalidated for LLMs
**Rating: STRONG (caveat: no dedicated adversarial web search performed)** | Stakes: MODERATE-HIGH

**Claim:** The 60-85% success-rate desirable-difficulty zone is derived from human cognitive
science (Kapur, Duckworth) and has no empirical validation for LLM-agent architectures.

**Adversarial search:** No active web search performed (search capacity directed at higher-stakes
claims). Claim confirmed by source-doc self-acknowledgment.

**Key findings:**
- Source docs explicitly admit: `productive_persistence.md §6 Q8` — zone is used without
  LLM-agent validation
- Kapur productive failure and Duckworth grit are both rated **weak** for LLM transfer (NEW-003
  analysis)
- No AI-native benchmark (SWE-Bench, HumanEval, MMLU) is known to have explicitly targeted
  60-85% as an optimal zone for agent capability development

**Caveat:** No dedicated adversarial web search was performed. Rating relies on source-doc
self-acknowledgment and indirect evidence from NEW-003 pillar analysis. If this claim drives
major architecture decisions, commission a targeted search on *"LLM agent optimal challenge zone"*
and *"curriculum learning agent success rate"* before relying on it.

**Action required (P2):**
Tag all design uses of 60-85% zone as `[UNVALIDATED HUMAN ANALOGY]`. Add to open research
questions. Retain as working hypothesis — do not remove. Propose empirical calibration: vary task
difficulty, measure retention of capability on related tasks.

---

### CODE-001 — Ghost Symbol: enforce_constraint Does Not Exist
**Rating: STRONG** | Stakes: MODERATE-HIGH | Claim stands: YES

**Claim:** `enforce_constraint` function does not exist in `src/`. `lat.md/constraint-system.md:21`
references a ghost symbol. Real entry point is `check_step_constraints` (`constraint.py:391`).

**Adversarial search:** No counter-evidence found. Confirmed by direct grep across entire repo.

**Key findings:**
- `grep enforce_constraint` across entire repo → found ONLY in `lat.md/constraint-system.md:21`
  and `docs/md-claims-audit.md:34,175`. NOT found anywhere in `src/`
- Actual constraint entry points in `src/constraint.py`: `check_step_constraints` (line 391),
  `register_constraint` (line 487). No `enforce_constraint` anywhere.
- `docs/md-claims-audit.md` already flagged this as HIGH severity ghost symbol
- `lat.md` is read for design decisions — any engineer implementing constraint logic from lat.md
  will reference a nonexistent function and get a runtime error

**Action required (P1):**
Update `lat.md/constraint-system.md:21` — replace `enforce_constraint` with
`check_step_constraints (constraint.py:391)`. Also update any `docs/` cross-references that use
`enforce_constraint`. No implementation change required — function exists under correct name.

---

## Prioritized Action Plan

### P0 — Fix Before Using in Any Design (Citation Error / Contested)

| ID | Action | Target |
|----|--------|--------|
| THEORY-009 | Narrow Signal 3 block to verbalized confidence only; annotate Kadavath 2022 inversion | `adversarial-verification.md:65-66,144-147`; `productive_persistence.md:430` |

### P1 — Fix Before Next Coding Sprint (Implementation Gaps)

| ID | Action | Target |
|----|--------|--------|
| IMPL-009 | Wire `task['attempt']` into director replan context | `agent_loop.py`, `director.py`, `task_store.py` |
| DISSENT-004 | Add IMPL-007: pre/post skill confidence divergence detector | `skills.py`, `agent_loop.py` |
| CODE-001 | Replace ghost symbol `enforce_constraint` → `check_step_constraints` in lat.md | `lat.md/constraint-system.md:21` |

### P2 — Fix Before Architecture Decisions (Framing / Documentation)

| ID | Action | Target |
|----|--------|--------|
| THEORY-004 | Remove UCB/Gittins framing; replace with Thompson Sampling intuition | `productive_persistence.md`, research docs |
| NEW-003 | Replace uniform `[DOMAIN-TRANSFER: UNVALIDATED]` with per-pillar plausibility scores | `productive_persistence.md`, `zoom-metacognition-adaptive-expertise.md` |
| ADVERSARIAL-003 | Clarify Inspector = background analytics, not execution gate | `ARCHITECTURE_OVERVIEW.md`, `skills/arch-quality-selfimprove.md` |
| DISSENT-002 | Tag 60-85% zone uses as `[UNVALIDATED HUMAN ANALOGY]` | All design docs using this zone |

---

## Claims Requiring No Code Fix

- **ADVERSARIAL-003** — no code change; docs clarification only
- **DISSENT-002** — no removal; add annotation and open research question
- **NEW-003** — no code change; improve tagging in research docs

---

## Open Research Questions

1. **Empirical calibration of 60-85% zone for LLMs:** Run Poe on calibrated task sets at 40%,
   60%, 75%, 90% success rates; measure capability retention on related tasks. This would validate
   or refute DISSENT-002 with actual data.

2. **Thompson Sampling as principled bandit model:** If retry budget is to be principled,
   implement per-retry outcome-distribution tracking and compare empirically to hardcoded
   thresholds. Validates THEORY-004's "better framing" claim.

3. **Meta-ignorance detection empirics:** After IMPL-007 is added, measure divergence rate in
   production — how often does `skill.utility_score` diverge from actual outcomes? Determines
   whether the detector fires usefully or creates noise.

4. **Behavioral confidence signals:** No paper covers this for LLM agents. Experiment: track
   action revision frequency as behavioral confidence proxy; correlate with downstream outcome
   quality.

5. **Cleeremans RPT for LLMs:** Search literature on *"LLM next-token surprise"* and *"internal
   consistency"* — may surface empirical work validating or refuting this pillar.

---

## Meta-Observations

1. **Citation inversion is the most dangerous failure mode.** THEORY-009 and THEORY-004 both cite
   papers in ways that contradict or misapply the papers' actual findings. Cross-checking cited
   papers against their abstracts before including them in design docs is a cheap check that would
   have caught both.

2. **Source docs were largely self-aware about their gaps.** Most flagged issues were already
   self-acknowledged in the source documents. Adversarial verification confirmed the gaps, not
   discovered them. This is a good sign for source doc quality.

3. **Post-hoc rationalization is hard to detect from within.** THEORY-004's UCB/Gittins framing
   looks like grounding because the math is real and the intuition is directionally correct. The
   tell is the direction of causation: constants predate the theory doc. A timestamp check on
   cited theory vs code age surfaces this.

4. **"Gap larger than stated" is a common pattern.** Both IMPL-009 and DISSENT-004 turned out to
   be larger than the original claims: IMPL-009 revealed that `replan_count` also resets per
   restart; DISSENT-004 revealed the `confidence` kwarg creates a false impression of tracking.
   Adversarial search adds precision, not just confirmation.

5. **All claims contained a real residual concern.** None were fully refuted. Adversarial
   verification is not "find reasons to dismiss claims" — it's "calibrate the scope of the
   concern." The net effect is narrower, more actionable flags.

---

## Appendix: Artifact Index

| Artifact | Path | Contents |
|----------|------|----------|
| Full corpus | `projects/adversarial-verification-for-each-key/artifacts/claims.json` | 58 claims from source docs |
| Top 8 claims | `projects/adversarial-verification-for-each-key/artifacts/top-claims.json` | Ranked by adversarial stakes |
| Verify plan | `projects/adversarial-verification-for-each-key/artifacts/verify-plan.md` | Search strategy per claim |
| Contradictions 1-2 | `projects/adversarial-verification-for-each-key/artifacts/contradictions_1_2.md` | NEW-003, THEORY-004 |
| Contradictions 3-4 | `projects/adversarial-verification-for-each-key/artifacts/contradictions_3_4.md` | DISSENT-004, IMPL-009 |
| Contradictions 5-6 | `projects/adversarial-verification-for-each-key/artifacts/contradictions_5_6.md` | ADVERSARIAL-003, THEORY-009 |
| Structured ratings | `projects/adversarial-verification-for-each-key/artifacts/ratings.json` | Full structured data for all 8 claims |
