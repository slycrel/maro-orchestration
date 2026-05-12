# Zoom-In / Zoom-Out Metacognition: Adaptive vs Routine Expertise Survey

**Date:** 2026-05-12
**Informs:** crystallization path (skills.py, evolver.py), core loop (agent_loop.py, inspector.py), skill promotion thresholds, zoom-out trigger logic
**Confidence:** high overall (5 primary empirical sources + 2 theoretical frameworks, cross-validated) — see per-finding ratings and evidence gaps below

---

## Research Question

What does the adaptive vs routine expertise literature tell us about:
1. When should an agent zoom out (reframe, re-decompose) vs zoom in (retry, refine)?
2. How does a skill crystallize without becoming brittle — losing adaptability as it gains efficiency?
3. What signals most reliably trigger appropriate meta-level shifts?

---

## Key Findings

### 1. The Core Distinction (Hatano & Inagaki 1986)

- **Routine expertise**: surface-feature encoding → pattern match → execution. Low metacognitive monitoring. Near-transfer only. Failure mode: silent error — wrong schema applies, agent doesn't notice. ([Hatano & Inagaki, 1986])
- **Adaptive expertise**: structural encoding → principle retrieval → reconstruction. High metacognitive monitoring. Near + far transfer. Failure mode: rare — mismatch detection triggers strategy shift. ([Hatano & Inagaki, 1986])
- **The mechanism**: adaptive experts check "is this schema actually fitting this problem?" continuously. Routine experts don't — they apply until stuck.
- **Zoom-out trigger**: procedure applied + outcome unexpected = primary trigger for level shift.
- **Verbalization finding**: explaining reasoning to others forces structural re-encoding of tacit knowledge, accelerating adaptive expertise. This is not incidental — it builds the zoom-out circuit itself.

### 2. PFL: The Correct Adaptive Expertise Metric (Schwartz & Bransford 1999; Schwartz, Bransford & Sears 2005)

- **Preparation for Future Learning (PFL)** is the hallmark of adaptive expertise: not how well you apply what you know now, but how fast you learn from new information when your current procedure fails. ([Schwartz & Bransford, 1999])
- **Efficiency × Innovation axes are orthogonal**, not inverse. Both are achievable. The path to both requires alternating efficiency-deepening phases and deliberate innovation-challenge phases — not just more practice. ([Schwartz, Bransford & Sears, 2005])
- **High automaticity dampens anomaly salience**: routine experts are less likely to notice near-misses and unexpected-path successes than clean failures. Zoom-out trigger must fire on near-misses, not just explicit failure. ([Schwartz et al., 2005])

### 3. Neural / Computational Level-Shift Mechanisms (Cleeremans 2002; Fleming 2010; Miller & Cohen 2001; Koechlin 2007)

- **Cleeremans RPT**: zoom-out fires when prediction error exceeds a threshold → higher-order redescription recruited. Automaticity suppresses this error signal — highly practiced routines run without generating the meta-level signal needed to trigger zoom-out. ([Cleeremans, 2002, Radical Plasticity Thesis])
- **Fleming metacognitive efficiency**: zoom-out fires on confidence–accuracy *decoupling*, not just failure. Pre-action vs post-action confidence gap is a stronger signal than outcome alone. High meta-d (metacognitive sensitivity) is dissociable from task performance (d-prime). ([Fleming & Dolan, 2012])
- **Meta-ignorance catastrophe**: high confidence + wrong output does NOT trigger zoom-out. The agent is wrong and doesn't know it — the most dangerous failure mode. Must be detected structurally (skill match high, outcome diverges). ([Fleming, 2010])
- **Cognitive load effect**: high load degrades meta-d before d-prime — the agent continues to perform but loses metacognitive accuracy. Under load, zoom-out threshold must be lowered to compensate. ([Fleming et al.])
- **Miller & Cohen PFC model**: prefrontal top-down biasing is the mechanism that overrides automatic responses when they conflict with current goals. Zoom-out = PFC engagement overriding the automated response. ([Miller & Cohen, 2001])

### 4. Skill Crystallization and Brittle Transfer (Ericsson 1993; Ericsson & Pool 2016)

