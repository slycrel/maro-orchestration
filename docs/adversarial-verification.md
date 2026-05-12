# Adversarial Verification Brief — Productive Persistence & Zoom-Metacognition Claims

> Research brief following `docs/research-brief-template.md`

**Generated:** 2026-05-12  
**Pipeline:** adversarial-verification-for-each-key (8 steps, current session)  
**Scope:** 58 claims across 7 categories — AUDIT(6), CODE(14), THEORY(17), IMPL(9), DISSENT(5), ADVERSARIAL(4), RISK(3)  
**Rating scale:** strong / moderate / weak / contested  
**Source artifacts:** `projects/adversarial-verification-for-each-key/artifacts/`

---

## 0) Question

- **Primary question:** Do the key claims in the productive-persistence and zoom-metacognition research docs survive adversarial scrutiny — correct attribution, accurate implementation status, valid domain-transfer assumptions, and no factually false gap claims?
- **Why we care:** These docs drive Phase 62–66 design decisions. Stale or misattributed claims cause real work to be skipped, wrong priorities to be set, and theoretically-grounded features to be built on invalid foundations.
- **Success criteria:** Each claim rated strong/moderate/weak/contested; flags issued at 4 severity tiers; concrete correction queue produced.

---

## 1) Constraints

- Time budget: Single session, 8 steps
- Required stance: Skeptic — adversarial by design
- Must-use sources: codebase (grep + direct read), research docs, academic literature cited in source docs
- Evidence standard: Code citations verified at 2026-05-12 line numbers

---

## 2) Research Plan (Angles)

1. **Direct refutation search** — grep for symbols claimed absent; check if "not yet implemented" functions exist
2. **Attribution accuracy** — do cited authors (Duckworth, Kapur, Seligman, Boyd, UCB/Gittins) actually support the framing used?
3. **LLM domain-transfer validity** — do human psych/neuro/military findings transfer to LLM inference-time operation?
4. **Methodology weaknesses** — does the audit's string-match approach miss semantic equivalents, re-exports, underscore-prefixed privates?
5. **Stale implementation notes** — do "not yet wired" claims match current codebase state?
6. **External counter-evidence** — do published papers (Kadavath 2022, Guo 2017, Xiong 2023) contradict claims the docs treat as settled?
7. **Internal dissent** — do source docs themselves contain contradictions that invalidate derived claims?

---

## 3) Sources & Provenance

- `src/agent_loop.py` — direct grep; verified line numbers: `:2701` (`_error_fingerprint`), `:3344` (`_is_converging`), `:241` (`step_retries`), `:267` (`director_replan_count`)
- `src/evolver.py` — `:521` (`revert_suggestion`), `:1963` (`_verify_post_apply`)
- `src/scope.py` — `:623` (`inject_scope_into_context`)
- `src/knowledge_lens.py` — `:802` (`calibrated_alignment_threshold`)
- `user/GOALS.md` — confirmed present; refutes `docs/md-claims-audit.md:49`
- `docs/research-brief-findings-and-design.md` — source of IMPL-003/IMPL-004 stale notes
- `docs/md-claims-audit.md` — source of CODE-012 false-negative
- `docs/research/productive_persistence.md` — primary source for THEORY claims 1–17
- `docs/research/zoom-metacognition-adaptive-expertise.md` — THEORY-006, DISSENT claims
- Kadavath et al. (2022): LLM verbalized confidence systematically overconfident
- Xiong et al. (2023): LLM confidence inconsistent, does not reliably track factual accuracy
- Guo et al. (2017): Modern neural networks miscalibrated (output confidence ≠ empirical probability)

---

## 4) Executive Summary

58 claims evaluated. **41 confirmed** (strong or moderate), **4 directly refuted**, **13 contested**, **4 weak**. Three structural issues dominate:

