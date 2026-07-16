---
status: living
---

# Local Validator — zero-cost first-pass validation

Poe's highest-volume LLM call is **validation** ("did this step result satisfy
the goal?"). Those calls are frequent and mostly easy, so paying a frontier API
for each is the biggest avoidable token sink. The local validator lets a model
running on the same box judge first **for free**, escalating to a paid model
only when the local judge is *uncertain*.

This is **optional and additive**. With no local models configured, validation
behaves exactly as before (paid path). See `src/local_models.py`.

## The validation ladder

```
Tier 0   free, deterministic   claim_verifier · settled_by_command · tests · constraints
Tier 1   free, HOSTED          Groq/Gemini free API tier (hosted_free.py)     ← when enabled + keyed
Tier 1b  free, LOCAL model     local validator → verdict + confidence         ← this feature (backup)
Tier 2   paid                  paid validator (the step's adapter)            ← escalation target
Tier 3   paid ensemble         quality_gate.run_llm_council (3-persona trio)
```

**Free-tier order (decreed 2026-07-16, Jeremy):** hosted-free first, local as
the availability backup — "slow + local seems better than a network API call
fail for whatever reason." When `validate.hosted_free.enabled` is on and a
`GROQ_API_KEY`/`GEMINI_API_KEY` is present, the hosted tier judges first
(stronger models, ~1–2s). The local tier is consulted only when the hosted
tier is inert (disabled, no keys, all providers breaker-tripped) or fails to
produce any verdict (network failure / unparseable output). A *genuine*
hosted UNDECIDED escalates straight to paid — the weaker local model doesn't
overrule a stronger model's uncertainty. With hosted-free off (the
fresh-install default), the local tier remains the first free rung, unchanged.

If a free verdict's `confidence >= min_certainty` (local:
`validate.min_certainty`; hosted: `validate.hosted_free.min_certainty`) it is
**decisive** and the paid path is skipped entirely (zero cost). Below that
threshold the verdict is **UNDECIDED** and we escalate to the paid `adapter`.
The returned dict gains `decision` (`HOSTED_FREE_PASS` | `HOSTED_FREE_FAIL` |
`LOCAL_PASS` | `LOCAL_FAIL` | `ESCALATED`) and `source`.

A dead endpoint or empty result surfaces as confidence `0.0`, which is below any
threshold → automatic escalation (hosted `0.0` first falls back to the local
backup). Nothing ever blocks on a free validator.

## Runtimes

One OpenAI-compatible HTTP adapter serves both:

| Runtime | Where | Endpoint | Notes |
|---------|-------|----------|-------|
| `mlx`    | Apple Silicon | `http://127.0.0.1:8088/v1` | `mlx_lm.server`, in a uv venv (default here) |
| `ollama` | Linux / anywhere | `http://127.0.0.1:11434/v1` | `ollama serve` (default on the prod box) |

`validate.runtime: auto` picks `mlx` on Apple Silicon, else `ollama`.

## Setup

You install the runtime + model once; the orchestration starts/stops the server
itself at run time (see **Lifecycle** below). No OS service.

### Apple Silicon (MLX)

```bash
scripts/local-validator.sh setup                       # uv venv + mlx-lm (one-time)
scripts/local-validator.sh pull mlx-community/VibeThinker-3B-4bit   # download the model
# no manual `start` needed — the loop spins it up on demand.
# `start`/`stop`/`status` exist for dev (keep it warm across back-to-back runs).
```

### Linux (Ollama)

```bash
ollama pull <model>        # e.g. a small reasoning/coder model
# An external Ollama daemon is reused; when none is reachable and autostart is
# enabled, Maro can launch/reap `ollama serve` for the run.
```

## Lifecycle (orchestration-managed, not an OS service)

The local model is a resource the orchestration owns — **not** a launchd/systemd
"always-on" service.

