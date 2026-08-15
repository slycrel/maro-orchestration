"""Tests for the Director's Playbook — evolving operational wisdom."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from playbook import (
    load_playbook,
    seed_playbook,
    inject_playbook,
    append_to_playbook,
    curate_playbook,
    parse_entries,
)


@pytest.fixture(autouse=True)
def _isolate_workspace(monkeypatch, tmp_path):
    """Point workspace to temp dir."""
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))


class TestPlaybookSeed:

    def test_seed_creates_file(self, tmp_path):
        seed_playbook()
        path = tmp_path / "playbook.md"
        assert path.exists()
        content = path.read_text()
        assert "Director's Playbook" in content
        assert "## Decomposition" in content

    def test_seed_guidance_is_priors_not_requirements(self, tmp_path):
        """Operator surprise-read pins (2026-08-02): the seed carries the
        usually-form decree, and the certified-inappropriate step-count cap
        ("1-4 steps" for narrow goals) and unconditional "promote
        aggressively" stay out."""
        seed_playbook()
        content = (tmp_path / "playbook.md").read_text()
        assert "usually, do" in content          # decree note in the header
        assert "priors the run weighs" in content
        assert "1-4 steps" not in content        # P2: removed, not rephrased
        assert "promote aggressively" not in content  # P16: exit path instead
        assert "contest/retire is the exit" in content

    def test_seed_is_idempotent(self, tmp_path):
        seed_playbook()
        path = tmp_path / "playbook.md"
        original = path.read_text()
        seed_playbook()  # Second call should not overwrite
        assert path.read_text() == original

    def test_load_creates_seed_if_missing(self, tmp_path):
        text = load_playbook()
        assert "Director's Playbook" in text
        assert (tmp_path / "playbook.md").exists()


