"""What-not-how mint-form pass (2026-08-02).

Jeremy's decree: "how is ok when asking for work, but usually we aren't —
asking for the right result is the more important part." Lessons are minted
as observations (WHAT was derived), not procedures (HOW to act); the
surprise-read chunk 1 certified L4/M9/M13/M14 as the failure shapes.

Pins here:
- every LLM mint-site prompt carries the shared form rules (and the
  "actionable" how-bias wording is gone),
- the deterministic loop_finalize templates mint diagnosis observations
  with the proposed action marked unverified,
- mint paths stamp the originating run into evidence_sources (M14's
  structural defect was evidence_sources=[] on every reflect mint),
- dedup re-sightings merge their evidence refs (capped) — the "repeated
  across runs X, Y" record — except on contested rows,
- the seed-reader never serves a contested lesson as the style example.
"""

import json
import types

import pytest

from memory import (
    _LESSON_FORM_RULES,
    _REFLECT_SYSTEM,
    _STEP_LESSON_SYSTEM,
    _seed_lesson_block,
    extract_deferred_lessons,
    reflect_and_record,
)
from knowledge_web import (
    MemoryTier,
    contest_lesson,
    load_tiered_lessons,
    record_tiered_lesson,
)
from loop_finalize import _auto_diagnosis_lesson_text, _recovery_plan_lesson_text
from thinkback import _THINKBACK_SYSTEM


