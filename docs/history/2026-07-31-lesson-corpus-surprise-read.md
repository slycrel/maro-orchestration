---
status: record
---

# Lesson-corpus surprise read (2026-07-31)

**Status: reading artifact** — snapshot of the live tiered lesson store
(`~/.maro/workspace/memory/{long,medium}/lessons.jsonl`) taken 2026-07-31 for
the LeAct surprise-count diagnostic
(see `2026-07-31-leact-opus-contrast.md` and the BACKLOG LeAct entry).
One town name from a location-research run redacted for the public copy;
otherwise verbatim. The store itself is untouched.

## How to do the read

Count the entries that **surprise** you — not the ones that are wrong.
"Surprise" = you would not have written this yourself; it taught the system
something you didn't already carry. Wrong-but-unsurprising and
right-but-obvious both count as zero.

- **Zero surprises** after ~150 runs ⇒ mirroring collapse confirmed
  empirically — the corpus is a restatement of your own priors.
- **Nonzero** ⇒ those entries are the seed set: exactly the lessons the
  current tenure gate fails to protect, and the concrete targets a Δ-gate
  experiment would be evaluated against.

React in a new session with entry numbers (`L1–L4`, `M1–M131`) — a bare list
plus anything you'd argue with is plenty.

## What the mechanical companion count says (before your read)

- **The entire LONG tier (4 rows) got tenure via machine restatement.** All
  four are recovery-path template strings the recovery subsystem re-mints
  verbatim on repeat firings — `sessions_validated` 7–35 came from the same
  code path re-emitting the same sentence, not from independent
  re-derivation. All four carry novelty 0.00. This is a sharper version of
  the restatement-tenure critique than Opus had: the only lessons with
  permanence are the ones a deterministic loop restates for free.
- **Medium tier: 36 of 131 rows have novelty > 0 — every one minted on or
  after 2026-07-27** (the chunk-6 novelty term ships the field; older rows
  default 0.00). The high-novelty cohort at the top is where surprises are
  most likely to live; the long novelty-0.00 tail is the pre-chunk-6 era.
- Nothing here has ever been retired by contradiction; decay and GC are the
  only exits so far.

---

## LONG tier — the tenure holders (L1–L4)

**L1** — score 1.00 · novelty 0.00 · validated 35x · applied 1x · 2026-06-11 · agenda
> [recovery-plan] token_explosion: Distill prior step outputs to key findings before continuing. Keep full output in artifacts.

**L2** — score 1.00 · novelty 0.00 · validated 10x · applied 2x · 2026-06-23 · agenda
> [recovery-verified] retry-with-hint unblocked a run: step 5 blocked ([ralph verify] The result does not directly address the step); retry 1 with hint

**L3** — score 1.00 · novelty 0.00 · validated 8x · applied 1x · 2026-07-02 · agenda
> [recovery-plan] adapter_timeout: Retry with smaller step scope or switch to API adapter

**L4** — score 1.00 · novelty 0.00 · validated 7x · applied 2x · 2026-06-11 · agenda
> [recovery-plan] decomposition_too_broad: Re-run with tighter max_steps and code-surface cap


## MEDIUM tier (M1–M131), sorted by current score

**M1** — score 1.27 · novelty 0.89 · validated 0x · applied 0x · 2026-07-29 · agenda
> [planning] Goals that enumerate numbered deliverable sections (claim table, architecture map, ranked next steps, verdict) should be decomposed one step per section, plus explicit evidence-gathering steps feeding them. A run that ends 0/0 steps on a 5-part memo means the decomposer treated a structured deliverable as one unstructured research blob, leaving nothing to execute or resume.

**M2** — score 1.26 · novelty 0.88 · validated 0x · applied 0x · 2026-07-31 · agenda
> When asked whether an external research idea should change a system's roadmap, structure the answer to separate three layers explicitly: (1) what the source's evidence actually shows, (2) which sub-ideas are genuinely transferable versus superficially similar, and (3) the smallest reversible, non-destructive experiment (with a concrete oracle and success/failure criterion) needed to test transferability. This keeps recommendations from drifting into premature large-scope changes (e.g. training/fine-tuning) when a cheap targeted test would answer the question.

**M3** — score 1.26 · novelty 0.87 · validated 0x · applied 2x · 2026-07-29 · agenda
> [planning-agenda] Diagnosis tasks that explicitly list observed failure symptoms (e.g., 'escalated instead of attempting recoverable retrieval') produce more decision-grade output when the memo maps each symptom to a specific repo mechanism (file/symbol) rather than treating symptoms as generic agent behavior. Structuring the task as 'explain these N symptoms via actual code evidence' prevents drift into generic commentary.

**M4** — score 1.26 · novelty 0.87 · validated 0x · applied 0x · 2026-07-29 · agenda
> In multi-report diagnosis chains (this was the third of three complementary self-diagnoses), treat prior reports' claims as hypotheses to re-verify against current repo/behavior evidence rather than inherited ground truth, and require an explicit completion contract (all sections filled, no unresolved TODOs, uncertainty labeled) so a partial memo cannot be marked done.

**M5** — score 1.26 · novelty 0.87 · validated 0x · applied 0x · 2026-07-29 · agenda
> [execution] For long analysis/memo tasks, emit the section skeleton with UNVERIFIED/UNKNOWN placeholders as the first artifact, then fill sections in place. This guarantees a partial, honestly-labeled deliverable survives a stall, instead of the all-or-nothing failure where a stuck run returns zero output despite substantial available local evidence.

**M6** — score 1.26 · novelty 0.87 · validated 0x · applied 0x · 2026-07-27 · agenda
> When a prompt explicitly says 'do not escalate/stop because X is inaccessible' and gives recovered facts as sufficient context, treat that as a hard constraint on the final output, not just on the planning phase: the finished deliverable must still contain concrete, purchase-ready conclusions rather than reverting to hedged 'more research needed' framing once the blocked resource is mentioned again.

**M7** — score 1.26 · novelty 0.87 · validated 0x · applied 0x · 2026-07-27 · agenda
> [recovery-plan] token_explosion: When a task requires N similar detailed items (e.g., 3 tire recommendations with specs/pricing/URLs), producing them in one large final-answer pass risks hitting output limits before completion. Instead, draft each item as a compact structured block (brand/model/size/load-index/price/URL) incrementally and assemble at the end, so a cutoff loses only the last item rather than the whole synthesis.

