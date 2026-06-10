# Research Brief: Productive Persistence & Zoom-Out Metacognition — Findings and Design

*Synthesized: 2026-05-12*
*Sources: docs/research/productive_persistence.md, docs/research/zoom-metacognition-adaptive-expertise.md, docs/research/productive-persistence.md, docs/research/zoom-metacognition.md*

---

## 0) Question

- **Primary question:** How should Poe calibrate persistence vs. giving up, and when should it zoom out to reframe rather than retry?
- **Why we care / decision it informs:** Agent_loop.py's stuck detection, retry budget design, zoom-out trigger logic, skill crystallization policy, and the inspector's quality gates all depend on getting this right. Under-persistence wastes recoverable progress; over-persistence compounds sunk cost and hallucinates forward motion.
- **Success criteria:** A unified design that maps external research to concrete Poe code sites, with implementation gaps explicitly flagged and ranked by implementation priority.

---

## 1) Constraints

- Time budget: Research synthesis only — no new empirical studies commissioned
- Cost/tooling budget: Existing docs corpus only; no external API calls
- Required stance: Neutral — surface both supportive and contradictory evidence
- Must-use sources: productive_persistence.md (2026-05-12), zoom-metacognition-adaptive-expertise.md (2026-05-12), older predecessor docs for diff analysis
- Must-avoid sources: None

---

## 2) Research Plan (Angles)

1. **What does "productive" mean in persistence?** (hypothesis-narrowing criterion)
2. **How should retry budgets be tiered and calibrated?** (tactic / strategy / goal tiers)
3. **When should an agent zoom out rather than persist?** (five signal model)
4. **How do skills crystallize without becoming brittle?** (promotion criteria)
5. **What are the theory-to-implementation gaps in current Poe code?**
6. **Where do the research docs contradict each other, and which model is more defensible?**

---

## 3) Sources & Provenance

- `docs/research/productive_persistence.md` (2026-05-12) — primary; Duckworth, Kapur, UCB/Gittins, Dweck, Seligman, Hatano, meta-RL
- `docs/research/zoom-metacognition-adaptive-expertise.md` (2026-05-12) — primary; Hatano & Inagaki, Schwartz & Bransford, Fleming, Cleeremans, Ericsson, Feltovich/Spiro/CFT, Argyris & Schön, Boyd
- `docs/research/productive-persistence.md` (2026-03-27) — predecessor; Bjork desirable difficulty, N=3 heuristic origin, earlier failure taxonomy draft
- `docs/research/zoom-metacognition.md` — predecessor; earlier zoom-out signal model (failure-centric only)
- `skills/arch-core-loop.md` — Poe codebase implementation notes (corroborated code references)

---

## 4) Executive Summary

Productive persistence is not about trying harder — it is about using each failure to narrow the hypothesis space while keeping sunk-cost attachment low. The critical distinction is between **informative failures** (new signal, persist or pivot) and **confirming failures** (repeated signal, stop). Current Poe code tracks `stuck_streak` as a raw count; the research shows count alone is insufficient — the error-signature must change for a retry to constitute productive exploration.

Zoom-out should fire on **five signals**, not just step failure: (1) outcome unexpected after procedure applied, (2) near-miss success where parent goal wasn't advanced, (3) confidence–accuracy decoupling, (4) meta-ignorance (high skill score + diverging outcome), and (5) schema fitness check failure. The current `is_converging` logic covers only signal 5, and is not yet wired into `agent_loop.py`.

Skill crystallization becomes brittle through the same mechanism: automaticity suppresses the error signal that would trigger zoom-out. Promotion criteria must encode the cases in which a skill is *not* applicable, not just the cases where it succeeded.

The two most urgent implementation gaps are: (a) error-signature deduplication in `stuck_streak` (blocking the informative/confirming distinction), and (b) wiring `is_converging` into the core retry path — it is implemented (Phase 62) but not called from the step-execution block in `agent_loop.py`. Both have bounded implementation scope. Meta-RL calibration of persistence thresholds is the high-value long-term item. Adversarial review also surfaced a stale-context risk at `agent_loop.py:686` where injected context is not cleared on re-decompose — a silent assumption-inheritance bug independent of the persistence design.

---

## 5) Key Findings

### Persistence Design

