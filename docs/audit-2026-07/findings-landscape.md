# Purgatorio Eye 6 — External landscape re-verification

Probed 2026-07-09, read-only (this file is the only write). Frame: **re-verify
Maro's choices against a world that moved** — where does a 1.0 open-source
release stand in the July-2026 agent-framework landscape, what does a
stranger-evaluator hit in the first hour, which README claims does the
landscape make untenable, and what does Maro undersell? Per charter, every
finding carries a **steal / ignore / watch** verdict naming the Maro subsystem
it touches. Steal-from-don't-migrate stands.

Landscape baseline actually fetched this session (every landscape claim below
cites one of these):

- LangGraph — https://github.com/langchain-ai/langgraph (36.9k stars, MIT;
  durable execution, checkpointing, HITL, LangSmith observability)
- CrewAI — https://github.com/crewAIInc/crewAI (55.3k stars, MIT; crews/flows,
  MCP + A2A, enterprise control plane, anonymous telemetry default-on)
- OpenHands — https://github.com/All-Hands-AI/OpenHands (80.2k stars, MIT;
  npm/docker install, GUI + headless, docs site, active CI)
- Hermes Agent — https://github.com/NousResearch/hermes-agent (212k stars, MIT;
  "The self-improving AI agent": skills-from-experience, persistent memory,
  Telegram/Discord/Slack/WhatsApp/Signal/CLI, NL cron, 40+ tools, MCP,
  one-line installer, 300+ models)
- Letta — https://github.com/letta-ai/letta (23.7k stars, Apache-2.0;
  memory-first agents, self-editing memory, cloud + self-host)
- Survey pieces — https://www.firecrawl.dev/blog/best-open-source-agent-frameworks
  (10-framework 2026 table-stakes survey: streaming, docs, observability,
  multi-LLM, production case studies) and
  https://www.digitalapplied.com/blog/open-source-agent-frameworks-5-compared-2026
  (May-2026 snapshot: MCP native in 4/5 frameworks; AutoGen in maintenance
  mode, merged into Microsoft Agent Framework 1.0 GA April 2026; OpenAI
  archived Swarm).

---

## Findings

### land-01 — Not installable by name: no PyPI package, and the version says 0.5.0

- **claim:** The 1.0 story fails at minute one — `pip install
  maro-orchestration` 404s on PyPI (probed:
  `curl https://pypi.org/pypi/maro-orchestration/json` → HTTP 404); install is
  clone+`pip install .` only (README.md:93-96), and `pyproject.toml:7` says
  `version = "0.5.0"`. Every fetched peer has a one-command install (Hermes
  one-line installer; OpenHands npm/docker; CrewAI/LangGraph/Letta on PyPI).
- **evidence:** pyproject.toml:6-7; README.md:93-96; PyPI 404 probe this session.
- **subsystem:** platform (packaging)
- **severity:** blocker-for-1.0
- **status:** confirmed
- **verdict:** **steal** (peer one-command-install pattern) → platform
- **disposition:** backlog-item — PyPI publish + version bump IS the 1.0
  release act; check name availability before the tag

### land-02 — CI exists as an empty directory: no workflows, no badge, no enforced test gate

- **claim:** `.github/workflows/` exists with zero files (probed `ls -la`,
  dir mtime 2026-03-28) on a PUBLIC repo (probed `gh repo view`), so there
  are no CI runs and no badge — while docs/PUBLISH_CHECKLIST.md gates release
  on "pytest passes" with nothing enforcing it. Evaluators read a missing
  badge as "tests probably don't pass"; all fetched peers run CI.
- **evidence:** .github/workflows/ (empty); docs/PUBLISH_CHECKLIST.md:12-16.
- **subsystem:** ops
- **severity:** blocker-for-1.0
- **status:** confirmed
- **verdict:** **steal** (standard pytest workflow + badge) → ops
- **disposition:** backlog-item; the test-throttling concern is a local-box
  issue, not a GH-runner one

### land-03 — No examples/ directory and no root CHANGELOG

