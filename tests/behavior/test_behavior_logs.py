"""Behavior: append-only logs — playbook, captain's log, events feed.

Contract citations: docs/CONTRACTS.md B10 (playbook grammar), B8
(captains-log rotation + per-run slice), B9 (events.jsonl line discipline).
"""

import json

from harness import (
    EVENTS_LINE_BUDGET,
    assert_events_line_discipline,
    read_jsonl,
    workspace,
)


# ---------------------------------------------------------------------------
# (8) Playbook append grammar — B10
# ---------------------------------------------------------------------------

def test_playbook_entry_grammar_section_anchoring_and_dedup():
    from playbook import append_to_playbook

    path = workspace() / "playbook.md"

    # A hostile multi-line entry: embedded newlines could spoof a section
    # header. B10: writers collapse them — an entry is exactly ONE line
    # starting "- ".
    append_to_playbook(
        "Prefer scripted adapters\n## Spoofed Section\nfor tests",
        section="Learned", source="behavior-suite",
    )
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    # The grammar is LINE-anchored: the spoofed header may survive as inline
    # text, but no LINE may start `## Spoofed Section`.
    assert not any(l.startswith("## Spoofed Section") for l in lines), (
        "embedded newlines must be collapsed — no header spoofing (B10)"
    )
    entry_lines = [l for l in lines if "Prefer scripted adapters" in l]
    assert len(entry_lines) == 1
    assert entry_lines[0].startswith("- "), "an entry is one '- ' line (B10)"

    # Section anchoring: the entry sits under its `## <name>` header.
    learned_idx = lines.index("## Learned")
    entry_idx = lines.index(entry_lines[0])
    assert entry_idx > learned_idx
    between = lines[learned_idx + 1:entry_idx]
    assert not any(l.startswith("## ") for l in between), (
        "entry must land inside its own section (B10)"
    )

    # Dedup on exact core-text match: appending again adds nothing.
    append_to_playbook(
        "Prefer scripted adapters ## Spoofed Section for tests",
        section="Learned", source="behavior-suite",
    )
    text2 = path.read_text(encoding="utf-8")
    assert text2.count("Prefer scripted adapters") == 1, "appends dedup (B10)"

    # Length cap: entries are clipped to ~500 chars.
    append_to_playbook("x" * 900, section="Learned")
    long_lines = [l for l in path.read_text(encoding="utf-8").splitlines()
                  if l.startswith("- x")]
    assert long_lines and len(long_lines[0]) < 600, "entries clip near 500 (B10)"

    # Unknown sections are legal: appending under a new section creates it.
    append_to_playbook("A brand new insight", section="Behavior Suite")
    text3 = path.read_text(encoding="utf-8")
    assert "## Behavior Suite" in text3
    assert "- A brand new insight" in text3


def test_playbook_alarm_key_replaces_in_place():
    """B10: the `alarm <key>` marker form is REPLACED in place on re-fire,
    never appended beside itself."""
    from playbook import append_to_playbook

    path = workspace() / "playbook.md"
    append_to_playbook("Calibration drift at 0.3", section="Alarms",
                       source="calibration", key="calibration:drift")
    append_to_playbook("Calibration drift at 0.5", section="Alarms",
                       source="calibration", key="calibration:drift")
    text = path.read_text(encoding="utf-8")
    assert "Calibration drift at 0.5" in text, "re-fire must land"
    assert "Calibration drift at 0.3" not in text, (
        "an alarm re-fire replaces the prior entry in place (B10)"
    )
    assert text.count("alarm calibration:drift") == 1


# ---------------------------------------------------------------------------
# (9) Captains-log rotation + per-run byte-offset slice — B8
# ---------------------------------------------------------------------------

def test_captains_log_rotation_keeps_every_row():
    """B8: size-gated rotation moves head rows to a timestamped archive
    sibling; nothing is deleted; live + archives together hold every row."""
    ws = workspace()
    # Rotation thresholds come from workspace config (B1 tier).
    (ws / "config.yml").write_text(
        "captains_log:\n  rotate_mb: 0.001\n  rotate_keep: 5\n",
        encoding="utf-8",
    )
    from captains_log import log_event

    subjects = [f"rotation-probe-{i:03d}" for i in range(40)]
    for s in subjects:
        log_event("note", s, "padding " * 10)

    live = ws / "memory" / "captains_log.jsonl"
    archives = sorted(live.parent.glob("captains_log.*.jsonl"))
    assert archives, "size gate must have produced a rotation archive (B8)"

    all_rows = read_jsonl(live)
    for arch in archives:
        all_rows.extend(read_jsonl(arch))
    seen = {r.get("subject") for r in all_rows}
    for s in subjects:
        assert s in seen, f"rotation lost row {s} — rotation never deletes (B8)"
    # The retained live tail honors rotate_keep (plus the rotation audit
    # row(s) appended after the swap).
    assert len(read_jsonl(live)) <= 10


