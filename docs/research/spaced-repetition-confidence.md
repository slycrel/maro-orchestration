# Spaced Repetition × Confidence Signals: Cognitive Science Findings
*Research date: 2026-03-27 | Author: Poe (autonomous research agent)*

---

## 1. Key Findings

### 1.1 The Core Tension: Confidence ≠ Mastery
- **High confidence at low mastery is the dominant failure mode** (Dunning-Kruger effect). Learners who feel confident often over-estimate retention, leading to under-review and rapid forgetting.
- **The testing effect** shows that retrieval *difficulty* predicts long-term retention better than subjective ease. Hard-won correct answers produce stronger memory traces than fluent ones.
- **Calibration improves with domain expertise**: expert learners have confidence scores that meaningfully predict actual retention; novices do not. This asymmetry is critical for memory system design.

### 1.2 Confidence as a Noisy Signal
- Confidence self-reports correlate with retention *on average* but have high per-item variance (~0.3–0.5 correlation in controlled studies).
- **Fluency illusion**: fast, effortless retrieval feels like mastery but may reflect surface familiarity rather than durable encoding. Confidence from fluency should be down-weighted.
- **Desirable difficulties** (spacing, interleaving) reduce confidence while increasing long-term retention — the *opposite* of naive confidence-following behavior.

### 1.3 Optimal Review Timing by Confidence State

| Learner State | Retrieval Success | Recommended Interval Multiplier |
|---|---|---|
| High confidence + correct | Full strength | 1.2–2.5× standard (extend, but verify) |
| High confidence + incorrect | Overconfidence detected | 0.3–0.5× (aggressive shortening + flag) |
| Low confidence + correct | Productive struggle | 0.8–1.2× (modest extension; note difficulty) |
| Low confidence + incorrect | Active learning zone | 0.4–0.7× (shorten; increase retrieval practice) |
| Medium confidence + correct | Calibrated learner | 1.0× (follow base algorithm) |

**Key insight**: the *mismatch* between confidence and outcome is the most diagnostic signal. Correct with low confidence = memory is consolidating well. Incorrect with high confidence = algorithmic emergency.

---

## 2. Algorithm Comparison

### 2.1 SM-2 (SuperMemo 2)
- **Mechanism**: Ease Factor (EF) adjusted ±0.1–0.2 per review; interval = previous × EF.
- **Confidence handling**: Grade 0–5 maps to quality; grades ≥4 extend, <3 reset to day 1.
- **Weakness**: EF floor at 1.3 prevents full collapse; doesn't distinguish confidence from accuracy; no explicit retrievability model.
- **Confidence signal**: implicit (grade encodes both correctness and subjective ease).

### 2.2 FSRS (Free Spaced Repetition Scheduler)
- **Mechanism**: Three-factor model — Stability (S), Difficulty (D), Retrievability (R = e^(-t/S)).
- **Confidence handling**: Ratings (Again/Hard/Good/Easy) modulate S multiplicatively; Hard rating reduces stability even on correct answers.
- **Key advantage**: Separates *how well you know it* (S) from *how hard it is* (D) from *current recall probability* (R). Confidence-like signals map directly to S adjustments.
- **Target retrievability**: Default 90%; can be tuned per-use-case.
- **Weakness**: Requires substantial review history to calibrate D accurately.

### 2.3 Anki (SM-2 variant)
- Adds "Again/Hard/Good/Easy" buttons; Hard gives 1.2× interval with −0.15 EF penalty even on correct recall.
- Closest mainstream system to confidence-weighted spacing.

### 2.4 Duolingo (ML-based)
- Uses Half-Life Regression: models per-word forgetting rate from implicit behavioral signals (response time, error rate).
- Response time is a proxy for confidence — slower correct answers treated as harder items.
- **Lesson**: implicit behavioral signals (latency) may be more reliable than explicit confidence self-reports.

### 2.5 Summary Comparison

| Algorithm | Explicit Confidence | Retrievability Model | Novice Calibration | Production-Ready |
|---|---|---|---|---|
| SM-2 | No | No | Poor | Yes |
| FSRS | Partial | Yes | Moderate | Yes |
| Duolingo HLR | Implicit | Yes | Good | Yes |
| Ideal system | Yes + validated | Yes | Adaptive | Target |

---

## 3. Confidence-Signal Implications

### 3.1 When to Trust Confidence
- **Trust high confidence** when: learner has multiple prior correct retrievals on this item, spacing intervals have been respected, domain expertise is established.
- **Distrust high confidence** when: item was recently introduced (<3 reviews), learner is a novice in domain, last review was very recent (recency halo).

