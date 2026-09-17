# pcd-sidecar

A small local HTTP server that gives Maro a drop-in alternative to
TypeSafe's Jev `/v1/systemone` API, scoring constrained decisions with a
small open model instead of a hosted one. It never generates free text: it
prefills the prompt once and scores every candidate *answer* directly from
the model's logits (Parallel Constrained Decoding).

## Wire contract

`POST /v1/systemone`

Request:
```json
{
  "model": "any-string",
  "state": "string | object | array",
  "questions": {
    "<id>": { "...one of the three Question shapes below..." }
  }
}
```

Question types:

- **noul** — `{"type":"noul","instructions":str,"criteria":{"true":str,"false":str}?}`
  → `{"type":"noul","noul":<p(yes) 0..1>}`
- **choice** — `{"type":"choice","instructions":str,"criteria":{option: description|null, ...}}`
  → `{"type":"choice","choice":opt,"confidence":c,"probabilities":{opt:p,...}}` (sums to 1)
- **score** — `{"type":"score","instructions":str,"criteria":[level0, level1, ...]}` (ordered)
  → `{"type":"score","score":<expected index, float>,"confidence":c,"legend":{"0":level0,...},"probabilities":{"0":p,...}}`

Response:
```json
{"model": "<the model this sidecar actually loaded>", "answers": {"<id>": <answer>}, "usage": {"input_tokens": n, "output_tokens": 0}}
```

`GET /healthz` → `{"ok":true,"model":...,"device":...,"loaded":bool}`

Errors are never a silent fallback answer: malformed requests get `400
{"error": msg}`, inference failures get `500 {"error": msg}`.

All questions in one request are evaluated independently against the same
`state` — no cross-field coherence is assumed or attempted, matching how
Jev itself works.

## Engine: how scoring works

For each question:

1. Build a chat-templated prompt: **system** = the question's instructions
   plus a rendered legend of the option/level descriptions; **user** = the
   state, rendered as compact JSON (or passed through raw if it's already a
   string); **assistant prefix** = `"<question id>: "`.
2. Tokenize `(prompt + candidate)` **independently for every candidate**
   (candidates = `"yes"/"no"` for noul, the option keys for choice, the
   level strings for score).
3. Find the longest token prefix every candidate's tokenization actually
   shares (not the prompt's standalone token count — see "known limits"
   below for why that distinction matters). Prefill the model on that
   shared prefix once, producing a KV cache.
4. Batch the diverging suffixes (one row per candidate) and run a single
   forward pass reusing the prefilled cache (`DynamicCache.batch_repeat_interleave`),
   producing the logits needed to score every candidate's remaining tokens
   in one pass.
5. Sum each candidate's per-token log-probabilities → one score per
   candidate for the *complete* candidate sequence.
6. **PMI correction** (off by default, `PCD_PMI=1` to enable): divide each
   candidate's likelihood under the real state by its likelihood under a
   neutral, state-free version of the same prompt (`"(no state given)"`).
   The state-free baseline for a given `(model, question)` never changes
   with `state`, so it's computed once and cached.
7. Softmax the (optionally PMI-corrected) scores → `probabilities`.
   `noul` = `p("yes")`. `score`'s `score` field = the probability-weighted
   expected index. `confidence` = `1 - normalised Shannon entropy` of the
   probability distribution (0 = uniform/maximally unsure, 1 = all mass on
   one candidate) — a documented statistic, not a calibrated confidence;
   the raw `probabilities` are always included so callers can compute
   their own.

### Why complete-sequence scoring, not first-token

Upstream prior art (`harshatheg/Qwen-2.5-1B-RLCD`) ranks candidates by
their *first* token, and when two candidates share a leading token
(a "token-prefix collision"), it clamps confidence to a fixed 0.75 floor
and synthesizes the sibling probabilities — fabricated numbers, not a
measurement. It also falls back to `choices[0]` on certain failures,
silently returning the first option instead of erroring. This sidecar
never does either: every candidate's complete token sequence is scored
and summed, so a shared first token just means the two candidates diverge
later in the sum — no special case, no fallback. Any real scoring failure
(e.g. two candidates tokenizing identically) raises and becomes a 500.

### Why the shared prefix is computed, not assumed

A naive version of "prefill once" tokenizes the prompt alone, then tokenizes
each candidate alone, and concatenates. That silently reintroduces the
collision bug above: a tokenizer is not guaranteed to segment `"prompt"`
identically in isolation and when immediately followed by different
continuation text. This implementation instead tokenizes `(prompt +
candidate)` fully for every candidate and takes the *longest token
sequence actually common to all of them* as the shared KV-cache prefix.
Correctness doesn't depend on the tokenizer's segmentation being stable
across contexts; efficiency does (a shorter common prefix just means less
of the prompt gets shared, not a wrong answer).

