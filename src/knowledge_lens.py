#!/usr/bin/env python3
# @lat: [[memory-system#Pending: Promotion Cycle (Phase 56)]]
"""Knowledge Lens — rules, hypotheses, decisions, and verification tracking.

Extracted from memory.py (lines 1469-2101). This is the "Lens" layer:

1. Standing Rules: observation → hypothesis → standing rule promotion cycle
2. Hypotheses: lessons being tracked toward standing-rule promotion
3. Decision Journal: ADR-style log of significant decisions
4. Verification Outcomes: accumulating verifier memory for calibration (Feynman F4)
5. Calibrated alignment threshold: evidence-based threshold tuning (Phase 60)
"""

from __future__ import annotations

import json
import math
import re
import textwrap
import logging
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from memory_ledger import _memory_dir, _text_similarity

log = logging.getLogger("poe.memory")

# ---------------------------------------------------------------------------
# Phase 56: Standing Rules — promotion cycle top tier
# ---------------------------------------------------------------------------
# Observation → hypothesis (2+ confirmations) → standing rule (applied by default)
# Contradiction demotes back to hypothesis. Rules survive indefinitely (no decay).

_RULES_FILENAME = "standing_rules.jsonl"
_HYPOTHESES_FILENAME = "hypotheses.jsonl"

# Minimum long-tier confirmations before promoting to standing rule
RULE_PROMOTE_CONFIRMATIONS = 2


@dataclass
class StandingRule:
    """A promoted rule applied unconditionally in every planning call."""
    rule_id: str
    rule: str                       # The rule text injected into decompose
    source_lesson_id: str           # Long-tier lesson this was promoted from
    domain: str                     # goal domain / task_type tag
    confirmations: int              # times confirmed in production after promotion
    contradictions: int             # times contradicted (≥1 → demoted back to hypothesis)
    promoted_at: str
    last_applied: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_id": self.rule_id, "rule": self.rule,
            "source_lesson_id": self.source_lesson_id, "domain": self.domain,
            "confirmations": self.confirmations, "contradictions": self.contradictions,
            "promoted_at": self.promoted_at, "last_applied": self.last_applied,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "StandingRule":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


@dataclass
class Hypothesis:
    """A lesson being tracked toward standing-rule promotion."""
    hyp_id: str
    lesson: str
    domain: str
    confirmations: int              # how many sessions have confirmed this pattern
    contradictions: int
    source_lesson_ids: List[str]    # lessons that contributed
    first_seen: str
    last_seen: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "hyp_id": self.hyp_id, "lesson": self.lesson, "domain": self.domain,
            "confirmations": self.confirmations, "contradictions": self.contradictions,
            "source_lesson_ids": self.source_lesson_ids,
            "first_seen": self.first_seen, "last_seen": self.last_seen,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Hypothesis":
        return cls(**{k: d.get(k, v) for k, v in {
            "hyp_id": "", "lesson": "", "domain": "", "confirmations": 0,
            "contradictions": 0, "source_lesson_ids": [], "first_seen": "", "last_seen": "",
        }.items()})


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _rules_path() -> Path:
    return _memory_dir() / _RULES_FILENAME


def _hypotheses_path() -> Path:
    return _memory_dir() / _HYPOTHESES_FILENAME


# ---------------------------------------------------------------------------
# Load / rewrite helpers
# ---------------------------------------------------------------------------

def load_standing_rules(domain: Optional[str] = None) -> List[StandingRule]:
    """Load all standing rules, optionally filtered by domain."""
    try:
        path = _rules_path()
        if not path.exists():
            return []
        rules = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    rules.append(StandingRule.from_dict(json.loads(line)))
                except Exception:
                    pass
        if domain:
            rules = [r for r in rules if r.domain == domain or r.domain == ""]
        return rules
    except Exception:
        return []


