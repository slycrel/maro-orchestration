# Go engine defaults — what a fresh install runs

The successor engine's own defaults registry. Source of truth is
`internal/defaults` (a Go table); this file is the human surface. A census
test enforces both directions — a registered default with no row here
fails, and a row here that nothing registers fails
(`internal/defaults:TestEveryDefaultIsDocumented`,
`TestEveryDocumentedKeyIsRegistered`).

Separate from the Python engine's `../docs/DEFAULTS.md` on purpose: that
file's reverse census (`tests/test_defaults_doc.py`) requires a reader in
`src/` for every dotted key in a table row, so a Go key placed there would
fail the Python suite. `../docs/DEFAULTS.md` points here in prose instead.

**The pattern** (inherited, unchanged): a capability defaults **ON** when
it only adds internal evidence or quality, and **OFF** when it
self-modifies, acts outward, spends money, or persists beyond the run.

| Key | Default | Flag | Why this value |
|---|---|---|---|
| `judgment.provider` | `llm` | `--judge-provider` | The incumbent generative judge over the run's own backend. This seam changes how a judgment is asked, not who answers it; a fresh install behaves exactly as before. |
| `judgment.shadow` | (empty) | `--judge-shadow` | **OFF.** A shadow arm reaches the network and spends money on every verdict. Evidence-gathering never turns itself on because the code shipped. |
| `judgment.timeout` | `1m0s` | — | A wire judgment is one small request; past a minute it is a hang, not a slow answer. |
| `judgment.jev.url` | `https://api.typesafe.ai` | — | TypeSafe's System One endpoint. |
| `judgment.jev.model` | `jev-latest` | — | The vendor's moving latest; pinning a version here would rot silently. |
| `judgment.jev.key_name` | `TYPESAFE_API_KEY` | — | A **name** in the secrets store, so the value never reaches a config file, a record, or a log line. |
| `judgment.hosted.url` | `https://generativelanguage.googleapis.com/v1beta/openai/` | `--hosted-url` | The cheap hosted tier, mirroring the Python engine's hosted-free ladder. Groq (`https://api.groq.com/openai/v1`, `llama-3.1-8b-instant`, `GROQ_API_KEY`) is a flag, not a code change. |
| `judgment.hosted.model` | `gemini-flash-lite-latest` | `--hosted-model` | The cheapest tier that cleared the 14-case validation corpus on the Python side (2026-07-16). |
| `judgment.hosted.key_name` | `GEMINI_API_KEY` | `--hosted-key` | A **name** in the secrets store, same rule as the Jev key. |
| `judgment.pcd.url` | `http://192.168.0.50:8765` | `--pcd-url` | The local System One sidecar on the M1: no auth, no spend, optional and experimental. Unreachable is a skipped provider, never a failed run. |

## Providers

`llm` (default) and `hosted` are asked in the versioned prose template
(`judgment-llm/1`) and parsed strictly. `jev` and `pcd` are asked in the
System One wire shape — the bytes recorded are the bytes sent. A wire
provider cannot carry a prose persona lens; the driver refuses that
combination rather than mangling it.

Judgment providers are tool-less and declare that they cannot act outward.
Keys are resolved from the secrets store **by name**, never from the
environment alone, and never printed.
