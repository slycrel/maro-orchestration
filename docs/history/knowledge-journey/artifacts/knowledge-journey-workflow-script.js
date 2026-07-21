export const meta = {
  name: 'knowledge-journey',
  description: 'Era-by-era git/doc archaeology of the maro project: excavate, verify, write detail files, completeness-check',
  phases: [
    { title: 'Excavate', detail: 'one archaeologist per era' },
    { title: 'Verify', detail: 'adversarial claim-check per era record' },
    { title: 'Write', detail: 'one detail file per era' },
    { title: 'Critique', detail: 'completeness critic over the full set' },
  ],
}

const REPO = args.repo
const OUTDIR = args.outdir
const TODAY = args.today

const ERAS = [
  { n: '00', slug: 'prehistory-openclaw', name: 'Prehistory: OpenClaw, Poe, and the poe-orchestrator prototype', range: 'Feb 2026 – 2026-03-05', git: null,
    hints: `Sources OUTSIDE the repo: ~/.openclaw/workspace/ (MEMORY.md, SOUL.md, GOALS.md, AGENTS.md, TASKS.md), ~/.openclaw/workspace/prototypes/poe-orchestrator/ (the prototype this repo superseded: goal ancestry, durable state, heartbeats; design inspirations Paperclip/LangGraph/DSPy/Reflexion/DeerFlow), ~/claude/idea.md (the Grok conversation that led to trying Claude Code), ~/claude/CLAUDE.md (the workspace context written at the switch), and ~/.claude/projects/-home-clawd-claude/memory/archive/ (retired auto-memories). Poe ran via Telegram bot with ~80 shell scripts. The era ends with the decision to run Claude Code as the doing-engine in a new repo. DO NOT quote any credentials/tokens you encounter (openclaw.json has secrets — do not open it).` },
  { n: '01', slug: 'foundation', name: 'Foundation: phases 0–6, the first autonomous loop', range: '2026-03-05 – 2026-03-17', git: '--since=2026-03-05 --until=2026-03-18',
    hints: `ROADMAP_ARCHIVE Phases 0-6 (Foundation Audit, Autonomous Loop, NOW/AGENDA lanes, Director/Worker hierarchy, Loop Sheriff + Heartbeat, Memory + Learning, OpenClaw + Telegram integration). docs/history/2026-03-17-mainline-plan.md marks the era boundary. First commit 97473ac "chore: repo hygiene for autonomy" (2026-03-05).` },
  { n: '02', slug: 'research-steal', name: 'Research & steal: external prior art becomes the method', range: '2026-03-17 – 2026-03-31', git: '--since=2026-03-18 --until=2026-04-01',
    hints: `docs/history/: 2026-03-24-factory-ai-research.md, 2026-03-25-anthropic-harness-research.md, 2026-03-25-memento-skills-research.md, 2026-03-28-loop-scratchpad.md, 2026-03-28-phase-audit.md, 2026-03-30-bitter-lesson-analysis.md, 2026-03-31-factory-mode-findings.md, 2026-03-31-sources.md. ROADMAP_ARCHIVE Phases 7-15 (Meta-Evolution, Scaling+Evaluation, Interruption, Mission Layer, Hooks+Reviewers, Oversight, Poe as CEO, Skill Evolution, Skill Sandbox).` },
  { n: '03', slug: 'phase-sprint', name: 'Phase sprint: tiered memory, promotion cycle, personas', range: '2026-04-01 – 2026-04-13', git: '--since=2026-04-01 --until=2026-04-14',
    hints: `CHANGELOG 1.6.0 (2026-04-01) = Phase 56 Promotion Cycle (StandingRule, decision journal — note: the decision journal read-wiring shipped here and its write side is dead TODAY; capture what it was MEANT to do). Phases 16-21 (Tiered Memory, Behavior-Aligned Routing, Sprint Contracts + Agent Separation, Sandbox Hardening, Persona System, Production Readiness). The 1.10.x CHANGELOG flood on 04-04/04-05. docs/history/2026-04-05-steal-list.md, 2026-04-05-success-criteria-gap-audit.md, 2026-04-13-phase-audit.md, 2026-04-01-prediction-markets-research-summary.md (the Polymarket side-quest).` },
  { n: '04', slug: 'session-sprint', name: 'Session sprint: core-loop machinery and the PM/dev experiment', range: '2026-04-13 – 2026-04-16', git: '--since=2026-04-14 --until=2026-04-17',
    hints: `ROADMAP_ARCHIVE sessions 20-34 (dense: LoopStateMachine, FailoverAdapter, K4 write path, codebase graph, prompt-injection guard, BLE outcome rewrite, PM/dev recipe rounds vs the orchestrator-test-recipes repo, Phase 61 input classification, Phase 63 Director closure check, Phase 65 proposal + MVE scope generation, ResolvedIntent v0 — plan-creation as its own step, correspondence.py dev retrieval, synthesize_skill 3-gate, closure verdict gates the loop). CHANGELOG 1.12-1.19 all dated 04-14/04-15.` },
  { n: '05', slug: 'adaptive-optional', name: 'Adaptive execution and the optional-services turn', range: '2026-04-15 – 2026-04-30', git: '--since=2026-04-15 --until=2026-05-01',
    hints: `Sessions 35-38: Phase 64A/B/C adaptive execution, heartbeat health-only by default + autonomy explicit (the "never-off vision becomes optional" turn — a philosophy shift worth capturing), scope A/B analysis, run transparency + closure reads deliverables, decomposition feedback wired forward. docs/history/2026-04-22-architecture.md is a full architecture snapshot of this era — gold for architecture_then.` },
  { n: '06', slug: 'quiet-reckoning', name: 'The quiet and the adversarial-verification reckoning', range: '2026-05-01 – 2026-06-07', git: '--since=2026-05-01 --until=2026-06-08',
    hints: `Only ~25 commits in May. Session 39 (2026-05-12): docs/history/2026-05-12-adversarial-verification{,-brief,-report}.md, 2026-05-12-md-claims-audit.md (docs had vibe-claimed things the code didn't do), 2026-05-12-research-brief-findings-and-design.md, 2026-05-12-research-brief-persistence-and-zoom.md (productive persistence + zoom-out metacognition). Investigate WHY the pause (check GOAL_BRAIN Decisions for this date range, auto-memory archive at ~/.claude/projects/-home-clawd-claude/memory/archive/) — do not speculate beyond evidence; if the cause isn't recorded, say so.` },
  { n: '07', slug: 'session40-reboot', name: 'Session-40 reboot: GOAL_BRAIN, entropy, the navigator shadow', range: '2026-06-08 – 2026-06-20', git: '--since=2026-06-08 --until=2026-06-21',
    hints: `ROADMAP_ARCHIVE session-40 entries (M1-M5, 2026-06-10/11): three latent memory data-corruption bugs, standing rules finally accrete, GOAL_BRAIN.md born as compiled-truth anchor, recall() one-read-seam, decay-by-invalidation v0 (Jeremy's entropy thread: "re-fight on collision" — refight_rule + contested gate; NOTE: its contradiction-recording writer never shipped, found dead 2026-07-20), captain's log rotation, navigator shadow round 2 + live dispatch shadow, the safe_list bug that had silently killed lesson extraction (fixed d088ca7 2026-06-11 — verify hash). Jeremy directives recorded: fix-in-place over rewrite; a program, not an operating system (no cron/daemons — rogue-process history).` },
  { n: '08', slug: 'recordmode-rename', name: 'Record-mode, the rename to Maro, and the dead-code purge', range: '2026-06-21 – 2026-07-02', git: '--since=2026-06-21 --until=2026-07-03',
    hints: `CHANGELOG 1.20/1.21 (06-21). Forward record-mode keystone (FailoverAdapter captures prompt/response/tool_events per call) 06-26 — unlocked Replayability; visibility ladder rungs 1-6 defined 06-26 after test-corpus harvest showed record lossier than claimed. Repo renamed openclaw-orchestration → maro-orchestration 06-26 (kept -orchestration suffix). docs/REFACTOR_PLAN.md dead-code purge (~9,575 lines removed — ossification evidence: "old and new decomposition pipelines side-by-side"). docs/history/2026-07-02-burnin.md, 2026-07-02-adversarial-verification-synthesis.md. Deep rename decree: Maro=framework, Conductor=role, Poe=optional persona; ~/.poe→~/.maro.` },
  { n: '09', slug: 'decisions-threads', name: 'Decision cleanup, thread architecture, done≠achieved', range: '2026-07-03 – 2026-07-09', git: '--since=2026-07-03 --until=2026-07-10',
    hints: `docs/history/2026-07-04-memory-decision-brief.md (filesystem-vs-real-memory direction: memory-as-module + bake-off behind memory_port.py), 2026-07-07-memory-bakeoff.md, 2026-07-08-worker-slice-ab.md (A/B won → default-on), 2026-07-09-decisions-for-jeremy.md, 2026-07-09-thread-architecture-decisions-brief.md (all 9 decisions resolved + the recursion decree: sub-goal spawning never foreclosed), 2026-07-09-done-vs-achieved.md. Also: workspace-pin unification (BACKLOG #-1), concurrency-hardening arc completed 07-09 (fail-closed file_lock, admission gate, worktree isolation — the "production incident class" of parallel forks overwriting each other), scope/ResolvedIntent injection LIVE on this box 07-09 (SF-4; 04-22 A/B: inject wins), Slack bridge mothballed 07-09, run-visibility arc (captain's log slice-first, NOW mini-reports).` },
  { n: '10', slug: 'verify-learn', name: 'Verify→Learn arc, 0.8.0 on PyPI, portable learning', range: '2026-07-10 – 2026-07-15', git: '--since=2026-07-10 --until=2026-07-16',
    hints: `VERIFY_LEARN_ARC V1-V5 closed (docs/VERIFY_LEARN_ARC.md): graduation behavioral auto-verify, navigator divergence adjudication (V4) + navigator lessons (V5, A/B-gated). docs/history/2026-07-12-fable-handoff.md (the model handoff to Fable — worth one paragraph: what changed in how the project runs), 2026-07-12-routing-and-probe-synthesis-design.md, 2026-07-12-git-history-privacy-scan.md, 2026-07-14-audit-delivery-and-resume-admission.md, 2026-07-14-session-reuse-{prototype,spike}.md, 2026-07-14-verdict-persistence-contract.md. 0.8.0 PUBLISHED on PyPI 07-15 (Jeremy's call; 1.0 later, NOT a work gate). Portable learning (maro-pack) chunks 1-4 shipped 07-13 with trust demotion on import. Known-gap pin-test convention decreed 07-12. Test-suite truth pass 07-14 (the "full run silently excluded the slow lane" find).` },
  { n: '11', slug: 'ecosystem-week', name: 'Ecosystem week: audits, free validation, Hermes, going multi-box', range: '2026-07-15 – 2026-07-20', git: '--since=2026-07-16 --until=2026-07-21',
    hints: `Purgatorio audits r1+r2 complete (docs/audit-2026-07/, docs/audit-2026-07-r2/; SF-13 standing rule born: decree-class statements get GOAL_BRAIN Decisions lines). Capabilities catalog + failure-pattern corpus (24 entries/6 families) in docs/CAPABILITIES.md. Hosted-free validation ladder LIVE 07-16 (Tier-0 deterministic → gemini-flash-lite → groq → local → paid; latency breaker). Mini2 Hermes host + cross-box dispatch LIVE 07-16 (forced-command SSH gate); OpenClaw SHUT DOWN on this box 07-16 (bot freed for Hermes). Conversational-compute decree 07-17 + NOW-lane triage built same day. Public viewer LIVE at maro.feifdom.com 07-17. Budget posture: openrouter/openai keys dead 07-17, backend_order=subprocess+anthropic. Hermes propose lane 07-20 (zero GitHub creds on mini2, land verb). Introspection-container provisioning decree 07-18.` },
]