- **DP builds mental representations, not just skills**: expert performance = chunked representations. Transfer depends on representation richness, not practice volume. ([Ericsson, Krampe & Tesch-Römer, 1993])
- **The brittle-transfer mechanism**: frequency → chunking → automaticity → tacit lock-in → structural access lost → near-transfer only. Chess expertise advantage exists only for legal game positions, not random boards (Chase & Simon, 1973).
- **Transfer conditions**: (a) varied case exposure with explicit contrast forces structural encoding; (b) verbalization/explanation-to-others forces representation construction; (c) interleaved difficulty escalation prevents full automatization. ([Ericsson, 1993])
- **Transfer blockers**: full automaticity (tacit lock-in), pure frequency repetition without contrast. Promoting a skill on frequency alone is the computational equivalent of automating without encoding the representation.

### 5. Ill-Structured Domains: CFT and Lateral Zoom-Out (Feltovich, Spiro & Coulson 1993)

- **Cognitive Flexibility Theory**: ill-structured domains require *case-network* representation (criss-crossed landscape), not schema extraction. The same concept must be traversed from multiple structurally distinct cases. Single-traversal extraction produces reductive biases. ([Feltovich, Spiro & Coulson, 1993])
- **Zoom-out type is domain-dependent**: in well-structured domains, zoom-out = hierarchical escalation (re-decompose). In ill-structured domains, zoom-out = *lateral case-traversal* ("what is this really a case of?") before hierarchical re-decompose. ([Spiro et al.])
- **Context-stripping as crystallization failure**: each promotion step that removes case context loses applicability conditions. A skill without its originating cases loses the information needed to know when NOT to apply it.
- **Ten reductive tendencies** catalogued by CFT that cause routine expertise: discretization, single-cause attribution, static representation, regularity overextension, central-features-only, isolation (ignores dependencies), directionality assumption, context-stripping, concept-stability, schema-reduction. These are the specific failure modes a crystallization promotion check should audit.

### 6. Double-Loop Learning and OODA (Argyris & Schön 1978; Boyd 1987)

- **Persistence is the default; reframing is the exception** that must be triggered deliberately. All three frameworks (DLL, OODA, adaptive expertise) agree: agents have a strong bias toward continuing current strategy. ([Argyris & Schön, 1978; Boyd, 1987])
- **Zoom-out = model problem, not execution problem**: single-loop / zoom-in = "I know what to do, I'm doing it wrong." Double-loop / zoom-out = "my understanding of the goal/environment is wrong." ([Argyris, 1991])
- **Defensive routines** block reframing in agents just as in humans: cached plans, stale context, and confirmed-schema bias prevent double-loop even when signals are present. ([Argyris & Schön, 1978])
- **Orientation hygiene (Boyd)**: before retrying a stuck step, check if step inputs are still valid, if success criterion still serves parent goal, if environment has shifted. If any answer is no — re-decompose immediately. ([Boyd, 1987])

---

## Synthesis: The Zoom-Out Signal Model

Across all six source clusters, five distinct zoom-out signals emerge:

| Signal | Source | Poe Analog |
|--------|--------|------------|
| Procedure applied + outcome unexpected | Hatano | Step completed, result diverges from plan expectation |
| Near-miss / unexpected-path success | Schwartz | Step "succeeded" but parent goal not advanced |
| Confidence–accuracy decoupling | Fleming | Pre-step confidence high + outcome poor OR outcome poor despite high skill match score |
| Meta-ignorance: confident + wrong | Fleming | Skill score high, outcome diverges — no self-detection |
| Schema fitness check fails | Hatano / Argyris | Step fails ≥ N times without convergence |

And three crystallization failure mechanisms:

| Failure Mode | Source | Poe Analog |
|-------------|--------|------------|
| Frequency → automaticity → tacit lock-in | Ericsson | Skill promoted on use-count alone |
| Context-stripping at promotion | CFT | Skill loses originating cases at crystallization |
| Single-traversal schema extraction | CFT | Skill crystallized from one use context only |

---

## Implications for Poe

### What to change

**Crystallization path (skills.py, evolver.py):**

