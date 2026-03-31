# Factory Mode Findings — Phase 47 Results

**Completed:** 2026-03-31
**Branch:** `factory`
**Conclusion:** Adversarial review is load-bearing. Merge it. Shelve full factory mode comparison until Mode 2 is further along.

---

## What Was Built

Two factory variants as an alternative to Mode 2's Director/Worker/Inspector scaffolding:

**`factory_minimal`** (`src/factory_minimal.py`)
Single LLM call with a behavior-description system prompt. No loop, no decomposition, no verification.

**`factory_thin`** (`src/factory_thin.py`)
Decompose → Execute (N steps) → Adversarial Review → Compile. All prompt-driven, no Director/Worker routing, no persona injection, no lesson injection.

Both use the "Bitter Lesson" framing: describe the desired outcome, let the model figure out the how.

---

## Benchmark Results

### Nootropic Goal
*"What nootropic stack should I take for cognitive enhancement, focusing on evidence-based compounds with the best safety profiles?"*

| Variant | Model | Tokens | Cost | Time | Steps | Status |
|---------|-------|-------:|-----:|-----:|------:|--------|
| factory_minimal | Haiku | 30K | $0.058 | 98s | 1/1 | done |
| factory_thin (no adv) | Haiku | 263K | $0.323 | 336s | 8/8 | done |
| factory_thin+adv | Haiku | 318K | $0.377 | 375s | 6/6 | done |
| factory_thin+adv+verify | Haiku | 266K | $0.362 | 493s | 6/6 | done |

### Polymarket Goal
*"Research winning Polymarket prediction market strategies: what do top wallet holders do differently, which market categories show edge, and what is the optimal bet sizing approach for a $1000 starting stake?"*

| Variant | Model | Tokens | Cost | Time | Steps | Status |
|---------|-------|-------:|-----:|-----:|------:|--------|
| factory_minimal | Haiku | 25K | $0.035 | 60s | 1/1 | done |
| factory_thin+adv | Haiku | 1,512K | $1.395 | 574s | 7/8 | partial |
| Mode 2 (full) | Sonnet | 344K | $1.274 | 1156s | 8/8 | done |

---

## Findings

### 1. Adversarial review is load-bearing (+$0.05, catches real errors)

Comparing factory_thin (no adv) vs factory_thin+adv on nootropic: +17% cost, +12% time, 4 real corrections caught:
- L-theanine dose range overstated
- Armodafinil + bromantane interaction downgraded from "safe" to "unknown"
- BPC-157 incorrectly classified as a nootropic
- Retatrutide interaction severity overclaimed

**Action taken:** Adversarial two-pass merged into `quality_gate.py`. `handle.py` appends contested claims to result text. This ships in main regardless of factory mode outcome.

### 2. Ralph verify loop is useful but not default-worthy (+30% wall time)

Verify caught a step 4 truncation on the nootropic run and triggered a retry that produced a more complete synthesis. But the retry loop adds ~30% wall time. Right call: flag-enabled (`--verify` / `ralph:` goal prefix), not on by default.

**Action taken:** `verify_step()` added to `step_exec.py`. `agent_loop.py` wires it via `ralph_verify` kwarg and `ralph:`/`verify:` magic prefixes.

### 3. Haiku token explosion makes factory_thin uneconomical on complex goals

factory_thin+adv polymarket used **1,512K tokens** vs Mode 2's **344K** — a 4.4× ratio — because Haiku lacks the output compression judgment that Sonnet applies. Step 1 alone consumed 560K tokens on the wallet research step.

The Mode 2 `EXECUTE_SYSTEM` prompt has explicit token efficiency language ("Target under 500 tokens for your complete_step result"). `FACTORY_STEP` lacked this. **Fixed in this commit.**

But even with the fix, Haiku's verbosity means factory_thin on Haiku is not reliably cheaper than Mode 2 on Sonnet for research goals. The model cost advantage disappears.

### 4. Mode 2 scaffolding — what's expendable vs load-bearing

**Not load-bearing** (removing didn't hurt quality):
- Persona routing
- Lesson injection
- Multi-plan comparison (Director overhead)
- Pre-plan challenger

**Load-bearing** (confirmed via this experiment):
- Adversarial review (merged)
- Token efficiency prompt
- Ralph verify loop (available, not default)

### 5. Wall time: factory_thin wins even at equivalent cost

factory_thin+adv polymarket: 574s. Mode 2: 1156s. The Director+Worker+Inspector scaffolding is ~50% of elapsed time. Matters for interactive use cases.

### 6. Variance is still high

Nootropic runs were clean (all steps done). Polymarket partial (step 2 stuck on data-cutoff issue). The quality gap between minimal and thin+adv was visible but hard to quantify rigorously without a scoring rubric. Need more test runs and a scoring framework before drawing strong conclusions.

---

## What Was Merged to Main

| Change | File | Status |
|--------|------|--------|
| Adversarial two-pass | `quality_gate.py` | ✅ merged |
| Contested claims display | `handle.py` | ✅ merged |
| `verify_step()` function | `step_exec.py` | ✅ merged |
| Ralph verify in agent loop | `agent_loop.py` | ✅ merged |
| `build_adapter(timeout=)` param | `llm.py` | ✅ merged |
| factory_minimal and factory_thin | `src/factory_*.py` | ✅ available, not wired to handle.py |

---

## What's Deferred (Phase 49)

The core question — *can we prompt our way into the full pipeline, or does the scaffolding carry real load?* — remains open. We've confirmed which specific pieces are load-bearing (adversarial review, token efficiency) and which aren't (persona routing, lesson injection). But the overall comparison needs:

1. **More test runs** across more goal types before drawing strong conclusions
2. **A scoring rubric** — right now quality comparison is subjective ("feels better")
3. **Mode 2 maturity** — Mode 2 is still changing (Phase 46 graduation, context compression). Testing factory mode vs a moving target produces noisy signal.
4. **Sonnet factory run** — all factory runs used Haiku; a Sonnet factory run would isolate prompt design from model capability

The right time to revisit: after Phase 46 ships and Mode 2 has been stable for a few weeks.

**Phase 49 scope:** Run both factory variants against a stable Mode 2 baseline, on 3+ goal types, with a scoring rubric. Decide: merge factory as `--mode thin` flag, or discard. See `ROADMAP.md Phase 49`.
