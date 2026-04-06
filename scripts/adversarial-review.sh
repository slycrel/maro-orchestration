#!/usr/bin/env bash
# Adversarial self-review: run the orchestration system against its own codebase.
#
# Pass A (default): blind — only gives repo URL, system must figure out setup.
# Pass B (--seeded): pre-checks out repo locally, separates setup from thinking.
# Garrytan pass (--garrytan): forces garrytan persona (power model, phase-gated).
#
# Historical results (runs against this repo):
#   Run 1-3: 30-50% file accuracy (sequential)
#   Run 4:   100% file accuracy (multi-plan + anti-hallucination guards)
#   Run 6:   100%, ~6.5min, 1.65M tokens (parallel + cost tracking)
#
# See tests/regression/adversarial-self-review-spec.md for full test spec.
#
# Usage:
#   bash scripts/adversarial-review.sh                      # Pass A (blind)
#   bash scripts/adversarial-review.sh --seeded             # Pass B (local checkout)
#   bash scripts/adversarial-review.sh --garrytan           # Pass A + garrytan persona
#   bash scripts/adversarial-review.sh --seeded --garrytan  # Pass B + garrytan
#   bash scripts/adversarial-review.sh --dry-run            # plan only, no execution

set -euo pipefail
cd "$(dirname "$0")/.."

MODE="blind"
GARRYTAN=""
DRY_RUN=""
for arg in "$@"; do
    case "$arg" in
        --seeded)   MODE="seeded" ;;
        --garrytan) GARRYTAN="1" ;;
        --dry-run)  DRY_RUN="1" ;;
    esac
done

LABEL="${MODE}$([ -n "$GARRYTAN" ] && echo '-garrytan' || echo '')"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_FILE="output/self-review-${TIMESTAMP}-${LABEL}.log"
REPORT_FILE="output/self-review-report-${TIMESTAMP}-${LABEL}.md"

echo "=== Adversarial Self-Review (${LABEL}) ==="
echo "Working directory: $(pwd)"
echo "Log:    ${LOG_FILE}"
echo "Report: ${REPORT_FILE}"
echo

# Pre-flight: run the test suite
echo "--- Pre-flight: test suite ---"
python3 -m pytest tests/ -q --tb=line 2>&1 | tail -5
echo

# Build goal prefix (garrytan: forces power model + phase-gated persona)
PREFIX=""
if [[ -n "$GARRYTAN" ]]; then
    PREFIX="garrytan: "
fi

# Build the goal based on mode
if [[ "$MODE" == "blind" ]]; then
    REVIEW_GOAL="${PREFIX}Check out the repo at https://github.com/slycrel/openclaw-orchestration and do a deep, critical review of what it gets right, what it's missing, and what should improve. Be adversarial, concrete, and evidence-based. Document: (1) what source you used and how you set up, (2) architecture strengths with evidence, (3) weaknesses, risks, and gaps, (4) actionable near-term and medium-term improvements, (5) anything you couldn't verify. Write the full review to ${REPORT_FILE}."
else
    REVIEW_GOAL="${PREFIX}Perform a deep, critical review of the orchestration codebase at $(pwd). The repo is already checked out here — read it directly. Be adversarial, concrete, and evidence-based. Document: (1) architecture strengths with evidence, (2) weaknesses, risks, and gaps with specific file/line references, (3) test coverage quality — not just pass count but whether tests catch real bugs, (4) actionable near-term and medium-term improvements, (5) anything you couldn't verify. Write the full review to ${REPORT_FILE}."
fi

echo "--- Review goal (${LABEL}) ---"
echo "${REVIEW_GOAL:0:220}..."
echo

mkdir -p output

if [[ -n "$DRY_RUN" ]]; then
    PYTHONPATH=src python3 -c "
from handle import handle
result = handle('''${REVIEW_GOAL}''', dry_run=True, verbose=True)
print(f'Status: {result.status}')
print(f'Lane:   {result.lane}')
print(f'Result: {str(result.result)[:400]}')
"
    echo
    echo "=== Dry run complete ==="
    exit 0
fi

# Run the review
echo "--- Starting review loop ---"
PYTHONPATH=src python3 -c "
import os, time
os.environ.setdefault('POE_LOG_LEVEL', 'INFO')

from handle import handle

started = time.monotonic()
result = handle(
    '''${REVIEW_GOAL}''',
    verbose=True,
)
elapsed = time.monotonic() - started

print()
print('=' * 60)
print(f'Status:    {result.status}')
print(f'Lane:      {result.lane}')
print(f'handle_id: {result.handle_id}')
print(f'Tokens:    {result.tokens_in + result.tokens_out:,}')
print(f'Elapsed:   {elapsed:.0f}s ({elapsed/60:.1f}min)')

if result.loop_result:
    lr = result.loop_result
    done_steps    = [s for s in lr.steps if s.status == 'done']
    blocked_steps = [s for s in lr.steps if s.status == 'blocked']
    print(f'Steps:     {len(done_steps)} done / {len(blocked_steps)} blocked / {len(lr.steps)} total')
    for s in lr.steps:
        icon = '+' if s.status == 'done' else 'x'
        tok  = s.tokens_in + s.tokens_out
        print(f'  [{icon}] step {s.index:2d}  {tok:>8,} tok  {s.elapsed_ms:>6,}ms  {s.text[:70]}')

    # Introspect
    try:
        from introspect import diagnose_loop, run_lenses, aggregate_lenses, plan_recovery, _build_step_profiles, _load_loop_events
        diag = diagnose_loop(lr.loop_id)
        print(f'\nDiagnosis: {diag.failure_class} ({diag.severity})')
        if diag.recommendation:
            print(f'Recommend: {diag.recommendation}')
        events   = _load_loop_events(lr.loop_id)
        profiles = _build_step_profiles(events)
        lrs      = run_lenses(diag, profiles)
        if lrs:
            agg = aggregate_lenses(diag, lrs)
            print(f'Lenses:    agreement={agg.lens_agreement} confidence={agg.confidence:.0%}')
            print(f'Action:    {agg.primary_action}')
        rec = plan_recovery(diag)
        if rec:
            print(f'Recovery:  [{\"AUTO\" if rec.auto_apply else \"SUGGEST\"}] {rec.action}')
    except Exception as exc:
        print(f'Introspection error: {exc}')

print()
print('Result snippet:')
print(str(result.result)[:800])
" 2>&1 | tee "${LOG_FILE}"

echo
echo "=== Review complete (${LABEL}) ==="
echo "Run log:  ${LOG_FILE}"
echo "Report:   ${REPORT_FILE} (if written by the agent)"
