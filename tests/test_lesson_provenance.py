"""Provenance gate tests (2026-07-29) — pinned on the db37d525 incident.

The four REAL lessons minted from the Poe tire-dispatch and diagnosis runs
are the classifier's ground truth: db37d525 (instruction-obedience
generalization, contaminated) must quarantine; bae0851f / 00d4fc7e /
08fed9e8 (outcome-shaped, kept) must stay clean. The end-to-end tripwire
runs a scaffolding-laden goal through the real record path and asserts the
minted lesson lands quarantined while a clean sibling doesn't.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import lesson_provenance
from lesson_provenance import (
    MINTED_FROM_OUTCOME,
    MINTED_FROM_PROMPT,
    classify_lesson_provenance,
)
from knowledge_web import (
    MemoryTier,
    PROVISIONAL_ENTRY_SCORE,
    _tiered_lessons_path,
    inject_tiered_lessons,
    load_tiered_lessons,
    promote_lesson,
    query_lessons,
    record_tiered_lesson,
    set_lesson_minted_from,
)
from memory_ledger import (
    _store_lesson,
    load_lessons,
    set_flat_lesson_minted_from,
)


def _setup(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    return tmp_path


# The real minted lessons from the 2026-07-27/28 incident, verbatim.
DB37D525 = (
    "When a prompt explicitly says 'do not escalate/stop because X is "
    "inaccessible' and gives recovered facts as sufficient context, treat "
    "that as a hard constraint on the final output, not just on the "
    "planning phase: the finished deliverable must still contain concrete, "
    "purchase-ready conclusions rather than reverting to hedged 'more "
    "research needed' framing once the blocked resource is mentioned again."
)
BAE0851F = (
    "When a task specifies an exact output contract (e.g., '3 tiers, each "
    "with brand/model, size, load index, real price, seller link'), mark it "
    "done only after checking each required field is concretely present in "
    "the deliverable — not just that a file was written. A run can complete "
    "its internal steps and still fail the goal if the final artifact is "
    "vague, hedged, or missing required specifics like real prices/links."
)
LESSON_00D4FC7E = (
    "[planning-agenda] Diagnosis tasks that explicitly list observed "
    "failure symptoms (e.g., 'escalated instead of attempting recoverable "
    "retrieval') produce more decision-grade output when the memo maps each "
    "symptom to a specific repo mechanism (file/symbol) rather than "
    "treating symptoms as generic agent behavior."
)
LESSON_08FED9E8 = (
    "[verification-diagnosis] When comparing a repo's behavior against an "
    "external reference pattern, run an adversarial re-check pass at the "
    "end that re-verifies each cited file/symbol claim against the actual "
    "source tree before finalizing — catches stale or invented citations "
    "that would otherwise undermine a decision-grade memo's credibility."
)
TIRE_GOAL = (
    "Complete this practical tire-buying research request. Do NOT escalate "
    "or stop merely because a linked page cannot be accessed; the recovered "
    "facts below are sufficient context."
)


# ---------------------------------------------------------------------------
# Classifier pins — the incident's ground truth
# ---------------------------------------------------------------------------

def test_classifier_quarantines_db37d525():
    assert classify_lesson_provenance(DB37D525, TIRE_GOAL) == MINTED_FROM_PROMPT


def test_classifier_quarantines_db37d525_without_goal():
    # The prompt-authority signal fires on lesson text alone — a truncated
    # source_goal must not hide the contamination.
    assert classify_lesson_provenance(DB37D525) == MINTED_FROM_PROMPT


def test_classifier_keeps_bae0851f_clean():
    # Outcome-shaped contract-verification advice; quotes the goal but does
    # not generalize obedience to it.
    assert classify_lesson_provenance(BAE0851F, TIRE_GOAL) == MINTED_FROM_OUTCOME


def test_classifier_keeps_meta_prompting_lesson_clean():
    # 00d4fc7e is ABOUT how to phrase diagnosis tasks (outcome-derived
    # meta-knowledge) — the gate must discriminate, not blanket-block
    # everything that mentions tasks or prompting.
    assert classify_lesson_provenance(LESSON_00D4FC7E) == MINTED_FROM_OUTCOME


def test_classifier_keeps_adversarial_recheck_lesson_clean():
    assert classify_lesson_provenance(LESSON_08FED9E8) == MINTED_FROM_OUTCOME


def test_classifier_obedience_signal():
    assert classify_lesson_provenance(
        "Always treat it as a hard constraint and never re-plan around it."
    ) == MINTED_FROM_PROMPT


def test_classifier_scaffolding_echo_needs_goal_match():
    lesson = "Do not escalate when a resource is missing; keep working."
    scaffolded_goal = "Fix the report. Do not escalate if the page is blocked."
    clean_goal = "Fix the report generator's missing-page handling."
    assert classify_lesson_provenance(lesson, scaffolded_goal) == MINTED_FROM_PROMPT
    # The same echo without a scaffolding-laden goal could be a genuine
    # recovery lesson — it stays clean.
    assert classify_lesson_provenance(lesson, clean_goal) == MINTED_FROM_OUTCOME


def test_classifier_domain_hard_constraint_stays_clean():
    # Domain facts phrased with "hard constraint" (no pronoun-obedience, no
    # prompt authority) are legitimate lessons.
    assert classify_lesson_provenance(
        "Rate limits are a hard constraint on parallel fetches; batch them."
    ) == MINTED_FROM_OUTCOME


# ---------------------------------------------------------------------------
# Tiered store: mint-time stamping and quarantine surfaces
# ---------------------------------------------------------------------------

def test_record_tiered_lesson_stamps_prompt_derived(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    tl = record_tiered_lesson(DB37D525, "agenda", "done", source_goal=TIRE_GOAL[:120])
    assert tl.minted_from == MINTED_FROM_PROMPT
    stored = load_tiered_lessons(tier=MemoryTier.MEDIUM, limit=None, raw=True)
    assert stored[0].minted_from == MINTED_FROM_PROMPT
    # Quarantined rows enter at the reduced (provisional) base score.
    assert stored[0].score <= PROVISIONAL_ENTRY_SCORE + 0.31  # base + max novelty


def test_record_tiered_lesson_stamps_outcome_derived(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    tl = record_tiered_lesson(LESSON_08FED9E8, "agenda", "done", source_goal="diagnosis run")
    assert tl.minted_from == MINTED_FROM_OUTCOME


def test_quarantined_excluded_from_query_lessons(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    record_tiered_lesson(DB37D525, "agenda", "done", source_goal=TIRE_GOAL[:120])
    record_tiered_lesson(LESSON_08FED9E8, "agenda", "done", source_goal="diagnosis")
    hits = query_lessons("prompt explicitly says do not escalate", task_type="agenda")
    assert all(t.minted_from != MINTED_FROM_PROMPT for t in hits)
    # Readout opt-in still sees it.
    hits_all = query_lessons("prompt explicitly says do not escalate",
                             task_type="agenda", include_quarantined=True)
    assert any(t.minted_from == MINTED_FROM_PROMPT for t in hits_all)


def test_quarantined_excluded_from_inject_tiered_lessons(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    record_tiered_lesson(DB37D525, "agenda", "done", source_goal=TIRE_GOAL[:120])
    injected = inject_tiered_lessons(task_type="agenda")
    assert "do not escalate/stop" not in injected


def test_quarantined_never_promotes(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    tl = record_tiered_lesson(DB37D525, "agenda", "done", source_goal=TIRE_GOAL[:120])
    # Force promotion eligibility on the stored row, flag intact.
    path = _tiered_lessons_path(MemoryTier.MEDIUM)
    rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    for r in rows:
        r["score"] = 1.0
        r["sessions_validated"] = 5
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    assert promote_lesson(tl.lesson_id) is False
    assert load_tiered_lessons(tier=MemoryTier.LONG, limit=None) == []


def test_prompt_derived_rerecord_does_not_reinforce(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    clean = record_tiered_lesson(LESSON_08FED9E8, "agenda", "done", source_goal="run A")
    again = record_tiered_lesson(LESSON_08FED9E8, "agenda", "done",
                                 source_goal="run B", minted_from="prompt")
    assert again.lesson_id == clean.lesson_id
    stored = load_tiered_lessons(tier=MemoryTier.MEDIUM, limit=None, raw=True)
    assert stored[0].times_reinforced == 0
    assert stored[0].sessions_validated == 0


def test_outcome_rerecord_clears_quarantine(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    tl = record_tiered_lesson(DB37D525, "agenda", "done", source_goal=TIRE_GOAL[:120])
    assert tl.minted_from == MINTED_FROM_PROMPT
    # Same text independently derived from an actual outcome earns
    # citizenship — explicit outcome stamp models a clean extraction context.
    record_tiered_lesson(DB37D525, "agenda", "done",
                         source_goal="clean run", minted_from="outcome")
    stored = load_tiered_lessons(tier=MemoryTier.MEDIUM, limit=None, raw=True)
    assert stored[0].minted_from == MINTED_FROM_OUTCOME


def test_set_lesson_minted_from_roundtrip(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    tl = record_tiered_lesson(LESSON_08FED9E8, "agenda", "done", source_goal="run")
    assert set_lesson_minted_from(tl.lesson_id, "prompt", reason="test") is True
    stored = load_tiered_lessons(tier=MemoryTier.MEDIUM, limit=None, raw=True)
    assert stored[0].minted_from == MINTED_FROM_PROMPT
    assert set_lesson_minted_from(tl.lesson_id, "outcome") is True
    stored = load_tiered_lessons(tier=MemoryTier.MEDIUM, limit=None, raw=True)
    assert stored[0].minted_from == MINTED_FROM_OUTCOME
    assert set_lesson_minted_from("nope1234", "prompt") is False


def test_killswitch_disables_classification(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(lesson_provenance, "provenance_gate_enabled", lambda: False)
    tl = record_tiered_lesson(DB37D525, "agenda", "done", source_goal=TIRE_GOAL[:120])
    assert tl.minted_from == ""


def test_killswitch_does_not_rearm_existing_stamps(monkeypatch, tmp_path):
    # The flag gates classification only — a stamped row stays excluded even
    # with the classifier off (off-switch is for a misfiring classifier, not
    # for re-injecting known-bad rows).
    _setup(monkeypatch, tmp_path)
    record_tiered_lesson(DB37D525, "agenda", "done", source_goal=TIRE_GOAL[:120])
    monkeypatch.setattr(lesson_provenance, "provenance_gate_enabled", lambda: False)
    injected = inject_tiered_lessons(task_type="agenda")
    assert "do not escalate/stop" not in injected


# ---------------------------------------------------------------------------
# Flat store: stamping, filtering, maintenance verb
# ---------------------------------------------------------------------------

def test_store_lesson_stamps_and_load_lessons_filters(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    l = _store_lesson("agenda", "done", DB37D525, source_goal=TIRE_GOAL)
    assert l.minted_from == MINTED_FROM_PROMPT
    _store_lesson("agenda", "done", LESSON_08FED9E8, source_goal="diagnosis run")
    visible = load_lessons(task_type="agenda", limit=10)
    assert all(x.minted_from != MINTED_FROM_PROMPT for x in visible)
    assert len(visible) == 1
    everything = load_lessons(task_type="agenda", limit=10, include_quarantined=True)
    assert len(everything) == 2


def test_flat_row_omits_empty_minted_from(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(lesson_provenance, "provenance_gate_enabled", lambda: False)
    _store_lesson("agenda", "done", "A plain lesson.", source_goal="goal")
    row = json.loads((tmp_path / "memory" / "lessons.jsonl").read_text().splitlines()[0])
    assert "minted_from" not in row


def test_flat_prompt_rerecord_does_not_reinforce(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    first = _store_lesson("agenda", "done", LESSON_08FED9E8, source_goal="run A")
    again = _store_lesson("agenda", "done", LESSON_08FED9E8,
                          source_goal="run B", minted_from="prompt")
    assert again.lesson_id == first.lesson_id
    stored = load_lessons(task_type="agenda", limit=10, include_quarantined=True)
    assert stored[0].times_reinforced == 0


def test_flat_outcome_rerecord_clears_quarantine(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    _store_lesson("agenda", "done", DB37D525, source_goal=TIRE_GOAL)
    _store_lesson("agenda", "done", DB37D525,
                  source_goal="clean run", minted_from="outcome")
    stored = load_lessons(task_type="agenda", limit=10, include_quarantined=True)
    assert stored[0].minted_from == MINTED_FROM_OUTCOME


def test_set_flat_lesson_minted_from_roundtrip(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    l = _store_lesson("agenda", "done", LESSON_08FED9E8, source_goal="run")
    assert set_flat_lesson_minted_from(l.lesson_id, "prompt", reason="test") is True
    stored = load_lessons(task_type="agenda", limit=10, include_quarantined=True)
    assert stored[0].minted_from == MINTED_FROM_PROMPT
    assert load_lessons(task_type="agenda", limit=10) == []
    assert set_flat_lesson_minted_from(l.lesson_id, "outcome") is True
    assert len(load_lessons(task_type="agenda", limit=10)) == 1
    assert set_flat_lesson_minted_from("nope1234", "prompt") is False


# ---------------------------------------------------------------------------
# End-to-end tripwire: the incident, replayed through the real mint path
# ---------------------------------------------------------------------------

def test_tripwire_scaffolded_goal_extraction_lands_quarantined(monkeypatch, tmp_path):
    """A scaffolding-laden dispatch goal whose extraction generalizes the
    scaffolding must land quarantined in BOTH stores; its outcome-shaped
    sibling from the same run keeps full citizenship. This is the db37d525
    incident as a permanent tripwire."""
    _setup(monkeypatch, tmp_path)
    from memory_ledger import record_outcome

    record_tiered_lesson(DB37D525, "agenda", "done", source_goal=TIRE_GOAL[:120])
    record_tiered_lesson(BAE0851F, "agenda", "done", source_goal=TIRE_GOAL[:120])
    record_outcome(
        goal=TIRE_GOAL, status="done", summary="tire memo written",
        task_type="agenda", lessons=[DB37D525, BAE0851F],
    )

    # Tiered: contaminated row quarantined, sibling clean.
    stored = {t.lesson: t for t in load_tiered_lessons(
        tier=MemoryTier.MEDIUM, limit=None, raw=True)}
    assert stored[DB37D525].minted_from == MINTED_FROM_PROMPT
    assert stored[BAE0851F].minted_from == MINTED_FROM_OUTCOME

    # Flat: same split.
    flat = {l.lesson: l for l in load_lessons(
        task_type="agenda", limit=10, include_quarantined=True)}
    assert flat[DB37D525].minted_from == MINTED_FROM_PROMPT
    assert flat[BAE0851F].minted_from == MINTED_FROM_OUTCOME

    # No injection surface serves the contaminated text.
    assert "do not escalate/stop" not in inject_tiered_lessons(task_type="agenda")
    for hit in query_lessons("escalate inaccessible prompt", task_type="agenda"):
        assert hit.lesson != DB37D525
    for l in load_lessons(task_type="agenda", limit=10):
        assert l.lesson != DB37D525
