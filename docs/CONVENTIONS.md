# Conventions (Poe Orchestration)

## Philosophy

- **One mission, one folder.**
- **One living checklist.** (`NEXT.md`)
- **Artifacts are truth.** If it didn’t get written down, it didn’t happen.
- **Interruptions are expensive.** Only ping the human when required.

## Canonical docs

### `NEXT.md`
The single living checklist for the project.

Rules:
- Top items should be concrete and independently shippable.
- Prefer small steps with clear outputs (files, reports, PRs).

### `RISKS.md`
Known risks, unknowns, assumptions, and things to watch.

### `DECISIONS.md`
Timestamped decisions.

### `PROVENANCE.md` (optional)
Pointers to source artifacts/data used for conclusions.

## Persona registry

Reusable focused personas live at `personas/`. List/show via `poe-persona list` / `poe-persona describe <name>`.

## Autonomy contract (default authority = C)
Poe may:
- edit files
- run scripts
- refactor workflows
- add/adjust OpenClaw cron jobs

Poe must ask before:
- spending money / placing real trades
- handling credentials/auth in a way that increases exposure
- destructive deletion of data
- posting externally (X/email/etc)
- major scope shifts ("this is a different project")

## Backward compatibility

- `NEXT.md`, `RISKS.md`, `DECISIONS.md`, `PROVENANCE.md` are stable artifacts.
- Existing checklist states `[ ] [~] [x] [!]` remain supported.
- CLI subcommands `init|next|done|log|blocked|report` are semver-governed.
- Breaking changes require a major version bump and migration notes.

## Migration from ad-hoc notes → canonical artifacts

1. Create project folder: `poe-project init <slug> "<mission>"`.
2. Move existing task bullets into `projects/<slug>/NEXT.md` using `- [ ]` syntax.
3. Add top risks in `RISKS.md`, evidence links in `PROVENANCE.md`.
4. Record first normalization decision in `DECISIONS.md`.
5. Set optional urgency in `PRIORITY` (integer, higher = sooner).
6. Use `poe-project next` and `done` as the default loop.
