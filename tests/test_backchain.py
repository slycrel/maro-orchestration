"""Tests for backchain.py — §9.9 backward-chaining as a recon generator."""

import json
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, "src")

from backchain import (
    MAX_PROBE_STEPS,
    Backchain,
    BackchainLink,
    apply_backchain,
    draw_backchain,
    probe_steps,
)


def _adapter_returning(payload) -> MagicMock:
    adapter = MagicMock()
    resp = MagicMock()
    resp.content = json.dumps(payload)
    adapter.complete.return_value = resp
    return adapter


_CHAIN = {
    "links": [
        {"condition": "the report file exists", "class": "established", "step": 3},
        {"condition": "git history is readable", "class": "verifiable",
         "probe": "git -C repo log --oneline -1"},
        {"condition": "the module map is accurate", "class": "unknown"},
    ]
}


class TestDrawBackchain:
    def test_parses_all_three_classes(self):
        chain = draw_backchain("goal", ["s1", "s2", "s3"], _adapter_returning(_CHAIN))
        assert chain is not None
        assert [l.link_class for l in chain.links] == \
            ["established", "verifiable", "unknown"]
        assert chain.links[0].step == 3
        assert "git" in chain.links[1].probe

    def test_established_with_out_of_range_step_downgrades_to_unknown(self):
        """A step reference outside the plan is a fabricated establishment."""
        payload = {"links": [
            {"condition": "c", "class": "established", "step": 9}]}
        chain = draw_backchain("g", ["s1", "s2"], _adapter_returning(payload))
        assert chain.links[0].link_class == "unknown"
        assert chain.links[0].step is None

    def test_established_with_garbage_step_downgrades(self):
        payload = {"links": [
            {"condition": "c", "class": "established", "step": "three"}]}
        chain = draw_backchain("g", ["s1"], _adapter_returning(payload))
        assert chain.links[0].link_class == "unknown"

    def test_verifiable_without_probe_downgrades_to_unknown(self):
        payload = {"links": [
            {"condition": "c", "class": "verifiable", "probe": "  "}]}
        chain = draw_backchain("g", ["s1"], _adapter_returning(payload))
        assert chain.links[0].link_class == "unknown"

    def test_bad_class_and_empty_condition_dropped(self):
        payload = {"links": [
            {"condition": "", "class": "unknown"},
            {"condition": "c", "class": "definitely"},
            "not a dict",
        ]}
        assert draw_backchain("g", ["s1"], _adapter_returning(payload)) is None

    def test_call_failure_returns_none(self):
        adapter = MagicMock()
        adapter.complete.side_effect = RuntimeError("boom")
        assert draw_backchain("g", ["s1"], adapter) is None

    def test_garbage_answer_returns_none(self):
        adapter = MagicMock()
        resp = MagicMock()
        resp.content = "not json"
        adapter.complete.return_value = resp
        assert draw_backchain("g", ["s1"], adapter) is None

    def test_no_adapter_or_no_steps_returns_none(self):
        assert draw_backchain("g", ["s1"], None) is None
        assert draw_backchain("g", [], MagicMock()) is None