def load_hypotheses(domain: Optional[str] = None) -> List[Hypothesis]:
    """Load tracked hypotheses, optionally filtered by domain."""
    try:
        path = _hypotheses_path()
        if not path.exists():
            return []
        hyps = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    hyps.append(Hypothesis.from_dict(json.loads(line)))
                except Exception:
                    pass
        if domain:
            hyps = [h for h in hyps if h.domain == domain or h.domain == ""]
        return hyps
    except Exception:
        return []


def _rewrite_rules(rules: List[StandingRule]) -> None:
    try:
        from file_lock import locked_write
        path = _rules_path()
        with locked_write(path):
            path.write_text(
                "\n".join(json.dumps(r.to_dict()) for r in rules) + ("\n" if rules else ""),
                encoding="utf-8",
            )
    except Exception:
        pass


def _rewrite_hypotheses(hyps: List[Hypothesis]) -> None:
    try:
        from file_lock import locked_write
        path = _hypotheses_path()
        with locked_write(path):
            path.write_text(
                "\n".join(json.dumps(h.to_dict()) for h in hyps) + ("\n" if hyps else ""),
                encoding="utf-8",
            )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Utility: _current_date (local helper matching memory.py)
# ---------------------------------------------------------------------------

def _current_date() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Pattern observation & contradiction
# ---------------------------------------------------------------------------

def observe_pattern(lesson: str, domain: str, *, source_lesson_id: str = "") -> Optional[StandingRule]:
    """Record a confirmed lesson observation. Promotes to StandingRule at threshold.

    Call this when a long-tier lesson is confirmed again in production:
    - First call: creates/increments Hypothesis
    - At RULE_PROMOTE_CONFIRMATIONS: promotes to StandingRule and removes Hypothesis

    Returns the new StandingRule if promotion occurred, else None.
    """
    now = _current_date()
    hyps = load_hypotheses(domain=None)

    # Find existing hypothesis by similarity (exact or near-exact lesson text)
    target_hyp: Optional[Hypothesis] = None
    lesson_lower = lesson.lower().strip()
    for h in hyps:
        if h.lesson.lower().strip() == lesson_lower or h.domain == domain and _text_similarity(h.lesson, lesson) > 0.85:
            target_hyp = h
            break

    if target_hyp is None:
        # New observation — create hypothesis
        import uuid as _uuid
        target_hyp = Hypothesis(
            hyp_id=str(_uuid.uuid4())[:8],
            lesson=lesson,
            domain=domain,
            confirmations=1,
            contradictions=0,
            source_lesson_ids=[source_lesson_id] if source_lesson_id else [],
            first_seen=now,
            last_seen=now,
        )
        hyps.append(target_hyp)
        _rewrite_hypotheses(hyps)
        # Captain's log
        try:
            from captains_log import log_event, HYPOTHESIS_CREATED
            log_event(
                event_type=HYPOTHESIS_CREATED,
                subject=target_hyp.hyp_id,
                summary=f"New hypothesis in {domain}: {lesson[:100]}",
                context={"domain": domain, "hyp_id": target_hyp.hyp_id},
            )
        except Exception:
            pass
        return None

    # Existing hypothesis — confirm
    target_hyp.confirmations += 1
    target_hyp.last_seen = now
    if source_lesson_id and source_lesson_id not in target_hyp.source_lesson_ids:
        target_hyp.source_lesson_ids.append(source_lesson_id)

    if target_hyp.confirmations >= RULE_PROMOTE_CONFIRMATIONS:
        # Check for contradiction with existing rules before promoting
        existing_rules = load_standing_rules()
        contradicting = check_contradiction(target_hyp.lesson, existing_rules)
        if contradicting is not None:
            log.warning(
                "hypothesis %s contradicts standing rule %s — blocking promotion",
                target_hyp.hyp_id, contradicting.rule_id,
            )
            try:
                from captains_log import log_event, HYPOTHESIS_CONTRADICTED
                log_event(
                    event_type=HYPOTHESIS_CONTRADICTED,
                    subject=target_hyp.hyp_id,
                    summary=(
                        f"Blocked promotion: '{target_hyp.lesson[:80]}' contradicts "
                        f"rule {contradicting.rule_id}: '{contradicting.rule[:80]}'"
                    ),
                    context={
                        "hyp_id": target_hyp.hyp_id,
                        "conflicting_rule_id": contradicting.rule_id,
                    },
                )
            except Exception:
                pass
            _rewrite_hypotheses(hyps)
            return None

        # Promote to standing rule
        import uuid as _uuid
        rule = StandingRule(
            rule_id=str(_uuid.uuid4())[:8],
            rule=target_hyp.lesson,
            source_lesson_id=target_hyp.source_lesson_ids[0] if target_hyp.source_lesson_ids else "",
            domain=target_hyp.domain,
            confirmations=target_hyp.confirmations,
            contradictions=0,
            promoted_at=now,
        )
        with open(_rules_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(rule.to_dict()) + "\n")
        # Remove from hypotheses
        hyps = [h for h in hyps if h.hyp_id != target_hyp.hyp_id]
        _rewrite_hypotheses(hyps)
        log.info("standing rule promoted: %s (domain=%s, confirmations=%d)",
                 rule.rule_id, rule.domain, rule.confirmations)
        # Captain's log
        try:
            from captains_log import log_event, HYPOTHESIS_PROMOTED
            log_event(
                event_type=HYPOTHESIS_PROMOTED,
                subject=rule.rule_id,
                summary=f"Hypothesis promoted to standing rule ({rule.confirmations} confirmations): {rule.rule[:100]}",
                context={"domain": rule.domain, "confirmations": rule.confirmations, "rule_id": rule.rule_id},
                related_ids=[f"rule:{rule.rule_id}"],
            )
        except Exception:
            pass
        return rule

    _rewrite_hypotheses(hyps)
    return None


