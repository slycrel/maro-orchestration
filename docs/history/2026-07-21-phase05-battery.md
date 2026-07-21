---
status: record
---

# Phase 0.5 battery — DEV_PATTERNS with-doc vs control over real ground truth

**Date:** 2026-07-21. **Adjudicator:** Claude (session 438e91bd), per the
pre-registered protocol below. **Run:** workflow `wf_c05ff0b1-5d9`, six serial
Fable agents (box OOM rule), ~44 min, 596,954 subagent tokens, 297 tool calls,
0 errors. **Raw outputs:** `phase05-battery/outputs.json` (all six arms'
findings + plans, verbatim). **Arms/prompts/schema:**
`phase05-battery/workflow-script.js`.

## Verdict (gate applied as pre-registered)

**AMBIGUOUS → DEV_PATTERNS ships as a NON-GATED pre-read** (CLAUDE.md line
added in this commit), stated plainly:

- **(a) Catches: no delta.** Both arms caught every ground truth their task
  pointed at, with code-derived provenance. The battery is NOT evidence the
  doc helps; it is evidence that pointed Fable-tier review finds these
  violation classes with or without it.
- **(b) Plan shape: structurally unable to discriminate.** The
  StructuredOutput schema *required* `done_means[]` / `consumers_named[]` /
  `tripwires_named[]` / `cuts_or_inversion` from every arm — the schema
  itself was the instruction the doc was supposed to provide. A
  pre-registration flaw, discovered at adjudication and confessed here
  rather than laundered.

The doc ships anyway because its cost is ≈zero and the doc-arm agents
demonstrably read and applied it (T3-doc cites `docs/DEV_PATTERNS.md`
twice: possible-now bias in its cuts, and as corroboration in finding 9).
What the battery did NOT show is a measurable delta over strong-agent
baseline behavior on pointed tasks.

## Scoring (a) — catches, provenance-scored

Ground truth (real violations at HEAD b3cc88b; see protocol):

| GT | Target arm | Control | Doc |
|---|---|---|---|
| GT1 decisions.jsonl: live reader, zero writers | T1 | ✓ code (+ empirical `ls` of workspace) | ✓ code |
| GT2 contradict_pattern zero callers → contest/refight lane dead | T2 | ✓ code | ✓ both |
| GT3 playbook 800-char head-window starvation | T3 | ✓ code + executed (780-char live block, zero learned entries) | ✓ code + executed (+ deeper: seed body alone is 955 chars > 800, so even fresh installs starve) |
| GT4 mode:thin / factory unadjudicated | T2 (plausible) | ✗ | ✗ |