**Run-scoped (primary).** `run_agent_loop` is wrapped so that, when a run will
use the local validator, it **spins the model up once at the start of the run and
tears it down at the end — on completion or failure** (`managed_for_run`). The
server stays warm for the whole run (no reaping between steps), and only the run
that actually spawned it reaps — a reused/external server, or a parent run's
server during nested/recovery calls, is left running.

`ensure_validator_running()` does the work: it **reuses** any server already
serving a configured model (ours, or one started with `local-validator.sh` —
never duplicated), else **spins up** `mlx_lm.server` as a managed child and waits
until ready. Both **mlx** and **ollama** can be managed; an already-running
external server is reused and never reaped by Maro.
Opt out with `validate.autostart: false`.

**Idle reaper (backstop).** For validations that happen outside a managed run,
the server is also reaped after `idle_shutdown_secs` of inactivity (and on
process exit). Run-scoped spin-ups suppress this — the run owns teardown — so it
only applies to ad-hoc/lazy use.

## Configuration (`~/.maro/workspace/config.yml`)

```yaml
validate:
  # 0..n local models, priority order. Empty/unset = paid validation (default).
  local_models:
    - mlx-community/VibeThinker-3B-4bit
  runtime: auto                 # auto | mlx | ollama
  endpoint: ""                  # override; else derived from runtime
  min_certainty: 0.6            # below this, local verdict is UNDECIDED → escalate
  escalation: cheap             # cheap (one paid gate) | council (3-persona trio)
  local_max_tokens: 2048        # OUTPUT ceiling; reasoning models need room for <think>.
                                 # Can also be a dict keyed by model id with a "default"
                                 # fallback, e.g. {llama3.2:3b: 256, default: 2048} — see
                                 # "Per-model tuning" below. Omit entirely to use the
                                 # built-in measured floors for models on this box.
  max_input_chars: 6000         # INPUT window the local validator sees of the result
  auto_verify: true             # default the ralph verify loop ON when a local
                                # validator is available (free). false to opt out.
  autostart: true               # orchestration may spin the mlx server up on demand
  idle_shutdown_secs: 300       # reap the managed server after this much idle (0=never)
  mlx_python: ""                # interpreter for mlx_lm.server (default: repo .venv-mlx)
```

### Two limits, and why they're different

The validator has an **output** budget and an **input** window — don't conflate them:

- **`local_max_tokens` (output ceiling).** How many tokens the validator may
  *generate*. A reasoning model's `<think>` trace plus its JSON verdict must fit,
  or `content` comes back empty and the verdict escalates. This is a *ceiling*,
  not a cost dial — you pay for tokens actually generated (the model stops on its
  own when done), so setting it generously is near-free. It only guards runaways.
- **`max_input_chars` (input window).** How much of the step result the validator
  *sees*. The paid path uses a cost-conscious 1200 chars; the **free** local
  validator can afford much more (default 6000) — judging a fuller view beats
  judging the first 1200 chars. Bounded by the model's context window.

For **very large artifacts** (a multi-KB file), neither knob is ideal — stuffing
the whole thing into context is wasteful. The right tool there is an *agentic
verifier* that reads the artifact selectively (grep/read a temp file) rather than
ingesting it wholesale. That's a tool-using validator, which a small reasoning
specialist (e.g. VibeThinker) is weak at — so it's queued as a deep-eval
direction in `BACKLOG.md`, not the default path.

### Per-model tuning (BACKLOG #10, 2026-07-13 measurement)

`local_max_tokens` is per-model, not a single global number. `local_max_tokens_for(model)`
(`src/local_models.py`) resolves it in this priority order:

1. `validate.local_max_tokens` as a **dict** with the model explicitly listed →
   that value.
2. The dict's own `"default"` key, if present and the model isn't listed.
3. `validate.local_max_tokens` as a **bare int/string** → that value, for every
   model (the pre-2026-07-13 global-floor behavior — still fully supported).
4. No config at all → the built-in, empirically-measured table for models
   installed on this box (`_MEASURED_MODEL_FLOORS`), else the generous 2048
   safety net for anything not in that table (an unmeasured or reasoning model).

