---
name: monitor_diagnose
description: "Inspect system logs, classify errors, diagnose root cause with evidence, and propose remediation"
roles_allowed: [worker, short]
triggers: [monitor, health check, system status, log analysis, incident assessment]
---

## Overview

Use this skill when you need to assess the health of a systemd service or application, understand what went wrong from logs, and document the diagnosis with evidence. Given a systemd unit name or log file path, this skill walks through structured log inspection, error classification, root-cause diagnosis, and incident documentation.

## Steps

1. **Accept input** — confirm you have either a systemd unit name (e.g., `nginx.service`) or a log file path. If neither is clear, ask the user.
2. **Retrieve recent logs** — if systemd unit: `journalctl -u UNIT --no-pager -n 100 --all`. If log file: `tail -n 100 FILE` plus context from surrounding lines. Include timestamps and severity levels.
3. **Classify error types** — scan for recognizable patterns: connection errors, resource exhaustion, permission denials, version mismatches, timeouts, config parsing failures. Note which errors repeat and which are one-off.
4. **Identify the root cause** — do not assume the first error in the log is the cause. Trace error chains: Does an earlier event trigger the visible failure? Is the error a side effect of a resource limit? Propose the single most likely root cause with 2–3 evidence lines quoted directly from the logs.
5. **Propose a concrete fix** — suggest one specific action: config change, service restart, resource adjustment, dependency install, permission fix. Link the fix directly to the diagnosed cause.
6. **Draft an incident note** — write a brief incident report to `output/monitor_diagnose_TIMESTAMP.md` with: timestamp, target (unit/file), errors observed (2–3 quoted lines), root cause (one sentence with reasoning), fix applied or recommended, and confidence level (high/medium/low based on evidence strength).
7. **Verify the fix (if applicable)** — if you apply a fix, re-check logs to confirm the error no longer appears and the service is stable.

## Common traps

- Mistaking the symptom (a crash) for the cause (the resource exhaustion or config error that triggered it).
- Over-interpreting single error lines out of context; errors earlier in the log often explain later ones.
- Proposing a generic fix (restart the service) without diagnosing why it failed in the first place.
- Writing an incident note that does not quote evidence; a diagnosis without evidence lines is untestable.
- Assuming the latest log entry is the most recent event; check timestamps carefully, especially in high-volume logs.

<!-- built by Maro itself (dogfood run 6dfaec5d-keen-alder, 2026-07-09) as 1.0 item (f); reviewed and graduated by hand. Verified live against casper-md5check.service with a correct root-cause diagnosis. -->