- **claim:** No `examples/` dir exists (probed root listing) and no root
  CHANGELOG (only docs/history/CHANGELOG.md). Peers ship runnable example
  galleries (CrewAI-examples: trip planner, stock analysis; LangGraph
  quickstarts). Nothing committed shows a full goal → run → verdict artifact
  trail a stranger can diff against their own first run.
- **evidence:** repo root listing; docs/history/CHANGELOG.md.
- **subsystem:** docs
- **severity:** real-but-deferrable
- **status:** confirmed
- **verdict:** **steal** (examples-gallery pattern) → docs; 2-3 committed
  example runs with expected run_card.json double as regression fixtures
- **disposition:** backlog-item

### land-04 — Docs corpus is internal-facing; no hosted docs, no help channel

- **claim:** All fetched peers have a docs site (docs.langchain.com,
  docs.crewai.com, docs.openhands.dev, hermes-agent.nousresearch.com/docs,
  docs.letta.com). Maro's docs/ is ~50 design/audit files framed internally
  (GOAL_BRAIN, Purgatorio, decrees) mixed with the handful a user needs
  (DEFAULTS.md, SECURITY_MODEL.md); README has no "where to get help / file
  issues" section end-to-end.
- **evidence:** docs/ listing this session; README.md (no support section).
- **subsystem:** docs
- **severity:** real-but-deferrable
- **status:** confirmed
- **verdict:** **steal** the minimum (curated user-docs subset or README
  doc-map + a help/issues pointer) → docs; **ignore** hosted docs site for 1.0
- **disposition:** backlog-item

### land-05 — No streaming of agent output to callers

- **claim:** The 2026 survey treats streaming/real-time execution as an
  essential criterion (firecrawl survey), and LangGraph/CrewAI/OpenHands all
  stream. Maro's llm.py has subprocess *liveness* streaming internally
  (llm.py:553,716 — kill-guard for silent local models) but exposes no
  streaming completion API; interfaces get final text plus step_callback
  granularity (README.md:249-258).
- **evidence:** src/llm.py:553,716 (only "stream" hits); README.md:249-258.
- **subsystem:** platform (llm adapters)
- **severity:** real-but-deferrable
- **status:** confirmed
- **verdict:** **watch** → platform/llm; step-level callbacks cover Maro's
  long-horizon (not chat-latency) profile; revisit if chat interfaces grow
- **disposition:** backlog-item (low)

### land-06 — Maro has an MCP client and the README never says the letters "MCP"

- **claim:** MCP is table stakes by mid-2026 (digitalapplied: native in 4/5
  compared frameworks; CrewAI advertises MCP + A2A; Hermes ships MCP). Maro
  *has* a working client — src/mcp_client.py (stdio + HTTP transports, tools
  registered as deferred `mcp__server__tool` entries) — but
  `grep -iE '\bmcp\b' README.md` returns zero hits, and the docstring shows
  Python-API registration only (no config-file wiring documented).
- **evidence:** src/mcp_client.py:1-27; README.md (0 MCP hits, probed).
- **subsystem:** platform / docs
- **severity:** real-but-deferrable
- **status:** confirmed
- **verdict:** **steal** (config-driven MCP server registration, the way
  peers expose it) + surface in README → platform (tool_registry/mcp_client)
- **disposition:** backlog-item; this is a checkbox evaluators literally scan for

### land-07 — Local/self-hosted model lane exists but is invisible

- **claim:** Local-LLM operation is a prominent 2026 evaluation axis (search
  surfaced dedicated local-LLM benchmark comparisons; Hermes advertises 300+
  models). OpenAIAdapter takes `base_url` (llm.py:1516-1519) so
  Ollama/vLLM/llama.cpp servers work in principle — the code even handles
  silent-but-computing local models (llm.py:562) — but the README backend
  table (README.md:70-88) lists only cloud keys and CLI OAuth lanes.
- **evidence:** src/llm.py:1516-1519,562; README.md:70-88.
- **subsystem:** platform / docs
- **severity:** real-but-deferrable
- **status:** confirmed (code path exists; end-to-end against a local server
  NOT probed this session — claim "OpenAI-compatible", not "Ollama-tested")