**1. Cluster of stale "not wired" notes (CRITICAL).** Four claims in `research-brief-findings-and-design.md` say Phase 62 features are absent or unwired. They are not. `_error_fingerprint()` ships at `agent_loop.py:2701`; `_is_converging()` is called at `agent_loop.py:3344`. Design decisions being made on these gap claims are being made on false premises.

**2. Wave 3 confidence-gap triggers must be blocked (HIGH).** The confidence-accuracy decoupling signal (THEORY-009) depends on LLM-reported confidence. External literature (Kadavath 2022, Guo 2017, Xiong 2023) establishes LLM confidence scores are systematically miscalibrated. Building an Inspector trigger on them fires on noise, not signal. This trigger must not be implemented without a calibration audit.

**3. Domain-transfer gap pervades all 17 THEORY claims (cross-cutting).** Every theoretical pillar — Duckworth, Kapur, Seligman, UCB/Gittins, Boyd OODA, Hatano, Fleming, Cleeremans — originates outside LLM-agent orchestration and has no empirical validation in this domain. Source docs acknowledge this in §Gap 5 of zoom-metacognition-adaptive-expertise.md; the warning must be propagated as a standing header disclaimer on `productive_persistence.md`.

The DISSENT category is the cleanest: all 5 claims confirmed strong with direct textual evidence. The AUDIT category is directionally correct but the 85% grounding rate is methodology-dependent and should be treated as a rough lower bound.

---

## 5) Key Findings

### Rating distribution

| Rating | Count | % |
|--------|-------|---|
| **strong** | 7 | 12% |
| **moderate** | 34 | 59% |
| **weak** | 4 | 7% |
| **contested** | 13 | 22% |

### By category

| Category | Claims | Strong | Moderate | Weak | Contested |
|----------|--------|--------|----------|------|-----------|
| AUDIT | 6 | 0 | 6 | 0 | 0 |
| CODE | 14 | 0 | 11 | 0 | 3 |
| THEORY | 17 | 0 | 7 | 4 | 6 |
| IMPL | 9 | 2 | 5 | 0 | 2 |
| DISSENT | 5 | 5 | 0 | 0 | 0 |
| ADVERSARIAL | 4 | 0 | 2 | 0 | 2 |
| RISK | 3 | 0 | 3 | 0 | 0 |

### Strongest findings (all DISSENT strong)

- **DISSENT-001 through DISSENT-005:** Internal doc contradictions correctly identified. Retry-N inconsistency code-confirmed (N=2 and N=3 coexist at `agent_loop.py:3156`, `graduation.py:104`, `llm.py:194`, `factory_thin.py:159`). 60–85% desirable-difficulty zone correctly flagged as human-only. Near-miss scope disagreement between older/newer docs confirmed.
- **IMPL-001, IMPL-002 (strong):** `step_retries` at `agent_loop.py:241` and `director_replan_count` at `agent_loop.py:267` confirmed implemented and correctly documented.

---

## 6) Counterpoints / Dissent — Flagged Claims

### CRITICAL — Direct Refutations (4 claims that are factually false)

**CODE-012** | `contested` | Source: `docs/md-claims-audit.md:49`
- Claim: "user/GOALS.md does not exist"
- Refutation: `user/GOALS.md` IS present. Confirmed by file search and citation in `docs/BITTER_LESSON_ANALYSIS.md`. The audit document contains a false negative.
- Action: Correct `docs/md-claims-audit.md:49`; remove GOALS.md from missing-file list; re-audit 85% grounding rate.

**IMPL-003** | `contested` | Source: `docs/research-brief-findings-and-design.md:94`
- Claim: "Error-signature hash not yet present in codebase"
- Refutation: `_error_fingerprint()` at `agent_loop.py:2701` implements the hash. This gap does not exist.
- Action: Correct §94; mark as SHIPPED.

**IMPL-004** | `contested` | Source: `docs/research-brief-findings-and-design.md:95`
- Claim: "`is_converging` implemented (Phase 62) but not wired into agent_loop.py core retry path"
- Refutation: `_is_converging()` IS called at `agent_loop.py:3344`. The "not wired" half is false.
- Action: Correct §95; remove "not wired" language.