1. **Replace frequency-only promotion criterion.** Skill promotion to Rule requires: frequency + structural encoding signal (boundary_conditions populated) + novel-context test result. Frequency alone is the brittle-transfer path.

2. **Add `originating_cases` metadata to skills.** Every skill record carries the ≥2 structurally distinct originating cases used to crystallize it. Require minimum 2 for Lesson → Skill promotion; ≥3 for Skill → Rule promotion.

3. **Add `case_diversity_score` to skill scoring.** Replace or supplement frequency-only criterion. Skills crystallized from diverse structural contexts score higher for promotion; those from single-context repetition are flagged for review.

4. **Add `boundary_conditions` field to crystallization record.** The conditions under which the skill does NOT apply. This is the applicability constraint that context-stripping removes.

5. **Run reductive-tendency audit before Rule-level promotion.** Check for at least: context-stripping (T8), schema-reduction (T10), and discretization (T1). These are the three most common crystallization failure modes.

6. **Interleave novel-context test after N uses before further promotion.** Before promoting from Skill → Rule, introduce one structurally distinct case. If the skill fails to transfer, block promotion and widen the case base.

**Inspector / zoom-out trigger (inspector.py, agent_loop.py):**

7. **Add confidence–outcome gap metric to Inspector.** Flag zoom-out when pre-step confidence is high AND outcome diverges from expectation. This fires before explicit failure — on the near-miss and unexpected-path success signals.

8. **Detect meta-ignorance structurally.** When skill match score is high but outcome diverges, flag for structural review (not just decay tuning). This is the "confident + wrong" failure that does not self-detect.

9. **Inspector fires on near-misses and unexpected-path outcomes**, not just failures. A step that "succeeded" but didn't advance the parent goal is a zoom-out signal.

10. **Add load-aware zoom-out sensitivity multiplier.** Under high cognitive load (long context, many active tasks, deep nesting), lower the zoom-out threshold. Load degrades meta-d before d-prime — the agent continues to perform but loses metacognitive accuracy.

11. **Introduce perturbation checks for high-frequency skills.** After N uses without structural update, inject a structurally distinct probe task to test whether the skill generalizes or has become brittle.

**Director narration / verbalization (director.py, step_exec.py):**

12. **Protect director narration under token pressure.** Verbalization (explaining reasoning to others) is the mechanism that forces structural re-encoding. Director narration IS the computational verbalization analog. Stripping it under token pressure removes the zoom-out circuit builder. It is not a cosmetic output — it is a functional mechanism.

13. **First-use director narration = verbalization condition.** When a skill is applied for the first time in a novel context, require director narration regardless of token budget. This is when structural encoding is most needed.

**Ill-structured task handling:**

14. **Zoom-out for ill-structured tasks = lateral before hierarchical.** When task_classification is ill-structured, zoom-out sequence is: (1) case-traversal query ("what is this really a case of? what prior cases share structure?"), then (2) hierarchical re-decompose. Skipping step 1 produces the CFT reductive biases.

15. **Add `task_classification` field to step records.** Well-structured vs ill-structured classification determines which zoom-out path to use. This is a binary first-pass; a heuristic classifier suffices.

### What stays the same

- **Tiered memory decay/reinforce model**: directionally correct. Decay direction is right; rate remains the empirical question.
- **Fluid → Lesson → Identity → Skill → Rule crystallization path**: structurally correct. The additions above extend it, not replace it.
- **Inspector friction detection**: correct mechanism. Additions above extend its trigger conditions.
- **Knowledge_web case structure**: do not flatten to schema-only. CFT validates preserving case-network over schema extraction.
- **Skill decay mechanism**: direction is correct. High-frequency skills should not be immune to decay; the rate is the tuning question.

### What needs more research