- **verdict:** **steal** (document the base_url lane; one Ollama smoke test
  before claiming it) → platform/llm
- **disposition:** backlog-item

### land-08 — No third-party observability exporter (OTel/LangSmith-class)

- **claim:** Peers integrate hosted observability (LangGraph→LangSmith;
  CrewAI→control-plane tracing); the survey lists observability among
  production-readiness criteria. Maro has structured `maro.*` loggers + JSONL
  metrics + record-mode capture but no OpenTelemetry hooks
  (`grep -rl opentelemetry src/` = 0) and no exporter of any kind.
- **evidence:** src/ grep (no otel); README.md:151-198.
- **subsystem:** platform (metrics)
- **severity:** real-but-deferrable
- **status:** confirmed
- **verdict:** **watch** → platform/metrics; record-mode (land-13) is the
  honest counter-story for 1.0; an OTel span exporter is a clean post-1.0 seam
- **disposition:** backlog-item (low)

### land-09 — No Windows support in a landscape that ships it

- **claim:** README.md:13-14 scopes to "Linux or macOS"; Hermes ships native
  Windows PowerShell support and OpenHands installs via npm cross-platform.
  A first-hour filter for a real user slice, but WSL2 exists.
- **evidence:** README.md:13-14; Hermes README (fetched).
- **subsystem:** platform
- **severity:** cosmetic
- **status:** confirmed
- **verdict:** **ignore** for 1.0 → platform; add "Windows via WSL2" to the
  prerequisites line and move on
- **disposition:** fixed-inline candidate (one README line)

### land-10 — The "self-improvement every 10 minutes" headline is claimed against the competitor that owns that exact tagline — and Maro's evolver has never run in production

- **claim:** README.md:26 leads with "Self-improvement: meta-evolver reviews
  failure patterns every 10 minutes and proposes prompt/guardrail/skill
  changes." Two problems: (a) this audit's own charter lists "evolver has
  never run in production" among its founding evidence
  (PURGATORIO_AUDIT.md:22-23), so the headline describes design intent, not
  operating behavior; (b) the niche's mindshare leader is literally named for
  this — Hermes Agent, 212k stars, tagline "The self-improving AI agent,"
  with a shipped skills-from-experience loop. An evaluator who tries both
  experiences Hermes improving and Maro's evolver idle; the claim converts
  from differentiator to credibility hole.
- **evidence:** README.md:26; docs/PURGATORIO_AUDIT.md:22-23; Hermes README
  (fetched). Messaging half only — evolver liveness itself is ops-eye territory.
- **subsystem:** quality/self-improvement / docs
- **severity:** blocker-for-1.0 (messaging; the fix is cheap — claim the
  shipped learning parts, stage the evolver claim to what verifiably fires)
- **status:** confirmed
- **verdict:** **watch** Hermes' self-improvement loop (steal candidates
  already on record: skill auto-extraction, Honcho-style user modeling —
  docs/history/CHANGELOG.md:923) → quality/self-improvement; correct our claim
- **disposition:** goal-brain-correction + README edit at 1.0

### land-11 — Feature-listing parity with Hermes is a losing frame; differentiation must move to verification and safety

- **claim:** Maro's README differentiators-by-listing — multi-channel ingress,
  cron scheduling, skill library, persistent memory — are all matched or
  exceeded by Hermes Agent (6 chat platforms, NL cron, 40+ tools, MCP,
  one-line install, 212k stars) and partially by CrewAI/Letta. What NO
  fetched peer advertises anywhere: done-vs-achieved outcome verification,
  default-on spend caps, write fences, hallucination claim-checking, local
  replay capture, portable learning. Maro's landscape position is "the one
  that checks its own work and can't bankrupt you," not "the one with a
  Telegram bot."
- **evidence:** README.md:23-30 vs all five fetched READMEs (none mention
  outcome verification or budget caps); README.md:121-126 (caps exist).
