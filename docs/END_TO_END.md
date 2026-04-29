# End-to-end verification

## Baseline verification

```bash
python3 -m pytest -q
bash scripts/smoke.sh
```

Expected:
- tests pass
- smoke prints `smoke=ok`

## Manual execution + review flow

```bash
python3 src/cli.py init demo "Ship demo flow" --priority 3
python3 src/cli.py tick \
  --project demo \
  --exec-cmd 'printf "%s" "$ORCH_PROJECT" > "$ORCH_RUN_ARTIFACT_DIR/project.txt"' \
  --require-artifact project.txt \
  --require-nonempty \
  --review-cmd 'grep -q demo "$ORCH_RUN_ARTIFACT_DIR/project.txt" && printf ok > "$ORCH_REVIEW_ARTIFACT_DIR/verdict.txt"'
```

Expected artifacts under `output/runs/<run_id>/`:
- `stdout.log`, `stderr.log`, `project.txt`
- `review/stdout.log`, `review/stderr.log`, `review/verdict.txt`
- `validation-summary.json`

## Agent loop smoke test

```bash
python3 -c "
import sys; sys.path.insert(0, 'src')
from agent_loop import run_agent_loop
r = run_agent_loop('test goal', dry_run=True)
print(r.summary())
"
```

## Worker session manifest flow

Worker-session manifests can now use either the explicit field names
(`environment`, `timeout_seconds`, `working_directory`) or the shorter aliases
(`env`, `timeout`, `cwd`).

Example manifest:

```json
{
  "cmd": "python3 worker.py",
  "payloadName": "in/session.json",
  "resultName": "out/result.json",
  "workingDirectory": "workers/demo",
  "environmentVariables": {
    "DEMO_MODE": "1"
  },
  "timeoutSeconds": 45
}
```

At runtime the worker receives:
- `ORCH_SESSION_PAYLOAD` / `ORCH_SESSION_PAYLOAD_PATH`
- `ORCH_SESSION_RESULT` / `ORCH_SESSION_RESULT_PATH`
- `ORCH_SESSION_WORKING_DIR`
- `ORCH_RUN_ARTIFACT_DIR`
- `ORCH_RUN_ARTIFACT_PATH`

## Inspecting evidence

```bash
python3 src/cli.py inspect-run <run_id>
python3 src/cli.py inspect-run <run_id> --format json
```
