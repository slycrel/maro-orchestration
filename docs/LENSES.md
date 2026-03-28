# Multi-Lens Introspection

This document defines the intended split between **lenses** and **personas** for orchestration self-reflection.

## Core distinction

- **Lenses** are analytical perspectives over shared run artifacts.
- **Personas** are optional implementation tools a lens may use when heuristic analysis is not enough.

In other words:
- a cost lens does not need an LLM; it can be arithmetic over traces
- a quality lens may need an LLM; when it does, it can invoke a reviewer/critic persona
- the **lens is the concept**
- the **persona is optional plumbing**

This separation keeps introspection extensible without forcing every analysis path through the persona system.

## Design goals

1. **Action, not commentary**
   Every lens must emit a recommended action, not just a report.
2. **Shared evidence substrate**
   Lenses should analyze the same run artifacts/logs/traces rather than building separate evidence silos.
3. **Heuristic first**
   Cheap deterministic lenses should run by default.
4. **Selective LLM use**
   Expensive LLM-backed lenses should run on failure, before escalation, or on high-stakes tasks.
5. **Aggregation matters**
   The system should combine lens outputs into a synthesized diagnosis with confidence and next action.

## Shared artifact set

Candidate common inputs for all lenses:
- loop/run logs
- planned step list + replan diffs
- step timing + retry counts
- token/cost accumulation
- block reasons
- produced artifacts
- validation outputs
- final status / partial result state

## Proposed initial lens registry

### Always-on heuristic lenses

#### ExecutionLens
Questions:
- Is the run making progress?
- Did a step complete, retry, time out, or block?
- Is the worker looping on the same outcome?

Outputs:
- failure mode / progress classification
- recommended action: retry, decompose, abort, escalate, continue

#### CostLens
Questions:
- Where are tokens/time being spent?
- Is the task burning budget in the wrong place?
- Did decomposition reduce or increase cost?

Outputs:
- cost hotspot
- recommended action: shrink step, switch model tier, summarize, checkpoint, continue

#### ArchitectureLens
Questions:
- Is the plan shape sane?
- Are steps too broad, too coupled, or in the wrong order?
- Are dependencies/orderings wrong?

Outputs:
- structural diagnosis
- recommended action: split step, reorder plan, parallelize, introduce bridge/helper

#### ForensicsLens
Questions:
- What exact event or transition caused failure?
- Which exception, timeout, constraint, or validation signal was decisive?

Outputs:
- concrete root-cause candidate with evidence
- recommended action: patch, instrument more, retry with trace logging

### Selective LLM-backed lenses

#### QualityLens
Default backend:
- reviewer / critic persona (or equivalent LLM analysis)

Questions:
- Is the output actually good?
- Is it shallow, vague, or merely stylistically polished?

Outputs:
- quality critique
- recommended action: deepen, add verification, reject, accept

#### UserIntentLens
Default backend:
- reality-checker / user-model persona

Questions:
- Is the run solving the thing Jeremy actually meant?
- Did the system drift into technically interesting but off-target work?

Outputs:
- intent alignment judgment
- recommended action: redirect, tighten success criteria, continue

#### CriticLens
Default backend:
- skepticism / critic persona

Questions:
- What is overbuilt, fake, brittle, or self-congratulatory?
- What assumptions are being smuggled in untested?

Outputs:
- adversarial critique
- recommended action: simplify, falsify, add test, cut feature

## Invocation policy

### Run always
- ExecutionLens
- CostLens
- ArchitectureLens
- ForensicsLens

These are cheap and should become standard ambient introspection.

### Run selectively
- QualityLens
- UserIntentLens
- CriticLens

Trigger when:
- a run fails or blocks repeatedly
- a run exceeds budget/time thresholds
- before escalating to human
- before finalizing high-stakes work
- when output quality is uncertain

## Aggregation / synthesis

A lens output by itself is not enough. The introspection subsystem should synthesize across lenses.

Example:
- ExecutionLens: repeated timeout on same step
- CostLens: 70% of tokens spent on one oversized step
- ArchitectureLens: step combines repo acquisition + analysis + synthesis

Synthesis:
- confidence: high
- diagnosis: decomposition granularity failure
- action: split into setup / map / docs / code / synthesis phases and rerun from checkpoint

## Why lenses should not just be personas

If every lens is forced through the persona system:
- cheap heuristic diagnosis becomes expensive
- observability becomes coupled to agent orchestration
- introspection risks becoming narrative instead of control logic

Using personas as optional backends preserves flexibility:
- heuristics where possible
- personas where useful
- one common lens interface either way

## Suggested implementation shape

```text
RunObserver -> TraceStore -> LensRegistry
                         -> ExecutionLens
                         -> CostLens
                         -> ArchitectureLens
                         -> ForensicsLens
                         -> (optional) QualityLens via reviewer persona
                         -> (optional) UserIntentLens via reality-checker persona
                         -> (optional) CriticLens via skepticism persona
                                     ↓
                               LensAggregator
                                     ↓
                       diagnosis + confidence + next action
```

## Near-term roadmap guidance

Recommended first build:
1. ExecutionLens
2. CostLens
3. ArchitectureLens
4. ForensicsLens
5. LensAggregator

Then add:
6. QualityLens
7. UserIntentLens
8. CriticLens

## Anti-goals

Do **not** turn this into:
- endless internal committee meetings
- multiple personas saying the same thing in different costumes
- introspection that produces only reports and no action
- expensive LLM analysis for problems heuristics can already diagnose

## Definition of success

The system should be able to say:
- what happened
- why it likely happened
- how confident it is
- what to do next
- whether this should graduate into a durable rule or guardrail

That is the path from interesting introspection to actual self-steering orchestration.
