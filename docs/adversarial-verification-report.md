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

---

---

# Second Adversarial Verification Pass — 2026-05-12

**Claims corpus:** 114 claims extracted from 3 source files (`adversarial-verification.md` [58], `research-brief-persistence-and-zoom.md` [28], `productive_persistence_summary.md` [28])
**Top-risk claims verified:** 5 (selected by cross-file conflict severity + blast radius + correction urgency)
**Methods:** Codebase grep (3/5 claims), literature adversarial review (1/5), cross-file conflict resolution (1/5)

---

## Executive Summary (Second Pass)

This pass expanded the claim corpus from 58 to 114 by ingesting two additional research docs, resolved a direct cross-file contradiction via grep, and confirmed two concrete code defects. The high-level finding from Pass 1 holds — claims are generally well-grounded — but Pass 2 surfaces one REFUTED claim (Inspector as execution gate) and one confirmed write-only implementation gap (`task['attempt']`), both actionable without design decisions.

| Verdict | Count | Claim IDs |
|---------|-------|-----------|
| VERIFIED (code-confirmed) | 1 | CONFLICT-`_is_converging` |
| REFUTED (code-confirmed) | 1 | ADVERSARIAL-003 |
| VERIFIED WITH SCOPE LIMITATION | 1 | NEW-003 |
| VERIFIED AS REAL GAP | 1 | IMPL-009 |
| UNCERTAIN (needs empirical validation) | 1 | THEORY-004 |

**Three highest-impact corrections:**
1. Update architecture docs to clarify Inspector is a post-hoc audit worker, not an execution-path gate.
2. Wire `task['attempt']` into director/planner context (write-only field confirmed by grep).
3. Reframe all theory citations as behavioral analogs (Reflexion/Self-Refine), not causal mechanisms (Duckworth/Kapur/meta-RL).

---

## Claim Verdicts (Second Pass)

---

### CONFLICT-`_is_converging` — Cross-File Contradiction Resolved
**Verdict: VERIFIED (claims-3 correct, claims-2 factually wrong)** | Confidence: strong (code-verified)

**Contradiction:** `research-brief-persistence-and-zoom.md/ZO-001` asserts `_is_converging` is "a wiring gap — not wired into retry logic." `productive_persistence_summary.md/IS-S-001` asserts it "is used in retry decisions."

**Code evidence:**
```
grep -n '_is_converging' src/agent_loop.py
  2718: def _is_converging(fingerprints)               # definition
  3344: converging = _is_converging(fingerprints)       # call in Phase 62 decision algorithm
```

Line 3344 is explicitly labeled `Phase 62: Convergence-aware decision algorithm`. The research-brief claim is code-refuted. Root cause: research-brief was written when the function was aspirational; summary reflects current code state.

**Action (P1):** Update `docs/research-brief-persistence-and-zoom.md` ZO-001 — remove "wiring gap" language. Add: *"Currently wired at `agent_loop.py:3344` (Phase 62) as of 2026-05-12."*

**Policy implication:** Code-status claims in design docs must be verified by grep before use in design decisions. Docs decay; grep is authoritative.

---

### ADVERSARIAL-003 — Inspector Described as Quality Gate, Not Wired as One
**Verdict: REFUTED** | Confidence: strong (code-verified)

This finding corroborates and extends Pass 1 findings. Inspector has zero integration in `agent_loop.py`:

```
grep -n 'inspector' src/agent_loop.py   → NO MATCHES
grep -n 'inspector' src/step_exec.py   → Lines 452, 468, 483 (role metadata only)
```

Inspector appears in `step_exec.py` only as role-dispatch metadata (`EXECUTE_TOOLS_INSPECTOR`). It is not called as a quality gate — it is a worker role that runs post-hoc. Inspector signals reach evolver for future-step coaching; they cannot affect the current step.

CLAUDE.md architecture table describes `inspector.py — Quality gates — friction detection` — this implies real-time integration, which the code disproves.

**Design consequence:** Engineers reading current architecture docs will add quality-gate logic to `inspector.py` (wrong location). Inspector improvements won't improve runtime quality until explicitly wired into `step_exec` or `agent_loop`.