**Why 256, not 2048, for llama3.2:3b / qwen-hermes:latest / qwen2.5-coder:3b:**
a real 45-call sweep (3 models x 3 floors [128/256/2048] x 5 realistic
step-result payloads, including a ~6000-char payload at the input_char_budget
ceiling and a vague/RETRY-shaped result) found **zero** empty-content
verdicts, **zero** JSON-parse failures, and **zero** `finish_reason="length"`
truncations at *any* floor for *any* of the three models — 100% decisive-local
across the board. Max output tokens observed: 63. `ollama show` confirms none
of the three report a "thinking" capability, so there's no `<think>` trace to
overrun in the first place — the VibeThinker failure mode this floor exists
for (BACKLOG #10, live 2026-06-21) simply doesn't apply to what's installed
today. 256 gives ~4x margin over the observed max; it costs nothing over 2048
in practice (a decisive model stops at EOS regardless of the ceiling) but is
a deliberately-chosen number rather than an arbitrary one. If VibeThinker (or
another reasoning model) comes back, it isn't in the table, so it gets the
2048 safety net automatically — no config edit required.

### Auto-verify

When a usable local validator is available, the per-step **ralph verify loop**
defaults **on** — verification is free, so it should run. This is equivalent to
prefixing every goal with `verify:`. It only activates when a configured model
is actually loaded at the endpoint (so a misconfigured/down validator never
silently routes verification to the paid path). Set `validate.auto_verify: false`
to keep verification opt-in (via `verify:`/`ralph:`/`--ralph-verify`) even with a
local validator present.

## Validation models — what works, and why

Validation is a **discrimination** task ("does this result satisfy the goal?"),
and the model's training signal matters more than its size.

- **Prefer a verifiable-reasoning / coder model.** Judging "is this code or
  math correct?" is close to what models like VibeThinker-3B (built on
  Qwen2.5-Coder-3B) were post-trained to do. These produce a `<think>` trace
  then a verdict, and they hold up on real step results.
- **Avoid general chat models in this role.** They are *not* tuned to grade and
  fail unpredictably. In testing, `devstral` (a capable general model) judged a
  **correct** `add()` function as FAIL. Bigger ≠ better judge.
- **It's a prior, not an oracle.** The local verdict is the cheap first pass;
  the confidence band exists precisely because a small model will be unsure on
  hard cases. Tune `min_certainty` up if you want more escalation to paid.
