---
name: maro-dispatch
description: "Dispatch autonomous goals to the Maro orchestrator on the maro box over SSH: send a goal, poll its status, fetch the final result. Use when the user asks to run/orchestrate a goal through maro, hand work to the other box, or check on a maro run."
version: 0.2.0
author: hermes-maro
platforms: [macos]
metadata:
  hermes:
    tags: [Orchestration, Maro, Goals, Autonomous, Dispatch]
prerequisites:
  commands: [ssh]
---

<!-- REPO COPY = SOURCE OF TRUTH (same convention as mini2-maro-inbox.sh).
     Lives on mini2 at ~/.hermes/skills/orchestration/maro-dispatch/SKILL.md;
     install with:
       scp deploy/hermes/mini2-maro-dispatch-SKILL.md \
           mini2:.hermes/skills/orchestration/maro-dispatch/SKILL.md -->


# Maro Dispatch

Maro is an autonomous agent orchestrator running on the maro box (the Linux
Mac Mini on this LAN). It takes a high-level goal, plans it, executes the
steps with its own LLM backend, verifies the outcome, and records a run card.
You talk to it over a restricted SSH channel — the host alias `maro-dispatch`
accepts ONLY the verbs below (it is not a shell).

Dispatch is **asynchronous**: sending a goal returns a `job_id` in seconds;
the run itself takes 5–30 minutes on the maro box. Never wait synchronously.

## Ground rules — don't guess, check

- **Never state anything about a run you haven't read from `status` or
  `result` output in this conversation.** "Maro is on it" is a claim about
  the future; the only facts are the fields the verbs return.
- Before explaining a failure, fetch `result <job_id>` and read what's
  actually there. Quote fields; don't paraphrase a guess into them.
- Distinguish out loud between what the record SAYS and what is ABSENT from
  it. "The record has no error field" is a finding; "it probably failed
  because X" is not.
- A dispatched job is not progress to report. Progress = a status change you
  observed by polling.
- If you're about to write "likely", "probably", or "should be" about a run's
  state — stop and run `status` instead.

<!-- The two sections below originated as Poe self-patches on the live mini2
     copy (2026-07-27/28), folded back into this source-of-truth copy
     2026-07-29 so an install never silently discards them. -->

## Dispatch triage — use orchestration deliberately

For a link, post, repo, or other bounded source, default to a direct one-shot
inspection before dispatching. The user usually wants a practical answer such
as **"is this worth my time?"**, not a research-paper-grade audit. Retrieve the
actual source, make a clear judgement, and take the obvious safe next action
when appropriate.

Dispatch to Maro only when the work genuinely benefits from it: multi-source
research, a long-running implementation/test, substantial comparison, or a
durable artifact. Do not use Maro merely to restate that an internet claim
needs verification. If direct access is blocked, say exactly what could not be
retrieved and try the next practical source path; do not hand the verification
burden back to the user.

When Maro is appropriate, include an outcome-focused request and require a
short user-facing answer in the final response. Do not force artifact-heavy
checklists unless the user specifically needs an audit trail.

## Maro project backlog maintenance

**Repo edits go through the propose lane** — use the `maro-propose` skill
(persistent clone + `maro-propose start/send`), never a fresh `/tmp` clone;
this machine cannot push to GitHub and a local-only commit strands.

For a request to add or reshape work in the Maro project's own backlog, inspect the canonical `BACKLOG.md` and the relevant design document first. Extend the existing architectural umbrella when the request is a next review/pass, rather than creating a detached duplicate. Make the entry decision-oriented and evidence-gated: work-shape question, organic acceptance cases, a direct baseline, explicit deliverable, and no broad implementation before the review. See [the Maro backlog maintenance reference](references/maro-backlog-maintenance.md) for the compound-thinking example and historical-arc posture.

## Send a goal

```bash
ssh maro-dispatch "dispatch <the full goal text here>"
```

Returns JSON: `{"job_id": "...", "status": "dispatched"}`. Report the job_id
to the user immediately — that is the receipt.

Goals should be self-contained and outcome-shaped (what done looks like, any
constraints). If the user's ask relies on context only you have (names,
preferences, prior conversation), enrich the goal text with it before
dispatching — Maro only sees what you send.

## Writing the goal text (added 2026-07-29 — learned the hard way)

Maro LEARNS from dispatched goals: its lesson extractor generalizes from
run outcomes, and text you add to the goal can end up shaping unrelated
future runs. A 2026-07-27 dispatch that embedded "Do NOT escalate or stop
merely because a linked page cannot be accessed" got generalized into a
stored lesson about obeying prompts, which then got injected into the next
run. Maro now quarantines that lesson class automatically — but a
well-shaped dispatch never triggers it. Rules:

- **Keep the user's ask verbatim.** Put their words in the goal unchanged;
  add your context AROUND them, clearly separated — never rewrite their ask
  into your own framing. (A typed envelope for this separation is specced:
  docs/DISPATCH_ENVELOPE.md in the maro repo. Until it lands, plain
  labeled sections do the job.)
