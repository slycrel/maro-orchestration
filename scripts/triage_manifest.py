#!/usr/bin/env python3
"""Render the destructive-rewrite triage manifest: every RISK site the
scanner reported on 2026-08-20, with the category it was triaged into.

Adversarial round 2026-08-20 (Experimentalist, accepted): the record shipped
eight aggregate categories with selected examples, which is not a
site -> classification mapping. "Every false positive has a reason written
down" was not reproducible from the artifact — you could not look up
`closure_verify._detect_next_ledger_gap` and find its verdict. This script
is the mapping, and `--check` fails when the scanner's live output no longer
matches it, so a new RISK site cannot quietly inherit "already triaged".

    python3 scripts/triage_manifest.py            # render the table
    python3 scripts/triage_manifest.py --check    # drift check (exit 1 on drift)
"""
from __future__ import annotations

import subprocess
import sys

# The verdict for every site in the 2026-08-20 scan. Keys are
# "<module>.py:<qualified name>" exactly as the scanner prints them.
CATEGORIES: dict[str, str] = {
    "markdown": "markdown/prose rewrite — no JSON parse; every line is carried "
                "and the 'drop' is a regex non-match, not a discard",
    "subprocess": "subprocess-output parser — the parsed text is pgrep/docker "
                  "stdout, not a durable store",
    "stream": "stream/LLM-output parser — nothing on disk is being rebuilt",
    "derived-index": "derived-index rebuild — the written file is generated FROM "
                     "the source, and regenerating is the repair",
    "read-only": "read-only loader — flagged via the call-graph leg; it drops, "
                 "but nothing writes the result back, so the bytes survive "
                 "(these are the silent-drop census's business, not this scan's)",
    "append-importer": "append-only importer — rows are appended to a LOCAL "
                       "store; the source artifact is never rewritten",
    "verbatim-preserve": "already verbatim-preserve — the rewrite rejoins raw "
                         "lines and never parses, so there is nothing to drop",
    "orchestrator": "call-graph noise — a drop loop and a write exist hundreds "
                    "of lines apart in one giant function, with no data path "
                    "between them",
    "REAL": "REAL DEFECT — fixed 2026-08-20, see "
            "docs/history/2026-08-20-destructive-rewrite-triage.md",
}

SITES: dict[str, str] = {}


def _add(cat: str, names: str) -> None:
    for n in names.split():
        assert n not in SITES, f"duplicate site {n}"
        SITES[n] = cat


_add("markdown", """
 boot_protocol.py:_read_completed_from_next boot_protocol.py:_load_dead_ends
 convo_miner.py:scan_maro_memory orch_items.py:parse_next orch_items.py:append_next_items
 thread_brain.py:_append_under pack.py:_append_conflicts_note
 pack.py:_append_conflicts_note.add_once pack.py:_review_section
 loop_report.py:_parse_reading_queue""")
_add("subprocess", """
 heartbeat.py:_is_interactive_session_active
 build_loop_runner.py:_worker_session_already_active
 container_exec.py:_reseed_probe worktree.py:_sanitize_untrusted_git""")
_add("stream", """
 llm.py:_parse_stream_json llm.py:_stream_events llm.py:_is_plain_missing_session_error
 orch_bridges.py:_tail_lines orch_bridges.py:_extract_session_result_from_text
 orch_bridges.py:command_execution_bridge orch_bridges.py:command_execution_bridge._execute
 orch_bridges.py:review_command_validation_bridge
 orch_bridges.py:review_command_validation_bridge._validate""")
_add("derived-index", """
 memory_ledger.py:_update_memory_index loop_report.py:_render_devlog_html
 portability.py:main""")
_add("read-only", """
 knowledge_lens.py:load_standing_rules knowledge_lens.py:load_hypotheses
 knowledge_lens.py:_lesson_texts_by_id knowledge_lens.py:search_decisions
 evolver_scans.py:_load_baselines evolver_scans.py:_load_dated_diagnoses
 evolver_scans.py:_record_suggestion_outcomes graduation.py:scan_candidates
 graduation.py:_already_proposed graduation.py:verify_graduation_rules
 shadow_lane.py:_today_ledger_count shadow_lane.py:_status
 router.py:build_training_data router.py:_count_skill_stats
 memory_quality.py:_load_corpus_from_workspace memory_quality.py:_load_paraphrase_queries
 navigator_shadow.py:_load_navigator_events memory_jsonl.py:_replay
 constraint.py:_load_dynamic_constraints closure_verify.py:_detect_next_ledger_gap
 handle.py:_load_user_config knowledge_bridge.py:_extract_llm
 knowledge_bridge.py:upsert_knowledge_from_candidate run_curation.py:_strip_result_preamble
 run_curation.py:promote_skills_lite pack.py:_read_jsonl_rows""")
_add("append-importer", """
 pack.py:_import_rules_as_hypotheses pack.py:_import_hypotheses pack.py:_import_lessons
 pack.py:_import_skill_records workspace_import.py:import_ledgers""")
_add("verbatim-preserve", """
 memory_ledger.py:compress_old_outcomes._drop_compressed captains_log.py:_maybe_rotate
 gc_memory.py:_gc_outcomes._trim""")
_add("orchestrator", """
 handle.py:_handle_impl heartbeat.py:heartbeat_loop doctor.py:run_doctor
 sheriff.py:check_project""")
_add("REAL", """
 doctor.py:cleanup_workspace_skills interrupt.py:poll interrupt.py:clear
 interrupt.py:poll._mark_applied interrupt.py:clear._mark_applied gc_memory.py:_gc_outcomes""")

# Sites the 2026-08-20 fixes turned from RISK into OK. A live scan will not
# report these any more; they stay here because the manifest is the record of
# what was triaged, not a snapshot of the current scan.
FIXED = {n for n, c in SITES.items() if c == "REAL"} | {
    "doctor.py:run_doctor", "gc_memory.py:_gc_outcomes._trim"}


def _live_sites() -> set[str]:
    out = subprocess.run([sys.executable, "scripts/scan_destructive_rewrites.py"],
                         capture_output=True, text=True,
                         env={"PYTHONPATH": "src", "PATH": "/usr/bin:/bin"}).stdout
    sites = set()
    for line in out.splitlines():
        if line.startswith("RISK"):
            _, loc, name = line.split(None, 2)
            sites.add(f"{loc.rsplit(':', 1)[0]}:{name.strip().rstrip('()')}")
    return sites


def main() -> int:
    if "--check" in sys.argv:
        live = _live_sites()
        untriaged = sorted(live - set(SITES))
        gone = sorted((set(SITES) - FIXED) - live)
        for n in untriaged:
            print(f"UNTRIAGED  {n} — new RISK site, not in the manifest")
        for n in gone:
            print(f"STALE      {n} — manifest lists it but the scanner does not")
        if untriaged or gone:
            print(f"\n{len(untriaged)} untriaged, {len(gone)} stale")
            return 1
        print(f"manifest matches the live scan: {len(live)} RISK sites, all triaged")
        return 0
    print(f"{len(SITES)} sites triaged 2026-08-20 "
          f"({sum(1 for c in SITES.values() if c == 'REAL')} real, "
          f"{sum(1 for c in SITES.values() if c != 'REAL')} false positives)\n")
    for cat in CATEGORIES:
        members = sorted(n for n, c in SITES.items() if c == cat)
        print(f"## {cat} ({len(members)})\n   {CATEGORIES[cat]}")
        for n in members:
            print(f"   - {n}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
