---
status: living
note: exploration in progress — research phase done 2026-07-12 (see Session findings); measured spike on the runtime box still pending funding
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

---

# Session findings — 2026-07-12 (research phase, dev Mac)

*Item 24 session, part 1. Four parallel research passes (live web fetches,
not model memory) + a code-seam audit of llm.py. The measured spike (part
2) still needs the runtime box + OpenRouter funding. Everything priced
below was live-verified 2026-07-12 unless flagged.*

## The landscape moved since the brief — three facts that reframe it

**1. Third-party OAuth on Claude subscriptions is dead, not gray.**
Anthropic enforced server-side blocks Jan 9 2026, updated the consumer
ToS to prohibit it, and sent a legal demand that made opencode remove
Claude subscription login (merged Mar 19 2026, PR #18186). First-party
`claude` / `claude -p` / Agent SDK remains the *sanctioned* path for
Pro/Max plans — there's an official support article for exactly our
usage. So "opencode as a claude-Max shim" is off the table permanently;
Lane B's Claude side is `claude -p` only.

**2. But `claude -p`-under-Max carries a live re-pricing risk.** On May
14 2026 Anthropic announced programmatic use (claude -p, Agent SDK, CI)
would move to a separate credit pool billed at API rates starting June
15 — then **paused it on the effective date** ("for now, nothing has
changed"; a reworked version will return with advance notice). Also: the
+50% weekly-limit promo expires ~July 13, so expect the workhorse lane
to feel tighter this week. Multi-route independence is now more
justified than when the brief was written, not less.

**3. OpenAI went the opposite direction — deliberately permissive.**
`codex exec --json` is an *officially documented* headless surface
(JSONL events, `--output-schema` for schema-constrained final answers,
session resume, sandbox flags, device-code auth for headless boxes).
OpenAI extends free Pro/Codex access to OSS maintainers using
third-party tools, and opencode ships native ChatGPT-plan sign-in.
Models under plan auth are now the GPT-5.6 family (sol/terra/luna, GA
Jul 9); the `gpt-5.x-codex` variants are sunset — don't pin a model.
Plan shape converged on Anthropic's: $20 Plus / $100 5x / $200 20x,
5-hour windows + (unpublished) weekly caps.

## Verified candidates table (replaces the brief's table)

| Route | Pricing (verified 2026-07-12) | Agentic? | Verdict for us |
|---|---|---|---|
| **OpenRouter** | Pure passthrough + 5.5% credit-purchase fee (min $0.80/txn; $5 min top-up — buy $20+ in one txn to amortize). Cheap tier: gpt-oss-20b $0.029/$0.14 per M in/out, deepseek-v4-flash $0.077/$0.154, glm-4.7-flash $0.06/$0.40. Mid tier: minimax-m3 $0.30/$1.20, deepseek-v4-pro $0.435/$0.87, glm-5.2 $0.42/$1.32, kimi-k2.5 $0.375/$2.03. | No (but full OpenAI-style tool calling + strict json_schema, `:exacto` variant for reliable tool calls, `:nitro`/latency routing) | **Fund it — the Lane A vehicle.** One key covers the whole menu incl. Fireworks-hosted endpoints (pin provider to measure speed deltas). Free `:free` tier exists (1,000 req/day once ≥$10 lifetime spend) but is lowest-priority routing — not for the 15s validation cap. |
| **Fireworks direct** | Same families at 1.5–4x OpenRouter floor (deepseek-v4-flash $0.14 vs $0.077; v4-pro $1.74 vs $0.435). Speed crown on big OSS MoEs (peak ~446 tok/s GLM 5.2); prepaid since Jul 1 2026. | No (strong fn-calling incl. recursive schemas) | **Skip a direct account for the spike** — reachable *through* OpenRouter provider-pinning. Revisit only if a pinned-Fireworks route wins and the 5.5% fee matters at volume. |
| **Featherless** | $25/mo flat = 4 concurrency units, but big MoEs (DeepSeek/Kimi-class) cost 4 units each → effectively **1 concurrent big-model request**, and **32K context cap** on that tier; 10–40 tok/s by design. 256K ctx needs $100+/mo agent tiers. | No | **Deprioritized.** Concurrency math + context cap + deliberate slowness fit none of our call classes. Flat-rate anxiety relief is real but opencode Go does it cheaper (below). |
| **opencode + Zen** | Zen = per-token *at cost* (zero markup) — no advantage over OpenRouter for raw calls. **New since the brief: "opencode Go" — $10/mo flat** ($5 first month) for 13 curated open coding models (GLM-5.2, Kimi K2.7 Code, Qwen3.7, DeepSeek V4 Pro/Flash, MiniMax M3…), rolling caps $12/5hr, $30/wk, $60/mo. | **Yes** — `opencode run --format json` (event stream), `--auto`, session resume, server mode | **The Lane-A-agentic experiment, repriced.** $10 flat for an agent loop over current OSS coding models is the cheapest possible test of "can the harness carry a mid-tier model through worker exec steps." Claude-Max login removed (legal); ChatGPT-plan OAuth works. |
| **codex OAuth** | Existing $20/mo Plus. Sol 15–90 msgs/5h on Plus (75–450 on Pro 5x). Metering is token-credit-based since Apr 2026; cached input at 10%. | Yes — `codex exec --json`, officially documented for automation | **Enable it — the adapter already exists** (`CodexCLIAdapter`, kept out of default backend order on purpose). Second frontier lane, already paid for, officially tolerated. `--output-schema` also makes it a candidate for *structured* calls, not just exec steps. |
| **claude -p (baseline)** | Max top tier. Weekly +50% promo ends ~Jul 13. | Yes | Workhorse; sanctioned; carries the paused-repricing policy risk above. |

## Code-seam reality check (what the spike actually needs to touch)

Better than the brief assumed — most of the measurement harness already
exists:

- **CodexCLIAdapter already shipped** (`src/llm.py:1329`, `codex exec
  --json`, auth probe at `:1320`); deliberately excluded from
  `DEFAULT_BACKEND_ORDER` (`:1757`). Enabling it is config
  (`model.backend_order`) or explicit `backend=` — no new code.
- **OpenRouterAdapter is not text-only** — it inherits full OpenAI-style
  `tools`/`tool_choice` emission and tool-call parsing
  (`src/llm.py:1638-1665`). Key via `OPENROUTER_API_KEY`.
- **Verdict-agreement comparator exists**: `src/validation_shadow.py`
  already logs free-vs-paid agreement / false_pass / false_fail with
  paid-as-ground-truth. This is spike step 4, pre-built.
- **ROI reporter exists**: `src/validator_roi.py` (`python3 -m
  validator_roi --json`) — per-tier latency, paid-calls-skipped, USD
  saved.
- **Latency breaker exists and is wired**: `local_models.py`
  `max_latency_ms` (default 15000) + `latency_guard_tripped()`, gating
  the validation ladder at `src/step_exec.py:1350-1358`. The cheap-route
  lane can reuse the identical pattern.
- **Replay material exists**: record-mode writes
  `<run-dir>/build/calls/call-NNNNN.json` with prompt, response,
  tokens, and a caller-stamped `purpose` label (classify/cuts/routing/…)
  — enough to replay per call class. Caveat: system/user split and tool
  schemas are flattened into one prompt string, so native-tool replay
  needs light re-derivation from `purpose`.
- **The one real gap**: per-tier model strings are **hardcoded** in
  `_MODEL_MAP` (`src/llm.py:173-202`), not config-overridable. The
  brief's "config recipe to flip it on" needs a small addition first —
  e.g. `model.tier_map.<backend>.<tier>` config keys layered over
  `_MODEL_MAP` defaults (read in `resolve_model`). ~30 lines + tests,
  still inside the no-re-architecture non-goal.
- **402 mechanics**: no credit probe exists; backend "availability" is
  key-presence only (`src/llm.py:1803-1821`). The 402 surfaces at
  runtime through FailoverAdapter and is correctly classified
  BILLING_ACTIONABLE (`src/llm_errors.py:98-102`). Since OpenRouter sits
  3rd in the default order, the stale key only bites on failover —
  funding it fixes this without config changes; alternatively drop it
  from `model.backend_order` until funded.

## Revised spike plan (supersedes steps 1–6 above)

1. **Fund OpenRouter $20** (one transaction; 5.5% + $0.80 min fee makes
   $5 top-ups ~16% overhead). This alone clears the 402.
2. **Add the config-overridable tier map** (`model.tier_map.*`) — the
   only code change. Everything else is config + existing tooling.
3. **Replay per call class from `calls/` ledgers** (validation-ladder
   verify, decompose/cuts, classify/routing) against: baseline claude -p
   vs `deepseek/deepseek-v4-flash` + `openai/gpt-oss-120b` (cheap class)
   and `minimax/minimax-m3` + `deepseek/deepseek-v4-pro` (planning
   class), using `:exacto` / `require_parameters` for structured calls.
   Score with the validation_shadow agreement pattern; latency via the
   existing breaker thresholds. Expected spend: cents — at $0.03–0.45/M
   input these classes are ~100x cheaper than the Sonnet-class calls
   they'd replace; the question is purely verdict agreement.
4. **Codex lane**: run one build-class goal with
   `backend_order: [codex]` for the worker-exec adapter (adapter
   exists); also probe `codex exec --output-schema` on one structured
   call class.
5. **Hybrid dress rehearsal**: one full Manti + one research goal with
   tier_map pointing non-agentic classes at the OpenRouter winner;
   compare run cards vs the 2026-07-11 baseline (6 steps / 16m43s /
   $1.52) via validator_roi.
6. **Optional follow-on ($10, only if 3–5 look good)**: one month of
   opencode Go as the Lane-A-agentic trial — `opencode run --format
   json` wrapped in a subprocess adapter shaped like CodexCLIAdapter.
   This replaces the brief's "opencode+Zen BYO" idea (Zen per-token is
   at-cost, no edge over OpenRouter; the Go flat sub is the novel part).

**Dropped**: Featherless (concurrency/context math fails our shape);
direct Fireworks account (reach it via OpenRouter provider-pinning).

**Policy watch**: (a) Anthropic's paused programmatic-use repricing —
promised advance notice; if it lands, Lane A absorbs the non-agentic
volume and codex absorbs overflow exec steps, which is exactly the
hybrid this spike derisks. (b) Weekly-limit promo expiry ~Jul 13.

**Flagged as unverified** (secondary sources only): OpenRouter $5
minimum top-up figure; Fireworks RPM/tier numbers; Codex weekly-cap
values (OpenAI publishes "may apply" only); Featherless $10 Basic tier
(absent from live pages, possibly discontinued); "Fable 5 metered
separately under Max" (single source cluster).
