# Memento-Skills Research — Captured 2026-03-25

Deep dive into Memento-Skills: self-evolving agents with external skill memory. Source: Tom Doerr tweet, arXiv:2603.18743, GitHub repo, project site.

Tweet: `@tom_doerr/status/2036622012399976516` — Tom Doerr amplifying the UCL paper. 241 stars at time of research.

---

## What It Is

**Memento-Skills** ("Let Agents Design Agents") — a framework enabling LLM agents to autonomously generate, test, repair, and evolve their own skill libraries at deployment time, without updating the underlying model's parameters.

- Paper: arXiv:2603.18743, submitted March 19, 2026, University College London (17 authors)
- Repo: github.com/Memento-Teams/Memento-Skills (MIT license)
- Ships with pre-built GUI installers for macOS (Apple Silicon) and Windows x64

---

## Formal Foundation: SRDP

**Stateful Reflective Decision Process** — an extension of the standard MDP:

```
DSRDP = ⟨S, A, P, R, γ, M, pLLM⟩
```

State is augmented as `x_t := (s_t, M_t)` — environment state plus an evolving external skill memory `M_t`. This recovers the Markov property for a learning agent without touching LLM weights. The skill library *is* the memory; updating it is how the agent learns.

---

## The Read–Execute–Reflect–Write Loop

```
1. Read     — skill router retrieves candidates from local library + remote catalog
              (sparse BM25 + dense embeddings, score-aware reciprocal rank fusion,
               optional cross-encoder reranking)

2. Execute  — skills run via tool calling in sandboxed uv environment

3. Reflect  — on failure: LLM-based failure attributor pinpoints the specific
              skill responsible (credit assignment at skill level, not task level)

4. Write    — four sub-mechanisms:
              a. Failure Attribution: identify which skill failed
              b. Skill Rewriting: targeted file-level patch to fix that skill
              c. Skill Discovery: if empirical success rate < threshold →
                 escalate to fundamentally redesign or synthesize a new skill
              d. Unit-Test Gate: auto-generate test cases from failures,
                 run against mutation before write-back (prevents regression)
```

The write-back loop is the core advance over predecessors like Voyager (which stored JS skills by description and never updated existing ones).

---

## Skill Format

Stored as structured markdown files with three sections:
1. **Declarative spec** — description and applicable contexts
2. **Executable behavior** — Python code + multi-step tool workflows
3. **Prompts and guardrails**

File-level mutations only — never model parameter updates.

---

## Behavior-Aligned Router (Key Novelty)

Rather than pure semantic similarity, the router is trained with **one-step offline RL** using multi-positive InfoNCE loss — learned scores interpreted as a soft Q-function with Boltzmann policy.

Training data: synthetic queries from local skill DB, filtered by LLM judge.

Results:
- Recall@1 = 0.60 (trained router)
- Recall@1 = 0.32 (BM25 baseline)
- Recall@1 = 0.54 (semantic baseline)
- ~10% relative improvement over best semantic baseline

This is the hardest part to replicate but also the highest leverage — routing that predicts execution success rather than semantic similarity.

---

## Benchmark Results

| Benchmark | Before evolution | After evolution | Gain |
|-----------|-----------------|-----------------|------|
| GAIA (test) | 52.3% | 66.0% | +13.7 pp (26% relative) |
| HLE (test) | 17.9% | 38.7% | +20.8 pp (116% relative) |

- GAIA: skill library 5 → 41 skills over 3 rounds
- HLE: skill library grew to 235 skills across 8 academic domains; strong within-domain cross-task transfer (biology training skills reused on unseen biology test questions)
- Underlying model: Gemini-3.1-Flash

---

## Security: Skill Poisoning

A real attack vector identified in the paper: a single poisoned agent can contaminate 87% of downstream decisions within 4 hours. Logically unsolvable paradoxes or contradictory API responses exploit the Write phase.

The paper surfaces this but doesn't fully solve it. Worth designing defenses into any skill library we build.

---

## Tech Stack

- LLM access: litellm (Claude, OpenAI, Ollama, Kimi, MiniMax, GLM)
- UI: Flet (desktop), Typer/Rich (CLI), WebSocket (Feishu bridge)
- Storage: SQLite + SQLAlchemy + sqlite-vec for embeddings
- Retrieval: BM25 + dense embeddings
- Sandbox: `uv` for isolated execution

