"""Mint-grounding slice 2a (2026-08-16): writer completion + the R1-4
laundering fix + promotion-judge visibility.

Census that drove this chunk (fix-every-lane): of the seven
record_tiered_lesson callers, two reflect paths + two loop_finalize
recovery writers were stamped by slice 1; step-lessons (memory.py),
prereq acquisition, and thinkback were minting unstamped WITH run
context in hand — thinkback was a lane R1-3's own list missed. The CLI
manual lane and evolver prompt_tweak apply-lane stay stampless by
construction (no minting-run ground truth at write time).

R1-4: knowledge_bridge stripped grounding entirely — an unsupported
provenance claim could launder into decay-free knowledge with the stamp
gone. Nodes now ground their OWN text against the minting outcome's
events at CREATE; re-observation never re-grounds (mint-time
semantics); the promotion judge sees the stamps, advisory only.
"""

import json
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def _make_run(handle_id, events=None):
    import runs
    rd = runs.create_run_dir(handle_id, prompt="goal")
    calls = rd / "build" / "calls"
    calls.mkdir(parents=True, exist_ok=True)
    (calls / "call-00001.json").write_text(json.dumps({
        "tool_events": events if events is not None else [
            {"name": "Bash",
             "input": "curl -s https://example.com/dataset.csv",
             "output": "ok", "is_error": False},
        ]}))
    return rd


_CLAIM = "The dataset was retrieved from example.com by an authenticated fetch."