const ERA_SCHEMA = {
  type: 'object',
  required: ['era_name', 'date_range', 'architecture_then', 'discoveries', 'pros_vs_current', 'cons_vs_current', 'believed_then_wrong_now', 'lost_good_ideas', 'sources_read'],
  properties: {
    era_name: { type: 'string' },
    date_range: { type: 'string' },
    boundary_commits: { type: 'object', properties: { first: { type: 'string' }, last: { type: 'string' }, representative: { type: 'string' } } },
    architecture_then: { type: 'string', description: 'What the system actually was at era end, verified by reading the tree at the representative commit (git show). 2-5 paragraphs.' },
    discoveries: { type: 'array', items: { type: 'object', required: ['title', 'aha', 'evidence'], properties: { title: { type: 'string' }, date: { type: 'string' }, aha: { type: 'string', description: 'what changed in our thinking' }, evidence: { type: 'array', items: { type: 'string' }, description: 'commit hashes, file:line, doc citations' } } } },
    pros_vs_current: { type: 'array', items: { type: 'object', required: ['point'], properties: { point: { type: 'string' }, evidence: { type: 'string' } } }, description: 'what that era architecture had going for it vs today — including simplicity or good ideas later lost' },
    cons_vs_current: { type: 'array', items: { type: 'object', required: ['point', 'status'], properties: { point: { type: 'string' }, status: { type: 'string', description: 'resolved-since | still-present' }, evidence: { type: 'string' } } } },
    believed_then_wrong_now: { type: 'array', items: { type: 'string' } },
    lost_good_ideas: { type: 'array', items: { type: 'object', required: ['idea', 'why_lost'], properties: { idea: { type: 'string' }, why_lost: { type: 'string' }, worth_reviving: { type: 'string' } } } },
    sources_read: { type: 'array', items: { type: 'string' } },
  },
}

