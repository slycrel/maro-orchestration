# Captain's Log — Learning System Changelog

*Spec for a narrated, append-only event stream that tracks what the learning pipeline actually does.*

---

## Concept

Every time the learning system takes an action — promotes a skill, breaks a circuit, applies an evolver suggestion, graduates a standing rule, decays a lesson into the graveyard — it writes a timestamped, human-readable log entry. Not raw data (that's `outcomes.jsonl`). Not aggregated metrics (that's the dashboard). A narrative record of decisions the system made about its own knowledge.

The tone is "captain's log" — factual, understated, occasionally dry. Not cosplay; more like a ship's log that happens to be recording an AI agent's self-modification history. The kind of thing you'd scroll through with coffee and actually notice when something looks off.

### Example Entries

```
[2026-04-09 14:32] SKILL CIRCUIT OPEN — "jina-x-scraper" hit 3 consecutive failures.
  Recent inputs: web URL, web URL, web URL (0/3 were X posts).
  Utility: 0.82 → 0.61. Queued for rewrite.
  Note: failures may reflect input mismatch, not skill degradation.

[2026-04-09 14:33] SKILL REWRITE — "jina-x-scraper" revised by evolver.
  Delta: added URL-pattern pre-check before Jina call.
  Circuit: OPEN → HALF_OPEN (probationary). Next 2 successes close it.

[2026-04-09 15:01] HYPOTHESIS PROMOTED — "x-post-deleted-pattern"
  Observation: 4 confirmations across 3 sessions. 0 contradictions.
  Now a standing rule: "When X post returns 'page doesn't exist',
  check for author repost within 48h before marking dead."

[2026-04-09 15:44] EVOLVER APPLIED — suggestion #247 (skill_pattern, confidence: 0.91)
  Target: decompose prompt for research tasks.
  Change: added "check existing skills before proposing new approach."
  Based on: 12 outcomes where existing skills were re-derived from scratch.

[2026-04-09 16:10] SKILL PROMOTED — "github-readme-extract" provisional → established.
  Utility: 0.78 over 7 uses. 6 successes, 1 partial.
  Circuit: CLOSED (no failures in last 5 uses).

[2026-04-09 17:30] LESSON DECAYED — "always-verify-api-endpoints-exist"
  Score: 0.31 (below 0.40 threshold). Last reinforced: 14 days ago.
  Moved to graveyard. Recoverable via `search_graveyard("api endpoints")`.

[2026-04-09 18:00] A/B RETIRED — variant "jina-x-scraper-v2" beat "jina-x-scraper-v1"
  v2: 4/5 successes (0.80). v1: 2/5 successes (0.40).
  v1 retired. v2 is now canonical.

[2026-04-09 18:15] STANDING RULE CONTRADICTED — "prefer-playwright-over-jina"
  Contradictions: 3 (now exceeds confirmations: 2). Demoted to hypothesis.
  Evidence: Jina succeeded on 3 recent article-style pages where Playwright timed out.

[2026-04-09 19:00] RULE GRADUATED — "github-readme-extract" skill → Stage 5 rule.
  Trigger: "extract readme from github repo"
  pass^3 = 0.74, use_count = 8. Now bypasses decompose entirely.
  Cost savings: ~$0.005/invocation → $0 (zero LLM).
```

---

## What Gets Logged

Every learning-system *action* (not observation) gets an entry. Specifically:

### Skill Lifecycle
- **SKILL SYNTHESIZED** — new skill created from successful loop with no prior match
- **SKILL PROMOTED** — provisional → established (with utility stats)
- **SKILL DEMOTED** — established → provisional (with reason)
- **SKILL REWRITE** — evolver rewrote skill body (with delta summary)
- **SKILL CIRCUIT OPEN/HALF_OPEN/CLOSED** — state transitions (with failure context)
- **SKILL VARIANT CREATED** — A/B challenger spawned
- **A/B RETIRED** — variant competition resolved (with win/loss stats)
- **ISLAND CULLED** — bottom-half skills removed from an island (with names)

