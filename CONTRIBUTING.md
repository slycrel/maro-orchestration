# Contributing

Thanks for helping improve `openclaw-orchestration`.

## Ground rules

- Keep changes small and reviewable.
- Prefer explicit behavior over clever behavior.
- Do not introduce private paths, credentials, or secrets.
- Preserve file-first portability.

## Local setup

```bash
# Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install the package in editable mode with dev dependencies
pip install -e ".[dev]"

# Make scripts executable
chmod +x scripts/*.sh
```

`[dev]` pulls in `pytest` and `requests`.  For the full optional stack (Slack, YAML persona files, websocket gateway):

```bash
pip install -e ".[all]"
```

## Running tests

```bash
python3 -m pytest
```

All tests are self-contained and mock LLM calls — no API keys needed.  1460+ tests, ~80 seconds.

## Suggested pre-PR checks

```bash
python3 -m pytest
bash -n scripts/*.sh
python3 -m py_compile src/orch.py
```

Optional manual smoke:

```bash
scripts/new_project.sh contrib-test "validate workflow"
scripts/mark_next_done.sh contrib-test
python3 - <<'PY'
from src.orch import select_next_item
print(select_next_item("contrib-test"))
PY
```

## Pull request checklist

- [ ] Behavior change is described clearly.
- [ ] Docs updated (README/ROADMAP/CHANGELOG as needed).
- [ ] No secrets or machine-specific paths.
- [ ] Smoke checks pass locally.

## Commit style (recommended)

- `docs: ...`
- `feat: ...`
- `fix: ...`
- `chore: ...`

## Reporting issues

Use the issue templates in `.github/ISSUE_TEMPLATE/` when possible.
