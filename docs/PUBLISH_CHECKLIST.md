---
status: living
---

# Publish Checklist (Go / No-Go)

Adopted 2026-07-09 (Jeremy, post-Purgatorio decision batch) as the 1.0 gate
scaffold; version is **1.0.0 at tag time** (internal CHANGELOG 1.x numbering
retired). The real gate is direct-use readiness — "start trying to use the
orchestration directly via openclaw or hermes instead of dev style".

## Scope and messaging
- [ ] README clearly states prototype status and scope; feature claims match implementation.

## Security and privacy review
- [ ] No secrets, tokens, credentials, or private hostnames/paths are committed.
- [ ] Examples use generic placeholders (no personal account data).
- [ ] Git-history personal-data review done (Jeremy's own pass — deferred to a
  dedicated conversation, GOAL_BRAIN Decisions 2026-07-09).

## Functional sanity checks
- [ ] `pytest` passes.
- [ ] `scripts/smoke.sh` passes.
- [ ] Core module (`src/orch.py`) imports without error.
- [ ] `maro-bootstrap install` runs on a clean workspace.
- [ ] Wheel installs on a clean machine (docker trial pattern,
  `tests/test_packaging.py` census green).

## PyPI (Purgatorio blocker #7)
- [x] Name availability checked 2026-07-10: **`maro-orchestration` (the name
  already in pyproject.toml) is FREE on PyPI.** Bare `maro` is taken (an
  unrelated stub "package for exercises", v0.0.1) and `pymaro` is Microsoft's
  MARO (Multi-Agent Resource Optimization) — adjacent-name confusion worth a
  README disambiguation line, but no rename needed. Publish under
  `maro-orchestration` as-is.
- [ ] `python -m build` produces wheel + sdist; `twine check` passes.
- [ ] Publish at tag time (Jeremy's act).

## Release gate
- [ ] CHANGELOG updated and version set to 1.0.0; tag applied.
- [ ] Rollback instruction present (git revert + `maro-bootstrap install`).

**GO** if all critical boxes (security + functional + release gate) are checked.
