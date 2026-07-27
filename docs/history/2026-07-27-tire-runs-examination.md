---
status: record
---

# Tire-Runs Examination + Fixes (2026-07-27)

Jeremy's tangent: three rounds of Poe (mini2/Hermes) dispatching maro to
research tires for his 1971 Ford F-250 2WD Camper Special (current
LT265/60R18 on 18" wheels; wants period-correct 16" + more sidewall;
front drums possibly converted). Two independent examinations — this
session's, and a blind Opus subagent review given only raw evidence
(task records, run dirs, repo paths, the Poe Telegram transcript; no
assessment prose) — followed by five same-day fixes on branch
`runs-tangent-fixes`.

## The three runs

**Run 1 — task 6ae4ff75, "stuck" at dispatch.** The dispatch navigator
escalated at conf 0.95: "I cannot access external URLs... transcript is
a hard prerequisite." False at every level — Poe had browsed the link,
plain curl from this box returns HTTP 200/842KB with the transcript
content (49× "235/85R16"), and executor containers curl the web
routinely. The navigator is a no_tools LLM call that projected its own
vantage onto the runtime. (The Opus review counters that, given Poe's
goal phrasing made the transcript a gate, escalation per se was
defensible — the divergence is real and both halves are true: the *move*
was arguable, the *stated reason* was factually wrong.)

**Run 2 — 4d20b559-patient-nettle, "incomplete"/partial.** Honest
research delivered (tire-recommendations-brief.md, 11.5KB: three picks
in LT235/85R16 LR E, live-verified price band, explicit unverified
flags). Both verdict layers then failed: LLM closure died on a swallowed
exception (fail-open, `log.debug` — invisible), and the deterministic
provenance guard matched the goal phrase "load range/index"
(`load` claim verb + slash token) as a claimed input file, demoting
done → incomplete. The false verdict leaked to Jeremy via Poe.
goal_achieved=False also skipped lesson crystallization — zero
LESSON_RECORDED all night. Plan was crippled pre-start: decompose and
scope both died on the subprocess adapter's 1500-token output cap
(counts thinking), leaving a heuristic 2-step mega-plan. $0.62.

**Run 3 — 1bfd0894-noble-marsh, "stuck"/failed.** Best research of the
three: real 8-step plan, script-verified geometry (caught that factory
wheels were 16.5" not 16"), rejected LT255/85R16 on clearance grounds,
pulled two purchase-ready tiers off live product pages (Milestar
Patagonia A/T R LT235/85R16 LR E $144.06; Falken Wildpeak A/T4W $225.69
with URL). Killed by the cost hard-stop after step 5 ($2.7433 vs
$2.00+$0.40 slush) with steps 6-8 remaining → stuck → success_class
"failed". Driver: subprocess workers curl raw retailer HTML into
context (step 4 alone: 2.14M input tokens/$1.21) — the capped Jina
fetch path is architecturally unreachable from the `claude -p` backend.
Also: ran in a fresh project (`finish-and-correct-the-tire`) while run
2's brief lived in `complete-this-practical-tirebuying-research` —
"finish and correct" silently became "start over". Scope died again on
the 1500 cap; persona auto-selection picked creative-director.

## The Opus independent review

Blind protocol held (raw evidence only, read-only). Verdict convergence
with this session's assessment on all four fix targets: provenance
regex (it reproduced the regex hit independently, noting the demotion
was "right by accident" — the brief genuinely lacked load-index numbers
— but reached by prose pattern-matching, non-robust), the 1500-cap
planning deaths, project fragmentation, closure fail-open. Its distinct
contributions: run 3's ending (cost hard-stop; this session still had
it mid-flight), the token-explosion mechanism (fetch seam unreachable
from the live backend), and the headline framing — **execution got
monotonically better across the three runs while the recorded verdict
got monotonically worse** (nothing → partial → failed). Structural
observation: the verdict stack is a tower of individually-reasonable
fail-open heuristics; composed, whatever cheap check happens to fire
becomes the arbiter, and the done-vs-achieved guard assumes
filesystem-shaped goals while the incoming goal mix has drifted to
research-shaped. One divergence (run 1, above) recorded rather than
adjudicated.

## Fixes shipped (this branch)

1. `6f5bce2` provenance — `_path_shaped()` gate: slash tokens count as
   claimed paths only with an anchor, extension, or existing base dir.
   Pinned against the live run-2 goal text.
2. `a0bb16d` planner/scope — `GOAL_REASONING_MAX_TOKENS = 4000` for
   cuts + all five decompose lanes; scope defaults 1200 → 4000. The
   subprocess cap counts thinking; parsers stay the contract gate;
   tight caps on loosely-parsed calls (rewrite) unchanged.
3. `348b601` navigator — vantage rule (judgment rule 6 + escalate
   contract): a claimed runtime capability limit needs runtime evidence
   (a failed attempt in the input); absent that, execute. Prompt-only,
   pin test on the sent system message.
4. `3be1aa1` dispatch — continuation-aware project routing: the
   existing dispatch navigator call sees a recent-projects menu
   (name/age/NEXT.md hint, mtime top-5) and may name one in its execute
   payload; handle_task binds the pick only if it names an existing
   project dir, stamps origin, passes it to handle(). Non-dispatch
   navigator prompts stay byte-identical.
5. closure — skip observability: the swallowed exception now logs at
   warning and lands in the skip event as `skip_detail`;
   `skip_reason="exception"` stays pinned. Fail-open posture unchanged
   (design question, deferred).

Deferred findings (token-lean fetch/mid-step brake, success accounting
vs answer quality, fail-open posture, persona misroute) → BACKLOG
"Tire-runs tangent — deferred findings (2026-07-27)". The success-
accounting item is chunk-9/stop-verdict territory — these runs are the
live exhibit for the survey's fail-open conflations.

Direction check (Jeremy's condition "directionally appropriate for our
longer term vision"): fix 3 is recon-before-verdict at runtime
(verdicts carry evidence — §13b); fix 4 is one-map continuity at the
project seam (the run that continues work lands where the work is);
fixes 1/2/5 are honest-verdict plumbing the compound-thinking wiring
will sit on. Nothing here pre-empts the open chunk-9 agenda questions.