- **Productive persistence = approach variation anchored by narrowing hypothesis space**, not sunk cost or emotional resolve. (Duckworth 2016, Kapur 2016 — productive_persistence.md §1)
- **Informative vs confirming failure is the critical gate.** A failure is informative if `(error_type × last_action × context_signature)` hash changes from the previous attempt. If the hash repeats, the agent is looping, not learning. (productive_persistence.md §2.1)
- **Infrastructure failures must be exempt from retry budgets.** They signal environmental brittleness, not hypothesis exhaustion, and counting them destroys the budget semantics. Seligman's learned helplessness model applies: repeated uncontrollable failures depress future try-rates even on solvable problems. (productive_persistence.md §2.3)
- **Tiered retry structure is correct:** tactic (2–3 retries), strategy (2 replans via `_REDECOMPOSE_THRESHOLD`), goal (human judgment). The UCB/Gittins framing grounds this as an opportunity-cost calculation, not just a counting heuristic. (productive_persistence.md §2.2)
- **Persistence thresholds are learnable** via meta-RL (Wang/Duan 2016). The lesson extraction pipeline (`memory.py`) is the plausible implementation site for adaptive calibration. Not yet implemented. (productive_persistence.md §2.2)

### Zoom-Out Signals

- **Five distinct zoom-out triggers** emerge from synthesis across six source clusters:
  1. Procedure applied + outcome unexpected (Hatano & Inagaki 1986)
  2. Near-miss / unexpected-path success — step succeeded but parent goal not advanced (Schwartz & Bransford 1999)
  3. Confidence–accuracy decoupling — pre-step confidence high, outcome poor (Fleming & Dolan 2012)
  4. Meta-ignorance — skill score high, outcome diverges, no self-detection (Fleming 2010)
  5. Schema fitness failure — fails ≥ N times without convergence (Hatano / Argyris)
- **High automaticity suppresses error signals** (Cleeremans RPT). Highly practiced routines do not generate the prediction-error signal that would trigger zoom-out. This is why the error-signature hash must be explicit infrastructure, not emergent behavior. (zoom-metacognition-adaptive-expertise.md §3)
- **Zoom-out type is domain-dependent.** Well-structured domains: hierarchical re-decompose. Ill-structured domains: lateral case-traversal first ("what is this really a case of?"), then hierarchical. (Feltovich/Spiro CFT — zoom-metacognition-adaptive-expertise.md §5)
- **Orientation hygiene (Boyd OODA):** Before retrying a stuck step, verify: step inputs still valid? Success criterion still serves parent goal? Environment shifted? Any "no" → re-decompose immediately. (zoom-metacognition-adaptive-expertise.md §6)

### Skill Crystallization

- **Brittle transfer mechanism:** frequency → chunking → automaticity → tacit lock-in → structural access lost → near-transfer only. Promoting a skill on success frequency alone encodes the wrong thing. (Ericsson 1993; Chase & Simon 1973)
- **CFT ten reductive tendencies** are the specific failure modes a promotion check should audit: discretization, single-cause attribution, static representation, regularity overextension, central-features-only, isolation, directionality assumption, context-stripping, concept-stability, schema-reduction. (Feltovich, Spiro & Coulson 1993)
- **Promotion criteria must include applicability boundaries** — the cases in which the skill should NOT fire — not just success-rate metrics. Context-stripping at promotion time loses this information permanently. (zoom-metacognition-adaptive-expertise.md §5)

### Poe Implementation Status

| Mechanism | Code Site | Status |
|---|---|---|
| Tactic retry counter | `agent_loop.py:241` `step_retries` | Implemented — count only, no signature check |
| Strategy replan counter | `agent_loop.py:267` `director_replan_count`, cap at `_REDECOMPOSE_THRESHOLD=2` | Implemented — fixed cap, not adaptive |
| Failure type taxonomy | `productive_persistence.md` §2.3 | Designed, not wired |
| Error-signature hash | Not yet present | Gap — blocks informative/confirming distinction |
| `is_converging` heuristic | `lat.md/zoom-metacognition.md` | **Implemented (Phase 62)** — not wired into `agent_loop.py` core retry path; adversarial review refuted "design-only" claim |
| Near-miss zoom-out | Not implemented | Gap |
| Confidence–accuracy decoupling | Not implemented | Gap |
| Meta-ignorance detection | Not implemented | Gap |
| Skill promotion CFT audit | `skills.py` (partial) | Partial — success rate exists, reductive-tendency audit absent |
| Cross-restart failure history | `task_store.py:69` `task['attempt']` | Implemented |