class TestPlaybookInjection:

    def test_inject_returns_operational_content(self, tmp_path):
        seed_playbook()
        block = inject_playbook()
        assert "## Operational Playbook" in block
        assert "Decomposition" in block

    def test_inject_respects_max_chars(self, tmp_path):
        seed_playbook()
        block = inject_playbook(max_chars=200)
        assert len(block) <= 300  # Some overhead for header

    def test_inject_empty_when_no_content(self, tmp_path):
        # Write an empty playbook (no ## headers)
        (tmp_path / "playbook.md").write_text("Just a title\n")
        block = inject_playbook()
        assert block == ""

    def test_inject_learned_outranks_spam(self, tmp_path):
        """The injection-horizon pin (wiring row 17 / 2026-07-16 incident).

        40 duplicate lines above the fold must not starve a learned entry
        appended at the tail — ranked selection replaced the head window.
        """
        spam = "- Be more concise *(from evolver:test-00)*\n" * 40
        (tmp_path / "playbook.md").write_text(
            "# Director's Playbook\n\n---\n\n"
            "## Execution\n\n"
            "- Always verify outputs before recording as done.\n"
            + spam +
            "\n## Learned\n\n"
            "- Route flaky network steps to the ops worker. *(from evolver:abc-01)*\n"
        )
        block = inject_playbook(max_chars=800)
        assert "Route flaky network steps" in block
        assert block.count("Be more concise") == 1  # deduped, not repeated

    def test_inject_seed_overflow_still_serves_learned(self, tmp_path):
        """Battery V6 pin: the seed alone overflows the 800-char budget, so a
        positional scheme could never show a learned entry. Ranked selection
        puts learned first regardless."""
        seed_playbook()
        append_to_playbook(
            "Prefer artifact-diff evidence over narration when verifying.",
            section="Learned", source="evolver:v6-pin",
        )
        block = inject_playbook(max_chars=800)
        assert "artifact-diff evidence" in block
        assert len(block) <= 800  # budget covers the top header too (F4)

    def test_inject_newest_learned_first_under_tight_budget(self, tmp_path):
        seed_playbook()
        append_to_playbook("Older learned entry about queue retries.",
                           section="Learned", source="evolver:old")
        append_to_playbook("Newer learned entry about artifact checks.",
                           section="Learned", source="evolver:new")
        block = inject_playbook(max_chars=120)
        assert "Newer learned entry" in block
        assert "Older learned entry" not in block

    def test_inject_learned_copy_beats_seed_duplicate(self, tmp_path):
        """Chunk-2 review F3 pin: when a learned entry shares its normalized
        core with a seed bullet, the attributed learned copy must survive
        dedup — rank picks the survivor, not file position."""
        (tmp_path / "playbook.md").write_text(
            "# Director's Playbook\n\n---\n\n"
            "## Execution\n\n"
            "- Always verify outputs before recording as done.\n"
            "\n## Learned\n\n"
            "- Always verify outputs before recording as done. "
            "*(from evolver:dup-01)*\n"
        )
        block = inject_playbook(max_chars=800)
        assert block.count("Always verify outputs") == 1
        assert "*(from evolver:dup-01)*" in block

    @pytest.mark.parametrize("budget", [120, 300, 800])
    def test_inject_budget_is_a_hard_cap(self, tmp_path, budget):
        """Chunk-2 review F4 pin: len(result) <= max_chars, top header
        included — callers (recall.py) trust the cap."""
        spam = "- Be more concise *(from evolver:test-00)*\n" * 40
        (tmp_path / "playbook.md").write_text(
            "# Director's Playbook\n\n---\n\n"
            "## Execution\n\n" + spam +
            "\n## Learned\n\n"
            "- Route flaky network steps to the ops worker. "
            "*(from evolver:abc-01)*\n"
        )
        block = inject_playbook(max_chars=budget)
        assert len(block) <= budget

    def test_inject_newer_learned_renders_above_older(self, tmp_path):
        """Chunk-2 review F6 pin: ranking must be visible in the rendered
        block, not just drive selection — within a section, newest first."""
        (tmp_path / "playbook.md").write_text(
            "# Director's Playbook\n\n---\n\n"
            "## Learned\n\n"
            "- Older learned entry about queue retries. *(from evolver:old)*\n"
            "- Newer learned entry about artifact checks. *(from evolver:new)*\n"
        )
        block = inject_playbook(max_chars=800)
        assert "Older learned entry" in block and "Newer learned entry" in block
        assert block.index("Newer learned entry") < block.index("Older learned entry")


class TestParseEntries:
    def test_learned_detection_by_attribution_and_section(self):
        text = (
            "## Execution\n"
            "- Seed bullet.\n"
            "- Attributed bullet. *(from evolver:x-00)*\n"
            "## Learned\n"
            "- Bare bullet in grown section.\n"
        )
        entries = parse_entries(text)
        assert [e["learned"] for e in entries] == [False, True, True]

    def test_core_normalization(self):
        text = "## Execution\n- Be Concise *(from evolver:a)*\n- be concise\n"
        entries = parse_entries(text)
        assert entries[0]["core"] == entries[1]["core"]