def contradict_pattern(lesson: str, domain: str) -> bool:
    """Record a contradiction — demotes hypothesis or increments rule.contradictions.

    A standing rule with contradictions >= 1 should be flagged for review.
    Returns True if something was found and updated.
    """
    lesson_lower = lesson.lower().strip()

    # Check standing rules first
    rules = load_standing_rules()
    for r in rules:
        if r.rule.lower().strip() == lesson_lower or _text_similarity(r.rule, lesson) > 0.85:
            r.contradictions += 1
            _rewrite_rules(rules)
            log.warning("standing rule contradicted: rule_id=%s contradictions=%d", r.rule_id, r.contradictions)
            try:
                from captains_log import log_event, STANDING_RULE_CONTRADICTED
                log_event(
                    event_type=STANDING_RULE_CONTRADICTED,
                    subject=r.rule_id,
                    summary=f"Contradicted (now {r.contradictions} vs {r.confirmations} confirmations): {r.rule[:100]}",
                    context={"contradictions": r.contradictions, "confirmations": r.confirmations},
                    related_ids=[f"rule:{r.rule_id}"],
                )
            except Exception:
                pass
            return True

    # Check hypotheses
    hyps = load_hypotheses()
    for h in hyps:
        if h.lesson.lower().strip() == lesson_lower or _text_similarity(h.lesson, lesson) > 0.85:
            h.contradictions += 1
            _demoted = h.contradictions > h.confirmations
            if _demoted:
                # Demote — remove hypothesis
                hyps = [x for x in hyps if x.hyp_id != h.hyp_id]
                log.info("hypothesis demoted (contradictions > confirmations): %s", h.hyp_id)
            _rewrite_hypotheses(hyps)
            try:
                from captains_log import log_event, HYPOTHESIS_CONTRADICTED
                _action = "Demoted and removed" if _demoted else "Contradicted"
                log_event(
                    event_type=HYPOTHESIS_CONTRADICTED,
                    subject=h.hyp_id,
                    summary=f"{_action} ({h.contradictions} contradictions vs {h.confirmations} confirmations): {h.lesson[:100]}",
                    context={"contradictions": h.contradictions, "confirmations": h.confirmations},
                )
            except Exception:
                pass
            return True

    return False


