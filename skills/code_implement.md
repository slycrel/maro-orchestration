---
name: code_implement
description: "Implement a new feature or module: design, write, test, and verify in one loop"
roles_allowed: [worker]
triggers: [implement, write code, build, create module, add feature, develop, code up, program]
---

## Overview

Use this skill when a goal requires producing working code. Covers the full cycle from design to verification.

## Steps

1. **Read before writing** — read every file you'll touch before making any edits. Note existing patterns (naming, error handling, import style).
2. **Define the contract** — write the function/class signature and docstring before the body. Lock the interface first.
3. **Implement the happy path** — write the simplest version that passes the core case. No premature error handling.
4. **Add error handling** — only at system boundaries (user input, external APIs, file I/O). Trust internal invariants.
5. **Write tests** — at minimum: happy path, one edge case, one failure mode. Mirror the existing test file style.
6. **Run tests** — `python3 -m pytest tests/ -q -x`. Fix failures before continuing.
7. **Verify integration** — import the new module in the calling code and confirm it wires correctly.
8. **Check for regressions** — run the full test suite one final time.

## Anti-patterns to avoid

- Adding docstrings, comments, or type annotations to code you didn't change.
- Over-engineering for hypothetical future requirements.
- Broad `except Exception` blocks that swallow errors silently.
- Mocking internal code in tests (mock external APIs only).