**M8** — score 1.26 · novelty 0.86 · validated 0x · applied 1x · 2026-07-29 · agenda
> [verification-diagnosis] When comparing a repo's behavior against an external reference pattern, run an adversarial re-check pass at the end that re-verifies each cited file/symbol claim against the actual source tree before finalizing — catches stale or invented citations that would otherwise undermine a decision-grade memo's credibility.

**M9** — score 1.26 · novelty 0.85 · validated 0x · applied 0x · 2026-07-27 · agenda
> [recovery-plan] repeat_blocker: This run was a repair of a prior dispatch that failed for the same reason (missing exact specs/current pricing with sources). When retrying a task that previously failed on a specific requirement, add an early checkpoint that re-tests exactly that requirement (e.g., 'can I actually find and verify a live price+URL for candidate X?') before running the rest of the plan, instead of discovering the same blocker again only at the end.

**M10** — score 1.26 · novelty 0.85 · validated 0x · applied 0x · 2026-07-29 · agenda
> On an output-token-limit cutoff mid-deliverable, resume by continuing the exact unfinished section without restating or re-summarizing completed sections — re-deriving prior output burns the same budget that caused the original stall and risks a second cutoff before the deliverable's completion contract (e.g., a required final section) is satisfied.

**M11** — score 1.26 · novelty 0.85 · validated 0x · applied 2x · 2026-07-29 · agenda
> For agenda/research tasks citing a nominally-blocked source (e.g., an X/Twitter link), attempt recovery via cache/mirror/alternate credible reporting before escalating or halting. Maro has a documented pattern of escalating on sources that were in fact recoverable; the task explicitly had to instruct 'source was recovered already, do not block on direct X access' to prevent this, indicating the default behavior over-escalates.

**M12** — score 1.25 · novelty 0.85 · validated 0x · applied 0x · 2026-07-29 · agenda
> When a source bundles structural/architectural reasoning with quantitative benchmark claims (e.g., 'HumanEval shows X outperforms Y'), verify each independently rather than letting confidence in the structural argument transfer to the numeric claim. Benchmark claims embedded in persuasive articles are frequently cherry-picked or unsupported even when the surrounding reasoning is sound.

**M13** — score 1.25 · novelty 0.85 · validated 0x · applied 0x · 2026-07-27 · agenda
> Tasks demanding 'exact per-model load ranges/indexes and current line-item pricing' require per-item verification against a manufacturer spec page AND a live retailer price page, not a single category/search page — budget time explicitly for two lookups per recommended item (spec confirmation + price confirmation) rather than one.

**M14** — score 1.25 · novelty 0.85 · validated 0x · applied 0x · 2026-07-31 · agenda
> When evaluating whether an externally-sourced idea (paper, social post) should inform an internal system, save the primary source content locally and grep it for the specific claims being cited before finalizing a verdict. This adversarial verification pass catches cases where a secondary summary (e.g. a social media post) generalizes beyond what the primary source's actual experimental scope supports.

**M15** — score 1.25 · novelty 0.85 · validated 0x · applied 0x · 2026-07-31 · agenda
> For research tasks that require fetching external URLs (social posts, arXiv pages, etc.), a single failed fetch attempt is not terminal — try an alternate retrieval path (different URL form, cached/saved HTML, abstract page instead of PDF) before reporting the source as inaccessible. Bake this expectation into the task framing up front rather than discovering it mid-run.

**M16** — score 1.25 · novelty 0.84 · validated 0x · applied 0x · 2026-07-29 · agenda
> When a task bundles external-claim verification (e.g., independently checking a cited benchmark stat) with deep internal evidence-gathering (repo files, past run behavior) into one deliverable, verify external claims tersely and reserve most of the output budget for internal evidence — treating both at equal depth is what pushed this run over its budget before the ranked-recommendations section was reached.

**M17** — score 1.25 · novelty 0.84 · validated 0x · applied 0x · 2026-07-29 · agenda
> Memos combining a claim-verification table, full architecture-to-framework mapping, and ranked recommendations-with-acceptance-tests routinely exceed one response's output budget. Scope such tasks up front as separate generation passes (e.g., claim table first, architecture mapping second, recommendations third) rather than one monolithic write-up, so a token-limit cutoff loses at most one section instead of stalling mid-synthesis.

**M18** — score 1.25 · novelty 0.84 · validated 0x · applied 0x · 2026-07-31 · agenda
> When step-tracking tools (e.g., complete_step) are not wired into the execution environment, step counts like '9/14' become unreliable and should not be treated as a progress signal. Verify goal achievement directly against the actual deliverable, and explicitly flag the broken tool wiring to the user/system instead of silently reporting a partial step count alongside a 'done' status.

**M19** — score 1.25 · novelty 0.83 · validated 0x · applied 0x · 2026-07-31 · agenda
> A large gap between reported step completion and a 'verified achieved' outcome (e.g., 9/14 steps but goal done) should be reconciled and explained before finalizing the summary — it usually means either the step plan over-decomposed the task or the completion tracker is broken, and the user should be told which.

**M20** — score 1.25 · novelty 0.83 · validated 0x · applied 0x · 2026-07-27 · agenda
> [planning] When a deliverable requires currently-valid external facts (live prices, direct product URLs, exact spec fields per item), the plan should treat 'verify each required field is present and current' as its own explicit step, not an assumed side-effect of research steps. Marking 4/4 steps done while still landing 'stuck' shows step-completion was tracked separately from goal-completion — the checklist should mirror the deliverable's required fields (e.g., one verification step per required field per item) so partial/stale data is caught before declaring done.

**M21** — score 1.25 · novelty 0.83 · validated 0x · applied 0x · 2026-07-29 · agenda
> When a house style file exists for this artifact family, verify the new artifact matches it on concrete structural points (heading hierarchy, example count/format, closing summary) rather than just overall similarity, since 'mirroring style' is a fuzzy claim that should be checked line-by-line before marking the goal verified.

**M22** — score 1.25 · novelty 0.82 · validated 0x · applied 0x · 2026-07-27 · agenda
> [verification] For 'purchase-ready' or 'current price' requirements, completion should be judged by whether every required per-item field (exact model, load range/index, price, URL) is independently confirmable from the cited source, not by whether a plausible-sounding item was written down. If a required field can't be verified from an available source, that item should be flagged or swapped rather than the run proceeding to a final answer that silently omits verification.

