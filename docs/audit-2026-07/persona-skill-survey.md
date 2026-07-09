# Persona + Skill Demand Survey (1.0 arc item e)

**Date:** 2026-07-09
**Status:** research artifact — informs the curated 1.0 ship set; changes nothing by itself
**Question:** what jobs do people actually want from an autonomous agent, and how does that map onto Maro's 24 shipped personas + 6 runtime skills?

**Method:** link-farm first (`~/claude/link-farm/ai_links_collection_v3.md`, 300+ curated links; Agent Design section = 122 entries), then targeted web checks on demand-heavy galleries/marketplaces (awesome-llm-apps ~117k stars, n8n template library ~10k templates, anthropics/skills ~160k stars, OpenClaw use-case roundups). Demand ranked by recurrence, not by what's technically interesting.

---

## 1. Demand survey — job families, with evidence

Ordered by strength of demand signal (recurrence across independent sources).

### A. Coding / software building (strongest signal, by far)
Build a feature from a spec, run an autonomous build loop, migrate a codebase, debug, test.
- link-farm: Factory "Missions" (2026-03-24) — long-running app-build/migration workflows going mainstream; Rohun (2026-01-19) — open-sourced "describe → PRD → autonomous build loop → notification" workflow; Poonam Soni (2026-03-25) — 3-agent prod-app-from-one-prompt claim; Corey Ganim (2026-03-20) — Paperclip+gstack+autoresearch autonomous dev stack, 10-15 agents; Santiago (2025-12-19) — spec-driven dev, 0% hand-written code; 0xMarioNawfal (2026-04-06) — everything-claude-code: 27 agents / 64 skills, mostly coding; Vox (2026-03-31) — foreman/module parallel dev pattern.
- The entire Claude Code section (63 entries) is this family.

### B. Deep research / synthesis briefs
Multi-source research → cited brief; literature review; "what happened in the last N days"; due diligence.
- link-farm: Tom Dörr (2026-04-01) — Feynman autonomous research agent; Huaxiu Yao (2026-03-15) — AutoResearchClaw (lit review → hypotheses → experiments → paper); Daniel Miessler (2026-03-08) + Karpathy autoresearch (~630 lines); Sowmay Jain (2026-04-08) — 67GB genome analyzed end-to-end for $5 (909K views — demand proxy for "point agent at hard analysis, get report").
- Web: awesome-llm-apps recurring types #1 = research/analysis agents (deep research, earnings calls, due diligence, competitor intel).

