# Tacit vs Explicit Knowledge in Expertise Theory

**Date:** 2026-04-05
**Phase:** 29 — Human Psychology Research Track
**Status:** Complete

---

## Question

What distinguishes tacit knowledge from explicit knowledge, how does expertise research (Dreyfus model, Polanyi, SECI) characterize the Stage 4 to Stage 5 transition, and what are the design implications for crystallization systems operating across the pipeline stages: Fluid → Lesson → Identity → Skill → Rule?

---

## Key Findings

### 1. The Tacit/Explicit Distinction

**Polanyi's formulation** (*The Tacit Dimension*, 1966): "We can know more than we can tell." Tacit knowledge is personal, context-specific, embodied — embedded in action and perception. The expert does not attend *to* the tool; they attend *through* it (subsidiary vs focal awareness). Explicit knowledge is articulable, transmissible in formal language, and codifiable independent of the knower.

**Dreyfus's mapping** (*Mind over Machine*, 1986): Expertise development is a progression from context-free rule-following (novice) toward context-sensitive, non-reflective response (expert). The critical insight: as skill develops, explicit rules become tacit. This is not loss — it is integration.

**SECI model** (Nonaka & Takeuchi, 1995): Knowledge conversion operates in four modes:
- **Socialization** (tacit → tacit): apprenticeship, observation
- **Externalization** (tacit → explicit): articulation, metaphor, crystallization — the hardest conversion
- **Combination** (explicit → explicit): synthesis, categorization
- **Internalization** (explicit → tacit): practice until rules dissolve into action

The key insight: the externalization bottleneck is real and irreversible. Tacit → explicit conversion always loses signal. There is no lossless path.

### 2. The Introspection Paradox

All three frameworks converge on the same finding: asking experts to articulate their knowledge degrades performance. Forcing a Stage 5 expert to explain regresses them toward Stage 3-4 behavior. The tacit structure is disrupted by focal attention. This is not a communication failure — it is a property of how tacit knowledge works.

### 3. The Stage 4 → Stage 5 Transition

This is the most critical transition in expertise theory — the shift from Proficient to Expert:

| Dimension | Stage 4 (Proficient) | Stage 5 (Expert) |
|-----------|---------------------|-----------------|
| Perception | Holistic, pattern-based | Intuitive, perceptual gestalt |
| Decision | Deliberate, conscious | Eliminated — intuition IS response |
| Rules | Articulable, accessible | Subsidiary — present but inaccessible |
| Error recovery | Explicit troubleshooting | Anomaly detection before formulation |
| Teachability | High | Low without contextual demonstration |

**Three mechanisms drive the transition:**
1. **Chunking**: pattern-action pairs compress; intermediate rule-retrieval steps disappear
2. **Subsidization** (Polanyi): rules shift from focal to subsidiary attention — still operative but no longer addressable
3. **Embodied schema formation**: response-ready schemata enacted before the reflective mind formulates

**SECI framing**: Stage 4→5 = closing of the Externalization-Internalization cycle. At Stage 5, knowledge has fully internalized — the SECI loop has run to completion for that domain. The expert's tacit knowing IS the crystallized outcome of all prior explicit learning.

**Transition is qualitative, not incremental.** It cannot be coached by more rules. It requires accumulated practice volume, pattern exposure, and failure correction at sufficient scale.

### 4. Tacit/Explicit Mapping to the Crystallization Pipeline

| Pipeline Stage | Knowledge Type | Primary Risk | Design Principle |
|---------------|---------------|-------------|-----------------|
| **Fluid** | Pre-differentiated; tacit dominates | Premature articulation destroys tacit signal | Preserve context metadata without structuring |
| **Lesson** | Primarily explicit (externalization product) | Tacit remainder discarded; felt sense lost | Narrative/reflective prompts before structured extraction |
| **Identity** | Explicit frame + tacit behavioral register | Declared identity diverges from enacted identity | Validate against behavioral patterns, not self-report |
| **Skill** | Explicit-to-tacit bridge (internalization target) | Treating Skill as stored procedure, not practiced capacity | Require demonstration; score against live performance |
| **Rule** | Fully explicit; tacit-origin risks stripped | Rule without context loses boundary conditions | Embed origin context, confidence decay, failure modes |

