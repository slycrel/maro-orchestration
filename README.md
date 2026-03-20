# openclaw-orchestration

File-first orchestration with a stable CLI, durable run artifacts, and operator-readable project state.

## What it does now

- project workspace bootstrap (`orch init`)
- priority-aware next-item selection across projects (`orch next`)
- explicit run lifecycle:
  - `orch start`
  - `orch finish`
  - `orch run`
- durable run artifacts under `output/runs/`
- operator status snapshot under `output/operator-status.json`
- auditable project journals:
  - `DECISIONS.md`
  - `RISKS.md`
  - `PROVENANCE.md`
- blocked-project triage and summary report generation
- parser hardening + unit tests for edge states/malformed lines/nested checklists
- smoke harness (`scripts/smoke.sh`)
- CI workflow scaffold

## Why this matters

This is no longer just a checklist helper. It now has the beginnings of a real orchestration control plane:
- claim work
- persist a run record
- transition task state through `todo -> doing -> done|blocked`
- leave an artifact trail the next session can inspect
- emit operator status for heartbeat / dashboards / external control loops

It is still a first pass. The actual autonomous planner/validator loop comes next.

## Quickstart

```bash
cd prototypes/poe-orchestration
python3 src/cli.py init demo "Ship demo flow" --priority 3
python3 src/cli.py run --project demo --worker handle --source manual
python3 src/cli.py finish <run_id> --status done --note "verified"
python3 src/cli.py tick --project demo --exec-cmd 'printf "%s\n" "$ORCH_ITEM_TEXT" > "$ORCH_RUN_ARTIFACT_DIR/item.txt"'
python3 src/cli.py status
python3 src/cli.py report --project demo
```

## Verify

```bash
cd prototypes/poe-orchestration
python3 -m pip install -U pytest
python3 -m pytest
bash scripts/smoke.sh
```

## Command summary

```bash
orch init <slug> <mission...> [--priority N]
orch next [--project <slug>]
orch start [--project <slug>] [--index N] [--worker NAME] [--source NAME] [--note TEXT]
orch finish <run_id> [--status done|blocked] [--note TEXT]
orch run [--project <slug>] [--worker NAME] [--source NAME] [--note TEXT] [--finish done|blocked] [--finish-note TEXT]
orch tick [--project <slug>] [--worker NAME] [--source NAME] [--note TEXT] [--exec-cmd 'shell command'] [--require-artifact PATH] [--require-nonempty]
orch loop [--project <slug>] [--worker NAME] [--source NAME] [--note TEXT] [--max-runs N] [--exec-cmd 'shell command'] [--require-artifact PATH] [--require-nonempty]
orch done <project> [--index N]
orch blocked
orch log <project> <message...>
orch status
orch report [--project <slug>] [--format md|json] [--out PATH]
```

## Current gaps

- no planner / decomposition engine yet
- validator can now enforce required run artifacts, but there is still no reviewer loop or semantic quality gate
- no OpenClaw queue adapter yet
- no real NOW vs AGENDA routing yet
- no remote/session worker backend yet; execution can now bridge to local shell commands, but not OpenClaw sessions or external agents

## Docs

- `VISION.md`
- `ROADMAP.md`
- `docs/poe_orchestration_spec.md`
- `docs/poe_intent.md`
- `docs/MIGRATION_GUIDE.md`
- `docs/QUEUE_ADAPTER.md`
- `docs/BACKWARD_COMPATIBILITY.md`
- `docs/SECURITY_MODEL.md`
- `docs/END_TO_END.md`

## Versioning

CLI and artifact contract follow semantic versioning policy in `docs/BACKWARD_COMPATIBILITY.md`.