**M23** — score 1.25 · novelty 0.82 · validated 0x · applied 1x · 2026-07-29 · agenda
> [execution-agenda] Tasks that say 'do NOT stop at planning or escalate because a link cannot be fetched' and 'the gist has already been recovered' are pre-empting a known failure mode (external-URL blocking). When a task explicitly pre-supplies facts to route around a tool limitation, proceed directly from those supplied facts rather than re-attempting the blocked path or re-raising the same blocker.

**M24** — score 1.25 · novelty 0.82 · validated 0x · applied 1x · 2026-07-29 · agenda
> For 'summarize unix command + N worked examples' tasks that are part of a series (e.g., join-examples.md, paste-examples.md), check for an existing sibling artifact first and mirror its structure/section order/tone rather than designing the format from scratch. This keeps the series consistent and removes formatting decisions from the critical path.

**M25** — score 1.25 · novelty 0.82 · validated 0x · applied 0x · 2026-07-29 · agenda
> For iterative self-diagnosis chains that reference prior reports (e.g., 'third complementary review after X and Y'), explicitly instruct the agent to inspect and correct prior conclusions rather than assume they are correct — this catches errors or stale claims compounding across sequential reviews instead of propagating them.

**M26** — score 1.24 · novelty 0.81 · validated 0x · applied 0x · 2026-07-31 · agenda
> For 'contrast external content with a repo' style agenda tasks, plan the external fetch (e.g., reading tweets/social posts) as an isolated early step, since fetching third-party social media content is the most failure-prone part of the task and should be confirmed successful before building analysis steps on top of it.

**M27** — score 1.24 · novelty 0.81 · validated 0x · applied 0x · 2026-07-27 · agenda
> When a prior task attempt was marked incomplete for a specific, named reason (e.g., missing exact specs/pricing), treat that reason as the primary acceptance criterion and verify it is fully satisfied for every deliverable item before finalizing, rather than re-doing the general research and assuming the gap is closed.

**M28** — score 1.24 · novelty 0.80 · validated 0x · applied 0x · 2026-07-27 · agenda
> When a task specifies an exact output contract (e.g., '3 tiers, each with brand/model, size, load index, real price, seller link'), mark it done only after checking each required field is concretely present in the deliverable — not just that a file was written. A run can complete its internal steps and still fail the goal if the final artifact is vague, hedged, or missing required specifics like real prices/links.

**M29** — score 1.24 · novelty 0.80 · validated 0x · applied 0x · 2026-07-29 · agenda
> [recovery] Treat pre-authorization clauses in the goal ('source already recovered, do not block on direct access', 'finish even if cited sources are inaccessible, label uncertainty') as a standing waiver for that specific failure mode: an unreachable URL must trigger label-and-continue with alternative sources, never a stuck/escalation state. Check the goal text for such waivers before declaring any source-access blocker.

**M30** — score 1.24 · novelty 0.79 · validated 0x · applied 0x · 2026-07-29 · agenda
> When a task cites a social-media source (X/Twitter thread, quoted article, etc.) that may be blocked or inaccessible live, explicitly pre-authorize use of already-recovered/cached content and forbid retrying direct access in the task framing — this prevents the agent from stalling or escalating dispatch on a source that is actually already recoverable.

**M31** — score 1.23 · novelty 0.77 · validated 0x · applied 0x · 2026-07-29 · agenda
> When a task asks the agent to react to a source's quantitative or benchmark claims (e.g., 'agentic workflow X outperforms model Y on benchmark Z'), explicitly require independent verification or qualification rather than allowing the claim to be repeated — social/marketing threads about agent architecture frequently overstate benchmark results, and a claim table with verified/misleading/unsupported labels catches this cheaply.

**M32** — score 1.13 · novelty 0.42 · validated 0x · applied 0x · 2026-07-31 · agenda
> [recovery-verified] re-decompose, retry-with-hint unblocked a run: step 1 blocked ([ralph verify] The result promises to deliver the quoted twe); retry 1 with hint

**M33** — score 1.12 · novelty 0.38 · validated 0x · applied 0x · 2026-07-31 · agenda
> [recovery-verified] retry-with-hint unblocked a run: step 3 blocked ([ralph verify] The result reports that the files do not exis); retry 1 with hint

**M34** — score 1.00 · novelty 0.86 · validated 2x · applied 0x · 2026-07-27 · agenda
> [recovery-plan] cost_spike: Route the costly step class to a cheaper model tier, or shrink the cached context the worker carries per turn.

**M35** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-10 · general
> Before Write/Bash calls targeting file creation, validate: (1) target path is within /home/clawd/.maro/workspace/ unless explicitly authorized elsewhere, (2) parent directory exists or can be created, (3) filename is complete/valid. Add early validation step to prevent writes to unexpected locations.

**M36** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-04 · agenda
> Well-scoped creative tasks with a single output (write haiku, save to file) need minimal planning overhead — direct execution is faster than decomposition. Reserve planning for multi-step or ambiguous goals.

**M37** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-09 · agenda
> For 'write generated content to a file' tasks, explicitly verify the output against stated structural constraints (e.g., line count, format) by reading the file back after writing, rather than assuming the write succeeded correctly.

**M38** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-09 · agenda
> When the goal specifies an exact output filename, write directly to that path in the working directory instead of spending steps searching for or confirming the location.

**M39** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-09 · agenda
> Small single-artifact creative/generation tasks (e.g., short poems, snippets) need only a minimal plan: generate content, write file, verify — avoid over-decomposing into extra steps.

**M40** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-22 · agenda
> [verification-agenda] structural_constraints: When a goal specifies a concrete structural constraint (e.g., 'two-line note', 'under 500 words', 'JSON with 3 keys'), verify it programmatically against the actual output (e.g., count lines/words/keys) rather than relying on the content 'looking right'. This catches subtle misses that content-quality review alone won't.

**M41** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-22 · agenda
> [planning-agenda] minimal_decomposition: For small, fully-specified content-creation goals (write X explaining Y, save to file Z), a 2-step plan (produce content, write+verify) is sufficient. Avoid adding extra steps like outlining, drafting, or multi-pass review for tasks this simple — it adds cost without improving the outcome.

