<!-- Produced by a maro run (PYTHONPATH=src python3 -m handle), 2026-08-19.
     Source: https://x.com/daniel_mac8/status/2089768482824921127
     Subject repo: https://github.com/DannyMac180/sol-advisor @ main
     Run cost, self-reported by the loop's introspect phase: 8/8 steps,
     ~3.6M tokens, 641s wall. Flagged [cost_spike] by our own inspector. -->

# Sol-advisor efficiency claim vs. Maro's orchestration design

> **Follow-up, 2026-08-20.** Reading this prompted Jeremy to state the target
> architecture (cheap/local step work under a 1–3 shot high-tier plan) — recorded
> as a GOAL_BRAIN decision. Checking whether we could get there surfaced history
> this analysis did not have: per-step cheap tiering was **built** (Phase 57,
> `classify_step_model`) and **deliberately removed** 2026-07-21 (`b6fd4881`)
> under the 2026-07-20 "execution defaults unified at MID" decree, after Haiku
> measured **4.4× tokens** vs Sonnet on the same goal
> (`docs/history/2026-03-31-factory-mode-findings.md` §3). So "route step work to
> a cheaper model" is a *settled* question here, not an open one — re-test against
> that baseline rather than rebuilding. A second finding: the post-run "async
> tail" only reorders in-process, so a blocking caller still waits for it — 53% of
> this very run's wall clock. Both are BACKLOG items.

Source: Daniel Mac (@daniel_mac8), 2026-08-18, testing DannyMac180/sol-advisor (Codex plugin, 658 lines, zero executable orchestration code — a single-session prompt contract, no scheduler/state/memory/retry machinery).

## 1. What the experiment measured, and what it didn't

**Measured:** routing-decision token/time overhead across 3 scenarios (forced Luna subagent, risk-gated selector, Sol-only) on 10 tasks the author generated himself and explicitly built "to test efficiency rather than quality." Result: Sol-only was more efficient in every scenario except median *time* for the risk-gated selector — a result the headline "in every case" already contradicts once you read past it.

