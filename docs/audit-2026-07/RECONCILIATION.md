# Purgatorio reconciliation — INTERIM (eyes 1–5)

**Status:** interim, written 2026-07-09 after eyes 1 (ops census), 2
(data/learning-store), 3 (backward archaeology), 4 (docs coherence),
5 (code-vs-spec + security). Eye 6 (external landscape) is running; eye
7 (forward historian) not yet launched. Final re-triage of the 1.0 list
happens after all seven. Per docs/PURGATORIO_AUDIT.md: overlap is a
feature — a gap found independently by multiple eyes is almost certainly
real.

Raw findings: findings-ops.md (15), findings-data.md (13),
findings-archaeology.md (11), findings-docs.md (11),
findings-code-security.md (7) — 57 findings, plus 42 explicit clean
checks.

---

## Super-findings (merged, triangulated)

### SF-1 — The self-improvement engine has zero production hours, and the record pretends otherwise
**Members:** ops-01, ops-02, ops-03, ops-05, ops-08, arch-04, arch-05,
arch-06, docs-03, docs-06, data-09. **Severity: blocker-for-1.0** (1.0
explicitly claims "self-learning in the build-out").

The strongest triangulation in the audit — four eyes hit it
independently. The heartbeat has no scheduler and has never beaten under
the Maro name (ops-01); the evolver has never run (ops-02, three
independent probes); 243 accreted skills are all provisional with
essentially zero applications (data-09). On top of the dead engine, the
*record* rots in layers: ratified decisions ride the dead vehicle
(refight_rule structurally unreachable, arch-04; nightly eval's only
caller is the dead heartbeat, yet was cited to close a BACKLOG item,
arch-05), GOAL_BRAIN asserts an in-process heartbeat fallback that does
not exist (arch-06), README sells self-improvement as ambient default
behavior (docs-06), and the shipped bootstrap generates a heartbeat unit
that **cannot start** — `sheriff.py --heartbeat` is not a flag; exit 2,
30s crash loop forever (docs-03), with a second, contradictory unit
definition in deploy/.

**Resolution shape (one arc, not eleven fixes):** (1) pick ONE
supervision story — the openclaw-gateway user-unit pattern ops-15
documents is the local proof — and delete the losing unit definition;
(2) fix bootstrap's ExecStart; (3) burn in evolver production hours
before any 1.0 self-learning claim; (4) reword README's two ambient
passages to "when the heartbeat loop is enabled (opt-in)"; (5)
GOAL_BRAIN corrections for arch-05/06. Enabling the heartbeat/evolver on
this box is **Jeremy's call** (off switches stay off).
*Partial movement this session:* the (f) dogfood runs are the first
deliberate learning-ON production hours the system has ever had.

### SF-2 — The learning layer is verdict-blind: done≠achieved never reached the stores
**Members:** data-02, data-07, data-08, + live dogfood specimen.
**Severity: blocker-for-1.0.**

Not one of 1381 outcomes rows carries `goal_achieved`; verdicts live
only in run metadata, which no learning consumer reads. The evolver,
inspector, lesson extractor, and recall's repeat-guard all equate
`status=="done"` with success — on Jeremy's own organic evidence (4/5
done, 1 achieved) every success-rate the learning loops compute is
inflated, and lessons are extracted from goal-failed runs as if they
succeeded. Corollaries: 5 hermes-trial imports sit unverdicted
(data-07), 12 pre-fix June `True` verdicts are indistinguishable from
post-fix ones (data-08). Live specimen from dogfood run 1
(6dfaec5d-keen-alder): Maro produced a *correct, evidence-quoted*
diagnosis and a good skill, and closure judged it `goal_achieved=false
@0.25` because the verifier could not reproduce privileged journalctl
output — the harsh-on-build-goals face of the same coin: verdicts are
both absent where needed and noisy where present.

**Resolution shape:** plumb tri-state `goal_achieved` (absent=unjudged)
into record_outcome/reflect_and_record; teach evolver/inspector/recall
to prefer it; stamp verdict provenance (judged_at / judge version) going
forward; annotate the 12 pre-fix rows. This IS 1.0 item (b)'s store-side
half.

### SF-3 — Test junk was live in production learning paths (purged; residuals remain)
**Members:** data-01 (**resolved this session**), data-06, data-13.
**Severity: was blocker; residuals real-but-deferrable.**

The purge executed before the (f) learning-ON runs: long tier 26→4,
top-level lessons 290→188, module store rebuilt 414→308, 0 junk
remaining, backup at `~/.maro/workspace/memory/backup-2026-07-09-data01/`.
Open residuals: provenance-era guard on bridge ingest (so pre-isolation
junk can't recur), import provenance stamping (data-06 — rides 1.0 item
(g), where provenance is a hard prerequisite for sharing learning), and
the cosmetic litter list (data-13).

### SF-4 — The scope/Phase 65 story: the experiment ran, the winning arm lost the config war, the docs say "dormant"
**Members:** arch-01, arch-02, arch-03. **Severity:
real-but-deferrable, but must be adjudicated before 1.0 flag defaults
are finalized.** **Jeremy decision.**