**Action (P0, doc):** Update `CLAUDE.md`, `skills/arch-quality-selfimprove.md`, `docs/ARCHITECTURE_OVERVIEW.md`:
> *"Inspector is a background audit worker, not an execution-path quality gate. Real-time gating is in `pre_flight` and `step_exec`. Inspector findings feed into evolver for future-step coaching, not current-step execution."*

**Separate decision (out of scope):** Should Inspector be wired into the hot path? If yes, that is a feature task; file separately.

---

### NEW-003 — AI-to-Human Theory Pillars Unvalidated in LLM Domain
**Verdict: VERIFIED WITH SCOPE LIMITATION** | Confidence: strong

(Corroborates Pass 1 / NEW-003 finding with additional resolution.)

**Behavioral analogs confirmed by LLM-native literature:**
- Reflexion (Shinn 2023): iterative retry with verbal feedback → measurable improvement
- Self-Refine (Madaan 2023): self-critique and iteration → validated
- Tree-of-Thoughts (Yao 2023): branching over proposals → non-linear gains
- LLM pass@k benchmarks: 2-4 retries improve success non-linearly → consistent with productive struggle framing
- UCB/Gittins exploration-exploitation: information-theoretic, biology-independent → applies

**Mechanistic claims that DO NOT transfer to stateless inference-time LLMs:**
- Kapur productive failure → requires schema building; LLMs are stateless across API calls
- Bjork desirable difficulty (60-85% zone) → calibrated on human memory consolidation timescales; LLM retries happen in seconds
- Duckworth grit → dispositional trait (months-years); per-task retry budgets (3-5 attempts) are a category mismatch
- Meta-RL "learning to learn" → requires weight updates; frozen inference-time LLMs cannot do this

**Summary:** System works for the right reasons (breadth-first search over proposal space, incremental feedback) but sometimes with wrong framing. Behavioral equivalence is true; mechanism equivalence is false.

**Action (P2):** Tag all theory citations as `[BEHAVIORAL_ANALOG: CONFIRMED]` or `[MECHANISM: UNVALIDATED]`. Cite Reflexion/Self-Refine as the direct LLM precedent. Remove temporal-scale claims about consolidation. (Supplements Pass 1 per-pillar tagging recommendation.)

---

### IMPL-009 — `task['attempt']` Written but Never Read by Director
**Verdict: VERIFIED AS REAL GAP** | Confidence: strong (code-verified)

(Corroborates Pass 1 / IMPL-009 finding. Same code evidence. Included here for cross-pass confirmation record.)

```
grep task['attempt'] across all src/*.py:
  task_store.py:69   → task['attempt'] = 0        (init)
  task_store.py:216  → task['attempt'] += 1        (increment)
  [all other src/]   → ZERO READ SITES
```

Two independent research documents (Pass 1 / IMPL-009 and Pass 2 / DA-S-001) both independently flagged this gap. Grep confirms both. The field is a dead letter: initialized, incremented, silently discarded on every restart.

Note: `orch_items.py::RunRecord.attempt` is a separate field at run-scope, not task-scope. The two are unrelated.

**Consequence:** Director cannot implement adaptive retry budgets, escalation policies, or learned constraints based on cross-restart history.

**Action (P1, LOW effort):**
1. Pass `task['attempt']` to `director._replan()` kwargs.
2. Include in planner prompt ("This is attempt N of this plan").
3. Add test: `director_observes_attempt_count_on_restart`.

---

### THEORY-004 — UCB/Gittins Grounding for Retry Thresholds
**Verdict: UNCERTAIN** | Confidence: inferred (45%)

Retry thresholds (N=2 strategy, N=2-3 tactic) are asserted as UCB/Gittins-grounded. No UCB/Gittins computation exists anywhere in `src/`. Theoretical critique:
- UCB requires stationary reward distributions — agent task difficulty is non-stationary.
- Gittins Index requires Markovian reward structure — multi-step planning has path dependencies.

However, the thresholds might be empirically valid heuristics for entirely different reasons. Whether N=2-3 is actually optimal has not been tested via ablation on real Poe missions. No LLM-agent retry threshold literature was found.

