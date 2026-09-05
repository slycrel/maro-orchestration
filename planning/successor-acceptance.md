# Successor v1 acceptance — the §8a predicate, live (2026-09-05)

Scratch workspace `l13` (not the production Python workspace), binary
built from `cc8a7441`+, backend `subprocess` model `haiku`, judge model
`haiku`, evaluator `blinded_evaluator` (haiku, tool-less). Every id
below is a record id or run handle in that workspace's journal
(`head=1758 frames=1505` at the end); `maro-go runs show <handle>` and
`maro-go experiment show <id>` re-derive every row from the fold.

## The predicate, row by row

| Clause (design §16, step 13) | Evidence |
|---|---|
| One goal family | `answer` (family rule `family/1`, "question shape"); every unit below was assessed `answer` at intake, treatment-blind. |
| One live randomized experiment through the real subprocess path reaching its stopping rule | Three did. `01M1QKSW4GS8V91FX08P9KN9E1` (apply the code-word lesson, n=6, admitted t/c/t/c/t/c, exposed 3/3): the evaluator lane closed it unprompted, `equivalent → item_redundant`, the lesson `01M1QKRMTQCWY3FYCGNW81K1VC` went `candidate → observed` (tenure, 3 exposures) `→ tombstone`. `01M1QM0TH7K35AGN4YDA5JFTRY` (apply the file-location lesson `01M1QKZTJBF82YXAMS7W33K0Q0`, n=6, exposed 3/3): `equivalent → item_redundant → tombstone`. `01M1QMWMEN6SMZ3D60NXG9WV98` (ablate the `model_judge` seed `01M1QKRMT8XV42X4SB8MWCK8GV`, n=4, exposed 2/2, every unit scored 1): `equivalent → item_redundant → tombstone`. All three took the "item_redundant followed by the policy tombstone" branch of the clause. |
| The predeclared LifecycleTransition committed from a recomputed attestation | The three tombstones above, each committed by the evaluator lane from an attestation recomputed over the cohort commitment; and one `item_helpful`: paired replay `01M1QMKS5ND2BQ3CRD2PKMTNZ0` (apply lesson `01M1QMDS65EHXMWMARETJGGD5B`, oracle `deterministic_fixture` = "juniper", 3 units, 6/6 arm evidences through the subprocess path; `treatment_helpful`, delta_itt 1.000, delta_pp 1.000, discordant 3) → revision `01M1QMDS65EHXMWMARETJGGD5C` `candidate → effective`. |
| A subsequent production run whose request hash or PolicySelection demonstrably changed because of it | Request hash: run `8505fbbd` (goal "What word does the Sanpete pump-word file contain?", before the close; recall 0 included, `stage:candidate`) execute request `s256v1:1d9ed2cf…a21d5`, deliverable "I don't have context for what the Sanpete pump-word file is"; run `97a0cab1` (same goal text, after the close; recall `1 included of 19 [01M1QMDS65EHXMWMARETJGGD5C]`) execute request `s256v1:b198c533…caf5f`, deliverable "The Sanpete pump-word file contains: **juniper**". Same goal thought, different request, and the difference is the lesson's rendering. PolicySelection: run `abd560a5` (before the ablation closed) `policy 2 of 2 enabled`; run `b30484d6` (after) `policy 01M1QNERVKMRY02CNFDZZQNKSS: 1 of 2 enabled, 1 excluded; mechanisms model_judge=false recall=true`. |
| The Manti target measured and reported | Run `a2bf9b50`: `now --target cost_usd=2.00 --why "Manti envelope: the Python run took 6 steps and $1.52"` on "Where can I get non-ethanol gas in or around Manti, Utah?". The `metering_target` rode the goal's intake command; the delivery ended with `metering: cost_usd — cost_usd measured 0.0283481, target 2 (Manti envelope: …): under`; no `overage` (none was due). The over-target path (an `overage` committed before the delivery, named in the line, the run still delivered) is `TestMeteringTargetIsMeasuredNeverEnforced`. |
| One mechanism removed with absence proof | The `model_judge` seed is `tombstone` (`learn list`); the next production goal, run `b30484d6`, selected `1 of 2` policies with the tombstoned seed in `Excluded` and `Mechanisms[model_judge]=false` in its attempt config — the fold refuses a config that disagrees with the selection. Step 11b's live run removed `recall` the same way on the height population. |
| One lens swap | Agenda run `6a50ce51` under the neutral lens: three `judge` invocations, no `lens` field, request `s256v1:7fdfb8f4…` begins "You are a judge. Given the goal, one planned step…". Agenda run `1c52a008` of the same goal under `serve --lens skeptic`: attempt config `lens: skeptic`, judge invocations carry `lens=skeptic`, request `s256v1:adc68f65…` begins "You are judging as a sceptic. A claim of success is not success…" followed by the same judge prompt shape; execute/plan/intent invocations carry no lens. The byte-exact "same facts" proof (skeptic judge request == Lensed(text, neutral judge request), execute requests identical) is `TestLensSwapOnTheSameFacts`; the fold's refusals are `TestFoldLensRules`. |

## What the live run taught (not in the predicate, and it matters)

1. **The blinded evaluator is blind to the lessons that add knowledge.**
   Both apply experiments on knowledge lessons closed `equivalent` with
   every unit scored 0: controls honestly said they could not find the
   file / did not know; treatments answered "**juniper**" and the
   evaluator — seeing only goal and deliverable — did not accept an
   answer it could not verify (its stored responses show it emitting
   tool-call text it has no tools for, searching for the file, then
   scoring `not_achieved`; two units needed three tries before a usable
   JSON reply). So the protocol tombstoned two lessons that had
   demonstrably changed behavior (the exposures and deliverables are in
   the ledger). The deterministic-fixture oracle on the same lesson
   measured `item_helpful` at once. Consequence for v2: the oracle class
   is part of the hypothesis — a lesson that supplies a fact needs an
   oracle that can check the fact (fixture, or an evaluator handed the
   authoritative artifact), and `blinded_evaluator` should refuse (or
   be refused for) goals whose achievement it cannot judge from the
   text. The v1 protocol is honest about what it measured; it is the
   oracle's competence that is narrower than the population.
2. **A treatment unit refused on safety grounds** ("I should decline to
   share family discount codes") — the lesson's wording made a plain
   word look like a credential. Lesson text is a prompt; the model's
   reflexes are part of the outcome.
3. **NOW executes inherit this repository's context.** The subprocess
   runs in the process's working directory (this repo's `go/`), so the
   executor read the repo's `CLAUDE.md` and answered the Manti question
   with "outside my wheelhouse — I'm here for the Maro project" and no
   web (`WebFetch`/`WebSearch` are disallowed). The Python run answered
   with tools in 6 steps for $1.52. The metering clause is met; the
   answer is not the Python answer, and the reason is `Request.Cwd` and
   tool policy, not the target. Owed: a per-workspace working directory
   and a tool policy the operator sets.
4. **Live admission is NOW-lane only** (an agenda deliverable is a
   closure rendering, not blindly scorable), so an ablation of the
   model judge can only be measured on runs that never consult it under
   `serve` (the process driver does not set `ModelJudge`; `now` does
   when a judge model is given). The removal is real and evidence-based
   — the mechanism was not changing outcomes on that population — but
   it was never going to.

## Cost

Every run above was haiku; the largest single run (Manti) measured
$0.0283 of reported cost. No cost abort exists to have fired (D15).
