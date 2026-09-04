---
status: living
---

# Contract-testing input for the successor (distilled 2026-09-04)

*Distilled in my own words from Jeremy's work-side contract-spec practice
(eight rounds of edge-by-edge measurement, 2026-08-27 → 09-02). The source is
machine-local and work-internal; nothing here quotes it. What transfers is the
SHAPE of the practice, mapped onto Maro's process edges per D16: contracts
govern process artifacts; thoughts flow through unconstrained.*

## The shape that transfers

1. **Two files per edge, never one.** A GENERATED file derived from the
   source at a ref (regeneration is the review: if the diff moves, the
   contract moved) and a DECLARED file a human edits, where **every line is
   executable** — it drives a test that can fail, or it does not belong. Maro
   today has the declared half (`docs/CONTRACTS.md`) and the census half
   (tripwires) but not the generated half; the successor's registry should be
   derivable from the Go types, with the prose file holding only what no
   generator can know.
2. **Three states, one severity model.** Every dimension of an edge is
   *derived* (asserted automatically), *declared* (asserted; fails if the
   system disagrees), or *undefined* (nobody looked → a WARNING that is never
   silenced). Absence of a constraint is never evidence of absence of a
   problem. This is the "found 0 is untrusted" rule one level down.
3. **Errors in exactly three ways:** two derived facts contradict; a declared
   line fails its test; an illegal combination (e.g. a value that feeds an
   authority decision AND accepts unknowns unchanged). Everything else is a
   warning. Keeps the guard honest about what it actually proved.
4. **Constraints are a third flavour with no safe direction.** Hard/soft
   classifies structure; a constraint on a VALUE (range, length, pattern,
   cardinality, cross-field) can break writers when tightened and readers
   when loosened. Its tri-state: *defined* / *unconstrained* (someone looked
   and decided any value of the type is legal — declared, executable: feed an
   absurd value, assert acceptance) / *undefined*. **D16 lands here:** thought
   payloads are declared `unconstrained` on purpose; process artifacts are
   `defined`. The prototype's caps were undefined constraints that got
   enforced anyway.
5. **Lifecycle is the one non-executable declaration** and it gates whether
   tests should exist: *stable* (a new consumer could adopt as-is) /
   *transitional* (meant to die; tripwire only) / *internal-loose* (deliberate,
   warnings stand) / *hardened-legacy* (wrong-but-shipping; guard like stable
   PLUS a design-flag naming what is wrong, who owns it, the sunset trigger;
   wins ties with stable) / *design-pending* (shape unowned or contested;
   suppress test generation, escalate). A generated suite on a contested
   shape is worse than none — it formalises a shape nobody chose. Maro's
   registry should carry this per edge; several Python edges are
   hardened-legacy by this definition (events without handle_id).
6. **Deliverable classes: tests, warnings, or a design escalation** — an
   escalation is a FILE beside the pair (question, evidence, candidate
   futures, recommendation), not a paragraph. Two measured triggers: the
   underivables cluster (auth AND vocabulary AND constraints all undefined on
   one edge = nobody designed it); two governing documents name different
   owners.
7. **The provider takes both chairs.** The writer of an artifact ships its
   emit-side pins AND a reference reader proving forwards compatibility (a
   newer payload still reads) and backwards compatibility (an older payload
   still reads, with declared absence semantics) — even when no consumer
   exists yet, especially then. Consumers test compliance adversarially and
   never define the contract. For the successor: the Go engine is the
   provider of its workspace; the behavior suite is its reference reader;
   Python's suite is a consumer of the shared spec, not its author.
8. **Provenance on every declaration: supplied or inferred.** Inferred (read
   off the implementation) is legitimate but is evidence of what IS, not what
   was MEANT. Search for a stated intent before inferring; record which you
   found. Most of Phase 1a was inferred and should say so.
9. **Absence has two axes.** Wire form (omitted / null / empty / never) and
   observable behaviour on absence (a status / tolerated / default), plus
   whether absence is even possible (yes / no / by-construction — true now,
   not a type guarantee). Presence: always vs data-dependent. Maro's B3
   "presence is evidence, absence proves nothing" is this rule stated once;
   it should be a per-field declaration.