**Not measured:** output quality (excluded by design), correctness, cost-per-outcome under real pricing (Colelioenz's reply: 25M Luna tokens ≈ 1M Sol tokens for the same money — "more tokens" ≠ "more expensive" or "slower"), and — the load-bearing gap — anything that doesn't fit in one session. DevCalledFede's reply names this exactly: "the ten tasks all fit in one session, which is where delegation can only lose." N=10, single author, single repo, no statistical treatment, tasks self-selected to be small and bounded.

**Scope of the claim, precisely:** this is a test of whether asking a model to decide, mid-session, whether to hand a sub-task to a cheaper model saves tokens when the delegating model already holds full context and could just do the work itself. It is not a test of multi-agent systems that persist state, memory, or decomposition across sessions that don't fit one context window — sol-advisor has none of that machinery to test.

**Structural point handed to us, and confirmed:** sol-advisor's contract requires the root to (a) write a five-part spec before delegating and (b) re-verify the complete diff itself after ("treat worker reports as claims"). That's not incidental — it's the mechanism by which "orchestration overhead" gets generated. Any system with a mandatory spec-out + full-verify-back step will show this tax on tasks small enough that the root could've just done the work. The question the goal poses — is this a property of multi-agent orchestration generally, or of this specific contract — has a real answer once you look at Maro: **it's the contract, but Maro reproduces the same shape at one specific point (see §3), not everywhere.**

## 2. Contrast against Maro, mechanism by mechanism

**Director/worker split (director.py):** same shape as sol-advisor's root/implementer split at the level of "one agent decomposes and delegates, another executes." That's where the resemblance ends structurally.

**Where the critique LANDS — worker review is sol-advisor's double-payment tax, reproduced:**
`_review_worker_output` (director.py:832-882) is a second real LLM call — `adapter.complete(system=_REVIEW_SYSTEM, user=directive+ticket+_clip(worker_result,4000), max_tokens=256)` — called **unconditionally** at both dispatch sites (director.py:527 initial, :565 revision-loop), with no mode/risk gating. Every dispatch pays: (1) directive authored by the director, (2) worker execution, (3) a second adapter call where the director re-reads the (clipped) result and decides accept/reject. Tokens from that review call land in the same `total_tokens_in/out` bucket the orchestration overhead would be measured against. On rejection, `MAX_REVIEW_ROUNDS` (director.py:538-582) repeats the full dispatch+review cycle. This is structurally identical to DevCalledFede's "Sol writes the spec, then pays again to read the result back" — Maro just does it in code instead of prose, and does it as an unconditional review call rather than a full-diff re-read.

One difference that cuts against Maro, not for it, though the exact shape needs correcting: `run_director()` does have a `skip_if_simple` flag (director.py:308, `_is_simple_directive` at :262) that routes single-scope, ≤15-word directives straight to `run_agent_loop`, skipping SPEC + challenge + dispatch + review entirely — a real analog to sol-advisor's `solo`. The asymmetry isn't "Maro has no bypass," it's that the bypass **defaults to off** (`skip_if_simple: bool = False`, labeled in-code as the "Skip-Director experiment") where sol-advisor's `solo` is the recommended default. Once a directive reaches `run_director()` with the default flags — which is the common path — `dispatch_worker()` itself still has no per-ticket bypass: review is mandatory on every dispatched ticket regardless of triviality or risk. Both things are true; only the "no equivalent exists" framing was wrong.

**Where the critique MISSES — adaptive execution loop and memory:**
Maro's decompose/execute/introspect loop with reassess/replan/restart/escalate is built for runs that don't fit in one context — the exact axis Daniel Mac's test structurally excludes (10 tasks chosen to each fit in one session). Once a task genuinely spans multiple turns or requires state to survive beyond a single context window, "just use one model in one session" isn't an available alternative to compare against — the single-session model would need to be re-primed from scratch, which is its own overhead the experiment never has to pay because it never tested that regime.

**Where the critique MISSES — memory/lessons amortize the spec tax over time, sol-advisor doesn't:**
sol-advisor has zero memory — every delegation pays the full spec-writing cost, forever, with no learning across runs (confirmed: no state store in the plugin). Maro's `memory_ledger.py` (`verdict_trust()`: FULL/EXCLUDED/DIRECTIONAL classification) and `lesson_provenance.py` persist trust and outcome classification *across* runs, and `memory_port.py`'s decaying trust float (`trust: float = 1.0`, invalidation lowers but never deletes) means the system isn't re-deriving the same trust judgment from zero on every single dispatch the way sol-advisor's per-call prose contract does. This doesn't eliminate the review-call tax in §3, but it's a genuinely different amortization structure that a single-session, N=10, no-memory test cannot see at all.

**Where the critique MISSES on enforcement mechanism (real, but doesn't change the token math):**
`dispatch_envelope.py`'s untrusted-payload boundary (documented at lines 181-187) is a parsed/validated structural boundary (`EnvelopeError`, `parse_dispatch_payload`) — not a narrative instruction asking the model to "treat worker reports as claims" and manually re-read a diff. That's a real difference in *how* distrust is enforced (code vs. prompt), and it means Maro's trust boundary can't be talked out of itself the way a prose contract can. But it's an enforcement-robustness distinction, not an efficiency distinction — it doesn't reduce or add to the token cost of `_review_worker_output`.

## 3. Where we are genuinely exposed

**#1 — `_review_worker_output` is ungated within `dispatch_worker()`, and the one bypass that exists (`skip_if_simple`) defaults off.** For small, bounded, single-context tasks — exactly Daniel Mac's test shape — Maro would lose the same comparison *as actually deployed*, because `run_director()`'s default flags don't invoke the `skip_if_simple` bypass (director.py:308, default `False`). If a caller opts into `skip_if_simple=True`, a narrow class of directives (single-scope, ≤15 words, no multi-step signal — `_is_simple_directive`, director.py:262) does route around SPEC + dispatch + review entirely, so the bypass exists in the codebase. But it's opt-in and heuristically narrow, versus sol-advisor's `solo` being the recommended default for a majority of work. So: on Maro's default path, and on any directive too complex for `_is_simple_directive`'s ≤15-word/single-sentence gate even with the flag on, Maro pays dispatch + mandatory review tokens with no escape hatch — sol-advisor's own default (skip delegation) is a lighter-weight policy than Maro's default behavior.

**#2 — the revision loop compounds it, bounded by a small constant.** On rejection the loop retries `MAX_REVIEW_ROUNDS - 1` additional times (director.py:540); `MAX_REVIEW_ROUNDS = 2` (director.py:56), so worst case is 2 total dispatch+review cycles per ticket — bounded, not open-ended, but still a real doubling on any rejected ticket. Same shape as Daniel Mac's follow-up finding ("Luna as subagent still uses more Sol tokens than Sol alone" — driven by "creating plans, specs etc."). This is the tax multiplied, not just paid once, and it scales with how often workers fail review, which nothing in the current design gates on ticket size or risk.

## 4. Change or no change

**No change to the director/worker split or the adaptive execution loop as designed.** These solve a problem — decomposition and state persistence across work that exceeds one context — that this experiment structurally cannot speak to. DevCalledFede's reply already makes this argument better than we could reconstruct it after the fact, and it holds: orchestration earns its keep exactly where a single-session comparison doesn't apply.

**One scoped thing worth a real decision, not a snap fix:** `dispatch_worker()` currently has no size/risk-gated bypass for `_review_worker_output` — every dispatch pays a mandatory review call regardless of ticket triviality. This is the one place the critique lands with actual force on Maro, and it's a legitimate design question (should trivial, low-risk worker outputs skip review the way sol-advisor's `solo` mode skips delegation entirely?) rather than something to patch reflexively from a single N=10 thread. Recording this as a decision candidate rather than making the change here — code changes to dispatch/director logic are out of scope for this analysis, and the tradeoff (review-skip risk vs. token savings) deserves its own evaluation, not a reaction to one tweet thread.