**ADVERSARIAL-001** | `contested` — refutation confirmed correct
- The prior claim (CLAIM-13 from a prior session: "_is_converging is design-intent only") was correctly refuted. `_is_converging()` at `agent_loop.py:3344` confirms Phase 62 shipped it. No code change needed; source docs saying "_is_converging is design-only" must be updated.

---

### HIGH — Stale Implementation Notes (4 claims)

**THEORY-002** | `contested` | Source: `docs/research-brief-findings-and-design.md:64`
- Claim: "Currently implemented only as count (stuck_streak); error-signature hash not yet wired"
- Stale: `_error_fingerprint()` at `agent_loop.py:2701` is wired. Three signals now exist: stuck_streak + _error_fingerprint + _is_converging.
- Action: Update note accordingly.

**THEORY-007** | `contested` | Source: `docs/research-brief-findings-and-design.md:71`
- Claim: "Implementation status: partial (stuck_streak only)"
- Stale: Both `_is_converging()` (`:3344`) and `_error_fingerprint()` (`:2701`) are present. Status is more than "partial."
- Action: Update to "Implemented via stuck_streak + _error_fingerprint + _is_converging."

**THEORY-011** | `contested` | Source: `docs/research-brief-findings-and-design.md:76`
- Claim: "is_converging (Phase 62) — not wired into agent_loop.py"
- Direct refutation: Called at `agent_loop.py:3344`.
- Action: Change status to "IMPLEMENTED — _is_converging() at agent_loop.py:3344."

**THEORY-009** | `contested` | **BLOCK required**
- Claim: "Confidence-accuracy decoupling signal (Fleming & Dolan 2012): high pre-step confidence + poor outcome triggers zoom-out"
- External counter-evidence (strong): Kadavath et al. (2022), Xiong et al. (2023), Guo et al. (2017) all confirm LLMs are systematically overconfident and miscalibrated. LLM-reported confidence scores do not reliably track factual accuracy. A confidence-gap Inspector trigger built on these scores fires on noise.
- Action: BLOCK Wave 3 confidence-gap triggers until LLM calibration baseline established. Add caveat to THEORY-009 in source docs.

---

### MEDIUM — Contested Evidence (5 claims)

**CODE-004** | `contested`
- Claim: `calibrated_alignment_threshold` defined in `memory.py`
- Counter: Defined at `knowledge_lens.py:802`; `memory.py` re-exports it; `inspector.py` imports from `memory`. "Defined in" vs "importable via" — interpretation-dependent.
- Action: Clarify doc wording. If doc says "defined in memory.py" → wrong. If "importable via memory.py" → correct.

**CODE-005** | `contested`
- Claim: `inject_scope_into_plan` absent from src/
- Counter: `inject_scope_into_context` at `scope.py:623` may be semantic equivalent under different name.
- Action: Compare `scope.py:623` to Phase 65 plan requirements; determine if gap is real or naming artifact.

**THEORY-001** | `contested`
- Claim: "Productive persistence = approach variation anchored by narrowing hypothesis space (Duckworth 2016, Kapur 2016)"
- Counter: This is an editorial synthesis. Duckworth's grit measures sustained interest over years (not strategy variation). Kapur's productive failure requires a mandatory consolidation phase. Neither directly supports "narrowing hypothesis space."
- Action: Tag `[DESIGN SYNTHESIS]`; remove direct Duckworth/Kapur attribution for this specific framing.

**THEORY-004** | `contested`
- Claim: "Tiered retry structure grounded in UCB/Gittins opportunity-cost framing"
- Counter: Zero Gittins/UCB computation in `src/`. UCB assumes stationary rewards and independent arms — both violated. Wang/Duan meta-RL supports *adaptive* thresholds, not fixed constants — the citation inverts its own finding. Thresholds are engineering constants.
- Action: Remove UCB/Gittins citation; reframe as "engineering heuristic."

