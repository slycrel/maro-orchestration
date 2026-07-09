# Purgatorio Eye 4 — Docs coherence

Probed 2026-07-09 ~16:30–17:15 MDT, read-only (no doc, source, or config file
modified; this findings file is the only write). Frame: **a stranger
pip-installs/clones Maro at 1.0 and reads the docs — do they end up with a true
picture, and do the docs agree with each other and with the code?** Every claim
below was probed against code in this session (file:line cited). Corroborations
of sibling eyes reference their ids; nothing from ops/data/archaeology is
re-reported as new.

## What matters most for 1.0

1. **The living security doc materially misrepresents the security posture**
   (docs-01): `docs/SECURITY_MODEL.md` (status: living) tells a stranger
   "Every skill runs sandboxed" and "Network access blocked unless explicitly
   opted in per-skill" — but `run_skill_sandboxed()` has **zero production
   callers** (only sandbox.py itself and its tests). The sandbox-hardens-a-stub
   fact is already known (PURGATORIO evidence list; eye 5 owns the code side),
   but the *living* doc that a security-conscious stranger reads first was
   never corrected. For a system that "executes LLM-directed shell commands
   unattended with money attached," this is the single worst doc-vs-code lie.
2. **The repo ships Jeremy's personal data and silently injects it into every
   stranger's planning prompts** (docs-02): `user/GOALS.md`, `user/CONTEXT.md`,
   `user/SIGNALS.md` are git-tracked and contain his identity, goals, and
   medical details (retatrutide/tirzepatide, nootropic stack), and
   `planner.py:362-368` auto-injects the first 500 chars of each into every
   decompose call from the install dir. A stranger's clean clone plans *their*
   goals with *Jeremy's* north star and health context in the prompt — and no
   stranger-facing doc (README says nothing about `user/`) tells them these
   files exist or should be replaced.
3. **The README's "Optional Services" instructions produce a crash-looping
   heartbeat on a clean install** (docs-03): `maro-bootstrap services`
   generates `maro-heartbeat.service` with
   `ExecStart=... sheriff.py --heartbeat`, but sheriff.py's argparse has no
   `--heartbeat` flag and *requires* a subcommand — the unit exits 2 and
   systemd retries every 30s forever. Nobody ever noticed because nobody
   (including this box) has ever run the generated unit — the docs face of
   ops-01.

## Findings

