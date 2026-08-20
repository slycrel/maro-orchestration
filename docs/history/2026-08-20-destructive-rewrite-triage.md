---
status: record
---

# Finishing the destructive-rewrite sweep — the 70-site triage (2026-08-20)

`scripts/scan_destructive_rewrites.py` was written on 2026-08-17 to find the
**destructive subset** of the silent-drop census: not silence, but *drop +
write-back*. A row dropped from a read is recoverable — the bytes are still on
disk. A row dropped from a loop whose result is written back is gone.

That chunk fixed its top hit (`skills.py`) and left the rest of the scanner's
output explicitly untriaged in BACKLOG. This record closes that: all 70 RISK
sites read by hand — **three real defects (6 scanner sites), 64 false
positives** — with the reason written down so nobody re-reads them next month.

## The three real ones (all probed live before the fix)

### 1. `doctor.cleanup_workspace_skills` — destructive

Third instance of the skills-store family, and the sharpest framing of it yet,
because this is a **repair verb**: an operator runs it *because* the store looks
wrong.

| probe | before |
|---|---|
| one non-UTF-8 byte in skills.jsonl | `UnicodeDecodeError` — verb dies |
| one *truncated* row (what a crashed append actually leaves) | 4 lines in, **3 out** — row destroyed |
| what the operator was told | `Cleaned: 3 skills remain (0 stale-hash + 0 duplicate(s) removed, 0 total)` |

The last line is the worst part. The summary counts only what the verb *meant*
to remove, so a destroyed row is reported as "0 removed". The fix strands and
carries unreadable rows verbatim, prints them as kept, and writes under the
file's lock via `atomic_write(errors="surrogateescape")` — the bare `write_text`
it replaced also raced `save_skill`, which fires on every skill match.

**Side-find, fixed here:** this verb *and* doctor's duplicate check both
hardcoded `~/.maro/workspace/memory/skills.jsonl`, while every runtime caller
resolves through `config.workspace_root()`. Under a `MARO_WORKSPACE` override,
doctor reported on — and rewrote — a store the running system was not using.
Now `_workspace_skills_path()`; byte-identical behaviour with no override set.

### 2. `interrupt.InterruptQueue` — the operator's control channel

`_read_lines()` strict-decoded the queue, and it feeds `peek()`, which **gates**
`poll()`, `clear()` and `is_empty()`. So one crash-torn append raised
`UnicodeDecodeError` out of every consumer: stop/pivot messages, and the kill
switch's own STOP interrupt, stopped reaching a running loop until someone
repaired the file by hand.

