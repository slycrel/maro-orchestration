---
name: watch_condition
description: "Check a feed/site/value against a condition on each invocation, fire a notification only on the transition to true, stay silent otherwise — state persists across runs via the project workspace"
roles_allowed: [worker, short]
triggers: [watch this, tell me when, notify me if, alert me when, keep an eye on, let me know if it changes]
---

## Overview

Use this skill for "watch X and tell me when Y" goals. Maro has no daemon of
its own — `maro heartbeat` is one-shot and relies on the host's own
scheduler (cron, `/loop`, systemd timer) to re-invoke it (see
`skills/arch-platform.md` § Heartbeat). This skill is written for **one
invocation of a recurring check**, not a long-running loop: the goal must
be re-run on the same project/goal-slug on a cadence (via the host
scheduler or the harness's own `/loop`) so the persisted state below
actually accumulates across ticks. A single one-off run of this skill
performs exactly one check, not a watch.

## Steps

1. **Define the condition precisely up front** — the exact source (feed
   URL, site, value), the exact trigger condition (a threshold, a text
   match, a state change), and the check cadence. A vague condition means
   every future tick has to re-guess what "fired" means.
2. **Establish a persistent checkpoint** — before checking anything, read
   the last-known state for this watch (last checked value, last-fired
   timestamp) from the project's own workspace state, not from memory. If
   none exists, this is tick 1: there is no "before" to compare against, so
   don't fire on tick 1 just because the condition happens to already be
   true — record the baseline and wait for a transition.
3. **Fetch the current value.** Use whatever source access the goal
   implies (web fetch, file read, API poll). Distinguish "fetched
   successfully, condition false" from "couldn't fetch" — an unreachable
   source is not the same as a false reading and must not be silently
   folded into "nothing to report."
4. **Compare to the checkpoint.** Fire only on a transition into the
   condition being true (false→true or crossing a threshold), never on
   every tick the condition continues to hold — that's notification spam,
   the exact anti-pattern this skill exists to avoid.
5. **On fire: notify with the concrete evidence** — what changed, the
   current value, the source, and when. On no-fire: the successful
   completion mode is near-silent — a short status line, not a manufactured
   summary padded to look like work happened.
6. **Update the checkpoint every invocation, fired or not** — through an
   atomic write, not a read-modify-write with a gap, so a crash mid-check
   can't corrupt or lose the last-known-good baseline for the next tick.
7. **Report unreachable/blocked sources as their own state**, distinct from
   "checked, condition false" — a broken watch that silently reports
   "nothing to see" is worse than one that says "couldn't check this time."

## Quality gates

- No repeat notification for a condition already reported firing — dedupe
  against the checkpoint's last-fired state.
- Tick 1 (no prior checkpoint) never fires purely because the condition is
  already true at first observation — only a transition fires.
- An unreachable source is reported as unreachable, never silently treated
  as a false reading.
- The checkpoint write is atomic — a crash between fetch and write must not
  leave the next tick with a corrupted or half-written baseline.
- No-fire ticks stay short; the absence of news is the expected, successful
  outcome, not a gap to fill with narration.

<!-- crystallizes CAPABILITIES.md Tier 3 ("watch-condition"): "Watch
     [site/feed] and tell me when [condition]." Built 2026-07-13, BACKLOG
     #22 blank-slate curation. Status: target, not live-verified — a real
     verification needs the condition to actually transition across two or
     more real, time-separated heartbeat/scheduler ticks (per
     docs/CAPABILITIES.md: "heartbeat + scheduler exist; needs a lived
     example"), which a single curation session can't fabricate honestly.
     Single-tick mechanics (checkpoint read/write, unreachable-source
     handling) are exercisable now; the no-repeat-fire/no-fire-on-tick-1
     discipline needs a real second tick to prove. -->