- **Structural similarity metric for case-network traversal**: CFT describes the mechanism (criss-crossed landscape) but doesn't specify a computational similarity function. This is the unresolved piece for case_diversity_score implementation.
- **Ill-structured vs well-structured task classifier**: needed to apply the lateral zoom-out path selectively. No existing heuristic is validated; this requires empirical development.
- **Minimum case base size for lateral traversal**: CFT is silent on the minimum number of cases needed before the criss-crossed landscape is useful. Empirical tuning required.
- **Optimal zoom-out threshold values**: retry count, confidence gap threshold, load sensitivity multiplier. These are empirically tunable — the research gives the signal types, not the calibration.
- **Confidence calibration for Poe's LLM backbone**: Fleming's meta-d / d-prime framework assumes a well-calibrated confidence reporter. LLM confidence scores are known to be poorly calibrated. Need separate calibration research.

---

## Confidence Analysis: Per-Finding Ratings

| Finding | Confidence | Basis | Key Gap |
|---------|-----------|-------|---------|
| Routine vs adaptive expertise distinction (Hatano & Inagaki) | **High** | Foundational; replicated across 40 years of expertise literature | Original source is a book chapter, not peer-reviewed journal — no direct replication study cited |
| Verbalization accelerates adaptive expertise | **High** | Multiple independent lines of evidence (Ericsson verbalization condition; Cleeremans RPT; metacognitive theory) | Mechanism is indirect inference in Hatano — not their primary claim |
| PFL as adaptive expertise metric (Schwartz & Bransford) | **High** | Experimental; replicated; widely adopted in learning sciences | PFL measured in human students; transfer to AI skill crystallization is an *analogy*, not tested |
| Efficiency × Innovation axes are orthogonal | **Medium** | Conceptual model with supportive experiments; not formally proven orthogonal in a statistical sense | No validated measurement instrument; the 2D model is a framework, not an empirically measured construct |
| Near-miss as zoom-out signal (Schwartz et al.) | **Medium** | Inferred from PFL + automaticity-dampens-anomaly finding; not directly tested as a trigger mechanism | No study directly tests near-miss detection rate in adaptive vs routine experts |
| Cleeremans RPT: prediction error → redescription | **Medium** | Well-cited theoretical framework; neural plausibility; but RPT is a theory, not a proven mechanism | Direct neuroimaging evidence for the specific threshold-crossing claim is thin; most support is behavioral |
| Confidence–accuracy decoupling as zoom-out signal (Fleming) | **High** | Neuroimaging + behavioral; Fleming meta-d framework is well-validated; rlPFC lateralization replicated | Original studies use perceptual tasks; generalization to complex reasoning tasks is assumed, not shown |
| Meta-ignorance catastrophe (confident + wrong) | **High** | Robustly demonstrated in calibration literature (Dunning-Kruger adjacent); Fleming meta-d framework | LLM confidence scores are known to be poorly calibrated — the detection mechanism itself needs calibration research |
| Cognitive load degrades meta-d before d-prime | **Medium** | Fleming et al. load studies; consistent with dual-process theory | Effect size and threshold values not established; load-multiplier values for Poe are engineering choices, not empirically derived |
| DP → brittle transfer (Ericsson) | **High** | Chase & Simon chess replication is landmark; extensively replicated | Deliberate practice literature is skewed toward motor/perceptual domains; complex reasoning transfer is less studied |
| Transfer conditions (varied case, verbalization, interleaving) | **High** | Each condition supported by multiple experiments; interleaving research is particularly robust | Optimal interleaving interval unknown; verbalization studied in human learning, not AI crystallization |
| CFT criss-crossed landscape (Feltovich/Spiro) | **Medium-High** | Well-regarded in learning sciences; influential in instructional design | CFT validated in educational settings; no computational instantiation tested; structural similarity metric unspecified |
| Lateral before hierarchical zoom-out for ill-structured tasks | **Medium** | Inferred from CFT + expertise literature synthesis; not directly tested as agent behavior | No study explicitly tests zoom-out ordering in agents; this is a design inference from CFT, not a proven rule |
| Ten reductive tendencies (Feltovich/Spiro) | **Medium** | Qualitatively catalogued from clinical observation in medical training; not exhaustively validated | List is empirically generated but not formally complete or ranked by frequency/severity |
| Double-loop learning (Argyris & Schön) | **Medium** | Influential management/learning theory; widely cited | Primarily descriptive/organizational; limited experimental validation in controlled settings |
| OODA orientation hygiene (Boyd) | **Low-Medium** | Military doctrine, not empirical cognitive science; logic is sound but evidence base is observational | No controlled studies; Boyd is a practitioner framework, not a research result |

