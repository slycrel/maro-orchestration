# End-to-end verification

## Baseline verification

Run in a clean environment:

```bash
cd prototypes/poe-orchestration
python3 -m pip install -U pytest
pytest
bash scripts/smoke.sh
```

Expected:
- tests pass
- smoke prints `smoke=ok`
- smoke also exercises:
  - `tick --exec-cmd`
  - required artifact validation
  - reviewer command validation
  - persisted `validation-summary.json`

## Manual execution + review flow

```bash
cd prototypes/poe-orchestration
python3 src/cli.py init demo "Ship demo flow" --priority 3
python3 src/cli.py tick \
  --project demo \
  --exec-cmd 'printf "%s" "$ORCH_PROJECT" > "$ORCH_RUN_ARTIFACT_DIR/project.txt"' \
  --require-artifact project.txt \
  --require-nonempty \
  --review-cmd 'grep -q demo "$ORCH_RUN_ARTIFACT_DIR/project.txt" && printf ok > "$ORCH_REVIEW_ARTIFACT_DIR/verdict.txt"'
```

Expected artifacts under `output/runs/<run_id>/`:
- `stdout.log`
- `stderr.log`
- `project.txt`
- `review/stdout.log`
- `review/stderr.log`
- `review/verdict.txt`
- `validation-summary.json`

## Inspecting evidence

Use the run id from `tick-start run_id=...`:

```bash
python3 src/cli.py inspect-run <run_id>
python3 src/cli.py inspect-run <run_id> --format json
```

Expected:
- text output includes `validation_status=done` for successful runs
- json output includes both the run record and the persisted validation summary
