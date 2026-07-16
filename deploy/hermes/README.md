# Hermes → Maro cross-box dispatch (session protocol v0)

The thinnest cross-box slice of `docs/SESSION_PROTOCOL_DESIGN.md` (§9 stage
2): Hermes on the Mini (192.168.0.55) dispatches goals to Maro on this box
over SSH and pulls the run_card back. Shipped + verified end-to-end
2026-07-16.

```
Hermes (Mini)  ──ssh maro-dispatch "<verb> …"──▶  maro-ssh-gate.sh  ──▶  dispatch.py
   ▲                (restricted key, forced command)                       │
   └────────────────────── JSON on stdout ◀────────────────────────────────┘
```

## Pieces

| Piece | Where | What |
|---|---|---|
| `maro-ssh-gate.sh` | this dir | forced-command target; allowlists `ping / dispatch / status / result / list`, validates ids, rejects everything else |
| `dispatch.py` | this dir | enqueue (returns `job_id` in seconds) + detached per-job drain worker; records the `job_id → handle_id` join that core doesn't persist |
| dispatch records | `~/.maro/workspace/output/hermes-dispatch/<job_id>.json` (+ `.log`) | dispatched → running → done/error; joined run_card fields once the run exists |
| authorized_keys entry | `~/.ssh/authorized_keys` (this box) | `command="…/maro-ssh-gate.sh",no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-pty` on the Mini's `hermes-dispatch@mini2` ed25519 key |
| SSH host alias | `~/.ssh/config` on the Mini | `Host maro-dispatch` → 192.168.0.45, dedicated key, BatchMode |
| Hermes skill | `~/.hermes/skills/orchestration/maro-dispatch/SKILL.md` on the Mini | teaches Hermes the verbs + async etiquette (dispatch returns a receipt; poll, never block) |

## Why not `maro-enqueue --drain` straight over ssh

`--drain` blocks for the whole run (5–30 min); Hermes caps a tool call at
300s. And nothing in core maps the enqueue `job_id` to the run's
`handle_id` — `maro-runs` can't answer "how did MY dispatch go" from a job
id alone. `dispatch.py` splits enqueue from drain (same
claim → handle_task → complete steps as `drain_task_store`, one job only —
the drain-once contract holds) and writes the join into the dispatch record.

## Security posture (design doc §8)

The transport edge is an execution-authority edge. The Mini's Hermes brain
is an LLM with shell access, so its key gets a forced command instead of a
login shell: verb allowlist, strict id validation, no pty/forwarding. Goal
*content* is untrusted by design (a goal IS code execution) — that is
Maro's containment machinery's job, not the gate's. The gate only pins the
surface.

Open question 7 in the design doc still stands: whether `container: on`
becomes the standing posture for network-sourced goals — Jeremy's call.

## Verify the lane

```bash
# from the Mini
ssh maro-dispatch ping
ssh maro-dispatch "dispatch <goal text>"      # → {"job_id": ...}
ssh maro-dispatch "status <job_id>"
ssh maro-dispatch "result <job_id>"           # dispatch record + run_card
```

Notify push (Maro → Telegram via `notify.command`) still works alongside:
Hermes owns the bot's *polling*; Maro's notify only *sends* — no conflict.
Push-to-Hermes-inbox (design doc §3) is a later stage.