class TestPlaybookAppend:

    def test_append_to_existing_section(self, tmp_path):
        seed_playbook()
        append_to_playbook(
            "Always check token counts before decompose.",
            section="Decomposition",
        )
        text = load_playbook()
        assert "Always check token counts before decompose." in text

    def test_append_creates_new_section(self, tmp_path):
        seed_playbook()
        append_to_playbook(
            "New insight about debugging.",
            section="Debugging",
        )
        text = load_playbook()
        assert "## Debugging" in text
        assert "New insight about debugging." in text

    def test_append_includes_source(self, tmp_path):
        seed_playbook()
        append_to_playbook(
            "Token budgets need attention.",
            section="Cost",
            source="evolver:sug-001",
        )
        text = load_playbook()
        assert "evolver:sug-001" in text

    def test_append_auto_adds_dash_prefix(self, tmp_path):
        seed_playbook()
        append_to_playbook("no dash", section="Learned")
        text = load_playbook()
        assert "- no dash" in text

    def test_append_updates_timestamp(self, tmp_path):
        seed_playbook()
        append_to_playbook("test", section="Learned")
        text = load_playbook()
        assert "*Last updated:" in text

    def test_append_dedup_skips_duplicate(self, tmp_path):
        """Identical entries should not be appended twice."""
        seed_playbook()
        append_to_playbook("Use lower token budgets for cheap tasks.", section="Learned")
        append_to_playbook("Use lower token budgets for cheap tasks.", section="Learned")
        text = load_playbook()
        assert text.count("Use lower token budgets for cheap tasks.") == 1

    def test_append_dedup_ignores_dash_prefix(self, tmp_path):
        """Dedup should work regardless of '- ' prefix."""
        seed_playbook()
        append_to_playbook("- Unique insight here.", section="Learned")
        append_to_playbook("Unique insight here.", section="Learned")
        text = load_playbook()
        assert text.count("Unique insight here.") == 1

    def test_append_different_entries_both_kept(self, tmp_path):
        """Different entries should both be kept."""
        seed_playbook()
        append_to_playbook("First insight.", section="Learned")
        append_to_playbook("Second insight.", section="Learned")
        text = load_playbook()
        assert "First insight." in text
        assert "Second insight." in text


class TestSectionBoundaries:
    """Line-anchored section lookup + one-line entry contract
    (2026-08-15 review, executed probe: substring matching let crafted
    entry text spoof Canon membership for the canon door's verify)."""

    def test_section_text_returns_real_section(self, tmp_path):
        seed_playbook()
        from playbook import section_text
        append_to_playbook("canon-grade insight", section="Canon",
                           source="canon:abc123")
        assert "canon:abc123" in section_text("Canon")
        assert section_text("Nonexistent") == ""

    def test_section_text_ignores_midline_header_lookalike(self, tmp_path):
        seed_playbook()
        from playbook import section_text
        append_to_playbook(
            "we discussed ## Canon promotion rules today",
            section="Learned")
        # No real ## Canon header line exists — a mid-line occurrence of
        # the header string must not open a phantom section.
        assert section_text("Canon") == ""

    def test_multiline_entry_cannot_inject_a_section_header(self, tmp_path):
        seed_playbook()
        from playbook import section_text
        append_to_playbook(
            "benign prefix\n## Canon\ncanon:fake-id spoofed content",
            section="Learned")
        text = load_playbook()
        # The entry landed as ONE line (newlines collapsed) …
        assert "benign prefix ## Canon canon:fake-id spoofed content" in text
        # … so no Canon section exists and the spoofed marker is not
        # readable as Canon membership.
        assert "canon:fake-id" not in section_text("Canon")

    def test_multiline_source_cannot_inject_either(self, tmp_path):
        seed_playbook()
        from playbook import section_text
        append_to_playbook(
            "ordinary insight", section="Learned",
            source="evolver:x\n## Canon\ncanon:fake-src")
        assert "canon:fake-src" not in section_text("Canon")

    def test_append_targets_real_header_not_embedded_string(self, tmp_path):
        seed_playbook()
        from playbook import section_text
        # An entry that MENTIONS ## Ops lands under Learned; a later
        # append to a real ## Ops section must create/target the real
        # header, not splice into the mention.
        append_to_playbook("notes about ## Ops rotation", section="Learned")
        append_to_playbook("restart the box weekly", section="Ops")
        assert "restart the box weekly" in section_text("Ops")
        assert "notes about" not in section_text("Ops")


class _FakeAdapter:
    def __init__(self, content):
        self._content = content
        self.calls = 0

    def complete(self, messages, **kwargs):
        self.calls += 1
        import types
        return types.SimpleNamespace(content=self._content)