The box has run scope-generation live since ~April in the A/B-*control*
configuration (`scope_generation: true` + `scope_ab_skip: true`): every
AGENDA run pays the scope LLM call, the scope is never injected, yet
ResolvedIntent flows into closure verification — while DEFAULTS.md and
CLAUDE.md call the feature dormant/paused. The one adjudicated
experiment (2026-04-22) says **inject** wins (plan compression 8 vs
15–40 steps); two further paid A/B datasets (04-25, 04-26) were never
even read. And the 2026-07-09 "accept ResolvedIntent v0 on organic
evidence" decision rests on evidence generated under this misdescribed
config.

**Decision needed:** pick the flag posture deliberately (inject / off /
keep paying for record-only), adjudicate or write off the two orphan
experiments, correct DEFAULTS/CLAUDE/GOAL_BRAIN to match reality.

### SF-5 — `user/` ships Jeremy's personal data and is an invisible config lane
**Members:** docs-02, docs-04, docs-05. **Severity: blocker-for-1.0
(privacy).**

`user/GOALS.md`/`CONTEXT.md`/`SIGNALS.md` are git-tracked with identity
and medical details, and planner.py injects 500 chars of each into
**every decompose prompt** resolved from the install dir — a stranger's
clone plans their goals with Jeremy's health context in the prompt.
Simultaneously `user/CONFIG.md` is a load-bearing config lane (`yolo`,
model tiers, MCP servers) that DEFAULTS.md's census structurally cannot
see and README never mentions. One "what is user/ at 1.0" decision
resolves the whole cluster: neutral templates in the repo, Jeremy's real
files move to a private overlay (workspace), the lane gets documented
or folded into YAML config.

### SF-6 — The security posture strangers are told about does not exist; the real one has named gaps
**Members:** docs-01, docs-08, docs-09, cs-04, cs-01, cs-02, cs-03,
cs-05; ops-13 folds in here. **Severity: blocker-for-1.0** (cs-04 +
docs-01), with real-but-deferrable satellites.

Triangulated by three eyes independently (docs, ops, code). The live
executor runs `claude -p --dangerously-skip-permissions` with full tool
access and **no sandbox** (cs-04; `sandbox.py` has zero runtime
callers) — while the living SECURITY_MODEL.md tells a stranger "every
skill runs sandboxed" and documents an env var (`POE_ENV_FILE`) no code
reads. Eye 5's threat model of the *actual* stack: the write fence is
diagnostic-only with a known relative-`..` blind spot (cs-01); secret
scrubbing misses Telegram-token / high-entropy / JSON-serialized
classes on sinks that feed shareable artifacts (cs-02); closure
verification runs LLM-authored shell with `shell=True`, no allowlist,
with untrusted worker output in the prompt — a real indirect
prompt-injection → shell path (cs-03); the worker push guard is an
accident-guard, trivially unset by the guarded party (cs-05); and three
services listen on 0.0.0.0 on a box with money attached (ops-13).

