---
status: living
---

# Environment requests — Maro installs what a job needs, never with runtime root

*Shipped (Python) 2026-09-07. Decree: GOAL_BRAIN 2026-09-07 (decision
`ea9e311f`). Code: `src/env_request.py`; loop seam in `loop_execute.py`
beside the operator-ask read; frame paragraph in `step_exec._env_block`;
image resolution in `llm._run_subprocess_safe`; answer application in
`operator_ask.answer`. Tests: `tests/test_env_request.py`. Hermes side:
`deploy/hermes/mini2-maro-inbox.sh` (brain prompt + fallback DM),
`deploy/hermes/mini2-maro-dispatch-SKILL.md` ("Decide an install request").
Go successor: owed (§8).*

## 1. Why this exists

The first live firing of the ask lane (run 084d3c1f, 2026-09-07) ended on
the operator three times. The third question offered "approval to install
Chromium and Playwright" as an option the run could not actually execute:
the executor image is `node:22-slim` plus git, python3 and curl; every
step runs as the host uid with no sudo binary and no pip; every step is a
fresh `docker run --rm`, so anything installed dies with the step. The
worker's probes (`which` for four browsers, `sudo -l`, `apt update`,
`python3 -m pip`) were real and their failures were true inside the box.
Saying yes would have changed nothing.

Jeremy's framing (2026-09-07): *"the app password is a distraction; the
user shouldn't be bootstrapping maro, it should be doing it itself… I'm
more interested in solving the pattern here — maro can't 'safely' install
software it needs to get its job done; we need to help facilitate that."*
And the boundary: no runtime root, ever. The "escalated privilege re-run of
a step with full sudo in the container" he floated is rejected here on two
grounds: root at runtime is exactly the property the container was built to
deny, and an install made that way still evaporates at `--rm`.

The rule this doc implements: **root happens at image build time only,
driven by an artifact the engine reviews, never inside a running worker.**

## 2. The contract — a file, not a sentence

Same shape as the ask lane. The container-lane execute frame carries a
`## Installing what you need` paragraph (`env_request.instructions`) naming
`$MARO_ENV_REQUEST`. A worker that lacks a tool writes ONE JSON object
there and ends its step:

```json
{"need": "drive a headless browser for the Yahoo login",
 "apt": ["chromium"], "pip": [], "npm": ["playwright"],
 "tried": "which chromium / npx playwright — both absent, no sudo"}
```

The engine reads the file after the step and nothing else. "I installed
X" in the prose without the file is a fabrication claim like any other
(the artifact guard already catches the missing binary); the file is a
request whatever the prose says. The consumed file is archived beside
itself (`env-request.<stamp>.requested.json`), never deleted.

| | Python |
|---|---|
| path (host side) | `<run_dir>/scratch/env-request.json` |
| path as the worker sees it | `/tmp/env-request.json` (the scratch bind) |
| env var | `MARO_ENV_REQUEST` (container `-e`, set beside `MARO_ASK`) |
| read by | `loop_execute` after each step, before the ask read |
| host lane | no paragraph, no read: a host worker has its own user-level installers (`pip --user`, `npm --prefix`) — this is a docker problem only |

A file that is not a request (bad JSON, no `need`, no packages, more than
20 per source) is archived and answered with the reason; it is never built
and never escalated.

## 3. Policy — three verdicts and a grant ledger

`env_request.evaluate` sorts every package spec into one of three bins:

- **allowed** — well-formed, from an enabled source (`env.install.sources`,
  default apt/pip/npm), not on the deny list.
- **escalate** — well-formed but from a disabled source or on the deny
  list (`env.install.deny`, default: sudo, su, openssh-server, docker*,
  containerd, podman, systemd, cron, polkitd, dbus — things that change
  what the container *is*, not what it can do).
- **rejected** — malformed. The grammar per source (Debian names; PEP 508
  names with extras and version operators; npm names with scope and
  range) is the injection boundary: specs reach a Dockerfile `RUN` line,
  shell-quoted, and nothing outside the grammar gets there.