def _setup(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    return tmp_path


class _FakeAdapter:
    def __init__(self, *payloads: str):
        self.payloads = list(payloads)

    def complete(self, messages, **kwargs):
        content = self.payloads.pop(0) if self.payloads else ""
        return types.SimpleNamespace(content=content)


# ---------------------------------------------------------------------------
# Prompt-content pins
# ---------------------------------------------------------------------------

class TestPromptForm:
    def test_form_rules_in_reflect_prompt(self):
        assert _LESSON_FORM_RULES in _REFLECT_SYSTEM

    def test_form_rules_in_step_prompt(self):
        assert _LESSON_FORM_RULES in _STEP_LESSON_SYSTEM

    def test_actionable_bias_removed(self):
        # "actionable" was the one-word how-bias in both extractor prompts.
        assert "actionable" not in _REFLECT_SYSTEM
        assert "actionable" not in _STEP_LESSON_SYSTEM

    def test_form_rules_cover_the_certified_shapes(self):
        # M13: requirement-as-observation, not a named lookup path.
        assert "trusted sources" in _LESSON_FORM_RULES
        # M9: a repeated failure is stated as evidence, not a countermeasure.
        assert "repeated" in _LESSON_FORM_RULES.lower()
        # M14: no self-credit clause without an evidencing observation.
        assert "self-credit" in _LESSON_FORM_RULES
        # The decree's exception: procedure form when the goal asked for one.
        assert "runbook" in _LESSON_FORM_RULES

    def test_step_prompt_keeps_scope_rules(self):
        # The form rules must not displace the step-scope discipline.
        assert "NEVER to goal-level success" in _STEP_LESSON_SYSTEM
        assert "deadness" in _STEP_LESSON_SYSTEM

    def test_thinkback_key_lessons_are_observations(self):
        # key_lessons get minted; reviews/retry_strategy stay prescriptive
        # (that's the decree's "asking for work" case).
        assert "derived observation" in _THINKBACK_SYSTEM
        assert "MAY be prescriptive" in _THINKBACK_SYSTEM


# ---------------------------------------------------------------------------
# Deterministic template form (loop_finalize)
# ---------------------------------------------------------------------------

class TestFinalizeTemplates:
    def test_recovery_plan_states_diagnosis_and_marks_unverified(self):
        text = _recovery_plan_lesson_text("repeat_blocker", "add an early checkpoint")
        assert text.startswith("[recovery-plan] repeat_blocker:")
        assert "diagnosed" in text
        assert "unverified" in text
        # The old bare-imperative form is gone.
        assert text != "[recovery-plan] repeat_blocker: add an early checkpoint"

    def test_recovery_plan_deterministic_for_dedup(self):
        a = _recovery_plan_lesson_text("token_explosion", "chunk the output")
        b = _recovery_plan_lesson_text("token_explosion", "chunk the output")
        assert a == b

    def test_auto_diagnosis_form(self):
        text = _auto_diagnosis_lesson_text("stuck_loop", "tighten max_steps")
        assert text.startswith("[auto-diagnosis] stuck_loop:")
        assert "diagnosed" in text
        assert "unverified" in text


# ---------------------------------------------------------------------------
# Evidence stamping (the structural M14 fix)
# ---------------------------------------------------------------------------

_TYPED_PAYLOAD = json.dumps(
    [{"lesson": "pricing claims needed two trusted sources; one page was not enough",
      "type": "verification"}]
)


class TestEvidenceStamping:
    def test_reflect_mints_carry_loop_evidence(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        reflect_and_record(
            "research tire options", "done", "found and priced three options",
            task_type="research", adapter=_FakeAdapter(_TYPED_PAYLOAD),
            loop_id="lp-evid1",
        )
        rows = load_tiered_lessons(MemoryTier.MEDIUM, task_type="research")
        assert rows, "expected a minted MEDIUM lesson"
        assert rows[0].evidence_sources == ["loop:lp-evid1"]

    def test_deferred_mints_carry_loop_evidence(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        reflect_and_record(
            "research tire options", "done", "found and priced three options",
            task_type="research", adapter=_FakeAdapter(),
            loop_id="lp-evid2", defer_lessons=True,
        )
        n = extract_deferred_lessons(
            "lp-evid2", adapter=_FakeAdapter(_TYPED_PAYLOAD))
        assert n == 1
        rows = load_tiered_lessons(MemoryTier.MEDIUM, task_type="research")
        assert rows and rows[0].evidence_sources == ["loop:lp-evid2"]

    def test_reinforce_merges_evidence_refs(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        text = "fetching specs via the manufacturer page verified cleanly"
        record_tiered_lesson(
            lesson_text=text, task_type="research", outcome="done",
            source_goal="goal a", tier=MemoryTier.MEDIUM,
            evidence_sources=["loop:lp-a"],
        )
        tl = record_tiered_lesson(
            lesson_text=text, task_type="research", outcome="done",
            source_goal="goal b", tier=MemoryTier.MEDIUM,
            evidence_sources=["loop:lp-b"],
        )
        assert tl.times_reinforced == 1
        assert tl.evidence_sources == ["loop:lp-a", "loop:lp-b"]

    def test_reinforce_evidence_capped_and_deduped(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from knowledge_web import _REINFORCE_EVIDENCE_CAP
        text = "parsing the ledger needed the header row skipped"
        record_tiered_lesson(
            lesson_text=text, task_type="general", outcome="done",
            source_goal="g0", tier=MemoryTier.MEDIUM,
            evidence_sources=["loop:lp-0"],
        )
        tl = None
        for i in range(1, _REINFORCE_EVIDENCE_CAP + 3):
            tl = record_tiered_lesson(
                lesson_text=text, task_type="general", outcome="done",
                source_goal=f"g{i}", tier=MemoryTier.MEDIUM,
                # duplicate ref + a fresh one — dupes must not re-append
                evidence_sources=["loop:lp-0", f"loop:lp-{i}"],
            )
        assert len(tl.evidence_sources) == _REINFORCE_EVIDENCE_CAP
        assert len(set(tl.evidence_sources)) == _REINFORCE_EVIDENCE_CAP

    def test_contested_rows_do_not_accumulate_evidence(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        text = "the export step needed the summary regenerated first"
        tl = record_tiered_lesson(
            lesson_text=text, task_type="general", outcome="done",
            source_goal="g", tier=MemoryTier.MEDIUM,
            evidence_sources=["loop:lp-1"],
        )
        assert contest_lesson(tl.lesson_id, "operator refuted", source="test")
        after = record_tiered_lesson(
            lesson_text=text, task_type="general", outcome="done",
            source_goal="g2", tier=MemoryTier.MEDIUM,
            evidence_sources=["loop:lp-2"],
        )
        assert after.times_reinforced == 1  # sighting counted (refight input)
        assert after.evidence_sources == ["loop:lp-1"]  # nothing merged


# ---------------------------------------------------------------------------
# Seed-reader hygiene
# ---------------------------------------------------------------------------

class TestSeedBlock:
    def test_contested_long_lesson_never_seeds(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        tl = record_tiered_lesson(
            lesson_text="always add an early checkpoint that re-tests the requirement",
            task_type="agenda", outcome="done", source_goal="g",
            tier=MemoryTier.LONG,
        )
        assert _seed_lesson_block("agenda") != ""  # clean row seeds
        assert contest_lesson(tl.lesson_id, "wrong altitude", source="test",
                              tier=MemoryTier.LONG)
        assert _seed_lesson_block("agenda") == ""  # contested row never does