const VERIFY_SCHEMA = {
  type: 'object',
  required: ['verdicts', 'summary'],
  properties: {
    verdicts: { type: 'array', items: { type: 'object', required: ['claim', 'verdict'], properties: { claim: { type: 'string' }, verdict: { type: 'string', description: 'CONFIRMED | REFUTED | UNVERIFIABLE' }, correction: { type: 'string', description: 'if REFUTED: what the evidence actually shows' } } } },
    summary: { type: 'string' },
  },
}

const WRITE_SCHEMA = {
  type: 'object',
  required: ['path', 'timeline_line', 'abstract', 'edges_for_plan'],
  properties: {
    path: { type: 'string' },
    timeline_line: { type: 'string', description: 'one summary-timeline entry: dates — era name — the defining aha, <=40 words' },
    abstract: { type: 'string', description: '5-8 line abstract of the era for the synthesizer' },
    edges_for_plan: { type: 'array', items: { type: 'string' }, description: 'lost good ideas / prior art / context the current swarm-review implementation plan should absorb' },
  },
}

function excavatePrompt(era) {
  const gitScope = era.git
    ? `Your commit range: git log ${era.git} in ${REPO}. Pick first/last/representative commits from it. Read key files AT the representative commit via git show <hash>:<path> — describe the architecture as it WAS, not as it is now.`
    : `This era predates the repo. No git archaeology; use the listed external sources. boundary_commits may note the repo's first commit (97473ac) as the era's end marker.`
  return `You are an era archaeologist for the maro-orchestration project (repo: ${REPO}). Today is ${TODAY}. Reconstruct ONE era of the project's knowledge journey with evidence.

ERA: ${era.name} (${era.range})
${gitScope}

LEADS (verify before trusting — these are hints from a scout, not established facts):
${era.hints}

Cross-era sources to slice for YOUR date range only: docs/history/ROADMAP_ARCHIVE.md, docs/history/CHANGELOG.md, BACKLOG_DONE.md, GOAL_BRAIN.md Decisions section (dated lines), docs/history/*.md dated in-range, and the auto-memory archive at ~/.claude/projects/-home-clawd-claude/memory/archive/. The dev-recall FTS index is available for rationale mining: cd ${REPO} && PYTHONPATH=src python3 -m correspondence query "<terms>" (read-only).

What matters most: (1) the AHA MOMENTS — where the project's THINKING changed, not just what shipped; (2) architecture-as-it-was, verified at the representative commit; (3) honest pros of that era vs today (simplicity counts; so do good ideas that later got lost); (4) cons vs today, marked resolved-since or still-present; (5) beliefs held then that we now know were wrong; (6) lost good ideas worth reviving. Quote Jeremy only from written records (GOAL_BRAIN Invariants/Decisions, docs), verbatim, sparingly. Never print secrets/tokens. Every factual claim needs a citation (commit hash, file path, doc section). If a lead can't be verified, say UNVERIFIED rather than repeating it. Return the structured era record.`
}