### PMI rationale

Raw summed log-probability biases toward short strings and away from
multi-token option words ("surface form competition" — Holtzman et al.,
2021). Dividing by the same candidate's likelihood under a state-free
version of the same question (domain-conditional PMI) removes the part of
each candidate's score that's just "how easy is this string for the model
to say", leaving only the part driven by the actual state. The baseline is
cacheable per `(model, question)` because it never depends on `state`.

### Known limits

- **Independence between questions.** Every question in a request is
  scored against the same `state` in isolation; there's no shared
  reasoning or consistency check across questions in one call (this
  matches Jev's own behavior).
- **Softmax ≠ demonstrated calibration.** The `probabilities` are a
  softmax over log-likelihoods, not a value that's been checked against
  real-world frequencies. Treat them as a ranking signal with a rough
  confidence flavor, not a probability that N% of "0.7" judgments are
  correct.
- **PMI is a correction, not a proof.** It removes one well-known bias
  (surface form competition) but is not a general calibration guarantee.
  `PCD_PMI` exists specifically so PMI-on vs PMI-off can be compared on
  a labelled set (see `replay.py`); it is off until that comparison favours it.
- **Small model, small context.** The default model
  (`Qwen/Qwen2.5-1.5B-Instruct`) is chosen for CPU-feasible latency, not
  peak judgment quality.

## Running locally

```bash
cd tools/pcd-sidecar
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python run_server.py
# in another shell:
./smoke.sh http://127.0.0.1:8765
```

## Deploying (systemd --user)

```bash
./deploy/deploy.sh <ssh-host>   # rsyncs the sidecar + installs deploy/pcd-sidecar.service
curl http://<host>:8765/healthz
```

The service file assumes a venv already exists at `~/pcd/.venv` on the
remote with `torch`/`transformers`/`accelerate`/`numpy` installed (CPU
wheels: `pip install --index-url https://download.pytorch.org/whl/cpu torch`).

## Env vars

| var           | default                        | meaning                                              |
|---------------|---------------------------------|-------------------------------------------------------|
| `PCD_MODEL`   | `Qwen/Qwen2.5-1.5B-Instruct`    | HF model id to load                                    |
| `PCD_DEVICE`  | `auto`                          | `auto` (cuda > mps > cpu), or `cuda`/`mps`/`cpu`/`mlx` |
| `PCD_DTYPE`   | (auto per device)               | `bf16`/`fp16`/`fp32` override                          |
| `PCD_PORT`    | `8765`                          | listen port (binds `0.0.0.0`)                          |
| `PCD_THREADS` | (torch default)                | `torch.set_num_threads(...)` on CPU                    |
| `PCD_PMI`     | off                             | `1`/`true` enables the PMI correction (off since 2026-09-17: the most over-asserting rule on benign documents in the replay) |
| `PCD_BACKEND` | `torch`                         | `torch` or `mlx` (Apple Silicon only, best-effort)      |

## Testing

```bash
pip install pytest
pytest -q                         # unit tests, no network, no real model
./smoke.sh http://<host>:8765     # one live request per question type
python replay.py http://<host>:8765   # labelled-corpus agreement report (stdout only)
```

`replay.py` runs `tests/fixtures/validation_cases.json` through a live
sidecar as a judge request and prints an agreement table — it does not
write any result numbers back into the repo.

## mlx backend (optional, Apple Silicon)

`pcd_sidecar/engine_mlx.py` implements the same `Engine` interface via
`mlx`/`mlx_lm`. It does **not** share a prefilled KV cache across
candidates the way the torch engine does (mlx_lm has no cheap batch-expand
cache primitive) — each candidate's full sequence is scored independently.
Same correctness, no "prefill once" compute saving. Select it with
`PCD_BACKEND=mlx` or `PCD_DEVICE=mlx`.

This backend was smoke-tested end to end on an M1 Mac (fast: ~1.7s p50 for
the 3-question benchmark in `replay.py`'s harness) but is **not** the
sidecar's deployment target. Jeremy's read on the underlying PCD-with-a-
1.5B-model approach is that it doesn't hold up well enough on that hardware
to treat as a drop-in Jev replacement there — so this backend stays an
optional, experimental provider, not something to deploy standing. The
sidecar's real home is the CPU (torch) deploy described above.
