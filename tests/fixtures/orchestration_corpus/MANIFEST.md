# Orchestration Test Corpus

Harvested from `~/.maro/workspace/runs/` + `projects/` by `scripts/harvest_corpus.py`. Dev fixtures only — not runtime data.

Each slice has a full `.jsonl` and a `.thinned.jsonl` (one+ representative per normalized signature, with `_cluster_size`). Records carry `_provenance` (path under the workspace). Secret-shaped strings are redacted.

**Not captured:** byte-exact per-step LLM prompt/response (never persisted) and inner tool_events (capture is new — only future runs will have them).

Total records: **5646**

| slice | records | unique sigs | thinned |
|-------|--------:|------------:|--------:|
| event_auto_recovery | 10 | 2 | 4 |
| event_claim_probed | 407 | 253 | 407 |
| event_claim_verifier_outcome | 69 | 2 | 6 |
| event_closure_verdict | 162 | 3 | 8 |
| event_diagnosis | 306 | 209 | 290 |
| event_hypothesis_created | 3 | 3 | 3 |
| event_hypothesis_promoted | 2 | 2 | 2 |
| event_input_mismatch | 1 | 1 | 1 |
| event_lesson_recorded | 122 | 116 | 122 |
| event_loop_created | 458 | 126 | 193 |
| event_memory_consolidated | 1 | 1 | 1 |
| event_metacognitive_decision | 1496 | 162 | 294 |
| event_navigator_decided | 5 | 2 | 5 |
| event_quality_gate_verdict | 124 | 62 | 91 |
| event_recall_performed | 107 | 2 | 6 |
| event_rule_verified | 6 | 2 | 6 |
| event_scope_generated | 144 | 2 | 6 |
| event_scope_parse_failed | 10 | 1 | 3 |
| event_skill_circuit_open | 76 | 57 | 72 |
| event_skill_synthesized | 2 | 2 | 2 |
| event_step_too_broad | 240 | 49 | 113 |
| event_validator_shadowed | 42 | 10 | 20 |
| loop_outcomes | 459 | 131 | 199 |
| step_outcomes | 1394 | 1265 | 1350 |