- **subsystem:** docs (positioning)
- **severity:** real-but-deferrable
- **status:** confirmed
- **verdict:** **ignore** the parity race → docs/positioning; lead 1.0 README
  with the verification/safety trio, demote channel breadth to a table row
- **disposition:** goal-brain-correction

### land-12 — Brag candidate: done≠achieved verdicts + claim verification are genuinely unique and have zero README presence

- **claim:** No fetched peer verifies goal *achievement* distinct from run
  *completion*. Maro ships the full stack — `goal_achieved` run metadata
  (src/handle.py:418-451), run_card outcome classification
  success/done-not-achieved/done-unverified (src/run_curation.py:16;
  docs/ARCHITECTURE_OVERVIEW.md:49-58), and claim_verifier.py file/symbol
  existence checks on step results — and `grep -i achiev README.md` returns
  nothing. The single strongest differentiator, unbragged.
- **evidence:** src/handle.py:418-451; src/run_curation.py:16;
  src/claim_verifier.py; README grep (0 hits).
- **subsystem:** quality/self-improvement / docs
- **severity:** real-but-deferrable (highest-leverage README edit available)
- **status:** confirmed
- **verdict:** **ignore** externally (nothing to steal — surface ours) →
  quality/self-improvement
- **disposition:** README edit at 1.0

### land-13 — Brag candidate: local, free, default-on LLM-call replay capture — LangGraph's equivalent is a commercial product

- **claim:** Record-mode captures every prompt/response/tool-event per call
  to `<run-dir>/build/calls/`, secret-scrubbed, default ON
  (src/runs.py:352-363; docs/ARCHITECTURE_OVERVIEW.md:40-47). The closest
  peer capability is LangGraph's time-travel debugging + LangSmith
  observability — the latter hosted/commercial. README mentions neither
  record-mode nor replay (`grep -iE 'replay|record-mode' README.md` = 0).
- **evidence:** src/runs.py:352-363; docs/ARCHITECTURE_OVERVIEW.md:36-47;
  README grep (0 hits); LangGraph README (fetched).
- **subsystem:** platform / docs
- **severity:** real-but-deferrable
- **status:** confirmed
- **verdict:** **ignore** externally (surface ours) → platform
- **disposition:** README edit at 1.0

### land-14 — Brag candidate: no phone-home telemetry + default-on spend caps, where the #2 framework collects usage data by default

- **claim:** CrewAI's README discloses anonymous telemetry (default-on,
  opt-out); Maro has no network telemetry of any kind (src grep: "telemetry"
  hits are local per-skill stats only, src/loop_blocked.py:382). Combined
  with fail-closed budget caps on by default (src/loop_init.py:38-73;
  README.md:121-126 — no fetched peer advertises default spend caps at all),
  this is a two-line trust brag the README makes only half of (caps yes,
  privacy never stated).
- **evidence:** src/loop_blocked.py:382; src/loop_init.py:38-73;
  README.md:121-126; CrewAI README (fetched telemetry disclosure).
- **subsystem:** platform / docs
- **severity:** cosmetic (pure messaging)
- **status:** confirmed
- **verdict:** **ignore** externally (surface ours) → platform
- **disposition:** README edit at 1.0

### land-15 — Brag candidate: portable learning (maro-import) has shipped machinery no peer matches, currently invisible

- **claim:** Letta's whole product is memory persistence/portability; Hermes
  brags persistent memory. Maro already ships cross-workspace learning
  *migration* with provenance, dedup-under-lock, and quarantine of curated
  files (src/workspace_import.py:19-22,79-117,152-169; live-proven in the
  hermes trial per docs/PORTABLE_LEARNING_DESIGN.md §0), with secret_scrub.py
  as the sharing choke point — and the README says nothing about any of it.
  With 1.0 item (g) making shareable learning official scope, the shipped
  half deserves surface now.
- **evidence:** src/workspace_import.py; src/secret_scrub.py;
  docs/PORTABLE_LEARNING_DESIGN.md:24-40; README grep (no maro-import).