---

## 6) Counterpoints / Dissent

**D1 — Retry-N design philosophy: the two docs directly contradict each other.**
- Older doc (productive-persistence.md, 2026-03-27): N≈3–5 drawn from RL heuristic; code applies N=3 uniformly across all failure types.
- Newer doc (productive_persistence.md, 2026-05-12): N=3 conditioned on failure TYPE; infrastructure failures explicitly exempt.
- **Resolution:** The newer model is more defensible because it separates the semantics of the budget from its numeric value. The flat-N model conflates uncontrollable failures (infrastructure) with hypothesis-testing failures (tactic), which is the exact error Seligman's learned helplessness model warns against. Backward-incompatible — existing code does not implement the type-weighted version.

**D2 — 60–85% success-rate "desirable difficulty" zone (Bjork/Kapur) is human-derived.**
- Both docs acknowledge this zone is empirically established only for human learners (Bjork 1994, Kapur 2016).
- Transfer to LLM-based agent architectures is asserted but not validated. Token-level sampling (temperature, top-p) may create a different kind of "difficulty" that the zone does not address.
- **Unresolved.** The zone is a useful heuristic but should not be treated as a validated empirical fact for Poe.

**D3 — Zoom-out trigger scope: older vs newer doc disagree on what fires zoom-out.**
- Older doc (zoom-metacognition.md): zoom-out is failure-centric — triggered by step failure, retry exhaustion, contradiction detection only.
- Newer doc (zoom-metacognition-adaptive-expertise.md): also fires on near-miss success and unexpected-success-path — signals that arrive WITHOUT failure.
- **Resolution:** The newer model is more complete. Schwartz et al. (2005) explicitly show that high automaticity makes near-misses *less* salient than clean failures — meaning failure-centric triggers have a systematic blind spot for the most dangerous cases. The expanded model is the correct design target.

**D4 — Meta-ignorance: absence from older doc is a design gap, not a counterpoint.**
- Fleming's meta-ignorance catastrophe (high confidence + wrong output does not self-trigger zoom-out) is absent from all pre-2026-05-12 Poe docs.
- This is not contested — it's a missing piece that has no current mitigation in code.

**D5 — "Persistence calibration is learnable" is speculative for the current codebase.**
- Wang/Duan 2016 meta-RL result shows persistence thresholds emerge in environments with variable difficulty and reward density. Poe's lesson extraction pipeline is plausible infrastructure, but whether the training signal quality is sufficient is unknown.
- The claim should be treated as a design aspiration, not an engineering commitment.

**D6 — Adversarial review: three claims in earlier docs were refuted or contested.**
- *Refuted*: CLAIM-13 ("_is_converging is design-intent only") — adversarial pass confirmed Phase 62 implements it. Documentation gap, not code gap.
- *Refuted*: CLAIM-07 ("reframe_intent primitive") — zero occurrences in `src/`; what exists is `_ae_restart_ctx`. Earlier docs named a fabricated primitive.
- *Contested*: Inspector-not-in-execution-path (CLAIM-14) — Inspector is not imported in `agent_loop.py`; real gate chain is `pre_flight → step_exec → _post_step_checks`. May be intentional architectural decoupling (inline friction detection adds latency); documentation is wrong, design may not be. Verdict pending explicit architecture decision.
- *Contested*: 50% sibling failure threshold — is a defensible symmetric-cost prior, but the reasoning is undocumented. Threshold should be annotated in code with rationale.

---

## 7) Risks, Unknowns, and What Would Change Our Mind

**Risks:**
- **Implementing error-signature hash incorrectly** could mask genuine loops (hash changes due to irrelevant context variation) or miss real loops (hash stable but semantically different). The hash function design is not specified in either doc.
- **Type-weighted retry budget requires a working failure classifier**, which itself has the meta-ignorance problem: the classifier can be wrong without knowing it. Bootstrapping this correctly requires at least one external validator per failure type.
- **Promoting skills with CFT-incomplete applicability conditions** bakes reductive tendencies into the evolved skill library. Each promotion step that strips context is irreversible unless the originating cases are stored alongside the skill.
- **Cross-restart failure history** (`task['attempt']`) is per-task, not per-approach. The same task attempted via different strategies looks identical in the counter — hiding which approaches have already failed.