---

## Implications

### For Crystallization System Design

**1. Explicit knowledge can be stored; tacit knowledge must be invoked.**
A system that treats all knowledge as explicit will systematically lose the most valuable signal. The pipeline must distinguish storage (explicit) from invocation (tacit) at every stage.

**2. Fluid → Lesson: protect the tacit texture before extraction.**
Use narrative/reflective prompts ("what mattered and why") before structured extraction. Never run JSON extraction on raw experience as the first operation. Preserve timing, emotional valence, and situational metadata.

**3. Lesson → Skill: include failure context, not just success patterns.**
Rules extracted only from successes lack boundary conditions. A rule without its failure mode is a trap. The crystallization system must actively seek and embed the conditions under which a lesson breaks.

**4. Skill validation requires demonstration, not self-report.**
A skill is tacit knowledge in progress. The test is performance under conditions, not stored text. The system must route skills through a demonstration/scoring path, not a retrieval path.

**5. Rule: attach confidence decay and origin context.**
Explicit rules degrade as context drifts. Without the original tacit context (the Fluid → Lesson transition), rules misfire silently. Embed: source situation, confidence level, last-validated date, known failure modes.

**6. Identity validation: enacted vs declared.**
Identity crystallizations are explicit statements of behavioral disposition. They are only as good as the behavioral record they were derived from. Validate identity claims against observed pattern history — do not trust self-generated identity declarations without behavioral grounding.

**7. The introspection paradox as a design constraint.**
The system cannot ask an expert "explain your tacit knowledge" and expect useful output. Instead: observe behavior under variation, infer rules from pattern divergence, extract lessons at failure boundaries (where tacit structure breaks and explicit attention is forced).

**8. Design the crystallization loop as SECI cycles, not one-way extraction.**
The system should close the internalization loop: explicit rules → practiced invocation → tacit absorption → re-crystallization when context shifts. A one-way Fluid→Rule pipeline will produce brittle rules without behavioral grounding.

### Failure Modes to Avoid

- **Articulation capture**: assuming that what an expert can say equals what they know
- **Rule completeness illusion**: assuming a well-formed rule covers its domain
- **Identity drift**: identity crystallizations diverging from behavioral record over time
- **Skill decay without invocation**: skills stored but not exercised degrade silently
- **Premature structuring**: applying JSON/schema to raw Fluid data before narrative extraction

---

## Sources

| Source | Key Contribution |
|--------|----------------|
| Polanyi, M. (1966). *The Tacit Dimension*. Doubleday. | Tacit/explicit distinction; subsidiary vs focal awareness; "we can know more than we can tell" |
| Dreyfus, H. & Dreyfus, S. (1980). *A Five-Stage Model of the Mental Activities Involved in Directed Skill Acquisition*. University of California. | Five-stage model; Stage 4→5 qualitative transition; intuition vs deliberation |
| Dreyfus, H. & Dreyfus, S. (1986). *Mind over Machine*. Free Press. | Full elaboration of skill acquisition model; expert holistic perception |
| Nonaka, I. & Takeuchi, H. (1995). *The Knowledge-Creating Company*. Oxford University Press. | SECI model; Socialization/Externalization/Combination/Internalization; externalization bottleneck |
| Ericsson, K.A. et al. (1993). "The Role of Deliberate Practice in the Acquisition of Expert Performance." *Psychological Review* 100(3):363–406. | Deliberate practice volume requirements; failure-correction cycle as learning mechanism |
| Anderson, J.R. (1982). "Acquisition of Cognitive Skill." *Psychological Review* 89(4):369–406. | ACT* theory; proceduralization and compilation as tacitization mechanisms |

---

*Research artifact for Phase 29 — Human Psychology Research Track. Part of the crystallization system design series.*