- **Measure before you trust it.** The shadow-eval harness
  (`src/validation_shadow.py`, shipped 2026-06-22) runs the local validator
  *and* the paid validator on the same step result, decide-only, and logs both:

  ```bash
  # gather data: enable in ~/.maro/workspace/config.yml, run real goals, then:
  #   validate: { shadow_eval: true }   # off by default — the decisive path
  #                                      # makes an EXTRA paid call (real spend)
  python3 -m validation_shadow --agreement
  ```

  It prints per-step-class agreement %, the two error directions, and a
  confidence-calibration table. The error directions are **not symmetric**:
  - `false_pass` = local PASS / paid FAIL — **the dangerous one** (a real defect
    slips through). Watch this; it should stay at 0.
  - `false_fail` = local FAIL / paid PASS — merely a wasted escalation (cost, not
    correctness).

  **First live data (2026-06-23, qwen2.5-coder:3b, n=29):** 96.6% agreement,
  **0 false_pass across every class.** analyze/exec_command/synthesize/
  read_artifact all 100%; `general` 94.1% (one false_fail — local too strict on a
  file-save).

  **Larger batch (2026-06-24, n=42):** 92.9% agreement, and **the first
  `false_pass` appeared** — in `general`, at local confidence **1.00**. The step
  was "list the skills/ directory and save the listing to
  `artifacts/skills-listing.txt`"; the worker saved to a *different* path and
  narrated success. Paid FAILed it (requirement unmet); local PASSed. The
  concrete classes held: exec_command (n=5), analyze (n=5), synthesize (n=3) all
  100% / 0 false_pass; read_artifact (n=4) 75% but every miss a *false_fail*
  (safe). Per-class table:

  | class | n | agree | false_pass | false_fail |
  |---|---|---|---|---|
  | exec_command | 5 | 100% | 0 | 0 |
  | analyze | 5 | 100% | 0 | 0 |
  | synthesize | 3 | 100% | 0 | 0 |
  | read_artifact | 4 | 75% | 0 | 1 |
  | general | 24 | 91.7% | **1** | 1 |

  **Routing conclusion — do NOT set per-class `min_certainty` yet.** The lever
  the data points at is *not* a confidence threshold: the lone false_pass fired
  at conf 1.00, so no certainty gate would have caught it. It's a
  requirement/side-effect-completion miss — the text-only local validator can't
  see that the artifact never landed at the asked-for path. Same
  provenance-blindness root as the fabricated-input bug (`verify_step` sees only
  strings). The safe concrete classes *could* eventually be trusted more (lower
  `min_certainty` → fewer paid escalations), but n=3–5 is too small to justify
  it. Keep global `min_certainty: 0.6`; treat `general`/save-shaped steps as the
  risk class; the real fix is provenance verification (the closure-verdict net,
  `BACKLOG.md`). See the per-class-routing item in `BACKLOG.md`.

  **Provenance verification SHIPPED (2026-06-24) — closes this gap.** The
  conclusion above ("the real fix is provenance verification") is now built:
  deterministic done≠achieved guards in `handle.py`, default on, free
  (no model call), wired into both verdict paths via `_provenance_missing(goal)`.
  Three checks — dir-qualified output (strict, exact path), bare-filename output
  (lenient, basename anywhere reasonable), and input (strict, local non-transient
  path must exist). The n=42 `false_pass` (worker saved to a different path,
  local PASS@1.00) is exactly what the output guard now demotes. Gates:
  `validate.output_provenance`, `validate.input_provenance`. This is the lever the
  shadow-eval data pointed at — *not* a confidence threshold. See BACKLOG_DONE.md.

### Reference + alternatives

| Model | Backend | Footprint | Role fit |
|-------|---------|-----------|----------|
| `mlx-community/VibeThinker-3B-4bit` | MLX | ~1.8 GB | **Apple Silicon reference.** Best measured size/latency/quality trade on the M1 Max. |
| `mlx-community/VibeThinker-3B-8bit` | MLX | ~3.4 GB | Same specialist at higher precision; measured slower here with no gain on the bounded eval. |
| `mlx-community/VibeThinker-1.5B-mlx-4bit` | MLX | ~1.2 GB resident / 844 MB disk | Runs comfortably, but failed the canonical judge eval; do not use for this role. |
| `qwen2.5-coder:3b` | Ollama | ~1.9 GB disk | Very fast, but rejected for validation after two unsafe false-passes on the shared corpus. Remains the only latency-viable local candidate on the 2014 Ubuntu box (see Linux measurement below). |
| `hf.co/mradermacher/VibeThinker-3B-GGUF:Q4_K_M` | Ollama (GGUF) | ~1.9 GB disk | The reference model's Linux/CPU build. **Fails the latency gate on the 2014 Ubuntu box** — 13/14 corpus verdicts hit the 60s generation timeout; the one completed took 55.8s vs the 15s breaker. Not viable there; unmeasured on faster Linux hosts. |

### M1 Max measurement (2026-07-14)

The earlier negative ROI data in this document came from a **2014 Mac mini
running Ubuntu**, not this machine. A fresh measurement on the 10-core,
64 GB M1 Max used Maro's real `LocalValidatorAdapter` + `VerificationAgent`
protocol, not a raw chat prompt:

