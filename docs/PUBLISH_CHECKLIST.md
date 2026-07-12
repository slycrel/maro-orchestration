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
- [x] `python -m build` produces wheel + sdist; `twine check --strict` passes
  (2026-07-12, both artifacts; added `readme`/license/urls/classifiers
  metadata — the page was previously blank). Version bumped 0.5.0 → 0.8.0.
- [x] Auth mechanism decided: **trusted publishing (OIDC), no API token.**
  Pending publisher registered by Jeremy (repo `slycrel/maro-orchestration`,
  workflow `pyPI-workflow.yml`, environment `pypi`); matching workflow
  shipped `6befbfb`. Manual `workflow_dispatch`, `dry_run` defaults true,
  publish job gated on explicit `dry_run=false` — nothing auto-publishes.
- [x] **Reserve-the-name publish at 0.8.0 — DONE 2026-07-12.** Published via
  the "Publish to PyPI" workflow (run #1, commit `e4e7467`, build+publish both
  green, 41s). OIDC trusted publishing worked first try — no token. **Live at
  https://pypi.org/project/maro-orchestration/ (0.8.0, wheel + sdist).** The
  pending publisher is now a permanent trusted publisher.
- [ ] 1.0.0 tag + publish at real readiness (post git-history review + the
  remaining -3 arc). Two-step by decree: 0.8.0 reserves the name now, 1.0.0
  is the real release.

## Release gate
- [ ] CHANGELOG updated and version set to 1.0.0; tag applied.
- [ ] Rollback instruction present (git revert + `maro-bootstrap install`).

**GO** if all critical boxes (security + functional + release gate) are checked.