class TestPlaybookCuration:
    """The dream-cycle curation verb (swarm-review chunk 2)."""

    def _spammy(self, tmp_path, n=10, extra=""):
        content = (
            "# Director's Playbook\n\n---\n\n"
            "## Execution\n\n"
            "- Always verify outputs before recording as done.\n"
            + "- Be more concise *(from evolver:test-00)*\n" * n
            + "\n## Learned\n\n"
            "- Real learned entry. *(from evolver:abc-01)*\n"
            + extra +
            "\n*Last updated: 2026-07-16*\n"
        )
        (tmp_path / "playbook.md").write_text(content)
        return content

    def test_dedup_collapses_and_archives_original(self, tmp_path):
        original = self._spammy(tmp_path, n=10)
        stats = curate_playbook(force=True)
        assert stats is not None
        assert stats["removed_duplicates"] == 9
        text = (tmp_path / "playbook.md").read_text()
        assert text.count("Be more concise") == 1
        assert "Real learned entry." in text
        # Data-retention: the pre-curation version is archived verbatim.
        archives = list((tmp_path / "playbook_history").glob("playbook-*.md"))
        assert len(archives) == 1
        assert archives[0].read_text() == original
        assert stats["archived"] == str(archives[0])

    def test_noop_when_clean_returns_none(self, tmp_path):
        seed_playbook()
        assert curate_playbook(force=True) is None
        assert not (tmp_path / "playbook_history").exists()

    def test_disabled_by_config(self, tmp_path):
        from unittest.mock import patch
        self._spammy(tmp_path)

        def fake_get(key, default=None):
            if key == "playbook.curation_enabled":
                return False
            return default

        with patch("config.get", side_effect=fake_get):
            assert curate_playbook() is None

    def test_llm_compression_applied_when_valid(self, tmp_path):
        from unittest.mock import patch
        self._spammy(tmp_path, n=2)
        compressed = (
            "# Director's Playbook\n\n## Execution\n\n"
            "- Verify outputs; be concise. *(from evolver:test-00)*\n"
            "## Learned\n\n"
            "- Real learned entry. *(from evolver:abc-01)*\n"
            "*Last updated: 2026-07-16*\n"
        )
        fake = _FakeAdapter(compressed)

        def fake_get(key, default=None):
            if key == "playbook.curation_min_chars":
                return 50  # force the size gate open
            return default

        with patch("config.get", side_effect=fake_get):
            stats = curate_playbook(force=True, adapter=fake)
        assert stats is not None and stats["llm_compressed"] is True
        assert fake.calls == 1
        text = (tmp_path / "playbook.md").read_text()
        assert "Verify outputs; be concise." in text
        # Timestamp refreshed on rewrite
        assert "*Last updated: 2026-07-16*" not in text

    def test_llm_compression_rejected_keeps_deterministic(self, tmp_path):
        from unittest.mock import patch
        self._spammy(tmp_path, n=5)
        # Invalid rewrite: drops the Learned section AND its attribution.
        fake = _FakeAdapter("# Director's Playbook\n\n## Execution\n\n- Tiny.\n")

        def fake_get(key, default=None):
            if key == "playbook.curation_min_chars":
                return 50
            return default

        with patch("config.get", side_effect=fake_get):
            stats = curate_playbook(force=True, adapter=fake)
        assert stats is not None and stats["llm_compressed"] is False
        text = (tmp_path / "playbook.md").read_text()
        assert "Real learned entry." in text          # nothing lost
        assert text.count("Be more concise") == 1     # dedup still applied

    def test_curation_never_raises(self, tmp_path, monkeypatch):
        import playbook as pb
        self._spammy(tmp_path)
        monkeypatch.setattr(pb, "_dedup_text",
                            lambda t: (_ for _ in ()).throw(RuntimeError("boom")))
        assert curate_playbook(force=True) is None

    def test_curation_skips_when_file_changes_mid_pass(self, tmp_path):
        """Chunk-2 review F1 pin: the LLM call runs OUTSIDE the write lock,
        so a concurrent append can land mid-curation. The compare-and-swap
        must then discard this pass — never clobber the fresh entry."""
        from unittest.mock import patch

        path = tmp_path / "playbook.md"
        self._spammy(tmp_path, n=5)

        class _ConcurrentWriterAdapter:
            def complete(self, messages, **kwargs):
                # Simulates another writer appending while curation computes.
                path.write_text(path.read_text() +
                                "- Fresh concurrent insight. *(from evolver:live)*\n")
                import types
                return types.SimpleNamespace(content="junk")  # rejected anyway

        def fake_get(key, default=None):
            if key == "playbook.curation_min_chars":
                return 50  # open the size gate so the adapter runs
            return default

        with patch("config.get", side_effect=fake_get):
            assert curate_playbook(force=True,
                                   adapter=_ConcurrentWriterAdapter()) is None
        text = path.read_text()
        assert "Fresh concurrent insight." in text   # concurrent write kept
        assert text.count("Be more concise") == 5     # our rewrite discarded
        assert not (tmp_path / "playbook_history").exists()  # no false archive