Every row below was replayed at `validate.min_certainty: 0.6` through the
production adapter and verifier against
the same committed 14-case corpus (`tests/fixtures/validation_cases.json`; seven
PASS and seven RETRY cases). Reproduce one row with
`scripts/validator-bakeoff.py --model MODEL --endpoint URL`.

| Candidate | Raw accuracy | Decisive coverage / accuracy | Unsafe decisive false-passes | Exact-protocol latency | Judgment |
|---|---:|---:|---:|---:|---|
| VibeThinker-3B-4bit (MLX, ~1.8 GB) | **14/14** | **14/14; 100% / 100%** | **0** | **8.83s average** | **Use as local validator** |
| VibeThinker-1.5B-4bit (MLX, 844 MB disk) | 8/14 | 3/14; 21% / 100% | 0 | 14.43s average | Reject: too little useful coverage and slower than 3B/4-bit |
| VibeThinker-3B-8bit (MLX, ~3.4 GB) | 12/14 | 13/14; 93% / 92% | 1 | 13.46s average | Reject: larger, slower, and less safe |
| qwen2.5-coder:3b (Ollama, ~1.9 GB disk) | 12/14 | 14/14; 100% / 86% | 2 | **0.81s average** | Reject: fast but blessed a read-only violation and an explicitly failing test run |

The 3B/4-bit result is strong but bounded: fourteen labeled examples show the
model is worth enabling behind the existing certainty, deterministic-provenance,
and latency gates. The follow-up 1.5B run establishes why the recommendation
does not go smaller merely to minimize footprint: its low-confidence nominal
passes would correctly escalate, but then the local call adds latency without
avoiding paid work. These results do **not** prove a 3B model should replace the
main planner/executor. Keep local use narrow to first-pass validation; let hard
or uncertain work escalate. The cached Ollama models occupy roughly 44 GB on
disk but are not configured in Maro and did not beat the 1.8 GB specialist
for this job. The old 2014 Ubuntu Mac mini remains a poor target: this verdict
is specifically for Apple Silicon/MLX. A Linux/Ollama deployment still needs an
on-box latency and safety replay before it is enabled there.

### Linux (2014 Ubuntu Mac mini) measurement (2026-07-16)

That replay ran (Jeremy: "we should use that new 4 bit quantized model"):
`hf.co/mradermacher/VibeThinker-3B-GGUF:Q4_K_M` via the production-capped
Ollama daemon (cores 2,3, nice 12), same 14 cases, exact adapter/verifier
protocol. **Fails the latency gate decisively:** 13/14 verdicts hit the
adapter's 60-second generation timeout without producing a verdict; the one
that completed took 55.8s (correct, decisive, conf 0.9) — ≥4× the 15s
breaker. Zero unsafe decisive false-passes, but decisive coverage was 1/14,
all of it paid for in wall-time. This converts the "reasoning models are a
CPU dead-end here" hypothesis into a measurement. Standing posture on that
box: `qwen2.5-coder:3b` stays as the fast local first-pass behind the
deterministic Tier-0 guards and the latency breaker; the quality upgrade
lane there is hosted-free validation (Groq/Gemini), not a local reasoning
model. Raw rows: `research/validator-bakeoff-linux-2026-07-16.json`.

### Linux qwen parameter sweep (2026-07-16)

Follow-up to Jeremy's "worth looking at the qwen2.5-coder:3b and seeing if
we can find a smaller bit model with similar capabilities there?" Note the
default Ollama `qwen2.5-coder` tags are **already Q4_K_M (4-bit)** — lower
bit-widths (Q2/Q3) save little on a CPU-compute-bound box and shed quality
fast, so the real lever is parameter count. Same daemon, corpus, and
protocol as above:

