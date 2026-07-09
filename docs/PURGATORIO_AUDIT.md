---
status: living
---

# The Purgatorio Pass — pre-1.0 retrospective audit

**Decreed by Jeremy 2026-07-09** (GOAL_BRAIN Decisions, same date): before the
"yay, we're almost to 1.0" road, a multi-eye audit of what we're doing now,
what our goals have been, and **what might be missing or neglected that is
assumed to be working**. His gut, on the record: "there's a ton of work ahead
and also (potentially) a ton of work littered behind us, some of which my gut
says should not be left behind."

Named for the obvious reason: Maro is Virgil, and this is seven-ish purging
passes on the way up the mountain before paradise (1.0). The audit **gates
declaring the 1.0 list complete** — findings will reshape MILESTONES -3
(a)–(h) — but does NOT freeze the queue: eyes are read-only, and in-flight
work (planning-depth shadow, BACKLOG #18, 1.0 remainders) continues. Only
*new arcs* wait for reconciliation.

**Evidence this is needed** (all tripped over, none found systematically):
git hooks silently dead for 7 weeks; heartbeat last beat 96 days ago; evolver
has never run in production; sandbox hardens a stub; pip packaging had never
worked; dev-recall indexed a ghost clone for 7 weeks.

---

## The eyes

Sequenced by yield-per-token, cheapest/warmest first. Each eye is a bounded
chunk with a timebox, not a synchronized mega-project (the audit must not
become its own boiled ocean). An eye may spawn sub-passes when a vein is rich
— the recursion decree applies to us too.

1. **Ops census — "what actually runs?"** Every cron, systemd unit, hook,
   listener, heartbeat, background loop: enabled? last actually fired? does
   anything verify it's alive? The gap between *built* and *operating* is
   where "assumed to be working" lives (dead-hooks bug is the archetype).
   Include token-economics/spend review (already instrumented; no separate
   cost eye).
2. **Data/learning-store health.** 5-6 months of runs, lessons, outcomes,
   indexes — some known-suspect (pre-fix goal_achieved poisoning, ghost
   index, MEDIUM-store gc). Now that learning is 1.0 scope, the data is
   product: which stores are healthy, which rotted, is anything the learning
   loops consume quietly lying to them?
3. **Backward archaeologist.** From now, walk backwards: work completed but
   lost in translation, decisions inferred-but-never-ratified, branches
   dropped and never finished.
4. **Docs coherence.** Ignore history; read the documentation corpus as a
   whole. Are the visions coherent, complete, on track? Clarify and bridge
   gaps.
5. **Code-vs-spec hardening + security threat model** (one combined read of
   the code). Review implementation against current specs; PLUS a real
   threat model, not a lint run — this system executes LLM-directed shell
   commands unattended with money attached, and 1.0 means strangers install
   it (sandbox-stub finding is exhibit A). Config/conventions
   standardization rides this read: generalize the DEFAULTS.md+census
   pattern to CLI conventions, error surfaces, logging, naming ghosts
   (e.g. OPENCLAW_WORKSPACE still in test env vars).
6. **External landscape re-verification.** Not "survey the internet" —
   *re-verify our choices against a world that moved*. (a) Re-audit the
   link-farm with fresh eyes (aged well / superseded / never digested);
   (b) de-bubble sweep: multi-modal searches deliberately outside the
   X-algorithm/Jeremy-discretion bubble (academic, other agent ecosystems,
   model/SDK layer changes). Verdict discipline per finding:
   **steal / ignore / watch**, each naming the Maro subsystem it touches —
   otherwise it's just a second link farm. Steal-from-don't-migrate stands.
7. **Forward historian.** NOT re-deriving history from commits: BACKLOG_DONE,
   ROADMAP_ARCHIVE, docs/history/, GOAL_BRAIN Decisions already ARE the
   timeline. The job is **diffing the compiled record against raw commits +
   chat/session history** (starting where Jeremy moved this work from
   OpenClaw to Claude) to find asks, decisions, and intent the record
   missed. Chat-history mining is the genuinely new material. Heaviest eye;
   runs last.

**Cross-cutting discipline (every eye):** distinguish *claimed* from
*probed*. "Verified" claims must name what was actually tested (the
pip-packaging lesson). Verify-before-fix applies to the audit's own output —
historically ~30-50% of adversarial findings hallucinate.

**Dogfood split:** bounded, verifiable eyes (or slices — link-farm re-audit,
parts of the ops census) run through Maro itself as missions, serving 1.0
item (f). Judgment-heavy corpus work (historian, docs coherence,
reconciliation) stays in Claude Code sessions where full context lives.

**Effort split:** high reasoning effort goes to verify/reconcile/adjudicate
stages (where hallucinated findings get caught); extraction/enumeration
sweeps run at medium — structure does the work there, not depth.

---

## Findings format

One file per eye in `docs/audit-2026-07/` (findings-<eye>.md, table or
JSONL). The directory is **disposable scaffolding** — verified findings
graduate into BACKLOG/GOAL_BRAIN through normal machinery; nothing cites the
audit dir as canonical afterward.

Fields per finding:

| field | meaning |
|---|---|
| id | eye-prefixed sequential (ops-01, arch-03…) |
| claim | one-sentence defect/gap statement |
| evidence | file:line / commit / log pointer — no pointer, no finding |
| subsystem | which of the 5 subsystems (or docs/ops/data) it touches |
| severity | blocker-for-1.0 / real-but-deferrable / cosmetic |
| status | unverified → confirmed / refuted / hallucinated |
| disposition | backlog-item / goal-brain-correction / fixed-inline / discard |

**Reconciliation** after every 2-3 eyes (not one big-bang at the end): merge
overlapping findings — overlap is a feature; a gap found independently by
multiple eyes is almost certainly real (triangulation = free confidence).
Final reconciliation re-triages the 1.0 list. The whole audit is the
adversarial-review pattern at project scale (independent finders →
adversarial verify → reconcile) — deliberately so: it doubles as the worked
example for the adversarial-review skill in the 1.0 ship set.

---

## Explicitly discarded

- Separate cost/perf eye (folded into ops census).
- Newcomer-UX eye (lives in the 1.0 README/quickstart item).
- Freezing development during the audit.
- Internet hive-mind anything (see 1.0 item (g): opt-in someday).