| id | claim | evidence | subsystem | severity | status | disposition-suggestion |
|---|---|---|---|---|---|---|
| docs-01 | SECURITY_MODEL.md (status: living) claims "Every skill runs sandboxed", "Every skill execution goes through `run_skill_sandboxed()`", and per-skill network isolation — false: `run_skill_sandboxed` has zero callers outside src/sandbox.py and its tests; the real enforcement layers on the live path (constraint.py pre-exec gates, write fence, injection_guard) are not what the doc describes. A stranger auditing security before install reads a posture that does not exist. | grep -rln run_skill_sandboxed → only src/sandbox.py, tests/test_sandbox*.py; docs/SECURITY_MODEL.md:20-22 ("Every skill runs sandboxed... Network access blocked"), :28-30 ("Every skill execution goes through run_skill_sandboxed()"); frontmatter status: living (line 2) | docs / quality-security | blocker-for-1.0 | confirmed | fixed-inline candidate: rewrite the Sandbox section to describe the *actual* enforcement stack (constraint gates + write fence + scavenge detect) and mark the sandbox module "built, unwired — see BACKLOG"; hand the wiring decision to eye 5 |
| docs-02 | Repo ships owner-personal data that is auto-injected into strangers' runs: user/GOALS.md, CONTEXT.md, SIGNALS.md are git-tracked (all 5 user/ files, last commit 61ef0a3) with Jeremy's identity, medical info (retatrutide, tirzepatide, nootropic stack incl. BPC-157/epitalon), Telegram bot handle; planner.py injects 500 chars of each into every decompose prompt resolved from the *install dir*, and evolver_scans.py feeds SIGNALS.md to signal scanning; README/quickstart never mention user/ or that these must be replaced | user/GOALS.md:6-9,18; user/CONTEXT.md:11-13; user/SIGNALS.md:8; src/planner.py:362-368 (`Path(__file__).parent.parent / "user"`, GOALS/CONTEXT/SIGNALS); src/evolver_scans.py:100,155-158; README.md — zero occurrences of "user/"; `git ls-files user/` = 5 files | docs / interface | blocker-for-1.0 | confirmed | backlog-item: replace user/ contents with neutral templates (ship Jeremy's real ones to ~/.maro/workspace/ or a private overlay), document the user/ mechanism in README; privacy review before any 1.0 tag |
| docs-03 | README "Optional Services" instructions install a service that cannot start: bootstrap's generated maro-heartbeat.service runs `sheriff.py --heartbeat`, but sheriff.py argparse defines only check/all/health subcommands (required=True) and no --heartbeat flag → exit 2, Restart=on-failure/RestartSec=30 = permanent 30s crash loop; the repo's own deploy/systemd/maro-heartbeat.service is a *different* (working) definition that hardcodes /home/clawd paths, so two contradictory unit definitions exist for the same service name | src/bootstrap.py:185-189 (`exec_cmd: sheriff.py --heartbeat`), :129-140 (ExecStart={exec_cmd}, Restart=on-failure); src/sheriff.py:467-484 (subparsers required, no --heartbeat); README.md:268-270 (`enable --now maro-heartbeat`); deploy/systemd/maro-heartbeat.service (ExecStart=heartbeat.py --loop, WorkingDirectory=/home/clawd/claude/maro-orchestration) | docs / platform | blocker-for-1.0 | confirmed | fixed-inline candidate: point bootstrap's exec at `heartbeat.py --loop` (or `cli heartbeat --loop`) and delete-or-genericize the repo unit; rides ops-01's "pick ONE supervision story" |
| docs-04 | A whole config lane is invisible to the config docs: repo-local user/CONFIG.md is parsed by handle.py (`yolo`, `default_model_tier`, `research_step_model`) and heartbeat.py (MCP servers), but DEFAULTS.md ("Every config key the code reads", census-enforced) documents none of these keys, its stated resolution order (env > workspace yml > user yml > hardcoded) omits the file entirely, and README's Configuration section never mentions it; the census structurally cannot catch this (it ASTs `config.get` aliases only — _load_user_config is a hand parser) | src/handle.py:465-480 (_load_user_config), :732-736 (default_model_tier), :993-997 + :1025 (yolo, and the user-facing hint "*Add `yolo: true` to user/CONFIG.md*"); src/heartbeat.py:963; docs/DEFAULTS.md:13-15 (resolution order), whole file (no yolo/default_model_tier rows); tests/test_defaults_doc.py:1-60 (census = config.get aliases only) | docs / platform | real-but-deferrable | confirmed | backlog-item: either fold user/CONFIG.md keys into the YAML config (one lane) or add a DEFAULTS.md section + census extension for the .md lane; today a stranger cannot discover yolo except from a runtime error string |
| docs-05 | CLAUDE.md's config table contradicts both README and code: it says user-level ~/.maro/config.yml holds "API keys, model prefs, yolo mode, notifications" — but README:338 says API keys "stay in the environment or secrets/.env, never here" (matching config.py credential discovery), and `yolo` is never read from YAML config at all (read from user/CONFIG.md + MARO_YOLO env; config.py's `get("yolo")` is only a docstring example) | CLAUDE.md Configuration table ("API keys, model prefs, yolo mode, notifications"); README.md:336-339; src/config.py:170-177 (docstring example only), :205-218 (credentials from env/.env); src/handle.py:993-997 (actual yolo read) | docs | real-but-deferrable | confirmed | fixed-inline candidate: correct the CLAUDE.md row (two words); part of the docs-04 lane cleanup |
| docs-06 | README presents self-improvement as ambient default behavior — "meta-evolver reviews failure patterns every 10 minutes" (What-it-does bullet) and "Every 10 heartbeat ticks (~10 min): evolver analyzes last 50 outcomes" (memory pipeline) — but the only vehicle is the opt-in heartbeat loop with `heartbeat.autonomy` default False, nothing schedules it on any install, and the evolver has zero production hours (ops-01/ops-02); a stranger's mental model of the product's headline capability is wrong on day one | README.md:26 and :403-406; src/heartbeat.py:888 (evolver_every=10), :956 (autonomy default False via config), :1053-1065 (evolver gated on autonomy tick); docs/DEFAULTS.md:45 (heartbeat.autonomy False); triangulates ops-01, ops-02, arch-05 | docs / quality-selfimprove | real-but-deferrable | confirmed | fixed-inline candidate: reword both README passages to "when the heartbeat loop is enabled (opt-in)..."; the deeper fix (evolver production hours) is ops-02's disposition |
| docs-07 | skills/arch-platform.md (the mandatory pre-read for platform work) documents the wrong backend failover order: "AnthropicSDK → OpenRouter → OpenAI → ClaudeSubprocess ... ClaudeSubprocess always available" — code and README both put subprocess *second* (DEFAULT_BACKEND_ORDER = anthropic, subprocess, openrouter, openai), which changes which backend a multi-key install actually uses | skills/arch-platform.md:17-22; src/llm.py:1667 (DEFAULT_BACKEND_ORDER); README.md:83-86 (correct order) | docs / platform | real-but-deferrable | confirmed | fixed-inline candidate (one block); check the other four arch skills for same-era drift while there |
| docs-08 | SECURITY_MODEL.md documents a secrets env var that no code reads: "`POE_ENV_FILE` env var for explicit override" — the code's override is MARO_ENV_FILE (POE_ENV_FILE has zero hits in src/); an operator following the security doc to relocate secrets gets a silently ignored setting | docs/SECURITY_MODEL.md Secrets Handling section ("POE_ENV_FILE"); grep -rn POE_ENV_FILE src/ = 0 hits; src/config.py:15,207 (MARO_ENV_FILE) | docs / platform | real-but-deferrable | confirmed | fixed-inline (one word) — fold into the docs-01 rewrite of SECURITY_MODEL |
| docs-09 | Pre-rename naming ghosts in the two most stranger-facing meta docs: CONTRIBUTING.md line 3 thanks contributors to "`openclaw-orchestration`"; SECURITY_MODEL.md line 7 describes "How poe-orchestration thinks" and proposes a "`poe-security audit` CLI" — the Maro rename decree (2026-06-25) says Maro is the framework name | CONTRIBUTING.md:3; docs/SECURITY_MODEL.md:7 + Future Work section; project_maro_rename decree | docs | cosmetic | confirmed | fixed-inline sweep (grep openclaw-orchestration/poe- over stranger-facing docs) |
| docs-10 | CLAUDE.md "Running things" still lists `maro-observe serve` as "Observe dashboard (Phase 36)" — archived 2026-07-02; the command now prints an archived-notice stub (graceful, but the top living doc advertises a dead surface that ARCHITECTURE_OVERVIEW.md correctly calls archived) | CLAUDE.md Running-things block ("maro-observe serve # Observe dashboard (Phase 36)"); src/observe.py:635 ("[ARCHIVED]"), :660-669 (stub message); docs/ARCHITECTURE_OVERVIEW.md:240-243 | docs | cosmetic | confirmed | fixed-inline (delete one line or point at `maro-observe watch`) |
| docs-11 | README's benchmarking examples read the git-tracked stale repo copy of telemetry: `maro-tool-costs --metrics memory/step-costs.jsonl` resolves to repo-local memory/ (tracked in git, frozen April, CLAUDE.md itself calls it "stale copies") instead of the live ~/.maro/workspace/memory/ — a fresh clone "works" and reports Jeremy's April costs as if they were the user's | README.md:170-178; `git ls-files memory/` includes memory/step-costs.jsonl; CLAUDE.md repo-layout row ("memory/ — Repo-local: stale copies"); triangulates data-10 (silent repo-local memory fallbacks) | docs / platform | cosmetic | confirmed | fixed-inline: point examples at `~/.maro/workspace/memory/step-costs.jsonl`; consider untracking repo memory/ fixtures (data-10's disposition covers the code side) |