This verdict is **inferred** (missing evidence), not a direct contradiction. Confidence elevates to REFUTED only if literature explicitly shows N=2-3 is suboptimal, or to VERIFIED if ablation confirms it.

**Action (P2):** Label thresholds as `[ENGINEERING_HEURISTIC: empirically unvalidated]` in all docs. Remove UCB/Gittins grounding language. See Pass 1 Thompson Sampling reframing recommendation (THEORY-004 there has more detail).

---

## Cross-Cutting Findings (Second Pass)

### CC-1: Theory Claims — Mechanism vs Behavior Confusion (HIGH)
Systematic framing error across all three source docs: mechanism equivalence asserted (Duckworth, Kapur, meta-RL) where only behavioral analogy is validated (Reflexion, Self-Refine, ToT). Audit all THEORY claims and retag.

### CC-2: Two Concrete Code Defects Confirmed (HIGH)
Neither requires design decisions — both are fixable directly:
- Inspector misdescribed as execution gate → doc correction
- `task['attempt']` write-only → thread to director

### CC-3: Research Docs Drift from Code Reality (MEDIUM)
`_is_converging` conflict proves a well-cited research doc contained a false code-status claim. Policy: grep before citing code status. Add `code_verified_at` timestamps to code-status claims. Schedule quarterly re-verification pass.

### CC-4: LLM-Agent Empirical Evidence Gap (MEDIUM)
Multiple theory claims cannot be fully verified because LLM-agent empirical studies are sparse. Behavioral evidence exists (Reflexion, Self-Refine, ToT). UCB/Gittins validated retry counts, Bjork zone applied to token budgets, and per-task ablations do not exist.

**Action:** Create `docs/llm-agent-empirical-evidence-gaps.md` listing all theory claims and marking which have peer-reviewed LLM-agent validation.

---

## Second Pass Action Matrix

| Priority | Action | Files |
|----------|--------|-------|
| P0 | Correct Inspector architecture description (post-hoc, not gate) | `CLAUDE.md`, `skills/arch-quality-selfimprove.md`, `docs/ARCHITECTURE_OVERVIEW.md` |
| P1 | Wire `task['attempt']` into director/planner | `src/task_store.py`, `src/director.py`, `tests/` |
| P1 | Remove `_is_converging` wiring-gap language from research-brief | `docs/research-brief-persistence-and-zoom.md` |
| P2 | Audit/retag THEORY claims for mechanism vs behavioral analog | all three source docs |
| P2 | Label retry thresholds as `[ENGINEERING_HEURISTIC]` | docs citing UCB/Gittins |
| P2 | Create `docs/llm-agent-empirical-evidence-gaps.md` | new file |
| P3 | Quarterly grep re-verification cadence for code-status claims | process |

---

## Second Pass Artifact Index

| Artifact | Path | Contents |
|----------|------|----------|
| Claims file 1 | `projects/adversarial-verification-for-each-key/artifacts/claims-1.json` | 58 claims from `adversarial-verification.md` |
| Claims file 2 | `projects/adversarial-verification-for-each-key/artifacts/claims-2.json` | 28 claims from `research-brief-persistence-and-zoom.md` |
| Claims file 3 | `projects/adversarial-verification-for-each-key/artifacts/claims-3.json` | 28 claims from `productive_persistence_summary.md` |
| Top 5 claims | `projects/adversarial-verification-for-each-key/artifacts/top-claims.json` | Ranked by conflict severity + blast radius |
| Contradictions | `projects/adversarial-verification-for-each-key/artifacts/contradictions.json` | Adversarial evidence for top 5 |
| Final classifications | `projects/adversarial-verification-for-each-key/artifacts/claims-verified.json` | Verdict + rationale + recommended actions |

---

---

# Third Adversarial Verification Pass — 2026-05-12 (Session 4)

**Source:** `docs/adversarial-verification-brief.md` — 27 claims consolidated from 3 prior passes
**Top 5 by stakes rank subjected to full adversarial search:** THEORY-009, IMPL-009, CLAIM-15, CLAIM-06, CLAIM-05
**Methods:** Codebase grep across entire `src/`, primary source literature review, cross-cycle code trace