### C. Personal ops / chief-of-staff (biggest *mainstream* / non-developer signal)
Daily briefing, inbox triage, calendar/task check, "what's on my plate", research-before-buy.
- link-farm: Jayden (2026-03-06) — Jim Prosser's "Chief of Staff with Claude Code" called the best real-world agentic example; Tech with Mak (2026-02-18) — Berman's OpenClaw masterclass, 21 daily use cases, CRM, memory.
- Web: every OpenClaw use-case roundup (Latenode, TLDL, Simplified, DigitalOcean, Sphere's "100 use cases") leads with inbox zero, morning brief, calendar/task triage; n8n's fastest-growing template class includes "email summarizers that digest inboxes daily."

### D. Monitoring / DevOps / on-call ops
Watch a service, detect errors, diagnose, hotfix, document; provision/harden servers.
- link-farm: Denislav Gavrilov (2025-12-28) — Clopus-Watcher: containerized Claude as 24/7 on-call K8s engineer; Imrat (2025-08-11) — Claude Code as DevOps agent tailing logs in tmux on a schedule; Denis Yurchak (2026-03-25) — one-shot secure VPS setup prompt (Claude-as-sysadmin).
- Web: OpenClaw roundups list "remote runs, tests, PRs, deployments, monitoring… manage servers from your phone"; n8n DevOps template category.

### E. Data analysis / document processing
Load/clean/explore data; extract structure from PDFs/spreadsheets/contracts; reporting.
- link-farm: Matt Dancho (2025-12-15) — business-science/ai-data-science-team (load→clean→EDA→features, reproducible); Jamie Quint (2026-03-07) — data agents, "80% data-team headcount reduction"; PageIndex (2×, 11.6K stars) — financial/legal/technical doc retrieval demand.
- Web: anthropics/skills' flagship skills are docx/pdf/pptx/xlsx (160k-star repo — the single strongest "what do people install first" signal); n8n 2026 finance/document-processing category (invoice extraction at scale).

### F. Web scraping / extraction / browser automation
Crawl, extract structured data, act through a browser (forms, submissions).
- link-farm: Gregor Zunic / Browser Use (2026-01-16); felpix (2026-03-21) — filed real taxes via Claude + Chrome (concrete shipped browser-action job); Vaishnavi (2026-04-10) — Google MCP Toolbox, plain-English access to 20+ databases (extraction demand at enterprise tier).
- Web: n8n "data scrapers that leverage vision AI for dynamic sites."

### G. Content creation / marketing
Posts, landing pages, email sequences, SEO pipelines — usually with a self-critique loop.
- link-farm: J.B. (2026-02-19) — recursive self-improvement loops for email sequences/ad creative/landing pages; Shann³ (2026-03-29) — autoresearch applied to landing-page copy, 56%→92% pass rate.
- Web: n8n sales/marketing category (auto-generated LinkedIn posts + images + hashtags); OpenClaw SEO-pipeline use cases; awesome-llm-apps content/media agents (journalism, podcasts, news briefs).

### H. Finance / markets analysis & trading
Market research, portfolio/probability analysis, prediction-market bots.
- link-farm: zostaff (2026-03-18) — autonomous Polymarket stack (Claude strategist + Codex engineer + OpenClaw orchestrator); Recogard (2026-04-10) — 36GB/72M Polymarket trade dataset; FinanceBench as the recurring RAG benchmark.
- Web: awesome-llm-apps finance/investment agents (coaching, analysis, VC due diligence). Note: real demand, but the *execution* half (placing trades) is a money-spending action — analysis ships, execution stays gated.

### I. Review / QA / verification / security
Code review before ship, plan critique, evidence-gated "is it actually done", pentesting.
- link-farm: Aman (2026-02-19) — Garry Tan's review-before-code CLAUDE.md pattern (1M-view class); Joseph Thacker (2026-03-01) — autonomous bug-bounty pipelines; chiefofautism (2026-02-07) — Shannon Lite autonomous pentester; Viv (2026-03-30) — "all harness design is about overcoming agent laziness"; tuna (2026-02-21) — Plankton uncheatable lint guard.
- This family is also Maro's own verify→learn thesis — demand and architecture agree.

### J. Summarization / reporting / status
Compress many inputs into one deliverable; recurring digests.
- link-farm: last30days pattern (Matt Van Horn, already a persona); Eric Siu (2026-04-06) — company knowledge layer powering 50+ daily workflows that "surfaces issues automatically."
- Web: OpenClaw morning-brief is the #1 cited daily habit; n8n "automated data reporting."

### K. Orchestration meta / self-improvement (framework, not a persona)
Evals flywheel, skill distillation (SKILLRL, EvoSkill), meta-agents tuning harnesses. Heavy in the link-farm (Sigrid Jin 2026-04-10, Ejaaz 2026-04-05, elvis 2026-03-12) — but this is Maro's *own product surface*, not a persona users pick. Listed for completeness; excluded from the ship-set question.

**Not observed as recurring demand:** interview coaching, psychology-of-AI research, celebrity-cosplay personas. These exist in our persona set because Jeremy wanted them — that is the point of the workspace override layer, not the shipped defaults.

---

## 2. Gap analysis — job families vs the existing 24 personas + 6 skills

### Personas, per-file ship verdicts

| Persona | Family | Verdict | Why |
|---|---|---|---|
| builder | A | **SHIP** | top demand family; generic, well-formed |
| research-assistant-deep-synth | B | **SHIP** | current router default; matches deep-research demand |
| ops | D | **SHIP** | on-call/monitoring demand (Clopus-Watcher class) |
| critic | I | **SHIP** | review demand; generic |
| reporter | J | **SHIP** | multi-agent synthesis is both demanded and framework-required |
| creative-director | G | **SHIP** | content/marketing demand; generic spec |
| scrapling-adaptive-web-recon | F | **SHIP** | extraction demand; name-checks the Scrapling OSS library (fine — it's a tool reference, not a person) |
| finance-analyst | H | **SHIP** (analysis only) | finance-agent demand is real; persona must not imply trade execution |
| plan-critic | I | **SHIP — infrastructure** | pre-flight gate; not a user-facing catalog item but the pipeline uses it |
| reality-checker-evidence-gate | I | **SHIP — infrastructure** | evidence gate = done≠achieved machinery |
| director-proxy | (internal) | **SHIP — infrastructure** | autonomous-run unblocker; framework needs it |
| loop-validator | (internal) | **SHIP — infrastructure** | spin detection; framework needs it (spec says "Poe Orchestration" — needs a neutral-wording pass before ship, cosmetic) |
| summarizer | J | keep-not-featured | overlaps reporter; per zero-overlap rule, fold into reporter or ship only one |
| strategist | — | keep-not-featured | prioritization/OKR demand is moderate; useful, not top-10 |
| simplifier | — | keep-not-featured | dev-culture judge; valuable inside Maro's own loops, weak stranger demand |
| systems-design-architect-coach | A-adjacent | keep-not-featured | "interview coach" framing is niche; architecture half overlaps builder/critic |
| last30days-brief | B/J | keep-not-featured | good pattern, but a *skill-shaped* job wearing a persona; candidate to become a skill |
| legal-researcher | B-domain | keep-not-featured | real demand (PageIndex legal use), but advice-liability tone needs a disclaimer pass before default-on |
| health-researcher | B-domain | keep-not-featured | same as legal |
| psyche-researcher | B-domain | **DON'T SHIP** | scoped "as these fields inform AI agent design" — a Jeremy research interest, not a stranger job |
| jeremy | — | **DON'T SHIP** | literally a user model of Jeremy |
| garrytan | — | **DON'T SHIP** | named-person likeness + known to brute-force Opus every step (cost footgun); the *review pattern* is worth extracting into a code_review skill |
| poe | — | **DON'T SHIP** | Jeremy's personal companion persona; framework decree is neutral-Conductor-by-default |
| companion | — | **DON'T SHIP** | thin adapter tuned to how *Jeremy* receives information; meaningless for a stranger |

Coverage summary: families A, B, D, F, G, H, I, J are covered by existing specs.
**Genuinely missing (vs demand): family C (personal assistant / chief-of-staff) and family E (data analyst / document processing).** C is the single largest mainstream-demand family and Maro has nothing for it; E has a `data_analysis` skill but no persona and no document-processing capability.

### Runtime skills, per-file

| Skill | Verdict | Notes |
|---|---|---|
| code_implement | SHIP | family A |
| web_research | SHIP | family B (shallow tier; see deep_research gap) |
| data_analysis | SHIP | family E (analysis half; no document I/O) |
| debug_investigate | SHIP | family A/D |
| resolve_ambiguity | SHIP | framework-internal, ship |
| compact_notation | SHIP | token-saver, ship |
| test_skill*, updatable_skill | don't ship | test fixtures — exclude from wheel/catalog |

Skill gaps vs demand (each is a "what a stranger asks for in week 1" item):
1. **document_process** (E) — read/extract PDF/xlsx/docx, generate docx/xlsx. The most-installed capability class in the ecosystem (anthropics/skills flagship).
2. **monitor_diagnose** (D) — watch a service/log, classify, diagnose, propose fix. `ops` persona has no skill-level muscle.
3. **web_extract** (F) — structured scraping with selector fallbacks + checkpoint/resume. Scrapling persona exists; no skill.
4. **report_synthesize** (J) — N sub-agent artifacts → one attributed synthesis with conflicts flagged. Reporter persona exists; no skill.
5. **daily_brief** (C) — assemble a recurring ranked digest from configured sources; pairs with heartbeat. The #1 OpenClaw habit.
6. **code_review** (I) — diff → confirmed findings with file:line, planted-bug-resistant. The garrytan pattern, de-personified.
7. **deep_research** (B) — fan-out queries, fetch, adversarial claim-check, cited report. `web_research` is the 8-step lite version; demand (Feynman, AutoResearchClaw, gpt-researcher) is for the heavy version.

---

## 3. Recommended ship set

### Personas — curated catalog (9)

| # | Persona | Rationale (one line) | Demand evidence |
|---|---|---|---|
| 1 | builder | the top job family; write/test/ship code | Factory Missions, Rohun loop, everything-claude-code (§1A) |
| 2 | research-analyst (= research-assistant-deep-synth) | deep cited research is job family #2 and the router default | Feynman, AutoResearchClaw, awesome-llm-apps (§1B) |
| 3 | assistant (chief-of-staff) — **NEW** | biggest mainstream family; daily brief + triage + "what's on my plate" | Prosser Chief-of-Staff, every OpenClaw roundup (§1C) |
| 4 | data-analyst — **NEW** | load/clean/analyze data + documents; only major family with zero persona | ai-data-science-team, n8n doc-processing, anthropics/skills (§1E) |
| 5 | ops | monitor/diagnose/fix/harden; 24/7 on-call demand | Clopus-Watcher, Imrat DevOps pattern (§1D) |
| 6 | critic | review-before-ship demand + Maro's own anti-laziness thesis | Garry Tan review pattern, Viv harness-laziness (§1I) |
| 7 | creative-director | content/marketing loops (posts, copy, campaigns) | n8n sales/marketing, J.B. content loops (§1G) |
| 8 | scrapling-adaptive-web-recon | web extraction is a distinct, recurring ask | Browser Use, n8n scrapers (§1F) |
| 9 | reporter | multi-agent output → one deliverable; demanded and framework-required | morning-brief demand + Maro's own fan-out (§1J) |

Plus **infrastructure personas that ship but aren't catalog items:** director-proxy, loop-validator, plan-critic, reality-checker-evidence-gate. (loop-validator needs its "Poe Orchestration" wording neutralized first.)

Kept in repo, not featured: summarizer (merge into reporter), strategist, simplifier, systems-design-architect-coach, last30days-brief (convert to skill), legal-researcher, health-researcher, finance-analyst (SHIP-eligible if catalog wants a 10th; analysis-only framing).

**Don't ship:** jeremy, poe, companion, garrytan, psyche-researcher — all box-specific. They stay in Jeremy's `~/.maro/workspace/personas/` (the override layer exists precisely for this).

Router note: `_PERSONA_ROUTING` (src/persona.py ~line 605) routes to garrytan and psyche-researcher today; the ship-set cut requires a routing-table pass in the implementation phase (out of scope here).

### Skills — default capabilities

Ship as-is: code_implement, web_research, data_analysis, debug_investigate, resolve_ambiguity, compact_notation.

Add (7), each marked **[SWIPE]** (OSS code exists — take it) or **[BUILD]** (orchestrator builds it — Maro test goal):

| Skill | Route | Rationale |
|---|---|---|
| document_process | SWIPE-then-BUILD | ecosystem's most-installed capability; see §4 license caveat |
| web_extract | SWIPE | Scrapling library is the persona's namesake already |
| deep_research | SWIPE | gpt-researcher's planner/executor/citation pattern is proven |
| monitor_diagnose | **BUILD** | shape exists (Clopus-Watcher) but it's harness-specific; small and Maro-native |
| daily_brief | **BUILD** | config-driven digest + heartbeat integration is inherently Maro-shaped |
| report_synthesize | **BUILD** | codify the reporter persona's 5-step workflow as a skill |
| code_review | **BUILD** (steal checklists) | de-personified garrytan pattern + everything-claude-code checklists |

### Orchestrator-builds-it draft goal statements (each is a Maro test goal)

1. **monitor_diagnose:** "Build a `monitor_diagnose` runtime skill: given a systemd unit name or log path, inspect recent logs, classify errors, produce a diagnosis with a proposed fix, and write an incident note under output/. Verify by intentionally breaking a sandbox service and confirming the skill detects it and names the actual cause."
2. **daily_brief:** "Build a `daily_brief` runtime skill: read a briefing config (list of sources: files, commands, RSS/URLs), assemble a ranked, deduplicated morning brief in markdown under output/, and register a recurring heartbeat task for it. Verify with 3 consecutive scheduled runs producing non-empty briefs with no repeated items."
3. **report_synthesize:** "Build a `report_synthesize` runtime skill: given N artifact files from sub-agents, produce one synthesis with per-claim source attribution and an explicit conflicts section. Verify on a fixture set containing a planted contradiction — the skill must surface it, not average it away."
4. **code_review:** "Build a `code_review` runtime skill: given a git diff, return findings with file:line references, separated into confirmed (with reasoning or reproduction) vs speculative. Verify against a fixture diff containing 3 planted bugs and 1 red herring — ≥2 bugs found, red herring not confirmed."
5. **document_process** (fallback if swipe blocked, see §4): "Build a `document_process` runtime skill: extract text and tables from PDF/xlsx into markdown/CSV, and generate docx/xlsx from structured input, using stdlib+vendored helpers. Verify by round-tripping fixture documents and diffing extracted tables against known-good CSV."
6. **assistant persona shakedown** (persona, not skill — still a test goal): "Run the new `assistant` persona on a synthetic chief-of-staff day (fixture inbox dir + calendar file + task list): produce a morning brief, a triaged action list, and one delegated sub-goal. Verify all three artifacts exist and the triage ranks the planted urgent item first."

### Curated 1.0 catalog, stated plainly

**9 personas** (builder, research-analyst, assistant*, data-analyst*, ops, critic, creative-director, scrapling-adaptive-web-recon, reporter — * = new) + 4 infrastructure personas + **13 skills** (6 existing + 7 above). That is what `pip install maro` hands a stranger.

---

## 4. OSS swipe candidates (swipe code, not deps — per decree)

| Skill / capability | Repo worth swiping | What to take | License note |
|---|---|---|---|
| document_process | github.com/anthropics/skills (`skills/pdf`, `skills/docx`, `skills/xlsx`, `skills/pptx`) | SKILL.md structure + helper scripts | **Caveat:** repo is mixed — many skills Apache-2.0 but the document skills are *source-available, not open source*. Verify per-file before swiping; if blocked, BUILD route (goal #5) on pypdf/python-docx/openpyxl (all permissive; these are libs, not scaffolding — acceptable deps or vendor the thin slices used). |
| deep_research | github.com/assafelovic/gpt-researcher | planner→parallel-executor→citation-verifier pipeline shape; prompt set | permissive (verify current license at swipe time) |
| web_extract | github.com/D4Vinci/Scrapling | adaptive selector fallback + auto-match logic | BSD-3 (verify); persona already models this tool |
| data_analysis upgrades | github.com/business-science/ai-data-science-team | load/clean/EDA/feature recipes as skill steps | MIT (verify). link-farm: Matt Dancho 2025-12-15 |
| monitor_diagnose | Clopus-Watcher (kuberdenis) | on-call loop shape: watch→detect→hotfix→document | small repo; read for shape, code is K8s-specific — BUILD |
| code_review checklists | github.com/affaan-m/everything-claude-code | review/TDD skill checklists (64 skills) | verify license; take checklists, not harness. link-farm: 0xMarioNawfal 2026-04-06 |
| assistant persona shape | awesome-llm-apps (Shubhamsaboo) starter agents | prompt shapes for assistant/data-analyst personas | Apache-2.0, 117k stars |
| notify-when-done (platform, later) | github.com/NousResearch/hermes-agent (PR #5779) | background-process completion notification pattern | link-farm: Teknium 2026-04-10; platform-layer, not a skill |
| future auto-eval loop (family K) | Karpathy autoresearch (~630 lines) | minimal experiment-loop | post-1.0; link-farm: Miessler 2026-03-08 |

---

## Sources

- Link-farm: `/home/clawd/claude/link-farm/ai_links_collection_v3.md` (entries cited inline by author + date; Agent Design §, Claude Code §, Skills & MCP §)
- [awesome-llm-apps](https://github.com/Shubhamsaboo/awesome-llm-apps) — category census, ~117k stars
- [anthropics/skills](https://github.com/anthropics/skills) — flagship skill census, ~160k stars
- [n8n workflow templates](https://n8n.io/workflows/) + roundups ([Intuz](https://www.intuz.com/blog/best-n8n-workflow-templates/), [Versich top-15 use cases](https://versich.com/blog/the-top-15-n8n-use-cases-revolutionizing-workflow-automation-in-2026/), [awesome-n8n-templates](https://github.com/enescingoz/awesome-n8n-templates))
- OpenClaw use-case roundups: [Latenode](https://latenode.com/blog/ai/ai-agents/popular-openclaw-use-cases), [TLDL 25+ examples](https://www.tldl.io/blog/openclaw-use-cases-2026), [Simplified top-10](https://simplified.com/blog/automation/top-openclaw-use-cases), [DigitalOcean](https://www.digitalocean.com/resources/articles/what-is-openclaw), [Sphere 100 use cases](https://www.sphereinc.com/blogs/100-openclaw-use-cases-you-can-try-today)
- Repo ground truth: `personas/*.md` (24 specs), `skills/*.md` (6 runtime + fixtures), `src/persona.py` `_PERSONA_ROUTING` (~line 605)