10. **Fail-soft consumers are a named defect class.** A reader that degrades
    EVERY failure to a value that is also a legitimate answer must declare
    its collapse set (what it swallows, what is indistinguishable from
    success) and what a fixed version would need to surface. Measured on an
    edge that rendered a 16-day outage identically to normal operation. Maro
    is full of these (`_write_step_artifact` swallows, bare excepts around
    log writes). The successor declares each one or removes it.
11. **Retry vs recovery, and the provider cannot enforce either.** A
    provider REQUESTS retry guidance per status (none / idempotent-safe /
    backoff); a consumer ignoring it is a warning against the consumer, never
    an error; the provider's own executable obligation is idempotency on
    repeat. Recovery (a state machine sweeping over hours) is not retry and
    cannot be forbidden by a contract — the contract can only make the
    terminal case legible (a distinct status, a structured body) so recovery
    can stop. Maps directly to Maro's stuck/blocked verdicts: the engine
    requests, the caller's state machine decides; the verdict's job is to be
    distinguishable.
12. **Renames do not exist.** A rename is a delete plus an add, and deletes
    walk the deprecation lifecycle. No rename detection; the diff shows a
    removal and that is a break.
13. **Per-status semantics.** What a status MEANS, what triggers it, its
    granularity (response-level vs per-element — one bad element making the
    whole response non-success is a different hazard), and which statuses are
    never emitted by the handler vs emitted below it by the stack. For the
    successor's events: which lifecycle events the engine itself emits vs
    which a backend or the OS injects.
14. **Transports beyond request/response** — the ones that map onto Maro's
    workspace directly:
    - *table*: the schema IS the contract; declare writers, readers, and
      **vocabulary-consistency** (the authority for a value set, every
      reader's status: exact / proper-subset / fetched-never-read, and
      separately whether it is ENFORCED and where). Every JSONL ledger in the
      workspace is a table transport. `success_class` vocabulary across
      writers/readers is the first candidate.
    - *config-bundle*: absence is a LAYER question (precedence, namespace,
      silent-divergent-default, unread). Maro's two-tier config is this
      transport; the `bool("false")` class from Phase 1a was
      `silent-divergent-default` with no name.
    - *token*: a claim set replayed everywhere with its own gates, each with
      its own enforcement point including "written but not checked". The
      dispatch envelope / provenance stamp is this transport.
15. **Answer key travels with the files.** A generated glossary: per key, per
    value, the meaning, a concrete wire example, and the evidence crumb (how
    it was measured, where). Otherwise the vocabulary is jargon with a
    confident face. `docs/CONTRACTS.md` §A is the start of one.
16. **`measured-by:` on every header claiming a measurement**, with
    `not-re-runnable-here` as an explicit legal value — the honest answer
    for a fact CI cannot re-establish, never a licence to omit.
17. **Circularity is real.** Guards passing is weaker evidence when the
    guard's author is the system under test; blind reviewers picked the
    AI-authored artifacts as weakest. Mitigations: a blind test-writer
    dry-run; checking a generated pair against an independently written
    one. Maro's answer so far is cross-model review plus mutation
    kill-proof; the successor should add the independent-pair check for
    its registry.

## What this changes in the Phase 2 design note

- The registry becomes derived + declared + lifecycle per edge, with an
  answer key, and `unconstrained` as a first-class declaration for every
  thought payload (D16).
- Every workspace ledger is a table transport with a vocabulary-consistency
  block; config is a config-bundle transport with a precedence block.
- Every best-effort writer and every swallowing reader in the backbone is
  either declared as fail-soft with its collapse set, or does not exist.
- Stuck/blocked/closure verdicts are designed as distinguishable terminal
  cases first, thresholds second (D13, D16).
- The behavior suite is restated as the provider's reference reader; the
  Python suite becomes a consumer of the shared spec.
