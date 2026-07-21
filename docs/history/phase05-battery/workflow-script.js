export const meta = {
  name: 'phase05-battery',
  description: 'Phase 0.5 with-doc vs control battery over real ground-truth violations (6 serial agents)',
  phases: [{ title: 'Battery', detail: 'six serial dev-task agents, alternating control/doc arms' }],
}
const REPO = '/home/clawd/claude/maro-orchestration'

const COMMON = `You are doing a scoped development task on the maro-orchestration repo at ${REPO} (an autonomous-agent orchestration framework, ~130 Python modules in src/). Work primarily from source code: for EVERY claim you make about current behavior, cite the src/ file and line evidence. Docs may be stale — verify anything you take from them against the code and say which you relied on. Do NOT modify any files; this is analysis/planning only. Your final output must be the structured report (it is data for an adjudicator, not a human-facing message).`

const DOC_PREAMBLE = `FIRST, read ${REPO}/docs/DEV_PATTERNS.md and apply BOTH halves: the taste half to how you shape any plan you produce, and the judgement half to every claim and finding you report. `

const TASKS = [
  { key: 'T1', prompt: `Design task: maro should capture design decisions made during runs (interface choices, formats, conventions) so future runs benefit from them. First assess what exists today around decision capture, storage, and recall in src/. Then produce a plan: concrete steps, and what "done" means. Flag any existing design debt you find along the way.` },
  { key: 'T2', prompt: `Review task: assess maro's learning lifecycle — how lessons and rules are recorded, tiered, injected into prompts, and contested/decayed over time. Identify design debt: places where the intended lifecycle is not actually wired, dead or unreachable paths, and anything shipped but never adjudicated. Evidence required for every finding.` },
  { key: 'T3', prompt: `Design task: improve how the director's playbook (operational wisdom at ~/.maro/workspace/playbook.md) reaches prompts. First assess the current injection behavior in src/ (what is selected, how much, from where). Then produce a plan: concrete steps, and what "done" means.` },
]

const SCHEMA = {
  type: 'object',
  required: ['findings', 'plan_summary', 'done_means', 'consumers_named', 'tripwires_named', 'cuts_or_inversion'],
  properties: {
    findings: { type: 'array', items: { type: 'object', required: ['claim', 'evidence', 'source'], properties: {
      claim: { type: 'string' }, evidence: { type: 'string', description: 'file:line citations' },
      source: { type: 'string', enum: ['code', 'docs', 'both'] } } } },
    plan_summary: { type: 'string', description: 'the plan steps, or "n/a" for pure review tasks' },
    done_means: { type: 'array', items: { type: 'string' }, description: 'named executed checks that would verify done; empty if none named' },
    consumers_named: { type: 'array', items: { type: 'string' }, description: 'for each store/emitter in the plan, the acting consumer named; empty if none' },
    tripwires_named: { type: 'array', items: { type: 'string' }, description: 'tests/assertions named that would catch regressions of the principles adopted; empty if none' },
    cuts_or_inversion: { type: 'string', description: 'what was ruled out / what would make this wrong to build; empty string if not considered' },
  },
}

const results = {}
phase('Battery')
for (const t of TASKS) {
  for (const arm of ['control', 'doc']) {
    const preamble = arm === 'doc' ? DOC_PREAMBLE : ''
    const r = await agent(`${preamble}${COMMON}\n\n${t.prompt}`, {
      label: `${t.key}-${arm}`, phase: 'Battery', schema: SCHEMA,
    })
    results[`${t.key}-${arm}`] = r
    log(`${t.key}-${arm} done`)
  }
}
return results