### Knowledge Crystallization
- **LESSON RECORDED** — new tiered lesson from outcome (with tier and confidence)
- **LESSON REINFORCED** — existing lesson confirmed (with new score)
- **LESSON DECAYED** — dropped below threshold, moved to graveyard
- **LESSON RECOVERED** — pulled back from graveyard by relevance match
- **HYPOTHESIS CREATED** — first observation of a pattern
- **HYPOTHESIS PROMOTED** — 2+ confirmations → standing rule
- **HYPOTHESIS CONTRADICTED** — contradiction count exceeds confirmations
- **STANDING RULE CONTRADICTED** — existing rule weakened by counter-evidence
- **RULE GRADUATED** — established skill → Stage 5 hardcoded rule
- **RULE DEMOTED** — Inspector wrong-answers triggered fallback to skill
- **CANON CANDIDATE** — lesson flagged for AGENTS.md promotion (human-gated)

### Evolver Actions
- **EVOLVER APPLIED** — suggestion auto-applied (with category, target, change summary)
- **EVOLVER GENERATED** — suggestion created but not yet applied (with confidence)
- **EVOLVER SKIPPED** — insufficient data or recent-enough prior run
- **GRADUATION PROPOSED** — repeated failure pattern → auto-fix suggestion

### Recovery & Diagnosis
- **AUTO-RECOVERY** — stuck loop diagnosed and auto-retried with adjusted params
- **DIAGNOSIS** — failure class identified (with class, severity, evidence summary)

### Decisions
- **DECISION RECORDED** — planning decision logged with rationale and alternatives

---

## Implementation

### Storage

```
memory/captains_log.jsonl
```

One JSON object per line. Fields:

```json
{
  "timestamp": "2026-04-09T14:32:00Z",
  "event_type": "SKILL_CIRCUIT_OPEN",
  "subject": "jina-x-scraper",
  "summary": "Hit 3 consecutive failures. Utility: 0.82 → 0.61. Queued for rewrite.",
  "context": {
    "recent_inputs": ["web URL", "web URL", "web URL"],
    "x_post_ratio": "0/3",
    "utility_before": 0.82,
    "utility_after": 0.61
  },
  "note": "Failures may reflect input mismatch, not skill degradation.",
  "loop_id": "loop-2026-04-09-143200",
  "related_ids": ["skill:jina-x-scraper"]
}
```

### Rendering

The raw `.jsonl` is for machines. For humans, a renderer formats it as the captain's log text shown above. Two access paths:

