# Purgatorio r2 Eye 6 — Landscape (REPO-SIDE ONLY)

Probed 2026-07-10, read-only (this file is the only write). **Scope cap,
deliberate:** the external web sweep is 1 day old (2026-07-09 baseline:
LangGraph / CrewAI / OpenHands / Hermes Agent / Letta / two survey pieces) —
NO web research was redone this pass. Everything below is the repo-side half:
what the fix wave (4e6dc1b..97aa5ef, 21 commits) actually changed against the
land-* findings, verified by reading files, greps, `gh run list`, and one
disposition curl (PyPI 404 re-probe — verification of a checklist claim, not
new landscape research). Claimed vs probed named per item.

---

## A. Prior-finding re-verification

### land-01 — PyPI package / version 0.5.0 → **partially-resolved**

- **probed:** pyproject.toml:6-7 still `name = "maro-orchestration"`,
  `version = "0.5.0"`. docs/PUBLISH_CHECKLIST.md:29-35 now has the name check
  ticked: `maro-orchestration` FREE on PyPI, bare `maro` taken (exercise stub),
  `pymaro` is Microsoft's MARO; "publish under maro-orchestration as-is."
  Re-probed `curl https://pypi.org/pypi/maro-orchestration/json` → **404**
  (consistent with "free", still unpublished). Checklist (adopted as living
  1.0 gate in 34241d9) sets version **1.0.0 at tag time** and publish as
  Jeremy's act; build/twine boxes unchecked.
- **status rationale:** the pre-tag half I flagged ("check name availability
  before the tag") is done and evidenced; install remains clone-only and the
  publish itself is deliberately the 1.0 release act. Nothing left here but
  the act.

### land-02 — CI empty directory, no badge, no enforced gate → **partially-resolved**

- **probed:** `.github/workflows/ci.yml` exists (added 2017d42, cites this
  finding): push-to-main + PR trigger, ubuntu-latest, py3.12,
  `pip install -e ".[dev]"`, hook install, `pytest tests/` — a real enforced
  gate, hermetic by conftest design (two fix rounds f215d17/7581510 landed to
  make it so). `gh run list` probed live: last 10 runs on main, HEAD (97aa5ef)
  and the 2 commits before it **green** (~2m40s each); one genuine red at
  6c03068 (2026-07-10 03:47) fixed by the next commit 6 minutes later — the
  gate demonstrably catches real failures.
- **still missing:** README has **zero** badge (`grep 'badge\|workflows/ci'
  README.md` = 0). The evaluator-visible half — "missing badge reads as tests
  probably don't pass" — is unchanged. One `![CI]` line closes this.

### land-03 — No examples/ dir, no root CHANGELOG → **still-open**

- **probed:** `ls examples` → absent; `ls CHANGELOG*` at root → absent (only
  docs/history/CHANGELOG.md). Unchanged by the fix wave.

### land-04 — Internal-facing docs, no help/issues pointer in README → **still-open**

- **probed:** README end-to-end still has no support/help/issues section
  (`grep -iE 'issues|help|support|discussion'` — only `/help` Telegram command
  and unrelated hits). `.github/ISSUE_TEMPLATE/` (bug_report, feature_request)
  exists but **pre-dates the audit** (eedd770) — not a fix-wave disposition,
  and README never points at it. docs/ remains design/audit-heavy.

### land-05 — No streaming completion API → **still-open** (by design)

- **probed:** `grep -in stream src/llm.py` — hits are unchanged in kind:
  subprocess liveness streaming (llm.py:553,716) and `_parse_stream_json`
  CLI-output parsing (llm.py:803); no streaming API to callers. Verdict was
  **watch**/backlog-low; still the right call. No regression.

### land-06 — MCP client invisible in README → **partially-resolved**

- **probed:** `grep -iE '\bmcp\b' README.md` went 0 → 1 hit: README.md:356
  mentions "MCP servers" in passing inside the `user/CONFIG.md` paragraph.
  Config-file wiring is now genuinely documented on the user lane:
  user/CONFIG.md:97-98 (`mcp_servers: npx -y @modelcontextprotocol/...`,
  URL = HTTP transport) + user/README.md:61 table row; heartbeat.py:969-998
  probed — it really parses `mcp_servers` from user/CONFIG.md (workspace
  overlay wins) and calls `registry.load_mcp_server()` per entry.
- **still missing:** MCP appears in no feature list — an evaluator scanning
  "What it does" / "What makes it different" for the MCP checkbox won't find
  it; the load path is heartbeat-startup only. The checkbox-scan complaint
  stands at half strength.

### land-07 — Local/self-hosted model lane invisible → **partially-resolved**

- **probed:** README.md:48 now brags a local-model lane prominently —
  "optional local model (ollama/mlx) as first-pass step judge... 82% of
  recorded step verifications (58/71 ladder events)" (`local_models.py`
  exists; `step_exec.py` has 4 ladder refs — probed). That surfaces local
  models as a *judging* lane, honestly scoped to box evidence.
