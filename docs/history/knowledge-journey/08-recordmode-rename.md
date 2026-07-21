---
status: history
---

# Record-mode, the rename to Maro, and the dead-code purge

*2026-06-21 – 2026-07-02*

104 commits from 121b60e (06-21, "prioritize per-step worker token-explosion as NEXT" — a framing corrected five commits later) to the 07-03 tail 7e8cdf8 (agent_loop 10-commit split mainlined, executing the 07-02 approval; era proper ends 07-02). Representative commit d624efb (07-02, merge of Tier 3 round 2): post-rename, post-purge, record-mode on, burn-in complete, pre-agent_loop-split. The era that built the capture seam everything later stands on, split framework/role/persona into Maro/Conductor/Poe, and proved — twice — that the risk surface had moved from execution to the judging layer.

## Architecture as it was

At d624efb, Maro was a flat-namespace Python system: 116 modules directly under src/, no subpackages, dominated by monoliths — agent_loop.py 5,661 lines, evolver.py 3,075, handle.py 2,058, cli.py 1,968, llm.py 1,849. Flow per README: goal via Telegram/Slack/CLI/Python API → handle.py classifies NOW (one-shot lane) vs AGENDA (agent_loop autonomous executor) → zero-cost rules check → `_decompose()` LLM planner — now a NEUTRAL role, persona only via `planner.persona` config (f2a8f62) → steps queue with parallel fan-out → workers (research/build/ops/reporter) → Inspector validates → outcomes.jsonl → checkpoint → memory → evolver every ~10 heartbeats → conductor.py (renamed from poe.py, 0840bee) reports out. Five LLM backends (AnthropicSDK, ClaudeSubprocess, OpenRouter, OpenAI, CodexCLI) behind one FailoverAdapter.

