---
status: living
---

# Two-box PoC: Hermes as the interface brain, Maro as the orchestrator

*Beta/prototype notes, shared in case you want to run orchestration the same
way. Everything here is live and verified as of 2026-07-16 on deliberately
modest hardware (two 2014 Mac Minis, ~$100 for the second one). Treat it as
a recipe with the potholes marked, not a supported product surface.*

## The shape

```
[ phone: Telegram (iMessage someday) ]
        │
[ Hermes Agent on box B ]        ← interface brain: conversation, persona,
        │                          channel handling, its own memory
   ssh (restricted key)          ← the ONLY link between the boxes
        │
[ Maro orchestrator on box A ]   ← execution brain: plan → execute → verify,
                                   honesty machinery, containment, run cards
```

Hermes ([Nous Research's open-source agent harness](https://github.com/NousResearch/hermes-agent))
turns out to be a natural end-user front for Maro: it owns the phone-facing
conversation and *enriches* goals with user context before dispatching —
unprompted, in our first live test it rewrote a terse ask into a cleaner,
more precise goal. Maro owns actually getting the work done and proving it.

Why two boxes at all: the interface should stay responsive while the
orchestrator grinds; the trust boundary between "an LLM with shell access"
and "a box that executes goals" becomes a real, inspectable edge; and the
orchestrator location stays a runtime fact you can move later (Maro's
learning data is portable by design).

## Box B: Hermes setup (ours: 2014 Mac Mini, macOS 12 Monterey)

- **Old-macOS rule:** use prebuilt release binaries, never `brew install` —
  unsupported-tier brew has no bottles and will compile LLVM from source for
  a pastry. This bit us repeatedly until it became a rule.
- **Install:** the stock Hermes installer, native (no Docker needed on the
  interface box). Config lands in `~/.hermes/` (`config.yaml`, `.env`,
  `skills/`, `logs/`).
- **Gateway as a service:** `hermes gateway install --start-now
  --start-on-login` → a launchd agent that survives reboots and restarts on
  crash. Logs: `~/.hermes/logs/gateway.log`.
- **Brain:** `hermes auth add openai-codex --type oauth` rides a ChatGPT
  subscription (no per-token cost). **Two steps are interactive-only** — the
  OAuth flow needs a TTY/browser, and `hermes model` is an interactive
  picker. Run both at a real terminal once; everything after is headless.
  An API-key lane (`hermes auth add openrouter --type api-key`) makes a fine
  fallback if funded.
- **Telegram channel:** bot token in `~/.hermes/.env` (file-to-file, never
  echo it), long-polling, **pairing-gated** — unpaired senders are refused.
  Gotcha: approve with the code the bot DMs the user
  (`hermes pairing approve telegram <CODE>`), *not* the request id shown by
  `hermes pairing list`.
- **Home channel:** make a separate Telegram group with the same bot and
  `/sethome` it — automation/cron output lands there instead of cluttering
  the DM. The DM stays a clean conversation lane.

## Box A: Maro side

Everything is in this directory (`deploy/hermes/`):

- `maro-ssh-gate.sh` — forced-command target for a **dedicated** ssh key.
  The interface brain is an LLM with shell access, so its key gets a verb
  allowlist (`ping / dispatch / status / result / list`), strict id
  validation, `no-pty`, no forwarding — not a login shell. See the
  authorized_keys line in the file header.
- `dispatch.py` — the async split of Maro's `enqueue --drain` contract:
  `dispatch` returns a `job_id` in seconds, a detached per-job worker drains
  it (drain-once holds), and the `job_id → handle_id` join is recorded under
  `~/.maro/workspace/output/hermes-dispatch/`. Async matters because Hermes
  caps a tool call at 300s and real runs take 10–30 minutes.
- The Hermes-side **skill** (`README.md` here reproduces the layout;
  installed at `~/.hermes/skills/orchestration/maro-dispatch/SKILL.md` on
  box B) teaches the verbs plus the etiquette: dispatch = receipt, poll for
  status, never block, quote the run's own verdict, never invent results.

Notify push (Maro → Telegram) coexists with Hermes owning the same bot:
Hermes holds the long-poll (`getUpdates`); Maro only calls `sendMessage`.
Point Maro's `telegram.chat_id` at the home-channel group id and run
results land in the ops lane.

## Security posture (the part not to skip)

A goal IS code execution. Consequences we actually implemented:

1. **No open ports, no HTTP API, no broker.** The transport is ssh over a
   trusted link; the public-API failure mode (one auth bug = RCE) is
   avoided by not building one.
2. **The dispatch key can only dispatch.** Forced command + allowlist; test
   it by trying `ssh <alias> "rm -rf /"` — you should get
   `{"error": "verb not allowed: rm"}`.
3. **Network-sourced goals run containerized.** We flipped Maro's
   `executor.container: on` the same day this lane went live — the
   dispatching brain is exactly the "hostile goal author" the mount
   whitelist exists for. Worth doing even though it had been running fine
   without: the harder edge is the better test.
4. **Each box keeps its own credentials.** Nothing under `~/.claude` or the
   workspace secrets crosses the wire; the interface box gets its own auth.

## Verify the whole thing

```bash
# from box B
ssh maro-dispatch ping                          # auth + gate alive
ssh maro-dispatch "dispatch <tiny goal>"        # → {"job_id": ...}
ssh maro-dispatch "status <job_id>"             # dispatched → running → done
ssh maro-dispatch "result <job_id>"             # run_card JSON
hermes -z "Dispatch this goal to maro: ..."     # the brain drives the skill
```

Our first end-to-end: a dispatched goal ran to `goal_achieved: true` on box
A and Hermes narrated the run card back accurately, headless, ~$0.19 of
metered cost on a subscription lane.

## Known gaps / deferred (honest list)

- **iMessage**: wants newer macOS or a trusted device; Monterey's Messages
  sign-in loops silently. Telegram is the working channel.
- **Push to Hermes**: results reach the human via Telegram notify; a
  Maro→Hermes inbox (so the *brain* learns of completion without polling)
  is a later protocol stage, as are effort/consent and mid-run injection —
  see `docs/SESSION_PROTOCOL_DESIGN.md`.
- **Tailscale on headless old-macOS** is its own yak (source build or GUI +
  System Extension approval); same-LAN dispatch doesn't need it.
- Hermes conversational memory and Maro execution memory are deliberately
  separate brains for now.
