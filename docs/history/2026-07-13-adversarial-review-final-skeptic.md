---
status: record
---

I could not write `output/adversarial-review-2026-07-13-final-skeptic.md` because this session is read-only and the user instruction also said not to modify files. Review content:

1. **Low: recursion check-in payload is off by one after the enqueue-before-notify fix.**  
   [src/director.py](/home/clawd/claude/maro-orchestration/src/director.py:1035) computes `checkin_number = origin["checkins_sent"] + 1`, but [src/director.py](/home/clawd/claude/maro-orchestration/src/director.py:1081) already increments `origin["checkins_sent"]` before `_fire_checkin()` is called. First check-in now emits `checkin_number=2` while the carried origin records `checkins_sent=1`.  
   Concrete proof: `_advance_origin_with_checkin({'origin': {}}, 2)` returns `{'checkins_sent': 1, ...}`, and `_fire_checkin`’s formula turns that into payload number `2`. Existing tests assert `goal_pass` and origin count but never assert `payload["checkin_number"]` ([tests/test_escalation.py](/home/clawd/claude/maro-orchestration/tests/test_escalation.py:188)).  
   Fix: either pass the pre-advance count to `_fire_checkin`, or have `_fire_checkin` use `origin["checkins_sent"]` directly.

2. **Medium: skill candidates are permanently consumed even when extraction crashes before evaluating them.**  
   [src/evolver.py](/home/clawd/claude/maro-orchestration/src/evolver.py:409) calls `extract_skills`, swallows any exception at [src/evolver.py](/home/clawd/claude/maro-orchestration/src/evolver.py:418), then still marks every candidate consumed at [src/evolver.py](/home/clawd/claude/maro-orchestration/src/evolver.py:426). That means a transient adapter failure, bad API key, timeout, or malformed LLM response can erase the only retry opportunity for a `skill_candidate`. This is distinct from “extract_skills returned [] after looking at the candidate”; here the consumer did not successfully act.  
   The test currently pins the lossy behavior ([tests/test_evolver.py](/home/clawd/claude/maro-orchestration/tests/test_evolver.py:396)), but that test encodes “avoid retrying indefinitely” by silently dropping signal. Root-cause fix should separate “declined after successful evaluation” from “evaluation failed”: only mark consumed after `extract_skills` returns normally, or stamp a retry/error field with bounded retry/backoff.