# Phase Completion Audit — Verified Findings

**Date:** 2026-03-28
**Method:** Automated audit (subagent) + manual fact-checking against actual codebase

The automated audit found many "CRITICAL" issues that turned out to be false —
it missed existing code. The findings below are **verified** after fact-checking.

## False positives from automated audit (things that ARE wired)

These were flagged as "BROKEN" or "never called" but actually exist:
- `write_event()` in observe.py — exists at line 279
- All 5 lens functions in introspect.py — exist at lines 450-652
- `persona_for_goal()` — called in `poe.py:550` (CEO layer)
- `attribute_failure_to_skills()` — called in `agent_loop.py:1239`
- `extract_skills()` — called in `agent_loop.py:502` and `cli.py:1274`
- `synthesize_skill()` — called in `agent_loop.py:528`
- `analyze_step_costs()` — called in `agent_loop.py:340`
- `morning_briefing()` — exists in `mission.py:1016`
- Auto-recovery — wired in `agent_loop.py:1465-1497`
- Mermaid diagram — in README at line 23

## Verified real issues (grouped by severity)

### CRITICAL

**C1. Circuit breaker state not checked at skill matching time**
- `skills.py:84` defines `circuit_state` field
- `skills.py:749` updates it on failure
- BUT `find_matching_skills()` does NOT filter out `circuit_state="open"` skills
- Means a broken skill keeps being injected into prompts after 3 failures
- **Fix:** Add `if s.circuit_state == "open": continue` to find_matching_skills()

**C2. Silent JSONL corruption recovery**
- `rules.py:93-96`, `memory.py`, `skills.py` — multiple modules swallow corrupted
  JSONL lines with bare `except Exception: continue`
- A single corrupted line silently drops that entry with no log
- Cumulative: if the file gets partially corrupted, data silently disappears
- **Fix:** Log corrupted lines at WARNING level

### HIGH

**H1. cost_budget defaults to None (off)**
- `agent_loop.py:562` — `cost_budget: Optional[float] = None`
- Means cost control is opt-in, not default
- The adversarial review script sets it to $5.00, but normal `poe-run` usage has no budget
- **Fix:** Consider a default budget or at minimum log estimated cost upfront

**H2. parallel_fan_out defaults to 0 (sequential)**
- Same issue — parallel execution is opt-in via the parameter
- Normal loop runs never get parallel execution
- **Fix:** Default to 3 when dependency annotations are present

**H3. Auto-promotion for skills is evolver-triggered, not continuous**
- `run_skill_maintenance()` in `evolver.py` handles promotion
- But evolver runs on heartbeat intervals (every ~10 ticks), not after every loop
- Skill promotion can be delayed by hours
- **Fix:** Call `maybe_auto_promote_skills()` in `_finalize_loop()`

**H4. No test for cost_budget actually stopping a loop**
- We test token_budget but no test for cost_budget hitting the slush limit
- **Fix:** Add test with mock adapter that burns known tokens + cost check

**H5. Mission recovery is pass-through, not automatic**
- `mission.py` runs milestones sequentially
- If milestone N fails, mission stops — no retry or skip-to-next
- Phase 34 claims "recovers from blocks" but this means the sheriff/heartbeat
  restarts the drain, not that the mission itself retries
- **Fix:** Add milestone retry or skip option

**H6. Docker image has no HEALTHCHECK**
- Dockerfile has no HEALTHCHECK instruction
- Container can start but fail silently in orchestrated environments
- **Fix:** Add `HEALTHCHECK CMD python3 src/bootstrap.py smoke`

### MEDIUM

**M1. Remaining stderr print() calls in agent_loop.py**
- ~10 places still use `print(f"[poe]...", file=sys.stderr)` alongside structured logging
- Not harmful but inconsistent — grep for debugging misses these
- Phase 43 claimed "all 11 modules instrumented" but mixed print/log remains

**M2. Step type regex patterns are untested for edge cases**
- `metrics.py:100-108` — patterns like `r"\bresearch\b"` tested only implicitly
- No dedicated test validates classification accuracy
- **Fix:** Add parametrized test with edge cases

**M3. Constraint patterns missing some unsafe_network cases**
- `constraint.py` catches curl, wget, git push, but not:
  - `requests.post` to arbitrary URLs
  - `upload to S3/GCS`
  - `webhook` (actually this one IS there)
- **Fix:** Add patterns for common Python HTTP library calls

**M4. Recovery plan params not fully utilized**
- `_RECOVERY_TABLE` has params like `max_steps=12` and hints
- Auto-recovery in agent_loop.py passes `max_steps` and `max_iterations` but
  ignores the `hint` param — the hint could improve the retry
- **Fix:** Inject hint into ancestry_context_extra for retry

**M5. Scratchpad shared.files_found only tracks .py files**
- `agent_loop.py` scratchpad accumulates `files_found` from step results
- Only checks `src/*.py` — misses `.md`, `.yaml`, `.json`, config files
- **Fix:** Extend glob to include common file types

**M6. find_recurring_patterns() exists but is only CLI-accessible**
- `poe-introspect --patterns` works, but no automation calls it
- Phase 46 claims "scaffolding" not "DONE" so this is expected
- **Fix:** Wire into evolver or heartbeat for periodic pattern check

### LOW

**L1. .dockerignore doesn't exclude memory/ directory**
- Runtime JSONL files could be baked into Docker images
- Only matters if building images from a dirty workspace

**L2. No test that Docker image builds**
- CI was removed; no automated Docker build verification

**L3. Persona routing table (_PERSONA_ROUTING) is manually maintained**
- Adding a new persona requires manually updating the routing keywords
- No validation that all personas in the registry have routing entries

**L4. TF-IDF memory retrieval quality never measured**
- `memory.py` implements TF-IDF but no baseline comparison exists
- We don't know if it's actually better than naive recency-based retrieval

**L5. ROADMAP.md is 900+ lines and conflates vision with status**
- Self-review Run 4 correctly identified this as a maintainability issue
- Not a bug but reduces doc trustworthiness over time

## Summary

| Severity | Count | Pattern |
|----------|-------|---------|
| CRITICAL | 2 | Circuit breaker not checked; silent data loss |
| HIGH | 6 | Defaults off; missing tests; incomplete automation |
| MEDIUM | 6 | Inconsistent logging; incomplete patterns; unused params |
| LOW | 5 | Docker, docs, measurement gaps |

The automated audit dramatically overstated the problem (claimed 9 CRITICAL when
only 2 are real). The root cause: it searched for function names but missed
indirect calls, lazy imports, and re-exports. This is itself a useful finding —
our review system still struggles with import indirection.

Most issues are **wiring defaults** (features exist but are off by default) and
**missing edge-case tests**, not fundamental architectural failures.
