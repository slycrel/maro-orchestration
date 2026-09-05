"""The landscape (feature-related-runs, 2026-09-05): before a goal runs,
Maro decides its relation to the workspace's prior runs — fresh / related /
rerun — from deterministic candidates and one recorded judge call, and the
decision drives feature 1's lineage (the goal follows the chosen run; its
answer rides into the request). Same fixture shape as the Go engine's
TestLandscapeDecidesTheRelation (go/internal/run/landscape_test.go).
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

GOAL_QUARTERLY = "Summarize the quarterly revenue report for the board"
GOAL_FOLLOW_UP = "Summarize the quarterly revenue report for the board, with margins"
GOAL_HAIKU = "Write a haiku about autumn leaves"


def _setup(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    (tmp_path / "memory").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _finished_run(goal, answer="", *, origin=None, handle_id=None, status="done",
                  finished=True, dry_run=False, extra=None):
    """A prior run as the workspace holds it: metadata with a prompt, a
    terminal status and ended_at, and (when given) a NOW answer artifact."""
    import runs
    import uuid
    handle_id = handle_id or uuid.uuid4().hex[:8]
    meta = {}
    if origin:
        meta["origin"] = origin
    if dry_run:
        meta["dry_run"] = True
    if extra:
        meta.update(extra)
    runs.create_run_dir(handle_id, prompt=goal, lane="now", extra_metadata=meta or None)
    if finished:
        runs.stamp_run_metadata_for(handle_id, {"status": status,
                                                "ended_at": "2026-09-05T00:00:00+00:00"})
    if answer:
        rd = runs.run_dir(handle_id)
        (rd / "artifact").mkdir(parents=True, exist_ok=True)
        (rd / "artifact" / f"now-{handle_id}.json").write_text(
            json.dumps({"result": answer}), encoding="utf-8")
    return handle_id


class _Judge:
    """A scripted judge: answers in order; records every request."""

    def __init__(self, *answers, raise_exc=None):
        self.answers = list(answers)
        self.calls = []
        self.raise_exc = raise_exc
        self.model = "scripted-judge"

    def complete(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        if self.raise_exc:
            raise self.raise_exc
        if not self.answers:
            raise AssertionError("judge asked more than scripted")

        class _R:
            content = self.answers.pop(0)
            input_tokens = 7
            output_tokens = 3
            model = "scripted-judge"
        return _R()


def _related(n, reason="follows it"):
    return json.dumps({"relation": "related", "run": n, "reason": reason})


def _rerun(n, reason="same ask"):
    return json.dumps({"relation": "rerun", "run": n, "reason": reason})


# ---------------------------------------------------------------------------
# units
# ---------------------------------------------------------------------------

class TestSimilarity:
    def test_jaccard_over_goal_words(self):
        from landscape import similarity, goal_words
        assert similarity(GOAL_QUARTERLY, GOAL_QUARTERLY) == 1.0
        assert similarity(GOAL_QUARTERLY, GOAL_HAIKU) == 0.0
        assert similarity("", GOAL_HAIKU) == 0.0
        assert goal_words("a an to the Board, BOARD!") == {"the", "board"}
        s = similarity(GOAL_QUARTERLY, GOAL_FOLLOW_UP)
        assert 0.7 < s < 1.0


class TestAnswerHead:
    def test_the_head_is_bounded(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from landscape import answer_head, prompt, RELATED_HEAD
        a = _finished_run(GOAL_QUARTERLY, "x" * (RELATED_HEAD + 500))
        head = answer_head(a)
        assert len(head) == RELATED_HEAD + 1 and head.endswith("…")
        req = prompt(GOAL_FOLLOW_UP, [{"handle_id": a, "goal": GOAL_QUARTERLY,
                                       "similarity": 0.9, "status": "done"}])
        assert "x" * RELATED_HEAD + "…" in req and "x" * (RELATED_HEAD + 1) not in req
        assert answer_head("00000000") == ""


class TestCandidates:
    def test_only_finished_production_runs_of_other_goals(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from landscape import candidates, FLOOR, TOP_K
        a = _finished_run(GOAL_QUARTERLY)
        _finished_run(GOAL_QUARTERLY, finished=False)          # still running
        _finished_run(GOAL_QUARTERLY, dry_run=True)            # a dry run
        h = _finished_run(GOAL_HAIKU)                          # below the floor
        cands, scanned, below = candidates(GOAL_FOLLOW_UP, exclude_handle_id="")
        assert [c["handle_id"] for c in cands] == [a]
        assert scanned == 2 and below == 1
        assert cands[0]["similarity"] >= FLOOR and cands[0]["status"] == "done"
        # the goal's own run is never its own candidate
        cands, _, _ = candidates(GOAL_FOLLOW_UP, exclude_handle_id=a)
        assert cands == []
        assert h not in [c["handle_id"] for c in cands]

    def test_top_k_by_similarity_then_newest_handle(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from landscape import candidates, TOP_K
        exact = [_finished_run(GOAL_QUARTERLY, handle_id=f"aaaa000{i}") for i in range(1, 4)]
        near = _finished_run(GOAL_FOLLOW_UP, handle_id="bbbb0001")
        cands, scanned, below = candidates(GOAL_QUARTERLY)
        assert scanned == 4 and below == 0
        assert len(cands) == TOP_K
        assert [c["handle_id"] for c in cands] == ["aaaa0003", "aaaa0002", "aaaa0001"]
        assert near not in [c["handle_id"] for c in cands]


class TestParse:
    CANDS = [{"handle_id": "aaaa0001", "goal": "g1"}, {"handle_id": "aaaa0002", "goal": "g2"}]

    def test_number_handle_and_fresh(self):
        from landscape import parse
        assert parse(_related(2), self.CANDS) == ("related", "aaaa0002", "follows it")
        assert parse(_related("2"), self.CANDS)[1] == "aaaa0002"
        assert parse(_rerun("aaaa0001"), self.CANDS) == ("rerun", "aaaa0001", "same ask")
        assert parse(_rerun("run aaaa0001"), self.CANDS)[1] == "aaaa0001"
        assert parse('{"relation":"fresh","run":0,"reason":"new"}', self.CANDS) == ("fresh", "", "new")
        assert parse('prose first {"relation": "Fresh", "run": "0"} prose after', self.CANDS)[0] == "fresh"

    def test_the_first_contract_names_by_number_only(self):
        from landscape import parse
        assert parse(_related("2"), self.CANDS, ver=1)[1] == "aaaa0002"
        with pytest.raises(ValueError):
            parse(_rerun("aaaa0001"), self.CANDS, ver=1)

    @pytest.mark.parametrize("bad", [
        "", "no json here", "[1,2]", '{"relation":"maybe","run":1}',
        '{"relation":"related","run":0}', '{"relation":"related","run":3}',
        '{"relation":"rerun","run":"cccc0009"}', '{"relation":"related","run":true}',
        '{"relation":"related"}',
    ])
    def test_outside_the_contract_raises(self, bad):
        from landscape import parse
        with pytest.raises(ValueError):
            parse(bad, self.CANDS)


# ---------------------------------------------------------------------------
# the fixture: A fresh, B follows A, C fresh (unrelated), D reruns A, E --fresh
# ---------------------------------------------------------------------------

class TestLandscapeDecidesTheRelation:
    def test_a_first_goal_is_fresh_without_a_call(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from landscape import decide, apply, related_context
        judge = _Judge()
        rec = decide(GOAL_QUARTERLY, handle_id="run_a", adapter=judge)
        assert rec["rule"] == "no_candidates" and rec["relation"] == "fresh"
        assert rec["scanned"] == 0 and rec["candidates"] == [] and "judge" not in rec
        assert judge.calls == []
        assert related_context(rec) == ""

    def test_a_follow_up_follows_the_prior_run(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from landscape import decide, apply, related_context, PROMPT_VER
        from recall import lineage_root, recall
        a = _finished_run(GOAL_QUARTERLY, "Revenue rose 12% on services.",
                          extra={"project": "board-report", "loops": [{"loop_id": "loop-a"}]})
        from loop_artifacts import _plan_manifest_path
        p = _plan_manifest_path("board-report", "loop-a")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("## Steps (1 planned)\n\n1. ⬜ Read the ledger\n", encoding="utf-8")
        b = _finished_run(GOAL_FOLLOW_UP, finished=False)
        judge = _Judge(_related(1, "a follow-up on the same report"))
        rec = decide(GOAL_FOLLOW_UP, handle_id=b, adapter=judge)
        assert rec["rule"] == "judge" and rec["relation"] == "related" and rec["chosen"] == a
        assert rec["reason"] == "a follow-up on the same report"
        assert rec["prompt_ver"] == PROMPT_VER
        assert rec["judge"] == {"model": "scripted-judge", "input_tokens": 7, "output_tokens": 3}
        # the one call: no tools, the landscape purpose, the candidate shown by handle
        (messages, kw), = judge.calls
        assert kw["no_tools"] is True and kw["purpose"] == "landscape"
        req = messages[-1].content
        assert f"Candidate 1 (run {a}, similarity" in req
        assert "Answer: Revenue rose 12% on services." in req and GOAL_FOLLOW_UP in req
        # the decision stamps the run and makes it FOLLOW a (feature 1's lineage)
        origin = apply(b, None, rec)
        assert origin["parent_handle_id"] == a and origin["related_by"] == "landscape"
        assert origin["relation"] == "related" and origin["parent_goal"] == GOAL_QUARTERLY
        from runs import run_dir
        meta = json.loads((run_dir(b) / "metadata.json").read_text())
        assert meta["landscape"]["chosen"] == a and meta["origin"]["parent_handle_id"] == a
        assert lineage_root(b) == a
        r = recall(GOAL_FOLLOW_UP, slice="dispatch", origin=origin)
        assert r.thread is not None and r.thread.parent_goal == GOAL_QUARTERLY
        # the prior's answer rides into the request
        ctx = related_context(rec)
        assert ctx.startswith(f"## Related prior run ({a}, related)")
        assert "Its answer:\nRevenue rose 12% on services." in ctx
        assert "Its plan" not in ctx and "Read the ledger" not in ctx   # related ≠ rerun

    def test_an_unrelated_goal_is_fresh_without_a_call(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from landscape import decide, apply
        _finished_run(GOAL_QUARTERLY, "Revenue rose.")
        judge = _Judge()
        rec = decide(GOAL_HAIKU, handle_id="run_c", adapter=judge)
        assert rec["rule"] == "no_candidates" and rec["scanned"] == 1 and rec["below_floor"] == 1
        assert judge.calls == []
        c = _finished_run(GOAL_HAIKU, finished=False)
        assert apply(c, None, rec) is None
        from recall import lineage_root
        assert lineage_root(c) == c

    def test_a_rerun_carries_the_prior_plan(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from landscape import decide, apply, related_context
        from loop_artifacts import _plan_manifest_path
        a = _finished_run(GOAL_QUARTERLY, "Revenue rose 12%.",
                          extra={"project": "board-report", "loops": [{"loop_id": "loop-1"}]})
        p = _plan_manifest_path("board-report", "loop-1")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# Run Plan — `loop-1`\n**Project:** board-report\n\n## Steps (2 planned)\n\n"
                     "1. ✅ `[research]` Read the revenue ledger | 900ms | 120 tok | $0.0010\n"
                     "2. ⬜ Draft the board summary\n\n## Execution Log\n\n1. not a step\n",
                     encoding="utf-8")
        d = _finished_run(GOAL_QUARTERLY, finished=False)
        judge = _Judge(_rerun(1))
        rec = decide(GOAL_QUARTERLY, handle_id=d, adapter=judge)
        assert rec["relation"] == "rerun" and rec["chosen"] == a
        origin = apply(d, {"source": "cli"}, rec)
        assert origin["relation"] == "rerun" and origin["parent_handle_id"] == a
        ctx = related_context(rec)
        assert ctx.startswith(f"## Related prior run ({a}, rerun)")
        assert ctx.endswith("Its plan (reuse or revise):\n1. Read the revenue ledger\n2. Draft the board summary")

    def test_fresh_skips_the_landscape(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from landscape import decide, apply
        _finished_run(GOAL_QUARTERLY, "Revenue rose.")
        judge = _Judge()
        rec = decide(GOAL_QUARTERLY, handle_id="run_e", adapter=judge, fresh=True)
        assert rec["rule"] == "fresh_override" and rec["relation"] == "fresh"
        assert rec["scanned"] == 0 and rec["candidates"] == [] and judge.calls == []
        rec = decide(GOAL_QUARTERLY, handle_id="run_e", adapter=judge, fresh=True, why="dry_run")
        assert rec["reason"] == "dry_run"

    def test_the_goal_s_own_run_is_never_its_candidate(self, monkeypatch, tmp_path):
        # a resumed run can already carry a terminal status; it must not
        # find itself in the landscape
        _setup(monkeypatch, tmp_path)
        from landscape import decide
        b = _finished_run(GOAL_QUARTERLY, "an earlier answer of this very run")
        judge = _Judge()
        rec = decide(GOAL_QUARTERLY, handle_id=b, adapter=judge)
        assert rec["rule"] == "no_candidates" and rec["scanned"] == 0 and judge.calls == []

    def test_a_fresh_record_never_names_a_parent(self, monkeypatch, tmp_path):
        # apply reads the RELATION, not the presence of a chosen run: a
        # forged or hand-edited record with relation fresh stamps no lineage
        _setup(monkeypatch, tmp_path)
        from landscape import apply
        a = _finished_run(GOAL_QUARTERLY)
        b = _finished_run(GOAL_FOLLOW_UP, finished=False)
        rec = {"rule": "judge", "relation": "fresh", "chosen": a,
               "candidates": [{"handle_id": a, "goal": GOAL_QUARTERLY}]}
        assert apply(b, {"source": "cli"}, rec) == {"source": "cli"}
        from recall import lineage_root
        assert lineage_root(b) == b

    def test_an_unreadable_judge_is_fresh_and_recorded(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from landscape import decide
        a = _finished_run(GOAL_QUARTERLY, "Revenue rose.")
        rec = decide(GOAL_QUARTERLY, handle_id="x", adapter=_Judge("I think they are related."))
        assert rec["rule"] == "judge_unreadable" and rec["relation"] == "fresh" and rec["chosen"] == ""
        assert rec["candidates"][0]["handle_id"] == a and "judge" in rec
        rec = decide(GOAL_QUARTERLY, handle_id="x", adapter=_Judge(raise_exc=RuntimeError("429 quota")))
        assert rec["rule"] == "judge_unreadable" and rec["reason"].startswith("judge failed: 429")
        rec = decide(GOAL_QUARTERLY, handle_id="x", adapter=None)
        assert rec["rule"] == "judge_unreadable" and rec["reason"] == "no judge available"
        # a judge FACTORY is built only when there is something to judge
        built = []
        def _factory():
            built.append(1)
            return _Judge(_related(1))
        rec = decide(GOAL_QUARTERLY, handle_id="x", adapter=_factory)
        assert rec["relation"] == "related" and built == [1]
        rec = decide(GOAL_HAIKU, handle_id="x", adapter=_factory)
        assert rec["rule"] == "no_candidates" and built == [1]
        rec = decide(GOAL_QUARTERLY, handle_id="x", adapter=lambda: (_ for _ in ()).throw(RuntimeError("no key")))
        assert rec["rule"] == "judge_unreadable" and rec["reason"] == "no judge available"
        # the chosen run must be one of the candidates, or the answer is unreadable
        rec = decide(GOAL_QUARTERLY, handle_id="x", adapter=_Judge(_related("zzzz9999")))
        assert rec["rule"] == "judge_unreadable" and rec["relation"] == "fresh"


# ---------------------------------------------------------------------------
# through the handle: the NOW request carries the prior's answer
# ---------------------------------------------------------------------------

class _NowAndJudge:
    """One adapter that answers the landscape and the NOW call."""

    def __init__(self, judge_answer=None):
        self.calls = []
        self.judge_answer = judge_answer
        self.model_key = "cheap"

    def complete(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        purpose = kwargs.get("purpose")
        text = self.judge_answer if purpose == "landscape" else "Revenue rose 12% on services; margins held at 41%."
        if purpose == "landscape" and text is None:
            raise AssertionError("the landscape was consulted when it should not have been")

        class _R:
            content = text
            input_tokens = 10
            output_tokens = 5
            model = "scripted"
        return _R()


def _no_hosted_free():
    return patch("hosted_free.build_hosted_free_adapter", return_value=None)


def _classify_now():
    from intent import ClassifyResult
    return patch("intent.classify", return_value=ClassifyResult("now", 0.9, "simple", introspects_self=False))


class TestHandleReadsTheLandscape:
    def test_the_follow_up_request_carries_the_prior_answer(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from handle import handle
        from runs import run_dir
        first = _NowAndJudge()
        with _no_hosted_free(), _classify_now():
            r1 = handle(GOAL_QUARTERLY, adapter=first, force_lane="now", dry_run=False)
        assert r1.status == "done"
        assert [kw["purpose"] for _, kw in first.calls] == ["now"]     # no candidates → no call
        meta1 = json.loads((run_dir(r1.handle_id) / "metadata.json").read_text())
        assert meta1["landscape"]["rule"] == "no_candidates"
        assert "origin" not in meta1 or not meta1["origin"].get("parent_handle_id")

        second = _NowAndJudge(_related(1, "the same report, with margins"))
        with _no_hosted_free(), _classify_now():
            r2 = handle(GOAL_FOLLOW_UP, adapter=second, force_lane="now", dry_run=False)
        assert r2.status == "done"
        assert [kw["purpose"] for _, kw in second.calls] == ["landscape", "now"]
        now_messages = second.calls[1][0]
        user = now_messages[-1].content
        assert f"## Related prior run ({r1.handle_id}, related)" in user
        assert "Revenue rose 12% on services" in user
        assert user.endswith(GOAL_FOLLOW_UP)
        meta2 = json.loads((run_dir(r2.handle_id) / "metadata.json").read_text())
        assert meta2["landscape"]["chosen"] == r1.handle_id
        assert meta2["origin"]["parent_handle_id"] == r1.handle_id
        assert meta2["origin"]["related_by"] == "landscape"
        from recall import lineage_root
        assert lineage_root(r2.handle_id) == r1.handle_id

    def test_fresh_and_after_skip_the_landscape(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from handle import handle
        from runs import run_dir
        with _no_hosted_free(), _classify_now():
            r1 = handle(GOAL_QUARTERLY, adapter=_NowAndJudge(), force_lane="now", dry_run=False)
            fresh = _NowAndJudge()          # a landscape call would raise
            r2 = handle(GOAL_QUARTERLY, adapter=fresh, force_lane="now", dry_run=False, fresh=True)
            after = _NowAndJudge()
            r3 = handle(GOAL_QUARTERLY, adapter=after, force_lane="now", dry_run=False,
                        origin={"source": "cli", "parent_handle_id": r1.handle_id,
                                "parent_goal": GOAL_QUARTERLY})
        meta2 = json.loads((run_dir(r2.handle_id) / "metadata.json").read_text())
        assert meta2["landscape"]["rule"] == "fresh_override"
        assert not (meta2.get("origin") or {}).get("parent_handle_id")
        assert [kw["purpose"] for _, kw in fresh.calls] == ["now"]
        assert "## Related prior run" not in fresh.calls[0][0][-1].content
        meta3 = json.loads((run_dir(r3.handle_id) / "metadata.json").read_text())
        assert "landscape" not in meta3                       # --after decided already
        assert meta3["origin"]["parent_handle_id"] == r1.handle_id
        assert [kw["purpose"] for _, kw in after.calls] == ["now"]

    def test_a_dry_run_records_a_skipped_landscape(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from handle import handle
        from runs import run_dir
        _finished_run(GOAL_QUARTERLY, "Revenue rose.")
        r = handle(GOAL_QUARTERLY, dry_run=True, force_lane="now")
        meta = json.loads((run_dir(r.handle_id) / "metadata.json").read_text())
        assert meta["landscape"]["rule"] == "fresh_override" and meta["landscape"]["reason"] == "dry_run"

    def test_an_unreadable_judge_does_not_block_the_run(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from handle import handle
        from runs import run_dir
        _finished_run(GOAL_QUARTERLY, "Revenue rose.")
        a = _NowAndJudge("not json at all")
        with _no_hosted_free(), _classify_now():
            r = handle(GOAL_QUARTERLY, adapter=a, force_lane="now", dry_run=False)
        assert r.status == "done"
        meta = json.loads((run_dir(r.handle_id) / "metadata.json").read_text())
        assert meta["landscape"]["rule"] == "judge_unreadable" and meta["landscape"]["relation"] == "fresh"
        assert "## Related prior run" not in a.calls[-1][0][-1].content

    def test_cli_after_and_fresh_contradict(self, monkeypatch, tmp_path, capsys):
        _setup(monkeypatch, tmp_path)
        from handle import main
        assert main(["--after", "aaaa0001", "--fresh", "anything"]) == 2
        assert "contradict" in capsys.readouterr().err


class TestAgendaReadsTheLandscape:
    """The AGENDA lane: the related block rides the loop's extra ancestry
    context (the same channel operator context uses), not the goal."""

    def test_the_related_block_reaches_the_loop(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from unittest.mock import MagicMock
        from handle import handle
        from agent_loop import LoopResult, StepOutcome
        from director import ClosureVerdict
        import llm
        a = _finished_run(GOAL_QUARTERLY, "Revenue rose 12% on services.")
        adapter = _NowAndJudge(_rerun(1, "the same report"))
        monkeypatch.setattr(llm, "build_adapter", lambda *x, **kw: adapter)
        goals, loop_kwargs = [], []

        def _fake_run(goal, *x, **kw):
            goals.append(goal)
            loop_kwargs.append(kw)
            return LoopResult(loop_id="test-land", project="test-proj", goal=goal, status="done",
                              stuck_reason=None,
                              steps=[StepOutcome(index=0, text="step", status="done",
                                                 result="output", iteration=0)])

        gate = MagicMock()
        gate.escalate = False
        gate.contested_claims = []
        with _no_hosted_free(), \
             patch("agent_loop.run_agent_loop", side_effect=_fake_run), \
             patch("intent.check_goal_clarity", return_value={"clear": True}), \
             patch("director.verify_goal_completion",
                   return_value=ClosureVerdict(complete=True, confidence=0.9, gaps=[],
                                               summary="verified", checks_run=2, checks_passed=2)), \
             patch("quality_gate.run_quality_gate", return_value=gate):
            r = handle(GOAL_QUARTERLY, force_lane="agenda", dry_run=False)
        assert goals and "## Related prior run" not in goals[0]
        extra = loop_kwargs[0].get("ancestry_context_extra", "")
        assert f"## Related prior run ({a}, rerun)" in extra
        assert "Revenue rose 12% on services." in extra
        # the DECIDED origin is the one the run continues with: recall's
        # lineage walk names the chosen run as this goal's parent
        assert f"This goal descends from: {GOAL_QUARTERLY!r} (handle {a}, via cli)" in extra
        assert [kw["purpose"] for _, kw in adapter.calls if kw.get("purpose") == "landscape"] == ["landscape"]
        from runs import run_dir
        meta = json.loads((run_dir(r.handle_id) / "metadata.json").read_text())
        assert meta["origin"]["parent_handle_id"] == a and meta["origin"]["relation"] == "rerun"
