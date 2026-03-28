#!/usr/bin/env bash
# Adversarial self-review: run the orchestration system against its own codebase.
#
# Pass A (default): blind — only gives repo URL, system must figure out setup
# Pass B (--seeded): gives a clean local checkout path
#
# See tests/regression/adversarial-self-review-spec.md for full test spec.
#
# Usage:
#   POE_LOG_LEVEL=INFO bash scripts/adversarial-review.sh           # Pass A (blind)
#   POE_LOG_LEVEL=INFO bash scripts/adversarial-review.sh --seeded  # Pass B (pre-seeded)
#   bash scripts/adversarial-review.sh --dry-run                    # plan only

set -euo pipefail
cd "$(dirname "$0")/.."

MODE="blind"
DRY_RUN=""
for arg in "$@"; do
    case "$arg" in
        --seeded) MODE="seeded" ;;
        --dry-run) DRY_RUN="1" ;;
    esac
done

echo "=== Adversarial Self-Review (${MODE}) ==="
echo "Working directory: $(pwd)"
echo "Log level: ${POE_LOG_LEVEL:-WARNING}"
echo

# Pre-flight: bootstrap smoke + fast tests
echo "--- Pre-flight: bootstrap smoke ---"
python3 src/bootstrap.py smoke
echo "OK"
echo

echo "--- Pre-flight: fast tests ---"
python3 -m pytest -m "not slow" -q --tb=line 2>&1 | tail -3
echo

# Build the goal based on mode
if [[ "$MODE" == "blind" ]]; then
    REVIEW_GOAL="Check out the repo at https://github.com/slycrel/openclaw-orchestration and do a deep, critical review of what it gets right, what it's missing, and what should improve. Be adversarial, concrete, and evidence-based. Document: (1) what source you used and how you set up, (2) architecture strengths with evidence, (3) weaknesses, risks, and gaps, (4) actionable near-term and medium-term improvements, (5) anything you couldn't verify. Write the full review to output/self-review-report.md."
else
    REVIEW_GOAL="Perform a deep, critical review of the orchestration codebase at $(pwd). The repo is already checked out here — read it directly. Be adversarial, concrete, and evidence-based. Document: (1) architecture strengths with evidence, (2) weaknesses, risks, and gaps with specific file/line references, (3) test coverage quality — not just pass count but whether tests catch real bugs, (4) actionable near-term and medium-term improvements, (5) anything you couldn't verify. Write the full review to output/self-review-report.md."
fi

echo "--- Review goal (${MODE}) ---"
echo "${REVIEW_GOAL:0:200}..."
echo

if [[ -n "$DRY_RUN" ]]; then
    python3 -c "
import sys; sys.path.insert(0, 'src')
from persona import PersonaRegistry, spawn_persona
registry = PersonaRegistry()
result = spawn_persona('researcher', '''${REVIEW_GOAL}''', registry=registry, dry_run=True, max_steps=12)
print(f'Status: {result.status}')
print(f'Summary: {result.summary}')
"
    echo
    echo "=== Dry run complete ==="
    exit 0
fi

# Create output directory
mkdir -p output

# Run the review
echo "--- Starting review loop ---"
python3 -c "
import sys, os, time
sys.path.insert(0, 'src')
os.environ.setdefault('POE_LOG_LEVEL', 'INFO')

from agent_loop import run_agent_loop, _configure_logging
_configure_logging(verbose=True)

started = time.monotonic()
result = run_agent_loop(
    '''${REVIEW_GOAL}''',
    project='self-review',
    max_steps=12,
    max_iterations=40,
    verbose=True,
)
elapsed = time.monotonic() - started

print()
print('=' * 60)
print(f'Status:   {result.status}')
print(f'Steps:    {result.summary()}')
print(f'Tokens:   {result.total_tokens_in + result.total_tokens_out:,}')
print(f'Elapsed:  {elapsed:.0f}s ({elapsed/60:.1f}min)')

# Show step breakdown
done = [s for s in result.steps if s.status == 'done']
blocked = [s for s in result.steps if s.status == 'blocked']
print(f'Done:     {len(done)}')
print(f'Blocked:  {len(blocked)}')
for s in result.steps:
    icon = '+' if s.status == 'done' else 'x'
    tok = s.tokens_in + s.tokens_out
    print(f'  [{icon}] step {s.index:2d}  {tok:>8,} tok  {s.elapsed_ms:>6,}ms  {s.text[:60]}')

# Run introspection on the result
try:
    from introspect import diagnose_loop, run_lenses, aggregate_lenses, plan_recovery, _build_step_profiles, _load_loop_events
    diag = diagnose_loop(result.loop_id)
    print(f'\nDiagnosis: {diag.failure_class} ({diag.severity})')
    if diag.recommendation:
        print(f'Recommendation: {diag.recommendation}')
    events = _load_loop_events(result.loop_id)
    profiles = _build_step_profiles(events)
    lens_results = run_lenses(diag, profiles)
    if lens_results:
        agg = aggregate_lenses(diag, lens_results)
        print(f'Lens agreement: {agg.lens_agreement} | confidence: {agg.confidence:.0%}')
        print(f'Primary action: {agg.primary_action}')
    recovery = plan_recovery(diag)
    if recovery:
        tag = 'AUTO' if recovery.auto_apply else 'SUGGEST'
        print(f'Recovery [{tag}]: {recovery.action}')
except Exception as exc:
    print(f'Introspection error: {exc}')
" 2>&1 | tee output/self-review-run.log

echo
echo "=== Review complete (${MODE}) ==="
echo "Run log: output/self-review-run.log"
echo "Report:  output/self-review-report.md (if generated)"