**Overall document confidence: High for the core design claims (1, 3, 7, 9, 10); Medium for the implementation details (interleaving rates, threshold values, classifier approach); Low for OODA-derived claims used in isolation.**

---

## Missing Evidence: What Would Raise Confidence

### Gap 1: LLM confidence calibration (Critical)
**What's missing:** Fleming's meta-d / d-prime framework assumes a well-calibrated confidence reporter. LLM self-reported confidence is known to be systematically miscalibrated (overconfident on out-of-distribution inputs, underconfident on paraphrase variants). The confidence–accuracy gap signal depends on a reliable confidence source.
**What to do:** Before implementing confidence-gap Inspector trigger, run calibration audit: sample N=100 Poe step outcomes, compare pre-step stated confidence to outcome correctness, plot calibration curve. Establish baseline meta-d analog before using it as a trigger.
**Risk if skipped:** Confidence–outcome gap metric fires on noise rather than signal; meta-ignorance detection fails.

### Gap 2: Structural similarity metric for case-network traversal (High)
**What's missing:** CFT describes the criss-crossed landscape conceptually but does not specify a computational similarity function. `case_diversity_score` implementation requires a metric for structural (not surface) similarity between cases.
**What to do:** Survey case-based reasoning (CBR) literature (Kolodner 1993; Leake 1996) for structural similarity metrics. Evaluate: feature-weight cosine, structure-mapping theory (Gentner 1983), or embedding-distance with task-type conditioning.
**Risk if skipped:** Case diversity score reduces to surface similarity → misses the CFT point entirely → same reductive biases.

### Gap 3: Ill-structured vs well-structured task classifier (High)
**What's missing:** The lateral-before-hierarchical zoom-out rule depends on correctly classifying a task as ill-structured. No validated heuristic exists. CFT identifies the distinction conceptually (multiple valid interpretations, no single correct solution structure) but not computationally.
**What to do:** Develop heuristic: task is ill-structured if (a) goal admits multiple decompositions without clear dominance, (b) prior skill match is low across all candidate skills, or (c) domain is flagged as ill-structured in knowledge_web. Validate against N=50 historical task types.
**Risk if skipped:** Lateral zoom-out applied to well-structured tasks (inefficient); hierarchical zoom-out applied to ill-structured tasks (produces CFT reductive biases).

### Gap 4: Minimum case base for lateral traversal (Medium)
**What's missing:** CFT is silent on the minimum case count before the criss-crossed landscape produces meaningful traversal. At 2 cases, traversal is trivial; at 20, it may be computationally expensive.
**What to do:** Empirical: log case_diversity_score vs. promotion outcomes for Poe skills; find the inflection point where additional cases stop improving transfer performance.
**Risk if skipped:** `originating_cases` threshold (2 for Lesson→Skill, 3 for Skill→Rule) is an educated guess, not an empirically derived value.

### Gap 5: AI crystallization vs human expertise analogy validity (Medium)
**What's missing:** The entire research translation assumes that human adaptive expertise mechanisms (verbalization, deliberate practice, CFT case networks) map cleanly to LLM-backed skill crystallization. This is a strong assumption. LLMs don't "practice" in the Ericsson sense; their representations are not built incrementally from cases in the way human memory is.
**What to do:** State the analogy explicitly in design docs. Treat research findings as generative hypotheses (implement and test) rather than validated design specs. Flag each implementation change for empirical validation.
**Risk if skipped:** Implementing mechanisms that are well-grounded in human expertise literature but have unknown validity for AI systems — the design is then a bet, not a specification.

### Gap 6: Double-loop / OODA evidence quality (Low priority)
**What's missing:** Argyris & Schön and Boyd are practitioner frameworks, not empirical cognitive science. Their inclusion in this synthesis is for design inspiration, not empirical grounding.
**What to do:** Do not treat DLL/OODA findings as equal-weight evidence. Use them as design heuristics, validate computationally, not by citing the research.
**Risk if skipped:** Minor. These frameworks reinforce conclusions already grounded in stronger sources; they add no unique design claims that aren't also supported empirically.