Baseline skill set (9): web search, filesystem, terminal, image analysis, PDF/DOCX/XLSX/PPTX processing, skill creation, Python dependency install.

---

## Adjacent Work (Tom Doerr)

**`tom-doerr/agent`** — NLCO (Natural Language Constraint Optimization) iteration loop using DSPy. Refines an artifact against constraints stored in markdown with persistent memory files. Same impulse as Memento but focused on single-artifact refinement, not skill library evolution.

**`tom-doerr/cc_approver`** — DSPy-based permission hook for Claude Code's `PreToolUse` system. *Learns* which tool permissions are appropriate from labeled JSONL logs using MIPROv2/GEPA optimizers. Directly relevant: if Claude Code sessions run as Poe workers, this is a plug-in learned safety layer.

---

## What to Steal for Poe

Poe already has a skill library (`src/skills.py`) and a meta-evolver (`src/evolver.py`). Memento-Skills operationalizes the improvement loop more precisely at the individual-skill level.

### Prioritized hit list

**1. Failure attribution before retry (High — add to evolver/inspector)**

Poe's current recovery retries at the task level. Adding an LLM-based attributor that pinpoints *which specific sub-skill or tool workflow* caused the failure gives the evolver dramatically better signal. Inspector already grades sessions — route low-scoring calls back with attribution context.

**2. Unit-test gate on skill mutations (High — add to skills.py)**

Before the evolver pushes any updated skill/prompt/pattern into the live library, auto-generate synthetic test cases from failures and run them against the mutation. Prevents regressions from well-intentioned but broken updates. This is critical for any self-modifying system.

**3. Utility score per skill with threshold-based escalation (Medium)**

Track empirical success rate per skill across executions. When a skill consistently underperforms, escalate from "patch this skill" to "redesign from scratch." The Inspector already has friction detection — wire it to per-skill scoring and trigger escalation.

**4. Skill format: structured markdown (Medium — refine skills.py)**

Poe's current skills are loosely structured. Adopting the three-section format (declarative spec + executable behavior + guardrails) makes skills more reliably parseable, reusable, and patchable.

**5. Behavior-aligned retrieval router (Medium — harder)**

Poe's task routing uses keyword/semantic matching. Replacing with an offline RL-trained router (InfoNCE, execution-success as reward) would improve routing accuracy. Poe's Inspector already generates execution outcome labels — the training data exists, just needs to be structured and used.

**6. Sandbox isolation for skill execution (Medium)**

Poe's skills currently run in-process. Memento's `uv` sandbox isolation prevents a bad skill from corrupting the main process. Worth adding for any skills that run untrusted or externally-sourced code.

**7. Skill poisoning defenses (Lower — but real)**

Review all skill write paths for adversarial inputs. Consider: signature verification, content hashing, rate limits on rewrites, human approval gate for new-skill synthesis (vs. patches).

**8. SRDP formalism as design guide (Theoretical)**

`x_t := (s_t, M_t)` cleanly models what Poe's goal ancestry + skill library already does intuitively. Useful framing for thinking about what state a Worker Session needs to carry across context windows.

---

## Phase Mapping

These ideas map to Poe's roadmap as near-term extensions to existing phases:

| Idea | Natural home |
|------|-------------|
| Failure attribution | Phase 12 Inspector → route to evolver |
| Unit-test gate | Phase 10 `skills.py` extension |
| Per-skill success rate + escalation | Phase 10 `skills.py` + Phase 12 Inspector |
| Structured skill format | Phase 10 `skills.py` refactor |
| RL-trained router | Future phase (needs outcome label collection first) |
| Sandbox isolation | Future phase |
| Poisoning defenses | Future phase |

---

## Sources

- [GitHub: Memento-Teams/Memento-Skills](https://github.com/Memento-Teams/Memento-Skills)
- [arXiv:2603.18743](https://arxiv.org/abs/2603.18743)
- [alphaXiv overview](https://www.alphaxiv.org/overview/2603.18743v1)
- [Hugging Face paper page](https://huggingface.co/papers/2603.18743)
- [skills.memento.run](https://skills.memento.run/)
- [tom-doerr/agent](https://github.com/tom-doerr/agent)
- [tom-doerr/cc_approver](https://github.com/tom-doerr/cc_approver)
- [Tom Doerr GitHub](https://github.com/tom-doerr)