**What held (don't re-litigate):** done≠achieved verdict flow is clean
(fail-open null verdict excluded, worker "done" can never self-award
`goal_achieved`); viz_server traversal defense solid; only
`yaml.safe_load` in the codebase; injection_guard's source allowlist
resists keyword-stuffing.

**Resolution shape:** (1) rewrite SECURITY_MODEL.md to the verified
actual stack (cwd-bind + constraint gates + injection_guard + scavenge
diagnostics + budget caps), label sandbox "built, unwired", fix
POE_ENV_FILE→MARO_ENV_FILE, sweep rename ghosts — eye 5's evidence
makes this writable now; (2) **Jeremy decision:** the 1.0 isolation
story (container / user-namespace / opt-in sandbox wiring / honest
"trusted-operator-only" framing); (3) backlog the satellites (fence
enforcement-or-containment, scrub patterns, closure-command
restriction, push-guard framing).

### SF-7 — Lying-state litter: dead lanes that look alive, silent fallbacks to stale data
**Members:** ops-04, ops-06, ops-07, ops-12, ops-14, data-10, docs-10,
docs-11. **Severity: real-but-deferrable, one credential decision
urgent-ish.**

slack-bridge is dead in every dimension yet holds live-looking Slack
tokens in `.env` (ops-04 — revive-or-remove, and revoke tokens if
remove); telegram_listener looks alive on disk but froze in April;
eight modules silently fall back to the repo-local stale `memory/`
copies instead of erroring (data-10 — the ghost-index pattern waiting
to recur), and README's benchmarking examples actually read that stale
copy (docs-11); assorted dead pidfiles, a decorative tmux-claude unit,
and the hermes trial containers set to auto-resurrect
(`unless-stopped`) — the latter must be `docker rm`'d when the trial
chunk ends per the good-system-citizen rule.

### SF-8 — The knowledge "web" is write-only and injects known fabrications
**Members:** data-05, arch-07, arch-08, arch-09. **Severity:
real-but-deferrable (docs descope may be needed for 1.0 honesty).**

All 2124 edges date to the April import; the read side
(`load_knowledge_edges`) has zero external callers — the accepted
memory direction's Phase 2 shipped only its BM25 half. Two lat.md nodes
flagged as fabricated on 2026-07-04 (citing nonexistent files) are
still actively injected into planner/director prompts (arch-07 —
cheapest fix in the audit: delete/correct 2 nodes).
`record_rule_wrong_answer` remains unwired (arch-09). Either wire the
read side or descope "knowledge web" claims in 1.0 docs to "node store
with BM25 retrieval".

### SF-9 — May 2026 is a month-sized hole in the learning record
**Members:** data-03 (contradicts ops-10's "hiatus" spend reading).
**Severity: real-but-deferrable.**

476 May run dirs and 965 captains-log events exist, but outcomes,
step-costs, and daily logs have zero May rows — activity happened,
recording didn't. Investigate the May-era wiring break enough to trust
that the *current* recording path doesn't share it, and document the
hole so no analysis treats May as "no activity".

### SF-10 — The lesson-promotion funnel has never promoted
**Members:** data-04 (mechanism), data-09 (skills twin — also in SF-1).
**Severity: real-but-deferrable.**

10/10 consolidation cycles: 279 decayed, 95 gc'd, 0 promoted; the only
promotions ever (4) came from the reinforcement-time hook. Decay
economics guarantee gc-before-promote for anything not re-hit within
days. Measure the desired funnel rate before tuning — and note the (f)
dogfood crystallization audit is the first data point on what the
funnel does under deliberate use.

### SF-11 — Verified good (record these, they're signal too)
ops-10 (no silent token burner; ≤$2.79/day; $229.38 all-time), ops-11
(git hooks healthy post-fix, tripwired), ops-15 (openclaw-gateway is
the supervision pattern to copy), data-12 (module store
sqlite==JSONL exact; dev-recall docs lane fresh; medium tier clean
post-Jun-23), 24 docs clean checks (quickstart commands, backend order,
budget defaults, DEFAULTS spot-checks all hold), 13 archaeology clean
checks (checkpoint wiring real, no-daemons consolidation holds, merged
branches really merged).

### Unmerged singles
- ops-09 — 63% of step-cost rows have empty `model`; blinds the next
  burner hunt. Backlog (populate at record time).
- ops-13 — three services listen on 0.0.0.0 on a box running an agent
  with money attached (novnc :6080, nginx :8088, vnc :5900). Folded
  into SF-6.
- cs-06 — `router.py` pickle.load of a workspace file: latent code-exec
  gadget, currently unreachable via import whitelist. Must be resolved
  (JSON/joblib-safe) before item (g) bundle-import ships.
- cs-07 — env>config precedence is per-call-site discipline, not
  centralized; corroborates docs-05. Low backlog (helper function).
- arch-10 — origin/factory branch (5 commits, benchmark results) is
  work finished, pushed, and absent from the record. Adjudicate:
  merge learnings / archive / delete.
- arch-11 — GOAL_BRAIN (wins-by-decree doc) is stale on thread-brain
  maintenance vs MILESTONES. Goal-brain correction.
- docs-07 — arch-platform.md documents the wrong backend failover
  order (the mandatory pre-read for platform work). Fixed-inline.
- data-11 — dev-recall session-transcript lane 84 days stale; **must
  re-ingest before eye 7** (chat-history mining reads that index).
- Dogfood specimen (this session, run 6dfaec5d): persona router sent a
  skill-building meta-goal to health-researcher @0.892 because the
  goal *text* contained "diagnosis/health" — keyword routing has no
  meta-goal awareness. Feeds the (f) writeup.

---

## Interim 1.0-blocker list (re-triage pending eyes 5–7)

| # | blocker | members | state |
|---|---|---|---|
| 1 | Self-improvement claims vs zero production hours + broken supervision story | SF-1 | open; supervision decision = Jeremy |
| 2 | Verdict-blind learning stores | SF-2 | open; = 1.0 item (b) store-side |
| 3 | `user/` personal data shipped + injected; invisible config lane | SF-5 | open; privacy review before any tag |
| 4 | Security: no-sandbox live path + SECURITY_MODEL.md misrepresentation | SF-6 (cs-04, docs-01) | open; doc rewrite now writable; isolation story = Jeremy |
| 5 | Test junk live in learning paths | SF-3 | **resolved 2026-07-09** (purge + rebuild); ingest guard residual |
| 6 | Heartbeat unit from bootstrap crash-loops on clean install | in SF-1 | open; fixed-inline candidate |

Adjudicate-before-1.0 (not blockers, but flag-default-shaping): SF-4
(scope posture), SF-7's slack-bridge credential decision, SF-8's
docs-descope-or-wire choice.

---

## Eyes 6–7 (to be appended)

- Eye 6 (external landscape): running. Positions 1.0 scope against the
  mid-2026 framework field.
- Eye 7 (forward historian): **precondition — run dev-recall session
  ingest first (data-11).**
- Final pass: re-triage the blocker list, fold in eye 5/6/7 findings,
  produce the consolidated decisions-for-Jeremy list.
