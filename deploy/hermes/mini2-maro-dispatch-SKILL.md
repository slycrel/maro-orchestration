---
name: maro-dispatch
description: "Dispatch autonomous goals to the Maro orchestrator on the maro box over SSH: send a goal, poll its status, fetch the final result. Use when the user asks to run/orchestrate a goal through maro, hand work to the other box, or check on a maro run."
version: 0.1.0
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