class TestValidCompression:
    """Chunk-2 review F2 pins: the compression guard is structural."""

    def test_header_must_survive_as_exact_line(self):
        from playbook import _valid_compression
        old = "## Cost\n- a\n- b\n"
        assert _valid_compression(old, "## Costly notes\n- a\n- b\n") is False
        assert _valid_compression(old, "## Cost\n- a\n- b\n") is True

    def test_duplicate_attributions_may_not_collapse(self):
        from playbook import _valid_compression
        old = "## L\n- x *(from e:1)*\n- y *(from e:1)*\n"
        merged = "## L\n- xy *(from e:1)*\n- z\n"       # 2 bullets, 1 attrib
        assert _valid_compression(old, merged) is False
        kept = "## L\n- x *(from e:1)*\n- y *(from e:1)*\n"
        assert _valid_compression(old, kept) is True

    def test_bullet_floor_rounds_up(self):
        from playbook import _valid_compression
        old = "## L\n- a\n- b\n- c\n"
        assert _valid_compression(old, "## L\n- a\n") is False   # 1/3 = 33%
        assert _valid_compression(old, "## L\n- a\n- b\n") is True  # 2/3 >= 60%


class TestWorkspaceSkillResolution:
    """Test that skill_loader scans workspace before repo."""

    def test_workspace_skills_dir_exists(self, tmp_path):
        from config import skills_dir
        sd = skills_dir()
        assert sd.exists()
        assert str(tmp_path) in str(sd)

    def test_workspace_personas_dir_exists(self, tmp_path):
        from config import personas_dir
        pd = personas_dir()
        assert pd.exists()
        assert str(tmp_path) in str(pd)

    def test_skill_loader_scans_workspace(self, tmp_path):
        """Workspace skill should be found by SkillLoader."""
        from config import skills_dir
        ws_skills = skills_dir()

        # Write a skill to workspace
        skill_md = ws_skills / "ws_test_skill.md"
        skill_md.write_text(
            "---\n"
            "name: ws-test-skill\n"
            "description: A workspace skill\n"
            "roles_allowed: [worker]\n"
            "triggers: [test workspace]\n"
            "---\n"
            "Body of the workspace skill.\n"
        )

        from skill_loader import SkillLoader
        loader = SkillLoader()
        loader.invalidate()
        summaries = loader.load_summaries()
        names = [s.name for s in summaries]
        assert "ws-test-skill" in names

    def test_workspace_skill_overrides_repo(self, tmp_path):
        """Workspace version should override repo version with same name."""
        from config import skills_dir
        ws_skills = skills_dir()

        # Write a skill that matches a repo skill name
        # (web_research exists in repo)
        skill_md = ws_skills / "web_research.md"
        skill_md.write_text(
            "---\n"
            "name: web_research\n"
            "description: EVOLVED version of web research\n"
            "roles_allowed: [worker]\n"
            "triggers: [research]\n"
            "---\n"
            "Evolved body.\n"
        )

        from skill_loader import SkillLoader
        loader = SkillLoader()
        loader.invalidate()
        summaries = {s.name: s for s in loader.load_summaries()}
        assert "web_research" in summaries
        assert "EVOLVED" in summaries["web_research"].description


