---
name: adversary-trio
description: Pre-work triage/adjudication panel — three opposite-model (codex exec) adversaries with FORCED-OPPOSED dispositions adjudicate work items into DO_NOW / PARTIAL / DEFER before building. Distinct from adversarial-review (post-land code review of diffs); this one judges plans and scope, not code.
---

# adversary-trio — work-item triage panel

Run settled-out work items (or one contested decision) through three
adversaries on the **opposite model family** before committing effort.
The deliverable is a per-item disposition table plus the crux of any
disagreement — NOT implementation.

**Hard constraint** (same as adversarial-review): seats run via
`codex exec`. Never internal subagents / the Agent tool — those share
your model *and your priors*, which defeats both decorrelations.

## Calibration — read before every run (learned 2026-07-29)

The first live trio ratified the operator's own conservative prior on
an item Jeremy had already effectively authorized, 8/8 unanimous.
Retrospective verdict, now standing law:

1. **Adversary panels decorrelate arguments, not dispositions.** Three
   seats seeded with the same prior produce the same verdict with
   better prose. The fix is structural: each seat is ASSIGNED an
   opposed disposition (below). Agreement across opposed seats is
   signal; agreement from shared caution is noise wearing a robe.
2. **Unanimity is a suspicion trigger, not a confidence score.** On any
   unanimous verdict, check what prior your prompt seeded before
   trusting it.
3. **Never use the trio to infer Jeremy's intent or permission.**
   Intent resolves from his words + GOAL_BRAIN decrees, and broad
   directives extend to ACTIVE-class work (decision b3b10377). A
   permission question has no tree to refute — a panel just launders
   deference into something that looks adjudicated. Trio the *work*
   (scope, order, reversibility), never the *man*.

## What it's FOR (and not)

- FOR: triaging a batch of open items in autonomous mode; scoping a
  chunk into its reversible core; ordering settled work; adjudicating
  a genuinely contested design tradeoff with a checkable tree.
- NOT for: reviewing shipped diffs (use adversarial-review), permission
  or intent questions (see calibration #3), decisions Jeremy already
  made (execute them).

Its best observed output is **PARTIAL scope-narrowing**: converting
"should we?" into "which half is reversible?" (verify-only,
repo-visible-only, no-promote-path — first run, 2026-07-29).

## The three seats (forced-opposed dispositions)

| Seat | Assigned disposition | Argues |
|------|---------------------|--------|
| advocate | maximal | Do the most, now — cost of delay, compounding value, what waiting actually buys (usually nothing) |
| skeptic | minimal | Defer or shrink — concrete failure scenarios, irreversibility, what we don't yet know |
| scoper | reversible core | The smallest slice that ships real value and can be backed out; names the cut line explicitly |

Each seat must argue its assigned disposition's best case per item,
**and explicitly concede items where its disposition loses** — a seat
that never concedes is costume, and its verdicts get discounted at
synthesis.

## Mechanics

```sh
TRIO_DIR=$(mktemp -d /tmp/adversary-trio.XXXXXX)
codex exec --skip-git-repo-check -o "$TRIO_DIR/advocate.md" "prompt" 2>/dev/null
```

- One `codex exec` per seat, output files named after seats. Prompts
  >128KB go via stdin. Read-only default (`--profile edit` only if a
  seat must run probes).
- Background + monitor (block, timeout 600s). Network-bound, so
  parallel seats are fine; drop to sequential if the box is loaded.
- Before synthesis: `ls "$TRIO_DIR"/*.md` — a missing/empty seat is
  reported in the verdict, never silently skipped.

### Seat prompt contains

1. The item list — each with a one-paragraph description, its current
   state in the tree (paths), and any relevant decree quoted verbatim.
2. The seat's assigned disposition + the concession requirement.
3. Instructions: per item, output `DO_NOW | PARTIAL(<named core>) |
   DEFER(<the specific decision only the owner can make>)`, the
   strongest argument for it, and any tree-checkable claim marked
   `settled_by_command:` so synthesis can probe it.

## Synthesis (yours, not theirs)

Per item:
- **Opposed seats converge** → strong verdict, adopt it.
- **Seats split** → the split is the finding. Name the crux (usually
  one factual question or one value judgment), settle the factual ones
  against the tree yourself, and only then pick — or carry the value
  judgment to Jeremy as a sharp question, not a vibe.
- **Spot-verify every tree claim before acting on it** — 30-78% of
  unverified cross-model reviewer claims are wrong (five independent
  measurements).
- DEFER verdicts must name the specific decision and what evidence
  would flip it. "Needs input" without a named decision is a reject —
  send it back to the seat's text and extract the real blocker.

Output: a triage table (item / verdict / one-line why / crux-if-split)
into the chunk record or DEV_LOG per house style. Decree-class
outcomes → SF-13 (GOAL_BRAIN Decisions line + runtime
`knowledge_lens decision` pipe).

## Adjudication (pre-registered — the test that can fail)

- **Keep signal**: within ~5 runs, at least one verdict materially
  changed scope or order versus what you'd have done solo (the
  first run's verify-only / no-promote-path narrowings qualify).
- **Kill signal**: three consecutive runs where the trio ratifies your
  solo prior with no scope change — that's the decoration failure mode
  this skill was rebuilt to avoid, and if forced-opposed seats don't
  fix it, the mechanism doesn't work.
- Adjudicate after 5 runs; verdict → GOAL_BRAIN Decisions. No silent
  half-death (playbook / Stage-5 / factory lineage rule).