class TestProbeSteps:
    def test_probes_carry_recon_tag_with_condition(self):
        chain = Backchain(links=[
            BackchainLink("git history is readable", "verifiable",
                          probe="git log --oneline -1"),
        ])
        steps = probe_steps(chain)
        assert len(steps) == 1
        assert steps[0].startswith("git log --oneline -1 [recon: ")
        assert "verifies precondition — git history is readable" in steps[0]

    def test_capped_at_max(self):
        chain = Backchain(links=[
            BackchainLink(f"cond {i}", "verifiable", probe=f"probe {i}")
            for i in range(5)
        ])
        assert len(probe_steps(chain)) == MAX_PROBE_STEPS

    def test_brackets_scrubbed_from_tag(self):
        """Brackets in a condition would corrupt the [recon: ...] grammar."""
        chain = Backchain(links=[
            BackchainLink("the [after:2] tag parses", "verifiable", probe="p"),
        ])
        step = probe_steps(chain)[0]
        tag_body = step.split("[recon: ", 1)[1]
        assert "[" not in tag_body[:-1]
        # The tag must still be recognized as recon by the planner grammar.
        from planner import step_flavor
        assert step_flavor(step)[0] == "recon"

    def test_brackets_scrubbed_from_probe_body(self):
        """The probe body is equally LLM-authored: a [after:N]-shaped
        substring in it would forge a dependency edge (parse_dependencies
        searches the whole step string) — r1 finding, all three lenses."""
        chain = Backchain(links=[
            BackchainLink("cond", "verifiable",
                          probe="grep '[after:2]' build.log"),
        ])
        step = probe_steps(chain)[0]
        from planner import after_deps, step_flavor
        assert not after_deps(step)  # None/[] — no forged dependency
        assert step_flavor(step)[0] == "recon"
        assert step.startswith("grep '(after:2)' build.log [recon: ")

    def test_established_and_unknown_links_never_become_steps(self):
        chain = Backchain(links=[
            BackchainLink("done by plan", "established", step=1),
            BackchainLink("nobody knows", "unknown"),
        ])
        assert probe_steps(chain) == []


