# Publish Checklist (Go / No-Go)

## Scope and messaging
- [ ] README clearly states prototype status and scope; feature claims match implementation.

## Security and privacy review
- [ ] No secrets, tokens, credentials, or private hostnames/paths are committed.
- [ ] Examples use generic placeholders (no personal account data).

## Functional sanity checks
- [ ] `pytest` passes.
- [ ] `scripts/smoke.sh` passes.
- [ ] Core module (`src/orch.py`) imports without error.
- [ ] `poe-bootstrap install` runs on a clean workspace.

## Release gate
- [ ] CHANGELOG updated and version tag applied.
- [ ] Rollback instruction present (git revert + `poe-bootstrap install`).

**GO** if all critical boxes (security + functional + release gate) are checked.
