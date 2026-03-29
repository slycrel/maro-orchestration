#!/usr/bin/env bash
# Run a phase completion audit through the orchestration system.
# Smaller/faster than the full adversarial review — focuses on verifying
# that claimed "DONE" phases actually work.
#
# Usage:
#   POE_LOG_LEVEL=INFO bash scripts/audit-phases.sh
#   bash scripts/audit-phases.sh --dry-run

set -euo pipefail
cd "$(dirname "$0")/.."

DRY_RUN=""
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN="1"

AUDIT_GOAL="Audit the completed phases in this orchestration codebase at $(pwd). \
Read ROADMAP.md to find all phases marked DONE or COMPLETE. For each one, verify: \
(1) the claimed feature actually exists in the code (check the specific files mentioned), \
(2) there are tests that exercise the feature (not just dry_run tests), \
(3) the feature is wired into the execution path (actually called, not dead code). \
Flag any phase where the claim doesn't match reality. \
Write findings to output/phase-audit-report.md with: phase number, claim, verdict (VERIFIED/PARTIAL/BROKEN), evidence."

echo "=== Phase Completion Audit (orchestrated) ==="

if [[ -n "$DRY_RUN" ]]; then
    python3 -c "
import sys; sys.path.insert(0, 'src')
from persona import PersonaRegistry, spawn_persona
result = spawn_persona('researcher', '''${AUDIT_GOAL}''', registry=PersonaRegistry(), dry_run=True, max_steps=8)
print(f'Status: {result.status}')
print(f'Summary: {result.summary}')
"
    exit 0
fi

mkdir -p output
python3 -c "
import sys, os, time
sys.path.insert(0, 'src')
os.environ.setdefault('POE_LOG_LEVEL', 'INFO')

from agent_loop import run_agent_loop, _configure_logging
_configure_logging(verbose=True)

result = run_agent_loop(
    '''${AUDIT_GOAL}''',
    project='phase-audit',
    max_steps=8,
    max_iterations=30,
    parallel_fan_out=3,
    cost_budget=3.0,
    verbose=True,
)
print()
print(f'Status: {result.status}')
print(f'Summary: {result.summary()}')
print(f'Tokens: {result.total_tokens_in + result.total_tokens_out:,}')
" 2>&1 | tee output/phase-audit-run.log

echo "=== Audit complete ==="