# Negation / opposition keywords that signal potential contradiction
_NEGATION_PAIRS = [
    ({"always", "must", "require"}, {"never", "skip", "avoid", "don't", "do not"}),
    ({"verify", "validate", "check"}, {"skip", "omit", "bypass"}),
    ({"include", "add", "use"}, {"exclude", "remove", "avoid", "don't use"}),
    ({"fast", "quick", "speed"}, {"thorough", "comprehensive", "slow"}),
    ({"simple", "minimal"}, {"detailed", "extensive", "comprehensive"}),
]


def check_contradiction(
    candidate: str,
    existing_rules: List[StandingRule],
    *,
    similarity_threshold: float = 0.5,
) -> Optional[StandingRule]:
    """Check if a candidate rule contradicts any existing standing rule.

    Uses text similarity + negation keyword detection. Returns the first
    contradicting rule found, or None if no contradiction detected.

    A contradiction is: high topic similarity (rules about the same thing)
    combined with opposing directives (one says "always X", the other "never X").
    """
    candidate_lower = candidate.lower().strip()
    candidate_words = set(candidate_lower.split())

    for rule in existing_rules:
        rule_lower = rule.rule.lower().strip()
        rule_words = set(rule_lower.split())

        # Topic similarity: are these rules about the same thing?
        topic_sim = _text_similarity(candidate, rule.rule)
        if topic_sim < similarity_threshold:
            continue

        # Check for negation opposition
        for group_a, group_b in _NEGATION_PAIRS:
            cand_has_a = bool(candidate_words & group_a)
            cand_has_b = bool(candidate_words & group_b)
            rule_has_a = bool(rule_words & group_a)
            rule_has_b = bool(rule_words & group_b)

            # Contradiction: one has A-words and the other has B-words (or vice versa)
            if (cand_has_a and rule_has_b) or (cand_has_b and rule_has_a):
                return rule

    return None


def inject_standing_rules(domain: str = "") -> str:
    """Return standing rules formatted for injection into decompose system prompt.

    Returns empty string if no rules exist (safe to always call).
    """
    rules = load_standing_rules(domain=domain)
    if not rules:
        return ""
    lines = ["### Standing Rules (apply unconditionally)"]
    for r in rules:
        lines.append(f"- {r.rule}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Phase 56: Decision Journal
# ---------------------------------------------------------------------------
# ADR-style log of significant decisions. Searched before new decisions.
# Format: what was decided, alternatives considered, why this won, trade-offs.

_DECISIONS_FILENAME = "decisions.jsonl"

DECISION_SEARCH_LIMIT = 3  # max decisions to inject into context


@dataclass
class Decision:
    """A recorded architectural or strategic decision."""
    decision_id: str
    domain: str                     # goal domain / subsystem tag
    decision: str                   # what was decided
    alternatives: List[str]         # what else was considered
    rationale: str                  # why this won
    trade_offs: str                 # known downsides
    recorded_at: str
    goal_context: str = ""          # the goal that prompted this decision

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision_id": self.decision_id, "domain": self.domain,
            "decision": self.decision, "alternatives": self.alternatives,
            "rationale": self.rationale, "trade_offs": self.trade_offs,
            "recorded_at": self.recorded_at, "goal_context": self.goal_context,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Decision":
        return cls(**{k: d.get(k, v) for k, v in {
            "decision_id": "", "domain": "", "decision": "", "alternatives": [],
            "rationale": "", "trade_offs": "", "recorded_at": "", "goal_context": "",
        }.items()})


def _decisions_path() -> Path:
    return _memory_dir() / _DECISIONS_FILENAME


def record_decision(
    decision: str,
    rationale: str,
    *,
    domain: str = "",
    alternatives: Optional[List[str]] = None,
    trade_offs: str = "",
    goal_context: str = "",
) -> Decision:
    """Record a significant decision to the decision journal.

    Args:
        decision: What was decided (one sentence).
        rationale: Why this was chosen over alternatives.
        domain: Subsystem/domain tag for filtering (e.g. "memory", "routing").
        alternatives: Other options that were considered.
        trade_offs: Known downsides or limitations of the chosen approach.
        goal_context: The goal that prompted this decision.

    Returns:
        The recorded Decision object.
    """
    import uuid as _uuid
    d = Decision(
        decision_id=str(_uuid.uuid4())[:8],
        domain=domain,
        decision=decision,
        alternatives=alternatives or [],
        rationale=rationale,
        trade_offs=trade_offs,
        recorded_at=datetime.now(timezone.utc).isoformat(),
        goal_context=goal_context,
    )
    try:
        with open(_decisions_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(d.to_dict()) + "\n")
    except Exception as exc:
        log.warning("decision journal write failed: %s", exc)

    # Captain's log
    try:
        from captains_log import log_event, DECISION_RECORDED
        log_event(
            event_type=DECISION_RECORDED,
            subject=d.decision_id,
            summary=f"Decision ({domain or 'general'}): {decision[:100]}",
            context={"domain": domain, "alternatives": len(alternatives or [])},
        )
    except Exception:
        pass

    return d


# ---------------------------------------------------------------------------
# TF-IDF ranking (local, avoids circular imports)
# ---------------------------------------------------------------------------

_STOP_WORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "is", "was", "are", "were", "be", "been", "being", "it",
    "its", "this", "that", "these", "those", "i", "we", "you", "he", "she",
    "they", "what", "when", "where", "who", "which", "how", "if", "as", "by",
    "from", "not", "can", "will", "do", "did", "does", "have", "had", "has",
    "should", "would", "could", "may", "might", "step", "goal", "task",
})