function verifyPrompt(record, era) {
  return `You are an adversarial verifier. Another agent produced a historical era record for the maro-orchestration project (repo: ${REPO}); era: ${era.name} (${era.range}). This project's own data shows 30-78% of unverified reviewer claims are wrong — assume errors exist and hunt for them.

ERA RECORD (JSON):
${JSON.stringify(record)}

Extract the CHECKABLE factual claims (commit hashes exist and match their description; quoted text appears verbatim in the cited doc; "X existed/didn't exist at commit C" — check via git show C:path; dates; version numbers; "found dead today" claims) and verify each against git and the docs. Prioritize: commit hashes, verbatim quotes, at-the-time architecture claims (these are where archaeologists hallucinate). Aim to check the 10-20 most load-bearing claims, not every sentence. Verdict per claim: CONFIRMED / REFUTED (with correction) / UNVERIFIABLE. Do not verify opinions (pros/cons judgments) — only their factual predicates. Read-only; never print secrets.`
}

function writePrompt(record, verdicts, era) {
  return `You are the writer for one era of the maro-orchestration knowledge journey. Today is ${TODAY}.

ERA RECORD: ${JSON.stringify(record)}
VERIFICATION VERDICTS: ${JSON.stringify(verdicts)}

Write ${OUTDIR}/${era.n}-${era.slug}.md (mkdir -p the directory first; overwrite if present). Apply the verdicts: REFUTED claims get corrected per the verifier (or dropped if unsalvageable); UNVERIFIABLE load-bearing claims get an explicit "(unverified)" mark; CONFIRMED stand. House style: start with frontmatter (three dashes, status: history, three dashes), then # ${era.name}, *${era.range}*. Sections: ## Architecture as it was, ## Discoveries & aha moments (subsection per discovery with evidence lines), ## Pros vs today's architecture, ## Cons vs today's architecture (mark each resolved-since/still-present), ## What we believed then, ## Lost good ideas (with worth-reviving notes), ## Sources. Keep it tight and factual — a future session should read this in 3 minutes. Cite commits as short hashes, docs as paths. Write the file, then return: path, timeline_line (dates — era name — defining aha, <=40 words), abstract (5-8 lines), edges_for_plan (lost ideas/prior art the current swarm-review implementation plan should absorb; empty array if none).`
}

