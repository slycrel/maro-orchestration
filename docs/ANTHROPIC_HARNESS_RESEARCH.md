# Anthropic Harness Design Research — Captured 2026-03-25

Source: @codebypoonam tweet surfacing Anthropic Engineering Blog posts on long-running agent harnesses.
82K views, 1,622 bookmarks — high signal.

Primary sources:
- [Harness design for long-running application development](https://www.anthropic.com/engineering/harness-design-long-running-apps)
- [Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)
- [Demystifying Evals for AI Agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)

---

## 1. Three-Agent App Builder

Three sequential specialized agents produce production-quality full-stack apps in ~6 hours without human intervention.

### Roles

**Planner** — Converts 1–4 sentence prompt into full product spec. Deliberately avoids granular technical detail (prevents cascading implementation errors). Actively identifies opportunities to embed AI features. Outputs a spec file.

**Generator** — Implements features one sprint at a time. Before each sprint, negotiates a **sprint contract** with the Evaluator — a pre-implementation agreement on what "done" means with explicit testable success criteria.

**Evaluator** — Uses Playwright MCP to interact with the live application like a real user (active clicking, not screenshot grading). Grades sprints against four criteria: Design Quality, Originality, Craft, Functionality. Tuned toward skepticism — not leniency.

### Key design decisions

**GAN-inspired:** Generator-Evaluator loop explicitly mirrors GANs. Models "confidently praise their own mediocre output," so externalizing evaluation and tuning the evaluator toward skepticism is far more tractable than making a generator self-critical.

**File-based handoffs:** Agents communicate via structured files (spec file, sprint contracts, progress log) — not direct conversation. One agent writes, the next reads. Maintains faithful implementation without over-specification.

**Context continuity:** One continuous session using Claude Agent SDK's automatic compaction. No context resets. Opus 4.5+'s improved coherence removed "context anxiety" — earlier models would lose coherence over long sessions.

**Design anti-patterns penalized:** Evaluator down-scores "generic AI slop patterns" via few-shot calibration examples. Originality and Design Quality weighted more heavily than Functionality.

---

## 2. Two-Agent Session-Continuity Pattern

For multi-session long-running work where context windows reset between sessions.

### Two phases

**Initializer Agent** (runs once at project start):
- Creates `init.sh` — reproducible environment bootstrap
- Creates `claude-progress.txt` — session-to-session narrative work log
- Creates `feature_list.json` — structured requirements (200+ items) all marked `passes: false`
- Makes initial git commit

**Coding Agent** (every subsequent session):
1. Read `pwd` → read git log + progress file
2. Check feature list state
3. Run `init.sh` (reproducible environment)
4. Run end-to-end tests (detect broken state before touching anything)
5. Pick highest-priority incomplete feature
6. Implement → commit → update progress file → repeat

**Critical constraint:** "It is unacceptable to remove or edit tests." Feature manifest is immutable — agents can only flip `passes: false → true`, never delete entries or lower standards.

**Incremental checkpointing:** Commit immediately after each feature, leaving code in mergeable state at all times.

---

## 3. Sixteen-Agent C Compiler

Built a 100k-line Rust-based C compiler capable of building the Linux 6.9 kernel (x86, ARM, RISC-V). ~$20K API cost, ~2,000 sessions over two weeks.

**Architecture:** Fully distributed — no central orchestrator. Each agent runs in a Docker container with shared Git repo access.

**File-based lock coordination:** Agent "takes a lock" by writing to `current_tasks/`. On completion: merge other agents' changes locally → push branch → release lock. Merge conflicts handled autonomously.

**Running failure docs:** Agents maintain persistent documentation of failed approaches and remaining tasks — preventing duplicate effort on known-unsolvable paths across sessions.

**GCC as test oracle:** External authoritative reference system validates work. When Claude compiler handled some subtrees, GCC compiled the rest — preventing competing fixes from overwriting each other.

**Emergent specialization:** Some agents gravitated toward documentation, others toward code quality — without explicit role assignment.

---

## 4. Evaluation Framework

From "Demystifying Evals for AI Agents":

**Three grader types:**
1. **Code-based** — string matching, regex, static analysis, outcome verification, tool call validation. Fast/reproducible, but miss valid alternative solutions.
2. **Model-based** — rubric scoring, NL assertions, pairwise comparisons. Handle subjectivity, require calibration.
3. **Human graders** — gold standard for calibrating the above.

**Two eval modes:**
- **Capability evals** — start at low pass rates, track new functionality
- **Regression evals** — target ~100% pass rates, catch degradation

**Non-determinism metrics:**
- `pass@k` — probability ≥1 correct across k attempts (exploratory capabilities)
- `pass^k` — probability ALL k attempts succeed (increasingly stringent; regression gate)

**Active testing principle:** Computer use agents verify outcomes via DOM/filesystem/database state inspection — not API responses. The oracle is the real system state.

---

## 5. The "March of Nines" Problem

Compounding failure across long agent chains. A 10-step Feature with 90% per-step success = 35% chance of failure on the full chain. Daily failure is the baseline.

**Fix:** Stage gating (validate before progressing) + state persistence (safe restart without re-running passing stages). Not retry logic — genuine checkpoint-and-resume.

This is the quantitative argument for Poe's milestone validation architecture. Each milestone boundary must be a real gate, not just a notification.

---

## Prioritized Hit List for Poe

| Priority | Concept | Where it maps in Poe |
|----------|---------|---------------------|
| ★★★ | Sprint contracts (pre-flight "done" agreement) | Phase 11 hooks → Feature boundary |
| ★★★ | GAN loop: Generator ≠ Evaluator, always skeptical | Phase 12 Inspector calibration |
| ★★★ | Initializer/Coding Agent boot protocol | Phase 10 Mission Worker startup |
| ★★ | `pass@k` / `pass^k` in skill gate | Phase 14 skill test gate |
| ★★ | Running failure docs per Worker session | Phase 14 attribution → persistent artifact |
| ★★ | File-based immutable feature manifest | Phase 10 feature_list.json pattern |
| ★★ | Few-shot calibration for Inspector rubrics | Phase 12 Inspector |
| ★ | Distributed file-lock coordination (no central orch) | Future (current scale doesn't need it) |
| ★ | External oracle for verification | Future (Polymarket: verify vs. market data) |

---

## Core Insight (applies across all of it)

**Define "done" before starting work, not after.**

Poe has the architectural slots (Inspector, goal ancestry, hook system, skill library) but the contracts are implicit and post-hoc. Making them explicit and pre-flight — sprint contracts negotiated before a Worker starts, test cases generated before a skill mutation is written — is the gap that Phase 19 closes.

The GAN insight is equally important: no Worker should ever grade its own output. Inspector must be a separate context tuned toward skepticism, not a self-review pass.