**M42** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-22 · agenda
> [execution-agenda] exact_filename: When the goal names an exact output filename, write directly to that filename instead of a temp/staging file, avoiding an unnecessary rename/move step and a source of potential mismatch during verification.

**M43** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-09 · agenda
> File write operations should include verification (stat/read-back) immediately after writing to catch path issues, encoding mismatches, or partial writes before the task is marked complete

**M44** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-10 · agenda
> [execution] For JSONL file analysis, parse only required fields rather than full records—use streaming JSON parse to extract targeted fields, reducing memory footprint and improving throughput on large files.

**M45** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-09 · agenda
> For file-based deliverables, verify by reading back the output file to confirm it meets the stated format and requirements—catches trivial errors and confirms success in one quick pass.

**M46** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-22 · agenda
> [recovery-plan] retry_churn: Skip the churning step and continue with remaining steps

**M47** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-22 · agenda
> [recovery-verified] retry-with-hint unblocked a run: step 2 blocked ([deliverable-path-miss] step names output path(s) that do no); retry 1 with hint

**M48** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-22 · agenda
> [verification-agenda] Never mark a task 'done' when fewer than all planned steps are complete (e.g., 7/9). Before emitting a final status, explicitly compare completed-step count to total-step count; if they don't match, the status must be 'incomplete' or 'blocked', not 'done'.

**M49** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-22 · agenda
> [planning-agenda] When a goal contains an explicit secondary requirement embedded in prose (e.g., 'record your format choice as a design decision'), convert it into its own standalone checklist item with independent pass/fail verification — don't assume it's satisfied as a side effect of building the primary artifact (the script), since this is a common reason for full-goal judged-failure despite the main deliverable working.

**M50** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-22 · agenda
> [recovery-agenda] If adversarial/self-verification surfaces gaps in early steps (e.g., steps 1-6 verified but 7-9 unstarted), halt forward progress and resolve or explicitly flag the gap before reporting completion, rather than letting unresolved steps silently accumulate until the final summary.

**M51** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-04 · agenda
> For script-write-and-run tasks, validate output immediately after execution (before formatting/saving) to catch logic errors cheaply before investing in presentation

**M52** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-09 · agenda
> Well-specified creative tasks with clear constraints (format, scope, output location) can be executed directly without extensive planning—reducing latency for agenda-type work

**M53** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-10 · agenda
> Gather output schema and collision-check constraints before the main write step. Read reference examples to extract exact patterns (frontmatter structure, formatting conventions) and validate uniqueness requirements (trigger values, function names) upfront. This prevents rework from schema violations or naming collisions post-write.

**M54** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-11 · agenda
> [execution] For factual lookup or location-finding tasks, do a final sanity check before marking complete: re-read the user's original question and confirm the deliverable directly answers it. This catches cases where you completed the plan but solved the wrong problem.

**M55** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-10 · agenda
> Partition verification into focused single-concern checks rather than one monolithic verification. Structure syntax/field validation separately from content quality validation (examples, edge cases, completeness). This isolates failure modes—a YAML error doesn't cause false negatives in content checks—and makes each verifier's pass/fail decision atomic.

**M56** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-09 · agenda
> [verification-synthesis] planted-contradictions: For synthesis/aggregation workflows, verify with fixtures containing deliberate contradictions. Test that conflicts surface in dedicated sections rather than being lost through averaging or omission. Generalizes to any multi-source conflict-handling task.

**M57** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-09 · agenda
> When a task deliverable is 'a skill definition matching an existing format,' read the reference file (e.g. skills/web_research.md) first to extract the exact frontmatter schema and section structure, then mirror it, rather than inferring the format from the task description alone.

**M58** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-09 · agenda
> [planning] Verification-heavy tasks should start with success criteria and fixture design before implementation—this surfaces ambiguity (e.g., confirmed vs. speculative distinction) upfront rather than during rework cycles.

**M59** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-10 · agenda
> Structure multi-step artifact tasks as explicit DAGs with numbered steps and [after:X] dependencies rather than linear sequences. Independent steps (e.g., read reference + collect triggers) can run in parallel, while dependent steps wait correctly. This scales better than flat task lists and makes concurrency safe without extra coordination.

**M60** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-12 · agenda
> [execution] Multi-platform social content research should parallelize query angles within each platform (concurrent Reddit subreddit/query combos, concurrent X search queries) before aggregating, not serialize platform-by-platform. This avoids blocking the entire run on one platform's slow/failing search phase. Also, Twitter-specific: auth cookies expire mid-session (~1h); warm-up should be re-checkpointed and re-run if search calls fail, not left as manual.

**M61** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-12 · agenda
> [recovery] Research tasks with platform-specific auth and content filtering should maintain incremental checkpoint files per platform/phase (entries_upgraded.json, reddit_new_by_subreddit.json, x_results_by_query.json) during execution. On mid-task failure, resume loads the last checkpoint and continues from there, not from the start. Without this, resuming a multi-hour research run re-scans the same subreddits/queries.