class TestApplyBackchain:
    def _enable(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        (tmp_path / "config.yml").write_text("planner:\n  backchain: true\n")

    def test_off_by_default_no_llm_call(self):
        adapter = MagicMock()
        steps = ["s1", "s2"]
        out = apply_backchain("g", steps, adapter)
        assert out == steps
        assert not adapter.complete.called

    @staticmethod
    def _records(rd):
        lines = (rd / "build" / "backchain.jsonl").read_text().splitlines()
        return [json.loads(l) for l in lines if l.strip()]

    def test_enabled_prepends_probes_and_records(self, tmp_path, monkeypatch):
        self._enable(tmp_path, monkeypatch)
        rd = tmp_path / "run-x"
        (rd / "build").mkdir(parents=True)
        adapter = _adapter_returning(_CHAIN)
        with patch("runs.current_run_dir", return_value=rd):
            out = apply_backchain("g", ["s1", "s2", "s3"], adapter)
        assert len(out) == 4
        assert out[0].startswith("git -C repo log")
        assert "[recon: " in out[0]
        assert out[1:] == ["s1", "s2", "s3"]
        (rec,) = self._records(rd)
        assert len(rec["links"]) == 3
        assert rec["probes_injected"] == 1
        # r1 HIGH (both lenses): recorded step refs must match the FINAL
        # numbering. _CHAIN says established at step 3 of the pre-injection
        # plan; one probe was prepended, so the executed plan has that
        # step at position 4 — and the record must say 4.
        assert rec["links"][0]["step"] == 4
        assert out[3] == "s3"
        # r1 (architect): acted-on probes are distinguishable from
        # capped-out ones in the record.
        assert rec["links"][1]["injected"] is True

    def test_all_established_changes_nothing_but_still_records(
            self, tmp_path, monkeypatch):
        self._enable(tmp_path, monkeypatch)
        rd = tmp_path / "run-x"
        (rd / "build").mkdir(parents=True)
        payload = {"links": [
            {"condition": "c1", "class": "established", "step": 1},
            {"condition": "c2", "class": "established", "step": 2},
        ]}
        with patch("runs.current_run_dir", return_value=rd):
            out = apply_backchain("g", ["s1", "s2"], _adapter_returning(payload))
        assert out == ["s1", "s2"]
        (rec,) = self._records(rd)
        assert rec["probes_injected"] == 0
        # No injection → step refs keep the (identical) original numbering.
        assert [l["step"] for l in rec["links"]] == [1, 2]

    def test_replan_appends_second_chain(self, tmp_path, monkeypatch):
        """Restart/escalation loops re-plan in the same run dir — a later
        chain must append, never clobber (r1 finding, two lenses)."""
        self._enable(tmp_path, monkeypatch)
        rd = tmp_path / "run-x"
        (rd / "build").mkdir(parents=True)
        with patch("runs.current_run_dir", return_value=rd):
            apply_backchain("g", ["s1", "s2", "s3"], _adapter_returning(_CHAIN))
            apply_backchain("g2", ["t1", "t2", "t3"], _adapter_returning(_CHAIN))
        recs = self._records(rd)
        assert len(recs) == 2

    def test_capped_out_probe_link_marked_uninjected(
            self, tmp_path, monkeypatch):
        self._enable(tmp_path, monkeypatch)
        rd = tmp_path / "run-x"
        (rd / "build").mkdir(parents=True)
        payload = {"links": [
            {"condition": f"c{i}", "class": "verifiable", "probe": f"p{i}"}
            for i in range(3)
        ]}
        with patch("runs.current_run_dir", return_value=rd):
            out = apply_backchain("g", ["s1", "s2"], _adapter_returning(payload))
        assert len(out) == 2 + MAX_PROBE_STEPS
        (rec,) = self._records(rd)
        assert [l["injected"] for l in rec["links"]] == [True, True, False]

    def test_goal_stated_ceiling_skips_injection(self, tmp_path, monkeypatch):
        """An operator-stated step ceiling binds the whole plan — backchain
        must not push past explicit operator intent (r1, architect)."""
        self._enable(tmp_path, monkeypatch)
        adapter = MagicMock()
        steps = ["s1", "s2"]
        out = apply_backchain("do the thing in at most 2 steps", steps, adapter)
        assert out == steps
        assert not adapter.complete.called

    def test_skips_boundary_plans(self, tmp_path, monkeypatch):
        self._enable(tmp_path, monkeypatch)
        from planner import BOUNDARY_TAG
        adapter = MagicMock()
        steps = ["probe A [recon: decides the boundary plan — x]",
                 f"Plan and complete the rest {BOUNDARY_TAG}"]
        out = apply_backchain("g", steps, adapter)
        assert out == steps
        assert not adapter.complete.called

    def test_skips_trivial_plans(self, tmp_path, monkeypatch):
        self._enable(tmp_path, monkeypatch)
        adapter = MagicMock()
        out = apply_backchain("g", ["only step"], adapter)
        assert out == ["only step"]
        assert not adapter.complete.called

    def test_draw_failure_leaves_plan_untouched(self, tmp_path, monkeypatch):
        self._enable(tmp_path, monkeypatch)
        adapter = MagicMock()
        adapter.complete.side_effect = RuntimeError("dead key")
        steps = ["s1", "s2"]
        assert apply_backchain("g", steps, adapter) == steps

    def test_record_failure_never_blocks_probe_injection(
            self, tmp_path, monkeypatch):
        self._enable(tmp_path, monkeypatch)
        adapter = _adapter_returning(_CHAIN)
        with patch("runs.current_run_dir", side_effect=RuntimeError("no run")):
            out = apply_backchain("g", ["s1", "s2", "s3"], adapter)
        assert len(out) == 4  # probe still prepended

    def test_planner_import_failure_fails_closed(self, tmp_path, monkeypatch):
        """If the boundary/ceiling exclusion checks can't run, backchain
        must skip, not fire on the possibly-excluded lane (r1, two
        lenses: the original except: pass failed open)."""
        self._enable(tmp_path, monkeypatch)
        adapter = MagicMock()
        # A None entry in sys.modules makes `from planner import ...`
        # raise ImportError without touching the import machinery.
        with patch.dict(sys.modules, {"planner": None}):
            out = apply_backchain("g", ["s1", "s2"], adapter)
        assert out == ["s1", "s2"]
        assert not adapter.complete.called