def _tokenize(text: str) -> List[str]:
    """Lowercase + split on non-alphanumeric, filter stop words + short tokens."""
    return [
        t for t in re.sub(r"[^a-z0-9]+", " ", text.lower()).split()
        if t not in _STOP_WORDS and len(t) > 2
    ]


def _tfidf_rank(query: str, items: list, *, top_k: Optional[int] = None) -> list:
    """Rank items by TF-IDF cosine similarity to query.

    Each item must have a ``.lesson`` attribute containing the text to compare.
    Pure stdlib — no sklearn, no numpy.
    """
    if not items:
        return []

    query_terms = _tokenize(query)
    if not query_terms:
        return items

    docs: List[List[str]] = [query_terms]
    for item in items:
        docs.append(_tokenize(item.lesson))

    n_docs = len(docs)

    df: Counter = Counter()
    for doc in docs:
        for term in set(doc):
            df[term] += 1

    def idf(term: str) -> float:
        return math.log(n_docs / (df.get(term, 0) + 1)) + 1.0

    def tfidf_vec(doc_terms: List[str]) -> Dict[str, float]:
        tf = Counter(doc_terms)
        total = max(len(doc_terms), 1)
        return {t: (c / total) * idf(t) for t, c in tf.items()}

    def cosine(v1: Dict[str, float], v2: Dict[str, float]) -> float:
        dot = sum(v1.get(t, 0.0) * v2.get(t, 0.0) for t in v1)
        norm1 = math.sqrt(sum(x * x for x in v1.values())) or 1.0
        norm2 = math.sqrt(sum(x * x for x in v2.values())) or 1.0
        return dot / (norm1 * norm2)

    query_vec = tfidf_vec(query_terms)
    scores: List[tuple] = []
    for item, doc_terms in zip(items, docs[1:]):
        doc_vec = tfidf_vec(doc_terms)
        sim = cosine(query_vec, doc_vec)
        scores.append((sim, item))

    scores.sort(key=lambda x: x[0], reverse=True)
    ranked = [item for _, item in scores]
    return ranked[:top_k] if top_k is not None else ranked


# ---------------------------------------------------------------------------
# Decision search & injection
# ---------------------------------------------------------------------------