## Clean checks (probed, no finding)

Counted because the README/DEFAULTS story mostly *does* hold — 24 checks clean:

1. All 5 quickstart commands exist as pyproject entry points with real
   callables: maro-bootstrap (bootstrap:main), maro-doctor (doctor:main:486),
   maro-handle (handle:main:2201), maro-run (agent_loop:main:538), maro-enqueue
   (handle:enqueue_main:2257).
2. README backend order matches code: DEFAULT_BACKEND_ORDER anthropic →
   subprocess → openrouter → openai (llm.py:1667); all 5 adapter classes exist.
3. Credential priority order (env → MARO_ENV_FILE/secrets/.env →
   openclaw.json) matches config.py:15-17, 207-218, 247-255.
4. Workspace resolution MARO_WORKSPACE → OPENCLAW_WORKSPACE → WORKSPACE_ROOT →
   ~/.maro/workspace matches config.py:35.
5. Budget defaults $5/run, $25/day match loop_init.py:30-31; fail-closed
   coercion and 0-opt-out as documented.
6. DEFAULTS.md spot-checks all match code: validate.write_fence True
   (loop_execute.py:724), memory.worker_slice True (director.py:421),
   navigator.act_dispatch True (navigator_shadow.py:276), heartbeat.autonomy
   False (heartbeat.py:956), evolver.auto_enqueue_signals False
   (evolver_store.py:286), budget.transparency_usd 2.0 (run_curation.py:201),
   file_lock.fail_open False (file_lock.py:90).