class TestGuidanceForm:
    """The 2026-08-02 decree: injected guidance is a prior, not a requirement.

    The live playbook was fixed by hand; these pin the generator so the next
    evolver append can't regress the form (the upgrade edge that surprise
    read left open).
    """

    def test_seed_states_the_entry_form(self):
        from playbook import _SEED_CONTENT
        assert "usually" in _SEED_CONTENT.lower()
        assert "not requirements it obeys" in _SEED_CONTENT

    def test_evolver_prompt_carries_the_guidance_rules(self):
        from evolver import _EVOLVER_SYSTEM
        from playbook import GUIDANCE_FORM_RULES
        assert GUIDANCE_FORM_RULES in _EVOLVER_SYSTEM

    def test_guidance_rules_forbid_the_command_forms(self):
        from playbook import GUIDANCE_FORM_RULES
        body = " ".join(GUIDANCE_FORM_RULES.lower().split())
        for banned in ("always", "you must", "require", "reject any"):
            assert banned in body, f"{banned!r} should be named as a form to avoid"
        assert "prior the run weighs" in body

    def test_evolver_prompt_asks_for_a_regex_not_prose(self):
        """new_guardrail's pattern is matched as a regex — the prompt must say so."""
        from evolver import _EVOLVER_SYSTEM
        assert "regular expression" in _EVOLVER_SYSTEM
        assert "new_guardrail only" in _EVOLVER_SYSTEM


