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
Tier 0  free, deterministic   claim_verifier · settled_by_command · tests · constraints
Tier 1  free, LOCAL model     local validator → verdict + confidence        ← this feature
Tier 2  paid                  paid validator (the step's adapter)            ← escalation target
Tier 3  paid ensemble         quality_gate.run_llm_council (3-persona trio)
```

`verify_step()` runs Tier 1 first when `validate.local_models` is set. If the
local verdict's `confidence >= validate.min_certainty` it is **decisive** and
the paid path is skipped entirely (zero cost). Below that threshold the verdict
is **UNDECIDED** and we escalate to the paid `adapter`. The returned dict gains
`decision` (`LOCAL_PASS` | `LOCAL_FAIL` | `ESCALATED`) and `source`.

A dead endpoint or empty result surfaces as confidence `0.0`, which is below any
threshold → automatic escalation. Nothing ever blocks on the local model.

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
scripts/local-validator.sh pull mlx-community/VibeThinker-3B-8bit   # download the model
# no manual `start` needed — the loop spins it up on demand.
# `start`/`stop`/`status` exist for dev (keep it warm across back-to-back runs).
```

### Linux (Ollama)

```bash
ollama pull <model>        # e.g. a small reasoning/coder model
# ollama is its own daemon and exposes /v1; orchestration does NOT manage it.
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
until ready. Only the **mlx** runtime is managed (Ollama runs its own daemon).
Opt out with `validate.autostart: false`.

**Idle reaper (backstop).** For validations that happen outside a managed run,
the server is also reaped after `idle_shutdown_secs` of inactivity (and on
process exit). Run-scoped spin-ups suppress this — the run owns teardown — so it
only applies to ad-hoc/lazy use.

## Configuration (`~/.poe/workspace/config.yml`)

```yaml
validate:
  # 0..n local models, priority order. Empty/unset = paid validation (default).
  local_models:
    - mlx-community/VibeThinker-3B-8bit
  runtime: auto                 # auto | mlx | ollama
  endpoint: ""                  # override; else derived from runtime
  min_certainty: 0.6            # below this, local verdict is UNDECIDED → escalate
  escalation: cheap             # cheap (one paid gate) | council (3-persona trio)
  local_max_tokens: 2048        # OUTPUT ceiling; reasoning models need room for <think>
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
ingesting it wholesale. That's a tool-using validator, which a small specialist
like VibeThinker is weak at — so it's queued as a deep-eval direction in
`BACKLOG.md`, not the default path.

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
  # gather data: enable in ~/.poe/workspace/config.yml, run real goals, then:
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

### Reference + alternatives

| Model | Backend | Footprint | Role fit |
|-------|---------|-----------|----------|
| `mlx-community/VibeThinker-3B-8bit` | MLX | ~3.4 GB | **Reference.** Verifiable-reasoning specialist (code/math). |
| `mlx-community/VibeThinker-3B-4bit` | MLX | ~1.9 GB | Same model, smaller/faster, slightly lower fidelity. |
| `mlx-community/VibeThinker-1.5B-mlx-4bit` | MLX | ~1.0 GB | For RAM-constrained boxes; weaker judge. |
| a Qwen2.5-Coder / reasoning model via Ollama | Ollama | varies | Linux prod box; pick a coder/reasoning tune, not a chat model. |

## Hardware — can a "generally modern machine" run this?

Yes, for the 3B reference model on any reasonably current machine:

- **RAM**: 8-bit 3B needs ~3.4 GB resident (4-bit ~1.9 GB). 16 GB total RAM is
  comfortable; 8 GB works with the 4-bit quant.
- **Apple Silicon (MLX)**: any M-series. Measured ~90 tok/s generation and
  ~5–11 s per validation on an M1 (a full `<think>` + verdict). Free, but
  slower than a Haiku call — the win is **token cost, not latency**.
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

# 2. download + warm the model (~3.4 GB on first run, cached afterward)
scripts/local-validator.sh pull mlx-community/VibeThinker-3B-8bit

# 3. start the server (defaults to VibeThinker-3B-8bit on :8088)
scripts/local-validator.sh start

# 4. confirm it's loaded
scripts/local-validator.sh status
poe-doctor                       # "Local validator: mlx @ ... — active: ..."
```

Then enable it in `~/.poe/workspace/config.yml`:

```yaml
validate:
  local_models: ["mlx-community/VibeThinker-3B-8bit"]
  runtime: mlx
```

To keep it running across reboots, wrap `local-validator.sh start` in a launchd
agent (macOS) or systemd unit (Linux). On the Linux prod box, use Ollama
instead — `ollama pull <coder-model>` and set `runtime: ollama`.

### Caveats
- Reasoning models emit a `<think>` trace; `local_max_tokens` must be high
  enough to reach the final JSON verdict. The adapter floors it (default 1024);
  don't set it tiny or `content` comes back empty and every call escalates.
- First call after `start` loads the model into memory (a few seconds); keep
  the server warm rather than starting per-validation.