### 3.2 Confidence Calibration Score
Track per-item calibration history: `calibration = mean(confidence_predicted_accuracy - actual_accuracy)`. A learner with calibration near 0 should have their confidence scores weighted more heavily in interval calculations.

### 3.3 Mismatch Detection Rules
```
if confidence_high AND outcome_incorrect:
    decay_multiplier = 0.35          # aggressive reset
    flag_item = "overconfidence"
    increase_retrieval_frequency = True

if confidence_low AND outcome_correct:
    decay_multiplier = 1.0            # no extension; productive difficulty
    flag_item = "underconfidence"
    note_for_calibration = True

if confidence_high AND outcome_correct:
    decay_multiplier = min(2.0, base * 1.3)   # extend but cap

if confidence_low AND outcome_incorrect:
    decay_multiplier = 0.5            # shorten interval
    increase_retrieval_frequency = True
```

### 3.4 Fluency Penalty
If retrieval latency is available: items retrieved in <1 second should receive a 0.8× confidence weight (fluency likely reflects priming or surface recall, not durable encoding).

---

## 4. Recommended Decay Model Adjustments for Poe

### 4.1 Current Model Gaps (Inferred)
Poe's memory decay model likely uses recency + retrieval count as primary signals. The gap is: **no confidence weighting, no mismatch detection, no calibration tracking**.

### 4.2 Proposed Composite Decay Formula

```
effective_age = recency_score × retrieval_strength_factor × confidence_modifier

confidence_modifier:
  base = 1.0
  if has_confidence_signal:
    base = 0.7 + (0.6 × calibrated_confidence)   # maps [0,1] confidence → [0.7, 1.3]
  if mismatch_detected:
    base = base × mismatch_penalty                # 0.3–0.5 for overconfidence failures

decay_rate = λ_base × (1 / retrieval_strength) × confidence_modifier
memory_weight = e^(-decay_rate × effective_age)
```

### 4.3 Specific Recommendations

**Immediate (low-effort, high-value):**
1. **Add mismatch detection**: track confidence-vs-outcome per memory item. Overconfidence failures should trigger immediate re-review scheduling (treat as decay multiplied by 3×).
2. **Treat high-confidence items with longer intervals only after 3+ correct retrievals**: prevent the fluency illusion from locking in under-reviewed memories.

**Medium-term:**
3. **Implement per-item calibration score**: track `mean(confidence - accuracy)` over retrieval history. Use this to weight how much to trust confidence signals for that item.
4. **Use FSRS-style Stability/Difficulty separation**: decouple "how stable is this memory" from "how inherently hard is this concept." High-difficulty items should have compressed intervals even when retrieval succeeds.

**Longer-term:**
5. **Behavioral proxies for confidence**: if Poe generates responses, response latency and hedging language ("I think", "probably") can serve as implicit confidence proxies, reducing reliance on explicit ratings.
6. **Adaptive retrievability target**: default to 85–90% retrievability threshold but lower to 70% for items with high overconfidence history (force more retrieval practice on dangerous items).

### 4.4 Priority Implementation Order

```
Priority 1: Mismatch detection + aggressive decay on overconfidence failures
Priority 2: Minimum retrieval count gate before trusting high-confidence extensions
Priority 3: Calibration score tracking per memory item
Priority 4: FSRS-style Stability/Difficulty decomposition
Priority 5: Behavioral confidence proxies (latency, hedging)
```

---

## 5. References & Basis

- **SM-2 algorithm**: Wozniak (1987) SuperMemo documentation; EF mechanics well-established.
- **FSRS**: Jarrett Ye et al. (2022–2024); open-source, validated against large Anki datasets.
- **Testing effect**: Roediger & Karpicke (2006) *Psychological Science*; retrieval difficulty predicts retention.
- **Dunning-Kruger**: Kruger & Dunning (1999) *Journal of Personality and Social Psychology*.
- **Metacognition calibration**: Koriat (2007); Bjork et al. (2013) on desirable difficulties.
- **Fluency illusion**: Jacoby & Dallas (1981); Alter & Oppenheimer (2009) on processing fluency and judgments.
- **Duolingo HLR**: Settles & Meeder (2016) *ACL*; half-life regression for vocabulary learning.

---

*Generated by Poe research agent. For Poe memory system integration, see Priority Implementation Order above.*
