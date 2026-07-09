# Eye 5 — code-vs-spec + security threat model

Read-only audit. Every finding carries file:line evidence actually read.
Claimed-vs-probed discipline applied; low-confidence/absence-only hypotheses
were dropped rather than reported. Corroborations with sibling eyes noted by id.

Scope recap: Half A probed the 5 highest-stakes contracts (write fence, worker
push guard, secret scrubbing, done≠achieved verdict flow, config precedence).
Half B is a defensive threat model for a 1.0 that strangers install: injection
→ shell surfaces, subprocess with user/LLM-controlled strings, secrets in
artifacts, unsafe deserialization, path traversal in serving endpoints.

## Findings

| id | claim | evidence | subsystem | severity | status | disposition |
|---|---|---|---|---|---|---|
| cs-01 | The "write fence" is diagnostic-only and has a within-fence relative-path blind spot: `detect_out_of_fence_access` never changes step status (docstring: "Diagnostic only"), and its relative-write handler `_flag_rel_write` returns early when the cwd is still in-fence — so a worker whose cwd is `project_dir` that writes `../../../etc/x` (relative `..` traversal) is neither blocked nor flagged. cwd-binding is the only real bound and it does not stop `..` escape. Confirms the BACKLOG watch-item. | artifact_check.py:483-486 ("Diagnostic only: nothing here changes step status"); :657-663 `_flag_rel_write` returns when `_in_fence(at_cwd)`; :628-633 detector only considers ABSOLUTE paths for reads; llm.py:671-679 subprocess only sets `cwd=project_dir`, no chroot/`..` guard | core-loop / quality | real-but-deferrable | confirmed | backlog-item: either promote the scavenge report to an enforcing gate or realpath-contain worker writes; stop SECURITY_MODEL.md implying an enforced fence (see docs-01) |
| cs-02 | `secret_scrub` misses whole secret classes: pattern list covers `sk-ant-`, `sk-`, `gh[pousr]_`, `xox[baprs]-`, `AKIA…`, and label-prefixed `token/secret/…=value`, but NOT Telegram bot tokens (`\d{8,10}:[\w-]{35}`), generic high-entropy hex/base64 values, or JSON-serialized `"…token": "value"` (the char after "token" is `"`, so the labeled regex's `\s*[:=]` never matches). Any such value reaching a "scrubbed" sink passes through verbatim. | secret_scrub.py:13-20 (full pattern list); consumed by runs.record_llm_call (runs.py:414-431 → build/calls/*.json) and write_environment_snapshot (runs.py:474-509 → source/environment.json, which serializes MARO_*/OPENCLAW_* env values and full effective config) | platform / security | real-but-deferrable | confirmed | backlog-item: add Telegram-token + high-entropy fallback patterns; the two scrub sinks feed shareable artifacts (viz serves build/calls, reports embed scrubbed config) |
| cs-03 | Closure verification executes LLM-authored shell commands with `shell=True` and no allowlist, and one prompt input is untrusted worker output — an indirect prompt-injection → shell path. `work_summary` is built from step `result` text (worker output, which can contain fetched web content), fed into the plan-generation prompt that emits `command` strings, then each is run `shell=True` in the project cwd. `claim_probe` runs reviewer-authored `settled_by_command` the same way. | closure_verify.py:383-390 (work_summary from step results) → :443-459 (LLM generates `checks[].command`) → :476-479 `subprocess.run(cmd, shell=True, …, cwd=cwd)`; claim_probe.py:92-111 `subprocess.run(cmd, shell=True, …)` on LLM-authored `settled_by_command` | quality / self-improve | real-but-deferrable | confirmed | backlog-item: designed capability, but the threat model (Half B) must name it — worker output steering a closure check is realistic; timeout is present but no command restriction |
| cs-04 | The core work executor runs `claude -p --dangerously-skip-permissions` with full built-in tool access and NO sandbox on the live path; the `sandbox.py` hardening module (RLIMIT/network-block/venv) has zero runtime callers (only cli.py test-runner + its own tests). Strangers installing 1.0 get unattended full-permission shell driven by an LLM with money attached, while the *living* SECURITY_MODEL.md tells them "every skill runs sandboxed." | llm.py:1006-1017 (`--dangerously-skip-permissions`; only `--disallowedTools WebFetch,WebSearch` or `--tools ""` for no_tools); grep `run_skill_sandboxed` → only src/sandbox.py + tests; corroborates docs-01 and the ops-eye sandbox-stub note | quality / security | blocker-for-1.0 | confirmed | goal-brain-correction + backlog-item: (a) rewrite SECURITY_MODEL.md to the ACTUAL enforcement stack (cwd-bind + constraint pre-exec gates + injection_guard + scavenge diag), label sandbox "built, unwired"; (b) decide the 1.0 isolation story — the eye-5-owned wiring decision docs-01 handed up |
| cs-05 | Worker push guard is advisory, not a boundary: it depends on `MARO_WORKER_RUN` in the child env, which the worker's own shell can `unset` before pushing, and the git hook is repo-local so a worker operating inside a *different* project's git tree has no guard at all. Protects against accident, not a determined/injected worker. | llm.py:648-649 (`child_env["MARO_WORKER_RUN"]="1"`); .git/hooks/pre-push:10 `[ -z "${MARO_WORKER_RUN:-}" ] && exit 0` (identical to scripts/hooks/pre-push); bypass also via `MARO_ALLOW_MAIN_PUSH=1` (:11) | interface / platform | real-but-deferrable | confirmed | backlog-item: acknowledge as accident-guard in docs; a real block needs server-side branch protection (GitHub) |
| cs-06 | `router.py` does `pickle.load` of a workspace file — a code-exec gadget if that file is ever attacker-supplied. Currently NOT reachable via the import path (workspace_import's whitelist copies only `memory/**/*.jsonl` ledgers + `runs/` trees; `memory/router-model.pkl` is neither), so defensive/latent, not live. | router.py:335-336 `pickle.load(f)` from `_model_path()` (workspace memory/); workspace_import.py:8-16 whitelist (jsonl ledgers + runs copytree only) — .pkl not imported | platform | cosmetic | confirmed | backlog-item (low): if workspace-sharing (1.0 item g) ships bundle import, switch router persistence to JSON/joblib-safe or gate the load behind a trust check |
| cs-07 | Config precedence "env > config.yml > default" is NOT centralized: `config.get()` reads only the merged YAML tiers, so env-var override is per-call-site discipline (each setting re-implements it, e.g. handle.py yolo). Correct where done, but a latent inconsistency — a new setting that forgets the env check silently loses the documented precedence. Workspace>user nested-merge is correctly one-level as documented. | config.py:170-186 (`get` never reads os.environ); config.py:156-163 (workspace-over-user shallow+one-level-nested merge — matches CLAUDE.md); handle.py:994-996 (yolo does env-first manually); corroborates docs-05 | platform | cosmetic | confirmed | backlog-item (low): a `get_with_env(key, env_name, default)` helper would make the precedence contract enforceable instead of remembered |

## Clean checks (probed, no defect — recorded so they aren't re-litigated)

- **done≠achieved verdict flow — CONTRACT HOLDS.** `goal_achieved=True` is only
  ever written by (1) the NOW-lane text judge after `fulfilled is True`
  (handle.py:449-452), (2) closure verdict *and only when checks actually ran*
  — the fail-open null verdict (`complete=True, "Verification skipped.",
  checks_run=0`) is explicitly excluded (handle.py:1700-1720, gate
  `_closure.checks_run > 0`), or (3) the curation judge (run_curation.py:129).
  The worker's own `status="done"` never sets `goal_achieved`; closure
  contradicting a declared "done" demotes to incomplete (handle.py:1679-1694).
  The 2026-07-02 skipped-verification poisoning bug is guarded exactly here.
- **viz_server path traversal — SAFE.** Default-deny allowlist (only
  `index.html` + `<run-dir>/build/**`), rejects any `..` segment before touching
  disk, realpath-containment re-check, GET/HEAD only (POST/PUT/DELETE/PATCH →
  405), directory listing disabled, binds `127.0.0.1` by default
  (viz_server.py:37-106). `source/` and `artifact/` (unscrubbed raw git output)
  are unreachable by construction — matches its own guardrail docstring.
- **gateway.py — no server endpoint.** Outbound WebSocket *client* to the
  OpenClaw gateway; auth token read from openclaw.json, never logged
  (gateway.py:78-106, dataclass ":61 only on outbound, never logged"). No
  path-writing or request-handling surface to traverse.
- **YAML loads are safe.** Only `yaml.safe_load` in the codebase
  (config.py:115); zero `yaml.load(...)` without SafeLoader. `eval(`/`os.system`/
  `shell=True` appearing in constraint.py:207-210 and sandbox.py:45-67 are
  *detection blocklists*, not live calls. `os.system("clear")` (observe.py:656)
  is a constant, not user-controlled.
- **injection_guard source allowlist — SAFE against keyword-stuffing.**
  `_source_is_allowed` uses exact-match + path-*component* match, not substring,
  so `github.com/evil/workspace-tools` does not match the `workspace` entry
  (injection_guard.py:93-113). Applied to evolver suggestions
  (evolver_store.py:387-408) and synthesized skills (skill_lifecycle.py:463-478);
  worker/external content uses the separate `security.scan_external_content`
  redactor (loop_post_step.py:463-480).

## Notes / partial-probes (not counted as findings)

- injection_guard exfil pattern hardcodes an allowlist of only `r.jina.ai` and
  `api.anthropic.com` and only matches `.com/.io/.net` TLDs
  (injection_guard.py:60) — narrow, but it's a *risk annotator*, not a blocker.
- `security.scan_external_content` only runs when `"PRE-FETCHED"` or `"http"`
  appears in the step text and result >200 chars (loop_post_step.py:464-465),
  and only *redacts* HIGH risk into downstream context — external content
  reaching a step without those markers is unscanned. Heuristic gate; plausible
  but no confirmed bypass built in the time box, so not filed.

## Triangulation with sibling eyes

- cs-04 corroborates **docs-01** (SECURITY_MODEL.md misrepresents sandbox) and
  the ops-eye sandbox-stub observation — independently found → high confidence.
  docs-01 explicitly handed the wiring decision to eye 5; that is cs-04's
  disposition (a).
- cs-07 corroborates **docs-05** (yolo/config-precedence doc drift).
- cs-02's environment.json/config scrub sink overlaps the data eye's run-metadata
  census (findings-data.md) but the missing-pattern defect is new here.