log(`Excavating ${ERAS.length} eras serially — box OOM'd twice under load; one live agent at a time, resume-cache banks each step`)

const results = []
for (const era of ERAS) {
  const record = await agent(excavatePrompt(era), { label: `dig:${era.slug}`, phase: 'Excavate', schema: ERA_SCHEMA })
  if (!record) { results.push(null); continue }
  const verdicts = await agent(verifyPrompt(record, era), { label: `verify:${era.slug}`, phase: 'Verify', schema: VERIFY_SCHEMA })
  const out = await agent(writePrompt(record, verdicts || { verdicts: [], summary: 'verifier unavailable' }, era), { label: `write:${era.slug}`, phase: 'Write', schema: WRITE_SCHEMA })
  if (!out) { results.push(null); continue }
  const refuted = (verdicts?.verdicts || []).filter(v => v.verdict === 'REFUTED').length
  results.push({ era: era.n, slug: era.slug, refuted_claims: refuted, ...out })
  log(`era ${era.n} ${era.slug} complete (${results.filter(Boolean).length}/${ERAS.length})`)
}

const done = results.filter(Boolean)
log(`${done.length}/${ERAS.length} era files written; running completeness critic`)

const critic = await agent(
  `You are the completeness critic for the maro-orchestration knowledge-journey history (repo: ${REPO}). Twelve era detail files were just written to ${OUTDIR}/ covering Feb 2026 through ${TODAY}. Read all of them, then hunt for what is MISSING from the set as a whole: (1) discoveries/aha moments recorded in BACKLOG_DONE.md, GOAL_BRAIN.md Decisions, docs/history/, or ~/.claude/projects/-home-clawd-claude/memory/archive/ that no era file mentions; (2) through-line themes that recur across eras but no file names (candidates to check: entropy/decay, done-vs-achieved, visibility rungs, verification-over-trust, steal-don't-build, the recurring gap between read-wiring and write-wiring); (3) contradictions BETWEEN era files; (4) any era whose pros-vs-today section is empty flattery rather than real tradeoffs. Return your findings as plain text, most important first, each with a citation. Read-only — do not edit the files.`,
  { label: 'critic:completeness', phase: 'Critique' }
)

return {
  eras: done.map(d => ({ n: d.era, slug: d.slug, path: d.path, timeline_line: d.timeline_line, abstract: d.abstract, edges_for_plan: d.edges_for_plan, refuted_claims: d.refuted_claims })),
  missing: results.length - done.length,
  critic,
}