def test_captains_log_slice_lands_in_run_dir_and_filters_by_handle_id():
    """B8: the per-run slice is carved by BYTE OFFSET between run open and
    close — it may contain other runs' rows, so readers filter by
    handle_id; the slice must contain this run's rows."""
    from captains_log import log_event
    from runs import close_run, open_run

    hid = "0ddba115"
    open_run(hid, prompt="slice probe goal", lane="agenda")
    # Rows emitted while the run-dir pin is active carry handle_id (B8).
    log_event("note", "slice-probe-a", "first row in window")
    log_event("note", "slice-probe-b", "second row in window")
    close_run(hid, status="done")

    from harness import run_dir_for
    rd = run_dir_for(hid)
    slice_path = rd / "build" / "captains_log_slice.jsonl"
    assert slice_path.exists(), "close must carve the log slice (B8)"
    rows = read_jsonl(slice_path)
    mine = [r for r in rows if r.get("handle_id") == hid]
    subjects = {r.get("subject") for r in mine}
    assert {"slice-probe-a", "slice-probe-b"} <= subjects, (
        "the slice window must contain the run's own rows, joinable by "
        "handle_id (B8)"
    )


# ---------------------------------------------------------------------------
# (10) events.jsonl line discipline — B9
# ---------------------------------------------------------------------------

def test_events_line_discipline_under_hostile_fields():
    """B9: every emitted line is one valid JSON object ≤4096 bytes; hostile
    field values are capped/coerced/dropped — an event is never silently
    lost for a bad field, and NaN/Infinity never reach the wire."""
    from observe import write_event

    class HostileStr:
        def __str__(self):
            raise RuntimeError("str() bomb")

    events = workspace() / "memory" / "events.jsonl"

    assert write_event("behavior_probe", goal="plain", status="ok") is True
    # Oversize multibyte strings: caps are BYTE-aware after encoding.
    write_event("behavior_probe", goal="é" * 5000, step="漢" * 5000,
                detail="🎯" * 5000)
    # str()-hostile value: survives, never dropped silently.
    write_event("behavior_probe", goal=HostileStr(), detail="after hostile")
    # Numeric hostility: a >4300-digit int would make json.dumps raise;
    # non-finite floats are not JSON. Both must be DROPPED and named.
    write_event("behavior_probe", tokens_in=10 ** 5000,
                elapsed_ms=float("inf"), detail="numeric hostility")
    # A huge tool_pathologies payload rides the shed ladder.
    write_event("behavior_probe",
                tool_pathologies=[{"detail": "p" * 9000}] * 10,
                detail="pathology flood")

    n = assert_events_line_discipline(events)
    assert n >= 5, "every event must land as a row (possibly truncated) (B9)"

    rows = read_jsonl(events)
    probe_rows = [r for r in rows
                  if r.get("event_type") == "behavior_probe"
                  or r.get("orig_event_type") == "behavior_probe"]
    assert len(probe_rows) == 5, "no event may be silently dropped (B9)"

    # Invalid numerics: absent from the row, named in invalid_fields.
    numeric_row = next(r for r in probe_rows
                       if r.get("detail", "").startswith("numeric"))
    assert "tokens_in" not in numeric_row
    assert "elapsed_ms" not in numeric_row
    assert "tokens_in" in numeric_row.get("invalid_fields", [])
    assert "elapsed_ms" in numeric_row.get("invalid_fields", [])

    # No NaN/Infinity tokens anywhere on the wire (B2/B9).
    raw = events.read_text(encoding="utf-8")
    for tok in ("NaN", "Infinity"):
        assert tok not in raw, f"bare {tok} must never reach the wire (B2)"

    # Every line respects the single-write byte budget (checked above via
    # assert_events_line_discipline against EVENTS_LINE_BUDGET).
    assert EVENTS_LINE_BUDGET == 4096
