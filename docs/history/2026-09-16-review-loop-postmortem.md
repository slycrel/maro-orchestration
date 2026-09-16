---
status: record
date: 2026-09-16
title: Review-loop postmortem — 31 rounds on one chunk
---

# Review-loop postmortem: 31 rounds on one chunk (2026-09-16)

**Status:** history / postmortem. Written the day the loop stopped. Jeremy's
verdict that prompted it: *"30 rounds is way way too much; 2-3 should be the
norm, 6-7 should be rare. 31 is just we're doing it wrong... I'm not running
into this elsewhere, so we are probably 'holding it wrong'."*

## What happened

| Chunk | Rounds | Landed through | Shape |
|---|---|---|---|
| Container auth / notify finish line | 22 | 4b3706f0 | r12–18 the same acknowledgment rule found at one more sender per round |
| Item 2, landscape project binding (1dc74714) | 31 | e6999d78 | r27–31 every HIGH a failure-path twin of the previous round's fix |

Two consecutive chunks, 53 rounds. Each round cost roughly 1.5–2 hours of
wall clock (two reviewers ~30 min, codex fix ~30 min, mutation + targets +
full suite ~15 min, land + CI ~10 min). The flip decree at r17 (codex writes,
Claude reviews) changed who wrote the fixes; the round count kept climbing.
So the fixer was not the generator.

Item 2 stopped under an orchestrator stop rule, not at a fixpoint: round 31
still filed six distinct HIGHs, seven cheap ones were fixed and the two
design-class ones were queued.

## Why (root causes, in order of weight)

1. **The stop decision belonged to the adversary.** The rule was "a round with
   no HIGH is the fixpoint." A gpt-5.6-sol/high reviewer with shell probes can
   construct a failure scenario for any fresh code in a codebase whose
   convention is best-effort writes (stamps that return None instead of
   raising, two stores with two locks). Every one of those scenarios is
   *true*; the ledger marked them VERIFIED; the rule then obliged a round. A
   criterion that an unbounded adversary decides is not reachable.
2. **Scope grew every round.** Each round reviewed the *whole chunk*: 31
   commits, a 600 KB prompt, and a ledger rule that any file touched by a
   fix commit is in range. Fixes in operator_ask, run_curation, file_lock,
   memory_ledger, notify and config were all "item 2" by round 25. The
   reviewer was reviewing the accumulated fix layer, not the feature.
3. **The prompt asked for it.** Watch-list item 1 says the fixes are "the
   likeliest home of this round's HIGH — attack them first", and the
   recorded-direction block grew to ~20 KB of "do not re-file unless you
   show a NEW consequence." That is an invitation to find the adjacent
   variant, and the reviewers accepted it every round.
4. **Instances were fixed where a class was found.** "Stamp result
   unchecked" is one class with dozens of members; it was fixed one site per
   round (handle's pause in r30, three sibling producers filed in r31). The
   right moves were a checked primitive, or recording the class as accepted
   direction, in the round it first appeared.
5. **No orchestrator triage.** Verify-before-fix asks "is the claim true?"
   It never asked "how likely, how bad, is this worth a round?" A
   sub-millisecond two-process window, a naive timestamp on a field the
   system always writes aware, ENOSPC between two writes — all true, none
   worth a two-hour round. A human team calls these known residuals and
   ships.
6. **The signal at r17 was read as license.** Jeremy said 17 was rough and
   "I'm ok continuing to finish what we've started." I proposed a ~6-round
   budget, did not apply it, and ran 14 more rounds. That was my call and
   the wrong one; a stop rule set at r30 was 20 rounds late.

What a normal PR review has that this loop lacked: a bounded diff, severity
decided by the team, "ship with known residuals" as the default, and a
re-review that reads only the delta.

## The protocol from here (proposed, pending Jeremy's ratification)

- **Budget:** 2–3 rounds per CODE chunk. A fourth needs a named reason
  written in the verdict; a fifth needs Jeremy.
- **Round 1** reviews the chunk diff (two reviewers, size-based). Every
  verified finding is *triaged*, not queued: **fix** (likely and
  consequential), **pin** (real but unlikely → a known-gap pin test and a
  BACKLOG line, no round), **class** (a convention-level finding → its own
  item, fixed as a primitive or recorded as direction), **refute**.
- **Round 2** reads the *fix diff only* (one reviewer), with the chunk's
  intent and the round-1 triage as context. No "attack the fixes" framing,
  no recorded-direction lists beyond ten lines. Its findings get the same
  triage. Ship.
- **Round 3** only when round 2 found a likely-and-consequential HIGH *in
  the fix itself* (a regression). Otherwise stop.
- **Cost line** in every verdict: wall clock for the round so far, so the
  spend is visible where the decision is made.

## Pointers

- BACKLOG: "The review loop itself: 31 rounds on item 2" (process + residue).
- Item 2 round notes: BACKLOG landscape bullet, r26–r31 notes.
- Prompts and verdicts for r28–r31 in the session scratchpad
  (`build_item2_round{28..31}.py`, `r{28..31}_verdict.md`).
- Adversarial-review skill: `~/.claude/skills/adversarial-review/` — the
  watch-list wording (item 1) and the fixpoint rule are what this changes.