- **subsystem:** memory/knowledge / docs
- **severity:** real-but-deferrable
- **status:** confirmed
- **verdict:** **watch** Letta's memory-block/sharing API as the convergent
  prior art (link-farm line 99) → memory/knowledge; surface ours in README
- **disposition:** README edit at 1.0 + cross-ref to 1.0 item (g)

---

## Link-farm re-audit (charter item a)

Source: `~/claude/link-farm/ai_links_collection_v3.md` (2133 lines), probed
this session; classifications are for the orchestration-relevant entries.

| Entry (line) | Class | Verdict → subsystem |
|---|---|---|
| Hermes notify-when-done, background-process → agent notification (91, 329) | **aged-well, never-digested as code** — Anthropic copied it (Monitor tool); maps exactly to Session-40 "next: async fork join" | **steal** → core-loop (parallel fan-out / fork join) |
| Letta/MemGPT memory-blocks API mirrored by Anthropic managed agents (99, 337) | **aged-well** — validates memory-as-module + `memory_port` bake-off direction; the API shape to watch when adapter-2 is chosen | **watch** → memory/knowledge (memory_port) |
| Akshay: thin "dumb loop" (Anthropic) vs thick orchestration (CrewAI/LangChain) harness split (85) | **aged-well** — the live architectural question Maro's dumb-loop audit (docs/DUMB_LOOP_AUDIT.md) already engages | **watch** → core-loop |
| Vtrivedy10/LangChain harness hill-climbing with evals as flywheel (103, 105) | **never-digested** — no repo doc engages it; it is the verify→learn arc stated as method | **steal** (the eval-as-signal framing, not the stack) → quality/self-improvement |
| Hive (aden-hive/hive) self-improving agent swarm (68, 1033) | **never-digested** — zero mentions anywhere in repo docs (probed grep) | **watch** → quality/self-improvement; skim before the verify→learn arc, likely convergent |
| Ejaaz open-source meta-agent that rewrites its own harness, #1 on TerminalBench in 24h (121) | **partially digested** — Meta-Harness (Stanford) entered the steal-list 2026-04-03 (docs/history/2026-04-05-steal-list.md); this OSS demo did not | **watch** → quality/self-improvement (evolver) |
| Miessler "Bitter Lesson Engineering" anti-scaffolding (137) | **digested** — docs/history/2026-03-30-bitter-lesson-analysis.md | aged-well; no action |
| Justin Brooke 7-markdown-files agent structure (107) | **superseded** — Maro's workspace/persona/skill layout is well past this | **ignore** |
| LangGraph learning-path tutorial (283, 667) | **superseded** (tutorial content, not design signal) | **ignore** |

De-bubble note: the two survey pieces above (firecrawl, digitalapplied) are
deliberately outside the X-bubble sources the link-farm skews toward; the
academic lane (Meta-Harness arXiv 2603.28052) is already on the steal-list
record. Biggest de-bubble deltas the bubble missed: AutoGen→maintenance mode /
Microsoft Agent Framework 1.0 GA (April 2026), and OpenAI Swarm archived —
neither appears anywhere in repo docs or the link-farm.

---

## Positioning verdict (one paragraph)

Maro enters a July-2026 field where orchestration mechanics (multi-agent
roles, memory, checkpointing, chat ingress, cron, MCP) are commoditized —
LangGraph owns stateful-graph enterprise, CrewAI owns role-play ergonomics,
OpenHands owns coding agents, and Hermes Agent (212k stars) owns the exact
"self-improving autonomous agent" tagline Maro's README leads with while
Maro's evolver has never fired in production. Competing on that headline or
on feature-count loses on contact. What no fetched peer offers — and what
Maro has actually shipped — is the *accountability layer*: done-vs-achieved
verdicts, hallucination claim-checking, default-on fail-closed spend caps,
write fences, local free replay capture, and provenance-tracked portable
learning. The 1.0 move is not to build missing table stakes beyond packaging
+ CI (land-01/02), but to reposition: "the autonomous agent framework that
verifies its own work and can't silently spend your money," with
self-improvement staged honestly as the shipped learning pipeline rather
than the idle evolver.
