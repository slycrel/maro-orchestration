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
    "read-only": "read-only loader or pure predicate — flagged via the "
                 "call-graph leg; it drops or merely counts, "
                 "but nothing writes the result back, so the bytes survive "
                 "(these are the silent-drop census's business, not this scan's)",
    "append-importer": "append-only importer — rows are appended to a LOCAL "
                       "store; the source artifact is never rewritten",
    "verbatim-preserve": "already verbatim-preserve — the rewrite rejoins raw "
                         "lines and never parses, so there is nothing to drop",
    "orchestrator": "call-graph noise — a drop loop and a write exist hundreds "
                    "of lines apart in one giant function, with no data path "
                    "between them",
    "clean-then-raw": "the scan parses every line with `loads_clean`, and the "
                      "bare `json.loads` re-parses ONE line that scan already "
                      "proved taint-free — the r5 verdict rule cannot see that "
                      "ordering, and the preserve tests already cover these",
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
 loop_report.py:_parse_reading_queue
 playbook.py:_replace_alarm playbook.py:_expire_text playbook.py:_dedup_text""")
_add("subprocess", """
 llm.py:_run_subprocess_safe
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
 memory_sqlite.py:_catch_up
 memory_ledger.py:_update_memory_index loop_report.py:_render_devlog_html
 portability.py:main""")
_add("read-only", """
 metrics.py:_reverse_readline convo_miner.py:scan_session_logs
 correspondence.py:render_transcript
 playbook.py:parse_entries playbook.py:_valid_compression
 knowledge_lens.py:load_standing_rules knowledge_lens.py:load_hypotheses
 knowledge_lens.py:_lesson_texts_by_id knowledge_lens.py:search_decisions
 evolver_scans.py:_load_baselines evolver_scans.py:_load_dated_diagnoses
 evolver_scans.py:_record_suggestion_outcomes graduation.py:scan_candidates
 graduation.py:_already_proposed graduation.py:verify_graduation_rules
 shadow_lane.py:_today_ledger_count shadow_lane.py:_status
 router.py:_count_skill_stats
 memory_quality.py:_load_corpus_from_workspace memory_quality.py:_load_paraphrase_queries
 navigator_shadow.py:_load_navigator_events memory_jsonl.py:_replay
 jsonl_utils.py:_iter_lines_reverse
 constraint.py:_load_dynamic_constraints closure_verify.py:_detect_next_ledger_gap
 handle.py:_load_user_config knowledge_bridge.py:_extract_llm
 knowledge_bridge.py:upsert_knowledge_from_candidate run_curation.py:_strip_result_preamble
 run_curation.py:promote_skills_lite pack.py:_read_jsonl_rows""")
_add("append-importer", """
 pack.py:_import_rules_as_hypotheses pack.py:_import_hypotheses pack.py:_import_lessons
 pack.py:_import_skill_records workspace_import.py:import_ledgers""")
_add("verbatim-preserve", """
 memory_ledger.py:compress_old_outcomes._drop_compressed captains_log.py:_maybe_rotate
 gc_memory.py:_gc_outcomes._classify""")
_add("clean-then-raw", """
 memory_ledger.py:stamp_outcome_verdict memory_ledger.py:stamp_outcome_verdict._stamp
 memory_ledger.py:annotate_outcome_lessons
 memory_ledger.py:annotate_outcome_lessons._stamp""")
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
    "gc_memory.py:_gc_outcomes._classify"}
# `doctor.py:run_doctor` used to be listed here too, and adversarial r5 took
# it back out — deliberately, with the reasoning written down rather than the
# entry quietly deleted. It is RISK again, but nothing in it regressed: r5
# refuted the OK verdict itself (a function got OK for merely MENTIONING
# `loads_clean` anywhere in its body, even while parsing every line with bare
# `json.loads`), so the verdict that put run_doctor in this set was the weak
# one. Its actual triage — "orchestrator", a drop loop and a write hundreds
# of lines apart in one giant function with no data path between them — was
# read by hand on 2026-08-20 and is unchanged. A gate that cannot tell "the
# code regressed" from "the rule got stricter" would have reported this as a
# REGRESSION, which is why the resolution is a hand re-read and a recorded
# reason, not a silently widened exemption.
# `router.py:build_training_data` was listed under "read-only" until r15
# (2026-08-21). Its skills side moved from read_text + bare json.loads onto
# read_jsonl_announced + validate_skill_row, so the function no longer frames
# or parses lines itself — the surface is still watched, inside the shared
# announced reader that now owns the framing. Its triage was correct and the
# fix was about WHAT it trains on, not about a rewrite; removed rather than
# marked FIXED because the scanner cannot see a site that no longer exists.
# `gc_memory.py:_gc_outcomes._trim` used to be listed here. The `vanished`
# leg below caught it on its first run — `_trim` no longer frames lines at
# all (it calls `_classify`, which does, and which the scanner reports as its
# own site). So the surface is still watched, under the name that now owns
# the framing. Recorded rather than silently swapped: this is the exact
# reasoning the check exists to force someone to do out loud, instead of a
# fixed site quietly leaving the field of view.


# Sites whose FRAMING moved into a nested scope, and the inner site that
# owns it now. Adversarial r10 made the scanner lexical throughout — the
# parse proof, the binding census and the framing test all read one scope
# — because a clean call inside an uncalled nested helper was certifying
# its outer destructive rewrite as OK. The cost is that nine functions
# whose only `split("\n")` lives in a `locked_rmw` closure stopped being
# reported under the OUTER name.
#
# This is the `_trim` situation from r3, at nine times the size, and it
# gets the same answer: a surface is still watched when the scanner can
# still SEE it, under whatever name owns the framing. The exemption is
# proof-carrying — the `blind` leg below re-checks that each named twin is
# in the live scan, so if a twin ever leaves too, the gate fires instead of
# reporting green. A blanket "these are allowed to be missing" is exactly
# the one-directional exemption `regressed` and `vanished` were written
# against.
#
# `llm.py:_run_subprocess_safe` is the one entry with no twin: its framing
# is in `_drain_new_events`, which the scanner does not report because
# nothing writes what it returns. Hand-re-read 2026-08-20 and unchanged in
# its triage — it parses `codex`/`claude` NDJSON stdout, and no durable
# store is being rebuilt. Recorded as a None rather than deleted, so the
# row that says "this was looked at" survives the site leaving the scan.
MOVED: "dict[str, str | None]" = {
    "llm.py:_run_subprocess_safe": None,
    "memory_ledger.py:annotate_outcome_lessons":
        "memory_ledger.py:annotate_outcome_lessons._stamp",
    "memory_ledger.py:stamp_outcome_verdict":
        "memory_ledger.py:stamp_outcome_verdict._stamp",
    "orch_bridges.py:command_execution_bridge":
        "orch_bridges.py:command_execution_bridge._execute",
    "orch_bridges.py:review_command_validation_bridge":
        "orch_bridges.py:review_command_validation_bridge._validate",
    "pack.py:_append_conflicts_note": "pack.py:_append_conflicts_note.add_once",
    "gc_memory.py:_gc_outcomes": "gc_memory.py:_gc_outcomes._classify",
    "interrupt.py:poll": "interrupt.py:poll._mark_applied",
    "interrupt.py:clear": "interrupt.py:clear._mark_applied",
}


def _scan() -> "tuple[set[str], set[str]]":
    """(RISK sites, EVERY site the scanner reported — RISK and OK alike)."""
    out = subprocess.run([sys.executable, "scripts/scan_destructive_rewrites.py"],
                         capture_output=True, text=True,
                         env={"PYTHONPATH": "src", "PATH": "/usr/bin:/bin"}).stdout
    risk, seen = set(), set()
    for line in out.splitlines():
        if not (line.startswith("RISK") or line.startswith("OK")):
            continue
        verdict, loc, name = line.split(None, 2)
        site = f"{loc.rsplit(':', 1)[0]}:{name.strip().rstrip('()')}"
        seen.add(site)
        if verdict == "RISK":
            risk.add(site)
    return risk, seen


def _live_sites() -> set[str]:
    return _scan()[0]


def compare(live: "set[str]", seen: "set[str] | None" = None) -> \
        "tuple[list, list, list, list, list, list]":
    """(untriaged, stale, regressed, vanished, blind, resurfaced) legs.

    Pure, so the drift gate itself can be tested against a synthetic live set
    — the runner's own must-detect rule applied to the runner.

      untriaged  a RISK site the manifest has never classified
      stale      a manifest site the scanner no longer reports (and which was
                 not one of the 2026-08-20 fixes)
      regressed  a site the 2026-08-20 fixes made OK that is RISK again
      vanished   a FIXED site the scanner no longer reports AT ALL — not as
                 RISK, not as OK

    `regressed` exists because of adversarial r2 (2026-08-20, 2 lenses,
    verified): a resurfaced FIXED site was neither untriaged (it is in SITES)
    nor stale (FIXED exempted it), so re-introducing the exact destructive
    rewrite this arc removed passed the gate silently. A one-directional
    exemption is not a gate.

    `vanished` exists because of adversarial r3, which found the failure mode
    `regressed` could not see: a fixed site does not have to turn RISK to
    stop being watched — it can simply leave the scanner's field of view, and
    then `live & FIXED` is empty forever and the gate reports green. That is
    exactly what the arc's own `splitlines()` -> `split("\n")` conversion did
    to all six of its fixed sites. Watching a site means being able to SEE
    it; `seen` is every site the scanner reported, at any verdict. Pass None
    to skip the check (for callers testing the other three legs).

    `blind` exists because of adversarial r10, and because `MOVED` would
    otherwise be a third one-directional exemption. A moved site is
    excused from `stale`/`vanished` only while the inner site named as its
    new home is itself in the live scan; if that twin disappears, the
    surface is unwatched and this leg says so. The exemption has to keep
    paying for itself.

    `resurfaced` exists because of adversarial r11, which found the other
    direction `blind` cannot see: a MOVED site is expected ABSENT from the
    scan, so if its OUTER name comes back as RISK — someone put framing
    back in the outer scope — it is neither untriaged (it is in SITES),
    nor stale (MOVED exempts it), nor blind (the twin is still there),
    and its years-old triage label silently absorbs materially new code.
    A moved site reappearing means the move is no longer true; the entry
    must be re-triaged, not inherited. This also covers the one MOVED
    entry with no twin (`llm.py:_run_subprocess_safe`).
    """
    untriaged = sorted(live - set(SITES))
    stale = sorted((set(SITES) - FIXED - set(MOVED)) - live)
    regressed = sorted(live & FIXED)
    vanished = sorted(FIXED - seen - set(MOVED)) if seen is not None else []
    blind = sorted(t for t in MOVED.values()
                   if t is not None and t not in seen) \
        if seen is not None else []
    # `seen`, not `live` (adversarial r12, Skeptic, probed): the move
    # premise — "the outer name is expected ABSENT from the scan" — is
    # falsified by the outer name coming back at ANY verdict. An OK
    # resurfacer means framing returned to the outer scope with a
    # superficially clean parse beside it, which is more suspicious, not
    # less. Callers that pass seen=None (three-leg tests) fall back to
    # live, the only visibility they offered.
    resurfaced = sorted((seen if seen is not None else live) & set(MOVED))
    return untriaged, stale, regressed, vanished, blind, resurfaced


def main() -> int:
    if "--check" in sys.argv:
        live, seen = _scan()
        (untriaged, stale, regressed, vanished, blind,
         resurfaced) = compare(live, seen)
        for n in untriaged:
            print(f"UNTRIAGED  {n} — new RISK site, not in the manifest")
        for n in stale:
            print(f"STALE      {n} — manifest lists it but the scanner does not")
        for n in regressed:
            print(f"REGRESSED  {n} — fixed on 2026-08-20, destructive again")
        for n in vanished:
            print(f"VANISHED   {n} — a fixed site the scanner can no longer "
                  f"see at all; the gate is blind to it, not clean")
        for n in blind:
            print(f"BLIND      {n} — a moved site's new home is not in the "
                  f"scan either; the MOVED exemption covers nothing")
        for n in resurfaced:
            print(f"RESURFACED {n} — a moved site is back in the scan under "
                  f"its outer name; the move is no longer true, re-triage it")
        if untriaged or stale or regressed or vanished or blind or resurfaced:
            print(f"\n{len(untriaged)} untriaged, {len(stale)} stale, "
                  f"{len(regressed)} regressed, {len(vanished)} vanished, "
                  f"{len(blind)} blind, {len(resurfaced)} resurfaced")
            return 1
        # len(live), not a count derived from the manifest: with FIXED and
        # MOVED both subtracted the arithmetic version drifted from what
        # the scanner actually reported (78 vs 72), and a gate that prints
        # a number nobody can reproduce from its own output is the shape
        # this whole script exists to replace.
        print(f"manifest matches the live scan: {len(live)} RISK sites, "
              f"all triaged ({len(MOVED)} watched under a moved name)")
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
