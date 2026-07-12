---
status: brief — input for a dedicated exploration session (Jeremy-funded, date TBD)
---

# Model-Route Exploration Brief

*Written 2026-07-11 at Jeremy's request ("I have up front OpenRouter,
Fireworks AI, OpenCode Go (or Zen?), or maybe Featherless to bring to the
table on what a cheap but capable route would be to use our orchestrator...
do you mind writing something up for that and I'll send a session down that
rabbit hole sometime soon?"). This is the map for that session, not the
session itself.*

## Why now

- The subprocess `claude -p` lane is the workhorse and it's good, but it
  binds every run to one vendor, one auth posture, and the Max-plan rate
  windows. Jeremy just escalated to the top personal Max tier, which buys
  headroom, not independence.
- OpenRouter is currently configured somewhere in the chain and returning
  **402 Payment Required** (spotted in run 8a20665f pre-flight) — the
  multi-route story is half-wired and unfunded today.
- The local-model lane (Ollama/qwen on this box) proved CPU-bound reasoning
  is a dead end here, but the *latency breaker* infrastructure it produced
  is exactly the kind of harness a cheap-route lane needs.
- North-star framing from Jeremy: eventually **a home user on local
  hardware** should be able to run this. Not realizable yet; the 2014 Mini
  stays precisely because it surfaces edges fast hardware would hide. A
  cheap-OSS-model route is the intermediate rung on that ladder.

## The split decision (name it before descending)

Two genuinely different philosophies, and the session should treat them as
such rather than comparing everything on $/token:

**Lane A — OSS models via serving providers** (OpenRouter routing to OSS,
Fireworks, Featherless, opencode's Zen gateway):
- Cheaper, sometimes flat-rate. Reduced capability *by design*.
- Jeremy's hardening thesis: weaker models stress the orchestration — if
  the harness (verify, closure, cuts, lessons) can carry a mid-tier OSS
  model to correct outcomes, the infrastructure is genuinely strong. This
  is the same reason the 2014 Mini stays. **Reduced capability is a
  feature for infrastructure hardening, not just a cost tradeoff.**
- Direct API = we must supply the tool loop. This is THE architectural
  difference: `claude -p` gives an agentic tool-executing worker for free;
  a raw chat-completions endpoint gives text. Worker exec steps need
  either (a) an agent CLI wrapping the route (opencode does this), or
  (b) our own tool-execution loop (big build, don't start it casually),
  or (c) restricting Lane A to the *non-agentic* call classes.

**Lane B — frontier models via OAuth/subscription CLIs** (`claude -p`
today, codex OAuth proven in the Hermes trial, opencode + Anthropic/OpenAI
auth):
- Capability ceiling stays high; flat monthly cost; agentic loop included.
- Gray-area TOS posture (known, accepted so far), rate windows, vendor
  coupling.

The pragmatic hybrid hypothesis (test it, don't assume it): **Lane B for
agentic worker steps, Lane A for the high-volume non-agentic call classes**
— validation ladder, ralph verify, closure checks, classify/routing,
lesson extraction. Those are (a) the majority of call *count*, (b)
structured-output calls where mid-tier OSS models are usually fine, and
(c) already behind the adapter seam.

## Candidates

| Route | What it is | Pricing shape | Agentic? | Notes for us |
|---|---|---|---|---|
| **OpenRouter** | Meta-router over ~300 models (OSS + frontier) | Per-token, BYO credits | No (raw API) | Adapter already exists in llm.py; one key = whole menu; per-model price/latency comparison built into their dashboard. Best *exploration* surface even if not the final route. Fix the 402 first. |
| **Fireworks AI** | Fast OSS serving (Llama/Qwen/DeepSeek etc.), fp8, function calling | Per-token, cheap | No (raw API, good fn-calling) | Speed is the differentiator — relevant to our validation-latency cap (15s). |
| **Featherless** | Flat-rate unlimited OSS inference (subscription tiers) | **Flat monthly** | No | Flat rate kills per-call cost anxiety for burn-heavy loops (our introspection/validation chatter). Throughput caps instead of bills — matches "app not systemic" budgeting. Model catalog is HuggingFace-broad but serving speed varies. |
| **opencode (Go) + Zen** | OSS agent CLI (Go) with its own model gateway ("Zen") + BYO keys/OAuth | Mixed | **Yes** — full agent loop | The only Lane-A candidate that brings its own tool loop; could be the `claude -p`-shaped shim for OSS models (same pattern as the Hermes claude-CLI-shim brain). Jeremy's earlier sidequest instinct. |
| **codex OAuth** | ChatGPT-plan OAuth via codex CLI (gpt-5.5) | Existing $20/mo | Yes | Proven in Hermes trial; recipe already in memory. Second frontier lane, already paid for. |
| **claude -p** (baseline) | Current workhorse | Max plan (top tier now) | Yes | Every comparison is against this. |

## What the session should actually do (measured spike, not a re-plumb)

1. **Fund + fix OpenRouter** (small credit load; it's the exploration
   surface). Repoint or remove the stale 402 config either way.
2. Pick **three representative call classes** with real prompts pulled
   from run ledgers (we have thousands in `calls/`):
   validation-ladder verify, decompose/planning, and one worker exec step
   (the exec one only for routes with an agent loop).
3. Run each class across: claude -p (baseline), 2-3 OSS models via
   OpenRouter (e.g. a Qwen-72B-class, a DeepSeek-V3-class, one small),
   Fireworks (same model where possible, for speed delta), codex OAuth.
4. Measure per class: **cost, wall latency, and verdict agreement with
   baseline** (we already log ground truth in run cards — replay is
   cheap). The latency breaker's per-process ROI pattern is the model to
   copy for "is this route actually helping."
5. If a Lane-A route clears the bar on the non-agentic classes: wire it as
   the tier mapping for those call sites only (config change, no
   architecture), and run **one full Manti** + **one research-class goal**
   with the hybrid routing. Compare card cost/wall/verdicts vs the
   2026-07-11 post-fix baseline (6 steps / 16m43s / $1.52).
6. Separately (bigger, only if appetite): trial opencode-as-worker-shim on
   one build-class goal — that's the Lane-A-agentic experiment, and the
   template is the Hermes claude-CLI-shim.

Suggested experiment budget: $20-50 total credits across providers;
Featherless only makes sense as a follow-on subscription month if the
per-token spike shows OSS models clearing the quality bar at volume.

## Non-goals for that session

- No re-architecting llm.py — the adapter seam and tier map already
  support per-backend model strings; this is config + measurement.
- No building our own tool-execution loop for raw APIs (that decision
  deserves its own brief if Lane-A-agentic via opencode fails).
- No changing the default posture on this box until the numbers exist.

## Success criterion

A funded, measured recommendation: which route(s) carry which call
classes, at what cost delta, with what quality delta — and a config recipe
to flip it on. "OSS models can't do X yet" is an acceptable, valuable
answer if the data shows it (that's the hardening map, not a failure).