1. **CLI: `poe-log [--since DATE] [--type EVENT_TYPE] [--subject PATTERN] [--limit N]`**
   - Default: last 20 entries, all types
   - `--since 2026-04-01` for date filtering
   - `--type SKILL` for skill-related events only
   - `--subject jina` for pattern matching on subject
   - Output: formatted text (the captain's log style)

2. **Dashboard panel** — if/when the orchestration dashboard becomes a command center, the log is a natural sidebar or tab. Scrollable, filterable, with links to related artifacts.

3. **Director injection** — the Poe director can query recent log entries to understand what the learning system has been doing. Useful for context like "the last 3 evolver runs applied skill rewrites to scraping tools" before deciding how to approach a new scraping task.

### Writing Entries

A single function, called from every learning-system action point:

```python
def log_event(
    event_type: str,
    subject: str,
    summary: str,
    context: dict | None = None,
    note: str | None = None,
    loop_id: str | None = None,
    related_ids: list[str] | None = None,
) -> None:
    """Append a captain's log entry. Never raises."""
```

Call sites (non-exhaustive — each maps to a log event type above):

| Module | Function | Event |
|--------|----------|-------|
| skills.py | `update_skill_utility()` | SKILL_CIRCUIT_* on state change |
| skills.py | `maybe_auto_promote_skills()` | SKILL_PROMOTED |
| skills.py | `maybe_demote_skills()` | SKILL_DEMOTED |
| skills.py | `rewrite_skill()` | SKILL_REWRITE |
| skills.py | `create_skill_variant()` | SKILL_VARIANT_CREATED |
| skills.py | `retire_losing_variants()` | A/B_RETIRED |
| skills.py | `run_island_cycle()` | ISLAND_CULLED |
| evolver.py | `synthesize_skill()` | SKILL_SYNTHESIZED |
| evolver.py | `_apply_suggestion_action()` | EVOLVER_APPLIED |
| evolver.py | `run_evolver()` | EVOLVER_GENERATED / EVOLVER_SKIPPED |
| memory.py | `record_tiered_lesson()` | LESSON_RECORDED |
| memory.py | `reinforce_lesson()` | LESSON_REINFORCED |
| memory.py | `observe_pattern()` | HYPOTHESIS_CREATED / HYPOTHESIS_PROMOTED |
| memory.py | `contradict_pattern()` | HYPOTHESIS/STANDING_RULE_CONTRADICTED |
| memory.py | `record_decision()` | DECISION_RECORDED |
| rules.py | `graduate_skill_to_rule()` | RULE_GRADUATED |
| rules.py | `demote_rule_to_skill()` | RULE_DEMOTED |
| graduation.py | (main function) | GRADUATION_PROPOSED |
| introspect.py | `diagnose_loop()` | DIAGNOSIS |
| agent_loop.py | auto-recovery path | AUTO_RECOVERY |
| gc_memory.py | `_gc_tiered_lessons()` | LESSON_DECAYED (for graveyard moves) |

### The "Note" Field

This is the editorial layer. Most entries won't have one. But when context suggests a nuance the raw data doesn't capture — like "failures may reflect input mismatch, not skill degradation" — the calling code can add a note. Over time, these notes become some of the most valuable entries in the log, because they capture *judgment* about the learning event, not just the event itself.

The note field is also where the captain's log personality lives. Not every entry needs flavor, but the ones that have it should be worth reading.

---

## Relationship to Memory Architecture

This log is a natural inhabitant of the **Ledger** view from the knowledge layer architecture (doc 01):

- **Temporal**: ordered by timestamp, append-only, git-like history
- **Associative**: `related_ids` field links to skills, lessons, rules (Web view)
- **Persona-aware**: the director can query it through a Lens; Jeremy reads it differently than the evolver would

It's also a concrete first artifact for the "learning health" metric suggested in the audit (doc 06). A simple query over `captains_log.jsonl` counting action events per time window gives you the pulse of the learning system without building a separate metrics pipeline.

### Future: Input Classification Tag

Jeremy's Jina example points to a missing piece: the log can *observe* that failures might be input mismatches, but the system can't *prevent* the circuit breaker from firing on mismatches without an input classification layer. A future addition:

- Tag each skill invocation with input characteristics (URL type, content type, source)
- Compare against skill's declared domain (X posts, articles, GitHub repos, etc.)
- Only count failures against the skill when input matches its domain
- Log mismatches as their own event: **INPUT_MISMATCH** — "jina-x-scraper invoked on non-X URL. Failure not counted against skill."

This is the failure attribution concept mentioned in the audit. It's deeper work, but the log gives you the visibility to know when it's needed.

---

## Scope and Boundaries

What this is:
- A narrated changelog of learning-system actions
- Append-only, cheap to write, human-readable
- A debugging tool for "why did the system unlearn X?"
- A trust-building artifact for the operator

What this is not:
- A replacement for outcomes.jsonl (raw data)
- A replacement for the dashboard (aggregated metrics)
- A decision-making input for the agent loop (yet — director injection is optional)
- A full audit trail (change_log.jsonl already does that for mutations)

---

## Implementation Priority

This is a **Phase 60-ish** feature. Low risk, high signal, no architectural prerequisites. The `log_event()` function is ~30 lines. The call sites are straightforward additions to existing functions. The CLI renderer is another ~50 lines. Total scope: a few hundred lines across 8-10 files.

Can be built incrementally: start with skill lifecycle events (highest signal for Jeremy's trust concern), add evolver/knowledge events next, add the note field and director injection last.
