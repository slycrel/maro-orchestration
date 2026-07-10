# Purgatorio r2 — Eye 5: code-vs-spec + security (delta re-audit)

**Date:** 2026-07-10. HEAD 97aa5ef, delta 4e6dc1b..HEAD (21 commits, 71
files). Re-verifies cs-01..07 from `docs/audit-2026-07/findings-code-security.md`
and threat-models the fix-wave surface.

*(Note: the eye agent was blocked from writing this file by a report-file
guard; table transcribed verbatim from its structured output by the
reconciling session — content identical.)*

## Half A — prior findings re-verified

| id | r1 severity | r2 status | evidence (probed) |
|---|---|---|---|
| cs-01 | blocker-member | **partially-resolved** | Enforcement shipped: loop_execute.py:721-724 demotes done→blocked on out-of-fence write when `validate.write_fence` (default True, DEFAULTS.md:65) — the "diagnostic-only" half is FALSE now. BUT artifact_check.py:659 `_flag_rel_write` still returns early when `_in_fence(at_cwd)`, so a relative `../../../etc/foo` write from an in-fence cwd still passes. The `..` blind spot matters *more* now that a gate exists around it. |
| cs-02 | real-but-deferrable | **still-open** | secret_scrub.py:13-20 pattern list byte-identical to r1. Telegram bot token (digits:base62), JSON `"token":"..."` (quote after label defeats `\s*[:=]`), and high-entropy values still pass verbatim on both scrub sinks. |
| cs-03 | real-but-deferrable | **still-open** | closure_verify.py:582 `subprocess.run(cmd, shell=True, cwd=cwd)` (moved from :476); work_summary still built from worker step summaries (:485) → check-gen prompt (:556/:655). claim_probe.py:109 same. No allowlist; timeout the only bound. Worker-output→shell indirect-injection path unchanged. |
| cs-04 | blocker-member | **partially-resolved** | Doc half RESOLVED: 1d3b77e's SECURITY_MODEL.md is honest — "no sandbox on the live executor path" (:14-53), sandbox.py labelled unwired prototype (:31-35), container direction recorded decided-not-built (:57-86). Code half UNCHANGED: llm.py:1007 still `--dangerously-skip-permissions`; grep docker over src/ = zero in llm.py. "Dockerized executor decided for 1.0" = decided, not shipped. |
| cs-05 | real-but-deferrable | **still-open** (now spec-honest) | pre-push hook byte-identical; `MARO_WORKER_RUN` skip + `MARO_ALLOW_MAIN_PUSH=1` bypass; no server-side protection. SECURITY_MODEL.md:44 now explicitly labels it advisory — spec matches reality. |
| cs-06 | latent | **still-open** | router.py:336 `pickle.load` unchanged; still on no import path (workspace_import.py:43 globs `*.jsonl` only). Resolve before item (g) bundle-import. |
| cs-07 | cosmetic | **still-open** | config.py:204-226 `get()` never reads os.environ; no helper added; handle.py:1009-1011 still hand-rolls yolo env-first. |

No regressions among cs-01..07. The security rewrite is a doc-honesty fix,
not a code-posture change — that is the correct reading of the batch-#3
decision.

## Half B — new findings (delta surface)

| id | severity | claim | evidence |
|---|---|---|---|
| cs-r2-01 | real-but-deferrable | The NEW skills-lite promoter (ccc20fc, default ON) is a live self-modification path that auto-injects worker-authored skill .md into every future run's planning prompt but SKIPS `injection_guard.scan_content` — the gate its two sibling self-mod lanes run-and-discard-on-finding. Its only content gate is `sandbox._DANGEROUS_PATTERNS`, a Python-code substring blocklist guarding the wrong threat: a skills-lite .md is LLM *instructions* injected into prompts, not executed code, so prompt-injection payloads pass. Candidates come from worker-written run/artifact + run/build + project artifact dirs (can carry indirect injection from fetched web content). Directly contradicts SECURITY_MODEL.md:47 ("self-modification path is stricter and runs injection_guard"). | run_curation.py:355-362 (only `_DANGEROUS_PATTERNS` scan; injection_guard grep = 0 hits) vs evolver_store.py:431-452 and skill_lifecycle.py:463-478 (both call `injection_guard.scan_content(source='internal')`, discard on findings). Wiring: CURATORS incl. promote_skills_lite (run_curation.py:490) → curate_run → handle.py:612-613 every finished run → config.skills_dir() overlay (:347,:376) → skills_context injection (loop_planning.py:334,383 → planner.py:357). Gate only success_class ∈ (success, done-unverified) (:311) + frontmatter shape (:338). `_slugify` kills path traversal — the gap is content-trust, not path. **Adversarially verified: confirmed.** |

## Clean checks (probed, not prose)

- **CI workflow** (2017d42/7581510/f215d17): push:[main] + plain
  `pull_request` (NOT pull_request_target); no `secrets:` referenced;
  conftest.py:53-77 strips API keys and blocks claude/codex CLI subprocess
  — fork-PR safe.
- **user-overlay readers** (7c1086c/bf144fc): `config.user_file(name)`
  called only with hardcoded literals at all 6 call sites — no traversal;
  overlay is operator-owned.
- **sheriff archive sweep** (a9824ce): `--apply`-gated (dry-run default),
  scoped to dormant dirs under projects_root(), move-not-delete, no shell.
- **resume/retention** (6c03068/dd5e930/97aa5ef): only surviving finalize
  delete is loop_finalize.py:69, gated behind `artifacts.auto_prune_days>0`
  (default 0 = never), scoped glob, other-loops-only.
- **verdict plumbing** (d6c143b): grep of + lines for
  subprocess|shell|Popen|os.system|eval|exec( = zero; no new
  `goal_achieved=True` writer bypasses the closure `checks_run>0` gate.
- **0.0.0.0 listeners** (ops-13): live ss shows :6080 (noVNC) and :8088
  (openclaw-webhost nginx) — neither is Maro; viz_server defaults
  127.0.0.1, gateway normalizes 0.0.0.0→127.0.0.1. Maro-side clean;
  box-level exposure is OpenClaw's, tracked in ops census.
- **containerized executor**: verified nothing landed in code — GOAL_BRAIN
  decision only (line 1435).