A **grant** (`source:spec` in the project's `manifest.json`) moves a spec
from escalate to allowed for that project only. Grants are written by an
orchestrator `allow`, never by the worker, and live beside the policy: the
override is visible in one file, the policy never learns exceptions
(feedback: operator flags are overrides, never the design).

## 4. The build — a per-project image layer

In-policy packages accumulate in `<workspace>/executor-layers/<project>/`:

```
manifest.json    project, base, layer, image, apt[], pip[], npm[], grants[], updated_at
Dockerfile       regenerated from the manifest each build — the reviewable artifact
build.log        the last docker build's output
layers.jsonl     one line per build: layer, image, added, ok, seconds, reason, failure tail
```

The Dockerfile is `FROM <base image>` + `USER root` + one `RUN` per
source (`apt-get install --no-install-recommends …`, `pip install
--break-system-packages …` with `python3-pip` added to apt automatically
because the slim base has none, `npm install -g …`). Root exists in those
lines and nowhere else; the runtime still starts every step with `--user
<host uid>`. The image tag is
`maro-executor:p-<project-slug>-l<N>-<cli>-r<rev>` — the base's `-<cli>-r<rev>`
tail is preserved so `container_exec`'s verbs-baked detection still reads
the revision.

At docker-run time `llm._run_subprocess_safe` asks
`env_request.effective_image(current_project())`: the project's layer when
its recorded base equals the configured base image AND docker still has
the tag; otherwise the base. A changed base invalidates every project
layer (the next request rebuilds the accumulated packages on the new
base); a pruned image falls back loudly to the base.

A failed or timed-out build (`env.install.build_timeout_s`, default 900 s)
leaves the previous layer current and hands the failure tail to the
worker.

## 5. What the loop does

After the step, before the ask read (`loop_execute`):

| verdict | action |
|---|---|
| built | re-run THIS step on the new image with the note *"Environment updated: installed apt:chromium, npm:playwright — this step now runs on image …; do not request these again"* at the top of its context (the loop_blocked retry idiom: re-arm what the step saw, add the note, re-queue) |
| build_failed / rejected / disabled / host lane | re-run the step with the reason so the worker adjusts or proceeds without |
| escalate | pause (§6) |

At most two env re-runs per step (`MAX_RETRIES_PER_STEP`); after that the
step's own outcome stands. Trace edges `step.env_request → env.<kind>`;
captain's log `ENV_LAYER_BUILT` / `ENV_LAYER_FAILED`.

## 6. Escalation — to the orchestrator, not the user

Jeremy 2026-09-07: *"we should escalate to the orchestrator, not the ask
lane unless the orchestrator says so; it's been a while, but that's the
pattern we landed on early on — orchestrator guides in place of the user,
user gets involved if they must. So I'd be ok with a notification of the
ask and an escalation to the orchestrator (you, poe, or user if CLI)."*

An out-of-policy request records an `operator_ask` of kind `env_request`
on the run (same typed pause `awaiting-clarification`, same 24 h time box,
same resume-by-handle) and emits the **`escalation`** event — not
`operator_question` — with `point: env_request`, `audience: orchestrator`,
the one-line decision (§9.6 shape), the request by source, and why policy
escalated. Every consumer sees it: the durable escalations file, the
Telegram card (header "🔧 maro wants to install software — the orchestrator
decides", so the notification is honest about who acts), and the Hermes
inbox leg, whose brain prompt now says the decision is Hermes's own:
allow ordinary tooling for the stated need and tell Jeremy in one line;
deny unrelated, service-replacing or enormous requests; involve Jeremy
only when the request touches his accounts, money, or the box's role.

The orchestrator answers through the existing verb:

```
maro answer <handle> allow [note]     # gate: ssh maro-dispatch "answer <handle> allow"
maro answer <handle> deny <why>
```

`operator_ask.answer` sees the record's kind and applies the verb on the
host before enqueueing the resume: `allow` writes the grants, builds the
layer now, and the continuation reason carries the OUTCOME (*"The
orchestrator ALLOWED it. Environment updated: installed … — the run
continues on image …"*) under `== Orchestrator decision ==`; `deny`
carries the note; anything else is passed through as neither. The resumed
step therefore runs on the new image with no second request. `maro asks`
lists these as `[install]`.

## 7. What is deliberately not here

- **No runtime root, no privileged re-run.** The only root is `docker
  build` on the host, from a Dockerfile the engine wrote from validated
  names.
- **No host installs.** The host lane gets no paragraph; the pattern is
  container-only by construction, as Jeremy framed it.
- **No auto-allow of escalated requests on a timeout.** An unanswered
  escalation expires like a question; the run stays paused.
- **No package-source discovery.** The worker names packages as the
  source knows them; a wrong name fails the build and comes back as a
  build failure for the worker to correct, not as an engine guess.

## 8. Owed

- **Go successor parity.** The Go engine runs steps through the same
  executor image; `internal/invoke` has the `HandOff`/drop conventions to
  mirror. Owed before the shadow lane can take an install-needing goal.
- **Tool presence in the frame** (BACKLOG, "presence index covers secrets
  only"): the worker still cannot tell "absent in the image" from "absent
  on the host". With this lane the honest line is "not in this image —
  request it", which makes the request the deliberate move instead of a
  guess.
- **Live (in-step) ask for time-boxed inputs.** A 2FA code is consumed by
  the session that requested it, so the browser must stay alive while the
  operator answers. The pause lane ends the step; a time-boxed in-step
  variant (worker writes the ask, polls for an answer file, the engine
  feeds the reply into scratch) is the piece the mail goal cannot skip.
  Not built; design owed.
- **First live firing:** the mail goal re-fired on this lane — expected to
  request chromium + playwright (in policy → autonomous build), log in
  with the stored credentials, and stop at the 2FA challenge until the
  live ask exists. Named falsifier: Yahoo bot detection on headless Linux
  Chromium.
