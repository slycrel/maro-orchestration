# The DeepMind/MIT coordination-cost study vs. maro's topology — and the audit of our own logs

Date: 2026-08-21 (dev Mac session). Source lead: BACKLOG "Link-farm research
leads" #2 — Yarchi @undefinedki, https://x.com/undefinedki/status/2087634870260449474
(2026-08-14). **Provenance loop closed:** Jeremy dispatched this exact link
2026-08-14 as run `751e2dea-merry-thicket`, which ended incomplete ("verdict
partial pending full verification") — this document is that unfinished
question, finished. The prior run's partial ANSWER.md was read first and its
findings are built on, not rediscovered (the Re-run identity lesson, honored
manually).

## 1. The paper, actually

"Towards a Science of Scaling Agent Systems: When and why agent systems
work" — Google DeepMind + MIT Media Lab, arXiv:2512.08296, published in
Nature Machine Intelligence 8:1157–1172 (July 2026). 260 controlled
configurations: **5 architectures** (single-agent SAS, independent MAS,
centralized MAS, decentralized MAS, hybrid MAS) × **9 models in 3 families**
(GPT-5-nano/mini/5; Gemini 2.0 Flash / 2.5 Flash / 2.5 Pro; Claude Sonnet
3.7 / 4 / 4.5) × **6 benchmarks** (web browsing, financial reasoning,
planning, work automation, software engineering, terminal tasks). Prompts,
tools, and compute budgets held constant; only coordination structure and
model capability vary.

**The numbers that matter for us:**

- **Capability-saturation threshold ≈ 45%.** Tasks where single-agent
  accuracy already exceeds ~45% experience *negative* returns from
  additional agents (interaction term β̂ = −0.236, p = 0.004 — the paper's
  "baseline paradox"). Single-agent baseline is the most robust predictor
  of whether coordination helps at all.
- **The gain/degradation range is wide and sign-flips by task structure:**
  +80.8% on decomposable financial reasoning, −70.0% on sequential
  planning. Decomposability is the regime where fan-out pays; sequential
  dependence is where it burns.
- **SWE-bench Verified: every multi-agent variant lost to solo.** SAS mean
  0.522; hybrid −2.1%, centralized −3.1%, decentralized −5.4%, independent
  −14.9%. Attributed to capable models sitting above the 45% threshold.
- **Error amplification by architecture** (trace-level, vs SAS 1.0×):
  centralized 4.4×, hybrid 5.1×, decentralized 7.8×, **independent 17.2×**.
  What contains errors in the centralized case is the verification
  bottleneck — someone reads the work before it becomes the answer.
- **Coordination is super-linearly expensive.** Turns scale as
  T = 2.72·(n+0.5)^1.724 (R² = 0.974); communication overhead over SAS:
  independent +58%, decentralized +263%, centralized +285%, hybrid +515%.
  Success-per-turn efficiency: SAS 0.466 vs centralized 0.120 (3.9×
  penalty) and hybrid 0.074 (6.3×).
- **Predictive model:** picks the best architecture for a task in 87% of
  held-out configurations — but cross-validated R² is only 0.373–0.413, so
  the regression explains a minority of outcome variance. Read the
  thresholds as strong directional evidence, not a law.

**Standing caveat, same one as the sol-advisor analysis
(`research/2026-08-19-sol-advisor-efficiency-claim.md`):** the benchmark
tasks fit in one context. Orchestration whose purpose is state that
*outlives* one context window — maro's core loop — is structurally outside
what was measured. The paper is evidence about *concurrent fan-out on a
bounded task*, not about sequential decompose/persist/resume.

## 2. The audit of our own logs: the multi-agent arm is empty

The backlog bullet asked: "Audit recent multi-agent runs against solo
baselines IN OUR OWN LOGS." Done, on the box's live workspace (read-only,
2026-08-21). The honest result is that **there are no multi-agent runs to
audit** — maro as deployed is a single-agent system:

- `memory/step-costs.jsonl`: 4,919 call records ($418.61, 536M tokens),
  every one from the solo agent loop (step types: general 1,979, research
  1,410, verify 503, write 493, implement 111, analyze 270, plan 130,
  summarize 23). No worker-dispatch or review-call records.