- **No behavior-steering scaffolding.** Don't tell Maro when not to
  escalate, stop, or ask for help ("do not escalate/stop because...", "you
  cannot use X as an excuse"). Maro has its own recovery and escalation
  policy; steering text can't improve it, and it's exactly the class that
  gets quarantined now. State the FACTS instead ("the linked page 404s
  from the maro box; its contents are included below") and let Maro decide
  how to proceed.
- **Label recovered material as reference, not directives.** When you
  fetch content Maro can't reach (xurl, curl), include it under an
  explicit label with its source — e.g. "Reference material (recovered by
  Poe from <url>, which is unreachable from the maro box):" — rather than
  weaving it into the task instructions.
- **Only dispatch what the user asked for.** A follow-up idea of yours
  (however helpful) is a PROPOSAL — put it to the user first; dispatch it
  only after they say yes. The user hearing about a run they never asked
  for costs more trust than the run is worth.

### Source resistance is not a dispatch blocker

<!-- Poe self-patch (2026-07-28), folded back 2026-07-29 with the
     goal-steering parts rewritten to comply with the section above — the
     original told you to write recovery-ladder DIRECTIVES into the goal,
     which is the scaffolding class that contaminated Maro's lesson store. -->

Use [the resilient source-research reference](references/resilient-source-research.md)
for the recovery ladder and recommendation quality gate. The ladder is for
YOU, before dispatching — not text to paste into the goal.

For substantial research grounded in a user-provided URL, inspect and recover
the source yourself first where possible (direct page/API, renderers or
mirrors, source-linked primary documents, search), then include the recovered
facts or text in the goal as labeled reference material. Frame the URL as
supplemental evidence, not a hard prerequisite — state plainly what is and
isn't reachable from the maro box and let Maro plan around the facts.

For purchase, safety, or fitment research, the quality bar for a usable
final recommendation is source-backed and concrete (exact item/specification,
primary or seller URL, and the specific condition it relies on). A broad
category page, unlinked price range, or missing key specification is an
**incomplete** outcome — report it to the user as such, with the gaps the
verifier listed, and propose a focused repair dispatch. Dispatch the repair
only after the user agrees (see "Only dispatch what the user asked for").

## Check progress

```bash
ssh maro-dispatch "status <job_id>"
```

`status` field: `dispatched` → `running` → `done` (or `error`,
`clarification_needed`, `incomplete`). Once the run finishes it also includes
`goal_achieved`, `goal_verdict_summary`, and `handle_id`.

Non-`done` outcomes carry their own explanation (added 2026-07-16):

- `clarification_needed` → `clarification_question` holds the exact question
  Maro needs answered. Relay it to the user verbatim, then re-dispatch the
  goal with the answer appended.
- `incomplete` → `goal_verdict_gaps` lists what the verifier found missing
  (the truncated `goal_verdict_summary` alone can be misleading).
- Any preflight-terminated run → `result_excerpt` carries the full result
  text (question, guard refusal, or error detail).

## Pushed events — check the inbox FIRST (added 2026-07-17)

Maro PUSHES completion and escalation events here: each event lands as a
JSON file in `~/.hermes/inbox/maro/` (the payload is the run_card, with
`job_id` when the run came from a dispatch). The contract is two-tone:
Maro sends DATA, you compose the user-facing answer. Key payload fields:

- `.goal` — the user's ORIGINAL ASK. Answer this, not "did the run work".
- `.answer_summary` — a distilled answer (source in `.answer_source`).
- `.deliverable_content` — the full deliverable text (name in
  `.deliverable_name`; `.deliverable_truncated` true if capped at 16KB).
- `.goal_achieved` / `.goal_verdict_summary` / `.goal_verdict_gaps` — the
  verifier's take. Relay gaps plainly when the goal was NOT achieved.

For dispatched-run completions and escalations, a detached brain turn (you,
spawned by the inbox script) is asked to compose and send the user's DM from
that data — grounded strictly in the payload, organized however serves the
reader. If you're that turn: quote the data, never invent findings, keep it
phone-glance short, offer the full report on request, then move the event
file to `processed/`.

- When the user asks about a dispatched job: `ls -t ~/.hermes/inbox/maro/`
  (and `processed/`) and read the newest file for that job_id BEFORE
  reaching for ssh polling — the pushed card is the same data, already local,
  including the full deliverable text.
- After consuming an event in conversation, move its file into
  `~/.hermes/inbox/maro/processed/`.
- No event file for a job you dispatched = the run is still going (or the
  push leg failed) — THEN use `status <job_id>` over ssh.

## Fetch the final result

```bash
ssh maro-dispatch "result <job_id>"
```

When finished, returns the dispatch record plus the full `run_card` JSON
(goal, status, goal_achieved, verdict summary, artifact info, cost).

## List recent dispatches

```bash
ssh maro-dispatch "list"
```

## Connectivity check

```bash
ssh maro-dispatch "ping"
```

## Reporting back to the user

- After dispatching: give the job_id and say the run is underway on the maro
  box; offer to check on it later. Do NOT block waiting for completion.
- When asked "how's it going": run `status` and relay the status verbatim.
- When it's done: report (1) status, (2) `goal_achieved` true/false, (3) the
  verdict summary. Quote the run's own output — never invent results.
- If a poll shows `error`, report the error text as-is.

## Incomplete or underspecified outcomes

A non-`done` state is not an explanation. Before attributing a cause or
asking the user to supply missing source material:

1. Fetch `result <job_id>` as well as `status <job_id>` and distinguish
   explicitly between details the run returned and fields that are absent.
2. Run `list` to see whether recent jobs share the same failure pattern.
3. Inspect the user's original direct source with an independent permitted
   retrieval path when practical. Do not infer that a post, page, or tool was
   inaccessible merely because the dispatched job produced no findings.
4. If the restricted dispatch interface exposes no trace/error detail,
   state that limitation plainly. Do NOT dispatch a diagnostic goal asking
   Maro to inspect its own run records: dispatched goals execute in an
   isolated container with no view of those records, so the diagnostic
   burns tokens proving its own isolation (verified 2026-07-16). The
   `status`/`result` fields above are the supported evidence channel; if
   they are insufficient, say so and let the user take it to the maro box.

Never label a probable blocker as the root cause without evidence. Report
what was checked, the exact status, and what diagnostic information is still
unavailable.
