#!/usr/bin/env python3
"""Replay-based fitness oracle — deterministic strategy evaluation from past outcomes.

Phase 50 steal: replace LLM-mediated inspector scoring with a deterministic
replay scorer that runs candidate strategies against past outcomes from
outcomes.jsonl. No LLM in the eval path.

Core mechanism:
1. Load past outcomes (TF-IDF ranked by similarity to strategy description)
2. Score strategy based on how similar past outcomes performed
3. Return deterministic fitness score: 0.0-1.0

Why this works: if a strategy looks like past strategies that succeeded, it's
likely to succeed. If it looks like patterns that failed, it's likely to fail.
The replay oracle avoids LLM hallucination in fitness evaluation.

Usage:
    from strategy_evaluator import evaluate_strategy, StrategyFitnessReport

    report = evaluate_strategy("search the web for X and summarize")
    print(report.fitness_score, report.confidence, report.similar_outcomes)
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("poe.strategy_evaluator")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Minimum number of similar outcomes needed to report a confident score
MIN_OUTCOMES_FOR_CONFIDENCE = 3

# TF-IDF similarity threshold below which outcomes are not considered similar
SIMILARITY_THRESHOLD = 0.1

# Outcome status weights for fitness computation
_STATUS_WEIGHTS: Dict[str, float] = {
    "done": 1.0,
    "partial": 0.5,
    "stuck": 0.0,
    "error": 0.0,
}

# Stop words for TF-IDF tokenization
_STOP = {
    "the", "a", "an", "and", "or", "for", "to", "in", "of", "is", "it",
    "this", "that", "with", "from", "are", "were", "have", "has", "had",
    "its", "but", "not", "you", "all", "can", "will", "more", "than",
    "been", "into", "task", "step", "goal", "result", "output",
}

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class SimilarOutcome:
    """A past outcome that resembles the candidate strategy."""
    outcome_id: str
    goal: str
    status: str
    similarity: float     # TF-IDF cosine similarity to strategy text
    weight: float         # similarity × status_weight (contribution to fitness)
    summary: str = ""


@dataclass
class StrategyFitnessReport:
    """Deterministic fitness evaluation of a candidate strategy.

    All fields derived from past outcomes — no LLM calls.
    """
    strategy: str                        # The strategy text being evaluated
    fitness_score: float                 # 0.0-1.0: weighted pass-rate from similar outcomes
    confidence: float                    # 0.0-1.0: based on # and quality of similar outcomes
    similar_outcomes: List[SimilarOutcome]  # Matched past outcomes (ranked)
    outcomes_searched: int               # Total outcomes searched
    done_count: int                      # # of similar outcomes that were "done"
    stuck_count: int                     # # of similar outcomes that were "stuck"
    above_threshold: int                 # # of outcomes with similarity ≥ SIMILARITY_THRESHOLD
    verdict: str                         # "PASS" | "FAIL" | "UNCERTAIN"
    notes: List[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"StrategyFitnessReport",
            f"  strategy: {self.strategy[:60]}",
            f"  fitness_score={self.fitness_score:.2f} confidence={self.confidence:.2f} verdict={self.verdict}",
            f"  outcomes: {self.done_count} done / {self.stuck_count} stuck / {self.above_threshold} matched",
        ]
        if self.notes:
            lines.extend(f"  note: {n}" for n in self.notes)
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# TF-IDF engine (no deps)
# ---------------------------------------------------------------------------


def _tokenize(text: str) -> List[str]:
    return [
        t for t in re.sub(r"[^a-z0-9]+", " ", text.lower()).split()
        if t not in _STOP and len(t) > 2
    ]


def _tfidf_cosine(query_terms: List[str], docs: List[List[str]]) -> List[float]:
    """Return cosine similarity of query against each doc. Pure stdlib."""
    if not docs or not query_terms:
        return [0.0] * len(docs)

    all_docs = [query_terms] + docs
    n = len(all_docs)

    # IDF: log(N / df + 1) + 1
    df: Counter = Counter()
    for doc in all_docs:
        for term in set(doc):
            df[term] += 1

    def idf(t: str) -> float:
        return math.log(n / (df.get(t, 0) + 1)) + 1.0

    def vec(terms: List[str]) -> Dict[str, float]:
        tf = Counter(terms)
        total = max(len(terms), 1)
        return {t: (c / total) * idf(t) for t, c in tf.items()}

    def cosine(v1: Dict[str, float], v2: Dict[str, float]) -> float:
        dot = sum(v1.get(t, 0.0) * v2.get(t, 0.0) for t in v1)
        n1 = math.sqrt(sum(x * x for x in v1.values())) or 1.0
        n2 = math.sqrt(sum(x * x for x in v2.values())) or 1.0
        return dot / (n1 * n2)

    qvec = vec(query_terms)
    return [cosine(qvec, vec(doc)) for doc in docs]


# ---------------------------------------------------------------------------
# Core evaluator
# ---------------------------------------------------------------------------


def evaluate_strategy(
    strategy: str,
    *,
    outcomes: Optional[List[Any]] = None,
    max_outcomes: int = 100,
    top_k: int = 10,
    pass_threshold: float = 0.6,
    fail_threshold: float = 0.35,
) -> StrategyFitnessReport:
    """Score a candidate strategy by replaying it against past outcomes.

    The fitness score is the similarity-weighted pass-rate of similar past
    outcomes — a high score means strategies like this one have historically
    succeeded; a low score means they've failed.

    Args:
        strategy:         The strategy text to evaluate (skill description,
                          evolver suggestion, or step template).
        outcomes:         Pre-loaded outcomes list. If None, loads from disk.
        max_outcomes:     Max outcomes to search (most recent first).
        top_k:            Number of similar outcomes to include in report.
        pass_threshold:   fitness_score ≥ this → PASS verdict.
        fail_threshold:   fitness_score ≤ this → FAIL verdict.

    Returns:
        StrategyFitnessReport with deterministic fitness score.
    """
    if outcomes is None:
        try:
            from memory import load_outcomes
            outcomes = load_outcomes(limit=max_outcomes)
        except Exception as exc:
            log.warning("evaluate_strategy: failed to load outcomes: %s", exc)
            outcomes = []

    outcomes_searched = len(outcomes)

    if not outcomes:
        return StrategyFitnessReport(
            strategy=strategy,
            fitness_score=0.5,  # neutral — no data
            confidence=0.0,
            similar_outcomes=[],
            outcomes_searched=0,
            done_count=0,
            stuck_count=0,
            above_threshold=0,
            verdict="UNCERTAIN",
            notes=["no outcomes available — returning neutral score"],
        )

    strategy_terms = _tokenize(strategy)
    if not strategy_terms:
        return StrategyFitnessReport(
            strategy=strategy,
            fitness_score=0.5,
            confidence=0.0,
            similar_outcomes=[],
            outcomes_searched=outcomes_searched,
            done_count=0,
            stuck_count=0,
            above_threshold=0,
            verdict="UNCERTAIN",
            notes=["strategy text has no meaningful tokens"],
        )

    # Build per-outcome search text (goal + summary + lessons)
    outcome_docs: List[List[str]] = []
    for o in outcomes:
        text = o.goal
        if hasattr(o, "summary") and o.summary:
            text += " " + o.summary
        if hasattr(o, "lessons") and o.lessons:
            text += " " + " ".join(o.lessons)
        outcome_docs.append(_tokenize(text))

    similarities = _tfidf_cosine(strategy_terms, outcome_docs)

    # Score each outcome
    scored: List[Tuple[float, Any, float]] = []
    for outcome, sim in zip(outcomes, similarities):
        if sim < SIMILARITY_THRESHOLD:
            continue
        status_wt = _STATUS_WEIGHTS.get(outcome.status, 0.5)
        weight = sim * status_wt
        scored.append((sim, outcome, weight))

    scored.sort(key=lambda x: x[0], reverse=True)
    above_threshold = len(scored)

    if not scored:
        return StrategyFitnessReport(
            strategy=strategy,
            fitness_score=0.5,
            confidence=0.0,
            similar_outcomes=[],
            outcomes_searched=outcomes_searched,
            done_count=0,
            stuck_count=0,
            above_threshold=0,
            verdict="UNCERTAIN",
            notes=[f"no outcomes above similarity threshold {SIMILARITY_THRESHOLD:.2f}"],
        )

    # Compute similarity-weighted fitness
    total_sim = sum(s for s, _, _ in scored)
    fitness_score = sum(s * _STATUS_WEIGHTS.get(o.status, 0.5) for s, o, _ in scored) / max(total_sim, 1e-9)

    done_count = sum(1 for _, o, _ in scored if o.status == "done")
    stuck_count = sum(1 for _, o, _ in scored if o.status in ("stuck", "error"))

    # Confidence: grows with number of similar outcomes, saturates at MIN_OUTCOMES_FOR_CONFIDENCE
    confidence_raw = min(above_threshold / MIN_OUTCOMES_FOR_CONFIDENCE, 1.0)
    # Also scale by mean similarity of matched outcomes
    mean_sim = total_sim / max(above_threshold, 1)
    confidence = confidence_raw * min(mean_sim * 3.0, 1.0)

    # Build top-k SimilarOutcome objects
    top_scored = scored[:top_k]
    similar_outcomes = [
        SimilarOutcome(
            outcome_id=o.outcome_id,
            goal=o.goal[:80],
            status=o.status,
            similarity=round(s, 3),
            weight=round(w, 3),
            summary=getattr(o, "summary", "")[:100],
        )
        for s, o, w in top_scored
    ]

    # Verdict
    if fitness_score >= pass_threshold and confidence >= 0.3:
        verdict = "PASS"
    elif fitness_score <= fail_threshold and confidence >= 0.3:
        verdict = "FAIL"
    else:
        verdict = "UNCERTAIN"

    notes: List[str] = []
    if done_count == 0 and above_threshold > 0:
        notes.append("no similar past outcomes succeeded — high risk")
    if confidence < 0.3:
        notes.append(f"low confidence ({confidence:.0%}) — fewer than {MIN_OUTCOMES_FOR_CONFIDENCE} similar outcomes")

    report = StrategyFitnessReport(
        strategy=strategy,
        fitness_score=round(fitness_score, 3),
        confidence=round(confidence, 3),
        similar_outcomes=similar_outcomes,
        outcomes_searched=outcomes_searched,
        done_count=done_count,
        stuck_count=stuck_count,
        above_threshold=above_threshold,
        verdict=verdict,
        notes=notes,
    )

    log.info(
        "strategy_eval: fitness=%.2f confidence=%.2f verdict=%s similar=%d/%d strategy=%r",
        fitness_score, confidence, verdict, above_threshold, outcomes_searched,
        strategy[:50],
    )

    return report


def evaluate_skill(skill: Any) -> StrategyFitnessReport:
    """Convenience wrapper: evaluate a Skill object using its description + steps.

    Builds the strategy text from the skill's description, trigger patterns,
    and step templates, then calls evaluate_strategy.
    """
    parts = [skill.description]
    if hasattr(skill, "trigger_patterns"):
        parts.extend(skill.trigger_patterns[:3])
    if hasattr(skill, "steps_template"):
        parts.extend(skill.steps_template[:4])
    strategy_text = " ".join(str(p) for p in parts)
    return evaluate_strategy(strategy_text)


def evaluate_suggestion(suggestion: Any) -> StrategyFitnessReport:
    """Convenience wrapper: evaluate an Evolver Suggestion object.

    Uses suggestion.suggestion text as the strategy description.
    """
    text = getattr(suggestion, "suggestion", str(suggestion))
    return evaluate_strategy(text)


# ---------------------------------------------------------------------------
# CLI — poe-replay
# ---------------------------------------------------------------------------

def main() -> int:  # noqa: C901
    """CLI entry point: poe-replay.

    Usage:
        poe-replay "research Polymarket trends"
        poe-replay "fix build failures" --compare
        poe-replay --outcome-id ab12cd34
        poe-replay --outcome-id ab12cd34 --compare

    --compare mode runs evaluation twice: once without lessons injected into
    the strategy text, once with lessons prepended. Shows the fitness delta
    so you can quantify lesson injection value.
    """
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="poe-replay",
        description="Replay-based fitness oracle for goals/strategies/outcomes",
    )
    parser.add_argument("goal", nargs="*", help="Goal or strategy text to evaluate")
    parser.add_argument(
        "--outcome-id", "-o",
        help="Look up a past outcome by outcome_id and evaluate its goal",
    )
    parser.add_argument(
        "--compare", "-c",
        action="store_true",
        help="Compare fitness with vs. without lesson injection",
    )
    parser.add_argument(
        "--top", "-n",
        type=int,
        default=5,
        help="Number of similar outcomes to display (default: 5)",
    )
    args = parser.parse_args()

    # Resolve strategy text
    strategy_text = ""
    if args.outcome_id:
        try:
            from memory import load_outcomes
            outcomes_pool = load_outcomes(limit=500)
            match = next(
                (o for o in outcomes_pool if o.outcome_id == args.outcome_id),
                None,
            )
            if match is None:
                print(f"outcome_id {args.outcome_id!r} not found in outcomes.jsonl", file=sys.stderr)
                return 1
            strategy_text = match.goal
            if match.summary:
                strategy_text += " " + match.summary
            print(f"Loaded outcome: [{match.status}] {match.goal!r}")
        except Exception as exc:
            print(f"Failed to load outcome: {exc}", file=sys.stderr)
            return 1
    elif args.goal:
        strategy_text = " ".join(args.goal)
    else:
        parser.print_help()
        return 1

    # Baseline evaluation
    baseline = evaluate_strategy(strategy_text)
    print(f"\n{'='*60}")
    print(f"Strategy: {strategy_text[:80]!r}")
    print(f"{'='*60}")
    print(baseline.summary())
    print(f"\nTop {args.top} similar outcomes:")
    for s in baseline.similar_outcomes[:args.top]:
        print(f"  [{s.status}] sim={s.similarity:.3f} — {s.goal[:60]}")

    if args.compare:
        # Inject lessons and re-evaluate
        try:
            from memory import inject_tiered_lessons
            task_type = "general"
            lessons_text = inject_tiered_lessons(task_type, strategy_text)
        except Exception as exc:
            print(f"\n[compare] failed to load lessons: {exc}", file=sys.stderr)
            return 0

        if not lessons_text:
            print("\n[compare] no lessons available — comparison not possible")
            return 0

        augmented = f"{strategy_text}\n\n{lessons_text}"
        augmented_report = evaluate_strategy(augmented)

        delta = augmented_report.fitness_score - baseline.fitness_score
        delta_str = f"+{delta:.3f}" if delta >= 0 else f"{delta:.3f}"

        print(f"\n{'='*60}")
        print(f"With lessons injected:")
        print(f"{'='*60}")
        print(f"  fitness:  {augmented_report.fitness_score:.3f}  (baseline: {baseline.fitness_score:.3f}, delta: {delta_str})")
        print(f"  verdict:  {augmented_report.verdict}  (baseline: {baseline.verdict})")
        print(f"  confidence: {augmented_report.confidence:.3f}  (baseline: {baseline.confidence:.3f})")
        if delta > 0.05:
            print("  → lessons IMPROVE predicted fitness")
        elif delta < -0.05:
            print("  → lessons DEGRADE predicted fitness (inspect lesson quality)")
        else:
            print("  → no significant fitness change from lesson injection")

    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