- `memory/captains_log.jsonl`: 7,120 rows, **zero** director/worker/review
  events.
- `memory/events.jsonl`: all 205 rows matching "director" are goals *about*
  the director code (the 2026-08-19 sol-advisor analysis, the link-farm
  scan runs) — not the Director path executing.
- No `telegram_listener` process (the other `run_director` entry point) is
  running on the box.

So the Director/worker split — our centralized-MAS shape — is code-live but
**production-dormant**. The degradation pattern can't reproduce in our logs
because we never ran the arm that would produce it. The comparison the
paper makes is one our deployment already resolved in favor of solo.

## 3. What we can measure: our checking tax vs. theirs

maro's solo loop carries a verification lane (closure verify, claim
probes). From step-costs: verify = 503/4,919 calls, $52.11 of $418.61 =
**12.4% of spend**. Compare the paper's centralized-MAS coordination cost
for the same error-containment mechanism: +285% communication overhead and
a 3.9× efficiency penalty.

That contrast is the doctrinal point: **maro gets the verification
bottleneck — the exact mechanism that keeps centralized error amplification
at 4.4× instead of 17.2× — *inside* one agent at ~12% overhead**, because
the verifier shares the worker's context instead of being a separately
primed process that must have the state re-explained to it. This is the
architecture the paper's own numbers argue for on tasks above the
saturation threshold: solo execution + verification, not multi-agent
coordination.

(Dev-layer note, outside maro's runtime: the adversarial-review skill's
parallel seats are independent-MAS-shaped — the 17.2× regime — but two
properties convert them: review is decomposable (the +80.8% regime; seats
don't build on each other's outputs, so errors don't cascade), and every
finding is probe-verified before landing, which is the verification
bottleneck applied per-claim. The observed hit rate across r15–r24 is
consistent with that reading.)

## 4. The guard the backlog asked for

"If the pattern reproduces, add a guard against reflexive fan-out where a
solo agent already scores high." maro has no reflexive fan-out to guard
today, so the guard is **doctrine recorded where the fan-out decisions will
be made**, not a code hook policing a path that doesn't exist:

1. **Concurrent milestone-area agents (BACKLOG, Jeremy 2026-08-16):** this
   paper is the evidence that entry's "honest open question" was waiting
   on. Concurrent area probes are predicted to pay only where (a) the
   areas are genuinely independent — the decomposable regime — or (b) the
   solo baseline is weak (the run is stuck/failing, the sub-45% shape).
   Where solo already succeeds, fan-out is predicted net-negative with
   super-linear turn overhead. That maps exactly onto the entry's own
   pathfinding-vs-build-out distinction: cheap independent probes on weak
   baselines = the paper's winning case; concurrent build-out of dependent
   areas = its −70% sequential-planning case. Any build must A/B against
   the sequential baseline before earning a default (same gate discipline
   as knowledge.edge_expansion).
2. **Director worker-review (BACKLOG 2026-08-19):** the ungated review tax
   sits on a *dormant* path — urgency down, stakes unchanged. When the
   bypass A/B runs, the paper adds the architecture-level prior:
   centralized review is the right *shape* (best error containment) and
   the cost to beat is its 3.9× efficiency penalty.
3. **Lead #3 (automated topology search) stays sequenced behind this and
   drops in priority:** the paper measured the space a topology search
   would explore and found it mostly negative for SWE-shaped work above
   the threshold. Hand-designed solo+verify is already at the empirical
   optimum for our task class; a search earns tokens only if we enter the
   sub-threshold or decomposable regimes.

Not a blanket ban: name the regime. Decomposable-with-cheap-merge fan-out
(multi-modal search sweeps, per-finding verification seats) is the case the
paper's +80.8% column supports.

## 5. Topology rationale — the record 751e2dea found missing

The prior run's finding was "no documented rationale for the
single-Director + sequential-Worker topology." This document is that
record: **maro runs solo-with-verification because (a) its work is
SWE-shaped and above the capability-saturation threshold, where every
measured multi-agent variant underperforms solo; (b) its verification lane
buys centralized-MAS error containment at ~12% overhead instead of 285%;
and (c) its orchestration machinery exists for cross-context persistence,
which concurrent fan-out does not address.** Concurrency remains available
behind evidence gates (item 1 above), not defaults.