def search_decisions(query: str, domain: str = "", limit: int = DECISION_SEARCH_LIMIT) -> List[Decision]:
    """Search the decision journal for relevant prior decisions.

    Uses TF-IDF similarity against decision + rationale text.
    Returns top-K matches, newest first on ties.
    """
    try:
        path = _decisions_path()
        if not path.exists():
            return []
        all_decisions = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    all_decisions.append(Decision.from_dict(json.loads(line)))
                except Exception:
                    pass
        if domain:
            all_decisions = [d for d in all_decisions if d.domain == domain or d.domain == ""]

        if not all_decisions:
            return []

        # Rank by similarity to query
        class _FakeTL:
            """Adapter so _tfidf_rank can score decisions."""
            def __init__(self, d: Decision):
                self.lesson = f"{d.decision} {d.rationale}"
                self._d = d

        scored = _tfidf_rank(query, [_FakeTL(d) for d in all_decisions], top_k=limit)
        return [s._d for s in scored]
    except Exception:
        return []


def inject_decisions(goal: str, domain: str = "") -> str:
    """Return relevant prior decisions formatted for injection into decompose prompt.

    Returns empty string if no relevant decisions (safe to always call).
    """
    decisions = search_decisions(goal, domain=domain)
    if not decisions:
        return ""
    lines = ["### Prior Decisions (search before making new ones)"]
    for d in decisions:
        alts = f" Alternatives considered: {', '.join(d.alternatives)}." if d.alternatives else ""
        lines.append(f"- **{d.decision}** — {d.rationale}{alts}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Feynman F4: Accumulating verifier memory
# ---------------------------------------------------------------------------

@dataclass
class VerificationOutcome:
    """Record of a claim verification attempt — builds over time for calibration.

    Phase 59 Feynman F4: tracking verifier outputs enables post-hoc accuracy
    analysis and per-claim-type confidence threshold calibration. Each time
    the inspector or adversarial lens assesses a claim, we record it.
    """
    verification_id: str
    claim_type: str   # "quality" | "alignment" | "step_correctness" | "adversarial" | ...
    verdict: str      # "pass" | "fail" | "uncertain"
    source: str       # "llm" | "heuristic" | "lesson" | "standing_rule"
    confidence: float  # 0.0–1.0
    goal: str = ""
    outcome_id: str = ""
    recorded_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    notes: str = ""   # optional free-form context


def _verification_outcomes_path() -> Path:
    return _memory_dir() / "verification_outcomes.jsonl"


def record_verification(
    claim_type: str,
    verdict: str,
    source: str,
    confidence: float,
    *,
    goal: str = "",
    outcome_id: str = "",
    notes: str = "",
) -> VerificationOutcome:
    """Record a verification outcome to disk.

    Phase 59 Feynman F4: builds the accumulating verifier memory.
    Called by inspector.check_alignment(), adversarial lens, and any
    other verification path. Over time, enables per-claim-type calibration.

    Args:
        claim_type:  Category of claim verified ("quality" | "alignment" | ...).
        verdict:     Verifier's verdict ("pass" | "fail" | "uncertain").
        source:      What answered the query ("llm" | "heuristic" | "lesson" | "standing_rule").
        confidence:  Verifier's confidence (0.0–1.0).
        goal:        Original goal text (for traceability).
        outcome_id:  Outcome/loop_id if available.
        notes:       Free-form context.
    """
    import uuid
    vo = VerificationOutcome(
        verification_id=str(uuid.uuid4())[:8],
        claim_type=claim_type,
        verdict=verdict,
        source=source,
        confidence=confidence,
        goal=goal[:200] if goal else "",
        outcome_id=outcome_id,
        notes=notes[:200] if notes else "",
    )
    path = _verification_outcomes_path()
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(vo)) + "\n")
    return vo


