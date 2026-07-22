---
status: record
---

# Triad ablation — seat divergence report

Goal: summarize evidence on multi-family review panels

Question (era 04, verbatim intent): do N seats draw DIFFERENT lines,
or near-identical lists at Nx cost? High overlap = costume; low
overlap = genuine angle diversity. This report measures; the
graduation call stays human.

## Arm: costume (3 calls)

- **devil_advocate** [WEAK] (hosted_free:gemini:gemini-flash-lite-latest)
    - Extrapolates a direct policy recommendation ('adopt immediately') from a single study (Stanford HELM-Review, N=1200) without analyzing cost, latency, or compute overhead.
    - Fails to account for the second model family's capability floor—if the review model is significantly weaker than the primary model, its false-passes or false-rejects will introduce noise rather than r
    - Ignores prompt injection and adversarial vulnerability transfer across distinct model families (e.g., whether common training data or alignment techniques leave shared blind spots).
    - Omits operational complexity, API dependency risks, and licensing/cost barriers of maintaining dual-family infrastructure.
- **domain_skeptic** [WEAK] (hosted_free:gemini:gemini-flash-lite-latest)
    - Citing a synthetic/simulation step (ablation harness) as if it were a completed empirical finding from a named academic study (Stanford HELM-Review 2025).
    - Extrapolating a 40% reduction in 'correlated false-passes' on benchmark tasks directly to real-world 'research output' without accounting for domain-specific context or semantic validity.
    - Treating model family diversity as a silver bullet while ignoring shared pre-training data contamination across ostensibly 'different' model families.
    - Prescribing an immediate operational adoption gate based on a fixed N=1200 task evaluation without cost-benefit or latency trade-off analysis.
- **implementation_critic** [WEAK] (hosted_free:gemini:gemini-flash-lite-latest)
    - Cites a 2025 Stanford HELM-Review study with an exact sample size (N=1200 tasks) which appears to be hallucinated or synthetic, as requested by the prompt's 'synthetic step' label.
    - Lacks actionable configuration specifics: does not state which model families should be paired (e.g., OpenAI with Anthropic) or how tie-breaking/consensus is calculated when families disagree.
    - Provides no implementation steps, prompt templates, or API integration details required to actually build the review gate.

Mean pairwise concern overlap: 0.12 — diverged (genuine angles)
Distinct catches per seat: devil_advocate=4, domain_skeptic=4, implementation_critic=3

## Arm: evidence (3 calls)

> NOTE: transcript_aware ran on a single synthetic step — it degenerates to artifact-only here; pass --steps-json for a real trail

- **transcript_aware** [WEAK] (hosted_free:gemini:gemini-flash-lite-latest)
    - The step result claims the 2025 Stanford HELM-Review study (N=1200 tasks) supports the 40% reduction metric, but this source and data are synthetic/unverified assertions generated within a single harn
    - The final recommendation to 'adopt a two-family review gate immediately' makes an evidence-free leap from a single summarized study's metric to an organizational prescription.
- **artifact_only** [WEAK] (hosted_free:gemini:gemini-flash-lite-latest)
    - Cites a specific study ('2025 Stanford HELM-Review study, N=1200 tasks') without providing a bibliography, link, or methodological details.
    - Makes an absolute policy recommendation ('adopt a two-family review gate immediately') based on a single metric without addressing cost, latency, or operational feasibility.
    - Fails to define what constitutes a 'model family' or how reviewers are weighted/combined in the panel.
- **probe_armed** [WEAK] (hosted_free:gemini:gemini-flash-lite-latest) codes=PHANTOM_SYMBOL
    - FINDING[PHANTOM_SYMBOL] cites a specific 2025 Stanford HELM-Review study (N=1200 tasks) that cannot be verified to exist.

Mean pairwise concern overlap: 0.20 — diverged (genuine angles)
Distinct catches per seat: artifact_only=2, probe_armed=1, transcript_aware=2