class TestWriterCompletion:
    """C1-C3: the three writers R1-3 left unstamped now stamp."""

    def _capture(self, monkeypatch, module):
        seen = []

        def fake_record(*a, **kw):
            seen.append(kw)
            return types.SimpleNamespace(lesson_id="l1")

        # Each writer imports record_tiered_lesson at call time from a
        # different home; patch the exporting module's symbol.
        monkeypatch.setattr(module, "record_tiered_lesson", fake_record)
        return seen

    def test_step_lessons_stamp_against_loop(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        _make_run("h-s2-step")
        import memory
        seen = self._capture(monkeypatch, memory)

        class FakeAdapter:
            def complete(self, messages, **kw):
                return types.SimpleNamespace(content=json.dumps(
                    [{"lesson": _CLAIM, "type": "execution"}]))

        steps = [types.SimpleNamespace(
            status="done", result="ok", step="step 1",
            confidence="strong")]
        memory.extract_step_lessons(
            "goal", steps, adapter=FakeAdapter(), loop_id="h-s2-step")
        assert len(seen) == 1
        statuses = {g["family"]: g["status"] for g in seen[0]["grounding"]}
        assert statuses["fetch"] == "supported"
        assert statuses["auth"] == "unsupported"

    def test_step_lessons_without_loop_id_stampless(self, monkeypatch,
                                                    tmp_path):
        # Absent context -> absent stamps (None), byte-identical row later.
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        import memory
        seen = self._capture(monkeypatch, memory)

        class FakeAdapter:
            def complete(self, messages, **kw):
                return types.SimpleNamespace(content=json.dumps(
                    [{"lesson": _CLAIM, "type": "execution"}]))

        steps = [types.SimpleNamespace(
            status="done", result="ok", step="step 1",
            confidence="strong")]
        memory.extract_step_lessons("goal", steps, adapter=FakeAdapter(),
                                    loop_id="")
        assert len(seen) == 1
        assert seen[0]["grounding"] is None

    def test_prereq_grounds_against_sub_loop(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        _make_run("h-s2-prereq")
        import prereq
        seen = []

        def fake_record(*a, **kw):
            seen.append(kw)
            return types.SimpleNamespace(lesson_id="l1")

        import memory
        monkeypatch.setattr(memory, "record_tiered_lesson", fake_record)
        sub_result = types.SimpleNamespace(
            loop_id="h-s2-prereq",
            steps=[types.SimpleNamespace(status="done", result=_CLAIM)],
            stuck_reason="")
        import agent_loop
        monkeypatch.setattr(agent_loop, "run_agent_loop",
                            lambda *a, **kw: sub_result)
        out = prereq._spawn_knowledge_sub_loop(
            "auth fetching", goal_id="g1", adapter=object())
        assert out  # summary extracted
        assert len(seen) == 1
        statuses = {g["family"]: g["status"] for g in seen[0]["grounding"]}
        assert statuses["auth"] == "unsupported"

    def test_thinkback_grounds_against_run(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        _make_run("h-s2-tb")
        import thinkback
        import knowledge_web
        seen = []

        def fake_record(*a, **kw):
            seen.append(kw)
            return types.SimpleNamespace(lesson_id="l1")

        monkeypatch.setattr(knowledge_web, "record_tiered_lesson",
                            fake_record)
        thinkback._save_thinkback_lessons("goal", [_CLAIM], "h-s2-tb")
        assert len(seen) == 1
        statuses = {g["family"]: g["status"] for g in seen[0]["grounding"]}
        assert statuses["fetch"] == "supported"
        assert statuses["auth"] == "unsupported"


class TestKnowledgeLaunderingFix:
    """C5: grounding travels to KnowledgeNode at create; mint-time only."""

    def _outcome(self, loop_id):
        return types.SimpleNamespace(
            goal="fetch the dataset", status="done",
            outcome_id="o-s2", loop_id=loop_id, dry_run=False,
            summary=_CLAIM, task_type="general")

    def test_node_carries_grounding_at_create(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        _make_run("h-s2-node")
        from knowledge_bridge import outcome_to_knowledge
        import knowledge_bridge as kb
        monkeypatch.setattr(
            kb, "_extract_heuristic",
            lambda outcome: [("Dataset retrieval",
                              _CLAIM, "insight")])
        n = outcome_to_knowledge(self._outcome("h-s2-node"), adapter=None)
        assert n == 1
        from knowledge_web import load_knowledge_nodes
        nodes = load_knowledge_nodes(status=None)
        assert len(nodes) == 1
        statuses = {g["family"]: g["status"] for g in nodes[0].grounding}
        assert statuses["fetch"] == "supported"
        assert statuses["auth"] == "unsupported"

    def test_reobservation_never_regrounds(self, monkeypatch, tmp_path):
        # Mint-time semantics: the second observation (different run, no
        # events) bumps confidence but leaves the original receipts alone.
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        _make_run("h-s2-mint")
        from knowledge_bridge import outcome_to_knowledge
        import knowledge_bridge as kb
        monkeypatch.setattr(
            kb, "_extract_heuristic",
            lambda outcome: [("Dataset retrieval", _CLAIM, "insight")])
        outcome_to_knowledge(self._outcome("h-s2-mint"), adapter=None)
        from knowledge_web import load_knowledge_nodes
        before = load_knowledge_nodes(status=None)[0].grounding
        assert before
        outcome_to_knowledge(self._outcome("no-such-run"), adapter=None)
        nodes = load_knowledge_nodes(status=None)
        assert len(nodes) == 1  # deduped update, not a second node
        assert nodes[0].grounding == before
        assert nodes[0].times_applied == 1

    def test_stampless_node_row_shape_preserved(self, monkeypatch, tmp_path):
        # Absent-key discipline: no run ground truth -> the persisted row
        # has NO grounding key at all.
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        from knowledge_bridge import outcome_to_knowledge
        import knowledge_bridge as kb
        monkeypatch.setattr(
            kb, "_extract_heuristic",
            lambda outcome: [("Dataset retrieval", _CLAIM, "insight")])
        outcome_to_knowledge(self._outcome(""), adapter=None)
        from memory_ledger import _memory_dir
        row = json.loads(
            (_memory_dir() / "knowledge_nodes.jsonl").read_text()
            .strip().splitlines()[0])
        assert "grounding" not in row


class TestPromotionJudgeVisibility:
    """C6: the judge SEES stamps; advisory — never a deterministic flip."""

    def _judge_capture(self, verdict=True):
        calls = []

        class FakeAdapter:
            def complete(self, messages, **kw):
                calls.append(messages)
                return types.SimpleNamespace(
                    content=json.dumps({"valid": verdict, "reason": "r"}))

        return FakeAdapter(), calls

    def test_unsupported_claims_reach_the_judge(self):
        from knowledge_web import _validate_node_for_promotion
        adapter, calls = self._judge_capture()
        d = {"node_id": "n1", "node_type": "insight", "title": "t",
             "description": "d", "times_applied": 3, "confidence": 0.8,
             "grounding": [{"claim": "authenticated fetch",
                            "family": "auth", "status": "unsupported",
                            "receipts": []}]}
        _validate_node_for_promotion(d, adapter)
        user_msg = calls[0][1].content
        assert "UNSUPPORTED" in user_msg
        assert "authenticated fetch" in user_msg
        assert "not an automatic disqualifier" in user_msg

    def test_supported_claims_render_count_line(self):
        from knowledge_web import _validate_node_for_promotion
        adapter, calls = self._judge_capture()
        d = {"node_id": "n1", "node_type": "insight", "title": "t",
             "description": "d", "times_applied": 3, "confidence": 0.8,
             "grounding": [{"claim": "fetched", "family": "fetch",
                            "status": "supported", "receipts": ["r"]}]}
        _validate_node_for_promotion(d, adapter)
        assert "1/1 method claim(s) supported" in calls[0][1].content

    def test_stampless_prompt_byte_identical(self):
        # Behavior preservation: no grounding -> the judge prompt must not
        # mention grounding at all (old nodes judge exactly as before).
        from knowledge_web import _validate_node_for_promotion
        adapter, calls = self._judge_capture()
        d = {"node_id": "n1", "node_type": "insight", "title": "t",
             "description": "d", "times_applied": 3, "confidence": 0.8}
        _validate_node_for_promotion(d, adapter)
        assert "grounding" not in calls[0][1].content.lower()

    def test_advisory_never_flips_a_valid_verdict(self, monkeypatch,
                                                  tmp_path):
        # The fail-open decree's pin: a judged-valid node with UNSUPPORTED
        # claims still promotes — stamps inform the judge, nothing
        # deterministically blocks.
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        import knowledge_web as kw
        from knowledge_web import (KnowledgeNode, NODE_CANDIDATE,
                                   append_knowledge_node,
                                   promote_knowledge_candidates)
        node = KnowledgeNode(
            node_id="n-adv", node_type="insight", title="t",
            description="d", status=NODE_CANDIDATE, confidence=0.9,
            times_applied=99,
            grounding=[{"claim": "authenticated fetch", "family": "auth",
                        "status": "unsupported", "receipts": []}])
        append_knowledge_node(node)
        adapter, _ = self._judge_capture(verdict=True)
        promoted = promote_knowledge_candidates(adapter=adapter)
        assert promoted == ["n-adv"]