**Unknowns:**
- Correct finite-horizon N for informative failure when RL theory only gives asymptotic answers
- Whether the 60–85% desirable-difficulty zone transfers to LLM/token-sampling architectures
- How to detect feedback-loop breakage (agent actions not affecting environment) without pre-existing meta-cognition infrastructure
- Minimum viable implementation for meta-RL persistence calibration via `memory.py`
- How the failure-type classifier avoids meta-ignorance in its own classification (the bootstrap problem)
- What structural signals can distinguish informative vs confirming failure without relying on LLM self-report

**Open questions requiring code investigation:**
- Does `_sibling_failure_rate()` exclude DAG-cascade blocked tasks, or does it count them against the threshold? (`agent_loop.py:2740`)
- Does introspection (Phases 44–46) function as a Kapur consolidation-phase analog — i.e., does structured reflection after failure produce better subsequent performance than immediate retry? If yes, the "Poe has no consolidation phase" objection (CLAIM-08) is answered.
- What is the empirical basis for Inspector `breach_threshold=0.30`? Is it tuned from production runs or set arbitrarily?
- Should `_error_fingerprint` (if implemented) unify with `_is_converging` and `stuck_streak` into a single convergence-detection subsystem?
- Does Poe have any goal-mutation primitive distinct from full restart (`_ae_restart_ctx`)? If not, the tactic → strategy → goal escalation ladder has no middle rung at the goal level.
- Should `load_lessons()` fire at task `claim()` time (before execution) rather than only at `_decompose_goal()`? Earlier lesson injection may prevent the failure class that triggered the lesson rather than only informing re-decompose.

**Disconfirming evidence to look for:**
- If implementing error-signature hashing does NOT reduce stuck_streak false positives in production runs → count-based approach may be adequate
- If the failure-type classifier's error rate on infrastructure vs tactic failures is high in practice → the type-weighted budget model adds complexity without correctness
- If skill promotion with CFT applicability-boundary encoding doesn't measurably improve transfer on novel tasks → Ericsson's representation-richness hypothesis may not hold in LLM skill contexts

---

## 8) Recommendation

- **Recommended course of action:** Implement the two highest-leverage, bounded-scope gaps first: (1) error-signature deduplication in `stuck_streak` to enable informative/confirming failure distinction, and (2) wire `is_converging` into `agent_loop.py` as a zoom-out trigger gate alongside schema-fitness failure. Then add failure type taxonomy (infrastructure / tactic / strategy / meta) to the retry budget calculation. Defer meta-RL calibration and CFT skill promotion until the basic loops are validated in production.
- **Confidence level: medium.** The theoretical grounding is strong (multiple independent converging research lines), but theory-to-LLM-agent transfer is empirically unvalidated for several key claims (desirable-difficulty zone, meta-RL calibration). The code gap analysis is based on doc descriptions and partial code references, not a full audit.

---

## 9) Next Actions (Concrete)