---

## Design Decisions (what NOT to do)

1. **Do NOT promote skills on use-count alone.** This is the brittle-transfer path. Frequency is a necessary but not sufficient condition.
2. **Do NOT strip case metadata at crystallization.** Loss of originating cases = loss of applicability conditions = loss of the information needed to not apply the skill incorrectly.
3. **Do NOT skip director narration under token pressure.** Narration IS the structural encoding mechanism, not a log artifact.
4. **Do NOT use identical zoom-out path for all task types.** Hierarchical zoom-out alone is correct for well-structured tasks; lateral case-traversal first is required for ill-structured tasks.
5. **Do NOT rely on explicit failure alone as zoom-out trigger.** Meta-ignorance (confident + wrong) and near-misses are the failure modes that don't self-detect. The inspector must catch them structurally.

---

## Sources

- Hatano, G. & Inagaki, K. (1986). "Two courses of expertise." In H. Stevenson, H. Azuma & K. Hakuta (Eds.), *Child Development and Education in Japan* (pp. 262–272). Freeman.
- Schwartz, D.L. & Bransford, J.D. (1999). "A time for telling." *Cognition and Instruction, 16*(4), 475–522.
- Schwartz, D.L., Bransford, J.D. & Sears, D. (2005). "Efficiency and innovation in transfer." In J. Mestre (Ed.), *Transfer of Learning from a Modern Multidisciplinary Perspective* (pp. 1–51). Information Age Publishing.
- Cleeremans, A. (2002). "Levels of representation in implicit learning." In R. French & A. Cleeremans (Eds.), *Implicit Learning and Consciousness*. Psychology Press.
- Fleming, S.M. & Dolan, R.J. (2012). "The neural basis of metacognitive ability." *Philosophical Transactions of the Royal Society B, 367*, 1338–1349.
- Miller, E.K. & Cohen, J.D. (2001). "An integrative theory of prefrontal cortex function." *Annual Review of Neuroscience, 24*, 167–202.
- Koechlin, E. & Summerfield, C. (2007). "An information theoretical approach to prefrontal executive function." *Trends in Cognitive Sciences, 11*(6), 229–235.
- Ericsson, K.A., Krampe, R.T. & Tesch-Römer, C. (1993). "The role of deliberate practice in the acquisition of expert performance." *Psychological Review, 100*(3), 363–406.
- Ericsson, K.A. & Pool, R. (2016). *Peak: Secrets from the New Science of Expertise.* Houghton Mifflin Harcourt.
- Chase, W.G. & Simon, H.A. (1973). "Perception in chess." *Cognitive Psychology, 4*, 55–81.
- Feltovich, P.J., Spiro, R.J. & Coulson, R.L. (1993). "Learning, teaching, and testing for complex conceptual understanding." In N. Frederiksen, R. Mislevy & I. Bejar (Eds.), *Test Theory for a New Generation of Tests*. Erlbaum.
- Spiro, R.J., Vispoel, W.L., Schmitz, J., Samarapungavan, A. & Boerger, A. (1987). "Knowledge acquisition for application: Cognitive flexibility and transfer in complex content domains." In B. Britton (Ed.), *Executive Control Processes*. Erlbaum.
- Argyris, C. & Schön, D. (1978). *Organizational Learning: A Theory of Action Perspective.* Addison-Wesley.
- Argyris, C. (1991). "Teaching smart people how to learn." *Harvard Business Review, 69*(3), 99–109.
- Boyd, J.R. (1987). *A Discourse on Winning and Losing* (OODA loop briefings). Air University Library.

---

*Confidence rationale: see "Confidence Analysis: Per-Finding Ratings" and "Missing Evidence" sections above. Short form: core design claims (routine/adaptive distinction, brittle transfer, metacognitive efficiency, verbalization effect) are High confidence and replicated. Implementation parameters (threshold values, interleaving rates, case counts) are engineering choices, not empirically derived. The AI-to-human analogy is the single largest unvalidated assumption in the whole document.*
