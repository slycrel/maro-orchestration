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

### Apple Silicon (MLX) — framework-managed

```bash
scripts/local-validator.sh setup                       # uv venv + mlx-lm
scripts/local-validator.sh start                       # serve VibeThinker-3B on :8088
scripts/local-validator.sh status                      # check it's loaded
```

### Linux (Ollama)

```bash
ollama pull <model>        # e.g. a small reasoning/coder model
# ollama serve already exposes /v1
```

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
  local_max_tokens: 2048        # floor; reasoning models need room to finish <think>
  auto_verify: true             # default the ralph verify loop ON when a local
                                # validator is available (free). false to opt out.
```

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
- **Measure before you trust it.** Run the local validator in *shadow* against
  the paid validator on historical runs and compare agreement (the same pattern
  as `src/navigator_shadow.py --agreement`) before relying on it for a given
  class of step. See the deep-eval task in `BACKLOG.md`.

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