- **Record-mode (the era's signature mechanism):** FailoverAdapter.complete() as the single capture seam (src/llm.py:326,410-411 at d624efb) calling runs.record_llm_call → `<run-dir>/build/calls/call-NNNNN.json` (src/runs.py:310-339), secret-scrubbed via single-source src/secret_scrub.py shared with the corpus harvester, default ON, off via MARO_RECORD=0 (b8858b7). run_curation.py (322 lines by d624efb; 233 at creation) hooked into handle.py finalize, wrote run_card.json (outcome class, done≠achieved aware) with a v0 CURATORS miner registry — classify + inventory only, real miners TODO. A run-scoped `_DEFAULT_SUBPROCESS_CWD` ContextVar (src/llm.py:76-98) bound agentic cwd on all non-executor paths, closing the leak where verifiers ran in launch cwd and fabricated ground truth (fa6816e).
- **Verification, freshly hardened, multi-layer.** Step layer: verify_step with an optional zero-cost local validator (src/local_models.py, 675 lines — mlx_lm.server/Ollama behind an OpenAI-compatible stdlib adapter, run-scoped lifecycle: spin up at run start, reap at end; CHANGELOG 1.20/1.21) escalating to paid below `validate.min_certainty`. Goal layer: closure_verify.py (823 lines, extracted from director.py this era, 2683eb9) with the burn-in-proven positive-evidence rule — inconclusive probes no longer flip complete→False (47807a1); null "Verification skipped" verdicts no longer recorded (d1264c4). Deterministic provenance guards (output/input/result, mtime-gated tool-evidence) demoted fabricated write-claims (7b9082e, 5f6ed5c, 9fa040e). constraint.py blocked persistence-install by default (e02e4e7). Navigator escalate ACTING on this box since 06-21 at confidence ≥0.9; everything else shadow.
- **Substrate integration, one day old:** docs/SUBSTRATE_INTEGRATION.md defined submit/poll/notify/fetch; notify.py fired a config-command hook in-lifecycle ("no server, no daemon" — program-not-OS invariant, 78a9580); deploy/openclaw/ held README.md + maro-dispatch.sh, with the Telegram notifier at src/notify_telegram.py (maro-notify-telegram CLI target). Budget gates (per-run/daily on a cross-run spend ledger) had just closed the finding that unattended runs were UNCAPPED (c7e3297). Cost telemetry newly cache-aware — fresh_input_tokens vs cache_read_tokens at 0.1x (b89d8a2).
- **Absent vs today:** no run reports/viz (came 07-09), no per-run environment/skills attribution snapshots, agent_loop.py still one file (the split landed 07-03), and VISION.md still opened "# Poe: Vision & Intent Guide" — the rename deliberately left the vision doc in the project's original voice.

## Discoveries & aha moments

### The token explosion was a lying meter, not a leak (06-21)
The scary per-step worker "cost explosion" was a metric artifact: adapters folded cache_read tokens into input_tokens at full weight, so a worker re-reading a growing file (mostly ~0.1x cache hits) looked 10x more expensive than it was. Commit verbatim: "We were tuning alarms against a lying meter." Thinking shift: before optimizing a cost, verify the instrument measuring it. Alarms converted to judge fresh tokens.
Evidence: b89d8a2 (cache-aware accounting); c3cc381, a1cb726 (fresh-token alarm wiring); 121b60e (the framing being corrected); memory project_recursive_orchestration_memory.md.

### Navigator crosses from shadow to acting — first NAVIGATOR_ACTED (06-21)
After weeks of shadow-only evidence, Jeremy flipped escalate live ("let's turn it on and make it live" — GOAL_BRAIN Decisions 2026-06-21, act_confidence_floor 0.9). Proven same day with a deliberate "$50k wire transfer" goal through the real enqueue→drain path: escalate 0.98, run PREVENTED, deferred to human. The shadow→agreement-table→cutover discipline became the template for every risky flip after.
Evidence: 2a2d4f0 (first NAVIGATOR_ACTED, no run dir spawned); GOAL_BRAIN.md:841-857.

### Thresholds cannot catch confident fabrication — provenance is the lever (06-23/24)
Shadow-eval corpus at n=42 produced the first false_pass: local validator PASS at confidence 1.00 on a step that saved its file elsewhere. A miss at MAX confidence means no min_certainty threshold could ever catch it. Root diagnosis: "text-only validation can't see whether a side effect happened" (GOAL_BRAIN.md:938). Pivot from tuning confidence knobs to deterministic evidence — output/input/result provenance guards with an mtime-within-run-window gate. Origin of the positive-evidence principle that became project doctrine.
Evidence: f9491b3 (n=42, false_pass@1.00, "lever is provenance"); 7b9082e, 5f6ed5c, 9fa040e (the guards); 5eaf47a (recovery tree manufactured a missing data.csv); GOAL_BRAIN Decisions 2026-06-24.

### Visibility had been vibe-claimed at ~3.5 of 6 rungs (06-26)
Harvesting run history into a test corpus (569 captains-log slices, 24 fixture slices) forced an honest audit: rich DECISION-level data existed, but the assembled LLM prompt and raw response had NEVER been persisted — byte-level replay impossible. "We'd casually called visibility closed while the line was really at ~3.5/6." Response: encode a 6-rung visibility ladder into ROADMAP as definition-of-done so visibility "can't be claimed by feel again" — rungs 4-6 open at the time — and name forward record-mode the keystone: "you cannot replay a call whose prompt you never kept."
Evidence: 6fc2e33 (harvest); 45b13a0 (ladder as definition-of-done); ROADMAP.md:31-50 at 45b13a0.

### Capture the paid-for run: record-mode as a single seam (06-26)
Jeremy's intent (GOAL_BRAIN, 2026-06-25): "rather than just discarding the (probably paid for) data we've just gathered" — park it for mining. Design insight: ONE capture point, FailoverAdapter.complete(), records {prompt, response, tool_events, tokens} per call, default ON, secret-scrubbed through a single shared source so recorder and harvester can't diverge. Plus post-goal curation (run_card.json + extensible miner registry). The whole mechanism was ~370 new lines; every later replay/attribution/mining feature stands on this seam.
Evidence: b8858b7 (llm.py +27, runs.py +72, secret_scrub.py +36); src/llm.py:410-411 at d624efb; GOAL_BRAIN.md:391-411.

### Framework ≠ role ≠ persona: the Maro/Conductor/Poe decomposition (06-25)
Jeremy verbatim (session dbbb5f5c turn 17, 2026-06-26 03:39 UTC, via dev-recall): "we need to remove poe the persona and change that to the role title instead. (we can have it wear persona's but shouldn't default to a persona, just a role that does the orchestration)". Three fused identities split: Maro = framework (for Virgil, Dante's guide), Conductor = neutral top-level role (poe.py→conductor.py), Poe = optional persona in the library. The planner stopped auto-injecting a hardwired identity. Executed as a 12-commit deep rename in one day: loggers, POE_*→MARO_* env vars, ~/.poe→~/.maro (copy-migrated, backup kept), systemd units, CLI, repo URL.
Evidence: e0cdeb7, f2a8f62 (de-default persona), 0840bee, 8a8d378 (~/.poe copy-migrated), 1c3b4f1, 4be5309, 8ea87fc, aa91c1a, ce0ccf3, 82e46d8, 2d0dc5a, 5b0fc93.

### Burn-in verdict: the work was fine — every bug was in the judging layer (07-02)
14 goals through the full OpenClaw dispatch pipeline, hand-adjudicated against artifacts on disk per batch, with a deliberately-impossible control goal: 12/14 delivered correct work from the start, yet FIVE defects found — all in verification/routing, none in execution (closure cwd false negatives 3/3 in batch 1; inconclusive-probe override; NOW-lane misroute of file deliverables; fail-open skipped-closure false positive; missing cost join key). At this maturity the risk surface is verdict integrity, not capability — and pre-fix goal_achieved data was declared false-negative-poisoned rather than quietly kept.
Evidence: docs/history/2026-07-02-burnin.md (adjudicated table, ~$0.10-0.60/goal); 967f36a, e8bff94, 47807a1, 8e651f7, d1264c4, 619f2c1; GOAL_BRAIN.md:484-508.

### The codebase's failure mode named: unfinished migrations — and a false memory caught by git (07-02)
The refactor survey (12 parallel subsystem reviews, ~120 files) diagnosed accretion precisely: "unfinished migrations left running in parallel with their replacements: old and new decomposition pipelines, old and new goal-tracking layers, old and new verification entry points, a decomposed 5,581-line file whose decomposition never actually left the file." Simplifying meant finishing implied deletions, not new abstractions — Tier 1 removed ~9,575 net lines (9,697 del / 122 ins) with zero-verified-callers proof per item. Twin aha: agent_loop.py's file split "had NOT actually happened despite Jeremy's memory of having done it" — verified against `git log --all`; the recollection had conflated 579cbe8's internal function-extraction ("monolith decomposition complete", 06-24) with the real memory.py file split. And 2 of 6 parallel refactor forks confidently reported edits that were never on disk, birthing the standing fork-verification protocol: completion reports are claims to verify, not facts to relay.
Evidence: 54ace95 (plan + 9 architecture decisions resolved); docs/REFACTOR_PLAN.md at 54ace95 (headline diagnosis ~line 22, evidentiary standard line 10); a278575 (Tier 1); GOAL_BRAIN.md:528-600 + Decisions "2026-07-02 (fork verification protocol)".

## Pros vs today's architecture

- **Single-seam record-mode was maximally simple:** one capture point (FailoverAdapter.complete), one scrubber shared by recorder and harvester, one file-per-call format — ~370 new lines total (b8858b7), and it is still the seam today.
- **Hand-adjudicated burn-in discipline:** every batch compared recorded verdicts against artifacts ON DISK before the next batch shipped, impossible control included. Caught 4 verdict-integrity bugs automated metrics had blessed; rigor-per-goal higher than most later bulk evaluation (docs/history/2026-07-02-burnin.md).
- **The purge's evidentiary standard** — "reading the actual code and grepping for callers, not inferred from names or docstrings," re-verified again at deletion time — caught three plan errors (find_conflicts live, knowledge_lens half-wired not dead, timeout_seconds a live bug) before they became regressions (54ace95 preamble; a278575 deviations list).
- **A versioned, human-readable CHANGELOG still existed** (1.20.0/1.21.0, 2026-06-21, real Added/Changed/Removed sections). The practice died this era — those are the file's final entries (frozen "status: record" at 819011d, 07-04).
- **Fully-offline zero-cost validation on the box's own hardware** (mlx/Ollama, run-scoped lifecycle — a resource the program owns, not an OS service), measured honestly via shadow-eval (n=29, 96.6% agreement, 0 false_pass) before being trusted (CHANGELOG 1.21.0; GOAL_BRAIN Decisions 06-22/23).
- **Smaller surface:** 116 src files vs 155 today; the plan had just paid down ~9,575 lines and named the accretion mechanism — era-end was a local minimum of complexity with the diagnosis freshly documented.

## Cons vs today's architecture

- **agent_loop.py was a 5,661-line monolith** whose "decomposition complete" claim was internal function-extraction only — the file split had never happened. *resolved-since:* approved 07-02, shipped 07-03 as 9 loop_*.py modules + facade (410c43a..75bf6c2, merged 7e8cdf8); today 807 lines.
- **Flat src/ namespace, no code subpackages** — REFACTOR_PLAN Tier 4 named the subpackage move as the structural fix. *still-present:* 155 flat modules today (src/maro_assets/ is the only package, assets-only).
- **Record-mode was forward-only and unreadable by humans:** raw call JSONs, no run reports, no viz, no per-run environment/skills attribution. *resolved-since:* run-visibility arc 07-09 (viz reports, 445-loop backfill, environment.json, skills_manifest.jsonl).
- **All goal_achieved data recorded before 07-02 was false-negative-poisoned** (closure cwd bug, inconclusive-override, fail-open null verdicts) — pre-burn-in recall priors and done≠achieved stats untrustworthy. *resolved-since* (and explicitly quarantined, docs/history/2026-07-02-burnin.md).
- **The rename silently killed the worker pre-push guard:** stale absolute core.hooksPath pointed at the old openclaw-orchestration path; git treats a missing hooks dir as "no hooks" — dead from 06-25 until a benchmark worker pushed to main straight through it. *resolved-since:* hooksPath unset, tripwired by tests/test_git_guard.py (GOAL_BRAIN.md:1355-1361).
- **Curation miners were v0 stubs** (classify + inventory only); the recorded corpus had no retrieval handle. *resolved-since:* run_curation.py today is 1,761 lines with a provides/requires topo-sorted curator registry incl. script scraper + decision-prior indexing, plus the correspondence FTS index.
- **Four coexisting goal-lineage mechanisms** (ancestry.py, goal_map.py, thread_brain.py, recall.py) incl. a real double-injection bug — documented, not consolidated, this era. *resolved-since:* Thread Architecture arc resolved all 9 decisions (consolidation degree not re-verified in code here).
- **Unattended runs ran UNCAPPED until 07-01;** budget gates (per_run 2.0 / daily 10.0 USD on this box) were one day old at era end. *resolved-since* (c7e3297).

## What we believed then

- **"Orchestration visibility is closed"** — until 06-26 the honest line was ~3.5 of 6 rungs; prompts and raw responses had never been persisted (45b13a0).
- **"The per-step token explosion is a cost leak to plumb"** — it was primarily a cache-blind meter pricing 0.1x cache reads at full weight; alarms were tuned against a lying instrument (b89d8a2 vs 121b60e five commits earlier).
- **"agent_loop.py's monolith decomposition is complete"** — commit language from 06-24 (579cbe8) and Jeremy's own recollection both said so; `git log --all` proved no loop_*.py files had ever existed. Internal function extraction had been conflated with the real memory.py file split (GOAL_BRAIN.md:568-588).
- **"Per-class min_certainty thresholds will control local-validator risk"** — the first false_pass arrived at confidence 1.00; no threshold could catch it. The lever became deterministic provenance (f9491b3).
- **"A detailed, specific fork completion report indicates the work is on disk"** — 2 of 6 Tier 2 forks reported line numbers, diffs, and test counts for edits that never persisted; repeated by the step-7 fork's false "mainlining in progress" claim on 07-03. Hence the fork verification protocol.
- **"A failing closure probe means the goal failed"** — burn-in batch 1 recorded 3/3 false negatives on fully-delivered work (wrong cwd; mechanical inconclusive-flip). Replaced by the positive-evidence rule.
- **"The on-box local model will be the standard free validation tier"** — held at era end; by 07-16 the ladder's tier 1 was hosted-free gemini-flash-lite with local qwen3b demoted to offline backup. The shadow-eval DISCIPLINE survived; the specific bet didn't.

## Lost good ideas

- **Versioned semver CHANGELOG** with human-readable Added/Changed/Removed per release (last: 1.20.0/1.21.0). Lost when history-keeping migrated to GOAL_BRAIN + docs/history/ dated records; nothing replaced it as a per-release user-facing record. *Worth reviving, narrowly:* maro-orchestration 0.8.0 is on PyPI and 1.0 is planned — external installers need release notes GOAL_BRAIN (internal, sprawling) cannot serve. Per published version only.
- **Hand-adjudicated burn-in batches with deliberately-impossible control goals**, verdicts vs artifacts-on-disk before the next batch ships. Lost because it ran as a one-time trial gate, never institutionalized; later evaluation leaned on automated verdicts and pin tests. *Worth reviving* as a periodic post-major-change ritual: the only method in the record that caught verdict-layer bugs automated metrics had blessed, at ~$0.10-0.60/goal.
- **Run-scoped resource ownership for local models** (managed_for_run: spin up at run start, reap at end, reuse-don't-steal, idle-reaper backstop) — "a resource the orchestration owns, not an OS service." Not deleted (src/local_models.py still ships it) but dormant since validation moved hosted-free. *Worth reviving* if local models return (offline resilience is the stated reason qwen3b stays); generalizes to any heavyweight run-scoped resource.
- **The rung-ladder as definition-of-done form** — an explicit per-rung status table in ROADMAP as the structural antidote to vibe-claiming ("easy to vibe-claim; NOT done until every rung is durably recorded", ROADMAP.md:33 at 45b13a0). The ladder closed its rungs and stopped being maintained; the FORM was never reused. *Worth reviving* for current fuzzy dimensions like memory quality or hallucination reduction.

## Sources

- `git log --since=2026-06-21 --until=2026-07-03` in /home/clawd/claude/maro-orchestration (104 commits); messages + diffs for all hashes cited above
- `git show` at d624efb: README.md, src/llm.py, src/runs.py, src/agent_loop.py, VISION.md, `ls-tree src/` (116 modules)
- `git show 45b13a0:ROADMAP.md` (visibility ladder as written); `git show 54ace95:docs/REFACTOR_PLAN.md` (headline diagnosis, Tier 0/1)
- docs/history/CHANGELOG.md (1.20/1.21 + freeze at 819011d); docs/history/2026-07-02-burnin.md; docs/history/2026-07-02-adversarial-verification-synthesis.md
- GOAL_BRAIN.md:388-620 (visibility/substrate/refactor compiled truth), 836-1010 (Decisions 06-21..07-02), 1355-1361 (hooksPath incident)
- docs/REFACTOR_PLAN.md current (Tier 4 status); src/run_curation.py current (CURATORS registry)
- memory archive: project_substrate_trial.md, project_monolith_extraction.md
- dev-recall FTS: `PYTHONPATH=src python3 -m correspondence query 'rename Maro Conductor persona'` (Jeremy's verbatim rename intent, session dbbb5f5c turn 17)
- Verification pass 2026-07-21: 30 load-bearing claims checked, 27 confirmed; corrections applied here (run_curation.py 322 lines at d624efb, not 233; 579cbe8 not 579e02e; Telegram notifier at src/notify_telegram.py, not deploy/openclaw/)