| Candidate | Raw accuracy | Decisive coverage / accuracy | Unsafe decisive false-passes | Avg latency (warm daemon) | Judgment |
|---|---:|---:|---:|---:|---|
| qwen2.5-coder:3b (box baseline) | 12/14 | 14/14; 100% / 86% | 2 | 10.9s (max 30.7s = first-call load) | **Keep.** Only candidate under the 15s breaker with full coverage; its two known false-passes (read-only violation, failing test run) are exactly the cases Tier-0 deterministic guards catch first |
| qwen2.5-coder:1.5b | 7/14 | 10/14; 71% / 60% | 1 | 5.6s | Reject: false-RETRYs correct work (factorial, flatten, honored constraint) and still blessed a failing test run — coin-flip accuracy at half the latency isn't a trade |
| qwen2.5-coder:0.5b | 6/14 | 9/14; 64% / 56% | 3 | 2.4s | Reject: rubber stamp — passed 6 of 7 negative cases (decisively or via low-conf escalation), including path/read-only/test violations |

Capability degrades faster than latency improves: each halving of
parameters roughly halves latency but the model loses the ability to say
RETRY with evidence. The 3b's Linux false-passes are the **same two cases**
it failed on the M1 — consistent cross-platform, so the M1 quality verdict
transfers even though the latency numbers don't (0.81s on M1 vs 10.9s
here). No smaller qwen with similar capability exists at this corpus; 3b
stays the box's local floor. Raw rows:
`research/validator-bakeoff-linux-qwen-{3b,1-5b,0-5b}-2026-07-16.json`.

These are single-sample point estimates from a small, temperature-0.1 corpus
run, not a statistical proof of safety. In particular, zero unsafe passes among
seven negative examples does not establish a zero real-world error rate. The
gated live eval now refuses any sampled decisive false-pass, and production
shadow telemetry remains the broader evidence source; rerun the corpus after
model, prompt, runtime, or quantization changes.

## Hardware — can a "generally modern machine" run this?

Yes, for the 3B reference model on any reasonably current machine:

- **RAM**: the 4-bit reference peaked at 1.83 GB on the M1 Max; 16 GB total RAM
  is comfortable and 8 GB is plausible for validator-only use.
- **Apple Silicon (MLX)**: any M-series. On this M1 Max the 4-bit reference
  averaged 8.83s per exact-protocol validation, competitive with the recorded
  ~6.5s paid path while avoiding API cost and egress.
- **Linux/x86 (Ollama)**: runs on CPU; a small GPU helps. Use a quantized
  build to keep memory and latency reasonable.
- The model runs as a **separate process**, so it doesn't load any heavy deps
  into the framework interpreter and doesn't compete with it for the GIL.

## Installing the reference model (VibeThinker-3B on MLX)

Requires [`uv`](https://docs.astral.sh/uv/) and Apple Silicon. The script
creates an isolated venv (Python 3.12) so it's independent of the system Python.

```bash
# 1. one-time: create the runtime venv and install mlx-lm
scripts/local-validator.sh setup

# 2. download + warm the model (~1.8 GB on first run, cached afterward)
scripts/local-validator.sh pull mlx-community/VibeThinker-3B-4bit

# 3. start the server (defaults to VibeThinker-3B-4bit on :8088)
scripts/local-validator.sh start

# 4. confirm it's loaded
scripts/local-validator.sh status
poe-doctor                       # "Local validator: mlx @ ... — active: ..."
```

Then enable it in `~/.maro/workspace/config.yml`:

```yaml
validate:
  local_models: ["mlx-community/VibeThinker-3B-4bit"]
  runtime: mlx
```

To keep it running across reboots, wrap `local-validator.sh start` in a launchd
agent (macOS) or systemd unit (Linux). On the Linux prod box, use Ollama
instead — `ollama pull <coder-model>` and set `runtime: ollama`.

### Caveats
- Reasoning models emit a `<think>` trace; `local_max_tokens` must be high
  enough to reach the final JSON verdict. The adapter floors it (module
  default 2048 for a model with no measured entry; see "Per-model tuning"
  above) — don't set it tiny or `content` comes back empty and every call
  escalates.
- First call after `start` loads the model into memory (a few seconds); keep
  the server warm rather than starting per-validation.