**M62** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-17 · agenda
> [verification] Contradiction-seeking against live sources for recommendations: When evaluating tools/integrations/features, construct opposing claims ('this doesn't work for the use case', 'prerequisites make it infeasible') and verify against current live data, not cached knowledge. Catches stale claims and marketing overstatement.

**M63** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-12 · agenda
> [verification] Triangle verification for location-based research: primary source fetch → independent secondary cross-check (OSM) → re-fetch primary with modified query. This pattern caught data staleness and identified coverage gaps ([town] fell outside the second query's bounding box, flagging a coverage issue, not a data problem).

**M64** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-12 · agenda
> [verification-agenda] When a run reports a fractional completion count (e.g. '7/8 steps') alongside outcome='done', explicitly reconcile the two before marking goal-verified: enumerate the required deliverables from the goal text and confirm each exists with expected content, rather than trusting a step-count summary that may hide a skipped or degraded step.

**M65** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-17 · agenda
> [planning] Structured 7-step decomposition for research-and-recommendation agendas: (1) source identification, (2) claim extraction, (3) use-case fit assessment, (4) risk/cost/prerequisite analysis, (5) ranking synthesis, (6) live verification, (7) delivery with claim provenance. Ensures completeness and separates original sources from conclusions.

**M66** — score 1.00 · novelty 0.00 · validated 0x · applied 1x · 2026-07-11 · agenda
> [execution] Social-media-scrape-and-analyze tasks: collect with lightweight extraction during fetch (user quote, failure reason, one-word pattern hint) rather than hoarding raw JSON/HTML. Parse for signal as you gather, not after. Re-fetch full text only if verification needs it.

**M67** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-15 · agenda
> [execution] For claims with well-known counter-myths (like Napoleon's height), general web searches amplify the myth itself. Prioritize academic historians and fact-checking databases (Snopes, HistoryMatters, etc.) in the first pass—web results that merely debunk or discuss the myth aren't sources that verify the counter-claim.

**M68** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-12 · agenda
> [planning] Upfront success criteria (specific list + verified against independent source, not crowd-sourced alone) directed verification strategy and prevented scope-creep. This allowed stopping at the right depth rather than over-verifying or under-verifying.

**M69** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-09 · agenda
> [execution] When building a component with a reference format (e.g., 'see skills/web_research.md'), scaffold the format first—read the reference, create an empty template—before designing the complex logic. This decouples format dependencies from workflow design and unblocks parallel thinking.

**M70** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-10 · agenda
> When categorizing items from a directory listing, validate closure: verify that sum of all category counts equals total discovered items. This catches misclassifications and edge cases (uncategorizable items) before finalizing the summary.

**M71** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-04 · agenda
> [planning] Pre-specify comparison dimensions for research synthesis tasks—naming the exact techniques to compare (allowlist fencing, chroot-lite, copy-on-write) narrows scope and prevents exploratory rabbit holes vs. open-ended research.

**M72** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-15 · agenda
> [verification-loop] For iterative fixing tasks with output comparison, run the code immediately to establish baseline before any changes. This prevents false starts and clarifies the actual gap between current and expected state.

**M73** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-02 · agenda
> For translating technical docs to non-technical audiences, ruthlessly filter details by audience relevance—omit implementation specifics (env vars, file paths, commands) unless they're needed to understand value. Lead with outcomes before mechanism.

**M74** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-17 · agenda
> [recovery-research-stall] Synthesis-phase debugging: When multi-step research agendas stall during summary (step 3/5→table+takeaways), immediately diagnose: (1) Does the product/model actually exist? (2) What thread context was recoverable? (3) Token budget remaining? These three checks usually explain stuck state and determine whether to escalate or narrow scope.

**M75** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-11 · agenda
> [recovery] Checkpoint after raw-data collection and before synthesis/grouping step: save collected metadata (title, source, reason, pattern-hint) to a reusable form. If the grouping or artifact-write step stalls, future runs skip API fetches and resume at synthesis.

**M76** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-04 · agenda
> Format-governed appends (ledgers, schemas): read and extract the existing format pattern first (e.g., maturity-ladder structure), then validate new entries against it before commit to catch compliance errors in a single pass rather than through iteration.

**M77** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-15 · agenda
> [verification] Never claim a code change works without executing it fresh and observing the actual output—this catches subtle JSON formatting or logic errors that eyeballing would miss.

**M78** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-04 · agenda
> [execution] Atomic agenda tasks should execute the action tool immediately — for simple file creation, invoke Write without planning or confirmation-seeking. File creation is reversible and has no blast radius.

**M79** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-12 · agenda
> [verification] Content filtering by multi-part criteria (e.g., 'concrete task AND concrete failure') should tag rejections with a reason type ('title-only', 'vague_task', 'success_story', 'no_concrete_failure'). On resume, a previously-rejected entry with reason X can be skipped if the condition still holds, instead of re-evaluating edge cases. Keeps rejection tracking from being the bottleneck on incremental runs.

**M80** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-02 · agenda
> For data transformation tasks, verify by reading the actual output file rather than just checking exit codes—this catches format, encoding, and content issues that status checks miss

**M81** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-03 · agenda
> Script tasks with file outputs should validate the output format and content immediately after generation—file I/O errors and formatting issues are caught faster with early save-and-verify cycles rather than deferring validation until the end

**M82** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-02 · agenda
> [verification] Output verification for file-write tasks need not be exhaustive—simple checks (file existence + line count sanity) catch incomplete work without re-reading source, saving tokens on future digest/extract tasks.

**M83** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-03 · agenda
> Agenda tasks decomposed into many discrete steps (8+ checkpoints) rather than coarse phases (3-4 stages) allow earlier error detection—checkpoints between write/test/run/save catch integration misalignments before accumulated complexity makes rework expensive

**M84** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-04 · agenda
> [execution] For agenda-type research+synthesis, decompose into 8-10 discrete verifiable steps—fits in one session without context resets and maintains momentum across research-gather-synthesize-write.

**M85** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-09 · agenda
> Bounded creative tasks decompose cleanly into: generate, write, verify—sequencing these as separate discrete steps makes debugging and re-runs easier when output needs adjustment

**M86** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-16 · agenda
> [planning] Goals phrased as 'document evidence of X' need concrete success criteria defined upfront: what specific evidence counts as proof? What content makes the artifact sufficient? Without this clarity, task completion (artifact created) and goal achievement (goal proved) diverge.

**M87** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-15 · agenda
> [verification] When sources conflict on historical claims, 'stuck' often means sources exist but their relationship to the claim is ambiguous (e.g., source contradicts the claim, or source makes a weaker version). Check whether sources are primary, secondary, or interpretive—and whether they're *citing* a deeper source you haven't yet found.

**M88** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-09 · agenda
> When building a new runtime skill definition, explicitly diff its proposed triggers against every existing skill's AND persona's trigger list before finalizing — persona scopes (e.g. a 'reporter' persona's posture) can silently collide with a new skill's workflow triggers even though they live in different files.

**M89** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-03 · agenda
> [execution] On extraction/summary tasks, inspect source structure before designing processing logic. If source already matches output requirements (e.g., 'N bullet summary' and source has N bullets), transfer directly instead of extracting/synthesizing. Collapses scope, saves tokens.

**M90** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-12 · agenda
> [planning] Multi-source synthesis tasks should preemptively map input dependencies, required vs. optional sources, and correction references upfront. Without this scoping, gaps in step definitions surface midway (like at step 7/10) when rework is expensive.

**M91** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-16 · agenda
> [cost-scope] simple-deliverable: For single-artifact content tasks (e.g., 'produce one Markdown paragraph with N items'), a single generation pass plus a direct constraint checklist is sufficient — don't allocate a multi-step pipeline with adversarial re-verification of 'prior claims' when there are no prior claims or code to adversarially test.

**M92** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-16 · agenda
> [verification] Validating artifact format (well-formed structure) is distinct from validating artifact content against the goal. A syntactically valid artifact does not prove the goal was achieved if the content doesn't actually address the requirement. When the goal is 'verify X,' inspect that the artifact's substance proves X, not just that it conforms to schema.

**M93** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-02 · agenda
> Multi-format data tasks need explicit cross-verification: create each format independently, then compare counts and key fields. This surfaces inconsistencies that sequential/iterative creation can hide.

**M94** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-17 · agenda
> [planning] Set verification scope boundaries explicitly upfront (e.g., 'significant claims only', 'primary documentation', 'credible sources'). Prevents unbounded verification work and clarifies the stopping point for 'unverified' verdicts.

**M95** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-15 · agenda
> HTTP validation and fact-checking tasks need specialist execution: use deep-research skill, inline Bash curl checks, or code-reviewer agents rather than general agents. General agents optimize for 'sounds plausible' not 'provably correct'.

**M96** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-03 · agenda
> For tasks with measurable output requirements (line count, word count, format), use metric-based verification (wc -l, wc -w, grep patterns) rather than manual inspection. This objectively validates constraints and catches requirement violations reliably.

**M97** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-03 · agenda
> [planning] Agenda tasks requiring local service endpoints (127.0.0.1:port) must explicitly identify and verify prerequisites in decomposition — what services must be running, how to verify they're available. Stuck state here suggests the CI dashboard wasn't running.

**M98** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-12 · agenda
> [recovery] Log precise failure boundaries, not counts. '7/9: streams 1-2 done, stream 3 hung at Twitter query timeout (52s), stages 5-9 skipped' enables intelligent resume. Just '7/9 complete' is useless — future you doesn't know whether to retry, backfill, or skip ahead.

**M99** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-12 · agenda
> [recovery] When a multi-step task gets stuck at midpoint, pause to clarify with the user: (a) are step definitions clear from here onward, (b) have all input dependencies been mapped, (c) what does success look like for remaining steps. Ambiguous middle sections predict hangs.

**M100** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-16 · agenda
> For agenda goals producing output artifacts, define success criteria upfront: exact markdown format, required sections (command outputs, analysis text), and accessibility requirement (rendered to user vs. saved to file). Misalignment on 'done' is typically caught too late.

**M101** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-02 · agenda
> [verification] For tasks involving file operations (run, read, modify), verify the target file exists at the expected path before attempting the operation. This catches missing-resource errors at step 1 instead of failing partway through execution.

**M102** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-03 · agenda
> [recovery-verified] retry-with-hint unblocked a run: step 2 blocked (No service is listening on 127.0.0.1:59999. curl fails with ); retry 1 with hint

**M103** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-03 · agenda
> [verification] For repair/transformation tasks, validate by independently running the output through standard tooling (e.g., json.tool) rather than relying solely on the repair logic's self-validation

**M104** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-02 · agenda
> File format conversion tasks are easier to verify and debug when structured as: write the transformer → create representative test fixtures → execute → read output directly (rather than trusting the script blindly)

**M105** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-08 · agenda
> [recovery] When stuck on multi-component work, enumerate: (1) which component/deliverable is blocked, (2) root cause (missing prereq knowledge vs unclear spec vs API mismatch vs test failure), (3) minimum targeted fix. Different causes need different recovery paths; treat each separately rather than generic retries.

**M106** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-09 · agenda
> Separate evidence collection from root-cause proposal: gather logs → classify error types → *then* propose cause. Require evidence quotes in outputs to make diagnoses auditable. A diagnosis without quoted evidence lines is untestable.

**M107** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-09 · agenda
> State-dependent features like deduplication require fixture tests that verify output *differs* between runs — capture and diff both invocations' outputs rather than eyeballing, to catch silent persistence bugs that single-run tests miss

**M108** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-09 · agenda
> Complex state/dedup schemes need anti-pattern documentation alongside the happy path — include a 'Common traps' section listing failure modes (e.g., 'don't delete state files', 'don't hand-roll dedup logic') to prevent silent correctness bugs that survive unit tests and only surface in production

**M109** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-09 · agenda
> For skills whose job is to synthesize/merge multiple inputs (e.g. multi-agent artifact synthesis), verify correctness by constructing a minimal fixture set with one deliberately planted contradiction, then confirm the skill's output surfaces it in an explicit conflicts/discrepancies section rather than silently averaging or smoothing it away — this is a stronger correctness check than testing on harmonious inputs only.

**M110** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-04 · agenda
> [recovery-verified] retry-with-hint unblocked a run: step 3 blocked ([ralph verify] The result is vague and incomplete. It appear); retry 1 with hint

**M111** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-04 · agenda
> Breaking analysis workflows into atomic steps (write→run→parse→analyze→save) prevents large rework from intermediate errors; parallelizing write-run steps is not possible but validating in sequence is fast

**M112** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-04 · agenda
> Multi-deliverable ledger tasks: enumerate sub-steps upfront (deepen edge → add edge → append run note) to enable step-by-step tracking and prevent scope creep. The 9/9 completion suggests explicit decomposition prevents missed steps.

**M113** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-09 · agenda
> When building diagnostic workflows, emphasize recurrence patterns across multiple observations (boots, time windows, repeated runs) as a root-cause filter: chronic identical errors point to systematic causes, not transients. Bake this into the diagnostic method early.

**M114** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-09 · agenda
> [recovery-verified] retry-with-hint unblocked a run: step 2 blocked ([ralph verify] Result is a preparation statement with no act); retry 1 with hint

**M115** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-10 · agenda
> [recovery-verified] retry-with-hint unblocked a run: step 3 blocked ([ralph verify] Step required output to artifacts/triggers.tx); retry 1 with hint

**M116** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-10 · agenda
> [recovery-verified] retry-with-hint unblocked a run: step 4 blocked ([ralph verify] Result is a JSON schema definition, not actua); retry 1 with hint

**M117** — score 1.00 · novelty 0.00 · validated 0x · applied 0x · 2026-07-10 · agenda
> Text artifact verification should always use direct file Read, not just tool output confirmation — this catches content mismatches and guarantees the artifact contains what was intended.

**M118** — score 0.87 · novelty 0.89 · validated 0x · applied 0x · 2026-07-29 · agenda
> To confirm whether claimed system capabilities (e.g., named functions like fork/collate/checkpoint/escalation) are real versus doc-only aspirations, verify via exact source file:line citations (e.g., navigator.py:25/40/59/257) AND cross-check against an independent prior diagnostic transcript if one exists. Agreement between direct source inspection and an independent prior analysis is stronger evidence than either alone.

**M119** — score 0.85 · novelty 0.83 · validated 0x · applied 0x · 2026-07-29 · agenda
> When resuming work that references prior diagnostic sessions, do a full sweep from the source root to locate prior memos, session logs, and named evidence surfaces in one pass, then persist the raw output to an artifact file. This worked cleanly (exit 0, all targets found) and gave downstream steps a stable reference to grep/read against instead of re-querying the filesystem.

**M120** — score 0.68 · novelty 0.00 · validated 1x · applied 0x · 2026-07-21 · agenda
> [verification] For 'create file with exact content' goals, verify by reading the file back and doing an exact match against the required content (including checking for stray trailing newlines/whitespace), not just confirming the file exists — 'single word' specs silently fail on whitespace mismatches.

**M121** — score 0.68 · novelty 0.00 · validated 1x · applied 0x · 2026-07-03 · agenda
> [verification] Before declaring an agenda task 'done', confirm the actual step count matches the plan (e.g. 6/9 steps completed is not done) — check for and reconcile pre-existing output artifacts (like artifacts/metrics.md from a prior escalated run) rather than assuming the goal was met just because a file with the expected name exists.

**M122** — score 0.68 · novelty 0.00 · validated 1x · applied 0x · 2026-07-02 · agenda
> File-based scripts need end-to-end verification: create sample input data, run the script with argv handling, and capture actual output to a file. Bugs in argument parsing or file I/O only surface this way, not through unit tests.

**M123** — score 0.68 · novelty 0.00 · validated 1x · applied 0x · 2026-06-26 · agenda
> [specification-driven-verify] For agenda tasks with concrete output specs, validate systematically against each criterion ('15 lines', 'Fizz at 3-multiples', 'Buzz at 5-multiples') rather than holistic review. Catches mismatches reliably and reduces ambiguity about what 'done' means.

**M124** — score 0.68 · novelty 0.00 · validated 1x · applied 0x · 2026-07-03 · agenda
> [planning] For monitoring/metrics-fetch tasks that write to fixed artifact paths, check whether the target file already exists and inspect its content/freshness before overwriting or skipping — stale content from a prior run can silently satisfy a naive existence check and cause premature completion.

**M125** — score 0.68 · novelty 0.00 · validated 1x · applied 0x · 2026-07-02 · agenda
> Content creation tasks benefit from pre-defining structural constraints (file paths, index format, linking patterns) upfront—these act as guardrails that make both execution and verification deterministic and reduce rework.

**M126** — score 0.68 · novelty 0.00 · validated 1x · applied 0x · 2026-06-26 · agenda
> [verification] For script-generation tasks, always execute the script and capture stdout to provide exact output — mental simulation of numeric sequences (primes, fibonacci) is error-prone and slower than a single subprocess call.

**M127** — score 0.68 · novelty 0.00 · validated 1x · applied 0x · 2026-06-26 · agenda
> On agenda tasks, a quick 30-second discovery phase (Read to check file structure, grep to understand the script) prevents getting stuck. Stuck at 0/1 suggests I skipped discovery and hit an undiagnosed blocker on first action.

**M128** — score 0.68 · novelty 0.00 · validated 1x · applied 0x · 2026-07-02 · agenda
> When a script must handle sys.argv, test with the actual command-line invocation pattern (not just programmatic imports) to catch argument parsing bugs and ensure sys.argv[1] indexing is correct.

**M129** — score 0.68 · novelty 0.00 · validated 1x · applied 0x · 2026-07-02 · agenda
> For data processing scripts, design sample data to cover edge cases (numeric types, missing values, text in numeric columns) before writing the script. This catches assumptions early rather than during user testing.

**M130** — score 0.62 · novelty 0.00 · validated 1x · applied 0x · 2026-07-09 · agenda
> Agenda tasks with fully specified requirements (exact output format, location, content scope) benefit from direct execution rather than planning—the spec is already complete, and planning overhead adds no value.

**M131** — score 0.60 · novelty 0.00 · validated 2x · applied 0x · 2026-07-16 · agenda
> [verification-checklist] explicit-constraints: When a goal states precise, mechanically-checkable constraints (exact item count, format like 'one paragraph', negative constraints like 'do not modify files'), verify by enumerating each constraint and checking it directly against the final output/repo-diff, rather than generic re-review — this catches violations (wrong count, stray edits) that qualitative review misses.

---

# Jeremy's read — chunk 1: L1–L4 + M1–M15 (reacted 2026-08-01)

His framing first: *"Feels a little like I'm giving my reaction as much as
trying to find the surprises, hopefully it's helpful regardless"* — and the
process *"was pretty tedious… We might have to figure out a better way to
get this information."* (Answered below: the remainder is re-issued as
family judgments, not 116 more rows.)

Verbatim reads:

| Entry | Read | Direction |
|---|---|---|
| L4 | "surprised in a discouraging way; tighter step count and smaller code surface isn't going to improve the plan, just add constraints to make it 'cheaper'" | negative |
| M2 | "surprised at how prescriptive the methodology is" | ambivalent |
| M4 | "surprised with the adversarial/skeptic style tone in a slightly positive way" | **positive** |
| M6 | "surprised in a negative way; likely poe-prompted trickle down in this one (and for the record, consistently surprised that something that's 'easy' for end users are a consistent 'nope' from LLMs)" | negative |
| M7 | "surprised this isn't a split planner answer, rather than 'do it differently in 1-shot'. Seems like a clear sidequest-as-deliverable, so probably means we need work on identifying those." | system-gap |
| M8 | "surprised in a negative way; seems too narrow/brittle, will miss cases due to the prompting angle IMO" | negative |
| M9 | "surprised in an odd way — prompting up front to not make the same mistake again; that should be derived evidence maybe not prompted initially? I'm not sure where the better line is there, probably makes sense with our particular implementation, but feels off" | negative |
| M11 | "surprised we're not recommending a proper skill (or looking for one) here" | system-gap |
| M12 | "surprisingly good advice" | **positive** |
| M13 | "surprised; I'd say something like '2 trusted sources' rather than naming a specific failure path (seems to be describing how, not what)" | negative |
| M14 | "surprised that another part of the system is named to allow laziness shaped as freedom" | negative |

Unflagged (zero surprise): L1–L3, M1, M3, M5, M10, M15.

## What the tally says

11 of 19 entries flagged ⇒ **mirroring collapse is refuted** — the corpus
is not a restatement of Jeremy's priors. But the original rubric's binary
(zero/nonzero) missed that direction matters. The flags split three ways:

1. **Δ-gate seed set (positive surprise, the rubric's intended sense):**
   **M4, M12** — plus arguably M2 if prescriptive-but-sound counts. These
   are the entries a Δ-gate experiment must protect.
2. **Contradiction / retirement candidates (surprised-because-wrong):**
   **L4, M6, M8, M9, M13, M14.** The corpus diverges from his priors
   mostly by being wrong at the lesson-writing altitude, not by
   out-thinking him. Directly relevant to the mechanical-count note above
   that *nothing has ever been retired by contradiction* — this is an
   operator-certified test corpus for a retirement mechanism, six entries
   deep after one chunk.
3. **System-gap flags (the lesson is a symptom; the cure is elsewhere):**
   **M7** → side-quest-as-deliverable identification, already an open
   design space (`INTENT_RESOLUTION_DESIGN.md`, BACKLOG "naming the
   side-quests-before-decompose shape"). **M11** → the worker-reachable
   fetch verb from the 2026-07-27 artifacts-over-streams decree
   (GOAL_BRAIN). Both reads are fresh operator evidence those existing
   cuts are the right ones.

### M14 clarified (Jeremy follow-up, 2026-08-01)

The unhelpful-and-potentially-incorrect part is the second sentence: *"This
adversarial verification pass catches cases where a secondary summary …
generalizes beyond what the primary source's actual experimental scope
supports."* He's right, on two counts:

1. **The mechanism can't deliver the claimed benefit.** Grep catches
   *absence-class* errors — a cited stat or quote that literally isn't in
   the source. Over-generalization is a *scope-semantics* error: the
   summary reuses the source's own vocabulary while stretching its scope,
   so grep finds the words and stamps a false pass. In exactly the
   circumstance the sentence names, the "adversarial pass" label launders
   a non-check into rigor — worse than claiming nothing.
2. **The lesson is self-exemplifying.** Provenance check (lesson_id
   `655ea616`, live store): minted 2026-07-31 from the LeAct X-post
   evaluation run itself, `evidence_sources: []`, `sessions_validated: 0`,
   confidence 0.5 — a secondary summary generalizing a capability claim
   beyond what its own experimental scope (one run, no recorded catch)
   supports. It commits the failure it warns about.

The honest mint would have been: "grep the saved source to catch cited
claims whose support text doesn't exist; scope over-reach requires reading
the methods/limitations sections — grep will false-pass it." M14 stays in
the contradiction set with the sharper charge: claimed benefit
undeliverable by stated mechanism, and an unfalsifiable self-credit clause
(no named falsifier, no evidence source).

## Recurring altitude critique (4 of 11 reads)

L4, M9, M13, M14 are versions of one complaint: **the lesson is minted at
the wrong altitude — prescribing *how* (a named procedure, checkpoint,
path) where it should record *what* (the derived observation).** M13 is
the cleanest statement: "2 trusted sources," not
spec-page-plus-retailer-page. Same shape as the provenance/positive-
evidence line: lessons should be evidence the planner reasons from, not
pre-baked instructions the next run obeys. Candidate mint-time rule,
pending Jeremy's confirmation.

---

# Chunk 2, re-instrumented: 8 family judgments instead of 116 rows

The chunk-1 tedium was mostly corpus redundancy — the store re-mints the
same lesson once per run in a family (the tire runs and the blocked-X-link
runs each minted 4–5 near-identical entries). All 21 unread rows with
novelty > 0 cluster into 8 families. **Judge the family, not the members**;
call out a member only if it deviates from your family verdict.

**F1 — Output-budget scoping** (M16, M17 · kin of read M7/M10)
> Big memos exceed one response's output budget: scope as separate
> generation passes, spend depth on internal evidence over external
> claims, resume from the cut point without restating.

**F2 — Step-count ≠ goal-completion** (M18, M19 · tail kin M48/M64/M121)
> Step counters are unreliable or broken; verify the deliverable directly
> and tell the user which of the two (plan or tracker) broke.

**F3 — Per-field deliverable contracts** (M20, M22, M27, M28 · kin of read M13)
> "Purchase-ready" is judged per required field (model, load index,
> price, URL), each independently confirmable — one verification step per
> field, prior-failure reason becomes the acceptance criterion.

**F4 — Mirror the sibling artifact** (M21, M24)
> Series/house-style tasks: find the existing sibling artifact first and
> mirror its structure line-by-line rather than designing from scratch.

**F5 — Inherited-claim skepticism** (M25 · kin of read M4)
> In diagnosis chains, prior reports' claims are hypotheses to re-verify,
> not ground truth.

**F6 — Blocked-source waivers** (M23, M26, M29, M30, M31 · kin of read M6/M11/M15)
> The X/Twitter cluster: honor pre-authorization clauses ("source already
> recovered, don't block on direct access"), isolate the fragile fetch as
> an early step, and don't repeat social-thread benchmark claims
> unverified.

**F7 — Recovery template strings** (M32, M33, M34)
> Machine-minted restatements — same species as the L1–L4 tenure holders.

**F8 — Citation verification + evidence sweeps** (M118, M119 · kin of read M8)
> Verify file:line claims against the actual tree; do one
> sweep-and-persist evidence pass before analysis.

**The tail (M35–M117, M120–M131 — 95 rows):** all pre-chunk-6, novelty
0.00, generic verify-by-reading-back / minimal-planning /
checkpoint-resume boilerplate the mechanical count already tags as prior
restatement. Recommended protocol: **skip it**, or sample 5 rows blind to
confirm the tail is restatement; a surprise in the sample reopens the
tail, otherwise it's done.

So the remaining read is: 8 family verdicts + (optionally) 5 sampled tail
rows. Same reaction format as chunk 1.

