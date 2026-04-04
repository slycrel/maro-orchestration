---
name: debug_investigate
description: "Diagnose a failing system: reproduce, isolate, fix, and verify the root cause"
roles_allowed: [worker, short]
triggers: [debug, fix bug, diagnose, investigate failure, error, broken, not working, crash, exception, traceback]
---

## Overview

Use this skill when something is broken and the cause is unclear. Systematic narrowing beats guessing.

## Steps

1. **Reproduce the failure** — get a minimal, reliable reproduction before touching any code. If you can't reproduce it, you can't verify the fix.
2. **Read the full traceback** — the last frame is usually not the root cause; look for the first frame in your own code.
3. **Form a hypothesis** — write one sentence: "I think X is happening because Y." Make it falsifiable.
4. **Add targeted logging** — one or two `print()` or `log.debug()` statements at the suspected site. Not a logging framework — just enough to confirm the hypothesis.
5. **Test the hypothesis** — run the reproduction. Confirm or refute. If refuted, form a new hypothesis.
6. **Implement the minimal fix** — smallest possible change that eliminates the root cause. Do not refactor surrounding code.
7. **Remove debug logging** — clean up any temporary print/log statements.
8. **Verify** — run the full test suite; confirm the original reproduction now passes.

## Common traps

- Fixing the symptom instead of the cause.
- Adding `try/except` around the error site to suppress it — this hides the bug.
- Assuming the fix works without running a reproduction.