class TestNoInjectSections:
    """Signals held "for human review" ranked ABOVE the curated seed."""

    def test_signals_section_is_not_injected(self, tmp_path, monkeypatch):
        import playbook
        path = tmp_path / "playbook.md"
        path.write_text(
            "# Director's Playbook\n\n"
            "## Execution\n\n"
            "- Atomic steps usually beat broad ones.\n\n"
            "## Signals\n\n"
            "- [Signal] Investigate the second data source *(from evolver:sig-1)*\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(playbook, "_playbook_path", lambda: path)

        block = playbook.inject_playbook(max_chars=800)
        assert "Atomic steps" in block
        assert "Signal" not in block
        assert "## Signals" not in block

    def test_signals_stay_in_the_file(self, tmp_path, monkeypatch):
        import playbook
        path = tmp_path / "playbook.md"
        body = (
            "# Director's Playbook\n\n"
            "## Signals\n\n"
            "- [Signal] keep me\n"
        )
        path.write_text(body, encoding="utf-8")
        monkeypatch.setattr(playbook, "_playbook_path", lambda: path)

        assert playbook.inject_playbook(max_chars=800) == ""
        assert "keep me" in path.read_text()


class TestAlarms:
    """Entries that report a live check, not a durable insight.

    The playbook had no way to tell the two apart, so four frozen cost
    alarms from different eras all told every run to "consider rolling
    back" — and calibration warnings accreted as near-dups. Two more
    accreted in the two days AFTER the operator collapsed the first batch
    by hand, which is what made this a mechanism gap and not a cleanup.
    """

    def _read(self, tmp_path):
        return (tmp_path / "playbook.md").read_text()

    def test_reading_the_same_check_replaces_in_place(self, tmp_path):
        seed_playbook()
        append_to_playbook("observation category is overconfident: 0.88 vs 0.27.",
                           section="Learned", source="evolver:cal-1",
                           key="calibration:observation")
        append_to_playbook("observation category is overconfident: 0.85 vs 0.31.",
                           section="Learned", source="evolver:cal-2",
                           key="calibration:observation")
        text = self._read(tmp_path)
        assert text.count("calibration:observation") == 1
        assert "0.31" in text and "0.27" not in text

    def test_different_checks_coexist(self, tmp_path):
        seed_playbook()
        append_to_playbook("observation is overconfident.", section="Learned",
                           source="evolver:c1", key="calibration:observation")
        append_to_playbook("sub_mission is overconfident.", section="Learned",
                           source="evolver:c2", key="calibration:sub_mission")
        text = self._read(tmp_path)
        assert "calibration:observation" in text
        assert "calibration:sub_mission" in text

    def test_unkeyed_entries_are_never_alarms(self, tmp_path):
        seed_playbook()
        append_to_playbook("Prefer artifact-diff evidence over narration.",
                           section="Learned", source="evolver:x")
        from playbook import alarm_key
        line = [l for l in self._read(tmp_path).splitlines()
                if "artifact-diff" in l][0]
        assert alarm_key(line) == ""

    def test_alarm_expires_when_the_check_stops_firing(self, tmp_path):
        from playbook import expire_stale_alarms
        (tmp_path / "playbook.md").write_text(
            "# Director's Playbook\n\n"
            "## Learned\n\n"
            "- Durable insight worth keeping. *(from evolver:keep)*\n"
            "- avg_cost_usd has risen 40%. *(from evolver:d1 · alarm drift:avg_cost_usd @2026-01-01)*\n",
            encoding="utf-8",
        )
        assert expire_stale_alarms(max_age_days=14) == 1
        text = self._read(tmp_path)
        assert "Durable insight" in text
        assert "avg_cost_usd" not in text

    def test_fresh_alarm_survives_expiry(self, tmp_path):
        from playbook import expire_stale_alarms, _today
        (tmp_path / "playbook.md").write_text(
            "# Director's Playbook\n\n## Learned\n\n"
            f"- Reading. *(from evolver:d1 · alarm drift:x @{_today()})*\n",
            encoding="utf-8",
        )
        assert expire_stale_alarms(max_age_days=14) == 0
        assert "drift:x" in self._read(tmp_path)

    def test_expiry_archives_before_rewriting(self, tmp_path):
        from playbook import expire_stale_alarms
        (tmp_path / "playbook.md").write_text(
            "# Director's Playbook\n\n## Learned\n\n"
            "- Old reading. *(from evolver:d1 · alarm drift:x @2026-01-01)*\n",
            encoding="utf-8",
        )
        expire_stale_alarms(max_age_days=14)
        archives = list((tmp_path / "playbook_history").glob("*"))
        assert archives, "expiry must archive the prior version first"
        assert "Old reading" in archives[0].read_text()

    def test_alarm_line_still_parses_as_learned(self, tmp_path):
        """The marker rides inside the attribution, so provenance detection
        and injection ranking keep working."""
        entries = parse_entries(
            "## Learned\n"
            "- A reading. *(from evolver:d1 · alarm drift:x @2026-08-04)*\n"
        )
        assert entries[0]["learned"] is True
        assert "drift:x" in entries[0]["line"]

    def test_curation_expires_alarms(self, tmp_path):
        from playbook import curate_playbook
        (tmp_path / "playbook.md").write_text(
            "# Director's Playbook\n\n## Learned\n\n"
            "- Keep me. *(from evolver:keep)*\n"
            "- Stale reading. *(from evolver:d1 · alarm drift:x @2026-01-01)*\n",
            encoding="utf-8",
        )
        stats = curate_playbook(force=True)
        assert stats is not None
        assert stats["expired_alarms"] == ["drift:x"]
        assert "Stale reading" not in self._read(tmp_path)
        assert "Keep me" in self._read(tmp_path)