**ADVERSARIAL-004** | `contested`
- Claim: "Stale context injection at agent_loop.py:686 on re-decompose is a FLAGGED RISK"
- Design ambiguity: Passing prior injected context into re-decompose may be intentional continuity (benefit) or stale-assumption bug (risk). Design intent not documented; no test for stale-context isolation.
- Action: Architecture decision needed. Document if intentional; fix at `:686` if isolation is intended.

---

### WEAK — Invalid or Thin Evidence Basis (4 claims)

**THEORY-003** | `weak`
- Claim: "Infrastructure failures must be exempt from retry budgets to avoid learned helplessness (Seligman 1972)"
- Refutation: Seligman studies biological organisms with persistent motivational state. LLMs have no persistent motivational state across calls. LLM domain transfer is invalid.
- What holds: The engineering principle (infra failures shouldn't consume persistence budget) is valid without Seligman.
- Action: Remove Seligman citation; retain infrastructure-exemption principle with engineering rationale.

**THEORY-005** | `weak`
- Claim: "Persistence thresholds are learnable via meta-RL (Wang/Duan 2016)"
- Refutation: Wang/Duan 2016 are arXiv preprints (not peer-reviewed). Fundamental training/inference gap: meta-RL learns policies during training via gradients; Poe operates at inference-time only. "Learnable via meta-RL" implies architectural commitment Poe does not have.
- What holds: Thresholds are, in principle, learnable. Tag as design aspiration.
- Action: Tag `[DESIGN ASPIRATION — FUTURE ARCHITECTURE]`.

**THEORY-006** | `weak`
- Claim: "Five distinct zoom-out signals exist"
- Refutation: All five signals originate from human psychology/neuroscience/military doctrine (Hatano, Schwartz, Fleming×2, Boyd). LLM agents have no metacognitive monitoring layer, no proprioceptive error signals, no adversarial loop context. Each signal needs independent LLM validation.
- What holds: The five-signal taxonomy is a useful design framework, not a validated specification.
- Action: Tag each signal `[HUMAN ANALOG — LLM VALIDATION REQUIRED]`. Signals 3 (confidence) and 5 (schema fitness) have most viable LLM analogs.

**THEORY-014** | `weak`
- Claim: "Orientation hygiene (Boyd OODA) requires pre-retry verification"
- Refutation: Boyd OODA is military doctrine (source doc explicitly rates Low-Medium confidence). No controlled studies outside military contexts. Core mechanism (adversarial disruption of opponent's decision loop) has no analog in software task execution.
- What holds: The three-question checklist (inputs valid? criterion serves goal? environment shifted?) is independently useful as an engineering heuristic.
- Action: Remove OODA attribution; retain 3-question checklist as `[ENGINEERING HEURISTIC]`.

---

### Cross-Cutting Issues

**CC-01 — Domain transfer disclaimer (all 17 THEORY claims):**
All THEORY claims apply findings from human psychology, RL theory, or military doctrine to LLM-based AI orchestration without empirical validation. `zoom-metacognition-adaptive-expertise.md §Gap 5` warns of this; that warning must be propagated as a standing header on `productive_persistence.md`:

> ⚠️ **Domain transfer caveat:** All theoretical claims (THEORY-001 through THEORY-017) apply findings from human psychology, cognitive science, RL theory, and military doctrine to LLM-based AI orchestration. None of these transfers have been empirically validated in this domain. Treat each as a *design hypothesis*, not a validated specification.

**CC-02 — Source confidence cascade:**
Source docs rate OODA at Low-Medium and Double-loop learning at Medium. Derived claims cannot exceed source confidence. Annotate each THEORY claim with provenance: `[source: OODA, confidence: Low-Medium]`.

**CC-03 — IMPLEMENTED vs DESIGN HYPOTHESIS tagging absent:**
`productive_persistence.md` has no implementation-status tags. Readers cannot distinguish shipped features from design aspirations. Functions absent from `src/`: Gittins/UCB computation, Kapur consolidation phase, OODA orientation hygiene, Bereiter & Scardamalia schema fitness criteria, near-miss detection, confidence-accuracy decoupling (blocked), meta-ignorance detection.  
Action: Audit `productive_persistence.md` section-by-section and tag each claim `[IMPLEMENTED]` / `[DESIGN HYPOTHESIS]` / `[DESIGN ASPIRATION]`.

---

## 7) Risks, Unknowns, and What Would Change Our Mind

- **Risk:** Proceeding with Wave 3 confidence-gap triggers before LLM calibration audit — builds on systematically unreliable signal input.
- **Risk:** Using stale IMPL-003/IMPL-004 gap claims to prioritize implementation work — would cause duplicate or wasted work on already-shipped features.
- **Risk:** Treating THEORY citations as validated foundations for architecture decisions — exposes design to invalidation when LLM domain transfer fails empirically.
- **Unknown:** Whether `inject_scope_into_context` at `scope.py:623` fully covers Phase 65 `inject_scope_into_plan` semantics.
- **Unknown:** Whether stale-context injection at `agent_loop.py:686` is intentional continuity or an isolation bug.
- **Unknown:** Which retry path (`_handle_blocked_step` ~:526 vs `_decide_stuck_action` ~:3335) handles the majority of real-world failures — affects whether `_is_converging` being wired at `:3344` is sufficient coverage.
- **Disconfirming evidence:** An empirical study showing LLM self-reported confidence correlates with factual accuracy at r > 0.7 would partially validate THEORY-009 confidence-gap triggers.

---

## 8) Recommendation

1. **Fix the four directly refuted claims immediately** (IMPL-003, IMPL-004, CODE-012, THEORY-011) — source docs used for active design decisions must not contain factually false gap claims.
2. **Block Wave 3 confidence-gap triggers** pending LLM calibration audit.
3. **Propagate domain-transfer caveat** to `productive_persistence.md` header.
4. **Tag each THEORY claim** as `[IMPLEMENTED]` / `[DESIGN HYPOTHESIS]` / `[DESIGN ASPIRATION]`.
5. **Work down the ordered action queue** in priority order (CRITICAL → HIGH → MEDIUM → LOW → CC).

**Confidence level:**
- HIGH on the four direct refutations (code-confirmed at named line numbers)
- HIGH on THEORY-009 block (three independent external papers confirm LLM miscalibration)
- MEDIUM on THEORY attribution issues (research docs support the counter; attribution is partly editorial judgment)

---

## 9) Next Actions (Ordered)

| # | Priority | Claim | Action | Target |
|---|----------|-------|--------|--------|
| 1 | CRITICAL | IMPL-003 | "Hash not yet present" → "_error_fingerprint() ships at agent_loop.py:2701" | research-brief-findings-and-design.md §94 |
| 2 | CRITICAL | IMPL-004 | "Not wired" → "_is_converging() wired at agent_loop.py:3344" | research-brief-findings-and-design.md §95 |
| 3 | CRITICAL | CODE-012 | Remove user/GOALS.md from missing-file list; file EXISTS | docs/md-claims-audit.md:49 |
| 4 | CRITICAL | THEORY-011 | Update status: "_is_converging() wired at agent_loop.py:3344" | research-brief-findings-and-design.md:76 |
| 5 | HIGH | THEORY-009 | **BLOCK** Wave 3 confidence triggers; add LLM miscalibration caveat | productive_persistence.md + BACKLOG |
| 6 | HIGH | THEORY-002 | Update: hash IS wired; stuck_streak + _error_fingerprint + _is_converging all active | research-brief-findings-and-design.md:64 |
| 7 | HIGH | THEORY-007 | Update from "partial (stuck_streak)" to full implementation status | research-brief-findings-and-design.md:71 |
| 8 | HIGH | ADVERSARIAL-004 | Architecture decision: intentional continuity vs isolation bug at agent_loop.py:686 | design review |
| 9 | MEDIUM | THEORY-004 | Remove UCB/Gittins citation; reframe as engineering heuristic | productive_persistence.md |
| 10 | MEDIUM | THEORY-001 | Tag `[DESIGN SYNTHESIS]`; remove Duckworth/Kapur attribution for hypothesis-narrowing | productive_persistence.md |
| 11 | MEDIUM | CODE-004 | Clarify "defined in" (knowledge_lens.py:802) vs "importable via" (memory.py) | docs |
| 12 | MEDIUM | CODE-005 | Compare scope.py:623 to Phase 65 semantics; determine if gap is real | Phase 65 plan |
| 13 | MEDIUM | AUDIT-001 | Validate RUNTIME_ABSENT bucket (~46 claims); treat 85% as lower bound until done | docs/md-claims-audit.md |
| 14 | LOW | THEORY-014 | Remove OODA attribution; retain 3-question checklist as `[ENGINEERING HEURISTIC]` | productive_persistence.md |
| 15 | LOW | THEORY-003 | Remove Seligman citation; retain infra-exemption principle with engineering rationale | productive_persistence.md |
| 16 | LOW | THEORY-005 | Tag `[DESIGN ASPIRATION — FUTURE ARCHITECTURE]`; remove "learnable via meta-RL" | productive_persistence.md |
| 17 | LOW | THEORY-006 | Tag each of 5 signals `[HUMAN ANALOG — LLM VALIDATION REQUIRED]` | productive_persistence.md |
| 18 | CC | All THEORY | Add domain-transfer caveat block to productive_persistence.md header | productive_persistence.md |
| 19 | CC | All THEORY | Tag each claim: `[IMPLEMENTED]` / `[DESIGN HYPOTHESIS]` / `[DESIGN ASPIRATION]` | productive_persistence.md |

---

## 10) Appendix

### Claim count reconciliation

Step 1 reported 54 claims from `claims.json` metadata. Direct category enumeration yields 58 (AUDIT:6 + CODE:14 + THEORY:17 + IMPL:9 + DISSENT:5 + ADVERSARIAL:4 + RISK:3). The 58 figure is used throughout; the 4-claim discrepancy is a metadata artifact in `claims.json`.

### Complete claim ratings

Full per-claim ratings: `projects/adversarial-verification-for-each-key/artifacts/claim-ratings.json`  
Flagged claims with detailed action notes: `projects/adversarial-verification-for-each-key/artifacts/flagged-claims.md`  
Internal counter-evidence (codebase search): `artifacts/counter-evidence-internal-part1.md`, `artifacts/counter-evidence-internal-part2.md`  
External counter-evidence: `artifacts/counter-evidence-external.md`

### Audit methodology weakness note

String-match grounding checks (used for the 85% AUDIT figure) miss:
- Underscore-prefixed privates (`_error_fingerprint` won't match `error_fingerprint`)
- Re-exported symbols (`calibrated_alignment_threshold` via `memory.py` re-export)
- Case-sensitivity on filenames
- Semantic equivalents with different names (`inject_scope_into_context` vs `inject_scope_into_plan`)

The 85% grounding rate should be treated as a rough lower bound, not a precise statistic.

### Evidence quality notes

- **Zero weak claims** in AUDIT, CODE, IMPL, DISSENT, ADVERSARIAL, RISK — all 4 weak claims are in THEORY, where LLM domain-transfer invalidity is the primary issue.
- **13 contested claims** (22%) is higher than a stable codebase would show — reflects that the source docs are an active research layer under rapid iteration.
- **Most reliable evidence cluster:** DISSENT-001 through DISSENT-005 (all strong, direct textual evidence) and IMPL-001/002 (code-confirmed).
- **Most uncertain cluster:** THEORY-001, THEORY-004, THEORY-005, THEORY-014 — human research with no LLM-domain empirical grounding.

---

*Generated by adversarial-verification pipeline (8 steps, session 2026-05-12). Code citations verified against `src/` at time of generation. External literature findings from training data — verify DOIs before use in published documentation.*