def load_verification_outcomes(
    claim_type: Optional[str] = None,
    *,
    limit: int = 100,
    source: Optional[str] = None,
) -> List[VerificationOutcome]:
    """Load verifier history from disk.

    Phase 59 Feynman F4: enables analysis of verifier accuracy trends.

    Args:
        claim_type: If set, only return outcomes for this claim type.
        limit:      Maximum number of records to return (newest first).
        source:     If set, only return outcomes from this source tier.
    """
    path = _verification_outcomes_path()
    if not path.exists():
        return []

    results: List[VerificationOutcome] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                vo = VerificationOutcome(**{
                    k: d[k] for k in VerificationOutcome.__dataclass_fields__ if k in d
                })
                if claim_type and vo.claim_type != claim_type:
                    continue
                if source and vo.source != source:
                    continue
                results.append(vo)
            except Exception:
                continue
    except Exception:
        pass

    results.reverse()  # newest first
    return results[:limit]


def verification_accuracy(claim_type: Optional[str] = None) -> Dict[str, float]:
    """Compute per-verdict distribution for verifier calibration.

    Returns dict: {"pass_rate": float, "fail_rate": float, "uncertain_rate": float,
                   "total": int, "avg_confidence": float}
    """
    outcomes = load_verification_outcomes(claim_type=claim_type, limit=500)
    if not outcomes:
        return {"pass_rate": 0.0, "fail_rate": 0.0, "uncertain_rate": 0.0,
                "total": 0, "avg_confidence": 0.0}
    total = len(outcomes)
    passes = sum(1 for o in outcomes if o.verdict == "pass")
    fails = sum(1 for o in outcomes if o.verdict == "fail")
    uncertain = sum(1 for o in outcomes if o.verdict == "uncertain")
    avg_conf = sum(o.confidence for o in outcomes) / total
    return {
        "pass_rate": passes / total,
        "fail_rate": fails / total,
        "uncertain_rate": uncertain / total,
        "total": total,
        "avg_confidence": avg_conf,
    }


# ---------------------------------------------------------------------------
# Phase 60: Verification calibration loop
# ---------------------------------------------------------------------------

_ALIGNMENT_THRESHOLD_BASE = 0.60
_ALIGNMENT_THRESHOLD_MIN = 0.45
_ALIGNMENT_THRESHOLD_MAX = 0.75
_CALIBRATION_MIN_SAMPLES = 10  # don't adjust until we have enough data


def calibrated_alignment_threshold(claim_type: str = "alignment") -> float:
    """Return an evidence-based alignment threshold derived from verifier history.

    Phase 60: Uses ``verification_accuracy()`` stats to auto-tune the pass/fail
    threshold that ``check_alignment()`` uses to label a session as aligned.

    Logic (requires >= _CALIBRATION_MIN_SAMPLES outcomes):
    - If verifier avg_confidence is low AND uncertain_rate is high -> lower threshold
      (the verifier is being overly conservative; we're probably rejecting good work).
    - If fail_rate is high AND avg_confidence is high -> raise threshold
      (the verifier is confidently saying many things fail; raise bar for "pass").
    - Otherwise -> return _ALIGNMENT_THRESHOLD_BASE (0.60).

    Returns a float in [_ALIGNMENT_THRESHOLD_MIN, _ALIGNMENT_THRESHOLD_MAX].
    """
    stats = verification_accuracy(claim_type=claim_type)
    if stats["total"] < _CALIBRATION_MIN_SAMPLES:
        return _ALIGNMENT_THRESHOLD_BASE

    avg_conf: float = stats["avg_confidence"]
    fail_rate: float = stats["fail_rate"]
    uncertain_rate: float = stats["uncertain_rate"]

    threshold = _ALIGNMENT_THRESHOLD_BASE

    # Conservative verifier: lots of uncertainty + low confidence -> relax threshold
    if avg_conf < 0.55 and uncertain_rate > 0.30:
        threshold = _ALIGNMENT_THRESHOLD_BASE - 0.10

    # Strict verifier: high confidence + high fail rate -> tighten threshold
    elif avg_conf > 0.70 and fail_rate > 0.40:
        threshold = _ALIGNMENT_THRESHOLD_BASE + 0.10

    return max(_ALIGNMENT_THRESHOLD_MIN, min(_ALIGNMENT_THRESHOLD_MAX, threshold))