Not silent — `loop_post_step` already logs an ERROR per step, and its comment
says exactly why ("silent failure means user-initiated interrupts could be
silently dropped while the loop keeps running"). But total: the channel stayed
dead. One torn byte should cost one interrupt, and now does.

Both `_mark_applied` merges already had the preserve branch, but re-dumped
every row they *did* parse — so a byte-tainted-but-valid twin came back as clean
`\udcXX` escapes and the corruption signal was erased forever. `loads_clean`
refuses taint, which sends those rows down the preserve branch that was already
there.

### 3. `gc_memory._gc_outcomes` — the r4 deferral, closed

A strict decode inside `except Exception: return 0, 0, 0`.

```
GC healthy   : (2, 1, 0)
GC after one torn append : (0, 0, 0)
```

"Nothing to collect", forever, with **no log line at all**, while the store grew
without bound. The trim itself was already verbatim-preserve, so this was the
silent-full-disable flavour rather than the destructive one. Announced read; and
a byte-tainted row's timestamp cannot be trusted to authorize its own deletion,
so taint now joins the keep-conservatively branch with everything else.

## The 64 false positives, by why

The scanner's docstring already warns that OK is a hint and that markdown and
single-object rewrites match its write markers. That is what most of these are.

| shape | count | examples | why it is not the bug |
|---|---|---|---|
| markdown / prose rewrite | 10 | `orch_items.parse_next` + `append_next_items`, `thread_brain._append_under`, `boot_protocol._read_completed_from_next`, `convo_miner.scan_maro_memory`, `pack._append_conflicts_note` | no JSON parse; every line is carried, and the "drop" is a regex non-match, not a discard |
| subprocess-output parser | 4 | `heartbeat._is_interactive_session_active`, `build_loop_runner._worker_session_already_active`, `container_exec._reseed_probe`, `worktree._sanitize_untrusted_git` | the parsed text is `pgrep`/`docker` stdout, not a durable store |
| stream / LLM-output parser | 9 | `llm._parse_stream_json`, `llm._stream_events`, `orch_bridges._tail_lines`, `orch_bridges._extract_session_result_from_text` | same — nothing on disk is being rebuilt |
| derived-index rebuild | 3 | `memory_ledger._update_memory_index`, `loop_report._render_devlog_html`, `portability.main` | the written file is generated *from* the source, and regenerating is the repair |
| read-only loader | 26 | `knowledge_lens.load_standing_rules` / `load_hypotheses` / `search_decisions`, `evolver_scans._load_baselines`, `graduation.scan_candidates`, `shadow_lane._status`, `router.build_training_data`, `memory_quality.*`, `navigator_shadow._load_navigator_events` | flagged only via the call-graph leg; they drop, but nothing writes the result back — recoverable, and already on the silent-drop census |
| append-only importer | 5 | `pack._import_lessons` / `_import_hypotheses` / `_import_skill_records`, `workspace_import.import_ledgers` | rows are appended to a *local* store; the source pack is never rewritten |
| already verbatim-preserve | 3 | `memory_ledger.compress_old_outcomes._drop_compressed`, `captains_log._maybe_rotate`, `gc_memory._gc_outcomes._trim` | the rewrite rejoins raw lines and never parses, so there is nothing to drop |
| giant orchestrator | 4 | `handle._handle_impl`, `heartbeat.heartbeat_loop`, `doctor.run_doctor`, `sheriff.check_project` | call-graph noise: a drop loop and a write exist hundreds of lines apart with no data path between them |

Two of those FPs are worth a sentence each, because "not destructive" is not the
same as "fine":

- `constraint._load_dynamic_constraints` and `knowledge_lens.load_standing_rules`
  strict-read inside a broad except, so one torn byte silently empties a
  **guardrail** set. Recoverable (the bytes stay), already on the census, and
  wanting the announced-read treatment when someone next touches those files.
- `captains_log._maybe_rotate` disables rotation on a torn byte, but warns, and
  never parses — so the log keeps growing loudly rather than losing data.

## Receipts

- 15 tests — 6 `test_doctor.py`, 5 `test_interrupt.py`, 4 `test_gc_memory.py` —
  each written against a live probe *before* the fix.
- `tests/mutation/interrupt_gc_doctor_preserve.json`: 19 file-derived
  must-detect mutations, **19/19 first pass**, including the deliberate-drop
  direction (a strand-and-carry that quietly turned the cleanup verb into a
  no-op would be a worse bug than the one it fixes).
- Census: 2 sites cleared. The scanner's RISK count falls 70 → 63.
- Full suite verified in an isolated `git worktree` at HEAD + this change only:
  **9622 passed, 1 skipped**. The shared checkout shows two unrelated reds from
  another session's in-flight `captains_log.py` chunk.

## Lesson

The scanner earned its keep by being *wrong 64 times out of 70* — because
reading 70 short functions took one session, and the three it surfaced were a
repair verb that destroyed data while reporting "0 removed", the kill switch's
delivery path, and a GC that had been reporting success while doing nothing.
A hint list that is 4% true is still worth reading when the 4% looks like that.

The corollary is the one the scanner's own docstring already makes: a "found 0"
from a tool nobody has falsified is worth nothing. This triage is the
falsification pass, and its durable output is the FP table above — so the next
person to run the scanner starts from 63 known-benign sites, not 63 unknowns.