---

## Executive Summary (Third Pass)

This pass returns to the 5 highest-stakes claims from the consolidated brief and runs fresh adversarial evidence searches. Net result: no prior verdicts overturned. Three claims received important nuance upgrades. One previously deferred claim (CLAIM-05) received its first full elaboration. The propagation gap in THEORY-009 remains open after three passes — that edit still has not been made.

**Key new findings vs brief:**

| Claim | Brief verdict | Third pass upgrade |
|-------|--------------|-------------------|
| THEORY-009 | CONTESTED / P0 | Confirmed + nuance: "full block unsupported" slightly overshoots — behavioral block still warranted for domain-transfer reasons regardless of Kadavath inversion |
| IMPL-009 | CONFIRMED / P1 | Confirmed + extended fix: add max-attempt circuit breaker; load_lessons() is a complement not a substitute |
| CLAIM-15 | CONFIRMED / P1 | Confirmed + partial mitigation found (replan_count cap=2 limits but doesn't fix); wording error noted (.extend → .append); test gap confirmed |
| CLAIM-06 | CONFIRMED NARROWER / P1 | Confirmed + stronger narrowing: non-DAG parallel has ZERO cascade capacity (not just reduced); cascade is DAG+TIMEOUT only |
| CLAIM-05 | UNELABORATED / P2 | Fully elaborated: THREE constants (not two); code correctly separates them; conflation is doc-level only; Thompson Sampling valid; pass@k provides LLM-native grounding |

**Three-pass pattern conclusion:** Citation inversion (THEORY-009) is the most persistent live error. After three dedicated adversarial passes confirming it, the underlying edit (`adversarial-verification.md:65-66,144-147`) has still not been made. Verification without propagation is a process failure.

---

## Third Pass Claim Verdicts

---

### THEORY-009 — Confidence-Gap Block (Kadavath Citation Inversion)
**Rating: CONTESTED (confirmed inversion; narrower remedy than brief prescribes)** | Urgency: P0

**Prior verdict (Passes 1-2):** Citation inversion confirmed; full block overstated; narrow to verbalized only.

**Third-pass adversarial findings:**

- **Inversion confirmed (strong):** Kadavath 2022 studies structured T/F self-assessment for factual knowledge. Its core finding — P(True) is well-calibrated and improves with scale — directly contradicts using it as evidence of systematic miscalibration. The pass-1 inversion finding holds.
- **"Full block unsupported" slightly overshoots (contra-1, moderate):** Brief concludes behavioral confidence triggers are unblocked. Third-pass contra-evidence: Kadavath specifically tested factual Q&A, NOT multi-step task-execution confidence. Domain mismatch cuts both ways — the paper does not establish that task-execution confidence IS well-calibrated either. Block on behavioral signals remains warranted for domain-transfer reasons even if Kadavath is inapplicable.
- **"Mostly" qualifier in Kadavath title (contra-2, weak):** Paper documents calibration failures under OOD inputs, ambiguous questions, information-insufficient cases — all common in agentic task execution. "Mostly" ≠ "always well-calibrated."
- **Practical consequence is zero (contra-3, strong):** Neither verbalized nor behavioral confidence triggers are implemented in `src/`. The "narrow block" vs "maintain block" question has no code impact today. Both readings produce the same outcome: calibration audit required before Wave 3 triggers are built.
- **Source docs' conclusion survives inversion (contra-4, strong):** `productive_persistence.md:430` arrives at "calibration audit required" — correct conclusion from different evidence. Xiong 2023 alone supports the audit requirement for verbalized triggers. The audit is sound engineering practice independent of Kadavath.

**Revised verdict:**
- Kadavath inversion: **confirmed** (strong)
- Full block on verbalized confidence: **supported** (Xiong 2023)
- Full block on behavioral confidence: **contested** — not supported by Kadavath (domain mismatch), but also not disproved; domain-transfer gap warrants audit prerequisite regardless
- Calibration audit prerequisite: **confirmed** (strong) — survives all citation corrections

**Propagation gap — still open (3rd confirmation):**
`adversarial-verification.md:65-66` and `:144-147` still contain the inverted Kadavath citation and overstated full-block. This has been flagged in every pass; the edit has not been made.

**Action (P0 — unchanged):**
1. Update `docs/adversarial-verification.md:65-66`: replace "Kadavath 2022 (LLM verbalized confidence systematically overconfident)" with "Xiong 2023 (verbalized confidence explicitly overconfident — applies narrowly to stated percentage outputs)"
2. Update `docs/adversarial-verification.md:144-147`: annotate that block on behavioral signals is warranted for domain-transfer reasons (Kadavath scope is factual Q&A, not task execution), not for Kadavath miscalibration claim
3. Update `docs/research/productive_persistence.md:430`: annotate Kadavath scope — "T/F structured factual self-assessment; does not cover task-execution confidence expression"

---

### IMPL-009 — `task['attempt']` Write-Only; Director Blind Across Restarts
**Rating: CONFIRMED (strong)** | Urgency: P1

**Prior verdict (Passes 1-2):** task['attempt'] incremented at task_store.py:216, never read anywhere in src/. replan_count also session-local. Gap larger than originally stated.

**Third-pass adversarial findings:**

- **Core claim confirmed independently (strong):** director.py:934-937 extracts `reason`, `depth`, `job_id`, `parent_id` from task dict — `attempt` is silently discarded. handle.py:1421-1438 does not forward attempt to run_agent_loop(). orch-items `RunRecord.attempt` is a separate tracking system — not the same field.
- **Strongest contra (moderate) — load_lessons() provides semantic compensation:** agent_loop.py:2784-2803 injects prior stuck-run lessons on restart via TF-IDF similarity. When same-goal prior run ended `status="stuck"`, the lesson is retrieved and injected into decompose prompt. This provides soft awareness that a similar goal previously failed.
- **Why contra doesn't close the gap (strong):** (1) Semantic match only — paraphrased goals return no lessons. (2) No count: lessons say "prior run failed" not "attempt N." (3) No threshold gating: director cannot escalate after N attempts. (4) Empty workspace: new environments have zero compensation regardless of task_store attempt count.
- **Infinite-loop risk (new, strong):** Without a max-attempt circuit breaker, task_store items can loop forever across restarts with no escalation. Only exit is operator manual inspection.

**Extended fix recommendation (upgraded from pass 2):**
1. Pass `task['attempt']` to director replan context; include "This is attempt N of this plan" in planner prompt
2. Add max-attempt circuit breaker in `drain_task_store()` or `handle_task()` — escalate to human review after N cross-restart attempts (configurable via `config.yml`)
3. Keep load_lessons() as complement — it provides context about failure mode; attempt count adds structural gating
4. Tests: `director_observes_attempt_count_on_restart`, `max_attempt_circuit_breaker_fires`, `load_lessons_is_complement_not_substitute`

---

### CLAIM-15 — Re-decompose Doesn't Clear `step_outcomes`; Stale Accumulation
**Rating: CONFIRMED (strong)** | Urgency: P1

**Prior status:** Confirmed in prior passes as root cause of CLAIM-06's real risk. Brief prescribes: "Clear or annotate `injected_context` on re-decompose path."

**Third-pass adversarial findings (first full grep-grounded analysis of this claim):**

- **Core claim confirmed (strong):** grep for `step_outcomes = []` / `step_outcomes.clear()` across agent_loop.py → zero matches. `_decision.redecompose` handler (agent_loop.py:650-687) appends blocked step, inserts sub-steps, returns — list never cleared.
- **Accumulation inflates sibling rate (strong):** `_sibling_failure_rate()` at :2732-2741 reads ALL entries unconditionally: `blocked = sum(1 for s in step_outcomes if s.status == "blocked")`. No cycle-scoping, no timestamp filter. Prior cycles' blocked steps inflate the rate in subsequent cycles.
- **`len >= 3` guard worsened by accumulation (strong):** The guard at :3355 (`len(step_outcomes) >= 3`) is intended to prevent single-step false triggers but is actually satisfied FASTER by stale accumulation — prior cycles' entries help hit threshold in the new cycle.

**Partial mitigation found — NOT in brief (contra-1):**
`replan_count < _REDECOMPOSE_THRESHOLD` (agent_loop.py:3357; threshold=2 at line 2747) caps total redecompositions. After 2 redecompositions the trigger cannot fire again. **Consequence:** stale accumulation can only cause 2 spurious redecompositions before halting. **However:** this consumes the 2-redecompose budget on false signals — legitimate high-failure-rate cycles then cannot trigger, as the budget is exhausted.

**Wording error in brief (minor):** Brief says `step_outcomes.extend()`; actual code uses `step_outcomes.append()` at line 679. Same net effect (unbounded accumulation), wrong method name.

**Test gap confirmed:** `test_sibling_failure_triggers_redecompose` tests with freshly constructed outcomes. No test for cross-cycle stale scenario.

**Action (P1):**
1. Clear `step_outcomes` at start of new redecompose cycle (agent_loop.py:650 block), or scope `_sibling_failure_rate()` to current cycle via an index: `step_outcomes[cycle_start_idx:]`
2. Add test: `test_stale_step_outcomes_do_not_inflate_sibling_rate_after_redecompose`
3. Update brief wording: `.extend()` → `.append()` (minor)
4. Add note in fix: `replan_count` cap bounds worst-case damage to 2 spurious redecompositions, but budget exhaustion is its own failure mode

---

### CLAIM-06 — `_sibling_failure_rate()` Counts `blocked`; Cascade Risk
**Rating: CONFIRMED (moderate, NARROWER than prior brief)** | Urgency: P1

**Prior verdict (Passes 1-3):** Confirmed real; cascade mechanism is DAG+TIMEOUT only (not all failures); stale accumulation (CLAIM-15) is primary risk multiplier.

**Third-pass adversarial findings (deepest grep analysis of this claim):**

- **Core mechanism confirmed (strong):** agent_loop.py:2740 — `sum(1 for s in step_outcomes if s.status == "blocked")`. Docstring at :2733 explicitly: "blocked (not done)" — intentional design.
- **Cascade requires DAG+TIMEOUT, not just "upstream failure" (contra-1, strong):** Line 2681 `remaining_deps.get(step_idx, set()).discard(completed_idx)` fires for ALL completed steps regardless of status. A non-timeout blocked upstream step completes normally → its entry recorded in results[completed_idx] → downstreams unblocked → downstreams proceed to execute. True cascade only when: upstream TIMES OUT → _timed_out branch at line 2656 breaks completion loop → lines 2686-2692 mark all unreached steps "blocked." Brief's "single upstream failure" should read "single upstream TIMEOUT in DAG mode."
- **Non-DAG parallel fanout has ZERO cascade capacity (contra-2, strong, stronger than prior):** `_run_steps_parallel()` (lines 2499-2542) runs all steps via ThreadPoolExecutor with NO dependency relationships. A "blocked" step in one thread cannot cause any other thread's step to be blocked. Brief framing "only in fanout parallel paths" is incorrect — non-DAG parallel is the cascade-immune path; DAG is the cascade-capable path.
- **Sequential execution immune via execution model, not line :3356 (contra-3, moderate):** In sequential execution, unexecuted downstream steps never appear in step_outcomes. The `len >= 3` guard at :3356 is a minimum-count gate, not a sequential-vs-parallel distinction.

**Fix priority clarification:**
CLAIM-15 (clear step_outcomes on redecompose) resolves the primary risk vector — stale accumulation is what inflates `_sibling_failure_rate()` in practice. A `blocked_by_cascade=True` flag on the DAG timeout path closes the secondary risk for in-cycle DAG timeouts. Fix CLAIM-15 first; then optionally add cascade exclusion flag.

**Action (P1):**
1. Fix CLAIM-15 first (stale accumulation root cause)
2. Add `blocked_by_cascade=True` flag in agent_loop.py:2686-2692 timeout handler; exclude cascade-blocked steps from `_sibling_failure_rate()` numerator
3. Update brief: replace "single upstream failure" with "single upstream TIMEOUT in DAG mode"; remove "fanout parallel" as cascade source

---

### CLAIM-05 — Retry Threshold UCB/Gittins Grounding
**Rating: CONFIRMED (core) with TWO wording corrections** | Urgency: P2

**Prior status in brief:** "Carried from prior pass; not elaborated in this brief | Cannot independently verify without prior pass artifacts | Prior pass rated moderate; no basis to downgrade." Third pass provides first full code-grounded analysis.

**Core sub-claims verified (strong):**
- Zero UCB/Gittins computation anywhere in `src/` — grep for `UCB`, `Gittins`, `bandit`, `thompson` across all src/*.py → zero matches
- UCB stationarity assumption violated: each retry injects different failure context (agent_loop.py:629-633) → non-stationary rewards
- UCB independent-arms assumption violated: step dependency chains mean retry arms are not independent
- Both stated line numbers accurate: `stuck_streak >= 2` at :4225; `_RETRY_THRESHOLD=3` at :2745; comment at :2744 explicitly cites "zoom-metacognition research" not RL computation

**Wording correction 1 — "conflation" is doc-level only, code is correctly separated (contra-1, strong):**
- `stuck_streak >= 2` (:4225): tracks CONSECUTIVE REPETITION of identical (step_text, step_status) pairs; resets to 0 on any new action; triggers adaptive execution / stuck advisor
- `_RETRY_THRESHOLD = 3` (:2745): controls BLOCKED/FAILED retry count in `_handle_blocked_step()` via `prior_retries` parameter
- These track different quantities, govern entirely separate code paths, have zero cross-reference in code
- "Conflation" exists only in research doc prose ("2-3 retry threshold"); the implementation is correctly separated

**Wording correction 2 — THREE constants, not two (contra-2, moderate):**
- `stuck_streak >= 2` (:4225) — repetition detector
- `_RETRY_THRESHOLD = 3` (:2745) — blocked-step retry gate
- **`_DIAGNOSIS_RETRY_THRESHOLD = 2` (:3165)** — gates `diagnose_loop()` consultation in `_consult_diagnosis()`, called at :3292
- Three-tier gate fires in sequence: diagnosis at retry 2, further retry up to 3, repetition detection on outer loop

**Strongest counter to "no valid bandit model" (contra-3, moderate):**
`docs/research/productive_persistence.md:16` already mentions Thompson Sampling: *"naturally phases out low-value options as evidence accumulates."* Thompson Sampling has no stationarity assumption; Exp3 achieves O(√T) regret without stationarity. The problem is the WRONG model cited (UCB/Gittins), not that bandit framing is inapplicable.

**LLM-native empirical grounding (contra-4, moderate):**
LLM pass@k benchmarks (Chen et al. 2021 HumanEval; Kulal et al. 2019) show 2-4 retries improve success non-linearly. This is direct LLM-native empirical evidence for the threshold range — entirely independent of bandit theory.

**Corrected framing for research docs:**
> "Three separate threshold constants exist — `stuck_streak >= 2` (repetition detector), `_RETRY_THRESHOLD=3` (blocked-step retry gate), `_DIAGNOSIS_RETRY_THRESHOLD=2` (diagnosis consultation gate) — governing different code paths. These are NOT conflated in code. Research docs incorrectly describe them as a unified '2-3 retry rule.' UCB/Gittins framing is post-hoc rationalization; replace with Thompson Sampling intuition or pass@k empirical grounding."

**Action (P2 — doc only, no code change):**
1. Replace "two threshold constants" framing with three-constant description in all research docs
2. Replace UCB/Gittins language with Thompson Sampling intuition + pass@k citation
3. Preserve the retry budget values (2-3) — they are empirically defensible; only the theoretical grounding changes

---

## Third Pass Confidence Ratings Summary

| Claim | Urgency | Third-pass verdict | Confidence | Rating change |
|-------|---------|-------------------|------------|--------------|
| THEORY-009 | P0 | CONTESTED — inversion confirmed; "full block unsupported" overshoots; behavioral block warranted for domain-transfer | strong | Nuanced — prior "narrow to verbalized only" was slightly too permissive for behavioral signals |
| IMPL-009 | P1 | CONFIRMED — structural gap unchanged; extended fix (circuit breaker) | strong | No change to core; fix recommendation extended |
| CLAIM-15 | P1 | CONFIRMED — no clearing on redecompose; partial mitigation (replan cap=2) not in brief | strong | No change to verdict; partial mitigation now documented |
| CLAIM-06 | P1 | CONFIRMED NARROWER — cascade is DAG+TIMEOUT only; non-DAG parallel immune | moderate | Non-DAG parallel cascade capacity corrected to zero (not "reduced") |
| CLAIM-05 | P2 | CONFIRMED (core) — two wording errors: conflation is doc-level only; three constants not two | moderate→moderate | First full elaboration; two wording corrections; no rating change |

---

## Third Pass Action Matrix

### P0 — Still Unexecuted After Three Passes (Propagation Failure)

| Action | File | Status |
|--------|------|--------|
| Narrow Signal 3 block: replace inverted Kadavath citation with Xiong 2023 (verbalized confidence only); annotate behavioral block as warranted for domain-transfer reasons, not Kadavath miscalibration | `docs/adversarial-verification.md:65-66, 144-147` | **NOT DONE — 3rd pass confirms still unedited** |
| Annotate Kadavath scope in productive_persistence.md | `docs/research/productive_persistence.md:430` | **NOT DONE** |

### P1 — New or Upgraded from This Pass

| Action | File | Detail |
|--------|------|--------|
| Wire `task['attempt']` to director + add max-attempt circuit breaker | `src/director.py`, `src/handle.py`, `src/task_store.py` | Circuit breaker is new — not in prior fix prescriptions |
| Clear `step_outcomes` on redecompose (or scope `_sibling_failure_rate` to current cycle) | `src/agent_loop.py:650` | Core CLAIM-15 fix |
| Add test: cross-cycle stale step_outcomes accumulation | `tests/test_agent_loop.py` | Gap confirmed by code search |
| Add `blocked_by_cascade=True` flag in DAG timeout handler | `src/agent_loop.py:2686-2692` | Secondary to CLAIM-15 fix |

### P2 — Documentation Corrections

| Action | File | Detail |
|--------|------|--------|
| Replace UCB/Gittins framing with Thompson Sampling + pass@k | Research docs citing UCB/Gittins | CLAIM-05: three constants; code correctly separates; pass@k is better grounding |
| Replace "single upstream failure" with "single upstream TIMEOUT in DAG mode" in CLAIM-06 docs | Any doc citing CLAIM-06 | Cascade is more specific than described |
| Replace "fanout parallel" as cascade source with "DAG-mode only" | Any doc citing CLAIM-06 | Non-DAG parallel has zero cascade capacity |
| Update CLAIM-05 framing: three constants not two; code correctly separates | Research docs | `_DIAGNOSIS_RETRY_THRESHOLD=2` at :3165 missed in prior framing |

---

## Third Pass Artifact Index

| Artifact | Path | Contents |
|----------|------|----------|
| Claims corpus (this pass) | `projects/adversarial-verification-for-each-key/step1-claims-list.json` | 27 claims from `adversarial-verification-brief.md` |
| THEORY-009 contradictions | `projects/adversarial-verification-for-each-key/artifacts/claim1-contradictions.md` | Citation inversion nuance; propagation gap evidence |
| IMPL-009 contradictions | `projects/adversarial-verification-for-each-key/artifacts/claim2-contradictions.md` | load_lessons() contra; circuit breaker recommendation |
| CLAIM-15 contradictions | `projects/adversarial-verification-for-each-key/artifacts/claim3-contradictions.md` | replan_count cap partial mitigation; test gap |
| CLAIM-06 contradictions | `projects/adversarial-verification-for-each-key/artifacts/claim4-contradictions.md` | DAG+TIMEOUT cascade specificity; non-DAG immune |
| CLAIM-05 contradictions | `projects/adversarial-verification-for-each-key/artifacts/claim5-contradictions.md` | Three constants; conflation doc-level; Thompson Sampling |