Cross-catches: GT1 was independently re-caught by **both** T2 arms (cites
commit a278575's "half-wired, not dead" deferral). GT4 was reachable only by
a stretch from the T2 framing; neither arm went there — scored as designed
("plausibly"), no delta.

Contamination check: the violations are documented in-repo. Provenance
scoring shows the catches were **code-led** — every GT catch cites src/
file:line evidence and several attach executed checks; docs citations appear
as *secondary* corroboration (REFACTOR_PLAN, GOAL_BRAIN) after the code
evidence. The docs-contamination worry did not materialize as docs-derived
catching.

## Scoring (b) — plan shape (invalid, counts reported anyway)

| Arm | done_means | consumers | tripwires | cuts/inversion |
|---|---|---|---|---|
| T1-control | 7 | 5 | 7 | yes |
| T1-doc | 6 | 4 | 5 | yes |
| T2-control | 5 | 10 | 7 | yes |
| T2-doc | 3 | 11 | 6 | yes |
| T3-control | 4 | 5 | 5 | yes |
| T3-doc | 5 | 4 | 5 | yes |

Control totals 16/20/19, doc totals 14/19/16 — control marginally *higher*.
Meaningless either way: the schema forced the properties (see Verdict). Kept
for the record because pre-registration said counts would be reported.

## Why no delta (interpretation, marked as such)

Ceiling effect. Each task prompt pointed straight at the violating
subsystem, and Fable-tier agents grep callers, read live stores, and execute
probes by default. The instrument measured discoverability of known-class
violations under pointed review (high), not the doc's marginal effect. A
battery that could show the doc's effect would need **unpointed** tasks
(prompts that don't name the subsystem), weaker agent tiers, or scoring of
omission classes. Not rerun now — the arc continues; redesign only if the
question becomes load-bearing again.

## Battery-design lessons (for the next instrument)

1. Schema-required fields cannot measure whether an agent *chooses* to
   produce those fields. Score free-form output, or make the fields optional
   and score presence.
2. Pointed tasks measure discoverability, not doc effect. Blind the task
   framing, not just the doc.
3. Real-instance ground truth + provenance scoring worked: catches were
   cleanly attributable to code evidence vs docs echo.
4. Cost of this instrument class: 6 Fable agents ≈ 600k tokens / 44 min.

## The real yield — new findings (verified by adjudicator before listing)

The battery's actual value was reconnaissance. Six new findings verified
against the tree (per the verify-before-fix rule; two verified with nuance):

| # | Finding | Evidence (verified) | Arc home |
|---|---|---|---|
| V1 | Recall's loop lesson substrate reads the LEGACY flat store, not the tiered store — decay/tiers/reinforcement have no effect on main-loop injection | `recall.py:592-597` imports `load_lessons` → `memory_ledger.py:903` (legacy `lessons.jsonl`); comment says "Tiered" | **Checkpoint-class flag for chunk 6** (novelty-term scoring would be invisible to the loop prompt as planned) |
| V2 | Standing-rule domain vocabulary mismatch: rules written with `domain=task_type` (all 4 live rules: `agenda`), sole live reader filters by project slug — rules invisible on project-scoped runs | `knowledge_web.py:336-341` writer; `recall.py:636-637` reader; live `standing_rules.jsonl` dump | Chunk 4 prerequisite (upstream of rules_cited stamping) |
| V3 | K4 bridge + link-farm import write knowledge nodes as `NODE_CANDIDATE`; no code path ever flips to `NODE_ACTIVE`; readers filter ACTIVE-only — every bridged node permanently invisible | `knowledge_bridge.py:265`, `knowledge_web.py:1669` writers; `knowledge_web.py:1357,1372` reader filter; no promote path in grep | BACKLOG (chunk 1 batch) — new half-closed-loop instance |
| V4 | Stage-3 dashboard accesses canon candidates as attributes (`c.content`) but the function returns dicts (key `lesson`) — degrades to an error dict (not a crash: wrapped in `except`) the day candidates exist; advertised `maro-memory canonize` command doesn't exist | `knowledge.py:65-72` vs `knowledge_web.py:1203-1214` | BACKLOG small-bug (chunk 1 batch) |
| V5 | `planner.decompose` persona prefix is discarded whenever any extras exist — line 891 rebuilds `system = DECOMPOSE_SYSTEM + extras`, dropping the persona wrap built at 761-772. Under the personas-stay decree this silently disables personas on every run with cuts/constraint extras | `planner.py:761-772` vs `planner.py:891-892` | Chunk 1 candidate fix (one-line; personas-stay decree makes it live-impacting) |
| V6 | Playbook seed body alone (955 chars) exceeds the 800-char budget — even a fresh install never injects Cost/Quality sections | executed: `len(seed[seed.find('## '):])` = 955; `recall.py:680` budget | Chunk 2 input (playbook repair) |

Double-sourced by independent arms but NOT adjudicator-verified (verify
before acting on any of these): canon promotion starved
(`inject_tiered_lessons`/`times_applied` never on live loop path — CLI
`--compare` only), verifier-calibration loop orphaned since a278575
(`record_verification`/`calibrated_alignment_threshold` zero callers,
outcomes file frozen at 2026-04), memory_bridge worker-slice trust frozen at
first ingest (raw score stored as trust, dedup skip prevents update),
medium→long promotion starved by its own constants (0.9 threshold vs
0.85/day decay), director never sees the playbook despite `playbook.py`
docstring, playbook Signals section has no surface at all (write-only
human-review pen), navigator.lesson_inject still unadjudicated (already
GOAL_BRAIN-watched), per-project DECISIONS.md is misnamed write-many/read-none
telemetry, four unrelated "decision" seams (naming-collision discoverability
debt), `_LOOP_ACTIONABLE_EVENTS` includes a phantom event no production code
emits.

Full details for every finding above: `phase05-battery/outputs.json`.

---

## Pre-registered protocol (verbatim, from scratchpad, written before any run)

```
# Phase 0.5 battery protocol — PRE-REGISTERED 2026-07-21 ~11:50 local, before any battery run

Kept in scratchpad (not repo) until runs complete, so control agents cannot read it.
Lands verbatim inside the battery report in docs/history/ afterward.

## Ground truth (real violations at HEAD b3cc88b — chunks 2-4 will destroy these)

- GT1: `decisions.jsonl` — read side live (recall substrate #3, knowledge_lens.py:882), ZERO writers ever.
- GT2: `contradict_pattern` (knowledge_lens.py:373) — zero callers; `refight_rule` (skill_lifecycle.py:684-694) unreachable.
- GT3: playbook injection-horizon bug (playbook.py:109-135) — 800-char head window; live file head = duplicate test-era lines; learned content never reaches a prompt.
- GT4: `mode:thin` / factory_minimal.py / factory_thin.py ride at HEAD unadjudicated (Phase 49 gate never fired).

## Arms

Six serial agents (box rule), fresh context each, same repo:
- T1-control / T1-doc — design task touching GT1
- T2-control / T2-doc — review task touching GT2+GT3 (+GT4 plausibly)
- T3-control / T3-doc — design task touching GT3
Doc arms differ ONLY by a preamble instructing to read docs/DEV_PATTERNS.md and apply both halves.

## Scoring (adjudicated by me after all six return; each catch re-verified before counting)

(a) CATCHES: which GTs surfaced unprompted, with provenance scored per catch:
    code-derived (cites src/ evidence) vs docs-derived (cites KNOWLEDGE_JOURNEY/GOAL_BRAIN/era files).
    Docs-derived catches are counted separately — the repo now documents the violations,
    so (a) may be contaminated; provenance keeps it honest.
(b) PLAN SHAPE: per output — consumers named for every store/emitter? done-means named
    before build steps? tripwire/test named per principle? cuts/inversion present?
    Scored as counts of the four properties present, blind-ish (I score before unblinding
    which arm is which in my notes... arms are identifiable by content; honesty over theater:
    scored with the rubric applied mechanically, both arms same pass).

## Gate (pre-registered, unchanged from the plan)

Clear delta in (a) code-derived catches or (b) plan-shape counts, doc-arm over control →
graduate DEV_PATTERNS to standing CLAUDE.md pre-read. Ambiguous/no delta → ship as
non-gated pre-read, stated plainly; no laundering noise as a verdict. Either way the
battery report lands in docs/history/ with raw outputs.

## Contamination acknowledgment

The violations are documented in-repo (era 12, KNOWLEDGE_JOURNEY, GOAL_BRAIN, plans).
Task prompts require src/ file:line evidence for behavior claims; agents may still read
docs. Provenance scoring (above) is the mitigation, and (b) plan shape is contamination-
resistant (the doc's taste half isn't derivable from knowing the violations).
```

*(One deviation from protocol discovered at adjudication: the "(b) plan
shape is contamination-resistant" claim was wrong for a different reason
than contamination — the output schema forced the shape. Recorded in the
Verdict section above.)*