7. Telegram slash-command table matches _dispatch_slash (all 9 commands:
   telegram_listener.py:260-397; director/build/ops at :342).
8. Skill circuit breaker numbers match: 3 to open, 2 to close
   (skills.py:63-64); auto-promote 5 uses / 0.70 matches
   ARCHITECTURE_OVERVIEW (skills.py:57-58).
9. Python API example is accurate: run_agent_loop kwargs (project,
   step_callback) exist (agent_loop.py:116-136), step_callback called with
   4 args (loop_post_step.py:868), LoopResult.summary() exists
   (loop_types.py:167).
10. MARO_LOG_LEVEL env var and maro.* logger namespace are real
    (loop_types.py:28,39).
11. bootstrap subcommands install/dirs/services/status/smoke all exist
    (bootstrap.py:375-380); starter config is never-overwritten as claimed
    (bootstrap.py:106-117); generated service set (heartbeat/telegram/
    inspector) matches README's Optional Services names (modulo docs-03's
    broken exec).
12. inspector.py --loop and cli heartbeat --loop/--autonomy/--no-autonomy
    exist as README documents (inspector.py:915, cli_args.py:159-161).
13. Memory tiers short/medium/long exist as README states
    (knowledge_web.py:62-65; SHORT documented in-process-only).
14. Frontmatter contract holds: all 44 flat docs/*.md carry valid status
    frontmatter (living/dormant-design), enforced by
    tests/test_docs_frontmatter.py.
15. Every file INDEX.md points at exists (spot-swept: test_defaults_doc.py,
    scripts/host-check.sh, all 5 skills/arch-*.md, the 4 dated history briefs,
    ROADMAP_ARCHIVE.md).
16. VISION.md's Poe-voice is *properly fenced* with the 2026-06-25 naming note
    — dated, explains the rename mapping; not drift (unlike docs-09's ghosts).
17. HOST_MONITORING.md and SUBSTRATE_INTEGRATION.md (both living) spot-checked
    accurate: host-check.sh exists and bundles the four checks;
    maro-handle/maro-enqueue contract commands exist.
18. END_TO_END.md smoke/agent-loop commands reference real surfaces
    (scripts/smoke.sh, run_agent_loop dry_run).
19. Worker-session manifest aliases in README match END_TO_END's list (same
    source of truth).
20. maro_assets ships as the one real package with skills/personas package
    data (pyproject:73-76, 221-222; src/maro_assets/ populated).
21. `maro-observe serve` fails *gracefully* with a pointer to the archive —
    the code side of docs-10 is fine.
22. DEFAULTS.md census test exists and is meaningfully harder to fool than a
    fixed name list (AST alias resolution) — docs-04 is a lane it can't see,
    not a census bug.
23. write-fence description in README (project dir + workspace + /tmp +
    goal-named paths, demote done→blocked) matches DEFAULTS.md and
    loop_execute.py behavior flags.
24. README "No OpenClaw installation required" holds: openclaw.json is a
    third-priority optional fallback (config.py:247-255).

## Notes for reconciliation

- docs-01 + docs-08 + docs-09 are one SECURITY_MODEL.md rewrite; it is the
  highest-leverage single doc fix in this eye (living, security-scoped,
  stranger-facing, three confirmed falsehoods).
- docs-03 hardens ops-01: the heartbeat was not merely never scheduled — the
  shipped scheduler template for it cannot start. "Pick ONE supervision story"
  should include deleting one of the two contradictory unit definitions.
- docs-06 is the docs face of the ops-02/arch-05 dead-vehicle super-finding;
  reconcile as one item ("self-improvement claims vs zero production hours")
  spanning README wording, GOAL_BRAIN correction, and evolver burn-in.
- docs-02 and docs-04 both orbit the `user/` directory: it is simultaneously
  undocumented (config lane), over-shipped (personal data), and load-bearing
  (prompt injection + yolo). One "what is user/ at 1.0" decision resolves
  both.