- **still missing:** the *backend* half — OpenAIAdapter `base_url`
  (llm.py:1516-1519, unchanged) means an Ollama/vLLM server can run the whole
  loop, and the README backend table (README.md:94-100) still lists only
  cloud keys + CLI OAuth lanes. Running Maro entirely local remains
  undocumented (and still untested end-to-end as far as the repo shows).

### land-08 — No OTel/observability exporter → **still-open** (by design)

- **probed:** `grep -r opentelemetry src/` = 0, unchanged. Was
  **watch**/backlog-low; record-mode counter-story is now actually in the
  README (line 47), which strengthens the honest position. No regression.

### land-09 — No Windows story, one-line WSL2 fix suggested → **still-open**

- **probed:** `grep -iE 'windows|wsl' README.md` = 0. README.md:14 still
  "Linux or macOS". The flagged one-line fixed-inline candidate wasn't taken.
  Cosmetic then, cosmetic now.

### land-10 — Self-improvement headline vs idle evolver → **resolved**

- **probed:** commit 83ede86 ("reposition — accountability-first,
  self-improvement staged to what fires") is real in the text, not just the
  message. `grep 'every 10 minutes\|meta-evolver' README.md` = 0. The two
  ambient passages both changed: (a) the old headline bullet is now
  README.md:30 "Learning pipeline: skills synthesized and promoted at run
  finalization... see [Memory and self-improvement] for exactly what fires
  today vs what's still experimental"; (b) README.md:447-452 states outright
  that `run_evolver` "sits behind opt-in heartbeat autonomy and **has never
  fired in production**... treat the meta-cycle as experimental", with the
  fires-today pipeline (415-445) claimed instead. The module table
  (README.md:393) also carries "heartbeat meta-cycle (experimental)". This is
  exactly the staged-honesty fix prescribed. The Hermes-collision headline is
  gone; new headline (line 5) is accountability.

### land-11 — Feature-parity frame vs accountability positioning → **resolved**

- **probed:** README.md:38-50 "What makes it different" now opens with
  "Most of the list above is table stakes in 2026. Maro's distinguishing
  layer is accountability: 'done' is treated as a claim to verify, not a
  status to trust" — the precise reframe prescribed. Channel breadth demoted
  to one "Interface-agnostic" bullet (line 32) and a Compatibility list at
  the bottom. The verification/safety trio leads.

### land-12 — done≠achieved unbragged → **resolved**

- **probed:** headline README.md:5 ("verifies whether the goal was actually
  achieved, not just whether the loop finished") + README.md:42 (done ≠
  achieved bullet: `goal_achieved` verdict, `done-not-achieved` run-card
  class, handle.py/run_curation.py named) + fabrication detection and
  grounded adversarial review bullets (43-44) + module table rows 407-408.
  `grep -i achiev README.md` went 0 → multiple load-bearing hits.

### land-13 — Replay capture unbragged → **resolved**

- **probed:** README.md:47 — full record-mode brag: per-call prompt/response/
  tool events, secret-scrubbed, `build/calls/`, "local, free, on by default",
  `MARO_RECORD=0` opt-out, runs.py named. Also module table row 407.

### land-14 — No-telemetry + default caps half-bragged → **resolved**

- **probed:** README.md:50 "No phone-home — no network telemetry of any kind;
  all metrics stay in local JSONL" (the previously-missing privacy half) +
  README.md:45 fail-closed caps bullet + README.md:142-147 "Safe by default"
  quickstart callout + Safety section 458-462 (malformed values fail closed).
  Both lines of the two-line trust brag now exist.

### land-15 — Portable learning unbragged → **resolved**

- **probed:** README.md:49 — maro-import bullet: provenance markers,
  exact-line dedup under lock, curated-file quarantine, workspace_import.py
  named; module table row 409. Watch-verdict on Letta's memory API remains a
  standing note (web-side, out of scope today).

---

## B. New findings (r2 sweep, repo-side landscape lens)

### land-r2-01 — Safety became the headline, but README never states the trusted-operator / no-sandbox boundary or links SECURITY_MODEL.md

- **claim:** The repositioning (83ede86) makes accountability/safety the sales
  pitch — "Safe by default" (README.md:142), a full "Safety and reliability"
  section (456-481). Meanwhile the honestly-rewritten security doc (1d3b77e)
  leads with the opposite-polarity fact: "**There is no sandbox on the live
  executor path**" — the executor runs `claude -p ...
  --dangerously-skip-permissions` as the operator's user with their
  filesystem/network/credentials, safe only under a trusted-operator
  assumption (docs/SECURITY_MODEL.md:14-30). README contains zero mention of
  sandbox absence or the trusted-operator model and zero link to
  SECURITY_MODEL.md (probed: `grep -in 'security\|SECURITY_MODEL' README.md`
  → only prompt-injection lines 399/481). A stranger-evaluator comparing
  frameworks on the safety axis Maro now leads with reads "safe by default"
  as stronger than what the security doc says is true — the same
  claim-vs-reality gap class land-10 just closed for self-improvement,
  reopened on the safety axis the repositioning newly weight-bears.
- **evidence:** README.md:142,456-481 (greps probed); docs/SECURITY_MODEL.md:14-30.
- **subsystem:** docs (positioning/safety)
- **severity:** real-but-deferrable (two sentences + one link before 1.0;
  cheap, same fix shape as land-10)

### land-r2-02 — Checklist-acknowledged pymaro/Microsoft-MARO disambiguation line not in README

- **claim:** docs/PUBLISH_CHECKLIST.md:32-34 itself flags that `pymaro` is
  Microsoft's MARO (Multi-Agent Resource Optimization) — "adjacent-name
  confusion worth a README disambiguation line" — and no such line exists
  (`grep -i 'pymaro\|Microsoft' README.md` = 0). Anyone who googles "maro
  agent framework" hits Microsoft's project first; one line at the top
  disambiguates.
- **evidence:** docs/PUBLISH_CHECKLIST.md:32-34; README.md grep = 0.
- **subsystem:** docs (positioning)
- **severity:** cosmetic

---

## Clean checks (probed, nothing wrong)

- CI green at HEAD and functioning as a real gate: caught red 6c03068,
  fixed in 772bb20 within minutes (`gh run list` probed live).
- Quickstart console commands (`maro-bootstrap`, `maro-doctor`, `maro-handle`,
  `maro-run`) all exist as pyproject `[project.scripts]` entry points
  (pyproject.toml:24-44) — the stranger's first-hour commands are real.
- README Docker compatibility claim intact: `Dockerfile` +
  `docker-compose.yml` exist at root; SF-7 "containers removed" commit
  (0281403) was live-host container cleanup, doc-only in repo (probed
  `git show --stat`).
- LICENSE is MIT (matches all fetched peers), CONTRIBUTING.md exists.
- README.md:48's box-stat claim ("82%, 58/71 ladder events") is scoped
  honestly to "the box this repo runs on" — evidence-shaped, not
  landscape-overreach. (Stat itself not re-derived; workspace-side.)
- No self-improvement overclaim regression anywhere in README
  (`every 10 minutes` / `meta-evolver` greps = 0).

## Positioning verdict delta (repo-side)

The 2026-07-09 verdict said the 1.0 move was packaging + CI + reposition to
"the framework that verifies its own work and can't silently spend your
money." One day later: the reposition is **done and faithful** (land-10/11/12/
13/14/15 all resolved in text, honestly staged), CI is **real and green**
(badge missing), and packaging is name-cleared with publish deliberately
parked as the tag-time act. The one new wrinkle is land-r2-01: leading with
safety raises the bar for safety candor — the trusted-operator sentence
belongs in the README before a stranger reads "safe by default" as a sandbox
claim.
