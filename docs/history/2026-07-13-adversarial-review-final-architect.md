---
status: record
---

I couldn’t write `output/adversarial-review-2026-07-13-final-architect.md` because this session is read-only and the user explicitly said not to modify files. Here is the review content.

1. **High: enqueue failure still completes the escalation task and kills the chain silently.**  
   [src/director.py](/home/clawd/claude/maro-orchestration/src/director.py:1221) catches `task_store.enqueue()` failures in the `continue` branch, only logs a warning, and returns `EscalationDecision(action="continue", followup_task_id=None)`. The same pattern exists for `narrow` at [src/director.py](/home/clawd/claude/maro-orchestration/src/director.py:1251). Then [src/handle_queue.py](/home/clawd/claude/maro-orchestration/src/handle_queue.py:267) marks the original escalation task complete as long as `handle_task()` returns without raising.  
   Concrete failure: task store is temporarily down, director decides “continue”, enqueue raises, no continuation exists, the escalation task is completed, and the goal dies. f837c06 fixed the misleading check-in, but not the underlying dead-chain failure. The fix should make enqueue failure cross a boundary explicitly: return/surface an error disposition, notify, or raise so `drain_task_store()` marks the task failed instead of complete.

2. **Medium: `recursion_checkin` notifications do not carry the identifier that the notification boundary exposes.**  
   `_fire_checkin()` emits `job_id` and `parent_job_id`, but no `handle_id` at [src/director.py](/home/clawd/claude/maro-orchestration/src/director.py:1053). `notify.emit()` derives `MARO_HANDLE_ID` only from `payload["handle_id"]` at [src/notify.py](/home/clawd/claude/maro-orchestration/src/notify.py:112) and exports it at [src/notify.py](/home/clawd/claude/maro-orchestration/src/notify.py:149).  
   Concrete failure: a default-on recursion check-in reaches a shell/Telegram bridge with blank `MARO_HANDLE_ID`, so the substrate cannot correlate the “still running” message with the run/thread it should let the user redirect. This is a boundary mismatch: either the event contract should be job-centric, or the payload should include the relevant handle, probably from `origin["parent_handle_id"]` until a child run handle exists.

3. **Low: check-in numbering is off by one after f837c06.**  
   `_advance_origin_with_checkin()` increments `origin["checkins_sent"]` before enqueue at [src/director.py](/home/clawd/claude/maro-orchestration/src/director.py:1081), then `_fire_checkin()` renders `checkin_number = origin["checkins_sent"] + 1` at [src/director.py](/home/clawd/claude/maro-orchestration/src/director.py:1035). The first emitted check-in reports `checkin_number == 2` while the enqueued origin correctly stores `checkins_sent == 1`.  
   Concrete failure: operators and downstream logs see every check-in numbered one higher than reality. This is small but exactly the kind of telemetry drift that undermines the new progress-notification feature.

I did not find a substantive regression in the 75b8ccc `_tabulate_agreement` extraction; its return counts match the prior per-row aggregation shape.