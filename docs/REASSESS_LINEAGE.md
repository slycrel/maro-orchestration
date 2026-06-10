# Re-assess Lineage — How We Got Here

**Purpose:** Preserve the conceptual path that led to the current re-assess, so we do not accidentally re-fight the same battle under new names.

This is not the architecture. It is the breadcrumb trail.

---

## Short version

The project did **not** move in a straight line from "better planning" to "better orchestration."

It moved through a more specific and more honest arc:

1. notice recurring drift / judgment failures
2. try to systematize good judgment with constraints
3. realize that synthesis narrowed the problem too much into planning
4. recover the broader frame: **stages done != completed goal**
5. recognize that **verification against reality** is a sibling, not a footnote
6. begin stepping back from decomposition-first scaffolding toward a **1-shot-first / justify-decompose** frame

That arc matters. It explains why some designs looked promising, why they later felt overfit, and why the re-assess needs to guard against elegant local solutions that miss the real defect.

---

## The lineage

### 1) The original pressure: judgment drift, not planning elegance
The motivating pain was not "the planner needs more structure."

It was closer to:
- the system can look coherent while being wrong
- the loop can exit cleanly without the goal actually being satisfied
- self-reported completion is not ground truth
- the system needs stronger judgment and validation across execution, not just before execution

This is the root concern.

### 2) Constraint orchestration was a sincere but partial capture
The constraint/scope work was an attempt to operationalize good judgment:
- draw the rectangle first
- narrow the possibility space
- make failure modes explicit up front
- systematize the kind of taste a strong engineer applies naturally

That was a real insight.

But the design capture narrowed the broader problem into **pre-planning constraint generation**.

Relevant doc:
- `docs/CONSTRAINT_ORCHESTRATION_DESIGN.md`

### 3) The corrective review: planning and verification are different failures
The critical review made the load-bearing correction:
- the design helps **planning**
- the motivating defect was largely in **verification**
- a perfect scope rail still does not catch "nobody ran a browser"

The sharp formulation:
- constraint orchestration and verification-with-real-feedback are **siblings**, not substitutes

Relevant doc:
- `docs/CONSTRAINT_ORCHESTRATION_REVIEW.md`

That correction is one of the most important intellectual moves in this branch.

### 4) Scope became one technique, not the answer
After the correction, scope/constraints remained interesting, but as:
- a candidate technique
- for one slice of the broader control problem
- not the master key

This is the right scale for it.

### 5) The broader frame re-emerged: stages done != completed goal
Once the planning/verification split became explicit, the larger defect came back into focus:
- a system can complete its internal sequence and still fail the actual user goal
- loop success and goal success are different things
- reality-contact has to be first-class, not a garnish added after nice orchestration diagrams

This is the same family as:
- "nobody ran a browser"
- "the artifact does not exist even though the loop says done"
- "labels drift away from operational reality"

This line connects directly to the drift guard.

Relevant doc:
- `docs/REASSESS_DRIFT_GUARD.md`

### 6) The bitter-lesson step-back: maybe decomposition is the thing over-earning explanation
A later step-back pushed even harder:
- instead of adding more scaffolding around planning/decomposition,
- try the goal in one shot first,
- and make decomposition the escape hatch that must justify itself.

This is not anti-structure in general. It is suspicion toward default ceremony.

Relevant note:
- `BACKLOG.md` → "DISCUSS — invert the planning stage: 1-shot first, escape hatch second"

If this frame wins, some unbuilt scope/constraint machinery may be anti-features rather than deferred features.

---

## What should survive the re-assess

Even if specific designs are dropped, several insights seem durable:

- **Judgment is the real problem, not just decomposition quality**
- **Planning and verification are distinct control surfaces**
- **Reality-contact must outrank elegant self-description**
- **Scope/constraints can help, but only as one technique among others**
- **Default scaffolding should be treated as guilty until proven load-bearing**
- **The system should stay aimed at the user’s goal over ordered time, not merely complete internal stages**

---

## Practical use

When reviewing a new mechanism, ask:
- Is this solving the actual defect, or a narrowed surrogate of it?
- Is this about planning, verification, or both?
- Does it improve target retention against reality, or just improve internal neatness?
- If decomposition were no longer the default, would this mechanism still deserve to exist?

If those questions are not answered, the design is probably rephrasing old confusion.
