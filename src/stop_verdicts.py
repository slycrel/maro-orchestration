"""Typed stop verdicts — why a run stopped short of clean success.

Compound-thinking §9.4/§13b (docs/COMPOUND_THINKING_DESIGN.md): a stop is
an observation, not a fact. Every verdict is recorded WITH its evidence
(the ``stop_evidence`` string beside it) and carries a type-derived reopen
condition — a dead end doesn't stay a dead end. The stop-path survey
(docs/history/2026-07-23-stop-path-survey.md) is the seam map this
vocabulary was wired against.

Four verdicts are goal-directed — observations about the goal itself:

  out-of-budget            A preset cap ended the run (daily spend,
                           max_iterations, token/cost budget, wall-clock,
                           restart depth). Possibility untested. Reopens
                           trivially with budget.
  thesis-refuted           Avenues exhausted with convergence evidence —
                           repeated attempts stopped changing the outcome.
                           Reopens on new connection evidence (a new
                           landmark or vantage).
  reachable-but-not-worth-it  A path exists but its discovered cost exceeds
                           the value (director escalation "close"). Reopens
                           when the cost or value estimate moves.
  lost-the-plot            Work completed coherently but does not serve the
                           original ask (closure demotion, provenance
                           guard). Reopens on re-anchor against the ask.

One value is an event marker, NOT a verdict (Jeremy's decree, GOAL_BRAIN
2026-07-27 item 5: "the four verdicts are observations about the map; an
interrupt is an event about the run"):

  external-interrupt       Kill switch, operator stop, backend death,
                           merge/infra failure, awaiting-human hold. The
                           goal was not (fully) probed — this row carries
                           no evidence about the goal and learning
                           consumers must not treat it as failure evidence.
                           Reopens when the interruption clears.

The decree asks for run-level interrupted:reason PLUS whichever verdict
the evidence supported at interrupt time. This module implements that
with one field and precedence rather than two fields: every break site
that observes map evidence stamps its goal verdict first-write-wins, so
by the time interrupt machinery stamps, a supported verdict already owns
the field and survives (Jeremy's run-3 example reads "out-of-budget,
work product delivered" — the cost breaker stamped before the stop).
The external-interrupt marker appears only when NO map observation
existed at interrupt time, and the reason travels in ``stop_evidence``.
``GOAL_VERDICTS`` is the taxonomy; the marker is deliberately outside it.

The verdict rides BESIDE existing fields, never replaces them: LoopResult/
LoopContext carry ``stop_verdict``/``stop_evidence`` in-flight, run
metadata.json and the outcome-ledger row persist them, and
run_curation.classify_outcome forwards them onto the run card. ``status``
vocabulary is unchanged.
"""

from __future__ import annotations

OUT_OF_BUDGET = "out-of-budget"
THESIS_REFUTED = "thesis-refuted"
NOT_WORTH_IT = "reachable-but-not-worth-it"
LOST_THE_PLOT = "lost-the-plot"
EXTERNAL_INTERRUPT = "external-interrupt"

# Goal-directed verdicts: observations about the goal, admissible as
# learning evidence subject to verdict_trust. EXTERNAL_INTERRUPT is
# deliberately NOT here — it's an event marker, not a verdict (see module
# docstring; Jeremy's decree 2026-07-27 item 5).
GOAL_VERDICTS = frozenset(
    (OUT_OF_BUDGET, THESIS_REFUTED, NOT_WORTH_IT, LOST_THE_PLOT)
)

# Every value the stop_verdict FIELD may legally hold (the four verdicts
# plus the interrupt event marker). Named to avoid implying five verdicts.
VALID_STOP_VALUES = GOAL_VERDICTS | {EXTERNAL_INTERRUPT}

# Statuses that already encode the external-interrupt family distinctly at
# the status level (the survey's "taxonomy hole" rows). classify_outcome
# uses this to derive a fallback verdict for runs whose stop site predates
# the verdict wiring or never reached a stamp.
INTERRUPT_STATUSES = frozenset(
    ("interrupted", "stranded", "refused_busy", "clarification_needed")
)