1. **Add error-signature hash to `LoopContext`** — hash of `(error_type, last_action, context_signature)` per step; `stuck_streak` increments only if hash is unchanged from previous attempt. Resolves the informative/confirming failure distinction at the tactic tier. (`agent_loop.py:233–241`)
2. **Wire `is_converging` into step execution path** — `is_converging = False` should trigger zoom-out (re-decompose) rather than another retry. Currently designed in `lat.md/zoom-metacognition.md` but not called from `agent_loop.py`. (`agent_loop.py` step-execution block)
3. **Implement failure-type classifier** with four types: informative / confirming / infrastructure / meta. Infrastructure failures skip the retry budget counter. Informative failures get full budget; confirming failures force immediate pivot. (`agent_loop.py` or new `failure_classifier.py`)
4. **Add near-miss zoom-out trigger** — after a step marked "success", check if parent goal's progress metric advanced. If not, treat as near-miss and fire zoom-out. (Schwartz & Bransford 1999 signal 2 — currently unimplemented)
5. **Extend skill promotion criteria with applicability boundaries** — each promoted skill must record: the failure modes that led to its creation (context), explicit conditions under which it should NOT fire (CFT anti-cases). `skills.py` currently tracks success rate only.
6. **Validate 60–85% difficulty zone empirically against Poe runs** — run retrospective analysis: what was Poe's step success rate at the tactic tier when overall mission outcomes were best? Confirm or refute the zone claim for this architecture.
7. **Design meta-RL calibration hook in `memory.py`** — define the minimum data schema for persistence-outcome pairs that would train adaptive retry thresholds. Even if meta-RL training is months away, capturing the right data now avoids a bootstrapping gap later.
8. **Clear stale `next_step_injected_context` on re-decompose** — at `agent_loop.py:686`, prior-step injected context is passed unchanged into re-decompose. If the goal reframes, stale assumptions from the previous decomposition are silently inherited. Add an explicit context-reset on any re-decompose that changes goal scope. (Implementation risk flagged by adversarial review, CLAIM-15)
9. **Annotate `_sibling_failure_rate` threshold with rationale** — document whether the 50% threshold is empirically tuned or a symmetric-cost prior. If the latter, note it explicitly in code so future tuning has a baseline justification.

---

## 10) Appendix

### A. Failure Taxonomy (from productive_persistence.md §2.3)

| Type | Description | Budget treatment | Example |
|---|---|---|---|
| **Informative** | Eliminates a hypothesis; output is new signal | Full tactic budget | Tool returns unexpected data type |
| **Confirming** | Repeats same error; no new information | Force pivot immediately | Same parse error on 3rd identical input |
| **Infrastructure** | Environment/toolchain failure; agent's hypothesis untestable | Exempt from budget; escalate separately | API timeout, disk full |
| **Meta** | Agent's model of the task is wrong | Force zoom-out to strategy tier | Misidentified goal scope |

### B. Five Zoom-Out Signals (from zoom-metacognition-adaptive-expertise.md, Synthesis section)

| Signal | Source | Implementation status |
|---|---|---|
| Procedure applied + outcome unexpected | Hatano & Inagaki 1986 | Partial (`stuck_streak`) |
| Near-miss: step succeeded, parent goal not advanced | Schwartz & Bransford 1999 | Not implemented |
| Confidence–accuracy decoupling | Fleming & Dolan 2012 | Not implemented |
| Meta-ignorance: skill score high, outcome diverges | Fleming 2010 | Not implemented |
| Schema fitness failure: fails ≥ N without convergence | Hatano / Argyris | Partial (`is_converging` — not wired) |

### C. CFT's Ten Reductive Tendencies (crystallization failure checklist)

Discretization, single-cause attribution, static representation, regularity overextension, central-features-only, isolation (ignores inter-concept dependencies), directionality assumption, context-stripping, concept-stability, schema-reduction. (Feltovich, Spiro & Coulson 1993)

### D. Key Citations

- Duckworth (2016) — grit is goal-stable, not strategy-stable
- Kapur (2016) — productive failure: early exploratory errors outperform guarded first passes
- Bjork (1994) — desirable difficulty zone for human learners
- Hatano & Inagaki (1986) — adaptive vs routine expertise; zoom-out trigger
- Schwartz & Bransford (1999); Schwartz, Bransford & Sears (2005) — PFL metric; near-miss salience
- Fleming & Dolan (2012) — metacognitive efficiency; meta-d dissociability
- Fleming (2010) — meta-ignorance catastrophe
- Cleeremans (2002) — Radical Plasticity Thesis; automaticity suppresses prediction error
- Ericsson, Krampe & Tesch-Römer (1993); Ericsson & Pool (2016) — deliberate practice, mental representations, transfer conditions
- Feltovich, Spiro & Coulson (1993) — Cognitive Flexibility Theory; ten reductive tendencies
- Argyris & Schön (1978) — double-loop learning; defensive routines
- Boyd (1987) — OODA; orientation hygiene
- Wang et al. (2016); Duan et al. (2016) — meta-RL; persistence thresholds are learnable
- Seligman (1972) — learned helplessness from uncontrollable failures
- Dweck (2006) — growth mindset; failure attribution
